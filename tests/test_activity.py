import hashlib
import json
import os
import runpy
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
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

        The delegation runs from the first turn's start to the last turn's end unless `delegation` gives (status, start, end).
        Returns the delegation's sub-agent id, or None.
        """
        self.threads.append((ident, title, provider, json.dumps({"modelSelection": {"model": model}}) if payload is None else payload))
        self.turns += [(ident, status, self.stamp(start), self.stamp(end)) for status, start, end in turns]
        if parent is None:
            return None
        if delegation is None:
            end = turns[-1][2] if turns else None
            delegation = ("running" if end is None else "completed", turns[0][1] if turns else 30, end)
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

    def run(self, *args, at=True, t3_home=True, env=None):
        words = [sys.executable, str(SCRIPT)]
        words += ["--at", str(self.store)] if at is True else ["--at", str(at)] if at else []
        words += ["--t3-home", str(self.base)] if t3_home is True else ["--t3-home", str(t3_home)] if t3_home else []
        environment = {"HOME": str(self.home), "PATH": os.environ.get("PATH", ""), **(env or {})}
        return subprocess.run([*words, *args], capture_output=True, text=True, env=environment, cwd=self.root)

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


if __name__ == "__main__":
    unittest.main()
