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


from collections import Sequence
from functools import wraps
import logging
import os
import socket
import subprocess
from subprocess import CalledProcessError
import sys

import click
import yaml


DEFAULT_CONFDIR = '/etc/borg-sya'
DEFAULT_CONFFILE = 'config'


def which(command):
    for d in os.environ['PATH'].split(':'):
        for binary in os.listdir(d):
            if binary == command:
                return os.path.join(d, command)
    sys.exit(f"{command} error: command not found.")


BINARY = which('borg')


class LockInUse(Exception):
    pass


class ProcessLock():
    """This class comes from this very elegant way of having a pid lock in
    order to prevent multiple instances from running on the same host.
    http://stackoverflow.com/a/7758075
    """

    def __init__(self, process_name):
        self.pname = process_name

    def acquire(self):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            # The bind address is the one of an abstract UNIX socket (begins
            # with a null byte) followed by an address which exists in the
            # abstract socket namespace (Linux only). See unix(7).
            self.socket.bind('\0' + self.pname)
        except socket.error:
            raise LockInUse

    def release(self):
        self.socket.close()


class PrePostScript():
    def __init__(self, pre, pre_desc, post, post_desc, borg):
        self.pre = pre
        self.pre_desc = pre_desc
        self.post = post
        self.post_desc = post_desc
        self.borg = borg

        self.nesting_level = 0
        self.lazy = False

    def __call__(self, *, lazy=False):
        self.lazy = True
        return(self)

    def __enter__(self):
        if self.lazy:
            # Only actually enter at the next invocation. This still increments
            # the nesting_level so that cleanup will nevertheless occur at this
            # outer level.
            self.lazy = False
        elif self.nesting_level == 0:
            # Exceptions from the pre- and post-scripts are intended to
            # propagate!
            if self.pre:  # don't fail if self.pre == None
                if not isinstance(self.pre, Sequence):
                    self.pre = [self.pre]
                for script in self.pre:
                    self.borg.run_script(script, self.options,
                                         name=self.pre_desc)
        self.nesting_level += 1

    def __exit__(self, type, value, traceback):
        self.nesting_level -= 1
        if self.nesting_level == 0:
            if self.post:  # don't fail if self.post == None
                if not isinstance(self.post, Sequence):
                    self.post = [self.post]
                for script in self.post:
                    # Maybe use an environment variable instead?
                    # (BACKUP_STATUS=<borg returncode>)
                    self.borg.run_script(script, self.options,
                                         name=self.post_desc,
                                         args=1 if type else 0)


class Repository(PrePostScript):
    def __init__(self, cfg, name, options, borg):
        self.name = name
        self.borg = borg
        cfg = cfg['repositories'][name]

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
            except IOError as e:
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

    def check(self, borg):
        args = self.borg_args()
        args.append(f"{self}")
        try:
            self.borg('check', args, self)
        except BackupError:
            logging.error(f"'{self.name}' backup check failed. You "
                          "should investigate.")
            raise

    def __str__(self):
        """Used to construct the commandline arguments for borg, do not change!
        """
        return(self.path)


class InvalidConfigurationError(Exception):
    pass


class Task():
    KEEP_INTERVALS = ('hourly', 'daily', 'weekly', 'monthly', 'yearly')

    def __init__(self, cfg, name, borg):
        try:
            self.name = name
            self.borg = borg
            tcfg = cfg['tasks'][name]

            if 'repository' not in tcfg:
                raise InvalidConfigurationError("'repository' is mandatory "
                                                "for each task in config")
            self.repo = borg.repos[tcfg['repository']]

            self.enabled = tcfg.get('run_this', True)
            self.keep = tcfg.get('keep', {})
            if not all(k in self.KEEP_INTERVALS for k in self.keep):
                raise InvalidConfigurationError()
            self.prefix = tcfg.get('prefix', '{hostname}')
            self.include_file = tcfg.get('include_file', None)
            self.exclude_file = tcfg.get('exclude_file', None)
            self.includes = tcfg.get('includes', [])
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
                tcfg.get('pre', None), f"'{name}' pre-backup script",
                tcfg.get('post', None), f"'{name}' post-backup script",
                borg)
        except (KeyError, ValueError, TypeError) as e:
            raise InvalidConfigurationError(str(e))

        self.lazy = False

    def __str__(self):
        return(self.name)

    def __call__(self, *, lazy=False):
        self.lazy = True
        return(self)

    def __enter__(self):
        self.repo(lazy=self.lazy).__enter__()
        self.scripts(lazy=self.lazy).__enter__()
        self.lazy = False

    def __exit__(self, *exc):
        self.repo.__exit__(*exc)
        self.scripts.__exit__(*exc)

    def backup(self, progress):
        # Check if we want to run this backup task
        if not self.enabled:
            logging.debug(f"! Task disabled. 'run_this' must be set to 'yes' "
                          "in {name}")
            return

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

    def prune(self):
        if self.keep:
            args = []
            if self.borg.verbose:
                args.extend(['--list', '--stats'])
            for interval, number in self.keep.items():
                args.extend([f'--keep-{interval}', number])
            args.append(f'--prefix={self.prefix}-')
            args.append(f"{self.repo}")
            try:
                with self:
                    self.borg('prune', args, self.repo)
            except BackupError:
                logging.error(f"'{self.name}' old files cleanup failed. "
                              "You should investigate.")
                raise


def isexec(path):
    if os.path.isfile(path):
        if os.access(path, os.X_OK):
            return(True)
        else:
            logging.warn(f"{path} exists, but cannot be executed "
                         "by the current user.")
    return(False)


class BackupError(Exception):
    pass


class Borg():
    """Encapsulate all information related to running external tools.
    """
    def __init__(self, confdir, dryrun, verbose):
        self.confdir = confdir
        self.dryrun = dryrun
        self.verbose = verbose

    def _run(self, path, args=None, env=None):
        if self.dryrun:
            logging.info(f"$ {path} {' '.join(args or [])}")
            # print("$ %s %s" % (path, ' '.join(args or []), ))
        else:
            cmdline = [path]
            if args is not None:
                cmdline.extend(args)
            subprocess.check_call(cmdline, env=env)

    def run_script(self, path, name="", args=None, env=None):
        if path:
            if not os.path.isabs(path):
                path = os.path.join(self.confdir, path)
            if isexec(path):
                try:
                    self._run(path, args, env)
                except CalledProcessError as e:
                    if name:
                        logging.error(f"{name} failed. You should investigate.")
                    logging.error(e)
                    raise BackupError()

    def __call__(self, command, args, repo):
        env = repo.borg_env() or None

        if self.verbose:
            args.insert(0, '--verbose')
        args.insert(0, command)
        try:
            self._run(BINARY, args, env=env)
        except CalledProcessError as e:
            logging.error(e)
            raise BackupError()


def pass_object(f):
    @wraps(f)
    @click.pass_context
    def wrapper(ctx, *args, **kwargs):
        return ctx.invoke(f, ctx.obj, *args, **kwargs)
    return(wrapper)


@main.command(help="Do a backup run.")
@click.option('-p', '--progress/--no-progress',
              help="Show progress.")
@click.argument('tasks', nargs=-1,
                help="Tasks to run, default is all.")
@pass_object
def create(progress, tasks, borg):
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


@main.command(help="Perform a check for repository consistency.")
@click.option('-p', '--progress/--no-progress',
              help="Show progress.")
@click.option('-r/-t', '--repo/--task', 'repo', default=False,
              help="Whether to directly name repositories to check or select "
                   "them from tasks.")
@click.argument('items', nargs='*',
                help="Repositories resp. tasks to select repositories from, "
                     "default is all")
@click.pass_object
def check(progress, repo, items, borg):
    if repo:
        repos = items or borg.repos
    else:
        tasks = items or borg.tasks
        repos = set(borg.tasks[t].repo for t in tasks)

    for repo in repos:
        logging.info(f'-- Checking repository {repo.name}...')
        repo.check()
        logging.info(f'-- Done checking {repo.name}.')


@main.command(help="Mount a snapshot.")
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
@click.option('--umask', default=None,
              help="Set umask when mounting")
# TODO: --daemon choice
# TODO: it IS possible to mount a whole archive
@click.option('-f', '--foreground/--background',
              help="Whether to stay in the foreground or daemonize")
@click.argument('item', required=True,
                help="The repository or task. If a repository, a prefix can "
                     "be speified as 'repo::prefix'. Optionally append an "
                     "arbitrary number of '^' to choose the last, next-to "
                     "last or earlier archives. Otherwise, all matching "
                     "archives will be mounted.")
@click.argument('mountpoint', required=True,
                help="The mountpoint.")
@pass_object
def mount(repo, prefix, umask, foreground, item, mountpoint, borg):
    index = len(item)
    item = item.rstrip('^')
    index = index - len(item)

    if repo:
        repo, _, prefix = item.partition('::')
        repo = borg.repos[item]
    else:
        repo = borg.tasks[item].repo
        prefix = borg.tasks[item].prefix

    raise NotImplementedError()
    logging.info(f"-- Mounting archive from repository {repo.name} "
                 "with prefix {prefix}...")
    logging.info(f"-- Selected archive {archive}")
    borg_args = []
    borg_args.append(f"{repo}")
    try:
        # TODO: proper passphrase/key support. Same for do_check, verify
        # correctness of do_backup.
        borg('mount', borg_args, repo.passphrase)
    except BackupError:
        logging.error(f"'{repo}:{prefix}' mounting failed. "
                      "You should investigate.")
        raise
    # TODO: is this true?
    logging.info('-- Done mounting. borg has daemonized, manually unmount '
                 'the repo to shut down the FUSE driver.')







@click.group()
@click.option('-d', '--config-dir', 'confdir',
              default=DEFAULT_CONFDIR,
              help="Configuration directory, default is {DEFAULT_CONFDIR}")
@click.option('-n', '--dry-run', 'dryrun', is_flag=True,
              help="Do not run backup, don't act.")
@click.option('-v', '--verbose', is_flag=True,
              help="Be verbose and print stats.")
def main(confdir, dryrun, verbose):
    logging.basicConfig(format='%(message)s', level=logging.WARNING)

    if not os.path.isdir(confdir):
        sys.exit(f"Configuration directory '{confdir}' not found.")

    with open(os.path.join(DEFAULT_CONFFILE), 'r') as f:
        cfg = yaml.safe_load(f)

    # TODO: proper validation of the config file
    if 'verbose' in cfg['sya']:
        assert(isinstance(cfg['sya']['verbose'], bool))
        verbose = verbose or cfg['sya']['verbose']
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Object to hold all global state.
    borg = Borg(confdir, dryrun, verbose)

    # Parse configuration into corresponding classes.
    borg.repos = {repo: Repository(cfg, cfg['repositories'][repo], borg)
                  for repo in cfg['repositories']}
    borg.tasks = {task: Task(cfg, cfg['tasks'][task], borg)
                  for task in cfg['tasks']}

    logging.shutdown()

# vim: ts=4 sw=4 expandtab
