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
from brigade_lib._watch.progress import attempt_count

def require_seat(seat):
    """The cleaned provider/model seat. Provider is the text before the first slash. The model id is the rest and may contain further slashes. Both parts must be non-empty, and the seat holds no whitespace."""
    value = clean(seat)
    provider, _, model = value.partition("/")
    if not (provider and model) or any(character.isspace() for character in value):
        raise BrigadeError("a model is provider/model")
    return value


def latest_models(restaurant):
    """The latest model log row per seat. The latest row for a seat wins."""
    found = {}
    for row in restaurant.rows("log.tsv"):
        if row["kind"] == "model":
            found[row["id"]] = row
    return found


def skip_lines(restaurant):
    """One line per seat whose latest model row is skipped, sorted by seat."""
    return [f"skipped {seat} ({row['note']}); run model clear {shlex.quote(seat)} after a smoke test passes"
            for seat, row in sorted(latest_models(restaurant).items()) if row["state"] == "skipped"]


def record_model_skip(restaurant, seat, cause):
    """Record one provider/model seat as skipped. The same cause writes no row while the seat stays skipped with it."""
    name = require_seat(seat)
    latest = latest_models(restaurant).get(name)
    if latest is not None and latest["state"] == "skipped" and latest["note"] == cause:
        return f"{name}: already skipped"
    restaurant.log("model", name, "skipped", cause)
    return f"{name}: recorded as skipped"


def record_model_clear(restaurant, seat):
    """Record a passing smoke test for one seat and clear its skip."""
    name = require_seat(seat)
    latest = latest_models(restaurant).get(name)
    if latest is None:
        raise BrigadeError(f"{name} is not skipped")
    if latest["state"] == "cleared":
        return f"{name}: already cleared"
    restaurant.log("model", name, "cleared", "smoke test passed")
    return f"{name}: no longer skipped"


def record_worker_end(restaurant, ident, error):
    """Record that this attempt's worker ended or failed and wrote no report.

    The log row is the only dish write, so the dish keeps its lease, its branch,
    and its thread cell.
    """
    reason = clean(error)
    if not reason:
        raise BrigadeError("--worker-ended needs a reason")
    _, dish = restaurant.find("dishes.tsv", ident)
    if dish["state"] != "in-progress":
        raise BrigadeError(f"{ident} is {dish['state']}; --worker-ended records an in-progress dish")
    if not dish["thread"]:
        raise BrigadeError(f"{ident} has no worker thread recorded")
    if dish.get("reported") == "yes":
        raise BrigadeError(f"{ident} has a report-back on this attempt")
    if report_is_current(restaurant, dish):
        raise BrigadeError(f"{ident} has a report file for this attempt")
    attempt = str(attempt_count(restaurant, dish))
    for row in restaurant.rows("log.tsv"):
        if row["kind"] == "ended" and row["id"] == ident and row["state"] == attempt:
            if row["note"] == reason:
                return f"{ident}: worker end already recorded"
            raise BrigadeError(f"{ident} already records a different worker end for this attempt")
    restaurant.log("ended", ident, attempt, reason)
    return f"{ident}: worker ended with no report; the recorded reason is {reason}"


def report_is_current(restaurant, dish):
    """True when reports/<id>.md exists and its mtime is at least the last attempt start."""
    path = restaurant.dir / "reports" / f"{dish['id']}.md"
    if not path.exists():
        return False
    written = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    start = attempt_starts(restaurant, dish)[-1]
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return written >= start


