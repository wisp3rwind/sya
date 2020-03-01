import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, Gtk, Gio, GObject, GLib
BindingFlags = GObject.BindingFlags


@Gtk.Template.from_resource("/com/example/Sya/compression_chooser.ui")
class CompressionChooser(Gtk.Box):
    __gtype_name__ = "CompressionChooser"

    algorithm_combo = Gtk.Template.Child()
    level_combo = Gtk.Template.Child()
    auto_button = Gtk.Template.Child()

    def set_specs(self, specs):
        for alg in specs.keys():
            self.algorithm_combo.append(id=alg, text=alg)
        self.algorithm_combo.set_active_id(next(iter(specs.keys())))
        self.level_combo.props.sensitive = False

    def select_spec(self, alg, level):
        self.level_combo.props.sensitive = True
        pass
