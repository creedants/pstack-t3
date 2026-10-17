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


def fragment_tree_findings(paths, vendor=()):
    """Write a temporary skills tree from tree-relative path to text and return the fragment check's findings.

    The vendor skill directory points at a temporary path with a directory per
    name in vendor, so no finding depends on this checkout.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for rel, text in paths.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        vendored = root / "vendor/pstack/skills"
        vendored.mkdir(parents=True)
        for name in vendor:
            (vendored / name).mkdir()
        with mock.patch.object(check, "VENDOR_SKILLS_PATH", vendored):
            return check.fragment_findings(root)


FENCE_HEADER = "import os\nimport unittest\n\n\n"
FENCE_SOURCE = inspect.getsource(fence_git_environment)
SETUP_SOURCE = inspect.getsource(setUpModule)


def tests_tree(directory, modules):
    """A directory named tests holding landing/support.py and the given modules, each a source string."""
    tests = Path(directory) / "tests"
    landing = tests / "landing"
    landing.mkdir(parents=True)
    reference = FENCE_HEADER + FENCE_SOURCE + "\n\n" + SETUP_SOURCE
    (landing / "support.py").write_text(reference)
    for name, source in modules.items():
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


def landing_tree(directory, support, modules):
    """tests_tree plus the given landing modules. support.py is absent when support is None."""
    tests = tests_tree(directory, {})
    landing = tests / "landing"
    if support is None:
        (landing / "support.py").unlink()
    else:
        (landing / "support.py").write_text(support)
    for name, source in modules.items():
        (landing / name).write_text(source)
    return tests


COPY = "Copy fence_git_environment and setUpModule from tests/landing/support.py"
BRIGADE_IMPORT = "Import setUpModule from tests/brigade/support.py"
LANDING_IMPORT = "Import setUpModule from tests/landing/support.py"
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
                [f"tests/brigade/support.py: fence_git_environment differs from the one in tests/landing/support.py. {COPY}"],
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


class LandingFenceCheckTest(unittest.TestCase):
    def test_support_with_the_landing_fence_and_an_area_import_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": AREA_IMPORT})
            self.assertEqual(check.fence_findings(tests), [])

    def test_an_absolute_import_of_setup_from_tests_landing_support_passes(self):
        source = "from tests.landing.support import setUpModule\n\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(check.fence_findings(tests), [])

    def test_an_area_module_that_defines_the_landing_fence_itself_passes(self):
        source = FENCE_HEADER + FENCE_SOURCE + "\n\n" + SETUP_SOURCE + "\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(check.fence_findings(tests), [])

    def test_a_support_fence_drift_redefines_the_reference(self):
        drifted = FENCE_SOURCE.replace('    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull\n', "")
        self.assertNotEqual(drifted, FENCE_SOURCE)
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(drifted), {"test_area.py": AREA_IMPORT})
            self.assertEqual(check.fence_findings(tests), [])

    def test_an_area_module_with_no_setup_is_named(self):
        source = "from .support import Doc\n\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/landing/test_area.py: has no setUpModule bound directly in the module body. {LANDING_IMPORT}"],
            )

    def test_a_local_setup_that_never_calls_the_fence_is_named(self):
        source = "class AreaTest(unittest.TestCase):\n    pass\n\n\ndef setUpModule():\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [
                    f"tests/landing/test_area.py: has no def fence_git_environment directly in the module body. {LANDING_IMPORT}",
                    f"tests/landing/test_area.py: setUpModule contains no call to the bare name fence_git_environment. {LANDING_IMPORT}",
                ],
            )

    def test_setup_imported_under_another_name_is_named(self):
        source = "from .support import setUpModule as start\n\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/landing/test_area.py: imports setUpModule under another name. {LANDING_IMPORT}"],
            )

    def test_setup_bound_only_inside_a_block_is_named(self):
        source = "class AreaTest(unittest.TestCase):\n    pass\n\n\nif True:\n    from .support import setUpModule\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/landing/test_area.py: has no setUpModule bound directly in the module body. {LANDING_IMPORT}"],
            )

    def test_setup_imported_from_another_module_is_named(self):
        source = "from tests.other import setUpModule\n\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/landing/test_area.py: binds setUpModule by an import that is not from tests.landing.support. {LANDING_IMPORT}"],
            )

    def test_setup_bound_by_assignment_is_named(self):
        source = "setUpModule = None\n\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/landing/test_area.py: binds setUpModule to something other than an import from tests.landing.support. {LANDING_IMPORT}"],
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
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/landing/test_area.py: binds setUpModule to something other than an import from tests.landing.support. {LANDING_IMPORT}"],
            )

    def test_a_class_named_setup_after_a_valid_import_is_named(self):
        source = "from .support import setUpModule\n\n\nclass setUpModule:\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/landing/test_area.py: binds setUpModule to something other than an import from tests.landing.support. {LANDING_IMPORT}"],
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
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(check.fence_findings(tests), [])

    def test_an_annotated_assignment_of_setup_is_named(self):
        source = "from .support import setUpModule\n\nsetUpModule: Callable[[], None] = None\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/landing/test_area.py: binds setUpModule to something other than an import from tests.landing.support. {LANDING_IMPORT}"],
            )

    def test_a_plain_import_that_binds_setup_is_named(self):
        source = "import setUpModule\n\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/landing/test_area.py: binds setUpModule by an import that is not from tests.landing.support. {LANDING_IMPORT}"],
            )

    def test_a_plain_import_that_renames_setup_away_leaves_the_name_unbound(self):
        source = "import setUpModule as start\n\n\nclass AreaTest(unittest.TestCase):\n    pass\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/landing/test_area.py: has no setUpModule bound directly in the module body. {LANDING_IMPORT}"],
            )

    def test_a_module_that_only_imports_support_classes_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_docs_like.py": IMPORTS_ONLY})
            self.assertEqual(check.fence_findings(tests), [])

    def test_a_module_that_imports_a_support_function_and_no_setup_is_named(self):
        source = "from .support import fence_git_environment\n"
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": source})
            self.assertEqual(
                check.fence_findings(tests),
                [f"tests/landing/test_area.py: has no setUpModule bound directly in the module body. {LANDING_IMPORT}"],
            )

    def test_a_missing_landing_directory_fails_the_whole_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = tests_tree(directory, {})
            shutil.rmtree(tests / "landing")
            self.assertEqual(
                check.fence_findings(tests),
                ["tests/landing/support.py: is missing, cannot be read or parsed, or has no def fence_git_environment directly in the module body, so no test module can be compared with it"],
            )

    def test_a_landing_directory_without_support_fails_the_whole_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, None, {})
            self.assertEqual(
                check.fence_findings(tests),
                ["tests/landing/support.py: is missing, cannot be read or parsed, or has no def fence_git_environment directly in the module body, so no test module can be compared with it"],
            )

    def test_a_nested_landing_module_is_not_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = landing_tree(directory, support_source(), {"test_area.py": AREA_IMPORT})
            nested = tests / "landing" / "nested"
            nested.mkdir()
            (nested / "test_deep.py").write_text("x = 1\n")
            self.assertEqual(check.fence_findings(tests), [])


class LinkCheckTest(unittest.TestCase):
    def test_a_thread_link_with_an_id_is_not_a_file_link(self):
        self.assertEqual(link_findings("t3-thread://v1/abc-123", "other.md"), [])

    def test_a_query_bearing_file_link_to_an_existing_file_is_not_broken(self):
        self.assertEqual(link_findings("other.md?raw=1"), [])
        self.assertEqual(link_findings("other.md?raw=1#x"), [])

    def test_other_targets_still_must_resolve(self):
        self.assertEqual(
            link_findings("missing.md", "t3-thread://v2/abc", "t3-thread://v1/"),
            [
                "demo/SKILL.md: broken link missing.md",
                "demo/SKILL.md: broken link t3-thread://v2/abc",
                "demo/SKILL.md: broken link t3-thread://v1/",
            ],
        )


class LightTableExemptionTest(unittest.TestCase):
    TABLE = (
        "| Spawn | Light behavior |\n"
        "| --- | --- |\n"
        "| `swarm` | Spawn at most 3 workers. |\n"
    )
    SKILL = "---\nname: pstack-runtime\ndescription: d\n---\n\n## Gate review\n\nCall `watch_pull_request`. Call `unwatch_pull_request`.\n"

    def test_a_light_table_in_light_md_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "pstack-runtime"
            runtime.mkdir(parents=True)
            (runtime / "SKILL.md").write_text(self.SKILL)
            (runtime / "light.md").write_text(
                "# Light behavior\n\nRead this file before every spawn and run the row for that spawn.\n\n" + self.TABLE
            )
            self.assertEqual(check.check_tree(root), [])

    def test_the_same_table_in_another_file_still_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "pstack-runtime"
            runtime.mkdir(parents=True)
            (runtime / "SKILL.md").write_text(self.SKILL)
            (runtime / "other.md").write_text(self.TABLE)
            findings = check.check_tree(root)
            self.assertTrue(
                any("light table restated" in finding and "pstack-runtime/other.md" in finding for finding in findings),
                findings,
            )

    def test_forbidden_patterns_still_apply_to_light_md(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "pstack-runtime"
            runtime.mkdir(parents=True)
            (runtime / "SKILL.md").write_text(self.SKILL)
            (runtime / "light.md").write_text("# Light behavior\n\nRun with run_in_background.\n")
            findings = check.check_tree(root)
            self.assertTrue(
                any("pstack-runtime/light.md" in finding and "Cursor Task flag" in finding for finding in findings),
                findings,
            )


class FragmentCheckTest(unittest.TestCase):
    def test_a_fragment_link_to_a_missing_skill_heading_is_reported(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "## Terms\n",
                    "brigade/verdict-protocol.md": "[the script](SKILL.md#the-script)\n",
                }
            ),
            ["brigade/verdict-protocol.md:1: fragment #the-script names no heading in brigade/SKILL.md"],
        )

    def test_a_fragment_link_to_a_present_skill_heading_passes(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "## Terms\n",
                    "brigade/verdict-protocol.md": "[the terms](SKILL.md#terms)\n",
                }
            ),
            [],
        )

    def test_a_fragment_link_to_a_missing_on_demand_heading_is_reported(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "[the protocol](verdict-protocol.md#verdict)\n",
                    "brigade/verdict-protocol.md": "## Rule\n",
                }
            ),
            ["brigade/SKILL.md:1: fragment #verdict names no heading in brigade/verdict-protocol.md"],
        )

    def test_a_fragment_link_to_a_present_on_demand_heading_passes(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "[the protocol](verdict-protocol.md#rule)\n",
                    "brigade/verdict-protocol.md": "## Rule\n",
                }
            ),
            [],
        )

    def test_an_in_file_fragment_link_to_a_missing_heading_is_reported(self):
        self.assertEqual(
            fragment_tree_findings({"pstack-runtime/SKILL.md": "## Modes\n[the roles](#roles)\n"}),
            ["pstack-runtime/SKILL.md:2: fragment #roles names no heading in pstack-runtime/SKILL.md"],
        )

    def test_an_in_file_fragment_link_to_a_present_heading_passes(self):
        self.assertEqual(
            fragment_tree_findings({"pstack-runtime/SKILL.md": "## Modes\n[the modes](#modes)\n"}),
            [],
        )

    def test_a_fragment_link_into_a_vendor_skill_is_not_checked(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "[the persona](../poteto-mode/agents/poteto-agent.md#missing-heading)\n",
                    "poteto-mode/agents/poteto-agent.md": "## Persona\n",
                },
                vendor=("poteto-mode",),
            ),
            [],
        )

    def test_a_fragment_link_inside_a_fence_is_not_checked(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "## Terms\n\n```\n[the rule](verdict-protocol.md#the-script)\n```\n",
                    "brigade/verdict-protocol.md": "## Rule\n",
                }
            ),
            [],
        )

    def test_a_tilde_fence_hides_a_missing_link(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "~~~md\n[example](#missing)\n~~~\n"}),
            [],
        )

    def test_a_longer_fence_hides_inner_fence_markers(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "````md\n```md\n[example](#missing)\n```\n````\n"}),
            [],
        )

    def test_a_heading_inside_a_tilde_fence_does_not_count(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "~~~md\n## Missing\n~~~\n[bad](#missing)\n"}),
            ["brigade/SKILL.md:4: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_fragment_link_inside_a_code_span_is_not_checked(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "Use `[example](#missing)` as syntax.\n"}),
            [],
        )

    def test_a_fence_inside_a_blockquote_hides_its_link(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "> ```md\n> [example](#missing)\n> ```\n"}),
            [],
        )

    def test_a_fence_inside_a_list_item_hides_its_link(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "1. Example\n\n    ```md\n    [example](#missing)\n    ```\n"}),
            [],
        )

    def test_a_fence_ends_at_the_next_list_item(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "1. Example\n\n   ```md\n   sample\n2. [bad](#missing)\n"}),
            ["brigade/SKILL.md:5: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_code_span_with_inner_backticks_leaves_a_later_link_visible(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "Use `` ``` `` to fence [bad](#missing), then `x`.\n"}),
            ["brigade/SKILL.md:1: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_link_title_with_an_escaped_quote_is_checked(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": '[bad](#missing "say \\"hi\\"")\n'}),
            ["brigade/SKILL.md:1: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_titled_fragment_link_to_a_missing_heading_is_reported(self):
        self.assertEqual(
            fragment_tree_findings({"pstack-runtime/SKILL.md": '## Terms\n[bad](#missing "title")\n'}),
            ["pstack-runtime/SKILL.md:2: fragment #missing names no heading in pstack-runtime/SKILL.md"],
        )

    def test_a_fragment_link_to_a_second_duplicate_heading_passes(self):
        self.assertEqual(
            fragment_tree_findings({"pstack-runtime/SKILL.md": "## Terms\n## Terms\n[second](#terms-1)\n"}),
            [],
        )

    def test_a_suffix_collision_allocates_the_next_free_anchor(self):
        self.assertEqual(
            fragment_tree_findings({"pstack-runtime/SKILL.md": "## Terms\n## Terms-1\n## Terms\n[third](#terms-2)\n"}),
            [],
        )

    def test_a_percent_encoded_fragment_to_a_present_heading_passes(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "## Caf\u00e9\n[ok](#caf%C3%A9)\n"}),
            [],
        )

    def test_a_percent_encoded_fragment_to_a_missing_heading_is_reported(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "## Terms\n[bad](#caf%C3%A9)\n"}),
            ["brigade/SKILL.md:2: fragment #caf\u00e9 names no heading in brigade/SKILL.md"],
        )

    def test_a_query_bearing_destination_to_a_present_heading_passes(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "[ok](verdict-protocol.md?raw=1#rule)\n",
                    "brigade/verdict-protocol.md": "## Rule\n",
                }
            ),
            [],
        )

    def test_a_query_bearing_destination_to_a_missing_heading_is_reported(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "[bad](verdict-protocol.md?raw=1#missing)\n",
                    "brigade/verdict-protocol.md": "## Rule\n",
                }
            ),
            ["brigade/SKILL.md:1: fragment #missing names no heading in brigade/verdict-protocol.md"],
        )

    def test_a_quote_marker_inside_a_quoted_fence_stays_in_code(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "> ```md\n> > ```\n> [example](#missing)\n> ```\n"}),
            [],
        )

    def test_a_quoted_fence_ends_before_a_reported_link(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "> ```md\n> > ```\n> ```\n> [bad](#missing)\n"}),
            ["brigade/SKILL.md:4: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_quote_marker_inside_a_fenced_list_item_stays_in_code(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "- ```md\n  > ```\n  [example](#missing)\n  ```\n"}),
            [],
        )

    def test_a_link_outside_a_fenced_list_item_is_still_reported(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "- ```md\n>   [bad](#missing)\n"}),
            ["brigade/SKILL.md:2: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_document_fence_after_a_quoted_list_is_not_a_list_fence(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "> - item\n\n  ```md\n[example](#missing)\n  ```\n"}),
            [],
        )

    def test_a_list_indent_does_not_reach_into_a_later_quote(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "- item\n>   ```md\n> [example](#missing)\n> ```\n"}),
            [],
        )

    def test_an_indented_heading_in_a_quoted_code_block_is_not_a_heading(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "- item\n>     ## Missing\n[bad](#missing)\n"}),
            ["brigade/SKILL.md:3: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_link_in_a_nested_quote_after_a_blank_quote_line_is_reported(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "> > ```md\n>\n> > [bad](#missing)\n"}),
            ["brigade/SKILL.md:3: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_fence_in_a_parent_list_ends_at_the_parent_boundary(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "- - item\n\n  ```md\n[bad](#missing)\n  ```\n"}),
            ["brigade/SKILL.md:4: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_check_tree_reports_a_fragment_that_names_no_heading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "brigade"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: brigade\ndescription: d\n---\n\n## Terms\n\n[the rule](verdict-protocol.md#the-script)\n"
            )
            (skill / "verdict-protocol.md").write_text("## Rule\n")
            self.assertIn(
                "brigade/SKILL.md:8: fragment #the-script names no heading in brigade/verdict-protocol.md",
                check.check_tree(root),
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

    def test_a_missing_process_directory_does_not_crash_before_the_header(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "rows.tsv"
            env = dict(os.environ, **{measure_capacity.PROC_ROOT_ENV: str(Path(directory) / "no-proc")})
            with out.open("w") as handle:
                result = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "measure_capacity.py"), "--label", "x", "--samples", "1", "--interval", "0"],
                    stdout=handle,
                    stderr=subprocess.PIPE,
                    env=env,
                    timeout=60,
                )
            lines = out.read_text().splitlines()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lines[0].split("\t"), list(measure_capacity.COLUMNS))
        self.assertEqual(len(lines), 2)
        row = dict(zip(measure_capacity.COLUMNS, lines[1].split("\t")))
        self.assertEqual(row["label"], "x")
        for name in measure_capacity.COLUMNS:
            if name not in ("label", "ts"):
                self.assertEqual(row[name], measure_capacity.UNAVAILABLE, name)


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


class SkillCountCheckTest(unittest.TestCase):
    def _tree(self, guide, skills=(), readme=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "pstack-runtime/scripts").mkdir(parents=True)
        (root / "pstack-runtime/scripts/roles.py").write_text("")
        for name in skills:
            skill = root / name
            skill.mkdir()
            (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n\nbody\n")
        document = root / "docs/guide.md"
        document.parent.mkdir()
        document.write_text(guide)
        patcher = mock.patch.object(check, "GUIDE_PATH", document)
        patcher.start()
        self.addCleanup(patcher.stop)
        if readme is None:
            readme = f"See all {len(skills)} skills and every playbook.\n"
        self._readme_states(root, readme)
        return root

    def _readme_states(self, root, readme):
        """Point README_PATH at a README holding the given text."""
        document = root / "README.md"
        document.write_text(readme)
        patcher = mock.patch.object(check, "README_PATH", document)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_guide_that_quotes_the_shipped_count_is_quiet(self):
        root = self._tree("`doctor` should show `3/3 pstack-t3` for each provider you use.\n", ("a", "b", "c"))
        self.assertEqual(check.check_tree(root), [])

    def test_a_stale_count_is_named_with_the_line_the_quote_and_the_shipped_count(self):
        root = self._tree("intro\n`doctor` should show `2/2 pstack-t3` for each provider you use.\n", ("a", "b", "c"))
        self.assertEqual(check.check_tree(root), [
            "docs/guide.md:2: quotes the doctor count 2/2, and the shipped skill count is 3",
        ])

    def test_a_quote_whose_two_numbers_differ_is_named(self):
        root = self._tree("`doctor` should show `3/4 pstack-t3` for each provider you use.\n", ("a", "b", "c"))
        self.assertEqual(check.check_tree(root), [
            "docs/guide.md:1: quotes the doctor count 3/4, and the shipped skill count is 3",
        ])

    def test_a_guide_that_quotes_no_count_is_named(self):
        root = self._tree("no count here\n", ("a",))
        self.assertEqual(check.check_tree(root), [
            "docs/guide.md: quotes no doctor count, and the shipped skill count is 1",
        ])

    def test_a_guide_that_cannot_be_read_is_named(self):
        root = self._tree("`doctor` should show `1/1 pstack-t3` for each provider you use.\n", ("a",))
        (root / "docs/guide.md").unlink()
        self.assertEqual(check.check_tree(root), [
            "docs/guide.md: could not be read, so its doctor count was not checked: FileNotFoundError",
        ])

    def test_an_explicit_tree_argument_gets_no_guide_finding(self):
        root = self._tree("intro\n`doctor` should show `2/2 pstack-t3` for each provider you use.\n", skills=("a", "b"))
        (root / "pstack-runtime/SKILL.md").write_text(
            "---\nname: pstack-runtime\ndescription: d\n---\n\n"
            "Call `watch_pull_request`. Call `unwatch_pull_request`.\n"
        )
        tests = tests_tree(root, {})
        output = io.StringIO()
        with mock.patch.object(check, "TESTS_DIR", tests), contextlib.redirect_stdout(output):
            status = check.main([str(root)])
        self.assertEqual((status, output.getvalue()), (0, "ok\n"))

    def test_a_readme_that_states_the_shipped_count_is_quiet(self):
        root = self._tree("`doctor` should show `2/2 pstack-t3` for each provider you use.\n", skills=("a", "b"))
        self.assertEqual(check.check_tree(root), [])

    def test_a_stale_readme_count_is_named_with_the_line_the_stated_count_and_the_shipped_count(self):
        root = self._tree(
            "`doctor` should show `3/3 pstack-t3` for each provider you use.\n",
            readme="See all 60 skills and every playbook.\n",
            skills=("a", "b", "c"),
        )
        self.assertEqual(check.check_tree(root), [
            "README.md:1: states all 60 skills, and the shipped skill count is 3",
        ])

    def test_a_readme_that_states_no_skill_count_is_named(self):
        root = self._tree("`doctor` should show `1/1 pstack-t3` for each provider you use.\n", skills=("a",))
        self._readme_states(root, "no count here\n")
        self.assertEqual(check.check_tree(root), [
            "README.md: states no skill count, and the shipped skill count is 1",
        ])

    def test_a_readme_that_cannot_be_read_is_named(self):
        root = self._tree("`doctor` should show `1/1 pstack-t3` for each provider you use.\n", skills=("a",))
        (root / "README.md").unlink()
        self.assertEqual(check.check_tree(root), [
            "README.md: could not be read, so its skill count was not checked: FileNotFoundError",
        ])

    def test_an_explicit_tree_argument_gets_no_readme_count_finding(self):
        root = self._tree(
            "intro\n`doctor` should show `2/2 pstack-t3` for each provider you use.\n",
            readme="See all 60 skills and every playbook.\n",
            skills=("a", "b"),
        )
        (root / "pstack-runtime/SKILL.md").write_text(
            "---\nname: pstack-runtime\ndescription: d\n---\n\n"
            "Call `watch_pull_request`. Call `unwatch_pull_request`.\n"
        )
        tests = tests_tree(root, {})
        output = io.StringIO()
        with mock.patch.object(check, "TESTS_DIR", tests), contextlib.redirect_stdout(output):
            status = check.main([str(root)])
        self.assertEqual((status, output.getvalue()), (0, "ok\n"))


class MinimumT3CodeVersionCheckTest(unittest.TestCase):
    """The tests cover agreeing minimum-version sentences, a different version in each document, and a gate string that cannot be read."""

    def _tree(self, readme, guide):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "pstack-runtime/scripts").mkdir(parents=True)
        (root / "pstack-runtime/scripts/roles.py").write_text("")
        skill = root / "demo"
        skill.mkdir()
        (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\nbody\n")
        readme_path = root / "README.md"
        readme_path.write_text("See all 1 skills and every playbook.\n" + readme)
        guide_path = root / "docs/guide.md"
        guide_path.parent.mkdir()
        guide_path.write_text("`doctor` should show `1/1 pstack-t3` for each provider you use.\n" + guide)
        for attribute, document in (("README_PATH", readme_path), ("GUIDE_PATH", guide_path)):
            patcher = mock.patch.object(check, attribute, document)
            patcher.start()
            self.addCleanup(patcher.stop)
        return root

    def test_a_readme_and_guide_that_name_the_gate_version_are_quiet(self):
        gate = check._setup_gate_version()
        root = self._tree(
            f"Setup needs T3 Code {gate} or later.\n",
            f"You need [T3 Code](https://example.test/releases) `{gate}` or later.\n",
        )
        self.assertEqual(check.check_tree(root), [])

    def test_a_readme_naming_another_version_is_named_with_both_versions(self):
        gate = check._setup_gate_version()
        root = self._tree(
            "Setup needs T3 Code 9.9.9 or later.\n",
            f"You need T3 Code `{gate}` or later.\n",
        )
        self.assertEqual(check.check_tree(root), [
            f"README.md:2: states the T3 Code minimum version 9.9.9, and the setup gate requires {gate}",
        ])

    def test_a_guide_naming_another_version_is_named_with_both_versions(self):
        gate = check._setup_gate_version()
        root = self._tree(
            f"Setup needs T3 Code {gate} or later.\n",
            "You need T3 Code `9.9.9` or later, and Python 3.10 or later.\n",
        )
        self.assertEqual(check.check_tree(root), [
            f"docs/guide.md:2: states the T3 Code minimum version 9.9.9, and the setup gate requires {gate}",
        ])

    def test_a_gate_string_that_cannot_be_read_is_named_instead_of_silently_skipping_the_comparison(self):
        root = self._tree(
            "Setup needs T3 Code 9.9.9 or later.\n",
            "You need T3 Code `9.9.9` or later.\n",
        )
        gate = mock.patch.dict(check.REQUIRED_TOOLS, {"setup-pstack/SKILL.md": ("T3 Code soon or later",)})
        gate.start()
        self.addCleanup(gate.stop)
        self.assertEqual(check.check_tree(root), [
            "setup-pstack/SKILL.md: the setup gate's T3 Code minimum version could not be read",
        ])


def planted_entry():
    return next(label for label in check.REQUIRED_SCAN if label.endswith("t3-archive-threads.py"))


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

    def _ops_repo(self, planted, doc):
        root = self._directory()
        ops = root / "ops"
        ops.mkdir()
        for label in check.REQUIRED_SCAN:
            if label.startswith("ops/"):
                (ops / Path(label).name).write_text(planted if label.endswith("t3-archive-threads.py") else "echo ok\n")
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
            f"{planted_entry()}:1: holds this machine's home directory",
        ])

    def test_dev_null_does_not_report_the_user_dev(self):
        who = check.Identity("/home/dev", "dev", "docs")
        ops, document = self._ops_repo("</dev/null\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [])

    def test_a_tilde_dev_reports_the_user(self):
        who = check.Identity("/home/dev", "dev", "docs")
        ops, document = self._ops_repo("see ~dev/notes\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            f"{planted_entry()}:1: holds this machine's user name",
        ])

    def test_a_longer_root_path_reports_the_home_and_not_the_user(self):
        who = check.Identity("/root", "root", "x")
        ops, document = self._ops_repo("cd /root/foo\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            f"{planted_entry()}:1: holds this machine's home directory",
        ])

    def test_a_quoted_ops_conf_does_not_report_the_host(self):
        who = check.Identity("/home/alice", "alice", "ops")
        ops, document = self._ops_repo('echo "ops.conf"\n', "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [])

    def test_ping_ops_lan_reports_the_host(self):
        who = check.Identity("/home/alice", "alice", "ops")
        ops, document = self._ops_repo("ping ops.lan\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            f"{planted_entry()}:1: holds this machine's host name",
        ])

    def test_config_example_after_one_slash_does_not_report_the_host(self):
        who = check.Identity("/home/alice", "alice", "config")
        ops, document = self._ops_repo("cp ops/config.example dest\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [])

    def test_config_after_two_slashes_reports_the_host(self):
        who = check.Identity("/home/alice", "alice", "config")
        ops, document = self._ops_repo("open //config/share\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            f"{planted_entry()}:1: holds this machine's host name",
        ])

    def test_dollar_app_prev_does_not_report_the_host(self):
        who = check.Identity("/home/alice", "alice", "app")
        ops, document = self._ops_repo("cp $APP.prev dest\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [])

    def test_app_before_a_port_reports_the_host(self):
        who = check.Identity("/home/alice", "alice", "app")
        ops, document = self._ops_repo("curl app:3773/x\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            f"{planted_entry()}:1: holds this machine's host name",
        ])

    def test_a_quoted_dotted_host_reports_the_host(self):
        who = check.Identity("/home/zed", "zed", "box7.lan")
        text = 'ssh "box7.lan"\nHOST="box7.lan"\nserver = "box7.lan"\nssh "BOX7.LAN"\nssh "box7.lan:2222"\n'
        ops, document = self._ops_repo(text, "ok\n")
        hit = f"{planted_entry()}:%d: holds this machine's host name"
        self.assertEqual(check.machine_value_findings(ops, document, who), [hit % n for n in range(1, 6)])

    def test_a_dotted_host_in_a_slash_path_reports_the_host(self):
        who = check.Identity("/home/zed", "zed", "box7.lan")
        text = "export BACKUPS=/mnt/box7.lan/archives\nscp backup:/box7.lan/data dest\n"
        ops, document = self._ops_repo(text, "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            f"{planted_entry()}:1: holds this machine's host name",
            f"{planted_entry()}:2: holds this machine's host name",
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


class RenderedTreeCheckTest(unittest.TestCase):
    def _root(self, roles=False, demo=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "pstack-runtime").mkdir()
        (root / "pstack-runtime/SKILL.md").write_text(
            "---\nname: pstack-runtime\ndescription: d\n---\n\n"
            "Call `watch_pull_request`. Call `unwatch_pull_request`.\n"
        )
        (root / "setup-pstack").mkdir()
        (root / "setup-pstack/SKILL.md").write_text(
            "---\nname: setup-pstack\ndescription: d\n---\n\n"
            "Call `watch_pull_request`. T3 Code 0.1.0 or later.\n"
        )
        if roles:
            (root / "pstack-runtime/scripts").mkdir()
            (root / "pstack-runtime/scripts/roles.py").write_text("")
        if demo is not None:
            (root / "demo").mkdir()
            (root / "demo/SKILL.md").write_text(demo)
        return root

    def _guide_agrees(self, root):
        """Point GUIDE_PATH at a guide that quotes the count this tree ships."""
        document = root / "docs/guide.md"
        document.parent.mkdir()
        shipped = len(list(root.glob("*/SKILL.md")))
        document.write_text(f"`doctor` should show `{shipped}/{shipped} pstack-t3` for each provider you use.\n")
        patcher = mock.patch.object(check, "GUIDE_PATH", document)
        patcher.start()
        self.addCleanup(patcher.stop)
        readme = root / "README.md"
        readme.write_text(f"See all {shipped} skills and every playbook.\n")
        patcher = mock.patch.object(check, "README_PATH", readme)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_tree_with_both_witness_skills_and_roles_py_has_no_roles_finding(self):
        root = self._root(roles=True)
        self._guide_agrees(root)
        self.assertEqual(check.check_tree(root), [])

    def test_a_tree_with_both_witness_skills_and_no_roles_py_names_that_file(self):
        self.assertEqual(check.check_tree(self._root()), ["pstack-runtime/scripts/roles.py: is missing"])

    def test_disable_model_invocation_in_a_skill_body_is_not_a_frontmatter_finding(self):
        demo = (
            "---\nname: demo\ndescription: d\n---\n\n"
            "The build strips disable-model-invocation.\n"
        )
        root = self._root(roles=True, demo=demo)
        self._guide_agrees(root)
        self.assertEqual(check.check_tree(root), [])

    def test_disable_model_invocation_in_frontmatter_is_named(self):
        demo = (
            "---\nname: demo\ndescription: d\ndisable-model-invocation: true\n---\n\n"
            "body\n"
        )
        root = self._root(roles=True, demo=demo)
        self._guide_agrees(root)
        self.assertEqual(
            check.check_tree(root),
            ["demo/SKILL.md: frontmatter contains disable-model-invocation"],
        )

    def test_disable_model_invocation_after_one_mark_is_only_missing_frontmatter(self):
        demo = "---\nname: demo\ndescription: d\ndisable-model-invocation: true\n"
        root = self._root(roles=True, demo=demo)
        self._guide_agrees(root)
        self.assertEqual(
            check.check_tree(root),
            ["demo/SKILL.md: missing frontmatter"],
        )


class EffortSpeedTest(unittest.TestCase):
    def test_an_instruction_to_choose_a_dial_is_named(self):
        reason = (
            "pick, choose, select, raise, lower, change, use, or set has at most 180 intervening characters before reasoning level, effort level, fast or standard speed, fast mode, or standard speed, "
            "or pick, choose, or select has at most 60 intervening characters before effort; both matches are case-insensitive, stay on one line, and have no '.', '!', or '?' between the verb and dial; "
            "point at the Subagents section through an override"
        )
        body = (
            "---\nname: demo\ndescription: d\n---\n\n"
            "Pick each subagent's reasoning level and fast or standard speed for the job, per the Subagents section.\n"
            "Before each spawn, start from the role's line or default and pick the reasoning level and speed the job needs.\n"
            "Choose the effort level before the spawn.\n"
            "Choose the effort before the spawn.\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(body)
            findings = check.check_tree(directory)
        lines = body.splitlines()
        expected = [
            f"demo/SKILL.md:{number}: {reason}: {lines[number - 1]}"
            for number in (6, 7, 8, 9)
        ]
        self.assertEqual(findings, expected)

    def test_a_subagents_pointer_and_a_described_reasoning_level_are_not_named(self):
        body = (
            "---\nname: demo\ndescription: d\n---\n\n"
            "Follow the Subagents section.\n"
            "Pass the seat the role this step names, per the Subagents section of the poteto-mode skill.\n"
            "A seat that names its own reasoning level keeps it when it is at or below the budget level.\n"
            "Before each spawn, start from the role the step names and pass that seat unchanged.\n"
            "When no roles file exists and it matters, ask whether the user wants to pick a model for each role and a reasoning budget now.\n"
            "A large or cross-cutting effort routes to figure-it-out.\n"
            'Use `--set "judgment and prose=claudeAgent/claude-opus-5-5?effort=xhigh"` for that seat.\n'
            "The reasoning level to pick is xhigh.\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(body)
            self.assertEqual(check.check_tree(directory), [])


if __name__ == "__main__":
    unittest.main()



class ThreadOrganizeLintTest(unittest.TestCase):
    def _skill(self, body):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        skill = root / "demo"
        skill.mkdir()
        (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\n" + body + "\n")
        return root

    def test_a_threadids_key_in_shipped_text_is_reported(self):
        root = self._skill("Archive with `t3_thread_organize`, `threadIds`: [t1, t2].")
        self.assertEqual(
            check.check_tree(root),
            [
                "demo/SKILL.md:6: shipped text uses a plural threadIds key. "
                "Pass one explicit threadId per t3_thread_organize call: Archive with `t3_thread_organize`, `threadIds`: [t1, t2]."
            ],
        )
    def test_a_thread_organize_archive_step_without_threadid_is_reported(self):
        root = self._skill("t3_thread_organize archive this thread.")
        self.assertEqual(
            check.check_tree(root),
            [
                "demo/SKILL.md:6: t3_thread_organize archive/settle/snooze step must name threadId. "
                "Add the one explicit threadId: t3_thread_organize archive this thread."
            ],
        )

    def test_a_thread_organize_step_that_names_threadid_passes(self):
        root = self._skill("t3_thread_organize archive this thread by threadId.")
        self.assertEqual(check.check_tree(root), [])
