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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from brigade_lib import seam
from brigade_lib.items import attempt_starts
from brigade_lib.reviews import item_verdicts, pass_check, round_count, round_debt, unrecorded_reviews
from brigade_lib.shared import BrigadeError, MalformedTable, clean, slug
from brigade_lib.store import MESSAGE_ONLY, ROUND_KIND, TABLES, reporting_of, round_budget_of, store_path
from brigade_lib.tickets import EVERY_ROW, label_words, priority_of, read_labels, request_files
from brigade_lib.startable import NO_WORKER, STARTS_AFTER, Standing, Waiting, lease_refusals, plan, startable, startable_lines, waiting_ticket
from brigade_lib.watch import _latest_trunk, closing_options, dish_progress, open_item_decisions, option_key, record_model_clear, record_model_skip, record_worker_end, report_is_current, require_seat, watch, worker_where

@dataclass(frozen=True)
class Run:
    argv: tuple


@dataclass(frozen=True)
class See:
    words: str


@dataclass(frozen=True)
class If:
    condition: str
    action: str


@dataclass(frozen=True)
class Reply:
    yes: bool
    occasion: str
    detail: str
    close: tuple


@dataclass(frozen=True)
class Trigger:
    wake: str
    dish: str = ""
    request: str = ""
    verdict: str = ""
    result: str = ""
    question: bool = False


@dataclass(frozen=True)
class Debt:
    dish: str
    count: int
    at: str


@dataclass(frozen=True)
class Snapshot:
    """One read of the store. It holds no plan. warn prints on standard error and writes nothing."""

    reporting: str
    reports_to: str
    last_report_at: str
    owner_block: str
    restaurant: str
    intake: tuple
    dishes: tuple
    tickets: tuple
    decisions: tuple
    debts: tuple
    round_counts: dict
    events: tuple
    counts: dict
    progress: tuple
    unrecorded: tuple
    inbox: tuple
    requests: tuple
    passes: dict
    blocked_since: tuple
    moment: datetime
    close_refusal: str
    warn: object
    place: str
    report_gaps: tuple
    trunk: object
    round_budget: int


def dish_by_id(snapshot, ident):
    for dish in snapshot.dishes:
        if dish["id"] == ident:
            return dish
    return None


def open_for(snapshot, ident):
    for row in snapshot.decisions:
        if row["dish"] == ident:
            return row
    return None


def lowest_id(idents, warn):
    """The lowest id. A suffix that is not a base-10 integer warns and sorts after every numeric suffix."""
    ranked = []
    for ident in idents:
        try:
            ranked.append((0, int(ident[1:], 10), ident))
        except ValueError:
            warn(f"{ident} has no base-10 id suffix")
            ranked.append((1, 0, ident))
    return min(ranked)[2]


def crossing_send_back(restaurant, dish_id):
    """The at of the send-back that reached the round budget, using the same rows round_count counts."""
    budget = round_budget_of(restaurant.meta)
    verdicts = item_verdicts(restaurant.rows("pass.tsv"), dish_id)
    settled = [row["at"] for row in verdicts if row["verdict"] == "pass"]
    settled += [row["answered"] for row in restaurant.rows("86.tsv")
                if row["dish"] == dish_id and row["kind"] == ROUND_KIND and row["state"] == "answered"]
    since = max(settled, default="")
    backs = [row["at"] for row in verdicts if row["verdict"] == "send-back" and row["at"] > since]
    if len(backs) < budget:
        return ""
    return backs[budget - 1]


def load_inbox(restaurant):
    """Each json handoff take_tickets would read, as (name, ok, problem). A bad file is not a traceback."""
    found = []
    inbox = restaurant.dir / "inbox"
    for path in sorted(inbox.glob("*.json")):
        try:
            raw = path.read_text()
        except (OSError, UnicodeError) as error:
            found.append((path.name, False, f"could not be read ({error})"))
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            found.append((path.name, False, f"is not valid JSON ({error.msg})"))
            continue
        if not isinstance(data, dict):
            found.append((path.name, False, "is not a JSON object"))
            continue
        missing = [key for key in ("handoff", "source", "ref", "summary") if key not in data]
        if missing:
            found.append((path.name, False, f"has no {missing[0]} key"))
            continue
        found.append((path.name, True, ""))
    return tuple(found)


def counted_timebox(restaurant, dish):
    """The row next passes to dish_progress. An empty timebox stays empty. A timebox that is not a base-10 integer warns and reads as 60."""
    cell = dish.get("timebox") or ""
    if cell == "":
        return dish
    try:
        int(cell, 10)
    except ValueError:
        restaurant.warn(f"{dish['id']} timebox {cell} is not a base-10 integer")
        return {**dish, "timebox": "60"}
    return dish


def malformed_close(restaurant):
    """The first malformed row rows names, in TABLES order, or empty when every table parses."""
    for table in TABLES:
        try:
            restaurant.rows(table)
        except MalformedTable as error:
            return str(error)
    return ""


def load_snapshot(restaurant, moment):
    """Read the store under the caller's lock. The caller already refused a bad owner."""
    read_labels(restaurant, EVERY_ROW)
    meta = restaurant.meta
    since = meta.get("lastReportAt") or ""
    dishes = tuple(restaurant.rows("dishes.tsv"))
    tickets = tuple(restaurant.rows("rail.tsv"))
    decisions = tuple(row for row in restaurant.rows("86.tsv") if row["state"] == "open")
    open_dishes = {row["dish"] for row in decisions if row["dish"]}
    progress = []
    gaps = []
    for dish in dishes:
        if dish["state"] != "in-progress" or dish.get("reported") != "yes":
            continue
        if dish["id"] in open_dishes:
            continue
        dish = counted_timebox(restaurant, dish)
        if not report_is_current(restaurant, dish):
            gaps.append((dish["id"], worker_where(dish)))
            continue
        for sentence in dish_progress(restaurant, dish, moment):
            if "no report-back" in sentence:
                continue
            if sentence.startswith(f"{dish['id']}: reported ") or sentence.startswith(f"{dish['id']}: report written "):
                progress.append((dish["id"], sentence))
    debts = []
    round_counts = {}
    for dish in dishes:
        round_counts[dish["id"]] = round_count(restaurant, dish["id"])
        count = round_debt(restaurant, dish)
        if count:
            debts.append(Debt(dish["id"], count, crossing_send_back(restaurant, dish["id"])))
    passes = {}
    for dish in dishes:
        sha = dish.get("sha") or ""
        if dish["state"] == "queued" or sha:
            ok, why = pass_check(restaurant, dish["id"], sha)
            passes[dish["id"]] = (ok, why)
    blocked_since = {}
    for event in restaurant.rows("log.tsv"):
        if event["kind"] == "ticket" and event["state"] == "blocked" and event["id"] not in blocked_since:
            blocked_since[event["id"]] = event["at"]
    pending = tuple((path.stem, path.read_text().strip()) for path in request_files(restaurant)[0])
    block = restaurant.owner_block() or ""
    trunk = _latest_trunk(restaurant.rows("log.tsv"))
    return Snapshot(
        reporting_of(meta), meta.get("reportsTo") or "", since, block, slug(meta["restaurant"]),
        tuple(meta.get("intake") or ()), dishes, tickets, decisions, tuple(debts), round_counts,
        tuple(seam.events_since(restaurant, since).values()), seam.counts(restaurant), tuple(progress),
        tuple(unrecorded_reviews(restaurant)), load_inbox(restaurant), pending, passes,
        tuple(blocked_since.items()), moment, malformed_close(restaurant), restaurant.warn,
        store_path(restaurant.dir), tuple(gaps), trunk, round_budget_of(meta))


def inbox_ready(snapshot):
    """True when inbox take would read every json handoff and there is something to take."""
    if any(not ok for _, ok, _ in snapshot.inbox):
        return False
    return bool(snapshot.inbox) or bool(snapshot.requests)


def accepted_argv(snapshot, argv):
    """The argv when that command would exit 0 on this store. None means this sheet prints no run of it."""
    if snapshot.owner_block:
        return None
    if argv == ("inbox", "take"):
        return argv if inbox_ready(snapshot) else None
    if argv[:2] == ("ticket", "paths"):
        return None
    if len(argv) == 3 and argv[0] == "dish" and argv[2] == "--reported":
        dish = dish_by_id(snapshot, argv[1])
        if dish and dish["state"] == "in-progress" and dish.get("reported") != "yes":
            return argv
        return None
    if len(argv) == 5 and argv[0] == "86" and argv[1] == "add" and argv[2] == "--dish" and argv[4] == "--round-budget":
        debt = next((row for row in snapshot.debts if row.dish == argv[3]), None)
        if debt and not open_for(snapshot, debt.dish) and debt.at > snapshot.last_report_at:
            return argv
        return None
    if argv in (("close",), ("close", "--to-file")):
        return None if snapshot.close_refusal else argv
    if len(argv) == 4 and argv[0] == "dish" and argv[2:] == ("--state", "merged"):
        dish = dish_by_id(snapshot, argv[1])
        passed = snapshot.passes.get(argv[1])
        if dish and dish["state"] == "queued" and passed and passed[0]:
            return argv
        return None
    return None


def maybe_run(snapshot, argv):
    accepted = accepted_argv(snapshot, argv)
    if accepted is None:
        return None
    return Run(tuple(accepted))


def _trunk_red(snapshot):
    """The stored red trunk dict or None."""
    trunk = getattr(snapshot, "trunk", None)
    if isinstance(trunk, dict) and trunk.get("state") == "red":
        return trunk
    return None


def _trunk_cues(snapshot):
    """The See cues owed while the stored trunk stays red."""
    red = _trunk_red(snapshot)
    if red is None:
        return []
    ident = red.get("id") or "trunk"
    since = red.get("since") or ""
    workflow = red.get("workflow") or ""
    url = red.get("url") or ""
    return [
        See(f"Trunk {ident} CI failing since {since}: {workflow} {url}. Read the failing jobs."),
        See(f"File or fire an urgent fix for the red trunk {ident}."),
        See(f"Tell the owner the trunk {ident} is red."),
    ]


