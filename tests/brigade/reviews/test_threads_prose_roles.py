from ..support import (
    setUpModule,
    BrigadeTest,
    CLAUDE,
    CODEX,
    THREAD_A,
    THREAD_B,
    THREAD_C,
)


PROSE = "The code delegate ran at 18:11 UTC on a Saturday, off peak."


class ThreadsProseRoleTest(BrigadeTest):
    def test_a_prose_line_naming_a_child_role_is_skipped_and_the_row_above_it_is_recorded(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                  f"- code delegate `{THREAD_B}` opencode-go/step-5-preview-free\n"
                                  f"{PROSE}\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX), "D1 sent-back")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""],
                          ["D1", "1", "code delegate", THREAD_B, "opencode-go/step-5-preview-free", "", "stamp"]])

    def test_a_list_row_under_a_prose_line_naming_a_child_role_still_records_its_thread(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                  f"{PROSE}\n"
                                  f"- claims check `{THREAD_C}` {CODEX}\n")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX), "D1 sent-back")
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""],
                          ["D1", "1", "claims check", THREAD_C, CODEX, "", "stamp"]])

    def test_a_malformed_list_row_under_a_prose_line_naming_a_child_role_is_refused_by_its_number(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                  f"{PROSE}\n"
                                  f"- claims check {THREAD_C} {CODEX}\n")
        before = self.store_files()
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, ok=False),
                         "brigade: D1: reports/D1.md cannot be read; line 6 is not one list item of the form "
                         "- <role> `<thread id>` <seat>; nothing recorded")
        self.assertEqual(self.store_files(), before)
        self.assertEqual([row[1:] for row in self.thread_rows()],
                         [["D1", "1", "worker", THREAD_A, "", "stamp", ""]])
