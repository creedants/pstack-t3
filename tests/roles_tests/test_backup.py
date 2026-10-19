import json
import unittest
from .support import setUpModule
from .catalogs import LIMIT, MIGRATION_BACKUPS, VERIFIER_OUT, _GROK_LEVELS, _select, backup_catalog, run_backup


class BackupCliCase(unittest.TestCase):
    def backup(self, *args, text=LIMIT, catalog=None, roles_file=None):
        document = {"backups": MIGRATION_BACKUPS} if roles_file is None else roles_file
        return run_backup(*args, text=text, catalog=catalog, roles_file=document)


    def assert_backup(self, completed, expected):
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), expected)


    def grok_relaunch(self, level):
        return {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seat": {
                "providerInstanceId": "grok",
                "model": "grok-4.7",
                "options": {"reasoningEffort": level},
            },
            "report": f"verifiers: codex/gpt-6.1-sol hit its usage limit; relaunched on grok/grok-4.7 at {level}",
        }


class BackupCliTest(BackupCliCase):
    HIGH_VERIFIER = (
        "--role", "verifiers",
        "--provider", "codex",
        "--model", "gpt-6.1-sol",
        "--options", '{"reasoningEffort": "high"}',
        "--brief-mode", "light",
    )


    def test_codex_worker_at_xhigh_relaunches_on_opus(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--options", '[{"id": "reasoningEffort", "value": "xhigh"}]',
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })


    def test_grok_worker_relaunches_on_opus_at_the_light_cap(self):
        completed = self.backup(
            "--role", "feature, refactoring",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '[{"id": "reasoningEffort", "value": "high"}]',
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "feature, refactoring",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "feature, refactoring: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })


    def test_how_explorer_relaunches_on_sonnet(self):
        completed = self.backup(
            "--role", "how explorer",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "medium"}',
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "how explorer",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5", "options": {"effort": "medium"}},
            "report": "how explorer: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-sonnet-5-5 at medium",
        })


    def test_light_mode_caps_the_level_at_medium(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--options", '[{"id": "reasoningEffort", "value": "xhigh"}]',
            "--brief-mode", "light",
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })


    def test_light_mode_relaunches_a_high_verifier_at_high(self):
        completed = self.backup(*self.HIGH_VERIFIER, "--author", "claude-opus-5-5")
        self.assert_backup(completed, self.grok_relaunch("high"))


    def test_light_mode_with_small_budget_relaunches_a_high_verifier_at_medium(self):
        completed = self.backup(*self.HIGH_VERIFIER, "--author", "claude-opus-5-5", roles_file={"budget": "small", "backups": MIGRATION_BACKUPS})
        self.assert_backup(completed, self.grok_relaunch("medium"))


    def test_light_mode_resumes_a_high_verifier_at_high(self):
        completed = self.backup(*self.HIGH_VERIFIER, "--author", "claude-opus-5-5", "--resume")
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "high"}},
            "report": "verifiers: codex/gpt-6.1-sol resumed on codex/gpt-6.1-sol at high after the reset",
        })


    def test_light_mode_keeps_each_effort_seat_of_the_verifiers_backup_panel_at_its_level(self):
        catalog = backup_catalog()
        catalog["providers"].extend(
            {
                "providerInstanceId": provider,
                "canRunChildTask": True,
                "constraints": [],
                "models": [{"id": model, "options": [_select("reasoningEffort", _GROK_LEVELS)]}],
            }
            for provider, model in (("kilo", "kilo-1"), ("openrouter", "muse-lite-2"))
        )
        completed = self.backup(
            *VERIFIER_OUT,
            "--brief-mode", "light",
            catalog=catalog,
            roles_file={"roles": {"review backups": [
                {"providerInstanceId": "kilo", "model": "kilo-1", "options": {"reasoningEffort": "xhigh"}},
                {"providerInstanceId": "openrouter", "model": "muse-lite-2", "options": {"reasoningEffort": "high"}},
            ]}},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "panel")
        self.assertEqual(payload["seats"], [
            {"providerInstanceId": "kilo", "model": "kilo-1", "options": {"reasoningEffort": "xhigh"}},
            {"providerInstanceId": "openrouter", "model": "muse-lite-2", "options": {"reasoningEffort": "high"}},
        ])


    def test_codex_ultra_lands_on_medium_under_the_light_default(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--options", '{"reasoningEffort": "ultra"}',
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })


    def test_a_variant_level_maps_onto_the_backup_effort(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"variant": "high"}',
            "--brief-mode", "full",
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "high"}},
            "report": "bug-fix: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at high",
        })


    def test_a_seat_with_no_level_gets_medium_under_the_light_default(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })


    def test_a_claude_worker_parks_and_names_neither_codex_nor_cursor(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "claudeAgent",
            "--model", "claude-opus-5-5",
            "--options", '{"effort": "xhigh"}',
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "bug-fix",
            "failed": "claudeAgent/claude-opus-5-5",
            "report": "bug-fix: claudeAgent/claude-opus-5-5 hit its usage limit; no backup seat, so the work waits for the reset",
        })
        self.assertNotIn("codex", completed.stdout)
        self.assertNotIn("cursor", completed.stdout)


    def test_codex_verifier_with_a_claude_author_relaunches_on_grok(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "claude-opus-5-5",
            "--options", '[{"id": "reasoningEffort", "value": "xhigh"}]',
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seat": {
                "providerInstanceId": "grok",
                "model": "grok-4.7",
                "options": {"reasoningEffort": "xhigh"},
            },
            "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; relaunched on grok/grok-4.7 at xhigh",
        })


    def test_codex_verifier_with_a_grok_author_relaunches_on_opus(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "grok-4.7",
            "--options", '{"reasoningEffort": "xhigh"}',
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}},
            "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at xhigh",
        })


    def test_codex_verifier_parks_when_grok_authored_and_claude_is_out(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "grok-4.7",
            "--out", "claudeAgent",
            "--options", '{"reasoningEffort": "xhigh"}',
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; no backup seat, so the work waits for the reset",
        })


    def test_options_array_and_object_parse_to_the_same_seat(self):
        expected = {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        }
        array = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '[{"id": "reasoningEffort", "value": "high"}]',
        )
        obj = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
        )
        self.assert_backup(array, expected)
        self.assert_backup(obj, expected)


    def test_two_author_families_park_a_reviewer(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "claude-opus-5-5",
            "--author", "grok-4.7",
            "--options", '{"reasoningEffort": "xhigh"}',
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; no backup seat, so the work waits for the reset",
        })


    def test_muse_subscription_quota_exhausted_relaunches(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            text="API error 429: Subscription quota exhausted.",
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })


    def test_context_full_and_prompt_too_long_are_not_a_usage_limit(self):
        expected = {
            "decision": "not-usage-limit",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "report": "bug-fix: grok/grok-4.7 failed without a usage limit; respawn per Failure handling",
        }
        for text in (
            "blocking_limit: context window full, compact the conversation or start another thread",
            "prompt is too long",
        ):
            with self.subTest(text=text):
                completed = self.backup(
                    "--role", "bug-fix",
                    "--provider", "grok",
                    "--model", "grok-4.7",
                    "--options", '{"reasoningEffort": "high"}',
                    text=text,
                )
                self.assert_backup(completed, expected)
