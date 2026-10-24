if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import setUpModule
from ..support import BrigadeTest, CLAUDE, CODEX, HELD_LINE, Path, ROOT, SCRIPT, STILL_OPEN, call, datetime, json, os, runpy, shlex, subprocess, sys, time, timedelta, timezone
from .fixtures import _Helpers

class WakeTest(BrigadeTest, _Helpers):
    def test_next_readonly(self):
        self.queue()
        lock = self.at / "restaurant.lock"
        lock.unlink(missing_ok=True)
        before, landing = self.store_files(), self.landing_rows()
        self.assertEqual(self.brigade("next"), "\n".join([
            "wake: unspecified", "reporting: milestones", "nothing else owed", "reply: no (routine: unspecified)"]))
        self.assertFalse(lock.exists())
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.landing_rows(), landing)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.open_admin()
        self.admin("request", "--to", "perf", "from-user perf: please fix the gate")
        admin_dir = self.store / "bridge-kit" / ".admin"
        before, landing = self.store_files(), self.landing_rows()
        admin_before = self.directory_files(admin_dir)
        triggers = [
            ("next",),
            ("next", "report-back", "D1"),
            ("next", "review", "D1", "--verdict", "pass"),
            ("next", "review", "D1", "--verdict", "send-back"),
            ("next", "review", "D1", "--verdict", "blocked"),
            ("next", "review", "D1", "--verdict", "pass-with-edits"),
            ("next", "checks", "D1", "--result", "pass"),
            ("next", "checks", "D1", "--result", "fail"),
            ("next", "merge", "D1"),
            ("next", "liveness",),
            ("next", "intake",),
            ("next", "morning",),
            ("next", "evening",),
            ("next", "user",),
            ("next", "user", "--question"),
            ("next", "admin", "A1"),
            ("next", "--failure", "fixable"),
            ("next", "--failure", "stuck"),
            ("next", "--failure", "stuck", "merge", "D1"),
            ("next", "merge", "D1", "--failure", "stuck"),
            ("next", "--unsent", "failed"),
            ("next", "--unsent", "report", "liveness"),
            ("next", "liveness", "--unsent", "report"),
        ]
        for args in triggers:
            with self.subTest(args=args):
                self.brigade(*args)
                self.assertEqual(self.store_files(), before)
                self.assertEqual(self.landing_rows(), landing)
                self.assertEqual(self.directory_files(admin_dir), admin_before)
        for level in ("every-turn", "milestones", "digest"):
            self.brigade("set", "--reporting", level)
            before, landing = self.store_files(), self.landing_rows()
            self.brigade("next")
            self.assertEqual(self.store_files(), before)
            self.assertEqual(self.landing_rows(), landing)
        self.brigade("set", "--reports-to", "admin-thread")
        before, landing = self.store_files(), self.landing_rows()
        self.assertIn("reports to: admin-thread", self.brigade("next"))
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.landing_rows(), landing)
        for args, sentence in (
            (("next", "report-back", "D99"), "brigade: no D99 in dishes.tsv\n"),
            (("next", "review", "D99", "--verdict", "pass"), "brigade: no D99 in dishes.tsv\n"),
            (("next", "checks", "D99", "--result", "fail"), "brigade: no D99 in dishes.tsv\n"),
            (("next", "merge", "D99"), "brigade: no D99 in dishes.tsv\n"),
            (("next", "admin", "A9"), "brigade: no request A9 in the inbox\n"),
        ):
            with self.subTest(args=args):
                before = self.store_files()
                self.assertEqual(self.run_raw(*args), (1, "", sentence))
                self.assertEqual(self.store_files(), before)
        self.assertEqual(self.admin("next", ok=False), "brigade: the executive admin routes work and never runs it")
        self.assertEqual(self.directory_files(admin_dir), admin_before)
        self.store_from_before_kinds()
        self.assertFalse((self.at / "goals.tsv").exists())
        before, landing = self.store_files(), self.landing_rows()
        self.brigade("next")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.landing_rows(), landing)
        self.assertFalse((self.at / "goals.tsv").exists())
        self.brigade("set", "--thread", "t1")
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at), "next"])
        self.assertEqual((outcome.code, outcome.stdout_text(), outcome.stderr_text()),
                         (1, "", "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status\n"))
        self.assertEqual(self.brigade("--owner", "other@1", "next", ok=False),
                         "brigade: owner other@1 is stale; this store is owned by t1@1")
        meta_path = self.at / "restaurant.json"
        meta = json.loads(meta_path.read_text())
        meta["thread"] = ""
        meta_path.write_text(json.dumps(meta) + "\n")
        before, landing = self.store_files(), self.landing_rows()
        self.assertEqual(self.brigade("next", ok=False),
                         "brigade: this store has no recorded thread; it was retired, and only set --thread --expect restarts it")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.landing_rows(), landing)
        mod = runpy.run_path(str(SCRIPT))
        glob = mod["run"].__globals__
        glob["READ_ONLY"] = True
        try:
            restaurant = mod["Restaurant"](str(self.at))
            with self.assertRaises(mod["BrigadeError"]) as raised:
                restaurant.write("probe.txt", "nope\n")
            self.assertEqual(str(raised.exception), "brigade: next is read-only")
            self.assertFalse((self.at / "probe.txt").exists())
            with self.assertRaises(mod["BrigadeError"]) as raised:
                mod["land_result"](str(self.project), "status")
            self.assertEqual(str(raised.exception), "brigade: next is read-only")
        finally:
            glob["READ_ONLY"] = False
        meta["thread"] = "t1"
        meta_path.write_text(json.dumps(meta) + "\n")
        mod["Restaurant"](str(self.at), "t1@1").write("probe.txt", "ok\n")
        self.assertEqual((self.at / "probe.txt").read_text(), "ok\n")
    def test_next_wrong_id(self):
        self.fired_bug_fix()
        before = self.store_files()
        self.assertEqual(self.run_raw("next", "report-back", "D99"), (1, "", "brigade: no D99 in dishes.tsv\n"))
        self.assertEqual(self.run_raw("next", "admin", "A3"), (1, "", "brigade: no request A3 in the inbox\n"))
        self.assertEqual(self.store_files(), before)
    def test_next_warns_when_a_timebox_is_not_a_base_10_integer(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--reported")
        self.brigade("set", "--reporting", "digest")
        path = self.at / "dishes.tsv"
        lines = path.read_text().splitlines()
        header = lines[0].split("\t")
        fields = lines[1].split("\t")
        self.assertLess(header.index("timebox"), len(fields))
        self.assertIn("T", fields[header.index("at")])
        self.assertEqual(fields[header.index("reported")], "yes")
        fields[header.index("timebox")] = "sixty"
        lines[1] = "\t".join(fields)
        path.write_text("\n".join(lines) + "\n")
        code, out, err = self.run_raw("next")
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err + out)
        self.assertIn("brigade: warning: D1 timebox sixty is not a base-10 integer\n", err)
        sheet = out.splitlines()
        self.assertEqual([line for line in sheet if line.startswith("reply:")], [sheet[-1]])
        fields[header.index("timebox")] = ""
        lines[1] = "\t".join(fields)
        path.write_text("\n".join(lines) + "\n")
        code, out, err = self.run_raw("next")
        self.assertEqual(code, 0)
        self.assertNotIn("timebox", err)
        self.assertNotIn("Traceback", err + out)
    def test_next_warns_when_an_id_suffix_is_not_a_base_10_integer(self):
        self.fired_bug_fix()
        self.park_item()
        path = self.at / "86.tsv"
        text = path.read_text()
        self.assertIn("\nQ1\t", text)
        path.write_text(text.replace("\nQ1\t", "\nQx\t", 1))
        code, out, err = self.run_raw("next")
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err + out)
        self.assertEqual(err.count("brigade: warning: Qx has no base-10 id suffix"), 1)
        sheet = out.splitlines()
        self.assertEqual([line for line in sheet if line.startswith("reply:")], [sheet[-1]])
    def test_next_sees_a_malformed_edits_row_instead_of_close(self):
        self.fired_bug_fix()
        self.brigade("set", "--reporting", "digest")
        self.brigade("set", "--reports-to", "admin-thread")
        (self.at / "edits.tsv").write_text("at\tdish\tfrom\tto\treport\nnot-a-row\n")
        code, out, err = self.run_raw("next", "evening")
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err + out)
        self.assertNotIn("run close", out)
        self.assertIn("see bridge-kit/perf/edits.tsv line 2 is malformed; fix or remove it", out)
        self.assertIn("see t3_thread_send admin-thread mode queue: report perf: the path close --to-file prints", out)
        sheet = out.splitlines()
        self.assertTrue(sheet[-1].startswith("reply:"))
    def test_next_asks_for_a_report_the_row_says_was_written(self):
        self.fired_bug_fix()
        self.brigade("set", "--reporting", "digest")
        self.brigade("dish", "D1", "--thread", "thread-9")
        self.brigade("dish", "D1", "--reported")
        line = ("if reports/D1.md is absent or older than the last attempt start: "
                "read the worker thread (thread thread-9) and get the report")
        bare = self.next_sheet()
        self.assertIn(line, bare)
        self.assertEqual(bare.splitlines()[-1], "reply: no (routine: unspecified)")
        self.assertNotIn("running ", bare)
        morning = self.next_sheet("morning")
        self.assertIn(line, morning)
        named = self.next_sheet("report-back", "D1")
        self.assertIn(line, named)
        self.park_item("which way")
        held = self.next_sheet()
        self.assertNotIn("reports/D1.md is absent", held)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("dish", "D1", "--thread", "thread-9")
        self.brigade("dish", "D1", "--reported")
        current_path = self.attempt_report("done\n")
        current = self.next_sheet()
        self.assertIn("see D1: report written ", current)
        self.assertNotIn("reports/D1.md is absent", current)
        old = time.time() - 7200
        os.utime(current_path, (old, old))
        stale = self.next_sheet()
        self.assertIn(line, stale)
        self.assertNotIn("see D1: report written ", stale)
        self.assertNotIn("see D1: reported ", stale)
        self.assertNotIn("running ", stale)

        def stamp_attempt(at):
            path = self.at / "log.tsv"
            lines = path.read_text().splitlines()
            rewritten = [lines[0]]
            found = False
            for row in lines[1:]:
                fields = row.split("\t")
                if not found and fields[1:4] == ["dish", "D1", "in-progress"]:
                    fields[0] = at
                    row = "\t".join(fields)
                    found = True
                rewritten.append(row)
            self.assertTrue(found)
            path.write_text("\n".join(rewritten) + "\n")

        self.fresh()
        self.brigade("dish", "D1", "--reported")
        self.brigade("86", "add", "--dish", "D1", "--question", "Which way?", "--options", "a,b", "--default", "a")
        reports = self.at / "reports"
        reports.mkdir()
        (reports / "D1.md").write_text("held\n")
        stamp_attempt("2026-10-11T03:00:00")
        code, out, err = self.run_raw("next")
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("Traceback", out)
        self.assertEqual(out.splitlines(), [
            "wake: unspecified",
            "reporting: milestones",
            "see D1: open decision Q1: Which way?; launch no worker or verifier until 86 answer Q1",
            "run close",
            "reply: yes (decision: Q1)",
        ])
        self.fresh()
        self.brigade("dish", "D1", "--reported")
        self.attempt_report("current\n")
        stamp_attempt("2020-01-01T00:00:00")
        code, out, err = self.run_raw("next")
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err + out)
        self.assertTrue(any(line.startswith("see D1: report written ") for line in out.splitlines()))
        self.assertNotIn("reports/D1.md is absent", out)
    def test_next_malformed(self):
        self.fired_bug_fix()
        path = self.at / "dishes.tsv"
        rows = path.read_text().splitlines()
        rows[1] = "not-a-row"
        path.write_text("\n".join(rows) + "\n")
        code, out, err = self.run_raw("next")
        self.assertEqual((code, out, err),
                         (1, "", "brigade: bridge-kit/perf/dishes.tsv line 2 is malformed; fix or remove it\n"))
        self.assertNotIn("Traceback", err)
