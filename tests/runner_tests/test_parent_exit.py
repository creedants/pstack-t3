"""A worker without the prctl path exits after its runner dies."""

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

from .support import LINUX, MARKS, RUNNER, RunnerCase, setUpModule


def wait_gone(pid: int, limit: float = 10.0) -> bool:
    """Return whether pid is gone within limit seconds. Count a zombie as gone when /proc reports state Z."""
    deadline = time.monotonic() + limit
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1][0]
            if state == "Z":
                return True
        except (FileNotFoundError, IndexError, ProcessLookupError):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


NO_PRCTL = (
    "try:\n"
    "    import _run_tests.processes as _proc\n"
    "    _proc._prctl = None\n"
    "except Exception:\n"
    "    pass\n"
)

GATE = """
import os
import sys
import time
from pathlib import Path

def started(pid):
    raw = Path(f"/proc/{pid}/stat").read_bytes()
    return raw[raw.rindex(b")") + 2:].split()[19].decode()

if "--worker" in sys.argv:
    proj = os.environ.get("PARENT_EXIT_PROJECT")
    if proj:
        root = Path(proj)
        (root / "worker.tmp").write_text(f"{os.getpid()} {started(os.getpid())}")
        os.replace(root / "worker.tmp", root / "worker")
        while not (root / "go").exists():
            time.sleep(0.02)
"""


class ParentExitTest(RunnerCase, unittest.TestCase):
    def write_sitecustomize(self, extra=""):
        ppath = self.scratch / "ppath"
        ppath.mkdir(exist_ok=True)
        scripts = str(RUNNER.parent)
        header = f"import sys\nsys.path.insert(0, {scripts!r})\n"
        (ppath / "sitecustomize.py").write_text(header + NO_PRCTL + extra)
        return ppath

    def runner_env(self, ppath):
        env = dict(os.environ)
        scripts = str(RUNNER.parent)
        old = env.get("PYTHONPATH")
        base = str(ppath) + os.pathsep + scripts
        env["PYTHONPATH"] = base if not old else base + os.pathsep + old
        env["TMPDIR"] = str(self.scratch)
        return env

    @unittest.skipUnless(LINUX, "the test needs /proc to see a zombie as gone")
    def test_startup_race_exits_worker_started_before_runner_death(self):
        self.write("test_slow.py", """
            import time
            import unittest

            class SlowTest(unittest.TestCase):
                def test_slow(self):
                    time.sleep(60)
        """)
        ppath = self.write_sitecustomize(GATE)
        env = self.runner_env(ppath)
        env["PARENT_EXIT_PROJECT"] = str(self.proj)
        proc = subprocess.Popen([sys.executable, str(RUNNER), "-s", str(self.tests), "-j", "1"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=env, start_new_session=True)
        self.track(proc)
        worker = self.pid_from("worker")
        self.addCleanup(lambda: self.end(worker))
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait()
        time.sleep(0.5)
        (self.proj / "go").touch()
        self.assertTrue(wait_gone(worker, 10.0), f"worker {worker} is still there after its runner died")

    @unittest.skipUnless(LINUX, "the test needs /proc to see a zombie as gone")
    def test_gil_hold_kills_worker_in_catastrophic_backtrack(self):
        self.write("marks.py", MARKS)
        self.write("test_gil.py", """
            import re
            import os
            import unittest

            import marks

            class GilTest(unittest.TestCase):
                def test_gil(self):
                    marks.mark("worker", marks.mine())
                    re.fullmatch(r"(a+)+", "a" * 34 + "!")
        """)
        ppath = self.write_sitecustomize()
        env = self.runner_env(ppath)
        proc = subprocess.Popen([sys.executable, str(RUNNER), "-s", str(self.tests), "-j", "1"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env=env, start_new_session=True)
        self.track(proc)
        worker = self.pid_from("worker")
        self.addCleanup(lambda: self.end(worker))
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait()
        self.assertTrue(wait_gone(worker, 10.0), f"worker {worker} is still there after its runner died")

    @unittest.skipUnless(LINUX, "the test needs /proc to see a zombie as gone")
    def test_watcher_cleanup_ends_watcher_with_worker(self):
        self.write("marks.py", MARKS)
        self.write("test_aaa_watcher.py", """
            import os
            import unittest
            from pathlib import Path

            import marks

            def find_watcher():
                # Returns the pid, start time, and parent of a process in this worker's process group, or None, None, None.
                # The group and the start time come from one read of /proc/<pid>/stat.
                me = os.getpid()
                raw = Path(f"/proc/{me}/stat").read_bytes()
                my_pgrp = int(raw[raw.rindex(b")") + 2:].split()[2])
                for entry in os.listdir("/proc"):
                    if not entry.isdigit() or int(entry) == me:
                        continue
                    try:
                        fields = Path("/proc", entry, "stat").read_bytes()
                        fields = fields[fields.rindex(b")") + 2:].split()
                    except (OSError, ValueError):
                        continue
                    if int(fields[2]) == my_pgrp:
                        return int(entry), fields[19].decode(), int(fields[1])
                return None, None, None

            class WatcherTest(unittest.TestCase):
                def test_a_finds_watcher(self):
                    watcher, started, ppid = find_watcher()
                    self.assertIsNotNone(watcher, "no other process shares this worker group")
                    marks.mark("watcher", f"{watcher} {started}")
                    self.assertNotEqual(ppid, os.getpid(), "the watcher parent is the worker")
        """)
        self.write("test_zzz_reap.py", """
            import time
            import unittest
            from pathlib import Path

            PROJECT = Path(__file__).resolve().parents[1]

            class ReapTest(unittest.TestCase):
                def test_b_watcher_is_gone(self):
                    watcher = int((PROJECT / "watcher").read_text().split()[0])
                    deadline = time.monotonic() + 5
                    while True:
                        try:
                            state = Path(f"/proc/{watcher}/stat").read_text().rsplit(") ", 1)[1][0]
                        except (FileNotFoundError, ProcessLookupError):
                            return
                        if state == "Z":
                            return
                        self.assertLess(time.monotonic(), deadline, f"watcher {watcher} is still {state}")
                        time.sleep(0.02)
        """)
        ppath = self.write_sitecustomize()
        env = self.runner_env(ppath)
        proc = subprocess.Popen([sys.executable, str(RUNNER), "-s", str(self.tests), "-j", "1"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=env, start_new_session=True)
        self.track(proc)
        try:
            out, err = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            import signal as _signal
            os.killpg(proc.pid, _signal.SIGKILL)
            proc.communicate()
            self.fail("the runner was still running after 60 s")
        self.assertEqual(proc.returncode, 0, err)


if __name__ == "__main__":
    unittest.main()
