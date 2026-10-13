import os
import runpy
import sqlite3
from pathlib import Path

from .support import (
    setUpModule,
    BrigadeTest,
    SCRIPT,
    json,
    subprocess,
    sys,
)


FIRE = "2026-10-01T00:00:00+00:00"
CLOSE = "2026-10-01T02:00:00+00:00"
HOUR = "2026-10-01T01:00:00+00:00"
FOOTER = "tokens not requested. Pass --tokens to read them."


class SpendTest(BrigadeTest):
    def fill(self, dishes, passes=(), threads=(), logs=(), goals=()):
        self.open()
        self.put("dishes.tsv", dishes)
        self.put("pass.tsv", passes)
        self.put("threads.tsv", threads)
        self.put("log.tsv", logs)
        if goals:
            self.put("goals.tsv", goals)

    def put(self, table, rows):
        path = self.at / table
        header = path.read_text().splitlines()[0].split("\t")
        lines = ["\t".join(header)]
        for row in rows:
            lines.append("\t".join(str(row.get(column, "")) for column in header))
        path.write_text("\n".join(lines) + "\n")

    def dish(self, ident="D1", **cells):
        row = {
            "id": ident, "at": FIRE, "state": "merged", "kind": "fix", "goal": "G1",
            "thread": "dish-thread-sentinel", "summary": "summary sentinel",
        }
        row.update(cells)
        return row

    def close(self, ident="D1", at=CLOSE, state="merged"):
        return {"at": at, "kind": "dish", "id": ident, "state": state, "note": "note sentinel"}

    def streams(self, *args, env=None):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at), *args],
            capture_output=True, text=True, env=env,
        )
        return result.returncode, result.stdout, result.stderr

    def token_env(self, claude=None):
        home = Path(self.temporary.name) / "home"
        home.mkdir(exist_ok=True)
        if claude is None:
            claude = Path(self.temporary.name) / "claude"
            claude.mkdir(exist_ok=True)
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["CLAUDE_CONFIG_DIR"] = str(claude)
        env.pop("T3CODE_HOME", None)
        return env

    def database(self, providers=(), turns=(), *, tables=True, indexed=True):
        base = Path(self.temporary.name) / "t3"
        path = base / "userdata" / "statev2.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            extra = Path(str(path) + suffix)
            if extra.exists():
                extra.unlink()
        connection = sqlite3.connect(path)
        try:
            if tables:
                connection.execute(
                    "CREATE TABLE orchestration_v2_projection_provider_threads "
                    "(thread_id TEXT, provider_thread_id TEXT, driver TEXT, payload_json TEXT)"
                )
                connection.execute(
                    "CREATE TABLE orchestration_v2_projection_provider_turns "
                    "(provider_thread_id TEXT, payload_json TEXT)"
                )
                if indexed:
                    connection.execute(
                        "CREATE INDEX provider_threads_thread "
                        "ON orchestration_v2_projection_provider_threads(thread_id)"
                    )
                    connection.execute(
                        "CREATE INDEX provider_turns_thread "
                        "ON orchestration_v2_projection_provider_turns(provider_thread_id)"
                    )
                else:
                    connection.execute(
                        "CREATE INDEX provider_threads_driver "
                        "ON orchestration_v2_projection_provider_threads(driver)"
                    )
                connection.executemany(
                    "INSERT INTO orchestration_v2_projection_provider_threads "
                    "(thread_id, provider_thread_id, driver, payload_json) VALUES (?, ?, ?, ?)",
                    providers,
                )
                connection.executemany(
                    "INSERT INTO orchestration_v2_projection_provider_turns "
                    "(provider_thread_id, payload_json) VALUES (?, ?)",
                    turns,
                )
            connection.commit()
        finally:
            connection.close()
        return base

    def provider(self, thread, provider, driver, native=None, **extra):
        payload = dict(extra)
        if native is not None:
            payload["nativeThreadRef"] = {"nativeId": native}
        return (thread, provider, driver, json.dumps(payload))

    def turn(self, provider, incoming, outgoing, cached=0, created=0, reasoning=0, status="complete", token_usage=None):
        body = {"turnTokenUsage": {
            "usageStatus": status,
            "inputTokens": incoming,
            "cachedInputTokens": cached,
            "cacheCreationTokens": created,
            "outputTokens": outgoing,
            "reasoningTokens": reasoning,
        }}
        if token_usage is not None:
            body["tokenUsage"] = token_usage
        return (provider, json.dumps(body))

    def assistant(self, ident, model, incoming, outgoing, cache_read=None, cache_write=None, content=None):
        usage = {"input_tokens": incoming, "output_tokens": outgoing}
        if cache_read is not None:
            usage["cache_read_input_tokens"] = cache_read
        if cache_write is not None:
            usage["cache_creation_input_tokens"] = cache_write
        message = {"id": ident, "model": model, "usage": usage}
        if content is not None:
            message["content"] = [{"type": "text", "text": content}]
        return json.dumps({"type": "assistant", "message": message})

    def write_claude(self, native, session_lines, subagent_lines=()):
        base = Path(self.temporary.name) / "claude"
        project = base / "projects" / "work"
        project.mkdir(parents=True, exist_ok=True)
        (project / f"{native}.jsonl").write_text("\n".join(session_lines) + "\n")
        if subagent_lines:
            folder = project / native / "subagents"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "child.jsonl").write_text("\n".join(subagent_lines) + "\n")
        return base

    def ledger(self, thread="ledger-1", seat="codex/gpt-6.1-sol"):
        return {"at": FIRE, "dish": "D1", "role": "worker", "thread": thread, "seat": seat}

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
            "not json secret fragment",
            self.assistant("msgOk1", "claude-opus-4-6", 10, 4, content="secret message text"),
            self.assistant("msgPriv99", "secret model sentinel", 6, 6, content="secret message text"),
        ])
        base = self.database([
            self.provider("ledger-priv", "prov-1", "claudeAgent", native, provider_session_id="native-session-sentinel"),
        ])
        code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env(claude))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            out,
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "  not recorded\n"
            "threads without a token source 1\n",
        )
        for secret in (
            "note sentinel", native, "secret model sentinel", "secret message text",
            "msgPriv99", "msgOk1", "not json secret fragment", "native-session-sentinel",
            "summary sentinel", "dish-thread-sentinel",
        ):
            self.assertNotIn(secret, out + err)

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
            "  claude-opus-4-6  in 13  cache-read 4  cache-write 1  out 27  reasoning 0\n"
            "threads without a token source 0\n",
        )
        self.assertNotIn("999", out + err)
        self.assertNotIn(native, out + err)

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
            "  claude-haiku-4-5  in 8  cache-read 0  cache-write 0  out 2  reasoning 0\n"
            "  claude-opus-4-6  in 1000  cache-read 2000  cache-write 500  out 250  reasoning 0  $0.012000\n"
            "  claude-sonnet-4-6  in 4  cache-read 1  cache-write 1  out 3  reasoning 0\n"
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
        home = Path(self.temporary.name) / "home"
        home.mkdir(exist_ok=True)
        empty = Path(self.temporary.name) / "empty-t3"
        empty.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["T3CODE_HOME"] = str(empty)
        env["CLAUDE_CONFIG_DIR"] = str(Path(self.temporary.name) / "claude-empty")
        code, out, err = self.streams("spend", env=env)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            out,
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n" + FOOTER + "\n",
        )
        self.assertEqual(err, "")
        self.assertNotIn("could not be opened", out + err)
        self.assertFalse((empty / "userdata" / "statev2.sqlite").exists())
        self.assertFalse((home / ".t3").exists())
