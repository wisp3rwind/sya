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


@contextmanager
def prepostscript(pre_path, pre_name, post_path, post_name, options):
    # Exceptions from the pre- and post-scripts are intended to propagate.
    # If this function simply didn't yield in case of an exception in the
    # pre-script, some kind of error would occur (didn't test, but probably
    # something akin 'generator didn't yield').
    run_extra_script(pre_path, options, name=pre_name)
    post_args = []
    try:
        yield post_args
    finally:
        # Maybe use an environment variable instead?
        # (BACKUP_STATUS=<borg returncode>)
        run_extra_script(post_path, options, name=post_name, args=post_args)


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
    with prepostscript(
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
    with prepostscript(
            conffile['sya'].get('pre', None), "Global pre script",
            conffile['sya'].get('post', None), "Global post script",
            options):
        # Task loop
        for task in conffile.sections():
            if task == 'sya':
                continue
            logging.info('-- Backing up using %s configuration...' % task)
            process_task(options, conffile, task, gen_args)
            logging.info('-- Done backing up %s.' % task)

    lock.release()


def do_check(options, conffile, gen_opts):
    for task in conffile.sections():
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


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('-v', '--verbose', action='store_true',
                        help="Be verbose and print stats.")
    parser.add_argument('-p', '--progress', action='store_true',
                        help="Show progress.")
    parser.add_argument('-c', '--check', action='store_true',
                        help="Perform a repository check for consistency.")
    parser.add_argument('-d', '--config-dir', dest='confdir',
                        default=DEFAULT_CONFDIR,
                        help="Configuration directory, default is %s."
                             "" % DEFAULT_CONFDIR)
    parser.add_argument('-t', '--task',
                        default='*',
                        help="Task to run, default is all.")
    parser.add_argument('-n', '--dry-run', action='store_true', dest='dryrun',
                        help="Do not run backup, don't act.")
    options = parser.parse_args()

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

    if options.check:
        do_check(options, conffile, gen_args)
    else:
        do_backup(options, conffile, gen_args)

    logging.shutdown()


if __name__ == '__main__':
    main()

# vim: ts=4 sw=4 expandtab
