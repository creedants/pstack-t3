from ..support import setUpModule
from tests.brigade.test_affected import AffectedBase


class AffectedSplit1dTest(AffectedBase):
    def test_briefs_lib_one_hop_maps_fails_when_helper_carried_entry_import_not_followed(self):
        root = str(self.archive_root())
        for rel in ("t3/added/brigade/scripts/brigade_lib/briefs.py", "skills/brigade/scripts/brigade_lib/briefs.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.brigade.test_briefs", out)
            self.assertNotIn("tests.landing.test_times", out)
            self.assertNotIn("No affected mapping", out)
