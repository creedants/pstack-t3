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
    def test_an_opencode_turn_reports_the_thread_model_selection(self):
        self.fill([self.dish()], threads=[self.ledger(seat="opencode/opencode-go")], logs=[self.close()])
        base = self.database(
            [self.provider("ledger-1", "prov-1", "opencode")],
            [self.turn("prov-1", 100, 20, cached=80, created=5, reasoning=4)],
            threads=[self.selected("ledger-1", "opencode-go/step-5-preview-free", instance="opencode")],
        )
        code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env())
        self.assertEqual(code, 0, out + err)
        self.assertEqual(err, "")
        self.assertEqual(
            out,
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "  opencode-go/step-5-preview-free  in 15  cache-read 80  cache-write 5  out 20  reasoning 4\n"
            "threads without a token source 0\n",
        )
    def test_a_thread_model_that_is_missing_empty_or_not_a_model_id_stays_model_not_recorded(self):
        cases = (
            ("no selection row", ()),
            ("null model", (None,)),
            ("empty model", ("",)),
            ("model with a space", ("gpt 6",)),
            ("model as a number", (7,)),
        )
        for name, models in cases:
            with self.subTest(name=name):
                self.fill([self.dish()], threads=[self.ledger()], logs=[self.close()])
                base = self.database(
                    [self.provider("ledger-1", "prov-1", "codex")],
                    [self.turn("prov-1", 100, 20, cached=80, created=5, reasoning=4)],
                    threads=[self.selected("ledger-1", model) for model in models],
                )
                code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env())
                self.assertEqual(code, 0, out + err)
                self.assertEqual(err, "")
                self.assertEqual(
                    out,
                    "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
                    "  model not recorded  in 15  cache-read 80  cache-write 5  out 20  reasoning 4\n"
                    "threads without a token source 0\n",
                )
    def test_an_unreadable_thread_table_keeps_every_token_total(self):
        native = "sessUnreadable1"
        cases = (
            ("table dropped", "absent"),
            ("no thread_id", "no_thread_id"),
            ("no payload_json", "no_payload_json"),
            ("no index", "no_index"),
        )
        for name, thread_table in cases:
            with self.subTest(name=name):
                self.fill(
                    [self.dish()],
                    threads=[
                        self.ledger("ledger-turn", "codex/gpt-6.1-sol"),
                        self.ledger("ledger-claude", "claudeAgent/claude-opus-4-6"),
                    ],
                    logs=[self.close()],
                )
                claude = self.write_claude(native, [
                    self.assistant("msgUnreadable1", "claude-opus-4-6", 10, 4),
                ])
                base = self.database(
                    [
                        self.provider("ledger-turn", "prov-1", "codex"),
                        self.provider("ledger-claude", "prov-2", "claudeAgent", native),
                    ],
                    [self.turn("prov-1", 100, 20, cached=80, created=5, reasoning=4)],
                    threads=[self.selected("ledger-turn", "gpt-6.1-sol")],
                    thread_table=thread_table,
                )
                code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env(claude))
                self.assertEqual(code, 0, out + err)
                self.assertEqual(err, "")
                self.assertEqual(
                    out,
                    "D1  wall 2h  rounds 0  send-backs 0  threads 2\n"
                    "  claude-opus-4-6  in 10  cache-read 0  cache-write 0  out 4  reasoning not recorded\n"
                    "  model not recorded  in 15  cache-read 80  cache-write 5  out 20  reasoning 4\n"
                    "threads without a token source 0\n",
                )
    def test_a_partial_turn_prints_not_recorded(self):
        self.fill([self.dish()], threads=[self.ledger()], logs=[self.close()])
        base = self.database(
            [self.provider("ledger-1", "prov-1", "codex")],
            [self.turn("prov-1", 100, 20, cached=80), self.turn("prov-1", 25, 25, status="partial")],
        )
        code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env())
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            out,
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "  not recorded\n"
            "threads without a token source 1\n",
        )
        self.assertNotIn("20", out)
        self.assertNotIn("25", out)
    def test_claude_output_is_the_deduped_log_total(self):
        native = "sessDedupe1"
        self.fill([self.dish()], threads=[self.ledger(seat="claudeAgent/claude-opus-4-6")], logs=[self.close()])
        claude = self.write_claude(
            native,
            [self.assistant("m1", "claude-opus-4-6", 10, 20, cache_read=4, cache_write=1)],
            [
                self.assistant("m1", "claude-opus-4-6", 99, 5, cache_read=50, cache_write=9),
                self.assistant("m2", "claude-opus-4-6", 3, 7),
            ],
        )
        base = self.database(
            [self.provider("ledger-1", "prov-1", "claudeAgent", native)],
            [self.turn("prov-1", 1, 999)],
        )
        code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env(claude))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            out,
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "  claude-opus-4-6  in 13  cache-read 4  cache-write 1  out 27  reasoning not recorded\n"
            "threads without a token source 0\n",
        )
        self.assertNotIn("999", out + err)
        self.assertNotIn(native, out + err)
        code, json_out, json_err = self.streams(
            "spend", "--tokens", "--json", "--t3-home", str(base), env=self.token_env(claude),
        )
        self.assertEqual(code, 0, json_out + json_err)
        report = json.loads(json_out)
        self.assertEqual(report["rows"][0]["models"], [{
            "model": "claude-opus-4-6", "in": 13, "cache_read": 4, "cache_write": 1, "out": 27,
            "reasoning": "not recorded",
        }])
        self.assertNotIn("dollars", report["rows"][0]["models"][0])
    def test_an_unknown_token_schema_still_prints_the_store(self):
        cases = (
            ("no provider tables", {"tables": False}),
            ("no usable index", {"indexed": False}),
        )
        for name, options in cases:
            with self.subTest(name=name):
                self.fill([self.dish()], threads=[self.ledger()], logs=[self.close()])
                base = self.database(**options)
                code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env())
                self.assertEqual(code, 0, out + err)
                self.assertEqual(err.strip(), "token schema is not the one this command knows")
                self.assertEqual(
                    out,
                    "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
                    "  not recorded\n"
                    "threads without a token source 1\n",
                )
                self.assertNotIn("SCAN", err)
                self.assertNotIn("SEARCH", err)
    def test_tokens_leave_the_fixture_store_and_database_unchanged(self):
        self.fill([self.dish()], threads=[self.ledger()], logs=[self.close()])
        lock = self.at / "restaurant.lock"
        if lock.exists():
            lock.unlink()
        base = self.database(
            [self.provider("ledger-1", "prov-1", "codex")],
            [self.turn("prov-1", 12, 4, cached=2, created=1, reasoning=1)],
        )
        database = base / "userdata" / "statev2.sqlite"
        before_store = self.store_files()
        before_db = database.read_bytes()
        code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env())
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            out,
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "  model not recorded  in 9  cache-read 2  cache-write 1  out 4  reasoning 1\n"
            "threads without a token source 0\n",
        )
        self.assertEqual(self.store_files(), before_store)
        self.assertEqual(database.read_bytes(), before_db)
        self.assertFalse(lock.exists())
        self.assertFalse(Path(str(database) + "-wal").exists())
        self.assertFalse(Path(str(database) + "-shm").exists())
    def test_dollars_print_only_for_a_listed_model(self):
        native = "sessBill1"
        self.fill([self.dish()], threads=[self.ledger(seat="claudeLean/claude-opus-4-6")], logs=[self.close()])
        claude = self.write_claude(native, [
            self.assistant("bill-opus", "claude-opus-4-6", 1000, 250, cache_read=2000, cache_write=500),
            self.assistant("bill-haiku", "claude-haiku-4-5", 8, 2, cache_read=0, cache_write=0),
            self.assistant("bill-sonnet", "claude-sonnet-4-6", 4, 3, cache_read=1, cache_write=1),
        ])
        (self.at / "prices.json").write_text(json.dumps({
            "claude-opus-4-6": {"input": 3, "cache_read": 1.5, "cache_write": 6, "output": 12},
            "claude-haiku-4-5": "subscription",
        }))
        base = self.database([self.provider("ledger-1", "prov-1", "claudeLean", native)])
        code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env(claude))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            out,
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "  claude-haiku-4-5  in 8  cache-read 0  cache-write 0  out 2  reasoning not recorded\n"
            "  claude-opus-4-6  in 1000  cache-read 2000  cache-write 500  out 250  reasoning not recorded  $0.012000\n"
            "  claude-sonnet-4-6  in 4  cache-read 1  cache-write 1  out 3  reasoning not recorded\n"
            "threads without a token source 0\n",
        )
    def test_a_bad_price_list_prints_no_report(self):
        self.fill([self.dish()], threads=[self.ledger()], logs=[self.close()])
        (self.at / "prices.json").write_text('{"claude-opus-4-6": {"input": 1, "output": 2}}')
        base = Path(self.temporary.name) / "t3-missing"
        code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env())
        self.assertEqual(code, 1, out + err)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), "brigade: prices.json is not a price list")
        self.assertFalse((base / "userdata" / "statev2.sqlite").exists())
    def test_a_negative_input_is_not_recorded(self):
        self.fill([self.dish()], threads=[self.ledger()], logs=[self.close()])
        base = self.database(
            [self.provider("ledger-1", "prov-1", "codex")],
            [self.turn("prov-1", 10, 4, cached=8, created=5)],
        )
        code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env())
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            out,
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "  not recorded\n"
            "threads without a token source 1\n",
        )
        self.assertNotIn("in -3", out)
        self.assertNotIn("in 10", out)
