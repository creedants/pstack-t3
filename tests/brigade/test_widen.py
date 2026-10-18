from .support import BrigadeTest, setUpModule, SCRIPT, _owner_words, call_in_process


class WidenTest(BrigadeTest):
    def test_widen_claims_new_and_releases_old_fails_when_old_lease_stays_active(self):
        self.started()
        self.assertEqual(self.brigade("dish", "D1", "--add-paths", "src/a.py"), "D1: L2 replaces L1")
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual((row["lease"], row["paths"]), ("L2", "README.md,src/a.py"))
        self.assertEqual(self.lease_lines(), ["L2 active perf/D1: README.md, src/a.py"])

    def test_widen_refused_by_other_holder_changes_nothing_fails_when_dish_moves_on_refusal(self):
        self.started()
        engine = self.store / "bridge-kit" / "engine"
        self.run_at(engine, "open", "--project-root", str(self.project), "--name", "Engine")
        (engine / "menu.md").write_text("## Purpose\n\nEngine.\n")
        self.run_at(engine, "ticket", "add", "--kind", "fix", "--summary", "engine work")
        self.assertEqual(self.run_at(engine, "fire", "--tickets", "T1", "--station", "bug-fix",
                                     "--summary", "e", "--paths", "src/a.py"),
                         "D1 (lease L2 held by engine/D1)")
        before = self.store_files()
        self.assertEqual(self.brigade("dish", "D1", "--add-paths", "src/a.py", ok=False),
                         "brigade: nothing widened: paths overlap L2 held by engine/D1 on src/a.py")
        self.assertEqual(self.store_files(), before)
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual((row["lease"], row["paths"]), ("L1", "README.md"))

    def test_widen_rerun_is_noop_fails_when_second_claim_leaks_a_lease(self):
        self.started()
        self.assertEqual(self.brigade("dish", "D1", "--add-paths", "src/a.py"), "D1: L2 replaces L1")
        before = self.land("lease", "list")
        self.assertEqual(self.brigade("dish", "D1", "--add-paths", "src/a.py"),
                         "D1: L2 already covers src/a.py")
        self.assertEqual(self.land("lease", "list"), before)
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual((row["lease"], row["paths"]), ("L2", "README.md,src/a.py"))

    def test_widen_released_ancestor_cover_fails_when_printed_command_leaves_no_active_lease(self):
        import os
        import shlex
        from unittest import mock
        self.init_landing("--cap", "3")
        with mock.patch.dict(os.environ, self.land_env()):
            self.open()
            self.brigade("set", "--change-note-dir", "changes")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                         "--branch", "perf/d1", "--paths", "src,changes")
            self.assertEqual(self.land("lease", "release", "L1"), "L1 released")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            out = self.brigade("dish", "D1", "--branch", "perf/d1-r2")
            self.assertIn("$B dish D1 --add-paths changes/perf%2Fd1-r2.md", out)
            printed = [line.strip() for line in out.splitlines() if "dish D1 --add-paths" in line][0]
            args = shlex.split(printed.removeprefix("$B ").strip())
            self.assertEqual(self.brigade(*args), "D1: L2 replaces L1")
            row = self.table_row(self.at, "dishes.tsv", "D1")
            self.assertEqual(row["lease"], "L2")
            self.assertIn("changes/perf%2Fd1-r2.md", row["paths"])
            lines = self.lease_lines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith("L2 active perf/D1: "))
            for part in ("src", "changes", "changes/perf%2Fd1.md", "changes/perf%2Fd1-r2.md"):
                self.assertIn(part, lines[0])

    def test_widen_expired_ancestor_cover_fails_when_printed_command_leaves_no_active_lease(self):
        import os
        import shlex
        from contextlib import closing
        from unittest import mock
        self.init_landing("--cap", "3")
        with mock.patch.dict(os.environ, self.land_env()):
            self.open()
            self.brigade("set", "--change-note-dir", "changes")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                         "--branch", "perf/d1", "--paths", "src,changes")
            with closing(self.landing_db()) as db, db:
                db.execute("UPDATE lease SET expires = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", 1))
            self.assertEqual(self.land("lease", "list"), "no leases held")
            out = self.brigade("dish", "D1", "--branch", "perf/d1-r2")
            self.assertIn("$B dish D1 --add-paths changes/perf%2Fd1-r2.md", out)
            printed = [line.strip() for line in out.splitlines() if "dish D1 --add-paths" in line][0]
            args = shlex.split(printed.removeprefix("$B ").strip())
            self.assertEqual(self.brigade(*args), "D1: L2 replaces L1")
            row = self.table_row(self.at, "dishes.tsv", "D1")
            self.assertEqual(row["lease"], "L2")
            self.assertIn("changes/perf%2Fd1-r2.md", row["paths"])
            lines = self.lease_lines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith("L2 active perf/D1: "))
            for part in ("src", "changes", "changes/perf%2Fd1.md", "changes/perf%2Fd1-r2.md"):
                self.assertIn(part, lines[0])

    def test_in_review_refuses_uncovered_paths_fails_when_out_of_lease_reaches_review(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n", "outside.py": "x\n"})
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", sha, ok=False),
                         f"brigade: D1 at {sha[:12]} changes paths outside L1: outside.py; "
                         f"run dish D1 --add-paths outside.py or split the change")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")

    def test_in_review_passes_covered_change_fails_when_covered_change_is_refused(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", sha), "D1 in-review")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-review")

    def test_in_review_refuses_fetch_failure_fails_when_failed_fetch_enters_review(self):
        import json
        import subprocess
        from contextlib import closing
        self.started()
        with closing(self.landing_db()) as db, db:
            db.execute("UPDATE contract SET value = ? WHERE key = 'mode'", (json.dumps("merge"),))
            db.execute("UPDATE contract SET value = ? WHERE key = 'trunk'", (json.dumps("main"),))
            db.execute("UPDATE contract SET value = ? WHERE key = 'remote'", (json.dumps("origin"),))
        subprocess.run(["git", "remote", "add", "origin", "file:///nonexistent-pstack-remote"],
                       cwd=self.project, capture_output=True)
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        before = self.store_files()
        outcome = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                 *_owner_words(self.at),
                                                 "dish", "D1", "--state", "in-review", "--sha", sha])
        self.assertEqual(outcome.code, 1)
        out = outcome.stderr_text().strip()
        self.assertIn("D1 cannot check coverage", out)
        self.assertIn("fetch", out)
        self.assertIn("Fetch origin main", out)
        self.assertIn("in-review", out)
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")

    def test_in_review_refuses_bad_sha_fails_when_unknown_sha_reaches_review(self):
        self.started()
        bad = "0" * 40
        before = self.store_files()
        out = self.brigade("dish", "D1", "--state", "in-review", "--sha", bad, ok=False)
        self.assertIn(f"D1 at {bad} cannot check coverage", out)
        self.assertIn("merge-base", out)
        self.assertIn("Pass a SHA that exists", out)
        self.assertIn("in-review", out)
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")

    def test_in_review_skips_no_lease_fails_when_no_lease_is_refused(self):
        self.started()
        self.hand_edit("dishes.tsv", "D1", lease="", paths="")
        bad = "0" * 40
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", bad), "D1 in-review")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-review")

    def test_in_review_skips_no_contract_fails_when_no_contract_is_refused(self):
        self.git_project()
        self.landing_env()
        self.open()
        (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s")
        self.hand_edit("dishes.tsv", "D1", lease="L1", paths="README.md")
        bad = "0" * 40
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", bad), "D1 in-review")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-review")
