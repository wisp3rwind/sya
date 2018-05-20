from functools import wraps
import json
import logging
from queue import Queue
import signal
from subprocess import Popen, PIPE
import sys
from threading import Condition, Thread

from .defs import (
    BorgError,
    _ERROR_MESSAGE_IDS,
    _MESSAGE_TYPES,
    _PROMPT_MESSAGE_IDS,
    _OPERATION_MESSAGE_IDS,
)

from ..util import which

# TODO:
# - logging (component-wise, hierarchical)
# - clear separation between borg adapter, config parsing, logging, UI
# - human-readable output (click!). Maybe re-use borgs own message formatting? Or
#   directly display the JSON?


try:
    BINARY = which('borg')
except RuntimeError as e:
    sys.exit(str(e))


class Repository():
    def __init__(self, name, path, borg,
                 compression=None, remote_path=None, passphrase=None,
                 ):
        self.name = name
        self.path = path
        self.borg = borg
        self.compression = compression
        self.remote_path = remote_path
        self.passphrase = passphrase

    def borg_args(self, create=False):
        args = []
        if self.remote_path:
            args.extend(['--remote-path', self.remote_path])

        if create and self.compression:
            args.extend(['--compression', self.compression])

        return(args)

    @property
    def borg_env(self):
        env = {}
        if self.passphrase:
            env['BORG_PASSPHRASE'] = self.passphrase

        return(env)

    def __str__(self):
        """Used to construct the commandline arguments for borg, do not change!
        """
        return(self.path)


def _while_running(while_running=True):
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if (    (while_running and self._running) or
                    (not while_running and not self._running)
                    ):
                return func(self, *args, **kwargs)
            else:
                raise RuntimeError()
        return wrapper
    return decorator


POISON = object()


class Borg():

    def __init__(self, dryrun, verbose, log=None):
        self.dryrun = dryrun
        self.verbose = verbose
        self._running = False
        self._log = log or logging.getLogger('borg')
        self._log_json = 'raw'

    def _readerthread(self, fh, name, buf, condition):
        for line in fh:
            with condition:
                buf.put((name, line))
                condition.notify()
        fh.close()
        with condition:
            buf.put((name, POISON))
            condition.notify()

    def _communicate_linewise(self, p, stdout=True, stderr=True):
        buf = Queue()
        nthreads = 0
        threads = []
        new_msg = Condition()
        if stdout:
            stdout_thread = Thread(target=self._readerthread,
                                   args=(p.stdout, 'stdout', buf, new_msg)
                                   )
            stdout_thread.daemon = True
            stdout_thread.start()
            nthreads += 1
            threads.append(stdout_thread)
        if stderr:
            stderr_thread = Thread(target=self._readerthread,
                                   args=(p.stderr, 'stderr', buf, new_msg)
                                   )
            stderr_thread.daemon = True
            stderr_thread.start()
            nthreads += 1
            threads.append(stderr_thread)

        while nthreads:
            with new_msg:
                new_msg.wait_for(lambda: not buf.empty())
                source, msg = buf.get()
                if msg is POISON:
                    nthreads -= 1
                elif source == 'stdout':
                    yield (msg, None)
                elif source == 'stderr':
                    yield (None, msg)

    @_while_running(False)
    def _run(self, command, options, env=None, progress=True, output=False):
        commandline = [BINARY, command]
        commandline.append('--log-json')
        if progress:
            commandline.append('--progress')
        if self.verbose:
            options.insert(0, '--verbose')

        outbuf = []
        if output:
            # Not supported by all commands
            commandline.append('--json')

        commandline.extend(options)

        self._log.debug(commandline)
        self._p = p = Popen(commandline, env=env,
                            stdout=PIPE, stderr=PIPE,
                            )

        self._running = True

        try:
            for stdout, stderr in self._communicate_linewise(p):
                if output and stdout is not None:
                    outbuf.append(stdout)
                elif stderr:
                    msg = self.parse_json(stderr)
                    if msg.get('type') == 'log_message':
                        name = msg.get('name', '')
                        if (name.startswith('borg.output') and name not in
                                ['borg.output.progress']
                                ):
                            self._log.info(('[BORG] ' + msg.get('message', '')).rstrip('\n'))
                    if msg:
                        yield msg
        except Exception:
            # ?
            raise

        self._running = False

        if self._log_json == 'raw':
            # Maybe not a good idea because this might include listings with
            # potentially many thousand items
            for line in outbuf:
                self._log.debug(('[JSON OUT] ' + line.decode('utf8')).rstrip('\n'))

        return(outbuf)

    def parse_json(self, msg):
        # TODO: collect multi-line json
        # TODO: do not accept json that is not wrapped in braces, e.g.
        # lines like "[foo]\n"
        if self._log_json == 'raw':
            # Maybe not a good idea because this might include listings with
            # potentially many thousand items
            self._log.debug(('[JSON] ' + msg.decode('utf8')).rstrip('\n'))

        try:
            msg = json.loads(msg)
        except json.JSONDecodeError:
            self._log.debug(('[NOT JSON] ' + msg.decode('utf8')).rstrip('\n'))
            return None

        if msg.get('type') == 'log_message':
            if msg.get('msgid') in _ERROR_MESSAGE_IDS:
                e = BorgError(**msg)
                raise e

        return msg

    @_while_running()
    def _signal(self, sig):
        if not self._running:
            raise RuntimeError()
        self._p.send_signal(sig)

    def _interrupt(self):
        self._signal(signal.SIGINT)

    def _terminate(self):
        self._signal(signal.SIGTERM)

    @_while_running()
    def _reply(self, answer):
        """ Answer a prompt.
        """
        raise NotImplementedError()

    def _yes(self):
        self._reply('YES')

    def _no(self):
        self._reply('NO')

    def init(self):
        raise NotImplementedError()

    def create(self, repo, includes, excludes=[],
               prefix='{hostname}', progress_cb=(lambda m: None),
               stats=False):
        if not includes:
            raise ValueError('No paths given to include in the archive!')

        options = repo.borg_args(create=True)
        if stats:
            # actually, this is already implied by --json
            options.append('--stats')
        for e in excludes:
            options.extend(['--exclude', e])
        options.append(f'{repo}::{prefix}')
        options.extend(includes)

        with repo:
            for msg in self._run('create', options, progress=bool(progress_cb)):
                if msg['type'] == 'log_message':
                    if msg.get('msgid') in _PROMPT_MESSAGE_IDS:
                        raise RuntimeError()
                    else:
                        # Debug messages, ...
                        pass
                elif msg['type'] in ['progress_message', 'progress_percent']:
                    # raise NotImplementedError()
                    progress_cb(msg)

    def mount(self, repo, archive=None, mountpoint='/mnt', foreground=False):
        raise NotImplementedError()
        options = repo.borg_args()
        if foreground:
            options.append('--foreground')

        if archive:
            target = f'{repo}::{archive}'
        else:
            target = str(repo)
        options.append(target)

        with repo:
            for msg in self._run('mount', options):
                if msg.type == 'log_message':
                    if hasattr(msg, 'msgid') and msg.msgid:
                        if msg.msgid in self._PROMPT_MESSAGE_IDS:
                            raise RuntimeError()
                        else:
                            # Debug messages, ...
                            pass

    def umount(self):
        raise NotImplementedError()

    def extract(self):
        raise NotImplementedError()

    def list(self, repo,
             prefix=None, glob=None, first=0, last=0,
             # TODO: support exclude patterns.
             sort_by='', additional_keys=[], pandas=True, short=False):
        # NOTE: This can list either repo contents (archives) or archive
        # contents (files). Respect that, maybe even split in separate methods
        # (since e.g. repos should have the 'short' option to only return the
        # prefix, while only archives should have the pandas option(?)).
        options = repo.borg_args()

        if prefix and glob:
            raise ValueError("Cannot combine archive matching by prefix and "
                             "glob pattern!")
        if prefix:
            options.extend(['--prefix', prefix])
        if glob:
            options.extend(['--glob-archives', glob])

        if short:
            # default format: 'prefix     Mon, 2017-05-22 02:52:37'
            # --short format: 'prefix'
            pass

        if sort_by:
            if sort_by in 'timestamp name id'.split():
                options.extend(['--sort-by', sort_by])
            elif all(s in 'timestamp name id'.split() for s in sort_by):
                options.extend(['--sort-by', ','.join(sort_by)])
            else:
                raise ValueError("Invalid sorting criterion {sort_by} for "
                                 "file listing!")

        if first:
            options.extend(['--first', str(first)])
        if last:
            options.extend(['--last', str(last)])

        if additional_keys:
            # TODO: validate?
            options.extend(['--format',
                            ' '.join(f'{{{k}}}' for k in additional_keys)
                            ])

        output = []
        with repo:
            for msg in self._run('list', options, output=output):
                if msg['type'] == 'log_message':
                    if msg.get('msgid') in _PROMPT_MESSAGE_IDS:
                        raise RuntimeError()
                    else:
                        # Debug messages, ...
                        pass

        output = (json.loads(line) for line in output)
        if pandas:
            import pandas as pd
            # TODO: set dtype for all fields that could occur (defaults or
            # additional_keys)
            # dtype=...,
            return pd.DataFrame.from_records(output)
        else:
            return list(output)

    def info(self):
        raise NotImplementedError()

    def delete(self):
        raise NotImplementedError()

    def prune(self, repo, keep, prefix=None, verbose=True):
        if not keep:
            raise ValueError('No archives to keep given for pruning!')
        options = repo.borg_args()

        if verbose:
            options.extend(['--list', '--stats'])
        for interval, number in keep.items():
            options.extend([f'--keep-{interval}', str(number)])
        if prefix:
            options.extend(['--prefix', prefix])
        options.append(f"{repo}")

        with repo:
            for msg in self._run('prune', options):
                if msg['type'] == 'log_message':
                    if msg.get('msgid') in _PROMPT_MESSAGE_IDS:
                        raise RuntimeError()
                    else:
                        # Debug messages, ...
                        pass

    def recreate(self):
        raise NotImplementedError()
