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


if __name__ == "__main__":
    unittest.main()
