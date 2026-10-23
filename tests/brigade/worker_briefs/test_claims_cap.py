from ..support import (
    THREAD_A,
    THREAD_B,
    THREAD_C,
    BrigadeTest,
    setUpModule,
)


CLAIMS_CHECK = "claudeLean/claude-haiku-5-5"

THREE_RUNS = (
    f"- claims check `{THREAD_A}` {CLAIMS_CHECK}\n"
    f"- claims check `{THREAD_B}` {CLAIMS_CHECK}\n"
    f"- claims check `{THREAD_C}` {CLAIMS_CHECK}\n"
)

TWO_RUNS = (
    f"- claims check `{THREAD_A}` {CLAIMS_CHECK}\n"
    f"- claims check `{THREAD_B}` {CLAIMS_CHECK}\n"
)


class ClaimsCapTest(BrigadeTest):
    def report_with_threads(self, threads):
        self.review_file("D1.md", "# D1\n\nStatus: done\n\nClaims check: 4 statements checked, 0 corrected\n\n"
                                  f"## Threads\n\n{threads}")

    def test_a_brief_states_the_two_run_claims_cap_and_not_the_rerun_rule(self):
        self.coordinator()
        text = self.brigade(*self.BRIEF)
        verify = text.split("\nVERIFY:\n", 1)[1].split("\nTIMEBOX:", 1)[0]
        self.assertIn("The claims check runs at most twice per round, the first check and one recheck after fixing what it "
                      "flagged.", verify)
        self.assertIn("each flag kept is listed with one line of reason in the report", verify)
        self.assertNotIn("Run the claims check again after any later edit to prose", text)

    def test_close_warns_when_a_report_names_three_claims_check_runs(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.report_with_threads(THREE_RUNS)
        self.assertEqual(self.close_run("--dry-run")[::2],
                         (0, "brigade: warning: reports/D1.md names 3 claims check runs; the cap is two per round"))

    def test_close_prints_no_claims_cap_warning_for_two_claims_check_runs(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.report_with_threads(TWO_RUNS)
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
