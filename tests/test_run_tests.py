"""Runner tests facade. Package holds mixins and ids stay stable."""
if __package__ in (None, ""):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import isolation

isolation.install()

import os
import sys
import unittest
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests.runner_tests
from tests.runner_tests.support import (
    LINUX,
    NEEDS_LINUX,
    RunnerCase,
)
from tests.runner_tests.test_accounting import AccountMixin, ParseEventMixin
from tests.runner_tests.test_ci_gates import CiGatesMixin
from tests.runner_tests.test_ci_jobs import CiJobsMixin
from tests.runner_tests.test_ids_and_fixtures import IdsFixturesMixin
from tests.runner_tests.test_lifecycle import LifecycleMixin
from tests.runner_tests.test_parts import BuildShardsMixin, PartsMixin
from tests.runner_tests.test_result_files import ResultFilesMixin
from tests.runner_tests.test_results import ResultsMixin
from tests.runner_tests.test_sweeps import SweepMixin, SweepsMixin
from tests.runner_tests.test_workers import WorkersMixin


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


class RunTestsTest(
    ResultsMixin,
    WorkersMixin,
    IdsFixturesMixin,
    ResultFilesMixin,
    SweepsMixin,
    PartsMixin,
    CiJobsMixin,
    CiGatesMixin,
    RunnerCase,
    unittest.TestCase,
):
    pass


class BuildShardsTest(BuildShardsMixin, unittest.TestCase):
    pass


class LifecycleTest(LifecycleMixin, RunnerCase, unittest.TestCase):
    pass


class AccountTest(AccountMixin, unittest.TestCase):
    pass


class ParseEventTest(ParseEventMixin, unittest.TestCase):
    pass


@unittest.skipUnless(LINUX, NEEDS_LINUX)
class SweepTest(SweepMixin, unittest.TestCase):
    pass


if __name__ == "__main__":
    unittest.main()
