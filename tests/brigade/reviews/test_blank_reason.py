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
)


NO_REASON = ("brigade: --verdict send-back needs --reason, one of false-wording, missing-test, wrong-behavior, "
             "out-of-lease, scope, other")
REASON_WITHOUT_SEND_BACK = "brigade: --reason goes only with --verdict send-back"


class BlankReasonTest(BrigadeTest):
    def test_a_send_back_with_no_reason_is_refused_before_the_store_lock_is_taken(self):
        self.fired_bug_fix()
        lock = self.at / "restaurant.lock"
        lock.unlink()
        before = self.store_files()
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, code=1), NO_REASON)
        self.assertFalse(lock.exists())
        self.assertEqual(self.store_files(), before)

    def test_a_member_send_back_with_no_reason_is_refused_the_same_way(self):
        self.fired_bug_fix()
        before = self.store_files()
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, "--member", code=1), NO_REASON)
        self.assertEqual(self.store_files(), before)

    def test_a_send_back_reason_is_recorded_in_the_rows_reason_cell(self):
        self.fired_bug_fix()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--reason", "scope",
                     "--author", CLAUDE, "--verifier", CODEX)
        row = self.pass_rows()[-1]
        self.assertEqual((row[4], row[10]), ("send-back", "scope"))

    def test_a_pass_still_records_with_no_reason(self):
        self.fired_bug_fix()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass",
                     "--author", CLAUDE, "--verifier", CODEX)
        row = self.pass_rows()[-1]
        self.assertEqual((row[4], row[10]), ("pass", ""))

    def test_a_reason_with_a_pass_is_still_refused(self):
        self.fired_bug_fix()
        before = self.store_files()
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--reason", "scope",
                                      "--author", CLAUDE, "--verifier", CODEX, code=1), REASON_WITHOUT_SEND_BACK)
        self.assertEqual(self.store_files(), before)
