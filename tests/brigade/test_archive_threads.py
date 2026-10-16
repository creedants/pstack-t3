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
