if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import setUpModule
from ..support import BrigadeTest, SCRIPT, call, json, subprocess, sys
import contextlib
import io
import os
import runpy
import sqlite3
from pathlib import Path
from .fixtures import FIRE, CLOSE, HOUR, FOOTER, _OMIT, _EMPTY_DETAILS, _Helpers

class SpendTest(BrigadeTest, _Helpers):
    def test_no_ledger_rows_print_threads_not_recorded(self):
        self.fill([self.dish()], logs=[self.close()])
        self.assertEqual(
            self.brigade("spend"),
            "D1  wall 2h  rounds 0  send-backs 0  threads not recorded\n" + FOOTER,
        )
    def test_pass_with_edits_is_a_round_and_not_a_send_back(self):
        self.fill([self.dish()], passes=[
            {"at": FIRE, "dish": "D1", "verdict": "pass-with-edits", "author": "codex/gpt-6.1-sol", "verifier": "grok/grok-4",
             "note": "note sentinel"},
            {"at": CLOSE, "dish": "D1", "verdict": "send-back", "author": "codex/gpt-6.1-sol", "verifier": "grok/grok-4"},
            {"at": CLOSE, "dish": "D1", "verdict": "send-back", "author": "codex/gpt-6.1-sol", "verifier": "grok/grok-4",
             "member": "panel"},
        ], logs=[self.close()])
        self.assertEqual(
            self.brigade("spend"),
            "D1  wall 2h  rounds 2  send-backs 1  threads not recorded\n" + FOOTER,
        )
    def test_an_open_item_prints_wall_open(self):
        self.fill([self.dish(state="in-progress")])
        self.assertEqual(
            self.brigade("spend"),
            "D1  wall open  rounds 0  send-backs 0  threads not recorded\n" + FOOTER,
        )
    def test_a_merged_item_with_no_close_row_prints_wall_not_recorded(self):
        self.fill([self.dish(state="merged")], logs=[self.close(state="in-progress")])
        self.assertEqual(
            self.brigade("spend"),
            "D1  wall not recorded  rounds 0  send-backs 0  threads not recorded\n" + FOOTER,
        )
    def test_a_reviewer_row_does_not_print_the_item_wall(self):
        self.fill(
            [self.dish()],
            passes=[{"at": FIRE, "dish": "D1", "verdict": "pass", "author": "claudeAgent/claude-opus-4-6",
                     "verifier": "grok/grok-4"}],
            logs=[self.close()],
        )
        self.assertEqual(
            self.brigade("spend", "--by", "reviewer"),
            "grok/grok-4  wall not recorded  rounds 1  send-backs 0  threads not recorded\n" + FOOTER,
        )
    def test_json_wall_is_whole_seconds(self):
        self.fill([self.dish()], logs=[self.close()])
        report = json.loads(self.brigade("spend", "--json"))
        self.assertEqual(report, {
            "by": "item",
            "rows": [{"key": "D1", "wall": 7200, "rounds": 0, "send_backs": 0, "threads": "not recorded"}],
            "threads_without_a_token_source": "not requested",
        })
    def test_a_goal_row_sums_its_items(self):
        self.fill(
            [self.dish("D1"), self.dish("D2", at=FIRE)],
            passes=[
                {"at": FIRE, "dish": "D1", "verdict": "pass", "author": "a", "verifier": "b"},
                {"at": FIRE, "dish": "D2", "verdict": "pass", "author": "a", "verifier": "b"},
                {"at": CLOSE, "dish": "D2", "verdict": "pass", "author": "a", "verifier": "b"},
            ],
            threads=[
                {"at": FIRE, "dish": "D1", "role": "worker", "thread": "ledger-a", "seat": "grok/grok-4"},
                {"at": FIRE, "dish": "D2", "role": "worker", "thread": "ledger-b", "seat": "grok/grok-4"},
            ],
            logs=[self.close("D1", CLOSE), self.close("D2", HOUR)],
            goals=[{"id": "G1", "at": FIRE, "state": "open", "name": "goal name sentinel", "purpose": "why", "done": "when"}],
        )
        self.assertEqual(
            self.brigade("spend", "--by", "goal"),
            "G1  wall 3h  rounds 3  send-backs 0  threads 2\n" + FOOTER,
        )
    def test_a_shared_thread_stays_in_both_counts_and_is_not_named(self):
        self.fill(
            [self.dish("D1"), self.dish("D2")],
            threads=[
                {"at": FIRE, "dish": "D1", "role": "worker", "thread": "ledger-shared", "seat": "grok/grok-4"},
                {"at": FIRE, "dish": "D2", "role": "worker", "thread": "ledger-shared", "seat": "grok/grok-4"},
            ],
            logs=[self.close("D1", CLOSE), self.close("D2", HOUR)],
        )
        code, out, err = self.streams("spend")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            out.strip(),
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "D2  wall 1h  rounds 0  send-backs 0  threads 1\n" + FOOTER,
        )
        self.assertEqual(err.strip(), "brigade: warning: D1 and D2 share a ledger thread")
        self.assertNotIn("ledger-shared", out + err)
    def test_the_model_key_is_one_worker_seat(self):
        cases = (
            ("seat on the ledger", {"at": FIRE, "dish": "D1", "role": "worker", "thread": "ledger-1",
                                    "seat": "claudeAgent/claude-opus-4-6"}, (), "claude-opus-4-6", 0),
            ("author when the ledger seat is empty",
             {"at": FIRE, "dish": "D1", "role": "worker", "thread": "ledger-1", "seat": ""},
             ({"at": FIRE, "dish": "D1", "verdict": "pass", "author": "codex/gpt-6.1-sol", "verifier": "grok/grok-4"},),
             "gpt-6.1-sol", 1),
            ("no seat", {"at": FIRE, "dish": "D1", "role": "reviewer", "thread": "ledger-1", "seat": "grok/grok-4"},
             (), "seat not recorded", 0),
        )
        for name, thread, passes, key, rounds in cases:
            with self.subTest(name=name):
                self.fill([self.dish()], passes=passes, threads=[thread], logs=[self.close()])
                self.assertEqual(
                    self.brigade("spend", "--by", "model"),
                    f"{key}  wall 2h  rounds {rounds}  send-backs 0  threads 1\n" + FOOTER,
                )
    def test_several_worker_seats_print_seats_differ(self):
        self.fill([self.dish()], threads=[
            {"at": FIRE, "dish": "D1", "role": "worker", "thread": "ledger-1", "seat": "grok/grok-4"},
            {"at": FIRE, "dish": "D1", "role": "fix worker", "thread": "ledger-2", "seat": "codex/gpt-6.1-sol"},
        ], logs=[self.close()])
        self.assertEqual(
            self.brigade("spend", "--by", "model"),
            "seats differ  wall 2h  rounds 0  send-backs 0  threads 2\n" + FOOTER,
        )
