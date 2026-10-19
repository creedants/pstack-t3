from ..support import setUpModule
from tests.brigade.test_affected import AffectedBase


class AffectedPrivatePackagesTest(AffectedBase):
    def facade_repo(self):
        """A repo whose private package src/_governor is reached by the load ref src/governor.py directly and by the load ref src/land.py through one hop."""
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

    def facade_modules(self, path):
        """The module names the picker's output names, once `No affected mapping` is out."""
        out = self.affected("--paths", path)
        self.assertNotIn("No affected mapping", out)
        head = out.index("Affected test modules are ") + len("Affected test modules are ")
        tail = out.index("Run them in the foreground")
        return frozenset(part.strip().strip("`").strip() for part in out[head:tail].split(","))

    def test_private_file_and_init_member_map_with_the_facade_fails_when_the_facade_imports_not_expanded(self):
        self.facade_repo()
        modules = self.facade_modules("src/governor.py")
        self.assertIn("tests.test_direct", modules)
        self.assertIn("tests.test_hopper", modules)
        self.assertEqual(self.facade_modules("src/_governor/admission.py"), modules)
        self.assertEqual(self.facade_modules("src/_governor/extra.py"), modules)

    def test_private_file_no_import_reaches_not_mapped_fails_when_expansion_beyond_imports(self):
        self.facade_repo()
        self.assertIn("No affected mapping", self.affected("--paths", "src/_governor/deep.py"))

    def test_loader_outside_the_private_package_maps_its_tests_fails_when_expansion_disturbs_keys(self):
        self.facade_repo()
        self.assertIn("tests.test_plain", self.affected("--paths", "src/steady.py"))
        self.assertIn("tests.test_direct", self.affected("--paths", "src/governor.py"))
        self.assertIn("tests.test_hopper", self.affected("--paths", "src/land.py"))
