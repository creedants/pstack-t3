"""Cleanup ends a process a runner test recorded, through the pidfd it took while that process ran, whatever its command line names. Each case here reads /proc, so it runs on Linux only."""

import os
import select
import subprocess
import sys
import unittest

from .support import LINUX, MARKS, RUNNER, RunnerCase, setUpModule, start_time

NO_PRCTL = (
    "try:\n"
    "    import _run_tests.processes as _proc\n"
    "    _proc._prctl = None\n"
    "except Exception:\n"
    "    pass\n"
)

# The worker ends the watcher that would end it when its runner dies, marks itself, then sleeps. Nothing ends it after its runner is killed.
LOST_WORKER = """
    import os
    import signal
    import time
    import unittest
    from pathlib import Path

    import marks

    def end_watchers():
        me = os.getpid()
        raw = Path(f"/proc/{me}/stat").read_bytes()
        mine = int(raw[raw.rindex(b")") + 2:].split()[2])
        ended = 0
        open_pidfd, send = getattr(os, "pidfd_open", None), getattr(signal, "pidfd_send_signal", None)
        if open_pidfd is None or send is None:
            return ended
        for entry in os.listdir("/proc"):
            if not entry.isdigit() or int(entry) == me:
                continue
            try:
                handle = open_pidfd(int(entry))
            except OSError:
                continue
            try:
                stat = Path("/proc", entry, "stat").read_bytes()
                if int(stat[stat.rindex(b")") + 2:].split()[2]) != mine:
                    continue
                send(handle, signal.SIGKILL)
                ended += 1
            except (OSError, ValueError, IndexError):
                continue
            finally:
                os.close(handle)
        return ended

    class LostTest(unittest.TestCase):
        def test_lost(self):
            self.assertGreater(end_watchers(), 0, "the worker has no watcher in its process group")
            marks.mark("worker", os.getpid())
            time.sleep(60)
"""

RESPONDER = (
    "import sys\n"
    "while True:\n"
    "    line = sys.stdin.readline()\n"
    "    if not line:\n"
    "        break\n"
    "    sys.stdout.write('pong\\n')\n"
    "    sys.stdout.flush()\n"
)


class CleanupTest(RunnerCase, unittest.TestCase):
    def sitecustomize(self):
        """Write a sitecustomize that hides prctl from a runner, so that runner starts a watcher the worker test can end."""
        ppath = self.scratch / "ppath"
        ppath.mkdir(exist_ok=True)
        (ppath / "sitecustomize.py").write_text(f"import sys\nsys.path.insert(0, {str(RUNNER.parent)!r})\n" + NO_PRCTL)
        return ppath

    def runner_env(self):
        base = os.pathsep.join((str(self.sitecustomize()), str(RUNNER.parent)))
        old = os.environ.get("PYTHONPATH")
        return {**os.environ, "PYTHONPATH": base if not old else base + os.pathsep + old}

    def responder(self):
        """Start a process that answers each line it reads with pong, so a test can tell it still runs."""
        proc = subprocess.Popen([sys.executable, "-c", RESPONDER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                text=True, start_new_session=True)
        self.addCleanup(self.stop_responder, proc)
        return proc

    def stop_responder(self, proc):
        proc.kill()
        proc.wait()
        proc.stdin.close()
        proc.stdout.close()

    def assert_answers(self, proc, limit=5):
        """Assert proc answers one request within limit seconds. A process that received SIGKILL answers nothing."""
        try:
            proc.stdin.write("ping\n")
            proc.stdin.flush()
        except OSError:
            self.fail("the process ended before it answered a request")
        ready, _, _ = select.select([proc.stdout], [], [], limit)
        self.assertTrue(ready, "the process did not answer a request")
        self.assertEqual(proc.stdout.readline(), "pong\n")

    @unittest.skipUnless(LINUX, "the test reads /proc")
    def test_cleanup_ends_a_worker_that_outlived_its_runner(self):
        env = self.runner_env()
        left = {}

        class LeavesWorker(RunnerCase, unittest.TestCase):
            def runTest(self):
                self.write("marks.py", MARKS)
                self.write("test_lost.py", LOST_WORKER)
                proc = self.start(env=env)
                worker = self.pid_from("worker")
                left["worker"] = worker
                proc.kill()
                proc.wait()
                self.assert_alive(worker)

        result = unittest.TestResult()
        LeavesWorker().run(result)
        self.assertTrue(result.wasSuccessful(), f"{result.failures}{result.errors}")
        self.assert_dead(left["worker"])

    @unittest.skipUnless(LINUX, "the test reads /proc")
    def test_cleanup_leaves_a_pid_whose_recorded_start_time_differs(self):
        """Cleanup signals no process for a record whose start time /proc does not report for that pid, and the sleeper answers after end."""
        sleeper = self.responder()
        self.remember(sleeper.pid)
        self.starts[sleeper.pid] = "0"
        self.end(sleeper.pid)
        self.assert_answers(sleeper)

    @unittest.skipUnless(LINUX, "the test reads /proc")
    def test_cleanup_leaves_a_pid_whose_record_holds_no_pidfd(self):
        """A start time is not a handle. Cleanup signals no process for a record that holds only a start time, so a pid the kernel gives to another process after the check is not signalled, and the sleeper answers after end."""
        sleeper = self.responder()
        self.starts[sleeper.pid] = start_time(sleeper.pid)
        self.end(sleeper.pid)
        self.assert_answers(sleeper)

    @unittest.skipUnless(LINUX, "the test reads /proc")
    def test_cleanup_leaves_a_pid_whose_mark_carries_no_start_time(self):
        """A mark that carries no start time cannot be tied to the process the test started. Cleanup records nothing from it, and the sleeper answers after end."""
        sleeper = self.responder()
        (self.proj / "worker").write_text(str(sleeper.pid))
        worker = self.pid_from("worker")
        self.assertEqual(worker, sleeper.pid)
        self.end(worker)
        self.assert_answers(sleeper)

    @unittest.skipUnless(LINUX, "the test reads /proc")
    def test_cleanup_leaves_a_pid_whose_mark_carries_another_start_time(self):
        """A mark whose start time /proc does not report for the pid names a process that ended, so cleanup does not record the process that holds the pid now, and the sleeper answers after end."""
        sleeper = self.responder()
        (self.proj / "worker").write_text(f"{sleeper.pid} 0")
        worker = self.pid_from("worker")
        self.assertEqual(worker, sleeper.pid)
        self.end(worker)
        self.assert_answers(sleeper)


if __name__ == "__main__":
    unittest.main()
