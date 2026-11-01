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

    def test_grouped_parts_cover_every_test_exactly_once_rather_than_scattering_one_module(self):
        for name in ("test_alpha", "test_beta", "test_gamma", "test_delta",
                     "test_eps", "test_zeta", "test_eta", "test_theta", "test_iota"):
            lines = ["import unittest", "", f"class {name.title()}Test(unittest.TestCase):"]
            for index in range(2):
                lines.append(f"    def test_{index}(self):")
                lines.append("        pass")
            self.write(f"{name}.py", "\n".join(lines) + "\n")
        plan = self.plan_of(self.tests)
        ids = [test.id for test in plan]
        for count in range(1, 9):
            parts = [run_tests.tests_in_part(plan, index, count) for index in range(1, count + 1)]
            got = [test.id for part in parts for test in part]
            self.assertEqual(sorted(got), sorted(ids))
            self.assertEqual(len(got), len(set(test.seq for part in parts for test in part)))
        pair = [run_tests.tests_in_part(plan, index, 2) for index in range(1, 3)]
        for part in pair:
            by_module: dict[str, list] = {}
            for test in part:
                by_module.setdefault(test.module, []).append(test)
            for module, tests in by_module.items():
                self.assertEqual(len(tests), 2)
        repo = self.plan_of(run_tests.ROOT / "tests")
        repo_ids = [test.id for test in repo]
        for count in range(1, 9):
            parts = [run_tests.tests_in_part(repo, index, count) for index in range(1, count + 1)]
            got = [test.id for part in parts for test in part]
            self.assertEqual(sorted(got), sorted(repo_ids))
            self.assertEqual(len(got), len(repo))

    def test_every_test_of_one_module_lands_in_one_part_including_a_module_larger_than_a_shard(self):
        # This starts no process: the split runs in memory.
        plan = []
        seq = 0
        for module, count in (("big", run_tests.SHARD_SIZE + 5), ("giant", 200), ("giant2", 200)):
            for index in range(count):
                plan.append(run_tests.Test(seq, f"{module}.Case.test_{index}", module, f"{module}.Case"))
                seq += 1
        plan = tuple(plan)
        costs = run_tests.Costs(
            {
                "big": run_tests.ModuleCost(tests=run_tests.SHARD_SIZE + 5, seconds=0.25, overhead=0.1),
                "giant": run_tests.ModuleCost(tests=200, seconds=2000.0, overhead=1.0),
                "giant2": run_tests.ModuleCost(tests=200, seconds=2000.0, overhead=1.0),
            },
            default_seconds=1.0,
            default_overhead=1.0,
        )
        for count in (2, 3):
            assignment = run_tests.part_of(plan, count, costs)
            big_parts = {assignment[test.seq] for test in plan if test.module == "big"}
            self.assertEqual(len(big_parts), 1)

    def test_a_heavy_module_above_one_parts_worker_share_splits_per_test_while_a_light_one_stays_whole(self):
        # This starts no process: the split runs in memory.
        plan = []
        seq = 0
        for module, count in (("heavy", 4), ("mid", 4), ("small", 4), ("tiny", 4)):
            for index in range(count):
                plan.append(run_tests.Test(seq, f"{module}.Case.test_{index}", module, f"{module}.Case"))
                seq += 1
        plan = tuple(plan)
        costs = run_tests.Costs(
            {
                "heavy": run_tests.ModuleCost(tests=4, seconds=400.0, overhead=1.0),
                "mid": run_tests.ModuleCost(tests=4, seconds=4.0, overhead=1.0),
                "small": run_tests.ModuleCost(tests=4, seconds=4.0, overhead=1.0),
                "tiny": run_tests.ModuleCost(tests=4, seconds=4.0, overhead=1.0),
            },
            default_seconds=1.0,
            default_overhead=1.0,
        )
        assignment = run_tests.part_of(plan, 2, costs)
        heavy_parts = {assignment[test.seq] for test in plan if test.module == "heavy"}
        self.assertGreater(len(heavy_parts), 1)
        for module in ("mid", "small", "tiny"):
            self.assertEqual(len({assignment[test.seq] for test in plan if test.module == module}), 1)

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
            "modules_list": json.dumps({"modules": []}),
            "modules_entry_string": json.dumps({"modules": {"mod": "slow"}}),
            "modules_short": json.dumps({"modules": {"mod": [1.0]}}),
            "modules_bad_rate": json.dumps({"modules": {"mod": [-1.0, 1.0]}}),
            "modules_bad_overhead": json.dumps({"modules": {"mod": [1.0, "slow"]}}),
            "modules_bad_defaults": json.dumps({"default_seconds": "slow", "default_overhead": -2,
                                                "modules": {"test_nine": [1.0, 1.0]}}),
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

    def test_a_malformed_module_entry_falls_back_to_defaults_while_valid_modules_still_record(self):
        # This starts no process: the costs are read from a file the test writes.
        path = self.proj / "durations.json"
        path.write_text(json.dumps({
            "default_seconds": 2.0,
            "default_overhead": 3.0,
            "modules": {
                "good": [2.0, 1.5],
                "bad": {"tests": 0},
            },
        }))
        costs = run_tests.read_durations(path)
        self.assertIn("good", costs.modules)
        self.assertNotIn("bad", costs.modules)
        self.assertEqual(costs.default_seconds, 2.0)
        self.assertEqual(costs.default_overhead, 3.0)
        self.assertEqual(costs.estimate("bad", 4), 4 * 2.0 + 1 * 3.0)

    def test_the_split_does_not_depend_on_plan_order(self):
        # This starts no process: the split runs in memory.
        first = tuple(run_tests.Test(seq, f"mod.Case.test_{name}", "mod", "mod.Case")
                      for seq, name in enumerate("abcd"))
        second = tuple(run_tests.Test(seq, f"mod.Case.test_{name}", "mod", "mod.Case")
                       for seq, name in enumerate("dcba"))
        costs = run_tests.Costs(
            {"mod": run_tests.ModuleCost(tests=4, seconds=104.0, overhead=1.0)},
            default_seconds=1.0,
            default_overhead=1.0,
        )
        for count in (1, 2, 3, 4):
            with self.subTest(count=count):
                by_id_first = {test.id: run_tests.part_of(first, count, costs)[test.seq] for test in first}
                by_id_second = {test.id: run_tests.part_of(second, count, costs)[test.seq] for test in second}
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

    def test_summarize_durations_sums_test_seconds_and_medians_shard_overheads_rather_than_dropping_modules(self):
        # This starts no process: the summary runs in memory.
        plan = (
            run_tests.Test(0, "aaa.Case.test_one", "aaa", "aaa.Case"),
            run_tests.Test(1, "aaa.Case.test_two", "aaa", "aaa.Case"),
            run_tests.Test(2, "bbb.Case.test_one", "bbb", "bbb.Case"),
        )
        measured = {"aaa.Case.test_one": 1.0, "aaa.Case.test_two": 3.0, "bbb.Case.test_one": 2.0}
        spans = [("aaa", (0,), 2.5), ("aaa", (1,), 5.0)]
        doc = run_tests.summarize_durations(plan, measured, spans)
        self.assertEqual(doc["modules"]["aaa"], [2.0, 1.75])
        self.assertEqual(doc["modules"]["bbb"], [2.0, doc["default_overhead"]])
        self.assertEqual(doc["default_seconds"], 2.0)
        self.assertEqual(doc["default_overhead"], 1.75)
        self.assertEqual(list(doc["modules"]), sorted(doc["modules"]))
        text = run_tests.format_durations(doc)
        self.assertEqual(json.loads(text), doc)
        self.assertEqual(len(text.splitlines()), 6 + len(doc["modules"]))

    def test_record_durations_writes_a_readable_file_and_exits_with_the_suite_status(self):
        self.write("test_slow.py", """
            import time
            import unittest

            class SlowTest(unittest.TestCase):
                def test_one(self):
                    time.sleep(0.3)

                def test_two(self):
                    time.sleep(0.3)
        """)
        out = self.proj / "durations.json"
        result = self.runner("--record-durations", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(out.read_text())
        self.assertGreater(data["default_seconds"], 0)
        self.assertGreater(data["default_overhead"], 0)
        self.assertIn("test_slow", data["modules"])
        record = data["modules"]["test_slow"]
        self.assertEqual(len(record), 2)
        self.assertGreater(record[0], 0)
        self.assertGreater(record[1], 0)
        reread = run_tests.read_durations(out)
        self.assertEqual(reread.modules["test_slow"].seconds, record[0])
        self.assertEqual(reread.modules["test_slow"].overhead, record[1])
        self.assertEqual(reread.default_seconds, data["default_seconds"])
        self.assertEqual(reread.default_overhead, data["default_overhead"])
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
        self.assertGreater(data["default_overhead"], 0)
        self.assertIsInstance(data["modules"], dict)

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

    def test_a_part_run_passes_shards_in_descending_cost_ahead_of_build_order_while_a_full_run_keeps_build_order(self):
        plan = []
        seq = 0
        for module, count in (("big", 21), ("early", 1), ("late", 1)):
            for _ in range(count):
                plan.append(run_tests.Test(seq, module + ".id", module, module + ".C"))
                seq += 1
        plan = tuple(plan)
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
        costs = run_tests.read_durations()
        placed = run_tests.build_shards(tuple(plan))
        expected = run_tests.launch_order(placed, True, lambda shard: costs.estimate(shard.module, len(shard.seqs)))
        self.assertEqual(
            [(shard.number, len(shard.seqs)) for batch in received for shard in batch],
            [(shard.number, len(shard.seqs)) for shard in expected],
        )
        received.clear()
        with tempfile.TemporaryDirectory() as directory:
            run_tests.run(Path(directory), 1, 30, None)
        placed = run_tests.build_shards(tuple(plan))
        self.assertEqual(
            [(shard.number, len(shard.seqs)) for batch in received for shard in batch],
            [(shard.number, len(shard.seqs)) for shard in placed],
        )
