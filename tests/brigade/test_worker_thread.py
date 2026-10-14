from .support import (
    setUpModule,
    BrigadeTest,
    THREAD_A,
    THREAD_B,
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

    def test_a_thread_bound_after_send_back_stays_when_the_item_returns_to_in_progress(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.record("abc", "send-back", "--note", "the attempt")
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_B), "D1 sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.dish_fields("state", "thread"), ("in-progress", THREAD_B))

    def test_repeating_that_thread_on_the_in_progress_command_is_not_an_earlier_attempt(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.record("abc", "send-back", "--note", "the attempt")
        self.brigade("dish", "D1", "--thread", THREAD_B)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", THREAD_B), "D1 in-progress")
        self.assertEqual(self.dish_fields("thread"), (THREAD_B,))

    def test_one_call_with_state_and_a_new_thread_still_starts_the_fix_attempt(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.record("abc", "send-back", "--note", "the attempt")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", THREAD_B), "D1 in-progress")
        self.assertEqual(self.dish_fields("state", "thread"), ("in-progress", THREAD_B))

    def test_a_sent_back_item_that_did_not_bind_a_new_thread_still_clears_it(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.record("abc", "send-back", "--note", "the attempt")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "")

    def test_a_thread_bound_onto_an_empty_sent_back_cell_stays_for_the_restart(self):
        self.fired_bug_fix()
        self.record("abc", "send-back", "--note", "the attempt")
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_B), "D1 sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], THREAD_B)

    def test_a_thread_bound_after_dish_state_sent_back_stays_for_the_restart(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.brigade("dish", "D1", "--state", "sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--thread", THREAD_B), "D1 sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], THREAD_B)

    def test_a_thread_bound_on_a_queued_item_is_cleared_on_the_return_to_in_progress(self):
        self.fired_bug_fix()
        self.record("abc", "pass")
        self.brigade("dish", "D1", "--state", "queued")
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.dish_fields("thread"), ("",))

    def test_a_thread_bound_after_send_back_is_cleared_when_queued_comes_before_in_progress(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--thread", THREAD_A)
        self.record("abc", "pass")
        self.record("def", "send-back", "--note", "the attempt")
        self.brigade("dish", "D1", "--thread", THREAD_B)
        self.brigade("dish", "D1", "--state", "queued", "--sha", "abc")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "")
        self.assertIn(f"worker\tD1\tretired\t{THREAD_B}", (self.at / "log.tsv").read_text())
        self.assertEqual(
            self.brigade("dish", "D1", "--thread", THREAD_B, ok=False),
            f"brigade: thread {THREAD_B} is an earlier attempt of D1; a send-back launches a fresh worker",
        )
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "")
