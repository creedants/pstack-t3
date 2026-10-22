from ..support import BrigadeTest, setUpModule


class HouseRulesMissingTest(BrigadeTest):
    def test_brief_refuses_a_missing_house_rules_file_and_writes_no_brief(self):
        self.coordinator()
        (self.at / "house-rules.md").unlink()
        before = self.store_files()
        code, out, err = self.run_raw(*self.BRIEF)
        self.assertEqual(code, 1)
        self.assertEqual(err.strip().splitlines(),
                         [f"brigade: {self.at}/house-rules.md is missing; no brief was written"])
        self.assertEqual(out, "")
        self.assertFalse((self.at / "briefs/D1.md").exists())
        self.assertFalse((self.at / "briefs/D1.contract.md").exists())
        self.assertEqual(self.store_files(), before)
