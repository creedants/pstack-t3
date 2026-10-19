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

    def test_brigade_source_and_twin_pick_briefs_without_times_fails_when_helper_union_not_narrowed(self):
        root = str(self.archive_root())
        for rel in ("t3/added/brigade/scripts/brigade.py", "skills/brigade/scripts/brigade.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.brigade.test_briefs", out)
            self.assertNotIn("tests.landing.test_times", out)
            self.assertNotIn("No affected mapping", out)

    def test_landing_source_and_twin_pick_briefs_and_times_fails_when_landing_entry_not_carried(self):
        root = str(self.archive_root())
        for rel in ("t3/added/landing/scripts/land.py", "skills/landing/scripts/land.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.brigade.test_briefs", out)
            self.assertIn("tests.landing.test_times", out)
            self.assertNotIn("No affected mapping", out)

    def test_briefs_lib_one_hop_maps_fails_when_helper_carried_entry_import_not_followed(self):
        root = str(self.archive_root())
        for rel in ("t3/added/brigade/scripts/brigade_lib/briefs.py", "skills/brigade/scripts/brigade_lib/briefs.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.brigade.test_briefs", out)
            self.assertNotIn("tests.landing.test_times", out)
            self.assertNotIn("No affected mapping", out)

    def test_governor_one_hop_maps_fails_when_helper_carried_entry_import_not_followed(self):
        root = str(self.archive_root())
        for rel in ("t3/added/landing/scripts/landing_lib/governor.py", "skills/landing/scripts/landing_lib/governor.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.landing.test_slot", out)
            self.assertIn("tests.brigade.test_briefs", out)
            self.assertNotIn("No affected mapping", out)

    def test_activity_source_and_twin_pick_activity_and_render_fails_when_transitive_import_not_closed(self):
        root = str(self.archive_root())
        for rel in ("t3/added/brigade/scripts/activity.py", "skills/brigade/scripts/activity.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.test_activity", out)
            self.assertIn("tests.test_activity_render", out)
            self.assertNotIn("No affected mapping", out)
