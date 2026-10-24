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

    def test_model_names_come_from_each_row_with_counts_count_desc_then_name(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), model="model-b", turns=(("completed", 90, 80),))
        for number, model in enumerate(("model-a", "model-a", "model-a", "model-b", "model-c", "model-d", "model-e")):
            fixture.thread(native(number), model=model, parent=worker(1), delegation=("completed", 70, 60))
        page = page_of(fixture)
        self.assertEqual(page.legend, (("model-a", 3), ("model-b", 2), ("model-c", 1), ("model-d", 1), ("model-e", 1)))
        self.assertEqual(sorted({row.model for row in page.groups[0].rows}), ["model-a", "model-b", "model-c", "model-d", "model-e"])

    def test_same_model_different_providers_share_lane_color_not_provider_color(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), provider="claudeAgent", model="model-a", turns=(("completed", 90, 80),))
        fixture.thread(delegated(worker(1), "helper-task"), provider="codex", model="model-a", parent=worker(1), turns=(("completed", 70, 60),))
        page = page_of(fixture)
        self.assertEqual(page.legend, (("model-a", 2),))
        data = MOD["wire"](page)
        self.assertEqual(data["P"], [["model-a", MOD["model_slot"]("model-a"), 2]])
        self.assertEqual({line[3] for _, lines in data["G"] for line in lines}, {MOD["model_slot"]("model-a")})

    def test_different_models_with_different_slots_get_different_lane_colors_not_one_color(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), model="model-a", turns=(("completed", 90, 80),))
        fixture.thread(delegated(worker(1), "helper-task"), model="model-b", parent=worker(1), turns=(("completed", 70, 60),))
        page = page_of(fixture)
        self.assertEqual(page.legend, (("model-a", 1), ("model-b", 1)))
        self.assertNotEqual(MOD["model_slot"]("model-a"), MOD["model_slot"]("model-b"))
        data = MOD["wire"](page)
        self.assertEqual({line[3] for _, lines in data["G"] for line in lines}, {MOD["model_slot"]("model-a"), MOD["model_slot"]("model-b")})

    def test_same_model_keeps_slot_across_model_sets_not_legend_index(self):
        first = Fixture(self.root / "first")
        first.unit("D7", thread=worker(1))
        first.thread(worker(1), model="model-a", turns=(("completed", 90, 80),))
        first.thread(native(1), model="model-a", parent=worker(1), delegation=("completed", 70, 60))
        first.thread(native(2), model="model-b", parent=worker(1), delegation=("completed", 70, 60))
        second = Fixture(self.root / "second")
        second.unit("D7", thread=worker(1))
        second.thread(worker(1), model="model-a", turns=(("completed", 90, 80),))
        second.thread(native(1), model="model-b", parent=worker(1), delegation=("completed", 70, 60))
        second.thread(native(2), model="model-b", parent=worker(1), delegation=("completed", 70, 60))
        first_page, second_page = page_of(first), page_of(second)
        self.assertEqual(first_page.legend, (("model-a", 2), ("model-b", 1)))
        self.assertEqual(second_page.legend, (("model-b", 2), ("model-a", 1)))
        first_data, second_data = MOD["wire"](first_page), MOD["wire"](second_page)
        self.assertEqual([slot for name, slot, _ in first_data["P"] if name == "model-a"], [MOD["model_slot"]("model-a")])
        self.assertEqual([slot for name, slot, _ in second_data["P"] if name == "model-a"], [MOD["model_slot"]("model-a")])
        first_slots = {line[3] for _, lines in first_data["G"] for line in lines if first_data["M"][line[2]] == "model-a"}
        second_slots = {line[3] for _, lines in second_data["G"] for line in lines if second_data["M"][line[2]] == "model-a"}
        self.assertEqual((first_slots, second_slots), ({MOD["model_slot"]("model-a")}, {MOD["model_slot"]("model-a")}))

    def test_hidden_and_missing_models_share_other_slot_no_named_model_uses_it(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), model="model-a", turns=(("completed", 90, 80),))
        fixture.thread(native(1), model=worker(2), parent=worker(1), delegation=("completed", 70, 60))
        fixture.thread(native(2), model="claudeAgent/", parent=worker(1), delegation=("completed", 70, 60))
        page = page_of(fixture)
        self.assertEqual(page.legend, (("other model", 2), ("model-a", 1)))
        data = MOD["wire"](page)
        for name, slot, _ in data["P"]:
            self.assertEqual(slot, MOD["model_slot"](name))
        self.assertEqual(MOD["model_slot"]("other model"), MOD["OTHER_SLOT"])
        for name in ("model-a", "model-b", "model-c", "model-d", "model-e"):
            self.assertNotEqual(MOD["model_slot"](name), MOD["OTHER_SLOT"])
        other_lines = [line for _, lines in data["G"] for line in lines if data["M"][line[2]] == "other model"]
        self.assertEqual(len(other_lines), 2)
        self.assertEqual({line[3] for line in other_lines}, {MOD["OTHER_SLOT"]})

    def test_parent_with_no_turn_in_the_window_has_a_row_with_no_bar_and_only_its_child_is_counted(self):
        fixture = self.fixture
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), provider="codex", turns=(("completed", 900, 890),))
        fixture.thread(delegated(worker(1), "helper-task"), title="helper", parent=worker(1), turns=(("failed", 20, 10),))
        page = page_of(fixture)
        parent, child = page.groups[0].rows
        self.assertEqual((parent.depth, parent.label, parent.status.value, parent.seconds, parent.spans, parent.stands_for), (0, "worker", "done", 0, (), 0))
        self.assertEqual((child.depth, child.label, [(span.x, span.w) for span in child.spans], child.stands_for), (1, "helper", [(889, 55)], 1))
        self.assertEqual((page.totals, page.legend), (MOD["Totals"](running=0, agents=0, subagents=1, failed=1), (("model-a", 1),)))
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
