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
        pass

    def test_check_ok(self):
        pass

    def test_check_fail(self):
        pass

    def test_mount(self):
        pass


class TestFullCLI():
    """ Actually runs borg, otherwise the same as the mocked class.
    """
    def test_create(self, make_config, runner):
        confdir, cfg = make_config()
        runner.invoke(cli.main, ['-d', confdir, 'create', cfg['tasks'][0]])

    def test_check_ok(self, make_config, runner):
        confdir, cfg = make_config()
        pass

    def test_check_fail(self, make_config, runner):
        confdir, cfg = make_config()
        pass

    def test_mount(self, make_config, runner):
        confdir, cfg = make_config()
        pass
