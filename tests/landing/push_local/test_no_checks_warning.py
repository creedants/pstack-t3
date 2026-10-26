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

    def init_without_checks(self, mode):
        return self.land("init", "--trunk", "main", "--mode", mode, *self.EXTRA.get(mode, ()))

    def init_warns_once_and_succeeds(self, mode):
        out = self.init_without_checks(mode)
        self.assertEqual(sum(WARNING in line for line in out.splitlines()), 1, out)
        self.assertIn(HOW, out)
        self.assertIn(f"{mode} mode", out)
        self.assertEqual(self.contract()["checks"], [])
        self.assertNotIn(WARNING, self.land("init", "--trunk", "main", "--mode", mode, "--check", "./check.sh"))
        self.assertEqual(self.contract()["checks"], ["./check.sh"])

    def status_warns_only_while_no_check_is_set(self, mode):
        self.init_without_checks(mode)
        self.assertIn(WARNING, self.land("status"))
        self.land("init", "--trunk", "main", "--mode", mode, "--check", "./check.sh")
        self.assertNotIn(WARNING, self.land("status"))
        self.assertNotIn(WARNING, self.land("init", "--trunk", "main", "--mode", mode))

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
        self.assertNotIn(WARNING, self.land("init", "--trunk", "main", "--mode", "push"))

    def test_status_json_carries_no_warning(self):
        self.init_without_checks("push")
        self.assertNotIn(WARNING, self.land("status", "--json"))

    def test_merge_mode_prints_no_warning_with_no_checks(self):
        self.assertNotIn(WARNING, self.init_without_checks("merge"))
        self.assertNotIn(WARNING, self.land("status"))

    def test_human_mode_prints_no_warning_with_no_checks(self):
        self.assertNotIn(WARNING, self.init_without_checks("human"))
        self.assertNotIn(WARNING, self.land("status"))


if __name__ == "__main__":
    import unittest

    unittest.main()
