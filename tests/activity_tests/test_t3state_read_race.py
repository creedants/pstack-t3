import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .support import MOD, setUpModule

OLD_TITLE = "old title " + "a" * 9000
NEW_TITLE = "new title " + "b" * 9000
START = "2026-10-01T00:00:00.000Z"
END = "2026-10-01T00:10:00.000Z"
PAYLOAD = json.dumps({"modelSelection": {"model": "m", "instanceId": "i"}})

TABLES = """
create table orchestration_v2_projection_metadata (projection_name text, schema_version integer);
create table orchestration_v2_projection_threads (thread_id text, title text, default_provider text, payload_json text, project_id text);
create table orchestration_v2_projection_runs (thread_id text, status text, requested_at text, completed_at text);
create table orchestration_v2_projection_subagents (subagent_id text, thread_id text, child_thread_id text, status text, started_at text, completed_at text);
"""


class T3StateReadRaceTest(unittest.TestCase):
    """An activity read that a writer replaces mid-read returns one committed title, never a splice."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def database(self):
        """T3's database with a parent and a child thread whose title spans pages, closed so it leaves no log."""
        path = self.directory / "statev2.sqlite"
        writer = sqlite3.connect(str(path), isolation_level=None)
        writer.executescript(TABLES)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("insert into orchestration_v2_projection_metadata values (?, ?)", ("thread-projections", 2))
        writer.execute("insert into orchestration_v2_projection_threads values (?, ?, ?, ?, ?)",
                       ("parent-thread", "parent", "opencode", PAYLOAD, "project"))
        writer.execute("insert into orchestration_v2_projection_threads values (?, ?, ?, ?, ?)",
                       ("child-thread", OLD_TITLE, "opencode", PAYLOAD, "project"))
        writer.execute("insert into orchestration_v2_projection_runs values (?, ?, ?, ?)",
                       ("child-thread", "completed", START, END))
        writer.execute("insert into orchestration_v2_projection_subagents values (?, ?, ?, ?, ?, ?)",
                       ("node-1", "parent-thread", "child-thread", "completed", START, None))
        writer.close()
        self.assertFalse(Path(f"{path}-wal").exists())
        return path

    def test_a_writer_that_replaces_a_multipage_title_mid_read_never_leaves_a_spliced_title_fails_when_the_read_takes_no_lock(self):
        path = self.database()
        swapped = []

        def replace():
            writer = sqlite3.connect(str(path), isolation_level=None)
            writer.execute("update orchestration_v2_projection_threads set title = ? where thread_id = ?",
                           (NEW_TITLE, "child-thread"))
            writer.close()

        class Hook(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                cursor = super().execute(sql, *args, **kwargs)
                if "payload_json" in sql and not swapped:
                    replace()
                    swapped.append(True)
                return cursor

        def connect(*args, **kwargs):
            return sqlite3.connect(*args, factory=Hook, **kwargs)

        shim = mock.Mock(wraps=sqlite3)
        shim.connect = connect
        with mock.patch.dict(MOD["open_t3"].__globals__, {"sqlite3": shim}):
            connection = MOD["open_t3"](path)
            state = MOD["read_t3"](connection, MOD["Window"](0, 2e9), {"parent-thread"})
            connection.close()
        self.assertEqual(len(swapped), 1)
        self.assertIn(state.agents["child-thread"].title, (OLD_TITLE, NEW_TITLE),
                      f"the read returned a title that is neither committed one ({len(state.agents['child-thread'].title)} characters)")
