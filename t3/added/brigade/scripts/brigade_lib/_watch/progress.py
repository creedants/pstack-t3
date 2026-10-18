import argparse
import fcntl
import fnmatch
import functools
import hashlib
import io
import json
import os
import posixpath
import re
import runpy
import shlex
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from brigade_lib import seam
from brigade_lib.items import attempt_starts
from brigade_lib.leases import block_inputs, entry_line, fire_command, recheck_blocks, renew_line, workers_line
from brigade_lib.reviews import round_debt
from brigade_lib.shared import BrigadeError, clean
from brigade_lib.store import LEASED_STATES, OPEN_LINES, OPEN_RUN_MINUTES
from brigade_lib.tickets import handed_count, handoff_id, inbox_file, sibling_rails, taken_row

def option_key(text):
    return text.strip().rstrip(".").casefold()


def closing_options(row):
    if not row.get("dish", "").strip():
        return []
    default = option_key(row.get("default", ""))
    options = []
    for option in row.get("options", "").split(","):
        option = option.strip()
        if option and option_key(option) != default:
            options.append(option)
    return options


def open_item_decisions(rows):
    decisions = {}
    for row in rows:
        dish = row["dish"]
        if row["state"] == "open" and dish and dish not in decisions:
            decisions[dish] = row
    return decisions


def worker_where(dish):
    """The worker words a progress sentence names."""
    if dish["thread"]:
        return f"thread {dish['thread']}"
    if dish["task"]:
        return f"task {dish['task']}"
    return "no worker recorded"


def attempt_count(restaurant, dish):
    """The attempt this dish is on, counted from 1."""
    return len(attempt_starts(restaurant, dish))


def ended_row(restaurant, dish):
    """The log row that records this attempt's worker end, or None.

    The attempt number is the row's state cell, so no row is rewritten when a
    replacement worker starts another attempt.
    """
    number = str(attempt_count(restaurant, dish))
    found = None
    for row in restaurant.rows("log.tsv"):
        if row["kind"] == "ended" and row["id"] == dish["id"] and row["state"] == number:
            found = row
    return found


def recovery_lines(dish, error):
    """The flag line and the four recovery steps for a dish whose worker ended with no report."""
    ident = dish["id"]
    branch = dish.get("branch") or ""
    context = shlex.quote(error)
    # A brief needs a goal, an acceptance, a verify, a base, and a contract, so the line names them
    # instead of a command brief refuses.
    steps = [f"  keep the commits on {shlex.quote(branch)} and record no new branch; the dish keeps its lease"
             if branch else "  keep the commits where the worker left them; the dish keeps its lease",
             f"  write the continuation brief: brief {ident} with --base <trunk ref>, --context {context}, "
             "and the goal, acceptance, verify, and contract it needs",
             f"  launch a fresh thread in the same worktree and record it with dish {ident} --thread <new thread id>"]
    # The record needs a thread, but the cell can be cleared afterwards without starting another attempt.
    steps.append(f"  archive the dead thread {shlex.quote(dish['thread'])}" if dish["thread"]
                 else "  archive the dead worker")
    return [f"{ident}: worker ended with no report; the recorded reason is {error}", *steps]


def dish_progress(restaurant, dish, moment, held=False):
    """The in-progress lines watch prints for one dish. Empty when the dish is held or not in progress.

    The report file's mtime is read here. Call this under the store lock.
    """
    if held or dish["state"] != "in-progress":
        return []
    starts = attempt_starts(restaurant, dish)
    if not dish["thread"] and len(starts) > 1:
        return [f"{dish['id']}: in progress with no worker thread; launch a fresh worker"]
    start = starts[-1]
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    minutes = int((moment - start).total_seconds() // 60)
    timebox = int(dish.get("timebox") or 60)
    report_path = restaurant.dir / "reports" / f"{dish['id']}.md"
    where = worker_where(dish)
    written = datetime.fromtimestamp(report_path.stat().st_mtime, timezone.utc) if report_path.exists() else None
    if written and written >= start:
        if dish.get("reported") == "yes":
            ago = int((moment - written).total_seconds() // 60)
            if ago > OPEN_RUN_MINUTES:
                return [f"{dish['id']}: reported {ago}m ago, not in review; read the thread ({where})"]
            return [f"{dish['id']}: report written {ago}m ago; review it even if the worker's run is still open ({where})"]
        return [f"{dish['id']}: report written, no report-back ({where})"]
    # A dead worker is neither running nor over its timebox, so its lines come before both.
    ended = ended_row(restaurant, dish)
    if ended:
        return recovery_lines(dish, ended["note"])
    if minutes > timebox:
        return [f"{dish['id']}: over its {timebox}m timebox at {minutes}m with no report; read its thread and decide ({where})"]
    return [f"{dish['id']}: running {minutes}m of {timebox}m ({where})"]


