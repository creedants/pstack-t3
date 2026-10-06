import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GROK_SEAT = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "xhigh"}}
OPUS_SEAT = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}}


def show(*role_names):
    with tempfile.TemporaryDirectory() as directory:
        env = {**os.environ, "XDG_CONFIG_HOME": directory}
        argv = [
            sys.executable,
            str(ROOT / "t3/scripts/roles.py"),
            "show",
            "--cwd",
            directory,
            "--catalog",
            str(ROOT / "tests/fixtures/catalog.json"),
            "--parent",
            "claudeAgent/claude-opus-5-5",
        ]
        for name in role_names:
            argv.extend(["--role", name])
        return subprocess.run(argv, env=env, capture_output=True, text=True)


class RolesCliTest(unittest.TestCase):
    def test_one_role_resolves_that_role(self):
        completed = show("bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload["roles"]), ["bug-fix"])
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [GROK_SEAT])

    def test_two_roles_resolve_both(self):
        completed = show("bug-fix", "judgment and prose")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload["roles"]), ["bug-fix", "judgment and prose"])
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [GROK_SEAT])
        self.assertEqual(payload["roles"]["judgment and prose"]["seats"], [OPUS_SEAT])

    def test_repeated_identical_role_resolves_once(self):
        completed = show("bug-fix", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload["roles"]), ["bug-fix"])
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [GROK_SEAT])
