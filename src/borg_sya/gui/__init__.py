import atexit
import importlib.resources
import logging
import os.path
import sys

import click
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gio

from ..core import *
from ..core.borg import BorgError, DefaultHandlers, InvalidBorgOptions


with importlib.resources.path("borg_sya.gui", "data") as data_dir:
    gresources = Gio.resource_load(os.path.join(data_dir, "sya.gresource"))
    Gio.resources_register(gresources)


class BorgHandlers(DefaultHandlers):
    pass


class Handlers():
    def onDestroy(self, *args):
        Gtk.main_quit()

    def on_back_button_clicked(self, *args):
        pass


@Gtk.Template.from_resource("/com/example/Sya/repo_list_row.ui")
class RepoListRow(Gtk.ListBoxRow):
    __gtype_name__ = "RepoListRow"

    repo_icon = Gtk.Template.Child()
    repo_name_label = Gtk.Template.Child()
    repo_loc_label = Gtk.Template.Child()
    repo_avail_label = Gtk.Template.Child()
    repo_total_label = Gtk.Template.Child()
    repo_usage_level = Gtk.Template.Child()

    grid = Gtk.Template.Child()

    def __init__(self, repo):
        super().__init__()

        self.repo_icon.props.icon_name = "folder"
        self.repo_name_label.props.label = repo.name
        self.repo_loc_label.props.label = repo.path
        # TODO: Asynchronously get disk usage
        self.repo_avail_label.props.label = "Unknown"
        self.repo_total_label.props.label = "Unknown"
        # TODO: add offset values to GtkLevelBAr in order to change color
        # depending on value
        self.repo_usage_level.props.min_value = 0.0
        self.repo_usage_level.props.max_value = 1.0
        self.repo_usage_level.props.value = 0.42


# @Gtk.Template.from_resource("/com/example/Sya/repo_list.ui")
class RepoListBox(Gtk.ListBox):
    __gtype_name__ = "RepoListBox"

    def __init__(self, title, repos):
        pass

    @staticmethod
    def update_header(row, prev_row):
        if (prev_row and not row.get_header()):
            row.set_header(Gtk.Separator(orientation="horizontal"))
        else:
            row.set_header(None)


def gui_main(cx):
    builder = Gtk.Builder()
    builder.add_from_resource("/com/example/Sya/main.ui")
    # builder.add_from_file("gui/data/main.ui")

    builder.connect_signals(Handlers())

    add_repo_page = builder.get_object("add_repo_page")
    repo_info_page = builder.get_object("repo_info_page")
    add_task_page = builder.get_object("add_task_page")
    task_info_page = builder.get_object("task_info_page")

    repo_list_box = builder.get_object("repo_list_box")
    repo_list_box.set_header_func(RepoListBox.update_header)
    for name, repo in cx.repos.items():
        repo_list_box.add(RepoListRow(repo))

    mainWindow = builder.get_object("mainWindow")
    mainWindow.show_all()

    Gtk.main()



@click.command()
@click.option('-d', '--config-dir', 'confdir',
              default=DEFAULT_CONFDIR,
              help=f"Configuration directory, default is {DEFAULT_CONFDIR}")
def main(confdir):
    handler = logging.StreamHandler(sys.stderr)

    try:
        cx = Context.from_configuration(handler, confdir, DEFAULT_CONFFILE)
    except OSError:
        print(f"Configuration file at "
              f"'{os.path.join(confdir, DEFAULT_CONFFILE)}' "
              f"not found or not accessible.",
              file=sys.stderr)
        raise click.Abort()
    except InvalidConfigurationError as e:
        print(e, file=sys.stderr)
        raise click.Abort()

    atexit.register(logging.shutdown)
    cx.verbose = True

    cx.handler_factory = lambda **kw: BorgHandlers(cx.log, **kw)

    gui_main(cx)
