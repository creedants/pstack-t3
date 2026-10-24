"""The host-gh guard as a brigade case installs it, run through a nested case for its setup, cleanup, and PATH."""

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import CaseBase, LandingFlows, setUpModule

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


def run_case(body, bases=(CaseBase,)):
    """Run body as the one test of a case over bases, and return that case's result.

    The case class is built inside this function, so module discovery collects no case that has no stub of its own.
    """

    class Nested(*bases, unittest.TestCase):
        def test_nested(self):
            body(self)

    result = unittest.TestResult()
    Nested("test_nested").run(result)
    return result


class BrigadeHostGhGuardTest(unittest.TestCase):
    """A nested brigade case, run for the failure its guard records and for the stub it lets through."""

    def failure_text(self, result):
        return "\n".join(text for _, text in result.failures)

    def test_a_brigade_case_that_calls_gh_with_no_stub_fails_with_the_guard_message_and_the_call_exits_127(self):
        def body(case):
            result = subprocess.run(["gh", "repo", "view"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 127)

        result = run_case(body)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.failures), 1)
        self.assertIn("this brigade test called gh with no stub of its own; recorded calls: [['repo', 'view']]",
                      self.failure_text(result))

    def test_a_brigade_case_that_puts_its_own_stub_first_answers_from_it_and_the_guard_records_nothing(self):
        def body(case):
            bindir = Path(case.temporary.name) / "bin"
            bindir.mkdir()
            script = bindir / "gh"
            script.write_text(f"#!{sys.executable}\nprint('stub answered')\n")
            script.chmod(0o755)
            with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}):
                result = subprocess.run(["gh", "repo", "view"], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout.strip()), (0, "stub answered"))
            self.assertEqual(case.gh_guard.calls(), [])

        result = run_case(body)
        self.assertEqual((result.failures, result.errors), ([], []))

    def test_a_landing_case_whose_stub_has_no_answer_fails_at_cleanup_even_when_the_test_accepts_the_nonzero_exit(self):
        def body(case):
            result = subprocess.run(["gh", "pr", "list"], capture_output=True, text=True, env=case.land_env())
            self.assertEqual(result.returncode, 1)
            self.assertEqual(case.gh_guard.calls(), [])

        result = run_case(body, (CaseBase, LandingFlows))
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.failures), 1)
        self.assertIn("this brigade gh stub had no answer for ['pr', 'list']", self.failure_text(result))

    def test_a_landing_case_with_a_missing_json_value_fails_at_cleanup(self):
        def body(case):
            result = subprocess.run(["gh", "repo", "view", "--json"], capture_output=True, text=True, env=case.land_env())
            self.assertEqual(result.returncode, 1)

        result = run_case(body, (CaseBase, LandingFlows))
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.failures), 1)
        self.assertIn("this brigade gh stub had no answer for ['repo', 'view', '--json']", self.failure_text(result))


if __name__ == "__main__":
    unittest.main()
