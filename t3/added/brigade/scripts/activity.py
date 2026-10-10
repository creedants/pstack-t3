#!/usr/bin/env python3
"""Show what one brigade coordinator's agents and sub-agents are doing, as one HTML page.

Opens the coordinator's store and T3 Code's state database read-only.
Prints one self-contained HTML document, or plain lines with --text, or writes either to the file --out names.

Every string the page takes from the store or from T3 is made by one filter, the class Privacy.
From each string the filter removes the exact text of each value below that is 4 characters or longer, as written and percent-decoded once and twice.
The values are the thread ids, sub-agent ids, and request names in the store and in T3's turn and sub-agent rows.
They are also the provider names and instance ids of the page's threads that are not one of this tool's provider names.
They are also each run of characters around an at sign in a title, a summary, a work item id, a pull request value, a model name, or the store's name.
They are also the paths of the store, the project it records, T3 Code's base directory and database, the home directory, and the --out file, each as given and with symbolic links followed.
From the first line of a title, a request name's words, a summary, a work item id, and the store's name the filter then drops each word that holds a slash, a backslash, a percent escape, a leading tilde, a colon or an at sign between two characters, a UUID, 6 or more digits, 7 or more hexadecimal characters with a digit and no letter or digit beside them, or an underscore before 6 or more letters and digits that hold a lower-case letter and a digit or an upper-case letter.
A model name is printed when it is at most 48 characters of lower-case letters and digits joined by single dots and hyphens, starts with a letter, and holds none of those values and none of those id shapes but 8 digits that start with 20. One leading provider name and slash is dropped first. Any other model reads `other model`.
A pull request link is printed only as https://host/owner/repository/pull/number, when the host, the owner, and the repository hold nothing the filter removes. A work item on the page with any other value has no link and is counted in a note.
A label can hold the words of a request name.
"""

import argparse
import enum
import fcntl
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import quote, unquote, urlsplit


DEFAULT_HOURS = 3.0
MIN_HOURS = 0.01
MAX_HOURS = 168.0
BUDGET = 16000
FIXED_BUDGET = 7500
# html_preview and html_render refuse more than 512000 characters.
MAX_BUDGET = 500000

PROJECTION = "thread-projections"
SUPPORTED_PROJECTION = 2
THREADS = "orchestration_v2_projection_threads"
RUNS = "orchestration_v2_projection_runs"
SUBAGENTS = "orchestration_v2_projection_subagents"
METADATA = "orchestration_v2_projection_metadata"
T3_SHAPE = {
    METADATA: ("projection_name", "schema_version"),
    THREADS: ("thread_id", "title", "default_provider", "payload_json"),
    RUNS: ("thread_id", "status", "requested_at", "completed_at"),
    SUBAGENTS: ("subagent_id", "thread_id", "child_thread_id", "status", "started_at", "completed_at"),
}
THREAD_PAYLOAD_KEY = "modelSelection"
SELECTION_KEYS = ("model", "instanceId")
# The forms read_t3 accepts. A live T3 database read on 2026-10-10 held no other form.
# Every value read_t3 selects is text. NULL is accepted only in the columns below, which T3's tables declare nullable.
# A thread id, a sub-agent id, a status, and a provider are never empty. A title can be empty.
# A timestamp is YYYY-MM-DDTHH:MM:SS, then an optional .mmm, then Z or a +HH:MM or -HH:MM offset.
# A thread's payload is a JSON object whose modelSelection is an object with a model and an instanceId that are not empty.
NULLABLE = {RUNS: ("completed_at",), SUBAGENTS: ("child_thread_id", "started_at", "completed_at")}
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{3})?(?:Z|[+-]\d\d:\d\d)", re.ASCII)
T3_DATABASE = "statev2.sqlite"
BATCH = 500
CHANGED = "this T3 build stores threads differently, so update pstack-t3"

STORE_RECORD = "restaurant.json"
STORE_LOCK = "restaurant.lock"
UNITS_TABLE = "dishes.tsv"
LOG_TABLE = "log.tsv"
STORE_COLUMNS = {
    UNITS_TABLE: ("id", "at", "state", "station", "tickets", "task", "thread", "branch", "pr", "sha", "summary", "timebox", "lease", "paths", "reported"),
    LOG_TABLE: ("at", "kind", "id", "state", "note"),
}
TABLE_WORDS = {UNITS_TABLE: "work item table", LOG_TABLE: "log"}

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
PROVIDERS = {"claudeAgent": ("Claude", 1), "codex": ("Codex", 2), "grok": ("Grok", 3), "opencode": ("OpenCode", 4), "cursor": ("Cursor", 5)}
OTHER_PROVIDER = ("Other", 6)
OTHER_MODEL = "other model"
UNGROUPED = "Not tied to a work item"
TITLE_CHARS = 60
LABEL_CHARS = 40


class Status(enum.Enum):
    RUNNING = "running"
    QUEUED = "queued"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


T3_STATUS = {
    "running": Status.RUNNING, "starting": Status.RUNNING,
    "queued": Status.QUEUED, "preparing": Status.QUEUED,
    "waiting": Status.WAITING, "idle": Status.WAITING,
    "completed": Status.DONE,
    "failed": Status.FAILED,
    "cancelled": Status.STOPPED, "interrupted": Status.STOPPED, "rolled_back": Status.STOPPED,
}
# The order is the order open_turn looks in, so an agent with a running turn reads running whatever else is queued behind it.
OPEN = (Status.RUNNING, Status.QUEUED)
LIVE = (*OPEN, Status.WAITING)
QUIET = (Status.DONE, Status.STOPPED)


class Evidence(enum.Enum):
    RECORD = "record"
    REQUEST = "request"
    LINEAGE = "lineage"
    NONE = "none"


class ActivityError(Exception):
    status = 1


class SourceError(ActivityError):
    status = 3


@dataclass(frozen=True)
class Unit:
    id: str
    state: str
    summary: str
    pr: str
    worker: str
    earlier_workers: tuple[str, ...]
    task: str


@dataclass(frozen=True)
class Store:
    name: str
    slug_parts: frozenset
    coordinators: tuple[str, ...]
    units: tuple[Unit, ...]
    project_root: str = ""


@dataclass(frozen=True)
class Window:
    start: float
    end: float


@dataclass(frozen=True)
class Turn:
    status: Status
    start: float
    end: Optional[float]


@dataclass(frozen=True)
class Delegation:
    status: Status
    start: float
    end: Optional[float]


@dataclass(frozen=True)
class Agent:
    thread: str
    parent: Optional[str]
    request: Optional[str]
    provider: str
    model: str
    title: str
    turns: tuple[Turn, ...]
    delegation: Optional[Delegation]


@dataclass(frozen=True)
class T3:
    agents: Mapping[str, Agent]
    node_thread: Mapping[str, str]
    other_threads: int
    unknown_status: int
    unstarted: int = 0
    private: frozenset = frozenset()


@dataclass(frozen=True)
class Assignment:
    unit: Optional[str]
    evidence: Evidence


@dataclass(frozen=True)
class Span:
    x: int
    w: int
    status: Status


@dataclass(frozen=True)
class Row:
    depth: int
    label: str
    model: str
    provider: str
    status: Status
    seconds: int
    spans: tuple[Span, ...]
    open_seconds: Optional[int] = None
    stands_for: int = 1


@dataclass(frozen=True)
class Item:
    id: str
    summary: str
    state: str
    tone: str
    pr: str
    in_flight: bool
    no_link: str = ""


@dataclass(frozen=True)
class Group:
    item: Optional[Item]
    rows: tuple[Row, ...]
    agents: int
    subagents: int


@dataclass(frozen=True)
class Totals:
    running: int
    agents: int
    subagents: int
    failed: int


@dataclass(frozen=True)
class Hidden:
    dropped_items: int = 0
    dropped_agents: int = 0
    cut_agents: int = 0
    cut_in_flight: int = 0
    other_threads: int = 0
    unknown_status: int = 0
    by_request_name: int = 0
    unstarted: int = 0


@dataclass(frozen=True)
class Page:
    name: str
    window: Window
    totals: Totals
    legend: tuple[tuple[str, int], ...]
    coordinator: Optional[Row]
    groups: tuple[Group, ...]
    items: tuple[Item, ...]
    hidden: Hidden
    fold: int = 0


def parser():
    top = argparse.ArgumentParser(prog="activity.py", description=__doc__)
    top.add_argument("--at", help="the coordinator's store directory (default $BRIGADE_DIR)")
    top.add_argument("--hours", type=float, default=DEFAULT_HOURS, help="how many hours back the page looks, from 0.01 to 168, rounded to whole seconds")
    top.add_argument("--text", action="store_true", help="print plain lines instead of the HTML document")
    top.add_argument("--out", help="write the output to this file and print `wrote <file> (<n> bytes)` instead; a file inside T3 Code's directory or the store is refused")
    top.add_argument("--max-bytes", type=int, default=BUDGET, help="the most bytes the HTML document and the newline after it take, from 16000 to 500000")
    top.add_argument("--t3-home", help="T3 Code's base directory (default $T3CODE_HOME, else ~/.t3)")
    return top


def main(argv=None):
    try:
        output = run(sys.argv[1:] if argv is None else argv)
    except ActivityError as error:
        print(f"activity: {error}", file=sys.stderr)
        return error.status
    sys.stdout.buffer.write(output.encode(errors="backslashreplace") + b"\n")
    return 0


def run(argv):
    args = parser().parse_args(argv)
    if not MIN_HOURS <= args.hours <= MAX_HOURS:
        raise ActivityError("--hours must be from 0.01 to 168; pass a number in that range")
    if not BUDGET <= args.max_bytes <= MAX_BUDGET:
        raise ActivityError("--max-bytes must be from 16000 to 500000; pass a number in that range")
    directory = store_dir(args.at, os.environ)
    store = read_store(directory)
    path = t3_database(args.t3_home, os.environ, Path.home())
    target = out_file(args.out, path, directory) if args.out else None
    now = time.time()
    window = Window(now - round(args.hours * 3600), now)
    connection = open_t3(path)
    try:
        check_shape(connection)
        check_coordinator(connection, store.coordinators)
        t3 = read_t3(connection, window, roots_of(store))
    except sqlite3.Error as error:
        raise unreadable(error, path) from None
    finally:
        connection.close()
    privacy = privacy_of(store, t3, (directory, path, path.parents[1], Path.home(), *((args.out,) if args.out else ())))
    page = build_page(store, t3, window, privacy)
    output = render_text(page) if args.text else fit(page, args.max_bytes - len("\n"))[1]
    if target is None:
        return output
    data = output.encode(errors="backslashreplace")
    try:
        target.write_bytes(data)
    except OSError as error:
        raise ActivityError(f"cannot write the --out file ({error.strerror}); pass a path this user can write") from None
    return f"wrote {args.out} ({len(data)} bytes)"


def out_file(flag, database, store):
    """The file --out names, with every symbolic link in its path followed.
    It is refused inside T3's base directory, inside the directory that holds the database, and inside the store.
    It is refused when it is the same file as the database or one of its two side files, which covers a hard link.
    """
    target = Path(os.path.realpath(flag))

    def inside(directory):
        return Path(os.path.realpath(directory)) in (target, *target.parents)

    def same(file):
        try:
            return os.path.samefile(target, file)
        except OSError:
            return False

    if inside(database.parents[1]) or inside(database.parent) or any(same(f"{database}{suffix}") for suffix in ("", "-wal", "-shm")):
        raise ActivityError("--out names a file inside T3 Code's directory or one of its database files; pass a path outside it")
    if inside(store):
        raise ActivityError("--out names a file inside the coordinator's store; pass a path outside it")
    return target


def store_dir(flag, environ):
    chosen = flag or environ.get("BRIGADE_DIR")
    if not chosen:
        raise ActivityError("pass --at <store directory> or set BRIGADE_DIR")
    return Path(chosen)


def read_table(directory, table):
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
    try:
        text = data.decode()
    except UnicodeDecodeError as error:
        raise malformed(table, data[:error.start].count(b"\n") + 1) from None
    rows = []
    for number, line in enumerate(text.split("\n")[1:-1], start=2):
        fields = line.split("\t")
        if len(fields) != len(columns):
            raise malformed(table, number)
        rows.append(dict(zip(columns, fields)))
    return rows


def malformed(table, number):
    return ActivityError(f"line {number} of the store's {TABLE_WORDS[table]} is malformed; fix or remove it")


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
    except OSError as error:
        raise ActivityError(f"cannot read the store ({error.strerror}); check its permissions and run this again") from None
    earlier = {}
    for row in log:
        if row["kind"] == "worker" and row["state"] == "retired" and row["note"]:
            earlier.setdefault(row["id"], []).append(row["note"])
    by_id = {
        row["id"]: Unit(row["id"], row["state"], row["summary"], row["pr"], row["thread"], tuple(earlier.get(row["id"], ())), row["task"])
        for row in units
    }
    name = meta.get("restaurant") if isinstance(meta.get("restaurant"), str) else ""
    root = meta["projectRoot"] if isinstance(meta.get("projectRoot"), str) else ""
    coordinators = tuple(value for value in (meta.get("thread"), meta.get("previousThread")) if isinstance(value, str) and value.strip())
    return Store(name, frozenset(re.findall(r"[a-z0-9]+", f"{name} {Path(root).name}".lower())), coordinators, tuple(by_id.values()), root)


def roots_of(store):
    workers = {thread for unit in store.units for thread in (unit.worker, *unit.earlier_workers) if thread}
    return frozenset(store.coordinators) | workers


def t3_database(flag, environ, home):
    variable = environ.get("T3CODE_HOME", "").strip()
    chosen = flag or variable
    source = "the --t3-home directory" if flag else "the T3CODE_HOME directory" if variable else "the default base directory"
    if chosen == "~" or chosen.startswith("~/"):
        base = home / chosen[2:]
    else:
        base = Path(chosen) if chosen else home / ".t3"
    for state in ("userdata",) if chosen else ("userdata", "dev"):
        if (base / state / T3_DATABASE).is_file():
            return base / state / T3_DATABASE
    raise SourceError(f"no T3 Code database under {source}; pass --t3-home <T3's base directory> or set T3CODE_HOME")


def open_t3(path):
    """With a write-ahead log and its index beside the database, mode=ro reads the log and creates no file.
    With no log, mode=ro would create both files, so the database is opened immutable.
    With a log and no index, a read would create the index or miss the log's rows, so the open is refused.
    """
    log, index = Path(f"{path}-wal").exists(), Path(f"{path}-shm").exists()
    if log and not index:
        raise SourceError("T3's database has a write-ahead log with no index beside it; start T3 Code and run this again")
    try:
        connection = sqlite3.connect(f"file:{quote(str(path))}?mode=ro" + ("" if log else "&immutable=1"), uri=True, timeout=2, isolation_level=None)
    except sqlite3.Error as error:
        raise unreadable(error, path) from None
    try:
        connection.execute("PRAGMA query_only=1")
        connection.execute("BEGIN")
    except sqlite3.Error as error:
        connection.close()
        raise unreadable(error, path) from None
    return connection


def unreadable(error, path):
    text = str(error)
    if text.startswith("Could not decode to UTF-8"):
        text = "a text value is not UTF-8"
    for known in (str(path), quote(str(path)), str(Path(path).parent)):
        text = text.replace(known, "")
    words = " ".join(word for word in text.split() if "/" not in word and "\\" not in word)
    return SourceError(f"cannot read T3's database ({words}); check that T3 Code is running and try again")


def check_shape(connection):
    tables = {name for (name,) in connection.execute("select name from sqlite_master where type in ('table', 'view')")}
    for table, columns in T3_SHAPE.items():
        if table not in tables:
            raise SourceError(f"T3's database has no table {table}; {CHANGED}")
        present = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for column in columns:
            if column not in present:
                raise SourceError(f"T3's table {table} has no column {column}; {CHANGED}")
    record = connection.execute(f"select schema_version from {METADATA} where projection_name = ?", (PROJECTION,)).fetchone()
    if record is None:
        raise SourceError(f"T3's database has no {PROJECTION} record; {CHANGED}")
    if record[0] != SUPPORTED_PROJECTION:
        raise SourceError(f"T3's thread records are not version {SUPPORTED_PROJECTION}, the version this tool reads; update pstack-t3")


def check_coordinator(connection, coordinators):
    if not coordinators:
        return
    marks = ", ".join("?" * len(coordinators))
    if connection.execute(f"select 1 from {THREADS} where thread_id in ({marks}) limit 1", coordinators).fetchone() is None:
        raise SourceError("T3's database has no record of this coordinator's thread; pass --t3-home <the base directory of the T3 that runs it>")


def parse_time(text, where):
    try:
        if not STAMP.fullmatch(text):
            raise ValueError
        # Python 3.10 rejects the Z that T3 writes.
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        raise SourceError(f"T3's {where} is not a timestamp; {CHANGED}") from None


def text_in(value, table, column, empty=False):
    if not isinstance(value, str):
        raise SourceError(f"T3's {table}.{column} is not text; {CHANGED}")
    if not value and not empty:
        raise SourceError(f"T3's {table}.{column} is empty; {CHANGED}")
    return value


def time_in(value, table, column):
    if value is None and column in NULLABLE.get(table, ()):
        return None
    return parse_time(value, f"{table}.{column}")


def parse_status(text, ended):
    """A running or queued status on a row with an end, and a finished status on a row with none, contradict the row and read unknown."""
    status = T3_STATUS.get(text, Status.UNKNOWN)
    return Status.UNKNOWN if (status in OPEN if ended else status not in LIVE) else status


def request_name(thread_id):
    """A delegated child's thread id holds, after URL decoding, `delegate-task:<request name>` at its end."""
    _, marker, name = unquote(thread_id).rpartition("delegate-task:")
    return name if marker and name else None


def in_scope(parent_of, roots, active):
    kept, others = set(), 0
    for thread in active:
        chain, seen = [], set()
        while thread is not None and thread not in roots and thread not in seen:
            seen.add(thread)
            chain.append(thread)
            thread = parent_of.get(thread)
        if thread in roots:
            kept.update(chain, [thread])
        else:
            others += 1
    return kept, others


def batches(values):
    values = sorted(values)
    return [values[start:start + BATCH] for start in range(0, len(values), BATCH)]


def touches(start, end, window):
    return start <= window.end and (end is None or end >= window.start)


def read_t3(connection, window, roots):
    parent_of, delegations, nodes, unstarted, seen = {}, {}, {}, [], set()
    for node, parent, child, status, started, completed in connection.execute(
            f"select subagent_id, thread_id, child_thread_id, status, started_at, completed_at from {SUBAGENTS}"):
        node, parent, status = (text_in(value, SUBAGENTS, column) for value, column in ((node, "subagent_id"), (parent, "thread_id"), (status, "status")))
        start, end = time_in(started, SUBAGENTS, "started_at"), time_in(completed, SUBAGENTS, "completed_at")
        seen.update((node, parent))
        if child is None:
            unstarted.append((parent, None))
            continue
        child = text_in(child, SUBAGENTS, "child_thread_id")
        seen.add(child)
        nodes[node] = child
        if start is None:
            parent_of.setdefault(child, parent)
            unstarted.append((parent, child))
        elif child not in delegations or start >= delegations[child].start:
            parent_of[child] = parent
            delegations[child] = Delegation(parse_status(status, end is not None), start, end)
    # Every turn is read and compared as an instant. A filter on the stored text would hide a row whose text sorts before the window.
    turns, ran = {}, set()
    for thread, status, requested, completed in connection.execute(f"select thread_id, status, requested_at, completed_at from {RUNS}"):
        thread, status = text_in(thread, RUNS, "thread_id"), text_in(status, RUNS, "status")
        start, end = time_in(requested, RUNS, "requested_at"), time_in(completed, RUNS, "completed_at")
        ran.add(thread)
        if touches(start, end, window):
            turns.setdefault(thread, []).append(Turn(parse_status(status, end is not None), start, end))
    idle = [child for child, delegation in delegations.items() if child not in ran and touches(delegation.start, delegation.end, window)]
    active = set(turns) | set(idle)
    kept, others = in_scope(parent_of, roots, active)
    described = {}
    for batch in batches(kept):
        marks = ", ".join("?" * len(batch))
        for thread, title, provider, payload in connection.execute(
                f"select thread_id, title, default_provider, payload_json from {THREADS} where thread_id in ({marks})", batch):
            described[thread] = (text_in(title, THREADS, "title", empty=True), text_in(provider, THREADS, "default_provider"), *selection_of(payload))
    agents, instances = {}, set()
    for thread in sorted(kept):
        if thread not in described:
            raise SourceError(f"T3's {THREADS} has no row for a thread that {RUNS} or {SUBAGENTS} names; {CHANGED}")
        title, provider, model, instance = described[thread]
        instances.update((provider, instance))
        own = tuple(sorted(turns.get(thread, ()), key=lambda turn: turn.start))
        delegation = delegations.get(thread)
        if delegation and not (thread in active and touches(delegation.start, delegation.end, window)):
            delegation = None
        agents[thread] = Agent(thread, parent_of.get(thread), request_name(thread), provider, model, title, own, delegation)
    statuses = [record.status for agent in agents.values() for record in (*agent.turns, agent.delegation) if record]
    waiting = sum(parent in kept and (child is None or child not in turns) for parent, child in unstarted)
    private = frozenset(seen | ran | (instances - set(PROVIDERS)))
    return T3(agents, {node: child for node, child in nodes.items() if child in kept}, others, statuses.count(Status.UNKNOWN), waiting, private)


def selection_of(payload):
    try:
        data = json.loads(payload) if isinstance(payload, str) else None
    except (ValueError, RecursionError):
        data = None
    if not isinstance(data, dict) or THREAD_PAYLOAD_KEY not in data:
        raise SourceError(f"T3's thread payload has no {THREAD_PAYLOAD_KEY}; {CHANGED}")
    selection = data[THREAD_PAYLOAD_KEY]
    for key in SELECTION_KEYS:
        value = selection.get(key) if isinstance(selection, dict) else None
        if not isinstance(value, str) or not value:
            raise SourceError(f"T3's thread payload holds no text at {THREAD_PAYLOAD_KEY}.{key}; {CHANGED}")
    return tuple(selection[key] for key in SELECTION_KEYS)


UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
ID_LIKE = rf"{UUID}|(?<![0-9A-Za-z])(?=[0-9a-fA-F]*[0-9])[0-9a-fA-F]{{7,}}(?![0-9A-Za-z])|[0-9]{{6,}}|_(?=[0-9A-Za-z]*[0-9A-Z])(?=[0-9A-Za-z]*[a-z])[0-9A-Za-z]{{6,}}"
# The shapes of a word the module docstring says the filter drops.
PRIVATE_WORD = re.compile(rf"[/\\]|^~|%[0-9A-Fa-f]{{2}}|.[:@].|{ID_LIKE}")
OPENERS = "\"'`([<{"
EMAIL = re.compile(r"[^\s@<>()\[\]\"',;:]+@[^\s@<>()\[\]\"',;:]+")
MODEL = re.compile(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*")
MODEL_CHARS = 48
MODEL_DATE = re.compile(r"20[0-9]{6}")
HOST = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
PULL = re.compile(r"/(?!\.+/)([A-Za-z0-9._-]+)/(?!\.+/)([A-Za-z0-9._-]+)/pull/[0-9]+")
ANCHOR = 4
REFUSED, CUT = "refused", "cut"
PATH_TAIL = re.compile(r"/root/(?:\S*/)?([A-Za-z0-9_-]+)")
SLUG = re.compile(r"[a-z0-9.]+(?:-[a-z0-9.]+)+")
ROLE = re.compile(r"Act as the (.+?) sub-agent")
UNIT_SUFFIX = re.compile(r"(.+?)[a-z][0-9]*")
BAR = {
    Status.RUNNING: Status.RUNNING, Status.QUEUED: Status.QUEUED,
    Status.WAITING: Status.DONE, Status.DONE: Status.DONE,
    Status.FAILED: Status.FAILED, Status.STOPPED: Status.STOPPED, Status.UNKNOWN: Status.UNKNOWN,
}


class Privacy:
    """The filter every string from the store or from T3 passes before it is on a page.
    text() is that filter. model() and link() call it and accept one fixed shape each.
    """

    def __init__(self, values):
        forms = set()
        for value in values:
            for _ in range(3):
                forms.add(value)
                value = unquote(value)
        index = {}
        for form in forms:
            if len(form) >= ANCHOR:
                index.setdefault(form[:ANCHOR], []).append(form)
        self.index = {anchor: sorted(found, key=len, reverse=True) for anchor, found in index.items()}

    def without_known(self, text):
        """text with a space in place of each value this run read, the longest value first where two start at one place."""
        kept, at = [], 0
        while at < len(text):
            found = next((value for value in self.index.get(text[at:at + ANCHOR], ()) if text.startswith(value, at)), None)
            kept.append(" " if found else text[at])
            at += len(found) if found else 1
        return "".join(kept)

    def text(self, value):
        """The first line of value with single spaces, repeated until it holds no value this run read and no word of a private shape."""
        lines = value.encode("utf-8", "ignore").decode().strip().splitlines()
        line = " ".join(lines[0].split()) if lines else ""
        while True:
            words = self.without_known(line).split()
            kept = " ".join(word for word in words if not PRIVATE_WORD.search(word.lstrip(OPENERS)))
            if kept == line:
                return kept
            line = kept

    def model(self, value):
        """value without one leading `<a key of PROVIDERS>/`, or OTHER_MODEL when that is not a model name by the module docstring's rule."""
        driver, slash, rest = value.partition("/")
        name = rest if slash and driver in PROVIDERS else value
        parts = [part for part in re.split("[.-]", name) if not MODEL_DATE.fullmatch(part)]
        plain = MODEL.fullmatch(name) and len(name) <= MODEL_CHARS and self.text(" ".join(parts)) == " ".join(parts) and self.without_known(name) == name
        return name if plain else OTHER_MODEL

    def link(self, value):
        """The pull request address, or no address and REFUSED. A blank value gives neither. Userinfo, a query, and a fragment are not kept. A value with a port is refused."""
        if not value.strip():
            return "", ""
        try:
            parts = urlsplit(value)
            host, port = parts.hostname or "", parts.port
        except ValueError:
            return "", REFUSED
        address = PULL.fullmatch(parts.path)
        if parts.scheme != "https" or port is not None or not HOST.fullmatch(host) or not address:
            return "", REFUSED
        if any(self.text(word) != word for word in (host, *address.groups())):
            return "", REFUSED
        return f"https://{host}{parts.path}", ""


def privacy_of(store, t3, paths=()):
    ids = {*store.coordinators, *t3.private}
    for unit in store.units:
        ids.update((unit.worker, unit.task, *unit.earlier_workers))
    names = {request_name(value) or "" for value in ids}
    places = {form for path in (store.project_root, *map(str, paths)) if path for form in (path, os.path.realpath(path))}
    read = [store.name, *(text for unit in store.units for text in (unit.id, unit.summary, unit.pr)),
            *(text for agent in t3.agents.values() for text in (agent.title, agent.model))]
    return Privacy({*ids, *names, *places, *(address for text in read for address in EMAIL.findall(text))})


def unit_named(part, units):
    suffixed = UNIT_SUFFIX.fullmatch(part)
    return units.get(part) or (units.get(suffixed.group(1)) if suffixed else None)


def assign(store, t3):
    coordinators = frozenset(store.coordinators)
    units = {unit.id.lower(): unit.id for unit in store.units}
    requested = {agent.request: thread for thread, agent in t3.agents.items() if agent.request and agent.parent in coordinators}
    recorded = {}
    for unit in store.units:
        for thread in (*unit.earlier_workers, unit.worker, t3.node_thread.get(unit.task), requested.get(unit.task)):
            if thread:
                recorded[thread] = unit.id
    found = {}
    for start in t3.agents:
        thread, below = start, []
        while thread not in found:
            agent = t3.agents[thread]
            if thread in recorded:
                found[thread] = Assignment(recorded[thread], Evidence.RECORD)
            elif agent.parent in coordinators:
                named = (unit_named(part, units) for part in (agent.request or "").split("-"))
                unit = next((unit for unit in named if unit), None)
                found[thread] = Assignment(unit, Evidence.REQUEST if unit else Evidence.NONE)
            elif agent.parent in t3.agents and agent.parent not in below:
                below.append(thread)
                thread = agent.parent
            else:
                found[thread] = Assignment(None, Evidence.NONE)
        unit = found[thread].unit
        for child in below:
            found[child] = Assignment(unit, Evidence.LINEAGE if unit else Evidence.NONE)
    return {thread: assignment for thread, assignment in found.items() if thread not in coordinators}


def words_of(name, store, units, privacy):
    parts = name.split("-")
    if parts[0] == "brigade":
        del parts[0]
    while parts and parts[0] in store.slug_parts:
        del parts[0]
    return privacy.text(" ".join("review" if part == "verify" else part for part in parts if not unit_named(part, units)))


def label_of(agent, store, unit, privacy):
    if any(agent.thread == other.worker for other in store.units):
        return "worker"
    if any(agent.thread in other.earlier_workers for other in store.units):
        return "earlier worker"
    units = {other.id.lower(): other.id for other in store.units}
    line = " ".join((agent.title.strip().splitlines() or [""])[0].split())
    written = ""
    if len(line) <= TITLE_CHARS and not line.startswith(("Act as", "You are")) and privacy.without_known(line) == line:
        tail = PATH_TAIL.fullmatch(line)
        whole = tail.group(1) if tail else line
        written = privacy.text(whole)
        if written != whole:
            written = ""
        if unit:
            written = re.sub(rf"^{re.escape(unit)}(?::\s*|\s+)", "", written)
        if SLUG.fullmatch(written):
            written = words_of(written, store, units, privacy)
    role = ROLE.match(line)
    for text in (written, words_of(agent.request or "", store, units, privacy), privacy.text(role.group(1)) if role else ""):
        if text:
            return text if len(text) <= LABEL_CHARS else text[:LABEL_CHARS - 1].rstrip() + "…"
    return "sub-agent" if agent.parent else "agent"


def stretches(agent):
    delegation = agent.delegation
    if agent.turns or delegation is None:
        return agent.turns
    return (Turn(delegation.status, delegation.start, delegation.end),)


def open_turn(agent):
    for status in OPEN:
        for turn in reversed(stretches(agent)):
            if turn.end is None and turn.status is status:
                return turn
    return None


def status_of(agent):
    turn, delegation = open_turn(agent), agent.delegation
    if turn:
        return turn.status
    if delegation:
        return Status.WAITING if delegation.end is None else delegation.status
    return agent.turns[-1].status if agent.turns else Status.DONE


def spans_of(agent, window):
    length = window.end - window.start

    def point(moment):
        return round((min(max(moment, window.start), window.end) - window.start) / length * 1000)

    spans = []
    for turn in stretches(agent):
        x = min(point(turn.start), 999)
        right = max(point(window.end if turn.end is None else turn.end), x + 1)
        status, last = BAR[turn.status], spans[-1] if spans else None
        if last and last.status is status and turn.end is not None and x - (last.x + last.w) < 5:
            spans[-1] = Span(last.x, max(last.x + last.w, right) - last.x, status)
        else:
            spans.append(Span(x, right - x, status))
    return tuple(spans)


def seconds_of(agent, window):
    """The seconds of the window inside this agent's stretches. A queued stretch is not work, so it adds none."""
    worked = [turn for turn in stretches(agent) if turn.status is not Status.QUEUED]
    return int(sum(max(0, min(window.end, window.end if turn.end is None else turn.end) - max(window.start, turn.start)) for turn in worked))


def tree(agents):
    def began(agent):
        return (min((turn.start for turn in stretches(agent)), default=0), agent.thread)

    inside = {agent.thread for agent in agents}
    children, order, stack = {}, [], []
    for agent in sorted(agents, key=began, reverse=True):
        if agent.parent in inside:
            children.setdefault(agent.parent, []).append(agent)
        else:
            stack.append((agent, 0))
    while stack:
        agent, depth = stack.pop()
        order.append((agent, min(depth, 2)))
        stack.extend((child, depth + 1) for child in children.get(agent.thread, ()))
    return order


def with_parents(agents):
    by_thread = {agent.thread: agent for agent in agents}
    kept = set()
    for agent in agents:
        thread = agent.thread if stretches(agent) else None
        while thread in by_thread and thread not in kept:
            kept.add(thread)
            thread = by_thread[thread].parent
    return [agent for agent in agents if agent.thread in kept]


def item_of(unit, privacy):
    word, tone = STATE_WORDS.get(unit.state, UNKNOWN_STATE)
    link, why = privacy.link(unit.pr)
    return Item(privacy.text(unit.id), privacy.text(unit.summary), word, tone, link, unit.state not in FINISHED_STATES, why)


def build_page(store, t3, window, privacy):
    """Each string of the page that comes from the store or from T3 is made by privacy.text, privacy.model, or privacy.link.
    The other strings are the constants of this file and numbers.
    """
    def row(agent, depth, label):
        turn = open_turn(agent)
        return Row(depth, label, privacy.model(agent.model), PROVIDERS.get(agent.provider, OTHER_PROVIDER)[0], status_of(agent),
                   seconds_of(agent, window), spans_of(agent, window), int(window.end - turn.start) if turn else None, 1 if stretches(agent) else 0)

    def order(entry):
        unit, agents = entry
        ends = [window.end if turn.end is None else turn.end for agent in agents for turn in stretches(agent)]
        return (unit is None, all(open_turn(agent) is None for agent in agents), -max(ends, default=window.start), unit)

    placed = assign(store, t3)
    units = {unit.id: unit for unit in store.units}
    members = {}
    for thread, assignment in placed.items():
        members.setdefault(assignment.unit, []).append(t3.agents[thread])
    members = {unit: with_parents(agents) for unit, agents in members.items()}
    groups, by_request = [], 0
    for unit, agents in sorted(((unit, agents) for unit, agents in members.items() if agents), key=order):
        rows = tuple(row(agent, depth, label_of(agent, store, unit, privacy)) for agent, depth in tree(agents))
        active = [agent for agent in agents if stretches(agent)]
        subagents = sum(agent.parent is not None for agent in active)
        by_request += sum(placed[agent.thread].evidence is Evidence.REQUEST for agent in active)
        groups.append(Group(item_of(units[unit], privacy) if unit else None, rows, len(active) - subagents, subagents))
    everyone = [row for group in groups for row in group.rows if row.stands_for]
    subagents = sum(group.subagents for group in groups)
    totals = Totals(sum(row.status is Status.RUNNING for row in everyone), len(everyone) - subagents, subagents, sum(row.status is Status.FAILED for row in everyone))
    providers = [row.provider for row in everyone]
    legend = tuple(sorted(((name, providers.count(name)) for name in set(providers)), key=lambda entry: (-entry[1], entry[0])))
    own = None
    coordinators = [t3.agents[thread] for thread in store.coordinators if thread in t3.agents]
    if coordinators:
        turns = sorted((turn for agent in coordinators for turn in agent.turns), key=lambda turn: turn.start)
        own = row(replace(coordinators[0], turns=tuple(turns), delegation=None), 0, "coordinator")
        bars = tuple(replace(span, status=Status.DONE) if span.status is Status.RUNNING else span for span in own.spans)
        own = replace(own, spans=bars, open_seconds=None)

    def number(unit):
        digits = re.search(r"[0-9]{1,18}", unit.id)
        return (int(digits.group()) if digits else 0, unit.id)

    items = tuple(item_of(unit, privacy) for unit in sorted(units.values(), key=number) if unit.state not in FINISHED_STATES)
    hidden = Hidden(other_threads=t3.other_threads, unknown_status=t3.unknown_status, by_request_name=by_request, unstarted=t3.unstarted)
    return Page(privacy.text(store.name) or "this coordinator", window, totals, legend, own, tuple(groups), items, hidden)


KEPT_ITEMS = (24, 16, 12, 8, 6, 4, 2, 0)
MAX_GROUPS = 6
MAX_ROWS = 6
MAX_SPANS = 4
COORDINATOR_SPANS = 8
MAX_STRIP = 8
NAME_BYTES = 36
ID_BYTES = 12
LABEL_BYTES = 20
SUMMARY_BYTES = 36
MODEL_BYTES = 30
LINK_BYTES = 90
JOINED = (Status.RUNNING, Status.QUEUED, Status.FAILED, Status.UNKNOWN, Status.STOPPED, Status.DONE)


ENTITIES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"), ("\\", "&#92;"))
UNPRINTED = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def entities(text):
    text = UNPRINTED.sub(" ", text)
    for character, entity in ENTITIES:
        text = text.replace(character, entity)
    return text


def encode(data):
    def written(value):
        if isinstance(value, str):
            return entities(value)
        if isinstance(value, dict):
            return {key: written(each) for key, each in value.items()}
        return [written(each) for each in value] if isinstance(value, (list, tuple)) else value

    return json.dumps(written(data), separators=(",", ":"), ensure_ascii=False)


def size(text):
    return len(entities(text).encode())


def clip(text, limit):
    if size(text) <= limit:
        return text
    kept = min(len(text), limit)
    while kept and size(text[:kept] + "…") > limit:
        kept -= 1
    return text[:kept] + "…"


def count(number, noun):
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def people(agents, subagents):
    return ", ".join(count(number, noun) for number, noun in ((agents, "agent"), (subagents, "sub-agent")) if number)


def agents_in(rows):
    return sum(row.stands_for for row in rows)


def union(spans, status):
    bars = []
    for span in sorted(spans, key=lambda span: span.x):
        if bars and span.x <= bars[-1].x + bars[-1].w:
            bars[-1] = Span(bars[-1].x, max(bars[-1].x + bars[-1].w, span.x + span.w) - bars[-1].x, status)
        else:
            bars.append(Span(span.x, span.w, status))
    return bars


def summary(rows, depth, label):
    spans = [span for row in rows for span in row.spans]
    failed = [span for span in spans if span.status is Status.FAILED]
    bars = union((span for span in spans if span.status is not Status.FAILED), Status.DONE) + union(failed, Status.FAILED)
    models, providers = {row.model for row in rows}, {row.provider for row in rows}
    return Row(depth, label, models.pop() if len(models) == 1 else "", providers.pop() if len(providers) == 1 else "", Status.DONE,
               sum(row.seconds for row in rows), tuple(bars), None, agents_in(rows))


def families(rows):
    runs = []
    for row in rows:
        if row.depth == 0 or not runs:
            runs.append([])
        runs[-1].append(row)
    return runs


def kept_rows(rows, keeps):
    kept, above = [], []
    for row, keep in zip(rows, keeps):
        del above[row.depth:]
        if keep:
            kept.append(replace(row, depth=sum(above)))
        above.append(keep)
    return kept


def finished(group):
    return group.item is not None and not group.item.in_flight and not any(row.status in LIVE for row in group.rows)


def fold_finished_items(page):
    groups = tuple(
        replace(group, rows=(summary(group.rows, 0, people(group.agents, group.subagents)),)) if finished(group) and agents_in(group.rows) >= 2 else group
        for group in page.groups)
    return replace(page, groups=groups, fold=page.fold + 1)


def fold_quiet_subagents(page):
    groups = []
    for group in page.groups:
        rows = []
        for family in families(group.rows):
            folds = [index > 0 and row.status in QUIET for index, row in enumerate(family)]
            folded = [row for row, fold in zip(family, folds) if fold]
            if agents_in(folded) >= 2:
                family = [*kept_rows(family, [not fold for fold in folds]), summary(folded, 1, count(agents_in(folded), "sub-agent"))]
            rows += family
        groups.append(replace(group, rows=tuple(rows)))
    return replace(page, groups=tuple(groups), fold=page.fold + 1)


def keep_finished_items(limit, page):
    groups, seen, items, agents = [], 0, 0, 0
    for group in page.groups:
        seen += finished(group)
        if finished(group) and seen > limit:
            items, agents = items + 1, agents + agents_in(group.rows)
        else:
            groups.append(group)
    hidden = replace(page.hidden, dropped_items=page.hidden.dropped_items + items, dropped_agents=page.hidden.dropped_agents + agents)
    return replace(page, groups=tuple(groups), hidden=hidden, fold=page.fold + 1)


def first(values, limit, urgent):
    chosen = set(sorted(range(len(values)), key=lambda index: (not urgent(values[index]), index))[:limit])
    return [index in chosen for index in range(len(values))]


def joined(spans, limit):
    spans = sorted(spans, key=lambda span: span.x)
    while len(spans) > limit:
        at = min(range(len(spans) - 1), key=lambda index: spans[index + 1].x - spans[index].x - spans[index].w)
        left, right = spans[at], spans[at + 1]
        status = min(left.status, right.status, key=JOINED.index)
        spans[at:at + 2] = [Span(left.x, max(left.x + left.w, right.x + right.w) - left.x, status)]
    return tuple(sorted(spans, key=lambda span: span.status is Status.FAILED))


def cap_everything(page):
    def shown(item):
        fits = size(item.pr) <= LINK_BYTES
        return replace(item, id=clip(item.id, ID_BYTES), summary=clip(item.summary, SUMMARY_BYTES), pr=item.pr if fits else "", no_link=item.no_link if fits else CUT)

    def narrow(row, limit):
        return replace(row, label=clip(row.label, LABEL_BYTES), model=clip(row.model, MODEL_BYTES), spans=joined(row.spans, limit))

    def running(row):
        return row.open_seconds is not None

    groups, cut, gone_items, gone_agents = [], 0, 0, 0
    stays = first(page.groups, MAX_GROUPS, lambda group: any(running(row) for row in group.rows))
    for group, stay in zip(page.groups, stays):
        keeps = first(group.rows, MAX_ROWS if stay else 0, lambda row: running(row) or row.status is Status.FAILED)
        rows = tuple(narrow(row, MAX_SPANS) for row in kept_rows(group.rows, keeps))
        lost = agents_in(group.rows) - agents_in(rows)
        if stay:
            groups.append(replace(group, item=shown(group.item) if group.item else None, rows=rows))
        # A cut group's item in flight is in page.items, so the strip lists it or cut_in_flight counts it. Any other item is counted here.
        if not stay and group.item and not group.item.in_flight:
            gone_items, gone_agents = gone_items + 1, gone_agents + lost
        else:
            cut += lost
    drawn = [group.item for group in groups]
    listed = first(page.items, MAX_STRIP, lambda item: shown(item) in drawn)
    items = tuple(shown(item) for item, keep in zip(page.items, listed) if keep)
    hidden = replace(page.hidden, cut_agents=page.hidden.cut_agents + cut, cut_in_flight=page.hidden.cut_in_flight + len(page.items) - len(items),
                     dropped_items=page.hidden.dropped_items + gone_items, dropped_agents=page.hidden.dropped_agents + gone_agents)
    coordinator = narrow(page.coordinator, COORDINATOR_SPANS) if page.coordinator else None
    return replace(page, name=clip(page.name, NAME_BYTES), coordinator=coordinator, groups=tuple(groups), items=items, hidden=hidden, fold=page.fold + 1)


FOLDS = (fold_finished_items, fold_quiet_subagents, *(partial(keep_finished_items, limit) for limit in KEPT_ITEMS), cap_everything)


def fit(page, budget):
    document = render_html(page)
    for fold in FOLDS:
        if len(document.encode()) <= budget:
            break
        page = fold(page)
        document = render_html(page)
    return page, document


def say(number, one, several, **values):
    return (one if number == 1 else several).format(n=number, **values)


def items_of(page):
    return [*page.items, *dict.fromkeys(group.item for group in page.groups if group.item and group.item not in page.items)]


def notes(page):
    rows = [row for group in page.groups for row in group.rows]
    links = [item.no_link for item in items_of(page)]
    below = [row.stands_for for row in rows if row.stands_for > 1 and row.depth]
    whole = [row for row in rows if row.stands_for > 1 and not row.depth]
    hidden, lines = page.hidden, []
    if not page.totals.agents + page.totals.subagents:
        lines.append("No agent or sub-agent of this coordinator ran in this window.")
    if below:
        lines.append(say(len(below), "{agents} sub-agents that are done or stopped are shown as 1 summary row.",
                         "{agents} sub-agents that are done or stopped are shown as {n} summary rows.", agents=sum(below)))
    if whole:
        lines.append(say(len(whole), "1 merged or dropped work item with no agent running, queued, or waiting is shown as one row.",
                         "{n} merged or dropped work items with no agent running, queued, or waiting are each shown as one row."))
    if hidden.dropped_items:
        lines.append(say(hidden.dropped_items, "1 merged or dropped work item with {agents} is not shown. Use a larger --max-bytes to see more.",
                         "{n} merged or dropped work items with {agents} are not shown. Use a larger --max-bytes to see more.",
                         agents=count(hidden.dropped_agents, "agent")))
    if hidden.cut_agents:
        lines.append(say(hidden.cut_agents, "1 more agent is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
                         "{n} more agents are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."))
    if hidden.cut_in_flight:
        lines.append(say(hidden.cut_in_flight, "1 more work item in flight is not listed.", "{n} more work items in flight are not listed."))
    if CUT in links:
        lines.append(say(links.count(CUT), "1 pull request link is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
                         "{n} pull request links are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."))
    if REFUSED in links:
        lines.append(say(links.count(REFUSED), "1 pull request link is not shown. It is not an https://host/owner/repository/pull/number address, or it holds text this page removes.",
                         "{n} pull request links are not shown. Each is not an https://host/owner/repository/pull/number address, or it holds text this page removes."))
    if hidden.unstarted:
        lines.append(say(hidden.unstarted, "T3 lists 1 sub-agent of a thread on this page with no thread or no start time. It is not shown.",
                         "T3 lists {n} sub-agents of threads on this page with no thread or no start time. They are not shown."))
    if hidden.unknown_status:
        lines.append(say(hidden.unknown_status, "T3 gave 1 status this tool reads as unknown.", "T3 gave {n} statuses this tool reads as unknown."))
    if hidden.by_request_name:
        lines.append(say(hidden.by_request_name, "1 agent is grouped by the name of the request that started it.",
                         "{n} agents are grouped by the name of the request that started them."))
    if hidden.other_threads:
        lines.append(say(hidden.other_threads, "1 other thread ran in T3 outside this coordinator.", "{n} other threads ran in T3 outside this coordinator."))
    return lines


WIRE_VERSION = 1
WIRE_STATUS = (Status.RUNNING, Status.QUEUED, Status.WAITING, Status.DONE, Status.FAILED, Status.STOPPED, Status.UNKNOWN)
WIRE_BARS = (Status.DONE, Status.RUNNING, Status.FAILED, Status.STOPPED, Status.UNKNOWN, Status.QUEUED)
TEXT_RUNNING = 7
TEXT_QUEUED = 4
TEXT_ITEMS = 9
TEXT_FAILED = 4


def joined_lines(source):
    return "".join(line.strip() for line in source.splitlines())


def squeezed(source):
    def tight(code):
        return re.sub(r"\s+", " ", re.sub(r"\s*([^\w\s$.])\s*", r"\1", code)).replace(";}", "}")

    return "".join(part if part.startswith("'") else tight(part) for part in re.split(r"('[^']*')", source.strip()))


# The page sets no background on html, body, or #o. The only colors it names are theme variables of html_render, its own variable --c, inherit, and transparent.
STYLE = joined_lines("""
    #o{font:13px/1.4 var(--font-sans);color:var(--foreground)}
    #o a{color:inherit;text-decoration:none}
    h2{font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;margin:18px 0 6px}
    h2,small,.stat span,.legend,.axis,.sum,.foot,.co,.chip{color:var(--muted-foreground)}
    .diff{font-weight:600}
    .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
    .stat,.now div,.strip>*{border:1px solid var(--border);border-radius:var(--radius);padding:6px 10px}
    .stat b{display:block;font-size:22px;line-height:1.1}
    .now div,.strip>*,.grp{display:flex;gap:8px;align-items:baseline;min-width:0}
    .now div{margin-bottom:4px}
    .mark{width:8px;height:8px;border-radius:50%;border:1px solid;box-sizing:border-box;align-self:center;flex:none}
    .pulse{border:0;background:var(--success)}
    .sum,.l{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .sum{flex:1;min-width:0;font-weight:400}
    .strip{display:flex;flex-wrap:wrap;gap:6px}
    .strip>*{max-width:100%;box-sizing:border-box}
    .strip .sum{flex:0 1 auto;max-width:200px}
    .legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:11.5px;margin-bottom:6px}
    .dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;background:var(--c)}
    .tl{position:relative;--lab:230px}
    .axis{position:relative;height:16px;margin-left:var(--lab);font-size:10.5px}
    .axis i{position:absolute;width:5.5em;text-align:center;white-space:nowrap;font-style:normal}
    .grid{position:absolute;left:var(--lab);right:0;top:16px;bottom:0;pointer-events:none}
    .grid i{position:absolute;top:0;bottom:0;width:1px;background:var(--border)}
    .grp{position:relative;margin-top:8px;padding:3px 0;border-top:1px solid var(--border);background:var(--background);font-weight:600}
    .chip{font-size:10.5px;padding:1px 7px;border:1px solid;border-radius:99px;white-space:nowrap}
    .go,.live b{color:var(--success)}
    .info{color:var(--info)}
    .warn{color:var(--warning)}
    .bad,.alarm b,.diff{color:var(--destructive)}
    .row{display:grid;grid-template-columns:var(--lab) 1fr;align-items:center;height:19px}
    .l{padding-right:8px;font-size:12px}
    .d1{padding-left:14px}
    .d2{padding-left:28px}
    small{font-size:10.5px}
    .t{position:relative;height:100%}
    .b{position:absolute;top:4px;height:11px;min-width:3px;border-radius:3px;background:var(--c)}
    .run{background:repeating-linear-gradient(135deg,var(--c) 0 4px,transparent 4px 7px)}
    .q{background:transparent;border:1px solid var(--c);box-sizing:border-box}
    .stop{opacity:.45}
    .p0,.co{--c:var(--muted-foreground)}
    .p1{--c:var(--chart-1)}
    .p2{--c:var(--chart-2)}
    .p3{--c:var(--chart-3)}
    .p4{--c:var(--chart-4)}
    .p5{--c:var(--chart-5)}
    .p6{--c:var(--chart-6)}
    .f{--c:var(--destructive)}
    .foot{font-size:11px;margin-top:12px}
    @media(max-width:520px){
    .tl{--lab:128px}
    .stats{grid-template-columns:repeat(2,1fr)}
    .axis i:nth-child(odd){display:none}
    .row{position:relative;height:30px;align-items:start}
    .l small{position:absolute;left:0;right:0;bottom:0;overflow:hidden;text-overflow:ellipsis;line-height:13px}
    .d1 small{left:14px}
    .d2 small{left:28px}
    }
""")

RENDERER = squeezed("""
    const root = document.getElementById('o'), raw = document.getElementById('d').textContent;
    const add = (parent, tag, cls = '', text = '') => {
      const node = parent.appendChild(document.createElement(tag));
      node.className = cls;
      node.textContent = text;
      return node;
    };
    let sum = 2166136261, D = null;
    for (let i = 0; i < raw.length; i++) sum = Math.imul(sum ^ raw.charCodeAt(i), 16777619) >>> 0;
    if (sum !== H) add(root, 'p', 'diff', 'This copy differs from what the tool wrote. Run the command again.');
    const plain = (key, value) => typeof value != 'string' ? value : [['&lt;', 60], ['&gt;', 62], ['&quot;', 34], ['&#92;', 92], ['&amp;', 38]]
      .reduce((text, [entity, code]) => text.split(entity).join(String.fromCharCode(code)), value);
    try { D = JSON.parse(raw, plain); } catch (error) {}
    if (D && D.v === 1) {
      const [start, length, span] = D.w, kinds = ['', 'run', 'f', 'stop', 'p0', 'q'];
      const clock = (t, day) => new Date(t * 1000).toLocaleString([], day ? {weekday: 'short', hour: 'numeric'} : {hour: 'numeric', minute: '2-digit'});
      const link = (parent, text, url) => {
        const safe = url.startsWith('https://'), node = add(parent, safe ? 'a' : 'span', '', text);
        if (safe) {
          node.href = url;
          node.target = '_blank';
          node.rel = 'noopener';
        }
        return node;
      };
      const stats = add(root, 'div', 'stats');
      ['running now', 'agents, last ' + span, 'sub-agents', 'failed'].forEach((label, i) => {
        const box = add(stats, 'div', 'stat' + (i ? i > 2 && D.n[i] ? ' alarm' : '' : ' live'));
        add(box, 'b', '', D.n[i]);
        add(box, 'span', '', label);
      });
      const open = [[], []];
      for (const [index, rows] of D.G) {
        const above = [];
        for (const r of rows) {
          above[r[0]] = r[1];
          if (r[8] != null) open[r[4]].push([(index < 0 ? '' : D.I[index][0] + ' ') + r[1], (D.M[r[2]] || '') + (r[0] ? ' · under ' + above[r[0] - 1] : ''), r[8]]);
        }
      }
      ['Running now', 'Queued'].forEach((heading, i) => {
        if (open[i].length) {
          add(root, 'h2', '', heading);
          const list = add(root, 'div', 'now');
          for (const [name, detail, elapsed] of open[i]) {
            const line = add(list, 'div');
            add(line, 'i', i ? 'mark' : 'mark pulse');
            add(line, 'b', '', name);
            add(line, 'span', 'sum', detail);
            add(line, 'span', '', elapsed);
          }
        }
      });
      add(root, 'h2', '', 'Timeline');
      const legend = add(root, 'div', 'legend');
      for (const [cls, text] of [...D.P.map(p => ['p' + p[1], p[0] + ' ' + p[2]]), ['f', 'failed']]) {
        const entry = add(legend, 'span');
        add(entry, 'i', 'dot ' + cls);
        add(entry, 'span', '', text);
      }
      const lanes = add(root, 'div', 'tl'), axis = add(lanes, 'div', 'axis'), grid = add(lanes, 'div', 'grid');
      const step = 60 * [15, 30, 60, 180, 720, 1440][[2, 6, 12, 24, 72].filter(hours => length > hours * 3600).length];
      const day = new Date(start * 1000);
      day.setHours(0, 0, 0, 0);
      for (let t = day / 1000; t < start + length; t += step) {
        const x = (t - start) / length * 100;
        if (x > 4 && x < 97) {
          add(axis, 'i', '', clock(t, length > 86400)).style.left = 'min(' + x + '% - 2.75em,100% - 5.5em)';
          add(grid, 'i').style.left = x + '%';
        }
      }
      const lane = (cls, depth, label, model, status, time, spans) => {
        const row = add(lanes, 'div', 'row ' + cls), name = add(row, 'div', 'l d' + depth, label + ' '), track = add(row, 'div', 't');
        add(name, 'small', '', D.M[model]);
        row.title = [label, D.M[model], D.S[status], time].filter(Boolean).join(' · ');
        for (let i = 0; i < spans.length; i += 3) {
          const bar = add(track, 'i', 'b ' + kinds[spans[i + 2]]);
          bar.style.left = spans[i] / 10 + '%';
          bar.style.width = spans[i + 1] / 10 + '%';
        }
      };
      if (D.k) lane('co', 0, 'coordinator', ...D.k);
      for (const [index, rows] of D.G) {
        const head = add(lanes, 'div', 'grp'), item = D.I[index];
        if (item) {
          link(head, item[0], item[4]);
          add(head, 'span', 'sum', item[1]);
          add(head, 'b', 'chip ' + item[3], item[2]);
        } else add(head, 'span', '', 'Not tied to a work item');
        for (const r of rows) lane('p' + (r[3] < 0 ? 0 : D.P[r[3]][1]), r[0], r[1], r[2], r[4], r[5], r[6]);
      }
      const strip = D.I.filter(item => item[5]);
      if (strip.length) {
        add(root, 'h2', '', 'Work items in flight');
        const box = add(root, 'div', 'strip');
        for (const item of strip) {
          const chip = link(box, '', item[4]);
          add(chip, 'b', '', item[0]);
          add(chip, 'span', 'sum', item[1]);
          add(chip, 'b', 'chip ' + item[3], item[2]);
        }
      }
      const foot = add(root, 'div', 'foot', 'As of ' + clock(start + length) + ' for ' + D.c + '. Bars show turn or delegation intervals and may join across gaps. Striped bars include running turns. Outlined bars include queued turns. Faded bars include stopped turns.');
      for (const note of D.N) add(foot, 'div', '', note);
    }
""")


def wire(page):
    models, providers = [], [name for name, _ in page.legend]
    colors = dict([*PROVIDERS.values(), OTHER_PROVIDER])

    def model(name):
        if name and name not in models:
            models.append(name)
        return models.index(name) if name else -1

    def bars(spans):
        return [number for span in spans for number in (span.x, span.w, WIRE_BARS.index(span.status))]

    def line(row):
        tail = [row.stands_for, dur(row.open_seconds)] if row.open_seconds is not None else [row.stands_for] if row.stands_for != 1 else []
        provider = providers.index(row.provider) if row.provider in providers else -1
        return [row.depth, row.label, model(row.model), provider, WIRE_STATUS.index(row.status), dur(row.seconds), bars(row.spans), *tail]

    items = items_of(page)
    own, totals = page.coordinator, page.totals
    return {
        "v": WIRE_VERSION,
        "c": page.name,
        "w": [int(page.window.start), round(page.window.end - page.window.start), span(page.window)],
        "n": [totals.running, totals.agents, totals.subagents, totals.failed],
        "S": [status.value for status in WIRE_STATUS],
        "P": [[name, colors[name], agents] for name, agents in page.legend],
        "k": own and [model(own.model), WIRE_STATUS.index(own.status), dur(own.seconds), bars(own.spans)],
        "I": [[item.id, item.summary, item.state, item.tone, item.pr, int(item in page.items)] for item in items],
        "G": [[items.index(group.item) if group.item else -1, [line(row) for row in group.rows]] for group in page.groups],
        "M": models,
        "N": notes(page),
    }


def checksum(text):
    value, units = 2166136261, text.encode("utf-16-le")
    for at in range(0, len(units), 2):
        value = ((value ^ int.from_bytes(units[at:at + 2], "little")) * 16777619) & 0xFFFFFFFF
    return value


def render_html(page):
    data = encode(wire(page))
    return (
        "<!doctype html><meta charset=utf-8><meta name=viewport content=\"width=device-width,initial-scale=1\">"
        f"<title>Agent activity</title><style>{STYLE}</style><div id=o></div>"
        f"<script type=application/json id=d>{data}</script><script>const H={checksum(data)};{RENDERER}</script>")


def span(window):
    seconds = round(window.end - window.start)
    parts = ((seconds // 3600, "h"), (seconds % 3600 // 60, "m"), (seconds % 60, "s"))
    return " ".join(f"{number}{unit}" for number, unit in parts if number)


def dur(seconds):
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m"


def render_text(page):
    def capped(lines, limit, noun):
        return lines[:limit] + ([f"  and {count(len(lines) - limit, 'more ' + noun)}"] if len(lines) > limit else [])

    def line(*parts):
        return "  " + "   ".join(part for part in parts if part)

    running, queued, failed, items, loose = [], [], [], [], []
    for group in page.groups:
        above = {}
        for row in group.rows:
            above[row.depth] = row.label
            name = f"{group.item.id} {row.label}" if group.item else row.label
            if row.open_seconds is not None:
                waits = row.status is Status.QUEUED
                (queued if waits else running).append(line(name, row.model, f"{'queued' if waits else 'running'} for {dur(row.open_seconds)}",
                                                           f"under {above[row.depth - 1]}" if row.depth else ""))
            if row.status is Status.FAILED:
                failed.append(line(name, row.model, f"{dur(row.seconds)} at work"))
        work = f"{people(group.agents, group.subagents)}, {dur(sum(row.seconds for row in group.rows))} at work"
        if group.item:
            items.append(line(group.item.id, group.item.state, group.item.summary, work, group.item.pr))
        else:
            loose.append(line(UNGROUPED, work))
    drawn = [group.item for group in page.groups]
    items += [line(item.id, item.state, item.summary, "no activity in this window", item.pr) for item in page.items if item not in drawn]
    totals = page.totals
    lines = [
        f"Agent activity for {page.name}, last {span(page.window)}",
        f"{totals.running} running now, {count(totals.agents, 'agent')}, {count(totals.subagents, 'sub-agent')}, {totals.failed} failed",
    ]
    for heading, body in (
            ("Running now", capped(running, TEXT_RUNNING, "running agent")),
            ("Queued", capped(queued, TEXT_QUEUED, "queued agent")),
            ("Work items", capped(items, TEXT_ITEMS, "work item") + loose),
            ("Failed", capped(failed, TEXT_FAILED, "failed agent")),
            ("Notes", ["  " + note for note in notes(page)])):
        if body:
            lines += [heading, *body]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
