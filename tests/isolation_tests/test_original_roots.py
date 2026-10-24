"""The runner's environment rewrite keeps protecting the home directories its caller started with.

A runner replaces HOME and the XDG values for each child, so a child that computes its protected
roots from that environment alone loses the originals. The fixture here has no isolation prelude. It
asks for one write into each original directory, records the verdict, and starts a nested runner on
a second copy of itself so the nested report proves the roots survive a nested run.
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


if __name__ == "__main__":
    unittest.main()
