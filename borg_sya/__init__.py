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
# TODO: Command that executes the pre-scripts and then drops the user in a
#       shell


from functools import wraps
import logging
import os
import sys
from time import sleep

import click
import yaml
from yaml.loader import SafeLoader
from yaml.nodes import ScalarNode, MappingNode, SequenceNode

from . import util
from .util import (LockInUse, ProcessLock,
                   LazyReentrantContextmanager)
from . import borg
from .borg import (Borg, BorgError)

DEFAULT_CONFDIR = '/etc/borg-sya'
DEFAULT_CONFFILE = 'config.yaml'
APP_NAME = 'borg-sya'


class InvalidConfigurationError(Exception):
    pass


class PrePostScript(LazyReentrantContextmanager):
    def __init__(self, pre, pre_desc, post, post_desc, dryrun, log, dir):
        super().__init__()

        self.pre = pre
        if not isinstance(self.pre, list):
            self.pre = [self.pre]
        self.pre_desc = pre_desc
        self.post = post
        if not isinstance(self.post, list):
            self.post = [self.post]
        self.post_desc = post_desc
        self.dryrun = dryrun
        self.log = log
        self.dir = dir

    def _run_script(self, script, cx, args=None, env=None):
        if script:
            assert(isinstance(script, util.Script))
            if not self.dryrun:
                script.run(args=args, env=env,
                           log=self.log, dryrun=self.dryrun,
                           dir=self.dir)
            else:
                self.log.info(
                    script.run(pretend=True,
                               args=args, env=env,
                               log=self.log, dryrun=self.dryrun,
                               dir=self.dir)
                )

    def _announce(self, msg):
        msg = "Running " + msg
        if not self.dryrun:
            self.log.debug(msg)
        else:
            self.log.info(msg)

    def _enter(self):
        # Exceptions from the pre- and post-scripts are intended to
        # propagate!
        self._announce(self.pre_desc)
        for script in self.pre:
            self._run_script(script)

    def _exit(self, type, value, traceback):
        self._announce(self.post_desc)
        for script in self.post:
            # Maybe use an environment variable instead?
            # (BACKUP_STATUS=<borg returncode>)
            self._run_script(script, "Running " + self.post_desc,
                             args=[str(1 if type else 0)])


# Check if we want to run this backup task
def if_enabled(f):
    @wraps(f)
    def wrapper(self, *args, **kwargs):
        if self.enabled:
            return(f(self, *args, **kwargs))
        elif not hasattr(self, 'disabled_msg_shown'):
            self.cx.debug(f"! Task disabled. Set 'run_this' to 'yes' "
                          f"in config section {self.name} to change this.")
            self.disabled_msg_shown = True
            return
    return(wrapper)


class Repository(borg.Repository):
    def __init__(self, name, path, cx,
                 compression=None, remote_path=None, passphrase=None,
                 pre=None, pre_desc=None, post=None, post_desc=None,
                 ):
        super().__init__(self,
                         name, path=path,
                         compression=compression, remote_path=remote_path,
                         passphrase=passphrase,
                         borg=cx.borg,
                         )
        self.scripts = PrePostScript(pre, pre_desc, post, post_desc,
                                     cx.dryrun, cx.log, cx.confdir)

    @classmethod
    def from_yaml(cls, name, cfg, cx):
        # check if we have a passphrase file
        passphrase = cfg.get('passphrase', '')
        passphrase_file = cfg.get('passphrase-file', None)
        if passphrase_file:
            passphrase_file = os.path.join(cx, passphrase_file)
            try:
                with open(passphrase_file) as f:
                    passphrase = f.readline().strip()
            except OSError as e:
                raise InvalidConfigurationError()

        return cls(
            # BorgRepository args
            name,
            path=cfg['path'],
            compression=cfg.get('compression', None),
            remote_path=cfg.get('remote-path', None),
            passphrase=passphrase,
            cx=cx,
            # PrePostScript args
            pre=cfg.get('mount', None),
            pre_desc=f'mount script for repository {name}',
            post=cfg.get('umount', None),
            post_desc=f'unmount script for repository {name}',
        )

    def __call__(self, *, lazy=False):
        self.lazy = lazy
        return(self)

    @if_enabled
    def __enter__(self):
        self._lock = self.cx.lock(str(self))
        self._lock.__enter__()
        self.scripts(lazy=self.lazy).__enter__()
        self.lazy = False

    @if_enabled
    def __exit__(self, *exc):
        self.scripts.__exit__(*exc)
        self._lock.__exit__(*exc)

    def check(self):
        with self:
            self.cx.borg.check(self)


class Task():
    KEEP_INTERVALS = ('hourly', 'daily', 'weekly', 'monthly', 'yearly')

    def __init__(self, name, cx,
                 repo, enabled, prefix, keep,
                 includes, include_file, exclude_file,
                 pre, pre_desc, post, post_desc,
                 ):
        self.name = name
        self.cx = cx
        self.repo = repo
        self.enabled = enabled
        self.prefix = prefix
        self.keep = keep
        self.includes = includes
        self.include_file = include_file
        self.exclude_file = exclude_file

        self.lazy = False
        self.scripts = PrePostScript(pre, pre_desc, post, post_desc,
                                     cx.dryrun, cx.log, cx.confdir)

    @classmethod
    def from_yaml(cls, name, cfg, cx):
        try:
            if 'repository' not in cfg:
                raise InvalidConfigurationError("'repository' is mandatory "
                                                "for each task in config")

            keep = cfg.get('keep', {})
            if not all(k in cls.KEEP_INTERVALS for k in keep):
                raise InvalidConfigurationError()

            include_file = cfg.get('include_file', None)
            exclude_file = cfg.get('exclude_file', None)
            includes = cfg.get('includes', [])
            if not includes and not include_file:
                raise InvalidConfigurationError(f"'paths' is mandatory in "
                                                "configuration file {name}")
            # Do not load include and exclude files yet since this task might
            # not even be run.
            if include_file:
                include_file = os.path.join(borg.confdir, include_file)
            if exclude_file:
                exclude_file = os.path.join(borg.confdir, exclude_file)

            return cls(
                name,
                cx=cx,
                repo=cx.repos[cfg['repository']],
                enabled=cfg.get('run_this', True),
                prefix=cfg.get('prefix', '{hostname}'),
                keep=keep,
                includes=includes,
                include_file=include_file,
                exclude_file=exclude_file,
                # PrePostScript args
                pre=cfg.get('pre', None),
                pre_desc=f"'{name}' pre-backup script",
                post=cfg.get('post', None),
                post_desc=f"'{name}' post-backup script",
            )
        except (KeyError, ValueError, TypeError) as e:
            raise InvalidConfigurationError(str(e))

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
    def create(self, progress):
        # TODO: Human-readable logging.
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

        # run the backup
        with self:
            self.cx.borg.create(
                self.repo,
                [i.strip() for i in includes],
                [e.strip() for e in excludes],
                prefix=f'{self.prefix}-{{now:%Y-%m-%d_%H:%M:%S}}',
                stats=True,
            )

    @if_enabled
    def prune(self):
        try:
            with self:
                self.borg.prune(self.repo,
                                self.keep,
                                prefix=f'{self.prefix}-',
                                )
        except BorgError as e:
            self.cx.error(e)
            self.cx.error(f"'{self.name}' old files cleanup failed. "
                          f"You should investigate.")
            raise
        except ValueError as e:
            self.cx.error(f"'{self.name}' old files cleanup failed ({e})."
                          f"You should investigate, your configuration might "
                          f"be invalid.")
            raise InvalidConfigurationError(e)


class SyaSafeLoader(SafeLoader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        seq = [(SequenceNode, None)]
        for a, b in [('tasks', 'pre'),
                     ('tasks', 'post'),
                     ('repositories', 'mount'),
                     ('repositories', 'umount')]:
            path = [(MappingNode, a),
                    (MappingNode, None),  # name
                    (MappingNode, b),
                    ]
            self.add_path_resolver('!external_script', path, ScalarNode)
            self.add_path_resolver('!external_script', path + seq, ScalarNode)


class Context():
    def __init__(self, confdir, dryrun, verbose, log, repos, tasks):
        self.confdir = confdir
        self.dryrun = dryrun
        self.verbose = verbose
        self.log = log
        self.repos = repos or dict()
        self.tasks = tasks or dict()
        self.borg = Borg(dryrun, verbose)

    @classmethod
    def from_configuration(cls, confdir, conffile):
        logging.basicConfig(
            format='{name}: {message}', style='}',
            level=logging.WARNING,
        )
        log = logging.getLogger()

        if not os.path.isabs(conffile):
            conffile = os.path.join(confdir, conffile)
        try:
            with open(conffile, 'r') as f:
                cfg = yaml.load(f, SyaSafeLoader)
        except OSError as e:
            log.error(f"Configuration file at '{conffile}' not found or not "
                      f"accessible:\n{e}")
            raise

        # TODO: proper validation of the config file
        verbose = cfg['sya'].get('verbose', False)
        assert(isinstance(cfg['sya']['verbose'], bool))

        # Parse configuration into corresponding classes.
        cx = cls(confdir=confdir, dryrun=False,
                 verbose=verbose, log=log,
                 repos=None, tasks=None,
                 )
        cx.repos = {repo: Repository.from_yaml(repo, rcfg, cx)
                    for repo, rcfg in cfg['repositories'].items()
                    }
        cx.tasks = {task: Task.from_yaml(task, tcfg, cx)
                    for task, tcfg in cfg['tasks'].items()
                    }

        return cx

    @property
    def verbose(self):
        return self._verbose

    @verbose.setter
    def verbose(self, value):
        if value:
            self.log.setLevel(logging.DEBUG)
        else:
            self.log.setLevel(logging.WARNING)

    def validate_repos(self, repos):
        repos = repos or self.repos
        repos = [self.repos[r] for r in repos]
        return repos

    def validate_tasks(self, tasks):
        tasks = tasks or self.tasks
        tasks = [self.tasks[t] for t in tasks]
        repos = set(t.repo for t in tasks if t.enabled)
        return (tasks, repos)

    def lock(self, *args):
        with ProcessLock('sya' + self.confdir + '-'.join(*args)):
            yield

    def print(self, msg):
        self.log.info(msg)
        print(msg)

    def info(self, msg):
        self.log.info(msg)

    def warning(self, msg):
        self.log.warning(msg)

    def error(self, msg):
        self.log.error(msg)


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
    try:
        cx = Context.from_configuration(confdir, DEFAULT_CONFFILE)
    except OSError:
        print(f"Configuration file at "
              f"'{os.path.join(confdir, DEFAULT_CONFFILE)}' "
              f"not found or not accessible.",
              file=sys.stderr)
        raise click.Abort()
    except InvalidConfigurationError() as e:
        print(e)
        raise click.Abort()
    if verbose:  # if True in the config file, do not set to False here
        cx.verbose = verbose
    cx.dryrun = dryrun
    ctx.obj = cx


@main.resultcallback()
def exit(*args, **kwargs):
    logging.shutdown()


@main.command(help="Launch the GUI.")
@click.pass_obj
def gui(ctx):
    pass


@main.command(help="Do a backup run. If no Task is specified, run all.")
@click.option('-p', '--progress/--no-progress',
              help="Show progress.")
@click.argument('tasks', nargs=-1)
@click.pass_obj
def create(cx, progress, tasks):
    cx = cx.sub_context('CREATE')
    for task in (tasks or cx.tasks):
        try:
            task = cx.tasks[task]
        except KeyError:
            cx.error(f'-- No such task: {task}, skipping...')
        else:
            cx.info(f'-- Backing up using {task} configuration...')
            with task(lazy=True):
                try:
                    task.create(progress)
                except BorgError as e:
                    cx.error(f"'{task}' backup failed. "
                             f"You should investigate.")
                except LockInUse:
                    cx.error(f"-- Another process seems to be accessing "
                             f"the repository {task.repo.name}. "
                             f"Could not create a new archive for task "
                             f"{task}.")
                else:
                    task.prune()
                    cx.info(f'-- Done backing up {task}.')


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
def check(cx, progress, repo, items):
    if repo:
        repos = cx.validate_repos(items)
    else:
        _, repos = cx.validate_tasks(items)

    for repo in repos:
        cx.info(f'-- Checking repository {repo.name}...')
        try:
            repo.check()
        except BorgError as e:
            cx.error(f"-- Error {e} when checking repository {repo.name}."
                     f"You should investigate.")
        except LockInUse as e:
            cx.error(f"-- Another process seems to be accessing the "
                     f"repository {repo.name}. Could not check it.")
            continue
        else:
            cx.info(f'-- Done checking {repo.name}.')


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
# TODO: it IS possible to mount a whole archive
@click.argument('item', required=True)
@click.argument('mountpoint', required=True)
@click.pass_obj
def mount(cx, repo, all, umask, item, mountpoint):
    index = len(item)
    item = item.rstrip('^')
    index = index - len(item)

    if repo and not all:
        cx.error("Mounting only the last archive not implemented.")
        raise click.Abort()

    if repo:
        repo, _, prefix = item.partition('::')
        try:
            repo = cx.repos[item]
        except KeyError:
            cx.error(f"No such repository: '{item}'")
            raise click.Abort()
    else:
        try:
            repo = cx.tasks[item].repo
            prefix = cx.tasks[item].prefix
        except KeyError:
            cx.error(f'No such task: {item}')
            raise click.Abort()

    if index and all:
        cx.error(f"Giving {'^' * index} and '--all' conflict.")
        raise click.Abort()

    if prefix and all:
        cx.error(f"Borg doen't support mounting only archives with "
                 f"a given prefix. Mounting only the last archive "
                 f"matching '{prefix}'.")
        all = False

    with repo(lazy=True):
        archive = None
        if not all:
            cx.info(f"-- Searching for last archive from "
                    f"repository '{repo.name}' with prefix '{prefix}'.")
            try:
                # short will only output archive names,
                # last return only the index+1 most recent archives
                archive = cx.borg.list(repo, prefix,
                                       short=True, last=index + 1)[0]
            except (IndexError, BorgError):
                raise click.Abort()
            cx.info(f"-- Selected archive '{archive}'")

        # TODO: interactive archive selection!

        cx.info(f"-- Mounting archive from repository '{repo.name}' "
                f"with prefix '{prefix}'...")
        try:
            # By default, borg daemonizes and exits on unmount. Since we want
            # to run post-scripts (e.g. umount), this is not sensible and we
            # set foreground to True.
            cx.borg.mount(repo, archive, mountpoint, foreground=True)
        except BorgError as e:
            cx.error(f"-- Mounting '{repo.name}' failed: \n"
                     f"{e}\n"
                     f"You should investigate.")
        except KeyboardInterrupt:
            while True:
                try:
                    cx.borg.umount(repo, mountpoint)
                    # TODO: Find out what the JSON log/output of borg mount
                    # are, log something appropriate here
                    break
                except (BorgError, RuntimeError) as e:
                    if 'failed to unmount' in str(e):
                        # Might fail if this happens to quickly after mounting.
                        sleep(2)
                        continue

            cx.info('-- Done unmounting (the FUSE driver has exited).')

# vim: ts=4 sw=4 expandtab
