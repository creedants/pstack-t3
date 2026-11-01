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
    run_tests,
    setUpModule,
    sys,
    unittest,
)


class PartsCostsMixin:
    def test_a_modules_recorded_overhead_changes_which_part_its_single_test_lands_in(self):
        """Two one-test modules with the same seconds per test, where only bravo's recorded overhead separates them.

        bravo costs 3 s and alpha 1 s, so the split places bravo first and bravo takes part 1. Without the overhead
        both cost 1 s and the id order places alpha first, so alpha takes part 1 and bravo part 2.
        """
        plan = (
            run_tests.Test(0, "alpha.C.test_00", "alpha", "alpha.C"),
            run_tests.Test(1, "bravo.C.test_00", "bravo", "bravo.C"),
        )
        costs = run_tests.Costs(
            {
                "alpha": run_tests.ModuleCost(tests=1, seconds=1.0, overhead=0.0),
                "bravo": run_tests.ModuleCost(tests=1, seconds=1.0, overhead=2.0),
            },
            default_seconds=1.0,
            default_overhead=1.0,
        )
        self.assertEqual(costs.estimate("bravo", 1), 3.0)
        assignment = run_tests.part_of(plan, 2, costs)
        self.assertEqual([assignment[0], assignment[1]], [2, 1])

    def test_a_part_run_starts_shards_in_descending_estimated_cost_not_build_order(self):
        """The shards cost 3 s, 4 s, 9 s, and 5 s in build order, and a part run starts the 9 s one first.

        The build order follows the module size, so shard 1 holds the three-test alpha, shard 2 charlie, shard 3
        bravo, and shard 4 delta.
        """
        plan = []
        seq = 0
        for module, count in (("alpha", 3), ("bravo", 1), ("charlie", 2), ("delta", 1)):
            for index in range(count):
                plan.append(run_tests.Test(seq, f"{module}.C.test_{index}", module, f"{module}.C"))
                seq += 1
        costs = run_tests.Costs(
            {
                "alpha": run_tests.ModuleCost(tests=3, seconds=3.0, overhead=0.0),
                "bravo": run_tests.ModuleCost(tests=1, seconds=9.0, overhead=0.0),
                "charlie": run_tests.ModuleCost(tests=2, seconds=4.0, overhead=0.0),
                "delta": run_tests.ModuleCost(tests=1, seconds=5.0, overhead=0.0),
            },
            default_seconds=1.0,
            default_overhead=1.0,
        )
        shards = run_tests.build_shards(tuple(plan))
        ordered = run_tests.launch_order(
            shards, True, lambda shard: costs.estimate(shard.module, len(shard.seqs)))
        self.assertEqual([shard.number for shard in ordered], [3, 4, 2, 1])

    def test_a_missing_durations_file_still_covers_every_repository_test_once_in_eight_parts(self):
        """A file that does not exist yields the built-in defaults, and every test the real repository discovery returns still lands in exactly one of eight parts."""
        saved = sys.path[:]
        try:
            found = run_tests.discover(run_tests.ROOT / "tests")
        finally:
            sys.path[:] = saved
        plan = tuple(run_tests.Test(seq, test.id(), type(test).__module__, run_tests.class_name(test))
                     for seq, test in enumerate(found))
        missing = self.proj / "no-such-durations.json"
        self.assertFalse(missing.exists())
        parts = [run_tests.tests_in_part(plan, index, 8, durations_path=missing) for index in range(1, 9)]
        got = [test.seq for part in parts for test in part]
        self.assertEqual(sorted(got), [test.seq for test in plan])
        self.assertEqual(len(got), len(set(got)))
        self.assertTrue(all(parts))
