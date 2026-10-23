from ..support import setUpModule
from tests.brigade.test_affected import AffectedBase


class AffectedSplit1eTest(AffectedBase):
    def test_governor_one_hop_maps_fails_when_helper_carried_entry_import_not_followed(self):
        root = str(self.archive_root())
        for rel in ("t3/added/landing/scripts/landing_lib/governor.py", "skills/landing/scripts/landing_lib/governor.py"):
            out = self.brigade("affected", "--root", root, "--paths", rel)
            self.assertIn("tests.landing.test_slot", out)
            self.assertIn("tests.brigade.test_briefs", out)
            self.assertNotIn("No affected mapping", out)
