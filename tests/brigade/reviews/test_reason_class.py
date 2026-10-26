if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import os
import shutil
import sys
import unittest
from pathlib import Path

from ..support import setUpModule
from ..support import (
    BrigadeTest,
    CLAUDE,
    CODEX,
)

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "t3/added/brigade/scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "t3/added/brigade/scripts"))
from brigade_lib._reviews.reason_class import report_class
from brigade_lib._reviews.rounds import report_says


class Store:
    """The one attribute report_class reads from a store: its directory."""

    def __init__(self, directory):
        self.dir = directory


class ReportClassTest(BrigadeTest):
    def report_store(self):
        return Store(self.at)

    def pass_rows(self):
        """pass.tsv's rows as dicts, padded to the header's length."""
        header, *lines = (self.at / "pass.tsv").read_text().splitlines()
        keys = header.split("\t")
        return [dict(zip(keys, line.split("\t") + [""] * len(keys))) for line in lines]

    def test_a_report_with_a_reason_line_states_its_class(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Findings.\n\nReason: scope\n")
        self.assertEqual(report_class(self.report_store(), "D1-review-1.md"), "scope")

    def test_a_report_with_no_reason_line_states_nothing(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Findings.\n")
        self.assertIsNone(report_class(self.report_store(), "D1-review-1.md"))

    def test_a_report_with_an_unknown_class_states_nothing(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Findings.\n\nReason: mystery\n")
        self.assertIsNone(report_class(self.report_store(), "D1-review-1.md"))

    def test_an_empty_report_cell_states_nothing(self):
        self.fired_bug_fix()
        self.assertIsNone(report_class(self.report_store(), ""))

    def test_a_reports_prefixed_cell_reads_the_file(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Findings.\n\nReason: other\n")
        self.assertEqual(report_class(self.report_store(), "reports/D1-review-1.md"), "other")

    def test_a_cell_that_names_another_directory_states_nothing(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Findings.\n\nReason: scope\n")
        report_store = self.report_store()
        self.assertIsNone(report_class(report_store, "sub/D1-review-1.md"))
        self.assertIsNone(report_class(report_store, "../D1-review-1.md"))
        self.assertIsNone(report_class(report_store, "."))

    def test_a_missing_report_states_nothing(self):
        self.fired_bug_fix()
        self.assertIsNone(report_class(self.report_store(), "D1-review-9.md"))

    def test_a_symlink_report_states_nothing(self):
        self.fired_bug_fix()
        outside = self.root / "outside.md"
        outside.write_text("Reason: scope\n")
        reports = self.at / "reports"
        reports.mkdir(exist_ok=True)
        (reports / "D1-review-1.md").symlink_to(outside)
        self.assertIsNone(report_class(self.report_store(), "D1-review-1.md"))

    def test_a_directory_report_states_nothing(self):
        self.fired_bug_fix()
        reports = self.at / "reports"
        reports.mkdir(exist_ok=True)
        (reports / "D1-review-1.md").mkdir(exist_ok=True)
        self.assertIsNone(report_class(self.report_store(), "D1-review-1.md"))

    @unittest.skipIf(os.geteuid() == 0, "root searches a directory whatever its mode")
    def test_an_unsearchable_reports_directory_states_nothing(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Findings.\n\nReason: scope\n")
        reports = self.at / "reports"
        reports.chmod(0o000)
        self.addCleanup(reports.chmod, 0o755)
        self.assertIsNone(report_class(self.report_store(), "D1-review-1.md"))

    def test_a_cell_the_file_system_refuses_states_nothing(self):
        self.fired_bug_fix()
        self.assertIsNone(report_class(self.report_store(), "D1\x00.md"))

    def test_a_report_outside_the_store_states_nothing(self):
        self.fired_bug_fix()
        outside = self.root / "outside-reports"
        outside.mkdir(exist_ok=True)
        (outside / "D1-review-1.md").write_text("Reason: scope\n")
        reports = self.at / "reports"
        if reports.is_symlink() or reports.is_file():
            reports.unlink()
        elif reports.is_dir():
            shutil.rmtree(reports)
        reports.symlink_to(outside, target_is_directory=True)
        self.assertIsNone(report_class(self.report_store(), "D1-review-1.md"))

    def test_two_reason_lines_state_the_first_class(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Reason: scope\n\nReason: other\n")
        self.assertEqual(report_class(self.report_store(), "D1-review-1.md"), "scope")

    def test_a_report_that_is_not_utf8_states_no_class(self):
        self.fired_bug_fix()
        path = self.review_file("D1-review-1.md", "Reason: scope\n")
        path.write_bytes(b"Reason: scope\n\xff\xfe not text\n")
        self.assertIsNone(report_class(self.report_store(), "D1-review-1.md"))

    def test_a_quoted_reason_line_states_nothing(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Findings.\n\n> Reason: scope\n")
        self.assertIsNone(report_class(self.report_store(), "D1-review-1.md"))

    def test_a_fenced_example_does_not_replace_the_reviewers_classification(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md",
                         "A short repro paragraph.\n\n```\nReason: scope\n```\n\nReason: wrong-behavior\n")
        self.assertEqual(report_class(self.report_store(), "D1-review-1.md"), "wrong-behavior")

    def test_a_reason_line_only_inside_a_fence_states_nothing(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md",
                         "A short repro paragraph.\n\n```\nReason: scope\n```\n")
        self.assertIsNone(report_class(self.report_store(), "D1-review-1.md"))

    def test_a_pass_row_with_a_classed_report_reads_through_report_says(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Findings.\n\nReason: scope\n")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--reason", "other",
                     "--author", CLAUDE, "--verifier", CODEX, "--report", "D1-review-1.md")
        self.assertEqual(report_says([("D1", self.pass_rows())], self.report_store()),
                         "report says: scope 1; D1 recorded other, report says scope")
