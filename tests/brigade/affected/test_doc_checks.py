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


class AffectedDocChecksTest(AffectedBase):
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

    def test_a_doc_read_through_a_helper_selects_its_test(self):
        self.git_project()
        self.write_doc_fixture()
        out = self.affected("--paths", "t3/added/brigade/notes.md")
        self.assertIn("tests.brigade.test_docs", out)
        self.assertNotIn("No affected mapping", out)

    def test_a_doc_no_test_reads_selects_none_of_those_tests(self):
        self.git_project()
        self.write_doc_fixture()
        out = self.affected("--paths", "t3/added/unread.md")
        self.assertIn("No affected mapping", out)
        self.assertNotIn("tests.brigade.test_docs", out)
