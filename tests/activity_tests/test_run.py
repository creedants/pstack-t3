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

    def test_run_changes_no_file_in_the_store_and_no_byte_of_a_closed_database(self):
        fixture = failed_child(self.fixture).write()
        before = listing(fixture.store), listing(fixture.database.parent)
        self.assertEqual(sorted(before[0]), ["dishes.tsv", "log.tsv", "restaurant.json"])
        self.assertEqual(sorted(before[1]), ["statev2.sqlite"])
        self.document()
        self.out("--text")
        after = listing(fixture.database.parent)
        self.assertEqual(listing(fixture.store), before[0])
        self.assertLessEqual(set(after) - set(before[1]), {"statev2.sqlite-wal", "statev2.sqlite-shm"})
        self.assertEqual({name: after[name] for name in before[1]}, before[1])
        log = Path(f"{fixture.database}-wal")
        if log.exists():
            self.assertLessEqual(len(log.read_bytes()), 32)

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
