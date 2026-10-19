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
from tests import test_activity
from tests.activity_tests.support import ActivityCase, CHANGED, COORDINATOR, ELSEWHERE, Fixture, FROZEN, GUIDE, LOG_COLUMNS, MARKERS, MOD, NOW, OutputCase, PROJECT, PR6, PR7, ROOT, SCHEMA, SCRIPT, SKILL, UNIT_COLUMNS, VIEWS, busy, day, delegated, empty, extreme, failed_child, forms, frozen_bytes, live_listing, listing, native, one_running, populate, put, queued_only, setUpModule, tree, typical, widen_items, worker, write_goal_names, write_goals, write_threads
from tests.activity_tests.pages import BARE, CLEAN, DATA, FilterCase, HOSTILE, HOSTILE_DRAWN, HOSTILE_WRITTEN, NEEDLES, NodeRender, ODD, ORDINARY, PROGRAM, REPORT, REQUEST, ROW_FIELDS, LINK_FIELDS, ADDRESS, STORE_WORDS, STUB, UUID_TEXT, WORDS, agent, agents_shown, data_of, declarations, drawn, group, hostile, item, labels, named, ordinary, page, page_of, painted, row, same, skill_section, store_of, strings, tagged, T3_REFUSAL, STORE_REFUSAL
from tests.activity_tests import prior_one, prior_two
PRIOR_ACTIVITY = prior_one.TEXT + prior_two.TEXT
class PriorPageTest(OutputCase):
    def test_a_store_from_before_kinds_keeps_the_prior_data_payload_except_model_colors_and_legend_and_keeps_no_unplaceable_labels(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        script = Path(directory.name) / "activity.py"
        script.write_bytes(zlib.decompress(base64.b64decode(PRIOR_ACTIVITY)))
        fixture = one_running(self.fixture).write()
        self.assertEqual([len(line.split("\t")) for line in (fixture.store / "dishes.tsv").read_text().splitlines()], [15, 15, 15])
        self.assertFalse((fixture.store / "goals.tsv").exists())
        # The style and script differ from the prior build. The data element holds the values the page builds its
        # strings from, not every drawn string, so this compares those values only, except P and the lane colors which now come from models.
        html = fixture.stdout_bytes()
        current = json.loads(DATA.search(html.decode()).group(1))
        prior = json.loads(DATA.search(frozen_bytes(script, fixture).decode()).group(1))
        self.assertEqual(current["P"], [["model-a", MOD["model_slot"]("model-a"), 1]])
        self.assertEqual(prior["P"], [["Claude", 1, 1]])
        self.assertEqual([[index, [line[:3] + line[4:] for line in lines]] for index, lines in current["G"]],
                         [[index, [line[:3] + line[4:] for line in lines]] for index, lines in prior["G"]])
        self.assertEqual([line[3] for _, lines in current["G"] for line in lines], [MOD["model_slot"]("model-a")])
        self.assertEqual({key: current[key] for key in current if key not in ("P", "G", "C")},
                         {key: prior[key] for key in prior if key not in ("P", "G", "C")})

        second = one_running(Fixture(self.root / "outside")).write()
        (second.store / "goals.tsv").write_text(
            "id\tat\tstate\tname\tpurpose\tdone\tshare\tclosed\n"
            "G1\t2026-10-10T11:00:00.000Z\topen\tUnused goal name\tpurpose text\tdone test\t1\t\n")
        table = second.store / "dishes.tsv"
        header, *rows = table.read_text().splitlines()
        table.write_text("\n".join([header, *("\t".join([row, "nope", "G9"]) for row in rows)]) + "\n")
        html, text = second.stdout_bytes(), second.stdout_bytes("--text")
        current = json.loads(DATA.search(html.decode()).group(1))
        prior = json.loads(DATA.search(frozen_bytes(script, second).decode()).group(1))
        self.assertEqual(current["P"], [["model-a", MOD["model_slot"]("model-a"), 1]])
        self.assertEqual(prior["P"], [["Claude", 1, 1]])
        self.assertEqual([[index, [line[:3] + line[4:] for line in lines]] for index, lines in current["G"]],
                         [[index, [line[:3] + line[4:] for line in lines]] for index, lines in prior["G"]])
        self.assertEqual([line[3] for _, lines in current["G"] for line in lines], [MOD["model_slot"]("model-a")])
        self.assertEqual({key: current[key] for key in current if key not in ("P", "G", "C")},
                         {key: prior[key] for key in prior if key not in ("P", "G", "C")})
        for output in (html, text):
            self.assertNotIn(b"Unassigned", output)
            self.assertNotIn(b"Unused goal name", output)
            self.assertNotIn(b"nope", output)
        self.assertNotIn(" \u00b7 ".encode(), text)
        self.assertNotIn(" \u00b7 ", DATA.search(html.decode()).group(1))
