#!/usr/bin/env python3.6
#
# sya, a simple front-end to the borg backup software
# Copyright (C) 2016 Alexandre Rossi <alexandre.rossi@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

# TODO: include example services in the package
# TODO: Consider using confuse since it has config overlays. Default config
#       appears to be weird, though.
# TODO: add a safeguard to never run borg commands from without the context
#       managers.
# TODO: different levels of verbosity
# TODO: Use colorama
# TODO: Bypass main() when doing `borg-sya {create|check|...} --help` in order
#       not to crash on broken configs


from functools import wraps
import logging
import os
import sys

import click
import yaml

from .util import (which, ExternalScript,
                   LockInUse, ProcessLock,
                   LazyReentrantContextmanager)

DEFAULT_CONFDIR = '/etc/borg-sya'
DEFAULT_CONFFILE = 'config.yaml'


try:
    BINARY = which('borg')
except RuntimeError as e:
    sys.exit(str(e))


class InvalidConfigurationError(Exception):
    pass


class BackupError(Exception):
    pass


class PrePostScript(LazyReentrantContextmanager):
    def __init__(self, pre, pre_desc, post, post_desc, borg):
        super().__init__()

        self.pre = pre
        if not isinstance(self.pre, list):
            self.pre = [self.pre]
        self.pre_desc = pre_desc
        self.post = post
        if not isinstance(self.post, list):
            self.post = [self.post]
        self.post_desc = post_desc
        self.borg = borg

    def _enter(self):
        # Exceptions from the pre- and post-scripts are intended to
        # propagate!
        for script in self.pre:
            self.borg.run_script(script, self.pre_desc)

    def _exit(self, type, value, traceback):
        for script in self.post:
            # Maybe use an environment variable instead?
            # (BACKUP_STATUS=<borg returncode>)
            self.borg.run_script(script, self.post_desc,
                                 args=[str(1 if type else 0)])


class Borg():
    """Encapsulate all information related to running external tools.
    """
    def __init__(self, confdir, dryrun, verbose):
        self.confdir = confdir
        self.dryrun = dryrun
        self.verbose = verbose

    def run_script(self, script, msg="", args=None, env=None):
        script(args, env, self.dryrun, self.confdir)

    def __call__(self, command, args, repo):
        assert(repo.entered)
        env = repo.borg_env() or None

        if self.verbose:
            args.insert(0, '--verbose')
        args.insert(0, command)

        ExternalScript.run(BINARY, args, env, self.dryrun)


class Repository(PrePostScript):
    def __init__(self, name, cfg, borg):
        self.name = name
        self.borg = borg

        self.path = cfg['path']

        super().__init__(
            cfg.get('mount', None), f'Mount script for repository {name}',
            cfg.get('umount', None), f'Unmount script for repository {name}',
            borg
        )

        self.compression = cfg.get('compression', None)
        self.passphrase = cfg.get('passphrase', '')
        passphrase_file = cfg.get('passphrase-file', None)
        self.remote_path = cfg.get('remote-path', None)

        # check if we have a passphrase file
        if passphrase_file:
            passphrase_file = os.path.join(borg.confdir, passphrase_file)
            try:
                with open(passphrase_file) as f:
                    self.passphrase = f.readline().strip()
            except OSError as e:
                raise InvalidConfigurationError()

    def borg_args(self, create=False):
        args = []
        if self.remote_path:
            args.extend(['--remote-path', self.remote_path])

        if create and self.compression:
            args.extend(['--compression', self.compression])

        return(args)

    def borg_env(self):
        env = {}
        if self.passphrase:
            env['BORG_PASSPHRASE'] = self.passphrase

        return(env)

    def check(self):
        args = self.borg_args()
        args.append(f"{self}")
        try:
            with self:
                self.borg('check', args, self)
        except BackupError:
            logging.error(f"'{self.name}' backup check failed. You "
                          "should investigate.")
            raise

    def __str__(self):
        """Used to construct the commandline arguments for borg, do not change!
        """
        return(self.path)


# Check if we want to run this backup task
def if_enabled(f):
    @wraps(f)
    def wrapper(self, *args, **kwargs):
        if self.enabled:
            return(f(self, *args, **kwargs))
        elif not hasattr(self, 'disabled_msg_shown'):
            logging.debug(f"! Task disabled. 'run_this' must be set to 'yes' "
                          "in {name}")
            self.disabled_msg_shown = True
            return
    return(wrapper)


class Task():
    KEEP_INTERVALS = ('hourly', 'daily', 'weekly', 'monthly', 'yearly')

    def __init__(self, name, cfg, borg):
        try:
            self.name = name
            self.borg = borg

            if 'repository' not in cfg:
                raise InvalidConfigurationError("'repository' is mandatory "
                                                "for each task in config")
            self.repo = borg.repos[cfg['repository']]

            self.enabled = cfg.get('run_this', True)
            self.keep = cfg.get('keep', {})
            if not all(k in self.KEEP_INTERVALS for k in self.keep):
                raise InvalidConfigurationError()
            self.prefix = cfg.get('prefix', '{hostname}')
            self.include_file = cfg.get('include_file', None)
            self.exclude_file = cfg.get('exclude_file', None)
            self.includes = cfg.get('includes', [])
            if not self.includes and not self.include_file:
                raise InvalidConfigurationError(f"'paths' is mandatory in "
                                                "configuration file {name}")
            if self.include_file:
                self.include_file = os.path.join(borg.confdir,
                                                 self.include_file)
            if self.exclude_file:
                self.exclude_file = os.path.join(borg.confdir,
                                                 self.exclude_file)
            self.scripts = PrePostScript(
                cfg.get('pre', None), f"'{name}' pre-backup script",
                cfg.get('post', None), f"'{name}' post-backup script",
                borg)
        except (KeyError, ValueError, TypeError) as e:
            raise InvalidConfigurationError(str(e))

        self.lazy = False

    def __str__(self):
        return(self.name)

    def __call__(self, *, lazy=False):
        self.lazy = lazy
        return(self)

    @if_enabled
    def __enter__(self):
        self.repo(lazy=self.lazy).__enter__()
        self.scripts(lazy=self.lazy).__enter__()
        self.lazy = False

    @if_enabled
    def __exit__(self, *exc):
        self.scripts.__exit__(*exc)
        self.repo.__exit__(*exc)

    @if_enabled
    def backup(self, progress):
        args = self.repo.borg_args(create=True)

        if self.borg.verbose:
            args.append('--stats')

        if progress:
            args.append('--progress')

        args.append(f'{self.repo}::{self.prefix}-{{now:%Y-%m-%d_%H:%M:%S}}')

        # include and exclude patterns
        includes = self.includes[:]
        excludes = []
        if self.include_file:
            with open(self.include_file) as f:
                for line in f.readlines():
                    if line.startswith('- '):
                        excludes.append(line[2:])
                    else:
                        includes.append(line)

        if self.exclude_file:
            with open(self.exclude_file) as f:
                excludes.extend(f.readlines())

        for exclude in excludes:
            args.extend(['--exclude', exclude.strip()])
        args.extend(i.strip() for i in includes)

        # run the backup
        try:
            # Load and execute if applicable pre-task commands
            with self:
                self.borg('create', args, self.repo)
        except BackupError:
            logging.error(f"'{self.name}' backup failed. "
                          "You should investigate.")
            raise

    @if_enabled
    def prune(self):
        if self.keep:
            args = []
            if self.borg.verbose:
                args.extend(['--list', '--stats'])
            for interval, number in self.keep.items():
                args.extend([f'--keep-{interval}', str(number)])
            args.append(f'--prefix={self.prefix}-')
            args.append(f"{self.repo}")
            try:
                with self:
                    self.borg('prune', args, self.repo)
            except BackupError:
                logging.error(f"'{self.name}' old files cleanup failed. "
                              "You should investigate.")
                raise


@click.group()
@click.option('-d', '--config-dir', 'confdir',
              default=DEFAULT_CONFDIR,
              help=f"Configuration directory, default is {DEFAULT_CONFDIR}")
@click.option('-n', '--dry-run', 'dryrun', is_flag=True,
              help="Do not run backup, don't act.")
@click.option('-v', '--verbose', is_flag=True,
              help="Be verbose and print stats.")
@click.pass_context
def main(ctx, confdir, dryrun, verbose):
    logging.basicConfig(format='%(message)s', level=logging.WARNING)

    if not os.path.isdir(confdir):
        sys.exit(f"Configuration directory '{confdir}' not found.")

    try:
        with open(os.path.join(confdir, DEFAULT_CONFFILE), 'r') as f:
            cfg = yaml.safe_load(f)
    except OSError as e:
        logging.error(f"Cannot access configuration file: {e}")
        sys.exit(1)

    # TODO: proper validation of the config file
    if 'verbose' in cfg['sya']:
        assert(isinstance(cfg['sya']['verbose'], bool))
        verbose = verbose or cfg['sya']['verbose']
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Object to hold all global state.
    borg = Borg(confdir, dryrun, verbose)

    # Parse configuration into corresponding classes.
    borg.repos = {repo: Repository(repo, rcfg, borg)
                  for repo, rcfg in cfg['repositories'].items()}
    borg.tasks = {task: Task(task, tcfg, borg)
                  for task, tcfg in cfg['tasks'].items()}

    ctx.obj = borg

    logging.shutdown()


@main.command(help="Do a backup run. If no Task is speified, run all.")
@click.option('-p', '--progress/--no-progress',
              help="Show progress.")
@click.argument('tasks', nargs=-1)
@click.pass_obj
def create(borg, progress, tasks):
    lock = ProcessLock('sya' + borg.confdir)
    try:
        lock.acquire()
    except LockInUse:
        logging.error('Another instance seems to be running '
                      'on the same conf dir.')
        sys.exit(1)

    for task in (tasks or borg.tasks):
        task = borg.tasks[task]
        logging.info(f'-- Backing up using {task} configuration...')
        with task(lazy=True):
            task.backup(progress)
            task.prune()
        logging.info(f'-- Done backing up {task}.')

    lock.release()


@main.command(help="Perform a check for repository consistency. "
                   "Repositories can either be specified directly or "
                   "by task. If neither is provided, check all.")
@click.option('-p', '--progress/--no-progress',
              help="Show progress.")
@click.option('-r/-t', '--repo/--task', 'repo', default=False,
              help="Whether to directly name repositories to check or select "
                   "them from tasks.")
@click.argument('items', nargs=-1)
@click.pass_obj
def check(borg, progress, repo, items):
    if repo:
        repos = items or borg.repos
        repos = [borg.repos[r] for r in repos]
    else:
        tasks = items or borg.tasks
        tasks = [borg.tasks[t] for t in tasks]
        repos = set(t.repo for t in tasks if t.enabled)

    for repo in repos:
        logging.info(f'-- Checking repository {repo.name}...')
        repo.check()
        logging.info(f'-- Done checking {repo.name}.')


@main.command(help="Mount a snapshot. Takes a repository or task and the "
                   "mountpoint as positional arguments. If a repository, "
                   "a prefix can "
                   "be speified as 'repo::prefix'. Optionally append an "
                   "arbitrary number of '^' to choose the last, next-to "
                   "last or earlier archives. Otherwise, all matching "
                   "archives will be mounted.")
# --repo name[^[^ ...]] -> repo
# --repo name::prefix^^ -> repo, prefix
# --task name[^[^ ...]] -> repo, prefix
# --before=2017-02-01T12:45:10
@click.option(
    '-r/-t', '--repo/--task', 'repo',
    help="Whether to select archives for a repository or task. "
         "narrowed down further by specifying '--prefix'. "
         "Optionally append an arbitrary number of '^' to choose the "
         "next-to last or earlier archives.")
@click.option('-a', '--all', is_flag=True,
              help="Mount the complete repository. The default is to mount "
                   "only the last archive.")
@click.option('--umask', default=None,
              help="Set umask when mounting")
# TODO: --daemon choice
# TODO: it IS possible to mount a whole archive
# Daemonizing is actually problematic since the unmounting won't take place.
# @click.option('-f', '--foreground/--background',
#               help="Whether to stay in the foreground or daemonize")
@click.argument('item', required=True)
@click.argument('mountpoint', required=True)
@click.pass_obj
def mount(borg, repo, all, umask, item, mountpoint):
    index = len(item)
    item = item.rstrip('^')
    index = index - len(item)

    if repo:
        repo, _, prefix = item.partition('::')
        try:
            repo = borg.repos[item]
        except KeyError:
            logging.error(f"No such repository: '{item}'")
            sys.exit(1)
    else:
        try:
            repo = borg.tasks[item].repo
            prefix = borg.tasks[item].prefix
        except KeyError:
            logging.error(f'No such task: {item}')
            sys.exit(1)

    logging.info(f"-- Mounting archive from repository '{repo.name}' "
                 f"with prefix '{prefix}'...")
    with repo:
        archive = None
        if False or index:
            args = repo.borg_args()
            args.append(f"{repo}")
            # args.append('--short')
            # format: 'prefix     Mon, 2017-05-22 02:52:37'
            archives = borg('list', args, repo)
            archive = archives.split('\n')[-index]
            logging.info(f"-- Selected archive {archive}")
        args = repo.borg_args()
        args.append('--foreground')
        if archive:
            args.append(f"{repo}::{archive}")
        else:
            args.append(f"{repo}")
        args.append(mountpoint)
        try:
            borg('mount', args, repo)
        except BackupError as e:
            logging.error(f"mounting '{repo.name}' failed: \n"
                          f"{e}\n"
                          f"You should investigate.")
            sys.exit(1)
    # TODO: is this true?
    logging.info('-- Done mounting. borg has daemonized, manually unmount '
                 'the repo to shut down the FUSE driver.')

# vim: ts=4 sw=4 expandtab
