if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import ast
import importlib
import sys

from ..support import Path, ROOT, setUpModule
from tests.brigade.test_affected import AffectedBase


class AffectedDocChecksTest(AffectedBase):
    def brigade_module(self, name):
        """The module name from this repository's script directory, loaded fresh so another module's copy cannot answer."""
        scripts = str(ROOT / "t3/added/brigade/scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
            self.addCleanup(sys.path.remove, scripts)
        for one in [n for n in list(sys.modules) if n == "brigade_lib" or n.startswith("brigade_lib.")]:
            del sys.modules[one]
        module = importlib.import_module(name)
        self.assertEqual(Path(module.__file__).resolve(),
                         (Path(scripts) / (name.replace(".", "/") + ".py")).resolve())
        return module

    def write_doc_fixture(self):
        self.write("t3/added/brigade/notes.md", "# Notes\nThe picker names the doc test.\n")
        self.write("t3/added/unread.md", "# Unread\nNo test reads this.\n")
        self.write("tests/brigade/_support/names.py",
                   "from pathlib import Path\n\n"
                   "ROOT = Path(__file__).resolve().parents[3]\n"
                   "DOC_NAMES = (\"notes.md\",)\n\n\n"
                   "def docs():\n"
                   "    return \"\\n\".join((ROOT / \"t3/added/brigade\" / name).read_text() for name in DOC_NAMES)\n")
        self.write("tests/brigade/_support/doc_checks.py",
                   "from .names import docs\n\n\n"
                   "def check_notes():\n"
                   "    assert \"The picker names the doc test.\" in docs()\n")
        self.write("tests/brigade/test_docs.py",
                   "from ._support.doc_checks import check_notes\n\n\n"
                   "def test_the_doc_check_passes():\n"
                   "    check_notes()\n")
        self.track()

    def write_reader(self, module, expression, names):
        self.write(f"tests/readers/{module}.py",
                   "from pathlib import Path\n\n"
                   "ROOT = Path(__file__).resolve().parents[2]\n"
                   f"NAMES = {names!r}\n\n\n"
                   f"def test_{module}():\n"
                   f"    \"\\n\".join(({expression}).read_text() for name in NAMES)\n")

    def test_a_doc_read_through_a_helper_selects_its_test(self):
        self.git_project()
        self.write_doc_fixture()
        out = self.affected("--paths", "t3/added/brigade/notes.md")
        self.assertIn("tests.brigade.test_docs", out)
        self.assertNotIn("No affected mapping", out)

    def test_an_unread_doc_keeps_the_full_suite_fallback(self):
        self.git_project()
        self.write_doc_fixture()
        out = self.affected("--paths", "t3/added/unread.md")
        self.assertIn("No affected mapping", out)
        self.assertNotIn("tests.brigade.test_docs", out)

    def test_a_single_directory_read_selects_its_test(self):
        self.git_project()
        self.write("docs/one.md", "one\n")
        self.write_reader("test_single", "ROOT / \"docs\" / name", ("one.md",))
        self.track()
        out = self.affected("--paths", "docs/one.md")
        self.assertIn("tests.readers.test_single", out)
        self.assertNotIn("No affected mapping", out)

    def test_an_exact_entry_and_a_directory_entry_both_select_their_tests(self):
        self.git_project()
        self.write("docs/sub/a.md", "a\n")
        self.write("docs/sub/b.md", "b\n")
        self.write("tests/readers/test_exact.py",
                   "from pathlib import Path\n\n"
                   "ROOT = Path(__file__).resolve().parents[2]\n\n\n"
                   "def test_exact():\n    (ROOT / \"docs/sub/a.md\").read_text()\n")
        self.write_reader("test_parent", "ROOT / \"docs/sub\" / name", ("a.md", "b.md"))
        self.track()
        out = self.affected("--paths", "docs/sub/a.md")
        self.assertIn("tests.readers.test_exact", out)
        self.assertIn("tests.readers.test_parent", out)

    def test_two_directory_entries_both_select_their_tests(self):
        self.git_project()
        self.write("docs/sub/child.md", "c\n")
        self.write("docs/sub/deeper/b.md", "b\n")
        self.write_reader("test_outer", "ROOT / \"docs/sub\" / name", ("child.md",))
        self.write_reader("test_inner", "ROOT / \"docs/sub/deeper\" / name", ("b.md",))
        self.track()
        out = self.affected("--paths", "docs/sub/deeper/b.md")
        self.assertIn("tests.readers.test_outer", out)
        self.assertIn("tests.readers.test_inner", out)

    def test_a_directory_entry_union_does_not_depend_on_insertion_order(self):
        table_class = self.brigade_module("brigade_lib._affected.model")._PathTable
        for rows in (("docs/sub/*", "docs/sub/deeper/*"), ("docs/sub/deeper/*", "docs/sub/*")):
            table = table_class({rows[0]: ("tests.test_outer",), rows[1]: ("tests.test_inner",)})
            self.assertEqual(table.get("docs/sub/deeper/b.md"), ("tests.test_inner", "tests.test_outer"))

    def test_a_python_file_in_a_read_directory_keeps_the_full_suite_fallback(self):
        self.git_project()
        self.write("scripts/tools/tool.py", "VALUE = 1\n")
        self.write_reader("test_scripts", "ROOT / \"scripts/tools\" / name", ("tool.py",))
        self.track()
        out = self.affected("--paths", "scripts/tools/tool.py")
        self.assertIn("No affected mapping", out)
        self.assertNotIn("tests.readers.test_scripts", out)

    def test_a_directory_entry_does_not_shadow_a_mapped_generated_path(self):
        self.git_project()
        self.vendor_layout()
        self.write("t3/added/brigade/notes.md", "# Notes\n")
        self.write("t3/scripts/roles.py", "VALUE = 1\n")
        self.write("skills/pstack-runtime/scripts/roles.py", "VALUE = 1\n")
        self.write("tests/readers/test_roles.py",
                   "import runpy\nfrom pathlib import Path\n\n"
                   "ROOT = Path(__file__).resolve().parents[2]\n\n\n"
                   "def test_roles():\n    runpy.run_path(str(ROOT / \"t3/scripts/roles.py\"))\n")
        self.write_reader("test_added", "ROOT / \"t3/added\" / name", ("brigade/notes.md",))
        self.track()
        out = self.affected("--paths", "skills/pstack-runtime/scripts/roles.py")
        self.assertIn("tests.readers.test_roles", out)

    def test_a_single_directory_prefix_is_kept_and_a_later_unsafe_operand_is_rejected(self):
        prefix = self.brigade_module("brigade_lib._affected.scan")._read_prefix
        cases = {
            "ROOT / \"t3/added/brigade\" / name": "t3/added/brigade",
            "ROOT / name": "",
            "unknown / \"t3/added/brigade\" / name": "",
            "\"/etc\" / name": "",
            "ROOT / \"..\" / name": "",
            "ROOT / \"docs\" / name": "docs",
            "ROOT / \"t3/added/brigade\" / \"/etc/passwd\"": "",
            "ROOT / \"t3/added/brigade\" / \"..\" / name": "",
            "ROOT / \"t3/added/brigade\" / name / \"..\"": "",
        }
        for text, want in cases.items():
            with self.subTest(text):
                self.assertEqual(prefix(ast.parse(text, mode="eval").body, {}), want)
