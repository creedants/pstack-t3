from .support import (
    setUpModule,
    BrigadeTest,
    SCRIPT,
    Path,
    call,
    json,
    os,
    subprocess,
)


class ContextCapTest(BrigadeTest):
    def settings(self):
        return Path(self.project) / ".claude" / "settings.local.json"

    def meta(self):
        return json.loads((self.at / "restaurant.json").read_text())

    def write_settings(self, text):
        """Write this text into the project's settings file, making the directory that holds it."""
        self.settings().parent.mkdir(parents=True, exist_ok=True)
        self.settings().write_text(text)

    def drift(self, held, cap):
        """The reopen line for a settings file that holds held while the store's cap is cap."""
        return f"{self.settings()} holds autoCompactWindow {held}, not {cap}; run brigade.py set --context-cap {cap}"

    def missing_drift(self, cap):
        """The reopen line for a settings file that holds no integer cap while the store's cap is cap."""
        return f"{self.settings()} holds no integer autoCompactWindow; run brigade.py set --context-cap {cap}"

    def open(self, *extra, ok=True, code=None):
        return self.brigade("open", "--project-root", str(self.project), "--name", "Perf", *extra, ok=ok, code=code)

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

    def test_open_refuses_a_settings_directory_that_is_a_symbolic_link(self):
        self.git_project()
        tracked = self.project / "config" / "settings.local.json"
        self.commit_files({"config/settings.local.json": '{\n  "keep": true\n}\n'})
        run = lambda *command: subprocess.run(command, cwd=self.project, capture_output=True)
        self.assertEqual(run("git", "ls-files", "--error-unmatch", "--", "config/settings.local.json").returncode, 0)
        before = tracked.read_bytes()
        (self.project / ".claude").symlink_to("config")
        error = self.open(code=1)
        self.assertEqual(error, f"brigade: {self.project / '.claude'} is a symbolic link; "
                                "brigade writes .claude/settings.local.json through no link")
        self.assertEqual(tracked.read_bytes(), before)
        self.assertFalse(self.at.exists())
        self.assertEqual(list((self.store / "bridge-kit").iterdir()), [])

    def test_open_refuses_a_repository_whose_index_git_cannot_read(self):
        self.git_project()
        self.commit_files({".claude/settings.local.json": '{\n  "keep": true\n}\n'})
        (self.project / ".git" / "index").write_bytes(b"not an index")
        before = self.settings().read_bytes()
        error = self.open(code=1)
        self.assertEqual(error, f"brigade: git cannot say whether {self.settings()} is tracked; "
                                "fix that repository and run this again")
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertFalse(self.at.exists())

    def test_open_refuses_a_repository_where_both_git_lookups_fail(self):
        self.git_project()
        self.commit_files({".claude/settings.local.json": '{\n  "keep": true\n}\n'})
        (self.project / ".git" / "config").write_bytes(b"not a config\n")
        before = self.settings().read_bytes()
        error = self.open(code=1)
        self.assertEqual(error, f"brigade: git cannot say whether {self.settings()} is tracked; "
                                "fix that repository and run this again")
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertFalse(self.at.exists())
        self.assertEqual(list((self.store / "bridge-kit").iterdir()), [])

    def test_open_refuses_a_project_whose_git_cannot_start(self):
        self.git_project()
        self.commit_files({".claude/settings.local.json": '{\n  "keep": true\n}\n'})
        before = self.settings().read_bytes()
        empty = Path(self.temporary.name) / "no-git"
        empty.mkdir()
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                      "open", "--project-root", str(self.project), "--name", "Perf"],
                       env={"PATH": str(empty)})
        self.assertEqual((outcome.code, outcome.stderr_text().strip()),
                         (1, f"brigade: cannot run git in {self.settings().parent}: No such file or directory"))
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertFalse(self.at.exists())

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

    def test_open_refuses_a_tracked_settings_file_symlink_and_changes_nothing(self):
        self.git_project()
        (self.project / "untracked.json").write_text('{"keep":true}')
        self.settings().parent.mkdir(parents=True)
        os.symlink("../untracked.json", self.settings())
        subprocess.run(["git", "-C", str(self.project), "add", ".claude/settings.local.json"], check=True,
                       capture_output=True)
        self.assertEqual(self.open(code=1),
                         f"brigade: {self.settings()} is tracked by git; "
                         "untrack it before brigade writes autoCompactWindow")
        self.assertTrue(self.settings().is_symlink())
        self.assertEqual(os.readlink(self.settings()), "../untracked.json")
        self.assertEqual((self.project / "untracked.json").read_text(), '{"keep":true}')
        self.assertFalse(self.at.exists())
        self.assertEqual(list((self.store / "bridge-kit").iterdir()), [])

    def test_open_refuses_a_dangling_settings_file_symlink_whose_target_is_tracked(self):
        self.git_project()
        self.commit_files({"missing.json": '{"keep":true}'})
        (self.project / "missing.json").unlink()
        self.settings().parent.mkdir(parents=True)
        os.symlink("../missing.json", self.settings())
        self.assertEqual(self.open(code=1),
                         f"brigade: {self.project / 'missing.json'} is tracked by git; "
                         "untrack it before brigade writes autoCompactWindow")
        self.assertTrue(self.settings().is_symlink())
        self.assertEqual(os.readlink(self.settings()), "../missing.json")
        self.assertFalse(self.at.exists())
        self.assertEqual(list((self.store / "bridge-kit").iterdir()), [])

    def test_open_refuses_an_outer_tracked_settings_file_a_nested_repository_hides(self):
        self.git_project()
        self.commit_files({".claude/settings.local.json": '{"keep":true}'})
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.settings().parent, check=True,
                       capture_output=True)
        before = self.settings().read_bytes()
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf", code=1),
                         f"brigade: {self.settings()} is tracked by the repository that holds the project root; "
                         "untrack it there before brigade writes autoCompactWindow")
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertFalse(self.at.exists())
        self.assertEqual(list((self.store / "bridge-kit").iterdir()), [])

    def test_set_refuses_an_outer_tracked_settings_file_a_nested_repository_hides(self):
        self.git_project()
        self.open()
        subprocess.run(["git", "-C", str(self.project), "add", ".claude/settings.local.json"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "settings"],
                       cwd=self.project, capture_output=True, check=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.settings().parent, check=True,
                       capture_output=True)
        before, store = self.settings().read_bytes(), self.store_files()
        self.assertEqual(self.brigade("set", "--context-cap", "120000", code=1),
                         f"brigade: {self.settings()} is tracked by the repository that holds the project root; "
                         "untrack it there before brigade writes autoCompactWindow")
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertEqual(self.store_files(), store)
        self.assertNotIn("contextCap", self.meta())

    def test_open_refuses_an_outer_tracked_settings_file_symlink_a_nested_repository_hides(self):
        self.git_project()
        (self.project / "untracked.json").write_text('{"keep":true}')
        self.settings().parent.mkdir(parents=True)
        os.symlink("../untracked.json", self.settings())
        subprocess.run(["git", "-C", str(self.project), "add", ".claude/settings.local.json"], check=True,
                       capture_output=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.settings().parent, check=True,
                       capture_output=True)
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf", code=1),
                         f"brigade: {self.settings()} is tracked by the repository that holds the project root; "
                         "untrack it there before brigade writes autoCompactWindow")
        self.assertTrue(self.settings().is_symlink())
        self.assertEqual(os.readlink(self.settings()), "../untracked.json")
        self.assertEqual((self.project / "untracked.json").read_text(), '{"keep":true}')
        self.assertFalse(self.at.exists())
        self.assertEqual(list((self.store / "bridge-kit").iterdir()), [])

    def test_open_writes_a_settings_file_a_nested_repository_leaves_untracked(self):
        self.git_project()
        self.settings().parent.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.settings().parent, check=True,
                       capture_output=True)
        self.assertEqual(self.open(), f"opened {self.at}")
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 300000\n}\n')

    def test_open_refuses_a_settings_file_when_the_project_root_repository_git_cannot_read(self):
        self.git_project()
        self.commit_files({".claude/settings.local.json": '{"keep":true}'})
        (self.project / ".git" / "index").write_bytes(b"not an index")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.settings().parent, check=True,
                       capture_output=True)
        before = self.settings().read_bytes()
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf", code=1),
                         f"brigade: git cannot say whether the repository that holds the project root tracks "
                         f"{self.settings()}; fix that repository and run this again")
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertFalse(self.at.exists())
        self.assertEqual(list((self.store / "bridge-kit").iterdir()), [])

    def test_open_refuses_a_tracked_settings_file_symlink_target_a_linked_project_root_path_hides(self):
        self.git_project()
        self.commit_files({".claude/settings.json": '{"keep":true}'})
        self.settings().parent.mkdir(exist_ok=True)
        os.symlink("settings.json", self.settings())
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.settings().parent, check=True,
                       capture_output=True)
        linked = Path(self.temporary.name) / "linked-root"
        linked.symlink_to(self.project)
        target = self.settings().parent / "settings.json"
        before = target.read_bytes()
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                      "open", "--project-root", str(linked), "--name", "Perf"])
        self.assertEqual((outcome.code, outcome.stderr_text().strip()),
                         (1, f"brigade: {target} is tracked by the repository that holds the project root; "
                             "untrack it there before brigade writes autoCompactWindow"))
        self.assertTrue(self.settings().is_symlink())
        self.assertEqual(os.readlink(self.settings()), "settings.json")
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse(self.at.exists())
        self.assertEqual(list((self.store / "bridge-kit").iterdir()), [])

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

    def test_status_reports_a_drifted_missing_or_broken_settings_file(self):
        self.open()
        clean = ("thread not recorded\nreporting: milestones, no landing contract\n"
                 "workers: 0 of 2 running, 2 idle, auto-start: normal\n"
                 "liveness: every 600000 ms (default for milestones)")
        self.assertEqual(self.brigade("status"), clean)
        drift = f"{self.settings()} needs autoCompactWindow 300000; run brigade.py set --context-cap 300000"
        self.settings().write_text('{\n  "autoCompactWindow": 1\n}\n')
        self.assertEqual(self.brigade("status"), f"{clean}\n{drift}")
        self.settings().unlink()
        self.assertEqual(self.brigade("status"), f"{clean}\n{drift}")
        self.write_settings('{"autoCompactWindow":')
        self.assertEqual(self.brigade("status"), f"{clean}\n{drift}")
        self.settings().write_text('[1]\n')
        self.assertEqual(self.brigade("status"), f"{clean}\n{drift}")
        for text in ('{\n  "keep": NaN,\n  "autoCompactWindow": 300000\n}\n',
                     '{\n  "keep": Infinity,\n  "autoCompactWindow": 300000\n}\n',
                     '{\n  "keep": -Infinity,\n  "autoCompactWindow": 300000\n}\n'):
            with self.subTest(text=text):
                self.write_settings(text)
                self.assertEqual(self.brigade("status"), f"{clean}\n{drift}")

    def edits_store_with_settings(self, settings):
        """The edits fixture with .claude/settings.local.json in every commit and a coordinator that writes no file."""
        self.git_project()
        reviewed = self.commit_files({"src/greeting.txt": self.SOURCE, "out/greeting.txt": self.SOURCE.upper(),
                                      "tools/build.py": self.BUILD, "tools/check.py": self.CHECK,
                                      ".claude/settings.local.json": settings})
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                     "--coordinator-provider", "other")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("set", "--build-command", self.BUILD_COMMAND, "--check-command", self.CHECK_COMMAND)
        return reviewed

    def test_edits_verify_compares_a_committed_settings_file_and_allows_only_a_cap_change(self):
        reviewed = self.edits_store_with_settings('{\n  "autoCompactWindow": 300000,\n  "keep": true\n}\n')
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        cap_only = self.head_commit(self.FIXED, **{".claude/settings.local.json":
                                                   '{\n  "autoCompactWindow": 90000,\n  "keep": true\n}\n'})
        self.assertEqual(self.verify(reviewed, cap_only),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n"
                             f"{self.verified(reviewed, cap_only)}", ""))
        self.assertEqual(len(self.edits_rows()), 1)
        changed = self.head_commit(self.FIXED, **{".claude/settings.local.json":
                                                  '{\n  "autoCompactWindow": 90000,\n  "keep": false\n}\n'})
        self.assertEqual(self.verify(reviewed, changed),
                         (1, f"FAIL .claude/settings.local.json: differs from {reviewed} with the edits applied\n"
                             f"built out/greeting.txt\nedited src/greeting.txt",
                          self.not_verified(reviewed, changed)))
        self.assertEqual(len(self.edits_rows()), 1)

    def test_edits_verify_fails_a_settings_edit_hidden_by_the_cap_exemption(self):
        reviewed = self.edits_store_with_settings('{\n  "autoCompactWindow": 300000,\n  "keep": true\n}\n')
        self.edits_report(self.edit_block("1", ".claude/settings.local.json", '"keep": true', '"keep": false'),
                          self.edit_block("2", "src/greeting.txt", "helo", "hello"),
                          self.edit_block("3", "src/greeting.txt", "wrold", "world"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        cap_only = self.head_commit(self.FIXED, **{".claude/settings.local.json":
                                                   '{\n  "autoCompactWindow": 90000,\n  "keep": true\n}\n'})
        self.assertEqual(self.verify(reviewed, cap_only),
                         (1, f"FAIL .claude/settings.local.json: differs from {reviewed} with the edits applied\n"
                             f"built out/greeting.txt\nedited src/greeting.txt",
                          self.not_verified(reviewed, cap_only)))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_passes_the_coordinators_own_settings_file_swept_into_a_head_commit(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED, **{".claude/settings.local.json":
                                               '{\n  "autoCompactWindow": 300000\n}\n'})
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n"
                             f"{self.verified(reviewed, head)}", ""))
        self.assertEqual(len(self.edits_rows()), 1)
        smuggled = self.head_commit(self.FIXED, **{".claude/settings.local.json":
                                                    '{\n  "autoCompactWindow": 90000,\n  "keep": false\n}\n'})
        self.assertEqual(self.verify(reviewed, smuggled),
                         (1, f"FAIL .claude/settings.local.json: differs from {reviewed} with the edits applied\n"
                             f"built out/greeting.txt\nedited src/greeting.txt",
                          self.not_verified(reviewed, smuggled)))
        self.assertEqual(len(self.edits_rows()), 1)

    def open_edits_dish(self):
        """D1 fired and in progress on a coordinator that writes no settings file, with the build and check commands set."""
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                     "--coordinator-provider", "other")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("set", "--build-command", self.BUILD_COMMAND, "--check-command", self.CHECK_COMMAND)

    def head_settings_entry(self, kind, text, mode=None):
        """The head commit holding the FIXED greeting and one settings entry, and its sha.

        kind is "link" for a symbolic link whose target text is text, or "file" for a regular file.
        """
        link = self.settings()
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.exists() or link.is_symlink():
            link.unlink()
        if kind == "link":
            os.symlink(text, link)
        else:
            link.write_text(text)
            if mode is not None:
                link.chmod(mode)
        (self.project / "src").mkdir(parents=True, exist_ok=True)
        (self.project / "out").mkdir(parents=True, exist_ok=True)
        (self.project / "src" / "greeting.txt").write_text(self.FIXED)
        (self.project / "out" / "greeting.txt").write_text(self.FIXED.upper())
        run = lambda *command: subprocess.run(command, cwd=self.project, capture_output=True, text=True, check=True)
        run("git", "add", "-A")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "head")
        return run("git", "rev-parse", "HEAD").stdout.strip()

    def test_edits_verify_fails_a_settings_file_created_as_a_symbolic_link(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_settings_entry("link", '{"autoCompactWindow":300000}')
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL .claude/settings.local.json: differs from {reviewed} with the edits applied\n"
                             f"built out/greeting.txt\nedited src/greeting.txt",
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_a_settings_file_created_as_an_executable_file(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_settings_entry("file", '{\n  "autoCompactWindow": 300000\n}\n', 0o755)
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL .claude/settings.local.json: differs from {reviewed} with the edits applied\n"
                             f"built out/greeting.txt\nedited src/greeting.txt",
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_a_cap_change_on_a_committed_settings_file_symlink(self):
        self.git_project()
        self.settings().parent.mkdir(parents=True, exist_ok=True)
        os.symlink('{"autoCompactWindow":300000}', self.settings())
        reviewed = self.commit_files({"src/greeting.txt": self.SOURCE, "out/greeting.txt": self.SOURCE.upper(),
                                      "tools/build.py": self.BUILD, "tools/check.py": self.CHECK})
        self.open_edits_dish()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_settings_entry("link", '{"autoCompactWindow":90000}')
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL .claude/settings.local.json: differs from {reviewed} with the edits applied\n"
                             f"built out/greeting.txt\nedited src/greeting.txt",
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_compares_a_settings_file_whose_earlier_blob_is_no_object(self):
        reviewed = self.edits_store_with_settings("[1]\n")
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED, **{".claude/settings.local.json": '{\n  "autoCompactWindow": 90000\n}\n'})
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL .claude/settings.local.json: differs from {reviewed} with the edits applied\n"
                             f"built out/greeting.txt\nedited src/greeting.txt",
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

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
            "liveness: every 600000 ms (default for milestones)\n"
            "context cap 300000 is not enforced on this host")
        self.brigade("set", "--context-cap", "120000")
        self.assertEqual(self.meta()["contextCap"], 120000)
        self.assertFalse(self.settings().exists())
        self.assertTrue(self.brigade("status").endswith("\ncontext cap 120000 is not enforced on this host"))

    def thread_landing(self):
        """A local landing contract and a store that records the thread t1."""
        self.init_landing()
        self.landing_env()
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf")
        self.brigade("set", "--thread", "t1")

    def store_a_cap_that_is_not_a_whole_number(self):
        """Write restaurant.json holding contextCap "400000" and return its bytes."""
        meta = self.meta()
        meta["contextCap"] = "400000"
        (self.at / "restaurant.json").write_text(json.dumps(meta))
        return (self.at / "restaurant.json").read_bytes()

    def test_replace_refuses_a_malformed_stored_cap_before_raising_the_landing_floor(self):
        self.thread_landing()
        before = self.store_a_cap_that_is_not_a_whole_number()
        rows = self.landing_rows()
        error = self.brigade("set", "--thread", "t2", "--replace", code=1)
        self.assertIn("brigade: warning: restaurant.json contextCap '400000' is invalid: "
                      "it is not a whole number", error.splitlines())
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)
        self.assertEqual(self.landing_rows(), rows)

    def test_replace_with_a_repair_flag_repairs_the_stored_cap_and_raises_the_landing_floor(self):
        self.thread_landing()
        self.store_a_cap_that_is_not_a_whole_number()
        self.brigade("set", "--thread", "t2", "--replace", "--context-cap", "400000")
        meta = self.meta()
        self.assertEqual((meta["thread"], meta["generation"], meta["contextCap"]), ("t2", 2, 400000))
        self.assertIn(("perf/", 2), self.landing_rows()["owner"])

    def test_replace_with_a_context_cap_raises_no_landing_floor_when_the_settings_read_refuses(self):
        self.thread_landing()
        self.write_settings("{broken")
        settings_before, store_before = self.settings().read_bytes(), self.store_files()
        rows = self.landing_rows()
        error = self.brigade("set", "--thread", "t2", "--replace", "--context-cap", "400000", code=1)
        self.assertEqual(error, f"brigade: {self.settings()} holds invalid JSON; fix or remove it")
        self.assertEqual(self.settings().read_bytes(), settings_before)
        self.assertEqual(self.store_files(), store_before)
        self.assertEqual(self.landing_rows(), rows)

    def test_replace_with_a_context_cap_on_valid_settings_replaces_the_thread_and_writes_the_cap(self):
        self.thread_landing()
        self.write_settings('{\n  "keep": true\n}\n')
        self.brigade("set", "--thread", "t2", "--replace", "--context-cap", "400000")
        meta = self.meta()
        self.assertEqual((meta["thread"], meta["generation"], meta["contextCap"]), ("t2", 2, 400000))
        self.assertIn(("perf/", 2), self.landing_rows()["owner"])
        self.assertEqual(self.settings().read_text(), '{\n  "keep": true,\n  "autoCompactWindow": 400000\n}\n')
