if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from .support import (
    setUpModule,
    check,
    ROOT,
    FENCE_HEADER,
    FENCE_SOURCE,
    SETUP_SOURCE,
    tests_tree,
    COPY,
)


class FenceCheckTest(unittest.TestCase):
    def test_every_test_module_in_this_repository_carries_the_fence(self):
        self.assertEqual(check.fence_findings(ROOT / "tests"), [])

    def test_a_module_with_the_fence_and_a_setup_that_does_other_work_too_passes(self):
        source = FENCE_HEADER + FENCE_SOURCE + "\n\ndef setUpModule():\n    prepare()\n    fence_git_environment()\n    prepare()\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {"test_other.py": source})
            self.assertEqual(check.fence_findings(tests), [])

    def test_a_module_without_the_fence_is_named(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {"test_plain.py": "import unittest\n\n\nclass T(unittest.TestCase):\n    pass\n"})
            self.assertEqual(
                check.fence_findings(tests),
                [
                    f"tests/test_plain.py: has no def fence_git_environment directly in the module body. {COPY}",
                    f"tests/test_plain.py: has no def setUpModule directly in the module body. {COPY}",
                ],
            )

    def test_a_fence_defined_inside_a_class_is_not_directly_in_the_module_body(self):
        indented = "".join(f"    {line}" if line.strip() else line for line in FENCE_SOURCE.splitlines(keepends=True))
        source = FENCE_HEADER + "class Helpers:\n" + indented + "\n\n" + SETUP_SOURCE
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {"test_nested.py": source})
            self.assertEqual(check.fence_findings(tests), [f"tests/test_nested.py: has no def fence_git_environment directly in the module body. {COPY}"])

    def test_a_fence_whose_body_differs_is_named(self):
        drifted = FENCE_SOURCE.replace('    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull\n', "")
        self.assertNotEqual(drifted, FENCE_SOURCE)
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {"test_drift.py": FENCE_HEADER + drifted + "\n\n" + SETUP_SOURCE})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/test_drift.py: fence_git_environment differs from the one in tests/landing/support.py. {COPY}"],
            )

    def test_a_fence_whose_docstring_differs_is_named(self):
        edited = FENCE_SOURCE.replace("Remove every GIT_* variable", "Drop every GIT_* variable")
        self.assertNotEqual(edited, FENCE_SOURCE)
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {"test_words.py": FENCE_HEADER + edited + "\n\n" + SETUP_SOURCE})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/test_words.py: fence_git_environment differs from the one in tests/landing/support.py. {COPY}"],
            )

    def test_comments_and_blank_lines_in_the_fence_do_not_count_as_a_difference(self):
        commented = FENCE_SOURCE.replace("    saved = dict(os.environ)\n", "    # kept for the cleanup\n\n    saved = dict(os.environ)\n")
        self.assertNotEqual(commented, FENCE_SOURCE)
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {"test_commented.py": FENCE_HEADER + commented + "\n\n" + SETUP_SOURCE})
            self.assertEqual(check.fence_findings(tests), [])

    def test_a_module_with_the_fence_and_no_setup_is_named(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {"test_unset.py": FENCE_HEADER + FENCE_SOURCE})
            self.assertEqual(check.fence_findings(tests), [f"tests/test_unset.py: has no def setUpModule directly in the module body. {COPY}"])

    def test_a_setup_that_never_calls_the_fence_is_named(self):
        source = FENCE_HEADER + FENCE_SOURCE + "\n\ndef setUpModule():\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {"test_idle.py": source})
            self.assertEqual(check.fence_findings(tests), [f"tests/test_idle.py: setUpModule contains no call to the bare name fence_git_environment. {COPY}"])

    def test_a_module_that_cannot_be_read_or_parsed_is_named(self):
        sources = {"test_syntax.py": b"def (:\n", "test_bytes.py": b"# \xe9\n"}
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {})
            for name, source in sources.items():
                (tests / name).write_bytes(source)
            findings = check.fence_findings(tests)
            self.assertEqual(len(findings), 2)
            for name, finding in zip(sorted(sources), findings):
                self.assertTrue(finding.startswith(f"tests/{name}: could not be read or parsed, so its git environment fence was not checked: "), finding)

    def test_a_reference_module_that_cannot_be_read_fails_the_whole_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {})
            (tests / "landing" / "support.py").write_bytes(b"# \xe9\n")
            self.assertEqual(
                check.fence_findings(tests),
                ["tests/landing/support.py: is missing, cannot be read or parsed, or has no def fence_git_environment directly in the module body, so no test module can be compared with it"],
            )

    def test_a_directory_named_like_a_test_module_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {})
            (tests / "test_dir.py").mkdir()
            self.assertEqual(check.fence_findings(tests), [])

    def test_a_fence_rebound_by_assignment_after_its_def_is_not_seen(self):
        source = FENCE_HEADER + FENCE_SOURCE + "\n\n" + SETUP_SOURCE + "\n\nfence_git_environment = lambda: None\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {"test_rebound.py": source})
            self.assertEqual(check.fence_findings(tests), [])

    def test_files_outside_the_test_module_pattern_are_not_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {"helpers.py": "x = 1\n", "test_data.txt": "x\n"})
            (tests / "fixtures").mkdir()
            (tests / "fixtures" / "test_inner.py").write_text("x = 1\n")
            self.assertEqual(check.fence_findings(tests), [])

    def test_a_reference_module_without_the_fence_fails_the_whole_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {})
            (tests / "landing" / "support.py").write_text("import unittest\n")
            self.assertEqual(
                check.fence_findings(tests),
                ["tests/landing/support.py: is missing, cannot be read or parsed, or has no def fence_git_environment directly in the module body, so no test module can be compared with it"],
            )

    def test_the_command_exits_non_zero_and_names_the_module(self):
        with tempfile.TemporaryDirectory() as directory:
            skills = Path(directory) / "skills"
            skills.mkdir()
            tests = tests_tree(directory, {"test_plain.py": "x = 1\n"})
            output = io.StringIO()
            with mock.patch.object(check, "TESTS_DIR", tests), contextlib.redirect_stdout(output):
                status = check.main([str(skills)])
            self.assertEqual(status, 1)
            self.assertIn(f"tests/test_plain.py: has no def fence_git_environment directly in the module body. {COPY}", output.getvalue().splitlines())

    def test_the_command_prints_ok_and_exits_zero_when_every_module_has_the_fence(self):
        with tempfile.TemporaryDirectory() as directory:
            skills = Path(directory) / "skills"
            skills.mkdir()
            tests = tests_tree(directory, {})
            output = io.StringIO()
            with mock.patch.object(check, "TESTS_DIR", tests), contextlib.redirect_stdout(output):
                status = check.main([str(skills)])
            self.assertEqual((status, output.getvalue()), (0, "ok\n"))
