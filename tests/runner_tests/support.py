import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_tests.py"
sys.path.insert(0, str(ROOT / "scripts"))

import run_tests

RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
LINUX = sys.platform.startswith("linux")
NEEDS_LINUX = "the runner sweeps the processes below it on Linux only"

PASSING = """
    import unittest

    class AlphaTest(unittest.TestCase):
        def test_one(self):
            self.assertEqual(1, 1)

        def test_two(self):
            self.assertEqual(2, 2)
"""


GOOD = """
    import unittest

    class GoodTest(unittest.TestCase):
        def test_good(self):
            pass
"""

# A temporary project's tests import this to tell the test that owns the project which processes they started.
MARKS = """
    import os
    import subprocess
    import sys
    from pathlib import Path

    PROJECT = Path(__file__).resolve().parents[1]
    SLEEPER = ["-c", "import time; time.sleep(60)", str(PROJECT)]
    PARENT = (
        "import subprocess, sys\\n"
        "child = subprocess.Popen([sys.executable, *sys.argv[1:]], start_new_session=True,\\n"
        "                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\\n"
        "print(child.pid)\\n"
    )

    def mark(name, pid):
        (PROJECT / (name + ".tmp")).write_text(str(pid))
        os.replace(PROJECT / (name + ".tmp"), PROJECT / name)

    def orphan():
        parent = subprocess.run([sys.executable, "-c", PARENT, *SLEEPER], capture_output=True, text=True, check=True)
        return int(parent.stdout)
"""

ESCAPE = """
    import subprocess
    import sys
    import time
    import unittest

    import marks

    class EscapeTest(unittest.TestCase):
        def test_escape(self):
            child = subprocess.Popen([sys.executable, *marks.SLEEPER], start_new_session=True)
            marks.mark("child", child.pid)
            time.sleep({seconds})
"""

HANG_AT_IMPORT = """
    import os
    import subprocess
    import sys
    import time

    import marks

    if {escape}:
        marks.mark("child", subprocess.Popen([sys.executable, *marks.SLEEPER], start_new_session=True).pid)
    marks.mark("listing", os.getpid())
    print("importing", flush=True)
    time.sleep(60)

    import unittest

    class ProbeTest(unittest.TestCase):
        def test_probe(self):
            pass
"""

# Each line is written before a passing test ends, so the test's own result follows it.
WRITES_TO_ITS_RESULT_FILE = """
    import json
    import sys
    import unittest

    class WriteTest(unittest.TestCase):
        def test_write(self):
            spec = json.load(open(sys.argv[sys.argv.index("--worker") + 1]))
            with open(spec["results"], "a") as out:
                for line in {lines!r}:
                    out.write(line + "\\n")
"""

NEVER_AN_EVENT = """
    import json
    import sys
    import time
    import unittest

    class JunkTest(unittest.TestCase):
        def test_junk(self):
            spec = json.load(open(sys.argv[sys.argv.index("--worker") + 1]))
            with open(spec["results"], "a") as out:
                for _ in range(1200):
                    out.write({text!r})
                    out.flush()
                    time.sleep(0.05)
"""

# {body} is DURING_THE_TEST or AT_EXIT with remove, replace, or shorten for {act}.
RESULT_FILE = """
    import atexit
    import os
    import shutil
    import time
    import unittest
    from pathlib import Path

    # The runner gives a worker one directory. It holds the result file, the worker's output, and TMPDIR.
    HOME = Path(os.environ["TMPDIR"]).parent
    RESULTS = HOME / "results.jsonl"

    def remove():
        for entry in HOME.iterdir():
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()

    def replace():
        for entry in HOME.iterdir():
            if entry.is_file():
                copy = entry.with_name(entry.name + ".copy")
                shutil.copyfile(entry, copy)
                os.replace(copy, entry)

    def shorten():
        # One second is 20 of the runner's poll intervals. The test relies on the runner reading in that time what the worker has written.
        time.sleep(1)
        os.truncate(RESULTS, 8)

    class ResultFileTest(unittest.TestCase):
        def test_result_file(self):
            {body}
"""
DURING_THE_TEST = "{act}(); time.sleep(60)"
AT_EXIT = "atexit.register({act})"

# Argument 1 is the project directory. The launcher starts a child that starts a sleeper in a new session, then
# becomes the command in its other arguments. The child ends when allow-parent-exit appears in the project.
LAUNCHER_CHILD = (
    "import os, subprocess, sys, time\n"
    "from pathlib import Path\n"
    "root = Path(sys.argv[1])\n"
    "sleeper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', str(root)], start_new_session=True,\n"
    "                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "(root / 'sleeper.tmp').write_text(str(sleeper.pid))\n"
    "os.replace(root / 'sleeper.tmp', root / 'sleeper')\n"
    "while not (root / 'allow-parent-exit').exists():\n"
    "    time.sleep(.01)\n"
)
LAUNCHER = (
    "import os, subprocess, sys, time\n"
    "from pathlib import Path\n"
    "root = Path(sys.argv[1])\n"
    f"child = subprocess.Popen([sys.executable, '-c', {LAUNCHER_CHILD!r}, str(root)],\n"
    "                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "(root / 'parent').write_text(str(child.pid))\n"
    "while not (root / 'sleeper').exists():\n"
    "    time.sleep(.01)\n"
    "os.execv(sys.executable, [sys.executable, *sys.argv[2:]])\n"
)

# Lets the launcher's child end, waits until it has ended, and then sleeps {seconds}.
RELEASES_THE_PARENT = """
    import time
    import unittest
    from pathlib import Path

    PROJECT = Path(__file__).resolve().parents[1]

    def ended(pid):
        try:
            return Path(f"/proc/{{pid}}/stat").read_text().rsplit(") ", 1)[1][0] == "Z"
        except FileNotFoundError:
            return True

    class ReleaseTest(unittest.TestCase):
        def test_release(self):
            parent = int((PROJECT / "parent").read_text())
            (PROJECT / "allow-parent-exit").touch()
            deadline = time.monotonic() + 20
            while not ended(parent):
                self.assertLess(time.monotonic(), deadline, "the launcher's child is still running")
                time.sleep(0.01)
            (PROJECT / "released").touch()
            time.sleep({seconds})
"""
INHERITED = "run_tests: this process had a child before the run, so the runner ends only the process groups of the children it starts"


def start_time(pid):
    """Return the start time /proc reports for pid, or None where it reports none."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


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


class RunnerCase:
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.starts = {}
        self.proj = Path(tmp.name).resolve() / "proj"
        self.tests = self.proj / "tests"
        self.tests.mkdir(parents=True)
        # The runner makes its scratch directory here.
        self.scratch = Path(tmp.name).resolve() / "scratch"
        self.scratch.mkdir()

    def write(self, name, body):
        (self.tests / name).write_text(textwrap.dedent(body))

    def start(self, *args, env=None, before=()):
        proc = subprocess.Popen([sys.executable, *before, str(RUNNER), "-s", str(self.tests), "-j", "2", *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env={**(env or os.environ), "TMPDIR": str(self.scratch)}, start_new_session=True)
        self.track(proc)
        self.addCleanup(lambda: (proc.stdout.close(), proc.stderr.close()))
        return proc

    def finish(self, proc, limit=60):
        try:
            out, err = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            self.fail(f"the runner was still running after {limit} s")
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)

    def runner(self, *args, env=None, limit=60):
        return self.finish(self.start(*args, env=env), limit)

    def serial(self):
        return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=self.proj,
                              capture_output=True, text=True)

    def serial_count(self):
        return RAN.search(self.serial().stderr).group(1)

    def track(self, proc):
        """Record a process the test started with subprocess.Popen, so that cleanup ends it."""
        self.remember(proc.pid)
        self.addCleanup(self.end, proc.pid)

    def remember(self, pid):
        """Record the process at pid by the start time /proc reports for it, for end to check at cleanup."""
        self.starts[pid] = start_time(pid)

    def end(self, pid):
        """End the process the test recorded at pid. Leave the pid alone where the test holds no start time for it, or where /proc reports another one, as it does after the kernel gives that pid to a new process."""
        started = self.starts.get(pid)
        if started is None or start_time(pid) != started:
            return
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    def pid_from(self, name, limit=30):
        """Return the pid code in the temporary project wrote with marks.mark. At cleanup, end that process."""
        path = self.proj / name
        deadline = time.monotonic() + limit
        while not path.exists():
            self.assertLess(time.monotonic(), deadline, f"{name} was not written")
            time.sleep(0.02)
        pid = int(path.read_text())
        self.remember(pid)
        self.addCleanup(self.end, pid)
        return pid

    def assert_gone(self, pid):
        with self.assertRaises(ProcessLookupError, msg=f"process {pid} is still there"):
            os.kill(pid, 0)

    def assert_alive(self, pid):
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1][0]
        except FileNotFoundError:
            state = "gone"
        self.assertIn(state, ("S", "R"), f"process {pid}")

    def assert_dead(self, pid, limit=5):
        """Assert the process is gone, whether or not anything reaped it. A process that ended and is not yet reaped reads as a zombie."""
        deadline = time.monotonic() + limit
        while True:
            try:
                state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1][0]
            except (FileNotFoundError, IndexError):
                return
            if state == "Z":
                return
            self.assertLess(time.monotonic(), deadline, f"process {pid} is still {state}")
            time.sleep(0.02)

    def inherited(self, *args, seconds, sig=None):
        """Run RELEASES_THE_PARENT in a runner whose process had a child before the run. Return the runner's result and the pid of that child's sleeper."""
        self.write("test_release.py", RELEASES_THE_PARENT.format(seconds=seconds))
        proc = self.start(*args, before=("-c", LAUNCHER, str(self.proj)))
        sleeper = self.pid_from("sleeper")
        self.pid_from("parent")
        if sig is not None:
            deadline = time.monotonic() + 30
            while not (self.proj / "released").exists():
                self.assertLess(time.monotonic(), deadline, "the test did not let the launcher's child end")
                time.sleep(0.02)
            proc.send_signal(sig)
        return self.finish(proc), sleeper

    def result_file(self, act, body):
        """Run RESULT_FILE with that act and body. Return the runner's stderr."""
        self.write("test_result_file.py", RESULT_FILE.format(body=body.format(act=act)))
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nERROR: shard 1 of test_result_file\n", result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "1")
        return result.stderr

    def assert_stopped_and_lost(self, stderr, what):
        self.assertRegex(stderr, r"\nThe worker for shard 1 was killed by signal 9 before the runner read that it had finished\.\n"
                                 rf"Its result file {what}\. The runner stopped reading it\.\n")
        self.assertIn("\nLOST: test_result_file.ResultFileTest.test_result_file\n" + "-" * 70 + "\n"
                      "The runner stopped reading the result file of shard 1 before it read a result for this test.\n", stderr)
        self.assertTrue(stderr.endswith("\n\nFAILED (errors=1, lost=1)\n"), stderr)

    def assert_stopped_and_none_lost(self, stderr, what):
        self.assertRegex(stderr, rf"\nIts result file {what}\. The runner stopped reading it\.\n")
        self.assertTrue(stderr.endswith("\n\nFAILED (errors=1)\n"), stderr)

    def escape(self, *args, seconds, sig=None):
        """Run a test that starts a sleeper in a new session. Return the runner's result and the sleeper's pid."""
        self.write("marks.py", MARKS)
        self.write("test_escape.py", ESCAPE.format(seconds=seconds))
        proc = self.start(*args)
        child = self.pid_from("child")
        if sig is not None:
            proc.send_signal(sig)
        return self.finish(proc), child

