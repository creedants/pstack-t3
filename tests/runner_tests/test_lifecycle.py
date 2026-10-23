from .support import (
    GOOD,
    LINUX,
    MARKS,
    NEEDS_LINUX,
    os,
    signal,
    unittest,
)


LOWERED = "run_tests: -j 8 lowered to 4 for the 1 slot this command holds"


class LifecycleMixin:
    @unittest.skipUnless(LINUX, NEEDS_LINUX)
    def test_a_sigkill_of_the_runner_ends_the_worker_that_is_running_a_test(self):
        self.write("marks.py", MARKS)
        self.write("test_slow.py", """
            import os
            import time
            import unittest

            import marks

            class SlowTest(unittest.TestCase):
                def test_slow(self):
                    marks.mark("worker", os.getpid())
                    time.sleep(60)
        """)
        proc = self.start()
        worker = self.pid_from("worker")
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait()
        self.assert_dead(worker)

    def test_a_run_removes_a_scratch_directory_named_after_a_pid_no_process_has(self):
        self.write("test_good.py", GOOD)
        stale = self.scratch / "run-tests-2147483647-left"
        stale.mkdir()
        (stale / "left.txt").write_text("left")
        result = self.runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.splitlines()[-1], "OK")
        self.assertFalse(stale.exists())

    def test_a_run_inside_a_one_slot_lowers_8_jobs_to_4_and_says_so(self):
        self.five_modules()
        result = self.runner("-j", "8", env={**os.environ, "LAND_SLOT": "worker", "LAND_SLOT_FILES": "worker-1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.count(LOWERED), 1, result.stderr)
        # The lowered line is printed before the run starts, so the plan line follows it.
        self.assertTrue(result.stderr.splitlines()[1].endswith("4 at a time"), result.stderr)

    def test_a_run_inside_a_two_slot_command_keeps_8_jobs(self):
        self.five_modules()
        result = self.runner("-j", "8", env={**os.environ, "LAND_SLOT": "worker", "LAND_SLOT_FILES": "worker-1 worker-2"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("lowered", result.stderr)
        self.assertTrue(result.stderr.splitlines()[0].endswith("5 at a time"), result.stderr)

    def test_a_run_outside_a_slot_keeps_8_jobs(self):
        self.five_modules()
        result = self.runner("-j", "8", env={name: value for name, value in os.environ.items() if not name.startswith("LAND_SLOT")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("lowered", result.stderr)
        self.assertTrue(result.stderr.splitlines()[0].endswith("5 at a time"), result.stderr)

    def five_modules(self):
        for name in "abcde":
            self.write(f"test_{name}.py", GOOD)
