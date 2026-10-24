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


class AffectedTransitiveTest(AffectedBase):
    def test_support_module_names_source_maps_importer_fails_when_support_not_closed(self):
        self.git_project()
        self.write("src/x.py", "x = 1\n")
        self.write("tests/support.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "SRC = ROOT / \"src/x.py\"\n"
                   "def payload():\n    return SRC.read_text()\n")
        self.write("tests/test_a.py",
                   "from tests.support import payload\n"
                   "def test_a():\n    assert payload\n")
        self.track()
        out = self.affected("--paths", "src/x.py")
        self.assertIn("tests.test_a", out)
        self.assertNotIn("No affected mapping", out)

    def test_mixin_module_loads_source_maps_inheritor_fails_when_mixin_not_closed(self):
        self.git_project()
        self.write("src/y.py", "y = 1\n")
        self.write("tests/pkg/__init__.py", "")
        self.write("tests/pkg/mixin.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[2]\n"
                   "SRC = ROOT / \"src/y.py\"\n"
                   "class Mixin:\n"
                   "    def payload(self):\n"
                   "        return SRC.read_text()\n")
        self.write("tests/test_x.py",
                   "from tests.pkg.mixin import Mixin\n"
                   "class TestX(Mixin):\n"
                   "    def test_x(self):\n"
                   "        assert self\n")
        self.track()
        out = self.affected("--paths", "src/y.py")
        self.assertIn("tests.test_x", out)
        self.assertNotIn("No affected mapping", out)
