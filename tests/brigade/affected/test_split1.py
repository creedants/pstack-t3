import json
import os
import posixpath
import tempfile
from unittest import mock

from ..support import Path, ROOT, setUpModule
from tests.brigade.test_affected import AffectedBase, ALWAYS, LAYOUT, WORKFLOW, _base_resolve_import, reference_close


class AffectedSplit1Test(AffectedBase):
    def test_diff_names_changed_file_fails_when_diff_not_read(self):
        self.git_project()
        self.write("src/a.py", "x = 1\n")
        self.write("tests/test_diff.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "def test_f():\n    (ROOT / \"src/a.py\").read_text()\n")
        self.track()
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.write("src/a.py", "x = 2\n")
        self.git("add", "-A")
        out = self.affected("--diff", "HEAD")
        self.assertIn("tests.test_diff", out)
        self.assertIn(ALWAYS, out)

    def test_git_nonzero_is_error_fails_when_error_swallowed(self):
        self.git_project()
        self.write("tests/test_other.py", "def test_o():\n    pass\n")
        self.track()
        err = self.affected("--diff", "not-a-ref...also-bad", ok=False)
        self.assertIn("git diff failed", err)

    def test_paths_and_diff_together_exit_1_fails_when_exclusivity_missing(self):
        self.git_project()
        self.write("tests/test_other.py", "def test_o():\n    pass\n")
        self.track()
        err = self.affected("--paths", "src/a.py", "--diff", "HEAD", ok=False, code=1)
        self.assertIn("use either --paths or --diff", err)

    def test_neither_paths_nor_diff_exits_1_fails_when_neither_allowed(self):
        self.git_project()
        self.write("tests/test_other.py", "def test_o():\n    pass\n")
        self.track()
        err = self.affected(ok=False, code=1)
        self.assertIn("affected needs --paths or --diff", err)

    def test_import_one_hop_maps_fails_when_import_not_followed(self):
        self.git_project()
        self.write("src/a.py", "import b\n")
        self.write("src/b.py", "y = 1\n")
        self.write("tests/test_hop.py",
                   "import runpy\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "A = ROOT / \"src/a.py\"\n"
                   "def test_h():\n    runpy.run_path(str(A))\n")
        self.track()
        out = self.affected("--paths", "src/b.py")
        self.assertIn("tests.test_hop", out)

    def test_import_two_hops_not_mapped_fails_when_hops_not_limited(self):
        self.git_project()
        self.write("src/a.py", "import b\n")
        self.write("src/b.py", "import c\n")
        self.write("src/c.py", "z = 1\n")
        self.write("tests/test_hop2.py",
                   "import runpy\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "A = ROOT / \"src/a.py\"\n"
                   "def test_h():\n    runpy.run_path(str(A))\n")
        self.track()
        out = self.affected("--paths", "src/c.py")
        self.assertIn("src/c.py", out)
        self.assertIn("full suite", out)

    def test_pin_maps_fails_when_pin_ignored(self):
        self.git_project()
        pin = "pin-" + "x" * 36
        self.assertEqual(len(pin), 40)
        self.write("src/pinned.py", f"MARK = \"{pin}\"\n")
        self.write("tests/test_pin.py", f"MARK = \"{pin}\"\n\ndef test_p():\n    assert MARK\n")
        self.track()
        out = self.affected("--paths", "src/pinned.py")
        self.assertIn("tests.test_pin", out)
        self.assertIn(ALWAYS, out)
