if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import setUpModule
from .catalogs import MUSE, backup_catalog
from .test_backup import BackupCliCase


class BackupCliTest(BackupCliCase):
    def test_provider_and_model_authors_exclude_every_family(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "claudeAgent/claude-opus-5-5",
            "--author", "grok/grok-4.7",
            "--options", '{"reasoningEffort": "xhigh"}',
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; no backup seat, so the work waits for the reset",
        })


    def test_a_provider_and_model_author_skips_that_family_on_claude_agent(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "grok/grok-4.7",
            "--options", '{"reasoningEffort": "xhigh"}',
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}},
            "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at xhigh",
        })


    def test_cursor_claude_relaunches_on_claude_agent(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "cursor",
            "--model", "claude-opus-5-5",
            "--options", '{"effort": "xhigh"}',
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "cursor/claude-opus-5-5",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: cursor/claude-opus-5-5 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })


    def test_worker_backup_uses_the_configured_claude_agent_seat(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })


    def test_cursor_light_role_relaunches_on_claude_agent_sonnet(self):
        completed = self.backup(
            "--role", "how explorer",
            "--provider", "cursor",
            "--model", "claude-haiku-5-5",
            "--options", '{"effort": "medium"}',
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "how explorer",
            "failed": "cursor/claude-haiku-5-5",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5", "options": {"effort": "medium"}},
            "report": "how explorer: cursor/claude-haiku-5-5 hit its usage limit; relaunched on claudeAgent/claude-sonnet-5-5 at medium",
        })


    def test_cursor_claude_parks_when_claude_agent_is_out(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "cursor",
            "--model", "claude-opus-5-5",
            "--options", '{"effort": "xhigh"}',
            "--out", "claudeAgent",
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "bug-fix",
            "failed": "cursor/claude-opus-5-5",
            "report": "bug-fix: cursor/claude-opus-5-5 hit its usage limit; no backup seat, so the work waits for the reset",
        })


    def test_resume_returns_the_original_claude_agent_seat(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "claudeAgent",
            "--model", "claude-opus-5-5",
            "--options", '{"effort": "xhigh"}',
            "--resume",
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "claudeAgent/claude-opus-5-5",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: claudeAgent/claude-opus-5-5 resumed on claudeAgent/claude-opus-5-5 at medium after the reset",
        })


    def test_resume_parks_while_claude_agent_stays_out(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "claudeAgent",
            "--model", "claude-opus-5-5",
            "--options", '{"effort": "xhigh"}',
            "--out", "grok",
            "--out", "claudeAgent",
            "--resume",
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "bug-fix",
            "failed": "claudeAgent/claude-opus-5-5",
            "report": "bug-fix: claudeAgent/claude-opus-5-5 is still out after the reset; no backup seat, so the work waits for the reset",
        })


    def test_resume_returns_the_original_grok_seat_when_grok_is_back(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            "--resume",
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {
                "providerInstanceId": "grok",
                "model": "grok-4.7",
                "options": {"reasoningEffort": "medium"},
            },
            "report": "bug-fix: grok/grok-4.7 resumed on grok/grok-4.7 at medium after the reset",
        })


    def test_resume_uses_claude_agent_when_the_original_provider_stays_out(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            "--out", "grok",
            "--resume",
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: grok/grok-4.7 resumed on claudeAgent/claude-opus-5-5 at medium after the reset",
        })


    def test_resume_of_a_reviewer_does_not_return_an_author_family(self):
        parked = self.backup(
            "--role", "verifiers",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--author", "grok/grok-4.7",
            "--author", "claudeAgent/claude-opus-5-5",
            "--options", '{"reasoningEffort": "xhigh"}',
            "--resume",
        )
        self.assert_backup(parked, {
            "decision": "park",
            "role": "verifiers",
            "failed": "grok/grok-4.7",
            "report": "verifiers: grok/grok-4.7 is still out after the reset; no backup seat, so the work waits for the reset",
        })
        moved = self.backup(
            "--role", "verifiers",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--author", "grok/grok-4.7",
            "--options", '{"reasoningEffort": "xhigh"}',
            "--resume",
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(moved, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}},
            "report": "verifiers: grok/grok-4.7 resumed on claudeAgent/claude-opus-5-5 at xhigh after the reset",
        })


    def test_resume_of_a_namespaced_reviewer_does_not_return_its_author(self):
        muse = self.backup(
            "--role", "verifiers",
            "--provider", "opencode",
            "--model", MUSE,
            "--author", "opencode/" + MUSE,
            "--out", "grok",
            "--out", "claudeAgent",
            "--resume",
        )
        self.assert_backup(muse, {
            "decision": "park",
            "role": "verifiers",
            "failed": "opencode/opencode/muse-lite-2-free",
            "report": (
                "verifiers: opencode/opencode/muse-lite-2-free is still out after the reset; "
                "no backup seat, so the work waits for the reset"
            ),
        })
        catalog = backup_catalog()
        opencode = next(p for p in catalog["providers"] if p["providerInstanceId"] == "opencode")
        opencode["models"].append({"id": "anthropic/claude-sonnet-5-5", "options": []})
        claude = self.backup(
            "--role", "verifiers",
            "--provider", "opencode",
            "--model", "anthropic/claude-sonnet-5-5",
            "--author", "claudeAgent/claude-opus-5-5",
            "--resume",
            catalog=catalog,
        )
        self.assert_backup(claude, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "opencode/anthropic/claude-sonnet-5-5",
            "seat": {"providerInstanceId": "grok", "model": "grok-4.7"},
            "report": "verifiers: opencode/anthropic/claude-sonnet-5-5 resumed on grok/grok-4.7 after the reset",
        })


    def test_resume_returns_a_reviewer_whose_family_wrote_nothing(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "grok/grok-4.7",
            "--author", "claudeAgent/claude-opus-5-5",
            "--options", '{"reasoningEffort": "xhigh"}',
            "--resume",
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "xhigh"}},
            "report": "verifiers: codex/gpt-6.1-sol resumed on codex/gpt-6.1-sol at xhigh after the reset",
        })


    def test_resume_of_a_light_role_uses_sonnet_when_the_provider_stays_out(self):
        completed = self.backup(
            "--role", "how explorer",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "medium"}',
            "--out", "grok",
            "--resume",
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "how explorer",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5", "options": {"effort": "medium"}},
            "report": "how explorer: grok/grok-4.7 resumed on claudeAgent/claude-sonnet-5-5 at medium after the reset",
        })


    def test_grok_usage_balance_exhausted_is_a_usage_limit(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            text="API error (status 402 Payment Required): Grok Build usage balance exhausted\n",
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })


    def test_a_cursor_grok_reviewer_moves_to_the_grok_provider(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "cursor",
            "--model", "grok-4.7",
            "--author", "claudeAgent/claude-opus-5-5",
            "--options", '{"reasoningEffort": "high"}',
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "cursor/grok-4.7",
            "seat": {
                "providerInstanceId": "grok",
                "model": "grok-4.7",
                "options": {"reasoningEffort": "high"},
            },
            "report": "verifiers: cursor/grok-4.7 hit its usage limit; relaunched on grok/grok-4.7 at high",
        })


    def test_resume_of_a_reviewer_still_out_uses_the_configured_backup(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "claudeAgent/claude-opus-5-5",
            "--options", '{"reasoningEffort": "xhigh"}',
            "--out", "codex",
            "--resume",
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
            "report": "verifiers: codex/gpt-6.1-sol resumed on grok/grok-4.7 at xhigh after the reset",
        })
