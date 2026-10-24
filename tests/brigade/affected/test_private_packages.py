if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

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

    def test_loader_outside_the_private_package_maps_its_tests_fails_when_expansion_drops_their_tests(self):
        self.facade_repo()
        self.assertIn("tests.test_plain", self.affected("--paths", "src/steady.py"))
        self.assertIn("tests.test_direct", self.affected("--paths", "src/governor.py"))
        self.assertIn("tests.test_hopper", self.affected("--paths", "src/land.py"))

    def direct_loader_repo(self):
        self.git_project()
        self.write("src/__init__.py", "")
        self.write("src/governor.py", "from ._governor.admission import slot\n")
        self.write("src/_governor/__init__.py", "from .extra import marker\n")
        self.write("src/_governor/admission.py", "def slot():\n    return 1\n")
        self.write("src/_governor/extra.py", "MARKER = 1\n")
        self.write("tests/test_facade.py",
                   "import importlib.util\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "A = ROOT / \"src/governor.py\"\n"
                   "def test_f():\n"
                   "    spec = importlib.util.spec_from_file_location(\"src.governor\", A)\n"
                   "    module = importlib.util.module_from_spec(spec)\n"
                   "    spec.loader.exec_module(module)\n"
                   "    assert module.slot() == 1\n")
        self.track()

    def pin_member_repo(self):
        self.git_project()
        self.write("src/__init__.py", "")
        self.write("src/governor.py",
                   "from ._governor.admission import slot\n"
                   "MESSAGE = \"a distinctive facade message that is deliberately longer than forty chars\"\n")
        self.write("src/_governor/__init__.py", "")
        self.write("src/_governor/admission.py", "def slot():\n    return 1\n")
        self.write("tests/__init__.py", "")
        self.write("tests/runner.py",
                   "import importlib\n"
                   "def message():\n"
                   "    return importlib.import_module(\"src.governor\").MESSAGE\n")
        self.write("tests/test_facade.py",
                   "from .runner import message\n"
                   "def test_f():\n"
                   "    assert message() == \"a distinctive facade message that is deliberately longer than forty chars\"\n")
        self.write("tests/test_member.py",
                   "from src._governor.admission import slot\n"
                   "def test_m():\n"
                   "    assert slot() == 1\n")
        self.track()

    def test_init_member_of_directly_loaded_facade_maps_with_the_facade_fails_when_the_facade_imports_skip_the_init(self):
        self.direct_loader_repo()
        modules = self.facade_modules("src/governor.py")
        self.assertEqual(self.facade_modules("src/_governor/admission.py"), modules)
        self.assertEqual(self.facade_modules("src/_governor/extra.py"), modules)

    def test_member_with_its_own_mapping_gains_the_facade_tests_even_when_the_facade_test_comes_from_a_literal_pin(self):
        self.pin_member_repo()
        self.assertEqual(self.facade_modules("src/_governor/admission.py"),
                         self.facade_modules("src/governor.py") | {"tests.test_member"})
