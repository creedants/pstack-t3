from ..support import setUpModule
from tests.brigade.test_affected import AffectedBase


class AffectedPrivatePackagesTest(AffectedBase):
    def facade_repo(self):
        """A repo whose facade src/governor.py a hop-only ref reaches through src/land.py's import."""
        self.git_project()
        self.write("src/governor.py",
                   "from ._governor.admission import slot\nfrom ._governor import marker\n")
        self.write("src/_governor/__init__.py", "from .extra import marker\n")
        self.write("src/_governor/admission.py", "def slot():\n    return 1\n")
        self.write("src/_governor/extra.py", "MARKER = 1\n")
        self.write("src/_governor/deep.py", "HIDE = 1\n")
        self.write("src/land.py", "from . import governor\n")
        self.write("src/steady.py", "PLAIN = 1\n")
        self.write("tests/test_direct.py",
                   "import runpy\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "A = ROOT / \"src/governor.py\"\n"
                   "def test_d():\n    runpy.run_path(str(A))\n")
        self.write("tests/test_hopper.py",
                   "import runpy\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "A = ROOT / \"src/land.py\"\n"
                   "def test_h():\n    runpy.run_path(str(A))\n")
        self.write("tests/test_plain.py",
                   "import runpy\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "A = ROOT / \"src/steady.py\"\n"
                   "def test_p():\n    runpy.run_path(str(A))\n")
        self.track()

    def test_hopped_facade_selects_private_files_fails_when_the_facade_hops_not_expanded(self):
        self.facade_repo()
        out = self.affected("--paths", "src/_governor/admission.py")
        self.assertIn("tests.test_direct", out)
        self.assertIn("tests.test_hopper", out)
        self.assertNotIn("No affected mapping", out)

    def test_private_init_member_selects_facade_tests_fails_when_init_import_not_followed(self):
        self.facade_repo()
        out = self.affected("--paths", "src/_governor/extra.py")
        self.assertIn("tests.test_direct", out)
        self.assertIn("tests.test_hopper", out)
        self.assertNotIn("No affected mapping", out)

    def test_private_member_of_a_member_not_mapped_fails_when_expansion_not_bounded(self):
        self.facade_repo()
        out = self.affected("--paths", "src/_governor/deep.py")
        self.assertIn("No affected mapping", out)

    def test_loader_outside_the_private_package_maps_unchanged_fails_when_expansion_disturbs_keys(self):
        self.facade_repo()
        self.assertIn("tests.test_plain", self.affected("--paths", "src/steady.py"))
        self.assertIn("tests.test_direct", self.affected("--paths", "src/governor.py"))
        self.assertIn("tests.test_hopper", self.affected("--paths", "src/governor.py"))
        self.assertIn("tests.test_hopper", self.affected("--paths", "src/land.py"))
