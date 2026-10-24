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
class LedgerEarlierTest(OutputCase):
    def test_earlier_attempt_lines_leave_the_current_list_and_no_thread_id_reaches_the_page(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Activity page", thread=worker(3), pr="https://example.test/o/r/pull/7")
        fixture.retired("D7", worker(1))
        fixture.retired("D7", worker(2))
        fixture.thread(worker(3), title="D7 worker", turns=(("running", 4.5, None),))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.thread(worker(2), turns=(("completed", 70, 60),))
        fixture.write_store()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": worker(1), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "claims check", "thread": "opaque=three-1-claims", "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "worker", "thread": worker(2), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "code delegate", "thread": "opaque=three-2-code", "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "3", "role": "worker", "thread": worker(3), "start": stamp, "end": ""},
            {"at": stamp, "dish": "D7", "attempt": "3", "role": "claims check", "thread": "opaque=three-3-claims", "start": stamp, "end": ""},
        ])
        fixture.write()
        summary = "attempts 1-2: 2 workers, 1 code delegate, 1 claims check, all ended"
        current = ["attempt 3 · worker · open", "attempt 3 · claims check · open"]
        payload = ["attempt 1 · worker · ended", "attempt 1 · claims check · ended", "attempt 2 · worker · ended", "attempt 2 · code delegate · ended"]
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1:at + 4], ["    " + summary, *["    " + phrase for phrase in current]])
        document = self.document()
        data = data_of(document)
        self.assertEqual([line for _, rows in data.get("L", []) for line in rows], current)
        folded = data.get("E", [])
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0][1], summary)
        self.assertEqual(folded[0][2], payload)
        self.assertTrue(data.get("R"))
        self.assertEqual(sum(len(idxs) for _, idxs in data["R"]), 2)
        self.assertIn(summary, document)
        for phrase in (*current, *payload):
            self.assertIn(phrase, document)
        self.assertNotIn("opaque=", document)
        self.assertNotIn("opaque=", "\n".join(lines))
        for ident in (worker(1), worker(2), worker(3)):
            self.assertNotIn(ident, document)
            self.assertNotIn(ident, "\n".join(lines))
    def test_live_earlier_worker_stays_visible_with_its_ledger_row_current(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Activity page", thread=worker(3), pr="https://example.test/o/r/pull/7")
        fixture.retired("D7", worker(1))
        fixture.retired("D7", worker(2))
        fixture.thread(worker(3), title="D7 worker", turns=(("running", 4.5, None),))
        fixture.thread(worker(1), turns=(("running", 6.5, None),))
        fixture.thread(worker(2), turns=(("completed", 70, 60),))
        fixture.write_store()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": worker(1), "start": stamp, "end": ""},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "worker", "thread": worker(2), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "3", "role": "worker", "thread": worker(3), "start": stamp, "end": ""},
        ])
        fixture.write()
        summary = "attempt 2: 1 worker, all ended"
        current = ["attempt 1 · worker · open", "attempt 3 · worker · open"]
        payload = ["attempt 2 · worker · ended"]
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1:at + 4], ["    " + summary, *["    " + phrase for phrase in current]])
        self.assertIn("  D7 earlier worker   running for ", "\n".join(lines))
        document = self.document()
        data = data_of(document)
        self.assertEqual([line for _, rows in data.get("L", []) for line in rows], current)
        folded = data.get("E", [])
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0][1], summary)
        self.assertEqual(folded[0][2], payload)
        lanes = data["G"][0][1]
        covered = [(lanes[index][0], lanes[index][1]) for _, idxs in data["R"] for index in idxs]
        self.assertEqual(covered, [(0, "earlier worker")])
        self.assertEqual(sum(1 for line in lanes if line[1] == "earlier worker"), 2)
        for ident in (worker(1), worker(2), worker(3)):
            self.assertNotIn(ident, document)
            self.assertNotIn(ident, "\n".join(lines))
    def test_live_descendant_of_a_completed_earlier_worker_keeps_its_ancestor_chain_visible(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Activity page", thread=worker(3), pr="https://example.test/o/r/pull/7")
        fixture.retired("D7", worker(1))
        fixture.retired("D7", worker(2))
        fixture.thread(worker(3), title="D7 worker", turns=(("running", 4.5, None),))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.thread(worker(2), turns=(("completed", 70, 60),))
        child = delegated(worker(1), "helper-task")
        fixture.thread(child, title="helper", parent=worker(1), turns=(("running", 6.5, None),))
        fixture.write_store()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": worker(1), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "worker", "thread": worker(2), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "3", "role": "worker", "thread": worker(3), "start": stamp, "end": ""},
        ])
        fixture.write()
        summary = "attempt 2: 1 worker, all ended"
        current = ["attempt 1 · worker · ended", "attempt 3 · worker · open"]
        payload = ["attempt 2 · worker · ended"]
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1:at + 4], ["    " + summary, *["    " + phrase for phrase in current]])
        running = [line for line in lines if "running for" in line]
        self.assertTrue(any("D7 helper" in line and "under earlier worker" in line for line in running), running)
        document = self.document()
        data = data_of(document)
        self.assertEqual([line for _, rows in data.get("L", []) for line in rows], current)
        folded = data.get("E", [])
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0][1], summary)
        self.assertEqual(folded[0][2], payload)
        lanes = data["G"][0][1]
        covered = [(lanes[index][0], lanes[index][1]) for _, idxs in data["R"] for index in idxs]
        self.assertEqual(covered, [(0, "earlier worker")])
        self.assertIn("helper", [line[1] for line in lanes])
        for ident in (worker(1), worker(2), worker(3), child):
            self.assertNotIn(ident, document)
            self.assertNotIn(ident, "\n".join(lines))
    def test_live_depth_three_row_keeps_its_depth_two_parent_visible(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Activity page", thread=worker(3), pr="https://example.test/o/r/pull/7")
        fixture.retired("D7", worker(1))
        fixture.retired("D7", worker(2))
        fixture.thread(worker(3), title="D7 worker", turns=(("running", 4.5, None),))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.thread(worker(2), turns=(("completed", 70, 60),))
        first = delegated(worker(1), "level-one-task")
        fixture.thread(first, title="helper one", parent=worker(1), turns=(("completed", 50, 40),))
        second = delegated(first, "level-two-task")
        fixture.thread(second, title="helper two", parent=first, turns=(("completed", 45, 35),))
        third = delegated(second, "level-three-task")
        fixture.thread(third, title="helper three", parent=second, turns=(("running", 6.5, None),))
        fixture.write_store()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": worker(1), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "worker", "thread": worker(2), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "3", "role": "worker", "thread": worker(3), "start": stamp, "end": ""},
        ])
        fixture.write()
        summary = "attempt 2: 1 worker, all ended"
        current = ["attempt 1 · worker · ended", "attempt 3 · worker · open"]
        payload = ["attempt 2 · worker · ended"]
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1:at + 4], ["    " + summary, *["    " + phrase for phrase in current]])
        running = [line for line in lines if "running for" in line]
        self.assertTrue(any("D7 helper three" in line for line in running), running)
        document = self.document()
        data = data_of(document)
        self.assertEqual([line for _, rows in data.get("L", []) for line in rows], current)
        folded = data.get("E", [])
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0][1], summary)
        self.assertEqual(folded[0][2], payload)
        lanes = data["G"][0][1]
        covered = [(lanes[index][0], lanes[index][1]) for _, idxs in data["R"] for index in idxs]
        self.assertEqual(covered, [(0, "earlier worker")])
        self.assertIn("helper one", [line[1] for line in lanes])
        self.assertIn("helper two", [line[1] for line in lanes])
        self.assertIn("helper three", [line[1] for line in lanes])
        self.assertEqual(sum(1 for line in lanes if line[1] == "earlier worker"), 2)
        for ident in (worker(1), worker(2), worker(3), first, second, third):
            self.assertNotIn(ident, document)
            self.assertNotIn(ident, "\n".join(lines))
    def test_earlier_worker_without_a_ledger_row_is_counted_once_by_lane_label(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Activity page", thread=worker(3), pr="https://example.test/o/r/pull/7")
        fixture.retired("D7", worker(1))
        fixture.retired("D7", worker(2))
        fixture.thread(worker(3), title="D7 worker", turns=(("running", 4.5, None),))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.thread(worker(2), turns=(("completed", 70, 60),))
        fixture.write_store()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "claims check", "thread": "opaque=missing-1-claims", "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "worker", "thread": worker(2), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "code delegate", "thread": "opaque=missing-2-code", "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "3", "role": "worker", "thread": worker(3), "start": stamp, "end": ""},
            {"at": stamp, "dish": "D7", "attempt": "3", "role": "claims check", "thread": "opaque=missing-3-claims", "start": stamp, "end": ""},
        ])
        fixture.write()
        summary = "attempts 1-2: 1 worker, 1 code delegate, 1 claims check, 1 earlier worker, all ended"
        current = ["attempt 3 · worker · open", "attempt 3 · claims check · open"]
        payload = ["attempt 1 · claims check · ended", "attempt 2 · worker · ended", "attempt 2 · code delegate · ended"]
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1:at + 4], ["    " + summary, *["    " + phrase for phrase in current]])
        document = self.document()
        data = data_of(document)
        self.assertEqual([line for _, rows in data.get("L", []) for line in rows], current)
        folded = data.get("E", [])
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0][1], summary)
        self.assertEqual(folded[0][2], payload)
        lanes = data["G"][0][1]
        covered = [(lanes[index][0], lanes[index][1]) for _, idxs in data["R"] for index in idxs]
        self.assertEqual(covered, [(0, "earlier worker"), (0, "earlier worker")])
        self.assertNotIn("opaque=", document)
        self.assertNotIn("opaque=", "\n".join(lines))
    def test_two_completed_children_with_one_ledger_row_count_two_code_delegates(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Activity page", thread=worker(3), pr="https://example.test/o/r/pull/7")
        fixture.retired("D7", worker(1))
        fixture.retired("D7", worker(2))
        fixture.thread(worker(3), title="D7 worker", turns=(("running", 4.5, None),))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.thread(worker(2), turns=(("completed", 70, 60),))
        first = delegated(worker(1), "first-part")
        fixture.thread(first, title="code delegate", parent=worker(1), turns=(("completed", 50, 40),))
        second = delegated(worker(1), "second-part")
        fixture.thread(second, title="code delegate", parent=worker(1), turns=(("completed", 45, 35),))
        fixture.write_store()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": worker(1), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "code delegate", "thread": first, "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "worker", "thread": worker(2), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "3", "role": "worker", "thread": worker(3), "start": stamp, "end": ""},
        ])
        fixture.write()
        summary = "attempts 1-2: 2 workers, 2 code delegates, all ended"
        current = ["attempt 3 · worker · open"]
        payload = ["attempt 1 · worker · ended", "attempt 1 · code delegate · ended", "attempt 2 · worker · ended"]
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1:at + 3], ["    " + summary, *["    " + phrase for phrase in current]])
        document = self.document()
        data = data_of(document)
        self.assertEqual([line for _, rows in data.get("L", []) for line in rows], current)
        folded = data.get("E", [])
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0][1], summary)
        self.assertEqual(folded[0][2], payload)
        lanes = data["G"][0][1]
        covered = [(lanes[index][0], lanes[index][1]) for _, idxs in data["R"] for index in idxs]
        self.assertEqual(sorted(covered), [(0, "earlier worker"), (0, "earlier worker"), (1, "code delegate"), (1, "code delegate")])
        for ident in (worker(1), worker(2), worker(3), first, second):
            self.assertNotIn(ident, document)
            self.assertNotIn(ident, "\n".join(lines))
