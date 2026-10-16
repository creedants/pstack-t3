"""The outsider smoke catches a command a fresh HOME cannot run."""

import os
import subprocess
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
    """A caller's environment whose HOME, XDG config and state, Claude, and brigade paths sit under `canary`."""
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


def run(canary, tmpdir, *argv, overrides=None):
    env = environment(canary, tmpdir)
    env.update(overrides or {})
    return in_process.invoke(in_process.Invocation(
        program=ROOT / "scripts" / "outsider_smoke.py",
        argv=argv,
        env=env,
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

    def test_inherited_git_overrides_cannot_reach_the_canary_home(self):
        # Review D306 round 1: an inherited GIT_INDEX_FILE, GIT_TRACE, or GIT_CONFIG_SYSTEM let Git
        # write or read outside the home this run made.
        with tempfile.TemporaryDirectory() as canary, tempfile.TemporaryDirectory() as tmpdir:
            (Path(canary) / CANARY).write_text("keep\n")
            (Path(canary) / "system-config").write_text("not a git configuration line\n")
            overrides = {
                "GIT_INDEX_FILE": str(Path(canary) / "index"),
                "GIT_TRACE": str(Path(canary) / "trace"),
                "GIT_CONFIG_SYSTEM": str(Path(canary) / "system-config"),
            }
            before = listing(Path(canary))
            result = run(canary, tmpdir, overrides=overrides)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = "".join(outsider_smoke.ok_line(name) for name in outsider_smoke.plan_names())
            self.assertEqual(result.stdout, expected)
            self.assertEqual(listing(Path(canary)), before)

    def test_inherited_python_startup_overrides_cannot_reach_the_canary_home(self):
        # Review D306 round 2 shows startup overrides escape the created home.
        # The canary holds a cache directory, a comment only .pth file, and a docstring only site customization.
        # The run stays in this process so recorded child output is the observer.
        # The old code inherits PYTHONVERBOSE. The new code drops that variable.
        with tempfile.TemporaryDirectory() as canary, tempfile.TemporaryDirectory() as tmpdir:
            (Path(canary) / CANARY).write_text("keep\n")
            (Path(canary) / "python-cache").mkdir()
            site = Path(canary) / "python-user" / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}" / "site-packages"
            site.mkdir(parents=True)
            (site / "canary.pth").write_text("# canary\n")
            (Path(canary) / "python-path").mkdir()
            (Path(canary) / "python-path" / "sitecustomize.py").write_text('"""Canary site customization."""\n')
            overrides = {
                "PYTHONPYCACHEPREFIX": str(Path(canary) / "python-cache"),
                "PYTHONUSERBASE": str(Path(canary) / "python-user"),
                "PYTHONPATH": str(Path(canary) / "python-path"),
                "PYTHONVERBOSE": "1",
                "PYTHONDONTWRITEBYTECODE": "0",
            }
            before = listing(Path(canary))
            original = subprocess.run
            transcripts = []
            real_process = os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)

            def recording(*args, **kwargs):
                completed = original(*args, **kwargs)
                transcripts.append(completed.stdout or "")
                transcripts.append(completed.stderr or "")
                return completed

            subprocess.run = recording
            try:
                result = run(canary, tmpdir, overrides=overrides)
            finally:
                subprocess.run = original
                if real_process is not None:
                    os.environ["PSTACK_TEST_REAL_PROCESS"] = real_process
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = "".join(outsider_smoke.ok_line(name) for name in outsider_smoke.plan_names())
            self.assertEqual(result.stdout, expected)
            self.assertEqual(listing(Path(canary)), before)
            self.assertNotIn(str(canary), "".join(transcripts))
            self.assertNotIn(str(canary), result.stdout + result.stderr)
