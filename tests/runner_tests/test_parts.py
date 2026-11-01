if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

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

    def test_a_heavy_recorded_duration_puts_that_test_alone_in_its_part(self):
        # This starts no process: the split reads a durations file the test writes.
        self.write("test_quad.py", """
            import unittest

            class QuadTest(unittest.TestCase):
                def test_a(self):
                    pass

                def test_b(self):
                    pass

                def test_c(self):
                    pass

                def test_heavy(self):
                    pass
        """)
        plan = self.plan_of(self.tests)
        heavy = next(test.id for test in plan if test.id.endswith("test_heavy"))
        durations = self.proj / "durations.json"
        durations.write_text(json.dumps({"default_seconds": 1.0, "seconds": {heavy: 1000.0}}))
        first = [test.id for test in run_tests.tests_in_part(plan, 1, 2, durations_path=durations)]
        second = [test.id for test in run_tests.tests_in_part(plan, 2, 2, durations_path=durations)]
        self.assertEqual(first, [heavy])
        self.assertEqual(len(second), 3)
        self.assertNotIn(heavy, second)

    def test_a_broken_durations_file_still_covers_every_test_exactly_once(self):
        # This starts no process: the split reads files the test writes.
        lines = ["import unittest", "", "class NineTest(unittest.TestCase):"]
        for index in range(9):
            lines.append(f"    def test_{index:02d}(self):")
            lines.append("        pass")
        self.write("test_nine.py", "\n".join(lines) + "\n")
        plan = self.plan_of(self.tests)
        ids = [test.id for test in plan]
        good = ids[0]
        payloads = {
            "missing": None,
            "empty": "",
            "not_json": "not json",
            "list": "[1, 2]",
            "seconds_list": '{"seconds": []}',
            "slow_string": json.dumps({"seconds": {good: "slow"}}),
            "bad_default": json.dumps({"default_seconds": -1, "seconds": {good: 0}}),
        }
        for name, payload in payloads.items():
            path = self.proj / f"durations-{name}.json"
            if payload is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_text(payload)
            for count in range(1, 9):
                with self.subTest(payload=name, count=count):
                    parts = [run_tests.tests_in_part(plan, index, count, durations_path=path)
                             for index in range(1, count + 1)]
                    got = [test.id for part in parts for test in part]
                    self.assertEqual(sorted(got), sorted(ids))
                    self.assertEqual(len(got), len({test.seq for part in parts for test in part}))
                    self.assertTrue(all(parts))

    def test_the_split_does_not_depend_on_plan_order(self):
        # This starts no process: the split runs in memory.
        first = tuple(run_tests.Test(seq, f"mod.Case.test_{name}", "mod", "mod.Case")
                      for seq, name in enumerate("abcd"))
        second = tuple(run_tests.Test(seq, f"mod.Case.test_{name}", "mod", "mod.Case")
                       for seq, name in enumerate("dcba"))
        seconds = {"mod.Case.test_d": 100.0}
        for count in (1, 2, 3, 4):
            with self.subTest(count=count):
                by_id_first = {test.id: run_tests.part_of(first, count, seconds, 1.0)[test.seq] for test in first}
                by_id_second = {test.id: run_tests.part_of(second, count, seconds, 1.0)[test.seq] for test in second}
                self.assertEqual(by_id_first, by_id_second)

    def test_recording_durations_caps_the_worker_count_at_the_landing_slot_limit(self):
        import io

        seen = []
        original_record = run_tests.record_durations

        def fake_record(start, jobs, timeout, out):
            seen.append(jobs)
            return 0

        run_tests.record_durations = fake_record
        self.addCleanup(setattr, run_tests, "record_durations", original_record)
        saved_slot = os.environ.get("LAND_SLOT")
        saved_files = os.environ.get("LAND_SLOT_FILES")
        os.environ["LAND_SLOT"] = "worker"
        os.environ["LAND_SLOT_FILES"] = "worker-0"

        def restore_slot():
            if saved_slot is None:
                os.environ.pop("LAND_SLOT", None)
            else:
                os.environ["LAND_SLOT"] = saved_slot
            if saved_files is None:
                os.environ.pop("LAND_SLOT_FILES", None)
            else:
                os.environ["LAND_SLOT_FILES"] = saved_files

        self.addCleanup(restore_slot)
        previous = sys.stderr
        sink = io.StringIO()
        sys.stderr = sink
        self.addCleanup(setattr, sys, "stderr", previous)
        out = self.proj / "durations.json"
        self.assertEqual(run_tests.main(["-s", str(self.tests), "-j", "8", "--record-durations", str(out)]), 0)
        self.assertEqual(seen, [4])
        self.assertIn("lowered to 4", sink.getvalue())

    def test_record_durations_writes_seconds_for_each_test_it_measures(self):
        one = run_tests.Test(0, "test_slow.SlowTest.test_one", "test_slow", "test_slow.SlowTest")
        two = run_tests.Test(1, "test_slow.SlowTest.test_two", "test_slow", "test_slow.SlowTest")
        three = run_tests.Test(2, "test_slow.SlowTest.test_three", "test_slow", "test_slow.SlowTest")
        plan = (one, two, three)

        def start_line(seq):
            return json.dumps({"ev": "start", "seq": seq})

        def result_line(seq):
            return json.dumps({"ev": "result", "seq": seq, "status": "ok", "problems": []})

        batches = [
            [start_line(0)],
            [result_line(0)],
            [start_line(1)],
            [result_line(1)],
            [start_line(2), result_line(2)],
        ]
        clocks = [10.0, 10.25, 11.0, 11.5, 12.0]

        class FakeClock:
            def __init__(self, values):
                self.values = list(values)
                self.index = 0

            def monotonic(self):
                value = self.values[self.index]
                self.index += 1
                return value

        original_list_tests = run_tests.list_tests
        original_run = run_tests.run
        original_time = run_tests.time
        run_tests.list_tests = lambda start, scratch, timeout, pause: plan
        self.addCleanup(setattr, run_tests, "list_tests", original_list_tests)
        self.addCleanup(setattr, run_tests, "run", original_run)
        self.addCleanup(setattr, run_tests, "time", original_time)

        def fake_run(start, jobs, timeout, part=None, *, list_tests_fn=None):
            found = list_tests_fn(start, None, timeout, None)
            self.assertEqual(tuple(test.seq for test in found), (0, 1, 2))
            channel_path = self.proj / "channel.jsonl"
            channel = run_tests.Channel(channel_path)
            self.addCleanup(channel.close)
            handle = open(channel_path, "a")
            self.addCleanup(handle.close)
            for lines in batches:
                for line in lines:
                    handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                channel.read()
            return 0

        run_tests.run = fake_run
        run_tests.time = FakeClock(clocks)
        out = self.proj / "durations.json"
        self.assertEqual(run_tests.record_durations(Path(self.tests), 1, 30.0, out), 0)
        data = json.loads(out.read_text())
        self.assertEqual(data["seconds"], {one.id: 0.25, two.id: 0.5})
        self.assertNotIn(three.id, data["seconds"])
        reread, default = run_tests.read_durations(out)
        self.assertEqual(reread, data["seconds"])
        self.assertEqual(default, data["default_seconds"])
        self.assertGreater(default, 0)

    def test_record_durations_writes_a_readable_file_and_exits_with_the_suite_status(self):
        self.write("test_slow.py", """
            import unittest

            class SlowTest(unittest.TestCase):
                def test_one(self):
                    pass

                def test_two(self):
                    pass
        """)
        out = self.proj / "durations.json"
        result = self.runner("--record-durations", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(out.read_text())
        self.assertGreater(data["default_seconds"], 0)
        self.assertIsInstance(data["seconds"], dict)
        reread, default = run_tests.read_durations(out)
        self.assertEqual(reread, data["seconds"])
        self.assertEqual(default, data["default_seconds"])
        self.write("test_slow.py", """
            import unittest

            class SlowTest(unittest.TestCase):
                def test_one(self):
                    self.assertEqual(1, 2)
        """)
        failed = self.runner("--record-durations", str(out))
        self.assertEqual(failed.returncode, 1, failed.stderr)
        data = json.loads(out.read_text())
        self.assertGreater(data["default_seconds"], 0)
        self.assertIsInstance(data["seconds"], dict)

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

