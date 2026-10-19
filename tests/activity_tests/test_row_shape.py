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
class RowShapeTest(OutputCase):
    """The fixture's rows are the coordinator, then a worker, then the worker's delegated child."""

    def staffed(self, name, spoil=lambda fixture: None):
        fixture = self.fixture = Fixture(self.root / name)
        fixture.now = datetime(2026, 10, 10, 0, 30, tzinfo=timezone.utc).timestamp()
        fixture.coordinator()
        fixture.unit("D7", thread=worker(1))
        fixture.thread(worker(1), title="D7 worker", turns=(("completed", 45, 15),))
        fixture.thread(delegated(worker(1), "kit-d7-part"), title="part", parent=worker(1), turns=(("completed", 25, 22),))
        spoil(fixture)
        return fixture.write()

    def refused(self, name, spoil):
        self.staffed(name, spoil)
        return self.fails(3)

    def test_timestamp_in_another_shape_exits_3_wherever_its_text_sorts(self):
        texts = ("", "0000-bad", "bad", "2026-13-45T00:00:00.000Z", "2026-10-09T23:45:00", "2026-10-09 23:45:00.000Z", "2026-10-09T23:45:00.000000Z", 5, b"2026-10-09T23:45:00.000Z")
        columns = (("turns", "orchestration_v2_projection_runs.requested_at", 2, False), ("turns", "orchestration_v2_projection_runs.completed_at", 3, True),
                   ("delegations", "orchestration_v2_projection_subagents.started_at", 4, True), ("delegations", "orchestration_v2_projection_subagents.completed_at", 5, True))
        for rows, where, column, nullable in columns:
            for number, text in enumerate(texts if nullable else (*texts, None)):
                with self.subTest(where=where, text=text):
                    line = self.refused(f"{rows}-{column}-{number}", lambda fixture: put(getattr(fixture, rows), -1, column, text))
                    self.assertEqual(line, f"activity: T3's {where} is not a timestamp; {CHANGED}")

    def test_turn_written_with_a_utc_offset_across_midnight_is_drawn_like_the_same_instants_written_with_z(self):
        for hours in (None, 0, -7, 14):
            with self.subTest(hours):
                def offset(fixture):
                    put(fixture.turns, 1, 2, fixture.stamp_at_offset(45, hours))
                    put(fixture.turns, 1, 3, fixture.stamp_at_offset(15, hours))

                fixture = self.staffed(f"offset-{hours}", offset if hours is not None else lambda fixture: None)
                self.assertEqual(fixture.turns[1][2:], {None: ("2026-10-09T23:45:00.000Z", "2026-10-10T00:15:00.000Z"), 0: ("2026-10-09T23:45:00.000+00:00", "2026-10-10T00:15:00.000+00:00"),
                                                        -7: ("2026-10-09T16:45:00.000-07:00", "2026-10-09T17:15:00.000-07:00"),
                                                        14: ("2026-10-10T13:45:00.000+14:00", "2026-10-10T14:15:00.000+14:00")}[hours])
                data = data_of(self.document())
                self.assertEqual((data["n"], data["G"][0][1][0][5:7]), ([0, 1, 1, 0], ["30m", [750, 167, 0]]))

    def test_value_that_is_not_text_or_an_empty_id_status_or_provider_exits_3_and_names_the_column(self):
        threads, runs, subagents = "orchestration_v2_projection_threads", "orchestration_v2_projection_runs", "orchestration_v2_projection_subagents"
        cases = (("threads", 2, 1, 42, f"{threads}.title is not text"), ("threads", 2, 1, None, f"{threads}.title is not text"), ("threads", 2, 1, b"part", f"{threads}.title is not text"),
                 ("threads", 1, 2, 42, f"{threads}.default_provider is not text"), ("threads", 1, 2, None, f"{threads}.default_provider is not text"),
                 ("threads", 1, 2, "", f"{threads}.default_provider is empty"),
                 ("turns", 1, 0, None, f"{runs}.thread_id is not text"), ("turns", 1, 0, 42, f"{runs}.thread_id is not text"), ("turns", 1, 0, "", f"{runs}.thread_id is empty"),
                 ("turns", 1, 1, None, f"{runs}.status is not text"), ("turns", 1, 1, "", f"{runs}.status is empty"),
                 ("delegations", 0, 0, 42, f"{subagents}.subagent_id is not text"), ("delegations", 0, 0, None, f"{subagents}.subagent_id is not text"),
                 ("delegations", 0, 1, None, f"{subagents}.thread_id is not text"), ("delegations", 0, 1, "", f"{subagents}.thread_id is empty"),
                 ("delegations", 0, 2, 42, f"{subagents}.child_thread_id is not text"), ("delegations", 0, 2, "", f"{subagents}.child_thread_id is empty"),
                 ("delegations", 0, 3, 42, f"{subagents}.status is not text"), ("delegations", 0, 3, None, f"{subagents}.status is not text"))
        for number, (rows, index, column, value, words) in enumerate(cases):
            with self.subTest(words=words, value=value):
                line = self.refused(f"type-{number}", lambda fixture: put(getattr(fixture, rows), index, column, value))
                self.assertEqual(line, f"activity: T3's {words}; {CHANGED}")

    def test_thread_payload_whose_model_selection_holds_no_model_or_no_instance_id_text_exits_3(self):
        cases = (('{"modelSelection":{}}', "model"), ('{"modelSelection":{"model":42,"instanceId":"codex"}}', "model"), ('{"modelSelection":[]}', "model"),
                 ('{"modelSelection":null}', "model"), ('{"modelSelection":{"model":"","instanceId":"codex"}}', "model"),
                 ('{"modelSelection":{"model":"model-a"}}', "instanceId"), ('{"modelSelection":{"model":"model-a","instanceId":7}}', "instanceId"),
                 ('{"modelSelection":{"model":"model-a","instanceId":""}}', "instanceId"))
        for number, (payload, key) in enumerate(cases):
            with self.subTest(payload):
                line = self.refused(f"payload-{number}", lambda fixture: put(fixture.threads, 0, 3, payload))
                self.assertEqual(line, f"activity: T3's thread payload holds no text at modelSelection.{key}; {CHANGED}")

    def test_payload_nested_too_deep_to_parse_or_not_text_exits_3(self):
        for number, payload in enumerate(("[" * 100000, b'{"modelSelection":{"model":"model-a","instanceId":"codex"}}', 42, None)):
            with self.subTest(number):
                line = self.refused(f"deep-{number}", lambda fixture: put(fixture.threads, 0, 3, payload))
                self.assertEqual(line, f"activity: T3's thread payload has no modelSelection; {CHANGED}")

    def test_thread_with_a_turn_and_no_thread_row_exits_3(self):
        for rows, index in (("threads", 1), ("threads", 2)):
            with self.subTest(index):
                line = self.refused(f"absent-{index}", lambda fixture: put(getattr(fixture, rows), index, 0, None))
                self.assertEqual(line, "activity: T3's orchestration_v2_projection_threads has no row for a thread that orchestration_v2_projection_runs "
                                       f"or orchestration_v2_projection_subagents names; {CHANGED}")

    def test_sub_agent_with_no_thread_or_no_start_time_is_counted_in_a_note_and_one_with_a_turn_is_drawn_from_its_turn(self):
        def pending(fixture):
            fixture.delegations.append(("node:nodemarker-pending", worker(1), None, "running", None, None))
            fixture.thread(delegated(worker(1), "kit-d7-late"), title="late", parent=worker(1), delegation=("running", 20, None))
            put(fixture.delegations, -1, 4, None)
            put(fixture.delegations, 0, 4, None)
            fixture.delegations.append(("node:nodemarker-elsewhere", "mcp:threadmarker-other-project", None, "running", None, None))

        fixture = self.staffed("pending", pending)
        data = data_of(self.document())
        self.assertEqual((data["n"], [line[:2] for line in data["G"][0][1]]), ([0, 1, 1, 0], [[0, "worker"], [1, "part"]]))
        self.assertEqual(data["N"], ["T3 lists 2 sub-agents with no thread or no start time under threads this page read. They are not shown."])
        self.assertIn("  T3 lists 2 sub-agents with no thread or no start time under threads this page read. They are not shown.", self.out("--text").split("\n"))

    def test_sub_agent_rows_that_make_a_thread_its_own_parent_or_ancestor_exit_3_in_both_forms(self):
        def looped(*pairs, started):
            def spoil(fixture):
                fixture.thread(worker(2), title="second worker", turns=(("running", 4, None),))
                fixture.thread("mcp:threadmarker-other-project", turns=(("running", 3, None),))
                fixture.turns.append((worker(1), "running", fixture.stamp(5), None))
                for number, (parent, child) in enumerate(pairs):
                    fixture.delegations.append((f"node:nodemarker-loop-{number}", parent, child, "running", fixture.stamp(6) if started else None, None))
            return spoil

        other = "mcp:threadmarker-other-project"
        cases = {"self": ((worker(1), worker(1)),), "two": ((worker(1), worker(2)), (worker(2), worker(1))), "three": ((worker(1), worker(2)), (worker(2), other), (other, worker(1))),
                 "outside": ((other, other),), "child": ((delegated(worker(1), "kit-d7-part"), worker(1)),)}
        for name, pairs in cases.items():
            for started in (False, True):
                with self.subTest(name, started=started):
                    self.staffed(f"{name}-{started}", looped(*pairs, started=started))
                    line = f"activity: T3's orchestration_v2_projection_subagents makes a thread its own ancestor; {CHANGED}"
                    self.assertEqual((self.fails(3), self.fails(3, "--text")), (line, line))

    def test_self_parent_row_and_two_thread_cycle_that_a_later_row_overrides_and_self_parent_row_with_no_start_time_after_a_valid_row_exit_3_in_both_forms(self):
        cases = {"self": ((worker(1), worker(1), 10), (COORDINATOR, worker(1), 5)),
                 "unstarted": ((COORDINATOR, worker(1), 10), (worker(1), worker(1), None)),
                 "two": ((worker(2), worker(1), 10), (worker(1), worker(2), 10), (COORDINATOR, worker(1), 5))}
        for name, rows in cases.items():
            def spoil(fixture, rows=rows):
                fixture.thread(worker(2), title="second worker", turns=(("running", 4, None),))
                fixture.turns.append((worker(1), "running", fixture.stamp(5), None))
                fixture.delegations += [(f"node:nodemarker-masked-{number}", parent, child, "running", fixture.stamp(start), None) for number, (parent, child, start) in enumerate(rows)]

            with self.subTest(name):
                self.staffed(f"masked-{name}", spoil)
                line = f"activity: T3's orchestration_v2_projection_subagents makes a thread its own ancestor; {CHANGED}"
                self.assertEqual((self.fails(3), self.fails(3, "--text")), (line, line))

    def test_sub_agent_rows_with_no_start_time_that_make_no_loop_are_read_and_the_running_worker_is_counted(self):
        def chained(fixture):
            fixture.thread(worker(2), title="second worker", turns=(("running", 4, None),))
            fixture.turns.append((worker(1), "running", fixture.stamp(5), None))
            fixture.delegations.append(("node:nodemarker-chain", worker(1), worker(2), "running", None, None))

        fixture = self.staffed("chain", chained)
        data = data_of(self.document())
        self.assertEqual((data["n"], [line[:2] for line in data["G"][0][1]], data["N"]), ([2, 1, 2, 0], [[0, "worker"], [1, "part"], [1, "second worker"]], []))
        self.assertEqual(self.out("--text").split("\n")[1], "2 running now, 1 agent, 2 sub-agents, 0 failed")

    def test_no_count_is_negative_and_a_page_with_a_running_agent_never_says_no_agent_ran_for_150_random_sets_of_sub_agent_rows(self):
        """Each set either makes a thread its own ancestor, which is refused, or gives a page whose every count is at least 0 before and after each fold."""
        randomly = random.Random(125)
        threads = (COORDINATOR, worker(1), worker(2), native(1), native(2), "mcp:threadmarker-other-project")
        outcomes = {"refused": 0, "running": 0, "quiet": 0}
        for number in range(150):
            fixture = Fixture(self.root / f"random-{number}")
            fixture.unit("D7", thread=worker(1))
            fixture.unit("D8", "merged", thread=worker(2))
            for thread in threads:
                turns = [(randomly.choice(("running", "queued", "completed", "failed", "interrupted")), start, end)
                         for start, end in ((randomly.randint(20, 170), randomly.randint(5, 19)),) * randomly.randint(0, 2)]
                fixture.thread(thread, turns=tuple((status, start, None if status in ("running", "queued") else end) for status, start, end in turns))
            for index in range(randomly.randint(0, 7)):
                parent, child = randomly.choice(threads), randomly.choice(threads + (None,))
                start = randomly.choice((None, randomly.randint(20, 170)))
                fixture.delegations.append((f"node:nodemarker-{number}-{index}", parent, child, randomly.choice(("running", "completed", "failed")), fixture.stamp(start), None))
            try:
                store, t3, window = fixture.write().read()
            except MOD["SourceError"] as error:
                self.assertEqual(str(error), f"T3's orchestration_v2_projection_subagents makes a thread its own ancestor; {CHANGED}")
                outcomes["refused"] += 1
                continue
            privacy = MOD["privacy_of"](store, t3)
            page = MOD["build_page"](store, t3, window, privacy)
            running = [thread for thread, one in t3.agents.items() if thread != COORDINATOR and any(turn.status.value == "running" and turn.end is None for turn in MOD["stretches"](one))]
            outcomes["running" if running else "quiet"] += 1
            for fold, folded in enumerate([page, *(page := step(page) for step in MOD["FOLDS"])]):
                with self.subTest(number, fold=fold):
                    counts = [*vars(folded.totals).values(), *vars(folded.hidden).values(), *(count for one in folded.groups for count in (one.agents, one.subagents)),
                              *(each.stands_for for one in folded.groups for each in one.rows), *(each.seconds for one in folded.groups for each in one.rows)]
                    self.assertEqual([count for count in counts if count < 0], [])
                    said = MOD["notes"](folded, MOD["items_of"](folded))
                    empty_note = "No agent or sub-agent of this coordinator ran in this window." in said
                    self.assertEqual((folded.totals.running, empty_note), (len(running), False) if running else (0, empty_note))
                    self.assertIsNone(re.search(r"-[0-9]", MOD["render_text"](folded, privacy.clean).split("\n")[1]))
                    self.assertEqual(data_of(MOD["render_html"](folded, privacy.clean))["n"], list(vars(folded.totals).values()))
        self.assertEqual([name for name, times in outcomes.items() if times < 20], [])

    def test_sqlite_error_that_quotes_a_value_exits_3_without_the_value(self):
        fixture = self.staffed("undecodable")
        connection = sqlite3.connect(fixture.database)
        connection.execute("update orchestration_v2_projection_threads set title = cast(x'70617468ff6d61726b6572' as text) where title = 'part'")
        connection.commit()
        connection.close()
        line = self.fails(3)
        self.assertEqual(line, "activity: cannot read T3's database (a text value is not UTF-8); check that T3 Code is running and try again")
