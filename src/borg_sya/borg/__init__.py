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
    _VERBOSITY_OPTIONS,
)
from .helpers import (
    format_file_size,
)

from ..util import which, format_commandline

# TODO:
# - logging (component-wise, hierarchical)
# - clear separation between borg adapter, config parsing, logging, UI
# - human-readable output (click!). Maybe re-use borgs own message formatting? Or
#   directly display the JSON?


try:
    BINARY = which('borg')
except RuntimeError as e:
    sys.exit(str(e))


class InvalidBorgOptions(Exception):
    pass


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
    """Decorator that ensures that a single Borg instance can only ever spawn
    and interact with one borg process at a time.
    """
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


class DefaultHandlers():
    """State machine base class for handling any status emitted by borg or
    user interaction. The implementation is very basic, actual interaction
    (e.g. reacting to prompts by borg) need to be handled in sublasses.

    What the base class does is some basic dispatching based on message type
    and content. It also passes the contents of the bare json as keyword
    arguments.

    """
    # The following flags control a number of common options that will be
    # passed to borg, such as `--progress`, `--verbose`, etc.
    # Cf. `man borg-common`.
    handles_progress = True
    wants_loglevel = logging.INFO

    def __init__(self, log):
        self.log = log
        self._spinners = dict()

    def _dispatch(self, msg):
        if msg.get('type') == 'log_message':
            name = msg.get('name')
            if msg.get('msgid') in _ERROR_MESSAGE_IDS:
                f = self.onError
            elif msg.get('msgid') in _PROMPT_MESSAGE_IDS:
                f = self.onPrompt
            # elif msg.get('msgid') in _OPERATION_MESSAGE_IDS:
            #     pass
            elif name.startswith('borg.'):
                f = self.onBorgOutput
            else:
                # Debug messages, ...
                f = self.onOtherMessage
        # TODO: Maybe combine progress_message/_percent into one handler
        # onProgress(message=msg.get("msgcontent", ""), percent=msg.get("percent", None), **...)
        elif msg['type'] == 'progress_message':
            f = self.onProgressMessage
        elif msg['type'] == 'progress_percent':
            f = self.onProgressPercent
        elif msg['type'] == 'archive_progress':
            f = self.onArchiveProgress
        elif msg['type'] == 'file_status':
            f = self._onUnhandled
        elif msg['type'] == 'question_prompt':
            f = self._onUnhandled
        elif msg['type'] == 'question_prompt_retry':
            f = self._onUnhandled
        elif msg['type'] == 'question_invalid_answer':
            f = self._onUnhandled
        elif msg['type'] == 'question_accepted_default':
            f = self._onUnhandled
        elif msg['type'] == 'question_accepted_true':
            f = self._onUnhandled
        elif msg['type'] == 'question_accepted_false':
            f = self._onUnhandled
        elif msg['type'] == 'question_env_answer':
            f = self._onUnhandled
        else:
            f = self._onUnhandled

        f(**msg)

    def _onUnhandled(self, **msg):
        self.log.debug(f"Unknown message received from borg, type={msg['type']}")

    def onError(self, **msg):
        # Does this always mean that there was a fatal error, or would it be
        # sensible to communicate this to the outside in a reentrant way?
        raise BorgError(**msg)

    def onBorgOutput(self, **msg):
        """Receives the messages that borg would write to sterr on a standard
        (non-JSON) CLI session
        """
        # TODO: should these messages be passed on?
        if msg.get('name') not in ['borg.output.progress']:
            self.log.info(f"[BORG] {msg.get('message', '')}".rstrip('\n'))

    def onProgressMessage(self, **msg):
        pass

    def onProgressPercent(self, **msg):
        pass

    def format_archive_progress(self,
                                original_size, compressed_size,
                                deduplicated_size, nfiles, time,
                                **msg):
        # Mimic borg's progress output
        return '{osize} O {csize} C {dsize} D {nfiles} N '.format(
                    osize=format_file_size(original_size),
                    csize=format_file_size(compressed_size),
                    dsize=format_file_size(deduplicated_size),
                    nfiles=nfiles,
        )

    def onArchiveProgress(self, path, **msg):
        # TODO: truncate path
        self.log.info(self.format_archive_progress(**msg) + path)

    def onPrompt(self, **msg):
        raise RuntimeError()

    def onOtherMessage(self, **msg):
        pass


POISON = object()


class Borg():

    _HANDLERCLASS = DefaultHandlers

    def __init__(self, dryrun, log=None):
        self.dryrun = dryrun
        self._running = False
        self._log = log if log else logging.getLogger('borg')
        self._log_json = False # 'raw'

    def _readerthread(self, fh, name, as_json, buf, condition):
        """ Reads either raw lines or JSON objects from the given stream. If
            reading JSON and the first line starts with an opening brace,
            subsequent lines will be aggregated until a valid JSON object
            results. Anything not wrapped in braces will not be considered
            to be JSON and will be dropped with an entry to the debug log.

            Yields POISON when encountering the end of the stream.
        """
        def _pass_msg(msg):
            with condition:
                buf.put((name, msg))
                condition.notify()

        if as_json:
            previous = b""
            for line in fh:
                line = previous + line
                if line.lstrip().startswith(b'{'):
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        # Assume that all of the JSON will be well-formed.
                        # Then, a decoding error simply means that not all of
                        # the JSON was read yet, i.e. borg has split it over
                        # multiple lines.
                        # TODO: This is probably not very efficient. Maybe only
                        # try to load the json when opening { and closing }
                        # match? Otoh, most of the json is one-line.
                        previous = line + b'\n'
                    else:
                        previous = b""
                        if self._log_json == 'raw':
                            # Maybe not a good idea because this might include
                            # listings with potentially many thousand items
                            self._log.debug(('[JSON] ' + line.decode('utf8')).rstrip('\n'))
                        _pass_msg(msg)
                else:
                    # Not JSON
                    self._log.debug(('[NOT JSON] ' + line.decode('utf8')).rstrip('\n'))
                    return None
                    # _pass_msg(line)
        else:
            for line in fh:
                _pass_msg(line)
        fh.close()
        _pass_msg(POISON)

    def _communicate(self, p, stdout='raw', stderr='raw'):
        """Similar to Popen.communicate, but without the deadlocks when both
        stdout and stderr are written to.
        """
        buf = Queue()
        nthreads = 0
        threads = []
        new_msg = Condition()
        if stdout in ['raw', 'json']:
            stdout_thread = Thread(target=self._readerthread,
                                   args=(p.stdout,
                                         'stdout', stdout == 'json',
                                         buf, new_msg),
                                   )
            stdout_thread.daemon = True
            stdout_thread.start()
            nthreads += 1
            threads.append(stdout_thread)
        if stderr in ['raw', 'json']:
            stderr_thread = Thread(target=self._readerthread,
                                   args=(p.stderr,
                                         'stderr', stderr == 'json',
                                         buf, new_msg),
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

    # TODO check `man borg-common` for more arguments to support
    @_while_running(False)
    def _run(self, command, options, env=None, output=False,
             handlers=None):
        """Run a borg commandline (possibly after extending it with a number
        of common arguments given as parameters to this function). Messages
        from borg are read as JSON and dispatched to the `handlers`.
        """
        handlers = (handlers or self._HANDLERCLASS(self._log))

        commandline = [BINARY, command]
        commandline.append('--log-json')
        if handlers.handles_progress:
            commandline.append('--progress')
        verbosity_flag = _VERBOSITY_OPTIONS[handlers.wants_loglevel]
        if verbosity_flag:
            options.insert(0, verbosity_flag)

        outbuf = []
        if output:
            # Not supported by all commands
            commandline.append('--json')

        commandline.extend(options)

        self._log.debug(format_commandline(commandline))
        if not self.dryrun:
            self._p = p = Popen(commandline, env=env,
                                stdout=PIPE, stderr=PIPE,
                                )

            self._running = True

            for stdout, msg in self._communicate(p, stdout='raw',
                                                    stderr='json'):
                if output and stdout is not None:
                    outbuf.append(stdout)
                elif msg:
                    handlers._dispatch(msg)

            self._running = False

            if self._log_json == 'raw':
                # Maybe not a good idea because this might include listings with
                # potentially many thousand items
                for line in outbuf:
                    self._log.debug(('[JSON OUT] ' + line.decode('utf8')).rstrip('\n'))

        return(outbuf)

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

    def _handle_archive_filter_options(self, sorting, options,
            prefix=None, glob_archives=None, sort_by=None, first=0, last=0,
            **kwargs
            ):
        if prefix and glob_archives:
            raise InvalidBorgOptions(
                    "options --glob-archives and --prefix conflict"
                    )
        if prefix: options.extend(['--prefix', prefix])
        if glob_archives: options.extend(['--glob-archives', glob_archives])
        if sorting:
            # Some commands support the previous filters, but not these
            if sort_by:
                if all(s in ['timestamp', 'name', 'id']
                       for s in sort_by.split(',')):
                    options.extend(['--sort-by', sort_by])
                else:
                    raise ValueError()
            if first: options.extend(['--first', str(int(first))])
            if last: options.extend(['--last', str(int(last))])
        return kwargs

    def _handle_common_options(self, options, **kwargs):
        # TODO: handle a useful subset of
        # https://borgbackup.readthedocs.io/en/stable/usage/general.html#common-options
        raise NotImplementedError()

    def _handle_unknown_arguments(self, remaining):
        if remaining:
            raise InvalidBorgOptions("unknown borg arguments {}".format(
                            ', '.join(remaining.keys())
                            ))

    # borg-check also takes an archive instead of a full repo as argument, this
    # is not supported here fore now.
    def check(self, repo,
              repos_only=False, archives_only=False,
              verify_data=False, repair=False, save_space=False,
              handlers=None, **kwargs,
              ):
        if repos_only and verify_data:
            raise InvalidBorgOptions('borg-check options --repository-only and '
                                     '--verify-data conflict')

        options = repo.borg_args()
        if repos_only: options.append('--repository-only')
        if archives_only: options.append('--archives-only')
        if verify_data: options.append('--verify-data')
        if repair: options.append('--repair')
        if save_space: options.append('--save-space')
        remaining = self._handle_archive_filter_options(True, options, **kwargs)
        self._handle_unknown_arguments(remaining)
        options.append(f"{repo}")

        with repo:
            self._run('check', options, handlers=handlers)

    def create(self, repo, includes, excludes=[],
               prefix='{hostname}', stats=False,
               handlers=None):
        if not includes:
            raise InvalidBorgOptions(
                'No paths given to include in the archive',
            )

        options = repo.borg_args(create=True)
        if stats:
            # actually, this is already implied by --json
            options.append('--stats')
        for e in excludes:
            options.extend(['--exclude', e])
        options.append(f'{repo}::{prefix}')
        options.extend(includes)

        with repo:
            self._run('create', options, handlers=handlers)

    def mount(self, repo, archive=None, mountpoint='/mnt', foreground=False,
              handlers=None):
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
            self._run('mount', options, handlers=handlers)

    def umount(self, repo, handlers=None):
        raise NotImplementedError()

    def extract(self, repo, handlers=None):
        raise NotImplementedError()

    def list(self, repo,
             # TODO: support exclude patterns.
             additional_keys=[], pandas=True, short=False,
             handlers=None,
             **kwargs):
        # NOTE: This can list either repo contents (archives) or archive
        # contents (files). Respect that, maybe even split in separate methods
        # (since e.g. repos should have the 'short' option to only return the
        # prefix, while only archives should have the pandas option(?)).
        options = repo.borg_args()

        if short:
            # default format: 'prefix     Mon, 2017-05-22 02:52:37'
            # --short format: 'prefix'
            raise NotImplementedError()

        if additional_keys:
            # TODO: validate?
            options.extend(['--format',
                            ' '.join(f'{{{k}}}' for k in additional_keys)
                            ])

        remaining = self._handle_archive_filter_options(True, options, **kwargs)
        self._handle_unknown_arguments(remaining)

        output = []
        with repo:
            self._run('list', options, output=output, handlers=handlers)

        output = (json.loads(line) for line in output)
        if pandas:
            import pandas as pd
            # TODO: set dtype for all fields that could occur (defaults or
            # additional_keys)
            # dtype=...,
            return pd.DataFrame.from_records(output)
        else:
            return list(output)

    def info(self, repo, handlers=None, **kwargs):
        options = []
        remaining = self._handle_archive_filter_options(True, options, **kwargs)
        self._handle_unknown_arguments(remaining)

        raise NotImplementedError()

    def delete(self, repo, handlers=None):
        raise NotImplementedError()

    def prune(self, repo, intervals, verbose=True, save_space=False,
              handlers=None, **kwargs):
        # TODO: support --keep-within INTERVAL
        # TODO: support --list
        # TODO: support --stats
        # TODO: support --force
        # TODO: support --dry-run (generally implement dryrun support for the
        # whole class: on two levels, not running borg at all, and running
        # borg --dry-run
        if not intervals:
            raise InvalidBorgOptions('No intervals specified to keep archives in when pruning')
        options = repo.borg_args()

        # TODO: Is the verbose option necessary here, or should --list --stats
        # always be passed and filtering of the output occur but in the
        # handlers class?
        if verbose:
            options.extend(['--list', '--stats'])
        for interval, number in intervals.items():
            if interval == 'within':
                if type(number) != str:
                    raise InvalidBorgOptions("Invalid interval '{}' specified "
                            "for --keep-within when pruning".format(interval))
                options.extend([f'--keep-within', number])
            elif interval in ['last', 'secondly', 'minutely', 'hourly',
                                'daily', 'weekly', 'monthly', 'yearly']:
                options.extend([f'--keep-{interval}', str(number)])
            else:
                raise InvalidBorgOptions("Invalid interval '{}' specified for "
                                 "pruning".format(interval))
        if save_space: options.append('--save-space')
        remaining = self._handle_archive_filter_options(False, options, **kwargs)
        self._handle_unknown_arguments(remaining)
        options.append(f"{repo}")

        with repo:
            self._run('prune', options, handlers=handlers)

    def recreate(self, handlers=None):
        raise NotImplementedError()
