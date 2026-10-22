import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "t3/scripts"))
sys.path.append(str(ROOT / "scripts"))

import json
import tempfile
import roles  # noqa: E402
from tests.pstack_t3.support import CATALOG, config, _context_catalog, _EFFORT_OPTION, _WINDOW_OPTION


class RolesConfigTest(unittest.TestCase):
    def test_budget_leaves_models_without_effort_alone(self):
        seat = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"fastMode": True}}
        resolved = roles.resolve(config("small", **{"bug-fix": [seat]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]["seats"][0]
        self.assertEqual(resolved["options"], {"fastMode": True, "effort": "medium"})

    def test_model_fallback_keeps_options_the_fallback_model_supports(self):
        seat = {"providerInstanceId": "codex", "model": "gone", "options": {"reasoningEffort": "low", "serviceTier": "priority"}}
        entry = roles.resolve(config("large", **{"bug-fix": [seat]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(entry["seats"][0]["options"], {"reasoningEffort": "low", "serviceTier": "priority"})

    def test_parse_seat(self):
        self.assertEqual(roles.parse_seat("inherit"), "inherit")
        self.assertEqual(roles.parse_seat("codex/gpt-6.1-sol?reasoningEffort=high&fast=true"),
                         {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "high", "fast": True}})
        with self.assertRaises(roles.RolesError):
            roles.parse_seat("gpt-6.1-sol")

    def test_single_role_rejects_two_seats(self):
        with self.assertRaises(roles.RolesError):
            roles.check_shape({"roles": {"bug-fix": ["inherit", "inherit"]}}, "test")

    def test_project_roles_override_user_roles_per_role(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "user.json").write_text(json.dumps({"budget": "small", "roles": {"bug-fix": ["inherit"], "hillclimb": ["inherit"]}}))
            (base / "project.json").write_text(json.dumps({"roles": {"bug-fix": [{"providerInstanceId": "grok", "model": "grok-4.7"}]}}))
            merged = roles.merged_config(base, base / "user.json", base / "project.json")
        self.assertEqual(merged["budget"], "small")
        self.assertEqual(merged["roles"]["bug-fix"][0]["providerInstanceId"], "grok")
        self.assertEqual(merged["roles"]["hillclimb"], ["inherit"])

    def test_project_write_without_budget_keeps_the_user_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / ".git").mkdir()
            user = base / "user.json"
            user.write_text(json.dumps({"budget": "large", "roles": {}}))
            catalog = str(ROOT / "tests/fixtures/catalog.json")
            self.assertEqual(roles.main(["write", "--project", "--cwd", directory, "--catalog", catalog, "--set", "bug-fix=inherit"]), 0)
            project = base / ".pstack/t3-roles.json"
            self.assertNotIn("budget", json.loads(project.read_text()))
            self.assertEqual(roles.merged_config(base, user, project)["budget"], "large")

    def test_write_refuses_seats_outside_the_catalog_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "roles.json"
            catalog = str(ROOT / "tests/fixtures/catalog.json")
            bad = roles.main(["write", "--config", str(target), "--catalog", catalog, "--set", "bug-fix=cursor/default"])
            self.assertEqual(bad, 2)
            self.assertFalse(target.exists())
            args = ["write", "--config", str(target), "--catalog", catalog, "--budget", "large", "--set", "interrogate reviewers=inherit;codex/gpt-6.1-sol"]
            self.assertEqual(roles.main(args), 0)
            first = target.read_text()
            self.assertEqual(roles.main(args), 0)
            self.assertEqual(target.read_text(), first)
            self.assertEqual(json.loads(first)["roles"]["interrogate reviewers"][1]["model"], "gpt-6.1-sol")

    def test_a_configured_seat_is_used_as_configured(self):
        seat = {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "high"}}
        entry = roles.resolve(config("large", **{"bug-fix": [seat]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [seat])
        self.assertEqual(entry["source"], "test")
        self.assertNotIn("notes", entry)

    def test_configured_panel_keeps_its_seat_count(self):
        seats = ["inherit", {"providerInstanceId": "codex", "model": "gpt-6.1-sol"}, {"providerInstanceId": "grok", "model": "grok-4.7"}]
        entry = roles.resolve(config(**{"interrogate reviewers": seats}), CATALOG, ["interrogate reviewers"])["roles"]["interrogate reviewers"]
        self.assertEqual(entry["seats"][0], {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}})
        self.assertEqual(len(entry["seats"]), 3)
        self.assertEqual(entry["source"], "test")

    def test_resolve_does_not_mutate_inputs_and_repeats(self):
        catalog = json.loads(json.dumps(CATALOG))
        before_catalog = json.dumps(catalog)
        before_roles = json.dumps(roles.ROLES)
        cfg = config()
        before_cfg = json.dumps(cfg)
        first = roles.resolve(cfg, catalog)
        second = roles.resolve(cfg, catalog)
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(catalog), before_catalog)
        self.assertEqual(json.dumps(roles.ROLES), before_roles)
        self.assertEqual(json.dumps(cfg), before_cfg)

    def test_runtime_describes_the_unset_role_policy(self):
        text = (ROOT / "t3/added/pstack-runtime/roles.md").read_text()
        self.assertIn("There are no built-in model picks.", text)
        self.assertIn("Do not reconstruct role defaults", text)
        self.assertIn("`gpt-6.1-sol` is `gpt`", text)
        self.assertNotIn('"model": "gpt-6.1-sol"', text)
        defaults = text.split("## Built-in defaults", 1)[1].split("## Excluded seats", 1)[0]
        self.assertIn('resolves to `"inherit"`', defaults)
        self.assertIn("roles.py show` owns this mapping", defaults)

    def test_setup_stops_before_writing_when_watch_is_absent(self):
        text = (ROOT / "t3/setup.md").read_text()
        self.assertIn(
            'Say "Setup cannot finish. T3 Code 0.1.2 or later is required because this host does not expose watch_pull_request."',
            text,
        )
        self.assertIn("stop setup before writing roles or a saved catalog", text)
        self.assertIn("A deferred `watch_pull_request` counts as present", text)
        self.assertIn("dummy PR", text)

    def test_configured_haiku_seat_loads_on_any_role(self):
        seat = {"providerInstanceId": "claudeAgent", "model": "anthropic/claude-haiku-5-5"}
        roles.check_shape({"roles": {"swarm workers": [seat]}}, "roles.json")

    def test_missing_model_falls_back_to_the_first_model_the_exclude_list_allows(self):
        catalog = {
            "inheritedProviderInstanceId": "grok",
            "inheritedModel": "grok-4.7",
            "providers": [{
                "providerInstanceId": "cursor", "canRunChildTask": True, "constraints": [],
                "models": [
                    {"id": "claude-haiku-4-5", "options": []},
                    {"id": "claude-haiku-5-5", "options": [{"id": "effort", "type": "select", "options": [{"id": "high"}]}]},
                ],
            }],
        }
        entry = roles.resolve(
            {**config(**{"bug-fix": [{"providerInstanceId": "cursor", "model": "claude-gone"}]}),
             "exclude": roles.parse_exclude([{"model": "*claude-haiku-4-5*"}], "test")},
            catalog,
            ["bug-fix"],
        )["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "cursor", "model": "claude-haiku-5-5", "options": {"effort": "high"}}])

    def test_haiku_parent_inherit_resolves_for_how_explorer(self):
        catalog = {
            "inheritedProviderInstanceId": "claudeAgent",
            "inheritedModel": "claude-haiku-5-5",
            "providers": [{
                "providerInstanceId": "claudeAgent", "canRunChildTask": True, "constraints": [],
                "models": [{"id": "claude-haiku-5-5", "options": [
                    {"id": "effort", "type": "select", "options": [{"id": "medium", "isDefault": True}, {"id": "high"}]},
                ]}],
            }],
        }
        entry = roles.resolve(config(**{"how explorer": ["inherit"]}), catalog, ["how explorer"])["roles"]["how explorer"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "claudeAgent", "model": "claude-haiku-5-5", "options": {"effort": "medium"}}])
        self.assertEqual(entry["haikuBrief"], list(roles.HAIKU_BRIEF))

class FixedContextWindowTest(unittest.TestCase):
    def _entry(self, provider_id, model_options, saved_options):
        seat = {"providerInstanceId": provider_id, "model": "claude-opus-5-5", "options": saved_options}
        catalog = _context_catalog(provider_id, model_options)
        return roles.resolve(config(**{"judgment and prose": [seat]}), catalog, ["judgment and prose"])["roles"]["judgment and prose"]

    def test_native_claude_5_drops_a_saved_context_window(self):
        entry = self._entry("claudeAgent", [_EFFORT_OPTION], {"effort": "xhigh", "contextWindow": "1m"})
        self.assertEqual(entry["seats"], [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}}])
        self.assertEqual(entry["notes"], ["dropped unknown options contextWindow"])
        setup = (ROOT / "t3/setup.md").read_text()
        self.assertIn("`dropped unknown options contextWindow`", setup)
        self.assertIn("because T3 Code fixes those models at 1M context", setup)

    def test_keeps_context_window_when_the_catalog_offers_it(self):
        saved = {"effort": "xhigh", "contextWindow": "1m"}
        entry = self._entry("claudeAgent", [_EFFORT_OPTION, _WINDOW_OPTION], saved)
        self.assertEqual(entry["seats"], [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {**saved, "effort": "medium"}}])
        self.assertNotIn("notes", entry)
        setup = (ROOT / "t3/setup.md").read_text()
        self.assertIn("Write `contextWindow` only for a model whose catalog entry offers it.", setup)

    def test_cursor_claude_5_keeps_a_saved_context_window(self):
        saved = {"effort": "xhigh", "contextWindow": "300k"}
        entry = self._entry("cursor", [_EFFORT_OPTION, _WINDOW_OPTION], saved)
        self.assertEqual(entry["seats"], [{"providerInstanceId": "cursor", "model": "claude-opus-5-5", "options": {**saved, "effort": "medium"}}])
        self.assertNotIn("notes", entry)
        self.assertEqual(entry["seats"][0]["options"]["contextWindow"], "300k")
        setup = (ROOT / "t3/setup.md").read_text()
        self.assertIn("Cursor's Claude 5 models still offer it.", setup)
