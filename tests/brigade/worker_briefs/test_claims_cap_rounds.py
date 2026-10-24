if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

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


class ClaimsCapRoundsTest(BrigadeTest):
    def report_with_threads(self, threads):
        self.review_file("D1.md", "# D1\n\nStatus: done\n\nClaims check: 4 statements checked, 0 corrected\n\n"
                                  f"## Threads\n\n{threads}")

    def attempt_report_with_threads(self, threads):
        self.attempt_report("# D1\n\nStatus: done\n\nClaims check: 4 statements checked, 0 corrected\n\n"
                            f"## Threads\n\n{threads}")

    def test_close_stays_silent_for_two_recorded_checks_plus_one_new_check(self):
        self.fired_bug_fix()
        self.report_with_threads(TWO_RUNS)
        self.record("abc", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.attempt_report_with_threads(THREE_RUNS)
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "def")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))

    def test_close_warns_for_three_new_checks_in_one_round(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.report_with_threads(THREE_RUNS)
        self.assertEqual(self.close_run("--dry-run")[::2],
                         (0, "brigade: warning: reports/D1.md names 3 new claims check runs for this round; the cap is two per round"))

    def test_close_stays_silent_for_a_stale_report_older_than_the_last_attempt_start(self):
        import os
        from datetime import datetime, timezone
        self.fired_bug_fix()
        self.record("abc", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.report_with_threads(THREE_RUNS)
        path = self.at / "reports" / "D1.md"
        stamp = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(path, (stamp, stamp))
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "def")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
