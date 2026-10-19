"""Shared helpers, fixtures, and repo scaffolding for the roles tests."""


import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from tests import in_process
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "t3/scripts"))
sys.path.insert(0, str(ROOT / "t3/added/landing/scripts"))
import land  # noqa: E402
import roles  # noqa: E402


GROK_SEAT = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "xhigh"}}


OPUS_SEAT = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}}


GROK_MEDIUM = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "medium"}}


OPUS_MEDIUM = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}}


OPUS_HIGH = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "high"}}


INHERIT_ENTRY = {"source": "default", "seats": "inherit", "note": roles.UNSET_ROLE_NOTE}


NO_CATALOG_INFO = (
    "info: light mode caps reasoning at medium, but show had no catalog, "
    "so no seat was capped. Rerun with --catalog and --parent.\n"
)


NO_PARENT_INFO = (
    "info: light mode caps reasoning at medium, but show had no --parent, "
    "so an inherit seat may keep the parent's reasoning. Rerun with --parent.\n"
)


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


def show(*role_names):
    with tempfile.TemporaryDirectory() as directory:
        env = {**os.environ, "XDG_CONFIG_HOME": directory}
        argv = [
            "show",
            "--cwd",
            directory,
            "--catalog",
            str(ROOT / "tests/fixtures/catalog.json"),
            "--parent",
            "claudeAgent/claude-opus-5-5",
        ]
        for name in role_names:
            argv.extend(["--role", name])
        return in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=tuple(argv),
            env=env,
        ))


PLAYBOOK_NAMES = ", ".join((
    "authoring-a-skill",
    "autonomous-run",
    "autopilot-full",
    "autopilot-stack",
    "babysit",
    "bug-fix",
    "eval",
    "feature",
    "hillclimb",
    "investigation",
    "multi-phase-plan",
    "opening-a-pr",
    "orchestrate",
    "pause-safely",
    "perf-issue",
    "prototype",
    "refactoring",
    "runtime-forensics",
    "session-pickup",
    "shipping",
    "trace-forensics",
    "visual-parity",
    "worktree-cleanup",
))


PERSONA_PATH = ROOT / "t3" / "agents" / "poteto-agent.md"


def agent_body():
    text = PERSONA_PATH.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    fences = [index for index, line in enumerate(lines) if line.strip() == "---"]
    return "".join(lines[fences[1] + 1:]).strip()


def run_check_brief(path):
    return in_process.invoke(in_process.Invocation(
        program=ROOT / "t3/scripts/roles.py",
        argv=("check-brief", str(path)),
    ))


def mode_output(*args):
    with tempfile.TemporaryDirectory() as directory:
        completed = in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=("mode", "--cwd", directory, *args),
            env={**os.environ, "XDG_CONFIG_HOME": directory},
        ))
    assert completed.returncode == 0 and completed.stderr == "", completed.stderr
    return completed.stdout


def check_text(text):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "brief.md"
        path.write_text(text, encoding="utf-8")
        return run_check_brief(path)


CATALOG = ROOT / "tests/fixtures/catalog.json"


FOUR_PATHS = [
    "**/migrations/**",
    "t3/added/landing/scripts/land.py",
    "t3/added/brigade/scripts/brigade.py",
    "scripts/install.py",
]


ROOT_ALIASES = (".", "/", "a/..", " ", "//")


class Repo:
    def __init__(self, directory):
        self.directory = Path(directory)
        (self.directory / ".git").mkdir()
        self.user = self.directory / "pstack-t3" / "roles.json"
        self.project = self.directory.resolve() / ".pstack" / "t3-roles.json"

    def put(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def run(self, *args):
        env = {**os.environ, "XDG_CONFIG_HOME": str(self.directory)}
        return in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=(*args, "--cwd", str(self.directory)),
            env=env,
        ))

    def show_bug_fix(self):
        return self.run(
            "show",
            "--catalog",
            str(CATALOG),
            "--parent",
            "claudeAgent/claude-opus-5-5",
            "--role",
            "bug-fix",
        )

    def write(self, *args):
        return self.run("write", "--catalog", str(CATALOG), *args)


LIGHT_USER = "Mode: light\nMode source: roles.json\n"


LIGHT_PROJECT = "Mode: light\nMode source: .pstack/t3-roles.json\n"


TRACKED = (
    "t3/added/landing/scripts/land.py",
    "t3/added/landing/SKILL.md",
    "db/migrations/0001.sql",
    "docs/migrations.md",
    "scripts/install.py",
)


CANONICAL_CORPUS = (
    "", ".", "/", " a/b ", "./a//b/", "a/../b", "/../x", "a\\b", "a/b/", "../x", "..", "a/../../x",
)


WAIVER_ROWS = {
    "feature": (
        ("Arena", "Interrogate", "Comment Sicko"),
        ("How", "Architect", "Arena", "Interrogate", "Comment Sicko"),
    ),
    "bug-fix": (
        ("Comment Sicko",),
        ("How", "Why", "Architect", "Comment Sicko"),
    ),
    "refactoring": (
        ("Comment Sicko",),
        ("How", "Architect", "Comment Sicko"),
    ),
    "perf-issue": (
        ("Comment Sicko",),
        ("How", "Architect", "Comment Sicko"),
    ),
    "hillclimb": (
        ("Comment Sicko",),
        ("How", "Comment Sicko"),
    ),
    "authoring-a-skill": (
        ("Comment Sicko", "Second-provider test"),
        ("Comment Sicko", "Second-provider test"),
    ),
}


def run_mode(directory, *args, env=None):
    base = {**os.environ, "XDG_CONFIG_HOME": str(directory)}
    if env:
        base.update(env)
    return in_process.invoke(in_process.Invocation(
        program=ROOT / "t3/scripts/roles.py",
        argv=("mode", *args, "--cwd", str(directory)),
        env=base,
    ))


def escalated(path):
    return f"Mode: full\nMode source: escalated: lease covers {path}\n"


class GitRepo:
    def __init__(self, directory, tracked):
        self.directory = Path(directory)
        self.env = {
            **os.environ,
            "XDG_CONFIG_HOME": str(self.directory),
            "GIT_CEILING_DIRECTORIES": str(self.directory.resolve().parent),
        }
        subprocess.run(
            ["git", "init", "-q"], cwd=self.directory, env=self.env, check=True, capture_output=True,
        )
        for relative in tracked:
            path = self.directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("tracked\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "--", relative],
                cwd=self.directory,
                env=self.env,
                check=True,
                capture_output=True,
            )
        self.project = self.directory / ".pstack" / "t3-roles.json"
        self.user = self.directory / "pstack-t3" / "roles.json"

    def put(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def run(self, *args, cwd=None):
        return in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=("mode", *args, "--cwd", str(cwd or self.directory)),
            env=self.env,
        ))


class ModeCliCase(unittest.TestCase):
    """Shared repo helpers for the mode command tests."""

    def open_repo(self):
        return tempfile.TemporaryDirectory()

    def show_role(self, repo, *args):
        return repo.run("show", "--catalog", str(CATALOG), "--parent", "claudeAgent/claude-opus-5-5", *args)


def roles_source():
    """Read the code wherever the split put it."""
    parts = [(ROOT / "t3/scripts/roles.py").read_text()]
    parts.extend(p.read_text() for p in sorted((ROOT / "t3/scripts/_roles").glob("*.py")))
    return "\n".join(parts)

