from .support import BrigadeTest, setUpModule


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
