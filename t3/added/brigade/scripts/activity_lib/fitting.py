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
from activity_lib.t3state import LIVE, QUIET, Status
from activity_lib.page import LABEL_BYTES, MODEL_BYTES, NAME_BYTES, Span, agents_in, clip, count, flight_of, palette_of, people, shown, span, summary


KEPT_ITEMS = (24, 16, 12, 8, 6, 4, 2, 0)


MAX_GROUPS = 6


MAX_ROWS = 6


MAX_SPANS = 4


COORDINATOR_SPANS = 8


MAX_STRIP = 8


JOINED = (Status.RUNNING, Status.QUEUED, Status.FAILED, Status.UNKNOWN, Status.STOPPED, Status.DONE)


def families(rows):
    runs = []
    for row in rows:
        if row.depth == 0 or not runs:
            runs.append([])
        runs[-1].append(row)
    return runs


def kept_rows(rows, keeps):
    kept, above = [], []
    for row, keep in zip(rows, keeps):
        del above[row.depth:]
        if keep:
            kept.append(replace(row, depth=sum(above)))
        above.append(keep)
    return kept


def finished(group):
    return group.item is not None and not group.item.in_flight and not any(row.status in LIVE for row in group.rows)


def fold_finished_items(page):
    palette = palette_of(page.legend)
    groups = []
    for group in page.groups:
        if finished(group) and agents_in(group.rows) >= 2:
            made = summary(group.rows, 0, people(group.agents, group.subagents))
            made = replace(made, color_slot=palette.get(made.model, made.color_slot))
            groups.append(replace(group, rows=(made,)))
        else:
            groups.append(group)
    return replace(page, groups=tuple(groups), fold=page.fold + 1)


def fold_quiet_subagents(page):
    palette = palette_of(page.legend)
    groups = []
    for group in page.groups:
        rows = []
        for family in families(group.rows):
            folds = [index > 0 and row.status in QUIET for index, row in enumerate(family)]
            folded = [row for row, fold in zip(family, folds) if fold]
            if agents_in(folded) >= 2:
                made = summary(folded, 1, count(agents_in(folded), "sub-agent"))
                made = replace(made, color_slot=palette.get(made.model, made.color_slot))
                family = [*kept_rows(family, [not fold for fold in folds]), made]
            rows += family
        groups.append(replace(group, rows=tuple(rows)))
    return replace(page, groups=tuple(groups), fold=page.fold + 1)


def keep_finished_items(limit, page):
    groups, seen, items, agents = [], 0, 0, 0
    for group in page.groups:
        seen += finished(group)
        if finished(group) and seen > limit:
            items, agents = items + 1, agents + agents_in(group.rows)
        else:
            groups.append(group)
    hidden = replace(page.hidden, dropped_items=page.hidden.dropped_items + items, dropped_agents=page.hidden.dropped_agents + agents)
    return replace(page, groups=tuple(groups), hidden=hidden, fold=page.fold + 1)


def first(values, limit, urgent):
    chosen = set(sorted(range(len(values)), key=lambda index: (not urgent(values[index]), index))[:limit])
    return [index in chosen for index in range(len(values))]


def joined(spans, limit):
    spans = sorted(spans, key=lambda span: span.x)
    while len(spans) > limit:
        at = min(range(len(spans) - 1), key=lambda index: spans[index + 1].x - spans[index].x - spans[index].w)
        left, right = spans[at], spans[at + 1]
        status = min(left.status, right.status, key=JOINED.index)
        spans[at:at + 2] = [Span(left.x, max(left.x + left.w, right.x + right.w) - left.x, status)]
    return tuple(sorted(spans, key=lambda span: span.status is Status.FAILED))


def narrow(row, limit=None):
    return replace(row, label=clip(row.label, LABEL_BYTES), model=clip(row.model, MODEL_BYTES), spans=row.spans if limit is None else joined(row.spans, limit))


def counted(hidden, cuts, unlisted):
    """hidden with the agents and items a cut leaves out added to its counts.
    Each of cuts is a group as it was and the rows of it that stay, or None for a group that is cut.
    unlisted is how many items in flight leave the strip.
    """
    cut, gone_items, gone_agents = 0, 0, 0
    for group, rows in cuts:
        lost = agents_in(group.rows) - agents_in(rows or ())
        # A cut group's item in flight is in page.items, so the strip lists it or cut_in_flight counts it. Any other item is counted here.
        if rows is None and group.item and not group.item.in_flight:
            gone_items, gone_agents = gone_items + 1, gone_agents + lost
        else:
            cut += lost
    return replace(hidden, cut_agents=hidden.cut_agents + cut, cut_in_flight=hidden.cut_in_flight + unlisted,
                   dropped_items=hidden.dropped_items + gone_items, dropped_agents=hidden.dropped_agents + gone_agents)


def cut_items(page, items):
    kept = tuple(items)
    return replace(page, items=kept, flight=flight_of(kept, page.goals))


def count_line(group):
    return f"{group.heading} · {group.count}" if group.heading else group.count


def cap_everything(page):

    def running(row):
        return row.open_seconds is not None

    groups, cuts = [], []
    stays = first(page.groups, MAX_GROUPS, lambda group: any(running(row) for row in group.rows))
    for group, stay in zip(page.groups, stays):
        keeps = first(group.rows, MAX_ROWS if stay else 0, lambda row: running(row) or row.status is Status.FAILED)
        rows = tuple(narrow(row, MAX_SPANS) for row in kept_rows(group.rows, keeps))
        cuts.append((group, rows if stay else None))
        if stay:
            groups.append(replace(group, item=shown(group.item) if group.item else None, rows=rows))
    drawn = [group.item for group in groups]
    listed = first(page.items, MAX_STRIP, lambda item: shown(item) in drawn)
    items = tuple(shown(item) for item, keep in zip(page.items, listed) if keep)
    hidden = counted(page.hidden, cuts, len(page.items) - len(items))
    coordinator = narrow(page.coordinator, COORDINATOR_SPANS) if page.coordinator else None
    page = replace(page, name=clip(page.name, NAME_BYTES), coordinator=coordinator, groups=tuple(groups), hidden=hidden, fold=page.fold + 1)
    return cut_items(page, items)


FOLDS = (fold_finished_items, fold_quiet_subagents, *(partial(keep_finished_items, limit) for limit in KEPT_ITEMS), cap_everything)
