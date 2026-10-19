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
from activity_lib.t3state import OPEN, Status
from activity_lib.privacy import CUT, REFUSED
from activity_lib.page import NAME_BYTES, clip, count, flight_of, summary
from activity_lib.fitting import FOLDS, MAX_STRIP, count_line, counted, cut_items, first, kept_rows


FRAME_HEIGHT = 2000


FRAME_MARGIN = 40


FRAME_FOLDS = len(FOLDS) - 1


STATS_PX = 121


HEADING_PX = 41


OPEN_PX = 37


LEGEND_PX = (23, 43)


AXIS_PX = 16


GROUP_PX = 34


ROW_PX = 30


LEDGER_PX = 16


LEDGER_GAP_PX = 8


CHIP_PX = 39


STRIP_PX = 35


FOOT_PX = 12


LINE_PX = 16


FOOT_TEXT = 203


FOOT_CHARS = 60


NOTE_CHARS = 64


COUNT_CHARS = 50


COUNT_LINE_PX = 19


COUNT_GAP_PX = 8


def text_lines(characters, per_line):
    return 1 + characters // per_line


def count_slots(text):
    """Slots in one count line. M, W, m, and w take two. Every other character takes one."""
    return sum(2 if char in "MWmw" else 1 for char in text)


def flight_column_px(flight):
    """Pixels the grouped strip's goal lines reserve. None reserves 0. A flight with no groups reserves 0. Each goal reserves GROUP_PX. The goal's name and counts wrap to one or more lines, and each line reserves COUNT_LINE_PX. COUNT_GAP_PX sits between goal lines."""
    if flight is None or not flight.groups:
        return 0
    rows = [text_lines(count_slots(count_line(group)), COUNT_CHARS) for group in flight.groups]
    return COUNT_LINE_PX * sum(rows) + COUNT_GAP_PX * (len(rows) - 1) + GROUP_PX * len(rows)


def fixed_height(page, said):
    """The height of the parts frame_cuts() never cuts. They are stats, the Timeline heading, legend, axis, the coordinator's row, and the footer with the notes in said.
    A character of the name counts as two, because the name can hold any character and FOOT_CHARS is for Latin text.
    """
    lines = text_lines(FOOT_TEXT + 2 * len(page.name), FOOT_CHARS) + sum(text_lines(len(note), NOTE_CHARS) for note in said)
    return STATS_PX + HEADING_PX + LEGEND_PX[len(page.legend) > 1] + AXIS_PX + (ROW_PX if page.coordinator else 0) + FOOT_PX + LINE_PX * lines


def ledger_px(page):
    grouped = {group.item.key for group in page.groups if group.item}
    seen = set()
    total = 0
    for item in items_of(page):
        if item.key in seen:
            continue
        seen.add(item.key)
        summary = 1 if item.fold.summary else 0
        if not item.lines and not summary:
            continue
        total += LEDGER_GAP_PX + LEDGER_PX * (len(item.lines) + summary)
        if item.key not in grouped:
            total += GROUP_PX
    return total


def height(page):
    """The modelled height of the HTML page at 390 px wide.
    fixed_height, plus GROUP_PX a group and ROW_PX a shown row, plus HEADING_PX once for each of the two open lists that has a line and OPEN_PX a line in it, plus STRIP_PX once and CHIP_PX a chip when the page has chips.
    Rows flagged earlier are folded and not counted.
    A grouped strip adds flight_column_px. A page with ledger lines adds ledger_px.
    """
    rows = [row for group in page.groups for row in group.rows if not row.earlier]
    total = fixed_height(page, notes(page, items_of(page))) + GROUP_PX * len(page.groups) + ROW_PX * len(rows)
    for status in OPEN:
        lines = sum(row.open_seconds is not None and row.status is status for row in rows)
        total += HEADING_PX + OPEN_PX * lines if lines else 0
    total += flight_column_px(page.flight)
    return total + (STRIP_PX + CHIP_PX * len(page.items) if page.items else 0) + ledger_px(page)


def frame_cuts(page, limit):
    """The page cut for height, as the cut after reclaim and the cut before it.
    A page whose height() is at most limit is returned as (page, page).
    The name is clipped to NAME_BYTES. At most MAX_STRIP chips stay, those of an item with a group on the page first.
    Rows are tried in three passes, and each row whose parts fit what is left of limit stays. Rows flagged earlier always stay and cost nothing, since height() does not count them.
    The first pass tries every row with a Running now or Queued line, the second every failed row, the third every other row.
    Each pass tries its rows in order of turn, then of group in page order. A row's turn is the number of rows of its group that are tried before it in any pass.
    A group with no row left is cut with its heading. counted() counts the agents and items left out.
    Unused worst case counted note room is reclaimed, except the attempt line floor, and rejected rows are retried in the same order.
    The cut before reclaim has its attempt lines trimmed with fit_lines().
    The reclaimed cut is returned with the cut before it as (final, first_cut) when its height stays at most limit, and as (None, first_cut) when it does not.
    """
    if height(page) <= limit:
        return page, page
    page = replace(page, name=clip(page.name, NAME_BYTES))
    # Room for the notes a cut can add, and for the attempt-line note when the page has attempt lines.
    # Reclaimed room retries rejected rows below.
    has_lines = any(item.lines or item.fold.summary or item.fold.lines for item in items_of(page))
    worst = replace(page.hidden, dropped_items=page.hidden.dropped_items or 1, cut_agents=page.hidden.cut_agents or 1, cut_in_flight=page.hidden.cut_in_flight or 1,
                    cut_lines=(page.hidden.cut_lines or 1) if has_lines else page.hidden.cut_lines)
    left = limit - fixed_height(page, notes(replace(page, hidden=worst), items_of(page)))
    drawn = [group.item for group in page.groups]
    listed = first(page.items, MAX_STRIP, lambda item: item in drawn)
    items = tuple(item for item, keep in zip(page.items, listed) if keep)
    flight = flight_of(items, page.goals)
    left -= (STRIP_PX + CHIP_PX * len(items) + flight_column_px(flight)) if items else 0
    left -= ledger_px(page)

    def tier(row):
        return 0 if row.open_seconds is not None else 1 if row.status is Status.FAILED else 2

    pieces = []
    kept, headed, lists = set(), set(), set()
    for at, group in enumerate(page.groups):
        for index, row in enumerate(group.rows):
            if row.earlier:
                kept.add((at, index))
                headed.add(at)
    left -= GROUP_PX * len(headed)
    for at, group in enumerate(page.groups):
        visible = [index for index, row in enumerate(group.rows) if not row.earlier]
        ranked = sorted(visible, key=lambda index: (tier(group.rows[index]), index))
        pieces += [(tier(group.rows[index]), turn, at, index) for turn, index in enumerate(ranked)]
    for _, _, at, index in sorted(pieces):
        row = page.groups[at].rows[index]
        opened = row.open_seconds is not None
        cost = ROW_PX + (at not in headed) * GROUP_PX + opened * (OPEN_PX + (row.status not in lists) * HEADING_PX)
        if cost <= left:
            left -= cost
            kept.add((at, index))
            headed.add(at)
            if opened:
                lists.add(row.status)

    def build(kept):
        groups, cuts = [], []
        for at, group in enumerate(page.groups):
            rows = tuple(kept_rows(group.rows, [(at, index) in kept for index in range(len(group.rows))]))
            cuts.append((group, rows or None))
            if rows:
                groups.append(replace(group, rows=rows))
        return groups, cuts

    groups, cuts = build(kept)
    first_cut = cut_items(replace(page, groups=tuple(groups), hidden=counted(page.hidden, cuts, len(page.items) - len(items))), items)
    first_cut = fit_lines(first_cut, limit)
    # Reclaim unused worst case counted note room, except the attempt line floor that fit() may still add. The retried result is measured before it is kept.
    actual = counted(page.hidden, cuts, len(page.items) - len(items))
    held = replace(actual, cut_lines=worst.cut_lines)
    reclaimed = fixed_height(page, notes(replace(page, hidden=worst), items_of(page))) - fixed_height(page, notes(replace(page, hidden=held), items_of(page)))
    if reclaimed > 0:
        left += reclaimed
        for _, _, at, index in sorted(pieces):
            if (at, index) in kept:
                continue
            row = page.groups[at].rows[index]
            opened = row.open_seconds is not None
            cost = ROW_PX + (at not in headed) * GROUP_PX + opened * (OPEN_PX + (row.status not in lists) * HEADING_PX)
            if cost <= left:
                left -= cost
                kept.add((at, index))
                headed.add(at)
                if opened:
                    lists.add(row.status)
        groups, cuts = build(kept)
        final = cut_items(replace(page, groups=tuple(groups), hidden=counted(page.hidden, cuts, len(page.items) - len(items))), items)
        if height(final) <= limit:
            return final, first_cut
    return None, first_cut


def framed(page, limit):
    """The cut frame_cuts() makes for height. A page whose height() is at most limit is returned as it is.
    The cut after reclaim is returned when it is at most limit, and the cut before reclaim, with its attempt lines trimmed with fit_lines(), is returned otherwise.
    """
    cut, fallback = frame_cuts(page, limit)
    return cut if cut is not None else fallback


def fit_lines(page, limit):
    """The page with the oldest current attempt lines dropped until its height() is at most limit.
    A page whose height() is at most limit, or that has no current attempt line, is returned as it is.
    A drop takes the oldest current line of every work item, in items_of order, then the second oldest of every work item, and so on, so each work item keeps its newest lines. Fold payload stays. cut_lines grows by the lines dropped.
    The fewest drops whose height() is at most limit is kept, and every current line is dropped when none fits.
    """
    listed, seen = [], set()
    for item in items_of(page):
        if item.key not in seen:
            seen.add(item.key)
            listed.append(item)
    total = sum(len(item.lines) for item in listed)
    if total == 0 or height(page) <= limit:
        return page

    def dropped(count):
        taken, left, position = {}, count, 0
        while left:
            for item in listed:
                if len(item.lines) > position:
                    taken[item.key] = position + 1
                    left -= 1
                    if not left:
                        break
            position += 1

        def trimmed(item):
            if item is None or item.key not in taken:
                return item
            return replace(item, lines=item.lines[taken[item.key]:])

        groups = tuple(replace(group, item=trimmed(group.item)) if group.item is not None else group for group in page.groups)
        items = tuple(trimmed(item) for item in page.items)
        hidden = replace(page.hidden, cut_lines=page.hidden.cut_lines + count)
        return cut_items(replace(page, groups=groups, items=items, hidden=hidden), items)

    for count in range(1, total):
        trimmed = dropped(count)
        if height(trimmed) <= limit:
            return trimmed
    return dropped(total)


def say(number, one, several, **values):
    return (one if number == 1 else several).format(n=number, **values)


def items_of(page):
    return [*page.items, *dict.fromkeys(group.item for group in page.groups if group.item and group.item not in page.items)]


def notes(page, listed):
    """The notes of a page. listed holds the work items the form prints, so a link is counted only for an item the reader sees."""
    rows = [row for group in page.groups for row in group.rows]
    links = [item.no_link for item in listed]
    below = [row.stands_for for row in rows if row.stands_for > 1 and row.depth]
    whole = [row for row in rows if row.stands_for > 1 and not row.depth]
    hidden, lines = page.hidden, []
    if not page.totals.agents + page.totals.subagents:
        lines.append("No agent or sub-agent of this coordinator ran in this window.")
    if below:
        lines.append(say(len(below), "{agents} sub-agents that are done or stopped are shown as 1 summary row.",
                         "{agents} sub-agents that are done or stopped are shown as {n} summary rows.", agents=sum(below)))
    if whole:
        lines.append(say(len(whole), "1 merged or dropped work item with no agent running, queued, or waiting is shown as one row.",
                         "{n} merged or dropped work items with no agent running, queued, or waiting are each shown as one row."))
    if hidden.dropped_items:
        lines.append(say(hidden.dropped_items, "1 merged or dropped work item with {agents} is not shown. Use a larger --max-bytes to see more.",
                         "{n} merged or dropped work items with {agents} are not shown. Use a larger --max-bytes to see more.",
                         agents=count(hidden.dropped_agents, "agent")))
    if hidden.cut_agents:
        lines.append(say(hidden.cut_agents, "1 more agent is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
                         "{n} more agents are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."))
    if hidden.cut_lines:
        lines.append(say(hidden.cut_lines, "1 attempt line is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
                         "{n} attempt lines are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."))
    if hidden.cut_in_flight:
        lines.append(say(hidden.cut_in_flight, "1 more work item in flight is not listed.", "{n} more work items in flight are not listed."))
    if CUT in links:
        lines.append(say(links.count(CUT), "1 pull request link is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
                         "{n} pull request links are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."))
    if REFUSED in links:
        lines.append(say(links.count(REFUSED), "1 pull request link is not shown. It is not an https://host/owner/repository/pull/number address, or it holds text this page removes.",
                         "{n} pull request links are not shown. Each is not an https://host/owner/repository/pull/number address, or it holds text this page removes."))
    if hidden.unstarted:
        lines.append(say(hidden.unstarted, "T3 lists 1 sub-agent with no thread or no start time under a thread this page read. It is not shown.",
                         "T3 lists {n} sub-agents with no thread or no start time under threads this page read. They are not shown."))
    if hidden.unknown_status:
        lines.append(say(hidden.unknown_status, "T3 gave 1 status this tool reads as unknown.", "T3 gave {n} statuses this tool reads as unknown."))
    if hidden.by_request_name:
        lines.append(say(hidden.by_request_name, "1 agent is grouped by the name of the request that started it.",
                         "{n} agents are grouped by the name of the request that started them."))
    if hidden.other_threads:
        lines.append(say(hidden.other_threads, "1 other thread ran in this coordinator's T3 project outside this coordinator.",
                         "{n} other threads ran in this coordinator's T3 project outside this coordinator."))
    return lines
