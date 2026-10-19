from ..support import (
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


class ReviewsSplit1Test(BrigadeTest):
    def test_each_threads_case_is_refused_by_its_number_or_records_its_rows(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        role = f"claims check `{THREAD_C}` {CODEX}"
        item = ("brigade: D1: reports/D1.md cannot be read; line 6 is not one list item of the form "
                "- <role> `<thread id>` <seat>; nothing recorded")
        deeper = ("brigade: D1: reports/D1.md cannot be read; line 6 is a heading deeper than the ## Threads "
                  "heading; nothing recorded")
        again = "brigade: D1: reports/D1.md cannot be read; line 6 is a second ## Threads heading; nothing recorded"
        recorded = [["D1", "1", "worker", THREAD_A, "", "stamp", ""],
                    ["D1", "1", "code delegate", THREAD_B, "opencode-go/step-5-preview-free", "", "stamp"],
                    ["D1", "1", "claims check", THREAD_C, CODEX, "", "stamp"]]
        cases = (
            ("prose", "Both children finished.", "", recorded[:2]),
            ("indented dash", f" - {role}", item, None),
            ("three-space dash", f"   - {role}", item, None),
            ("tabbed dash", f"\t- {role}", item, None),
            ("indented star", f"  * {role}", item, None),
            ("tabbed plus", f"\t+ {role}", item, None),
            ("numbered dot", f"1. {role}", item, None),
            ("numbered paren", f"1) {role}", item, None),
            ("bullet", f"\u2022 {role}", item, None),
            ("missing dash", role, item, None),
            ("missing separator", f"-{role}", item, None),
            ("bare dash", "-", item, None),
            ("bare star", "*", item, None),
            ("bare plus", "+", item, None),
            ("indented deeper heading", "  ### Children", item, None),
            ("indented Threads heading", "  ## Threads", item, None),
            ("dash tab", f"-\t{role}", item, None),
            ("star space", f"* {role}", item, None),
            ("plus space", f"+ {role}", item, None),
            ("dash then role only", "- claims check", item, None),
            ("deeper heading", "### Children", deeper, None),
            ("second Threads heading", "## Threads", again, None),
            ("one item", f"- {role}", "", recorded),
        )
        for name, third, error, expected in cases:
            with self.subTest(case=name):
                self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                          f"- code delegate `{THREAD_B}` opencode-go/step-5-preview-free\n{third}\n")
                before = self.store_files()
                record = ("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                          "--author", CLAUDE, "--verifier", CODEX)
                if error:
                    self.assertEqual(self.brigade(*record, ok=False), error)
                    self.assertEqual(self.store_files(), before)
                else:
                    self.assertEqual(self.brigade(*record), "D1 sent-back")
                    self.assertEqual([row[1:] for row in self.thread_rows()], expected)

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
