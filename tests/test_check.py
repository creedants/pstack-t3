import contextlib
import inspect
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


def support_source(fence=None):
    """support.py with the landing fence, unless fence replaces that function source, plus class Doc."""
    body = FENCE_SOURCE if fence is None else fence
    return FENCE_HEADER + body + "\n\n" + SETUP_SOURCE + "\n\nclass Doc:\n    pass\n"


def brigade_tree(directory, support, modules):
    """tests_tree plus tests/brigade and the given modules. support.py is written only when support is not None."""
    tests = tests_tree(directory, {})
    brigade = tests / "brigade"
    brigade.mkdir()
    if support is not None:
        (brigade / "support.py").write_text(support)
    for name, source in modules.items():
        (brigade / name).write_text(source)
    return tests


COPY = "Copy fence_git_environment and setUpModule from tests/test_landing.py"
BRIGADE_IMPORT = "Import setUpModule from tests/brigade/support.py"
AREA_IMPORT = (
    "from .support import (\n"
    "    setUpModule,\n"
    "    runpy,\n"
    ")\n"
    "\n"
    "\n"
    "class AreaTest(unittest.TestCase):\n"
    "    def test_one(self):\n"
    "        pass\n"
)
IMPORTS_ONLY = "from .support import (\n    Doc,\n)\n"
README_AT_1_0_0 = (
    "Preamble keeps 1.0.0 and upstream pstack.\n"
    '  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="license MIT"></a>\n'
    '  <a href="upstream.json"><img src="https://img.shields.io/badge/upstream%20pstack-1.0.0-555.svg" alt="upstream pstack 1.0.0"></a>\n'
    "Postscript keeps 1.0.0.\n"
)
DISAGREE_1_0_0 = "README.md badge url 1.0.0 alt 1.0.0 disagrees with upstream.json version 1.1.0"
ABSENT_1_1_0 = "README.md has no upstream badge and upstream.json version is 1.1.0"


def badge_line(url, alt):
    return (
        f'  <a href="upstream.json"><img src="https://img.shields.io/badge/upstream%20pstack-{url}-555.svg" '
        f'alt="upstream pstack {alt}"></a>\n'
    )


def checker_checkout(root, readme):
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(ROOT / "scripts/check.py", scripts / "check.py")
    roles = root / "t3/scripts"
    roles.mkdir(parents=True)
    shutil.copy(ROOT / "t3/scripts/roles.py", roles / "roles.py")
    skill = root / "skills/demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\nbody\n")
    tests_tree(root, {})
    (root / "upstream.json").write_text(json.dumps({"version": "1.1.0"}) + "\n")
    if readme is not None:
        (root / "README.md").write_text(readme)


def run_check(root):
    return subprocess.run([sys.executable, "scripts/check.py"], cwd=root, capture_output=True, text=True)


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


class BrigadeFenceCheckTest(unittest.TestCase):
    def test_support_with_the_landing_fence_and_an_area_import_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": AREA_IMPORT})
            self.assertEqual(check.fence_findings(tests), [])

    def test_an_absolute_import_of_setup_from_tests_brigade_support_passes(self):
        source = "from tests.brigade.support import setUpModule\n\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(check.fence_findings(tests), [])

    def test_an_area_module_that_defines_the_landing_fence_itself_passes(self):
        source = FENCE_HEADER + FENCE_SOURCE + "\n\n" + SETUP_SOURCE + "\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(check.fence_findings(tests), [])

    def test_support_whose_fence_differs_is_named(self):
        drifted = FENCE_SOURCE.replace('    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull\n', "")
        self.assertNotEqual(drifted, FENCE_SOURCE)
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(drifted), {"test_area.py": AREA_IMPORT})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/brigade/support.py: fence_git_environment differs from the one in tests/test_landing.py. {COPY}"],
            )

    def test_an_area_module_with_no_setup_is_named(self):
        source = "from .support import Doc\n\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/brigade/test_area.py: has no setUpModule bound directly in the module body. {BRIGADE_IMPORT}"],
            )

    def test_a_local_setup_that_never_calls_the_fence_is_named(self):
        source = "class AreaTest(unittest.TestCase):\n    pass\n\n\ndef setUpModule():\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [
                    f"tests/brigade/test_area.py: has no def fence_git_environment directly in the module body. {BRIGADE_IMPORT}",
                    f"tests/brigade/test_area.py: setUpModule contains no call to the bare name fence_git_environment. {BRIGADE_IMPORT}",
                ],
            )

    def test_setup_imported_under_another_name_is_named(self):
        source = "from .support import setUpModule as start\n\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/brigade/test_area.py: imports setUpModule under another name. {BRIGADE_IMPORT}"],
            )

    def test_setup_bound_only_inside_a_block_is_named(self):
        source = "class AreaTest(unittest.TestCase):\n    pass\n\n\nif True:\n    from .support import setUpModule\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/brigade/test_area.py: has no setUpModule bound directly in the module body. {BRIGADE_IMPORT}"],
            )

    def test_setup_imported_from_another_module_is_named(self):
        source = "from tests.other import setUpModule\n\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/brigade/test_area.py: binds setUpModule by an import that is not from tests.brigade.support. {BRIGADE_IMPORT}"],
            )

    def test_setup_bound_by_assignment_is_named(self):
        source = "setUpModule = None\n\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/brigade/test_area.py: binds setUpModule to something other than an import from tests.brigade.support. {BRIGADE_IMPORT}"],
            )

    def test_an_async_setup_after_a_valid_import_is_named(self):
        source = (
            "from .support import setUpModule, fence_git_environment\n"
            "import os\n"
            "import unittest\n"
            "\n"
            "async def setUpModule():\n"
            "    fence_git_environment()\n"
            "\n"
            "class AreaTest(unittest.IsolatedAsyncioTestCase):\n"
            "    async def test_sentinel(self):\n"
            '        self.assertIn("GIT_D202_REVIEW_PROBE", os.environ)\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/brigade/test_area.py: binds setUpModule to something other than an import from tests.brigade.support. {BRIGADE_IMPORT}"],
            )

    def test_a_class_named_setup_after_a_valid_import_is_named(self):
        source = "from .support import setUpModule\n\n\nclass setUpModule:\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/brigade/test_area.py: binds setUpModule to something other than an import from tests.brigade.support. {BRIGADE_IMPORT}"],
            )

    def test_a_value_free_annotation_after_a_valid_import_passes(self):
        source = (
            "from .support import setUpModule\n"
            "from typing import Callable\n"
            "import os\n"
            "import unittest\n"
            "\n"
            "setUpModule: Callable[[], None]\n"
            "\n"
            "class AreaTest(unittest.TestCase):\n"
            "    def test_fence(self):\n"
            '        self.assertNotIn("GIT_D202_REVIEW_PROBE", os.environ)\n'
        )
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(check.fence_findings(tests), [])

    def test_an_annotated_assignment_of_setup_is_named(self):
        source = "from .support import setUpModule\n\nsetUpModule: Callable[[], None] = None\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/brigade/test_area.py: binds setUpModule to something other than an import from tests.brigade.support. {BRIGADE_IMPORT}"],
            )

    def test_a_plain_import_that_binds_setup_is_named(self):
        source = "import setUpModule\n\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/brigade/test_area.py: binds setUpModule by an import that is not from tests.brigade.support. {BRIGADE_IMPORT}"],
            )

    def test_a_plain_import_that_renames_setup_away_leaves_the_name_unbound(self):
        source = "import setUpModule as start\n\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/brigade/test_area.py: has no setUpModule bound directly in the module body. {BRIGADE_IMPORT}"],
            )

    def test_a_module_that_only_imports_support_classes_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_docs_like.py": IMPORTS_ONLY})
            self.assertEqual(check.fence_findings(tests), [])

    def test_a_module_that_imports_a_support_function_and_no_setup_is_named(self):
        source = "from .support import fence_git_environment\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/brigade/test_area.py: has no setUpModule bound directly in the module body. {BRIGADE_IMPORT}"],
            )

    def test_a_missing_brigade_directory_is_not_a_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {})
            self.assertEqual(check.fence_findings(tests), [])

    def test_a_brigade_directory_without_support_is_named(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, None, {})
            self.assertEqual(
                check.fence_findings(tests),
                ["tests/brigade/support.py: could not be read or parsed, so its git environment fence was not checked: FileNotFoundError"],
            )

    def test_a_nested_brigade_module_is_not_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = brigade_tree(directory, support_source(), {"test_area.py": AREA_IMPORT})
            nested = tests / "brigade" / "nested"
            nested.mkdir()
            (nested / "test_deep.py").write_text("x = 1\n")
            self.assertEqual(check.fence_findings(tests), [])


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


class PlaybookBulletCheckTest(unittest.TestCase):
    def test_a_demo_skill_has_no_playbook_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\nbody\n")
            self.assertEqual(check.check_tree(directory), [])

    def test_the_command_reports_a_missing_playbook_file(self):
        with tempfile.TemporaryDirectory() as directory:
            skills = Path(directory) / "skills"
            skill = skills / "poteto-mode"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: poteto-mode\ndescription: d\n---\n\n## Playbooks\n- **No such playbook.** Nothing.\n")
            tests = tests_tree(directory, {})
            output = io.StringIO()
            with mock.patch.object(check, "TESTS_DIR", tests), contextlib.redirect_stdout(output):
                status = check.main([str(skills)])
            self.assertEqual(
                (status, output.getvalue()),
                (1, 'poteto-mode/SKILL.md: playbook "No such playbook" resolves to playbooks/no-such-playbook.md, which does not exist\n'),
            )

    def test_the_command_accepts_an_indented_note_under_a_playbook_bullet(self):
        with tempfile.TemporaryDirectory() as directory:
            skills = Path(directory) / "skills"
            skill = skills / "poteto-mode"
            (skill / "playbooks").mkdir(parents=True)
            (skill / "playbooks/bug-fix.md").write_text("ok\n")
            (skill / "SKILL.md").write_text(
                "---\nname: poteto-mode\ndescription: d\n---\n\n"
                "## Playbooks\n"
                "- **Bug fix.** File: `playbooks/bug-fix.md`.\n"
                "    - Choose this when the defect is already reproduced.\n"
            )
            tests = tests_tree(directory, {})
            output = io.StringIO()
            with mock.patch.object(check, "TESTS_DIR", tests), contextlib.redirect_stdout(output):
                status = check.main([str(skills)])
            self.assertEqual((status, output.getvalue()), (0, "ok\n"))


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


class BadgeCheckTest(unittest.TestCase):
    def test_check_py_names_an_older_badge_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checker_checkout(root, README_AT_1_0_0)
            result = run_check(root)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (1, DISAGREE_1_0_0 + "\n", ""))
            self.assertEqual((root / "README.md").read_text(), README_AT_1_0_0)

    def test_check_py_names_a_missing_badge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checker_checkout(root, None)
            result = run_check(root)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (1, ABSENT_1_1_0 + "\n", ""))
            self.assertFalse((root / "README.md").exists())

    def test_badge_findings_name_each_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream = root / "upstream.json"
            upstream.write_text(json.dumps({"version": "1.1.0"}) + "\n")
            readme = root / "README.md"
            cases = (
                ("url", badge_line("1.0.0", "1.1.0"), ["README.md badge url 1.0.0 alt 1.1.0 disagrees with upstream.json version 1.1.0"]),
                ("alt", badge_line("1.1.0", "1.0.0"), ["README.md badge url 1.1.0 alt 1.0.0 disagrees with upstream.json version 1.1.0"]),
                ("both", badge_line("1.0.0", "1.2.0"), ["README.md badge url 1.0.0 alt 1.2.0 disagrees with upstream.json version 1.1.0"]),
                ("agreement", badge_line("1.1.0", "1.1.0"), []),
                ("two lines", badge_line("1.1.0", "1.1.0") + badge_line("1.0.0", "1.0.0"), [ABSENT_1_1_0]),
                ("not a badge", "not a badge\n", [ABSENT_1_1_0]),
            )
            for name, text, expected in cases:
                with self.subTest(slot=name):
                    readme.write_text(text)
                    self.assertEqual(check.badge_findings(readme, upstream), expected)

    def test_badge_findings_read_a_crlf_readme(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upstream = root / "upstream.json"
            upstream.write_text(json.dumps({"version": "1.1.0"}) + "\n")
            readme = root / "README.md"
            readme.write_bytes(README_AT_1_0_0.replace("\n", "\r\n").encode())
            self.assertEqual(check.badge_findings(readme, upstream), [DISAGREE_1_0_0])
            agreed = README_AT_1_0_0.replace("upstream%20pstack-1.0.0", "upstream%20pstack-1.1.0").replace(
                "upstream pstack 1.0.0", "upstream pstack 1.1.0"
            )
            readme.write_bytes(agreed.replace("\n", "\r\n").encode())
            self.assertEqual(check.badge_findings(readme, upstream), [])

    def test_check_tree_judges_only_the_given_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skills/demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\nbody\n")
            readme = root / "README.md"
            upstream = root / "upstream.json"
            readme.write_text(README_AT_1_0_0)
            upstream.write_text(json.dumps({"version": "1.1.0"}) + "\n")
            with mock.patch.object(check, "README_PATH", readme), mock.patch.object(check, "UPSTREAM_PATH", upstream):
                self.assertEqual(check.check_tree(root / "skills"), [])
                self.assertEqual(check.badge_findings(readme, upstream), [DISAGREE_1_0_0])

    def test_an_explicit_tree_gets_no_repository_badge_finding(self):
        with tempfile.TemporaryDirectory() as directory:
            skills = Path(directory) / "skills"
            skills.mkdir()
            tests = tests_tree(directory, {})
            readme = Path(directory) / "README.md"
            upstream = Path(directory) / "upstream.json"
            readme.write_text(README_AT_1_0_0)
            upstream.write_text(json.dumps({"version": "1.1.0"}) + "\n")
            output = io.StringIO()
            with mock.patch.object(check, "TESTS_DIR", tests), mock.patch.object(
                check, "README_PATH", readme
            ), mock.patch.object(check, "UPSTREAM_PATH", upstream), contextlib.redirect_stdout(output):
                status = check.main([str(skills)])
            self.assertEqual((status, output.getvalue()), (0, "ok\n"))


class CheckoutRuleTest(unittest.TestCase):
    def _directory(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def test_library_wording_allows_a_comment_a_docstring_and_assert_refused_choice(self):
        source = (
            '"""invalid choice\n'
            "the following arguments are required\n"
            "unrecognized arguments\n"
            "expected one argument\n"
            '"""\n'
            "# invalid choice\n"
            "# the following arguments are required\n"
            "# unrecognized arguments\n"
            "# expected one argument\n"
            "def assert_refused_choice():\n"
            "    text = 'invalid choice'\n"
            "    text = 'the following arguments are required'\n"
            "    text = 'unrecognized arguments'\n"
            "    text = 'expected one argument'\n"
        )
        tests = tests_tree(self._directory(), {"test_quiet.py": source})
        self.assertEqual(check.library_wording_findings(tests), [])

    def test_library_wording_reports_each_sentence(self):
        source = (
            "import unittest\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_refuses(self):\n"
            "        self.assertIn(\"invalid choice: 'guess'\", err)\n"
            "        self.assertIn('the following arguments are required: --name', err)\n"
            "        self.assertIn('unrecognized arguments: --runtime-mode auto', err)\n"
            "        self.assertIn('expected one argument', err)\n"
        )
        tests = tests_tree(self._directory(), {"test_sample.py": source})
        self.assertEqual(check.library_wording_findings(tests), [
            "tests/test_sample.py:5: contains the argparse phrase 'invalid choice'. Assert the exit status and the words this project wrote, or call assert_refused_choice",
            "tests/test_sample.py:6: contains the argparse phrase 'the following arguments are required'. Assert the exit status and the words this project wrote, or call assert_refused_choice",
            "tests/test_sample.py:7: contains the argparse phrase 'unrecognized arguments'. Assert the exit status and the words this project wrote, or call assert_refused_choice",
            "tests/test_sample.py:8: contains the argparse phrase 'expected one argument'. Assert the exit status and the words this project wrote, or call assert_refused_choice",
        ])
        narrowed = (
            'SENTENCE = "invalid choice"\n'
            "def helper():\n"
            "    return 'the following arguments are required'\n"
            "def test_library_wording_nested():\n"
            "    def helper():\n"
            "        return 'unrecognized arguments'\n"
            "    return helper\n"
            "def assert_refused_choice():\n"
            "    def helper():\n"
            "        return 'expected one argument'\n"
            "    return helper\n"
        )
        named = tests_tree(self._directory(), {"test_check.py": narrowed})
        self.assertEqual(check.library_wording_findings(named), [
            "tests/test_check.py:1: contains the argparse phrase 'invalid choice'. Assert the exit status and the words this project wrote, or call assert_refused_choice",
            "tests/test_check.py:3: contains the argparse phrase 'the following arguments are required'. Assert the exit status and the words this project wrote, or call assert_refused_choice",
            "tests/test_check.py:6: contains the argparse phrase 'unrecognized arguments'. Assert the exit status and the words this project wrote, or call assert_refused_choice",
        ])

    def _ops_repo(self, nightly, doc):
        root = self._directory()
        ops = root / "ops"
        ops.mkdir()
        (ops / "t3-nightly-update.sh").write_text(nightly)
        (ops / "t3-archive-threads.py").write_text("echo ok\n")
        (ops / "config.example").write_text("echo ok\n")
        (ops / "notes.txt").write_text("hello from the build\n")
        document = root / "docs" / "operations.md"
        document.parent.mkdir()
        document.write_text(doc)
        return ops, document

    def test_machine_values_are_quiet_on_benign_text(self):
        who = check.Identity("/opt/plant-home", "planter", "plantbox")
        ops, document = self._ops_repo("echo ok\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [])

    def test_machine_values_report_a_planted_home_directory(self):
        who = check.Identity("/opt/plant-home", "planter", "plantbox")
        ops, document = self._ops_repo("APPS=/opt/plant-home/apps\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            "ops/t3-nightly-update.sh:1: holds this machine's home directory",
        ])

    def test_dev_null_does_not_report_the_user_dev(self):
        who = check.Identity("/home/dev", "dev", "docs")
        ops, document = self._ops_repo("</dev/null\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [])

    def test_a_tilde_dev_reports_the_user(self):
        who = check.Identity("/home/dev", "dev", "docs")
        ops, document = self._ops_repo("see ~dev/notes\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            "ops/t3-nightly-update.sh:1: holds this machine's user name",
        ])

    def test_a_longer_root_path_reports_the_home_and_not_the_user(self):
        who = check.Identity("/root", "root", "x")
        ops, document = self._ops_repo("cd /root/foo\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            "ops/t3-nightly-update.sh:1: holds this machine's home directory",
        ])

    def test_a_quoted_ops_conf_does_not_report_the_host(self):
        who = check.Identity("/home/alice", "alice", "ops")
        ops, document = self._ops_repo('echo "ops.conf"\n', "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [])

    def test_ping_ops_lan_reports_the_host(self):
        who = check.Identity("/home/alice", "alice", "ops")
        ops, document = self._ops_repo("ping ops.lan\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            "ops/t3-nightly-update.sh:1: holds this machine's host name",
        ])

    def test_config_example_after_one_slash_does_not_report_the_host(self):
        who = check.Identity("/home/alice", "alice", "config")
        ops, document = self._ops_repo("cp ops/config.example dest\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [])

    def test_config_after_two_slashes_reports_the_host(self):
        who = check.Identity("/home/alice", "alice", "config")
        ops, document = self._ops_repo("open //config/share\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            "ops/t3-nightly-update.sh:1: holds this machine's host name",
        ])

    def test_dollar_app_prev_does_not_report_the_host(self):
        who = check.Identity("/home/alice", "alice", "app")
        ops, document = self._ops_repo("cp $APP.prev dest\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [])

    def test_app_before_a_port_reports_the_host(self):
        who = check.Identity("/home/alice", "alice", "app")
        ops, document = self._ops_repo("curl app:3773/x\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            "ops/t3-nightly-update.sh:1: holds this machine's host name",
        ])

    def test_a_quoted_dotted_host_reports_the_host(self):
        who = check.Identity("/home/zed", "zed", "box7.lan")
        text = 'ssh "box7.lan"\nHOST="box7.lan"\nserver = "box7.lan"\nssh "BOX7.LAN"\nssh "box7.lan:2222"\n'
        ops, document = self._ops_repo(text, "ok\n")
        hit = "ops/t3-nightly-update.sh:%d: holds this machine's host name"
        self.assertEqual(check.machine_value_findings(ops, document, who), [hit % n for n in range(1, 6)])

    def test_a_dotted_host_in_a_slash_path_reports_the_host(self):
        who = check.Identity("/home/zed", "zed", "box7.lan")
        text = "export BACKUPS=/mnt/box7.lan/archives\nscp backup:/box7.lan/data dest\n"
        ops, document = self._ops_repo(text, "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            "ops/t3-nightly-update.sh:1: holds this machine's host name",
            "ops/t3-nightly-update.sh:2: holds this machine's host name",
        ])

    def _pair(self, roles, install):
        root = self._directory()
        roles_path = root / "roles.py"
        install_path = root / "install.py"
        roles_path.write_text(roles)
        install_path.write_text(install)
        return roles_path, install_path

    def test_shared_source_matches_when_the_three_names_are_the_same(self):
        body = (
            'IGNORED = "same"\n'
            "def config_home(environ, default):\n"
            "    return environ\n"
            "def new_file(directory):\n"
            "    return directory\n"
        )
        roles_path, install_path = self._pair(body, body)
        self.assertEqual(check.shared_source_findings(roles_path, install_path), [])

    def test_shared_source_reports_a_comment_and_a_missing_name(self):
        install = (
            'IGNORED = "same"\n'
            "def config_home(environ, default):\n"
            "    return environ\n"
            "def new_file(directory):\n"
            "    return directory\n"
        )
        commented = (
            'IGNORED = "same"\n'
            "def config_home(environ, default):\n"
            "    # kept\n"
            "    return environ\n"
            "def new_file(directory):\n"
            "    return directory\n"
        )
        roles_path, install_path = self._pair(commented, install)
        self.assertEqual(check.shared_source_findings(roles_path, install_path), [
            "t3/scripts/roles.py: config_home differs from the one in scripts/install.py. Make the source text the same",
        ])
        missing = (
            "def config_home(environ, default):\n"
            "    return environ\n"
            "def new_file(directory):\n"
            "    return directory\n"
        )
        roles_path, install_path = self._pair(missing, install)
        self.assertEqual(check.shared_source_findings(roles_path, install_path), [
            "t3/scripts/roles.py: has no IGNORED. Define it with the same source text as scripts/install.py",
        ])

    def test_shared_source_reports_a_decorator_a_trailing_comment_or_an_unresolved_binding(self):
        same = (
            'IGNORED = "same"\n'
            "def config_home(environ, default):\n"
            "    return environ\n"
            "def new_file(directory):\n"
            "    return directory\n"
        )
        differs = "t3/scripts/roles.py: config_home differs from the one in scripts/install.py. Make the source text the same"
        cases = (
            (
                "a decorator on one function",
                'IGNORED = "same"\n'
                "def review_identity(function):\n"
                "    return function\n"
                "\n"
                "@review_identity\n"
                "def config_home(environ, default):\n"
                "    return environ\n"
                "def new_file(directory):\n"
                "    return directory\n",
                [differs],
            ),
            (
                "a trailing inline comment",
                'IGNORED = "same"\n'
                "def config_home(environ, default):\n"
                "    return environ  # review comment\n"
                "def new_file(directory):\n"
                "    return directory\n",
                [differs],
            ),
            (
                "an indented trailing comment",
                'IGNORED = "same"\n'
                "def config_home(environ, default):\n"
                "    return environ\n"
                "    # review comment\n"
                "def new_file(directory):\n"
                "    return directory\n",
                [differs],
            ),
            (
                "a later definition inside if",
                'IGNORED = "same"\n'
                "def config_home(environ, default):\n"
                "    return environ\n"
                "def new_file(directory):\n"
                "    return directory\n"
                "if True:\n"
                "    def config_home(environ, default):\n"
                "        return environ\n",
                ["t3/scripts/roles.py:7: config_home is bound where the static comparison cannot resolve it"],
            ),
            (
                "a later definition inside try",
                'IGNORED = "same"\n'
                "def config_home(environ, default):\n"
                "    return environ\n"
                "def new_file(directory):\n"
                "    return directory\n"
                "try:\n"
                "    def new_file(directory):\n"
                "        return directory\n"
                "except Exception:\n"
                "    pass\n",
                ["t3/scripts/roles.py:7: new_file is bound where the static comparison cannot resolve it"],
            ),
            (
                "a conditional assignment to IGNORED",
                'IGNORED = "same"\n'
                "def config_home(environ, default):\n"
                "    return environ\n"
                "def new_file(directory):\n"
                "    return directory\n"
                "if True:\n"
                '    IGNORED = "different"\n',
                ["t3/scripts/roles.py:7: IGNORED is bound where the static comparison cannot resolve it"],
            ),
            (
                "IGNORED +=",
                'IGNORED = "same"\n'
                "def config_home(environ, default):\n"
                "    return environ\n"
                "def new_file(directory):\n"
                "    return directory\n"
                'IGNORED += " different"\n',
                ["t3/scripts/roles.py:6: IGNORED is bound where the static comparison cannot resolve it"],
            ),
        )
        for label, roles, expected in cases:
            with self.subTest(label):
                roles_path, install_path = self._pair(roles, same)
                self.assertEqual(check.shared_source_findings(roles_path, install_path), expected)


if __name__ == "__main__":
    unittest.main()
