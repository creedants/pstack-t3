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
    _owner_words,
)


class HelperArchiveTest(BrigadeTest):
    def archived(self, *args):
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                      *_owner_words(self.at), *(str(arg) for arg in args)])
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        return outcome.stderr_text().splitlines()

    def test_helper_lines_print_at_verdict(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                   f"- code delegate `{THREAD_B}` opencode-go/step-5-preview-free\n")
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "abc", "--verdict", "pass",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_C),
                         [f"archive thread: {THREAD_C}", f"archive thread: {THREAD_B}"])

    def test_exclusions_hold_for_helper_id(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n\n## Threads\n\n"
                                   f"- code delegate `{THREAD_A}` opencode-go/step-5-preview-free\n")
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "abc", "--verdict", "pass",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_C),
                         [f"archive thread: {THREAD_C}"])

    def test_report_with_no_helpers_prints_only_verifier_line(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.review_file("D1.md", "findings\n")
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "abc", "--verdict", "pass",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_C),
                         [f"archive thread: {THREAD_C}"])

    def test_late_verdict_at_old_sha_announces_no_replacement_helper(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.record("abc", "send-back", "--note", "the attempt")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.brigade("dish", "D1", "--thread", THREAD_D)
        self.attempt_report("findings\n\n## Threads\n\n"
                            f"- code delegate `{THREAD_B}` opencode-go/step-5-preview-free\n")
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                       "--reason", "other",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_C, "--late"),
                         [f"archive thread: {THREAD_C}"])

    def test_stale_report_does_not_reannounce_helper(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.attempt_report("findings\n\n## Threads\n\n"
                            f"- code delegate `{THREAD_B}` opencode-go/step-5-preview-free\n")
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                       "--reason", "other",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_C),
                         [f"archive thread: {THREAD_C}", f"archive thread: {THREAD_B}"])
        self.brigade("dish", "D1", "--state", "in-progress")
        self.brigade("dish", "D1", "--thread", THREAD_D)
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "def", "--verdict", "send-back",
                                       "--reason", "other",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_C),
                         [f"archive thread: {THREAD_C}"])
