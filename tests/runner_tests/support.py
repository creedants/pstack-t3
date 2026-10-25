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
# A report is a pid and the start time field 22 of /proc/<pid>/stat reports for it, or - where /proc has no entry for the pid.
# mine, sleeper, and orphan take a report from the process it names before that process can exit, and every step after that carries it unchanged.
MARKS = """
    import os
    import subprocess
    import sys
    import tempfile
    import time
    from pathlib import Path

    PROJECT = Path(__file__).resolve().parents[1]
    # Writes its own report to the path in argv 2 under the directory in argv 1, then sleeps for 60 s.
    PROGRAM = (
        "import os, sys, time\\n"
        "from pathlib import Path\\n"
        "def started():\\n"
        "    try:\\n"
        "        raw = Path('/proc/self/stat').read_bytes()\\n"
        "    except OSError:\\n"
        "        return '-'\\n"
        "    return raw[raw.rindex(b')') + 2:].split()[19].decode()\\n"
        "root, name = Path(sys.argv[1]), sys.argv[2]\\n"
        "(root / (name + '.tmp')).write_text(f'{os.getpid()} {started()}')\\n"
        "os.replace(root / (name + '.tmp'), root / name)\\n"
        "time.sleep(60)\\n"
    )
    # Starts a process that writes its own report in a new session, then ends.
    PARENT = (
        "import subprocess, sys\\n"
        "subprocess.Popen([sys.executable, *sys.argv[1:]], start_new_session=True,\\n"
        "                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\\n"
    )

    def mine():
        '''Return this process's pid and the start time its own /proc/self/stat reports, or - where /proc has no entry for it.'''
        try:
            raw = Path("/proc/self/stat").read_bytes()
        except OSError:
            return f"{os.getpid()} -"
        return f"{os.getpid()} {raw[raw.rindex(b')') + 2:].split()[19].decode()}"

    def reported(name, limit=10):
        '''Return the report the process that writes name made for itself.'''
        path = PROJECT / name
        deadline = time.monotonic() + limit
        while not path.exists():
            if time.monotonic() > deadline:
                raise AssertionError(f"{name} was not reported")
            time.sleep(0.01)
        return path.read_text()

    def sleeper(name):
        '''Start a process in a new session that writes its own report to name, and return that report.'''
        subprocess.Popen([sys.executable, "-c", PROGRAM, str(PROJECT), name], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return reported(name)

    def mark(name, report):
        '''Write the report a test gives for name. A report is a pid with the start time /proc reported for the process it names. A report that holds only a pid leaves the mark without a start time, and cleanup records nothing from it.

        The report is staged under this process's TMPDIR, inside its run root, because the guard refuses a rename whose source is outside it.'''
        handle, staged = tempfile.mkstemp(prefix=name + ".")
        with os.fdopen(handle, "w") as staging:
            staging.write(str(report))
        os.replace(staged, PROJECT / name)

    def orphan(name):
        '''Start a process whose parent starts a sleeper in a new session and ends. Return the sleeper's report.'''
        subprocess.run([sys.executable, "-c", PARENT, "-c", PROGRAM, str(PROJECT), name], check=True)
        return reported(name)
"""

ESCAPE = """
    import time
    import unittest

    import marks

    class EscapeTest(unittest.TestCase):
        def test_escape(self):
            marks.sleeper("child")
            time.sleep({seconds})
"""

HANG_AT_IMPORT = """
    import time

    import marks

    if {escape}:
        marks.sleeper("child")
    marks.mark("listing", marks.mine())
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
    "def started(pid):\n"
    "    raw = Path(f'/proc/{pid}/stat').read_bytes()\n"
    "    return raw[raw.rindex(b')') + 2:].split()[19].decode()\n"
    "root = Path(sys.argv[1])\n"
    "sleeper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', str(root)], start_new_session=True,\n"
    "                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "(root / 'sleeper.tmp').write_text(f'{sleeper.pid} {started(sleeper.pid)}')\n"
    "os.replace(root / 'sleeper.tmp', root / 'sleeper')\n"
    "while not (root / 'allow-parent-exit').exists():\n"
    "    time.sleep(.01)\n"
)
LAUNCHER = (
    "import os, subprocess, sys, time\n"
    "from pathlib import Path\n"
    "def started(pid):\n"
    "    raw = Path(f'/proc/{pid}/stat').read_bytes()\n"
    "    return raw[raw.rindex(b')') + 2:].split()[19].decode()\n"
    "root = Path(sys.argv[1])\n"
    f"child = subprocess.Popen([sys.executable, '-c', {LAUNCHER_CHILD!r}, str(root)],\n"
    "                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "(root / 'parent').write_text(f'{child.pid} {started(child.pid)}')\n"
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
            parent = int((PROJECT / "parent").read_text().split()[0])
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


HAS_PIDFD = hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal")


def pidfd(pid):
    """Return a pidfd for the process at pid, or None where the platform offers none or no process is at pid."""
    if not HAS_PIDFD:
        return None
    try:
        return os.pidfd_open(pid)
    except OSError:
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
        self.handles = {}
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
            # proc is this test's unreaped child, so the kernel keeps its pid and process group id until communicate reaps it.
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
        """Record a process the test started with subprocess.Popen. Cleanup ends it through a pidfd taken here where /proc reports a start time and the platform offers a pidfd."""
        self.remember(proc.pid)
        self.addCleanup(self.end, proc.pid)

    def remember(self, pid):
        """Record the process at pid by a pidfd taken now and the start time /proc reports for it now."""
        started = start_time(pid)
        if started is not None:
            self.hold(pid, started)

    def hold(self, pid, started):
        """Record a pidfd for the process at pid that was taken while /proc reported the start time given, for end to signal.

        Record nothing where /proc reports another start time, as it does after the kernel gives that pid to a new process, or where the pidfd was taken after that happened.
        """
        if not started or start_time(pid) != started:
            return
        handle = pidfd(pid)
        if handle is None:
            return
        if start_time(pid) != started:
            os.close(handle)
            return
        self.starts[pid] = started
        self.handles[pid] = handle

    def end(self, pid):
        """End the process the test recorded at pid through the pidfd taken while it ran, where /proc still reports the recorded start time.

        Signal nothing where the record holds no pidfd, or where /proc reports another start time, as it does after the kernel gives that pid to a new process.
        """
        handle = self.handles.pop(pid, None)
        started = self.starts.pop(pid, None)
        if handle is None or started is None or start_time(pid) != started:
            if handle is not None:
                os.close(handle)
            return
        try:
            signal.pidfd_send_signal(handle, signal.SIGKILL)
        except OSError:
            pass
        finally:
            os.close(handle)

    def pid_from(self, name, limit=30):
        """Return the pid code in the temporary project wrote with marks.mark. Record the process only where the mark carries the start time /proc still reports for it, and cleanup ends it through the pidfd taken here where the platform offers one."""
        path = self.proj / name
        deadline = time.monotonic() + limit
        while not path.exists():
            self.assertLess(time.monotonic(), deadline, f"{name} was not written")
            time.sleep(0.02)
        pid, _, started = path.read_text().partition(" ")
        pid = int(pid)
        self.hold(pid, started)
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
            except (FileNotFoundError, IndexError, ProcessLookupError):
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

