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
from brigade_lib.reviews import item_verdicts, pass_check, round_count, round_debt, unrecorded_reviews
from brigade_lib.shared import BrigadeError, MalformedTable, clean, slug
from brigade_lib.store import MESSAGE_ONLY, ROUND_KIND, TABLES, reporting_of, round_budget_of, store_path
from brigade_lib.tickets import EVERY_ROW, label_words, priority_of, read_labels, request_files
from brigade_lib.startable import NO_WORKER, STARTS_AFTER, Standing, Waiting, lease_refusals, plan, startable, startable_lines, waiting_ticket
from brigade_lib.watch import _latest_trunk, closing_options, dish_progress, open_item_decisions, option_key, record_model_clear, record_model_skip, record_worker_end, report_is_current, require_seat, watch, worker_where

def ticket_lines(restaurant, state, held):
    labels, lines = read_labels(restaurant, EVERY_ROW), []
    for row in restaurant.rows("rail.tsv"):
        if state and row["state"] != state:
            continue
        line = f"{row['id']} {row['state']} {priority_of(row)}{label_words(labels.tickets[row['id']])} [{row['source']}] {row['summary']}"
        if row["ref"]:
            line += f" {row['ref']}"
        if row["state"] == "waiting" and row["id"] in held:
            line += f" blocked: {held[row['id']][0]}"
        lines.append(line)
    return "\n".join(lines)


def hang_attempt(row, starts):
    """The attempt a hang row belongs to: its `(attempt <n>)` suffix, or the attempt running when it was logged."""
    match = re.search(r" \(attempt (\d+)\)$", row["note"])
    if match:
        return int(match.group(1))
    return sum(start <= datetime.fromisoformat(row["at"]) for start in starts)


def record_hang(restaurant, ident, provider, minutes, attempt=None):
    if not clean(provider):
        raise BrigadeError("hang needs a provider")
    if minutes < 0:
        raise BrigadeError("minutes must be 0 or more")
    _, dish = restaurant.find("dishes.tsv", ident)
    starts = attempt_starts(restaurant, dish)
    current = len(starts)
    if attempt is not None and not 1 <= attempt <= current:
        raise BrigadeError(f"{ident} has attempts 1 to {current}")
    # An earlier attempt was replaced, so only the current one waits for its report-back.
    if attempt in (None, current) and dish.get("reported") != "yes":
        earlier = f"; for a run left open by an earlier attempt, pass --attempt {'1' if current == 2 else f'1 to {current - 1}'}"
        raise BrigadeError("no report-back on this attempt" + (earlier if current > 1 else ""))
    number = attempt or current
    for row in restaurant.rows("log.tsv"):
        if row["kind"] == "hang" and row["id"] == ident and hang_attempt(row, starts) == number:
            return f"{ident}: hang already recorded"
    suffix = f" (attempt {attempt})" if attempt else ""
    restaurant.log("hang", ident, "open", f"{provider} {minutes}m{suffix}")
    return f"{ident}: {provider} open {minutes}m{suffix}"


