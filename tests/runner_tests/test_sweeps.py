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


class SweepsMixin:
    def test_a_module_that_exits_the_listing_with_status_0_at_import_exits_2_with_no_plan(self):
        self.write("test_good.py", GOOD)
        self.write("test_leave.py", "import os\nos._exit(0)\n")
        result = self.runner()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stderr, f"run_tests: the listing of {self.tests} left no plan the runner can read\n")

    def test_a_module_that_sleeps_at_import_past_a_2_second_timeout_exits_2_with_its_output_and_no_listing_process(self):
        self.write("marks.py", MARKS)
        self.write("test_hang.py", HANG_AT_IMPORT.format(escape=False))
        proc = self.start("--timeout", "2")
        listing = self.pid_from("listing")
        result = self.finish(proc, limit=20)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stderr, f"run_tests: the listing of {self.tests} exceeded the limit of 2 s\nimporting\n")
        self.assert_gone(listing)
        self.assertEqual(os.listdir(self.scratch), [])

    @unittest.skipUnless(LINUX, NEEDS_LINUX)
    def test_a_listing_killed_at_a_2_second_timeout_exits_2_and_ends_a_process_it_started_in_a_new_session(self):
        self.write("marks.py", MARKS)
        self.write("test_hang.py", HANG_AT_IMPORT.format(escape=True))
        proc = self.start("--timeout", "2")
        child = self.pid_from("child")
        result = self.finish(proc, limit=20)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assert_gone(child)

    @unittest.skipUnless(LINUX, NEEDS_LINUX)
    def test_sigterm_while_a_module_sleeps_at_import_exits_130_and_ends_the_listing_and_a_process_it_started_in_a_new_session(self):
        self.write("marks.py", MARKS)
        self.write("test_hang.py", HANG_AT_IMPORT.format(escape=True))
        proc = self.start()
        child, listing = self.pid_from("child"), self.pid_from("listing")
        proc.send_signal(signal.SIGTERM)
        result = self.finish(proc, limit=20)
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertEqual(result.stderr, "run_tests: interrupted\n")
        self.assert_gone(listing)
        self.assert_gone(child)
        self.assertEqual(os.listdir(self.scratch), [])

    @unittest.skipUnless(LINUX, NEEDS_LINUX)
    def test_sigint_exits_130_and_ends_a_process_a_test_started_in_a_new_session(self):
        result, child = self.escape(seconds=60, sig=signal.SIGINT)
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertEqual(result.stderr.splitlines()[-1], "run_tests: interrupted")
        self.assert_gone(child)
        self.assertEqual(os.listdir(self.scratch), [])

    @unittest.skipUnless(LINUX, NEEDS_LINUX)
    def test_sigterm_exits_130_and_ends_a_process_a_test_started_in_a_new_session(self):
        result, child = self.escape(seconds=60, sig=signal.SIGTERM)
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertEqual(result.stderr.splitlines()[-1], "run_tests: interrupted")
        self.assert_gone(child)
        self.assertEqual(os.listdir(self.scratch), [])

    @unittest.skipUnless(LINUX, NEEDS_LINUX)
    def test_a_run_that_kills_a_worker_at_a_2_second_timeout_ends_a_process_its_test_started_in_a_new_session(self):
        result, child = self.escape("--timeout", "2", seconds=60)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("The worker for shard 1 exceeded the limit of 2 s while this test was running.\n", result.stderr)
        self.assert_gone(child)

    @unittest.skipUnless(LINUX, NEEDS_LINUX)
    def test_a_passing_run_ends_a_process_a_test_started_in_a_new_session(self):
        result, child = self.escape(seconds=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.splitlines()[-1], "OK")
        self.assert_gone(child)

    @unittest.skipUnless(LINUX, NEEDS_LINUX)
    def test_a_passing_run_ends_a_process_in_a_new_session_whose_parent_exited_during_the_test(self):
        self.write("marks.py", MARKS)
        self.write("test_orphan.py", """
            import unittest

            import marks

            class OrphanTest(unittest.TestCase):
                def test_orphan(self):
                    marks.mark("child", marks.orphan())
        """)
        result = self.runner()
        child = self.pid_from("child")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.splitlines()[-1], "OK")
        self.assert_gone(child)

    def test_a_killed_process_whose_parent_exited_is_gone_within_10_seconds_while_its_test_still_runs(self):
        self.write("marks.py", MARKS)
        self.write("test_orphan.py", """
            import os
            import signal
            import time
            import unittest

            import marks

            class OrphanTest(unittest.TestCase):
                def test_orphan(self):
                    child = marks.orphan()
                    marks.mark("child", child)
                    os.kill(child, signal.SIGKILL)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        try:
                            os.kill(child, 0)
                        except ProcessLookupError:
                            return
                        time.sleep(0.01)
                    self.fail("the killed process is still there")
        """)
        result = self.runner()
        self.pid_from("child")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.splitlines()[-1], "OK")

    @unittest.skipUnless(LINUX, NEEDS_LINUX)
    def test_a_passing_run_leaves_alone_a_child_its_process_had_before_the_run(self):
        self.write("test_good.py", GOOD)
        launcher = (
            "import os, subprocess, sys\n"
            "from pathlib import Path\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', sys.argv[1]],\n"
            "                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "Path(sys.argv[1], 'child').write_text(str(child.pid))\n"
            "os.execv(sys.executable, [sys.executable, *sys.argv[2:]])\n"
        )
        result = subprocess.run([sys.executable, "-c", launcher, str(self.proj), str(RUNNER), "-s", str(self.tests)],
                                capture_output=True, text=True, timeout=60, env={**os.environ, "TMPDIR": str(self.scratch)})
        child = self.pid_from("child")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_alive(child)
        self.assertEqual(result.stderr.splitlines()[0], INHERITED)

    @unittest.skipUnless(LINUX, NEEDS_LINUX)
    def test_a_passing_run_whose_process_had_a_child_says_so_and_leaves_alone_that_childs_orphan_in_a_new_session(self):
        result, sleeper = self.inherited(seconds=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.splitlines()[-1], "OK")
        self.assert_alive(sleeper)
        self.assertEqual(result.stderr.splitlines()[0], INHERITED)

    @unittest.skipUnless(LINUX, NEEDS_LINUX)
    def test_sigint_on_a_run_whose_process_had_a_child_exits_130_and_leaves_alone_that_childs_orphan_in_a_new_session(self):
        result, sleeper = self.inherited(seconds=60, sig=signal.SIGINT)
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertEqual(result.stderr.splitlines()[-1], "run_tests: interrupted")
        self.assert_alive(sleeper)
        self.assertEqual(result.stderr.splitlines()[0], INHERITED)

    @unittest.skipUnless(LINUX, NEEDS_LINUX)
    def test_a_run_whose_process_had_a_child_and_that_kills_a_worker_at_a_2_second_timeout_leaves_alone_that_childs_orphan_in_a_new_session(self):
        result, sleeper = self.inherited("--timeout", "2", seconds=60)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("The worker for shard 1 exceeded the limit of 2 s while this test was running.\n", result.stderr)
        self.assert_alive(sleeper)
        self.assertEqual(result.stderr.splitlines()[0], INHERITED)

    def test_a_run_keeps_a_scratch_directory_whose_pid_field_is_superscript_two(self):
        self.write("test_good.py", GOOD)
        kept = self.scratch / "run-tests-²-x"
        kept.mkdir()
        result = self.runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.splitlines()[-1], "OK")
        self.assertTrue(kept.is_dir())


class SweepMixin:
    def test_sweep_given_no_time_returns_a_live_child_and_given_time_ends_it(self):
        script = (
            "import subprocess, sys\n"
            f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
            "import run_tests\n"
            "watch = run_tests.Watch()\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(watch.sweep(0) == [child.pid], watch.sweep(), child.pid)\n"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        first, second, pid = result.stdout.split()
        self.assertEqual((first, second), ("True", "[]"))
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid), 0)

