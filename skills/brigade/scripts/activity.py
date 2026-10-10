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
    `open_seconds` is how long the open turn has run, counted from its start. It is None exactly when `status` is not in OPEN.
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
    for thread in kept:
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


if __name__ == "__main__":
    sys.exit(main())
