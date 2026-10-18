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
from contextlib import contextmanager, redirect_stderr
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from brigade_lib import seam, spend
from brigade_lib.leases import holding_blocks
from brigade_lib.reviews import CLAIMS_LINE, CLAIMS_STATES
from brigade_lib.shared import BrigadeError, MalformedTable, clean, plural
from brigade_lib.store import CLOSED_GOAL_STATES, KINDS, Restaurant, holder_prefix, is_admin, parse_row, reporting_of, siblings, store_path
from brigade_lib.tickets import EVERY_ROW, Label, Tally, finished_requests, handed_count, intake_list, read_labels, request_files, sibling_named, tell

REQUEST_NOTE = re.compile(r"^to (\S+): (.*)$")


def admin_requests(admin):
    """Each request the admin recorded, as (id, coordinator directory name, line)."""
    found = []
    for row in admin.rows("log.tsv"):
        match = REQUEST_NOTE.match(row["note"]) if row["kind"] == "request" else None
        if match:
            found.append((row["id"], match.group(1), match.group(2)))
    return found


def send_request(admin, to, line):
    """Record the request in the admin's log, then publish it into the coordinator's inbox under the admin's lock."""
    name, sibling = sibling_named(admin, to)
    line = clean(line)
    if not line:
        raise BrigadeError("a request needs its line")
    ident = f"A{max((int(ident[1:]) for ident, _, _ in admin_requests(admin)), default=0) + 1}"
    admin.log("request", ident, "sent", f"to {name}: {line}")
    admin.publish(admin.dir.parent / name / "inbox" / f"{ident}.line", line + "\n")
    return f"{ident} for {name}; {tell(name, sibling)}"


def relayed(admin):
    """The rows sync copied, as {relay id: the coordinator's row}."""
    return {row["id"]: json.loads(row["note"]) for row in admin.rows("log.tsv") if row["kind"] == "relay"}


def finished_by_coordinator(admin):
    """Each coordinator's finished requests, read from its own log.tsv. Run it before the admin's lock, as sync reads."""
    names = {name for _, name, _ in admin_requests(admin)}
    return {name: finished_requests(Restaurant(admin.dir.parent / name)) for name in names
            if (admin.dir.parent / name / "restaurant.json").is_file()}


def republish(admin, finished):
    """Publish again each request whose file is gone and whose coordinator has no inbox-done row for it."""
    lines = []
    for ident, name, line in admin_requests(admin):
        path = admin.dir.parent / name / "inbox" / f"{ident}.line"
        if path.exists() or ident in finished.get(name, ()):
            continue
        admin.publish(path, line + "\n")
        lines.append(f"{ident} republished for {name}")
    return "\n".join(lines) or "nothing to republish"


def ruling_line(row):
    line = (f"{row['id']} {row['state']} {row['kind']} ({row['parties']}): {row['question']} "
            f"Decided by {row['rule']}: {row['decision']}.")
    return line + (f" Supersedes {row['supersedes']}." if row["supersedes"] else "")


def _ruling_note(row, replacer):
    """The log note for a ruling row's current state. replacer maps an id to the ruling that replaced it."""
    state = row["state"]
    if state == "in-force":
        return row["decision"]
    if state in ("superseded", "overruled"):
        return f"replaced by {replacer[row['id']]}"
    return f"{row['decision']} ({state})"


def unlogged_rulings(admin):
    """Each rulings.tsv row whose state is not the latest ruling event, as (id, state, note).

    rulings.tsv is the authority. A new in-force row comes before the row it replaces, which is the order a ruling command logs them.
    """
    rows = admin.rows("rulings.tsv")
    latest = {}
    for event in admin.rows("log.tsv"):
        if event["kind"] == "ruling":
            latest[event["id"]] = event["state"]
    replacer = {row["supersedes"]: row["id"] for row in rows if row["supersedes"]}
    pending = sorted((row for row in rows if latest.get(row["id"]) != row["state"]), key=lambda row: row["state"] != "in-force")
    return [(row["id"], row["state"], _ruling_note(row, replacer)) for row in pending]


def log_rulings(admin):
    """Append a ruling event for each rulings.tsv row the log does not yet record. A rerun appends nothing."""
    with admin.guarded():
        for ident, state, note in unlogged_rulings(admin):
            admin.log("ruling", ident, state, note)


def add_ruling(admin, kind, parties, question, rule, decision, supersedes="", ended="superseded"):
    """Record a ruling, and mark the one it replaces, in one rewrite of rulings.tsv. Recording comes before carrying out."""
    rows = admin.rows("rulings.tsv")
    if supersedes:
        old = next((row for row in rows if row["id"] == supersedes), None)
        if old is None:
            raise BrigadeError(f"no {supersedes} in rulings.tsv")
        if old["state"] in ("superseded", "overruled"):
            raise BrigadeError(f"{supersedes} is already {old['state']}; replace the ruling that replaced it")
        old["state"] = ended
    ident = admin.next_id("rulings.tsv")
    rows.append({"id": ident, "at": seam.now(), "kind": kind, "parties": ", ".join(intake_list(parties)), "question": question,
                 "rule": rule, "decision": decision, "supersedes": supersedes, "state": "in-force"})
    admin.save_rows("rulings.tsv", rows)
    log_rulings(admin)
    return ident


def overrule(admin, ident, decision):
    _, old = admin.find("rulings.tsv", ident)
    return add_ruling(admin, old["kind"], old["parties"], old["question"], "user", decision, ident, ended="overruled")


def end_ruling(admin, ident, state):
    rows, row = admin.find("rulings.tsv", ident)
    if row["state"] == state:
        return f"{ident} {state}"
    if row["state"] != "in-force":
        raise BrigadeError(f"{ident} is {row['state']}; only an in-force ruling ends")
    row["state"] = state
    admin.save_rows("rulings.tsv", rows)
    log_rulings(admin)
    return f"{ident} {state}"


def log_rows(data, start):
    """The complete rows of a log.tsv snapshot that starts at byte start.

    Returns (offset, row) pairs, the byte just past the last newline consumed, and the offset of a malformed line or None.
    """
    rows, position = [], 0
    while (newline := data.find(b"\n", position)) >= 0:
        offset = start + position
        try:
            line = data[position:newline].decode()
        except UnicodeDecodeError:
            return rows, offset, offset
        if offset > 0:
            row = parse_row("log.tsv", line)
            if row is None:
                return rows, offset, offset
            rows.append((offset, row))
        position = newline + 1
    return rows, start + position, None


def sync(admin):
    """Copy each coordinator's new log.tsv rows into the admin's log as relay rows, once each.

    Each coordinator's bytes are copied under its shared lock. The admin's lock is taken only after that lock is
    released, so no command holds two stores' locks. A relay id is the coordinator's store path and the row's byte
    offset, which a tail repair never moves, so a rerun after a crash copies nothing twice.
    A document item row is relayed but prints nothing.
    Returns the lines to print and the malformed-line failures.
    """
    cursors = admin.meta.get("cursors") or {}
    copied, failures = [], []
    for name in siblings(admin.dir, admin.meta.get("projectRoot")):
        directory = admin.dir.parent / name
        store = store_path(directory)
        start = int(cursors.get(store, 0))
        data = Restaurant(directory).snapshot("log.tsv", start)
        if data is None:
            continue
        rows, end, malformed = log_rows(data, start)
        copied.append((name, store, rows, end))
        if malformed is not None:
            failures.append(f"{store}/log.tsv at byte {malformed} is malformed; nothing past it relayed")
    lines = []
    with admin.guarded():
        have = relayed(admin)
        cursors = dict(admin.meta.get("cursors") or {})
        for name, store, rows, end in copied:
            for offset, row in rows:
                ident = f"{store}@{offset}"
                if ident in have:
                    continue
                admin.append("log.tsv", {"at": seam.now(), "kind": "relay", "id": ident, "state": "copied",
                                         "note": json.dumps(row, separators=(",", ":"))})
                if row["kind"] != "doc":
                    lines.append(f"{name} {row['at']} {row['kind']} {row['id']} {row['state']}: {row['note']}")
            # A sync that started later may have read further and moved this cursor already.
            cursors[store] = max(end, int(cursors.get(store, 0)))
        admin.change_meta(cursors=cursors, lastActivityAt=seam.now())
    return "\n".join(lines), failures


def admin_sections(admin, since):
    """The admin's report adds the rulings recorded or changed since the last one, each coordinator's relayed
    events except document items, and each coordinator's newest report."""
    events = [event for event in admin.rows("log.tsv") if event["at"] > since]
    changed = {event["id"] for event in events if event["kind"] == "ruling"}
    changed.update(ident for ident, _, _ in unlogged_rulings(admin))
    lines = []
    rulings = [row for row in admin.rows("rulings.tsv") if row["id"] in changed]
    if rulings:
        lines += ["## Rulings", "", *(f"- {ruling_line(row)}" for row in rulings), ""]
    by_store = {}
    for event in events:
        if event["kind"] == "relay":
            by_store.setdefault(event["id"].rpartition("@")[0], []).append(json.loads(event["note"]))
    blocks = []
    for name in siblings(admin.dir, admin.meta.get("projectRoot")):
        directory = admin.dir.parent / name
        closeouts = sorted((directory / "closeouts").glob("*.md"))
        rows = [row for row in by_store.get(store_path(directory), []) if row["kind"] != "doc"]
        if not rows and not closeouts:
            continue
        blocks += [f"### {name}", "", *(f"- {row['at']} {row['kind']} {row['id']} {row['state']}: {row['note']}" for row in rows)]
        blocks += [f"- Newest report: {closeouts[-1]}"] if closeouts else []
        blocks.append("")
    if blocks:
        lines += ["## From each coordinator", "", *blocks]
    return lines


