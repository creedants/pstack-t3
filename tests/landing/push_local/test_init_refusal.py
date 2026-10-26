if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from pathlib import Path
from unittest import mock

from ..support import (
    setUpModule,
    LandingTest as LandingTestBase,
    sh,
    unittest,
)

REFUSAL = "local mode lands on refs/landing/main; pass --base <commit> to create it"


class LandingTest(LandingTestBase):

    def refuse_local_init(self):
        self.assertEqual(self.land("init", "--trunk", "main", "--mode", "local", "--check", "./check.sh", ok=False), f"land: {REFUSAL}")

    def stores(self):
        return list((self.base / "state").glob("pstack-t3/landing/*/land.db"))

    def test_a_refused_local_init_leaves_no_contract_for_status_to_report(self):
        self.refuse_local_init()
        self.assertEqual(self.stores(), [])
        self.assertIn("has no landing contract", self.land("status", ok=False))

    def test_a_push_init_succeeds_after_a_refused_local_init(self):
        self.refuse_local_init()
        self.assertIn("created", self.init(mode="push"))
        self.assertIn("push mode", self.land("status"))

    def test_a_local_init_with_base_succeeds_after_a_refused_local_init(self):
        self.refuse_local_init()
        self.assertIn("created", self.init(mode="local", base=sh("git", "rev-parse", "HEAD", cwd=self.work)))
        self.assertIn("local mode", self.land("status"))

    def test_a_refused_local_re_init_leaves_the_stored_contract_as_it_was(self):
        self.init(mode="local", base=sh("git", "rev-parse", "HEAD", cwd=self.work))
        sh("git", "update-ref", "-d", "refs/landing/main", cwd=self.work)
        before = self.queue_rows()
        with self.fake_gh():
            self.assertEqual(self.land("init", "--trunk", "main", "--mode", "local", "--check", "echo other", ok=False), f"land: {REFUSAL}")
        self.assertEqual(self.queue_rows(), before)

    def lock_lane(self):
        lock = Path(sh("git", "rev-parse", "--path-format=absolute", "--git-path", "refs/landing/main.lock", cwd=self.work))
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("")
        self.addCleanup(lambda: lock.unlink(missing_ok=True))
        return lock

    def test_a_first_local_init_whose_lane_ref_is_refused_leaves_no_contract(self):
        lock = self.lock_lane()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        refusal = self.land("init", "--trunk", "main", "--mode", "local", "--check", "./check.sh", "--base", base, ok=False)
        self.assertEqual(refusal.count("\n"), 0, refusal)
        self.assertIn(str(lock), refusal)
        self.assertIn("has no landing contract", self.land("status", ok=False))

    def refuse_first_lane_init(self):
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        lock = self.lock_lane()
        self.land("init", "--trunk", "main", "--mode", "local", "--check", "./check.sh", "--base", base, ok=False)
        return lock

    def test_status_after_a_refused_first_local_init_reports_no_contract_not_a_key_error(self):
        self.refuse_first_lane_init()
        status = self.land("status", ok=False)
        self.assertIn("has no landing contract", status)
        self.assertNotIn("KeyError", status)
        self.assertNotIn("commonDir", status)

    def test_a_push_init_succeeds_after_a_first_local_init_whose_lane_ref_is_refused(self):
        self.refuse_first_lane_init()
        self.assertIn("created", self.init(mode="push"))
        self.assertIn("push mode", self.land("status"))

    def test_a_local_init_with_base_succeeds_after_a_first_local_init_whose_lane_ref_is_refused(self):
        lock = self.refuse_first_lane_init()
        lock.unlink()
        self.assertIn("created", self.init(mode="local", base=sh("git", "rev-parse", "HEAD", cwd=self.work)))
        self.assertIn("local mode", self.land("status"))

    def test_a_second_init_that_commits_during_a_refused_first_init_keeps_its_contract(self):
        lock = self.lock_lane()
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        real_unlink = Path.unlink
        second = []

        def second_init():
            lock.unlink(missing_ok=True)
            second.append(self.init(mode="local", base=base))

        def unlink_after_a_second_init(path, *args, **kwargs):
            if path.name == "land.db" and not second:
                second_init()
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", unlink_after_a_second_init):
            refusal = self.land("init", "--trunk", "main", "--mode", "local", "--check", "./check.sh", "--base", base, ok=False)
        self.assertIn(str(lock), refusal)
        if not second:
            second_init()
        self.assertIn("created", second[0])
        self.assertIn("local mode", self.land("status"))

    def test_a_local_re_init_whose_lane_ref_is_refused_keeps_the_stored_contract(self):
        base = sh("git", "rev-parse", "HEAD", cwd=self.work)
        self.init(mode="local", base=base)
        sh("git", "update-ref", "-d", "refs/landing/main", cwd=self.work)
        lock = self.lock_lane()
        before = self.queue_rows()
        with self.fake_gh():
            refusal = self.land("init", "--trunk", "main", "--mode", "local", "--check", "echo other", "--base", base, ok=False)
        self.assertEqual(refusal.count("\n"), 0, refusal)
        self.assertIn(str(lock), refusal)
        self.assertEqual(self.queue_rows(), before)
        self.assertEqual(sh("git", "for-each-ref", "refs/landing", cwd=self.work), "")


if __name__ == "__main__":
    unittest.main()
