from ..support import setUpModule
from tests.brigade.test_affected import AffectedBase


class AffectedSplit1cTest(AffectedBase):
    def test_landing_source_and_twin_pick_briefs_and_times_fails_when_landing_entry_not_carried(self):
        root = str(self.archive_root())
        for rel in ("t3/added/landing/scripts/land.py", "skills/landing/scripts/land.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.brigade.test_briefs", out)
            self.assertIn("tests.landing.test_times", out)
            self.assertNotIn("No affected mapping", out)
