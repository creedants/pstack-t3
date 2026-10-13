from .support import (
    setUpModule,
    BrigadeTest,
    THREAD_A,
)


class WorkerThreadTest(BrigadeTest):
    def test_a_padded_dish_thread_of_the_current_worker_is_not_retired_and_the_next_unpadded_command_is_accepted(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        before = (self.at / "log.tsv").read_bytes()
        self.assertEqual(self.brigade("dish", "D1", "--thread", f" {THREAD_A} "), "D1 in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], THREAD_A)
        self.assertEqual((self.at / "log.tsv").read_bytes(), before)
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_A), "D1 in-progress")

    def test_a_whitespace_only_dish_thread_still_retires_the_current_worker(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.assertEqual(self.brigade("dish", "D1", "--thread", "   "), "D1 in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "")
        self.assertIn(f"worker\tD1\tretired\t{THREAD_A}", (self.at / "log.tsv").read_text())
