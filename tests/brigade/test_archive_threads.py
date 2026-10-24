if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import types

from .support import (
    setUpModule,
    BrigadeTest,
    CLAUDE,
    CODEX,
    SCRIPT,
    THREAD_A,
    THREAD_B,
    THREAD_D,
    _owner_words,
    call,
    call_in_process,
    run_globals,
)


class ArchiveThreadsTest(BrigadeTest):
    def archived(self, *args):
        """Every line the command wrote to standard error."""
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                      *_owner_words(self.at), *(str(arg) for arg in args)])
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        return outcome.stderr_text().splitlines()

    def record_send_back(self, *extra):
        return self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                            "--author", CLAUDE, "--verifier", CODEX, *extra)

    def record_pass(self, *extra):
        return self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass",
                            "--author", CLAUDE, "--verifier", CODEX, *extra)

    def test_pass_record_send_back_with_reviewer_thread_prints_only_that_thread(self):
        self.fired_bug_fix()
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_A),
                         [f"archive thread: {THREAD_A}"])

    def test_pass_record_pass_with_reviewer_thread_prints_only_that_thread(self):
        self.fired_bug_fix()
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "abc", "--verdict", "pass",
                                       "--author", CLAUDE, "--verifier", CODEX,
                                       "--reviewer-thread", THREAD_A),
                         [f"archive thread: {THREAD_A}"])

    def test_pass_record_without_reviewer_thread_prints_nothing(self):
        self.fired_bug_fix()
        self.assertEqual(self.archived("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back",
                                       "--author", CLAUDE, "--verifier", CODEX), [])

    def test_fix_round_thread_prints_the_replaced_worker_only(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.record_send_back()
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.archived("dish", "D1", "--thread", THREAD_B),
                         [f"archive thread: {THREAD_A}"])

    def test_first_dish_thread_on_a_fired_item_prints_nothing(self):
        self.fired_bug_fix()
        self.assertEqual(self.archived("dish", "D1", "--thread", THREAD_A), [])

    def test_repeated_dish_thread_with_the_same_id_prints_nothing(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.assertEqual(self.archived("dish", "D1", "--thread", THREAD_A), [])

    def test_merged_dish_prints_every_worker_thread_in_order(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.record_send_back()
        self.brigade("dish", "D1", "--state", "in-progress")
        self.brigade("dish", "D1", "--thread", THREAD_B)
        self.record_pass()
        self.assertEqual(self.archived("dish", "D1", "--state", "merged"),
                         [f"archive thread: {THREAD_A}", f"archive thread: {THREAD_B}"])

    def test_dropped_dish_prints_only_its_own_workers(self):
        self.fired_bug_fix()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "t2")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Fix t2")
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.brigade("dish", "D2", "--thread", THREAD_B)
        lines = self.archived("dish", "D1", "--state", "dropped")
        self.assertEqual(lines, [f"archive thread: {THREAD_A}"])
        self.assertNotIn(f"archive thread: {THREAD_B}", lines)

    def test_dropped_dish_omits_a_thread_another_item_in_flight_holds(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.record_send_back()
        self.brigade("dish", "D1", "--state", "in-progress")
        self.brigade("dish", "D1", "--thread", THREAD_B)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "t2")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Fix t2")
        self.brigade("dish", "D2", "--thread", THREAD_B)
        lines = self.archived("dish", "D1", "--state", "dropped")
        self.assertIn(f"archive thread: {THREAD_A}", lines)
        self.assertNotIn(f"archive thread: {THREAD_B}", lines)

    def test_dropped_dish_never_prints_the_coordinator_thread(self):
        self.fired_bug_fix()
        self.brigade("set", "--thread", THREAD_D)
        self.brigade("dish", "D1", "--thread", THREAD_D)
        self.assertEqual([row[4] for row in self.thread_rows()], [THREAD_D])
        self.assertEqual(self.archived("dish", "D1", "--state", "dropped"), [])

    def test_pass_record_reviewer_thread_equal_to_current_worker_prints_nothing(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                      *_owner_words(self.at), "pass", "record", "D1", "--sha", "abc",
                                      "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX,
                                      "--reviewer-thread", THREAD_A])
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stderr_text().splitlines(), [])
        self.assertEqual(outcome.stdout_text().strip(), "D1 sent-back")
        self.assertEqual(len(self.pass_rows()), 1)

    def test_late_pass_record_reviewer_thread_equal_to_current_worker_prints_nothing_and_keeps_worker_open(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                      *_owner_words(self.at), "pass", "record", "D1", "--sha", "abc",
                                      "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX,
                                      "--reviewer-thread", THREAD_A, "--late"])
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stderr_text().splitlines(), [])
        self.assertEqual(outcome.stdout_text().strip(), "D1: late send-back on record; D1 stays in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], THREAD_A)
        rows = self.thread_rows()
        self.assertEqual(len(rows), 2)
        worker = [row for row in rows if row[4] == THREAD_A and row[3] in ("worker", "fix worker")][0]
        self.assertEqual(worker[7], "")

    def test_dropped_dish_omits_replacement_thread_bound_during_lease_release(self):
        # Covers a replacement bound during the lease release gap.
        self.started()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        glob = run_globals(self, SCRIPT)
        original = glob["land_result"]

        def stubbed(project_root, *args):
            if args[:2] == ("lease", "release"):
                inner = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                       *_owner_words(self.at), "dish", "D1",
                                                       "--thread", THREAD_B])
                self.assertEqual(inner.code, 0, inner.stdout_text() + inner.stderr_text())
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            return original(project_root, *args)

        glob["land_result"] = stubbed
        try:
            outcome = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                     *_owner_words(self.at), "dish", "D1",
                                                     "--state", "dropped", "--stopped", "run-A"])
        finally:
            glob["land_result"] = original
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        lines = outcome.stderr_text().splitlines()
        self.assertIn(f"archive thread: {THREAD_A}", lines)
        self.assertNotIn(f"archive thread: {THREAD_B}", lines)
        self.assertEqual(lines, [f"archive thread: {THREAD_A}"])
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "dropped")
        closed = [row[7] for row in self.thread_rows()]
        self.assertTrue(closed)
        for end in closed:
            self.assertEqual(end, "stamp")

    def test_dropped_dish_omits_a_reused_worker_id_bound_during_lease_release(self):
        # Covers a reused id bound during the lease release: the rebind is not announced.
        self.started()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.brigade("dish", "D1", "--thread", "")
        self.brigade("dish", "D1", "--thread", THREAD_B)
        glob = run_globals(self, SCRIPT)
        original = glob["land_result"]

        def stubbed(project_root, *args):
            if args[:2] == ("lease", "release"):
                inner = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                       *_owner_words(self.at), "dish", "D1",
                                                       "--thread", THREAD_A])
                self.assertEqual(inner.code, 0, inner.stdout_text() + inner.stderr_text())
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            return original(project_root, *args)

        glob["land_result"] = stubbed
        try:
            outcome = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                     *_owner_words(self.at), "dish", "D1",
                                                     "--state", "dropped", "--stopped", "run-B"])
        finally:
            glob["land_result"] = original
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stderr_text().splitlines(), [f"archive thread: {THREAD_B}"])
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "dropped")

    def test_dropped_dish_omits_a_reused_worker_id_rebound_then_cleared_during_lease_release(self):
        # Covers a reused id whose rebound row outlives a cleared dish thread cell during the lease release.
        self.started()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.brigade("dish", "D1", "--thread", "")
        self.brigade("dish", "D1", "--thread", THREAD_B)
        self.brigade("dish", "D1", "--thread", "")
        glob = run_globals(self, SCRIPT)
        original = glob["land_result"]

        def stubbed(project_root, *args):
            if args[:2] == ("lease", "release"):
                for inner_args in (["dish", "D1", "--thread", THREAD_A], ["dish", "D1", "--thread", ""]):
                    inner = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                           *_owner_words(self.at), *inner_args])
                    self.assertEqual(inner.code, 0, inner.stdout_text() + inner.stderr_text())
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            return original(project_root, *args)

        glob["land_result"] = stubbed
        try:
            outcome = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                     *_owner_words(self.at), "dish", "D1",
                                                     "--state", "dropped", "--stopped", "run-B"])
        finally:
            glob["land_result"] = original
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stderr_text().splitlines(), [f"archive thread: {THREAD_B}"])
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "dropped")

    def test_dropped_dish_omits_a_cleared_thread_rebound_during_lease_release(self):
        self.started()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.brigade("dish", "D1", "--thread", "")
        glob = run_globals(self, SCRIPT)
        original = glob["land_result"]

        def stubbed(project_root, *args):
            if args[:2] == ("lease", "release"):
                inner = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                       *_owner_words(self.at), "dish", "D1",
                                                       "--thread", THREAD_A])
                self.assertEqual(inner.code, 0, inner.stdout_text() + inner.stderr_text())
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")
            return original(project_root, *args)

        glob["land_result"] = stubbed
        try:
            outcome = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                     *_owner_words(self.at), "dish", "D1",
                                                     "--state", "dropped", "--stopped", "run-A"])
        finally:
            glob["land_result"] = original
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stderr_text().splitlines(), [])
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "dropped")
