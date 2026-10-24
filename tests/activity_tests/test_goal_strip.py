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
            "  Ship the parser · 1 working",
            "    D1   working   Parser work   no activity in this window",
            "  Retire the queue · 2 landing",
            "    D2   landing   Queue one   no activity in this window",
            "    D3   landing   Queue two   no activity in this window",
            "  Unassigned · 1 in review, 1 blocked",
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
        self.assertEqual(drawn[-19:], [
            "Ship the parser · 1 working", "D1", "Parser work", "working",
            "Retire the queue · 2 landing", "D2", "Queue one", "landing", "D3", "Queue two", "landing",
            "Unassigned · 1 in review, 1 blocked", "D4", "Loose one", "blocked", "D5", "Loose two", "in review",
            "Work items in flight",
        ])
        for ident in ("D1", "D2", "D3", "D4", "D5"):
            self.assertEqual(drawn.count(ident), 1)

    def test_a_goal_name_is_printed_once_in_the_html_strip_and_the_plain_form(self):
        fixture = one_running(self.fixture).write()
        write_goals(fixture, [("G1", "2026-10-10T11:00:00.000Z", "open", "Ship the parser", "purpose", "done", "1", "")])
        widen_items(fixture, {"D7": ("feature", "G1")})
        html, text = self.document(), self.out("--text")
        drawn = painted(self, html)["texts"]
        self.assertEqual([part for part in drawn if "Ship the parser" in part], ["Ship the parser · 1 working"])
        self.assertEqual([line for line in text.splitlines() if "Ship the parser" in line], ["  Ship the parser · 1 working"])

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
            "  Ship the parser · 1 merged",
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
            "  Ship the parser · 1 merged",
            "    feature   D1   merged   Finished parser   1 agent, 10m at work",
            "  Unassigned · 1 working",
            "    D2   working   Open shelf   no activity in this window",
        ])
        html = self.document()
        self.assertIn("Open shelf", html)
        self.assertNotIn("Ship the parser", html)
        self.assertNotIn("Unassigned", html)
        self.assertNotIn("F", data_of(html))
