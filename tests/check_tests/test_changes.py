if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from .support import (
    setUpModule,
    check,
    ROOT,
)


def change_tree_findings(files):
    with tempfile.TemporaryDirectory() as directory:
        changes = Path(directory) / "changes"
        changes.mkdir()
        for name, data in files.items():
            path = changes / name
            if isinstance(data, bytes):
                path.write_bytes(data)
            else:
                path.write_text(data)
        return check.change_findings(changes)


class ChangeFindingsTest(unittest.TestCase):
    def test_a_fragment_with_an_unknown_kind_names_the_file_and_line(self):
        self.assertEqual(
            change_tree_findings({"pstack-t3%2Fbad.md": "- kind bug-fix\n- did a thing\n"}),
            ["changes/pstack-t3%2Fbad.md line 1 names a kind that is not feature, fix, polish, upkeep, or study"],
        )

    def test_a_fragment_with_a_known_kind_and_bullets_passes(self):
        self.assertEqual(change_tree_findings({"pstack-t3%2Fgood.md": "- kind fix\n- did a thing\n"}), [])

    def test_a_fragment_without_a_kind_marker_passes(self):
        self.assertEqual(change_tree_findings({"pstack-t3%2Fplain.md": "- did a thing\n"}), [])

    def test_a_non_bullet_line_after_the_kind_marker_reports_the_real_file_line(self):
        self.assertEqual(
            change_tree_findings({"pstack-t3%2Fbad.md": "- kind fix\n- ok\nnot a bullet\n"}),
            ["changes/pstack-t3%2Fbad.md line 3 is not a bullet or a continuation line"],
        )

    def test_a_fragment_with_only_a_kind_marker_holds_no_bullet(self):
        self.assertEqual(
            change_tree_findings({"pstack-t3%2Fbad.md": "- kind fix\n"}),
            ["changes/pstack-t3%2Fbad.md holds no bullet"],
        )

    def test_a_non_markdown_entry_is_not_a_fragment(self):
        self.assertEqual(
            change_tree_findings({"notes.txt": "- kind fix\n- ok\n"}),
            ["changes/notes.txt is not a fragment"],
        )

    def test_a_symlink_entry_is_not_a_fragment(self):
        with tempfile.TemporaryDirectory() as directory:
            changes = Path(directory) / "changes"
            changes.mkdir()
            (changes / "target.md").write_text("- kind fix\n- ok\n")
            (changes / "pstack-t3%2Flink.md").symlink_to(changes / "target.md")
            self.assertEqual(check.change_findings(changes), ["changes/pstack-t3%2Flink.md is not a fragment"])

    def test_a_non_utf8_fragment_is_named(self):
        self.assertEqual(
            change_tree_findings({"pstack-t3%2Fbad.md": b"\xff\xfe\n"}),
            ["changes/pstack-t3%2Fbad.md is not UTF-8"],
        )

    def test_a_missing_changes_directory_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(check.change_findings(Path(directory) / "changes"), [])

    def test_the_real_changes_tree_passes(self):
        self.assertEqual(check.change_findings(ROOT / "changes"), [])
