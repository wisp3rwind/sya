import atexit
import importlib.resources
import logging
import os.path
import sys

import click
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gio, GObject, GLib
BindingFlags = GObject.BindingFlags

from ..core import *
from ..core.borg import BorgError, DefaultHandlers, InvalidBorgOptions
from .custom_expander import CustomExpander


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


@Gtk.Template.from_resource("/com/example/Sya/repo_entry_title.ui")
class RepoEntryTitle(Gtk.Grid):
    __gtype_name__ = "RepoEntryTitle"

    repo_icon = Gtk.Template.Child()
    repo_name_label = Gtk.Template.Child()
    repo_loc_label = Gtk.Template.Child()
    repo_avail_label = Gtk.Template.Child()
    repo_total_label = Gtk.Template.Child()
    repo_usage_level = Gtk.Template.Child()

    def __init__(self, repo):
        super().__init__()

        if repo == "add_new":
            self.repo_icon.props.icon_name = "list-add"
            self.repo_name_label.props.label = "Add new repository"
            self.repo_loc_label.props.visible = False
            self.repo_avail_label.props.visible = False
            self.repo_total_label.props.visible = False
            self.repo_usage_level.props.visible = False
        else:
            self.repo_icon.props.icon_name = "drive-harddisk"
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


@Gtk.Template.from_resource("/com/example/Sya/repo_entry_detail.ui")
class RepoEntryDetail(Gtk.Box):
    __gtype_name__ = "RepoEntryDetail"

    def __init__(self, repo):
        super().__init__()

        if repo == "add_new":
            pass
        else:
            pass


@Gtk.Template.from_resource("/com/example/Sya/task_list_row.ui")
class TaskListRow(Gtk.ListBoxRow):
    __gtype_name__ = "TaskListRow"

    task_icon = Gtk.Template.Child()
    task_name_label = Gtk.Template.Child()
    repo_name_label = Gtk.Template.Child()

    def __init__(self, task):
        super().__init__()

        if task == "add_new":
            self.task_icon.props.icon_name = "list-add"
            self.task_name_label.props.label = "Add new task"
        else:
            self.task_icon.props.icon_name = "gtk-ok"
            self.task_name_label.props.label = task.name
            self.repo_name_label.props.label = task.repo.name


@Gtk.Template.from_resource("/com/example/Sya/repo_list.ui")
class RepoList(Gtk.Box):
    __gtype_name__ = "RepoList"

    title = GObject.Property(type=str, default="")
    list_title = Gtk.Template.Child()
    repo_list_box = Gtk.Template.Child()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def setup(self):
        # This cannot be done in __init__ because there, the PyGObject
        # bindings have not yet retrieved the Template.Child()ren.
        self.repo_list_box.set_header_func(self.update_header)
        self.bind_property("title", self.list_title, "label",
                BindingFlags.SYNC_CREATE | BindingFlags.BIDIRECTIONAL)
        self.hide()
        self.i = 1

    @staticmethod
    def update_header(row, prev_row):
        if (prev_row and not row.get_header()):
            row.set_header(Gtk.Separator(orientation="horizontal"))
        else:
            row.set_header(None)

    def hide(self, flag=True):
        self.props.visible = flag

    def populate(self, repo):
        row = Gtk.ListBoxRow()
        exp = CustomExpander()
        row.add(exp)
        exp.set_title(RepoEntryTitle(repo))
        exp.add(RepoEntryDetail(repo))
        self.repo_list_box.add(row)

        self.hide(False)


@Gtk.Template.from_resource("/com/example/Sya/task_list.ui")
class TaskList(Gtk.Box):
    __gtype_name__ = "TaskList"

    title = GObject.Property(type=str, default="")
    list_title = Gtk.Template.Child()
    task_list_box = Gtk.Template.Child()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def setup(self):
        # This cannot be done in __init__ because there, the PyGObject
        # bindings have not yet retrieved the Template.Child()ren.
        self.task_list_box.set_header_func(self.update_header)
        self.bind_property("title", self.list_title, "label",
                BindingFlags.SYNC_CREATE | BindingFlags.BIDIRECTIONAL)
        self.hide()

    @staticmethod
    def update_header(row, prev_row):
        if (prev_row and not row.get_header()):
            row.set_header(Gtk.Separator(orientation="horizontal"))
        else:
            row.set_header(None)

    def hide(self, flag=True):
        # self.props.visible = not flag
        pass

    def populate(self, task):
        self.task_list_box.add(TaskListRow(task))

        self.hide(False)


@Gtk.Template.from_resource("/com/example/Sya/task_info_page_no_repo.ui")
class NoRepoFoundPage(Gtk.Box):
    __gtype_name__ = "NoRepoFoundPage"


@Gtk.Template.from_resource("/com/example/Sya/repo_info_page.ui")
class RepoInfoPage(Gtk.Box):
    __gtype_name__ = "RepoInfoPage"

    scrolled_window = Gtk.Template.Child()
    add_new_list = Gtk.Template.Child()
    local_repo_list = Gtk.Template.Child()
    remote_repo_list = Gtk.Template.Child()

    def setup(self):
        self.add_new_list.setup()
        self.local_repo_list.setup()
        self.remote_repo_list.setup()

    def populate(self, cx):
        self.add_new_list.populate("add_new")

        for repo in cx.repos.values():
            self.local_repo_list.populate(repo)


@Gtk.Template.from_resource("/com/example/Sya/task_info_page.ui")
class TaskInfoPage(Gtk.Box):
    __gtype_name__ = "TaskInfoPage"

    scrolled_window = Gtk.Template.Child()
    add_new_list = Gtk.Template.Child()
    local_task_list = Gtk.Template.Child()
    remote_task_list = Gtk.Template.Child()

    def setup(self):
        self.add_new_list.setup()
        self.local_task_list.setup()
        self.remote_task_list.setup()

    def populate(self, cx):
        self.add_new_list.populate("add_new")

        for task in cx.tasks.values():
            self.local_task_list.populate(task)


def gui_main(cx):
    builder = Gtk.Builder()
    builder.add_from_resource("/com/example/Sya/main.ui")
    # builder.add_from_file("gui/data/main.ui")

    builder.connect_signals(Handlers())

    repos_page = builder.get_object("repos_page")
    tasks_stack = builder.get_object("task_wrapper_stack")
    tasks_page = builder.get_object("task_info_page")
    no_repo_found_page = builder.get_object("no_repo_found_page")

    repos_page.setup()
    repos_page.populate(cx)

    if len(cx.repos) > 0:
        tasks_stack.set_visible_child(tasks_page)
        tasks_page.setup()
        tasks_page.populate(cx)
    else:
        tasks_stack.set_visible_child(no_repo_found_page)

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
