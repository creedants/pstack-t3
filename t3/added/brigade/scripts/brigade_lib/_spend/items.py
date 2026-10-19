import json
import math
import os
import re
import runpy
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

from brigade_lib.reviews import item_verdicts, send_backs
from brigade_lib.shared import BrigadeError, age
from brigade_lib.store import PREFIX, WORKER_ROLES
from brigade_lib._spend.model import CLOSED, DISH_ID, NOT_RECORDED, OPEN, SEATS_DIFFER, SEAT_MISSING, SINCE, DIMENSIONS, Item, Measure, _Question, _Row, _Store, _total

def _question(args):
    since = None
    if args.since is not None:
        if SINCE.fullmatch(args.since) is None:
            raise BrigadeError(f"--since {args.since!r} is not YYYY-MM-DD")
        try:
            since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise BrigadeError(f"--since {args.since!r} is not YYYY-MM-DD") from None
    item = args.item
    if item is not None and DISH_ID.fullmatch(item) is None:
        raise BrigadeError(f"--item {item!r} is not a dish id")
    return _Question(args.by, item, since, bool(args.tokens), getattr(args, "t3_home", None), bool(args.json))


def _stamp(text):
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _seat_text(seat):
    _head, slash, tail = seat.partition("/")
    return tail if slash else seat


def _one_seat(seats):
    distinct = list(dict.fromkeys(seat for seat in seats if seat))
    if not distinct:
        return None
    if len(distinct) > 1:
        return SEATS_DIFFER
    return _seat_text(distinct[0])


def _worker_key(dish_id, thread_rows, pass_rows):
    seats = [row["seat"] for row in thread_rows if row["dish"] == dish_id and row["role"] in WORKER_ROLES]
    chosen = _one_seat(seats)
    if chosen is not None:
        return chosen
    authors = [row["author"] for row in item_verdicts(pass_rows, dish_id)]
    return _one_seat(authors) or SEAT_MISSING


def _reviewer_key(dish_id, pass_rows):
    seats = sorted({row["verifier"] for row in item_verdicts(pass_rows, dish_id) if row["verifier"]})
    return " + ".join(seats) if seats else SEAT_MISSING


def _wall(dish, log_rows):
    closes = [row for row in log_rows if row["kind"] == "dish" and row["id"] == dish["id"] and row["state"] in CLOSED]
    if not closes:
        if dish["state"] in CLOSED:
            return Measure.missing(NOT_RECORDED)
        return Measure.missing(OPEN)
    fired = _stamp(dish["at"])
    ends = [_stamp(row["at"]) for row in closes]
    if fired is None or any(end is None for end in ends):
        return Measure.missing(NOT_RECORDED)
    delta = (max(ends) - fired).total_seconds()
    if delta < 0:
        return Measure.missing(NOT_RECORDED)
    return Measure.of(int(delta))


def _threads(dish_id, thread_rows):
    found = []
    for row in thread_rows:
        if row["dish"] != dish_id or not row["thread"] or row["thread"] in found:
            continue
        found.append(row["thread"])
    return tuple(found) if found else None


def _items(store, question):
    dishes = store.rows("dishes.tsv")
    passed = store.rows("pass.tsv")
    log_rows = store.rows("log.tsv")
    thread_rows = store.rows("threads.tsv")
    if question.item is not None and all(row["id"] != question.item for row in dishes):
        raise BrigadeError(f"no {question.item} in dishes.tsv")
    items = []
    for dish in dishes:
        if question.item is not None and dish["id"] != question.item:
            continue
        fired = _stamp(dish["at"])
        if question.since is not None and (fired is None or fired < question.since):
            continue
        ident = dish["id"]
        items.append(Item(
            ident, dish["kind"], dish["goal"], fired, _wall(dish, log_rows),
            len(item_verdicts(passed, ident)), len(send_backs(passed, ident)),
            _worker_key(ident, thread_rows, passed), _reviewer_key(ident, passed),
            _threads(ident, thread_rows),
        ))
    return items


def _dish_number(dish_id):
    return int(dish_id[len(PREFIX["dishes.tsv"]):])


def _threads_by_item(items):
    groups = {}
    for item in items:
        for thread in item.threads or ():
            groups.setdefault(thread, []).append(item.dish_id)
    return groups


def _warn_shares(store, items):
    texts = []
    for ids in _threads_by_item(items).values():
        unique = sorted(set(ids), key=_dish_number)
        if len(unique) < 2:
            continue
        if len(unique) == 2:
            texts.append(f"{unique[0]} and {unique[1]} share a ledger thread")
        else:
            texts.append(f"{', '.join(unique[:-1])}, and {unique[-1]} share a ledger thread")
    for text in dict.fromkeys(texts):
        store.warn(text)


def _rows(items, by):
    order, buckets = [], {}
    for item in items:
        key = DIMENSIONS[by](item)
        if key not in buckets:
            order.append(key)
            buckets[key] = []
        buckets[key].append(item)
    rows = []
    for key in order:
        group = tuple(buckets[key])
        # A reviewer row has no wall clock, so the item duration is not copied onto it.
        walls = [Measure.missing(NOT_RECORDED) for _ in group] if by == "reviewer" else [item.wall for item in group]
        threads = [Measure.missing(NOT_RECORDED) if item.threads is None else Measure.of(len(item.threads)) for item in group]
        rows.append(_Row(
            key, _total(walls), _total(Measure.of(item.rounds) for item in group),
            _total(Measure.of(item.send_backs) for item in group), _total(threads), group,
        ))
    return rows


