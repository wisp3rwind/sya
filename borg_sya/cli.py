import click
import logging
import os
import sys
import time
import traceback

from . import InvalidConfigurationError, Context
from .borg import BorgError
from .gui import main as gui_main
from .util import LockInUse

DEFAULT_CONFDIR = '/etc/borg-sya'
DEFAULT_CONFFILE = 'config.yaml'
APP_NAME = 'borg-sya'


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
        print(e)
        raise click.Abort()
    if verbose:  # if True in the config file, do not set to False here
        cx.verbose = verbose
    cx.dryrun = dryrun
    ctx.obj = cx


@main.resultcallback()
@click.pass_context
def exit(cx, *args, **kwargs):
    logging.shutdown()


@main.command(help="Launch the GUI.")
@click.pass_obj
def gui(cx):
    gui_main(cx)


@main.command(help="Do a backup run. If no Task is specified, run all.")
@click.option('-p', '--progress/--no-progress',
              help="Show progress.")
@click.argument('tasks', nargs=-1)
@click.pass_obj
def create(cx, progress, tasks):
    # cx = cx.sub_context('CREATE') # TODO: implement
    for task in (tasks or cx.tasks):
        try:
            task = cx.tasks[task]
        except KeyError:
            cx.error(f'-- No such task: {task}, skipping...')
        else:
            cx.info(f'-- Backing up using {task} configuration...')
            with task(lazy=True):
                try:
                    task.create(progress)
                except BorgError as e:
                    cx.error(f"'{task}' backup failed. "
                             f"You should investigate.")
                except LockInUse:
                    cx.error(f"-- Another process seems to be accessing "
                             f"the repository {task.repo.name}. "
                             f"Could not create a new archive for task "
                             f"{task}.")
                except KeyboardInterrupt as e:
                    traceback.print_exc()
                    raise
                else:
                    task.prune()
                    cx.info(f'-- Done backing up {task}.')


@main.command(help="Perform a check for repository consistency. "
                   "Repositories can either be specified directly or "
                   "by task. If neither is provided, check all.")
@click.option('-p', '--progress/--no-progress',
              help="Show progress.")
@click.option('-r/-t', '--repo/--task', 'repo', default=False,
              help="Whether to directly name repositories to check or select "
                   "them from tasks.")
@click.argument('items', nargs=-1)
@click.pass_obj
def check(cx, progress, repo, items):
    if repo:
        repos = cx.validate_repos(items)
    else:
        _, repos = cx.validate_tasks(items)

    for repo in repos:
        cx.info(f'-- Checking repository {repo.name}...')
        try:
            repo.check()
        except BorgError as e:
            cx.error(f"-- Error {e} when checking repository {repo.name}."
                     f"You should investigate.")
        except LockInUse as e:
            cx.error(f"-- Another process seems to be accessing the "
                     f"repository {repo.name}. Could not check it.")
            continue
        else:
            cx.info(f'-- Done checking {repo.name}.')


@main.command(help="Mount a snapshot. Takes a repository or task and the "
                   "mountpoint as positional arguments. If a repository, "
                   "a prefix can "
                   "be speified as 'repo::prefix'. Optionally append an "
                   "arbitrary number of '^' to choose the last, next-to "
                   "last or earlier archives. Otherwise, all matching "
                   "archives will be mounted.")
# --repo name[^[^ ...]] -> repo
# --repo name::prefix^^ -> repo, prefix
# --task name[^[^ ...]] -> repo, prefix
# --before=2017-02-01T12:45:10
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
        try:
            repo = cx.repos[item]
        except KeyError:
            cx.error(f"No such repository: '{item}'")
            raise click.Abort()
    else:
        try:
            repo = cx.tasks[item].repo
            prefix = cx.tasks[item].prefix
        except KeyError:
            cx.error(f'No such task: {item}')
            raise click.Abort()

    if index and all:
        cx.error(f"Giving {'^' * index} and '--all' conflict.")
        raise click.Abort()

    if prefix and all:
        cx.error(f"Borg doen't support mounting only archives with "
                 f"a given prefix. Mounting only the last archive "
                 f"matching '{prefix}'.")
        all = False

    with repo(lazy=True):
        archive = None
        if not all:
            cx.info(f"-- Searching for last archive from "
                    f"repository '{repo.name}' with prefix '{prefix}'.")
            try:
                # short will only output archive names,
                # last return only the index+1 most recent archives
                archive = cx.borg.list(repo, prefix,
                                       short=True, last=index + 1)[0]
            except (IndexError, BorgError):
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
        except BorgError as e:
            cx.error(f"-- Mounting '{repo.name}' failed: \n"
                     f"{e}\n"
                     f"You should investigate.")
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
                        continue

            cx.info('-- Done unmounting (the FUSE driver has exited).')
