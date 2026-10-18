from .support import (
    setUpModule,
    BULLETS,
    BrigadeTest,
    CLAUDE,
    CODEX,
    DOC_SETTINGS,
    Path,
    ROOT,
    SCRIPT,
    THREAD_A,
    ProcessReason,
    real_process,
    real_popen,
    _race_args,
    THREAD_B,
    THREAD_C,
    _ItemsRace,
    _owner_words,
    _wait_for_path,
    io,
    json,
    mock,
    os,
    re,
    runpy,
    shlex,
    subprocess,
    sys,
)


class ItemsTest(BrigadeTest, _ItemsRace):
    def test_fire_groups_waiting_tickets_into_one_dish(self):
        self.open()
        self.brigade("set", "--intake", "github")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Startup is slow on cold boot", "--source", "github", "--ref", "#12"), "T1")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Splash screen hangs"), "T2")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--station", "perf-issue", "--summary", "Cut cold start time"), "D1")
        self.assertEqual(self.brigade("ticket", "list"),
                         "T1 assigned normal fix [github] Startup is slow on cold boot #12\nT2 assigned normal fix [user] Splash screen hangs")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "again", ok=False),
                         "brigade: T1 is assigned, not waiting")

    def test_send_back_blocks_landing(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "send-back",
                     "--author", CLAUDE, "--verifier", CODEX, "--note", "test asserts the bug")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False),
                         "brigade: D1 at abc: send-back (test asserts the bug)")
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued", ok=False),
                         "brigade: only reviewed work lands: D1 at abc: send-back (test asserts the bug)")

    def test_the_built_brigade_refuses_a_bedrock_claude_author_with_a_claude_verifier(self):
        self.fired_bug_fix()
        built = ROOT / "skills/brigade/scripts/brigade.py"
        # process exit status
        result = real_process(
            ProcessReason.EXIT_STATUS, built,
            ["--store", str(self.store), "--at", str(self.at), *_owner_words(self.at),
             "pass", "record", "D1", "--sha", "abc", "--verdict", "pass",
             "--author", self.BEDROCK, "--verifier", CLAUDE],
        )
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertEqual(result.stderr.strip(), self.same_family_refusal(self.BEDROCK, CLAUDE))
        self.assertEqual(self.pass_rows(), [])

    def test_pass_record_without_roles_py_refuses_and_records_nothing(self):
        self.fired_bug_fix()
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        missing = Path(self.temporary.name) / "missing"
        glob["BUILT_ROLES"], glob["SOURCE_ROLES"] = missing / "built.py", missing / "source.py"
        argv = ["--store", str(self.store), "--at", str(self.at), *_owner_words(self.at), "pass", "record", "D1",
                "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX]
        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            code = glob["main"](argv)
        self.assertEqual(code, 1)
        self.assertEqual(stderr.getvalue(), f"brigade: cannot find roles.py at {missing / 'built.py'} or "
                                            f"{missing / 'source.py'}; build or reinstall pstack-t3\n")
        self.assertEqual(self.pass_rows(), [])

    def test_from_report_changes_only_the_ticket_table_the_log_and_last_activity(self):
        self.fired_bug_fix()
        self.review_file("D1.md", BULLETS)

        def changed_by(*command):
            before = self.store_files()
            self.brigade(*command)
            after = self.store_files()
            return before, sorted(name for name in after if after[name] != before.get(name))

        _, by_hand = changed_by("ticket", "add", "--kind", "fix", "--summary", "by hand")
        (self.at / "restaurant.lock").unlink()
        before, from_report = changed_by("ticket", "add", "--from-report", "reports/D1.md")
        self.assertEqual(from_report, ["log.tsv", "rail.tsv", "restaurant.json"])
        self.assertEqual(from_report, by_hand)
        old, new = json.loads(before["restaurant.json"]), json.loads((self.at / "restaurant.json").read_text())
        self.assertEqual([key for key in new if new[key] != old.get(key)], ["lastActivityAt"])

    def test_a_member_send_back_recorded_after_the_passing_rows_changes_no_verdict(self):
        self.fired_bug_fix()
        self.record("abc", "pass")
        self.assertEqual(self.brigade("pass", "record", "D1", "--pr", "u/9", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", "grok/grok-4.7", "--note", "nit", "--member"),
                         "D1: member send-back on record; D1 stays passed")
        self.assertEqual(self.dish_fields("state", "pr", "sha"), ("passed", "", "abc"))
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc"), f"D1 at abc passed review by {CODEX}")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"verdict": "pass", "author": CLAUDE, "verifier": CODEX, "note": "", "crossFamily": True})
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued"), "D1 queued")

    def test_the_first_dish_thread_writes_the_worker_row_open_at_attempt_one(self):
        self.fired_bug_fix()
        self.assertEqual((self.at / "threads.tsv").read_text(),
                         "at\tdish\tattempt\trole\tthread\tseat\tstart\tend\n")
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_A), "D1 in-progress")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], THREAD_A)

    def test_a_second_attempt_after_a_send_back_gets_role_fix_worker_attempt_two_and_closes_the_first_row(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.record("abc", "send-back", "--note", "test asserts the bug")
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_B), "D1 sent-back")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", "stamp"],
                          ["D1", "2", "fix worker", THREAD_B, "", "stamp", ""]])

    def test_the_same_dish_thread_command_twice_adds_one_row(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        after = (self.at / "threads.tsv").read_bytes()
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_A), "D1 in-progress")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])
        self.assertEqual((self.at / "threads.tsv").read_bytes(), after)

    def test_an_old_store_whose_threads_table_is_lost_starts_the_next_worker_row_at_attempt_one(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.record("abc", "send-back", "--note", "test asserts the bug")
        self.brigade("dish", "D1", "--state", "in-progress")
        (self.at / "threads.tsv").unlink()
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_B), "D1 in-progress")
        self.assertEqual((self.at / "threads.tsv").read_text().splitlines()[0],
                         "at\tdish\tattempt\trole\tthread\tseat\tstart\tend")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_B, "", "stamp", ""]])

    def test_dish_reported_and_a_state_change_close_the_worker_row(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.assertEqual(self.brigade("dish", "D1", "--reported"), "D1 in-progress")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", "stamp"]])
        self.brigade("dish", "D1", "--thread", THREAD_B)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review"), "D1 in-review")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", "stamp"],
                          ["D1", "2", "fix worker", THREAD_B, "", "stamp", "stamp"]])
        self.brigade("dish", "D1", "--thread", THREAD_C)
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped", "--stopped", "run-1"),
                         "D1 dropped; T1 waiting again")
        self.assertEqual([row[2:5] for row in self.thread_rows()],
                         [["1", "worker", THREAD_A], ["2", "fix worker", THREAD_B], ["3", "fix worker", THREAD_C]])
        self.assertEqual([row[7] for row in self.thread_rows()], ["stamp", "stamp", "stamp"])

    def test_dish_thread_with_reported_closes_that_worker_row(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_A, "--reported"), "D1 in-progress")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", "stamp"]])

    def test_dish_thread_with_a_state_that_ends_the_attempt_closes_that_worker_row(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_A, "--state", "in-review"), "D1 in-review")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", "stamp"]])

    def test_a_state_flag_that_changes_nothing_leaves_the_worker_row_open(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])

    def test_a_replacement_worker_started_with_its_state_and_thread_together_keeps_its_row_open(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", THREAD_B), "D1 in-progress")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", "stamp"],
                          ["D1", "2", "fix worker", THREAD_B, "", "stamp", ""]])

    def test_close_warns_about_the_report_of_an_item_in_review_that_has_no_claims_check_line(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "# D1\n\nStatus: done\n\nThe Claims check found nothing.\nclaims check: 3 statements\n")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        code, before, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, self.no_claims("in-review")))
        code, out, err = self.close_run("--to-file")
        self.assertEqual((code, err), (0, self.no_claims("in-review")))
        self.assertEqual(Path(out).read_text().strip(), before)
        code, out, err = self.close_run()
        self.assertEqual((code, err), (0, self.no_claims("in-review")))
        self.assertNotIn("warning", out)
        self.assertEqual(self.close_run()[2], self.no_claims("in-review"))

    def test_close_prints_the_claims_check_warning_after_the_warning_for_an_unrecorded_review(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "# D1\n")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.review_file("D1-review-1.md")
        self.assertEqual(self.close_run("--dry-run")[::2],
                         (0, self.no_row("D1-review-1.md") + "\n" + self.no_claims("in-review")))

    def test_close_accepts_each_shape_of_a_claims_check_line(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        for line in ("Claims check: 14 statements checked, 2 corrected", "## Claims check", "### Claims check",
                     "- Claims check: 3 statements checked", "**Claims check.**", "1. **Claims check**",
                     "  > Claims check: none", "2) `Claims check`", "_Claims check_"):
            self.review_file("D1.md", f"# D1\n\nStatus: done\n\n{line}\n\nMore.\n")
            self.assertEqual(self.close_run("--dry-run")[::2], (0, ""), line)

    def test_close_reads_a_report_that_is_not_utf8_for_its_claims_check_line(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        report = self.review_file("D1.md")
        report.write_bytes(b"# D1 \xff\xfe\n\nStatus: done\n")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_claims("in-review")))
        report.write_bytes(b"# D1 \xff\xfe\n\n## Claims check\n")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))

    def test_close_prints_no_claims_check_warning_for_an_item_in_progress_sent_back_blocked_or_merged(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "# D1\n\nStatus: done\n")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.record("abc", "send-back")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.record("abc", "blocked")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.record("abc", "pass")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_claims("passed")))
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))

    def test_close_prints_no_claims_check_warning_for_a_report_that_is_missing_or_a_link(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        target = Path(self.temporary.name) / "elsewhere.md"
        target.write_text("# D1\n")
        (self.at / "reports").mkdir()
        (self.at / "reports" / "D1.md").symlink_to(target)
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        (self.at / "reports" / "D1.md").unlink()
        self.review_file("D1.md", "# D1\n")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_claims("in-review")))

    def test_report_entry_names_the_kind_of_each_entry_under_reports(self):
        self.fired_bug_fix()
        reports = self.review_file("file.md").parent
        (Path(self.temporary.name) / "findings.md").write_text("findings\n")
        self.linked("live.md", "findings.md")
        self.linked("dangling.md", "gone.md")
        (reports / "directory.md").mkdir()
        expected = {"file.md": "file", "live.md": "link", "dangling.md": "link", "directory.md": "other", "absent.md": "missing"}
        if hasattr(os, "mkfifo"):
            os.mkfifo(reports / "fifo.md")
            expected["fifo.md"] = "other"
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        restaurant = glob["Restaurant"](self.at)
        self.assertEqual({name: glob["report_entry"](restaurant, name) for name in expected}, expected)
        reports.rename(self.at / "kept")
        reports.symlink_to(self.at / "kept")
        self.assertEqual({name: glob["report_entry"](restaurant, name) for name in expected},
                         {**expected, "file.md": "elsewhere"})
        reports.unlink()
        reports.write_text("not a directory\n")
        self.assertEqual({glob["report_entry"](restaurant, name) for name in expected}, {"missing"})

    def test_a_send_back_clears_the_worker_and_refuses_the_old_thread(self):
        self.open()
        self.fire_one()
        self.brigade("dish", "D1", "--state", "sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-8"), "D1 sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-9", ok=False),
                         "brigade: thread thread-9 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "thread-8")
        refused = self.brigade("dish", "D1", "--state", "in-progress", "--thread", "thread-9", ok=False)
        self.assertEqual(refused, "brigade: thread thread-9 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "sent-back")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "thread-8")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", "thread-10"), "D1 in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "thread-10")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in progress with no worker thread; launch a fresh worker")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-9", ok=False),
                         "brigade: thread thread-9 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-10", ok=False),
                         "brigade: thread thread-10 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-11"), "D1 in-progress")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-11"), "D1 in-progress")

    def test_a_new_branch_prints_the_commands_that_lease_its_fragment(self):
        from unittest import mock
        self.init_landing("--cap", "3")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.assertEqual(self.renamed(), "\n".join([
                "D1 in-progress",
                "D1: L1 does not cover changes/perf%2Fd1-r2.md; cover it with this command:",
                "  $B dish D1 --add-paths changes/perf%2Fd1-r2.md",
            ]))
            row = self.table_row(self.at, "dishes.tsv", "D1")
            self.assertEqual((row["branch"], row["lease"], row["paths"]), ("perf/d1-r2", "L1", "src,changes/perf%2Fd1.md"))
            self.assertEqual(self.lease_lines(), ["L1 active perf/D1: changes/perf%2Fd1.md, src"])
            self.assertEqual(self.brigade("dish", "D1", "--add-paths", "changes/perf%2Fd1-r2.md"), "D1: L2 replaces L1")
            self.assertEqual(self.lease_lines(), ["L2 active perf/D1: changes/perf%2Fd1-r2.md, changes/perf%2Fd1.md, src"])
            self.assertEqual(self.brigade("dish", "D1", "--branch", "perf/d1-r2"), "D1 in-progress")

    def test_an_active_fragment_gap_prints_no_manual_claim_or_release(self):
        from unittest import mock
        self.init_landing("--cap", "3")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            result = self.renamed()
            self.assertIn("$B dish D1 --add-paths changes/perf%2Fd1-r2.md", result)
            self.assertNotIn("$L lease claim", result)
            self.assertNotIn("$L lease release", result)
            self.assertNotIn("--lease <new lease>", result)

    def test_a_branch_rename_by_a_replaced_owner_claims_nothing(self):
        from unittest import mock
        self.init_landing("--cap", "3")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            taken = []
            result = self.renamed(lambda: taken or taken.append(
                self.brigade("set", "--thread", "replacement", "--replace")))
            self.assertEqual(len(taken), 1)
            self.assertIn("$B dish D1 --add-paths", result)
            self.assertEqual(self.lease_lines(), ["L1 active perf/D1: changes/perf%2Fd1.md, src"])
            stale = self.run_at(self.at, "--owner", "coordinator@1", "dish", "D1", "--branch", "perf/d1-r3", ok=False)
            self.assertEqual(stale, "brigade: owner coordinator@1 is stale; this store is owned by replacement@2")
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["branch"], "perf/d1-r2")
            self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_a_branch_rename_after_submit_says_the_lease_cannot_change(self):
        self.init_landing("--cap", "3")
        from unittest import mock
        with mock.patch.dict(os.environ, self.land_env()):
            self.open()
            self.brigade("set", "--change-note-dir", "changes")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "one",
                         "--branch", "perf/d1", "--paths", "a.txt")
            sha = self.worker_commit("perf/d1", {"a.txt": "changed\n"})
            self.passed(sha)
            self.submit(sha)
            self.brigade("dish", "D1", "--state", "queued")
            self.assertEqual(self.brigade("dish", "D1", "--branch", "perf/d1-r2"), "\n".join([
                "D1 queued",
                "D1: L1 is submitted, so it cannot take changes/perf%2Fd1-r2.md; the submitted commit lands as it is. "
                "Run dish D1 --branch perf/d1-r2 again if its entry bounces",
            ]))
            self.assertEqual(self.lease_lines(), ["L1 submitted perf/D1: a.txt, changes/perf%2Fd1.md"])

    def test_the_third_send_back_refuses_another_fix_round(self):
        self.started()
        self.assertEqual(self.sent_back(2), "D1 sent-back")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: sent back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.send_back("a3"), "D1 sent-back; 3 send-backs, decision pending")
        before = tuple((self.at / name).read_bytes() for name in ("dishes.tsv", "log.tsv", "86.tsv"))
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", "w9", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(tuple((self.at / name).read_bytes() for name in ("dishes.tsv", "log.tsv", "86.tsv")), before)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", sha), "D1 in-review")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)

    def test_known_limits_closes_the_round_decision_and_lifts_the_refusal(self):
        self.round_decision_lifts_the_refusal("Known limits.", "known limits")

    def test_redesign_closes_the_round_decision_and_lifts_the_refusal(self):
        self.round_decision_lifts_the_refusal("redesign", "redesign")

    def test_drop_closes_the_round_decision_and_lifts_the_refusal(self):
        self.round_decision_lifts_the_refusal("drop", "drop")

    def test_late_send_backs_written_after_a_pass_count_once_the_item_is_back_in_progress(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.passed(sha)
        for sha in ("m1", "m2", "m3"):
            self.assertEqual(self.send_back(sha, "--late"), "D1: late send-back on record; D1 stays passed")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: passed, not submitted")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("passed"))
        self.brigade("dish", "D1", "--state", "queued")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade("dish", "D1", "--thread", "bounce-worker", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "known limits"), "Q1 answered")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "bounce-worker"), "D1 in-progress")

    def test_a_late_send_back_below_the_budget_names_no_decision(self):
        self.started()
        self.sent_back(1)
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.send_back("a0", "--late"), "D1: late send-back on record; D1 stays in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in progress with no worker thread; launch a fresh worker")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "w2"), "D1 in-progress")

    def test_fire_refuses_a_ticket_of_an_item_that_owes_its_decision(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade(*self.FIRE_AGAIN, ok=False), self.NO_FIRE)
        self.assertEqual(self.brigade("ticket", "set", "T1", "--state", "waiting"), "T1 waiting")
        before = self.unchanged()
        self.assertEqual(self.brigade(*self.FIRE_AGAIN, ok=False), self.NO_FIRE)
        self.unchanged(before)
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] one")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        self.assertEqual(self.brigade(*self.FIRE_AGAIN, ok=False), self.NO_FIRE)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "redesign"), "Q1 answered")
        self.assertEqual(self.brigade(*self.FIRE_AGAIN), "D2")

    def test_fire_takes_the_ticket_of_an_item_under_its_budget(self):
        self.started()
        self.sent_back(2)
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
        self.assertEqual(self.brigade(*self.FIRE_AGAIN), "D2")

    def test_an_ordinary_decision_answered_with_a_round_option_lifts_no_refusal(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Redesign the title wording?",
                                      "--options", "redesign, keep parked", "--default", "keep parked"), "Q1")
        held = "brigade: D1 is held by Q1; answer it, then run 86 add --dish D1 --round-budget"
        table = (self.at / "86.tsv").read_bytes()
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), held)
        self.assertEqual((self.at / "86.tsv").read_bytes(), table)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "redesign"), "Q1 answered")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q1")["kind"], "")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q2")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q2")["kind"], "round-budget")
        self.assertEqual(self.brigade("86", "answer", "Q2", "--answer", "redesign"), "Q2 answered")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")

    def test_member_send_backs_do_not_count_toward_the_round_budget(self):
        self.started()
        self.sent_back(2)
        for seat in ("grok/grok-4.7", "opencode/kimi-k3"):
            self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "a2", "--verdict", "send-back", "--author", CODEX,
                                          "--verifier", seat, "--member"),
                             "D1: member send-back on record; D1 stays sent-back")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: sent back")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 2 send-backs since its last pass or round decision; the decision opens at 3")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")

    def test_dropping_a_queued_item_whose_entry_bounced_releases_its_lease(self):
        self.started("--check", "test ! -e BROKEN", paths="README.md,BROKEN")
        self.brigade("dish", "D1", "--thread", "w1")
        sha = self.worker_commit("perf/d1", {"BROKEN": "x\n"})
        self.passed(sha)
        self.submit(sha)
        self.brigade("dish", "D1", "--state", "queued")
        self.land("land")
        self.assertIn("D1 holds L1", self.brigade("dish", "D1", "--state", "dropped", ok=False))
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped", "--stopped", "idle"), "D1 dropped; T1 waiting again")
        self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_a_drop_waits_for_the_worker_and_then_releases_the_lease(self):
        self.started()
        self.brigade("dish", "D1", "--thread", "w1")
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped", ok=False),
                         "brigade: D1 holds L1 and its worker may still be running; "
                         "wait for its run with t3_thread_wait, then pass --stopped <run id>")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")
        self.assertEqual(self.land("lease", "list").split(" until ")[0], "L1 active perf/D1")
        self.assertIn("L1 held by perf/D1",
                      self.land("lease", "claim", "--holder", "engine/D1", "--paths", "README.md", ok=False))
        self.worker_commit("perf/d1", {"README.md": "late\n"})
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped", "--stopped", "r1"), "D1 dropped; T1 waiting again")
        dropped = [line for line in (self.at / "log.tsv").read_text().splitlines() if "\tD1\tdropped\t" in line]
        self.assertEqual(len(dropped), 1)
        self.assertIn("stopped r1", dropped[0])
        self.assertEqual(self.land("lease", "claim", "--holder", "engine/D1", "--paths", "README.md"), "L2")

    def test_a_late_worker_submit_after_the_drop_is_refused(self):
        self.started()
        self.brigade("dish", "D1", "--thread", "w1")
        self.worker_commit("perf/d1", {"README.md": "first\n"})
        case = Path(self.temporary.name)
        worktree = case / "perf-d1"
        script = (f"while [ ! -e {shlex.quote(str(case / 'go'))} ]; do sleep 0.01; done; "
                  f"echo late > README.md && git add -A && git -c user.name=t -c user.email=t@t commit -qm late && "
                  f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 't3/added/landing/scripts/land.py'))} "
                  f"--repo {shlex.quote(str(self.project))} submit --holder perf/D1 --branch perf/d1 "
                  f"--sha $(git rev-parse HEAD) --lease L1 --reviewer {CLAUDE}")
        worker = subprocess.Popen(["bash", "-c", script], cwd=worktree, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True)
        try:
            self.assertEqual(self.brigade("dish", "D1", "--state", "dropped", "--stopped", "r1"), "D1 dropped; T1 waiting again")
            engine = self.store / "bridge-kit" / "engine"
            self.run_at(engine, "open", "--project-root", str(self.project), "--name", "Engine")
            (engine / "menu.md").write_text("## Purpose\n\nEngine.\n")
            self.run_at(engine, "ticket", "add", "--kind", "fix", "--summary", "engine work")
            self.assertEqual(self.run_at(engine, "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "e",
                                         "--paths", "README.md"), "D1 (lease L2 held by engine/D1)")
            (case / "go").touch()
            _, err = worker.communicate(timeout=30)
        finally:
            worker.kill()
        self.assertEqual(worker.returncode, 1)
        self.assertEqual(err.strip(), "land: L1 is not an active lease held by perf/D1; claim one before submitting")
        self.assertEqual(self.land("status", "--holder", "perf/D1"), "no entries held by perf/D1")
        self.assertEqual([line.split(" until ")[0] for line in self.land("lease", "list").splitlines()],
                         ["L2 active engine/D1"])
        self.assertEqual(self.table_row(engine, "dishes.tsv", "D1")["lease"], "L2")

    def test_a_replaced_coordinators_paused_commands_change_nothing(self):
        self.init_landing()
        self.landing_env()
        docs = self.store / "bridge-kit" / "docs"
        self.run_at(docs, "open", "--project-root", str(self.project), "--name", "Docs")
        (docs / "menu.md").write_text("## Purpose\n\nDocs.\n")
        self.run_at(docs, "set", "--thread", "t1")
        old = ("--owner", "t1@1")
        self.assertIn("owner t1@1", self.run_at(docs, *old, "status"))
        self.run_at(docs, *old, "ticket", "add", "--kind", "fix", "--summary", "one")
        self.assertEqual(self.run_at(docs, *old, "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                                     "--paths", "README.md"), "D1 (lease L1 held by docs/D1)")
        self.run_at(docs, *old, "dish", "D1", "--thread", "w1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0", "--owner", "docs/@1")
        stale_brigade = "brigade: owner t1@1 is stale; this store is owned by t2@2"
        stale_land = "land: owner docs/@1 is stale; docs/ is at generation 2"
        land_script = str(ROOT / "t3/added/landing/scripts/land.py")
        commands = [
            ("pause", ["--store", str(self.store), "--at", str(docs), *old, "ticket", "add", "--kind", "fix", "--summary", "late"], stale_brigade),
            ("pause", ["--store", str(self.store), "--at", str(docs), *old, "dish", "D1", "--state", "dropped", "--stopped", "r1"],
             stale_brigade),
            ("land-pause", ["--repo", str(self.project), "lease", "release", "L1", "--owner", "docs/@1"], stale_land),
            ("land-pause", ["--repo", str(self.project), "lease", "renew", "L1", "--owner", "docs/@1"], stale_land),
        ]
        running = []
        support = Path(__file__).resolve().parent / "support.py"
        for number, (mode, args, message) in enumerate(commands):
            case = Path(self.temporary.name) / f"stale{number}"
            case.mkdir()
            proc = real_popen(
                ProcessReason.RACE,
                [sys.executable, str(support), *_race_args(mode, case, args)],
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                text=True)
            running.append((case, proc, message))
        for case, _, _ in running:
            _wait_for_path(case / "paused", timeout=20)
        self.run_at(docs, "set", "--thread", "t2", "--replace")
        self.assertIn("owner t2@2", self.run_at(docs, "--owner", "t2@2", "status"))
        tables = ("rail.tsv", "log.tsv", "dishes.tsv", "restaurant.json")
        after_replace = {name: (docs / name).read_bytes() for name in tables}
        row = self.lease_row(1)
        for case, proc, message in running:
            (case / "proceed").touch()
            code, _, err = self._finish_race(proc)
            self.assertEqual((code, err), (1, message))
        self.assertEqual(self.run_at(docs, *old, "set", "--thread", "t2", "--workers", "9", ok=False), stale_brigade)
        self.assertEqual(self.run_at(docs, *old, "set", "--thread", "t1", ok=False), "brigade: thread t2 already recorded")
        self.assertEqual({name: (docs / name).read_bytes() for name in tables}, after_replace)
        self.assertEqual(self.lease_row(1), row)
        new = ("--owner", "t2@2")
        self.assertEqual(self.run_at(docs, *new, "ticket", "add", "--kind", "fix", "--summary", "late"), "T2")
        self.assertEqual(self.land("lease", "renew", "L1", "--owner", "docs/@2"), "L1 renewed")
        self.assertEqual(self.land("lease", "release", "L1", "--owner", "docs/@2"), "L1 released")
        self.assertEqual(self.run_at(docs, *new, "dish", "D1", "--state", "dropped", "--stopped", "r1"), "D1 dropped; T1 waiting again")
        self.assertEqual(self.land("owner", "--prefix", "docs/", "--generation", "2"), "docs/ at generation 2")
        self.assertEqual(self.land("owner", "--prefix", "docs/", "--generation", "1", ok=False),
                         "land: docs/ is at generation 2; a floor never lowers")

    def test_dish_mode_full_twice_keeps_the_first_reason(self):
        self.fired_bug_fix()
        self.assertEqual(self.brigade("dish", "D1", "--mode", "full", "--reason", "contested: x"),
                         "D1 in-progress, mode full: contested: x")
        self.assertEqual(self.brigade("dish", "D1", "--mode", "full", "--reason", "y"),
                         "D1 in-progress, mode full: contested: x")
        self.assertEqual([row[:4] for row in self.mode_rows()], [["mode", "D1", "full", "contested: x"]])
        self.assertEqual(self.brigade("dish", "D1", "--state", "blocked", "--mode", "full", "--reason", "y"),
                         "D1 blocked, mode full: contested: x")
        self.record("abc", "pass")
        self.brigade("dish", "D1", "--state", "queued")
        self.assertEqual(self.brigade("dish", "D1", "--mode", "full", "--reason", "z", ok=False),
                         "brigade: D1 is queued; only open work moves to full mode")
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.brigade("dish", "D1", "--mode", "full", "--reason", "z", ok=False),
                         "brigade: D1 is merged; only open work moves to full mode")
        self.assertEqual(len(self.mode_rows()), 1)

    def test_dish_mode_full_with_a_stale_owner_appends_nothing(self):
        self.fired_bug_fix()
        self.brigade("set", "--thread", "c1")
        self.brigade("set", "--thread", "c2", "--replace")
        stale = "brigade: owner c1@1 is stale; this store is owned by c2@2"
        before = (self.at / "log.tsv").read_bytes()
        self.assertEqual(self.brigade("--owner", "c1@1", "dish", "D1", "--mode", "full", "--reason", "r", ok=False), stale)
        self.assertEqual((self.at / "log.tsv").read_bytes(), before)
        self.brigade("dish", "D1", "--mode", "full", "--reason", "r")
        after = (self.at / "log.tsv").read_bytes()
        self.assertEqual(self.brigade("--owner", "c1@1", "dish", "D1", "--mode", "full", "--reason", "r", ok=False), stale)
        self.assertEqual((self.at / "log.tsv").read_bytes(), after)
        self.record("abc", "pass")
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.brigade("--owner", "c1@1", "dish", "D1", "--mode", "full", "--reason", "r", ok=False), stale)

    def test_fire_mode_full_records_the_escalation_after_the_dish_row(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "lock order")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the lock order",
                                      "--mode", "full", "--reason", "tickets name a lock"), "D1")
        rows = [line.split("\t")[1:4] for line in (self.at / "log.tsv").read_text().splitlines()[1:]]
        self.assertEqual(rows[-3:], [["dish", "D1", "in-progress"], ["mode", "D1", "full"], ["ticket", "T1", "assigned"]])
        self.assertEqual(self.mode_rows()[0][3], "tickets name a lock")

    def test_a_store_that_keeps_changing_refuses_the_brief_after_three_resolutions(self):
        self.coordinator()
        case = Path(self.temporary.name) / "race"
        case.mkdir()
        support = Path(__file__).resolve().parent / "support.py"
        churning = real_popen(
            ProcessReason.PRE_MAIN,
            [sys.executable, str(support), *_race_args(
                "brief-churn", case,
                ["--store", str(self.store), "--at", str(self.at), *_owner_words(self.at), *self.BRIEF])],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        self.assertEqual(self._finish_race(churning), (1, "", "brigade: D1 changed while resolving its mode; run brief again"))
        self.assertFalse((self.at / "briefs/D1.md").exists())

    def test_pass_check_fails_at_the_sha_of_a_pass_with_edits_row(self):
        self.fired_bug_fix()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.record_edits("abc", "--edits", "D1-review-1.md", "--note", "two wording edits")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False),
                         "brigade: D1 at abc: pass-with-edits (two wording edits)")
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued", ok=False),
                         "brigade: only reviewed work lands: D1 at abc: pass-with-edits (two wording edits)")

    def test_edits_verify_and_pass_record_refuse_a_report_with_a_fence_that_starts_after_three_spaces(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "wrold", "world"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        self.quoting_report("   ````", "   ````")
        head = self.head_commit(self.QUOTED_APPLIED)
        refusal = "brigade: reports/D1-review-1.md: line 5 " + self.NESTED_FENCE
        self.assertEqual(self.verify(reviewed, head), (1, "", refusal))
        self.assertEqual(self.edits_rows(), [])
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.record_edits(head, "--edits", "D1-review-1.md", ok=False), refusal)
        self.assertEqual(len(self.pass_rows()), 1)

    def test_edits_verify_refuses_a_to_and_a_from_that_name_a_commit_by_a_ref(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        refusal = "brigade: {} names a commit by something other than the start of its id; pass the commit id"
        self.assertEqual(self.verify(reviewed, "HEAD"), (1, "", refusal.format("HEAD")))
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record_edits("main", "--edits", "D1-review-1.md")
        self.assertEqual(self.verify("main", head), (1, "", refusal.format("main")))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_refuses_a_from_with_no_pass_with_edits_round_and_a_sha_that_is_no_commit(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.brigade("dish", "D1", "--state", "in-progress")
        self.send_back(head)
        self.assertEqual(self.verify(head, head), (1, "", f"brigade: D1 has no pass-with-edits round at {head}"))
        self.assertEqual(self.verify(reviewed[:12], head),
                         (1, "", f"brigade: D1 has no pass-with-edits round at {reviewed[:12]}"))
        absent = "0" * 40
        self.assertEqual(self.verify(reviewed, absent),
                         (1, "", f"brigade: {absent} is not a commit in {self.project.resolve()}"))

    def test_a_second_review_report_lifts_the_refusal_of_a_pass_and_the_edits_report_does_not(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello world\nsecond line, changed\n")
        self.assertEqual(self.verify(reviewed, head)[0], 1)
        self.assertEqual(self.record_pass_at(head, "--report", "reports/D1-review-1.md", ok=False), self.gate(reviewed, head))
        self.review_file("D1-review-2.md", "Verdict: pass\n")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", head), "D1 in-review")
        self.assertEqual(self.record_pass_at(head, "--report", "reports/D1-review-2.md"), "D1 passed")

    def test_a_send_back_after_a_pass_with_edits_round_ends_the_refusal_of_a_pass(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.send_back("h2")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.record_pass_at("h3"), "D1 passed")

    def test_fire_gives_the_item_the_kind_and_goal_of_its_lead_ticket_and_leaves_that_tickets_two_cells_as_they_were(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "polish", "--goal", "G2")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"), "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("polish", "G2"))
        self.assertEqual(self.label("rail.tsv", "T1"), ("polish", "G2"))

    def test_fire_kind_gives_the_kind_to_the_item_and_to_a_lead_ticket_that_has_none(self):
        self.open()
        self.rail_before_kinds("a")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "upkeep"), "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("upkeep", ""))
        self.assertEqual(self.label("rail.tsv", "T1"), ("upkeep", ""))

    def test_fire_with_no_kind_from_the_ticket_or_the_flag_gives_the_item_and_the_lead_ticket_the_kind_of_its_station(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "9")
        self.rail_before_kinds("a", "b", "c")
        for number, (station, kind) in enumerate((("bug-fix", "fix"), ("perf-issue", "fix"), ("investigation", "study")), 1):
            with self.subTest(station=station):
                self.assertEqual(self.brigade("fire", "--tickets", f"T{number}", "--station", station, "--summary", f"Do {number}"),
                                 f"D{number}")
                self.assertEqual(self.label("dishes.tsv", f"D{number}"), (kind, ""))
                self.assertEqual(self.label("rail.tsv", f"T{number}"), (kind, ""))

    def test_fire_takes_the_lead_tickets_kind_over_the_kind_of_its_station(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "upkeep")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("upkeep", ""), ("upkeep", "")))

    def test_fire_kind_is_taken_over_the_kind_of_the_station(self):
        self.open()
        self.rail_before_kinds("a")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a", "--kind", "polish")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("polish", ""), ("polish", "")))

    def test_fire_of_a_lead_ticket_with_no_kind_on_a_station_that_gives_none_is_refused_and_changes_no_file(self):
        self.open()
        self.rail_before_kinds("a")
        before = self.store_files()
        for station in ("feature", "refactoring", "docs"):
            with self.subTest(station=station):
                self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", station, "--summary", "Do a", ok=False),
                                 f"brigade: T1 has no kind and station {station} gives none; pass --kind {self.KIND_CHOICE}")
        self.assertEqual(self.store_files(), before)

    def test_fire_with_paths_of_a_lead_ticket_with_no_kind_is_refused_before_it_claims_a_lease_and_changes_no_file(self):
        case = self.fake_land()
        (case / "claim-proceed").touch()
        self.open()
        self.rail_before_kinds("a")
        before = self.store_files()
        proc = self.fire_with_fake_land(case, "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--paths", "src")
        _, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()),
                         (1, f"brigade: T1 has no kind and station feature gives none; pass --kind {self.KIND_CHOICE}"))
        calls = (case / "land-calls").read_text().splitlines() if (case / "land-calls").exists() else []
        self.assertFalse(any(line.startswith("lease claim") for line in calls))
        self.assertEqual(self.store_files(), before)

    def test_fire_with_no_ticket_needs_a_kind_from_the_flag_or_the_station(self):
        self.open()
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", ",", "--station", "feature", "--summary", "No ticket", ok=False),
                         "brigade: no ticket gives this item a kind and station feature gives none; "
                         f"pass --kind {self.KIND_CHOICE}")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("fire", "--tickets", ",", "--station", "feature", "--summary", "No ticket", "--kind", "study"), "D1")
        self.assertEqual(self.brigade("fire", "--tickets", ",", "--station", "bug-fix", "--summary", "No ticket"), "D2")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("dishes.tsv", "D2")), (("study", ""), ("fix", "")))

    def test_fire_kind_that_differs_from_the_lead_tickets_kind_is_refused_and_an_equal_one_is_accepted(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "feature", ok=False),
                         "brigade: T1 has kind fix, and --kind feature does not match it; "
                         "drop --kind, or run ticket set T1 --kind feature first when no item in flight names T1")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "fix"), "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("fix", ""))

    def test_fire_kind_refuses_a_word_that_is_none_of_the_five(self):
        self.open()
        self.rail_before_kinds("a")
        before = self.store_files()
        self.assert_refused_choice(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "chore",
                                                ok=False), "--kind", "chore", ("feature", "fix", "polish", "upkeep", "study"))
        self.assertEqual(self.store_files(), before)

    def test_fire_refuses_a_ticket_after_the_lead_that_is_under_another_goal_and_takes_it_as_a_ride_along(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2", "--priority", "low")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both", ok=False),
                         "brigade: T2 is under G2 and the lead ticket T1 is under G1; "
                         "name T2 in --ride-along, which takes a low ticket that is not a feature, or start it on its own")
        self.assertEqual(self.store_files(), before)
        rail = self.table_row(self.at, "rail.tsv", "T2")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "Do both"),
                         "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("fix", "G1"))
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2"), {**rail, "state": "assigned", "dish": "D1"})
        self.assertEqual(self.label("rail.tsv", "T2"), ("polish", "G2"))

    def test_fire_refuses_a_ticket_under_a_goal_after_an_unassigned_lead_ticket_and_takes_it_as_a_ride_along(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G1", "--priority", "low")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both", ok=False),
                         "brigade: T2 is under G1 and the lead ticket T1 is unassigned; "
                         "name T2 in --ride-along, which takes a low ticket that is not a feature, or start it on its own")
        self.assertEqual(self.store_files(), before)
        self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "Do both")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T2")), (("fix", ""), ("polish", "G1")))

    def test_fire_takes_a_ticket_of_the_lead_tickets_goal_and_an_unassigned_ticket_with_no_ride_along_and_each_keeps_its_two_cells(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "feature", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "polish")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2,T3", "--station", "bug-fix", "--summary", "Do all"), "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("fix", "G1"))
        self.assertEqual([self.label("rail.tsv", ident) for ident in ("T1", "T2", "T3")],
                         [("fix", "G1"), ("feature", "G1"), ("polish", "")])

    def test_fire_refuses_a_ride_along_id_that_is_not_in_tickets_and_one_that_is_the_lead_ticket(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1", "--priority", "low")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2", "--priority", "low")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x", ok=False),
                         "brigade: --ride-along names T2, which is not in --tickets; add it there or drop it")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T1,T2", "--station", "bug-fix", "--summary", "x",
                                      ok=False),
                         "brigade: T1 is the lead ticket, and the lead ticket cannot ride along; "
                         "put the ticket whose goal the item takes first in --tickets")
        self.assertEqual(self.store_files(), before)

    def test_fire_refuses_a_ride_along_ticket_of_priority_normal_or_urgent(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "polish", "--goal", "G2", "--priority", "urgent")
        before = self.store_files()
        for ident, priority in (("T2", "normal"), ("T3", "urgent")):
            with self.subTest(ident=ident):
                self.assertEqual(self.brigade("fire", "--tickets", f"T1,{ident}", "--ride-along", ident, "--station", "bug-fix",
                                              "--summary", "x", ok=False),
                                 f"brigade: {ident} is {priority}; only a low ticket rides along; "
                                 f"start it on its own, or run ticket set {ident} --priority low")
        self.assertEqual(self.store_files(), before)
        self.brigade("ticket", "set", "T2", "--priority", "low")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x"), "D1")

    def test_fire_takes_a_report_ticket_with_an_empty_priority_cell_as_a_ride_along(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.follow_ups_in("## Follow-ups\n\n- Tidy the guide.\n")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2")["priority"], "")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "fix", "--goal", "G2")
        self.assertEqual(self.brigade("fire", "--tickets", "T3,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x"), "D2")
        self.assertEqual((self.label("dishes.tsv", "D2"), self.label("rail.tsv", "T2")), (("fix", "G2"), ("polish", "G1")))

    def test_fire_refuses_a_ride_along_ticket_that_is_a_feature(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "feature", "--goal", "G2", "--priority", "low")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x",
                                      ok=False),
                         "brigade: T2 is a feature, and a feature does not ride along; start it as its own item")
        self.assertEqual(self.store_files(), before)

    def test_fire_refuses_a_low_feature_named_in_ride_along_that_is_under_the_lead_tickets_goal(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "feature", "--goal", "G1", "--priority", "low")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x",
                                      ok=False),
                         "brigade: T2 is a feature, and a feature does not ride along; start it as its own item")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "x"), "D1")

    def test_fire_of_a_lead_ticket_whose_kind_cell_holds_no_kind_warns_needs_a_kind_and_writes_it_over_the_cell(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.hand_edit("rail.tsv", "T1", kind="bugfix")
        warning = ("brigade: warning: T1 has kind 'bugfix', which is not feature, fix, polish, upkeep, or study; it reads as no kind; "
                   "run ticket set T1 --kind <kind>\n")
        before = self.store_files()
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a"),
                         (1, "", warning + f"brigade: T1 has no kind and station feature gives none; "
                                           f"pass --kind {self.KIND_CHOICE}\n"))
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "polish"),
                         (0, "D1\n", warning))
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("polish", ""), ("polish", "")))

    def test_fire_of_a_lead_ticket_whose_kind_cell_holds_no_kind_writes_the_kind_of_its_station_over_the_cell(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.hand_edit("rail.tsv", "T1", kind="bugfix")
        code, out, err = self.run_raw("fire", "--tickets", "T1", "--station", "investigation", "--summary", "Do a")
        self.assertEqual((code, out), (0, "D1\n"))
        self.assertIn("T1 has kind 'bugfix'", err)
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("study", ""), ("study", "")))

    def test_fire_of_a_lead_ticket_whose_goal_has_no_row_warns_starts_an_unassigned_item_and_leaves_the_tickets_cell(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G2")
        self.hand_edit("rail.tsv", "T1", goal="G9")
        self.hand_edit("rail.tsv", "T2", goal="G8")
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"),
                         (0, "D1\n", "brigade: warning: T1 names goal 'G9', which has no row in goals.tsv; it reads as unassigned; "
                                     "run ticket set T1 --goal G<n> or --no-goal\n"))
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("fix", ""), ("fix", "G9")))

    def test_fire_warns_of_a_bad_cell_on_a_ticket_after_the_lead_and_reads_that_ticket_as_unassigned(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G2")
        self.hand_edit("rail.tsv", "T2", goal="G9")
        self.assertEqual(self.run_raw("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both"),
                         (0, "D1\n", "brigade: warning: T2 names goal 'G9', which has no row in goals.tsv; it reads as unassigned; "
                                     "run ticket set T2 --goal G<n> or --no-goal\n"))
        self.assertEqual(self.label("rail.tsv", "T2"), ("fix", "G9"))

    def test_fire_of_a_ticket_with_no_kind_prints_the_owner_refusal_and_the_not_waiting_refusal_first(self):
        self.open()
        self.rail_before_kinds("a", "b")
        self.brigade("ticket", "set", "T2", "--state", "done")
        self.brigade("set", "--thread", "t1")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "x", ok=False, owner=False),
                         "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status")
        self.assertEqual(self.brigade("--owner", "t0@1", "fire", "--tickets", "T1", "--station", "feature", "--summary", "x", ok=False),
                         "brigade: owner t0@1 is stale; this store is owned by t1@1")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "feature", "--summary", "x", ok=False),
                         "brigade: T2 is done, not waiting")
        self.assertEqual(self.store_files(), before)

    def test_fire_of_a_ticket_with_no_kind_on_paths_another_lease_holds_is_refused_for_the_kind_and_logs_no_block(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.rail_before_kinds("a")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            leases, before = self.land("lease", "list"), self.store_files()
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--paths", "src",
                                          ok=False),
                             f"brigade: T1 has no kind and station feature gives none; pass --kind {self.KIND_CHOICE}")
            self.assertEqual((self.land("lease", "list"), self.store_files()), (leases, before))
            refused = self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--paths", "src",
                                   "--kind", "upkeep", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by engine/D1", refused)
            self.assertEqual(self.blocked_note(self.at, "T1"),
                             {"kind": "lease", "tickets": ["T1"], "station": "feature", "summary": "Do a", "paths": "src",
                              "timebox": 60, "work": "upkeep"})

    def test_a_blocked_fire_with_no_kind_flag_and_no_ride_along_writes_the_note_keys_it_wrote_before_kinds(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Do b", "--branch", "docs/gate", ok=False)
        self.assertEqual(self.blocked_note(self.at, "T2"),
                         {"kind": "workers", "tickets": ["T2"], "station": "bug-fix", "summary": "Do b", "paths": "", "timebox": 60,
                          "branch": "docs/gate"})

    def test_a_dish_update_and_a_review_pass_leave_the_items_kind_and_goal_cells_as_fire_wrote_them(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc", "--thread", "w1")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("feature", "G1"), ("feature", "G1")))
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "merged")

    def test_a_dropped_items_lead_ticket_waits_again_with_the_kind_fire_wrote_on_it(self):
        self.open()
        self.rail_before_kinds("a")
        self.brigade("fire", "--tickets", "T1", "--station", "investigation", "--summary", "Look at a")
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
        row = self.table_row(self.at, "rail.tsv", "T1")
        self.assertEqual((row["state"], row["kind"], row["goal"]), ("waiting", "study", ""))
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a"), "D2")
        self.assertEqual(self.label("dishes.tsv", "D2"), ("study", ""))

    def test_fire_refuses_a_ticket_of_a_paused_goal_and_starts_no_dish_and_logs_no_block(self):
        self.open()
        self.goal("activity view")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one", "--goal", "G1")
        self.brigade("goal", "set", "G1", "--state", "paused")
        before = self.tables()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix one", ok=False),
                         "brigade: nothing fired: its goal is paused")
        self.assertEqual(self.tables(), before)
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix G1 [user] one")
        self.brigade("goal", "set", "G1", "--state", "open")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix one"), "D1")

    def test_fire_refuses_a_ride_along_ticket_of_a_paused_goal_and_starts_no_item(self):
        # The lead ticket T4 has no goal and the rider T5 is under paused G1, so the fire must read both.
        self.queue()
        self.goal("activity view")
        self.filed("a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a", "--paths", "a.txt")
        self.brigade("set", "--cap", "fix=1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--paths", "b.txt", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "fix", "--paths", "c.txt")
        self.brigade("ticket", "add", "--summary", "Add d", "--kind", "feature", "--paths", "d.txt", "--priority", "urgent")
        self.brigade("ticket", "add", "--summary", "Paused rider", "--kind", "polish", "--paths", "d.txt", "--priority", "low", "--goal", "G1")
        self.brigade("goal", "set", "G1", "--state", "paused")
        before = self.tables()
        self.assertEqual(self.brigade("fire", "--tickets", "T4,T5", "--ride-along", "T5", "--station", "feature", "--summary", "Add d", code=1),
                         "brigade: nothing fired: its goal is paused")
        self.assertEqual(self.tables(), before)
        self.assertEqual(self.brigade("ticket", "list", "--state", "assigned"), "T1 assigned normal fix G1 [user] a")

    def test_fire_refuses_a_second_item_of_a_kind_at_its_cap(self):
        self.open()
        self.brigade("set", "--cap", "fix=1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix one")
        before = self.tables()
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Fix two", ok=False),
                         "brigade: nothing fired: the fix cap holds (1 of 1)")
        self.assertEqual(self.tables(), before)
        self.assertEqual(self.label("dishes.tsv", "D1"), ("fix", ""))

    def test_fire_refuses_an_item_of_a_goal_at_its_share(self):
        self.open()
        self.goal("activity view", "--share", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one", "--goal", "G1")
        self.brigade("ticket", "add", "--kind", "polish", "--summary", "two", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix one")
        before = self.tables()
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "docs", "--summary", "Tidy two", ok=False),
                         "brigade: nothing fired: the G1 share holds (1 of 1)")
        self.assertEqual(self.tables(), before)

    def test_a_fire_refused_by_a_spend_control_releases_no_lease_and_leaves_the_ticket_waiting(self):
        self.queue()
        self.brigade("set", "--cap", "fix=1")
        self.filed("Fix a", "--paths", "a.txt")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a", "--paths", "a.txt")
        self.filed("Fix b", "--paths", "b.txt")
        before = self.land("lease", "list")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Fix b",
                                      "--paths", "b.txt", ok=False),
                         "brigade: nothing fired: the fix cap holds (1 of 1)")
        self.assertEqual(self.land("lease", "list"), before)
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] Fix b")

    def test_goal_set_refuses_done_and_dropped_while_a_ticket_of_the_goal_rides_along_in_another_goals_item(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2", "--priority", "low")
        self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "Do both")
        self.assert_goal_keeps_open("G2", "T2 riding along", "finish or drop the item each of those tickets rides along in")
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1, T2 waiting again")
        self.assert_goal_keeps_open("G2", "T2 waiting", "give each waiting ticket another goal with ticket set --goal, or none with --no-goal; a waiting "
                                    "ticket that an item in flight names keeps its goal until that item is merged or dropped")

    def test_goal_set_names_the_waiting_tickets_the_items_in_flight_and_the_tickets_riding_along_in_one_refusal(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2", "--priority", "low")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "fix", "--goal", "G2")
        self.brigade("ticket", "add", "--summary", "d", "--kind", "fix", "--goal", "G2")
        self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "Do a and b")
        self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "Do c")
        self.assert_goal_keeps_open(
            "G2", "T4 waiting; D2 in flight; T2 riding along",
            "give each waiting ticket another goal with ticket set --goal, or none with --no-goal; a waiting ticket that an item in "
            "flight names keeps its goal until that item is merged or dropped; finish or drop each item in flight; "
            "finish or drop the item each of those tickets rides along in")

    def test_from_report_gives_each_ticket_the_kind_polish_and_the_goal_of_the_reports_item(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G2")
        self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a")
        self.assertEqual(len(self.follow_ups_in(BULLETS).splitlines()), 3)
        self.assertEqual([self.label("rail.tsv", ident) for ident in ("T2", "T3", "T4")], [("polish", "G2")] * 3)

    def test_from_report_on_an_item_with_no_kind_and_no_goal_files_tickets_with_no_kind_and_no_goal(self):
        self.open()
        self.rail_before_kinds("a")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.hand_edit("dishes.tsv", "D1", kind="")
        self.follow_ups_in(BULLETS)
        self.assertEqual([self.label("rail.tsv", ident) for ident in ("T2", "T3", "T4")], [("", "")] * 3)

    def test_close_prints_no_goal_heading_in_a_store_whose_tickets_and_items_have_kinds_and_no_goal(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.assertEqual(self.close_run("--dry-run")[1].splitlines()[4:],
                         ["## In progress", "", "- fix D1 (T1): Fix a", "", "## New tickets, not started", "", "- polish T2: b",
                          "", "## Spend", "", "goal not recorded  wall open  rounds 0  send-backs 0  threads not recorded"])

    def test_an_item_and_an_assigned_ticket_whose_kind_cells_hold_no_kind_are_warned_of_and_are_in_no_count_by_kind(self):
        self.fired_bug_fix()
        self.hand_edit("rail.tsv", "T1", kind="chore")
        self.hand_edit("dishes.tsv", "D1", kind="chore")
        out = self.assert_each_printing_command_warns(
            f"T1 has kind 'chore', {self.NOT_A_KIND}; {self.NOT_WHILE} T1 is assigned; correct it in rail.tsv by hand",
            f"D1 has kind 'chore', {self.NOT_A_KIND}; no command changes this cell; correct it in dishes.tsv by hand")
        self.assertEqual(len(out["status"].splitlines()), 4)
        self.assertEqual(out["ticket list"], "T1 assigned normal [user] s")
        self.assertEqual(out["close --dry-run"].splitlines()[4:], ["## In progress", "", "- D1 (T1): Fix s",
                                                                  "", "## Spend", "",
                                                                  "goal not recorded  wall open  rounds 0  send-backs 0  threads not recorded"])
        self.assertNotIn("chore", "".join(out.values()))

    def test_fire_of_a_ticket_whose_goal_cell_and_whose_goals_tsv_row_id_are_the_same_padded_text_warns_of_both_and_starts_an_unassigned_item(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.hand_edit("goals.tsv", "G1", id=" G1 ")
        self.hand_edit("rail.tsv", "T1", goal=" G1 ")
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"),
                         (0, "D1\n", f"brigade: warning: {self.PADDED_GOAL_ROW}\n"
                                     f"brigade: warning: T1 names goal ' G1 ', {self.NO_GOAL_ROW}; "
                                     "run ticket set T1 --goal G<n> or --no-goal\n"))
        self.assertEqual(self.raw_cells("dishes.tsv", "D1", "kind", "goal"), (b"fix", b""))
        self.assertEqual(self.raw_cells("rail.tsv", "T1", "state", "kind", "goal"), (b"assigned", b"fix", b" G1 "))
        self.assertEqual(self.raw_cells("goals.tsv", " G1 ", "id"), (b" G1 ",))

    def test_ticket_add_goal_ticket_set_goal_and_fire_warn_of_a_goals_tsv_row_whose_id_an_earlier_row_has(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.copy_goal_row("G1", "second")
        warning = ("brigade: warning: goals.tsv has more than one row with id G1; the first is the goal and the others are not read; "
                   "remove the others from goals.tsv by hand\n")
        self.assertEqual(self.run_raw("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1"), (0, "T2\n", warning))
        self.assertEqual(self.run_raw("ticket", "set", "T1", "--goal", "G1"), (0, "T1 goal G1\n", warning))
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"), (0, "D1\n", warning))
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T2")), (("fix", "G1"), ("fix", "G1")))

    def test_a_ticket_and_an_item_that_name_a_goal_with_no_row_are_warned_of_and_count_as_unassigned(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.hand_edit("rail.tsv", "T1", goal="")
        self.hand_edit("rail.tsv", "T2", goal="G9")
        self.hand_edit("dishes.tsv", "D1", goal="G7")
        out = self.assert_each_printing_command_warns(
            f"T2 names goal 'G9', {self.NO_GOAL_ROW}; run ticket set T2 --goal G<n> or --no-goal",
            f"D1 names goal 'G7', {self.NO_GOAL_ROW}; no command changes this cell; correct it in dishes.tsv by hand")
        self.assertEqual(out["status"].splitlines()[3:],
                         ["liveness: every 600000 ms (default for milestones)",
                          "kinds: fix 1 waiting, 1 in flight", "goals: unassigned 1 waiting, 1 in flight"])
        self.assertEqual(out["goal list"].splitlines()[0], "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs")
        self.assertEqual(out["goal list"].splitlines()[-1], "unassigned: 1 waiting, 1 in flight, 0 merged, 0 send-backs")
        self.assertEqual(out["ticket list"], "T1 assigned normal fix [user] a\nT2 waiting normal fix [user] b")
        self.assertEqual(out["startable"].splitlines()[2], "T2 normal fix, 0m: unknown, no paths recorded")
        self.assertEqual(out["close --dry-run"].splitlines()[4:],
                         ["## In progress", "", "- fix D1 (T1): Fix a", "", "## New tickets, not started", "", "- fix T2: b",
                          "", "## Spend", "", "G7  wall open  rounds 0  send-backs 0  threads not recorded"])
        self.assertNotIn("G9", "".join(out.values()))
        before, _, after = out["close --dry-run"].partition("## Spend")
        self.assertNotIn("G7", "".join(value for key, value in out.items() if key != "close --dry-run") + before)
        self.assertIn("G7", after)

    def test_with_goals_tsv_deleted_each_goal_cell_is_warned_of_its_row_counts_as_unassigned_and_no_command_writes_the_table(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        (self.at / "goals.tsv").unlink()
        out = self.assert_each_printing_command_warns(
            f"T1 names goal 'G1', {self.NO_GOAL_ROW}; {self.NOT_WHILE} T1 is assigned; correct it in rail.tsv by hand",
            f"T2 names goal 'G1', {self.NO_GOAL_ROW}; run ticket set T2 --goal G<n> or --no-goal",
            f"D1 names goal 'G1', {self.NO_GOAL_ROW}; no command changes this cell; correct it in dishes.tsv by hand")
        self.assertEqual(out["status"].splitlines()[3:], ["liveness: every 600000 ms (default for milestones)", "kinds: fix 1 waiting, 1 in flight"])
        self.assertEqual(out["goal list"], "no open or paused goals\nunassigned: 1 waiting, 1 in flight, 0 merged, 0 send-backs")
        self.assertEqual(out["ticket list"], "T1 assigned normal fix [user] a\nT2 waiting normal fix [user] b")
        self.assertEqual(out["close --dry-run"].splitlines()[4:],
                         ["## In progress", "", "- fix D1 (T1): Fix a", "", "## New tickets, not started", "", "- fix T2: b",
                          "", "## Spend", "", "G1  wall open  rounds 0  send-backs 0  threads not recorded"])
        self.assertFalse((self.at / "goals.tsv").exists())

    def test_next_accepts_commands(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        for number in range(3):
            self.brigade("pass", "record", "D1", "--sha", f"s{number}", "--verdict", "send-back",
                         "--author", CLAUDE, "--verifier", CODEX, "--late")
        inbox = self.at / "inbox"
        inbox.mkdir()
        (inbox / "hand.json").write_text(json.dumps(
            {"handoff": "app/other/T1", "summary": "Fix a", "source": "user", "ref": ""}))
        sheet = self.next_sheet("report-back", "D1")
        self.assertEqual(sheet, "\n".join([
            "wake: report-back D1",
            "reporting: milestones",
            "run inbox take",
            "run dish D1 --reported",
            "run 86 add --dish D1 --round-budget",
            "run close",
            "reply: yes (decision: D1)",
        ]))
        self.execute_sheet(sheet)
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        sheet = self.next_sheet("merge", "D1")
        self.assertEqual(sheet, "\n".join([
            "wake: merge D1",
            "reporting: milestones",
            "run inbox take",
            "run dish D1 --state merged",
            "see Finish with the old worker for thread none, branch none, and pull request none for D1. "
            "If its run is still open, interrupt it and hang the attempt, then archive it. "
            "Remove the worktree and delete the branch after that. Do not claim they are already gone.",
            "if the queue is paused and this thread cannot fix it and this is not a bounce handed to a fresh worker and --failure stuck was not passed: "
            "run next --failure stuck",
            "reply: yes (merged: D1)",
        ]))
        self.execute_sheet(sheet)

    def test_next_facts(self):
        self.fired_bug_fix()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        pause = ("if the queue is paused and this thread cannot fix it and this is not a bounce handed to a fresh worker and --failure stuck was not passed: "
                 "run next --failure stuck")
        follow = "if land or watch reports D1 landed: run next merge D1"
        unspecified = self.next_sheet()
        self.assertEqual(unspecified, "\n".join([
            "wake: unspecified", "reporting: milestones", "see Run land for D1.", follow, pause,
            "reply: no (routine: unspecified)"]))
        self.assertNotIn("run dish D1 --state merged", unspecified)
        checks = self.next_sheet("checks", "D1", "--result", "pass")
        self.assertEqual(checks, "\n".join([
            "wake: checks D1", "reporting: milestones", "see Run land for holder perf/D1.", follow, pause,
            "reply: no (routine: pull request)"]))
        self.assertNotIn("run dish D1 --state merged", checks)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "other")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Other")
        review = self.next_sheet("review", "D2", "--verdict", "send-back")
        self.assertIn("see Record pass record for D2 with verdict send-back. No sha is recorded.", review)
        self.assertNotIn("sha abc", review)
        self.assertNotIn("D2 is send-back", review)
        self.assertNotIn("D2 sent-back", review)
        self.assertIn("if the old worker's wait for D2 is terminal and D2 may take a fix round: run dish D2 --state in-progress", review)
        self.assertNotIn("run pass", review)
        self.assertFalse(any(line.startswith("run dish D2") for line in review.splitlines()))
        (self.at / "inbox").mkdir()
        (self.at / "inbox" / "bad.json").write_text("{")
        bad = self.next_sheet()
        self.assertIn("see inbox/bad.json is not valid JSON", bad)
        self.assertNotIn("run inbox take", bad)
        self.assertTrue(any(line.startswith("if ") for line in bad.splitlines()))

    def test_next_replies_yes_when_a_later_occasion_qualifies(self):
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        stuck = self.next_sheet("review", "D1", "--verdict", "send-back", "--failure", "stuck")
        self.assertEqual(stuck.splitlines()[-1], "reply: yes (stuck: stuck)")
        self.assert_one_close_file(stuck)
        unsent = self.next_sheet("review", "D1", "--verdict", "send-back", "--unsent", "report")
        self.assertEqual(unsent.splitlines()[-1], "reply: yes (report: unsent report)")
        self.assert_one_close_file(unsent)
        failed = self.next_sheet("checks", "D1", "--result", "fail", "--unsent", "failed")
        self.assertEqual(failed.splitlines()[-1], "reply: yes (failed: unsent failed)")
        self.assert_one_close_file(failed)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "second")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        merged = self.next_sheet("merge", "D1", "--failure", "stuck")
        self.assertEqual(merged.splitlines()[-1], "reply: yes (stuck: stuck)")
        self.assert_one_close_file(merged)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("set", "--reports-to", "admin-thread")
        quiet = self.next_sheet("review", "D1", "--verdict", "send-back", "--failure", "stuck")
        self.assertEqual(quiet.splitlines()[-1], "reply: no (reports to the executive admin: send-back)")
        routed = self.next_sheet("review", "D1", "--verdict", "send-back", "--unsent", "report")
        self.assertEqual(routed.splitlines()[-1], "reply: yes (report: unsent report)")
        self.assert_one_close_file(routed)
        routed_fail = self.next_sheet("checks", "D1", "--result", "fail", "--unsent", "failed")
        self.assertEqual(routed_fail.splitlines()[-1], "reply: yes (failed: unsent failed)")
        self.assert_one_close_file(routed_fail)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("set", "--reports-to", "admin-thread")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "second")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        admin_merge = self.next_sheet("merge", "D1", "--failure", "stuck")
        self.assertEqual(admin_merge.splitlines()[-1], "reply: no (reports to the executive admin: merge)")

    def test_fire_shared_doc_with_two_tickets_is_refused(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "50")
        self.seed_doc_settings()
        for doc in self.SHARED_DOCS:
            with self.subTest(doc=doc):
                self.brigade("ticket", "add", "--kind", "fix", "--summary", f"First {doc}")
                self.brigade("ticket", "add", "--kind", "fix", "--summary", f"Second {doc}")
                ids = f"T{2 * self.SHARED_DOCS.index(doc) + 1},T{2 * self.SHARED_DOCS.index(doc) + 2}"
                before = self.store_files()
                self.assertEqual(self.brigade("fire", "--tickets", ids, "--station", "bug-fix", "--summary", f"Fix {doc}",
                                              "--paths", doc, "--timebox", "60", ok=False),
                                 self.doc_line((doc,), count=2))
                self.assertEqual(self.store_files(), before)

    def test_fire_shared_doc_with_ride_along_counts_every_ticket(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "50")
        self.seed_doc_settings()
        self.goal("activity view")
        self.goal("current with upstream")
        self.brigade("ticket", "add", "--summary", "Lead work", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "Ride work", "--kind", "polish", "--goal", "G2", "--priority", "low")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix",
                                      "--summary", "Both", "--paths", "docs/guide.md", "--timebox", "60", ok=False),
                         self.doc_line(("docs/guide.md",), count=2))
        self.assertEqual(self.store_files(), before)

    def test_fire_shared_doc_refusal_order(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.seed_doc_settings()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "First")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Second")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Third")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "First"), "D1")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Again",
                                      "--paths", "docs/guide.md", "--timebox", "120", ok=False),
                         "brigade: T1 is assigned, not waiting")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second",
                                      "--paths", "docs/guide.md", "--timebox", "120", ok=False),
                         self.doc_line(("docs/guide.md",), timebox=120))
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second",
                                      "--paths", "docs/guide.md", "--timebox", "120", "--kind", "feature", ok=False),
                         self.doc_line(("docs/guide.md",), timebox=120))
        self.assertEqual(self.store_files(), before)

    def test_fire_shared_doc_refusal_precedes_no_kind_before_a_lease(self):
        case = self.fake_land()
        (case / "claim-proceed").touch()
        self.open()
        self.seed_doc_settings()
        self.rail_before_kinds("a")
        before = self.store_files()
        proc = self.fire_with_fake_land(case, "--tickets", "T1", "--station", "feature", "--summary", "Do a",
                                        "--paths", "docs/guide.md", "--timebox", "120")
        _, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()),
                         (1, self.doc_line(("docs/guide.md",), timebox=120)))
        self.assertFalse((case / "land-calls").exists())
        self.assertEqual(self.store_files(), before)

    def test_fire_shared_doc_wins_over_a_lease_clash(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.seed_doc_settings()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long doc work")
            self.land("lease", "claim", "--holder", "engine/D9", "--paths", "docs/guide.md")
            leases = self.land("lease", "list")
            before = self.store_files()
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                          "--paths", "docs/guide.md", "--timebox", "120", ok=False),
                             self.doc_line(("docs/guide.md",), timebox=120))
            self.assertEqual(self.land("lease", "list"), leases)
            self.assertEqual(self.store_files(), before)

    def test_fire_doc_item_kind_mismatch_still_refuses(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a",
                                      "--paths", "docs/guide.md", "--timebox", "120", "--doc-item", "--kind", "feature",
                                      ok=False),
                         "brigade: T1 has kind fix, and --kind feature does not match it; "
                         "drop --kind, or run ticket set T1 --kind feature first when no item in flight names T1")
        self.assertEqual(self.store_files(), before)

    def test_fire_leading_slash_parent_path_refuses_and_changes_no_file(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.seed_doc_settings()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long doc work")
            leases = self.land("lease", "list")
            before = self.store_files()
            log = (self.at / "log.tsv").read_bytes()
            rail = (self.at / "rail.tsv").read_bytes()
            dishes = (self.at / "dishes.tsv").read_bytes()
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                          "--paths", "/../docs/guide.md", "--timebox", "120", ok=False),
                             self.doc_line(("docs/guide.md",), timebox=120))
            self.assertEqual(self.land("lease", "list"), leases)
            self.assertEqual(self.store_files(), before)
            self.assertEqual(((self.at / "log.tsv").read_bytes(), (self.at / "rail.tsv").read_bytes(),
                              (self.at / "dishes.tsv").read_bytes()), (log, rail, dishes))

    def test_lease_parts_match_landing_canonical(self):
        brigade = runpy.run_path(str(SCRIPT))
        land = runpy.run_path(str(ROOT / "t3/added/landing/scripts/land.py"))
        lease_parts = brigade["_lease_parts"]
        canonical = land["canonical"]
        inside = (
            ("/docs/guide.md", "docs/guide.md"),
            ("//docs//guide.md", "docs/guide.md"),
            ("docs/./guide.md", "docs/guide.md"),
            ("docs/a/../guide.md", "docs/guide.md"),
            ("\\docs\\guide.md", "docs/guide.md"),
            ("/../docs/guide.md", "docs/guide.md"),
            ("/t3/added/../added/brigade", "t3/added/brigade"),
            ("t3/added//brigade/", "t3/added/brigade"),
            ("/", ""),
            (".", ""),
            ("/.", ""),
            ("/./docs/guide.md", "docs/guide.md"),
            ("docs//guide.md", "docs/guide.md"),
        )
        for raw, want in inside:
            with self.subTest(raw=raw):
                self.assertEqual(lease_parts(raw), [want])
                self.assertEqual(canonical(raw), want)
        outside = ("../docs/guide.md", "a/../../b", "..", "../")
        for raw in outside:
            with self.subTest(raw=raw):
                self.assertEqual(lease_parts(raw), [])
                with self.assertRaises(land["LandError"]):
                    canonical(raw)

    def test_fire_untracked_skill_file_path_refuses(self):
        self.git_project()
        self.open()
        self.seed_doc_settings()
        for path in ("t3/added/new-skill/SKILL.md", "skills/new-skill/SKILL.md"):
            with self.subTest(path=path):
                (self.project / path).parent.mkdir(parents=True, exist_ok=True)
                (self.project / path).write_text("# Skill\n")
                ident = self.brigade("ticket", "add", "--kind", "fix", "--summary", f"Long work on {path}")
                before = self.store_files()
                self.assertEqual(self.brigade("fire", "--tickets", ident, "--station", "bug-fix", "--summary", "Long",
                                              "--paths", path, "--timebox", "120", ok=False),
                                 self.doc_line((path,), timebox=120))
                self.assertEqual(self.store_files(), before)

    def test_fire_untracked_skill_directory_refuses(self):
        self.git_project()
        self.open()
        self.seed_doc_settings()
        for directory, doc in (("t3/added/new-skill", "t3/added/new-skill/SKILL.md"),
                               ("skills/new-skill", "skills/new-skill/SKILL.md")):
            with self.subTest(directory=directory):
                (self.project / doc).parent.mkdir(parents=True, exist_ok=True)
                (self.project / doc).write_text("# Skill\n")
                ident = self.brigade("ticket", "add", "--kind", "fix", "--summary", f"Long work on {directory}")
                before = self.store_files()
                self.assertEqual(self.brigade("fire", "--tickets", ident, "--station", "bug-fix", "--summary", "Long",
                                              "--paths", directory, "--timebox", "120", ok=False),
                                 self.doc_line((doc,), timebox=120))
                self.assertEqual(self.store_files(), before)

    def test_fire_untracked_nested_repository_directory_refuses(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.seed_doc_settings()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long doc work")
            nested = self.project / "t3/added/new-skill"
            (nested / "SKILL.md").parent.mkdir(parents=True, exist_ok=True)
            (nested / "SKILL.md").write_text("# Skill\n")
            subprocess.run(["git", "init", "-q"], cwd=nested, capture_output=True, check=True)
            leases = self.land("lease", "list")
            before = self.store_files()
            log = (self.at / "log.tsv").read_bytes()
            rail = (self.at / "rail.tsv").read_bytes()
            dishes = (self.at / "dishes.tsv").read_bytes()
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                          "--paths", "t3/added/new-skill", "--timebox", "120", ok=False),
                             self.doc_line(("t3/added/new-skill/SKILL.md",), timebox=120))
            self.assertEqual(self.land("lease", "list"), leases)
            self.assertEqual(self.store_files(), before)
            self.assertEqual(((self.at / "log.tsv").read_bytes(), (self.at / "rail.tsv").read_bytes(),
                              (self.at / "dishes.tsv").read_bytes()), (log, rail, dishes))

    def test_fire_nested_repository_symlink_is_not_followed(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.seed_doc_settings()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long doc work")
            nested = self.project / "t3/added/new-skill"
            (nested / "SKILL.md").parent.mkdir(parents=True, exist_ok=True)
            (nested / "SKILL.md").write_text("# Skill\n")
            subprocess.run(["git", "init", "-q"], cwd=nested, capture_output=True, check=True)
            outside = Path(self.temporary.name) / "outside"
            outside.mkdir()
            (outside / "SKILL.md").write_text("# Outside\n")
            os.symlink(outside, nested / "link")
            before = self.store_files()
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                          "--paths", "t3/added/new-skill", "--timebox", "120", ok=False),
                             self.doc_line(("t3/added/new-skill/SKILL.md",), timebox=120))
            self.assertEqual(self.store_files(), before)

    def test_fire_with_no_shared_docs_set_fires_on_a_shared_path(self):
        case = self.fake_land()
        (case / "claim-proceed").touch()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "First")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Second")
        proc = self.fire_with_fake_land(case, "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Both",
                                        "--paths", "README.md", "--timebox", "120")
        out, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()), (0, ""))
        self.assertTrue(out.strip().startswith("D1"), out)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Third")
        proc = self.fire_with_fake_land(case, "--tickets", "T3", "--station", "bug-fix", "--summary", "Doc",
                                        "--paths", "README.md", "--doc-item")
        out, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()), (0, ""))
        self.assertEqual(self.doc_log_note(self.at, out.strip().split()[0]), "doc-item")

    def test_fire_reads_the_catalog_from_the_store_only(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "First")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Second")
            self.seed_doc_settings(sharedDocs=[{"path": "src/app.py", "kind": "file"}])
            self.assertIn("D1", self.brigade("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Both",
                                             "--paths", "README.md", "--timebox", "120"))
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Third")
            before = self.store_files()
            self.assertEqual(self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "Third",
                                          "--paths", "src/app.py", "--timebox", "120", ok=False),
                             self.doc_line(("src/app.py",), timebox=120))
            self.assertEqual(self.store_files(), before)

    def test_fire_reads_a_file_directory_prefix_and_a_skill_prefix_from_the_store(self):
        self.tracked_project("README.md", "docs/guide.md", "docs/skills.md", "lib/thing/SKILL.md")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long work")
        cases = (
            ([{"path": "docs/", "kind": "file"}], "docs", ("docs/guide.md", "docs/skills.md")),
            ([{"path": "lib/", "kind": "skill"}], "lib/thing", ("lib/thing/SKILL.md",)),
            ([{"path": "lib", "kind": "skill"}], "lib/thing", ("lib/thing/SKILL.md",)),
        )
        for shared, given, held in cases:
            with self.subTest(shared=shared, given=given):
                self.seed_doc_settings(sharedDocs=shared)
                before = self.store_files()
                self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                              "--paths", given, "--timebox", "120", ok=False),
                                 self.doc_line(held, timebox=120))
                self.assertEqual(self.store_files(), before)

    def test_fire_reads_the_doc_timebox_from_the_store(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.seed_doc_settings(sharedDocs=[{"path": "docs/guide.md", "kind": "file"}], docTimebox=30)
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "At the limit")
            self.assertIn("D1", self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "At",
                                             "--paths", "docs/guide.md", "--timebox", "30"))
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Over the limit")
            before = self.store_files()
            self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Over",
                                          "--paths", "docs/guide.md", "--timebox", "31", ok=False),
                             self.doc_line(("docs/guide.md",), timebox=31, minutes=30))
            self.assertEqual(self.store_files(), before)

    def test_a_malformed_shared_docs_refuses_before_any_write(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long doc work")
            cases = (
                ("not a list", "it is not a list"),
                (["not an object"], "an entry is not an object"),
                ([{"path": "a.md", "kind": "file", "extra": 1}], "an entry has unknown key 'extra'"),
                ([{"kind": "file"}], "an entry's 'path' is not a repository path: None"),
                ([{"path": "../a.md", "kind": "file"}], "an entry's 'path' is not a repository path: '../a.md'"),
                ([{"path": "a.md", "kind": "doc"}], "an entry's 'kind' is not file or skill: 'doc'"),
            )
            for bad, why in cases:
                with self.subTest(bad=bad):
                    self.seed_doc_settings(sharedDocs=bad)
                    before = self.store_files()
                    leases = self.land("lease", "list")
                    sentence = f"restaurant.json sharedDocs {bad!r} is invalid: {why}"
                    refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                           "--paths", "docs/guide.md", "--timebox", "120", ok=False)
                    self.assertIn(f"brigade: warning: {sentence}", refused)
                    self.assertIn(f"brigade: {sentence}", refused)
                    self.assertEqual(self.store_files(), before)
                    self.assertEqual(self.land("lease", "list"), leases)

    def test_a_malformed_doc_timebox_refuses_before_any_write(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long doc work")
            for bad in (True, 0, "90", 2.5):
                with self.subTest(bad=bad):
                    self.seed_doc_settings(docTimebox=bad)
                    before = self.store_files()
                    leases = self.land("lease", "list")
                    sentence = (f"restaurant.json docTimebox {bad!r} is invalid: "
                                "it is not a whole number of minutes above zero")
                    refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Long",
                                           "--paths", "docs/guide.md", "--timebox", "120", ok=False)
                    self.assertIn(f"brigade: warning: {sentence}", refused)
                    self.assertIn(f"brigade: {sentence}", refused)
                    self.assertEqual(self.store_files(), before)
                    self.assertEqual(self.land("lease", "list"), leases)

    def test_set_refuses_a_malformed_shared_docs_and_leaves_restaurant_json_unchanged(self):
        self.open()
        self.seed_doc_settings(sharedDocs="not a list")
        before = (self.at / "restaurant.json").read_bytes()
        refused = self.brigade("set", "--reporting", "digest", ok=False)
        self.assertIn("sharedDocs", refused)
        self.assertIn("it is not a list", refused)
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_set_refuses_a_malformed_doc_timebox_and_leaves_restaurant_json_unchanged(self):
        self.open()
        self.seed_doc_settings(docTimebox=0)
        before = (self.at / "restaurant.json").read_bytes()
        refused = self.brigade("set", "--reporting", "digest", ok=False)
        self.assertIn("docTimebox", refused)
        self.assertIn("it is not a whole number of minutes above zero", refused)
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_set_writes_with_valid_shared_docs_and_doc_timebox(self):
        self.open()
        self.seed_doc_settings(sharedDocs=[{"path": "docs/guide.md", "kind": "file"}], docTimebox=30)
        self.brigade("set", "--reporting", "digest")
        meta = json.loads((self.at / "restaurant.json").read_text())
        self.assertEqual(meta["reporting"], "digest")
        self.assertEqual(meta["sharedDocs"], [{"path": "docs/guide.md", "kind": "file"}])
        self.assertEqual(meta["docTimebox"], 30)

    def test_the_skill_documents_the_shared_docs_value_this_suite_seeds(self):
        text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        blocks = [block for block in re.findall(r"```json\n(.*?)```", text, re.S) if "sharedDocs" in block]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(json.loads(blocks[0]), DOC_SETTINGS)

    def test_a_refused_fire_that_records_a_block_records_paths_on_a_ticket_that_has_none(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the gate")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the gate",
                                   "--paths", "src", ok=False)
            self.assertEqual(refused, "brigade: nothing fired: paths overlap L1 held by engine/D1 on src")
            self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["paths"], "src")
            self.assertEqual(len((self.at / "dishes.tsv").read_text().splitlines()), 1)
            self.assertIn("engine/D1", self.land("lease", "list"))
            self.assertEqual(self.brigade("startable"), "\n".join([
                "workers: 0 of 2 running, 2 idle, auto-start: normal",
                "waiting tickets: 1, blocked: 1",
                "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src",
            ]))
            self.assertEqual(self.brigade("ticket", "list"),
                             "T1 waiting normal fix [user] Fix the gate blocked: lease")
            self.assertEqual(self.brigade("watch"), "\n".join([
                "workers: 0 of 2 running, 2 idle, auto-start: normal",
                "T1: waiting on L1 (engine/D1)",
            ]))

    def test_a_fire_keeps_paths_a_ticket_already_records(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("set", "--change-note-dir", "changes")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Keep mine", "--paths", "lib")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Keep mine",
                                   "--paths", "src", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by engine/D1", refused)
            self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["paths"], "lib")
            self.assertEqual(self.blocked_note(self.at, "T1")["paths"], "src")
            self.land("lease", "release", "L1")
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Keep mine",
                                          "--paths", "src"), "D1 (lease L2 held by perf/D1)")
            self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["paths"], "lib")
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["paths"], "src,changes/perf%2Fd1.md")

    def test_a_dropped_item_keeps_the_paths_its_fire_recorded(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("set", "--change-note-dir", "changes")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the gate")
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the gate",
                                          "--paths", "src"), "D1 (lease L1 held by perf/D1)")
            self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["paths"], "src")
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["paths"], "src,changes/perf%2Fd1.md")
            self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
            self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["paths"], "src")
            self.assertEqual(self.brigade("startable"), "\n".join([
                "workers: 0 of 2 running, 2 idle, auto-start: normal",
                "waiting tickets: 1, startable: 1",
                "T1 normal fix, 0m: startable; src",
                "  Fix the gate",
            ]))
            self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the gate")
            self.assertEqual(self.brigade("watch"), "\n".join([
                "workers: 0 of 2 running, 2 idle, auto-start: normal",
                "no work in progress",
            ]))

    def test_a_fire_with_no_paths_leaves_an_empty_paths_cell_empty(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the gate")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the gate")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["paths"], "")
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Wait")
        refused = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Wait", "--paths", "",
                               ok=False)
        self.assertIn("nothing fired: 1 of 1 workers running", refused)
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2")["paths"], "")
        self.assertEqual(self.blocked_note(self.at, "T2")["paths"], "")
