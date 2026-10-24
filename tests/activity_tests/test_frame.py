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
        self.assertEqual((MOD["height"](fitted), sum(len(one.rows) for one in fitted.groups)), (1960, 21))
        data = data_of(self.document())
        self.assertEqual((sum(len(lines) for _, lines in data["G"]), len([each for each in data["I"] if each[5]])), (21, 8))
        self.assertEqual(data["N"], [
            "35 sub-agents that are done or stopped are shown as 5 summary rows.",
            "27 merged or dropped work items with 301 agents are not shown. Use a larger --max-bytes to see more.",
            "48 more agents are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            "1 more work item in flight is not listed.",
            "112 agents are grouped by the name of the request that started them."])

    def test_fit_with_the_frame_keeps_each_fixture_page_at_most_1960_tall_and_16000_bytes_with_its_totals_its_legend_and_every_agent_shown_or_counted(self):
        for build, agents, tall in ((typical, 50, 1452), (busy, 400, 1960), (day, 400, 1935), (extreme, 3000, 1926)):
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

    def test_framed_charges_the_heading_of_a_group_that_holds_an_earlier_row_so_the_cut_fits_the_limit(self):
        built = page(
            group(item("A"), replace(row("a0"), earlier=True), row("a1"), row("a2")),
            group(item("B"), replace(row("b0"), earlier=True), row("b1"), row("b2")),
            group(item("C"), row("c1"), row("c2"), row("c3")),
            group(item("D"), row("d1"), row("d2"), row("d3")))
        self.assertEqual(MOD["height"](built), 713)
        # Deleting `left -= GROUP_PX * len(headed)` makes the cut 715 against 712.
        self.assertLessEqual(MOD["height"](MOD["framed"](built, 712)), 712)

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

    def test_a_ledger_over_the_frame_after_every_group_is_removed_is_trimmed_to_the_frame(self):
        fixture = self.fixture
        fixture.coordinator()
        seats = {}
        for number in range(8):
            fixture.unit(f"D{number}", "in-progress", "Activity page", thread=worker(number + 1))
            fixture.thread(worker(number + 1), title=f"D{number} worker", turns=(("completed", 10, 9),))
            seats[f"D{number}"] = [f"model-{number}-{record}" for record in range(12)]
        fixture.write_store()
        stamp = fixture.stamp(10)
        write_threads(fixture, [{"at": stamp, "dish": f"D{number}", "attempt": "1", "role": "worker",
                                 "thread": worker(1000 + number * 12 + record), "start": stamp, "end": stamp,
                                 "seat": seats[f"D{number}"][record]}
                                for number in range(8) for record in range(12)])
        unfolded = page_of(fixture)
        self.assertEqual(MOD["line_count"](unfolded), 96)
        self.assertGreater(MOD["height"](unfolded), 1960)
        cut = MOD["framed"](unfolded, 1960)
        self.assertEqual(len(cut.groups), 0)
        self.assertLessEqual(MOD["height"](cut), 1960)
        self.assertEqual(cut.hidden.cut_lines, 96 - MOD["line_count"](cut))
        # Each work item keeps its newest lines, so no work item loses its ledger to make room.
        for one in MOD["items_of"](cut):
            self.assertEqual([line.seat for line in one.lines], seats[one.id][-len(one.lines):])
            self.assertGreater(len(one.lines), 0)
        fitted, document = MOD["fit"](unfolded, 15999, CLEAN, 1960)
        self.assertLessEqual(MOD["height"](fitted), 1960)
        self.assertLess(len(document.encode()), 16000)
        self.assertGreater(fitted.hidden.cut_lines, 0)
        # Each work item on the fitted page keeps its newest current lines.
        for one in MOD["items_of"](fitted):
            self.assertEqual([line.seat for line in one.lines],
                             seats[one.id][-len(one.lines):])

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

    def test_reclaim_does_not_make_fit_keep_fewer_groups_than_the_base_at_the_byte_budget_boundary(self):
        fixture = self.fixture
        for number in range(20):
            fixture.unit(f"D{number}", "in-progress", "x" * 340, thread=worker(number + 1))
            fixture.thread(worker(number + 1), title=f"D{number} worker", turns=(("completed", 10, 9),))
        page = page_of(fixture)
        fitted, document = MOD["fit"](page, 15999, CLEAN, 1960)
        # The last fold before the cap renders 16073 bytes, over the 15999-byte budget and over the 1960-pixel frame, so its frame cuts run before the cap.
        self.assertEqual((len(fitted.groups), MOD["height"](fitted), len(document.encode())), (19, 1888, 15813))
        shifted, shifted_document = MOD["fit"](page, 16117, CLEAN, 1960)
        # The reclaimed cut of twenty groups renders at 16118 bytes, one over the 16117-byte budget, so fit keeps the cut before reclaim.
        self.assertEqual((len(shifted.groups), MOD["height"](shifted), len(shifted_document.encode())), (19, 1888, 15813))

    def test_fit_tries_the_fold_pages_frame_cuts_when_the_trimmed_pages_frame_cuts_stay_over_budget(self):
        fixture = self.fixture
        for number in range(10):
            fixture.unit(f"D{number}", "in-progress", "x" * 800, thread=worker(number + 1))
            fixture.thread(worker(number + 1), title=f"D{number} worker", turns=(("completed", 10, 9),))
        fixture.write_store()
        stamp = fixture.stamp(10)
        write_threads(fixture, [{"at": stamp, "dish": f"D{number}", "attempt": "1", "role": "worker",
                                 "thread": worker(1000 + number * 20 + record), "start": stamp, "end": stamp,
                                 "seat": f"model-{number}-{record}"}
                                for number in range(10) for record in range(4)])
        page = page_of(fixture)
        trimmed, _, fits = MOD["within_budget"](page, 15999, CLEAN, MOD["render_html"], ledger=MOD["newest_ledger"])
        self.assertEqual((fits, MOD["height"](page), MOD["height"](trimmed)), (False, 2062, 1374))
        # The trimmed page renders 16653 bytes, over the 15999-byte budget, but its height fits the 1960-pixel frame, so its frame cuts keep all ten groups and stay over budget. The fold page's frame cut keeps eight groups.
        fitted, document = MOD["fit"](page, 15999, CLEAN, 1960)
        self.assertEqual((len(fitted.groups), MOD["height"](fitted), len(document.encode())), (8, 1216, 15065))
