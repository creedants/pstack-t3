if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import json
import tempfile
import unittest
from pathlib import Path
from .support import (
    setUpModule,
    check,
    ROOT,
)


PIN = "1" * 40
OTHER = "2" * 40


class TstackPinTest(unittest.TestCase):
    def checkout(self, commit):
        """A checkout whose tstack.json pins PIN and whose one source page links tstack at commit."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "tstack.json").write_text(json.dumps({"commit": PIN}))
        page = root / "t3/added/demo/SKILL.md"
        page.parent.mkdir(parents=True)
        page.write_text(f"Read https://github.com/t3dotgg/tstack/blob/{commit}/skills/full-send/SKILL.md first.\n")
        return root

    def test_a_tstack_link_at_the_pinned_commit_passes(self):
        self.assertEqual(check.tstack_link_findings(self.checkout(PIN)), [])

    def test_a_tstack_link_at_another_commit_than_the_pin_is_reported(self):
        self.assertEqual(check.tstack_link_findings(self.checkout(OTHER)),
                         [f"t3/added/demo/SKILL.md:1: tstack link names {OTHER}, but tstack.json pins {PIN}"])

    def test_the_repository_links_tstack_only_at_its_pin(self):
        """The defect: a link left at an old commit after a bump. CI runs the build, which does not run this check."""
        self.assertEqual(check.tstack_link_findings(ROOT), [])
