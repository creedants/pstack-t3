if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock
import urllib.error
import urllib.request
from urllib.parse import quote, unquote
import html
import io
import random
import runpy
import select
import stat
import time
import fcntl
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from tests import in_process
import tests.test_activity as test_activity
from tests.activity_tests.support import ActivityCase, CHANGED, COORDINATOR, ELSEWHERE, Fixture, FROZEN, GUIDE, LOG_COLUMNS, MARKERS, MOD, NOW, OutputCase, PROJECT, PR6, PR7, ROOT, SCHEMA, SCRIPT, SKILL, UNIT_COLUMNS, VIEWS, busy, day, delegated, empty, extreme, failed_child, forms, frozen_bytes, live_listing, listing, native, one_running, populate, put, queued_only, setUpModule, tree, typical, widen_items, worker, write_goal_names, write_goals, write_threads
from tests.activity_tests.pages import BARE, CLEAN, DATA, FilterCase, HOSTILE, HOSTILE_DRAWN, HOSTILE_WRITTEN, NEEDLES, NodeRender, ODD, ORDINARY, PROGRAM, REPORT, REQUEST, ROW_FIELDS, LINK_FIELDS, ADDRESS, STORE_WORDS, STUB, UUID_TEXT, WORDS, agent, agents_shown, data_of, declarations, drawn, group, hostile, item, labels, named, ordinary, page, page_of, painted, row, same, skill_section, store_of, strings, tagged, T3_REFUSAL, STORE_REFUSAL
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
