#!/usr/bin/env python3
"""Run the install, the coordinator commands, and one landing against a home this run made.

A stranger clones this repository and runs the install. This script runs that,
plus the coordinator commands, against a home it mints under a temp root. Each
step is one process, so the run proves the environment that process starts
with and the exit status it returns. The stdout of a run that works holds one
ok line per step. The run lands one commit on a throwaway local bare remote.
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[1]
FIXTURE_CATALOG = CHECKOUT / "tests" / "fixtures" / "catalog.json"
AUTHOR_NAMES = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")
README_TEXT = "# outsider smoke\n"
WORK_FIRST = "first line\n"
WORK_TEXT = "first line\nsecond line\n"
WORK_SUMMARY = "Add a second work line"
# One prep command writes a file whose text is its second argument.
WRITER = "import sys\nopen(sys.argv[1], 'w', encoding='utf-8').write(sys.argv[2])\n"


@dataclass(frozen=True)
class Layout:
    """The root, home, catalog, repository, checkout, and bare remote for one run.

    The catalog, the scratch repo, and the remote are siblings of the home.
    """

    root: Path
    home: Path
    catalog: Path
    repo: Path
    checkout: Path
    remote: Path


@dataclass(frozen=True)
class Ref:
    """A value captured from an earlier step's stripped stdout."""

    name: str


@dataclass(frozen=True)
class Expect:
    """A postcondition on a step's stdout. Kind is `is` or `has`."""

    kind: str
    text: str

    def reason(self, actual):
        """None when `actual` meets the postcondition, else a reason string."""
        if self.kind == "is":
            want, got = self.text, actual
            norm_want = want[:-1] if want.endswith("\n") else want
            norm_got = got[:-1] if got.endswith("\n") else got
            if norm_got == norm_want:
                return None
            return f"expected is {self.text!r}, got {actual!r}"
        if self.kind == "has":
            if self.text in actual:
                return None
            return f"expected has {self.text!r}, got {actual!r}"
        return f"unknown kind {self.kind!r}"


@dataclass(frozen=True)
class Setup:
    """One prep command. It prints no ok line and fails with `fail <name>`."""

    name: str
    argv: tuple
    cwd: Path
    capture: str | None = None


@dataclass(frozen=True)
class Step:
    """One command of the plan. `cwd` is where it runs and `argv` is what it runs."""

    name: str
    argv: tuple
    cwd: Path
    capture: str | None = None
    expect: Expect | None = None


@dataclass(frozen=True)
class Created:
    """The device and inode pairs of the entries this run made."""

    inodes: frozenset


class ForeignHome(Exception):
    """A path this run did not create."""

    def __init__(self, path):
        super().__init__(path)
        self.path = path


def plan_names():
    """The name of each step, in order."""
    return tuple(step.name for step in _commands(_stand_in()) if isinstance(step, Step))


def ok_line(name):
    """The success line run_plan prints after a step returns zero."""
    return f"ok {name}\n"


def fail_line(name):
    """The failure line die prints after a command returns nonzero."""
    return f"fail {name}\n"


def _stand_in():
    """A layout whose paths no command reads, so the names come from the one list of steps."""
    return Layout(Path(""), Path(""), Path(""), Path(""), CHECKOUT, Path(""))


def brigade(layout, *args):
    """A brigade command against the restaurant the open step captured."""
    return (
        sys.executable,
        "-I",
        str(CHECKOUT / "skills" / "brigade" / "scripts" / "brigade.py"),
        "--at",
        Ref("restaurant"),
        *args,
    )


def land(layout, *args):
    """A landing command against the scratch repository."""
    return (
        sys.executable,
        "-I",
        str(CHECKOUT / "skills" / "landing" / "scripts" / "land.py"),
        "--repo",
        str(layout.repo),
        *args,
    )


def _worker_script(layout):
    repo, worker = shlex.quote(str(layout.repo)), shlex.quote(str(layout.root / "worker"))
    return (
        "set -e; "
        f"git -C {repo} worktree add {worker} -b smoke/d1 origin/main; "
        f"printf 'first line\\nsecond line\\n' > {worker}/a.txt; "
        f"git -C {worker} add a.txt; "
        f"git -C {worker} commit -m '{WORK_SUMMARY}'; "
        f"git -C {worker} rev-parse HEAD"
    )


def _commands(layout):
    """Every setup and step, in order. Executable and script paths are absolute."""
    python = sys.executable
    document = json.dumps(_catalog_document(), indent=2) + "\n"
    return (
        Setup("catalog", (python, "-I", "-c", WRITER, str(layout.catalog), document), CHECKOUT),
        Setup("init-repo", ("git", "init", "-b", "main", str(layout.repo)), CHECKOUT),
        Setup("write-readme", (python, "-I", "-c", WRITER, str(layout.repo / "README.md"), README_TEXT), CHECKOUT),
        Setup("write-work", (python, "-I", "-c", WRITER, str(layout.repo / "a.txt"), WORK_FIRST), CHECKOUT),
        Setup("add", ("git", "-C", str(layout.repo), "add", "README.md", "a.txt"), CHECKOUT),
        Setup("commit", ("git", "-C", str(layout.repo), "commit", "--message", "outsider smoke"), CHECKOUT),
        Setup("init-remote", ("git", "init", "--bare", str(layout.remote)), CHECKOUT),
        Setup("add-origin", ("git", "-C", str(layout.repo), "remote", "add", "origin", str(layout.remote)), CHECKOUT),
        Setup("push", ("git", "-C", str(layout.repo), "push", "origin", "main"), CHECKOUT),
        Step("install", (python, "-I", str(CHECKOUT / "scripts" / "install.py")), CHECKOUT),
        Step("doctor", (python, "-I", str(CHECKOUT / "scripts" / "install.py"), "doctor"), CHECKOUT),
        Step(
            "roles",
            (
                python,
                "-I",
                str(CHECKOUT / "skills" / "pstack-runtime" / "scripts" / "roles.py"),
                "show",
                "--catalog",
                str(layout.catalog),
                "--parent",
                "grok/grok-4.7",
                "--role",
                "feature, refactoring",
            ),
            CHECKOUT,
        ),
        Step(
            "brigade",
            (
                python,
                "-I",
                str(CHECKOUT / "skills" / "brigade" / "scripts" / "brigade.py"),
                "open",
                "--project-root",
                str(layout.repo),
                "--name",
                "smoke",
            ),
            CHECKOUT,
            capture="restaurant",
        ),
        Step(
            "land",
            (
                python,
                "-I",
                str(CHECKOUT / "skills" / "landing" / "scripts" / "land.py"),
                "--repo",
                str(layout.repo),
                "init",
                "--trunk",
                "main",
                "--mode",
                "push",
                "--remote",
                "origin",
            ),
            CHECKOUT,
        ),
        Step(
            "ticket",
            brigade(layout, "ticket", "add", "--summary", WORK_SUMMARY, "--kind", "feature", "--paths", "a.txt"),
            CHECKOUT,
            expect=Expect("is", "T1"),
        ),
        Step(
            "fire",
            brigade(layout, "fire", "--tickets", "T1", "--station", "feature", "--summary", WORK_SUMMARY, "--paths", "a.txt"),
            CHECKOUT,
            expect=Expect("is", "D1 (lease L1 held by smoke/D1)"),
        ),
        Step("worker", ("sh", "-c", _worker_script(layout)), CHECKOUT, capture="sha"),
        Step(
            "dish",
            brigade(layout, "dish", "D1", "--branch", "smoke/d1", "--sha", Ref("sha"), "--state", "in-review"),
            CHECKOUT,
            expect=Expect("is", "D1 in-review"),
        ),
        Step(
            "pass",
            brigade(
                layout,
                "pass",
                "record",
                "D1",
                "--sha",
                Ref("sha"),
                "--verdict",
                "pass",
                "--author",
                "grok/grok-4.7",
                "--verifier",
                "claude/claude-opus-5-5",
            ),
            CHECKOUT,
            expect=Expect("is", "D1 passed"),
        ),
        Step(
            "submit",
            land(
                layout,
                "submit",
                "--holder",
                "smoke/D1",
                "--branch",
                "smoke/d1",
                "--sha",
                Ref("sha"),
                "--lease",
                "L1",
                "--reviewer",
                "claude/claude-opus-5-5",
            ),
            CHECKOUT,
            expect=Expect("is", "E1"),
        ),
        Step("queue", land(layout, "land"), CHECKOUT, expect=Expect("is", "landed E1 (smoke/D1)")),
        Step(
            "trunk",
            ("git", "--git-dir", str(layout.remote), "show", "main:a.txt"),
            CHECKOUT,
            expect=Expect("is", WORK_TEXT),
        ),
        Step(
            "merged",
            brigade(layout, "dish", "D1", "--state", "merged"),
            CHECKOUT,
            expect=Expect("is", "D1 merged"),
        ),
        Step(
            "close",
            brigade(layout, "close", "--dry-run"),
            CHECKOUT,
            expect=Expect("has", "merged: 1"),
        ),
    )


def require_created(path, created):
    """Raise ForeignHome unless `path` names an entry this run created."""
    try:
        entry = path.lstat()
    except OSError:
        raise ForeignHome(path) from None
    if (entry.st_dev, entry.st_ino) not in created.inodes:
        raise ForeignHome(path)


def entry_of(path):
    """The device and inode pair that names the entry at `path`."""
    entry = path.lstat()
    return entry.st_dev, entry.st_ino


def inside(path, parent):
    """Whether `path` is `parent`, or sits under it, once every symlink is resolved."""
    real, top = Path(os.path.realpath(path)), Path(os.path.realpath(parent))
    return real == top or top in real.parents


def mint_base(inherited):
    """Where the run mints its root. TMPDIR wins unless it sits inside the inherited HOME."""
    base, home = inherited.get("TMPDIR"), inherited.get("HOME")
    if not base:
        return Path("/tmp")
    if home and inside(Path(base), Path(home)):
        return Path("/tmp")
    return Path(base)


def make_layout(root, home):
    """The paths one run uses. The scratch repo may not reach the checkout, and the catalog may not sit in the home."""
    catalog, repo, remote, checkout = root / "catalog.json", root / "repo", root / "remote.git", CHECKOUT
    if inside(repo, checkout) or inside(checkout, repo):
        sys.exit(f"outsider_smoke: {repo} would reach the checkout {checkout}")
    if inside(catalog, home):
        sys.exit(f"outsider_smoke: {catalog} would sit inside the home {home}")
    return Layout(root, home, catalog, repo, checkout, remote)


def child_env(home, inherited):
    """The environment every command of the run inherits. HOME names the home this run made.

    Only PATH, LANG, LC_ names, and the four commit identity names survive.
    GIT_CONFIG_NOSYSTEM=1 disables Git system configuration for the run.
    """
    env = {}
    for name in ("PATH", "LANG", *AUTHOR_NAMES):
        if name in inherited:
            env[name] = inherited[name]
    for name, value in inherited.items():
        if name.startswith("LC_"):
            env[name] = value
    env["HOME"] = str(home)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _catalog_document():
    """The saved orchestrator_capabilities the roles step reads."""
    fixture = json.loads(FIXTURE_CATALOG.read_text(encoding="utf-8"))
    provider = next(item for item in fixture["providers"] if item["providerInstanceId"] == "grok")
    model = next(item for item in provider["models"] if item["id"] == "grok-4.7")
    effort = next(item for item in model["options"] if item["id"] == "reasoningEffort")
    return {
        "inheritedProviderInstanceId": "grok",
        "inheritedModel": "grok-4.7",
        "providers": [{
            "providerInstanceId": "grok",
            "driverKind": provider["driverKind"],
            "displayName": provider["displayName"],
            "canRunChildTask": True,
            "canRunCrossProviderChildTask": True,
            "constraints": [],
            "models": [{"id": "grok-4.7", "label": model["label"], "options": [effort]}],
        }],
    }


def _resolve(argv, captures):
    """Replace every Ref with the value the named capture stored."""
    resolved = []
    for part in argv:
        if isinstance(part, Ref):
            resolved.append(captures[part.name])
        elif isinstance(part, Path):
            resolved.append(str(part))
        else:
            resolved.append(part)
    return tuple(resolved)


def _extract(capture, stdout):
    """The value one capture stores from the stripped stdout."""
    stripped = stdout.strip()
    if capture == "restaurant" and stripped.startswith("opened "):
        return stripped[len("opened "):].strip()
    if capture == "sha":
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        return lines[-1] if lines else ""
    return stripped


def spawn(argv, cwd, env):
    """Run one command and wait for it. Its output is captured, so a failure can quote it."""
    return subprocess.run(argv, cwd=cwd, env=dict(env), text=True, capture_output=True)


def run_plan(entries, env):
    """Run each entry in order. Setups print no ok line. The first failure ends the run."""
    captures = {}
    for entry in entries:
        completed = spawn(_resolve(entry.argv, captures), entry.cwd, env)
        if completed.returncode:
            die(entry.name, completed)
        if entry.capture:
            captures[entry.capture] = _extract(entry.capture, completed.stdout)
        if isinstance(entry, Step) and entry.expect is not None:
            reason = entry.expect.reason(completed.stdout)
            if reason is not None:
                print(fail_line(entry.name), end="", file=sys.stderr)
                print(reason, file=sys.stderr)
                print(completed.stdout, end="", file=sys.stderr)
                print(completed.stderr, end="", file=sys.stderr)
                raise SystemExit(1)
        if isinstance(entry, Step):
            print(ok_line(entry.name), end="")


def die(name, completed):
    """Report a command that failed and end the run. The lines already printed stay where they are."""
    print(fail_line(name), end="", file=sys.stderr)
    print(completed.stderr, end="", file=sys.stderr)
    print(completed.stdout, end="", file=sys.stderr)
    raise SystemExit(1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", help="refuse the run unless this directory is one the run created")
    args = parser.parse_args(argv)
    inherited = dict(os.environ)
    if args.home is not None:
        try:
            require_created(Path(args.home), Created(frozenset()))
        except ForeignHome as refusal:
            print(f"refused: {refusal.path} is not a directory this run created", file=sys.stderr)
            return 1
    if [name for name in AUTHOR_NAMES if not inherited.get(name)]:
        print("git identity is unset", file=sys.stderr)
        return 1
    root = Path(tempfile.mkdtemp(dir=str(mint_base(inherited))))
    home = root / "home"
    home.mkdir()
    created = Created(frozenset((entry_of(root), entry_of(home))))
    layout = make_layout(root, home)
    env = child_env(home, inherited)
    try:
        run_plan(_commands(layout), env)
    except SystemExit:
        print(f"kept {root}", file=sys.stderr)
        raise
    require_created(root, created)
    shutil.rmtree(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
