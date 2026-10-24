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
    def test_since_keeps_dishes_fired_at_or_after_that_midnight(self):
        self.fill(
            [self.dish("D1", at="2026-10-01T23:00:00+00:00"), self.dish("D2", at="2026-10-02T00:00:00+00:00")],
            logs=[self.close("D1", "2026-10-02T01:00:00+00:00"), self.close("D2", "2026-10-02T02:00:00+00:00")],
        )
        self.assertEqual(
            self.brigade("spend", "--since", "2026-10-02"),
            "D2  wall 2h  rounds 0  send-backs 0  threads not recorded\n" + FOOTER,
        )
    def test_an_unknown_item_is_refused(self):
        self.fill([self.dish()])
        self.assertEqual(self.brigade("spend", "--item", "D9", ok=False), "brigade: no D9 in dishes.tsv")
    def test_a_bad_since_is_refused(self):
        self.open()
        self.assertEqual(self.brigade("spend", "--since", "yesterday", ok=False), "brigade: --since 'yesterday' is not YYYY-MM-DD")
    def test_a_bad_item_is_refused(self):
        self.open()
        self.assertEqual(self.brigade("spend", "--item", "nope", ok=False), "brigade: --item 'nope' is not a dish id")
    def test_spend_refuses_a_write(self):
        self.open()
        before = self.store_files()
        loaded = runpy.run_path(str(SCRIPT))
        proxy = loaded["spend_text"].__globals__["_Store"](loaded["Restaurant"](self.at))
        with self.assertRaises(loaded["BrigadeError"]) as caught:
            proxy.append("log.tsv", {"at": FIRE})
        self.assertEqual(str(caught.exception), "spend is read-only")
        self.assertEqual(self.store_files(), before)
    def test_private_text_stays_out_of_both_streams(self):
        """run_at returns only one stream, so this test starts the process itself."""
        native = "sessAb12"
        self.fill([self.dish()], threads=[self.ledger("ledger-priv", "claudeAgent/claude-opus-4-6")], logs=[self.close()])
        claude = self.write_claude(native, [
            self.assistant("msgOk1", "claude-opus-4-6", 10, 4, content="secret message text"),
        ])
        base = self.database([
            self.provider("ledger-priv", "prov-1", "claudeAgent", native, provider_session_id="native-session-sentinel"),
        ])
        code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env(claude))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            out,
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "  claude-opus-4-6  in 10  cache-read 0  cache-write 0  out 4  reasoning not recorded\n"
            "threads without a token source 0\n",
        )
        for secret in ("secret message text", "msgOk1", native, "native-session-sentinel"):
            self.assertNotIn(secret, out)
            self.assertNotIn(secret, err)
    def test_a_malformed_claude_line_stays_off_both_streams(self):
        native = "sessBad1"
        self.fill([self.dish()], threads=[self.ledger(seat="claudeAgent/claude-opus-4-6")], logs=[self.close()])
        claude = self.write_claude(native, [
            "not json secret fragment",
            self.assistant("msgLater41", "claude-opus-4-6", 41, 43, cache_read=17, cache_write=19),
        ])
        base = self.database([self.provider("ledger-1", "prov-1", "claudeAgent", native)])
        code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env(claude))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            out,
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "  not recorded\n"
            "threads without a token source 1\n",
        )
        for secret in ("not json secret fragment", "in 41", "out 43", "cache-read 17", "cache-write 19"):
            self.assertNotIn(secret, out)
            self.assertNotIn(secret, err)
    def test_grok_and_cursor_print_not_recorded_and_not_a_zero(self):
        for driver in ("grok", "cursor"):
            with self.subTest(driver=driver):
                self.fill([self.dish()], threads=[self.ledger()], logs=[self.close()])
                base = self.database(
                    [self.provider("ledger-1", "prov-1", driver)],
                    [self.turn("prov-1", 40, 40)],
                )
                code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env())
                self.assertEqual(code, 0, out + err)
                self.assertEqual(err, "")
                self.assertEqual(
                    out,
                    "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
                    "  not recorded\n"
                    "threads without a token source 1\n",
                )
                self.assertNotIn("in 0", out)
    def test_turn_token_usage_ignores_the_larger_token_usage(self):
        self.fill([self.dish()], threads=[self.ledger(seat="opencode/opencode-go")], logs=[self.close()])
        base = self.database(
            [self.provider("ledger-1", "prov-1", "opencode", provider_session_id="native-session-sentinel")],
            [self.turn("prov-1", 100, 20, cached=80, created=5, reasoning=4, token_usage={"inputTokens": 1000000, "outputTokens": 500000})],
        )
        code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env())
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            out,
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "  model not recorded  in 15  cache-read 80  cache-write 5  out 20  reasoning 4\n"
            "threads without a token source 0\n",
        )
        self.assertNotIn("1000000", out + err)
        self.assertNotIn("500000", out + err)
        self.assertNotIn("native-session-sentinel", out + err)
        self.assertNotIn("reasoningTokens", out)
        code, json_out, json_err = self.streams(
            "spend", "--tokens", "--json", "--t3-home", str(base), env=self.token_env(),
        )
        self.assertEqual(code, 0, json_out + json_err)
        report = json.loads(json_out)
        self.assertEqual(report["rows"][0]["models"], [{
            "model": "model not recorded", "in": 15, "cache_read": 80, "cache_write": 5, "out": 20,
            "reasoning": 4,
        }])
        self.assertEqual(report["threads_without_a_token_source"], 0)
        self.assertNotIn("dollars", report["rows"][0]["models"][0])
    def test_a_codex_turn_reports_the_thread_model_selection(self):
        self.fill([self.dish()], threads=[self.ledger()], logs=[self.close()])
        base = self.database(
            [self.provider("ledger-1", "prov-1", "codex")],
            [self.turn("prov-1", 100, 20, cached=80, created=5, reasoning=4)],
            threads=[self.selected("ledger-1", "gpt-6.1-sol")],
        )
        code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env())
        self.assertEqual(code, 0, out + err)
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "  gpt-6.1-sol  in 15  cache-read 80  cache-write 5  out 20  reasoning 4\n"
            "threads without a token source 0\n",
        )
