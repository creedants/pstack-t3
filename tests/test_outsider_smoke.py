"""The outsider smoke catches a command a fresh HOME cannot run."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from tests import in_process

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import outsider_smoke  # noqa: E402

AUTHOR = {
    "GIT_AUTHOR_NAME": "outsider",
    "GIT_AUTHOR_EMAIL": "outsider@example.com",
    "GIT_COMMITTER_NAME": "outsider",
    "GIT_COMMITTER_EMAIL": "outsider@example.com",
}
CANARY = "canary.txt"


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


def listing(directory):
    """Every name under `directory`, relative to it."""
    found = []
    for dirpath, dirnames, filenames in os.walk(directory):
        for name in sorted(dirnames) + sorted(filenames):
            found.append(str((Path(dirpath) / name).relative_to(directory)))
    return found


def environment(canary, tmpdir):
    """A caller's environment whose HOME and XDG paths all sit under `canary`."""
    env = os.environ.copy()
    env.update(AUTHOR)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["HOME"] = str(Path(canary) / "home")
    env["XDG_CONFIG_HOME"] = str(Path(canary) / "xdg-config")
    env["XDG_STATE_HOME"] = str(Path(canary) / "xdg-state")
    env["CLAUDE_CONFIG_DIR"] = str(Path(canary) / "claude")
    env["BRIGADE_STORE"] = str(Path(canary) / "brigade")
    env["TMPDIR"] = str(tmpdir)
    return env


def run(canary, tmpdir, *argv):
    return in_process.invoke(in_process.Invocation(
        program=ROOT / "scripts" / "outsider_smoke.py",
        argv=argv,
        env=environment(canary, tmpdir),
    ))


class OutsiderSmokeTest(unittest.TestCase):
    def test_every_step_runs_against_the_home_the_script_made(self):
        # The script starts one process per step because the check is the environment that process
        # inherits and that process's exit status.
        with tempfile.TemporaryDirectory() as canary, tempfile.TemporaryDirectory() as tmpdir:
            (Path(canary) / CANARY).write_text("keep\n")
            before = listing(Path(canary))
            result = run(canary, tmpdir)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = "".join(outsider_smoke.ok_line(name) for name in outsider_smoke.plan_names())
            self.assertEqual(result.stdout, expected)
            self.assertEqual(listing(Path(canary)), before)

    def test_a_home_this_run_did_not_create_is_refused(self):
        with tempfile.TemporaryDirectory() as canary, tempfile.TemporaryDirectory() as tmpdir:
            (Path(canary) / CANARY).write_text("keep\n")
            before = listing(Path(canary))
            result = run(canary, tmpdir, "--home", canary)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn(str(canary), result.stderr)
            self.assertEqual(listing(Path(canary)), before)
