import base64
import fcntl
import hashlib
import html
import json
import os
import random
import re
import runpy
import select
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests import in_process
from unittest import mock
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "t3/added/brigade/scripts/activity.py"
MOD = runpy.run_path(str(SCRIPT))
SKILL = ROOT / "t3/added/brigade/SKILL.md"
VIEWS = ROOT / "t3/added/brigade/views.md"
GUIDE = ROOT / "docs/guide.md"

MARKERS = ("threadmarker", "nodemarker", "pathmarker", "homemarker", "projectmarker", "0a1b2c3")
COORDINATOR = "mcp:threadmarker-coordinator"
PROJECT = "project:projectmarker-own"
ELSEWHERE = "project:projectmarker-elsewhere"
CHANGED = "this T3 build stores threads differently, so update pstack-t3"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc).timestamp()
FROZEN = "import runpy, sys, time\nclock = float(sys.argv[1])\ntime.time = lambda: clock\ndel sys.argv[:2]\nrunpy.run_path(sys.argv[0], run_name='__main__')"
SCHEMA = {
    "orchestration_v2_projection_metadata": ("projection_name", "schema_version"),
    "orchestration_v2_projection_threads": ("thread_id", "title", "default_provider", "payload_json", "project_id"),
    "orchestration_v2_projection_runs": ("thread_id", "status", "requested_at", "completed_at"),
    "orchestration_v2_projection_subagents": ("subagent_id", "thread_id", "child_thread_id", "status", "started_at", "completed_at"),
}
UNIT_COLUMNS = ("id", "at", "state", "station", "tickets", "task", "thread", "branch", "pr", "sha", "summary", "timebox", "lease", "paths", "reported")
LOG_COLUMNS = ("at", "kind", "id", "state", "note")


def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
    saved = dict(os.environ)

    def restore():
        os.environ.clear()
        os.environ.update(saved)

    unittest.addModuleCleanup(restore)
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull


def setUpModule():
    fence_git_environment()


def worker(number):
    return f"mcp:threadmarker-worker-{number}"


def write_threads(fixture, rows):
    columns = ("at", "dish", "attempt", "role", "thread", "seat", "start", "end")
    lines = ["\t".join(columns)]
    lines += ["\t".join(row.get(column, "") for column in columns) for row in rows]
    (fixture.store / "threads.tsv").write_text("\n".join(lines) + "\n")


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
        self.now = NOW
        self.units, self.log, self.threads, self.turns, self.delegations = [], [], [], [], []
        self.writer = None

    def ago(self, minutes):
        assert self.now == int(self.now), "a stamp is written in milliseconds, so a fractional now can put it after the instant it names and shorten an age by a second"
        return self.now - minutes * 60

    def stamp(self, minutes):
        return None if minutes is None else datetime.fromtimestamp(self.ago(minutes), timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def unit(self, ident, state="in-progress", summary="", thread="", task="", pr=""):
        row = dict.fromkeys(UNIT_COLUMNS, "")
        row.update(id=ident, at=self.stamp(600), state=state, summary=summary, thread=thread, task=task, pr=pr)
        self.units.append(row)

    def retired(self, ident, thread):
        self.log.append({"at": self.stamp(500), "kind": "worker", "id": ident, "state": "retired", "note": thread})

    def thread(self, ident, title="", provider="claudeAgent", model="model-a", parent=None, turns=(), delegation=None, payload=None, instance=None, project=PROJECT):
        selection = {"instanceId": provider if instance is None else instance, "model": model}
        self.threads.append((ident, title, provider, json.dumps({"modelSelection": selection}) if payload is None else payload, project))
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

    def command(self, *args, at=True, t3_home=True, env=None, cwd=None):
        """The script runs as the main program of a process whose time.time() returns self.now, however long ago the fixture was built."""
        argv = ["--at", str(self.store)] if at is True else ["--at", str(at)] if at else []
        argv += ["--t3-home", str(self.base)] if t3_home is True else ["--t3-home", str(t3_home)] if t3_home else []
        environment = {"HOME": str(self.home), "PATH": os.environ.get("PATH", ""), **(env or {})}
        return in_process.Invocation(
            program=SCRIPT,
            argv=(*argv, *args),
            env=environment,
            cwd=cwd or self.root,
            clock=self.now,
        )

    def run(self, *args, **how):
        return in_process.invoke(self.command(*args, **how))

    def stdout_bytes(self, *args, **how):
        result = in_process.invoke(replace(self.command(*args, **how), text=False))
        assert (result.returncode, result.stderr) == (0, b""), result.stderr
        return result.stdout

    def stamp_at_offset(self, minutes, hours):
        return datetime.fromtimestamp(self.ago(minutes), timezone(timedelta(hours=hours))).isoformat(timespec="milliseconds")

    def start(self, *args, **how):
        return in_process.spawn(in_process.Reason.RACE, self.command(*args, **how))

    def read(self, hours=3.0):
        store = MOD["read_store"](self.store)
        window = MOD["Window"](self.now - hours * 3600, self.now)
        connection = MOD["open_t3"](self.database)
        try:
            MOD["check_shape"](connection)
            return store, MOD["read_t3"](connection, window, MOD["roots_of"](store), store.coordinators), window
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

    def widened(self, cells):
        """Rewrite the work item table with each row's extra cells from `cells` by item id appended to its 15 cells."""
        path = self.fixture.store / "dishes.tsv"
        header, *rows = path.read_text().splitlines()
        path.write_text("\n".join([header, *("\t".join([row, *cells.get(row.split("\t")[0], ())]) for row in rows)]) + "\n")

    def brigade_rows(self, table):
        return runpy.run_path(str(SCRIPT.with_name("brigade.py")))["Restaurant"](self.fixture.store).rows(table)

    def kinds_and_goals(self):
        return [(row["id"], row["kind"], row["goal"]) for row in MOD["read_table"](self.fixture.store, "dishes.tsv")]

    def test_work_item_table_of_15_cell_rows_is_read_with_an_empty_kind_and_goal_on_each_row(self):
        one_running(self.fixture).write()
        self.assertEqual([len(line.split("\t")) for line in (self.fixture.store / "dishes.tsv").read_text().splitlines()], [15, 15, 15])
        self.assertEqual(self.kinds_and_goals(), [("D7", "", ""), ("D6", "", "")])
        self.assertEqual(MOD["read_table"](self.fixture.store, "dishes.tsv"), self.brigade_rows("dishes.tsv"))

    def test_work_item_table_of_17_cell_rows_is_read_with_the_kind_and_goal_each_row_holds(self):
        one_running(self.fixture).write()
        self.widened({"D7": ("feature", "G1"), "D6": ("fix", "")})
        self.assertEqual(self.kinds_and_goals(), [("D7", "feature", "G1"), ("D6", "fix", "")])
        self.assertEqual(MOD["read_table"](self.fixture.store, "dishes.tsv"), self.brigade_rows("dishes.tsv"))

    def test_work_item_table_that_mixes_15_cell_and_17_cell_rows_is_read_with_the_rows_brigade_reads(self):
        one_running(self.fixture).write()
        self.widened({"D6": ("polish", "G2")})
        self.assertEqual([len(line.split("\t")) for line in (self.fixture.store / "dishes.tsv").read_text().splitlines()], [15, 15, 17])
        self.assertEqual(self.kinds_and_goals(), [("D7", "", ""), ("D6", "polish", "G2")])
        self.assertEqual(MOD["read_table"](self.fixture.store, "dishes.tsv"), self.brigade_rows("dishes.tsv"))

    def test_work_item_row_of_16_cells_lacks_one_trailing_cell_and_is_read_with_an_empty_goal_as_brigade_reads_it(self):
        one_running(self.fixture).write()
        self.widened({"D7": ("upkeep",)})
        self.assertEqual(self.kinds_and_goals(), [("D7", "upkeep", ""), ("D6", "", "")])
        self.assertEqual(MOD["read_table"](self.fixture.store, "dishes.tsv"), self.brigade_rows("dishes.tsv"))

    def test_rows_outside_the_five_kinds_and_without_a_goal_row_print_the_15_cell_page_and_a_real_kind_does_not(self):
        fixture = one_running(self.fixture).write()
        narrow = (fixture.stdout_bytes(), fixture.stdout_bytes("--text"))
        self.widened({"D7": ("nope", "G9"), "D6": ("later", "G2")})
        self.assertEqual(self.kinds_and_goals(), [("D7", "nope", "G9"), ("D6", "later", "G2")])
        self.assertEqual((fixture.stdout_bytes(), fixture.stdout_bytes("--text")), narrow)
        self.assertIn(b"Agent activity page", narrow[0])
        self.assertEqual(narrow[1].split(b"\n")[0], b"Agent activity for kit, last 3h")
        fixture.write_store()
        self.widened({"D7": ("feature", "G1"), "D6": ("fix", "G2")})
        self.assertEqual(self.kinds_and_goals(), [("D7", "feature", "G1"), ("D6", "fix", "G2")])
        wide = (fixture.stdout_bytes(), fixture.stdout_bytes("--text"))
        self.assertNotEqual(wide, narrow)
        self.assertIn(b"feature", wide[0])
        self.assertIn(b"feature", wide[1])
        self.assertNotIn(b"nope", wide[0])

    def test_work_item_row_of_14_cells_or_18_cells_exits_1_with_its_line_number(self):
        for extra in (None, ("feature", "G1", "one more")):
            with self.subTest(extra):
                fixture = self.fixture = one_running(Fixture(self.root / str(len(extra or ())))).write()
                if extra is None:
                    path = fixture.store / "dishes.tsv"
                    header, first, second = path.read_text().splitlines()
                    path.write_text("\n".join([header, first, second.rsplit("\t", 1)[0]]) + "\n")
                else:
                    self.widened({"D6": extra})
                self.assertEqual(len((fixture.store / "dishes.tsv").read_text().splitlines()[2].split("\t")), 14 if extra is None else 18)
                self.assertEqual(self.fails(1), "activity: line 3 of the store's work item table is malformed; fix or remove it")

    def test_log_row_that_is_not_utf8_exits_1_with_its_line_number(self):
        self.fixture.write()
        with open(self.fixture.store / "log.tsv", "ab") as table:
            table.write(b"\xff\t\t\t\t\n")
        self.assertEqual(self.fails(1), "activity: line 2 of the store's log is malformed; fix or remove it")

    def test_table_that_ends_in_a_cut_multi_byte_character_is_read_with_the_rows_brigade_reads_from_the_same_bytes(self):
        fixture = one_running(self.fixture)
        fixture.retired("D7", worker(9))
        fixture.write()
        for name in ("dishes.tsv", "log.tsv"):
            with open(fixture.store / name, "ab") as table:
                table.write("D8\tcut \u20ac".encode()[:-1])
        result = fixture.run()
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual([each[0] for each in json.loads(re.search(r"id=d>(.*?)</script>", result.stdout).group(1))["I"]], ["D6", "D7"])
        self.assertEqual(fixture.run("--text").stdout.split("\n")[:2], ["Agent activity for kit, last 3h", "1 running now, 1 agent, 0 sub-agents, 0 failed"])
        rows = runpy.run_path(str(SCRIPT.with_name("brigade.py")))["Restaurant"](fixture.store).rows
        self.assertEqual([row["id"] for row in rows("dishes.tsv")], ["D7", "D6"])
        self.assertEqual([row["note"] for row in rows("log.tsv")], [worker(9)])
        for name in ("dishes.tsv", "log.tsv"):
            self.assertEqual(MOD["read_table"](fixture.store, name), rows(name))

    def test_wrong_field_count_on_a_line_before_one_that_is_not_utf8_exits_1_with_the_line_number_brigade_names(self):
        fixture = self.fixture
        fixture.write()
        with open(fixture.store / "log.tsv", "ab") as table:
            table.write(b"too\tfew\n\xff\t\t\t\t\n")
        self.assertEqual(self.fails(1), "activity: line 2 of the store's log is malformed; fix or remove it")
        brigade = runpy.run_path(str(SCRIPT.with_name("brigade.py")))
        with self.assertRaises(brigade["BrigadeError"]) as refused:
            brigade["Restaurant"](fixture.store).rows("log.tsv")
        self.assertEqual(str(refused.exception), "proj/kit/log.tsv line 2 is malformed; fix or remove it")

    def test_store_columns_equal_brigade_tables(self):
        tables = runpy.run_path(str(SCRIPT.with_name("brigade.py")))["TABLES"]
        names = ("dishes.tsv", "log.tsv", "threads.tsv")
        self.assertEqual({name: MOD["STORE_COLUMNS"][name] for name in names}, {name: tables[name] for name in names})

    def test_malformed_thread_table_line_exits_1_with_the_line_number(self):
        self.fixture.write()
        path = self.fixture.store / "threads.tsv"
        header = "at\tdish\tattempt\trole\tthread\tseat\tstart\tend\n"
        path.write_text(header + "only\tseven\tcells\there\tand\there\tend\n")
        self.assertEqual(self.fails(1), "activity: line 2 of the store's thread table is malformed; fix or remove it")
        path.write_bytes(header.encode() + b"\xff\t\t\t\t\t\t\t\n")
        self.assertEqual(self.fails(1), "activity: line 2 of the store's thread table is malformed; fix or remove it")


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

    def test_blank_variable_falls_back_to_the_default_base_directory(self):
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


def same(text):
    return text


def CLEAN(text):
    """The filter with no identifier and no path of a run."""
    return MOD["Privacy"]().clean(text)


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

    def test_bars_join_when_the_same_status_is_under_5_thousandths_apart_and_a_queued_turn_adds_no_seconds(self):
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
        self.assertIn("  D7   working   1 sub-agent, 10m at work", MOD["render_text"](page, CLEAN).split("\n"))

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
        self.assertEqual((labels(page), page.coordinator, page.hidden.other_threads), ({"D7": [(0, "worker"), (1, "how explorer")]}, None, 0))


class LabelTest(unittest.TestCase):
    STORE = store_of(("D7", worker(2), (worker(1),)), ("D8", "", ()))

    def label(self, title="", request="", parent=COORDINATOR, unit="D7"):
        thread = delegated(parent, request) if request else native(1) if parent else worker(9)
        return MOD["label_of"](agent(thread, title=title, parent=parent), self.STORE, unit, MOD["Privacy"]({thread, COORDINATOR, worker(1), worker(2)}))

    def test_current_worker_and_earlier_worker(self):
        self.assertEqual(MOD["label_of"](agent(worker(2), title="D7 anything"), self.STORE, "D7", MOD["Privacy"]()), "worker")
        self.assertEqual(MOD["label_of"](agent(worker(1), title="D7 anything"), self.STORE, "D7", MOD["Privacy"]()), "earlier worker")

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
        for title in ("/root/notes.md", "/pathmarker/notes", "/root/spec_review now"):
            with self.subTest(title):
                self.assertEqual(self.label(title, parent=worker(2)), "sub-agent")
        self.assertEqual((self.label("/root", parent=worker(2)), self.label("/review", parent=worker(2))), ("/root", "/review"))

    def test_title_that_holds_private_text_loses_to_the_request_name(self):
        self.assertEqual(self.label("Read the brief at /pathmarker/brief.md", "why-investigator"), "why investigator")
        self.assertEqual(self.label("Read /pathmarker/brief.md first", parent=worker(2)), "sub-agent")
        self.assertEqual(self.label(f"Ask {worker(1)} first", "why-investigator"), "why investigator")
        self.assertEqual(self.label("Mail acct-homemarker@example.test first", "why-investigator"), "why investigator")
        self.assertEqual(self.label("Read the brief first", "why-investigator"), "Read the brief first")
        self.assertEqual(self.label("Fix 0a1b2c3 in src/a.py now", "brigade-kit-d7r2-fix"), "Fix 0a1b2c3 in src/a.py now")

    def test_title_that_holds_its_own_request_name_of_two_or_more_parts_loses_to_the_words_of_that_name(self):
        self.assertEqual(self.label("see why-investigator", "why-investigator"), "why investigator")
        self.assertEqual(self.label("Why-Investigator: part 2", "why-investigator"), "why investigator")
        self.assertEqual(self.label("why-investigator", "why-investigator"), "why investigator")
        self.assertEqual(self.label("see the explorer", "explorer"), "see the explorer")
        self.assertEqual(self.label("Act as the brigade-d7 sub-agent for this task.", "brigade-d7"), "brigade d7")
        self.assertEqual(self.label("Act as the Brigade-D7 review sub-agent.", "brigade-d7"), "brigade d7")

    def test_request_name_of_one_ordinary_word_is_the_label_and_a_title_of_that_word_is_too(self):
        for word in ("explorer", "queue", "code", "review", "src", "451237"):
            with self.subTest(word):
                self.assertEqual((self.label(request=word), self.label(word, word), self.label("Act as the review sub-agent for this task.", word)), (word, word, word))
        self.assertEqual(self.label(request="how-explorer"), "how explorer")

    def test_title_that_is_one_lower_case_word_with_a_hyphen_is_read_as_a_request_name(self):
        self.assertEqual(self.label("brigade-kit-d7-verify-2", "brigade-kit-d7-verify-0a1b2c3"), "review 2")

    def test_request_name_words_drop_brigade_the_stores_leading_slug_parts_unit_ids_and_parts_of_7_or_more_hexadecimal_characters_with_a_digit_and_a_letter(self):
        self.assertEqual(self.label(request="brigade-kit-d7-verify-0a1b2c3"), "review")
        self.assertEqual(self.label(request="proj-kit-nightly-audit-kit-1234567"), "nightly audit kit 1234567")
        self.assertEqual(self.label(request="audit-0a1b2c-facade-defaced-deadbeef01"), "audit 0a1b2c facade defaced")
        self.assertEqual(self.label(request="trial-d8-kimi"), "trial kimi")
        self.assertEqual(self.label(request="trial-d9-kimi"), "trial d9 kimi")

    def test_role_from_an_act_as_title_when_the_request_name_leaves_nothing(self):
        self.assertEqual(self.label("Act as the review sub-agent for this task.", "brigade-kit-d7-0a1b2c3"), "review")

    def test_request_name_whose_every_part_the_store_explains_gives_all_its_words_when_no_title_names_a_role(self):
        store = store_of(("queue", "", ()), ("review", "", ()), name="queue", slug=("queue",))
        for request, label in (("queue", "queue"), ("review", "review"), ("queue-review", "queue review"), ("brigade-queue-review-0a1b2c3", "brigade queue review")):
            with self.subTest(request):
                thread = delegated(COORDINATOR, request)
                self.assertEqual(MOD["label_of"](agent(thread, parent=COORDINATOR), store, "queue", MOD["Privacy"]({thread})), label)
        self.assertEqual(self.label(request="brigade-kit-d7"), "brigade kit d7")

    def test_thread_with_no_usable_title_or_request_reads_sub_agent_or_agent(self):
        self.assertEqual(self.label("123e4567-e89b-12d3-a456-426614174000", parent=worker(2)), "sub-agent")
        self.assertEqual(self.label("You are the helper.", parent=None), "agent")


UUID_TEXT = "123e4567-e89b-12d3-a456-426614174000"
ORDINARY = ("queue", "code", "review", "src", "private auditor", "451237", "D7", "pstack-t3", "gpt-6.1-sol", "claude-sonnet-4-5-20250929", "kit-d7-audit-task", "0a1b2c3")


class FilterTest(unittest.TestCase):
    """Privacy on one string: clean, field, model, and link, and the unescaped that clean reads. The identifiers and paths are the ones each test gives."""

    def clean(self, text, identifiers=(), paths=()):
        return MOD["Privacy"](identifiers, paths).clean(text)

    def test_clean_removes_each_word_that_holds_an_identifier_it_was_given_in_any_case_as_written_and_percent_encoded_once_and_twice(self):
        child = delegated(COORDINATOR, "kit-d7-audit")
        ids = {child, COORDINATOR, "sess_Ab12Cd34Ef56", "x y_secret1 z"}
        for value in (child, unquote(child), unquote(unquote(child)), COORDINATOR, "sess_Ab12Cd34Ef56", "x y_secret1 z"):
            forms = (value, value.upper(), value.lower(), value.swapcase(), quote(value, safe=""), quote(quote(value, safe=""), safe=""), quote(value, safe="").lower(),
                     value.replace("-", "%2D").replace("_", "%5f"), quote(value))
            for form in forms:
                with self.subTest(form):
                    self.assertEqual((self.clean(f"a {form} b", ids), self.clean(f"a pre{form}post b", ids), self.clean(f"a (id={form}), b", ids)), ("a b", "a b", "a b"))

    def test_clean_keeps_an_identifier_that_reads_as_ordinary_words_and_every_ordinary_word(self):
        text = "Fix the queue code review in src for private auditor 451237 on D7 of pstack-t3 with gpt-6.1-sol and claude-sonnet-4-5-20250929 by kit-d7-audit-task at 0a1b2c3"
        self.assertEqual(self.clean(text, ORDINARY), text)
        self.assertEqual([value for value in ORDINARY if not MOD["ordinary"](value)], [])
        structured = (COORDINATOR, worker(1), native(1), "sess_Ab12Cd34Ef56", "0123456789abcdef0", "a" * 49, "two  spaces", "queue/report", "queue:report", "-queue", "queue-")
        self.assertEqual([value for value in structured if MOD["ordinary"](value)], [])
        self.assertEqual(self.clean("the 0123456789abcdef0 run and abc", {"0123456789abcdef0", "abc"}), "the run and abc")

    def test_clean_removes_each_word_that_holds_a_path_it_was_given_and_a_path_below_it(self):
        paths = {"/srv/pathmarker state/proj", "/home/homemarker"}
        self.assertEqual(self.clean("read /srv/pathmarker state/proj and /SRV/PATHMARKER STATE/PROJ/kit/x.md and x=/home/homemarker/.t3 end", paths=paths), "read and and end")
        self.assertEqual(self.clean("in %2Fsrv%2Fpathmarker%20state%2Fproj and %252Fhome%252Fhomemarker%252Fa and pre/home/homemarkerpost end", paths=paths), "in and and end")

    def test_clean_removes_a_word_that_holds_a_uuid_or_one_of_t3s_id_words_before_a_colon_and_a_letter_digit_underscore_or_percent_sign(self):
        self.assertEqual(self.clean(f"at {UUID_TEXT} and x{UUID_TEXT.upper()}y and {quote(UUID_TEXT)} end"), "at and and end")
        for word in MOD["T3_KINDS"]:
            with self.subTest(word):
                self.assertEqual(self.clean(f"a {word}:alpha b {word.upper()}:1 c prefix={word}:x d {word}%3Aalpha e {word}%253A_x f x{word}:%41 g {word}:%20x h"), "a b c d e f g h")
                self.assertEqual(self.clean(f"the {word}: one and {word} :x and {word}:) end"), f"the {word}: one and {word} :x and {word}:) end")
        self.assertEqual(self.clean("12:30 D7:fix feature:x key:value a::b Re:plan"), "12:30 D7:fix feature:x key:value a::b Re:plan")

    def test_clean_removes_a_word_that_holds_an_email_address_and_keeps_an_at_sign_with_no_address_around_it(self):
        self.assertEqual(self.clean("mail acct-homemarker@example.test and <a.b@c.org>, x=USER@EXAMPLE.TEST and user%40example.test now"), "mail and and now")
        self.assertEqual(self.clean("mail \"jd\"@example.test now"), "mail now")
        kept = "bump react@18.2.0 for @handle on user@host at a@b and @example.test and (jd)@example.test"
        self.assertEqual(self.clean(kept), kept)

    def test_clean_removes_the_listed_addresses_with_a_quoted_local_part_of_1_to_62_characters_or_a_bracketed_host_and_keeps_the_listed_neighbours(self):
        for word in ('"auditor"@example.test', "auditor@[127.0.0.1]", "auditor@[IPv6:2001:db8::1]", "AUDITOR@[ipv6:2001:DB8::1]", '"a.b"@[127.0.0.1]', "<auditor@[127.0.0.1]>", "%22auditor%22%40example.test", '"abc\\"@example.test'):
            with self.subTest(word):
                self.assertEqual(self.clean(f"mail {word} now"), "mail now")
        self.assertEqual(self.clean('mail "the auditor"@example.test now'), "mail now")
        long = f'"{"a" * 63}"@example.test'
        self.assertEqual((self.clean(f"mail {long[0]}{long[2:]} now"), self.clean(f"mail {long} now")), ("mail now", f"mail {long} now"))
        kept = "bump react@18.2.0 for @handle on user@host and (jd)@example.test with list[0]@[a] or x@[1] and x@[1.2.3] then x@[1234.1.1.1] and x@[IPv6:] or \"\"@example.test and \"jd\" @example.test now"
        self.assertEqual(self.clean(kept), kept)

    def test_clean_removes_the_listed_addresses_whose_quoted_local_part_of_1_to_62_characters_holds_a_backslash_and_the_character_after_it_as_a_pair_and_keeps_the_listed_neighbours(self):
        words = ('"audit\\""@example.test', '"a\\"b"@example.test', '"a\\"b\\"c"@example.test', '"\\""@example.test', '"a\\\\b"@example.test', '"ab\\\\"@example.test', '"ab\\\\\\""@example.test',
                 '"audit\\""@[127.0.0.1]', '"audit\\""@[IPv6:2001:db8::1]', '"AUDIT\\""@EXAMPLE.TEST', '<"audit\\""@example.test>', "%22audit%5C%22%22%40example.test", "%2522audit%255C%2522%2522%2540example.test",
                 '"a\\%0Ab\\""@example.test')
        for word in words:
            with self.subTest(word):
                self.assertEqual(self.clean(f"mail {word} now"), "mail now")
        self.assertEqual(self.clean('mail "the\\ audit\\" one"@example.test now'), "mail now")
        full, over = (f'"{"a" * count}\\""@example.test' for count in (60, 61))
        self.assertEqual((self.clean(f"mail {full} now"), self.clean(f"mail {over} now")), ("mail now", f"mail {over} now"))
        kept = 'see \\"@example.test and "ab\\\\""@example.test with "a\\"b" @example.test or "a\\"b"@host and \\" @example.test now'
        self.assertEqual(self.clean(kept), kept)

    def test_clean_removes_a_request_name_with_a_hyphen_as_a_whole_token_in_any_case_and_keeps_a_longer_token_the_token_after_a_slash_that_starts_a_word_and_a_name_with_no_hyphen(self):
        clean = MOD["Privacy"](requests={"kit-d7-audit-task", "code-review", "queue", "a-b"}).clean
        self.assertEqual(clean("see kit-d7-audit-task now and KIT-D7-AUDIT-TASK. or x.kit-d7-audit-task, with (code-review) then x_code-review and a-b end"), "see now and or with then and end")
        self.assertEqual(clean("id=kit%2Dd7%2Daudit%2Dtask and &quot;code-review&quot; and src/code-review end"), "and and end")
        kept = "Run /code-review today on the queue for kit-d7-audit-task-2 and prekit-d7-audit-taskpost with x=/code-review and (/code-review) or code review and pre-code-review of a-b-c"
        self.assertEqual(clean(kept), kept)
        self.assertEqual(MOD["Privacy"]().clean("see kit-d7-audit-task now"), "see kit-d7-audit-task now")

    def test_clean_removes_a_hyphenated_request_name_from_the_end_of_a_30000_character_value_of_m_and_spaces(self):
        text = ("m " * 14994) + " code-review"
        self.assertEqual(len(text), 30000)
        self.assertEqual(MOD["Privacy"](requests={"code-review"}).clean(text), ("m " * 14993) + "m")

    def test_clean_removes_a_percent_encoded_hyphenated_request_name_from_the_end_of_a_30000_character_value_of_m_and_spaces(self):
        text = ("m " * 14993) + " code%2Dreview"
        self.assertEqual(len(text), 30000)
        self.assertEqual(MOD["Privacy"](requests={"code-review"}).clean(text), ("m " * 14992) + "m")

    def test_found_yields_the_reviewed_request_name_spans(self):
        privacy = MOD["Privacy"](requests={"code-review"})
        self.assertEqual(privacy.clean("m" * 30000 + "-"), "m" * 30000 + "-")
        cases = (
            ("code-review", [(0, 11)]),
            ("/code-review", []),
            ("src/code-review", [(4, 15)]),
            ("x=/code-review", []),
            ("(/code-review)", []),
            ("pre-code-review", []),
            ("code-review--code-review", [(0, 11), (13, 24)]),
            ("code-review code-review", [(0, 11), (12, 23)]),
            ("code-review-", [(0, 11)]),
            ("-code-review", [(1, 12)]),
            ("/-code-review", [(2, 13)]),
            ("x_code-review", [(2, 13)]),
            ("x//code-review", [(3, 14)]),
            ("//code-review", [(2, 13)]),
            ("m" * 30000 + "-", []),
        )
        for text, spans in cases:
            with self.subTest(text[:24]):
                self.assertEqual(list(privacy.found(text)), spans)

    def test_clean_removes_a_request_name_with_a_hyphen_and_an_underscore_a_dot_or_a_doubled_hyphen_anywhere_in_a_word_when_it_is_4_or_more_characters(self):
        clean = MOD["Privacy"](requests={"odd_name-1", "v1.2-check", "two--parts"}).clean
        self.assertEqual(clean("see odd_name-1 and preODD_NAME-1post and v1.2-check-2 and odd%5Fname%2D1 and xtwo--partsx end"), "see and and and and end")
        self.assertEqual(clean("see odd_name and v1.2 check end"), "see odd_name and v1.2 check end")

    def test_clean_removes_a_request_name_of_fewer_than_4_characters_with_a_hyphen_that_is_not_letters_and_digits_joined_by_single_hyphens_only_where_no_letter_digit_underscore_or_hyphen_is_directly_before_or_after_it(self):
        clean = MOD["Privacy"](requests={"a-_", "A-."}).clean
        for word in ("a-_", "A-_", "(a-_)", '"a-_"', "x=a-_", "a-_,", "a-_.", "a-_:", "/a-_", "src/a-_", "a-_/x", "x.a-_", "a%2D_", "a%2D%5F", "a%252D_", "a-.", "a-..", "(A-.)", "a%2D%2E"):
            with self.subTest(word):
                self.assertEqual(clean(f"see {word} now"), "see now")
        kept = "see xa-_ and a-_b with a-__ or a-_-2 then pre-a-_ and _a-_ for a-.py at 1a-. and a- with a - _ and a_- now"
        self.assertEqual(clean(kept), kept)
        self.assertEqual(MOD["Privacy"]().clean("see a-_ and a-. now"), "see a-_ and a-. now")

    def test_clean_removes_a_request_name_that_is_one_hyphen_two_hyphens_or_a_letter_before_or_after_a_hyphen_only_where_no_letter_digit_underscore_or_hyphen_is_directly_before_or_after_it(self):
        cases = {"-": ("a - b and (-) or -. end", "a b and or end", "a well-known -5 with -- and -v or a- then a-b and _- now"),
                 "--": ("a -- b and (--) end", "a b and end", "a --flag with a--b or --- and - then a-b now"),
                 "x-": ("a x- b and X-. or (x-) end", "a b and or end", "a x-ray with x-- or ax- and x-_ then x - and x now"),
                 "-y": ("a -y b and -Y, or =-y end", "a b and or end", "a x-y with -yes or --y and _-y then - y and y now")}
        for name, (text, left, kept) in cases.items():
            with self.subTest(name):
                clean = MOD["Privacy"](requests={name}).clean
                self.assertEqual((clean(text), clean(kept)), (left, kept))

    def test_clean_reads_the_listed_request_names_without_the_spaces_that_start_and_end_them_and_removes_the_forms_of_fewer_than_4_characters_that_their_percent_escapes_decode_to(self):
        self.assertEqual(MOD["Privacy"](requests={" a-_ "}).clean("see a-_"), "see")
        self.assertEqual(MOD["Privacy"](requests={" code-review "}).clean("run code-review and /code-review"), "run and /code-review")
        clean = MOD["Privacy"](requests={"%61-%5F", "b-%2520"}).clean
        self.assertEqual(clean("see a-_ and %61-%5F or A-_. then b- and b-%20 end"), "see and or then and end")
        self.assertEqual(clean("see xa-_ and b-b end"), "see xa-_ and b-b end")

    def test_clean_removes_an_absolute_path_of_two_or_more_segments_a_home_path_a_drive_path_and_a_file_address_and_keeps_a_relative_path(self):
        gone = ("/pathmarker/notes.md", "/a/b", "/home/someone/x", "(/pathmarker/a)", "x=/pathmarker/a", "a:/pathmarker/a", "/pathmarker//a", "/.config/a", "/~x/a", "/path-marker+1/a", "/pathmarker/a/",
                "~/x", "~/pathmarker", "~user/pathmarker", "$HOME/pathmarker", "${HOME}/pathmarker", "~\\pathmarker",
                "C:\\Users\\private\\file.txt", "c:/Users/private", "C:\\x", "\\\\server\\share", "file:///home/private/secret", "file:/home/private/secret", "FILE:C:/x", "x=file:~/a", "file:secret.txt",
                "file:/secret", "%2Fpathmarker%2Fnotes.md", "%252Fpathmarker%252Fa", "</pathmarker/a>", ">/pathmarker/a")
        for word in gone:
            with self.subTest(word):
                self.assertEqual(self.clean(f"see {word} now"), "see now")
        kept = "see src/a.py and/or docs/guide.md with opencode-go/model at https://example.test/o/r/pull/7 for I/O 24/7 +/- ~ ~5 ~1u/x ${HOME/x $HOME}/x a\\b é:\\x Makefile:12 profile:x file: x 1/2 ./a ../b now"
        self.assertEqual(self.clean(kept), kept)

    def test_clean_keeps_a_one_segment_word_that_starts_with_a_slash_wherever_a_path_could_start(self):
        kept = ("/review", "/loop", "/code-review", "/pathmarker", "/secret", "/tmp", "/review.", "/review,", "(/review)", "x=/review", "key:/review", "</review>", ">/review", "/review/",
                "\"/review\"", "'/review'/'/loop'", "`/loop`", "“/code-review”", "%2Freview", "%252Freview", "/review:", "/REVIEW", "/review_2", "/.hidden", "/~x")
        for word in kept:
            with self.subTest(word):
                self.assertEqual(self.clean(f"run {word} now"), f"run {word} now")
        text = "Run /review then /loop and /code-review, or \"/review\" twice"
        self.assertEqual(self.clean(text), text)
        self.assertEqual(self.clean("/review /loop /a/b /code-review"), "/review /loop /code-review")
        self.assertEqual(self.clean("see /srv/a b/c /loop d/e and /srv/f /g/h /review now"), "see /loop d/e and /review now")

    def test_clean_removes_a_one_segment_path_it_was_given_in_any_case_and_keeps_another_one_segment_word(self):
        paths = {"/storemarker", "/checkoutmarker"}
        self.assertEqual(self.clean("read /storemarker and /CHECKOUTMARKER and x/storemarker/a and %2Fcheckoutmarker then /review and /loop end", paths=paths), "read and and and then /review and /loop end")
        self.assertEqual(self.clean("read /storemarker and /checkoutmarker end"), "read /storemarker and /checkoutmarker end")

    def test_clean_removes_a_path_of_two_or_more_segments_whose_first_starts_with_a_hyphen_a_plus_sign_an_asterisk_an_at_sign_or_a_second_slash_and_keeps_one_segment_two_slashes_alone_and_two_slashes_after_a_colon(self):
        for word in ("/-secret/notes.txt", "/+secret/notes.txt", "//secret/share/notes.txt", "///a/b", "x=//secret/a", "(/*/a)", "/@a/b", "%2F-secret%2Fnotes.txt"):
            with self.subTest(word):
                self.assertEqual(self.clean(f"see {word} now"), "see now")
        kept = "see /review and /-flag with /+1 or // and // note at https://example.test/o/r/pull/7 for ftp://secret/a and a//b then /- / -/ now"
        self.assertEqual(self.clean(kept), kept)

    def test_clean_removes_a_path_it_was_given_of_fewer_than_4_characters_only_as_a_whole_word_that_starts_with_a_slash_where_a_path_can_start_with_or_without_closing_marks_after_it(self):
        for word in ("/x", "/X", "/x.", "/x/", "(/x)", "x=/x", "\"/x\"", "/x/src", "/x/).", "%2Fx", "/x?!"):
            with self.subTest(word):
                self.assertEqual(self.clean(f"in {word} now", paths={"/x"}), "in now")
        kept = "see /x.py and /xy with /x-ray in src/x.py or /review at https://example.test/x/r/pull/7 and https://x/o/r/pull/7 for a/x and x/ then /x(1) now"
        self.assertEqual(self.clean(kept, paths={"/x"}), kept)
        self.assertEqual(self.clean("in /x and /X/ or /x, then /xy now", paths={"/x/"}), "in and or then /xy now")
        self.assertEqual(self.clean("at / and (/) or // then /a and https://example.test/o/r/pull/7 now", paths={"/"}), "at and or then /a and https://example.test/o/r/pull/7 now")
        self.assertEqual(self.clean("in /x. and /x.. or /x now", paths={"/x."}), "in and or /x now")
        self.assertEqual(self.clean("in /x and /xy now"), "in /x and /xy now")

    def test_clean_removes_a_path_after_a_quotation_mark_up_to_the_next_such_mark_and_any_other_through_each_next_word_with_a_slash(self):
        self.assertEqual(self.clean('see "/srv/private dir/secret file.txt" and `~/a b` and \'C:\\a b\\c d\' now'), "see and and now")
        self.assertEqual(self.clean("see “/srv/private dir/secret file.txt” and ‘~/a b’ and x=”/srv/a now"), "see and and now")
        self.assertEqual(self.clean('see "/srv/private dir/secret file.txt and more'), "see file.txt and more")
        self.assertEqual(self.clean("see /srv/private dir/secret file.txt now"), "see file.txt now")
        self.assertEqual(self.clean("see file:/home/private/My Documents/n.txt and file:~/My Documents/n.txt now"), "see and now")
        self.assertEqual(self.clean("see /srv/a then b/c d/e and f/g"), "see then b/c d/e and f/g")

    def test_clean_writes_each_control_character_as_a_space_before_it_looks_and_drops_a_character_utf8_cannot_hold(self):
        ids = {"two_words here"}
        for character in ("\x00", "\x1b", "\x7f", "\x80", "\t", "\n", "\u2028"):
            with self.subTest(repr(character)):
                self.assertEqual(self.clean(f"a two_words{character}here b", ids), "a b")
                self.assertEqual(self.clean(f"a{character}b"), "a b")
        self.assertEqual(self.clean("a two_wo\udc80rds here b mcp\ud800:x", ids), "a b")
        self.assertEqual(self.clean("a tab\there_id b tab here_id c TAB\x00HERE_ID d", {"tab\there_id"}), "a b c d")

    def test_clean_removes_text_that_is_private_only_as_the_document_writes_it(self):
        self.assertEqual(self.clean("a <private_thread9 b", {"lt;private_thread9"}), "a b")
        self.assertEqual(self.clean('a "x b', {"quot;x"}), "a b")
        self.assertEqual(MOD["entities"](self.clean("R&D <b> \"q\" a\\b")), "R&amp;D &lt;b&gt; &quot;q&quot; a&#92;b")

    def test_clean_repeats_until_no_word_holds_private_text_keeps_the_spaces_before_the_first_word_and_gives_an_empty_text_after_8_passes(self):
        ids = {"left_1 right_2"}
        self.assertEqual(self.clean("  D7 worker   mcp:x   running for 5m"), "  D7 worker   running for 5m")
        self.assertEqual(self.clean("  mcp:x worker   node:y"), "  worker")
        self.assertEqual(self.clean("say left_1 /pathmarker/a right_2 end", ids), "say end")
        nested = " ".join(["left_1"] * 7 + ["/pathmarker/a"] + ["right_2"] * 7)
        self.assertEqual(self.clean(f"say {nested} end", ids), "say end")
        deep = " ".join(["left_1"] * 8 + ["/pathmarker/a"] + ["right_2"] * 8)
        self.assertEqual(self.clean(f"say {deep} end", ids), "")
        self.assertEqual(self.clean(""), "")

    def test_no_string_clean_returns_holds_private_text_as_written_or_as_the_document_writes_it(self):
        child = delegated(COORDINATOR, "kit-d7-audit")
        privacy = MOD["Privacy"]({child, COORDINATOR, "sess_Ab12Cd34Ef56", "lt;private_thread9"}, {"/srv/pathmarker state/proj"})
        pieces = ("queue", "D7", child, quote(child, safe=""), COORDINATOR.upper(), "<private_thread9", "/srv/pathmarker", "state/proj/x", "a@b.co", UUID_TEXT, "node:x", "%2Fa%2Fb",
                  "file:x", "\x00", "&", "\"", "~/a", "sess_Ab12Cd34Ef56", "%", "3A", "mcp", ":", "x", "src/a.py", "'", "C:\\a")
        state = 7
        for _ in range(400):
            words = []
            for _ in range(6):
                state = state * 1103515245 + 12345 & 0x7FFFFFFF
                words.append(pieces[state % len(pieces)] + ("" if state & 64 else " "))
            cleaned = privacy.clean("".join(words))
            with self.subTest(cleaned):
                self.assertEqual((privacy.private(cleaned), privacy.clean(cleaned), MOD["UNPRINTED"].search(cleaned)), ([], cleaned, None))

    def test_unescaped_equals_urllib_unquote_and_names_the_source_span_of_each_character(self):
        for text in ("", "plain", "a%20b", "%E2%82%AC5", "%e2%82%ac", "%zz%4", "100%", "%41%42C%44", "%C3%28", "caf%C3%A9 %F0%9D%94%B8", "%2541"):
            with self.subTest(text):
                self.assertEqual(MOD["unescaped"](text)[0], unquote(text))
        self.assertEqual(MOD["unescaped"]("a%41%42c"), ("aABc", [(0, 1), (1, 7), (1, 7), (7, 8)]))

    def test_field_is_the_first_line_with_single_spaces_as_clean_leaves_it(self):
        field = MOD["Privacy"]({"two_words here"}).field
        self.assertEqual(field("  Fix   the queue \n second line"), "Fix the queue")
        self.assertEqual(field("a two_words    here b and /pathmarker/x c"), "a b and c")
        self.assertEqual(field(""), "")

    def test_model_is_the_name_after_one_provider_prefix_cut_to_48_characters_and_other_model_when_nothing_is_left(self):
        model = MOD["Privacy"]({worker(1)}).model
        kept = ("model-a", "gpt-6.1-sol", "claude-sonnet-4-5-20250929", "default", "k3", "a" * 48, "Model-A", "model_a", "GPT 5 (high)", "opencode-go/deepseek-v4.1-flash", "vendor/model-b",
                "queue", "code", "review", "src", "private auditor", "451237", "model-0a1b2c3", "модель")
        self.assertEqual([model(name) for name in kept], list(kept))
        self.assertEqual((model("opencode/muse-spark-1.3-free"), model("claudeAgent/model-b"), model(" model-a\nsecond"), model("a" * 49)), ("muse-spark-1.3-free", "model-b", "model-a", "a" * 47 + "…"))
        other = ("mcp:alpha-secret", "node:alpha-secret", "run:alpha-secret", "user@example.test", "C:\\Users\\private\\file.txt", "file:/home/private/secret", "/pathmarker/model", worker(1), UUID_TEXT, "", " ")
        self.assertEqual([name for name in other if model(name) != "other model"], [])

    def test_link_is_the_https_pull_request_address_without_userinfo_query_or_fragment_a_blank_value_gives_neither_and_any_other_value_is_refused(self):
        link = MOD["Privacy"]({"sess_Ab12Cd34Ef56", worker(1)}).link
        address = "https://git.example/Owner.x/re_po-1/pull/123456"
        for value in (address, "https://user:pw@git.example/Owner.x/re_po-1/pull/123456", address + "?x=mcp:alpha-secret", address + "#task:alpha-secret",
                      "HTTPS://GIT.EXAMPLE/Owner.x/re_po-1/pull/123456", "https://git.example/Owner.x/re_po-1/pull/123456?", f"https://{quote(worker(1), safe='')}@git.example/Owner.x/re_po-1/pull/123456"):
            with self.subTest(value):
                self.assertEqual(link(value), (address, ""))
        for value in ("https://git.example/queue/code/pull/451237", "https://451237.example/review/src/pull/7", "https://git.example/kit-d7-audit/0a1b2c3/pull/" + "7" * 400):
            with self.subTest(value):
                self.assertEqual(link(value), (value, ""))
        self.assertEqual((link(""), link("  ")), (("", ""), ("", "")))
        refused = ("http://git.example/o/r/pull/7", "https://git.example:8443/o/r/pull/7", "https://git.example/o/r/pull/7/files", "https://git.example/o/r/pull/7/", "https://git.example/o/r/pull/",
                   "https://git.example/o/pull/7", "https://git.example/a/o/r/pull/7", "https://git.example/o/r/pulls/7", "https://git.example/o/r/pull/7 x", "https://git.example/o/r/pull/x7",
                   "https:///o/r/pull/7", "https://git_example/o/r/pull/7", "https://[::1]/o/r/pull/7", "https://[x/o/r/pull/7", "https://git.example:port/o/r/pull/7", "git.example/o/r/pull/7",
                   "file:/home/private/secret", "private/file.txt", "https://git.example/o%2Fr/r/pull/7", "https://git.example/../r/pull/7", "https://git.example/o/./pull/7", "https://git.example/o/r/pull/7\\x",
                   f"https://git.example/{worker(1)}/r/pull/7", f"https://git.example/o/{UUID_TEXT}/pull/7", "https://git.example/sess_Ab12Cd34Ef56/r/pull/7", "https://git.example/o/SESS_AB12CD34EF56/pull/7",
                   f"https://{UUID_TEXT}.example/o/r/pull/7")
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
        self.assertEqual(MOD["notes"](folded, MOD["items_of"](folded)), ["3 merged or dropped work items with no agent running, queued, or waiting are each shown as one row."])

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
        self.assertEqual(MOD["notes"](folded, MOD["items_of"](folded)), ["9 sub-agents that are done or stopped are shown as 4 summary rows."])
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
        self.assertEqual(MOD["notes"](folded, MOD["items_of"](folded)), [
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
        self.assertEqual(MOD["notes"](folded, MOD["items_of"](folded)), [
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
        self.assertEqual(MOD["notes"](folded, MOD["items_of"](folded)), [
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
        self.assertIn("'. Bars show turn or delegation intervals and may join across gaps. Striped bars include running intervals. Outlined bars include queued intervals. Faded bars include stopped intervals.'", MOD["RENDERER"])
        self.assertNotIn("time an agent was at work", MOD["RENDERER"])

    def test_cap_everything_lists_8_in_flight_items_with_those_of_a_group_on_the_page_first(self):
        items = [item(f"D{number}") for number in range(1, 13)]
        folded = MOD["cap_everything"](page(group(items[10], row("worker")), items=items))
        self.assertEqual([each.id for each in folded.items], ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D11"])
        self.assertEqual(folded.hidden.cut_in_flight, 4)
        self.assertEqual(MOD["notes"](folded, MOD["items_of"](folded)), ["4 more work items in flight are not listed."])

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
        self.assertEqual((first.rows[0].label, first.rows[0].model, folded.name), ("é" * 8 + "…", "m" * 21 + "…", "𝔸" * 8 + "…"))
        self.assertEqual(folded.items, (first.item, second.item, third.item, fourth.item))
        self.assertEqual([each.no_link for each in folded.items], ["cut", "", "cut", ""])
        self.assertEqual(MOD["notes"](folded, MOD["items_of"](folded)), ["2 pull request links are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."])

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
            data = data_of(MOD["render_html"](folded, CLEAN))
            counted = sum(int(note.split()[0]) for note in data["N"] if "pull request link" in note)
            with self.subTest(fold=index):
                self.assertEqual((len(data["I"]), sum(bool(each[4]) for each in data["I"]) + counted), (len(on_page), len(on_page)))
        self.assertEqual([note for note in MOD["notes"](folded, MOD["items_of"](folded)) if "pull request link" in note], [
            "3 pull request links are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            "3 pull request links are not shown. Each is not an https://host/owner/repository/pull/number address, or it holds text this page removes."])

    def test_notes_say_no_agent_ran_for_an_empty_window_and_count_unknown_statuses_requests_and_other_threads(self):
        self.assertEqual(MOD["notes"](page(), []), ["No agent or sub-agent of this coordinator ran in this window."])
        hidden = MOD["Hidden"](dropped_items=1, dropped_agents=1, cut_agents=1, cut_in_flight=1, other_threads=1, unknown_status=1, by_request_name=1, unstarted=1)
        linked = page(group(item("D1", no_link="cut"), row("worker")), items=[item("D2", no_link="refused"), item("D3", pr=PR7)], hidden=hidden)
        self.assertEqual(MOD["notes"](linked, MOD["items_of"](linked)), [
            "1 merged or dropped work item with 1 agent is not shown. Use a larger --max-bytes to see more.",
            "1 more agent is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            "1 more work item in flight is not listed.",
            "1 pull request link is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            "1 pull request link is not shown. It is not an https://host/owner/repository/pull/number address, or it holds text this page removes.",
            "T3 lists 1 sub-agent with no thread or no start time under a thread this page read. It is not shown.",
            "T3 gave 1 status this tool reads as unknown.",
            "1 agent is grouped by the name of the request that started it.",
            "1 other thread ran in this coordinator's T3 project outside this coordinator."])
        several = MOD["Hidden"](other_threads=3, unknown_status=2, by_request_name=9)
        self.assertEqual(MOD["notes"](page(group(item("D1"), row("worker")), hidden=several), [item("D1")]), [
            "T3 gave 2 statuses this tool reads as unknown.",
            "9 agents are grouped by the name of the request that started them.",
            "3 other threads ran in this coordinator's T3 project outside this coordinator."])


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
  insertBefore(node, ref) {
    const from = this.kids.indexOf(node);
    if (from >= 0) this.kids.splice(from, 1);
    const at = ref ? this.kids.indexOf(ref) : -1;
    if (at < 0) this.kids.push(node); else this.kids.splice(at, 0, node);
    return node;
  },
  querySelector(sel) {
    const name = sel[0] === '.' ? sel.slice(1) : sel;
    const walk = (node) => {
      if ((node.className || '').split(' ').includes(name)) return node;
      for (const kid of node.kids || []) {
        const found = walk(kid);
        if (found) return found;
      }
      return null;
    };
    return walk(this);
  },
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
  one: nodes.filter(node => node.className === 'one').map(node => node.own),
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


class ProjectTest(OutputCase):
    IDLE = "No agent or sub-agent of this coordinator ran in this window."
    NOTE = "1 other thread ran in this coordinator's T3 project outside this coordinator."

    def others(self, fixture):
        fixture.thread("mcp:threadmarker-near", turns=(("completed", 10, 5),))
        fixture.thread("mcp:threadmarker-far", turns=(("completed", 10, 5),), project=ELSEWHERE)
        fixture.thread(delegated("mcp:threadmarker-far", "x"), parent="mcp:threadmarker-far", turns=(("running", 3, None),), project=ELSEWHERE)
        return fixture

    def test_other_thread_in_another_t3_project_and_its_child_are_not_counted_and_one_in_the_coordinators_project_is(self):
        fixture = self.others(self.fixture)
        fixture.coordinator()
        _, t3, _ = fixture.write().read()
        self.assertEqual(t3.other_threads, 1)
        self.assertEqual(data_of(self.document())["N"], [self.IDLE, self.NOTE])
        self.assertEqual(self.out("--text").split("\n")[-4:], ["Notes", "  " + self.IDLE, "  " + self.NOTE, ""])

    def test_project_is_the_previous_threads_when_t3_has_no_row_for_the_current_thread_and_the_current_threads_when_it_has_one(self):
        cases = (("absent", None, self.NOTE),
                 ("present", ELSEWHERE, "2 other threads ran in this coordinator's T3 project outside this coordinator."))
        for name, project, note in cases:
            with self.subTest(name):
                fixture = self.fixture = self.others(Fixture(self.root / name))
                fixture.meta.update(thread="mcp:threadmarker-current", previousThread=COORDINATOR)
                fixture.coordinator()
                if project:
                    fixture.thread("mcp:threadmarker-current", project=project)
                fixture.write()
                self.assertEqual(data_of(self.document())["N"], [self.IDLE, note])
                self.assertEqual(self.out("--text").split("\n")[-4:], ["Notes", "  " + self.IDLE, "  " + note, ""])

    def test_store_with_no_recorded_coordinator_thread_counts_no_other_thread(self):
        fixture = self.others(self.fixture)
        fixture.meta.pop("thread")
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), turns=(("completed", 20, 10),))
        _, t3, _ = fixture.write().read()
        self.assertEqual(t3.other_threads, 0)
        self.assertEqual(data_of(self.document())["N"], [])
        self.assertNotIn("Notes", self.out("--text").split("\n"))

    def test_database_with_no_project_id_column_exits_3(self):
        self.fixture.write(schema=dict(SCHEMA, orchestration_v2_projection_threads=("thread_id", "title", "default_provider", "payload_json")))
        self.assertEqual(self.fails(3), f"activity: T3's table orchestration_v2_projection_threads has no column project_id; {CHANGED}")

    def test_project_id_is_in_no_string_of_what_read_t3_returns_and_in_neither_output_form(self):
        fixture = self.others(self.fixture)
        fixture.coordinator()
        _, t3, _ = fixture.write().read()
        self.assertEqual(t3.other_threads, 1)
        self.assertIn("threadmarker-near", repr(t3))
        self.assertNotIn("projectmarker", repr(t3))
        for form in ((), ("--text",)):
            self.assertNotIn("projectmarker", self.out(*form))


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
        self.assertEqual(data["N"], ["1 agent is grouped by the name of the request that started it.", "1 other thread ran in this coordinator's T3 project outside this coordinator."])

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
                         (written.strip(), written.strip(), written.strip(), ["model-a", "&lt;b&gt;&#92;&quot;model"]))

    def test_data_element_of_a_page_whose_strings_hold_markup_a_quote_a_backslash_a_tab_and_u2028_holds_no_backslash_or_angle_bracket(self):
        raw = DATA.search(MOD["render_html"](hostile(), same)).group(1)
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
        self.assertEqual(MOD["encode"]({"<": ["</script>\t", 1, None, {"b": "\"\\"}]}, same), '{"<":["&lt;/script&gt; ",1,null,{"b":"&quot;&#92;"}]}')

    def test_encode_writes_a_string_in_a_tuple_as_entities_writes_it(self):
        self.assertEqual(MOD["encode"]({"a": ("<",)}, same), '{"a":["&lt;"]}')

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

    def test_narrow_rule_puts_each_rows_model_on_a_second_line_of_a_30_pixel_row_and_hides_only_every_other_axis_label_and_the_wide_rule_gives_the_model_up_to_half_the_label_column(self):
        narrow = dict(re.findall(r"([^{}]+)\{([^{}]*)\}", re.search(r"@media\(max-width:520px\)\{(.*)\}$", MOD["STYLE"]).group(1)))
        self.assertEqual([selector for selector, body in narrow.items() if "display:none" in body], [".axis i:nth-child(odd)"])
        self.assertEqual(narrow[".row"], "position:relative;height:30px;align-items:start")
        self.assertEqual(narrow[".l small"], "position:absolute;left:0;right:0;bottom:0;max-width:none;line-height:13px")
        self.assertEqual((narrow[".d1 small"], narrow[".d2 small"]), ("left:14px", "left:28px"))
        wide = MOD["STYLE"].split("@media")[0]
        self.assertEqual(re.findall(r"[^{}]*small[^{}]*\{[^{}]*\}", wide), ["h2,small,.stat span,.legend,.axis,.sum,.foot,.co,.chip{color:var(--muted-foreground)}",
                                                                              ".sum,.l span,.l small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
                                                                              ".l small{flex:none;max-width:50%}", "small{font-size:10.5px}"])
        self.assertIn(".now div,.strip>*,.grp,.l{display:flex;gap:8px;align-items:baseline;min-width:0}", wide)
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


def tagged(text):
    return f"[{text}]"


def named(fixture):
    """A page whose every string from the store or from T3 holds one word in capitals that no other string of it holds."""
    fixture.meta["restaurant"] = "NAMEWORD kit"
    fixture.coordinator(model="coordinator-MODELWORD")
    fixture.unit("IDWORD", "in-progress", "SUMWORD of the page", thread=worker(1), pr="https://git.example/OWNERWORD/r/pull/7")
    fixture.unit("QUIETWORD", "queued", "IDLEWORD summary", pr="https://git.example/o/REPOWORD/pull/6")
    fixture.thread(worker(1), title="D7 worker", model="worker-FIRSTWORD", turns=(("running", 40, None),))
    child = delegated(worker(1), "helper-task")
    fixture.thread(child, title="PARENTWORD helper", model="child-SECONDWORD", parent=worker(1), turns=(("running", 30, None),))
    fixture.thread(native(1), title="LABELWORD reader", model="leaf-THIRDWORD", parent=child, delegation=("queued", 20, None))
    fixture.thread(delegated(COORDINATOR, "loose-task"), title="LOOSEWORD agent", model="loose-FOURTHWORD", parent=COORDINATOR, turns=(("failed", 25, 24),))
    return fixture


WORDS = ("NAMEWORD", "MODELWORD", "IDWORD", "SUMWORD", "OWNERWORD", "QUIETWORD", "IDLEWORD", "REPOWORD", "FIRSTWORD", "PARENTWORD", "SECONDWORD", "LABELWORD", "THIRDWORD", "LOOSEWORD", "FOURTHWORD")


class EmitTest(OutputCase):
    """Every string a form emits is one the filter returned. These tests fail when a renderer emits a string another way."""

    def pages(self):
        for build in (empty, named, failed_child, queued_only, busy):
            unfolded = page_of(build(Fixture(self.root / build.__name__)))
            yield build.__name__, unfolded
            yield build.__name__ + " capped", MOD["cap_everything"](unfolded)
            yield build.__name__ + " shortened", MOD["shortened"](unfolded)

    def test_every_string_of_the_data_element_is_one_clean_returned_and_the_document_outside_it_and_the_checksum_is_the_same_for_every_page(self):
        shells = set()
        for name, drawn_page in self.pages():
            with self.subTest(name):
                document = MOD["render_html"](drawn_page, tagged)
                raw = DATA.search(document).group(1)
                emitted, given = strings(list(json.loads(raw).values())), strings(list(MOD["wire"](drawn_page).values()))
                self.assertEqual(emitted, [MOD["entities"](tagged(text)) for text in given])
                self.assertEqual(document.count(raw), 1)
                shells.add(re.sub(r"const H=[0-9]+;", "const H=0;", document.replace(raw, "")))
        self.assertEqual(len(shells), 1)
        shell = shells.pop()
        self.assertEqual([word for word in WORDS if word in shell], [])

    def test_every_plain_line_is_one_clean_returned_whole(self):
        for name, drawn_page in self.pages():
            with self.subTest(name):
                lines = MOD["render_text"](drawn_page, same).split("\n")
                self.assertEqual(MOD["render_text"](drawn_page, tagged).split("\n"), [tagged(line) for line in lines])
                self.assertEqual((lines[0][:19], len(lines) > 2), ("Agent activity for ", True))

    def test_each_word_of_the_named_page_is_in_the_document_and_each_but_the_coordinators_model_is_in_the_text(self):
        fixture = named(self.fixture).write()
        document, text = self.document(), self.out("--text")
        self.assertEqual(([word for word in WORDS if word not in document], [word for word in WORDS if word not in text]), ([], ["MODELWORD"]))

    def test_run_gives_both_forms_the_filter_of_the_run(self):
        """ODD, and the store's directory straight after a letter, are private text only to a filter that knows what this run read."""
        fixture = named(self.fixture)
        fixture.retired("IDWORD", ODD)
        fixture.units[0]["summary"] = f"SUMWORD of {ODD} and x{fixture.store} today"
        fixture.write()
        self.assertEqual(CLEAN(fixture.units[0]["summary"]), fixture.units[0]["summary"])
        self.assertEqual(data_of(self.document())["I"][0][1], "SUMWORD of and today")
        self.assertIn("   working   SUMWORD of and today   ", self.out("--text"))

    @unittest.skipUnless(shutil.which("node"), "node is not on PATH")
    def test_renderer_draws_each_string_that_holds_a_word_of_the_named_page_alone_in_a_text_or_an_attribute(self):
        fixture = named(self.fixture).write()
        document = self.document()
        built = RendererTest.render(self, document)
        drawn_strings = [*built["texts"], *built["titles"], *(link[1] for link in built["links"])]
        data = set(strings(list(data_of(document).values())))
        self.assertEqual([text for text in drawn_strings if any(word in text for word in WORDS) and text not in data], [])
        self.assertEqual([word for word in WORDS if not any(word in text for text in drawn_strings)], [])
        self.assertEqual(built["texts"][built["texts"].index("Running now") + 1:built["texts"].index("Timeline")], [
            "IDWORD", "worker", "worker-FIRSTWORD", "40m", "IDWORD", "PARENTWORD helper", "child-SECONDWORD", " · under ", "worker", "30m",
            "Queued", "IDWORD", "LABELWORD reader", "leaf-THIRDWORD", " · under ", "PARENTWORD helper", "20m"])


class SizeTest(OutputCase):
    def test_empty_window_is_at_most_the_fixed_budget_and_says_no_agent_ran(self):
        empty(self.fixture).write()
        document = self.document()
        data = data_of(document)
        self.assertLessEqual(len(document.encode()), MOD["FIXED_BUDGET"])
        self.assertEqual((data["n"], data["G"], data["k"], data["N"]), ([0, 0, 0, 0], [], None, ["No agent or sub-agent of this coordinator ran in this window."]))
        self.assertEqual([item[0] for item in data["I"]], ["D7"])

    def test_typical_window_shows_7_finished_work_items_as_one_row_each_and_is_at_most_16000_bytes(self):
        typical(self.fixture).write()
        document = self.document()
        data = data_of(document)
        self.assertLess(len(document.encode()), 16000)
        self.assertEqual((data["n"], agents_shown(data), sum(len(lines) for _, lines in data["G"])), ([3, 9, 41, 1], 50, 17))
        self.assertEqual(data["N"], ["7 merged or dropped work items with no agent running, queued, or waiting are each shown as one row.",
                                     "14 agents are grouped by the name of the request that started them."])

    def test_busy_window_is_folded_to_at_most_16000_bytes_with_a_note_and_max_bytes_buys_every_row_back(self):
        busy(self.fixture).write()
        document = self.document()
        data = data_of(document)
        self.assertLess(len(document.encode()), 16000)
        self.assertEqual(data["n"], [5, 36, 364, 3])
        self.assertLess(sum(len(lines) for _, lines in data["G"]), 400)
        self.assertIn("27 merged or dropped work items with 301 agents are not shown. Use a larger --max-bytes to see more.", data["N"])
        whole = data_of(self.document("--max-bytes", "500000"))
        self.assertEqual((whole["n"], agents_shown(whole), sum(len(lines) for _, lines in whole["G"])), ([5, 36, 364, 3], 400, 400))
        self.assertEqual(whole["N"], ["112 agents are grouped by the name of the request that started them."])

    def test_window_shaped_like_a_measured_day_is_over_12000_and_at_most_16000_bytes_and_the_last_fold_is_not_applied(self):
        unfolded = page_of(day(self.fixture))
        self.assertEqual((unfolded.totals, len(unfolded.items), sum(each.depth == 0 for one in unfolded.groups for each in one.rows)), (MOD["Totals"](8, 36, 364, 32), 6, 148))
        fitted, document = MOD["fit"](unfolded, 15999, CLEAN)
        self.assertGreater(len(document.encode()), 12000)
        self.assertLess(len(document.encode()), 16000)
        self.assertLess(fitted.fold, len(MOD["FOLDS"]))
        self.assertEqual(agents_shown(data_of(document)) + fitted.hidden.dropped_agents, 400)
        framed, _ = MOD["fit"](unfolded, 15999, CLEAN, 1960)
        printed = self.document()
        self.assertLess(len(printed.encode()), 16000)
        self.assertEqual(sum(len(lines) for _, lines in data_of(printed)["G"]), sum(len(one.rows) for one in framed.groups))

    def test_extreme_window_with_four_byte_characters_and_long_links_is_at_most_16000_bytes(self):
        extreme(self.fixture).write()
        document = self.document()
        data = data_of(document)
        self.assertLess(len(document.encode()), 16000)
        self.assertEqual((data["n"], len(data["G"]), len([item for item in data["I"] if item[5]])), ([60, 200, 2800, 10], 4, 8))
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
        counts = ("dropped_items", "dropped_agents", "cut_agents", "cut_lines", "cut_in_flight", "other_threads", "unknown_status", "by_request_name", "unstarted")
        hidden = MOD["Hidden"](**dict.fromkeys(counts, 9999999))
        legend = tuple((name, 9999999) for name in ("Claude", "Codex", "Cursor", "Grok", "OpenCode", "Other"))
        sizes = {}
        for status in ("running", "queued"):
            for character in ("s", "<", '"', "𝕏"):
                wide = character * 400

                def big(number, no_link=""):
                    return item(str(number) + wide, summary=str(number) + wide, pr="https://example.test/" + "x" * 69, no_link=no_link)

                def rows(prefix):
                    return [replace(row(prefix + str(n) + wide, depth=min(n, 2), status=status, spans=spans, open_seconds=604800, stands_for=99999, model=f"{prefix}.{n}" + wide),
                                    seconds=604800) for n in range(40)]

                groups = [group(big(number, "refused" if number % 2 else "cut"), *rows(str(number))) for number in range(40)]
                unfolded = page(*groups, items=[big(number) for number in range(100, 140)], coordinator=row("coordinator", spans=spans, model=wide), hidden=hidden, name=wide)
                totals = MOD["Totals"](9999999, 9999999, 9999999, 9999999)
                largest = MOD["cap_everything"](replace(unfolded, window=MOD["Window"](1790000000.0, 1790604799.0), totals=totals, legend=legend))
                self.assertEqual((len(largest.groups), [len(one.rows) for one in largest.groups], len(largest.items), len(MOD["notes"](largest, MOD["items_of"](largest)))), (6, [6] * 6, 8, 12))
                self.assertEqual((MOD["wire"](largest)["w"][2], len(MOD["wire"](largest)["M"])), ("167h 59m 59s", 37))
                sizes[status, character] = len(MOD["render_html"](largest, same).encode())
        self.assertEqual({case: size for case, size in sizes.items() if size + len("\n") > 16000}, {})


class FrameTest(OutputCase):
    def test_height_of_a_page_with_one_group_two_rows_one_of_them_running_one_chip_and_one_note_is_539(self):
        only = item("D1")
        built = page(group(only, row("worker", status="running", open_seconds=5), row("helper", depth=1)), items=[only], hidden=MOD["Hidden"](by_request_name=1))
        self.assertEqual(MOD["notes"](built, [only]), ["1 agent is grouped by the name of the request that started it."])
        self.assertEqual(MOD["height"](built), 539)

    def test_frame_budget_is_1960_and_the_busy_page_at_the_default_max_bytes_is_at_most_that_tall_and_its_notes_count_the_rows_it_leaves_out(self):
        self.assertEqual(MOD["FRAME_HEIGHT"] - MOD["FRAME_MARGIN"], 1960)
        fixture = busy(self.fixture)
        fitted, _ = MOD["fit"](page_of(fixture), 15999, CLEAN, 1960)
        self.assertEqual((MOD["height"](fitted), sum(len(one.rows) for one in fitted.groups)), (1950, 20))
        data = data_of(self.document())
        self.assertEqual((sum(len(lines) for _, lines in data["G"]), len([each for each in data["I"] if each[5]])), (20, 8))
        self.assertEqual(data["N"], [
            "28 sub-agents that are done or stopped are shown as 4 summary rows.",
            "27 merged or dropped work items with 301 agents are not shown. Use a larger --max-bytes to see more.",
            "55 more agents are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            "1 more work item in flight is not listed.",
            "112 agents are grouped by the name of the request that started them."])

    def test_fit_with_the_frame_keeps_each_fixture_page_at_most_1960_tall_and_16000_bytes_with_its_totals_its_legend_and_every_agent_shown_or_counted(self):
        for build, agents, tall in ((typical, 50, 1472), (busy, 400, 1950), (day, 400, 1925), (extreme, 3000, 1882)):
            with self.subTest(build.__name__):
                unfolded = page_of(build(Fixture(self.root / build.__name__)))
                fitted, document = MOD["fit"](unfolded, 15999, CLEAN, 1960)
                self.assertEqual(MOD["height"](fitted), tall)
                self.assertLess(len(document.encode()), 16000)
                self.assertEqual((fitted.totals, fitted.legend), (unfolded.totals, unfolded.legend))
                self.assertEqual(agents_shown(data_of(document)) + fitted.hidden.dropped_agents + fitted.hidden.cut_agents, agents)

    def test_framed_keeps_the_running_row_then_the_failed_rows_then_each_other_row_that_fits_what_is_left_and_counts_what_it_cuts(self):
        first, merged = item("A"), item("B", in_flight=False)
        built = page(group(first, row("a1"), row("a2", depth=1, status="running", open_seconds=5), row("a3", status="failed")),
                     group(merged, row("b1"), row("b2")), group(None, row("c1", status="failed"), row("c2")))
        self.assertEqual(MOD["height"](built), 667)
        self.assertIs(MOD["framed"](built, 667), built)
        cut = MOD["framed"](built, 623)
        self.assertEqual(drawn(cut), {"A": [(0, "a2", 1), (0, "a3", 1)], None: [(0, "c1", 1), (0, "c2", 1)]})
        self.assertEqual(cut.hidden, MOD["Hidden"](dropped_items=1, dropped_agents=2, cut_agents=1))
        self.assertEqual((MOD["height"](cut), cut.fold), (607, 0))

    def test_the_frame_keeps_room_for_each_strip_group(self):
        goal = MOD["Goal"]("G1", "Ship")
        chip = MOD["Item"]("D1", "one", "working", "go", "", True, "", 1, "feature", "G1")
        built = page(group(None, *(row(f"worker {number}") for number in range(4))), items=(chip,),
                     hidden=MOD["Hidden"](dropped_items=1, dropped_agents=1, cut_agents=1, cut_in_flight=1))
        built = replace(built, goals=(goal,), flight=MOD["flight_of"]((chip,), (goal,)))
        limit = MOD["height"](built) - 1
        cut = MOD["framed"](built, limit)
        self.assertLessEqual(MOD["height"](cut), limit)
        self.assertLess(sum(len(one.rows) for one in cut.groups), 4)

    def test_height_counts_wrapped_count_lines_and_gaps_for_eight_wide_groups(self):
        wide = "W" * 36
        wide_goals = tuple(MOD["Goal"](f"G{number}", wide) for number in range(8))
        short_goals = tuple(MOD["Goal"](f"G{number}", f"Ship {number}") for number in range(8))

        def built(goals):
            items = tuple(MOD["Item"](f"D{number}", "summary", "working", "go", "", True, "", 0, "feature", f"G{number}") for number in range(8))
            groups = tuple(group(items[number], row(f"w{number}a"), row(f"w{number}b", depth=1)) for number in range(8))
            made = page(*groups, items=items, coordinator=row("coordinator"))
            return replace(made, goals=goals, flight=MOD["flight_of"](items, goals))

        short_page, wide_page = built(short_goals), built(wide_goals)
        self.assertEqual((MOD["height"](short_page), MOD["height"](wide_page)), (1886, 2038))
        fitted, _ = MOD["fit"](wide_page, 15999, CLEAN, 1960)
        self.assertLessEqual(MOD["height"](fitted), 1960)
        self.assertLess(sum(len(one.rows) for one in fitted.groups), 16)
        kept, _ = MOD["fit"](short_page, 15999, CLEAN, 1960)
        self.assertEqual(sum(len(one.rows) for one in kept.groups), 16)

    def test_max_bytes_16001_prints_the_77_rows_the_byte_limit_alone_leaves_and_a_page_under_the_frame_is_the_same_with_and_without_it(self):
        busy(self.fixture).write()
        self.assertEqual(sum(len(lines) for _, lines in data_of(self.document("--max-bytes", "16001"))["G"]), 77)
        short = page_of(one_running(Fixture(self.root / "short")))
        self.assertEqual(MOD["fit"](short, 15999, CLEAN, 1960), MOD["fit"](short, 15999, CLEAN))


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
            "  1 other thread ran in this coordinator's T3 project outside this coordinator.",
            ""]))

    def test_text_and_document_hold_the_same_four_counts(self):
        busy(self.fixture).write()
        self.assertEqual(data_of(self.document())["n"], [5, 36, 364, 3])
        self.assertEqual(self.out("--text").split("\n")[1], "5 running now, 36 agents, 364 sub-agents, 3 failed")

    def test_text_cuts_the_running_work_item_and_failed_lists_and_counts_the_rest(self):
        lines = self.render(busy).split("\n")
        self.assertEqual((lines[2], lines[8]), ("Running now", "Work items"))
        self.assertEqual(len(lines[3:8]), 5)
        self.assertEqual((len(lines[9:18]), lines[18]), (9, "  and 27 more work items"))
        self.assertEqual((lines[19], len(lines[20:23]), lines[23]), ("Failed", 3, "Notes"))
        cut = MOD["render_text"](page(group(item("D1"), *[row(f"r{n}", status="running", open_seconds=5) for n in range(9)],
                                              *[row(f"f{n}", status="failed") for n in range(5)])), CLEAN).split("\n")
        self.assertEqual((cut[10], cut[18]), ("  and 2 more running agents", "  and 1 more failed agent"))

    def test_ten_work_items_whose_shortened_ids_summaries_states_and_links_are_the_same_are_each_listed_or_counted_in_the_text_at_max_bytes_500000_and_16000(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.meta["restaurant"] = "name " * 10000
        for number in range(10):
            fixture.unit(f"D12345678901234567890-{number}", summary="same", thread=worker(number))
            fixture.thread(worker(number), turns=(("running", 10, None),))
        fixture.write()
        whole = [f"  D12345678901234567890-{number}   working   same   1 agent, 10m at work" for number in range(9)]
        for budget, listed in (("500000", whole), ("16000", ["  D12345678…   working   same   1 agent, 10m at work"] * 9)):
            with self.subTest(budget):
                lines = self.out("--text", "--max-bytes", budget).split("\n")
                at = lines.index("Work items")
                self.assertEqual((lines[1], lines[at + 1:at + 12]), ("10 running now, 10 agents, 0 sub-agents, 0 failed", [*listed, "  and 1 more work item", ""]))

    def test_ten_work_items_whose_shortened_ids_summaries_states_and_links_are_the_same_each_have_their_own_entry_or_are_counted_in_the_document_at_max_bytes_16000(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.meta["restaurant"] = "name " * 10000
        for number in range(10):
            fixture.unit(f"D12345678901234567890-{number}", summary="same", thread=worker(number))
            fixture.thread(worker(number), turns=(("running", 10, None),))
        fixture.write()
        data = data_of(self.document("--max-bytes", "16000"))
        self.assertEqual((data["I"], [group[0] for group in data["G"]], data["N"][1]), (
            [["D12345678…", "same", "working", "go", "", 1]] * 8, [0, 1, 2, 3, 4, 5], "2 more work items in flight are not listed."))

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
        for text in ("2", "4", "1", "running now", "agents, last 3h", "sub-agents", "failed", "Running now", "model-a", "42m",
                     "6m", "Timeline", "Claude 3", "Codex 2", "Grok 1", "coordinator", "worker",
                     "architect runner 2", "spec_review", "review", "model-c", "why investigator", "Not tied to a work item", "Work items in flight",
                     "D7", "Agent activity page", "in review", "D6", "Queue fix", "landing", "D5", "Old work", "merged",
                     "1 agent is grouped by the name of the request that started it.", "1 other thread ran in this coordinator's T3 project outside this coordinator."):
            self.assertIn(text, texts)
        self.assertNotIn("This copy differs from what the tool wrote. Run the command again.", texts)
        self.assertEqual(texts[texts.index("Running now") + 1:texts.index("Timeline")],
                         ["D7", "worker", "model-a", "42m", "D7", "architect runner 2", "model-b", " · under ", "worker", "6m"])
        self.assertRegex(texts[-5], r"^As of \d+:\d\d [AP]M for $")
        self.assertEqual(texts[-4:-2], ["kit", ". Bars show turn or delegation intervals and may join across gaps. Striped bars include running intervals. Outlined bars include queued intervals. Faded bars include stopped intervals."])
        self.assertEqual(built["links"], [["A", PR7, "_blank", "noopener"], ["A", PR6, "_blank", "noopener"], ["A", PR7, "_blank", "noopener"]])
        classes = built["classes"]
        self.assertEqual([classes.count(name) for name in ("stat live", "stat alarm", "mark pulse", "row co", "row p1", "row p2", "row p3", "grp", "b run", "b f", "b stop")],
                         [1, 1, 2, 1, 3, 2, 1, 3, 2, 1, 1])
        self.assertEqual(len(built["bars"]), sum(len(line[6]) // 3 for _, lines in data_of(document)["G"] for line in lines) + 2)
        self.assertEqual([bar for bar in built["bars"] if not re.fullmatch(r"min\([0-9]+(\.[0-9])?%,100% - 3px\) [0-9]+(\.[0-9])?%", " ".join(bar))], [])
        self.assertEqual(built["titles"], ["running · 15m", "coordinator", "model-c", "running · 63m", "worker", "model-a", "running · 6m", "architect runner 2", "model-b",
                                           "done · 60s", "spec_review", "model-b", "failed · 3m", "review", "model-c", "done · 5m", "worker", "model-a", "stopped · 2m", "why investigator", "model-a"])

    def test_page_that_render_html_writes_is_drawn_with_markup_a_quote_a_backslash_and_text_that_reads_as_an_entity_as_that_text_and_a_tab_and_u2028_as_one_space(self):
        built = self.render(MOD["render_html"](hostile(), same))
        self.assertNotIn("This copy differs from what the tool wrote. Run the command again.", built["texts"])
        self.assertEqual([text for text in built["texts"] if "onerror" in text or "&" in text],
                         ["D1 " + HOSTILE_DRAWN, HOSTILE_DRAWN, HOSTILE_DRAWN, "model " + HOSTILE_DRAWN, HOSTILE_DRAWN])
        self.assertEqual(built["titles"], ["done · 60s", HOSTILE_DRAWN, "model " + HOSTILE_DRAWN])

    def test_item_block_lists_ledger_rows_in_attempt_order_with_role_seat_and_open_or_ended(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "verifier", "thread": worker(3), "seat": "grok/grok-4", "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": worker(1), "seat": "", "start": stamp, "end": ""},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "fix worker", "thread": worker(2), "seat": "", "start": stamp, "end": ""},
        ])
        phrases = [
            "attempt 1 · worker · open",
            "attempt 2 · verifier · grok/grok-4 · ended",
            "attempt 2 · fix worker · open",
        ]
        document = self.document()
        texts = self.render(document)["texts"]
        self.assertNotIn("This copy differs from what the tool wrote. Run the command again.", texts)
        self.assertEqual([text for text in texts if text.startswith("attempt ")], phrases)
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1:at + 4], ["    " + phrase for phrase in phrases])
        for ident in (worker(1), worker(2), worker(3)):
            self.assertNotIn(ident, document)
            self.assertNotIn(ident, "\n".join(lines))

    def test_worker_with_an_end_stamp_says_ended_and_a_verifier_with_an_empty_end_says_open(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": worker(1), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "verifier", "thread": worker(2), "start": "", "end": ""},
        ])
        phrases = ["attempt 1 · worker · ended", "attempt 2 · verifier · open"]
        texts = self.render(self.document())["texts"]
        self.assertEqual([text for text in texts if text.startswith("attempt ")], phrases)
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1:at + 3], ["    " + phrase for phrase in phrases])

    def test_hostile_seat_is_escaped_in_the_document_and_drawn_as_text(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        # A tab would split this cell. The seat keeps HOSTILE with that tab written as a space.
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "verifier", "thread": worker(2),
             "seat": HOSTILE.replace("\t", " "), "start": stamp, "end": stamp},
        ])
        document = self.document()
        raw = DATA.search(document).group(1)
        self.assertIn("attempt 1 · verifier · x&lt;/script&gt;&lt;img onerror=a… · ended", raw)
        self.assertNotIn("</script><img", raw)
        self.assertEqual([character for character in "<>" if character in raw], [])
        drawn = [text for text in self.render(document)["texts"] if text.startswith("attempt ")]
        self.assertEqual(drawn, ["attempt 1 · verifier · x</script><img onerror=a… · ended"])
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1], "    attempt 1 · verifier · x</script><img onerror=a… · ended")

    def test_renderer_lists_a_queued_agent_under_queued_with_a_plain_mark_and_an_outlined_bar_and_draws_no_running_list(self):
        fixture = queued_only(self.fixture)
        fixture.thread(native(1), title="/root/spec_review", parent=worker(1), delegation=("queued", 2, None))
        fixture.write()
        built = self.render(self.document())
        texts, classes = built["texts"], built["classes"]
        self.assertEqual([text for text in texts if text in ("Running now", "Queued", "Timeline")], ["Queued", "Timeline"])
        self.assertEqual(texts[texts.index("Queued") + 1:texts.index("Timeline")], ["D7", "worker", "model-a", "5m", "D7", "spec_review", "model-a", " · under ", "worker", "2m"])
        self.assertEqual([classes.count(name) for name in ("mark", "mark pulse", "b q", "b run", "stat live")], [2, 0, 2, 0, 1])
        self.assertEqual((texts[0], built["titles"][3:]), ("0", ["queued · 0s", "worker", "model-a", "queued · 0s", "spec_review", "model-a"]))

    def test_renderer_puts_a_bar_that_starts_at_the_windows_end_3_pixels_inside_its_track(self):
        fixture = one_running(self.fixture)
        fixture.thread(delegated(worker(1), "helper-task"), title="helper", parent=worker(1), turns=(("running", 0.05, None),))
        fixture.write()
        built = self.render(self.document())
        self.assertEqual(built["bars"][-1], ["min(99.9%,100% - 3px)", "0.1%"])
        self.assertIn(".b{position:absolute;top:4px;height:11px;min-width:3px;", MOD["STYLE"])

    def test_renderer_draws_a_flag_in_a_note_in_its_own_element_with_the_class_that_cannot_wrap(self):
        note = "2 more agents are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."
        document = MOD["render_html"](page(group(item("D1"), row("worker")), hidden=MOD["Hidden"](cut_agents=2)), same)
        self.assertEqual(data_of(document)["N"], [note])
        built = self.render(document)
        self.assertEqual(built["texts"][-3:], ["2 more agents are not shown, because the page is at its size limit. Use a larger ", "--max-bytes", " to see more."])
        self.assertEqual(built["classes"][-4:], ["", "", "nb", ""])
        self.assertIn(".nb,.one{white-space:nowrap}", MOD["STYLE"])

    def test_renderer_draws_the_item_and_label_of_an_open_line_the_id_of_a_group_heading_and_the_id_of_a_chip_with_the_one_line_class(self):
        failed_child(self.fixture).write()
        self.assertEqual(self.render(self.document())["one"], ["D7", "worker", "D7", "architect runner 2", "D7", "D5", "D6", "D7"])
        self.assertIn(".nb,.one{white-space:nowrap}.one{overflow:hidden;text-overflow:ellipsis}", MOD["STYLE"])

    def test_renderer_says_the_copy_differs_when_one_character_of_the_data_changed(self):
        one_running(self.fixture).write()
        texts = self.render(self.document(), spoil=True)["texts"]
        self.assertEqual(texts[0], "This copy differs from what the tool wrote. Run the command again.")
        self.assertEqual(texts[texts.index("Running now") + 1:texts.index("Timeline")], ["D7", "worker", "model-a", "42m"])

    def test_renderer_draws_a_folded_page_and_an_empty_one_with_no_error(self):
        for build, heading in ((busy, "Running now"), (empty, "Timeline")):
            with self.subTest(build.__name__):
                fixture = self.fixture = Fixture(self.fixture.root / build.__name__)
                build(fixture).write()
                self.assertIn(heading, self.render(self.document())["texts"])

def skill_section():
    return ("\n" + VIEWS.read_text()).split("\n## Agent activity\n", 1)[1].split("\n## ", 1)[0]


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


REQUEST = "kit-d7-audit-task"
ODD = "T3homemarker_Session9"
BARE = "7ead0a2b-e89b-12d3-a456-426614174000"
ADDRESS = "acct-homemarker@example.test"
NEEDLES = (*MARKERS, "426614174000", "example.test", "secret")
ROW_FIELDS = ("model", "title", "title in a sentence")
LINK_FIELDS = ("pull request", "pull request owner", "pull request query", "pull request fragment", "pull request userinfo")


def tree(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(Path(directory).rglob("*")) if path.is_file()}


def put(rows, index, column, value):
    rows[index] = (*rows[index][:column], value, *rows[index][column + 1:])


def forms(value):
    """value as written, in other cases, and percent-encoded once, twice, and in part."""
    once = quote(value, safe="")
    return tuple(dict.fromkeys((value, value.upper(), value.lower(), value.swapcase(), once, quote(once, safe=""), once.lower(), quote(value), value.replace("-", "%2D").replace("_", "%5f"))))


class PrivacyTest(OutputCase):
    """The contract: no private text, as the script's docstring defines it, is in either form, and a request name of two or more parts reaches a label only as separate words."""

    def staffed(self, name):
        fixture = self.fixture = Fixture(self.root / name)
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Activity page", thread=worker(1), pr="https://git.example/o/r/pull/7")
        fixture.retired("D7", BARE)
        fixture.thread(worker(1), title="D7 worker", turns=(("running", 30, None),))
        fixture.thread(BARE, title="D7 first worker", turns=(("completed", 60, 50),))
        self.child = delegated(worker(1), REQUEST)
        self.node = fixture.thread(self.child, title="Act as the audit sub-agent for this task.", provider="codex", parent=worker(1), turns=(("completed", 25, 20),))
        self.leaf = fixture.thread(native(1), title="/root/spec_review", parent=self.child, delegation=("completed", 24, 23))
        fixture.unit("D8", "in-review", "Second item", thread=ODD)
        fixture.thread(ODD, title="D8 worker", turns=(("completed", 45, 40),))
        return fixture

    def identifiers(self):
        return (COORDINATOR, worker(1), BARE, ODD, self.child, self.node, native(1), self.leaf, unquote(self.child))

    def paths(self, fixture):
        return (str(fixture.store), str(fixture.home), str(fixture.base), str(fixture.database), fixture.meta["projectRoot"], str(fixture.store / "dishes.tsv"), str(fixture.home / ".config" / "secret.json"))

    def exact(self, fixture):
        """The fixture's identifiers and paths as written, and four of them in every case and encoding forms() gives.
        The run reads each identifier but the last, which is one thread id decoded. It reads each path but the last two, which lie below the store and below the home directory.
        """
        ids, paths = self.identifiers(), self.paths(fixture)
        return tuple(dict.fromkeys((*ids, *paths, *(form for value in (ODD, self.child, *paths[:2]) for form in forms(value)))))

    def shapes(self):
        return ("mcp:alpha-secret", "thread:secret-1", "node:alpha-secret", "run:alpha-secret", "task:alpha-secret", "prefix=mcp:alpha-secret", "id=mcp%3Aalpha-secret", "context-transfer:secret",
                "provider-thread:secret", "9f8e7d6c-e89b-12d3-a456-426614174000", "/srv/secret/notes.md", "/secret/x", "~/secret/notes.md", "~user/secret", "$HOME/secret", "C:\\Users\\secret\\file.txt",
                "file:/home/secret/notes", "file:///srv/secret", "(/srv/secret)", "x=/srv/secret", "%2Fsrv%2Fsecret%2Fnotes.md", ADDRESS, ADDRESS.upper(), quote(ADDRESS, safe=""), f"<{ADDRESS}>")

    def planted(self, fixture):
        """Each value as the page may meet it: alone, and for an identifier or a path the run read also inside a longer word."""
        return (*self.exact(fixture), *(f"pre{value}post" for value in self.exact(fixture)), *self.shapes())

    def plant(self, fixture, field, number, values):
        """One thread or one work item that holds the values in the field, or the store's name with the values added.
        A thread is running, failed, or queued. With at most 6, 4, and 4 of them beside the fixture's own running worker, the text form prints each one's row.
        """
        part, unit, joined = delegated(worker(1), f"part-{number}"), f"D{100 + number}", " and ".join(values)
        link = f"https://git.example/o/r/pull/{100 + number}"
        if field in ROW_FIELDS + ("provider", "instance id"):
            how = {"model": {"model": joined}, "title": {"title": joined}, "title in a sentence": {"title": f"see {joined} now"}, "provider": {"provider": joined}, "instance id": {"instance": joined}}[field]
            turn = ("running", 20, None) if number % 14 < 6 else ("failed", 20, 10) if number % 14 < 10 else ("queued", 20, None)
            fixture.thread(part, parent=worker(1), turns=(turn,), **{"title": f"part {number}", **how})
        elif field == "store name":
            fixture.meta["restaurant"] += f" {joined}"
        elif field == "unit id":
            fixture.unit(joined, "in-progress", f"item {number}")
        elif field == "branch":
            fixture.unit(unit, "in-progress", f"item {number}")
            fixture.units[-1]["branch"] = joined
        else:
            links = {"pull request": joined, "pull request owner": f"https://git.example/{joined}/r/pull/{100 + number}", "pull request query": f"{link}?x={joined}",
                     "pull request fragment": f"{link}#{joined}", "pull request userinfo": f"https://{quote(joined, safe='')}@git.example/o/r/pull/{100 + number}"}
            fixture.unit(unit, "in-progress", f"Fix {joined} today" if field == "summary" else f"item {number}", pr=links.get(field, ""))

    def found(self, output):
        """The needles in the output as written, in lower case, and percent-decoded once and twice."""
        read = [output, unquote(output), unquote(unquote(output))]
        return [needle for needle in (*NEEDLES, self.temporary, REQUEST) if any(needle.lower() in text.lower() for text in read)]

    def checked(self, fixture, field, slots, text=True):
        """Plant one slot's values in each thread or work item, run both forms, and name each needle either holds."""
        for number, values in enumerate(slots):
            self.plant(fixture, field, number, values)
        fixture.write()
        document = self.document("--max-bytes", "500000")
        data = data_of(document)
        self.assertEqual(([item[0] for item in data["I"] if item[0] in ("D7", "D8")], [line[1] for line in data["G"][0][1][:2]]), (["D7", "D8"], ["earlier worker", "worker"]), field)
        self.assertEqual((self.found(document), self.found(self.document()), self.found(self.out("--text")) if text else []), ([], [], []), field)

    def test_no_identifier_the_run_read_no_path_and_no_address_in_a_model_or_a_title_is_in_the_document_or_the_text(self):
        """The text form prints at most 7 running, 4 failed, and 4 queued rows, so each fixture holds 14 planted rows beside its own running worker."""
        for field in ROW_FIELDS:
            for start in range(0, len(self.planted(self.staffed("count"))), 14):
                with self.subTest(field, start=start):
                    fixture = self.staffed(f"{field.replace(' ', '-')}-{start}")
                    self.checked(fixture, field, [(value,) for value in self.planted(fixture)[start:start + 14]])
                    self.assertNotIn("more running agent", self.out("--text"))

    def test_no_identifier_the_run_read_no_path_and_no_address_in_a_summary_a_work_item_id_or_the_stores_name_is_in_the_document_or_the_text(self):
        """The text form prints at most 9 work items and two have agents, so the values are spread over 7 planted items."""
        for field in ("summary", "unit id", "store name"):
            with self.subTest(field):
                fixture = self.staffed(field.replace(" ", "-"))
                values = self.planted(fixture)
                self.checked(fixture, field, [values[start::7] for start in range(7)])

    def test_no_identifier_the_run_read_no_path_and_no_address_in_a_field_no_page_prints_is_in_the_document_or_the_text(self):
        for field in ("provider", "instance id", "branch"):
            with self.subTest(field):
                fixture = self.staffed(field.replace(" ", "-"))
                self.checked(fixture, field, [(value,) for value in self.planted(fixture)])

    def test_no_identifier_the_run_read_no_path_and_no_address_in_a_pull_request_value_is_in_the_document_or_the_text(self):
        """Every value is planted for the document. The text form prints 7 planted items, so it is checked with each identifier and path as written, 7 to a fixture."""
        for field in LINK_FIELDS:
            with self.subTest(field, form="document"):
                fixture = self.staffed(field.replace(" ", "-"))
                self.checked(fixture, field, [(value,) for value in self.planted(fixture)], text=False)
            for start in range(0, 21, 7):
                with self.subTest(field, start=start):
                    fixture = self.staffed(f"{field.replace(' ', '-')}-{start}")
                    core = (*self.identifiers()[:8], *self.paths(fixture), ADDRESS, "mcp:alpha-secret", "/srv/secret/notes.md", "file:/home/secret/notes", "~/secret", "9f8e7d6c-e89b-12d3-a456-426614174000")
                    self.assertEqual(len(core), 21)
                    self.checked(fixture, field, [(value,) for value in core[start:start + 7]])

    def test_every_planted_value_is_private_text_and_the_fixture_reads_each_identifier_it_plants_but_the_decoded_child_id(self):
        fixture = self.staffed("values")
        store, t3, _ = fixture.write().read()
        privacy = MOD["privacy_of"](store, t3, (fixture.store, fixture.database, fixture.base, fixture.home))
        self.assertEqual([value for value in self.identifiers() if value not in {*t3.ids, *store.coordinators}], [unquote(self.child)])
        self.assertEqual([value for value in self.planted(fixture) if privacy.clean(f"a {value} b") != "a b"], [])
        self.assertEqual([value for value in self.planted(fixture) if not self.found(value)], [])

    def test_request_name_of_two_or_more_parts_is_split_into_words_in_its_own_label_for_each_fixture_title(self):
        titles = ("", "Act as the audit sub-agent for this task.", "You are the auditor.", "x" * 61, REQUEST, f"see {REQUEST}", f"{REQUEST.upper()}: part 2", f"Read /pathmarker/{REQUEST}.md", "auditor")
        labels = ("audit task", "audit task", "audit task", "audit task", "audit task", "audit task", "audit task", "audit task", "auditor")
        for number, (title, label) in enumerate(zip(titles, labels)):
            with self.subTest(title):
                fixture = self.staffed(f"request-{number}")
                put(fixture.threads, 3, 1, title)
                fixture.write()
                document, text = self.document(), self.out("--text")
                self.assertEqual((self.found(document), self.found(text), data_of(document)["G"][0][1][2][1]), ([], [], label))

    def test_identifier_with_a_space_is_in_neither_form_when_two_fields_or_a_control_character_would_spell_it(self):
        """The plain form joins a work item's id and a row's label into one line. The document draws them in two elements."""
        spaced = "left_homemarker right_homemarker"
        for number, between in enumerate((" ", "\x00", "\x1b", "\x7f", "\x80", "%20", "%2520")):
            with self.subTest(between):
                fixture = self.staffed(f"spaced-{number}")
                fixture.unit("left_homemarker", "in-progress", f"see left_homemarker{between}right_homemarker now", thread=spaced)
                fixture.thread(spaced, title="right_homemarker", model="left_homemarker", turns=(("running", 9, None),))
                fixture.thread(delegated(spaced, "helper-task"), title="right_homemarker", model="right_homemarker", parent=spaced, turns=(("running", 8, None),))
                # read_store refuses a name that holds a NUL character.
                fixture.meta["restaurant"] = f"left_homemarker{between.replace(chr(0), chr(27))}right_homemarker kit"
                fixture.write()
                document, text = self.document(), self.out("--text")
                for output in (document, text):
                    read = (output, unquote(output), unquote(unquote(output)))
                    self.assertEqual([form for form in read if re.search(r"left_homemarker[\s\x00-\x1f\x7f-\x9f]+right_homemarker", form)], [])
                data = data_of(document)
                self.assertEqual((data["c"], [item[:2] for item in data["I"] if "left" in item[0]], [line[1] for index, lines in data["G"] for line in lines if "left" in data["I"][index][0]]),
                                 ("kit", [["left_homemarker", "see now"]], ["worker", "right_homemarker"]))
                self.assertEqual(text.split("\n")[4:6], ["  left_homemarker worker   left_homemarker   running for 9m", "  right_homemarker   running for 8m   under worker"])
                self.assertIn("  left_homemarker   working   see now   1 agent, 1 sub-agent, 17m at work", text.split("\n"))

    def test_out_file_named_in_a_summary_is_not_in_the_file_it_names(self):
        fixture = self.staffed("out")
        target = fixture.root / "pathmarker out" / "page.html"
        target.parent.mkdir(parents=True)
        fixture.unit("D9", "in-progress", f"Wrote {target} and {quote(str(target), safe='')} today")
        fixture.write()
        for form in ((), ("--text",)):
            with self.subTest(form):
                self.out(*form, "--out", str(target))
                written = target.read_text()
                self.assertEqual((self.found(written), "Wrote and today" in written), ([], True))


def ordinary(fixture):
    """A store, its agents, and their models named with ordinary words. Two workers' thread ids and every provider instance id are such words too."""
    fixture.meta["restaurant"] = "private auditor"
    fixture.coordinator(instance="queue")
    fixture.unit("queue", "in-progress", "queue src report", thread="review", task="queue", pr="https://git.example/queue/code/pull/451237")
    fixture.thread("review", title="queue worker", model="code", instance="private auditor", turns=(("running", 30, None),))
    fixture.thread(delegated(COORDINATOR, "queue"), title="queue", model="queue", instance="451237", parent=COORDINATOR, turns=(("running", 20, None),))
    fixture.unit("code", "in-review", "code review of the queue", thread="451237")
    fixture.thread("451237", title="code worker", provider="codex", model="private auditor", instance="code", turns=(("completed", 50, 40),))
    fixture.thread(delegated("451237", "code-review"), model="451237", parent="451237", turns=(("failed", 39, 38),))
    fixture.thread(delegated("451237", "src"), title="src", model="review", parent="451237", turns=(("running", 10, None),))
    fixture.unit("review", "queued", "review the src queue")
    fixture.unit("src", "blocked", "src code")
    fixture.unit("451237", "passed", "451237 private auditor", task="review")
    fixture.thread(delegated(COORDINATOR, "review"), model="src", parent=COORDINATOR, turns=(("queued", 5, None),))
    return fixture


class OrdinaryWordsTest(OutputCase):
    def test_store_whose_ids_request_names_summaries_titles_models_and_name_are_ordinary_words_loses_none_of_them_to_the_filter_in_the_text(self):
        fixture = ordinary(self.fixture).write()
        self.assertEqual(self.out("--text"), "\n".join([
            "Agent activity for private auditor, last 3h",
            "3 running now, 2 agents, 4 sub-agents, 1 failed",
            "Running now",
            "  code src   review   running for 10m   under worker",
            "  queue worker   code   running for 30m",
            "  queue queue   queue   running for 20m",
            "Queued",
            "  451237 review   src   queued for 5m",
            "Work items",
            "  451237   passed review   451237 private auditor   1 sub-agent, 0s at work",
            "  code   in review   code review of the queue   1 agent, 2 sub-agents, 21m at work",
            "  queue   working   queue src report   1 agent, 1 sub-agent, 50m at work   https://git.example/queue/code/pull/451237",
            "  review   landing   review the src queue   no activity in this window",
            "  src   blocked   src code   no activity in this window",
            "Failed",
            "  code code review   451237   60s at work",
            ""]))

    def test_store_whose_ids_request_names_summaries_titles_models_and_name_are_ordinary_words_loses_none_of_them_to_the_filter_in_the_document(self):
        fixture = ordinary(self.fixture).write()
        data = data_of(self.document())
        self.assertEqual((data["c"], data["M"], data["N"]), ("private auditor", ["model-a", "src", "private auditor", "451237", "review", "code", "queue"], []))
        self.assertEqual(data["I"], [["code", "code review of the queue", "in review", "info", "", 1],
                                     ["queue", "queue src report", "working", "go", "https://git.example/queue/code/pull/451237", 1],
                                     ["review", "review the src queue", "landing", "warn", "", 1], ["src", "src code", "blocked", "bad", "", 1],
                                     ["451237", "451237 private auditor", "passed review", "info", "", 1]])
        self.assertEqual([[data["I"][index][0], [(line[1], data["M"][line[2]]) for line in lines]] for index, lines in data["G"]], [
            ["451237", [("review", "src")]],
            ["code", [("worker", "private auditor"), ("code review", "451237"), ("src", "review")]],
            ["queue", [("worker", "code"), ("queue", "queue")]]])

    @unittest.skipUnless(shutil.which("node"), "node is not on PATH")
    def test_page_drawn_for_that_store_names_the_item_and_the_agent_of_each_running_and_queued_row(self):
        fixture = ordinary(self.fixture).write()
        texts = RendererTest.render(self, self.document())["texts"]
        self.assertEqual(texts[texts.index("Running now") + 1:texts.index("Timeline")], [
            "code", "src", "review", " · under ", "worker", "10m", "queue", "worker", "code", "30m", "queue", "queue", "queue", "20m",
            "Queued", "451237", "review", "src", "5m"])

    def test_identifier_that_is_an_ordinary_word_is_not_in_the_filter_and_every_other_identifier_of_the_fixture_is(self):
        fixture = ordinary(self.fixture)
        store, t3, _ = fixture.write().read()
        known = {form for found in MOD["privacy_of"](store, t3, (fixture.store, "relative")).index.values() for form in found}
        child = delegated(COORDINATOR, "queue")
        places = {os.path.abspath(path).lower() for path in (fixture.store, "relative", fixture.meta["projectRoot"])} | {os.path.realpath(path).lower() for path in (fixture.store, "relative", fixture.meta["projectRoot"])}
        expected = {value.lower() for thread in (COORDINATOR, child, delegated(COORDINATOR, "review"), delegated("451237", "code-review"), delegated("451237", "src"))
                    for value in (thread, unquote(thread), unquote(unquote(thread)))}
        nodes = {node.lower() for node, *_ in fixture.delegations}
        self.assertEqual(known, expected | nodes | places)
        self.assertEqual([word for word in ("queue", "review", "451237", "private auditor", "code", "src") if word in known], [])


class SlashCommandTest(OutputCase):
    """The slash commands in these fixtures stay in both forms. The planted paths and file: addresses go.
    The one-segment project root and store directory in these fixtures go too.
    """

    COMMANDS = ("/review", "/loop", "/code-review")
    PATHS = ("/home/someone/x", "~/x", "/a/b", "C:\\Users\\someone", "file:someone.txt")

    def staffed(self, name, root="/checkoutmarker"):
        fixture = self.fixture = Fixture(self.fixture.root / name)
        fixture.meta["projectRoot"] = root
        fixture.coordinator()
        return fixture

    def test_title_a_summary_and_a_work_item_id_that_hold_slash_commands_keep_them_in_the_document_and_the_text(self):
        fixture = self.staffed("commands")
        for number, command in enumerate(self.COMMANDS, start=1):
            fixture.unit(command, "in-progress", f"Run {command} on the queue", thread=worker(number))
            fixture.thread(worker(number), title=f"{command} worker", turns=(("running", 40 - number, None),))
            fixture.thread(delegated(worker(number), f"helper-{number}"), title=command, parent=worker(number), turns=(("running", 20 - number, None),))
        fixture.unit("D9", "in-review", "Run /review then /loop and /code-review today")
        fixture.thread(delegated(worker(3), "helper-4"), title="Run /review then /loop and /code-review", parent=worker(3), turns=(("running", 8, None),))
        fixture.write()
        data, text = data_of(self.document()), self.out("--text").split("\n")
        self.assertEqual([item[:2] for item in data["I"]], [["/code-review", "Run /code-review on the queue"], ["/loop", "Run /loop on the queue"], ["/review", "Run /review on the queue"],
                                                            ["D9", "Run /review then /loop and /code-review today"]])
        self.assertEqual([[data["I"][index][0], [line[1] for line in lines]] for index, lines in data["G"]], [
            ["/code-review", ["worker", "/code-review", "Run /review then /loop and /code-review"]], ["/loop", ["worker", "/loop"]], ["/review", ["worker", "/review"]]])
        self.assertEqual(text[2:10], [
            "Running now",
            "  /code-review worker   model-a   running for 37m",
            "  /code-review /code-review   model-a   running for 17m   under worker",
            "  /code-review Run /review then /loop and /code-review   model-a   running for 8m   under worker",
            "  /loop worker   model-a   running for 38m",
            "  /loop /loop   model-a   running for 18m   under worker",
            "  /review worker   model-a   running for 39m",
            "  /review /review   model-a   running for 19m   under worker"])
        self.assertEqual([line.split("   ")[:3] for line in text[text.index("Work items") + 1:text.index("Work items") + 5]], [
            ["  /code-review", "working", "Run /code-review on the queue"], ["  /loop", "working", "Run /loop on the queue"], ["  /review", "working", "Run /review on the queue"],
            ["  D9", "in review", "Run /review then /loop and /code-review today"]])

    def test_title_a_summary_and_a_work_item_id_that_hold_a_path_of_two_segments_a_home_path_a_drive_path_or_a_file_address_lose_it_in_the_document_and_the_text(self):
        fixture = self.staffed("paths")
        fixture.unit("D7", "in-progress", f"Read {' and '.join(self.PATHS)} after /review today", thread=worker(1))
        fixture.thread(worker(1), title="D7 worker", turns=(("running", 30, None),))
        for number, path in enumerate(self.PATHS, start=1):
            fixture.unit(path, "in-progress", f"item {number}", thread=worker(10 + number))
            fixture.thread(worker(10 + number), title="worker", turns=(("running", 20, None),))
            fixture.thread(delegated(worker(1), f"helper-{number}"), title=f"/loop over {path} now", parent=worker(1), turns=(("running", 10 + number, None),))
        fixture.write()
        document, text = self.document(), self.out("--text")
        data = data_of(document)
        self.assertEqual([word for word in ("someone", "/a/b", "~/x") if word in document or word in text], [])
        self.assertEqual(sorted(item[:2] for item in data["I"]), [["D7", "Read and and and and after /review today"], *(["work item", f"item {number}"] for number in range(1, 6))])
        self.assertEqual([line[1] for line in data["G"][[data["I"][index][0] for index, _ in data["G"]].index("D7")][1]], ["worker", "helper 5", "helper 4", "helper 3", "helper 2", "helper 1"])
        self.assertIn("  D7   working   Read and and and and after /review today   1 agent, 5 sub-agents, ", text)

    def test_one_segment_project_root_the_store_records_is_in_neither_form_and_a_slash_command_beside_it_is_in_both(self):
        fixture = self.staffed("root")
        fixture.unit("D7", "in-progress", "Run /review in /checkoutmarker and /CHECKOUTMARKER/src and x=/checkoutmarker today", thread=worker(1))
        fixture.unit("/checkoutmarker", "in-progress", "second item")
        fixture.thread(worker(1), title="D7 worker", turns=(("running", 30, None),))
        fixture.thread(delegated(worker(1), "helper-1"), title="/loop in /checkoutmarker", parent=worker(1), turns=(("running", 20, None),))
        fixture.thread(delegated(worker(1), "helper-2"), title="/loop", parent=worker(1), turns=(("running", 10, None),))
        fixture.write()
        document, text = self.document(), self.out("--text")
        data = data_of(document)
        self.assertEqual(["checkoutmarker" in output.lower() for output in (document, text)], [False, False])
        self.assertEqual((sorted(item[:2] for item in data["I"]), [line[1] for line in data["G"][0][1]]), ([["D7", "Run /review in and and today"], ["work item", "second item"]], ["worker", "helper 1", "/loop"]))
        self.assertIn("  D7 /loop   model-a   running for 10m   under worker", text.split("\n"))
        self.assertIn("  D7   working   Run /review in and and today   1 agent, 2 sub-agents, ", text)

    def test_filter_of_a_run_whose_store_directory_and_project_root_are_one_segment_removes_both_and_keeps_a_slash_command(self):
        fixture = self.staffed("places")
        store, t3, _ = fixture.write().read()
        privacy = MOD["privacy_of"](store, t3, ("/storemarker",))
        self.assertEqual(store.project_root, "/checkoutmarker")
        self.assertEqual(privacy.clean("in /storemarker and /checkoutmarker and /STOREMARKER/dishes.tsv run /review and /loop and /code-review"), "in and and run /review and /loop and /code-review")
        self.assertEqual(MOD["Privacy"]().clean("in /storemarker and /checkoutmarker"), "in /storemarker and /checkoutmarker")


class PlantedTest(OutputCase):
    """One fixture holds a value in the store's name, a work item id, a summary, a model, and the written title of a delegated child, each beside ordinary words."""

    def planted(self, name, value, root=None):
        fixture = self.fixture = Fixture(self.root / name)
        fixture.coordinator()
        if root is not None:
            fixture.meta["projectRoot"] = root
        fixture.meta["restaurant"] = f"kit {value}"
        fixture.unit(f"D7 {value}", summary=f"see {value} now", thread=worker(1))
        fixture.thread(worker(1), model=f"m {value}", turns=(("running", 10, None),))
        fixture.thread(delegated(COORDINATOR, "helper-second"), title=f"read {value} here", parent=COORDINATOR, turns=(("running", 9, None),))
        return fixture

    def gone(self, fixture, value):
        """No string of the document's data, with its character references decoded, and no line of the text holds value in any case. The words beside it stay in the store's name, the id, the summary, and the model.
        A title that holds it is not used, and its row takes the label of its request name. Returns the data."""
        fixture.write()
        data, lines = data_of(self.document()), self.out("--text").split("\n")
        written = [html.unescape(text) for part in data.values() for text in strings(part)]
        self.assertEqual([text for text in (*written, *lines) if value.lower() in text.lower()], [])
        self.assertEqual((data["c"], data["I"][0][:2], data["M"], data["G"][1][1][0][1]), ("kit", ["D7", "see now"], ["model-a", "m"], "helper second"))
        self.assertEqual((lines[0], [line for line in lines if line.startswith(("  D7 ", "  helper second "))]), (
            "Agent activity for kit, last 3h", ["  D7 worker   m   running for 10m", "  helper second   model-a   running for 9m", "  D7   working   see now   1 agent, 10m at work"]))
        return data

    def test_whole_request_name_of_a_delegated_task_is_removed_from_the_stores_name_a_work_item_id_a_summary_and_a_model_and_a_pull_request_link_and_another_rows_written_title_that_hold_it_are_not_shown(self):
        request = "kit-d7-audit-task"
        fixture = self.planted("request", request)
        fixture.thread(delegated(COORDINATOR, request), parent=COORDINATOR, turns=(("running", 8, None),))
        fixture.unit("D8", pr=f"https://example.test/{request}/repo/pull/7")
        fixture.unit("D9", pr=f"https://example.test/owner/{request}/pull/8")
        data = self.gone(fixture, request)
        self.assertEqual(([item[4] for item in data["I"]], data["N"]), (["", "", ""], [
            "2 pull request links are not shown. Each is not an https://host/owner/repository/pull/number address, or it holds text this page removes."]))

    def test_slash_command_a_longer_token_and_a_request_name_with_no_hyphen_stay_in_a_summary_and_a_written_title_beside_delegated_tasks_named_code_review_queue_and_kit_d7_audit_task(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D1", summary="Run /code-review on the queue for kit-d7-audit-task-2", thread=worker(1))
        fixture.thread(worker(1), turns=(("running", 10, None),))
        for number, request in enumerate(("code-review", "queue", "kit-d7-audit-task")):
            fixture.thread(delegated(COORDINATOR, request), parent=COORDINATOR, turns=(("running", 9 - number, None),))
        fixture.thread(delegated(COORDINATOR, "helper-second"), title="/code-review kit-d7-audit-task-2", parent=COORDINATOR, turns=(("running", 5, None),))
        fixture.write()
        data, lines = data_of(self.document()), self.out("--text").split("\n")
        self.assertEqual((data["I"][0][:2], [line[1] for line in data["G"][1][1]]), (
            ["D1", "Run /code-review on the queue for kit-d7-audit-task-2"], ["code review", "queue", "d7 audit task", "/code-review kit-d7-audit-task-2"]))
        self.assertEqual([line for line in lines if "queue" in line or "task-2" in line], [
            "  queue   model-a   running for 8m", "  /code-review kit-d7-audit-task-2   model-a   running for 5m",
            "  D1   working   Run /code-review on the queue for kit-d7-audit-task-2   1 agent, 10m at work"])

    def test_absolute_path_whose_first_segment_starts_with_a_hyphen_a_plus_sign_or_a_second_slash_and_a_recorded_project_root_of_2_or_3_characters_are_removed_from_the_stores_name_a_work_item_id_a_summary_and_a_model_and_a_written_title_that_holds_one_is_not_shown(self):
        for number, path in enumerate(("/-secret/notes.txt", "/+secret/notes.txt", "//secret/share/notes.txt")):
            with self.subTest(path):
                self.gone(self.planted(f"shape-{number}", path), path)
        for number, root in enumerate(("/x", "/xy")):
            with self.subTest(root=root):
                self.gone(self.planted(f"root-{number}", root, root=root), root)

    def test_email_address_with_a_quoted_local_part_and_one_with_a_bracketed_host_are_removed_from_the_stores_name_a_work_item_id_a_summary_and_a_model_and_a_written_title_that_holds_one_is_not_shown(self):
        for number, address in enumerate(('"auditor"@example.test', "auditor@[127.0.0.1]")):
            with self.subTest(address):
                self.gone(self.planted(f"address-{number}", address), address)

    def test_each_listed_email_address_whose_quoted_local_part_ends_in_a_backslash_and_a_quotation_mark_is_removed_from_the_stores_name_a_work_item_id_a_summary_and_a_model_and_a_written_title_that_holds_it_is_not_shown(self):
        for number, address in enumerate(('"audit\\""@example.test', f'"{"a" * 60}\\""@example.test', '"audit\\""@[127.0.0.1]')):
            with self.subTest(address):
                self.gone(self.planted(f"pair-{number}", address), address)

    def test_whole_request_name_of_3_characters_that_holds_a_hyphen_and_an_underscore_or_a_dot_is_removed_from_the_stores_name_a_work_item_id_a_summary_and_a_model_and_a_pull_request_link_and_another_rows_written_title_that_hold_it_are_not_shown(self):
        for number, request in enumerate(("a-_", "a-.")):
            with self.subTest(request):
                fixture = self.planted(f"short-{number}", request)
                fixture.thread(delegated(COORDINATOR, request), parent=COORDINATOR, turns=(("running", 8, None),))
                fixture.unit("D8", pr=f"https://example.test/{request}/repo/pull/7")
                fixture.unit("D9", pr=f"https://example.test/owner/{request}/pull/8")
                data = self.gone(fixture, request)
                self.assertEqual(([item[4] for item in data["I"]], data["N"]), (["", "", ""], [
                    "2 pull request links are not shown. Each is not an https://host/owner/repository/pull/number address, or it holds text this page removes."]))

    def test_word_that_holds_the_listed_request_name_of_3_characters_beside_a_letter_a_digit_an_underscore_or_a_hyphen_stays_in_the_stores_name_a_work_item_id_a_summary_a_model_and_a_written_title(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.meta["restaurant"] = "kit xa-_"
        fixture.unit("D7 a-_b", summary="see xa-_ and a-_2 with a-__ or pre-a-_ now", thread=worker(1))
        fixture.thread(worker(1), model="m a-_b", turns=(("running", 10, None),))
        fixture.thread(delegated(COORDINATOR, "helper-second"), title="read xa-_y here", parent=COORDINATOR, turns=(("running", 9, None),))
        fixture.thread(delegated(COORDINATOR, "a-_"), parent=COORDINATOR, turns=(("running", 8, None),))
        fixture.write()
        data, lines = data_of(self.document()), self.out("--text").split("\n")
        self.assertEqual((data["c"], data["I"][0][:2], data["M"], [line[1] for line in data["G"][1][1]]), (
            "kit xa-_", ["D7 a-_b", "see xa-_ and a-_2 with a-__ or pre-a-_ now"], ["model-a", "m a-_b"], ["read xa-_y here", "a _"]))
        self.assertEqual((lines[0], [line for line in lines if line.startswith(("  D7 ", "  read "))]), (
            "Agent activity for kit xa-_, last 3h", ["  D7 a-_b worker   m a-_b   running for 10m", "  read xa-_y here   model-a   running for 9m",
                                                    "  D7 a-_b   working   see xa-_ and a-_2 with a-__ or pre-a-_ now   1 agent, 10m at work"]))


class ReferenceTest(unittest.TestCase):
    """The sentences the review of round 2 corrected, as the script's docstring and its generated reference now state them."""

    def test_reference_states_the_filter_and_the_pull_request_rule_as_the_docstring_does_and_holds_no_sentence_of_the_filter_it_replaced(self):
        reference = " ".join((ROOT / "docs/cli/activity.md").read_text().split())
        stated = ("Every string a page emits passes one filter after its parts are joined.",
                  "An identifier that reads as ordinary words is not private text.",
                  "The filter removes a word for no other reason.",
                  "Such a path starts with a slash before a character other than a space, a slash, a quotation mark, or a backtick, and it holds a second slash with no space, quotation mark, or backtick before it and a character other than those after it.",
                  "It leaves a word of one segment that starts with a slash, such as /review, unless it holds private text.",
                  "Userinfo, a query, and a fragment are dropped.",
                  "A work item on the page whose nonblank pull request value cannot be converted to such an address, or whose address the filter would change, has no link and is counted in a note.",
                  "The output and the newline after it take at most --max-bytes bytes.")
        self.assertEqual([sentence for sentence in stated if sentence not in reference or sentence not in " ".join(MOD["__doc__"].split())], [])
        replaced = ("4 characters or longer", "with any other value that is not blank", "the class Privacy", "6 or more digits", "the most bytes the HTML document",
                    "Such a path starts with a slash before a letter", "unless it holds a path this run read.")
        self.assertEqual([words for words in replaced if words in reference], [])
        self.assertIn("| `--max-bytes MAX_BYTES` | | | `16000` | the most bytes the output and the newline after it take, from 16000 to 500000 |", reference)


class PrivateValuesTest(OutputCase):
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

    def test_document_keeps_a_model_with_no_private_text_and_document_and_text_keep_the_pull_request_address_and_count_a_refused_link(self):
        fixture = one_running(self.fixture)
        fixture.units[0]["pr"] = "https://user:pw@example.test/o/r/pull/7?x=mcp:alpha-secret#task:alpha-secret"
        fixture.units[1]["pr"] = "https://example.test/o/r/pull/6/files"
        fixture.thread(delegated(worker(1), "kit-d7-part-1"), title="part 1", model="mcp:alpha-secret", parent=worker(1), turns=(("completed", 20, 10),))
        fixture.thread(delegated(worker(1), "kit-d7-part-2"), title="part 2", provider="opencode", model="opencode/model-b-20260101", parent=worker(1), turns=(("completed", 20, 10),))
        fixture.thread(delegated(worker(1), "kit-d7-part-3"), title="part 3", model="opencode-go/Model_C (high)", parent=worker(1), turns=(("completed", 20, 10),))
        fixture.write()
        data, text = data_of(self.document()), self.out("--text").split("\n")
        note = "1 pull request link is not shown. It is not an https://host/owner/repository/pull/number address, or it holds text this page removes."
        self.assertEqual((data["M"], [item[4] for item in data["I"]], data["N"]), (["model-a", "other model", "model-b-20260101", "opencode-go/Model_C (high)"], ["", PR7], [note]))
        self.assertEqual([line for line in text if "   working   " in line or "D6" in line], ["  D7   working   Agent activity page   1 agent, 3 sub-agents, 1h 33m at work   " + PR7,
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
        return self.fails(3)

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
                data = data_of(self.document())
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
        data = data_of(self.document())
        self.assertEqual((data["n"], [line[:2] for line in data["G"][0][1]]), ([0, 1, 1, 0], [[0, "worker"], [1, "part"]]))
        self.assertEqual(data["N"], ["T3 lists 2 sub-agents with no thread or no start time under threads this page read. They are not shown."])
        self.assertIn("  T3 lists 2 sub-agents with no thread or no start time under threads this page read. They are not shown.", self.out("--text").split("\n"))

    def test_sub_agent_rows_that_make_a_thread_its_own_parent_or_ancestor_exit_3_in_both_forms(self):
        def looped(*pairs, started):
            def spoil(fixture):
                fixture.thread(worker(2), title="second worker", turns=(("running", 4, None),))
                fixture.thread("mcp:threadmarker-other-project", turns=(("running", 3, None),))
                fixture.turns.append((worker(1), "running", fixture.stamp(5), None))
                for number, (parent, child) in enumerate(pairs):
                    fixture.delegations.append((f"node:nodemarker-loop-{number}", parent, child, "running", fixture.stamp(6) if started else None, None))
            return spoil

        other = "mcp:threadmarker-other-project"
        cases = {"self": ((worker(1), worker(1)),), "two": ((worker(1), worker(2)), (worker(2), worker(1))), "three": ((worker(1), worker(2)), (worker(2), other), (other, worker(1))),
                 "outside": ((other, other),), "child": ((delegated(worker(1), "kit-d7-part"), worker(1)),)}
        for name, pairs in cases.items():
            for started in (False, True):
                with self.subTest(name, started=started):
                    self.staffed(f"{name}-{started}", looped(*pairs, started=started))
                    line = f"activity: T3's orchestration_v2_projection_subagents makes a thread its own ancestor; {CHANGED}"
                    self.assertEqual((self.fails(3), self.fails(3, "--text")), (line, line))

    def test_self_parent_row_and_two_thread_cycle_that_a_later_row_overrides_and_self_parent_row_with_no_start_time_after_a_valid_row_exit_3_in_both_forms(self):
        cases = {"self": ((worker(1), worker(1), 10), (COORDINATOR, worker(1), 5)),
                 "unstarted": ((COORDINATOR, worker(1), 10), (worker(1), worker(1), None)),
                 "two": ((worker(2), worker(1), 10), (worker(1), worker(2), 10), (COORDINATOR, worker(1), 5))}
        for name, rows in cases.items():
            def spoil(fixture, rows=rows):
                fixture.thread(worker(2), title="second worker", turns=(("running", 4, None),))
                fixture.turns.append((worker(1), "running", fixture.stamp(5), None))
                fixture.delegations += [(f"node:nodemarker-masked-{number}", parent, child, "running", fixture.stamp(start), None) for number, (parent, child, start) in enumerate(rows)]

            with self.subTest(name):
                self.staffed(f"masked-{name}", spoil)
                line = f"activity: T3's orchestration_v2_projection_subagents makes a thread its own ancestor; {CHANGED}"
                self.assertEqual((self.fails(3), self.fails(3, "--text")), (line, line))

    def test_sub_agent_rows_with_no_start_time_that_make_no_loop_are_read_and_the_running_worker_is_counted(self):
        def chained(fixture):
            fixture.thread(worker(2), title="second worker", turns=(("running", 4, None),))
            fixture.turns.append((worker(1), "running", fixture.stamp(5), None))
            fixture.delegations.append(("node:nodemarker-chain", worker(1), worker(2), "running", None, None))

        fixture = self.staffed("chain", chained)
        data = data_of(self.document())
        self.assertEqual((data["n"], [line[:2] for line in data["G"][0][1]], data["N"]), ([2, 1, 2, 0], [[0, "worker"], [1, "part"], [1, "second worker"]], []))
        self.assertEqual(self.out("--text").split("\n")[1], "2 running now, 1 agent, 2 sub-agents, 0 failed")

    def test_no_count_is_negative_and_a_page_with_a_running_agent_never_says_no_agent_ran_for_150_random_sets_of_sub_agent_rows(self):
        """Each set either makes a thread its own ancestor, which is refused, or gives a page whose every count is at least 0 before and after each fold."""
        randomly = random.Random(125)
        threads = (COORDINATOR, worker(1), worker(2), native(1), native(2), "mcp:threadmarker-other-project")
        outcomes = {"refused": 0, "running": 0, "quiet": 0}
        for number in range(150):
            fixture = Fixture(self.root / f"random-{number}")
            fixture.unit("D7", thread=worker(1))
            fixture.unit("D8", "merged", thread=worker(2))
            for thread in threads:
                turns = [(randomly.choice(("running", "queued", "completed", "failed", "interrupted")), start, end)
                         for start, end in ((randomly.randint(20, 170), randomly.randint(5, 19)),) * randomly.randint(0, 2)]
                fixture.thread(thread, turns=tuple((status, start, None if status in ("running", "queued") else end) for status, start, end in turns))
            for index in range(randomly.randint(0, 7)):
                parent, child = randomly.choice(threads), randomly.choice(threads + (None,))
                start = randomly.choice((None, randomly.randint(20, 170)))
                fixture.delegations.append((f"node:nodemarker-{number}-{index}", parent, child, randomly.choice(("running", "completed", "failed")), fixture.stamp(start), None))
            try:
                store, t3, window = fixture.write().read()
            except MOD["SourceError"] as error:
                self.assertEqual(str(error), f"T3's orchestration_v2_projection_subagents makes a thread its own ancestor; {CHANGED}")
                outcomes["refused"] += 1
                continue
            privacy = MOD["privacy_of"](store, t3)
            page = MOD["build_page"](store, t3, window, privacy)
            running = [thread for thread, one in t3.agents.items() if thread != COORDINATOR and any(turn.status.value == "running" and turn.end is None for turn in MOD["stretches"](one))]
            outcomes["running" if running else "quiet"] += 1
            for fold, folded in enumerate([page, *(page := step(page) for step in MOD["FOLDS"])]):
                with self.subTest(number, fold=fold):
                    counts = [*vars(folded.totals).values(), *vars(folded.hidden).values(), *(count for one in folded.groups for count in (one.agents, one.subagents)),
                              *(each.stands_for for one in folded.groups for each in one.rows), *(each.seconds for one in folded.groups for each in one.rows)]
                    self.assertEqual([count for count in counts if count < 0], [])
                    said = MOD["notes"](folded, MOD["items_of"](folded))
                    empty_note = "No agent or sub-agent of this coordinator ran in this window." in said
                    self.assertEqual((folded.totals.running, empty_note), (len(running), False) if running else (0, empty_note))
                    self.assertIsNone(re.search(r"-[0-9]", MOD["render_text"](folded, privacy.clean).split("\n")[1]))
                    self.assertEqual(data_of(MOD["render_html"](folded, privacy.clean))["n"], list(vars(folded.totals).values()))
        self.assertEqual([name for name, times in outcomes.items() if times < 20], [])

    def test_sqlite_error_that_quotes_a_value_exits_3_without_the_value(self):
        fixture = self.staffed("undecodable")
        connection = sqlite3.connect(fixture.database)
        connection.execute("update orchestration_v2_projection_threads set title = cast(x'70617468ff6d61726b6572' as text) where title = 'part'")
        connection.commit()
        connection.close()
        line = self.fails(3)
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
                self.assertEqual(self.out("--text"), "\n".join([
                    "Agent activity for kit, last 3h",
                    "0 running now, 1 agent, 0 sub-agents, 0 failed",
                    "Queued",
                    "  D7 worker   model-a   queued for 5m",
                    "Work items",
                    "  D7   working   Activity page   1 agent, 0s at work",
                    ""]))
                data = data_of(self.document())
                self.assertEqual((data["n"], data["S"][1], data["G"][0][1][0][4:]), ([0, 1, 0, 0], "queued", [1, "0s", [972, 28, 5], 1, "5m"]))

    def test_agent_with_a_running_turn_and_a_queued_turn_is_counted_running_and_keeps_both_bars(self):
        fixture = queued_only(self.fixture)
        fixture.turns[1:] = [(worker(1), "running", fixture.stamp(10), None), (worker(1), "queued", fixture.stamp(5), None)]
        fixture.write()
        lines = self.out("--text").split("\n")
        self.assertEqual(lines[1:5], ["1 running now, 1 agent, 0 sub-agents, 0 failed", "Running now", "  D7 worker   model-a   running for 10m", "Work items"])
        data = data_of(self.document())
        self.assertEqual((data["n"], data["G"][0][1][0][4:]), ([1, 1, 0, 0], [0, "10m", [944, 56, 1, 972, 28, 5], 1, "10m"]))

    def test_providers_own_sub_agent_whose_open_delegation_is_queued_is_listed_under_queued(self):
        fixture = queued_only(self.fixture)
        put(fixture.turns, 1, 1, "running")
        fixture.thread(native(1), title="/root/spec_review", parent=worker(1), delegation=("queued", 2, None))
        fixture.write()
        lines = self.out("--text").split("\n")
        self.assertEqual(lines[1:6], ["1 running now, 1 agent, 1 sub-agent, 0 failed", "Running now", "  D7 worker   model-a   running for 5m", "Queued",
                                      "  D7 spec_review   model-a   queued for 2m   under worker"])


class BudgetTest(OutputCase):
    def test_stdout_of_the_html_form_with_its_newline_is_at_most_max_bytes_at_the_exact_size_and_one_byte_under_it(self):
        fixture = busy(self.fixture).write()
        whole = fixture.stdout_bytes("--max-bytes", "500000")
        self.assertTrue(len(whole) > 16001 and whole.endswith(b"</script>\n"))
        self.assertEqual(fixture.stdout_bytes("--max-bytes", str(len(whole))), whole)
        under = fixture.stdout_bytes("--max-bytes", str(len(whole) - 1))
        self.assertLess(len(under), len(whole))
        self.assertLessEqual(len(fixture.stdout_bytes()), 16000)

    def long_values(self):
        """A store name, a summary, a work item id, a pull request number, and a model that are each valid and thousands of characters long, and a written title of 60 characters."""
        fixture = one_running(self.fixture)
        fixture.meta["restaurant"] = "name " * 10000
        fixture.units[0].update(summary="word " * 10000, pr="https://git.example/o/r/pull/" + "7" * 20000)
        fixture.unit("D" + "9" * 5000, "in-progress", "long id", pr="https://git.example/o/r/pull/8")
        fixture.thread(delegated(worker(1), "helper-task"), title="t" * 60, model="m" * 30000, parent=worker(1), turns=(("failed", 20, 10),))
        return fixture.write()

    def test_stdout_of_the_text_form_with_its_newline_is_at_most_max_bytes_at_the_exact_size_and_one_byte_under_it_where_it_marks_each_cut_and_counts_the_link(self):
        fixture = self.long_values()
        whole = fixture.stdout_bytes("--text", "--max-bytes", "500000")
        self.assertTrue(len(whole) > 100000 and whole.endswith(b" at work\n"), len(whole))
        self.assertEqual(fixture.stdout_bytes("--text", "--max-bytes", str(len(whole))), whole)
        under = fixture.stdout_bytes("--text", "--max-bytes", str(len(whole) - 1))
        self.assertLess(len(under), 16000)
        self.assertLessEqual(len(fixture.stdout_bytes("--text")), 16000)
        self.assertEqual(len(whole), 125520)
        self.assertEqual(under.decode().split("\n"), [
            "Agent activity for name name name name name name nam…, last 3h",
            "1 running now, 1 agent, 1 sub-agent, 1 failed",
            "Running now",
            "  D7 worker   model-a   running for 42m",
            "Work items",
            "  D7   working   word word word word word word wor…   1 agent, 1 sub-agent, 73m at work",
            "  D6   landing   Queue fix   no activity in this window   " + PR6,
            "  D99999999…   working   long id   no activity in this window   https://git.example/o/r/pull/8",
            "Failed",
            "  D7 " + "t" * 17 + "…   " + "m" * 21 + "…   10m at work",
            "Notes",
            "  1 pull request link is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            ""])
        self.assertEqual(fixture.stdout_bytes("--text"), under)
        self.assertEqual(fixture.stdout_bytes("--text", "--max-bytes", "16000"), under)

    def test_stdout_of_the_html_form_with_very_long_values_is_at_most_max_bytes_at_the_exact_size_and_one_byte_under_it(self):
        fixture = self.long_values()
        whole = fixture.stdout_bytes("--max-bytes", "500000")
        self.assertTrue(125000 < len(whole) <= 500000 and whole.endswith(b"</script>\n"), len(whole))
        self.assertEqual(fixture.stdout_bytes("--max-bytes", str(len(whole))), whole)
        under = fixture.stdout_bytes("--max-bytes", str(len(whole) - 1))
        self.assertLess(len(under), 16000)
        self.assertEqual(fixture.stdout_bytes(), under)
        data = data_of(under.decode()[:-1])
        self.assertEqual((data["c"], [item[:2] + item[4:5] for item in data["I"]], data["M"], data["N"]), (
            "name name name name name name nam…", [["D6", "Queue fix", PR6], ["D7", "word word word word word word wor…", ""], ["D99999999…", "long id", "https://git.example/o/r/pull/8"]],
            ["model-a", "m" * 21 + "…"], ["1 pull request link is not shown, because the page is at its size limit. Use a larger --max-bytes to see more."]))

    def test_largest_plain_form_of_a_shortened_page_is_under_8000_bytes_with_every_list_full_every_note_and_every_string_over_its_byte_limit(self):
        counts = ("dropped_items", "dropped_agents", "cut_agents", "cut_lines", "cut_in_flight", "other_threads", "unknown_status", "by_request_name", "unstarted")
        hidden = MOD["Hidden"](**dict.fromkeys(counts, 9999999))
        sizes = {}
        for character in ("s", "<", '"', "𝕏"):
            wide = character * 400

            def big(number, no_link=""):
                return item(str(number) + wide, summary=str(number) + wide, pr="https://example.test/" + "x" * 69, no_link=no_link)

            def rows(prefix, status):
                return [replace(row(prefix + str(n) + wide, depth=min(n, 2), status=status, open_seconds=None if status == "failed" else 604800, stands_for=99999, model=str(n) + wide),
                                seconds=604800) for n in range(12)]

            groups = [replace(group(big(number, "refused" if number % 2 else "cut"), *rows("a", "running"), *rows("b", "queued"), *rows("c", "failed")), agents=9999999, subagents=9999999)
                      for number in range(12)]
            groups.append(replace(group(None, *rows("d", "failed")), agents=9999999, subagents=9999999))
            unfolded = replace(page(*groups, items=[big(number) for number in range(100, 140)], hidden=hidden, name=wide),
                               window=MOD["Window"](1790000000.0, 1790604799.0), totals=MOD["Totals"](9999999, 9999999, 9999999, 9999999))
            lines = MOD["render_text"](MOD["shortened"](unfolded), same).split("\n")
            self.assertEqual(([lines.index(heading) for heading in ("Running now", "Queued", "Work items", "Failed", "Notes")], len(lines)), ([2, 11, 17, 29, 35], 48))
            self.assertEqual((lines[10], lines[16], lines[27], lines[34]), ("  and 137 more running agents", "  and 140 more queued agents", "  and 43 more work items", "  and 152 more failed agents"))
            sizes[character] = len("\n".join(lines).encode())
        self.assertEqual({character: size for character, size in sizes.items() if size >= 8000}, {})

    def test_stdout_is_utf8_under_a_locale_that_is_not(self):
        fixture = one_running(self.fixture)
        fixture.units[0]["summary"] = "Agent activity page 𝔸é"
        fixture.write()
        ascii_locale = {"LC_ALL": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"}
        for form in ((), ("--text",)):
            with self.subTest(form):
                result = in_process.finished(
                    in_process.Reason.INHERITED_ENVIRONMENT,
                    replace(fixture.command(*form, env=ascii_locale), text=False),
                )
                self.assertEqual((result.returncode, result.stderr), (0, b""), result.stderr)
                self.assertIn("Agent activity page 𝔸é".encode(), result.stdout)

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

    def test_text_form_of_a_page_with_a_30000_character_model_of_m_and_request_name_helper_task_is_125520_bytes_within_2_seconds(self):
        fixture = self.long_values()
        started = time.perf_counter()
        whole = fixture.stdout_bytes("--text", "--max-bytes", "500000")
        elapsed = time.perf_counter() - started
        self.assertEqual(len(whole), 125520)
        self.assertNotIn(b"helper-task", whole)
        self.assertLess(elapsed, 2.0, elapsed)


class WindowTest(OutputCase):
    def test_window_is_labelled_by_its_whole_seconds_as_hours_minutes_and_seconds_in_both_forms(self):
        fixture = one_running(self.fixture).write()
        for hours, seconds, words in (("0.01", 36, "36s"), ("0.25", 900, "15m"), ("1", 3600, "1h"), ("1.51", 5436, "1h 30m 36s"), ("3", 10800, "3h"), ("168", 604800, "168h")):
            with self.subTest(hours):
                self.assertEqual(self.out("--text", "--hours", hours).split("\n")[0], f"Agent activity for kit, last {words}")
                self.assertEqual(data_of(self.document("--hours", hours))["w"][1:], [seconds, words])



def frozen_bytes(script, fixture, *args):
    result = in_process.invoke(in_process.Invocation(
        program=script,
        argv=("--at", str(fixture.store), "--t3-home", str(fixture.base), *args),
        env={"HOME": str(fixture.home), "PATH": os.environ.get("PATH", "")},
        cwd=fixture.root,
        clock=fixture.now,
        text=False,
    ))
    if (result.returncode, result.stderr) != (0, b""):
        raise AssertionError(result.stderr)
    return result.stdout


PRIOR_ACTIVITY = """
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
"""


class PriorPageTest(OutputCase):
    def test_a_store_from_before_kinds_matches_the_old_page(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        script = Path(directory.name) / "activity.py"
        script.write_bytes(zlib.decompress(base64.b64decode(PRIOR_ACTIVITY)))
        fixture = one_running(self.fixture).write()
        self.assertEqual([len(line.split("\t")) for line in (fixture.store / "dishes.tsv").read_text().splitlines()], [15, 15, 15])
        self.assertFalse((fixture.store / "goals.tsv").exists())
        for args in ((), ("--text",)):
            self.assertEqual(fixture.stdout_bytes(*args), frozen_bytes(script, fixture, *args))

        second = one_running(Fixture(self.root / "outside")).write()
        (second.store / "goals.tsv").write_text(
            "id\tat\tstate\tname\tpurpose\tdone\tshare\tclosed\n"
            "G1\t2026-10-10T11:00:00.000Z\topen\tUnused goal name\tpurpose text\tdone test\t1\t\n")
        table = second.store / "dishes.tsv"
        header, *rows = table.read_text().splitlines()
        table.write_text("\n".join([header, *("\t".join([row, "nope", "G9"]) for row in rows)]) + "\n")
        html, text = second.stdout_bytes(), second.stdout_bytes("--text")
        self.assertEqual((html, text), (frozen_bytes(script, second), frozen_bytes(script, second, "--text")))
        for output in (html, text):
            self.assertNotIn(b"Unassigned", output)
            self.assertNotIn(b"Unused goal name", output)
            self.assertNotIn(b"nope", output)
        self.assertNotIn(" \u00b7 ".encode(), text)
        self.assertNotIn(" \u00b7 ", DATA.search(html.decode()).group(1))


def write_goals(fixture, rows):
    header = "id\tat\tstate\tname\tpurpose\tdone\tshare\tclosed"
    (fixture.store / "goals.tsv").write_text("\n".join([header, *("\t".join(row) for row in rows)]) + "\n")


def widen_items(fixture, cells):
    path = fixture.store / "dishes.tsv"
    header, *rows = path.read_text().splitlines()
    widened = ["\t".join([row, *cells.get(row.split("\t")[0], ())]) for row in rows]
    path.write_text("\n".join([header, *widened]) + "\n")


def painted(case, document):
    data, program = DATA.search(document).group(1), PROGRAM.search(document).group(1)
    script = case.fixture.root / "render.js"
    script.write_text(f"const DATA = {json.dumps(data)};{STUB}{program}{REPORT}")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, env={"PATH": os.environ["PATH"], "TZ": "UTC"})
    case.assertEqual((result.returncode, result.stderr), (0, ""), result.stderr)
    return json.loads(result.stdout)


class GoalStripTest(OutputCase):
    def test_a_goal_name_loses_each_kind_of_private_text(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Parser work", thread=worker(1))
        fixture.thread(worker(1), title="D7 worker", turns=(("running", 30, None),))
        fixture.write()
        email = "ada@private.example"
        uuid = "7ead0a2b-e89b-12d3-a456-426614174000"
        file_url = "file:/home/private/secret"
        drive = "c:/secret/notes"
        purpose = "purpose-sentinel-qq"
        done = "done-sentinel-zz"
        name = " ".join(["Kept", worker(1), str(fixture.store), email, uuid, file_url, drive, "parser"])
        write_goals(fixture, [("Gkept", "2026-10-10T11:00:00.000Z", "done", name, purpose, done, "1", "")])
        widen_items(fixture, {"D7": ("feature", "Gkept")})
        html, text = self.document(), self.out("--text")
        for needle in (worker(1), str(fixture.store), email, uuid, file_url, drive, purpose, done, "Gkept"):
            self.assertNotIn(needle, html)
            self.assertNotIn(needle, text)
        for kept in ("Kept", "parser"):
            self.assertIn(kept, html)
            self.assertIn(kept, text)

    def test_each_shown_item_is_in_one_group_and_the_counts_match(self):
        fixture = self.fixture
        fixture.meta["thread"] = ""
        for ident, state, summary in (
                ("D1", "in-progress", "Parser work"),
                ("D2", "queued", "Queue one"),
                ("D3", "queued", "Queue two"),
                ("D4", "blocked", "Loose one"),
                ("D5", "in-review", "Loose two")):
            fixture.unit(ident, state, summary)
        fixture.write()
        stamp = "2026-10-10T11:00:00.000Z"
        write_goals(fixture, [
            ("G0", stamp, "paused", "Empty shelf", "purpose text", "done test", "1", ""),
            ("G1", stamp, "open", "Ship the parser", "purpose text", "done test", "1", ""),
            ("G2", stamp, "done", "Retire the queue", "purpose text", "done test", "1", ""),
        ])
        widen_items(fixture, {"D1": ("", "G1"), "D2": ("", "G2"), "D3": ("", "G2"), "D4": ("", ""), "D5": ("", "G9")})
        html, text = self.document(), self.out("--text")
        data = data_of(html)
        self.assertEqual([item[0] for item in data["I"]], ["D1", "D2", "D3", "D4", "D5"])
        self.assertEqual([item[5] for item in data["I"]], [0, 0, 0, 0, 0])
        self.assertEqual(data["F"], [
            ["Ship the parser · 1 working", "Ship the parser", [0]],
            ["Retire the queue · 2 landing", "Retire the queue", [1, 2]],
            ["Unassigned · 1 in review, 1 blocked", "Unassigned", [3, 4]],
        ])
        seen = []
        for count, _heading, indexes in data["F"]:
            seen += indexes
            self.assertEqual(sum(int(part) for part in count.split() if part.isdigit()), len(indexes))
        self.assertEqual(seen, [0, 1, 2, 3, 4])
        lines = text.splitlines()
        self.assertEqual(lines[lines.index("Work items") + 1:lines.index("Notes")], [
            "  Ship the parser   1 working",
            "  Ship the parser",
            "    D1   working   Parser work   no activity in this window",
            "  Retire the queue   2 landing",
            "  Retire the queue",
            "    D2   landing   Queue one   no activity in this window",
            "    D3   landing   Queue two   no activity in this window",
            "  Unassigned   1 in review, 1 blocked",
            "  Unassigned",
            "    D4   blocked   Loose one   no activity in this window",
            "    D5   in review   Loose two   no activity in this window",
        ])
        for ident in ("D1", "D2", "D3", "D4", "D5"):
            self.assertEqual(html.count(ident), 1)
            self.assertEqual(text.count(ident), 1)
        for absent in ("Empty shelf", "purpose text", "done test", "G0", "G1", "G2", "G9"):
            self.assertNotIn(absent, html)
            self.assertNotIn(absent, text)
        drawn = painted(self, html)["texts"]
        self.assertEqual(drawn[-22:], [
            "Ship the parser · 1 working", "Ship the parser", "D1", "Parser work", "working",
            "Retire the queue · 2 landing", "Retire the queue", "D2", "Queue one", "landing", "D3", "Queue two", "landing",
            "Unassigned · 1 in review, 1 blocked", "Unassigned", "D4", "Loose one", "blocked", "D5", "Loose two", "in review",
            "Work items in flight",
        ])
        for ident in ("D1", "D2", "D3", "D4", "D5"):
            self.assertEqual(drawn.count(ident), 1)

    def test_a_kind_outside_the_five_shows_no_tag(self):
        fixture = one_running(self.fixture).write()
        widen_items(fixture, {"D7": ("feature", ""), "D6": ("nope", "")})
        html, text = self.document(), self.out("--text")
        self.assertNotIn("nope", html)
        self.assertNotIn("nope", text)
        data = data_of(html)
        self.assertEqual([(item[0], item[6]) for item in data["I"]], [("D6", ""), ("D7", "feature")])
        item_lines = [line for line in text.splitlines() if "   D6   " in line or "   D7   " in line]
        self.assertEqual(item_lines, [
            "    feature   D7   working   Agent activity page   1 agent, 63m at work   " + PR7,
            "    D6   landing   Queue fix   no activity in this window   " + PR6,
        ])
        drawn = painted(self, html)["texts"]
        self.assertEqual(drawn.count("feature"), 1)
        self.assertNotIn("nope", drawn)

    def test_the_page_does_not_write_the_store_or_the_database(self):
        fixture = one_running(self.fixture).write()
        write_goals(fixture, [("G1", "2026-10-10T11:00:00.000Z", "open", "Ship the parser", "purpose text", "done test", "1", "")])
        widen_items(fixture, {"D7": ("feature", "G1"), "D6": ("fix", "G1")})
        before = listing(fixture.store), listing(fixture.database.parent)
        self.assertIn("goals.tsv", before[0])
        self.document()
        self.out("--text")
        self.assertEqual((listing(fixture.store), listing(fixture.database.parent)), before)

    def test_a_finished_labeled_item_shows_its_goal_and_kind_in_text(self):
        fixture = self.fixture
        fixture.coordinator(turns=(("completed", 60, 50),))
        fixture.unit("D1", "merged", "Finished parser", thread=worker(1))
        fixture.thread(worker(1), title="D1 worker", turns=(("completed", 30, 20),))
        fixture.write()
        write_goals(fixture, [("G1", "2026-10-10T11:00:00.000Z", "open",
                               "Ship the parser", "purpose-secret", "done-secret", "1", "")])
        widen_items(fixture, {"D1": ("feature", "G1")})
        lines = self.out("--text").splitlines()
        self.assertEqual(lines[lines.index("Work items") + 1:], [
            "  Ship the parser   1 merged",
            "  Ship the parser",
            "    feature   D1   merged   Finished parser   1 agent, 10m at work",
        ])
        html = self.document()
        self.assertIn("Finished parser", html)
        self.assertNotIn("Ship the parser", html)
        self.assertNotIn("F", data_of(html))

    def test_text_groups_a_finished_item_when_the_strip_item_has_no_labels(self):
        fixture = self.fixture
        fixture.coordinator(turns=(("completed", 60, 50),))
        fixture.unit("D1", "merged", "Finished parser", thread=worker(1))
        fixture.unit("D2", "in-progress", "Open shelf")
        fixture.thread(worker(1), title="D1 worker", turns=(("completed", 30, 20),))
        fixture.write()
        write_goals(fixture, [("G1", "2026-10-10T11:00:00.000Z", "open",
                               "Ship the parser", "purpose-secret", "done-secret", "1", "")])
        widen_items(fixture, {"D1": ("feature", "G1")})
        lines = self.out("--text").splitlines()
        self.assertEqual(lines[lines.index("Work items") + 1:], [
            "  Ship the parser   1 merged",
            "  Ship the parser",
            "    feature   D1   merged   Finished parser   1 agent, 10m at work",
            "  Unassigned   1 working",
            "  Unassigned",
            "    D2   working   Open shelf   no activity in this window",
        ])
        html = self.document()
        self.assertIn("Open shelf", html)
        self.assertNotIn("Ship the parser", html)
        self.assertNotIn("Unassigned", html)
        self.assertNotIn("F", data_of(html))


class LedgerTest(OutputCase):
    def test_store_without_threads_tsv_omits_the_ledger_hook_and_matches_a_header_only_file(self):
        fixture = one_running(self.fixture).write()
        absent, text = fixture.stdout_bytes(), fixture.stdout_bytes("--text")
        (fixture.store / "threads.tsv").write_text("at\tdish\tattempt\trole\tthread\tseat\tstart\tend\n")
        present = fixture.stdout_bytes()
        self.assertEqual(present, absent)
        self.assertEqual(fixture.stdout_bytes("--text"), text)
        for document in (absent, present):
            self.assertNotIn(b".ldg", document)
            self.assertNotIn(b"D.L", document)

    def test_timeline_header_wraps_its_attempt_lines_instead_of_squeezing_the_id_and_title_to_no_width(self):
        """The attempt block's 100% basis fills the header's line, so the header needs a wrap to give the id, title, and chip a line of their own."""
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [{"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": "opaque=ledger-wrap", "start": stamp, "end": ""}])
        style = re.search(r"<style>(.*?)</style>", self.document(), re.S).group(1)
        self.assertEqual([(name, value) for selector, name, value in declarations(style) if selector == ".tl>.grp"], [("flex-wrap", "wrap")])

    def test_opaque_ledger_private_token_in_the_thread_and_the_seat_is_absent_from_both_forms(self):
        token = "opaque=ledger-private-token"
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": token, "seat": token, "start": stamp, "end": ""},
        ])
        document = self.document()
        lines = self.out("--text").split("\n")
        self.assertNotIn(token, document)
        self.assertNotIn(token, "\n".join(lines))
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1], "    attempt 1 · worker · open")
        self.assertNotIn(token, MOD["roots_of"](MOD["read_store"](fixture.store)))

    def test_an_attempt_of_five_thousand_digits_sorts_after_numeric_attempts(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "10", "role": "worker", "thread": "opaque=ledger-sort-10", "start": stamp, "end": ""},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "worker", "thread": "opaque=ledger-sort-2", "start": stamp, "end": ""},
            {"at": stamp, "dish": "D7", "attempt": "9" * 5000, "role": "worker", "thread": "opaque=ledger-sort-long", "start": stamp, "end": ""},
        ])
        phrases = [
            "attempt 2 · worker · open",
            "attempt 10 · worker · open",
            "attempt 999999999… · worker · open",
        ]
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1:at + 4], ["    " + phrase for phrase in phrases])
        shown = [line for _, rows in data_of(self.document()).get("L", []) for line in rows]
        self.assertEqual(shown, phrases)

    def test_five_hundred_ledger_rows_stay_within_max_bytes_and_the_note_counts_the_lines_left_out(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {
                "at": stamp,
                "dish": "D7",
                "attempt": str(number),
                "role": "worker",
                "thread": f"opaque=ledger-row-{number}",
                "seat": "grok/grok-4",
                "start": stamp,
                "end": "",
            }
            for number in range(1, 501)
        ])

        def phrase(number):
            return f"attempt {number} · worker · grok/grok-4 · open"

        def html_phrases(document):
            return [line for _, lines in data_of(document).get("L", []) for line in lines]

        def text_phrases(text):
            return [line[4:] for line in text.split("\n") if line.startswith("    attempt ")]

        html_bytes = fixture.stdout_bytes()
        text_bytes = fixture.stdout_bytes("--text")
        self.assertEqual((len(html_bytes), len(text_bytes)), (15991, 15967))
        self.assertLessEqual(len(html_bytes), 16000)
        self.assertLessEqual(len(text_bytes), 16000)
        html, text = self.document(), self.out("--text")
        self.assertEqual(data_of(html)["N"], [
            "1 more agent is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            "358 attempt lines are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
        ])
        self.assertEqual(html_phrases(html), [phrase(number) for number in range(1, 143)])
        self.assertEqual((html_phrases(html)[0], html_phrases(html)[-1]), (phrase(1), phrase(142)))
        self.assertIn("  181 attempt lines are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.", text)
        self.assertEqual(text_phrases(text), [phrase(number) for number in range(1, 320)])
        self.assertEqual((text_phrases(text)[0], text_phrases(text)[-1]), (phrase(1), phrase(319)))

        wide_html, wide_text = self.document("--max-bytes", "500000"), self.out("--text", "--max-bytes", "500000")
        wide = [phrase(number) for number in range(1, 501)]
        self.assertEqual(html_phrases(wide_html), wide)
        self.assertEqual(text_phrases(wide_text), wide)
        for output in (wide_html, wide_text):
            self.assertNotIn("attempt line is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.", output)
            self.assertNotIn("attempt lines are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.", output)

        html_bytes = fixture.stdout_bytes("--max-bytes", "20000")
        text_bytes = fixture.stdout_bytes("--text", "--max-bytes", "20000")
        self.assertEqual((len(html_bytes), len(text_bytes)), (19989, 19984))
        self.assertLessEqual(len(html_bytes), 20000)
        self.assertLessEqual(len(text_bytes), 20000)
        html, text = self.document("--max-bytes", "20000"), self.out("--text", "--max-bytes", "20000")
        self.assertEqual(data_of(html)["N"], [
            "272 attempt lines are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
        ])
        self.assertEqual(html_phrases(html), [phrase(number) for number in range(1, 229)])
        self.assertEqual((html_phrases(html)[0], html_phrases(html)[-1]), (phrase(1), phrase(228)))
        self.assertIn("  99 attempt lines are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.", text)
        self.assertEqual(text_phrases(text), [phrase(number) for number in range(1, 402)])
        self.assertEqual((text_phrases(text)[0], text_phrases(text)[-1]), (phrase(1), phrase(401)))

    def test_ordinary_ledger_secret_token_in_the_thread_and_the_seat_is_absent_from_both_forms(self):
        token = "ledger-secret-token"
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [{"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": token, "seat": token, "start": stamp, "end": ""}])
        document, text = self.document(), self.out("--text")
        self.assertNotIn(token, document)
        self.assertNotIn(token, text)
        self.assertIn("attempt 1 · worker · open", text)

    def test_ordinary_ledgertoken_in_the_thread_attempt_role_and_seat_is_absent_from_both_forms(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        token, other = "ledgertoken", "ledger-secret-token"
        row = {"at": stamp, "dish": "D7", "start": stamp, "end": ""}
        write_threads(fixture, [{**row, "attempt": token, "role": token, "thread": token, "seat": token}, {**row, "attempt": "1", "role": "worker", "thread": other, "seat": other}])
        document, text = self.document(), self.out("--text")
        self.assertIn("attempt 1 · worker · open", text)
        self.assertEqual([token in document, token in text, other in document, other in text], [False, False, False, False])

    def test_stored_attempt_orders_two_then_ten_then_the_spaced_cell_that_displays_2(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        rows = (("10", "ten", "opaque=ten"), (" 2 ", "spaced", "opaque=spaced"), ("2", "two", "opaque=two"))
        write_threads(fixture, [{"at": stamp, "dish": "D7", "attempt": attempt, "role": role, "thread": thread, "start": stamp, "end": ""} for attempt, role, thread in rows])
        text, document = self.out("--text"), self.document()
        order = [line.strip() for line in text.split("\n") if line.strip().startswith("attempt ")]
        self.assertEqual(order, ["attempt 2 · two · open", "attempt 10 · ten · open", "attempt 2 · spaced · open"])
        self.assertNotIn("opaque=", document)
        self.assertNotIn("opaque=", text)

    def test_padded_thread_cell_is_absent_from_both_forms(self):
        token = " ledgertoken "
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [{"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": token, "seat": token, "start": stamp, "end": ""}])
        document, text = self.document(), self.out("--text")
        self.assertIn("attempt 1 · worker · open", text)
        self.assertNotIn(token.strip(), document)
        self.assertNotIn(token.strip(), text)

    def test_short_thread_cell_is_absent_as_its_own_word_and_stays_inside_a_longer_word(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [{"at": stamp, "dish": "D7", "attempt": "1", "role": "T1", "thread": "T1", "seat": "T12", "start": stamp, "end": ""}])
        document, text = self.document(), self.out("--text")
        self.assertIsNone(re.search(r"(?<![\w-])T1(?![\w-])", document))
        self.assertIsNone(re.search(r"(?<![\w-])T1(?![\w-])", text))
        self.assertIn("T12", document)
        self.assertIn("T12", text)

    def test_attempt_rank_reads_one_to_eighteen_ascii_digits_as_an_integer(self):
        rank = MOD["attempt_rank"]
        self.assertEqual((rank("007"), rank("9" * 18)), ((0, 7), (0, int("9" * 18))))
        self.assertEqual((rank("9" * 19), rank(""), rank("٢")), ((1, "9" * 19), (1, ""), (1, "٢")))


class LiveTest(OutputCase):
    def serve(self, *args, **how):
        """Start activity.py --serve 0 as a real interpreter and return its process and printed token address. A race: the server answers while this test fetches."""
        process = in_process.spawn(in_process.Reason.RACE, self.fixture.command("--serve", "0", *args, **how))

        def stop():
            process.terminate()
            process.wait(timeout=30)
            process.stdout.close()
            process.stderr.close()

        self.addCleanup(stop)
        deadline = time.time() + 30
        ready = []
        while not ready and time.time() < deadline:
            if process.poll() is not None:
                break
            ready, _, _ = select.select([process.stdout], [], [], min(1.0, max(0.0, deadline - time.time())))
        if not ready and process.poll() is not None:
            stderr = process.stderr.read()
            self.fail(f"the live server exited with {process.returncode} before printing an address: {stderr.strip()[-500:]}")
        self.assertTrue(ready, "the live server printed no address")
        url = process.stdout.readline().strip()
        self.assertTrue(url.startswith("http://127.0.0.1:"), url)
        return process, url

    def fetch(self, url, method=None, data=None):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method=method, data=data)) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def served(self):
        one_running(self.fixture).write()
        return self.serve()

    def test_serves_the_page_only_at_its_token_path(self):
        _, url = self.served()
        port = url.split(":")[2].split("/")[0]
        code, _, body = self.fetch(url)
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith(b"<!doctype html>"), body[:80])
        code, _, body = self.fetch(url + "?x=1")
        self.assertEqual(code, 200)
        self.assertTrue(body.startswith(b"<!doctype html>"), body[:80])
        for target in (f"http://127.0.0.1:{port}/", url.rsplit("/", 1)[0] + "/wrong", url + "x"):
            with self.subTest(target=target):
                self.assertEqual(self.fetch(target)[0], 404)

    def test_get_page_get_missing_and_options_carry_null_cors_and_no_store(self):
        _, url = self.served()
        _, plain, _ = self.fetch(url)
        _, missing, _ = self.fetch(url.rsplit("/", 1)[0] + "/wrong")
        _, options, _ = self.fetch(url, method="OPTIONS")
        for headers in (plain, missing, options):
            with self.subTest(headers=dict(headers)):
                self.assertEqual(headers.get("Access-Control-Allow-Origin"), "null")
                self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_only_get_and_options_are_answered(self):
        _, url = self.served()
        self.assertEqual(self.fetch(url, data=b"x")[0], 501)
        self.assertEqual(self.fetch(url, method="OPTIONS")[0], 204)
        self.assertEqual(self.fetch(url)[0], 200)

    def test_the_server_binds_127_0_0_1_only(self):
        fixture = one_running(self.fixture).write()
        settings = MOD["Settings"](fixture.store, fixture.database, 3.0, MOD["BUDGET"])
        server = MOD["live_server"](settings, MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"]), 0)
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], "127.0.0.1")

    def test_the_server_binds_without_a_hostname_lookup(self):
        fixture = one_running(self.fixture).write()
        settings = MOD["Settings"](fixture.store, fixture.database, 3.0, MOD["BUDGET"])
        with mock.patch("socket.getfqdn", side_effect=AssertionError("no hostname lookup")):
            server = MOD["live_server"](settings, MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"]), 0)
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_name, "127.0.0.1")

    def test_the_token_file_is_mode_0600_and_reused_across_restarts(self):
        fixture = one_running(self.fixture).write()
        first = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        path = fixture.store / "activity-token"
        self.assertTrue(path.is_file())
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertGreaterEqual(len(first.token) * 8, 128)
        second = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertEqual((second.token, second.route, second.url), (first.token, first.route, first.url))
        os.chmod(path, 0o644)
        third = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertNotEqual(third.token, first.token)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertTrue(stat.S_ISREG(os.stat(path).st_mode))

    def test_a_token_file_with_whitespace_around_the_token_is_reused(self):
        fixture = one_running(self.fixture).write()
        path = fixture.store / "activity-token"
        token = "t" * 43
        path.write_text(f" {token}\n\t")
        os.chmod(path, 0o600)
        second = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertEqual(second.token, token.encode("ascii"))

    def test_short_token_is_replaced_with_a_private_regular_file(self):
        fixture = one_running(self.fixture).write()
        path = fixture.store / "activity-token"
        path.write_text("x")
        os.chmod(path, 0o600)
        second = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertNotEqual(second.token, b"x")
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]{43}", second.token.decode("ascii")))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertTrue(stat.S_ISREG(os.stat(path).st_mode))

    def test_exposed_token_file_is_replaced_not_reused(self):
        fixture = one_running(self.fixture).write()
        first = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        path = fixture.store / "activity-token"
        os.chmod(path, 0o644)
        bad = path.read_text().strip()
        self.assertEqual(bad, first.token.decode("ascii"))
        second = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertNotEqual(second.token.decode("ascii"), bad)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertTrue(stat.S_ISREG(os.stat(path).st_mode))

    def test_symlink_token_path_is_replaced_without_touching_its_target(self):
        fixture = one_running(self.fixture).write()
        path = fixture.store / "activity-token"
        target = Path(self.fixture.root) / "symlink-target"
        target.write_text("known")
        os.chmod(target, 0o644)
        before = (target.read_bytes(), stat.S_IMODE(os.stat(target).st_mode))
        os.symlink(target, path)
        second = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertNotEqual(second.token, b"known")
        self.assertFalse(path.is_symlink())
        self.assertTrue(stat.S_ISREG(os.stat(path).st_mode))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual((target.read_bytes(), stat.S_IMODE(os.stat(target).st_mode)), before)

    def test_invalid_utf8_token_file_is_replaced_with_a_private_regular_file(self):
        fixture = one_running(self.fixture).write()
        path = fixture.store / "activity-token"
        path.write_bytes(b"\xff\xfe\x00bad")
        os.chmod(path, 0o644)
        real_chmod = os.chmod

        def check_private_before_chmod(target, mode):
            if Path(target) == path and stat.S_IMODE(os.stat(path).st_mode) != 0o600:
                self.assertEqual(path.read_bytes(), b"\xff\xfe\x00bad", "a fresh token was readable before chmod")
            return real_chmod(target, mode)

        with mock.patch.object(os, "chmod", side_effect=check_private_before_chmod):
            second = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]{43}", second.token.decode("ascii")))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertTrue(stat.S_ISREG(os.stat(path).st_mode))

    def test_empty_token_file_is_replaced_with_a_private_regular_file(self):
        fixture = one_running(self.fixture).write()
        path = fixture.store / "activity-token"
        path.write_text("")
        os.chmod(path, 0o644)
        real_chmod = os.chmod

        def check_private_before_chmod(target, mode):
            if Path(target) == path and stat.S_IMODE(os.stat(path).st_mode) != 0o600:
                self.assertEqual(path.read_bytes(), b"", "a fresh token was readable before chmod")
            return real_chmod(target, mode)

        with mock.patch.object(os, "chmod", side_effect=check_private_before_chmod):
            second = MOD["secret_of"](fixture.store, MOD["DEFAULT_PORT"])
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]{43}", second.token.decode("ascii")))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertTrue(stat.S_ISREG(os.stat(path).st_mode))

    def test_the_live_wrapper_holds_the_token_url_the_collapse_rule_and_both_buttons(self):
        fixture = one_running(self.fixture).write()
        wrapper = self.out("--live-page")
        token = (fixture.store / "activity-token").read_text().strip()
        self.assertIn(f"http://127.0.0.1:48731/activity/{token}", wrapper)
        self.assertIn("merged|dropped|done", wrapper)
        self.assertIn("Expand all", wrapper)
        self.assertIn("Collapse all", wrapper)
        self.assertIn("30000", wrapper)

    def test_a_broken_store_answers_503_with_the_one_line_message_and_no_page(self):
        fixture = one_running(self.fixture).write()
        (fixture.store / "restaurant.json").unlink()
        _, url = self.serve()
        for _ in range(2):
            code, _, body = self.fetch(url)
            self.assertEqual(code, 503)
            self.assertEqual(body, b"activity: that directory holds no coordinator's store; pass --at <store directory> or set BRIGADE_DIR")


if __name__ == "__main__":
    unittest.main()
