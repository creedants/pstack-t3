import tempfile
import unittest
from pathlib import Path

from tests.check_tests.support import check, setUpModule


class HomePathTest(unittest.TestCase):
    def _directory(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def test_a_home_directory_path_is_reported_at_its_line_fails_when_the_scan_skips_files(self):
        root = self._directory()
        (root / "notes").mkdir()
        (root / "notes" / "one.md").write_text("first line\nsee /home/someone/notes.md\n")
        self.assertEqual(check.home_path_findings(root), [
            "notes/one.md:2: holds a home directory path",
        ])

    def test_a_vendor_path_is_not_scanned_fails_when_vendor_is_walked(self):
        root = self._directory()
        (root / "vendor" / "p").mkdir(parents=True)
        (root / "vendor" / "p" / "notes.md").write_text("see /home/someone/notes.md\n")
        self.assertEqual(check.home_path_findings(root), [])

    def test_an_allow_listed_scrubber_test_is_not_scanned_fails_when_the_allow_list_is_ignored(self):
        root = self._directory()
        (root / "tests" / "activity_tests").mkdir(parents=True)
        (root / "tests" / "activity_tests" / "test_words.py").write_text("see /home/someone/notes.md\n")
        self.assertEqual(check.home_path_findings(root), [])

    def test_a_tree_without_a_home_directory_path_reports_nothing_fails_when_every_match_is_reported(self):
        root = self._directory()
        (root / "README.md").write_text("# Title\nno machine path here\n")
        self.assertEqual(check.home_path_findings(root), [])
