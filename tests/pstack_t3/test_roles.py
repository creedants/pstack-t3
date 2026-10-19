import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "t3/scripts"))
sys.path.append(str(ROOT / "scripts"))

import json
import os
import re
import subprocess
import tempfile
import roles  # noqa: E402
from tests.pstack_t3.support import CATALOG, fence_git_environment, config


def setUpModule():
    fence_git_environment()

class RolesTest(unittest.TestCase):
    def test_unset_roles_inherit_and_review_backups_stays_unset(self):
        for catalog in (None, CATALOG):
            for name in roles.ROLES:
                with self.subTest(catalog=bool(catalog), role=name):
                    entry = roles.resolve(config(), catalog, [name])["roles"][name]
                    if name == "review backups":
                        self.assertEqual(entry, {"source": "default", "seats": "unset", "note": roles.UNSET_NOTE})
                    else:
                        self.assertEqual(entry, {
                            "source": "default",
                            "seats": "inherit",
                            "note": roles.UNSET_ROLE_NOTE,
                        })

    def test_runtime_role_table_lists_the_same_names_as_the_role_list(self):
        text = (ROOT / "t3/added/pstack-runtime/roles.md").read_text()
        section = text.split("## Role names", 1)[1]
        names = re.findall(r"\| `([^`]+)` \|", section)
        self.assertEqual(sorted(names), sorted(roles.ROLES))
        self.assertEqual(len(names), len(set(names)))
        proposals = (ROOT / "t3/setup.md").read_text().split("**(b) Propose roles.**", 1)[1].split("**(c) Confirm.**", 1)[0]
        self.assertIn("propose a seat per role", proposals)

    def test_setup_proposes_seats_from_the_catalog_and_leaves_unset_roles_inherit(self):
        text = (ROOT / "t3/setup.md").read_text()
        proposals = text.split("**(b) Propose roles.**", 1)[1].split("**(c) Confirm.**", 1)[0]
        for phrase in (
            "Reason over the live catalog from step 1 and propose a seat per role",
            "Any role the user leaves unset resolves to `inherit`, unless the `exclude` list refuses the inherited seat, and `review backups` stays unset.",
            "It has no built-in seats, and it refuses `inherit`, because the parent can be the author.",
            "Set at least two seats of different model families",
        ):
            self.assertIn(phrase, proposals)
        self.assertNotIn("built-in Haiku", text)
        self.assertNotIn("Leave `skill tests`", text)

    def test_show_prints_inherit_for_an_unset_role(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "XDG_CONFIG_HOME": directory}
            completed = subprocess.run(
                [sys.executable, str(ROOT / "t3/scripts/roles.py"), "show", "--cwd", directory,
                 "--catalog", str(ROOT / "tests/fixtures/catalog.json"), "--role", "bug-fix",
                 "--parent", "claudeAgent/claude-opus-5-5"],
                env=env, capture_output=True, text=True, check=True)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], "inherit")
        self.assertEqual(entry["note"], roles.UNSET_ROLE_NOTE)
        self.assertEqual(entry["source"], "default")

    def test_review_backups_is_unset_by_default(self):
        for catalog in (None, CATALOG):
            entry = roles.resolve(config(), catalog, ["review backups"])["roles"]["review backups"]
            self.assertEqual(entry, {"source": "default", "seats": "unset", "note": roles.UNSET_NOTE})

    def test_null_model_options_are_treated_as_none(self):
        provider = {"providerInstanceId": "pi", "canRunChildTask": True, "constraints": [], "models": [{"id": "pi-1", "options": None}]}
        catalog = {**CATALOG, "providers": CATALOG["providers"] + [provider]}
        entry = roles.resolve(config("large", **{"bug-fix": [{"providerInstanceId": "pi", "model": "pi-1"}]}), catalog, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "pi", "model": "pi-1"}])

    def test_validate_rejects_option_values_the_catalog_lacks(self):
        seat = {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "banana", "serviceTier": True}}
        problems = roles.validate(config(**{"bug-fix": [seat]}), CATALOG)
        self.assertEqual(len(problems), 2, problems)

    def test_budget_makes_inherit_explicit_so_the_cap_applies(self):
        capped = roles.resolve(config("small", **{"bug-fix": ["inherit"]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(capped["seats"], [{
            "providerInstanceId": "claudeAgent",
            "model": "claude-opus-5-5",
            "options": {"effort": "medium"},
        }])
        self.assertNotIn("notes", capped)
        self.assertIn("inherit made explicit", capped["info"][0])
        plain = roles.resolve(config(mode="full", **{"bug-fix": ["inherit"]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(plain["seats"], ["inherit"])

    def test_budget_caps_ultra_to_max_under_unlimited(self):
        ultra = {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "ultra"}}
        kept = roles.resolve(config(mode="full", **{"bug-fix": [ultra]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]["seats"]
        self.assertEqual(kept, [ultra])
        capped = roles.resolve(config("unlimited", **{"bug-fix": [ultra]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]["seats"]
        self.assertEqual(capped, [{
            "providerInstanceId": "codex",
            "model": "gpt-6.1-sol",
            "options": {"reasoningEffort": "max"},
        }])

    def test_catalog_stdin_matches_the_file_error_and_accepts_json(self):
        script = [sys.executable, str(ROOT / "t3/scripts/roles.py"), "show", "--role", "bug-fix", "--parent", "claudeAgent/claude-opus-5-5"]
        catalog_text = (ROOT / "tests/fixtures/catalog.json").read_text()

        def detail(stderr):
            line = stderr.strip().splitlines()[-1]
            self.assertTrue(line.startswith("error: "), stderr)
            self.assertIn("invalid JSON: ", line)
            return line.split("invalid JSON: ", 1)[1]

        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "XDG_CONFIG_HOME": directory}
            base = [*script, "--cwd", directory]

            def run(catalog_arg, stdin=""):
                return subprocess.run([*base, "--catalog", catalog_arg], input=stdin, capture_output=True, text=True, env=env)

            valid_stdin = run("-", catalog_text)
            self.assertEqual(valid_stdin.returncode, 0, valid_stdin.stderr)
            valid_file = run(str(ROOT / "tests/fixtures/catalog.json"))
            self.assertEqual(valid_file.returncode, 0, valid_file.stderr)
            self.assertEqual(valid_stdin.stdout, valid_file.stdout)
            self.assertEqual(json.loads(valid_stdin.stdout)["roles"]["bug-fix"]["seats"], "inherit")

            bad = Path(directory) / "bad.json"
            empty = Path(directory) / "empty.json"
            bad.write_text("{not json")
            empty.write_text("")
            for stdin_text, file_path in (("", empty), ("{not json", bad)):
                from_stdin = run("-", stdin_text)
                from_file = run(str(file_path))
                self.assertEqual(from_stdin.returncode, 2, from_stdin.stderr)
                self.assertEqual(from_file.returncode, 2, from_file.stderr)
                self.assertNotIn("Traceback", from_stdin.stderr)
                self.assertEqual(detail(from_stdin.stderr), detail(from_file.stderr))

    def test_budget_understands_extra_high_and_keeps_none(self):
        model = {"id": "m", "options": [{"id": "reasoning_effort", "type": "select", "options": [{"id": "none"}, {"id": "low"}, {"id": "high"}, {"id": "extra-high"}]}]}
        self.assertEqual(roles.apply_budget({"model": "m"}, model, "unlimited")["options"], {"reasoning_effort": "extra-high"})
        self.assertEqual(roles.apply_budget({"model": "m", "options": {"reasoning_effort": "none"}}, model, "small")["options"], {"reasoning_effort": "none"})
        self.assertEqual(roles.apply_budget({"model": "m", "options": {"reasoning_effort": "extra-high"}}, model, "small")["options"], {"reasoning_effort": "low"})

    def test_snapshot_parent_comes_from_the_caller_not_the_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config_dir = base / "pstack-t3"
            config_dir.mkdir()
            (config_dir / "catalog.json").write_text(json.dumps({
                **CATALOG,
                "inheritedProviderInstanceId": "grok",
                "inheritedModel": "grok-4.7",
            }))
            (config_dir / "roles.json").write_text(json.dumps({
                "version": 1, "budget": "small", "roles": {"bug-fix": ["inherit"]},
            }))
            env = {**os.environ, "XDG_CONFIG_HOME": str(base)}
            completed = subprocess.run(
                [sys.executable, str(ROOT / "t3/scripts/roles.py"), "show", "--cwd", directory,
                 "--role", "bug-fix", "--parent", "codex/gpt-6.1-sol"],
                env=env, capture_output=True, text=True, check=True)
            seats = json.loads(completed.stdout)["roles"]["bug-fix"]["seats"]
        self.assertEqual(seats, [{
            "providerInstanceId": "codex",
            "model": "gpt-6.1-sol",
            "options": {"reasoningEffort": "medium"},
        }])

    def test_unrunnable_provider_falls_back_to_the_parent_made_explicit_with_a_note(self):
        entry = roles.resolve(config(**{"swarm workers": [{"providerInstanceId": "cursor", "model": "default"}]}), CATALOG, ["swarm workers"])["roles"]["swarm workers"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}}])
        self.assertIn("not authenticated", entry["notes"][0])
        self.assertIn("inherit made explicit", entry["info"][0])

    def test_missing_model_falls_back_to_first_model_of_the_provider(self):
        entry = roles.resolve(config(**{"bug-fix": [{"providerInstanceId": "codex", "model": "gpt-9"}]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "medium"}}])
        self.assertIn("gpt-9", entry["notes"][0])

    def test_budget_caps_explicit_effort_and_skips_special_values(self):
        seat = {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "ultra"}}
        resolved = roles.resolve(config("large", **{"bug-fix": [seat]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]["seats"][0]
        self.assertEqual(resolved["options"], {"reasoningEffort": "xhigh"})
        claude = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}
        resolved = roles.resolve(config("unlimited", **{"bug-fix": [claude]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]["seats"][0]
        self.assertEqual(resolved["options"], {"effort": "max"})

    def test_budget_keeps_a_lower_explicit_choice(self):
        seat = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "low"}}
        resolved = roles.resolve(config("large", **{"bug-fix": [seat]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]["seats"][0]
        self.assertEqual(resolved["options"], {"reasoningEffort": "low"})
