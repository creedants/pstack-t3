#!/usr/bin/env python3
"""Show what one brigade coordinator's agents and sub-agents are doing, as one HTML page.

Reads the coordinator's store and T3 Code's state database and writes to neither.
Prints one self-contained HTML document, or plain lines with --text.
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
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import quote, unquote

# scripts/cli_reference.py executes this file outside sys.modules, so a dataclass field
# cannot name a class declared after it and the file has no postponed annotations.

DEFAULT_HOURS = 3.0
MAX_HOURS = 168.0
# The whole document, in UTF-8 bytes. The coordinator types it once for html_preview and once for html_render.
BUDGET = 16000
# The stylesheet, the renderer, and the shell around the data, with the data of an empty window.
FIXED_BUDGET = 7000
# html_preview and html_render refuse more than 512000 characters.
MAX_BUDGET = 500000

PROJECTION = "thread-projections"
SUPPORTED_PROJECTION = 2
THREADS = "orchestration_v2_projection_threads"
RUNS = "orchestration_v2_projection_runs"
SUBAGENTS = "orchestration_v2_projection_subagents"
METADATA = "orchestration_v2_projection_metadata"
# Every table and column this file reads. T3 publishes no contract for them, so check_shape compares this to the database first.
T3_SHAPE = {
    METADATA: ("projection_name", "schema_version"),
    THREADS: ("thread_id", "title", "default_provider", "payload_json"),
    RUNS: ("thread_id", "status", "requested_at", "completed_at"),
    SUBAGENTS: ("subagent_id", "thread_id", "child_thread_id", "status", "started_at", "completed_at"),
}
# The one key read from a thread's payload_json. Its value may be null.
THREAD_PAYLOAD_KEY = "modelSelection"
T3_DATABASE = "statev2.sqlite"
BATCH = 500
CHANGED = "this T3 build stores threads differently, so update pstack-t3"

STORE_RECORD = "restaurant.json"
STORE_LOCK = "restaurant.lock"
UNITS_TABLE = "dishes.tsv"
LOG_TABLE = "log.tsv"
# A copy of brigade.py TABLES for the two tables read here. tests/test_activity.py asserts the copy is equal.
STORE_COLUMNS = {
    UNITS_TABLE: ("id", "at", "state", "station", "tickets", "task", "thread", "branch", "pr", "sha", "summary", "timebox", "lease", "paths", "reported"),
    LOG_TABLE: ("at", "kind", "id", "state", "note"),
}
TABLE_WORDS = {UNITS_TABLE: "work item table", LOG_TABLE: "log"}

# Store state to (word on the page, tone).
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
# T3's driver name in default_provider to (name on the page, chart color). A custom instance name can hold an account name, so no other name is shown.
PROVIDERS = {"claudeAgent": ("Claude", 1), "codex": ("Codex", 2), "grok": ("Grok", 3), "opencode": ("OpenCode", 4), "cursor": ("Cursor", 5)}
OTHER_PROVIDER = ("Other", 6)
UNGROUPED = "Not tied to a work item"
TITLE_CHARS = 48
LABEL_CHARS = 40


class Status(enum.Enum):
    """What one agent is doing, or how it ended. The value is the word on the page."""

    RUNNING = "running"
    QUEUED = "queued"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


# T3's status text for a turn or a delegation.
T3_STATUS = {
    "running": Status.RUNNING, "starting": Status.RUNNING,
    "queued": Status.QUEUED, "preparing": Status.QUEUED,
    "waiting": Status.WAITING, "idle": Status.WAITING,
    "completed": Status.DONE,
    "failed": Status.FAILED,
    "cancelled": Status.STOPPED, "interrupted": Status.STOPPED, "rolled_back": Status.STOPPED,
}
OPEN = (Status.RUNNING, Status.QUEUED)
QUIET = (Status.DONE, Status.STOPPED)


class Evidence(enum.Enum):
    """Why an agent sits under its work item. assign() tries them in this order."""

    RECORD = "record"
    LINEAGE = "lineage"
    REQUEST = "request"
    NONE = "none"


class ActivityError(Exception):
    """An expected failure. main() prints `activity: <message>` on stderr and returns `status`."""

    status = 1


class SourceError(ActivityError):
    """T3's database is missing, unreadable, another T3's, or not the shape T3_SHAPE names."""

    status = 3


@dataclass(frozen=True)
class Unit:
    """One row of the store's units table. `worker`, `earlier_workers`, and `task` are T3 ids."""

    id: str
    state: str
    summary: str
    pr: str
    worker: str                       # the current worker's thread id, or ""
    earlier_workers: tuple[str, ...]  # thread ids of retired workers, from the log
    task: str                         # the latest delegation: a sub-agent id, a request name, or ""


@dataclass(frozen=True)
class Store:
    """One coordinator's store at one moment. A unit id appears once in `units`, which is in table order.

    `coordinators` holds the recorded thread and the previous thread, blanks dropped.
    `slug_parts` holds the lower-case words of the store's name and of its project directory's name.
    """

    name: str
    slug_parts: frozenset
    coordinators: tuple[str, ...]
    units: tuple[Unit, ...]


@dataclass(frozen=True)
class Window:
    """The time range the page covers, in epoch seconds, with start before end."""

    start: float
    end: float


@dataclass(frozen=True)
class Turn:
    """One turn of a thread. `end` is None while the turn is open."""

    status: Status
    start: float
    end: Optional[float]


@dataclass(frozen=True)
class Delegation:
    """One row of T3's sub-agents table, seen from the child. `end` is None while it is open."""

    status: Status
    start: float
    end: Optional[float]


@dataclass(frozen=True)
class Agent:
    """One thread in scope. `turns` is sorted by start and holds only turns that touch the window.

    `delegation` is set when it touches the window and the thread has a turn in the window or has never had a turn.
    A provider's own sub-agent has a delegation and no turns.
    An ancestor kept so its child has a parent row has neither.
    """

    thread: str
    parent: Optional[str]
    request: Optional[str]           # the request name a delegated child was started with
    provider: str                    # T3's driver name
    model: str
    title: str                       # unscrubbed
    turns: tuple[Turn, ...]
    delegation: Optional[Delegation]


@dataclass(frozen=True)
class T3:
    """What was read from T3, limited to one coordinator's scope.

    `agents` holds every thread in scope that is active in the window, and each one's ancestors up to its root.
    `node_thread` maps a sub-agent id to its child thread, for the children in `agents`.
    `other_threads` counts threads active in the window outside the scope.
    `unknown_status` counts the statuses in `agents` that parse_status could not read.
    """

    agents: Mapping[str, Agent]
    node_thread: Mapping[str, str]
    other_threads: int
    unknown_status: int


@dataclass(frozen=True)
class Assignment:
    """The work item an agent belongs to and why. `unit` is None exactly when `evidence` is NONE."""

    unit: Optional[str]
    evidence: Evidence


# The types below hold what a person reads. No field holds a thread id, a sub-agent id, a request name, or a path.


@dataclass(frozen=True)
class Span:
    """One bar, in thousandths of the window. 0 <= x, 1 <= w, and x + w <= 1000.

    `status` is DONE, RUNNING, FAILED, STOPPED, or UNKNOWN.
    """

    x: int
    w: int
    status: Status


@dataclass(frozen=True)
class Row:
    """One line of the timeline.

    `depth` is 0 for a row with no parent row in its group, and 1 or 2 below one.
    `seconds` is the time spent in turns inside the window.
    `open_seconds` is how long the open turn has run, counted from its start. It is None when `status` is not in OPEN, and on the coordinator's row.
    `stands_for` is 1 for one agent. A summary row stands for 2 or more.
    """

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
    """One work item. `state` and `tone` come from STATE_WORDS. `pr` is an https URL or ""."""

    id: str
    summary: str
    state: str
    tone: str
    pr: str
    in_flight: bool


@dataclass(frozen=True)
class Group:
    """A work item and its rows in tree order. `item` is None for the agents tied to no work item.

    `agents` and `subagents` count the group's threads without and with a parent. No fold step changes them.
    """

    item: Optional[Item]
    rows: tuple[Row, ...]
    agents: int
    subagents: int


@dataclass(frozen=True)
class Totals:
    """The four numbers at the top. No fold step changes them, and the coordinator's own thread is in none.

    `agents` counts threads with no parent, `subagents` counts threads with one, `running` counts
    agents whose status is in OPEN, and `failed` counts agents whose status is FAILED.
    """

    running: int
    agents: int
    subagents: int
    failed: int


@dataclass(frozen=True)
class Hidden:
    """Counts of what the page does not draw. notes() writes one sentence for each field that is not zero.

    The sum of `stands_for` over every row, plus `dropped_agents` and `cut_agents`, equals
    `totals.agents + totals.subagents` on every page.
    """

    dropped_items: int = 0       # finished work items drop_old_items removed
    dropped_agents: int = 0      # the agents of those items
    cut_agents: int = 0          # agents whose row cap_everything removed
    cut_in_flight: int = 0       # in-flight items cap_everything removed from the strip
    other_threads: int = 0
    unknown_status: int = 0
    by_request_name: int = 0     # agents grouped with Evidence.REQUEST


@dataclass(frozen=True)
class Page:
    """Everything either renderer needs.

    `coordinator` is the coordinator's own row. It has no RUNNING span and is in no count.
    `legend` holds each provider's name and its number of agents, most agents first. No fold step changes it.
    `items` holds the in-flight work items. A group's item can be a finished one.
    `fold` is how many fold steps were applied.
    """

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
    top.add_argument("--hours", type=float, default=DEFAULT_HOURS, help="how many hours back the page looks, more than 0 and at most 168")
    top.add_argument("--text", action="store_true", help="print plain lines instead of the HTML document")
    top.add_argument("--out", help="write the output to this file and print `wrote <file> (<n> bytes)` instead")
    top.add_argument("--max-bytes", type=int, default=BUDGET, help="the size in bytes the HTML document must fit, from 16000 to 500000")
    top.add_argument("--t3-home", help="T3 Code's base directory (default $T3CODE_HOME, else ~/.t3)")
    return top


def main(argv=None):
    try:
        print(run(sys.argv[1:] if argv is None else argv))
    except ActivityError as error:
        print(f"activity: {error}", file=sys.stderr)
        return error.status
    return 0


def run(argv):
    """What main() prints on stdout. Raises ActivityError with one line for every expected failure."""
    args = parser().parse_args(argv)
    if not 0 < args.hours <= MAX_HOURS:
        raise ActivityError("--hours must be more than 0 and at most 168; pass a number in that range")
    if not BUDGET <= args.max_bytes <= MAX_BUDGET:
        raise ActivityError("--max-bytes must be from 16000 to 500000; pass a number in that range")
    store = read_store(store_dir(args.at, os.environ))
    path = t3_database(args.t3_home, os.environ, Path.home())
    now = time.time()
    window = Window(now - args.hours * 3600, now)
    connection = open_t3(path)
    try:
        check_shape(connection)
        check_coordinator(connection, store.coordinators)
        t3 = read_t3(connection, window, roots_of(store))
    except sqlite3.Error as error:
        raise unreadable(error, path) from None
    finally:
        connection.close()
    page = build_page(store, t3, window)
    output = render_text(page) if args.text else fit(page, args.max_bytes)[1]
    if not args.out:
        return output
    data = output.encode()
    try:
        Path(args.out).write_bytes(data)
    except OSError as error:
        raise ActivityError(f"cannot write the --out file ({error.strerror}); pass a path this user can write") from None
    return f"wrote {args.out} ({len(data)} bytes)"


def store_dir(flag, environ):
    chosen = flag or environ.get("BRIGADE_DIR")
    if not chosen:
        raise ActivityError("pass --at <store directory> or set BRIGADE_DIR")
    return Path(chosen)


def read_table(directory, table):
    """The rows of one store table as dicts keyed by STORE_COLUMNS[table], or [] when the file is absent.

    The header line is skipped and the text after the last newline is dropped, as brigade.py does.
    The read holds a shared lock on the store's lock file when that file exists. It never creates the file.
    """
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
    """One coordinator's store as a Store. A unit's earlier workers are the notes of its retired-worker log rows."""
    try:
        try:
            meta = json.loads((directory / STORE_RECORD).read_text())
        except (FileNotFoundError, NotADirectoryError):
            raise ActivityError("that directory holds no coordinator's store; pass --at <store directory> or set BRIGADE_DIR") from None
        except ValueError:
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
    project = Path(meta["projectRoot"]).name if isinstance(meta.get("projectRoot"), str) else ""
    coordinators = tuple(value for value in (meta.get("thread"), meta.get("previousThread")) if isinstance(value, str) and value.strip())
    return Store(name, frozenset(re.findall(r"[a-z0-9]+", f"{name} {project}".lower())), coordinators, tuple(by_id.values()))


def roots_of(store):
    """The threads a coordinator's scope starts from: its own, each unit's worker, and each earlier worker."""
    workers = {thread for unit in store.units for thread in (unit.worker, *unit.earlier_workers) if thread}
    return frozenset(store.coordinators) | workers


def t3_database(flag, environ, home):
    """The path of T3's state database.

    The base directory is --t3-home, else $T3CODE_HOME when it is not blank, else <home>/.t3, with a leading ~ read as <home>.
    The database is <base>/userdata/statev2.sqlite. With no flag and no variable, <base>/dev/statev2.sqlite is used when the userdata one is absent.
    """
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
    """A read-only connection in one read transaction. The caller closes it.

    With a write-ahead log and its index beside the database, T3 is live and mode=ro reads the log and creates no file.
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
    """The failure for an SQLite error, with the database's path and every word that holds a slash left out."""
    text = str(error)
    for known in (str(path), quote(str(path)), str(Path(path).parent)):
        text = text.replace(known, "")
    words = " ".join(word for word in text.split() if "/" not in word and "\\" not in word)
    return SourceError(f"cannot read T3's database ({words}); check that T3 Code is running and try again")


def check_shape(connection):
    """Raise SourceError unless the database has every table and column in T3_SHAPE and thread records of the supported version."""
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
    """Raise SourceError when the store records a coordinator thread and T3 has a row for none of the recorded ones."""
    if not coordinators:
        return
    marks = ", ".join("?" * len(coordinators))
    if connection.execute(f"select 1 from {THREADS} where thread_id in ({marks}) limit 1", coordinators).fetchone() is None:
        raise SourceError("T3's database has no record of this coordinator's thread; pass --t3-home <the base directory of the T3 that runs it>")


def parse_time(text, where):
    """An ISO 8601 timestamp as epoch seconds. A value with no zone is read as UTC."""
    try:
        # Python 3.10 rejects the Z that T3 writes.
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise SourceError(f"T3's {where} is not a timestamp; {CHANGED}") from None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).timestamp()


def parse_status(text, ended):
    """T3's status text as a Status. UNKNOWN for a text T3_STATUS lacks, and for a running or queued status on a record that has ended."""
    status = T3_STATUS.get(text, Status.UNKNOWN)
    return Status.UNKNOWN if ended and status in OPEN else status


def request_name(thread_id):
    """The request name a delegated child was started with, or None for any other thread.

    A delegated child's thread id holds, after URL decoding, `delegate-task:<request name>` at its end.
    """
    _, marker, name = unquote(thread_id).rpartition("delegate-task:")
    return name if marker and name else None


def in_scope(parent_of, roots, active):
    """The active threads that are a root or have a root among their ancestors, with those ancestors, and the number of other active threads."""
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
    """One coordinator's activity in the window, as a T3.

    A thread is active when it has a turn that touches the window, or when it has never had a turn and its delegation touches the window.
    When T3 holds two delegations for one child, the one that started last is used.
    """
    parent_of, delegations, nodes = {}, {}, {}
    for node, parent, child, status, started, completed in connection.execute(
            f"select subagent_id, thread_id, child_thread_id, status, started_at, completed_at from {SUBAGENTS}"):
        if not child:
            continue
        start = parse_time(started, f"{SUBAGENTS}.started_at")
        end = None if completed is None else parse_time(completed, f"{SUBAGENTS}.completed_at")
        nodes[node] = child
        if child not in delegations or start >= delegations[child].start:
            parent_of[child] = parent
            delegations[child] = Delegation(parse_status(status, end is not None), start, end)
    since = datetime.fromtimestamp(window.start, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    turns = {}
    for thread, status, requested, completed in connection.execute(
            f"select thread_id, status, requested_at, completed_at from {RUNS} where completed_at is null or completed_at >= ?", (since,)):
        start = parse_time(requested, f"{RUNS}.requested_at")
        end = None if completed is None else parse_time(completed, f"{RUNS}.completed_at")
        if touches(start, end, window):
            turns.setdefault(thread, []).append(Turn(parse_status(status, end is not None), start, end))
    idle = [child for child, delegation in delegations.items() if child not in turns and touches(delegation.start, delegation.end, window)]
    ran = set()
    for batch in batches(idle):
        marks = ", ".join("?" * len(batch))
        ran.update(thread for (thread,) in connection.execute(f"select distinct thread_id from {RUNS} where thread_id in ({marks})", batch))
    active = set(turns) | (set(idle) - ran)
    kept, others = in_scope(parent_of, roots, active)
    described = {}
    for batch in batches(kept):
        marks = ", ".join("?" * len(batch))
        for thread, title, provider, payload in connection.execute(
                f"select thread_id, title, default_provider, payload_json from {THREADS} where thread_id in ({marks})", batch):
            described[thread] = (title or "", provider or "", model_of(payload))
    agents = {}
    for thread in sorted(kept):
        title, provider, model = described.get(thread, ("", "", ""))
        own = tuple(sorted(turns.get(thread, ()), key=lambda turn: turn.start))
        delegation = delegations.get(thread)
        if delegation and not (thread in active and touches(delegation.start, delegation.end, window)):
            delegation = None
        agents[thread] = Agent(thread, parent_of.get(thread), request_name(thread), provider, model, title, own, delegation)
    statuses = [record.status for agent in agents.values() for record in (*agent.turns, agent.delegation) if record]
    return T3(agents, {node: child for node, child in nodes.items() if child in kept}, others, statuses.count(Status.UNKNOWN))


def model_of(payload):
    """The model name in a thread's payload_json, or "" when the payload names none."""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        data = None
    if not isinstance(data, dict) or THREAD_PAYLOAD_KEY not in data:
        raise SourceError(f"T3's thread payload has no {THREAD_PAYLOAD_KEY}; {CHANGED}")
    selection = data[THREAD_PAYLOAD_KEY]
    model = selection.get("model") if isinstance(selection, dict) else None
    return model if isinstance(model, str) else ""


UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
# A UUID, 7 or more hex digits that include a digit, 6 or more digits, or one of T3's id prefixes.
ID_LIKE = re.compile(rf"{UUID}|(?<![0-9A-Za-z])(?=[0-9a-fA-F]*[0-9])[0-9a-fA-F]{{7,}}(?![0-9A-Za-z])|[0-9]{{6,}}|^(?:mcp|thread|node|run):\S")
OPENERS = "\"'`([<{"
PATH_TAIL = re.compile(r"\S*/([A-Za-z0-9_-]+)")
SLUG = re.compile(r"[a-z0-9.]+(?:-[a-z0-9.]+)+")
ROLE = re.compile(r"Act as the (.+?) sub-agent")
UNIT_SUFFIX = re.compile(r"(.+?)[a-z][0-9]*")
# What a bar shows for each status of a turn.
BAR = {
    Status.RUNNING: Status.RUNNING, Status.QUEUED: Status.RUNNING,
    Status.WAITING: Status.DONE, Status.DONE: Status.DONE,
    Status.FAILED: Status.FAILED, Status.STOPPED: Status.STOPPED, Status.UNKNOWN: Status.UNKNOWN,
}


def unit_named(part, units):
    """The unit a request-name part names, or None. `units` maps each unit id in lower case to the id.

    A part names a unit when it is that id in lower case, alone or followed by one letter and then digits, as in d7, d7c, and d7r2.
    """
    suffixed = UNIT_SUFFIX.fullmatch(part)
    return units.get(part) or (units.get(suffixed.group(1)) if suffixed else None)


def assign(store, t3):
    """An Assignment for every agent but the coordinators. The first rule that applies to a thread decides. No rule reads a title.

    1. RECORD. The thread is a unit's worker or earlier worker. Or a unit's task names it, as a sub-agent id in
       t3.node_thread or as the request name of a coordinator's child. When two units name it, the later one in the table wins.
    2. LINEAGE. The thread's parent is an agent that is not a coordinator, and that parent has a unit. The thread takes it.
    3. REQUEST. The thread's parent is a coordinator, and unit_named() accepts a `-` separated part of its request name.
       The first such part names the unit.
    4. NONE.
    """
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


def path_like(part):
    if "://" in part:
        return not part.startswith(("http://", "https://"))
    return part.startswith(("/", "~")) or part.count("/") >= 2 or part.count("\\") >= 2


def scrub(text):
    """The first line of text without its path-like and id-like parts. Every string on a Page but a link and a model name went through it.

    A part is the text between spaces, read after any opening quote or bracket.
    It is path-like when it starts with / or ~, holds two or more / or two or more backslashes, or holds :// and does not start with http:// or https://.
    It is id-like when ID_LIKE matches in it.
    """
    lines = text.encode("utf-8", "ignore").decode().strip().splitlines()
    parts = [(part, part.lstrip(OPENERS)) for part in (lines[0].split() if lines else ())]
    return " ".join(part for part, bare in parts if not path_like(bare) and not ID_LIKE.search(bare))


def model_name(text):
    """A model's name as shown: the text after its last /, with each run of spaces as one space."""
    return " ".join(text.encode("utf-8", "ignore").decode().rsplit("/", 1)[-1].split())


def link_of(text):
    return text if text.startswith("https://") and not re.search(r"\s", text) else ""


def words_of(name, store, units):
    """The words of a request name.

    Split on `-`. Drop a leading `brigade`. Then drop each leading part that is in store.slug_parts.
    Then drop every part that names a unit and every id-like part. `verify` reads `review`.
    """
    parts = name.split("-")
    if parts[0] == "brigade":
        del parts[0]
    while parts and parts[0] in store.slug_parts:
        del parts[0]
    kept = ["review" if part == "verify" else part for part in parts if not unit_named(part, units) and not ID_LIKE.search(part)]
    return scrub(" ".join(kept))


def label_of(agent, store, unit):
    """A name for one agent, at most LABEL_CHARS characters. `unit` is the id of the agent's work item, or None. The first rule that gives text decides.

    1. A unit's current worker is `worker`. An earlier one is `earlier worker`.
    2. The title's first line, when it is at most TITLE_CHARS characters and does not start with `Act as` or `You are`.
       A line that is one path-like part ending in a segment of letters, digits, `_`, and `-` gives that segment.
       The line is scrubbed, and a leading `unit` with its `:` or space is dropped.
       What is left is read by words_of() when it is one lower-case word with a `-` in it, which is a request name used as a title.
    3. words_of() the request name.
    4. The role in a title that starts `Act as the <role> sub-agent`.
    5. `sub-agent` for a thread with a parent and `agent` for one without.
    """
    if any(agent.thread == other.worker for other in store.units):
        return "worker"
    if any(agent.thread in other.earlier_workers for other in store.units):
        return "earlier worker"
    units = {other.id.lower(): other.id for other in store.units}
    line = (agent.title.strip().splitlines() or [""])[0].strip()
    written = ""
    if len(line) <= TITLE_CHARS and not line.startswith(("Act as", "You are")):
        tail = PATH_TAIL.fullmatch(line) if path_like(line) else None
        written = scrub(tail.group(1) if tail else line)
        if unit:
            written = re.sub(rf"^{re.escape(unit)}(?::\s*|\s+)", "", written)
        if SLUG.fullmatch(written):
            written = words_of(written, store, units)
    role = ROLE.match(line)
    for text in (written, words_of(agent.request or "", store, units), scrub(role.group(1)) if role else ""):
        if text:
            return text if len(text) <= LABEL_CHARS else text[:LABEL_CHARS - 1].rstrip() + "…"
    return "sub-agent" if agent.parent else "agent"


def stretches(agent):
    """The agent's time at work: its turns, or its delegation as one turn when it has no turns. An open delegation reads RUNNING."""
    delegation = agent.delegation
    if agent.turns or delegation is None:
        return agent.turns
    return (Turn(Status.RUNNING if delegation.end is None else delegation.status, delegation.start, delegation.end),)


def open_turn(agent):
    return next((turn for turn in reversed(stretches(agent)) if turn.end is None and turn.status in OPEN), None)


def status_of(agent):
    """One Status per agent. The first rule that applies decides.

    1. A turn with no end that is running or queued gives its status. So does the open delegation of an agent with no turns, which reads RUNNING.
    2. An open delegation gives WAITING.
    3. A closed delegation gives its status.
    4. The last turn gives its status.
    5. An agent with no turn and no delegation in the window reads DONE.
    """
    turn, delegation = open_turn(agent), agent.delegation
    if turn:
        return turn.status
    if delegation:
        return Status.WAITING if delegation.end is None else delegation.status
    return agent.turns[-1].status if agent.turns else Status.DONE


def spans_of(agent, window):
    """The agent's bars: one per stretch, clipped to the window.

    A stretch that has ended joins the bar before it when both show the same status and are less than 5 thousandths apart.
    """
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
    return int(sum(max(0, min(window.end, window.end if turn.end is None else turn.end) - max(window.start, turn.start)) for turn in stretches(agent)))


def tree(agents):
    """The agents as (agent, depth) in tree order: each agent with no parent among them, then its children, earliest start first. Depth stops at 2."""
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


def item_of(unit):
    word, tone = STATE_WORDS.get(unit.state, UNKNOWN_STATE)
    return Item(scrub(unit.id), scrub(unit.summary), word, tone, link_of(unit.pr), unit.state not in FINISHED_STATES)


def build_page(store, t3, window):
    """The unfolded Page.

    Groups whose agents have open work come first, then the latest activity first. The group tied to no work item is last.
    `items` is in order of the number in each id.
    The coordinators' turns make one row, and that row's RUNNING bars read DONE.
    """
    def row(agent, depth, label):
        turn = open_turn(agent)
        return Row(depth, label, model_name(agent.model), PROVIDERS.get(agent.provider, OTHER_PROVIDER)[0], status_of(agent),
                   seconds_of(agent, window), spans_of(agent, window), int(window.end - turn.start) if turn else None)

    def order(entry):
        unit, agents = entry
        ends = [window.end if turn.end is None else turn.end for agent in agents for turn in stretches(agent)]
        return (unit is None, all(open_turn(agent) is None for agent in agents), -max(ends, default=window.start), unit)

    placed = assign(store, t3)
    units = {unit.id: unit for unit in store.units}
    members = {}
    for thread, assignment in placed.items():
        members.setdefault(assignment.unit, []).append(t3.agents[thread])
    groups = []
    for unit, agents in sorted(members.items(), key=order):
        rows = tuple(row(agent, depth, label_of(agent, store, unit)) for agent, depth in tree(agents))
        subagents = sum(agent.parent is not None for agent in agents)
        groups.append(Group(item_of(units[unit]) if unit else None, rows, len(agents) - subagents, subagents))
    everyone = [row for group in groups for row in group.rows]
    subagents = sum(group.subagents for group in groups)
    totals = Totals(sum(row.status in OPEN for row in everyone), len(everyone) - subagents, subagents, sum(row.status is Status.FAILED for row in everyone))
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
        digits = re.search(r"[0-9]+", unit.id)
        return (int(digits.group()) if digits else 0, unit.id)

    items = tuple(item_of(unit) for unit in sorted(units.values(), key=number) if unit.state not in FINISHED_STATES)
    hidden = Hidden(other_threads=t3.other_threads, unknown_status=t3.unknown_status,
                    by_request_name=sum(assignment.evidence is Evidence.REQUEST for assignment in placed.values()))
    return Page(scrub(store.name) or "this coordinator", window, totals, legend, own, tuple(groups), items, hidden)


KEPT_ITEMS = 6
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
# Which status a bar keeps when cap_everything joins two bars, strongest first.
JOINED = (Status.RUNNING, Status.FAILED, Status.UNKNOWN, Status.STOPPED, Status.DONE)


def encode(data):
    """Compact JSON that cannot end a script element: `<`, `>`, `&`, U+2028, and U+2029 are written as \\u escapes."""
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    for character in "<>&  ":
        text = text.replace(character, f"\\u{ord(character):04x}")
    return text


def clip(text, limit):
    """text, cut to end in an ellipsis when its encode() form without the quotes is over limit UTF-8 bytes."""
    def size(value):
        return len(encode(value).encode()) - 2

    if size(text) <= limit:
        return text
    kept = min(len(text), limit)
    while kept and size(text[:kept] + "…") > limit:
        kept -= 1
    return text[:kept] + "…"


def count(number, noun):
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def people(agents, subagents):
    """Such as `1 agent, 9 sub-agents`. A zero count is left out."""
    return ", ".join(count(number, noun) for number, noun in ((agents, "agent"), (subagents, "sub-agent")) if number)


def quiet(rows):
    return all(row.status in QUIET for row in rows)


def summary(rows, depth, label):
    """One row that stands for rows. Its bars are the union of theirs and read DONE. It keeps a model or provider only when every row has the same one."""
    bars = []
    for span in sorted((span for row in rows for span in row.spans), key=lambda span: span.x):
        if bars and span.x <= bars[-1].x + bars[-1].w:
            bars[-1] = Span(bars[-1].x, max(bars[-1].x + bars[-1].w, span.x + span.w) - bars[-1].x, Status.DONE)
        else:
            bars.append(Span(span.x, span.w, Status.DONE))
    models, providers = {row.model for row in rows}, {row.provider for row in rows}
    return Row(depth, label, models.pop() if len(models) == 1 else "", providers.pop() if len(providers) == 1 else "", Status.DONE,
               sum(row.seconds for row in rows), tuple(bars), None, sum(row.stands_for for row in rows))


def families(rows):
    """rows split into runs that each start at a row of depth 0."""
    runs = []
    for row in rows:
        if row.depth == 0 or not runs:
            runs.append([])
        runs[-1].append(row)
    return runs


def fold_finished_subagents(page):
    """Step 1. Under a row of depth 0 with 2 or more rows below it, all DONE or STOPPED like the row itself, one summary row replaces the rows below."""
    groups = []
    for group in page.groups:
        rows = []
        for top, *below in families(group.rows):
            if len(below) >= 2 and quiet([top, *below]):
                below = [summary(below, 1, count(sum(row.stands_for for row in below), "sub-agent"))]
            rows += [top, *below]
        groups.append(replace(group, rows=tuple(rows)))
    return replace(page, groups=tuple(groups), fold=page.fold + 1)


def finished(group):
    return group.item is not None and not group.item.in_flight and quiet(group.rows)


def fold_quiet_items(page):
    """Step 2. A group of 2 or more rows, all DONE or STOPPED, whose work item is merged or dropped becomes one summary row."""
    groups = tuple(
        replace(group, rows=(summary(group.rows, 0, people(group.agents, group.subagents)),)) if finished(group) and len(group.rows) >= 2 else group
        for group in page.groups)
    return replace(page, groups=groups, fold=page.fold + 1)


def drop_old_items(page):
    """Step 3. Of the groups whose work item is merged or dropped and whose rows are all DONE or STOPPED, the first KEPT_ITEMS in page order stay."""
    groups, seen, items, agents = [], 0, 0, 0
    for group in page.groups:
        seen += finished(group)
        if finished(group) and seen > KEPT_ITEMS:
            items, agents = items + 1, agents + sum(row.stands_for for row in group.rows)
        else:
            groups.append(group)
    hidden = replace(page.hidden, dropped_items=page.hidden.dropped_items + items, dropped_agents=page.hidden.dropped_agents + agents)
    return replace(page, groups=tuple(groups), hidden=hidden, fold=page.fold + 1)


def first(values, limit, urgent):
    """The limit values to keep, in their own order: the urgent ones are chosen first, then the earliest."""
    chosen = set(sorted(range(len(values)), key=lambda index: (not urgent(values[index]), index))[:limit])
    return [index in chosen for index in range(len(values))]


def joined(spans, limit):
    """spans with the two neighbors nearest each other joined until at most limit are left."""
    spans = list(spans)
    while len(spans) > limit:
        at = min(range(len(spans) - 1), key=lambda index: spans[index + 1].x - spans[index].x - spans[index].w)
        left, right = spans[at], spans[at + 1]
        status = min(left.status, right.status, key=JOINED.index)
        spans[at:at + 2] = [Span(left.x, max(left.x + left.w, right.x + right.w) - left.x, status)]
    return tuple(spans)


def cap_everything(page):
    """Step 4. Its output has a size limit whatever the input.

    At most MAX_GROUPS groups stay, those with a running row first. At most MAX_ROWS rows a group stay, running and failed rows first.
    A row whose parent row was cut moves up a depth. At most MAX_SPANS bars a row and COORDINATOR_SPANS on the coordinator's row stay.
    At most MAX_STRIP in-flight items stay, those of a group still on the page first.
    The coordinator's name, ids, labels, summaries, and model names are clipped, and a link over LINK_BYTES bytes is dropped.
    """
    def shown(item):
        link = item.pr if len(item.pr.encode()) <= LINK_BYTES else ""
        return replace(item, id=clip(item.id, ID_BYTES), summary=clip(item.summary, SUMMARY_BYTES), pr=link)

    def narrow(row, depth, limit):
        return replace(row, depth=depth, label=clip(row.label, LABEL_BYTES), model=clip(row.model, MODEL_BYTES), spans=joined(row.spans, limit))

    def running(row):
        return row.open_seconds is not None

    groups, cut = [], 0
    stays = first(page.groups, MAX_GROUPS, lambda group: any(running(row) for row in group.rows))
    for group, stay in zip(page.groups, stays):
        keeps = first(group.rows, MAX_ROWS if stay else 0, lambda row: running(row) or row.status is Status.FAILED)
        rows, above = [], []
        for row, keep in zip(group.rows, keeps):
            del above[row.depth:]
            if keep:
                rows.append(narrow(row, sum(above), MAX_SPANS))
            else:
                cut += row.stands_for
            above.append(keep)
        if stay:
            groups.append(replace(group, item=shown(group.item) if group.item else None, rows=tuple(rows)))
    drawn = [group.item for group in groups]
    listed = first(page.items, MAX_STRIP, lambda item: shown(item) in drawn)
    items = tuple(shown(item) for item, keep in zip(page.items, listed) if keep)
    hidden = replace(page.hidden, cut_agents=page.hidden.cut_agents + cut, cut_in_flight=page.hidden.cut_in_flight + len(page.items) - len(items))
    coordinator = narrow(page.coordinator, 0, COORDINATOR_SPANS) if page.coordinator else None
    return replace(page, name=clip(page.name, NAME_BYTES), coordinator=coordinator, groups=tuple(groups), items=items, hidden=hidden, fold=page.fold + 1)


# Applied in order, each to the result of the one before, until the document fits.
FOLDS = (fold_finished_subagents, fold_quiet_items, drop_old_items, cap_everything)


def fit(page, budget):
    """(page, document) for the first of page and its folds whose document is at most budget bytes, or for the last fold."""
    document = render_html(page)
    for fold in FOLDS:
        if len(document.encode()) <= budget:
            break
        page = fold(page)
        document = render_html(page)
    return page, document


def say(number, one, several, **values):
    return (one if number == 1 else several).format(n=number, **values)


def notes(page):
    """One sentence for each thing the page does not draw as its own row, in a fixed order."""
    rows = [row for group in page.groups for row in group.rows]
    # A summary row below a row of depth 0 came from fold_finished_subagents. A summary row of depth 0 came from fold_quiet_items.
    below = [row.stands_for for row in rows if row.stands_for > 1 and row.depth]
    whole = [row for row in rows if row.stands_for > 1 and not row.depth]
    hidden, lines = page.hidden, []
    if not page.totals.agents + page.totals.subagents:
        lines.append("No agent or sub-agent of this coordinator ran in this window.")
    if below:
        lines.append(say(len(below), "{agents} sub-agents that are done or stopped are shown as 1 summary row.",
                         "{agents} sub-agents that are done or stopped are shown as {n} summary rows.", agents=sum(below)))
    if whole:
        lines.append(say(len(whole), "1 merged or dropped work item whose agents are all done or stopped is shown as one row.",
                         "{n} merged or dropped work items whose agents are all done or stopped are each shown as one row."))
    if hidden.dropped_items:
        lines.append(say(hidden.dropped_items, "1 merged or dropped work item with {agents} is not shown. Use a larger --max-bytes to see more.",
                         "{n} merged or dropped work items with {agents} are not shown. Use a larger --max-bytes to see more.",
                         agents=count(hidden.dropped_agents, "agent")))
    if hidden.cut_agents:
        lines.append(say(hidden.cut_agents, "1 more agent is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
                         "{n} more agents are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."))
    if hidden.cut_in_flight:
        lines.append(say(hidden.cut_in_flight, "1 more work item in flight is not listed.", "{n} more work items in flight are not listed."))
    if hidden.unknown_status:
        lines.append(say(hidden.unknown_status, "T3 gave 1 status this tool cannot read.", "T3 gave {n} statuses this tool cannot read."))
    if hidden.by_request_name:
        lines.append(say(hidden.by_request_name, "1 agent is grouped by the name of the request that started it.",
                         "{n} agents are grouped by the name of the request that started them."))
    if hidden.other_threads:
        lines.append(say(hidden.other_threads, "1 other thread ran in T3 outside this coordinator.", "{n} other threads ran in T3 outside this coordinator."))
    return lines


if __name__ == "__main__":
    sys.exit(main())
