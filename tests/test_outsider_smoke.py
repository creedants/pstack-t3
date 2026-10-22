"""The outsider smoke catches a command a fresh HOME cannot run."""

import os
import re
import shlex
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
REQUIRED_STEPS = (
    "install",
    "doctor",
    "roles",
    "brigade",
    "land",
    "ticket",
    "fire",
    "worker",
    "dish",
    "pass",
    "submit",
    "queue",
    "trunk",
    "merged",
    "close",
)
PAGE = ROOT / "docs" / "first-coordinator.md"
REF_MARK = "\0REF"


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


def _norm_token(token):
    if token.startswith("$") or token.startswith("~") or token == "...":
        return token
    if "python" in token.lower() and ("bin" in token or token.endswith("python3") or token == sys.executable):
        return "python3"
    if token.endswith(".py") and "/" in token:
        for marker in ("skills/", "scripts/"):
            at = token.find(marker)
            if at != -1:
                return token[at:]
    return token


def _plan_token(part):
    if isinstance(part, outsider_smoke.Ref):
        return REF_MARK
    text = str(part)
    if text == "-I":
        return None
    return _norm_token(text)


def _expand_sh(argv):
    if len(argv) >= 3 and argv[0] == "sh" and argv[1] == "-c":
        found = []
        for chunk in str(argv[2]).split(";"):
            text = chunk.strip()
            if not text or text == "set -e":
                continue
            found.append([_norm_token(t) for t in shlex.split(text)])
        return found
    tokens = []
    for part in argv:
        token = _plan_token(part)
        if token is not None:
            tokens.append(token)
    return [tokens]


def _plan_lists():
    entries = outsider_smoke._commands(outsider_smoke._stand_in())
    flat = []
    for entry in entries:
        flat.extend(_expand_sh(entry.argv))
    return flat


def _page_blocks(text):
    return re.findall(r"```[^\n]*\n(.*?)```", text, re.DOTALL)


def _page_lists(text):
    found = []
    for block in _page_blocks(text):
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            inner = stripped
            match = re.match(r"^[A-Za-z_][A-Za-z0-9_]*=\$\((.*)\)\s*$", stripped)
            if match:
                inner = match.group(1)
            found.append([_norm_token(t) for t in shlex.split(inner)])
    return found


def _matches(page, plan):
    if len(page) != len(plan):
        return False
    for have, want in zip(page, plan):
        if have.startswith("$") or have.startswith("~") or have == "...":
            continue
        if have != want:
            return False
    return True


def _unknown_page_commands(text):
    plans = _plan_lists()
    return [page for page in _page_lists(text) if not any(_matches(page, plan) for plan in plans)]


def _missing_steps(text):
    pages = _page_lists(text)
    entries = [e for e in outsider_smoke._commands(outsider_smoke._stand_in()) if isinstance(e, outsider_smoke.Step)]
    missing = []
    for entry in entries:
        wants = _expand_sh(entry.argv)
        if entry.name == "worker":
            wants = [tokens for tokens in wants if tokens and tokens[0] == "git"]
        if not all(any(_matches(page, want) for page in pages) for want in wants):
            missing.append(entry.name)
    return missing


def _t3_inside_fence(text):
    inside = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            inside = not inside
        elif inside and "**T3 action:**" in line:
            return True
    return False


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

    def test_every_required_step_prints_its_ok_line(self):
        # Review D306 round 1: a plan that dropped the roles step left every test green, because
        # the expected lines came from the same plan. These names come from this test.
        with tempfile.TemporaryDirectory() as canary, tempfile.TemporaryDirectory() as tmpdir:
            (Path(canary) / CANARY).write_text("keep\n")
            result = run(canary, tmpdir)
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in REQUIRED_STEPS:
                self.assertIn(outsider_smoke.ok_line(name), result.stdout)

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

    def test_page_command_without_a_plan_command_is_refused(self):
        # A page fence that names a program the plan never runs would teach a stranger a wrong command.
        text = PAGE.read_text(encoding="utf-8")
        self.assertEqual(_unknown_page_commands(text), [])
        bogus = text + "\n```sh\npython3 scripts/not_a_command.py\n```\n"
        self.assertIn(["python3", "scripts/not_a_command.py"], _unknown_page_commands(bogus))

    def test_page_covers_every_step_the_plan_runs(self):
        # A step with no page fence leaves a stranger with no command for that part of the run.
        text = PAGE.read_text(encoding="utf-8")
        self.assertEqual(_missing_steps(text), [])

    def test_t3_action_callout_stays_out_of_fences(self):
        # A T3 action inside a fence reads as a command the smoke runs, but the smoke cannot start T3 threads.
        text = PAGE.read_text(encoding="utf-8")
        self.assertFalse(_t3_inside_fence(text))

    def test_ref_names_a_capture_from_an_earlier_entry(self):
        # A Ref to a name no earlier entry captured would fail only at run time, after slow steps already ran.
        seen = set()
        for entry in outsider_smoke._commands(outsider_smoke._stand_in()):
            for part in entry.argv:
                if isinstance(part, outsider_smoke.Ref):
                    self.assertIn(part.name, seen)
            if entry.capture:
                seen.add(entry.capture)

    def test_expect_reason_reports_both_kinds(self):
        # A broken postcondition that reason calls met would let a wrong store state pass as green.
        self.assertIsNone(outsider_smoke.Expect("is", "T1").reason("T1\n"))
        self.assertIsNone(outsider_smoke.Expect("is", "T1").reason("T1"))
        self.assertIsNotNone(outsider_smoke.Expect("is", "T1").reason("T2\n"))
        self.assertIsNone(outsider_smoke.Expect("has", "merged: 1").reason("Since opening. merged: 1.\n"))
        self.assertIsNotNone(outsider_smoke.Expect("has", "merged: 1").reason("nothing merged\n"))

    def test_page_missing_a_step_fails_the_check(self):
        # A check that stays green when a step leaves the page would let the page drift from the smoke.
        text = PAGE.read_text(encoding="utf-8")
        self.assertEqual(_missing_steps(text), [])
        mutated = text.replace("close --dry-run", "close --to-file")
        self.assertIn("close", _missing_steps(mutated))
