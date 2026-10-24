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
from .catalogs import LIMIT, plain_provider, run_backup


MIGRATION_JSON = {
    "exclude": [
        {"model": "grok*fast*"},
        {"model": "grok*", "options": {"fastMode": True}},
        {"model": "*claude-haiku-4-5*"},
        {"provider": "codex", "roles": ["review backups"]},
    ],
    "backups": {
        "feature, refactoring": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "bug-fix": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "perf-issue": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "hillclimb": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "judgment and prose": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "hardest tasks": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "how explorer": [{"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5"}],
        "how explainer": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "why investigators": [{"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5"}],
        "why synthesizer": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "reflect tooling": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "reflect judgment, divergent, synthesizer": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "swarm workers": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "skill tests": [{"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5"}],
        "arena runners": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "arena cross-judge pool": [{"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": False}}, {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "architect runners": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "interrogate reviewers": [{"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": False}}, {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "verifiers": [{"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": False}}, {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
    },
}


MIGRATION_CATALOG = {"providers": [
    plain_provider("claudeAgent", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"),
    {
        "providerInstanceId": "grok",
        "canRunChildTask": True,
        "constraints": [],
        "models": [
            {"id": "grok-4.7", "options": [{"id": "fastMode", "type": "boolean"}]},
            {"id": "grok-4.7-build-fast", "options": []},
        ],
    },
    plain_provider("codex", "gpt-6.1-sol"),
    {"providerInstanceId": "cursor", "driverKind": "cursor", "canRunChildTask": True, "constraints": [],
     "models": [{"id": "default", "options": []}]},
]}


class HouseRuleMigrationCliTest(unittest.TestCase):
    """The owner's house-rule JSON reproduces the four picks, the three refusals, and the CLI paths this test names."""

    def setUp(self):
        self.exclude = roles.parse_exclude(MIGRATION_JSON["exclude"], "test")
        self.backups = roles.parse_backups(MIGRATION_JSON["backups"], "test")

    def show(self, *args, roles_file):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, MIGRATION_CATALOG)
            repo.put(repo.user, roles_file)
            return repo.run("show", "--catalog", str(path), *args)

    def validate(self, roles_file):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, MIGRATION_CATALOG)
            repo.put(repo.user, roles_file)
            return repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")

    def exclude_roles_file(self, roles):
        return {"version": 1, "roles": roles, "exclude": MIGRATION_JSON["exclude"]}

    def backup(self, role, failed, authors=()):
        return roles.backup_seat(
            role, failed, LIMIT, MIGRATION_CATALOG, "default", [], list(authors),
            backups=self.backups, exclude=self.exclude, panel_budget="default",
        )

    def test_the_exclude_and_backups_keys_reproduce_the_removed_builtin_picks(self):
        worker = self.backup("bug-fix", {"provider": "codex", "model": "gpt-6.1-sol"})
        self.assertEqual(worker.decision, "relaunch")
        self.assertEqual(worker.seat, {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"})

        tests = self.backup("skill tests", {"provider": "grok", "model": "grok-4.7"})
        self.assertEqual(tests.decision, "relaunch")
        self.assertEqual(tests.seat, {"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5"})

        grok_author = self.backup(
            "verifiers", {"provider": "codex", "model": "gpt-6.1-sol"}, authors=("grok/grok-4.7",)
        )
        self.assertEqual(grok_author.decision, "relaunch")
        self.assertEqual(grok_author.seat, {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"})

        claude_author = self.backup(
            "verifiers", {"provider": "codex", "model": "gpt-6.1-sol"}, authors=("claudeAgent/claude-opus-5-5",)
        )
        self.assertEqual(claude_author.decision, "relaunch")
        self.assertEqual(
            claude_author.seat,
            {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": False}},
        )

        for seat in (worker.seat, tests.seat, grok_author.seat, claude_author.seat):
            self.assertNotIn(seat["providerInstanceId"], ("codex", "cursor"))

        for seat in (
            {"providerInstanceId": "grok", "model": "grok-4.7-build-fast"},
            {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": True}},
            {"providerInstanceId": "claudeAgent", "model": "claude-haiku-4-5"},
        ):
            with self.subTest(seat=seat):
                self.assertIsNotNone(roles.exclude_reason(seat, self.exclude))

    def test_the_three_reviewer_backup_lists_carry_the_grok_false_pin(self):
        expected = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": False}}
        for role in ("arena cross-judge pool", "interrogate reviewers", "verifiers"):
            with self.subTest(role=role):
                result = self.backup(
                    role, {"provider": "codex", "model": "gpt-6.1-sol"},
                    authors=("claudeAgent/claude-opus-5-5",),
                )
                self.assertEqual(result.decision, "relaunch")
                self.assertEqual(result.seat, expected)

    def test_show_keeps_a_grok_seat_that_declares_fast_mode_false(self):
        seat = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": False}}
        completed = self.show(
            "--parent", "claudeAgent/claude-opus-5-5", "--role", "bug-fix",
            roles_file=self.exclude_roles_file({"bug-fix": [seat]}),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["roles"]["bug-fix"]["seats"], [seat])

    def test_show_keeps_a_claude_seat_that_declares_fast_mode_true(self):
        seat = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"fastMode": True}}
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, self.exclude_roles_file({"bug-fix": [seat]}))
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout)["roles"]["bug-fix"]["seats"],
            [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5",
              "options": {"fastMode": True, "effort": "medium"}}],
        )

    def test_show_drops_only_the_codex_review_backups_seat(self):
        codex = {"providerInstanceId": "codex", "model": "gpt-6.1-sol"}
        grok = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": False}}
        claude = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}
        completed = self.show(
            "--parent", "claudeAgent/claude-opus-5-5", "--role", "review backups",
            roles_file=self.exclude_roles_file({"review backups": [codex, grok, claude]}),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["review backups"]
        self.assertEqual(entry["seats"], [grok, claude])
        self.assertTrue(any("codex/gpt-6.1-sol" in note for note in entry.get("notes", [])), entry)

    def test_validate_lists_the_codex_review_backups_seat(self):
        codex = {"providerInstanceId": "codex", "model": "gpt-6.1-sol"}
        completed = self.validate(self.exclude_roles_file({"review backups": [codex]}))
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertTrue(
            any(
                line.startswith("review backups: codex/gpt-6.1-sol:") and roles.EXCLUDE_RULE in line
                for line in completed.stdout.splitlines()
            ),
            completed.stdout,
        )

    def test_launches_seats_still_refuses_a_seat_that_resolves_to_a_cursor_parent(self):
        completed = self.show(
            "--parent", "cursor/default", "--role", "skill tests", "--launches-seats",
            roles_file=self.exclude_roles_file({"skill tests": ["inherit"]}),
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("cannot launch seats", completed.stderr)

    def test_show_replaces_a_haiku_4_5_parent_the_exclude_list_refuses(self):
        completed = self.show(
            "--parent", "claudeAgent/claude-haiku-4-5", "--role", "bug-fix",
            roles_file=self.exclude_roles_file({}),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"})
        self.assertTrue(
            any(note.startswith("inherit replaced by claudeAgent/claude-opus-5-5") for note in entry.get("notes", [])),
            entry,
        )

    def test_backup_relaunches_on_the_backups_list_not_on_codex(self):
        completed = run_backup(
            "--role", "bug-fix", "--provider", "codex", "--model", "gpt-6.1-sol",
            catalog=MIGRATION_CATALOG,
            roles_file={"version": 1, "backups": MIGRATION_JSON["backups"], "exclude": MIGRATION_JSON["exclude"]},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "relaunch")
        self.assertEqual(payload["seat"], {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"})
