import click
from click.testing import CliRunner
import pytest

import borg_sya.cli as cli


@pytest.fixture
def runner():
    return CliRunner()


class TestMockedCLI():
    """ Mocks calls to borg and checks that the correct command lines are
    being run.
    """
    def test_create(self):
        raise NotImplementedError()

    def test_check_ok(self):
        raise NotImplementedError()

    def test_check_fail(self):
        raise NotImplementedError()

    def test_mount(self):
        raise NotImplementedError()


class TestFullCLI():
    """ Actually runs borg, otherwise the same as the mocked class.
    """
    def test_create(self, simple_cfg, runner):
        runner.invoke(cli.main, ['-d', confdir, 'create', simple_cfg['tasks'][0]])

    def test_check_ok(self, simple_cfg, runner):
        raise NotImplementedError()

    def test_check_fail(self, simple_cfg, runner):
        raise NotImplementedError()

    def test_mount(self, simple_cfg, runner):
        raise NotImplementedError()
