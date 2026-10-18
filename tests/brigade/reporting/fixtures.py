from ..support import setUpModule
from ..support import BrigadeTest, Path, datetime, json, mock, os
import sqlite3

FIRE = "2026-10-01T00:00:00+00:00"
CLOSE_AT = "2026-10-01T02:00:00+00:00"


class _SpendFixture:
    """`fill`, `put`, and `dish` mirror `_Helpers` in tests/brigade/spend/fixtures.py. `dish_close` mirrors its `close`."""

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

    def dish_close(self, ident="D1", at=CLOSE_AT, state="merged"):
        return {"at": at, "kind": "dish", "id": ident, "state": state, "note": "note sentinel"}

    def goal_row(self, ident, name):
        return {"id": ident, "at": FIRE, "state": "open", "name": name, "purpose": "why", "done": "when"}

    def thread_row(self, dish, name):
        return {"at": FIRE, "dish": dish, "role": "worker", "thread": name, "seat": "codex/gpt-6.1-sol"}

    def token_database(self):
        """A database with spend's provider tables and indexes and no rows, so every thread reads as not recorded."""
        base = Path(self.temporary.name) / "t3"
        path = base / "userdata" / "statev2.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "CREATE TABLE orchestration_v2_projection_provider_threads "
                "(thread_id TEXT, provider_thread_id TEXT, driver TEXT, payload_json TEXT)")
            connection.execute(
                "CREATE TABLE orchestration_v2_projection_provider_turns "
                "(provider_thread_id TEXT, payload_json TEXT)")
            connection.execute(
                "CREATE INDEX provider_threads_thread "
                "ON orchestration_v2_projection_provider_threads(thread_id)")
            connection.execute(
                "CREATE INDEX provider_turns_thread "
                "ON orchestration_v2_projection_provider_turns(provider_thread_id)")
            connection.commit()
        finally:
            connection.close()
        return base

    def advance_report(self, stamp):
        path = self.at / "restaurant.json"
        meta = json.loads(path.read_text())
        meta["lastReportAt"] = stamp
        path.write_text(json.dumps(meta, indent=2) + "\n")

