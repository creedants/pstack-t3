"""Thin facade over tests/release_tests/. See that package for the suites.

Discovery imports this file as test_release and collects the package itself, so the re-exports below load
only when unittest imports this file by its qualified name or runs it as a script.
"""

import os
import sys
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests.release_tests

if __name__ in ("tests.test_release", "__main__"):
    from tests.release_tests.test_kind import KindTest
    from tests.release_tests.test_order import OrderTest
    from tests.release_tests.test_refusal import RefusalTest
    from tests.release_tests.test_rule import RuleTest
    from tests.release_tests.test_run import RunTest

if __name__ == "__main__":
    unittest.main()
