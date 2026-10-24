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
class CapTest(ActivityCase):
    def test_cap_everything_keeps_6_groups_with_those_that_have_a_running_row_first(self):
        groups = [group(item(f"D{number}"), row("worker"), row("helper", depth=1, stands_for=3)) for number in range(1, 9)]
        groups.append(group(None, row("stray", status="running", open_seconds=5)))
        folded = MOD["cap_everything"](page(*groups, items=[item("D1")]))
        self.assertEqual(list(drawn(folded)), ["D1", "D2", "D3", "D4", "D5", None])
        self.assertEqual((folded.hidden.cut_agents, folded.fold), (12, 1))
        self.assertEqual(MOD["notes"](folded, MOD["items_of"](folded)), [
            "15 sub-agents that are done or stopped are shown as 5 summary rows.",
            "12 more agents are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."])
    def test_cap_everything_counts_a_cut_group_of_a_merged_item_as_an_item_not_shown_and_a_cut_group_of_an_item_in_flight_by_its_agents(self):
        groups = [group(item(f"D{number}"), row("worker", status="running", open_seconds=5)) for number in range(1, 7)]
        groups.append(group(item("D7", in_flight=False), row("worker"), row("helper", depth=1, status="waiting", stands_for=2)))
        groups.append(group(item("D8"), row("worker")))
        groups.append(group(None, row("stray")))
        folded = MOD["cap_everything"](page(*groups, items=[one.item for one in groups if one.item and one.item.in_flight]))
        self.assertEqual(list(drawn(folded)), ["D1", "D2", "D3", "D4", "D5", "D6"])
        hidden = folded.hidden
        self.assertEqual((hidden.dropped_items, hidden.dropped_agents, hidden.cut_agents, hidden.cut_in_flight, [each.id for each in folded.items]),
                         (1, 3, 2, 0, ["D1", "D2", "D3", "D4", "D5", "D6", "D8"]))
        self.assertEqual(MOD["notes"](folded, MOD["items_of"](folded)), [
            "1 merged or dropped work item with 3 agents is not shown. Use a larger --max-bytes to see more.",
            "2 more agents are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."])
    def test_cap_everything_keeps_6_rows_a_group_with_running_and_failed_rows_first_and_moves_a_row_up_when_its_parent_is_cut(self):
        rows = [row("a"), row("b", depth=1), row("c", depth=2, status="running", open_seconds=9), row("d", depth=2)]
        rows += [row(label, status="failed") for label in "efghi"] + [row("j")]
        folded = MOD["cap_everything"](page(group(item("D1"), *rows)))
        self.assertEqual(drawn(folded), {"D1": [(0, "c", 1), (0, "e", 1), (0, "f", 1), (0, "g", 1), (0, "h", 1), (0, "i", 1)]})
        self.assertEqual(folded.hidden.cut_agents, 4)
        kept = MOD["cap_everything"](page(group(item("D1"), row("a"), row("b", depth=1), row("c", depth=2, status="running", open_seconds=9))))
        self.assertEqual(drawn(kept), {"D1": [(0, "a", 1), (1, "b", 1), (2, "c", 1)]})
    def test_cap_everything_joins_the_nearest_bars_until_4_are_left_on_a_row_and_8_on_the_coordinators(self):
        spans = ((0, 10, "done"), (12, 10, "failed"), (100, 10, "done"), (115, 10, "stopped"), (300, 10, "done"), (500, 10, "running"), (700, 10, "done"))
        folded = MOD["cap_everything"](page(group(item("D1"), row("a", spans=spans)), coordinator=row("coordinator", spans=spans + ((900, 5, "done"), (906, 5, "done")))))
        self.assertEqual([(span.x, span.w, span.status.value) for span in folded.groups[0].rows[0].spans],
                         [(300, 10, "done"), (500, 10, "running"), (700, 10, "done"), (0, 125, "failed")])
        self.assertEqual([(span.x, span.w, span.status.value) for span in folded.coordinator.spans],
                         [(0, 10, "done"), (100, 10, "done"), (115, 10, "stopped"), (300, 10, "done"), (500, 10, "running"), (700, 10, "done"), (900, 11, "done"), (12, 10, "failed")])
    def test_joined_makes_one_bar_of_two_across_a_gap_of_99_and_the_footer_says_bars_may_join_across_gaps(self):
        spans = MOD["joined"]([MOD["Span"](x, 1, MOD["Status"].DONE) for x in (0, 100, 300, 600, 900)], 4)
        self.assertEqual([(span.x, span.w) for span in spans], [(0, 101), (300, 1), (600, 1), (900, 1)])
        self.assertIn("'. Bars show turn or delegation intervals and may join across gaps. Striped bars include running intervals. Outlined bars include queued intervals. Faded bars include stopped intervals.'", MOD["RENDERER"])
        self.assertNotIn("time an agent was at work", MOD["RENDERER"])
    def test_cap_everything_lists_8_in_flight_items_with_those_of_a_group_on_the_page_first(self):
        items = [item(f"D{number}") for number in range(1, 13)]
        folded = MOD["cap_everything"](page(group(items[10], row("worker")), items=items))
        self.assertEqual([each.id for each in folded.items], ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D11"])
        self.assertEqual(folded.hidden.cut_in_flight, 4)
        self.assertEqual(MOD["notes"](folded, MOD["items_of"](folded)), ["4 more work items in flight are not listed."])
    def test_cap_everything_clips_strings_by_the_bytes_of_their_entity_form_and_drops_a_link_whose_entity_form_is_over_90_bytes(self):
        long = item("D" + "1" * 20, summary="<" * 30, pr="https://example.test/" + "x" * 70)
        short = item("D2", summary="𝔸" * 30, pr="https://example.test/" + "x" * 69)
        quoted = item("D3", summary='"' * 30, pr="https://example.test/?" + "&" * 14)
        plain = item("D4", summary="s" * 37, pr="https://example.test/?" + "&" * 13)
        groups = [group(long, row("é" * 30, model="m" * 40)), group(short, row("\\" * 30)), group(quoted, row("worker")), group(plain, row("worker"))]
        folded = MOD["cap_everything"](page(*groups, items=[long, short, quoted, plain], name="𝔸" * 20))
        first, second, third, fourth = folded.groups
        self.assertEqual((first.item.id, first.item.summary, first.item.pr), ("D" + "1" * 8 + "…", "<" * 8 + "…", ""))
        self.assertEqual((second.item.summary, second.item.pr, second.rows[0].label), ("𝔸" * 8 + "…", "https://example.test/" + "x" * 69, "\\" * 3 + "…"))
        self.assertEqual((third.item.summary, third.item.pr), ('"' * 5 + "…", ""))
        self.assertEqual((fourth.item.summary, fourth.item.pr), ("s" * 33 + "…", "https://example.test/?" + "&" * 13))
        self.assertEqual((first.rows[0].label, first.rows[0].model, folded.name), ("é" * 8 + "…", "m" * 21 + "…", "𝔸" * 8 + "…"))
        self.assertEqual(MOD["wire"](folded)["G"][0][1][0][3], MOD["model_slot"]("m" * 40))
        self.assertEqual(folded.items, (first.item, second.item, third.item, fourth.item))
        self.assertEqual([each.no_link for each in folded.items], ["cut", "", "cut", ""])
        self.assertEqual(MOD["notes"](folded, MOD["items_of"](folded)), ["2 pull request links are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."])
    def test_every_fold_on_the_busy_fixture_and_on_one_with_failed_agents_in_merged_units_keeps_the_agent_count_the_totals_and_the_legend(self):
        for build, totals, in_flight in ((busy, MOD["Totals"](5, 36, 364, 3), 8), (day, MOD["Totals"](8, 36, 364, 32), 6)):
            fixture = self.fixture = Fixture(self.fixture.root / build.__name__)
            unfolded = folded = page_of(build(fixture))
            self.assertEqual((unfolded.totals, sum(each.stands_for for one in unfolded.groups for each in one.rows)), (totals, 400))
            shown = []
            for index, fold in enumerate(MOD["FOLDS"]):
                folded = fold(folded)
                rows = [each for one in folded.groups for each in one.rows]
                shown.append(len(rows))
                with self.subTest(build.__name__, fold=index):
                    self.assertEqual(sum(each.stands_for for each in rows) + folded.hidden.dropped_agents + folded.hidden.cut_agents, 400)
                    self.assertEqual([each.id for each in MOD["items_of"](folded) if not each.pr and not each.no_link], [])
                    self.assertEqual((folded.totals, folded.legend, folded.fold), (unfolded.totals, unfolded.legend, index + 1))
            self.assertEqual(shown, sorted(shown, reverse=True))
            self.assertEqual((len(folded.groups), len(folded.items), shown[-1] < shown[0]), (6, in_flight, True))
    def test_every_fold_of_a_page_whose_items_all_have_a_link_leaves_each_item_on_the_page_with_its_link_or_counted_in_a_note(self):
        fixture = busy(self.fixture)
        for number, unit in enumerate(fixture.units, start=1):
            unit["pr"] = ("https://example.test/" + "o" * 60 + f"/r/pull/{number}", f"https://example.test/o/r/pull/{number}", f"http://example.test/o/r/pull/{number}")[number % 3]
        folded = page_of(fixture)
        for index, fold in enumerate(MOD["FOLDS"]):
            folded = fold(folded)
            on_page = MOD["items_of"](folded)
            data = data_of(MOD["render_html"](folded, CLEAN))
            counted = sum(int(note.split()[0]) for note in data["N"] if "pull request link" in note)
            with self.subTest(fold=index):
                self.assertEqual((len(data["I"]), sum(bool(each[4]) for each in data["I"]) + counted), (len(on_page), len(on_page)))
        self.assertEqual([note for note in MOD["notes"](folded, MOD["items_of"](folded)) if "pull request link" in note], [
            "3 pull request links are not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            "3 pull request links are not shown. Each is not an https://host/owner/repository/pull/number address, or it holds text this page removes."])
    def test_notes_say_no_agent_ran_for_an_empty_window_and_count_unknown_statuses_requests_and_other_threads(self):
        self.assertEqual(MOD["notes"](page(), []), ["No agent or sub-agent of this coordinator ran in this window."])
        hidden = MOD["Hidden"](dropped_items=1, dropped_agents=1, cut_agents=1, cut_in_flight=1, other_threads=1, unknown_status=1, by_request_name=1, unstarted=1)
        linked = page(group(item("D1", no_link="cut"), row("worker")), items=[item("D2", no_link="refused"), item("D3", pr=PR7)], hidden=hidden)
        self.assertEqual(MOD["notes"](linked, MOD["items_of"](linked)), [
            "1 merged or dropped work item with 1 agent is not shown. Use a larger --max-bytes to see more.",
            "1 more agent is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            "1 more work item in flight is not listed.",
            "1 pull request link is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
            "1 pull request link is not shown. It is not an https://host/owner/repository/pull/number address, or it holds text this page removes.",
            "T3 lists 1 sub-agent with no thread or no start time under a thread this page read. It is not shown.",
            "T3 gave 1 status this tool reads as unknown.",
            "1 agent is grouped by the name of the request that started it.",
            "1 other thread ran in this coordinator's T3 project outside this coordinator."])
        several = MOD["Hidden"](other_threads=3, unknown_status=2, by_request_name=9)
        self.assertEqual(MOD["notes"](page(group(item("D1"), row("worker")), hidden=several), [item("D1")]), [
            "T3 gave 2 statuses this tool reads as unknown.",
            "9 agents are grouped by the name of the request that started them.",
            "3 other threads ran in this coordinator's T3 project outside this coordinator."])
