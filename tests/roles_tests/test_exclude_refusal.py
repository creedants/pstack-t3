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
from .catalogs import fixture_catalog, run_backup


class ExcludeRefusalOwnerCliTest(unittest.TestCase):
    """One owner decides the exclude refusal, so resolve, inherit, panel, backup, and validate refuse the same seats.

    The clause is "<bare id> matches exclude entry <n> (<label>), and <EXCLUDE_RULE>".
    resolve, inherit, panel, and validate report it. backup skips a refused candidate and relaunches on the next allowed one.
    """

    OPUS = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}
    CLAUSE = f"claude-opus-5-5 matches exclude entry 1 (claude-opus-5-5), and {roles.EXCLUDE_RULE}"

    def show(self, roles_file, *extra):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog())
            repo.put(repo.user, roles_file)
            return repo.run(
                "show", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5", *extra,
            )

    def validate(self, roles_file):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog())
            repo.put(repo.user, roles_file)
            return repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")

    def test_resolve_refuses_a_final_target_with_the_owner_clause(self):
        completed = self.show({
            "version": 1,
            "budget": "small",
            "roles": {"bug-fix": [{**self.OPUS, "options": {"effort": "xhigh"}}]},
            "exclude": [{"options": {"effort": "medium"}}],
        }, "--role", "bug-fix")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            "error: claudeAgent/claude-opus-5-5: "
            f"claude-opus-5-5 matches exclude entry 1 (effort=medium), and {roles.EXCLUDE_RULE}\n",
        )

    def test_inherit_reports_the_replacement_with_the_owner_clause(self):
        completed = self.show({
            "version": 1,
            "roles": {},
            "exclude": [{"model": "claude-opus-5-5"}],
        }, "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], {"providerInstanceId": "claudeAgent", "model": "claude-haiku-4-5"})
        self.assertEqual(entry["notes"], [f"inherit replaced by claudeAgent/claude-haiku-4-5: {self.CLAUSE}"])

    def test_panel_drops_a_refused_backups_seat_with_the_owner_clause(self):
        exclude = roles.parse_exclude([{"model": "claude-opus-5-5"}], "test")
        verdicts = roles.panel_verdicts([self.OPUS], fixture_catalog(), "default", exclude=exclude)
        self.assertEqual(verdicts[0].reason, self.CLAUSE)
        self.assertEqual(verdicts[0].notes, (f"dropped claudeAgent/claude-opus-5-5: {self.CLAUSE}",))

    def test_backup_skips_an_excluded_first_candidate_and_relaunches_on_the_allowed_second(self):
        completed = run_backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            roles_file={
                "version": 1,
                "backups": {"bug-fix": [self.OPUS, {"providerInstanceId": "codex", "model": "gpt-6.1-sol"}]},
                "exclude": [{"model": "claude-opus-5-5"}],
            },
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "relaunch")
        self.assertNotEqual(payload["seat"]["model"], "claude-opus-5-5")
        self.assertEqual(
            payload["seat"],
            {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "medium"}},
        )

    def test_validate_lists_a_refused_backups_seat_with_the_owner_clause(self):
        completed = self.validate({
            "version": 1,
            "roles": {"bug-fix": [{"providerInstanceId": "codex", "model": "gpt-6.1-sol"}]},
            "backups": {"bug-fix": [self.OPUS]},
            "exclude": [{"model": "claude-opus-5-5"}],
        })
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(
            completed.stdout,
            f"backups.bug-fix: claudeAgent/claude-opus-5-5: {self.CLAUSE}\n",
        )

    def test_validate_lists_a_refused_configured_seat_with_the_owner_clause(self):
        completed = self.validate({
            "version": 1,
            "roles": {"bug-fix": [self.OPUS]},
            "exclude": [{"model": "claude-opus-5-5"}],
        })
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(completed.stdout, f"bug-fix: claudeAgent/claude-opus-5-5: {self.CLAUSE}\n")

    def test_settle_without_a_catalog_names_the_owner_clause_for_a_refused_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"version": 1, "roles": {"bug-fix": ["inherit"]}, "exclude": [{"model": "claude-opus-5-5"}]})
            completed = repo.run("show", "--parent", "claudeAgent/claude-opus-5-5", "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout)["roles"]["bug-fix"]["note"],
            "the parent claudeAgent/claude-opus-5-5 claude-opus-5-5 matches exclude entry 1 (claude-opus-5-5), "
            f"and {roles.EXCLUDE_RULE}, so inherit needs the catalog: "
            "call orchestrator_capabilities and rerun roles.py show --catalog",
        )


class ExcludeUnionCliTest(unittest.TestCase):
    """T967. The merged exclude list is the union of the user and project lists."""

    def test_user_exclude_stays_excluded_when_project_has_own_exclude_list(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog())
            repo.put(repo.user, {
                "version": 1,
                "roles": {"bug-fix": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}]},
                "exclude": [{"model": "claude-opus-5-5"}],
            })
            repo.put(repo.project, {"exclude": [{"model": "grok-4.7"}]})
            config = roles.merged_config(str(repo.directory), user_path=repo.user, project_path=repo.project)
            completed = repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")
        self.assertEqual(config["exclude"], ({"model": "claude-opus-5-5"}, {"model": "grok-4.7"}))
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            completed.stdout,
            "bug-fix: claudeAgent/claude-opus-5-5: "
            f"claude-opus-5-5 matches exclude entry 1 (claude-opus-5-5), and {roles.EXCLUDE_RULE}\n",
        )
