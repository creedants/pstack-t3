import contextlib
import io
import os
import runpy
import sqlite3
from pathlib import Path

from .support import (
    setUpModule,
    BrigadeTest,
    SCRIPT,
    call,
    json,
    subprocess,
    sys,
)


FIRE = "2026-10-01T00:00:00+00:00"
CLOSE = "2026-10-01T02:00:00+00:00"
HOUR = "2026-10-01T01:00:00+00:00"
FOOTER = "tokens not requested. Pass --tokens to read them."
_OMIT = object()
_EMPTY_DETAILS = object()


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

    def database(self, providers=(), turns=(), *, threads=(), tables=True, indexed=True, thread_table="complete"):
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
                if thread_table != "absent":
                    columns = {
                        "complete": "thread_id TEXT PRIMARY KEY, payload_json TEXT",
                        "no_thread_id": "payload_json TEXT",
                        "no_payload_json": "thread_id TEXT PRIMARY KEY",
                        "no_index": "thread_id TEXT, payload_json TEXT",
                    }[thread_table]
                    connection.execute(
                        "CREATE TABLE orchestration_v2_projection_threads "
                        f"({columns})"
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
                if thread_table in ("complete", "no_index"):
                    connection.executemany(
                        "INSERT INTO orchestration_v2_projection_threads "
                        "(thread_id, payload_json) VALUES (?, ?)",
                        threads,
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

    def selected(self, thread, model, instance="codex"):
        """One row of the `thread_id` and `payload_json` columns that `spend` reads from T3's thread projection, with the thread's recorded model selection in the payload."""
        payload = {"modelSelection": {"instanceId": instance, "model": model}}
        return (thread, json.dumps(payload))

    def assistant(self, ident, model, incoming, outgoing, cache_read=None, cache_write=None, content=None, thinking=_OMIT):
        usage = {"input_tokens": incoming, "output_tokens": outgoing}
        if cache_read is not None:
            usage["cache_read_input_tokens"] = cache_read
        if cache_write is not None:
            usage["cache_creation_input_tokens"] = cache_write
        if thinking is _EMPTY_DETAILS:
            usage["output_tokens_details"] = {}
        elif thinking is not _OMIT:
            usage["output_tokens_details"] = {"thinking_tokens": thinking}
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

    def priced_opus(self, lines):
        native = "sessThink1"
        self.fill([self.dish()], threads=[self.ledger(seat="claudeAgent/claude-opus-4-6")], logs=[self.close()])
        claude = self.write_claude(native, lines)
        (self.at / "prices.json").write_text(json.dumps({
            "claude-opus-4-6": {"input": 3, "cache_read": 1.5, "cache_write": 6, "output": 12},
        }))
        base = self.database([self.provider("ledger-1", "prov-1", "claudeAgent", native)])
        code, out, err = self.streams("spend", "--tokens", "--t3-home", str(base), env=self.token_env(claude))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(err, "")
        return out

    def opus_report(self, reasoning):
        shown = "not recorded" if reasoning is None else reasoning
        return (
            "D1  wall 2h  rounds 0  send-backs 0  threads 1\n"
            "  claude-opus-4-6  in 1000  cache-read 2000  cache-write 500  out 250  "
            f"reasoning {shown}  $0.012000\n"
            "threads without a token source 0\n"
        )

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
