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


class ResultsMixin:
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

    def test_a_test_with_one_skipped_subtest_exits_0_with_ok_skipped_1_and_ran_1_test_and_the_serial_command_prints_ok_skipped_1(self):
        self.write("test_subskip.py", """
            import unittest

            class SubSkipTest(unittest.TestCase):
                def test_each(self):
                    with self.subTest(i=0):
                        self.skipTest("later")
                    with self.subTest(i=1):
                        self.assertEqual(1, 1)
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.splitlines()[-1], "OK (skipped=1)")
        self.assertEqual(RAN.search(result.stderr).group(1), "1")
        serial = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=self.proj,
                                capture_output=True, text=True)
        self.assertEqual(serial.stderr.splitlines()[-1], "OK (skipped=1)")

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

    def test_two_modules_import_a_test_module_by_its_top_level_name_and_through_the_tests_package(self):
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

    def test_the_runner_exits_2_when_pstack_run_tests_holds_its_start_directory(self):
        self.write("test_alpha.py", PASSING)
        result = self.runner(env={**os.environ, "PSTACK_RUN_TESTS": str(self.tests)})
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, f"run_tests: already running on {self.tests}. "
                                        "A test must not start the runner on its own suite.\n")

    def test_the_runner_exits_2_when_the_start_directory_does_not_exist(self):
        self.tests.rmdir()
        result = self.runner()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, f"run_tests: no test found under {self.tests}\n")

    def test_help_exits_0_and_ends_with_the_last_2_paragraphs_of_the_module_docstring_with_10_seconds_for_sweep_seconds(self):
        result = subprocess.run([sys.executable, str(RUNNER), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        guarantee, processes = (" ".join(paragraph.split()) for paragraph in run_tests.__doc__.split("\n\n")[-2:])
        self.assertTrue(guarantee.startswith("What this runner guarantees. The tests are trusted code. "), guarantee)
        self.assertIn(" A worker runs the test objects its own discovery builds, and the run fails when that discovery "
                      "differs from the listing's in ids, count, or order. ", guarantee)
        self.assertEqual(processes.count("SWEEP_SECONDS"), 2)
        self.assertTrue(processes.startswith("The runner sends SIGKILL to the process group of each child it starts "), processes)
        self.assertTrue(" ".join(result.stdout.split()).endswith(
            f" {guarantee} {processes}".replace("SWEEP_SECONDS", "10 seconds")), result.stdout)
        self.assertIn("\n\nWhat this runner guarantees. ", result.stdout)
        self.assertIn("\n\nThe runner sends SIGKILL ", result.stdout)

    def test_a_start_directory_with_no_test_ends_as_the_serial_command_ends(self):
        result = self.runner()
        serial = self.serial()
        self.assertEqual(result.returncode, serial.returncode, result.stderr)
        self.assertEqual(result.returncode, 5 if sys.version_info >= (3, 12) else 0)
        self.assertEqual(result.stderr.splitlines()[-1], serial.stderr.splitlines()[-1])
        self.assertEqual(RAN.search(result.stderr).group(1), "0")

