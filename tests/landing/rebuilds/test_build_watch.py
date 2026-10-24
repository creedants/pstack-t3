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

    def test_build_watch_supports_descriptors_above_1024(self):
        """The build watch reads pipes above descriptor 1024."""
        import resource
        # Holding one descriptor per pass leaves the next pipe at 1025 and 1026
        # with nothing open above 1024, so the runner's wake pipe, selector, and
        # subprocess pipes run to 1031.
        needed = 1032
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if hard < needed:
            self.skipTest(f"hard fd limit {hard} is below the test requirement of {needed}")
        budget = min(1100, hard)
        if soft < budget:
            resource.setrlimit(resource.RLIMIT_NOFILE, (budget, hard))
            self.addCleanup(resource.setrlimit, resource.RLIMIT_NOFILE, (soft, hard))
        held = []
        self.addCleanup(lambda: [os.close(fd) for fd in held])
        while True:
            first, second = os.pipe()
            if first > 1024 and second > 1024:
                os.close(first)
                os.close(second)
                break
            os.close(first)
            os.close(second)
            held.append(os.open(os.devnull, os.O_RDONLY))
        # The command exits 3 so the test can read the status back.
        code, out = land.run_build("exit 3", str(self.work), 10)
        self.assertEqual((code, out), (3, ""))

    def test_watch_error_fails_fast_instead_of_waiting_to_the_deadline(self):
        """Watch errors fail fast instead of waiting to the deadline."""
        import selectors
        from unittest import mock

        def failing(self, timeout=None):
            raise OSError(errno.EBADF, "Bad file descriptor")

        with mock.patch.object(selectors.DefaultSelector, "select", failing):
            started = time.monotonic()
            with self.assertRaises(OSError):
                land.run_build("exit 0", str(self.work), 30)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5)

    def test_run_build_refuses_to_start_while_sigchld_is_ignored(self):
        """Refuses to start while SIGCHLD is ignored."""
        from unittest import mock
        previous = signal.getsignal(signal.SIGCHLD)
        self.addCleanup(signal.signal, signal.SIGCHLD, previous)
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)
        with mock.patch.object(land.subprocess, "Popen", side_effect=AssertionError("spawned")) as spawned:
            with self.assertRaises(ValueError) as raised:
                land.run_build("exit 0", str(self.work), 10)
        self.assertEqual(str(raised.exception), "run_build refuses to start while SIGCHLD is ignored")
        spawned.assert_not_called()
        self.assertIs(signal.getsignal(signal.SIGCHLD), signal.SIG_IGN)

    def test_second_grace_sigint_cuts_the_drain_short(self):
        """A second grace SIGINT cuts the five second drain short."""
        elapsed, calls = self._grace_run(2)
        self.assertLess(elapsed, 4)
        self.assertEqual(calls, [1])

    def test_first_grace_sigint_waits_the_full_drain(self):
        """A first grace SIGINT waits the full five second drain."""
        elapsed, calls = self._grace_run(1)
        self.assertGreaterEqual(elapsed, 4.5)
        self.assertEqual(calls, [1])

    def test_install_race_restores_handler_after_interrupt(self):
        """Install race restores the entry handler."""
        real_signal = signal.signal
        previous = signal.getsignal(signal.SIGINT)
        injected = []

        def wrapping_signal(signum, handler):
            result = real_signal(signum, handler)
            if signum == signal.SIGINT and handler is not previous and not injected:
                injected.append(True)
                signal.raise_signal(signal.SIGINT)
            return result

        signal.signal = wrapping_signal
        try:
            with self.assertRaises(KeyboardInterrupt):
                land.run_build("sleep 30", str(self.work), 30)
        finally:
            signal.signal = real_signal
        self.assertEqual(injected, [True])
        self.assertIs(signal.getsignal(signal.SIGINT), previous)

    def test_previous_callable_runs_after_the_reap(self):
        """Previous runs after the reap."""
        previous = signal.getsignal(signal.SIGINT)
        for platform in _platforms():
            with self.subTest(platform=platform):
                with _platform(platform):
                    snapshots = []
                    with _GroupTrace(on_killpg=lambda: signal.raise_signal(signal.SIGINT)) as trace:
                        def returning_previous(signum, frame):
                            snapshots.append(list(trace.events))
                            try:
                                os.waitpid(-1, os.WNOHANG)
                            except ChildProcessError:
                                pass

                        signal.signal(signal.SIGINT, returning_previous)
                        try:
                            land.run_build("sleep 5", str(self.work), 0.1)
                            restored = signal.getsignal(signal.SIGINT)
                        finally:
                            signal.signal(signal.SIGINT, previous)
                    leader = trace.leader()
                    self.assertEqual(_names(trace.events, leader), ["killpg", "reaped"])
                    self.assertEqual([_names(seen, leader) for seen in snapshots], [["killpg", "reaped"]])
                    self.assertIs(restored, returning_previous)
