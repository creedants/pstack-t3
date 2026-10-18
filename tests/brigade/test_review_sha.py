from pathlib import Path

from .support import BrigadeTest, setUpModule


class ReviewShaTest(BrigadeTest):
    def test_in_review_refuses_empty_sha_fails_when_empty_sha_reaches_review(self):
        self.started()
        before = self.store_files()
        out = self.brigade("dish", "D1", "--state", "in-review", ok=False)
        self.assertIn("D1 has no SHA", out)
        self.assertIn("--sha", out)
        self.assertIn("in-review", out)
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")

    def test_in_review_passes_stored_sha_fails_when_stored_sha_is_ignored(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.hand_edit("dishes.tsv", "D1", sha=sha)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review"), "D1 in-review")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-review")

    def test_in_review_refuses_status_failure_fails_when_failed_status_reaches_review(self):
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

    def test_in_review_refuses_git_failure_fails_when_missing_head_reaches_review(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        head = self.project / ".git" / "HEAD"
        saved = self.project / ".git" / "HEAD.saved"
        head.rename(saved)
        try:
            before = self.store_files()
            out = self.brigade("dish", "D1", "--state", "in-review", ok=False)
            self.assertIn("D1 cannot check coverage", out)
            self.assertIn("could not read landing status", out)
            self.assertIn("in-review", out)
            self.assertEqual(self.store_files(), before)
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")
            out = self.brigade("dish", "D1", "--state", "in-review", "--sha", sha, ok=False)
            self.assertIn("D1 cannot check coverage", out)
            self.assertIn("could not read landing status", out)
            self.assertIn("in-review", out)
            self.assertEqual(self.store_files(), before)
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")
        finally:
            saved.rename(head)

    def test_in_review_skips_no_contract_fails_when_clean_repo_has_no_contract(self):
        self.git_project()
        self.landing_env()
        self.open()
        (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s")
        self.hand_edit("dishes.tsv", "D1", lease="L1", paths="README.md")
        bad = "0" * 40
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", bad), "D1 in-review")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-review")
