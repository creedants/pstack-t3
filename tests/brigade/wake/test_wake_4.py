from ..support import setUpModule
from ..support import BrigadeTest, CLAUDE, CODEX, HELD_LINE, Path, ROOT, SCRIPT, STILL_OPEN, call, datetime, json, os, runpy, shlex, subprocess, sys, time, timedelta, timezone
from .fixtures import _Helpers

class WakeTest(BrigadeTest, _Helpers):
    def test_fire_doc_item_keeps_workers_kind_and_lease_checks(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
            self.seed_doc_settings()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "First")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Second")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "First")
            self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second",
                                          "--paths", "docs/guide.md", "--timebox", "120", "--doc-item", ok=False),
                             "brigade: nothing fired: 1 of 1 workers running")
            self.assertEqual(self.blocked_note(self.at, "T2"),
                             {"kind": "workers", "tickets": ["T2"], "station": "bug-fix", "summary": "Second",
                              "paths": "docs/guide.md", "timebox": 120, "doc_item": True})
            self.brigade("dish", "D1", "--state", "sent-back")
            unblocked = next(part for part in self.brigade("watch").splitlines() if "unblocked; run" in part)
            self.assertIn("--doc-item", unblocked)
            ran = self.bash_unblocked(self.at, unblocked)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            dish = ran.stdout.strip().split()[0]
            self.assertEqual(self.doc_log_note(self.at, dish), "doc-item: docs/guide.md")
    def test_next_points_at_startable_with_one_see_line(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix a")
        lines = self.brigade("next").splitlines()
        see = [line for line in lines if line.startswith("see ")]
        self.assertEqual(see, ["see Run startable and take the first ticket it prints as startable, "
                               "with every ticket it prints as rides with that ticket."])
    def test_a_second_worker_end_with_the_same_error_writes_nothing(self):
        self.open()
        self.fire_one(timebox="60")
        error = "rate limit from grok/grok-4.7"
        self.brigade("dish", "D1", "--worker-ended", error)
        before = json.loads((self.at / "restaurant.json").read_text())["lastActivityAt"]
        self.assertEqual(self.brigade("dish", "D1", "--worker-ended", error), "D1: worker end already recorded")
        self.assertEqual(len([row for row in self.log_rows() if row["kind"] == "ended"]), 1)
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["lastActivityAt"], before)
    def test_a_different_worker_end_on_the_same_attempt_is_refused(self):
        self.open()
        self.fire_one(timebox="60")
        self.brigade("dish", "D1", "--worker-ended", "rate limit from grok/grok-4.7")
        self.assertEqual(self.brigade("dish", "D1", "--worker-ended", "context window exhausted", ok=False),
                         "brigade: D1 already records a different worker end for this attempt")
        self.assertEqual([row["note"] for row in self.log_rows() if row["kind"] == "ended"],
                         ["rate limit from grok/grok-4.7"])
    def test_worker_ended_refuses_every_other_dish_flag(self):
        self.open()
        self.fire_one(timebox="60")
        others = (("--state", "sent-back"), ("--state", "dropped"), ("--thread", "thread-10"), ("--thread", ""),
                  ("--task", "t-2"), ("--branch", "perf/d2"), ("--pr", "https://x/1"), ("--sha", "abc"),
                  ("--timebox", "90"), ("--reported",), ("--lease", "L1"), ("--paths", "README.md"),
                  ("--stopped", "idle"), ("--mode", "full", "--reason", "why"))
        for extra in others:
            self.assertEqual(self.brigade("dish", "D1", "--worker-ended", "worker died", *extra, ok=False),
                             "brigade: --worker-ended takes no other dish flag", extra)
        self.assertEqual([row for row in self.log_rows() if row["kind"] == "ended"], [])
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "thread-9")
    def test_worker_ended_refuses_a_dish_that_is_not_in_progress_has_no_thread_or_already_has_a_report(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "4")
        for number in range(1, 5):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", f"Fix {number}")
            self.brigade("fire", "--tickets", f"T{number}", "--station", "bug-fix", "--summary", f"Fix {number}",
                         "--thread", "thread-9")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.brigade("dish", "D2", "--thread", "")
        self.brigade("dish", "D3", "--reported")
        self.attempt_report("done", dish="D4")
        refusals = (
            (("dish", "D1", "--worker-ended", "worker died"),
             "brigade: D1 is sent-back; --worker-ended records an in-progress dish"),
            (("dish", "D2", "--worker-ended", "worker died"), "brigade: D2 has no worker thread recorded"),
            (("dish", "D3", "--worker-ended", "worker died"), "brigade: D3 has a report-back on this attempt"),
            (("dish", "D4", "--worker-ended", "worker died"), "brigade: D4 has a report file for this attempt"),
        )
        for argv, refusal in refusals:
            self.assertEqual(self.brigade(*argv, ok=False), refusal, argv)
        self.assertEqual([row for row in self.log_rows() if row["kind"] == "ended"], [])
    def test_model_clear_refuses_a_seat_with_no_model_row_and_a_model_needs_provider_and_name(self):
        self.open()
        self.assertEqual(self.brigade("model", "clear", "grok/grok-4.7", ok=False),
                         "brigade: grok/grok-4.7 is not skipped")
        for seat in ("grok", "grok/", "/grok-4.7", "grok /grok-4.7"):
            self.assertEqual(self.brigade("model", "skip", seat, "--cause", "rate-limit", ok=False),
                             "brigade: a model is provider/model", seat)
        self.assertEqual([row for row in self.log_rows() if row["kind"] == "model"], [])
    def test_close_omits_the_worker_end_and_the_skipped_model(self):
        self.open()
        self.fire_one(timebox="60")
        self.brigade("dish", "D1", "--worker-ended", "rate limit from grok/grok-4.7")
        self.brigade("model", "skip", "grok/grok-4.7", "--cause", "rate-limit")
        closed = self.brigade("close", "--dry-run")
        self.assertIn("Cut startup", closed)
        self.assertNotIn("rate limit", closed)
        self.assertNotIn("grok/grok-4.7", closed)
    def test_next_and_startable_stay_unchanged_when_a_worker_has_ended_and_a_model_is_skipped(self):
        self.open()
        self.fire_one(timebox="60")
        before = (self.brigade("next", "liveness"), self.brigade("startable"))
        self.brigade("dish", "D1", "--worker-ended", "worker died")
        self.brigade("model", "skip", "grok/grok-4.7", "--cause", "rate-limit")
        self.assertEqual((self.brigade("next", "liveness"), self.brigade("startable")), before)
    def test_the_executive_admin_store_refuses_model_skip_and_clear(self):
        self.open_admin()
        refusal = "brigade: model works only in a coordinator's store"
        self.assertEqual(self.admin("model", "skip", "grok/grok-4.7", "--cause", "rate-limit", ok=False), refusal)
        self.assertEqual(self.admin("model", "clear", "grok/grok-4.7", ok=False), refusal)
        rows = [line.split("\t") for line in
                (self.store / "bridge-kit" / ".admin" / "log.tsv").read_text().splitlines()[1:] if line]
        self.assertEqual([row[1] for row in rows], [])
    def test_red_trunk_owes_fix_and_tell_at_all_levels(self):
        self.fired_bug_fix()
        note = json.dumps({"sha": "aaa111", "workflow": "CI",
                           "url": "https://example.com/run/2", "since": "2026-10-01T00:00:00+00:00"})
        self._seed_trunk("red", note)
        for level in ("every-turn", "milestones", "digest"):
            self.brigade("set", "--reporting", level)
            for wake in (("liveness",), ("merge", "D1")):
                sheet = self.next_sheet(*wake)
                self.assertIn("Read the failing jobs", sheet)
                self.assertIn("urgent fix", sheet)
                self.assertIn("Tell the owner", sheet)
                self.assertIn("trunk", sheet.lower())
                self.assertTrue(sheet.splitlines()[-1].startswith("reply: yes"))
    def test_green_trunk_owes_no_cue(self):
        self.fired_bug_fix()
        self._seed_trunk("green", "ddd444")
        for wake in (("liveness",), ("merge", "D1")):
            sheet = self.next_sheet(*wake)
            self.assertNotIn("failing jobs", sheet)
            self.assertNotIn("urgent fix", sheet)
            self.assertNotIn("red trunk", sheet)
