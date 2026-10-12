#!/usr/bin/env python3
"""Bookkeeping for brigade restaurants.

A restaurant is one directory under the brigade store. Its tables hold current
state and log.tsv holds every change, so a report can say what is new since
the last one. The head chef thread is the only writer.

Kitchen words name files and commands. Output is plain engineering prose.
"""

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

TICKET_STATES = ("waiting", "assigned", "moved", "done", "dropped")
LIVE_TICKET_STATES = ("waiting", "assigned")
DISH_STATES = ("in-progress", "in-review", "passed", "sent-back", "blocked", "queued", "merged", "dropped")
COUNTED_STATES = ("in-progress", "in-review")
# A dish in one of these holds an active lease that watch keeps renewing. A queued dish's lease is the queue's.
LEASED_STATES = ("in-progress", "in-review", "passed", "sent-back", "blocked")
# A dish in one of these has started and is neither merged nor dropped.
IN_FLIGHT_STATES = (*LEASED_STATES, "queued")
OPEN_LINES = {"in-review": "in review", "passed": "passed, not submitted", "sent-back": "sent back", "blocked": "parked"}
VERDICTS = {"pass": "passed", "send-back": "sent-back", "blocked": "blocked", "pass-with-edits": "sent-back"}
EDITS_VERDICT = "pass-with-edits"
MODES = ("full", "light")
ROUND_BUDGET = 3
OWING_STATES = ("in-progress", "in-review", "sent-back", "blocked")
ROUND_DECISIONS = ("known limits", "redesign", "drop")
ROUND_PARKED = "keep parked"
ROUND_KIND = "round-budget"
OPEN_RUN_MINUTES = 10
PRIORITIES_BY_RANK = ("urgent", "normal", "low")
AUTOFIRE = ("off", *PRIORITIES_BY_RANK)
SOURCE_PRIORITY = {"upstream": "urgent", "report": "low"}
KINDS = ("feature", "fix", "polish", "upkeep", "study")
KIND_CHOICE = f"<{'|'.join(KINDS)}>"
# The kind fire gives an item when neither its lead ticket nor --kind gives one. A station not listed here gives none.
STATION_KIND = {"bug-fix": "fix", "perf-issue": "fix", "investigation": "study"}
GOAL_STATES = ("open", "paused", "done", "dropped")
CLOSED_GOAL_STATES = ("done", "dropped")
# The states a goal may move to from each state.
GOAL_MOVES = {"open": ("paused", "done", "dropped"), "paused": ("open", "done", "dropped"),
              "done": ("open",), "dropped": ("open",)}
LOW_RESERVE = 1
LOW_FLAG_DAYS = 7
GENERATED_ROOT = "skills/"
SOURCE_ROOT = "t3/"
LINE_SUFFIX = r":\d+(?:-\d+)?$"


@dataclass(frozen=True)
class StandingText:
    count_label: str
    line_words: str


STANDINGS = {
    "start": StandingText("startable", "startable"),
    "rides": StandingText("riding", "rides with {why}"),
    "blocked": StandingText("blocked", "blocked, {why}"),
    "unknown": StandingText("unknown", "unknown, no paths recorded"),
    "decision": StandingText("decisions", "decision for the owner"),
    "below": StandingText("below auto-start", "below auto-start"),
    "reserve": StandingText("held back", f"held back, low tickets leave {LOW_RESERVE} worker idle while an urgent or normal ticket waits"),
}
NO_WORKER = "waiting for an idle worker"
STARTS_AFTER = "starts after {tickets}"

TABLES = {
    "rail.tsv": ("id", "at", "state", "source", "ref", "dish", "summary", "priority", "paths", "decision", "kind", "goal"),
    "dishes.tsv": ("id", "at", "state", "station", "tickets", "task", "thread", "branch", "pr", "sha", "summary", "timebox", "lease", "paths", "reported",
                   "kind", "goal"),
    "pass.tsv": ("at", "dish", "pr", "sha", "verdict", "author", "verifier", "note", "report", "member"),
    "86.tsv": ("id", "at", "state", "dish", "question", "options", "default", "answer", "kind", "answered"),
    "log.tsv": ("at", "kind", "id", "state", "note"),
    "edits.tsv": ("at", "dish", "from", "to", "report"),
    "goals.tsv": ("id", "at", "state", "name", "purpose", "done", "share", "closed"),
    # Only the executive admin's store has this table.
    "rulings.tsv": ("id", "at", "kind", "parties", "question", "rule", "decision", "supersedes", "state"),
}
# How many trailing cells a row of each table may lack. A missing cell reads as empty.
ADDED_COLUMNS = {"pass.tsv": 2, "86.tsv": 2, "rail.tsv": 5, "dishes.tsv": 2}
PREFIX = {"rail.tsv": "T", "dishes.tsv": "D", "86.tsv": "Q", "rulings.tsv": "R", "goals.tsv": "G"}
ADMIN_DIR = ".admin"
ADMIN_NAME = "executive admin"
WORK_COMMANDS = ("fire", "brief", "contract", "dish", "pass", "edits", "watch", "startable", "next")
# next sets this. A write or a land.py call then raises instead of changing a store.
READ_ONLY = False
# Kind, the store row, and whether the line leaves no store row. The Events table in the skill is the source.
ADMIN_LINES = (
    ("merged", "dish merged", False),
    ("sent-back", "dish sent-back or blocked", False),
    ("decision", "decision open", False),
    ("failed", "none", True),
    ("drained", "none", True),
    ("report", "none", True),
    ("blocked", "ticket blocked", False),
    ("misrouted", "ticket moved", False),
    ("reply", "none", True),
    ("contest", "none", True),
    ("appeal", "none", True),
)
UNSENT = tuple(kind for kind, _, _ in ADMIN_LINES)
MESSAGE_ONLY = frozenset(kind for kind, _, only in ADMIN_LINES if only)
DRAIN_LABELS = ("in progress", "in review", "passed review", "waiting to land")
YES_OCCASIONS = {
    "every-turn": frozenset(("user", "evening", "decision", "failure", "stuck", "merged", "sent-back", "blocked", "every-turn")),
    "milestones": frozenset(("user", "evening", "decision", "failure", "stuck", "merged", "sent-back", "blocked")),
    "digest": frozenset(("user", "evening", "decision", "stuck", "drained")),
}
CLOSE_OCCASIONS = frozenset(("evening", "decision", "user"))
OCCASION_ORDER = ("user", "decision", "evening", "drained", "merged", "sent-back", "blocked", "stuck", "failure")
ROUTINE_DETAILS = {
    "unspecified": "unspecified",
    "report-back": "worker report",
    "checks": "pull request",
    "merge": "merge",
    "liveness": "liveness",
    "intake": "intake",
    "morning": "morning service",
    "evening": "evening report",
    "user": "user message",
    "admin": "admin request",
}
ADMIN_COMMANDS = ("request", "rule", "sync")
RULING_KINDS = ("contested-paths", "ownership", "shares", "queue-order")
RULING_RULES = ("purpose", "priority", "age", "related-work", "dependency", "floor", "user")
RULING_STATES = ("in-force", "done", "expired", "superseded", "overruled")
NOT_STOPPED = "the old run has not been confirmed stopped; wait for it with t3_thread_wait, then pass --stopped <run id>"
NOTHING_HANDED = "nothing handed to you"
NEEDS_CONTRACT = ("brief needs a contract; pass --contract <text> or the contract key in --fields, and write none when the item has no safety, privacy, or integrity property")
SNAPSHOT_CHUNK = 1 << 16
REVIEW_FILE = re.compile(r"(D\d+)-review(-.+)?\.md")
ITEM_REPORT = re.compile(r"(D\d+)\.md")
CLAIMS_LINE = re.compile(r"^[ \t#>*+\-_`\d.)]*Claims check", re.M)
CLAIMS_STATES = ("in-review", "passed")
# A report shows each ticket and dish once, under the latest state it reached since the last report.
SECTIONS = {
    ("dish", "merged"): "Merged",
    ("dish", "passed"): "Passed review, not submitted",
    ("dish", "queued"): "Waiting to land",
    ("dish", "sent-back"): "Sent back after review",
    ("dish", "blocked"): "Blocked",
    ("mode", "full"): "Moved to full mode",
    ("dish", "in-progress"): "In progress",
    ("dish", "in-review"): "In progress",
    ("ticket", "waiting"): "New tickets, not started",
    ("ticket", "moved"): "Handed to another coordinator",
    ("dish", "dropped"): "Dropped",
    ("ticket", "dropped"): "Dropped",
}
# The report sections whose lines print a kind and can be grouped by goal.
GROUPED_SECTIONS = tuple(SECTIONS[key] for key in (("dish", "merged"), ("dish", "in-progress"), ("ticket", "waiting")))

MENU = """# Menu: {restaurant}

## Purpose

What this restaurant exists to achieve, in one or two sentences.

## What good looks like

Checkable outcomes, one per line.

## Off the menu

Work this restaurant does not take, even when asked.

## Budget

Any cap on parallel workers, and whether a provider's usage limit switches this restaurant to light mode.
"""

ADMIN_MENU = """# Menu: {restaurant}

## Purpose

Keep the user up to date across every coordinator on this repository, route the user's requests, and settle conflicts between coordinators by published rules.

## Priorities

Coordinator names, highest first, one per line. Only the user changes this list.

## Off the menu

Writing code, landing work, and every decision that belongs to the user: the repository cap, the landing mode, priorities, and purposes.
"""

HOUSE_RULES = """# House rules: {restaurant}

Paste these into every brief verbatim.

1. Write in plain engineering prose. brigade's kitchen terms name its files and commands only. Never use them in replies, reports, commits, or PRs.
2. Work lands only through the repository's landing queue (the landing skill). Workers never merge, rebase shared branches, or push trunk.
"""


class BrigadeError(Exception):
    pass


class MalformedTable(BrigadeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def slug(text):
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not value:
        raise BrigadeError(f"cannot make a slug from {text!r}")
    return value


def store_root(flag=None):
    if flag:
        return Path(flag)
    if os.environ.get("BRIGADE_STORE"):
        return Path(os.environ["BRIGADE_STORE"])
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "pstack-t3" / "brigade"


def clean(value):
    return re.sub(r"[\t\r\n]+", " ", str(value if value is not None else "")).strip()


# The cells save_rows and append write as they are given. read_labels decides whether each holds a legal value, and
# clean changes no legal value, so cleaning one could only make a cell that is not legal read as legal after a rewrite.
KEPT_CELLS = {"rail.tsv": ("kind", "goal"), "dishes.tsv": ("kind", "goal"), "goals.tsv": ("id",)}


def stored(table, key, value):
    """The text save_rows and append write into one cell.

    A cell in KEPT_CELLS is written as it is given when it is text with no tab and no newline. That is every value a
    table read gives. Every other value is written as clean gives it.
    """
    if key in KEPT_CELLS.get(table, ()) and isinstance(value, str) and "\t" not in value and "\n" not in value:
        return value
    return clean(value)


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, suffix=".tmp") as handle:
        try:
            handle.write(text)
        except BaseException:
            handle.close()
            os.unlink(handle.name)
            raise
    os.replace(handle.name, path)


def is_timestamp(value):
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return "T" in value


def parse_row(table, line):
    """One complete line of a table as a row, or None when its field count or timestamp is wrong."""
    header = TABLES[table]
    fields = line.split("\t")
    missing = len(header) - len(fields)
    if not 0 <= missing <= ADDED_COLUMNS.get(table, 0) or not is_timestamp(fields[header.index("at")]):
        return None
    return dict(zip(header, fields + [""] * missing))


def read_chunk(fd):
    return os.read(fd, SNAPSHOT_CHUNK)


def is_admin(meta):
    return meta.get("role") == "admin"


def holder_prefix(meta):
    """The landing holder prefix of a store: `.admin/` for the executive admin, else the restaurant's slug."""
    return f"{ADMIN_DIR}/" if is_admin(meta) else f"{slug(meta['restaurant'])}/"


def store_path(directory):
    """A store's path under the store root, such as app/docs. Handoff ids and relay ids start with it."""
    return f"{directory.parent.name}/{directory.name}"


# Resolved directories of the exclusive locks this process holds.
# A read of one of these takes no second lock. A read of any other store raises.
HELD_LOCKS = []


class Restaurant:
    def __init__(self, directory, owner=None):
        self.dir = Path(directory)
        if not (self.dir / "restaurant.json").is_file():
            raise BrigadeError(f"{self.dir} is not a restaurant (no restaurant.json); run brigade.py open")
        if owner is not None and not re.fullmatch(r".*@\d+", owner):
            raise BrigadeError(f"--owner takes <thread>@<generation>, got {owner!r}")
        self.owner = owner
        # set --thread changes the owner, so it alone writes without matching the recorded one.
        self.unfenced = False
        self._lock_fd = None
        self._lock_depth = 0
        self._read_depth = 0
        # The warning texts this command has printed. One command makes one Restaurant for its own store.
        self.warned = set()

    def warn(self, text):
        """Print one warning line on standard error. A text this command has printed is not printed again."""
        if text not in self.warned:
            self.warned.add(text)
            print(f"brigade: warning: {text}", file=sys.stderr)

    def owner_block(self):
        """The sentence fence raises, or None when this caller may use the store."""
        meta = self.meta
        if meta.get("generation") is None:
            return None
        recorded = f"{meta.get('thread') or ''}@{meta['generation']}"
        if self.owner is None:
            return f"this store is owned by {recorded}; pass --owner <thread>@<generation> from status"
        if self.owner != recorded:
            return f"owner {self.owner} is stale; this store is owned by {recorded}"
        if not meta.get("thread"):
            return "this store has no recorded thread; it was retired, and only set --thread --expect restarts it"
        return None

    def fence(self):
        """Refuse a write from any thread but the recorded owner. Call it under the lock and keep the lock through the write."""
        block = self.owner_block()
        if block:
            raise BrigadeError(block)

    def land_owner(self):
        """The --owner words for land.py: this store's holder prefix at the caller's generation."""
        if self.owner is None:
            return []
        return ["--owner", f"{holder_prefix(self.meta)}@{self.owner.rpartition('@')[2]}"]

    @contextmanager
    def locked(self):
        """Every write in this store holds an exclusive lock on restaurant.lock.

        The lock is on a sidecar file because rewrites replace a table's inode.
        It is reentrant within one Restaurant, so a command can hold it around
        its reads and writes while each write also takes it.
        """
        if self._lock_depth == 0:
            fd = os.open(self.dir / "restaurant.lock", os.O_RDWR | os.O_CREAT, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._lock_fd = fd
            self._held = self.dir.resolve()
            HELD_LOCKS.append(self._held)
        self._lock_depth += 1
        try:
            yield
        finally:
            self._lock_depth -= 1
            if self._lock_depth == 0:
                HELD_LOCKS.remove(self._held)
                os.close(self._lock_fd)
                self._lock_fd = None

    @contextmanager
    def read_lock(self, create=True):
        """A shared lock for a read, unless this process already holds this store's lock.

        Under its own lock a command reads its own tables and takes no second lock.
        Reading another store while holding a lock raises, so the caller reads that store first.
        No process holds two stores' locks.
        create is false when a missing restaurant.lock must stay missing. A nested read takes no second lock.
        """
        if self._read_depth or self.dir.resolve() in HELD_LOCKS:
            yield
            return
        if HELD_LOCKS:
            raise BrigadeError(
                f"cannot read {self.dir} while holding {HELD_LOCKS[0]}'s lock; "
                "read other stores before taking a lock")
        lock = self.dir / "restaurant.lock"
        if not create and not lock.exists():
            self._read_depth += 1
            try:
                yield
            finally:
                self._read_depth -= 1
            return
        fd = os.open(lock, os.O_RDWR | (os.O_CREAT if create else 0), 0o644)
        self._read_depth += 1
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            yield
        finally:
            self._read_depth -= 1
            os.close(fd)

    def snapshot(self, table, offset=0):
        """The bytes of a table from offset to its end, copied while no writer can repair a tail or append.

        Every table read goes through here, rows and sync included. None when the table does not exist.
        """
        with self.read_lock():
            try:
                fd = os.open(self.dir / table, os.O_RDONLY)
            except FileNotFoundError:
                return None
            try:
                os.lseek(fd, offset, os.SEEK_SET)
                chunks = []
                while chunk := read_chunk(fd):
                    chunks.append(chunk)
            finally:
                os.close(fd)
        return b"".join(chunks)

    @contextmanager
    def checked(self):
        """One command's checks and writes, run against tables that all parse.

        Nothing inside may wait on stdin, a subprocess, or a sleep. That would stop every other command on this store.
        """
        with self.locked():
            for table in TABLES:
                self.rows(table)
            yield

    @contextmanager
    def guarded(self):
        """The one gate for every change this command makes: the store lock, then the owner fence, held through the change."""
        if READ_ONLY:
            raise BrigadeError("brigade: next is read-only")
        with self.locked():
            if not self.unfenced:
                self.fence()
            yield

    def write(self, relative, text):
        self.publish(self.dir / relative, text)

    def publish(self, path, text):
        """Write a file this store owns, or a handoff into a sibling's inbox."""
        with self.guarded():
            write_atomic(path, text)

    def remove(self, path):
        with self.guarded():
            path.unlink()

    @property
    def meta(self):
        return json.loads((self.dir / "restaurant.json").read_text())

    def change_meta(self, drop=(), **fields):
        """Reread restaurant.json under the lock and change only these fields.

        A command that read the file before a replacement can then never write the old owner back.
        """
        with self.guarded():
            meta = self.meta
            meta.update(fields)
            for key in drop:
                meta.pop(key, None)
            self.write("restaurant.json", json.dumps(meta, indent=2) + "\n")
            return meta

    def rows(self, table):
        """The rows of a table's finished lines after its header line. A table that does not exist has none.

        A finished line ends in a newline. The bytes after the last newline are never decoded.
        A finished line that is not UTF-8, or that parse_row turns down, is refused as malformed.
        A finished header line is decoded and never parsed.
        """
        data = self.snapshot(table)
        if data is None:
            return []
        rows = []
        for number, line in enumerate(data.split(b"\n")[:-1], start=1):
            header = number == 1
            try:
                text = line.decode()
            except UnicodeDecodeError:
                text = None
            row = text if header or text is None else parse_row(table, text)
            if row is None:
                raise MalformedTable(f"{store_path(self.dir.resolve())}/{table} line {number} is malformed; fix or remove it")
            if not header:
                rows.append(row)
        return rows

    def save_rows(self, table, rows):
        header = TABLES[table]
        body = ["\t".join(header)] + ["\t".join(stored(table, key, row.get(key, "")) for key in header) for row in rows]
        self.write(table, "\n".join(body) + "\n")

    def append(self, table, row):
        data = ("\t".join(stored(table, key, row.get(key, "")) for key in TABLES[table]) + "\n").encode()
        path = self.dir / table
        with self.guarded():
            if not path.exists():
                self.save_rows(table, [])
            fd = os.open(path, os.O_RDWR | os.O_APPEND)
            try:
                end = os.fstat(fd).st_size
                if end and os.pread(fd, 1, end - 1) != b"\n":
                    # A killed append left an unfinished tail. Writing after it would join two rows.
                    end = os.pread(fd, end, 0).rfind(b"\n") + 1
                    os.ftruncate(fd, end)
                written = os.write(fd, data)
                if written != len(data):
                    os.ftruncate(fd, end)
                    raise BrigadeError(f"wrote {written} of {len(data)} bytes to {table}; nothing appended")
            finally:
                os.close(fd)

    def log(self, kind, ident, state, note=""):
        with self.guarded():
            self.append("log.tsv", {"at": now(), "kind": kind, "id": ident, "state": state, "note": note})
            self.change_meta(lastActivityAt=now())

    def next_id(self, table):
        numbers = [int(row["id"][1:]) for row in self.rows(table) if row.get("id", "")[1:].isdigit()]
        return f"{PREFIX[table]}{max(numbers, default=0) + 1}"

    def find(self, table, ident):
        rows = self.rows(table)
        for row in rows:
            if row["id"] == ident:
                return rows, row
        raise BrigadeError(f"no {ident} in {table}")

    def update(self, table, ident, log_kind, note=None, **fields):
        rows, row = self.find(table, ident)
        changed = {key: value for key, value in fields.items() if value is not None}
        moved = "state" in changed and changed["state"] != row.get("state")
        row.update(changed)
        self.save_rows(table, rows)
        if moved:
            self.log(log_kind, ident, changed["state"], note or row.get("summary") or row.get("question", ""))
        return row


REPORTING = ("every-turn", "milestones", "digest")
CLAIM_WAIT_SECONDS = 1.0
PURPOSE_TEMPLATE = "What this restaurant exists to achieve"
OFF_MENU_TEMPLATE = "Work this restaurant does not take"


def reporting_of(meta):
    value = meta.get("reporting")
    if value in (None, ""):
        return "milestones"
    return value


def menu_section(text, heading):
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else ""


def siblings(directory, project_root):
    """The other coordinators in this project directory on the same project root, by directory name."""
    found = {}
    for meta_path in sorted(directory.parent.glob("*/restaurant.json")):
        if meta_path.parent == directory:
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("projectRoot") == project_root:
            found[meta_path.parent.name] = meta
    return found


def sibling_lines(restaurant):
    blocks = []
    for directory_name, meta in siblings(restaurant.dir, restaurant.meta.get("projectRoot")).items():
        meta_path = restaurant.dir.parent / directory_name / "restaurant.json"
        menu_path = meta_path.parent / "menu.md"
        menu = menu_path.read_text() if menu_path.is_file() else ""
        purpose = menu_section(menu, "Purpose")
        if not purpose or purpose.startswith(PURPOSE_TEMPLATE):
            purpose = "not written yet"
        else:
            purpose = " ".join(purpose.split())
        off = menu_section(menu, "Off the menu")
        if not off or off.startswith(OFF_MENU_TEMPLATE):
            off_line = "not written yet"
        else:
            off_line = "; ".join(line.strip() for line in off.splitlines() if line.strip())
        thread = (meta.get("thread") or "").strip() or "not recorded"
        name = meta.get("restaurant") or meta_path.parent.name
        blocks.append(
            f"sibling {name} ({meta_path.parent}), thread {thread}\n"
            f"  purpose: {purpose}\n"
            f"  off the menu: {off_line}")
    return blocks


def _read_meta(path):
    try:
        text = path.read_text()
    except OSError:
        return None, "missing"
    try:
        return json.loads(text), "ok"
    except json.JSONDecodeError:
        return None, "invalid"


def wait_for_meta(directory):
    path = directory / "restaurant.json"
    deadline = time.monotonic() + CLAIM_WAIT_SECONDS
    while True:
        meta, state = _read_meta(path)
        if state == "ok":
            return meta
        if time.monotonic() >= deadline:
            if state == "invalid":
                raise BrigadeError(f"{path} is not valid JSON")
            raise BrigadeError(f"{directory} has no restaurant.json after one second")
        time.sleep(0.01)


def intake_owner(directory, project_root, source):
    for name, meta in siblings(directory, project_root).items():
        if source in (meta.get("intake") or []):
            return name
    return None


def refuse_owned_intake(directory, project_root, sources):
    for source in sources:
        owner = intake_owner(directory, project_root, source)
        if owner:
            raise BrigadeError(f"{owner} already owns intake from {source}; move tickets to it instead")


def intake_list(text):
    sources = []
    for item in text.split(","):
        item = clean(item)
        if item and item not in sources:
            sources.append(item)
    return sources


def base_source(source):
    """A taken ticket's source is `<source> (from <handoff id>)`, and a request's ticket `<source> (request A<n>)`.

    The intake source is the part before.
    """
    return re.split(r" \((?:from|request) ", source, maxsplit=1)[0]


def priority_of(row):
    return row["priority"] or SOURCE_PRIORITY.get(base_source(row["source"]), "normal")


def paths_of(row):
    return tuple(part for part in row["paths"].split(",") if part)


def ticket_paths(text):
    if text == "":
        return ()
    found = set()
    for part in text.split(","):
        path = posixpath.normpath(part.strip().replace("\\", "/")).lstrip("/")
        if path in ("", ".", "..") or path.startswith("../"):
            raise BrigadeError(f"--paths takes files or directories inside the repository, got {part!r}")
        found.add(path)
    return tuple(sorted(found))


@dataclass(frozen=True)
class Tracked:
    files: frozenset
    directories: frozenset
    names: dict


def unique_names(files):
    sources = {path.rsplit("/", 1)[-1] for path in files if path.startswith(SOURCE_ROOT)}
    by_name = {}
    for path in files:
        name = path.rsplit("/", 1)[-1]
        if not (path.startswith(GENERATED_ROOT) and name in sources):
            by_name.setdefault(name, []).append(path)
    return {name: paths[0] for name, paths in by_name.items() if len(paths) == 1}


def tracked_paths(project_root):
    try:
        result = subprocess.run(["git", "-C", str(project_root), "ls-files", "-z"], capture_output=True,
                                encoding="utf-8", errors="surrogateescape")
    except OSError:
        return None
    if result.returncode != 0:
        return None
    files = frozenset(name for name in result.stdout.split("\0") if name)
    directories = frozenset(str(above) for name in files for above in PurePosixPath(name).parents if str(above) != ".")
    return Tracked(files, directories, unique_names(files))


def pattern_directory(parts):
    marked = next(index for index, part in enumerate(parts) if re.search(r"[*?\[]", part))
    return "/".join(parts[:marked])


def quoted_paths(text, tracked):
    if tracked is None:
        return ()
    found = set()
    for quote in re.finditer(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", text):
        content = quote.group(2)
        span = re.sub(LINE_SUFFIX, "", content.strip())
        if not span or re.search(r"\s", span) or span.startswith("/") or ".." in span.split("/"):
            continue
        span = posixpath.normpath(span)
        if not re.search(r"[*?\[]", span):
            if span != "." and (span in tracked.files or span in tracked.directories):
                found.add(span)
            elif content not in tracked.files and content not in tracked.directories:
                name = re.sub(LINE_SUFFIX, "", content)
                if not re.search(r"[/\s*?\[]", name) and name in tracked.names:
                    found.add(tracked.names[name])
            continue
        directory = pattern_directory(span.split("/"))
        if directory and fnmatch.filter(tracked.files, span):
            found.add(directory)
    return tuple(sorted(found))


def record_paths(restaurant, tracked, write):
    rows = restaurant.rows("rail.tsv")
    lines, recorded = [], False
    for row in rows:
        if row["state"] != "waiting" or paths_of(row):
            continue
        paths = ",".join(quoted_paths(row["summary"], tracked))
        if paths:
            lines.append(f"{row['id']} {'paths' if write else 'would record'} {paths}")
            row["paths"], recorded = paths, True
        else:
            lines.append(f"{row['id']} quotes no tracked path")
    if write and recorded:
        restaurant.save_rows("rail.tsv", rows)
    return "\n".join(lines) or "every waiting ticket records paths"


def set_intake(restaurant, sources):
    meta = restaurant.meta
    refuse_owned_intake(restaurant.dir, meta.get("projectRoot"), sources)
    for source in meta.get("intake") or []:
        if source in sources:
            continue
        held = [row for row in restaurant.rows("rail.tsv")
                if base_source(row["source"]) == source and row["state"] in LIVE_TICKET_STATES]
        if held:
            raise BrigadeError(f"{held[0]['id']} from {source} is {held[0]['state']}; finish, drop, or move it before dropping {source} from intake")
    meta["intake"] = sources
    return meta


def handoff_id(directory, ticket):
    """Unique across the store: the source store's path under the store root, then the ticket."""
    return f"{directory.parent.name}/{directory.name}/{ticket}"


def inbox_file(directory, handoff):
    return directory / "inbox" / f"{handoff.replace('/', '~')}.json"


def sibling_rails(restaurant):
    """Each sibling's rail.tsv, read before this store's lock is taken.

    The caller's lock never held a sibling still, so reading it first is the same check.
    """
    rails = {}
    for name in siblings(restaurant.dir, restaurant.meta.get("projectRoot")):
        directory = (restaurant.dir.parent / name).resolve()
        rails[directory] = Restaurant(directory).rows("rail.tsv")
    return rails


def taken_row(rows, handoff):
    """The ticket filed for this handoff among these rail rows, if any."""
    if not rows:
        return None
    marker = f"(from {handoff})"
    return next((row for row in rows if row["source"].endswith(marker)), None)


def is_live(directory, row, rails):
    """waiting and assigned are live. moved is as live as the ticket it became. A handoff not yet taken is live."""
    if row["state"] != "moved":
        return row["state"] in LIVE_TICKET_STATES
    target = (directory.parent / row["dish"].removeprefix("to:")).resolve()
    if target in rails:
        rows = rails[target]
    elif not (target / "restaurant.json").is_file():
        return True
    else:
        rows = Restaurant(target).rows("rail.tsv")
    taken = taken_row(rows, handoff_id(directory, row["id"]))
    return taken is None or is_live(target, taken, rails)


def refuse_live_ref(restaurant, ref, rails):
    if not ref:
        return
    own = restaurant.dir.resolve()
    own_rows = restaurant.rows("rail.tsv")
    rails = {**rails, own: own_rows}
    stores = [(own, own_rows)]
    stores.extend((directory, rows) for directory, rows in rails.items() if directory != own)
    for directory, rows in stores:
        for row in rows:
            if row["ref"] == ref and is_live(directory, row, rails):
                where = "" if directory == own else f" in {directory.name}"
                raise BrigadeError(f"{ref} is already {row['id']}{where} ({row['state']}); nothing added")


def one_of(words):
    """`a, b, or c` from three or more words."""
    return ", ".join(words[:-1]) + f", or {words[-1]}"


@dataclass(frozen=True)
class Goal:
    """One row of goals.tsv as read_labels reads it.

    state is one of GOAL_STATES. share is the digits of a whole number of 1 or more with no leading zero, or "" for no limit.
    """
    id: str
    state: str
    name: str
    purpose: str
    done: str
    share: str
    closed: str


@dataclass(frozen=True)
class Label:
    """A ticket's or an item's kind and goal as read_labels reads them.

    kind is one of KINDS, or "" for no kind. goal is a key of Labels.goals, or "" for unassigned.
    """
    kind: str = ""
    goal: str = ""


@dataclass(frozen=True)
class Fault:
    """One warning of read_labels. table is the table the row is in, id is the row's id cell, and text is the warning."""
    table: str
    id: str
    text: str


@dataclass(frozen=True)
class ActsOn:
    """The rows of rail.tsv and dishes.tsv whose kind and goal a command acts on.

    read_labels prints the warnings of these rows. tickets holds ticket ids and items holds item ids.
    every is true for a command that lists or counts every row.
    """
    tickets: tuple = ()
    items: tuple = ()
    every: bool = False

    def covers(self, fault):
        """True when read_labels prints this fault. Every fault of goals.tsv is printed."""
        return (fault.table == "goals.tsv" or self.every
                or fault.id in {"rail.tsv": self.tickets, "dishes.tsv": self.items}[fault.table])


EVERY_ROW = ActsOn(every=True)
GOALS_ONLY = ActsOn()


@dataclass(frozen=True)
class Labels:
    """The kinds and goals of one store, read under one hold of the store lock.

    goals is {goal id: Goal} in file order. tickets is {ticket id: Label} for the rows of rail.tsv, and items is
    {item id: Label} for the rows of dishes.tsv. faults is one Fault per cell whose value is not legal,
    and one per id of goals.tsv with a row that is not a goal.
    """
    goals: dict
    tickets: dict
    items: dict
    faults: tuple


def whole_share(text):
    """The digits of a whole number of 1 or more with no leading zero, or "" when the text is not such a number."""
    return text.lstrip("0") if text.isascii() and text.isdigit() else ""


def read_goal(row, faults):
    state, share = row["state"], whole_share(row["share"])
    if state not in GOAL_STATES:
        faults.append(Fault("goals.tsv", row["id"], f"{row['id']} has state {state!r}, which is not {one_of(GOAL_STATES)}; "
                                                    f"it reads as open; run goal set {row['id']} --state <state>"))
        state = "open"
    if row["share"] and not share:
        faults.append(Fault("goals.tsv", row["id"], f"{row['id']} has share {row['share']!r}, which is not a whole number of "
                                                    f"1 or more; it reads as no limit; run goal set {row['id']} --share <n>"))
    return Goal(row["id"], state, row["name"], row["purpose"], row["done"], share, row["closed"])


def in_flight_tickets(restaurant):
    """{ticket id: the first row of dishes.tsv whose state is in IN_FLIGHT_STATES and whose tickets cell names the id}."""
    found = {}
    for item in restaurant.rows("dishes.tsv"):
        if item["state"] in IN_FLIGHT_STATES:
            for ident in item["tickets"].split(","):
                found.setdefault(ident, item)
    return found


def frozen_label(ticket, held):
    """Why ticket set does not change this ticket's kind and goal now, or "" when it does. `held` is in_flight_tickets().

    It changes them when the ticket is waiting and no item in flight names it. fire wrote an item's kind and goal when the
    item started, and no command writes the item's two cells after that.
    """
    if ticket["state"] != "waiting":
        return f"{ticket['id']} is {ticket['state']}"
    item = held.get(ticket["id"])
    return f"{ticket['id']} is a ticket of {item['id']}, which is {item['state']}" if item else ""


def read_label(table, row, goals, faults, by_hand):
    """One row's Label. `by_hand` is the end of the warning for a bad cell of a row whose cells ticket set does not change now.

    It is "" for any other row, and the warning then ends with the ticket set line to run.
    """
    ident = row["id"]
    kind = row["kind"] if row["kind"] in KINDS else ""
    goal = row["goal"] if row["goal"] in goals else ""
    if row["kind"] and not kind:
        faults.append(Fault(table, ident, f"{ident} has kind {row['kind']!r}, which is not {one_of(KINDS)}; it reads as no kind; "
                                          + (by_hand or f"run ticket set {ident} --kind <kind>")))
    if row["goal"] and not goal:
        faults.append(Fault(table, ident, f"{ident} names goal {row['goal']!r}, which has no row in goals.tsv; it reads as unassigned; "
                                          + (by_hand or f"run ticket set {ident} --goal G<n> or --no-goal")))
    return Label(kind, goal)


def read_labels(restaurant, acts_on):
    """The one reader of the kind and goal cells of rail.tsv and dishes.tsv, and of goals.tsv. It writes nothing to the store.

    It prints one warning line on standard error for each fault of goals.tsv and for each fault of a row in `acts_on`.
    A command prints a warning once, however many times it reads.
    Call it under the store lock. A goals.tsv that does not exist reads as no goals.
    A row of goals.tsv with an empty id is not a goal. Neither is a row whose id clean changes, which is an id with white
    space at an end or a carriage return in it. Of the other rows of goals.tsv with one id, the first is the goal.
    The rows with an empty id add one Fault between them. So do the rows with one id that are not a goal.
    A kind cell, a goal cell, and an id of goals.tsv are read as they are in the file, and stored writes each back as it
    is, so a rewrite changes the reading of none that the command did not set.
    A cell whose value is not legal reads as follows and adds one Fault.

    - A kind that is not one of KINDS reads as no kind.
    - A goal that is not the id of a goal reads as unassigned.
    - A goal's state that is not one of GOAL_STATES reads as open.
    - A goal's share that is not empty and not a whole number of 1 or more reads as no limit.
    """
    goals, faults, extra = {}, [], []
    for row in restaurant.rows("goals.tsv"):
        if row["id"] and clean(row["id"]) == row["id"] and row["id"] not in goals:
            goals[row["id"]] = read_goal(row, faults)
        elif row["id"] not in extra:
            extra.append(row["id"])
    for ident in extra:
        if not ident:
            text = "goals.tsv has a row with no id; it is not a goal; remove it from goals.tsv by hand"
        elif clean(ident) != ident:
            text = (f"goals.tsv has a row with id {ident!r}, which has white space at an end or a carriage return in it; "
                    "it is not a goal; correct the id in goals.tsv by hand to an id no other row has, or remove the row")
        else:
            text = (f"goals.tsv has more than one row with id {ident}; the first is the goal and the others are not read; "
                    "remove the others from goals.tsv by hand")
        faults.append(Fault("goals.tsv", ident, text))
    held, tickets = in_flight_tickets(restaurant), {}
    for row in restaurant.rows("rail.tsv"):
        frozen = frozen_label(row, held)
        tickets[row["id"]] = read_label("rail.tsv", row, goals, faults,
                                        frozen and f"ticket set does not change this cell while {frozen}; correct it in rail.tsv by hand")
    items = {row["id"]: read_label("dishes.tsv", row, goals, faults, "no command changes this cell; correct it in dishes.tsv by hand")
             for row in restaurant.rows("dishes.tsv")}
    for fault in faults:
        if acts_on.covers(fault):
            restaurant.warn(fault.text)
    return Labels(goals, tickets, items, tuple(faults))


@dataclass(frozen=True)
class Tally:
    """The work under one kind or one goal. waiting holds ticket ids. in_flight and merged hold item ids."""
    waiting: tuple = ()
    in_flight: tuple = ()
    merged: tuple = ()
    send_backs: int = 0


@dataclass(frozen=True)
class LabelCounts:
    """kinds is {kind: Tally} and goals is {goal id: Tally}. The key "" holds the work with no kind, and the unassigned work.

    riding is {goal id: ticket ids}. A key with no work is absent.
    """
    kinds: dict
    goals: dict
    riding: dict


def count_labels(restaurant, labels):
    """The counts by kind and by goal. Call it under the same hold of the store lock as the read_labels that made `labels`.

    Each row counts under its own kind and its own goal as `labels` gives them.

    - waiting. The rows of rail.tsv whose state is waiting.
    - in flight. The rows of dishes.tsv whose state is in IN_FLIGHT_STATES.
    - merged. The rows of dishes.tsv whose state is merged.
    - send-backs. The rows of pass.tsv whose verdict is send-back, whose member cell is empty, and whose dish cell is the id of
      a row of dishes.tsv. They count under that item's kind and goal, whatever the item's state.
    - riding, for a goal. The rows of rail.tsv whose state is assigned and whose goal is that goal, where the dish cell is not
      the id of an item under that goal.
    """
    kinds, goals, riding = {}, {}, {}

    def add(label, field, ident):
        for found, key in ((kinds, label.kind), (goals, label.goal)):
            found.setdefault(key, {"waiting": [], "in_flight": [], "merged": [], "send_backs": []})[field].append(ident)

    for row in restaurant.rows("rail.tsv"):
        label = labels.tickets[row["id"]]
        if row["state"] == "waiting":
            add(label, "waiting", row["id"])
        elif row["state"] == "assigned" and label.goal and labels.items.get(row["dish"], Label()).goal != label.goal:
            riding.setdefault(label.goal, []).append(row["id"])
    for row in restaurant.rows("dishes.tsv"):
        if row["state"] in IN_FLIGHT_STATES:
            add(labels.items[row["id"]], "in_flight", row["id"])
        elif row["state"] == "merged":
            add(labels.items[row["id"]], "merged", row["id"])
    for row in restaurant.rows("pass.tsv"):
        if row["verdict"] == "send-back" and not row["member"] and row["dish"] in labels.items:
            add(labels.items[row["dish"]], "send_backs", row["dish"])

    def tallies(found):
        return {key: Tally(tuple(lists["waiting"]), tuple(lists["in_flight"]), tuple(lists["merged"]), len(lists["send_backs"]))
                for key, lists in found.items()}

    return LabelCounts(tallies(kinds), tallies(goals), {goal: tuple(ids) for goal, ids in riding.items()})


def open_goal(labels, ident):
    """The goal a ticket is being given. Refuses an id with no row, and a goal that is done or dropped."""
    if not ident:
        raise BrigadeError('--goal "" names no goal; run goal list --all to see every goal')
    goal = labels.goals.get(ident)
    if goal is None:
        raise BrigadeError(f"no goal {ident}; run goal list --all to see every goal")
    if goal.state in CLOSED_GOAL_STATES:
        raise BrigadeError(f"{ident} is {goal.state}; a ticket takes an open or paused goal; "
                           f"run goal set {ident} --state open first, or pick another goal")
    return goal


def goal_share(text):
    """--share as the cell to store. Empty text stays empty. Refuses text that is not a whole number of 1 or more."""
    text = clean(text)
    if text and not whole_share(text):
        raise BrigadeError(f'--share takes a whole number of 1 or more, or "" for no limit, got {text!r}')
    return whole_share(text)


def add_goal(restaurant, name, purpose, done, share=""):
    """Append one open goal and log it. Returns the new id.

    Refuses when an open or paused goal has the same name by same_text, so a command run a second time adds no second goal.
    """
    name, purpose, done, share = clean(name), clean(purpose), clean(done), goal_share(share)
    if not (name and purpose and done):
        raise BrigadeError("goal add needs a nonempty --name, --purpose, and --done")
    for goal in read_labels(restaurant, GOALS_ONLY).goals.values():
        if goal.state not in CLOSED_GOAL_STATES and same_text(goal.name) == same_text(name):
            raise BrigadeError(f"same name as {goal.id} ({goal.state}); nothing added")
    ident = restaurant.next_id("goals.tsv")
    restaurant.append("goals.tsv", {"id": ident, "at": now(), "state": "open", "name": name, "purpose": purpose,
                                    "done": done, "share": share})
    restaurant.log("goal", ident, "open", name)
    return ident


def goal_work(counted, ident):
    """What keeps a goal from closing, as (words, advice) pairs. Empty when no ticket of the goal waits or rides along and no item is in flight."""
    tally = counted.goals.get(ident, Tally())
    held = ((tally.waiting, "waiting", "give each waiting ticket another goal with ticket set --goal, or none with --no-goal; "
             "a waiting ticket that an item in flight names keeps its goal until that item is merged or dropped"),
            (tally.in_flight, "in flight", "finish or drop each item in flight"),
            (counted.riding.get(ident, ()), "riding along", "finish or drop the item each of those tickets rides along in"))
    return [(f"{', '.join(ids)} {words}", advice) for ids, words, advice in held if ids]


def set_goal(restaurant, ident, name=None, purpose=None, done=None, share=None, state=None, evidence=None):
    """Change one goal. Returns one line such as `G1 paused; share 2`.

    GOAL_MOVES is the state machine. A state the goal's cell already holds is accepted and changes neither the state nor
    the closed cell. A state cell that is not one of GOAL_STATES reads as open, and --state open rewrites it.
    A move to done or dropped needs --evidence, and is refused while goal_work finds work under the goal.
    One update call writes every change. It logs a goal row when the state changed.
    """
    labels = read_labels(restaurant, EVERY_ROW if state in CLOSED_GOAL_STATES else GOALS_ONLY)
    goal = labels.goals.get(ident)
    if goal is None:
        raise BrigadeError(f"no goal {ident}; run goal list --all to see every goal")
    if all(value is None for value in (name, purpose, done, share, state)):
        raise BrigadeError("goal set needs --state, --name, --purpose, --done, or --share")
    for flag, value in (("--name", name), ("--purpose", purpose), ("--done", done)):
        if value is not None and not clean(value):
            raise BrigadeError(f"goal set {flag} takes text; a goal keeps a name, a purpose, and a done test")
    if evidence is not None and state not in CLOSED_GOAL_STATES:
        raise BrigadeError("--evidence goes only with --state done or --state dropped")
    if share is not None:
        share = goal_share(share)
    _, row = restaurant.find("goals.tsv", ident)
    moving = state is not None and state != row["state"]
    closing = moving and state in CLOSED_GOAL_STATES
    if moving and state != goal.state and state not in GOAL_MOVES[goal.state]:
        raise BrigadeError(f"{ident} is {goal.state}; a {goal.state} goal moves only to open; run goal set {ident} --state open first")
    if closing:
        if not clean(evidence):
            raise BrigadeError(f"--state {state} needs --evidence <one line>")
        work = goal_work(count_labels(restaurant, labels), ident)
        if work:
            raise BrigadeError(f"{ident} still has work: {'; '.join(words for words, _ in work)}; "
                               f"{'; '.join(advice for _, advice in work)}; then run this again")
    closed = None
    if closing:
        closed = f"{now()} {clean(evidence)}"
    elif moving and goal.state in CLOSED_GOAL_STATES:
        closed = ""
    said = []
    if state is not None:
        said.append(state if moving else f"already {state}")
    if name is not None:
        said.append(f"name {clean(name)}")
    if purpose is not None:
        said.append("purpose changed")
    if done is not None:
        said.append("done test changed")
    if share is not None:
        said.append(f"share {share}" if share else "no share")
    kept = clean(name) if name is not None else goal.name
    restaurant.update("goals.tsv", ident, "goal", note=f"{kept}: {clean(evidence)}" if closing else kept,
                      state=state, name=name, purpose=purpose, done=done, share=share, closed=closed)
    return f"{ident} {'; '.join(said)}"


def plural(count, word):
    """`1 send-back`, `0 send-backs`, `2 send-backs`."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def tally_words(tally):
    return (f"{len(tally.waiting)} waiting, {len(tally.in_flight)} in flight, {len(tally.merged)} merged, "
            f"{plural(tally.send_backs, 'send-back')}")


def goal_lines(labels, counted, every):
    """The text of goal list.

    A goal is listed when it is open or paused, or when it has a ticket waiting, an item in flight, or a ticket riding along.
    `every` lists each other goal too. The unassigned line is last.
    """
    lines = []
    for goal in labels.goals.values():
        tally, riding = counted.goals.get(goal.id, Tally()), counted.riding.get(goal.id, ())
        closed = goal.state in CLOSED_GOAL_STATES
        if closed and not every and not (tally.waiting or tally.in_flight or riding):
            continue
        words = tally_words(tally) + (f", {len(riding)} riding along" if riding else "") + (f", share {goal.share}" if goal.share else "")
        lines += [f"{goal.id} {goal.state} {goal.name}: {words}", f"  purpose: {goal.purpose}", f"  done when: {goal.done}"]
        if closed:
            lines.append(f"  closed: {goal.closed}")
    if not lines:
        lines.append("no goals" if every else "no open or paused goals")
    return "\n".join([*lines, f"unassigned: {tally_words(counted.goals.get('', Tally()))}"])


def label_words(label):
    """What a ticket line prints for a label. ` fix G1`, ` fix`, ` G1`, or nothing for no kind and no goal."""
    return "".join(f" {word}" for word in (label.kind, label.goal) if word)


def status_label_lines(labels, counted):
    """Up to three lines of status. The counts by kind, the counts by goal, and the waiting tickets with no kind.

        kinds: feature 1 waiting; fix 1 waiting, 1 in flight; upkeep 1 in flight
        goals: G1 2 waiting, 1 in flight; G2 paused, 1 in flight; unassigned 2 waiting, 1 in flight
        untagged: 1

    An entry holds the waiting count and the in flight count that are not 0, and is absent when both are 0.
    kinds has an entry per kind in KINDS order. An item in flight with no kind is absent from kinds.
    goals has an entry per goal in file order and then one for unassigned work. A goal that is not open has its state
    after its id. The line is absent when goals.tsv has no row.
    A line with no entry is absent, and so is untagged at 0.
    """
    def entry(name, tally):
        found = [f"{len(ids)} {word}" for ids, word in ((tally.waiting, "waiting"), (tally.in_flight, "in flight")) if ids]
        return [f"{name} {', '.join(found)}"] if found else []

    lines = []
    kinds = [text for kind in KINDS for text in entry(kind, counted.kinds.get(kind, Tally()))]
    if kinds:
        lines.append(f"kinds: {'; '.join(kinds)}")
    goals = [text for goal in labels.goals.values()
             for text in entry(goal.id if goal.state == "open" else f"{goal.id} {goal.state},", counted.goals.get(goal.id, Tally()))]
    if labels.goals:
        goals += entry("unassigned", counted.goals.get("", Tally()))
    if goals:
        lines.append(f"goals: {'; '.join(goals)}")
    untagged = len(counted.kinds.get("", Tally()).waiting)
    if untagged:
        lines.append(f"untagged: {untagged}")
    return lines


def add_ticket(restaurant, summary, source, ref, request="", rails=None, again=False, priority="", paths=(), decision=False,
               kind="", goal=None):
    source, ref, request = clean(source), clean(ref), clean(request)
    meta = restaurant.meta
    if request:
        # A replayed request file must not file a second ticket, whatever state the first reached.
        for row in restaurant.rows("rail.tsv"):
            if row["source"].endswith(f"(request {request})"):
                raise BrigadeError(f"request {request} is already {row['id']}; nothing added")
    if source != "user":
        owner = intake_owner(restaurant.dir, meta.get("projectRoot"), source)
        if owner:
            raise BrigadeError(f"{owner} owns intake from {source}; ask it to file this and move it here")
        if source not in (meta.get("intake") or []):
            raise BrigadeError(f"no coordinator owns intake from {source}; the one that reads it runs set --intake {source}")
    refuse_live_ref(restaurant, ref, rails or {})
    if source == "user" and not ref and not again:
        for row in restaurant.rows("rail.tsv"):
            if row["state"] in LIVE_TICKET_STATES and same_text(row["summary"]) == same_text(summary):
                raise BrigadeError(f"same text as {row['id']} ({row['state']}); nothing added; "
                                   "pass --again to file a second ticket")
    if request:
        source = f"{source} (request {request})"
    with restaurant.guarded():
        # The kind and goal checks run after the owner fence and after each refusal above, so a ticket add that was
        # refused before kinds and goals existed is refused with the same words.
        if not kind:
            raise BrigadeError(f"ticket add needs --kind {KIND_CHOICE}")
        if goal is not None:
            open_goal(read_labels(restaurant, GOALS_ONLY), goal)
        return append_ticket(restaurant, summary, source, ref, priority, paths, decision, kind, goal or "")


def append_ticket(restaurant, summary, source, ref, priority="", paths=(), decision=False, kind="", goal=""):
    ident = restaurant.next_id("rail.tsv")
    restaurant.append("rail.tsv", {"id": ident, "at": now(), "state": "waiting", "source": source,
                                   "ref": ref, "summary": summary, "priority": priority,
                                   "paths": ",".join(paths), "decision": "yes" if decision else "",
                                   "kind": kind, "goal": goal})
    restaurant.log("ticket", ident, "waiting", summary)
    return ident


def in_reports(restaurant, report, name):
    """True when report is the bare name, `reports/<name>`, or a path whose directory resolves to this store's reports/."""
    return report in (name, f"reports/{name}") or Path(report).parent.resolve() == restaurant.dir.resolve() / "reports"


# What reports/<name> is when it is not a regular file of this store. Each value finishes the words `reports/<name> `.
NOT_A_REPORT = {
    "link": "is a symbolic link",
    "missing": "does not exist",
    "other": "is not a regular file",
    "elsewhere": "resolves outside this store's reports/",
}
EDITS_SECTION = (2, "Edits")
EDIT_TITLE = re.compile(r"Edit (\S.*)")
HEADING = re.compile(r"(#{1,6}) (?!(?:.*[ \t])?#+$)(\S(?:.*\S)?)")
FENCE_OPEN = re.compile(r"(`{3,})[^`]*|(~{3,}).*")
FILE_LINE = re.compile(r"File: `([^` ][^`]*)`(?!`)")
LABELS = {"Old text:": "old", "New text:": "new"}
# The marks edit_mark gives the five parts of one edit that one_edit reads, in the only order it reads them.
EDIT_MARKS = ["file", "old", "fence", "new", "fence"]
BLANK = re.compile(r"[ \t]*")
# What a block quote, a list item, and indentation put in front of a line's text.
NESTED = re.compile(r"[ \t]*(?:(?:>|(?:[-+*]|[0-9]{1,9}[.)])(?=[ \t]))[ \t]*)*")
UNDERLINE = re.compile(NESTED.pattern + r"(?:=+|-+)[ \t]*")
# What starts a line outside a fence that HEADING does not match and report_parts refuses. Each key is a key of EDITS_REFUSED.
UNREAD = (
    ("fence", re.compile(r"(?![`~])" + NESTED.pattern + r"(?:`{3}|~{3})")),
    ("heading", re.compile(NESTED.pattern + r"#{1,6}(?:[ \t]|$)")),
    ("html", re.compile(r" {0,3}<[A-Za-z/!?]")),
)
# Why parse_edits refuses a review report's edits. Each value finishes the words `reports/<name>`.
EDITS_REFUSED = {
    "none": " holds no edit under a ## Edits heading",
    "return": " holds a carriage return",
    "nul": " holds a NUL character",
    "mark": " starts with a byte order mark",
    "twice": " holds {count} headings whose text is Edits",
    "fence": (": line {line} has three backticks or tildes after nothing but indentation, block quote marks, and list marks; "
              "start each fence at the first column"),
    "open": ": line {line} opens a fence that no later line closes",
    "heading": (": line {line} starts like a heading and is not one to six #, one space, and the text, from the first column "
                "with nothing after the text"),
    "html": ": line {line} starts, after at most three spaces, with < and then an ASCII letter, /, !, or ?",
    "underline": (": line {line} is only = or only - once its indentation, block quote marks, list marks, and trailing "
                  "spaces and tabs are removed, and the line above it is not empty and not only spaces and tabs"),
    "inside": ": line {line} is a heading in the ## Edits section that is neither ### Edit <n> nor a heading of one or two #",
    "above": ": line {line} starts a fenced block in the ## Edits section above its first ### Edit heading",
    "parts": (": Edit {number} must hold, once each and in this order, a File line, an Old text line, a fenced block, "
              "a New text line, and a fenced block"),
    "near": (": Edit {number} has a File, Old text, or New text line after whitespace, a block quote mark, or a list mark, "
             "or an Old text or New text line that ends in whitespace"),
    "malformed": (": line {line} of Edit {number} starts, once its indentation, block quote marks, list marks, and the "
                  "whitespace at its two ends are removed, with File: and a backtick; what follows must be a path that "
                  "holds no backtick and does not start with a space, then a backtick that no backtick follows directly"),
    "below": (": line {line} is a File, Old text, or New text line of Edit {number}; the line above it must be empty or only "
              "spaces and tabs, the edit's heading, the closing line of a fence, or another of those three lines"),
    "apart": (": line {line} opens a fenced block of Edit {number}; every line between an Old text or New text line and "
              "the fenced block below it must be empty or only spaces and tabs"),
    "empty": ": Edit {number} has an empty old text",
    "path": ": Edit {number} names {path}; a path is relative to the repository and has no .. part",
}
NEEDS_COMMANDS = ("edits verify needs a build command and a check command; "
                  "run set --build-command <command> --check-command <command>, with none for one the project lacks")
NO_COMMAND = "none"
# Why an edit cannot apply to the reviewed commit.
EDIT_FAILS = {
    "missing": "FAIL {path}: no regular file at {sha}",
    "count": "FAIL {path}: old text of Edit {number} occurs {count} times at {sha}",
    "overlap": "FAIL {path}: Edit {number} overlaps another edit",
}
# One line per compared path, keyed by whether the build changed the path in the scratch copy and whether the copy and the head agree at it.
PATH_LINES = {
    (False, True): "edited {path}",
    (True, True): "built {path}",
    (False, False): "FAIL {path}: differs from {sha} with the edits applied",
    (True, False): "FAIL {path}: differs from what the build writes",
}
# The kind and the execute bit of each tree mode that edits verify compares by content.
BLOB_MODES = {"100644": ("file", False), "100755": ("file", True), "120000": ("link", False)}
# What `close` advises for an entry of each kind it warns about. Each value finishes the warning line.
REPAIR = {
    "link": "remove the link and leave its target as it is",
    "other": "move it out of reports/",
    "elsewhere": "replace the symbolic link at reports/ with a directory and leave the link's target as it is",
}


def report_stat(restaurant, name):
    """(kind, status) of reports/<name>. The kind is `file` or a key of NOT_A_REPORT, and the status is None for `missing`.

    The entry is read once, with one lstat that follows no link at <name>. Whether it is there, a symbolic link, or a
    regular file comes from that status alone, and `elsewhere` adds where reports/ itself resolves.
    The first that holds wins. `missing` is a name with nothing there, which is that lstat finding no entry or finding
    that reports/ is not a directory. `link` is a symbolic link, whether or not its target exists. `other` is an entry
    that is not a regular file. `elsewhere` is a regular file under a reports/ that resolves to another place.
    """
    reports = restaurant.dir / "reports"
    try:
        status = (reports / name).lstat()
    except (FileNotFoundError, NotADirectoryError):
        return "missing", None
    if stat.S_ISLNK(status.st_mode):
        return "link", status
    if not stat.S_ISREG(status.st_mode):
        return "other", status
    if reports.resolve() != restaurant.dir.resolve() / "reports":
        return "elsewhere", status
    return "file", status


def report_entry(restaurant, name):
    """The kind report_stat gives reports/<name>."""
    return report_stat(restaurant, name)[0]


def item_report(restaurant, report):
    """The bare name of an item report under this store's reports/.

    Its content is read, so report_entry must call reports/<name> a `file`.
    A path that in_reports turns down is refused.
    A `..` component is refused as written, before anything is resolved.
    """
    if ".." in Path(report).parts:
        raise BrigadeError(f"{report} has a .. component; name a file like reports/D2.md")
    name = Path(report).name
    if not ITEM_REPORT.fullmatch(name):
        raise BrigadeError(f"{name} is not an item report; name a file like reports/D2.md")
    if not in_reports(restaurant, report, name):
        raise BrigadeError(f"{report} is outside this store's reports/; name reports/{name}")
    kind = report_entry(restaurant, name)
    if kind != "file":
        raise BrigadeError(f"reports/{name} {NOT_A_REPORT[kind]}; nothing added")
    return name


class EditsRefused(BrigadeError):
    """A refusal from parse_edits. Its text is a value of EDITS_REFUSED with its fields filled in."""


@dataclass(frozen=True)
class Edit:
    number: str
    path: str
    old: str
    new: str


def report_parts(lines):
    """A report's lines as (kind, value, number) parts. The number is that of the part's first line, counted from 1.

    A `fence` is the text between a fence's two lines.
    A fence opens at a line that starts at the first column with three or more backticks and holds no other backtick,
    or that starts there with three or more tildes.
    It closes at the next line that is at most three spaces, at least as many of the same character, and then only
    spaces and tabs.
    A `heading` is the (count of `#`, text) of a line outside a fence that HEADING matches in full. That is one to six `#`,
    one space, and a text with no whitespace at its two ends and no run of `#` at its end after a space or a tab.
    A `line` is any other line outside a fence.
    Raises EditsRefused for a fence that no later line closes, and for the first `line` that an UNREAD pattern matches at
    its start or that UNDERLINE matches in full directly below a `line` that BLANK does not match in full.
    """
    parts, index = [], 0
    while index < len(lines):
        line, number = lines[index], index + 1
        opened = FENCE_OPEN.fullmatch(line)
        if opened:
            run = opened.group(1) or opened.group(2)
            closing = re.compile(f" {{0,3}}{run[0]}{{{len(run)},}}[ \t]*")
            close = next((at for at in range(index + 1, len(lines)) if closing.fullmatch(lines[at])), None)
            if close is None:
                raise EditsRefused(EDITS_REFUSED["open"].format(line=number))
            parts.append(("fence", "\n".join(lines[index + 1:close]), number))
            index = close + 1
            continue
        heading = HEADING.fullmatch(line)
        if heading:
            parts.append(("heading", (len(heading.group(1)), heading.group(2)), number))
        else:
            unread = next((key for key, pattern in UNREAD if pattern.match(line)), None)
            if not unread and UNDERLINE.fullmatch(line) and parts and parts[-1][0] == "line" and not BLANK.fullmatch(parts[-1][1]):
                unread = "underline"
            if unread:
                raise EditsRefused(EDITS_REFUSED[unread].format(line=number))
            parts.append(("line", line, number))
        index += 1
    return parts


def edit_mark(kind, value):
    """What one part of an edit is to one_edit.

    A fence is `fence`. A line that FILE_LINE matches at its start is `file`. That is `File: `, a backtick, a path, and a
    backtick that no backtick follows directly, where the path holds no backtick and does not start with a space.
    A line that is `Old text:` is `old`, and one that is `New text:` is `new`. A line that BLANK matches in full is `blank`.
    Another line that is `file`, `old`, or `new` once what NESTED matches at its start and then the whitespace at its
    two ends are removed is `unread`. Another line that then starts with `File: ` and a backtick is `malformed`.
    Every line left is None.
    """
    if kind == "fence":
        return "fence"
    if FILE_LINE.match(value):
        return "file"
    if value in LABELS:
        return LABELS[value]
    if BLANK.fullmatch(value):
        return "blank"
    text = value[NESTED.match(value).end():].strip()
    if FILE_LINE.match(text) or text in LABELS:
        return "unread"
    return "malformed" if text.startswith("File: `") else None


def one_edit(number, parts):
    """The Edit of the (kind, value, line number) parts between an edit's heading and the next heading.

    Raises EditsRefused for a part that edit_mark calls `unread`, and for the first part that it calls `malformed`.
    Raises it unless the parts that edit_mark calls `file`, `old`, `new`, or `fence` are, in this order, one `file`,
    one `old`, one `fence`, one `new`, and one `fence`.
    Raises it for the first part that is a `file`, `old`, or `new` directly below a part that edit_mark calls None, or
    a `fence` whose nearest part above it that is not `blank` is neither `old` nor `new`.
    """
    marks = [edit_mark(kind, value) for kind, value, _ in parts]
    if "unread" in marks:
        raise EditsRefused(EDITS_REFUSED["near"].format(number=number))
    if "malformed" in marks:
        raise EditsRefused(EDITS_REFUSED["malformed"].format(line=parts[marks.index("malformed")][2], number=number))
    if [mark for mark in marks if mark in EDIT_MARKS] != EDIT_MARKS:
        raise EditsRefused(EDITS_REFUSED["parts"].format(number=number))
    above = nearest = "heading"
    for mark, (_, _, line) in zip(marks, parts):
        if mark in ("file", "old", "new") and above is None:
            raise EditsRefused(EDITS_REFUSED["below"].format(line=line, number=number))
        if mark == "fence" and nearest not in ("old", "new"):
            raise EditsRefused(EDITS_REFUSED["apart"].format(line=line, number=number))
        above = mark
        nearest = nearest if mark == "blank" else mark
    marked = [value for mark, (_, value, _) in zip(marks, parts) if mark in EDIT_MARKS]
    path, old, new = FILE_LINE.match(marked[0]).group(1), marked[2], marked[4]
    if not old:
        raise EditsRefused(EDITS_REFUSED["empty"].format(number=number))
    if path.startswith("/") or ".." in PurePosixPath(path).parts:
        raise EditsRefused(EDITS_REFUSED["path"].format(number=number, path=path))
    return Edit(number, posixpath.normpath(path), old, new)


def parse_edits(text):
    """The edits of a review report's `## Edits` section, in the report's order.

    The section starts after the report's one heading whose text is `Edits`, when that heading has two `#`.
    It ends before the next heading of one or two `#`, or where the report's parts end.
    In it a heading of three `#` whose text is `Edit <n>` starts an edit, and the edit ends before the next heading.
    The `<n>` is any text that starts with a character other than whitespace.
    Raises EditsRefused for a carriage return, for a NUL character, for a byte order mark as the first character,
    for what report_parts refuses, for a second heading whose text is `Edits`, for any other heading in the section,
    for a fence in the section above its first edit, for a section with no edit, and for the first edit that one_edit
    refuses.
    """
    for key, found in (("return", "\r" in text), ("nul", "\0" in text), ("mark", text.startswith("\ufeff"))):
        if found:
            raise EditsRefused(EDITS_REFUSED[key])
    parts = report_parts(text.split("\n"))
    named = sum(kind == "heading" and value[1] == EDITS_SECTION[1] for kind, value, _ in parts)
    if named > 1:
        raise EditsRefused(EDITS_REFUSED["twice"].format(count=named))
    section, edits = iter(parts), []
    if any((kind, value) == ("heading", EDITS_SECTION) for kind, value, _ in section):
        for kind, value, number in section:
            if kind == "heading":
                level, title = value
                if level < 3:
                    break
                numbered = EDIT_TITLE.fullmatch(title) if level == 3 else None
                if not numbered:
                    raise EditsRefused(EDITS_REFUSED["inside"].format(line=number))
                edits.append((numbered.group(1), []))
            elif edits:
                edits[-1][1].append((kind, value, number))
            elif kind == "fence":
                raise EditsRefused(EDITS_REFUSED["above"].format(line=number))
    if not edits:
        raise EditsRefused(EDITS_REFUSED["none"])
    return tuple(one_edit(number, parts) for number, parts in edits)


def report_edits(restaurant, name):
    """The edits of reports/<name>.

    Refuses a report that cannot be read and one that is not UTF-8. The caller has checked that the entry is a `file`.
    """
    try:
        return parse_edits((restaurant.dir / "reports" / name).read_bytes().decode("utf-8"))
    except OSError as error:
        raise BrigadeError(f"reports/{name} cannot be read: {error.strerror}") from None
    except UnicodeDecodeError:
        raise BrigadeError(f"reports/{name} is not UTF-8") from None
    except EditsRefused as error:
        raise BrigadeError(f"reports/{name}{error}") from None


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    oid: str


@dataclass(frozen=True)
class Digest:
    """What edits verify compares at one path. The kind is `file`, `link`, or `other`, and a link's hash is of its target."""
    kind: str
    sha256: str
    executable: bool


def occurrences(content, old):
    """The offset of every place old starts in content. Places that overlap each count."""
    found, at = [], content.find(old)
    while at != -1:
        found.append(at)
        at = content.find(old, at + 1)
    return found


def apply_edits(contents, edits, sha):
    """({path: new bytes}, lines). A run with any line changes no path.

    contents holds, for each path an edit names, the file's bytes at the reviewed commit, or None when no file is there.
    Every old text is placed in those bytes, never in the result of another edit.
    """
    found = [None if contents[edit.path] is None else occurrences(contents[edit.path], edit.old.encode()) for edit in edits]
    spans = [(edit.path, starts[0], starts[0] + len(edit.old.encode())) if starts is not None and len(starts) == 1 else None
             for edit, starts in zip(edits, found)]
    lines = []
    for index, (edit, starts, span) in enumerate(zip(edits, found, spans)):
        if starts is None:
            case = "missing"
        elif span is None:
            case = "count"
        elif any(other is not None and at != index and other[0] == span[0] and other[1] < span[2] and span[1] < other[2]
                 for at, other in enumerate(spans)):
            case = "overlap"
        else:
            continue
        line = EDIT_FAILS[case].format(path=edit.path, number=edit.number, count=len(starts or ()), sha=sha)
        if line not in lines:
            lines.append(line)
    if lines:
        return {}, lines
    changed = {}
    for path in {edit.path for edit in edits}:
        pieces, at = [], 0
        placed = sorted(((span[1], span[2], edit.new.encode()) for edit, span in zip(edits, spans) if edit.path == path),
                        key=lambda piece: piece[0])
        for start, end, new in placed:
            pieces += [contents[path][at:start], new]
            at = end
        changed[path] = b"".join(pieces) + contents[path][at:]
    return changed, []


def compared_paths(before, after, extracted, edited, built):
    """The paths either commit's tree holds where the two trees differ or the scratch copy changed in the edits or the build, sorted."""
    return sorted(path for path in before.keys() | after.keys()
                  if before.get(path) != after.get(path) or extracted.get(path) != edited.get(path)
                  or edited.get(path) != built.get(path))


def path_lines(paths, edited, built, head, sha):
    """One PATH_LINES line per path, and whether the scratch copy and the head agree at every path."""
    agree = [built.get(path) == head.get(path) for path in paths]
    lines = [PATH_LINES[edited.get(path) != built.get(path), same].format(path=path, sha=sha) for path, same in zip(paths, agree)]
    return lines, all(agree)


@dataclass(frozen=True)
class FollowUps:
    """What a report's follow-ups sections hold. A report with no such section is None, not this.

    Each of `items` becomes one ticket, and its position plus one is the `#<n>` in its ref.
    `asides` are the (reason, text) pairs the command prints and never files.
    """
    items: tuple
    asides: tuple


# A block that is one of these and nothing more says the report has no follow-up. Every longer block is read as work.
NO_WORK = ("none", "none required", "none needed", "nothing", "nothing needed", "n/a", "not applicable", "no follow-ups")


def says_no_work(text):
    """True when the block is one of NO_WORK and nothing more.

    Case does not count. Neither do list markers, the `*`, `_`, and backtick marks around it, or a final period.
    """
    text = re.sub(r"^(?:(?:[-*+]|\d+[.)])\s+)+", "", text.strip())
    return text.strip("*_` ").removesuffix(".").strip("*_` ").casefold() in NO_WORK


def follow_ups(text):
    """Every follow-ups section of a report, or None when no heading says follow-ups.

    A top-level list item or a paragraph is one block, and the indented lines, nested bullets, and fenced code under it
    are part of it. Each block is a follow-up, with three exceptions that are asides. A block that is one of NO_WORK
    and nothing more is one. So is a paragraph directly above a list item, which introduces the list, and a paragraph
    below its section's last list item, which closes the section.

    The text that follows the word on a follow-ups heading line is an aside too, whatever it says. It is the rest of the
    line without leading colons and surrounding whitespace.

    The text of a heading deeper than the open follow-ups heading that does not itself say follow-ups is an aside too,
    whatever it says. It is the line after its opening `#` marks, without surrounding whitespace. Such a heading with no
    text is an aside with no text. A paragraph with only such headings between it and a list item is directly above that item.

    A list item with no text is an aside with no text. It is not the list item a paragraph is directly above, and it is
    not its section's last list item. A paragraph looks past it as it looks past such a heading.
    """
    sections, level, fenced, blank, block = [], 0, False, True, None
    for line in text.splitlines():
        fence = line.strip().startswith("```")
        plain = not fence and not fenced
        fenced = fenced != fence
        heading = plain and re.match(r"(#{1,6})(\s|$)", line)
        if heading:
            depth = len(heading.group(1))
            named = re.match(r"#{1,6}\s+follow-?ups?\b", line, re.I)
            if named:
                level = depth
                sections.append([("heading", [re.sub(r"^[\s:]+", "", line[named.end():])])])
            elif depth <= level:
                level = 0
            elif level:
                sections[-1].append(("subheading", [line[depth:]]))
            block, blank = None, True
        elif not level:
            continue
        elif not line.strip():
            blank = plain
        else:
            marker = plain and re.match(r"(?:[-*+]|\d+[.)])(?:\s+|$)", line)
            if marker or block is None or plain and blank and not line[0].isspace():
                block = ("item" if marker else "para", [])
                sections[-1].append(block)
            block[1].append(line[marker.end():] if marker else line)
            blank = False
    if not sections:
        return None
    items, asides = [], []
    for blocks in sections:
        blocks = [(kind, " ".join(part.strip() for part in lines).strip()) for kind, lines in blocks]
        blocks = [("bare" if kind == "item" and not text else kind, text) for kind, text in blocks]
        blocks = [(kind, text) for kind, text in blocks if text or kind in ("subheading", "bare")]
        kinds = [kind for kind, _ in blocks] + ["end"]
        last = max((index for index, kind in enumerate(kinds) if kind == "item"), default=len(kinds))
        for index, (kind, text) in enumerate(blocks):
            if kind == "heading":
                asides.append(("text on the heading line", text))
            elif kind == "subheading":
                asides.append(("heading inside the section" if text else "heading inside the section with no text", text))
            elif kind == "bare":
                asides.append(("list item with no text", ""))
            elif says_no_work(text):
                asides.append(("says no work is needed", text))
            elif kind == "para" and next(below for below in kinds[index + 1:] if below not in ("subheading", "bare")) == "item":
                asides.append(("prose that introduces a list", text))
            elif kind == "para" and index > last:
                asides.append(("prose after the last list item", text))
            else:
                items.append(text)
    return FollowUps(tuple(items), tuple(asides))


def same_text(value):
    return " ".join(value.split()).casefold()


def follow_up_kind(item_kind, priority):
    """The kind of a follow-up ticket. No kind when its item has none. Else fix at priority urgent or normal, and polish at low."""
    if not item_kind:
        return ""
    return "polish" if priority == "low" else "fix"


def file_follow_ups(restaurant, name, found, write, tracked):
    """One waiting ticket per follow-up whose text no ticket of this store holds, in any state.

    A done or dropped ticket counts, so a rerun on the same report after the coordinator dropped one files nothing.
    The ref ends in a position, which is not an identity, so the text is the only key and refuse_live_ref is not asked.
    Each ticket takes the goal of the report's item, whatever that goal's state, and follow_up_kind of that item's kind.
    """
    restaurant.find("dishes.tsv", name[:-3])
    label = read_labels(restaurant, ActsOn(items=(name[:-3],))).items[name[:-3]]
    kind = follow_up_kind(label.kind, priority_of({"priority": "", "source": "report"}))
    if found is None:
        return f"reports/{name} has no follow-ups section; nothing added"
    aside = [f"not filed, {reason}: {text}" if text else f"not filed, {reason}" for reason, text in found.asides]
    if not found.items:
        return "\n".join([f"reports/{name} lists no follow-ups; nothing added", *aside])
    holders = {}
    for row in sorted(restaurant.rows("rail.tsv"), key=lambda row: row["state"] not in LIVE_TICKET_STATES):
        holders.setdefault(same_text(row["summary"]), f"{row['id']} ({row['state']})")
    lines = []
    for number, text in enumerate(found.items, 1):
        ref = f"{restaurant.dir.name}/reports/{name}#{number}"
        key = same_text(text)
        if key in holders:
            lines.append(f"skipped {ref}, same text as {holders[key]}: {text}")
        elif write:
            ident = append_ticket(restaurant, text, "report", ref, paths=quoted_paths(text, tracked), kind=kind, goal=label.goal)
            holders[key] = f"{ident} (waiting)"
            lines.append(f"{ident} added from {ref}: {text}")
        else:
            holders[key] = f"#{number} above"
            lines.append(f"would add from {ref}: {text}")
    return "\n".join(lines + aside)


def sibling_named(restaurant, to):
    """A sibling's directory name and restaurant.json, found by directory name or restaurant name."""
    meta = restaurant.meta
    names = siblings(restaurant.dir, meta.get("projectRoot"))
    name = to if to in names else next((n for n in names if slug(names[n].get("restaurant") or n) == slug(to)), None)
    if name is None:
        raise BrigadeError(f"{to} is not a sibling coordinator on {meta.get('projectRoot')}")
    return name, names[name]


def tell(name, meta):
    thread = (meta.get("thread") or "").strip()
    return f"tell thread {thread}" if thread else f"no thread recorded for {name}"


def move_ticket(restaurant, ident, to, rails):
    name, sibling = sibling_named(restaurant, to)
    target = (restaurant.dir.parent / name).resolve()
    destination = f"to:{name}"
    rows, row = restaurant.find("rail.tsv", ident)
    if row["state"] == "moved" and row["dish"] != destination:
        raise BrigadeError(f"{ident} is already moved to {row['dish'].removeprefix('to:')}")
    if row["state"] not in ("waiting", "moved"):
        raise BrigadeError(f"{ident} is {row['state']}; only a waiting ticket moves")
    if row["state"] == "waiting":
        # State and destination commit in one rewrite, so the ref stays live here from this point on.
        row["state"], row["dish"] = "moved", destination
        restaurant.save_rows("rail.tsv", rows)
    if not any(event["kind"] == "ticket" and event["id"] == ident and event["state"] == "moved"
               for event in restaurant.rows("log.tsv")):
        restaurant.log("ticket", ident, "moved", f"{row['summary']} (to {name})")
    handoff = handoff_id(restaurant.dir, ident)
    target_rows = rails.get(target)
    if target_rows is None:
        target_rows = Restaurant(target).rows("rail.tsv")
    # The kind goes with the ticket. The goal stays, because a goal id names a row of this store's goals.tsv only.
    label = read_labels(restaurant, ActsOn(tickets=(ident,))).tickets[ident]
    if taken_row(target_rows, handoff) is None:
        restaurant.publish(inbox_file(target, handoff), json.dumps({
            "handoff": handoff, "summary": row["summary"], "source": base_source(row["source"]), "ref": row["ref"],
            "priority": row["priority"], "paths": row["paths"], "decision": row["decision"],
            **({"kind": label.kind} if label.kind else {}),
        }, indent=2) + "\n")
    left = f"; it leaves {label.goal} here and arrives unassigned" if label.goal else ""
    return f"{ident} moved to {name}; {tell(name, sibling)}{left}"


def take_tickets(restaurant):
    lines = []
    for path in sorted((restaurant.dir / "inbox").glob("*.json")):
        try:
            handoff = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            raise BrigadeError(f"{path} is not valid JSON: {error}") from error
        source = handoff["handoff"]
        row = taken_row(restaurant.rows("rail.tsv"), source)
        if row is None:
            rows = restaurant.rows("rail.tsv")
            given = handoff.get("kind") or ""
            kind = given if given in KINDS else ""
            row = {"id": restaurant.next_id("rail.tsv"), "at": now(), "state": "waiting",
                   "source": f"{handoff['source']} (from {source})", "ref": handoff["ref"], "summary": handoff["summary"],
                   **{key: handoff.get(key, "") for key in ("priority", "paths", "decision")}, "kind": kind}
            if given and not kind:
                # The warning comes before the write, so a take killed after the write has printed it.
                restaurant.warn(f"{row['id']} came from {source} with kind {given!r}, which is not one this version knows, "
                                f"so it has no kind; run ticket set {row['id']} --kind {KIND_CHOICE}")
            restaurant.save_rows("rail.tsv", rows + [row])
        if not any(event["kind"] == "ticket" and event["note"] == f"from {source}" for event in restaurant.rows("log.tsv")):
            restaurant.log("ticket", row["id"], "waiting", f"from {source}")
        restaurant.remove(path)
        lines.append(f"{row['id']} from {source}: {row['summary']}")
    return lines


def request_files(restaurant):
    """Request files the executive admin published into this inbox, oldest first, as (pending, finished).

    A file whose id has an inbox-done row is finished. request --republish reads completions before
    the admin's lock, so it can publish a request inbox done finished meanwhile. Nothing counts that file.
    """
    found = sorted((path for path in (restaurant.dir / "inbox").glob("A*.line") if re.fullmatch(r"A\d+", path.stem)),
                   key=lambda path: int(path.stem[1:]))
    finished = finished_requests(restaurant)
    return [path for path in found if path.stem not in finished], [path for path in found if path.stem in finished]


def finished_requests(restaurant):
    return {row["id"] for row in restaurant.rows("log.tsv") if row["kind"] == "inbox-done"}


def take_inbox(restaurant):
    """File handed tickets, then print each request waiting for inbox done. A finished request's file is deleted."""
    lines = take_tickets(restaurant)
    pending, finished = request_files(restaurant)
    for path in finished:
        restaurant.remove(path)
    lines.extend(f"{path.stem}: {path.read_text().strip()}" for path in pending)
    return "\n".join(lines) or NOTHING_HANDED


def finish_request(restaurant, ident):
    """Record the request as acted on, then delete its file. A rerun after a crash between the two finishes the delete."""
    path = restaurant.dir / "inbox" / f"{ident}.line"
    if ident not in finished_requests(restaurant):
        if not path.exists():
            raise BrigadeError(f"no request {ident} in the inbox")
        restaurant.log("inbox-done", ident, "done", path.read_text().strip())
    if path.exists():
        restaurant.remove(path)
    return f"{ident} done"


def handed_count(restaurant):
    return len(list((restaurant.dir / "inbox").glob("*.json")))


def open_restaurant(root, project_root, name, reporting="milestones", intake=(), workers=None, admin=False, mode=None):
    project_root = Path(project_root).resolve()
    if admin:
        name = ADMIN_NAME
    directory = root / slug(project_root.name) / (ADMIN_DIR if admin else slug(name))
    # The project directory is shared by every coordinator on this path slug.
    # The coordinator directory is the claim, so mkdir must fail when it exists.
    directory.parent.mkdir(parents=True, exist_ok=True)
    try:
        directory.mkdir()
    except FileExistsError:
        created = False
    else:
        created = True
    if not created:
        meta = wait_for_meta(directory)
        if meta.get("projectRoot") != str(project_root):
            other = meta.get("projectRoot")
            raise BrigadeError(f"{directory} already holds a coordinator for {other}; pick another --name")
        return Restaurant(directory), False
    try:
        refuse_owned_intake(directory, str(project_root), intake)
    except BrigadeError:
        directory.rmdir()
        raise
    for filename, template in (("menu.md", ADMIN_MENU if admin else MENU), ("house-rules.md", HOUSE_RULES)):
        write_atomic(directory / filename, template.format(restaurant=name))
    for table in TABLES:
        if table == "rulings.tsv" and not admin:
            continue
        header = TABLES[table]
        write_atomic(directory / table, "\t".join(header) + "\n")
    meta = {"restaurant": name, "projectRoot": str(project_root),
            "reporting": reporting, "openedAt": now(), "lastActivityAt": now(),
            "lastReportAt": None, "thread": None, "schedules": {}, "intake": list(intake)}
    if workers is not None:
        meta["workers"] = workers
    if mode is not None:
        meta["mode"] = mode
    if admin:
        meta["role"] = "admin"
    write_atomic(directory / "restaurant.json", json.dumps(meta, indent=2) + "\n")
    return Restaurant(directory), True


def counts(restaurant):
    tickets = [row["state"] for row in restaurant.rows("rail.tsv")]
    dishes = [row["state"] for row in restaurant.rows("dishes.tsv")]
    questions = [row for row in restaurant.rows("86.tsv") if row["state"] == "open"]
    return {
        "waiting tickets": tickets.count("waiting"),
        "in progress": dishes.count("in-progress"),
        "in review": dishes.count("in-review"),
        "passed review": dishes.count("passed"),
        "waiting to land": dishes.count("queued"),
        "sent back": dishes.count("sent-back"),
        "blocked": dishes.count("blocked"),
        "merged": dishes.count("merged"),
        "decisions for you": len(questions),
        "handed to you": handed_count(restaurant),
        "requests from the executive admin": len(request_files(restaurant)[0]),
    }


def status_line(restaurant):
    return ", ".join(f"{label}: {value}" for label, value in counts(restaurant).items() if value) or "nothing on record"


@functools.cache
def roles_family():
    """roles.py's family, loaded in process.

    pass record asks under the store lock, which forbids a subprocess.
    runpy writes no __pycache__ under skills/.
    """
    return runpy.run_path(str(roles_script()))["family"]


def cross_family(row):
    family = roles_family()
    return family(row["author"]) != family(row["verifier"]) and not row["note"].startswith("same model family")


def item_verdicts(rows, dish_id):
    """The rows that carry the item's verdict. A panel member's row is on record and decides nothing."""
    return [row for row in rows if row["dish"] == dish_id and not row["member"]]


def send_backs(rows, dish_id):
    return [row for row in item_verdicts(rows, dish_id) if row["verdict"] == "send-back"]


def round_count(restaurant, dish_id):
    """The item's send-backs written after the later of its last passing verdict and its last answered round decision.

    Only a row `86 add --round-budget` wrote is a round decision. Another decision's answer never settles the count.
    """
    verdicts = item_verdicts(restaurant.rows("pass.tsv"), dish_id)
    settled = [row["at"] for row in verdicts if row["verdict"] == "pass"]
    settled += [row["answered"] for row in restaurant.rows("86.tsv")
                if row["dish"] == dish_id and row["kind"] == ROUND_KIND and row["state"] == "answered"]
    since = max(settled, default="")
    return sum(row["verdict"] == "send-back" and row["at"] > since for row in verdicts)


def round_debt(restaurant, dish):
    """The item's round count when it owes its round decision, else 0.

    The budget stops another fix round, so only an item that could get one owes.
    """
    if dish["state"] not in OWING_STATES:
        return 0
    count = round_count(restaurant, dish["id"])
    return count if count >= ROUND_BUDGET else 0


def fix_round_refusal(restaurant, dish):
    """Why the item gets no fix round now, or None."""
    debt = round_debt(restaurant, dish)
    if not debt:
        return None
    return (f"{dish['id']} has {debt} send-backs; no fix round until its decision is answered; "
            f"run 86 add --dish {dish['id']} --round-budget")


def round_decision(restaurant, dish_id):
    """The question, options, and default of the decision an item owes at ROUND_BUDGET send-backs."""
    _, dish = restaurant.find("dishes.tsv", dish_id)
    if dish["state"] not in OWING_STATES:
        raise BrigadeError(f"{dish_id} is {dish['state']}; it owes no round decision")
    count = round_count(restaurant, dish_id)
    if count < ROUND_BUDGET:
        raise BrigadeError(f"{dish_id} has {count} send-back{'' if count == 1 else 's'} since its last pass or round decision; "
                           f"the decision opens at {ROUND_BUDGET}")
    question = (f"{dish_id} was sent back {count} times. Accept the remaining findings as known limits and land it, "
                "send it through architect for a redesign, or drop it?")
    report = send_backs(restaurant.rows("pass.tsv"), dish_id)[-1]["report"]
    if report:
        question += f" Findings: reports/{report}."
    return question, ", ".join((*ROUND_DECISIONS, ROUND_PARKED)), ROUND_PARKED


def latest_verdict(restaurant, dish_id, sha):
    restaurant.find("dishes.tsv", dish_id)
    verdicts = [row for row in item_verdicts(restaurant.rows("pass.tsv"), dish_id) if row["sha"] == sha]
    return verdicts[-1] if verdicts else None


def pass_json(row):
    return json.dumps({"verdict": row["verdict"], "author": row["author"], "verifier": row["verifier"],
                       "note": row["note"], "crossFamily": cross_family(row)}, indent=2)


def pass_check(restaurant, dish_id, sha):
    latest = latest_verdict(restaurant, dish_id, sha)
    if latest is None:
        return False, f"{dish_id} has no review verdict for {sha}"
    if latest["verdict"] != "pass":
        return False, f"{dish_id} at {sha}: {latest['verdict']} ({latest['note'] or 'no note'})"
    return True, f"{dish_id} at {sha} passed review by {latest['verifier']}"


def review_report(restaurant, dish_id, report):
    """The bare name of `report`.

    A name that is not a review report of the item is refused. So is a path that in_reports turns down, and so is a
    name for which report_entry does not call reports/<name> a `file`.
    """
    name = Path(report).name
    match = REVIEW_FILE.fullmatch(name)
    if not match or match.group(1) != dish_id:
        raise BrigadeError(f"{name} is not a review report of {dish_id}; name a file like reports/{dish_id}-review-1.md")
    if not in_reports(restaurant, report, name):
        raise BrigadeError(f"{report} is outside this store's reports/; name reports/{name}")
    kind = report_entry(restaurant, name)
    if kind == "missing":
        raise BrigadeError(f"reports/{name} does not exist; write the review report first")
    if kind != "file":
        raise BrigadeError(f"reports/{name} {NOT_A_REPORT[kind]}; nothing recorded")
    return name


def record_pass(restaurant, dish_id, pr, sha, verdict, author, verifier, note="", same_family=False, report="",
                member=False, late=False, edits=""):
    _, dish = restaurant.find("dishes.tsv", dish_id)
    if verdict not in VERDICTS:
        raise BrigadeError(f"verdict must be one of {', '.join(VERDICTS)}")
    if roles_family()(author) == roles_family()(verifier) and not same_family:
        raise BrigadeError(f"verifier {verifier} is the same model family as author {author}; "
                           "pick a verifier from another family, or pass --same-family when no other family is runnable")
    if same_family:
        note = clean(f"same model family; {note}")
    if verdict == EDITS_VERDICT:
        if not edits:
            raise BrigadeError(f"--verdict {EDITS_VERDICT} needs --edits <review report>")
        if report:
            raise BrigadeError("--edits names the round's report; drop --report")
        report = edits
    elif edits:
        raise BrigadeError(f"--edits goes only with --verdict {EDITS_VERDICT}")
    if report:
        report = review_report(restaurant, dish_id, report)
    if verdict == EDITS_VERDICT:
        report_edits(restaurant, report)
    if verdict == "pass" and not member:
        edits_gate(restaurant, dish_id, sha, report)
    restaurant.append("pass.tsv", {"at": now(), "dish": dish_id, "pr": pr, "sha": sha, "verdict": verdict,
                                   "author": author, "verifier": verifier, "note": note, "report": report,
                                   "member": "yes" if member else ""})
    if member:
        return f"{dish_id}: member {verdict} on record; {dish_id} stays {dish['state']}"
    if late:
        printed = f"{dish_id}: late {verdict} on record; {dish_id} stays {dish['state']}"
    else:
        dish = restaurant.update("dishes.tsv", dish_id, "dish", state=VERDICTS[verdict], pr=pr, sha=sha)
        printed = f"{dish_id} {dish['state']}"
    debt = round_debt(restaurant, dish) if verdict == "send-back" else 0
    if debt:
        return f"{printed}; {debt} send-backs, decision pending"
    return printed


def edits_gate(restaurant, dish_id, sha, report):
    """Refuse a pass while the item's last verdict row is pass-with-edits.

    Two things lift it. One is an edits.tsv row for that round's sha, this sha, and that round's report.
    The other is a `report` that is not that round's report.
    """
    verdicts = item_verdicts(restaurant.rows("pass.tsv"), dish_id)
    if not verdicts or verdicts[-1]["verdict"] != EDITS_VERDICT:
        return
    last = verdicts[-1]
    if any(row["dish"] == dish_id and (row["from"], row["to"], row["report"]) == (last["sha"], clean(sha), last["report"])
           for row in restaurant.rows("edits.tsv")):
        return
    if report and report != last["report"]:
        return
    raise BrigadeError(f"{dish_id}'s last verdict is {EDITS_VERDICT} at {last['sha']}; run edits verify {dish_id} "
                       f"--from {last['sha']} --to {sha}, or name a second review's report with --report")


def command_words(command):
    try:
        return shlex.split(command)
    except ValueError as error:
        raise BrigadeError(f"{command!r} is not a command line: {error}") from None


def git(project_root, *words):
    return subprocess.run(["git", "-C", project_root, *words], capture_output=True)


def commit_tree(project_root, sha):
    """(the commit's full id, {path: TreeEntry}) of a commit in the project's repository."""
    found = git(project_root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{sha}^{{commit}}")
    if found.returncode:
        raise BrigadeError(f"{sha} is not a commit in {project_root}")
    commit = found.stdout.decode().strip()
    if not commit.startswith(sha.lower()):
        raise BrigadeError(f"{sha} names a commit by something other than the start of its id; pass the commit id")
    tree = {}
    for record in git(project_root, "ls-tree", "-r", "-z", commit).stdout.split(b"\0")[:-1]:
        entry, _, path = record.partition(b"\t")
        mode, _, oid = entry.decode().split(" ")
        tree[os.fsdecode(path)] = TreeEntry(mode, oid)
    return commit, tree


def head_digest(project_root, entry):
    kind, executable = BLOB_MODES.get(entry.mode, ("other", False))
    if kind == "other":
        return Digest(kind, entry.oid, executable)
    blob = git(project_root, "cat-file", "blob", entry.oid).stdout
    return Digest(kind, hashlib.sha256(blob).hexdigest(), executable)


def digests(root):
    """{path: Digest} of every entry under root that is not a directory. No link is followed."""
    found = {}
    for directory, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(directory) / name
            status = path.lstat()
            if stat.S_ISDIR(status.st_mode):
                continue
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(status.st_mode):
                found[relative] = Digest("link", hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest(), False)
            elif stat.S_ISREG(status.st_mode):
                found[relative] = Digest("file", hashlib.sha256(path.read_bytes()).hexdigest(), bool(status.st_mode & stat.S_IXUSR))
            else:
                found[relative] = Digest("other", "", False)
    return found


def exit_code(scratch, command):
    """Run a command in the scratch copy with no shell and no stdin. Its output goes to stderr when it exits non-zero."""
    try:
        result = subprocess.run(command_words(command), cwd=scratch, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as error:
        raise BrigadeError(f"cannot run {command}: {error.strerror}") from None
    if result.returncode:
        sys.stderr.buffer.write(result.stdout)
        sys.stderr.flush()
    return result.returncode


def replay(project_root, reviewed, before, after, edits, build, check, sha):
    """The lines of one verify, and whether it is clean. One scratch copy holds the reviewed commit, then the edits, then the build's output."""
    with tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory)
        archive = git(project_root, "archive", "--format=tar", reviewed).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            # extractall has no filter argument in Python 3.10 before 3.10.12 or in 3.11 before 3.11.4.
            tar.extractall(scratch, **({"filter": "tar"} if hasattr(tarfile, "tar_filter") else {}))
        extracted = digests(scratch)
        files = {path for path, digest in extracted.items() if digest.kind == "file"}
        contents = {edit.path: (scratch / edit.path).read_bytes() if edit.path in files else None for edit in edits}
        changed, lines = apply_edits(contents, edits, sha)
        if lines:
            return lines, False
        for path, data in changed.items():
            (scratch / path).write_bytes(data)
        edited = digests(scratch)
        if build != NO_COMMAND:
            code = exit_code(scratch, build)
            if code:
                return [f"FAIL build: {build} exited {code}"], False
        built = digests(scratch)
        paths = compared_paths(before, after, extracted, edited, built)
        head = {path: head_digest(project_root, after[path]) for path in paths if path in after}
        lines, clean = path_lines(paths, edited, built, head, sha)
        if clean and check != NO_COMMAND:
            code = exit_code(scratch, check)
            lines.append(f"FAIL check: {check} exited {code}" if code else "ok check")
            clean = not code
        return lines, clean


def verify_edits(restaurant, dish_id, from_sha, to_sha):
    """Called from run(), because it starts subprocesses. It holds the store lock for its first read and for its write, and never while a subprocess runs."""
    with restaurant.checked():
        restaurant.fence()
        restaurant.find("dishes.tsv", dish_id)
        rounds = [row for row in item_verdicts(restaurant.rows("pass.tsv"), dish_id)
                  if row["verdict"] == EDITS_VERDICT and row["sha"] == from_sha]
        if not rounds:
            raise BrigadeError(f"{dish_id} has no {EDITS_VERDICT} round at {from_sha}")
        meta = restaurant.meta
        build, check = meta.get("buildCommand"), meta.get("checkCommand")
        if not build or not check:
            raise BrigadeError(NEEDS_COMMANDS)
    name = rounds[-1]["report"]
    kind = report_entry(restaurant, name)
    if kind != "file":
        raise BrigadeError(f"reports/{name} {NOT_A_REPORT[kind]}; nothing verified")
    edits = report_edits(restaurant, name)
    (reviewed, before), (_, after) = (commit_tree(meta["projectRoot"], sha) for sha in (from_sha, to_sha))
    lines, verified = replay(meta["projectRoot"], reviewed, before, after, edits, build, check, from_sha)
    for line in lines:
        print(line)
    if not verified:
        raise BrigadeError(f"{dish_id}: edits not verified, {from_sha} to {to_sha}")
    row = {"dish": dish_id, "from": clean(from_sha), "to": clean(to_sha), "report": name}
    with restaurant.checked():
        restaurant.fence()
        if not any(all(recorded[key] == value for key, value in row.items()) for recorded in restaurant.rows("edits.tsv")):
            restaurant.append("edits.tsv", {"at": now(), **row})
    return f"{dish_id}: edits verified, {from_sha} to {to_sha}"


def unrecorded_reviews(restaurant):
    """(name, kind) for each entry under reports/ named like a review report that no pass.tsv row accounts for.

    The kind is report_stat's. A row of the entry's dish that names it in `report` accounts for it, and nothing is read
    from that entry. A row that names no report cannot say which file it reviewed, so it accounts for every `file` of its
    dish written at or before it, and for no entry of another kind. The time compared is the one in report_stat's status.
    An entry that report_stat calls `missing` is left out.
    """
    rows = restaurant.rows("pass.tsv")
    reports = restaurant.dir / "reports"
    unrecorded = []
    for path in sorted(reports.iterdir()) if reports.is_dir() else []:
        match = REVIEW_FILE.fullmatch(path.name)
        if not match:
            continue
        mine = [row for row in rows if row["dish"] == match.group(1)]
        if any(row["report"] == path.name for row in mine):
            continue
        kind, status = report_stat(restaurant, path.name)
        if kind == "file" and any(datetime.fromisoformat(row["at"].replace("Z", "+00:00")).timestamp() >= status.st_mtime
                                  for row in mine if not row["report"]):
            continue
        if kind != "missing":
            unrecorded.append((path.name, kind))
    return unrecorded


def unclaimed_reports(restaurant):
    """(id, state) of each dish in CLAIMS_STATES whose reports/<id>.md is a `file` with no line CLAIMS_LINE matches.

    The report is decoded as UTF-8 with replacement. An entry that report_stat does not call a `file` is not read.
    """
    unclaimed = []
    for dish in restaurant.rows("dishes.tsv"):
        name = f"{dish['id']}.md"
        if dish["state"] not in CLAIMS_STATES or report_entry(restaurant, name) != "file":
            continue
        text = (restaurant.dir / "reports" / name).read_bytes().decode("utf-8", "replace")
        if not CLAIMS_LINE.search(text):
            unclaimed.append((dish["id"], dish["state"]))
    return unclaimed


def section_lines(title, items, dishes, labels):
    """The lines of one report section under its title.

    A line of a section in GROUPED_SECTIONS has the kind of its item or ticket before the id, when it has one.
    When a line of such a section has a goal, the section prints its lines under one `### <goal id> <goal name>` heading
    per goal in goals.tsv order, then `### Unassigned`. A heading with no line is absent.
    """
    grouped, bullets = title in GROUPED_SECTIONS, []
    for event in items:
        of_item = event["kind"] in ("dish", "mode")
        dish = dishes.get(event["id"], {}) if of_item else {}
        tickets = f" ({dish['tickets'].replace(',', ', ')})" if dish.get("tickets") else ""
        pr = f" {dish['pr']}" if dish.get("pr") else ""
        label = (labels.items if of_item else labels.tickets).get(event["id"], Label())
        kind = f"{label.kind} " if grouped and label.kind else ""
        bullets.append((label.goal, f"- {kind}{event['id']}{tickets}: {event['note']}{pr}"))
    if not grouped or not any(goal for goal, _ in bullets):
        return [*(text for _, text in bullets), ""]
    lines = []
    headings = [*((goal.id, f"### {goal.id} {goal.name}") for goal in labels.goals.values()), ("", "### Unassigned")]
    for goal, heading in headings:
        under = [text for found, text in bullets if found == goal]
        if under:
            lines += [heading, "", *under, ""]
    return lines


def events_since(restaurant, since):
    """The latest log row of each kind and id newer than since."""
    latest = {}
    for event in restaurant.rows("log.tsv"):
        if event["at"] > since:
            latest[(event["kind"], event["id"])] = event
    return latest


def report(restaurant, write=True):
    meta = restaurant.meta
    since = meta.get("lastReportAt") or ""
    latest = events_since(restaurant, since)
    sections = {title: [] for title in SECTIONS.values()}
    for (kind, _), event in latest.items():
        title = SECTIONS.get((kind, event["state"]))
        if title:
            sections[title].append(event)
    dishes = {row["id"]: row for row in restaurant.rows("dishes.tsv")}
    labels = read_labels(restaurant, EVERY_ROW)
    stamp = now()
    lines = [f"# {meta['restaurant']} report", "", f"Since {since or 'opening'}. {status_line(restaurant)}.", ""]
    for title, items in sections.items():
        if items:
            lines += [f"## {title}", "", *section_lines(title, items, dishes, labels)]
    if is_admin(meta):
        lines += admin_sections(restaurant, since)
    questions = [row for row in restaurant.rows("86.tsv") if row["state"] == "open"]
    if questions:
        lines += ["## Decisions for you", ""]
        lines += [f"- {q['id']}: {q['question']} Options: {q['options']}. Default if no answer: {q['default']}." for q in questions]
        lines.append("")
    if len(lines) == 4:
        lines += ["Nothing new.", ""]
    text = "\n".join(lines)
    path = None
    if write:
        path = restaurant.dir / "closeouts" / f"{stamp[:26].replace(':', '')}.md"
        restaurant.write(path.relative_to(restaurant.dir), text)
        restaurant.change_meta(lastReportAt=stamp)
    return text, path


MEASURING_STATIONS = ("perf-issue", "hillclimb", "eval")
LAND = Path(__file__).resolve().parents[2] / "landing" / "scripts" / "land.py"
BUILT_ROLES = Path(__file__).resolve().parents[2] / "pstack-runtime" / "scripts" / "roles.py"
SOURCE_ROLES = Path(__file__).resolve().parents[3] / "scripts" / "roles.py"
ESCALATED = "escalated: "
ATTEMPT_AFTER = {"sent-back": "fix", "queued": "bounce"}
MODE_RESOLUTIONS = 3
SEAT_RULE = ("Seat rule. Copy the Mode value above into --brief-mode on every roles.py mode and roles.py show call you make, "
             "and pass no other mode flag. Never pass --session-mode. Mode source names where your launcher's decision came "
             "from. It does not make this thread a session.")
WAIT_RULE = ("- Never end your turn while a child task you started with delegate_task is still open, per step 5 of the "
             "pstack-runtime skill's Delegation section. This overrides delegate_task's text that says to end the turn and "
             "wait for a notification. A completion wakes you only when the child's run ends, and a stalled child or a run "
             "left open never ends. Wait on each open child task with t3_thread_wait on its childThreadId and timeoutMs "
             "300000, then read it with task_status. A child whose run is still open and whose last message holds the result "
             "is stalled after two minutes with no new activity, per step 5. If that wait returns at once while workState is "
             "still working or waiting_for_children, the child's run ended with its task open. Then wait between task_status "
             "checks with t3_thread_wait and timeoutMs 120000 on your own thread, the parentThreadId from "
             "orchestrator_capabilities, with runId set to its activeRunId from t3_thread_read, never a shell sleep, and "
             "count ten minutes from its last activity item. "
             "A child task is open while its workState is working or "
             "waiting_for_children, whatever hasPendingChildRuns says. Cancel a child task with task_cancel when it runs past "
             "its budget or stalls, per the runtime's Failure handling. A thread you launched with t3_thread_launch has no "
             "parent, so its finished turn never wakes you. While it stays healthy, repeat t3_thread_wait on its threadId with "
             "timeoutMs 300000 and read its activity with t3_thread_read. A timeout alone never stops it. Stop it with "
             "t3_thread_interrupt and then a terminal t3_thread_wait only when it stalls, with no new activity item for ten "
             "minutes per step 5 of the runtime's Delegation section, or runs past its budget. A long-lived "
             "owner your playbook supervises, such as an Orchestrate PR owner, follows the runtime's Top-level threads section "
             "and its playbook instead, and does not hold your report. Write the report only once every child task and every "
             "other thread you launched is terminal.")


def holder(restaurant, dish):
    return f"{slug(restaurant.meta['restaurant'])}/{dish}"


class ClaimError(BrigadeError):
    """land.py refused a lease claim. fire records the block and adds its own prefix."""


def worker_cap(meta):
    value = meta.get("workers")
    if value in (None, ""):
        return 2
    return int(value)


def autofire_of(meta):
    return meta.get("autofire") or "normal"


def workers_line(meta, running):
    cap = worker_cap(meta)
    return f"workers: {running} of {cap} running, {max(0, cap - running)} idle, auto-start: {autofire_of(meta)}"


def require_workers(workers):
    if workers is not None and workers < 1:
        raise BrigadeError("workers must be 1 or more")


def running_workers(restaurant):
    return sum(row["state"] in COUNTED_STATES for row in restaurant.rows("dishes.tsv"))


def workers_full(restaurant):
    cap = worker_cap(restaurant.meta)
    running = running_workers(restaurant)
    if running >= cap:
        return f"{running} of {cap} workers running"
    return None


def changelog_fragment(branch):
    encoded = branch.replace("%", "%25").replace("/", "%2F")
    return f"changes/{encoded}.md"


def with_fragment(paths, branch):
    if not paths or not branch:
        return paths
    fragment = changelog_fragment(branch)
    parts = [part for part in paths.split(",") if part]
    if fragment not in parts:
        parts.append(fragment)
    return ",".join(parts)


def refusal_kind(message):
    if "repository at its cap" in message:
        return "repository"
    return "lease"


def block_note(kind, ids, station, summary, paths, timebox, branch="", reason="", work="", ride_along=()):
    """`kind` is the kind of block. `work` is the --kind fire was given, and `ride_along` the ids --ride-along named."""
    note = {"kind": kind, "tickets": list(ids), "station": station, "summary": summary,
            "paths": paths, "timebox": timebox}
    if branch:
        note["branch"] = branch
    if reason:
        note["reason"] = reason
    if work:
        note["work"] = work
    if ride_along:
        note["ride_along"] = list(ride_along)
    return json.dumps(note, separators=(",", ":"))


def record_blocked(restaurant, ids, kind, station, summary, paths, timebox, branch="", reason="", work="", ride_along=()):
    waiting = {row["id"] for row in restaurant.rows("rail.tsv") if row["state"] == "waiting"}
    note = block_note(kind, ids, station, summary, paths, timebox, branch, reason, work, ride_along)
    for ident in ids:
        if ident in waiting:
            restaurant.log("ticket", ident, "blocked", note)


def blocked_waiting(restaurant):
    latest = {}
    for event in restaurant.rows("log.tsv"):
        if event["kind"] == "ticket":
            latest[event["id"]] = event
    found = []
    for ticket in restaurant.rows("rail.tsv"):
        event = latest.get(ticket["id"])
        if ticket["state"] != "waiting" or not event or event["state"] != "blocked":
            continue
        try:
            note = json.loads(event["note"])
        except json.JSONDecodeError:
            continue
        if isinstance(note, dict):
            found.append((ticket["id"], note))
    return found


def land_result(project_root, *args):
    if READ_ONLY:
        raise BrigadeError("brigade: next is read-only")
    return subprocess.run([sys.executable, str(LAND), "--repo", str(project_root), *args],
                          capture_output=True, text=True)


def contract_mode(project_root):
    """`lands by merge`, or `no landing contract` when land.py status fails."""
    result = land_result(project_root, "status")
    if result.returncode != 0 or not result.stdout.strip():
        return "no landing contract"
    return f"lands by {result.stdout.split(None, 1)[0]}"


def repository_cap(project_root):
    result = land_result(project_root, "status")
    if result.returncode != 0:
        return None
    match = re.search(r"changes in flight: \d+ of (\d+)", result.stdout)
    return int(match.group(1)) if match else None


def claim_lease(restaurant, dish, paths):
    """Claim the dish's paths in the repository's landing queue. A refused claim fires nothing."""
    result = land_result(restaurant.meta["projectRoot"], "lease", "claim",
                         "--holder", holder(restaurant, dish), "--paths", paths, *restaurant.land_owner())
    if result.returncode != 0:
        raise ClaimError(result.stderr.strip().removeprefix("land: "))
    return result.stdout.strip()


def release_lease(restaurant, lease):
    result = land_result(restaurant.meta["projectRoot"], "lease", "release", lease, *restaurant.land_owner())
    if result.returncode != 0:
        diagnostic = (result.stderr or result.stdout).strip().removeprefix("land: ")
        print(f"brigade: warning: could not release {lease}: {diagnostic}", file=sys.stderr)


def lease_check(project_root, holder_name, paths):
    result = land_result(project_root, "lease", "check", "--holder", holder_name, "--paths", paths)
    diagnostic = result.stderr.strip().removeprefix("land: ")
    return result.returncode, result.stdout.strip(), diagnostic


def fire_command(ident, note):
    """Shell-quoted fire line. Each option is one --name=value word so a value that starts with - stays the value."""
    tickets = ",".join(note.get("tickets") or [ident])
    pairs = [
        ("tickets", tickets),
        ("station", note.get("station", "")),
        ("summary", note.get("summary", "")),
    ]
    if note.get("branch"):
        pairs.append(("branch", note["branch"]))
    if note.get("paths"):
        pairs.append(("paths", note["paths"]))
    pairs.append(("timebox", note.get("timebox", 60)))
    if note.get("work"):
        pairs.append(("kind", note["work"]))
    if note.get("ride_along"):
        pairs.append(("ride-along", ",".join(note["ride_along"])))
    if note.get("reason"):
        pairs += [("mode", "full"), ("reason", note["reason"])]
    words = ["fire", *(f"--{name}={value}" for name, value in pairs)]
    return " ".join(shlex.quote(str(word)) for word in words)


def lease_block(project_root, holder_name, paths, fallback_kind="lease"):
    code, out, err = lease_check(project_root, holder_name, paths)
    if code == 0:
        return None
    held = re.findall(r"^(L\d+) held by (\S+) on ", out, re.M)
    if held:
        return "lease", "waiting on " + ", ".join(f"{lease} ({held_by})" for lease, held_by in held)
    room = re.search(r"(\d+ of \d+ changes in flight)", out)
    if room:
        return "repository", f"waiting for room in the repository ({room.group(1)})"
    return fallback_kind, f"waiting on the landing queue ({out or err or 'lease check failed'})"


def block_holds(project_root, prefix, ident, note, running, cap, next_dish):
    """The block on a waiting ticket that holds now, as (kind, text), or None. Workers come first, as in fire."""
    if running >= cap:
        return "workers", f"waiting for a worker ({running} of {cap} running)"
    requested = note.get("paths") or ""
    if not requested:
        return None
    known = note.get("branch") or f"{prefix}/{next_dish.lower()}"
    return lease_block(project_root, f"{prefix}/{ident}", with_fragment(requested, known), note.get("kind") or "lease")


def block_inputs(restaurant):
    """What a block recheck reads from the store. Call it under the store lock."""
    return blocked_waiting(restaurant), running_workers(restaurant), worker_cap(restaurant.meta), restaurant.next_id("dishes.tsv")


def recheck_blocks(restaurant, inputs):
    """Each blocked ticket, its note, and the block that holds now or None. land.py runs here, outside the store lock."""
    blocked, running, cap, next_dish = inputs
    root, prefix = restaurant.meta["projectRoot"], slug(restaurant.meta["restaurant"])
    return [(ident, note, block_holds(root, prefix, ident, note, running, cap, next_dish)) for ident, note in blocked]


def holding_blocks(restaurant):
    """The blocks that still hold, by ticket, rechecked as watch does."""
    with restaurant.checked():
        inputs = block_inputs(restaurant)
    return {ident: held for ident, _, held in recheck_blocks(restaurant, inputs) if held}


def paths_overlap(first, second):
    return any(a == b or a.startswith(b + "/") or b.startswith(a + "/") for a in first for b in second)


def paths_inside(paths, within):
    return all(any(a == b or a.startswith(b + "/") for b in within) for a in paths)


@dataclass(frozen=True)
class Waiting:
    id: str
    priority: str
    filed: datetime
    paths: tuple
    decision: bool
    summary: str
    label: Label = Label()


@dataclass(frozen=True)
class Standing:
    ticket: Waiting
    kind: str
    why: str
    overdue: bool


def waiting_ticket(row, label=Label()):
    filed = datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
    if filed.tzinfo is None:
        filed = filed.replace(tzinfo=timezone.utc)
    return Waiting(row["id"], priority_of(row), filed, paths_of(row), row["decision"] == "yes", row["summary"], label)


def plan(tickets, running, cap, level, refusals, now):
    allowed = () if level == "off" else PRIORITIES_BY_RANK[:PRIORITIES_BY_RANK.index(level) + 1]
    idle = max(0, cap - running)
    priorities = {ticket.id: ticket.priority for ticket in tickets}
    higher_waits = any(ticket.priority != "low" for ticket in tickets)
    started = {}
    standings = []
    for ticket in sorted(tickets, key=lambda ticket: (PRIORITIES_BY_RANK.index(ticket.priority), ticket.filed,
                                                     int(ticket.id[1:]) if ticket.id[1:].isdigit() else 0)):
        low = ticket.priority == "low"
        shared = [ident for ident, paths in started.items() if paths_overlap(ticket.paths, paths)]
        kind, why = "start", ""
        if ticket.decision:
            kind = "decision"
        elif not ticket.paths:
            kind = "unknown"
        elif ticket.id in refusals:
            kind, why = "blocked", refusals[ticket.id]
        elif low and len(shared) == 1 and priorities[shared[0]] != "low" and paths_inside(ticket.paths, started[shared[0]]):
            kind, why = "rides", shared[0]
        elif shared:
            kind, why = "blocked", STARTS_AFTER.format(tickets=", ".join(shared))
        elif ticket.priority not in allowed:
            kind = "below"
        elif not idle:
            kind, why = "blocked", NO_WORKER
        elif low and higher_waits and idle <= LOW_RESERVE:
            kind = "reserve"
        else:
            idle -= 1
            started[ticket.id] = ticket.paths
        standings.append(Standing(ticket, kind, why, low and now - ticket.filed > timedelta(days=LOW_FLAG_DAYS)))
    return standings


def lease_refusals(meta, tickets, next_dish):
    root, prefix = meta["projectRoot"], slug(meta["restaurant"])
    asked, refusals = {}, {}
    for ticket in tickets:
        if ticket.decision or not ticket.paths:
            continue
        if ticket.paths not in asked:
            asked[ticket.paths] = lease_block(root, f"{prefix}/{ticket.id}",
                                              with_fragment(",".join(ticket.paths), f"{prefix}/{next_dish.lower()}"))
        if asked[ticket.paths]:
            refusals[ticket.id] = clean(asked[ticket.paths][1])
    return refusals


def age(delta):
    seconds = max(0, int(delta.total_seconds()))
    if seconds >= 86400:
        return f"{seconds // 86400}d"
    if seconds >= 3600:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"


def startable_lines(standings, meta, running, now):
    lines = [workers_line(meta, running)]
    if not standings:
        return "\n".join([*lines, "no waiting tickets"])
    kinds = [standing.kind for standing in standings]
    lines.append(", ".join([f"waiting tickets: {len(standings)}",
                            *(f"{text.count_label}: {kinds.count(kind)}" for kind, text in STANDINGS.items() if kind in kinds)]))
    for standing in standings:
        ticket = standing.ticket
        flag = " (over a week)" if standing.overdue else ""
        paths = f"; {','.join(ticket.paths)}" if ticket.paths else ""
        words = STANDINGS[standing.kind].line_words.format(why=standing.why)
        lines.append(f"{ticket.id} {ticket.priority}{label_words(ticket.label)}, {age(now - ticket.filed)}{flag}: {words}{paths}")
        if standing.kind in ("start", "rides"):
            lines.append(f"  {ticket.summary}")
    return "\n".join(lines)


def startable(restaurant):
    with restaurant.checked():
        meta, labels = restaurant.meta, read_labels(restaurant, EVERY_ROW)
        tickets = [waiting_ticket(row, labels.tickets[row["id"]]) for row in restaurant.rows("rail.tsv") if row["state"] == "waiting"]
        running, next_dish = running_workers(restaurant), restaurant.next_id("dishes.tsv")
    refusals = lease_refusals(meta, tickets, next_dish)
    moment = datetime.now(timezone.utc)
    return startable_lines(plan(tickets, running, worker_cap(meta), autofire_of(meta), refusals, moment), meta, running, moment)


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


def unfireable(restaurant, ids):
    # A ticket fired again is the same work, so an item that owes its round decision holds its tickets.
    for dish in restaurant.rows("dishes.tsv"):
        held = [ident for ident in ids if ident in dish["tickets"].split(",")]
        refusal = fix_round_refusal(restaurant, dish) if held else None
        if refusal:
            return f"{held[0]} is {dish['id']}'s ticket; {refusal}"
    for ident in ids:
        _, ticket = restaurant.find("rail.tsv", ident)
        if ticket["state"] != "waiting":
            return f"{ident} is {ticket['state']}, not waiting"
    return None


def fire_label(labels, rail, ids, station, kind, ride_along):
    """The kind and goal the new item takes, as (Label, None), or (None, refusal). `rail` is {ticket id: row}.

    Call it after unfireable has found each id in `ids` and found it waiting. The lead ticket is ids[0].
    The item takes the lead ticket's goal, and no goal when `ids` is empty.
    The kind is the first of these that gives one. The lead ticket's kind, then `kind`, then STATION_KIND[station].
    `kind` beside a lead ticket of another kind is refused.
    Each id in `ride_along` must be in `ids`, must not be the lead ticket, must have priority low, and must not be a feature.
    Each other ticket after the lead must have the lead ticket's goal or no goal.
    """
    lead = ids[0] if ids else None
    mine = labels.tickets[lead] if lead else Label()
    if kind and mine.kind and kind != mine.kind:
        return None, (f"{lead} has kind {mine.kind}, and --kind {kind} does not match it; "
                      f"drop --kind, or run ticket set {lead} --kind {kind} first when no item in flight names {lead}")
    found = mine.kind or kind or STATION_KIND.get(station, "")
    if not found:
        return None, ((f"{lead} has no kind" if lead else "no ticket gives this item a kind")
                      + f" and station {station} gives none; pass --kind {KIND_CHOICE}")
    for ident in ride_along:
        if ident not in ids:
            return None, f"--ride-along names {ident}, which is not in --tickets; add it there or drop it"
        if ident == lead:
            return None, (f"{ident} is the lead ticket, and the lead ticket cannot ride along; "
                          "put the ticket whose goal the item takes first in --tickets")
        if priority_of(rail[ident]) != "low":
            return None, (f"{ident} is {priority_of(rail[ident])}; only a low ticket rides along; "
                          f"start it on its own, or run ticket set {ident} --priority low")
        if labels.tickets[ident].kind == "feature":
            return None, f"{ident} is a feature, and a feature does not ride along; start it as its own item"
    for ident in ids[1:]:
        theirs = labels.tickets[ident].goal
        if ident not in ride_along and theirs and theirs != mine.goal:
            return None, (f"{ident} is under {theirs} and the lead ticket {lead} is {'under ' + mine.goal if mine.goal else 'unassigned'}; "
                          f"name {ident} in --ride-along, which takes a low ticket that is not a feature, or start it on its own")
    return Label(found, mine.goal), None


def checked_fire_label(restaurant, ids, station, kind, ride_along):
    """fire_label on the store as it is now, read under the caller's lock.

    read_labels prints the warnings of goals.tsv and of the tickets in `ids`.
    """
    labels = read_labels(restaurant, ActsOn(tickets=tuple(ids)))
    rail = {row["id"]: row for row in restaurant.rows("rail.tsv")}
    return fire_label(labels, rail, ids, station, kind, ride_along)


def write_lead_kind(restaurant, ids, kind):
    """Write `kind` into the kind cell of the lead ticket, ids[0], when the cell holds another value. It logs nothing.

    fire calls it before it appends the item row. A fire killed after it leaves no item whose kind its lead ticket lacks.
    """
    rows = restaurant.rows("rail.tsv")
    changed = [row for row in rows if row["id"] in ids[:1] and row["kind"] != kind]
    for row in changed:
        row["kind"] = kind
    if changed:
        restaurant.save_rows("rail.tsv", rows)


def fire(restaurant, ids, station, task, thread, branch, summary, timebox, paths, reason=None, kind="", ride_along=()):
    """Start one item for the tickets in `ids`. The item takes the kind and goal fire_label gives.

    The kind and goal check runs twice, each time under the store lock. The first run comes after the owner check, unfireable, and the worker
    cap, so a fire that one of those refuses or blocks gets the words it got before kinds and goals. It comes before the
    lease claim, so a fire it refuses claims no lease. The second run sees a change made while the claim ran.
    The lead ticket's kind cell takes the item's kind in a rewrite of its own, before the item row is appended. A fire
    killed between its writes leaves no item whose kind its lead ticket lacks. No other kind or goal cell
    of a ticket changes, so a ticket that rides along keeps its own.
    """
    while True:
        with restaurant.checked():
            restaurant.fence()
            refusal = unfireable(restaurant, ids)
            if refusal:
                raise BrigadeError(refusal)
            dish = restaurant.next_id("dishes.tsv")
            known = branch or f"{slug(restaurant.meta['restaurant'])}/{dish.lower()}"
            leased = with_fragment(paths, known) if paths else ""
            full = workers_full(restaurant)
            if full:
                record_blocked(restaurant, ids, "workers", station, summary, paths, timebox, branch, reason, kind, ride_along)
                raise BrigadeError(f"nothing fired: {full}")
            _, refusal = checked_fire_label(restaurant, ids, station, kind, ride_along)
            if refusal:
                raise BrigadeError(refusal)
        # land.py can wait on the landing database, so the claim runs outside the store lock.
        # Another command may fire or take the tickets meanwhile, so the checks run again before the write.
        try:
            lease = claim_lease(restaurant, dish, leased) if leased else ""
        except ClaimError as error:
            with restaurant.checked():
                record_blocked(restaurant, ids, refusal_kind(str(error)), station, summary, paths, timebox, branch, reason,
                               kind, ride_along)
            raise BrigadeError(f"nothing fired: {error}") from error
        with restaurant.checked():
            refusal = unfireable(restaurant, ids)
            current = restaurant.next_id("dishes.tsv")
            full = None if refusal else workers_full(restaurant)
            label = None
            if not refusal and not full:
                label, refusal = checked_fire_label(restaurant, ids, station, kind, ride_along)
            if label and current == dish:
                write_lead_kind(restaurant, ids, label.kind)
                restaurant.append("dishes.tsv", {"id": dish, "at": now(), "state": "in-progress", "station": station,
                                                 "tickets": ",".join(ids), "task": task, "thread": thread,
                                                 "branch": branch, "summary": summary, "timebox": timebox,
                                                 "lease": lease, "paths": leased, "kind": label.kind, "goal": label.goal})
                restaurant.log("dish", dish, "in-progress", summary)
                if reason:
                    record_escalation(restaurant, dish, reason)
                rows = restaurant.rows("rail.tsv")
                for row in rows:
                    if row["id"] in ids:
                        row["state"], row["dish"] = "assigned", dish
                restaurant.save_rows("rail.tsv", rows)
                for row in rows:
                    if row["id"] in ids:
                        restaurant.log("ticket", row["id"], "assigned", row["summary"])
                return f"{dish} (lease {lease} held by {holder(restaurant, dish)})" if lease else dish
            if full and not refusal:
                record_blocked(restaurant, ids, "workers", station, summary, paths, timebox, branch, reason, kind, ride_along)
                refusal = full
        if lease:
            release_lease(restaurant, lease)
        if refusal:
            raise BrigadeError(f"nothing fired: {refusal}")


def item_escalation(mode, reason):
    if mode is None:
        if reason is not None:
            raise BrigadeError("--reason goes with --mode full")
        return None
    if mode == "light":
        raise BrigadeError("an item's mode only moves to full; change the coordinator with set --mode light")
    if mode != "full":
        raise BrigadeError(f"--mode takes full, got {mode!r}")
    if not clean(reason):
        raise BrigadeError('--mode full needs --reason "<one line>"')
    return clean(reason)


def latest_mode_note(events, ident):
    notes = [event["note"] for event in events if event["kind"] == "mode" and event["id"] == ident]
    return notes[-1] if notes else None


def record_escalation(restaurant, ident, reason):
    restaurant.fence()
    _, dish = restaurant.find("dishes.tsv", ident)
    if dish["state"] not in LEASED_STATES:
        raise BrigadeError(f"{ident} is {dish['state']}; only open work moves to full mode")
    recorded = latest_mode_note(restaurant.rows("log.tsv"), ident)
    if recorded is not None:
        return recorded
    restaurant.log("mode", ident, "full", reason)
    return reason


def menu_purpose(restaurant):
    text = (restaurant.dir / "menu.md").read_text()
    match = re.search(r"^## Purpose\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    purpose = match.group(1).strip() if match else ""
    if not purpose or purpose.startswith("What this restaurant exists to achieve"):
        raise BrigadeError("menu.md has no purpose yet; write the Purpose section before briefing a worker")
    return purpose


def attempt_kind(events, ident):
    kind = "first"
    for event in events:
        if event["kind"] == "dish" and event["id"] == ident:
            kind = ATTEMPT_AFTER.get(event["state"], kind)
    return kind


@dataclass(frozen=True)
class ModeInputs:
    project_root: str
    station: str
    attempt: str
    paths: str
    send_backs: int
    coordinator: str
    escalated: str
    dish: dict
    thread: str
    generation: int


@dataclass(frozen=True)
class ModeLines:
    lines: tuple
    mode: str
    escalation: str


def brief_dish(restaurant, ident, paths, lease, acceptance):
    _, dish = restaurant.find("dishes.tsv", ident)
    refusal = fix_round_refusal(restaurant, dish)
    if refusal:
        raise BrigadeError(refusal)
    paths, lease = paths or dish.get("paths", ""), lease or dish.get("lease", "")
    if not paths or not lease:
        raise BrigadeError(f"{ident} has no lease; fire with --paths, or pass --paths and --lease")
    if not acceptance:
        raise BrigadeError("a brief needs at least one --acceptance criterion")
    if dish["state"] not in ("in-progress", "sent-back"):
        raise BrigadeError(f"{ident} is {dish['state']}; brief a dish that is in progress or sent back")
    thread = (restaurant.meta.get("thread") or "").strip()
    if not thread:
        raise BrigadeError("no coordinator thread recorded; run brigade.py set --thread")
    menu_purpose(restaurant)
    return dish, paths, lease, thread


def mode_inputs(restaurant, dish, paths, thread):
    meta = restaurant.meta
    events = restaurant.rows("log.tsv")
    sent_back = len(send_backs(restaurant.rows("pass.tsv"), dish["id"]))
    return ModeInputs(meta["projectRoot"], dish["station"], attempt_kind(events, dish["id"]), paths, sent_back,
                      meta.get("mode") if meta.get("mode") in MODES else None, latest_mode_note(events, dish["id"]),
                      dish, thread, meta.get("generation"))


def roles_script():
    for path in (BUILT_ROLES, SOURCE_ROLES):
        if path.is_file():
            return path
    raise BrigadeError(f"cannot find roles.py at {BUILT_ROLES} or {SOURCE_ROLES}; build or reinstall pstack-t3")


def roles_mode(inputs):
    if HELD_LOCKS:
        raise BrigadeError(f"roles.py may run git; release {HELD_LOCKS[0]}'s store lock before resolving the mode")
    argv = [sys.executable, str(roles_script()), "mode", "--cwd", inputs.project_root, "--paths", inputs.paths,
            "--send-backs", str(inputs.send_backs)]
    if inputs.coordinator:
        argv += ["--coordinator-mode", inputs.coordinator]
    if inputs.escalated is not None:
        argv += ["--escalated", inputs.escalated]
    result = subprocess.run(argv + ["--playbook", inputs.station, "--attempt", inputs.attempt], capture_output=True, text=True)
    if result.returncode and "unknown playbook" in result.stderr:
        result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode:
        raise BrigadeError(f"roles.py mode refused the brief: {result.stderr.strip().removeprefix('error: ')}")
    lines = tuple(result.stdout.splitlines())
    modes = [line.removeprefix("Mode: ") for line in lines if line.startswith("Mode: ")]
    if len(modes) != 1 or modes[0] not in MODES:
        raise BrigadeError("roles.py mode printed no Mode: full or Mode: light line")
    source = next((line.removeprefix("Mode source: ") for line in lines if line.startswith("Mode source: ")), "")
    return ModeLines(lines, modes[0], source.removeprefix(ESCALATED) if source.startswith(ESCALATED) else None)


def brief(restaurant, ident, goal, acceptance, contract, verify, paths, lease, base, context):
    for _ in range(MODE_RESOLUTIONS):
        with restaurant.checked():
            restaurant.fence()
            dish, used_paths, _, thread = brief_dish(restaurant, ident, paths, lease, acceptance)
            inputs = mode_inputs(restaurant, dish, used_paths, thread)
        mode = roles_mode(inputs)
        with restaurant.checked():
            restaurant.fence()
            dish, used_paths, used_lease, thread = brief_dish(restaurant, ident, paths, lease, acceptance)
            if mode_inputs(restaurant, dish, used_paths, thread) != inputs:
                continue
            if inputs.escalated is None and mode.escalation:
                record_escalation(restaurant, ident, mode.escalation)
            return write_brief(restaurant, dish, mode, goal, acceptance, contract, verify, used_paths, used_lease, base,
                               context, thread)
    raise BrigadeError(f"{ident} changed while resolving its mode; run brief again")


def write_brief(restaurant, dish, mode, goal, acceptance, contract, verify, paths, lease, base, context, thread):
    ident = dish["id"]
    if not dish["branch"]:
        dish = restaurant.update("dishes.tsv", ident, "dish", branch=f"{slug(restaurant.meta['restaurant'])}/{ident.lower()}")
    tickets = {row["id"]: row for row in restaurant.rows("rail.tsv")}
    land = LAND
    report = restaurant.dir / "reports" / f"{ident}.md"
    sent_back = [row for row in item_verdicts(restaurant.rows("pass.tsv"), ident) if VERDICTS.get(row["verdict"]) == "sent-back"]
    findings = restaurant.dir / "reports" / (sent_back[-1]["report"] if sent_back and sent_back[-1]["report"] else f"{ident}-review.md")
    lines = [
        f"Use the poteto-mode skill and its `{dish['station']}` playbook.", *mode.lines, "Gate: brigade", SEAT_RULE, "",
        f"GOAL: {goal}",
        f"PURPOSE: {menu_purpose(restaurant)}",
        f"TICKETS: " + "; ".join(f"{t}: {tickets[t]['summary']}" for t in dish["tickets"].split(",") if t in tickets), "",
        "SCOPE:",
        f"- You run in your own git worktree on branch `{dish['branch']}`, started from `{base}`. Work only there, and commit on that branch.",
        f"- Change only these paths, leased to you as {lease}: {paths}. A change outside them is refused when it lands. Report it instead of making it.",
        "- Keep history linear: no merge commits. Never merge, rebase a shared branch, push trunk, or open a PR. The landing queue does that.",
        "", "CONTEXT:", *([f"- {item}" for item in context] or ["- None beyond the tickets."]),
        *([f"- A reviewer sent an earlier attempt back. Its findings: {findings}"] if findings.exists() else []),
        "", "ACCEPTANCE:", *[f"- {item}" for item in acceptance],
        "", "CONTRACT:", contract,
        "", "VERIFY:", f"- {verify}",
        f"- Run builds and tests through `python3 {land} slot -- <command>`.",
        *([f"- Run every measurement through `python3 {land} slot --exclusive -- <command>`, so no other work shares the machine while it runs."]
          if dish["station"] in MEASURING_STATIONS else []),
        "", f"TIMEBOX: {dish.get('timebox') or 60} minutes. The timebox orders the work and never waives a playbook step (How, Architect, investigation, or the implementation delegate). At the limit, write the report with what remains instead of skipping steps.",
        "", "REPORT:",
        WAIT_RULE,
        f"- Write it to {report}: status, branch, head SHA, what you ran and its output, before and after numbers with the method, deviations, follow-ups. "
        "List each follow-up as one top-level list item under a `## Follow-ups` heading, or write `None.` there.",
        "- Under the status line, repeat this brief's Mode: line, and its Waived by mode: line when it has one. A step that line names is not a deviation.",
        *(["- If you find the design contested, do not run interrogate. Stop at a verifiable point, commit, and write Contested: <one-line reason> under the status line. The coordinator moves the work to full mode and gives your report to a fresh worker."]
          if mode.mode == "light" else []),
        f"- After that file is written, call t3_thread_send to thread {thread} with mode \"auto\" and the one-line message \"{ident} done: report at {report}\".",
        "- Then end your turn with one line naming the report path.",
        "", "STANDING ORDERS:", (restaurant.dir / "house-rules.md").read_text().strip(),
    ]
    text = "\n".join(lines) + "\n"
    restaurant.write(contract_file(ident), contract + "\n")
    restaurant.write(Path("briefs") / f"{ident}.md", text)
    return text


def contract_file(ident):
    return Path("briefs") / f"{ident}.contract.md"


def stored_contract(restaurant, ident):
    """The contract the item's last brief stored.

    It is the contract file's text, with no newline converted and without the one newline write_brief put after the contract.
    """
    restaurant.find("dishes.tsv", ident)
    path = restaurant.dir / contract_file(ident)
    if not path.is_file():
        raise BrigadeError(f"{ident} has no contract on record; run brief with --contract")
    return path.read_bytes().decode("utf-8").removesuffix("\n")


def attempt_starts(restaurant, dish):
    """When each attempt started: the dish's in-progress log rows, oldest first.

    Replacing the worker appends that row again without leaving in-progress.
    """
    moves = [row["at"] for row in restaurant.rows("log.tsv") if row["kind"] == "dish" and row["id"] == dish["id"] and row["state"] == "in-progress"]
    return [datetime.fromisoformat(at) for at in moves or [dish["at"]]]


def retired_worker_threads(restaurant, ident):
    """Worker thread ids this dish already used on an earlier attempt."""
    return [row["note"] for row in restaurant.rows("log.tsv")
            if row["kind"] == "worker" and row["id"] == ident and row["state"] == "retired" and row["note"]]


def renew_line(restaurant, dish):
    """Renew a live lease. Returns the line to print, or None, and whether the landing store answered."""
    lease = dish["lease"]
    result = land_result(restaurant.meta["projectRoot"], "lease", "renew", lease, "--if-live", *restaurant.land_owner())
    error = result.stderr.strip().removeprefix("land: ")
    if result.returncode == 0 or error.startswith(f"{lease} is submitted"):
        return None, True
    if error == f"{lease} is released":
        return f"{dish['id']}: lease {lease} was released; claim again before submitting", True
    if error.startswith(f"{lease} expired at "):
        return f"{dish['id']}: lease {lease} expired; stop its worker, then run lease renew {lease}", True
    return f"{dish['id']}: could not renew {lease}: {error or result.stdout.strip()}", False


ENTRY_LINE = re.compile(r"^E(\d+) (\S+) \((\S+), ([0-9a-f]+)\)(.*)$")


def entry_line(restaurant, dish):
    """The queue's entry for this dish: its holder's highest entry at exactly the dish's SHA.

    Returns the line to print, or None, and whether that entry holds the dish's submission.
    """
    sha = dish["sha"].lower()
    if not sha:
        return (None if dish["state"] == "passed" else f"{dish['id']}: queued, but no SHA recorded"), False
    result = land_result(restaurant.meta["projectRoot"], "status", "--holder", holder(restaurant, dish["id"]), "--sha", sha)
    if result.returncode != 0:
        return f"{dish['id']}: could not read the queue: {result.stderr.strip().removeprefix('land: ')}", False
    found = None
    for line in result.stdout.splitlines():
        found = ENTRY_LINE.match(line) or found
    if not found:
        return (None if dish["state"] == "passed" else f"{dish['id']}: queued, but no entry at {sha[:12]}"), False
    number, state, rest = found.group(1), found.group(2), found.group(5)
    ident = dish["id"]
    if state == "bounced":
        return f"{ident}: E{number} bounced{rest}", False
    if dish["state"] == "passed":
        return f"{ident}: E{number} already submitted; mark it queued", True
    if state == "landed":
        return f"{ident}: landed as E{number} ({rest.removeprefix(' as ')}); mark it merged", True
    if state == "awaiting-merge":
        return f"{ident}: E{number} awaiting merge {rest.strip()}; watch that PR", True
    return f"{ident}: E{number} {state}", True


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
    if minutes > timebox:
        return [f"{dish['id']}: over its {timebox}m timebox at {minutes}m with no report; read its thread and decide ({where})"]
    return [f"{dish['id']}: running {minutes}m of {timebox}m ({where})"]


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
    # land.py waits on the landing database, so every call runs outside the store lock.
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
    if answered:
        # walk --stale-hours then measures whether this coordinator still keeps its leases alive.
        restaurant.change_meta(lastActivityAt=now())
    return "\n".join([workers, *(item_lines + lines or ["no work in progress"])])


def drop(restaurant, ident, stopped):
    """Drop a dish and release its lease, only after its worker stopped. A requested interrupt is not a stop."""
    with restaurant.checked():
        restaurant.fence()
        _, dish = restaurant.find("dishes.tsv", ident)
        lease = dish["lease"] if dish["state"] not in ("merged", "dropped") else ""
        if lease and dish["thread"] and not stopped:
            raise BrigadeError(f"{ident} holds {lease} and its worker may still be running; "
                               "wait for its run with t3_thread_wait, then pass --stopped <run id>")
    if lease:
        result = land_result(restaurant.meta["projectRoot"], "lease", "release", lease, *restaurant.land_owner())
        error = result.stderr.strip().removeprefix("land: ")
        if result.returncode != 0 and not error.startswith(f"{lease} is not active"):
            raise BrigadeError(f"{ident} not dropped: could not release {lease}: {error}")
    with restaurant.checked():
        _, dish = restaurant.find("dishes.tsv", ident)
        evidence = [f"stopped {stopped}"] if stopped else []
        evidence += [f"released {lease}"] if lease else []
        note = f"{dish['summary']} ({'; '.join(evidence)})" if evidence else None
        restaurant.update("dishes.tsv", ident, "dish", note=note, state="dropped")
        rows = restaurant.rows("rail.tsv")
        back = [row for row in rows if row["state"] == "assigned" and row["dish"] == ident]
        for row in back:
            row["state"], row["dish"] = "waiting", ""
        if back:
            restaurant.save_rows("rail.tsv", rows)
        for row in back:
            restaurant.log("ticket", row["id"], "waiting", f"{row['summary']} (back from dropped {ident})")
    if back:
        return f"{ident} dropped; {', '.join(row['id'] for row in back)} waiting again"
    return f"{ident} dropped"


def lease_states(project_root):
    """Each held lease's state in the landing queue, active or submitted, by id."""
    result = land_result(project_root, "lease", "list")
    return dict(line.split()[:2] for line in result.stdout.splitlines() if line.startswith("L"))


def fragment_lines(restaurant, ident, branch):
    """The commands that lease the new branch's changelog fragment. They are printed, not run.

    land.py cannot add paths to a lease. A claim made here would stay unrecorded when a drop or a new owner
    wins the store between the claim and its record.
    """
    states = lease_states(restaurant.meta["projectRoot"])
    with restaurant.checked():
        dish = restaurant.find("dishes.tsv", ident)[1]
    lease, paths = dish["lease"], with_fragment(dish["paths"], branch)
    if not lease or dish["state"] in ("merged", "dropped") or paths == dish["paths"]:
        return None
    fragment = changelog_fragment(branch)
    if states.get(lease) == "submitted":
        return (f"{ident}: {lease} is submitted, so it cannot take {fragment}; the submitted commit lands as it is. "
                f"Run dish {ident} --branch {branch} again if its entry bounces")
    owner = " ".join(restaurant.land_owner())
    owner = f" {owner}" if owner else ""
    active = states.get(lease) == "active"
    lines = [f"{ident}: {lease} {'does not cover' if active else 'is not active, so nothing covers'} {fragment}; "
             "cover it with these commands:",
             f"  $L lease claim --holder {holder(restaurant, ident)} --paths {shlex.quote(paths)}{owner}",
             f"  $B dish {ident} --lease <new lease> --paths {shlex.quote(paths)}"]
    if active:
        lines.append(f"  $L lease release {lease}{owner}")
    return "\n".join(lines)


def next_owner(meta, thread, replace, expect=None, stopped=None):
    """The generation set --thread records, and whether it changes the recorded thread.

    The executive admin's thread changes only by compare-and-swap: --expect names the thread it replaces.
    """
    current = (meta.get("thread") or "").strip()
    generation = meta.get("generation")
    if expect is not None:
        if not is_admin(meta):
            raise BrigadeError("--expect works only in the executive admin's store")
        if not replace:
            raise BrigadeError("the executive admin's thread changes only with --replace --expect <old>")
        if current != expect:
            raise BrigadeError(f"thread is {current or 'not recorded'}, not {expect or 'empty'}; nothing replaced")
        if thread == current:
            return generation or 1, False
        if current.startswith("recovering:") and not stopped:
            raise BrigadeError(NOT_STOPPED)
        return (generation or 0) + 1, True
    if is_admin(meta) and (current or generation) and thread != current:
        raise BrigadeError("the executive admin's thread changes only with --replace --expect <old>")
    if current and thread != current:
        if not replace:
            raise BrigadeError(f"thread {current} already recorded")
        return (generation or 1) + 1, True
    return generation or 1, not current


def set_thread(restaurant, args):
    """Raise the landing floor first, outside the store lock, so a crash before the rewrite is reached again."""
    while True:
        with restaurant.locked():
            target = next_owner(restaurant.meta, args.thread, args.replace, args.expect, args.stopped)
        generation, changing = target
        if changing and generation > 1:
            prefix = holder_prefix(restaurant.meta)
            result = land_result(restaurant.meta["projectRoot"], "owner", "--prefix", prefix, "--generation", str(generation))
            error = result.stderr.strip().removeprefix("land: ")
            # A project with no landing contract has no leases to fence.
            if result.returncode != 0 and "has no landing contract" not in error and "not a git repository" not in error:
                raise BrigadeError(f"thread not replaced: could not raise the landing floor for {prefix}: {error}")
        with restaurant.checked():
            if next_owner(restaurant.meta, args.thread, args.replace, args.expect, args.stopped) != target:
                continue
            # Recording the same thread again is an ordinary write, so it keeps the fence.
            restaurant.unfenced = changing
            return command(restaurant, args)


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


def display_root(path):
    path = Path(path)
    try:
        relative = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    if not relative.parts:
        return "~"
    return "~/" + relative.as_posix()


def leases_for(listing, prefix):
    """Lease ids from one `lease list`, held under this store's prefix. A failed listing holds none."""
    if listing.returncode != 0:
        return []
    found = []
    for line in listing.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3 or not re.fullmatch(r"L\d+", parts[0]):
            continue
        holder = parts[2]
        if holder.startswith(prefix):
            found.append(f"{parts[0]} ({holder.removeprefix(prefix)})")
    return found


def walk(root, stale_hours=24, repo=None):
    """Coordinators grouped by projectRoot. One land.py status and one lease list per root, outside any store lock."""
    wanted = str(Path(repo).resolve()) if repo is not None else None
    groups = {}
    for meta_path in sorted(root.glob("*/*/restaurant.json")):
        restaurant = Restaurant(meta_path.parent)
        project = restaurant.meta.get("projectRoot", "")
        if wanted is not None and project != wanted:
            continue
        groups.setdefault(project, []).append(restaurant)
    if not groups:
        if wanted is not None:
            return f"no restaurants for {wanted}", []
        return f"no restaurants under {root}", []
    lines, failures = [], []
    for project in sorted(groups):
        # The executive admin comes first in its repository.
        coordinators = sorted(groups[project], key=lambda restaurant: (not is_admin(restaurant.meta),
                                                                       str(restaurant.dir / "restaurant.json")))
        status = land_result(project, "status")
        listing = land_result(project, "lease", "list")
        header = status.stdout.strip() if status.returncode == 0 else "no landing contract"
        lines.append(f"{display_root(project)}: {header}")
        for restaurant in coordinators:
            meta = restaurant.meta
            age = datetime.now(timezone.utc) - datetime.fromisoformat(meta["lastActivityAt"])
            idle = f", idle {int(age.total_seconds() // 3600)}h" if age.total_seconds() > stale_hours * 3600 else ""
            try:
                counts = status_line(restaurant)
                blocked = len(holding_blocks(restaurant))
                questions = [row for row in restaurant.rows("86.tsv") if row["state"] == "open"]
            except MalformedTable as error:
                lines.append(f"  {meta['restaurant']}: {error}")
                failures.append(str(error))
                continue
            if blocked:
                counts = re.sub(r"(waiting tickets: \d+)", rf"\1 ({blocked} blocked)", counts, count=1)
            mode = f", mode {meta['mode']}" if meta.get("mode") else ""
            lines.append(f"  {meta['restaurant']} (reports {reporting_of(meta)}{mode}){idle}: {counts}")
            leases = leases_for(listing, holder_prefix(meta))
            lease_text = f", leases {', '.join(leases)}" if leases else ""
            lines.append(f"    thread {meta.get('thread') or 'not recorded'}{lease_text}")
            for question in questions:
                lines.append(f"    {question['id']}: {question['question']}")
    return "\n".join(lines), failures


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
    rows.append({"id": ident, "at": now(), "kind": kind, "parties": ", ".join(intake_list(parties)), "question": question,
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
                admin.append("log.tsv", {"at": now(), "kind": "relay", "id": ident, "state": "copied",
                                         "note": json.dumps(row, separators=(",", ":"))})
                lines.append(f"{name} {row['at']} {row['kind']} {row['id']} {row['state']}: {row['note']}")
            # A sync that started later may have read further and moved this cursor already.
            cursors[store] = max(end, int(cursors.get(store, 0)))
        admin.change_meta(cursors=cursors, lastActivityAt=now())
    return "\n".join(lines), failures


def admin_sections(admin, since):
    """The admin's report adds the rulings recorded or changed since the last one, each coordinator's relayed
    events, and each coordinator's newest report."""
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
        rows = by_store.get(store_path(directory), [])
        if not rows and not closeouts:
            continue
        blocks += [f"### {name}", "", *(f"- {row['at']} {row['kind']} {row['id']} {row['state']}: {row['note']}" for row in rows)]
        blocks += [f"- Newest report: {closeouts[-1]}"] if closeouts else []
        blocks.append("")
    if blocks:
        lines += ["## From each coordinator", "", *blocks]
    return lines


@dataclass(frozen=True)
class Run:
    argv: tuple


@dataclass(frozen=True)
class See:
    words: str


@dataclass(frozen=True)
class If:
    condition: str
    action: str


@dataclass(frozen=True)
class Reply:
    yes: bool
    occasion: str
    detail: str
    close: tuple


@dataclass(frozen=True)
class Trigger:
    wake: str
    dish: str = ""
    request: str = ""
    verdict: str = ""
    result: str = ""
    question: bool = False


@dataclass(frozen=True)
class Debt:
    dish: str
    count: int
    at: str


@dataclass(frozen=True)
class Snapshot:
    """One read of the store. It holds no plan. warn prints on standard error and writes nothing."""

    reporting: str
    reports_to: str
    last_report_at: str
    owner_block: str
    restaurant: str
    intake: tuple
    dishes: tuple
    tickets: tuple
    decisions: tuple
    debts: tuple
    round_counts: dict
    events: tuple
    counts: dict
    progress: tuple
    unrecorded: tuple
    inbox: tuple
    requests: tuple
    passes: dict
    blocked_since: tuple
    moment: datetime
    close_refusal: str
    warn: object
    place: str
    report_gaps: tuple


def dish_by_id(snapshot, ident):
    for dish in snapshot.dishes:
        if dish["id"] == ident:
            return dish
    return None


def open_for(snapshot, ident):
    for row in snapshot.decisions:
        if row["dish"] == ident:
            return row
    return None


def lowest_id(idents, warn):
    """The lowest id. A suffix that is not a base-10 integer warns and sorts after every numeric suffix."""
    ranked = []
    for ident in idents:
        try:
            ranked.append((0, int(ident[1:], 10), ident))
        except ValueError:
            warn(f"{ident} has no base-10 id suffix")
            ranked.append((1, 0, ident))
    return min(ranked)[2]


def crossing_send_back(restaurant, dish_id):
    """The at of the send-back that reached ROUND_BUDGET, using the same rows round_count counts."""
    verdicts = item_verdicts(restaurant.rows("pass.tsv"), dish_id)
    settled = [row["at"] for row in verdicts if row["verdict"] == "pass"]
    settled += [row["answered"] for row in restaurant.rows("86.tsv")
                if row["dish"] == dish_id and row["kind"] == ROUND_KIND and row["state"] == "answered"]
    since = max(settled, default="")
    backs = [row["at"] for row in verdicts if row["verdict"] == "send-back" and row["at"] > since]
    if len(backs) < ROUND_BUDGET:
        return ""
    return backs[ROUND_BUDGET - 1]


def load_inbox(restaurant):
    """Each json handoff take_tickets would read, as (name, ok, problem). A bad file is not a traceback."""
    found = []
    inbox = restaurant.dir / "inbox"
    for path in sorted(inbox.glob("*.json")):
        try:
            raw = path.read_text()
        except (OSError, UnicodeError) as error:
            found.append((path.name, False, f"could not be read ({error})"))
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            found.append((path.name, False, f"is not valid JSON ({error.msg})"))
            continue
        if not isinstance(data, dict):
            found.append((path.name, False, "is not a JSON object"))
            continue
        missing = [key for key in ("handoff", "source", "ref", "summary") if key not in data]
        if missing:
            found.append((path.name, False, f"has no {missing[0]} key"))
            continue
        found.append((path.name, True, ""))
    return tuple(found)


def report_is_current(restaurant, dish):
    """True when reports/<id>.md exists and its mtime is at least the last attempt start."""
    path = restaurant.dir / "reports" / f"{dish['id']}.md"
    if not path.exists():
        return False
    written = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return written >= attempt_starts(restaurant, dish)[-1]


def counted_timebox(restaurant, dish):
    """The row next passes to dish_progress. An empty timebox stays empty. A timebox that is not a base-10 integer warns and reads as 60."""
    cell = dish.get("timebox") or ""
    if cell == "":
        return dish
    try:
        int(cell, 10)
    except ValueError:
        restaurant.warn(f"{dish['id']} timebox {cell} is not a base-10 integer")
        return {**dish, "timebox": "60"}
    return dish


def malformed_close(restaurant):
    """The first malformed row rows names, in TABLES order, or empty when every table parses."""
    for table in TABLES:
        try:
            restaurant.rows(table)
        except MalformedTable as error:
            return str(error)
    return ""


def load_snapshot(restaurant, moment):
    """Read the store under the caller's lock. The caller already refused a bad owner."""
    read_labels(restaurant, EVERY_ROW)
    meta = restaurant.meta
    since = meta.get("lastReportAt") or ""
    dishes = tuple(restaurant.rows("dishes.tsv"))
    tickets = tuple(restaurant.rows("rail.tsv"))
    decisions = tuple(row for row in restaurant.rows("86.tsv") if row["state"] == "open")
    open_dishes = {row["dish"] for row in decisions if row["dish"]}
    progress = []
    gaps = []
    for dish in dishes:
        if dish["state"] != "in-progress" or dish.get("reported") != "yes":
            continue
        held = dish["id"] in open_dishes
        if not held:
            dish = counted_timebox(restaurant, dish)
        if not report_is_current(restaurant, dish):
            gaps.append((dish["id"], worker_where(dish)))
            continue
        for sentence in dish_progress(restaurant, dish, moment, held):
            if "no report-back" in sentence:
                continue
            if sentence.startswith(f"{dish['id']}: reported ") or sentence.startswith(f"{dish['id']}: report written "):
                progress.append((dish["id"], sentence))
    debts = []
    round_counts = {}
    for dish in dishes:
        round_counts[dish["id"]] = round_count(restaurant, dish["id"])
        count = round_debt(restaurant, dish)
        if count:
            debts.append(Debt(dish["id"], count, crossing_send_back(restaurant, dish["id"])))
    passes = {}
    for dish in dishes:
        sha = dish.get("sha") or ""
        if dish["state"] == "queued" or sha:
            ok, why = pass_check(restaurant, dish["id"], sha)
            passes[dish["id"]] = (ok, why)
    blocked_since = {}
    for event in restaurant.rows("log.tsv"):
        if event["kind"] == "ticket" and event["state"] == "blocked" and event["id"] not in blocked_since:
            blocked_since[event["id"]] = event["at"]
    pending = tuple((path.stem, path.read_text().strip()) for path in request_files(restaurant)[0])
    block = restaurant.owner_block() or ""
    return Snapshot(
        reporting_of(meta), meta.get("reportsTo") or "", since, block, slug(meta["restaurant"]),
        tuple(meta.get("intake") or ()), dishes, tickets, decisions, tuple(debts), round_counts,
        tuple(events_since(restaurant, since).values()), counts(restaurant), tuple(progress),
        tuple(unrecorded_reviews(restaurant)), load_inbox(restaurant), pending, passes,
        tuple(blocked_since.items()), moment, malformed_close(restaurant), restaurant.warn,
        store_path(restaurant.dir), tuple(gaps))


def inbox_ready(snapshot):
    """True when inbox take would read every json handoff and there is something to take."""
    if any(not ok for _, ok, _ in snapshot.inbox):
        return False
    return bool(snapshot.inbox) or bool(snapshot.requests)


def accepted_argv(snapshot, argv):
    """The argv when that command would exit 0 on this store. None means this sheet prints no run of it."""
    if snapshot.owner_block:
        return None
    if argv == ("inbox", "take"):
        return argv if inbox_ready(snapshot) else None
    if argv[:2] == ("ticket", "paths"):
        return None
    if len(argv) == 3 and argv[0] == "dish" and argv[2] == "--reported":
        dish = dish_by_id(snapshot, argv[1])
        if dish and dish["state"] == "in-progress" and dish.get("reported") != "yes":
            return argv
        return None
    if len(argv) == 5 and argv[0] == "86" and argv[1] == "add" and argv[2] == "--dish" and argv[4] == "--round-budget":
        debt = next((row for row in snapshot.debts if row.dish == argv[3]), None)
        if debt and not open_for(snapshot, debt.dish) and debt.at > snapshot.last_report_at:
            return argv
        return None
    if argv in (("close",), ("close", "--to-file")):
        return None if snapshot.close_refusal else argv
    if len(argv) == 4 and argv[0] == "dish" and argv[2:] == ("--state", "merged"):
        dish = dish_by_id(snapshot, argv[1])
        passed = snapshot.passes.get(argv[1])
        if dish and dish["state"] == "queued" and passed and passed[0]:
            return argv
        return None
    return None


def maybe_run(snapshot, argv):
    accepted = accepted_argv(snapshot, argv)
    if accepted is None:
        return None
    return Run(tuple(accepted))


def routine_detail(trigger):
    if trigger.wake == "review":
        return trigger.verdict
    return ROUTINE_DETAILS.get(trigger.wake, trigger.wake)


def fresh_decision_ids(snapshot):
    """New question ids, new decision-ticket ids, and dishes that owe a raise."""
    since = snapshot.last_report_at
    questions = [row["id"] for row in snapshot.decisions if row["at"] > since]
    tickets = [row["id"] for row in snapshot.tickets
               if row["state"] in ("waiting", "assigned") and row.get("decision") == "yes" and row["at"] > since]
    dishes = [debt.dish for debt in snapshot.debts if debt.at > since and not open_for(snapshot, debt.dish)]
    return questions, tickets, dishes


def budget_reached(snapshot, trigger):
    if trigger.wake != "review" or trigger.verdict not in ("send-back", "pass-with-edits"):
        return False
    if open_for(snapshot, trigger.dish):
        return False
    count = snapshot.round_counts.get(trigger.dish, 0)
    if trigger.verdict == "send-back":
        count += 1
    return count >= ROUND_BUDGET


def blocked_needs_user(snapshot, trigger, failure):
    return (trigger.wake == "review" and trigger.verdict == "blocked" and failure != "fixable"
            and not open_for(snapshot, trigger.dish))


def decision_holds(snapshot, trigger, failure):
    questions, tickets, dishes = fresh_decision_ids(snapshot)
    if questions or tickets or dishes:
        return True
    if budget_reached(snapshot, trigger):
        return True
    return blocked_needs_user(snapshot, trigger, failure)


def decision_detail(snapshot, trigger, failure):
    questions, tickets, dishes = fresh_decision_ids(snapshot)
    if budget_reached(snapshot, trigger) or blocked_needs_user(snapshot, trigger, failure):
        dishes = [*dishes, trigger.dish]
    if questions:
        return lowest_id(questions, snapshot.warn)
    if tickets:
        return lowest_id(tickets, snapshot.warn)
    if dishes:
        return lowest_id(dishes, snapshot.warn)
    return "decision"


def merge_accepted(snapshot, trigger):
    return trigger.wake == "merge" and maybe_run(snapshot, ("dish", trigger.dish, "--state", "merged")) is not None


def is_drained(snapshot, trigger):
    """The four counts are zero after an accepted merge claim, and the claim or a newer merged or dropped row exists."""
    if not snapshot.dishes and not snapshot.tickets:
        return False
    counts = dict(snapshot.counts)
    claimed = merge_accepted(snapshot, trigger)
    if claimed:
        counts["waiting to land"] = max(0, counts.get("waiting to land", 0) - 1)
    if any(counts.get(label, 0) for label in DRAIN_LABELS):
        return False
    newer = any(event["kind"] == "dish" and event["state"] in ("merged", "dropped") for event in snapshot.events)
    return bool(newer or claimed)


def request_text(snapshot, trigger):
    for ident, text in snapshot.requests:
        if ident == trigger.request:
            return text
    return ""


def from_user_request(snapshot, trigger):
    return trigger.wake == "admin" and request_text(snapshot, trigger).startswith("from-user ")


def note_unsent(holding, unsent, failure):
    if not unsent or unsent == "misrouted":
        return
    if unsent == "blocked":
        if failure != "stuck":
            holding["failure"] = ("unsent blocked", False)
        return
    holding[unsent] = (f"unsent {unsent}", unsent in MESSAGE_ONLY)


def held_occasions(snapshot, trigger, failure, unsent):
    """The occasions this wake holds, earliest first. A forced pair is yes at every level."""
    holding = {}
    if trigger.wake == "user" or (from_user_request(snapshot, trigger) and not snapshot.reports_to):
        detail = "question" if trigger.wake == "user" and trigger.question else "message"
        holding["user"] = (detail, False)
    if decision_holds(snapshot, trigger, failure):
        holding["decision"] = (decision_detail(snapshot, trigger, failure), False)
    if trigger.wake == "evening":
        holding["evening"] = ("report", False)
    if snapshot.reporting == "digest" and is_drained(snapshot, trigger):
        holding["drained"] = ("batch", False)
    if merge_accepted(snapshot, trigger):
        holding["merged"] = (trigger.dish, False)
    if trigger.wake == "review" and trigger.verdict in ("send-back", "pass-with-edits") and not budget_reached(snapshot, trigger):
        holding["sent-back"] = (trigger.dish, False)
    if trigger.wake == "review" and trigger.verdict == "blocked" and not blocked_needs_user(snapshot, trigger, failure):
        if failure != "fixable":
            holding["blocked"] = (trigger.dish, False)
    if failure == "stuck":
        holding["stuck"] = ("stuck", False)
    if failure == "fixable" or (trigger.wake == "checks" and trigger.result == "fail" and not failure):
        holding["failure"] = ("fixable" if failure == "fixable" else "fail", False)
    note_unsent(holding, unsent, failure)
    found = [(name, *holding[name]) for name in OCCASION_ORDER if name in holding]
    for name, pair in holding.items():
        if name not in OCCASION_ORDER:
            found.append((name, *pair))
    if snapshot.reporting == "every-turn" and not found:
        found.append(("every-turn", "wake", False))
    return found


def occasion_yes(level, occasion, forced):
    if forced:
        return True
    return occasion in YES_OCCASIONS[level]


def reply_for(snapshot, trigger, failure, unsent):
    """The one reply. A store that reports to the executive admin uses that rule first, unless --unsent is set. The reply is yes when any held occasion qualifies at this reporting level. The printed occasion is the first qualifying one, and the first held occasion when none qualify."""
    if snapshot.reports_to and not unsent:
        close = ("close", "--to-file") if trigger.wake == "evening" or is_drained(snapshot, trigger) else None
        if trigger.wake == "user":
            return Reply(True, "user", "question" if trigger.question else "message", None)
        return Reply(False, "reports to the executive admin", routine_detail(trigger), close)
    found = held_occasions(snapshot, trigger, failure, unsent)
    if not found:
        return Reply(False, "routine", routine_detail(trigger), None)
    qualifying = [(name, detail) for name, detail, flag in found if occasion_yes(snapshot.reporting, name, flag)]
    if qualifying:
        occasion, detail = qualifying[0]
        yes = True
    else:
        occasion, detail = found[0][0], found[0][1]
        yes = False
    close = None
    if snapshot.reporting == "digest" and yes:
        close = ("close", "--to-file")
    elif any(name in CLOSE_OCCASIONS for name, _detail in qualifying):
        close = ("close",)
    return Reply(yes, occasion, detail, close)


def wake_label(trigger):
    if trigger.wake in ("report-back", "review", "checks", "merge"):
        return f"{trigger.wake} {trigger.dish}"
    if trigger.wake == "admin":
        return f"admin {trigger.request}"
    return trigger.wake


def cue_line(cue):
    if isinstance(cue, Run):
        return "run " + " ".join(shlex.quote(word) for word in cue.argv)
    if isinstance(cue, See):
        return f"see {cue.words}"
    return f"if {cue.condition}: {cue.action}"


def format_sheet(snapshot, trigger, cues, reply):
    lines = [f"wake: {wake_label(trigger)}", f"reporting: {snapshot.reporting}"]
    if snapshot.reports_to:
        lines.append(f"reports to: {snapshot.reports_to}")
    lines.extend(cue_line(cue) for cue in cues)
    if not cues:
        lines.append("nothing else owed")
    token = "yes" if reply.yes else "no"
    lines.append(f"reply: {token} ({reply.occasion}: {reply.detail})")
    return "\n".join(lines)


def holder_of(snapshot, dish_id):
    return f"{snapshot.restaurant}/{dish_id}"


def land_follow(dish_id):
    return If(f"land or watch reports {dish_id} landed", f"run next merge {dish_id}")


def pause_if():
    return If("the queue is paused and this thread cannot fix it and this is not a bounce handed to a fresh worker and --failure stuck was not passed",
              "run next --failure stuck")


def pass_words(snapshot, dish):
    branch = dish.get("branch") or "none"
    sha = dish.get("sha") or "none"
    lease = dish.get("lease") or "none"
    return (f"File follow-ups from reports/{dish['id']}.md, write the pull request file, submit, and land. "
            f"Holder {holder_of(snapshot, dish['id'])}, branch {branch}, sha {sha}, lease {lease}.")


def merge_why(snapshot, dish):
    if dish["state"] != "queued":
        return f"{dish['id']} is {dish['state']}, not queued"
    ok, why = snapshot.passes.get(dish["id"], (False, f"{dish['id']} has no review verdict for {dish.get('sha') or ''}"))
    if not ok:
        return f"only reviewed work lands: {why}"
    return f"{dish['id']} cannot be marked merged"


def housekeeping(dish):
    thread = dish.get("thread") or "none"
    branch = dish.get("branch") or "none"
    pr = dish.get("pr") or "none"
    return (f"Finish with the old worker for thread {thread}, branch {branch}, and pull request {pr} for {dish['id']}. "
            f"If its run is still open, interrupt it and hang the attempt, then archive it. "
            f"Remove the worktree and delete the branch after that. Do not claim they are already gone.")


def prefix_cues(snapshot, trigger, failure):
    wake, dish_id = trigger.wake, trigger.dish
    if wake == "report-back":
        cues = []
        run = maybe_run(snapshot, ("dish", dish_id, "--reported"))
        if run:
            cues.append(run)
        owes = any(debt.dish == dish_id and debt.at > snapshot.last_report_at for debt in snapshot.debts)
        if owes or open_for(snapshot, dish_id):
            return cues
        cues.append(See(f"Read reports/{dish_id}.md and the diff. t3_thread_read the worker thread. "
                        f"If that run is still open, interrupt it, hang the attempt, then review per Run a service step 6."))
        return cues
    if wake == "review":
        if open_for(snapshot, dish_id):
            return []
        dish = dish_by_id(snapshot, dish_id)
        files = [name for name, _kind in snapshot.unrecorded if name.startswith(f"{dish_id}-review")]
        extra = f" Unrecorded review file {files[0]}." if files else ""
        sha = dish.get("sha") or ""
        if sha:
            recorded = f"Record pass record for {dish_id} at sha {sha} with verdict {trigger.verdict}.{extra}"
        else:
            recorded = f"Record pass record for {dish_id} with verdict {trigger.verdict}. No sha is recorded.{extra}"
        cues = [See(recorded)]
        if trigger.verdict in ("send-back", "pass-with-edits"):
            run = maybe_run(snapshot, ("86", "add", "--dish", dish_id, "--round-budget"))
            already = snapshot.round_counts.get(dish_id, 0) >= ROUND_BUDGET
            if not run and not already and budget_reached(snapshot, trigger):
                cues.append(If(f"{dish_id} reaches the round budget and no decision is open",
                               f"run 86 add --dish {dish_id} --round-budget"))
            elif not run and not already:
                cues.append(If(f"the old worker's wait for {dish_id} is terminal and {dish_id} may take a fix round",
                               f"run dish {dish_id} --state in-progress"))
        elif trigger.verdict == "pass":
            cues.append(See(pass_words(snapshot, dish)))
        elif trigger.verdict == "blocked" and failure != "fixable":
            cues.append(See(f"Only the user can unblock {dish_id}. Ask with 86 add --dish {dish_id} --question --options --default."))
        return cues
    if wake == "checks":
        dish = dish_by_id(snapshot, dish_id)
        cues = [See(f"Run land for holder {holder_of(snapshot, dish_id)}.")]
        if dish.get("pr"):
            cues.append(See(f"watch_pull_request {dish['pr']}."))
        cues.append(land_follow(dish_id))
        if trigger.result == "fail" and failure != "stuck" and snapshot.reporting == "digest":
            cues.append(If("this thread cannot fix the failed check",
                           f"run next checks {dish_id} --result fail --failure stuck"))
        return cues
    if wake == "merge":
        dish = dish_by_id(snapshot, dish_id)
        run = maybe_run(snapshot, ("dish", dish_id, "--state", "merged"))
        cues = [run] if run else [See(merge_why(snapshot, dish))]
        cues.append(See(housekeeping(dish)))
        return cues
    if wake == "liveness":
        cues = [See("watch")]
        for dish in snapshot.dishes:
            if dish["state"] == "queued":
                cues.append(land_follow(dish["id"]))
        cues.append(pause_if())
        cues.append(If("watch printed no work in progress", "run set --schedule liveness="))
        return cues
    if wake in ("intake", "morning"):
        if snapshot.intake:
            listed = ", ".join(snapshot.intake)
            cues = [See(f"Intake sources are {listed}. File tickets from those sources with ticket add.")]
        else:
            cues = [See("No intake source is recorded. File a user request with ticket add when the message asks for work.")]
        if wake == "morning":
            cues.append(See("Morning service is not a report."))
        return cues
    if wake == "evening":
        return [See("Write the evening report for this reporting level. At digest, plain sentences and the closeout path. "
                    "Otherwise at most three sentences and then the close output.")]
    if wake == "user":
        first = "Answer the question first." if trigger.question else "Answer the user first."
        return [See(first), See("File the work the message asks for with ticket add.")]
    if wake == "admin":
        text = request_text(snapshot, trigger)
        cues = [See(f"{trigger.request}: {text}")]
        if text.startswith("from-user "):
            cues.append(See(f"Treat {trigger.request} as a message from the user. File its work with ticket add --request {trigger.request}. "
                            f"Answer with the reply line."))
        elif text.startswith("answer "):
            cues.append(See(f"Run 86 answer for the decision {trigger.request} names."))
        elif text.startswith("reports-to "):
            cues.append(See(f"Run set --reports-to with the thread {trigger.request} names, or clear it when the thread is none."))
        elif text.startswith("ruling "):
            cues.append(See(f"Read the ruling {trigger.request} names. Carry it out when it is in force."))
        else:
            cues.append(See(f"{text} matches no row of the Requests table."))
        cues.append(If(f"the action for {trigger.request} is done", f"run inbox done {trigger.request}"))
        return cues
    return []


def older_than_hour(at, moment):
    try:
        stamp = datetime.fromisoformat(at)
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return moment - stamp > timedelta(hours=1)


def admin_sends(snapshot, trigger, failure, after_close):
    """Owed event lines for the admin thread. report and drained follow the close cue.

    A blocked row is an admin event only while that ticket's rail state is waiting.
    """
    if not snapshot.reports_to:
        return []
    thread = snapshot.reports_to
    name = snapshot.restaurant

    def send(line):
        return See(f"t3_thread_send {thread} mode queue: {line}")

    if after_close:
        cues = []
        if trigger.wake == "evening":
            cues.append(send(f"report {name}: the path close --to-file prints"))
        if is_drained(snapshot, trigger):
            cues.append(send(f"drained {name}: the path close --to-file prints"))
        return cues
    cues = []
    if merge_accepted(snapshot, trigger):
        dish = dish_by_id(snapshot, trigger.dish)
        title = dish.get("summary") or trigger.dish
        pr = f" [{dish['pr']}]" if dish.get("pr") else ""
        cues.append(send(f"merged {name}: {title}{pr}"))
    if trigger.wake == "review" and trigger.verdict in ("send-back", "pass-with-edits", "blocked"):
        cues.append(send(f"sent-back {name} {trigger.dish}: {trigger.verdict}"))
    if decision_holds(snapshot, trigger, failure):
        questions, _, _ = fresh_decision_ids(snapshot)
        if questions:
            row = next(item for item in snapshot.decisions if item["id"] == lowest_id(questions, snapshot.warn))
            cues.append(send(f"decision {name} {row['id']}: {row['question']} Options: {row['options']}. Default: {row['default']}."))
        else:
            cues.append(send(f"decision {name}: sent after 86 add prints the id"))
    if failure == "stuck":
        cues.append(send(f"failed {name}: cannot fix this"))
    waiting = {row["id"] for row in snapshot.tickets if row["state"] == "waiting"}
    for ident, at in snapshot.blocked_since:
        if ident not in waiting or not older_than_hour(at, snapshot.moment):
            continue
        cues.append(If(f"watch still shows {ident} blocked",
                       f"t3_thread_send {thread} mode queue clientRequestId blocked:{snapshot.place}/{ident}: "
                       f"blocked {name} {ident}: waiting since {at}. Lease ids are not in the store."))
    if from_user_request(snapshot, trigger):
        cues.append(send(f"reply {name}: sent after the answer"))
    return cues


def tail_cues(snapshot, trigger, failure, reply):
    named = {trigger.dish} if trigger.dish and trigger.wake in ("report-back", "review", "checks", "merge") else set()
    if trigger.wake == "liveness":
        named.update(dish["id"] for dish in snapshot.dishes if dish["state"] == "queued")
    cues = []
    for row in snapshot.decisions:
        if not row["dish"]:
            cues.append(See(f"{row['id']}: {row['question']}. Launch nothing until 86 answer {row['id']}."))
            continue
        cues.append(See(f"{row['dish']}: open decision {row['id']}: {row['question']}; "
                        f"launch no worker or verifier until 86 answer {row['id']}"))
    held = {row["dish"] for row in snapshot.decisions if row["dish"]}
    for dish_id, sentence in snapshot.progress:
        if dish_id not in named and dish_id not in held:
            cues.append(See(sentence))
    for dish_id, where in snapshot.report_gaps:
        if dish_id in held:
            continue
        cues.append(If(f"reports/{dish_id}.md is absent or older than the last attempt start",
                       f"read the worker thread ({where}) and get the report"))
    seen = set()
    for debt in snapshot.debts:
        run = maybe_run(snapshot, ("86", "add", "--dish", debt.dish, "--round-budget"))
        if run and run.argv not in seen:
            seen.add(run.argv)
            cues.append(run)
    for dish in snapshot.dishes:
        if dish["state"] != "passed" or dish["id"] in named:
            continue
        cues.append(See(f"{dish['id']}: passed, not submitted"))
        if dish.get("sha"):
            cues.append(If(f"the landing queue has an entry for {dish['id']}",
                           f"run land.py status --holder {holder_of(snapshot, dish['id'])} --sha {dish['sha']}"))
    queued = [dish for dish in snapshot.dishes if dish["state"] == "queued"]
    for dish in queued:
        if dish["id"] in named:
            continue
        cues.append(See(f"Run land for {dish['id']}."))
        cues.append(land_follow(dish["id"]))
    claiming = trigger.dish if merge_accepted(snapshot, trigger) else ""
    for event in snapshot.events:
        if event["kind"] == "dish" and event["state"] == "merged" and event["id"] != claiming:
            cues.append(See(f"{event['id']} merged and not closed out"))
    if any(row["state"] == "waiting" for row in snapshot.tickets):
        cues.append(See("Run startable and take the first ticket it prints as startable, "
                        "with every ticket it prints as rides with that ticket."))
    if queued and trigger.wake != "liveness":
        cues.append(pause_if())
    cues.extend(admin_sends(snapshot, trigger, failure, False))
    if reply.close:
        if snapshot.close_refusal:
            cues.append(See(snapshot.close_refusal))
        else:
            cues.append(Run(reply.close))
    cues.extend(admin_sends(snapshot, trigger, failure, True))
    return cues


def inbox_cues(snapshot):
    bad = [(name, problem) for name, ok, problem in snapshot.inbox if not ok]
    if bad:
        return [See(f"inbox/{name} {problem}, so inbox take would stop") for name, problem in bad]
    run = maybe_run(snapshot, ("inbox", "take"))
    return [run] if run else []


def compile_sheet(snapshot, trigger, failure, unsent):
    """The cues for this wake. The reply's close line comes before a report or drained line. It is a run when close would exit 0, and a see of the malformed row when close would refuse."""
    reply = reply_for(snapshot, trigger, failure, unsent)
    return [*inbox_cues(snapshot), *prefix_cues(snapshot, trigger, failure), *tail_cues(snapshot, trigger, failure, reply)]


def trigger_from(args):
    return Trigger(args.wake or "unspecified", getattr(args, "dish", "") or "", getattr(args, "request", "") or "",
                   getattr(args, "verdict", "") or "", getattr(args, "result", "") or "", bool(getattr(args, "question", False)))


def next_text(restaurant, args):
    """Print the steps this wake owes and one reply. The flag makes a write or land.py raise."""
    global READ_ONLY
    READ_ONLY = True
    try:
        trigger = trigger_from(args)
        failure = getattr(args, "failure", None)
        unsent = getattr(args, "unsent", None)
        with restaurant.read_lock(create=False):
            block = restaurant.owner_block()
            if block:
                raise BrigadeError(block)
            if trigger.dish:
                restaurant.find("dishes.tsv", trigger.dish)
            if trigger.request:
                pending = {path.stem for path in request_files(restaurant)[0]}
                if trigger.request not in pending:
                    raise BrigadeError(f"no request {trigger.request} in the inbox")
            snapshot = load_snapshot(restaurant, datetime.now(timezone.utc))
        reply = reply_for(snapshot, trigger, failure, unsent)
        return format_sheet(snapshot, trigger, compile_sheet(snapshot, trigger, failure, unsent), reply)
    finally:
        READ_ONLY = False


def parser():
    top = argparse.ArgumentParser(prog="brigade.py", description=__doc__.splitlines()[0])
    top.add_argument("--store", help="store root (default $BRIGADE_STORE or $XDG_STATE_HOME/pstack-t3/brigade)")
    top.add_argument("--at", default=os.environ.get("BRIGADE_DIR"), help="restaurant directory (default $BRIGADE_DIR)")
    top.add_argument("--owner", help="<thread>@<generation> from status; every write in a store with a generation needs it")
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("open", help="create a restaurant, or print an existing one")
    p.add_argument("--project-root", required=True)
    p.add_argument("--name")
    p.add_argument("--admin", action="store_true", help="open the repository's executive admin at <project>/.admin; takes no --name")
    p.add_argument("--reporting", choices=REPORTING, default="milestones",
                   help="how often the coordinator replies (default: milestones)")
    p.add_argument("--intake", default="", help="comma-separated intake sources this coordinator owns, such as github")
    p.add_argument("--workers", type=int, help="how many dishes may be in progress or in review; missing reads as 2")
    p.add_argument("--mode", choices=MODES, help="this restaurant's light or full mode; missing leaves it to the roles files")

    p = sub.add_parser("set", help="record the head chef thread, a schedule id, or the reporting level")
    p.add_argument("--thread")
    p.add_argument("--replace", action="store_true", help="replace a recorded coordinator thread")
    p.add_argument("--expect", help="executive admin only: replace the thread only while it is still this value")
    p.add_argument("--stopped", help="with --expect recovering:<id>: the run id t3_thread_wait reported terminal, idle, or gone")
    p.add_argument("--reports-to", help="the executive admin's thread this coordinator reports to; \"\" clears it")
    p.add_argument("--schedule", action="append", default=[], metavar="NAME=ID")
    p.add_argument("--reporting", choices=REPORTING, help="how often the coordinator replies")
    p.add_argument("--intake", help="comma-separated intake sources this coordinator owns; replaces the list, and \"\" clears it")
    p.add_argument("--workers", type=int, help="how many dishes may be in progress or in review")
    p.add_argument("--mode", help="full or light from the next brief; \"\" leaves it to the roles files")
    p.add_argument("--build-command",
                   help="the build command edits verify runs; none records that the project has no build, and \"\" removes "
                        "the setting")
    p.add_argument("--check-command",
                   help="the check command edits verify runs; none records that the project has no check, and \"\" removes "
                        "the setting")
    p.add_argument("--autofire", choices=AUTOFIRE,
                   help="the lowest ticket priority the startable command lists as startable, or off; missing reads as normal")

    p = sub.add_parser("ticket", help="add, list, update, move, or take tickets on the rail")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("add")
    how = a.add_mutually_exclusive_group(required=True)
    how.add_argument("--summary")
    how.add_argument("--from-report", metavar="FILE",
                     help="an item report under reports/, such as reports/D2.md; files one waiting ticket per follow-up in it "
                          "and changes only the ticket table, the log, and lastActivityAt; each ticket takes the goal of the "
                          "report's item, and when that item has a kind the ticket takes fix at priority urgent or normal "
                          "and polish at low; a follow-up is filed with no --priority, so it is low")
    a.add_argument("--dry-run", action="store_true", help="with --from-report: print what it would add; it changes no table and no log")
    a.add_argument("--source", default="user")
    a.add_argument("--ref", default="")
    a.add_argument("--request", default="", help="the admin request id this ticket carries out; refuses a second ticket for it")
    a.add_argument("--again", action="store_true",
                   help="with --source user and no --ref: add the ticket even when a waiting or assigned ticket of this store "
                        "has the same summary. Case, leading and trailing whitespace, and the length of a whitespace run "
                        "do not count")
    a.add_argument("--priority", choices=PRIORITIES_BY_RANK,
                   help="missing reads as urgent from upstream, low from report, and normal from every other source")
    a.add_argument("--paths", default="", help="comma-separated files and directories the work will touch")
    a.add_argument("--decision", action="store_true",
                   help="the ticket asks the owner to decide; the startable command never lists it as startable")
    a.add_argument("--kind", choices=KINDS, help="the kind of work; a ticket added with --summary is refused without it")
    a.add_argument("--goal", metavar="G<n>",
                   help="with --summary: the goal this ticket belongs to, which must be open or paused; without it the "
                        "ticket is unassigned")
    a = t.add_parser("list")
    a.add_argument("--state", choices=TICKET_STATES)
    a = t.add_parser("set")
    a.add_argument("id")
    a.add_argument("--state", choices=[state for state in TICKET_STATES if state != "moved"])
    a.add_argument("--priority", choices=PRIORITIES_BY_RANK)
    a.add_argument("--paths", help='replaces the recorded paths; "" clears them')
    a.add_argument("--decision", action=argparse.BooleanOptionalAction, default=None,
                   help="mark or unmark the ticket as one that asks the owner to decide")
    a.add_argument("--kind", choices=KINDS, help="replaces the kind of a waiting ticket; refused on a ticket in another state, "
                        "and on a waiting ticket that an item in flight names")
    goal = a.add_mutually_exclusive_group()
    goal.add_argument("--goal", metavar="G<n>",
                      help="replaces the goal of a waiting ticket with one that is open or paused; refused on a ticket in "
                           "another state, and on a waiting ticket that an item in flight names")
    goal.add_argument("--no-goal", action="store_true",
                      help="clears the goal of a waiting ticket, so it is unassigned; refused on a ticket in another state, "
                           "and on a waiting ticket that an item in flight names")
    a = t.add_parser("move", help="hand a waiting ticket to a sibling coordinator through its inbox")
    a.add_argument("id")
    a.add_argument("--to", required=True, help="the sibling's directory name")
    t.add_parser("take", help="file every ticket a sibling handed to this coordinator")
    a = t.add_parser("paths", help="record the tracked paths each waiting ticket's summary quotes, on tickets that record none; "
                                    "a quote that is no tracked path and that, with one trailing :<line> or :<line>-<line> "
                                    "dropped, holds no slash, no whitespace, and none of * ? [ and is no tracked path records "
                                    "the tracked file of that name when exactly one has that name, and a file under skills/ "
                                    "is not counted when a file of the same name is tracked under t3/")
    a.add_argument("--dry-run", action="store_true", help="print what it would record; it changes no table")

    p = sub.add_parser("goal", help="add, change, or list goals; a goal is a named line of work")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("add", help="record a new open goal and print its id; refused when an open or paused goal has the same "
                                 "name, where case, leading and trailing whitespace, and the length of a whitespace run "
                                 "do not count")
    a.add_argument("--name", required=True, help='what the goal is called, such as "activity view"')
    a.add_argument("--purpose", required=True, help="one sentence that says why this line of work exists")
    a.add_argument("--done", required=True, help="one sentence that states the test that shows the goal is reached")
    a.add_argument("--share", default="",
                   help="a whole number of 1 or more, recorded as the goal's share of the worker slots; goal list prints it, "
                        "and it does not affect which tickets start")
    a = t.add_parser("set", help="change a goal's state, name, purpose, done test, or share")
    a.add_argument("id")
    a.add_argument("--state", choices=GOAL_STATES,
                   help="paused is recorded and printed. It does not affect which tickets start. "
                        "A move to done or to dropped needs --evidence, and is refused while a ticket of the goal is "
                        "waiting, an item of the goal is in progress, in review, passed, sent back, blocked, or waiting "
                        "to land, or an assigned ticket of the goal is not in an item of the goal. "
                        "A done or dropped goal moves only to open.")
    a.add_argument("--evidence",
                   help="one line, stored with the time in the goal's closed cell when the goal moves to done or dropped; "
                        "with --state done, what shows the done test is met; with --state dropped, why the goal is "
                        "dropped; refused without --state done or --state dropped")
    a.add_argument("--name", help="replaces the goal's name; refused when it is empty or only whitespace")
    a.add_argument("--purpose", help="replaces the goal's purpose; refused when it is empty or only whitespace")
    a.add_argument("--done", help="replaces the goal's done test; refused when it is empty or only whitespace")
    a.add_argument("--share",
                   help='a whole number of 1 or more, recorded as the goal\'s share of the worker slots; "" clears it; '
                        "goal list prints it, and it does not affect which tickets start")
    a = t.add_parser("list",
                     help="the state, name, counts, purpose, and done test of each open or paused goal and of each done or "
                          "dropped goal that has a waiting ticket, an item in flight, or a ticket riding along, then the "
                          "counts of unassigned work; waiting counts waiting tickets; in flight counts items in progress, "
                          "in review, passed, sent back, blocked, or waiting to land; merged counts merged items; "
                          "send-backs counts the send-back verdicts on the goal's items, in any state, that are not a "
                          "panel member's row; riding along counts assigned tickets of the goal that are not in an item "
                          "of the goal")
    a.add_argument("--all", action="store_true", help="also print each other done or dropped goal")

    p = sub.add_parser("fire", help="group tickets into one dish and assign it to a station")
    p.add_argument("--tickets", required=True,
                   help="comma-separated ticket ids; the first is the lead ticket; the item takes the lead ticket's goal, "
                        "and its kind when it has one")
    p.add_argument("--station", required=True, help="the pstack playbook the worker runs")
    p.add_argument("--summary", required=True)
    for field in ("task", "thread", "branch"):
        p.add_argument(f"--{field}", default="")
    p.add_argument("--timebox", type=int, default=60, help="minutes before the liveness check flags the dish")
    p.add_argument("--paths", default="", help="paths the dish will change; fire claims a landing lease on them first")
    p.add_argument("--mode", help="full, to start the dish in full mode; needs --reason")
    p.add_argument("--reason", help="one line: why the dish runs in full mode")
    p.add_argument("--kind", choices=KINDS,
                   help="the item's kind when the lead ticket has none or --tickets names no ticket; a lead ticket with no "
                        "kind records it too; refused when the lead ticket has another kind; with no --kind and no kind "
                        "from the lead ticket, station bug-fix or perf-issue gives fix, station investigation gives study, "
                        "and fire is refused on any other station")
    p.add_argument("--ride-along", default="",
                   help="comma-separated ids from --tickets, not the first, that may be under another goal than the first "
                        "ticket's; each must have priority low and must not be a feature; every other ticket after the "
                        "first must have the first ticket's goal or no goal")

    p = sub.add_parser("brief", help="render the worker brief for a dish; refuses when a field or the coordinator thread is missing")
    p.add_argument("id")
    p.add_argument("--fields", help="JSON file of brief fields, or - for stdin")
    p.add_argument("--goal", default=None, help="one sentence: the outcome")
    p.add_argument("--acceptance", action="append", default=[], help="a checkable criterion; repeatable, at least one")
    p.add_argument("--contract", default=None,
                   help="the item's contract, rendered as given under CONTRACT: in the brief; write none when the item has no "
                        "safety, privacy, or integrity property; a brief with no contract is refused")
    p.add_argument("--verify", default=None, help="exact commands that prove it, plus known gotchas")
    p.add_argument("--paths", default=None, help="leased paths, when fire did not claim them")
    p.add_argument("--lease", default=None, help="the landing lease id, when fire did not claim it")
    p.add_argument("--base", default=None, help="the trunk ref the worker's branch starts from, such as origin/main")
    p.add_argument("--context", action="append", default=[], help="a pointer to files, PRs, or upstream reports; repeatable")

    p = sub.add_parser("contract", help="print the contract the dish's last brief stored")
    p.add_argument("id")

    sub.add_parser("watch", help="liveness: which dishes have reports, are running, or are over their timebox")
    sub.add_parser("startable", help="waiting tickets in the order they should start, each with why it can or cannot start now; "
                                     "a low ticket that would take the last idle worker is held back while any urgent "
                                     "or normal ticket is waiting, whether or not that ticket can start now; "
                                     "it starts nothing")

    p = sub.add_parser("hang", help="record one open run after report-back, once per attempt")
    p.add_argument("id")
    p.add_argument("--attempt", type=int, help="an earlier attempt, counted from 1, whose run was left open")
    p.add_argument("--provider", required=True)
    p.add_argument("--minutes", type=int, required=True)

    p = sub.add_parser("dish", help="update a dish")
    p.add_argument("id")
    p.add_argument("--state", choices=DISH_STATES)
    for field in ("task", "thread", "branch", "pr", "sha"):
        p.add_argument(f"--{field}")
    p.add_argument("--timebox", type=int, help="minutes; raise it once for a worker that is still making progress")
    p.add_argument("--reported", action="store_true", help="record that this attempt's report-back arrived")
    p.add_argument("--lease", help="record a lease id claimed again")
    p.add_argument("--paths", help="record the paths that lease covers")
    p.add_argument("--stopped", help="with --state dropped: the run id t3_thread_wait reported terminal, or idle")
    p.add_argument("--mode", help="full, to move open work to full mode once; needs --reason")
    p.add_argument("--reason", help="one line: why the dish moves to full mode")

    p = sub.add_parser("pass", help="record or check a review verdict for a dish at a head SHA")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("record")
    a.add_argument("dish")
    a.add_argument("--pr", default="", help="PR URL; omit for local-only work")
    a.add_argument("--sha", required=True)
    a.add_argument("--verdict", choices=VERDICTS, required=True)
    a.add_argument("--author", required=True, help="provider/model of the worker")
    a.add_argument("--verifier", required=True, help="provider/model of the reviewer")
    a.add_argument("--note", default="")
    a.add_argument("--same-family", action="store_true", help="allow it when no other family is runnable")
    a.add_argument("--report", default="", help="the round's findings file under reports/, such as D2-review-1.md")
    a.add_argument("--edits", default="",
                   help=f"the round's review report under reports/, such as D2-review-1.md; --verdict {EDITS_VERDICT} needs it, "
                        "and no other verdict takes it; refused with one line that says why when its edits cannot be read")
    kind = a.add_mutually_exclusive_group()
    kind.add_argument("--member", action="store_true",
                      help="a panel member's row that does not carry the item's verdict; the dish is left as it is")
    kind.add_argument("--late", action="store_true",
                      help="a round found unrecorded after the dish moved on; the dish is left as it is")
    a = t.add_parser("check")
    a.add_argument("dish")
    a.add_argument("--sha", required=True)
    a.add_argument("--json", action="store_true", help="print verdict, author, verifier, note, and crossFamily of the latest row")

    p = sub.add_parser("edits", help="compare a head with a reviewed commit that has its pass-with-edits round's edits applied and the build run")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("verify",
                     help="apply the edits of the item's last pass-with-edits round at --from to a scratch copy of that "
                          "commit, run the build command there unless it is none, and compare the copy with --to at every "
                          "path either commit holds where the two commits differ or the edits or the build changed the "
                          "copy; when every path agrees, run the check command there unless it is none; a run with no "
                          "failure adds one edits.tsv row for the pair unless an equal row exists; no worktree is read or "
                          "written")
    a.add_argument("dish")
    a.add_argument("--from", dest="from_sha", metavar="SHA", required=True,
                   help="the reviewed commit, as the pass-with-edits row records it")
    a.add_argument("--to", metavar="SHA", required=True, help="the head to compare")

    p = sub.add_parser("86", help="park or answer a decision that needs the user")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("add")
    a.add_argument("--question")
    a.add_argument("--options")
    a.add_argument("--default")
    a.add_argument("--dish", default="")
    a.add_argument("--round-budget", action="store_true",
                   help=f"with --dish: write the decision a dish owes at {ROUND_BUDGET} send-backs; takes no question, options, or default")
    a = t.add_parser("answer")
    a.add_argument("id")
    a.add_argument("--answer", required=True)
    t.add_parser("list")

    p = sub.add_parser("inbox", help="take handed tickets and the admin's requests, or finish a request")
    t = p.add_subparsers(dest="action", required=True)
    t.add_parser("take", help="file handed tickets and print each request file")
    a = t.add_parser("done", help="record a request as acted on and delete its file")
    a.add_argument("id")

    p = sub.add_parser("request", help="executive admin: publish a request line into a coordinator's inbox")
    p.add_argument("line", nargs="?")
    p.add_argument("--to", help="the coordinator's directory name")
    p.add_argument("--republish", action="store_true", help="publish again each request a crash left unwritten")

    p = sub.add_parser("rule", help="executive admin: record, overrule, end, or list rulings")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("add")
    a.add_argument("--kind", choices=RULING_KINDS, required=True)
    a.add_argument("--parties", required=True, help="comma-separated coordinator names")
    a.add_argument("--question", required=True)
    a.add_argument("--rule", choices=RULING_RULES, required=True, help="the rule that decided it")
    a.add_argument("--decision", required=True)
    a.add_argument("--supersedes", default="", help="the ruling this one replaces")
    a = t.add_parser("overrule", help="record the user's ruling in place of this one")
    a.add_argument("id")
    a.add_argument("--decision", required=True)
    a = t.add_parser("set", help="record how a ruling ended")
    a.add_argument("id")
    a.add_argument("--state", choices=("done", "expired"), required=True)
    a = t.add_parser("list")
    a.add_argument("--state", choices=RULING_STATES)

    sub.add_parser("sync", help="executive admin: copy each coordinator's new log rows into this log")

    sub.add_parser("status", help="the thread line first, then counts, then workers outside the executive admin's store, "
                                  "then a kinds line, a goals line, and an untagged line when each has something to count, "
                                  "then reports to, mode, and owner when present")
    p = sub.add_parser("close", help="write the report of what changed since the last one")
    output = p.add_mutually_exclusive_group()
    output.add_argument("--dry-run", action="store_true")
    output.add_argument("--to-file", action="store_true",
                        help="print only the path of the written report, not its text")
    p = sub.add_parser("walk", help="every restaurant's counts and open decisions, grouped by repository")
    p.add_argument("--stale-hours", type=float, default=24)
    p.add_argument("--repo", help="print only this repository")

    p = sub.add_parser("next", help="print the steps this wake owes and whether the wake is a reply. "
                                    "Leave the wake off when this wake has no known cause")
    p.add_argument("--failure", choices=("fixable", "stuck"), default=None,
                   help="fixable or stuck, for a failure this wake already knows")
    p.add_argument("--unsent", choices=UNSENT, default=None,
                   help="a line to the executive admin that did not send")
    wakes = p.add_subparsers(dest="wake")

    def wake_parser(name, help_text):
        parser = wakes.add_parser(name, help=help_text)
        parser.add_argument("--failure", choices=("fixable", "stuck"), default=argparse.SUPPRESS,
                            help="fixable or stuck, for a failure this wake already knows")
        parser.add_argument("--unsent", choices=UNSENT, default=argparse.SUPPRESS,
                            help="a line to the executive admin that did not send")
        return parser

    wake_parser("report-back", "a worker report arrived for this dish").add_argument("dish")
    review = wake_parser("review", "a review verdict for this dish")
    review.add_argument("dish")
    review.add_argument("--verdict", choices=VERDICTS, required=True)
    checks = wake_parser("checks", "a pull request check for this dish")
    checks.add_argument("dish")
    checks.add_argument("--result", choices=("pass", "fail"), required=True)
    wake_parser("merge", "the queue may have landed this dish").add_argument("dish")
    wake_parser("liveness", "the liveness schedule woke")
    wake_parser("intake", "the intake schedule woke")
    wake_parser("morning", "the morning service woke")
    wake_parser("evening", "the evening report is due")
    user = wake_parser("user", "a message from the user")
    user.add_argument("--question", action="store_true", help="the message asks a question")
    wake_parser("admin", "a request from the executive admin").add_argument("request")
    return top


def brief_contract(value):
    """The contract as given. Refuses None and text that is only whitespace."""
    if not (value or "").strip():
        raise BrigadeError(NEEDS_CONTRACT)
    return value


def _brief_fields(args):
    flagged = any(getattr(args, name) is not None for name in ("goal", "contract", "verify", "base", "paths", "lease")) or args.acceptance or args.context
    if args.fields is not None:
        if flagged:
            raise BrigadeError("use either --fields or the field flags, not both")
        if args.fields == "-":
            if sys.stdin.isatty():
                raise BrigadeError("brief fields: --fields - reads JSON from stdin")
            raw = sys.stdin.read()
        else:
            try:
                raw = Path(args.fields).read_text(encoding="utf-8")
            except OSError as error:
                raise BrigadeError(f"brief fields: {error}") from error
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise BrigadeError(f"brief fields: {error}") from error
        if not isinstance(payload, dict):
            raise BrigadeError("brief fields: JSON must be an object")
        allowed = {"goal", "acceptance", "contract", "verify", "base", "context", "paths", "lease"}
        lists = {"acceptance", "context"}
        unknown = [key for key in payload if key not in allowed]
        if unknown:
            raise BrigadeError(f"brief fields: unknown key {unknown[0]}")
        for key in ("goal", "acceptance", "verify", "base"):
            if key not in payload:
                raise BrigadeError(f"brief fields: missing {key}")
        for key, value in payload.items():
            if value is None:
                raise BrigadeError(f"brief fields: {key} is null")
            if key in lists:
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    raise BrigadeError(f"brief fields: {key} must be a list of strings")
            elif not isinstance(value, str):
                raise BrigadeError(f"brief fields: {key} must be a string")
        return {
            "goal": payload["goal"],
            "acceptance": payload["acceptance"],
            "contract": brief_contract(payload.get("contract")),
            "verify": payload["verify"],
            "paths": payload["paths"] if "paths" in payload else "",
            "lease": payload["lease"] if "lease" in payload else "",
            "base": payload["base"],
            "context": payload["context"] if "context" in payload else [],
        }
    if args.goal is None or args.verify is None or args.base is None:
        raise BrigadeError("brief needs --fields, or --goal, --verify, and --base")
    return {
        "goal": args.goal,
        "acceptance": args.acceptance,
        "contract": brief_contract(args.contract),
        "verify": args.verify,
        "paths": args.paths or "",
        "lease": args.lease or "",
        "base": args.base,
        "context": args.context,
    }


def run(argv):
    args = parser().parse_args(argv)
    root = store_root(args.store)
    if args.command == "open":
        if args.admin and args.name:
            raise BrigadeError("open --admin takes no --name")
        if not args.admin and not args.name:
            raise BrigadeError("open needs --name, or --admin")
        require_workers(args.workers)
        restaurant, created = open_restaurant(root, args.project_root, args.name, args.reporting,
                                              intake_list(args.intake), args.workers, args.admin, args.mode)
        lines = [f"{'opened' if created else 'exists'} {restaurant.dir}"]
        meta = restaurant.meta
        thread = (meta.get("thread") or "").strip()
        if not created and thread:
            lines.append(f"thread {thread} already recorded")
        intake = meta.get("intake") or []
        if not created and args.intake and intake_list(args.intake) != intake:
            lines.append(f"intake stays {', '.join(intake) or 'empty'}; change it with set --intake")
        if not created and args.mode is not None and args.mode != meta.get("mode"):
            lines.append(f"mode stays {meta.get('mode') or 'unset'}; change it with set --mode")
        lines.extend(sibling_lines(restaurant))
        if siblings(restaurant.dir, restaurant.meta.get("projectRoot")) and not is_admin(meta):
            cap = repository_cap(restaurant.meta["projectRoot"])
            workers = worker_cap(restaurant.meta)
            if cap is not None and workers >= cap:
                lines.append(f"warning: workers {workers} is at or above the repository cap of {cap} while a sibling exists")
        return "\n".join(lines)
    if args.command == "walk":
        text, failures = walk(root, args.stale_hours, args.repo)
        if failures:
            print(text)
            raise BrigadeError("\nbrigade: ".join(failures))
        return text
    if not args.at:
        raise BrigadeError("pass --at <restaurant dir> or set BRIGADE_DIR")
    restaurant = Restaurant(args.at, args.owner)
    admin = is_admin(restaurant.meta)
    if admin and args.command in WORK_COMMANDS:
        raise BrigadeError("the executive admin routes work and never runs it")
    if not admin and args.command in ADMIN_COMMANDS:
        raise BrigadeError(f"{args.command} works only in the executive admin's store")
    if args.command == "next":
        return next_text(restaurant, args)
    if args.command == "sync":
        # The admin's lock is taken only after each coordinator's read lock is released, so this runs outside checked.
        text, failures = sync(restaurant)
        if failures:
            if text:
                print(text)
            raise BrigadeError("\nbrigade: ".join(failures))
        return text or "nothing new"
    if args.command == "request" and args.republish:
        if args.to or args.line:
            raise BrigadeError("request --republish takes no --to or line")
        finished = finished_by_coordinator(restaurant)
        with restaurant.checked():
            return republish(restaurant, finished)
    if args.command == "status":
        # land.py can wait on the landing database, so the contract is read before the store lock.
        contract = contract_mode(restaurant.meta["projectRoot"])
        with restaurant.checked():
            return command(restaurant, args, contract)
    if args.command == "set" and (args.thread or args.expect is not None):
        if args.thread is None:
            raise BrigadeError("--expect needs --thread")
        return set_thread(restaurant, args)
    if args.command in ("fire", "dish"):
        args.reason = item_escalation(args.mode, args.reason)
        if args.reason and args.command == "dish" and args.state in ("queued", "merged", "dropped"):
            raise BrigadeError(f"--mode full needs open work; run --state {args.state} without it")
    if args.command == "dish" and args.state == "dropped":
        return drop(restaurant, args.id, args.stopped)
    if args.command == "brief":
        # A stalled stdin or file must not hold the store lock, so the fields are read and checked first.
        fields = _brief_fields(args)
        return brief(restaurant, args.id, **fields)
    if args.command == "edits":
        return verify_edits(restaurant, args.dish, args.from_sha, args.to)
    if args.command == "fire":
        ids = [ident.strip() for ident in args.tickets.split(",") if ident.strip()]
        ride_along = [ident.strip() for ident in args.ride_along.split(",") if ident.strip()]
        return fire(restaurant, ids, args.station, args.task, args.thread, args.branch, args.summary, args.timebox,
                    args.paths, args.reason, args.kind or "", ride_along)
    if args.command == "watch":
        return watch(restaurant)
    if args.command == "startable":
        return startable(restaurant)
    if args.command == "ticket" and args.action == "list":
        held = holding_blocks(restaurant)
        with restaurant.checked():
            return ticket_lines(restaurant, args.state, held)
    if args.command == "dish" and args.branch and args.lease is None:
        with restaurant.checked():
            result = command(restaurant, args)
        lines = fragment_lines(restaurant, args.id, args.branch)
        return f"{result}\n{lines}" if lines else result
    if args.command == "ticket" and args.action == "add" and args.from_report is not None:
        if args.source != "user" or args.ref or args.request or args.again:
            raise BrigadeError("--from-report takes no --source, --ref, --request, or --again")
        if args.priority or args.paths or args.decision:
            raise BrigadeError("--from-report takes no --priority, nonempty --paths, or --decision")
        if args.kind or args.goal is not None:
            raise BrigadeError("--from-report takes no --kind or --goal; each ticket's kind and goal follow from the report's item")
        name = item_report(restaurant, args.from_report)
        try:
            found = follow_ups((restaurant.dir / "reports" / name).read_text(encoding="utf-8"))
        except UnicodeDecodeError as error:
            raise BrigadeError(f"reports/{name} is not UTF-8 text; nothing added") from error
        tracked = tracked_paths(restaurant.meta["projectRoot"])
        with restaurant.checked():
            return file_follow_ups(restaurant, name, found, not args.dry_run, tracked)
    if args.command == "ticket" and args.action == "paths":
        project = restaurant.meta["projectRoot"]
        tracked = tracked_paths(project)
        if tracked is None:
            raise BrigadeError(f"cannot list tracked files in {project}")
        with restaurant.checked():
            return record_paths(restaurant, tracked, not args.dry_run)
    if args.command == "ticket" and args.action == "add" and args.dry_run:
        raise BrigadeError("--dry-run needs --from-report")
    if args.command == "ticket" and args.action in ("add", "set") and args.paths is not None:
        args.paths = ticket_paths(args.paths)
    rails = None
    if args.command == "ticket" and (args.action == "move" or args.action == "add" and args.ref):
        rails = sibling_rails(restaurant)
    with restaurant.checked():
        return command(restaurant, args, rails=rails)


def command(restaurant, args, contract=None, rails=None):
    if args.command == "set":
        meta = restaurant.meta
        changes, drop = {}, []
        if args.intake is not None:
            changes["intake"] = set_intake(restaurant, intake_list(args.intake))["intake"]
        if args.thread is not None and (args.thread or args.expect is not None):
            current = (meta.get("thread") or "").strip()
            generation, changing = next_owner(meta, args.thread, args.replace, args.expect, args.stopped)
            changes.update(generation=generation, thread=args.thread)
            if changing and current and not current.startswith("recovering:"):
                changes["previousThread"] = current
            if changing and is_admin(meta):
                # The row is the transition record. A crash before the metadata write keeps the stop evidence, and a retry finds the row.
                note = f"replaced {current}" if current else ("restarted" if meta.get("generation") else "first thread")
                note += f"; stopped {clean(args.stopped)}" if args.stopped else ""
                ident = f"{args.thread}@{generation}"
                if not any(row["kind"] == "thread" and row["id"] == ident for row in restaurant.rows("log.tsv")):
                    restaurant.log("thread", ident, "recorded", note)
        if args.reporting:
            changes["reporting"] = args.reporting
        if args.workers is not None:
            require_workers(args.workers)
            changes["workers"] = args.workers
        if args.mode is not None:
            if args.mode in MODES:
                changes["mode"] = args.mode
            elif args.mode == "":
                drop.append("mode")
            else:
                raise BrigadeError('--mode takes full, light, or ""')
        for key, value in (("buildCommand", args.build_command), ("checkCommand", args.check_command)):
            if value is None:
                continue
            if value.strip():
                command_words(value.strip())
                changes[key] = value.strip()
            else:
                drop.append(key)
        if args.autofire:
            changes["autofire"] = args.autofire
        if args.reports_to is not None:
            if clean(args.reports_to):
                changes["reportsTo"] = clean(args.reports_to)
            else:
                drop.append("reportsTo")
        if args.schedule:
            schedules = dict(meta.get("schedules") or {})
            for pair in args.schedule:
                if "=" not in pair:
                    raise BrigadeError(f"--schedule takes NAME=ID, got {pair!r}")
                name, ident = pair.split("=", 1)
                if ident:
                    schedules[name] = ident
                else:
                    schedules.pop(name, None)
            changes["schedules"] = schedules
        meta = restaurant.change_meta(drop, **changes)
        return json.dumps(meta, indent=2)

    if args.command == "contract":
        return stored_contract(restaurant, args.id)

    if args.command == "inbox":
        if args.action == "take":
            return take_inbox(restaurant)
        return finish_request(restaurant, args.id)

    if args.command == "request":
        if not args.to or args.line is None:
            raise BrigadeError('request needs --to <coordinator> "<line>", or --republish')
        return send_request(restaurant, args.to, args.line)

    if args.command == "rule":
        if args.action != "list":
            # A ruling command killed before its log row left an event to write first.
            log_rulings(restaurant)
        if args.action == "add":
            return add_ruling(restaurant, args.kind, args.parties, clean(args.question), args.rule, clean(args.decision),
                              clean(args.supersedes))
        if args.action == "overrule":
            return overrule(restaurant, args.id, clean(args.decision))
        if args.action == "set":
            return end_ruling(restaurant, args.id, args.state)
        rows = [row for row in restaurant.rows("rulings.tsv") if not args.state or row["state"] == args.state]
        return "\n".join(ruling_line(row) for row in rows) or "no rulings"

    if args.command == "goal":
        if args.action == "add":
            return add_goal(restaurant, args.name, args.purpose, args.done, args.share)
        if args.action == "set":
            return set_goal(restaurant, args.id, args.name, args.purpose, args.done, args.share, args.state, args.evidence)
        labels = read_labels(restaurant, EVERY_ROW)
        return goal_lines(labels, count_labels(restaurant, labels), args.all)

    if args.command == "ticket":
        if args.action == "add":
            return add_ticket(restaurant, args.summary, args.source, args.ref, args.request, rails or {}, again=args.again,
                              priority=args.priority or "", paths=args.paths, decision=args.decision,
                              kind=args.kind or "", goal=args.goal)
        if args.action == "move":
            return move_ticket(restaurant, args.id, args.to, rails or {})
        if args.action == "take":
            return "\n".join(take_tickets(restaurant)) or NOTHING_HANDED
        _, ticket = restaurant.find("rail.tsv", args.id)
        if ticket["state"] == "moved":
            raise BrigadeError(f"{args.id} moved to {ticket['dish'].removeprefix('to:')}; it is that coordinator's ticket now")
        held = in_flight_tickets(restaurant)
        frozen = frozen_label(ticket, held)
        if (args.kind or args.goal is not None or args.no_goal) and frozen:
            if ticket["state"] != "waiting":
                kept = (f"; {ticket['dish']} keeps the kind and goal it took when it started"
                        if ticket["state"] == "assigned" and ticket["dish"] else "")
                raise BrigadeError(f"{frozen}; --kind, --goal, and --no-goal change a waiting ticket only{kept}")
            item = held[args.id]["id"]
            raise BrigadeError(f"{frozen}; --kind, --goal, and --no-goal change a waiting ticket that no item in flight names; "
                               f"{item} keeps the kind and goal it took when it started; drop {item}, and every other item in flight that names {args.id}, first, "
                               f"or leave {args.id} as it is")
        changes, said = {}, []
        if args.state:
            changes["state"] = args.state
            said.append(args.state)
        if args.priority:
            changes["priority"] = args.priority
            said.append(f"priority {args.priority}")
        if args.paths is not None:
            changes["paths"] = ",".join(args.paths)
            said.append(f"paths {changes['paths']}" if args.paths else "no paths")
        if args.decision is not None:
            changes["decision"] = "yes" if args.decision else ""
            said.append("decision" if args.decision else "no decision")
        if args.kind:
            changes["kind"] = args.kind
            said.append(f"kind {args.kind}")
        if args.goal is not None:
            changes["goal"] = open_goal(read_labels(restaurant, GOALS_ONLY), args.goal).id
            said.append(f"goal {args.goal}")
        if args.no_goal:
            changes["goal"] = ""
            said.append("unassigned")
        if not said:
            raise BrigadeError("ticket set needs --state, --priority, --paths, --decision, --no-decision, --kind, --goal, or --no-goal")
        restaurant.update("rail.tsv", args.id, "ticket", **changes)
        return f"{args.id} {'; '.join(said)}"

    if args.command == "dish":
        _, current = restaurant.find("dishes.tsv", args.id)
        worker = any(value and value != current.get(field, "") for field, value in (("thread", args.thread), ("task", args.task)))
        if args.state == "in-progress" or worker:
            refusal = fix_round_refusal(restaurant, current)
            if refusal:
                raise BrigadeError(refusal)
        if args.state in COUNTED_STATES and current["state"] not in COUNTED_STATES:
            full = workers_full(restaurant)
            if full:
                raise BrigadeError(full)
        if (args.lease or args.paths) and current["state"] in ("merged", "dropped"):
            raise BrigadeError(f"{args.id} is {current['state']}; it holds no lease")
        if args.state in ("queued", "merged"):
            ok, why = pass_check(restaurant, args.id, args.sha or current["sha"])
            if not ok:
                raise BrigadeError(f"only reviewed work lands: {why}")
        entering = args.state == "in-progress" and current["state"] != "in-progress"
        restart = entering and current["state"] in ("sent-back", "queued")
        replacing = (bool(args.thread) and args.thread != current.get("thread", "")
                     and current["state"] == "in-progress" and args.state in (None, "in-progress"))
        thread = "" if restart and args.thread is None else args.thread
        recorded = set(retired_worker_threads(restaurant, args.id))
        earlier = set(recorded)
        if restart and current.get("thread"):
            earlier.add(current["thread"])
        if thread and thread in earlier:
            raise BrigadeError(f"thread {thread} is an earlier attempt of {args.id}; a send-back launches a fresh worker")
        escalated = None
        if args.reason:
            escalated = record_escalation(restaurant, args.id, args.reason)
        next_thread = current.get("thread", "") if thread is None else thread
        if current.get("thread") and next_thread != current["thread"] and (restart or next_thread):
            if current["thread"] not in recorded:
                restaurant.log("worker", args.id, "retired", current["thread"])
        reported = "" if entering or replacing else ("yes" if args.reported else None)
        row = restaurant.update("dishes.tsv", args.id, "dish", state=args.state, task=args.task, thread=thread,
                                branch=args.branch, pr=args.pr, sha=args.sha, timebox=args.timebox, reported=reported,
                                lease=args.lease, paths=args.paths)
        if replacing:
            restaurant.log("dish", args.id, "in-progress", row.get("summary", ""))
        if args.state == "merged":
            for ticket in filter(None, row["tickets"].split(",")):
                restaurant.update("rail.tsv", ticket, "ticket", state="done")
        if escalated is not None:
            return f"{args.id} {row['state']}, mode full: {escalated}"
        return f"{args.id} {row['state']}"

    if args.command == "pass":
        if args.action == "record":
            return record_pass(restaurant, args.dish, args.pr, args.sha, args.verdict, args.author, args.verifier,
                               args.note, args.same_family, args.report, args.member, args.late, args.edits)
        ok, why = pass_check(restaurant, args.dish, args.sha)
        latest = latest_verdict(restaurant, args.dish, args.sha) if args.json else None
        if latest is not None:
            if ok:
                return pass_json(latest)
            print(pass_json(latest))
        if not ok:
            raise BrigadeError(why)
        return why

    if args.command == "86":
        if args.action == "add":
            given = [value for value in (args.question, args.options, args.default) if value is not None]
            if args.round_budget:
                if given or not args.dish:
                    raise BrigadeError("86 add --round-budget takes --dish and no --question, --options, or --default")
                restaurant.find("dishes.tsv", args.dish)
            elif len(given) < 3:
                raise BrigadeError("86 add needs --question, --options, and --default")
            open_row = open_item_decisions(restaurant.rows("86.tsv")).get(args.dish)
            if open_row:
                if args.round_budget and open_row["kind"] != ROUND_KIND:
                    raise BrigadeError(f"{args.dish} is held by {open_row['id']}; answer it, "
                                       f"then run 86 add --dish {args.dish} --round-budget")
                return open_row["id"]
            if args.round_budget:
                args.question, args.options, args.default = round_decision(restaurant, args.dish)
            # Validate the text the row will store, since the append turns tabs and newlines into spaces.
            question, options, default = clean(args.question), clean(args.options), clean(args.default)
            if args.dish:
                keys = {option_key(option) for option in options.split(",") if option.strip()}
                if option_key(default) not in keys or len(keys) < 2:
                    raise BrigadeError("an item decision's default must be one of its options, "
                                       "with at least one other option that closes it")
            ident = restaurant.next_id("86.tsv")
            restaurant.append("86.tsv", {"id": ident, "at": now(), "state": "open", "dish": args.dish,
                                         "question": question, "options": options, "default": default,
                                         "kind": ROUND_KIND if args.round_budget else ""})
            restaurant.log("decision", ident, "open", question)
            return ident
        if args.action == "answer":
            _, row = restaurant.find("86.tsv", args.id)
            if not row["dish"].strip():
                restaurant.update("86.tsv", args.id, "decision", state="answered", answer=args.answer, answered=now())
                return f"{args.id} answered"
            if row["state"] == "answered":
                return f"{args.id} answered"
            closes = closing_options(row)
            if not closes:
                restaurant.update("86.tsv", args.id, "decision", state="answered", answer=args.answer, answered=now())
                return f"{args.id} answered"
            match = next((option for option in closes if option_key(option) == option_key(args.answer)), None)
            if match is None:
                quoted = " or ".join(shlex.quote(option) for option in closes)
                return f"{args.id} still open; {row['dish']} stays held until 86 answer {args.id} --answer {quoted}"
            restaurant.update("86.tsv", args.id, "decision", state="answered", answer=match, answered=now())
            return f"{args.id} answered"
        lines = []
        for row in restaurant.rows("86.tsv"):
            if row["state"] != "open":
                continue
            label = f"{row['id']} for {row['dish']}" if row["dish"] else row["id"]
            lines.append(f"{label}: {row['question']} Options: {row['options']}. Default: {row['default']}.")
        return "\n".join(lines) or "no open decisions"

    if args.command == "hang":
        return record_hang(restaurant, args.id, args.provider, args.minutes, args.attempt)
    if args.command == "status":
        meta = restaurant.meta
        thread = (meta.get("thread") or "").strip()
        level = f"reporting: {reporting_of(meta)}, {contract}"
        counts_text = status_line(restaurant)
        # The thread comes first, so a service's fence step compares it before anything else.
        lines = [f"thread {thread}" if thread else "thread not recorded",
                 level if counts_text == "nothing on record" else f"{level}, {counts_text}"]
        if not is_admin(meta):
            lines.append(workers_line(meta, running_workers(restaurant)))
        labels = read_labels(restaurant, EVERY_ROW)
        lines += status_label_lines(labels, count_labels(restaurant, labels))
        if meta.get("reportsTo"):
            lines.append(f"reports to {meta['reportsTo']}")
        if meta.get("mode"):
            lines.append(f"mode {meta['mode']}")
        if meta.get("generation") is not None:
            lines.append(f"owner {thread}@{meta['generation']}")
        return "\n".join(lines)
    if args.command == "close":
        if not args.dry_run and is_admin(restaurant.meta):
            log_rulings(restaurant)
        text, path = report(restaurant, write=not args.dry_run)
        linked = (restaurant.dir / "reports").is_symlink()
        for name, kind in unrecorded_reviews(restaurant):
            dish = name.split("-")[0]
            if kind == "file":
                print(f"brigade: warning: reports/{name} has no review row; record it with pass record {dish} --report {name}, "
                      f"and --late when {dish} has moved past that round", file=sys.stderr)
            else:
                # Under a reports/ that is a symbolic link, an entry of every kind resolves outside this store's reports/.
                kind = "elsewhere" if linked else kind
                print(f"brigade: warning: reports/{name} {NOT_A_REPORT[kind]} and no review row of {dish} names it; {REPAIR[kind]}",
                      file=sys.stderr)
        for ident, state in unclaimed_reports(restaurant):
            print(f"brigade: warning: reports/{ident}.md has no Claims check line; {ident} is {state}", file=sys.stderr)
        if args.to_file:
            return str(path.resolve())
        return text
    raise BrigadeError(f"unknown command {args.command}")


def main(argv=None):
    try:
        print(run(sys.argv[1:] if argv is None else argv))
    except BrigadeError as error:
        print(f"brigade: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
