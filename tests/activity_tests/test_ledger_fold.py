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
class LedgerFoldTest(OutputCase):
    def test_byte_budget_quiet_fold_keeps_every_earlier_family_inside_the_attempt_fold(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Activity page", thread=worker(3), pr="https://example.test/o/r/pull/7")
        fixture.retired("D7", worker(1))
        fixture.retired("D7", worker(2))
        fixture.thread(worker(3), title="D7 worker", turns=(("running", 4.5, None),))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.thread(worker(2), turns=(("completed", 70, 60),))
        for number in range(100):
            child = delegated(worker(1), f"code-delegate-{number}")
            fixture.thread(child, title="code delegate", parent=worker(1), turns=(("completed", 50, 40),))
        fixture.write_store()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": worker(1), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "worker", "thread": worker(2), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "3", "role": "worker", "thread": worker(3), "start": stamp, "end": ""},
        ])
        fixture.write()
        summary = "attempts 1-2: 2 workers, 100 code delegates, all ended"
        document = self.document("--max-bytes", "16000")
        data = data_of(document)
        self.assertEqual([fold[1] for fold in data.get("E", [])], [summary])
        lanes = data["G"][0][1]
        covered = {index for _, idxs in data["R"] for index in idxs}
        self.assertEqual(sorted(lanes[index][1] for index in covered), ["100 sub-agents", "earlier worker", "earlier worker"])
        self.assertEqual([lanes[index][1] for index in range(len(lanes)) if index not in covered], ["worker"])
        text = self.out("--text", "--max-bytes", "16000")
        self.assertIn("    " + summary, text)
        lines = text.split("\n")
        for heading in ("Running now", "Queued", "Failed"):
            if heading in lines:
                block = lines[lines.index(heading) + 1:]
                block = block[:next((at for at, line in enumerate(block) if line and not line.startswith(" ")), len(block))]
                for line in block:
                    self.assertNotIn("code delegate", line)
                    self.assertNotIn("sub-agents", line)
                    self.assertNotIn("earlier worker", line)
    def test_store_without_a_thread_table_folds_earlier_lanes_with_counts_and_state_only(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Activity page", thread=worker(3), pr="https://example.test/o/r/pull/7")
        fixture.retired("D7", worker(1))
        fixture.retired("D7", worker(2))
        fixture.thread(worker(3), title="D7 worker", turns=(("running", 4.5, None),))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.thread(worker(2), turns=(("completed", 70, 60),))
        fixture.write()
        summary = "2 earlier workers, all ended"
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1], "    " + summary)
        document = self.document()
        data = data_of(document)
        self.assertNotIn("L", data)
        folded = data.get("E", [])
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0][1], summary)
        self.assertEqual(folded[0][2], [])
        lanes = data["G"][0][1]
        covered = [(lanes[index][0], lanes[index][1]) for _, idxs in data["R"] for index in idxs]
        self.assertEqual(covered, [(0, "earlier worker"), (0, "earlier worker")])
        for ident in (worker(1), worker(2), worker(3)):
            self.assertNotIn(ident, document)
            self.assertNotIn(ident, "\n".join(lines))
    def test_single_attempt_with_no_earlier_family_has_no_fold(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [{"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": "opaque=single-worker", "start": stamp, "end": ""}])
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1], "    attempt 1 · worker · open")
        document = self.document()
        data = data_of(document)
        self.assertEqual([line for _, rows in data.get("L", []) for line in rows], ["attempt 1 · worker · open"])
        self.assertNotIn("attempts", document)
        self.assertNotIn('"E":', document)
        self.assertNotIn('"R":', document)
        self.assertNotIn("opaque=", document)
    def test_roles_match_the_store_role_tuple_or_display_order_drifs(self):
        import ast
        b = SCRIPT.parent / "brigade_lib"
        found = [l for p in [b / "store.py", *(b / "_store").glob("*.py")] for l in p.read_text().split("\n") if l.startswith("THREAD_ROLES = ")]
        self.assertEqual(len(found), 1)
        store_roles = ast.literal_eval(found[0].split("=", 1)[1].strip())
        self.assertEqual(MOD["ROLES"], store_roles)
        self.assertEqual(MOD["ROLES"], ("worker", "fix worker", "reviewer", "panel member", "code delegate", "claims check", "architect runner"))
