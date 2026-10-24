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
class PrivacyTest(OutputCase):
    """The contract: no private text, as the script's docstring defines it, is in either form, and a request name of two or more parts reaches a label only as separate words."""

    def staffed(self, name):
        fixture = self.fixture = Fixture(self.root / name)
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Activity page", thread=worker(1), pr="https://git.example/o/r/pull/7")
        fixture.retired("D7", BARE)
        fixture.thread(worker(1), title="D7 worker", turns=(("running", 30, None),))
        fixture.thread(BARE, title="D7 first worker", turns=(("completed", 60, 50),))
        self.child = delegated(worker(1), REQUEST)
        self.node = fixture.thread(self.child, title="Act as the audit sub-agent for this task.", provider="codex", parent=worker(1), turns=(("completed", 25, 20),))
        self.leaf = fixture.thread(native(1), title="/root/spec_review", parent=self.child, delegation=("completed", 24, 23))
        fixture.unit("D8", "in-review", "Second item", thread=ODD)
        fixture.thread(ODD, title="D8 worker", turns=(("completed", 45, 40),))
        return fixture

    def identifiers(self):
        return (COORDINATOR, worker(1), BARE, ODD, self.child, self.node, native(1), self.leaf, unquote(self.child))

    def paths(self, fixture):
        return (str(fixture.store), str(fixture.home), str(fixture.base), str(fixture.database), fixture.meta["projectRoot"], str(fixture.store / "dishes.tsv"), str(fixture.home / ".config" / "secret.json"))

    def exact(self, fixture):
        """The fixture's identifiers and paths as written, and four of them in every case and encoding forms() gives.
        The run reads each identifier but the last, which is one thread id decoded. It reads each path but the last two, which lie below the store and below the home directory.
        """
        ids, paths = self.identifiers(), self.paths(fixture)
        return tuple(dict.fromkeys((*ids, *paths, *(form for value in (ODD, self.child, *paths[:2]) for form in forms(value)))))

    def shapes(self):
        return ("mcp:alpha-secret", "thread:secret-1", "node:alpha-secret", "run:alpha-secret", "task:alpha-secret", "prefix=mcp:alpha-secret", "id=mcp%3Aalpha-secret", "context-transfer:secret",
                "provider-thread:secret", "9f8e7d6c-e89b-12d3-a456-426614174000", "/srv/secret/notes.md", "/secret/x", "~/secret/notes.md", "~user/secret", "$HOME/secret", "C:\\Users\\secret\\file.txt",
                "file:/home/secret/notes", "file:///srv/secret", "(/srv/secret)", "x=/srv/secret", "%2Fsrv%2Fsecret%2Fnotes.md", ADDRESS, ADDRESS.upper(), quote(ADDRESS, safe=""), f"<{ADDRESS}>")

    def planted(self, fixture):
        """Each value as the page may meet it: alone, and for an identifier or a path the run read also inside a longer word."""
        return (*self.exact(fixture), *(f"pre{value}post" for value in self.exact(fixture)), *self.shapes())

    def plant(self, fixture, field, number, values):
        """One thread or one work item that holds the values in the field, or the store's name with the values added.
        A thread is running, failed, or queued. With at most 6, 4, and 4 of them beside the fixture's own running worker, the text form prints each one's row.
        """
        part, unit, joined = delegated(worker(1), f"part-{number}"), f"D{100 + number}", " and ".join(values)
        link = f"https://git.example/o/r/pull/{100 + number}"
        if field in ROW_FIELDS + ("provider", "instance id"):
            how = {"model": {"model": joined}, "title": {"title": joined}, "title in a sentence": {"title": f"see {joined} now"}, "provider": {"provider": joined}, "instance id": {"instance": joined}}[field]
            turn = ("running", 20, None) if number % 14 < 6 else ("failed", 20, 10) if number % 14 < 10 else ("queued", 20, None)
            fixture.thread(part, parent=worker(1), turns=(turn,), **{"title": f"part {number}", **how})
        elif field == "store name":
            fixture.meta["restaurant"] += f" {joined}"
        elif field == "unit id":
            fixture.unit(joined, "in-progress", f"item {number}")
        elif field == "branch":
            fixture.unit(unit, "in-progress", f"item {number}")
            fixture.units[-1]["branch"] = joined
        else:
            links = {"pull request": joined, "pull request owner": f"https://git.example/{joined}/r/pull/{100 + number}", "pull request query": f"{link}?x={joined}",
                     "pull request fragment": f"{link}#{joined}", "pull request userinfo": f"https://{quote(joined, safe='')}@git.example/o/r/pull/{100 + number}"}
            fixture.unit(unit, "in-progress", f"Fix {joined} today" if field == "summary" else f"item {number}", pr=links.get(field, ""))

    def found(self, output):
        """The needles in the output as written, in lower case, and percent-decoded once and twice."""
        read = [output, unquote(output), unquote(unquote(output))]
        return [needle for needle in (*NEEDLES, self.temporary, REQUEST) if any(needle.lower() in text.lower() for text in read)]

    def checked(self, fixture, field, slots, text=True):
        """Plant one slot's values in each thread or work item, run both forms, and name each needle either holds."""
        for number, values in enumerate(slots):
            self.plant(fixture, field, number, values)
        fixture.write()
        document = self.document("--max-bytes", "500000")
        data = data_of(document)
        self.assertEqual(([item[0] for item in data["I"] if item[0] in ("D7", "D8")], [line[1] for line in data["G"][0][1][:2]]), (["D7", "D8"], ["earlier worker", "worker"]), field)
        self.assertEqual((self.found(document), self.found(self.document()), self.found(self.out("--text")) if text else []), ([], [], []), field)

    def test_no_identifier_the_run_read_no_path_and_no_address_in_a_model_or_a_title_is_in_the_document_or_the_text(self):
        """The text form prints at most 7 running, 4 failed, and 4 queued rows, so each fixture holds 14 planted rows beside its own running worker."""
        for field in ROW_FIELDS:
            for start in range(0, len(self.planted(self.staffed("count"))), 14):
                with self.subTest(field, start=start):
                    fixture = self.staffed(f"{field.replace(' ', '-')}-{start}")
                    self.checked(fixture, field, [(value,) for value in self.planted(fixture)[start:start + 14]])
                    self.assertNotIn("more running agent", self.out("--text"))

    def test_no_identifier_the_run_read_no_path_and_no_address_in_a_summary_a_work_item_id_or_the_stores_name_is_in_the_document_or_the_text(self):
        """The text form prints at most 9 work items and two have agents, so the values are spread over 7 planted items."""
        for field in ("summary", "unit id", "store name"):
            with self.subTest(field):
                fixture = self.staffed(field.replace(" ", "-"))
                values = self.planted(fixture)
                self.checked(fixture, field, [values[start::7] for start in range(7)])

    def test_no_identifier_the_run_read_no_path_and_no_address_in_a_field_no_page_prints_is_in_the_document_or_the_text(self):
        for field in ("provider", "instance id", "branch"):
            with self.subTest(field):
                fixture = self.staffed(field.replace(" ", "-"))
                self.checked(fixture, field, [(value,) for value in self.planted(fixture)])

    def test_no_identifier_the_run_read_no_path_and_no_address_in_a_pull_request_value_is_in_the_document_or_the_text(self):
        """Every value is planted for the document. The text form prints 7 planted items, so it is checked with each identifier and path as written, 7 to a fixture."""
        for field in LINK_FIELDS:
            with self.subTest(field, form="document"):
                fixture = self.staffed(field.replace(" ", "-"))
                self.checked(fixture, field, [(value,) for value in self.planted(fixture)], text=False)
            for start in range(0, 21, 7):
                with self.subTest(field, start=start):
                    fixture = self.staffed(f"{field.replace(' ', '-')}-{start}")
                    core = (*self.identifiers()[:8], *self.paths(fixture), ADDRESS, "mcp:alpha-secret", "/srv/secret/notes.md", "file:/home/secret/notes", "~/secret", "9f8e7d6c-e89b-12d3-a456-426614174000")
                    self.assertEqual(len(core), 21)
                    self.checked(fixture, field, [(value,) for value in core[start:start + 7]])

    def test_every_planted_value_is_private_text_and_the_fixture_reads_each_identifier_it_plants_but_the_decoded_child_id(self):
        fixture = self.staffed("values")
        store, t3, _ = fixture.write().read()
        privacy = MOD["privacy_of"](store, t3, (fixture.store, fixture.database, fixture.base, fixture.home))
        self.assertEqual([value for value in self.identifiers() if value not in {*t3.ids, *store.coordinators}], [unquote(self.child)])
        self.assertEqual([value for value in self.planted(fixture) if privacy.clean(f"a {value} b") != "a b"], [])
        self.assertEqual([value for value in self.planted(fixture) if not self.found(value)], [])

    def test_request_name_of_two_or_more_parts_is_split_into_words_in_its_own_label_for_each_fixture_title(self):
        titles = ("", "Act as the audit sub-agent for this task.", "You are the auditor.", "x" * 61, REQUEST, f"see {REQUEST}", f"{REQUEST.upper()}: part 2", f"Read /pathmarker/{REQUEST}.md", "auditor")
        labels = ("audit task", "audit task", "audit task", "audit task", "audit task", "audit task", "audit task", "audit task", "auditor")
        for number, (title, label) in enumerate(zip(titles, labels)):
            with self.subTest(title):
                fixture = self.staffed(f"request-{number}")
                put(fixture.threads, 3, 1, title)
                fixture.write()
                document, text = self.document(), self.out("--text")
                self.assertEqual((self.found(document), self.found(text), data_of(document)["G"][0][1][2][1]), ([], [], label))

    def test_identifier_with_a_space_is_in_neither_form_when_two_fields_or_a_control_character_would_spell_it(self):
        """The plain form joins a work item's id and a row's label into one line. The document draws them in two elements."""
        spaced = "left_homemarker right_homemarker"
        for number, between in enumerate((" ", "\x00", "\x1b", "\x7f", "\x80", "%20", "%2520")):
            with self.subTest(between):
                fixture = self.staffed(f"spaced-{number}")
                fixture.unit("left_homemarker", "in-progress", f"see left_homemarker{between}right_homemarker now", thread=spaced)
                fixture.thread(spaced, title="right_homemarker", model="left_homemarker", turns=(("running", 9, None),))
                fixture.thread(delegated(spaced, "helper-task"), title="right_homemarker", model="right_homemarker", parent=spaced, turns=(("running", 8, None),))
                # read_store refuses a name that holds a NUL character.
                fixture.meta["restaurant"] = f"left_homemarker{between.replace(chr(0), chr(27))}right_homemarker kit"
                fixture.write()
                document, text = self.document(), self.out("--text")
                for output in (document, text):
                    read = (output, unquote(output), unquote(unquote(output)))
                    self.assertEqual([form for form in read if re.search(r"left_homemarker[\s\x00-\x1f\x7f-\x9f]+right_homemarker", form)], [])
                data = data_of(document)
                self.assertEqual((data["c"], [item[:2] for item in data["I"] if "left" in item[0]], [line[1] for index, lines in data["G"] for line in lines if "left" in data["I"][index][0]]),
                                 ("kit", [["left_homemarker", "see now"]], ["worker", "right_homemarker"]))
                lines = text.split("\n")
                at = lines.index("Running now")
                self.assertEqual(lines[at + 2:at + 4], ["  left_homemarker worker   running for 9m", "  running for 8m   under worker"])
                self.assertIn("  left_homemarker   working   see now   1 agent, 1 sub-agent, 17m at work", text.split("\n"))

    def test_out_file_named_in_a_summary_is_not_in_the_file_it_names(self):
        fixture = self.staffed("out")
        target = fixture.root / "pathmarker out" / "page.html"
        target.parent.mkdir(parents=True)
        fixture.unit("D9", "in-progress", f"Wrote {target} and {quote(str(target), safe='')} today")
        fixture.write()
        for form in ((), ("--text",)):
            with self.subTest(form):
                self.out(*form, "--out", str(target))
                written = target.read_text()
                self.assertEqual((self.found(written), "Wrote and today" in written), ([], True))
class PrivateValuesTest(OutputCase):
    def test_relative_at_and_out_leave_their_own_words_in_the_text_form_and_the_same_paths_written_in_full_are_removed(self):
        fixture = self.fixture
        fixture.store = fixture.store.with_name("queue")
        fixture.meta["restaurant"] = "queue"
        fixture.coordinator()
        fixture.unit("D7", "in-progress", "Fix the queue report", pr="https://example.test/o/queue/pull/7")
        fixture.unit("D8", "in-progress", f"Read {fixture.store} and {fixture.store.parent / 'report'} first")
        fixture.write()
        self.out("--text", "--out", "report", at="queue", cwd=fixture.store.parent)
        self.assertEqual((fixture.store.parent / "report").read_text().split("\n"), [
            "Agent activity for queue, last 3h",
            "0 running now, 0 agents, 0 sub-agents, 0 failed",
            "Work items",
            "  D7   working   Fix the queue report   no activity in this window   https://example.test/o/queue/pull/7",
            "  D8   working   Read and first   no activity in this window",
            "Notes",
            "  No agent or sub-agent of this coordinator ran in this window."])

    def test_document_keeps_a_model_with_no_private_text_and_document_and_text_keep_the_pull_request_address_and_count_a_refused_link(self):
        fixture = one_running(self.fixture)
        fixture.units[0]["pr"] = "https://user:pw@example.test/o/r/pull/7?x=mcp:alpha-secret#task:alpha-secret"
        fixture.units[1]["pr"] = "https://example.test/o/r/pull/6/files"
        fixture.thread(delegated(worker(1), "kit-d7-part-1"), title="part 1", model="mcp:alpha-secret", parent=worker(1), turns=(("completed", 20, 10),))
        fixture.thread(delegated(worker(1), "kit-d7-part-2"), title="part 2", provider="opencode", model="opencode/model-b-20260101", parent=worker(1), turns=(("completed", 20, 10),))
        fixture.thread(delegated(worker(1), "kit-d7-part-3"), title="part 3", model="opencode-go/Model_C (high)", parent=worker(1), turns=(("completed", 20, 10),))
        fixture.write()
        data, text = data_of(self.document()), self.out("--text").split("\n")
        note = "1 pull request link is not shown. It is not an https://host/owner/repository/pull/number address, or it holds text this page removes."
        self.assertEqual((data["M"], [item[4] for item in data["I"]], data["N"]), (["model-a", "other model", "model-b-20260101", "opencode-go/Model_C (high)"], ["", PR7], [note]))
        self.assertEqual([line for line in text if "   working   " in line or "D6" in line], ["  D7   working   Agent activity page   1 agent, 3 sub-agents, 1h 33m at work   " + PR7,
                                                                                      "  D6   landing   Queue fix   no activity in this window"])
        self.assertEqual(text[-2:], ["  " + note, ""])
