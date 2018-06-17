import os
import pytest
import tempfile
import yaml

from borg_sya import Task, Repository

def random_strings(number, length):
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
            ntasks=1, nrepos=1,
            verbose=False,
            create_repos=True, create_tasks=True,
            ):
        cfg = {'sya': dict(),
               'repositories': dict(),
               'tasks': dict(),
               }
        if verbose:
            cfg['sya']['verbose'] = True

        tnames = random_strings(ntasks)
        for name in tnames:
            task = Task(
                    name=name,
                    )
            cfg['tasks'][name] = task.to_yaml()
            if create_tasks:
                pass

        rnames = random_strings(ntasks)
        for name in rnames:
            task = Repository(
                    name=name,
                    )
            cfg['tasks'][name] = task.to_yaml()
            if create_repos:
                pass

        with tempfile.TemporaryDirectory() as confdir:
            fh, fn = tempfile.mkstemp(b'.yaml')
            os.close(fh)

            with open(fn, 'wb') as fh:
                fh.write(yaml.dump(cfg, encoding='utf-8'))
            
            yield confdir, cfg

    return _make_config
