import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, Gtk, Gio, GObject, GLib
BindingFlags = GObject.BindingFlags


class CustomExpander(Gtk.Box):
    transition_duration = GObject.Property(type=GObject.TYPE_UINT,
            default=150)
    transition_type = GObject.Property(type=Gtk.RevealerTransitionType,
            default=Gtk.RevealerTransitionType.SLIDE_DOWN)
    child_revealed = GObject.Property(type=GObject.TYPE_BOOLEAN, default=False)
    reveal_child = GObject.Property(type=GObject.TYPE_BOOLEAN, default=False)

    def __init__(self, *args, **kwargs):
        kwargs.update(dict(orientation="vertical"))
        super().__init__(*args, **kwargs)

        self.title = Gtk.Label("label", hexpand=True)
        self.separator = Gtk.Separator(
            orientation="vertical",
            margin_start=6,
            margin_end=0,
            margin_top=9,
            margin_bottom=9,
        )
        self.button_image = Gtk.Image(stock="gtk-go-forward", pixel_size=32)
        css_provider = Gtk.CssProvider()
        # FIXME: sync this duration to the transition_duration Property
        css_provider.load_from_data(
            b'''
            image { transition: -gtk-icon-transform 0.1s; }
            .closed { -gtk-icon-transform: rotate(0deg); }
            .open { -gtk-icon-transform: rotate(90deg); }
            ''',
        )
        self.cx = self.button_image.get_style_context()
        self.cx.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.cx.add_class("closed")
        self.button = Gtk.Button(
            relief="none",
            image=self.button_image,
        )
        self.frame = Gtk.AspectFrame(
            ratio=1,
            shadow_type=Gtk.ShadowType.NONE,
            margin_start=6,
            margin_end=6,
            margin_top=6,
            margin_bottom=6,
        )
        self.frame.add(self.button)

        self.title_box = Gtk.Box(orientation="horizontal")
        self.title_box.pack_start(self.title, expand=True, fill=True, padding=0)
        self.title_box.pack_end(self.frame, expand=False, fill=False, padding=0)
        self.title_box.pack_end(self.separator, expand=False, fill=False, padding=0)

        self.event_box = Gtk.EventBox(above_child=True)
        self.event_box.add(self.title_box)

        self.revealer = Gtk.Revealer(reveal_child=False)

        # self.vbox = Gtk.Box(orientation="vertical")
        super().pack_start(self.event_box, expand=False, fill=False, padding=0)
        super().pack_end(self.revealer, expand=False, fill=False, padding=0)

        self.bind_property("transition-type",
                self.revealer, "transition-type",
                BindingFlags.SYNC_CREATE | BindingFlags.BIDIRECTIONAL)
        self.bind_property("transition-duration",
                self.revealer, "transition-duration",
                BindingFlags.SYNC_CREATE | BindingFlags.BIDIRECTIONAL)
        self.revealer.bind_property("child-revealed",
                self, "child-revealed",
                BindingFlags.DEFAULT)
        self.bind_property("reveal-child",
                self.revealer, "reveal-child",
                BindingFlags.SYNC_CREATE | BindingFlags.BIDIRECTIONAL)
        
        # self.event_box.props.events = Gdk.EventMask.BUTTON_PRESS_MASK
        self.button.connect("clicked", self.__on_button_clicked)
        self.event_box.connect("button-release-event", self.__on_button_clicked)

    # def add_child(self, builder, child, type=None):
    #     if type == "label":
    #         self.title_box.remove(self.title)
    #         self.title_box.pack_start(child)
    #     else:
    #         self.revealer.add_child(builder, child, None)

    def __on_button_clicked(self, *args, **kwargs):
        old = self.revealer.props.child_revealed
        self.revealer.props.reveal_child = not old
        self.cx.remove_class("closed")
        self.cx.remove_class("open")
        self.cx.add_class("closed" if old else "open")

    def set_title(self, widget):
        self.title_box.remove(self.title)
        self.title_box.pack_start(widget, True, True, 0)

    def add(self, content):
        self.revealer.add(content)

