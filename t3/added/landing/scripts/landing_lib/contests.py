import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import math
import os
import posixpath
import re
import shlex
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
from landing_lib import seam

def second_of(row):
    return row["b"] if row["first"] == row["a"] else row["a"]


def active_contests(db):
    """Open contests, and settled ones whose first holder has not landed an entry yet."""
    rows = db.execute("SELECT * FROM contest WHERE state IN ('open', 'settled') ORDER BY id").fetchall()
    return [row for row in rows if not contest_done(db, row)]


def contest_done(db, row):
    return row["state"] == "settled" and db.execute(
        "SELECT 1 FROM entry WHERE holder = ? AND state = 'landed'", (row["first"],)).fetchone() is not None


def held_holders(db):
    """Each held holder and the lowest active contest that holds it."""
    held = {}
    for row in active_contests(db):
        for holder in (row["a"], row["b"]) if row["state"] == "open" else (second_of(row),):
            held.setdefault(holder, row["id"])
    return held


def unheld_queued(store):
    held = held_holders(store.db)
    return [entry for entry in store.entries("queued") if entry["holder"] not in held]


def contest_id(text):
    if not re.fullmatch(r"C?\d+", text or ""):
        raise seam.LandError(f"{text!r} is not a contest id such as C3")
    return int(text.lstrip("C"))


def contest(store, owner, holders=None, settle=None, first=None, cancel=None):
    """Every form waits for the queue lock, so a ruling never changes under a running land."""
    with open(store.dir / ".queue.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        with store.tx() as db:
            seam.check_ruling_owner(db, owner)
            if holders is not None:
                return open_contest(store, db, holders)
            if settle is not None:
                return settle_contest(store, db, contest_id(settle), first)
            return cancel_contest(store, db, contest_id(cancel))


def open_contest(store, db, holders):
    pair = [holder.strip() for holder in holders.split(",")]
    if len(pair) != 2 or not all(pair) or pair[0] == pair[1]:
        raise seam.LandError("a contest needs two different holders")
    a, b = pair
    for row in active_contests(db):
        if {row["a"], row["b"]} == {a, b}:
            return f"C{row['id']}"
    started = db.execute("SELECT * FROM entry WHERE holder IN (?, ?) AND state IN ('landing', 'awaiting-merge', 'landed') "
                         "ORDER BY id LIMIT 1", (a, b)).fetchone()
    if started:
        raise seam.LandError(f"{started['holder']} has {seam.entry_label(started['id'])} {started['state']}; there is nothing left to order")
    cursor = db.execute("INSERT INTO contest (at, a, b, state) VALUES (?, ?, ?, 'open')", (seam.stamp(), a, b))
    store.log(db, "contest", cursor.lastrowid, "open", f"{a},{b}")
    return f"C{cursor.lastrowid}"


def settle_contest(store, db, number, first):
    row = db.execute("SELECT * FROM contest WHERE id = ?", (number,)).fetchone()
    if not row:
        raise seam.LandError(f"no C{number}")
    if row["state"] == "cancelled":
        raise seam.LandError(f"C{number} is cancelled")
    if first not in (row["a"], row["b"]):
        raise seam.LandError(f"{first} is not a holder in C{number}")
    second = row["b"] if first == row["a"] else row["a"]
    settled = f"C{number}: {first} lands first, {second} waits"
    if contest_done(db, row):
        if row["first"] == first:
            return settled
        raise seam.LandError(f"C{number} is done: {row['first']} landed")
    if row["first"] and row["first"] != first:
        # An open PR can merge on its own, so an order that has started cannot be reversed.
        started = db.execute("SELECT * FROM entry WHERE holder = ? AND state IN ('landing', 'awaiting-merge') ORDER BY id LIMIT 1",
                             (row["first"],)).fetchone()
        if started:
            raise seam.LandError(f"C{number} cannot put {first} first: {row['first']} has {seam.entry_label(started['id'])} {started['state']}")
    edges = [(other["first"], second_of(other), other["id"]) for other in active_contests(db)
             if other["state"] == "settled" and other["id"] != number]
    cycle = sorted(ident for start, end, ident in edges if start in reach(edges, second) and first in reach(edges, end))
    if cycle:
        raise seam.LandError(f"C{number} cannot order {first} before {second}: it closes a cycle with "
                        + ", ".join(f"C{ident}" for ident in cycle))
    if (row["state"], row["first"]) != ("settled", first):
        db.execute("UPDATE contest SET first = ?, state = 'settled' WHERE id = ?", (first, number))
        store.log(db, "contest", number, "settled", first)
    return settled


def reach(edges, start):
    """Every holder that must land after start, start included, under these first-to-second orders."""
    seen, todo = {start}, [start]
    while todo:
        node = todo.pop()
        for before, after, _ident in edges:
            if before == node and after not in seen:
                seen.add(after)
                todo.append(after)
    return seen


def cancel_contest(store, db, number):
    row = db.execute("SELECT state FROM contest WHERE id = ?", (number,)).fetchone()
    if not row:
        raise seam.LandError(f"no C{number}")
    if row["state"] != "cancelled":
        db.execute("UPDATE contest SET state = 'cancelled' WHERE id = ?", (number,))
        store.log(db, "contest", number, "cancelled")
    return f"C{number} cancelled"
