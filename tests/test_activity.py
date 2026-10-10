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
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "t3/added/brigade/scripts/activity.py"
MOD = runpy.run_path(str(SCRIPT))
SKILL = ROOT / "t3/added/brigade/SKILL.md"
GUIDE = ROOT / "docs/guide.md"

MARKERS = ("threadmarker", "nodemarker", "pathmarker", "homemarker", "0a1b2c3")
COORDINATOR = "mcp:threadmarker-coordinator"
CHANGED = "this T3 build stores threads differently, so update pstack-t3"
FROZEN = "import runpy, sys, time\nclock = float(sys.argv[1])\ntime.time = lambda: clock\ndel sys.argv[:2]\nrunpy.run_path(sys.argv[0], run_name='__main__')"
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
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(Path(directory).iterdir()) if path.is_file()}


def live_listing(directory, files=None):
    """listing() with the -shm file's bytes left out. Every SQLite reader of a live database writes its read mark there."""
    files = listing(directory) if files is None else files
    return {name: "" if name.endswith("-shm") else digest for name, digest in files.items()}


class Fixture:
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

    def thread(self, ident, title="", provider="claudeAgent", model="model-a", parent=None, turns=(), delegation=None, payload=None, instance=None):
        selection = {"instanceId": provider if instance is None else instance, "model": model}
        self.threads.append((ident, title, provider, json.dumps({"modelSelection": selection}) if payload is None else payload))
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

    def command(self, *args, at=True, t3_home=True, env=None, clock=None, cwd=None):
        """With clock, the script runs as the main program of a process whose time.time() returns that instant."""
        words = [sys.executable, str(SCRIPT)] if clock is None else [sys.executable, "-c", FROZEN, repr(clock), str(SCRIPT)]
        words += ["--at", str(self.store)] if at is True else ["--at", str(at)] if at else []
        words += ["--t3-home", str(self.base)] if t3_home is True else ["--t3-home", str(t3_home)] if t3_home else []
        environment = {"HOME": str(self.home), "PATH": os.environ.get("PATH", ""), **(env or {})}
        return [*words, *args], {"text": True, "env": environment, "cwd": cwd or self.root}

    def run(self, *args, **how):
        words, options = self.command(*args, **how)
        return subprocess.run(words, capture_output=True, **options)

    def stdout_bytes(self, *args, **how):
        words, options = self.command(*args, **how)
        result = subprocess.run(words, capture_output=True, **dict(options, text=False))
        assert (result.returncode, result.stderr) == (0, b""), result.stderr
        return result.stdout

    def stamp_at_offset(self, minutes, hours):
        return datetime.fromtimestamp(self.now - minutes * 60, timezone(timedelta(hours=hours))).isoformat(timespec="milliseconds")

    def start(self, *args, **how):
        words, options = self.command(*args, **how)
        return subprocess.Popen(words, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)

    def read(self, hours=3.0):
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
        self.root = Path(directory.name)
        self.temporary = Path(directory.name).name
        self.addCleanup(self.fixture.close)

    def fails(self, status, *args, **how):
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
             unit("D8", "merged", "Queue fix", "", "", (), "")), fixture.meta["projectRoot"]))

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

    def test_name_project_root_thread_or_previous_thread_of_the_store_record_that_is_not_null_and_not_a_string_or_holds_a_nul_or_a_lone_surrogate_exits_1_and_names_it(self):
        cases = (("restaurant", 7, "name"), ("restaurant", "k\x00it", "name"), ("projectRoot", ["proj"], "project root"), ("projectRoot", "/pathmarker/a\x00b", "project root"),
                 ("projectRoot", "/pathmarker/\ud800", "project root"), ("thread", 5, "thread"), ("thread", "mcp:threadmarker-\ud800", "thread"), ("thread", "mcp:threadmarker\x00", "thread"),
                 ("previousThread", [COORDINATOR], "previous thread"), ("previousThread", "mcp:threadmarker-\udcff", "previous thread"), ("previousThread", 1e999, "previous thread"))
        for number, (key, value, words) in enumerate(cases):
            with self.subTest(key=key, value=value):
                fixture = self.fixture = one_running(Fixture(self.root / str(number)))
                fixture.meta[key] = value
                fixture.write()
                for form in ((), ("--text",)):
                    self.assertEqual(self.fails(1, *form), f"activity: the store's coordinator record holds a {words} this tool cannot read; restore it and run this again")

    def test_store_record_with_a_null_or_absent_name_project_root_thread_and_previous_thread_is_read(self):
        for meta in ({"restaurant": None, "projectRoot": None, "thread": None, "previousThread": None}, {}):
            with self.subTest(meta):
                fixture = self.fixture = Fixture(self.root / str(len(meta)))
                fixture.meta = meta
                fixture.write_store()
                self.assertEqual(MOD["read_store"](fixture.store), MOD["Store"]("", frozenset(), (), (), ""))

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
        for hours in ("0", "-1", "168.5", "nan", "inf", "1e-100", "0.0099"):
            with self.subTest(hours):
                self.assertEqual(self.fails(1, f"--hours={hours}"), "activity: --hours must be from 0.01 to 168; pass a number in that range")

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


    @unittest.skipIf(os.geteuid() == 0, "root can enter every directory")
    def test_base_directory_this_user_cannot_enter_exits_3(self):
        self.fixture.write_store()
        locked = self.fixture.root / "pathmarker locked"
        (locked / "userdata").mkdir(parents=True)
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        self.assertEqual(self.fails(3, t3_home=str(locked)),
                         "activity: cannot look for T3's database under the --t3-home directory (Permission denied); check its permissions and run this again")


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
            ("orchestration_v2_projection_subagents.started_at", lambda f: f.delegations.append(("node:nodemarker", COORDINATOR, worker(1), "completed", "yesterday pathmarker", None))),
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
        self.assertEqual(name("thread:delegated-task:delegate-task%3Aouter%3Adelegate-task%3Ainner"), "inner")
        self.assertIsNone(name(worker(1)))
        self.assertIsNone(name(native(1)))

    def test_parse_time_reads_a_z_suffix_or_an_offset_with_or_without_milliseconds_and_refuses_a_text_with_no_zone(self):
        parse = MOD["parse_time"]
        self.assertEqual(parse("2026-10-03T01:49:21.841Z", "t.c"), 1790992161.841)
        self.assertEqual(parse("2026-10-03T01:49:21Z", "t.c"), 1790992161.0)
        self.assertEqual(parse("2026-10-03T03:49:21+02:00", "t.c"), 1790992161.0)
        self.assertEqual(parse("2026-10-02T18:49:21.841-07:00", "t.c"), 1790992161.841)
        for text in ("2026-10-03T01:49:21", "2026-10-03T01:49:21.841", "2026-10-03", "2026-10-03T01:49:21.8Z", "2026-10-03T01:49:21+0200", "2026-10-03T01:49:21Z\n", "٢٠٢٦-10-03T01:49:21Z",
                     "2026-02-30T01:49:21Z", "2026-10-03T25:49:21Z", None, 1790992161):
            with self.subTest(text), self.assertRaises(MOD["SourceError"]):
                parse(text, "t.c")

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

    def test_read_t3_keeps_only_turns_that_touch_the_window(self):
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
    return MOD["build_page"](store, t3, window, MOD["privacy_of"](store, t3))


def labels(page):
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
        self.assertEqual(MOD["build_page"](store, t3, MOD["Window"](fixture.now - 10800, fixture.now), MOD["privacy_of"](store, t3)).hidden.by_request_name, 3)

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
        late, early = delegated(worker(1), "late-part"), delegated(worker(1), "early-part")
        fixture.thread(late, title="late", parent=worker(1), turns=(("completed", 40, 35),))
        fixture.thread(early, title="early", parent=worker(1), turns=(("completed", 70, 60),))
        deep = delegated(early, "deep-part")
        fixture.thread(deep, title="deep", parent=early, turns=(("completed", 65, 64),))
        fixture.thread(delegated(deep, "deeper-part"), title="deeper", parent=deep, turns=(("completed", 64.5, 64.2),))
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
        fixture.coordinator(turns=(("completed", 60, 50), ("running", 5, None)), model="claudeAgent/model-c")
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

    def test_bars_join_when_the_same_status_is_under_5_thousandths_apart_an_open_turn_starts_its_own_and_a_queued_turn_adds_no_seconds(self):
        status, turn, window = MOD["Status"], MOD["Turn"], MOD["Window"](0.0, 1000.0)
        turns = (turn(status.DONE, -50.0, 100.0), turn(status.DONE, 104.0, 200.0), turn(status.DONE, 205.0, 300.0),
                 turn(status.STOPPED, 301.0, 310.0), turn(status.RUNNING, 400.0, 500.0), turn(status.QUEUED, 501.0, None))
        spans = MOD["spans_of"](agent(worker(1), turns=turns), window)
        self.assertEqual([(span.x, span.w, span.status.value) for span in spans],
                         [(0, 200, "done"), (205, 95, "done"), (301, 9, "stopped"), (400, 100, "running"), (501, 499, "queued")])
        self.assertEqual(MOD["seconds_of"](agent(worker(1), turns=turns), window), 100 + 96 + 95 + 9 + 100)

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
        fixture.thread(delegated(worker(1), "mail-helper"), title="acct-homemarker@example.test notes", parent=worker(1), turns=(("completed", 70, 60),))
        page = page_of(fixture)
        self.assertEqual((page.groups[0].item.summary, labels(page)), ("Fix sign-in for today", {"D7": [(0, "worker"), (1, "mail helper")]}))

    def test_provider_names_come_from_the_table_and_any_other_driver_reads_other(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), provider="codex", turns=(("completed", 90, 80),))
        for number, provider in enumerate(("acct-homemarker-instance", "acct-homemarker-instance", "grok", "cursor", "opencode", "claudeAgent", "Codex")):
            fixture.thread(native(number), provider=provider, parent=worker(1), delegation=("completed", 70, 60))
        page = page_of(fixture)
        self.assertEqual(page.legend, (("Other", 3), ("Claude", 1), ("Codex", 1), ("Cursor", 1), ("Grok", 1), ("OpenCode", 1)))
        self.assertEqual(sorted({row.provider for row in page.groups[0].rows}), ["Claude", "Codex", "Cursor", "Grok", "OpenCode", "Other"])

    def test_parent_with_no_turn_in_the_window_has_a_row_with_no_bar_and_only_its_child_is_counted(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), provider="codex", turns=(("completed", 900, 890),))
        fixture.thread(delegated(worker(1), "helper-task"), title="helper", parent=worker(1), turns=(("failed", 20, 10),))
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
        fixture.coordinator(turns=(("completed", 900, 890),))
        fixture.unit("D7")
        review = delegated(COORDINATOR, "brigade-kit-d7-verify")
        fixture.thread(review, parent=COORDINATOR, turns=(("completed", 900, 890),))
        fixture.thread(delegated(review, "d7-reader"), title="reader", parent=review, turns=(("completed", 20, 10),))
        page = page_of(fixture)
        self.assertEqual((drawn(page), page.hidden.by_request_name), ({"D7": [(0, "review", 0), (1, "reader", 1)]}, 0))

    def test_ancestor_with_no_turn_in_the_window_has_no_row_in_a_group_that_holds_none_of_its_descendants(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 900, 890),))
        node = fixture.thread(delegated(worker(1), "helper-task"), title="helper", parent=worker(1), turns=(("completed", 20, 10),))
        fixture.unit("D8", task=node)
        page = page_of(fixture)
        self.assertEqual((labels(page), page.totals), ({"D8": [(0, "helper")]}, MOD["Totals"](running=0, agents=0, subagents=1, failed=0)))

    def test_store_with_no_recorded_thread_shows_workers_and_their_children_and_no_coordinator_row(self):
        fixture = self.fixture
        fixture.meta.pop("thread")
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.thread(delegated(worker(1), "how-explorer"), parent=worker(1), turns=(("completed", 70, 60),))
        fixture.thread(delegated(COORDINATOR, "brigade-kit-d7-verify"), parent=COORDINATOR, turns=(("completed", 50, 40),))
        page = page_of(fixture)
        self.assertEqual((labels(page), page.coordinator, page.hidden.other_threads), ({"D7": [(0, "worker"), (1, "how explorer")]}, None, 1))


class LabelTest(unittest.TestCase):
    STORE = store_of(("D7", worker(2), (worker(1),)), ("D8", "", ()))

    def label(self, title="", request="", parent=COORDINATOR, unit="D7"):
        thread = delegated(parent, request) if request else native(1) if parent else worker(9)
        return MOD["label_of"](agent(thread, title=title, parent=parent), self.STORE, unit, MOD["Privacy"]({thread, request, COORDINATOR, worker(1), worker(2)}))

    def test_current_worker_and_earlier_worker(self):
        self.assertEqual(MOD["label_of"](agent(worker(2), title="D7 anything"), self.STORE, "D7", MOD["Privacy"](())), "worker")
        self.assertEqual(MOD["label_of"](agent(worker(1), title="D7 anything"), self.STORE, "D7", MOD["Privacy"](())), "earlier worker")

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

    def test_title_that_is_one_path_under_root_gives_its_last_segment_when_that_is_letters_digits_hyphens_and_underscores(self):
        self.assertEqual(self.label("/root/spec_review", parent=worker(2)), "spec_review")
        self.assertEqual(self.label("/root/a/b/spec-review_2", parent=worker(2)), "spec-review_2")
        for title in ("/root/notes.md", "/pathmarker/notes", "/root", "root/spec_review", "/root/spec_review now", "/root/acct_secretXYZ"):
            with self.subTest(title):
                self.assertEqual(self.label(title, parent=worker(2)), "sub-agent")

    def test_title_that_the_filter_changes_loses_to_the_request_name(self):
        self.assertEqual(self.label("Read the brief at /pathmarker/brief.md", "why-investigator"), "why investigator")
        self.assertEqual(self.label("Fix 0a1b2c3 now", "brigade-kit-d7r2-fix"), "fix")
        self.assertEqual(self.label("Read /pathmarker/brief.md first", parent=worker(2)), "sub-agent")
        self.assertEqual(self.label("Read the brief first", "why-investigator"), "Read the brief first")
        self.assertEqual(self.label(f"Ask {worker(1)} first", "why-investigator"), "why investigator")
        self.assertEqual(self.label("see why-investigator", "why-investigator"), "why investigator")

    def test_request_name_of_one_word_is_removed_from_its_own_label(self):
        self.assertEqual(self.label(request="explorer"), "sub-agent")
        self.assertEqual(self.label("explorer", "explorer"), "sub-agent")
        self.assertEqual(self.label("Act as the review sub-agent for this task.", "explorer"), "review")
        self.assertEqual(self.label(request="how-explorer"), "how explorer")

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

    def test_text_keeps_the_first_line_and_drops_each_word_that_holds_a_slash_a_backslash_a_leading_tilde_a_percent_escape_or_an_id_shape(self):
        text = MOD["Privacy"](()).text
        self.assertEqual(text("Fix the queue\nsecond line"), "Fix the queue")
        self.assertEqual(text("see `/pathmarker/a b` ~/pathmarker (a/b/c) C:\\pathmarker\\x file:///pathmarker src/a.py private/file.txt ~pathmarker and/or"), "see b`")
        self.assertEqual(text("at 0a1b2c3 and 123e4567-e89b-12d3-a456-426614174000 and 123456 and id=mcp%3Ax and 50%2f and acct_secretXYZ and ses_edd6ae1"), "at and and and and and and")
        self.assertEqual(text("defaced facade 12345 thread: one 100% spec_review MAX_BYTES t3_thread_send ~ a_b2c3d4 a_b2c3 a_bcdefg"), "defaced facade 12345 thread: one 100% spec_review MAX_BYTES t3_thread_send a_b2c3 a_bcdefg")
        self.assertEqual(text("link https://example.test/o/r/pull/7 gone"), "link gone")
        self.assertEqual(text(""), "")

    def test_text_drops_a_word_that_holds_a_colon_or_an_at_sign_between_two_characters(self):
        text = MOD["Privacy"](()).text
        self.assertEqual(text("mail acct-homemarker@example.test and <acct-homemarker@example.test> a@b user@host now"), "mail and now")
        self.assertEqual(text("mcp:x node:y task:alpha-secret prefix=mcp:alpha-secret 12:30 D7:fix"), "")
        self.assertEqual(text("@handle @example.test D7: fix: :x x: stay"), "@handle @example.test D7: fix: :x x: stay")

    def test_text_removes_each_value_of_4_or_more_characters_it_was_given_wherever_it_is_and_in_its_percent_decoded_forms(self):
        child = delegated(COORDINATOR, "kit-d7-audit")
        text = MOD["Privacy"]({child, "kit-d7-audit", "abc", "two words", "acct"}).text
        self.assertEqual(text(f"x{child}y and pre{unquote(child)}post"), "x y and pre post")
        self.assertEqual(text("the kit-d7-audit-2 and xkit-d7-auditx and two words and abc acct1 KIT-D7-AUDIT"), "the -2 and x x and and abc 1 KIT-D7-AUDIT")
        self.assertEqual(text(quote(child, safe="") + " " + child.replace("%3A", "%3a") + " end"), "end")

    def test_text_removes_a_value_that_dropping_a_word_or_joining_spaces_would_leave(self):
        text = MOD["Privacy"]({"two words", "kit-d7-audit"}).text
        self.assertEqual(text("say two   words and two a/b words and kit-d7-kit-d7-audit-audit"), "say and and kit-d7- -audit")

    def test_text_removes_the_longest_value_that_starts_at_a_place(self):
        text = MOD["Privacy"]({worker(1), worker(10)}).text
        self.assertEqual(text(f"a {worker(10)} b {worker(1)} c {worker(1)}7"), "a b c 7")

    def test_model_is_the_name_after_one_provider_prefix_when_it_is_lower_case_letters_digits_dots_and_hyphens_and_any_other_reads_other_model(self):
        model = MOD["Privacy"]({"model-secret", worker(1)}).model
        kept = ("model-a", "gpt-6.1-sol", "claude-sonnet-4-5-20250929", "default", "k3", "a" * 48)
        self.assertEqual([model(name) for name in kept], list(kept))
        self.assertEqual((model("opencode/muse-spark-1.3-free"), model("claudeAgent/model-b")), ("muse-spark-1.3-free", "model-b"))
        other = (*REVIEW_MODELS, *REVIEW_TEXTS, "file:/home/private/secret", "vendor/model-b", "opencode/sub/model-b", "Model-A", "model_a", "model a", "model-a\n", " model-a", "",
                 "3-model", "model-", "model..a", "a" * 49, "model-0a1b2c3", "model-123456", "model-deadbeef01", "model-secret", "opencode/model-secret", "x-model-secret-y", worker(1),
                 "model-20250929-1234567", "модель", "model-é")
        self.assertEqual([name for name in other if model(name) != "other model"], [])

    def test_link_is_the_https_pull_request_address_without_userinfo_query_or_fragment_a_blank_value_gives_neither_and_any_other_value_is_refused(self):
        link = MOD["Privacy"]({"kit-d7-audit", worker(1)}).link
        address = "https://git.example/Owner.x/re_po-1/pull/123456"
        for value in (address, "https://user:pw@git.example/Owner.x/re_po-1/pull/123456", address + "?x=mcp:alpha-secret", address + "#task:alpha-secret",
                      "HTTPS://GIT.EXAMPLE/Owner.x/re_po-1/pull/123456", "https://git.example/Owner.x/re_po-1/pull/123456?"):
            with self.subTest(value):
                self.assertEqual(link(value), (address, ""))
        self.assertEqual((link(""), link("  ")), (("", ""), ("", "")))
        refused = ("http://git.example/o/r/pull/7", "https://git.example:8443/o/r/pull/7", "https://git.example/o/r/pull/7/files", "https://git.example/o/r/pull/7/", "https://git.example/o/r/pull/",
                   "https://git.example/o/pull/7", "https://git.example/a/o/r/pull/7", "https://git.example/o/r/pulls/7", "https://git.example/o/r/pull/7 x", "https://git.example/o/r/pull/x7",
                   "https:///o/r/pull/7", "https://git_example/o/r/pull/7", "https://[::1]/o/r/pull/7", "https://[x/o/r/pull/7", "https://git.example:port/o/r/pull/7", "git.example/o/r/pull/7",
                   "file:/home/private/secret", "private/file.txt", "https://git.example/kit-d7-audit/r/pull/7", "https://git.example/o/0a1b2c3/pull/7", "https://123456.example/o/r/pull/7",
                   "https://git.example/o/acct_secretXYZ/pull/7", "https://git.example/o%2Fr/r/pull/7", "https://git.example/../r/pull/7", "https://git.example/o/./pull/7", "https://git.example/o/r/pull/7\\x", f"https://git.example/{worker(1)}/r/pull/7")
        self.assertEqual([value for value in refused if link(value) != ("", "refused")], [])


def populate(fixture, units, agents, running=0, failed=0, heavy=False, in_flight=None, failed_every=0):
    fixture.coordinator(turns=(("completed", 170, 160), ("running", 5, None)))
    states = ("in-progress", "in-review", "passed", "queued", "sent-back", "blocked")
    wide = "𝔸𝔹ℂ𝔻 " * 10 if heavy else ""
    model = "vendor/model-" + "𝕏" * 30 if heavy else "model-a"
    settled = 0
    for index in range(units):
        number, live = index + 1, index >= units - (max(1, units // 4) if in_flight is None else in_flight)
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
            elif not live and failed_every:
                settled += 1
                status = "completed" if settled % failed_every else "failed"
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


def item(ident, in_flight=True, summary="summary", pr="", no_link=""):
    return MOD["Item"](ident, summary, "working" if in_flight else "merged", "go" if in_flight else "", pr, in_flight, no_link)


def group(of, *rows):
    return MOD["Group"](of, tuple(rows), len(rows), 0)


def page(*groups, items=(), coordinator=None, hidden=None, name="kit"):
    rows = [each for one in groups for each in one.rows]
    totals = MOD["Totals"](sum(each.open_seconds is not None for each in rows), sum(each.stands_for for each in rows), 0, 0)
    return MOD["Page"](name, MOD["Window"](0.0, 10800.0), totals, (("Claude", totals.agents),), coordinator, tuple(groups), tuple(items), hidden or MOD["Hidden"]())


def drawn(folded):
    return {one.item.id if one.item else None: [(each.depth, each.label, each.stands_for) for each in one.rows] for one in folded.groups}


class FoldTest(ActivityCase):
    def family(self, number, state, children, last=("completed", "completed")):
        fixture = self.fixture
        fixture.unit(f"D{number}", state, thread=worker(number))
        start = 170 - number * 10
        fixture.thread(worker(number), turns=(("completed", start, start - 2),))
        for position in range(children):
            turn, delegation = last if position == children - 1 else ("completed", "completed")
            end = start - 3.5 - position
            fixture.thread(delegated(worker(number), f"part-{position}"), title=f"part {position}", parent=worker(number),
                           turns=((turn, start - 3 - position, end),), delegation=(delegation, start - 3 - position, None if delegation == "running" else end))

    def test_fold_finished_items_makes_one_row_of_a_merged_or_dropped_unit_with_2_or_more_agents_and_none_running_queued_or_waiting(self):
        self.family(1, "merged", 3, last=("cancelled", "cancelled"))
        self.family(2, "merged", 2, last=("failed", "failed"))
        self.family(3, "merged", 2, last=("completed", "running"))
        self.family(4, "in-review", 2)
        self.family(5, "merged", 0)
        self.family(6, "dropped", 1)
        self.family(7, "merged", 0)
        self.fixture.thread(delegated(worker(7), "part-0"), title="part 0", parent=worker(7), turns=(("running", 5, None),))
        self.family(8, "dropped", 0)
        self.fixture.thread(delegated(worker(8), "part-0"), title="part 0", parent=worker(8), turns=(("queued", 4, None),))
        unfolded = page_of(self.fixture)
        folded = MOD["fold_finished_items"](unfolded)
        self.assertEqual(drawn(folded), {
            "D7": [(0, "worker", 1), (1, "part 0", 1)],
            "D8": [(0, "worker", 1), (1, "part 0", 1)],
            "D6": [(0, "1 agent, 1 sub-agent", 2)],
            "D5": [(0, "worker", 1)],
            "D4": [(0, "worker", 1), (1, "part 0", 1), (1, "part 1", 1)],
            "D3": [(0, "worker", 1), (1, "part 0", 1), (1, "part 1", 1)],
            "D2": [(0, "1 agent, 2 sub-agents", 3)],
            "D1": [(0, "1 agent, 3 sub-agents", 4)]})
        self.assertEqual(folded.groups[7].item, MOD["Item"]("D1", "", "merged", "", "", False))
        summary = folded.groups[6].rows[0]
        self.assertEqual((summary.status.value, summary.seconds, summary.open_seconds, summary.model, summary.provider), ("done", 180, None, "model-a", "Claude"))
        self.assertEqual([(span.x, span.w, span.status.value) for span in summary.spans], [(167, 11, "done"), (183, 3, "done"), (189, 3, "failed")])
        self.assertEqual((folded.fold, folded.totals, folded.legend), (1, unfolded.totals, unfolded.legend))
        self.assertEqual(MOD["notes"](folded), ["3 merged or dropped work items with no agent running, queued, or waiting are each shown as one row."])

    def test_summary_row_has_the_union_of_the_bars_that_are_not_failed_as_done_and_then_the_union_of_the_failed_bars(self):
        rows = [row("a", spans=((0, 10, "done"), (40, 10, "failed"), (100, 10, "stopped"))), row("b", spans=((5, 10, "unknown"), (45, 10, "failed"), (300, 5, "failed"))),
                row("c", spans=((104, 20, "done"),), model="model-b")]
        summary = MOD["summary"](rows, 1, "3 sub-agents")
        self.assertEqual([(span.x, span.w, span.status.value) for span in summary.spans],
                         [(0, 15, "done"), (100, 24, "done"), (40, 15, "failed"), (300, 5, "failed")])
        self.assertEqual((summary.depth, summary.label, summary.model, summary.provider, summary.seconds, summary.stands_for), (1, "3 sub-agents", "", "Claude", 180, 3))

    def test_fold_quiet_subagents_makes_one_row_of_the_done_and_stopped_rows_below_a_top_row_and_keeps_the_other_rows_before_it(self):
        busy_rows = [row("worker", status="failed"), row("a", depth=1), row("b", depth=1, status="running", open_seconds=5), row("b1", depth=2),
                     row("c", depth=1, status="stopped"), row("c1", depth=2, status="waiting"), row("d", depth=1, status="failed"), row("e", depth=1, status="unknown"),
                     row("f", depth=1, status="queued", open_seconds=1), row("lone"), row("g", depth=1),
                     row("second", status="running", open_seconds=9), row("h", depth=1), row("i", depth=2, spans=((20, 5, "failed"), (30, 5, "done")))]
        parent_rows = [row("old", stands_for=0), row("j", depth=1, stands_for=0), row("k", depth=2), row("third"), row("l", depth=1, stands_for=0), row("m", depth=2), row("n", depth=2)]
        folded = MOD["fold_quiet_subagents"](page(group(item("D1"), *busy_rows), group(item("D2", in_flight=False), *parent_rows), group(None, row("o"), row("p", depth=1), row("q", depth=1))))
        self.assertEqual(drawn(folded), {
            "D1": [(0, "worker", 1), (1, "b", 1), (1, "c1", 1), (1, "d", 1), (1, "e", 1), (1, "f", 1), (1, "3 sub-agents", 3),
                   (0, "lone", 1), (1, "g", 1), (0, "second", 1), (1, "2 sub-agents", 2)],
            "D2": [(0, "old", 0), (1, "j", 0), (2, "k", 1), (0, "third", 1), (1, "2 sub-agents", 2)],
            None: [(0, "o", 1), (1, "2 sub-agents", 2)]})
        self.assertEqual([(span.x, span.w, span.status.value) for span in folded.groups[0].rows[-1].spans], [(0, 10, "done"), (30, 5, "done"), (20, 5, "failed")])
        self.assertEqual(folded.fold, 1)
        self.assertEqual(MOD["notes"](folded), ["9 sub-agents that are done or stopped are shown as 4 summary rows."])
        self.assertEqual(drawn(MOD["fold_quiet_subagents"](folded)), drawn(folded))

    def test_keep_finished_items_keeps_the_first_merged_or_dropped_units_with_no_row_running_queued_or_waiting_and_counts_the_rest(self):
        done = [group(item(f"D{number}", in_flight=False), row("3 agents", stands_for=3)) for number in range(1, 5)]
        failed = group(item("D5", in_flight=False), row("worker", status="failed"), row("helper", depth=1, status="unknown"))
        waiting = group(item("D6", in_flight=False), row("worker"), row("helper", depth=1, status="waiting"))
        queued = group(item("D8", in_flight=False), row("worker"), row("helper", depth=1, status="queued", open_seconds=1))
        running = group(item("D9", in_flight=False), row("worker", status="running", open_seconds=1))
        unfolded = page(group(item("D7"), row("worker")), done[0], waiting, done[1], failed, done[2], done[3], queued, running, group(None, row("stray")))
        folded = MOD["keep_finished_items"](3, unfolded)
        self.assertEqual(list(drawn(folded)), ["D7", "D1", "D6", "D2", "D5", "D8", "D9", None])
        self.assertEqual((folded.hidden.dropped_items, folded.hidden.dropped_agents, folded.fold), (2, 6, 1))
        self.assertEqual(MOD["notes"](folded), [
            "2 merged or dropped work items with no agent running, queued, or waiting are each shown as one row.",
            "2 merged or dropped work items with 6 agents are not shown. Use a larger --max-bytes to see more."])
        again = MOD["keep_finished_items"](1, folded)
        self.assertEqual(list(drawn(again)), ["D7", "D1", "D6", "D8", "D9", None])
        self.assertEqual((again.hidden.dropped_items, again.hidden.dropped_agents, again.fold), (4, 11, 2))

    def test_folds_3_to_10_keep_24_16_12_8_6_4_2_and_0_finished_units_and_count_each_dropped_unit_once(self):
        folded = page(group(item("D99"), row("worker")), *[group(item(f"D{number}", in_flight=False), row("2 agents", stands_for=2)) for number in range(30)])
        left = []
        for fold in MOD["FOLDS"][2:-1]:
            folded = fold(folded)
            left.append((len(folded.groups) - 1, folded.hidden.dropped_items, folded.hidden.dropped_agents))
        self.assertEqual(left, [(24, 6, 12), (16, 14, 28), (12, 18, 36), (8, 22, 44), (6, 24, 48), (4, 26, 52), (2, 28, 56), (0, 30, 60)])
        self.assertEqual((folded.fold, len(MOD["FOLDS"])), (8, 11))

    def test_cap_everything_keeps_6_groups_with_those_that_have_a_running_row_first(self):
        groups = [group(item(f"D{number}"), row("worker"), row("helper", depth=1, stands_for=3)) for number in range(1, 9)]
        groups.append(group(None, row("stray", status="running", open_seconds=5)))
        folded = MOD["cap_everything"](page(*groups, items=[item("D1")]))
        self.assertEqual(list(drawn(folded)), ["D1", "D2", "D3", "D4", "D5", None])
        self.assertEqual((folded.hidden.cut_agents, folded.fold), (12, 1))
        self.assertEqual(MOD["notes"](folded), [
            "15 sub-agents that are done or stopped are shown as 5 summary rows.",
            "12 more agents are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."])

    def test_cap_everything_counts_a_cut_group_of_a_merged_item_as_an_item_not_shown_and_a_cut_group_of_an_item_in_flight_by_its_agents(self):
        groups = [group(item(f"D{number}"), row("worker", status="running", open_seconds=5)) for number in range(1, 7)]
        groups.append(group(item("D7", in_flight=False), row("worker"), row("helper", depth=1, status="waiting", stands_for=2)))
        groups.append(group(item("D8"), row("worker")))
        groups.append(group(None, row("stray")))
        folded = MOD["cap_everything"](page(*groups, items=[one.item for one in groups if one.item and one.item.in_flight]))
        self.assertEqual(list(drawn(folded)), ["D1", "D2", "D3", "D4", "D5", "D6"])
        hidden = folded.hidden
        self.assertEqual((hidden.dropped_items, hidden.dropped_agents, hidden.cut_agents, hidden.cut_in_flight, [each.id for each in folded.items]),
                         (1, 3, 2, 0, ["D1", "D2", "D3", "D4", "D5", "D6", "D8"]))
        self.assertEqual(MOD["notes"](folded), [
            "1 merged or dropped work item with 3 agents is not shown. Use a larger --max-bytes to see more.",
            "2 more agents are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."])

    def test_cap_everything_keeps_6_rows_a_group_with_running_and_failed_rows_first_and_moves_a_row_up_when_its_parent_is_cut(self):
        rows = [row("a"), row("b", depth=1), row("c", depth=2, status="running", open_seconds=9), row("d", depth=2)]
        rows += [row(label, status="failed") for label in "efghi"] + [row("j")]
        folded = MOD["cap_everything"](page(group(item("D1"), *rows)))
        self.assertEqual(drawn(folded), {"D1": [(0, "c", 1), (0, "e", 1), (0, "f", 1), (0, "g", 1), (0, "h", 1), (0, "i", 1)]})
        self.assertEqual(folded.hidden.cut_agents, 4)
        kept = MOD["cap_everything"](page(group(item("D1"), row("a"), row("b", depth=1), row("c", depth=2, status="running", open_seconds=9))))
        self.assertEqual(drawn(kept), {"D1": [(0, "a", 1), (1, "b", 1), (2, "c", 1)]})

    def test_cap_everything_joins_the_nearest_bars_until_4_are_left_on_a_row_and_8_on_the_coordinators(self):
        spans = ((0, 10, "done"), (12, 10, "failed"), (100, 10, "done"), (115, 10, "stopped"), (300, 10, "done"), (500, 10, "running"), (700, 10, "done"))
        folded = MOD["cap_everything"](page(group(item("D1"), row("a", spans=spans)), coordinator=row("coordinator", spans=spans + ((900, 5, "done"), (906, 5, "done")))))
        self.assertEqual([(span.x, span.w, span.status.value) for span in folded.groups[0].rows[0].spans],
                         [(300, 10, "done"), (500, 10, "running"), (700, 10, "done"), (0, 125, "failed")])
        self.assertEqual([(span.x, span.w, span.status.value) for span in folded.coordinator.spans],
                         [(0, 10, "done"), (100, 10, "done"), (115, 10, "stopped"), (300, 10, "done"), (500, 10, "running"), (700, 10, "done"), (900, 11, "done"), (12, 10, "failed")])

    def test_joined_makes_one_bar_of_two_across_a_gap_of_99_and_the_footer_says_bars_may_join_across_gaps(self):
        spans = MOD["joined"]([MOD["Span"](x, 1, MOD["Status"].DONE) for x in (0, 100, 300, 600, 900)], 4)
        self.assertEqual([(span.x, span.w) for span in spans], [(0, 101), (300, 1), (600, 1), (900, 1)])
        self.assertIn("'. Bars show turn or delegation intervals and may join across gaps. Striped bars include running turns. Outlined bars include queued turns. Faded bars include stopped turns.'", MOD["RENDERER"])
        self.assertNotIn("time an agent was at work", MOD["RENDERER"])

    def test_cap_everything_lists_8_in_flight_items_with_those_of_a_group_on_the_page_first(self):
        items = [item(f"D{number}") for number in range(1, 13)]
        folded = MOD["cap_everything"](page(group(items[10], row("worker")), items=items))
        self.assertEqual([each.id for each in folded.items], ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D11"])
        self.assertEqual(folded.hidden.cut_in_flight, 4)
        self.assertEqual(MOD["notes"](folded), ["4 more work items in flight are not listed."])

    def test_cap_everything_clips_strings_by_the_bytes_of_their_entity_form_and_drops_a_link_whose_entity_form_is_over_90_bytes(self):
        long = item("D" + "1" * 20, summary="<" * 30, pr="https://example.test/" + "x" * 70)
        short = item("D2", summary="𝔸" * 30, pr="https://example.test/" + "x" * 69)
        quoted = item("D3", summary='"' * 30, pr="https://example.test/?" + "&" * 14)
        plain = item("D4", summary="s" * 37, pr="https://example.test/?" + "&" * 13)
        groups = [group(long, row("é" * 30, model="m" * 40)), group(short, row("\\" * 30)), group(quoted, row("worker")), group(plain, row("worker"))]
        folded = MOD["cap_everything"](page(*groups, items=[long, short, quoted, plain], name="𝔸" * 20))
        first, second, third, fourth = folded.groups
        self.assertEqual((first.item.id, first.item.summary, first.item.pr), ("D" + "1" * 8 + "…", "<" * 8 + "…", ""))
        self.assertEqual((second.item.summary, second.item.pr, second.rows[0].label), ("𝔸" * 8 + "…", "https://example.test/" + "x" * 69, "\\" * 3 + "…"))
        self.assertEqual((third.item.summary, third.item.pr), ('"' * 5 + "…", ""))
        self.assertEqual((fourth.item.summary, fourth.item.pr), ("s" * 33 + "…", "https://example.test/?" + "&" * 13))
        self.assertEqual((first.rows[0].label, first.rows[0].model, folded.name), ("é" * 8 + "…", "m" * 27 + "…", "𝔸" * 8 + "…"))
        self.assertEqual(folded.items, (first.item, second.item, third.item, fourth.item))
        self.assertEqual([each.no_link for each in folded.items], ["cut", "", "cut", ""])
        self.assertEqual(MOD["notes"](folded), ["2 pull request links are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."])

    def test_every_fold_on_the_busy_fixture_and_on_one_with_failed_agents_in_merged_units_keeps_the_agent_count_the_totals_and_the_legend(self):
        for build, totals, in_flight in ((busy, MOD["Totals"](5, 36, 364, 3), 8), (day, MOD["Totals"](8, 36, 364, 32), 6)):
            fixture = self.fixture = Fixture(self.fixture.root / build.__name__)
            unfolded = folded = page_of(build(fixture))
            self.assertEqual((unfolded.totals, sum(each.stands_for for one in unfolded.groups for each in one.rows)), (totals, 400))
            shown = []
            for index, fold in enumerate(MOD["FOLDS"]):
                folded = fold(folded)
                rows = [each for one in folded.groups for each in one.rows]
                shown.append(len(rows))
                with self.subTest(build.__name__, fold=index):
                    self.assertEqual(sum(each.stands_for for each in rows) + folded.hidden.dropped_agents + folded.hidden.cut_agents, 400)
                    self.assertEqual([each.id for each in MOD["items_of"](folded) if not each.pr and not each.no_link], [])
                    self.assertEqual((folded.totals, folded.legend, folded.fold), (unfolded.totals, unfolded.legend, index + 1))
            self.assertEqual(shown, sorted(shown, reverse=True))
            self.assertEqual((len(folded.groups), len(folded.items), shown[-1] < shown[0]), (6, in_flight, True))

    def test_every_fold_of_a_page_whose_items_all_have_a_link_leaves_each_item_on_the_page_with_its_link_or_counted_in_a_note(self):
        fixture = busy(self.fixture)
        for number, unit in enumerate(fixture.units, start=1):
            unit["pr"] = ("https://example.test/" + "o" * 60 + f"/r/pull/{number}", f"https://example.test/o/r/pull/{number}", f"http://example.test/o/r/pull/{number}")[number % 3]
        folded = page_of(fixture)
        for index, fold in enumerate(MOD["FOLDS"]):
            folded = fold(folded)
            on_page = MOD["items_of"](folded)
            data = data_of(MOD["render_html"](folded))
            counted = sum(int(note.split()[0]) for note in data["N"] if "pull request link" in note)
            with self.subTest(fold=index):
                self.assertEqual((len(data["I"]), sum(bool(each[4]) for each in data["I"]) + counted), (len(on_page), len(on_page)))
        self.assertEqual([note for note in MOD["notes"](folded) if "pull request link" in note], [
            "3 pull request links are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            "3 pull request links are not shown. Each is not an https://host/owner/repository/pull/number address, or it holds text this page removes."])

    def test_notes_say_no_agent_ran_for_an_empty_window_and_count_unknown_statuses_requests_and_other_threads(self):
        self.assertEqual(MOD["notes"](page()), ["No agent or sub-agent of this coordinator ran in this window."])
        hidden = MOD["Hidden"](dropped_items=1, dropped_agents=1, cut_agents=1, cut_in_flight=1, other_threads=1, unknown_status=1, by_request_name=1, unstarted=1)
        linked = page(group(item("D1", no_link="cut"), row("worker")), items=[item("D2", no_link="refused"), item("D3", pr=PR7)], hidden=hidden)
        self.assertEqual(MOD["notes"](linked), [
            "1 merged or dropped work item with 1 agent is not shown. Use a larger --max-bytes to see more.",
            "1 more agent is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            "1 more work item in flight is not listed.",
            "1 pull request link is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            "1 pull request link is not shown. It is not an https://host/owner/repository/pull/number address, or it holds text this page removes.",
            "T3 lists 1 sub-agent with no thread or no start time under a thread this page read. It is not shown.",
            "T3 gave 1 status this tool reads as unknown.",
            "1 agent is grouped by the name of the request that started it.",
            "1 other thread ran in T3 outside this coordinator."])
        several = MOD["Hidden"](other_threads=3, unknown_status=2, by_request_name=9)
        self.assertEqual(MOD["notes"](page(group(item("D1"), row("worker")), hidden=several)), [
            "T3 gave 2 statuses this tool reads as unknown.",
            "9 agents are grouped by the name of the request that started them.",
            "3 other threads ran in T3 outside this coordinator."])


DATA = re.compile(r"<script type=application/json id=d>(.*?)</script>", re.S)
PROGRAM = re.compile(r"<script>(const H=(\d+);.*)</script>$", re.S)
STORE_WORDS = re.compile(r"\b(dish|dishes|rail|86|pass|station|restaurant|fire|chef)\b", re.I)
PR7 = "https://example.test/o/r/pull/7"
PR6 = "https://example.test/o/r/pull/6"
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


HOSTILE = "x</script><img onerror=a(1)> \"q\" \\ & 'p'\t&lt;\u2028z"
HOSTILE_WRITTEN = "x&lt;/script&gt;&lt;img onerror=a(1)&gt; &quot;q&quot; &#92; &amp; 'p' &amp;lt; z"
HOSTILE_DRAWN = "x</script><img onerror=a(1)> \"q\" \\ & 'p' &lt; z"


def hostile():
    return page(group(item("D1 " + HOSTILE, summary=HOSTILE), row(HOSTILE, model="model " + HOSTILE)), name=HOSTILE)


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
                   provider="grok", model="grok/model-c", parent=COORDINATOR, turns=(("failed", 30.5, 27),))
    fixture.thread(worker(5), title="D5 worker", turns=(("completed", 170, 165),))
    fixture.thread(delegated(COORDINATOR, "why-investigator"), title="Read the brief at /pathmarker/brief.md", parent=COORDINATOR, turns=(("interrupted", 90, 88),))
    fixture.thread("mcp:threadmarker-other-project", turns=(("completed", 20, 10),))
    return fixture


def typical(fixture):
    return populate(fixture, units=9, agents=50, running=3, failed=1)


def busy(fixture):
    return populate(fixture, units=36, agents=400, running=5, failed=3)


def day(fixture):
    return populate(fixture, units=36, agents=400, running=8, failed=2, in_flight=6, failed_every=11)


def extreme(fixture):
    return populate(fixture, units=200, agents=3000, running=60, failed=10, heavy=True)


def declarations(style):
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", style.replace("@media(max-width:520px){", ""))
    return [(selector, *declaration.split(":", 1)) for selectors, body in rules for selector in selectors.split(",") for declaration in body.split(";")]


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
        self.assertEqual([line[5:6] + line[8:] for line in data["G"][0][1][:2]], [["63m", "42m"], ["6m", "6m"]])
        self.assertEqual([line[:2] + line[6][2:] + line[7:] for line in data["G"][2][1]], [[0, "why investigator", 3]])
        self.assertEqual([[bars[2::3] for bars in (line[6] for line in lines)] for _, lines in data["G"]], [[[0, 1], [1], [0], [2]], [[0]], [[3]]])
        self.assertEqual((data["k"][:3], data["k"][3][2::3]), ([0, 0, "15m"], [0, 0]))
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

    def test_markup_a_quote_and_an_escape_character_in_the_store_name_a_summary_and_a_title_leave_no_backslash_or_angle_bracket_in_the_data_element(self):
        fixture = one_running(self.fixture)
        text, written = "x<script><img onerror=a(1)> \"q\" & p\x1b", "x&lt;script&gt;&lt;img onerror=a(1)&gt; &quot;q&quot; &amp; p "
        fixture.meta["restaurant"] = text
        fixture.units[0]["summary"] = text
        fixture.thread(delegated(worker(1), "helper-task"), title=text, model="<b>\\\"model\x1b", parent=worker(1), turns=(("completed", 20, 10),))
        fixture.write()
        raw = DATA.search(self.document()).group(1)
        self.assertEqual([character for character in "\\<>" if character in raw], [])
        data = json.loads(raw)
        self.assertEqual((data["c"], data["I"][1][1], data["G"][0][1][1][1], data["M"]),
                         (written, written, written, ["model-a", "other model"]))

    def test_data_element_of_a_page_whose_strings_hold_markup_a_quote_a_backslash_a_tab_and_u2028_holds_no_backslash_or_angle_bracket(self):
        raw = DATA.search(MOD["render_html"](hostile())).group(1)
        self.assertEqual([character for character in "\\<>" if character in raw], [])
        data = json.loads(raw)
        self.assertEqual((data["c"], data["I"], data["G"][0][1][0][1], data["M"]),
                         (HOSTILE_WRITTEN, [["D1 " + HOSTILE_WRITTEN, HOSTILE_WRITTEN, "working", "go", "", 0]], HOSTILE_WRITTEN, ["model " + HOSTILE_WRITTEN]))

    def test_entities_writes_five_characters_as_entities_and_each_control_character_u2028_and_u2029_as_one_space(self):
        self.assertEqual(MOD["entities"]("&<>\"\\'"), "&amp;&lt;&gt;&quot;&#92;'")
        self.assertEqual(MOD["entities"]("&amp;&#92;"), "&amp;amp;&amp;#92;")
        controls = "".join(map(chr, [*range(0x20), *range(0x7F, 0xA0), 0x2028, 0x2029]))
        self.assertEqual(MOD["entities"](controls), " " * 67)
        self.assertEqual(MOD["entities"](" ~\xa0\u2027\u202a"), " ~\xa0\u2027\u202a")

    def test_encode_writes_each_string_value_as_entities_writes_it_and_leaves_numbers_null_and_keys(self):
        self.assertEqual(MOD["encode"]({"<": ["</script>\t", 1, None, {"b": "\"\\"}]}), '{"<":["&lt;/script&gt; ",1,null,{"b":"&quot;&#92;"}]}')

    def test_encode_writes_a_string_in_a_tuple_as_entities_writes_it(self):
        self.assertEqual(MOD["encode"]({"a": ("<",)}), '{"a":["&lt;"]}')

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

    def test_stylesheet_has_no_rule_for_html_body_root_or_every_element_sets_only_font_and_color_on_the_outermost_one_and_gives_the_threads_background_only_to_a_header_row(self):
        declared = declarations(MOD["STYLE"])
        whole_page = re.compile(r"(html|body|:root)(?![\w-])|\*")
        self.assertEqual([selector for selector in ("html", "body>div", ":root", "*", "#o", ".html", "bodyguard") if whole_page.match(selector)], ["html", "body>div", ":root", "*"])
        self.assertEqual([selector for selector, _, _ in declared if whole_page.match(selector)], [])
        self.assertEqual([(name, value) for selector, name, value in declared if selector == "#o"], [("font", "13px/1.4 var(--font-sans)"), ("color", "var(--foreground)")])
        self.assertEqual([selector for selector, _, value in declared if "var(--background)" in value], [".grp"])

    def test_narrow_rule_puts_each_rows_model_on_a_second_line_of_a_30_pixel_row_and_hides_only_every_other_axis_label(self):
        narrow = dict(re.findall(r"([^{}]+)\{([^{}]*)\}", re.search(r"@media\(max-width:520px\)\{(.*)\}$", MOD["STYLE"]).group(1)))
        self.assertEqual([selector for selector, body in narrow.items() if "display:none" in body], [".axis i:nth-child(odd)"])
        self.assertEqual(narrow[".row"], "position:relative;height:30px;align-items:start")
        self.assertEqual(narrow[".l small"], "position:absolute;left:0;right:0;bottom:0;overflow:hidden;text-overflow:ellipsis;line-height:13px")
        self.assertEqual((narrow[".d1 small"], narrow[".d2 small"]), ("left:14px", "left:28px"))
        wide = MOD["STYLE"].split("@media")[0]
        self.assertEqual(re.findall(r"[^{}]*small[^{}]*\{[^{}]*\}", wide), ["h2,small,.stat span,.legend,.axis,.sum,.foot,.co,.chip{color:var(--muted-foreground)}", "small{font-size:10.5px}"])
        self.assertEqual(re.findall(r"height:[^;}]*", MOD["STYLE"]), ["height:1.1", "height:8px", "height:9px", "height:16px", "height:19px", "height:100%", "height:11px", "height:30px", "height:13px"])

    def test_stylesheet_names_a_color_in_a_color_background_border_or_c_declaration_only_as_a_variable_transparent_or_inherit_and_reads_only_theme_variables_and_its_own_two(self):
        style = MOD["STYLE"]
        declared = declarations(style)
        self.assertEqual(re.findall(r"[0-9](?:vh|vw)\b|#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", style), [])

        def other_words(value):
            plain = r"var\(--[\w-]+\)|[0-9.]+(px|%|deg)?|solid|transparent|inherit|repeating-linear-gradient"
            return [word for word in re.findall(r"var\([^)]*\)|[\w.%-]+", value) if not re.fullmatch(plain, word)]

        self.assertEqual(other_words("1px solid red"), ["red"])
        self.assertEqual(other_words("repeating-linear-gradient(135deg,var(--c) 0 4px,canvas 4px 7px)"), ["canvas"])
        colored = [(name, value) for _, name, value in declared if name in ("color", "--c") or name.startswith(("background", "border"))]
        self.assertEqual([(name, value) for name, value in colored if other_words(value)], [])
        allowed = {"--foreground", "--muted-foreground", "--background", "--border", "--success", "--destructive", "--warning", "--info", "--radius", "--font-sans", "--font-mono"}
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
        self.assertLess(len(document.encode()), 16000)
        self.assertEqual((data["n"], agents_shown(data), sum(len(lines) for _, lines in data["G"])), ([3, 9, 41, 1], 50, 50))
        self.assertEqual(data["N"], ["14 agents are grouped by the name of the request that started them."])

    def test_busy_window_is_folded_to_at_most_16000_bytes_with_a_note_and_max_bytes_buys_every_row_back(self):
        busy(self.fixture).write()
        document = self.document()
        data = data_of(document)
        self.assertLess(len(document.encode()), 16000)
        self.assertEqual(data["n"], [5, 36, 364, 3])
        self.assertLess(sum(len(lines) for _, lines in data["G"]), 400)
        self.assertIn("27 merged or dropped work items with no agent running, queued, or waiting are each shown as one row.", data["N"])
        whole = data_of(self.document("--max-bytes", "500000"))
        self.assertEqual((whole["n"], agents_shown(whole), sum(len(lines) for _, lines in whole["G"])), ([5, 36, 364, 3], 400, 400))
        self.assertEqual(whole["N"], ["112 agents are grouped by the name of the request that started them."])

    def test_window_shaped_like_a_measured_day_is_over_12000_and_at_most_16000_bytes_and_the_last_fold_is_not_applied(self):
        unfolded = page_of(day(self.fixture))
        self.assertEqual((unfolded.totals, len(unfolded.items), sum(each.depth == 0 for one in unfolded.groups for each in one.rows)), (MOD["Totals"](8, 36, 364, 32), 6, 148))
        fitted, document = MOD["fit"](unfolded, 15999)
        self.assertGreater(len(document.encode()), 12000)
        self.assertLess(len(document.encode()), 16000)
        self.assertLess(fitted.fold, len(MOD["FOLDS"]))
        self.assertEqual(agents_shown(data_of(document)) + fitted.hidden.dropped_agents, 400)
        printed = self.document()
        self.assertGreater(len(printed.encode()), 12000)
        self.assertLess(len(printed.encode()), 16000)
        self.assertEqual(sum(len(lines) for _, lines in data_of(printed)["G"]), sum(len(one.rows) for one in fitted.groups))

    def test_extreme_window_with_four_byte_characters_and_long_links_is_at_most_16000_bytes(self):
        extreme(self.fixture).write()
        document = self.document()
        data = data_of(document)
        self.assertLess(len(document.encode()), 16000)
        self.assertEqual((data["n"], len(data["G"]), len([item for item in data["I"] if item[5]])), ([60, 200, 2800, 10], 6, 8))
        self.assertEqual(self.out("--text").split("\n")[1], "60 running now, 200 agents, 2800 sub-agents, 10 failed")
        self.assertLessEqual(len(self.out("--text").rstrip("\n").split("\n")), 40)

    def test_window_of_250_running_agents_on_250_items_in_flight_has_no_summary_row_and_counts_the_agents_and_items_it_leaves_out_as_the_guide_says(self):
        fixture = self.fixture
        fixture.coordinator()
        for number in range(1, 251):
            fixture.unit(f"D{number}", "in-progress", f"item {number}", thread=worker(number))
            fixture.thread(worker(number), title=f"D{number} worker", turns=(("running", 5 + number / 10, None),))
        fixture.write()
        document = self.document()
        data = data_of(document)
        rows = [line for _, lines in data["G"] for line in lines]
        self.assertLess(len(document.encode()), 16000)
        self.assertEqual((data["n"], len(rows), [line[7] for line in rows], len([each for each in data["I"] if each[5]])), ([250, 250, 0, 0], 6, [1] * 6, 8))
        self.assertEqual(data["N"], ["244 more agents are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
                                     "242 more work items in flight are not listed."])
        guide = GUIDE.read_text()
        self.assertIn("When the document exceeds its size limit, the page can fold finished rows into summaries or omit rows, and it counts the agents and work items those changes cover.", guide)
        self.assertNotIn("the page folds finished work into summary rows", guide)

    def test_largest_page_the_last_fold_can_leave_is_under_16000_bytes_with_six_providers_every_note_the_longest_window_label_and_every_clipped_string_over_its_byte_limit(self):
        spans = tuple((x * 40, 1, "failed") for x in range(25))
        counts = ("dropped_items", "dropped_agents", "cut_agents", "cut_in_flight", "other_threads", "unknown_status", "by_request_name", "unstarted")
        hidden = MOD["Hidden"](**dict.fromkeys(counts, 9999999))
        legend = tuple((name, 9999999) for name in ("Claude", "Codex", "Cursor", "Grok", "OpenCode", "Other"))
        sizes = {}
        for status in ("running", "queued"):
            for character in ("s", "<", '"', "𝕏"):
                wide = character * 400

                def big(number, no_link=""):
                    return item(str(number) + wide, summary=str(number) + wide, pr="https://example.test/" + "x" * 69, no_link=no_link)

                def rows(prefix):
                    return [replace(row(prefix + str(n) + wide, depth=min(n, 2), status=status, spans=spans, open_seconds=604800, stands_for=99999, model=str(n) + wide),
                                    seconds=604800) for n in range(40)]

                groups = [group(big(number, "refused" if number % 2 else "cut"), *rows(str(number))) for number in range(40)]
                unfolded = page(*groups, items=[big(number) for number in range(100, 140)], coordinator=row("coordinator", spans=spans, model=wide), hidden=hidden, name=wide)
                totals = MOD["Totals"](9999999, 9999999, 9999999, 9999999)
                largest = MOD["cap_everything"](replace(unfolded, window=MOD["Window"](1790000000.0, 1790604799.0), totals=totals, legend=legend))
                self.assertEqual((len(largest.groups), [len(one.rows) for one in largest.groups], len(largest.items), len(MOD["notes"](largest))), (6, [6] * 6, 8, 11))
                self.assertEqual(MOD["wire"](largest)["w"][2], "167h 59m 59s")
                sizes[status, character] = len(MOD["render_html"](largest).encode())
        self.assertEqual([case for case, size in sizes.items() if size >= 16000], [])


class TextTest(OutputCase):
    def test_text_lists_running_agents_work_items_failed_agents_and_notes(self):
        failed_child(self.fixture).write()
        self.assertEqual(self.out("--text"), "\n".join([
            "Agent activity for kit, last 3h",
            "2 running now, 2 agents, 4 sub-agents, 1 failed",
            "Running now",
            "  D7 worker   model-a   running for 42m",
            "  D7 architect runner 2   model-b   running for 6m   under worker",
            "Work items",
            f"  D7   in review   Agent activity page   1 agent, 3 sub-agents, 74m at work   {PR7}",
            "  D5   merged   Old work   1 agent, 5m at work",
            f"  D6   landing   Queue fix   no activity in this window   {PR6}",
            "  Not tied to a work item   1 sub-agent, 2m at work",
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
        self.assertTrue(target.read_text().startswith("Agent activity for kit, last 3h\n"))

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
        self.assertRegex(texts[-3], r"^As of \d+:\d\d [AP]M for kit\. Bars show turn or delegation intervals and may join across gaps\. Striped bars include running turns\. Outlined bars include queued turns\. Faded bars include stopped turns\.$")
        self.assertEqual(built["links"], [["A", PR7, "_blank", "noopener"], ["A", PR6, "_blank", "noopener"], ["A", PR7, "_blank", "noopener"]])
        classes = built["classes"]
        self.assertEqual([classes.count(name) for name in ("stat live", "stat alarm", "mark pulse", "row co", "row p1", "row p2", "row p3", "grp", "b run", "b f", "b stop")],
                         [1, 1, 2, 1, 3, 2, 1, 3, 2, 1, 1])
        self.assertEqual(len(built["bars"]), sum(len(line[6]) // 3 for _, lines in data_of(document)["G"] for line in lines) + 2)
        self.assertEqual([bar for bar in built["bars"] if not all(re.fullmatch(r"[0-9]+(\.[0-9])?%", side) for side in bar)], [])
        self.assertEqual(built["titles"], ["coordinator · model-c · running · 15m", "worker · model-a · running · 63m", "architect runner 2 · model-b · running · 6m",
                                           "spec_review · model-b · done · 60s", "review · model-c · failed · 3m", "worker · model-a · done · 5m", "why investigator · model-a · stopped · 2m"])

    def test_page_that_render_html_writes_is_drawn_with_markup_a_quote_a_backslash_and_text_that_reads_as_an_entity_as_that_text_and_a_tab_and_u2028_as_one_space(self):
        built = self.render(MOD["render_html"](hostile()))
        self.assertNotIn("This copy differs from what the tool wrote. Run the command again.", built["texts"])
        self.assertEqual([text for text in built["texts"] if "onerror" in text or "&" in text],
                         ["D1 " + HOSTILE_DRAWN, HOSTILE_DRAWN, HOSTILE_DRAWN + " ", "model " + HOSTILE_DRAWN, "As of 3:00 AM for " + HOSTILE_DRAWN
                          + ". Bars show turn or delegation intervals and may join across gaps. Striped bars include running turns. Outlined bars include queued turns. Faded bars include stopped turns."])
        self.assertEqual(built["titles"], [HOSTILE_DRAWN + " · model " + HOSTILE_DRAWN + " · done · 60s"])

    def test_renderer_lists_a_queued_agent_under_queued_with_a_plain_mark_and_an_outlined_bar_and_draws_no_running_list(self):
        fixture = queued_only(self.fixture)
        fixture.thread(native(1), title="/root/spec_review", parent=worker(1), delegation=("queued", 2, None))
        fixture.write()
        built = self.render(self.document(clock=fixture.now))
        texts, classes = built["texts"], built["classes"]
        self.assertEqual([text for text in texts if text in ("Running now", "Queued", "Timeline")], ["Queued", "Timeline"])
        self.assertEqual(texts[texts.index("Queued") + 1:texts.index("Timeline")], ["D7 worker", "model-a", "5m", "D7 spec_review", "model-a · under worker", "2m"])
        self.assertEqual([classes.count(name) for name in ("mark", "mark pulse", "b q", "b run", "stat live")], [2, 0, 2, 0, 1])
        self.assertEqual((texts[0], built["titles"][1:]), ("0", ["worker · model-a · queued · 0s", "spec_review · model-a · queued · 0s"]))

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

def skill_section():
    return SKILL.read_text().split("\n## Agent activity\n", 1)[1].split("\n## ", 1)[0]


class SkillTest(OutputCase):
    @unittest.skipUnless(shutil.which("bash"), "bash is not on PATH")
    def test_skills_command_lines_run_in_bash_with_a_space_in_the_skills_path_the_store_path_and_t3s_base_directory(self):
        fixture = one_running(self.fixture).write()
        skills, tools = fixture.root / "pathmarker skills", fixture.root / "tools"
        (skills / "brigade" / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPT, skills / "brigade" / "scripts" / "activity.py")
        tools.mkdir()
        (tools / "python3").symlink_to(sys.executable)
        block = re.search(r"```bash\n(.*?)```", skill_section(), re.S).group(1)
        define, *calls = block.replace("<skills>", str(skills)).replace("<restaurant dir>", str(fixture.store)).splitlines()
        environment = {"HOME": str(fixture.home), "PATH": f"{tools}{os.pathsep}{os.environ.get('PATH', '')}", "T3CODE_HOME": str(fixture.base)}
        outputs = []
        for call in calls:
            result = subprocess.run(["bash", "-c", f"{define}\n{call}"], capture_output=True, text=True, env=environment, cwd=fixture.root)
            self.assertEqual((result.returncode, result.stderr), (0, ""), call)
            outputs.append(result.stdout)
        self.assertEqual([call.split("#")[0].split() for call in calls], [["A"], ["A", "--hours", "12"], ["A", "--text"]])
        self.assertEqual(([data_of(output)["w"][1] for output in outputs[:2]], outputs[2].split("\n")[0]), ([10800, 43200], "Agent activity for kit, last 3h"))

    def test_skill_sends_the_text_form_when_the_preview_fails_and_tells_the_coordinator_to_skip_no_step_of_visual_reports(self):
        section = skill_section()
        steps = dict(re.findall(r"^([0-9])\. (.*)$", section, re.M))
        self.assertEqual(steps["2"], "Call `html_preview` with that document. When the preview shows a console error or a clipped or overlapping element, "
                                     "send the stdout of `A --text` and name the fault in the reply. Render only a page whose preview passes. "
                                     "Call `html_render` with the document unchanged and the title `Agent activity`, "
                                     "per steps 3 to 4 of [Visual reports](../pstack-runtime/SKILL.md#visual-reports).")
        self.assertEqual([word for word in ("skip", "step 2 of") if word in section], [])


REVIEW_MODELS = ("mcp:alpha-secret", "node:alpha-secret", "run:alpha-secret", "acct_secretXYZ", "user@example.test", "C:\\Users\\private\\file.txt")
REVIEW_TEXTS = ("task:alpha-secret", "acct_secretXYZ", "private/file.txt", "prefix=mcp:alpha-secret", "id=mcp%3Aalpha-secret")
REVIEW_VALUES = tuple(dict.fromkeys((*REVIEW_MODELS, *REVIEW_TEXTS, "file:/home/private/secret")))
NEEDLES = ("secret", "user@", "example.test", "private", "Users", "file.txt")
REQUEST = "kit-d7-audit-task"
INSTANCE = "acct-homemarker-instance"
ADDRESS = "acct-homemarker@example.test"
PLANTED = ("model", "title", "title in a sentence", "summary", "unit id", "provider", "instance id", "branch", "store name",
           "pull request", "pull request path", "pull request query", "pull request fragment", "pull request userinfo")


def tree(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(Path(directory).rglob("*")) if path.is_file()}


def put(rows, index, column, value):
    rows[index] = (*rows[index][:column], value, *rows[index][column + 1:])


class PrivacyTest(OutputCase):
    def staffed(self, name):
        fixture = self.fixture = Fixture(self.root / name)
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Activity page", thread=worker(1), pr="https://git.example/o/r/pull/7")
        fixture.thread(worker(1), title="D7 worker", instance=INSTANCE, turns=(("running", 30, None),))
        self.child = delegated(worker(1), REQUEST)
        self.node = fixture.thread(self.child, title=f"auditor for {ADDRESS}", provider="codex", parent=worker(1), turns=(("completed", 25, 20),))
        return fixture

    def own_values(self, fixture):
        child = self.child
        return (COORDINATOR, worker(1), child, unquote(child), unquote(unquote(child)), quote(child, safe=""), quote(quote(child, safe=""), safe=""),
                child.replace("%3A", "%3a"), self.node, REQUEST, INSTANCE, ADDRESS, str(fixture.store), str(fixture.base), str(fixture.database), str(fixture.home),
                fixture.meta["projectRoot"], quote(str(fixture.store), safe=""), quote(quote(str(fixture.home), safe=""), safe=""))

    def plant(self, fixture, field, number, value):
        part, unit = delegated(worker(1), f"kit-d7-part-{number}"), f"D{100 + number}"
        link = f"https://git.example/o/r/pull/{100 + number}"
        if field in ("model", "title", "title in a sentence", "provider", "instance id"):
            how = {"model": {"model": value}, "title": {"title": value}, "title in a sentence": {"title": f"see {value} now"},
                   "provider": {"provider": value}, "instance id": {"instance": value}}[field]
            fixture.thread(part, parent=worker(1), turns=(("completed", 20, 10),), **{"title": f"part {number}", **how})
        elif field == "store name":
            fixture.meta["restaurant"] += f" {value}"
        elif field == "unit id":
            fixture.unit(value, "in-progress", f"item {number}")
        elif field == "branch":
            fixture.unit(unit, "in-progress", f"item {number}")
            fixture.units[-1]["branch"] = value
        else:
            links = {"pull request": value, "pull request path": f"https://git.example/{value}", "pull request query": f"{link}?x={value}",
                     "pull request fragment": f"{link}#{value}", "pull request userinfo": f"https://{quote(value, safe='')}@git.example/o/r/pull/{100 + number}"}
            fixture.unit(unit, "in-progress", f"Fix {value} today" if field == "summary" else f"item {number}", pr=links.get(field, ""))

    def test_no_review_value_and_no_id_path_instance_or_address_of_the_fixture_planted_in_each_field_planted_names_is_in_the_document_or_the_text(self):
        for field in PLANTED:
            with self.subTest(field):
                fixture = self.staffed(field.replace(" ", "-"))
                for number, value in enumerate((*REVIEW_VALUES, *self.own_values(fixture))):
                    self.plant(fixture, field, number, value)
                fixture.write()
                for form, output in (("document", self.document("--max-bytes", "500000")), ("text", self.out("--text"))):
                    found = [needle for needle in (*NEEDLES, *MARKERS, self.temporary, REQUEST) if needle in output]
                    self.assertEqual(found, [], f"{field}, {form}")


class PrivateValuesTest(OutputCase):
    def test_privacy_of_knows_the_ids_request_names_instances_addresses_and_absolute_paths_the_run_read_and_no_provider_name_of_the_table_or_relative_path(self):
        fixture = self.fixture
        fixture.meta["previousThread"] = "mcp:threadmarker-previous"
        fixture.coordinator()
        fixture.unit("D7", "in-progress", f"Ask {ADDRESS}", thread=worker(2), task="kit-d7-recorded-task")
        fixture.retired("D7", worker(1))
        fixture.thread(worker(2), title="D7 worker", instance=INSTANCE, turns=(("running", 30, None),))
        child = delegated(worker(2), REQUEST)
        node = fixture.thread(child, title="auditor <second@example.test>", provider="acct-homemarker-driver", instance="codex", parent=worker(2), turns=(("completed", 25, 20),))
        other = delegated("mcp:threadmarker-other-project", "other-request")
        fixture.thread(other, parent="mcp:threadmarker-other-project", turns=(("completed", 400, 390),))
        store, t3, _ = fixture.write().read()
        known = {value for found in MOD["privacy_of"](store, t3, (fixture.store, "relative")).index.values() for value in found}
        expected = {COORDINATOR, "mcp:threadmarker-previous", worker(1), worker(2), child, unquote(child), node, REQUEST, "kit-d7-recorded-task", INSTANCE, "acct-homemarker-driver",
                    ADDRESS, "second@example.test", fixture.meta["projectRoot"], str(fixture.store), os.path.realpath(fixture.store), os.path.abspath("relative"), os.path.realpath("relative"),
                    other, unquote(other), "other-request", "mcp:threadmarker-other-project", fixture.delegations[-1][0]}
        self.assertEqual(known, expected)

    def test_relative_at_and_out_leave_their_own_words_in_the_text_form_and_the_same_paths_written_in_full_are_removed(self):
        fixture = self.fixture
        fixture.store = fixture.store.with_name("queue")
        fixture.meta["restaurant"] = "queue"
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Fix the queue report", pr="https://example.test/o/queue/pull/7")
        fixture.unit("D8", "in-progress", f"Read {fixture.store} and {fixture.store.parent / 'report'} first")
        fixture.write()
        self.out("--text", "--out", "report", at="queue", cwd=fixture.store.parent)
        self.assertEqual((fixture.store.parent / "report").read_text().split("\n"), [
            "Agent activity for queue, last 3h",
            "0 running now, 0 agents, 0 sub-agents, 0 failed",
            "Work items",
            "  D7   working   Fix the queue report   no activity in this window   https://example.test/o/queue/pull/7",
            "  D8   working   Read and first   no activity in this window",
            "Notes",
            "  No agent or sub-agent of this coordinator ran in this window."])

    def test_document_and_text_keep_the_plain_model_and_the_pull_request_address_and_count_a_refused_link(self):
        fixture = one_running(self.fixture)
        fixture.units[0]["pr"] = "https://user:pw@example.test/o/r/pull/7?x=mcp:alpha-secret#task:alpha-secret"
        fixture.units[1]["pr"] = "https://example.test/o/r/pull/6/files"
        fixture.thread(delegated(worker(1), "kit-d7-part-1"), title="part 1", model="acct_secretXYZ", parent=worker(1), turns=(("completed", 20, 10),))
        fixture.thread(delegated(worker(1), "kit-d7-part-2"), title="part 2", provider="opencode", model="opencode/model-b-20260101", parent=worker(1), turns=(("completed", 20, 10),))
        fixture.write()
        data, text = data_of(self.document()), self.out("--text").split("\n")
        note = "1 pull request link is not shown. It is not an https://host/owner/repository/pull/number address, or it holds text this page removes."
        self.assertEqual((data["M"], [item[4] for item in data["I"]], data["N"]), (["model-a", "other model", "model-b-20260101"], ["", PR7], [note]))
        self.assertEqual([line for line in text if "   working   " in line or "D6" in line], ["  D7   working   Agent activity page   1 agent, 2 sub-agents, 83m at work   " + PR7,
                                                                                      "  D6   landing   Queue fix   no activity in this window"])
        self.assertEqual(text[-2:], ["  " + note, ""])


T3_REFUSAL = "activity: --out names a file inside T3 Code's directory or one of its database files; pass a path outside it"
STORE_REFUSAL = "activity: --out names a file inside the coordinator's store; pass a path outside it"


class OutTest(OutputCase):
    def refused(self, target, line=T3_REFUSAL):
        before = tree(self.fixture.root)
        self.assertEqual(self.fails(1, "--out", str(target)), line)
        self.assertEqual(tree(self.fixture.root), before)

    def test_out_inside_t3s_base_directory_is_refused_and_creates_no_file(self):
        fixture = one_running(self.fixture).write()
        for target in (fixture.database.parent / "page.html", fixture.base / "page.html", fixture.base / "new" / "page.html",
                       fixture.root / "absent" / ".." / fixture.base.name / "userdata" / "page.html", fixture.base):
            with self.subTest(target.name):
                self.refused(target)

    def test_out_that_is_the_database_or_one_of_its_side_files_is_refused_and_changes_no_byte(self):
        fixture = one_running(self.fixture).write(live=True)
        for suffix in ("", "-wal", "-shm"):
            with self.subTest(suffix):
                self.refused(f"{fixture.database}{suffix}")

    def test_out_through_a_symbolic_link_or_a_hard_link_to_t3s_files_is_refused(self):
        fixture = one_running(self.fixture).write()
        directory, file, hard = fixture.root / "alias", fixture.root / "link.html", fixture.root / "hard.html"
        directory.symlink_to(fixture.database.parent, target_is_directory=True)
        file.symlink_to(fixture.database)
        os.link(fixture.database, hard)
        for target in (directory / "page.html", file, hard):
            with self.subTest(target.name):
                self.refused(target)

    def test_out_beside_a_database_that_the_userdata_link_leads_to_is_refused(self):
        fixture = one_running(self.fixture)
        elsewhere = fixture.root / "elsewhere"
        elsewhere.mkdir(parents=True)
        fixture.base.mkdir(parents=True)
        (fixture.base / "userdata").symlink_to(elsewhere, target_is_directory=True)
        fixture.write()
        self.refused(elsewhere / "page.html")

    def test_out_inside_the_store_is_refused_and_changes_no_file(self):
        fixture = one_running(self.fixture).write()
        for target in (fixture.store / "page.html", fixture.store / "dishes.tsv"):
            with self.subTest(target.name):
                self.refused(target, STORE_REFUSAL)

    def test_out_through_a_symbolic_link_to_a_file_outside_t3s_directory_and_the_store_is_written(self):
        fixture = one_running(self.fixture).write()
        real, link = fixture.root / "real.html", fixture.root / "link.html"
        real.write_text("old")
        link.symlink_to(real)
        self.out("--out", str(link))
        self.assertTrue(real.read_bytes().startswith(b"<!doctype html>"))
        self.assertTrue(link.is_symlink())


class RowShapeTest(OutputCase):
    """The fixture's rows are the coordinator, then a worker, then the worker's delegated child."""

    def staffed(self, name, spoil=lambda fixture: None):
        fixture = self.fixture = Fixture(self.root / name)
        fixture.now = datetime(2026, 10, 10, 0, 30, tzinfo=timezone.utc).timestamp()
        fixture.coordinator()
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), title="D7 worker", turns=(("completed", 45, 15),))
        fixture.thread(delegated(worker(1), "kit-d7-part"), title="part", parent=worker(1), turns=(("completed", 25, 22),))
        spoil(fixture)
        return fixture.write()

    def refused(self, name, spoil):
        self.staffed(name, spoil)
        return self.fails(3, clock=self.fixture.now)

    def test_timestamp_in_another_shape_exits_3_wherever_its_text_sorts(self):
        texts = ("", "0000-bad", "bad", "2026-13-45T00:00:00.000Z", "2026-10-09T23:45:00", "2026-10-09 23:45:00.000Z", "2026-10-09T23:45:00.000000Z", 5, b"2026-10-09T23:45:00.000Z")
        columns = (("turns", "orchestration_v2_projection_runs.requested_at", 2, False), ("turns", "orchestration_v2_projection_runs.completed_at", 3, True),
                   ("delegations", "orchestration_v2_projection_subagents.started_at", 4, True), ("delegations", "orchestration_v2_projection_subagents.completed_at", 5, True))
        for rows, where, column, nullable in columns:
            for number, text in enumerate(texts if nullable else (*texts, None)):
                with self.subTest(where=where, text=text):
                    line = self.refused(f"{rows}-{column}-{number}", lambda fixture: put(getattr(fixture, rows), -1, column, text))
                    self.assertEqual(line, f"activity: T3's {where} is not a timestamp; {CHANGED}")

    def test_turn_written_with_a_utc_offset_across_midnight_is_drawn_like_the_same_instants_written_with_z(self):
        for hours in (None, 0, -7, 14):
            with self.subTest(hours):
                def offset(fixture):
                    put(fixture.turns, 1, 2, fixture.stamp_at_offset(45, hours))
                    put(fixture.turns, 1, 3, fixture.stamp_at_offset(15, hours))

                fixture = self.staffed(f"offset-{hours}", offset if hours is not None else lambda fixture: None)
                self.assertEqual(fixture.turns[1][2:], {None: ("2026-10-09T23:45:00.000Z", "2026-10-10T00:15:00.000Z"), 0: ("2026-10-09T23:45:00.000+00:00", "2026-10-10T00:15:00.000+00:00"),
                                                        -7: ("2026-10-09T16:45:00.000-07:00", "2026-10-09T17:15:00.000-07:00"),
                                                        14: ("2026-10-10T13:45:00.000+14:00", "2026-10-10T14:15:00.000+14:00")}[hours])
                data = data_of(self.document(clock=fixture.now))
                self.assertEqual((data["n"], data["G"][0][1][0][5:7]), ([0, 1, 1, 0], ["30m", [750, 167, 0]]))

    def test_value_that_is_not_text_or_an_empty_id_status_or_provider_exits_3_and_names_the_column(self):
        threads, runs, subagents = "orchestration_v2_projection_threads", "orchestration_v2_projection_runs", "orchestration_v2_projection_subagents"
        cases = (("threads", 2, 1, 42, f"{threads}.title is not text"), ("threads", 2, 1, None, f"{threads}.title is not text"), ("threads", 2, 1, b"part", f"{threads}.title is not text"),
                 ("threads", 1, 2, 42, f"{threads}.default_provider is not text"), ("threads", 1, 2, None, f"{threads}.default_provider is not text"),
                 ("threads", 1, 2, "", f"{threads}.default_provider is empty"),
                 ("turns", 1, 0, None, f"{runs}.thread_id is not text"), ("turns", 1, 0, 42, f"{runs}.thread_id is not text"), ("turns", 1, 0, "", f"{runs}.thread_id is empty"),
                 ("turns", 1, 1, None, f"{runs}.status is not text"), ("turns", 1, 1, "", f"{runs}.status is empty"),
                 ("delegations", 0, 0, 42, f"{subagents}.subagent_id is not text"), ("delegations", 0, 0, None, f"{subagents}.subagent_id is not text"),
                 ("delegations", 0, 1, None, f"{subagents}.thread_id is not text"), ("delegations", 0, 1, "", f"{subagents}.thread_id is empty"),
                 ("delegations", 0, 2, 42, f"{subagents}.child_thread_id is not text"), ("delegations", 0, 2, "", f"{subagents}.child_thread_id is empty"),
                 ("delegations", 0, 3, 42, f"{subagents}.status is not text"), ("delegations", 0, 3, None, f"{subagents}.status is not text"))
        for number, (rows, index, column, value, words) in enumerate(cases):
            with self.subTest(words=words, value=value):
                line = self.refused(f"type-{number}", lambda fixture: put(getattr(fixture, rows), index, column, value))
                self.assertEqual(line, f"activity: T3's {words}; {CHANGED}")

    def test_thread_payload_whose_model_selection_holds_no_model_or_no_instance_id_text_exits_3(self):
        cases = (('{"modelSelection":{}}', "model"), ('{"modelSelection":{"model":42,"instanceId":"codex"}}', "model"), ('{"modelSelection":[]}', "model"),
                 ('{"modelSelection":null}', "model"), ('{"modelSelection":{"model":"","instanceId":"codex"}}', "model"),
                 ('{"modelSelection":{"model":"model-a"}}', "instanceId"), ('{"modelSelection":{"model":"model-a","instanceId":7}}', "instanceId"),
                 ('{"modelSelection":{"model":"model-a","instanceId":""}}', "instanceId"))
        for number, (payload, key) in enumerate(cases):
            with self.subTest(payload):
                line = self.refused(f"payload-{number}", lambda fixture: put(fixture.threads, 0, 3, payload))
                self.assertEqual(line, f"activity: T3's thread payload holds no text at modelSelection.{key}; {CHANGED}")

    def test_payload_nested_too_deep_to_parse_or_not_text_exits_3(self):
        for number, payload in enumerate(("[" * 100000, b'{"modelSelection":{"model":"model-a","instanceId":"codex"}}', 42, None)):
            with self.subTest(number):
                line = self.refused(f"deep-{number}", lambda fixture: put(fixture.threads, 0, 3, payload))
                self.assertEqual(line, f"activity: T3's thread payload has no modelSelection; {CHANGED}")

    def test_thread_with_a_turn_and_no_thread_row_exits_3(self):
        for rows, index in (("threads", 1), ("threads", 2)):
            with self.subTest(index):
                line = self.refused(f"absent-{index}", lambda fixture: put(getattr(fixture, rows), index, 0, None))
                self.assertEqual(line, "activity: T3's orchestration_v2_projection_threads has no row for a thread that orchestration_v2_projection_runs "
                                       f"or orchestration_v2_projection_subagents names; {CHANGED}")

    def test_sub_agent_with_no_thread_or_no_start_time_is_counted_in_a_note_and_one_with_a_turn_is_drawn_from_its_turn(self):
        def pending(fixture):
            fixture.delegations.append(("node:nodemarker-pending", worker(1), None, "running", None, None))
            fixture.thread(delegated(worker(1), "kit-d7-late"), title="late", parent=worker(1), delegation=("running", 20, None))
            put(fixture.delegations, -1, 4, None)
            put(fixture.delegations, 0, 4, None)
            fixture.delegations.append(("node:nodemarker-elsewhere", "mcp:threadmarker-other-project", None, "running", None, None))

        fixture = self.staffed("pending", pending)
        data = data_of(self.document(clock=fixture.now))
        self.assertEqual((data["n"], [line[:2] for line in data["G"][0][1]]), ([0, 1, 1, 0], [[0, "worker"], [1, "part"]]))
        self.assertEqual(data["N"], ["T3 lists 2 sub-agents with no thread or no start time under threads this page read. They are not shown."])
        self.assertIn("  T3 lists 2 sub-agents with no thread or no start time under threads this page read. They are not shown.", self.out("--text", clock=fixture.now).split("\n"))

    def test_sqlite_error_that_quotes_a_value_exits_3_without_the_value(self):
        fixture = self.staffed("undecodable")
        connection = sqlite3.connect(fixture.database)
        connection.execute("update orchestration_v2_projection_threads set title = cast(x'70617468ff6d61726b6572' as text) where title = 'part'")
        connection.commit()
        connection.close()
        line = self.fails(3, clock=fixture.now)
        self.assertEqual(line, "activity: cannot read T3's database (a text value is not UTF-8); check that T3 Code is running and try again")


def queued_only(fixture):
    fixture.coordinator()
    fixture.unit("D7", "in-progress", "Activity page", thread=worker(1))
    fixture.thread(worker(1), title="D7 worker", turns=(("queued", 5, None),))
    return fixture


class QueuedTest(OutputCase):
    def test_queued_turn_is_listed_under_queued_is_not_counted_running_and_keeps_a_queued_bar(self):
        for status in ("queued", "preparing"):
            with self.subTest(status):
                fixture = self.fixture = queued_only(Fixture(self.root / status))
                put(fixture.turns, 1, 1, status)
                fixture.write()
                self.assertEqual(self.out("--text", clock=fixture.now), "\n".join([
                    "Agent activity for kit, last 3h",
                    "0 running now, 1 agent, 0 sub-agents, 0 failed",
                    "Queued",
                    "  D7 worker   model-a   queued for 5m",
                    "Work items",
                    "  D7   working   Activity page   1 agent, 0s at work",
                    ""]))
                data = data_of(self.document(clock=fixture.now))
                self.assertEqual((data["n"], data["S"][1], data["G"][0][1][0][4:]), ([0, 1, 0, 0], "queued", [1, "0s", [972, 28, 5], 1, "5m"]))

    def test_agent_with_a_running_turn_and_a_queued_turn_is_counted_running_and_keeps_both_bars(self):
        fixture = queued_only(self.fixture)
        fixture.turns[1:] = [(worker(1), "running", fixture.stamp(10), None), (worker(1), "queued", fixture.stamp(5), None)]
        fixture.write()
        lines = self.out("--text", clock=fixture.now).split("\n")
        self.assertEqual(lines[1:5], ["1 running now, 1 agent, 0 sub-agents, 0 failed", "Running now", "  D7 worker   model-a   running for 10m", "Work items"])
        data = data_of(self.document(clock=fixture.now))
        self.assertEqual((data["n"], data["G"][0][1][0][4:]), ([1, 1, 0, 0], [0, "10m", [944, 56, 1, 972, 28, 5], 1, "10m"]))

    def test_providers_own_sub_agent_whose_open_delegation_is_queued_is_listed_under_queued(self):
        fixture = queued_only(self.fixture)
        put(fixture.turns, 1, 1, "running")
        fixture.thread(native(1), title="/root/spec_review", parent=worker(1), delegation=("queued", 2, None))
        fixture.write()
        lines = self.out("--text", clock=fixture.now).split("\n")
        self.assertEqual(lines[1:6], ["1 running now, 1 agent, 1 sub-agent, 0 failed", "Running now", "  D7 worker   model-a   running for 5m", "Queued",
                                      "  D7 spec_review   model-a   queued for 2m   under worker"])


class BudgetTest(OutputCase):
    def test_stdout_of_the_html_form_with_its_newline_is_at_most_max_bytes_at_the_exact_size_and_one_byte_under_it(self):
        fixture = busy(self.fixture).write()
        whole = fixture.stdout_bytes("--max-bytes", "500000", clock=fixture.now)
        self.assertTrue(len(whole) > 16001 and whole.endswith(b"</script>\n"))
        self.assertEqual(fixture.stdout_bytes("--max-bytes", str(len(whole)), clock=fixture.now), whole)
        under = fixture.stdout_bytes("--max-bytes", str(len(whole) - 1), clock=fixture.now)
        self.assertLess(len(under), len(whole))
        self.assertLessEqual(len(fixture.stdout_bytes(clock=fixture.now)), 16000)

    def test_stdout_is_utf8_under_a_locale_that_is_not(self):
        fixture = one_running(self.fixture)
        fixture.units[0]["summary"] = "Agent activity page 𝔸é"
        fixture.write()
        ascii_locale = {"LC_ALL": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"}
        for form in ((), ("--text",)):
            with self.subTest(form):
                self.assertIn("Agent activity page 𝔸é".encode(), fixture.stdout_bytes(*form, env=ascii_locale))

    def test_link_cut_by_the_last_fold_is_counted_in_a_note(self):
        fixture = extreme(self.fixture)
        for number, unit in enumerate(fixture.units, start=1):
            unit["pr"] = "https://example.test/" + "o" * 60 + "/" + "r" * 80 + f"/pull/{number}"
        fixture.write()
        data = data_of(self.document())
        cut = sum(not item[4] for item in data["I"])
        self.assertEqual((cut, len(data["I"]) >= 8), (len(data["I"]), True))
        self.assertIn(f"{cut} pull request links are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.", data["N"])
        whole = data_of(self.document("--max-bytes", "500000"))
        self.assertEqual(([item[4] for item in whole["I"] if not item[4]], [note for note in whole["N"] if "pull request" in note]), ([], []))


class WindowTest(OutputCase):
    def test_window_is_labelled_by_its_whole_seconds_as_hours_minutes_and_seconds_in_both_forms(self):
        fixture = one_running(self.fixture).write()
        for hours, seconds, words in (("0.01", 36, "36s"), ("0.25", 900, "15m"), ("1", 3600, "1h"), ("1.51", 5436, "1h 30m 36s"), ("3", 10800, "3h"), ("168", 604800, "168h")):
            with self.subTest(hours):
                self.assertEqual(self.out("--text", "--hours", hours).split("\n")[0], f"Agent activity for kit, last {words}")
                self.assertEqual(data_of(self.document("--hours", hours))["w"][1:], [seconds, words])


if __name__ == "__main__":
    unittest.main()
