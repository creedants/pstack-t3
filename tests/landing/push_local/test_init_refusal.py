if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

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


if __name__ == "__main__":
    unittest.main()
