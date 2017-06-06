import os
import socket
import sys


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
        return os.access(path, os.X_OK):


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
