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

    def test_provider_names_come_from_the_table_and_any_other_driver_reads_other(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), provider="codex", turns=(("completed", 90, 80),))
        for number, provider in enumerate(("acct-homemarker-instance", "acct-homemarker-instance", "grok", "cursor", "opencode", "claudeAgent", "")):
            fixture.thread(native(number), provider=provider, parent=worker(1), delegation=("completed", 70, 60))
        page = page_of(fixture)
        self.assertEqual(page.legend, (("Other", 3), ("Claude", 1), ("Codex", 1), ("Cursor", 1), ("Grok", 1), ("OpenCode", 1)))
        self.assertEqual(sorted({row.provider for row in page.groups[0].rows}), ["Claude", "Codex", "Cursor", "Grok", "OpenCode", "Other"])

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

    def test_title_over_48_characters_loses_to_the_request_name(self):
        self.assertEqual(self.label("Read-only review. Do not edit, commit, push, or merge.", "brigade-kit-d7r2-fix"), "fix")
        self.assertEqual(self.label("x" * 48, "brigade-kit-d7r2-fix"), "x" * 39 + "…")

    def test_leading_id_of_the_rows_own_unit_is_dropped_from_a_title(self):
        self.assertEqual(self.label("D7: rehearsal of the wake"), "rehearsal of the wake")
        self.assertEqual(self.label("D7 fresh-child test"), "fresh-child test")
        self.assertEqual(self.label("D8: rehearsal"), "D8: rehearsal")
        self.assertEqual(self.label("D7: rehearsal", unit=None), "D7: rehearsal")

    def test_title_that_is_one_path_gives_its_last_segment_and_any_other_path_is_dropped(self):
        self.assertEqual(self.label("/root/spec_review", parent=worker(2)), "spec_review")
        self.assertEqual(self.label("/pathmarker/notes.md", parent=worker(2)), "sub-agent")
        self.assertEqual(self.label("Read /pathmarker/brief.md first", parent=worker(2)), "Read first")

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

    def test_model_name_is_the_text_after_the_last_slash(self):
        self.assertEqual(MOD["model_name"]("vendor/sub/model-b-20260101"), "model-b-20260101")
        self.assertEqual(MOD["model_name"]("model-a"), "model-a")


if __name__ == "__main__":
    unittest.main()
