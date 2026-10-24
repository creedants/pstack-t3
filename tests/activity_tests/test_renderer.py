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
@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class RendererTest(NodeRender, OutputCase):
    def test_renderer_draws_the_counts_the_running_list_the_rows_the_items_and_the_notes(self):
        failed_child(self.fixture).write()
        document = self.document()
        built = self.render(document)
        texts = built["texts"]
        for text in ("2", "4", "1", "running now", "agents, last 3h", "sub-agents", "failed", "Running now", "model-a", "42m",
                     "6m", "Timeline", "model-a 3", "model-b 2", "model-c 1", "coordinator", "worker",
                     "architect runner 2", "spec_review", "review", "model-c", "why investigator", "Not tied to a work item", "Work items in flight",
                     "D7", "Agent activity page", "in review", "D6", "Queue fix", "landing", "D5", "Old work", "merged",
                     "1 agent is grouped by the name of the request that started it.", "1 other thread ran in this coordinator's T3 project outside this coordinator."):
            self.assertIn(text, texts)
        self.assertNotIn("This copy differs from what the tool wrote. Run the command again.", texts)
        self.assertEqual(texts[texts.index("Running now") + 1:texts.index("Timeline")],
                         ["D7", "worker", "42m", "D7", "architect runner 2", " · under ", "worker", "6m"])
        self.assertRegex(texts[-5], r"^As of \d+:\d\d [AP]M for $")
        self.assertEqual(texts[-4:-2], ["kit", ". Bars show turn or delegation intervals and may join across gaps. Striped bars include running intervals. Outlined bars include queued intervals. Faded bars include stopped intervals."])
        self.assertEqual(built["links"], [["A", PR7, "_blank", "noopener"], ["A", PR6, "_blank", "noopener"], ["A", "#g1", None, None]])
        classes = built["classes"]
        self.assertEqual([classes.count(name) for name in ("stat live", "stat alarm", "mark pulse", "row co", "row p1", "row p3", "row p4", "grp", "b run", "b f", "b stop")],
                         [1, 1, 2, 1, 3, 2, 1, 3, 2, 1, 1])
        self.assertEqual(len(built["bars"]), sum(len(line[6]) // 3 for _, lines in data_of(document)["G"] for line in lines) + 2)
        self.assertEqual([bar for bar in built["bars"] if not re.fullmatch(r"min\([0-9]+(\.[0-9])?%,100% - 3px\) [0-9]+(\.[0-9])?%", " ".join(bar))], [])
        self.assertEqual(built["titles"], ["running · 15m", "coordinator", "model-c", "running · 63m", "worker", "model-a", "running · 6m", "architect runner 2", "model-b",
                                           "done · 60s", "spec_review", "model-b", "failed · 3m", "review", "model-c", "done · 5m", "worker", "model-a", "stopped · 2m", "why investigator", "model-a"])
    def test_page_that_render_html_writes_is_drawn_with_markup_a_quote_a_backslash_and_text_that_reads_as_an_entity_as_that_text_and_a_tab_and_u2028_as_one_space(self):
        built = self.render(MOD["render_html"](hostile(), same))
        self.assertNotIn("This copy differs from what the tool wrote. Run the command again.", built["texts"])
        self.assertEqual([text for text in built["texts"] if "onerror" in text or "&" in text],
                         ["D1 " + HOSTILE_DRAWN, HOSTILE_DRAWN, HOSTILE_DRAWN, "model " + HOSTILE_DRAWN, HOSTILE_DRAWN])
        self.assertEqual(built["titles"], ["done · 60s", HOSTILE_DRAWN, "model " + HOSTILE_DRAWN])
    def test_item_block_lists_current_ledger_rows_in_attempt_order_and_folds_earlier_attempts_into_one_summary(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "verifier", "thread": worker(3), "seat": "grok/grok-4", "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": worker(1), "seat": "", "start": stamp, "end": ""},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "fix worker", "thread": worker(2), "seat": "", "start": stamp, "end": ""},
        ])
        current = [
            "attempt 2 · verifier · grok/grok-4 · ended",
            "attempt 2 · fix worker · open",
        ]
        summary = "attempt 1: 1 worker, all open"
        document = self.document()
        texts = self.render(document)["texts"]
        self.assertNotIn("This copy differs from what the tool wrote. Run the command again.", texts)
        self.assertEqual([text for text in texts if text.startswith("attempt ")], [summary, "attempt 1 · worker · open", *current])
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1:at + 4], ["    " + summary, *["    " + phrase for phrase in current]])
        for ident in (worker(1), worker(2), worker(3)):
            self.assertNotIn(ident, document)
            self.assertNotIn(ident, "\n".join(lines))
    def test_worker_with_an_end_stamp_says_ended_and_a_verifier_with_an_empty_end_says_open_and_an_earlier_attempts_worker_folds_into_one_summary_line(self):
        fixture = one_running(self.fixture).write()
        fixture.write_store()
        stamp = fixture.stamp(10)
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "worker", "thread": worker(1), "start": stamp, "end": stamp},
            {"at": stamp, "dish": "D7", "attempt": "2", "role": "verifier", "thread": worker(2), "start": "", "end": ""},
        ])
        summary = "attempt 1: 1 worker, all ended"
        current = ["attempt 2 · verifier · open"]
        texts = self.render(self.document())["texts"]
        self.assertEqual([text for text in texts if text.startswith("attempt ")], [summary, "attempt 1 · worker · ended", *current])
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1:at + 3], ["    " + summary, "    " + current[0]])
    def test_hostile_seat_is_escaped_in_the_document_and_drawn_as_text(self):
        fixture = one_running(self.fixture).write()
        stamp = fixture.stamp(10)
        # A tab would split this cell. The seat keeps HOSTILE with that tab written as a space.
        write_threads(fixture, [
            {"at": stamp, "dish": "D7", "attempt": "1", "role": "verifier", "thread": worker(2),
             "seat": HOSTILE.replace("\t", " "), "start": stamp, "end": stamp},
        ])
        document = self.document()
        raw = DATA.search(document).group(1)
        self.assertIn("attempt 1 · verifier · x&lt;/script&gt;&lt;img onerror=a… · ended", raw)
        self.assertNotIn("</script><img", raw)
        self.assertEqual([character for character in "<>" if character in raw], [])
        drawn = [text for text in self.render(document)["texts"] if text.startswith("attempt ")]
        self.assertEqual(drawn, ["attempt 1 · verifier · x</script><img onerror=a… · ended"])
        lines = self.out("--text").split("\n")
        at = lines.index(next(line for line in lines if line.startswith("  D7   ")))
        self.assertEqual(lines[at + 1], "    attempt 1 · verifier · x</script><img onerror=a… · ended")
    def test_renderer_lists_a_queued_agent_under_queued_with_a_plain_mark_and_an_outlined_bar_and_draws_no_running_list(self):
        fixture = queued_only(self.fixture)
        fixture.thread(native(1), title="/root/spec_review", parent=worker(1), delegation=("queued", 2, None))
        fixture.write()
        built = self.render(self.document())
        texts, classes = built["texts"], built["classes"]
        self.assertEqual([text for text in texts if text in ("Running now", "Queued", "Timeline")], ["Queued", "Timeline"])
        self.assertEqual(texts[texts.index("Queued") + 1:texts.index("Timeline")], ["D7", "worker", "5m", "D7", "spec_review", " · under ", "worker", "2m"])
        self.assertEqual([classes.count(name) for name in ("mark", "mark pulse", "b q", "b run", "stat live")], [2, 0, 2, 0, 1])
        self.assertEqual((texts[0], built["titles"][3:]), ("0", ["queued · 0s", "worker", "model-a", "queued · 0s", "spec_review", "model-a"]))
    def test_renderer_puts_a_bar_that_starts_at_the_windows_end_3_pixels_inside_its_track(self):
        fixture = one_running(self.fixture)
        fixture.thread(delegated(worker(1), "helper-task"), title="helper", parent=worker(1), turns=(("running", 0.05, None),))
        fixture.write()
        built = self.render(self.document())
        self.assertEqual(built["bars"][-1], ["min(99.9%,100% - 3px)", "0.1%"])
        self.assertIn(".b{position:absolute;top:4px;height:11px;min-width:3px;", MOD["STYLE"])
    def test_renderer_draws_a_flag_in_a_note_in_its_own_element_with_the_class_that_cannot_wrap(self):
        note = "2 more agents are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."
        document = MOD["render_html"](page(group(item("D1"), row("worker")), hidden=MOD["Hidden"](cut_agents=2)), same)
        self.assertEqual(data_of(document)["N"], [note])
        built = self.render(document)
        self.assertEqual(built["texts"][-3:], ["2 more agents are not shown, because the page is at its size limit. Use a larger ", "--max-bytes", " to see more."])
        self.assertEqual(built["classes"][-4:], ["", "", "nb", ""])
        self.assertIn(".nb,.one{white-space:nowrap}", MOD["STYLE"])
    def test_renderer_draws_the_item_and_label_of_an_open_line_the_id_of_a_group_heading_and_the_id_of_a_chip_with_the_one_line_class(self):
        failed_child(self.fixture).write()
        self.assertEqual(self.render(self.document())["one"], ["D7", "worker", "D7", "architect runner 2", "D7", "D5", "D6", "D7"])
        self.assertIn(".nb,.one{white-space:nowrap}.one{overflow:hidden;text-overflow:ellipsis}", MOD["STYLE"])
    def test_renderer_says_the_copy_differs_when_one_character_of_the_data_changed(self):
        one_running(self.fixture).write()
        texts = self.render(self.document(), spoil=True)["texts"]
        self.assertEqual(texts[0], "This copy differs from what the tool wrote. Run the command again.")
        self.assertEqual(texts[texts.index("Running now") + 1:texts.index("Timeline")], ["D7", "worker", "42m"])
    def test_renderer_draws_a_folded_page_and_an_empty_one_with_no_error(self):
        for build, heading in ((busy, "Running now"), (empty, "Timeline")):
            with self.subTest(build.__name__):
                fixture = self.fixture = Fixture(self.fixture.root / build.__name__)
                build(fixture).write()
                self.assertIn(heading, self.render(self.document())["texts"])
    def test_an_item_with_a_timeline_group_keeps_no_summary_in_the_in_flight_strip_and_links_its_id_to_the_group(self):
        failed_child(self.fixture).write()
        document = self.document()
        built = self.render(document)
        data = data_of(document)
        at = [index for index, item in enumerate(data["I"]) if item[0] == "D7"][0]
        self.assertIn("g" + str(at), built["ids"])
        self.assertIn(["A", "#g" + str(at), None, None], built["links"])
        # The Timeline header is the only place D7's summary stays; its anchored chip drops it.
        self.assertEqual(built["texts"].count("Agent activity page"), 1)
    def test_running_now_names_no_model_of_the_store(self):
        failed_child(self.fixture).write()
        document = self.document()
        built = self.render(document)
        open_list = built["texts"][built["texts"].index("Running now") + 1:built["texts"].index("Timeline")]
        self.assertEqual([text for text in open_list if any(model in text for model in set(data_of(document)["M"]))], [])
    def test_the_timeline_draws_the_same_text_for_a_store_whose_goal_name_changes(self):
        fixture = one_running(self.fixture).write()
        write_goals(fixture, [("G1", "2026-10-10T11:00:00.000Z", "open", "First goal", "purpose", "done", "1", "")])
        widen_items(fixture, {"D7": ("feature", "G1")})
        first = self.render(self.document())
        write_goals(fixture, [("G1", "2026-10-10T11:00:00.000Z", "open", "Second goal", "purpose", "done", "1", "")])
        second = self.render(self.document())

        def timeline(built):
            texts = built["texts"]
            end = next(index for index, text in enumerate(texts) if text.startswith("As of "))
            return texts[texts.index("Timeline"):end]

        self.assertNotIn("First goal", timeline(first))
        self.assertEqual(timeline(first), timeline(second))
