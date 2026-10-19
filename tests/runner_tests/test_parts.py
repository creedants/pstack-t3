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


class PartsMixin:
    def plan_of(self, start):
        saved = sys.path[:]
        try:
            found = run_tests.discover(start)
        finally:
            sys.path[:] = saved
        return tuple(run_tests.Test(seq, test.id(), type(test).__module__, run_tests.class_name(test))
                     for seq, test in enumerate(found))

    def test_parts_cover_a_long_module_and_a_one_test_module_without_putting_the_one_test_in_part_1(self):
        lines = ["import unittest", "", "class LongTest(unittest.TestCase):"]
        for index in range(11):
            lines.append(f"    def test_{index:02d}(self):")
            lines.append("        pass")
        self.write("test_long.py", "\n".join(lines) + "\n")
        self.write("test_one.py", """
            import unittest

            class OneTest(unittest.TestCase):
                def test_only(self):
                    pass
        """)
        plan = self.plan_of(self.tests)
        ids = [test.id for test in plan]
        for count in range(1, 9):
            parts = [run_tests.tests_in_part(plan, index, count) for index in range(1, count + 1)]
            got = [test.id for part in parts for test in part]
            self.assertEqual(sorted(got), sorted(ids))
            self.assertEqual(len(got), len(set(test.seq for part in parts for test in part)))
        fourth = [
            ["test_long.LongTest.test_00", "test_long.LongTest.test_04", "test_long.LongTest.test_08"],
            ["test_long.LongTest.test_01", "test_long.LongTest.test_05", "test_long.LongTest.test_09"],
            ["test_long.LongTest.test_02", "test_long.LongTest.test_06", "test_long.LongTest.test_10"],
            ["test_long.LongTest.test_03", "test_long.LongTest.test_07", "test_one.OneTest.test_only"],
        ]
        self.assertEqual([[test.id for test in run_tests.tests_in_part(plan, index, 4)] for index in range(1, 5)], fourth)
        repo = self.plan_of(run_tests.ROOT / "tests")
        repo_ids = [test.id for test in repo]
        for count in range(1, 9):
            parts = [run_tests.tests_in_part(repo, index, count) for index in range(1, count + 1)]
            got = [test.id for part in parts for test in part]
            self.assertEqual(sorted(got), sorted(repo_ids))
            self.assertEqual(len(got), len(repo))
            self.assertTrue(all(parts))

    def test_part_flag_runs_one_test_of_a_pair_and_refuses_a_part_outside_the_plan(self):
        for text, message in (("2/1", "2/1 has i outside 1 to n"),
                              ("1/0", "1/0 has n below 1"),
                              ("no", "no is not i/n")):
            result = self.runner("--part", text)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn(message, result.stderr)
        self.write("test_pair.py", """
            import unittest

            class PairTest(unittest.TestCase):
                def test_a(self):
                    pass

                def test_b(self):
                    pass
        """)
        plan = self.plan_of(self.tests)
        self.assertEqual([test.id for test in run_tests.tests_in_part(plan, 1, 2)], ["test_pair.PairTest.test_a"])
        self.assertEqual([test.id for test in run_tests.tests_in_part(plan, 2, 2)], ["test_pair.PairTest.test_b"])
        first = self.runner("--part", "1/2")
        second = self.runner("--part", "2/2")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertRegex(first.stderr, r"Ran 1 test")
        self.assertRegex(second.stderr, r"Ran 1 test")
        outside = self.runner("--part", "3/2")
        self.assertEqual(outside.returncode, 2, outside.stderr)
        self.assertIn("3/2 has i outside 1 to n", outside.stderr)
        self.assertNotIn("Ran ", outside.stderr)
        self.write("test_pair.py", """
            import unittest

            class PairTest(unittest.TestCase):
                def test_only(self):
                    pass
        """)
        empty = self.runner("--part", "2/2")
        self.assertEqual(empty.returncode, 2, empty.stderr)
        self.assertIn("part 2/2 has no test", empty.stderr)


class BuildShardsMixin:
    def test_every_seq_is_in_exactly_one_shard_and_each_shard_holds_1_to_20_seqs_of_one_module_in_ascending_order(self):
        for sizes in ([1], [10], [11], [25, 3], [100, 1, 10, 7, 31]):
            with self.subTest(sizes=sizes):
                # Round-robin, so the tests of different modules interleave in the plan while each has tests left.
                modules = [f"m{index}" for turn in range(max(sizes)) for index, size in enumerate(sizes) if turn < size]
                plan = tuple(run_tests.Test(seq, "one.id", module, f"{module}.C{seq}") for seq, module in enumerate(modules))
                shards = run_tests.build_shards(plan)
                self.assertEqual(sorted(seq for shard in shards for seq in shard.seqs), list(range(len(plan))))
                self.assertEqual([shard.number for shard in shards], list(range(1, len(shards) + 1)))
                for shard in shards:
                    self.assertIn(len(shard.seqs), range(1, 21))
                    self.assertEqual({plan[seq].module for seq in shard.seqs}, {shard.module})
                    self.assertEqual(list(shard.seqs), sorted(shard.seqs))
                    self.assertEqual(shard.classes, tuple(plan[seq].cls for seq in shard.seqs))

    def test_no_test_makes_no_shard(self):
        self.assertEqual(run_tests.build_shards(()), [])

    def test_a_part_queue_starts_one_test_shards_before_larger_shards_and_a_full_queue_keeps_build_order(self):
        plan = []
        seq = 0
        for module, count in (("big", 21), ("early", 1), ("late", 1)):
            for _ in range(count):
                plan.append(run_tests.Test(seq, module + ".id", module, module + ".C"))
                seq += 1
        shards = run_tests.build_shards(tuple(plan))
        self.assertEqual([shard.module for shard in shards], ["big", "big", "early", "late"])
        self.assertEqual(run_tests.launch_order(shards, False), tuple(shards))
        front = run_tests.launch_order(shards, True)
        self.assertEqual([shard.number for shard in front], [3, 4, 1, 2])
        self.assertEqual([len(shard.seqs) for shard in front], [1, 1, 11, 10])

    def test_a_part_run_passes_one_test_shards_to_the_pool_ahead_of_larger_shards(self):
        plan = []
        seq = 0
        for module, count in (("big", 21), ("early", 1), ("late", 1)):
            for _ in range(count):
                plan.append(run_tests.Test(seq, module + ".id", module, module + ".C"))
                seq += 1
        received = []

        def list_tests(start, scratch, timeout, pause):
            return tuple(plan)

        def run_pool(shards, jobs, timeout, launch_one, finish, tick, pause):
            received.append(tuple(shards))

        class Still:
            inherited = False

            def pause(self, owned):
                return None

            def sweep(self):
                return []

        # list_tests starts a child. Watch replaces SIGINT and SIGTERM. When it is the child subreaper, its sweep kills processes below this one.
        originals = (run_tests.list_tests, run_tests.run_pool, run_tests.Watch)
        run_tests.list_tests = list_tests
        run_tests.run_pool = run_pool
        run_tests.Watch = Still
        self.addCleanup(setattr, run_tests, "list_tests", originals[0])
        self.addCleanup(setattr, run_tests, "run_pool", originals[1])
        self.addCleanup(setattr, run_tests, "Watch", originals[2])
        sink = open(os.devnull, "w")
        self.addCleanup(sink.close)
        previous = sys.stderr
        sys.stderr = sink
        self.addCleanup(setattr, sys, "stderr", previous)
        with tempfile.TemporaryDirectory() as directory:
            run_tests.run(Path(directory), 1, 30, (1, 1))
        self.assertEqual(
            [(shard.number, len(shard.seqs)) for batch in received for shard in batch],
            [(3, 1), (4, 1), (1, 11), (2, 10)],
        )

