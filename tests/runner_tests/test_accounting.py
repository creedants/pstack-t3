from .support import (
    AT_EXIT,
    DURING_THE_TEST,
    ESCAPE,
    GOOD,
    HANG_AT_IMPORT,
    INHERITED,
    LAUNCHER,
    LAUNCHER_CHILD,
    LINUX,
    MARKS,
    NEEDS_LINUX,
    NEVER_AN_EVENT,
    PASSING,
    Path,
    RAN,
    RELEASES_THE_PARENT,
    RESULT_FILE,
    ROOT,
    RUNNER,
    WRITES_TO_ITS_RESULT_FILE,
    json,
    os,
    re,
    run_tests,
    setUpModule,
    signal,
    subprocess,
    sys,
    tempfile,
    textwrap,
    time,
    unittest,
)


class AccountMixin:
    OK = {"ev": "result", "status": "ok", "problems": []}
    SHARD = run_tests.Shard(2, "test_x", (4, 5), ("test_x.A", "test_x.B"))
    ONE = run_tests.Shard(2, "test_x", (4,), ("test_x.A",))
    SKIPPED_A = {"ev": "skipped", "method": "setUpClass", "name": "test_x.A"}
    SKIPPED_B = {"ev": "skipped", "method": "setUpClass", "name": "test_x.B"}
    UNSUPPORTED = ("reported a test as behind a skipped fixture with no earlier report that the setUpClass of its class "
                   "or the setUpModule of its module raised SkipTest")
    def test_a_seq_with_no_event_from_a_worker_that_finished_is_lost(self):
        events = [{"ev": "start", "seq": 4}, {**self.OK, "seq": 4}, {"ev": "done"}]
        accounting = run_tests.account(self.SHARD, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "ok")
        self.assertEqual(accounting.verdicts[5].kind, "lost")
        self.assertEqual(accounting.verdicts[5].cause,
                         "The worker for shard 2 finished without an accepted result for this test.")
        self.assertEqual(accounting.violations, ())
        self.assertEqual(accounting.ending, "")

    def test_a_seq_behind_a_fixture_that_skipped_is_skipped_and_the_seq_with_no_event_beside_it_is_lost(self):
        events = [self.SKIPPED_A, {"ev": "behind", "seq": 4, "skip": True}, {"ev": "done"}]
        accounting = run_tests.account(self.SHARD, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "skip")
        self.assertEqual(accounting.verdicts[5].kind, "lost")
        self.assertEqual(accounting.violations, ())

    def test_a_seq_reported_behind_a_skipped_fixture_is_skipped_after_a_skipped_event_for_its_class_or_module_and_else_a_violation_and_lost(self):
        behind = {"ev": "behind", "seq": 5, "skip": True}
        module = {"ev": "skipped", "method": "setUpModule", "name": "test_x"}
        cases = {
            "no skipped event": ([behind], "lost"),
            "the setUpClass of another class": ([self.SKIPPED_A, behind], "lost"),
            "the setUpModule of another module": ([{**module, "name": "test_y"}, behind], "lost"),
            "a setUpModule with the name of its class": ([{**module, "name": "test_x.B"}, behind], "lost"),
            "a setUpClass with the name of its module": ([{**self.SKIPPED_B, "name": "test_x"}, behind], "lost"),
            "the skipped event after it": ([behind, self.SKIPPED_B], "lost"),
            "the setUpClass of its class": ([self.SKIPPED_B, behind], "skip"),
            "the setUpModule of its module": ([module, behind], "skip"),
        }
        for case, (events, kind) in cases.items():
            with self.subTest(case=case):
                accounting = run_tests.account(self.SHARD, [*events, {"ev": "done"}], run_tests.Ended(0))
                self.assertEqual(accounting.verdicts[5].kind, kind)
                self.assertEqual(accounting.violations, ((5, self.UNSUPPORTED),) if kind == "lost" else ())

    def test_a_start_and_a_result_after_a_refused_behind_event_give_the_seq_that_result(self):
        events = [{"ev": "behind", "seq": 4, "skip": True}, {"ev": "start", "seq": 4}, {**self.OK, "seq": 4}, {"ev": "done"}]
        accounting = run_tests.account(self.ONE, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "ok")
        self.assertEqual(accounting.violations, ((4, self.UNSUPPORTED),))

    def test_a_seq_with_no_result_when_the_runner_stopped_reading_is_lost_with_that_cause_and_a_seq_with_a_result_keeps_it(self):
        events = [{"ev": "start", "seq": 4}, {**self.OK, "seq": 4}, {"ev": "start", "seq": 5}]
        accounting = run_tests.account(self.SHARD, events, run_tests.Ended(-9), stopped=True)
        self.assertEqual(accounting.verdicts[4].kind, "ok")
        self.assertEqual(accounting.verdicts[5].kind, "lost")
        self.assertEqual(accounting.verdicts[5].cause,
                         "The runner stopped reading the result file of shard 2 before it read a result for this test.")
        self.assertEqual(accounting.ending,
                         "The worker for shard 2 was killed by signal 9 before the runner read that it had finished.")

    def test_a_worker_the_runner_stopped_reading_after_its_done_event_has_no_ending_with_status_0_and_an_ending_when_killed(self):
        events = [{"ev": "start", "seq": 4}, {**self.OK, "seq": 4}, {"ev": "done"}]
        self.assertEqual(run_tests.account(self.ONE, events, run_tests.Ended(0), stopped=True).ending, "")
        self.assertEqual(run_tests.account(self.ONE, events, run_tests.Ended(-9), stopped=True).ending,
                         "The worker for shard 2 was killed by signal 9 after it reported that it had finished.")

    def test_a_seq_behind_a_fixture_that_failed_is_lost_with_the_fixture_cause(self):
        events = [{"ev": "fixture", "label": "setUpClass (test_x.A)", "traceback": "RuntimeError\n"},
                  {"ev": "behind", "seq": 4, "skip": False}, self.SKIPPED_B, {"ev": "behind", "seq": 5, "skip": True},
                  {"ev": "done"}]
        accounting = run_tests.account(self.SHARD, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "lost")
        self.assertEqual(accounting.verdicts[4].cause,
                         "A class or module fixture failed in the worker for shard 2, and this test did not start.")
        self.assertEqual(accounting.verdicts[5].kind, "skip")
        self.assertEqual(accounting.fixtures, (run_tests.Problem("error", "setUpClass (test_x.A)", "RuntimeError\n"),))

    def test_a_result_for_a_seq_the_shard_was_not_given_is_a_violation_and_no_verdict(self):
        events = [{"ev": "start", "seq": 4}, {**self.OK, "seq": 4}, {"ev": "start", "seq": 9}, {**self.OK, "seq": 9},
                  {"ev": "done"}]
        accounting = run_tests.account(self.ONE, events, run_tests.Ended(0))
        self.assertEqual(sorted(accounting.verdicts), [4])
        self.assertEqual(accounting.verdicts[4].kind, "ok")
        self.assertEqual(accounting.violations, ((9, "reported a test it was not given"),))

    def test_a_second_result_for_one_seq_is_a_violation_and_the_first_result_stands(self):
        bad = {"ev": "result", "seq": 4, "status": "bad",
               "problems": [{"kind": "fail", "label": "test_a (test_x.T)", "traceback": "AssertionError\n"}]}
        events = [{"ev": "start", "seq": 4}, bad, {**self.OK, "seq": 4}, {"ev": "done"}]
        accounting = run_tests.account(self.ONE, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "bad")
        self.assertEqual(accounting.verdicts[4].problems[0].traceback, "AssertionError\n")
        self.assertEqual(accounting.violations, ((4, "reported a second result for one test"),))

    def test_a_result_for_a_seq_with_no_start_is_a_violation_and_the_seq_is_lost(self):
        events = [{**self.OK, "seq": 4}, {"ev": "start", "seq": 5}, {**self.OK, "seq": 5}, {"ev": "done"}]
        accounting = run_tests.account(self.SHARD, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "lost")
        self.assertEqual(accounting.verdicts[5].kind, "ok")
        self.assertEqual(accounting.violations, ((4, "reported a result for a test it had not started"),))

    def test_a_start_for_a_seq_behind_a_fixture_is_a_violation_and_the_seq_stays_skipped(self):
        events = [self.SKIPPED_A, {"ev": "behind", "seq": 4, "skip": True}, {"ev": "start", "seq": 4}, {"ev": "done"}]
        accounting = run_tests.account(self.ONE, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "skip")
        self.assertEqual(accounting.violations, ((4, "started a test it had already reported"),))

    def test_a_result_after_the_done_event_is_a_violation_and_the_seq_is_lost(self):
        events = [{"ev": "done"}, {"ev": "start", "seq": 4}, {**self.OK, "seq": 4}]
        accounting = run_tests.account(self.ONE, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "lost")
        self.assertEqual(accounting.violations, ((4, "wrote an event after it reported that it had finished"),))

    def test_the_ending_is_empty_only_for_a_done_event_with_status_0_inside_the_limit(self):
        done = [{"ev": "start", "seq": 4}, {**self.OK, "seq": 4}, {"ev": "done"}]
        shard = self.ONE
        endings = {
            (True, 0, None): "",
            (True, 3, None): "The worker for shard 2 exited with status 3 after it reported that it had finished.",
            (True, -9, None): "The worker for shard 2 was killed by signal 9 after it reported that it had finished.",
            (True, -9, 2.0): "The worker for shard 2 exceeded the limit of 2 s after it reported that it had finished.",
            (True, 0, 2.0): "The worker for shard 2 exceeded the limit of 2 s after it reported that it had finished.",
            (False, 0, None): "The worker for shard 2 exited with status 0 before it reported that it had finished.",
            (False, 3, None): "The worker for shard 2 exited with status 3 before it reported that it had finished.",
            (False, -9, None): "The worker for shard 2 was killed by signal 9 before it reported that it had finished.",
            (False, -9, 2.0): "The worker for shard 2 exceeded the limit of 2 s before it reported that it had finished.",
            (False, 0, 2.0): "The worker for shard 2 exceeded the limit of 2 s before it reported that it had finished.",
        }
        for (finished, status, over), ending in endings.items():
            with self.subTest(finished=finished, status=status, over=over):
                accounting = run_tests.account(shard, done if finished else done[:2], run_tests.Ended(status, over))
                self.assertEqual(accounting.ending, ending)
                self.assertEqual(accounting.verdicts[4].kind, "ok")


class ParseEventMixin:
    def test_each_kind_of_event_parses_to_its_fields(self):
        events = [
            {"ev": "start", "seq": 3},
            {"ev": "result", "seq": 3, "status": "ok", "problems": []},
            {"ev": "result", "seq": 3, "status": "bad", "problems": [{"kind": "fail", "label": "a", "traceback": "b"}]},
            {"ev": "behind", "seq": 3, "skip": True},
            {"ev": "skipped", "method": "setUpClass", "name": "a.B"},
            {"ev": "fixture", "label": "setUpClass (a.B)", "traceback": "c"},
            {"ev": "done"},
        ]
        for event in events:
            with self.subTest(event=event):
                self.assertEqual(run_tests.parse_event(json.dumps(event).encode()), event)

    def test_a_line_that_is_no_event_raises_valueerror(self):
        lines = [
            b"", b"garbage", b"\xff\xfe", b"[" * 100000, b"7", b"[1]", b"{}",
            b'{"ev": ["start"]}', b'{"ev": "nope"}',
            b'{"ev": "start"}', b'{"ev": "start", "seq": "3"}', b'{"ev": "start", "seq": 3.0}',
            b'{"ev": "start", "seq": true}', b'{"ev": "start", "seq": 3, "more": 1}',
            b'{"ev": "done", "seq": 3}',
            b'{"ev": "behind", "seq": 3, "skip": 1}',
            b'{"ev": "skipped", "method": "setUpClass"}', b'{"ev": "skipped", "method": "setUpClass", "name": 3}',
            b'{"ev": "fixture", "label": "a"}',
            b'{"ev": "result", "seq": 3, "status": "lost", "problems": []}',
            b'{"ev": "result", "seq": 3, "status": "bad", "problems": []}',
            b'{"ev": "result", "seq": 3, "status": "ok", "problems": [{"kind": "fail", "label": "a", "traceback": "b"}]}',
            b'{"ev": "result", "seq": 3, "status": "bad", "problems": [{"kind": "lost", "label": "a", "traceback": "b"}]}',
            b'{"ev": "result", "seq": 3, "status": "bad", "problems": ["a"]}',
        ]
        for line in lines:
            with self.subTest(line=line[:60]):
                with self.assertRaises(ValueError):
                    run_tests.parse_event(line)

