"""A Python child started with a minimal environment still protects the roots its parent protects.

A test that starts a child with `env={"PATH": ...}` drops the pointer, the token, and the protected-root
record together, so the child used to create a fresh run root and protect only the real home. The driver
here runs in its own process with a disposable XDG state root, so the guard it installs protects that root.
It then starts one child per entry point the suite uses with that minimal environment, and each child
reports what the guard did about one write into the original root. A last child runs after the driver's
record is replaced with text that is not a record, so an unreadable record must stop a child rather than
let it run with fewer roots.
"""

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
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHILD = '''import json
import os
import sys

sys.path.insert(0, %(repo)r)
from tests import isolation

root = isolation.install()
try:
    with open(os.path.join(%(target)r, "canary.txt"), "a") as handle:
        handle.write("damaged")
except PermissionError:
    verdict = "refused"
else:
    verdict = "wrote"
with open(%(report)r, "w") as handle:
    json.dump({"verdict": verdict, "root": str(root), "protected": list(isolation._protected)}, handle)
'''

DRIVER = '''"""A guarded driver that starts one child per entry point with a minimal environment."""

import json
import os
import subprocess
import sys

sys.path.insert(0, @REPO@)
from tests import isolation

isolation.install()

REPO = @REPO@
TARGET = @TARGET@
REPORTS = @REPORTS@
REPORT = @REPORT@
MINIMAL = {"PATH": os.environ["PATH"]}
CHILD = @CHILD@


def source(entry):
    """Return the child body for one entry point, writing its verdict to a file named after the entry."""
    return CHILD % {"repo": REPO, "target": TARGET, "report": os.path.join(REPORTS, entry + ".json")}


def run(entry):
    return subprocess.run([sys.executable, "-c", source(entry)], env=MINIMAL,
                          capture_output=True, text=True)


def popen(entry):
    started = subprocess.Popen([sys.executable, "-c", source(entry)], env=MINIMAL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = started.communicate()
    return subprocess.CompletedProcess(started.args, started.returncode, out, err)


def spawn(entry):
    status = os.spawnve(os.P_WAIT, sys.executable, [sys.executable, "-c", source(entry)], MINIMAL)
    return subprocess.CompletedProcess([], status, "", "")


def exec_child(entry):
    pid = os.fork()
    if pid == 0:
        try:
            os.execve(sys.executable, [sys.executable, "-c", source(entry)], MINIMAL)
        finally:
            os._exit(127)
    _, status = os.waitpid(pid, 0)
    return subprocess.CompletedProcess([], os.waitstatus_to_exitcode(status), "", "")


ENTRIES = {"run": run, "popen": popen, "spawn": spawn, "exec": exec_child}


def record(done):
    return {"returncode": done.returncode, "output": (done.stdout or "") + (done.stderr or "")}


found = {}
for name, start in ENTRIES.items():
    found[name] = record(start(name))
with open(REPORT, "w") as handle:
    json.dump(found, handle)

# Fail closed: a record that cannot be read stops the child instead of letting it run with fewer roots.
try:
    from tests import isolation_roots
except ImportError:
    found["corrupt"] = {"returncode": None, "output": "no record mechanism"}
else:
    with open(isolation_roots.record(os.getpid()), "w") as handle:
        handle.write("not a record")
    found["corrupt"] = record(run("corrupt"))
with open(REPORT, "w") as handle:
    json.dump(found, handle)
'''

TIME_LIMIT = 600
ENTRIES = ("run", "popen", "spawn", "exec")


class MinimalEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="minimal-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.original = self.scratch / "original-state"
        (self.original / "nested").mkdir(parents=True)
        self.canary = self.original / "canary.txt"
        self.canary.write_text("original")
        self.home = self.scratch / "home"
        self.home.mkdir()
        self.tmp = self.scratch / "tmp"
        self.tmp.mkdir()
        self.reports = self.scratch / "reports"
        self.reports.mkdir()
        self.report = self.scratch / "report.json"
        body = (DRIVER.replace("@REPO@", repr(str(Path(__file__).resolve().parents[2])))
                .replace("@TARGET@", repr(str(self.original)))
                .replace("@REPORTS@", repr(str(self.reports)))
                .replace("@REPORT@", repr(str(self.report)))
                .replace("@CHILD@", repr(CHILD)))
        self.driver = self.scratch / "driver.py"
        self.driver.write_text(body)

    def drive(self):
        """Run the driver in its own process with a disposable home, temporary directory, and state root."""
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "TMPDIR": str(self.tmp),
               "XDG_STATE_HOME": str(self.original)}
        done = subprocess.run([sys.executable, str(self.driver)], env=env, capture_output=True,
                              text=True, cwd=str(self.scratch), timeout=TIME_LIMIT)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return json.loads(self.report.read_text())

    def test_every_entry_point_keeps_the_original_root_protected_and_an_unreadable_record_stops_a_child(self):
        found = self.drive()
        for entry in ENTRIES:
            with self.subTest(entry):
                path = self.reports / f"{entry}.json"
                self.assertTrue(path.exists(), f"{entry} wrote no verdict: {found.get(entry)}")
                verdict = json.loads(path.read_text())
                self.assertEqual(verdict["verdict"], "refused", verdict)
                self.assertIn(str(self.original), verdict["protected"], verdict)
        self.assertEqual(self.canary.read_text(), "original")
        self.assertEqual(sorted(os.listdir(self.original)), ["canary.txt", "nested"])
        self.assertEqual(os.listdir(self.original / "nested"), [])
        self.assertIsNotNone(found["corrupt"]["returncode"], found["corrupt"])
        self.assertNotEqual(found["corrupt"]["returncode"], 0, found["corrupt"])
        self.assertFalse((self.reports / "corrupt.json").exists(), found["corrupt"])
        self.assertEqual(self.canary.read_text(), "original")


if __name__ == "__main__":
    unittest.main()
