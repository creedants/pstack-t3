import importlib.util
import os
import pathlib
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from tests import in_process

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/release_tests"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "t3/added/brigade/scripts"))
sys.path.insert(0, str(ROOT / "scripts"))

if "pstack_release" in sys.modules:
    release = sys.modules["pstack_release"]
else:
    _spec = importlib.util.spec_from_file_location("pstack_release", ROOT / "scripts/release.py")
    release = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = release
    _spec.loader.exec_module(release)
from tests import test_pstack_t3  # noqa: E402
from brigade_lib.store import KINDS as STORE_KINDS  # noqa: E402

ENV = {
    **{name: value for name, value in os.environ.items() if not name.startswith("GIT_")},
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "LC_ALL": "C",
}
CHANGELOG = "# Changelog\n\n## 0.2.0 (2026-10-06)\n\n- old\n\n## 0.1.0\n\nFirst.\n"
HEADING = "## 0.2.1 (2026-10-09)\n\n"
UPKEEP = "### Changed\n\n"
SECTION = HEADING + UPKEEP + "- eta one\n  eta continued\n- eta two\n- beta\n- alpha\n- zeta again\n"
STEPS = (
    "this script never stages, commits, tags, or pushes. the remaining steps:\n"
    "  git add CHANGELOG.md changes/\n"
    '  git commit -m "release 0.2.1"\n'
    '  git tag -a v0.2.1 -m "pstack-t3 0.2.1"    # on the commit that lands on main\n'
    "  git push origin v0.2.1\n"
    "  gh release create v0.2.1 --notes-file <release notes file>\n"
)
DRY_RUN = (
    SECTION
    + "\nwould write this section to CHANGELOG.md\n"
    "would delete changes/eta.md\nwould delete changes/beta.md\n"
    "would delete changes/alpha.md\nwould delete changes/zeta.md\n"
    "nothing changed\n" + STEPS
)
REAL_RUN = (
    SECTION
    + "\nwrote this section to CHANGELOG.md\n"
    "deleted changes/eta.md\ndeleted changes/beta.md\n"
    "deleted changes/alpha.md\ndeleted changes/zeta.md\n" + STEPS
)
RELEASED = " M CHANGELOG.md\n D changes/alpha.md\n D changes/beta.md\n D changes/eta.md\n D changes/zeta.md\n"
UNDO = "git restore --staged --worktree CHANGELOG.md changes/"
ARCHIVE_UNDO = "git reset -q -- CHANGELOG.md changes/ docs/changelog/ && git checkout -- CHANGELOG.md changes/ && git clean -fdq -- docs/changelog/"
DIRTY = (
    "CHANGELOG.md or changes/ has uncommitted changes; commit them, "
    f"or run {UNDO} to undo an unfinished release:\n"
)
NOTHING_CHANGED = "; nothing changed, fix that and rerun"
NOT_A_BULLET = "is not a bullet or a continuation line; fix the fragment, commit it, and rerun\n"


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


def git(repo, *args, date=None):
    env = {**ENV, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else ENV
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8", env=env
    ).stdout


def commit(repo, files, day=1, second=0):
    for relative, text in files.items():
        path = repo / relative
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode())
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c", date=f"2026-01-{day:02d}T12:00:{second:02d}+00:00")


def run(repo, *args, env=ENV, date="2026-10-09"):
    notes = repo.parent / "stable-notes.md"
    notes.write_text("Stable check: T3 Code 0.1.0 passed\n")
    return in_process.invoke(in_process.Invocation(
        program=repo / "scripts/release.py",
        argv=(*args, "--notes-file", str(notes), "--date", date),
        env=env,
        cwd=repo.parent,
    ))


def state(repo):
    paths = [repo / "CHANGELOG.md"]
    for name in ("changes", "docs/changelog"):
        base = repo / name
        if base.is_dir():
            paths += sorted(base.rglob("*"))
    files = {str(path.relative_to(repo)): path.read_bytes() for path in paths if path.is_file() and not path.is_symlink()}
    return git(repo, "status", "--porcelain", "--untracked-files=all"), git(repo, "rev-parse", "HEAD"), files


@contextmanager
def repository(changelog=CHANGELOG):
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory) / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        (repo / "scripts").mkdir()
        shutil.copy(ROOT / "scripts/release.py", repo / "scripts/release.py")
        commit(repo, {"CHANGELOG.md": changelog})
        yield repo


def add_fragments_out_of_name_and_date_order(repo):
    commit(repo, {"changes/zeta.md": "- zeta\n", "changes/eta.md": "- eta one\n  eta continued\n- eta two\n"}, day=2)
    commit(repo, {"changes/beta.md": "- beta\n"}, day=3)
    commit(repo, {"changes/alpha.md": "- alpha\n"}, day=3)
    commit(repo, {"changes/zeta.md": None}, day=4)
    git(repo, "commit", "-q", "--allow-empty", "-m", "spacer", date="2026-01-05T12:00:00+00:00")
    (repo / "changes/zeta.md").write_text("- zeta again\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c", date="2025-12-01T12:00:00+00:00")


@contextmanager
def history():
    with repository() as repo:
        add_fragments_out_of_name_and_date_order(repo)
        yield repo


@contextmanager
def recording_handlers():
    """Stand in for the default handlers, so a signal the script fails to hold cannot kill the test run."""
    missed = []
    previous = {number: signal.signal(number, lambda signum, frame: missed.append(signum)) for number in release.HELD}
    try:
        yield missed
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


class ReleaseCase(unittest.TestCase):
    maxDiff = None

    def assert_refuses(self, repo, version, message):
        before = state(repo)
        result = run(repo, version)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", message))
        self.assertEqual(state(repo), before)

    def assert_section(self, repo, section, version="0.2.1"):
        result = run(repo, version, "--dry-run")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(result.stdout[: len(section) + 1], section + "\n")
