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
from brigade_lib import generated
from brigade_lib.leases import STANDINGS, _claim_norm, autofire_of, blocked_waiting, claim_paths, lease_block, paths_inside, paths_overlap, refill_of, running_workers, worker_cap, workers_line
from brigade_lib.shared import BrigadeError, age, clean, slug
from brigade_lib.store import LOW_FLAG_DAYS, PRIORITIES_BY_RANK, low_reserve_of
from brigade_lib.tickets import EVERY_ROW, Label, after_holds, label_words, paths_of, priority_of, read_labels, read_waits


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
    doc_item: bool = False
    after: str = ""


@dataclass(frozen=True)
class Standing:
    ticket: Waiting
    kind: str
    why: str
    overdue: bool


def waiting_ticket(row, label=Label(), *, doc_item=False, paths=None, after=""):
    filed = datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
    if filed.tzinfo is None:
        filed = filed.replace(tzinfo=timezone.utc)
    effective = paths if paths is not None else paths_of(row)
    return Waiting(row["id"], priority_of(row), filed, effective, row["decision"] == "yes", row["summary"], label,
                   bool(doc_item), after)


def plan(tickets, refill, running, cap, level, refusals, now, low_reserve, expanded=None):
    allowed = () if level == "off" else PRIORITIES_BY_RANK[:PRIORITIES_BY_RANK.index(level) + 1]
    idle = max(0, cap - running)
    priorities = {ticket.id: ticket.priority for ticket in tickets}
    higher_started = False
    started = {}
    standings = []
    for ticket in sorted(tickets, key=lambda ticket: (PRIORITIES_BY_RANK.index(ticket.priority), ticket.filed,
                                                     int(ticket.id[1:]) if ticket.id[1:].isdigit() else 0)):
        low = ticket.priority == "low"
        eff = expanded.get(ticket.id, ticket.paths) if expanded else ticket.paths
        shared = [ident for ident, paths in started.items() if scopes_overlap(eff, paths)]
        kind, why = "start", ""
        if ticket.after:
            kind, why = "blocked", ticket.after
        elif ticket.decision:
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
        elif low and higher_started and idle <= low_reserve:
            kind = "reserve"
        else:
            idle -= 1
            if not low:
                higher_started = True
            started[ticket.id] = eff
            refill = refill.started(ticket.label)
        standings.append(Standing(ticket, kind, why, low and now - ticket.filed > timedelta(days=LOW_FLAG_DAYS)))
    return standings


def lease_refusals(meta, tickets):
    """The block on a waiting ticket's paths, or nothing when the landing queue would admit them.

    The claim carries the ticket's saved paths and, when the contract lists generated paths and a map, the generated
    copies the map gives for them. That is the claim `fire` builds for the same paths, minus the change note. The note is
    named for the branch a `fire` runs on, which the preview does not know, so the preview passes no branch and
    claims no fragment. A guessed note would block a ticket whose paths are free.
    """
    root, prefix = meta["projectRoot"], slug(meta["restaurant"])
    asked, refusals = {}, {}
    for ticket in tickets:
        if ticket.decision or not ticket.paths or ticket.after:
            continue
        key = (ticket.paths, ticket.doc_item)
        if key not in asked:
            asked[key] = lease_block(root, f"{prefix}/{ticket.id}",
                                     claim_paths(meta, ",".join(ticket.paths), None, ticket.doc_item).text)
        if asked[key]:
            refusals[ticket.id] = clean(asked[key][1])
    return refusals


def scopes_overlap(first, second):
    """`paths_overlap` for two effective scopes. An empty string is the whole repository, so it overlaps everything."""
    return "" in first or "" in second or paths_overlap(first, second)


def expanded_paths(tickets, gen_map):
    found = {}
    for ticket in tickets:
        if not ticket.paths:
            found[ticket.id] = ticket.paths
            continue
        norms = []
        seen = set()
        for raw in ticket.paths:
            made = _claim_norm(raw)
            # An empty string is the whole repository, so keep it; only an escaping path reads as None.
            if made is None or made in seen:
                continue
            seen.add(made)
            norms.append(made)
        if gen_map is None or not gen_map.rules:
            found[ticket.id] = tuple(norms)
            continue
        extras = []
        for made in norms:
            if not made:
                continue
            try:
                got = generated.expand(gen_map, made, ticket.doc_item)
            except KeyboardInterrupt:
                raise
            except Exception:
                continue
            for one in got:
                if one not in seen:
                    seen.add(one)
                    extras.append(one)
        found[ticket.id] = tuple([*norms, *extras])
    return found


def startable_lines(standings, meta, running, now, low_reserve):
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
        if ticket.doc_item and ticket.paths:
            paths += " --doc-item"
        words = STANDINGS[standing.kind].line_words.format(why=standing.why, reserve=low_reserve)
        lines.append(f"{ticket.id} {ticket.priority}{label_words(ticket.label)}, {age(now - ticket.filed)}{flag}: {words}{paths}")
        if standing.kind in ("start", "rides"):
            lines.append(f"  {ticket.summary}")
    return "\n".join(lines)


def startable(restaurant):
    waits = read_waits(restaurant)
    with restaurant.checked():
        meta, labels = restaurant.meta, read_labels(restaurant, EVERY_ROW)
        held = after_holds(waits, restaurant.rows("rail.tsv"))
        low_reserve = low_reserve_of(meta)
        notes = dict(blocked_waiting(restaurant))
        tickets = []
        for row in restaurant.rows("rail.tsv"):
            if row["state"] != "waiting":
                continue
            note = notes.get(row["id"])
            if note is None:
                tickets.append(waiting_ticket(row, labels.tickets[row["id"]], after=held.get(row["id"], "")))
                continue
            raw_parts = tuple(part for part in (note.get("paths") or "").split(",") if part)
            valid = [made for part in raw_parts if (made := _claim_norm(part))]
            if not valid:
                tickets.append(waiting_ticket(row, labels.tickets[row["id"]], after=held.get(row["id"], "")))
                continue
            tickets.append(waiting_ticket(row, labels.tickets[row["id"]], doc_item=bool(note.get("doc_item")),
                                          paths=raw_parts, after=held.get(row["id"], "")))
        running = running_workers(restaurant)
        refill = refill_of(meta, restaurant.rows("dishes.tsv"), labels)
    try:
        gen_map = generated.read(meta["projectRoot"])
    except KeyboardInterrupt:
        raise
    except BrigadeError:
        raise
    except Exception:
        gen_map = generated.EMPTY
    expanded = expanded_paths(tickets, gen_map)
    refusals = lease_refusals(meta, tickets)
    moment = datetime.now(timezone.utc)
    return startable_lines(plan(tickets, refill, running, worker_cap(meta), autofire_of(meta), refusals, moment, low_reserve,
                                expanded), meta, running, moment, low_reserve)
