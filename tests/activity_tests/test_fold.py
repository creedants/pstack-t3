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
        self.assertEqual((summary.status.value, summary.seconds, summary.open_seconds, summary.model), ("done", 180, None, "model-a"))
        self.assertEqual([(span.x, span.w, span.status.value) for span in summary.spans], [(167, 11, "done"), (183, 3, "done"), (189, 3, "failed")])
        self.assertEqual((folded.fold, folded.totals, folded.legend), (1, unfolded.totals, unfolded.legend))
        self.assertEqual(MOD["notes"](folded, MOD["items_of"](folded)), ["3 merged or dropped work items with no agent running, queued, or waiting are each shown as one row."])
    def test_summary_row_has_the_union_of_the_bars_that_are_not_failed_as_done_and_then_the_union_of_the_failed_bars(self):
        rows = [row("a", spans=((0, 10, "done"), (40, 10, "failed"), (100, 10, "stopped"))), row("b", spans=((5, 10, "unknown"), (45, 10, "failed"), (300, 5, "failed"))),
                row("c", spans=((104, 20, "done"),), model="model-b")]
        summary = MOD["summary"](rows, 1, "3 sub-agents")
        self.assertEqual([(span.x, span.w, span.status.value) for span in summary.spans],
                         [(0, 15, "done"), (100, 24, "done"), (40, 15, "failed"), (300, 5, "failed")])
        self.assertEqual((summary.depth, summary.label, summary.model, summary.seconds, summary.stands_for), (1, "3 sub-agents", "", 180, 3))
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
    def test_synthesized_summary_rows_keep_the_earlier_flag_through_the_budget_transforms(self):
        lanes = [replace(row("earlier worker"), earlier=True), replace(row("a", depth=1), earlier=True), replace(row("b", depth=1), earlier=True)]
        folded = MOD["fold_quiet_subagents"](page(group(item("D7"), *lanes)))
        self.assertEqual([(each.label, each.earlier) for each in folded.groups[0].rows], [("earlier worker", True), ("2 sub-agents", True)])
        mixed = MOD["fold_quiet_subagents"](page(group(item("D7"), *lanes[:2], row("c", depth=1))))
        self.assertEqual([(each.label, each.earlier) for each in mixed.groups[0].rows], [("earlier worker", True), ("2 sub-agents", False)])
        for grown in (folded, mixed):
            for transform in (MOD["cap_everything"], lambda grown: MOD["framed"](grown, 1)):
                carried = transform(grown)
                left = [each for one in carried.groups for each in one.rows]
                for each in [each for one in grown.groups for each in one.rows if each.earlier]:
                    self.assertIn(each, left)
        finished = MOD["fold_finished_items"](page(group(item("D7", in_flight=False), *lanes)))
        self.assertEqual([(each.label, each.earlier) for each in finished.groups[0].rows], [("3 agents", True)])
    def test_folds_3_to_10_keep_24_16_12_8_6_4_2_and_0_finished_units_and_count_each_dropped_unit_once(self):
        folded = page(group(item("D99"), row("worker")), *[group(item(f"D{number}", in_flight=False), row("2 agents", stands_for=2)) for number in range(30)])
        left = []
        for fold in MOD["FOLDS"][2:-1]:
            folded = fold(folded)
            left.append((len(folded.groups) - 1, folded.hidden.dropped_items, folded.hidden.dropped_agents))
        self.assertEqual(left, [(24, 6, 12), (16, 14, 28), (12, 18, 36), (8, 22, 44), (6, 24, 48), (4, 26, 52), (2, 28, 56), (0, 30, 60)])
        self.assertEqual((folded.fold, len(MOD["FOLDS"])), (8, 11))
