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
from activity_lib.store import UNGROUPED
from activity_lib.theme import ATTEMPT_CSS, LEDGER_CSS, LEDGER_HOOK, RENDERER, STRIP_CSS, STRIP_HOOK, STYLE, squeezed
from activity_lib.t3state import Status
from activity_lib.page import checksum, count, dur, encode, flight_of, model_slot, people, span, summary
from activity_lib.attempts import spoken
from activity_lib.fitting import FOLDS, count_line
from activity_lib.sizing import FRAME_FOLDS, frame_cuts, height, items_of, notes
from activity_lib.budget import newest_ledger, shortened, within_budget


def fit(page, budget, clean, frame=None):
    """The page and its document after the first fold that leaves it at most budget bytes, or after the last fold.
    Each fold's page is measured with within_budget() with ledger=newest_ledger(), which keeps the most attempt lines whose document fits, a suffix of each work item's lines, and, when no count fits, bounds its fold summaries to their counts.
    With frame, a page whose height() is over frame takes the next fold too until FRAME_FOLDS folds are applied. After that frame_cuts() cuts it, and the first cut whose height() after within_budget() is at most frame and whose document is at most budget bytes stays, or on the last fold the first cut whose height() after within_budget() is at most frame. An over-budget page at a fold that reaches FRAME_FOLDS tries the trimmed page's frame cuts before the next fold. If those cuts fail, the trimmed page fits the frame, and the fold page is not the trimmed page, it then tries the fold page's frame cuts. The first cut whose document is at most budget bytes and whose height() is at most frame is returned. An over-budget cut keeps the most attempt lines whose document fits with newest_ledger(), and, when no count fits, bounds its fold summaries to their counts and keeps the most attempt lines of that page whose document fits, or no attempt line when no count of it fits. When neither cut stays before the last fold the next fold runs, and when no cut stays on the last fold that fold's own page and document are returned.
    """
    for done, fold in enumerate((None, *FOLDS)):
        page = fold(page) if fold else page
        trimmed, document, fits = within_budget(page, budget, clean, render_html, ledger=newest_ledger)
        last = done == len(FOLDS)
        if not fits and not last:
            if done >= FRAME_FOLDS and frame is not None:
                for cut in frame_cuts(trimmed, frame):
                    if cut is None:
                        continue
                    body, rendered, cut_fits = within_budget(cut, budget, clean, render_html, ledger=newest_ledger)
                    if cut_fits and height(body) <= frame:
                        return body, rendered
                if page is not trimmed and height(trimmed) <= frame:
                    for cut in frame_cuts(page, frame):
                        if cut is None:
                            continue
                        body, rendered, cut_fits = within_budget(cut, budget, clean, render_html, ledger=newest_ledger)
                        if cut_fits and height(body) <= frame:
                            return body, rendered
            continue
        page = trimmed
        if frame is None or height(page) <= frame:
            break
        if done >= FRAME_FOLDS:
            accepted = False
            for cut in frame_cuts(page, frame):
                if cut is None:
                    continue
                body, rendered, fits = within_budget(cut, budget, clean, render_html, ledger=newest_ledger)
                if (fits or last) and height(body) <= frame:
                    page, document, accepted = body, rendered, True
                    break
            if accepted:
                break
    return page, document


def fit_text(page, budget, clean):
    text = render_text(page, clean)
    if len(text.encode()) <= budget:
        return text
    _, text, _ = within_budget(shortened(page), budget, clean, render_text)
    return text


WIRE_VERSION = 1


WIRE_STATUS = (Status.RUNNING, Status.QUEUED, Status.WAITING, Status.DONE, Status.FAILED, Status.STOPPED, Status.UNKNOWN)


WIRE_BARS = (Status.DONE, Status.RUNNING, Status.FAILED, Status.STOPPED, Status.UNKNOWN, Status.QUEUED)


TEXT_RUNNING = 7


TEXT_QUEUED = 4


TEXT_ITEMS = 9


TEXT_FAILED = 4


def wire(page):
    models = []

    def model(name):
        if name and name not in models:
            models.append(name)
        return models.index(name) if name else -1

    def bars(spans):
        return [number for span in spans for number in (span.x, span.w, WIRE_BARS.index(span.status))]

    def line(row):
        tail = [row.stands_for, dur(row.open_seconds)] if row.open_seconds is not None else [row.stands_for] if row.stands_for != 1 else []
        return [row.depth, row.label, model(row.model), row.color_slot, WIRE_STATUS.index(row.status), dur(row.seconds), bars(row.spans), *tail]

    items = items_of(page)
    own, totals = page.coordinator, page.totals
    grouped = page.flight is not None

    def cells(item):
        row = [item.id, item.summary, item.state, item.tone, item.pr, 0 if grouped else int(item in page.items)]
        if grouped:
            row.append(item.kind)
        return row

    data = {
        "v": WIRE_VERSION,
        "c": page.name,
        "w": [int(page.window.start), round(page.window.end - page.window.start), span(page.window)],
        "n": [totals.running, totals.agents, totals.subagents, totals.failed],
        "S": [status.value for status in WIRE_STATUS],
        "P": [[name, model_slot(name), agents] for name, agents in page.legend],
        "k": own and [model(own.model), WIRE_STATUS.index(own.status), dur(own.seconds), bars(own.spans)],
        "I": [cells(item) for item in items],
        "G": [[items.index(group.item) if group.item else -1, [line(row) for row in group.rows]] for group in page.groups],
        "M": models,
        "N": notes(page, items),
    }
    if grouped:
        places = {id(item): index for index, item in enumerate(items)}
        data["F"] = [[count_line(group), group.heading, [places[id(item)] for item in group.items]] for group in page.flight.groups]
    if any(item.lines for item in items):
        data["L"] = [[index, [spoken(line) for line in item.lines]] for index, item in enumerate(items) if item.lines]
    if any(item.fold.summary for item in items):
        data["E"] = [[index, item.fold.summary, [spoken(line) for line in item.fold.lines]] for index, item in enumerate(items) if item.fold.summary]
    earlier = [[at, [index for index, row in enumerate(group.rows) if row.earlier]] for at, group in enumerate(page.groups) if any(row.earlier for row in group.rows)]
    if earlier:
        data["R"] = earlier
    return data


def render_html(page, clean):
    """The document. The only strings in it that are not constants of this file are in the data element, and encode() passes each of those to clean."""
    data = encode(wire(page), clean)
    style = STYLE if page.flight is None else STYLE + STRIP_CSS
    script = RENDERER if page.flight is None else RENDERER + STRIP_HOOK
    if any(item.lines or item.fold.summary for item in items_of(page)):
        style += LEDGER_CSS
        script += squeezed(LEDGER_HOOK)
    if any(item.fold.summary for item in items_of(page)):
        style += ATTEMPT_CSS
    return (
        "<!doctype html><meta charset=utf-8><meta name=viewport content=\"width=device-width,initial-scale=1\">"
        f"<title>Agent activity</title><style>{style}</style><div id=o></div>"
        f"<script type=application/json id=d>{data}</script><script>const H={checksum(data)};{script}</script>")


def render_text(page, clean):
    """The plain lines. Each line is joined from its parts first and then passed to clean, whole."""
    def capped(lines, limit, noun):
        return lines[:limit] + ([f"  and {count(len(lines) - limit, 'more ' + noun)}"] if len(lines) > limit else [])

    def line(*parts):
        return "  " + "   ".join(part for part in parts if part)

    def worked(rows):
        return f"{dur(sum(row.seconds for row in rows))} at work"

    running, queued, failed = [], [], []
    for group in page.groups:
        above = {}
        for row in group.rows:
            if row.earlier:
                above[row.depth] = row.label
                continue
            above[row.depth] = row.label
            name = f"{group.item.id} {row.label}" if group.item else row.label
            if row.open_seconds is not None:
                waits = row.status is Status.QUEUED
                (queued if waits else running).append(line(name, f"{'queued' if waits else 'running'} for {dur(row.open_seconds)}",
                                                           f"under {above[row.depth - 1]}" if row.depth else ""))
            if row.status is Status.FAILED:
                failed.append(line(name, row.model, worked([row])))
    drawn = {group.item: f"{people(group.agents, group.subagents)}, {worked(group.rows)}" for group in page.groups if group.item}
    listed = [*drawn, *(item for item in page.items if item not in drawn)][:TEXT_ITEMS]
    more = len(drawn) + sum(item not in drawn for item in page.items) - len(listed)
    loose = [line(UNGROUPED, f"{people(group.agents, group.subagents)}, {worked(group.rows)}") for group in page.groups if not group.item]

    def flat_block(item):
        head = line(item.id, item.state, item.summary, drawn.get(item, "no activity in this window"), item.pr)
        summary = ["    " + item.fold.summary] if item.fold.summary else []
        return [head, *summary, *("    " + spoken(entry) for entry in item.lines)]

    def grouped_block(item):
        head = "  " + line(item.kind, item.id, item.state, item.summary, drawn.get(item, "no activity in this window"), item.pr)
        summary = ["      " + item.fold.summary] if item.fold.summary else []
        return [head, *summary, *("      " + spoken(entry) for entry in item.lines)]

    flat = [part for item in listed for part in flat_block(item)]
    text_flight = flight_of(listed, page.goals)
    if text_flight is None:
        items = flat
    else:
        items = []
        for group in text_flight.groups:
            items.append(line(count_line(group)))
            for item in group.items:
                items.extend(grouped_block(item))
    totals = page.totals
    lines = [
        f"Agent activity for {page.name}, last {span(page.window)}",
        f"{totals.running} running now, {count(totals.agents, 'agent')}, {count(totals.subagents, 'sub-agent')}, {totals.failed} failed",
    ]
    for heading, body in (
            ("Models", [line(name, str(agents)) for name, agents in page.legend]),
            ("Running now", capped(running, TEXT_RUNNING, "running agent")),
            ("Queued", capped(queued, TEXT_QUEUED, "queued agent")),
            ("Work items", items + ([f"  and {count(more, 'more work item')}"] if more else []) + loose),
            ("Failed", capped(failed, TEXT_FAILED, "failed agent")),
            ("Notes", ["  " + note for note in notes(page, listed)])):
        if body:
            lines += [heading, *body]
    return "\n".join(clean(line) for line in lines)
