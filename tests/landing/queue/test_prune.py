from ..support import (
    setUpModule,
    LandingTest as LandingTestBase,
    land,
    sh,
    unittest,
)
from .base import QueueBase

SETTLED_LISTING = ["would delete landing/e1 (E1, bounced)", "would delete landing/q2 (E2, landed)"]


class LandingTest(QueueBase, LandingTestBase):

    def gh_calls(self):
        path = self.base / "gh-calls"
        return path.read_text().splitlines() if path.exists() else []

    def settled_and_in_flight_branches(self):
        with self.fake_gh():
            (self.base / "checks").write_text("failed")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            (self.base / "checks").write_text("passed")
            self.queue_one(path="b.txt", name="w2", holder="r/D2")
            self.land("land")
            (self.base / "checks").write_text("pending")
            self.queue_one(path="lib/x.py", name="w3", holder="r/D3")
            self.land("land")
        main = self.on_origin("rev-parse", "main")
        for branch in ("landing/e1", "landing/q2", "landing/e99", "feature/x"):
            self.on_origin("update-ref", f"refs/heads/{branch}", main)
        return self.base / "origin.git"

    def test_prune_dry_run_lists_the_settled_entries_branches_and_acts_on_none(self):
        with self.fake_gh():
            remote = self.settled_and_in_flight_branches()
            calls = len(self.gh_calls())
            self.assertEqual(self.land("prune", "--dry-run").splitlines(), SETTLED_LISTING)
            self.assertEqual(len([line for line in self.gh_calls()[calls:] if line.startswith("pr close ")]), 0)
        for branch in ("landing/e1", "landing/q2", "landing/e99", "landing/e3", "feature/x"):
            self.assertTrue(self.ref_exists(f"refs/heads/{branch}", remote), branch)

    def test_prune_deletes_the_settled_branches_and_a_second_run_does_nothing(self):
        with self.fake_gh():
            remote = self.settled_and_in_flight_branches()
            calls = len(self.gh_calls())
            self.assertEqual(self.land("prune").splitlines(), [
                "closed https://github.com/o/r/pull/9 (E1, bounced)",
                "deleted landing/e1 (E1, bounced)",
                "deleted landing/q2 (E2, landed)",
            ])
            self.assertEqual([line for line in self.gh_calls()[calls:] if line.startswith("pr close ")],
                             ["pr close https://github.com/o/r/pull/9 --comment queue prune: E1, bounced"])
            calls = len(self.gh_calls())
            self.assertEqual(self.land("prune"), "")
            self.assertEqual(len(self.gh_calls()), calls)
        for branch in ("landing/e1", "landing/q2"):
            self.assertFalse(self.ref_exists(f"refs/heads/{branch}", remote), branch)
        for branch in ("landing/e3", "landing/e99", "feature/x"):
            self.assertTrue(self.ref_exists(f"refs/heads/{branch}", remote), branch)


    def test_prune_leaves_non_canonical_names_alone(self):
        with self.fake_gh():
            remote = self.settled_and_in_flight_branches()
            main = self.on_origin("rev-parse", "main")
            near = ["landing/e01", "landing/q02", "landing/e\u0661", "landing/e1x", "landing/e9223372036854775808", "landing/e0"]
            for branch in near:
                self.on_origin("update-ref", f"refs/heads/{branch}", main)
            self.assertEqual(self.land("prune", "--dry-run").splitlines(), SETTLED_LISTING)
            for branch in near:
                self.assertTrue(self.ref_exists(f"refs/heads/{branch}", remote), branch)
            reported = self.land("prune")
            self.assertIn("deleted landing/e1 (E1, bounced)", reported)
            self.assertIn("deleted landing/q2 (E2, landed)", reported)
            for branch in near:
                self.assertNotIn(branch, reported)
                self.assertTrue(self.ref_exists(f"refs/heads/{branch}", remote), branch)
            for branch in ("landing/e1", "landing/q2"):
                self.assertFalse(self.ref_exists(f"refs/heads/{branch}", remote), branch)
            self.assertEqual(self.land("prune"), "")


if __name__ == "__main__":
    unittest.main()
