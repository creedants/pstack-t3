from ..support import os

class EditsStatusMixin:
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

