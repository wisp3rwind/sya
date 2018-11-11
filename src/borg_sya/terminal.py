import blessings
from contextlib import contextmanager
import itertools
import sys
import threading


class Spinner():
    SYMBOLS = ['[' + s + ']' for s in '|/-\\']  # python's r'' strings are weird...

    def __init__(self, cli, pos, symbols=None):
        """

        The caller must hold a lock for the stderr sream.
        """
        symbols = symbols or self.SYMBOLS
        self._symbols = itertools.cycle(symbols)
        self.pos = pos
        self._cli = cli

    def __call__(self, msg):
        """
        >>> with cli.spinner("Starting...") as status:
                # be productive
        ...     status("x %")
        """
        with self._cli._locks[self._cli.stderr]:
            self._advance(msg)

    def update(self, msg):
        self(msg)

    def _advance(self, msg):
        self.msg = msg
        self._current_symbol = next(self._symbols)
        self.draw()

    def draw(self):
        with self._cli.replace_line_err(self.pos):
            self._cli._print(self._current_symbol + ' ' + self.msg,
                             term=self._cli.stderr,
                             end='',
                             flush=True,  # Otherwise might not happen due to lack of EOL
                             )


class DummySpinner(Spinner):
    """ Doesn't actually spin, but can be used as a drop-in when headed into a
    pipe.
    """
    def __init__(self, *args, silent=False, **kwargs):
        self.silent = silent
        super().__init__(*args, **kwargs)

    def draw(self):
        if not self.silent:
            self._cli._print(self._current_symbol + ' ' + self.msg,
                             term=self._cli.stderr,
                             flush=True,
                             )


class Terminal():
    def __init__(self):
        self.stdout = blessings.Terminal(stream=sys.stdout)
        self.stderr = blessings.Terminal(stream=sys.stderr)
        self._locks = {
            self.stdout: threading.Lock(),
            self.stderr: threading.Lock(),
        }
        # FIXME: restore cursor state on exit
        # self.print_err(self.stderr.hide_cursor, end='')

        self._spinners = []

    @property
    def height(self):
        return self.term.height

    @property
    def width(self):
        return self.term.width

    @contextmanager
    def hidden_cursor(self):
        with self.stderr.hidden_cursor:
            yield

    @contextmanager
    def replace_line(self, pos, term=None):
        pos = len(self._spinners) - pos
        term = term or self.stdout
        with term.location():
            print(term.move_up * pos + term.clear_eol + term.clear_bol,
                  file=term.stream,
                  end='',
            )
            yield

    @contextmanager
    def replace_line_err(self, pos):
        with self.replace_line(pos, term=self.stderr):
            yield

    def _print(self, msg, end='\n', term=None, flush=False):
        print(msg, file=term.stream, end=end, flush=flush)

    def output(self, msg, end='\n'):
        """ Print to stdout, i.e. actual output
        """
        term = self.stdout
        with self._locks[term]:
            self._print(
                msg,
                end=end,
                term=term,
                flush=True,
            )

    def print(self, msg, end='\n'):
        """ Print to stderr (above all of the spinners), i.e. logs etc. 
        """
        if end != '\n':
            # Disallow because this would be hard to handle when spinners are
            # active.
            raise ValueError()

        term = self.stderr
        with self._locks[term]:
            self._print(
                term.move_up * len(self._spinners)
                + msg
                + term.move_down * len(self._spinners),  # new lines
                end=end,
                term=term,
            )
            for spinner in self._spinners:
                spinner.draw()
            self._flush(term)
    
    def _flush(self, term):
        term.stream.flush()

    def flush(self):
        """ Intended to be called by a logging.StreamHandler.
        """
        self._flush(self.stderr)

    def write(self, text):
        """ Intended to be called by a logging.StreamHandler.
        """
        # hack, since StreamHandler will always print the terminator using a
        # separate call to write()
        if text:
            self.print(text)

    @contextmanager
    def spinner(self, msg, symbols=None, silent_for_pipes=False):
        term = self.stderr

        with self._locks[term]:
            if term.does_styling:
                s = Spinner(self, len(self._spinners), symbols)
                # add one line
                self._print('', term=term, flush=True)
                self._spinners.append(s)
                s._advance(msg)
            else:
                s = DummySpinner(self, len(self._spinners), msg, symbols,
                                 silent=silent_for_pipes
                                 )

        yield s

        if term.does_styling:
            with self._locks[term]:
                idx = self._spinners.index(s)
                for spinner in self._spinners[idx + 1:]:
                    # These are now closer to the bottom line
                    spinner.pos -= 1
                self._spinners.remove(s)

                # remove one line
                self._print(term.move_up + term.clear_eol + term.clear_bol,
                            term=term,
                            end='',
                            flush=True,
                )

                # redraw all below below the removed one such that they
                # actually move up
                for spinner in self._spinners[:idx]:
                    spinner.draw()


if __name__ == '__main__':
    """ Basic test.

    Run as
    >>> python terminal.py
    and
    >>> python terminal.py 2>&1 | tee
    """
    import time
    T = 0.5
    t = Terminal()
    time.sleep(T)
    with t.spinner("foo") as s:
        time.sleep(T)
        with t.spinner("bar", silent_for_pipes=True) as s2:
            for i in range(4):
                if i == 3:
                    t.print("something\nthat spans two lines")
                time.sleep(T)
                s("foo" + str(i))
                time.sleep(T)
                s2("bar" + str(i))
            time.sleep(T)
        time.sleep(T)
