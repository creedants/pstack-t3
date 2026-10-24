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
    def test_replacing_an_in_progress_worker_starts_a_new_attempt(self):
        self.open()
        self.fire_one(timebox="60")
        log = self.at / "log.tsv"
        log.write_text(log.read_text().replace(f"{__import__('datetime').date.today().year}-", "2020-"))
        self.attempt_report("partial from the overdue worker")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written, no report-back (thread thread-9)")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", "fresh-worker"), "D1 in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (thread fresh-worker)")
        self.attempt_report("partial from the fresh worker")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written, no report-back (thread fresh-worker)")
        self.brigade("dish", "D1", "--timebox", "90", "--task", "t-2")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written, no report-back (thread fresh-worker)")
        self.brigade("dish", "D1", "--state", "in-progress", "--thread", "fresh-worker")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written, no report-back (thread fresh-worker)")
        self.brigade("dish", "D1", "--reported")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written 0m ago; review it even if the worker's run is still open (thread fresh-worker)")
        self.brigade("dish", "D1", "--thread", "worker-3")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 90m (thread worker-3)")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-9", ok=False),
                         "brigade: thread thread-9 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "worker-3")
        self.attempt_report("partial from worker-3")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written, no report-back (thread worker-3)")
    def test_hang_records_one_open_run_per_attempt(self):
        self.open()
        self.fire_one()
        error = self.brigade("hang", "D1", "--provider", "grok/grok-4.7", "--minutes", "20", ok=False)
        self.assertIn("no report-back on this attempt", error)
        self.assertEqual([row for row in self.log_rows() if row["kind"] == "hang"], [])
        report = self.at / "reports" / "D1.md"
        report.parent.mkdir()
        report.write_text("done")
        self.brigade("dish", "D1", "--reported")
        self.assertEqual(self.brigade("hang", "D1", "--provider", "grok/grok-4.7", "--minutes", "20"),
                         "D1: grok/grok-4.7 open 20m")
        hangs = [row for row in self.log_rows() if row["kind"] == "hang"]
        self.assertEqual([(row["kind"], row["id"], row["state"], row["note"]) for row in hangs],
                         [("hang", "D1", "open", "grok/grok-4.7 20m")])
        self.assertEqual(self.brigade("hang", "D1", "--provider", "grok/grok-4.7", "--minutes", "5"),
                         "D1: hang already recorded")
        self.assertEqual(len([row for row in self.log_rows() if row["kind"] == "hang"]), 1)
        closed = self.brigade("close", "--dry-run")
        self.assertIn("Cut startup", closed)
        self.assertNotIn("grok/grok-4.7 20m", closed)
        self.brigade("dish", "D1", "--thread", "worker-3")
        error = self.brigade("hang", "D1", "--provider", "grok/grok-4.7", "--minutes", "20", ok=False)
        self.assertIn("no report-back", error)
        report.write_text("done again")
        self.brigade("dish", "D1", "--reported")
        self.brigade("hang", "D1", "--provider", "grok/grok-4.7", "--minutes", "20")
        self.assertEqual(len([row for row in self.log_rows() if row["kind"] == "hang"]), 2)
    def test_hang_records_a_run_left_open_by_an_earlier_attempt(self):
        self.open()
        self.fire_one()
        report = self.at / "reports" / "D1.md"
        report.parent.mkdir()
        report.write_text("done")
        self.brigade("dish", "D1", "--reported")
        self.brigade("dish", "D1", "--thread", "worker-3")
        error = self.brigade("hang", "D1", "--provider", "grok", "--minutes", "13", ok=False)
        self.assertEqual(error, "brigade: no report-back on this attempt; "
                                "for a run left open by an earlier attempt, pass --attempt 1")
        self.assertEqual(self.brigade("hang", "D1", "--attempt", "1", "--provider", "grok", "--minutes", "13"),
                         "D1: grok open 13m (attempt 1)")
        self.assertEqual(self.brigade("hang", "D1", "--attempt", "1", "--provider", "grok", "--minutes", "14"),
                         "D1: hang already recorded")
        self.assertEqual(self.brigade("hang", "D1", "--attempt", "3", "--provider", "grok", "--minutes", "1", ok=False),
                         "brigade: D1 has attempts 1 to 2")
        report.write_text("done again")
        self.brigade("dish", "D1", "--reported")
        self.assertEqual(self.brigade("hang", "D1", "--provider", "grok", "--minutes", "2"), "D1: grok open 2m")
        self.assertEqual(self.brigade("hang", "D1", "--attempt", "2", "--provider", "grok", "--minutes", "2"),
                         "D1: hang already recorded")
        self.assertEqual([row["note"] for row in self.log_rows() if row["kind"] == "hang"],
                         ["grok 13m (attempt 1)", "grok 2m"])
    def test_hang_without_attempt_counts_toward_the_attempt_it_was_recorded_in(self):
        self.open()
        self.fire_one()
        report = self.at / "reports" / "D1.md"
        report.parent.mkdir()
        report.write_text("done")
        self.brigade("dish", "D1", "--reported")
        self.brigade("hang", "D1", "--provider", "grok", "--minutes", "20")
        self.brigade("dish", "D1", "--thread", "worker-3")
        self.assertEqual(self.brigade("hang", "D1", "--attempt", "1", "--provider", "grok", "--minutes", "20"),
                         "D1: hang already recorded")
    def test_a_branch_rename_during_a_drop_leaves_no_lease(self):
        from unittest import mock
        self.init_landing("--cap", "3")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            dropped = []
            result = self.renamed(lambda: dropped or dropped.append(self.brigade("dish", "D1", "--state", "dropped")))
            self.assertEqual(dropped, ["D1 dropped; T1 waiting again"])
            self.assertEqual(result, "D1 in-progress")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            refused = self.brigade("dish", "D1", "--lease", "L2", "--paths", "src", ok=False)
            self.assertEqual(refused, "brigade: D1 is dropped; it holds no lease")
            self.assertEqual(self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src"), "L2")
            self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")
    def test_an_unblocked_command_round_trips_through_bash(self):
        from unittest import mock
        marker = Path(self.temporary.name) / "shell-marker"
        summary = f"Fix O'Reilly; touch {marker}; # $(touch {marker}) & spaces"
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("set", "--change-note-dir", "changes")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "quoted")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src/app.py")
            refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", summary,
                                   "--branch", "docs/gate", "--paths", "src/app.py", "--timebox", "45", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by engine/D1", refused)
            self.land("lease", "release", "L1")
            line = self.brigade("watch")
            ran = self.bash_unblocked(self.at, line)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertFalse(marker.exists(), ran.stdout + ran.stderr)
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual(row["summary"], summary)
        self.assertEqual(row["paths"], "src/app.py,changes/docs%2Fgate.md")
        self.assertEqual(row["timebox"], "45")
        self.assertEqual(row["branch"], "docs/gate")
    def test_an_option_leading_summary_round_trips_through_bash(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                     "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "seed")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "seed")
        blocker = "D1"
        cases = (("-Werror", "T2", "D2"), ("--help", "T3", "D3"))
        for summary, ticket, dish in cases:
            self.brigade("ticket", "add", "--kind", "fix", f"--summary={summary}")
            refused = self.brigade("fire", "--tickets", ticket, "--station", "bug-fix",
                                   f"--summary={summary}", "--timebox", "45", ok=False)
            self.assertIn("nothing fired: 1 of 1 workers running", refused)
            self.brigade("dish", blocker, "--state", "sent-back")
            line = next(part for part in self.brigade("watch").splitlines() if part.startswith(f"{ticket}: unblocked"))
            ran = self.bash_unblocked(self.at, line)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            row = self.table_row(self.at, "dishes.tsv", dish)
            self.assertEqual(row["summary"], summary)
            self.assertEqual(row["timebox"], "45")
            self.assertEqual(row["station"], "bug-fix")
            blocker = dish
    def test_a_later_fire_does_not_keep_the_refused_attempts_fragment(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            directory = self.store / "bridge-kit" / "review-more"
            opened = self.brigade("open", "--project-root", str(self.project), "--name", "Review more")
            self.assertEqual(opened, f"opened {directory}")
            self.run_at(directory, "set", "--change-note-dir", "changes")
            self.run_at(directory, "ticket", "add", "--kind", "fix", "--summary", "Blocked on src")
            self.run_at(directory, "ticket", "add", "--kind", "fix", "--summary", "Takes the first id")
            self.land("lease", "claim", "--holder", "engine/D9", "--paths", "src")
            refused = self.run_at(directory, "fire", "--tickets", "T1", "--station", "bug-fix",
                                  "--summary", "Blocked on src", "--paths", "src", "--timebox", "30", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by engine/D9", refused)
            note = self.blocked_note(directory, "T1")
            self.assertEqual(note["paths"], "src")
            self.assertNotIn("branch", note)
            started = self.run_at(directory, "fire", "--tickets", "T2", "--station", "bug-fix",
                                  "--summary", "Takes the first id", "--paths", "other")
            self.assertEqual(started, "D1 (lease L2 held by review-more/D1)")
            self.assertEqual(self.table_row(directory, "dishes.tsv", "D1")["paths"], "other,changes/review-more%2Fd1.md")
            self.land("lease", "release", "L1")
            line = self.run_at(directory, "watch")
            self.assertNotIn("waiting on", line)
            self.assertIn("--paths=src --timebox=30", line)
            self.assertNotIn("changes/review-more%2Fd1.md", line)
            ran = self.bash_unblocked(directory, line)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        row = self.table_row(directory, "dishes.tsv", "D2")
        self.assertEqual(row["paths"], "src,changes/review-more%2Fd2.md")
        self.assertEqual(row["branch"], "")
        self.assertEqual(row["timebox"], "30")
        self.assertEqual(row["summary"], "Blocked on src")
    def test_a_refused_fire_keeps_an_explicit_branch(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            directory = self.store / "bridge-kit" / "review"
            opened = self.brigade("open", "--project-root", str(self.project), "--name", "Review")
            self.assertEqual(opened, f"opened {directory}")
            self.run_at(directory, "ticket", "add", "--kind", "fix", "--summary", "Explicit branch")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            refused = self.run_at(directory, "fire", "--tickets", "T1", "--station", "bug-fix",
                                  "--summary", "Explicit branch", "--branch", "docs/a%b/c",
                                  "--paths", "src,changes/docs%2Fa%25b%2Fc.md", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by engine/D1", refused)
            note = self.blocked_note(directory, "T1")
            self.assertEqual(note["paths"], "src,changes/docs%2Fa%25b%2Fc.md")
            self.assertEqual(note.get("branch"), "docs/a%b/c")
            self.land("lease", "release", "L1")
            line = self.run_at(directory, "watch")
            self.assertIn("--branch", line)
            self.assertIn("docs/a%b/c", line)
            ran = self.bash_unblocked(directory, line)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        row = self.table_row(directory, "dishes.tsv", "D1")
        self.assertEqual(row["branch"], "docs/a%b/c")
        self.assertEqual(row["paths"], "src,changes/docs%2Fa%25b%2Fc.md")
        self.assertEqual(row["summary"], "Explicit branch")
        self.assertNotIn("changes/review%2Fd1.md", row["paths"])
    def test_a_worker_refusal_prints_the_running_count(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "3")
        for number, summary in enumerate(("one", "two", "three"), start=1):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
            self.brigade("fire", "--tickets", f"T{number}", "--station", "bug-fix", "--summary", summary)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "four")
        self.brigade("set", "--workers", "1")
        refused = self.brigade("fire", "--tickets", "T4", "--station", "bug-fix", "--summary", "four", ok=False)
        self.assertIn("nothing fired: 3 of 1 workers running", refused)
        self.assertIn("T4: waiting for a worker (3 of 1 running)", self.brigade("watch"))
    def test_a_repository_cap_refusal_prints_the_repository_line(self):
        from unittest import mock
        self.init_landing("--cap", "1")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Room")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "other.txt")
            refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Room",
                                   "--paths", "src", ok=False)
            self.assertIn("nothing fired: repository at its cap: 1 of 1 changes in flight", refused)
            self.assertIn("blocked: repository", self.brigade("ticket", "list"))
            self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: waiting for room in the repository (1 of 1 changes in flight)")
    def test_re_admitting_an_expired_lease_names_the_holder_that_claimed_its_paths(self):
        self.started()
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.assertEqual(self.land("lease", "claim", "--holder", "engine/D1", "--paths", "README.md"), "L2")
        self.assertIn("D1: lease L1 expired; stop its worker, then run lease renew L1", self.brigade("watch"))
        self.assertEqual(self.land("lease", "renew", "L1", ok=False),
                         "land: L1 expired and L2 held by engine/D1 now covers README.md; claim again after it is released")
    def test_86_add_round_budget_writes_the_three_option_decision(self):
        self.started()
        self.sent_back(2)
        self.brigade("dish", "D1", "--state", "in-progress")
        self.review_file("D1-review-3.md")
        self.send_back("a3", "--report", "D1-review-3.md")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["state"], row["dish"], row["question"], row["options"], row["default"], row["kind"],
                          row["answered"]),
                         ("open", "D1", f"{self.ROUND_QUESTION} Findings: reports/D1-review-3.md.",
                          "known limits, redesign, drop, keep parked", "keep parked", "round-budget", ""))
        table = (self.at / "86.tsv").read_bytes()
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        self.assertEqual((self.at / "86.tsv").read_bytes(), table)
        self.assertEqual(self.brigade("watch").splitlines(), [
            "workers: 0 of 2 running, 2 idle, auto-start: normal",
            self.PENDING,
            f"D1: open decision Q1: {self.ROUND_QUESTION} Findings: reports/D1-review-3.md.; "
            "launch no worker or verifier until 86 answer Q1",
        ])
