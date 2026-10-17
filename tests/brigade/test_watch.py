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
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc1234")
        self.land("lease", "release", "L1")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: lease L1 was released; claim again before submitting")
        before = (self.at / "restaurant.json").read_text()
        (Path(self.temporary.name) / "state").rename(Path(self.temporary.name) / "moved")
        self.assertEqual(self.brigade("watch"),
                         f"workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: could not renew L1: {self.project.resolve()} has no landing contract; run land.py init in it")
        self.assertEqual((self.at / "restaurant.json").read_text(), before)

    def test_review_passed_parked_and_sent_back_items_each_get_a_watch_line_and_renew(self):
        self.started()
        verdict = ("pass", "record", "D1", "--sha", "abc1234", "--author", CODEX, "--verifier", CLAUDE, "--verdict")
        steps = [(("dish", "D1", "--state", "in-review", "--sha", "abc1234"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review"),
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
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review"), "D1 in-review")
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
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc1234")
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
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc1234")
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

    def test_watch_shows_a_checkout_block_for_a_waiting_ticket_whose_path_left_the_checkout(self):
        self.git_project()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "filler")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "second")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "filler",
                     "--paths", "a.txt")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "second",
                                      "--paths", "b.txt", ok=False),
                         "brigade: nothing fired: 1 of 1 workers running")
        self.brigade("dish", "D1", "--state", "sent-back")
        subprocess.run(["git", "rm", "-q", "b.txt"], cwd=self.project, check=True,
                       capture_output=True, text=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "drop b"],
                       cwd=self.project, check=True, capture_output=True, text=True)
        lines = self.brigade("watch").splitlines()
        self.assertTrue(any(part.startswith("T2: b.txt is not in the checkout. Drop it from --paths") for part in lines), lines)
