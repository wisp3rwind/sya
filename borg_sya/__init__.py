#!/usr/bin/env python3
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
import configparser


DEFAULT_CONFDIR = '/etc/borg-sya'
DEFAULT_CONFFILE = 'config'


def which(command):
    for d in os.environ['PATH'].split(':'):
        for binary in os.listdir(d):
            if binary == command:
                return os.path.join(d, command)
    sys.exit("%s error: command not found." % command)


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
        logging.info("$ %s %s" % (path, ' '.join(args or []), ))
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
                    logging.error("%s failed. You should investigate." % name)
                logging.error(e)
                raise BackupError()


class PrePostScript():
    def __init__(self, pre, pre_desc, post, post_desc, options):
        self.pre = pre
        self.pre_desc = pre_desc
        self.post = post
        self.post_desc = post_desc
        self.options = options

    def __enter__(self):
        self.result = []
        # Exceptions from the pre- and post-scripts are intended to propagate!
        if self.pre:  # don't fail if self.pre == None
            if not isinstance(self.pre, Sequence):
                self.pre = [self.pre]
            for script in self.pre:
                run_extra_script(script, self.options, name=self.pre_desc)
        return(self.result)

    def __exit__(self, type, value, traceback):
        # Maybe use an environment variable instead?
        # (BACKUP_STATUS=<borg returncode>)
        if self.post:  # don't fail if self.post == None
            if not isinstance(self.post, Sequence):
                self.post = [self.post]
            for script in self.post:
                run_extra_script(script, self.options, name=self.post_desc,
                                 args=self.post_args)


def isexec(path):
    if os.path.isfile(path):
        if os.access(path, os.X_OK):
            return(True)
        else:
            logging.warn("%s exists, but cannot be executed "
                         "by the current user." % path)
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


def parse_conf(confdir, conf):
    # Loading target dir
    if 'repository' not in conf:
        logging.error("'repository' is mandatory for each task in config")
        return

    # check if we have a passphrase file
    if 'passphrase_file' in conf:
        conf['passphrase_file'] = os.path.join(confdir, conf['passphrase_file'])
        with open(conf['passphrase_file']) as f:
            conf['passphrase'] = f.readline().strip()
    else:
        conf['passphrase_file'] = ''
        conf['passphrase'] = ''

    return conf


KEEP_FLAGS = ('keep-hourly', 'keep-daily', 'keep-weekly', 'keep-monthly',
              'keep-yearly')


def process_task(options, conffile, task, gen_opts):
    conf = conffile[task]
    backup_args = list(gen_opts)

    if conffile['sya'].getboolean('verbose'):
        backup_args.append('--stats')

    if options.progress:
        backup_args.append('--progress')

    # Check if we want to run this backup task
    if not conf.getboolean('run_this'):
        logging.debug("! Task disabled. 'run_this' must be set to 'yes' in %s"
                      % task)
        return

    try:
        prefix = conf['prefix']
    except KeyError:
        prefix = '{hostname}'
    backup_args.append('{repo}::{prefix}-{{now:%Y-%m-%d_%H:%M:%S}}'.format(
        repo=conf['repository'], prefix=prefix))

    if 'remote-path' in conf:
        backup_args.append('--remote-path')
        backup_args.append(conf['remote-path'])

    if 'compression' in conf:
        backup_args.extend(['--compression', conf['compression']])

    # Loading source paths
    includes = []
    if 'paths' in conf:
        includes.extend(conf['paths'].strip().split(','))
    elif 'include_file' not in conf:
        logging.error("'paths' is mandatory in configuration file %s" % task)
        return

    # include and exclude patterns
    excludes = []
    if 'include_file' in conf:
        with open(os.path.join(options.confdir, conf['include_file'])) as f:
            for line in f.readlines():
                if line.startswith('- '):
                    excludes.append(line[2:])
                else:
                    includes.append(line)

    if 'exclude_file' in conf:
        with open(os.path.join(options.confdir, conf['exclude_file'])) as f:
            excludes.extend(f.readlines())

    for exclude in excludes:
        backup_args.extend(['--exclude', exclude.strip()])
    backup_args.extend(i.strip() for i in includes)

    # Load and execute if applicable pre-task commands
    with PrePostScript(
            conf.get('pre', None), "'%s' pre-backup script" % task,
            conf.get('post', None), "'%s' post-backup script" % task,
            options) as status:
        # run the backup
        try:
            borg('create', backup_args, conf['passphrase'], options.dryrun)
        except BackupError:
            logging.error("'%s' backup failed. You should investigate." % task)
            status.append('1')
        else:
            status.append('0')
            # prune old backups
            if any(k in conf for k in KEEP_FLAGS):
                backup_cleanup_args = list(gen_opts)
                if conffile['sya'].getboolean('verbose'):
                    backup_cleanup_args.append('--list')
                    backup_cleanup_args.append('--stats')
                for keep in KEEP_FLAGS:
                    if keep in conf:
                        backup_cleanup_args.extend(['--' + keep, conf[keep]])
                backup_cleanup_args.append('--prefix={}-'.format(prefix))
                backup_cleanup_args.append(conf['repository'])
                try:
                    borg('prune', backup_cleanup_args, conf['passphrase'],
                         options.dryrun)
                except BackupError:
                    logging.error("'%s' old files cleanup failed. You should "
                                  "investigate." % conf['name'])


def do_backup(options, conffile, gen_args):
    lock = ProcessLock('sya' + options.confdir)
    try:
        lock.acquire()
    except LockInUse:
        logging.error('Another instance seems to be running '
                      'on the same conf dir.')
        sys.exit(1)

    # Wrap in global 'pre' and 'post' scripts if they exists
    with PrePostScript(
            conffile['sya'].get('pre', None), "Global pre script",
            conffile['sya'].get('post', None), "Global post script",
            options):
        # Task loop
        tasks = options.tasks or conffile.sections()
        for task in tasks:
            if task == 'sya':
                continue
            logging.info('-- Backing up using %s configuration...' % task)
            process_task(options, conffile, task, gen_args)
            logging.info('-- Done backing up %s.' % task)

    lock.release()


def do_check(options, conffile, gen_opts):
    tasks = options.tasks or conffile.sections()
    for task in tasks:
        if task == 'sya':
            continue
        logging.info('-- Checking using %s configuration...' % task)
        backup_args = list(gen_opts)
        backup_args.append(conffile[task]['repository'])
        try:
            borg('check', backup_args, conffile[task]['passphrase'],
                 options.dryrun)
        except BackupError:
            logging.error("'%s' backup check failed. You should investigate."
                          % task)
        logging.info('-- Done checking %s.' % task)


def mount(options, conffile, gen_opts):
    repo = None
    prefix = None
    if options.task:
        repo = conffile[options.task]['repository']
        prefix = conffile[options.task]['prefix']
    if options.repo:
        repo = options.repo.strip('^')
    if options.prefix:
        prefix = options.prefix.strip('^')

    raise NotImplementedError()
    logging.info("-- Mounting archive from repository {} with prefix {}..."
                 "".format(repo, prefix))
    logging.info("-- Selected archive {}".format(""))
    borg_args = list(gen_opts)
    borg_args.append(repo)
    try:
        # TODO: proper passphrase/key support. Same for do_check, verify
        # correctness of do_backup.
        borg('mount', borg_args, conffile[task]['passphrase'],
             options.dryrun)
    except BackupError:
        logging.error("'{}:{}' mounting failed. You should investigate."
                      "".format(repo, prefix))
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
        sys.exit("Configuration directory '%s' not found." % options.confdir)

    conffile = configparser.ConfigParser()
    conffile.add_section('sya')
    conffile['sya']['verbose'] = 'no'
    conffile.read(os.path.join(options.confdir, DEFAULT_CONFFILE))
    for section in conffile.sections():
        if section != 'sya':
            parse_conf(options.confdir, conffile[section])

    if conffile['sya'].getboolean('verbose'):
        logging.getLogger().setLevel(logging.DEBUG)
        gen_args.append('-v')

    options.func(options, conffile, gen_args)

    logging.shutdown()


if __name__ == '__main__':
    main()

# vim: ts=4 sw=4 expandtab
