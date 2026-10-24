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
class OrdinaryWordsTest(OutputCase):
    def test_store_whose_ids_request_names_summaries_titles_models_and_name_are_ordinary_words_loses_none_of_them_to_the_filter_in_the_text(self):
        fixture = ordinary(self.fixture).write()
        self.assertEqual(self.out("--text"), "\n".join([
            "Agent activity for private auditor, last 3h",
            "3 running now, 2 agents, 4 sub-agents, 1 failed",
            "Models",
            "  451237   1",
            "  code   1",
            "  private auditor   1",
            "  queue   1",
            "  review   1",
            "  src   1",
            "Running now",
            "  code src   running for 10m   under worker",
            "  queue worker   running for 30m",
            "  queue queue   running for 20m",
            "Queued",
            "  451237 review   queued for 5m",
            "Work items",
            "  451237   passed review   451237 private auditor   1 sub-agent, 0s at work",
            "  code   in review   code review of the queue   1 agent, 2 sub-agents, 21m at work",
            "  queue   working   queue src report   1 agent, 1 sub-agent, 50m at work   https://git.example/queue/code/pull/451237",
            "  review   landing   review the src queue   no activity in this window",
            "  src   blocked   src code   no activity in this window",
            "Failed",
            "  code code review   451237   60s at work",
            ""]))

    def test_store_whose_ids_request_names_summaries_titles_models_and_name_are_ordinary_words_loses_none_of_them_to_the_filter_in_the_document(self):
        fixture = ordinary(self.fixture).write()
        data = data_of(self.document())
        self.assertEqual((data["c"], data["M"], data["N"]), ("private auditor", ["model-a", "src", "private auditor", "451237", "review", "code", "queue"], []))
        self.assertEqual(data["I"], [["code", "code review of the queue", "in review", "info", "", 1],
                                     ["queue", "queue src report", "working", "go", "https://git.example/queue/code/pull/451237", 1],
                                     ["review", "review the src queue", "landing", "warn", "", 1], ["src", "src code", "blocked", "bad", "", 1],
                                     ["451237", "451237 private auditor", "passed review", "info", "", 1]])
        self.assertEqual([[data["I"][index][0], [(line[1], data["M"][line[2]]) for line in lines]] for index, lines in data["G"]], [
            ["451237", [("review", "src")]],
            ["code", [("worker", "private auditor"), ("code review", "451237"), ("src", "review")]],
            ["queue", [("worker", "code"), ("queue", "queue")]]])

    @unittest.skipUnless(shutil.which("node"), "node is not on PATH")
    def test_page_drawn_for_that_store_names_the_item_and_the_agent_of_each_running_and_queued_row(self):
        fixture = ordinary(self.fixture).write()
        texts = NodeRender.render(self, self.document())["texts"]
        self.assertEqual(texts[texts.index("Running now") + 1:texts.index("Timeline")], [
            "code", "src", " · under ", "worker", "10m", "queue", "worker", "30m", "queue", "queue", "20m",
            "Queued", "451237", "review", "5m"])

    def test_identifier_that_is_an_ordinary_word_is_not_in_the_filter_and_every_other_identifier_of_the_fixture_is(self):
        fixture = ordinary(self.fixture)
        store, t3, _ = fixture.write().read()
        known = {form for found in MOD["privacy_of"](store, t3, (fixture.store, "relative")).index.values() for form in found}
        child = delegated(COORDINATOR, "queue")
        places = {os.path.abspath(path).lower() for path in (fixture.store, "relative", fixture.meta["projectRoot"])} | {os.path.realpath(path).lower() for path in (fixture.store, "relative", fixture.meta["projectRoot"])}
        expected = {value.lower() for thread in (COORDINATOR, child, delegated(COORDINATOR, "review"), delegated("451237", "code-review"), delegated("451237", "src"))
                    for value in (thread, unquote(thread), unquote(unquote(thread)))}
        nodes = {node.lower() for node, *_ in fixture.delegations}
        self.assertEqual(known, expected | nodes | places)
        self.assertEqual([word for word in ("queue", "review", "451237", "private auditor", "code", "src") if word in known], [])
class SlashCommandTest(OutputCase):
    """The slash commands in these fixtures stay in both forms. The planted paths and file: addresses go.
    The one-segment project root and store directory in these fixtures go too.
    """

    COMMANDS = ("/review", "/loop", "/code-review")
    PATHS = ("/home/someone/x", "~/x", "/a/b", "C:\\Users\\someone", "file:someone.txt")

    def staffed(self, name, root="/checkoutmarker"):
        fixture = self.fixture = Fixture(self.fixture.root / name)
        fixture.meta["projectRoot"] = root
        fixture.coordinator()
        return fixture

    def test_title_a_summary_and_a_work_item_id_that_hold_slash_commands_keep_them_in_the_document_and_the_text(self):
        fixture = self.staffed("commands")
        for number, command in enumerate(self.COMMANDS, start=1):
            fixture.unit(command, "in-progress", f"Run {command} on the queue", thread=worker(number))
            fixture.thread(worker(number), title=f"{command} worker", turns=(("running", 40 - number, None),))
            fixture.thread(delegated(worker(number), f"helper-{number}"), title=command, parent=worker(number), turns=(("running", 20 - number, None),))
        fixture.unit("D9", "in-review", "Run /review then /loop and /code-review today")
        fixture.thread(delegated(worker(3), "helper-4"), title="Run /review then /loop and /code-review", parent=worker(3), turns=(("running", 8, None),))
        fixture.write()
        data, text = data_of(self.document()), self.out("--text").split("\n")
        self.assertEqual([item[:2] for item in data["I"]], [["/code-review", "Run /code-review on the queue"], ["/loop", "Run /loop on the queue"], ["/review", "Run /review on the queue"],
                                                            ["D9", "Run /review then /loop and /code-review today"]])
        self.assertEqual([[data["I"][index][0], [line[1] for line in lines]] for index, lines in data["G"]], [
            ["/code-review", ["worker", "/code-review", "Run /review then /loop and /code-review"]], ["/loop", ["worker", "/loop"]], ["/review", ["worker", "/review"]]])
        self.assertEqual(text[4:12], [
            "Running now",
            "  /code-review worker   running for 37m",
            "  /code-review /code-review   running for 17m   under worker",
            "  /code-review Run /review then /loop and /code-review   running for 8m   under worker",
            "  /loop worker   running for 38m",
            "  /loop /loop   running for 18m   under worker",
            "  /review worker   running for 39m",
            "  /review /review   running for 19m   under worker"])
        self.assertEqual([line.split("   ")[:3] for line in text[text.index("Work items") + 1:text.index("Work items") + 5]], [
            ["  /code-review", "working", "Run /code-review on the queue"], ["  /loop", "working", "Run /loop on the queue"], ["  /review", "working", "Run /review on the queue"],
            ["  D9", "in review", "Run /review then /loop and /code-review today"]])

    def test_title_a_summary_and_a_work_item_id_that_hold_a_path_of_two_segments_a_home_path_a_drive_path_or_a_file_address_lose_it_in_the_document_and_the_text(self):
        fixture = self.staffed("paths")
        fixture.unit("D7", "in-progress", f"Read {' and '.join(self.PATHS)} after /review today", thread=worker(1))
        fixture.thread(worker(1), title="D7 worker", turns=(("running", 30, None),))
        for number, path in enumerate(self.PATHS, start=1):
            fixture.unit(path, "in-progress", f"item {number}", thread=worker(10 + number))
            fixture.thread(worker(10 + number), title="worker", turns=(("running", 20, None),))
            fixture.thread(delegated(worker(1), f"helper-{number}"), title=f"/loop over {path} now", parent=worker(1), turns=(("running", 10 + number, None),))
        fixture.write()
        document, text = self.document(), self.out("--text")
        data = data_of(document)
        self.assertEqual([word for word in ("someone", "/a/b", "~/x") if word in document or word in text], [])
        self.assertEqual(sorted(item[:2] for item in data["I"]), [["D7", "Read and and and and after /review today"], *(["work item", f"item {number}"] for number in range(1, 6))])
        self.assertEqual([line[1] for line in data["G"][[data["I"][index][0] for index, _ in data["G"]].index("D7")][1]], ["worker", "helper 5", "helper 4", "helper 3", "helper 2", "helper 1"])
        self.assertIn("  D7   working   Read and and and and after /review today   1 agent, 5 sub-agents, ", text)

    def test_one_segment_project_root_the_store_records_is_in_neither_form_and_a_slash_command_beside_it_is_in_both(self):
        fixture = self.staffed("root")
        fixture.unit("D7", "in-progress", "Run /review in /checkoutmarker and /CHECKOUTMARKER/src and x=/checkoutmarker today", thread=worker(1))
        fixture.unit("/checkoutmarker", "in-progress", "second item")
        fixture.thread(worker(1), title="D7 worker", turns=(("running", 30, None),))
        fixture.thread(delegated(worker(1), "helper-1"), title="/loop in /checkoutmarker", parent=worker(1), turns=(("running", 20, None),))
        fixture.thread(delegated(worker(1), "helper-2"), title="/loop", parent=worker(1), turns=(("running", 10, None),))
        fixture.write()
        document, text = self.document(), self.out("--text")
        data = data_of(document)
        self.assertEqual(["checkoutmarker" in output.lower() for output in (document, text)], [False, False])
        self.assertEqual((sorted(item[:2] for item in data["I"]), [line[1] for line in data["G"][0][1]]), ([["D7", "Run /review in and and today"], ["work item", "second item"]], ["worker", "helper 1", "/loop"]))
        self.assertIn("  D7 /loop   running for 10m   under worker", text.split("\n"))
        self.assertIn("  D7   working   Run /review in and and today   1 agent, 2 sub-agents, ", text)

    def test_filter_of_a_run_whose_store_directory_and_project_root_are_one_segment_removes_both_and_keeps_a_slash_command(self):
        fixture = self.staffed("places")
        store, t3, _ = fixture.write().read()
        privacy = MOD["privacy_of"](store, t3, ("/storemarker",))
        self.assertEqual(store.project_root, "/checkoutmarker")
        self.assertEqual(privacy.clean("in /storemarker and /checkoutmarker and /STOREMARKER/dishes.tsv run /review and /loop and /code-review"), "in and and run /review and /loop and /code-review")
        self.assertEqual(MOD["Privacy"]().clean("in /storemarker and /checkoutmarker"), "in /storemarker and /checkoutmarker")
