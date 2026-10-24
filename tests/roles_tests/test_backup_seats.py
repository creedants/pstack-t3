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
from .support import Repo, setUpModule
from .catalogs import fixture_catalog, run_backup


MALFORMED_BACKUP_SEATS = (
    {"providerInstanceId": ["claudeAgent"], "model": "claude-opus-5-5"},
    {"providerInstanceId": "claudeAgent", "model": ["claude-opus-5-5"]},
    {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": ["effort"]},
    {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": ["low"]}},
)


class BackupSeatShapeCliTest(unittest.TestCase):
    """B3. A malformed backups seat is refused by name, never with a traceback."""

    def test_each_malformed_backups_seat_shape_in_the_table_is_refused_without_a_traceback(self):
        for command in ("validate", "show"):
            for seat in MALFORMED_BACKUP_SEATS:
                with self.subTest(command=command, seat=seat):
                    with tempfile.TemporaryDirectory() as directory:
                        repo = Repo(directory)
                        path = repo.directory / "catalog.json"
                        repo.put(path, fixture_catalog())
                        repo.put(repo.user, {"version": 1, "roles": {}, "backups": {"bug-fix": [seat]}})
                        completed = repo.run(
                            command, "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5",
                        )
                    self.assertEqual(completed.returncode, 2, completed.stderr)
                    self.assertIn("backups.bug-fix", completed.stderr)
                    self.assertNotIn("Traceback", completed.stdout)
                    self.assertNotIn("Traceback", completed.stderr)

    def test_write_force_refuses_a_malformed_backups_seat_and_leaves_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {
                "version": 1,
                "roles": {},
                "backups": {"bug-fix": [{"providerInstanceId": ["claudeAgent"], "model": "claude-opus-5-5"}]},
            })
            before = repo.user.read_bytes()
            completed = repo.write("--force", "--set", "bug-fix=grok/grok-4.7")
            self.assertEqual(repo.user.read_bytes(), before)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("backups.bug-fix", completed.stderr)

    def test_validate_names_a_backups_option_the_catalog_does_not_offer(self):
        for label, options, named in (
            ("unknown id", {"typo": "xhigh"}, "typo"),
            ("bad select value", {"effort": "banana"}, "effort"),
        ):
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    path = repo.directory / "catalog.json"
                    repo.put(path, fixture_catalog())
                    repo.put(repo.user, {"version": 1, "roles": {}, "backups": {"bug-fix": [{
                        "providerInstanceId": "claudeAgent",
                        "model": "claude-opus-5-5",
                        "options": options,
                    }]}})
                    completed = repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")
                self.assertEqual(completed.returncode, 1, completed.stderr)
                line = next(
                    (line for line in completed.stdout.splitlines() if line.startswith("backups.bug-fix:")), None,
                )
                self.assertIsNotNone(line, completed.stdout)
                self.assertIn(named, line)

    def test_backup_refuses_when_its_own_backups_entry_has_a_bad_option(self):
        completed = run_backup(
            "--role", "bug-fix", "--provider", "grok", "--model", "grok-4.7",
            roles_file={"version": 1, "backups": {"bug-fix": [{
                "providerInstanceId": "claudeAgent",
                "model": "claude-opus-5-5",
                "options": {"typo": "xhigh"},
            }]}},
        )
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertEqual(completed.stdout, "")
        self.assertIn("backups.bug-fix", completed.stderr)


class BackupCandidateOptionsCliTest(unittest.TestCase):
    """B5. A backup candidate keeps its declared options and takes the failed level only when it names no effort level."""

    def test_a_candidate_keeps_its_options_and_a_bare_candidate_takes_the_failed_level(self):
        cases = (
            (
                "declared options",
                ("--provider", "claudeAgent", "--model", "claude-opus-5-5", "--options", '{"effort": "xhigh"}'),
                {"providerInstanceId": "grok", "model": "grok-4.7",
                 "options": {"reasoningEffort": "low", "fastMode": False}},
                {"providerInstanceId": "grok", "model": "grok-4.7",
                 "options": {"reasoningEffort": "low", "fastMode": False}},
            ),
            (
                "no options",
                ("--provider", "grok", "--model", "grok-4.7", "--options", '{"reasoningEffort": "medium"}'),
                {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
                {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            ),
            (
                "declared special effort",
                ("--provider", "grok", "--model", "grok-4.7", "--options", '{"reasoningEffort": "high"}'),
                {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5",
                 "options": {"effort": "ultrathink"}},
                {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5",
                 "options": {"effort": "medium"}},
            ),
        )
        for label, failed_flags, candidate, expected in cases:
            with self.subTest(label=label):
                completed = run_backup(
                    "--role", "bug-fix", *failed_flags,
                    roles_file={"version": 1, "backups": {"bug-fix": [candidate]}},
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                payload = json.loads(completed.stdout)
                self.assertEqual(payload["decision"], "relaunch")
                self.assertEqual(payload["seat"], expected)
