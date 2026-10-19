from ..support import setUpModule
from ..support import BrigadeTest, SCRIPT, call, json, subprocess, sys
import contextlib
import io
import os
import runpy
import sqlite3
from pathlib import Path

FIRE = "2026-10-01T00:00:00+00:00"
CLOSE = "2026-10-01T02:00:00+00:00"
HOUR = "2026-10-01T01:00:00+00:00"
FOOTER = "tokens not requested. Pass --tokens to read them."
_OMIT = object()
_EMPTY_DETAILS = object()

class _Helpers:
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
