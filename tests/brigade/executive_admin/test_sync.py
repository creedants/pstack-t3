if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import Path, ProcessReason, SUPPORT, _race_args, _wait_for_path, os, real_popen, sys, time


class SyncMixin:
    def test_sync_relays_a_coordinators_goal_log_row_and_the_admins_close_prints_it_in_that_coordinators_block(self):
        self.open("docs")
        self.open_admin()
        self.brigade("docs", "goal", "add", "--name", "activity view", "--purpose", "p", "--done", "d")
        self.assertRegex(self.admin("sync"), r"^docs \S+ goal G1 open: activity view$")
        self.assertEqual([(row["kind"], row["id"], row["state"], row["note"]) for _, row in self.relays()],
                         [("goal", "G1", "open", "activity view")])
        report = self.admin("close", "--dry-run").splitlines()
        block = report[report.index("### docs") + 2]
        self.assertRegex(block, r"^- \S+ goal G1 open: activity view$")

    def test_sync_and_close_hide_a_doc_row_but_keep_its_relay(self):
        self.open("docs")
        self.open_admin()
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "Work")
        self.brigade("docs", "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Work")
        self.assertNotEqual(self.admin("sync"), "nothing new")
        before_close = self.admin("close", "--dry-run")
        self.assertEqual(self.admin("sync"), "nothing new")
        log = self.dir("docs") / "log.tsv"
        log.write_text(log.read_text() + "2026-10-12T00:00:00+00:00\tdoc\tD1\titem\tdoc-item\n")
        self.assertEqual(self.admin("sync"), "nothing new")
        self.assertEqual(self.admin("close", "--dry-run"), before_close)
        self.assertIn(("doc", "D1", "item", "doc-item"),
                      [(row["kind"], row["id"], row["state"], row["note"]) for _, row in self.relays()])
        self.assertNotIn(" doc ", self.admin("close", "--dry-run"))

    def test_sync_relays_each_row_once_and_a_kill_before_the_cursor_copies_nothing_twice(self):
        self.open("docs")
        self.open_admin()
        start = self.size("docs")
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "one")
        sync = self.admin("sync")
        self.assertRegex(sync, r"^docs \S+ ticket T1 waiting: one$")
        self.assertEqual([(ident, row["kind"], row["id"], row["state"], row["note"]) for ident, row in self.relays()],
                         [(f"app/docs@{start}", "ticket", "T1", "waiting", "one")])
        self.assertEqual(self.cursor("docs"), self.size("docs"))
        self.assertEqual(self.admin("sync"), "nothing new")
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "two")
        before = self.cursor("docs")
        died = self.child("die-before-cursor", ".admin", "sync")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.cursor("docs"), before)
        self.assertEqual(len(self.relays()), 2)
        self.assertEqual(self.admin("sync"), "nothing new")
        self.assertEqual([row["note"] for _, row in self.relays()], ["one", "two"])
        self.assertEqual(self.cursor("docs"), self.size("docs"))

    def test_a_row_appended_while_sync_runs_is_relayed_once_even_with_an_older_timestamp(self):
        self.open("docs")
        self.open_admin()
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "one")
        case, proc = self.admin_child("pause", "sync", "sync")
        _wait_for_path(case / "paused", timeout=20)
        older = "2020-01-01T00:00:00Z"
        appended = self.append_row("docs", older, "ticket", "T9", "waiting", "late")
        self.assertEqual(self.finish(appended)[0], 0)
        (case / "proceed").touch()
        code, out, err = self.finish(proc)
        self.assertEqual(code, 0, err)
        self.assertEqual([row["note"] for _, row in self.relays()], ["one"])
        self.assertRegex(self.admin("sync"), rf"^docs {older} ticket T9 waiting: late$")
        self.assertEqual(self.admin("sync"), "nothing new")
        self.assertEqual([row["note"] for _, row in self.relays()], ["one", "late"])

    def test_an_older_sync_finishing_late_never_moves_the_cursor_back(self):
        self.open("docs")
        self.open_admin()
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "one")
        case = Path(self.temporary.name) / "outer"
        case.mkdir()
        support = SUPPORT
        outer = real_popen(
            ProcessReason.STOPPED,
            [sys.executable, str(support), *_race_args(
                "pause", case, ["--store", str(self.store), "--at", str(self.dir(".admin")), "sync"])],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        self.addCleanup(lambda: outer.poll() is None and outer.kill())
        _wait_for_path(case / "paused", timeout=20)
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "two")
        self.admin("sync")
        consumed = self.size("docs")
        self.assertEqual(self.cursor("docs"), consumed)
        (case / "proceed").touch()
        code, out, err = self.finish(outer)
        self.assertEqual((code, out), (0, "nothing new"), err)
        self.assertEqual(self.cursor("docs"), consumed)
        self.assertEqual([row["note"] for _, row in self.relays()], ["one", "two"])

    def test_sync_leaves_an_unfinished_tail_and_relays_the_repaired_row_once(self):
        self.open("engine")
        self.open("core")
        self.open_admin()
        for number in range(1, 7):
            self.brigade("engine", "ticket", "add", "--kind", "fix", "--summary", f"engine {number}")
        for number in range(1, 6):
            self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", f"core {number}")
        self.brigade("engine", "ticket", "move", "T6", "--to", "core")
        self.admin("sync")
        died = self.child("die-inside-log-append", "core", "ticket", "take")
        self.assertEqual(died.returncode, 9, died.stderr)
        tail = self.cursor("core")
        self.assertRegex((self.dir("core") / "log.tsv").read_bytes()[tail:].decode(), r"^[^\n]+\tticket\tT6\twaiting\tfro$")
        relayed = len(self.relays())
        self.assertEqual(self.admin("sync"), "nothing new")
        self.assertEqual((len(self.relays()), self.cursor("core")), (relayed, tail))
        self.brigade("core", "ticket", "take")
        self.admin("sync")
        new = self.relays()[relayed:]
        self.assertEqual([(ident, row["kind"], row["id"], row["state"], row["note"]) for ident, row in new],
                         [(f"app/core@{tail}", "ticket", "T6", "waiting", "from app/engine/T6")])

    def test_a_joined_line_stops_sync_at_that_line(self):
        self.open("docs")
        self.open_admin()
        self.admin("sync")
        log = self.dir("docs") / "log.tsv"
        good = f"{self.STAMP}\tticket\tT1\twaiting\tgood\n"
        joined = f"{self.STAMP}\tticket\tT6\twaiting\tfro{self.STAMP}\tticket\tT7\twaiting\tnote\n"
        at = self.size("docs") + len(good)
        log.write_text(log.read_text() + good + joined + f"{self.STAMP}\tticket\tT8\twaiting\tafter\n")
        self.assertEqual(self.admin("sync", ok=False), f"brigade: app/docs/log.tsv at byte {at} is malformed; nothing past it relayed")
        self.assertEqual(self.cursor("docs"), at)
        self.assertEqual([row["note"] for _, row in self.relays()], ["good"])
        self.assertEqual(self.admin("sync", ok=False), f"brigade: app/docs/log.tsv at byte {at} is malformed; nothing past it relayed")
        self.assertEqual([row["note"] for _, row in self.relays()], ["good"])

    def test_a_reader_racing_a_tail_repair_relays_only_the_repaired_row(self):
        self.open("core")
        self.open_admin()
        self.admin("sync")
        log = self.dir("core") / "log.tsv"
        tail = self.size("core")
        self.assertEqual(self.cursor("core"), tail)
        log.write_bytes(log.read_bytes() + f"{self.STAMP}\tticket\tT6\twaiting\tfro".encode())
        case, sync = self.admin_child("read16", "reader", "sync")
        _wait_for_path(case / "paused", timeout=20)
        support = SUPPORT
        append = real_popen(
            ProcessReason.OTHER_LOCK,
            [sys.executable, str(support), *_race_args(
                "append-row", self.temporary.name,
                [str(self.dir("core")), "2026-10-06T00:00:01Z", "ticket", "T7", "waiting", "from app/engine/T7"])],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        self.addCleanup(lambda: append.poll() is None and append.kill())
        time.sleep(1)
        self.assertIsNone(append.poll(), "the append waits for the reader's lock")
        (case / "proceed").touch()
        code, out, err = self.finish(sync)
        self.assertEqual((code, out), (0, "nothing new"), err)
        self.assertEqual(self.cursor("core"), tail)
        self.assertEqual(self.finish(append)[0], 0)
        self.assertEqual(self.relays(), [])
        self.admin("sync")
        self.assertEqual(self.relays(), [(f"app/core@{tail}", {"at": "2026-10-06T00:00:01Z", "kind": "ticket", "id": "T7",
                                                                 "state": "waiting", "note": "from app/engine/T7"})])
        self.assertEqual(log.read_bytes()[tail:], b"2026-10-06T00:00:01Z\tticket\tT7\twaiting\tfrom app/engine/T7\n")
