#!/usr/bin/env python3
"""Show what one brigade coordinator's agents and sub-agents are doing, as one HTML page.

Opens the coordinator's store and T3 Code's state database read-only.
Prints one self-contained HTML document, or plain lines with --text, or writes either to the file --out names.
The output and the newline after it take at most --max-bytes bytes.
Output that would take more is shortened. It counts the agents, work items, attempt lines, and pull request links it leaves out, and it ends each string it cuts with an ellipsis.

Every string a page emits passes one filter after its parts are joined.
In the HTML document that is each string of the data element. In the plain form it is each line.
The filter drops each character UTF-8 cannot hold and writes each control character, line separator, and paragraph separator as a space.
It then removes each word that holds private text and repeats until no word does.
The first word that stays follows the spaces that start the string, and each later word that stays follows the spaces that stood before it.
A string that still holds private text after 8 passes is emitted empty.
The filter reads each string as written, as the HTML document writes it, and both with their percent escapes decoded once and twice.

Private text is an identifier or a path this run read, as stored and with its own percent escapes decoded once and twice.
Its control characters are read as spaces, and it is compared with each character of both texts in lower case when that is one character.
The identifiers are the thread ids, the sub-agent ids, and the work items' task values in the store and in T3's turn and sub-agent rows.
An identifier that reads as ordinary words is not private text.
That is at most 48 characters of letters and digits in runs joined by single spaces, dots, or hyphens, where a run with both letters and digits is at most 8 characters.
A form of spaces only is not private text either. Neither is an identifier form of fewer than 4 characters.
A non-empty thread-column value is read as stored and with its percent escapes decoded once and twice, and each of those forms is also read without the spaces that start and end it.
A reading that holds a character other than a space is private text even when it reads as ordinary words.
A form of 4 or more characters is private text anywhere in a word.
A shorter form is private text only where no letter, digit, underscore, or hyphen stands directly before or after it.
The paths are the store's directory, the project root the store records, T3 Code's base directory and database, the home directory, and the --out file.
Each path is read as an absolute path, with symbolic links followed, and as given when that is absolute.
A path form of 4 or more characters is private text anywhere in a word.
A path form of fewer than 4 characters is read without the slashes that end it, and a form of slashes only is read as one slash. It is private text only as a whole word.
That word starts with a slash that starts the string or follows one of the characters the last path shape below can follow, and it ends before the next space, quotation mark, or backtick.
The word is also the form when it is the form before one or more of a slash, a closing round, square, curly, or angle bracket, and the marks . , ; : ! ? |.

Private text is also text of one of these shapes, with upper and lower case letters read alike.
A UUID.
One of mcp, thread, node, run, task, run-attempt, provider-turn, provider-session, context-transfer, and context-handoff anywhere in a word, then a colon, then a letter, a digit, an underscore, or a percent sign.
An email address, which is an at sign between a local part and a domain.
The domain is a name that holds a dot before two or more letters. Or it is square brackets around four numbers of 1 to 3 digits joined by dots, or around `IPv6:` and one or more hexadecimal digits, colons, and dots.
The local part is 1 to 62 characters other than a straight double quotation mark between two such marks.
Or it is 1 to 62 characters between two such marks, read from the left with a backslash and the character after it as a pair, where the last pair ends before the closing mark and each such mark among the characters is the second character of a pair.
Or it ends in a character other than a space, an at sign, a round or square bracket, an angle bracket, a straight quotation mark, a comma, a semicolon, a colon, or an ampersand.
`file:` with no letter or digit before it, then a letter, a digit, an underscore, a slash, a backslash, a dot, a tilde, or a percent sign.
A drive path, which is a letter from a to z with no letter or digit before it, then a colon, then a slash or a backslash.
A path that starts the string or follows a space, a quotation mark, a backtick, an opening bracket, an angle bracket, an equals sign, a colon, a comma, a semicolon, or a vertical bar.
Such a path starts with a slash before a character other than a space, a slash, a quotation mark, or a backtick, and it holds a second slash with no space, quotation mark, or backtick before it and a character other than those after it.
It can start with two or more slashes instead when no colon stands before them.
Or it starts with a tilde before a slash or a backslash, with a tilde and a name that starts with a letter or an underscore before one, with $HOME or ${HOME} before one, or with two backslashes before a letter, a digit, an underscore, or a dot.
A path after an opening quotation mark or a backtick is private text up to the mark that closes it.
Any other path, and a path with no mark that closes it, is private text to the end of its word and through each next word that holds a slash or a backslash and does not start with a slash.

Private text is also the request name of a delegated task when that name holds a hyphen.
The request names are the text after the last `delegate-task:` of each identifier this run read, with the identifier's percent escapes decoded once and without the spaces that start and end that text.
A name that is runs of letters and digits joined by single hyphens is private text only as a whole such run, compared in lower case as an identifier is.
A longer such run that holds the name is not the name. Neither is the name after a slash that starts the string or follows one of the characters the last path shape above can follow, such as /code-review.
Any other name with a hyphen is read as it is and with its percent escapes decoded once and twice, each form without the spaces that start and end it. It is compared as a path this run read is.
A form of 4 or more characters is private text anywhere in a word.
A form of fewer than 4 characters is private text only where no letter, digit, underscore, or hyphen stands directly before or after it.
A request name with no hyphen is not private text.

The filter removes a word for no other reason.
The filter leaves a work item id, a summary, a title, the store's name, and a model name as they are unless they hold private text. It leaves a relative path such as src/a.py.
It leaves a word of one segment that starts with a slash, such as /review, unless it holds private text.
A delegated task's own request name of two or more hyphen-separated parts is split into words for its label.
A pull request link is printed only as https://host/owner/repository/pull/number. Userinfo, a query, and a fragment are dropped.
A work item on the page whose nonblank pull request value cannot be converted to such an address, or whose address the filter would change, has no link and is counted in a note.
Different work items can print the same id, summary, state, and link. Each work item that is in flight or has an agent in the window is still listed or counted as its own work item.

--serve runs a local HTTP server on 127.0.0.1 for this store's page and prints its token address. --live-page prints the wrapper that polls the live server for html_render at the given address plus /activity/<token>. The address defaults to the fixed http://127.0.0.1:48731, which does not follow --serve. It accepts an origin such as https://home.example.ts.net:48731. The token lives in activity-token under the store directory.
"""

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


DEFAULT_HOURS = 3.0
MIN_HOURS = 0.01
MAX_HOURS = 168.0
BUDGET = 16000
FIXED_BUDGET = 8000
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
    THREADS: ("thread_id", "title", "default_provider", "payload_json", "project_id"),
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
# A thread's project id is compared inside SQLite and never returned.
NULLABLE = {RUNS: ("completed_at",), SUBAGENTS: ("child_thread_id", "started_at", "completed_at")}
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{3})?(?:Z|[+-]\d\d:\d\d)", re.ASCII)
T3_DATABASE = "statev2.sqlite"
BATCH = 500
CHANGED = "this T3 build stores threads differently, so update pstack-t3"

STORE_RECORD = "restaurant.json"
STORE_LOCK = "restaurant.lock"
UNITS_TABLE = "dishes.tsv"
LOG_TABLE = "log.tsv"
GOALS_TABLE = "goals.tsv"
THREADS_TABLE = "threads.tsv"
STORE_COLUMNS = {
    UNITS_TABLE: ("id", "at", "state", "station", "tickets", "task", "thread", "branch", "pr", "sha", "summary", "timebox", "lease", "paths", "reported", "kind", "goal"),
    LOG_TABLE: ("at", "kind", "id", "state", "note"),
    GOALS_TABLE: ("id", "at", "state", "name", "purpose", "done", "share", "closed"),
    THREADS_TABLE: ("at", "dish", "attempt", "role", "thread", "seat", "start", "end"),
}
# How many trailing cells a row of each table may lack. A missing cell reads as empty. brigade.py allows the same.
ADDED_COLUMNS = {UNITS_TABLE: 2}
TABLE_WORDS = {UNITS_TABLE: "work item table", LOG_TABLE: "log", GOALS_TABLE: "goals table", THREADS_TABLE: "thread table"}
# The forms read_store accepts at these keys of the store's record.
# Each is absent, null, or a string that encodes as UTF-8 and holds no NUL character.
RECORD_WORDS = {"restaurant": "name", "projectRoot": "project root", "thread": "thread", "previousThread": "previous thread"}

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
DRIVERS = frozenset({"claudeAgent", "codex", "grok", "opencode", "cursor"})
OTHER_MODEL = "other model"
MODEL_SLOTS = 5
OTHER_SLOT = 6
MIXED_SLOT = 0
UNGROUPED = "Not tied to a work item"
UNNAMED = "work item"
KINDS = ("feature", "fix", "polish", "upkeep", "study")
UNASSIGNED = "Unassigned"
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
class GoalRecord:
    id: str
    name: str


@dataclass(frozen=True)
class Goal:
    id: str
    name: str


@dataclass(frozen=True)
class Unit:
    id: str
    state: str
    summary: str
    pr: str
    worker: str
    earlier_workers: tuple[str, ...]
    task: str
    kind: str = ""
    goal: str = ""
    branch: str = ""


@dataclass(frozen=True)
class Store:
    name: str
    slug_parts: frozenset
    coordinators: tuple[str, ...]
    units: tuple[Unit, ...]
    project_root: str = ""
    goals: tuple[GoalRecord, ...] = ()
    threads: tuple = ()


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


@dataclass(frozen=True)
class Assignment:
    unit: Optional[str]
    evidence: Evidence


@dataclass(frozen=True)
class Span:
    x: int
    w: int
    status: Status


# The roles the store records, in display order. It matches the store role tuple. A row with another role sorts after these as unknown.
ROLES = ("worker", "fix worker", "reviewer", "panel member", "code delegate", "claims check", "architect runner")


@dataclass(frozen=True)
class RoleCount:
    role: str
    n: int
    opened: int


@dataclass(frozen=True)
class AttemptFold:
    summary: str = ""
    lines: tuple = ()
    attempts: tuple = ()
    ranks: tuple = ()
    counts: tuple = ()


@dataclass(frozen=True)
class Row:
    depth: int
    label: str
    model: str
    status: Status
    seconds: int
    spans: tuple[Span, ...]
    open_seconds: Optional[int] = None
    stands_for: int = 1
    earlier: bool = False
    color_slot: Optional[int] = None

    def __post_init__(self):
        if self.color_slot is None:
            object.__setattr__(self, "color_slot", model_slot(self.model))


@dataclass(frozen=True)
class Item:
    id: str
    summary: str
    state: str
    tone: str
    pr: str
    in_flight: bool
    no_link: str = ""
    # The work item's position among the store's work items, so two work items that print alike stay two. No form prints it.
    key: int = 0
    kind: str = ""
    goal: str = ""
    lines: tuple = ()
    fold: AttemptFold = AttemptFold()


@dataclass(frozen=True)
class LedgerLine:
    attempt: str
    role: str
    seat: str
    open: bool


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
    cut_lines: int = 0
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
    goals: tuple[Goal, ...] = ()
    flight: Optional["Flight"] = None


@dataclass(frozen=True)
class StripGroup:
    heading: str
    count: str
    items: tuple[Item, ...]


@dataclass(frozen=True)
class Flight:
    groups: tuple[StripGroup, ...]


# The live view's named shapes. Secret holds the token bytes, the route those bytes answer at, and the URL that embeds both. Settings holds what one page build reads. Pages holds the cached body bytes, the time that body was built, and the lock every read and write takes.
DEFAULT_PORT = 48731
LIVE_REBUILD_SECONDS = 10
LIVE_POLL_MS = 30000
TOKEN_FILE_NAME = "activity-token"
TOKEN_BYTES = 32


@dataclass(frozen=True)
class Origin:
    text: str


@dataclass(frozen=True)
class Secret:
    token: bytes
    route: bytes
    url: str
    given_origin: bool = False


@dataclass(frozen=True)
class Settings:
    store: Path
    database: Path
    hours: float
    max_bytes: int


@dataclass(frozen=True)
class Pages:
    body: bytes
    built: float
    lock: threading.Lock = field(default_factory=threading.Lock)


def parser():
    top = argparse.ArgumentParser(prog="activity.py", description=__doc__)
    top.add_argument("--at", help="the coordinator's store directory (default $BRIGADE_DIR)")
    top.add_argument("--hours", type=float, default=DEFAULT_HOURS, help="how many hours back the page looks, from 0.01 to 168, rounded to whole seconds")
    top.add_argument("--text", action="store_true", help="print plain lines instead of the HTML document")
    top.add_argument("--out", help="write the output to this file and print `wrote <file> (<n> bytes)` instead; a file inside T3 Code's directory or the store is refused")
    top.add_argument("--max-bytes", type=int, default=BUDGET, help="the most bytes the output and the newline after it take, from 16000 to 500000")
    top.add_argument("--t3-home", help="T3 Code's base directory (default $T3CODE_HOME, else ~/.t3)")
    top.add_argument("--serve", nargs="?", const=DEFAULT_PORT, default=None, type=int, metavar="PORT",
                     help="run a local HTTP server on 127.0.0.1 for this store's page, rebuilt at most every 10 seconds; PORT defaults to 48731 and 0 picks an ephemeral port. The wrapper sends its token to whatever listens on the port, so on a machine with other local users, do not leave a rendered wrapper polling while the server is stopped")
    top.add_argument("--live-page", nargs="?", const="", default=None, metavar="ORIGIN",
                     help="print the self-contained HTML wrapper that polls the live server every 30 seconds for html_render at the given address plus /activity/<token>; with no value the address is the fixed http://127.0.0.1:48731 and does not follow --serve, with an http:// or https:// origin with a host and no userinfo, path, query, or fragment such as https://home.example.ts.net:48731 it polls that origin; a value with whitespace, a backslash, ?, or #, or with a port that does not parse, is refused; the wrapper holds the token, so never share or commit it")
    return top


def main(argv=None):
    try:
        output = run(sys.argv[1:] if argv is None else argv)
    except ActivityError as error:
        print(f"activity: {error}", file=sys.stderr)
        return error.status
    if output is None:
        return 0
    sys.stdout.buffer.write(output.encode(errors="backslashreplace") + b"\n")
    return 0


def render_page(settings, text=False, out=None, store=None):
    """The page output for one store. run() and each live view request share it, so both filter private text alike. run() passes the store it already read, keeping its refusal order."""
    if store is None:
        store = read_store(settings.store)
    path = settings.database
    now = time.time()
    window = Window(now - round(settings.hours * 3600), now)
    connection = open_t3(path)
    try:
        check_shape(connection)
        check_coordinator(connection, store.coordinators)
        t3 = read_t3(connection, window, roots_of(store), store.coordinators)
    except sqlite3.Error as error:
        raise unreadable(error, path) from None
    finally:
        connection.close()
    privacy = privacy_of(store, t3, (settings.store, path, path.parents[1], Path.home(), *((out,) if out else ())))
    page = build_page(store, t3, window, privacy)
    budget = settings.max_bytes - len("\n")
    frame = FRAME_HEIGHT - FRAME_MARGIN if settings.max_bytes == BUDGET else None
    return fit_text(page, budget, privacy.clean) if text else fit(page, budget, privacy.clean, frame)[1]


def run(argv):
    args = parser().parse_args(argv)
    if not MIN_HOURS <= args.hours <= MAX_HOURS:
        raise ActivityError("--hours must be from 0.01 to 168; pass a number in that range")
    if not BUDGET <= args.max_bytes <= MAX_BUDGET:
        raise ActivityError("--max-bytes must be from 16000 to 500000; pass a number in that range")
    directory = store_dir(args.at, os.environ)
    if args.live_page is not None:
        origin = None if args.live_page == "" else origin_of(args.live_page)
        return live_wrapper(secret_of(directory, DEFAULT_PORT, origin).url)
    if args.serve is not None:
        if not 0 <= args.serve <= 65535:
            raise ActivityError("--serve takes a port from 0 to 65535; 0 picks an ephemeral port and no value means 48731")
        path = t3_database(args.t3_home, os.environ, Path.home())
        serve(Settings(directory, path, args.hours, args.max_bytes), secret_of(directory, args.serve), args.serve)
        return None
    store = read_store(directory)
    path = t3_database(args.t3_home, os.environ, Path.home())
    target = out_file(args.out, path, directory) if args.out else None
    output = render_page(Settings(directory, path, args.hours, args.max_bytes), args.text, args.out, store)
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
    """The rows of a table's finished lines after its header line. A table that does not exist has none.
    A finished line ends in a newline. The bytes after the last newline are never decoded.
    A finished line that is not UTF-8 is refused as malformed, with its own line number. So is a finished line after the header with a wrong field count.
    A work item row may lack its last cell or its last two, and each missing cell reads as empty.
    The header line is decoded and never parsed.
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
    rows = []
    for number, line in enumerate(data.split(b"\n")[:-1], start=1):
        try:
            text = line.decode()
        except UnicodeDecodeError:
            raise malformed(table, number) from None
        if number == 1:
            continue
        fields = text.split("\t")
        missing = len(columns) - len(fields)
        if not 0 <= missing <= ADDED_COLUMNS.get(table, 0):
            raise malformed(table, number)
        rows.append(dict(zip(columns, fields + [""] * missing)))
    return rows


def malformed(table, number):
    return ActivityError(f"line {number} of the store's {TABLE_WORDS[table]} is malformed; fix or remove it")


def record_text(meta, key):
    """The string the store's record holds at key, or an empty string when the key is absent or null."""
    value = meta.get(key)
    if value is None:
        return ""
    try:
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError
        value.encode()
    except ValueError:
        raise ActivityError(f"the store's coordinator record holds a {RECORD_WORDS[key]} this tool cannot read; restore it and run this again") from None
    return value


def goals_of(directory):
    seen = set()
    kept = []
    for row in read_table(directory, GOALS_TABLE):
        ident = row["id"]
        if not ident or ident in seen:
            continue
        seen.add(ident)
        kept.append(GoalRecord(ident, row["name"]))
    return tuple(kept)


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
        goals = goals_of(directory)
        threads = tuple(read_table(directory, THREADS_TABLE))
    except OSError as error:
        raise ActivityError(f"cannot read the store ({error.strerror}); check its permissions and run this again") from None
    earlier = {}
    for row in log:
        if row["kind"] == "worker" and row["state"] == "retired" and row["note"]:
            earlier.setdefault(row["id"], []).append(row["note"])
    by_id = {
        row["id"]: Unit(row["id"], row["state"], row["summary"], row["pr"], row["thread"], tuple(earlier.get(row["id"], ())), row["task"], row["kind"], row["goal"], row["branch"])
        for row in units
    }
    name, root = record_text(meta, "restaurant"), record_text(meta, "projectRoot")
    coordinators = tuple(value for value in (record_text(meta, "thread"), record_text(meta, "previousThread")) if value.strip())
    return Store(name, frozenset(re.findall(r"[a-z0-9]+", f"{name} {Path(root).name}".lower())), coordinators, tuple(by_id.values()), root, goals, threads)


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
        try:
            found = (base / state / T3_DATABASE).is_file()
        except OSError as error:
            raise SourceError(f"cannot look for T3's database under {source} ({error.strerror}); check its permissions and run this again") from None
        if found:
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


UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
# T3 Code writes one of these words and a colon before most ids. A live T3 database read on 2026-10-10 used them for threads, runs, sub-agents,
# delegated tasks, provider threads, sessions, and turns, and context transfers and handoffs. A thread id can also be a bare UUID.
# `thread` also matches in `provider-thread`.
T3_KINDS = ("mcp", "thread", "node", "run", "task", "run-attempt", "provider-turn", "provider-session", "context-transfer", "context-handoff")
OPENS = r"(?:^|(?<=[\s\"'`“”‘’(\[{<>=:,;|]))"
QUOTES = "\"'`“”‘’"
# One segment after a slash is a slash command such as /review, so a path that starts with a slash needs a second segment.
# A colon before two slashes is a URL's.
SEGMENT = rf"[^\s/{QUOTES}]"
PATH = rf"(?:{OPENS}(?:(?:/|(?<!:)/{{2,}})(?={SEGMENT}+/[^\s{QUOTES}])|~(?:[^\W\d][\w.-]*)?[/\\]|\$(?:HOME|\{{HOME\}})[/\\]|\\\\[\w.])|(?<![^\W_])[a-z]:[/\\])"
# The shapes the module docstring lists, but an email address, which Privacy.found looks for at each at sign.
SHAPES = re.compile(rf"(?P<path>{PATH})|{UUID}|(?:{'|'.join(T3_KINDS)}):[\w%]|(?<![^\W_])file:(?=[\w/\\.~%])", re.IGNORECASE)
LOCAL = re.compile(r"(?:[^\s@<>()\[\]\"',;:&]+|\"[^\"]+\"|\"(?:\\.|[^\"\\])+\")\Z", re.DOTALL)
LOCAL_CHARS = 64
DOMAIN = re.compile(r"[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}|\[(?:\d{1,3}(?:\.\d{1,3}){3}|IPv6:[0-9A-Fa-f:.]+)\]", re.IGNORECASE)
TOKEN_ATOM = r"[^\W_]"
TOKEN = rf"{TOKEN_ATOM}+(?:-{TOKEN_ATOM}+)+"
OPENS_AT = re.compile(OPENS)
PIECE = re.compile(rf"{TOKEN_ATOM}+|/")
SLASH_WORD = re.compile(rf"{OPENS}/[^\s{QUOTES}]*")
# A form of fewer than 4 characters of a request name that is not one TOKEN is not private text with one of these directly before or after it.
BESIDE = r"[\w-]"
CLOSERS = "/)]}>.,;:!?|"
CLOSES = {'"': '"', "'": "'", "`": "`", "“": "”", "‘": "’"}
WORD_END = re.compile(r"\S*")
NEXT_PATH_WORD = re.compile(r"\s+(?!/)\S*[/\\]\S*")
WORD = re.compile(r"\S+")
RUN = r"(?:[^\W\d_]+|\d+|[^\W_]{1,8})"
ORDINARY = re.compile(rf"{RUN}(?:[ .-]{RUN})*")
ORDINARY_CHARS = 48
ESCAPES = re.compile("(?:%[0-9A-Fa-f]{2})+")
ANCHOR = 4
PASSES = 8
MODEL_CHARS = 48
HOST = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
PULL = re.compile(r"/(?!\.+/)([A-Za-z0-9._-]+)/(?!\.+/)([A-Za-z0-9._-]+)/pull/[0-9]+")
REFUSED, CUT = "refused", "cut"
PATH_TAIL = re.compile(r"/root/(?:\S*/)?([A-Za-z0-9_-]+)")
SLUG = re.compile(r"[a-z0-9.]+(?:-[a-z0-9.]+)+")
HASH = re.compile(r"(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,}")
ROLE = re.compile(r"Act as the (.+?) sub-agent")
UNIT_SUFFIX = re.compile(r"(.+?)[a-z][0-9]*")
BAR = {
    Status.RUNNING: Status.RUNNING, Status.QUEUED: Status.QUEUED,
    Status.WAITING: Status.DONE, Status.DONE: Status.DONE,
    Status.FAILED: Status.FAILED, Status.STOPPED: Status.STOPPED, Status.UNKNOWN: Status.UNKNOWN,
}
ENTITIES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"), ("\\", "&#92;"))
UNPRINTED = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def entities(text):
    text = UNPRINTED.sub(" ", text)
    for character, entity in ENTITIES:
        text = text.replace(character, entity)
    return text


def plain(text):
    """text without a character UTF-8 cannot hold, and with one space for each character entities() writes as a space."""
    return UNPRINTED.sub(" ", text.encode("utf-8", "ignore").decode())


def lowered(text):
    """text in lower case, one character for each character of text."""
    low = text.lower()
    return low if len(low) == len(text) else "".join(char.lower() if len(char.lower()) == 1 else char for char in text)


def ordinary(text):
    """True for at most 48 characters of letters and digits in runs joined by single spaces, dots, or hyphens, where a run that holds both letters and digits is at most 8 characters."""
    return len(text) <= ORDINARY_CHARS and ORDINARY.fullmatch(text) is not None


def unescaped(text, where=None):
    """text with each run of percent escapes decoded once, and for each character of the result the span of the source it came from.
    where gives that span for each character of text. Without it, text is the source.
    """
    where = [(at, at + 1) for at in range(len(text))] if where is None else where
    parts, origin, at = [], [], 0
    for run in ESCAPES.finditer(text):
        decoded = bytes.fromhex(run.group().replace("%", "")).decode("utf-8", "replace")
        parts += [text[at:run.start()], decoded]
        origin += where[at:run.start()] + [(where[run.start()][0], where[run.end() - 1][1])] * len(decoded)
        at = run.end()
    return "".join([*parts, text[at:]]), origin + where[at:]


def as_entities(text):
    """entities(text) for a text plain() returned, and for each character of it the span of text it came from."""
    written = dict(ENTITIES)
    parts = [written.get(char, char) for char in text]
    return "".join(parts), [(at, at + 1) for at, part in enumerate(parts) for _ in part]


def views(text):
    """The forms of text the filter reads: as written and as entities() writes it, each also with its percent escapes decoded once and twice."""
    for view, where in ((text, None), *((as_entities(text),) if any(char in text for char, _ in ENTITIES) else ())):
        yield view, where
        for _ in range(2):
            if not ESCAPES.search(view):
                break
            view, where = unescaped(view, where)
            yield view, where


def path_end(text, match):
    """Where the path that starts at match ends. After an opening quotation mark or a backtick it ends at the mark that closes it.
    With no such pair it ends with its word, or with the last of the words after it that each hold a slash or a backslash and do not start with a slash.
    """
    quote = text[match.start() - 1:match.start()]
    close = text.find(CLOSES[quote], match.end()) if quote in CLOSES else -1
    if close >= 0:
        return close + 1
    end = WORD_END.match(text, match.end()).end()
    more = NEXT_PATH_WORD.match(text, end)
    while more:
        end = more.end()
        more = NEXT_PATH_WORD.match(text, end)
    return end


def without(text, spans):
    """text without each word that a span touches. The first word that stays follows the spaces that start text.
    Each later word that stays follows the spaces that stood before it. The spaces that end text stay when its last word stays.
    """
    marked = bytearray(len(text))
    for start, end in spans:
        marked[start:end] = b"\x01" * (end - start)
    words = list(WORD.finditer(text))
    keeps = [1 not in marked[word.start():word.end()] for word in words]
    if True not in keeps:
        return ""
    parts = [text[:words[0].start()]]
    for index, word in enumerate(words):
        if keeps[index]:
            parts += [text[words[index - 1].end():word.start()] if len(parts) > 1 else "", word.group()]
    return "".join(parts) + (text[words[-1].end():] if keeps[-1] else "")


class Privacy:
    """The one filter of both output forms. The module docstring states what it removes.
    clean() is the filter. encode() passes it each string of the document's data, and render_text passes it each line.
    field(), model(), and link() shape one value of the store or of T3 for a page, and each calls clean().
    """

    def __init__(self, identifiers=(), paths=(), requests=(), cells=()):
        """index holds each form of 4 or more characters of an identifier that does not read as ordinary words, of a path, of a request name with a hyphen that is not one TOKEN, and of a thread-column value that holds a character other than a space.
        roots holds each shorter form of a path that is not blank, in lower case, without the slashes that end it. A form of slashes only is `/`.
        names holds each request name that is one TOKEN, in lower case.
        shorts matches, in its one group, each shorter form of a request name with a hyphen that is not one TOKEN, and each shorter form of a thread-column value that holds a character other than a space, in lower case, where no BESIDE character is directly before or after it. It is None when there is no such form.
        A request name, and each form of one that is not one TOKEN, is read without the spaces that start and end it. A request name with no hyphen is in none of the four.
        A thread-column value is read as stored and with its percent escapes decoded once and twice, and each of those forms is also read without the spaces that start and end it. ordinary() does not exempt it. A form of spaces only is in none of the four.
        """
        index, roots, shorts = {}, set(), set()
        requests = {plain(value).strip() for value in requests if "-" in value}
        tokens = {value for value in requests if re.fullmatch(TOKEN, value)}
        self.names = frozenset(map(lowered, tokens))
        known = (*((plain(value), False) for value in identifiers), *((plain(value), True) for value in paths))
        for value, path in known:
            for _ in range(3):
                low = lowered(value)
                if len(low) >= ANCHOR and low.strip() and (path or not ordinary(value)):
                    index.setdefault(low[:ANCHOR], set()).add(low)
                elif path and low.strip():
                    roots.add(low.rstrip("/") or "/")
                value = unescaped(value)[0]
        for value in requests - tokens:
            for _ in range(3):
                low = lowered(value).strip()
                if len(low) >= ANCHOR:
                    index.setdefault(low[:ANCHOR], set()).add(low)
                else:
                    shorts.add(low)
                value = unescaped(value)[0]
        for value in map(plain, cells):
            for _ in range(3):
                low = lowered(value)
                for form in (low, low.strip()):
                    if form.strip() and len(form) >= ANCHOR:
                        index.setdefault(form[:ANCHOR], set()).add(form)
                    elif form.strip():
                        shorts.add(form)
                value = unescaped(value)[0]
        self.roots = frozenset(roots)
        self.index = {anchor: tuple(found) for anchor, found in index.items()}
        self.shorts = re.compile(rf"(?<!{BESIDE})(?=({'|'.join(map(re.escape, sorted(shorts, key=len, reverse=True)))})(?!{BESIDE}))") if shorts else None
        self.cache = {}

    def found(self, text):
        """The spans of text that hold an indexed form, a token that is one of names, a form shorts matches, or a word that is one of roots, each in any case, or text of a private shape."""
        def name_spans(low):
            at = 0
            length = len(low)
            while at < length:
                piece = PIECE.match(low, at)
                if piece is None:
                    at += 1
                    continue
                if piece.group() == "/":
                    at = piece.end()
                    continue
                start, end = piece.span()
                while end < length and low[end] == "-":
                    segment = PIECE.match(low, end + 1)
                    if segment is None or segment.group() == "/":
                        break
                    end = segment.end()
                at = end
                if end == piece.end():
                    continue
                slash = start - 1
                if slash >= 0 and low[slash] == "/" and OPENS_AT.match(low, slash):
                    continue
                if low[start:end] in self.names:
                    yield start, end

        low = lowered(text)
        for at in range(len(low) - ANCHOR + 1 if self.index else 0):
            for form in self.index.get(low[at:at + ANCHOR], ()):
                if low.startswith(form, at):
                    yield at, at + len(form)
        if self.names and "-" in low:
            yield from name_spans(low)
        for match in self.shorts.finditer(low) if self.shorts else ():
            yield match.span(1)
        for match in SLASH_WORD.finditer(low) if self.roots else ():
            word = match.group()
            while word not in self.roots and len(word) > 1 and word[-1] in CLOSERS:
                word = word[:-1]
            if word in self.roots:
                yield match.start(), match.start() + len(word)
        covered = 0
        for match in SHAPES.finditer(text):
            if match.start() >= covered:
                covered = path_end(text, match) if match.lastgroup == "path" else match.end()
                yield match.start(), covered
        at = text.find("@")
        while at >= 0:
            local, domain = LOCAL.search(text, max(0, at - LOCAL_CHARS), at), DOMAIN.match(text, at + 1)
            if local and domain:
                yield local.start(), domain.end()
            at = text.find("@", at + 1)

    def private(self, text):
        """The spans of text that found() gives for any of views(text)."""
        return [(start, end) if where is None else (where[start][0], where[end - 1][1])
                for view, where in views(text) for start, end in self.found(view)]

    def clean(self, text):
        """plain(text) without each word that holds private text, repeated until no word does. A text that still holds some after 8 passes becomes empty."""
        if text not in self.cache:
            kept = plain(text)
            for _ in range(PASSES):
                spans = self.private(kept)
                if not spans:
                    break
                kept = without(kept, spans)
            else:
                kept = "" if self.private(kept) else kept
            self.cache[text] = kept
        return self.cache[text]

    def field(self, text):
        """The first line of text with single spaces between its words, as clean() leaves it."""
        line = (text.strip().splitlines() or [""])[0]
        while True:
            tidy = " ".join(self.clean(line).split())
            if tidy == line:
                return tidy
            line = tidy

    def model(self, value):
        """value as field() leaves it, without one leading `<a key of DRIVERS>/`, cut to 48 characters. OTHER_MODEL when nothing is left."""
        driver, slash, rest = value.partition("/")
        name = self.field(rest if slash and driver in DRIVERS else value)
        return short(name, MODEL_CHARS) or OTHER_MODEL

    def link(self, value):
        """The pull request address of value and no reason, or no address and REFUSED. A blank value gives neither.
        The address is https, the host, and the path /owner/repository/pull/number. Userinfo, a query, and a fragment are dropped.
        A value in another form, a value with a port, and an address clean() would change are refused.
        """
        if not value.strip():
            return "", ""
        try:
            parts = urlsplit(value)
            host, port = parts.hostname or "", parts.port
        except ValueError:
            return "", REFUSED
        if parts.scheme != "https" or port is not None or not HOST.fullmatch(host) or not PULL.fullmatch(parts.path):
            return "", REFUSED
        address = f"https://{host}{parts.path}"
        return (address, "") if self.clean(address) == address else ("", REFUSED)


def privacy_of(store, t3, paths=()):
    """The filter for one run. Its identifiers are the thread ids, sub-agent ids, and task values of the store and of T3's rows.
    Its paths are the project root the store records and the given paths.
    The non-empty values in the thread column are not identifiers.
    Its request names are the names request_name() reads from those identifiers and from those values.
    """
    ids = {*store.coordinators, *t3.ids}
    for unit in store.units:
        ids.update((unit.worker, unit.task, *unit.earlier_workers))
    cells = tuple(row["thread"] for row in store.threads if row["thread"])
    # A relative path as given can be one ordinary word, which a page may hold for another reason.
    places = {form for path in (store.project_root, *map(str, paths)) if path
              for form in (os.path.abspath(path), os.path.realpath(path), *((path,) if os.path.isabs(path) else ()))}
    return Privacy(ids, places, filter(None, map(request_name, (*ids, *cells))), cells)


def short(text, limit):
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def unit_named(part, units):
    suffixed = UNIT_SUFFIX.fullmatch(part)
    return units.get(part) or (units.get(suffixed.group(1)) if suffixed else None)


def assign(store, t3):
    """One coordinator child unit when its id part or branch tail run matches one unit, else none. RECORD wins."""
    coordinators = frozenset(store.coordinators)
    units = {unit.id.lower(): unit.id for unit in store.units}
    tails = {}
    for unit in store.units:
        tail = unit.branch.rsplit("/", 1)[-1].lower() if unit.branch else ""
        if tail:
            tails.setdefault(tail, []).append(unit.id)
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
                request = agent.request or ""
                matched = set()
                for part in request.replace("/", "-").split("-"):
                    hit = unit_named(part, units)
                    if hit:
                        matched.add(hit)
                padded = f"-{request.replace('/', '-').lower()}-"
                for tail, ids in tails.items():
                    if f"-{tail}-" in padded:
                        matched.update(ids)
                unit = next(iter(matched)) if len(matched) == 1 else None
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


def words_of(name, store=None, units=()):
    """The parts of a request name between its hyphens, joined by spaces, with `verify` read as `review` and without each part HASH matches.
    With a store, also without a leading `brigade`, the store's leading slug parts, and each part that is a work item's id in lower case, alone or before a letter from a to z and optional digits.
    """
    parts = name.split("-")
    if store and parts[0] == "brigade":
        del parts[0]
    while store and parts and parts[0] in store.slug_parts:
        del parts[0]
    return " ".join("review" if part == "verify" else part for part in parts if not (store and unit_named(part, units)) and not HASH.fullmatch(part))


def label_of(agent, store, unit, privacy):
    """`worker` or `earlier worker` for a thread the store records as one. For any other thread, the first of these that is not empty, and `sub-agent` or `agent` when all are:
    the written title, the request name's words without the parts the store explains, the role an `Act as` title names, and all the request name's words.
    A row's own request name of two or more parts reaches its label only through words_of. A title that holds its own row's request name of two or more parts is used only when it is a slug, which words_of reads, and its role is not used.
    A written title that clean() would change is not used. The last name of a `/root/.../name` title and the role of an `Act as` title are read before that check, and each passes field().
    """
    if any(agent.thread == other.worker for other in store.units):
        return "worker"
    if any(agent.thread in other.earlier_workers for other in store.units):
        return "earlier worker"
    units = {other.id.lower(): other.id for other in store.units}
    line = " ".join(plain((agent.title.strip().splitlines() or [""])[0]).split())
    request = agent.request or ""
    names = "-" in request and request.lower() in line.lower()
    written = ""
    if len(line) <= TITLE_CHARS and not line.startswith(("Act as", "You are")) and not (names and not SLUG.fullmatch(line)):
        tail = PATH_TAIL.fullmatch(line)
        written = tail.group(1) if tail else line
        if privacy.field(written) != written:
            written = ""
        if unit:
            written = re.sub(rf"^{re.escape(unit)}(?::\s*|\s+)", "", written)
        if SLUG.fullmatch(written):
            written = words_of(written, store, units)
    role = ROLE.match(line)
    for text in (written, words_of(request, store, units), role.group(1) if role and not names else "", words_of(request)):
        text = privacy.field(text)
        if text:
            return short(text, LABEL_CHARS)
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
    """True depths in tree preorder. build_page clips each depth to 2 for display."""
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
        order.append((agent, depth))
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


def is_open(row):
    return not row["end"]


def attempt_rank(cell):
    """(0, int(cell)) when cell is ASCII, its length is from 1 to 18, and every character is a digit. (1, cell) otherwise."""
    if cell.isascii() and 1 <= len(cell) <= 18 and cell.isdigit():
        return (0, int(cell))
    return (1, cell)


def lines_of(rows, dish, privacy):
    chosen = [(index, row) for index, row in enumerate(rows) if row["dish"] == dish]
    return tuple(
        LedgerLine(privacy.field(row["attempt"]), privacy.field(row["role"]), privacy.field(row["seat"]), is_open(row))
        for _, row in sorted(chosen, key=lambda pair: (attempt_rank(pair[1]["attempt"]), pair[0])))


def attempt_tail(counts):
    """The roles and the state of one fold summary. Counts holds RoleCounts in display order."""
    roles = ", ".join(count(entry.n, entry.role) for entry in counts)
    opened = sum(entry.opened for entry in counts)
    total = sum(entry.n for entry in counts)
    ended = total - opened
    if opened == 0:
        state = "all ended"
    elif ended == 0:
        state = "all open"
    else:
        state = f"{opened} open plus {ended} ended"
    return f"{roles}, {state}" if roles else state


def format_attempt(attempts, ranks, counts):
    """One summary line for folded earlier attempts."""
    tail = attempt_tail(counts)
    if not attempts:
        return tail
    if len(attempts) == 1:
        head = f"attempt {attempts[0]}"
    elif ranks and all(kind == 0 for kind, _ in ranks):
        numbers = sorted(number for _, number in ranks)
        if numbers == list(range(numbers[0], numbers[0] + len(numbers))):
            head = f"attempts {numbers[0]}-{numbers[-1]}"
        else:
            head = f"attempts {', '.join(attempts)}"
    else:
        head = f"attempts {', '.join(attempts)}"
    return f"{head}: {tail}"


def brief_attempt(ranks, counts):
    """One summary line for folded earlier attempts."""
    tail = attempt_tail(counts)
    if not ranks:
        return tail
    head = "1 attempt" if len(ranks) == 1 else f"{len(ranks)} attempts"
    return f"{head}: {tail}"


def partition_attempts(dish_rows, rows, is_finished, privacy, threads=(), parents=()):
    """Split one work item into current lines and a fold of earlier attempts."""
    if is_finished:
        current = tuple(
            LedgerLine(privacy.field(row["attempt"]), privacy.field(row["role"]), privacy.field(row["seat"]), is_open(row))
            for _, row in sorted(enumerate(dish_rows), key=lambda pair: (attempt_rank(pair[1]["attempt"]), pair[0])))
        marked = tuple(replace(row, earlier=False) for row in rows)
        return current, AttemptFold(), marked
    matched = tuple(threads) if len(threads) == len(rows) else ()
    parent_threads = tuple(parents) if len(parents) == len(rows) else ()
    family, inside = set(), False
    for at, row in enumerate(rows):
        if row.depth == 0:
            inside = row.label == "earlier worker"
        if inside:
            family.add(at)
    shown = set()
    if matched and parent_threads:
        index_of = {}
        for at, thread in enumerate(matched):
            index_of.setdefault(thread, at)
        for at, row in enumerate(rows):
            if row.status in LIVE:
                shown.add(at)
                seen, cur = {at}, at
                while True:
                    parent = parent_threads[cur]
                    if not parent:
                        break
                    parent_at = index_of.get(parent)
                    if parent_at is None or parent_at in seen:
                        break
                    shown.add(parent_at)
                    if rows[parent_at].depth == 0:
                        break
                    seen.add(parent_at)
                    cur = parent_at
    else:
        for at, row in enumerate(rows):
            if row.status in LIVE:
                depth = row.depth
                shown.add(at)
                for before in range(at - 1, -1, -1):
                    if rows[before].depth < depth:
                        depth = rows[before].depth
                        shown.add(before)
                        if not depth:
                            break
    earlier_at = family - shown
    kept = {matched[at] for at in family & shown} if matched else set()
    ordinal_of = {}
    for row in dish_rows:
        cell = row["attempt"]
        if not (cell.isascii() and 1 <= len(cell) <= 18 and cell.isdigit()) and cell not in ordinal_of:
            ordinal_of[cell] = len(ordinal_of)

    def rank_of(cell):
        if cell.isascii() and 1 <= len(cell) <= 18 and cell.isdigit():
            return (0, int(cell))
        return (1, ordinal_of[cell])

    ranked = [(index, row, rank_of(row["attempt"])) for index, row in enumerate(dish_rows)]
    distinct = sorted({rank for _, _, rank in ranked})
    top = max(distinct, default=None)
    current_raw = sorted((triple for triple in ranked if triple[2] == top), key=lambda triple: (triple[2], triple[0]))
    earlier_raw = sorted((triple for triple in ranked if triple[2] != top), key=lambda triple: (triple[2], triple[0]))
    stayed = {index for index, row, _ in earlier_raw if row["thread"] and row["thread"] in kept}
    current = tuple(
        LedgerLine(privacy.field(row["attempt"]), privacy.field(row["role"]), privacy.field(row["seat"]), is_open(row))
        for _, row, _ in sorted((*current_raw, *(triple for triple in earlier_raw if triple[0] in stayed)), key=lambda triple: (triple[2], triple[0])))
    folded = [triple for triple in earlier_raw if triple[0] not in stayed]
    earlier_lines = tuple(
        LedgerLine(privacy.field(row["attempt"]), privacy.field(row["role"]), privacy.field(row["seat"]), is_open(row)) for _, row, _ in folded)
    if not folded and not earlier_at:
        marked = tuple(replace(row, earlier=False) for row in rows)
        return current, AttemptFold(), marked
    lane_threads = {matched[at] for at in earlier_at} if matched else set()
    ledger_threads = {row["thread"] for _, row, _ in folded if row["thread"]}
    grouped, pending = {}, {}
    for (_, row, _), line in zip(folded, earlier_lines):
        thread = row["thread"]
        if thread and thread in lane_threads:
            if thread in pending:
                pending[thread][1] = pending[thread][1] or line.open
            else:
                pending[thread] = [line.role, line.open]
            continue
        grouped.setdefault(line.role, [0, 0])
        grouped[line.role][0] += 1
        grouped[line.role][1] += line.open
    for role, opened in pending.values():
        grouped.setdefault(role, [0, 0])
        grouped[role][0] += 1
        grouped[role][1] += opened
    for at in sorted(earlier_at):
        thread = matched[at] if matched else ""
        if thread and thread in ledger_threads:
            continue
        grouped.setdefault(rows[at].label, [0, 0])
        grouped[rows[at].label][0] += 1
        grouped[rows[at].label][1] += rows[at].status in LIVE
    seen = {}
    for _, row, rank in folded:
        if rank not in seen:
            seen[rank] = clip(privacy.field(row["attempt"]), ID_BYTES)
    ordered = sorted(seen)
    attempts = tuple(seen[rank] for rank in ordered)
    ranks = tuple(ordered)
    counts = []
    for role in ROLES:
        if role in grouped:
            n, opened = grouped.pop(role)
            counts.append(RoleCount(role, n, opened))
    for label in sorted(grouped):
        n, opened = grouped[label]
        counts.append(RoleCount(label, n, opened))
    summary = format_attempt(attempts, ranks, tuple(counts))
    fold = AttemptFold(summary, earlier_lines, attempts, ranks, tuple(counts))
    marked = tuple(replace(row, earlier=(at in earlier_at)) for at, row in enumerate(rows))
    return current, fold, marked


def spoken(line):
    parts = (
        f"attempt {clip(line.attempt, ID_BYTES)}",
        clip(line.role, LABEL_BYTES),
        clip(line.seat, SUMMARY_BYTES),
        "open" if line.open else "ended",
    )
    return " · ".join(part for part in parts if part)


def item_of(unit, privacy, key, goal_ids=(), lines=(), fold=AttemptFold()):
    word, tone = STATE_WORDS.get(unit.state, UNKNOWN_STATE)
    link, why = privacy.link(unit.pr)
    kind = unit.kind if unit.kind in KINDS else ""
    goal = unit.goal if unit.goal in goal_ids else ""
    return Item(privacy.field(unit.id) or UNNAMED, privacy.field(unit.summary), word, tone, link, unit.state not in FINISHED_STATES, why, key, kind, goal, tuple(lines), fold)


def build_page(store, t3, window, privacy):
    """Each string of the page that comes from the store or from T3 is a value privacy.field, privacy.model, or privacy.link returned, or a label short() cut from a field() value.
    The other strings are the constants of this file and numbers. No page type holds a thread id, a sub-agent id, or a path.
    """
    goals = tuple(Goal(record.id, clip(privacy.field(record.name), SUMMARY_BYTES)) for record in store.goals)
    goal_ids = {goal.id for goal in goals}

    def row(agent, depth, label):
        turn = open_turn(agent)
        return Row(depth, label, privacy.model(agent.model), status_of(agent),
                   seconds_of(agent, window), spans_of(agent, window), int(window.end - turn.start) if turn else None, 1 if stretches(agent) else 0)

    def order(entry):
        unit, agents = entry
        ends = [window.end if turn.end is None else turn.end for agent in agents for turn in stretches(agent)]
        return (unit is None, all(open_turn(agent) is None for agent in agents), -max(ends, default=window.start), unit)

    placed = assign(store, t3)
    units = {unit.id: unit for unit in store.units}
    keys = {unit.id: key for key, unit in enumerate(store.units)}
    members = {}
    for thread, assignment in placed.items():
        members.setdefault(assignment.unit, []).append(t3.agents[thread])
    members = {unit: with_parents(agents) for unit, agents in members.items()}
    groups, by_request, folded_by_unit = [], 0, {}
    for unit, agents in sorted(((unit, agents) for unit, agents in members.items() if agents), key=order):
        ordered = tree(agents)
        rows = tuple(row(agent, min(depth, 2), label_of(agent, store, unit, privacy)) for agent, depth in ordered)
        active = [agent for agent in agents if stretches(agent)]
        subagents = sum(agent.parent is not None for agent in active)
        by_request += sum(placed[agent.thread].evidence is Evidence.REQUEST for agent in active)
        if unit:
            raw = [entry for entry in store.threads if entry["dish"] == unit]
            in_flight = units[unit].state not in FINISHED_STATES
            live = any(entry.status in LIVE for entry in rows)
            current, fold, marked = partition_attempts(raw, rows, (not in_flight and not live), privacy, tuple(agent.thread for agent, _ in ordered), tuple(agent.parent for agent, _ in ordered))
            folded_by_unit[unit] = (current, fold)
            item = item_of(units[unit], privacy, keys[unit], goal_ids, current, fold)
            groups.append(Group(item, marked, len(active) - subagents, subagents))
        else:
            groups.append(Group(None, tuple(replace(entry, earlier=False) for entry in rows), len(active) - subagents, subagents))
    everyone = [row for group in groups for row in group.rows if row.stands_for]
    subagents = sum(group.subagents for group in groups)
    totals = Totals(sum(row.status is Status.RUNNING for row in everyone), len(everyone) - subagents, subagents, sum(row.status is Status.FAILED for row in everyone))
    models = [row.model for row in everyone]
    legend = tuple(sorted(((name, models.count(name)) for name in set(models)), key=lambda entry: (-entry[1], entry[0])))
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

    def folded_item(unit):
        if unit.id in folded_by_unit:
            current, fold = folded_by_unit[unit.id]
            return item_of(unit, privacy, keys[unit.id], goal_ids, current, fold)
        raw = [entry for entry in store.threads if entry["dish"] == unit.id]
        current, fold, _ = partition_attempts(raw, (), False, privacy)
        return item_of(unit, privacy, keys[unit.id], goal_ids, current, fold)

    items = tuple(folded_item(unit)
                  for unit in sorted(units.values(), key=number) if unit.state not in FINISHED_STATES)
    hidden = Hidden(other_threads=t3.other_threads, unknown_status=t3.unknown_status, by_request_name=by_request, unstarted=t3.unstarted)
    return Page(privacy.field(store.name) or "this coordinator", window, totals, legend, own, tuple(groups), items, hidden, goals=goals, flight=flight_of(items, goals))


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
MODEL_BYTES = 24
LINK_BYTES = 90
JOINED = (Status.RUNNING, Status.QUEUED, Status.FAILED, Status.UNKNOWN, Status.STOPPED, Status.DONE)


def encode(data, clean):
    """data as JSON. Each string value in it is clean(value), then written as entities."""
    def written(value):
        if isinstance(value, str):
            return entities(clean(value))
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
    models = {row.model for row in rows}
    return Row(depth, label, models.pop() if len(models) == 1 else "", Status.DONE,
               sum(row.seconds for row in rows), tuple(bars), None, agents_in(rows), all(row.earlier for row in rows))


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


def shown(item):
    fits = size(item.pr) <= LINK_BYTES
    return replace(item, id=clip(item.id, ID_BYTES), summary=clip(item.summary, SUMMARY_BYTES), pr=item.pr if fits else "", no_link=item.no_link if fits else CUT)


def narrow(row, limit=None):
    return replace(row, label=clip(row.label, LABEL_BYTES), model=clip(row.model, MODEL_BYTES), spans=row.spans if limit is None else joined(row.spans, limit))


def counted(hidden, cuts, unlisted):
    """hidden with the agents and items a cut leaves out added to its counts.
    Each of cuts is a group as it was and the rows of it that stay, or None for a group that is cut.
    unlisted is how many items in flight leave the strip.
    """
    cut, gone_items, gone_agents = 0, 0, 0
    for group, rows in cuts:
        lost = agents_in(group.rows) - agents_in(rows or ())
        # A cut group's item in flight is in page.items, so the strip lists it or cut_in_flight counts it. Any other item is counted here.
        if rows is None and group.item and not group.item.in_flight:
            gone_items, gone_agents = gone_items + 1, gone_agents + lost
        else:
            cut += lost
    return replace(hidden, cut_agents=hidden.cut_agents + cut, cut_in_flight=hidden.cut_in_flight + unlisted,
                   dropped_items=hidden.dropped_items + gone_items, dropped_agents=hidden.dropped_agents + gone_agents)


def tally(items):
    counts = {}
    for item in items:
        counts[item.state] = counts.get(item.state, 0) + 1
    ordered = [word for state, (word, _) in STATE_WORDS.items() if state not in FINISHED_STATES]
    ordered.append(UNKNOWN_STATE[0])
    seen = set(ordered)
    parts = [f"{counts[word]} {word}" for word in ordered if counts.get(word)]
    for item in items:
        word = item.state
        if word not in seen:
            parts.append(f"{counts[word]} {word}")
            seen.add(word)
    return ", ".join(parts)


def flight_of(items, goals):
    ids = {goal.id for goal in goals}
    grouped = {goal.id: [] for goal in goals}
    loose = []
    labeled = False
    for item in items:
        resolved = item.goal in ids
        if item.kind or resolved:
            labeled = True
        (grouped[item.goal] if resolved else loose).append(item)
    if not labeled:
        return None
    groups = [StripGroup(goal.name, tally(grouped[goal.id]), tuple(grouped[goal.id])) for goal in goals if grouped[goal.id]]
    if loose:
        groups.append(StripGroup(UNASSIGNED, tally(loose), tuple(loose)))
    return Flight(tuple(groups))


def cut_items(page, items):
    kept = tuple(items)
    return replace(page, items=kept, flight=flight_of(kept, page.goals))


def count_line(group):
    return f"{group.heading} · {group.count}" if group.heading else group.count


def cap_everything(page):

    def running(row):
        return row.open_seconds is not None

    groups, cuts = [], []
    stays = first(page.groups, MAX_GROUPS, lambda group: any(running(row) for row in group.rows))
    for group, stay in zip(page.groups, stays):
        keeps = first(group.rows, MAX_ROWS if stay else 0, lambda row: running(row) or row.status is Status.FAILED)
        rows = tuple(narrow(row, MAX_SPANS) for row in kept_rows(group.rows, keeps))
        cuts.append((group, rows if stay else None))
        if stay:
            groups.append(replace(group, item=shown(group.item) if group.item else None, rows=rows))
    drawn = [group.item for group in groups]
    listed = first(page.items, MAX_STRIP, lambda item: shown(item) in drawn)
    items = tuple(shown(item) for item, keep in zip(page.items, listed) if keep)
    hidden = counted(page.hidden, cuts, len(page.items) - len(items))
    coordinator = narrow(page.coordinator, COORDINATOR_SPANS) if page.coordinator else None
    page = replace(page, name=clip(page.name, NAME_BYTES), coordinator=coordinator, groups=tuple(groups), hidden=hidden, fold=page.fold + 1)
    return cut_items(page, items)


FOLDS = (fold_finished_items, fold_quiet_subagents, *(partial(keep_finished_items, limit) for limit in KEPT_ITEMS), cap_everything)


# html_render shows a frame at most 2000 CSS pixels tall. The margin covers two more lines of footer text than height() counts.
FRAME_HEIGHT = 2000
FRAME_MARGIN = 40
# The number of folds a page over its height takes before frame_cuts() cuts it.
FRAME_FOLDS = len(FOLDS) - 1

# The height of each part at 390 px wide, in CSS pixels, rounded up from the height measured in headless Chromium.
STATS_PX = 121
HEADING_PX = 41
OPEN_PX = 37
LEGEND_PX = (23, 43)
AXIS_PX = 16
GROUP_PX = 34
ROW_PX = 30
LEDGER_PX = 16
LEDGER_GAP_PX = 8
CHIP_PX = 39
STRIP_PX = 35
FOOT_PX = 12
LINE_PX = 16
# The footer's fixed paragraph is FOOT_TEXT characters with an 8-character time and no name.
# A paragraph of n characters is counted as 1 + n // FOOT_CHARS lines, and a note as 1 + n // NOTE_CHARS lines.
FOOT_TEXT = 203
FOOT_CHARS = 60
NOTE_CHARS = 64
# The reservation counts M, W, m, and w as two slots. A count line counts as 1 + n // COUNT_CHARS lines.
COUNT_CHARS = 50
# A line a count line wraps to reserves this many pixels.
COUNT_LINE_PX = 19
# The reservation counts this gap between count lines.
COUNT_GAP_PX = 8


def text_lines(characters, per_line):
    return 1 + characters // per_line


def count_slots(text):
    """Slots in one count line. M, W, m, and w take two. Every other character takes one."""
    return sum(2 if char in "MWmw" else 1 for char in text)


def flight_column_px(flight):
    """Pixels the grouped strip's goal lines reserve. None reserves 0. A flight with no groups reserves 0. Each goal reserves GROUP_PX. The goal's name and counts wrap to one or more lines, and each line reserves COUNT_LINE_PX. COUNT_GAP_PX sits between goal lines."""
    if flight is None or not flight.groups:
        return 0
    rows = [text_lines(count_slots(count_line(group)), COUNT_CHARS) for group in flight.groups]
    return COUNT_LINE_PX * sum(rows) + COUNT_GAP_PX * (len(rows) - 1) + GROUP_PX * len(rows)


def fixed_height(page, said):
    """The height of the parts frame_cuts() never cuts. They are stats, the Timeline heading, legend, axis, the coordinator's row, and the footer with the notes in said.
    A character of the name counts as two, because the name can hold any character and FOOT_CHARS is for Latin text.
    """
    lines = text_lines(FOOT_TEXT + 2 * len(page.name), FOOT_CHARS) + sum(text_lines(len(note), NOTE_CHARS) for note in said)
    return STATS_PX + HEADING_PX + LEGEND_PX[len(page.legend) > 1] + AXIS_PX + (ROW_PX if page.coordinator else 0) + FOOT_PX + LINE_PX * lines


def ledger_px(page):
    grouped = {group.item.key for group in page.groups if group.item}
    seen = set()
    total = 0
    for item in items_of(page):
        if item.key in seen:
            continue
        seen.add(item.key)
        summary = 1 if item.fold.summary else 0
        if not item.lines and not summary:
            continue
        total += LEDGER_GAP_PX + LEDGER_PX * (len(item.lines) + summary)
        if item.key not in grouped:
            total += GROUP_PX
    return total


def height(page):
    """The modelled height of the HTML page at 390 px wide.
    fixed_height, plus GROUP_PX a group and ROW_PX a shown row, plus HEADING_PX once for each of the two open lists that has a line and OPEN_PX a line in it, plus STRIP_PX once and CHIP_PX a chip when the page has chips.
    Rows flagged earlier are folded and not counted.
    A grouped strip adds flight_column_px. A page with ledger lines adds ledger_px.
    """
    rows = [row for group in page.groups for row in group.rows if not row.earlier]
    total = fixed_height(page, notes(page, items_of(page))) + GROUP_PX * len(page.groups) + ROW_PX * len(rows)
    for status in OPEN:
        lines = sum(row.open_seconds is not None and row.status is status for row in rows)
        total += HEADING_PX + OPEN_PX * lines if lines else 0
    total += flight_column_px(page.flight)
    return total + (STRIP_PX + CHIP_PX * len(page.items) if page.items else 0) + ledger_px(page)


def frame_cuts(page, limit):
    """The page cut for height, as the cut after reclaim and the cut before it.
    A page whose height() is at most limit is returned as (page, page).
    The name is clipped to NAME_BYTES. At most MAX_STRIP chips stay, those of an item with a group on the page first.
    Rows are tried in three passes, and each row whose parts fit what is left of limit stays. Rows flagged earlier always stay and cost nothing, since height() does not count them.
    The first pass tries every row with a Running now or Queued line, the second every failed row, the third every other row.
    Each pass tries its rows in order of turn, then of group in page order. A row's turn is the number of rows of its group that are tried before it in any pass.
    A group with no row left is cut with its heading. counted() counts the agents and items left out.
    Unused worst case counted note room is reclaimed, except the attempt line floor, and rejected rows are retried in the same order.
    The cut before reclaim has its attempt lines trimmed with fit_lines().
    The reclaimed cut is returned with the cut before it as (final, first_cut) when its height stays at most limit, and as (None, first_cut) when it does not.
    """
    if height(page) <= limit:
        return page, page
    page = replace(page, name=clip(page.name, NAME_BYTES))
    # Room for the notes a cut can add, and for the attempt-line note when the page has attempt lines, so the result needs no second pass.
    has_lines = any(item.lines or item.fold.summary or item.fold.lines for item in items_of(page))
    worst = replace(page.hidden, dropped_items=page.hidden.dropped_items or 1, cut_agents=page.hidden.cut_agents or 1, cut_in_flight=page.hidden.cut_in_flight or 1,
                    cut_lines=(page.hidden.cut_lines or 1) if has_lines else page.hidden.cut_lines)
    left = limit - fixed_height(page, notes(replace(page, hidden=worst), items_of(page)))
    drawn = [group.item for group in page.groups]
    listed = first(page.items, MAX_STRIP, lambda item: item in drawn)
    items = tuple(item for item, keep in zip(page.items, listed) if keep)
    flight = flight_of(items, page.goals)
    left -= (STRIP_PX + CHIP_PX * len(items) + flight_column_px(flight)) if items else 0
    left -= ledger_px(page)

    def tier(row):
        return 0 if row.open_seconds is not None else 1 if row.status is Status.FAILED else 2

    pieces = []
    kept, headed, lists = set(), set(), set()
    for at, group in enumerate(page.groups):
        for index, row in enumerate(group.rows):
            if row.earlier:
                kept.add((at, index))
                headed.add(at)
    left -= GROUP_PX * len(headed)
    for at, group in enumerate(page.groups):
        visible = [index for index, row in enumerate(group.rows) if not row.earlier]
        ranked = sorted(visible, key=lambda index: (tier(group.rows[index]), index))
        pieces += [(tier(group.rows[index]), turn, at, index) for turn, index in enumerate(ranked)]
    for _, _, at, index in sorted(pieces):
        row = page.groups[at].rows[index]
        opened = row.open_seconds is not None
        cost = ROW_PX + (at not in headed) * GROUP_PX + opened * (OPEN_PX + (row.status not in lists) * HEADING_PX)
        if cost <= left:
            left -= cost
            kept.add((at, index))
            headed.add(at)
            if opened:
                lists.add(row.status)

    def build(kept):
        groups, cuts = [], []
        for at, group in enumerate(page.groups):
            rows = tuple(kept_rows(group.rows, [(at, index) in kept for index in range(len(group.rows))]))
            cuts.append((group, rows or None))
            if rows:
                groups.append(replace(group, rows=rows))
        return groups, cuts

    groups, cuts = build(kept)
    first_cut = cut_items(replace(page, groups=tuple(groups), hidden=counted(page.hidden, cuts, len(page.items) - len(items))), items)
    first_cut = fit_lines(first_cut, limit)
    # Reclaim unused worst case counted note room, except the attempt line floor that fit() may still add. The retried result is measured before it is kept.
    actual = counted(page.hidden, cuts, len(page.items) - len(items))
    held = replace(actual, cut_lines=worst.cut_lines)
    reclaimed = fixed_height(page, notes(replace(page, hidden=worst), items_of(page))) - fixed_height(page, notes(replace(page, hidden=held), items_of(page)))
    if reclaimed > 0:
        left += reclaimed
        for _, _, at, index in sorted(pieces):
            if (at, index) in kept:
                continue
            row = page.groups[at].rows[index]
            opened = row.open_seconds is not None
            cost = ROW_PX + (at not in headed) * GROUP_PX + opened * (OPEN_PX + (row.status not in lists) * HEADING_PX)
            if cost <= left:
                left -= cost
                kept.add((at, index))
                headed.add(at)
                if opened:
                    lists.add(row.status)
        groups, cuts = build(kept)
        final = cut_items(replace(page, groups=tuple(groups), hidden=counted(page.hidden, cuts, len(page.items) - len(items))), items)
        if height(final) <= limit:
            return final, first_cut
    return None, first_cut


def framed(page, limit):
    """The cut frame_cuts() makes for height. A page whose height() is at most limit is returned as it is.
    The cut after reclaim is returned when it is at most limit, and the cut before reclaim, with its attempt lines trimmed with fit_lines(), is returned otherwise.
    """
    cut, fallback = frame_cuts(page, limit)
    return cut if cut is not None else fallback


def line_count(page):
    seen = set()
    total = 0
    for item in items_of(page):
        if item.key in seen:
            continue
        seen.add(item.key)
        total += len(item.lines) + len(item.fold.lines)
    return total


def limit_ledger(page, keep):
    """The page with the first keep attempt lines in items_of order on every copy of each work item. cut_lines counts the lines the prefix leaves off.
    items_of order is page.items first, then each group item not in page.items. Each work item key appears once, current lines before fold payload within a key. Within a key, payload drops before current lines.
    """
    seen, remaining, removed = set(), keep, 0
    kept_current, kept_payload = {}, {}
    for item in items_of(page):
        if item.key in seen:
            continue
        seen.add(item.key)
        take_current = min(len(item.lines), remaining)
        remaining -= take_current
        take_payload = min(len(item.fold.lines), remaining)
        remaining -= take_payload
        kept_current[item.key] = item.lines[:take_current]
        kept_payload[item.key] = item.fold.lines[:take_payload]
        removed += (len(item.lines) - take_current) + (len(item.fold.lines) - take_payload)

    def trimmed(item):
        if item is None or item.key not in kept_current:
            return item
        fold = replace(item.fold, lines=kept_payload[item.key]) if item.fold.summary else item.fold
        return replace(item, lines=kept_current[item.key], fold=fold)

    groups = tuple(replace(group, item=trimmed(group.item)) if group.item is not None else group for group in page.groups)
    items = tuple(trimmed(item) for item in page.items)
    hidden = replace(page.hidden, cut_lines=page.hidden.cut_lines + removed)
    return cut_items(replace(page, groups=groups, items=items, hidden=hidden), items)


def newest_ledger(page, keep):
    """The page with keep attempt lines left, the newest lines of each work item, on every copy of that item. cut_lines counts the lines the suffix leaves off.
    items_of order is page.items first, then each group item not in page.items. Each work item key appears once, and its fold payload holds its older attempts, so its payload lines drop before its current lines.
    A drop takes the oldest attempt line of each work item that has one, in items_of order, then the second oldest of each work item that still has one, and so on, so every work item keeps a suffix of its current lines and a suffix of its fold payload's lines.
    """
    listed, seen = [], set()
    for item in items_of(page):
        if item.key not in seen:
            seen.add(item.key)
            listed.append(item)
    total = sum(len(item.lines) + len(item.fold.lines) for item in listed)
    count = max(total - keep, 0)

    def dropped(number):
        taken, left, position = {}, number, 0
        while left:
            for item in listed:
                if len(item.lines) + len(item.fold.lines) > position:
                    taken[item.key] = position + 1
                    left -= 1
                    if not left:
                        break
            position += 1
        return taken

    taken = dropped(count)

    def trimmed(item):
        if item is None or item.key not in taken:
            return item
        cut, payload = taken[item.key], item.fold.lines
        lines = item.lines[max(cut - len(payload), 0):]
        fold = replace(item.fold, lines=payload[min(cut, len(payload)):]) if item.fold.summary else item.fold
        return replace(item, lines=lines, fold=fold)

    groups = tuple(replace(group, item=trimmed(group.item)) if group.item is not None else group for group in page.groups)
    items = tuple(trimmed(item) for item in page.items)
    hidden = replace(page.hidden, cut_lines=page.hidden.cut_lines + count)
    return cut_items(replace(page, groups=groups, items=items, hidden=hidden), items)


def fit_lines(page, limit):
    """The page with the oldest current attempt lines dropped until its height() is at most limit.
    A page whose height() is at most limit, or that has no current attempt line, is returned as it is.
    A drop takes the oldest current line of every work item, in items_of order, then the second oldest of every work item, and so on, so each work item keeps its newest lines. Fold payload stays. cut_lines grows by the lines dropped.
    The fewest drops whose height() is at most limit is kept, and every current line is dropped when none fits.
    """
    listed, seen = [], set()
    for item in items_of(page):
        if item.key not in seen:
            seen.add(item.key)
            listed.append(item)
    total = sum(len(item.lines) for item in listed)
    if total == 0 or height(page) <= limit:
        return page

    def dropped(count):
        taken, left, position = {}, count, 0
        while left:
            for item in listed:
                if len(item.lines) > position:
                    taken[item.key] = position + 1
                    left -= 1
                    if not left:
                        break
            position += 1

        def trimmed(item):
            if item is None or item.key not in taken:
                return item
            return replace(item, lines=item.lines[taken[item.key]:])

        groups = tuple(replace(group, item=trimmed(group.item)) if group.item is not None else group for group in page.groups)
        items = tuple(trimmed(item) for item in page.items)
        hidden = replace(page.hidden, cut_lines=page.hidden.cut_lines + count)
        return cut_items(replace(page, groups=groups, items=items, hidden=hidden), items)

    for count in range(1, total):
        trimmed = dropped(count)
        if height(trimmed) <= limit:
            return trimmed
    return dropped(total)


def briefed(page):
    """The page with every fold summary in bounded count form. Counts and state stay exact, and every line stays."""
    def trimmed(item):
        if item is None or not item.fold.summary:
            return item
        return replace(item, fold=replace(item.fold, summary=brief_attempt(item.fold.ranks, item.fold.counts)))

    groups = tuple(replace(group, item=trimmed(group.item)) if group.item is not None else group for group in page.groups)
    items = tuple(trimmed(item) for item in page.items)
    return cut_items(replace(page, groups=groups, items=items), items)


def within_budget(page, budget, clean, render, brief=False, ledger=limit_ledger):
    """The page, its render, and whether that render's UTF-8 length is at most budget.
    A page that already fits, or a page with no attempt line, is returned as it is.
    Any other page keeps the most attempt lines whose render fits, searched by count through ledger(page, count). limit_ledger(page, keep) keeps the first keep attempt lines in items_of order, and newest_ledger(page, keep) keeps each work item's newest lines. When no count fits, the summaries are bounded to their attempt counts and the search runs again, so a long enumeration of attempts that are not one contiguous range no longer adds bytes beyond its count. Counts and state stay exact, and no lane is dropped. When even the briefed zero line form is over budget, that form is returned as it is.
    """
    text = render(page, clean)
    count = line_count(page)
    fits = len(text.encode()) <= budget
    if fits or count == 0:
        return page, text, fits
    cache = {count: (page, text, False)}

    def at(keep):
        if keep not in cache:
            trimmed = ledger(page, keep)
            body = render(trimmed, clean)
            cache[keep] = (trimmed, body, len(body.encode()) <= budget)
        return cache[keep]

    low, high, best = 0, count - 1, None
    while low <= high:
        mid = (low + high) // 2
        if at(mid)[2]:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    if best is None:
        if brief:
            return at(0)
        return within_budget(briefed(page), budget, clean, render, True, ledger)
    for _ in range(count + 1):
        chosen = at(best)[2]
        nxt = best + 1
        if chosen and (nxt >= count or not at(nxt)[2]):
            break
        if not chosen:
            if best == 0:
                break
            best -= 1
        else:
            best += 1
    return at(best)


def fit(page, budget, clean, frame=None):
    """The page and its document after the first fold that leaves it at most budget bytes, or after the last fold.
    Each fold's page is measured with within_budget() with ledger=newest_ledger(), which keeps the most attempt lines whose document fits, a suffix of each work item's lines, and, when no count fits, bounds its fold summaries to their counts.
    With frame, a page whose height() is over frame takes the next fold too until FRAME_FOLDS folds are applied. After that frame_cuts() cuts it, and the first cut whose height() after within_budget() is at most frame and whose document is at most budget bytes stays, or on the last fold the first cut whose height() after within_budget() is at most frame. An over-budget page at a fold that reaches FRAME_FOLDS tries the trimmed page's frame cuts before the next fold. If those cuts fail, the trimmed page fits the frame, and the fold page is not the trimmed page, it then tries the fold page's frame cuts. The first cut whose document is at most budget bytes and whose height() is at most frame is returned. An over-budget cut keeps the most attempt lines whose document fits with newest_ledger(), and, when no count fits, bounds its fold summaries to their counts and keeps the most attempt lines of that page whose document fits, or no attempt line when no count of it fits. When neither cut stays before the last fold the next fold runs, and when no cut stays on the last fold that fold's own page and document are returned.
    """
    for done, fold in enumerate((None, *FOLDS)):
        page = fold(page) if fold else page
        trimmed, document, fits = within_budget(page, budget, clean, render_html, ledger=newest_ledger)
        last = done == len(FOLDS)
        if not fits and not last:
            if done >= FRAME_FOLDS and frame is not None:
                for cut in frame_cuts(trimmed, frame):
                    if cut is None:
                        continue
                    body, rendered, cut_fits = within_budget(cut, budget, clean, render_html, ledger=newest_ledger)
                    if cut_fits and height(body) <= frame:
                        return body, rendered
                if page is not trimmed and height(trimmed) <= frame:
                    for cut in frame_cuts(page, frame):
                        if cut is None:
                            continue
                        body, rendered, cut_fits = within_budget(cut, budget, clean, render_html, ledger=newest_ledger)
                        if cut_fits and height(body) <= frame:
                            return body, rendered
            continue
        page = trimmed
        if frame is None or height(page) <= frame:
            break
        if done >= FRAME_FOLDS:
            accepted = False
            for cut in frame_cuts(page, frame):
                if cut is None:
                    continue
                body, rendered, fits = within_budget(cut, budget, clean, render_html, ledger=newest_ledger)
                if (fits or last) and height(body) <= frame:
                    page, document, accepted = body, rendered, True
                    break
            if accepted:
                break
    return page, document


def shortened(page):
    """The page the plain form prints when it is over its budget. Every row and item stays.
    The name, each item's id and summary, and each group row's label and model are clipped as cap_everything clips them, and each link it would leave out is left out.
    """
    groups = tuple(replace(group, item=shown(group.item) if group.item else None, rows=tuple(narrow(row) for row in group.rows)) for group in page.groups)
    return cut_items(replace(page, name=clip(page.name, NAME_BYTES), groups=groups), (shown(item) for item in page.items))


def fit_text(page, budget, clean):
    text = render_text(page, clean)
    if len(text.encode()) <= budget:
        return text
    _, text, _ = within_budget(shortened(page), budget, clean, render_text)
    return text


def say(number, one, several, **values):
    return (one if number == 1 else several).format(n=number, **values)


def items_of(page):
    return [*page.items, *dict.fromkeys(group.item for group in page.groups if group.item and group.item not in page.items)]


def notes(page, listed):
    """The notes of a page. listed holds the work items the form prints, so a link is counted only for an item the reader sees."""
    rows = [row for group in page.groups for row in group.rows]
    links = [item.no_link for item in listed]
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
    if hidden.cut_lines:
        lines.append(say(hidden.cut_lines, "1 attempt line is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
                         "{n} attempt lines are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."))
    if hidden.cut_in_flight:
        lines.append(say(hidden.cut_in_flight, "1 more work item in flight is not listed.", "{n} more work items in flight are not listed."))
    if CUT in links:
        lines.append(say(links.count(CUT), "1 pull request link is not shown, because the page is at its size limit. Use a larger --max-bytes to see more.",
                         "{n} pull request links are not shown, because the page is at its size limit. Use a larger --max-bytes to see more."))
    if REFUSED in links:
        lines.append(say(links.count(REFUSED), "1 pull request link is not shown. It is not an https://host/owner/repository/pull/number address, or it holds text this page removes.",
                         "{n} pull request links are not shown. Each is not an https://host/owner/repository/pull/number address, or it holds text this page removes."))
    if hidden.unstarted:
        lines.append(say(hidden.unstarted, "T3 lists 1 sub-agent with no thread or no start time under a thread this page read. It is not shown.",
                         "T3 lists {n} sub-agents with no thread or no start time under threads this page read. They are not shown."))
    if hidden.unknown_status:
        lines.append(say(hidden.unknown_status, "T3 gave 1 status this tool reads as unknown.", "T3 gave {n} statuses this tool reads as unknown."))
    if hidden.by_request_name:
        lines.append(say(hidden.by_request_name, "1 agent is grouped by the name of the request that started it.",
                         "{n} agents are grouped by the name of the request that started them."))
    if hidden.other_threads:
        lines.append(say(hidden.other_threads, "1 other thread ran in this coordinator's T3 project outside this coordinator.",
                         "{n} other threads ran in this coordinator's T3 project outside this coordinator."))
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
    .now div,.strip>*,.grp,.l{display:flex;gap:8px;align-items:baseline;min-width:0}
    .now{display:flex;flex-wrap:wrap;gap:6px}
    .mark{width:8px;height:8px;border-radius:50%;border:1px solid;box-sizing:border-box;align-self:center;flex:none}
    .pulse{border:0;background:var(--success)}
    .sum,.l span,.l small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
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
    .l small{flex:none;max-width:50%}
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
    .nb,.one{white-space:nowrap}
    .one{overflow:hidden;text-overflow:ellipsis}
    @media(max-width:520px){
    .tl{--lab:128px}
    .stats{grid-template-columns:repeat(2,1fr)}
    .axis i:nth-child(odd){display:none}
    .row{position:relative;height:30px;align-items:start}
    .l small{position:absolute;left:0;right:0;bottom:0;max-width:none;line-height:13px}
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
    const link = (p, t, u) => {
      const s = u.startsWith('https://'), n = add(p, s ? 'a' : 'span', '', t);
      if (s) {
        n.href = u;
        n.target = '_blank';
        n.rel = 'noopener';
      }
      return n;
    };
    const chip = (p, i) => {
      const it = D.I[i], b = add(p, 'div');
      if (it[6]) add(b, 'b', 'chip', it[6]);
      if (D.G.some((g) => g[0] === i) || (D.L || []).concat(D.E || []).some(([a]) => a === i)) {
        add(b, 'a', 'one', it[0]).href = '#g' + i;
      } else {
        const n = link(b, '', it[4]);
        add(n, 'b', 'one', it[0]);
        add(n, 'span', 'sum', it[1]);
      }
      add(b, 'b', 'chip ' + it[3], it[2]);
      return b;
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
          if (r[8] != null) open[r[4]].push([index < 0 ? '' : D.I[index][0], r[1], D.M[r[2]], r[0] ? above[r[0] - 1] : '', r[8]]);
        }
      }
      ['Running now', 'Queued'].forEach((heading, i) => {
        if (open[i].length) {
          add(root, 'h2', '', heading);
          const list = add(root, 'div', 'now');
          for (const [item, label, , parent, elapsed] of open[i]) {
            const line = add(list, 'div');
            add(line, 'i', i ? 'mark' : 'mark pulse');
            if (item) add(line, 'b', 'one', item);
            add(line, 'b', 'one', label);
            if (parent) {
              add(line, 'span', '', ' · under ');
              add(line, 'span', '', parent);
            }
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
        const row = add(lanes, 'div', 'row ' + cls), name = add(row, 'div', 'l d' + depth), track = add(row, 'div', 't');
        add(name, 'span', '', label).title = label;
        add(name, 'small', '', D.M[model]).title = D.M[model] || '';
        row.title = D.S[status] + ' · ' + time;
        for (let i = 0; i < spans.length; i += 3) {
          const bar = add(track, 'i', 'b ' + kinds[spans[i + 2]]);
          bar.style.left = 'min(' + spans[i] / 10 + '%,100% - 3px)';
          bar.style.width = spans[i + 1] / 10 + '%';
        }
      };
      if (D.k) lane('co', 0, 'coordinator', ...D.k);
      for (const [index, rows] of D.G) {
        const head = add(lanes, 'div', 'grp'), item = D.I[index];
        if (index >= 0) head.id = 'g' + index;
        if (item) {
          link(head, item[0], item[4]).className = 'one';
          add(head, 'span', 'sum', item[1]);
          add(head, 'b', 'chip ' + item[3], item[2]);
        } else add(head, 'span', '', 'Not tied to a work item');
        for (const r of rows) lane('p' + r[3], r[0], r[1], r[2], r[4], r[5], r[6]);
      }
      const strip = D.I.map((it, i) => [i, it]).filter(([, it]) => it[5]);
      if (strip.length) {
        add(root, 'h2', '', 'Work items in flight');
        const box = add(root, 'div', 'strip');
        for (const [i] of strip) chip(box, i);
      }
      const foot = add(root, 'div', 'foot');
      add(foot, 'span', '', 'As of ' + clock(start + length) + ' for ');
      add(foot, 'span', '', D.c);
      add(foot, 'span', '', '. Bars show turn or delegation intervals and may join across gaps. Striped bars include running intervals. Outlined bars include queued intervals. Faded bars include stopped intervals.');
      for (const note of D.N) {
        const line = add(foot, 'div');
        note.split(/(--[a-z-]+)/).forEach((part, i) => add(line, 'span', i % 2 ? 'nb' : '', part));
      }
    }
""")

STRIP_CSS = ".strip>.grp{flex:1 0 100%;border:0;background:transparent;border-radius:0;padding:2px 0}"
# `.ldg` has a 100% flex basis, so `.tl>.grp` wraps it onto a line under the id, title, and state chip.
LEDGER_CSS = ".tl>.grp{flex-wrap:wrap}.ldg{flex:1 0 100%;margin:0;padding:0;font-weight:400}.ldg span{display:block;height:16px;line-height:16px;overflow:hidden;white-space:nowrap;font-size:11.5px;color:var(--muted-foreground)}"
ATTEMPT_CSS = ".row.earlier{display:none}.ldg details{margin:0;padding:0}.ldg summary{display:block;height:16px;line-height:16px;overflow:hidden;white-space:nowrap;font-size:11.5px;color:var(--muted-foreground);cursor:pointer}"
LEDGER_HOOK = """
    if (D && D.v === 1 && ((D.L && D.L.length) || (D.E && D.E.length))) {
      const children = (node) => node.kids || [...node.children];
      const hasEarlier = (node) => ((node.className || '').split(' ').indexOf('earlier') >= 0);
      const addEarlier = (node) => { if (!hasEarlier(node)) node.className = (node.className ? node.className + ' ' : '') + 'earlier'; };
      const dropEarlier = (node) => { node.className = (node.className || '').split(' ').filter((c) => c && c !== 'earlier').join(' '); };
      const attemptMap = (typeof window !== 'undefined' && window.attemptState) || {};
      if (typeof window !== 'undefined' && !window.attemptState) window.attemptState = attemptMap;
      const byCurrent = new Map((D.L || []).map(([index, lines]) => [index, lines]));
      const byFold = new Map((D.E || []).map(([index, summary, payload]) => [index, [summary, payload]]));
      const byEarlier = new Map((D.R || []).map(([gpos, idxs]) => [gpos, idxs]));
      const lanes = root.querySelector('.tl');
      const groups = D.G || [];
      const heads = children(lanes).filter((node) => (node.className || '').split(' ').includes('grp'));
      const domGroups = [];
      let cur = null;
      for (const node of children(lanes)) {
        const cls = (node.className || '').split(' ');
        if (cls.indexOf('grp') >= 0) { cur = {head: node, rows: []}; domGroups.push(cur); }
        else if (cur && cls.indexOf('row') >= 0) cur.rows.push(node);
      }
      const attach = (head, gpos, itemIndex) => {
        const current = byCurrent.get(itemIndex) || [];
        const folded = byFold.get(itemIndex);
        if (!current.length && !folded) return;
        const block = document.createElement('div');
        block.className = 'ldg';
        head.appendChild(block);
        const itemId = (D.I[itemIndex] || [])[0] || String(itemIndex);
        let details = null;
        if (folded) {
          const summary = folded[0], payload = folded[1] || [];
          details = document.createElement('details');
          const sum = document.createElement('summary');
          sum.textContent = summary;
          details.appendChild(sum);
          for (const phrase of payload) {
            const span = document.createElement('span');
            span.textContent = phrase;
            details.appendChild(span);
          }
          block.appendChild(details);
          details.setAttribute('data-item', itemId);
          const open = !!attemptMap[itemId];
          details.open = open;
          const rows = (gpos != null && domGroups[gpos]) ? domGroups[gpos].rows : [];
          const idxs = (gpos != null && byEarlier.get(gpos)) || [];
          const earlierRows = idxs.map((ri) => rows[ri]).filter(Boolean);
          if (open) { for (const r of earlierRows) dropEarlier(r); }
          else { for (const r of earlierRows) addEarlier(r); }
          details.ontoggle = () => {
            const isOpen = !!details.open;
            attemptMap[itemId] = isOpen;
            for (const r of earlierRows) { if (isOpen) dropEarlier(r); else addEarlier(r); }
            if (typeof window !== 'undefined' && window.refreshAttempt) window.refreshAttempt(itemId);
          };
        }
        for (const phrase of current) {
          const span = document.createElement('span');
          span.textContent = phrase;
          block.appendChild(span);
        }
      };
      const placed = new Set();
      if (heads.length === groups.length) {
        groups.forEach((group, at) => {
          if (byCurrent.has(group[0]) || byFold.has(group[0])) { attach(heads[at], at, group[0]); placed.add(group[0]); }
        });
      }
      const seen = new Set(placed);
      for (const index of [...byCurrent.keys(), ...byFold.keys()]) {
        if (seen.has(index)) continue;
        seen.add(index);
        const item = D.I[index];
        const head = add(lanes, 'div', 'grp');
        head.id = 'g' + index;
        link(head, item[0], item[4]).className = 'one';
        add(head, 'span', 'sum', item[1]);
        add(head, 'b', 'chip ' + item[3], item[2]);
        attach(head, null, index);
      }
    }
"""
STRIP_HOOK = squeezed("""
    if (D && D.v === 1 && D.F && D.F.length) {
      const box = add(root, 'div', 'strip');
      for (const [line, , indexes] of D.F) {
        add(box, 'div', 'grp', line);
        for (const index of indexes) chip(box, index);
      }
      const foot = root.querySelector('.foot'), title = add(root, 'h2', '', 'Work items in flight');
      root.insertBefore(title, foot);
      root.insertBefore(box, foot);
    }
""")


def wire(page):
    models = []

    def model(name):
        if name and name not in models:
            models.append(name)
        return models.index(name) if name else -1

    def bars(spans):
        return [number for span in spans for number in (span.x, span.w, WIRE_BARS.index(span.status))]

    def line(row):
        tail = [row.stands_for, dur(row.open_seconds)] if row.open_seconds is not None else [row.stands_for] if row.stands_for != 1 else []
        return [row.depth, row.label, model(row.model), row.color_slot, WIRE_STATUS.index(row.status), dur(row.seconds), bars(row.spans), *tail]

    items = items_of(page)
    own, totals = page.coordinator, page.totals
    grouped = page.flight is not None

    def cells(item):
        row = [item.id, item.summary, item.state, item.tone, item.pr, 0 if grouped else int(item in page.items)]
        if grouped:
            row.append(item.kind)
        return row

    data = {
        "v": WIRE_VERSION,
        "c": page.name,
        "w": [int(page.window.start), round(page.window.end - page.window.start), span(page.window)],
        "n": [totals.running, totals.agents, totals.subagents, totals.failed],
        "S": [status.value for status in WIRE_STATUS],
        "P": [[name, model_slot(name), agents] for name, agents in page.legend],
        "k": own and [model(own.model), WIRE_STATUS.index(own.status), dur(own.seconds), bars(own.spans)],
        "I": [cells(item) for item in items],
        "G": [[items.index(group.item) if group.item else -1, [line(row) for row in group.rows]] for group in page.groups],
        "M": models,
        "N": notes(page, items),
    }
    if grouped:
        places = {id(item): index for index, item in enumerate(items)}
        data["F"] = [[count_line(group), group.heading, [places[id(item)] for item in group.items]] for group in page.flight.groups]
    if any(item.lines for item in items):
        data["L"] = [[index, [spoken(line) for line in item.lines]] for index, item in enumerate(items) if item.lines]
    if any(item.fold.summary for item in items):
        data["E"] = [[index, item.fold.summary, [spoken(line) for line in item.fold.lines]] for index, item in enumerate(items) if item.fold.summary]
    earlier = [[at, [index for index, row in enumerate(group.rows) if row.earlier]] for at, group in enumerate(page.groups) if any(row.earlier for row in group.rows)]
    if earlier:
        data["R"] = earlier
    return data


def checksum(text):
    value, units = 2166136261, text.encode("utf-16-le")
    for at in range(0, len(units), 2):
        value = ((value ^ int.from_bytes(units[at:at + 2], "little")) * 16777619) & 0xFFFFFFFF
    return value


def model_slot(name: str) -> int:
    """MIXED_SLOT when empty, OTHER_SLOT for OTHER_MODEL, else checksum slot 1 to MODEL_SLOTS."""
    if not name:
        return MIXED_SLOT
    if name == OTHER_MODEL:
        return OTHER_SLOT
    return checksum(name) % MODEL_SLOTS + 1


def render_html(page, clean):
    """The document. The only strings in it that are not constants of this file are in the data element, and encode() passes each of those to clean."""
    data = encode(wire(page), clean)
    style = STYLE if page.flight is None else STYLE + STRIP_CSS
    script = RENDERER if page.flight is None else RENDERER + STRIP_HOOK
    if any(item.lines or item.fold.summary for item in items_of(page)):
        style += LEDGER_CSS
        script += squeezed(LEDGER_HOOK)
    if any(item.fold.summary for item in items_of(page)):
        style += ATTEMPT_CSS
    return (
        "<!doctype html><meta charset=utf-8><meta name=viewport content=\"width=device-width,initial-scale=1\">"
        f"<title>Agent activity</title><style>{style}</style><div id=o></div>"
        f"<script type=application/json id=d>{data}</script><script>const H={checksum(data)};{script}</script>")


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


def render_text(page, clean):
    """The plain lines. Each line is joined from its parts first and then passed to clean, whole."""
    def capped(lines, limit, noun):
        return lines[:limit] + ([f"  and {count(len(lines) - limit, 'more ' + noun)}"] if len(lines) > limit else [])

    def line(*parts):
        return "  " + "   ".join(part for part in parts if part)

    def worked(rows):
        return f"{dur(sum(row.seconds for row in rows))} at work"

    running, queued, failed = [], [], []
    for group in page.groups:
        above = {}
        for row in group.rows:
            if row.earlier:
                above[row.depth] = row.label
                continue
            above[row.depth] = row.label
            name = f"{group.item.id} {row.label}" if group.item else row.label
            if row.open_seconds is not None:
                waits = row.status is Status.QUEUED
                (queued if waits else running).append(line(name, f"{'queued' if waits else 'running'} for {dur(row.open_seconds)}",
                                                           f"under {above[row.depth - 1]}" if row.depth else ""))
            if row.status is Status.FAILED:
                failed.append(line(name, row.model, worked([row])))
    drawn = {group.item: f"{people(group.agents, group.subagents)}, {worked(group.rows)}" for group in page.groups if group.item}
    listed = [*drawn, *(item for item in page.items if item not in drawn)][:TEXT_ITEMS]
    more = len(drawn) + sum(item not in drawn for item in page.items) - len(listed)
    loose = [line(UNGROUPED, f"{people(group.agents, group.subagents)}, {worked(group.rows)}") for group in page.groups if not group.item]

    def flat_block(item):
        head = line(item.id, item.state, item.summary, drawn.get(item, "no activity in this window"), item.pr)
        summary = ["    " + item.fold.summary] if item.fold.summary else []
        return [head, *summary, *("    " + spoken(entry) for entry in item.lines)]

    def grouped_block(item):
        head = "  " + line(item.kind, item.id, item.state, item.summary, drawn.get(item, "no activity in this window"), item.pr)
        summary = ["      " + item.fold.summary] if item.fold.summary else []
        return [head, *summary, *("      " + spoken(entry) for entry in item.lines)]

    flat = [part for item in listed for part in flat_block(item)]
    text_flight = flight_of(listed, page.goals)
    if text_flight is None:
        items = flat
    else:
        items = []
        for group in text_flight.groups:
            items.append(line(count_line(group)))
            for item in group.items:
                items.extend(grouped_block(item))
    totals = page.totals
    lines = [
        f"Agent activity for {page.name}, last {span(page.window)}",
        f"{totals.running} running now, {count(totals.agents, 'agent')}, {count(totals.subagents, 'sub-agent')}, {totals.failed} failed",
    ]
    for heading, body in (
            ("Models", [line(name, str(agents)) for name, agents in page.legend]),
            ("Running now", capped(running, TEXT_RUNNING, "running agent")),
            ("Queued", capped(queued, TEXT_QUEUED, "queued agent")),
            ("Work items", items + ([f"  and {count(more, 'more work item')}"] if more else []) + loose),
            ("Failed", capped(failed, TEXT_FAILED, "failed agent")),
            ("Notes", ["  " + note for note in notes(page, listed)])):
        if body:
            lines += [heading, *body]
    return "\n".join(clean(line) for line in lines)


LIVE_CSS = joined_lines("""
    .livebar{display:flex;gap:8px;align-items:center;margin-bottom:6px;font:11px var(--font-sans);color:var(--muted-foreground)}
    .livebar button{font:inherit;color:var(--foreground);background:var(--muted);border:1px solid var(--border);border-radius:4px;padding:1px 8px;cursor:pointer}
""")

LIVE_FOLD = squeezed("""
    const openState = {};
    const attemptState = window.attemptState || (window.attemptState = {});
    let appliers = [];
    const setAll = (open) => { for (const a of appliers) { openState[a.fold] = open; a.apply(); } };
    function fold() {
      const lanes = document.querySelector('#o .tl');
      if (!lanes) return;
      appliers = [];
      const groups = [];
      let g = null;
      for (const n of [...lanes.children]) {
        if (n.classList.contains('grp')) { g = {head: n, rows: []}; groups.push(g); }
        else if (g && n.classList.contains('row')) g.rows.push(n);
      }
      const seen = {};
      for (const {head, rows} of groups) {
        const id = (head.querySelector('.one') || {}).textContent || head.textContent;
        const block = head.querySelector('.ldg details');
        const key = (block && block.getAttribute('data-item')) || id;
        const nth = (seen[id] = (seen[id] || 0) + 1);
        const fold = id + '\\0' + nth;
        const state = (head.querySelector(':scope>b.chip') || {}).textContent || '';
        const byDefault = !/merged|dropped|done/.test(state);
        const caret = document.createElement('span');
        caret.style.cssText = 'flex:none;width:1em;color:var(--muted-foreground)';
        head.prepend(caret);
        head.style.cursor = 'pointer';
        const apply = () => {
          const open = fold in openState ? openState[fold] : byDefault;
          caret.textContent = open ? '\u25BE' : '\u25B8';
          const attemptOpen = !!attemptState[key];
          if (block) block.open = attemptOpen;
          for (const r of rows) { const earlier = r.classList.contains('earlier'); r.style.display = !open ? 'none' : (earlier && !attemptOpen ? 'none' : ''); }
          const l = head.querySelector('.ldg');
          if (l) l.style.display = open ? '' : 'none';
        };
        head.onclick = (e) => { if (e.target.closest('a,summary,details')) return; openState[fold] = !(fold in openState ? openState[fold] : byDefault); apply(); };
        apply();
        appliers.push({fold, key, apply});
      }
      window.refreshAttempt = (itemId) => { for (const a of appliers) { if (a.key === itemId) a.apply(); } };
    }
""")

LIVE_SCRIPT = squeezed("""
    const status = (t) => { document.getElementById('live').textContent = t; };
    async function tick() {
      try {
        const r = await fetch(LIVE_URL, {cache: 'no-store'});
        if (!r.ok) throw new Error('server answered ' + r.status);
        const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
        const scripts = [...doc.querySelectorAll('script:not([type])')];
        document.getElementById('s').textContent = doc.querySelector('style').textContent + '.tl>.grp{flex-wrap:wrap}';
        document.getElementById('d').textContent = doc.getElementById('d').textContent;
        document.getElementById('o').replaceChildren();
        for (const s of scripts) new Function(s.textContent)();
        fold();
        status('Live. Updates every 30 seconds. Last update ' + new Date().toLocaleTimeString() + '.');
      } catch (e) { status('Live updates paused: the local activity server is not reachable (' + e.message + '). Last good view stays below.'); }
    }
    document.getElementById('ex').onclick = () => setAll(true);
    document.getElementById('co').onclick = () => setAll(false);
    tick();
    setInterval(tick, LIVE_EVERY);
""")


def origin_of(value):
    """The origin for --live-page. A value with whitespace or backslash or ? or # is refused. A port that does not parse is refused. The rest must parse as http:// or https:// with a host and no userinfo, path, query, or fragment."""
    bad = "--live-page takes an http:// or https:// origin with no userinfo, path, query, or fragment; pass one such as https://home.example.ts.net:48731, or omit it to use http://127.0.0.1:48731"
    if any(char.isspace() for char in value) or "\\" in value or "?" in value or "#" in value:
        raise ActivityError(bad)
    try:
        parts = urlsplit(value)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise ActivityError(bad) from None
    if parts.netloc.endswith(":"):
        raise ActivityError(bad)
    if parts.scheme not in ("http", "https"):
        raise ActivityError(bad)
    if not host:
        raise ActivityError(bad)
    if parts.username is not None or parts.password is not None:
        raise ActivityError(bad)
    if parts.path or parts.query or parts.fragment:
        raise ActivityError(bad)
    return Origin(value)


def secret_of(directory, port, origin=None):
    """The live view's secret for one store. A fresh token holds TOKEN_BYTES bytes from secrets. It lives in <store>/activity-token, a file created mode 0600 and reused across restarts while it stays valid, with surrounding ASCII whitespace ignored, and replaced otherwise."""
    path = directory / TOKEN_FILE_NAME
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        fd = None
    except OSError as error:
        raise ActivityError(f"cannot create the live view's token file ({error.strerror}); check its permissions and run this again") from None
    if fd is not None:
        text = secrets.token_urlsafe(TOKEN_BYTES)
        with os.fdopen(fd, "w") as token_file:
            token_file.write(text)
        token = text.encode("ascii")
        if origin is None:
            return Secret(token, b"/activity/" + token, f"http://127.0.0.1:{port}/activity/{text}")
        return Secret(token, b"/activity/" + token, f"{origin.text}/activity/{text}", True)
    text = read_valid_token(path)
    if text:
        token = text.encode("ascii")
        if origin is None:
            return Secret(token, b"/activity/" + token, f"http://127.0.0.1:{port}/activity/{text}")
        return Secret(token, b"/activity/" + token, f"{origin.text}/activity/{text}", True)
    text = secrets.token_urlsafe(TOKEN_BYTES)
    install_token(path, text)
    token = text.encode("ascii")
    if origin is None:
        return Secret(token, b"/activity/" + token, f"http://127.0.0.1:{port}/activity/{text}")
    return Secret(token, b"/activity/" + token, f"{origin.text}/activity/{text}", True)


def read_valid_token(path):
    """The existing token when its file is a private regular file holding a generated token, with surrounding ASCII whitespace ignored, else an empty string."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return ""
    try:
        done = os.fstat(fd)
        if not stat.S_ISREG(done.st_mode):
            return ""
        if done.st_uid != os.getuid():
            return ""
        if stat.S_IMODE(done.st_mode) != 0o600:
            return ""
        raw = b""
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            raw += chunk
            if len(raw) > 4096:
                return ""
        try:
            text = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            return ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", text or ""):
            return ""
        return text
    except OSError:
        return ""
    finally:
        os.close(fd)


def install_token(path, text):
    """Write a replacement token through a private temporary file, then move it over the token path."""
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(dir=path.parent)
    except OSError as error:
        raise ActivityError(f"cannot write the live view's token file ({error.strerror}); check its permissions and run this again") from None
    try:
        with os.fdopen(tmp_fd, "w") as tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except OSError as error:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise ActivityError(f"cannot write the live view's token file ({error.strerror}); check its permissions and run this again") from None


class LiveServer(http.server.ThreadingHTTPServer):
    """The HTTP server for one store's live view. It binds without the hostname lookup in HTTPServer.server_bind."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def live_server(settings, secret, port):
    """The HTTP server for one store's live view. It binds 127.0.0.1 only."""
    server = LiveServer(("127.0.0.1", port), LiveHandler)
    server.daemon_threads = True
    server.live_settings = settings
    server.live_secret = secret
    server.live_pages = Pages(body=b"", built=0.0)
    return server


class LiveHandler(http.server.BaseHTTPRequestHandler):
    """One live view request. GET at the secret route answers the current page. GET at any other path answers 404. Only GET and OPTIONS are answered."""

    def live_send(self, code, kind, body):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Access-Control-Allow-Origin", "null")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_OPTIONS(self):
        self.live_send(204, "text/plain; charset=utf-8", b"")

    def do_GET(self):
        secret = self.server.live_secret
        request = self.path.split("?", 1)[0].encode("ascii", "backslashreplace")
        if not secrets.compare_digest(request, secret.route):
            self.live_send(404, "text/plain; charset=utf-8", b"")
            return
        pages = self.server.live_pages
        with pages.lock:
            pages = self.server.live_pages
            now = time.time()
            if pages.body and now - pages.built <= LIVE_REBUILD_SECONDS:
                body = pages.body
            else:
                try:
                    body = render_page(self.server.live_settings).encode(errors="backslashreplace")
                except ActivityError as error:
                    self.live_send(503, "text/plain; charset=utf-8", f"activity: {error}".encode(errors="backslashreplace"))
                    return
                self.server.live_pages = replace(pages, body=body, built=now)
        self.live_send(200, "text/html; charset=utf-8", body)

    def log_message(self, *args):
        pass


def serve(settings, secret, port):
    """Run the live view server for one store until interrupted. It prints the token address so a wrapper can embed it."""
    server = live_server(settings, secret, port)
    actual = server.server_address[1]
    if actual != port and not secret.given_origin:
        secret = replace(secret, url=f"http://127.0.0.1:{actual}{secret.route.decode('ascii')}")
        server.live_secret = secret
    print(secret.url, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def live_wrapper(url):
    """The self-contained wrapper that polls one live view server and folds its page. It holds the token, so it must never be shared or committed."""
    return (
        "<!doctype html><meta charset=utf-8><meta name=viewport content=\"width=device-width,initial-scale=1\">"
        f"<title>Agent activity live</title><style>{LIVE_CSS}</style>"
        "<div class=livebar><button id=ex>Expand all</button><button id=co>Collapse all</button>"
        "<span id=live>Connecting to the live activity server...</span></div>"
        "<style id=s></style><div id=o></div><script type=application/json id=d></script>"
        f"<script>const LIVE_URL={json.dumps(url)};const LIVE_EVERY={LIVE_POLL_MS};{LIVE_FOLD}{LIVE_SCRIPT}</script>")


if __name__ == "__main__":
    sys.exit(main())
