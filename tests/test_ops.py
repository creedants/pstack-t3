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

ROOT = Path(__file__).resolve().parents[1]
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


ACCEPTED, NO_OWN_KEY = object(), object()
READER_CASES = (
    ("base", lambda base: base, ACCEPTED),
    ("crlf", lambda base: base.replace("\n", "\r\n"), ACCEPTED),
    ("no final newline", lambda base: base.rstrip("\n"), ACCEPTED),
    ("comment and blank", lambda base: "# c\n\n   \t\n  # d\n" + base, ACCEPTED),
    ("leading blanks", lambda base: base.replace("ARCHIVE_T3_HOME", " \tARCHIVE_T3_HOME"), ACCEPTED),
    ("other key", lambda base: base + "SOMETHING_ELSE=1 2 = 3\n", ACCEPTED),
    ("empty other value", lambda base: base + "X=\n", ACCEPTED),
    ("number sign inside a value", lambda base: base + "X=1 # not a comment\n", ACCEPTED),
    ("lone carriage return inside", lambda base: base + "X=1\rY=2\n", ACCEPTED),
    ("comment that is not UTF-8", lambda base: base.encode() + b"# \xff\xfe\n", ACCEPTED),
    ("space before equals", lambda base: base + "X =1\n", "line 2 is not KEY=value"),
    ("no equals", lambda base: base + "just words\n", "line 2 is not KEY=value"),
    ("lowercase key", lambda base: base + "x=1\n", "line 2 is not KEY=value"),
    ("digit first", lambda base: base + "1X=1\n", "line 2 is not KEY=value"),
    ("empty key", lambda base: base + "=1\n", "line 2 is not KEY=value"),
    ("export prefix", lambda base: base + "export X=1\n", "line 2 is not KEY=value"),
    ("key that is not UTF-8", lambda base: base.encode() + b"K\xff=1\n", "line 2 is not KEY=value"),
    ("other key twice", lambda base: base + "X=1\nX=1\n", "line 3 sets X a second time"),
    ("archive key twice", lambda base: base + "ARCHIVE_T3_HOME=/a/t3\n", "line 2 sets ARCHIVE_T3_HOME a second time"),
    ("nightly apps dir kept", lambda base: base + "NIGHTLY_APPS_DIR=/a/apps\n", ACCEPTED),
    ("nightly app name kept", lambda base: base + "NIGHTLY_APP_NAME=t3code\n", ACCEPTED),
    ("nightly restart env kept", lambda base: base + "NIGHTLY_RESTART_ENV=/a/restart.env\n", ACCEPTED),
    ("nightly mise tools kept", lambda base: base + "NIGHTLY_MISE_TOOLS=alpha\n", ACCEPTED),
    ("nightly version commands kept", lambda base: base + "NIGHTLY_VERSION_COMMANDS=alpha\n", ACCEPTED),
    ("nightly key twice", lambda base: base + "NIGHTLY_APP_NAME=t3code\nNIGHTLY_APP_NAME=t3code\n",
     "line 3 sets NIGHTLY_APP_NAME a second time"),
    ("empty file", lambda base: "", NO_OWN_KEY),
)


class ConfigTest(OpsTest):
    def probe(self, world, config=True, **env):
        return world.sandbox.run(ARCHIVE, "--project", "fixture project", "--dry-run", config=config, **env)

    def assertRan(self, done):
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, ARCHIVE_CASES[0][1] + "\n", ""))

    def test_no_config_prints_one_line_naming_the_file_and_changes_nothing(self):
        world = self.world()
        box = world.sandbox
        before = box.tree()
        for arguments in ((), ("--help",), ("--project", "fixture project"),
                          ("--project", "fixture project", "--top-level-settled")):
            with self.subTest(arguments=arguments):
                done = box.run(ARCHIVE, *arguments, config=False, T3_CODE_MCP_AUTHORIZATION=CREDENTIAL)
                self.assertRefused(done, ARCHIVE, f"no config file at {box.home}/.config/pstack-t3/ops.conf. "
                                                  "Copy ops/config.example to that path and set every key.")
                self.assertEqual(box.tree(), before)
                self.assertEqual(world.stub.requests, [])

    def test_xdg_config_home_is_used_only_when_it_is_absolute(self):
        world = self.world()
        box = world.sandbox
        box.write_config(box.root / "xdg/pstack-t3/ops.conf")
        self.assertRan(self.probe(world, config=False, XDG_CONFIG_HOME=str(box.root / "xdg")))
        self.assertRefused(self.probe(world, config=False, XDG_CONFIG_HOME="xdg"),
                           ARCHIVE, f"no config file at {box.home}/.config/pstack-t3/ops.conf. "
                           "Copy ops/config.example to that path and set every key.")

    def test_environment_variable_names_the_file(self):
        world = self.world()
        box = world.sandbox
        named = box.root / "named.conf"
        self.assertRefused(self.probe(world, config=False, PSTACK_T3_OPS_CONFIG=""),
                           ARCHIVE, f"no config file at {box.home}/.config/pstack-t3/ops.conf. "
                           "Copy ops/config.example to that path and set every key.")
        box.write_config(box.home / ".config/pstack-t3/ops.conf")
        self.assertRan(self.probe(world, config=False, PSTACK_T3_OPS_CONFIG=""))
        self.assertRefused(self.probe(world, config=False, PSTACK_T3_OPS_CONFIG=str(named)),
                           ARCHIVE, f"no config file at {named}. Copy ops/config.example to that path and set every key.")
        box.write_config(named)
        (box.home / ".config/pstack-t3/ops.conf").unlink()
        self.assertRan(self.probe(world, config=False, PSTACK_T3_OPS_CONFIG=str(named)))

    def test_flag_wins_and_a_missing_flag_file_is_not_replaced(self):
        world = self.world()
        box = world.sandbox
        box.write_config(box.home / ".config/pstack-t3/ops.conf")
        named = box.write_config(box.root / "named.conf")
        flagged = box.root / "flagged.conf"
        self.assertRefused(self.probe(world, config=flagged, PSTACK_T3_OPS_CONFIG=str(named)),
                           ARCHIVE, f"no config file at {flagged}. Copy ops/config.example to that path and set every key.")
        box.write_config(flagged)
        named.write_text("not a config\n")
        (box.home / ".config/pstack-t3/ops.conf").write_text("not a config\n")
        self.assertRan(self.probe(world, config=flagged, PSTACK_T3_OPS_CONFIG=str(named)))

    def test_config_flag_with_no_file_exits_2(self):
        world = self.world()
        done = world.sandbox.run(ARCHIVE, "--config", config=False)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (2, "", f"{ARCHIVE.name}: --config needs a file\n"))

    def test_a_config_path_that_is_a_directory_is_refused_and_a_dangling_link_is_no_config(self):
        world = self.world()
        link = world.sandbox.root / "link.conf"
        link.symlink_to(world.sandbox.root / "absent.conf")
        self.assertRefused(self.probe(world, config=world.sandbox.home),
                           ARCHIVE, f"config file {world.sandbox.home} is not a readable file")
        self.assertRefused(self.probe(world, config=link),
                           ARCHIVE, f"no config file at {link}. Copy ops/config.example to that path and set every key.")

    def test_the_reader_accepts_and_refuses_each_file(self):
        world = self.world()
        box = world.sandbox
        for name, build, expected in READER_CASES:
            with self.subTest(case=name):
                file = box.write_config(text=build(box.config_text()))
                done = self.probe(world)
                if expected is ACCEPTED:
                    self.assertRan(done)
                else:
                    problem = "does not set ARCHIVE_T3_HOME" if expected is NO_OWN_KEY else expected
                    self.assertRefused(done, ARCHIVE, f"config file {file} {problem}")

    def test_a_script_refuses_an_unknown_key_with_its_prefix_and_ignores_other_keys(self):
        world = self.world()
        box = world.sandbox
        file = box.write_config(text=box.config_text() + "ARCHIVE_DB=/x\n")
        with self.subTest(line="ARCHIVE_DB=/x"):
            self.assertRefused(self.probe(world),
                               ARCHIVE, f"config file {file} line 2 sets ARCHIVE_DB, and this script has no such key")
        for line in ("NIGHTLY_APPS_DIR=/a/apps", "NIGHTLY_APP_NAME=t3code", "NIGHTLY_RESTART_ENV=/a/restart.env",
                     "NIGHTLY_MISE_TOOLS=alpha", "NIGHTLY_VERSION_COMMANDS=alpha",
                     "NIGHTLY_LOG=/x", "OTHER=1"):
            with self.subTest(line=line):
                box.write_config(text=box.config_text() + line + "\n")
                self.assertRan(self.probe(world))

    def test_the_example_holds_the_one_required_key(self):
        box = self.world().sandbox
        example = EXAMPLE.read_text()
        keys = re.findall(r"^([A-Z][A-Z0-9_]*)=", example, re.MULTILINE)
        self.assertEqual(keys, ["ARCHIVE_T3_HOME"])
        archive = box.run(ARCHIVE, "--project", "fixture project", "--dry-run", config=EXAMPLE)
        self.assertEqual((archive.returncode, archive.stdout), (1, ""))
        self.assertIn("sqlite3.OperationalError", archive.stderr)
        file = box.write_config(text="".join(line for line in example.splitlines(True) if not line.startswith("ARCHIVE_T3_HOME=")))
        self.assertRefused(box.run(ARCHIVE, "--project", "fixture project", "--dry-run", config=file),
                           ARCHIVE, f"config file {file} does not set ARCHIVE_T3_HOME")
        legacy = (("NIGHTLY_APPS_DIR", "/a/apps"), ("NIGHTLY_APP_NAME", "t3code"),
                  ("NIGHTLY_RESTART_ENV", "/a/restart.env"), ("NIGHTLY_MISE_TOOLS", "alpha"),
                  ("NIGHTLY_VERSION_COMMANDS", "alpha"))
        full = example + "".join(f"{key}={value}\n" for key, value in legacy)
        full_file = box.write_config(text=full)
        full_run = box.run(ARCHIVE, "--project", "fixture project", "--dry-run", config=full_file)
        self.assertEqual((full_run.returncode, full_run.stdout), (1, ""))
        self.assertIn("sqlite3.OperationalError", full_run.stderr)
        for key, _ in legacy:
            with self.subTest(key=key):
                omitted = "".join(line for line in full.splitlines(True) if not line.startswith(key + "="))
                done = box.run(ARCHIVE, "--project", "fixture project", "--dry-run",
                               config=box.write_config(text=omitted))
                self.assertEqual((done.returncode, done.stdout), (1, ""))
                self.assertIn("sqlite3.OperationalError", done.stderr)

    def test_archive_refuses_a_t3_home_that_is_relative_or_holds_a_uri_character(self):
        world = self.world()
        for value in ("t3-home", "/a/t3?mode=rw", "/a/t3#x", "/a/t 3", ""):
            with self.subTest(value=value):
                file = world.sandbox.write_config(ARCHIVE_T3_HOME=value)
                self.assertRefused(world.sandbox.run(ARCHIVE, "--project", "fixture project"), ARCHIVE,
                                   f"config file {file} puts '{value}' in ARCHIVE_T3_HOME. "
                                   "It must start with / and use only the characters A-Za-z0-9._/-.")
                self.assertEqual(world.stub.requests, [])

    def test_blanks_around_the_value_are_dropped(self):
        world = self.world()
        box = world.sandbox
        box.write_config(text=box.config_text(ARCHIVE_T3_HOME=f"  {box.home}/t3-home \t").replace("\n", "\r\n"))
        self.assertRan(self.probe(world))



class ArchiveTest(OpsTest):
    def test_archives_exactly_the_expected_threads_for_each_flag_set(self):
        world = self.world()
        for flags, line, expected in ARCHIVE_CASES:
            with self.subTest(flags=flags):
                done = world.run(ARCHIVE, *flags)
                tail = f"archived {len(expected)}, failed 0\n" if expected else ""
                self.assertEqual((done.returncode, done.stdout, done.stderr), (0, line + "\n" + tail, ""))
                self.assertEqual(sorted(done.archived), sorted(expected))

    def test_dry_run_prints_the_same_summary_and_sends_nothing(self):
        world = self.world()
        for flags, line, _ in ARCHIVE_CASES:
            with self.subTest(flags=flags):
                done = world.run(ARCHIVE, *flags, "--dry-run")
                self.assertEqual((done.returncode, done.stdout, done.stderr), (0, line + "\n", ""))
                self.assertEqual(world.stub.requests, [])

    def test_nothing_to_archive_sends_nothing(self):
        world = self.world()
        done = world.run(ARCHIVE, "--older-than-hours", "72", "--top-level-settled")
        self.assertEqual((done.returncode, done.stdout), (0, "to archive: 0; skipped: live 12, recent 10, under a running thread 5, kept 0\n"))
        self.assertEqual(world.stub.requests, [])

    def test_the_database_file_is_unchanged_after_a_run(self):
        world = self.world()
        before = world.database.read_bytes()
        self.assertEqual(len(world.run(ARCHIVE).archived), 9)
        self.assertEqual(world.database.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in world.database.parent.iterdir()), ["server-runtime.json", "statev2.sqlite"])

    def test_every_request_carries_the_credential_and_every_request_after_the_first_carries_the_session_id(self):
        world = self.world()
        done = world.run(ARCHIVE)
        requests = world.stub.requests
        self.assertEqual([request["method"] for request in requests], ["initialize", "notifications/initialized", *["tools/call"] * 9])
        self.assertEqual({request["path"] for request in requests}, {"/mcp"})
        self.assertEqual({request["authorization"] for request in requests}, {CREDENTIAL})
        self.assertEqual([request["session"] for request in requests], [None, *[SESSION] * 10])
        self.assertEqual([request["params"] for request in requests[2:]], [
            {"name": "t3_thread_organize", "arguments": {"action": "archive", "threadId": name}} for name in done.archived])

    def test_an_event_stream_reply_is_read(self):
        world = self.world()
        world.stub.event_stream = True
        done = world.run(ARCHIVE)
        self.assertEqual((done.returncode, done.stdout.splitlines()[1]), (0, "archived 9, failed 0"))
        self.assertEqual(sorted(done.archived), sorted(ARCHIVE_CASES[0][2]))

    def test_a_failed_archive_is_reported_and_the_rest_continue(self):
        world = self.world()
        world.stub.failures = {**dict.fromkeys(("d-old", "d-pinned", "q-old", "loop-a"), TOOL_ERROR),
                               **dict.fromkeys(("d-parent", "g-old", "loop-b"), RPC_ERROR), "del-kid": NO_REPLY}
        done = world.run(ARCHIVE)
        failures = []
        for number, name in enumerate(done.archived, 3):
            if name in world.stub.failures:
                reply = world.stub.answer({"id": number, "method": "tools/call", "params": {"arguments": {"threadId": name}}})[2]
                failures.append("failed: " + json.dumps(reply)[:200])
        self.assertEqual((sum('"isError": true' in line for line in failures), sum('"error": {"code"' in line for line in failures),
                          failures.count("failed: null")), (4, 2, 1))
        self.assertEqual(done.stdout.splitlines(), [ARCHIVE_CASES[0][1], *failures[:5], "archived 2, failed 7"])
        self.assertEqual((done.returncode, len(done.archived)), (1, 9))

    def test_the_project_title_must_match_exactly_one_project(self):
        world = self.world()
        done = world.run(ARCHIVE, project="no such project")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (1, "", "expected one project titled 'no such project', found 0\n"))
        self.assertEqual(world.stub.requests, [])
        twins = self.world(projects=((P1, "Fixture Project"), (P2, "fixture project")))
        done = twins.run(ARCHIVE)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (1, "", "expected one project titled 'fixture project', found 2\n"))
        self.assertEqual(twins.stub.requests, [])

    def test_a_missing_file_or_update_time_ends_with_a_traceback_and_exit_1(self):
        world = self.world()
        (world.database.parent / "server-runtime.json").unlink()
        done = world.run(ARCHIVE)
        self.assertEqual((done.returncode, done.stdout, done.archived), (1, ARCHIVE_CASES[0][1] + "\n", ()))
        self.assertRegex(done.stderr, r"(?s)^Traceback .*\nFileNotFoundError: ")
        world.database.unlink()
        done = world.run(ARCHIVE)
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertRegex(done.stderr, r"(?s)^Traceback .*\nsqlite3\.OperationalError: ")
        world = self.world(threads=(("top", P1, None, (), OLD, ""), ("kid", P1, "top", (), None, "S")))
        done = world.run(ARCHIVE)
        self.assertEqual((done.returncode, done.stdout, world.stub.requests), (1, "", []))
        self.assertRegex(done.stderr, r"(?s)^Traceback .*\nTypeError: ")

    def test_the_database_under_archive_t3_home_is_read_and_home_dot_t3_is_not(self):
        world = self.world()
        decoy = world.sandbox.home / ".t3/userdata"
        build_database(decoy / "statev2.sqlite", PROJECTS, (("decoy-top", P1, None, (), OLD, "S"), ("decoy-kid", P1, "decoy-top", (), OLD, "S")))
        (decoy / "server-runtime.json").write_text(json.dumps({"origin": world.stub.origin}))
        done = world.run(ARCHIVE)
        self.assertEqual((done.returncode, sorted(done.archived)), (0, sorted(ARCHIVE_CASES[0][2])))

    def test_a_runless_child_with_a_pending_or_running_node_is_not_selected(self):
        threads = (("top", P1, None, (), OLD, "S"), ("kid-pending", P1, "top", (), OLD, "S"),
                   ("kid-running", P1, "top", (), OLD, "S"), ("kid-old", P1, "top", (), OLD, "S"))
        nodes = (("kid-pending", None, "pending"), ("kid-running", None, "running"))
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE)
        self.assertEqual(done.returncode, 0)
        self.assertNotIn("kid-pending", done.archived)
        self.assertNotIn("kid-running", done.archived)
        self.assertIn("kid-old", done.archived)
        self.assertNotIn("top", done.archived)

    def test_a_runless_child_with_a_waiting_provider_native_subagent_row_is_not_selected(self):
        threads = (("top", P1, None, (), OLD, "S"), ("kid-waiting", P1, "top", (), OLD, "S"),
                   ("kid-old", P1, "top", (), OLD, "S"))
        subagents = (("top", None, "kid-waiting", "provider_native", "waiting"),)
        world = self.world(threads=threads, subagents=subagents)
        done = world.run(ARCHIVE)
        self.assertEqual(done.returncode, 0)
        self.assertNotIn("kid-waiting", done.archived)
        self.assertNotIn("top", done.archived)
        self.assertNotIn("kid-old", done.archived)

    def test_a_child_whose_rows_are_all_terminal_and_which_is_past_the_cutoff_is_selected(self):
        threads = (("top", P1, None, (), OLD, "S"), ("kid-done", P1, "top", ("completed",), OLD, "S"))
        nodes = (("kid-done", "kid-done-run-0", "completed"),)
        subagents = (("top", "top-run-0", "kid-done", "provider_native", "completed"),)
        world = self.world(threads=threads, nodes=nodes, subagents=subagents)
        done = world.run(ARCHIVE)
        self.assertEqual(done.returncode, 0)
        self.assertIn("kid-done", done.archived)

    def test_a_live_runless_child_protects_its_ancestors(self):
        threads = (("top", P1, None, (), OLD, "S"), ("mid", P1, "top", (), OLD, "S"),
                   ("kid-live", P1, "mid", (), OLD, "S"))
        nodes = (("kid-live", None, "pending"),)
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE)
        self.assertEqual(done.returncode, 0)
        self.assertNotIn("kid-live", done.archived)
        self.assertNotIn("mid", done.archived)
        self.assertNotIn("top", done.archived)
        self.assertEqual(done.archived, ())

    def test_a_child_under_the_named_coordinator_follows_the_same_rule(self):
        threads = (("coord", P1, None, ("running",), RECENT), ("kid-live", P1, "coord", (), OLD, "S"),
                   ("kid-done", P1, "coord", ("completed",), OLD, "S"))
        nodes = (("kid-live", None, "running"),)
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE, "--coordinator", "coord")
        self.assertEqual(done.returncode, 0)
        self.assertNotIn("kid-live", done.archived)
        self.assertIn("kid-done", done.archived)

    def test_the_selection_flag_prints_the_expected_json_and_never_constructs_the_client(self):
        world = self.world()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only")
        self.assertEqual((done.returncode, done.stderr, world.stub.requests), (0, "", []))
        self.assertEqual(sorted(json.loads(done.stdout)), sorted(ARCHIVE_CASES[0][2]))

    def test_plain_selection_only_prints_the_sorted_json_array_and_nothing_else(self):
        world = self.world()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr, world.stub.requests),
                         (0, json.dumps(sorted(ARCHIVE_CASES[0][2])) + "\n", "", []))

    def test_batch_files_hold_the_same_ids_in_the_same_order_as_plain_selection_only(self):
        world = self.world()
        plain = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only")
        batch = world.sandbox.root / "batches"
        before = world.database.read_bytes()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only", "--batch-dir", str(batch))
        ids = json.loads(plain.stdout)
        self.assertEqual(ids, sorted(ARCHIVE_CASES[0][2]))
        self.assertEqual((batch / "batch-1.txt").read_text().splitlines(), ids)
        self.assertEqual((done.returncode, done.stdout, done.stderr),
                         (0, f"{batch / 'batch-1.txt'}\t{len(ids)}\n", ""))
        self.assertEqual([path.name for path in batch.iterdir()], ["batch-1.txt"])
        self.assertEqual(world.database.read_bytes(), before)
        self.assertEqual(world.stub.requests, [])

    def test_a_batch_size_of_2_over_5_candidates_gives_files_of_2_2_and_1(self):
        threads = (("top", P1, None, (), OLD, ""),
                   *[(f"n-{n}", P1, "top", (), OLD, "S") for n in range(5)])
        world = self.world(threads=threads)
        batch = world.sandbox.root / "batches"
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only",
                                 "--batch-dir", str(batch), "--batch-size", "2")
        files = [batch / "batch-1.txt", batch / "batch-2.txt", batch / "batch-3.txt"]
        groups = [["n-0", "n-1"], ["n-2", "n-3"], ["n-4"]]
        self.assertEqual([path.read_text().splitlines() for path in files], groups)
        self.assertEqual(done.stdout, "".join(f"{path}\t{len(group)}\n" for path, group in zip(files, groups)))
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(sorted(path.name for path in batch.iterdir()), ["batch-1.txt", "batch-2.txt", "batch-3.txt"])

    def test_default_batch_size_splits_141_candidates_into_140_and_1(self):
        threads = (("top", P1, None, (), OLD, ""),
                   *[(f"n-{n:03d}", P1, "top", (), OLD, "S") for n in range(141)])
        world = self.world(threads=threads)
        batch = world.sandbox.root / "batches"
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only",
                                 "--batch-dir", str(batch))
        ids = [f"n-{n:03d}" for n in range(141)]
        self.assertEqual((done.returncode, done.stderr, world.stub.requests), (0, "", []))
        self.assertEqual(sorted(path.name for path in batch.iterdir()), ["batch-1.txt", "batch-2.txt"])
        self.assertEqual((batch / "batch-1.txt").read_text().splitlines(), ids[:140])
        self.assertEqual((batch / "batch-2.txt").read_text().splitlines(), ids[140:])
        self.assertEqual(done.stdout, f"{batch / 'batch-1.txt'}\t140\n{batch / 'batch-2.txt'}\t1\n")

    def test_batch_dir_with_missing_parent_writes_nothing_and_exits_1(self):
        world = self.world()
        batch = world.sandbox.root / "no-parent" / "batches"
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only",
                                 "--batch-dir", str(batch))
        self.assertEqual((done.returncode, done.stdout, world.stub.requests), (1, "", []))
        self.assertFalse(batch.exists())
        self.assertFalse(batch.parent.exists())

    def test_dry_run_with_selection_only_prints_sorted_json(self):
        world = self.world()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--dry-run", "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr, world.stub.requests),
                         (0, json.dumps(sorted(ARCHIVE_CASES[0][2])) + "\n", "", []))

    def test_direct_run_without_credential_exits_1_with_summary_on_stdout_and_one_stderr_line(self):
        world = self.world()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, ARCHIVE_CASES[0][1] + "\n")
        self.assertEqual(world.stub.requests, [])
        self.assertIn("T3_CODE_MCP_AUTHORIZATION", done.stderr)
        self.assertEqual(len(done.stderr.strip().splitlines()), 1)

    def test_unknown_flag_exits_2(self):
        world = self.world()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--no-such-flag")
        self.assertEqual((done.returncode, done.stdout, world.stub.requests), (2, "", []))
        self.assertIn("--no-such-flag", done.stderr)

    def test_an_id_containing_percent_and_colon_is_written_unchanged(self):
        name = "a%b:c"
        world = self.world(threads=(("top", P1, None, (), OLD, ""), (name, P1, "top", (), OLD, "S")))
        batch = world.sandbox.root / "batches"
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only", "--batch-dir", str(batch))
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual((batch / "batch-1.txt").read_bytes(), b"a%b:c\n")
        self.assertEqual(done.stdout, f"{batch / 'batch-1.txt'}\t1\n")

    def test_zero_candidates_creates_the_directory_and_no_batch_file(self):
        world = self.world()
        batch = world.sandbox.root / "batches"
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--older-than-hours", "72",
                                 "--selection-only", "--batch-dir", str(batch))
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))
        self.assertEqual(list(batch.iterdir()), [])
        self.assertTrue(batch.is_dir())
        self.assertEqual(world.stub.requests, [])

    def test_an_existing_directory_is_refused(self):
        world = self.world()
        batch = world.sandbox.root / "batches"
        batch.mkdir()
        (batch / "keep.txt").write_text("stay\n")
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only", "--batch-dir", str(batch))
        self.assertEqual((done.returncode, done.stdout, done.stderr),
                         (1, "", f"{ARCHIVE.name}: {batch} already exists\n"))
        self.assertEqual([path.name for path in batch.iterdir()], ["keep.txt"])
        self.assertEqual((batch / "keep.txt").read_text(), "stay\n")
        self.assertEqual(world.stub.requests, [])

    def test_an_existing_file_is_refused(self):
        world = self.world()
        batch = world.sandbox.root / "batches"
        batch.write_text("stay\n")
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only", "--batch-dir", str(batch))
        self.assertEqual((done.returncode, done.stdout, done.stderr),
                         (1, "", f"{ARCHIVE.name}: {batch} already exists\n"))
        self.assertEqual(batch.read_text(), "stay\n")
        self.assertEqual(world.stub.requests, [])

    def test_batch_dir_without_selection_only_is_refused(self):
        world = self.world()
        batch = world.sandbox.root / "batches"
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--batch-dir", str(batch))
        self.assertEqual((done.returncode, done.stdout, done.stderr),
                         (2, "", f"{ARCHIVE.name}: --batch-dir needs --selection-only\n"))
        self.assertFalse(batch.exists())
        self.assertEqual(world.stub.requests, [])

    def test_batch_dir_works_with_t3_code_mcp_authorization_unset(self):
        world = self.world()
        batch = world.sandbox.root / "batches"
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only", "--batch-dir", str(batch))
        self.assertEqual((done.returncode, done.stderr, world.stub.requests), (0, "", []))
        self.assertEqual((batch / "batch-1.txt").read_text().splitlines(), sorted(ARCHIVE_CASES[0][2]))
        self.assertEqual(done.stdout, f"{batch / 'batch-1.txt'}\t9\n")

    def test_a_batch_size_below_1_is_an_argparse_error(self):
        world = self.world()
        batch = world.sandbox.root / "batches"
        for value in ("0", "-1"):
            with self.subTest(value=value):
                done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only",
                                         "--batch-dir", str(batch), "--batch-size", value)
                self.assertEqual((done.returncode, done.stdout, world.stub.requests), (2, "", []))
                self.assertIn("--batch-size", done.stderr)
                self.assertFalse(batch.exists())

    def test_without_the_flag_and_without_the_variable_the_exit_is_non_zero_and_the_line_names_the_variable_and_the_flag(self):
        world = self.world()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(world.stub.requests, [])
        self.assertIn("T3_CODE_MCP_AUTHORIZATION", done.stderr)
        self.assertIn("--selection-only", done.stderr)
        self.assertEqual(len(done.stderr.strip().splitlines()), 1)

    def test_a_database_missing_a_needed_table_archives_nothing_and_exits_non_zero(self):
        world = self.world()
        base = sqlite3.connect(world.database)
        base.execute("alter table orchestration_v2_projection_nodes rename to renamed_nodes")
        base.commit()
        base.close()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only",
                                 T3_CODE_MCP_AUTHORIZATION=CREDENTIAL)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(world.stub.requests, [])

    def test_an_ancestor_above_a_deleted_bridge_whose_child_has_a_pending_runless_node_is_not_selected(self):
        threads = (("root", P1, None, (), OLD, ""), ("ancestor", P1, "root", (), OLD, "S"),
                   ("bridge", P1, "ancestor", (), OLD, "D"), ("kid-live", P1, "bridge", (), OLD, "S"))
        nodes = (("kid-live", None, "pending"),)
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE, "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))
        self.assertEqual(done.archived, ())

    def test_a_child_below_a_deleted_bridge_whose_ancestor_has_a_pending_runless_node_is_not_selected(self):
        threads = (("root", P1, None, (), OLD, ""), ("ancestor-live", P1, "root", (), OLD, "S"),
                   ("bridge", P1, "ancestor-live", (), OLD, "D"), ("kid-old", P1, "bridge", (), OLD, "S"))
        nodes = (("ancestor-live", None, "pending"),)
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE, "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))
        self.assertEqual(done.archived, ())

    def test_an_ancestor_above_a_bridge_in_another_project_whose_child_has_a_pending_runless_node_is_not_selected(self):
        threads = (("root", P1, None, (), OLD, ""), ("ancestor", P1, "root", (), OLD, "S"),
                   ("bridge", P2, "ancestor", (), OLD, "S"), ("kid-live", P1, "bridge", (), OLD, "S"))
        nodes = (("kid-live", None, "pending"),)
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE, "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))
        self.assertEqual(done.archived, ())

    def test_a_child_below_a_bridge_in_another_project_whose_ancestor_has_a_pending_runless_node_is_not_selected(self):
        threads = (("root", P1, None, (), OLD, ""), ("ancestor-live", P1, "root", (), OLD, "S"),
                   ("bridge", P2, "ancestor-live", (), OLD, "S"), ("kid-old", P1, "bridge", (), OLD, "S"))
        nodes = (("ancestor-live", None, "pending"),)
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE, "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))
        self.assertEqual(done.archived, ())

    def test_an_ancestor_of_a_deleted_bridge_with_a_pending_runless_node_is_not_selected(self):
        threads = (("root", P1, None, (), OLD, ""), ("ancestor", P1, "root", (), OLD, "S"),
                   ("bridge", P1, "ancestor", (), OLD, "D"))
        nodes = (("bridge", None, "pending"),)
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE, "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))
        self.assertEqual(done.archived, ())

    def test_an_ancestor_of_a_child_with_a_running_run_is_not_selected_with_top_level_settled(self):
        threads = (("top", P1, None, (), OLD, "S"), ("mid", P1, "top", (), OLD, "S"),
                   ("kid", P1, "mid", ("running",), OLD, "S"))
        world = self.world(threads=threads)
        done = world.run(ARCHIVE, "--top-level-settled", "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))
        self.assertEqual(done.archived, ())

    def test_an_ancestor_above_a_deleted_bridge_whose_child_has_a_running_run_is_not_selected_with_top_level_settled(self):
        threads = (("top", P1, None, (), OLD, "S"), ("mid", P1, "top", (), OLD, "S"),
                   ("bridge", P1, "mid", (), OLD, "D"), ("kid", P1, "bridge", ("running",), OLD, "S"))
        world = self.world(threads=threads)
        done = world.run(ARCHIVE, "--top-level-settled", "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))
        self.assertEqual(done.archived, ())

    def test_a_finished_sibling_of_a_child_with_a_running_run_under_the_coordinator_is_still_selected(self):
        threads = (("coord", P1, None, ("running",), RECENT),
                   ("kid-live", P1, "coord", ("running",), OLD, "S"),
                   ("kid-done", P1, "coord", ("completed",), OLD, "S"))
        world = self.world(threads=threads)
        done = world.run(ARCHIVE, "--coordinator", "coord")
        self.assertEqual((done.returncode, done.stdout, done.stderr),
                         (0, "to archive: 1; skipped: live 2, recent 0, under a running thread 0, kept 0\n"
                          "archived 1, failed 0\n", ""))
        self.assertEqual(done.archived, ("kid-done",))

    def test_a_thread_whose_descendant_run_is_completed_is_selected_once_past_the_cutoff(self):
        threads = (("top", P1, None, (), RECENT, ""), ("mid", P1, "top", (), OLD, "S"),
                   ("kid", P1, "mid", ("completed",), OLD, "S"))
        world = self.world(threads=threads)
        done = world.run(ARCHIVE)
        self.assertEqual((done.returncode, done.stdout, done.stderr),
                         (0, "to archive: 2; skipped: live 0, recent 0, under a running thread 0, kept 0\n"
                          "archived 2, failed 0\n", ""))
        self.assertEqual(done.archived, ("mid", "kid"))

    def test_a_settled_thread_whose_run_is_interrupted_cancelled_failed_or_rolled_back_is_selected_when_a_child_of_that_run_is_pending_running_or_waiting(self):
        cases = (
            ("interrupted", "pending", "node"),
            ("interrupted", "pending", "subagent"),
            ("cancelled", "running", "node"),
            ("failed", "waiting", "subagent"),
            ("rolled_back", "pending", "node"),
            ("rolled_back", "running", "subagent"),
        )
        for run_status, child_status, kind in cases:
            with self.subTest(run_status=run_status, child_status=child_status, kind=kind):
                threads = (("worker", P1, None, (run_status,), OLD, "S"),
                           ("task", P1, "worker", (), OLD, "S"))
                if kind == "node":
                    nodes = (("task", "worker-run-0", child_status),)
                    subagents = ()
                else:
                    nodes = ()
                    subagents = (("worker", "worker-run-0", "task", "provider_native", child_status),)
                world = self.world(threads=threads, nodes=nodes, subagents=subagents)
                done = world.run(ARCHIVE, "--top-level-settled", "--selection-only")
                self.assertEqual((done.returncode, done.stderr), (0, ""))
                self.assertEqual(json.loads(done.stdout), ["task", "worker"])

    def test_a_settled_thread_whose_latest_run_is_running_or_preparing_is_not_selected_when_a_child_of_that_run_is_pending_or_waiting(self):
        cases = (
            ("running", "pending", "node"),
            ("preparing", "waiting", "subagent"),
        )
        for run_status, child_status, kind in cases:
            with self.subTest(run_status=run_status, child_status=child_status, kind=kind):
                threads = (("worker", P1, None, (run_status,), OLD, "S"),
                           ("task", P1, "worker", (), OLD, "S"))
                if kind == "node":
                    nodes = (("task", "worker-run-0", child_status),)
                    subagents = ()
                else:
                    nodes = ()
                    subagents = (("worker", "worker-run-0", "task", "provider_native", child_status),)
                world = self.world(threads=threads, nodes=nodes, subagents=subagents)
                done = world.run(ARCHIVE, "--top-level-settled", "--selection-only")
                self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))


class ScanTest(OpsTest):
    def repo(self, doc):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        ops = root / "ops"
        ops.mkdir()
        for label in REQUIRED_SCAN:
            if label.startswith("ops/"):
                (ops / Path(label).name).write_text("echo ok\n")
        document = root / "docs" / "operations.md"
        document.parent.mkdir()
        document.write_text(doc)
        return ops, document

    def test_the_scan_is_silent_for_the_ci_runner_and_a_root_container(self):
        for identity in (Identity("/home/runner", "runner", "localhost"), Identity("/root", "root", "localhost")):
            with self.subTest(user=identity.user):
                self.assertEqual(machine_value_findings(ROOT / "ops", DOC, identity), [])

    def test_the_scan_reports_planted_values_and_ignores_longer_names(self):
        who = Identity("/home/zed", "zed", "box7.lan")
        planted = [f"APPS={who.home}/apps", f"see ~{who.user}/notes", f"mail {who.user}@example.org", f"ssh {who.user}@box7",
                   "http://Box7.tail.example:3773", f"cd /srv/{who.user}", "ping BOX7.LAN", "curl box7:3773/x", "open //box7/share"]
        benign = ["rm -rf squashfs-zed", "def zed(tid):", "top = zed(tid)", "the zed is fast", "/home/zedekiah/x", "/home/zed.bak",
                  "ssh box7", "it runs on box7.", "box70.lan", "unbox7.lan", "@box7x", "box7: a label"]
        ops, document = self.repo("\n".join(planted + benign) + "\n")
        self.assertEqual(machine_value_findings(ops, document, who), [
            "docs/operations.md:1: holds this machine's home directory",
            "docs/operations.md:1: holds this machine's user name",
            "docs/operations.md:2: holds this machine's user name",
            "docs/operations.md:3: holds this machine's user name",
            "docs/operations.md:4: holds this machine's user name",
            "docs/operations.md:4: holds this machine's host name",
            "docs/operations.md:5: holds this machine's host name",
            "docs/operations.md:6: holds this machine's user name",
            "docs/operations.md:7: holds this machine's host name",
            "docs/operations.md:8: holds this machine's host name",
            "docs/operations.md:9: holds this machine's host name",
        ])
        container = Identity("/root", "root", "x")
        ops, document = self.repo('rm -rf "$APPS/squashfs-root" "$NEW"\ntop = root(tid)\n/rootfs\n')
        self.assertEqual(machine_value_findings(ops, document, container), [])
        ops, document = self.repo("cd /root\nls ~root\n")
        self.assertEqual(machine_value_findings(ops, document, container), [
            "docs/operations.md:1: holds this machine's home directory",
            "docs/operations.md:1: holds this machine's user name",
            "docs/operations.md:2: holds this machine's user name",
        ])


if __name__ == "__main__":
    unittest.main()
