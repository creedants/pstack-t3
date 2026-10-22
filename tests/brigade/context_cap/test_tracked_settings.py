from ..support import Path, SCRIPT, call, os, subprocess

class TrackedSettingsMixin:
    def test_open_refuses_a_settings_directory_that_is_a_symbolic_link(self):
        self.git_project()
        tracked = self.project / "config" / "settings.local.json"
        self.commit_files({"config/settings.local.json": '{\n  "keep": true\n}\n'})
        run = lambda *command: subprocess.run(command, cwd=self.project, capture_output=True)
        self.assertEqual(run("git", "ls-files", "--error-unmatch", "--", "config/settings.local.json").returncode, 0)
        before = tracked.read_bytes()
        (self.project / ".claude").symlink_to("config")
        error = self.open_cap(code=1)
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
        error = self.open_cap(code=1)
        self.assertEqual(error, f"brigade: git cannot say whether {self.settings()} is tracked; "
                                "fix that repository and run this again")
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertFalse(self.at.exists())

    def test_open_refuses_a_repository_where_both_git_lookups_fail(self):
        self.git_project()
        self.commit_files({".claude/settings.local.json": '{\n  "keep": true\n}\n'})
        (self.project / ".git" / "config").write_bytes(b"not a config\n")
        before = self.settings().read_bytes()
        error = self.open_cap(code=1)
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
                                      "open", "--project-root", str(self.project), "--name", "Perf",
                                      "--context-cap", "300000"],
                       env={"PATH": str(empty)})
        self.assertEqual((outcome.code, outcome.stderr_text().strip()),
                         (1, f"brigade: cannot run git in {self.settings().parent}: No such file or directory"))
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertFalse(self.at.exists())

    def test_open_refuses_a_tracked_settings_file_symlink_and_changes_nothing(self):
        self.git_project()
        (self.project / "untracked.json").write_text('{"keep":true}')
        self.settings().parent.mkdir(parents=True)
        os.symlink("../untracked.json", self.settings())
        subprocess.run(["git", "-C", str(self.project), "add", ".claude/settings.local.json"], check=True,
                       capture_output=True)
        self.assertEqual(self.open_cap(code=1),
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
        self.assertEqual(self.open_cap(code=1),
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
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                                      "--context-cap", "300000", code=1),
                         f"brigade: {self.settings()} is tracked by the repository that holds the project root; "
                         "untrack it there before brigade writes autoCompactWindow")
        self.assertEqual(self.settings().read_bytes(), before)
        self.assertFalse(self.at.exists())
        self.assertEqual(list((self.store / "bridge-kit").iterdir()), [])

    def test_set_refuses_an_outer_tracked_settings_file_a_nested_repository_hides(self):
        self.git_project()
        self.write_settings('{\n  "autoCompactWindow": 300000\n}\n')
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
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                                      "--context-cap", "300000", code=1),
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
        self.assertEqual(self.open_cap(), f"opened {self.at}")
        self.assertEqual(self.settings().read_text(), '{\n  "autoCompactWindow": 300000\n}\n')

    def test_open_refuses_a_settings_file_when_the_project_root_repository_git_cannot_read(self):
        self.git_project()
        self.commit_files({".claude/settings.local.json": '{"keep":true}'})
        (self.project / ".git" / "index").write_bytes(b"not an index")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.settings().parent, check=True,
                       capture_output=True)
        before = self.settings().read_bytes()
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                                      "--context-cap", "300000", code=1),
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
                                      "open", "--project-root", str(linked), "--name", "Perf",
                                      "--context-cap", "300000"])
        self.assertEqual((outcome.code, outcome.stderr_text().strip()),
                         (1, f"brigade: {target} is tracked by the repository that holds the project root; "
                             "untrack it there before brigade writes autoCompactWindow"))
        self.assertTrue(self.settings().is_symlink())
        self.assertEqual(os.readlink(self.settings()), "settings.json")
        self.assertEqual(target.read_bytes(), before)
        self.assertFalse(self.at.exists())
        self.assertEqual(list((self.store / "bridge-kit").iterdir()), [])

