from ..support import CLAUDE, CODEX, Path, ProcessReason, SCRIPT, THREAD_A, THREAD_B, THREAD_C, _owner_words, _race_args, io, mock, os, real_popen, runpy, sys

class DishMixin:
    def test_a_replacement_worker_started_with_its_state_and_thread_together_keeps_its_row_open(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", THREAD_B), "D1 in-progress")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", "stamp"],
                          ["D1", "2", "fix worker", THREAD_B, "", "stamp", ""]])

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

    def test_a_state_flag_that_changes_nothing_leaves_the_worker_row_open(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])

    def test_a_store_that_keeps_changing_refuses_the_brief_after_three_resolutions(self):
        self.coordinator()
        case = Path(self.temporary.name) / "race"
        case.mkdir()
        support = Path(__file__).resolve().parent.parent / "support.py"
        churning = real_popen(
            ProcessReason.PRE_MAIN,
            [sys.executable, str(support), *_race_args(
                "brief-churn", case,
                ["--store", str(self.store), "--at", str(self.at), *_owner_words(self.at), *self.BRIEF])],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        self.assertEqual(self._finish_race(churning), (1, "", "brigade: D1 changed while resolving its mode; run brief again"))
        self.assertFalse((self.at / "briefs/D1.md").exists())

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

    def test_close_accepts_each_shape_of_a_claims_check_line(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        for line in ("Claims check: 14 statements checked, 2 corrected", "## Claims check", "### Claims check",
                     "- Claims check: 3 statements checked", "**Claims check.**", "1. **Claims check**",
                     "  > Claims check: none", "2) `Claims check`", "_Claims check_"):
            self.review_file("D1.md", f"# D1\n\nStatus: done\n\n{line}\n\nMore.\n")
            self.assertEqual(self.close_run("--dry-run")[::2], (0, ""), line)

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

    def test_close_prints_the_claims_check_warning_after_the_warning_for_an_unrecorded_review(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "# D1\n")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.review_file("D1-review-1.md")
        self.assertEqual(self.close_run("--dry-run")[::2],
                         (0, self.no_row("D1-review-1.md") + "\n" + self.no_claims("in-review")))

    def test_close_reads_a_report_that_is_not_utf8_for_its_claims_check_line(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        report = self.review_file("D1.md")
        report.write_bytes(b"# D1 \xff\xfe\n\nStatus: done\n")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_claims("in-review")))
        report.write_bytes(b"# D1 \xff\xfe\n\n## Claims check\n")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))

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

    def test_dish_thread_with_a_state_that_ends_the_attempt_closes_that_worker_row(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_A, "--state", "in-review"), "D1 in-review")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", "stamp"]])

    def test_dish_thread_with_reported_closes_that_worker_row(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_A, "--reported"), "D1 in-progress")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", "stamp"]])

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

    def test_pass_check_fails_at_the_sha_of_a_pass_with_edits_row(self):
        self.fired_bug_fix()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.record_edits("abc", "--edits", "D1-review-1.md", "--note", "two wording edits")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False),
                         "brigade: D1 at abc: pass-with-edits (two wording edits)")
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued", ok=False),
                         "brigade: only reviewed work lands: D1 at abc: pass-with-edits (two wording edits)")

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

    def test_the_first_dish_thread_writes_the_worker_row_open_at_attempt_one(self):
        self.fired_bug_fix()
        self.assertEqual((self.at / "threads.tsv").read_text(),
                         "at\tdish\tattempt\trole\tthread\tseat\tstart\tend\n")
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_A), "D1 in-progress")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], THREAD_A)

    def test_the_same_dish_thread_command_twice_adds_one_row(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        after = (self.at / "threads.tsv").read_bytes()
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_A), "D1 in-progress")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])
        self.assertEqual((self.at / "threads.tsv").read_bytes(), after)
