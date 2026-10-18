from .support import BrigadeTest, setUpModule


class GateParityTest(BrigadeTest):
    def submit_entry(self, sha, lease):
        return self.land("submit", "--holder", "perf/D1", "--branch", "perf/d1",
                         "--sha", sha, "--lease", lease, "--reviewer", "reviewer")

    def test_gate_agrees_with_submit_for_whole_repo_widen(self):
        self.started()
        self.assertEqual(self.brigade("dish", "D1", "--add-paths", "."), "D1: L2 replaces L1")
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n", "outside.py": "x\n"})
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", sha), "D1 in-review")
        self.assertEqual(self.submit_entry(sha, "L2"), "E1")

    def test_gate_agrees_with_submit_for_generated_copy(self):
        self.started("--generated", "gen.txt", "--generated-map", "src/gen-src.txt=gen.txt", "--rebuild", "make gen")
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n", "gen.txt": "gen\n"})
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", sha), "D1 in-review")
        self.assertEqual(self.submit_entry(sha, "L1"), "E1")

    def test_gate_agrees_with_submit_for_own_change_note(self):
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--change-note-dir", "changes")
        (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                                      "--branch", "perf/d1", "--paths", "src/a.py"),
                         "D1 (lease L1 held by perf/D1)")
        sha = self.worker_commit("perf/d1", {"src/a.py": "x\n", "changes/perf%2Fd1.md": "note\n"})
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", sha), "D1 in-review")
        self.assertEqual(self.submit_entry(sha, "L1"), "E1")

    def test_gate_agrees_with_submit_for_directory_lease(self):
        self.started(paths="src")
        sha = self.worker_commit("perf/d1", {"src/a.py": "x\n"})
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", sha), "D1 in-review")
        self.assertEqual(self.submit_entry(sha, "L1"), "E1")
