"""Cleanup ends a process a runner test recorded, whatever its command line names. Each case here reads /proc, so it runs on Linux only."""

import os
import signal
import subprocess
import sys
import time
import unittest

from .support import LINUX, MARKS, RUNNER, RunnerCase, setUpModule

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
        for entry in os.listdir("/proc"):
            if not entry.isdigit() or int(entry) == me:
                continue
            try:
                stat = Path("/proc", entry, "stat").read_bytes()
                if int(stat[stat.rindex(b")") + 2:].split()[2]) != mine:
                    continue
            except (OSError, ValueError, IndexError):
                continue
            os.kill(int(entry), signal.SIGKILL)
            ended += 1
        return ended

    class LostTest(unittest.TestCase):
        def test_lost(self):
            self.assertGreater(end_watchers(), 0, "the worker has no watcher in its process group")
            marks.mark("worker", os.getpid())
            time.sleep(60)
"""


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
                os.kill(proc.pid, signal.SIGKILL)
                proc.wait()
                self.assert_alive(worker)

        result = unittest.TestResult()
        LeavesWorker().run(result)
        self.assertTrue(result.wasSuccessful(), f"{result.failures}{result.errors}")
        worker = left["worker"]
        try:
            self.assert_dead(worker)
        finally:
            try:
                os.kill(worker, signal.SIGKILL)
            except OSError:
                pass

    @unittest.skipUnless(LINUX, "the test reads /proc")
    def test_cleanup_leaves_a_pid_whose_recorded_start_time_differs(self):
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                   stdout=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: (sleeper.kill(), sleeper.wait()))
        # What the record reads after the kernel gives this pid to a process that started at another time.
        self.starts[sleeper.pid] = "0"
        self.end(sleeper.pid)
        self.assert_alive(sleeper.pid)


if __name__ == "__main__":
    unittest.main()
