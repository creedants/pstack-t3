"""Role lists, error types, shared choices, panel constants, and notes."""


import re
from dataclasses import dataclass


SINGLE_ROLES = [
    "feature, refactoring",
    "bug-fix",
    "perf-issue",
    "hillclimb",
    "judgment and prose",
    "hardest tasks",
    "how explorer",
    "how explainer",
    "why investigators",
    "why synthesizer",
    "reflect tooling",
    "reflect judgment, divergent, synthesizer",
    "swarm workers",
    "skill tests",
]


PANEL_ROLES = [
    "arena runners",
    "arena cross-judge pool",
    "architect runners",
    "interrogate reviewers",
    "verifiers",
    "review backups",
]


ROLES = SINGLE_ROLES + PANEL_ROLES


BUDGETS = {"default": None, "small": "medium", "medium": "high", "large": "xhigh", "unlimited": "max"}


MODES = ("full", "light")


ATTEMPTS = ("first", "fix", "bounce")


EFFORT_IDS = ("effort", "reasoningEffort", "reasoning_effort", "reasoning")


LADDER = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4, "xhigh": 5, "extra-high": 5, "extra_high": 5, "max": 6, "ultra": 7}


SPECIAL = {"ultracode", "ultrathink"}


INHERIT = "inherit"


DRIVER_RUNTIME_MODES = {"muse": frozenset({"approval-required", "full-access"})}


RUNTIME_MODE_GAP = "runtimeModeGap"


CURSOR_DRIVER = "cursor"   # its harness sends target.options as a JSON string, which T3 refuses


HAIKU_BRIEF = (
    "Keep working until everything the user asked for is done, and only stop to ask when you can't go on without the user or before a risky step. When the work the user asked for is done and checked, stop and report. Don't add new features, docs, or refactors that weren't asked for. If you think one would help, mention it at the end instead of doing it.",
    "When you change code that can be run, built, or type-checked, run a real check that exercises the change before reporting it done: the project's tests, type-checker, or build, or the changed command itself. A syntax-only check, or a check command that failed to start, does not count; if all that is missing is the project's declared dependencies, install them with its own package manager and lockfile (e.g. npm install, pip install -r requirements.txt), never via sudo or the system package manager, unless told not to. Only if no real check can run here, say which one you did not run and why instead of reporting the change as done.",
)


CATALOG_REQUIRED = "catalog-required"


class RolesError(Exception):
    pass


class ModeSettingsError(RolesError):
    """A mode setting or mode input this version rejects."""


@dataclass(frozen=True)
class Parent:
    provider: str
    model: str


@dataclass(frozen=True)
class DefaultSelection:
    seats: object
    notes: tuple = ()


REVIEW_ROLES = frozenset({"verifiers", "interrogate reviewers", "arena cross-judge pool"})


PANEL_GATE_ROLE = "verifiers"


PANEL_BACKUP_ROLE = "review backups"


AUTHOR_CHECKED_ROLES = REVIEW_ROLES | {PANEL_BACKUP_ROLE}


PANEL_MINIMUM_PASSES = 2


PANEL_MAXIMUM_REPRODUCED_BLOCKERS = 0


PANEL_RULE = {
    "waitForAllTerminal": True,
    "minimumPasses": PANEL_MINIMUM_PASSES,
    "maximumReproducedBlockers": PANEL_MAXIMUM_REPRODUCED_BLOCKERS,
}


UNSET_ROLE_NOTE = "unset in roles.json, so it inherits this thread's seat; run setup to choose a seat"


UNSET_NOTE = (
    "review backups is set in no roles file. Set it there to let roles.py backup run a review "
    "panel when a verifier's backups are out. Unset, a verifier parks."
)


EXCLUDE_RULE = "the roles file's exclude list refuses this seat for this role"


PANEL_CATALOG_NOTE = (
    "review backups drops seats by the catalog: "
    "call orchestrator_capabilities and rerun roles.py show --catalog"
)


USAGE_LIMIT_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"you['’]?ve hit your (?:usage )?limit\b",
    r"usage limit reached",
    r"reached your usage limit",
    r"usage limit exceeded",
    r"out of usage",
    r"quota exceeded",
    r"exceeded your quota",
    r"insufficient_quota",
    r"usage balance exhausted",
))


@dataclass(frozen=True)
class Backup:
    """One usage-limit decision. relaunch carries a seat. panel carries seats and drop notes."""

    decision: str
    report: str
    seat: dict | None = None
    seats: tuple | None = None
    notes: tuple = ()


__all__ = [
    "SINGLE_ROLES",
    "PANEL_ROLES",
    "ROLES",
    "BUDGETS",
    "MODES",
    "ATTEMPTS",
    "EFFORT_IDS",
    "LADDER",
    "SPECIAL",
    "INHERIT",
    "DRIVER_RUNTIME_MODES",
    "RUNTIME_MODE_GAP",
    "CURSOR_DRIVER",
    "HAIKU_BRIEF",
    "CATALOG_REQUIRED",
    "RolesError",
    "ModeSettingsError",
    "Parent",
    "DefaultSelection",
    "REVIEW_ROLES",
    "PANEL_GATE_ROLE",
    "PANEL_BACKUP_ROLE",
    "AUTHOR_CHECKED_ROLES",
    "PANEL_MINIMUM_PASSES",
    "PANEL_MAXIMUM_REPRODUCED_BLOCKERS",
    "PANEL_RULE",
    "UNSET_ROLE_NOTE",
    "UNSET_NOTE",
    "EXCLUDE_RULE",
    "PANEL_CATALOG_NOTE",
    "USAGE_LIMIT_PATTERNS",
    "Backup",
]
