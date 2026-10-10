import fcntl
import hashlib
import json
import os
import re
import runpy
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "t3/added/brigade/scripts/activity.py"
MOD = runpy.run_path(str(SCRIPT))

# Every invented id and path holds one of these words, so a leak is a substring search.
MARKERS = ("threadmarker", "nodemarker", "pathmarker", "homemarker", "0a1b2c3")
COORDINATOR = "mcp:threadmarker-coordinator"
CHANGED = "this T3 build stores threads differently, so update pstack-t3"
# T3's tables as this change reads them, written out here so a wrong name in the script cannot also be in the fixture.
SCHEMA = {
    "orchestration_v2_projection_metadata": ("projection_name", "schema_version"),
    "orchestration_v2_projection_threads": ("thread_id", "title", "default_provider", "payload_json"),
    "orchestration_v2_projection_runs": ("thread_id", "status", "requested_at", "completed_at"),
    "orchestration_v2_projection_subagents": ("subagent_id", "thread_id", "child_thread_id", "status", "started_at", "completed_at"),
}
UNIT_COLUMNS = ("id", "at", "state", "station", "tickets", "task", "thread", "branch", "pr", "sha", "summary", "timebox", "lease", "paths", "reported")
LOG_COLUMNS = ("at", "kind", "id", "state", "note")


def worker(number):
    return f"mcp:threadmarker-worker-{number}"


def native(number):
    return f"thread:provider:threadmarker-native-{number}"


def delegated(parent, request):
    """A delegated child's thread id, in the form T3 writes it."""
    return "thread:delegated-task:command%3A" + quote(parent, safe="") + "%3Adelegate-task%3A" + quote(request, safe="")


def listing(directory):
    """Each file's name and the SHA-256 of its bytes."""
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(Path(directory).iterdir()) if path.is_file()}


def live_listing(directory, files=None):
    """listing() with the -shm file's bytes left out. Every SQLite reader of a live database writes its read mark there."""
    files = listing(directory) if files is None else files
    return {name: "" if name.endswith("-shm") else digest for name, digest in files.items()}


class Fixture:
    """A temp store and a temp T3 base directory. Times are minutes before `now`."""

    def __init__(self, root):
        self.root = Path(root)
        self.store = self.root / "pathmarker state" / "proj" / "kit"
        self.home = self.root / "homemarker"
        self.base = self.root / "pathmarker t3 base"
        self.database = self.base / "userdata" / "statev2.sqlite"
        self.meta = {"restaurant": "kit", "projectRoot": str(self.root / "pathmarker checkout" / "proj"), "thread": COORDINATOR}
        self.now = time.time()
        self.units, self.log, self.threads, self.turns, self.delegations = [], [], [], [], []
        self.writer = None

    def stamp(self, minutes):
        return None if minutes is None else datetime.fromtimestamp(self.now - minutes * 60, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def unit(self, ident, state="in-progress", summary="", thread="", task="", pr=""):
        row = dict.fromkeys(UNIT_COLUMNS, "")
        row.update(id=ident, at=self.stamp(600), state=state, summary=summary, thread=thread, task=task, pr=pr)
        self.units.append(row)

    def retired(self, ident, thread):
        self.log.append({"at": self.stamp(500), "kind": "worker", "id": ident, "state": "retired", "note": thread})

    def thread(self, ident, title="", provider="claudeAgent", model="model-a", parent=None, turns=(), delegation=None, payload=None):
        """Add a thread with its turns, each (status, start, end). A thread with a parent also gets a delegation.

        Unless `delegation` gives (status, start, end), it runs from the first turn's start to the last turn's end with the last turn's status.
        Returns the delegation's sub-agent id, or None.
        """
        self.threads.append((ident, title, provider, json.dumps({"modelSelection": {"model": model}}) if payload is None else payload))
        self.turns += [(ident, status, self.stamp(start), self.stamp(end)) for status, start, end in turns]
        if parent is None:
            return None
        if delegation is None:
            end = turns[-1][2] if turns else None
            delegation = (turns[-1][0] if turns and end is not None else "running", turns[0][1] if turns else 30, end)
        node = f"node:delegated-task:nodemarker-{len(self.delegations)}"
        self.delegations.append((node, parent, ident, delegation[0], self.stamp(delegation[1]), self.stamp(delegation[2])))
        return node

    def coordinator(self, turns=(("running", 5, None),), **fields):
        self.thread(COORDINATOR, title="Coordinator pathmarker", turns=turns, **fields)

    def write_store(self):
        self.store.mkdir(parents=True, exist_ok=True)
        (self.store / "restaurant.json").write_text(json.dumps(self.meta))
        for name, columns, rows in (("dishes.tsv", UNIT_COLUMNS, self.units), ("log.tsv", LOG_COLUMNS, self.log)):
            lines = ["\t".join(columns)] + ["\t".join(row[column] for column in columns) for row in rows]
            (self.store / name).write_text("\n".join(lines) + "\n")

    def write_database(self, schema=SCHEMA, version=2, live=False):
        """Write T3's database in WAL mode. Closed, it has no -wal or -shm file. With live, a writer stays connected and both files exist."""
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        for table, columns in schema.items():
            connection.execute(f"create table {table} ({', '.join(columns)})")
        rows = {
            "orchestration_v2_projection_metadata": [("thread-projections", version)] if version is not None else [],
            "orchestration_v2_projection_threads": self.threads,
            "orchestration_v2_projection_runs": self.turns,
            "orchestration_v2_projection_subagents": self.delegations,
        }
        for table, columns in schema.items():
            if columns == SCHEMA.get(table):
                connection.executemany(f"insert into {table} values ({', '.join('?' * len(columns))})", rows[table])
        if live:
            self.writer = connection
        else:
            connection.close()

    def write(self, **database):
        self.write_store()
        self.write_database(**database)
        return self

    def close(self):
        if self.writer is not None:
            self.writer.close()

    def command(self, *args, at=True, t3_home=True, env=None):
        """The words and keyword arguments that run the script. --at and --t3-home name this fixture unless given, and None leaves one out."""
        words = [sys.executable, str(SCRIPT)]
        words += ["--at", str(self.store)] if at is True else ["--at", str(at)] if at else []
        words += ["--t3-home", str(self.base)] if t3_home is True else ["--t3-home", str(t3_home)] if t3_home else []
        environment = {"HOME": str(self.home), "PATH": os.environ.get("PATH", ""), **(env or {})}
        return [*words, *args], {"text": True, "env": environment, "cwd": self.root}

    def run(self, *args, **how):
        words, options = self.command(*args, **how)
        return subprocess.run(words, capture_output=True, **options)

    def start(self, *args, **how):
        words, options = self.command(*args, **how)
        return subprocess.Popen(words, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)

    def read(self, hours=3.0):
        """read_store and read_t3 over this fixture, as (Store, T3, Window)."""
        store = MOD["read_store"](self.store)
        window = MOD["Window"](self.now - hours * 3600, self.now)
        connection = MOD["open_t3"](self.database)
        try:
            MOD["check_shape"](connection)
            return store, MOD["read_t3"](connection, window, MOD["roots_of"](store)), window
        finally:
            connection.close()


class ActivityCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.fixture = Fixture(directory.name)
        self.addCleanup(self.fixture.close)

    def fails(self, status, *args, **how):
        """The one line a failed run prints, after checking its status and that stdout is empty."""
        result = self.fixture.run(*args, **how)
        self.assertEqual((result.returncode, result.stdout), (status, ""), result.stderr)
        self.assertEqual(result.stderr.count("\n"), 1, result.stderr)
        for marker in MARKERS:
            self.assertNotIn(marker, result.stderr)
        return result.stderr.strip()


class StoreTest(ActivityCase):
    def test_read_store_returns_units_coordinators_and_retired_workers(self):
        fixture = self.fixture
        fixture.meta["previousThread"] = "mcp:threadmarker-previous"
        fixture.unit("D7", "in-review", "Activity page", thread=worker(2), task="node:delegated-task:nodemarker-9", pr="https://example.test/o/r/pull/7")
        fixture.unit("D8", "merged", "Queue fix")
        fixture.retired("D7", worker(1))
        fixture.log.append({"at": fixture.stamp(400), "kind": "dish", "id": "D7", "state": "in-review", "note": "mcp:threadmarker-unrelated"})
        fixture.write_store()
        unit, store = MOD["Unit"], MOD["read_store"](fixture.store)
        self.assertEqual(store, MOD["Store"](
            "kit", frozenset({"kit", "proj"}), (COORDINATOR, "mcp:threadmarker-previous"),
            (unit("D7", "in-review", "Activity page", "https://example.test/o/r/pull/7", worker(2), (worker(1),), "node:delegated-task:nodemarker-9"),
             unit("D8", "merged", "Queue fix", "", "", (), ""))))

    def test_read_store_drops_the_text_after_the_last_newline(self):
        fixture = self.fixture
        fixture.unit("D7")
        fixture.write_store()
        with open(fixture.store / "dishes.tsv", "a") as table:
            table.write("D8\tkilled append")
        self.assertEqual([unit.id for unit in MOD["read_store"](fixture.store).units], ["D7"])

    def test_no_at_and_no_brigade_dir_exits_1(self):
        self.fixture.write()
        self.assertEqual(self.fails(1, at=None), "activity: pass --at <store directory> or set BRIGADE_DIR")

    def test_brigade_dir_names_the_store_when_at_is_absent(self):
        self.fixture.write_store()
        line = self.fails(3, at=None, env={"BRIGADE_DIR": str(self.fixture.store)})
        self.assertEqual(line, "activity: no T3 Code database under the --t3-home directory; pass --t3-home <T3's base directory> or set T3CODE_HOME")

    def test_directory_with_no_store_record_exits_1(self):
        self.fixture.write()
        (self.fixture.store / "restaurant.json").unlink()
        self.assertEqual(self.fails(1), "activity: that directory holds no coordinator's store; pass --at <store directory> or set BRIGADE_DIR")

    def test_store_record_that_is_not_a_json_object_exits_1(self):
        for text in ("[]", "{"):
            with self.subTest(text):
                self.fixture.write_store()
                (self.fixture.store / "restaurant.json").write_text(text)
                self.assertEqual(self.fails(1), "activity: the store's coordinator record is not a JSON object; restore it and run this again")

    def test_admin_store_exits_1(self):
        self.fixture.meta["role"] = "admin"
        self.fixture.write()
        self.assertEqual(self.fails(1), "activity: this is the executive admin's store; pass one coordinator's store directory")

    def test_row_with_a_wrong_field_count_exits_1_with_its_line_number(self):
        fixture = self.fixture
        fixture.unit("D7")
        fixture.write()
        with open(fixture.store / "dishes.tsv", "a") as table:
            table.write("D8\ttoo few fields\n")
        self.assertEqual(self.fails(1), "activity: line 3 of the store's work item table is malformed; fix or remove it")

    def test_log_row_that_is_not_utf8_exits_1_with_its_line_number(self):
        self.fixture.write()
        with open(self.fixture.store / "log.tsv", "ab") as table:
            table.write(b"\xff\t\t\t\t\n")
        self.assertEqual(self.fails(1), "activity: line 2 of the store's log is malformed; fix or remove it")

    def test_store_columns_equal_brigade_tables(self):
        tables = runpy.run_path(str(SCRIPT.with_name("brigade.py")))["TABLES"]
        self.assertEqual(MOD["STORE_COLUMNS"], {name: tables[name] for name in ("dishes.tsv", "log.tsv")})


class ArgumentTest(ActivityCase):
    def test_hours_outside_the_range_exits_1(self):
        for hours in ("0", "-1", "168.5", "nan"):
            with self.subTest(hours):
                self.assertEqual(self.fails(1, f"--hours={hours}"), "activity: --hours must be more than 0 and at most 168; pass a number in that range")

    def test_max_bytes_outside_the_range_exits_1(self):
        for size in ("15999", "500001"):
            with self.subTest(size):
                self.assertEqual(self.fails(1, f"--max-bytes={size}"), "activity: --max-bytes must be from 16000 to 500000; pass a number in that range")


class DatabasePathTest(ActivityCase):
    def setUp(self):
        super().setUp()
        self.home = self.fixture.root / "homemarker"
        self.find = MOD["t3_database"]

    def make(self, base, state="userdata"):
        (base / state).mkdir(parents=True)
        (base / state / "statev2.sqlite").write_bytes(b"")
        return base / state / "statev2.sqlite"

    def test_flag_beats_the_variable(self):
        flagged, named = self.make(self.fixture.root / "flag"), self.make(self.fixture.root / "variable")
        self.assertEqual(self.find(str(flagged.parents[1]), {"T3CODE_HOME": str(named.parents[1])}, self.home), flagged)
        self.assertEqual(self.find(None, {"T3CODE_HOME": f" {named.parents[1]} "}, self.home), named)

    def test_blank_variable_falls_back_to_the_home_directory(self):
        default = self.make(self.home / ".t3")
        self.assertEqual(self.find(None, {"T3CODE_HOME": "  "}, self.home), default)
        self.assertEqual(self.find(None, {}, self.home), default)

    def test_leading_tilde_is_the_home_directory(self):
        named = self.make(self.home / "elsewhere")
        self.assertEqual(self.find(None, {"T3CODE_HOME": "~/elsewhere"}, self.home), named)
        self.assertEqual(self.find("~/elsewhere", {}, self.home), named)

    def test_dev_is_used_only_with_no_flag_no_variable_and_no_userdata_database(self):
        dev = self.make(self.home / ".t3", "dev")
        self.assertEqual(self.find(None, {}, self.home), dev)
        userdata = self.make(self.home / ".t3")
        self.assertEqual(self.find(None, {}, self.home), userdata)
        explicit = self.make(self.fixture.root / "explicit", "dev")
        for flag, environ in ((str(explicit.parents[1]), {}), (None, {"T3CODE_HOME": str(explicit.parents[1])})):
            with self.subTest(flag=flag), self.assertRaises(MOD["SourceError"]):
                self.find(flag, environ, self.home)

    def test_missing_database_names_where_the_base_directory_came_from(self):
        self.fixture.write_store()
        end = "; pass --t3-home <T3's base directory> or set T3CODE_HOME"
        empty = str(self.fixture.root / "pathmarker empty")
        self.assertEqual(self.fails(3, t3_home=empty), "activity: no T3 Code database under the --t3-home directory" + end)
        self.assertEqual(self.fails(3, t3_home=None, env={"T3CODE_HOME": empty}), "activity: no T3 Code database under the T3CODE_HOME directory" + end)
        self.assertEqual(self.fails(3, t3_home=None), "activity: no T3 Code database under the default base directory" + end)


class ShapeTest(ActivityCase):
    def test_write_ahead_log_with_no_index_exits_3_and_changes_no_file(self):
        fixture = self.fixture.write()
        Path(f"{fixture.database}-wal").write_bytes(b"")
        before = listing(fixture.database.parent)
        self.assertEqual(self.fails(3), "activity: T3's database has a write-ahead log with no index beside it; start T3 Code and run this again")
        self.assertEqual(listing(fixture.database.parent), before)

    def test_file_that_is_not_a_database_exits_3_with_sqlites_words_and_no_path(self):
        fixture = self.fixture
        fixture.write_store()
        fixture.database.parent.mkdir(parents=True)
        fixture.database.write_bytes(b"not a database, " * 64)
        self.assertEqual(self.fails(3), "activity: cannot read T3's database (file is not a database); check that T3 Code is running and try again")

    def test_unreadable_drops_the_path_and_every_word_with_a_slash(self):
        path = Path("/pathmarker dir/userdata/statev2.sqlite")
        error = sqlite3.OperationalError(f"unable to open {path} near /pathmarker/other and C:\\pathmarker")
        self.assertEqual(
            str(MOD["unreadable"](error, path)),
            "cannot read T3's database (unable to open near and); check that T3 Code is running and try again")

    def test_missing_table_exits_3(self):
        schema = {table: columns for table, columns in SCHEMA.items() if not table.endswith("_runs")}
        self.fixture.write(schema=schema)
        self.assertEqual(self.fails(3), f"activity: T3's database has no table orchestration_v2_projection_runs; {CHANGED}")

    def test_missing_column_exits_3(self):
        schema = dict(SCHEMA, orchestration_v2_projection_subagents=("subagent_id", "thread_id", "child_thread_id", "status", "started_at"))
        self.fixture.write(schema=schema)
        self.assertEqual(self.fails(3), f"activity: T3's table orchestration_v2_projection_subagents has no column completed_at; {CHANGED}")

    def test_missing_projection_record_exits_3(self):
        self.fixture.write(version=None)
        self.assertEqual(self.fails(3), f"activity: T3's database has no thread-projections record; {CHANGED}")

    def test_projection_version_3_exits_3(self):
        self.fixture.write(version=3)
        self.assertEqual(self.fails(3), "activity: T3's thread records are not version 2, the version this tool reads; update pstack-t3")

    def test_extra_table_and_extra_column_pass_the_shape_check(self):
        schema = dict(SCHEMA, orchestration_v2_projection_metadata=("projection_name", "schema_version", "updated_at"), unrelated=("value",))
        fixture = self.fixture
        fixture.write_store()
        fixture.write_database(schema=schema)
        connection = sqlite3.connect(fixture.database)
        connection.execute("insert into orchestration_v2_projection_metadata values ('thread-projections', 2, '')")
        connection.commit()
        connection.close()
        connection = MOD["open_t3"](fixture.database)
        self.addCleanup(connection.close)
        self.assertIsNone(MOD["check_shape"](connection))

    def test_coordinator_thread_absent_from_the_database_exits_3(self):
        self.fixture.write()
        self.assertEqual(
            self.fails(3),
            "activity: T3's database has no record of this coordinator's thread; pass --t3-home <the base directory of the T3 that runs it>")

    def test_bad_timestamp_exits_3_and_names_the_column(self):
        cases = (
            ("orchestration_v2_projection_runs.requested_at", lambda f: f.turns.append((COORDINATOR, "completed", "yesterday pathmarker", None))),
            ("orchestration_v2_projection_subagents.started_at", lambda f: f.delegations.append(("node:nodemarker", COORDINATOR, worker(1), "completed", None, None))),
        )
        for where, spoil in cases:
            with self.subTest(where):
                fixture = self.fixture = Fixture(self.fixture.root / where)
                fixture.coordinator()
                spoil(fixture)
                fixture.write()
                self.assertEqual(self.fails(3), f"activity: T3's {where} is not a timestamp; {CHANGED}")

    def test_thread_payload_with_no_model_selection_exits_3(self):
        for payload in ('{"lineage": {}}', "[]", "not json pathmarker"):
            with self.subTest(payload):
                fixture = self.fixture = Fixture(self.fixture.root / str(len(payload)))
                fixture.coordinator(payload=payload)
                fixture.write()
                self.assertEqual(self.fails(3), f"activity: T3's thread payload has no modelSelection; {CHANGED}")


class OpenTest(ActivityCase):
    def rows(self):
        connection = MOD["open_t3"](self.fixture.database)
        try:
            return connection.execute("select thread_id from orchestration_v2_projection_threads").fetchall()
        finally:
            connection.close()

    def test_closed_database_is_read_and_no_file_appears_beside_it(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.write()
        before = listing(fixture.database.parent)
        self.assertEqual(sorted(before), ["statev2.sqlite"])
        self.assertEqual(self.rows(), [(COORDINATOR,)])
        self.assertEqual(listing(fixture.database.parent), before)

    def test_live_database_is_read_through_its_log_with_the_same_files_and_the_same_database_and_log_bytes(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.write(live=True)
        before = listing(fixture.database.parent)
        self.assertEqual(sorted(before), ["statev2.sqlite", "statev2.sqlite-shm", "statev2.sqlite-wal"])
        self.assertEqual(self.rows(), [(COORDINATOR,)])
        self.assertEqual(live_listing(fixture.database.parent), live_listing(fixture.database.parent, before))

    def test_connection_refuses_a_write(self):
        self.fixture.write(live=True)
        connection = MOD["open_t3"](self.fixture.database)
        self.addCleanup(connection.close)
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute("delete from orchestration_v2_projection_metadata")


class ReadTest(ActivityCase):
    def test_request_name_is_the_decoded_text_after_the_last_delegate_task_marker(self):
        name = MOD["request_name"]
        self.assertEqual(name(delegated(COORDINATOR, "brigade-kit-d7-verify-0a1b2c3")), "brigade-kit-d7-verify-0a1b2c3")
        self.assertEqual(name(delegated(COORDINATOR, "two words")), "two words")
        self.assertIsNone(name(worker(1)))
        self.assertIsNone(name(native(1)))

    def test_parse_time_reads_a_z_suffix_and_reads_no_zone_as_utc(self):
        parse = MOD["parse_time"]
        self.assertEqual(parse("2026-10-03T01:49:21.841Z", "t.c"), 1790992161.841)
        self.assertEqual(parse("2026-10-03T01:49:21", "t.c"), 1790992161.0)
        self.assertEqual(parse("2026-10-03T03:49:21+02:00", "t.c"), 1790992161.0)

    def test_in_scope_keeps_ancestors_up_to_the_root_and_counts_the_rest(self):
        parent_of = {"child": "worker", "grandchild": "child", "stray": "elsewhere", "loop-a": "loop-b", "loop-b": "loop-a", "above": None}
        kept, others = MOD["in_scope"](parent_of, frozenset({"worker", "root"}), {"grandchild", "root", "stray", "loop-a", "lone"})
        self.assertEqual((kept, others), ({"grandchild", "child", "worker", "root"}, 3))

    def test_read_t3_keeps_the_coordinators_scope_and_counts_other_threads(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.coordinator()
        fixture.thread(worker(1), provider="codex", model="model-b", title="D7 worker", turns=(("completed", 50, 40), ("failed", 30, 20)))
        explorer = delegated(worker(1), "how-explorer")
        node = fixture.thread(explorer, parent=worker(1), turns=(("completed", 45, 41),))
        fixture.thread("mcp:threadmarker-other-project", turns=(("completed", 10, 5),))
        fixture.thread(delegated("mcp:threadmarker-other-project", "x"), parent="mcp:threadmarker-other-project", turns=(("running", 3, None),))
        store, t3, _ = fixture.write().read()
        self.assertEqual(sorted(t3.agents), sorted([COORDINATOR, worker(1), explorer]))
        self.assertEqual(t3.other_threads, 2)
        self.assertEqual(t3.node_thread, {node: explorer})
        agent = t3.agents[worker(1)]
        self.assertEqual((agent.parent, agent.request, agent.provider, agent.model, agent.title, agent.delegation), (None, None, "codex", "model-b", "D7 worker", None))
        self.assertEqual([(turn.status.value, round(fixture.now - turn.start), round(fixture.now - turn.end)) for turn in agent.turns],
                         [("done", 3000, 2400), ("failed", 1800, 1200)])
        child = t3.agents[explorer]
        self.assertEqual((child.parent, child.request, child.delegation.status.value), (worker(1), "how-explorer", "done"))

    def test_read_t3_reads_only_turns_that_touch_the_window(self):
        fixture = self.fixture
        fixture.coordinator(turns=(("completed", 400, 390), ("completed", 200, 170), ("running", 5, None)))
        _, t3, _ = fixture.write().read(hours=3)
        self.assertEqual([round(fixture.now - turn.start) for turn in t3.agents[COORDINATOR].turns], [12000, 300])

    def test_thread_with_no_turn_in_the_window_is_not_active_unless_it_never_had_a_turn(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 20, 10),))
        stale = delegated(worker(1), "stale")
        fixture.thread(stale, parent=worker(1), turns=(("completed", 900, 890),), delegation=("running", 900, None))
        fixture.thread(native(1), parent=worker(1), delegation=("running", 15, None))
        fixture.thread(native(2), parent=worker(1), delegation=("completed", 900, 890))
        _, t3, _ = fixture.write().read()
        self.assertEqual(sorted(t3.agents), sorted([worker(1), native(1)]))
        self.assertEqual((t3.agents[native(1)].turns, t3.agents[native(1)].delegation.status.value), ((), "running"))

    def test_ancestor_with_no_turn_in_the_window_is_kept_with_no_turns_and_no_delegation(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 900, 890),))
        middle = delegated(worker(1), "middle")
        fixture.thread(middle, parent=worker(1), turns=(("completed", 800, 790),), delegation=("running", 800, None))
        leaf = delegated(middle, "leaf")
        fixture.thread(leaf, parent=middle, turns=(("running", 4, None),))
        _, t3, _ = fixture.write().read()
        self.assertEqual(sorted(t3.agents), sorted([worker(1), middle, leaf]))
        self.assertEqual((t3.agents[middle].turns, t3.agents[middle].delegation, t3.agents[middle].title), ((), None, ""))

    def test_delegation_that_ended_before_the_window_is_not_on_an_agent_with_a_turn_in_it(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 20, 10),))
        child = delegated(worker(1), "follow-up")
        fixture.thread(child, parent=worker(1), turns=(("completed", 900, 890), ("completed", 9, 8)), delegation=("completed", 900, 890))
        _, t3, _ = fixture.write().read()
        self.assertIsNone(t3.agents[child].delegation)

    def test_status_text_t3_status_lacks_and_an_ended_running_status_count_as_unknown(self):
        fixture = self.fixture
        fixture.coordinator(turns=(("teleported", 30, 20), ("running", 19, 18), ("completed", 9, 8)))
        fixture.thread("mcp:threadmarker-other", turns=(("teleported", 30, 20),))
        _, t3, _ = fixture.write().read()
        self.assertEqual([turn.status.value for turn in t3.agents[COORDINATOR].turns], ["unknown", "unknown", "done"])
        self.assertEqual(t3.unknown_status, 2)

    def test_of_two_delegations_for_one_child_the_one_that_started_last_is_used(self):
        fixture = self.fixture
        fixture.coordinator()
        child = delegated(COORDINATOR, "twice")
        fixture.thread(child, parent=COORDINATOR, turns=(("completed", 9, 8),), delegation=("completed", 9, 8))
        fixture.delegations.append(("node:nodemarker-early", "mcp:threadmarker-elsewhere", child, "failed", fixture.stamp(50), fixture.stamp(40)))
        _, t3, _ = fixture.write().read()
        self.assertEqual((t3.agents[child].parent, t3.agents[child].delegation.status.value), (COORDINATOR, "done"))
        self.assertEqual(sorted(t3.node_thread.values()), [child, child])

    def test_check_coordinator_passes_a_store_with_no_recorded_thread(self):
        fixture = self.fixture
        fixture.write()
        connection = MOD["open_t3"](fixture.database)
        self.addCleanup(connection.close)
        self.assertIsNone(MOD["check_coordinator"](connection, ()))
        with self.assertRaises(MOD["SourceError"]):
            MOD["check_coordinator"](connection, (COORDINATOR,))


def page_of(fixture, hours=3.0):
    store, t3, window = fixture.write().read(hours)
    return MOD["build_page"](store, t3, window)


def labels(page):
    """Each group's rows as (depth, label), keyed by the work item's id, or None for the agents tied to no work item."""
    return {group.item.id if group.item else None: [(row.depth, row.label) for row in group.rows] for group in page.groups}


def agent(thread, title="", parent=None, turns=(), delegation=None, provider="claudeAgent", model="model-a"):
    return MOD["Agent"](thread, parent, MOD["request_name"](thread), provider, model, title, tuple(turns), delegation)


def store_of(*units, name="kit", slug=("kit", "proj"), coordinators=(COORDINATOR,)):
    return MOD["Store"](name, frozenset(slug), coordinators, tuple(MOD["Unit"](ident, "in-progress", "", "", work, tuple(earlier), "") for ident, work, earlier in units))


class GroupingTest(ActivityCase):
    def test_worker_earlier_worker_and_task_by_sub_agent_id_and_by_request_name_group_by_record(self):
        fixture = self.fixture
        fixture.coordinator()
        review = delegated(COORDINATOR, "second-opinion-0a1b2c3")
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.thread(worker(2), turns=(("completed", 70, 60),))
        node = fixture.thread(delegated(COORDINATOR, "anything"), parent=COORDINATOR, turns=(("completed", 50, 45),))
        fixture.thread(review, parent=COORDINATOR, turns=(("completed", 40, 35),))
        fixture.unit("D7", thread=worker(2), task=node)
        fixture.unit("D8", task="second-opinion-0a1b2c3")
        fixture.retired("D7", worker(1))
        store, t3, _ = fixture.write().read()
        placed = MOD["assign"](store, t3)
        self.assertEqual({thread: (found.unit, found.evidence.value) for thread, found in placed.items()}, {
            worker(1): ("D7", "record"), worker(2): ("D7", "record"),
            delegated(COORDINATOR, "anything"): ("D7", "record"), review: ("D8", "record")})

    def test_of_two_units_that_name_one_thread_the_later_in_the_table_wins(self):
        fixture = self.fixture
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.unit("D7", thread=worker(1))
        fixture.unit("D8")
        fixture.retired("D8", worker(1))
        fixture.meta.pop("thread")
        self.assertEqual(labels(page_of(fixture)), {"D8": [(0, "worker")]})

    def test_workers_child_and_grandchild_take_the_workers_unit_whatever_their_request_names_hold(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", thread=worker(1))
        fixture.unit("D8")
        child = delegated(worker(1), "architect-d8-runner-2")
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.thread(child, parent=worker(1), turns=(("completed", 70, 60),))
        fixture.thread(delegated(child, "d8-helper"), parent=child, turns=(("completed", 65, 62),))
        store, t3, _ = fixture.write().read()
        self.assertEqual(sorted((found.unit, found.evidence.value) for found in MOD["assign"](store, t3).values()),
                         [("D7", "lineage"), ("D7", "lineage"), ("D7", "record")])

    def test_coordinators_child_groups_by_request_name_only_for_a_unit_the_store_has(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7")
        for request in ("brigade-kit-d7-verify-0a1b2c3", "d7r2-fix", "x-d7c", "brigade-kit-d9-verify", "d77-fix", "D7-fix"):
            fixture.thread(delegated(COORDINATOR, request), parent=COORDINATOR, turns=(("completed", 20, 10),))
        store, t3, _ = fixture.write().read()
        placed = MOD["assign"](store, t3)
        self.assertEqual({MOD["request_name"](thread): (found.unit, found.evidence.value) for thread, found in placed.items()}, {
            "brigade-kit-d7-verify-0a1b2c3": ("D7", "request"), "d7r2-fix": ("D7", "request"), "x-d7c": ("D7", "request"),
            "brigade-kit-d9-verify": (None, "none"), "d77-fix": (None, "none"), "D7-fix": (None, "none")})
        self.assertEqual(MOD["build_page"](store, t3, MOD["Window"](fixture.now - 10800, fixture.now)).hidden.by_request_name, 3)

    def test_title_that_holds_a_unit_id_is_not_grouping_evidence(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7")
        fixture.thread(delegated(COORDINATOR, "helper"), title="D7 review of the page", parent=COORDINATOR, turns=(("completed", 20, 10),))
        self.assertEqual(labels(page_of(fixture)), {None: [(0, "D7 review of the page")]})

    def test_agent_tied_to_no_unit_is_in_the_last_group_and_in_the_totals(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", thread=worker(1))
        fixture.thread(delegated(COORDINATOR, "why-investigator"), parent=COORDINATOR, turns=(("running", 4.5, None),))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        page = page_of(fixture)
        self.assertEqual(labels(page), {"D7": [(0, "worker")], None: [(0, "why investigator")]})
        self.assertEqual([group.item is None for group in page.groups], [False, True])
        self.assertEqual(page.totals, MOD["Totals"](running=1, agents=1, subagents=1, failed=0))

    def test_groups_with_open_work_come_first_then_the_latest_activity_first(self):
        fixture = self.fixture
        for number, turns in ((1, (("completed", 30, 20),)), (2, (("completed", 150, 140), ("running", 100, None))), (3, (("completed", 15, 10),))):
            fixture.unit(f"D{number}", thread=worker(number))
            fixture.thread(worker(number), turns=turns)
        self.assertEqual([group.item.id for group in page_of(fixture).groups], ["D2", "D3", "D1"])

    def test_rows_are_in_tree_order_by_start_with_depth_capped_at_2(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        late, early = delegated(worker(1), "late"), delegated(worker(1), "early")
        fixture.thread(late, title="late", parent=worker(1), turns=(("completed", 40, 35),))
        fixture.thread(early, title="early", parent=worker(1), turns=(("completed", 70, 60),))
        deep = delegated(early, "deep")
        fixture.thread(deep, title="deep", parent=early, turns=(("completed", 65, 64),))
        fixture.thread(delegated(deep, "deeper"), title="deeper", parent=deep, turns=(("completed", 64.5, 64.2),))
        self.assertEqual(labels(page_of(fixture)), {"D7": [(0, "worker"), (1, "early"), (2, "deep"), (2, "deeper"), (1, "late")]})


class StatusTest(ActivityCase):
    def test_providers_own_sub_agent_with_no_turns_has_one_bar_from_its_delegation(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 100, 80),))
        fixture.thread(native(1), title="/root/spec_review", parent=worker(1), delegation=("completed", 90, 45))
        fixture.thread(native(2), title="/root/standards", parent=worker(1), delegation=("running", 9.5, None))
        page = page_of(fixture)
        done, live = page.groups[0].rows[1:]
        self.assertEqual((done.label, done.status.value, done.seconds, done.open_seconds), ("spec_review", "done", 2700, None))
        self.assertEqual([(span.x, span.w, span.status.value) for span in done.spans], [(500, 250, "done")])
        self.assertEqual((live.label, live.status.value, live.open_seconds), ("standards", "running", 570))
        self.assertEqual([(span.x, span.w, span.status.value) for span in live.spans], [(947, 53, "running")])
        self.assertEqual(page.totals, MOD["Totals"](running=1, agents=1, subagents=2, failed=0))

    def test_open_delegation_with_no_open_turn_reads_waiting_and_is_not_counted_running(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 100, 80),))
        fixture.thread(delegated(worker(1), "explorer"), parent=worker(1), turns=(("completed", 70, 60),), delegation=("running", 70, None))
        page = page_of(fixture)
        row = page.groups[0].rows[1]
        self.assertEqual((row.status.value, row.open_seconds, [span.status.value for span in row.spans]), ("waiting", None, ["done"]))
        self.assertEqual(page.totals.running, 0)

    def test_coordinators_open_turn_is_in_no_count_and_its_row_has_no_running_bar(self):
        fixture = self.fixture
        fixture.meta["previousThread"] = "mcp:threadmarker-previous"
        fixture.coordinator(turns=(("completed", 60, 50), ("running", 5, None)), model="vendor/model-c")
        fixture.thread("mcp:threadmarker-previous", turns=(("failed", 120, 110),))
        page = page_of(fixture)
        own = page.coordinator
        self.assertEqual((own.label, own.model, own.provider, own.open_seconds, own.seconds), ("coordinator", "model-c", "Claude", None, 1500))
        self.assertEqual([(span.x, span.w, span.status.value) for span in own.spans], [(333, 56, "failed"), (667, 55, "done"), (972, 28, "done")])
        self.assertEqual((page.totals, page.groups, page.legend), (MOD["Totals"](0, 0, 0, 0), (), ()))

    def test_agent_whose_last_turn_failed_is_counted_failed_and_one_that_then_completed_a_turn_is_not(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.unit("D8", thread=worker(2))
        fixture.thread(worker(1), turns=(("completed", 100, 90), ("failed", 60, 50)))
        fixture.thread(worker(2), turns=(("failed", 100, 90), ("completed", 89.9, 80)))
        page = page_of(fixture)
        rows = {group.item.id: group.rows[0] for group in page.groups}
        self.assertEqual((rows["D7"].status.value, rows["D8"].status.value, page.totals.failed), ("failed", "done", 1))
        self.assertEqual([span.status.value for span in rows["D8"].spans], ["failed", "done"])

    def test_closed_delegation_decides_the_status_over_the_last_turn(self):
        closed = MOD["Delegation"](MOD["Status"].STOPPED, 10.0, 20.0)
        turn = MOD["Turn"](MOD["Status"].DONE, 10.0, 20.0)
        self.assertEqual(MOD["status_of"](agent(delegated(worker(1), "x"), parent=worker(1), turns=(turn,), delegation=closed)).value, "stopped")
        self.assertEqual(MOD["status_of"](agent(worker(1))).value, "done")

    def test_bars_join_when_the_same_status_is_under_5_thousandths_apart_and_an_open_turn_starts_its_own(self):
        status, turn, window = MOD["Status"], MOD["Turn"], MOD["Window"](0.0, 1000.0)
        turns = (turn(status.DONE, -50.0, 100.0), turn(status.DONE, 104.0, 200.0), turn(status.DONE, 205.0, 300.0),
                 turn(status.STOPPED, 301.0, 310.0), turn(status.RUNNING, 400.0, 500.0), turn(status.QUEUED, 501.0, None))
        spans = MOD["spans_of"](agent(worker(1), turns=turns), window)
        self.assertEqual([(span.x, span.w, span.status.value) for span in spans],
                         [(0, 200, "done"), (205, 95, "done"), (301, 9, "stopped"), (400, 100, "running"), (501, 499, "running")])
        self.assertEqual(MOD["seconds_of"](agent(worker(1), turns=turns), window), 100 + 96 + 95 + 9 + 100 + 499)

    def test_bar_at_the_windows_end_keeps_a_width_of_1_inside_the_window(self):
        turn = MOD["Turn"](MOD["Status"].RUNNING, 999.9, None)
        spans = MOD["spans_of"](agent(worker(1), turns=(turn,)), MOD["Window"](0.0, 1000.0))
        self.assertEqual([(span.x, span.w) for span in spans], [(999, 1)])


class PageTest(ActivityCase):
    def test_in_flight_items_are_in_number_order_with_their_plain_state_words(self):
        fixture = self.fixture
        states = ("in-progress", "in-review", "passed", "queued", "sent-back", "blocked", "merged", "dropped", "plated")
        for number, state in zip((10, 9, 8, 7, 6, 5, 4, 3, 2), states):
            fixture.unit(f"D{number}", state, f"summary {number}", pr="https://example.test/o/r/pull/7" if number == 9 else "http://example.test/plain" if number == 8 else "")
        page = page_of(fixture)
        self.assertEqual([(item.id, item.state, item.tone, item.pr) for item in page.items], [
            ("D2", "other", "", ""), ("D5", "blocked", "bad", ""), ("D6", "sent back", "warn", ""), ("D7", "landing", "warn", ""),
            ("D8", "passed review", "info", ""), ("D9", "in review", "info", "https://example.test/o/r/pull/7"), ("D10", "working", "go", "")])

    def test_finished_unit_with_activity_has_a_group_and_is_not_in_flight(self):
        fixture = self.fixture
        fixture.unit("D7", "merged", "Queue fix /pathmarker/notes.md", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        page = page_of(fixture)
        self.assertEqual(page.groups[0].item, MOD["Item"]("D7", "Queue fix", "merged", "", "", False))
        self.assertEqual(page.items, ())

    def test_email_address_in_a_title_and_in_a_work_item_summary_is_not_on_the_page(self):
        fixture = self.fixture
        fixture.unit("D7", "in-progress", "Fix sign-in for acct-homemarker@example.test today", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.thread(delegated(worker(1), "helper"), title="acct-homemarker@example.test notes", parent=worker(1), turns=(("completed", 70, 60),))
        page = page_of(fixture)
        self.assertEqual((page.groups[0].item.summary, labels(page)), ("Fix sign-in for today", {"D7": [(0, "worker"), (1, "helper")]}))

    def test_provider_names_come_from_the_table_and_any_other_driver_reads_other(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), provider="codex", turns=(("completed", 90, 80),))
        for number, provider in enumerate(("acct-homemarker-instance", "acct-homemarker-instance", "grok", "cursor", "opencode", "claudeAgent", "")):
            fixture.thread(native(number), provider=provider, parent=worker(1), delegation=("completed", 70, 60))
        page = page_of(fixture)
        self.assertEqual(page.legend, (("Other", 3), ("Claude", 1), ("Codex", 1), ("Cursor", 1), ("Grok", 1), ("OpenCode", 1)))
        self.assertEqual(sorted({row.provider for row in page.groups[0].rows}), ["Claude", "Codex", "Cursor", "Grok", "OpenCode", "Other"])

    def test_parent_with_no_turn_in_the_window_has_a_row_with_no_bar_and_only_its_child_is_counted(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), provider="codex", turns=(("completed", 900, 890),))
        fixture.thread(delegated(worker(1), "helper"), title="helper", parent=worker(1), turns=(("failed", 20, 10),))
        page = page_of(fixture)
        parent, child = page.groups[0].rows
        self.assertEqual((parent.depth, parent.label, parent.status.value, parent.seconds, parent.spans, parent.stands_for), (0, "worker", "done", 0, (), 0))
        self.assertEqual((child.depth, child.label, [(span.x, span.w) for span in child.spans], child.stands_for), (1, "helper", [(889, 55)], 1))
        self.assertEqual((page.totals, page.legend), (MOD["Totals"](running=0, agents=0, subagents=1, failed=1), (("Claude", 1),)))
        self.assertEqual((page.groups[0].agents, page.groups[0].subagents), (0, 1))
        self.assertEqual([line[6:] for line in MOD["wire"](page)["G"][0][1]], [[[], 0], [[889, 55, 2]]])
        self.assertIn("  D7   working   1 sub-agent, 10m at work", MOD["render_text"](page).split("\n"))

    def test_parent_with_no_turn_in_the_window_is_not_counted_as_grouped_by_request_name(self):
        fixture = self.fixture
        fixture.unit("D7")
        review = delegated(COORDINATOR, "brigade-kit-d7-verify")
        fixture.thread(review, parent=COORDINATOR, turns=(("completed", 900, 890),))
        fixture.thread(delegated(review, "reader"), title="reader", parent=review, turns=(("completed", 20, 10),))
        page = page_of(fixture)
        self.assertEqual((drawn(page), page.hidden.by_request_name), ({"D7": [(0, "review", 0), (1, "reader", 1)]}, 0))

    def test_ancestor_with_no_turn_in_the_window_has_no_row_in_a_group_that_holds_none_of_its_descendants(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 900, 890),))
        node = fixture.thread(delegated(worker(1), "helper"), title="helper", parent=worker(1), turns=(("completed", 20, 10),))
        fixture.unit("D8", task=node)
        page = page_of(fixture)
        self.assertEqual((labels(page), page.totals), ({"D8": [(0, "helper")]}, MOD["Totals"](running=0, agents=0, subagents=1, failed=0)))

    def test_store_with_no_recorded_thread_shows_workers_and_their_children_and_no_coordinator_row(self):
        fixture = self.fixture
        fixture.meta.pop("thread")
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.thread(delegated(worker(1), "explorer"), parent=worker(1), turns=(("completed", 70, 60),))
        fixture.thread(delegated(COORDINATOR, "brigade-kit-d7-verify"), parent=COORDINATOR, turns=(("completed", 50, 40),))
        page = page_of(fixture)
        self.assertEqual((labels(page), page.coordinator, page.hidden.other_threads), ({"D7": [(0, "worker"), (1, "explorer")]}, None, 1))


class LabelTest(unittest.TestCase):
    STORE = store_of(("D7", worker(2), (worker(1),)), ("D8", "", ()))

    def label(self, title="", request="", parent=COORDINATOR, unit="D7"):
        thread = delegated(parent, request) if request else native(1) if parent else worker(9)
        return MOD["label_of"](agent(thread, title=title, parent=parent), self.STORE, unit)

    def test_current_worker_and_earlier_worker(self):
        self.assertEqual(MOD["label_of"](agent(worker(2), title="D7 anything"), self.STORE, "D7"), "worker")
        self.assertEqual(MOD["label_of"](agent(worker(1), title="D7 anything"), self.STORE, "D7"), "earlier worker")

    def test_written_title_wins_over_the_request_name(self):
        self.assertEqual(self.label("how explorer: store and CLI", "brigade-kit-d7-verify-0a1b2c3"), "how explorer: store and CLI")

    def test_title_that_starts_with_act_as_or_you_are_loses_to_the_request_name(self):
        self.assertEqual(self.label("Act as the review sub-agent for this task.", "brigade-kit-d7-verify-0a1b2c3"), "review")
        self.assertEqual(self.label("You are a code delegate for item D7.", "architect-d7-runner-2"), "architect runner 2")

    def test_title_over_60_characters_loses_to_the_request_name(self):
        self.assertEqual(self.label("Read-only review of the page. Do not edit, commit, push, or merge.", "brigade-kit-d7r2-fix"), "fix")
        self.assertEqual(self.label("x" * 61, "brigade-kit-d7r2-fix"), "fix")
        self.assertEqual(self.label("x" * 60, "brigade-kit-d7r2-fix"), "x" * 39 + "…")

    def test_leading_id_of_the_rows_own_unit_is_dropped_from_a_title(self):
        self.assertEqual(self.label("D7: rehearsal of the wake"), "rehearsal of the wake")
        self.assertEqual(self.label("D7 fresh-child test"), "fresh-child test")
        self.assertEqual(self.label("D8: rehearsal"), "D8: rehearsal")
        self.assertEqual(self.label("D7: rehearsal", unit=None), "D7: rehearsal")

    def test_title_that_is_one_path_gives_its_last_segment_only_when_that_holds_no_dot(self):
        self.assertEqual(self.label("/root/spec_review", parent=worker(2)), "spec_review")
        self.assertEqual(self.label("/pathmarker/notes.md", parent=worker(2)), "sub-agent")

    def test_title_that_scrub_drops_a_part_from_loses_to_the_request_name(self):
        self.assertEqual(self.label("Read the brief at /pathmarker/brief.md", "why-investigator"), "why investigator")
        self.assertEqual(self.label("Fix 0a1b2c3 now", "brigade-kit-d7r2-fix"), "fix")
        self.assertEqual(self.label("Read /pathmarker/brief.md first", parent=worker(2)), "sub-agent")
        self.assertEqual(self.label("Read the brief first", "why-investigator"), "Read the brief first")

    def test_title_that_is_one_lower_case_word_with_a_hyphen_is_read_as_a_request_name(self):
        self.assertEqual(self.label("brigade-kit-d7-verify-2", "brigade-kit-d7-verify-0a1b2c3"), "review 2")

    def test_request_name_words_drop_brigade_the_stores_leading_slug_parts_unit_ids_and_ids(self):
        self.assertEqual(self.label(request="brigade-kit-d7-verify-0a1b2c3"), "review")
        self.assertEqual(self.label(request="proj-kit-nightly-audit-kit-1234567"), "nightly audit kit")
        self.assertEqual(self.label(request="trial-d8-kimi"), "trial kimi")
        self.assertEqual(self.label(request="trial-d9-kimi"), "trial d9 kimi")

    def test_role_from_an_act_as_title_when_the_request_name_leaves_nothing(self):
        self.assertEqual(self.label("Act as the review sub-agent for this task.", "brigade-kit-d7-0a1b2c3"), "review")

    def test_thread_with_no_usable_title_or_request_reads_sub_agent_or_agent(self):
        self.assertEqual(self.label("123e4567-e89b-12d3-a456-426614174000", parent=worker(2)), "sub-agent")
        self.assertEqual(self.label("You are the helper.", parent=None), "agent")

    def test_scrub_keeps_the_first_line_and_drops_paths_and_ids(self):
        scrub = MOD["scrub"]
        self.assertEqual(scrub("Fix the queue\nsecond line"), "Fix the queue")
        self.assertEqual(scrub("see `/pathmarker/a b` ~/pathmarker (a/b/c) C:\\pathmarker\\x file:///pathmarker src/a.py"), "see b` src/a.py")
        self.assertEqual(scrub("at 0a1b2c3 and 123e4567-e89b-12d3-a456-426614174000 and 123456 and mcp:x and node:y"), "at and and and and")
        self.assertEqual(scrub("defaced facade 12345 https://example.test/o/r/pull/7 thread: one"), "defaced facade 12345 https://example.test/o/r/pull/7 thread: one")
        self.assertEqual(scrub(""), "")

    def test_scrub_drops_a_part_that_holds_an_at_sign_with_a_character_before_it_and_a_dot_after_it(self):
        scrub = MOD["scrub"]
        self.assertEqual(scrub("mail acct-homemarker@example.test and <acct-homemarker@example.test> now"), "mail and now")
        self.assertEqual(scrub("@handle a@b user@host @example.test stay"), "@handle a@b user@host @example.test stay")

    def test_model_name_is_the_text_after_the_last_slash(self):
        self.assertEqual(MOD["model_name"]("vendor/sub/model-b-20260101"), "model-b-20260101")
        self.assertEqual(MOD["model_name"]("model-a"), "model-a")


def populate(fixture, units, agents, running=0, failed=0, heavy=False):
    """A coordinator, `units` work items with the last quarter in flight, and `agents` agents spread over them.

    Each item has a worker, children of the worker with written titles, review children of the coordinator, and
    sub-agents of a provider under the worker's children. `running` agents have an open turn and `failed` agents
    failed, all in the in-flight items. With heavy, summaries, titles, models, and links are long and hold four-byte characters.
    """
    fixture.coordinator(turns=(("completed", 170, 160), ("running", 5, None)))
    states = ("in-progress", "in-review", "passed", "queued", "sent-back", "blocked")
    wide = "𝔸𝔹ℂ𝔻 " * 10 if heavy else ""
    model = "vendor/model-" + "𝕏" * 30 if heavy else "model-a"
    for index in range(units):
        number, live = index + 1, index >= units - max(1, units // 4)
        link = f"https://example.test/o/r/pull/{number}" + ("/long-path" * 12 if heavy and number % 2 else "")
        fixture.unit(f"D{number}", states[index % 6] if live else ("merged", "dropped")[index % 5 == 0], f"{wide}summary of item {number}", thread=worker(number), pr=link)
        base, child = 170 - index * 160 / units, None
        for position in range(agents // units + (index < agents % units)):
            start = max(2.0, base - position * 0.15)
            status, end = "completed", start - 1
            if live and running:
                status, end, running = "running", None, running - 1
            elif live and failed:
                status, failed = "failed", failed - 1
            turns = ((status, start, end),)
            provider = ("claudeAgent", "codex", "grok", "opencode", "cursor")[position % 5]
            if position == 0:
                fixture.thread(worker(number), title=f"D{number} worker pathmarker", provider=provider, model=model, turns=turns)
            elif position % 3 == 1:
                child = delegated(worker(number), f"how-explorer-{position}")
                fixture.thread(child, title=f"{wide}how explorer: part {position}", provider=provider, model=model, parent=worker(number), turns=turns)
            elif position % 3 == 2:
                request = f"brigade-kit-d{number}-verify-{position}-0a1b2c3"
                fixture.thread(delegated(COORDINATOR, request), title="Act as the review sub-agent for this task.", provider=provider, model=model, parent=COORDINATOR, turns=turns)
            else:
                fixture.thread(f"{native(number)}-{position}", title="/root/spec_review", provider=provider, model=model, parent=child, delegation=(status, start, end))
    return fixture


def row(label, depth=0, status="done", spans=((0, 10, "done"),), open_seconds=None, stands_for=1, model="model-a"):
    bars = tuple(MOD["Span"](x, w, MOD["Status"](kind)) for x, w, kind in spans)
    return MOD["Row"](depth, label, model, "Claude", MOD["Status"](status), 60, bars, open_seconds, stands_for)


def item(ident, in_flight=True, summary="summary", pr=""):
    return MOD["Item"](ident, summary, "working" if in_flight else "merged", "go" if in_flight else "", pr, in_flight)


def group(of, *rows):
    return MOD["Group"](of, tuple(rows), len(rows), 0)


def page(*groups, items=(), coordinator=None, hidden=None, name="kit"):
    rows = [each for one in groups for each in one.rows]
    totals = MOD["Totals"](sum(each.open_seconds is not None for each in rows), sum(each.stands_for for each in rows), 0, 0)
    return MOD["Page"](name, MOD["Window"](0.0, 10800.0), totals, (("Claude", totals.agents),), coordinator, tuple(groups), tuple(items), hidden or MOD["Hidden"]())


def drawn(folded):
    """Each group's rows as (depth, label, stands_for), keyed by the work item's id."""
    return {one.item.id if one.item else None: [(each.depth, each.label, each.stands_for) for each in one.rows] for one in folded.groups}


class FoldTest(ActivityCase):
    def family(self, number, state, children, last=("completed", "completed")):
        """A unit with a worker and `children` children of it. `last` gives the last child's turn status and its delegation's status. A running delegation is open."""
        fixture = self.fixture
        fixture.unit(f"D{number}", state, thread=worker(number))
        start = 170 - number * 10
        fixture.thread(worker(number), turns=(("completed", start, start - 2),))
        for position in range(children):
            turn, delegation = last if position == children - 1 else ("completed", "completed")
            end = start - 3.5 - position
            fixture.thread(delegated(worker(number), f"part-{position}"), title=f"part {position}", parent=worker(number),
                           turns=((turn, start - 3 - position, end),), delegation=(delegation, start - 3 - position, None if delegation == "running" else end))

    def test_step_1_folds_two_or_more_quiet_rows_under_a_quiet_row_into_one_summary_row(self):
        self.family(1, "merged", 3, last=("cancelled", "cancelled"))
        self.family(2, "in-progress", 2, last=("failed", "failed"))
        self.family(3, "merged", 1)
        unfolded = page_of(self.fixture)
        folded = MOD["fold_finished_subagents"](unfolded)
        self.assertEqual(drawn(folded), {
            "D3": [(0, "worker", 1), (1, "part 0", 1)],
            "D2": [(0, "worker", 1), (1, "part 0", 1), (1, "part 1", 1)],
            "D1": [(0, "worker", 1), (1, "3 sub-agents", 3)]})
        summary = folded.groups[2].rows[1]
        self.assertEqual((summary.status.value, summary.seconds, summary.open_seconds, summary.model, summary.provider), ("done", 90, None, "model-a", "Claude"))
        self.assertEqual([(span.x, span.w, span.status.value) for span in summary.spans], [(128, 3, "done"), (133, 3, "done"), (139, 3, "done")])
        self.assertEqual((folded.fold, folded.totals, folded.legend), (1, unfolded.totals, unfolded.legend))
        self.assertEqual(MOD["notes"](folded), ["3 sub-agents that are done or stopped are shown as 1 summary row."])
        self.assertEqual(drawn(MOD["fold_finished_subagents"](folded)), drawn(folded))

    def test_step_2_folds_a_merged_or_dropped_unit_whose_rows_are_all_quiet_into_one_row(self):
        self.family(1, "merged", 3)
        self.family(2, "dropped", 1)
        self.family(3, "merged", 2, last=("completed", "running"))
        self.family(4, "in-review", 2)
        self.family(5, "merged", 0)
        folded = MOD["fold_quiet_items"](MOD["fold_finished_subagents"](page_of(self.fixture)))
        self.assertEqual(drawn(folded), {
            "D5": [(0, "worker", 1)],
            "D4": [(0, "worker", 1), (1, "2 sub-agents", 2)],
            "D3": [(0, "worker", 1), (1, "part 0", 1), (1, "part 1", 1)],
            "D2": [(0, "1 agent, 1 sub-agent", 2)],
            "D1": [(0, "1 agent, 3 sub-agents", 4)]})
        self.assertEqual(folded.groups[4].item, MOD["Item"]("D1", "", "merged", "", "", False))
        self.assertEqual(MOD["notes"](folded), [
            "2 sub-agents that are done or stopped are shown as 1 summary row.",
            "2 merged or dropped work items whose agents are all done or stopped are each shown as one row."])

    def test_step_3_keeps_the_6_latest_quiet_merged_or_dropped_units_and_counts_the_rest(self):
        for number in range(1, 9):
            self.family(number, "merged", 1 if number == 1 else 0)
        self.family(9, "in-progress", 0)
        self.family(10, "merged", 1, last=("failed", "failed"))
        folded = MOD["drop_old_items"](page_of(self.fixture))
        self.assertEqual(list(drawn(folded)), ["D10", "D9", "D8", "D7", "D6", "D5", "D4", "D3"])
        self.assertEqual((folded.hidden.dropped_items, folded.hidden.dropped_agents, folded.fold), (2, 3, 1))
        self.assertEqual(MOD["notes"](folded), ["2 merged or dropped work items with 3 agents are not shown. Use a larger --max-bytes to see more."])

    def test_step_4_keeps_6_groups_with_those_that_have_a_running_row_first(self):
        groups = [group(item(f"D{number}"), row("worker"), row("helper", depth=1, stands_for=3)) for number in range(1, 9)]
        groups.append(group(None, row("stray", status="running", open_seconds=5)))
        folded = MOD["cap_everything"](page(*groups, items=[item("D1")]))
        self.assertEqual(list(drawn(folded)), ["D1", "D2", "D3", "D4", "D5", None])
        self.assertEqual((folded.hidden.cut_agents, folded.fold), (12, 1))
        self.assertEqual(MOD["notes"](folded), [
            "15 sub-agents that are done or stopped are shown as 5 summary rows.",
            "12 more agents are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."])

    def test_step_4_keeps_6_rows_a_group_with_running_and_failed_rows_first_and_moves_a_row_up_when_its_parent_is_cut(self):
        rows = [row("a"), row("b", depth=1), row("c", depth=2, status="running", open_seconds=9), row("d", depth=2)]
        rows += [row(label, status="failed") for label in "efghi"] + [row("j")]
        folded = MOD["cap_everything"](page(group(item("D1"), *rows)))
        self.assertEqual(drawn(folded), {"D1": [(0, "c", 1), (0, "e", 1), (0, "f", 1), (0, "g", 1), (0, "h", 1), (0, "i", 1)]})
        self.assertEqual(folded.hidden.cut_agents, 4)
        kept = MOD["cap_everything"](page(group(item("D1"), row("a"), row("b", depth=1), row("c", depth=2, status="running", open_seconds=9))))
        self.assertEqual(drawn(kept), {"D1": [(0, "a", 1), (1, "b", 1), (2, "c", 1)]})

    def test_step_4_joins_the_nearest_bars_until_4_are_left_on_a_row_and_8_on_the_coordinators(self):
        spans = ((0, 10, "done"), (12, 10, "failed"), (100, 10, "done"), (115, 10, "stopped"), (300, 10, "done"), (500, 10, "running"), (700, 10, "done"))
        folded = MOD["cap_everything"](page(group(item("D1"), row("a", spans=spans)), coordinator=row("coordinator", spans=spans + ((900, 5, "done"), (906, 5, "done")))))
        self.assertEqual([(span.x, span.w, span.status.value) for span in folded.groups[0].rows[0].spans],
                         [(0, 125, "failed"), (300, 10, "done"), (500, 10, "running"), (700, 10, "done")])
        self.assertEqual([(span.x, span.w) for span in folded.coordinator.spans],
                         [(0, 10), (12, 10), (100, 10), (115, 10), (300, 10), (500, 10), (700, 10), (900, 11)])

    def test_step_4_lists_8_in_flight_items_with_those_of_a_group_on_the_page_first(self):
        items = [item(f"D{number}") for number in range(1, 13)]
        folded = MOD["cap_everything"](page(group(items[10], row("worker")), items=items))
        self.assertEqual([each.id for each in folded.items], ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D11"])
        self.assertEqual(folded.hidden.cut_in_flight, 4)
        self.assertEqual(MOD["notes"](folded), ["4 more work items in flight are not listed."])

    def test_step_4_clips_strings_by_their_encoded_bytes_and_drops_a_link_over_90_bytes(self):
        long = item("D" + "1" * 20, summary="<" * 30, pr="https://example.test/" + "x" * 70)
        short = item("D2", summary="𝔸" * 30, pr="https://example.test/" + "x" * 69)
        folded = MOD["cap_everything"](page(group(long, row("é" * 30, model="m" * 40)), group(short, row("worker")), items=[long, short], name="𝔸" * 20))
        first, second = folded.groups[0], folded.groups[1]
        self.assertEqual((first.item.id, first.item.summary, first.item.pr), ("D" + "1" * 8 + "…", "<" * 5 + "…", ""))
        self.assertEqual((second.item.summary, second.item.pr), ("𝔸" * 8 + "…", "https://example.test/" + "x" * 69))
        self.assertEqual((first.rows[0].label, first.rows[0].model, folded.name), ("é" * 8 + "…", "m" * 27 + "…", "𝔸" * 8 + "…"))
        self.assertEqual(folded.items, (first.item, second.item))

    def test_every_fold_step_on_the_busy_fixture_keeps_the_agent_count_the_totals_and_the_legend(self):
        unfolded = folded = page_of(populate(self.fixture, units=36, agents=400, running=5, failed=3))
        self.assertEqual((unfolded.totals, sum(each.stands_for for one in unfolded.groups for each in one.rows)), (MOD["Totals"](5, 36, 364, 3), 400))
        shown = []
        for step in MOD["FOLDS"]:
            folded = step(folded)
            rows = [each for one in folded.groups for each in one.rows]
            shown.append(len(rows))
            with self.subTest(step.__name__):
                self.assertEqual(sum(each.stands_for for each in rows) + folded.hidden.dropped_agents + folded.hidden.cut_agents, 400)
                self.assertEqual((folded.totals, folded.legend), (unfolded.totals, unfolded.legend))
        self.assertEqual(shown, sorted(shown, reverse=True))
        self.assertEqual((folded.fold, len(folded.groups), len(folded.items), shown[-1] < shown[0]), (4, 6, 8, True))

    def test_notes_say_no_agent_ran_for_an_empty_window_and_count_unread_statuses_requests_and_other_threads(self):
        self.assertEqual(MOD["notes"](page()), ["No agent or sub-agent of this coordinator ran in this window."])
        hidden = MOD["Hidden"](dropped_items=1, dropped_agents=1, cut_agents=1, cut_in_flight=1, other_threads=1, unknown_status=1, by_request_name=1)
        self.assertEqual(MOD["notes"](page(group(item("D1"), row("worker")), hidden=hidden)), [
            "1 merged or dropped work item with 1 agent is not shown. Use a larger --max-bytes to see more.",
            "1 more agent is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            "1 more work item in flight is not listed.",
            "T3 gave 1 status this tool cannot read.",
            "1 agent is grouped by the name of the request that started it.",
            "1 other thread ran in T3 outside this coordinator."])
        several = MOD["Hidden"](other_threads=3, unknown_status=2, by_request_name=9)
        self.assertEqual(MOD["notes"](page(group(item("D1"), row("worker")), hidden=several)), [
            "T3 gave 2 statuses this tool cannot read.",
            "9 agents are grouped by the name of the request that started them.",
            "3 other threads ran in T3 outside this coordinator."])


DATA = re.compile(r"<script type=application/json id=d>(.*?)</script>", re.S)
PROGRAM = re.compile(r"<script>(const H=(\d+);.*)</script>$", re.S)
STORE_WORDS = re.compile(r"\b(dish|dishes|rail|86|pass|station|restaurant|fire|chef)\b", re.I)
PR7 = "https://example.test/o/r/pull/7"
PR6 = "https://example.test/o/r/pull/6"
# A document with no HTML parser. It keeps every node the renderer makes.
STUB = """
const nodes = [];
const make = tag => ({
  tagName: tag.toUpperCase(), kids: [], style: {}, className: '', own: '',
  appendChild(kid) { this.kids.push(kid); return kid; },
  set textContent(value) { this.kids = []; this.own = String(value); },
  get textContent() { return this.own; },
});
const page = make('div'), document = {
  getElementById: id => id === 'd' ? {textContent: DATA} : page,
  createElement: tag => { const node = make(tag); nodes.push(node); return node; },
};
"""
REPORT = """
console.log(JSON.stringify({
  texts: nodes.map(node => node.own).filter(Boolean),
  links: nodes.filter(node => node.href).map(node => [node.tagName, node.href, node.target, node.rel]),
  classes: nodes.map(node => node.className),
  titles: nodes.filter(node => node.title).map(node => node.title),
  bars: nodes.filter(node => node.className.startsWith('b ')).map(node => [node.style.left, node.style.width]),
}));
"""


def empty(fixture):
    fixture.unit("D7", "in-progress", "Agent activity page", pr=PR7)
    fixture.coordinator(turns=(("completed", 400, 390),))
    return fixture


def one_running(fixture):
    fixture.coordinator()
    fixture.unit("D7", "in-progress", "Agent activity page", thread=worker(1), pr=PR7)
    fixture.unit("D6", "queued", "Queue fix", pr=PR6)
    fixture.thread(worker(1), title="D7 worker", turns=(("completed", 100.5, 80), ("running", 42.5, None)))
    return fixture


def failed_child(fixture):
    fixture.coordinator(turns=(("completed", 150, 140), ("running", 5.5, None)), model="model-c")
    fixture.unit("D7", "in-review", "Agent activity page", thread=worker(1), pr=PR7)
    fixture.unit("D6", "queued", "Queue fix", pr=PR6)
    fixture.unit("D5", "merged", "Old work", thread=worker(5))
    fixture.thread(worker(1), title="D7 worker", turns=(("completed", 120.5, 100), ("running", 42.5, None)))
    runner = delegated(worker(1), "architect-d7-runner-2")
    fixture.thread(runner, title="architect runner 2", provider="codex", model="model-b", parent=worker(1), turns=(("running", 6.5, None),))
    fixture.thread(native(1), title="/root/spec_review", provider="codex", model="model-b", parent=runner, delegation=("completed", 5, 4))
    fixture.thread(delegated(COORDINATOR, "brigade-kit-d7-verify-0a1b2c3"), title="Act as the review sub-agent for this task.",
                   provider="grok", model="vendor/model-c", parent=COORDINATOR, turns=(("failed", 30.5, 27),))
    fixture.thread(worker(5), title="D5 worker", turns=(("completed", 170, 165),))
    fixture.thread(delegated(COORDINATOR, "why-investigator"), title="Read the brief at /pathmarker/brief.md", parent=COORDINATOR, turns=(("interrupted", 90, 88),))
    fixture.thread("mcp:threadmarker-other-project", turns=(("completed", 20, 10),))
    return fixture


def typical(fixture):
    return populate(fixture, units=9, agents=50, running=3, failed=1)


def busy(fixture):
    return populate(fixture, units=36, agents=400, running=5, failed=3)


def extreme(fixture):
    return populate(fixture, units=200, agents=3000, running=60, failed=10, heavy=True)


def data_of(document):
    return json.loads(DATA.search(document).group(1))


def strings(value):
    if isinstance(value, str):
        return [value]
    return [text for each in value for text in strings(each)] if isinstance(value, list) else []


def agents_shown(data):
    return sum(line[7] if len(line) > 7 else 1 for _, lines in data["G"] for line in lines)


class OutputCase(ActivityCase):
    def out(self, *args, **how):
        result = self.fixture.run(*args, **how)
        self.assertEqual((result.returncode, result.stderr), (0, ""), result.stdout[:200])
        return result.stdout

    def document(self, *args, **how):
        """The document a run prints, without the newline print adds."""
        out = self.out(*args, **how)
        self.assertTrue(out.startswith("<!doctype html>") and out.endswith("</script>\n"), out[:80])
        return out[:-1]


class DocumentTest(OutputCase):
    def test_default_window_is_3_hours_and_hours_changes_which_turns_are_on_the_page(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 200, 190), ("completed", 100, 90)))
        fixture.meta.pop("thread")
        fixture.write()
        for args, length, bars in (((), 10800, 1), (("--hours", "4"), 14400, 2)):
            with self.subTest(args):
                data = data_of(self.document(*args))
                self.assertEqual((data["w"][1], len(data["G"][0][1][0][6]) // 3), (length, bars))

    def test_data_object_holds_the_counts_legend_items_groups_and_notes(self):
        failed_child(self.fixture).write()
        data = data_of(self.document())
        self.assertEqual(sorted(data), ["G", "I", "M", "N", "P", "S", "c", "k", "n", "v", "w"])
        self.assertEqual((data["v"], data["c"], data["n"], data["w"][1]), (1, "kit", [2, 2, 4, 1], 10800))
        self.assertEqual(data["S"], ["running", "queued", "waiting", "done", "failed", "stopped", "unknown"])
        self.assertEqual(data["P"], [["Claude", 1, 3], ["Codex", 2, 2], ["Grok", 3, 1]])
        self.assertEqual(data["M"], ["model-c", "model-a", "model-b"])
        self.assertEqual(data["I"], [["D6", "Queue fix", "landing", "warn", PR6, 1], ["D7", "Agent activity page", "in review", "info", PR7, 1],
                                     ["D5", "Old work", "merged", "", "", 0]])
        self.assertEqual([index for index, _ in data["G"]], [1, 2, -1])
        self.assertEqual([line[:5] + line[7:8] for line in data["G"][0][1]],
                         [[0, "worker", 1, 0, 0, 1], [1, "architect runner 2", 2, 1, 0, 1], [2, "spec_review", 2, 1, 3], [0, "review", 0, 2, 4]])
        self.assertEqual([[line[8] // 60 for line in lines if len(line) > 8] for _, lines in data["G"]], [[42, 6], [], []])
        self.assertEqual([line[:2] + line[6][2:] + line[7:] for line in data["G"][2][1]], [[0, "why investigator", 3]])
        self.assertEqual([[bars[2::3] for bars in (line[6] for line in lines)] for _, lines in data["G"]], [[[0, 1], [1], [0], [2]], [[0]], [[3]]])
        self.assertEqual((data["k"][:2], data["k"][3][2::3]), ([0, 0], [0, 0]))
        self.assertEqual(data["N"], ["1 agent is grouped by the name of the request that started it.", "1 other thread ran in T3 outside this coordinator."])

    def test_checksum_in_the_document_is_fnv_1a_over_the_data_elements_utf16_units(self):
        fixture = one_running(self.fixture)
        fixture.units[0]["summary"] = "Agent activity page 𝔸é"
        fixture.write()
        document = self.document()
        value = 2166136261
        units = DATA.search(document).group(1).encode("utf-16-le")
        for at in range(0, len(units), 2):
            value = ((value ^ (units[at] | units[at + 1] << 8)) * 16777619) % 2 ** 32
        self.assertEqual(int(PROGRAM.search(document).group(2)), value)
        self.assertEqual((MOD["checksum"]("a"), MOD["checksum"]("foobar")), (0xE40C292C, 0xBF9CF968))

    def test_markup_in_a_summary_a_title_and_a_model_leaves_no_angle_bracket_or_ampersand_in_the_data_element(self):
        fixture = one_running(self.fixture)
        hostile = "x</script><img onerror=a(1)> \"q\" & 'p'"
        fixture.units[0]["summary"] = hostile
        fixture.thread(delegated(worker(1), "helper"), title=hostile, model="<b>&model", parent=worker(1), turns=(("completed", 20, 10),))
        fixture.write()
        raw = DATA.search(self.document()).group(1)
        self.assertEqual([character for character in "<>&" if character in raw], [])
        data = json.loads(raw)
        self.assertEqual((data["I"][1][1], data["G"][0][1][1][1], data["M"]), (hostile, hostile, ["model-a", "<b>&model"]))
        self.assertEqual(MOD["encode"]({"a": "</script>\u2028\u2029&"}), '{"a":"\\u003c/script\\u003e\\u2028\\u2029\\u0026"}')

    def test_no_invented_id_or_path_is_in_the_document_or_the_text(self):
        for build in (failed_child, busy):
            with self.subTest(build.__name__):
                fixture = self.fixture = Fixture(self.fixture.root / build.__name__)
                build(fixture).write()
                for output in (self.document(), self.out("--text"), self.document("--max-bytes", "500000")):
                    self.assertEqual([marker for marker in MARKERS if marker in output], [])

    def test_each_store_state_reads_as_its_plain_word_and_no_store_word_is_in_the_page_or_the_text(self):
        fixture = self.fixture
        fixture.coordinator()
        states = ("in-progress", "in-review", "passed", "queued", "sent-back", "blocked", "merged", "dropped", "plated")
        for number, state in enumerate(states, start=1):
            fixture.unit(f"D{number}", state, f"summary {number}", thread=worker(number))
            fixture.thread(worker(number), turns=(("completed", 100 - number, 99 - number),))
        fixture.write()
        data, text = data_of(self.document()), self.out("--text")
        words = ["working", "in review", "passed review", "landing", "sent back", "blocked", "merged", "dropped", "other"]
        self.assertEqual([item[2] for item in sorted(data["I"], key=lambda item: item[0])], words)
        for word in words:
            self.assertIn(f"   {word}   summary", text)
        for output in (" ".join(strings(list(data.values()))), text, MOD["STYLE"], MOD["RENDERER"]):
            self.assertEqual(STORE_WORDS.findall(output), [])
            self.assertEqual([state for state in ("in-progress", "in-review", "sent-back", "plated") if state in output], [])

    def test_renderer_source_holds_no_call_that_parses_a_string_as_html_or_code(self):
        self.assertEqual([call for call in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "Function(", "setTimeout", "\"", "`") if call in MOD["RENDERER"]], [])
        self.assertIn(MOD["UNGROUPED"], MOD["RENDERER"])

    def test_stylesheet_sets_no_background_on_the_page_and_takes_colors_only_from_theme_variables(self):
        style = MOD["STYLE"]
        rules = dict(re.findall(r"([^{}]+)\{([^{}]*)\}", style.replace("@media(max-width:520px){", "")))
        self.assertEqual(rules["#o"], "font:13px/1.4 var(--font-sans);color:var(--foreground)")
        self.assertEqual([selector for selector in rules if re.fullmatch(r"(html|body|\*|:root)", selector)], [])
        self.assertEqual(re.findall(r"[0-9](?:vh|vw)\b|#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", style), [])
        allowed = {"--foreground", "--muted-foreground", "--border", "--success", "--destructive", "--warning", "--info", "--radius", "--font-sans", "--font-mono"}
        allowed |= {f"--chart-{number}" for number in range(1, 7)}
        own = set(re.findall(r"(--[\w-]+):", style))
        self.assertEqual(own, {"--c", "--lab"})
        self.assertEqual(set(re.findall(r"var\((--[\w-]+)", style)) - own - allowed, set())
        self.assertEqual(re.findall(r"@media[^{]*", style), ["@media(max-width:520px)"])


class SizeTest(OutputCase):
    def test_empty_window_is_at_most_the_fixed_budget_and_says_no_agent_ran(self):
        empty(self.fixture).write()
        document = self.document()
        data = data_of(document)
        self.assertLessEqual(len(document.encode()), MOD["FIXED_BUDGET"])
        self.assertEqual((data["n"], data["G"], data["k"], data["N"]), ([0, 0, 0, 0], [], None, ["No agent or sub-agent of this coordinator ran in this window."]))
        self.assertEqual([item[0] for item in data["I"]], ["D7"])

    def test_typical_window_is_unfolded_and_at_most_16000_bytes(self):
        typical(self.fixture).write()
        document = self.document()
        data = data_of(document)
        self.assertLessEqual(len(document.encode()), 16000)
        self.assertEqual((data["n"], agents_shown(data), sum(len(lines) for _, lines in data["G"])), ([3, 9, 41, 1], 50, 50))
        self.assertEqual(data["N"], ["14 agents are grouped by the name of the request that started them."])

    def test_busy_window_is_folded_to_at_most_16000_bytes_with_a_note_and_max_bytes_buys_every_row_back(self):
        busy(self.fixture).write()
        document = self.document()
        data = data_of(document)
        self.assertLessEqual(len(document.encode()), 16000)
        self.assertEqual(data["n"], [5, 36, 364, 3])
        self.assertLess(sum(len(lines) for _, lines in data["G"]), 400)
        self.assertRegex(" ".join(data["N"]), r"shown as \d+ summary rows?\.|not shown")
        whole = data_of(self.document("--max-bytes", "500000"))
        self.assertEqual((whole["n"], agents_shown(whole), sum(len(lines) for _, lines in whole["G"])), ([5, 36, 364, 3], 400, 400))
        self.assertEqual(whole["N"], ["112 agents are grouped by the name of the request that started them."])

    def test_extreme_window_with_four_byte_characters_and_long_links_is_at_most_16000_bytes(self):
        extreme(self.fixture).write()
        document = self.document()
        data = data_of(document)
        self.assertLessEqual(len(document.encode()), 16000)
        self.assertEqual((data["n"], len(data["G"]), len([item for item in data["I"] if item[5]])), ([60, 200, 2800, 10], 6, 8))
        self.assertEqual(self.out("--text").split("\n")[1], "60 running now, 200 agents, 2800 sub-agents, 10 failed")
        self.assertLessEqual(len(self.out("--text").rstrip("\n").split("\n")), 40)

    def test_page_with_every_field_at_its_largest_fits_16000_bytes_after_the_last_fold_step(self):
        wide, spans = "𝕏" * 400, tuple((x * 40, 1, "failed") for x in range(25))
        def big(number):
            return item("D" + "9" * 30 + str(number), summary="<" * 400, pr="https://example.test/" + "x" * 69)
        def rows(prefix):
            return [row(prefix + wide + str(n), depth=min(n, 2), status="running", spans=spans, open_seconds=604800, stands_for=99999, model=wide + str(n)) for n in range(40)]
        groups = [group(big(number), *rows(str(number))) for number in range(40)]
        groups.append(group(big(98), row("worker"), row("9 sub-agents", depth=1, stands_for=99999)))
        hidden = MOD["Hidden"](**dict.fromkeys(("dropped_items", "dropped_agents", "cut_agents", "cut_in_flight", "other_threads", "unknown_status", "by_request_name"), 9999999))
        largest = MOD["cap_everything"](page(*groups, items=[big(number) for number in range(100, 140)], coordinator=row("coordinator", spans=spans, model=wide), hidden=hidden, name=wide))
        self.assertEqual((len(largest.groups), [len(one.rows) for one in largest.groups], len(largest.items)), (6, [6] * 6, 8))
        self.assertLessEqual(len(MOD["render_html"](largest).encode()), 16000)


class TextTest(OutputCase):
    def test_text_lists_running_agents_work_items_failed_agents_and_notes(self):
        failed_child(self.fixture).write()
        self.assertEqual(self.out("--text"), "\n".join([
            "Agent activity for kit, last 3 hours",
            "2 running now, 2 agents, 4 sub-agents, 1 failed",
            "Running now",
            "  D7 worker   model-a   running for 42m",
            "  D7 architect runner 2   model-b   running for 6m   under worker",
            "Work items",
            f"  D7   in review   Agent activity page   1 agent, 3 sub-agents, 74m at work   {PR7}",
            "  D5   merged   Old work   1 agent, 5m at work",
            f"  D6   landing   Queue fix   no activity in this window   {PR6}",
            "  Not tied to a work item: 1 sub-agent, 2m at work",
            "Failed",
            "  D7 review   model-c   3m at work",
            "Notes",
            "  1 agent is grouped by the name of the request that started it.",
            "  1 other thread ran in T3 outside this coordinator.",
            ""]))

    def test_text_and_document_hold_the_same_four_counts(self):
        busy(self.fixture).write()
        self.assertEqual(data_of(self.document())["n"], [5, 36, 364, 3])
        self.assertEqual(self.out("--text").split("\n")[1], "5 running now, 36 agents, 364 sub-agents, 3 failed")

    def test_text_cuts_each_list_and_counts_the_rest(self):
        lines = self.render(busy).split("\n")
        self.assertEqual((lines[2], lines[8]), ("Running now", "Work items"))
        self.assertEqual(len(lines[3:8]), 5)
        self.assertEqual((len(lines[9:18]), lines[18]), (9, "  and 27 more work items"))
        self.assertEqual((lines[19], len(lines[20:23]), lines[23]), ("Failed", 3, "Notes"))
        cut = MOD["render_text"](page(group(item("D1"), *[row(f"r{n}", status="running", open_seconds=5) for n in range(9)],
                                              *[row(f"f{n}", status="failed") for n in range(5)]))).split("\n")
        self.assertEqual((cut[10], cut[18]), ("  and 2 more running agents", "  and 1 more failed agent"))

    def render(self, build):
        build(self.fixture).write()
        return self.out("--text")

    def test_one_hour_window_reads_last_1_hour(self):
        one_running(self.fixture).write()
        self.assertEqual(self.out("--text", "--hours", "1").split("\n")[0], "Agent activity for kit, last 1 hour")


class RunTest(OutputCase):
    def test_out_writes_the_document_and_prints_one_line_with_the_file_and_its_size(self):
        one_running(self.fixture).write()
        target = self.fixture.root / "page.html"
        printed = self.out("--out", str(target))
        document = target.read_bytes()
        self.assertEqual(printed, f"wrote {target} ({len(document)} bytes)\n")
        self.assertTrue(document.startswith(b"<!doctype html>") and document.endswith(b"</script>"))
        printed = self.out("--text", "--out", str(target))
        self.assertEqual(printed, f"wrote {target} ({len(target.read_bytes())} bytes)\n")
        self.assertTrue(target.read_text().startswith("Agent activity for kit, last 3 hours\n"))

    def test_out_in_a_directory_that_does_not_exist_exits_1(self):
        one_running(self.fixture).write()
        line = self.fails(1, "--out", str(self.fixture.root / "pathmarker absent" / "page.html"))
        self.assertEqual(line, "activity: cannot write the --out file (No such file or directory); pass a path this user can write")

    def test_run_changes_no_file_in_the_store_or_beside_a_closed_database(self):
        fixture = failed_child(self.fixture).write()
        before = listing(fixture.store), listing(fixture.database.parent)
        self.assertEqual(sorted(before[0]), ["dishes.tsv", "log.tsv", "restaurant.json"])
        self.document()
        self.out("--text")
        self.assertEqual((listing(fixture.store), listing(fixture.database.parent)), before)

    def test_run_beside_a_live_database_reads_its_log_and_keeps_the_same_files_and_database_and_log_bytes(self):
        fixture = failed_child(self.fixture).write(live=True)
        before = listing(fixture.database.parent)
        self.assertEqual(sorted(before), ["statev2.sqlite", "statev2.sqlite-shm", "statev2.sqlite-wal"])
        self.assertEqual(data_of(self.document())["n"], [2, 2, 4, 1])
        self.assertEqual(live_listing(fixture.database.parent), live_listing(fixture.database.parent, before))

    def test_database_under_a_directory_with_a_space_a_question_mark_and_a_hash_is_read(self):
        fixture = self.fixture
        fixture.base = fixture.root / "pathmarker t3 ?base #1"
        fixture.database = fixture.base / "userdata" / "statev2.sqlite"
        one_running(fixture).write()
        self.assertEqual(data_of(self.document())["n"], [1, 1, 0, 0])

    def test_t3code_home_names_the_base_directory_when_the_flag_is_absent(self):
        fixture = one_running(self.fixture).write()
        self.assertEqual(data_of(self.document(t3_home=None, env={"T3CODE_HOME": str(fixture.base)}))["n"], [1, 1, 0, 0])

    def test_read_waits_while_a_writer_holds_the_stores_lock(self):
        fixture = one_running(self.fixture).write()
        with open(fixture.store / "restaurant.lock", "w") as held:
            before = listing(fixture.store)
            fcntl.flock(held, fcntl.LOCK_EX)
            process = fixture.start()
            self.addCleanup(process.kill)
            with self.assertRaises(subprocess.TimeoutExpired):
                process.wait(timeout=2)
            fcntl.flock(held, fcntl.LOCK_UN)
            self.assertEqual(process.wait(timeout=30), 0)
            process.stdout.close()
            process.stderr.close()
        self.assertEqual(listing(fixture.store), before)

    def test_reference_page_holds_no_value_from_the_machine(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import cli_reference
        hostile = {"BRIGADE_DIR": "/tmp/SENTINEL", "T3CODE_HOME": "/tmp/SENTINEL", "HOME": "/tmp/SENTINEL"}
        with mock.patch.dict(os.environ, hostile):
            parser = cli_reference.load(SCRIPT)
            text = cli_reference.render_tool(ROOT, "t3/added/brigade/scripts/activity.py")
        self.assertEqual(parser.prog, "activity.py")
        self.assertEqual([action.default for action in parser._actions if isinstance(action.default, str) and action.default != "==SUPPRESS=="], [])
        self.assertEqual([word for word in ("SENTINEL", str(Path.home()), str(ROOT)) if word in text], [])


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class RendererTest(OutputCase):
    def render(self, document, spoil=False):
        """What the renderer builds from a document, in a document object that has no HTML parser."""
        data, program = DATA.search(document).group(1), PROGRAM.search(document).group(1)
        if spoil:
            data = data.replace('"kit"', '"kat"', 1)
        script = self.fixture.root / "render.js"
        script.write_text(f"const DATA = {json.dumps(data)};{STUB}{program}{REPORT}")
        result = subprocess.run(["node", str(script)], capture_output=True, text=True, env={"PATH": os.environ["PATH"], "TZ": "UTC"})
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        return json.loads(result.stdout)

    def test_renderer_draws_the_counts_the_running_list_the_rows_the_items_and_the_notes(self):
        failed_child(self.fixture).write()
        document = self.document()
        built = self.render(document)
        texts = built["texts"]
        for text in ("2", "4", "1", "running now", "agents, last 3h", "sub-agents", "failed", "Running now", "D7 worker", "model-a", "42m",
                     "D7 architect runner 2", "model-b · under worker", "6m", "Timeline", "Claude 3", "Codex 2", "Grok 1", "coordinator ", "worker ",
                     "architect runner 2 ", "spec_review ", "review ", "model-c", "why investigator ", "Not tied to a work item", "Work items in flight",
                     "D7", "Agent activity page", "in review", "D6", "Queue fix", "landing", "D5", "Old work", "merged",
                     "1 agent is grouped by the name of the request that started it.", "1 other thread ran in T3 outside this coordinator."):
            self.assertIn(text, texts)
        self.assertNotIn("This copy differs from what the tool wrote. Run the command again.", texts)
        self.assertRegex(texts[-3], r"^As of \d+:\d\d [AP]M for kit\. Each bar is time an agent was at work\. A striped bar is still running, and a faded bar was stopped\.$")
        self.assertEqual(built["links"], [["A", PR7, "_blank", "noopener"], ["A", PR6, "_blank", "noopener"], ["A", PR7, "_blank", "noopener"]])
        classes = built["classes"]
        self.assertEqual([classes.count(name) for name in ("stat live", "stat alarm", "pulse", "row co", "row p1", "row p2", "row p3", "grp", "b run", "b f", "b stop")],
                         [1, 1, 2, 1, 3, 2, 1, 3, 2, 1, 1])
        self.assertEqual(len(built["bars"]), sum(len(line[6]) // 3 for _, lines in data_of(document)["G"] for line in lines) + 2)
        self.assertEqual([bar for bar in built["bars"] if not all(re.fullmatch(r"[0-9]+(\.[0-9])?%", side) for side in bar)], [])
        self.assertIn("review · failed · 3m", built["titles"])

    def test_renderer_says_the_copy_differs_when_one_character_of_the_data_changed(self):
        one_running(self.fixture).write()
        texts = self.render(self.document(), spoil=True)["texts"]
        self.assertEqual(texts[0], "This copy differs from what the tool wrote. Run the command again.")
        self.assertIn("D7 worker", texts)

    def test_renderer_draws_a_folded_page_and_an_empty_one_with_no_error(self):
        for build, heading in ((busy, "Running now"), (empty, "Timeline")):
            with self.subTest(build.__name__):
                fixture = self.fixture = Fixture(self.fixture.root / build.__name__)
                build(fixture).write()
                self.assertIn(heading, self.render(self.document())["texts"])


if __name__ == "__main__":
    unittest.main()
