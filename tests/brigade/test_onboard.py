from .support import BrigadeTest, json, setUpModule


class OnboardDetectTest(BrigadeTest):
    """Onboarding detection names only the settings the store still needs."""

    def detect(self, *extra):
        return json.loads(self.brigade("onboard", "--project-root", str(self.project), "--name", "Perf",
                                       "--detect", *extra))

    def test_fully_configured_store_asks_nothing(self):
        # Processes: git and land build the fixture project and its contract.
        self.git_project()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--mode", "light", "--intake", "github", "--build-command", "make build",
                     "--check-command", "make test", "--change-note-dir", "changes")
        before = self.store_files()
        found = self.detect()
        self.assertEqual(found["missing"], [])
        self.assertEqual(found["facts"]["restaurant"]["state"], "configured")
        self.assertEqual(found["facts"]["landing_mode"]["state"], "configured")
        self.assertEqual(self.store_files(), before)

    def test_fresh_repo_lists_only_the_missing_settings(self):
        # Processes: git builds the fixture repository. No land contract and no store exist.
        self.git_project()
        (self.project / "package.json").write_text(json.dumps({"scripts": {"test": "jest", "build": "tsc"}}))
        found = self.detect()
        self.assertEqual(found["facts"]["check_command"], {
            "state": "detected", "value": "npm test", "fix": "set --check-command <command>"})
        self.assertEqual(found["facts"]["build_command"], {
            "state": "detected", "value": "npm run build", "fix": "set --build-command <command>"})
        self.assertEqual(found["facts"]["required_checks"]["state"], "unknown")
        self.assertEqual(found["facts"]["generated"]["state"], "none")
        self.assertEqual(found["facts"]["trunk"], {"state": "detected", "value": "main", "fix": None})
        self.assertEqual(found["facts"]["contract_checks"]["state"], "unknown")
        self.assertEqual(found["missing"], ["restaurant", "mode", "intake", "change_note_dir", "landing_mode"])

    def test_generated_paths_without_a_map_read_as_invalid(self):
        # Processes: git and land build the fixture project and a contract with generated paths but no map.
        self.git_project()
        self.init_landing("--generated", "skills")
        self.landing_env()
        self.open()
        found = self.detect()
        self.assertEqual(found["facts"]["generated"]["state"], "invalid")
        self.assertIn("land.py init --generated-map", found["facts"]["generated"]["value"])
        self.assertIn("land.py init --generated-map", found["facts"]["generated"]["fix"])


class OnboardSummaryTest(BrigadeTest):
    """The summary file records one line per fact and its reruns show only what changed."""

    def summary(self):
        return self.brigade("onboard", "--project-root", str(self.project), "--name", "Perf", "--summary")

    def test_changed_setting_shows_old_arrow_new(self):
        self.open()
        first = self.summary()
        self.assertIn("added reporting: milestones", first)
        self.brigade("set", "--reporting", "digest")
        second = self.summary()
        self.assertIn("reporting: milestones -> digest", second)
        lines = (self.at / "onboarding.txt").read_text().splitlines()
        self.assertIn("reporting: digest", lines)
        self.assertTrue((self.at / "onboarding.txt").read_text().endswith("\n"))

    def test_identical_summary_leaves_the_file_untouched(self):
        self.open()
        self.summary()
        stamp = (self.at / "onboarding.txt").stat().st_mtime_ns
        self.assertEqual(self.summary(), "no change to onboarding.txt")
        self.assertEqual((self.at / "onboarding.txt").stat().st_mtime_ns, stamp)
