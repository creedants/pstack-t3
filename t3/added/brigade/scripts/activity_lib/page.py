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
from activity_lib.store import FINISHED_STATES, Goal, STATE_WORDS, UNASSIGNED, UNKNOWN_STATE
from activity_lib.t3state import OPEN, Status, Turn, Window
from activity_lib.privacy import BAR, CUT, HASH, MIXED_SLOT, MODEL_SLOTS, OTHER_MODEL, OTHER_SLOT, PATH_TAIL, ROLE, SLUG, UNIT_SUFFIX, entities, plain, short


TITLE_CHARS = 60


LABEL_CHARS = 40


class Evidence(enum.Enum):
    RECORD = "record"
    REQUEST = "request"
    LINEAGE = "lineage"
    NONE = "none"


@dataclass(frozen=True)
class Assignment:
    unit: Optional[str]
    evidence: Evidence


@dataclass(frozen=True)
class Span:
    x: int
    w: int
    status: Status


ROLES = ("worker", "fix worker", "reviewer", "panel member", "code delegate", "claims check", "architect runner")


@dataclass(frozen=True)
class RoleCount:
    role: str
    n: int
    opened: int


@dataclass(frozen=True)
class AttemptFold:
    summary: str = ""
    lines: tuple = ()
    attempts: tuple = ()
    ranks: tuple = ()
    counts: tuple = ()


@dataclass(frozen=True)
class Row:
    depth: int
    label: str
    model: str
    status: Status
    seconds: int
    spans: tuple[Span, ...]
    open_seconds: Optional[int] = None
    stands_for: int = 1
    earlier: bool = False
    color_slot: Optional[int] = None

    def __post_init__(self):
        if self.color_slot is None:
            object.__setattr__(self, "color_slot", model_slot(self.model))


@dataclass(frozen=True)
class Item:
    id: str
    summary: str
    state: str
    tone: str
    pr: str
    in_flight: bool
    no_link: str = ""
    # The work item's position among the store's work items, so two work items that print alike stay two. No form prints it.
    key: int = 0
    kind: str = ""
    goal: str = ""
    lines: tuple = ()
    fold: AttemptFold = AttemptFold()


@dataclass(frozen=True)
class LedgerLine:
    attempt: str
    role: str
    seat: str
    open: bool


@dataclass(frozen=True)
class Group:
    item: Optional[Item]
    rows: tuple[Row, ...]
    agents: int
    subagents: int
    collapsed: bool = False


@dataclass(frozen=True)
class Totals:
    running: int
    agents: int
    subagents: int
    failed: int


@dataclass(frozen=True)
class Hidden:
    dropped_items: int = 0
    dropped_agents: int = 0
    cut_agents: int = 0
    cut_in_flight: int = 0
    cut_lines: int = 0
    other_threads: int = 0
    unknown_status: int = 0
    by_request_name: int = 0
    unstarted: int = 0


@dataclass(frozen=True)
class Page:
    name: str
    window: Window
    totals: Totals
    legend: tuple[tuple[str, int], ...]
    coordinator: Optional[Row]
    groups: tuple[Group, ...]
    items: tuple[Item, ...]
    hidden: Hidden
    fold: int = 0
    goals: tuple[Goal, ...] = ()
    flight: Optional["Flight"] = None


@dataclass(frozen=True)
class StripGroup:
    heading: str
    count: str
    items: tuple[Item, ...]


@dataclass(frozen=True)
class Flight:
    groups: tuple[StripGroup, ...]


def unit_named(part, units):
    suffixed = UNIT_SUFFIX.fullmatch(part)
    return units.get(part) or (units.get(suffixed.group(1)) if suffixed else None)


def assign(store, t3):
    """One coordinator child unit when its id part or branch tail run matches one unit, else none. RECORD wins."""
    coordinators = frozenset(store.coordinators)
    units = {unit.id.lower(): unit.id for unit in store.units}
    tails = {}
    for unit in store.units:
        tail = unit.branch.rsplit("/", 1)[-1].lower() if unit.branch else ""
        if tail:
            tails.setdefault(tail, []).append(unit.id)
    requested = {agent.request: thread for thread, agent in t3.agents.items() if agent.request and agent.parent in coordinators}
    recorded = {}
    for unit in store.units:
        for thread in (*unit.earlier_workers, unit.worker, t3.node_thread.get(unit.task), requested.get(unit.task)):
            if thread:
                recorded[thread] = unit.id
    known = {unit.id for unit in store.units}
    for row in store.threads:
        thread = row.get("thread", "")
        dish = row.get("dish", "")
        if thread and dish in known:
            recorded[thread] = dish
    found = {}
    for start in t3.agents:
        thread, below = start, []
        while thread not in found:
            agent = t3.agents[thread]
            if thread in recorded:
                found[thread] = Assignment(recorded[thread], Evidence.RECORD)
            elif agent.parent in coordinators:
                request = agent.request or ""
                matched = set()
                for part in request.replace("/", "-").split("-"):
                    hit = unit_named(part, units)
                    if hit:
                        matched.add(hit)
                padded = f"-{request.replace('/', '-').lower()}-"
                for tail, ids in tails.items():
                    if f"-{tail}-" in padded:
                        matched.update(ids)
                unit = next(iter(matched)) if len(matched) == 1 else None
                found[thread] = Assignment(unit, Evidence.REQUEST if unit else Evidence.NONE)
            elif agent.parent in t3.agents and agent.parent not in below:
                below.append(thread)
                thread = agent.parent
            else:
                found[thread] = Assignment(None, Evidence.NONE)
        unit = found[thread].unit
        for child in below:
            found[child] = Assignment(unit, Evidence.LINEAGE if unit else Evidence.NONE)
    return {thread: assignment for thread, assignment in found.items() if thread not in coordinators}


def words_of(name, store=None, units=()):
    """The parts of a request name between its hyphens, joined by spaces, with `verify` read as `review` and without each part HASH matches.
    With a store, also without a leading `brigade`, the store's leading slug parts, and each part that is a work item's id in lower case, alone or before a letter from a to z and optional digits.
    """
    parts = name.split("-")
    if store and parts[0] == "brigade":
        del parts[0]
    while store and parts and parts[0] in store.slug_parts:
        del parts[0]
    return " ".join("review" if part == "verify" else part for part in parts if not (store and unit_named(part, units)) and not HASH.fullmatch(part))


def label_of(agent, store, unit, privacy):
    """`worker` or `earlier worker` for a thread the store records as one. For any other thread, the first of these that is not empty, and `sub-agent` or `agent` when all are:
    the written title, the request name's words without the parts the store explains, the role an `Act as` title names, and all the request name's words.
    A row's own request name of two or more parts reaches its label only through words_of. A title that holds its own row's request name of two or more parts is used only when it is a slug, which words_of reads, and its role is not used.
    A written title that clean() would change is not used. The last name of a `/root/.../name` title and the role of an `Act as` title are read before that check, and each passes field().
    """
    if any(agent.thread == other.worker for other in store.units):
        return "worker"
    if any(agent.thread in other.earlier_workers for other in store.units):
        return "earlier worker"
    units = {other.id.lower(): other.id for other in store.units}
    line = " ".join(plain((agent.title.strip().splitlines() or [""])[0]).split())
    request = agent.request or ""
    names = "-" in request and request.lower() in line.lower()
    written = ""
    if len(line) <= TITLE_CHARS and not line.startswith(("Act as", "You are")) and not (names and not SLUG.fullmatch(line)):
        tail = PATH_TAIL.fullmatch(line)
        written = tail.group(1) if tail else line
        if privacy.field(written) != written:
            written = ""
        if unit:
            written = re.sub(rf"^{re.escape(unit)}(?::\s*|\s+)", "", written)
        if SLUG.fullmatch(written):
            written = words_of(written, store, units)
    role = ROLE.match(line)
    for text in (written, words_of(request, store, units), role.group(1) if role and not names else "", words_of(request)):
        text = privacy.field(text)
        if text:
            return short(text, LABEL_CHARS)
    return "sub-agent" if agent.parent else "agent"


def stretches(agent):
    delegation = agent.delegation
    if agent.turns or delegation is None:
        return agent.turns
    return (Turn(delegation.status, delegation.start, delegation.end),)


def open_turn(agent):
    for status in OPEN:
        for turn in reversed(stretches(agent)):
            if turn.end is None and turn.status is status:
                return turn
    return None


def status_of(agent):
    turn, delegation = open_turn(agent), agent.delegation
    if turn:
        return turn.status
    if delegation:
        return Status.WAITING if delegation.end is None else delegation.status
    return agent.turns[-1].status if agent.turns else Status.DONE


def spans_of(agent, window):
    length = window.end - window.start

    def point(moment):
        return round((min(max(moment, window.start), window.end) - window.start) / length * 1000)

    spans = []
    for turn in stretches(agent):
        x = min(point(turn.start), 999)
        right = max(point(window.end if turn.end is None else turn.end), x + 1)
        status, last = BAR[turn.status], spans[-1] if spans else None
        if last and last.status is status and turn.end is not None and x - (last.x + last.w) < 5:
            spans[-1] = Span(last.x, max(last.x + last.w, right) - last.x, status)
        else:
            spans.append(Span(x, right - x, status))
    return tuple(spans)


def seconds_of(agent, window):
    """The seconds of the window inside this agent's stretches. A queued stretch is not work, so it adds none."""
    worked = [turn for turn in stretches(agent) if turn.status is not Status.QUEUED]
    return int(sum(max(0, min(window.end, window.end if turn.end is None else turn.end) - max(window.start, turn.start)) for turn in worked))


def tree(agents):
    """True depths in tree preorder. build_page clips each depth to 2 for display."""
    def began(agent):
        return (min((turn.start for turn in stretches(agent)), default=0), agent.thread)

    inside = {agent.thread for agent in agents}
    children, order, stack = {}, [], []
    for agent in sorted(agents, key=began, reverse=True):
        if agent.parent in inside:
            children.setdefault(agent.parent, []).append(agent)
        else:
            stack.append((agent, 0))
    while stack:
        agent, depth = stack.pop()
        order.append((agent, depth))
        stack.extend((child, depth + 1) for child in children.get(agent.thread, ()))
    return order


def with_parents(agents):
    by_thread = {agent.thread: agent for agent in agents}
    kept = set()
    for agent in agents:
        thread = agent.thread if stretches(agent) else None
        while thread in by_thread and thread not in kept:
            kept.add(thread)
            thread = by_thread[thread].parent
    return [agent for agent in agents if agent.thread in kept]


def is_open(row):
    return not row["end"]


NAME_BYTES = 36


ID_BYTES = 12


LABEL_BYTES = 20


SUMMARY_BYTES = 36


MODEL_BYTES = 24


LINK_BYTES = 90


def encode(data, clean):
    """data as JSON. Each string value in it is clean(value), then written as entities."""
    def written(value):
        if isinstance(value, str):
            return entities(clean(value))
        if isinstance(value, dict):
            return {key: written(each) for key, each in value.items()}
        return [written(each) for each in value] if isinstance(value, (list, tuple)) else value

    return json.dumps(written(data), separators=(",", ":"), ensure_ascii=False)


def size(text):
    return len(entities(text).encode())


def clip(text, limit):
    if size(text) <= limit:
        return text
    kept = min(len(text), limit)
    while kept and size(text[:kept] + "…") > limit:
        kept -= 1
    return text[:kept] + "…"


def count(number, noun):
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def people(agents, subagents):
    return ", ".join(count(number, noun) for number, noun in ((agents, "agent"), (subagents, "sub-agent")) if number)


def agents_in(rows):
    return sum(row.stands_for for row in rows)


def union(spans, status):
    bars = []
    for span in sorted(spans, key=lambda span: span.x):
        if bars and span.x <= bars[-1].x + bars[-1].w:
            bars[-1] = Span(bars[-1].x, max(bars[-1].x + bars[-1].w, span.x + span.w) - bars[-1].x, status)
        else:
            bars.append(Span(span.x, span.w, status))
    return bars


def summary(rows, depth, label):
    spans = [span for row in rows for span in row.spans]
    failed = [span for span in spans if span.status is Status.FAILED]
    bars = union((span for span in spans if span.status is not Status.FAILED), Status.DONE) + union(failed, Status.FAILED)
    models = {row.model for row in rows}
    return Row(depth, label, models.pop() if len(models) == 1 else "", Status.DONE,
               sum(row.seconds for row in rows), tuple(bars), None, agents_in(rows), all(row.earlier for row in rows))


def shown(item):
    fits = size(item.pr) <= LINK_BYTES
    return replace(item, id=clip(item.id, ID_BYTES), summary=clip(item.summary, SUMMARY_BYTES), pr=item.pr if fits else "", no_link=item.no_link if fits else CUT)


def tally(items):
    counts = {}
    for item in items:
        counts[item.state] = counts.get(item.state, 0) + 1
    ordered = [word for state, (word, _) in STATE_WORDS.items() if state not in FINISHED_STATES]
    ordered.append(UNKNOWN_STATE[0])
    seen = set(ordered)
    parts = [f"{counts[word]} {word}" for word in ordered if counts.get(word)]
    for item in items:
        word = item.state
        if word not in seen:
            parts.append(f"{counts[word]} {word}")
            seen.add(word)
    return ", ".join(parts)


def flight_of(items, goals):
    ids = {goal.id for goal in goals}
    grouped = {goal.id: [] for goal in goals}
    loose = []
    labeled = False
    for item in items:
        resolved = item.goal in ids
        if item.kind or resolved:
            labeled = True
        (grouped[item.goal] if resolved else loose).append(item)
    if not labeled:
        return None
    groups = [StripGroup(goal.name, tally(grouped[goal.id]), tuple(grouped[goal.id])) for goal in goals if grouped[goal.id]]
    if loose:
        groups.append(StripGroup(UNASSIGNED, tally(loose), tuple(loose)))
    return Flight(tuple(groups))


def checksum(text):
    value, units = 2166136261, text.encode("utf-16-le")
    for at in range(0, len(units), 2):
        value = ((value ^ int.from_bytes(units[at:at + 2], "little")) * 16777619) & 0xFFFFFFFF
    return value


def model_slot(name: str) -> int:
    """MIXED_SLOT when empty, OTHER_SLOT for OTHER_MODEL, else checksum slot 1 to MODEL_SLOTS."""
    if not name:
        return MIXED_SLOT
    if name == OTHER_MODEL:
        return OTHER_SLOT
    return checksum(name) % MODEL_SLOTS + 1


def palette_of(legend) -> dict:
    """Allocate in sorted name order. Reserve OTHER_SLOT for OTHER_MODEL. Wrap after eleven named models. Stable for one model set."""
    palette, used = {}, {MIXED_SLOT, OTHER_SLOT}
    available = [1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12]
    for name in sorted(name for name, _ in legend):
        if name == OTHER_MODEL:
            palette[name] = OTHER_SLOT
        elif not name:
            palette[name] = MIXED_SLOT
        elif name not in palette:
            wanted = model_slot(name)
            if wanted not in used and wanted in available:
                palette[name] = wanted
                used.add(wanted)
            else:
                free = [slot for slot in available if slot not in used]
                if free:
                    palette[name] = free[0]
                    used.add(free[0])
                else:
                    wrapped = available[checksum(name) % len(available)]
                    palette[name] = wrapped
    return palette


def span(window):
    seconds = round(window.end - window.start)
    parts = ((seconds // 3600, "h"), (seconds % 3600 // 60, "m"), (seconds % 60, "s"))
    return " ".join(f"{number}{unit}" for number, unit in parts if number)


def dur(seconds):
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m"
