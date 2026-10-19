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
    def test_a_shared_thread_adds_no_tokens(self):
        self.fill(
            [self.dish("D1"), self.dish("D2")],
            threads=[
                {"at": FIRE, "dish": "D1", "role": "worker", "thread": "ledger-shared", "seat": "codex/gpt-6.1-sol"},
                {"at": FIRE, "dish": "D2", "role": "worker", "thread": "ledger-shared", "seat": "codex/gpt-6.1-sol"},
            ],
            logs=[self.close("D1", CLOSE), self.close("D2", HOUR)],
        )
        base = self.database(
            [self.provider("ledger-shared", "prov-1", "codex")],
            [self.turn("prov-1", 77, 77)],
        )
        code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env())
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            out,
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "  not recorded\n"
            "D2  wall 1h  rounds 0  send-backs 0  threads 1\n"
            "  not recorded\n"
            "threads without a token source 1\n",
        )
        self.assertEqual(err.strip(), "brigade: warning: D1 and D2 share a ledger thread")
        self.assertNotIn("ledger-shared", out + err)
        self.assertNotIn("77", out + err)
        self.assertNotIn("could not be opened", err)
    def test_a_default_run_does_not_open_the_database(self):
        self.fill([self.dish()], threads=[self.ledger()], logs=[self.close()])
        base = self.database()
        database = base / "userdata" / "statev2.sqlite"
        loaded = runpy.run_path(str(SCRIPT))
        original = runpy.run_path

        def patched(path_name, init_globals=None, run_name=None):
            module = original(path_name, init_globals, run_name)
            if Path(path_name).name == "activity.py":
                def open_t3(_database):
                    raise AssertionError("open_t3")
                module["open_t3"] = open_t3
            return module

        stdout, stderr = io.StringIO(), io.StringIO()
        runpy.run_path = patched
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = loaded["main"]([
                    "--store", str(self.store), "--at", str(self.at),
                    "spend", "--t3-home", str(base),
                ])
        finally:
            runpy.run_path = original
        self.assertEqual(code, 0)
        self.assertEqual(
            stdout.getvalue(),
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n" + FOOTER + "\n",
        )
        self.assertEqual(stderr.getvalue(), "")
        self.assertTrue(database.is_file())
    def test_missing_reasoning_stays_missing_through_the_sum_and_a_recorded_zero_stays_zero(self):
        space = runpy.run_path(str(SCRIPT))["spend_text"].__globals__
        counts = space["_Counts"]
        merge = space["_merge"]
        line = space["_model_line"]
        rendered = space["_json_models"]
        cases = (
            (None, None, None, "not recorded", "not recorded"),
            (None, 0, None, "not recorded", "not recorded"),
            (None, 11, None, "not recorded", "not recorded"),
            (0, None, None, "not recorded", "not recorded"),
            (0, 0, 0, "0", 0),
            (0, 11, 11, "11", 11),
            (7, None, None, "not recorded", "not recorded"),
            (7, 0, 7, "7", 7),
            (7, 11, 18, "18", 18),
        )
        for left, right, summed, text, json_reasoning in cases:
            with self.subTest(left=left, right=right):
                got = merge([
                    {"m": counts(1, 2, 3, 4, left)},
                    {"m": counts(1, 2, 3, 4, right)},
                ])["m"]
                self.assertEqual(
                    (got.reasoning, got.incoming, got.cache_read, got.cache_write, got.outgoing),
                    (summed, 2, 4, 6, 8),
                )
                self.assertEqual(
                    line("m", got, None),
                    f"  m  in 2  cache-read 4  cache-write 6  out 8  reasoning {text}",
                )
                self.assertEqual(rendered((("m", got),), {}), [{
                    "model": "m", "in": 2, "cache_read": 4, "cache_write": 6, "out": 8,
                    "reasoning": json_reasoning,
                }])
    def test_an_invalid_model_name_stays_off_both_streams(self):
        native = "sessHidden"
        model = "bad model name"
        content = "hidden model body"
        ident = "msgHidden9"
        self.fill(
            [self.dish()],
            threads=[self.ledger(seat="claudeAgent/claude-opus-4-6")],
            logs=[self.close()],
        )
        claude = self.write_claude(native, [
            self.assistant(ident, model, 10, 4, content=content),
            self.assistant("msgLater41", "claude-opus-4-6", 41, 43, cache_read=17, cache_write=19),
        ])
        base = self.database([
            self.provider("ledger-1", "prov-1", "claudeAgent", native, provider_session_id="native-session-sentinel"),
        ])
        argv = ["--store", str(self.store), "--at", str(self.at), "spend", "--tokens", "--t3-home", str(base)]
        env = self.token_env(claude)
        outcome = call(self, SCRIPT, argv, env=env)
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stderr_text(), "")
        self.assertEqual(
            outcome.stdout_text(),
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "  not recorded\n"
            "threads without a token source 1\n",
        )
        json_outcome = call(self, SCRIPT, [*argv, "--json"], env=env)
        self.assertEqual(json_outcome.code, 0, json_outcome.stdout_text() + json_outcome.stderr_text())
        self.assertEqual(json_outcome.stderr_text(), "")
        report = json.loads(json_outcome.stdout_text())
        self.assertEqual(report["rows"][0]["models"], "not recorded")
        self.assertEqual(report["threads_without_a_token_source"], 1)
        for secret in (model, content, ident, native, "native-session-sentinel", "in 41", "out 43"):
            self.assertNotIn(secret, outcome.stdout_text())
            self.assertNotIn(secret, outcome.stderr_text())
            self.assertNotIn(secret, json_outcome.stdout_text())
            self.assertNotIn(secret, json_outcome.stderr_text())
    def test_thinking_tokens_print_as_reasoning(self):
        out = self.priced_opus([
            self.assistant("think-7", "claude-opus-4-6", 1000, 250, cache_read=2000, cache_write=500, thinking=7),
        ])
        self.assertEqual(out, self.opus_report(7))
    def test_zero_thinking_tokens_stay_zero(self):
        out = self.priced_opus([
            self.assistant("think-0", "claude-opus-4-6", 1000, 250, cache_read=2000, cache_write=500, thinking=0),
        ])
        self.assertEqual(out, self.opus_report(0))
    def test_a_missing_empty_details_null_negative_bool_or_non_integer_thinking_count_stays_not_recorded(self):
        cases = (
            ("missing", _OMIT),
            ("empty-details", _EMPTY_DETAILS),
            ("null", None),
            ("bool", True),
            ("negative", -1),
            ("non-integer", 1.5),
        )
        for name, thinking in cases:
            with self.subTest(name=name):
                kwargs = {} if thinking is _OMIT else {"thinking": thinking}
                out = self.priced_opus([
                    self.assistant("think-bad", "claude-opus-4-6", 1000, 250, cache_read=2000, cache_write=500, **kwargs),
                ])
                self.assertEqual(out, self.opus_report(None))
    def test_a_recorded_count_plus_a_missing_count_stays_not_recorded(self):
        out = self.priced_opus([
            self.assistant("think-a", "claude-opus-4-6", 600, 100, cache_read=1500, cache_write=200, thinking=7),
            self.assistant("think-b", "claude-opus-4-6", 400, 150, cache_read=500, cache_write=300),
        ])
        self.assertEqual(out, self.opus_report(None))
