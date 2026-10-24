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
class EmitTest(OutputCase):
    """Every string a form emits is one the filter returned. These tests fail when a renderer emits a string another way."""

    def pages(self):
        for build in (empty, named, failed_child, queued_only, busy):
            unfolded = page_of(build(Fixture(self.root / build.__name__)))
            yield build.__name__, unfolded
            yield build.__name__ + " capped", MOD["cap_everything"](unfolded)
            yield build.__name__ + " shortened", MOD["shortened"](unfolded)

    def test_html_data_strings_pass_through_clean_and_each_fixed_shell_excludes_private_words(self):
        shells = set()
        for name, drawn_page in self.pages():
            with self.subTest(name):
                document = MOD["render_html"](drawn_page, tagged)
                raw = DATA.search(document).group(1)
                emitted, given = strings(list(json.loads(raw).values())), strings(list(MOD["wire"](drawn_page).values()))
                self.assertEqual(emitted, [MOD["entities"](tagged(text)) for text in given])
                self.assertEqual(document.count(raw), 1)
                shells.add(re.sub(r"const H=[0-9]+;", "const H=0;", document.replace(raw, "")))
        self.assertLessEqual(len(shells), 2)
        for shell in shells:
            self.assertEqual([word for word in WORDS if word in shell], [])

    def test_every_plain_line_is_one_clean_returned_whole(self):
        for name, drawn_page in self.pages():
            with self.subTest(name):
                lines = MOD["render_text"](drawn_page, same).split("\n")
                self.assertEqual(MOD["render_text"](drawn_page, tagged).split("\n"), [tagged(line) for line in lines])
                self.assertEqual((lines[0][:19], len(lines) > 2), ("Agent activity for ", True))

    def test_each_word_of_the_named_page_is_in_the_document_and_each_but_the_coordinators_model_is_in_the_text(self):
        fixture = named(self.fixture).write()
        document, text = self.document(), self.out("--text")
        self.assertEqual(([word for word in WORDS if word not in document], [word for word in WORDS if word not in text]), ([], ["MODELWORD"]))

    def test_run_gives_both_forms_the_filter_of_the_run(self):
        """ODD, and the store's directory straight after a letter, are private text only to a filter that knows what this run read."""
        fixture = named(self.fixture)
        fixture.retired("IDWORD", ODD)
        fixture.units[0]["summary"] = f"SUMWORD of {ODD} and x{fixture.store} today"
        fixture.write()
        self.assertEqual(CLEAN(fixture.units[0]["summary"]), fixture.units[0]["summary"])
        self.assertEqual(data_of(self.document())["I"][0][1], "SUMWORD of and today")
        self.assertIn("   working   SUMWORD of and today   ", self.out("--text"))

    @unittest.skipUnless(shutil.which("node"), "node is not on PATH")
    def test_renderer_draws_each_string_that_holds_a_word_of_the_named_page_alone_in_a_text_or_an_attribute(self):
        fixture = named(self.fixture).write()
        document = self.document()
        built = NodeRender.render(self, document)
        drawn_strings = [*built["texts"], *built["titles"], *(link[1] for link in built["links"])]
        data = set(strings(list(data_of(document).values())))
        legend = {f"{name} {agents}" for name, _, agents in data_of(document)["P"]}
        self.assertEqual([text for text in drawn_strings if any(word in text for word in WORDS) and text not in data and text not in legend], [])
        self.assertEqual([word for word in WORDS if not any(word in text for text in drawn_strings)], [])
        self.assertEqual(built["texts"][built["texts"].index("Running now") + 1:built["texts"].index("Timeline")], [
            "IDWORD", "worker", "40m", "IDWORD", "PARENTWORD helper", " · under ", "worker", "30m",
            "Queued", "IDWORD", "LABELWORD reader", " · under ", "PARENTWORD helper", "20m"])
class TextTest(OutputCase):
    def test_text_lists_running_agents_work_items_failed_agents_and_notes(self):
        failed_child(self.fixture).write()
        self.assertEqual(self.out("--text"), "\n".join([
            "Agent activity for kit, last 3h",
            "2 running now, 2 agents, 4 sub-agents, 1 failed",
            "Models",
            "  model-a   3",
            "  model-b   2",
            "  model-c   1",
            "Running now",
            "  D7 worker   running for 42m",
            "  D7 architect runner 2   running for 6m   under worker",
            "Work items",
            f"  D7   in review   Agent activity page   1 agent, 3 sub-agents, 74m at work   {PR7}",
            "  D5   merged   Old work   1 agent, 5m at work",
            f"  D6   landing   Queue fix   no activity in this window   {PR6}",
            "  Not tied to a work item   1 sub-agent, 2m at work",
            "Failed",
            "  D7 review   model-c   3m at work",
            "Notes",
            "  1 agent is grouped by the name of the request that started it.",
            "  1 other thread ran in this coordinator's T3 project outside this coordinator.",
            ""]))

    def test_text_and_document_hold_the_same_four_counts(self):
        busy(self.fixture).write()
        self.assertEqual(data_of(self.document())["n"], [5, 36, 364, 3])
        self.assertEqual(self.out("--text").split("\n")[1], "5 running now, 36 agents, 364 sub-agents, 3 failed")

    def test_text_cuts_the_running_work_item_and_failed_lists_and_counts_the_rest(self):
        lines = self.render(busy).split("\n")
        self.assertEqual((lines[2], lines[3], lines[4], lines[10]), ("Models", "  model-a   400", "Running now", "Work items"))
        self.assertEqual(len(lines[5:10]), 5)
        self.assertEqual((len(lines[11:20]), lines[20]), (9, "  and 27 more work items"))
        self.assertEqual((lines[21], len(lines[22:25]), lines[25]), ("Failed", 3, "Notes"))
        cut = MOD["render_text"](page(group(item("D1"), *[row(f"r{n}", status="running", open_seconds=5) for n in range(9)],
                                              *[row(f"f{n}", status="failed") for n in range(5)])), CLEAN).split("\n")
        self.assertEqual((cut[12], cut[20]), ("  and 2 more running agents", "  and 1 more failed agent"))

    def test_ten_work_items_whose_shortened_ids_summaries_states_and_links_are_the_same_are_each_listed_or_counted_in_the_text_at_max_bytes_500000_and_16000(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.meta["restaurant"] = "name " * 10000
        for number in range(10):
            fixture.unit(f"D12345678901234567890-{number}", summary="same", thread=worker(number))
            fixture.thread(worker(number), turns=(("running", 10, None),))
        fixture.write()
        whole = [f"  D12345678901234567890-{number}   working   same   1 agent, 10m at work" for number in range(9)]
        for budget, listed in (("500000", whole), ("16000", ["  D12345678…   working   same   1 agent, 10m at work"] * 9)):
            with self.subTest(budget):
                lines = self.out("--text", "--max-bytes", budget).split("\n")
                at = lines.index("Work items")
                self.assertEqual((lines[1], lines[at + 1:at + 12]), ("10 running now, 10 agents, 0 sub-agents, 0 failed", [*listed, "  and 1 more work item", ""]))

    def test_the_document_at_max_bytes_16000_gives_each_of_ten_work_items_with_the_same_summary_state_and_link_its_own_entry_or_counts_it(self):
        fixture = self.fixture
        fixture.coordinator()
        fixture.meta["restaurant"] = "name " * 10000
        for number in range(10):
            fixture.unit(f"D12345678901234567890-{number}", summary="same", thread=worker(number))
            fixture.thread(worker(number), turns=(("running", 10, None),))
        fixture.write()
        data = data_of(self.document("--max-bytes", "16000"))
        self.assertEqual((data["I"], [group[0] for group in data["G"]], data["N"][0]), (
            [["D12345678901234567890-0", "same", "working", "go", "", 1],
             ["D12345678901234567890-1", "same", "working", "go", "", 1],
             ["D12345678901234567890-2", "same", "working", "go", "", 1],
             ["D12345678901234567890-3", "same", "working", "go", "", 1],
             ["D12345678901234567890-4", "same", "working", "go", "", 1],
             ["D12345678901234567890-5", "same", "working", "go", "", 1],
             ["D12345678901234567890-6", "same", "working", "go", "", 1],
             ["D12345678901234567890-7", "same", "working", "go", "", 1],
             ["D12345678901234567890-8", "same", "working", "go", "", 0],
             ["D12345678901234567890-9", "same", "working", "go", "", 0]],
            [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], "2 more work items in flight are not listed."))

    def test_a_running_line_names_no_model_of_the_store(self):
        failed_child(self.fixture).write()
        lines = self.out("--text").split("\n")
        models = set(data_of(self.document())["M"])
        self.assertEqual([line for line in lines[lines.index("Running now") + 1:lines.index("Work items")] if any(model in line for model in models)], [])

    def render(self, build):
        build(self.fixture).write()
        return self.out("--text")
class WindowTest(OutputCase):
    def test_window_is_labelled_by_its_whole_seconds_as_hours_minutes_and_seconds_in_both_forms(self):
        fixture = one_running(self.fixture).write()
        for hours, seconds, words in (("0.01", 36, "36s"), ("0.25", 900, "15m"), ("1", 3600, "1h"), ("1.51", 5436, "1h 30m 36s"), ("3", 10800, "3h"), ("168", 604800, "168h")):
            with self.subTest(hours):
                self.assertEqual(self.out("--text", "--hours", hours).split("\n")[0], f"Agent activity for kit, last {words}")
                self.assertEqual(data_of(self.document("--hours", hours))["w"][1:], [seconds, words])
class QueuedTest(OutputCase):
    def test_queued_turn_is_listed_under_queued_is_not_counted_running_and_keeps_a_queued_bar(self):
        for status in ("queued", "preparing"):
            with self.subTest(status):
                fixture = self.fixture = queued_only(Fixture(self.root / status))
                put(fixture.turns, 1, 1, status)
                fixture.write()
                self.assertEqual(self.out("--text"), "\n".join([
                    "Agent activity for kit, last 3h",
                    "0 running now, 1 agent, 0 sub-agents, 0 failed",
                    "Models",
                    "  model-a   1",
                    "Queued",
                    "  D7 worker   queued for 5m",
                    "Work items",
                    "  D7   working   Activity page   1 agent, 0s at work",
                    ""]))
                data = data_of(self.document())
                self.assertEqual((data["n"], data["S"][1], data["G"][0][1][0][4:]), ([0, 1, 0, 0], "queued", [1, "0s", [972, 28, 5], 1, "5m"]))

    def test_agent_with_a_running_turn_and_a_queued_turn_is_counted_running_and_keeps_both_bars(self):
        fixture = queued_only(self.fixture)
        fixture.turns[1:] = [(worker(1), "running", fixture.stamp(10), None), (worker(1), "queued", fixture.stamp(5), None)]
        fixture.write()
        lines = self.out("--text").split("\n")
        self.assertEqual(lines[1:7], ["1 running now, 1 agent, 0 sub-agents, 0 failed", "Models", "  model-a   1", "Running now", "  D7 worker   running for 10m", "Work items"])
        data = data_of(self.document())
        self.assertEqual((data["n"], data["G"][0][1][0][4:]), ([1, 1, 0, 0], [0, "10m", [944, 56, 1, 972, 28, 5], 1, "10m"]))

    def test_providers_own_sub_agent_whose_open_delegation_is_queued_is_listed_under_queued(self):
        fixture = queued_only(self.fixture)
        put(fixture.turns, 1, 1, "running")
        fixture.thread(native(1), title="/root/spec_review", parent=worker(1), delegation=("queued", 2, None))
        fixture.write()
        lines = self.out("--text").split("\n")
        self.assertEqual(lines[1:8], ["1 running now, 1 agent, 1 sub-agent, 0 failed", "Models", "  model-a   2", "Running now", "  D7 worker   running for 5m", "Queued",
                                      "  D7 spec_review   queued for 2m   under worker"])
