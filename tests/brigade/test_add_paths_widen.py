import os
import shlex
from unittest import mock

from .support import BrigadeTest, SCRIPT, _owner_words, call_in_process, run_globals, setUpModule


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

    def test_add_paths_mismatch_keeps_the_lease_the_other_call_adopted(self):
        self.started()
        self.assertEqual(self.land("lease", "claim", "--holder", "perf/D1", "--paths", "README.md,src/a.py"), "L2")
        self.assertEqual(self.land("lease", "release", "L1"), "L1 released")
        commands = run_globals(self, SCRIPT)["COMMANDS"]
        dish = next(command.handle for command in commands if command.name == "dish").__globals__
        module = dish["widen"].__globals__
        widen_lease = module["widen_lease"]
        paused = []

        def widen_then_run_other(restaurant, lease, claim):
            result = widen_lease(restaurant, lease, claim)
            if not paused:
                paused.append(lease)
                inner = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                       *_owner_words(self.at), "dish", "D1",
                                                       "--add-paths", "inner.py"])
                self.assertEqual(inner.code, 0, inner.stdout_text() + inner.stderr_text())
            return result

        module["widen_lease"] = widen_then_run_other
        try:
            outcome = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                     *_owner_words(self.at), "dish", "D1",
                                                     "--add-paths", "outer.py"])
        finally:
            module["widen_lease"] = widen_lease
        self.assertEqual(outcome.code, 1)
        self.assertEqual(outcome.stderr_text().strip(),
                         "brigade: D1 changed while widening; run dish D1 --add-paths again")
        listed = self.land("lease", "list")
        self.assertIn("L2 active perf/D1 until ", listed)
        self.assertIn(": README.md, inner.py, outer.py, src/a.py", listed)
        self.assertEqual(self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src/a.py", ok=False),
                         "land: paths overlap L2 held by perf/D1 on README.md, inner.py, outer.py, src/a.py")

    def test_drop_after_a_mismatch_releases_the_lease_the_widen_left_unrecorded(self):
        self.started()
        self.assertEqual(self.land("lease", "claim", "--holder", "perf/D1", "--paths", "README.md,src/a.py"), "L2")
        self.assertEqual(self.land("lease", "release", "L1"), "L1 released")
        commands = run_globals(self, SCRIPT)["COMMANDS"]
        dish = next(command.handle for command in commands if command.name == "dish").__globals__
        module = dish["widen"].__globals__
        widen_lease = module["widen_lease"]
        paused = []

        def widen_then_claim(restaurant, lease, claim):
            result = widen_lease(restaurant, lease, claim)
            if not paused:
                paused.append(lease)
                self.assertEqual(self.land("lease", "claim", "--holder", "perf/D1",
                                            "--paths", "README.md,src/a.py"), "L3")
                inner = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                       *_owner_words(self.at), "dish", "D1",
                                                       "--lease", "L3", "--paths", "README.md,src/a.py"])
                self.assertEqual(inner.code, 0, inner.stdout_text() + inner.stderr_text())
            return result

        module["widen_lease"] = widen_then_claim
        try:
            outcome = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                     *_owner_words(self.at), "dish", "D1",
                                                     "--add-paths", "outer.py"])
        finally:
            module["widen_lease"] = widen_lease
        self.assertEqual(outcome.code, 1)
        self.assertEqual(outcome.stderr_text().strip(),
                         "brigade: D1 changed while widening; run dish D1 --add-paths again")
        listed = self.land("lease", "list")
        self.assertIn("L2 active perf/D1 until ", listed)
        self.assertIn("L3 active perf/D1 until ", listed)
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
        self.assertEqual(self.land("lease", "list"), "no leases held")
        self.assertEqual(self.brigade("dish", "D1", "--add-paths", "outer.py", ok=False),
                         "brigade: D1 is dropped; it holds no lease")

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
