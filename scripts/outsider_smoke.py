#!/usr/bin/env python3
"""Run the README quick start, and the coordinator commands, against a home this run made.

A stranger clones this repository and runs two commands from the Quick start. This
script runs those two, plus the coordinator commands, against a home it mints under a
temp root. Each step is one process, so the run proves the environment that process
starts with and the exit status it returns. The stdout of a run that works holds one
ok line per step.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[1]
FIXTURE_CATALOG = CHECKOUT / "tests" / "fixtures" / "catalog.json"
AUTHOR_NAMES = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")
DROPPED = ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
           "XDG_RUNTIME_DIR", "XDG_CONFIG_DIRS", "XDG_DATA_DIRS",
           "CLAUDE_CONFIG_DIR", "BRIGADE_STORE", "BRIGADE_DIR")
README_TEXT = "# outsider smoke\n"
# One prep command writes a file whose text is its second argument.
WRITER = "import sys\nopen(sys.argv[1], 'w', encoding='utf-8').write(sys.argv[2])\n"


@dataclass(frozen=True)
class Layout:
    """Every path one run uses. The catalog and the scratch repo are siblings of the home."""

    root: Path
    home: Path
    catalog: Path
    repo: Path
    checkout: Path


@dataclass(frozen=True)
class Step:
    """One command of the plan. `cwd` is where it runs and `argv` is what it runs."""

    name: str
    argv: tuple
    cwd: Path


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
    return tuple(step.name for step in _commands(_stand_in()))


def ok_line(name):
    """The line a step that worked prints."""
    return f"ok {name}\n"


def fail_line(name):
    """The line a step that failed prints."""
    return f"fail {name}\n"


def _stand_in():
    """A layout whose paths no command reads, so the names come from the one list of steps."""
    return Layout(Path(""), Path(""), Path(""), Path(""), CHECKOUT)


def _commands(layout):
    """Every step, in order. Each argv is absolute and each step runs in the checkout."""
    python = sys.executable
    return (
        Step("install", (python, str(CHECKOUT / "scripts" / "install.py")), CHECKOUT),
        Step("doctor", (python, str(CHECKOUT / "scripts" / "install.py"), "doctor"), CHECKOUT),
        Step(
            "roles",
            (
                python,
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
                str(CHECKOUT / "skills" / "brigade" / "scripts" / "brigade.py"),
                "open",
                "--project-root",
                str(layout.repo),
                "--name",
                "smoke",
            ),
            CHECKOUT,
        ),
        Step(
            "land",
            (
                python,
                str(CHECKOUT / "skills" / "landing" / "scripts" / "land.py"),
                "--repo",
                str(layout.repo),
                "init",
                "--trunk",
                "main",
                "--mode",
                "local",
                "--base",
                "HEAD",
            ),
            CHECKOUT,
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
    catalog, repo, checkout = root / "catalog.json", root / "repo", CHECKOUT
    if inside(repo, checkout) or inside(checkout, repo):
        sys.exit(f"outsider_smoke: {repo} would reach the checkout {checkout}")
    if inside(catalog, home):
        sys.exit(f"outsider_smoke: {catalog} would sit inside the home {home}")
    return Layout(root, home, catalog, repo, checkout)


def child_env(home, inherited):
    """The environment every command of the run inherits. HOME names the home this run made.

    Git honors its own path, configuration, template, and tracing overrides independently of HOME,
    so every GIT_* variable is dropped except the four that name the commit identity. System
    configuration is off for the run.
    """
    env = {name: value for name, value in inherited.items()
           if name not in DROPPED and (not name.startswith("GIT_") or name in AUTHOR_NAMES)}
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


def prepare(layout, env):
    """Write the catalog and the scratch commit. Prep is not a step, so it prints no ok line."""
    document = json.dumps(_catalog_document(), indent=2) + "\n"
    calls = (
        (sys.executable, "-c", WRITER, str(layout.catalog), document),
        ("git", "init", "-b", "main", str(layout.repo)),
        (sys.executable, "-c", WRITER, str(layout.repo / "README.md"), README_TEXT),
        ("git", "-C", str(layout.repo), "add", "README.md"),
        ("git", "-C", str(layout.repo), "commit", "--message", "outsider smoke"),
    )
    for argv in calls:
        completed = spawn(argv, CHECKOUT, env)
        if completed.returncode:
            die("scratch", completed)


def spawn(argv, cwd, env):
    """Run one command and wait for it. Its output is captured, so a failure can quote it."""
    return subprocess.run(argv, cwd=cwd, env=dict(env), text=True, capture_output=True)


def run_steps(steps, env):
    """Run each step in order. The first step that fails ends the run."""
    for step in steps:
        completed = spawn(step.argv, step.cwd, env)
        if completed.returncode:
            die(step.name, completed)
        print(ok_line(step.name), end="")


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
        prepare(layout, env)
        run_steps(_commands(layout), env)
    except SystemExit:
        print(f"kept {root}", file=sys.stderr)
        raise
    require_created(root, created)
    shutil.rmtree(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
