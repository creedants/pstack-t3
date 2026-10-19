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

OPEN = "open"


NOT_RECORDED = "not recorded"


NOT_REQUESTED = "not requested"


MODEL_MISSING = "model not recorded"


SEATS_DIFFER = "seats differ"


SEAT_MISSING = "seat not recorded"


CLOSED = ("merged", "dropped")


ABSENT_WORDS = (OPEN, NOT_RECORDED)


SINCE = re.compile(r"\d{4}-\d{2}-\d{2}")


DISH_ID = re.compile(re.escape(PREFIX["dishes.tsv"]) + r"\d+")


NATIVE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,200}$")


MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,80}$")


INDEX_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


SCHEMA = "token schema is not the one this command knows"


UNREADABLE = "T3's database could not be opened read-only"


PRICE_KEYS = ("input", "cache_read", "cache_write", "output")


MILLION = Decimal(1000000)


PLACES = Decimal("0.000001")


PROVIDER_THREADS = "orchestration_v2_projection_provider_threads"


PROVIDER_TURNS = "orchestration_v2_projection_provider_turns"


THREADS = "orchestration_v2_projection_threads"


TURN_FIELDS = (
    "usageStatus", "inputTokens", "cachedInputTokens",
    "cacheCreationTokens", "outputTokens", "reasoningTokens",
)


DRIVERS = {
    "claudeAgent": "claude",
    "claudeLean": "claude",
    "codex": "turns",
    "opencode": "turns",
    "grok": "gap",
    "cursor": "gap",
}


@dataclass(frozen=True)
class Measure:
    value: int | None
    gaps: tuple[tuple[str, int], ...]

    @staticmethod
    def of(value):
        return Measure(value, ())

    @staticmethod
    def missing(word):
        return Measure(None, ((word, 1),))


def _total(measures):
    value, counted, gaps = 0, False, {}
    for measure in measures:
        if measure.value is None:
            for word, count in measure.gaps:
                gaps[word] = gaps.get(word, 0) + count
        else:
            value += measure.value
            counted = True
    ordered = tuple((word, gaps[word]) for word in ABSENT_WORDS if gaps.get(word))
    if not counted:
        return Measure(None, ordered)
    return Measure(value, ordered)


@dataclass(frozen=True)
class Item:
    dish_id: str
    kind: str
    goal: str
    fired: datetime | None
    wall: Measure
    rounds: int
    send_backs: int
    worker_key: str
    reviewer_key: str
    threads: tuple[str, ...] | None


def _item_key(item):
    return item.dish_id


def _goal_key(item):
    return item.goal or "goal not recorded"


def _kind_key(item):
    return item.kind or "kind not recorded"


def _model_key(item):
    return item.worker_key


def _reviewer_key(item):
    return item.reviewer_key


DIMENSIONS = {
    "item": _item_key,
    "goal": _goal_key,
    "kind": _kind_key,
    "model": _model_key,
    "reviewer": _reviewer_key,
}


@dataclass(frozen=True)
class _Question:
    by: str
    item: str | None
    since: datetime | None
    tokens: bool
    t3_home: str | None
    as_json: bool


@dataclass(frozen=True)
class _Row:
    key: str
    wall: Measure
    rounds: Measure
    send_backs: Measure
    threads: Measure
    items: tuple


class _Store:
    """rows and warn only. Every other attribute raises spend is read-only."""

    def __init__(self, restaurant):
        object.__setattr__(self, "_restaurant", restaurant)

    def __getattribute__(self, name):
        if name not in ("rows", "warn"):
            raise BrigadeError("spend is read-only")
        restaurant = object.__getattribute__(self, "_restaurant")
        return getattr(restaurant, name)


class _Bare(Exception):
    def __init__(self):
        # The failing log line must not leave this function inside the exception.
        Exception.__init__(self)


class _Schema(Exception):
    pass


class _Closed(Exception):
    pass


@dataclass(frozen=True)
class _Counts:
    incoming: int
    cache_read: int
    cache_write: int
    outgoing: int
    reasoning: int | None


@dataclass(frozen=True)
class _Call:
    ident: str
    model: str
    incoming: int
    cache_read: int
    cache_write: int
    outgoing: int
    reasoning: int | None


def _add(left, right):
    reasoning = None if left.reasoning is None or right.reasoning is None else left.reasoning + right.reasoning
    return _Counts(
        left.incoming + right.incoming, left.cache_read + right.cache_read,
        left.cache_write + right.cache_write, left.outgoing + right.outgoing,
        reasoning,
    )


def _merge(parts):
    merged = {}
    for part in parts:
        for name, counts in part.items():
            merged[name] = _add(merged[name], counts) if name in merged else counts
    return merged or None


