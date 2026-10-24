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
class LabelTest(unittest.TestCase):
    STORE = store_of(("D7", worker(2), (worker(1),)), ("D8", "", ()))

    def label(self, title="", request="", parent=COORDINATOR, unit="D7"):
        thread = delegated(parent, request) if request else native(1) if parent else worker(9)
        return MOD["label_of"](agent(thread, title=title, parent=parent), self.STORE, unit, MOD["Privacy"]({thread, COORDINATOR, worker(1), worker(2)}))

    def test_current_worker_and_earlier_worker(self):
        self.assertEqual(MOD["label_of"](agent(worker(2), title="D7 anything"), self.STORE, "D7", MOD["Privacy"]()), "worker")
        self.assertEqual(MOD["label_of"](agent(worker(1), title="D7 anything"), self.STORE, "D7", MOD["Privacy"]()), "earlier worker")

    def test_written_title_wins_over_the_request_name(self):
        self.assertEqual(self.label("how explorer: store and CLI", "brigade-kit-d7-verify-0a1b2c3"), "how explorer: store and CLI")

    def test_title_that_starts_with_act_as_or_you_are_loses_to_the_request_name(self):
        self.assertEqual(self.label("Act as the review sub-agent for this task.", "brigade-kit-d7-verify-0a1b2c3"), "review")
        self.assertEqual(self.label("You are a code delegate for item D7.", "architect-d7-runner-2"), "architect runner 2")

    def test_title_over_60_characters_loses_to_the_request_name(self):
        self.assertEqual(self.label("Read-only review of the page. Do not edit, commit, push, or merge.", "brigade-kit-d7r2-fix"), "fix")
        self.assertEqual(self.label("x" * 61, "brigade-kit-d7r2-fix"), "fix")
        self.assertEqual(self.label("x" * 60, "brigade-kit-d7r2-fix"), "x" * 39 + "…")

    def test_leading_id_of_the_rows_own_unit_is_dropped_from_a_title(self):
        self.assertEqual(self.label("D7: rehearsal of the wake"), "rehearsal of the wake")
        self.assertEqual(self.label("D7 fresh-child test"), "fresh-child test")
        self.assertEqual(self.label("D8: rehearsal"), "D8: rehearsal")
        self.assertEqual(self.label("D7: rehearsal", unit=None), "D7: rehearsal")

    def test_title_that_is_one_path_under_root_gives_its_last_segment_when_that_is_letters_digits_hyphens_and_underscores(self):
        self.assertEqual(self.label("/root/spec_review", parent=worker(2)), "spec_review")
        self.assertEqual(self.label("/root/a/b/spec-review_2", parent=worker(2)), "spec-review_2")
        for title in ("/root/notes.md", "/pathmarker/notes", "/root/spec_review now"):
            with self.subTest(title):
                self.assertEqual(self.label(title, parent=worker(2)), "sub-agent")
        self.assertEqual((self.label("/root", parent=worker(2)), self.label("/review", parent=worker(2))), ("/root", "/review"))

    def test_title_that_holds_private_text_loses_to_the_request_name(self):
        self.assertEqual(self.label("Read the brief at /pathmarker/brief.md", "why-investigator"), "why investigator")
        self.assertEqual(self.label("Read /pathmarker/brief.md first", parent=worker(2)), "sub-agent")
        self.assertEqual(self.label(f"Ask {worker(1)} first", "why-investigator"), "why investigator")
        self.assertEqual(self.label("Mail acct-homemarker@example.test first", "why-investigator"), "why investigator")
        self.assertEqual(self.label("Read the brief first", "why-investigator"), "Read the brief first")
        self.assertEqual(self.label("Fix 0a1b2c3 in src/a.py now", "brigade-kit-d7r2-fix"), "Fix 0a1b2c3 in src/a.py now")

    def test_title_that_holds_its_own_request_name_of_two_or_more_parts_loses_to_the_words_of_that_name(self):
        self.assertEqual(self.label("see why-investigator", "why-investigator"), "why investigator")
        self.assertEqual(self.label("Why-Investigator: part 2", "why-investigator"), "why investigator")
        self.assertEqual(self.label("why-investigator", "why-investigator"), "why investigator")
        self.assertEqual(self.label("see the explorer", "explorer"), "see the explorer")
        self.assertEqual(self.label("Act as the brigade-d7 sub-agent for this task.", "brigade-d7"), "brigade d7")
        self.assertEqual(self.label("Act as the Brigade-D7 review sub-agent.", "brigade-d7"), "brigade d7")

    def test_request_name_of_one_ordinary_word_is_the_label_and_a_title_of_that_word_is_too(self):
        for word in ("explorer", "queue", "code", "review", "src", "451237"):
            with self.subTest(word):
                self.assertEqual((self.label(request=word), self.label(word, word), self.label("Act as the review sub-agent for this task.", word)), (word, word, word))
        self.assertEqual(self.label(request="how-explorer"), "how explorer")

    def test_title_that_is_one_lower_case_word_with_a_hyphen_is_read_as_a_request_name(self):
        self.assertEqual(self.label("brigade-kit-d7-verify-2", "brigade-kit-d7-verify-0a1b2c3"), "review 2")

    def test_request_name_words_drop_brigade_the_stores_leading_slug_parts_unit_ids_and_parts_of_7_or_more_hexadecimal_characters_with_a_digit_and_a_letter(self):
        self.assertEqual(self.label(request="brigade-kit-d7-verify-0a1b2c3"), "review")
        self.assertEqual(self.label(request="proj-kit-nightly-audit-kit-1234567"), "nightly audit kit 1234567")
        self.assertEqual(self.label(request="audit-0a1b2c-facade-defaced-deadbeef01"), "audit 0a1b2c facade defaced")
        self.assertEqual(self.label(request="trial-d8-kimi"), "trial kimi")
        self.assertEqual(self.label(request="trial-d9-kimi"), "trial d9 kimi")

    def test_role_from_an_act_as_title_when_the_request_name_leaves_nothing(self):
        self.assertEqual(self.label("Act as the review sub-agent for this task.", "brigade-kit-d7-0a1b2c3"), "review")

    def test_request_name_whose_every_part_the_store_explains_gives_all_its_words_when_no_title_names_a_role(self):
        store = store_of(("queue", "", ()), ("review", "", ()), name="queue", slug=("queue",))
        for request, label in (("queue", "queue"), ("review", "review"), ("queue-review", "queue review"), ("brigade-queue-review-0a1b2c3", "brigade queue review")):
            with self.subTest(request):
                thread = delegated(COORDINATOR, request)
                self.assertEqual(MOD["label_of"](agent(thread, parent=COORDINATOR), store, "queue", MOD["Privacy"]({thread})), label)
        self.assertEqual(self.label(request="brigade-kit-d7"), "brigade kit d7")

    def test_thread_with_no_usable_title_or_request_reads_sub_agent_or_agent(self):
        self.assertEqual(self.label("123e4567-e89b-12d3-a456-426614174000", parent=worker(2)), "sub-agent")
        self.assertEqual(self.label("You are the helper.", parent=None), "agent")
