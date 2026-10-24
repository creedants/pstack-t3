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
    setUpModule,
    BrigadeTest,
    CLAUDE,
    CODEX,
    SCRIPT,
    THREAD_A,
    THREAD_B,
    THREAD_C,
    THREAD_D,
    call,
    json,
    _owner_words,
)


HELPER = f"- code delegate `{THREAD_B}` opencode-go/step-5-preview-free\n"


class LateHelperRowTest(BrigadeTest):
    def archived(self, *args):
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                      *_owner_words(self.at), *(str(arg) for arg in args)])
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        return outcome.stderr_text().splitlines()

    def test_late_verdict_does_not_suppress_the_next_attempts_helper_line(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n")
        self.record("abc", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.attempt_report(f"findings\n\n## Threads\n\n{HELPER}")
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_C, "--late"),
                         [f"archive thread: {THREAD_C}"])
        self.brigade("dish", "D1", "--thread", THREAD_D)
        self.attempt_report(f"findings\n\n## Threads\n\n{HELPER}")
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "def", "--verdict", "send-back",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_C),
                         [f"archive thread: {THREAD_C}", f"archive thread: {THREAD_B}"])

    def test_stale_report_announces_no_helper_that_a_member_row_bound_at_the_current_attempt(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n")
        self.record("abc", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.attempt_report(f"findings\n\n## Threads\n\n{HELPER}")
        self.brigade("dish", "D1", "--thread", THREAD_D)
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "def", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, "--member"),
                         "D1: member send-back on record; D1 stays in-progress")
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "def", "--verdict", "send-back",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_C),
                         [f"archive thread: {THREAD_C}"])

    def test_late_helper_thread_stays_in_spend_after_report_replaced(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.record("abc", "send-back")
        self.brigade("dish", "D1", "--thread", THREAD_D)
        self.attempt_report(f"findings\n\n## Threads\n\n{HELPER}")
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_C, "--late"),
                         [f"archive thread: {THREAD_C}"])
        late = json.loads(self.brigade("spend", "--json"))
        self.assertEqual(late["rows"][0]["threads"], 4)
        self.assertIn(THREAD_B, (self.at / "threads.tsv").read_text())
        self.attempt_report("findings\n")
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "def", "--verdict", "send-back",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_C),
                         [f"archive thread: {THREAD_C}"])
        current = json.loads(self.brigade("spend", "--json"))
        self.assertEqual(current["rows"][0]["threads"], 4)
        self.assertIn(THREAD_B, (self.at / "threads.tsv").read_text())

    def test_block_resume_keeps_legitimate_helper_suppressing_next_attempt(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.brigade("dish", "D1", "--state", "blocked")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.attempt_report(f"findings\n\n## Threads\n\n{HELPER}")
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_C),
                         [f"archive thread: {THREAD_C}", f"archive thread: {THREAD_B}"])
        self.brigade("dish", "D1", "--state", "in-progress")
        self.brigade("dish", "D1", "--thread", THREAD_D)
        self.attempt_report(f"findings\n\n## Threads\n\n{HELPER}")
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "def", "--verdict", "send-back",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_C),
                         [f"archive thread: {THREAD_C}"])
