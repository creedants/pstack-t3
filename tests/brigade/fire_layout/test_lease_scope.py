from ..support import SCRIPT, _owner_words, call


class LeaseScopeMixin:
    def test_same_branch_dish_over_unexpanded_lease_prints_only_in_progress(self):
        self.git_project()
        self.commit_paths("t3/runtime.md,t3/setup.md")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--change-note-dir", "changes")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                     "--branch", "perf/d1", "--paths", "t3/runtime.md")
        self.assertEqual(self.dish_paths(), "t3/runtime.md,changes/perf%2Fd1.md")
        self.vendor_layout()
        self.assertEqual(self.brigade("dish", "D1", "--branch", "perf/d1"), "D1 in-progress")
        self.assertEqual(self.dish_paths(), "t3/runtime.md,changes/perf%2Fd1.md")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "t")
        out = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "t",
                           "--paths", "t3/setup.md")
        self.assertEqual(out, "D2 (lease L2 held by perf/D2)\nadded skills/setup-pstack/SKILL.md, docs/skills.md")

    def test_startable_blocks_overlapping_generated_dirs(self):
        self.git_project()
        self.vendor_layout()
        self.commit_paths("t3/added/teach,t3/overrides/teach")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--paths", "t3/added/teach")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "b", "--paths", "t3/overrides/teach")
        text = self.brigade("startable")
        self.assertIn("waiting tickets: 2, startable: 1, blocked: 1", text)
        self.assertIn("T1 normal fix, 0m: startable; t3/added/teach", text)
        self.assertIn("T2 normal fix, 0m: blocked, starts after T1; t3/overrides/teach", text)

    def test_startable_saved_scope_with_whole_repository_component_blocks_the_second_scope(self):
        self.git_project()
        self.vendor_layout()
        self.commit_paths("README.md,t1.txt,t2.txt")
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "filler")
        self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "filler")
        for ident in ("T1", "T2"):
            self.assertEqual(self.brigade("fire", "--tickets", ident, "--station", "bug-fix",
                                          "--summary", ident, "--paths", f".,{ident.lower()}.txt", "--doc-item",
                                          ok=False),
                             "brigade: nothing fired: 1 of 1 workers running")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.brigade("set", "--workers", "2")
        text = self.brigade("startable")
        self.assertIn("waiting tickets: 2, startable: 1, blocked: 1", text)
        self.assertIn("T1 normal fix, 0m: startable; .,t1.txt --doc-item", text)
        self.assertIn("T2 normal fix, 0m: blocked, starts after T1; .,t2.txt --doc-item", text)
        saved = {line[:2]: line.split("; ", 1)[1][:-len(" --doc-item")]
                 for line in text.splitlines() if line.startswith(("T1 ", "T2 "))}
        self.assertEqual(saved, {"T1": ".,t1.txt", "T2": ".,t2.txt"})
        self.assertIn("D2 (lease", self.brigade("fire", "--tickets", "T1", "--station", "bug-fix",
                                                "--summary", "one", "--paths", saved["T1"], "--doc-item"))
        refused = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "two",
                               "--paths", saved["T2"], "--doc-item", ok=False)
        self.assertIn("paths overlap L1 held by perf/D2", refused)

    def test_startable_blocks_the_second_scope_when_only_the_first_saved_scope_holds_the_repository_root(self):
        # Only the first saved scope holds the root, so the overlap needs scopes_overlap, not paths_overlap.
        text = self.saved_scope_pair(".,t1.txt", "t2.txt")
        self.assertIn("waiting tickets: 2, startable: 1, blocked: 1", text)
        self.assertIn("T1 normal fix, 0m: startable; .,t1.txt", text)
        self.assertIn("T2 normal fix, 0m: blocked, starts after T1; t2.txt", text)

    def test_startable_blocks_the_second_scope_when_only_the_second_saved_scope_holds_the_repository_root(self):
        # Only the second saved scope holds the root, so the overlap needs scopes_overlap, not paths_overlap.
        text = self.saved_scope_pair("t1.txt", ".,t2.txt")
        self.assertIn("waiting tickets: 2, startable: 1, blocked: 1", text)
        self.assertIn("T1 normal fix, 0m: startable; t1.txt", text)
        self.assertIn("T2 normal fix, 0m: blocked, starts after T1; .,t2.txt", text)

    def test_fire_leases_a_stale_split_path_and_warns_on_stderr(self):
        self.git_project()
        self.vendor_layout()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                      *_owner_words(self.at), "fire", "--tickets", "T1",
                                      "--station", "bug-fix", "--summary", "s",
                                      "--paths", "tests/test_landing.py"])
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stdout_text().strip(), "D1 (lease L1 held by perf/D1)")
        self.assertEqual(outcome.stderr_text().splitlines(),
                         ["fire: tests/test_landing.py is not in the checkout; a stale ticket path leases nothing real"])
        self.assertIn("tests/test_landing.py", self.land("lease", "list"))
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual(row["state"], "in-progress")
        self.assertIn("tests/test_landing.py", row["paths"])

    def test_fire_leases_a_missing_path_with_no_build_layout_and_warns_on_stderr(self):
        self.git_project()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                      *_owner_words(self.at), "fire", "--tickets", "T1",
                                      "--station", "bug-fix", "--summary", "s",
                                      "--paths", "missing.txt"])
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stdout_text().strip(), "D1 (lease L1 held by perf/D1)")
        self.assertEqual(outcome.stderr_text().splitlines(),
                         ["fire: missing.txt is not in the checkout; a stale ticket path leases nothing real"])
        self.assertIn("missing.txt", self.land("lease", "list"))
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual(row["state"], "in-progress")
        self.assertIn("missing.txt", row["paths"])

    def test_fire_warns_once_per_missing_path_sorted_and_deduplicated(self):
        self.git_project()
        self.vendor_layout()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                      *_owner_words(self.at), "fire", "--tickets", "T1",
                                      "--station", "bug-fix", "--summary", "s",
                                      "--paths", "zebra.txt,apple.txt,zebra.txt"])
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stdout_text().strip(), "D1 (lease L1 held by perf/D1)")
        self.assertEqual(outcome.stderr_text().splitlines(),
                         ["fire: apple.txt is not in the checkout; a stale ticket path leases nothing real",
                          "fire: zebra.txt is not in the checkout; a stale ticket path leases nothing real"])
        listed = self.land("lease", "list")
        self.assertIn("apple.txt", listed)
        self.assertIn("zebra.txt", listed)
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual(row["state"], "in-progress")
        self.assertIn("zebra.txt,apple.txt,zebra.txt", row["paths"])
