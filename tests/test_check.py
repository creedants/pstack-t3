"""Facade over tests/check_tests/. Holds the fence and the two library-wording tests, whose argparse literals are exempt only in a file named test_check.py."""

import os
import tempfile
import unittest
from pathlib import Path

from tests.check_tests.support import check, tests_tree


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


if __name__ == "__main__":
    unittest.main()
