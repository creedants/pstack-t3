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
from activity_lib.page import NAME_BYTES, clip, count, shown
from activity_lib.attempts import brief_attempt
from activity_lib.fitting import cut_items, narrow
from activity_lib.sizing import items_of


DEFAULT_HOURS = 3.0


MIN_HOURS = 0.01


MAX_HOURS = 168.0


BUDGET = 16000


FIXED_BUDGET = 8000


MAX_BUDGET = 500000


def line_count(page):
    seen = set()
    total = 0
    for item in items_of(page):
        if item.key in seen:
            continue
        seen.add(item.key)
        total += len(item.lines) + len(item.fold.lines)
    return total


def limit_ledger(page, keep):
    """The page with the first keep attempt lines in items_of order on every copy of each work item. cut_lines counts the lines the prefix leaves off.
    items_of order is page.items first, then each group item not in page.items. Each work item key appears once, current lines before fold payload within a key. Within a key, payload drops before current lines.
    """
    seen, remaining, removed = set(), keep, 0
    kept_current, kept_payload = {}, {}
    for item in items_of(page):
        if item.key in seen:
            continue
        seen.add(item.key)
        take_current = min(len(item.lines), remaining)
        remaining -= take_current
        take_payload = min(len(item.fold.lines), remaining)
        remaining -= take_payload
        kept_current[item.key] = item.lines[:take_current]
        kept_payload[item.key] = item.fold.lines[:take_payload]
        removed += (len(item.lines) - take_current) + (len(item.fold.lines) - take_payload)

    def trimmed(item):
        if item is None or item.key not in kept_current:
            return item
        fold = replace(item.fold, lines=kept_payload[item.key]) if item.fold.summary else item.fold
        return replace(item, lines=kept_current[item.key], fold=fold)

    groups = tuple(replace(group, item=trimmed(group.item)) if group.item is not None else group for group in page.groups)
    items = tuple(trimmed(item) for item in page.items)
    hidden = replace(page.hidden, cut_lines=page.hidden.cut_lines + removed)
    return cut_items(replace(page, groups=groups, items=items, hidden=hidden), items)


def newest_ledger(page, keep):
    """The page with keep attempt lines left, the newest lines of each work item, on every copy of that item. cut_lines counts the lines the suffix leaves off.
    items_of order is page.items first, then each group item not in page.items. Each work item key appears once, and its fold payload holds its older attempts, so its payload lines drop before its current lines.
    A drop takes the oldest attempt line of each work item that has one, in items_of order, then the second oldest of each work item that still has one, and so on, so every work item keeps a suffix of its current lines and a suffix of its fold payload's lines.
    """
    listed, seen = [], set()
    for item in items_of(page):
        if item.key not in seen:
            seen.add(item.key)
            listed.append(item)
    total = sum(len(item.lines) + len(item.fold.lines) for item in listed)
    count = max(total - keep, 0)

    def dropped(number):
        taken, left, position = {}, number, 0
        while left:
            for item in listed:
                if len(item.lines) + len(item.fold.lines) > position:
                    taken[item.key] = position + 1
                    left -= 1
                    if not left:
                        break
            position += 1
        return taken

    taken = dropped(count)

    def trimmed(item):
        if item is None or item.key not in taken:
            return item
        cut, payload = taken[item.key], item.fold.lines
        lines = item.lines[max(cut - len(payload), 0):]
        fold = replace(item.fold, lines=payload[min(cut, len(payload)):]) if item.fold.summary else item.fold
        return replace(item, lines=lines, fold=fold)

    groups = tuple(replace(group, item=trimmed(group.item)) if group.item is not None else group for group in page.groups)
    items = tuple(trimmed(item) for item in page.items)
    hidden = replace(page.hidden, cut_lines=page.hidden.cut_lines + count)
    return cut_items(replace(page, groups=groups, items=items, hidden=hidden), items)


def briefed(page):
    """The page with every fold summary in bounded count form. Counts and state stay exact, and every line stays."""
    def trimmed(item):
        if item is None or not item.fold.summary:
            return item
        return replace(item, fold=replace(item.fold, summary=brief_attempt(item.fold.ranks, item.fold.counts)))

    groups = tuple(replace(group, item=trimmed(group.item)) if group.item is not None else group for group in page.groups)
    items = tuple(trimmed(item) for item in page.items)
    return cut_items(replace(page, groups=groups, items=items), items)


def within_budget(page, budget, clean, render, brief=False, ledger=limit_ledger):
    """The page, its render, and whether that render's UTF-8 length is at most budget.
    A page that already fits, or a page with no attempt line, is returned as it is.
    Any other page keeps the most attempt lines whose render fits, searched by count through ledger(page, count). limit_ledger(page, keep) keeps the first keep attempt lines in items_of order, and newest_ledger(page, keep) keeps each work item's newest lines. When no count fits, the summaries are bounded to their attempt counts and the search runs again, so a long enumeration of attempts that are not one contiguous range no longer adds bytes beyond its count. Counts and state stay exact, and no lane is dropped. When even the briefed zero line form is over budget, that form is returned as it is.
    """
    text = render(page, clean)
    count = line_count(page)
    fits = len(text.encode()) <= budget
    if fits or count == 0:
        return page, text, fits
    cache = {count: (page, text, False)}

    def at(keep):
        if keep not in cache:
            trimmed = ledger(page, keep)
            body = render(trimmed, clean)
            cache[keep] = (trimmed, body, len(body.encode()) <= budget)
        return cache[keep]

    low, high, best = 0, count - 1, None
    while low <= high:
        mid = (low + high) // 2
        if at(mid)[2]:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    if best is None:
        if brief:
            return at(0)
        return within_budget(briefed(page), budget, clean, render, True, ledger)
    for _ in range(count + 1):
        chosen = at(best)[2]
        nxt = best + 1
        if chosen and (nxt >= count or not at(nxt)[2]):
            break
        if not chosen:
            if best == 0:
                break
            best -= 1
        else:
            best += 1
    return at(best)


def shortened(page):
    """The page the plain form prints when it is over its budget. Every row and item stays.
    The name, each item's id and summary, and each group row's label and model are clipped as cap_everything clips them, and each link it would leave out is left out.
    """
    groups = tuple(replace(group, item=shown(group.item) if group.item else None, rows=tuple(narrow(row) for row in group.rows)) for group in page.groups)
    return cut_items(replace(page, name=clip(page.name, NAME_BYTES), groups=groups), (shown(item) for item in page.items))
