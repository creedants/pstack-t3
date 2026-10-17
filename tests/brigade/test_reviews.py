from .support import (
    setUpModule,
    BrigadeTest,
    CLAUDE,
    CODEX,
    HELD_QUESTION,
    ITEM_DECISION,
    Path,
    STILL_OPEN,
    THREAD_A,
    THREAD_B,
    THREAD_C,
    THREAD_D,
    datetime,
    json,
    os,
    shutil,
    subprocess,
    timezone,
    unittest,
)


class ReviewsTest(BrigadeTest):
    def test_pass_refuses_a_verifier_from_the_author_family(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        error = self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "pass",
                             "--author", CLAUDE, "--verifier", "cursor/claude-sonnet-5-5", ok=False)
        self.assertIn("same model family", error)
        self.assertEqual(self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "pass",
                                      "--author", CLAUDE, "--verifier", "cursor/claude-sonnet-5-5", "--same-family"), "D1 passed")
        self.assertIn("same model family", (self.at / "pass.tsv").read_text())

    def test_pass_check_json_prints_a_cross_family_pass(self):
        self.fired_bug_fix()
        self.record("abc", "pass")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"verdict": "pass", "author": CLAUDE, "verifier": CODEX, "note": "", "crossFamily": True})
        self.assertIn('"crossFamily": true', out)
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc"), f"D1 at abc passed review by {CODEX}")

    def test_pass_check_json_exits_1_when_a_send_back_voids_the_pass(self):
        self.fired_bug_fix()
        self.record("abc", "pass")
        self.record("abc", "send-back", "--note", "test asserts the bug")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (1, "brigade: D1 at abc: send-back (test asserts the bug)"))
        self.assertIn('"verdict": "send-back"', out)
        self.assertEqual(json.loads(out)["note"], "test asserts the bug")

    def test_pass_check_json_marks_a_same_family_pass(self):
        self.fired_bug_fix()
        self.record("abc", "pass", "--same-family", verifier="cursor/claude-sonnet-5-5")
        self.record("def", "pass", "--same-family")
        with (self.at / "pass.tsv").open("a") as table:
            table.write(f"2026-10-08T00:00:00+00:00\tD1\t\tghi\tpass\t{CLAUDE}\tcursor/claude-sonnet-5-5\t\n")
        for sha, note in (("abc", "same model family;"), ("def", "same model family;"), ("ghi", "")):
            code, out, err = self.check_json(sha)
            self.assertEqual((code, err), (0, ""))
            self.assertIn('"crossFamily": false', out)
            self.assertEqual(json.loads(out)["note"], note)

    def test_pass_record_refuses_a_bedrock_claude_author_with_a_claude_verifier(self):
        self.assert_one_family(self.BEDROCK, CLAUDE)

    def test_pass_record_refuses_an_underscored_claude_author_with_a_claude_verifier(self):
        self.assert_one_family("anthropic/claude_opus_5", "cursor/claude-sonnet-5-5")

    def test_pass_record_refuses_a_dated_claude_author_with_a_dated_claude_verifier(self):
        self.assert_one_family("vertex/claude-sonnet-5-5@20260101", "claudeAgent/claude-haiku-5-5-20260301")

    def test_pass_record_refuses_a_namespaced_muse_author_with_a_muse_verifier(self):
        self.assert_one_family(self.NAMESPACED_MUSE, "openrouter/muse-lite-2")

    def test_pass_record_records_two_families_with_no_flag(self):
        self.fired_bug_fix()
        for sha, author, verifier in (("abc", CLAUDE, CODEX), ("def", self.NAMESPACED_MUSE, CLAUDE)):
            self.assertEqual(self.brigade("pass", "record", "D1", "--sha", sha, "--verdict", "pass",
                                          "--author", author, "--verifier", verifier), "D1 passed")
        self.assertEqual([row[5:8] for row in self.pass_rows()], [[CLAUDE, CODEX, ""], [self.NAMESPACED_MUSE, CLAUDE, ""]])

    def test_pass_check_json_reads_a_stored_bedrock_row_as_one_family(self):
        self.fired_bug_fix()
        with (self.at / "pass.tsv").open("a") as table:
            table.write(f"2026-10-08T00:00:00+00:00\tD1\t\tabc\tpass\t{self.BEDROCK}\t{CLAUDE}\t\n")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (0, ""))
        self.assertIn('"crossFamily": false', out)

    def test_pass_check_json_at_another_sha_has_no_verdict(self):
        self.fired_bug_fix()
        self.record("abc", "pass")
        self.assertEqual(self.check_json("def"), (1, "", "brigade: D1 has no review verdict for def"))

    def test_a_pass_table_holds_rows_written_before_and_after_the_report_and_member_columns(self):
        self.fired_bug_fix()
        stamp = "2026-10-08T00:00:00+00:00"
        old = f"{stamp}\tD1\t\tabc\tpass\t{CLAUDE}\t{CODEX}\told row"
        table = self.at / "pass.tsv"
        table.write_text("at\tdish\tpr\tsha\tverdict\tauthor\tverifier\tnote\n" + old + "\n"
                         + f"{stamp}\tD1\t\tdef\tsend-back\t{CLAUDE}\t{CODEX}\tnew row\tD1-review-2.md\t\n")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc"), f"D1 at abc passed review by {CODEX}")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "def", ok=False),
                         "brigade: D1 at def: send-back (new row)")
        self.record("ghi", "pass")
        self.assertEqual(table.read_text().splitlines()[1], old)
        self.assertEqual(self.pass_rows()[2][3:], ["ghi", "pass", CLAUDE, CODEX, "", "", "", ""])
        for fields, line in ((old.split("\t")[:7], 5), ([*old.split("\t"), "r.md", "", "extra", "extra"], 5)):
            table.write_text(table.read_text() + "\t".join(fields) + "\n")
            self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False),
                             f"brigade: bridge-kit/perf/pass.tsv line {line} is malformed; fix or remove it")
            table.write_text("\n".join(table.read_text().splitlines()[:-1]) + "\n")

    def test_pass_record_stores_the_bare_name_of_an_existing_review_report(self):
        self.fired_bug_fix()
        path = self.review_file("D1-review-1.md")
        self.review_file("D1-review.md")
        self.review_file("D1-review-2-panel-1.md")
        self.record("abc", "send-back", "--report", str(path))
        self.record("abc", "send-back", "--report", "reports/D1-review.md")
        self.record("abc", "send-back", "--report", "D1-review-2-panel-1.md")
        self.assertEqual([row[8] for row in self.pass_rows()], ["D1-review-1.md", "D1-review.md", "D1-review-2-panel-1.md"])
        before = (self.at / "pass.tsv").read_bytes()
        record = ("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.brigade(*record, "--report", "D1-review-9.md", ok=False),
                         "brigade: reports/D1-review-9.md does not exist; write the review report first")
        self.review_file("D2-review-1.md")
        self.review_file("D1.md")
        self.review_file("D11-review.md")
        for name in ("D2-review-1.md", "D1.md", "D11-review.md"):
            self.assertEqual(self.brigade(*record, "--report", name, ok=False),
                             f"brigade: {name} is not a review report of D1; name a file like reports/D1-review-1.md")
        self.assertEqual((self.at / "pass.tsv").read_bytes(), before)
        self.assertEqual(self.dish_fields("state"), ("sent-back",))

    def test_pass_record_refuses_a_report_path_outside_the_reports_directory_of_the_store(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md")
        elsewhere = Path(self.temporary.name) / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "D1-review-1.md").write_text("another file\n")
        before = (self.at / "pass.tsv").read_bytes()
        record = ("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        for report in (str(elsewhere / "D1-review-1.md"), "no/such/dir/D1-review-1.md"):
            self.assertEqual(self.brigade(*record, "--report", report, ok=False),
                             f"brigade: {report} is outside this store's reports/; name reports/D1-review-1.md")
        self.assertEqual((self.at / "pass.tsv").read_bytes(), before)

    def test_pass_record_refuses_a_directory_a_live_link_a_dangling_link_and_a_file_under_a_linked_reports(self):
        self.fired_bug_fix()
        reports = self.review_file("D1-review-4.md").parent
        outside = Path(self.temporary.name) / "outside"
        (outside / "reports").mkdir(parents=True)
        (outside / "findings.md").write_text("findings\n")
        (outside / "reports" / "D1-review-5.md").write_text("findings\n")
        (reports / "D1-review-1.md").mkdir()
        (reports / "D1-review-2.md").symlink_to(outside / "findings.md")
        (reports / "D1-review-3.md").symlink_to(outside / "gone.md")
        self.record("abc", "send-back", "--report", "D1-review-4.md")
        before = (self.at / "pass.tsv").read_bytes()
        record = ("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.brigade(*record, "--report", "D1-review-1.md", ok=False),
                         "brigade: reports/D1-review-1.md is not a regular file; nothing recorded")
        self.assertEqual(self.brigade(*record, "--report", "D1-review-2.md", ok=False),
                         "brigade: reports/D1-review-2.md is a symbolic link; nothing recorded")
        self.assertEqual(self.brigade(*record, "--report", "D1-review-3.md", ok=False),
                         "brigade: reports/D1-review-3.md is a symbolic link; nothing recorded")
        reports.rename(self.at / "kept")
        reports.symlink_to(outside / "reports")
        self.assertEqual(self.brigade(*record, "--report", "D1-review-5.md", ok=False),
                         "brigade: reports/D1-review-5.md resolves outside this store's reports/; nothing recorded")
        self.assertEqual((self.at / "pass.tsv").read_bytes(), before)
        self.assertEqual([row[8] for row in self.pass_rows()], ["D1-review-4.md"])

    def test_a_member_send_back_recorded_before_the_passing_rows_changes_no_verdict(self):
        self.fired_bug_fix()
        self.assertEqual(self.brigade("pass", "record", "D1", "--pr", "u/9", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", "grok/grok-4.7", "--note", "nit", "--member"),
                         "D1: member send-back on record; D1 stays in-progress")
        self.assertEqual(self.dish_fields("state", "pr", "sha"), ("in-progress", "", ""))
        self.assertEqual(self.check_json("abc"), (1, "", "brigade: D1 has no review verdict for abc"))
        self.record("abc", "pass")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc"), f"D1 at abc passed review by {CODEX}")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"verdict": "pass", "author": CLAUDE, "verifier": CODEX, "note": "", "crossFamily": True})
        self.assertEqual([row[4:] for row in self.pass_rows()],
                         [["send-back", CLAUDE, "grok/grok-4.7", "nit", "", "yes", ""], ["pass", CLAUDE, CODEX, "", "", "", ""]])

    def test_a_member_row_follows_the_family_rule_and_takes_no_late_flag(self):
        self.fired_bug_fix()
        member = ("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--author", CLAUDE, "--member")
        self.assertIn("same model family", self.brigade(*member, "--verifier", "cursor/claude-sonnet-5-5", ok=False))
        self.assertIn("not allowed with argument", self.brigade(*member, "--verifier", CODEX, "--late", ok=False))
        self.assertEqual(self.pass_rows(), [])

    def test_pass_record_for_an_unknown_item_leaves_no_row(self):
        self.fired_bug_fix()
        for flags in ((), ("--member",), ("--late",)):
            self.assertEqual(self.brigade("pass", "record", "D9", "--sha", "abc", "--verdict", "pass",
                                          "--author", CLAUDE, "--verifier", CODEX, *flags, ok=False),
                             "brigade: no D9 in dishes.tsv")
        self.assertEqual(self.pass_rows(), [])

    def test_pass_record_writes_the_reviewer_row_and_child_rows_with_the_thread_and_the_report(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n")
        self.record("abc", "pass")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])
        self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                  f"- code delegate `{THREAD_B}` opencode-go/step-5-preview-free\n"
                                  f"- claims check `{THREAD_C}` {CODEX}\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "def", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, "--reviewer-thread", THREAD_D),
                         "D1 sent-back")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""],
                          ["D1", "1", "reviewer", THREAD_D, CODEX, "", "stamp"],
                          ["D1", "1", "code delegate", THREAD_B, "opencode-go/step-5-preview-free", "", "stamp"],
                          ["D1", "1", "claims check", THREAD_C, CODEX, "", "stamp"]])
        after = (self.at / "threads.tsv").read_bytes()
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "def", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, "--reviewer-thread", THREAD_D,
                                      "--same-family"), "D1 sent-back")
        self.assertEqual((self.at / "threads.tsv").read_bytes(), after)

    def test_pass_record_reads_a_report_with_a_threads_section_with_none_and_with_one_unreadable_line(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n")
        self.record("abc", "pass")
        self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                  f"- claims check `{THREAD_B}` grok/grok-4.7\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "def", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX), "D1 sent-back")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""],
                          ["D1", "1", "claims check", THREAD_B, "grok/grok-4.7", "", "stamp"]])
        self.review_file("D1.md", "findings\n\n## Threads\n\n- claims check\n")
        before = self.store_files()
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "ghi", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, ok=False),
                         "brigade: D1: reports/D1.md cannot be read; line 5 is not one list item of the form "
                         "- <role> `<thread id>` <seat>; nothing recorded")
        self.assertEqual(self.store_files(), before)
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""],
                          ["D1", "1", "claims check", THREAD_B, "grok/grok-4.7", "", "stamp"]])

    def test_a_padded_report_thread_id_is_refused_by_its_number_and_a_rerun_adds_no_row(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n")
        self.record("abc", "pass")
        self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                  f"- claims check ` {THREAD_B} ` grok/grok-4.7\n")
        before = self.store_files()
        for _ in range(2):
            self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "def", "--verdict", "send-back",
                                          "--author", CLAUDE, "--verifier", CODEX, ok=False),
                             "brigade: D1: reports/D1.md cannot be read; line 5 holds a thread id that the "
                             "table would change; nothing recorded")
            self.assertEqual(self.store_files(), before)
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])

    def test_a_whitespace_only_report_thread_id_is_refused_and_writes_no_thread_cell(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "findings\n\n## Threads\n\n- claims check ` ` grok/grok-4.7\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, ok=False),
                         "brigade: D1: reports/D1.md cannot be read; line 5 holds a thread id that the "
                         "table would change; nothing recorded")
        self.assertEqual((self.at / "threads.tsv").read_text(),
                         "at\tdish\tattempt\trole\tthread\tseat\tstart\tend\n")

    def test_a_longer_outer_fence_holding_shorter_fences_adds_no_child_row(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                  "````markdown\n"
                                  "```text\n"
                                  f"- claims check `{THREAD_B}` grok/grok-4.7\n"
                                  "```\n"
                                  "````\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX), "D1 sent-back")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])

    def test_mixed_backtick_and_tilde_fences_add_no_child_row(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                  "```text\n"
                                  "~~~\n"
                                  f"- claims check `{THREAD_B}` grok/grok-4.7\n"
                                  "~~~\n"
                                  "```\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX), "D1 sent-back")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])

    def test_a_threads_example_in_a_fence_indented_three_spaces_adds_no_child_row(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n\n"
                                  "   ```markdown\n"
                                  "## Threads\n"
                                  f"- claims check `{THREAD_B}` grok/grok-4.7\n"
                                  "## Other example section\n"
                                  "   ```\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass",
                                      "--author", CLAUDE, "--verifier", CODEX), "D1 passed")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])

    def test_a_threads_example_in_a_fence_indented_four_spaces_is_not_a_fence_so_it_adds_a_child_row(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n\n"
                                  "    ```markdown\n"
                                  "## Threads\n"
                                  f"- claims check `{THREAD_B}` grok/grok-4.7\n"
                                  "## Other example section\n"
                                  "    ```\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass",
                                      "--author", CLAUDE, "--verifier", CODEX), "D1 passed")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""],
                          ["D1", "1", "claims check", THREAD_B, "grok/grok-4.7", "", "stamp"]])

    def test_a_role_the_report_cannot_hold_is_refused_by_its_number_and_name(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", f"findings\n\n## Threads\n\n- fixer `{THREAD_B}` grok/grok-4.7\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, ok=False),
                         "brigade: D1: reports/D1.md cannot be read; line 5 holds role 'fixer', which is not "
                         "code delegate, claims check, or architect runner; nothing recorded")
        for role, writer in (("fix worker", "dish <item> --thread <id>"), ("reviewer", "pass record --reviewer-thread <id>"),
                             ("panel member", "thread <dish> --role \"panel member\"")):
            with self.subTest(role=role):
                self.review_file("D1.md", f"findings\n\n## Threads\n\n- {role} `{THREAD_B}` grok/grok-4.7\n")
                self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                              "--author", CLAUDE, "--verifier", CODEX, ok=False),
                                 f"brigade: D1: reports/D1.md cannot be read; line 5 holds role '{role}', "
                                 f"which {writer} writes; nothing recorded")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])

    def test_a_refused_thread_write_changes_no_file_in_the_store(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n\n## Threads\n\n- claims check\n")
        before = self.store_files()
        member = ("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--author", CLAUDE, "--member")
        for extra in (("--reviewer-thread", THREAD_B),):
            self.assertEqual(self.brigade(*member, "--verifier", CODEX, *extra, ok=False),
                             "brigade: --member takes no --reviewer-thread; record a panel member's row with "
                             f"thread D1 --role \"panel member\" --thread <id> --seat <provider/model>")
        self.assert_refused_choice(self.brigade("thread", "D1", "--role", "worker", "--thread", THREAD_C,
                                               "--seat", "grok/grok-4.7", ok=False),
                                   "--role", "worker", ("panel member",))
        self.assert_refused_choice(self.brigade("thread", "D1", "--role", "architect runner", "--thread", THREAD_C,
                                               "--seat", "grok/grok-4.7", ok=False),
                                   "--role", "architect runner", ("panel member",))
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "def", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, ok=False),
                         "brigade: D1: reports/D1.md cannot be read; line 5 is not one list item of the form "
                         "- <role> `<thread id>` <seat>; nothing recorded")
        self.assertEqual(self.store_files(), before)
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])

    def test_a_store_with_no_threads_table_runs_status_watch_dish_and_pass_record_unchanged(self):
        self.fired_bug_fix()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "next")
        (self.at / "threads.tsv").unlink()
        files = sorted(self.store_files())
        self.assertEqual(self.brigade("status").splitlines()[0], "thread not recorded")
        self.assertEqual(self.brigade("watch"),
                         "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (no worker recorded)")
        self.assertEqual(self.brigade("startable").splitlines()[:2],
                         ["workers: 1 of 2 running, 1 idle, auto-start: normal", "waiting tickets: 1, unknown: 1"])
        self.assertEqual(self.brigade("dish", "D1"), "D1 in-progress")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass",
                                      "--author", CLAUDE, "--verifier", CODEX), "D1 passed")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc"), f"D1 at abc passed review by {CODEX}")
        self.assertFalse((self.at / "threads.tsv").exists())
        self.assertEqual(sorted(self.store_files()), files)

    def test_the_thread_command_records_a_panel_member_and_a_report_records_an_architect_runner_and_a_rerun_adds_nothing(self):
        self.fired_bug_fix()
        self.assertEqual(self.brigade("thread", "D1", "--role", "panel member", "--thread", THREAD_A,
                                      "--seat", "grok/grok-4.7"), "D1: panel member on record")
        self.review_file("D1.md", f"findings\n\n## Threads\n\n- architect runner `{THREAD_B}` "
                                  "opencode-go/muse-spark-1.3-contributor\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX),
                         "D1 sent-back")
        after = (self.at / "threads.tsv").read_bytes()
        self.assertEqual(self.brigade("thread", "D1", "--role", "panel member", "--thread", THREAD_A,
                                      "--seat", "grok/grok-4.7"), "D1: panel member already on record")
        self.assertEqual((self.at / "threads.tsv").read_bytes(), after)
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "panel member", THREAD_A, "grok/grok-4.7", "", "stamp"],
                          ["D1", "1", "architect runner", THREAD_B,
                           "opencode-go/muse-spark-1.3-contributor", "", "stamp"]])
        self.assertEqual(self.brigade("thread", "D9", "--role", "panel member", "--thread", THREAD_C,
                                      "--seat", "grok/grok-4.7", ok=False), "brigade: no D9 in dishes.tsv")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", "grok/grok-4.7", "--member", "--note", "nit"),
                         "D1: member send-back on record; D1 stays sent-back")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "panel member", THREAD_A, "grok/grok-4.7", "", "stamp"],
                          ["D1", "1", "architect runner", THREAD_B,
                           "opencode-go/muse-spark-1.3-contributor", "", "stamp"]])

    def test_a_member_row_takes_no_reviewer_thread(self):
        self.fired_bug_fix()
        before = self.store_files()
        member = ("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--author", CLAUDE, "--member")
        self.assertEqual(self.brigade(*member, "--verifier", CODEX, "--reviewer-thread", THREAD_A, ok=False),
                         "brigade: --member takes no --reviewer-thread; record a panel member's row with "
                         f"thread D1 --role \"panel member\" --thread <id> --seat <provider/model>")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade(*member, "--verifier", CODEX),
                         "D1: member send-back on record; D1 stays in-progress")
        self.assertEqual(self.thread_rows(), [])

    def test_close_warns_about_a_dangling_link_that_only_a_row_of_another_item_names(self):
        self.fired_bug_fix()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "t")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Fix t")
        self.review_file("D2-review-1.md")
        self.brigade("pass", "record", "D2", "--sha", "abc", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX,
                     "--report", "D2-review-1.md")
        table = self.at / "pass.tsv"
        table.write_text(table.read_text().replace("D2-review-1.md", "D1-review-7.md"))
        (self.at / "reports" / "D2-review-1.md").unlink()
        self.assertEqual([(row[1], row[8]) for row in self.pass_rows()], [("D2", "D1-review-7.md")])
        self.linked("D1-review-7.md", "gone.md")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.a_link("D1-review-7.md")))

    def test_86_add_round_budget_is_refused_below_three_send_backs(self):
        self.started()
        table = (self.at / "86.tsv").read_bytes()
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 0 send-backs since its last pass or round decision; the decision opens at 3")
        self.sent_back(1)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 1 send-back since its last pass or round decision; the decision opens at 3")
        self.assertEqual(self.brigade("86", "add", "--dish", "D9", "--round-budget", ok=False), "brigade: no D9 in dishes.tsv")
        self.assertEqual((self.at / "86.tsv").read_bytes(), table)

    def test_86_add_takes_a_question_or_the_round_budget(self):
        self.started()
        self.sent_back(3)
        needs = "brigade: 86 add needs --question, --options, and --default"
        self.assertEqual(self.brigade("86", "add", ok=False), needs)
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?", "--options", "a, b", ok=False), needs)
        alone = "brigade: 86 add --round-budget takes --dish and no --question, --options, or --default"
        self.assertEqual(self.brigade("86", "add", "--round-budget", ok=False), alone)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, "--question", "Move?", ok=False), alone)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, "--default", "drop", ok=False), alone)
        self.assertEqual((self.at / "86.tsv").read_text().splitlines(),
                         ["id\tat\tstate\tdish\tquestion\toptions\tdefault\tanswer\tkind\tanswered"])
        self.assertEqual(self.brigade("86", "add", "--question", "Ship it?", "--options", "yes, no", "--default", "no"), "Q1")
        self.assertEqual(self.park_item(), "Q2")

    def test_the_count_starts_again_after_a_round_decision(self):
        self.started()
        self.sent_back(3)
        self.brigade(*self.ROUND_BUDGET)
        self.brigade("86", "answer", "Q1", "--answer", "redesign")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.sent_back(2, start=4), "D1 sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.send_back("a6"), "D1 sent-back; 3 send-backs, decision pending")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q2")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q2")["question"], self.ROUND_QUESTION)

    def test_a_pass_at_three_send_backs_settles_the_count(self):
        self.started()
        self.assertEqual(self.sent_back(3), "D1 sent-back; 3 send-backs, decision pending")
        self.assertEqual(self.brigade(*self.PASS, "a3"), "D1 passed")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: passed, not submitted")
        self.assertEqual(self.brigade(*self.FIX_BRIEF, ok=False),
                         "brigade: D1 is passed; brief a dish that is in progress or sent back")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("passed"))
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued"), "D1 queued")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("queued"))
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", "bounce-worker"), "D1 in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (thread bounce-worker)")
        self.assertEqual(self.send_back("a4"), "D1 sent-back")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 1 send-back since its last pass or round decision; the decision opens at 3")
        self.assertEqual(self.brigade("86", "list"), "no open decisions")

    def test_a_merged_item_owes_nothing_and_its_ticket_starts_a_new_count(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade(*self.PASS, "a4"), "D1 passed")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "a4", "--verdict", "send-back", "--author", CODEX,
                                      "--verifier", CLAUDE, "--member"),
                         "D1: member send-back on record; D1 stays passed")
        self.assertEqual(self.brigade("dish", "D1", "--state", "merged"), "D1 merged")
        for sha in ("m1", "m2", "m3"):
            self.assertEqual(self.send_back(sha, "--late"), "D1: late send-back on record; D1 stays merged")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("merged"))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--state", "waiting"), "T1 waiting")
        self.assertEqual(self.brigade(*self.FIRE_AGAIN), "D2")
        self.assertEqual(self.brigade("pass", "record", "D2", "--sha", "b1", "--verdict", "send-back", "--author", CODEX,
                                      "--verifier", CLAUDE), "D2 sent-back")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD2: sent back")
        self.assertEqual(self.brigade("86", "list"), "no open decisions")

    def test_a_dropped_item_owes_nothing_and_its_ticket_starts_a_new_count(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("dropped"))
        self.assertEqual(self.brigade(*self.FIRE_AGAIN), "D2")
        self.assertEqual(self.brigade("pass", "record", "D2", "--sha", "b1", "--verdict", "send-back", "--author", CODEX,
                                      "--verifier", CLAUDE), "D2 sent-back")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD2: sent back")
        self.assertEqual(self.brigade("86", "list"), "no open decisions")

    def test_a_dropped_item_put_back_in_progress_owes_its_decision_again(self):
        self.started()
        self.sent_back(3)
        self.brigade("dish", "D1", "--state", "dropped")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.brigade("watch").splitlines()[1], self.PENDING)
        self.assertEqual(self.brigade("dish", "D1", "--thread", "fresh-worker", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade(*self.FIRE_AGAIN, ok=False), self.NO_FIRE)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")

    def test_a_late_send_back_written_before_a_pass_stops_counting_at_the_pass(self):
        self.started()
        self.sent_back(2)
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.send_back("a0", "--late"),
                         "D1: late send-back on record; D1 stays in-progress; 3 send-backs, decision pending")
        self.passed("a3")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: passed, not submitted")
        self.brigade("dish", "D1", "--state", "queued")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.send_back("a4"), "D1 sent-back")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 1 send-back since its last pass or round decision; the decision opens at 3")

    def test_a_detour_through_review_starts_no_fix_round(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review"), "D1 in-review")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        before = self.unchanged()
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", "fresh-worker", ok=False),
                         self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--thread", "fresh-worker", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--task", "fresh-task", ok=False), self.NO_FIX_ROUND)
        self.unchanged(before)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade("86", "list"), "no open decisions")

    def test_late_rows_recorded_while_the_decision_is_open_stop_counting_at_its_answer(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        for count in (4, 5, 6):
            self.assertEqual(self.send_back(f"missed{count}", "--late"),
                             f"D1: late send-back on record; D1 stays sent-back; {count} send-backs, decision pending")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "redesign"), "Q1 answered")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertGreater(row["answered"], self.pass_rows()[-1][0])
        self.assertGreater(row["answered"], row["at"])
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: sent back")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.send_back("missed7", "--late"), "D1: late send-back on record; D1 stays in-progress")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 1 send-back since its last pass or round decision; the decision opens at 3")

    def test_a_decision_table_written_before_the_kind_and_answered_columns_still_reads(self):
        self.started()
        self.assertEqual(self.sent_back(3), "D1 sent-back; 3 send-backs, decision pending")
        stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        old = ["\t".join(["Q1", stamp, "answered", "D1", "Drop this item?", "drop, keep parked", "keep parked", "drop"]),
               "\t".join(["Q2", stamp, "open", "", "Ship it?", "yes, no", "no", ""])]
        table = self.at / "86.tsv"
        table.write_text("id\tat\tstate\tdish\tquestion\toptions\tdefault\tanswer\n" + "\n".join(old) + "\n")
        self.assertEqual(self.brigade("86", "list"), "Q2: Ship it? Options: yes, no. Default: no.")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q3")
        lines = table.read_text().splitlines()
        self.assertEqual(lines[1:3], old)
        self.assertEqual(lines[3].split("\t")[5:], ["known limits, redesign, drop, keep parked", "keep parked", "",
                                                     "round-budget", ""])
        self.assertEqual(self.brigade("86", "answer", "Q3", "--answer", "drop"), "Q3 answered")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        stored = [self.table_row(self.at, "86.tsv", ident) for ident in ("Q1", "Q2", "Q3")]
        self.assertEqual([(row["state"], row["answer"], row["kind"], bool(row["answered"])) for row in stored],
                         [("answered", "drop", "", False), ("open", "", "", False), ("answered", "drop", "round-budget", True)])
        for fields, line in ((old[1].split("\t")[:7], 5), ([*old[1].split("\t"), "", "", "extra"], 5)):
            table.write_text(table.read_text() + "\t".join(fields) + "\n")
            self.assertEqual(self.brigade("86", "list", ok=False),
                             f"brigade: bridge-kit/perf/86.tsv line {line} is malformed; fix or remove it")
            table.write_text("\n".join(table.read_text().splitlines()[:-1]) + "\n")

    def test_an_unrelated_answered_item_decision_lifts_no_refusal(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.park_item(), "Q1")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "moved"), "Q1 answered")
        self.assertNotEqual(self.table_row(self.at, "86.tsv", "Q1")["answered"], "")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q2")

    def test_86_list_names_the_item_and_keeps_a_blank_dish_in_the_old_format(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.assertEqual(self.brigade("86", "add", "--question", "Ship it?", "--options", "yes, no", "--default", "no"), "Q2")
        self.assertEqual(self.brigade("86", "list"), "\n".join([
            f"Q1 for D1: {HELD_QUESTION} Options: moved, keep parked. Default: keep parked.",
            "Q2: Ship it? Options: yes, no. Default: no.",
        ]))

    def test_moved_closes_an_item_decision_and_a_later_answer_writes_nothing(self):
        self.started()
        ordinary = self.brigade("watch")
        self.assertEqual(ordinary, "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (no worker recorded)")
        self.park_item()
        opened = [row for row in self.log_rows() if row["kind"] == "decision"]
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "Moved."), "Q1 answered")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["state"], row["answer"]), ("answered", "moved"))
        answered = [row for row in self.log_rows() if row["kind"] == "decision" and row["state"] == "answered"]
        self.assertEqual(len(answered), 1)
        self.assertEqual(len([row for row in self.log_rows() if row["kind"] == "decision"]), len(opened) + 1)
        self.assertEqual(self.brigade("watch"), ordinary)
        before = self.store_bytes()
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), "Q1 answered")
        self.assertEqual(self.store_bytes(), before)

    def test_a_second_refusal_after_keep_parked_prints_the_open_id(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        table = (self.at / "86.tsv").read_text()
        decisions = [row for row in self.log_rows() if row["kind"] == "decision"]
        other = "D1: cursor could not pass the seat's options: refused again. Move this coordinator to a provider that passes them?"
        self.assertEqual(self.park_item(other), "Q1")
        self.assertEqual((self.at / "86.tsv").read_text(), table)
        self.assertEqual([row for row in self.log_rows() if row["kind"] == "decision"], decisions)

    def test_an_item_decision_needs_a_default_and_a_closing_option(self):
        self.started()
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                      "--options", "keep parked", "--default", "keep parked", ok=False), ITEM_DECISION)
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                      "--options", "moved, keep parked", "--default", "ship it", ok=False), ITEM_DECISION)
        self.assertEqual((self.at / "86.tsv").read_text().splitlines(),
                         ["id\tat\tstate\tdish\tquestion\toptions\tdefault\tanswer\tkind\tanswered"])
        self.assertEqual(self.park_item(), "Q1")
        table = (self.at / "86.tsv").read_text()
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                      "--options", "keep parked", "--default", "keep parked"), "Q1")
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                      "--options", "moved, keep parked", "--default", "ship it"), "Q1")
        self.assertEqual((self.at / "86.tsv").read_text(), table)

    def test_an_item_decision_whose_options_collapse_to_the_default_once_stored_is_refused(self):
        self.started()
        before = tuple((self.at / name).read_bytes() for name in ("86.tsv", "log.tsv"))
        for options in ("keep parked, keep\nparked", "keep parked, keep\tparked", "keep parked, keep\r\nparked"):
            self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                          "--options", options, "--default", "keep parked", ok=False),
                             ITEM_DECISION, repr(options))
            self.assertEqual(tuple((self.at / name).read_bytes() for name in ("86.tsv", "log.tsv")), before,
                             repr(options))

    def test_an_item_decision_stores_its_options_cleaned_and_closes_only_on_the_other_option(self):
        self.started()
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move\tthis?",
                                      "--options", "moved\r\nelsewhere, keep\nparked",
                                      "--default", "keep\tparked"), "Q1")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["question"], row["options"], row["default"]),
                         ("Move this?", "moved elsewhere, keep parked", "keep parked"))
        before = self.store_bytes()
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"),
                         "Q1 still open; D1 stays held until 86 answer Q1 --answer 'moved elsewhere'")
        self.assertEqual(self.store_bytes(), before)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "Moved elsewhere."), "Q1 answered")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["state"], row["answer"]), ("answered", "moved elsewhere"))

    def test_a_15_cell_dishes_row_and_a_10_cell_rail_row_are_read_and_a_read_leaves_both_tables_byte_identical(self):
        self.open()
        self.old_item()
        before = self.tables()
        self.assertEqual(self.brigade("ticket", "list"), "T1 assigned normal [user] Fix login")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")
        self.assertEqual(self.brigade("status").splitlines()[1], "reporting: milestones, no landing contract, in review: 1")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False), "brigade: D1 has no review verdict for abc")
        self.assertEqual(self.tables(), before)

    def test_a_pass_with_edits_row_moves_the_item_to_sent_back_and_names_the_report(self):
        self.fired_bug_fix()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.assertEqual(self.record_edits("abc", "--edits", "reports/D1-review-1.md"), "D1 sent-back")
        self.assertEqual(self.dish_fields("state", "sha"), ("sent-back", "abc"))
        self.assertEqual([row[3:5] + row[8:] for row in self.pass_rows()],
                         [["abc", "pass-with-edits", "D1-review-1.md", "", ""]])

    def test_pass_with_edits_rows_do_not_count_toward_the_round_budget(self):
        self.started()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.sent_back(2)
        self.brigade("dish", "D1", "--state", "in-progress")
        for sha in ("b1", "b2"):
            self.assertEqual(self.record_edits(sha, "--edits", "D1-review-1.md"), "D1 sent-back")
            self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 2 send-backs since its last pass or round decision; the decision opens at 3")
        self.assertEqual(self.send_back("a3"), "D1 sent-back; 3 send-backs, decision pending")

    def test_pass_record_refuses_pass_with_edits_without_edits_and_edits_with_another_verdict_or_a_report(self):
        self.fired_bug_fix()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.assertEqual(self.record_edits("abc", ok=False), "brigade: --verdict pass-with-edits needs --edits <review report>")
        self.assertEqual(self.record_edits("abc", "--report", "D1-review-1.md", ok=False),
                         "brigade: --verdict pass-with-edits needs --edits <review report>")
        for verdict in ("pass", "send-back", "blocked"):
            self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", verdict, "--author", CODEX,
                                          "--verifier", CLAUDE, "--edits", "D1-review-1.md", ok=False),
                             "brigade: --edits goes only with --verdict pass-with-edits", verdict)
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-1.md", "--report", "D1-review-1.md", ok=False),
                         "brigade: --edits names the round's report; drop --report")
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-9.md", ok=False),
                         "brigade: reports/D1-review-9.md does not exist; write the review report first")
        self.assertEqual(self.record_edits("abc", "--edits", "D1.md", ok=False),
                         "brigade: D1.md is not a review report of D1; name a file like reports/D1-review-1.md")
        self.assertEqual(self.pass_rows(), [])
        self.assertEqual(self.dish_fields("state"), ("in-progress",))

    def test_pass_record_refuses_a_pass_with_edits_report_whose_edits_do_not_parse(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n## Blockers\n\nNone.\n\n## Edits verified\n\nYes.\n")
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-1.md", ok=False),
                         "brigade: reports/D1-review-1.md holds no edit under a ## Edits heading")
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"),
                          "### Edit 2\n\nFile: `src/greeting.txt`\n\nOld text:\n\n```\nwrold\n```\n")
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-1.md", ok=False), self.PARTS_REFUSAL.format(2))
        self.assertEqual(self.pass_rows(), [])
        self.assertEqual(self.dish_fields("state"), ("in-progress",))

    def test_edits_verify_passes_a_head_that_is_the_edits_and_the_rebuilt_output_and_records_the_pair_once(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.edits_rows(), [])
        clean = (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", "")
        self.assertEqual(self.verify(reviewed, head), clean)
        self.assertEqual(self.edits_rows(), [["D1", reviewed, head, "D1-review-1.md"]])
        self.assertEqual(self.verify(reviewed, head), clean)
        self.assertEqual(self.edits_rows(), [["D1", reviewed, head, "D1-review-1.md"]])

    def test_edits_verify_ignores_an_edit_under_a_notes_edits_heading(self):
        reviewed = self.edits_store()
        self.two_edits(after="## Edits verified\n\nApplied in a scratch copy.\n\n## Notes edits\n\n"
                             + self.edit_block("1", "/srv/notes/review.md", "helo", "goodbye"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    def test_edits_verify_parses_an_edits_section_that_ends_the_report(self):
        reviewed = self.edits_store()
        self.two_edits(after="")
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    def test_edits_verify_fails_a_head_with_one_more_changed_line_in_a_source_file(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello world\nsecond line, changed\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, "FAIL out/greeting.txt: differs from what the build writes\n"
                             f"FAIL src/greeting.txt: differs from {reviewed} with the edits applied",
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_a_head_that_applied_an_edit_with_one_character_different(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello World\nsecond line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"built out/greeting.txt\nFAIL src/greeting.txt: differs from {reviewed} with the edits applied",
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_an_old_text_that_occurs_twice_at_the_reviewed_commit(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"),
                          self.edit_block("2", "src/greeting.txt", "n", "N"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello wrold\nsecoNd liNe\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL src/greeting.txt: old text of Edit 2 occurs 2 times at {reviewed}",
                          self.not_verified(reviewed, head)))

    def test_edits_verify_fails_two_edits_whose_old_texts_overlap_and_an_edit_to_a_path_with_no_file(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo wr", "hello wr"),
                          self.edit_block("2", "src/greeting.txt", "wrold", "world"),
                          self.edit_block("3", "src/absent.txt", "helo", "hello"),
                          self.edit_block("4", "src/greeting.txt", "second", "third"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello world\nthird line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, "FAIL src/greeting.txt: Edit 1 overlaps another edit\n"
                             "FAIL src/greeting.txt: Edit 2 overlaps another edit\n"
                             f"FAIL src/absent.txt: no regular file at {reviewed}",
                          self.not_verified(reviewed, head)))

    def test_edits_verify_fails_an_edit_that_names_a_symbolic_link_at_the_reviewed_commit(self):
        self.edits_store()
        (self.project / "src/link.txt").symlink_to("greeting.txt")
        reviewed = self.commit_files({})
        self.edits_report(self.edit_block("1", "src/link.txt", "helo", "hello"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL src/link.txt: no regular file at {reviewed}", self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_reads_the_edits_section_and_not_an_edits_line_in_an_earlier_fence(self):
        reviewed = self.edits_store()
        quoted = "## Edits\n\n" + self.edit_block("1", "tools/check.py", "import", "export")
        self.review_file("D1-review-1.md", f"Verdict: pass with edits\n\n## Blockers\n\n````\n{quoted}````\n\n## Edits\n\n"
                         + self.edit_block("1", "src/greeting.txt", "helo", "hello") + "\n"
                         + self.edit_block("2", "src/greeting.txt", "wrold", "world"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    def test_edits_verify_fails_a_head_that_applied_an_edit_quoted_in_a_tilde_fence_and_not_the_edit_of_the_edits_section(self):
        self.assert_the_quoted_edit_is_not_read("~~~text", "~~~")

    def test_edits_verify_fails_a_head_that_applied_an_edit_quoted_in_a_backtick_fence_with_two_info_words_and_not_the_edit_of_the_edits_section(self):
        self.assert_the_quoted_edit_is_not_read("````text title", "````")

    def test_pass_record_refuses_a_report_with_a_heading_that_is_not_number_signs_one_space_and_its_text_from_the_first_column(self):
        self.fired_bug_fix()
        for heading in (" ## Edits", "## Edits ", "## Edits\t", "## Edits ##", "##  Edits", "##\tEdits", "> ## Edits", "- ## Edits",
                        "1. ## Edits", "    ## Edits", "\t## Edits", "##", "> - ### Edit 2", "### Edit 2 "):
            with self.subTest(heading=heading):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{heading}\n\nNotes.\n\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md: line 3 starts like a heading and is not one to six #, one space, "
                                 "and the text, from the first column with nothing after the text")

    def test_pass_record_refuses_a_report_with_three_backticks_or_tildes_after_indentation_block_quote_marks_or_list_marks(self):
        self.fired_bug_fix()
        for opening in (" ```", "   ~~~~", "    ```", "\t~~~", "> ```", ">```", "- ```text", "1. ~~~", "  + ```", "> - ```", "2)\t~~~"):
            with self.subTest(opening=opening):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{opening}\nquoted\n\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md: line 3 " + self.NESTED_FENCE)

    def test_pass_record_refuses_a_report_with_a_fence_that_no_later_line_closes(self):
        self.fired_bug_fix()
        for opening, last in (("````text", "```"), ("~~~", "```"), ("```", "``"), ("```", "    ```"), ("~~~~", "~~~~ ~")):
            with self.subTest(opening=opening, last=last):
                body = (f"Verdict: pass with edits\n\n{self.section()}\n{opening}\nThe sentence then reads so.\n{last}\n\n"
                        "### Edit 2\n\nFile: `src/greeting.txt`\n\nOld text:\n")
                self.assertEqual(self.refused_edits(body),
                                 "brigade: reports/D1-review-1.md: line 21 opens a fence that no later line closes")

    def test_pass_record_refuses_a_report_with_a_line_of_only_equals_signs_or_only_hyphens_after_its_nesting_and_below_a_line_that_is_not_empty_and_not_only_spaces_and_tabs(self):
        self.fired_bug_fix()
        for above, underline in (("Edits", "---"), ("Edits", "====="), ("Edits", " -"), ("Edits", "=  "), ("> Edits", "> ---"),
                                 ("> Edits", ">="), ("- Edits", "  ==="), ("+ Edits", "\t-"), ("1. Edits", "   --- \t"),
                                 ("> - Edits", "  >   ="), ("Edits", "- -"), ("\u00a0", "---"), ("\x0c", "==="),
                                 (" \u2003", "-"), ("\x1f\t", "=")):
            with self.subTest(above=above, underline=underline):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{above}\n{underline}\n\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md: line 4 is only = or only - once its indentation, block quote "
                                 "marks, list marks, and trailing spaces and tabs are removed, and the line above it is not empty "
                                 "and not only spaces and tabs")

    def test_pass_record_refuses_a_report_with_a_line_that_starts_with_an_angle_bracket_and_then_an_ascii_letter_a_slash_an_exclamation_mark_or_a_question_mark(self):
        self.fired_bug_fix()
        for line in ("<details>", "   <!--", "</summary>", "<?php", "<item>: edits verified"):
            with self.subTest(line=line):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{line}\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md: line 3 starts, after at most three spaces, with < and then an "
                                 "ASCII letter, /, !, or ?")

    def test_pass_record_refuses_a_report_with_two_headings_whose_text_is_edits(self):
        self.fired_bug_fix()
        for other in ("## Edits", "# Edits", "#### Edits"):
            with self.subTest(other=other):
                quoted = self.edit_block("8", "src/greeting.txt", "helo", "hello")
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{other}\n\n{quoted}\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md holds 2 headings whose text is Edits")

    def test_pass_record_refuses_a_heading_in_the_edits_section_that_is_not_an_edit_heading_and_has_three_or_more_number_signs(self):
        self.fired_bug_fix()
        for heading in ("#### Edit 2", "### Edit", "### Edit  2", "### Notes on the edits", "###### Edit 2"):
            with self.subTest(heading=heading):
                body = (f"Verdict: pass with edits\n\n{self.section()}\n{heading}\n\n"
                        + self.edit_block("2", "src/greeting.txt", "helo", "hello").split("\n", 1)[1])
                self.assertEqual(self.refused_edits(body),
                                 f"brigade: reports/D1-review-1.md: line {body.split(chr(10)).index(heading) + 1} is a heading in the "
                                 "## Edits section that is neither ### Edit <n> nor a heading of one or two #")

    def test_edits_verify_does_not_read_an_edit_below_a_heading_of_one_number_sign_that_follows_the_edits_section(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"),
                          after="# Appendix\n\n" + self.edit_block("2", "src/greeting.txt", "wrold", "world"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.QUOTED_APPLIED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    def test_pass_record_refuses_a_fenced_block_in_the_edits_section_above_its_first_edit_heading(self):
        self.fired_bug_fix()
        unheaded = self.edit_block("1", "src/greeting.txt", "helo", "hello").split("\n", 2)[2]
        self.assertEqual(self.refused_edits("Verdict: pass with edits\n\n## Edits\n\n" + unheaded + "\n"
                                            + self.edit_block("2", "src/greeting.txt", "wrold", "world")),
                         "brigade: reports/D1-review-1.md: line 9 starts a fenced block in the ## Edits section above its first "
                         "### Edit heading")

    def test_pass_record_refuses_an_edit_with_a_second_file_line_old_text_line_new_text_line_or_a_third_fenced_block(self):
        self.fired_bug_fix()
        unheaded = self.edit_block("2", "src/greeting.txt", "helo", "hello").split("\n", 2)[2]
        for more in ("File: `src/other.txt`\n", "Old text:\n", "New text:\n", "```\nThe sentence then reads so.\n```\n", unheaded):
            with self.subTest(more=more):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{self.section()}\n{more}"),
                                 self.PARTS_REFUSAL.format(1))

    def test_pass_record_refuses_an_edit_with_a_file_old_text_or_new_text_line_after_whitespace_a_block_quote_mark_or_a_list_mark_or_a_label_line_that_ends_in_whitespace(self):
        self.fired_bug_fix()
        for more in ("  File: `src/other.txt`", "1. File: `src/other.txt`, line 2", "> Old text:", "- New text:", "\tOld text:",
                     "Old text: ", "New text:\t", "   > - New text:  ", "\u00a0File: `src/other.txt`", "\u00a0Old text:",
                     "\x0cNew text:", "Old text:\u00a0", "> \u2003New text:"):
            with self.subTest(more=more):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{self.section()}\n{more}\n"),
                                 "brigade: reports/D1-review-1.md: Edit 1 has a File, Old text, or New text line after "
                                 "whitespace, a block quote mark, or a list mark, or an Old text or New text line that ends in "
                                 "whitespace")

    def test_edits_verify_and_pass_record_refuse_a_file_old_text_or_new_text_line_directly_below_a_line_that_is_none_of_empty_only_spaces_and_tabs_the_edit_heading_a_closing_fence_and_those_three_lines(self):
        cases = []
        for line in ("File: `src/greeting.txt`", "Old text:", "New text:"):
            for above, below in self.AROUND:
                body = self.one_edit_report().replace(line, "\n".join(filter(None, (above, line, below))))
                cases.append((body, f": line {body.split(chr(10)).index(line) + 1}{self.BELOW}"))
        self.assert_edits_verify_and_pass_record_refuse(cases)

    def test_edits_verify_and_pass_record_refuse_an_edit_with_a_line_that_is_not_empty_and_not_only_spaces_and_tabs_between_an_old_text_or_new_text_line_and_the_fence_below_it(self):
        cases = []
        for label, fence in (("Old text:", "```"), ("New text:", "```text")):
            for between in ("A note.\n", "\nA note.\n\n", "- item\n", "\u00a0\n", "> quoted\n\n"):
                body = self.one_edit_report().replace(f"{label}\n\n", f"{label}\n{between}")
                cases.append((body, f": line {body.split(chr(10)).index(fence) + 1}{self.APART}"))
        body = self.one_edit_report().replace("File: `src/greeting.txt`\n\nOld text:\n\n",
                                              "File: `src/greeting.txt` ``\nOld text:\n`` and a note\n\n")
        self.assert_edits_verify_and_pass_record_refuse(cases + [(body, ": line 11" + self.APART)])

    def test_edits_verify_and_pass_record_refuse_an_edit_with_a_line_that_starts_once_its_indentation_block_quote_marks_list_marks_and_end_whitespace_are_removed_with_file_and_a_backtick_and_a_path_that_starts_with_a_space_or_a_backtick_ends_at_no_backtick_or_ends_at_two(self):
        report = self.one_edit_report()
        cases = [report.replace("File: `src/greeting.txt`", line)
                 for line in ("File: `src/greeting.txt`` b`", "File: ` src/greeting.txt `", "File: ``src/greeting.txt``",
                              "File: `src/greeting.txt", "File: ``")]
        cases += [report.replace("### Edit 1\n", f"### Edit 1\n\n{line}\n")
                  for line in ("> File: ``src/other.txt``", "  - File: ` src/other.txt `", "\u00a0File: `src/other.txt``")]
        self.assert_edits_verify_and_pass_record_refuse([(body, self.MALFORMED) for body in cases])

    def test_edits_verify_and_pass_record_refuse_a_report_that_starts_with_a_byte_order_mark_and_one_that_holds_a_nul_character(self):
        report = self.one_edit_report()
        self.assert_edits_verify_and_pass_record_refuse([("\ufeff" + report, " starts with a byte order mark"),
                                                         (report.replace("wrold", "wr\0old"), " holds a NUL character"),
                                                         ("Notes\0\n\n" + report, " holds a NUL character")])

    def test_edits_verify_reads_an_edit_whose_lines_stand_directly_below_one_another_in_a_report_with_lines_that_start_with_a_digit_that_is_not_ascii_and_a_period_or_a_closing_parenthesis(self):
        reviewed = self.edits_store()
        notes = ("## Notes\n\n\u0661. ```\n\n\uff11) ~~~\n\nNote\n\u0661. ---\n\n\u0661. ## Edits\n\n"
                 "\u0967. <details>\n\n")
        self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n" + notes + "## Edits\n\n"
                         "### Edit 1\nFile: `src/greeting.txt`, line 1.\nOld text:\n```\nhelo\n```\nNew text:\n```text\nhello\n```\n\n"
                         "\u0661. Old text:\n\n\u0661. File: `src/other.txt`\n\n"
                         "### Edit 2\n\nFile: `src/greeting.txt`\nOld text:\n\n```\nwrold\n```\nNew text:\n```\nworld\n```\n")
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    @unittest.skipIf(os.geteuid() == 0, "the superuser reads a file with no read permission")
    def test_pass_record_refuses_a_report_that_cannot_be_read(self):
        self.fired_bug_fix()
        report = self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n" + self.section())
        report.chmod(0)
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-1.md", ok=False),
                         "brigade: reports/D1-review-1.md cannot be read: Permission denied")
        self.assertEqual(self.pass_rows(), [])

    def test_pass_record_refuses_a_report_that_holds_a_carriage_return(self):
        self.fired_bug_fix()
        report = "Verdict: pass with edits\n\n" + self.section()
        for body in (report.replace("\n", "\r\n"), report.replace("wrold", "wrold\r"), "Notes\r" + report):
            with self.subTest(body=body):
                self.assertEqual(self.refused_edits(body), "brigade: reports/D1-review-1.md holds a carriage return")

    def test_pass_record_refuses_a_report_that_is_not_utf_8(self):
        self.fired_bug_fix()
        report = ("Verdict: pass with edits\n\n" + self.section()).encode()
        self.assertEqual(self.refused_edits(report.replace(b"world", b"w\xf6rld")), "brigade: reports/D1-review-1.md is not UTF-8")

    def test_edits_verify_reads_tilde_fences_and_closing_fences_with_spaces_around_them_below_notes_that_report_parts_does_not_refuse(self):
        reviewed = self.edits_store()
        notes = ("## Notes\n\n> quoted\n\n- item\n\n      nested\n\n-```\n\n```a`b\n\n``\n\n    <details>\n\n<\u00e9\n\n"
                 "< 3 items\n\n---\n\n## Rule\n---\n\n```\ncode\n```\n---\n\n- File: `src/other.txt`\n\n  Old text:\n\n"
                 "#hashtag\n\n####### seven\n\n")
        self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n" + notes + "## Edits\n\n"
                         "### Edit 1\n\nFile: `src/greeting.txt`\n\nOld text:\n\n~~~\nhelo\n~~~\n\n"
                         "New text:\n\n~~~~text one `two`\nhello\n  ~~~~~ \t\n\n"
                         "### Edit 2\n\nFile: `src/greeting.txt`\n\nOld text:\n\n```\nwrold\n   ```\n\nNew text:\n\n```\nworld\n```  \n")
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    def test_edits_verify_refuses_a_build_command_that_names_a_program_that_does_not_exist(self):
        command = "no-such-program-d152 gen"
        reviewed = self.edits_store(build=command)
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        code, out, err = self.verify(reviewed, head)
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith(f"brigade: cannot run {command}: "), err)
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_a_head_that_left_an_edit_unapplied(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello wrold\nsecond line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, "FAIL out/greeting.txt: differs from what the build writes\n"
                             f"FAIL src/greeting.txt: differs from {reviewed} with the edits applied",
                          self.not_verified(reviewed, head)))

    def test_edits_verify_refuses_a_report_that_no_longer_has_an_edits_section(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n## Blockers\n\nNone.\n\n## Edits verified\n\nYes.\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, "", "brigade: reports/D1-review-1.md holds no edit under a ## Edits heading"))
        (self.at / "reports" / "D1-review-1.md").unlink()
        self.assertEqual(self.verify(reviewed, head),
                         (1, "", "brigade: reports/D1-review-1.md does not exist; nothing verified"))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_a_head_whose_build_output_is_stale(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED, out=self.SOURCE.upper())
        self.assertEqual(self.verify(reviewed, head),
                         (1, "FAIL out/greeting.txt: differs from what the build writes\nedited src/greeting.txt",
                          self.not_verified(reviewed, head)))

    def test_edits_verify_fails_when_the_check_command_exits_non_zero(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "second", "forbidden"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("helo wrold\nforbidden line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"built out/greeting.txt\nedited src/greeting.txt\nFAIL check: {self.CHECK_COMMAND} exited 3",
                          f"check saw forbidden\n{self.not_verified(reviewed, head)}"))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_when_the_build_command_exits_non_zero(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "second", "unbuildable"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("helo wrold\nunbuildable line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL build: {self.BUILD_COMMAND} exited 2",
                          f"build refuses unbuildable\n{self.not_verified(reviewed, head)}"))

    def test_edits_verify_reads_a_four_backtick_fence_around_three_backticks_and_an_empty_new_text(self):
        note = "Intro\n```\ncode\n```\nremove me\nEnd\n"
        reviewed = self.edits_store(**{"docs/note.md": note})
        self.edits_report(self.edit_block("1", "docs/note.md", "```\ncode\n```", "```\ncoda\n```", fence="````"),
                          "### Edit 2\n\nFile: `docs/note.md`:5\n\nOld text:\n\n```\nremove me\n```\n\nNew text:\n\n```\n```\n")
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.SOURCE, **{"docs/note.md": "Intro\n```\ncoda\n```\n\nEnd\n"})
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"edited docs/note.md\nok check\n{self.verified(reviewed, head)}", ""))

    def test_edits_verify_and_pass_record_refuse_a_path_that_is_absolute_or_has_a_dot_dot_part(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        for path in ("/srv/app/greeting.txt", "src/../../greeting.txt"):
            self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"), self.edit_block("7b", path, "helo", "hello"))
            refusal = (f"brigade: reports/D1-review-1.md: Edit 7b names {path}; "
                       "a path is relative to the repository and has no .. part")
            self.assertEqual(self.verify(reviewed, head), (1, "", refusal))
            self.assertEqual(self.record_edits(reviewed, "--edits", "D1-review-1.md", ok=False), refusal)
        self.edits_report(self.edit_block("3", "src/greeting.txt", "", "hello"))
        self.assertEqual(self.verify(reviewed, head), (1, "", "brigade: reports/D1-review-1.md: Edit 3 has an empty old text"))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_leaves_the_worktree_and_its_head_unchanged(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        (self.project / "untracked.txt").write_text("mine\n")
        (self.project / "src/greeting.txt").write_text("half an edit\n")
        state = lambda: tuple(subprocess.run(("git", *command), cwd=self.project, capture_output=True, text=True,
                                             check=True).stdout
                              for command in (("status", "--porcelain"), ("rev-parse", "HEAD"), ("diff",)))
        before = state()
        self.assertEqual(before[0], " M src/greeting.txt\n?? untracked.txt\n")
        self.assertEqual(self.verify(reviewed, head)[0], 0)
        self.assertEqual(self.verify(reviewed, reviewed)[0], 1)
        self.assertEqual(state(), before)

    def test_a_pass_after_a_pass_with_edits_round_is_refused_until_the_edits_are_verified_for_that_head(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))
        self.assertEqual(self.record_pass_at(head, "--late", ok=False), self.gate(reviewed, head))
        self.assertEqual(len(self.pass_rows()), 1)
        self.assertEqual(self.verify(reviewed, head)[0], 0)
        self.assertEqual(self.record_pass_at(head), "D1 passed")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", head), f"D1 at {head} passed review by {CLAUDE}")

    def test_verified_edits_do_not_lift_the_refusal_of_a_pass_at_another_head_or_at_the_same_head_written_shorter(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head)[0], 0)
        later = self.head_commit(self.FIXED + "one more line\n")
        self.assertEqual(self.record_pass_at(later, ok=False), self.gate(reviewed, later))
        self.assertEqual(self.record_pass_at(head[:12], ok=False), self.gate(reviewed, head[:12]))
        self.assertEqual(len(self.pass_rows()), 1)

    def test_a_member_pass_after_a_pass_with_edits_round_is_recorded_without_verified_edits(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "anyhead", "--verdict", "pass", "--author", CODEX,
                                      "--verifier", "grok/grok-4.7", "--member"),
                         "D1: member pass on record; D1 stays sent-back")
        self.assertEqual(self.record_pass_at("anyhead", ok=False), self.gate(reviewed, "anyhead"))

    def test_close_groups_in_progress_and_new_tickets_by_goal_and_prints_the_kind_on_the_lines_of_merged_in_progress_and_new_tickets(self):
        self.close_setup()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.record("abc", "pass")
        self.brigade("dish", "D1", "--state", "merged")
        self.brigade("dish", "D4", "--state", "in-review", "--sha", "def")
        self.brigade("pass", "record", "D4", "--sha", "def", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX,
                     "--note", "still hangs")
        code, out, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[4:], [
            "## Merged", "",
            "- study D1 (T1): Measure capacity", "",
            "## Sent back after review", "",
            "- D4 (T5): Fix the splash", "",
            "## In progress", "",
            "### G1 activity view", "",
            "- fix D2 (T2, T3): Fix the lease check", "",
            "### Unassigned", "",
            "- upkeep D3 (T4): Follow the nightly", "",
            "## New tickets, not started", "",
            "### G1 activity view", "",
            "- fix T6: Drops a row", "",
            "### G2 current with upstream", "",
            "- polish T7: Later", "",
            "### Unassigned", "",
            "- polish T8: Tidy the docs",
            "- T9: Installer",
            "", "## Spend", "",
            "goal not recorded  wall 0m (1 open)  rounds 1  send-backs 0  threads not recorded",
            "G1  wall open  rounds 0  send-backs 0  threads not recorded",
            "G2  wall open  rounds 1  send-backs 1  threads not recorded",
        ])

    def test_next_runs_each_conditional_action(self):
        self.fired_bug_fix()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        liveness = self.next_sheet("liveness")
        self.assertEqual(self.execute_conditionals(liveness), [
            "next merge D1",
            "next --failure stuck",
            "set --schedule liveness=",
        ])
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        checks = self.next_sheet("checks", "D1", "--result", "fail")
        self.assertEqual(self.execute_conditionals(checks), [
            "next merge D1",
            "next checks D1 --result fail --failure stuck",
        ])
        self.fresh()
        review = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.execute_conditionals(review), ["dish D1 --state in-progress"])
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("pass", "record", "D1", "--sha", "s2", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        owed = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertIn("if D1 reaches the round budget and no decision is open: run 86 add --dish D1 --round-budget", owed)
        self.brigade("pass", "record", "D1", "--sha", "s3", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.execute_commands([["86", "add", "--dish", "D1", "--round-budget"]]),
                         ["86 add --dish D1 --round-budget"])
        shutil.rmtree(self.at)
        self.open()
        inbox = self.at / "inbox"
        inbox.mkdir()
        (inbox / "A1.line").write_text("please fix the gate\n")
        admin = self.next_sheet("admin", "A1")
        self.assertEqual(self.execute_conditionals(admin), ["inbox done A1"])
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        landed = self.next_sheet()
        self.assertIn("if the landing queue has an entry for D1: run land.py status --holder perf/D1 --sha abc", landed)
        self.init_landing()
        self.assertEqual(self.execute_conditionals(landed), ["land.py status --holder perf/D1 --sha abc"])

    def test_next_omits_the_fix_round_at_the_round_budget(self):
        self.fresh()
        below = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertIn("if the old worker's wait for D1 is terminal and D1 may take a fix round: run dish D1 --state in-progress", below)
        self.assertNotIn("round-budget", below)
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.execute_conditionals(below), ["dish D1 --state in-progress"])
        self.fresh()
        for number in range(3):
            self.brigade("pass", "record", "D1", "--sha", f"s{number}", "--verdict", "send-back",
                         "--author", CLAUDE, "--verifier", CODEX)
        owed = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertIn("\nrun 86 add --dish D1 --round-budget\n", f"\n{owed}\n")
        self.assertNotIn("run dish D1 --state in-progress", owed)
        self.execute_sheet(owed)
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("pass", "record", "D1", "--sha", "s2", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        crossing = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertIn("if D1 reaches the round budget and no decision is open: run 86 add --dish D1 --round-budget", crossing)
        self.assertNotIn("run dish D1 --state in-progress", crossing)
        self.brigade("pass", "record", "D1", "--sha", "s3", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.execute_conditionals(crossing), ["86 add --dish D1 --round-budget"])

    def test_next_restarts_a_pass_with_edits_review_after_two_send_backs(self):
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("pass", "record", "D1", "--sha", "s2", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        sheet = self.next_sheet("review", "D1", "--verdict", "pass-with-edits")
        self.assertIn(
            "if the old worker's wait for D1 is terminal and D1 may take a fix round: run dish D1 --state in-progress",
            sheet)
        self.assertNotIn("round-budget", sheet)
        self.assertEqual(sheet.splitlines()[-1], "reply: yes (sent-back: D1)")
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"), name="D1-review-3.md")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "s3", "--verdict", "pass-with-edits",
                                      "--author", CLAUDE, "--verifier", CODEX, "--edits", "D1-review-3.md"),
                         "D1 sent-back")
        self.assertEqual(self.execute_conditionals(sheet), ["dish D1 --state in-progress"])
        self.fresh()
        for number in range(3):
            self.brigade("pass", "record", "D1", "--sha", f"s{number}", "--verdict", "send-back",
                         "--author", CLAUDE, "--verifier", CODEX)
        at_budget = self.next_sheet("review", "D1", "--verdict", "pass-with-edits")
        self.assertNotIn("run dish D1 --state in-progress", at_budget)
        self.assertIn("run 86 add --dish D1 --round-budget", at_budget)

    def test_a_reason_is_refused_with_a_verdict_other_than_send_back(self):
        self.fired_bug_fix()
        before = (self.at / "pass.tsv").read_bytes()
        refusal = "brigade: --reason goes only with --verdict send-back"
        for verdict in ("pass", "blocked", "pass-with-edits"):
            with self.subTest(verdict=verdict):
                self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", verdict,
                                              "--author", CLAUDE, "--verifier", CODEX, "--reason", "scope", ok=False), refusal)
        self.assertEqual((self.at / "pass.tsv").read_bytes(), before)

    def test_a_reason_outside_the_list_is_refused_and_writes_no_row(self):
        self.fired_bug_fix()
        before = (self.at / "pass.tsv").read_bytes()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                     "--author", CLAUDE, "--verifier", CODEX, "--reason", "nonsense", ok=False)
        self.assertEqual((self.at / "pass.tsv").read_bytes(), before)
        self.assertEqual(self.pass_rows(), [])

    def test_a_send_back_stores_its_reason(self):
        self.fired_bug_fix()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                     "--author", CLAUDE, "--verifier", CODEX, "--reason", "missing-test")
        self.assertEqual(self.pass_rows()[-1][-1], "missing-test")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False),
                         "brigade: D1 at abc: send-back (no note)")

    def test_a_row_written_before_the_reason_column_reads_as_empty_and_counts_as_unrecorded(self):
        self.fired_bug_fix()
        stamp = "2026-10-08T00:00:00+00:00"
        table = self.at / "pass.tsv"
        table.write_text("at\tdish\tpr\tsha\tverdict\tauthor\tverifier\tnote\treport\tmember\n"
                         + f"{stamp}\tD1\t\ts1\tsend-back\t{CLAUDE}\t{CODEX}\t\t\t\n"
                         + f"{stamp}\tD1\t\ts2\tpass\t{CLAUDE}\t{CODEX}\t\t\t\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "s3", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, "--reason", "scope"), "D1 sent-back")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "s2"), f"D1 at s2 passed review by {CODEX}")
        self.assertEqual(self.brigade("pass", "stats"), "\n".join([
            "last 1 item with a verdict",
            "first pass overall: 0 of 1 (0.0%)",
            f"first pass {CLAUDE}: 0 of 1 (0.0%)",
            "send-backs: scope 1, unrecorded 1",
        ]))

    def test_pass_stats_prints_the_first_pass_rate_per_author_and_the_send_back_reasons(self):
        self.fired_bug_fix()
        self.record("abc", "pass", author=CODEX, verifier=CLAUDE)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "t")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Fix t")
        self.brigade("pass", "record", "D2", "--sha", "d2a", "--verdict", "send-back",
                     "--author", CLAUDE, "--verifier", CODEX, "--reason", "missing-test")
        self.brigade("pass", "record", "D2", "--sha", "d2b", "--verdict", "send-back",
                     "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "u")
        self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "Fix u")
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"), name="D3-review-1.md")
        self.brigade("pass", "record", "D3", "--sha", "d3", "--verdict", "pass-with-edits",
                     "--author", CLAUDE, "--verifier", CODEX, "--edits", "D3-review-1.md")
        self.assertEqual(self.brigade("pass", "stats", "--last", "3"), "\n".join([
            "last 3 items with a verdict",
            "first pass overall: 1 of 3 (33.3%)",
            f"first pass {CLAUDE}: 0 of 2 (0.0%)",
            f"first pass {CODEX}: 1 of 1 (100.0%)",
            "send-backs: missing-test 1, unrecorded 1",
        ]))

    def test_pass_stats_prints_no_items_with_a_verdict_when_no_pass_row_exists(self):
        self.open()
        before = {p.relative_to(self.at): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.at.rglob("*") if p.is_file()}
        self.assertEqual(self.brigade("pass", "stats"), "no items with a verdict")
        self.assertEqual({p.relative_to(self.at): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.at.rglob("*") if p.is_file()}, before)
