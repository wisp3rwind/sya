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
import itertools
import logging
import os

import yaml
from yaml.loader import SafeLoader
from yaml.nodes import ScalarNode, MappingNode, SequenceNode

from . import util
from .util import (ProcessLock, LazyReentrantContextmanager)
from . import borg
from .borg import (Borg, BorgError)
from . import terminal


__all__ = ['InvalidConfigurationError',
           'Task',
           'Repository',
           'Context',
           ]


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

    def _run_script(self, script, args=None, env=None):
        if script:
            assert(isinstance(script, util.Script))
            res = script.run(args=args, env=env,
                       log=self.log, dryrun=self.dryrun,
                       dir=self.dir)

    def _announce(self, msg):
        if not self.dryrun:
            self.log.debug("Running " + msg)
        else:
            self.log.info("Would run " + msg)

    def _enter(self):
        # Exceptions from the pre- and post-scripts are intended to
        # propagate!
        self._announce(self.pre_desc)
        if self.pre:
            for script in self.pre:
                self._run_script(script)
        elif self.dryrun:
            self.log.info("    (no scripts specified)")

    def _exit(self, type, value, traceback):
        self._announce(self.post_desc)
        if self.post:
            for script in self.post:
                # Maybe use an environment variable instead?
                # (BACKUP_STATUS=<borg returncode>)
                self._run_script(script, args=[str(1 if type else 0)])
        elif self.dryrun:
            self.log.info("    (no scripts specified)")


# Check if we want to run this backup task
def if_enabled(f):
    @wraps(f)
    def wrapper(self, *args, **kwargs):
        if self.enabled:
            return(f(self, *args, **kwargs))
        elif not hasattr(self, 'disabled_msg_shown'):
            self.cx.debug(f"! Task disabled. Set 'run-this' to 'yes' "
                          f"in config section {self.name} to change this.")
            self.disabled_msg_shown = True
            return
    return(wrapper)


class Repository(borg.Repository):
    def __init__(self, name, path, cx,
                 compression=None, remote_path=None, passphrase=None,
                 pre=None, pre_desc=None, post=None, post_desc=None,
                 ):
        self.cx = cx
        super().__init__(name, path=path,
                         compression=compression, remote_path=remote_path,
                         passphrase=passphrase,
                         borg=cx.borg,
                         )
        self._lock = self.cx.lock(str(self))
        self.scripts = PrePostScript(pre, pre_desc, post, post_desc,
                                     cx.dryrun, cx.log, cx.confdir)
        self.lazy = False

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

    def to_yaml(self):
        """ NOTE: This doesn't round-trip, since defaults and any information
        (passphrase for now) that were read from included files will end up in
        the output.
        """
        out = {
            'path': self.path,
        }
        if self.passphrase: out['passphrase'] = self.passphrase
        if self.compression: out['compression'] = self.compression
        if self.remote_path: out['remote-path'] = self.remote_path
        if self.scripts.pre: out['mount'] = self.scripts.pre
        if self.scripts.post: out['umount'] = self.scripts.post

        return out

    def __equal__(self, other):
        return NotImplementedError()

    def __call__(self, *, lazy=False):
        self.lazy = lazy
        return(self)

    def __enter__(self):
        self._lock.__enter__()
        self.scripts(lazy=self.lazy).__enter__()
        self.lazy = False

    def __exit__(self, *exc):
        self.scripts.__exit__(*exc)
        self._lock.__exit__(*exc)

    def check(self, progress, **kwargs):
        with self:
            self.cx.borg.check(self,
                               handlers=self.cx.handler_factory(
                                   progress=progress,
                               ),
                               **kwargs
                               )


class Task():
    KEEP_INTERVALS = ('within', 'hourly', 'daily', 'weekly', 'monthly',
            'yearly')

    def __init__(self, name, cx,
                 repo, enabled, prefix, keep,
                 includes, include_file, exclude_file, path_prefix,
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
        self.path_prefix = path_prefix

        self.lazy = False
        self.scripts = PrePostScript(pre, pre_desc, post, post_desc,
                                     cx.dryrun, cx.log, cx.confdir)

    @classmethod
    def from_yaml(cls, name, cfg, cx):
        try:
            if 'repository' not in cfg:
                raise InvalidConfigurationError("'repository' is mandatory "
                                                "for each task in config")

            def verify_intervals(intervals):
                if not all(k in cls.KEEP_INTERVALS for k in intervals):
                    raise InvalidConfigurationError()
            keep = cfg.get('keep', [])
            if isinstance(keep, dict):
                # A single prune run
                verify_intervals(keep)
                keep = [keep]
            elif isinstance(keep, list):
                # A list of multiple, succesive prune runs
                for k in keep:
                    verify_intervals(k)
            else:
                raise InvalidConfigurationError()

            include_file = cfg.get('include-file', None)
            exclude_file = cfg.get('exclude-file', None)
            includes = cfg.get('includes', [])
            if not includes and not include_file:
                raise InvalidConfigurationError(
                        f"Either 'includes' or 'include-file' is mandatory in "
                        f"configuration for task {name}"
                        )
            # Do not load include and exclude files yet since this task might
            # not even be run.
            if include_file:
                include_file = os.path.join(cx.confdir, include_file)
            if exclude_file:
                exclude_file = os.path.join(cx.confdir, exclude_file)

            return cls(
                name,
                cx=cx,
                repo=cx.repos[cfg['repository']],
                enabled=cfg.get('run-this', True),
                prefix=cfg.get('prefix', '{hostname}'),
                keep=keep,
                includes=includes,
                include_file=include_file,
                exclude_file=exclude_file,
                path_prefix=cfg.get('path-prefix', ''),
                # PrePostScript args
                pre=cfg.get('pre', None),
                pre_desc=f"'{name}' pre-backup script",
                post=cfg.get('post', None),
                post_desc=f"'{name}' post-backup script",
            )
        except (KeyError, ValueError, TypeError) as e:
            raise InvalidConfigurationError(str(e))

    def to_yaml(self):
        """ NOTE: This doesn't round-trip, since defaults are not written to
        the output.
        """
        out = {
            'repository': self.repo.name,
            'run-this': self.enabled,
        }
        if self.keep: out['keep'] = self.keep
        if self.includes: out['includes'] = self.includes
        if self.include_file: out['include-file'] = self.include_file
        if self.exclude_file: out['exclude-file'] = self.exclude_file
        if self.prefix != '{hostname}': out['prefix'] = self.prefix
        if self.scripts.pre: out['pre'] = self.scripts.pre
        if self.scripts.post: out['post'] = self.scripts.post

        return out

    def __equal__(self, other):
        return NotImplementedError()

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

        includes = [i.rstrip('\r\n') for i in includes]
        excludes = [e.rstrip('\r\n') for e in excludes]

        # TODO: Proper error handling for invalid paths
        assert(all(os.path.isabs(i) for i in includes))
        assert(all(os.path.isabs(e) for e in excludes))

        if self.path_prefix:
            assert(os.path.isabs(self.path_prefix))
            # Strip the initial '/' such that os.path.join will treat these
            # as relative paths
            assert(all(i.startswith(os.sep) for i in includes))
            assert(all(e.startswith(os.sep) for e in excludes))

            includes = [i.lstrip(os.sep) for i in includes]
            excludes = [e.lstrip(os.sep) for e in excludes]

            includes = [os.path.join(self.path_prefix, i) for i in includes]
            excludes = [os.path.join(self.path_prefix, e) for e in excludes]

        # run the backup
        with self:
            self.cx.borg.create(
                self.repo,
                includes, excludes,
                prefix=f'{self.prefix}-{{now:%Y-%m-%d_%H:%M:%S}}',
                stats=True,
                handlers=self.cx.handler_factory(progress=progress)
            )

    @if_enabled
    def prune(self):
        try:
            with self:
                for intervals in self.keep:
                    self.cx.borg.prune(self.repo,
                                       intervals,
                                       prefix=f'{self.prefix}-',
                                       handlers=self.cx.handler_factory()
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
    def __init__(self, confdir, dryrun, verbose, term, log, repos, tasks):
        self.confdir = confdir
        self.borg = Borg(dryrun)
        self.dryrun = dryrun
        self.term = term
        self.log = log
        self.verbose = verbose
        self.repos = repos or dict()
        self.tasks = tasks or dict()
        self.handler_factory = None

    @classmethod
    def from_configuration(cls, confdir, conffile):
        term = terminal.Terminal()
        handler = logging.StreamHandler(term)
        handler.terminator = ''
        logging.basicConfig(
            format='{name}: {message}', style='{',
            level=logging.WARNING,
            handlers=[handler],
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
                 verbose=verbose, term=term, log=log,
                 repos=None, tasks=None,
                 )
        cx.repos = {repo: Repository.from_yaml(repo, rcfg, cx)
                    for repo, rcfg in cfg['repositories'].items()
                    }
        cx.tasks = {task: Task.from_yaml(task, tcfg, cx)
                    for task, tcfg in cfg['tasks'].items()
                    }

        return cx
    
    @classmethod
    def to_configuration(cls, confdir, conffile):
        raise NotImplementedError()

    @property
    def verbose(self):
        return self._verbose

    @verbose.setter
    def verbose(self, value):
        self._verbose = value
        if self.log:
            if value:
                self.log.setLevel(logging.DEBUG)
            else:
                self.log.setLevel(logging.WARNING)

    @property
    def dryrun(self):
        return self._dryrun

    @dryrun.setter
    def dryrun(self, value):
        self._dryrun = value
        self.borg.dryrun = value
        for obj in itertools.chain(
                getattr(self, 'tasks', {}).values(),
                getattr(self, 'repos', {}).values()
                ):
            obj.scripts.dryrun = value

    def validate_repos(self, repos):
        try:
            repos = [self.repos[r] for r in repos]
        except KeyError as e:
            self.error(f'No such repository: {e}')
            raise SystemExit()
        repos = repos or self.repos
        return repos

    def validate_tasks(self, tasks):
        try:
            tasks = [self.tasks[t] for t in tasks]
        except KeyError as e:
            self.error(f'No such task: {e}')
            raise SystemExit()
        tasks = tasks or self.tasks
        repos = set(t.repo for t in tasks if t.enabled)
        return (tasks, repos)

    def lock(self, *args):
        return ProcessLock('sya' + self.confdir + '-'.join(*args))

    @property
    def handler_factory(self):
        return self._handler_factory

    @handler_factory.setter
    def handler_factory(self, func):
        self._handler_factory = func or (lambda: None)

    # def print(self, msg):
    #     if self.log:
    #         self.log.info(msg)
    #         print(msg)

    def debug(self, msg):
        if self.log:
            self.log.debug(msg)

    def info(self, msg):
        if self.log:
            self.log.info(msg)

    def warning(self, msg):
        if self.log:
            self.log.warning(msg)

    def error(self, msg):
        if self.log:
            self.log.error(msg)

# vim: ts=4 sw=4 expandtab
