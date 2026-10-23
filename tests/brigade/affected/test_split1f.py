from ..support import setUpModule
from tests.brigade.test_affected import AffectedBase


class AffectedSplit1fTest(AffectedBase):
    def test_activity_source_and_twin_pick_activity_and_render_fails_when_transitive_import_not_closed(self):
        root = str(self.archive_root())
        for rel in ("t3/added/brigade/scripts/activity.py", "skills/brigade/scripts/activity.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.test_activity", out)
            self.assertIn("tests.test_activity_render", out)
            self.assertNotIn("No affected mapping", out)
