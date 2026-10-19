import base64
import fcntl
import hashlib
import html
import json
import os
import random
import re
import runpy
import select
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests import in_process
from unittest import mock
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "t3/added/brigade/scripts/activity.py"
MOD = runpy.run_path(str(SCRIPT))
SKILL = ROOT / "t3/added/brigade/SKILL.md"
VIEWS = ROOT / "t3/added/brigade/views.md"
GUIDE = ROOT / "docs/guide.md"
SKILL = ROOT / "t3/added/brigade/SKILL.md"
VIEWS = ROOT / "t3/added/brigade/views.md"
GUIDE = ROOT / "docs/guide.md"
MARKERS = ("threadmarker", "nodemarker", "pathmarker", "homemarker", "projectmarker", "0a1b2c3")
COORDINATOR = "mcp:threadmarker-coordinator"
PROJECT = "project:projectmarker-own"
ELSEWHERE = "project:projectmarker-elsewhere"
CHANGED = "this T3 build stores threads differently, so update pstack-t3"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc).timestamp()
FROZEN = "import runpy, sys, time\nclock = float(sys.argv[1])\ntime.time = lambda: clock\ndel sys.argv[:2]\nrunpy.run_path(sys.argv[0], run_name='__main__')"
SCHEMA = {
    "orchestration_v2_projection_metadata": ("projection_name", "schema_version"),
    "orchestration_v2_projection_threads": ("thread_id", "title", "default_provider", "payload_json", "project_id"),
    "orchestration_v2_projection_runs": ("thread_id", "status", "requested_at", "completed_at"),
    "orchestration_v2_projection_subagents": ("subagent_id", "thread_id", "child_thread_id", "status", "started_at", "completed_at"),
}
UNIT_COLUMNS = ("id", "at", "state", "station", "tickets", "task", "thread", "branch", "pr", "sha", "summary", "timebox", "lease", "paths", "reported")
LOG_COLUMNS = ("at", "kind", "id", "state", "note")
def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
    saved = dict(os.environ)

    def restore():
        os.environ.clear()
        os.environ.update(saved)

    unittest.addModuleCleanup(restore)
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull
def setUpModule():
    fence_git_environment()
def worker(number):
    return f"mcp:threadmarker-worker-{number}"
def write_threads(fixture, rows):
    columns = ("at", "dish", "attempt", "role", "thread", "seat", "start", "end")
    lines = ["\t".join(columns)]
    lines += ["\t".join(row.get(column, "") for column in columns) for row in rows]
    (fixture.store / "threads.tsv").write_text("\n".join(lines) + "\n")
def native(number):
    return f"thread:provider:threadmarker-native-{number}"
def delegated(parent, request):
    """A delegated child's thread id, in the form T3 writes it."""
    return "thread:delegated-task:command%3A" + quote(parent, safe="") + "%3Adelegate-task%3A" + quote(request, safe="")
def listing(directory):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(Path(directory).iterdir()) if path.is_file()}
def live_listing(directory, files=None):
    """listing() with the -shm file's bytes left out. Every SQLite reader of a live database writes its read mark there."""
    files = listing(directory) if files is None else files
    return {name: "" if name.endswith("-shm") else digest for name, digest in files.items()}
class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        self.store = self.root / "pathmarker state" / "proj" / "kit"
        self.home = self.root / "homemarker"
        self.base = self.root / "pathmarker t3 base"
        self.database = self.base / "userdata" / "statev2.sqlite"
        self.meta = {"restaurant": "kit", "projectRoot": str(self.root / "pathmarker checkout" / "proj"), "thread": COORDINATOR}
        self.now = NOW
        self.units, self.log, self.threads, self.turns, self.delegations = [], [], [], [], []
        self.writer = None

    def ago(self, minutes):
        assert self.now == int(self.now), "a stamp is written in milliseconds, so a fractional now can put it after the instant it names and shorten an age by a second"
        return self.now - minutes * 60

    def stamp(self, minutes):
        return None if minutes is None else datetime.fromtimestamp(self.ago(minutes), timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def unit(self, ident, state="in-progress", summary="", thread="", task="", pr=""):
        row = dict.fromkeys(UNIT_COLUMNS, "")
        row.update(id=ident, at=self.stamp(600), state=state, summary=summary, thread=thread, task=task, pr=pr)
        self.units.append(row)

    def retired(self, ident, thread):
        self.log.append({"at": self.stamp(500), "kind": "worker", "id": ident, "state": "retired", "note": thread})

    def thread(self, ident, title="", provider="claudeAgent", model="model-a", parent=None, turns=(), delegation=None, payload=None, instance=None, project=PROJECT):
        selection = {"instanceId": provider if instance is None else instance, "model": model}
        self.threads.append((ident, title, provider, json.dumps({"modelSelection": selection}) if payload is None else payload, project))
        self.turns += [(ident, status, self.stamp(start), self.stamp(end)) for status, start, end in turns]
        if parent is None:
            return None
        if delegation is None:
            end = turns[-1][2] if turns else None
            delegation = (turns[-1][0] if turns and end is not None else "running", turns[0][1] if turns else 30, end)
        node = f"node:delegated-task:nodemarker-{len(self.delegations)}"
        self.delegations.append((node, parent, ident, delegation[0], self.stamp(delegation[1]), self.stamp(delegation[2])))
        return node

    def coordinator(self, turns=(("running", 5, None),), **fields):
        self.thread(COORDINATOR, title="Coordinator pathmarker", turns=turns, **fields)

    def write_store(self):
        self.store.mkdir(parents=True, exist_ok=True)
        (self.store / "restaurant.json").write_text(json.dumps(self.meta))
        for name, columns, rows in (("dishes.tsv", UNIT_COLUMNS, self.units), ("log.tsv", LOG_COLUMNS, self.log)):
            lines = ["\t".join(columns)] + ["\t".join(row[column] for column in columns) for row in rows]
            (self.store / name).write_text("\n".join(lines) + "\n")

    def write_database(self, schema=SCHEMA, version=2, live=False):
        """Write T3's database in WAL mode. Closed, it has no -wal or -shm file. With live, a writer stays connected and both files exist."""
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        for table, columns in schema.items():
            connection.execute(f"create table {table} ({', '.join(columns)})")
        rows = {
            "orchestration_v2_projection_metadata": [("thread-projections", version)] if version is not None else [],
            "orchestration_v2_projection_threads": self.threads,
            "orchestration_v2_projection_runs": self.turns,
            "orchestration_v2_projection_subagents": self.delegations,
        }
        for table, columns in schema.items():
            if columns == SCHEMA.get(table):
                connection.executemany(f"insert into {table} values ({', '.join('?' * len(columns))})", rows[table])
        if live:
            self.writer = connection
        else:
            connection.close()

    def write(self, **database):
        self.write_store()
        self.write_database(**database)
        return self

    def close(self):
        if self.writer is not None:
            self.writer.close()

    def command(self, *args, at=True, t3_home=True, env=None, cwd=None):
        """The script runs as the main program of a process whose time.time() returns self.now, however long ago the fixture was built."""
        argv = ["--at", str(self.store)] if at is True else ["--at", str(at)] if at else []
        argv += ["--t3-home", str(self.base)] if t3_home is True else ["--t3-home", str(t3_home)] if t3_home else []
        environment = {"HOME": str(self.home), "PATH": os.environ.get("PATH", ""), **(env or {})}
        return in_process.Invocation(
            program=SCRIPT,
            argv=(*argv, *args),
            env=environment,
            cwd=cwd or self.root,
            clock=self.now,
        )

    def run(self, *args, **how):
        return in_process.invoke(self.command(*args, **how))

    def stdout_bytes(self, *args, **how):
        result = in_process.invoke(replace(self.command(*args, **how), text=False))
        assert (result.returncode, result.stderr) == (0, b""), result.stderr
        return result.stdout

    def stamp_at_offset(self, minutes, hours):
        return datetime.fromtimestamp(self.ago(minutes), timezone(timedelta(hours=hours))).isoformat(timespec="milliseconds")

    def start(self, *args, **how):
        return in_process.spawn(in_process.Reason.RACE, self.command(*args, **how))

    def read(self, hours=3.0):
        store = MOD["read_store"](self.store)
        window = MOD["Window"](self.now - hours * 3600, self.now)
        connection = MOD["open_t3"](self.database)
        try:
            MOD["check_shape"](connection)
            return store, MOD["read_t3"](connection, window, MOD["roots_of"](store), store.coordinators), window
        finally:
            connection.close()
class ActivityCase(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.fixture = Fixture(directory.name)
        self.root = Path(directory.name)
        self.temporary = Path(directory.name).name
        self.addCleanup(self.fixture.close)

    def fails(self, status, *args, **how):
        result = self.fixture.run(*args, **how)
        self.assertEqual((result.returncode, result.stdout), (status, ""), result.stderr)
        self.assertEqual(result.stderr.count("\n"), 1, result.stderr)
        for marker in MARKERS:
            self.assertNotIn(marker, result.stderr)
        return result.stderr.strip()
def populate(fixture, units, agents, running=0, failed=0, heavy=False, in_flight=None, failed_every=0):
    fixture.coordinator(turns=(("completed", 170, 160), ("running", 5, None)))
    states = ("in-progress", "in-review", "passed", "queued", "sent-back", "blocked")
    wide = "𝔸𝔹ℂ𝔻 " * 10 if heavy else ""
    model = "vendor/model-" + "𝕏" * 30 if heavy else "model-a"
    settled = 0
    for index in range(units):
        number, live = index + 1, index >= units - (max(1, units // 4) if in_flight is None else in_flight)
        link = f"https://example.test/o/r/pull/{number}" + ("/long-path" * 12 if heavy and number % 2 else "")
        fixture.unit(f"D{number}", states[index % 6] if live else ("merged", "dropped")[index % 5 == 0], f"{wide}summary of item {number}", thread=worker(number), pr=link)
        base, child = 170 - index * 160 / units, None
        for position in range(agents // units + (index < agents % units)):
            start = max(2.0, base - position * 0.15)
            status, end = "completed", start - 1
            if live and running:
                status, end, running = "running", None, running - 1
            elif live and failed:
                status, failed = "failed", failed - 1
            elif not live and failed_every:
                settled += 1
                status = "completed" if settled % failed_every else "failed"
            turns = ((status, start, end),)
            provider = ("claudeAgent", "codex", "grok", "opencode", "cursor")[position % 5]
            if position == 0:
                fixture.thread(worker(number), title=f"D{number} worker pathmarker", provider=provider, model=model, turns=turns)
            elif position % 3 == 1:
                child = delegated(worker(number), f"how-explorer-{position}")
                fixture.thread(child, title=f"{wide}how explorer: part {position}", provider=provider, model=model, parent=worker(number), turns=turns)
            elif position % 3 == 2:
                request = f"brigade-kit-d{number}-verify-{position}-0a1b2c3"
                fixture.thread(delegated(COORDINATOR, request), title="Act as the review sub-agent for this task.", provider=provider, model=model, parent=COORDINATOR, turns=turns)
            else:
                fixture.thread(f"{native(number)}-{position}", title="/root/spec_review", provider=provider, model=model, parent=child, delegation=(status, start, end))
    return fixture
def empty(fixture):
    fixture.unit("D7", "in-progress", "Agent activity page", pr=PR7)
    fixture.coordinator(turns=(("completed", 400, 390),))
    return fixture
def one_running(fixture):
    fixture.coordinator()
    fixture.unit("D7", "in-progress", "Agent activity page", thread=worker(1), pr=PR7)
    fixture.unit("D6", "queued", "Queue fix", pr=PR6)
    fixture.thread(worker(1), title="D7 worker", turns=(("completed", 100.5, 80), ("running", 42.5, None)))
    return fixture
def failed_child(fixture):
    fixture.coordinator(turns=(("completed", 150, 140), ("running", 5.5, None)), model="model-c")
    fixture.unit("D7", "in-review", "Agent activity page", thread=worker(1), pr=PR7)
    fixture.unit("D6", "queued", "Queue fix", pr=PR6)
    fixture.unit("D5", "merged", "Old work", thread=worker(5))
    fixture.thread(worker(1), title="D7 worker", turns=(("completed", 120.5, 100), ("running", 42.5, None)))
    runner = delegated(worker(1), "architect-d7-runner-2")
    fixture.thread(runner, title="architect runner 2", provider="codex", model="model-b", parent=worker(1), turns=(("running", 6.5, None),))
    fixture.thread(native(1), title="/root/spec_review", provider="codex", model="model-b", parent=runner, delegation=("completed", 5, 4))
    fixture.thread(delegated(COORDINATOR, "brigade-kit-d7-verify-0a1b2c3"), title="Act as the review sub-agent for this task.",
                   provider="grok", model="grok/model-c", parent=COORDINATOR, turns=(("failed", 30.5, 27),))
    fixture.thread(worker(5), title="D5 worker", turns=(("completed", 170, 165),))
    fixture.thread(delegated(COORDINATOR, "why-investigator"), title="Read the brief at /pathmarker/brief.md", parent=COORDINATOR, turns=(("interrupted", 90, 88),))
    fixture.thread("mcp:threadmarker-other-project", turns=(("completed", 20, 10),))
    return fixture
def typical(fixture):
    return populate(fixture, units=9, agents=50, running=3, failed=1)
def busy(fixture):
    return populate(fixture, units=36, agents=400, running=5, failed=3)
def day(fixture):
    return populate(fixture, units=36, agents=400, running=8, failed=2, in_flight=6, failed_every=11)
def extreme(fixture):
    return populate(fixture, units=200, agents=3000, running=60, failed=10, heavy=True)
class OutputCase(ActivityCase):
    def out(self, *args, **how):
        result = self.fixture.run(*args, **how)
        self.assertEqual((result.returncode, result.stderr), (0, ""), result.stdout[:200])
        return result.stdout

    def document(self, *args, **how):
        out = self.out(*args, **how)
        self.assertTrue(out.startswith("<!doctype html>") and out.endswith("</script>\n"), out[:80])
        return out[:-1]
def write_goals(fixture, rows):
    header = "id\tat\tstate\tname\tpurpose\tdone\tshare\tclosed"
    (fixture.store / "goals.tsv").write_text("\n".join([header, *("\t".join(row) for row in rows)]) + "\n")
def widen_items(fixture, cells):
    path = fixture.store / "dishes.tsv"
    header, *rows = path.read_text().splitlines()
    widened = ["\t".join([row, *cells.get(row.split("\t")[0], ())]) for row in rows]
    path.write_text("\n".join([header, *widened]) + "\n")
def queued_only(fixture):
    fixture.coordinator()
    fixture.unit("D7", "in-progress", "Activity page", thread=worker(1))
    fixture.thread(worker(1), title="D7 worker", turns=(("queued", 5, None),))
    return fixture
def put(rows, index, column, value):
    rows[index] = (*rows[index][:column], value, *rows[index][column + 1:])
def frozen_bytes(script, fixture, *args):
    result = in_process.invoke(in_process.Invocation(
        program=script,
        argv=("--at", str(fixture.store), "--t3-home", str(fixture.base), *args),
        env={"HOME": str(fixture.home), "PATH": os.environ.get("PATH", "")},
        cwd=fixture.root,
        clock=fixture.now,
        text=False,
    ))
    if (result.returncode, result.stderr) != (0, b""):
        raise AssertionError(result.stderr)
    return result.stdout
def tree(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(Path(directory).rglob("*")) if path.is_file()}
def forms(value):
    """value as written, in other cases, and percent-encoded once, twice, and in part."""
    once = quote(value, safe="")
    return tuple(dict.fromkeys((value, value.upper(), value.lower(), value.swapcase(), once, quote(once, safe=""), once.lower(), quote(value), value.replace("-", "%2D").replace("_", "%5f"))))
PR7 = "https://example.test/o/r/pull/7"
PR6 = "https://example.test/o/r/pull/6"
def write_goal_names(directory, names):
    """Write the store's goals table, one goal per name, with the id G1 up."""
    columns = MOD["STORE_COLUMNS"]["goals.tsv"]
    lines = ["\t".join(columns)]
    for number, name in enumerate(names, start=1):
        goal = dict.fromkeys(columns, "")
        goal.update(id=f"G{number}", state="open", name=name)
        lines.append("\t".join(goal[column] for column in columns))
    (directory / "goals.tsv").write_text("\n".join(lines) + "\n")
