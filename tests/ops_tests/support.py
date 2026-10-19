import dataclasses
import hashlib
import http.server
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple, Optional, Union

from tests import in_process

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from check import Identity, REQUIRED_SCAN, machine_value_findings  # noqa: E402
ARCHIVE = ROOT / "ops/t3-archive-threads.py"
EXAMPLE = ROOT / "ops/config.example"
DOC = ROOT / "docs/operations.md"

CREDENTIAL = "test-credential"
SESSION = "session-1"


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




@dataclasses.dataclass(frozen=True)
class Layout:
    t3_home: str = "t3-home"


class Sandbox:
    def __init__(self, cleanup, layout=Layout()):
        self.layout = layout
        self.root = Path(tempfile.mkdtemp(prefix="ops-"))
        cleanup(shutil.rmtree, self.root, True)
        self.home = self.root / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.config = self.root / "ops.conf"
        (self.root / "tmp").mkdir(parents=True, exist_ok=True)

    def env(self, **extra):
        return {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home),
                "TMPDIR": str(self.root / "tmp"), "LC_ALL": "C", **extra}

    def config_text(self, drop=(), **overrides):
        if "ARCHIVE_T3_HOME" in drop:
            return ""
        return f"ARCHIVE_T3_HOME={overrides.get('ARCHIVE_T3_HOME', str(self.home / self.layout.t3_home))}\n"

    def write_config(self, path=None, *, text=None, drop=(), **overrides):
        path = Path(path or self.config)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.config_text(drop, **overrides) if text is None else text
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        return path

    def tree(self):
        tree = {}
        for path in sorted(self.home.rglob("*")):
            mode = stat.S_IMODE(path.lstat().st_mode)
            if path.is_symlink():
                entry = ("link", mode, os.readlink(path))
            elif path.is_dir():
                entry = ("dir", mode, "")
            else:
                entry = ("file", mode, hashlib.sha256(path.read_bytes()).hexdigest())
            tree[path.relative_to(self.home).as_posix()] = entry
        return tree

    def run(self, script, *args, config: Union[bool, Path] = True, **extra_env):
        flag = []
        if config is True and not self.config.exists():
            self.write_config()
        if config is not False:
            flag = ["--config", str(self.config if config is True else config)]
        env = self.env(**extra_env)
        return in_process.invoke(in_process.Invocation(
            program=script,
            argv=(*flag, *args),
            env=env,
            cwd=self.root,
            stdin="",
        ))


TOOL_ERROR, RPC_ERROR, NO_REPLY = "tool error", "rpc error", "no reply"


class McpStub:
    def __init__(self, cleanup):
        self.requests, self.failures, self.event_stream = [], {}, False
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append({"path": self.path, "method": body["method"], "params": body.get("params"),
                                      "authorization": self.headers.get("Authorization"),
                                      "session": self.headers.get("Mcp-Session-Id")})
                status, headers, reply = stub.answer(body)
                kind, data = "application/json", b"" if reply is None else json.dumps(reply).encode()
                if stub.event_stream and body["method"] == "tools/call":
                    progress = json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}).encode()
                    kind, data = "text/event-stream", b"event: message\ndata: " + progress + b"\n\nevent: message\ndata: " + data + b"\n\n"
                self.send_response(status)
                for name, value in {"Content-Type": kind, "Content-Length": str(len(data)), **headers}.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.01,), daemon=True)
        self.thread.start()
        cleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def answer(self, body):
        reply = {"jsonrpc": "2.0", "id": body.get("id")}
        if body["method"] == "initialize":
            reply["result"] = {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "stub", "version": "1"}}
            return 200, {"Mcp-Session-Id": SESSION}, reply
        if body["method"] == "notifications/initialized":
            return 202, {}, None
        failure = self.failures.get(body["params"]["arguments"].get("threadId"))
        if failure == NO_REPLY:
            return 200, {}, None
        if failure == RPC_ERROR:
            reply["error"] = {"code": -32000, "message": "tool failed"}
        elif failure == TOOL_ERROR:
            reply["result"] = {"isError": True, "content": [{"type": "text", "text": "not archived"}]}
        else:
            reply["result"] = {"content": [{"type": "text", "text": "archived"}]}
        return 200, {}, reply

    def archived(self):
        return [request["params"]["arguments"]["threadId"] for request in self.requests
                if request["method"] == "tools/call" and request["params"]["name"] == "t3_thread_organize"
                and request["params"]["arguments"].get("action") == "archive"]

    def reset(self):
        self.requests.clear()


OLD, RECENT = 48, 1
SETTLED, PINNED, ARCHIVED, DELETED = "S", "P", "A", "D"


class Thread(NamedTuple):
    id: str
    project: str
    parent: Optional[str]
    run_statuses: tuple
    hours_since_update: Optional[float]
    marks: str = ""


P1, P2 = "project-1", "project-2"
PROJECTS = ((P1, "Fixture Project"), (P2, "Other Project"))
THREADS = (
    Thread("coord",        P1, None,          ("running",),               RECENT),
    Thread("other",        P1, None,          ("waiting",),               RECENT),
    Thread("done-top",     P1, None,          ("completed",),             OLD),
    Thread("idle-top",     P1, None,          (),                         OLD, SETTLED),
    Thread("pinned-top",   P1, None,          (),                         OLD, SETTLED + PINNED),
    Thread("recent-top",   P1, None,          (),                         RECENT, SETTLED),
    Thread("busy-top",     P1, None,          ("queued",),                OLD, SETTLED),
    Thread("quiet-top",    P1, None,          ("completed",),             OLD, SETTLED),
    Thread("gone-top",     P1, None,          ("running",),               OLD, ARCHIVED),
    Thread("deleted-top",  P1, None,          (),                         OLD, DELETED),
    Thread("c-old",        P1, "coord",       ("completed",),             OLD, SETTLED),
    Thread("c-recent",     P1, "coord",       (),                         RECENT, SETTLED),
    Thread("c-live",       P1, "coord",       ("running",),               RECENT),
    Thread("c-parent",     P1, "coord",       ("completed",),             OLD, SETTLED),
    Thread("c-grand-live", P1, "c-parent",    ("starting",),              RECENT),
    Thread("c-grand-old",  P1, "c-parent",    (),                         OLD, SETTLED),
    Thread("o-old",        P1, "other",       (),                         OLD, SETTLED),
    Thread("d-old",        P1, "done-top",    ("completed", "failed"),    OLD, SETTLED),
    Thread("d-recent",     P1, "done-top",    (),                         RECENT, SETTLED),
    Thread("d-live",       P1, "done-top",    ("completed", "preparing"), OLD),
    Thread("d-archived",   P1, "done-top",    (),                         OLD, SETTLED + ARCHIVED),
    Thread("d-deleted",    P1, "done-top",    (),                         OLD, SETTLED + DELETED),
    Thread("d-unsettled",  P1, "done-top",    (),                         OLD),
    Thread("d-pinned",     P1, "done-top",    (),                         OLD, SETTLED + PINNED),
    Thread("d-parent",     P1, "done-top",    ("completed",),             OLD, SETTLED),
    Thread("d-grand-live", P1, "d-parent",    ("running",),               RECENT),
    Thread("d-grand-old",  P1, "d-parent",    (),                         OLD, SETTLED),
    Thread("q-live",       P1, "quiet-top",   ("running",),               RECENT),
    Thread("q-old",        P1, "quiet-top",   (),                         OLD, SETTLED),
    Thread("g-old",        P1, "gone-top",    (),                         OLD, SETTLED),
    Thread("del-kid",      P1, "deleted-top", (),                         OLD, SETTLED),
    Thread("x-kid",        P1, "p2-live-top", (),                         OLD, SETTLED),
    Thread("loop-a",       P1, "loop-b",      (),                         OLD, SETTLED),
    Thread("loop-b",       P1, "loop-a",      (),                         OLD, SETTLED),
    Thread("p2-live-top",  P2, None,          ("running",),               RECENT),
    Thread("p2-idle-top",  P2, None,          (),                         OLD, SETTLED),
    Thread("p2-kid",       P2, "p2-idle-top", (),                         OLD, SETTLED),
)
_DONE = ("d-old", "d-unsettled", "d-pinned", "d-grand-old")
_LOOSE = ("q-old", "g-old", "del-kid", "loop-a", "loop-b")
_COORD = ("c-old", "c-grand-old")
ARCHIVE_CASES = (
    ((),
     "to archive: 9; skipped: live 12, recent 1, under a running thread 5, kept 0", (*_DONE, *_LOOSE)),
    (("--top-level-settled",),
     "to archive: 10; skipped: live 12, recent 1, under a running thread 5, kept 0", ("idle-top", *_DONE, *_LOOSE)),
    (("--coordinator", "coord"),
     "to archive: 11; skipped: live 12, recent 2, under a running thread 2, kept 0", (*_COORD, *_DONE, *_LOOSE)),
    (("--coordinator", "coord", "--top-level-settled"),
     "to archive: 12; skipped: live 12, recent 2, under a running thread 2, kept 0",
     ("idle-top", *_COORD, *_DONE, *_LOOSE)),
    (("--keep", "done-top"),
     "to archive: 5; skipped: live 8, recent 0, under a running thread 5, kept 9", _LOOSE),
    (("--keep", "coord", "--coordinator", "coord"),
     "to archive: 11; skipped: live 11, recent 2, under a running thread 2, kept 1", (*_COORD, *_DONE, *_LOOSE)),
    (("--keep", "d-parent", "--keep", "c-old", "--coordinator", "coord"),
     "to archive: 10; skipped: live 11, recent 2, under a running thread 2, kept 2",
     ("c-grand-old", "d-old", "d-unsettled", "d-pinned", "d-grand-old", *_LOOSE)),
    (("--older-than-hours", "0.5", "--top-level-settled"),
     "to archive: 12; skipped: live 12, recent 0, under a running thread 5, kept 0",
     ("idle-top", "recent-top", "d-old", "d-recent", "d-unsettled", "d-pinned", "d-grand-old", *_LOOSE)),
    (("--older-than-hours", "72", "--top-level-settled"),
     "to archive: 0; skipped: live 12, recent 10, under a running thread 5, kept 0", ()),
    (("--coordinator", "idle-top", "--top-level-settled"),
     "to archive: 9; skipped: live 12, recent 1, under a running thread 5, kept 0", (*_DONE, *_LOOSE)),
)


def build_database(path, projects, threads, now=None, nodes=(), subagents=()):
    now = now or datetime.now(timezone.utc)

    def stamp(hours):
        if hours is None:
            return None
        moment = now - timedelta(hours=hours)
        return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"

    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path)
    database.execute("create table projection_projects (project_id, title)")
    database.execute("create table orchestration_v2_projection_threads "
                     "(thread_id, project_id, updated_at, archived_at, deleted_at, payload_json)")
    database.execute("create table orchestration_v2_projection_runs (run_id, thread_id, status)")
    database.execute("create table orchestration_v2_projection_nodes (thread_id, run_id, status)")
    database.execute("create table orchestration_v2_projection_subagents "
                     "(thread_id, run_id, child_thread_id, origin, status)")
    database.executemany("insert into projection_projects values (?, ?)", projects)
    for thread in (Thread(*row) for row in threads):
        when = stamp(thread.hours_since_update)
        payload = {"lineage": {"parentThreadId": thread.parent} if thread.parent else None,
                   "settledAt": when if SETTLED in thread.marks else None, "pinnedAt": when if PINNED in thread.marks else None}
        database.execute("insert into orchestration_v2_projection_threads values (?, ?, ?, ?, ?, ?)",
                         (thread.id, thread.project, when, when if ARCHIVED in thread.marks else None,
                          when if DELETED in thread.marks else None, json.dumps(payload)))
        database.executemany("insert into orchestration_v2_projection_runs values (?, ?, ?)",
                             [(f"{thread.id}-run-{n}", thread.id, status) for n, status in enumerate(thread.run_statuses)])
    database.executemany("insert into orchestration_v2_projection_nodes values (?, ?, ?)", list(nodes))
    database.executemany("insert into orchestration_v2_projection_subagents values (?, ?, ?, ?, ?)", list(subagents))
    database.commit()
    database.close()


@dataclasses.dataclass(frozen=True)
class ArchiveRun:
    returncode: int
    stdout: str
    stderr: str
    archived: tuple


class ArchiveWorld:
    def __init__(self, cleanup, layout=Layout(), projects=PROJECTS, threads=THREADS, nodes=(), subagents=()):
        self.sandbox = Sandbox(cleanup, layout)
        self.stub = McpStub(cleanup)
        userdata = self.sandbox.home / layout.t3_home / "userdata"
        self.database = userdata / "statev2.sqlite"
        build_database(self.database, projects, threads, nodes=nodes, subagents=subagents)
        (userdata / "server-runtime.json").write_text(json.dumps({"origin": self.stub.origin}))

    def run(self, script, *flags, config=True, project="fixture project"):
        self.stub.reset()
        done = self.sandbox.run(script, "--project", project, *flags, config=config, T3_CODE_MCP_AUTHORIZATION=CREDENTIAL)
        return ArchiveRun(done.returncode, done.stdout, done.stderr, tuple(self.stub.archived()))


class OpsTest(unittest.TestCase):
    maxDiff = None

    def world(self, **fixture):
        return ArchiveWorld(self.addCleanup, **fixture)

    def assertRefused(self, done, script, problem):
        self.assertEqual((done.returncode, done.stdout, done.stderr), (78, "", f"{script.name}: {problem}\n"))


