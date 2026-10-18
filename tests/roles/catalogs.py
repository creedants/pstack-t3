"""Catalog builders and seat fixtures shared by the roles tests."""


import json
import os
import tempfile
from tests import in_process
from .support import CATALOG, ROOT, Repo


def _select(option_id, values):
    return {"id": option_id, "type": "select", "options": [{"id": value} for value in values]}


_CLAUDE_LEVELS = ("low", "medium", "high", "xhigh", "max", "ultracode", "ultrathink")


_CODEX_LEVELS = ("low", "medium", "high", "xhigh", "max", "ultra")


_GROK_LEVELS = ("low", "medium", "high", "xhigh")


LIMIT = "You've hit your usage limit. Try again later.\n"


MUSE = "opencode/muse-lite-2-free"


STEP = "opencode/step-9-preview-free"


BUNNY = "opencode/bunny-1-free"


MUSE_SEAT = {"providerInstanceId": "opencode", "model": MUSE, "options": {"variant": "high"}}


STEP_SEAT = {"providerInstanceId": "opencode", "model": STEP, "options": {"variant": "high"}}


BUNNY_SEAT = {"providerInstanceId": "opencode", "model": BUNNY, "options": {"variant": "max"}}


REVIEW_BACKUPS = {"roles": {"review backups": [MUSE_SEAT, STEP_SEAT, BUNNY_SEAT]}}


OPUS_BACKUP = [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}]


SONNET_BACKUP = [{"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5"}]


REVIEW_LADDER_BACKUP = [
    {"providerInstanceId": "grok", "model": "grok-4.7"},
    {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
]


MIGRATION_BACKUPS = {
    "feature, refactoring": OPUS_BACKUP,
    "bug-fix": OPUS_BACKUP,
    "perf-issue": OPUS_BACKUP,
    "hillclimb": OPUS_BACKUP,
    "judgment and prose": OPUS_BACKUP,
    "hardest tasks": OPUS_BACKUP,
    "how explorer": SONNET_BACKUP,
    "how explainer": OPUS_BACKUP,
    "why investigators": SONNET_BACKUP,
    "why synthesizer": OPUS_BACKUP,
    "reflect tooling": OPUS_BACKUP,
    "reflect judgment, divergent, synthesizer": OPUS_BACKUP,
    "swarm workers": OPUS_BACKUP,
    "skill tests": SONNET_BACKUP,
    "arena runners": OPUS_BACKUP,
    "arena cross-judge pool": REVIEW_LADDER_BACKUP,
    "architect runners": OPUS_BACKUP,
    "interrogate reviewers": REVIEW_LADDER_BACKUP,
    "verifiers": REVIEW_LADDER_BACKUP,
}


PANEL_RULE = {"waitForAllTerminal": True, "minimumPasses": 2, "maximumReproducedBlockers": 0}


VERIFIER_OUT = (
    "--role", "verifiers",
    "--provider", "codex",
    "--model", "gpt-6.1-sol",
    "--author", "claudeAgent/claude-opus-5-5",
    "--out", "grok",
)


VERIFIER_PARK = {
    "decision": "park",
    "role": "verifiers",
    "failed": "codex/gpt-6.1-sol",
    "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; no backup seat, so the work waits for the reset",
}


def backup_catalog(*, second_claude=False):
    claude_options = [_select("effort", _CLAUDE_LEVELS)]
    providers = []
    if second_claude:
        providers.append({
            "providerInstanceId": "claudeDesktop",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "claude-opus-5-5", "options": claude_options}],
        })
    providers.extend([
        {
            "providerInstanceId": "cursor",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "claude-opus-5-5", "options": claude_options}],
        },
        {
            "providerInstanceId": "codex",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "gpt-6.1-sol", "options": [_select("reasoningEffort", _CODEX_LEVELS)]}],
        },
        {
            "providerInstanceId": "grok",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{
                "id": "grok-4.7",
                "options": [_select("reasoningEffort", _GROK_LEVELS), {"id": "fastMode", "type": "boolean"}],
            }],
        },
        {
            "providerInstanceId": "claudeAgent",
            "canRunChildTask": True,
            "constraints": [],
            "models": [
                {"id": "claude-opus-5-5", "options": claude_options},
                {"id": "claude-sonnet-5-5", "options": claude_options},
            ],
        },
        {
            "providerInstanceId": "opencode",
            "canRunChildTask": True,
            "constraints": [],
            "models": [
                {"id": model, "options": [_select("variant", ("high", "max"))], "cost": 0}
                for model in (MUSE, STEP, BUNNY)
            ],
        },
    ])
    return {"providers": providers}


def run_backup(*args, text=LIMIT, catalog=None, roles_file=None):
    with tempfile.TemporaryDirectory() as directory:
        repo = Repo(directory)
        path = repo.directory / "catalog.json"
        repo.put(path, backup_catalog() if catalog is None else catalog)
        if roles_file is not None:
            repo.put(repo.user, roles_file)
        env = {**os.environ, "XDG_CONFIG_HOME": str(repo.directory)}
        return in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=(
                "backup",
                "--cwd", str(repo.directory),
                "--catalog", str(path),
                "--parent", "claudeAgent/claude-opus-5-5",
                *args,
            ),
            env=env,
            stdin=text,
        ))


def plain_provider(provider_id, *model_ids, runs=True):
    return {
        "providerInstanceId": provider_id,
        "canRunChildTask": runs,
        "constraints": [],
        "models": [{"id": model_id, "options": []} for model_id in model_ids],
    }


def metered_line(role, provider, model):
    return (
        f"{role}: {provider}/{model} is a metered OpenCode model and the allow list does not name {model}. "
        f"Add it with --allow-opencode {model}"
    )


def refusal(role, provider, model):
    return "error: refusing to write without --force:\n" + metered_line(role, provider, model) + "\n"


def fixture_catalog():
    return json.loads(CATALOG.read_text())
