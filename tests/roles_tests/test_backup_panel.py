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
import os
import tempfile
from tests import in_process
from .support import ROOT, Repo, setUpModule
from .catalogs import BUNNY, BUNNY_SEAT, LIMIT, MUSE, MUSE_SEAT, OPUS_BACKUP, PANEL_RULE, REVIEW_BACKUPS, REVIEW_LADDER_BACKUP, STEP, STEP_SEAT, VERIFIER_OUT, VERIFIER_PARK, backup_catalog
from .test_backup import BackupCliCase


BEDROCK_CLAUDE = (
    "anthropic.claude-sonnet-5-5-v1:0",
    "us.anthropic.claude-sonnet-5-5-v1:0",
    "eu.anthropic.claude-sonnet-5-5-v1:0",
    "apac.anthropic.claude-sonnet-5-5-v1:0",
    "global.anthropic.claude-sonnet-5-5-v1:0",
    "amazon-bedrock/us.anthropic.claude-sonnet-5-5-v1:0",
)


def bedrock_catalog():
    catalog = backup_catalog()
    opencode = next(provider for provider in catalog["providers"] if provider["providerInstanceId"] == "opencode")
    opencode["models"].extend({"id": model, "options": []} for model in ("claude-sonnet-5-5", *BEDROCK_CLAUDE))
    return catalog


class BackupCliTest(BackupCliCase):
    def test_rate_limit_overload_and_429_are_not_a_usage_limit(self):
        expected = {
            "decision": "not-usage-limit",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "report": "bug-fix: grok/grok-4.7 failed without a usage limit; respawn per Failure handling",
        }
        for text in ("rate_limit_error", "overloaded", "429", "usage limit", "model not found"):
            with self.subTest(text=text):
                completed = self.backup(
                    "--role", "bug-fix",
                    "--provider", "grok",
                    "--model", "grok-4.7",
                    "--options", '{"reasoningEffort": "high"}',
                    text=text,
                )
                self.assert_backup(completed, expected)


    def test_listed_limit_phrases_relaunch(self):
        expected = {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        }
        phrases = (
            "You've hit your usage limit. Try again later.",
            "You've hit your limit",
            "usage limit reached",
            "reached your usage limit",
            "usage limit exceeded",
            "out of usage",
            "quota exceeded",
            "exceeded your quota",
            "insufficient_quota",
        )
        for text in phrases:
            with self.subTest(text=text):
                completed = self.backup(
                    "--role", "bug-fix",
                    "--provider", "grok",
                    "--model", "grok-4.7",
                    "--options", '{"reasoningEffort": "high"}',
                    text=text,
                )
                self.assert_backup(completed, expected)


    def test_inherit_missing_author_and_catalog_stdin_exit_2(self):
        inherit = self.backup(
            "--role", "bug-fix",
            "--provider", "inherit",
            "--model", "claude-opus-5-5",
        )
        self.assertEqual(inherit.returncode, 2)
        self.assertEqual(inherit.stdout, "")
        self.assertIn("inherit", inherit.stderr)

        missing_author = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
        )
        self.assertEqual(missing_author.returncode, 2)
        self.assertEqual(missing_author.stdout, "")
        self.assertIn("--author", missing_author.stderr)

        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            env = {**os.environ, "XDG_CONFIG_HOME": str(repo.directory)}
            stdin_catalog = in_process.invoke(in_process.Invocation(
                program=ROOT / "t3/scripts/roles.py",
                argv=(
                    "backup",
                    "--cwd", str(repo.directory),
                    "--catalog", "-",
                    "--parent", "claudeAgent/claude-opus-5-5",
                    "--role", "bug-fix",
                    "--provider", "grok",
                    "--model", "grok-4.7",
                ),
                env=env,
                stdin=LIMIT,
            ))
        self.assertEqual(stdin_catalog.returncode, 2)
        self.assertEqual(stdin_catalog.stdout, "")
        self.assertIn("--catalog -", stdin_catalog.stderr)


    def test_unset_review_backups_keeps_the_verifier_park(self):
        self.assert_backup(self.backup(*VERIFIER_OUT), VERIFIER_PARK)


    def test_review_backups_turn_a_verifier_park_into_a_panel(self):
        completed = self.backup(*VERIFIER_OUT, roles_file=REVIEW_BACKUPS)
        self.assert_backup(completed, {
            "decision": "panel",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seats": [MUSE_SEAT, STEP_SEAT, BUNNY_SEAT],
            "rule": PANEL_RULE,
            "report": (
                "verifiers: codex/gpt-6.1-sol hit its usage limit; every paid reviewer backup is out, "
                "so review backups runs 3 seats; land only if no reviewer reproduces a blocker and at least two pass"
            ),
        })


    def test_a_configured_verifiers_backup_wins_over_the_panel(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "claudeAgent/claude-opus-5-5",
            "--options", '{"reasoningEffort": "xhigh"}',
            roles_file={
                "roles": {"review backups": [MUSE_SEAT, STEP_SEAT, BUNNY_SEAT]},
                "backups": {"verifiers": REVIEW_LADDER_BACKUP},
            },
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


    def test_resume_with_every_configured_backup_out_runs_the_panel(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "claudeAgent/claude-opus-5-5",
            "--out", "codex",
            "--out", "grok",
            "--out", "claudeAgent",
            "--resume",
            text="",
            roles_file={
                "roles": {"review backups": [MUSE_SEAT, STEP_SEAT, BUNNY_SEAT]},
                "backups": {"verifiers": REVIEW_LADDER_BACKUP},
            },
        )
        self.assert_backup(completed, {
            "decision": "panel",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seats": [MUSE_SEAT, STEP_SEAT, BUNNY_SEAT],
            "rule": PANEL_RULE,
            "report": (
                "verifiers: codex/gpt-6.1-sol is still out after the reset; every paid reviewer backup is out, "
                "so review backups runs 3 seats; land only if no reviewer reproduces a blocker and at least two pass"
            ),
        })


    def test_only_verifiers_get_a_panel(self):
        both_keys = {
            "roles": {"review backups": [MUSE_SEAT, STEP_SEAT, BUNNY_SEAT]},
            "backups": {"interrogate reviewers": REVIEW_LADDER_BACKUP, "bug-fix": OPUS_BACKUP},
        }
        interrogate = self.backup(
            "--role", "interrogate reviewers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "claudeAgent/claude-opus-5-5",
            "--out", "grok",
            roles_file=both_keys,
        )
        self.assert_backup(interrogate, {
            "decision": "park",
            "role": "interrogate reviewers",
            "failed": "codex/gpt-6.1-sol",
            "report": "interrogate reviewers: codex/gpt-6.1-sol hit its usage limit; no backup seat, so the work waits for the reset",
        })
        worker = self.backup(
            "--role", "bug-fix",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--out", "grok",
            roles_file=both_keys,
        )
        self.assert_backup(worker, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })


    def test_panel_drops_blocked_author_missing_and_repeated_seats(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--author", "opencode/" + MUSE,
            "--out", "claudeAgent",
            roles_file={"roles": {"review backups": [
                {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
                MUSE_SEAT,
                {"providerInstanceId": "opencode", "model": "opencode/nope-free"},
                STEP_SEAT,
                BUNNY_SEAT,
                {"providerInstanceId": "opencode", "model": BUNNY, "options": {"variant": "high"}},
                {"providerInstanceId": "grok", "model": "grok-4.7"},
            ]}},
        )
        self.assert_backup(completed, {
            "decision": "panel",
            "role": "verifiers",
            "failed": "grok/grok-4.7",
            "seats": [STEP_SEAT, BUNNY_SEAT],
            "rule": PANEL_RULE,
            "notes": [
                "dropped claudeAgent/claude-opus-5-5: claudeAgent is out",
                "dropped opencode/opencode/muse-lite-2-free: muse wrote the diff",
                "dropped opencode/opencode/nope-free: not runnable or not in the catalog",
                "dropped opencode/opencode/bunny-1-free: family bunny already seated",
                "dropped grok/grok-4.7: grok is out",
            ],
            "report": (
                "verifiers: grok/grok-4.7 hit its usage limit; every paid reviewer backup is out, "
                "so review backups runs 2 seats; land only if no reviewer reproduces a blocker and at least two pass"
            ),
        })


    def test_one_usable_seat_parks(self):
        completed = self.backup(
            *VERIFIER_OUT,
            roles_file={"roles": {"review backups": [
                {"providerInstanceId": "opencode", "model": "opencode/nope-free"},
                STEP_SEAT,
            ]}},
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "report": (
                "verifiers: codex/gpt-6.1-sol hit its usage limit; review backups has 1 usable seat and needs 2, "
                "so the work waits for the reset "
                "(dropped opencode/opencode/nope-free: not runnable or not in the catalog)"
            ),
        })


    def test_a_panel_member_limit_parks_without_a_backup(self):
        member = ("--role", "review backups", "--provider", "opencode", "--model", STEP)
        self.assert_backup(self.backup(*member, roles_file=REVIEW_BACKUPS), {
            "decision": "park",
            "role": "review backups",
            "failed": "opencode/opencode/step-9-preview-free",
            "report": (
                "review backups: opencode/opencode/step-9-preview-free hit its usage limit; "
                "a review backups seat has no backup, so it counts as no pass"
            ),
        })
        self.assert_backup(self.backup(*member, text="connection reset\n", roles_file=REVIEW_BACKUPS), {
            "decision": "not-usage-limit",
            "role": "review backups",
            "failed": "opencode/opencode/step-9-preview-free",
            "report": (
                "review backups: opencode/opencode/step-9-preview-free failed without a usage limit; "
                "respawn per Failure handling"
            ),
        })


    def test_a_bedrock_claude_author_skips_the_claude_agent_backup(self):
        for model in BEDROCK_CLAUDE:
            with self.subTest(model=model):
                completed = self.backup(
                    "--role", "verifiers",
                    "--provider", "codex",
                    "--model", "gpt-6.1-sol",
                    "--author", "opencode/" + model,
                    "--out", "grok",
                    catalog=bedrock_catalog(),
                )
                self.assert_backup(completed, VERIFIER_PARK)


    def test_resume_of_a_bedrock_claude_reviewer_does_not_return_a_claude_author(self):
        for model in BEDROCK_CLAUDE:
            with self.subTest(model=model):
                completed = self.backup(
                    "--role", "verifiers",
                    "--provider", "opencode",
                    "--model", model,
                    "--author", "claudeAgent/claude-opus-5-5",
                    "--out", "grok",
                    "--out", "claudeAgent",
                    "--resume",
                    text="",
                    catalog=bedrock_catalog(),
                )
                self.assert_backup(completed, {
                    "decision": "park",
                    "role": "verifiers",
                    "failed": "opencode/" + model,
                    "report": (
                        f"verifiers: opencode/{model} is still out after the reset; "
                        "no backup seat, so the work waits for the reset"
                    ),
                })


    def test_panel_drops_a_bedrock_claude_seat_for_a_claude_author(self):
        for model in BEDROCK_CLAUDE:
            with self.subTest(model=model):
                completed = self.backup(
                    *VERIFIER_OUT,
                    catalog=bedrock_catalog(),
                    roles_file={"roles": {"review backups": [
                        {"providerInstanceId": "opencode", "model": model},
                        STEP_SEAT,
                        BUNNY_SEAT,
                    ]}},
                )
                self.assert_backup(completed, {
                    "decision": "panel",
                    "role": "verifiers",
                    "failed": "codex/gpt-6.1-sol",
                    "seats": [STEP_SEAT, BUNNY_SEAT],
                    "rule": PANEL_RULE,
                    "notes": [f"dropped opencode/{model}: claude wrote the diff"],
                    "report": (
                        "verifiers: codex/gpt-6.1-sol hit its usage limit; every paid reviewer backup is out, "
                        "so review backups runs 2 seats; land only if no reviewer reproduces a blocker and at least two pass"
                    ),
                })


    def test_a_bedrock_form_of_a_seated_family_parks_a_two_seat_panel(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "grok/grok-4.7",
            "--out", "grok",
            "--out", "claudeAgent",
            catalog=bedrock_catalog(),
            roles_file={"roles": {"review backups": [
                {"providerInstanceId": "opencode", "model": "claude-sonnet-5-5"},
                {"providerInstanceId": "opencode", "model": "us.anthropic.claude-sonnet-5-5-v1:0"},
            ]}},
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "report": (
                "verifiers: codex/gpt-6.1-sol hit its usage limit; review backups has 1 usable seat and needs 2, "
                "so the work waits for the reset "
                "(dropped opencode/us.anthropic.claude-sonnet-5-5-v1:0: family claude already seated)"
            ),
        })


    def test_resume_of_a_panel_member_in_an_author_family_parks(self):
        completed = self.backup(
            "--role", "review backups",
            "--provider", "opencode",
            "--model", MUSE,
            "--author", "opencode/" + MUSE,
            "--resume",
            text="",
            roles_file=REVIEW_BACKUPS,
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "review backups",
            "failed": "opencode/opencode/muse-lite-2-free",
            "report": (
                "review backups: opencode/opencode/muse-lite-2-free is in an author's family, "
                "so it does not resume and counts as no pass"
            ),
        })


    def test_resume_of_a_panel_member_returns_its_seat_for_another_author(self):
        completed = self.backup(
            "--role", "review backups",
            "--provider", "opencode",
            "--model", MUSE,
            "--author", "claudeAgent/claude-opus-5-5",
            "--resume",
            text="",
            roles_file=REVIEW_BACKUPS,
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "review backups",
            "failed": "opencode/opencode/muse-lite-2-free",
            "seat": {"providerInstanceId": "opencode", "model": MUSE},
            "report": (
                "review backups: opencode/opencode/muse-lite-2-free resumed on "
                "opencode/opencode/muse-lite-2-free after the reset"
            ),
        })


    def test_resume_of_a_panel_member_needs_an_author(self):
        completed = self.backup(
            "--role", "review backups",
            "--provider", "opencode",
            "--model", MUSE,
            "--resume",
            text="",
            roles_file=REVIEW_BACKUPS,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            "error: role 'review backups' needs --author with --resume, the model whose work it judges\n",
        )


    def test_light_mode_keeps_a_variant_seat(self):
        completed = self.backup(*VERIFIER_OUT, "--brief-mode", "light", roles_file=REVIEW_BACKUPS)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "panel")
        self.assertEqual(payload["seats"], [MUSE_SEAT, STEP_SEAT, BUNNY_SEAT])
