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


class IdsFixturesMixin:
    def test_a_test_built_by_a_load_tests_hook_with_42_runs_with_42_and_fails_as_in_the_serial_command(self):
        self.write("test_good.py", GOOD)
        self.write("test_probe.py", """
            import unittest

            class Probe(unittest.TestCase):
                def __init__(self, methodName="runTest", value=0):
                    super().__init__(methodName)
                    self.value = value

                def test_probe(self):
                    self.assertNotEqual(self.value, 42)

            def load_tests(loader, tests, pattern):
                return unittest.TestSuite([Probe("test_probe", 42)])
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nAssertionError: 42 == 42\n", result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "2")
        self.assertEqual(result.stderr.splitlines()[-1], "FAILED (failures=1)")
        self.assertEqual(self.serial().stderr.splitlines()[-1], "FAILED (failures=1)")

    def test_two_tests_with_one_id_each_run_and_each_count(self):
        self.write("test_probe.py", """
            import unittest

            class Probe(unittest.TestCase):
                def __init__(self, methodName="runTest", value=0):
                    super().__init__(methodName)
                    self.value = value

                def test_probe(self):
                    self.assertNotEqual(self.value, 2)

            def load_tests(loader, tests, pattern):
                return unittest.TestSuite([Probe("test_probe", 1), Probe("test_probe", 2)])
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nAssertionError: 2 == 2\n", result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "2")
        self.assertEqual(result.stderr.splitlines()[-1], "FAILED (failures=1)")
        self.assertEqual(self.serial().stderr.splitlines()[-1], "FAILED (failures=1)")

    def test_25_tests_with_one_id_run_once_each_in_2_shards(self):
        self.write("test_probe.py", """
            import unittest
            from pathlib import Path

            class Probe(unittest.TestCase):
                def __init__(self, methodName="runTest", value=0):
                    super().__init__(methodName)
                    self.value = value

                def test_probe(self):
                    with open(Path(__file__).resolve().parents[1] / "ran", "a") as out:
                        out.write(f"{self.value}\\n")

            def load_tests(loader, tests, pattern):
                return unittest.TestSuite(Probe("test_probe", value) for value in range(25))
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.splitlines()[0], "run_tests: 25 tests, 2 shards, 2 at a time")
        self.assertEqual(sorted(map(int, (self.proj / "ran").read_text().split())), list(range(25)))
        self.assertEqual(RAN.search(result.stderr).group(1), "25")

    def test_one_test_object_discovered_twice_runs_twice_and_counts_2_tests(self):
        self.write("test_probe.py", """
            import unittest
            from pathlib import Path

            class Probe(unittest.TestCase):
                def test_probe(self):
                    with open(Path(__file__).resolve().parents[1] / "ran", "a") as out:
                        out.write("x")

            def load_tests(loader, tests, pattern):
                probe = Probe("test_probe")
                return unittest.TestSuite([probe, probe])
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.proj / "ran").read_text(), "xx")
        self.assertEqual(RAN.search(result.stderr).group(1), "2")
        self.assertEqual(self.serial_count(), "2")

    def test_a_worker_whose_discovery_orders_the_tests_differently_runs_none_and_both_tests_are_lost(self):
        self.write("test_shift.py", """
            import unittest
            from pathlib import Path

            SEEN = Path(__file__).resolve().parents[1] / "seen"

            class ShiftTest(unittest.TestCase):
                def test_a(self):
                    pass

                def test_b(self):
                    pass

            def load_tests(loader, tests, pattern):
                names = ["test_b", "test_a"] if SEEN.exists() else ["test_a", "test_b"]
                SEEN.touch()
                return unittest.TestSuite(ShiftTest(name) for name in names)
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nLOST: test_shift.ShiftTest.test_a\n" + "-" * 70 + "\n"
                      "The worker for shard 1 exited with status 1 before it started this test.\n", result.stderr)
        self.assertEqual(result.stderr.count("The worker for shard 1 exited with status 1 before it started this test.\n"), 2)
        self.assertIn("  run_tests: this worker discovered 2 tests and the listing discovered 2. "
                      "The ids first differ at position 0.\n", result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "2")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1, lost=2)\n"), result.stderr)

    def test_a_load_tests_hook_that_adds_a_test_on_each_call_exits_1_and_the_listed_test_is_lost(self):
        self.write("test_grow.py", """
            import unittest
            from pathlib import Path

            CALLS = Path(__file__).resolve().parents[1] / "calls"

            class GrowTest(unittest.TestCase):
                def runTest(self):
                    pass

            def load_tests(loader, tests, pattern):
                with open(CALLS, "a") as calls:
                    calls.write("x")
                return unittest.TestSuite(GrowTest() for _ in CALLS.read_text())
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr.splitlines()[0], "run_tests: 1 test, 1 shard, 1 at a time")
        self.assertIn("\nLOST: test_grow.GrowTest.runTest\n" + "-" * 70 + "\n"
                      "The worker for shard 1 exited with status 1 before it started this test.\n", result.stderr)
        self.assertIn("  run_tests: this worker discovered 2 tests and the listing discovered 1. "
                      "The ids first differ at position 1.\n", result.stderr)
        self.assertEqual((self.proj / "calls").read_text(), "xx")
        self.assertEqual(RAN.search(result.stderr).group(1), "1")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1, lost=1)\n"), result.stderr)

    def test_a_behind_event_a_test_writes_for_the_next_test_before_it_stops_the_suite_exits_1_and_the_next_test_is_lost(self):
        self.write("test_probe.py", """
            import json
            import sys
            import unittest

            class P(unittest.TestCase):
                def test_a(self):
                    spec = json.load(open(sys.argv[sys.argv.index("--worker") + 1]))
                    with open(spec["results"], "a") as out:
                        out.write(json.dumps({"ev": "behind", "seq": spec["seqs"][1], "skip": True}) + "\\n")
                    self._outcome.result.stop()

                def test_b(self):
                    self.fail("never ran")
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nERROR: shard 1 of test_probe\n" + "-" * 70 + "\n"
                      "The worker reported a test as behind a skipped fixture with no earlier report that the setUpClass of "
                      "its class or the setUpModule of its module raised SkipTest. The test is test_probe.P.test_b.\n",
                      result.stderr)
        self.assertIn("\nLOST: test_probe.P.test_b\n" + "-" * 70 + "\n"
                      "The worker for shard 1 finished without an accepted result for this test.\n", result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "2")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1, lost=1)\n"), result.stderr)

    def test_a_test_no_fixture_covers_is_lost_when_another_class_skipped_in_setupclass(self):
        self.write("test_probe.py", """
            import unittest

            class A(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    raise unittest.SkipTest("skip A only")

                def test_a(self):
                    pass

            class B(unittest.TestCase):
                def test_b_stop(self):
                    self._outcome.result.stop()

                def test_c_failure(self):
                    self.fail("must not disappear")
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nLOST: test_probe.B.test_c_failure\n" + "-" * 70 + "\n"
                      "The worker for shard 1 finished without an accepted result for this test.\n", result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "3")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (lost=1, skipped=1)\n"), result.stderr)

    def test_a_class_whose_setupclass_raised_skiptest_is_skipped_and_a_class_whose_setupclass_failed_is_lost_in_one_worker(self):
        self.write("test_probe.py", """
            import unittest

            class A(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    raise unittest.SkipTest("skip A only")

                def test_a(self):
                    pass

            class B(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    raise RuntimeError("no fixture")

                def test_b(self):
                    pass
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nERROR: setUpClass (test_probe.B)\n", result.stderr)
        self.assertIn("\nLOST: test_probe.B.test_b\n" + "-" * 70 + "\n"
                      "A class or module fixture failed in the worker for shard 1, and this test did not start.\n",
                      result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "2")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1, lost=1, skipped=1)\n"), result.stderr)

    def test_a_setupmodule_that_raises_skiptest_skips_the_tests_of_its_module_in_both_classes(self):
        self.write("test_good.py", GOOD)
        self.write("test_probe.py", """
            import unittest

            def setUpModule():
                raise unittest.SkipTest("no module")

            class A(unittest.TestCase):
                def test_a(self):
                    pass

            class B(unittest.TestCase):
                def test_b(self):
                    pass
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.splitlines()[-1], "OK (skipped=2)")
        self.assertEqual(RAN.search(result.stderr).group(1), "3")

