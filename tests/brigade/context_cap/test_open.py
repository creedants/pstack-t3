from ..support import json, os

class CapOpenMixin:
    def test_open_writes_the_default_cap_when_the_settings_file_is_missing(self):
        self.assertEqual(self.open(), f"opened {self.at}")
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 300000\n}\n')
        self.assertNotIn("contextCap", self.meta())

    def test_open_refuses_a_stored_context_cap_that_is_not_a_whole_number_and_names_it(self):
        self.open()
        settings_before = self.settings().read_bytes()
        for stored, shown in (("400000", "'400000'"), (True, "True")):
            with self.subTest(stored=stored):
                meta = self.meta()
                meta["contextCap"] = stored
                (self.at / "restaurant.json").write_text(json.dumps(meta))
                store_before = self.store_files()
                error = self.open(code=1)
                self.assertIn(f"brigade: warning: restaurant.json contextCap {shown} is invalid: "
                              "it is not a whole number", error.splitlines())
                self.assertEqual(self.settings().read_bytes(), settings_before)
                self.assertEqual(self.store_files(), store_before)

    def test_open_takes_the_default_cap_when_context_cap_is_missing(self):
        self.open()
        self.brigade("set", "--context-cap", "90000")
        self.brigade("set", "--context-cap", "")
        self.assertNotIn("contextCap", self.meta())
        self.settings().unlink()
        self.assertEqual(self.open(), f"exists {self.at}\n{self.missing_drift(300000)}")
        self.assertFalse(self.settings().exists())

    def test_open_refuses_a_stored_coordinator_provider_that_is_not_known_and_names_it(self):
        self.open()
        meta = self.meta()
        meta["coordinatorProvider"] = "vm"
        (self.at / "restaurant.json").write_text(json.dumps(meta))
        settings_before, store_before = self.settings().read_bytes(), self.store_files()
        error = self.open(code=1)
        self.assertIn("brigade: warning: restaurant.json coordinatorProvider 'vm' is invalid: "
                      "it is not claude or other", error.splitlines())
        self.assertEqual(self.settings().read_bytes(), settings_before)
        self.assertEqual(self.store_files(), store_before)

    def test_open_takes_the_default_provider_when_coordinator_provider_is_missing(self):
        self.open()
        self.brigade("set", "--coordinator-provider", "other")
        self.brigade("set", "--coordinator-provider", "")
        self.assertNotIn("coordinatorProvider", self.meta())
        self.settings().unlink()
        self.assertEqual(self.open(), f"exists {self.at}\n{self.missing_drift(300000)}")
        self.assertFalse(self.settings().exists())

    def test_open_stores_the_passed_cap_and_provider_and_syncs_them(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                     "--context-cap", "200000", "--coordinator-provider", "claude")
        self.assertEqual(self.meta()["contextCap"], 200000)
        self.assertEqual(self.meta()["coordinatorProvider"], "claude")
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 200000\n}\n')

    def test_reopen_reports_a_missing_settings_file_and_creates_none(self):
        self.open()
        self.brigade("set", "--context-cap", "90000")
        self.assertEqual(
            self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                         "--context-cap", "80000"),
            f"exists {self.at}\ncontext-cap stays 90000; change it with set --context-cap")
        self.assertEqual(self.meta()["contextCap"], 90000)
        self.settings().unlink()
        self.assertEqual(self.open(), f"exists {self.at}\n{self.missing_drift(90000)}")
        self.assertFalse(self.settings().exists())

    def test_reopen_reports_an_ignored_context_cap_and_keeps_the_store(self):
        self.open()
        self.brigade("set", "--context-cap", "90000")
        before = self.store_files()
        self.assertEqual(
            self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                         "--context-cap", "80000"),
            f"exists {self.at}\ncontext-cap stays 90000; change it with set --context-cap")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.meta()["contextCap"], 90000)

    def test_reopen_reports_an_ignored_coordinator_provider_and_keeps_the_store(self):
        self.open()
        self.brigade("set", "--coordinator-provider", "other")
        before = self.store_files()
        self.assertEqual(
            self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                         "--coordinator-provider", "claude"),
            f"exists {self.at}\ncoordinator-provider stays other; change it with set --coordinator-provider")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.meta()["coordinatorProvider"], "other")

    def test_reopen_reports_both_ignored_flags_and_keeps_the_store(self):
        self.open()
        self.brigade("set", "--context-cap", "90000")
        self.brigade("set", "--coordinator-provider", "other")
        before = self.store_files()
        self.assertEqual(
            self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                         "--context-cap", "80000", "--coordinator-provider", "claude"),
            f"exists {self.at}\n"
            "context-cap stays 90000; change it with set --context-cap\n"
            "coordinator-provider stays other; change it with set --coordinator-provider")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.meta()["contextCap"], 90000)
        self.assertEqual(self.meta()["coordinatorProvider"], "other")

    def test_a_refused_open_of_a_new_store_leaves_no_store_and_claims_no_intake(self):
        self.write_settings('{"autoCompactWindow":')
        refusal = f"brigade: {self.settings()} holds invalid JSON; fix or remove it"
        self.assertEqual(self.open("--intake", "github", code=1), refusal)
        self.assertFalse(self.at.exists())
        self.assertEqual(list((self.store / "bridge-kit").iterdir()), [])
        # No coordinator claimed github, so the next open reaches the same refusal instead of an intake dispute.
        self.assertEqual(self.run_at(self.store / "bridge-kit" / "replacement",
                                     "open", "--project-root", str(self.project), "--name", "Replacement",
                                     "--intake", "github", code=1), refusal)
        self.assertFalse((self.store / "bridge-kit" / "replacement").exists())

    def test_reopen_reports_a_settings_file_that_holds_no_object_and_writes_nothing(self):
        self.open()
        before = self.store_files()
        self.write_settings('[1, {"keep": true}]\n')
        self.assertEqual(self.open(), f"exists {self.at}\n{self.missing_drift(300000)}")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.settings().read_text(), '[1, {"keep": true}]\n')

    def test_open_creation_adopts_an_existing_cap_and_writes_no_settings_byte(self):
        self.write_settings('{\n  "other": true,\n  "autoCompactWindow": 500000\n}\n')
        before = self.settings().read_bytes()
        self.assertEqual(self.open(), f"opened {self.at}")
        self.assertEqual(self.meta()["contextCap"], 500000)
        self.assertEqual(self.settings().read_bytes(), before)

    def test_open_creation_writes_the_default_over_a_cap_that_is_not_an_integer(self):
        self.write_settings('{\n  "autoCompactWindow": "500000"\n}\n')
        self.assertEqual(self.open(), f"opened {self.at}")
        self.assertNotIn("contextCap", self.meta())
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 300000\n}\n')

    def test_reopen_reports_a_hand_edit_to_the_adopted_cap_and_keeps_both_values(self):
        self.write_settings('{\n  "autoCompactWindow": 500000\n}\n')
        self.open()
        self.write_settings('{\n  "autoCompactWindow": 600000\n}\n')
        store = self.store_files()
        self.assertEqual(self.open(), f"exists {self.at}\n{self.drift(600000, 500000)}")
        self.assertEqual(self.store_files(), store)
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 600000\n}\n')

    def test_reopen_writes_no_byte_when_the_settings_file_already_matches(self):
        self.open()
        self.write_settings('{"autoCompactWindow":300000}')
        before = self.settings().read_bytes()
        self.assertEqual(self.open(), f"exists {self.at}")
        self.assertEqual(self.settings().read_bytes(), before)

    def test_a_failed_settings_write_on_open_leaves_no_store_and_claims_no_intake(self):
        if os.geteuid() == 0:
            self.skipTest("root writes through file permissions")
        self.write_settings('{"keep":true}')
        self.settings().parent.chmod(0o555)
        before = self.settings().read_bytes()
        try:
            self.assertEqual(self.open("--intake", "github", code=1),
                             f"brigade: cannot write {self.settings()}: Permission denied")
            self.assertEqual(self.settings().read_bytes(), before)
            self.assertFalse(self.at.exists())
            self.assertEqual(list((self.store / "bridge-kit").iterdir()), [])
        finally:
            self.settings().parent.chmod(0o755)

