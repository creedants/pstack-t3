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


class AffectedAncestorShadowTest(AffectedBase):
    def test_runnable_importer_shadowed_by_ancestor_falls_back_fails_when_ancestor_guess_clears_uncertainty(self):
        self.git_project()
        self.write("src/x.py", "x=1\n")
        self.write("tests/__init__.py", "")
        self.write("tests/group/__init__.py", "")
        self.write("tests/runtime/__init__.py", "")
        self.write("tests/test_impl.py",
                   "def payload():\n    return \"irrelevant\"\n")
        self.write("tests/runtime/test_impl.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[2]\n"
                   "SRC = ROOT / \"src/x.py\"\n"
                   "def payload():\n    return SRC.read_text()\n"
                   "def test_impl():\n    assert payload().strip() != \"\"\n")
        self.write("tests/group/test_a.py",
                   "import sys\nfrom pathlib import Path\n"
                   "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / \"runtime\"))\n"
                   "import test_impl\n"
                   "payload = test_impl.payload\n"
                   "def test_a():\n    assert payload().strip() == \"x=1\"\n")
        self.track()
        self.restaurant(self.project, layout={"always": "tests.runtime.test_impl", "entryPoints": ["src/x.py"],
                                              "helpers": [], "importRoots": []})
        out = self.affected("--paths", "src/x.py")
        self.assertIn("full suite", out)

    def test_support_importer_shadowed_by_ancestor_falls_back_fails_when_ancestor_guess_clears_uncertainty(self):
        self.git_project()
        self.write("src/x.py", "x=1\n")
        self.write("tests/__init__.py", "")
        self.write("tests/group/__init__.py", "")
        self.write("tests/runtime/__init__.py", "")
        self.write("tests/test_impl.py",
                   "def payload():\n    return \"irrelevant\"\n")
        self.write("tests/runtime/test_impl.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[2]\n"
                   "SRC = ROOT / \"src/x.py\"\n"
                   "def payload():\n    return SRC.read_text()\n"
                   "def test_impl():\n    assert payload().strip() != \"\"\n")
        self.write("tests/group/support.py",
                   "import sys\nfrom pathlib import Path\n"
                   "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / \"runtime\"))\n"
                   "import test_impl\n"
                   "def payload():\n    return test_impl.payload()\n")
        self.write("tests/group/test_a.py",
                   "from support import payload\n"
                   "def test_a():\n    assert payload().strip() == \"x=1\"\n")
        self.track()
        self.restaurant(self.project, layout={"always": "tests.runtime.test_impl", "entryPoints": ["src/x.py"],
                                              "helpers": [], "importRoots": []})
        out = self.affected("--paths", "src/x.py")
        self.assertIn("full suite", out)
