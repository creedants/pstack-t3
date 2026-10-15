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
from brigade_lib.layout import _layout, _one
from brigade_lib.leases import STANDINGS, autofire_of, claim_paths, lease_block, paths_inside, paths_overlap, refill_of, running_workers, worker_cap, workers_line
from brigade_lib.shared import age, clean, slug
from brigade_lib.store import LOW_FLAG_DAYS, LOW_RESERVE, PRIORITIES_BY_RANK
from brigade_lib.tickets import EVERY_ROW, Label, label_words, paths_of, priority_of, read_labels


def _goal_paused(goal, refill):
    if not goal:
        return False
    found = refill.goals.get(goal)
    return found is not None and found.state == "paused"


NO_WORKER = "waiting for an idle worker"
STARTS_AFTER = "starts after {tickets}"


@dataclass(frozen=True)
class Waiting:
    id: str
    priority: str
    filed: datetime
    paths: tuple
    decision: bool
    summary: str
    label: Label = Label()


@dataclass(frozen=True)
class Standing:
    ticket: Waiting
    kind: str
    why: str
    overdue: bool


def waiting_ticket(row, label=Label()):
    filed = datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
    if filed.tzinfo is None:
        filed = filed.replace(tzinfo=timezone.utc)
    return Waiting(row["id"], priority_of(row), filed, paths_of(row), row["decision"] == "yes", row["summary"], label)


def plan(tickets, refill, running, cap, level, refusals, now, expanded=None):
    allowed = () if level == "off" else PRIORITIES_BY_RANK[:PRIORITIES_BY_RANK.index(level) + 1]
    idle = max(0, cap - running)
    priorities = {ticket.id: ticket.priority for ticket in tickets}
    higher_waits = any(ticket.priority != "low" for ticket in tickets)
    started = {}
    standings = []
    for ticket in sorted(tickets, key=lambda ticket: (PRIORITIES_BY_RANK.index(ticket.priority), ticket.filed,
                                                     int(ticket.id[1:]) if ticket.id[1:].isdigit() else 0)):
        low = ticket.priority == "low"
        eff = expanded.get(ticket.id, ticket.paths) if expanded else ticket.paths
        shared = [ident for ident, paths in started.items() if paths_overlap(eff, paths)]
        kind, why = "start", ""
        if ticket.decision:
            kind = "decision"
        elif not ticket.paths:
            kind = "unknown"
        elif ticket.id in refusals:
            kind, why = "blocked", refusals[ticket.id]
        elif ticket.label.goal and _goal_paused(ticket.label.goal, refill):
            kind, why = "paused", "its goal is paused"
        elif (low and len(shared) == 1 and priorities[shared[0]] != "low"
              and paths_inside(eff, started[shared[0]])):
            kind, why = "rides", shared[0]
        elif shared:
            kind, why = "blocked", STARTS_AFTER.format(tickets=", ".join(shared))
        elif ticket.priority not in allowed:
            kind = "below"
        elif (hold := refill.hold(ticket.label)):
            kind, why = hold.standing, hold.words
        elif not idle:
            kind, why = "blocked", NO_WORKER
        elif low and higher_waits and idle <= LOW_RESERVE:
            kind = "reserve"
        else:
            idle -= 1
            started[ticket.id] = eff
            refill = refill.started(ticket.label)
        standings.append(Standing(ticket, kind, why, low and now - ticket.filed > timedelta(days=LOW_FLAG_DAYS)))
    return standings


def lease_refusals(meta, tickets, next_dish):
    root, prefix = meta["projectRoot"], slug(meta["restaurant"])
    asked, refusals = {}, {}
    for ticket in tickets:
        if ticket.decision or not ticket.paths:
            continue
        if ticket.paths not in asked:
            asked[ticket.paths] = lease_block(root, f"{prefix}/{ticket.id}",
                                              claim_paths(root, ",".join(ticket.paths), f"{prefix}/{next_dish.lower()}").text)
        if asked[ticket.paths]:
            refusals[ticket.id] = clean(asked[ticket.paths][1])
    return refusals


def expanded_paths(tickets, lay):
    found = {}
    for ticket in tickets:
        if not ticket.paths or lay is None:
            found[ticket.id] = ticket.paths
            continue
        seen = set(ticket.paths)
        extras = []
        for raw in ticket.paths:
            try:
                got = _one(raw, lay)
            except KeyboardInterrupt:
                raise
            except Exception:
                continue
            for one in got:
                if one not in seen:
                    seen.add(one)
                    extras.append(one)
        found[ticket.id] = tuple([*ticket.paths, *extras])
    return found


def startable_lines(standings, meta, running, now):
    lines = [workers_line(meta, running)]
    if not standings:
        return "\n".join([*lines, "no waiting tickets"])
    kinds = [standing.kind for standing in standings]
    lines.append(", ".join([f"waiting tickets: {len(standings)}",
                            *(f"{text.count_label}: {kinds.count(kind)}" for kind, text in STANDINGS.items() if kind in kinds)]))
    for standing in standings:
        ticket = standing.ticket
        flag = " (over a week)" if standing.overdue else ""
        paths = f"; {','.join(ticket.paths)}" if ticket.paths else ""
        words = STANDINGS[standing.kind].line_words.format(why=standing.why)
        lines.append(f"{ticket.id} {ticket.priority}{label_words(ticket.label)}, {age(now - ticket.filed)}{flag}: {words}{paths}")
        if standing.kind in ("start", "rides"):
            lines.append(f"  {ticket.summary}")
    return "\n".join(lines)


def startable(restaurant):
    with restaurant.checked():
        meta, labels = restaurant.meta, read_labels(restaurant, EVERY_ROW)
        tickets = [waiting_ticket(row, labels.tickets[row["id"]]) for row in restaurant.rows("rail.tsv") if row["state"] == "waiting"]
        running, next_dish = running_workers(restaurant), restaurant.next_id("dishes.tsv")
        refill = refill_of(meta, restaurant.rows("dishes.tsv"), labels)
    try:
        lay = _layout(meta["projectRoot"])
    except KeyboardInterrupt:
        raise
    except Exception:
        lay = None
    expanded = expanded_paths(tickets, lay)
    refusals = lease_refusals(meta, tickets, next_dish)
    moment = datetime.now(timezone.utc)
    return startable_lines(plan(tickets, refill, running, worker_cap(meta), autofire_of(meta), refusals, moment, expanded), meta, running, moment)
