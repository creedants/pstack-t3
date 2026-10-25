"""A Python child started with a minimal environment still protects the roots its parent protects.

A test that starts a child with `env={"PATH": ...}` drops the pointer, the token, and the protected-root
record together, so the child used to create a fresh run root and protect only the real home. The driver
here runs in its own process with a disposable XDG state root, so the guard it installs protects that root.
It then starts one child per entry point a test uses to start a Python child, and each child
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

RECORD_DRIVER = '''"""A guarded driver whose planted records and active protection decide what a child inherits."""

import importlib.util
import json
import os
import subprocess
import sys

sys.path.insert(0, @REPO@)
spec = importlib.util.spec_from_file_location("pstack_test_isolation_roots", @ROOTS@)
roots = importlib.util.module_from_spec(spec)
sys.modules["pstack_test_isolation_roots"] = roots
spec.loader.exec_module(roots)
roots._TEMPORARIES = (@BASE1@, @BASE2@)
@PLANT@
from tests import isolation

isolation.install()
@LAUNCH@
'''

RECORD_CHILD = '''import importlib.util
import json
import os
import sys

sys.path.insert(0, @REPO@)
spec = importlib.util.spec_from_file_location("pstack_test_isolation_roots", @ROOTS@)
module = importlib.util.module_from_spec(spec)
sys.modules["pstack_test_isolation_roots"] = module
spec.loader.exec_module(module)
module._TEMPORARIES = (@BASE1@, @BASE2@)
from tests import isolation

found = {"verdict": "stopped", "protected": []}
try:
    isolation.install()
except SystemExit as problem:
    found["message"] = str(problem)
else:
    found["protected"] = list(isolation._protected)
    try:
        with open(os.path.join(@TARGET@, "canary.txt"), "a") as handle:
            handle.write("damaged")
    except PermissionError:
        found["verdict"] = "refused"
    else:
        found["verdict"] = "wrote"
with open(@REPORT@, "w") as handle:
    json.dump(found, handle)
'''

LAUNCH = '''ENV = {"PATH": os.environ["PATH"], "HOME": @CHILD_HOME@, "TMPDIR": @CHILD_TMP@}
SOURCE = @CHILD@
done = subprocess.run([sys.executable, "-c", SOURCE], env=ENV, capture_output=True, text=True)
if not os.path.exists(@REPORT@):
    with open(@REPORT@, "w") as handle:
        json.dump({"verdict": "stopped", "message": done.stdout + done.stderr,
                   "code": done.returncode}, handle)
'''

FORK_LAUNCH = '''ENV = {"PATH": os.environ["PATH"], "HOME": @CHILD_HOME@, "TMPDIR": @CHILD_TMP@}
SOURCE = @CHILD@
pid = os.fork()
if pid == 0:
    done = subprocess.run([sys.executable, "-c", SOURCE], env=ENV, capture_output=True, text=True)
    with open(@REPORT@ + ".launch", "w") as handle:
        handle.write("code %d\\n%s%s" % (done.returncode, done.stdout, done.stderr))
    os._exit(0)
os.waitpid(pid, 0)
'''

PROTECTING_LAUNCH = '''ENV = {"PATH": os.environ["PATH"], "HOME": @CHILD_HOME@, "TMPDIR": @CHILD_TMP@}
SOURCE = @CHILD@
with isolation.protecting(@TARGET@):
    done = subprocess.run([sys.executable, "-c", SOURCE], env=ENV, capture_output=True, text=True)
if not os.path.exists(@REPORT@):
    with open(@REPORT@, "w") as handle:
        json.dump({"verdict": "stopped", "message": done.stdout + done.stderr,
                   "code": done.returncode}, handle)
'''

STALE_PLANT = '''old = os.path.join(@SCRATCH_DIR@, "old")
os.makedirs(old, exist_ok=True)
folder = os.path.join(@BASE1@, "pstack-t3-isolation-%d" % os.getuid())
os.makedirs(folder, mode=0o700, exist_ok=True)
os.chmod(folder, 0o700)
stale = os.path.join(folder, "%d.json" % os.getpid())
with open(stale, "w") as handle:
    json.dump({"pid": os.getpid(), "uid": os.getuid(), "roots": [old]}, handle)
os.chmod(stale, 0o600)
'''

FORGED_PLANT = '''import json
import os
import sys

pid = int(sys.argv[1])
folder = os.path.join(@BASE1@, "pstack-t3-isolation-%d" % os.getuid())
os.makedirs(folder, mode=0o777, exist_ok=True)
os.chmod(folder, 0o777)
forged = os.path.join(folder, "%d.json" % pid)
with open(forged, "w") as handle:
    json.dump({"pid": pid, "uid": os.getuid(), "roots": [@BASE1@]}, handle)
os.chmod(forged, 0o600)
'''

FORGED_LAUNCH = '''planted = @FORGED@
done = subprocess.run([sys.executable, "-c", planted, str(os.getpid())],
                      env={"PATH": os.environ["PATH"]}, capture_output=True, text=True)
if done.returncode != 0:
    raise SystemExit("the forged record could not be planted: " + done.stdout + done.stderr)
ENV = {"PATH": os.environ["PATH"], "HOME": @CHILD_HOME@, "TMPDIR": @CHILD_TMP@}
SOURCE = @CHILD@
done = subprocess.run([sys.executable, "-c", SOURCE], env=ENV, capture_output=True, text=True)
if not os.path.exists(@REPORT@):
    with open(@REPORT@, "w") as handle:
        json.dump({"verdict": "stopped", "message": done.stdout + done.stderr,
                   "code": done.returncode}, handle)
'''


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


class RecordTrustTest(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="records-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.base1 = self.scratch / "base1"
        self.base2 = self.scratch / "base2"
        self.external = self.scratch / "external"
        self.home = self.scratch / "home"
        self.tmp = self.scratch / "tmp"
        self.child_home = self.scratch / "child-home"
        self.child_tmp = self.scratch / "child-tmp"
        for path in (self.base1, self.base2, self.external, self.home, self.tmp,
                     self.child_home, self.child_tmp):
            path.mkdir()
        self.canary = self.external / "canary.txt"
        self.canary.write_text("original")
        self.report = self.scratch / "child.json"

    def paths(self, template):
        """Return template with this case's paths and repository location filled in."""
        repo = Path(__file__).resolve().parents[2]
        return (template.replace("@REPO@", repr(str(repo)))
                .replace("@ROOTS@", repr(str(repo / "tests" / "isolation_roots.py")))
                .replace("@BASE1@", repr(str(self.base1)))
                .replace("@BASE2@", repr(str(self.base2)))
                .replace("@TARGET@", repr(str(self.external)))
                .replace("@CHILD_HOME@", repr(str(self.child_home)))
                .replace("@CHILD_TMP@", repr(str(self.child_tmp)))
                .replace("@REPORT@", repr(str(self.report)))
                .replace("@SCRATCH_DIR@", repr(str(self.scratch))))

    def fill(self, template):
        """Return template with the child source filled in as well."""
        return self.paths(template).replace("@CHILD@", repr(self.paths(RECORD_CHILD)))

    def drive(self, plant, launch, state=None, config=None):
        """Run a driver that installs the guard, then start its one child with a minimal environment."""
        body = self.fill(RECORD_DRIVER.replace("@PLANT@", self.paths(plant))
                         .replace("@LAUNCH@", self.fill(launch)))
        driver = self.scratch / "driver.py"
        driver.write_text(body)
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "TMPDIR": str(self.tmp)}
        if state is not None:
            env["XDG_STATE_HOME"] = str(state)
        if config is not None:
            env["XDG_CONFIG_HOME"] = str(config)
        done = subprocess.run([sys.executable, str(driver)], env=env, capture_output=True,
                              text=True, cwd=str(self.scratch), timeout=TIME_LIMIT)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(self.report.exists(), done.stdout + done.stderr)
        return json.loads(self.report.read_text())

    def assert_kept(self):
        self.assertEqual(self.canary.read_text(), "original")
        self.assertEqual(sorted(os.listdir(self.external)), ["canary.txt"])

    def test_a_stale_first_record_does_not_hide_the_genuine_record_in_a_later_candidate(self):
        found = self.drive(STALE_PLANT, LAUNCH, state=self.base1, config=self.external)
        self.assertEqual(found["verdict"], "refused", found)
        self.assertIn(str(self.external), found["protected"], found)
        self.assert_kept()

    def test_a_forged_record_in_a_writable_directory_stops_or_refuses_the_child(self):
        launch = FORGED_LAUNCH.replace("@FORGED@", repr(self.paths(FORGED_PLANT)))
        found = self.drive("", launch, state=self.base1, config=self.external)
        self.assertIn(found["verdict"], ("stopped", "refused"), found)
        self.assert_kept()

    def test_a_forked_guard_publishes_its_protection_for_the_child_it_starts(self):
        found = self.drive("", FORK_LAUNCH, state=self.external)
        self.assertEqual(found["verdict"], "refused", found)
        self.assert_kept()

    def test_an_active_protecting_context_is_published_for_the_child_it_starts(self):
        found = self.drive("", PROTECTING_LAUNCH)
        self.assertEqual(found["verdict"], "refused", found)
        self.assert_kept()


if __name__ == "__main__":
    unittest.main()
