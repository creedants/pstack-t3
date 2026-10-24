if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import json
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest import mock

from .support import BrigadeTest, setUpModule

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "t3/added/brigade/scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "t3/added/brigade/scripts"))
from brigade_lib import scope as scope_mod
from brigade_lib.shared import BrigadeError


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
            self.assertIn("could not read repository identity", out)
            self.assertIn("in-review", out)
            self.assertEqual(self.store_files(), before)
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")
            out = self.brigade("dish", "D1", "--state", "in-review", "--sha", sha, ok=False)
            self.assertIn("D1 cannot check coverage", out)
            self.assertIn("could not read repository identity", out)
            self.assertIn("in-review", out)
            self.assertEqual(self.store_files(), before)
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")
        finally:
            saved.rename(head)

    def test_in_review_skips_no_contract_fails_when_clean_repo_is_refused(self):
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

    def test_in_review_refuses_moved_phrase_repo_fails_when_moved_repo_reaches_review(self):
        self.project = self.root / "fixture has no landing contract; run land.py init in it unrelated"
        self.project.mkdir()
        self.init_landing()
        self.landing_env()
        opened = self.open()
        self.at = Path(opened.splitlines()[0].split(" ", 1)[1].strip())
        (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "README.md"),
                         "D1 (lease L1 held by perf/D1)")
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n", "outside.py": "x\n"})
        saved = self.root / "saved-phrase-repo"
        self.project.rename(saved)
        try:
            before = self.store_files()
            out = self.brigade("dish", "D1", "--state", "in-review", ok=False)
            self.assertIn("D1 cannot check coverage", out)
            self.assertIn("could not read repository identity", out)
            self.assertIn("in-review", out)
            self.assertEqual(self.store_files(), before)
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")
            out = self.brigade("dish", "D1", "--state", "in-review", "--sha", sha, ok=False)
            self.assertIn("D1 cannot check coverage", out)
            self.assertIn("could not read repository identity", out)
            self.assertIn("in-review", out)
            self.assertEqual(self.store_files(), before)
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")
        finally:
            saved.rename(self.project)
        out = self.brigade("dish", "D1", "--state", "in-review", "--sha", sha, ok=False)
        self.assertIn("changes paths outside L1", out)
        self.assertIn("outside.py", out)
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")

    def test_in_review_refuses_phrase_common_dir_fails_when_mismatched_store_reaches_review(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n", "outside.py": "x\n"})
        with closing(self.landing_db()) as db:
            real = db.execute("SELECT value FROM contract WHERE key = 'commonDir'").fetchone()[0]
        fake = json.dumps("/unrelated has no landing contract; run land.py init in it suffix/.git")
        with closing(self.landing_db()) as db, db:
            db.execute("UPDATE contract SET value = ? WHERE key = 'commonDir'", (fake,))
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
            with closing(self.landing_db()) as db, db:
                db.execute("UPDATE contract SET value = ? WHERE key = 'commonDir'", (real,))
        out = self.brigade("dish", "D1", "--state", "in-review", "--sha", sha, ok=False)
        self.assertIn("changes paths outside L1", out)
        self.assertIn("outside.py", out)
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")

    def _read_status_refuses(self, stderr_text):
        fake = subprocess.CompletedProcess(
            args=["land.py", "--repo", str(self.project), "status"],
            returncode=1,
            stdout="",
            stderr=stderr_text,
        )
        before = self.store_files()
        with mock.patch.object(scope_mod, "land_result", return_value=fake):
            with self.assertRaises(BrigadeError) as ctx:
                scope_mod.read_status(str(self.project))
        self.assertIn("could not read landing status", str(ctx.exception))
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")

    def test_in_review_refuses_prefix_phrase_fails_when_prefixed_phrase_reaches_review(self):
        self.started()
        self._read_status_refuses("unrelated prefix has no landing contract; run land.py init in it\n")

    def test_in_review_refuses_suffix_phrase_fails_when_suffixed_phrase_reaches_review(self):
        self.started()
        self._read_status_refuses("has no landing contract; run land.py init in it unrelated suffix\n")

    def test_in_review_refuses_traceback_phrase_fails_when_traceback_phrase_reaches_review(self):
        self.started()
        self._read_status_refuses(
            "Traceback (most recent call last):\n"
            "  File \"land.py\", line 1, in <module>\n"
            "Exception: has no landing contract; run land.py init in it\n"
        )

    def test_in_review_refuses_wrapped_phrase_fails_when_wrapped_phrase_reaches_review(self):
        self.started()
        self._read_status_refuses(
            "land: wrapped error containing has no landing contract; run land.py init in it suffix\n"
        )
