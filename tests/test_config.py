import pytest

from borg_sya import Task, Repository


class TestYamlRoundtrip():
    """ Assert that from_yaml * to_yaml == id (the reverse is not true due to
    the handling of include files!).
    """
    def test_yaml_roundtrip_task(task):
        assert(task == Task.from_yaml(task.to_yaml))

    def test_yaml_roundtrip_repo(repo):
        assert(task == Repository.from_yaml(repo.to_yaml))

