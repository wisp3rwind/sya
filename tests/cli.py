import unittest
from test import make_config


class MockedCLITest(unittest.TestCase):
    """ Mocks calls to borg and checks that the correct command lines are
    being run.
    """
    def __init__(self):
        super().__init__()

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_create(self):
        pass

    def test_check_ok(self):
        pass

    def test_check_fail(self):
        pass

    def test_mount(self):
        pass


class FullCLITest(MockedCLITest):
    """ Actually runs borg, otherwise the same as the mocked class.
    """
    def test_create(self):
        pass

    def test_check_ok(self):
        pass

    def test_check_fail(self):
        pass

    def test_mount(self):
        pass
