import click
from contextlib import contextmanager
import logging
import os
import sys
import time
import traceback

from . import InvalidConfigurationError, Context
from .borg import BorgError, DefaultHandlers, InvalidBorgOptions
from .gui import main as gui_main
from .util import LockInUse, truncate_path

DEFAULT_CONFDIR = '/etc/borg-sya'
DEFAULT_CONFFILE = 'config.yaml'
APP_NAME = 'borg-sya'


class BorgHandlers(DefaultHandlers):
    def __init__(self, log, cli, **kwargs):
        # FIXME: actually respect the progress option
        kwargs.pop('progress')
        self.cli = cli
        self._spinners = dict()
        super().__init__(log, **kwargs)

    def __del__(self):
        for contextmanager, _ in self._spinners.values():
            contextmanager.__exit__(None, None, None)
        self._spinners = dict()

    def _get_spinner(self, name):
        try:
            _, spinner = self._spinners[name]
        except KeyError:
            contextmanager = self.cli.spinner('')
            spinner = contextmanager.__enter__()
            self._spinners[name] = (contextmanager, spinner)
        return spinner

    def _close_spinner(self, name):
        try:
            (contextmanager, _) = self._spinners.pop(name)
            contextmanager.__exit__(None, None, None)
        except KeyError:
            pass

    def onArchiveProgress(self, path, **msg):
        spinner = self._get_spinner('onArchiveProgress')
        text = self.format_archive_progress(**msg)
        # FIXME: instead of ' - 15', determine the actual indentation caused by
        # the logger
        term_width = self.cli.stderr.width - 15
        if len(text) <= term_width:
            space = term_width - len(text)
            if space >= 12:
                text += truncate_path(path, space)
        else:
            text = truncate_path(path, term_width)


        spinner.update(text)


@click.group()
@click.option('-d', '--config-dir', 'confdir',
              default=DEFAULT_CONFDIR,
              help=f"Configuration directory, default is {DEFAULT_CONFDIR}")
@click.option('-n', '--dry-run', 'dryrun', is_flag=True,
              help="Do not run backup, don't act.")
@click.option('-v', '--verbose', is_flag=True,
              help="Be verbose and print stats.")
@click.pass_context
def main(ctx, confdir, dryrun, verbose):
    try:
        cx = Context.from_configuration(confdir, DEFAULT_CONFFILE)
    except OSError:
        print(f"Configuration file at "
              f"'{os.path.join(confdir, DEFAULT_CONFFILE)}' "
              f"not found or not accessible.",
              file=sys.stderr)
        raise click.Abort()
    except InvalidConfigurationError as e:
        print(e, file=sys.stderr)
        raise click.Abort()
    if verbose:  # if True in the config file, do not set to False here
        cx.verbose = verbose
    cx.dryrun = dryrun
    cx.handler_factory = lambda **kw: BorgHandlers(cx.log, cx.term, **kw)
    ctx.obj = cx


@main.resultcallback()
@click.pass_context
def exit(cx, *args, **kwargs):
    logging.shutdown()


@main.command(help="Launch the GUI.")
@click.pass_obj
def gui(cx):
    gui_main(cx)


@contextmanager
def handle_errors(cx, repo, action, action_failed):
    try:
        yield
    except InvalidBorgOptions as e:
        cx.error(f"Invalid commandline options: {e}")
    except BorgError as e:
        cx.error(f"Error {e} when {action_failed}.\nYou should investigate.")
    except LockInUse as e:
        cx.error(f"Another process seems to be accessing the "
                 f"repository {repo.name}. Could not {action}.")
    except KeyboardInterrupt as e:
        traceback.print_exc()
        raise


@main.command(help="Do a backup run. If no Task is specified, run all.")
@click.option('-p', '--progress/--no-progress',
              help="Show progress.")
@click.argument('tasks', nargs=-1)
@click.pass_obj
def create(cx, progress, tasks):
    # cx = cx.sub_context('CREATE') # TODO: implement
    tasks, repos = cx.validate_tasks(tasks)
    for task in tasks:
        cx.info(f'-- Backing up using {task} configuration...')
        with task(lazy=True):
            with handle_errors(cx, task.repo,
                               f"create a new archive for task '{task}'",
                               f"backing up task '{task}'",
                               ) as status:
                task.create(progress)
                task.prune()
        cx.info(f'-- Done backing up {task}.')


# TODO: support --archives-only, --repository-only
@main.command(help="Perform a check for repository consistency. "
                   "Repositories can either be specified directly or "
                   "by task. If neither is provided, check all.")
@click.option('-p', '--progress/--no-progress',
              help="Show progress.")
@click.option('-r/-t', '--repo/--task', 'repo', default=False,
              help="Whether to directly name repositories to check or select "
                   "them from tasks.")
@click.option('--repair', 'repair', default=False,
              help="Attempt to repair any inconsistencies found")
@click.option('--verify-data', 'verify_data', default=False,
              help="Perform cryptographic archive data integrity verification.")
@click.argument('items', nargs=-1)
@click.pass_obj
def check(cx, progress, repo, repair, verify_data, items):
    if repo:
        repos = cx.validate_repos(items)
    else:
        _, repos = cx.validate_tasks(items)

    for repo in repos:
        cx.info(f'-- Checking repository {repo.name}...')
        with handle_errors(cx, repo,
                           "check it",
                           f"when checking repository {repo.name}."
                           ):
            repo.check(
                repair=repair,
                verify_data=verify_data,
                progress=progress,
            )
        cx.info(f'-- Done checking {repo.name}.')


@main.command(help="Mount a snapshot. Takes a repository or task and the "
                   "mountpoint as positional arguments. If a repository, "
                   "a prefix can "
                   "be specified as 'repo::prefix'. Optionally append an "
                   "arbitrary number of '^' to choose the last, next-to "
                   "last or earlier archives. Otherwise, all matching "
                   "archives will be mounted.")
# --repo name[^[^ ...]] -> repo
# --repo name::prefix^^ -> repo, prefix
# --task name[^[^ ...]] -> repo, prefix
# --before=2017-02-01T12:45:10
# Maybe change syntax to repo::prefix::{,0,-1,1,-2,2}
#   for {last,first,last,second,second-to-last,third} and so on?
#   -> can prefixes contain colons? If so, maybe prefer repo::prefix -1
@click.option(
    '-r/-t', '--repo/--task', 'repo',
    help="Whether to select archives for a repository or task. "
         "narrowed down further by specifying '--prefix'. "
         "Optionally append an arbitrary number of '^' to choose the "
         "next-to last or earlier archives.")
@click.option('-a', '--all', is_flag=True,
              help="Mount the complete repository. The default is to mount "
                   "only the last archive.")
@click.option('--umask', default=None,
              help="Set umask when mounting")
# TODO: it IS possible to mount a whole archive
@click.argument('item', required=True)
@click.argument('mountpoint', required=True)
@click.pass_obj
def mount(cx, repo, all, umask, item, mountpoint):
    index = len(item)
    item = item.rstrip('^')
    index = index - len(item)

    if repo and not all:
        cx.error("Mounting only the last archive not implemented.")
        raise click.Abort()

    if repo:
        repo, _, prefix = item.partition('::')
        repo = cx.validate_repos([item])[0]
    else:
        tasks, repos = cx.validate_tasks([item])
        assert(len(tasks) == len(repos) == 1)
        task = tasks[0]
        repo = repos[0]
        prefix = task.prefix

    if index and all:
        cx.error(f"Giving {'^' * index} and '--all' conflict.")
        raise click.Abort()

    if prefix and all:
        cx.error(f"Borg doen't support mounting only archives with "
                 f"a given prefix. Mounting only the last archive "
                 f"matching '{prefix}'.")
        all = False

    with repo(lazy=True), handle_errors(
            cx, repo,
            "mount archive(s)"
            f"mounting repository {repo.name}."
            ):
        archive = None
        if not all:
            cx.info(f"-- Searching for last archive from "
                    f"repository '{repo.name}' with prefix '{prefix}'.")
            try:
                # short will only output archive names,
                # last returns only the index+1 most recent archives
                archive = cx.borg.list(repo, prefix,
                                       short=True, last=index + 1)[0]
            except IndexError:
                raise click.Abort()
            cx.info(f"-- Selected archive '{archive}'")

        # TODO: interactive archive selection!

        cx.info(f"-- Mounting archive from repository '{repo.name}' "
                f"with prefix '{prefix}'...")
        try:
            # By default, borg daemonizes and exits on unmount. Since we want
            # to run post-scripts (e.g. umount), this is not sensible and we
            # set foreground to True.
            cx.borg.mount(repo, archive, mountpoint, foreground=True)
        except KeyboardInterrupt:
            while True:
                try:
                    cx.borg.umount(repo, mountpoint)
                    # TODO: Find out what the JSON log/output of borg mount
                    # are, log something appropriate here
                    break
                except (BorgError, RuntimeError) as e:
                    if 'failed to unmount' in str(e):
                        # Might fail if this happens to quickly after mounting.
                        time.sleep(2)
                    else:
                        raise

            cx.info('-- Done unmounting (the FUSE driver has exited).')
