if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

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
                     "--check-command", "make test", "--change-note-dir", "changes", "--pr-credit", "on")
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
        self.assertEqual(found["missing"], ["restaurant", "mode", "pr_credit", "models", "intake", "change_note_dir", "landing_mode"])

    def test_onboard_reports_no_test_command_when_only_this_repository_script_exists(self):
        self.git_project()
        (self.project / "scripts").mkdir()
        (self.project / "scripts" / "run_tests.py").write_text("")
        found = self.detect()
        self.assertEqual(found["facts"]["check_command"]["state"], "missing")
        self.assertIn("check_command", found["missing"])

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
        # Processes: git builds the fixture repository. Fake git and gh on PATH isolate the trunk read.
        self.git_project()
        import os as _os
        import shutil as _shutil
        import time as _time
        from pathlib import Path as _Path
        from unittest import mock as _mk
        real_git = _shutil.which("git")
        self.assertIsNotNone(real_git)
        bindir = _Path(self.temporary.name) / f"fakebin-trunk-{_time.time_ns()}"
        bindir.mkdir()
        (bindir / "git").write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            "text = ' '.join(args)\n"
            "if 'remote' in text and 'get-url' in text:\n"
            "    print('https://github.com/o/r.git')\n"
            "    sys.exit(0)\n"
            "if 'branch' in text and '--show-current' in text:\n"
            "    print('feature/x')\n"
            "    sys.exit(0)\n"
            "if 'symbolic-ref' in text and 'origin/HEAD' in text:\n"
            "    print('refs/remotes/origin/main')\n"
            "    sys.exit(0)\n"
            f"real = {real_git!r}\n"
            "os.execv(real, [real, *args])\n"
        )
        (bindir / "git").chmod(0o755)
        args_file = _Path(self.temporary.name) / f"gh-args-trunk-{_time.time_ns()}.txt"
        payload = {"required_status_checks": {"contexts": ["test (3.10)", "test (3.12)"], "checks": []}}
        payload_text = json.dumps(payload)
        (bindir / "gh").write_text(
            "#!/usr/bin/env python3\n"
            "import sys, json\n"
            f"args_file = {str(args_file)!r}\n"
            f"payload_text = {payload_text!r}\n"
            "args = sys.argv[1:]\n"
            "open(args_file, 'a').write(' '.join(args) + '\\n')\n"
            "if len(args) >= 2 and args[0] == 'api' and args[1].startswith('repos/'):\n"
            "    print(payload_text)\n"
            "    sys.exit(0)\n"
            "if len(args) >= 2 and args[0] == 'auth' and args[1] == 'status':\n"
            "    sys.exit(0)\n"
            "print('unexpected gh call: ' + ' '.join(args), file=sys.stderr)\n"
            "sys.exit(1)\n"
        )
        (bindir / "gh").chmod(0o755)
        with _mk.patch.dict(_os.environ, {"PATH": str(bindir) + _os.pathsep + _os.environ.get("PATH", "")}):
            found = self.detect()
        recorded = args_file.read_text() if args_file.is_file() else ""
        self.assertIn("/branches/main/", recorded)
        self.assertNotIn("feature/x", recorded)
        self.assertEqual(found["facts"]["required_checks"]["state"], "detected")
        self.assertEqual(found["facts"]["required_checks"]["value"], ["test (3.10)", "test (3.12)"])

    def test_gitlab_remote_gives_unknown_pr_ability(self):
        # Processes: git builds the fixture repository. A fake git on PATH isolates the remote read.
        self.git_project()
        import os as _os
        import shutil as _shutil
        import time as _time
        from pathlib import Path as _Path
        from unittest import mock as _mk
        real_git = _shutil.which("git")
        self.assertIsNotNone(real_git)
        bindir = _Path(self.temporary.name) / f"fakebin-gitlab-{_time.time_ns()}"
        bindir.mkdir()
        (bindir / "git").write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            "text = ' '.join(args)\n"
            "if 'remote' in text and 'get-url' in text:\n"
            "    print('https://gitlab.com/o/r.git')\n"
            "    sys.exit(0)\n"
            f"real = {real_git!r}\n"
            "os.execv(real, [real, *args])\n"
        )
        (bindir / "git").chmod(0o755)
        with _mk.patch.dict(_os.environ, {"PATH": str(bindir) + _os.pathsep + _os.environ.get("PATH", "")}):
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
        # Processes: git and land build the fixture project and its cap.
        self.git_project()
        self.init_landing("--cap", "3")
        self.landing_env()
        self.open()
        found = self.detect()
        self.assertEqual(found["facts"]["repository_cap"],
                         {"state": "detected", "value": 3, "fix": None})

    def test_malformed_status_gives_invalid_landing_and_generated(self):
        # Processes: git and land build the fixture project and its contract. A fake land.py on disk isolates the malformed read.
        self.git_project()
        self.init_landing()
        self.landing_env()
        self.open()
        import os as _os
        import subprocess as _sp
        import sys as _sys
        import time as _time
        from pathlib import Path as _Path
        from ._support.names import ROOT as _ROOT, _owner_words as _owner_words
        from ._support.race_child import _race_args as _race_args
        from .support import SUPPORT as _SUPPORT
        case = _Path(self.temporary.name) / f"fake-land-malformed-{_time.time_ns()}"
        case.mkdir()
        real_land = str(_ROOT / "t3/added/landing/scripts/land.py")
        (case / "land.py").write_text(
            "#!/usr/bin/env python3\n"
            "import subprocess, sys\n"
            f"real = {real_land!r}\n"
            "args = sys.argv[1:]\n"
            "if 'status' in args and '--json' in args:\n"
            "    print('not json')\n"
            "    sys.exit(0)\n"
            "r = subprocess.run([sys.executable, real, *args], capture_output=True, text=True)\n"
            "sys.stdout.write(r.stdout)\n"
            "sys.stderr.write(r.stderr)\n"
            "sys.exit(r.returncode)\n"
        )
        brigade_args = ["--store", str(self.store), "--at", str(self.at), *_owner_words(self.at),
                        "onboard", "--project-root", str(self.project), "--name", "Perf", "--detect"]
        tail = _race_args("fake-land", case, brigade_args)
        proc = _sp.run([_sys.executable, str(_SUPPORT), *tail], capture_output=True, text=True,
                       env=dict(_os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        found = json.loads(proc.stdout.strip())
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
