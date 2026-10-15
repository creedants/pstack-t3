from .support import (
    setUpModule,
    BrigadeTest,
    Path,
    json,
    os,
    subprocess,
)


class ContextCapTest(BrigadeTest):
    def settings(self):
        return Path(self.project) / ".claude" / "settings.local.json"

    def meta(self):
        return json.loads((self.at / "restaurant.json").read_text())

    def test_open_writes_the_default_cap_when_the_settings_file_is_missing(self):
        self.assertEqual(self.open(), f"opened {self.at}")
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 300000\n}\n')
        self.assertNotIn("contextCap", self.meta())

    def test_open_stores_the_passed_cap_and_provider_and_syncs_them(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                     "--context-cap", "200000", "--coordinator-provider", "claude")
        self.assertEqual(self.meta()["contextCap"], 200000)
        self.assertEqual(self.meta()["coordinatorProvider"], "claude")
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 200000\n}\n')

    def test_reopen_keeps_the_stored_cap_and_repairs_the_settings_file(self):
        self.open()
        self.brigade("set", "--context-cap", "90000")
        self.assertEqual(
            self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                         "--context-cap", "80000"),
            f"exists {self.at}")
        self.assertEqual(self.meta()["contextCap"], 90000)
        self.settings().unlink()
        self.assertEqual(self.open(), f"exists {self.at}")
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 90000\n}\n')

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

    def test_set_accepts_the_floor_value_and_refuses_anything_below_it(self):
        self.open()
        self.brigade("set", "--context-cap", "50000")
        self.assertEqual(self.meta()["contextCap"], 50000)
        before = (self.at / "restaurant.json").read_bytes()
        error = self.brigade("set", "--context-cap", "49999", ok=False)
        self.assertEqual(error, "brigade: --context-cap takes an integer of at least 50000, got '49999'")
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 50000\n}\n')

    def test_set_refuses_a_non_integer_cap_and_changes_nothing(self):
        self.open()
        before = (self.at / "restaurant.json").read_bytes()
        error = self.brigade("set", "--context-cap", "abc", ok=False)
        self.assertEqual(error, "brigade: --context-cap takes an integer of at least 50000, got 'abc'")
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 300000\n}\n')

    def test_set_refuses_an_unknown_provider_and_changes_nothing(self):
        self.open()
        before = (self.at / "restaurant.json").read_bytes()
        error = self.brigade("set", "--coordinator-provider", "grok", ok=False)
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
        error = self.brigade("set", "--context-cap", "120000", ok=False)
        self.assertEqual(
            error,
            f"brigade: {self.settings()} is tracked by git; "
            "untrack it before brigade writes autoCompactWindow")
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertNotIn("contextCap", self.meta())

    def test_set_refuses_invalid_json_and_changes_nothing(self):
        self.open()
        self.settings().write_text('{"autoCompactWindow":')
        before = self.settings().read_bytes()
        error = self.brigade("set", "--context-cap", "120000", ok=False)
        self.assertEqual(error, f"brigade: {self.settings()} holds invalid JSON; fix or remove it")
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertNotIn("contextCap", self.meta())

    def test_set_replaces_a_settings_file_holding_a_json_array(self):
        self.open()
        self.settings().write_text('[1]\n')
        self.brigade("set", "--context-cap", "120000")
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 120000\n}\n')

    def test_status_reports_a_drifted_missing_or_broken_settings_file(self):
        self.open()
        clean = ("thread not recorded\nreporting: milestones, no landing contract\n"
                 "workers: 0 of 2 running, 2 idle, auto-start: normal")
        self.assertEqual(self.brigade("status"), clean)
        drift = f"{self.settings()} needs autoCompactWindow 300000; run brigade.py set --context-cap 300000"
        self.settings().write_text('{\n  "autoCompactWindow": 1\n}\n')
        self.assertEqual(self.brigade("status"), f"{clean}\n{drift}")
        self.settings().unlink()
        self.assertEqual(self.brigade("status"), f"{clean}\n{drift}")
        self.settings().write_text('{"autoCompactWindow":')
        self.assertEqual(self.brigade("status"), f"{clean}\n{drift}")
        self.settings().write_text('[1]\n')
        self.assertEqual(self.brigade("status"), f"{clean}\n{drift}")

    def test_status_reports_an_unreadable_settings_file(self):
        if os.geteuid() == 0:
            self.skipTest("root reads through file permissions")
        self.open()
        self.settings().chmod(0o000)
        try:
            drift = (f"{self.settings()} needs autoCompactWindow 300000; "
                     "run brigade.py set --context-cap 300000")
            self.assertTrue(self.brigade("status").endswith(f"\n{drift}"))
        finally:
            self.settings().chmod(0o644)

    def test_other_provider_writes_no_file_and_status_says_the_cap_is_not_enforced(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                     "--coordinator-provider", "other")
        self.assertFalse(self.settings().exists())
        self.assertEqual(
            self.brigade("status"),
            "thread not recorded\nreporting: milestones, no landing contract\n"
            "workers: 0 of 2 running, 2 idle, auto-start: normal\n"
            "context cap 300000 is not enforced on this host")
        self.brigade("set", "--context-cap", "120000")
        self.assertEqual(self.meta()["contextCap"], 120000)
        self.assertFalse(self.settings().exists())
        self.assertTrue(self.brigade("status").endswith("\ncontext cap 120000 is not enforced on this host"))
