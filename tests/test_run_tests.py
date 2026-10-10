import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_tests.py"
sys.path.insert(0, str(ROOT / "scripts"))

import run_tests

RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)

PASSING = """
    import unittest

    class AlphaTest(unittest.TestCase):
        def test_one(self):
            self.assertEqual(1, 1)

        def test_two(self):
            self.assertEqual(2, 2)
"""


class RunTestsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.proj = Path(tmp.name).resolve() / "proj"
        self.tests = self.proj / "tests"
        self.tests.mkdir(parents=True)

    def write(self, name, body):
        (self.tests / name).write_text(textwrap.dedent(body))

    def runner(self, *args, env=None):
        return subprocess.run([sys.executable, str(RUNNER), "-s", str(self.tests), "-j", "2", *args],
                              capture_output=True, text=True, env=env)

    def serial_count(self):
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=self.proj,
                                capture_output=True, text=True)
        return RAN.search(result.stderr).group(1)

    def test_a_passing_suite_exits_0_with_ok_and_the_serial_test_count(self):
        self.write("test_alpha.py", PASSING)
        self.write("test_beta.py", """
            import unittest

            class BetaTest(unittest.TestCase):
                def test_three(self):
                    self.assertEqual(3, 3)
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.splitlines()[0], "run_tests: 3 tests, 2 shards, 2 at a time")
        self.assertRegex(result.stderr, r"\nRan 3 tests in \d+\.\d{3}s\n\nOK\n\Z")
        self.assertEqual(RAN.search(result.stderr).group(1), "3")
        self.assertEqual(self.serial_count(), "3")

    def test_a_one_test_suite_run_with_j_4_prints_1_test_1_shard_1_at_a_time(self):
        self.write("test_single.py", """
            import unittest

            class SingleTest(unittest.TestCase):
                def test_one(self):
                    pass
        """)
        result = self.runner("-j", "4")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.splitlines()[0], "run_tests: 1 test, 1 shard, 1 at a time")

    def test_a_failing_assertion_exits_1_with_its_message_and_the_serial_test_count(self):
        self.write("test_alpha.py", PASSING)
        self.write("test_bad.py", """
            import unittest

            class BadTest(unittest.TestCase):
                def test_fine(self):
                    pass

                def test_wrong(self):
                    self.assertEqual(1, 2)
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nFAIL  test_bad.BadTest.test_wrong\n", result.stderr)
        self.assertIn("\nFAIL: test_wrong (test_bad.BadTest", result.stderr)
        self.assertIn("\nAssertionError: 1 != 2\n", result.stderr)
        self.assertTrue(result.stderr.endswith("\n\nFAILED (failures=1)\n"), result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "4")
        self.assertEqual(self.serial_count(), "4")

    def test_an_error_a_skip_an_expected_failure_and_an_unexpected_success_are_in_the_last_line(self):
        self.write("test_mixed.py", """
            import unittest

            class MixedTest(unittest.TestCase):
                def test_error(self):
                    raise RuntimeError("boom")

                @unittest.skip("not today")
                def test_skip(self):
                    pass

                @unittest.expectedFailure
                def test_xfail(self):
                    self.assertEqual(1, 2)

                @unittest.expectedFailure
                def test_xpass(self):
                    pass
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nERROR: test_error (test_mixed.MixedTest", result.stderr)
        self.assertIn("\nRuntimeError: boom\n", result.stderr)
        self.assertIn("\nUNEXPECTED SUCCESS: test_mixed.MixedTest.test_xpass\n", result.stderr)
        self.assertTrue(result.stderr.endswith(
            "\n\nFAILED (errors=1, skipped=1, expected failures=1, unexpected successes=1)\n"), result.stderr)

    def test_a_skip_alone_exits_0_with_ok_and_the_skip_count(self):
        self.write("test_skip.py", """
            import unittest

            class SkipTest(unittest.TestCase):
                @unittest.skip("not today")
                def test_skip(self):
                    pass
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "1")
        self.assertTrue(result.stderr.endswith("\n\nOK (skipped=1)\n"), result.stderr)

    def test_two_failed_subtests_print_two_blocks_and_count_one_test(self):
        self.write("test_sub.py", """
            import unittest

            class SubTest(unittest.TestCase):
                def test_each(self):
                    for i in range(3):
                        with self.subTest(i=i):
                            self.assertEqual(i, 1)
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stderr.count("\nFAIL: test_each (test_sub.SubTest"), 2)
        self.assertIn("(i=0)\n", result.stderr)
        self.assertIn("(i=2)\n", result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "1")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (failures=2)\n"), result.stderr)

    def test_one_class_name_failing_in_two_modules_prints_a_block_for_each_module(self):
        body = """
            import unittest

            class SameTest(unittest.TestCase):
                def test_wrong(self):
                    self.assertEqual("here", "there")
        """
        self.write("test_one.py", body)
        self.write("test_two.py", body)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nFAIL: test_wrong (test_one.SameTest", result.stderr)
        self.assertIn("\nFAIL: test_wrong (test_two.SameTest", result.stderr)
        self.assertTrue(result.stderr.endswith("\n\nFAILED (failures=2)\n"), result.stderr)

    def test_a_module_that_fails_to_import_counts_as_one_test_and_one_error(self):
        self.write("test_alpha.py", PASSING)
        self.write("test_broken.py", "import no_such_module_d130\n")
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nERROR: test_broken (unittest.loader._FailedTest", result.stderr)
        self.assertIn("\nModuleNotFoundError: No module named 'no_such_module_d130'\n", result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "3")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1)\n"), result.stderr)

    def test_a_module_imports_another_test_module_by_its_top_level_name_and_through_the_tests_package(self):
        self.write("test_alpha.py", "X = 7\n" + textwrap.dedent(PASSING))
        self.write("test_top.py", """
            import unittest
            import test_alpha

            class TopTest(unittest.TestCase):
                def test_x(self):
                    self.assertEqual(test_alpha.X, 7)
        """)
        self.write("test_package.py", """
            import unittest
            from tests.test_alpha import X

            class PackageTest(unittest.TestCase):
                def test_x(self):
                    self.assertEqual(X, 7)
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "4")
        self.assertTrue(result.stderr.endswith("\n\nOK\n"), result.stderr)

    def test_a_worker_gets_tmpdir_and_both_xdg_homes_as_directories_outside_the_project(self):
        self.write("test_env.py", """
            import os
            import tempfile
            import unittest
            from pathlib import Path

            PROJECT = Path(__file__).resolve().parents[1]

            class EnvTest(unittest.TestCase):
                def test_env(self):
                    for name in ("TMPDIR", "XDG_STATE_HOME", "XDG_CONFIG_HOME"):
                        path = Path(os.environ[name]).resolve()
                        self.assertTrue(path.is_dir(), name)
                        self.assertNotIn(PROJECT, [path, *path.parents], name)
                    self.assertEqual(tempfile.gettempdir(), os.environ["TMPDIR"])
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "1")
        self.assertTrue(result.stderr.endswith("\n\nOK\n"), result.stderr)

    def test_a_test_that_exits_its_worker_with_status_3_is_lost_with_the_test_after_it_and_the_shard_output_prints_once(self):
        self.write("test_exit.py", """
            import os
            import unittest

            class ExitTest(unittest.TestCase):
                def test_a_before(self):
                    pass

                def test_b_exits(self):
                    print("about to exit", flush=True)
                    os._exit(3)

                def test_c_after(self):
                    pass
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nLOST  test_exit.ExitTest.test_b_exits\n", result.stderr)
        self.assertIn("\nLOST: test_exit.ExitTest.test_b_exits\n" + "-" * 70 + "\n"
                      "The worker for shard 1 exited with status 3 while this test was running.\n"
                      "Output of its shard:\n  about to exit\n", result.stderr)
        self.assertIn("\nLOST: test_exit.ExitTest.test_c_after\n" + "-" * 70 + "\n"
                      "The worker for shard 1 exited with status 3 before it started this test.\n", result.stderr)
        self.assertEqual(result.stderr.count("Output of its shard:"), 1)
        self.assertEqual(RAN.search(result.stderr).group(1), "3")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (lost=2)\n"), result.stderr)

    def test_a_test_that_sleeps_past_a_2_second_timeout_is_lost(self):
        self.write("test_slow.py", """
            import time
            import unittest

            class SlowTest(unittest.TestCase):
                def test_sleeps(self):
                    time.sleep(60)
        """)
        result = self.runner("--timeout", "2")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nLOST: test_slow.SlowTest.test_sleeps\n" + "-" * 70 + "\n"
                      "The worker for shard 1 exceeded the limit of 2 s ", result.stderr)
        self.assertTrue(result.stderr.endswith("\n\nFAILED (lost=1)\n"), result.stderr)

    def test_a_raising_setupclass_prints_its_traceback_and_loses_the_tests_of_its_class(self):
        self.write("test_fixture.py", """
            import unittest

            class FixtureTest(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    raise RuntimeError("no fixture")

                def test_one(self):
                    pass

                def test_two(self):
                    pass
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nERROR: setUpClass (test_fixture.FixtureTest)\n", result.stderr)
        self.assertIn("\nRuntimeError: no fixture\n", result.stderr)
        cause = "A class or module fixture failed in the worker for shard 1, and this test did not start.\n"
        self.assertIn("\nLOST: test_fixture.FixtureTest.test_one\n" + "-" * 70 + "\n" + cause, result.stderr)
        self.assertIn("\nLOST: test_fixture.FixtureTest.test_two\n" + "-" * 70 + "\n" + cause, result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "2")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1, lost=2)\n"), result.stderr)

    def test_a_setupclass_that_raises_skiptest_exits_0_with_ok_skipped_2_and_counts_4_tests(self):
        self.write("test_alpha.py", PASSING)
        self.write("test_git.py", """
            import unittest

            class GitTest(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    raise unittest.SkipTest("no git")

                def test_one(self):
                    pass

                def test_two(self):
                    pass
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.splitlines()[-1], "OK (skipped=2)")
        self.assertEqual(RAN.search(result.stderr).group(1), "4")

    def test_the_runner_exits_2_when_pstack_run_tests_holds_its_start_directory(self):
        self.write("test_alpha.py", PASSING)
        result = self.runner(env={**os.environ, "PSTACK_RUN_TESTS": str(self.tests)})
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, f"run_tests: already running on {self.tests}. "
                                        "A test must not start the runner on its own suite.\n")

    def test_the_runner_exits_2_when_the_start_directory_holds_no_test(self):
        result = self.runner()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, f"run_tests: no test found under {self.tests}\n")


class AccountTest(unittest.TestCase):
    OK = {"ev": "result", "status": "ok", "problems": []}

    def test_a_seq_the_finished_worker_never_reported_is_lost(self):
        events = [{"ev": "start", "seq": 4}, {**self.OK, "seq": 4}, {"ev": "done"}]
        accounting = run_tests.account(run_tests.Shard(2, "test_x", (4, 5)), events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "ok")
        self.assertEqual(accounting.verdicts[5].kind, "lost")
        self.assertEqual(accounting.verdicts[5].cause,
                         "The worker for shard 2 finished and reported fewer tests than it was given.")
        self.assertEqual(accounting.violations, ())

    def test_a_result_for_a_seq_the_shard_was_not_given_is_a_violation_and_no_verdict(self):
        events = [{"ev": "start", "seq": 4}, {**self.OK, "seq": 4}, {"ev": "start", "seq": 9}, {**self.OK, "seq": 9},
                  {"ev": "done"}]
        accounting = run_tests.account(run_tests.Shard(2, "test_x", (4,)), events, run_tests.Ended(0))
        self.assertEqual(sorted(accounting.verdicts), [4])
        self.assertEqual(accounting.verdicts[4].kind, "ok")
        self.assertEqual(accounting.violations, ((9, "reported a test it was not given"),))

    def test_a_second_result_for_one_seq_is_a_violation_and_the_first_result_stands(self):
        bad = {"ev": "result", "seq": 4, "status": "bad",
               "problems": [{"kind": "fail", "label": "test_a (test_x.T)", "traceback": "AssertionError\n"}]}
        events = [{"ev": "start", "seq": 4}, bad, {**self.OK, "seq": 4}, {"ev": "done"}]
        accounting = run_tests.account(run_tests.Shard(2, "test_x", (4,)), events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "bad")
        self.assertEqual(accounting.verdicts[4].problems[0].traceback, "AssertionError\n")
        self.assertEqual(accounting.violations, ((4, "reported a second result for one test"),))

    def test_a_seq_with_no_start_is_lost_with_the_fixture_cause_when_one_fixture_failed_and_one_skipped(self):
        events = [{"ev": "fixture", "label": "setUpClass (test_x.A)", "traceback": "RuntimeError\n"},
                  {"ev": "fixture", "skip": True, "label": "setUpClass (test_x.B)", "reason": "no git"},
                  {"ev": "done"}]
        accounting = run_tests.account(run_tests.Shard(2, "test_x", (4,)), events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "lost")
        self.assertEqual(accounting.verdicts[4].cause,
                         "A class or module fixture failed in the worker for shard 2, and this test did not start.")
        self.assertEqual(accounting.fixtures, (run_tests.Problem("error", "setUpClass (test_x.A)", "RuntimeError\n"),))


if __name__ == "__main__":
    unittest.main()
