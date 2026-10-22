from ..support import json, os, subprocess

class CapOpenMixin:
    def test_open_writes_nothing_into_the_project_root_without_a_cap_or_provider_flag(self):
        self.git_project()
        self.assertEqual(self.open(), f"opened {self.at}")
        status = subprocess.run(["git", "-C", str(self.project), "status", "--porcelain"],
                                capture_output=True, text=True, check=True)
        self.assertEqual(status.stdout, "")
        self.assertFalse(self.settings().exists())
        self.assertNotIn("contextCap", self.meta())

    def test_open_with_a_context_cap_writes_the_settings_file(self):
        self.assertEqual(self.open("--context-cap", "300000"), f"opened {self.at}")
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 300000\n}\n')
        self.assertEqual(self.meta()["contextCap"], 300000)

    def test_open_with_an_explicit_claude_provider_writes_the_default_cap(self):
        self.assertEqual(self.open("--coordinator-provider", "claude"), f"opened {self.at}")
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 300000\n}\n')
        self.assertNotIn("contextCap", self.meta())

    def test_open_refuses_a_stored_context_cap_that_is_not_a_whole_number_and_names_it(self):
        self.open_cap()
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
        self.assertEqual(self.open(), f"exists {self.at}")
        self.assertFalse(self.settings().exists())

    def test_open_refuses_a_stored_coordinator_provider_that_is_not_known_and_names_it(self):
        self.open_cap()
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
        self.assertEqual(self.open(), f"exists {self.at}")
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
        self.assertEqual(self.open_cap("--intake", "github", code=1), refusal)
        self.assertFalse(self.at.exists())
        self.assertEqual(list((self.store / "bridge-kit").iterdir()), [])
        # No coordinator claimed github, so the next open reaches the same refusal instead of an intake dispute.
        self.assertEqual(self.run_at(self.store / "bridge-kit" / "replacement",
                                     "open", "--project-root", str(self.project), "--name", "Replacement",
                                     "--context-cap", "300000", "--intake", "github", code=1), refusal)
        self.assertFalse((self.store / "bridge-kit" / "replacement").exists())

    def test_reopen_reports_a_settings_file_that_holds_no_object_and_writes_nothing(self):
        self.open_cap()
        before = self.store_files()
        self.write_settings('[1, {"keep": true}]\n')
        self.assertEqual(self.open(), f"exists {self.at}\n{self.missing_drift(300000)}")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.settings().read_text(), '[1, {"keep": true}]\n')

    def test_open_creation_leaves_an_existing_settings_file_alone(self):
        self.write_settings('{\n  "other": true,\n  "autoCompactWindow": 500000\n}\n')
        before = self.settings().read_bytes()
        self.assertEqual(self.open(), f"opened {self.at}")
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertNotIn("contextCap", self.meta())

    def test_open_creation_leaves_a_non_integer_cap_alone(self):
        self.write_settings('{\n  "autoCompactWindow": "500000"\n}\n')
        before = self.settings().read_bytes()
        self.assertEqual(self.open(), f"opened {self.at}")
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertNotIn("contextCap", self.meta())

    def test_reopen_reports_a_hand_edit_to_a_recorded_cap_and_keeps_both_values(self):
        self.write_settings('{\n  "autoCompactWindow": 500000\n}\n')
        self.open("--context-cap", "500000")
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
            self.assertEqual(self.open_cap("--intake", "github", code=1),
                             f"brigade: cannot write {self.settings()}: Permission denied")
            self.assertEqual(self.settings().read_bytes(), before)
            self.assertFalse(self.at.exists())
            self.assertEqual(list((self.store / "bridge-kit").iterdir()), [])
        finally:
            self.settings().parent.chmod(0o755)

