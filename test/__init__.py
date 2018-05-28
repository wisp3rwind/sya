import tempfile
import yaml


def make_config(tasks, repos, verbose=False):
    cfg = {'sya': dict(),
           'repositories': dict(),
           'tasks': dict(),
           }
    if verbose:
        cfg['sya']['verbose'] = True

    for t in tasks:
        raise NotImplementedError()

    for r in repos:
        raise NotImplementedError()

    fh, fn = tempfile.mkstemp(b'.yaml')
    fh.close()

    with open(fn, 'wb') as fh:
        fh.write(yaml.dump(cfg, encoding='utf-8'))
    
    return fn

    
