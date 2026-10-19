from ..support import json, subprocess

class CapSetMixin:
    def test_set_stores_the_cap_and_prints_the_full_new_meta(self):
        self.open()
        out = self.brigade("set", "--context-cap", "120000")
        self.assertEqual(json.loads(out), self.meta())
        self.assertEqual(self.meta()["contextCap"], 120000)
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 120000\n}\n')

    def test_set_keeps_every_other_settings_key(self):
        self.open()
        self.settings().write_text('{\n  "other": true,\n  "autoCompactWindow": 300000\n}\n')
        self.brigade("set", "--context-cap", "80000")
        self.assertEqual(self.settings().read_text(),
                         '{\n  "other": true,\n  "autoCompactWindow": 80000\n}\n')

    def test_set_updates_the_cap_without_rewriting_the_numbers_around_it(self):
        self.open()
        self.settings().write_text('{\n  "overflow": 1e400,\n  "rounded": 1.0000000000000001,\n'
                                   '  "autoCompactWindow": 300000\n}\n')
        self.brigade("set", "--context-cap", "80000")
        self.assertEqual(self.settings().read_text(),
                         '{\n  "overflow": 1e400,\n  "rounded": 1.0000000000000001,\n'
                         '  "autoCompactWindow": 80000\n}\n')

    def test_set_inserts_the_cap_in_the_documents_own_spacing(self):
        self.open()
        self.write_settings('{"keep":true}')
        self.brigade("set", "--context-cap", "80000")
        self.assertEqual(self.settings().read_text(), '{"keep":true,"autoCompactWindow": 80000}')
        self.write_settings('{\n  "keep": true\n}\n')
        self.brigade("set", "--context-cap", "90000")
        self.assertEqual(self.settings().read_text(), '{\n  "keep": true,\n  "autoCompactWindow": 90000\n}\n')

    def test_set_accepts_the_floor_value_and_refuses_anything_below_it(self):
        self.open()
        self.brigade("set", "--context-cap", "50000")
        self.assertEqual(self.meta()["contextCap"], 50000)
        before = (self.at / "restaurant.json").read_bytes()
        error = self.brigade("set", "--context-cap", "49999", code=1)
        self.assertEqual(error, "brigade: --context-cap takes an integer of at least 50000, got '49999'")
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 50000\n}\n')

    def test_set_refuses_a_non_integer_cap_and_changes_nothing(self):
        self.open()
        before = (self.at / "restaurant.json").read_bytes()
        error = self.brigade("set", "--context-cap", "abc", code=1)
        self.assertEqual(error, "brigade: --context-cap takes an integer of at least 50000, got 'abc'")
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 300000\n}\n')

    def test_set_refuses_an_unknown_provider_and_changes_nothing(self):
        self.open()
        before = (self.at / "restaurant.json").read_bytes()
        error = self.brigade("set", "--coordinator-provider", "grok", code=1)
        self.assertEqual(error, "brigade: --coordinator-provider takes claude or other, got 'grok'")
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)
        self.assertNotIn("coordinatorProvider", self.meta())

    def test_set_records_and_clears_the_provider(self):
        self.open()
        out = self.brigade("set", "--coordinator-provider", "other")
        self.assertEqual(json.loads(out), self.meta())
        self.assertEqual(self.meta()["coordinatorProvider"], "other")
        out = self.brigade("set", "--coordinator-provider", "")
        self.assertEqual(json.loads(out), self.meta())
        self.assertNotIn("coordinatorProvider", self.meta())

    def test_clearing_the_cap_restores_the_default_in_meta_and_file(self):
        self.open()
        self.brigade("set", "--context-cap", "80000")
        out = self.brigade("set", "--context-cap", "")
        self.assertEqual(json.loads(out), self.meta())
        self.assertNotIn("contextCap", self.meta())
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 300000\n}\n')

    def test_set_refuses_a_tracked_settings_file_and_changes_nothing(self):
        self.git_project()
        self.open()
        before = self.settings().read_bytes()
        subprocess.run(["git", "-C", str(self.project), "add", ".claude/settings.local.json"], check=True,
                       capture_output=True)
        error = self.brigade("set", "--context-cap", "120000", code=1)
        self.assertEqual(
            error,
            f"brigade: {self.settings()} is tracked by git; "
            "untrack it before brigade writes autoCompactWindow")
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertNotIn("contextCap", self.meta())

    def test_set_refuses_invalid_json_and_changes_nothing(self):
        self.open()
        self.write_settings('{"autoCompactWindow":')
        before = self.settings().read_bytes()
        error = self.brigade("set", "--context-cap", "120000", code=1)
        self.assertEqual(error, f"brigade: {self.settings()} holds invalid JSON; fix or remove it")
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertNotIn("contextCap", self.meta())

    def test_set_refuses_a_settings_file_that_holds_no_json_object(self):
        self.open()
        self.write_settings('[1, {"keep": true}]\n')
        before, store = self.settings().read_bytes(), self.store_files()
        error = self.brigade("set", "--context-cap", "120000", code=1)
        self.assertEqual(error, f"brigade: {self.settings()} holds no JSON object; fix or remove it")
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertEqual(self.store_files(), store)
        self.assertNotIn("contextCap", self.meta())

    def test_open_refuses_a_settings_file_holding_a_non_json_constant(self):
        for text in ('{\n  "keep": NaN\n}\n', '{\n  "keep": Infinity\n}\n', '{\n  "keep": -Infinity\n}\n'):
            with self.subTest(text=text):
                self.write_settings(text)
                self.assertEqual(self.open(code=1),
                                 f"brigade: {self.settings()} holds invalid JSON; fix or remove it")
                self.assertEqual(self.settings().read_text(), text)
                self.assertFalse(self.at.exists())

    def test_set_refuses_a_settings_file_holding_a_non_json_constant(self):
        self.open()
        store = self.store_files()
        for text in ('{\n  "keep": NaN\n}\n', '{\n  "keep": Infinity\n}\n', '{\n  "keep": -Infinity\n}\n'):
            with self.subTest(text=text):
                self.write_settings(text)
                self.assertEqual(self.brigade("set", "--context-cap", "120000", code=1),
                                 f"brigade: {self.settings()} holds invalid JSON; fix or remove it")
                self.assertEqual(self.settings().read_text(), text)
                self.assertEqual(self.store_files(), store)
                self.assertNotIn("contextCap", self.meta())

