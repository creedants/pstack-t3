"""The runner's environment rewrite keeps protecting the home directories its caller started with.

A runner replaces HOME and the XDG values for each child, so a child that computes its protected
roots from that environment alone loses the originals. The fixture here has no isolation prelude. It
asks for one write into each original directory, records the verdict, and starts a nested runner on
a second copy of itself so the nested report proves the roots survive a nested run.

The last cases cover the guard's own record files. One replaces the record name with a symlink to a
protected canary, and the guard must replace the name instead of following it. Another swaps the
record directory for a symlink at the moment the guard lists it, and the replacement target must keep
its file. The third makes the record directory itself a protected root, so the guard must skip that
candidate and keep the file it holds.
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

ROOT = Path(__file__).resolve().parents[2]
NAMES = ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR")
ORIGINALS = "PSTACK_TEST_PROTECTED_ROOTS"
PATHS = "PSTACK_TEST_ORIGINAL_PATHS"
REPORT = "PSTACK_TEST_ORIGINAL_REPORT"
NESTED = "PSTACK_TEST_ORIGINAL_NESTED"

FIXTURE = '''"""A fixture with no isolation prelude that writes into each original home directory it is given."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

PATHS = "PSTACK_TEST_ORIGINAL_PATHS"
REPORT = "PSTACK_TEST_ORIGINAL_REPORT"
NESTED = "PSTACK_TEST_ORIGINAL_NESTED"
REPO = @REPO@


def attempt(paths):
    """Return one verdict per original directory, refusing or allowing one write into each."""
    entries = {}
    for name, directory in paths.items():
        try:
            Path(directory, "written").write_text("x")
        except PermissionError:
            entries[name] = "refused"
        else:
            entries[name] = "allowed"
    return entries


class OriginalRootTest(unittest.TestCase):
    def test_no_original_directory_accepts_a_write_here_or_in_a_nested_run(self):
        self.assertIn("PSTACK_TEST_HOME_ROOT", os.environ)
        entries = attempt(json.loads(os.environ[PATHS]))
        Path(os.environ[REPORT]).write_text(json.dumps(entries))
        self.assertEqual([name for name, verdict in entries.items() if verdict == "allowed"], [])
        nested = os.environ.pop(NESTED, "")
        if not nested:
            return
        env = dict(os.environ)
        env[REPORT] = str(Path(os.environ[REPORT]).with_name("nested.json"))
        done = subprocess.run([sys.executable, "scripts/run_tests.py", "-s", nested, "-j", "1"],
                              cwd=REPO, env=env, capture_output=True, text=True, timeout=600)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
'''


ROUTE_FIXTURE = '''"""A fixture with no isolation prelude that applies each environment route and starts a probe."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, @REPO@)
from tests import isolation

isolation.install()

ORIGINALS = "PSTACK_TEST_PROTECTED_ROOTS"
REPORT = @REPORT@
TARGET = @TARGET@
ROUTES = ("remove", "rewrite", "clear", "worker_env")
PROBE = """import sys
import unittest
from pathlib import Path

sys.path.insert(0, @REPO@)
from tests import isolation

isolation.install()


class ProbeTest(unittest.TestCase):
    def test_the_write_is_refused(self):
        with self.assertRaises(PermissionError):
            Path(@TARGET@, @NAME@).write_text("x")
"""


def apply(name):
    """Apply one route to this environment and return what it did."""
    if name == "remove":
        del os.environ[ORIGINALS]
        return "removed"
    if name == "rewrite":
        os.environ[ORIGINALS] = json.dumps([str(isolation.root())])
        return "rewritten"
    if name == "clear":
        os.environ.clear()
        return "cleared"
    env = isolation.worker_env({}, Path(tempfile.mkdtemp(prefix="route-home-")))
    os.environ.clear()
    os.environ.update(env)
    return "built"


def probe(name):
    """Write a probe module inside the run root, start it in a grandchild, and return its verdict."""
    directory = Path(tempfile.mkdtemp(prefix=f"probe-{name}-"))
    body = PROBE.replace("@NAME@", repr(name))
    (directory / f"probe_{name}.py").write_text(body)
    done = subprocess.run([sys.executable, "-m", "unittest", f"probe_{name}"], cwd=directory,
                          env=dict(os.environ), capture_output=True, text=True, timeout=600)
    return ("refused" if done.returncode == 0 else "allowed"), done.stdout + done.stderr


class EnvironmentRouteTest(unittest.TestCase):
    def test_no_environment_route_drops_the_original_roots_a_child_protects(self):
        with self.assertRaises(PermissionError):
            Path(TARGET, "direct").write_text("x")
        report = {}
        for name in ROUTES:
            with self.subTest(name):
                saved = dict(os.environ)
                try:
                    try:
                        mutation = apply(name)
                    except PermissionError:
                        mutation = "refused"
                    verdict, output = probe(name)
                finally:
                    os.environ.clear()
                    os.environ.update(saved)
                report[name] = {"mutation": mutation, "probe": verdict, "output": output}
        Path(REPORT).write_text(json.dumps(report))
        self.assertEqual({name: entry["mutation"] for name, entry in report.items()},
                         {"remove": "refused", "rewrite": "refused", "clear": "cleared",
                          "worker_env": "built"})
        self.assertEqual([entry["probe"] for entry in report.values()], ["refused"] * len(ROUTES))
        for name, entry in report.items():
            self.assertFalse(Path(TARGET, name).exists(), entry["output"])
'''


COPIED_FIXTURE = '''"""A fixture with no isolation prelude that copies its environment and drops a trusted root."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, @REPO@)
from tests import isolation

isolation.install()

ORIGINALS = "PSTACK_TEST_PROTECTED_ROOTS"
TARGET = Path(@TARGET@)
HARMLESS = @HARMLESS@
REPORT = Path(@REPORT@)
ROUTES = ("remove", "empty", "append")
ENTRIES = ("named", "direct", "discover", "runner")
PROBE = """import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, @REPO@)
from tests import isolation

isolation.install()


class ProbeTest(unittest.TestCase):
    def test_the_original_write_is_refused(self):
        try:
            Path(@TARGET@, "written").write_text("x")
        except PermissionError:
            Path(os.environ["PSTACK_D4_PROBE_REPORT"]).write_text("refused")
            return
        Path(os.environ["PSTACK_D4_PROBE_REPORT"]).write_text("wrote")
        self.fail("the original root accepted a write")


if __name__ == "__main__":
    unittest.main()
"""


def copied(route):
    env = dict(os.environ)
    if route == "remove":
        del env[ORIGINALS]
    elif route == "empty":
        env[ORIGINALS] = "[]"
    else:
        env[ORIGINALS] = json.dumps([HARMLESS])
    return env


def command(entry, package, plain):
    if entry == "named":
        return [sys.executable, "-m", "unittest", "probe_pkg.test_probe"]
    if entry == "direct":
        return [sys.executable, str(package / "test_probe.py")]
    if entry == "discover":
        return [sys.executable, "-m", "unittest", "discover", "-s", str(plain), "-p", "test_probe.py"]
    return [sys.executable, str(Path(@REPO@) / "scripts" / "run_tests.py"), "-s", str(plain), "-j", "1"]


class CopiedEnvironmentTest(unittest.TestCase):
    def test_a_copied_environment_that_drops_the_record_keeps_the_original_root_protected(self):
        scratch = Path(tempfile.mkdtemp(prefix="copied-"))
        package = scratch / "probe_pkg"
        package.mkdir()
        (package / "__init__.py").write_text("")
        plain = scratch / "probe_dir"
        plain.mkdir()
        (package / "test_probe.py").write_text(PROBE)
        (plain / "test_probe.py").write_text(PROBE)
        reports = scratch / "reports"
        reports.mkdir()
        try:
            TARGET.joinpath("direct").write_text("x")
        except PermissionError:
            direct = "refused"
        else:
            direct = "allowed"
        found = {"direct": direct}
        for entry in ENTRIES:
            for route in ROUTES:
                report = reports / f"{entry}-{route}.txt"
                env = copied(route)
                env["PSTACK_D4_PROBE_REPORT"] = str(report)
                done = subprocess.run(command(entry, package, plain), cwd=scratch, env=env,
                                      capture_output=True, text=True, timeout=600)
                found[f"{entry} {route}"] = report.read_text() if report.exists() else f"no report: {done.stdout[-400:]}{done.stderr[-400:]}"
        REPORT.write_text(json.dumps(found))
        self.assertEqual(found.pop("direct"), "refused")
        self.assertEqual({name: verdict for name, verdict in found.items() if verdict != "refused"}, {}, found)
'''


COLON_FIXTURE = '''"""A fixture with no isolation prelude that writes into a directory whose name holds a colon."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, @REPO@)
from tests import isolation

isolation.install()


class ColonTest(unittest.TestCase):
    def test_the_write_is_refused(self):
        with self.assertRaises(PermissionError):
            Path(@TARGET@, "written").write_text("x")
'''


class OriginalRootsTest(unittest.TestCase):
    def test_a_write_into_an_original_home_directory_is_refused_in_the_run_and_in_a_nested_run(self):
        scratch = Path(tempfile.mkdtemp(prefix="originals-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        originals = {}
        for name in NAMES:
            directory = scratch / name.lower()
            directory.mkdir()
            originals[name] = str(directory)
        home = scratch / "home"
        home.mkdir()
        tmp = scratch / "tmp"
        tmp.mkdir()
        fixture = scratch / "fixture"
        fixture.mkdir()
        nested = scratch / "nested"
        nested.mkdir()
        body = FIXTURE.replace("@REPO@", repr(str(ROOT)))
        (fixture / "test_original_roots.py").write_text(body)
        (nested / "test_original_roots.py").write_text(body)
        env = {key: value for key, value in os.environ.items()
               if key not in (isolation.POINTER, isolation.TOKEN, ORIGINALS)}
        env.update(originals)
        env.update({PATHS: json.dumps(originals), REPORT: str(scratch / "report.json"), NESTED: str(nested),
                    "HOME": str(home), "TMPDIR": str(tmp)})
        done = subprocess.run([sys.executable, "scripts/run_tests.py", "-s", str(fixture), "-j", "1"],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        outer = json.loads((scratch / "report.json").read_text())
        inner = json.loads((scratch / "nested.json").read_text())
        self.assertEqual(sorted(outer), sorted(NAMES))
        self.assertEqual(sorted(inner), sorted(NAMES))
        self.assertEqual(sorted(outer.values()), ["refused"] * len(NAMES))
        self.assertEqual(sorted(inner.values()), ["refused"] * len(NAMES))
        for name, directory in originals.items():
            self.assertEqual(os.listdir(directory), [], name)


class EnvironmentRouteTest(unittest.TestCase):
    def test_no_environment_route_drops_the_original_roots_a_child_protects(self):
        scratch = Path(tempfile.mkdtemp(prefix="routes-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        state = scratch / "original-state"
        state.mkdir()
        home = scratch / "home"
        home.mkdir()
        tmp = scratch / "tmp"
        tmp.mkdir()
        fixture = scratch / "fixture"
        fixture.mkdir()
        report = scratch / "report.json"
        body = (ROUTE_FIXTURE.replace("@REPO@", repr(str(ROOT)))
                .replace("@TARGET@", repr(str(state)))
                .replace("@REPORT@", repr(str(report))))
        (fixture / "test_original_routes.py").write_text(body)
        env = {key: value for key, value in os.environ.items()
               if key not in (isolation.POINTER, isolation.TOKEN, ORIGINALS)}
        env.update({"XDG_STATE_HOME": str(state), "HOME": str(home), "TMPDIR": str(tmp)})
        done = subprocess.run([sys.executable, "scripts/run_tests.py", "-s", str(fixture), "-j", "1"],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        found = json.loads(report.read_text())
        self.assertEqual(sorted(found), ["clear", "remove", "rewrite", "worker_env"])
        self.assertEqual({name: entry["mutation"] for name, entry in found.items()},
                         {"clear": "cleared", "remove": "refused", "rewrite": "refused",
                          "worker_env": "built"})
        self.assertEqual([entry["probe"] for entry in found.values()], ["refused"] * len(found))
        self.assertEqual(os.listdir(state), [])


class MalformedRecordTest(unittest.TestCase):
    def test_a_malformed_or_dropping_record_is_refused_and_does_not_replace_the_value(self):
        root = str(isolation.root())
        cases = {
            "empty": "",
            "not json": "not json",
            "json object": json.dumps({"roots": [root]}),
            "relative path": json.dumps(["."]),
            "number": json.dumps([1]),
            "run root only": json.dumps([root]),
        }
        for name, value in cases.items():
            with self.subTest(name):
                saved = os.environ[ORIGINALS]
                try:
                    with self.assertRaises(PermissionError):
                        os.environ[ORIGINALS] = value
                    self.assertEqual(os.environ[ORIGINALS], saved)
                finally:
                    os.environ[ORIGINALS] = saved
        home = Path(tempfile.mkdtemp(prefix="malformed-home-"))
        self.addCleanup(shutil.rmtree, home, True)
        with self.assertRaises(SystemExit):
            isolation.worker_env({ORIGINALS: "not json"}, home)


class ColonNameTest(unittest.TestCase):
    def test_an_original_root_name_with_a_colon_keeps_its_protection(self):
        scratch = Path(tempfile.mkdtemp(prefix="colon-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        state = scratch / "original:state"
        state.mkdir()
        home = scratch / "home"
        home.mkdir()
        tmp = scratch / "tmp"
        tmp.mkdir()
        fixture = scratch / "fixture"
        fixture.mkdir()
        body = (COLON_FIXTURE.replace("@REPO@", repr(str(ROOT)))
                .replace("@TARGET@", repr(str(state))))
        (fixture / "test_colon_roots.py").write_text(body)
        env = {key: value for key, value in os.environ.items()
               if key not in (isolation.POINTER, isolation.TOKEN, ORIGINALS)}
        env.update({"XDG_STATE_HOME": str(state), "HOME": str(home), "TMPDIR": str(tmp)})
        done = subprocess.run([sys.executable, "scripts/run_tests.py", "-s", str(fixture), "-j", "1"],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(os.listdir(state), [])


class CopiedEnvironmentTest(unittest.TestCase):
    def test_a_copied_environment_that_drops_the_record_keeps_the_original_root_protected(self):
        scratch = Path(tempfile.mkdtemp(prefix="copied-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        state = scratch / "original-state"
        home = scratch / "home"
        tmp = scratch / "tmp"
        harmless = scratch / "harmless"
        fixture = scratch / "fixture"
        for path in (state, home, tmp, harmless, fixture):
            path.mkdir()
        report = scratch / "report.json"
        body = (COPIED_FIXTURE.replace("@REPO@", repr(str(ROOT)))
                .replace("@TARGET@", repr(str(state)))
                .replace("@HARMLESS@", repr(str(harmless)))
                .replace("@REPORT@", repr(str(report))))
        (fixture / "test_copied_env.py").write_text(body)
        env = {key: value for key, value in os.environ.items()
               if key not in (isolation.POINTER, isolation.TOKEN, ORIGINALS)}
        env.update({"XDG_STATE_HOME": str(state), "HOME": str(home), "TMPDIR": str(tmp)})
        done = subprocess.run([sys.executable, "scripts/run_tests.py", "-s", str(fixture), "-j", "1"],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        found = json.loads(report.read_text())
        self.assertEqual(found.pop("direct"), "refused", found)
        self.assertEqual({name: verdict for name, verdict in found.items() if verdict != "refused"}, {}, found)
        self.assertEqual(os.listdir(state), [])


LOADER = '''
import importlib.util

sys.path.insert(0, REPO)
spec = importlib.util.spec_from_file_location("pstack_test_isolation_roots", ROOTS)
module = importlib.util.module_from_spec(spec)
sys.modules["pstack_test_isolation_roots"] = module
spec.loader.exec_module(module)
module._TEMPORARIES = (BASE1, BASE2)
try:
    from tests import isolation
except SystemExit as problem:
    print("STOPPED", problem)
else:
    try:
        isolation.install()
    except SystemExit as problem:
        print("STOPPED", problem)
    else:
        print("INSTALLED")
'''

CONSTANTS = '''import json
import os
import sys

REPO = @REPO@
ROOTS = @ROOTS@
BASE1 = @BASE1@
BASE2 = @BASE2@
FOLDER = os.path.join(BASE1, "pstack-t3-isolation-%d" % os.getuid())
'''

SYMLINK_CHILD = CONSTANTS + '''CANARY = @CANARY@

if os.environ.get("PSTACK_D13_PHASE") != "two":
    exec(@LOADER@)
    os.environ["PSTACK_D13_PHASE"] = "two"
    os.execve(sys.executable, [sys.executable, __file__], dict(os.environ))

os.makedirs(FOLDER, mode=0o700, exist_ok=True)
name = os.path.join(FOLDER, "%d.json" % os.getpid())
if os.path.lexists(name):
    os.unlink(name)
os.symlink(CANARY, name)
exec(@LOADER@)
print("RECORD", os.path.islink(name), os.path.isfile(name))
'''

SWAP_CHILD = CONSTANTS + '''CANARY_DIR = @CANARY_DIR@
os.makedirs(FOLDER, mode=0o700, exist_ok=True)
with open(os.path.join(FOLDER, "999999.json"), "w") as handle:
    handle.write("{}")
os.chmod(os.path.join(FOLDER, "999999.json"), 0o600)
os.makedirs(CANARY_DIR, exist_ok=True)
with open(os.path.join(CANARY_DIR, "999999.json"), "w") as handle:
    handle.write("kept")
os.chmod(os.path.join(CANARY_DIR, "999999.json"), 0o600)
real_listdir = os.listdir


def swapping(target):
    if os.path.lexists(FOLDER) and not os.path.islink(FOLDER):
        os.rename(FOLDER, FOLDER + ".moved")
        os.symlink(CANARY_DIR, FOLDER)
    return real_listdir(target)


os.listdir = swapping
exec(@LOADER@)
'''

PROTECTED_CHILD = CONSTANTS + '''os.makedirs(FOLDER, mode=0o700, exist_ok=True)
CANARY = os.path.join(FOLDER, "999999.json")
with open(CANARY, "w") as handle:
    handle.write("kept")
os.chmod(CANARY, 0o600)
exec(@LOADER@)
print("CANARY", open(CANARY).read() if os.path.exists(CANARY) else "gone")
print("SECOND", os.path.exists(os.path.join(BASE2, "pstack-t3-isolation-%d" % os.getuid(),
                                            "%d.json" % os.getpid())))
'''


class RecordSafetyTest(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="record-safety-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.base1 = self.scratch / "base1"
        self.base2 = self.scratch / "base2"
        self.external = self.scratch / "external"
        self.home = self.scratch / "home"
        self.tmp = self.scratch / "tmp"
        for path in (self.base1, self.base2, self.external, self.home, self.tmp):
            path.mkdir()
        self.canary = self.external / "canary.json"
        self.canary.write_text("kept")
        self.folder = self.base1 / f"pstack-t3-isolation-{os.getuid()}"

    def child(self, template):
        """Return one child body with this case's paths and the loader filled in."""
        loader = LOADER

        def fill(text):
            return (text.replace("@REPO@", repr(str(ROOT)))
                    .replace("@ROOTS@", repr(str(ROOT / "tests" / "isolation_roots.py")))
                    .replace("@BASE1@", repr(str(self.base1)))
                    .replace("@BASE2@", repr(str(self.base2)))
                    .replace("@CANARY@", repr(str(self.canary)))
                    .replace("@CANARY_DIR@", repr(str(self.scratch / "canary-dir")))
                    .replace("@LOADER@", repr(loader)))
        return fill(template)

    def run_child(self, body, state=None):
        source = self.scratch / "child.py"
        source.write_text(body)
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "TMPDIR": str(self.tmp)}
        if state is not None:
            env["XDG_STATE_HOME"] = str(state)
        return subprocess.run([sys.executable, str(source)], env=env, capture_output=True,
                              text=True, cwd=str(self.scratch), timeout=600)

    def test_a_symlinked_record_name_is_replaced_instead_of_followed(self):
        done = self.run_child(self.child(SYMLINK_CHILD), state=self.external)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.canary.read_text(), "kept")
        self.assertIn("RECORD False True", done.stdout, done.stdout + done.stderr)
        records = list(self.folder.glob("*.json"))
        self.assertEqual(len(records), 1, done.stdout + done.stderr)
        self.assertFalse(records[0].is_symlink())

    def test_a_record_directory_swapped_for_a_symlink_leaves_the_replacement_canary(self):
        done = self.run_child(self.child(SWAP_CHILD))
        self.assertIn("STOPPED", done.stdout + done.stderr)
        canary = self.scratch / "canary-dir" / "999999.json"
        self.assertTrue(canary.is_file(), done.stdout + done.stderr)
        self.assertEqual(canary.read_text(), "kept")
        moved = self.folder.with_name(self.folder.name + ".moved")
        self.assertFalse((moved / "999999.json").exists(), done.stdout + done.stderr)

    def test_a_protected_record_directory_is_skipped_and_its_canary_is_kept(self):
        done = self.run_child(self.child(PROTECTED_CHILD), state=self.folder)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("CANARY kept", done.stdout, done.stdout + done.stderr)
        self.assertIn("SECOND True", done.stdout, done.stdout + done.stderr)
        self.assertEqual((self.folder / "999999.json").read_text(), "kept")


if __name__ == "__main__":
    unittest.main()
