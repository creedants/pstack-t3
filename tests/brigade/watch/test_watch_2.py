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
from ..support import BrigadeTest, CLAUDE, CODEX, HELD_LINE, Path, ROOT, SCRIPT, STILL_OPEN, call, _WakeRace, datetime, json, os, runpy, shlex, subprocess, sys, time, timedelta, timezone
from .fixtures import _Helpers

class WatchTest(BrigadeTest, _Helpers):
    def test_a_passed_item_whose_entry_landed_is_not_told_to_claim_again(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "fast\n"})
        self.passed(sha)
        self.submit(sha)
        self.land("land")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: E1 already submitted; mark it queued")
    def test_an_entry_another_run_landed_says_mark_it_merged(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "fast\n"})
        self.passed(sha)
        self.submit(sha)
        self.brigade("dish", "D1", "--state", "queued")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: E1 queued")
        self.land("land")
        landed = subprocess.run(["git", "rev-parse", "refs/landing/lane"], cwd=self.project, capture_output=True,
                                text=True, check=True).stdout.strip()
        self.assertEqual(self.brigade("watch"), f"workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: landed as E1 ({landed[:12]}); mark it merged")
    def test_an_old_bounce_at_a_sha_sharing_the_items_prefix_is_ignored(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "reviewed\n"})
        self.passed(sha)
        self.submit(sha)
        from contextlib import closing
        with closing(self.landing_db()) as db, db:
            db.execute("UPDATE entry SET sha = ?, state = 'bounced', note = 'old different SHA' WHERE id = 1",
                       (self.same_prefix(sha),))
            db.execute("UPDATE lease SET state = 'active' WHERE id = 1")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: passed, not submitted")
    def test_a_later_bounce_sharing_the_prefix_does_not_hide_the_queued_entry(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "reviewed\n"})
        self.passed(sha)
        self.assertEqual(self.submit(sha), "E1")
        self.brigade("dish", "D1", "--state", "queued")
        self.seed_entry(self.same_prefix(sha), "bounced", "other SHA")
        self.assertEqual(self.land("status", "--holder", "perf/D1").splitlines()[1],
                         f"E2 bounced (perf/D1, {sha[:12]}): other SHA")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: E1 queued")
    def test_watch_names_a_worker_that_ended_with_no_report_and_the_recorded_reason(self):
        self.open()
        self.fire_one(timebox="60")
        error = "rate limit from grok/grok-4.7"
        self.assertEqual(self.brigade("dish", "D1", "--worker-ended", error),
                         f"D1: worker ended with no report; the recorded reason is {error}")
        self.assertEqual(self.brigade("watch"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            f"D1: worker ended with no report; the recorded reason is {error}",
            "  keep the commits on perf/d1 and record no new branch; the dish keeps its lease",
            "  write the continuation brief: brief D1 with --base <trunk ref>, "
            f"--context {shlex.quote(error)}, and the goal, acceptance, verify, and contract it needs",
            "  launch a fresh thread in the same worktree and record it with dish D1 --thread <new thread id>",
            "  archive the dead thread thread-9",
        ]))
        self.assertEqual([(row["id"], row["state"], row["note"]) for row in self.log_rows() if row["kind"] == "ended"],
                         [("D1", "1", error)])
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual((row["thread"], row["branch"], row["reported"]), ("thread-9", "perf/d1", ""))
    def test_a_report_file_written_after_the_worker_end_keeps_the_report_line(self):
        self.open()
        self.fire_one(timebox="60")
        self.brigade("dish", "D1", "--worker-ended", "worker died")
        self.attempt_report("done")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n"
                                                "D1: report written, no report-back (thread thread-9)")
    def test_replacing_the_worker_hides_the_ended_line(self):
        self.open()
        self.fire_one(timebox="60")
        self.brigade("dish", "D1", "--worker-ended", "worker died")
        self.brigade("dish", "D1", "--thread", "thread-10")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n"
                                                "D1: running 0m of 60m (thread thread-10)")
        self.assertEqual([(row["state"], row["note"]) for row in self.log_rows() if row["kind"] == "ended"],
                         [("1", "worker died")])
    def test_an_ended_row_from_the_previous_attempt_does_not_flag_the_new_one(self):
        self.open()
        self.fire_one(timebox="60")
        self.brigade("dish", "D1", "--worker-ended", "worker died on attempt 1")
        self.brigade("dish", "D1", "--thread", "thread-10")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n"
                                                "D1: running 0m of 60m (thread thread-10)")
        self.brigade("dish", "D1", "--worker-ended", "worker died on attempt 2")
        self.assertIn("D1: worker ended with no report; the recorded reason is worker died on attempt 2",
                      self.brigade("watch"))
        self.assertEqual([(row["state"], row["note"]) for row in self.log_rows() if row["kind"] == "ended"],
                         [("1", "worker died on attempt 1"), ("2", "worker died on attempt 2")])
    def test_a_cleared_thread_cell_does_not_name_an_empty_dead_thread(self):
        self.open()
        self.fire_one(timebox="60")
        self.brigade("dish", "D1", "--worker-ended", "worker died")
        self.brigade("dish", "D1", "--thread", "")
        lines = self.brigade("watch").splitlines()
        self.assertEqual(lines[1], "D1: worker ended with no report; the recorded reason is worker died")
        self.assertEqual(lines[-1], "  archive the dead worker")
    def test_a_held_dish_does_not_print_the_ended_line(self):
        self.open()
        self.fire_one(timebox="60")
        self.brigade("dish", "D1", "--worker-ended", "worker died")
        self.park_item()
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
    def test_watch_lists_a_skipped_model_until_model_clear_records_it(self):
        self.open()
        self.fire_one(timebox="60")
        before = self.brigade("watch")
        seat = "grok/grok-4.7"
        self.assertEqual(self.brigade("model", "skip", seat, "--cause", "rate-limit"),
                         f"{seat}: recorded as skipped")
        workers, dish_line = before.splitlines()
        skipped = f"skipped {seat} (rate-limit); run model clear {shlex.quote(seat)} after a smoke test passes"
        self.assertEqual(self.brigade("watch"), "\n".join([workers, skipped, dish_line]))
        self.assertEqual(self.brigade("model", "clear", seat), f"{seat}: no longer skipped")
        self.assertEqual(self.brigade("watch"), before)
        self.assertEqual(self.brigade("model", "clear", seat), f"{seat}: already cleared")
        self.assertEqual([(row["state"], row["note"]) for row in self.log_rows() if row["kind"] == "model"],
                         [("skipped", "rate-limit"), ("cleared", "smoke test passed")])
    def test_a_skipped_model_does_not_replace_no_work_in_progress(self):
        self.open()
        self.brigade("model", "skip", "grok/grok-4.7", "--cause", "rate-limit")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n"
                                                "skipped grok/grok-4.7 (rate-limit); "
                                                "run model clear grok/grok-4.7 after a smoke test passes\n"
                                                "no work in progress")
    def test_a_second_skip_with_the_same_cause_writes_nothing_and_a_new_cause_replaces_the_line(self):
        self.open()
        seat = "grok/grok-4.7"
        self.brigade("model", "skip", seat, "--cause", "rate-limit")
        self.assertEqual(self.brigade("model", "skip", seat, "--cause", "rate-limit"), f"{seat}: already skipped")
        self.assertEqual([(row["state"], row["note"]) for row in self.log_rows() if row["kind"] == "model"],
                         [("skipped", "rate-limit")])
        self.assertEqual(self.brigade("model", "skip", seat, "--cause", "failed"),
                         f"{seat}: recorded as skipped")
        self.assertIn(f"skipped {seat} (failed); run model clear {shlex.quote(seat)} after a smoke test passes",
                      self.brigade("watch"))
        self.assertEqual(len([row for row in self.log_rows() if row["kind"] == "model"]), 2)
    def test_a_seat_with_a_namespaced_model_id_skips_watches_and_clears(self):
        self.open()
        seat = "opencode-go/opencode-go/step-5-preview-free"
        self.assertEqual(self.brigade("model", "skip", seat, "--cause", "rate-limit"),
                         f"{seat}: recorded as skipped")
        skipped = (f"skipped {seat} (rate-limit); "
                   f"run model clear {shlex.quote(seat)} after a smoke test passes")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n"
                                                f"{skipped}\n"
                                                "no work in progress")
        self.assertEqual(self.brigade("model", "clear", seat), f"{seat}: no longer skipped")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n"
                                                "no work in progress")
        self.assertEqual([(row["id"], row["state"], row["note"]) for row in self.log_rows() if row["kind"] == "model"],
                         [(seat, "skipped", "rate-limit"), (seat, "cleared", "smoke test passed")])
    def test_new_red_run_at_same_sha_refreshes_liveness_cues(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        for workflow in ("Build", "Tests"):
            payload = json.dumps([{"status": "completed", "conclusion": "failure", "headSha": "aaa111",
                                   "createdAt": "2026-10-01T00:00:00Z", "workflowName": workflow,
                                   "url": f"https://example.com/run/{workflow}"}])
            bindir = self._gh_dir(payload=payload)
            with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
                self.brigade("watch")
        sheet = self.brigade("next", "liveness")
        self.assertIn("Tests https://example.com/run/Tests", sheet)
        self.assertNotIn("Build https://example.com/run/Build", sheet)
    def test_trunk_red_reports_streak_start(self):
        self.started()
        self._remote_trunk()
        payload = ('[{"conclusion":"failure","status":"completed","headSha":"aaa111",'
                   '"createdAt":"2026-10-02T00:00:00Z","url":"https://example.com/run/2","workflowName":"CI"},'
                   '{"conclusion":"failure","status":"completed","headSha":"bbb222",'
                   '"createdAt":"2026-10-01T00:00:00Z","url":"https://example.com/run/1","workflowName":"CI"},'
                   '{"conclusion":"success","status":"completed","headSha":"ccc333",'
                   '"createdAt":"2026-09-30T00:00:00Z","url":"https://example.com/run/0","workflowName":"CI"}]')
        bindir = self._gh_dir(payload=payload)
        from unittest import mock
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertIn("trunk CI failing since 2026-10-01T00:00:00Z: CI https://example.com/run/2", out)
        reds = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(len(reds), 1)
        self.assertEqual((reds[0]["id"], reds[0]["state"]), ("main", "red"))
        data = json.loads(reds[0]["note"])
        self.assertEqual((data["sha"], data["workflow"], data["url"], data["since"]),
                         ("aaa111", "CI", "https://example.com/run/2", "2026-10-01T00:00:00Z"))
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            again = self.brigade("watch")
        self.assertIn("trunk CI failing since 2026-10-01T00:00:00Z: CI https://example.com/run/2", again)
        self.assertEqual(len([row for row in self.log_rows() if row["kind"] == "trunk"]), 1)
