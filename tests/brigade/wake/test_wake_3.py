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
    def test_the_workers_line_prints_no_idle_worker_when_more_run_than_the_cap(self):
        self.open()
        for ticket in ("T1", "T2"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", ticket)
            self.brigade("fire", "--tickets", ticket, "--station", "bug-fix", "--summary", ticket)
        self.brigade("set", "--workers", "1")
        workers = "workers: 2 of 1 running, 0 idle, auto-start: normal"
        self.assertEqual(self.brigade("status").splitlines()[2], workers)
        self.assertEqual(self.brigade("watch").splitlines()[0], workers)
    def test_a_store_with_no_edits_table_runs_status_watch_startable_a_pass_and_close_and_gains_no_table(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        (self.at / "edits.tsv").unlink()
        self.assertEqual(self.brigade("status").splitlines()[0], "thread not recorded")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")
        self.assertEqual(self.brigade("startable"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nno waiting tickets")
        self.assertEqual(self.record_pass_at("abc"), "D1 passed")
        code, out, err = self.close_run()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[0], "# Perf report")
        self.assertFalse((self.at / "edits.tsv").exists())
    def test_fire_of_a_ticket_with_no_kind_is_refused_for_the_kind_before_the_worker_cap(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.rail_before_kinds("a", "b")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "feature", "--summary", "Do b", ok=False),
                         f"brigade: T2 has no kind and station feature gives none; pass --kind {self.KIND_CHOICE}")
        self.assertEqual([row for row in self.log_rows() if row["kind"] == "ticket" and row["id"] == "T2" and row["state"] == "blocked"], [])
        self.brigade("dish", "D1", "--state", "sent-back")
        line = "T2: unblocked; run fire --tickets=T2 --station=feature '--summary=Do b' --timebox=60"
        self.assertNotIn(line, self.brigade("watch").splitlines())
    def test_a_goal_mismatch_at_a_full_cap_is_refused_for_the_goal_and_records_no_block(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.goal("one")
        self.goal("two")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "polish", "--goal", "G2")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Do b")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T3", "--station", "feature", "--summary", "Do one", ok=False), "brigade: T3 is under G2 and the lead ticket T1 is under G1; name T3 in --ride-along, which takes a low ticket that is not a feature, or start it on its own")
        self.assertEqual([row for row in self.log_rows() if row["kind"] == "ticket" and row["state"] == "blocked"], [])
    def test_a_block_note_written_before_kinds_reprints_the_fire_line_it_printed_then(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "Fix the gate", "--kind", "fix")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        note = ('{"kind":"workers","tickets":["T2"],"station":"bug-fix","summary":"Fix the gate","paths":"","timebox":45,'
                '"branch":"docs/gate","reason":"wide change"}')
        with open(self.at / "log.tsv", "a") as log:
            log.write(f"2026-10-01T00:00:00+00:00\tticket\tT2\tblocked\t{note}\n")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.assertIn("T2: unblocked; run fire --tickets=T2 --station=bug-fix '--summary=Fix the gate' --branch=docs/gate "
                      "--timebox=45 --mode=full '--reason=wide change'", self.brigade("watch").splitlines())
    def test_a_blocked_fire_with_kind_and_ride_along_records_both_and_the_fire_line_watch_reprints_carries_them_and_runs(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.goal("activity view")
        self.goal("current with upstream")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        self.hand_edit("rail.tsv", "T2", kind="")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "polish", "--goal", "G2", "--priority", "low")
        self.assertEqual(self.brigade("fire", "--tickets", "T2,T3", "--ride-along", "T3", "--kind", "upkeep", "--station", "feature",
                                      "--summary", "Do b and c", ok=False), "brigade: nothing fired: 1 of 1 workers running")
        note = {"kind": "workers", "tickets": ["T2", "T3"], "station": "feature", "summary": "Do b and c", "paths": "", "timebox": 60,
                "work": "upkeep", "ride_along": ["T3"]}
        self.assertEqual((self.blocked_note(self.at, "T2"), self.blocked_note(self.at, "T3")), (note, note))
        self.brigade("dish", "D1", "--state", "sent-back")
        line = ("T2: unblocked; run fire --tickets=T2,T3 --station=feature '--summary=Do b and c' --timebox=60 --kind=upkeep "
                "--ride-along=T3")
        self.assertIn(line, self.brigade("watch").splitlines())
        ran = self.bash_unblocked(self.at, line)
        self.assertEqual((ran.returncode, ran.stdout.strip()), (0, "D2"), ran.stderr)
        self.assertEqual((self.label("dishes.tsv", "D2"), self.label("rail.tsv", "T2"), self.label("rail.tsv", "T3")),
                         (("upkeep", "G1"), ("upkeep", "G1"), ("polish", "G2")))
