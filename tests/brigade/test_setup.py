from .support import BrigadeTest, Path, json, setUpModule


class SetupBoundaryTest(BrigadeTest):
    """The open and set commands refuse a malformed stored setting before they write restaurant.json or the settings file."""

    def settings(self):
        return Path(self.project) / ".claude" / "settings.local.json"

    def meta(self):
        return json.loads((self.at / "restaurant.json").read_text())

    def write_meta(self, **changes):
        meta = self.meta()
        meta.update(changes)
        (self.at / "restaurant.json").write_text(json.dumps(meta))

    def reopen(self, *extra, **kwargs):
        return self.brigade("open", "--project-root", str(self.project), "--name", "Perf", *extra, **kwargs)

    def test_reopen_refuses_a_malformed_caps_and_writes_nothing(self):
        self.open()
        self.write_meta(caps="polish=1")
        self.settings().unlink()
        store = self.store_files()
        error = self.reopen(code=1)
        self.assertIn("brigade: warning: restaurant.json caps 'polish=1' is invalid: it is not an object", error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse(self.settings().exists())

    def test_reopen_refuses_a_malformed_review_entry_and_writes_nothing(self):
        self.open()
        self.write_meta(review={"fix": "fullish"})
        self.settings().unlink()
        store = self.store_files()
        error = self.reopen(code=1)
        self.assertIn("brigade: warning: restaurant.json review 'fullish' is invalid: 'fix' needs full or light", error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse(self.settings().exists())

    def test_reopen_refuses_a_malformed_context_cap_on_the_other_provider_path(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                     "--coordinator-provider", "other")
        self.write_meta(contextCap="400000")
        store = self.store_files()
        error = self.reopen(code=1)
        self.assertIn("brigade: warning: restaurant.json contextCap '400000' is invalid: it is not a whole number",
                      error)
        self.assertEqual(self.store_files(), store)
        self.assertFalse(self.settings().exists())

    def test_set_refuses_a_malformed_context_cap_while_changing_the_provider(self):
        self.open()
        self.write_meta(contextCap="400000")
        store, settings = self.store_files(), self.settings().read_bytes()
        error = self.brigade("set", "--coordinator-provider", "other", code=1)
        self.assertIn("brigade: warning: restaurant.json contextCap '400000' is invalid: it is not a whole number",
                      error)
        self.assertEqual(self.store_files(), store)
        self.assertEqual(self.settings().read_bytes(), settings)

    def test_set_refuses_a_malformed_coordinator_provider_while_changing_the_reporting(self):
        self.open()
        self.write_meta(coordinatorProvider="vm")
        store, settings = self.store_files(), self.settings().read_bytes()
        error = self.brigade("set", "--reporting", "digest", code=1)
        self.assertIn("brigade: warning: restaurant.json coordinatorProvider 'vm' is invalid: it is not claude or other",
                      error)
        self.assertEqual(self.store_files(), store)
        self.assertEqual(self.settings().read_bytes(), settings)

    def test_set_repairs_or_clears_the_malformed_key_it_names(self):
        self.open()
        self.write_meta(contextCap="400000")
        self.brigade("set", "--context-cap", "")
        self.assertNotIn("contextCap", self.meta())
        self.write_meta(coordinatorProvider="vm")
        self.brigade("set", "--coordinator-provider", "claude")
        self.assertEqual(self.meta()["coordinatorProvider"], "claude")
        self.write_meta(review={"fix": "fullish"})
        self.brigade("set", "--review", "fix=light")
        self.assertEqual(self.meta()["review"], {"fix": "light"})

    def test_set_still_refuses_a_malformed_entry_the_command_does_not_name(self):
        self.open()
        self.write_meta(caps={"fix": "1"})
        store = self.store_files()
        error = self.brigade("set", "--cap", "polish=1", code=1)
        self.assertIn("brigade: warning: restaurant.json caps '1' is invalid: 'fix' needs a whole number of 1 or more",
                      error)
        self.assertEqual(self.store_files(), store)
