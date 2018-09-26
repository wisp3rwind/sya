import os
import pytest
import random
import string
import subprocess
import tempfile
import yaml

from borg_sya import Task, Repository, Context

def random_strings(number, length=6):
    """Return a number of unique random strings to be used as names for
    tasks and repositories.
    """
    res = set()
    while len(res) < number:
        res.add(''.join(
            random.choices(
                string.ascii_uppercase + string.digits,
                k=length))
            )
    return list(res)


@pytest.fixture
def make_config():
    def _make_config(
            ntasks=1, create_repo=True,
            verbose=False,
            ):
        cfg = {'sya': dict(),
               'repositories': dict(),
               'tasks': dict(),
               }
        if verbose:
            cfg['sya']['verbose'] = True

        with tempfile.TemporaryDirectory() as d:
            confdir = os.path.join(d, "config")
            rname = random_strings(1)[0]
            rname = f"repo-{rname}"
            rdir = os.path.join(d, rname)
            repo = Repository(
                    name=rname,
                    path=rdir,
                    cx=Context(confdir, False, verbose, None, None, None),
                    compression=None,
                    passphrase=None,
                    pre=None,
                    pre_desc=None,
                    post=None,
                    post_desc=None,
                    )
            cfg['repositories'][rname] = repo.to_yaml()
            if create_repo:
                os.mkdir(rdir)

            tnames = random_strings(ntasks)
            for name in tnames:
                name = f"task-{name}"
                task = Task(
                    name=name,
                    cx=None,
                    repo=repo,
                    enabled=True,
                    prefix=name,
                    keep={
                        hourly: 24,
                        },
                    includes=[],  # TODO: Include some data for backup tests in
                    # the tests module and copy that to tempdir, then list the
                    # directories here.
                    include_file=None,
                    exclude_file=None,
                    pre=None,
                    pre_desc='',
                    post=None,
                    post_desc='',
                )
                cfg['tasks'][name] = task.to_yaml()

            fh, fn = tempfile.mkstemp(b'.yaml')
            os.close(fh)

            with open(fn, 'wb') as fh:
                fh.write(yaml.dump(cfg, encoding='utf-8'))
            
            yield confdir, cfg

    return _make_config


@pytest.fixture
def _simple_cfg(make_config):
    yield from make_config(ntasks=1)


@pytest.fixture
def confdir(_simple_cfg):
    for c in _simple_cfg:
        yield c[0]


@pytest.fixture
def simple_cfg(_simple_cfg):
    for c in _simple_cfg:
        yield c[1]

@pytest.fixture(scope='package')
def important_data():
    with tempfile.TemporaryDirectory() as d:
        for i in range(3):
            fh, fn = tempfile.mkstemp(dir=d)
            os.close(fh)
            with open(fn, 'wb') as fh:
                fh.write(os.urandom(10000))
        yield d
