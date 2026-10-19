import json
import tempfile
import unittest
from .support import Repo, roles, setUpModule
from .catalogs import backup_catalog, fixture_catalog, run_backup


B2_CONFIG = {
    "version": 1,
    "budget": "small",
    "roles": {"bug-fix": [{
        "providerInstanceId": "claudeAgent",
        "model": "claude-opus-5-5",
        "options": {"effort": "xhigh"},
    }]},
    "exclude": [{"options": {"effort": "medium"}}],
}


def b2_next_candidate_catalog():
    """backup_catalog with claude-sonnet-5-5 declaring no effort option."""
    catalog = backup_catalog()
    claude = next(provider for provider in catalog["providers"] if provider["providerInstanceId"] == "claudeAgent")
    for model in claude["models"]:
        if model["id"] == "claude-sonnet-5-5":
            model["options"] = []
    return catalog


class ExcludeEntryShapeCliTest(unittest.TestCase):
    """Every exclude entry is an object; a string entry is refused and names the exclude key."""

    def test_a_string_exclude_entry_is_refused_and_names_the_exclude_key(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"version": 1, "roles": {}, "exclude": ["grok-4.7"]})
            completed = repo.show_bug_fix()
            expected = (
                f"error: {repo.user}: exclude entry 'grok-4.7' is a string; "
                "write an object with model, options, provider, or roles\n"
            )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, expected)


class ExcludedParentCliTest(unittest.TestCase):
    """B1. An unset role, and a role whose every seat is excluded, settle a refused parent against the catalog."""

    def show(self, roles_file, *, catalog=None, parent="claudeAgent/claude-opus-5-5"):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog() if catalog is None else catalog)
            repo.put(repo.user, roles_file)
            return repo.run("show", "--catalog", str(path), "--parent", parent, "--role", "bug-fix")

    def test_an_unset_role_replaces_an_excluded_parent_with_the_first_allowed_model(self):
        completed = self.show({"version": 1, "roles": {}, "exclude": [{"model": "claude-opus-5-5"}]})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], {"providerInstanceId": "claudeAgent", "model": "claude-haiku-4-5"})
        self.assertNotIn("note", entry)
        self.assertTrue(
            any(note.startswith("inherit replaced by claudeAgent/claude-haiku-4-5") for note in entry.get("notes", [])),
            entry,
        )

    def test_a_role_whose_every_seat_is_excluded_replaces_the_refused_parent(self):
        completed = self.show({
            "version": 1,
            "roles": {"bug-fix": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}]},
            "exclude": [{"model": "claude-opus-5-5"}],
        })
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertNotEqual(entry["seats"], "inherit")
        self.assertEqual(entry["seats"], {"providerInstanceId": "claudeAgent", "model": "claude-haiku-4-5"})
        notes = entry.get("notes", [])
        self.assertTrue(any(note.startswith("skipped configured seat claudeAgent/claude-opus-5-5") for note in notes), entry)
        self.assertTrue(
            any(note.startswith("inherit replaced by claudeAgent/claude-haiku-4-5") for note in notes),
            entry,
        )

    def test_a_provider_with_no_allowed_model_refuses_the_role(self):
        catalog = {"providers": [{
            "providerInstanceId": "claudeAgent",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "claude-opus-5-5", "options": []}],
        }]}
        completed = self.show(
            {"version": 1, "roles": {}, "exclude": [{"model": "claude-opus-5-5"}]},
            catalog=catalog,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("has no model the exclude list allows", completed.stderr)
        self.assertIn(roles.EXCLUDE_RULE, completed.stderr)


class ExcludeFinalTargetCliTest(unittest.TestCase):
    """B2. The exclude list is checked on the final target, after option mapping and the budget cap. The accepted `--resume` original-seat path is the exception."""

    def show(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog())
            repo.put(repo.user, B2_CONFIG)
            completed = repo.run(
                "show", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5", "--role", "bug-fix",
            )
        return completed

    def test_show_refuses_a_budget_capped_seat_the_exclude_list_names(self):
        completed = self.show()
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("exclude", completed.stderr)
        self.assertIn(roles.EXCLUDE_RULE, completed.stderr)

    def test_validate_lists_a_budget_capped_seat_the_exclude_list_names(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog())
            repo.put(repo.user, B2_CONFIG)
            completed = repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stderr, "")
        self.assertIn("bug-fix: claudeAgent/claude-opus-5-5", completed.stdout)
        self.assertIn(roles.EXCLUDE_RULE, completed.stdout)


class ExcludeBackupTargetCliTest(unittest.TestCase):
    """B2. A refused backup candidate lets the loop try the next one."""

    def test_backup_parks_when_the_final_target_the_failed_level_produces_is_refused(self):
        completed = run_backup(
            "--role", "bug-fix", "--provider", "grok", "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            "--brief-mode", "full",
            roles_file={
                "version": 1,
                "backups": {"bug-fix": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}]},
                "exclude": [{"options": {"effort": "high"}}],
            },
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "park")
        self.assertNotIn("seat", payload)

    def test_backup_tries_the_next_candidate_when_the_first_is_refused(self):
        completed = run_backup(
            "--role", "bug-fix", "--provider", "grok", "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            "--brief-mode", "full",
            catalog=b2_next_candidate_catalog(),
            roles_file={
                "version": 1,
                "backups": {"bug-fix": [
                    {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
                    {"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5"},
                ]},
                "exclude": [{"options": {"effort": "high"}}],
            },
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "relaunch")
        self.assertEqual(payload["seat"], {"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5"})

    def test_backup_skips_an_excluded_first_candidate_and_relaunches_on_an_allowed_second(self):
        completed = run_backup(
            "--role", "bug-fix", "--provider", "grok", "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            roles_file={
                "version": 1,
                "backups": {"bug-fix": [
                    {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
                    {"providerInstanceId": "codex", "model": "gpt-6.1-sol"},
                ]},
                "exclude": [{"model": "claude-opus-5-5"}],
            },
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "relaunch")
        self.assertEqual(
            payload["seat"],
            {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "medium"}},
        )


class BackupUnavailableCandidateCliTest(unittest.TestCase):
    """T951. The loop skips a backups candidate the catalog lacks, and validate still names it."""

    def test_backup_skips_a_first_candidate_the_catalog_lacks_and_relaunches_on_the_second(self):
        completed = run_backup(
            "--role", "bug-fix", "--provider", "grok", "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            roles_file={"version": 1, "backups": {"bug-fix": [
                {"providerInstanceId": "kilo", "model": "kilo-1"},
                {"providerInstanceId": "codex", "model": "gpt-6.1-sol"},
            ]}},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "relaunch")
        self.assertEqual(
            payload["seat"],
            {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "medium"}},
        )

    def test_validate_names_a_backups_seat_the_catalog_lacks(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, backup_catalog())
            repo.put(repo.user, {"version": 1, "roles": {}, "backups": {"bug-fix": [
                {"providerInstanceId": "kilo", "model": "kilo-1"},
            ]}})
            completed = repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn("backups.bug-fix: kilo/kilo-1: not runnable or not in the catalog", completed.stdout)


class ExcludeOptionCliTest(unittest.TestCase):
    """B4. A validate-only check reports an options pair no named catalog model can accept."""

    def validate(self, rule):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog())
            repo.put(repo.user, {"version": 1, "roles": {}, "exclude": [rule]})
            return repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")

    def test_validate_reports_an_exclude_options_pair_the_catalog_cannot_accept(self):
        for label, rule, named in (
            ("unknown id", {"options": {"typo": "high"}}, "typo"),
            ("bad select value", {"options": {"effort": "banana"}}, "effort"),
            ("wrong type", {"model": "claude-opus-5-5", "options": {"effort": True}}, "effort"),
        ):
            with self.subTest(label=label):
                completed = self.validate(rule)
                self.assertEqual(completed.returncode, 1, completed.stderr)
                line = next(
                    (line for line in completed.stdout.splitlines() if "exclude entry 1" in line), None,
                )
                self.assertIsNotNone(line, completed.stdout)
                self.assertIn(named, line)

    def test_a_rule_whose_provider_glob_matches_no_catalog_model_is_not_checked(self):
        completed = self.validate({"provider": "gemini*", "options": {"effort": "high"}})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("ok", completed.stdout)


class ExcludeNarrowingCliTest(unittest.TestCase):
    """B6. A provider glob and a roles list narrow a rule and AND with its model and options."""

    CODEX = {"providerInstanceId": "codex", "model": "gpt-6.1-sol"}
    GROK = {"providerInstanceId": "grok", "model": "grok-4.7"}

    def show(self, roles_file, role):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog())
            repo.put(repo.user, roles_file)
            return repo.run("show", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5", "--role", role)

    def test_a_roles_narrowed_rule_refuses_only_the_named_role(self):
        roles_file = {
            "version": 1,
            "roles": {"review backups": [self.CODEX], "bug-fix": [self.CODEX]},
            "exclude": [{"provider": "codex", "roles": ["review backups"]}],
        }
        panel = self.show(roles_file, "review backups")
        worker = self.show(roles_file, "bug-fix")
        self.assertEqual(panel.returncode, 0, panel.stderr)
        panel_entry = json.loads(panel.stdout)["roles"]["review backups"]
        self.assertEqual(panel_entry["seats"], [])
        self.assertTrue(any("codex/gpt-6.1-sol" in note for note in panel_entry.get("notes", [])), panel_entry)
        self.assertEqual(worker.returncode, 0, worker.stderr)
        self.assertEqual(json.loads(worker.stdout)["roles"]["bug-fix"]["seats"], [{
            "providerInstanceId": "codex",
            "model": "gpt-6.1-sol",
            "options": {"reasoningEffort": "medium"},
        }])

    def test_a_provider_glob_refuses_a_seat_in_every_role(self):
        roles_file = {
            "version": 1,
            "roles": {"bug-fix": [self.CODEX], "review backups": [self.GROK, self.CODEX]},
            "exclude": [{"provider": "codex*"}],
        }
        worker = self.show(roles_file, "bug-fix")
        panel = self.show(roles_file, "review backups")
        self.assertEqual(worker.returncode, 0, worker.stderr)
        worker_entry = json.loads(worker.stdout)["roles"]["bug-fix"]
        self.assertEqual(worker_entry["seats"], "inherit")
        self.assertTrue(
            any("skipped configured seat codex/gpt-6.1-sol" in note for note in worker_entry.get("notes", [])),
            worker_entry,
        )
        self.assertEqual(panel.returncode, 0, panel.stderr)
        panel_entry = json.loads(panel.stdout)["roles"]["review backups"]
        self.assertEqual(panel_entry["seats"], [self.GROK])
        self.assertTrue(any("codex/gpt-6.1-sol" in note for note in panel_entry.get("notes", [])), panel_entry)
