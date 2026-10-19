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


class ResultFilesMixin:
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

    def test_permission_error_for_an_empty_group_does_not_hide_a_result_file_error(self):
        self.write("test_result_file.py", RESULT_FILE.format(body=AT_EXIT.format(act="remove")))
        wrapper = (
            "import errno, os, runpy, sys\n"
            "killpg = os.killpg\n"
            "def macos_killpg(pgid, sig):\n"
            "    try:\n"
            "        killpg(pgid, sig)\n"
            "    except ProcessLookupError:\n"
            "        raise PermissionError(errno.EPERM, 'simulated macOS empty group')\n"
            "os.killpg = macos_killpg\n"
            "sys.argv = sys.argv[1:]\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n"
        )
        result = self.finish(self.start(before=("-c", wrapper)))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("\nERROR: shard 1 of test_result_file\n", result.stderr)
        self.assertEqual(RAN.search(result.stderr).group(1), "1")
        self.assert_stopped_and_none_lost(result.stderr, r"cannot be found at its path\. \[Errno 2\] [^\n]*results\.jsonl'")

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

