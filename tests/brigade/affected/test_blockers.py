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


class AffectedBlockerTest(AffectedBase):
    def test_both_modules_selected_fails_when_helper_outside_tree_loses_mapping(self):
        self.git_project()
        self.write("src/x.py", "x = 1\n")
        self.write("util/helper.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "SRC = ROOT / \"src/x.py\"\n"
                   "def payload():\n    return SRC.read_text()\n")
        self.write("tests/test_a.py",
                   "from util.helper import payload\n"
                   "def test_a():\n    assert payload().strip() == \"x=1\"\n")
        self.write("tests/test_b.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "def test_b():\n    assert (ROOT / \"src/x.py\").read_text().strip() != \"\"\n")
        self.track()
        self.restaurant(self.project, layout={"always": "tests.test_b", "entryPoints": ["src/x.py"],
                                              "helpers": ["util/helper.py"], "importRoots": []})
        out = self.affected("--paths", "src/x.py")
        self.assertIn("tests.test_a", out)
        self.assertIn("tests.test_b", out)
        self.assertNotIn("full suite", out)

    def test_package_importer_selected_fails_when_support_multi_resolve_drops_edge(self):
        self.git_project()
        self.write("src/x.py", "x = 1\n")
        self.write("tests/__init__.py", "")
        self.write("tests/test_impl.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\n"
                   "SRC = ROOT / \"src/x.py\"\n"
                   "def payload():\n    return SRC.read_text()\n"
                   "def test_impl():\n    assert payload().strip() != \"\"\n")
        self.write("tests/support.py",
                   "from tests import test_impl\n"
                   "def payload():\n    return test_impl.payload()\n")
        self.write("tests/test_a.py",
                   "from tests.support import payload\n"
                   "def test_a():\n    assert payload().strip() == \"x=1\"\n")
        self.track()
        self.restaurant(self.project, layout={"always": "tests.test_pstack_t3", "entryPoints": ["src/x.py"],
                                              "helpers": [], "importRoots": []})
        out = self.affected("--paths", "src/x.py")
        self.assertIn("tests.test_a", out)
        self.assertNotIn("No affected mapping", out)

    def test_full_suite_selected_fails_when_runtime_sys_path_import_drops_edge(self):
        self.git_project()
        self.write("src/x.py", "x = 1\n")
        self.write("tests/runtime/test_impl.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[2]\n"
                   "SRC = ROOT / \"src/x.py\"\n"
                   "def payload():\n    return SRC.read_text()\n"
                   "def test_impl():\n    assert payload().strip() != \"\"\n")
        self.write("tests/support.py",
                   "import sys\nfrom pathlib import Path\n"
                   "sys.path.insert(0, str(Path(__file__).resolve().parent / \"runtime\"))\n"
                   "import test_impl\n"
                   "def payload():\n    return test_impl.payload()\n")
        self.write("tests/test_a.py",
                   "from tests.support import payload\n"
                   "def test_a():\n    assert payload().strip() == \"x=1\"\n")
        self.track()
        self.restaurant(self.project, layout={"always": "tests.test_pstack_t3", "entryPoints": ["src/x.py"],
                                              "helpers": [], "importRoots": []})
        out = self.affected("--paths", "src/x.py")
        self.assertIn("full suite", out)
