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

class Store:
    def __init__(self, directory):
        self.db = None
        self.dir = Path(directory)
        db = sqlite3.connect(self.dir / "land.db", timeout=60, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(seam.SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(entry)")}
            for column in ("title", "body", "onto"):
                if column not in columns:
                    db.execute(f"ALTER TABLE entry ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            seam.migrate_entry_ids(db)
        except BaseException:
            try:
                db.close()
            except sqlite3.Error:
                pass
            raise
        self.db = db

    def close(self):
        """Close the landing database. A later call does nothing."""
        db = self.db
        if db is None:
            return
        self.db = None
        try:
            db.close()
        except sqlite3.ProgrammingError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    @classmethod
    def for_repo(cls, path):
        common = seam.common_dir(path)
        directory = seam.state_home() / "landing" / store_name(common)
        if not (directory / "land.db").exists():
            raise seam.LandError(f"{common.parent} has no landing contract; run land.py init in it")
        store = cls(directory)
        try:
            if Path(store.contract["commonDir"]) != common:
                raise seam.LandError(f"store {directory} belongs to {store.contract['commonDir']}, not {common}")
        except BaseException:
            store.close()
            raise
        return store

    @contextlib.contextmanager
    def tx(self):
        """One short write transaction. Never run git or checks inside it."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    @contextlib.contextmanager
    def trial(self):
        """One transaction that sees its own writes and keeps none of them."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        finally:
            self.db.execute("ROLLBACK")

    @property
    def contract(self):
        contract = {row["key"]: json.loads(row["value"]) for row in self.db.execute("SELECT key, value FROM contract")}
        if "mode" in contract:
            contract["mode"] = seam.LEGACY_MODES.get(contract["mode"], contract["mode"])
        return contract

    @property
    def repo(self):
        return Path(self.contract["repo"])

    def log(self, db, kind, ident, state, note=""):
        db.execute("INSERT INTO log VALUES (?, ?, ?, ?, ?)", (seam.stamp(), kind, ident, state, note))

    def set_entry(self, db, ident, state, **fields):
        assignments = ", ".join(f"{key} = ?" for key in ["state", *fields])
        db.execute(f"UPDATE entry SET {assignments} WHERE id = ?", (state, *fields.values(), ident))
        self.log(db, "entry", ident, state, fields.get("note", ""))

    def entries(self, *states):
        marks = ",".join("?" * len(states))
        return list(self.db.execute(f"SELECT * FROM entry WHERE state IN ({marks}) ORDER BY id", states))


def migrate_entry_ids(db):
    """Copy an older entry table so ids are never reused after a bounced row is deleted.

    SQLite reuses the largest rowid once that row is gone, and a resubmit deletes
    the bounced row. AUTOINCREMENT can only be declared in CREATE TABLE. The copy
    keeps every existing id. sqlite_sequence is renamed with the table so the next
    id is one past the highest id the table has held.
    """
    row = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'entry'").fetchone()
    if row is None or "AUTOINCREMENT" in row["sql"].upper():
        return
    columns = [info["name"] for info in db.execute("PRAGMA table_info(entry)")]
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute("BEGIN")
    try:
        db.execute("""
            CREATE TABLE entry_id_keep (
              id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, holder TEXT NOT NULL, branch TEXT NOT NULL,
              sha TEXT NOT NULL, base TEXT NOT NULL, fingerprint TEXT NOT NULL, lease INTEGER NOT NULL REFERENCES lease(id),
              reviewer TEXT NOT NULL,
              state TEXT NOT NULL CHECK (state IN ('queued', 'landing', 'awaiting-merge', 'landed', 'bounced')),
              candidate TEXT NOT NULL DEFAULT '', landed TEXT NOT NULL DEFAULT '', pr TEXT NOT NULL DEFAULT '',
              note TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
              onto TEXT NOT NULL DEFAULT '', UNIQUE (sha, holder))
        """)
        names = ", ".join(columns)
        db.execute(f"INSERT INTO entry_id_keep ({names}) SELECT {names} FROM entry")
        db.execute("DROP TABLE entry")
        db.execute("ALTER TABLE entry_id_keep RENAME TO entry")
        if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
            db.execute("UPDATE sqlite_sequence SET name = 'entry' WHERE name = 'entry_id_keep'")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def store_name(common):
    name = common.parent.name if common.name == ".git" else common.name.removesuffix(".git")
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") + "-" + hashlib.sha1(str(common).encode()).hexdigest()[:8]


def pin_ref(sha):
    return f"refs/landing/pins/{sha}"


def committer(repo):
    """Cherry-picks need a committer. Use the repository's identity, or a fixed one when it has none."""
    if seam.git("config", "user.email", cwd=repo, check=False).stdout.strip():
        return []
    return ["-c", "user.name=pstack landing", "-c", "user.email=landing@localhost"]


Identity = namedtuple("Identity", "name email")


CREDIT_LINE = re.compile(r"Based on #[0-9]+ by @[A-Za-z0-9-]+\.?")


AUTHOR_POLICIES = ("any", "configured", "credited")


BRIGADE_ROOT = "BRIGADE_STORE"


STORE_RECORD = "restaurant.json"


DISHES_TABLE = "dishes.tsv"


GOALS_TABLE = "goals.tsv"


KINDS = ("feature", "fix", "polish", "upkeep", "study")


def _table_rows(path):
    """The rows of one coordinator store table, each keyed by the cell names of its header line.

    A table that does not exist or cannot be read has no rows. Finished bytes that are not UTF-8
    have no rows. A line that holds more cells than its header empties the whole table. Bytes after
    the last newline are dropped before decoding and are never a row.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return []
    finished = data.split(b"\n")[:-1]
    try:
        text = b"\n".join(finished).decode()
    except ValueError:
        return []
    lines = text.split("\n") if finished else []
    header = lines[0].split("\t") if lines else []
    rows = []
    for line in lines[1:]:
        cells = line.split("\t")
        if len(cells) > len(header):
            return []
        rows.append(dict(zip(header, cells)))
    return rows


def _store_record(directory):
    """The coordinator record of one store directory, and whether that record is absent.

    A record that exists but cannot be read, or that is not a JSON object, gives an empty record that is
    not absent, so the caller skips that store. A missing record is absent.
    """
    try:
        text = (directory / STORE_RECORD).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, True
    except (OSError, ValueError):
        return {}, False
    try:
        record = json.loads(text)
    except (ValueError, RecursionError):
        return {}, False
    return (record if isinstance(record, dict) else {}), False


def _same_directory(text, repo):
    """True when text resolves to the same directory as repo does."""
    if not isinstance(text, str) or not text:
        return False
    try:
        return Path(text).resolve() == Path(repo).resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def _has_table(directory):
    """True when the store directory holds a dishes table name, and False when the name cannot be reached."""
    try:
        return (directory / DISHES_TABLE).is_file()
    except OSError:
        return False


def _item_store(repo):
    """The coordinator store for this repository, or None.

    A store is a directory two levels under the store root, and the stores are tried in sorted name
    order. The store read is the first whose readable record names this repository as its project root.
    A store with no record, with an unreadable record, with a record that is not a JSON object, with
    role admin, or with no reachable dishes table is skipped.
    """
    root = Path(os.environ.get(BRIGADE_ROOT) or seam.state_home() / "brigade")
    try:
        candidates = sorted(directory for directory in root.glob("*/*") if directory.is_dir())
    except OSError:
        return None
    for directory in candidates:
        record, absent = _store_record(directory)
        if absent:
            continue
        if not _has_table(directory):
            continue
        if record.get("role") == "admin":
            continue
        if _same_directory(record.get("projectRoot"), repo):
            return directory
    return None


def _item_row(directory, branch):
    """The work item row of one store on this branch, the last row that names it, or None."""
    found = None
    for row in _table_rows(directory / DISHES_TABLE):
        if row.get("branch") == branch:
            found = row
    return found


def _goal_name(directory, ident):
    """The name the goals table of one store gives this goal id, or "" when it has no such row.

    A name that is empty, or that holds a carriage return or a line feed, reads as no name. The first
    row with the id wins. An empty id reads as no name without reading the table.
    """
    if not ident:
        return ""
    for row in _table_rows(directory / GOALS_TABLE):
        if row.get("id") == ident:
            name = row.get("name", "")
            return name if name and "\r" not in name and "\n" not in name else ""
    return ""


def _item_line(store, entry):
    """The Kind and Goal line of the coordinator's work item row on this entry's branch, or "".

    The kind prints when it is one of KINDS. The goal prints as the name its id has in the goals table,
    and a name that table rejects reads as no goal.
    """
    directory = _item_store(store.repo)
    if directory is None:
        return ""
    row = _item_row(directory, entry["branch"])
    if row is None:
        return ""
    kind = row.get("kind", "")
    goal = _goal_name(directory, row.get("goal", ""))
    parts = [f"Kind: {kind}"] if kind in KINDS else []
    if goal:
        parts.append(f"Goal: {goal}")
    return ". ".join(parts)


def configured_identity(repo):
    """git config user.name and user.email read in repo, with no character removed. None unless both have a non-empty value."""
    # -z ends a value with a NUL, which no value holds.
    name, email = (seam.git("config", "-z", "--get", key, cwd=repo, check=False, exact=True).stdout.removesuffix("\0")
                   for key in ("user.name", "user.email"))
    return Identity(name, email) if name and email else None


def entry_commits(repo, entry):
    """The id, author, and Co-authored-by values of each reviewed commit of an entry, oldest first."""
    fields = "%H%x00%an%x00%ae%x00%(trailers:key=Co-authored-by,valueonly,separator=%x01)%x02"
    listed = seam.git("log", "--reverse", f"--format={fields}", f"{entry['base']}..{pin_ref(entry['sha'])}", cwd=repo, exact=True).stdout
    for record in listed.split("\x02")[:-1]:
        sha, name, email, credits = record.lstrip("\n").split("\x00")
        yield sha, Identity(name, email), credits.split("\x01")


def pr_body(store, entry):
    """The body ensure_pr passes to gh pr create.

    The body is the submitted one when there is one, and otherwise the message body of the reviewed
    commit. When the contract sets prItemLine and the item has a line, it follows that body after one
    blank line and is the whole body when the body holds no text but whitespace. A missing key reads as off.
    """
    body = entry["body"] or seam.git("log", "-1", "--format=%b", entry["sha"], cwd=store.repo).stdout.rstrip()
    if not store.contract.get("prItemLine", False):
        return body
    line = _item_line(store, entry)
    if not line:
        return body
    text = body.rstrip()
    return f"{text}\n\n{line}" if text else line


def foreign_author(store, entry):
    """Why this entry cannot land on authorship, or None.

    The contract's authorPolicy decides the check. any returns None without reading git or the commits.
    configured and credited run the check only when configured_identity gives a value. configured bounces the entry
    when a reviewed commit's author is not that identity. Both policies pass when no reviewed commit has another
    author. Otherwise credited passes when every foreign commit carries a Co-authored-by trailer equal to its author
    as name <email> and a line of pr_body, with surrounding whitespace removed, matches CREDIT_LINE. It bounces the
    entry otherwise. The reason names the oldest commit that fails.
    """
    policy = store.contract.get("authorPolicy", "any")
    if policy == "any":
        return None
    identity = configured_identity(store.repo)
    if identity is None:
        return None
    foreign = [(sha, author, credits) for sha, author, credits in entry_commits(store.repo, entry) if author != identity]
    if not foreign:
        return None
    credited = policy == "credited" and any(CREDIT_LINE.fullmatch(line.strip()) for line in pr_body(store, entry).splitlines())
    for sha, author, credits in foreign:
        if not (credited and f"{author.name} <{author.email}>" in credits):
            return (f"commit {sha[:12]} has author {seam.one_line(f'{author.name} <{author.email}>')}, not the configured "
                    f"{seam.one_line(f'{identity.name} <{identity.email}>')}; recommit it as the configured author")
    return None


def author_policy_note(contract):
    """The stored author policy for repo status, defaulting to any when the key is absent."""
    policy = contract.get("authorPolicy", "any")
    return f"author policy: {policy}"


def pr_item_line_note(contract):
    """The stored item line setting for repo status. Empty unless on."""
    return "pr item line: on" if contract.get("prItemLine", False) else ""


IDENTITY_PAUSE = "the configured git identity is now "


def identity_change(store):
    """Why land must pause on the configured identity, or None.

    With no identity stored under the contract key identity it returns None, and it first stores
    configured_identity(store.repo) there when that is not None. With one stored it returns a reason unless
    configured_identity(store.repo) equals it in both name and email. The reason gives the stored identity,
    and the configured one or a note that it is unset.
    """
    configured, remembered = configured_identity(store.repo), store.contract.get("identity")
    if remembered is None:
        if configured:
            with store.tx() as db:
                db.execute("INSERT OR REPLACE INTO contract VALUES ('identity', ?)", (json.dumps(configured),))
        return None
    remembered = Identity(*remembered)
    if configured == remembered:
        return None
    now = seam.one_line(f"{configured.name} <{configured.email}>") if configured else \
        "unset (user.name and user.email do not both have a non-empty value)"
    return (f"{IDENTITY_PAUSE}{now} and the queue last ran as {seam.one_line(f'{remembered.name} <{remembered.email}>')}; "
            f"check git config --show-origin user.email in {seam.one_line(str(store.repo))}, then run land.py resume")


def git_reason(stderr):
    """The useful line of a git failure: what the remote said, else the last line."""
    lines = [line.strip() for line in stderr.strip().splitlines() if line.strip()]
    remote = [line for line in lines if line.startswith("remote:") and line != "remote:"]
    return " ".join(remote) if remote else (lines[-1] if lines else "no output")
