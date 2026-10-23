import argparse
import enum
import fcntl
import http.server
import json
import os
import re
import secrets
import socketserver
import sqlite3
import stat
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import quote, unquote, urlsplit
from activity_lib.store import SourceError


PROJECTION = "thread-projections"


SUPPORTED_PROJECTION = 2


THREADS = "orchestration_v2_projection_threads"


RUNS = "orchestration_v2_projection_runs"


SUBAGENTS = "orchestration_v2_projection_subagents"


METADATA = "orchestration_v2_projection_metadata"


T3_SHAPE = {
    METADATA: ("projection_name", "schema_version"),
    THREADS: ("thread_id", "title", "default_provider", "payload_json", "project_id"),
    RUNS: ("thread_id", "status", "requested_at", "completed_at"),
    SUBAGENTS: ("subagent_id", "thread_id", "child_thread_id", "status", "started_at", "completed_at"),
}


THREAD_PAYLOAD_KEY = "modelSelection"


SELECTION_KEYS = ("model", "instanceId")


NULLABLE = {RUNS: ("completed_at",), SUBAGENTS: ("child_thread_id", "started_at", "completed_at")}


STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{3})?(?:Z|[+-]\d\d:\d\d)", re.ASCII)


T3_DATABASE = "statev2.sqlite"


BATCH = 500


CHANGED = "this T3 build stores threads differently, so update pstack-t3"


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


OPEN = (Status.RUNNING, Status.QUEUED)


LIVE = (*OPEN, Status.WAITING)


QUIET = (Status.DONE, Status.STOPPED)


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
    ids: frozenset = frozenset()


def t3_database(flag, environ, home):
    variable = environ.get("T3CODE_HOME", "").strip()
    chosen = flag or variable
    source = "the --t3-home directory" if flag else "the T3CODE_HOME directory" if variable else "the default base directory"
    if chosen == "~" or chosen.startswith("~/"):
        base = home / chosen[2:]
    else:
        base = Path(chosen) if chosen else home / ".t3"
    for state in ("userdata",) if chosen else ("userdata", "dev"):
        try:
            found = (base / state / T3_DATABASE).is_file()
        except OSError as error:
            raise SourceError(f"cannot look for T3's database under {source} ({error.strerror}); check its permissions and run this again") from None
        if found:
            return base / state / T3_DATABASE
    raise SourceError(f"no T3 Code database under {source}; pass --t3-home <T3's base directory> or set T3CODE_HOME")


def open_t3(path):
    """mode=ro reads T3's database through SQLite's locks, so the read is one committed snapshot and changes no committed data.
    A read-only open can create an empty log and index beside a database whose last writer just closed.
    With a write-ahead log and its index beside the database, the read reads that log.
    With a log and no index, a read would create the index or miss the log's rows, so the open is refused.
    """
    log, index = Path(f"{path}-wal").exists(), Path(f"{path}-shm").exists()
    if log and not index:
        raise SourceError("T3's database has a write-ahead log with no index beside it; start T3 Code and run this again")
    try:
        connection = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True, timeout=2, isolation_level=None)
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
    """A delegated child's thread id holds, after URL decoding twice, `delegate-task:<request name>` at its end."""
    _, marker, name = unquote(unquote(thread_id)).rpartition("delegate-task:")
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


def check_parents(parents_of):
    """Refuse a row that makes a thread its own parent, and rows that make a thread its own ancestor. The rows below a coordinator are drawn as a tree.
    parents_of holds every parent each child has in any row, whichever row read_t3 then uses for the tree.
    """
    clear = set()
    for start in parents_of:
        if start in clear:
            continue
        chain, stack = {start}, [(start, iter(parents_of[start]))]
        while stack:
            thread, parents = stack[-1]
            parent = next(parents, None)
            if parent is None:
                stack.pop()
                chain.discard(thread)
                clear.add(thread)
            elif parent in chain:
                raise SourceError(f"T3's {SUBAGENTS} makes a thread its own ancestor; {CHANGED}")
            elif parent in parents_of and parent not in clear:
                chain.add(parent)
                stack.append((parent, iter(parents_of[parent])))


def same_project(connection, threads, coordinators):
    """How many of threads are in the T3 project of this coordinator's thread. 0 when the store records no coordinator thread.
    The project is the current thread's. It is the previous thread's when T3 has no row for the current one.
    The query returns thread ids this run already read.
    """
    if not coordinators:
        return 0
    own, found = ", ".join("?" * len(coordinators)), 0
    for batch in batches(threads):
        marks = ", ".join("?" * len(batch))
        found += len(connection.execute(
            f"select thread_id from {THREADS} where thread_id in ({marks}) and project_id = "
            f"(select project_id from {THREADS} where thread_id in ({own}) order by thread_id = ? desc limit 1)",
            (*batch, *coordinators, coordinators[0])).fetchall())
    return found


def read_t3(connection, window, roots, coordinators=()):
    parent_of, parents_of, delegations, nodes, unstarted, seen = {}, {}, {}, {}, [], set()
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
        parents_of.setdefault(child, set()).add(parent)
        if start is None:
            parent_of.setdefault(child, parent)
            unstarted.append((parent, child))
        elif child not in delegations or start >= delegations[child].start:
            parent_of[child] = parent
            delegations[child] = Delegation(parse_status(status, end is not None), start, end)
    check_parents(parents_of)
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
    kept, _ = in_scope(parent_of, roots, active)
    others = same_project(connection, active - kept, coordinators)
    described = {}
    for batch in batches(kept):
        marks = ", ".join("?" * len(batch))
        for thread, title, provider, payload in connection.execute(
                f"select thread_id, title, default_provider, payload_json from {THREADS} where thread_id in ({marks})", batch):
            described[thread] = (text_in(title, THREADS, "title", empty=True), text_in(provider, THREADS, "default_provider"), *selection_of(payload))
    agents = {}
    for thread in sorted(kept):
        if thread not in described:
            raise SourceError(f"T3's {THREADS} has no row for a thread that {RUNS} or {SUBAGENTS} names; {CHANGED}")
        title, provider, model, _ = described[thread]
        own = tuple(sorted(turns.get(thread, ()), key=lambda turn: turn.start))
        delegation = delegations.get(thread)
        if delegation and not (thread in active and touches(delegation.start, delegation.end, window)):
            delegation = None
        agents[thread] = Agent(thread, parent_of.get(thread), request_name(thread), provider, model, title, own, delegation)
    statuses = [record.status for agent in agents.values() for record in (*agent.turns, agent.delegation) if record]
    waiting = sum(parent in kept and (child is None or child not in turns) for parent, child in unstarted)
    return T3(agents, {node: child for node, child in nodes.items() if child in kept}, others, statuses.count(Status.UNKNOWN), waiting, frozenset(seen | ran))


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


T3_KINDS = ("mcp", "thread", "node", "run", "task", "run-attempt", "provider-turn", "provider-session", "context-transfer", "context-handoff")
