import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "t3/scripts"))
sys.path.insert(0, str(ROOT / "scripts"))

import check  # noqa: E402
import roles  # noqa: E402

CATALOG = json.loads((ROOT / "tests/fixtures/catalog.json").read_text())


def config(budget="default", **assigned):
    return {"budget": budget, "roles": assigned, "sources": {name: "test" for name in assigned}}


GROK_SEAT = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "xhigh"}}
OPUS_SEAT = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}}
CODE_ROLES = (
    "feature, refactoring", "bug-fix", "perf-issue", "hillclimb",
    "swarm workers", "how explorer", "why investigators", "reflect tooling",
)
JUDGMENT_ROLES = (
    "judgment and prose", "hardest tasks", "how explainer", "why synthesizer",
    "reflect judgment, divergent, synthesizer",
)
PANEL_ROLES = ("arena runners", "arena cross-judge pool", "architect runners", "interrogate reviewers")


class RolesTest(unittest.TestCase):
    def test_fixture_defaults_are_grok_for_code_and_opus_for_judgment(self):
        for name in CODE_ROLES:
            entry = roles.resolve(config(), CATALOG, [name])["roles"][name]
            self.assertEqual(entry["seats"], [GROK_SEAT], name)
            self.assertNotIn("notes", entry, name)
        for name in JUDGMENT_ROLES:
            entry = roles.resolve(config(), CATALOG, [name])["roles"][name]
            self.assertEqual(entry["seats"], [OPUS_SEAT], name)
            self.assertNotIn("notes", entry, name)
        for name in PANEL_ROLES:
            entry = roles.resolve(config(), CATALOG, [name])["roles"][name]
            self.assertEqual(entry["seats"], [OPUS_SEAT, GROK_SEAT], name)
            self.assertNotIn("notes", entry, name)

    def test_every_role_has_one_default_policy(self):
        self.assertEqual(set(roles.ROLE_DEFAULTS), set(roles.ROLES))
        for name in roles.SINGLE_ROLES:
            policy = roles.ROLE_DEFAULTS[name]
            if name == "skill tests":
                self.assertIs(policy, roles.AdaptiveDefault.SKILL_TESTS)
            else:
                self.assertEqual(len(policy), 1)
        for name in roles.PANEL_ROLES:
            policy = roles.ROLE_DEFAULTS[name]
            if name == "verifiers":
                self.assertIs(policy, roles.AdaptiveDefault.VERIFIERS)
            else:
                self.assertEqual(len(policy), 2)

    def test_setup_examples_match_resolved_fixture_defaults(self):
        text = (ROOT / "t3/setup.md").read_text()
        examples = re.findall(r'--set "([^"]+)"', text)
        self.assertEqual(examples, [
            "judgment and prose=claudeAgent/claude-opus-5-5?effort=xhigh",
            "swarm workers=grok/grok-4.7?reasoningEffort=xhigh",
            "interrogate reviewers=claudeAgent/claude-opus-5-5?effort=xhigh;grok/grok-4.7?reasoningEffort=xhigh",
        ])
        for example in examples:
            name, value = example.split("=", 1)
            expected = [roles.parse_seat(part) for part in value.split(";")]
            entry = roles.resolve(config(), CATALOG, [name])["roles"][name]
            self.assertEqual(entry["seats"], expected, name)
            self.assertNotIn("notes", entry, name)

    def test_show_prints_the_builtin_bug_fix_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "XDG_CONFIG_HOME": directory}
            completed = subprocess.run(
                [sys.executable, str(ROOT / "t3/scripts/roles.py"), "show", "--cwd", directory,
                 "--catalog", str(ROOT / "tests/fixtures/catalog.json"), "--role", "bug-fix",
                 "--parent", "claudeAgent/claude-opus-5-5"],
                env=env, capture_output=True, text=True, check=True)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [GROK_SEAT])
        self.assertEqual(entry["source"], "default")

    def test_preferred_seat_matching_the_parent_stays_explicit(self):
        catalog = {**CATALOG, "inheritedProviderInstanceId": "grok", "inheritedModel": "grok-4.7"}
        entry = roles.resolve(config(), catalog, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [GROK_SEAT])
        self.assertNotIn("notes", entry)

    def test_skill_tests_default_is_one_other_family_seat(self):
        entry = roles.resolve(config(), CATALOG, ["skill tests"])["roles"]["skill tests"]
        self.assertEqual(entry["source"], "default")
        self.assertEqual(entry["seats"], [{"providerInstanceId": "codex", "model": "gpt-6-luna"}])

    def test_skill_tests_small_budget_caps_the_seat(self):
        seats = roles.resolve(config("small"), CATALOG, ["skill tests"])["roles"]["skill tests"]["seats"]
        self.assertEqual(seats, [{"providerInstanceId": "codex", "model": "gpt-6-luna", "options": {"reasoningEffort": "medium"}}])

    def test_skill_tests_falls_back_to_another_family_flagship_when_no_small_tier_exists(self):
        catalog = {
            "inheritedProviderInstanceId": "claudeAgent",
            "inheritedModel": "claude-opus-5-5",
            "providers": [
                {"providerInstanceId": "claudeAgent", "canRunChildTask": True, "constraints": [],
                 "models": [{"id": "claude-opus-5-5", "options": []}]},
                {"providerInstanceId": "codex", "canRunChildTask": True, "constraints": [],
                 "models": [
                     {"id": "gpt-6-terra", "options": [{"id": "reasoningEffort", "type": "select", "options": [{"id": "medium", "isDefault": True}, {"id": "high"}]}]},
                     {"id": "gpt-6.1-sol", "options": [{"id": "reasoningEffort", "type": "select", "options": [{"id": "low", "isDefault": True}, {"id": "medium"}, {"id": "high"}]}]},
                 ]},
            ],
        }
        seats = roles.resolve(config(), catalog, ["skill tests"])["roles"]["skill tests"]["seats"]
        self.assertEqual(seats, [{"providerInstanceId": "codex", "model": "gpt-6.1-sol"}])

    def test_runtime_lists_the_same_small_tier_tokens(self):
        text = (ROOT / "t3/runtime.md").read_text()
        match = re.search(r"prefer an id token in (.+?), then the lowest", text)
        self.assertIsNotNone(match)
        named = re.findall(r"`([a-z]+)`", match.group(1))
        self.assertEqual(set(named), set(roles.SMALL_TIER))

    def test_skill_tests_uses_a_small_model_inside_the_only_family(self):
        catalog = {**CATALOG, "providers": [p for p in CATALOG["providers"] if p["providerInstanceId"] == "claudeAgent"]}
        seats = roles.resolve(config(), catalog, ["skill tests"])["roles"]["skill tests"]["seats"]
        self.assertEqual(seats, [{"providerInstanceId": "claudeAgent", "model": "claude-haiku-4-5"}])
        capped = roles.resolve(config("small"), catalog, ["skill tests"])["roles"]["skill tests"]["seats"]
        self.assertEqual(capped, [{"providerInstanceId": "claudeAgent", "model": "claude-haiku-4-5"}])

    def test_skill_tests_without_a_catalog_inherits(self):
        self.assertEqual(roles.resolve(config(), None, ["skill tests"])["roles"]["skill tests"]["seats"], ["inherit"])

    def test_runtime_role_table_lists_the_same_names_as_the_role_list(self):
        text = (ROOT / "t3/runtime.md").read_text()
        section = text.split("### Role names", 1)[1].split("### Built-in defaults", 1)[0]
        names = re.findall(r"\| `([^`]+)` \|", section)
        self.assertEqual(sorted(names), sorted(roles.ROLES))
        self.assertEqual(len(names), len(set(names)))
        proposals = (ROOT / "t3/setup.md").read_text().split("**(b) Propose roles.**", 1)[1].split("**(c) Confirm.**", 1)[0]
        self.assertIn("`skill tests`", proposals)

    def test_panel_default_is_one_seat_per_runnable_provider_with_parent_inheriting(self):
        seats = roles.resolve(config(), CATALOG, ["verifiers"])["roles"]["verifiers"]["seats"]
        self.assertEqual(seats, [
            "inherit",
            {"providerInstanceId": "codex", "model": "gpt-6.1-sol"},
            {"providerInstanceId": "grok", "model": "grok-4.7"},
        ])

    def test_panel_skips_a_provider_serving_a_family_already_seated(self):
        cursor = {"providerInstanceId": "cursor", "canRunChildTask": True, "constraints": [],
                  "models": [{"id": "claude-opus-5-5", "options": None}]}
        catalog = {**CATALOG, "providers": CATALOG["providers"] + [cursor]}
        seats = roles.resolve(config(), catalog, ["verifiers"])["roles"]["verifiers"]["seats"]
        self.assertNotIn({"providerInstanceId": "cursor", "model": "claude-opus-5-5"}, seats)
        self.assertEqual(len(seats), 3)

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
        seats = roles.resolve(config("small", **{"bug-fix": ["inherit"]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]["seats"]
        self.assertEqual(seats, [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}}])
        entry = roles.resolve(config("small", **{"bug-fix": ["inherit"]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]
        self.assertNotIn("notes", entry)
        self.assertIn("inherit made explicit", entry["info"][0])
        plain = roles.resolve(config(), CATALOG, ["bug-fix"])["roles"]["bug-fix"]["seats"]
        self.assertEqual(plain, [GROK_SEAT])
        capped = roles.resolve(config("small"), CATALOG, ["bug-fix"])["roles"]["bug-fix"]["seats"]
        self.assertEqual(capped, [{"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "medium"}}])

    def test_unlimited_budget_raises_builtin_seats_to_the_highest_non_special_level(self):
        grok = roles.resolve(config("unlimited"), CATALOG, ["bug-fix"])["roles"]["bug-fix"]["seats"]
        self.assertEqual(grok, [GROK_SEAT])
        opus_max = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "max"}}
        judgment = roles.resolve(config("unlimited"), CATALOG, ["judgment and prose"])["roles"]["judgment and prose"]["seats"]
        self.assertEqual(judgment, [opus_max])
        panel = roles.resolve(config("unlimited"), CATALOG, ["interrogate reviewers"])["roles"]["interrogate reviewers"]["seats"]
        self.assertEqual(panel, [opus_max, GROK_SEAT])
        verifiers = roles.resolve(config("unlimited"), CATALOG, ["verifiers"])["roles"]["verifiers"]["seats"]
        self.assertEqual(verifiers[0], opus_max)
        self.assertEqual(verifiers[2]["options"]["reasoningEffort"], "xhigh")
        named = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}}
        kept = roles.resolve(config("unlimited", **{"judgment and prose": [named]}), CATALOG, ["judgment and prose"])["roles"]["judgment and prose"]["seats"]
        self.assertEqual(kept, [named])

    def test_unlimited_with_no_grok_stops_at_max_on_a_codex_parent(self):
        providers = [provider for provider in CATALOG["providers"] if provider["providerInstanceId"] != "grok"]
        catalog = {
            **CATALOG,
            "providers": providers,
            "inheritedProviderInstanceId": "codex",
            "inheritedModel": "gpt-6.1-sol",
        }
        codex_max = {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "max"}}
        for name in CODE_ROLES:
            seats = roles.resolve(config("unlimited"), catalog, [name])["roles"][name]["seats"]
            self.assertEqual(seats, [codex_max], name)
        panel = roles.resolve(config("unlimited"), catalog, ["interrogate reviewers"])["roles"]["interrogate reviewers"]["seats"]
        self.assertEqual(panel[1], codex_max)
        explicit = {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "ultra"}}
        capped = roles.resolve(config("unlimited", **{"bug-fix": [explicit]}), catalog, ["bug-fix"])["roles"]["bug-fix"]["seats"]
        self.assertEqual(capped, [codex_max])
        verifiers = roles.resolve(config("unlimited"), catalog, ["verifiers"])["roles"]["verifiers"]["seats"]
        self.assertEqual(verifiers[0], codex_max)
        self.assertNotIn("ultra", json.dumps(verifiers))
        full = roles.resolve(config("unlimited"), CATALOG, ["verifiers"])["roles"]["verifiers"]["seats"]
        self.assertEqual(full[1]["options"]["reasoningEffort"], "max")

    def test_show_writes_ultra_under_every_budget_when_it_is_the_only_level(self):
        only_ultra = {
            "inheritedProviderInstanceId": "codex",
            "inheritedModel": "gpt-6.1-sol",
            "providers": [{
                "providerInstanceId": "codex", "canRunChildTask": True, "constraints": [],
                "models": [{"id": "gpt-6.1-sol", "options": [
                    {"id": "reasoningEffort", "type": "select", "options": [{"id": "ultra"}]},
                ]}],
            }],
        }
        ultra = {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "ultra"}}
        codex_max = {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "max"}}

        def show(budget, catalog, seat=None):
            with tempfile.TemporaryDirectory() as directory:
                roles_file = Path(directory) / "pstack-t3" / "roles.json"
                roles_file.parent.mkdir()
                roles_file.write_text(json.dumps({"version": 1, "budget": budget, "roles": {"bug-fix": [seat]} if seat else {}}))
                completed = subprocess.run(
                    [sys.executable, str(ROOT / "t3/scripts/roles.py"), "show", "--cwd", directory,
                     "--catalog", "-", "--parent", "codex/gpt-6.1-sol", "--role", "bug-fix"],
                    input=json.dumps(catalog), env={**os.environ, "XDG_CONFIG_HOME": directory},
                    capture_output=True, text=True, check=True)
            return json.loads(completed.stdout)["roles"]["bug-fix"]["seats"]

        for budget in ("default", "small", "medium", "large", "unlimited"):
            self.assertEqual(show(budget, only_ultra), [ultra], budget)
            self.assertEqual(show(budget, only_ultra, ultra), [ultra], budget)
        self.assertEqual(show("default", CATALOG, ultra), [ultra])
        self.assertEqual(show("unlimited", CATALOG, ultra), [codex_max])

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
            self.assertEqual(json.loads(valid_stdin.stdout)["roles"]["bug-fix"]["seats"], [GROK_SEAT])

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
            snapshot = base / "pstack-t3" / "catalog.json"
            snapshot.parent.mkdir()
            snapshot.write_text(json.dumps(CATALOG))
            env = {**os.environ, "XDG_CONFIG_HOME": str(base)}
            run = lambda *extra: json.loads(subprocess.run([sys.executable, str(ROOT / "t3/scripts/roles.py"), "show", "--cwd", directory, "--role", "verifiers", *extra],
                                                           env=env, capture_output=True, text=True, check=True).stdout)
            seats = run("--parent", "codex/gpt-6.1-sol")["roles"]["verifiers"]["seats"]
            self.assertEqual(seats[0], "inherit")
            self.assertIn({"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}, seats)
            self.assertNotIn({"providerInstanceId": "codex", "model": "gpt-6.1-sol"}, seats)

    def test_panel_with_one_runnable_provider_is_three_inherit_seats(self):
        catalog = {**CATALOG, "providers": [p for p in CATALOG["providers"] if p["providerInstanceId"] == "claudeAgent"]}
        seats = roles.resolve(config(), catalog, ["verifiers"])["roles"]["verifiers"]["seats"]
        self.assertEqual(seats, ["inherit", "inherit", "inherit"])

    def test_arena_runners_with_one_runnable_provider_keeps_two_fallback_seats(self):
        catalog = {**CATALOG, "providers": [p for p in CATALOG["providers"] if p["providerInstanceId"] == "claudeAgent"]}
        entry = roles.resolve(config(), catalog, ["arena runners"])["roles"]["arena runners"]
        self.assertEqual(entry["seats"], [OPUS_SEAT, OPUS_SEAT])
        self.assertNotIn("fastMode", entry["seats"][1].get("options", {}))
        self.assertEqual(entry["notes"], [
            "arena runners seat 2: wanted grok-4.7, using claudeAgent/claude-opus-5-5 (missing family)",
            "arena runners: seats 1 and 2 both use claudeAgent/claude-opus-5-5, so the panel lost a distinct model",
        ])

    def test_without_catalog_panels_are_reported_for_the_agent_to_expand(self):
        entry = roles.resolve(config(), None, ["verifiers"])["roles"]["verifiers"]
        self.assertEqual(entry["seats"], "default-panel")
        self.assertIn("orchestrator_capabilities", entry["note"])

    def test_preferred_roles_without_a_catalog_ask_for_one(self):
        entry = roles.resolve(config(), None, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], "catalog-required")
        self.assertEqual(entry["note"], "call orchestrator_capabilities and rerun roles.py show --catalog")
        panel = roles.resolve(config(), None, ["arena runners"])["roles"]["arena runners"]
        self.assertEqual(panel["seats"], "catalog-required")
        skill = roles.resolve(config(), None, ["skill tests"])["roles"]["skill tests"]
        self.assertEqual(skill["seats"], ["inherit"])
        self.assertNotIn("note", skill)
        configured = roles.resolve(config(**{"bug-fix": ["inherit"]}), None, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(configured["seats"], ["inherit"])
        self.assertEqual(configured["source"], "test")

    def test_unrunnable_provider_falls_back_to_inherit_with_a_note(self):
        entry = roles.resolve(config(**{"swarm workers": [{"providerInstanceId": "cursor", "model": "default"}]}), CATALOG, ["swarm workers"])["roles"]["swarm workers"]
        self.assertEqual(entry["seats"], ["inherit"])
        self.assertIn("not authenticated", entry["notes"][0])

    def test_missing_model_falls_back_to_first_model_of_the_provider(self):
        entry = roles.resolve(config(**{"bug-fix": [{"providerInstanceId": "codex", "model": "gpt-9"}]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "codex", "model": "gpt-6.1-sol"}])
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

    def test_budget_leaves_models_without_effort_alone(self):
        seat = {"providerInstanceId": "claudeAgent", "model": "claude-haiku-4-5", "options": {"thinking": True}}
        resolved = roles.resolve(config("small", **{"bug-fix": [seat]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]["seats"][0]
        self.assertEqual(resolved["options"], {"thinking": True})

    def test_model_fallback_keeps_options_the_fallback_model_supports(self):
        seat = {"providerInstanceId": "codex", "model": "gone", "options": {"reasoningEffort": "low", "serviceTier": "priority"}}
        entry = roles.resolve(config("large", **{"bug-fix": [seat]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(entry["seats"][0]["options"], {"reasoningEffort": "low", "serviceTier": "priority"})

    def test_panel_when_parent_provider_cannot_run_children(self):
        catalog = {**CATALOG, "inheritedProviderInstanceId": "cursor", "inheritedModel": "grok-4.7",
                   "providers": [p for p in CATALOG["providers"] if p["providerInstanceId"] in ("cursor", "grok")]}
        seats = roles.resolve(config(), catalog, ["verifiers"])["roles"]["verifiers"]["seats"]
        self.assertEqual(seats, [{"providerInstanceId": "grok", "model": "grok-4.7"}] * 3)

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

    def test_missing_grok_model_names_the_replacement(self):
        grok = {
            "providerInstanceId": "grok", "canRunChildTask": True, "constraints": [],
            "models": [{"id": "grok-4.5", "options": [{"id": "reasoningEffort", "type": "select", "options": [
                {"id": "xhigh"}, {"id": "high"}, {"id": "medium"}, {"id": "low"}]}]}],
        }
        providers = [grok if provider["providerInstanceId"] == "grok" else provider for provider in CATALOG["providers"]]
        entry = roles.resolve(config(), {**CATALOG, "providers": providers}, ["swarm workers"])["roles"]["swarm workers"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "grok", "model": "grok-4.5", "options": {"reasoningEffort": "xhigh"}}])
        self.assertEqual(entry["notes"], ["swarm workers seat 1: wanted grok-4.7, using grok/grok-4.5 (missing model)"])

    def test_codex_only_catalog_substitutes_both_panel_seats(self):
        codex = next(provider for provider in CATALOG["providers"] if provider["providerInstanceId"] == "codex")
        catalog = {"inheritedProviderInstanceId": "codex", "inheritedModel": "gpt-6.1-sol", "providers": [codex]}
        entry = roles.resolve(config(), catalog, ["interrogate reviewers"])["roles"]["interrogate reviewers"]
        seat = {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "xhigh"}}
        self.assertEqual(entry["seats"], [seat, seat])
        self.assertEqual(entry["notes"], [
            "interrogate reviewers seat 1: wanted claude-opus-5-5, using codex/gpt-6.1-sol (missing family)",
            "interrogate reviewers seat 2: wanted grok-4.7, using codex/gpt-6.1-sol (missing family)",
            "interrogate reviewers: seats 1 and 2 both use codex/gpt-6.1-sol, so the panel lost a distinct model",
        ])

    def test_no_runnable_model_raises(self):
        catalog = {"providers": [{"providerInstanceId": "cursor", "canRunChildTask": False,
                                  "constraints": ["Provider is not authenticated."], "models": [{"id": "default", "options": []}]}]}
        for name in ("bug-fix", "verifiers"):
            with self.assertRaises(roles.RolesError) as caught:
                roles.resolve(config(), catalog, [name])
            self.assertIn("no provider in the catalog can run child tasks", str(caught.exception))

    def test_custom_runnable_provider_serves_a_preferred_model(self):
        grok = next(provider for provider in CATALOG["providers"] if provider["providerInstanceId"] == "grok")
        blocked = {**grok, "canRunChildTask": False, "constraints": ["Provider is not authenticated."]}
        custom = {
            "providerInstanceId": "acme", "canRunChildTask": True, "constraints": [],
            "models": [{"id": "grok-4.7", "options": [
                {"id": "reasoningEffort", "type": "select", "options": [{"id": "xhigh"}, {"id": "high"}]},
                {"id": "fastMode", "type": "boolean"},
            ]}],
        }
        providers = [blocked if provider["providerInstanceId"] == "grok" else provider for provider in CATALOG["providers"]]
        providers.append(custom)
        entry = roles.resolve(config(), {**CATALOG, "providers": providers}, ["swarm workers"])["roles"]["swarm workers"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "acme", "model": "grok-4.7", "options": {"reasoningEffort": "xhigh", "fastMode": True}}])
        self.assertNotIn("notes", entry)

    def test_unauthenticated_only_copy_falls_back_to_the_parent(self):
        grok = next(provider for provider in CATALOG["providers"] if provider["providerInstanceId"] == "grok")
        blocked = {**grok, "canRunChildTask": False, "constraints": ["Provider is not authenticated."]}
        providers = [blocked if provider["providerInstanceId"] == "grok" else provider for provider in CATALOG["providers"]]
        entry = roles.resolve(config(), {**CATALOG, "providers": providers}, ["swarm workers"])["roles"]["swarm workers"]
        self.assertEqual(entry["seats"], [OPUS_SEAT])
        self.assertNotIn("fastMode", entry["seats"][0]["options"])
        self.assertEqual(entry["notes"], ["swarm workers seat 1: wanted grok-4.7, using claudeAgent/claude-opus-5-5 (missing family)"])

    def test_claude_and_codex_catalog_without_grok_does_not_set_fast_mode(self):
        providers = [provider for provider in CATALOG["providers"] if provider["providerInstanceId"] in ("claudeAgent", "codex")]
        catalog = {**CATALOG, "providers": providers}
        entry = roles.resolve(config(), catalog, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [OPUS_SEAT])
        self.assertNotIn("fastMode", entry["seats"][0]["options"])
        self.assertIn("missing family", entry["notes"][0])
        panel = roles.resolve(config(), catalog, ["interrogate reviewers"])["roles"]["interrogate reviewers"]
        self.assertEqual(panel["seats"][1], OPUS_SEAT)
        self.assertNotIn("fastMode", panel["seats"][1]["options"])

    def test_exact_model_prefers_the_provider_whose_first_model_is_in_the_family(self):
        wrapper = {
            "providerInstanceId": "wrapper", "canRunChildTask": True, "constraints": [],
            "models": [
                {"id": "gpt-9", "options": []},
                {"id": "grok-4.7", "options": [{"id": "reasoningEffort", "type": "select", "options": [{"id": "xhigh"}]}]},
            ],
        }
        grok = next(provider for provider in CATALOG["providers"] if provider["providerInstanceId"] == "grok")
        providers = [wrapper] + [provider for provider in CATALOG["providers"] if provider["providerInstanceId"] != "grok"] + [grok]
        entry = roles.resolve(config(), {**CATALOG, "providers": providers}, ["swarm workers"])["roles"]["swarm workers"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "xhigh"}}])
        self.assertNotIn("notes", entry)

    def test_exact_model_keeps_catalog_order_when_no_first_model_is_in_the_family(self):
        def carrier(provider_id, first_model):
            return {
                "providerInstanceId": provider_id, "canRunChildTask": True, "constraints": [],
                "models": [
                    {"id": first_model, "options": []},
                    {"id": "grok-4.7", "options": [{"id": "reasoningEffort", "type": "select", "options": [{"id": "xhigh"}]}]},
                ],
            }
        providers = [carrier("first", "gpt-9"), carrier("second", "claude-haiku-9")]
        providers.extend(provider for provider in CATALOG["providers"] if provider["providerInstanceId"] != "grok")
        entry = roles.resolve(config(), {**CATALOG, "providers": providers}, ["swarm workers"])["roles"]["swarm workers"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "first", "model": "grok-4.7", "options": {"reasoningEffort": "xhigh"}}])
        self.assertNotIn("notes", entry)

    def test_preferred_seat_notes_a_lower_declared_effort(self):
        grok = {
            "providerInstanceId": "grok", "canRunChildTask": True, "constraints": [],
            "models": [{"id": "grok-4.7", "options": [{"id": "reasoningEffort", "type": "select", "options": [
                {"id": "high"}, {"id": "medium"}, {"id": "low"}]}]}],
        }
        providers = [grok if provider["providerInstanceId"] == "grok" else provider for provider in CATALOG["providers"]]
        entry = roles.resolve(config(), {**CATALOG, "providers": providers}, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "high"}}])
        self.assertEqual(entry["notes"], ["bug-fix seat 1: wanted xhigh, using high"])

    def test_preferred_seat_adds_no_effort_option_when_the_model_declares_none(self):
        grok = {"providerInstanceId": "grok", "canRunChildTask": True, "constraints": [], "models": [{"id": "grok-4.7", "options": []}]}
        providers = [grok if provider["providerInstanceId"] == "grok" else provider for provider in CATALOG["providers"]]
        entry = roles.resolve(config(), {**CATALOG, "providers": providers}, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "grok", "model": "grok-4.7"}])
        self.assertNotIn("notes", entry)

    def test_extra_high_spelling_satisfies_the_builtin_ceiling(self):
        grok = {
            "providerInstanceId": "grok", "canRunChildTask": True, "constraints": [],
            "models": [{"id": "grok-4.7", "options": [{"id": "reasoningEffort", "type": "select", "options": [
                {"id": "high"}, {"id": "extra-high"}]}]}],
        }
        providers = [grok if provider["providerInstanceId"] == "grok" else provider for provider in CATALOG["providers"]]
        entry = roles.resolve(config(), {**CATALOG, "providers": providers}, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "extra-high"}}])
        self.assertNotIn("notes", entry)

    def test_explicit_codex_seat_wins_over_the_builtin(self):
        seat = {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "high"}}
        entry = roles.resolve(config("large", **{"bug-fix": [seat]}), CATALOG, ["bug-fix"])["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [seat])
        self.assertEqual(entry["source"], "test")
        self.assertNotIn("notes", entry)

    def test_configured_panel_keeps_its_seat_count(self):
        seats = ["inherit", {"providerInstanceId": "codex", "model": "gpt-6.1-sol"}, {"providerInstanceId": "grok", "model": "grok-4.7"}]
        entry = roles.resolve(config(**{"interrogate reviewers": seats}), CATALOG, ["interrogate reviewers"])["roles"]["interrogate reviewers"]
        self.assertEqual(entry["seats"][0], "inherit")
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
        self.assertEqual([seat.model_id for seat in (roles.OPUS, roles.GROK)], ["claude-opus-5-5", "grok-4.7"])

    def test_runtime_describes_the_builtin_policy(self):
        text = (ROOT / "t3/runtime.md").read_text()
        self.assertIn("catalog-required", text)
        self.assertIn("Do not reconstruct role defaults", text)
        self.assertNotIn("Expand it yourself", text)
        self.assertIn("`gpt-6.1-sol` is `gpt`", text)
        self.assertNotIn('"model": "gpt-6.1-sol"', text)
        defaults = text.split("### Built-in defaults", 1)[1].split("### Budget", 1)[0]
        self.assertIn('`verifiers` is three `"inherit"` seats', defaults)
        self.assertIn("roles.py show` owns this mapping", defaults)

    def test_setup_stops_before_writing_when_watch_is_absent(self):
        text = (ROOT / "t3/setup.md").read_text()
        self.assertIn(
            'Say "Setup cannot finish. T3 Code 0.0.46-nightly.20261005.2702 or later is required because this host does not expose watch_pull_request."',
            text,
        )
        self.assertIn("stop setup before writing roles or a saved catalog", text)
        self.assertIn("A deferred `watch_pull_request` counts as present", text)
        self.assertIn("dummy PR", text)


class InstallTest(unittest.TestCase):
    def run_install(self, home, *args):
        env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
        env.pop("CLAUDE_CONFIG_DIR", None)
        return subprocess.run([sys.executable, str(ROOT / "scripts/install.py"), *args], env=env, capture_output=True, text=True)

    def setUp(self):
        if not (ROOT / "skills/pstack-runtime/SKILL.md").exists():
            subprocess.run([sys.executable, str(ROOT / "scripts/build.py")], check=True)

    def test_install_refuses_conflicts_then_replace_and_uninstall_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            existing = home / ".claude/skills/swarm"
            existing.mkdir(parents=True)
            (existing / "SKILL.md").write_text("mine")
            refused = self.run_install(home)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("swarm", refused.stderr)
            self.assertFalse((home / ".grok/skills/poteto-mode").exists())
            replaced = self.run_install(home, "--replace")
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
            for harness in (".claude", ".agents", ".grok", ".cursor"):
                self.assertTrue((home / harness / "skills/poteto-mode/SKILL.md").exists(), harness)
            self.assertEqual(self.run_install(home, "doctor").returncode, 0)
            again = self.run_install(home)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("already installed", again.stdout)
            self.assertEqual(self.run_install(home, "uninstall").returncode, 0)
            self.assertEqual((existing / "SKILL.md").read_text(), "mine")
            self.assertFalse((home / ".grok/skills/poteto-mode").exists())

    def test_uninstall_dry_run_changes_nothing_and_harness_filter_is_respected(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.assertEqual(self.run_install(home, "--harness", "codex,grok").returncode, 0)
            dry = self.run_install(home, "uninstall", "--dry-run")
            self.assertIn("would remove", dry.stdout)
            self.assertTrue((home / ".agents/skills/swarm").is_symlink())
            self.assertEqual(self.run_install(home, "uninstall", "--harness", "codex").returncode, 0)
            self.assertFalse((home / ".agents/skills/swarm").exists())
            self.assertTrue((home / ".grok/skills/swarm").is_symlink())
            self.assertEqual(self.run_install(home, "uninstall").returncode, 0)
            self.assertFalse((home / ".grok/skills/swarm").exists())

    def test_blocked_restore_keeps_its_backup_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            mine = home / ".grok/skills/swarm"
            mine.mkdir(parents=True)
            (mine / "SKILL.md").write_text("mine")
            self.assertEqual(self.run_install(home, "--harness", "grok", "--replace").returncode, 0)
            (mine).unlink()
            mine.mkdir()
            (mine / "SKILL.md").write_text("squatter")
            self.run_install(home, "uninstall")
            self.assertEqual((mine / "SKILL.md").read_text(), "squatter")
            shutil.rmtree(mine)
            self.run_install(home, "uninstall")
            self.assertEqual((mine / "SKILL.md").read_text(), "mine")

    def test_two_replacements_in_one_second_keep_both_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = home / ".grok/skills/swarm"
            target.parent.mkdir(parents=True)
            for text in ("first", "second"):
                if target.is_symlink():
                    target.unlink()
                target.write_text(text)
                self.assertEqual(self.run_install(home, "--harness", "grok", "--replace").returncode, 0)
            backups = sorted(p.read_text() for p in (home / ".config/pstack-t3/backups").rglob("swarm"))
            self.assertEqual(backups, ["first", "second"])

    def test_replace_never_moves_the_checkout_when_a_skills_dir_points_into_it(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".grok").mkdir()
            (home / ".grok/skills").symlink_to(ROOT / "skills")
            result = self.run_install(home, "--harness", "grok", "--replace")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((ROOT / "skills/swarm/SKILL.md").is_file())
            self.assertFalse((ROOT / "skills/swarm").is_symlink())
            self.assertEqual(self.run_install(home, "uninstall").returncode, 0)
            self.assertTrue((ROOT / "skills/swarm/SKILL.md").is_file())

    def test_shared_directory_uninstalls_only_when_every_sharing_harness_is_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".agents/skills").mkdir(parents=True)
            (home / ".grok").mkdir()
            (home / ".grok/skills").symlink_to(home / ".agents/skills")
            self.assertEqual(self.run_install(home, "--harness", "codex,grok").returncode, 0)
            partial = self.run_install(home, "uninstall", "--harness", "grok")
            self.assertIn("shared with codex", partial.stdout)
            self.assertTrue((home / ".agents/skills/swarm").is_symlink())
            self.assertEqual(self.run_install(home, "uninstall", "--harness", "codex,grok").returncode, 0)
            self.assertFalse((home / ".agents/skills/swarm").exists())

    def test_doctor_fails_when_a_skills_dir_points_inside_one_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".grok").mkdir()
            (home / ".grok/skills").symlink_to(ROOT / "skills/swarm")
            self.assertNotEqual(self.run_install(home, "doctor", "--harness", "grok").returncode, 0)

    def test_doctor_flags_stale_copies_in_directories_a_provider_also_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.assertEqual(self.run_install(home, "--harness", "codex").returncode, 0)
            (home / ".codex/skills/swarm").mkdir(parents=True)
            (home / ".codex/skills/swarm/SKILL.md").write_text("old")
            result = self.run_install(home, "doctor", "--harness", "codex")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(".codex/skills also holds other copies", result.stdout)

    def test_shared_real_directory_is_linked_once(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            shared = home / "shared"
            shared.mkdir()
            (home / ".grok").mkdir()
            (home / ".cursor").mkdir()
            (home / ".grok/skills").symlink_to(shared)
            (home / ".cursor/skills").symlink_to(shared)
            result = self.run_install(home, "--harness", "grok,cursor")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((shared / "swarm/SKILL.md").exists())


CODE_DELEGATE_SITES = (
    ("poteto-mode/playbooks/feature.md", "4. Delegate code-writing", "Playbook: playbooks/feature.md"),
    ("poteto-mode/playbooks/refactoring.md", "5. Move in small", "Playbook: playbooks/refactoring.md"),
    ("poteto-mode/playbooks/bug-fix.md", "3. Plan the fix.", "Playbook: playbooks/bug-fix.md"),
    ("poteto-mode/playbooks/perf-issue.md", "3. Plan the fix from the trace.", "Playbook: playbooks/perf-issue.md"),
    ("poteto-mode/playbooks/hillclimb.md", "   - Hand the change", "Playbook: playbooks/hillclimb.md"),
    ("swarm/SKILL.md", "Every brief stands alone.", "Playbook: playbooks/<name>.md"),
    ("arena/SKILL.md", "Spawn all N candidates", "Playbook: playbooks/<name>.md"),
)


class CodeDelegateBriefTest(unittest.TestCase):
    def test_every_code_delegate_step_points_at_the_brief_check(self):
        for path, step, playbook_line in CODE_DELEGATE_SITES:
            lines = (ROOT / "skills" / path).read_text().splitlines()
            matches = [line for line in lines if line.startswith(step)]
            self.assertEqual(len(matches), 1, (path, step))
            for needed in ("pstack-runtime/SKILL.md#delegation)", "poteto-agent persona first", playbook_line, "`roles.py check-brief` before `delegate_task`"):
                self.assertIn(needed, matches[0], path)

    def test_routed_skill_exception_excludes_code_writing_children(self):
        text = (ROOT / "skills/poteto-mode/SKILL.md").read_text()
        self.assertIn("It never covers a code-writing child.", text)


class BuildTest(unittest.TestCase):
    def test_generated_tree_passes_check(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "skills"
            result = subprocess.run([sys.executable, str(ROOT / "scripts/build.py"), "--out", str(out)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(check.check_tree(out), [])
            self.assertTrue((out / "pstack-runtime/scripts/roles.py").exists())
            for skill_md in out.glob("*/SKILL.md"):
                self.assertNotIn("disable-model-invocation", skill_md.read_text().split("---")[1], skill_md)

    def test_replace_tree_recovers_an_interrupted_swap_before_copying(self):
        import build
        from unittest import mock
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "dest.outgoing").mkdir()
            (base / "dest.outgoing/old").write_text("old")
            (base / "src").mkdir()
            with mock.patch.object(build.shutil, "copytree", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    build.replace_tree(base / "src", base / "dest")
            self.assertEqual((base / "dest/old").read_text(), "old")

    def test_check_rejects_invalid_yaml_frontmatter(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: demo\ndescription: [unterminated\n---\n\nbody\n")
            self.assertTrue(any("not valid YAML" in finding for finding in check.check_tree(directory)))

    def test_catalog_lists_every_skill_and_playbook(self):
        import catalog
        text = catalog.render(ROOT / "skills")
        for skill_md in (ROOT / "skills").glob("*/SKILL.md"):
            self.assertIn(f"[`{skill_md.parent.name}`]", text)
        self.assertIn("(../skills/poteto-mode/playbooks/bug-fix.md)", text)

    def test_check_flags_cursor_leftovers(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\nSpawn with `subagent_type: generalPurpose`.\n")
            findings = check.check_tree(directory)
        self.assertTrue(any("subagent_type" in finding for finding in findings))

    def test_check_requires_watch_tool_names_on_the_runtime_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "pstack-runtime"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: pstack-runtime\ndescription: d\n---\n\nNo watch here.\n")
            findings = check.check_tree(directory)
            self.assertIn("pstack-runtime/SKILL.md: missing watch_pull_request", findings)
            self.assertIn("pstack-runtime/SKILL.md: missing unwatch_pull_request", findings)
            (skill / "SKILL.md").write_text(
                "---\nname: pstack-runtime\ndescription: d\n---\n\n"
                "Call `watch_pull_request`. Call `unwatch_pull_request`.\n"
            )
            findings = check.check_tree(directory)
        self.assertFalse(any("missing watch" in finding or "missing unwatch" in finding for finding in findings))

    def test_check_requires_the_watch_tool_and_version_on_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "setup-pstack"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: setup-pstack\ndescription: d\n---\n\nNo watch here.\n")
            findings = check.check_tree(directory)
            self.assertIn("setup-pstack/SKILL.md: missing watch_pull_request", findings)
            self.assertIn("setup-pstack/SKILL.md: missing 0.0.46-nightly.20261005.2702", findings)
            (skill / "SKILL.md").write_text(
                "---\nname: setup-pstack\ndescription: d\n---\n\n"
                "Call `watch_pull_request`. T3 Code 0.0.46-nightly.20261005.2702 or later.\n"
            )
            findings = check.check_tree(directory)
        self.assertFalse(any("missing watch_pull_request" in finding or "missing 0.0.46" in finding for finding in findings))

    def test_check_rejects_the_cursor_xhigh_slug(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\nUse claude-opus-5-5-xhigh.\n")
            findings = check.check_tree(directory)
        self.assertTrue(any("claude-opus-5-5-xhigh" in finding for finding in findings))

    def test_check_rejects_an_indented_catalog_heredoc_closer(self):
        bad = "\n".join([
            "---",
            "name: demo",
            "description: d",
            "---",
            "",
            "```bash",
            "python3 tool <<'JSON'",
            '{"ok": true}',
            "  JSON",
            "```",
            "",
        ])
        good = "\n".join([
            "---",
            "name: demo",
            "description: d",
            "---",
            "",
            "```bash",
            "python3 tool <<'JSON'",
            '{"ok": true}',
            "JSON",
            "```",
            "",
        ])
        plain = "\n".join([
            "---",
            "name: demo",
            "description: d",
            "---",
            "",
            "  JSON",
            "",
        ])
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(bad)
            findings = check.check_tree(directory)
        self.assertIn(
            "demo/SKILL.md:9: catalog heredoc closer JSON is indented. Put JSON at column 0",
            findings,
        )
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(good)
            findings = check.check_tree(directory)
        self.assertFalse(any("heredoc closer" in finding for finding in findings))
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(plain)
            findings = check.check_tree(directory)
        self.assertFalse(any("heredoc closer" in finding for finding in findings))

    def test_check_scopes_the_catalog_heredoc_closer_to_its_body(self):
        text = "\n".join([
            "---",
            "name: demo",
            "description: d",
            "---",
            "",
            "  JSON",
            "```bash",
            "python3 tool <<'JSON'",
            '{"name": "JSON"}',
            "JSON ",
            "JSON\t",
            "  JSON",
            "JSON",
            "```",
            "",
            "  JSON",
            "",
        ])
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(text)
            findings = check.check_tree(directory)
        self.assertEqual(findings, [
            "demo/SKILL.md:10: catalog heredoc closer JSON has trailing whitespace. The closer is JSON with nothing after it",
            "demo/SKILL.md:11: catalog heredoc closer JSON has trailing whitespace. The closer is JSON with nothing after it",
            "demo/SKILL.md:12: catalog heredoc closer JSON is indented. Put JSON at column 0",
        ])


def fragment_name(branch):
    return branch.replace("%", "%25").replace("/", "%2F") + ".md"


def fragment_holds_bullets(lines):
    open_bullet = False
    for line in lines:
        if line.startswith("- ") and line[2:].strip():
            open_bullet = True
            continue
        if open_bullet and (line.startswith(" ") or line.startswith("\t")) and line.strip():
            continue
        return False
    return open_bullet


def changelog_findings(root):
    findings = []
    changelog = root / "CHANGELOG.md"
    if changelog.is_file():
        for line in changelog.read_text().splitlines():
            if line.strip() == "## Unreleased":
                findings.append(
                    "CHANGELOG.md has a ## Unreleased heading. Write the bullet under changes/."
                )
                break
    changes = root / "changes"
    if not changes.is_dir():
        return findings
    for path in sorted(p for p in changes.rglob("*") if p.is_file()):
        relative = path.relative_to(changes).as_posix()
        if "/" in relative:
            findings.append(f"changes/{relative} sits in a subdirectory. changes/ is flat.")
            continue
        if not fragment_holds_bullets(path.read_text().splitlines()):
            findings.append(f"changes/{relative} holds a line that is not a bullet.")
    return findings


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "LC_ALL": "C",
        },
    ).stdout


def release_bullets(repo):
    log = _git(
        repo,
        "log",
        "--reverse",
        "--diff-filter=A",
        "--format=",
        "--name-only",
        "--",
        "changes/",
    )
    order = []
    for line in log.splitlines():
        if not line.startswith("changes/") or line.count("/") != 1 or not (repo / line).is_file():
            continue
        if line in order:
            order.remove(line)
        order.append(line)
    bullets = []
    for relative in order:
        bullets.extend((repo / relative).read_text().splitlines())
    return bullets


class ChangelogTest(unittest.TestCase):
    def test_fragment_name_encodes_percent_before_slash(self):
        self.assertEqual(fragment_name("docs/a"), "docs%2Fa.md")
        self.assertEqual(fragment_name("docs-a"), "docs-a.md")
        self.assertEqual(fragment_name("a/b%c"), "a%2Fb%25c.md")

    def test_changelog_findings_name_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n- old\n")
            (root / "changes").mkdir()
            (root / "changes" / "note.md").write_text("Prose.\n")
            nested = root / "changes" / "nested"
            nested.mkdir()
            (nested / "x.md").write_text("- hidden\n")
            self.assertEqual(changelog_findings(root), [
                "CHANGELOG.md has a ## Unreleased heading. Write the bullet under changes/.",
                "changes/nested/x.md sits in a subdirectory. changes/ is flat.",
                "changes/note.md holds a line that is not a bullet.",
            ])
            (root / "CHANGELOG.md").write_text("# Changelog\n")
            (root / "changes" / "nested" / "x.md").unlink()
            nested.rmdir()
            (root / "changes" / "note.md").write_text("- First.\n- Second.\n")
            self.assertEqual(changelog_findings(root), [])
            (root / "changes" / "note.md").write_text(
                "- A user-facing change spans\n  two lines in the same bullet.\n"
            )
            self.assertEqual(changelog_findings(root), [])
            (root / "changes" / "note.md").write_text(
                "- A user-facing change spans\nthen prose on its own line.\n"
            )
            self.assertEqual(changelog_findings(root), [
                "changes/note.md holds a line that is not a bullet.",
            ])
            (root / "changes" / "note.md").unlink()
            self.assertEqual(changelog_findings(root), [])
            shutil.rmtree(root / "changes")
            self.assertEqual(changelog_findings(root), [])

    def test_repo_changelog_has_no_unreleased_heading(self):
        findings = changelog_findings(ROOT)
        self.assertEqual(findings, [], "\n".join(findings) or "Write bullets under changes/.")
        contributing = (ROOT / "CONTRIBUTING.md").read_text()
        self.assertIn(
            "git log --reverse --diff-filter=A --format= --name-only -- changes/",
            contributing,
        )
        self.assertNotIn("Add a line under Unreleased", contributing)
        self.assertNotIn("Each bullet is one line", contributing)
        self.assertIn("Indent a continuation line under that bullet.", contributing)
        self.assertIn("docs/guide.md", contributing)
        self.assertIn("one docs change", contributing)

    def test_release_cut_matches_the_fragments_that_still_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _git(repo, "init", "-q", "-b", "main")
            (repo / "changes").mkdir()
            (repo / "changes" / "first.md").write_text("- alpha\n")
            _git(repo, "add", "changes")
            _git(repo, "commit", "-qm", "first")
            (repo / "changes" / "second.md").write_text("- beta\n")
            _git(repo, "add", "changes")
            _git(repo, "commit", "-qm", "second")
            _git(repo, "rm", "-q", "changes/first.md")
            _git(repo, "commit", "-qm", "cut")
            (repo / "changes" / "first.md").write_text(
                "- alpha again\n  kept on the next line.\n"
            )
            _git(repo, "add", "changes")
            _git(repo, "commit", "-qm", "first again")
            bullets = release_bullets(repo)
            self.assertEqual(bullets, [
                "- beta",
                "- alpha again",
                "  kept on the next line.",
            ])
            section = "## 0.3.0 (2026-10-06)\n\n" + "\n".join(bullets) + "\n"
            held = "".join(
                (repo / name).read_text()
                for name in ("changes/second.md", "changes/first.md")
            )
            self.assertEqual(section, "## 0.3.0 (2026-10-06)\n\n" + held)
            self.assertNotIn("- alpha\n", section)
            self.assertIn("  kept on the next line.\n", section)
            (repo / "CHANGELOG.md").write_text("# Changelog\n\n" + section)
            _git(repo, "add", "CHANGELOG.md")
            _git(repo, "rm", "-q", "changes/first.md", "changes/second.md")
            _git(repo, "commit", "-qm", "0.3.0")
            self.assertEqual(
                _git(repo, "show", "HEAD^:changes/first.md"),
                "- alpha again\n  kept on the next line.\n",
            )
            self.assertEqual(
                _git(repo, "show", "HEAD:CHANGELOG.md"),
                "# Changelog\n\n## 0.3.0 (2026-10-06)\n\n"
                "- beta\n- alpha again\n  kept on the next line.\n",
            )
            names = [
                line for line in _git(
                    repo, "diff-tree", "--no-commit-id", "--name-status",
                    "--no-renames", "-r", "HEAD",
                ).splitlines()
                if line
            ]
            self.assertEqual(names, [
                "A\tCHANGELOG.md",
                "D\tchanges/first.md",
                "D\tchanges/second.md",
            ])
            self.assertEqual(release_bullets(repo), [])


AUDIT = ROOT / "t3/overrides/poteto-mode/scripts/worktree-audit.sh"
PREVIEW_TOOLS = (
    "preview_hover",
    "preview_drag",
    "preview_select",
    "preview_upload",
    "preview_dialog",
)


def _init_repo(path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "audit@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "audit"], check=True)
    (path / "f").write_text("a\n")
    subprocess.run(["git", "-C", str(path), "add", "f"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)


def _add_worktree(repo, path, branch):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", str(path), "-b", branch],
        check=True,
    )


class WorktreeAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        _init_repo(self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def _audit(self, *args, **env):
        run_env = os.environ.copy()
        for key in ("T3CODE_HOME", "T3_HOME", "T3_WORKTREES"):
            run_env.pop(key, None)
        run_env.update(env)
        completed = subprocess.run(
            [str(AUDIT), str(self.repo), *args],
            capture_output=True,
            text=True,
            env=run_env,
            timeout=60,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        marks = {}
        lines = [line for line in completed.stdout.splitlines() if line]
        self.assertGreaterEqual(len(lines), 2, completed.stdout)
        for line in lines[1:]:
            cols = line.split("\t")
            self.assertEqual(len(cols), 10, line)
            marks[cols[9]] = cols[7]
        return marks

    def test_configured_locations_count_and_bad_ones_do_not(self):
        t3_home = self.root / "code-home"
        other_home = self.root / "other-home"
        default = t3_home / "worktrees" / "proj" / "wt-default"
        custom = self.root / "home" / "custom" / "wt-custom"
        previous = self.root / "old" / "wt-old"
        stray = self.root / "stray" / "wt-stray"
        _add_worktree(self.repo, default, "b-default")
        _add_worktree(self.repo, custom, "b-custom")
        _add_worktree(self.repo, previous, "b-old")
        _add_worktree(self.repo, stray, "b-stray")
        (t3_home / "userdata").mkdir(parents=True)
        (t3_home / "userdata" / "settings.json").write_text(json.dumps({
            "worktreesDirectory": "~/custom",
            "previousWorktreesDirectories": [str(previous.parent), "/", "relative/nope"],
        }))
        marks = self._audit(
            T3CODE_HOME=str(t3_home),
            T3_HOME=str(other_home),
            HOME=str(self.root / "home"),
        )
        self.assertEqual(marks[str(default)], "yes")
        self.assertEqual(marks[str(custom)], "yes")
        self.assertEqual(marks[str(previous)], "yes")
        self.assertEqual(marks[str(stray)], "no")

    def test_flag_and_env_replace_the_configured_directory(self):
        t3_home = self.root / "t3home"
        default = t3_home / "worktrees" / "proj" / "wt-default"
        chosen = self.root / "chosen" / "wt"
        decoy = self.root / "decoy" / "wt"
        _add_worktree(self.repo, default, "b-default")
        _add_worktree(self.repo, chosen, "b-chosen")
        _add_worktree(self.repo, decoy, "b-decoy")
        (t3_home / "userdata").mkdir(parents=True)
        (t3_home / "userdata" / "settings.json").write_text(json.dumps({
            "worktreesDirectory": str(decoy.parent),
        }))
        marks = self._audit(
            "--t3-worktrees", str(chosen.parent),
            T3_HOME=str(t3_home),
            T3_WORKTREES=str(decoy.parent),
        )
        self.assertEqual(marks[str(default)], "yes")
        self.assertEqual(marks[str(chosen)], "yes")
        self.assertEqual(marks[str(decoy)], "no")

    def _parser_path(self, parser):
        bindir = self.root / f"bin-{parser}"
        bindir.mkdir()
        for cmd in (
            "bash", "git", "sed", "head", "date", "awk", "mktemp", "rm",
            "sort", "du", "grep", parser,
        ):
            found = shutil.which(cmd)
            if found is None:
                self.skipTest(f"{cmd} is not installed")
            (bindir / cmd).symlink_to(found)
        return str(bindir)

    def test_symlinked_setting_matches_the_physical_worktree(self):
        t3_home = self.root / "t3home"
        custom = self.root / "custom"
        alias = self.root / "alias"
        root_link = self.root / "root-link"
        direct = custom / "wt"
        via_alias = alias / "created-via-alias"
        outside = self.root / "outside" / "wt"
        default = t3_home / "worktrees" / "wt"
        _add_worktree(self.repo, direct, "b-direct")
        alias.symlink_to(custom, target_is_directory=True)
        _add_worktree(self.repo, via_alias, "b-alias")
        _add_worktree(self.repo, outside, "b-out")
        _add_worktree(self.repo, default, "b-default")
        root_link.symlink_to(Path("/"))
        (t3_home / "userdata").mkdir(parents=True)
        settings = t3_home / "userdata" / "settings.json"
        settings.write_text(json.dumps({"worktreesDirectory": str(alias)}))
        marks = self._audit(T3CODE_HOME=str(t3_home))
        self.assertEqual(marks[str(direct)], "yes")
        self.assertEqual(marks[str(custom / "created-via-alias")], "yes")
        self.assertEqual(marks[str(default)], "yes")
        self.assertEqual(marks[str(outside)], "no")

        settings.write_text(json.dumps({"worktreesDirectory": str(root_link)}))
        marks = self._audit(T3CODE_HOME=str(t3_home))
        self.assertEqual(marks[str(outside)], "no")
        self.assertEqual(marks[str(default)], "yes")
        self.assertEqual(marks[str(direct)], "no")

    def test_bad_previous_field_is_ignored_by_each_parser(self):
        t3_home = self.root / "t3home"
        custom = self.root / "custom" / "wt"
        outside = self.root / "outside" / "wt"
        previous = self.root / "previous" / "wt"
        default = t3_home / "worktrees" / "wt"
        for path, branch in (
            (custom, "b-custom"),
            (outside, "b-out"),
            (previous, "b-prev"),
            (default, "b-default"),
        ):
            _add_worktree(self.repo, path, branch)
        (t3_home / "userdata").mkdir(parents=True)
        settings = t3_home / "userdata" / "settings.json"
        bad_values = (
            {"oops": str(outside.parent)},
            "invalid",
            12,
            None,
            [1, {"oops": str(outside.parent)}, str(previous.parent)],
        )
        parsers = [name for name in ("jq", "python3") if shutil.which(name)]
        self.assertGreaterEqual(len(parsers), 1)
        for parser in parsers:
            tool_path = self._parser_path(parser)
            for value in bad_values:
                settings.write_text(json.dumps({
                    "worktreesDirectory": str(custom.parent),
                    "previousWorktreesDirectories": value,
                }))
                marks = self._audit(T3CODE_HOME=str(t3_home), PATH=tool_path)
                self.assertEqual(marks[str(custom)], "yes", (parser, value))
                self.assertEqual(marks[str(outside)], "no", (parser, value))
                self.assertEqual(marks[str(default)], "yes", (parser, value))
                expect_previous = "yes" if isinstance(value, list) else "no"
                self.assertEqual(marks[str(previous)], expect_previous, (parser, value))

    def test_padded_home_and_setting_count(self):
        t3_home = self.root / "t3home"
        home = self.root / "home"
        custom = self.root / "custom" / "wt"
        tilde_custom = home / "custom" / "wt"
        outside = self.root / "outside" / "wt"
        default = t3_home / "worktrees" / "wt"
        _add_worktree(self.repo, custom, "b-custom")
        _add_worktree(self.repo, tilde_custom, "b-tilde")
        _add_worktree(self.repo, outside, "b-out")
        _add_worktree(self.repo, default, "b-default")
        (t3_home / "userdata").mkdir(parents=True)
        settings = t3_home / "userdata" / "settings.json"
        settings.write_text(json.dumps({
            "worktreesDirectory": f"  {custom.parent}  ",
        }))
        marks = self._audit(T3CODE_HOME=f"  {t3_home}  ", HOME=str(home))
        self.assertEqual(marks[str(default)], "yes")
        self.assertEqual(marks[str(custom)], "yes")
        self.assertEqual(marks[str(outside)], "no")

        settings.write_text(json.dumps({"worktreesDirectory": "  ~/custom  "}))
        marks = self._audit(T3CODE_HOME=f"  {t3_home}  ", HOME=str(home))
        self.assertEqual(marks[str(tilde_custom)], "yes")
        self.assertEqual(marks[str(custom)], "no")

        marks = self._audit(T3CODE_HOME="   ", T3_HOME=str(t3_home), HOME=str(home))
        self.assertEqual(marks[str(default)], "yes")

    def test_javascript_whitespace_pad_counts_for_each_parser(self):
        t3_home = self.root / "t3home"
        custom = self.root / "custom" / "wt"
        outside = self.root / "outside" / "wt"
        default = t3_home / "worktrees" / "wt"
        _add_worktree(self.repo, custom, "b-custom")
        _add_worktree(self.repo, outside, "b-out")
        _add_worktree(self.repo, default, "b-default")
        (t3_home / "userdata").mkdir(parents=True)
        settings = t3_home / "userdata" / "settings.json"
        parsers = [name for name in ("jq", "python3") if shutil.which(name)]
        self.assertEqual(parsers, ["jq", "python3"])
        paths = {parser: self._parser_path(parser) for parser in parsers}
        for pad in ("\u00a0", "\ufeff"):
            for parser, tool_path in paths.items():
                label = (parser, hex(ord(pad)))
                settings.write_text(json.dumps({
                    "worktreesDirectory": f"{pad}{custom.parent}{pad}",
                }))
                marks = self._audit(T3CODE_HOME=str(t3_home), PATH=tool_path)
                self.assertEqual(marks[str(custom)], "yes", (*label, "setting"))
                self.assertEqual(marks[str(default)], "yes", (*label, "setting"))
                self.assertEqual(marks[str(outside)], "no", (*label, "setting"))

                settings.write_text(json.dumps({
                    "worktreesDirectory": str(custom.parent),
                }))
                marks = self._audit(
                    T3CODE_HOME=f"{pad}{t3_home}{pad}",
                    PATH=tool_path,
                )
                self.assertEqual(marks[str(custom)], "yes", (*label, "home"))
                self.assertEqual(marks[str(default)], "yes", (*label, "home"))
                self.assertEqual(marks[str(outside)], "no", (*label, "home"))

    def test_playbook_names_the_configured_worktree_location(self):
        text = (ROOT / "t3/overrides/poteto-mode/playbooks/worktree-cleanup.md").read_text()
        self.assertIn("worktreesDirectory", text)
        self.assertIn("--t3-worktrees", text)
        self.assertIn("T3_WORKTREES", text)
        self.assertIn("replaces those settings roots", text)


class PreviewToolDocTest(unittest.TestCase):
    def test_runtime_names_hover_drag_select_upload_and_dialog(self):
        text = (ROOT / "t3/runtime.md").read_text()
        row = next(line for line in text.splitlines() if "control-ui, browser MCP" in line)
        bullet = next(line for line in text.splitlines() if line.startswith("- Web or Electron UI:"))
        for name in PREVIEW_TOOLS:
            self.assertIn(name, row)
            self.assertIn(name, bullet)


class StalledChildDocTest(unittest.TestCase):
    def test_delegation_step_5_bounds_an_open_child(self):
        text = (ROOT / "t3/runtime.md").read_text()
        step = text.split("5. Collect results.", 1)[1].split("6. You own every child's output.", 1)[0]
        self.assertIn("t3_thread_wait", step)
        self.assertIn("timeoutMs: 300000", step)
        self.assertIn("does not end its turn while a child is open", step)

    def test_permissions_never_lowers_runtime_mode(self):
        text = (ROOT / "t3/runtime.md").read_text()
        section = text.split("### Permissions", 1)[1].split("### Failure handling", 1)[0]
        self.assertIn("never lower it", section)

    def test_failure_handling_names_a_stalled_child(self):
        text = (ROOT / "t3/runtime.md").read_text()
        section = text.split("### Failure handling", 1)[1].split("### Fresh children by default", 1)[0]
        self.assertIn("A stalled child", section)

    def test_no_source_tells_a_parent_to_end_its_turn_on_an_open_child(self):
        unbounded = re.compile(r"completion notifications? wakes?|let (?:each|the) completion|end the turn rather than wait"
                               r"|`mode: \"async\"`,? and end the turn")
        found = [f"{path.relative_to(ROOT)}:{number}"
                 for path in sorted((ROOT / "t3").rglob("*.md"))
                 for number, line in enumerate(path.read_text().splitlines(), 1) if unbounded.search(line)]
        self.assertEqual(found, [])

    def test_routed_skills_collect_children_per_delegation_step_5(self):
        sources = {
            "how/SKILL.md": "../pstack-runtime", "arena/SKILL.md": "../pstack-runtime",
            "interrogate/SKILL.md": "../pstack-runtime", "swarm/SKILL.md": "../pstack-runtime",
            "why/SKILL.md": "../pstack-runtime", "no-comments/SKILL.md": "../pstack-runtime",
            "poteto-mode/playbooks/orchestrate.md": "../../pstack-runtime",
        }
        missing = [name for name, runtime in sources.items()
                   if f"Delegation step 5]({runtime}/SKILL.md#delegation)" not in (ROOT / "t3/overrides" / name).read_text()]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
