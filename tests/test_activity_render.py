if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import isolation

isolation.install()

import os
import unittest
import tests.test_activity as test_activity
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
