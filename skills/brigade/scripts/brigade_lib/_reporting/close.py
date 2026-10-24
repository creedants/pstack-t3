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
from brigade_lib._notes import share_lines
from brigade_lib.leases import holding_blocks
from brigade_lib.reviews import CLAIMS_LINE, CLAIMS_STATES, hit_section, send_back_section
from brigade_lib.shared import BrigadeError, MalformedTable, clean, plural
from brigade_lib.store import CLOSED_GOAL_STATES, KINDS, Restaurant, holder_prefix, is_admin, parse_row, reporting_of, siblings, store_path
from brigade_lib.tickets import EVERY_ROW, Label, Tally, finished_requests, handed_count, intake_list, read_labels, request_files, sibling_named, tell
from brigade_lib._reporting.admin import admin_sections
from brigade_lib._reporting.status import GROUPED_SECTIONS, SECTIONS, status_line

REPAIR = {
    "link": "remove the link and leave its target as it is",
    "other": "move it out of reports/",
    "elsewhere": "replace the symbolic link at reports/ with a directory and leave the link's target as it is",
}


def section_lines(title, items, dishes, labels):
    """The lines of one report section under its title.

    A line of a section in GROUPED_SECTIONS has the kind of its item or ticket before the id, when it has one.
    When a line of such a section has a goal, the section prints its lines under one `### <goal id> <goal name>` heading
    per goal in goals.tsv order, then `### Unassigned`. A heading with no line is absent.
    """
    grouped, bullets = title in GROUPED_SECTIONS, []
    for event in items:
        of_item = event["kind"] in ("dish", "mode")
        dish = dishes.get(event["id"], {}) if of_item else {}
        tickets = f" ({dish['tickets'].replace(',', ', ')})" if dish.get("tickets") else ""
        pr = f" {dish['pr']}" if dish.get("pr") else ""
        label = (labels.items if of_item else labels.tickets).get(event["id"], Label())
        kind = f"{label.kind} " if grouped and label.kind else ""
        bullets.append((label.goal, f"- {kind}{event['id']}{tickets}: {event['note']}{pr}"))
    if not grouped or not any(goal for goal, _ in bullets):
        return [*(text for _, text in bullets), ""]
    lines = []
    headings = [*((goal.id, f"### {goal.id} {goal.name}") for goal in labels.goals.values()), ("", "### Unassigned")]
    for goal, heading in headings:
        under = [text for found, text in bullets if found == goal]
        if under:
            lines += [heading, "", *under, ""]
    return lines


def events_since(restaurant, since):
    """The latest log row of each kind and id newer than since."""
    latest = {}
    for event in restaurant.rows("log.tsv"):
        if event["at"] > since:
            latest[(event["kind"], event["id"])] = event
    return latest


def report(restaurant, write=True):
    meta = restaurant.meta
    since = meta.get("lastReportAt") or ""
    latest = events_since(restaurant, since)
    sections = {title: [] for title in SECTIONS.values()}
    for (kind, _), event in latest.items():
        title = SECTIONS.get((kind, event["state"]))
        if title:
            sections[title].append(event)
    dishes = {row["id"]: row for row in restaurant.rows("dishes.tsv")}
    labels = read_labels(restaurant, EVERY_ROW)
    stamp = seam.now()
    lines = [f"# {meta['restaurant']} report", "", f"Since {since or 'opening'}. {status_line(restaurant)}.", ""]
    for title, items in sections.items():
        if items:
            lines += [f"## {title}", "", *section_lines(title, items, dishes, labels)]
    if is_admin(meta):
        lines += admin_sections(restaurant, since)
    questions = [row for row in restaurant.rows("86.tsv") if row["state"] == "open"]
    if questions:
        lines += ["## Decisions for you", ""]
        lines += [f"- {q['id']}: {q['question']} Options: {q['options']}. Default if no answer: {q['default']}." for q in questions]
        lines.append("")
    if len(lines) == 4:
        lines += ["Nothing new.", ""]
    lines += send_back_section(restaurant.rows("pass.tsv"), stamp)
    lines += hit_section(restaurant.rows("pass.tsv"), restaurant.rows("edits.tsv"), restaurant.rows("86.tsv"), stamp)
    lines += spend_section(restaurant, since)
    lines += share_lines(restaurant)
    text = "\n".join(lines)
    path = None
    if write:
        path = restaurant.dir / "closeouts" / f"{stamp[:26].replace(':', '')}.md"
        restaurant.write(path.relative_to(restaurant.dir), text)
        restaurant.change_meta(lastReportAt=stamp)
    return text, path


def spend_section(restaurant, since):
    """The `## Spend` lines of a report, taken from `spend --by goal` for items fired at or after the UTC date of `since`.

    `since` is the ISO time of the last report, and its UTC date is the finest window `spend --since` takes, so a
    second close on the same UTC day repeats that day's rows. Each `spend_text` call runs under this store's held
    lock and its stderr stays off close's stderr. A token source spend reports as unreadable or as another schema
    keeps the goal rows and prints `tokens unavailable`. A call that raises, from a refused prices.json or a raising
    token read, is caught and rerun once with `tokens=False`; the section prints the goal rows that rerun renders
    with one `tokens unavailable` line, so the report keeps every other section.
    """
    date = since[:10] if since else None
    args = argparse.Namespace(by="goal", item=None, since=date, json=False, tokens=True, t3_home=None)
    captured = io.StringIO()
    with redirect_stderr(captured):
        try:
            text = spend.spend_text(restaurant, args)
        except Exception:
            args.tokens = False
            try:
                text = spend.spend_text(restaurant, args)
            except Exception:
                text = ""
        failed = not args.tokens or any(line in (spend.UNREADABLE, spend.SCHEMA)
                                        for line in captured.getvalue().splitlines())
    return ["## Spend", "", *_spend_lines(text, failed), ""]


def _spend_lines(text, token_failed):
    """The body of a `## Spend` section from one spend text.

    Spend text always ends in one footer line, which is dropped. On a token failure the indented model and
    `not recorded` lines go and one `tokens unavailable` line follows the goal rows. An empty body prints `none`.
    """
    body = text.splitlines()[:-1]
    if token_failed:
        body = [line for line in body if not line.startswith("  ")] + ["tokens unavailable"]
    return body or ["none"]


