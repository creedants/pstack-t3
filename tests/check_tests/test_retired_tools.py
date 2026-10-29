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

MERGED = "Call `t3_queue`, `t3_pending_request`, `t3_thread_transfer`, `preview_act`, `t3_thread_configure`."
RETIRED = "Call `t3_queue_list` on the recipient."
RETIRED_LINE = "retired T3 tool t3_queue_list; use t3_queue with action: " + RETIRED


class RetiredToolsTest(unittest.TestCase):
    def _root(self, line):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        demo = root / "t3/added/pstack-runtime/demo.md"
        demo.parent.mkdir(parents=True)
        demo.write_text(line + "\n")
        return root

    def test_merged_names_alone_are_quiet_fails_when_a_merged_name_matches_a_retired_one(self):
        self.assertEqual(check.retired_tool_findings(self._root(MERGED)), [])

    def test_a_retired_name_is_named_with_its_line_and_replacement_fails_when_it_is_missed(self):
        self.assertEqual(check.retired_tool_findings(self._root(RETIRED)), [
            f"t3/added/pstack-runtime/demo.md:1: {RETIRED_LINE}",
        ])

    def test_the_checkout_scan_reads_t3_only_fails_when_history_generated_output_or_stale_skills_is_read(self):
        """The defect: a history note, the generated CLI page, or a stale generated skills tree fails the build."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        tree = root / "rendered"
        tree.mkdir()
        for relative in ("t3/runtime.md", "docs/changelog/0.1.0.md", "docs/cli/roles.md", "skills/stale/SKILL.md"):
            document = root / relative
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_text(RETIRED + "\n")
        self.assertEqual(check.retired_tool_findings(tree, root), [f"t3/runtime.md:1: {RETIRED_LINE}"])
