if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import io
import os
import shutil
import tempfile
import unittest
import unittest.case
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


DOC = "tests.brigade.test_docs.LivenessIdleDocTest.test_a_held_item_skips_liveness_steps_2_to_5"
AREA_A = "tests.brigade.test_entry_load.EntryLoadTest.test_two_loads_do_not_share_held_locks"
AREA_B = "tests.brigade.test_store.StoreTest.test_the_executive_admin_status_has_no_workers_line"
SKILL = "tests.test_skill_commands.SkillCommandTest.test_the_skill_keeps_at_least_seven_command_lines"
ORDER = (DOC, AREA_A, DOC, SKILL, AREA_B, AREA_A)
EXPECTED = (
    ("tests.brigade.support.LivenessIdleDocTest.test_a_held_item_skips_liveness_steps_2_to_5", True),
    ("tests.brigade.test_entry_load.EntryLoadTest.test_two_loads_do_not_share_held_locks", True),
    ("tests.brigade.support.LivenessIdleDocTest.test_a_held_item_skips_liveness_steps_2_to_5", True),
    ("tests.test_skill_commands.SkillCommandTest.test_the_skill_keeps_at_least_seven_command_lines", True),
    ("tests.brigade.test_store.StoreTest.test_the_executive_admin_status_has_no_workers_line", True),
    ("tests.brigade.test_entry_load.EntryLoadTest.test_two_loads_do_not_share_held_locks", True),
)


def load_case(name):
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    found = list(suite)
    while len(found) == 1 and isinstance(found[0], unittest.TestSuite):
        found = list(found[0])
    if len(found) != 1:
        raise AssertionError((name, found))
    return found[0]


def watch(case, sentinels, seen):
    name = case._testMethodName
    original = getattr(case, name)

    def wrapped():
        seen.append((case.id(), all(key not in os.environ for key in sentinels)))
        return original()

    setattr(case, name, wrapped)
    return case


class BrigadeFenceOrderTest(unittest.TestCase):
    def test_each_body_misses_the_sentinels_and_the_environment_returns_with_them(self):
        scratch = tempfile.mkdtemp(prefix="d202-fence-")
        entry = dict(os.environ)
        saved_cleanups = list(unittest.case._module_cleanups)
        try:
            sentinels = {
                "GIT_D202_ORDER_A": str(Path(scratch) / "missing-a"),
                "GIT_D202_ORDER_B": str(Path(scratch) / "missing-b"),
            }
            os.environ.update(sentinels)
            before = dict(os.environ)
            seen = []
            # This module already registered its fence cleanup. A nested run would pop that cleanup with its own.
            unittest.case._module_cleanups.clear()
            try:
                suite = unittest.TestSuite(watch(load_case(name), sentinels, seen) for name in ORDER)
                result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
                self.assertEqual(tuple(seen), EXPECTED)
                self.assertEqual((result.testsRun, result.failures, result.errors), (len(ORDER), [], []))
                self.assertEqual(dict(os.environ), before)
            finally:
                unittest.case._module_cleanups[:] = saved_cleanups
                os.environ.clear()
                os.environ.update(entry)
        finally:
            shutil.rmtree(scratch)
