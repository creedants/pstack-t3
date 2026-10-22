from ..support import setUpModule
from ..support import BrigadeTest, CLAUDE, CODEX, Path, ProcessReason, SCRIPT, call, call_in_process, real_process, run_globals, _owner_words, _shown_root, datetime, io, json, mock, os, runpy, subprocess, sys, timezone
from .fixtures import CLOSE_AT, FIRE, _SpendFixture
import sqlite3

class SpendReportTest(BrigadeTest, _SpendFixture):
    def spend_block(self, text):
        block = text[text.index("## Spend"):]
        return block.split("\n\n## Follow-up share")[0]
    def two_goal_store(self):
        self.fill(
            [self.dish("D1"), self.dish("D2", goal="G2")],
            threads=[self.thread_row("D1", "ledger-a"), self.thread_row("D2", "ledger-b")],
            logs=[self.dish_close("D1", CLOSE_AT), self.dish_close("D2", CLOSE_AT)],
            goals=[self.goal_row("G1", "one"), self.goal_row("G2", "two")],
        )
    def test_close_prints_a_spend_section_with_one_goal_row_per_goal(self):
        base = self.token_database()
        self.two_goal_store()
        with mock.patch.dict(os.environ, {"T3CODE_HOME": str(base)}):
            code, out, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.spend_block(out), "\n".join([
            "## Spend", "",
            "G1  wall 2h  rounds 0  send-backs 0  threads 1",
            "  not recorded",
            "G2  wall 2h  rounds 0  send-backs 0  threads 1",
            "  not recorded",
        ]))
    def test_close_prints_a_spend_none_for_a_window_with_no_items(self):
        base = self.token_database()
        self.two_goal_store()
        self.advance_report("2026-10-02T00:00:00+00:00")
        with mock.patch.dict(os.environ, {"T3CODE_HOME": str(base)}):
            code, out, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.spend_block(out), "## Spend\n\nnone")
    def test_close_keeps_the_goal_rows_and_says_tokens_unavailable_when_the_token_source_is_unreadable(self):
        empty = Path(self.temporary.name) / "no-t3-database"
        self.two_goal_store()
        with mock.patch.dict(os.environ, {"T3CODE_HOME": str(empty)}):
            code, out, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.spend_block(out), "\n".join([
            "## Spend", "",
            "G1  wall 2h  rounds 0  send-backs 0  threads 1",
            "G2  wall 2h  rounds 0  send-backs 0  threads 1",
            "tokens unavailable",
        ]))
        self.assertNotIn("  not recorded", out)
    GOAL_ROWS_NO_TOKENS = "\n".join([
        "## Spend", "",
        "G1  wall 2h  rounds 0  send-backs 0  threads 1",
        "G2  wall 2h  rounds 0  send-backs 0  threads 1",
        "tokens unavailable",
    ])
    def each_close_form(self, expected, in_process=False):
        """`close --dry-run`, `close`, and `close --to-file` each print this Spend block with empty stderr.

        A writing close advances the report window, so the window is reset to every item before each form.
        """
        runner = call_in_process if in_process else call
        for flags in (("--dry-run",), (), ("--to-file",)):
            with self.subTest(flags=flags):
                self.advance_report("")
                outcome = runner(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                *_owner_words(self.at), "close", *flags])
                self.assertEqual((outcome.code, outcome.stderr_text().strip()), (0, ""))
                text = outcome.stdout_text().strip()
                self.assertEqual(self.spend_block(Path(text).read_text().strip() if flags == ("--to-file",) else text), expected)
    def test_close_keeps_the_goal_rows_and_says_tokens_unavailable_when_the_prices_file_is_refused(self):
        base = self.token_database()
        self.two_goal_store()
        (self.at / "prices.json").write_text("{broken")
        with mock.patch.dict(os.environ, {"T3CODE_HOME": str(base)}):
            self.each_close_form(self.GOAL_ROWS_NO_TOKENS)
    def test_close_keeps_the_goal_rows_and_says_tokens_unavailable_when_the_token_read_raises(self):
        base = self.token_database()
        self.two_goal_store()
        facade = run_globals(self, SCRIPT)["report"].__globals__["spend"].__dict__
        home = facade["_open_tokens"].__globals__
        self.assertIn("_spend.tokens", facade["_open_tokens"].__module__)

        def refused(question):
            raise PermissionError("the token read was refused")

        with mock.patch.dict(os.environ, {"T3CODE_HOME": str(base)}), mock.patch.dict(home, {"_open_tokens": refused}):
            self.each_close_form(self.GOAL_ROWS_NO_TOKENS, in_process=True)
