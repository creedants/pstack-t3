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
from brigade_lib._watch.progress import dish_progress, open_item_decisions
from brigade_lib._watch.runs import _idle_completed, _idle_line, _read_worker_runs
from brigade_lib._watch.seats import skip_lines
from brigade_lib._watch.trunk import _contract_trunk, _latest_trunk, _observe_trunk

HANDOFF_BYTES = 40000


def handoff_size_line(directory):
    path = directory / "handoff.md"
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
    except OSError:
        return None
    if size <= HANDOFF_BYTES:
        return None
    return f"handoff.md is {size} bytes. Rewrite its current-state section in place and move the old text to an archive file."


def watch(restaurant):
    rails = sibling_rails(restaurant)
    with restaurant.checked():
        restaurant.fence()
        progress = {}
        moment = datetime.now(timezone.utc)
        dishes = [dish for dish in restaurant.rows("dishes.tsv") if dish["state"] not in ("merged", "dropped")]
        decisions = open_item_decisions(restaurant.rows("86.tsv"))
        debts = {dish["id"]: round_debt(restaurant, dish) for dish in dishes}
        for dish in dishes:
            held = bool(decisions.get(dish["id"]) or debts[dish["id"]])
            sentences = dish_progress(restaurant, dish, moment, held)
            if sentences:
                progress[dish["id"]] = sentences
        threads = [dish["thread"] for dish in dishes
                   if dish["state"] == "in-progress" and dish.get("thread")]
        lines = []
        for ticket in restaurant.rows("rail.tsv"):
            if ticket["state"] != "moved":
                continue
            name = ticket["dish"].removeprefix("to:")
            target = restaurant.dir.parent / name
            handoff = handoff_id(restaurant.dir, ticket["id"])
            taken = taken_row(rails.get(target.resolve()), handoff)
            # take writes the ticket before it deletes the file, so a missing file with no ticket was never delivered.
            if inbox_file(target, handoff).exists():
                if taken is None:
                    lines.append(f"{ticket['id']}: moved to {name}, waiting for ticket take")
            elif taken is None:
                lines.append(f"{ticket['id']}: moved to {name}, not delivered; run ticket move {ticket['id']} --to {name} again")
        handed = handed_count(restaurant)
        if handed:
            lines.append(f"handed to you: {handed}; run ticket take")
        inputs = block_inputs(restaurant)
        workers = workers_line(restaurant.meta, inputs[1])
        # A skipped seat is a restaurant-wide fact, not a dish line, so it sits between the two.
        skipped = skip_lines(restaurant)
        project_root = restaurant.meta.get("projectRoot", "")
        latest = _latest_trunk(restaurant.rows("log.tsv"))
    # land.py waits on the landing database, so every call runs outside the store lock.
    # The T3 run read also runs outside the lock for the same reason.
    try:
        runs, run_skip = _read_worker_runs(threads)
    except Exception as error:
        runs, run_skip = {}, None
        try:
            run_skip = _worker_skip(error)
        except Exception:
            run_skip = "cannot see worker runs: unavailable"
    if runs:
        for dish in dishes:
            if dish["state"] != "in-progress" or not dish.get("thread"):
                continue
            if decisions.get(dish["id"]) or debts[dish["id"]]:
                continue
            current = progress.get(dish["id"])
            if not current or len(current) != 1:
                continue
            line = current[0]
            if not (line.startswith(f"{dish['id']}: running ") or line.startswith(f"{dish['id']}: over its ")):
                continue
            completed = _idle_completed(runs, dish["thread"], moment)
            if completed is not None:
                progress[dish["id"]] = [_idle_line(dish, completed)]
    item_lines, answered = [], True
    for dish in dishes:
        decision = decisions.get(dish["id"])
        pending = debts[dish["id"]]
        found = list(progress.get(dish["id"], []))
        entry, submitted = entry_line(restaurant, dish) if dish["state"] in ("passed", "queued") else (None, False)
        # A submitted entry owns the lease now, so a released lease is not a reason to claim again.
        if dish["lease"] and dish["state"] in LEASED_STATES and not submitted:
            line, ok = renew_line(restaurant, dish)
            answered = answered and ok
            found += [line] if line else []
        found += [entry] if entry else []
        if pending:
            item_lines.append(f"{dish['id']}: {debts[dish['id']]} send-backs, decision pending")
        if decision:
            item_lines.append(f"{dish['id']}: open decision {decision['id']}: {decision['question']}; "
                              f"launch no worker or verifier until 86 answer {decision['id']}")
        if decision or pending:
            item_lines += found
        else:
            item_lines += found or [f"{dish['id']}: {OPEN_LINES[dish['state']]}"]
    for ident, note, held in recheck_blocks(restaurant, inputs):
        lines.append(f"{ident}: {held[1]}" if held else f"{ident}: unblocked; run {fire_command(ident, note)}")
    try:
        trunk_line, trunk_row = _observe_trunk(project_root, latest)
    except Exception as error:
        trunk_line, trunk_row = None, None
        try:
            contract = _contract_trunk(project_root)
            trunk = contract["trunk"] if contract else (latest.get("id") if latest else None)
            mode = contract["mode"] if contract else None
            if trunk and mode != "local":
                text = str(error).strip().splitlines()
                detail = text[-1] if text else ""
                reason = detail or "observation failed"
                trunk_line = "cannot see trunk CI: %s" % reason
                if not (latest and latest.get("id") == trunk and latest.get("state") == "unseen"
                        and latest.get("reason") == reason):
                    trunk_row = (trunk, "unseen", reason)
        except Exception:
            pass
    if trunk_row is not None:
        restaurant.log("trunk", trunk_row[0], trunk_row[1], trunk_row[2])
    if trunk_line:
        lines.append(trunk_line)
    if answered:
        # walk --stale-hours then measures whether this coordinator still keeps its leases alive.
        restaurant.change_meta(lastActivityAt=seam.now())
    text = "\n".join([workers, *skipped, *([run_skip] if run_skip else []),
                      *(item_lines + lines or ["no work in progress"])])
    sentence = handoff_size_line(restaurant.dir)
    if sentence:
        restaurant.warn(sentence)
    return text


