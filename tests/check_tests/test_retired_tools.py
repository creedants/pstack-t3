if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import tempfile
import unittest
from pathlib import Path
from .support import (
    setUpModule,
    check,
)


class RetiredToolsTest(unittest.TestCase):
    def _root(self, line):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        demo = root / "t3/added/pstack-runtime/demo.md"
        demo.parent.mkdir(parents=True)
        demo.write_text(line + "\n")
        (root / "skills").mkdir()
        (root / "docs").mkdir()
        return root

    def test_merged_names_pass_fails_when_a_merged_name_is_reported(self):
        root = self._root("Call `t3_queue`, `t3_pending_request`, `t3_thread_transfer`, `preview_act`, `t3_thread_configure`.")
        self.assertEqual(check.retired_tool_findings(root), [])

    def test_a_retired_name_is_reported_with_its_line_and_replacement_fails_when_it_is_missed(self):
        root = self._root("Call `t3_queue_list` on the recipient.")
        self.assertEqual(check.retired_tool_findings(root), [
            "t3/added/pstack-runtime/demo.md:1: retired T3 tool t3_queue_list; "
            "use t3_queue with action: Call `t3_queue_list` on the recipient.",
        ])

    def test_changelog_history_is_not_scanned_fails_when_a_changelog_line_is_reported(self):
        """The defect: a history note naming a retired tool fails the build. History stays as written."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        runtime = root / "t3/runtime.md"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("Call `t3_queue_list` on the recipient.\n")
        history = root / "docs/changelog/0.1.0.md"
        history.parent.mkdir(parents=True)
        history.write_text("Call `t3_queue_list` on the recipient.\n")
        (root / "skills").mkdir()
        self.assertEqual(check.retired_tool_findings(root), [
            "t3/runtime.md:1: retired T3 tool t3_queue_list; "
            "use t3_queue with action: Call `t3_queue_list` on the recipient.",
        ])
