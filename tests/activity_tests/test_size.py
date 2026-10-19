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
class SizeTest(OutputCase):
    def test_empty_window_is_at_most_the_fixed_budget_and_says_no_agent_ran(self):
        empty(self.fixture).write()
        document = self.document()
        data = data_of(document)
        self.assertLessEqual(len(document.encode()), MOD["FIXED_BUDGET"])
        self.assertEqual((data["n"], data["G"], data["k"], data["N"]), ([0, 0, 0, 0], [], None, ["No agent or sub-agent of this coordinator ran in this window."]))
        self.assertEqual([item[0] for item in data["I"]], ["D7"])

    def test_typical_window_shows_7_finished_work_items_as_one_row_each_and_is_at_most_16000_bytes(self):
        typical(self.fixture).write()
        document = self.document()
        data = data_of(document)
        self.assertLess(len(document.encode()), 16000)
        self.assertEqual((data["n"], agents_shown(data), sum(len(lines) for _, lines in data["G"])), ([3, 9, 41, 1], 50, 17))
        self.assertEqual(data["N"], ["7 merged or dropped work items with no agent running, queued, or waiting are each shown as one row.",
                                     "14 agents are grouped by the name of the request that started them."])

    def test_busy_window_is_folded_to_at_most_16000_bytes_with_a_note_and_max_bytes_buys_every_row_back(self):
        busy(self.fixture).write()
        document = self.document()
        data = data_of(document)
        self.assertLess(len(document.encode()), 16000)
        self.assertEqual(data["n"], [5, 36, 364, 3])
        self.assertLess(sum(len(lines) for _, lines in data["G"]), 400)
        self.assertIn("27 merged or dropped work items with 301 agents are not shown. Use a larger --max-bytes to see more.", data["N"])
        whole = data_of(self.document("--max-bytes", "500000"))
        self.assertEqual((whole["n"], agents_shown(whole), sum(len(lines) for _, lines in whole["G"])), ([5, 36, 364, 3], 400, 400))
        self.assertEqual(whole["N"], ["112 agents are grouped by the name of the request that started them."])

    def test_window_shaped_like_a_measured_day_is_over_12000_and_at_most_16000_bytes_and_the_last_fold_is_not_applied(self):
        unfolded = page_of(day(self.fixture))
        self.assertEqual((unfolded.totals, len(unfolded.items), sum(each.depth == 0 for one in unfolded.groups for each in one.rows)), (MOD["Totals"](8, 36, 364, 32), 6, 148))
        fitted, document = MOD["fit"](unfolded, 15999, CLEAN)
        self.assertGreater(len(document.encode()), 12000)
        self.assertLess(len(document.encode()), 16000)
        self.assertLess(fitted.fold, len(MOD["FOLDS"]))
        self.assertEqual(agents_shown(data_of(document)) + fitted.hidden.dropped_agents, 400)
        framed, _ = MOD["fit"](unfolded, 15999, CLEAN, 1960)
        printed = self.document()
        self.assertLess(len(printed.encode()), 16000)
        self.assertEqual(sum(len(lines) for _, lines in data_of(printed)["G"]), sum(len(one.rows) for one in framed.groups))

    def test_extreme_window_with_four_byte_characters_and_long_links_is_at_most_16000_bytes(self):
        extreme(self.fixture).write()
        document = self.document()
        data = data_of(document)
        self.assertLess(len(document.encode()), 16000)
        self.assertEqual((data["n"], len(data["G"]), len([item for item in data["I"] if item[5]])), ([60, 200, 2800, 10], 5, 8))
        self.assertEqual(self.out("--text").split("\n")[1], "60 running now, 200 agents, 2800 sub-agents, 10 failed")
        self.assertLessEqual(len(self.out("--text").rstrip("\n").split("\n")), 40)

    def test_window_of_250_running_agents_on_250_items_in_flight_has_no_summary_row_and_counts_the_agents_and_items_it_leaves_out_as_the_guide_says(self):
        fixture = self.fixture
        fixture.coordinator()
        for number in range(1, 251):
            fixture.unit(f"D{number}", "in-progress", f"item {number}", thread=worker(number))
            fixture.thread(worker(number), title=f"D{number} worker", turns=(("running", 5 + number / 10, None),))
        fixture.write()
        document = self.document()
        data = data_of(document)
        rows = [line for _, lines in data["G"] for line in lines]
        self.assertLess(len(document.encode()), 16000)
        self.assertEqual((data["n"], len(rows), [line[7] for line in rows], len([each for each in data["I"] if each[5]])), ([250, 250, 0, 0], 12, [1] * 12, 8))
        self.assertEqual(data["N"], ["238 more agents are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
                                     "242 more work items in flight are not listed."])
        guide = GUIDE.read_text()
        self.assertIn("When the document exceeds its size limit, the page can fold finished rows into summaries or omit rows, and it counts the agents and work items those changes cover.", guide)
        self.assertNotIn("the page folds finished work into summary rows", guide)

    def test_largest_page_the_last_fold_can_leave_is_under_17000_bytes_with_six_models_every_note_the_longest_window_label_and_every_clipped_string_over_its_byte_limit(self):
        spans = tuple((x * 40, 1, "failed") for x in range(25))
        counts = ("dropped_items", "dropped_agents", "cut_agents", "cut_lines", "cut_in_flight", "other_threads", "unknown_status", "by_request_name", "unstarted")
        hidden = MOD["Hidden"](**dict.fromkeys(counts, 9999999))
        legend = tuple((name, 9999999) for name in ("model-a", "model-b", "model-c", "model-d", "model-e", "other model"))
        sizes = {}
        for status in ("running", "queued"):
            for character in ("s", "<", '"', "𝕏"):
                wide = character * 400

                def big(number, no_link=""):
                    return item(str(number) + wide, summary=str(number) + wide, pr="https://example.test/" + "x" * 69, no_link=no_link)

                def rows(prefix):
                    return [replace(row(prefix + str(n) + wide, depth=min(n, 2), status=status, spans=spans, open_seconds=604800, stands_for=99999, model=f"{prefix}.{n}" + wide),
                                    seconds=604800) for n in range(40)]

                groups = [group(big(number, "refused" if number % 2 else "cut"), *rows(str(number))) for number in range(40)]
                unfolded = page(*groups, items=[big(number) for number in range(100, 140)], coordinator=row("coordinator", spans=spans, model=wide), hidden=hidden, name=wide)
                totals = MOD["Totals"](9999999, 9999999, 9999999, 9999999)
                largest = MOD["cap_everything"](replace(unfolded, window=MOD["Window"](1790000000.0, 1790604799.0), totals=totals, legend=legend))
                self.assertEqual((len(largest.groups), [len(one.rows) for one in largest.groups], len(largest.items), len(MOD["notes"](largest, MOD["items_of"](largest)))), (6, [6] * 6, 8, 12))
                self.assertEqual((MOD["wire"](largest)["w"][2], len(MOD["wire"](largest)["M"])), ("167h 59m 59s", 37))
                sizes[status, character] = len(MOD["render_html"](largest, same).encode())
        self.assertEqual({case: size for case, size in sizes.items() if size + len("\n") > 17000}, {})
class BudgetTest(OutputCase):
    def test_stdout_of_the_html_form_with_its_newline_is_at_most_max_bytes_at_the_exact_size_and_one_byte_under_it(self):
        fixture = busy(self.fixture).write()
        whole = fixture.stdout_bytes("--max-bytes", "500000")
        self.assertTrue(len(whole) > 16001 and whole.endswith(b"</script>\n"))
        self.assertEqual(fixture.stdout_bytes("--max-bytes", str(len(whole))), whole)
        under = fixture.stdout_bytes("--max-bytes", str(len(whole) - 1))
        self.assertLess(len(under), len(whole))
        self.assertLessEqual(len(fixture.stdout_bytes()), 16000)

    def long_values(self):
        """A store name, a summary, a work item id, a pull request number, and a model that are each valid and thousands of characters long, and a written title of 60 characters."""
        fixture = one_running(self.fixture)
        fixture.meta["restaurant"] = "name " * 10000
        fixture.units[0].update(summary="word " * 10000, pr="https://git.example/o/r/pull/" + "7" * 20000)
        fixture.unit("D" + "9" * 5000, "in-progress", "long id", pr="https://git.example/o/r/pull/8")
        fixture.thread(delegated(worker(1), "helper-task"), title="t" * 60, model="m" * 30000, parent=worker(1), turns=(("failed", 20, 10),))
        return fixture.write()

    def test_stdout_of_the_text_form_with_its_newline_is_at_most_max_bytes_at_the_exact_size_and_one_byte_under_it_where_it_marks_each_cut_and_counts_the_link(self):
        fixture = self.long_values()
        whole = fixture.stdout_bytes("--text", "--max-bytes", "500000")
        self.assertTrue(len(whole) > 100000 and whole.endswith(b" at work\n"), len(whole))
        self.assertEqual(fixture.stdout_bytes("--text", "--max-bytes", str(len(whole))), whole)
        under = fixture.stdout_bytes("--text", "--max-bytes", str(len(whole) - 1))
        self.assertLess(len(under), 16000)
        self.assertLessEqual(len(fixture.stdout_bytes("--text")), 16000)
        self.assertEqual(len(whole), 125588)
        self.assertEqual(under.decode().split("\n"), [
            "Agent activity for name name name name name name nam…, last 3h",
            "1 running now, 1 agent, 1 sub-agent, 1 failed",
            "Models",
            "  " + "m" * 47 + "…   1",
            "  model-a   1",
            "Running now",
            "  D7 worker   running for 42m",
            "Work items",
            "  D7   working   word word word word word word wor…   1 agent, 1 sub-agent, 73m at work",
            "  D6   landing   Queue fix   no activity in this window   " + PR6,
            "  D99999999…   working   long id   no activity in this window   https://git.example/o/r/pull/8",
            "Failed",
            "  D7 " + "t" * 17 + "…   " + "m" * 21 + "…   10m at work",
            "Notes",
            "  1 pull request link is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            ""])
        self.assertEqual(fixture.stdout_bytes("--text"), under)
        self.assertEqual(fixture.stdout_bytes("--text", "--max-bytes", "16000"), under)

    def test_stdout_of_the_html_form_with_very_long_values_is_at_most_max_bytes_at_the_exact_size_and_one_byte_under_it(self):
        fixture = self.long_values()
        whole = fixture.stdout_bytes("--max-bytes", "500000")
        self.assertTrue(125000 < len(whole) <= 500000 and whole.endswith(b"</script>\n"), len(whole))
        self.assertEqual(fixture.stdout_bytes("--max-bytes", str(len(whole))), whole)
        under = fixture.stdout_bytes("--max-bytes", str(len(whole) - 1))
        self.assertLess(len(under), 16000)
        self.assertEqual(fixture.stdout_bytes(), under)
        data = data_of(under.decode()[:-1])
        self.assertEqual((data["c"], [item[:2] + item[4:5] for item in data["I"]], data["M"], data["N"]), (
            "name name name name name name nam…", [["D6", "Queue fix", PR6], ["D7", "word word word word word word wor…", ""], ["D99999999…", "long id", "https://git.example/o/r/pull/8"]],
            ["model-a", "m" * 21 + "…"], ["1 pull request link is not shown, because the page is at its size limit. Use a larger --max-bytes to see more."]))

    def test_largest_plain_form_of_a_shortened_page_is_under_8000_bytes_with_every_list_full_every_note_and_every_string_over_its_byte_limit(self):
        counts = ("dropped_items", "dropped_agents", "cut_agents", "cut_lines", "cut_in_flight", "other_threads", "unknown_status", "by_request_name", "unstarted")
        hidden = MOD["Hidden"](**dict.fromkeys(counts, 9999999))
        sizes = {}
        for character in ("s", "<", '"', "𝕏"):
            wide = character * 400

            def big(number, no_link=""):
                return item(str(number) + wide, summary=str(number) + wide, pr="https://example.test/" + "x" * 69, no_link=no_link)

            def rows(prefix, status):
                return [replace(row(prefix + str(n) + wide, depth=min(n, 2), status=status, open_seconds=None if status == "failed" else 604800, stands_for=99999, model=str(n) + wide),
                                seconds=604800) for n in range(12)]

            groups = [replace(group(big(number, "refused" if number % 2 else "cut"), *rows("a", "running"), *rows("b", "queued"), *rows("c", "failed")), agents=9999999, subagents=9999999)
                      for number in range(12)]
            groups.append(replace(group(None, *rows("d", "failed")), agents=9999999, subagents=9999999))
            unfolded = replace(page(*groups, items=[big(number) for number in range(100, 140)], hidden=hidden, name=wide),
                               window=MOD["Window"](1790000000.0, 1790604799.0), totals=MOD["Totals"](9999999, 9999999, 9999999, 9999999))
            lines = MOD["render_text"](MOD["shortened"](unfolded), same).split("\n")
            self.assertEqual(([lines.index(heading) for heading in ("Running now", "Queued", "Work items", "Failed", "Notes")], len(lines)), ([4, 13, 19, 31, 37], 50))
            self.assertEqual((lines[12], lines[18], lines[29], lines[36]), ("  and 137 more running agents", "  and 140 more queued agents", "  and 43 more work items", "  and 152 more failed agents"))
            sizes[character] = len("\n".join(lines).encode())
        self.assertEqual({character: size for character, size in sizes.items() if size >= 8000}, {})

    def test_stdout_is_utf8_under_a_locale_that_is_not(self):
        fixture = one_running(self.fixture)
        fixture.units[0]["summary"] = "Agent activity page 𝔸é"
        fixture.write()
        ascii_locale = {"LC_ALL": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"}
        for form in ((), ("--text",)):
            with self.subTest(form):
                result = in_process.finished(
                    in_process.Reason.INHERITED_ENVIRONMENT,
                    replace(fixture.command(*form, env=ascii_locale), text=False),
                )
                self.assertEqual((result.returncode, result.stderr), (0, b""), result.stderr)
                self.assertIn("Agent activity page 𝔸é".encode(), result.stdout)

    def test_link_cut_by_the_last_fold_is_counted_in_a_note(self):
        fixture = extreme(self.fixture)
        for number, unit in enumerate(fixture.units, start=1):
            unit["pr"] = "https://example.test/" + "o" * 400 + "/" + "r" * 400 + f"/pull/{number}"
        fixture.write()
        data = data_of(self.document())
        cut = sum(not item[4] for item in data["I"])
        self.assertEqual((cut, len(data["I"]) >= 8), (len(data["I"]), True))
        self.assertIn(f"{cut} pull request links are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.", data["N"])
        whole = data_of(self.document("--max-bytes", "500000"))
        self.assertEqual(([item[4] for item in whole["I"] if not item[4]], [note for note in whole["N"] if "pull request" in note]), ([], []))

    def test_text_form_of_a_page_with_a_30000_character_model_of_m_and_request_name_helper_task_is_125588_bytes_within_2_seconds(self):
        fixture = self.long_values()
        started = time.perf_counter()
        whole = fixture.stdout_bytes("--text", "--max-bytes", "500000")
        elapsed = time.perf_counter() - started
        self.assertEqual(len(whole), 125588)
        self.assertNotIn(b"helper-task", whole)
        self.assertLess(elapsed, 2.0, elapsed)
