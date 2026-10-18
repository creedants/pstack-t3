from pathlib import Path

from .support import BrigadeTest, setUpModule


class ReviewShaTest(BrigadeTest):
    def test_in_review_refuses_empty_sha_fails_when_leased_dish_names_no_commit(self):
        self.started()
        before = self.store_files()
        out = self.brigade("dish", "D1", "--state", "in-review", ok=False)
        self.assertIn("D1 has no SHA", out)
        self.assertIn("--sha", out)
        self.assertIn("in-review", out)
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")

    def test_in_review_passes_stored_sha_fails_when_stored_commit_needs_a_flag(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.hand_edit("dishes.tsv", "D1", sha=sha)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review"), "D1 in-review")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-review")

    def test_in_review_refuses_status_failure_fails_when_status_read_fails(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        database = next((Path(self.temporary.name) / "state").glob("pstack-t3/landing/*/land.db"))
        database.write_bytes(b"not a database")
        before = self.store_files()
        out = self.brigade("dish", "D1", "--state", "in-review", "--sha", sha, ok=False)
        self.assertIn("D1 cannot check coverage", out)
        self.assertIn("could not read landing status", out)
        self.assertIn("in-review", out)
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")
