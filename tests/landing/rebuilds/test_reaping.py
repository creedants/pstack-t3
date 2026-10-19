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

    def test_darwin_ps_probe_reap_is_not_a_leader_reap(self):
        """Reaps of the ps probe child are not leader reaps."""
        from unittest import mock
        real_popen = subprocess.Popen
        ps_pids = []

        def recording_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            argv = args[0] if args else kwargs.get("args", [])
            if list(argv)[:1] == ["ps"]:
                ps_pids.append(process.pid)
            return process

        with _platform(_DARWIN):
            with _GroupTrace() as trace:
                with mock.patch.object(subprocess, "Popen", recording_popen):
                    land.run_build("sleep 5", str(self.work), 0.1)
            leader = trace.leader()
            self.assertTrue(ps_pids)
            self.assertEqual(
                {pid for name, pid in trace.events if name == "reaped" and pid != leader},
                set(ps_pids))
            self.assertEqual(_names(trace.events, leader), ["killpg", "reaped"])
            self.assertFalse(_signalled_after_reap(trace.events))

    def test_sigint_sampled_lines_never_signal_after_reap(self):
        """Sampled lines never signal after the reap."""
        original = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGINT, original)
        codes = {land.run_build.__code__}
        for value in vars(land._Build).values():
            if callable(value):
                codes.add(value.__code__)

        def line_events(command, timeout):
            seen = []

            def tracer(frame, event, arg):
                if frame.f_code in codes:
                    if event == "line":
                        seen.append((frame.f_code.co_name, frame.f_lineno))
                    return tracer
                return None

            sys.settrace(tracer)
            try:
                land.run_build(command, str(self.work), timeout)
            except KeyboardInterrupt:
                pass
            finally:
                sys.settrace(None)
            return seen

        builds = [("exit 0", 5), ("sleep 5", 0.05), ("(sleep 30) & exit 0", 0.05)]
        for platform in _platforms():
            with self.subTest(platform=platform):
                with _platform(platform):
                    counts = [len(line_events(command, timeout)) for command, timeout in builds]
                    with _GroupTrace() as trace:
                        for kind in ("raising", "returning"):
                            for (command, timeout), total in zip(builds, counts):
                                for index in range(total):
                                    trace.clear()
                                    calls = []

                                    def raising_previous(signum, frame):
                                        calls.append(1)
                                        raise KeyboardInterrupt

                                    def returning_previous(signum, frame):
                                        calls.append(1)

                                    entry = raising_previous if kind == "raising" else returning_previous
                                    signal.signal(signal.SIGINT, entry)
                                    fired = [0]
                                    # hit records reach when raise interrupts before the increment.
                                    hit = [False]

                                    def tracer(frame, event, arg):
                                        if frame.f_code in codes:
                                            if event == "line":
                                                if fired[0] == index:
                                                    hit[0] = True
                                                    signal.raise_signal(signal.SIGINT)
                                                fired[0] += 1
                                            return tracer
                                        return None

                                    started = time.monotonic()
                                    sys.settrace(tracer)
                                    try:
                                        land.run_build(command, str(self.work), timeout)
                                    except KeyboardInterrupt:
                                        pass
                                    finally:
                                        sys.settrace(None)
                                    elapsed = time.monotonic() - started
                                    after = _signalled_after_reap(trace.events)
                                    restored = signal.getsignal(signal.SIGINT) is entry
                                    self.assertFalse(after, f"{platform} {kind} {command} line {index}: {trace.events}")
                                    self.assertTrue(restored, f"{platform} {kind} {command} line {index}")
                                    if hit[0]:
                                        self.assertEqual(calls, [1], f"{platform} {kind} {command} line {index}")
                                    else:
                                        self.assertEqual(calls, [], f"{platform} {kind} {command} line {index}")
                                    self.assertLess(elapsed, 6, f"{platform} {kind} {command} line {index}")

    def test_timeout_kills_live_build_with_no_exit_detector(self):
        """Timeout kills a live build without an exit detector."""
        parent = str(SCRIPT.parent)
        work = str(self.work)
        lines = [
            "import os, sys, time",
            f"sys.path.insert(0, {parent!r})",
            "import land",
            "if hasattr(os, 'waitid'):",
            "    try: del os.waitid",
            "    except AttributeError: pass",
            "os.environ['PATH'] = ''",
            "build = sys.executable + \" -c 'import time; time.sleep(0.8)'\"",
            "start = time.monotonic()",
            "code, out = land.run_build(build, sys.argv[1], 0.05)",
            "first = time.monotonic() - start",
            "holder = object.__new__(land.Integration)",
            "holder.path = sys.argv[1]",
            "start = time.monotonic()",
            "code2, out2 = land.Integration._run_build(holder, build, 0.05)",
            "second = time.monotonic() - start",
            "ok = code is None and code2 is None and first < 0.5 and second < 0.5",
            "print('RESULT ' + repr((code, code2, round(first, 3), round(second, 3))))",
            "raise SystemExit(0 if ok else 2)",
        ]
        result = subprocess.run(
            [sys.executable, "-c", "\n".join(lines), work],
            capture_output=True, text=True, timeout=60, cwd=work)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RESULT (None, None", result.stdout)
