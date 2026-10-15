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


class WakeTest(BrigadeTest, _WakeRace):
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

    def test_re_admitting_an_expired_lease_names_the_holder_that_claimed_its_paths(self):
        self.started()
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.assertEqual(self.land("lease", "claim", "--holder", "engine/D1", "--paths", "README.md"), "L2")
        self.assertIn("D1: lease L1 expired; stop its worker, then run lease renew L1", self.brigade("watch"))
        self.assertEqual(self.land("lease", "renew", "L1", ok=False),
                         "land: L1 expired and L2 held by engine/D1 now covers README.md; claim again after it is released")

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

    def test_keep_parked_leaves_the_round_decision_open_and_the_refusal_in_place(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q1")["question"], self.ROUND_QUESTION)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"),
                         "Q1 still open; D1 stays held until 86 answer Q1 --answer 'known limits' or redesign or drop")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("watch").splitlines()[1], self.PENDING)

    def test_watch_names_the_pending_decision_in_review_and_parked(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review"), "D1 in-review")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "a3", "--verdict", "blocked", "--author", CODEX,
                                      "--verifier", CLAUDE), "D1 blocked")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)

    def test_a_late_third_send_back_holds_an_item_in_progress(self):
        self.started()
        self.sent_back(2)
        self.brigade("dish", "D1", "--state", "in-progress", "--thread", "w3")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (thread w3)")
        self.assertEqual(self.send_back("a0", "--late"),
                         "D1: late send-back on record; D1 stays in-progress; 3 send-backs, decision pending")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        before = self.unchanged()
        self.assertEqual(self.brigade("dish", "D1", "--thread", "fresh-worker", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--task", "fresh-task", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.unchanged(before)
        self.assertEqual(self.brigade("dish", "D1", "--thread", "w3", "--reported"), "D1 in-progress")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", "a3"), "D1 in-review")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)

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

    def test_a_blank_dish_decision_does_not_hold_the_item(self):
        self.started()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc1234")
        self.assertEqual(self.brigade("86", "add", "--question", "Ship it?", "--options", "yes, no", "--default", "no"), "Q1")
        self.assertEqual(self.brigade("86", "list"), "Q1: Ship it? Options: yes, no. Default: no.")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")

    def test_a_second_86_add_for_a_held_item_prints_the_open_id_and_appends_nothing(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        table = (self.at / "86.tsv").read_text()
        decisions = [row for row in self.log_rows() if row["kind"] == "decision"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(len([line for line in table.splitlines() if line]) - 1, 1)
        other = "D1: cursor could not pass the seat's options: refused again. Move this coordinator to a provider that passes them?"
        self.assertEqual(self.park_item(other), "Q1")
        self.assertEqual((self.at / "86.tsv").read_text(), table)
        self.assertEqual([row for row in self.log_rows() if row["kind"] == "decision"], decisions)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)

    def test_keep_parked_leaves_the_item_decision_open_and_writes_nothing(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        before = self.store_bytes()
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        self.assertEqual(self.store_bytes(), before)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)

    def test_a_relayed_keep_parked_leaves_the_item_held(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.open_admin()
        before = (self.at / "86.tsv").read_bytes()
        self.admin("request", "--to", "perf", "answer perf Q1: keep parked")
        self.assertEqual(self.brigade("inbox", "take"), "A1: answer perf Q1: keep parked")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        self.assertEqual(self.brigade("inbox", "done", "A1"), "A1 done")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
        self.assertEqual((self.at / "86.tsv").read_bytes(), before)

    def test_free_text_keeps_the_hold_and_writes_nothing(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        before = self.store_bytes()
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "I moved it"), STILL_OPEN)
        self.assertEqual(self.store_bytes(), before)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)

    def test_a_blank_dish_decision_still_closes_on_any_text(self):
        self.started()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc1234")
        self.assertEqual(self.brigade("86", "add", "--question", "Ship it?",
                                      "--options", "keep parked", "--default", "keep parked"), "Q1")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), "Q1 answered")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["state"], row["answer"]), ("answered", "keep parked"))
        self.assertEqual(self.brigade("86", "add", "--question", "Really?",
                                      "--options", "yes", "--default", "no"), "Q2")
        self.assertEqual(self.brigade("86", "answer", "Q2", "--answer", "whatever"), "Q2 answered")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q2")["answer"], "whatever")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")

    def test_a_legacy_item_row_with_no_closing_option_closes_on_any_answer(self):
        self.started()
        ordinary = self.brigade("watch")
        question = "Hold this item?"
        path = self.at / "86.tsv"
        header = path.read_text().splitlines()[0]
        row = "\t".join(["Q1", "2026-10-06T00:00:00+00:00", "open", "D1", question, "keep parked", "keep parked", ""])
        path.write_text(header + "\n" + row + "\n")
        self.assertEqual(
            self.brigade("watch"),
            f"workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: open decision Q1: {question}; launch no worker or verifier until 86 answer Q1",
        )
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), "Q1 answered")
        stored = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((stored["state"], stored["answer"]), ("answered", "keep parked"))
        self.assertEqual(self.brigade("watch"), ordinary)

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

    def test_an_old_bounced_entry_is_ignored_for_the_current_sha(self):
        self.started("--check", "test ! -e BROKEN", paths="README.md,BROKEN")
        old = self.worker_commit("perf/d1", {"README.md": "fast\n", "BROKEN": "x\n"})
        self.passed(old)
        self.submit(old)
        self.brigade("dish", "D1", "--state", "queued")
        self.land("land")
        self.assertRegex(self.brigade("watch").splitlines()[1], r"^D1: E1 bounced: checks failed")
        self.brigade("dish", "D1", "--thread", "worker-1")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "")
        self.assertIn("D1: in progress with no worker thread; launch a fresh worker", self.brigade("watch"))
        self.assertEqual(self.brigade("dish", "D1", "--thread", "worker-1", ok=False),
                         "brigade: thread worker-1 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.brigade("dish", "D1", "--thread", "worker-2")
        new = self.worker_commit("perf/d1", {"BROKEN": None})
        self.passed(new)
        self.assertEqual(self.submit(new), "E2")
        self.brigade("dish", "D1", "--state", "queued")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: E2 queued")

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

    def test_a_refused_fire_mode_full_keeps_the_reason_in_the_unblocked_command(self):
        self.open()
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "a")
        self.assertIn("nothing fired: 1 of 1 workers running",
                      self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Fix the lock order",
                                   "--mode", "full", "--reason", "tickets name a lock", ok=False))
        self.brigade("dish", "D1", "--state", "blocked")
        line = next(line for line in self.brigade("watch").splitlines() if line.startswith("T2:"))
        self.assertEqual(line, "T2: unblocked; run fire --tickets=T2 --station=bug-fix '--summary=Fix the lock order' "
                               "--timebox=60 --mode=full '--reason=tickets name a lock'")
        result = self.bash_unblocked(self.at, line)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "D2"), result.stderr)
        self.assertEqual([row[:4] for row in self.mode_rows()], [["mode", "D2", "full", "tickets name a lock"]])

    def test_a_dishes_row_with_fourteen_fields_or_eighteen_is_malformed_and_one_with_sixteen_is_read(self):
        self.open()
        self.old_item()
        row = (self.at / "dishes.tsv").read_text().splitlines()[1].split("\t")
        for fields in (row[:14], [*row, "", "", ""]):
            (self.at / "dishes.tsv").write_text(self.OLD_DISHES + "\t".join(fields) + "\n")
            self.assertEqual(self.brigade("watch", ok=False),
                             "brigade: bridge-kit/perf/dishes.tsv line 2 is malformed; fix or remove it")
        (self.at / "dishes.tsv").write_text(self.OLD_DISHES + "\t".join([*row, ""]) + "\n")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")

    def test_status_and_watch_count_items_in_progress_and_in_review_as_running_workers(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "3")
        for summary in ("a", "b", "c"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "a")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b")
        self.brigade("dish", "D2", "--state", "in-review", "--sha", "abc1234")
        workers = "workers: 2 of 3 running, 1 idle, auto-start: normal"
        self.assertEqual(self.brigade("status"),
                         "thread not recorded\n"
                         "reporting: milestones, no landing contract, waiting tickets: 1, in progress: 1, in review: 1\n"
                         + workers + "\nkinds: fix 1 waiting, 2 in flight")
        self.assertEqual(self.brigade("watch"), workers + "\nD1: running 0m of 60m (no worker recorded)\nD2: in review")
        self.brigade("pass", "record", "D2", "--sha", "abc1234", "--author", CODEX, "--verifier", CLAUDE, "--verdict", "send-back")
        workers = "workers: 1 of 3 running, 2 idle, auto-start: normal"
        self.assertEqual(self.brigade("status").splitlines()[2], workers)
        self.assertEqual(self.brigade("watch"), workers + "\nD1: running 0m of 60m (no worker recorded)\nD2: sent back")

    def test_the_workers_line_prints_no_idle_worker_when_more_run_than_the_cap(self):
        self.open()
        for ticket in ("T1", "T2"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", ticket)
            self.brigade("fire", "--tickets", ticket, "--station", "bug-fix", "--summary", ticket)
        self.brigade("set", "--workers", "1")
        workers = "workers: 2 of 1 running, 0 idle, auto-start: normal"
        self.assertEqual(self.brigade("status").splitlines()[2], workers)
        self.assertEqual(self.brigade("watch").splitlines()[0], workers)

    def test_startable_lists_a_ticket_whose_paths_are_free_as_startable_with_its_summary(self):
        self.queue()
        self.filed("Fix a", "--paths", "a.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, startable: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
        ]))

    def test_startable_names_the_lease_and_holder_that_block_a_ticket(self):
        self.queue()
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
        self.filed("Fix the app", "--paths", "src/app.py")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, blocked: 1",
            "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src/app.py",
        ]))

    def test_startable_lists_a_ticket_that_records_no_paths_as_unknown(self):
        self.queue()
        self.filed("Fix something")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, unknown: 1",
            "T1 normal fix, 0m: unknown, no paths recorded",
        ]))

    def test_startable_lists_a_decision_ticket_as_a_decision_with_or_without_paths(self):
        self.queue()
        self.filed("Pick a name", "--decision", "--paths", "a.txt", "--priority", "urgent")
        self.filed("Pick a color", "--decision")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, decisions: 2",
            "T1 urgent fix, 0m: decision for the owner; a.txt",
            "T2 normal fix, 0m: decision for the owner",
        ]))

    def test_startable_lists_free_tickets_below_the_auto_start_level_as_below_it(self):
        self.queue()
        self.brigade("set", "--autofire", "urgent")
        self.filed("Sync", "--paths", "a.txt", "--priority", "urgent")
        self.filed("Fix b", "--paths", "b.txt")
        self.filed("Tidy c", "--paths", "c.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: urgent",
            "waiting tickets: 3, startable: 1, below auto-start: 2",
            "T1 urgent fix, 0m: startable; a.txt",
            "  Sync",
            "T2 normal fix, 0m: below auto-start; b.txt",
            "T3 low fix, 0m: below auto-start; c.txt",
        ]))

    def test_startable_lists_no_ticket_as_startable_at_level_off(self):
        self.queue()
        self.brigade("set", "--autofire", "off")
        self.filed("Sync", "--paths", "a.txt", "--priority", "urgent")
        self.filed("Tidy c", "--paths", "c.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: off",
            "waiting tickets: 2, below auto-start: 2",
            "T1 urgent fix, 0m: below auto-start; a.txt",
            "T2 low fix, 0m: below auto-start; c.txt",
        ]))

    def test_startable_lists_a_low_ticket_on_a_startable_ticket_file_or_under_its_directory_as_riding_with_it(self):
        self.queue()
        self.filed("Fix the guide and the scripts", "--paths", "docs/guide.md,scripts")
        self.filed("Tidy the guide", "--paths", "docs/guide.md", "--priority", "low")
        self.filed("Tidy the installer", "--paths", "scripts/install.py", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, riding: 2",
            "T1 normal fix, 0m: startable; docs/guide.md,scripts",
            "  Fix the guide and the scripts",
            "T2 low fix, 0m: rides with T1; docs/guide.md",
            "  Tidy the guide",
            "T3 low fix, 0m: rides with T1; scripts/install.py",
            "  Tidy the installer",
        ]))

    def test_startable_blocks_a_second_normal_ticket_that_shares_a_path_with_a_startable_one(self):
        self.queue()
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Fix a and b", "--paths", "a.txt,b.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 normal fix, 0m: blocked, starts after T1; a.txt,b.txt",
        ]))

    def test_startable_blocks_a_low_ticket_that_shares_paths_with_two_startable_tickets_and_names_both(self):
        self.queue("--workers", "3")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Fix b", "--paths", "b.txt")
        self.filed("Tidy both", "--paths", "a.txt,b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 3 running, 3 idle, auto-start: normal",
            "waiting tickets: 3, startable: 2, blocked: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 normal fix, 0m: startable; b.txt",
            "  Fix b",
            "T3 low fix, 0m: blocked, starts after T1, T2; a.txt,b.txt",
        ]))

    def test_startable_blocks_a_low_ticket_with_one_path_on_a_startable_ticket_file_and_one_path_outside_its_paths(self):
        self.queue()
        self.filed("Fix the guide", "--paths", "docs/guide.md")
        self.filed("Tidy the docs", "--paths", "docs/guide.md,docs/cli.md", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: startable; docs/guide.md",
            "  Fix the guide",
            "T2 low fix, 0m: blocked, starts after T1; docs/cli.md,docs/guide.md",
        ]))

    def test_startable_blocks_a_low_ticket_on_a_directory_above_a_startable_ticket_file_at_a_cap_of_one(self):
        self.queue("--workers", "1")
        self.filed("Fix one", "--paths", "src/a/one.py", "--priority", "urgent")
        self.filed("Fix two", "--paths", "src/b/two.py")
        self.filed("Tidy the source", "--paths", "src", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: normal",
            "waiting tickets: 3, startable: 1, blocked: 2",
            "T1 urgent fix, 0m: startable; src/a/one.py",
            "  Fix one",
            "T2 normal fix, 0m: blocked, waiting for an idle worker; src/b/two.py",
            "T3 low fix, 0m: blocked, starts after T1; src",
        ]))

    def test_startable_blocks_a_free_ticket_when_the_worker_cap_is_reached(self):
        self.queue("--workers", "1")
        self.filed("Running")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Running")
        self.filed("Fix b", "--paths", "b.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 1 running, 0 idle, auto-start: normal",
            "waiting tickets: 1, blocked: 1",
            "T2 normal fix, 0m: blocked, waiting for an idle worker; b.txt",
        ]))

    def test_startable_holds_back_a_free_low_ticket_from_the_last_idle_worker_while_a_normal_ticket_waits(self):
        self.queue()
        self.brigade("set", "--autofire", "low")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Tidy b", "--paths", "b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: low",
            "waiting tickets: 2, startable: 1, held back: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            f"T2 low fix, 0m: {self.HELD_BACK}; b.txt",
        ]))

    def test_startable_lists_a_free_low_ticket_as_startable_at_a_cap_of_one_when_no_urgent_or_normal_ticket_waits(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 1, startable: 1",
            "T1 low fix, 0m: startable; a.txt",
            "  Tidy a",
        ]))

    def test_startable_lists_two_free_low_tickets_as_startable_at_a_cap_of_two_when_no_urgent_or_normal_ticket_waits(self):
        self.queue()
        self.brigade("set", "--autofire", "low")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.filed("Tidy b", "--paths", "b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: low",
            "waiting tickets: 2, startable: 2",
            "T1 low fix, 0m: startable; a.txt",
            "  Tidy a",
            "T2 low fix, 0m: startable; b.txt",
            "  Tidy b",
        ]))

    def test_startable_blocks_a_free_low_ticket_for_no_idle_worker_when_a_normal_ticket_takes_the_one_worker(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Tidy b", "--paths", "b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 low fix, 0m: blocked, waiting for an idle worker; b.txt",
        ]))

    def test_startable_holds_back_a_free_low_ticket_while_a_normal_ticket_blocked_by_a_lease_waits(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
        self.filed("Fix the app", "--paths", "src/app.py")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, blocked: 1, held back: 1",
            "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src/app.py",
            f"T2 low fix, 0m: {self.HELD_BACK}; a.txt",
        ]))

    def test_startable_holds_back_a_free_low_ticket_while_an_urgent_ticket_with_no_paths_waits(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Fix something", "--priority", "urgent")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, unknown: 1, held back: 1",
            "T1 urgent fix, 0m: unknown, no paths recorded",
            f"T2 low fix, 0m: {self.HELD_BACK}; a.txt",
        ]))

    def test_startable_holds_back_a_free_low_ticket_while_a_normal_decision_ticket_waits(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Pick a name", "--decision")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, decisions: 1, held back: 1",
            "T1 normal fix, 0m: decision for the owner",
            f"T2 low fix, 0m: {self.HELD_BACK}; a.txt",
        ]))

    def test_startable_flags_a_low_ticket_filed_more_than_seven_days_ago_and_no_other(self):
        self.queue()
        self.filed("Old and low", "--paths", "a.txt", "--priority", "low")
        self.filed("Low", "--paths", "b.txt", "--priority", "low")
        self.filed("Old", "--paths", "c.txt")
        self.refile("T1", days=7, minutes=1)
        self.refile("T2", days=6, hours=23)
        self.refile("T3", days=9)
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, below auto-start: 2",
            "T3 normal fix, 9d: startable; c.txt",
            "  Old",
            "T1 low fix, 7d (over a week): below auto-start; a.txt",
            "T2 low fix, 6d: below auto-start; b.txt",
        ]))

    def test_startable_with_no_waiting_ticket_prints_the_workers_line_and_says_so(self):
        self.queue()
        self.filed("Done already")
        self.brigade("ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("startable"), self.NONE_RUNNING + "\nno waiting tickets")

    def test_startable_lists_a_ticket_of_a_paused_goal_with_its_reason_and_it_is_not_startable(self):
        self.queue()
        self.goal("activity view")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("goal", "set", "G1", "--state", "paused")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, goal paused: 1",
            "T1 normal fix G1, 0m: its goal is paused; a.txt",
        ]))
        self.brigade("goal", "set", "G1", "--state", "open")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, startable: 1",
            "T1 normal fix G1, 0m: startable; a.txt",
            "  Fix a",
        ]))

    def test_startable_lists_a_ticket_of_a_kind_at_its_cap_with_its_reason_and_it_is_not_startable(self):
        self.queue()
        self.brigade("set", "--cap", "fix=1")
        self.filed("Fix a", "--paths", "a.txt")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.filed("Fix b", "--paths", "b.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 1, kind cap: 1",
            "T2 normal fix, 0m: the fix cap holds (1 of 1); b.txt",
        ]))
        self.brigade("set", "--cap", "fix=")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 1, startable: 1",
            "T2 normal fix, 0m: startable; b.txt",
            "  Fix b",
        ]))

    def test_startable_lists_a_ticket_of_a_goal_at_its_share_with_its_reason_and_it_is_not_startable(self):
        self.queue()
        self.goal("activity view", "--share", "1")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.filed("Fix b", "--paths", "b.txt", "--goal", "G1")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 1, goal share: 1",
            "T2 normal fix G1, 0m: the G1 share holds (1 of 1); b.txt",
        ]))
        self.brigade("goal", "set", "G1", "--share", "2")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 1, startable: 1",
            "T2 normal fix G1, 0m: startable; b.txt",
            "  Fix b",
        ]))

    def test_startable_count_line_names_the_three_new_standings_when_they_occur(self):
        self.queue()
        self.goal("activity view", "--share", "1")
        self.goal("current with upstream")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.brigade("set", "--cap", "fix=1")
        self.brigade("goal", "set", "G2", "--state", "paused")
        self.brigade("ticket", "add", "--summary", "Tidy b", "--kind", "polish", "--paths", "b.txt", "--goal", "G1")
        self.filed("Fix c", "--paths", "c.txt", "--goal", "G2")
        self.filed("Fix d", "--paths", "d.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 3, goal paused: 1, kind cap: 1, goal share: 1",
            "T2 normal polish G1, 0m: the G1 share holds (1 of 1); b.txt",
            "T3 normal fix G2, 0m: its goal is paused; c.txt",
            "T4 normal fix, 0m: the fix cap holds (1 of 1); d.txt",
        ]))

    def test_startable_counts_a_ticket_that_starts_against_the_kind_and_the_goal_it_holds_a_slot_under(self):
        self.queue()
        self.brigade("set", "--cap", "fix=1")
        self.goal("activity view", "--share", "1")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.filed("Fix b", "--paths", "b.txt", "--goal", "G1")
        self.filed("Tidy c", "--paths", "c.txt", "--goal", "G1", "--kind", "polish")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, kind cap: 1, goal share: 1",
            "T1 normal fix G1, 0m: startable; a.txt",
            "  Fix a",
            "T2 normal fix G1, 0m: the fix cap holds (1 of 1); b.txt",
            "T3 normal polish G1, 0m: the G1 share holds (1 of 1); c.txt",
        ]))

    def test_startable_gives_each_spend_control_its_own_line(self):
        self.queue()
        self.goal("activity view", "--share", "1")
        self.goal("current with upstream")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.brigade("set", "--cap", "fix=1")
        self.brigade("goal", "set", "G2", "--state", "paused")
        for summary, kind, goal, paths, priority in (("Start one", "study", "", "one.txt", ""),
                                                     ("Sync two", "fix", "G2", "two.txt", ""),
                                                     ("Fix three", "fix", "", "three.txt", ""),
                                                     ("Tidy four", "polish", "G1", "four.txt", ""),
                                                     ("Measure five", "study", "", "five.txt", ""),
                                                     ("Tidy six", "polish", "", "six.txt", "low")):
            self.brigade("ticket", "add", "--summary", summary, "--kind", kind, "--paths", paths,
                         *(["--priority", priority] if priority else []), *(["--goal", goal] if goal else []))
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 6, startable: 1, blocked: 1, below auto-start: 1, goal paused: 1, kind cap: 1, goal share: 1",
            "T2 normal study, 0m: startable; one.txt",
            "  Start one",
            "T3 normal fix G2, 0m: its goal is paused; two.txt",
            "T4 normal fix, 0m: the fix cap holds (1 of 1); three.txt",
            "T5 normal polish G1, 0m: the G1 share holds (1 of 1); four.txt",
            "T6 normal study, 0m: blocked, waiting for an idle worker; five.txt",
            "T7 low polish, 0m: below auto-start; six.txt",
        ]))

    def test_startable_never_lists_a_low_ticket_of_a_paused_goal_as_riding_with_another_ticket(self):
        # The paused check comes before the ride-along branch, so a paused goal starts nothing even as a rider.
        self.queue()
        self.goal("activity view")
        self.goal("current with upstream", "--share", "1")
        self.filed("a", "--paths", "a.txt", "--goal", "G2")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a", "--paths", "a.txt")
        self.brigade("set", "--cap", "fix=1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--paths", "b.txt", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "fix", "--paths", "c.txt", "--goal", "G2")
        self.brigade("ticket", "add", "--summary", "d", "--kind", "polish", "--paths", "d.txt", "--goal", "G2")
        self.brigade("ticket", "add", "--summary", "e", "--kind", "polish", "--paths", "e.txt", "--priority", "low", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "Add e", "--kind", "feature", "--paths", "e.txt", "--priority", "urgent")
        self.brigade("goal", "set", "G1", "--state", "paused")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 5, startable: 1, goal paused: 2, kind cap: 1, goal share: 1",
            "T6 urgent feature, 0m: startable; e.txt",
            "  Add e",
            "T2 normal fix G1, 0m: its goal is paused; b.txt",
            "T3 normal fix G2, 0m: the fix cap holds (1 of 1); c.txt",
            "T4 normal polish G2, 0m: the G2 share holds (1 of 1); d.txt",
            "T5 low polish G1, 0m: its goal is paused; e.txt",
        ]))

    def test_startable_rides_a_low_ticket_of_an_open_goal_and_pauses_one_of_a_paused_goal(self):
        # The paused check leaves the open goal's rider alone and holds the paused goal's rider even when it overlaps a startable ticket.
        self.queue()
        self.goal("activity view")
        self.goal("current with upstream")
        self.filed("Fix a", "--paths", "a.txt")
        self.brigade("ticket", "add", "--summary", "Tidy a", "--kind", "polish", "--paths", "a.txt", "--priority", "low", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "Pause a", "--kind", "polish", "--paths", "a.txt", "--priority", "low", "--goal", "G2")
        self.brigade("goal", "set", "G2", "--state", "paused")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, riding: 1, goal paused: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 low polish G1, 0m: rides with T1; a.txt",
            "  Tidy a",
            "T3 low polish G2, 0m: its goal is paused; a.txt",
        ]))

    def test_startable_pauses_a_waiting_rider_of_a_goal_whose_running_item_keeps_running(self):
        # A paused goal holds its waiting tickets and lets the item already in flight finish.
        self.queue()
        self.goal("activity view")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a", "--paths", "a.txt")
        self.brigade("goal", "set", "G1", "--state", "paused")
        self.brigade("ticket", "add", "--summary", "Paused rider", "--kind", "polish", "--paths", "b.txt", "--priority", "low", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "Add b", "--kind", "feature", "--paths", "b.txt", "--priority", "urgent")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            "waiting tickets: 2, startable: 1, goal paused: 1",
            "T3 urgent feature, 0m: startable; b.txt",
            "  Add b",
            "T2 low polish G1, 0m: its goal is paused; b.txt",
        ]))
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")
        self.assertEqual(self.brigade("ticket", "list", "--state", "assigned"), "T1 assigned normal fix G1 [user] Fix a")

    def test_startable_orders_tickets_by_priority_then_oldest_filed_then_the_number_in_the_id(self):
        self.open()
        stamp = (datetime.now(timezone.utc) - timedelta(hours=5, minutes=1)).isoformat()
        rows = [("T10", stamp, "", "ten"), ("T9", stamp, "", "nine"), ("T11", stamp, "low", "eleven"),
                ("T12", datetime.now(timezone.utc).isoformat(), "urgent", "twelve"),
                ("T13", (datetime.now(timezone.utc) - timedelta(days=2, minutes=1)).isoformat(), "", "thirteen")]
        (self.at / "rail.tsv").write_text("id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision\n" + "".join(
            f"{ident}\t{at}\twaiting\tuser\t\t\t{summary}\t{priority}\t\t\n" for ident, at, priority, summary in rows))
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 5, unknown: 5",
            "T12 urgent, 0m: unknown, no paths recorded",
            "T13 normal, 2d: unknown, no paths recorded",
            "T9 normal, 5h: unknown, no paths recorded",
            "T10 normal, 5h: unknown, no paths recorded",
            "T11 low, 5h: unknown, no paths recorded",
        ]))

    def test_startable_lists_rail_rows_written_before_the_paths_cell_as_unknown(self):
        self.open()
        self.old_rail((datetime.now(timezone.utc) - timedelta(days=3, minutes=1)).isoformat())
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, unknown: 3",
            "T3 urgent, 3d: unknown, no paths recorded",
            "T1 normal, 3d: unknown, no paths recorded",
            "T2 low, 3d: unknown, no paths recorded",
        ]))
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_startable_without_an_owner_changes_no_store_file_and_no_landing_row(self):
        self.queue()
        self.brigade("set", "--thread", "t1")
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
        self.filed("Fix the app", "--paths", "src/app.py")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Fix something")
        store, landing = self.store_files(), self.landing_rows()
        self.assertEqual(self.brigade("startable", owner=False), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, blocked: 1, unknown: 1",
            "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src/app.py",
            "T2 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T3 normal fix, 0m: unknown, no paths recorded",
        ]))
        self.assertEqual((self.store_files(), self.landing_rows()), (store, landing))
        self.assertEqual(len(landing["lease"]), 1)

    def test_startable_agrees_with_lease_check_on_a_directory_lease_and_on_a_name_that_shares_its_prefix(self):
        self.queue()
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
        self.filed("Fix x", "--paths", "src/app/x.py")
        self.filed("Fix srcx", "--paths", "srcx")
        checks = [subprocess.run([sys.executable, str(ROOT / "t3/added/landing/scripts/land.py"), "--repo", str(self.project),
                                  "lease", "check", "--holder", holder, "--paths", paths],
                                 capture_output=True, text=True, env=self.land_env())
                  for holder, paths in (("perf/T1", "src/app/x.py"), ("perf/T2", "srcx"))]
        self.assertEqual([(check.returncode, check.stdout.strip()) for check in checks],
                         [(1, "L1 held by engine/D1 on src"), (0, "free")])
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src/app/x.py",
            "T2 normal fix, 0m: startable; srcx",
            "  Fix srcx",
        ]))

    def test_startable_prints_one_line_for_a_ticket_the_landing_queue_refuses_in_a_project_that_is_not_a_git_repository(self):
        self.open()
        self.filed("Fix a", "--paths", "src/a.py")
        lines = self.brigade("startable").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[2].startswith("T1 normal fix, 0m: blocked, waiting on the landing queue ("), lines[2])

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

    def test_startable_keeps_ticket_order_with_kinds_and_goals_with_paused_and_share_where_they_hold(self):
        self.queue("--workers", "3")
        self.goal("activity view", "--share", "1")
        self.goal("current with upstream")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "Add b", "--kind", "feature", "--paths", "b.txt", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "Sync c", "--kind", "upkeep", "--paths", "c.txt", "--goal", "G2",
                     "--priority", "urgent")
        self.brigade("ticket", "add", "--summary", "Tidy d", "--kind", "polish", "--paths", "d.txt", "--priority", "low")
        self.brigade("ticket", "add", "--summary", "Look at e", "--kind", "study")
        self.brigade("ticket", "add", "--summary", "Add f", "--kind", "feature", "--paths", "a.txt", "--goal", "G2")
        self.brigade("goal", "set", "G2", "--state", "paused")
        lines = ["workers: 0 of 3 running, 3 idle, auto-start: normal",
                 "waiting tickets: 6, startable: 1, unknown: 1, below auto-start: 1, goal paused: 2, goal share: 1",
                 "T3 urgent{}, 0m: its goal is paused; c.txt",
                 "T1 normal{}, 0m: startable; a.txt", "  Fix a",
                 "T2 normal{}, 0m: the G1 share holds (1 of 1); b.txt",
                 "T5 normal{}, 0m: unknown, no paths recorded",
                 "T6 normal{}, 0m: its goal is paused; a.txt",
                 "T4 low{}, 0m: below auto-start; d.txt"]
        words = iter((" upkeep G2", " fix G1", " feature G1", " study", " feature G2", " polish"))
        self.assertEqual(self.brigade("startable").splitlines(),
                         [line.format(next(words)) if "{}" in line else line for line in lines])
        for number in range(1, 7):
            self.hand_edit("rail.tsv", f"T{number}", kind="", goal="")
        self.assertEqual(self.brigade("startable").splitlines(), ["workers: 0 of 3 running, 3 idle, auto-start: normal",
                                                                  "waiting tickets: 6, startable: 3, blocked: 1, unknown: 1, "
                                                                  "below auto-start: 1",
                                                                  "T3 urgent, 0m: startable; c.txt", "  Sync c",
                                                                  "T1 normal, 0m: startable; a.txt", "  Fix a",
                                                                  "T2 normal, 0m: startable; b.txt", "  Add b",
                                                                  "T5 normal, 0m: unknown, no paths recorded",
                                                                  "T6 normal, 0m: blocked, starts after T1; a.txt",
                                                                  "T4 low, 0m: below auto-start; d.txt"])

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

    def test_fire_doc_item_keeps_workers_kind_and_lease_checks(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
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

    def test_model_clear_refuses_a_seat_with_no_model_row_and_a_model_needs_provider_and_name(self):
        self.open()
        self.assertEqual(self.brigade("model", "clear", "grok/grok-4.7", ok=False),
                         "brigade: grok/grok-4.7 is not skipped")
        for seat in ("grok", "grok/", "/grok-4.7", "grok /grok-4.7"):
            self.assertEqual(self.brigade("model", "skip", seat, "--cause", "rate-limit", ok=False),
                             "brigade: a model is provider/model", seat)
        self.assertEqual([row for row in self.log_rows() if row["kind"] == "model"], [])

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
