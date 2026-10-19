from .support import BrigadeTest, json, setUpModule


class OnboardDetectTest(BrigadeTest):
    """Missing holds the settings in state missing in table order, which excludes invalid ones."""

    def detect(self, *extra):
        return json.loads(self.brigade("onboard", "--project-root", str(self.project), "--name", "Perf",
                                       "--detect", *extra))

    def test_fully_configured_store_asks_nothing(self):
        # Processes: git and land build the fixture project and its contract.
        self.git_project()
        self.init_landing()
        self.landing_env()
        self.open()
        roles_dir = self.project / ".pstack"
        roles_dir.mkdir(exist_ok=True)
        (roles_dir / "t3-roles.json").write_text(json.dumps({"version": 1, "budget": "default", "roles": {}}))
        self.brigade("set", "--mode", "light", "--intake", "github", "--build-command", "make build",
                     "--check-command", "make test", "--change-note-dir", "changes")
        before = self.store_files()
        found = self.detect()
        self.assertEqual(found["missing"], [])
        self.assertEqual(found["facts"]["restaurant"]["state"], "configured")
        self.assertEqual(found["facts"]["landing_mode"]["state"], "configured")
        self.assertEqual(found["facts"]["models"], {"state": "configured", "value": "present", "fix": "run setup-pstack"})
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
        self.assertEqual(found["facts"]["models"], {"state": "missing", "value": "", "fix": "run setup-pstack"})
        self.assertEqual(found["missing"], ["restaurant", "mode", "models", "intake", "change_note_dir", "landing_mode"])

    def test_global_at_selects_the_existing_store(self):
        self.open()
        self.brigade("set", "--mode", "full")
        found = json.loads(self.brigade("onboard", "--project-root", str(self.project),
                                       "--name", "Other", "--detect"))
        self.assertEqual(found["facts"]["mode"]["value"], "full")

    def test_models_configured_when_project_roles_file_exists(self):
        # Processes: git builds the fixture repository. No land contract and no store exist.
        self.git_project()
        roles_dir = self.project / ".pstack"
        roles_dir.mkdir(exist_ok=True)
        (roles_dir / "t3-roles.json").write_text(json.dumps({"version": 1, "budget": "default", "roles": {}}))
        found = self.detect()
        self.assertEqual(found["facts"]["models"]["state"], "configured")
        self.assertNotIn("models", found["missing"])

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

    def test_non_utf8_restaurant_json_reports_invalid_restaurant(self):
        # Processes: git builds the fixture repository. A non UTF-8 restaurant.json must not crash detect.
        self.git_project()
        self.open()
        (self.at / "restaurant.json").write_bytes(b"\xff\xfe\x00bad")
        out = self.brigade("onboard", "--project-root", str(self.project), "--name", "Perf",
                           "--detect", owner=False)
        found = json.loads(out)
        self.assertEqual(found["facts"]["restaurant"]["state"], "invalid")

    def test_outside_mode_reads_as_invalid(self):
        # Processes: git builds the fixture repository. The writers refuse this value, so the test writes it directly.
        self.git_project()
        self.open()
        meta_path = self.at / "restaurant.json"
        meta = json.loads(meta_path.read_text())
        meta["mode"] = "medium"
        meta_path.write_text(json.dumps(meta))
        found = self.detect()
        self.assertEqual(found["facts"]["mode"]["state"], "invalid")

    def test_required_checks_use_trunk_not_current_branch(self):
        # Processes: git builds the fixture repository. Fake git and gh isolate the trunk read.
        self.git_project()
        import subprocess as sp
        from unittest import mock as mk
        real = sp.run
        seen = {}
        def fake(cmd, *a, **k):
            text = " ".join(str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else [cmd]))
            if "remote" in text and "get-url" in text:
                return sp.CompletedProcess(cmd, 0, "https://github.com/o/r.git\n", "")
            if "branch" in text and "--show-current" in text:
                return sp.CompletedProcess(cmd, 0, "feature/x\n", "")
            if "symbolic-ref" in text and "origin/HEAD" in text:
                return sp.CompletedProcess(cmd, 0, "refs/remotes/origin/main\n", "")
            if isinstance(cmd, (list, tuple)) and len(cmd) > 2 and cmd[0] == "gh" and cmd[1] == "api":
                seen["url"] = cmd[2]
                payload = {"required_status_checks": {"contexts": ["test (3.10)", "test (3.12)"], "checks": []}}
                return sp.CompletedProcess(cmd, 0, json.dumps(payload), "")
            if isinstance(cmd, (list, tuple)) and len(cmd) > 1 and cmd[0] == "gh" and cmd[1] == "auth":
                return sp.CompletedProcess(cmd, 0, "", "")
            return real(cmd, *a, **k)
        with mk.patch("subprocess.run", side_effect=fake):
            found = self.detect()
        self.assertIn("/branches/main/", seen.get("url", ""))
        self.assertNotIn("feature/x", seen.get("url", ""))
        self.assertEqual(found["facts"]["required_checks"]["state"], "detected")
        self.assertEqual(found["facts"]["required_checks"]["value"], ["test (3.10)", "test (3.12)"])

    def test_gitlab_remote_gives_unknown_pr_ability(self):
        # Processes: git builds the fixture repository. Fake git and gh isolate the remote read.
        self.git_project()
        import subprocess as sp
        from unittest import mock as mk
        real = sp.run
        def fake(cmd, *a, **k):
            text = " ".join(str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else [cmd]))
            if "remote" in text and "get-url" in text:
                return sp.CompletedProcess(cmd, 0, "https://gitlab.com/o/r.git\n", "")
            if isinstance(cmd, (list, tuple)) and len(cmd) > 1 and cmd[0] == "gh" and cmd[1] == "auth":
                return sp.CompletedProcess(cmd, 0, "", "")
            return real(cmd, *a, **k)
        with mk.patch("subprocess.run", side_effect=fake):
            found = self.detect()
        self.assertEqual(found["facts"]["pr_ability"]["state"], "unknown")
        self.assertEqual(found["facts"]["pr_ability"]["value"], "remote is not GitHub")

    def test_recorded_contract_gives_detected_contract_checks_and_generated(self):
        # Processes: git and land build the fixture project and its contract with checks and generated map.
        self.git_project()
        self.init_landing("--check", "make test", "--generated", "skills", "--generated-map", "t3src=skills/dst", "--rebuild", "python3 scripts/build.py")
        self.landing_env()
        self.open()
        found = self.detect()
        self.assertEqual(found["facts"]["contract_checks"]["state"], "detected")
        self.assertEqual(found["facts"]["contract_checks"]["value"], ["make test"])
        self.assertEqual(found["facts"]["generated"]["state"], "detected")

    def test_bad_workers_reporting_autofire_read_as_invalid(self):
        # Processes: git builds the fixture repository. The writers refuse these values, so the test writes them directly.
        self.git_project()
        self.open()
        meta_path = self.at / "restaurant.json"
        for key, bad in (("workers", 0), ("workers", 1.5), ("workers", True), ("reporting", "bad"), ("autofire", "bad")):
            meta = json.loads(meta_path.read_text())
            meta[key] = bad
            meta_path.write_text(json.dumps(meta))
            found = self.detect()
            self.assertEqual(found["facts"][key]["state"], "invalid", (key, bad))
            meta = json.loads(meta_path.read_text())
            meta.pop(key, None)
            meta_path.write_text(json.dumps(meta))

    def test_repository_cap_reports_recorded_cap(self):
        # Processes: git and land build the fixture project and its cap, then a second project with no cap.
        self.git_project()
        self.init_landing("--cap", "3")
        self.landing_env()
        self.open()
        found = self.detect()
        self.assertEqual(found["facts"]["repository_cap"],
                         {"state": "configured", "value": 3, "fix": None})

    def test_malformed_status_gives_invalid_landing_and_generated(self):
        # Processes: git and land build the fixture project and its contract. Fake land status isolates the malformed read.
        self.git_project()
        self.init_landing()
        self.landing_env()
        self.open()
        import subprocess as sp
        from unittest import mock as mk
        real = sp.run
        def fake(cmd, *a, **k):
            text = " ".join(str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else [cmd]))
            if "land.py" in text and "status" in text and "--json" in text:
                return sp.CompletedProcess(cmd, 0, "not json", "")
            return real(cmd, *a, **k)
        with mk.patch("subprocess.run", side_effect=fake):
            found = self.detect()
        self.assertEqual(found["facts"]["landing_mode"]["state"], "invalid")
        self.assertEqual(found["facts"]["generated"]["state"], "invalid")
        self.assertEqual(found["facts"]["repository_cap"]["state"], "invalid")
        self.assertEqual(found["facts"]["contract_checks"]["state"], "invalid")


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

    def test_whitespace_drift_rewrites_with_normalized_no_fact_changes(self):
        self.open()
        self.summary()
        path = self.at / "onboarding.txt"
        canonical = path.read_bytes()
        text = path.read_text()
        path.write_text(text.replace("\n", "  \n"))
        self.assertNotEqual(path.read_bytes(), canonical)
        self.assertEqual(self.summary(), "normalized onboarding.txt with no fact changes")
        self.assertEqual(path.read_bytes(), canonical)

    def test_multiline_value_stays_one_line_per_fact(self):
        self.open()
        self.brigade("set", "--build-command", "make build\nsecond line\u2028third line\vfourth line")
        found = json.loads(self.brigade("onboard", "--project-root", str(self.project), "--name", "Perf", "--detect"))
        self.assertIn("\n", found["facts"]["build_command"]["value"])
        self.summary()
        lines = (self.at / "onboarding.txt").read_text().splitlines()
        self.assertEqual(len(lines), len(found["facts"]))
        matching = [line for line in lines if line.startswith("build_command: ")]
        self.assertEqual(len(matching), 1)
        self.assertIn("make build second line third line fourth line", matching[0])
        self.assertEqual(self.summary(), "no change to onboarding.txt")
