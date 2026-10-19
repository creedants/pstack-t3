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


class WorkersMixin:
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

    def test_a_worker_gets_no_variable_with_the_uppercase_git_underscore_prefix_from_the_runners_environment(self):
        self.write("test_git_env.py", """
            import os
            import unittest

            class GitEnvTest(unittest.TestCase):
                def test_git_env(self):
                    self.assertEqual(sorted(name for name in os.environ if name.startswith("GIT_")), [])
        """)
        elsewhere = self.proj / "elsewhere"
        result = self.runner(env={**os.environ, "GIT_DIR": str(elsewhere / ".git"), "GIT_WORK_TREE": str(elsewhere),
                                  "GIT_AUTHOR_NAME": "someone"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "1")

    def test_a_test_that_exits_its_worker_with_status_3_is_lost_with_the_test_after_it_and_the_shard_error_shows_the_output_once(self):
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
                      "The worker for shard 1 exited with status 3 while this test was running.\n", result.stderr)
        self.assertIn("\nLOST: test_exit.ExitTest.test_c_after\n" + "-" * 70 + "\n"
                      "The worker for shard 1 exited with status 3 before it started this test.\n", result.stderr)
        self.assertIn("\nERROR: shard 1 of test_exit\n" + "-" * 70 + "\n"
                      "The worker for shard 1 exited with status 3 before it reported that it had finished.\n"
                      "Output of the worker:\n  about to exit\n", result.stderr)
        self.assertEqual(result.stderr.count("about to exit"), 1)
        self.assertEqual(RAN.search(result.stderr).group(1), "3")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1, lost=2)\n"), result.stderr)

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
        self.assertIn("\nERROR: shard 1 of test_slow\n" + "-" * 70 + "\n"
                      "The worker for shard 1 exceeded the limit of 2 s before it reported that it had finished.\n",
                      result.stderr)
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1, lost=1)\n"), result.stderr)

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

    def test_a_worker_killed_in_teardownmodule_after_its_test_passed_exits_1_with_an_error_for_its_shard(self):
        self.write("test_good.py", GOOD)
        self.write("test_probe.py", """
            import os
            import signal
            import unittest

            class Probe(unittest.TestCase):
                def test_probe(self):
                    pass

            def tearDownModule():
                os.kill(os.getpid(), signal.SIGKILL)
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nERROR: shard 2 of test_probe\n" + "-" * 70 + "\n"
                      "The worker for shard 2 was killed by signal 9 before it reported that it had finished.\n",
                      result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "2")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1)\n"), result.stderr)

    def test_a_worker_that_exits_0_in_teardownmodule_after_its_test_passed_exits_1_with_an_error_for_its_shard(self):
        self.write("test_good.py", GOOD)
        self.write("test_probe.py", """
            import os
            import unittest

            class Probe(unittest.TestCase):
                def test_probe(self):
                    pass

            def tearDownModule():
                os._exit(0)
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nERROR: shard 2 of test_probe\n" + "-" * 70 + "\n"
                      "The worker for shard 2 exited with status 0 before it reported that it had finished.\n",
                      result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "2")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1)\n"), result.stderr)

    def test_a_worker_that_exits_3_after_it_reported_that_it_had_finished_exits_1_with_an_error_that_shows_its_output(self):
        self.write("test_good.py", GOOD)
        self.write("test_probe.py", """
            import atexit
            import os
            import unittest

            class Probe(unittest.TestCase):
                def test_probe(self):
                    print("probe output", flush=True)
                    atexit.register(lambda: os._exit(3))
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nERROR: shard 2 of test_probe\n" + "-" * 70 + "\n"
                      "The worker for shard 2 exited with status 3 after it reported that it had finished.\n"
                      "Output of the worker:\n  probe output\n", result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "2")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1)\n"), result.stderr)

    def test_a_worker_that_writes_bytes_with_no_newline_past_a_2_second_timeout_is_killed_and_its_test_is_lost(self):
        self.write("test_junk.py", NEVER_AN_EVENT.format(text="x"))
        result = self.runner("--timeout", "2", limit=30)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nLOST: test_junk.JunkTest.test_junk\n" + "-" * 70 + "\n"
                      "The worker for shard 1 exceeded the limit of 2 s while this test was running.\n", result.stderr)
        self.assertIn("\nIts result file ends in a line with no newline. It reads 'xxx", result.stderr)
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1, lost=1)\n"), result.stderr)

    def test_a_worker_that_writes_lines_that_are_not_events_past_a_2_second_timeout_is_killed_and_its_test_is_lost(self):
        self.write("test_junk.py", NEVER_AN_EVENT.format(text="junk\n"))
        result = self.runner("--timeout", "2", limit=30)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nLOST: test_junk.JunkTest.test_junk\n" + "-" * 70 + "\n"
                      "The worker for shard 1 exceeded the limit of 2 s while this test was running.\n", result.stderr)
        self.assertIn("\nLine 2 of its result file is not an event. It reads 'junk'.\n", result.stderr)
        self.assertRegex(result.stderr, r"\nThe number of further lines of its result file that are not events is \d+\.\n")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1, lost=1)\n"), result.stderr)

