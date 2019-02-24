from io import BytesIO
import logging
import os
import socket
import subprocess
import sys
from subprocess import Popen
from threading import Thread
from wcwidth import wcswidth
from yaml import YAMLObject
from yaml.loader import SafeLoader


def which(command):
    for d in os.environ['PATH'].split(':'):
        try:
            for binary in os.listdir(d):
                if binary == command:
                    return os.path.join(d, command)
        except OSError:
            pass
    raise RuntimeError(f"Command not found: {command}.")


def isexec(path):
    if os.path.isfile(path):
        return os.access(path, os.X_OK)


INDENT = 4 * ' '
def format_commandline(args):
    lines = []
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith('-'):
            if (i + 1) < len(args) and not args[i + 1].startswith('-'):
                lines.append(INDENT + a + ' ' + args[i + 1])
                i += 1
            else:
                lines.append(INDENT + a)
        else:
            lines.append(INDENT + a)
        i += 1
    return '\n'.join(lines)


class LockInUse(Exception):
    pass


class ProcessLock():
    """This reentrant lock class comes from this very elegant way of having a
    pid lock in order to prevent multiple instances from running on the same
    host.
    http://stackoverflow.com/a/7758075
    """

    def __init__(self, process_name):
        self._recursion_level = 0
        self._pname = process_name

    def __enter__(self):
        self.acquire()

    def __exit__(self, type, value, traceback):
        self.release()

    def acquire(self):
        if not self._recursion_level:
            self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                # The bind address is the one of an abstract UNIX socket
                # (begins with a null byte) followed by an address which exists
                # in the abstract socket namespace (Linux only). See unix(7).
                self._socket.bind('\0' + self._pname)
            except socket.error:
                raise LockInUse
        self._recursion_level += 1

    def release(self):
        self._recursion_level -= 1
        if not self._recursion_level:
            self._socket.close()


class LazyReentrantContextmanager():
    def __init__(self):
        self.nesting_level = 0
        self.lazy = False
        self.entered = False

    def __call__(self, *, lazy=False):
        self.lazy = lazy
        return(self)

    def _enter(self):
        raise NotImplementedError()

    def _exit(self, type, value, traceback):
        raise NotImplementedError()

    def __enter__(self):
        if self.lazy:
            # Only actually enter at the next invocation. This still increments
            # the nesting_level so that cleanup will nevertheless occur at this
            # outer level.
            self.lazy = False
        elif not self.entered:
            self._enter()
            self.entered = True
        self.nesting_level += 1

    def __exit__(self, type, value, traceback):
        self.nesting_level -= 1
        if self.entered and self.nesting_level == 0:
            self._exit(type, value, traceback)
            self.entered = False


def indent(text, by=4):
    by = by * ' '
    return by + text.replace('\n', '\n' + by)


def truncate_path(path, width):
    if wcswidth(path) <= width:
        # This includes the case wcswidth(path) == -1, i.e. non-printable (borg/issues/1090)
        return path

    i = len(path) // 2
    d = 1
    while True:
        trunc = path[:i-d] + '…' + path[i+d:]
        if wcswidth(trunc) <= width:
            return trunc
        d += 1



class Script(YAMLObject):
    """A YAML object with a tag to be set by subclasses that reads a scalar
    node and returns a callable that executes the node's text.
    """

    yaml_loader = SafeLoader

    def __init__(self, script):
        self.script = script

    def run_popen(self, cmdline, **popen_args):
        # https://stackoverflow.com/questions/4984428/python-subprocess-get-childrens-output-to-file-and-terminal
        # https://stackoverflow.com/questions/17190221/subprocess-popen-cloning-stdout-and-stderr-both-to-terminal-and-variables
        def tee(infile, *files):
            def fanout(infile, *files):
                while True:
                    d = infile.readline(128)
                    if len(d):
                        for f, flush in files:
                            f.write(d)
                            if flush:
                                f.flush()
                    else:
                        break
                infile.close()
            t = Thread(target=fanout, args=(infile, *files))
            t.daemon = True
            t.start()
            return(t)

        p = Popen(cmdline, env=self.env,
                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                  **popen_args)
        out = BytesIO()
        err = BytesIO()
        t_out = tee(p.stdout, (sys.stdout.buffer, True), (out, False))
        t_err = tee(p.stderr, (sys.stderr.buffer, True), (err, False))
        t_out.join()
        t_err.join()
        p.wait()
        # TODO: Are there encoding issues here? (or above?)
        out = out.getvalue().decode('utf8')
        err = err.getvalue().decode('utf8')

        if p.returncode:
            raise RuntimeError(f"{cmdline} returned {p.returncode}:\n{err}")
        return(out + err)

    def run(self,
            log, args=None, env=None,
            dryrun=False, capture_out=True,
            dir=None, pretend=False):
        if self.script:
            self.log = log
            self.args = args
            self.env = env
            self.dryrun = dryrun
            self.capture_out = capture_out
            self.dir = dir
            self._pretend = pretend
            self._run()
            self._pretend = False

    def _run(self):
        raise NotImplementedError()

    def __call__(self, **kwargs):
        self.run(**kwargs)

    @classmethod
    def from_yaml(cls, loader, node):
        """Load a scalar (i.e. a string if the configuration file is valid)
        """
        script = loader.construct_scalar(node)
        return(cls(script))

    @classmethod
    def to_yaml(cls, dumper, data):
        raise NotImplementedError()

    def __str__(self):
        return(f"{self.__class__.__name__}(\n'{self.script}')")


class ExternalScript(Script):
    """
    """
    yaml_tag = '!external_script'

    def _run(self):
        if not os.path.isabs(self.script):
            script = os.path.join(self.dir, self.script)
        if not os.path.isfile(script):
            raise RuntimeError()

        if isexec(script):
            cmdline = [script]
            if self.args is not None:
                cmdline.extend(self.args)
            if self._pretend:
                return f"$ {cmdline if isinstance(cmdline, str) else ' '.join(cmdline)}"
            else:
                return(self.run_popen(cmdline))
        else:
            raise RuntimeError(f"{script} exists, but cannot be "
                               f"executed by the current user.")


class ShellScript(Script):
    """
    """
    yaml_tag = '!sh'

    def _run(self):
        if self.args:
            # logging.debug("ShellScript doesn't support `args`.")
            pass
        if self._pretend:
            return indent(self.script)
        else:
            return self.run_popen(self.script, shell=True)


class PythonScript(Script):
    """
    """
    yaml_tag = '!python'

    def _run(self):
        if self.args or self.env:
            raise NotImplementedError()

        if self._pretend:
            return indent(f">>> {'... '.join(script.splitlines(keepends=True))}")
        else:
            # Propagate exceptions
            return(exec(self.script))
