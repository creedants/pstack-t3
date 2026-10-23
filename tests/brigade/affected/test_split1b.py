from ..support import setUpModule
from tests.brigade.test_affected import AffectedBase


class AffectedSplit1bTest(AffectedBase):
    def test_brigade_source_and_twin_pick_briefs_without_times_fails_when_helper_union_not_narrowed(self):
        root = str(self.archive_root())
        for rel in ("t3/added/brigade/scripts/brigade.py", "skills/brigade/scripts/brigade.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.brigade.test_briefs", out)
            self.assertNotIn("tests.landing.test_times", out)
            self.assertNotIn("No affected mapping", out)
