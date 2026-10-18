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
from brigade_lib.compact import effective_provider, existing_cap, refuse_setting, sync_meta
from brigade_lib.shared import BrigadeError, MalformedTable, slug, stored


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
# Why a review sent the work back. pass record takes one of these only with --verdict send-back.
REASONS = ("false-wording", "missing-test", "wrong-behavior", "out-of-lease", "scope", "other")
REASON_VERDICTS = ("send-back",)
# A send-back row whose reason is empty or outside REASONS counts as this in `pass stats`.
UNRECORDED_REASON = "unrecorded"
# The roles a row of threads.tsv may hold. A report item or --role naming anything else is refused.
THREAD_ROLES = ("worker", "fix worker", "reviewer", "panel member", "code delegate", "claims check", "architect runner")
# The worker roles. dish --thread writes these, and a report may not name them.
WORKER_ROLES = ("worker", "fix worker")
# The roles a worker report lists, which its worker delegated. The report writes these and nothing else.
CHILD_ROLES = ("code delegate", "claims check", "architect runner")
# The roles the thread command writes. No other command takes a role as a flag.
THREAD_COMMAND_ROLES = ("panel member",)
# The command that writes each role the report may not name, by role.
THREADS_WRITERS = {"worker": "dish <item> --thread <id>", "fix worker": "dish <item> --thread <id>",
                   "reviewer": "pass record --reviewer-thread <id>", "panel member": "thread <dish> --role \"panel member\""}
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

TABLES = {
    "rail.tsv": ("id", "at", "state", "source", "ref", "dish", "summary", "priority", "paths", "decision", "kind", "goal"),
    "dishes.tsv": ("id", "at", "state", "station", "tickets", "task", "thread", "branch", "pr", "sha", "summary", "timebox", "lease", "paths", "reported",
                   "kind", "goal"),
    "pass.tsv": ("at", "dish", "pr", "sha", "verdict", "author", "verifier", "note", "report", "member", "reason"),
    "threads.tsv": ("at", "dish", "attempt", "role", "thread", "seat", "start", "end"),
    "86.tsv": ("id", "at", "state", "dish", "question", "options", "default", "answer", "kind", "answered"),
    "log.tsv": ("at", "kind", "id", "state", "note"),
    "edits.tsv": ("at", "dish", "from", "to", "report"),
    "goals.tsv": ("id", "at", "state", "name", "purpose", "done", "share", "closed"),
    # Only the executive admin's store has this table.
    "rulings.tsv": ("id", "at", "kind", "parties", "question", "rule", "decision", "supersedes", "state"),
}
# How many trailing cells a row of each table may lack. A missing cell reads as empty.
ADDED_COLUMNS = {"pass.tsv": 3, "86.tsv": 2, "rail.tsv": 5, "dishes.tsv": 2}
PREFIX = {"rail.tsv": "T", "dishes.tsv": "D", "86.tsv": "Q", "rulings.tsv": "R", "goals.tsv": "G"}
ADMIN_DIR = ".admin"
ADMIN_NAME = "executive admin"
WORK_COMMANDS = ("fire", "brief", "contract", "dish", "pass", "edits", "watch", "startable", "next", "thread", "hang")
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
ADMIN_COMMANDS = ("request", "rule", "sync")
RULING_KINDS = ("contested-paths", "ownership", "shares", "queue-order")
RULING_RULES = ("purpose", "priority", "age", "related-work", "dependency", "floor", "user")
RULING_STATES = ("in-force", "done", "expired", "superseded", "overruled")
NOTHING_HANDED = "nothing handed to you"
NEEDS_CONTRACT = ("brief needs a contract; pass --contract <text> or the contract key in --fields, and write none when the item has no safety, privacy, or integrity property")
# The restaurant.json key that holds the parent directory worker briefs pass to mktemp -d -p.
SCRATCH_KEY = "scratchDir"
SCRATCH_REFUSAL = "--scratch-dir takes an absolute directory path outside /tmp, or empty string to clear it"
# The restaurant.json key that overrides LOW_RESERVE.
LOW_RESERVE_KEY = "lowReserve"
# The restaurant.json key that overrides ROUND_BUDGET.
ROUND_BUDGET_KEY = "roundBudget"
# The restaurant.json key that overrides the liveness interval.
LIVENESS_KEY = "livenessMs"
LIVENESS_FLOOR = 60000
# The restaurant.json key that names the directory holding one change-note fragment per branch.
CHANGE_NOTE_KEY = "changeNoteDir"
CHANGE_NOTE_REFUSAL = ("it is not a repository-relative directory path with no leading slash, "
                       "no .., no comma, and no whitespace")
# The interval each reporting level uses when the store holds no livenessMs key.
LIVENESS_DEFAULT_MS = {"every-turn": 600000, "milestones": 600000, "digest": 1800000}
SNAPSHOT_CHUNK = 1 << 16

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

- You may write the change yourself when it changes one file outside tests, the added and removed lines outside tests are about 40 or fewer across the whole item, and the contract does not call its guarantee safety, privacy, or integrity. Count those lines from `git diff --numstat` against the item's base commit, quote that output in the report, and treat every round and commit as one diff. Past that size, or more than one file outside tests, the code delegate writes the change. The claims check and the gate review are never skipped.

1. A test earns its place when it fails for a defect that no other check reports; name that defect in the test name.
2. A test that pins a reviewed defect stays; remove it only with a mutation that shows another check fails for the same change.
3. Use a rule in the project's static check, when it has one, when the property is visible in the text or the tree without running the code, with one passing and one failing fixture test.
4. Use a test when the property is behavior: exit codes, written files, ordering, refusals.
5. Call the command in the test process through the project's in-process test helper, when it has one; start a process only for a race, a signal, exit status, the environment, or help output, and say which.
6. Never assert a library's own wording.
7. Add a case to an existing table when the setup is the same.
8. A test that sleeps to order two processes names the review finding it pins.
9. Do not add a test for what the project's build already refuses.
10. The reviewer asks of each new test: which change to the code makes this fail and nothing else fail?
"""


def store_root(flag=None):
    if flag:
        return Path(flag)
    if os.environ.get("BRIGADE_STORE"):
        return Path(os.environ["BRIGADE_STORE"])
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "pstack-t3" / "brigade"


def scratch_parent_text(value):
    """The parent directory briefs pass to mktemp -d -p, or empty string when the setting is unset.

    None, empty string, and whitespace-only mean unset. Any other value must be an absolute path outside /tmp, and an existing path must be a directory.
    """
    if value is None:
        return ""
    text = str(value)
    if not text.strip():
        return ""
    resolved = Path(text).resolve() if Path(text).is_absolute() else None
    tmp = Path("/tmp").resolve()
    refused = (any(bad in text for bad in ("\n", "\r", "~")) or resolved is None
               or resolved == Path("/tmp") or Path("/tmp") in resolved.parents
               or resolved == tmp or tmp in resolved.parents
               or (resolved.exists() and not resolved.is_dir()))
    if refused:
        raise BrigadeError(SCRATCH_REFUSAL)
    return str(resolved)


def low_reserve_of(meta):
    """The low-priority worker reserve this store enforces.

    A missing key takes LOW_RESERVE. A present value that is a boolean, is not an integer, or is negative is refused.
    """
    if LOW_RESERVE_KEY not in meta:
        return LOW_RESERVE
    value = meta[LOW_RESERVE_KEY]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        refuse_setting(LOW_RESERVE_KEY, value, "it is not a whole number of 0 or more")
    return value


def parse_low_reserve(text):
    """The integer a --low-reserve flag stores. Empty string is the caller's clear signal, not this function's."""
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise BrigadeError(f"--low-reserve takes a whole number of 0 or more, got '{text}'") from None
    if value < 0:
        raise BrigadeError(f"--low-reserve takes a whole number of 0 or more, got '{text}'")
    return value


def round_budget_of(meta):
    """The round budget this store enforces.

    A missing key takes ROUND_BUDGET. A present value that is a boolean, is not an integer, or is under 1 is refused.
    """
    if ROUND_BUDGET_KEY not in meta:
        return ROUND_BUDGET
    value = meta[ROUND_BUDGET_KEY]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        refuse_setting(ROUND_BUDGET_KEY, value, "it is not a whole number of 1 or more")
    return value


def parse_round_budget(text):
    """The integer a --round-budget flag stores. Empty string is the caller's clear signal, not this function's."""
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise BrigadeError(f"--round-budget takes a whole number of 1 or more, got '{text}'") from None
    if value < 1:
        raise BrigadeError(f"--round-budget takes a whole number of 1 or more, got '{text}'")
    return value


def liveness_of(meta):
    """The effective liveness interval in milliseconds and the reporting level it is the default for.

    A missing livenessMs key returns the store's reporting level's default and that level, and a reporting level
    the table does not hold reads as milestones. A present value returns it and "". A present value that is a
    boolean, is not an integer, or is under LIVENESS_FLOOR is refused.
    """
    if LIVENESS_KEY not in meta:
        level = reporting_of(meta)
        if level not in LIVENESS_DEFAULT_MS:
            level = "milestones"
        return LIVENESS_DEFAULT_MS[level], level
    value = meta[LIVENESS_KEY]
    if isinstance(value, bool) or not isinstance(value, int):
        refuse_setting(LIVENESS_KEY, value, "it is not an integer")
    if value < LIVENESS_FLOOR:
        refuse_setting(LIVENESS_KEY, value, f"it is under {LIVENESS_FLOOR}")
    return value, ""


def parse_liveness_ms(text):
    """The integer a --liveness-ms flag stores. Empty string is the caller's clear signal, not this function's."""
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise BrigadeError(f"--liveness-ms takes a whole number of at least {LIVENESS_FLOOR}, got '{text}'") from None
    if value < LIVENESS_FLOOR:
        raise BrigadeError(f"--liveness-ms takes a whole number of at least {LIVENESS_FLOOR}, got '{text}'")
    return value


def _change_note_norm(value):
    text = value.replace("\\", "/")
    if text.startswith("/") or ".." in text.split("/") or "," in text or re.search(r"\s", text):
        return None
    made = posixpath.normpath(text).lstrip("/")
    if made in ("", ".", "/") or made == ".." or made.startswith("../"):
        return None
    return made


def change_note_dir(meta):
    """The change-note directory this store records, or empty string when it records none.

    A missing key and an empty string both read as unset. A present value that is not a
    repository-relative directory path is refused through refuse_setting.
    """
    if CHANGE_NOTE_KEY not in meta:
        return ""
    value = meta[CHANGE_NOTE_KEY]
    if value == "":
        return ""
    if not isinstance(value, str):
        refuse_setting(CHANGE_NOTE_KEY, value, CHANGE_NOTE_REFUSAL)
    made = _change_note_norm(value)
    if made is None:
        refuse_setting(CHANGE_NOTE_KEY, value, CHANGE_NOTE_REFUSAL)
    return made


def parse_change_note_dir(text):
    """The directory a --change-note-dir flag stores, normalized. Empty string is the caller's clear signal, not this function's."""
    if not isinstance(text, str):
        raise BrigadeError("--change-note-dir takes a repository-relative directory path with no leading slash, "
                           f"no .., no comma, and no whitespace, got '{text}'")
    made = _change_note_norm(text)
    if made is None:
        raise BrigadeError("--change-note-dir takes a repository-relative directory path with no leading slash, "
                           f"no .., no comma, and no whitespace, got '{text}'")
    return made


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with seam.tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, suffix=".tmp") as handle:
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
                while chunk := seam.read_chunk(fd):
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
        if seam.READ_ONLY:
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
            seam.write_atomic(path, text)

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
            self.append("log.tsv", {"at": seam.now(), "kind": kind, "id": ident, "state": state, "note": note})
            self.change_meta(lastActivityAt=seam.now())

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


def open_restaurant(root, project_root, name, reporting="milestones", intake=(), workers=None, admin=False, mode=None,
                  context_cap=None, coordinator_provider=None):
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
        meta = seam.wait_for_meta(directory)
        if meta.get("projectRoot") != str(project_root):
            other = meta.get("projectRoot")
            raise BrigadeError(f"{directory} already holds a coordinator for {other}; pick another --name")
        return Restaurant(directory), False
    try:
        refuse_owned_intake(directory, str(project_root), intake)
    except BrigadeError:
        directory.rmdir()
        raise
    meta = {"restaurant": name, "projectRoot": str(project_root),
            "reporting": reporting, "openedAt": seam.now(), "lastActivityAt": seam.now(),
            "lastReportAt": None, "thread": None, "schedules": {}, "intake": list(intake)}
    if workers is not None:
        meta["workers"] = workers
    if mode is not None:
        meta["mode"] = mode
    if context_cap is not None:
        meta["contextCap"] = context_cap
    if coordinator_provider is not None:
        meta["coordinatorProvider"] = coordinator_provider
    if admin:
        meta["role"] = "admin"
    # A refused settings write happens before the store is published, so it leaves no store behind and claims no intake.
    if effective_provider(meta) == "claude":
        # Creation without --context-cap adopts an integer cap the user already set and writes nothing.
        # Creation with --context-cap, and creation with no integer cap in the file, writes it.
        held = existing_cap(meta["projectRoot"]) if context_cap is None else None
        if held is not None:
            meta["contextCap"] = held
        else:
            try:
                sync_meta(meta)
            except BrigadeError:
                directory.rmdir()
                raise
    for filename, template in (("menu.md", ADMIN_MENU if admin else MENU), ("house-rules.md", HOUSE_RULES)):
        seam.write_atomic(directory / filename, template.format(restaurant=name))
    for table in TABLES:
        if table == "rulings.tsv" and not admin:
            continue
        header = TABLES[table]
        seam.write_atomic(directory / table, "\t".join(header) + "\n")
    seam.write_atomic(directory / "restaurant.json", json.dumps(meta, indent=2) + "\n")
    return Restaurant(directory), True


def roles_script():
    for path in (seam.BUILT_ROLES, seam.SOURCE_ROLES):
        if path.is_file():
            return path
    raise BrigadeError(f"cannot find roles.py at {seam.BUILT_ROLES} or {seam.SOURCE_ROLES}; build or reinstall pstack-t3")
