import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_tests.py"
sys.path.insert(0, str(ROOT / "scripts"))

import run_tests

RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
LINUX = sys.platform.startswith("linux")
NEEDS_LINUX = "the runner sweeps the processes below it on Linux only"

PASSING = """
    import unittest

    class AlphaTest(unittest.TestCase):
        def test_one(self):
            self.assertEqual(1, 1)

        def test_two(self):
            self.assertEqual(2, 2)
"""


GOOD = """
    import unittest

    class GoodTest(unittest.TestCase):
        def test_good(self):
            pass
"""

# A temporary project's tests import this to tell the test that owns the project which processes they started.
MARKS = """
    import os
    import subprocess
    import sys
    from pathlib import Path

    PROJECT = Path(__file__).resolve().parents[1]
    SLEEPER = ["-c", "import time; time.sleep(60)", str(PROJECT)]
    PARENT = (
        "import subprocess, sys\\n"
        "child = subprocess.Popen([sys.executable, *sys.argv[1:]], start_new_session=True,\\n"
        "                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\\n"
        "print(child.pid)\\n"
    )

    def mark(name, pid):
        (PROJECT / (name + ".tmp")).write_text(str(pid))
        os.replace(PROJECT / (name + ".tmp"), PROJECT / name)

    def orphan():
        parent = subprocess.run([sys.executable, "-c", PARENT, *SLEEPER], capture_output=True, text=True, check=True)
        return int(parent.stdout)
"""

ESCAPE = """
    import subprocess
    import sys
    import time
    import unittest

    import marks

    class EscapeTest(unittest.TestCase):
        def test_escape(self):
            child = subprocess.Popen([sys.executable, *marks.SLEEPER], start_new_session=True)
            marks.mark("child", child.pid)
            time.sleep({seconds})
"""

HANG_AT_IMPORT = """
    import os
    import subprocess
    import sys
    import time

    import marks

    if {escape}:
        marks.mark("child", subprocess.Popen([sys.executable, *marks.SLEEPER], start_new_session=True).pid)
    marks.mark("listing", os.getpid())
    print("importing", flush=True)
    time.sleep(60)

    import unittest

    class ProbeTest(unittest.TestCase):
        def test_probe(self):
            pass
"""

# Each line is written before a passing test ends, so the test's own result follows it.
WRITES_TO_ITS_RESULT_FILE = """
    import json
    import sys
    import unittest

    class WriteTest(unittest.TestCase):
        def test_write(self):
            spec = json.load(open(sys.argv[sys.argv.index("--worker") + 1]))
            with open(spec["results"], "a") as out:
                for line in {lines!r}:
                    out.write(line + "\\n")
"""

NEVER_AN_EVENT = """
    import json
    import sys
    import time
    import unittest

    class JunkTest(unittest.TestCase):
        def test_junk(self):
            spec = json.load(open(sys.argv[sys.argv.index("--worker") + 1]))
            with open(spec["results"], "a") as out:
                for _ in range(1200):
                    out.write({text!r})
                    out.flush()
                    time.sleep(0.05)
"""

# {body} is DURING_THE_TEST or AT_EXIT with remove, replace, or shorten for {act}.
RESULT_FILE = """
    import atexit
    import os
    import shutil
    import time
    import unittest
    from pathlib import Path

    # The runner gives a worker one directory. It holds the result file, the worker's output, and TMPDIR.
    HOME = Path(os.environ["TMPDIR"]).parent
    RESULTS = HOME / "results.jsonl"

    def remove():
        for entry in HOME.iterdir():
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()

    def replace():
        for entry in HOME.iterdir():
            if entry.is_file():
                copy = entry.with_name(entry.name + ".copy")
                shutil.copyfile(entry, copy)
                os.replace(copy, entry)

    def shorten():
        # One second is 20 of the runner's poll intervals. The test relies on the runner reading in that time what the worker has written.
        time.sleep(1)
        os.truncate(RESULTS, 8)

    class ResultFileTest(unittest.TestCase):
        def test_result_file(self):
            {body}
"""
DURING_THE_TEST = "{act}(); time.sleep(60)"
AT_EXIT = "atexit.register({act})"

# Argument 1 is the project directory. The launcher starts a child that starts a sleeper in a new session, then
# becomes the command in its other arguments. The child ends when allow-parent-exit appears in the project.
LAUNCHER_CHILD = (
    "import os, subprocess, sys, time\n"
    "from pathlib import Path\n"
    "root = Path(sys.argv[1])\n"
    "sleeper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', str(root)], start_new_session=True,\n"
    "                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "(root / 'sleeper.tmp').write_text(str(sleeper.pid))\n"
    "os.replace(root / 'sleeper.tmp', root / 'sleeper')\n"
    "while not (root / 'allow-parent-exit').exists():\n"
    "    time.sleep(.01)\n"
)
LAUNCHER = (
    "import os, subprocess, sys, time\n"
    "from pathlib import Path\n"
    "root = Path(sys.argv[1])\n"
    f"child = subprocess.Popen([sys.executable, '-c', {LAUNCHER_CHILD!r}, str(root)],\n"
    "                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
    "(root / 'parent').write_text(str(child.pid))\n"
    "while not (root / 'sleeper').exists():\n"
    "    time.sleep(.01)\n"
    "os.execv(sys.executable, [sys.executable, *sys.argv[2:]])\n"
)

# Lets the launcher's child end, waits until it has ended, and then sleeps {seconds}.
RELEASES_THE_PARENT = """
    import time
    import unittest
    from pathlib import Path

    PROJECT = Path(__file__).resolve().parents[1]

    def ended(pid):
        try:
            return Path(f"/proc/{{pid}}/stat").read_text().rsplit(") ", 1)[1][0] == "Z"
        except FileNotFoundError:
            return True

    class ReleaseTest(unittest.TestCase):
        def test_release(self):
            parent = int((PROJECT / "parent").read_text())
            (PROJECT / "allow-parent-exit").touch()
            deadline = time.monotonic() + 20
            while not ended(parent):
                self.assertLess(time.monotonic(), deadline, "the launcher's child is still running")
                time.sleep(0.01)
            (PROJECT / "released").touch()
            time.sleep({seconds})
"""
INHERITED = "run_tests: this process had a child before the run, so the runner ends only the process groups of the children it starts"


class RunTestsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.proj = Path(tmp.name).resolve() / "proj"
        self.tests = self.proj / "tests"
        self.tests.mkdir(parents=True)
        # The runner makes its scratch directory here.
        self.scratch = Path(tmp.name).resolve() / "scratch"
        self.scratch.mkdir()

    def write(self, name, body):
        (self.tests / name).write_text(textwrap.dedent(body))

    def start(self, *args, env=None, before=()):
        proc = subprocess.Popen([sys.executable, *before, str(RUNNER), "-s", str(self.tests), "-j", "2", *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env={**(env or os.environ), "TMPDIR": str(self.scratch)}, start_new_session=True)
        self.addCleanup(lambda: (proc.stdout.close(), proc.stderr.close()))
        return proc

    def finish(self, proc, limit=60):
        try:
            out, err = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            self.fail(f"the runner was still running after {limit} s")
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)

    def runner(self, *args, env=None, limit=60):
        return self.finish(self.start(*args, env=env), limit)

    def serial(self):
        return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=self.proj,
                              capture_output=True, text=True)

    def serial_count(self):
        return RAN.search(self.serial().stderr).group(1)

    def end(self, pid):
        try:
            if str(self.proj).encode() in Path(f"/proc/{pid}/cmdline").read_bytes():
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    def pid_from(self, name, limit=30):
        """Return the pid code in the temporary project wrote with marks.mark. At cleanup, end that process where /proc shows the project path in its command line."""
        path = self.proj / name
        deadline = time.monotonic() + limit
        while not path.exists():
            self.assertLess(time.monotonic(), deadline, f"{name} was not written")
            time.sleep(0.02)
        pid = int(path.read_text())
        self.addCleanup(self.end, pid)
        return pid

    def assert_gone(self, pid):
        with self.assertRaises(ProcessLookupError, msg=f"process {pid} is still there"):
            os.kill(pid, 0)

    def assert_alive(self, pid):
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1][0]
        except FileNotFoundError:
            state = "gone"
        self.assertIn(state, ("S", "R"), f"process {pid}")

    def inherited(self, *args, seconds, sig=None):
        """Run RELEASES_THE_PARENT in a runner whose process had a child before the run. Return the runner's result and the pid of that child's sleeper."""
        self.write("test_release.py", RELEASES_THE_PARENT.format(seconds=seconds))
        proc = self.start(*args, before=("-c", LAUNCHER, str(self.proj)))
        sleeper = self.pid_from("sleeper")
        self.pid_from("parent")
        if sig is not None:
            deadline = time.monotonic() + 30
            while not (self.proj / "released").exists():
                self.assertLess(time.monotonic(), deadline, "the test did not let the launcher's child end")
                time.sleep(0.02)
            proc.send_signal(sig)
        return self.finish(proc), sleeper

    def result_file(self, act, body):
        """Run RESULT_FILE with that act and body. Return the runner's stderr."""
        self.write("test_result_file.py", RESULT_FILE.format(body=body.format(act=act)))
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nERROR: shard 1 of test_result_file\n", result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "1")
        return result.stderr

    def assert_stopped_and_lost(self, stderr, what):
        self.assertRegex(stderr, r"\nThe worker for shard 1 was killed by signal 9 before the runner read that it had finished\.\n"
                                 rf"Its result file {what}\. The runner stopped reading it\.\n")
        self.assertIn("\nLOST: test_result_file.ResultFileTest.test_result_file\n" + "-" * 70 + "\n"
                      "The runner stopped reading the result file of shard 1 before it read a result for this test.\n", stderr)
        self.assertTrue(stderr.endswith("\n\nFAILED (errors=1, lost=1)\n"), stderr)

    def assert_stopped_and_none_lost(self, stderr, what):
        self.assertRegex(stderr, rf"\nIts result file {what}\. The runner stopped reading it\.\n")
        self.assertTrue(stderr.endswith("\n\nFAILED (errors=1)\n"), stderr)

    def escape(self, *args, seconds, sig=None):
        """Run a test that starts a sleeper in a new session. Return the runner's result and the sleeper's pid."""
        self.write("marks.py", MARKS)
        self.write("test_escape.py", ESCAPE.format(seconds=seconds))
        proc = self.start(*args)
        child = self.pid_from("child")
        if sig is not None:
            proc.send_signal(sig)
        return self.finish(proc), child

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

    def test_25_tests_with_one_id_run_once_each_in_3_shards(self):
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
        self.assertEqual(result.stderr.splitlines()[0], "run_tests: 25 tests, 3 shards, 2 at a time")
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

    def test_a_line_that_is_not_json_in_a_result_file_exits_1_with_the_line_the_test_count_and_no_lost_test(self):
        self.write("test_good.py", GOOD)
        self.write("test_garbage.py", WRITES_TO_ITS_RESULT_FILE.format(lines=["garbage"]))
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nERROR: shard 1 of test_garbage\n" + "-" * 70 + "\n"
                      "Line 2 of its result file is not an event. It reads 'garbage'.\n", result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "2")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1)\n"), result.stderr)

    def test_json_lines_of_the_wrong_shape_in_a_result_file_exit_1_with_each_line_and_the_test_count(self):
        lines = ['[1]', '{"ev": "nope"}', '{"ev": "result", "seq": 0, "status": "fine", "problems": []}', '{"ev": "done", "seq": 0}']
        self.write("test_good.py", GOOD)
        self.write("test_garbage.py", WRITES_TO_ITS_RESULT_FILE.format(lines=lines))
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        for number, line in enumerate(lines, 2):
            self.assertIn(f"\nLine {number} of its result file is not an event. It reads {line!r}.\n", result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "2")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1)\n"), result.stderr)

    def test_a_test_that_removes_everything_in_the_directory_of_its_result_file_exits_1_with_an_error_for_its_shard_and_is_lost(self):
        self.assert_stopped_and_lost(self.result_file("remove", DURING_THE_TEST),
                                     r"cannot be found at its path\. \[Errno 2\] [^\n]*results\.jsonl'")

    def test_a_test_that_replaces_its_result_file_and_each_file_beside_it_with_a_copy_exits_1_with_an_error_for_its_shard_and_is_lost(self):
        self.assert_stopped_and_lost(self.result_file("replace", DURING_THE_TEST), "was replaced by another file at its path")

    def test_a_test_that_cuts_its_result_file_to_8_bytes_after_1_second_exits_1_with_an_error_for_its_shard_and_is_lost(self):
        self.assert_stopped_and_lost(self.result_file("shorten", DURING_THE_TEST),
                                     r"was shortened to 8 bytes after the runner had read \d+")

    def test_an_exit_handler_that_removes_everything_in_the_directory_of_the_result_file_exits_1_with_an_error_for_its_shard_and_no_lost_test(self):
        self.assert_stopped_and_none_lost(self.result_file("remove", AT_EXIT),
                                          r"cannot be found at its path\. \[Errno 2\] [^\n]*results\.jsonl'")

    def test_an_exit_handler_that_replaces_the_result_file_and_each_file_beside_it_with_a_copy_exits_1_with_an_error_for_its_shard_and_no_lost_test(self):
        self.assert_stopped_and_none_lost(self.result_file("replace", AT_EXIT), "was replaced by another file at its path")

    def test_an_exit_handler_that_cuts_the_result_file_to_8_bytes_after_1_second_exits_1_with_an_error_for_its_shard_and_no_lost_test(self):
        self.assert_stopped_and_none_lost(self.result_file("shorten", AT_EXIT),
                                          r"was shortened to 8 bytes after the runner had read \d+")

    def test_a_passing_result_a_test_writes_for_the_failing_test_after_it_exits_1_with_an_error_for_its_shard(self):
        self.write("test_forge.py", """
            import json
            import sys
            import unittest

            class ForgeTest(unittest.TestCase):
                def test_a_forges(self):
                    spec = json.load(open(sys.argv[sys.argv.index("--worker") + 1]))
                    with open(spec["results"], "a") as out:
                        for event in ({"ev": "start"}, {"ev": "result", "status": "ok", "problems": []}):
                            out.write(json.dumps({**event, "seq": max(spec["seqs"])}) + "\\n")

                def test_b_fails(self):
                    self.fail("the real outcome")
        """)
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nERROR: shard 1 of test_forge\n" + "-" * 70 + "\n"
                      "The worker started a test it had already reported. The test is test_forge.ForgeTest.test_b_fails.\n"
                      "The worker reported a second result for one test. The test is test_forge.ForgeTest.test_b_fails.\n",
                      result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "2")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1)\n"), result.stderr)

    def test_a_result_a_test_writes_for_a_seq_outside_its_shard_exits_1_with_an_error_for_its_shard(self):
        self.write("test_garbage.py", WRITES_TO_ITS_RESULT_FILE.format(
            lines=['{"ev": "start", "seq": 7}', '{"ev": "result", "seq": 7, "status": "ok", "problems": []}']))
        result = self.runner()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nERROR: shard 1 of test_garbage\n" + "-" * 70 + "\n"
                      "The worker reported a test it was not given. The test is seq 7.\n", result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "1")
        self.assertTrue(result.stderr.endswith("\n\nFAILED (errors=1)\n"), result.stderr)

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


class BuildShardsTest(unittest.TestCase):
    def test_every_seq_is_in_exactly_one_shard_and_each_shard_holds_1_to_10_seqs_of_one_module_in_ascending_order(self):
        for sizes in ([1], [10], [11], [25, 3], [100, 1, 10, 7, 31]):
            with self.subTest(sizes=sizes):
                # Round-robin, so the tests of different modules interleave in the plan while each has tests left.
                modules = [f"m{index}" for turn in range(max(sizes)) for index, size in enumerate(sizes) if turn < size]
                plan = tuple(run_tests.Test(seq, "one.id", module, f"{module}.C{seq}") for seq, module in enumerate(modules))
                shards = run_tests.build_shards(plan)
                self.assertEqual(sorted(seq for shard in shards for seq in shard.seqs), list(range(len(plan))))
                self.assertEqual([shard.number for shard in shards], list(range(1, len(shards) + 1)))
                for shard in shards:
                    self.assertIn(len(shard.seqs), range(1, 11))
                    self.assertEqual({plan[seq].module for seq in shard.seqs}, {shard.module})
                    self.assertEqual(list(shard.seqs), sorted(shard.seqs))
                    self.assertEqual(shard.classes, tuple(plan[seq].cls for seq in shard.seqs))

    def test_no_test_makes_no_shard(self):
        self.assertEqual(run_tests.build_shards(()), [])


class AccountTest(unittest.TestCase):
    OK = {"ev": "result", "status": "ok", "problems": []}
    SHARD = run_tests.Shard(2, "test_x", (4, 5), ("test_x.A", "test_x.B"))
    ONE = run_tests.Shard(2, "test_x", (4,), ("test_x.A",))
    SKIPPED_A = {"ev": "skipped", "method": "setUpClass", "name": "test_x.A"}
    SKIPPED_B = {"ev": "skipped", "method": "setUpClass", "name": "test_x.B"}
    UNSUPPORTED = ("reported a test as behind a skipped fixture with no earlier report that the setUpClass of its class "
                   "or the setUpModule of its module raised SkipTest")

    def test_a_seq_with_no_event_from_a_worker_that_finished_is_lost(self):
        events = [{"ev": "start", "seq": 4}, {**self.OK, "seq": 4}, {"ev": "done"}]
        accounting = run_tests.account(self.SHARD, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "ok")
        self.assertEqual(accounting.verdicts[5].kind, "lost")
        self.assertEqual(accounting.verdicts[5].cause,
                         "The worker for shard 2 finished without an accepted result for this test.")
        self.assertEqual(accounting.violations, ())
        self.assertEqual(accounting.ending, "")

    def test_a_seq_behind_a_fixture_that_skipped_is_skipped_and_the_seq_with_no_event_beside_it_is_lost(self):
        events = [self.SKIPPED_A, {"ev": "behind", "seq": 4, "skip": True}, {"ev": "done"}]
        accounting = run_tests.account(self.SHARD, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "skip")
        self.assertEqual(accounting.verdicts[5].kind, "lost")
        self.assertEqual(accounting.violations, ())

    def test_a_seq_reported_behind_a_skipped_fixture_is_skipped_after_a_skipped_event_for_its_class_or_module_and_else_a_violation_and_lost(self):
        behind = {"ev": "behind", "seq": 5, "skip": True}
        module = {"ev": "skipped", "method": "setUpModule", "name": "test_x"}
        cases = {
            "no skipped event": ([behind], "lost"),
            "the setUpClass of another class": ([self.SKIPPED_A, behind], "lost"),
            "the setUpModule of another module": ([{**module, "name": "test_y"}, behind], "lost"),
            "a setUpModule with the name of its class": ([{**module, "name": "test_x.B"}, behind], "lost"),
            "a setUpClass with the name of its module": ([{**self.SKIPPED_B, "name": "test_x"}, behind], "lost"),
            "the skipped event after it": ([behind, self.SKIPPED_B], "lost"),
            "the setUpClass of its class": ([self.SKIPPED_B, behind], "skip"),
            "the setUpModule of its module": ([module, behind], "skip"),
        }
        for case, (events, kind) in cases.items():
            with self.subTest(case=case):
                accounting = run_tests.account(self.SHARD, [*events, {"ev": "done"}], run_tests.Ended(0))
                self.assertEqual(accounting.verdicts[5].kind, kind)
                self.assertEqual(accounting.violations, ((5, self.UNSUPPORTED),) if kind == "lost" else ())

    def test_a_start_and_a_result_after_a_refused_behind_event_give_the_seq_that_result(self):
        events = [{"ev": "behind", "seq": 4, "skip": True}, {"ev": "start", "seq": 4}, {**self.OK, "seq": 4}, {"ev": "done"}]
        accounting = run_tests.account(self.ONE, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "ok")
        self.assertEqual(accounting.violations, ((4, self.UNSUPPORTED),))

    def test_a_seq_with_no_result_when_the_runner_stopped_reading_is_lost_with_that_cause_and_a_seq_with_a_result_keeps_it(self):
        events = [{"ev": "start", "seq": 4}, {**self.OK, "seq": 4}, {"ev": "start", "seq": 5}]
        accounting = run_tests.account(self.SHARD, events, run_tests.Ended(-9), stopped=True)
        self.assertEqual(accounting.verdicts[4].kind, "ok")
        self.assertEqual(accounting.verdicts[5].kind, "lost")
        self.assertEqual(accounting.verdicts[5].cause,
                         "The runner stopped reading the result file of shard 2 before it read a result for this test.")
        self.assertEqual(accounting.ending,
                         "The worker for shard 2 was killed by signal 9 before the runner read that it had finished.")

    def test_a_worker_the_runner_stopped_reading_after_its_done_event_has_no_ending_with_status_0_and_an_ending_when_killed(self):
        events = [{"ev": "start", "seq": 4}, {**self.OK, "seq": 4}, {"ev": "done"}]
        self.assertEqual(run_tests.account(self.ONE, events, run_tests.Ended(0), stopped=True).ending, "")
        self.assertEqual(run_tests.account(self.ONE, events, run_tests.Ended(-9), stopped=True).ending,
                         "The worker for shard 2 was killed by signal 9 after it reported that it had finished.")

    def test_a_seq_behind_a_fixture_that_failed_is_lost_with_the_fixture_cause(self):
        events = [{"ev": "fixture", "label": "setUpClass (test_x.A)", "traceback": "RuntimeError\n"},
                  {"ev": "behind", "seq": 4, "skip": False}, self.SKIPPED_B, {"ev": "behind", "seq": 5, "skip": True},
                  {"ev": "done"}]
        accounting = run_tests.account(self.SHARD, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "lost")
        self.assertEqual(accounting.verdicts[4].cause,
                         "A class or module fixture failed in the worker for shard 2, and this test did not start.")
        self.assertEqual(accounting.verdicts[5].kind, "skip")
        self.assertEqual(accounting.fixtures, (run_tests.Problem("error", "setUpClass (test_x.A)", "RuntimeError\n"),))

    def test_a_result_for_a_seq_the_shard_was_not_given_is_a_violation_and_no_verdict(self):
        events = [{"ev": "start", "seq": 4}, {**self.OK, "seq": 4}, {"ev": "start", "seq": 9}, {**self.OK, "seq": 9},
                  {"ev": "done"}]
        accounting = run_tests.account(self.ONE, events, run_tests.Ended(0))
        self.assertEqual(sorted(accounting.verdicts), [4])
        self.assertEqual(accounting.verdicts[4].kind, "ok")
        self.assertEqual(accounting.violations, ((9, "reported a test it was not given"),))

    def test_a_second_result_for_one_seq_is_a_violation_and_the_first_result_stands(self):
        bad = {"ev": "result", "seq": 4, "status": "bad",
               "problems": [{"kind": "fail", "label": "test_a (test_x.T)", "traceback": "AssertionError\n"}]}
        events = [{"ev": "start", "seq": 4}, bad, {**self.OK, "seq": 4}, {"ev": "done"}]
        accounting = run_tests.account(self.ONE, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "bad")
        self.assertEqual(accounting.verdicts[4].problems[0].traceback, "AssertionError\n")
        self.assertEqual(accounting.violations, ((4, "reported a second result for one test"),))

    def test_a_result_for_a_seq_with_no_start_is_a_violation_and_the_seq_is_lost(self):
        events = [{**self.OK, "seq": 4}, {"ev": "start", "seq": 5}, {**self.OK, "seq": 5}, {"ev": "done"}]
        accounting = run_tests.account(self.SHARD, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "lost")
        self.assertEqual(accounting.verdicts[5].kind, "ok")
        self.assertEqual(accounting.violations, ((4, "reported a result for a test it had not started"),))

    def test_a_start_for_a_seq_behind_a_fixture_is_a_violation_and_the_seq_stays_skipped(self):
        events = [self.SKIPPED_A, {"ev": "behind", "seq": 4, "skip": True}, {"ev": "start", "seq": 4}, {"ev": "done"}]
        accounting = run_tests.account(self.ONE, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "skip")
        self.assertEqual(accounting.violations, ((4, "started a test it had already reported"),))

    def test_a_result_after_the_done_event_is_a_violation_and_the_seq_is_lost(self):
        events = [{"ev": "done"}, {"ev": "start", "seq": 4}, {**self.OK, "seq": 4}]
        accounting = run_tests.account(self.ONE, events, run_tests.Ended(0))
        self.assertEqual(accounting.verdicts[4].kind, "lost")
        self.assertEqual(accounting.violations, ((4, "wrote an event after it reported that it had finished"),))

    def test_the_ending_is_empty_only_for_a_done_event_with_status_0_inside_the_limit(self):
        done = [{"ev": "start", "seq": 4}, {**self.OK, "seq": 4}, {"ev": "done"}]
        shard = self.ONE
        endings = {
            (True, 0, None): "",
            (True, 3, None): "The worker for shard 2 exited with status 3 after it reported that it had finished.",
            (True, -9, None): "The worker for shard 2 was killed by signal 9 after it reported that it had finished.",
            (True, -9, 2.0): "The worker for shard 2 exceeded the limit of 2 s after it reported that it had finished.",
            (True, 0, 2.0): "The worker for shard 2 exceeded the limit of 2 s after it reported that it had finished.",
            (False, 0, None): "The worker for shard 2 exited with status 0 before it reported that it had finished.",
            (False, 3, None): "The worker for shard 2 exited with status 3 before it reported that it had finished.",
            (False, -9, None): "The worker for shard 2 was killed by signal 9 before it reported that it had finished.",
            (False, -9, 2.0): "The worker for shard 2 exceeded the limit of 2 s before it reported that it had finished.",
            (False, 0, 2.0): "The worker for shard 2 exceeded the limit of 2 s before it reported that it had finished.",
        }
        for (finished, status, over), ending in endings.items():
            with self.subTest(finished=finished, status=status, over=over):
                accounting = run_tests.account(shard, done if finished else done[:2], run_tests.Ended(status, over))
                self.assertEqual(accounting.ending, ending)
                self.assertEqual(accounting.verdicts[4].kind, "ok")


class ParseEventTest(unittest.TestCase):
    def test_each_kind_of_event_parses_to_its_fields(self):
        events = [
            {"ev": "start", "seq": 3},
            {"ev": "result", "seq": 3, "status": "ok", "problems": []},
            {"ev": "result", "seq": 3, "status": "bad", "problems": [{"kind": "fail", "label": "a", "traceback": "b"}]},
            {"ev": "behind", "seq": 3, "skip": True},
            {"ev": "skipped", "method": "setUpClass", "name": "a.B"},
            {"ev": "fixture", "label": "setUpClass (a.B)", "traceback": "c"},
            {"ev": "done"},
        ]
        for event in events:
            with self.subTest(event=event):
                self.assertEqual(run_tests.parse_event(json.dumps(event).encode()), event)

    def test_a_line_that_is_no_event_raises_valueerror(self):
        lines = [
            b"", b"garbage", b"\xff\xfe", b"[" * 100000, b"7", b"[1]", b"{}",
            b'{"ev": ["start"]}', b'{"ev": "nope"}',
            b'{"ev": "start"}', b'{"ev": "start", "seq": "3"}', b'{"ev": "start", "seq": 3.0}',
            b'{"ev": "start", "seq": true}', b'{"ev": "start", "seq": 3, "more": 1}',
            b'{"ev": "done", "seq": 3}',
            b'{"ev": "behind", "seq": 3, "skip": 1}',
            b'{"ev": "skipped", "method": "setUpClass"}', b'{"ev": "skipped", "method": "setUpClass", "name": 3}',
            b'{"ev": "fixture", "label": "a"}',
            b'{"ev": "result", "seq": 3, "status": "lost", "problems": []}',
            b'{"ev": "result", "seq": 3, "status": "bad", "problems": []}',
            b'{"ev": "result", "seq": 3, "status": "ok", "problems": [{"kind": "fail", "label": "a", "traceback": "b"}]}',
            b'{"ev": "result", "seq": 3, "status": "bad", "problems": [{"kind": "lost", "label": "a", "traceback": "b"}]}',
            b'{"ev": "result", "seq": 3, "status": "bad", "problems": ["a"]}',
        ]
        for line in lines:
            with self.subTest(line=line[:60]):
                with self.assertRaises(ValueError):
                    run_tests.parse_event(line)


@unittest.skipUnless(LINUX, NEEDS_LINUX)
class SweepTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
