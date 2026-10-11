import contextlib
import inspect
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check  # noqa: E402
import measure_capacity  # noqa: E402


def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
    saved = dict(os.environ)

    def restore():
        os.environ.clear()
        os.environ.update(saved)

    unittest.addModuleCleanup(restore)
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull


def setUpModule():
    fence_git_environment()


def link_findings(*targets):
    body = "\n".join(f"[link]({target})" for target in targets)
    with tempfile.TemporaryDirectory() as directory:
        skill = Path(directory) / "demo"
        skill.mkdir()
        (skill / "other.md").write_text("other\n")
        (skill / "SKILL.md").write_text(f"---\nname: demo\ndescription: d\n---\n\n{body}\n")
        return [finding for finding in check.check_tree(directory) if "broken link" in finding]


FENCE_HEADER = "import os\nimport unittest\n\n\n"
FENCE_SOURCE = inspect.getsource(fence_git_environment)
SETUP_SOURCE = inspect.getsource(setUpModule)


def tests_tree(directory, modules):
    """A directory named tests holding test_landing.py and the given modules, each a source string."""
    tests = Path(directory) / "tests"
    tests.mkdir()
    reference = FENCE_HEADER + FENCE_SOURCE + "\n\n" + SETUP_SOURCE
    for name, source in {"test_landing.py": reference, **modules}.items():
        (tests / name).write_text(source)
    return tests


COPY = "Copy fence_git_environment and setUpModule from tests/test_landing.py"


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
                [f"tests/test_drift.py: fence_git_environment differs from the one in tests/test_landing.py. {COPY}"],
            )

    def test_a_fence_whose_docstring_differs_is_named(self):
        edited = FENCE_SOURCE.replace("Remove every GIT_* variable", "Drop every GIT_* variable")
        self.assertNotEqual(edited, FENCE_SOURCE)
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {"test_words.py": FENCE_HEADER + edited + "\n\n" + SETUP_SOURCE})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/test_words.py: fence_git_environment differs from the one in tests/test_landing.py. {COPY}"],
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
            (tests / "test_landing.py").write_bytes(b"# \xe9\n")
            self.assertEqual(
                check.fence_findings(tests),
                ["tests/test_landing.py: is missing, cannot be read or parsed, or has no def fence_git_environment directly in the module body, so no test module can be compared with it"],
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
            (tests / "test_landing.py").write_text("import unittest\n")
            self.assertEqual(
                check.fence_findings(tests),
                ["tests/test_landing.py: is missing, cannot be read or parsed, or has no def fence_git_environment directly in the module body, so no test module can be compared with it"],
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


class LinkCheckTest(unittest.TestCase):
    def test_a_thread_link_with_an_id_is_not_a_file_link(self):
        self.assertEqual(link_findings("t3-thread://v1/abc-123", "other.md"), [])

    def test_other_targets_still_must_resolve(self):
        self.assertEqual(
            link_findings("missing.md", "t3-thread://v2/abc", "t3-thread://v1/"),
            [
                "demo/SKILL.md: broken link missing.md",
                "demo/SKILL.md: broken link t3-thread://v2/abc",
                "demo/SKILL.md: broken link t3-thread://v1/",
            ],
        )


class FrontmatterWithoutPyYamlTest(unittest.TestCase):
    def test_without_pyyaml_a_malformed_flow_sequence_is_not_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: demo\ndescription: [unterminated\n---\n\nbody\n")
            with mock.patch.object(check, "yaml", None):
                self.assertEqual(check.check_tree(directory), [])


class BuildProcessCounterTest(unittest.TestCase):
    def test_a_python_process_running_the_build_script_the_test_runner_or_unittest_counts(self):
        counted = [
            ["python3", "scripts/run_tests.py"],
            ["/usr/bin/python3.12", "/work/pstack-t3/scripts/run_tests.py", "--worker", "spec.json"],
            ["python3", "/work/pstack-t3/scripts/build.py"],
            ["python3", "-m", "unittest", "discover", "-s", "tests"],
        ]
        for args in counted:
            self.assertTrue(measure_capacity.is_build(args), args)

    def test_other_processes_do_not_count(self):
        not_counted = [
            ["python3", "scripts/check.py"],
            ["python3", "other/run_tests.py"],
            ["bash", "-c", "python3 scripts/run_tests.py"],
            ["node", "/work/pstack-t3/scripts/run_tests.py"],
            ["python3", "-m", "pytest"],
        ]
        for args in not_counted:
            self.assertFalse(measure_capacity.is_build(args), args)


class SamplerOutputTest(unittest.TestCase):
    def test_a_sampler_stopped_early_keeps_the_rows_it_printed(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "rows.tsv"
            with out.open("w") as handle:
                sampler = subprocess.Popen(
                    [sys.executable, str(ROOT / "scripts" / "measure_capacity.py"), "--label", "x", "--samples", "1000", "--interval", "1"],
                    stdout=handle,
                )
                try:
                    deadline = time.time() + 5
                    while len(out.read_text().splitlines()) < 2 and time.time() < deadline:
                        time.sleep(0.1)
                finally:
                    sampler.terminate()
                    sampler.wait()
            lines = out.read_text().splitlines()
        self.assertEqual(lines[0].split("\t"), list(measure_capacity.COLUMNS))
        self.assertEqual(lines[1].split("\t")[0], "x")


if __name__ == "__main__":
    unittest.main()
