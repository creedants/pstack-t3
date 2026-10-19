from ..support import setUpModule
from tests.brigade.test_affected import AffectedBase


class AffectedAncestorSyspathCompeteTest(AffectedBase):
    def _base(self):
        self.git_project()
        self.write("src/x.py", "x=1\n")
        self.write("tests/__init__.py", "")
        self.write("tests/group/__init__.py", "")
        self.write("tests/runtime/__init__.py", "")
        self.write("tests/test_impl.py", "def payload():\n    return \"irrelevant\"\n")
        self.write("tests/runtime/test_impl.py",
                   "from pathlib import Path\nROOT = Path(__file__).resolve().parents[2]\n"
                   "SRC = ROOT / \"src/x.py\"\n"
                   "def payload():\n    return SRC.read_text()\n"
                   "def test_impl():\n    assert payload().strip() != \"\"\n")
        self.restaurant(self.project, layout={"always": "tests.runtime.test_impl", "entryPoints": ["src/x.py"], "helpers": [], "importRoots": []})

    def test_sys_path_inserts_in_test_do_not_clear_ancestor_only_import_falls_back(self):
        self._base()
        self.write("tests/group/test_a.py",
                   "import sys\nfrom pathlib import Path\n"
                   "ROOT = Path(__file__).resolve().parents[2]\n"
                   "sys.path.insert(0, str(ROOT / \"tests\"))\n"
                   "sys.path.insert(0, str(ROOT / \"tests/runtime\"))\n"
                   "import test_impl\n"
                   "payload = test_impl.payload\n"
                   "def test_a():\n    assert payload().strip() == \"x=1\"\n")
        self.track()
        out = self.affected("--paths", "src/x.py")
        self.assertIn("full suite", out)

    def test_sys_path_inserts_in_support_do_not_clear_ancestor_only_import_falls_back(self):
        self._base()
        self.write("tests/group/support.py",
                   "import sys\nfrom pathlib import Path\n"
                   "ROOT = Path(__file__).resolve().parents[2]\n"
                   "sys.path.insert(0, str(ROOT / \"tests\"))\n"
                   "sys.path.insert(0, str(ROOT / \"tests/runtime\"))\n"
                   "import test_impl\n"
                   "def payload():\n    return test_impl.payload()\n")
        self.write("tests/group/test_a.py",
                   "from tests.group.support import payload\n"
                   "def test_a():\n    assert payload().strip() == \"x=1\"\n")
        self.track()
        out = self.affected("--paths", "src/x.py")
        self.assertIn("full suite", out)

    def test_sys_path_append_in_support_does_not_clear_ancestor_only_import_falls_back(self):
        self._base()
        self.write("tests/group/support.py",
                   "import sys\nfrom pathlib import Path\n"
                   "ROOT = Path(__file__).resolve().parents[2]\n"
                   "sys.path.append(str(ROOT / \"tests\"))\n"
                   "import test_impl\n"
                   "def payload():\n    return test_impl.payload()\n")
        self.write("tests/group/test_a.py",
                   "import sys\nfrom pathlib import Path\n"
                   "ROOT = Path(__file__).resolve().parents[2]\n"
                   "sys.path.insert(0, str(ROOT / \"tests/runtime\"))\n"
                   "from tests.group.support import payload\n"
                   "def test_a():\n    assert payload().strip() == \"x=1\"\n")
        self.track()
        out = self.affected("--paths", "src/x.py")
        self.assertIn("full suite", out)
