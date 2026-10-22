from ..support import BrigadeTest, setUpModule, json


class HouseRulesCapTest(BrigadeTest):
    def write_house_rules(self, text):
        (self.at / "house-rules.md").write_text(text)


    def set_cap(self, value):
        path = self.at / "restaurant.json"
        meta = json.loads(path.read_text())
        meta["houseRulesCap"] = value
        path.write_text(json.dumps(meta))


    def test_brief_refuses_a_reviewer_over_the_cap_and_writes_nothing(self):
        self.coordinator()
        self.write_house_rules("[worker] Worker rule.\n[reviewer] " + "R" * 40 + "\n")
        self.set_cap(20)
        before = self.store_files()
        code, out, err = self.run_raw(*self.BRIEF)
        self.assertEqual(code, 1)
        self.assertIn("house rules for reviewer are 40 bytes, over the houseRulesCap of 20 bytes", err)
        self.assertEqual(out, "")
        self.assertFalse((self.at / "briefs/D1.md").exists())
        self.assertFalse((self.at / "briefs/D1.contract.md").exists())
        self.assertEqual(self.store_files(), before)


    def test_next_prints_one_stderr_line_per_over_cap_role_and_keeps_its_sheet(self):
        self.coordinator()
        self.write_house_rules("[worker] Worker rule.\n[reviewer] " + "R" * 40 + "\n")
        sheet = self.brigade("next", "morning")
        self.set_cap(20)
        code, out, err = self.run_raw("next", "morning")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), sheet)
        self.assertNotIn("house rules", out)
        self.assertEqual([line for line in err.splitlines() if "house rules" in line],
                         ["brigade: warning: house rules for reviewer are 40 bytes, "
                          "over the houseRulesCap of 20 bytes"])


    def test_brief_without_the_cap_key_writes_a_brief_and_keeps_only_the_worker_rule(self):
        self.coordinator()
        self.write_house_rules("[worker] Worker rule.\n[reviewer] " + "R" * 40 + "\n")
        self.brigade(*self.BRIEF)
        brief = (self.at / "briefs/D1.md").read_bytes()
        self.assertEqual(brief.split(b"STANDING ORDERS:\n", 1)[1], b"Worker rule.\n")


    def test_brief_and_next_name_every_over_cap_role_in_role_order(self):
        self.coordinator()
        self.write_house_rules("[worker] " + "W" * 40 + "\n[reviewer] short\n[coordinator] " + "C" * 40 + "\n")
        self.set_cap(20)
        worker = "house rules for worker are 40 bytes, over the houseRulesCap of 20 bytes"
        coordinator = "house rules for coordinator are 40 bytes, over the houseRulesCap of 20 bytes"
        code, _, err = self.run_raw(*self.BRIEF)
        self.assertEqual(code, 1)
        self.assertLess(err.index(worker), err.index(coordinator))
        self.assertNotIn("house rules for reviewer", err)
        code, _, err = self.run_raw("next", "morning")
        self.assertEqual(code, 0)
        self.assertLess(err.index(worker), err.index(coordinator))
        self.assertNotIn("house rules for reviewer", err)


    def test_a_bad_houseRulesCap_is_refused_and_writes_nothing(self):
        self.coordinator()
        self.write_house_rules("[worker] Worker rule.\n")
        for value in (0, -1, "10", True, None):
            with self.subTest(value=value):
                self.set_cap(value)
                before = self.store_files()
                code, _, err = self.run_raw(*self.BRIEF)
                self.assertEqual(code, 1)
                self.assertIn("restaurant.json houseRulesCap ", err)
                self.assertIn("is invalid: it is not a whole number of 1 or more", err)
                self.assertEqual(self.store_files(), before)


    def test_a_tagged_house_rules_file_renders_only_untagged_and_worker_rules_in_the_brief(self):
        self.coordinator()
        self.write_house_rules("# House rules: Perf\n\n"
                               "[worker] Worker rule one.\n"
                               "  worker continuation\n\n"
                               "[reviewer] Reviewer only.\n"
                               "[worker] Second worker.\n"
                               "[coordinator] Coordinator only.\n"
                               "Untagged last rule.\n")
        self.brigade(*self.BRIEF)
        brief = (self.at / "briefs/D1.md").read_bytes()
        self.assertEqual(brief.split(b"STANDING ORDERS:\n", 1)[1],
                         b"# House rules: Perf\n\nWorker rule one.\n  worker continuation\n\n"
                         b"Second worker.\nUntagged last rule.\n")
