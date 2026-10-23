import argparse
import fcntl
import fnmatch
import functools
import hashlib
import io
import json
import os
import posixpath
import re
import runpy
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager, redirect_stderr
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from brigade_lib import seam, spend
from brigade_lib.leases import holding_blocks
from brigade_lib.reviews import CLAIMS_LINE, CLAIMS_STATES, report_threads
from brigade_lib.shared import BrigadeError, MalformedTable, clean, plural
from brigade_lib.store import CLOSED_GOAL_STATES, IN_FLIGHT_STATES, KINDS, Restaurant, holder_prefix, is_admin, parse_row, reporting_of, siblings, store_path
from brigade_lib.tickets import EVERY_ROW, Label, Tally, finished_requests, handed_count, intake_list, read_labels, request_files, sibling_named, tell

SECTIONS = {
    ("dish", "merged"): "Merged",
    ("dish", "passed"): "Passed review, not submitted",
    ("dish", "queued"): "Waiting to land",
    ("dish", "sent-back"): "Sent back after review",
    ("dish", "blocked"): "Blocked",
    ("mode", "full"): "Moved to full mode",
    ("dish", "in-progress"): "In progress",
    ("dish", "in-review"): "In progress",
    ("ticket", "waiting"): "New tickets, not started",
    ("ticket", "moved"): "Handed to another coordinator",
    ("dish", "dropped"): "Dropped",
    ("ticket", "dropped"): "Dropped",
}


GROUPED_SECTIONS = tuple(SECTIONS[key] for key in (("dish", "merged"), ("dish", "in-progress"), ("ticket", "waiting")))


def tally_words(tally):
    return (f"{len(tally.waiting)} waiting, {len(tally.in_flight)} in flight, {len(tally.merged)} merged, "
            f"{plural(tally.send_backs, 'send-back')}")


def goal_lines(labels, counted, every):
    """The text of goal list.

    A goal is listed when it is open or paused, or when it has a ticket waiting, an item in flight, or a ticket riding along.
    `every` lists each other goal too. The unassigned line is last.
    """
    lines = []
    for goal in labels.goals.values():
        tally, riding = counted.goals.get(goal.id, Tally()), counted.riding.get(goal.id, ())
        closed = goal.state in CLOSED_GOAL_STATES
        if closed and not every and not (tally.waiting or tally.in_flight or riding):
            continue
        words = tally_words(tally) + (f", {len(riding)} riding along" if riding else "") + (f", share {goal.share}" if goal.share else "")
        lines += [f"{goal.id} {goal.state} {goal.name}: {words}", f"  purpose: {goal.purpose}", f"  done when: {goal.done}"]
        if closed:
            lines.append(f"  closed: {goal.closed}")
    if not lines:
        lines.append("no goals" if every else "no open or paused goals")
    return "\n".join([*lines, f"unassigned: {tally_words(counted.goals.get('', Tally()))}"])


def status_label_lines(labels, counted):
    """Up to three lines of status. The counts by kind, the counts by goal, and the waiting tickets with no kind.

        kinds: feature 1 waiting; fix 1 waiting, 1 in flight; upkeep 1 in flight
        goals: G1 2 waiting, 1 in flight; G2 paused, 1 in flight; unassigned 2 waiting, 1 in flight
        untagged: 1

    An entry holds the waiting count and the in flight count that are not 0, and is absent when both are 0.
    kinds has an entry per kind in KINDS order. An item in flight with no kind is absent from kinds.
    goals has an entry per goal in file order and then one for unassigned work. A goal that is not open has its state
    after its id. The line is absent when goals.tsv has no row.
    A line with no entry is absent, and so is untagged at 0.
    """
    def entry(name, tally):
        found = [f"{len(ids)} {word}" for ids, word in ((tally.waiting, "waiting"), (tally.in_flight, "in flight")) if ids]
        return [f"{name} {', '.join(found)}"] if found else []

    lines = []
    kinds = [text for kind in KINDS for text in entry(kind, counted.kinds.get(kind, Tally()))]
    if kinds:
        lines.append(f"kinds: {'; '.join(kinds)}")
    goals = [text for goal in labels.goals.values()
             for text in entry(goal.id if goal.state == "open" else f"{goal.id} {goal.state},", counted.goals.get(goal.id, Tally()))]
    if labels.goals:
        goals += entry("unassigned", counted.goals.get("", Tally()))
    if goals:
        lines.append(f"goals: {'; '.join(goals)}")
    untagged = len(counted.kinds.get("", Tally()).waiting)
    if untagged:
        lines.append(f"untagged: {untagged}")
    return lines


def counts(restaurant):
    tickets = [row["state"] for row in restaurant.rows("rail.tsv")]
    dishes = [row["state"] for row in restaurant.rows("dishes.tsv")]
    questions = [row for row in restaurant.rows("86.tsv") if row["state"] == "open"]
    return {
        "waiting tickets": tickets.count("waiting"),
        "in progress": dishes.count("in-progress"),
        "in review": dishes.count("in-review"),
        "passed review": dishes.count("passed"),
        "waiting to land": dishes.count("queued"),
        "sent back": dishes.count("sent-back"),
        "blocked": dishes.count("blocked"),
        "merged": dishes.count("merged"),
        "decisions for you": len(questions),
        "handed to you": handed_count(restaurant),
        "requests from the executive admin": len(request_files(restaurant)[0]),
    }


def status_line(restaurant):
    return ", ".join(f"{label}: {value}" for label, value in counts(restaurant).items() if value) or "nothing on record"


def unclaimed_reports(restaurant):
    """(id, state) of each dish in CLAIMS_STATES whose reports/<id>.md is a `file` with no line CLAIMS_LINE matches.

    The report is decoded as UTF-8 with replacement. An entry that report_stat does not call a `file` is not read.
    """
    unclaimed = []
    for dish in restaurant.rows("dishes.tsv"):
        name = f"{dish['id']}.md"
        if dish["state"] not in CLAIMS_STATES or seam.report_entry(restaurant, name) != "file":
            continue
        text = (restaurant.dir / "reports" / name).read_bytes().decode("utf-8", "replace")
        if not CLAIMS_LINE.search(text):
            unclaimed.append((dish["id"], dish["state"]))
    return unclaimed


CLAIMS_CHECK_ROLE = "claims check"
CLAIMS_RUN_CAP = 2


def claims_cap_reports(restaurant):
    """(id, count) of each dish in IN_FLIGHT_STATES whose reports/<id>.md ## Threads section holds more claims check children than CLAIMS_RUN_CAP.

    The report is decoded as UTF-8 with replacement. An entry that report_stat does not call a `file` is not read.
    A child comes from report_threads, so a line of the section that names no child is not counted.
    """
    over = []
    for dish in restaurant.rows("dishes.tsv"):
        name = f"{dish['id']}.md"
        if dish["state"] not in IN_FLIGHT_STATES or seam.report_entry(restaurant, name) != "file":
            continue
        text = (restaurant.dir / "reports" / name).read_bytes().decode("utf-8", "replace")
        children, _ = report_threads(text)
        runs = sum(1 for child in children if child["role"] == CLAIMS_CHECK_ROLE)
        if runs > CLAIMS_RUN_CAP:
            over.append((dish["id"], runs))
    return over


