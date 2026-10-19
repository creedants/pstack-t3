import os
import unittest
from tests import test_activity
from tests.activity_tests.browser import select_browser
Fixture = test_activity.Fixture
populate = test_activity.populate
CLEAN = test_activity.CLEAN
MOD = test_activity.MOD
def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
    saved = dict(os.environ)
    def restore():
        os.environ.clear()
        os.environ.update(saved)
    unittest.addModuleCleanup(restore)
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull
def setUpModule():
    fence_git_environment()
