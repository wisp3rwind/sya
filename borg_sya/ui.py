# TODO: Maybe do not depend on Glib when in commandline mode, resort to dbus,
# cf.  https://github.com/kivy/plyer/blob/master/plyer/platforms/linux/notification.py
# Or maybe use http://ntfy.readthedocs.io/en/latest/#linux-desktop-notifications-linux

import gi
gi.require_version('Notify', '0.7')

from gi.repository import Notify, GdkPixbuf
from functools import lru_cache

if not Notify.init('Borg SYA'):
    raise SystemExit(1)

# class Icon():
#     def __init__(pixbuf):
#         self._image = pixbuf
#     @functools.lru_cache
#     @classmethod
#     def new(path):
#         self.__init__(GdkPixbuf.Pixbuf.new_from_file(path))


class DesktopNotification():
    """
        >>> DesktopNotification('Title', 'Body text').show()
    """

    def __init__(self,
                 summary,
                 body,
                 icon=None,
                 timeout=None,
                 urgency=None,
                 ):
        self._notification = Notify.Notification.new(summary, body, icon)
        self.timeout = timeout
        self.urgency = urgency

        return self

    @property
    def timeout(self):
        raise NotImplementedError()

    @timeout.setter
    def timeout(self, t):
        if t in ['never', -1]:
            self._notification.set_timeout(Notify.EXPIRES_NEVER)
        elif not t:  # None, '', 0
            self._notification.set_timeout(Notify.EXPIRES_DEFAULT)
        else:  # numeric, not verified
            self._notification.set_timeout(t)

    @property
    def urgency(self):
        raise NotImplementedError()

    @urgency.setter
    def urgency(self, u):
        if u == 'low':
            self._notification.set_urgency(Notify.Urgency.LOW)
        elif u == 'normal' or not u:
            self._notification.set_urgency(Notify.Urgency.NORMAL)
        elif u == 'critical':
            self._notification.set_urgency(Notify.Urgency.CRITICAL)
        else:
            raise ValueError()

    @property
    def icon(self):
        raise NotImplementedError()

    @icon.setter
    def icon(self, file):
        # TODO: Update icon.
        raise NotImplementedError()

    def show(self):
        self._notification.show()
