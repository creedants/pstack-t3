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


STORE_RECORD = "restaurant.json"


STORE_LOCK = "restaurant.lock"


UNITS_TABLE = "dishes.tsv"


LOG_TABLE = "log.tsv"


GOALS_TABLE = "goals.tsv"


THREADS_TABLE = "threads.tsv"


STORE_COLUMNS = {
    UNITS_TABLE: ("id", "at", "state", "station", "tickets", "task", "thread", "branch", "pr", "sha", "summary", "timebox", "lease", "paths", "reported", "kind", "goal"),
    LOG_TABLE: ("at", "kind", "id", "state", "note"),
    GOALS_TABLE: ("id", "at", "state", "name", "purpose", "done", "share", "closed"),
    THREADS_TABLE: ("at", "dish", "attempt", "role", "thread", "seat", "start", "end"),
}


ADDED_COLUMNS = {UNITS_TABLE: 2}


TABLE_WORDS = {UNITS_TABLE: "work item table", LOG_TABLE: "log", GOALS_TABLE: "goals table", THREADS_TABLE: "thread table"}


RECORD_WORDS = {"restaurant": "name", "projectRoot": "project root", "thread": "thread", "previousThread": "previous thread"}


STATE_WORDS = {
    "in-progress": ("working", "go"),
    "in-review": ("in review", "info"),
    "passed": ("passed review", "info"),
    "queued": ("landing", "warn"),
    "sent-back": ("sent back", "warn"),
    "blocked": ("blocked", "bad"),
    "merged": ("merged", ""),
    "dropped": ("dropped", ""),
}


UNKNOWN_STATE = ("other", "")


FINISHED_STATES = ("merged", "dropped")


UNGROUPED = "Not tied to a work item"


UNNAMED = "work item"


KINDS = ("feature", "fix", "polish", "upkeep", "study")


UNASSIGNED = "Unassigned"


class ActivityError(Exception):
    status = 1


class SourceError(ActivityError):
    status = 3


@dataclass(frozen=True)
class GoalRecord:
    id: str
    name: str


@dataclass(frozen=True)
class Goal:
    id: str
    name: str


@dataclass(frozen=True)
class Unit:
    id: str
    state: str
    summary: str
    pr: str
    worker: str
    earlier_workers: tuple[str, ...]
    task: str
    kind: str = ""
    goal: str = ""
    branch: str = ""


@dataclass(frozen=True)
class Store:
    name: str
    slug_parts: frozenset
    coordinators: tuple[str, ...]
    units: tuple[Unit, ...]
    project_root: str = ""
    goals: tuple[GoalRecord, ...] = ()
    threads: tuple = ()


def read_table(directory, table):
    """The rows of a table's finished lines after its header line. A table that does not exist has none.
    A finished line ends in a newline. The bytes after the last newline are never decoded.
    A finished line that is not UTF-8 is refused as malformed, with its own line number. So is a finished line after the header with a wrong field count.
    A work item row may lack its last cell or its last two, and each missing cell reads as empty.
    The header line is decoded and never parsed.
    """
    columns = STORE_COLUMNS[table]
    try:
        lock = os.open(directory / STORE_LOCK, os.O_RDONLY)
    except FileNotFoundError:
        lock = None
    try:
        if lock is not None:
            fcntl.flock(lock, fcntl.LOCK_SH)
        try:
            data = (directory / table).read_bytes()
        except FileNotFoundError:
            return []
    finally:
        if lock is not None:
            os.close(lock)
    rows = []
    for number, line in enumerate(data.split(b"\n")[:-1], start=1):
        try:
            text = line.decode()
        except UnicodeDecodeError:
            raise malformed(table, number) from None
        if number == 1:
            continue
        fields = text.split("\t")
        missing = len(columns) - len(fields)
        if not 0 <= missing <= ADDED_COLUMNS.get(table, 0):
            raise malformed(table, number)
        rows.append(dict(zip(columns, fields + [""] * missing)))
    return rows


def malformed(table, number):
    return ActivityError(f"line {number} of the store's {TABLE_WORDS[table]} is malformed; fix or remove it")


def record_text(meta, key):
    """The string the store's record holds at key, or an empty string when the key is absent or null."""
    value = meta.get(key)
    if value is None:
        return ""
    try:
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError
        value.encode()
    except ValueError:
        raise ActivityError(f"the store's coordinator record holds a {RECORD_WORDS[key]} this tool cannot read; restore it and run this again") from None
    return value


def goals_of(directory):
    seen = set()
    kept = []
    for row in read_table(directory, GOALS_TABLE):
        ident = row["id"]
        if not ident or ident in seen:
            continue
        seen.add(ident)
        kept.append(GoalRecord(ident, row["name"]))
    return tuple(kept)


def read_store(directory):
    try:
        try:
            meta = json.loads((directory / STORE_RECORD).read_text())
        except (FileNotFoundError, NotADirectoryError):
            raise ActivityError("that directory holds no coordinator's store; pass --at <store directory> or set BRIGADE_DIR") from None
        except (ValueError, RecursionError):
            meta = None
        if not isinstance(meta, dict):
            raise ActivityError("the store's coordinator record is not a JSON object; restore it and run this again")
        if meta.get("role") == "admin":
            raise ActivityError("this is the executive admin's store; pass one coordinator's store directory")
        units = read_table(directory, UNITS_TABLE)
        log = read_table(directory, LOG_TABLE)
        goals = goals_of(directory)
        threads = tuple(read_table(directory, THREADS_TABLE))
    except OSError as error:
        raise ActivityError(f"cannot read the store ({error.strerror}); check its permissions and run this again") from None
    earlier = {}
    for row in log:
        if row["kind"] == "worker" and row["state"] == "retired" and row["note"]:
            earlier.setdefault(row["id"], []).append(row["note"])
    by_id = {
        row["id"]: Unit(row["id"], row["state"], row["summary"], row["pr"], row["thread"], tuple(earlier.get(row["id"], ())), row["task"], row["kind"], row["goal"], row["branch"])
        for row in units
    }
    name, root = record_text(meta, "restaurant"), record_text(meta, "projectRoot")
    coordinators = tuple(value for value in (record_text(meta, "thread"), record_text(meta, "previousThread")) if value.strip())
    return Store(name, frozenset(re.findall(r"[a-z0-9]+", f"{name} {Path(root).name}".lower())), coordinators, tuple(by_id.values()), root, goals, threads)


def roots_of(store):
    workers = {thread for unit in store.units for thread in (unit.worker, *unit.earlier_workers) if thread}
    return frozenset(store.coordinators) | workers
