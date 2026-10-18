from .support import (
    setUpModule,
    BrigadeTest,
    CLAUDE,
    CODEX,
    HELD_LINE,
    Path,
    ROOT,
    SCRIPT,
    STILL_OPEN,
    call,
    _WakeRace,
    datetime,
    json,
    os,
    runpy,
    shlex,
    subprocess,
    sys,
    time,
    timedelta,
    timezone,
)


class WatchTest(BrigadeTest, _WakeRace):
    def test_watch_reports_running_finished_and_overdue_work(self):
        self.open()
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")
        self.fire_one(timebox="30")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 30m (thread thread-9)")
        log = self.at / "log.tsv"
        log.write_text(log.read_text().replace(f"{__import__('datetime').date.today().year}-", "2020-"))
        self.assertIn("D1: over its 30m timebox", self.brigade("watch"))
        self.attempt_report("done")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written, no report-back (thread thread-9)")
        self.assertEqual(self.brigade("dish", "D1", "--reported"), "D1 in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written 0m ago; review it even if the worker's run is still open (thread thread-9)")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in progress with no worker thread; launch a fresh worker")
        self.brigade("dish", "D1", "--thread", "thread-10")
        self.attempt_report("second attempt")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written, no report-back (thread thread-10)")

    def test_an_idle_replacement_with_an_earlier_report_reaches_the_nudge(self):
        self.open()
        self.fire_one(timebox="60")
        log = self.at / "log.tsv"
        log.write_text(log.read_text().replace(f"{__import__('datetime').date.today().year}-", "2020-"))
        report = self.at / "reports" / "D1.md"
        report.parent.mkdir()
        report.write_text("report from the replaced worker")
        self.brigade("dish", "D1", "--state", "in-progress", "--thread", "fresh-worker")
        line = self.brigade("watch").splitlines()[1]
        text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        section = text.split("## Liveness check", 1)[1].split("\n## ", 1)[0]
        step = next(row for row in section.splitlines() if row.startswith("5. "))
        trigger = step.split("`", 2)[1].split("Nm")[0].replace("D<n>", "D1")
        self.assertEqual((report.exists(), line), (True, "D1: running 0m of 60m (thread fresh-worker)"))
        self.assertTrue(line.startswith(trigger), (trigger, line))
        self.assertIn("no report for the current attempt", step)
        self.assertNotIn("no report file exists", step)

    def test_watch_flags_a_reported_run_still_open_past_ten_minutes(self):
        self.open()
        self.fire_one()
        report = self.attempt_report("done")
        self.brigade("dish", "D1", "--reported")
        self.assertEqual(self.brigade("watch"),
                         "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written 0m ago; review it even if the worker's run is still open (thread thread-9)")
        start = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat(timespec="microseconds")
        log = self.at / "log.tsv"
        lines = log.read_text().splitlines()
        rewritten = [lines[0]]
        for line in lines[1:]:
            fields = line.split("\t")
            if fields[1:4] == ["dish", "D1", "in-progress"]:
                fields[0] = start
                line = "\t".join(fields)
            rewritten.append(line)
        log.write_text("\n".join(rewritten) + "\n")
        moment = datetime.now(timezone.utc) - timedelta(minutes=12, seconds=30)
        os.utime(report, (moment.timestamp(), moment.timestamp()))
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: reported 12m ago, not in review; read the thread (thread thread-9)")
        moment = datetime.now(timezone.utc) - timedelta(minutes=10, seconds=10)
        os.utime(report, (moment.timestamp(), moment.timestamp()))
        self.assertEqual(self.brigade("watch"),
                         "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written 10m ago; review it even if the worker's run is still open (thread thread-9)")

    def test_watch_renews_a_live_lease_and_records_activity(self):
        self.started()
        self.land("lease", "renew", "L1", "--ttl-hours", "0.01")
        before = json.loads((self.at / "restaurant.json").read_text())["lastActivityAt"]
        self.assertLess(self.lease_row(1)["expires"], (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (no worker recorded)")
        self.assertGreater(self.lease_row(1)["expires"], (datetime.now(timezone.utc) + timedelta(hours=5.9)).isoformat())
        self.assertGreater(json.loads((self.at / "restaurant.json").read_text())["lastActivityAt"], before)

    def test_an_expired_lease_stays_expired_until_an_explicit_renew(self):
        self.started()
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        expired = self.lease_row(1)["expires"]
        self.assertEqual(self.brigade("watch").splitlines(),
                         ["workers: 1 of 2 running, 1 idle, auto-start: normal",
                          "D1: running 0m of 60m (no worker recorded)",
                          "D1: lease L1 expired; stop its worker, then run lease renew L1"])
        self.assertEqual(self.lease_row(1)["expires"], expired)
        self.assertEqual(self.land("lease", "renew", "L1"), "L1 renewed")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (no worker recorded)")

    def test_a_released_or_unreadable_lease_gets_its_own_line(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.brigade("dish", "D1", "--state", "in-review", "--sha", sha)
        self.land("lease", "release", "L1")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: lease L1 was released; claim again before submitting")
        before = (self.at / "restaurant.json").read_text()
        (Path(self.temporary.name) / "state").rename(Path(self.temporary.name) / "moved")
        self.assertEqual(self.brigade("watch"),
                         f"workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: could not renew L1: {self.project.resolve()} has no landing contract; run land.py init in it")
        self.assertEqual((self.at / "restaurant.json").read_text(), before)

    def test_review_passed_parked_and_sent_back_items_each_get_a_watch_line_and_renew(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        verdict = ("pass", "record", "D1", "--sha", "abc1234", "--author", CODEX, "--verifier", CLAUDE, "--verdict")
        steps = [(("dish", "D1", "--state", "in-review", "--sha", sha), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review"),
                 ((*verdict, "send-back"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: sent back"),
                 ((*verdict, "blocked"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: parked"),
                 ((*verdict, "pass"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: passed, not submitted")]
        for command, line in steps:
            self.brigade(*command)
            self.land("lease", "renew", "L1", "--ttl-hours", "0.01")
            self.assertEqual(self.brigade("watch"), line)
            self.assertGreater(self.lease_row(1)["expires"],
                               (datetime.now(timezone.utc) + timedelta(hours=5.9)).isoformat(), line)

    def test_watch_names_the_pending_decision_in_review_and_parked(self):
        self.started()
        self.sent_back(3)
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", sha), "D1 in-review")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "a3", "--verdict", "blocked", "--author", CODEX,
                                      "--verifier", CLAUDE), "D1 blocked")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)

    def test_an_open_item_decision_replaces_an_over_timebox_line_and_renews_the_lease(self):
        self.started()
        self.brigade("dish", "D1", "--timebox", "30")
        self.backdate_attempt(45)
        self.land("lease", "renew", "L1", "--ttl-hours", "0.01")
        self.assertLess(self.lease_row(1)["expires"], (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        self.assertEqual(self.park_item(), "Q1")
        before = json.loads((self.at / "restaurant.json").read_text())["lastActivityAt"]
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
        self.assertGreater(self.lease_row(1)["expires"],
                           (datetime.now(timezone.utc) + timedelta(hours=5.9)).isoformat())
        self.assertGreater(json.loads((self.at / "restaurant.json").read_text())["lastActivityAt"], before)

    def test_an_open_item_decision_replaces_the_no_worker_line(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)

    def test_an_open_item_decision_replaces_the_in_review_line(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.brigade("dish", "D1", "--state", "in-review", "--sha", sha)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)

    def test_an_expired_lease_under_a_held_item_follows_the_decision_line(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.assertEqual(self.brigade("watch").splitlines(), [
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            HELD_LINE,
            "D1: lease L1 expired; stop its worker, then run lease renew L1",
        ])

    def test_answering_an_item_decision_restores_the_over_timebox_line(self):
        self.started()
        self.brigade("dish", "D1", "--timebox", "30")
        self.backdate_attempt(45)
        self.park_item()
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "moved"), "Q1 answered")
        self.assertRegex(
            self.brigade("watch"),
            r"^workers: 1 of 2 running, 1 idle, auto-start: normal\n"
            r"D1: over its 30m timebox at \d+m with no report; read its thread and decide \(no worker recorded\)$",
        )

    def test_answering_an_item_decision_restores_the_no_worker_line(self):
        self.started()
        self.park_item()
        self.brigade("dish", "D1", "--state", "sent-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "moved"), "Q1 answered")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in progress with no worker thread; launch a fresh worker")

    def test_answering_an_item_decision_restores_the_in_review_line(self):
        self.started()
        self.park_item()
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.brigade("dish", "D1", "--state", "in-review", "--sha", sha)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "moved"), "Q1 answered")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")

    def test_a_passed_item_with_a_submitted_entry_says_mark_it_queued(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "fast\n"})
        self.passed(sha)
        self.assertEqual(self.submit(sha), "E1")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: E1 already submitted; mark it queued")

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

    def _remote_trunk(self):
        from contextlib import closing
        with closing(self.landing_db()) as db, db:
            db.execute("UPDATE contract SET value = ? WHERE key = 'mode'", (json.dumps("merge"),))
            db.execute("UPDATE contract SET value = ? WHERE key = 'trunk'", (json.dumps("main"),))
            db.execute("UPDATE contract SET value = ? WHERE key = 'remote'", (json.dumps("origin"),))
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/repo.git"],
                       cwd=self.project, capture_output=True)
        subprocess.run(["git", "remote", "set-url", "origin", "https://github.com/example/repo.git"],
                       cwd=self.project, capture_output=True)

    def _gh_dir(self, payload=None, fail=False, with_gh=True):
        from pathlib import Path as _Path
        import shutil as _shutil
        import stat as _stat
        bindir = _Path(self.temporary.name) / f"fakebin-{time.time_ns()}"
        bindir.mkdir()
        git = _shutil.which("git")
        if git:
            (bindir / "git").symlink_to(git)
        if with_gh:
            script = bindir / "gh"
            if fail:
                script.write_text("#!/bin/sh\necho fake gh failed >&2\nexit 1\n")
            else:
                script.write_text("#!/bin/sh\ncat <<'EOF'\n" + (payload or "[]") + "\nEOF\n")
            script.chmod(0o755)
        return bindir

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

    def test_trunk_green_adds_no_trunk_line(self):
        self.started()
        self._remote_trunk()
        payload = ('[{"conclusion":"success","status":"completed","headSha":"ddd444",'
                   '"createdAt":"2026-10-02T00:00:00Z","url":"https://example.com/run/3","workflowName":"CI"}]')
        bindir = self._gh_dir(payload=payload)
        from unittest import mock
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            before = self.brigade("watch")
        self.assertNotIn("trunk CI", before)
        greens = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(len(greens), 1)
        self.assertEqual((greens[0]["id"], greens[0]["state"]), ("main", "green"))

    def test_trunk_unseen_reports_cannot_see_via_missing_and_failing_gh(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        bindir = self._gh_dir(with_gh=False)
        with mock.patch.dict(os.environ, {"PATH": str(bindir)}):
            missing = self.brigade("watch")
        self.assertIn("cannot see trunk CI:", missing)
        unseen = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(len(unseen), 1)
        self.assertEqual(unseen[0]["state"], "unseen")
        bindir_fail = self._gh_dir(fail=True)
        with mock.patch.dict(os.environ, {"PATH": str(bindir_fail) + os.pathsep + os.environ.get("PATH", "")}):
            failing = self.brigade("watch")
        self.assertIn("cannot see trunk CI:", failing)

    def _gh_dir_recording(self, payload, args_file):
        from pathlib import Path as _Path
        import shutil as _shutil
        bindir = _Path(self.temporary.name) / f"fakebin-{time.time_ns()}"
        bindir.mkdir()
        git = _shutil.which("git")
        if git:
            (bindir / "git").symlink_to(git)
        script = bindir / "gh"
        payload_path = bindir / "payload.json"
        text = payload if isinstance(payload, str) else json.dumps(payload)
        payload_path.write_text(text)
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, json\n"
            "args = sys.argv[1:]\n"
            f"open({str(args_file)!r}, 'w').write(' '.join(args))\n"
            "limit = 100\n"
            "if '--limit' in args:\n"
            "    try:\n"
            "        limit = int(args[args.index('--limit') + 1])\n"
            "    except Exception:\n"
            "        pass\n"
            f"rows = json.loads(open({str(payload_path)!r}).read())\n"
            "print(json.dumps(rows[:limit]))\n"
        )
        script.chmod(0o755)
        return bindir

    def _valid_red_payload(self, sha="aaa111", workflow="CI", url="https://example.com/run/2",
                            since="2026-10-02T00:00:00Z"):
        return json.dumps([{"status": "completed", "conclusion": "failure", "headSha": sha,
                            "createdAt": since, "url": url, "workflowName": workflow}])

    def _assert_no_red_cues(self):
        liveness = self.brigade("next", "liveness")
        self.assertNotIn("Read the failing jobs", liveness)
        self.assertNotIn("urgent fix", liveness)
        self.assertNotIn("red-trunk", liveness)
        merge = self.brigade("next", "merge", "D1")
        self.assertNotIn("Read the failing jobs", merge)
        self.assertNotIn("urgent fix", merge)
        self.assertNotIn("red-trunk", merge)

    def test_malformed_non_dict_rows_clear_stored_red(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        bindir = self._gh_dir(payload=self._valid_red_payload())
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertIn("trunk CI failing since", out)
        for bad in ("[null]", "[42]", '["bad"]'):
            bindir = self._gh_dir(payload=bad)
            with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
                seen = self.brigade("watch")
            self.assertIn("cannot see trunk CI:", seen)
            self.assertEqual(seen.count("cannot see trunk CI:"), 1)
            rows = [row for row in self.log_rows() if row["kind"] == "trunk"]
            self.assertEqual(rows[-1]["state"], "unseen")
            self._assert_no_red_cues()
            bindir = self._gh_dir(payload=self._valid_red_payload())
            with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
                self.brigade("watch")

    def test_malformed_missing_fields_clear_stored_red(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        bindir = self._gh_dir(payload=self._valid_red_payload())
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            self.brigade("watch")
        base = {"status": "completed", "conclusion": "failure", "headSha": "aaa111",
                "createdAt": "2026-10-02T00:00:00Z", "url": "https://example.com/run/2",
                "workflowName": "CI"}
        for key in ("headSha", "createdAt", "url", "workflowName"):
            bad = dict(base)
            del bad[key]
            bindir = self._gh_dir(payload=json.dumps([bad]))
            with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
                seen = self.brigade("watch")
            self.assertIn("cannot see trunk CI:", seen)
            self.assertEqual(seen.count("cannot see trunk CI:"), 1)
            rows = [row for row in self.log_rows() if row["kind"] == "trunk"]
            self.assertEqual(rows[-1]["state"], "unseen")
            self._assert_no_red_cues()
            bindir = self._gh_dir(payload=self._valid_red_payload())
            with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
                self.brigade("watch")

    def test_in_progress_failure_never_reports_red(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        payload = json.dumps([{"status": "in_progress", "conclusion": "failure", "headSha": "aaa111",
                               "createdAt": "2026-10-02T00:00:00Z", "url": "https://example.com/run/2",
                               "workflowName": "CI"}])
        bindir = self._gh_dir(payload=payload)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            seen = self.brigade("watch")
        self.assertIn("cannot see trunk CI:", seen)
        self.assertNotIn("trunk CI failing since", seen)
        rows = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(rows[-1]["state"], "unseen")

    def test_non_default_remote_selects_explicit_repo(self):
        self.started()
        from contextlib import closing
        from pathlib import Path as _Path
        with closing(self.landing_db()) as db, db:
            db.execute("UPDATE contract SET value = ? WHERE key = 'mode'", (json.dumps("merge"),))
            db.execute("UPDATE contract SET value = ? WHERE key = 'trunk'", (json.dumps("release/stable"),))
            db.execute("UPDATE contract SET value = ? WHERE key = 'remote'", (json.dumps("upstream"),))
        subprocess.run(["git", "remote", "add", "upstream", "https://github.com/example/other.git"],
                       cwd=self.project, capture_output=True)
        subprocess.run(["git", "remote", "set-url", "upstream", "https://github.com/example/other.git"],
                       cwd=self.project, capture_output=True)
        payload = json.dumps([{"status": "completed", "conclusion": "failure", "headSha": "aaa111",
                               "createdAt": "2026-10-01T00:00:00Z", "url": "https://example.com/run/2",
                               "workflowName": "CI"},
                              {"status": "completed", "conclusion": "success", "headSha": "ccc333",
                               "createdAt": "2026-09-30T00:00:00Z", "url": "https://example.com/run/0",
                               "workflowName": "CI"}])
        args_file = _Path(self.temporary.name) / "gh-args.txt"
        bindir = self._gh_dir_recording(payload, args_file)
        from unittest import mock
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertIn("trunk CI failing since 2026-10-01T00:00:00Z: CI https://example.com/run/2", out)
        recorded = args_file.read_text()
        self.assertIn("--repo", recorded)
        self.assertIn("example/other", recorded)
        self.assertIn("--branch", recorded)
        self.assertIn("release/stable", recorded)
        reds = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual((reds[-1]["id"], reds[-1]["state"]), ("release/stable", "red"))

    def test_unsupported_host_prints_cannot_see(self):
        self.started()
        self._remote_trunk()
        subprocess.run(["git", "remote", "set-url", "origin", "https://example.com/repo.git"],
                       cwd=self.project, capture_output=True)
        payload = self._valid_red_payload()
        bindir = self._gh_dir(payload=payload)
        from unittest import mock
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            seen = self.brigade("watch")
        self.assertIn("cannot see trunk CI:", seen)
        rows = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(rows[-1]["state"], "unseen")

    def test_missing_remote_prints_cannot_see(self):
        self.started()
        self._remote_trunk()
        subprocess.run(["git", "remote", "remove", "origin"], cwd=self.project, capture_output=True)
        payload = self._valid_red_payload()
        bindir = self._gh_dir(payload=payload)
        from unittest import mock
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            seen = self.brigade("watch")
        self.assertIn("cannot see trunk CI:", seen)
        rows = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(rows[-1]["state"], "unseen")

    def test_long_failure_streak_reports_streak_start(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        from pathlib import Path as _Path
        rows = []
        for day in range(10, 0, -1):
            rows.append({"status": "completed", "conclusion": "failure", "headSha": f"aaa{day:03d}",
                         "createdAt": f"2026-10-{day:02d}T00:00:00Z",
                         "url": f"https://example.com/run/{day}", "workflowName": "CI"})
        rows.append({"status": "completed", "conclusion": "success", "headSha": "ccc999",
                     "createdAt": "2026-09-30T00:00:00Z", "url": "https://example.com/run/0",
                     "workflowName": "CI"})
        args_file = _Path(self.temporary.name) / "gh-args-long.txt"
        bindir = self._gh_dir_recording(json.dumps(rows), args_file)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertIn("trunk CI failing since 2026-10-01T00:00:00Z: CI https://example.com/run/10", out)
        reds = [row for row in self.log_rows() if row["kind"] == "trunk"]
        data = json.loads(reds[-1]["note"])
        self.assertEqual(data["since"], "2026-10-01T00:00:00Z")

    def test_in_progress_window_finds_newest_completed(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        from pathlib import Path as _Path
        rows = []
        for number in range(5):
            rows.append({"status": "in_progress", "conclusion": "", "headSha": f"zzz{number:03d}",
                         "createdAt": f"2026-10-0{6+number}T00:00:00Z",
                         "url": f"https://example.com/run/w{number}", "workflowName": "CI"})
        rows.append({"status": "completed", "conclusion": "failure", "headSha": "aaa111",
                     "createdAt": "2026-10-01T00:00:00Z", "url": "https://example.com/run/old",
                     "workflowName": "CI"})
        args_file = _Path(self.temporary.name) / "gh-args-window.txt"
        bindir = self._gh_dir_recording(json.dumps(rows), args_file)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertIn("trunk CI failing since 2026-10-01T00:00:00Z: CI https://example.com/run/old", out)
        reds = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(reds[-1]["state"], "red")

    def test_truncated_failure_streak_reports_before_oldest_fetched(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        from pathlib import Path as _Path
        head = "1bd3ecf6510a180e34f1391d0910a869aae30f8a"
        base = datetime(2026, 10, 17, tzinfo=timezone.utc)
        rows = []
        for i in range(101):
            stamp = (base - timedelta(hours=i)).isoformat()
            rows.append({"status": "completed", "conclusion": "failure", "headSha": head,
                         "createdAt": stamp, "url": f"https://example.com/run/{i}", "workflowName": "CI"})
        rows.append({"status": "completed", "conclusion": "success", "headSha": "ccc999",
                     "createdAt": (base - timedelta(hours=101)).isoformat(),
                     "url": "https://example.com/run/101", "workflowName": "CI"})
        args_file = _Path(self.temporary.name) / "gh-args-cutoff-streak.txt"
        bindir = self._gh_dir_recording(json.dumps(rows), args_file)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        oldest = (base - timedelta(hours=99)).isoformat()
        self.assertIn(f"trunk CI failing since before {oldest}: CI https://example.com/run/0", out)
        reds = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(reds[-1]["state"], "red")
        data = json.loads(reds[-1]["note"])
        self.assertEqual(data["since"], f"before {oldest}")
        self.assertEqual(data["sha"], head)
        sheet = self.brigade("next", "liveness")
        self.assertIn("Read the failing jobs", sheet)
        self.assertTrue(sheet.splitlines()[-1].startswith("reply: yes"))

    def test_hidden_completed_failure_reports_no_completed_run_in_window(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        from pathlib import Path as _Path
        head = "1bd3ecf6510a180e34f1391d0910a869aae30f8a"
        base = datetime(2026, 10, 17, tzinfo=timezone.utc)
        rows = []
        for i in range(100):
            stamp = (base - timedelta(hours=i)).isoformat()
            rows.append({"status": "in_progress", "conclusion": "", "headSha": f"zzz{i:03d}",
                         "createdAt": stamp, "url": f"https://example.com/run/w{i}", "workflowName": "CI"})
        rows.append({"status": "completed", "conclusion": "failure", "headSha": head,
                     "createdAt": (base - timedelta(hours=100)).isoformat(),
                     "url": "https://example.com/run/100", "workflowName": "CI"})
        args_file = _Path(self.temporary.name) / "gh-args-cutoff-hidden.txt"
        bindir = self._gh_dir_recording(json.dumps(rows), args_file)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertIn("cannot see trunk CI: no completed run in the last 100 runs", out)
        unseen = [row for row in self.log_rows() if row["kind"] == "trunk"]
        self.assertEqual(unseen[-1]["state"], "unseen")
        self.assertIn("no completed run in the last 100 runs", unseen[-1]["note"])
        self._assert_no_red_cues()

    def test_stored_red_owes_next_liveness_and_merge(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        bindir = self._gh_dir(payload=self._valid_red_payload())
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertIn("trunk CI failing since", out)
        for wake in (("liveness",), ("merge", "D1")):
            sheet = self.brigade("next", *wake)
            self.assertIn("Read the failing jobs", sheet)
            self.assertIn("urgent fix", sheet)
            self.assertIn("Tell the owner", sheet)
            self.assertTrue(sheet.splitlines()[-1].startswith("reply: yes"))

    def test_stored_green_owes_no_next_red_step(self):
        self.started()
        self._remote_trunk()
        from unittest import mock
        payload = json.dumps([{"status": "completed", "conclusion": "success", "headSha": "ddd444",
                               "createdAt": "2026-10-02T00:00:00Z", "url": "https://example.com/run/3",
                               "workflowName": "CI"}])
        bindir = self._gh_dir(payload=payload)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
            out = self.brigade("watch")
        self.assertNotIn("trunk CI", out)
        self._assert_no_red_cues()
