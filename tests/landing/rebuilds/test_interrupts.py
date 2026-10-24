if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import (
    setUpModule,
    LandingTest as LandingTestBase,
    REVIEWER,
    SCRIPT,
    land,
    sh,
)

import contextlib
import errno
import os
import shutil
import signal
import subprocess
import sys
import time
from .base import BUILD_PY, GENERATED_PATHS, GENERATED_TEXT, REBUILD, RebuildsBase
from .trace import _GroupTrace, _DARWIN, _HOST, _names, _platform, _platforms, _signalled_after_reap, _zombie


class LandingTest(RebuildsBase, LandingTestBase):

    def test_exited_leader_helper_dies_on_interrupt(self):
        self._setup_generated()
        self.init_generated()
        exited_build = BUILD_PY + (
            "\nimport os as _os, time as _time\n"
            "_marker = root / 'skills' / '.build_helper'\n"
            "_forked = _os.fork()\n"
            "if _forked == 0:\n"
            "    _marker.write_text(f'{_os.getpid()} {_os.getpgrp()}')\n"
            "    _time.sleep(60)\n"
            "else:\n"
            "    _os._exit(0)\n"
        )
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "src/one.txt").write_text("reviewed-new\n")
        (path / "scripts/build.py").write_text(exited_build)
        sh("git", "add", "-A", cwd=path)
        sh("git", "commit", "-qm", "w1", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "src/one.txt,scripts/build.py")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        with land.Store.for_repo(self.work) as store:
            integration = store.dir / "integration"
        marker = integration / "skills" / ".build_helper"
        queue = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--repo", str(self.work), "land"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True, env=os.environ.copy())
        build_pgid = None
        helper_pid = None
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if queue.poll() is not None:
                    break
                try:
                    text = marker.read_text().strip()
                except OSError:
                    time.sleep(0.05)
                    continue
                if text:
                    helper_pid, build_pgid = (int(part) for part in text.split()[:2])
                    break
                time.sleep(0.05)
            if queue.poll() is not None:
                out, err = queue.communicate()
                self.fail(f"queue exited before the interrupt: {out[-2000:]} {err[-2000:]}")
            self.assertTrue(marker.exists(), "fixture build never recorded readiness")
            self.assertIsNotNone(helper_pid)
            self.assertIsNotNone(build_pgid)

            deadline = time.monotonic() + 10
            while not _zombie(build_pgid) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(_zombie(build_pgid), "the leader did not exit before the interrupt")
            try:
                os.kill(helper_pid, 0)
            except ProcessLookupError:
                self.fail("the helper did not survive until the interrupt")
            queue.send_signal(signal.SIGINT)
            queue.wait(timeout=120)
            self.assertNotEqual(queue.returncode, 0)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    os.kill(helper_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            try:
                os.kill(helper_pid, 0)
                self.fail("the helper survived the interrupt")
            except ProcessLookupError:
                pass
            live = sh("git", "show", "main:src/one.txt", cwd=self.base / "origin.git")
            self.assertEqual(live, "base1")
            with land.Store.for_repo(self.work) as store:
                status = sh("git", "status", "--porcelain", cwd=store.dir / "integration")
            self.assertEqual(status, "")
        finally:
            if build_pgid is not None:
                try:
                    os.killpg(build_pgid, signal.SIGKILL)
                except OSError:
                    pass
            if helper_pid is not None:
                try:
                    os.kill(helper_pid, signal.SIGKILL)
                except OSError:
                    pass
            if queue.poll() is None:
                try:
                    os.killpg(queue.pid, signal.SIGKILL)
                except OSError:
                    pass
            try:
                queue.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            for pipe in (queue.stdout, queue.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass

    def test_reaped_leader_gets_no_killpg_on_interrupt(self):
        """Runner sends SIGKILL only before it reaps."""
        real_killpg = os.killpg
        real_waitpid = os.waitpid
        previous = signal.getsignal(signal.SIGINT)
        attempts = []
        injected = []

        def recording_killpg(pgid, sig):
            attempts.append((pgid, sig))
            return real_killpg(pgid, sig)

        def injecting_waitpid(pid, options):
            try:
                result = real_waitpid(pid, options)
            except ChildProcessError:
                if not injected:
                    injected.append(True)
                    signal.raise_signal(signal.SIGINT)
                raise
            if result[0] != 0 and not injected:
                injected.append(True)
                signal.raise_signal(signal.SIGINT)
            return result

        os.killpg = recording_killpg
        os.waitpid = injecting_waitpid
        try:
            with self.assertRaises(KeyboardInterrupt):
                land.run_build("exit 0", str(self.work), 10)
        finally:
            os.killpg = real_killpg
            os.waitpid = real_waitpid
        self.assertEqual(injected, [True])
        self.assertEqual(attempts, [])
        self.assertIs(signal.getsignal(signal.SIGINT), previous)

    def test_sigint_ignored_preserves_build_when_sig_ign(self):
        parent = str(SCRIPT.parent)
        work = str(self.work)
        cmd = repr("sleep 0.05; printf finished")
        lines = [
            "import selectors, signal, sys",
            f"sys.path.insert(0, {parent!r})",
            "import land",
            "signal.signal(signal.SIGINT, signal.SIG_IGN)",
            "real = selectors.DefaultSelector.select",
            "sent = []",
            "def wrapping(self, timeout=None):",
            "    if not sent:",
            "        sent.append(True)",
            "        signal.raise_signal(signal.SIGINT)",
            "    return real(self, timeout)",
            "selectors.DefaultSelector.select = wrapping",
            f"try:\n    code, out = land.run_build({cmd}, {work!r}, 10)",
            "except KeyboardInterrupt:",
            '    print("CAUGHT_KEYBOARD_INTERRUPT")',
            "    raise SystemExit(1)",
            'print("RESULT " + repr((code, out)))',
            'raise SystemExit(0 if code == 0 and out == "finished" else 2)',
        ]
        result = subprocess.run(
            [sys.executable, "-c", "\n".join(lines)],
            capture_output=True, text=True, timeout=60, cwd=work)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("finished", result.stdout)
        self.assertNotIn("CAUGHT", result.stdout + result.stderr)

    def test_sigint_default_terminates_runner_when_sig_dfl(self):
        parent = str(SCRIPT.parent)
        work = str(self.work)
        cmd = repr("sleep 0.05; printf finished")
        lines = [
            "import selectors, signal, sys",
            f"sys.path.insert(0, {parent!r})",
            "import land",
            "signal.signal(signal.SIGINT, signal.SIG_DFL)",
            "real = selectors.DefaultSelector.select",
            "sent = []",
            "def wrapping(self, timeout=None):",
            "    if not sent:",
            "        sent.append(True)",
            "        signal.raise_signal(signal.SIGINT)",
            "    return real(self, timeout)",
            "selectors.DefaultSelector.select = wrapping",
            f"try:\n    result = land.run_build({cmd}, {work!r}, 10)",
            "except KeyboardInterrupt:",
            '    print("CAUGHT_KEYBOARD_INTERRUPT")',
            "    raise SystemExit(4)",
            'print("RETURNED " + repr(result))',
            "raise SystemExit(3)",
        ]
        result = subprocess.run(
            [sys.executable, "-c", "\n".join(lines)],
            capture_output=True, text=True, timeout=60, cwd=work)
        self.assertEqual(result.returncode, -signal.SIGINT, result.stdout + result.stderr)
        self.assertNotIn("RETURNED", result.stdout)
        self.assertNotIn("CAUGHT", result.stdout + result.stderr)
