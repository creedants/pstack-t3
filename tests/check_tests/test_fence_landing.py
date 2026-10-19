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
    support_source,
    landing_tree,
    tests_tree,
    AREA_IMPORT,
    IMPORTS_ONLY,
    LANDING_IMPORT,
    FENCE_HEADER,
    FENCE_SOURCE,
    SETUP_SOURCE,
)


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
