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


import sys
import os
from collections import Sequence
from contextlib import contextmanager
import logging
import argparse
import subprocess
from subprocess import CalledProcessError
import socket
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


class ProcessLock(object):
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


def run(path, args=None, env=None, dryrun=False):
    if dryrun:
        logging.info(f"$ {path} {' '.join(args or [])}")
        # print("$ %s %s" % (path, ' '.join(args or []), ))
    else:
        cmdline = [path]
        if args is not None:
            cmdline.extend(args)
        subprocess.check_call(cmdline, env=env)


def run_extra_script(path, options, name="", args=None, env=None, dryrun=False):
    if path:
        if not os.path.isabs(path):
            path = os.path.join(options.confdir, path)
        if isexec(path):
            try:
                run(path, args, env, options.dryrun)
            except CalledProcessError as e:
                if name:
                    logging.error(f"{name} failed. You should investigate.")
                logging.error(e)
                raise BackupError()


class PrePostScript():
    def __init__(self, pre, pre_desc, post, post_desc, options):
        self.pre = pre
        self.pre_desc = pre_desc
        self.post = post
        self.post_desc = post_desc
        self.options = options

        self.nesting_level = 0

    def __enter__(self):
        if self.nesting_level == 0:
            # Exceptions from the pre- and post-scripts are intended to
            # propagate!
            if self.pre:  # don't fail if self.pre == None
                if not isinstance(self.pre, Sequence):
                    self.pre = [self.pre]
                for script in self.pre:
                    run_extra_script(script, self.options, name=self.pre_desc)
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
                    run_extra_script(script, self.options, name=self.post_desc,
                                     args=1 if type else 0)


class Repository(PrePostScript):
    def __init__(self, cfg, name, options):
        self.name = name
        cfg = cfg['repositories'][name]

        self.path = cfg['path']

        super().__init__(
            cfg.get('mount', None), f'Mount script for repository {name}',
            cfg.get('umount', None), f'Unmount script for repository {name}',
            options
        )

        self.compression = cfg.get('compression', None)
        self.passphrase = cfg.get('passphrase', '')
        passphrase_file = cfg.get('passphrase-file', None)
        self.remote_path = cfg.get('remote-path', None)

        # check if we have a passphrase file
        if passphrase_file:
            passphrase_file = os.path.join(confdir, passphrase_file)
            try:
                with open(passphrase_file) as f:
                    self.passphrase = f.readline().strip()
            except IOError as e:
                raise

    @property
    def borg_args(self, create=False):
        args = []
        if self.remote_path:
            args.extend(['--remote-path', self.remote_path])

        if create and self.compression:
            args.extend(['--compression', self.compression])

        return(args)

    def __str__(self):
        """Used to construct the commandline arguments for borg, do not change!
        """
        return(self.path)


class InvalidConfigurationError(Exception):
    pass


class Task():
    KEEP_INTERVALS= ('hourly', 'daily', 'weekly', 'monthly', 'yearly')

    def __init__(self, cfg, name, options):
        try:
            self.name = name
            tcfg = cfg['tasks'][name]

            if 'repository' not in tcfg:
                logging.error("'repository' is mandatory for each task in config")
                return
            self.repo = cfg['repositories'][tcfg['repository']]

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
                self.include_file = os.path.join(options.confdir,
                                                 self.include_file)
            if self.exclude_file:
                self.exclude_file = os.path.join(options.confdir,
                                                 self.exclude_file)
            self.scripts = PrePostScript(
                tcfg.get('pre', None), f"'{name}' pre-backup script",
                tcfg.get('post', None), f"'{name}' post-backup script",
                options)
        except (KeyError, ValueError, TypeError) as e:
            raise InvalidConfigurationError(str(e))

    def backup(self, cfg, options, gen_opts):
        # Check if we want to run this backup task
        if not self.enabled:
            logging.debug(f"! Task disabled. 'run_this' must be set to 'yes' "
                          "in {name}")
            return

        backup_args = list(gen_opts)

        if cfg['sya']['verbose']:
            backup_args.append('--stats')

        if options.progress:
            backup_args.append('--progress')

        backup_args.append(f'{repo}::{prefix}-{{now:%Y-%m-%d_%H:%M:%S}}')

        backup_args.extend(self.repo.borg_args(create=True))

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
            backup_args.extend(['--exclude', exclude.strip()])
        backup_args.extend(i.strip() for i in includes)

        # run the backup
        try:
            # Load and execute if applicable pre-task commands
            with self.repo, self.scripts:
                borg('create', backup_args, self.repo.passphrase,
                     options.dryrun)
        except BackupError:
            logging.error(f"'{self.name}' backup failed. You should investigate.")
            raise

    def prune(self, cfg, options, gen_opts):
        if self.keep:
            backup_cleanup_args = list(gen_opts)
            if cfg['sya']['verbose']:
                backup_cleanup_args.append('--list')
                backup_cleanup_args.append('--stats')
            for interval, number in self.keep.items():
                backup_cleanup_args.extend([f'--keep-{interval}', number])
            backup_cleanup_args.append(f'--prefix={prefix}-')
            backup_cleanup_args.append(f"{repo}")
            try:
                with self.repo, self.scripts:
                    borg('prune', backup_cleanup_args,
                         self.repo.passphrase, options.dryrun)
            except BackupError:
                logging.error(f"'{name}' old files cleanup failed. "
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


def borg(command, args, passphrase=None, dryrun=False):
    if passphrase:
        env = {'BORG_PASSPHRASE': passphrase, }
    else:
        env = None

    args.insert(0, command)
    try:
        run(BINARY, args, env=env, dryrun=dryrun)
    except CalledProcessError as e:
        logging.error(e)
        raise BackupError()


def do_backup(options, cfg, gen_args):
    lock = ProcessLock('sya' + options.confdir)
    try:
        lock.acquire()
    except LockInUse:
        logging.error('Another instance seems to be running '
                      'on the same conf dir.')
        sys.exit(1)

    # Wrap in global 'pre' and 'post' scripts if they exists
    with PrePostScript(
            cfg['sya'].get('pre', None), "Global pre script",
            cfg['sya'].get('post', None), "Global post script",
            options):
        # Task loop
        tasks = options.tasks or cfg['tasks']
        for task in tasks:
            task = cfg['tasks'][task]
            logging.info(f'-- Backing up using {task} configuration...')
            task.backup(cfg, options, gen_args)
            task.prune(cfg, options, gen_args)
            logging.info(f'-- Done backing up {task}.')

    lock.release()


def do_check(options, conffile, gen_opts):
    tasks = options.tasks or cfg['tasks']
    # TODO: do not check repositories repeatedly
    for task in tasks:
        logging.info(f'-- Checking using {task} configuration...')
        backup_args = list(gen_opts)
        repo = cfg[task]['repository']
        repo = cfg['repositories'][repo]
        backup_args.append(f"{repo}")
        try:
            borg('check', backup_args, repo.passphrase, options.dryrun)
        except BackupError:
            logging.error(f"'{task}' backup check failed. You "
                          "should investigate.")
        logging.info(f'-- Done checking {task}.')


def mount(options, cfg, gen_opts):
    repo = None
    prefix = None
    if options.task:
        repo = cfg['tasks'][options.task]['repository']
        prefix = cfg['tasks'][options.task]['prefix']
    if options.repo:
        repo = options.repo.rstrip('^')
    if options.prefix:
        prefix = options.prefix.rstrip('^')

    repo = cfg['repositories'][repo]

    raise NotImplementedError()
    logging.info(f"-- Mounting archive from repository {repo.name} "
                 "with prefix {prefix}...")
    logging.info(f"-- Selected archive {archive}")
    borg_args = list(gen_opts)
    borg_args.append(f"{repo}")
    try:
        # TODO: proper passphrase/key support. Same for do_check, verify
        # correctness of do_backup.
        borg('mount', borg_args, repo.passphrase, options.dryrun)
    except BackupError:
        logging.error(f"'{repo}:{prefix}' mounting failed. "
                      "You should investigate.")
    # TODO: is this true?
    logging.info('-- Done mounting. borg has daemonized, manually unmount '
                 'the repo to shut down the FUSE driver.')


def main():
    p = argparse.ArgumentParser(allow_abbrev=False)
    p.add_argument(
        '-d', '--config-dir', dest='confdir', default=DEFAULT_CONFDIR,
        help="Configuration directory, default is {}".format(DEFAULT_CONFDIR))
    p.add_argument(
        '-n', '--dry-run', action='store_true', dest='dryrun',
        help="Do not run backup, don't act.")
    p.add_argument(
        '-v', '--verbose', action='store_true',
        help="Be verbose and print stats.")

    sp = p.add_subparsers()

    pcreate = sp.add_parser('create', help="Do a backup run.")
    pcreate.set_defaults(func=do_backup)
    pcreate.add_argument(
        '-p', '--progress', action='store_true',
        help="Show progress.")
    pcreate.add_argument(
        'tasks', nargs='*',
        help="Tasks to run, default is all.")

    pcheck = sp.add_parser('check',
                           help="Perform a check for repository consistency.")
    pcheck.set_defaults(func=do_check)
    pcheck.add_argument(
        '-p', '--progress', action='store_true',
        help="Show progress.")
    pcheck.add_argument(
        '-r', '--repo', action='store_true',
        help="Directly name repositories to check instead of selecting "
             "them from tasks.")
    pcheck.add_argument(
        'tasks', nargs='*',
        help="Tasks to select repositories from, default is all. If '-r' "
             "is given, name repositories instead of tasks.")

    pmount = sp.add_parser('mount', help="Mount a snapshot.")
    pmount.set_defaults(func=mount)
    # --repo name[^[^ ...]] -> repo
    # --task name[^[^ ...]] -> repo, prefix
    # --before=2017-02-01T12:45:10
    grselect = pmount.add_mutually_exclusive_group(required=True)
    grselect.add_argument(
        '-r', '--repo', default=None,
        help="Select the last archive in the given repository, this may be "
             "narrowed down further by specifying '--prefix'. "
             "Optionally append an arbitrary number of '^' to choose the "
             "next-to last or earlier archives.")
    grselect.add_argument(
        '-t', '--task', default=None,
        help="Select the last archive for the task (i.e. repository "
             "and prefix). Optionally append an arbitrary number of '^' "
             "to choose the next-to last or earlier archives.")
    pmount.add_argument(
        '-p', '--prefix', default=None,
        help="Narrow down the selection by matching the prefix.")
    pmount.add_argument(
        '--umask', default=None,
        help="Set umask when mounting")
    # TODO: --daemon choice
    # TODO: it IS possible to mount a whole archive
    pmount.add_argument(
        '-f', '--foreground', action='store_true',
        help="Whether to stay in the foreground or daemonize")
    pmount.add_argument(
        'mountpoint', nargs=1,
        help="The mountpoint.")

    options = p.parse_args()

    gen_args = []

    logging.basicConfig(format='%(message)s', level=logging.WARNING)

    if not os.path.isdir(options.confdir):
        sys.exit("Configuration directory '{options.confdir}' not found.")

    with open(os.path.join(options.confdir, DEFAULT_CONFFILE), 'r') as f:
        cfg = yaml.safe_load(f)

    # Parse configuration into corresponding classes.
    for repo in cfg['repositories']:
        cfg['repositories'][repo] = Repository(cfg, cfg['repositories'][repo],
                                               options)
    for task in cfg['tasks']:
        cfg['tasks'][task] = Task(cfg, cfg['tasks'][task], options)

    # TODO: proper validation of the config file
    if 'verbose' in cfg['sya']:
        assert(isinstance(cfg['sya']['verbose'], bool))
    if options.verbose:
        cfg['sya']['verbose'] = options.verbose
        del options.verbose
    if cfg['sya']['verbose'].get(bool):
        logging.getLogger().setLevel(logging.DEBUG)
        gen_args.append('-v')

    options.func(options, cfg, gen_args)

    logging.shutdown()


if __name__ == '__main__':
    main()

# vim: ts=4 sw=4 expandtab
