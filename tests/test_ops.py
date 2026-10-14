import base64
import dataclasses
import hashlib
import http.server
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal, NamedTuple, Optional, Union

from tests import in_process

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from check import Identity, machine_value_findings  # noqa: E402
NIGHTLY = ROOT / "ops/t3-nightly-update.sh"
ARCHIVE = ROOT / "ops/t3-archive-threads.py"
EXAMPLE = ROOT / "ops/config.example"
DOC = ROOT / "docs/operations.md"

REAL_TOOLS = ("env", "date", "tee", "cat", "sed", "grep", "sort", "tail", "head", "awk",
              "rm", "chmod", "seq", "base64", "openssl")
STUBBED = ("gh", "mise", "pacman", "yay", "pgrep", "pkill", "setsid", "notify-send", "sleep", "kill", "mv", "strings")
FORBIDDEN = ("gh", "mise", "pgrep", "pkill", "yay", "pacman", "setsid", "notify-send", "strings")

HAVE = "0.0.7-nightly.20000101.1"
WANT = "0.0.8-nightly.20000102.1"
REPO = "pingdotgg/t3code"
LATEST_FILTER = r'[.[]|select(.tagName|test("-nightly\\."))]|sort_by(.publishedAt)|last|.tagName'
ISO_TIME = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d"
CREDENTIAL = "test-credential"
SESSION = "session-1"

STUB = r'''
import json, os, re, shutil, sys
from pathlib import Path

STATE = Path(os.environ["OPS_STUB_STATE"])
NAME, ARGS = Path(sys.argv[0]).name, sys.argv[1:]
# Linux gives no process an ID above 4194304, so a signal sent to this one reaches no process.
UNSIGNALABLE_PID = 2**31 - 1
CAPTURED = ("MISE_MINIMUM_RELEASE_AGE", "OPS_RESTART_MARK")
scenario = json.loads((STATE / "scenario.json").read_text())
model = json.loads((STATE / "model.json").read_text())
calls = STATE / "calls.jsonl"
earlier = calls.read_text().splitlines() if calls.exists() else []
with open(calls, "a") as log:
    log.write(json.dumps({"name": NAME, "argv": ARGS, "clock": model["clock"],
                          "env": {k: os.environ[k] for k in CAPTURED if k in os.environ}}) + "\n")


def save():
    (STATE / "model.json").write_text(json.dumps(model))


def alive(p):
    return p["from"] <= model["clock"] and (p["until"] is None or model["clock"] < p["until"])


def after(flag):
    return ARGS[ARGS.index(flag) + 1]


status = 0
if NAME == "sleep":
    model["clock"] += int(ARGS[0])
    save()
elif NAME == "pgrep":
    if any(alive(p) and re.search(ARGS[-1], p["cmd"]) for p in model["procs"]):
        print(UNSIGNALABLE_PID)
    else:
        status = 1
elif NAME == "pkill":
    hit = [p for p in model["procs"] if re.search(ARGS[-1], p["cmd"])]
    status = 0 if any(alive(p) for p in hit) else 1
    model["procs"] = [p for p in model["procs"] if p not in hit]
    save()
elif NAME == "kill":
    if scenario["on_term"] is not None:
        for p in model["procs"]:
            if alive(p):
                p["until"] = model["clock"] + scenario["on_term"]
        save()
elif NAME == "setsid":
    words = Path(ARGS[-1]).read_text().split()
    if words[0] == "up":
        p = {"cmd": ARGS[-1], "from": model["clock"], "until": None}
        if words[1:2] == ["for"]:
            p["until"] = model["clock"] + int(words[2])
        if words[1:2] == ["after"]:
            p["from"] = model["clock"] + int(words[2])
        model["procs"].append(p)
        save()
elif NAME == "gh":
    if ARGS[:2] == ["release", "list"]:
        sys.stdout.write(scenario["latest_tag"] + "\n" if scenario["latest_tag"] else "")
    else:
        asset = STATE / "release" / ARGS[ARGS.index("download") + 1] / after("-p")
        if asset.exists() and after("-O") == "-":
            sys.stdout.write(asset.read_text())
        elif asset.exists():
            shutil.copyfile(asset, after("-O"))
        else:
            if after("-O") != "-":
                Path(after("-O")).write_text("partial")
            status = 1
elif NAME == "mise":
    sys.stdout.write(scenario["mise_output"])
    status = scenario["mise_status"]
elif NAME == "pacman":
    if scenario["cursor_cli"]:
        print("cursor-cli", scenario["cursor_cli"])
    else:
        status = 1
elif NAME == "yay":
    status = scenario["yay_status"]
elif NAME == "mv":
    if 1 + sum(json.loads(line)["name"] == "mv" for line in earlier) in scenario["fail_mv"]:
        status = 1
    else:
        os.execv(scenario["real_mv"], ["mv", *ARGS])
elif NAME == "strings":
    try:
        data = Path(ARGS[-1]).read_bytes()
    except OSError:
        status = 1
    else:
        for run in re.findall(rb"[ -~\t]{%d,}" % int(after("-n")), data):
            print(run.decode())
elif NAME != "notify-send":
    print(NAME, "1.0.0")
sys.exit(status)
'''

APPIMAGE = r'''
import os, sys
if sys.argv[1:] != ["--appimage-extract"] or MODE == "fail":
    sys.exit(1)
os.mkdir("squashfs-root", 0o700)
if MODE == "ok":
    with open("squashfs-root/t3code", "w") as out:
        out.write(BEHAVIOR + "\n")
    os.chmod("squashfs-root/t3code", 0o755)
'''


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


UP, DOWN = "up", "down"
IGNORES_TERM = None
STUB_SCENARIO = {"latest_tag": "", "on_term": 0, "mise_status": 0, "mise_output": "", "cursor_cli": "",
                 "yay_status": 0, "fail_mv": []}


def up_for(seconds):
    return f"up for {seconds}"


def up_after(seconds):
    return f"up after {seconds}"


@dataclasses.dataclass(frozen=True)
class Layout:
    apps: str = "apps"
    app_name: str = "t3code"
    restart_env: str = "apps/restart.env"
    t3_home: str = "t3-home"
    mise_tools: tuple = ("alpha", "npm:@scope/beta")
    version_commands: tuple = ("alpha", "beta", "gamma")


def asset_name(version):
    return f"T3-Code-{version}-x86_64.AppImage"


def sha512_base64(data):
    return base64.b64encode(hashlib.sha512(data).digest()).decode()


class Sandbox:
    def __init__(self, cleanup, layout=Layout(), *, yay=True, missing_commands=(), skip=None):
        def real(tool):
            found = shutil.which(tool)
            if found is None:
                if skip is None:
                    raise RuntimeError(f"{tool} is not installed")
                skip(f"{tool} is not installed")
            return found

        tools = {tool: real(tool) for tool in ("bash", "mv", *REAL_TOOLS)}
        self.layout = layout
        self.root = Path(tempfile.mkdtemp(prefix="ops-"))
        cleanup(shutil.rmtree, self.root, True)
        self.home, self.bin, self.state = self.root / "home", self.root / "bin", self.root / "stub"
        self.apps = self.home / layout.apps
        self.app = self.apps / layout.app_name
        self.new = self.apps / (layout.app_name + ".new")
        self.config = self.root / "ops.conf"
        self.bash = tools["bash"]
        restart_env = self.home / layout.restart_env
        for directory in (self.bin, self.state / "release", self.root / "tmp", self.apps, restart_env.parent):
            directory.mkdir(parents=True, exist_ok=True)
        restart_env.write_text("export OPS_RESTART_MARK=sourced\n")
        for tool in REAL_TOOLS:
            (self.bin / tool).symlink_to(tools[tool])
        stub = self.state / "stub"
        stub.write_text("#!" + sys.executable + " -IS" + STUB)
        stub.chmod(0o755)
        commands = [name for name in layout.version_commands if name not in missing_commands]
        for name in dict.fromkeys([*(name for name in STUBBED if yay or name != "yay"), *commands]):
            (self.bin / name).symlink_to(stub)
        (self.state / "model.json").write_text(json.dumps({"clock": 0, "procs": []}))
        (self.state / "scenario.json").write_text(json.dumps({**STUB_SCENARIO, "real_mv": tools["mv"]}))

    def env(self, **extra):
        return {"PATH": str(self.bin), "HOME": str(self.home), "TMPDIR": str(self.root / "tmp"), "LC_ALL": "C",
                "OPS_STUB_STATE": str(self.state), "OPS_STUB_BIN": str(self.bin),
                # kill is a bash builtin, so no PATH entry can replace it. bash imports this function and finds it first.
                "BASH_FUNC_kill%%": '() { "$OPS_STUB_BIN/kill" "$@"\n}', **extra}

    def config_text(self, drop=(), **overrides):
        values = {"NIGHTLY_APPS_DIR": str(self.apps), "NIGHTLY_APP_NAME": self.layout.app_name,
                  "NIGHTLY_RESTART_ENV": str(self.home / self.layout.restart_env),
                  "NIGHTLY_MISE_TOOLS": " ".join(self.layout.mise_tools),
                  "NIGHTLY_VERSION_COMMANDS": " ".join(self.layout.version_commands),
                  "ARCHIVE_T3_HOME": str(self.home / self.layout.t3_home), **overrides}
        return "".join(f"{key}={value}\n" for key, value in values.items() if key not in drop)

    def write_config(self, path=None, *, text=None, drop=(), **overrides):
        path = Path(path or self.config)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.config_text(drop, **overrides) if text is None else text
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        return path

    def run(self, script, *args, config: Union[bool, Path] = True, **extra_env):
        flag = []
        if config is True and not self.config.exists():
            self.write_config()
        if config is not False:
            flag = ["--config", str(self.config if config is True else config)]
        env = self.env(**extra_env)
        if Path(script).suffix == ".sh":
            return subprocess.run(
                [self.bash, str(script), *flag, *args],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                env=env,
                cwd=self.root,
                timeout=120,
            )
        return in_process.invoke(in_process.Invocation(
            program=script,
            argv=(*flag, *args),
            env=env,
            cwd=self.root,
            stdin="",
        ))

    def _build(self, where, version, behavior):
        where.mkdir(parents=True)
        (where / "t3code").write_text(behavior + "\n")
        (where / "t3code").chmod(0o755)
        if version is not None:
            (where / ".nightly-version").write_text(version + "\n")

    def _update(self, name, change):
        data = json.loads((self.state / name).read_text())
        change(data)
        (self.state / name).write_text(json.dumps(data))

    def install(self, version, behavior=UP, *, running=True):
        self._build(self.app, version, behavior)
        if running:
            self._update("model.json", lambda model: model["procs"].append(
                {"cmd": str(self.app / "t3code"), "from": 0, "until": None}))

    def install_without_version_file(self, version_in_app_asar):
        self.install(None)
        (self.app / "resources").mkdir()
        (self.app / "resources/app.asar").write_bytes(
            b"\x00\x010.0.0-nightly.19980101.1\x00\xfe" + version_in_app_asar.encode() + b"\x00\xff0.0.1-nightly.19990101.1\x00short\x00")

    def stage(self, version, behavior=UP):
        self._build(self.new, version, behavior)

    def publish(self, version, behavior=UP, *, unpack: Literal["ok", "fail", "empty"] = "ok",
                checksum: Literal["right", "wrong"] = "right", asset=True):
        release = self.state / "release" / ("v" + version)
        release.mkdir(parents=True)
        image = ("#!" + sys.executable + " -IS\n" + f"MODE, BEHAVIOR = {unpack!r}, {behavior!r}" + APPIMAGE).encode()
        if asset:
            (release / asset_name(version)).write_bytes(image)
        listed = sha512_base64(image if checksum == "right" else b"another file")
        (release / "nightly-linux.yml").write_text(
            f"version: {version}\nfiles:\n  - url: {asset_name(version)}\n    sha512: {listed}\n    size: {len(image)}\n")
        self.scenario(latest_tag="v" + version)

    def scenario(self, **changes):
        unknown = sorted(set(changes) - set(STUB_SCENARIO))
        if unknown:
            raise TypeError(f"no stub scenario key {unknown}")
        self._update("scenario.json", lambda scenario: scenario.update(changes))

    def calls(self, name=None):
        log = self.state / "calls.jsonl"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [call for call in calls if name is None or call["name"] == name]

    def trace(self, *names):
        return [" ".join([str(call["clock"]), call["name"], *call["argv"]]).replace(str(self.app), "APP")
                for call in self.calls() if not names or call["name"] in names]

    def clock(self):
        return json.loads((self.state / "model.json").read_text())["clock"]

    def mask(self, text):
        text = re.sub(ISO_TIME, "<time>", text.replace(str(self.root), "<root>"))
        return re.sub(r"(\.prev-\d+\.\d+)\.\d{6}(?!\d)", r"\1.<hhmmss>", text)

    def tree(self, masked=False):
        mask = self.mask if masked else (lambda text: text)
        tree = {}
        for path in sorted(self.home.rglob("*")):
            mode = stat.S_IMODE(path.lstat().st_mode)
            if path.is_symlink():
                entry = ("link", mode, mask(os.readlink(path)))
            elif path.is_dir():
                entry = ("dir", mode, "")
            else:
                entry = ("file", mode, hashlib.sha256(mask(path.read_bytes().decode("latin-1")).encode("latin-1")).hexdigest())
            tree[mask(path.relative_to(self.home).as_posix())] = entry
        return tree

    def log_lines(self):
        log = self.apps / "t3-nightly-update.log"
        return [re.sub(f"^{ISO_TIME} ", "", line) for line in log.read_text().splitlines()] if log.exists() else []

    def state_file(self):
        state = self.apps / "t3-nightly-update.state"
        return dict(line.split("=", 1) for line in state.read_text().splitlines()) if state.exists() else None

    def observed(self, completed):
        return {"returncode": completed.returncode, "stdout": self.mask(completed.stdout),
                "stderr": self.mask(completed.stderr), "calls": json.loads(self.mask(json.dumps(self.calls()))),
                "tree": self.tree(masked=True), "clock": self.clock()}


@dataclasses.dataclass(frozen=True)
class Scenario:
    step: str
    prepare: Callable[[Sandbox], None]
    options: dict = dataclasses.field(default_factory=dict)


def _with(method, *args, **kwargs):
    return lambda box: getattr(box, method)(*args, **kwargs)


def _do(*steps):
    return lambda box: [step(box) for step in steps] and None


def _stale_trees(box):
    (box.new / "stale").write_text("stale\n")
    (box.apps / "squashfs-root").mkdir()
    (box.apps / "squashfs-root/stale").write_text("stale\n")


def _cached_download(box):
    shutil.copyfile(box.state / "release" / ("v" + WANT) / asset_name(WANT), box.apps / asset_name(WANT))


_INSTALLED = _with("install", HAVE)
_PUBLISHED = _do(_INSTALLED, _with("publish", WANT))
_STAGED = _do(_INSTALLED, _with("stage", WANT))
_OUTDATED = _do(_PUBLISHED, _with("scenario", mise_output="alpha 1.0.0 1.1.0\n", cursor_cli="3.2.1"))

NIGHTLY_SCENARIOS = {
    "check": Scenario("check", _OUTDATED),
    "check-staged": Scenario("check", _do(_OUTDATED, _with("stage", WANT))),
    "check-asar": Scenario("check", _do(_with("install_without_version_file", HAVE), _with("publish", WANT))),
    "check-staged-unmarked": Scenario("check", _do(_OUTDATED, _with("stage", None))),
    "clis": Scenario("clis", _with("scenario", mise_output="upgraded alpha\n")),
    "clis-upgrade-fails": Scenario("clis", _with("scenario", mise_output="no such tool\n", mise_status=1)),
    "clis-cursor": Scenario("clis", _with("scenario", cursor_cli="3.2.1")),
    "clis-cursor-fails": Scenario("clis", _with("scenario", cursor_cli="3.2.1", yay_status=1)),
    "clis-no-yay": Scenario("clis", _with("scenario", cursor_cli="3.2.1"), {"yay": False}),
    "clis-last-missing": Scenario("clis", _do(), {"missing_commands": ("gamma",)}),
    "clis-middle-missing": Scenario("clis", _do(), {"missing_commands": ("beta",)}),
    "stage": Scenario("stage", _PUBLISHED),
    "stage-current": Scenario("stage", _do(_INSTALLED, _with("publish", HAVE))),
    "stage-unreadable": Scenario("stage", _INSTALLED),
    "stage-cached": Scenario("stage", _do(_PUBLISHED, _cached_download)),
    "stage-wrong-checksum": Scenario("stage", _do(_INSTALLED, _with("publish", WANT, checksum="wrong"))),
    "stage-no-manifest": Scenario("stage", _do(_PUBLISHED, lambda box: (box.state / "release" / ("v" + WANT) / "nightly-linux.yml").unlink())),
    "stage-download-fails": Scenario("stage", _do(_INSTALLED, _with("publish", WANT, asset=False))),
    "stage-unpack-fails": Scenario("stage", _do(_INSTALLED, _with("publish", WANT, unpack="fail"))),
    "stage-no-t3code": Scenario("stage", _do(_INSTALLED, _with("publish", WANT, unpack="empty"))),
    "stage-stale": Scenario("stage", _do(_PUBLISHED, _with("stage", "0.0.6-nightly.19991231.1", DOWN), _stale_trees)),
    "stage-move-fails": Scenario("stage", _do(_PUBLISHED, _with("scenario", fail_mv=[2]))),
    "restart": Scenario("restart", _STAGED),
    "restart-nothing-staged": Scenario("restart", _INSTALLED),
    "restart-staged-not-executable": Scenario("restart", _do(_STAGED, lambda box: (box.new / "t3code").chmod(0o644))),
    "restart-ignores-term": Scenario("restart", _do(_STAGED, _with("scenario", on_term=IGNORES_TERM))),
    "restart-not-running": Scenario("restart", _do(_with("install", HAVE, running=False), _with("stage", WANT))),
    "restart-slow-start": Scenario("restart", _do(_INSTALLED, _with("stage", WANT, up_after(3)))),
    "restart-no-start": Scenario("restart", _do(_INSTALLED, _with("stage", WANT, DOWN))),
    "restart-dies": Scenario("restart", _do(_INSTALLED, _with("stage", WANT, up_for(10)))),
    "restart-down": Scenario("restart", _do(_with("install", HAVE, DOWN), _with("stage", WANT, DOWN))),
    "restart-rollback-move-fails": Scenario("restart", _do(_INSTALLED, _with("stage", WANT, DOWN), _with("scenario", fail_mv=[3]))),
    "restart-no-env": Scenario("restart", _do(_STAGED, lambda box: (box.home / box.layout.restart_env).unlink())),
    "restart-prev-taken": Scenario("restart", _do(_STAGED, lambda box: Path(f"{box.app}.prev-20000101.1").mkdir())),
    "restart-move-fails": Scenario("restart", _do(_STAGED, _with("scenario", fail_mv=[1]))),
    "restart-swap-fails": Scenario("restart", _do(_STAGED, _with("scenario", fail_mv=[2]))),
}


def arrange(cleanup, name, layout=Layout(), skip=None):
    scenario = NIGHTLY_SCENARIOS[name]
    box = Sandbox(cleanup, layout, skip=skip, **scenario.options)
    scenario.prepare(box)
    return box


def play(cleanup, name, script=NIGHTLY, layout=Layout(), config=True, skip=None):
    box = arrange(cleanup, name, layout, skip)
    return box, box.run(script, NIGHTLY_SCENARIOS[name].step, config=config)


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
    def __init__(self, cleanup, layout=Layout(), projects=PROJECTS, threads=THREADS, nodes=(), subagents=(), skip=None):
        self.sandbox = Sandbox(cleanup, layout, skip=skip)
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

    def arrange(self, name):
        return arrange(self.addCleanup, name, skip=self.skipTest)

    def play(self, name):
        return play(self.addCleanup, name, skip=self.skipTest)

    def world(self, **fixture):
        return ArchiveWorld(self.addCleanup, skip=self.skipTest, **fixture)

    def assertRefused(self, done, script, problem):
        self.assertEqual((done.returncode, done.stdout, done.stderr), (78, "", f"{script.name}: {problem}\n"))


PROBE = {NIGHTLY: ("check",), ARCHIVE: ("--project", "fixture project", "--dry-run")}
RAN = {NIGHTLY: "t3 installed: \nt3 latest:    \n", ARCHIVE: ARCHIVE_CASES[0][1] + "\n"}
PREFIX = {NIGHTLY: "NIGHTLY_", ARCHIVE: "ARCHIVE_"}
FIRST_KEY = {NIGHTLY: "NIGHTLY_APPS_DIR", ARCHIVE: "ARCHIVE_T3_HOME"}

ACCEPTED, NO_OWN_KEY = object(), object()
READER_CASES = (
    ("base", lambda base: base, ACCEPTED),
    ("crlf", lambda base: base.replace("\n", "\r\n"), ACCEPTED),
    ("no final newline", lambda base: base.rstrip("\n"), ACCEPTED),
    ("comment and blank", lambda base: "# c\n\n   \t\n  # d\n" + base, ACCEPTED),
    ("leading blanks", lambda base: base.replace("NIGHTLY_APP_NAME", "  \tNIGHTLY_APP_NAME").replace("ARCHIVE_T3_HOME", " \tARCHIVE_T3_HOME"), ACCEPTED),
    ("other key", lambda base: base + "SOMETHING_ELSE=1 2 = 3\n", ACCEPTED),
    ("empty other value", lambda base: base + "X=\n", ACCEPTED),
    ("number sign inside a value", lambda base: base + "X=1 # not a comment\n", ACCEPTED),
    ("lone carriage return inside", lambda base: base + "X=1\rY=2\n", ACCEPTED),
    ("comment that is not UTF-8", lambda base: base.encode() + b"# \xff\xfe\n", ACCEPTED),
    ("space before equals", lambda base: base + "X =1\n", "line 7 is not KEY=value"),
    ("no equals", lambda base: base + "just words\n", "line 7 is not KEY=value"),
    ("lowercase key", lambda base: base + "x=1\n", "line 7 is not KEY=value"),
    ("digit first", lambda base: base + "1X=1\n", "line 7 is not KEY=value"),
    ("empty key", lambda base: base + "=1\n", "line 7 is not KEY=value"),
    ("export prefix", lambda base: base + "export X=1\n", "line 7 is not KEY=value"),
    ("key that is not UTF-8", lambda base: base.encode() + b"K\xff=1\n", "line 7 is not KEY=value"),
    ("other key twice", lambda base: base + "X=1\nX=1\n", "line 8 sets X a second time"),
    ("archive key twice", lambda base: base + "ARCHIVE_T3_HOME=/a/t3\n", "line 7 sets ARCHIVE_T3_HOME a second time"),
    ("nightly key twice", lambda base: base + "NIGHTLY_APP_NAME=t3code\n", "line 7 sets NIGHTLY_APP_NAME a second time"),
    ("empty file", lambda base: "", NO_OWN_KEY),
)


class ConfigTest(OpsTest):
    def probe(self, world, script, config=True, **env):
        return world.sandbox.run(script, *PROBE[script], config=config, **env)

    def assertRan(self, done, script):
        self.assertEqual((done.returncode, done.stdout[:len(RAN[script])], done.stderr), (0, RAN[script], ""))

    def test_no_config_prints_one_line_naming_the_file_and_changes_nothing(self):
        invocations = {
            NIGHTLY: ((), ("check",), ("clis",), ("stage",), ("restart",), ("no-such-step",)),
            ARCHIVE: ((), ("--help",), ("--project", "fixture project"), ("--project", "fixture project", "--top-level-settled")),
        }
        world = self.world()
        box = world.sandbox
        box.install(HAVE)
        box.stage(WANT)
        box.publish(WANT)
        before = box.tree()
        for script, argument_lists in invocations.items():
            for arguments in argument_lists:
                with self.subTest(script=script.name, arguments=arguments):
                    done = box.run(script, *arguments, config=False, T3_CODE_MCP_AUTHORIZATION=CREDENTIAL)
                    self.assertRefused(done, script, f"no config file at {box.home}/.config/pstack-t3/ops.conf. "
                                                     "Copy ops/config.example to that path and set every key.")
                    self.assertEqual(box.tree(), before)
                    self.assertEqual(box.calls(), [])
                    self.assertEqual(world.stub.requests, [])

    def test_xdg_config_home_is_used_only_when_it_is_absolute(self):
        for script in PROBE:
            with self.subTest(script=script.name):
                world = self.world()
                box = world.sandbox
                box.write_config(box.root / "xdg/pstack-t3/ops.conf")
                self.assertRan(self.probe(world, script, config=False, XDG_CONFIG_HOME=str(box.root / "xdg")), script)
                self.assertRefused(self.probe(world, script, config=False, XDG_CONFIG_HOME="xdg"), script,
                                   f"no config file at {box.home}/.config/pstack-t3/ops.conf. "
                                   "Copy ops/config.example to that path and set every key.")

    def test_environment_variable_names_the_file(self):
        for script in PROBE:
            with self.subTest(script=script.name):
                world = self.world()
                box = world.sandbox
                named = box.root / "named.conf"
                self.assertRefused(self.probe(world, script, config=False, PSTACK_T3_OPS_CONFIG=""), script,
                                   f"no config file at {box.home}/.config/pstack-t3/ops.conf. "
                                   "Copy ops/config.example to that path and set every key.")
                box.write_config(box.home / ".config/pstack-t3/ops.conf")
                self.assertRan(self.probe(world, script, config=False, PSTACK_T3_OPS_CONFIG=""), script)
                self.assertRefused(self.probe(world, script, config=False, PSTACK_T3_OPS_CONFIG=str(named)), script,
                                   f"no config file at {named}. Copy ops/config.example to that path and set every key.")
                box.write_config(named)
                (box.home / ".config/pstack-t3/ops.conf").unlink()
                self.assertRan(self.probe(world, script, config=False, PSTACK_T3_OPS_CONFIG=str(named)), script)

    def test_flag_wins_and_a_missing_flag_file_is_not_replaced(self):
        for script in PROBE:
            with self.subTest(script=script.name):
                world = self.world()
                box = world.sandbox
                box.write_config(box.home / ".config/pstack-t3/ops.conf")
                named = box.write_config(box.root / "named.conf")
                flagged = box.root / "flagged.conf"
                self.assertRefused(self.probe(world, script, config=flagged, PSTACK_T3_OPS_CONFIG=str(named)), script,
                                   f"no config file at {flagged}. Copy ops/config.example to that path and set every key.")
                box.write_config(flagged)
                named.write_text("not a config\n")
                (box.home / ".config/pstack-t3/ops.conf").write_text("not a config\n")
                self.assertRan(self.probe(world, script, config=flagged, PSTACK_T3_OPS_CONFIG=str(named)), script)

    def test_config_flag_with_no_file_exits_2(self):
        world = self.world()
        for script in PROBE:
            with self.subTest(script=script.name):
                done = world.sandbox.run(script, "--config", config=False)
                self.assertEqual((done.returncode, done.stdout, done.stderr), (2, "", f"{script.name}: --config needs a file\n"))

    def test_a_config_path_that_is_a_directory_is_refused_and_a_dangling_link_is_no_config(self):
        world = self.world()
        link = world.sandbox.root / "link.conf"
        link.symlink_to(world.sandbox.root / "absent.conf")
        for script in PROBE:
            with self.subTest(script=script.name):
                self.assertRefused(self.probe(world, script, config=world.sandbox.home), script,
                                   f"config file {world.sandbox.home} is not a readable file")
                self.assertRefused(self.probe(world, script, config=link), script,
                                   f"no config file at {link}. Copy ops/config.example to that path and set every key.")

    def test_both_readers_accept_and_refuse_the_same_files(self):
        world = self.world()
        box = world.sandbox
        for name, build, expected in READER_CASES:
            file = box.write_config(text=build(box.config_text()))
            for script in PROBE:
                with self.subTest(case=name, script=script.name):
                    done = self.probe(world, script)
                    if expected is ACCEPTED:
                        self.assertRan(done, script)
                    else:
                        problem = "does not set " + FIRST_KEY[script] if expected is NO_OWN_KEY else expected
                        self.assertRefused(done, script, f"config file {file} {problem}")

    def test_a_script_refuses_an_unknown_key_with_its_prefix_and_ignores_other_keys(self):
        world = self.world()
        box = world.sandbox
        for line, refuser in (("NIGHTLY_LOG=/x", NIGHTLY), ("ARCHIVE_DB=/x", ARCHIVE), ("OTHER=1", None)):
            file = box.write_config(text=box.config_text() + line + "\n")
            for script in PROBE:
                with self.subTest(line=line, script=script.name):
                    done = self.probe(world, script)
                    if script == refuser:
                        key = line.split("=")[0]
                        self.assertRefused(done, script, f"config file {file} line 7 sets {key}, and this script has no such key")
                    else:
                        self.assertRan(done, script)

    def test_the_example_is_read_by_both_scripts_and_each_of_its_keys_is_required_by_one_script(self):
        box = self.world().sandbox
        example = EXAMPLE.read_text()
        keys = re.findall(r"^([A-Z][A-Z0-9_]*)=", example, re.MULTILINE)
        self.assertEqual(len(keys), 6)
        nightly = box.run(NIGHTLY, "check", config=EXAMPLE)
        self.assertEqual((nightly.returncode, nightly.stdout[:len(RAN[NIGHTLY])], nightly.stderr), (0, RAN[NIGHTLY], ""))
        self.assertEqual(box.calls("mise")[0]["argv"], ["outdated", "first-tool", "second-tool", "npm:@scope/third-tool"])
        archive = box.run(ARCHIVE, *PROBE[ARCHIVE], config=EXAMPLE)
        self.assertEqual((archive.returncode, archive.stdout), (1, ""))
        self.assertIn("sqlite3.OperationalError", archive.stderr)
        with_every_key = {NIGHTLY: nightly.returncode, ARCHIVE: archive.returncode}
        for key in keys:
            file = box.write_config(text="".join(line for line in example.splitlines(True) if not line.startswith(key + "=")))
            for script in PROBE:
                with self.subTest(key=key, script=script.name):
                    done = box.run(script, *PROBE[script], config=file)
                    if key.startswith(PREFIX[script]):
                        self.assertRefused(done, script, f"config file {file} does not set {key}")
                    else:
                        self.assertEqual(done.returncode, with_every_key[script])

    def test_nightly_refuses_a_value_outside_its_characters(self):
        path = "It must start with / and use only the characters A-Za-z0-9._/-."
        word = "It must start with [A-Za-z0-9] and use only the characters "
        cases = (
            ("NIGHTLY_APPS_DIR", "a/apps", "puts 'a/apps' in NIGHTLY_APPS_DIR. " + path),
            ("NIGHTLY_APPS_DIR", '"/a/apps"', "puts '\"/a/apps\"' in NIGHTLY_APPS_DIR. " + path),
            ("NIGHTLY_APPS_DIR", "/a/my apps", "puts '/a/my apps' in NIGHTLY_APPS_DIR. " + path),
            ("NIGHTLY_RESTART_ENV", "$HOME/restart.env", "puts '$HOME/restart.env' in NIGHTLY_RESTART_ENV. " + path),
            ("NIGHTLY_APP_NAME", "..", "puts '..' in NIGHTLY_APP_NAME. " + word + "A-Za-z0-9._-."),
            ("NIGHTLY_APP_NAME", "a/b", "puts 'a/b' in NIGHTLY_APP_NAME. " + word + "A-Za-z0-9._-."),
            ("NIGHTLY_MISE_TOOLS", "alpha -rf", "puts '-rf' in NIGHTLY_MISE_TOOLS. " + word + "A-Za-z0-9._:@/+-."),
            ("NIGHTLY_MISE_TOOLS", "alpha *", "puts '*' in NIGHTLY_MISE_TOOLS. " + word + "A-Za-z0-9._:@/+-."),
            ("NIGHTLY_VERSION_COMMANDS", "alpha /bin/beta", "puts '/bin/beta' in NIGHTLY_VERSION_COMMANDS. " + word + "A-Za-z0-9._+-."),
            ("NIGHTLY_MISE_TOOLS", "", "gives NIGHTLY_MISE_TOOLS no value"),
            ("NIGHTLY_VERSION_COMMANDS", " \t", "gives NIGHTLY_VERSION_COMMANDS no value"),
        )
        box = self.arrange("restart")
        before = box.tree()
        for key, value, problem in cases:
            with self.subTest(key=key, value=value):
                file = box.write_config(**{key: value})
                for step in ("check", "restart"):
                    self.assertRefused(box.run(NIGHTLY, step), NIGHTLY, f"config file {file} {problem}")
                self.assertEqual(box.calls(), [])
                self.assertEqual(box.tree(), before)

    def test_archive_refuses_a_t3_home_that_is_relative_or_holds_a_uri_character(self):
        world = self.world()
        for value in ("t3-home", "/a/t3?mode=rw", "/a/t3#x", "/a/t 3", ""):
            with self.subTest(value=value):
                file = world.sandbox.write_config(ARCHIVE_T3_HOME=value)
                self.assertRefused(world.sandbox.run(ARCHIVE, "--project", "fixture project"), ARCHIVE,
                                   f"config file {file} puts '{value}' in ARCHIVE_T3_HOME. "
                                   "It must start with / and use only the characters A-Za-z0-9._/-.")
                self.assertEqual(world.stub.requests, [])

    def test_blanks_around_a_value_and_between_list_words_are_dropped(self):
        world = self.world()
        box = world.sandbox
        box.install(HAVE)
        box.write_config(NIGHTLY_APPS_DIR=f" \t{box.apps} \t", NIGHTLY_MISE_TOOLS="\talpha \t  npm:@scope/beta  ")
        done = box.run(NIGHTLY, "check")
        self.assertEqual((done.returncode, done.stdout.splitlines()[0]), (0, "t3 installed: " + HAVE))
        self.assertEqual(box.calls("mise")[0]["argv"], ["outdated", "alpha", "npm:@scope/beta"])
        box.write_config(text=box.config_text(ARCHIVE_T3_HOME=f"  {box.home}/t3-home \t").replace("\n", "\r\n"))
        self.assertRan(self.probe(world, ARCHIVE), ARCHIVE)


class NightlyTest(OpsTest):
    def test_no_step_and_an_unknown_step_print_the_header_comment_and_exit_2(self):
        lines = NIGHTLY.read_text().splitlines()
        end = next(number for number, line in enumerate(lines) if number and not line.startswith("#"))
        box = self.arrange("restart")
        before = box.tree()
        for arguments in ((), ("no-such-step",)):
            with self.subTest(arguments=arguments):
                done = box.run(NIGHTLY, *arguments)
                self.assertEqual((done.returncode, done.stdout, done.stderr), (2, "\n".join(lines[1:end]) + "\n", ""))
        self.assertEqual(box.calls(), [])
        self.assertEqual(box.tree(), before)

    def test_check_prints_versions_and_changes_nothing(self):
        box = self.arrange("check")
        before = box.tree()
        done = box.run(NIGHTLY, "check")
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(done.stdout.splitlines(), [
            "t3 installed: " + HAVE, "t3 latest:    " + WANT, "clis outdated:", "  alpha 1.0.0 1.1.0", "cursor-cli: 3.2.1"])
        self.assertEqual([(call["name"], call["argv"], call["env"]) for call in box.calls()], [
            ("gh", ["release", "list", "-R", REPO, "--limit", "40", "--json", "tagName,publishedAt", "-q", LATEST_FILTER], {}),
            ("mise", ["outdated", "alpha", "npm:@scope/beta"], {"MISE_MINIMUM_RELEASE_AGE": "0"}),
            ("pacman", ["-Q", "cursor-cli"], {})])
        self.assertEqual(box.tree(), before)

    def test_check_prints_the_staged_version_when_a_build_is_staged(self):
        _, done = self.play("check-staged")
        self.assertEqual(done.stdout.splitlines(), [
            "t3 installed: " + HAVE, "t3 latest:    " + WANT, "t3 staged:    " + WANT,
            "clis outdated:", "  alpha 1.0.0 1.1.0", "cursor-cli: 3.2.1"])

    def test_check_prints_unknown_for_a_staged_build_with_no_version_file(self):
        _, done = self.play("check-staged-unmarked")
        self.assertEqual((done.returncode, done.stderr, done.stdout.splitlines()[2]), (0, "", "t3 staged:    unknown"))

    def test_installed_version_falls_back_to_the_last_sorted_version_string_in_app_asar(self):
        box, done = self.play("check-asar")
        self.assertEqual(done.stdout.splitlines()[0], "t3 installed: " + HAVE)
        self.assertEqual(box.trace("strings"), ["0 strings -n 20 APP/resources/app.asar"])

    def test_clis_upgrades_the_configured_tools_with_no_hold_and_logs_each_version(self):
        box, done = self.play("clis")
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual([(call["argv"], call["env"]) for call in box.calls("mise")],
                         [(["upgrade", "alpha", "npm:@scope/beta"], {"MISE_MINIMUM_RELEASE_AGE": "0"})])
        self.assertEqual(box.log_lines(), [
            "clis: upgrading alpha npm:@scope/beta", "upgraded alpha", "clis: mise upgrade ok",
            "clis: alpha alpha 1.0.0", "clis: beta beta 1.0.0", "clis: gamma gamma 1.0.0"])
        self.assertEqual(box.trace("alpha", "beta", "gamma"), ["0 alpha --version", "0 beta --version", "0 gamma --version"])

    def test_clis_logs_a_failed_upgrade_and_still_logs_versions(self):
        box, done = self.play("clis-upgrade-fails")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(box.log_lines(), [
            "clis: upgrading alpha npm:@scope/beta", "no such tool", "clis: mise upgrade FAILED, see log",
            "clis: alpha alpha 1.0.0", "clis: beta beta 1.0.0", "clis: gamma gamma 1.0.0"])

    def test_clis_runs_yay_only_when_yay_exists_and_pacman_has_cursor_cli(self):
        for name, traced, logged in (
            ("clis-cursor", ["0 pacman -Q cursor-cli", "0 yay -S --needed --noconfirm cursor-cli"], ["clis: cursor-cli ok"]),
            ("clis-no-yay", [], []),
            ("clis", ["0 pacman -Q cursor-cli"], []),
        ):
            with self.subTest(scenario=name):
                box, done = self.play(name)
                self.assertEqual(done.returncode, 0)
                self.assertEqual(box.trace("pacman", "yay"), traced)
                self.assertEqual([line for line in box.log_lines() if "cursor-cli" in line], logged)

    def test_clis_logs_a_failed_cursor_cli_upgrade(self):
        box, done = self.play("clis-cursor-fails")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(box.log_lines()[2:4], ["clis: cursor-cli FAILED, see log", "clis: alpha alpha 1.0.0"])

    def test_clis_exits_1_when_the_last_listed_command_is_missing(self):
        box, done = self.play("clis-last-missing")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(box.log_lines()[-2:], ["clis: alpha alpha 1.0.0", "clis: beta beta 1.0.0"])
        box, done = self.play("clis-middle-missing")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(box.log_lines()[-2:], ["clis: alpha alpha 1.0.0", "clis: gamma gamma 1.0.0"])

    def test_stage_exits_3_and_removes_the_state_file_when_already_on_the_latest(self):
        box = self.arrange("stage-current")
        (box.apps / "t3-nightly-update.state").write_text("from=a\nto=b\n")
        done = box.run(NIGHTLY, "stage")
        self.assertEqual(done.returncode, 3)
        self.assertEqual(box.log_lines(), ["stage: already on " + HAVE])
        self.assertIsNone(box.state_file())
        self.assertEqual([call["argv"][:2] for call in box.calls("gh")], [["release", "list"]])
        self.assertFalse(box.new.exists())

    def test_stage_exits_1_when_the_latest_nightly_cannot_be_read(self):
        box = self.arrange("stage-unreadable")
        before = box.tree()
        done = box.run(NIGHTLY, "stage")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(box.log_lines(), ["stage: cannot read the latest nightly"])
        after = box.tree()
        self.assertEqual(after.pop("apps/t3-nightly-update.log")[0], "file")
        self.assertEqual(after, before)

    def test_stage_downloads_verifies_and_unpacks_beside_the_installed_build(self):
        box = self.arrange("stage")
        before = box.tree()
        done = box.run(NIGHTLY, "stage")
        file = asset_name(WANT)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual([call["argv"] for call in box.calls("gh")], [
            ["release", "list", "-R", REPO, "--limit", "40", "--json", "tagName,publishedAt", "-q", LATEST_FILTER],
            ["release", "download", "v" + WANT, "-R", REPO, "-p", file, "-O", file + ".part"],
            ["release", "download", "v" + WANT, "-R", REPO, "-p", "nightly-linux.yml", "-O", "-"]])
        self.assertEqual(box.trace("mv"), [f"0 mv -f {file}.part {file}", f"0 mv -T {box.apps}/squashfs-root APP.new"])
        after = box.tree()
        self.assertEqual({path: entry for path, entry in after.items() if path in before}, before)
        self.assertEqual(sorted(set(after) - set(before)), [
            "apps/" + file, "apps/t3-nightly-update.log", "apps/t3-nightly-update.state",
            "apps/t3code.new", "apps/t3code.new/.nightly-version", "apps/t3code.new/t3code"])
        self.assertEqual(after["apps/t3code.new"][:2], ("dir", 0o755))
        self.assertEqual(after["apps/t3code.new/t3code"][:2], ("file", 0o755))
        self.assertEqual(after["apps/" + file][1] & 0o111, 0o111)
        self.assertEqual(((box.new / "t3code").read_text(), (box.new / ".nightly-version").read_text()), ("up\n", WANT + "\n"))
        state = box.state_file()
        self.assertRegex(state.pop("staged"), f"^{ISO_TIME}$")
        self.assertEqual(state, {"from": HAVE, "to": WANT})
        self.assertEqual(box.log_lines(), [f"stage: {HAVE} -> {WANT}", f"stage: ready at {box.new}"])

    def test_stage_reuses_a_file_it_already_downloaded(self):
        box, done = self.play("stage-cached")
        self.assertEqual(done.returncode, 0)
        self.assertEqual([call["argv"] for call in box.calls("gh")][1:], [
            ["release", "download", "v" + WANT, "-R", REPO, "-p", "nightly-linux.yml", "-O", "-"]])
        self.assertEqual((box.new / ".nightly-version").read_text(), WANT + "\n")

    def test_stage_renames_a_file_with_a_wrong_or_unlisted_checksum_and_stages_nothing(self):
        for name, want in (("stage-wrong-checksum", sha512_base64(b"another file")[:16]), ("stage-no-manifest", "")):
            with self.subTest(scenario=name):
                box, done = self.play(name)
                file = box.apps / asset_name(WANT)
                self.assertEqual(done.returncode, 1)
                self.assertEqual(sorted(path.name for path in box.apps.iterdir()),
                                 sorted([file.name + ".bad", "restart.env", "t3-nightly-update.log", "t3code"]))
                got = sha512_base64(Path(str(file) + ".bad").read_bytes())[:16]
                self.assertEqual(box.log_lines(), [f"stage: {HAVE} -> {WANT}",
                                                   f"stage: checksum mismatch for {file.name} (want '{want}', got '{got}')"])
                self.assertIsNone(box.state_file())

    def test_stage_removes_the_partial_file_when_the_download_fails(self):
        box, done = self.play("stage-download-fails")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(box.log_lines(), [f"stage: {HAVE} -> {WANT}", "stage: download FAILED"])
        self.assertEqual(sorted(path.name for path in box.apps.iterdir()), ["restart.env", "t3-nightly-update.log", "t3code"])
        self.assertEqual(len(box.calls("gh")), 2)

    def test_stage_fails_when_the_unpack_fails_or_holds_no_t3code(self):
        for name, line in (("stage-unpack-fails", "stage: unpack FAILED"), ("stage-no-t3code", "stage: unpacked tree has no t3code")):
            with self.subTest(scenario=name):
                box, done = self.play(name)
                self.assertEqual(done.returncode, 1)
                self.assertEqual(box.log_lines(), [f"stage: {HAVE} -> {WANT}", line])
                self.assertEqual(sorted(path.name for path in box.apps.iterdir()),
                                 sorted([asset_name(WANT), "restart.env", "t3-nightly-update.log", "t3code"]))

    def test_stage_exits_1_and_writes_no_state_when_the_unpacked_tree_cannot_be_moved(self):
        box, done = self.play("stage-move-fails")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(box.log_lines(), [f"stage: {HAVE} -> {WANT}"])
        self.assertEqual(sorted(path.name for path in box.apps.iterdir()),
                         sorted([asset_name(WANT), "restart.env", "squashfs-root", "t3-nightly-update.log", "t3code"]))

    def test_stage_exits_1_when_the_apps_directory_does_not_exist(self):
        box = self.arrange("stage")
        before = box.tree()
        box.write_config(NIGHTLY_APPS_DIR=str(box.root / "absent"))
        done = box.run(NIGHTLY, "stage")
        self.assertEqual(done.returncode, 1)
        self.assertEqual([call["argv"][:2] for call in box.calls("gh")], [["release", "list"]])
        self.assertEqual(box.tree(), before)
        self.assertFalse((box.root / "absent").exists())

    def test_stage_replaces_a_stale_staged_build_and_a_stale_unpack(self):
        box, done = self.play("stage-stale")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(sorted(path.name for path in box.new.iterdir()), [".nightly-version", "t3code"])
        self.assertEqual(((box.new / "t3code").read_text(), (box.new / ".nightly-version").read_text()), ("up\n", WANT + "\n"))
        self.assertFalse((box.apps / "squashfs-root").exists())

    def test_restart_refuses_when_nothing_is_staged_or_the_staged_t3code_is_not_executable(self):
        for name in ("restart-nothing-staged", "restart-staged-not-executable"):
            with self.subTest(scenario=name):
                box = self.arrange(name)
                before = box.tree()
                done = box.run(NIGHTLY, "restart")
                self.assertEqual(done.returncode, 1)
                self.assertEqual(box.log_lines(), ["restart: nothing staged"])
                self.assertEqual(box.calls(), [])
                after = box.tree()
                del after["apps/t3-nightly-update.log"]
                self.assertEqual(after, before)

    def test_restart_swaps_the_staged_build_in_and_keeps_the_previous_one(self):
        box, done = self.play("restart")
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(box.trace(), [
            "0 sleep 5",
            "5 pgrep -o -f ^APP/t3code$",
            "5 kill -TERM 2147483647",
            "5 pgrep -f APP/t3code",
            "5 pkill -KILL -f APP/t3code",
            "5 sleep 1",
            "6 mv -T APP APP.prev-20000101.1",
            "6 mv -T APP.new APP",
            "6 setsid -f APP/t3code",
            "6 sleep 1",
            "7 pgrep -f ^APP/t3code$",
            "7 sleep 15",
            "22 pgrep -f ^APP/t3code$"])
        self.assertEqual(box.calls("setsid")[0]["env"], {"OPS_RESTART_MARK": "sourced"})
        self.assertEqual((box.app / ".nightly-version").read_text(), WANT + "\n")
        self.assertEqual(Path(f"{box.app}.prev-20000101.1/.nightly-version").read_text(), HAVE + "\n")
        self.assertFalse(box.new.exists())
        state = box.state_file()
        self.assertRegex(state.pop("restarted"), f"^{ISO_TIME}$")
        self.assertEqual(state, {"from": HAVE, "to": WANT, "result": "ok", "prev": f"{box.app}.prev-20000101.1"})
        self.assertEqual(list(state), ["from", "to", "result", "prev"])
        self.assertEqual(box.log_lines(), [f"restart: {HAVE} -> {WANT}", f"restart: running {WANT}"])

    def test_restart_kills_after_60_seconds_when_the_old_build_ignores_term(self):
        box, done = self.play("restart-ignores-term")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(box.trace("kill", "pkill", "mv"), [
            "5 kill -TERM 2147483647", "65 pkill -KILL -f APP/t3code",
            "66 mv -T APP APP.prev-20000101.1", "66 mv -T APP.new APP"])
        self.assertEqual(box.trace("pgrep", "sleep")[2:122], [
            line for second in range(5, 65) for line in (f"{second} pgrep -f APP/t3code", f"{second} sleep 1")])
        self.assertEqual(box.state_file()["result"], "ok")

    def test_restart_sends_no_signal_when_t3_was_not_running(self):
        box, done = self.play("restart-not-running")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(box.trace()[:6], [
            "0 sleep 5", "5 pgrep -o -f ^APP/t3code$", "5 pgrep -f APP/t3code", "5 pkill -KILL -f APP/t3code",
            "5 sleep 1", "6 mv -T APP APP.prev-20000101.1"])
        self.assertEqual(box.trace("kill"), [])
        self.assertEqual(box.trace("setsid"), ["6 setsid -f APP/t3code"])
        self.assertEqual(box.state_file()["result"], "ok")

    def test_restart_waits_for_a_build_that_takes_seconds_to_start(self):
        box, done = self.play("restart-slow-start")
        self.assertEqual(done.returncode, 0)
        self.assertEqual(box.trace()[8:], [
            "6 setsid -f APP/t3code",
            "6 sleep 1", "7 pgrep -f ^APP/t3code$", "7 sleep 1", "8 pgrep -f ^APP/t3code$", "8 sleep 1", "9 pgrep -f ^APP/t3code$",
            "9 sleep 15", "24 pgrep -f ^APP/t3code$"])
        self.assertEqual(box.state_file()["result"], "ok")

    def assertRolledBack(self, box, done):
        self.assertEqual(done.returncode, 1)
        self.assertEqual((box.app / ".nightly-version").read_text(), HAVE + "\n")
        self.assertEqual(Path(f"{box.app}.failed-20000102.1/.nightly-version").read_text(), WANT + "\n")
        self.assertEqual(sorted(path.name for path in box.apps.iterdir()), [
            "restart.env", "t3-nightly-update.log", "t3-nightly-update.state", "t3code", "t3code.failed-20000102.1"])
        state = box.state_file()
        self.assertRegex(state.pop("restarted"), f"^{ISO_TIME}$")
        self.assertEqual(state, {"from": HAVE, "to": WANT, "result": "rolled-back"})
        self.assertEqual(box.log_lines(), [
            f"restart: {HAVE} -> {WANT}", f"restart: {WANT} did not stay up; rolling back to {HAVE}",
            f"restart: rolled back, running {HAVE}"])
        self.assertEqual([call["argv"] for call in box.calls("notify-send")], [
            ["-u", "critical", "T3 Code update rolled back", f"{WANT} did not start. Running {HAVE} again."]])

    def test_restart_rolls_back_when_the_new_build_does_not_start(self):
        box, done = self.play("restart-no-start")
        self.assertRolledBack(box, done)
        self.assertEqual(box.trace()[8:], [
            "6 setsid -f APP/t3code",
            *(line for second in range(6, 51) for line in (f"{second} sleep 1", f"{second + 1} pgrep -f ^APP/t3code$")),
            "51 pkill -KILL -f APP/t3code",
            "51 sleep 1",
            "52 mv -T APP APP.failed-20000102.1",
            "52 mv -T APP.prev-20000101.1 APP",
            "52 setsid -f APP/t3code",
            "52 sleep 1",
            "53 pgrep -f ^APP/t3code$",
            f"53 notify-send -u critical T3 Code update rolled back {WANT} did not start. Running {HAVE} again."])

    def test_restart_rolls_back_when_the_new_build_dies_within_15_seconds(self):
        box, done = self.play("restart-dies")
        self.assertRolledBack(box, done)
        self.assertEqual(box.trace()[8:], [
            "6 setsid -f APP/t3code",
            "6 sleep 1",
            "7 pgrep -f ^APP/t3code$",
            "7 sleep 15",
            "22 pgrep -f ^APP/t3code$",
            "22 pkill -KILL -f APP/t3code",
            "22 sleep 1",
            "23 mv -T APP APP.failed-20000102.1",
            "23 mv -T APP.prev-20000101.1 APP",
            "23 setsid -f APP/t3code",
            "23 sleep 1",
            "24 pgrep -f ^APP/t3code$",
            f"24 notify-send -u critical T3 Code update rolled back {WANT} did not start. Running {HAVE} again."])

    def test_restart_reports_down_when_the_rollback_does_not_start(self):
        box, done = self.play("restart-down")
        log = box.apps / "t3-nightly-update.log"
        self.assertEqual(done.returncode, 1)
        self.assertEqual((box.app / ".nightly-version").read_text(), HAVE + "\n")
        self.assertEqual(Path(f"{box.app}.failed-20000102.1/.nightly-version").read_text(), WANT + "\n")
        state = box.state_file()
        self.assertRegex(state.pop("restarted"), f"^{ISO_TIME}$")
        self.assertEqual(state, {"from": HAVE, "to": WANT, "result": "down"})
        self.assertEqual(box.log_lines()[1:], [
            f"restart: {WANT} did not stay up; rolling back to {HAVE}", "restart: T3 Code is DOWN after rollback"])
        self.assertEqual([call["argv"] for call in box.calls("notify-send")], [
            ["-u", "critical", "T3 Code is down", f"The nightly update to {WANT} failed and the rollback did not start. See {log}."]])
        self.assertEqual(box.trace("setsid"), ["6 setsid -f APP/t3code", "52 setsid -f APP/t3code"])
        self.assertEqual(box.clock(), 97)

    def test_restart_leaves_the_new_build_in_place_when_the_rollback_cannot_move_it(self):
        box, done = self.play("restart-rollback-move-fails")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(box.trace("mv"), [
            "6 mv -T APP APP.prev-20000101.1", "6 mv -T APP.new APP", "52 mv -T APP APP.failed-20000102.1"])
        self.assertEqual(((box.app / ".nightly-version").read_text(), Path(f"{box.app}.prev-20000101.1/.nightly-version").read_text()),
                         (WANT + "\n", HAVE + "\n"))
        self.assertEqual(box.trace("setsid"), ["6 setsid -f APP/t3code", "52 setsid -f APP/t3code"])
        self.assertEqual(box.state_file()["result"], "down")

    def test_restart_starts_t3_without_the_environment_file_when_it_is_missing(self):
        box, done = self.play("restart-no-env")
        self.assertEqual(done.returncode, 0)
        self.assertRegex(done.stderr, rf"^{re.escape(str(NIGHTLY))}: line \d+: {re.escape(str(box.apps))}/restart.env: No such file or directory\n$")
        self.assertEqual([(call["argv"], call["env"]) for call in box.calls("setsid")], [(["-f", str(box.app / "t3code")], {})])
        self.assertEqual(box.state_file()["result"], "ok")

    def test_restart_adds_the_time_to_the_previous_name_when_it_is_taken(self):
        box, done = self.play("restart-prev-taken")
        self.assertEqual(done.returncode, 0)
        kept = [path for path in box.apps.iterdir() if re.fullmatch(r"t3code\.prev-20000101\.1\.\d{6}", path.name)]
        self.assertEqual([(path / ".nightly-version").read_text() for path in kept], [HAVE + "\n"])
        self.assertEqual(os.listdir(f"{box.app}.prev-20000101.1"), [])
        self.assertEqual(box.state_file()["prev"], str(kept[0]))

    def test_restart_starts_the_old_build_again_when_it_cannot_be_moved_aside(self):
        box, done = self.play("restart-move-fails")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(box.trace()[6:], [
            "6 mv -T APP APP.prev-20000101.1", "6 setsid -f APP/t3code", "6 sleep 1", "7 pgrep -f ^APP/t3code$"])
        self.assertEqual(((box.app / ".nightly-version").read_text(), (box.new / ".nightly-version").read_text()),
                         (HAVE + "\n", WANT + "\n"))
        self.assertEqual(box.log_lines()[1:], ["restart: could not move the old build aside; starting it again"])
        self.assertIsNone(box.state_file())

    def test_restart_restores_the_old_build_when_the_swap_fails(self):
        box, done = self.play("restart-swap-fails")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(box.trace()[6:], [
            "6 mv -T APP APP.prev-20000101.1", "6 mv -T APP.new APP", "6 mv -T APP.prev-20000101.1 APP",
            "6 setsid -f APP/t3code", "6 sleep 1", "7 pgrep -f ^APP/t3code$"])
        self.assertEqual(((box.app / ".nightly-version").read_text(), (box.new / ".nightly-version").read_text()),
                         (HAVE + "\n", WANT + "\n"))
        self.assertEqual(box.log_lines()[1:], ["restart: swap FAILED, old build restored"])
        self.assertFalse(Path(f"{box.app}.prev-20000101.1").exists())

    def test_restart_follows_another_layout_of_paths_and_names(self):
        elsewhere = Layout(apps="srv/builds", app_name="code-9", restart_env="etc/start.env")
        box, done = play(self.addCleanup, "restart", script=NIGHTLY, layout=elsewhere, skip=self.skipTest)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(box.app, box.home / "srv/builds/code-9")
        self.assertEqual(box.trace("mv", "setsid"), ["6 mv -T APP APP.prev-20000101.1", "6 mv -T APP.new APP", "6 setsid -f APP/t3code"])
        self.assertEqual(box.calls("setsid")[0]["env"], {"OPS_RESTART_MARK": "sourced"})
        self.assertEqual(sorted(path.name for path in box.app.parent.iterdir()),
                         ["code-9", "code-9.prev-20000101.1", "t3-nightly-update.log", "t3-nightly-update.state"])

    def test_one_scenario_played_in_two_sandboxes_is_observed_the_same(self):
        for name in ("stage", "restart-prev-taken", "restart-no-start"):
            with self.subTest(scenario=name):
                first_box, first = self.play(name)
                second_box, second = self.play(name)
                self.assertNotEqual(first_box.root, second_box.root)
                self.assertEqual(first_box.observed(first), second_box.observed(second))

    def test_the_sandbox_path_reaches_no_forbidden_command(self):
        box = Sandbox(self.addCleanup, skip=self.skipTest)
        self.assertEqual(box.env()["PATH"], str(box.bin))
        self.assertNotIn(os.pathsep, str(box.bin))
        self.assertEqual(sorted(os.listdir(box.bin)), sorted([*REAL_TOOLS, *STUBBED, "alpha", "beta", "gamma"]))
        resolve = 'for name; do type -P "$name" || echo "$name is not found"; done'
        found = subprocess.run([box.bash, "-c", resolve, "bash", *FORBIDDEN, "ls"], capture_output=True, text=True, env=box.env())
        self.assertEqual(found.stdout.splitlines(), [*(str(box.bin / name) for name in FORBIDDEN), "ls is not found"])
        for name in FORBIDDEN:
            self.assertEqual(os.readlink(box.bin / name), str(box.state / "stub"))
        bare = Sandbox(self.addCleanup, skip=self.skipTest, yay=False)
        found = subprocess.run([bare.bash, "-c", resolve, "bash", "yay"], capture_output=True, text=True, env=bare.env())
        self.assertEqual(found.stdout, "yay is not found\n")


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


class ScanTest(OpsTest):
    def repo(self, doc):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        ops = root / "ops"
        ops.mkdir()
        for name in ("t3-nightly-update.sh", "t3-archive-threads.py", "config.example"):
            (ops / name).write_text("echo ok\n")
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
