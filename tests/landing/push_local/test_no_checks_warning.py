if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import setUpModule, LandingTest as LandingTestBase
from ..merge.base import MergeBase

WARNING = "nothing gates a landing"
HOW = "land.py init again with --check"


class LandingTest(MergeBase, LandingTestBase):
    EXTRA = {"push": (), "local": ("--base", "main")}

    def setUp(self):
        super().setUp()
        forge = self.fake_gh()
        forge.__enter__()
        self.addCleanup(forge.__exit__, None, None, None)
        self.allow_methods(merge=True)

    def run_with_stderr(self, *args):
        """Run land.py, check it exits 0, and return (stdout, stderr). The warning goes to stderr so stdout keeps its shape."""
        code, out, err = self.run_land(self.work, *args)
        self.assertEqual(code, 0, out + err)
        return out, err

    def init_without_checks(self, mode):
        return self.run_with_stderr("init", "--trunk", "main", "--mode", mode, *self.EXTRA.get(mode, ()))

    def init_warns_once_and_succeeds(self, mode):
        out, err = self.init_without_checks(mode)
        self.assertEqual(sum(WARNING in line for line in err.splitlines()), 1, err)
        self.assertIn(HOW, err)
        self.assertIn(f"{mode} mode", err)
        self.assertNotIn(WARNING, out)
        self.assertEqual(self.contract()["checks"], [])
        self.assertEqual(self.run_with_stderr("init", "--trunk", "main", "--mode", mode, "--check", "./check.sh")[1], "")
        self.assertEqual(self.contract()["checks"], ["./check.sh"])

    def status_warns_only_while_no_check_is_set(self, mode):
        self.init_without_checks(mode)
        out, err = self.run_with_stderr("status")
        self.assertIn(WARNING, err)
        self.assertNotIn(WARNING, out)
        self.land("init", "--trunk", "main", "--mode", mode, "--check", "./check.sh")
        self.assertEqual(self.run_with_stderr("status")[1], "")
        self.assertEqual(self.run_with_stderr("init", "--trunk", "main", "--mode", mode)[1], "")

    def test_init_in_push_mode_with_no_checks_warns_once_and_succeeds(self):
        self.init_warns_once_and_succeeds("push")

    def test_init_in_local_mode_with_no_checks_warns_once_and_succeeds(self):
        self.init_warns_once_and_succeeds("local")

    def test_status_in_push_mode_warns_only_while_no_check_is_set(self):
        self.status_warns_only_while_no_check_is_set("push")

    def test_status_in_local_mode_warns_only_while_no_check_is_set(self):
        self.status_warns_only_while_no_check_is_set("local")

    def test_a_check_set_by_an_earlier_init_keeps_a_later_init_quiet(self):
        self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh")
        self.assertEqual(self.run_with_stderr("init", "--trunk", "main", "--mode", "push")[1], "")

    def test_status_json_carries_no_warning(self):
        self.init_without_checks("push")
        self.assertEqual(self.run_with_stderr("status", "--json")[1], "")

    def test_merge_mode_prints_no_warning_with_no_checks(self):
        self.assertEqual(self.init_without_checks("merge")[1], "")
        self.assertEqual(self.run_with_stderr("status")[1], "")

    def test_human_mode_prints_no_warning_with_no_checks(self):
        self.assertEqual(self.init_without_checks("human")[1], "")
        self.assertEqual(self.run_with_stderr("status")[1], "")


if __name__ == "__main__":
    import unittest

    unittest.main()
