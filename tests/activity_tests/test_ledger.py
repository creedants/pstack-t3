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
class LedgerTest(OutputCase):
    def test_store_without_threads_tsv_omits_the_ledger_hook_and_matches_a_header_only_file(self):
        fixture = one_running(self.fixture).write()
        absent, text = fixture.stdout_bytes(), fixture.stdout_bytes("--text")
        (fixture.store / "threads.tsv").write_text("at\tdish\tattempt\trole\tthread\tseat\tstart\tend\n")
        present = fixture.stdout_bytes()
        self.assertEqual(present, absent)
        self.assertEqual(fixture.stdout_bytes("--text"), text)
        for document in (absent, present):
            self.assertNotIn(b".ldg", document)
            # RENDERER reads D.L to anchor a chip, so the ledger hook is the token that must be absent.
            self.assertNotIn(b"byCurrent", document)
    def test_timeline_header_wraps_its_attempt_lines_instead_of_squeezing_the_id_and_title_to_no_width(self):
        """The attempt block's 100% basis fills the header's line, so the header needs a wrap to give the id, title, and chip a line of their own."""
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [{"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": "opaque=ledger-wrap", "start": stamp, "end": ""}])
        style = re.search(r"<style>(.*?)</style>", self.document(), re.S).group(1)
        self.assertEqual([(name, value) for selector, name, value in declarations(style) if selector == ".tl>.grp"], [("flex-wrap", "wrap")])
    def test_opaque_ledger_private_token_in_the_thread_and_the_seat_is_absent_from_both_forms(self):
        token = "opaque=ledger-private-token"
        fixture = one_running(self.fixture).write()
        fixture.write_store()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": token, "seat": token, "start": stamp, "end": ""},
        ])
        document = self.document()
        lines = self.out("--text").split("\n")
        self.assertNotIn(token, document)
        self.assertNotIn(token, "\n".join(lines))
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1], "    attempt 1 · worker · open")
        self.assertNotIn(token, MOD["roots_of"](MOD["read_store"](fixture.store)))
    def test_an_attempt_of_five_thousand_digits_sorts_after_numeric_attempts(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "10", "role": "worker", "thread": "opaque=ledger-sort-10", "start": stamp, "end": ""},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "worker", "thread": "opaque=ledger-sort-2", "start": stamp, "end": ""},
            {"at": stamp, "dish": "D7", "attempt": "9" * 5000, "role": "worker", "thread": "opaque=ledger-sort-long", "start": stamp, "end": ""},
        ])
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1:at + 3], ["    attempts 2, 10: 2 workers, all open", "    attempt 999999999… · worker · open"])
        shown = [line for _, rows in data_of(self.document()).get("L", []) for line in rows]
        self.assertEqual(shown, ["attempt 999999999… · worker · open"])
        folded = data_of(self.document()).get("E", [])
        self.assertEqual([summary for _, summary, _ in folded], ["attempts 2, 10: 2 workers, all open"])
        self.assertEqual([line for _, _, rows in folded for line in rows], ["attempt 2 · worker · open", "attempt 10 · worker · open"])
    def test_five_hundred_ledger_rows_stay_within_max_bytes_and_the_note_counts_the_lines_left_out(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {
                "at": stamp,
                "dish": "D7",
                "attempt": str(number),
                "role": "worker",
                "thread": f"opaque=ledger-row-{number}",
                "seat": "grok/grok-4",
                "start": stamp,
                "end": "",
            }
            for number in range(1, 501)
        ])

        def phrase(number):
            return f"attempt {number} · worker · grok/grok-4 · open"

        def html_current(document):
            return [line for _, lines in data_of(document).get("L", []) for line in lines]

        def html_fold(document):
            folded = data_of(document).get("E", [])
            return [(summary, rows) for _, summary, rows in folded]

        def text_current(text):
            return [line[4:] for line in text.split("\n") if line.startswith("    attempt ")]

        def text_summary(text):
            return [line[4:] for line in text.split("\n") if line.startswith("    attempts ")]

        html_bytes = fixture.stdout_bytes()
        text_bytes = fixture.stdout_bytes("--text")
        self.assertEqual((len(html_bytes), len(text_bytes)), (15970, 428))
        self.assertLessEqual(len(html_bytes), 16000)
        self.assertLessEqual(len(text_bytes), 16000)
        html, text = self.document(), self.out("--text")
        self.assertEqual(data_of(html)["N"], [
            "410 attempt lines are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
        ])
        self.assertEqual(html_current(html), [phrase(500)])
        summary, payload = html_fold(html)[0]
        self.assertEqual(summary, "attempts 1-499: 499 workers, all open")
        # The byte cut drops the oldest attempt lines, so the folded payload keeps the newest of its own lines.
        self.assertEqual(payload, [phrase(number) for number in range(411, 500)])
        self.assertEqual(text_summary(text), ["attempts 1-499: 499 workers, all open"])
        self.assertEqual(text_current(text), [phrase(500)])

        wide_html, wide_text = self.document("--max-bytes", "500000"), self.out("--text", "--max-bytes", "500000")
        self.assertEqual(html_current(wide_html), [phrase(500)])
        summary, payload = html_fold(wide_html)[0]
        self.assertEqual((summary, payload), ("attempts 1-499: 499 workers, all open", [phrase(number) for number in range(1, 500)]))
        self.assertEqual(text_summary(wide_text), ["attempts 1-499: 499 workers, all open"])
        self.assertEqual(text_current(wide_text), [phrase(500)])
        for output in (wide_html, wide_text):
            self.assertNotIn("attempt line is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.", output)
            self.assertNotIn("attempt lines are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.", output)

        html_bytes = fixture.stdout_bytes("--max-bytes", "20000")
        text_bytes = fixture.stdout_bytes("--text", "--max-bytes", "20000")
        self.assertEqual((len(html_bytes), len(text_bytes)), (19964, 428))
        self.assertLessEqual(len(html_bytes), 20000)
        self.assertLessEqual(len(text_bytes), 20000)
        html, text = self.document("--max-bytes", "20000"), self.out("--text", "--max-bytes", "20000")
        self.assertEqual(data_of(html)["N"], [
            "325 attempt lines are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
        ])
        self.assertEqual(html_current(html), [phrase(500)])
        summary, payload = html_fold(html)[0]
        self.assertEqual((summary, payload), ("attempts 1-499: 499 workers, all open", [phrase(number) for number in range(326, 500)]))
        self.assertEqual(text_summary(text), ["attempts 1-499: 499 workers, all open"])
        self.assertEqual(text_current(text), [phrase(500)])
    def test_three_thousand_sparse_attempts_fit_the_byte_budget_with_the_true_count_in_both_forms(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {
                "at": stamp,
                "dish": "D7",
                "attempt": str(2 * number + 1),
                "role": "worker",
                "thread": f"opaque=sparse-{number}",
                "seat": "",
                "start": stamp,
                "end": stamp,
            }
            for number in range(3000)
        ])
        for args in ((), ("--text",)):
            self.assertLessEqual(len(fixture.stdout_bytes(*args, "--max-bytes", "16000")), 16000)
        document = self.document("--max-bytes", "16000")
        folded = data_of(document).get("E", [])
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0][1], "2999 attempts: 2999 workers, all ended")
        text = self.out("--text", "--max-bytes", "16000")
        self.assertIn("2999 attempts: 2999 workers, all ended", text)
        running = [line for line in text.split("\n") if "running for" in line]
        self.assertTrue(any("D7 worker" in line for line in running), running)
    def test_ordinary_ledger_secret_token_in_the_thread_and_the_seat_is_absent_from_both_forms(self):
        token = "ledger-secret-token"
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [{"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": token, "seat": token, "start": stamp, "end": ""}])
        document, text = self.document(), self.out("--text")
        self.assertNotIn(token, document)
        self.assertNotIn(token, text)
        self.assertIn("attempt 1 · worker · open", text)
    def test_ordinary_ledgertoken_in_the_thread_role_and_seat_is_absent_from_both_forms(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        token, other = "ledgertoken", "ledger-secret-token"
        row = {"at": stamp, "dish": "D7", "start": stamp, "end": ""}
        write_threads(fixture, [{**row, "attempt": "1", "role": token, "thread": token, "seat": token}, {**row, "attempt": "1", "role": "worker", "thread": other, "seat": other}])
        document, text = self.document(), self.out("--text")
        self.assertIn("attempt 1 · worker · open", text)
        self.assertEqual([token in document, token in text, other in document, other in text], [False, False, False, False])
    def test_stored_attempt_orders_two_then_ten_then_the_spaced_cell_that_displays_2(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        rows = (("10", "ten", "opaque=ten"), (" 2 ", "spaced", "opaque=spaced"), ("2", "two", "opaque=two"))
        write_threads(fixture, [{"at": stamp, "dish": "D7", "attempt": attempt, "role": role, "thread": thread, "start": stamp, "end": ""} for attempt, role, thread in rows])
        text, document = self.out("--text"), self.document()
        order = [line.strip() for line in text.split("\n") if line.strip().startswith("attempt")]
        self.assertEqual(order, ["attempts 2, 10: 1 ten, 1 two, all open", "attempt 2 · spaced · open"])
        self.assertNotIn("opaque=", document)
        self.assertNotIn("opaque=", text)
    def test_padded_thread_cell_is_absent_from_both_forms(self):
        token = " ledgertoken "
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [{"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": token, "seat": token, "start": stamp, "end": ""}])
        document, text = self.document(), self.out("--text")
        self.assertIn("attempt 1 · worker · open", text)
        self.assertNotIn(token.strip(), document)
        self.assertNotIn(token.strip(), text)
    def test_short_thread_cell_is_absent_as_its_own_word_and_stays_inside_a_longer_word(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [{"at": stamp, "dish": "D7", "attempt": "1", "role": "T1", "thread": "T1", "seat": "T12", "start": stamp, "end": ""}])
        document, text = self.document(), self.out("--text")
        self.assertIsNone(re.search(r"(?<![\w-])T1(?![\w-])", document))
        self.assertIsNone(re.search(r"(?<![\w-])T1(?![\w-])", text))
        self.assertIn("T12", document)
        self.assertIn("T12", text)
    def test_attempt_rank_reads_one_to_eighteen_ascii_digits_as_an_integer(self):
        rank = MOD["attempt_rank"]
        self.assertEqual((rank("007"), rank("9" * 18)), ((0, 7), (0, int("9" * 18))))
        self.assertEqual((rank("9" * 19), rank(""), rank("٢")), ((1, "9" * 19), (1, ""), (1, "٢")))
