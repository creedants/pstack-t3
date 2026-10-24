if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import json
import sqlite3
import unittest
from .support import ARCHIVE, CREDENTIAL, SESSION, OLD, P1, P2, RECENT, OpsTest, setUpModule

class ArchiveAncestryTest(OpsTest):
    def test_a_database_missing_a_needed_table_archives_nothing_and_exits_non_zero(self):
        world = self.world()
        base = sqlite3.connect(world.database)
        base.execute("alter table orchestration_v2_projection_nodes rename to renamed_nodes")
        base.commit()
        base.close()
        done = world.sandbox.run(ARCHIVE, "--project", "fixture project", "--selection-only",
                                 T3_CODE_MCP_AUTHORIZATION=CREDENTIAL)
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(world.stub.requests, [])

    def test_an_ancestor_above_a_deleted_bridge_whose_child_has_a_pending_runless_node_is_not_selected(self):
        threads = (("root", P1, None, (), OLD, ""), ("ancestor", P1, "root", (), OLD, "S"),
                   ("bridge", P1, "ancestor", (), OLD, "D"), ("kid-live", P1, "bridge", (), OLD, "S"))
        nodes = (("kid-live", None, "pending"),)
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE, "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))
        self.assertEqual(done.archived, ())

    def test_a_child_below_a_deleted_bridge_whose_ancestor_has_a_pending_runless_node_is_not_selected(self):
        threads = (("root", P1, None, (), OLD, ""), ("ancestor-live", P1, "root", (), OLD, "S"),
                   ("bridge", P1, "ancestor-live", (), OLD, "D"), ("kid-old", P1, "bridge", (), OLD, "S"))
        nodes = (("ancestor-live", None, "pending"),)
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE, "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))
        self.assertEqual(done.archived, ())

    def test_an_ancestor_above_a_bridge_in_another_project_whose_child_has_a_pending_runless_node_is_not_selected(self):
        threads = (("root", P1, None, (), OLD, ""), ("ancestor", P1, "root", (), OLD, "S"),
                   ("bridge", P2, "ancestor", (), OLD, "S"), ("kid-live", P1, "bridge", (), OLD, "S"))
        nodes = (("kid-live", None, "pending"),)
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE, "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))
        self.assertEqual(done.archived, ())

    def test_a_child_below_a_bridge_in_another_project_whose_ancestor_has_a_pending_runless_node_is_not_selected(self):
        threads = (("root", P1, None, (), OLD, ""), ("ancestor-live", P1, "root", (), OLD, "S"),
                   ("bridge", P2, "ancestor-live", (), OLD, "S"), ("kid-old", P1, "bridge", (), OLD, "S"))
        nodes = (("ancestor-live", None, "pending"),)
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE, "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))
        self.assertEqual(done.archived, ())

    def test_an_ancestor_of_a_deleted_bridge_with_a_pending_runless_node_is_not_selected(self):
        threads = (("root", P1, None, (), OLD, ""), ("ancestor", P1, "root", (), OLD, "S"),
                   ("bridge", P1, "ancestor", (), OLD, "D"))
        nodes = (("bridge", None, "pending"),)
        world = self.world(threads=threads, nodes=nodes)
        done = world.run(ARCHIVE, "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))
        self.assertEqual(done.archived, ())

    def test_an_ancestor_of_a_child_with_a_running_run_is_not_selected_with_top_level_settled(self):
        threads = (("top", P1, None, (), OLD, "S"), ("mid", P1, "top", (), OLD, "S"),
                   ("kid", P1, "mid", ("running",), OLD, "S"))
        world = self.world(threads=threads)
        done = world.run(ARCHIVE, "--top-level-settled", "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))
        self.assertEqual(done.archived, ())

    def test_an_ancestor_above_a_deleted_bridge_whose_child_has_a_running_run_is_not_selected_with_top_level_settled(self):
        threads = (("top", P1, None, (), OLD, "S"), ("mid", P1, "top", (), OLD, "S"),
                   ("bridge", P1, "mid", (), OLD, "D"), ("kid", P1, "bridge", ("running",), OLD, "S"))
        world = self.world(threads=threads)
        done = world.run(ARCHIVE, "--top-level-settled", "--selection-only")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))
        self.assertEqual(done.archived, ())

    def test_a_finished_sibling_of_a_child_with_a_running_run_under_the_coordinator_is_still_selected(self):
        threads = (("coord", P1, None, ("running",), RECENT),
                   ("kid-live", P1, "coord", ("running",), OLD, "S"),
                   ("kid-done", P1, "coord", ("completed",), OLD, "S"))
        world = self.world(threads=threads)
        done = world.run(ARCHIVE, "--coordinator", "coord")
        self.assertEqual((done.returncode, done.stdout, done.stderr),
                         (0, "to archive: 1; skipped: live 2, recent 0, under a running thread 0, kept 0\n"
                          "archived 1, failed 0\n", ""))
        self.assertEqual(done.archived, ("kid-done",))

    def test_a_thread_whose_descendant_run_is_completed_is_selected_once_past_the_cutoff(self):
        threads = (("top", P1, None, (), RECENT, ""), ("mid", P1, "top", (), OLD, "S"),
                   ("kid", P1, "mid", ("completed",), OLD, "S"))
        world = self.world(threads=threads)
        done = world.run(ARCHIVE)
        self.assertEqual((done.returncode, done.stdout, done.stderr),
                         (0, "to archive: 2; skipped: live 0, recent 0, under a running thread 0, kept 0\n"
                          "archived 2, failed 0\n", ""))
        self.assertEqual(done.archived, ("mid", "kid"))

    def test_a_settled_thread_whose_run_is_interrupted_cancelled_failed_or_rolled_back_is_selected_when_a_child_of_that_run_is_pending_running_or_waiting(self):
        cases = (
            ("interrupted", "pending", "node"),
            ("interrupted", "pending", "subagent"),
            ("cancelled", "running", "node"),
            ("failed", "waiting", "subagent"),
            ("rolled_back", "pending", "node"),
            ("rolled_back", "running", "subagent"),
        )
        for run_status, child_status, kind in cases:
            with self.subTest(run_status=run_status, child_status=child_status, kind=kind):
                threads = (("worker", P1, None, (run_status,), OLD, "S"),
                           ("task", P1, "worker", (), OLD, "S"))
                if kind == "node":
                    nodes = (("task", "worker-run-0", child_status),)
                    subagents = ()
                else:
                    nodes = ()
                    subagents = (("worker", "worker-run-0", "task", "provider_native", child_status),)
                world = self.world(threads=threads, nodes=nodes, subagents=subagents)
                done = world.run(ARCHIVE, "--top-level-settled", "--selection-only")
                self.assertEqual((done.returncode, done.stderr), (0, ""))
                self.assertEqual(json.loads(done.stdout), ["task", "worker"])

    def test_a_settled_thread_whose_latest_run_is_running_or_preparing_is_not_selected_when_a_child_of_that_run_is_pending_or_waiting(self):
        cases = (
            ("running", "pending", "node"),
            ("preparing", "waiting", "subagent"),
        )
        for run_status, child_status, kind in cases:
            with self.subTest(run_status=run_status, child_status=child_status, kind=kind):
                threads = (("worker", P1, None, (run_status,), OLD, "S"),
                           ("task", P1, "worker", (), OLD, "S"))
                if kind == "node":
                    nodes = (("task", "worker-run-0", child_status),)
                    subagents = ()
                else:
                    nodes = ()
                    subagents = (("worker", "worker-run-0", "task", "provider_native", child_status),)
                world = self.world(threads=threads, nodes=nodes, subagents=subagents)
                done = world.run(ARCHIVE, "--top-level-settled", "--selection-only")
                self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "[]\n", ""))


