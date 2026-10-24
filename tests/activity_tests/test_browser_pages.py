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
from tests.activity_tests.browser import BUDGET, FRAME, MACOS, PINNED, PROBE_TIMEOUT, RENDER_TIMEOUT, TITLE, busy_page, confined_run, measure, select_browser, wide_page
DARWIN_PROBE_TIMEOUT = 120
class BrowserCase(unittest.TestCase):
    def setUp(self):
        """Find a headless browser, or skip outside CI when none starts.

        The first probe allows DARWIN_PROBE_TIMEOUT seconds on Darwin, where a first launch can take more than PROBE_TIMEOUT seconds.
        """
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        required = os.environ.get("CI") == "true"
        self.browser, unavailable = select_browser(self.root, timeout=DARWIN_PROBE_TIMEOUT if MACOS else PROBE_TIMEOUT)
        if self.browser is None and required:
            self.browser, unavailable = select_browser(self.root, timeout=60)
        if self.browser is None:
            reason = f"no working headless browser: {'; '.join(unavailable)}"
            if required:
                self.fail(reason)
            self.skipTest(reason)
class RenderHeightTest(BrowserCase):
    def test_height_of_each_page_drawn_at_390_pixels_is_not_above_the_height_the_model_reserves_or_the_frame(self):
        # The four pages the reviewer of pull request 192 measured, each with the reserved height, the timeline rows, and the flight groups the fit leaves on it.
        cases = (
            ("busy ordinary", busy_page(self.root / "ordinary", [f"Ship {number}" for number in range(1, 37)]), 1936, 11, 8),
            ("busy wide", busy_page(self.root / "wide", ["W" * 36] * 36), 1960, 9, 8),
            ("new short", wide_page([f"Ship {number}" for number in range(1, 9)]), 1886, 16, 8),
            ("new wide", wide_page(["W" * 36] * 8), 1950, 12, 8),
        )
        for name, unfolded, reserved, rows, groups in cases:
            with self.subTest(name):
                fitted, _ = MOD["fit"](unfolded, BUDGET, CLEAN, FRAME)
                drawn = [each for one in fitted.groups for each in one.rows]
                self.assertEqual(MOD["height"](fitted), reserved)
                self.assertEqual((len(drawn), len(fitted.flight.groups)), (rows, groups))
                height = measure(self.browser, MOD["render_html"](fitted, CLEAN), self.root)
                self.assertLessEqual(height, reserved)
                self.assertLessEqual(height, MOD["FRAME_HEIGHT"])
class LiveAttemptFoldTest(BrowserCase):
    def document_with_earlier_fold(self):
        fixture = Fixture(str(self.root / "live"))
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Activity page", thread=worker(3))
        fixture.retired("D7", worker(1))
        fixture.retired("D7", worker(2))
        fixture.thread(worker(3), title="D7 worker", turns=(("running", 4.5, None),))
        fixture.thread(worker(1), turns=(("completed", 90, 80),))
        fixture.thread(worker(2), turns=(("completed", 70, 60),))
        fixture.write_store()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": worker(1), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "worker", "thread": worker(2), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "3", "role": "worker", "thread": worker(3), "start": stamp, "end": ""},
        ])
        fixture.write_database()
        result = in_process.invoke(fixture.command())
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        return result.stdout
    def test_live_fold_script_on_the_static_document_shows_earlier_rows_when_attempt_details_open_and_hides_them_on_close(self):
        probe = "".join((
            "<script>", MOD["LIVE_FOLD"],
            "fold();",
            "var details = document.querySelector('#o .tl .ldg details');",
            "if (!details) { document.title = 'no attempt details'; } else {",
            "var rows = Array.prototype.slice.call(document.querySelectorAll('#o .tl .row.earlier'));",
            "var disp = function () { return rows.map(function (r) { return getComputedStyle(r).display; }); };",
            "var before = disp();",
            "details.open = true;",
            "details.dispatchEvent(new Event('toggle'));",
            "var after = disp();",
            "details.open = false;",
            "details.dispatchEvent(new Event('toggle'));",
            "var shut = disp();",
            "document.title = JSON.stringify({before: before, after: after, shut: shut});",
            "}",
            "</script>",
        ))
        target = Path(self.root) / "live.html"
        target.write_text(self.document_with_earlier_fold().replace("<div id=o></div>", f"<div id=o></div>{PINNED}") + probe)
        drawn = confined_run(self.browser, target, self.root, "e", RENDER_TIMEOUT)
        seen = json.loads(html.unescape(TITLE.search(drawn).group(1)))
        self.assertEqual(seen, {"before": ["none", "none"], "after": ["grid", "grid"], "shut": ["none", "none"]})
    def test_two_attempt_summaries_that_print_one_work_item_id_do_not_keep_separate_open_states(self):
        fixture = Fixture(str(self.root / "shared"))
        fixture.coordinator()
        for offset, ident in ((0, "mcp:shared1"), (10, "mcp:shared2")):
            fixture.unit(ident, "in-progress", "Activity page", thread=worker(offset + 3))
            fixture.retired(ident, worker(offset + 1))
            fixture.retired(ident, worker(offset + 2))
            fixture.thread(worker(offset + 3), title=f"worker {offset}", turns=(("running", 4.5, None),))
            fixture.thread(worker(offset + 1), turns=(("completed", 90, 80),))
            fixture.thread(worker(offset + 2), turns=(("completed", 70, 60),))
        fixture.write_store()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": ident, "attempt": str(attempt), "role": "worker", "thread": worker(offset + attempt), "start": stamp, "end": stamp}
            for offset, ident in ((0, "mcp:shared1"), (10, "mcp:shared2"))
            for attempt in (1, 2)
        ] + [
            {"at": stamp, "dish": ident, "attempt": "3", "role": "worker", "thread": worker(offset + 3), "start": stamp, "end": ""}
            for offset, ident in ((0, "mcp:shared1"), (10, "mcp:shared2"))
        ])
        fixture.write_database()
        result = in_process.invoke(fixture.command())
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        probe = "".join((
            "<script>", MOD["LIVE_FOLD"], "</script>",
            "<script>",
            "fold();",
            "var details = [].slice.call(document.querySelectorAll('#o .ldg details'));",
            "details[0].open = true;",
            "details[0].dispatchEvent(new Event('toggle'));",
            "document.title = JSON.stringify({ids: details.map(function (d) { return d.getAttribute('data-item'); }),",
            " open: details.map(function (d) { return d.open; })});",
            "</script>",
        ))
        target = Path(self.root) / "shared.html"
        target.write_text(result.stdout.replace("<div id=o></div>", f"<div id=o></div>{PINNED}") + probe)
        drawn = confined_run(self.browser, target, self.root, "s", RENDER_TIMEOUT)
        seen = json.loads(html.unescape(TITLE.search(drawn).group(1)))
        self.assertEqual(seen["ids"], ["work item", "work item"])
        # Deleting `if (block) block.open = attemptOpen;` from LIVE_FOLD's apply() leaves [true, false].
        self.assertEqual(seen["open"], [True, True])
    def test_two_groups_that_print_one_work_item_id_fold_independently(self):
        fixture = Fixture(str(self.root / "fold-alone"))
        fixture.coordinator()
        for offset, ident in ((0, "mcp:shared1"), (10, "mcp:shared2")):
            fixture.unit(ident, "in-progress", "Activity page", thread=worker(offset + 3))
            fixture.retired(ident, worker(offset + 1))
            fixture.retired(ident, worker(offset + 2))
            fixture.thread(worker(offset + 3), title=f"worker {offset}", turns=(("running", 4.5, None),))
            fixture.thread(worker(offset + 1), turns=(("completed", 90, 80),))
            fixture.thread(worker(offset + 2), turns=(("completed", 70, 60),))
        fixture.write_store()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": ident, "attempt": str(attempt), "role": "worker", "thread": worker(offset + attempt), "start": stamp, "end": stamp}
            for offset, ident in ((0, "mcp:shared1"), (10, "mcp:shared2"))
            for attempt in (1, 2)
        ] + [
            {"at": stamp, "dish": ident, "attempt": "3", "role": "worker", "thread": worker(offset + 3), "start": stamp, "end": ""}
            for offset, ident in ((0, "mcp:shared1"), (10, "mcp:shared2"))
        ])
        fixture.write_database()
        result = in_process.invoke(fixture.command())
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        # The second fold re-applies state after the click on the same nodes.
        probe = "".join((
            "<script>", MOD["LIVE_FOLD"], "</script>",
            "<script>",
            "fold();",
            "var heads = [].slice.call(document.querySelectorAll('#o .tl .grp'));",
            "if (heads.length < 2) { document.title = JSON.stringify({groups: heads.length}); } else {",
            "var ones = heads.map(function (h) { var o = h.querySelector('.one'); return o ? o.textContent : ''; });",
            "var groupsOf = function () { var out = []; var cur = null; var kids = document.querySelector('#o .tl').children; for (var i = 0; i < kids.length; i++) { var n = kids[i]; if (n.classList.contains('grp')) { cur = {head: n, rows: []}; out.push(cur); } else if (cur && n.classList.contains('row')) cur.rows.push(n); } return out; };",
            "var groups = groupsOf();",
            "groups[0].head.click();",
            "fold();",
            "groups = groupsOf();",
            "var disp = function (r) { return getComputedStyle(r).display; };",
            "var first = groups[0].rows.map(disp);",
            "var second = groups[1].rows.map(disp);",
            "var details = [].slice.call(document.querySelectorAll('#o .ldg details'));",
            "details[0].open = true;",
            "details[0].dispatchEvent(new Event('toggle'));",
            "document.title = JSON.stringify({ones: ones, first: first, second: second,",
            " open: details.map(function (d) { return d.open; })});",
            "}",
            "</script>",
        ))
        target = Path(self.root) / "fold-alone.html"
        target.write_text(result.stdout.replace("<div id=o></div>", f"<div id=o></div>{PINNED}") + probe)
        drawn = confined_run(self.browser, target, self.root, "s", RENDER_TIMEOUT)
        seen = json.loads(html.unescape(TITLE.search(drawn).group(1)))
        self.assertEqual(seen["ones"], ["work item", "work item"])
        self.assertTrue(len(seen["first"]) > 0 and all(each == "none" for each in seen["first"]))
        # Keying the fold by id alone leaves every row of the second group at none.
        self.assertTrue(len(seen["second"]) > 0 and any(each != "none" for each in seen["second"]))
        self.assertEqual(seen["open"], [True, True])
class ProbeSkipTest(unittest.TestCase):
    def test_a_missed_probe_skips_once_locally_on_every_os_and_retries_and_fails_under_ci(self):
        calls = []
        timeouts = []
        def miss(_directory, timeout=None):
            calls.append(os.environ.get("CI", ""))
            timeouts.append(timeout)
            return (None, ["stub missed"])
        subjects = (
            (RenderHeightTest, "test_height_of_each_page_drawn_at_390_pixels_is_not_above_the_height_the_model_reserves_or_the_frame"),
            (LiveAttemptFoldTest, "test_live_fold_script_on_the_static_document_shows_earlier_rows_when_attempt_details_open_and_hides_them_on_close"),
        )
        cases = (("", False, 1, False), ("true", False, 2, True), ("", True, 1, False), ("true", True, 2, True))
        with mock.patch(f"{__name__}.select_browser", miss):
            for subject, method in subjects:
                for ci, macos, tries, failed in cases:
                    with self.subTest(subject=subject.__name__, ci=ci, macos=macos), mock.patch.dict(os.environ, {"CI": ci}), mock.patch(f"{__name__}.MACOS", macos):
                        calls.clear()
                        timeouts.clear()
                        result = unittest.TextTestRunner(stream=io.StringIO()).run(subject(method))
                        self.assertEqual((calls[:], "no working headless browser: stub missed" in (result.failures or result.skipped)[0][1], bool(result.failures), bool(result.skipped)), ([ci] * tries, True, failed, not failed))
                        self.assertEqual(timeouts[0], DARWIN_PROBE_TIMEOUT if macos else PROBE_TIMEOUT)
