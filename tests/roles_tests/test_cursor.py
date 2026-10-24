if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import json
import tempfile
import unittest
from .support import Repo, roles, setUpModule


def cursor_only_catalog():
    return {"providers": [
        {
            "providerInstanceId": "cursor",
            "driverKind": "cursor",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "grok-4.7", "options": []}],
        },
        {
            "providerInstanceId": "claudeAgent",
            "driverKind": "claudeAgent",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "claude-opus-5-5", "options": []}],
        },
    ]}


class UnrunnableHaikuParentCliTest(unittest.TestCase):
    def show(self, constraints):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [{
                "providerInstanceId": "claudeAgent",
                "canRunChildTask": False,
                "constraints": constraints,
                "models": [{"id": "claude-haiku-4-5", "options": []}, {"id": "claude-opus-5-5", "options": []}],
            }]})
            repo.put(repo.user, {"version": 1, "roles": {"bug-fix": ["inherit"]},
                                 "exclude": [{"model": "*claude-haiku-4-5*"}]})
            return repo.run("show", "--catalog", str(path), "--parent", "claudeAgent/claude-haiku-4-5", "--role", "bug-fix")

    def test_inherit_of_an_excluded_parent_whose_provider_cannot_run_child_tasks_names_the_provider(self):
        reasons = (
            (["Provider is not authenticated.", "Sign in again."], "Provider is not authenticated.; Sign in again."),
            ([], "cannot run child tasks"),
        )
        for constraints, reason in reasons:
            with self.subTest(constraints=constraints):
                completed = self.show(constraints)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertIn("cannot inherit claudeAgent/claude-haiku-4-5", completed.stderr)
                self.assertIn(roles.EXCLUDE_RULE, completed.stderr)
                self.assertIn(f"claudeAgent is not runnable ({reason})", completed.stderr)


class SkillTestsCursorCliTest(unittest.TestCase):
    """The cursor driver cannot launch seats, so a configured seat that runs on it is refused under --launches-seats."""

    def run_show(self, roles, *extra):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, cursor_only_catalog())
            if roles is not None:
                repo.put(repo.user, {"version": 1, "roles": roles})
            return repo.run(
                "show",
                "--catalog", str(path),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--role", "skill tests",
                *extra,
            )

    def test_show_notes_a_configured_cursor_skill_tests_seat(self):
        completed = self.run_show({"skill tests": [{"providerInstanceId": "cursor", "model": "grok-4.7"}]})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["skill tests"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "cursor", "model": "grok-4.7"}])
        self.assertIn("runs on the cursor driver, which cannot launch seats", " ".join(entry.get("notes", [])))

    def test_validate_reports_a_configured_cursor_skill_tests_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, cursor_only_catalog())
            repo.put(repo.user, {"version": 1, "roles": {
                "skill tests": [{"providerInstanceId": "cursor", "model": "grok-4.7"}],
            }})
            completed = repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("runs on the cursor driver, which cannot launch seats", completed.stdout)
        self.assertEqual(completed.stderr, "")

    def test_launches_seats_refuses_a_configured_cursor_skill_tests_seat(self):
        completed = self.run_show(
            {"skill tests": [{"providerInstanceId": "cursor", "model": "grok-4.7"}]},
            "--launches-seats",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, (
            "error: role 'skill tests' has no seat for a child that launches seats: "
            "a seat that runs on the cursor driver cannot launch seats, and the seat resolved to cursor\n"
        ))

    def test_launches_seats_keeps_a_configured_non_cursor_seat(self):
        completed = self.run_show(
            {"skill tests": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}]},
            "--launches-seats",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["skill tests"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}])
