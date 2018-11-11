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
            self._cli._redraw_spinners()

    def update(self, msg):
        self(msg)

    def _advance(self, msg):
        self.msg = msg
        self._current_symbol = next(self._symbols)

    def render(self, width):
        return self._current_symbol + ' ' + self.msg


class DummySpinner(Spinner):
    """ Doesn't actually spin, but can be used as a drop-in when headed into a
    pipe.
    """
    def __init__(self, *args, silent=False, **kwargs):
        self.silent = silent
        super().__init__(*args, **kwargs)

    def render(self, width):
        if not self.silent:
            return super().render(width)
        else:
            return ''


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
        term = term or self.stdout
        with term.location():
            print(term.move_down * pos + term.clear_eol + term.clear_bol,
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
                term.move_x(0) + term.clear_eol + msg,
                term=term,
            )
            self._print(
                ('\n' + term.move_up) * msg.count('\n'),
                term=term,
                end='',
            )
            self._redraw_spinners()
    
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

    def _redraw_spinners(self):
        """
        
        Lock must be held.
        """
        term = self.stderr
        with term.location():
            last = self._spinners[-1] if self._spinners else None
            for spinner in self._spinners:
                self._print(
                    term.move_x(0) + term.clear_eol + spinner.render(term.width),
                    term=term,
                    end='',
                )
                if spinner is not last:
                    self._print(term.move_down, term=term, end='')
            self._print(term.clear_eos, end='', term=term)
        self._print(term.move_x(0), term=term, end='', flush=True)  # flush

    @contextmanager
    def spinner(self, msg, symbols=None, silent_for_pipes=False):
        term = self.stderr

        with self._locks[term]:
            if term.does_styling:
                s = Spinner(self, len(self._spinners), symbols)
                s._advance(msg)
                self._spinners.append(s)
                self._print('\n' + term.move_up, term=term, end='', flush=True)
                self._redraw_spinners()
            else:
                s = DummySpinner(self, len(self._spinners), msg, symbols,
                                 silent=silent_for_pipes
                                 )
                self.print(s.render())

        yield s

        if term.does_styling:
            with self._locks[term]:
                self._spinners.remove(s)
                self._redraw_spinners()


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
