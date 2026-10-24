if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import Path, ProcessReason, SUPPORT, _race_args, _wait_for_path, os, real_popen, sys


class RequestsMixin:
    def test_a_request_waits_in_the_inbox_until_done(self):
        self.open("docs")
        self.open_admin()
        self.brigade("docs", "set", "--thread", "th-docs")
        self.assertEqual(self.brigade("docs", "request", "--to", "docs", "x", ok=False),
                         "brigade: request works only in the executive admin's store")
        self.assertEqual(self.admin("request", "--to", "engine", "x", ok=False),
                         f"brigade: engine is not a sibling coordinator on {self.project}")
        line = "from-user docs: add a FAQ"
        self.assertEqual(self.admin("request", "--to", "docs", line), "A1 for docs; tell thread th-docs")
        self.assertEqual(self.inbox("docs"), ["A1.line"])
        # A failed send leaves the file. The coordinator's next wake prints it again.
        for _ in range(2):
            self.assertEqual(self.brigade("docs", "inbox", "take"), f"A1: {line}")
        self.assertIn("requests from the executive admin: 1", self.brigade("docs", "status"))
        self.assertEqual(self.brigade("docs", "inbox", "done", "A1"), "A1 done")
        self.assertEqual(self.brigade("docs", "inbox", "done", "A1"), "A1 done")
        self.assertEqual(self.brigade("docs", "inbox", "done", "A2", ok=False), "brigade: no request A2 in the inbox")
        self.assertEqual(self.inbox("docs"), [])
        self.assertEqual(self.brigade("docs", "inbox", "take"), "nothing handed to you")
        self.assertNotIn("requests from", self.brigade("docs", "status"))
        self.assertEqual([row[1:4] for row in self.rows("docs", "log.tsv") if row[1] == "inbox-done"], [["inbox-done", "A1", "done"]])
        self.assertEqual([row[1:] for row in self.rows(".admin", "log.tsv") if row[1] == "request"],
                         [["request", "A1", "sent", f"to docs: {line}"]])

    def test_status_walk_and_close_count_every_pending_request_as_from_the_executive_admin(self):
        self.open("docs")
        self.open_admin()
        self.admin("request", "--to", "docs", "reports-to docs th-admin")
        self.admin("request", "--to", "docs", "from-user docs: add a FAQ")
        for printed in (self.brigade("docs", "status"), self.brigade("docs", "walk", "--repo", str(self.project)),
                        self.brigade("docs", "close")):
            self.assertIn("requests from the executive admin: 2", printed)
            self.assertNotIn("requests from the user", printed)

    def test_a_request_killed_before_its_file_is_republished_once(self):
        self.open("docs")
        self.open_admin()
        died = self.child("die-before-publish", ".admin", "request", "--to", "docs", "from-user docs: add a FAQ")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.inbox("docs"), [])
        self.assertEqual(self.admin("request", "--republish"), "A1 republished for docs")
        self.assertEqual(self.admin("request", "--republish"), "nothing to republish")
        self.assertEqual(self.inbox("docs"), ["A1.line"])
        self.brigade("docs", "inbox", "done", "A1")
        self.assertEqual(self.admin("request", "--republish"), "nothing to republish")
        self.assertEqual(self.inbox("docs"), [])
        self.admin("sync")
        self.assertEqual(self.admin("request", "--republish"), "nothing to republish")
        self.assertEqual(self.inbox("docs"), [])

    def test_republish_before_sync_skips_a_request_the_coordinator_finished(self):
        self.open("core")
        self.open_admin()
        self.brigade("core", "set", "--thread", "core-thread")
        self.admin("set", "--thread", "admin-thread")
        self.assertEqual(self.admin("request", "--to", "core", "reports-to core admin-thread"),
                         "A1 for core; tell thread core-thread")
        self.assertEqual(self.brigade("core", "inbox", "take"), "A1: reports-to core admin-thread")
        self.assertEqual(self.brigade("core", "inbox", "done", "A1"), "A1 done")
        self.assertEqual(self.admin("request", "--republish"), "nothing to republish")
        self.assertEqual(self.inbox("core"), [])
        self.assertNotIn("requests from", self.brigade("core", "status"))

    def test_a_request_finished_while_republish_runs_stays_finished(self):
        self.open("core")
        self.open_admin()
        self.brigade("core", "set", "--thread", "core-thread")
        self.admin("request", "--to", "core", "reports-to core admin-thread")
        case = Path(self.temporary.name) / "republish"
        case.mkdir()
        support = SUPPORT
        proc = real_popen(
            ProcessReason.STOPPED,
            [sys.executable, str(support), *_race_args(
                "pause-after-finished", case,
                ["--store", str(self.store), "--at", str(self.dir(".admin")), "request", "--republish"])],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        _wait_for_path(case / "paused", timeout=20)
        self.assertEqual(self.brigade("core", "inbox", "done", "A1"), "A1 done")
        self.assertEqual(self.inbox("core"), [])
        (case / "proceed").touch()
        code, out, err = self.finish(proc)
        self.assertEqual(code, 0, err)
        self.assertNotIn("requests from", self.brigade("core", "status"))
        self.assertEqual(self.brigade("core", "inbox", "take"), "nothing handed to you")
        self.assertEqual(self.inbox("core"), [])
        self.assertEqual(self.admin("request", "--republish"), "nothing to republish")
        self.assertEqual(self.inbox("core"), [])

    def test_a_replayed_from_user_request_files_no_second_ticket(self):
        self.open("docs")
        self.assertEqual(self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "Add a FAQ", "--request", "A1"), "T1")
        refusal = "brigade: request A1 is already T1; nothing added"
        self.assertEqual(self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "Add a FAQ", "--request", "A1", ok=False), refusal)
        self.brigade("docs", "ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "Add a FAQ", "--request", "A1", ok=False), refusal)
        self.assertEqual(self.brigade("docs", "ticket", "list"), "T1 done normal fix [user (request A1)] Add a FAQ")
        self.assertEqual(self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "Other", "--request", "A2"), "T2")
