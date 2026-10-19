import os
import shlex
from unittest import mock

from .support import BrigadeTest, setUpModule


class AddPathsWidenTest(BrigadeTest):
    def test_add_paths_widens_the_recorded_lease_in_place(self):
        self.started()
        self.assertEqual(self.brigade("dish", "D1", "--add-paths", "src/a.py"), "D1: L1 now covers src/a.py")
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual((row["lease"], row["paths"]), ("L1", "README.md,src/a.py"))
        self.assertEqual(self.lease_lines(), ["L1 active perf/D1: README.md, src/a.py"])

    def test_add_paths_refused_by_another_holders_lease_changes_nothing(self):
        self.started()
        engine = self.store / "bridge-kit" / "engine"
        self.run_at(engine, "open", "--project-root", str(self.project), "--name", "Engine")
        (engine / "menu.md").write_text("## Purpose\n\nEngine.\n")
        self.run_at(engine, "ticket", "add", "--kind", "fix", "--summary", "engine work")
        self.assertEqual(self.run_at(engine, "fire", "--tickets", "T1", "--station", "bug-fix",
                                     "--summary", "e", "--paths", "src/a.py"),
                         "D1 (lease L2 held by engine/D1)")
        leases = self.land("lease", "list")
        self.assertEqual(self.brigade("dish", "D1", "--add-paths", "src/a.py", ok=False),
                         "brigade: nothing widened: paths overlap L2 held by engine/D1 on src/a.py")
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual((row["lease"], row["paths"]), ("L1", "README.md"))
        self.assertEqual(self.land("lease", "list"), leases)

    def test_add_paths_for_a_covered_path_reports_already_and_keeps_one_lease(self):
        self.started()
        self.assertEqual(self.brigade("dish", "D1", "--add-paths", "src/a.py"), "D1: L1 now covers src/a.py")
        self.assertEqual(self.brigade("dish", "D1", "--add-paths", "src/a.py"), "D1: L1 already covers src/a.py")
        self.assertEqual(self.lease_lines(), ["L1 active perf/D1: README.md, src/a.py"])

    def test_add_paths_reconciles_a_new_lease_claimed_by_a_stopped_swap(self):
        self.started()
        self.assertEqual(self.land("lease", "claim", "--holder", "perf/D1", "--paths", "README.md,src/a.py"), "L2")
        self.assertEqual(self.brigade("dish", "D1", "--add-paths", "src/a.py"), "D1: L1 now covers src/a.py")
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual((row["lease"], row["paths"]), ("L1", "README.md,src/a.py"))
        self.assertEqual(self.lease_lines(), ["L1 active perf/D1: README.md, src/a.py"])

    def test_add_paths_reconciles_a_swap_that_recorded_the_new_lease(self):
        self.started()
        self.assertEqual(self.land("lease", "claim", "--holder", "perf/D1", "--paths", "README.md,src/a.py"), "L2")
        self.hand_edit("dishes.tsv", "D1", lease="L2", paths="README.md,src/a.py")
        self.assertEqual(self.brigade("dish", "D1", "--add-paths", "src/a.py"), "D1: L2 already covers src/a.py")
        self.assertEqual(self.lease_lines(), ["L2 active perf/D1: README.md, src/a.py"])

    def test_add_paths_claims_fresh_when_the_recorded_lease_was_released(self):
        self.init_landing("--cap", "3")
        with mock.patch.dict(os.environ, self.land_env()):
            self.open()
            self.brigade("set", "--change-note-dir", "changes")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                         "--branch", "perf/d1", "--paths", "src,changes")
            self.assertEqual(self.land("lease", "release", "L1"), "L1 released")
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
