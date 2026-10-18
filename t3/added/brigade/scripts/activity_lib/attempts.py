import argparse
import enum
import fcntl
import http.server
import json
import os
import re
import secrets
import socketserver
import sqlite3
import stat
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import quote, unquote, urlsplit
from activity_lib.store import FINISHED_STATES, Goal, KINDS, STATE_WORDS, UNKNOWN_STATE, UNNAMED
from activity_lib.t3state import LIVE, Status
from activity_lib.page import AttemptFold, Evidence, Group, Hidden, ID_BYTES, Item, LABEL_BYTES, LedgerLine, Page, ROLES, RoleCount, Row, SUMMARY_BYTES, Totals, assign, clip, count, flight_of, is_open, label_of, open_turn, seconds_of, shown, span, spans_of, status_of, stretches, summary, tree, with_parents


def attempt_rank(cell):
    """(0, int(cell)) when cell is ASCII, its length is from 1 to 18, and every character is a digit. (1, cell) otherwise."""
    if cell.isascii() and 1 <= len(cell) <= 18 and cell.isdigit():
        return (0, int(cell))
    return (1, cell)


def lines_of(rows, dish, privacy):
    chosen = [(index, row) for index, row in enumerate(rows) if row["dish"] == dish]
    return tuple(
        LedgerLine(privacy.field(row["attempt"]), privacy.field(row["role"]), privacy.field(row["seat"]), is_open(row))
        for _, row in sorted(chosen, key=lambda pair: (attempt_rank(pair[1]["attempt"]), pair[0])))


def attempt_tail(counts):
    """The roles and the state of one fold summary. Counts holds RoleCounts in display order."""
    roles = ", ".join(count(entry.n, entry.role) for entry in counts)
    opened = sum(entry.opened for entry in counts)
    total = sum(entry.n for entry in counts)
    ended = total - opened
    if opened == 0:
        state = "all ended"
    elif ended == 0:
        state = "all open"
    else:
        state = f"{opened} open plus {ended} ended"
    return f"{roles}, {state}" if roles else state


def format_attempt(attempts, ranks, counts):
    """One summary line for folded earlier attempts."""
    tail = attempt_tail(counts)
    if not attempts:
        return tail
    if len(attempts) == 1:
        head = f"attempt {attempts[0]}"
    elif ranks and all(kind == 0 for kind, _ in ranks):
        numbers = sorted(number for _, number in ranks)
        if numbers == list(range(numbers[0], numbers[0] + len(numbers))):
            head = f"attempts {numbers[0]}-{numbers[-1]}"
        else:
            head = f"attempts {', '.join(attempts)}"
    else:
        head = f"attempts {', '.join(attempts)}"
    return f"{head}: {tail}"


def brief_attempt(ranks, counts):
    """One summary line for folded earlier attempts."""
    tail = attempt_tail(counts)
    if not ranks:
        return tail
    head = "1 attempt" if len(ranks) == 1 else f"{len(ranks)} attempts"
    return f"{head}: {tail}"


def partition_attempts(dish_rows, rows, is_finished, privacy, threads=(), parents=()):
    """Split one work item into current lines and a fold of earlier attempts."""
    if is_finished:
        current = tuple(
            LedgerLine(privacy.field(row["attempt"]), privacy.field(row["role"]), privacy.field(row["seat"]), is_open(row))
            for _, row in sorted(enumerate(dish_rows), key=lambda pair: (attempt_rank(pair[1]["attempt"]), pair[0])))
        marked = tuple(replace(row, earlier=False) for row in rows)
        return current, AttemptFold(), marked
    matched = tuple(threads) if len(threads) == len(rows) else ()
    parent_threads = tuple(parents) if len(parents) == len(rows) else ()
    family, inside = set(), False
    for at, row in enumerate(rows):
        if row.depth == 0:
            inside = row.label == "earlier worker"
        if inside:
            family.add(at)
    shown = set()
    if matched and parent_threads:
        index_of = {}
        for at, thread in enumerate(matched):
            index_of.setdefault(thread, at)
        for at, row in enumerate(rows):
            if row.status in LIVE:
                shown.add(at)
                seen, cur = {at}, at
                while True:
                    parent = parent_threads[cur]
                    if not parent:
                        break
                    parent_at = index_of.get(parent)
                    if parent_at is None or parent_at in seen:
                        break
                    shown.add(parent_at)
                    if rows[parent_at].depth == 0:
                        break
                    seen.add(parent_at)
                    cur = parent_at
    else:
        for at, row in enumerate(rows):
            if row.status in LIVE:
                depth = row.depth
                shown.add(at)
                for before in range(at - 1, -1, -1):
                    if rows[before].depth < depth:
                        depth = rows[before].depth
                        shown.add(before)
                        if not depth:
                            break
    earlier_at = family - shown
    kept = {matched[at] for at in family & shown} if matched else set()
    ordinal_of = {}
    for row in dish_rows:
        cell = row["attempt"]
        if not (cell.isascii() and 1 <= len(cell) <= 18 and cell.isdigit()) and cell not in ordinal_of:
            ordinal_of[cell] = len(ordinal_of)

    def rank_of(cell):
        if cell.isascii() and 1 <= len(cell) <= 18 and cell.isdigit():
            return (0, int(cell))
        return (1, ordinal_of[cell])

    ranked = [(index, row, rank_of(row["attempt"])) for index, row in enumerate(dish_rows)]
    distinct = sorted({rank for _, _, rank in ranked})
    top = max(distinct, default=None)
    current_raw = sorted((triple for triple in ranked if triple[2] == top), key=lambda triple: (triple[2], triple[0]))
    earlier_raw = sorted((triple for triple in ranked if triple[2] != top), key=lambda triple: (triple[2], triple[0]))
    stayed = {index for index, row, _ in earlier_raw if row["thread"] and row["thread"] in kept}
    current = tuple(
        LedgerLine(privacy.field(row["attempt"]), privacy.field(row["role"]), privacy.field(row["seat"]), is_open(row))
        for _, row, _ in sorted((*current_raw, *(triple for triple in earlier_raw if triple[0] in stayed)), key=lambda triple: (triple[2], triple[0])))
    folded = [triple for triple in earlier_raw if triple[0] not in stayed]
    earlier_lines = tuple(
        LedgerLine(privacy.field(row["attempt"]), privacy.field(row["role"]), privacy.field(row["seat"]), is_open(row)) for _, row, _ in folded)
    if not folded and not earlier_at:
        marked = tuple(replace(row, earlier=False) for row in rows)
        return current, AttemptFold(), marked
    lane_threads = {matched[at] for at in earlier_at} if matched else set()
    ledger_threads = {row["thread"] for _, row, _ in folded if row["thread"]}
    grouped, pending = {}, {}
    for (_, row, _), line in zip(folded, earlier_lines):
        thread = row["thread"]
        if thread and thread in lane_threads:
            if thread in pending:
                pending[thread][1] = pending[thread][1] or line.open
            else:
                pending[thread] = [line.role, line.open]
            continue
        grouped.setdefault(line.role, [0, 0])
        grouped[line.role][0] += 1
        grouped[line.role][1] += line.open
    for role, opened in pending.values():
        grouped.setdefault(role, [0, 0])
        grouped[role][0] += 1
        grouped[role][1] += opened
    for at in sorted(earlier_at):
        thread = matched[at] if matched else ""
        if thread and thread in ledger_threads:
            continue
        grouped.setdefault(rows[at].label, [0, 0])
        grouped[rows[at].label][0] += 1
        grouped[rows[at].label][1] += rows[at].status in LIVE
    seen = {}
    for _, row, rank in folded:
        if rank not in seen:
            seen[rank] = clip(privacy.field(row["attempt"]), ID_BYTES)
    ordered = sorted(seen)
    attempts = tuple(seen[rank] for rank in ordered)
    ranks = tuple(ordered)
    counts = []
    for role in ROLES:
        if role in grouped:
            n, opened = grouped.pop(role)
            counts.append(RoleCount(role, n, opened))
    for label in sorted(grouped):
        n, opened = grouped[label]
        counts.append(RoleCount(label, n, opened))
    summary = format_attempt(attempts, ranks, tuple(counts))
    fold = AttemptFold(summary, earlier_lines, attempts, ranks, tuple(counts))
    marked = tuple(replace(row, earlier=(at in earlier_at)) for at, row in enumerate(rows))
    return current, fold, marked


def spoken(line):
    parts = (
        f"attempt {clip(line.attempt, ID_BYTES)}",
        clip(line.role, LABEL_BYTES),
        clip(line.seat, SUMMARY_BYTES),
        "open" if line.open else "ended",
    )
    return " · ".join(part for part in parts if part)


def item_of(unit, privacy, key, goal_ids=(), lines=(), fold=AttemptFold()):
    word, tone = STATE_WORDS.get(unit.state, UNKNOWN_STATE)
    link, why = privacy.link(unit.pr)
    kind = unit.kind if unit.kind in KINDS else ""
    goal = unit.goal if unit.goal in goal_ids else ""
    return Item(privacy.field(unit.id) or UNNAMED, privacy.field(unit.summary), word, tone, link, unit.state not in FINISHED_STATES, why, key, kind, goal, tuple(lines), fold)


def build_page(store, t3, window, privacy):
    """Each string of the page that comes from the store or from T3 is a value privacy.field, privacy.model, or privacy.link returned, or a label short() cut from a field() value.
    The other strings are the constants of this file and numbers. No page type holds a thread id, a sub-agent id, or a path.
    """
    goals = tuple(Goal(record.id, clip(privacy.field(record.name), SUMMARY_BYTES)) for record in store.goals)
    goal_ids = {goal.id for goal in goals}

    def row(agent, depth, label):
        turn = open_turn(agent)
        return Row(depth, label, privacy.model(agent.model), status_of(agent),
                   seconds_of(agent, window), spans_of(agent, window), int(window.end - turn.start) if turn else None, 1 if stretches(agent) else 0)

    def order(entry):
        unit, agents = entry
        ends = [window.end if turn.end is None else turn.end for agent in agents for turn in stretches(agent)]
        return (unit is None, all(open_turn(agent) is None for agent in agents), -max(ends, default=window.start), unit)

    placed = assign(store, t3)
    units = {unit.id: unit for unit in store.units}
    keys = {unit.id: key for key, unit in enumerate(store.units)}
    members = {}
    for thread, assignment in placed.items():
        members.setdefault(assignment.unit, []).append(t3.agents[thread])
    members = {unit: with_parents(agents) for unit, agents in members.items()}
    groups, by_request, folded_by_unit = [], 0, {}
    for unit, agents in sorted(((unit, agents) for unit, agents in members.items() if agents), key=order):
        ordered = tree(agents)
        rows = tuple(row(agent, min(depth, 2), label_of(agent, store, unit, privacy)) for agent, depth in ordered)
        active = [agent for agent in agents if stretches(agent)]
        subagents = sum(agent.parent is not None for agent in active)
        by_request += sum(placed[agent.thread].evidence is Evidence.REQUEST for agent in active)
        if unit:
            raw = [entry for entry in store.threads if entry["dish"] == unit]
            in_flight = units[unit].state not in FINISHED_STATES
            live = any(entry.status in LIVE for entry in rows)
            current, fold, marked = partition_attempts(raw, rows, (not in_flight and not live), privacy, tuple(agent.thread for agent, _ in ordered), tuple(agent.parent for agent, _ in ordered))
            folded_by_unit[unit] = (current, fold)
            item = item_of(units[unit], privacy, keys[unit], goal_ids, current, fold)
            groups.append(Group(item, marked, len(active) - subagents, subagents))
        else:
            groups.append(Group(None, tuple(replace(entry, earlier=False) for entry in rows), len(active) - subagents, subagents))
    everyone = [row for group in groups for row in group.rows if row.stands_for]
    subagents = sum(group.subagents for group in groups)
    totals = Totals(sum(row.status is Status.RUNNING for row in everyone), len(everyone) - subagents, subagents, sum(row.status is Status.FAILED for row in everyone))
    models = [row.model for row in everyone]
    legend = tuple(sorted(((name, models.count(name)) for name in set(models)), key=lambda entry: (-entry[1], entry[0])))
    own = None
    coordinators = [t3.agents[thread] for thread in store.coordinators if thread in t3.agents]
    if coordinators:
        turns = sorted((turn for agent in coordinators for turn in agent.turns), key=lambda turn: turn.start)
        own = row(replace(coordinators[0], turns=tuple(turns), delegation=None), 0, "coordinator")
        bars = tuple(replace(span, status=Status.DONE) if span.status is Status.RUNNING else span for span in own.spans)
        own = replace(own, spans=bars, open_seconds=None)

    def number(unit):
        digits = re.search(r"[0-9]{1,18}", unit.id)
        return (int(digits.group()) if digits else 0, unit.id)

    def folded_item(unit):
        if unit.id in folded_by_unit:
            current, fold = folded_by_unit[unit.id]
            return item_of(unit, privacy, keys[unit.id], goal_ids, current, fold)
        raw = [entry for entry in store.threads if entry["dish"] == unit.id]
        current, fold, _ = partition_attempts(raw, (), False, privacy)
        return item_of(unit, privacy, keys[unit.id], goal_ids, current, fold)

    items = tuple(folded_item(unit)
                  for unit in sorted(units.values(), key=number) if unit.state not in FINISHED_STATES)
    hidden = Hidden(other_threads=t3.other_threads, unknown_status=t3.unknown_status, by_request_name=by_request, unstarted=t3.unstarted)
    return Page(privacy.field(store.name) or "this coordinator", window, totals, legend, own, tuple(groups), items, hidden, goals=goals, flight=flight_of(items, goals))
