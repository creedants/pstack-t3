"""The host-gh guard shim, run through real subprocesses for its exit status and PATH lookup."""

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import setUpModule

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ._support.host_gh_guard import HostGhGuard


class HostGhGuardTest(unittest.TestCase):

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.base = Path(self.scratch.name).resolve()
        before = os.environ["PATH"]
        self.addCleanup(os.environ.__setitem__, "PATH", before)
        self.guard = HostGhGuard(self.base)
        self.guard.install()

    def run_shim(self, *arguments, path=None):
        """Run gh with path as the whole PATH. Default is the shim's own directory alone."""
        return subprocess.run(["gh", *arguments], capture_output=True, text=True,
                              env={"PATH": path or str(self.guard.script.parent)})

    def test_a_stub_after_the_shim_on_path_answers_and_nothing_is_recorded(self):
        stub = self.base / "stub"
        stub.mkdir()
        script = stub / "gh"
        script.write_text(f"#!{sys.executable}\nimport sys\nprint(' '.join(sys.argv[1:]))\nsys.exit(23)\n")
        script.chmod(0o755)
        result = self.run_shim("repo", "view", path=os.pathsep.join([str(self.guard.script.parent), str(stub)]))
        self.assertEqual((result.returncode, result.stdout.strip()), (23, "repo view"))
        self.assertEqual(self.guard.calls(), [])

    def test_a_gh_outside_the_temporary_directory_and_tests_is_not_run_recorded_and_exits_127(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        marker = Path(outside.name) / "ran"
        script = Path(outside.name) / "gh"
        script.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('')\n")
        script.chmod(0o755)
        result = self.run_shim("repo", "view", path=os.pathsep.join([str(self.guard.script.parent), outside.name]))
        self.assertEqual(result.returncode, 127)
        self.assertFalse(marker.exists())
        self.assertEqual(self.guard.calls(), [["repo", "view"]])

    def test_a_call_with_no_arguments_is_recorded_once(self):
        result = self.run_shim()
        self.assertEqual(result.returncode, 127)
        self.assertEqual(self.guard.calls(), [[]])

    def test_a_call_whose_arguments_hold_an_empty_string_and_a_newline_is_recorded_once(self):
        result = self.run_shim("", "line\nbreak", "tail")
        self.assertEqual(result.returncode, 127)
        self.assertEqual(self.guard.calls(), [["", "line\nbreak", "tail"]])

    def test_no_gh_after_the_shim_is_recorded_and_exits_127(self):
        result = self.run_shim("repo", "view")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (127, "", ""))
        self.assertEqual(self.guard.calls(), [["repo", "view"]])

    def test_the_failure_message_names_the_call_with_no_stub_and_lists_the_recorded_calls(self):
        self.run_shim("repo", "view")
        with self.assertRaises(AssertionError) as raised:
            self.guard.assert_unused()
        self.assertEqual(
            str(raised.exception),
            "this landing test called gh with no stub of its own; "
            "recorded calls: [['repo', 'view']]")
