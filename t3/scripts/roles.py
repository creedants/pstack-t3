#!/usr/bin/env python3
"""Resolve pstack-t3 roles into T3 delegate_task targets.

Roles map a pstack role name to a list of seats. A seat is "inherit" or a
target {"providerInstanceId", "model", "options"} drawn from the catalog that
T3's orchestrator_capabilities tool returns.
"""

import argparse
import fnmatch
import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

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
]
ROLES = SINGLE_ROLES + PANEL_ROLES
BUDGETS = {"default": None, "small": "medium", "medium": "high", "large": "xhigh", "unlimited": "max"}
MODES = ("full", "light")
ATTEMPTS = ("first", "fix", "bounce")
EFFORT_IDS = ("effort", "reasoningEffort", "reasoning_effort", "reasoning")
LADDER = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4, "xhigh": 5, "extra-high": 5, "extra_high": 5, "max": 6, "ultra": 7}
SPECIAL = {"ultracode", "ultrathink"}
INHERIT = "inherit"
SMALL_TIER = frozenset({"haiku", "mini", "nano", "flash", "lite", "fast", "small", "luna"})
PROMPT_CAPS = {"claude-haiku-5-5": 100000}  # soft target: the estimated prompt stays at or under it
BYTES_PER_TOKEN = 4                          # rough, for prose and code
OVERHEAD_TOKENS = 41000                      # harness allowance: a real T3 Claude Haiku 5.5 child's first request was 40,427 tokens on 2026-10-07
BOUNDED_ROLES = frozenset({"skill tests"})
SKILL_TESTS_CAP_NOTE = "claude-haiku-5-5 is capped; roles.py bounded-seat launches it when the whole prompt fits"
CATALOG_REQUIRED = "catalog-required"
DEFAULT_PANEL = "default-panel"


class RolesError(Exception):
    pass


class ModeSettingsError(RolesError):
    """A mode setting or mode input this version rejects."""


@dataclass(frozen=True)
class ModeDecision:
    """One mode decision and where it came from.

    label is the brief grammar's Mode source value. where is what show prints
    as modeSource. A file level uses the file path. Every other level uses the label.
    """

    mode: str
    label: str
    where: str


DEFAULT_MODE = ModeDecision("full", "default", "default")


def effective_mode(config, brief=None, session=None, coordinator=None):
    """Brief, then session, then coordinator, then the merged file level, then full."""
    for value, label in ((brief, "brief"), (session, "session"), (coordinator, "restaurant.json")):
        if value is not None:
            return ModeDecision(value, label, label)
    return config.get("mode", DEFAULT_MODE)


def seat_budget(budget, mode):
    """Light caps only a default budget. An explicit budget wins both ways."""
    return "small" if mode == "light" and budget == "default" else budget


NO_CATALOG_INFO = (
    "info: light mode caps reasoning at medium, but show had no catalog, "
    "so no seat was capped. Rerun with --catalog and --parent."
)
NO_PARENT_INFO = (
    "info: light mode caps reasoning at medium, but show had no --parent, "
    "so an inherit seat may keep the parent's reasoning. Rerun with --parent."
)


def light_cap_gap(decision, budget, has_catalog, has_parent):
    """The info line show prints when the light cap may not have reached every seat."""
    if seat_budget(budget, decision.mode) == budget:
        return None
    if not has_catalog:
        return NO_CATALOG_INFO
    if not has_parent:
        return NO_PARENT_INFO
    return None


@dataclass(frozen=True)
class Parent:
    provider: str
    model: str


@dataclass(frozen=True)
class PreferredSeat:
    model_id: str
    effort_ceiling: str = "xhigh"
    prefer_fast: bool = False


class AdaptiveDefault(Enum):
    SKILL_TESTS = "skill-tests"
    VERIFIERS = "verifiers"


OPUS = PreferredSeat("claude-opus-5-5")
GROK = PreferredSeat("grok-4.7", prefer_fast=True)

ROLE_DEFAULTS = {
    "feature, refactoring": (GROK,),
    "bug-fix": (GROK,),
    "perf-issue": (GROK,),
    "hillclimb": (GROK,),
    "judgment and prose": (OPUS,),
    "hardest tasks": (OPUS,),
    "how explorer": (GROK,),
    "how explainer": (OPUS,),
    "why investigators": (GROK,),
    "why synthesizer": (OPUS,),
    "reflect tooling": (GROK,),
    "reflect judgment, divergent, synthesizer": (OPUS,),
    "swarm workers": (GROK,),
    "arena runners": (OPUS, GROK),
    "arena cross-judge pool": (OPUS, GROK),
    "architect runners": (OPUS, GROK),
    "interrogate reviewers": (OPUS, GROK),
    "skill tests": AdaptiveDefault.SKILL_TESTS,
    "verifiers": AdaptiveDefault.VERIFIERS,
}


@dataclass(frozen=True)
class DefaultSelection:
    seats: object
    notes: tuple = ()


def user_config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "pstack-t3" / "roles.json"


def snapshot_path():
    return user_config_path().with_name("catalog.json")


def project_config_path(cwd):
    current = Path(cwd).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / ".pstack" / "t3-roles.json"
        if candidate.is_file():
            return candidate
        if (directory / ".git").exists():
            return directory / ".pstack" / "t3-roles.json"
    return current / ".pstack" / "t3-roles.json"


def parse_json(text, origin):
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise RolesError(f"{origin}: invalid JSON: {error}") from error


def load_json(path):
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return None
    return parse_json(text, path)


def load_catalog(path):
    data = parse_json(sys.stdin.read(), "-") if str(path) == "-" else load_json(path)
    if data is None:
        raise RolesError(f"{path}: catalog not found")
    if "providers" not in data:
        raise RolesError(f"{path}: not an orchestrator_capabilities result (no providers)")
    return data


def parse_seat(text):
    """Parse `inherit` or `provider/model[?option=value&option=value]`."""
    text = text.strip()
    if text == INHERIT:
        return INHERIT
    head, _, query = text.partition("?")
    provider, slash, model = head.partition("/")
    if not slash or not provider or not model:
        raise RolesError(f"seat {text!r}: expected 'inherit' or 'provider/model[?option=value]'")
    options = {}
    for pair in filter(None, query.split("&")):
        key, equals, value = pair.partition("=")
        if not equals:
            raise RolesError(f"seat {text!r}: option {pair!r} needs a value")
        options[key] = {"true": True, "false": False}.get(value, value)
    seat = {"providerInstanceId": provider, "model": model}
    if options:
        seat["options"] = options
    return seat


def bare_id(model_id):
    return model_id.rsplit("/", 1)[-1].lower()


def prompt_cap(model_id):
    if not isinstance(model_id, str) or not model_id:
        return None
    return PROMPT_CAPS.get(bare_id(model_id))


def _line_text(model_id):
    """Bare id with a `5p3` version marker rewritten to `5.3`."""
    return re.sub(r"(\d)p(\d)", r"\1.\2", bare_id(model_id))


def model_version(model_id):
    return tuple(int(part) for part in re.findall(r"\d+", _line_text(model_id)))


def model_line(model_id):
    return tuple(part for part in re.split(r"[-_.\d]+", _line_text(model_id)) if part)


def window_tokens(choice_id):
    """Context-window choice ids. k is 1000 and m is 1000000."""
    match = re.fullmatch(r"(\d+)([km])", str(choice_id).lower())
    if not match:
        return None
    return int(match.group(1)) * {"k": 1000, "m": 1000000}[match.group(2)]


def cap_refusal(name, provider_id, model_id, *, inherit=False):
    cap = prompt_cap(model_id)
    if cap is None or name in BOUNDED_ROLES:
        return None
    verb = "inherit" if inherit else "use"
    allowed = ", ".join(sorted(BOUNDED_ROLES))
    return (
        f"role {name!r} cannot {verb} {provider_id}/{model_id}: "
        f"{bare_id(model_id)} is capped at {cap} prompt tokens, and only {allowed} may run a capped model"
    )


def parse_parent(text):
    """Provider is the text before the first slash. The model id may contain slashes."""
    message = (
        f"--parent {text!r}: expected '<inheritedProviderInstanceId>/<inheritedModel>' "
        "from orchestrator_capabilities"
    )
    if not isinstance(text, str) or "/" not in text or any(char.isspace() for char in text) or "?" in text:
        raise RolesError(message)
    provider, _, model = text.partition("/")
    if not provider or not model:
        raise RolesError(message)
    return Parent(provider, model)


def inherit_parent(catalog):
    """Parent stored on a catalog file, or None when those keys are missing or malformed."""
    if not isinstance(catalog, dict):
        return None
    provider = catalog.get("inheritedProviderInstanceId")
    model = catalog.get("inheritedModel")
    if not isinstance(provider, str) or not isinstance(model, str) or not provider or not model:
        return None
    try:
        return parse_parent(f"{provider}/{model}")
    except RolesError:
        return None


def stamp_parent(catalog, parent):
    return {
        **catalog,
        "inheritedProviderInstanceId": None if parent is None else parent.provider,
        "inheritedModel": None if parent is None else parent.model,
    }


def is_snapshot_path(path):
    if path is None or str(path) == "-":
        return False
    try:
        return Path(path).resolve() == snapshot_path().resolve()
    except OSError:
        return False


def given_parent(args):
    text = getattr(args, "parent", None)
    if text is None:
        return None
    return parse_parent(text)


def parent_for(args, catalog, catalog_path):
    """--parent wins. A snapshot never supplies a parent. An explicit catalog file does."""
    given = given_parent(args)
    if given is not None:
        return given
    if catalog is None or is_snapshot_path(catalog_path):
        return None
    return inherit_parent(catalog)


def all_capped_message(name, catalog):
    seen = []
    for provider in catalog.get("providers") or []:
        if not runnable(provider):
            continue
        for model in models_of(provider):
            cap = prompt_cap(model.get("id"))
            if cap is None:
                continue
            label = f"{bare_id(model['id'])} at {cap}"
            if label not in seen:
                seen.append(label)
    if not seen:
        seen = [f"{model_id} at {cap}" for model_id, cap in PROMPT_CAPS.items()]
    allowed = ", ".join(sorted(BOUNDED_ROLES))
    return (
        f"role {name!r} has no seat: every runnable model in the catalog is capped "
        f"({', '.join(seen)} prompt tokens), and only {allowed} may run a capped model"
    )


def no_runnable_provider(catalog):
    return not any(runnable(provider) for provider in (catalog or {}).get("providers") or [])


def canonical(path, kind="lease"):
    """Repository-relative POSIX path with no aliases. An empty string is the whole repository."""
    value = posixpath.normpath(path.strip().replace("\\", "/")).lstrip("/")
    if value in (".", ""):
        return ""
    if value == ".." or value.startswith("../"):
        raise ModeSettingsError(f"{kind} path {path!r} leaves the repository")
    return value


def lease_paths(text):
    """Leases land.py would claim for this comma-separated list."""
    return sorted({canonical(part) for part in text.split(",")})


def check_shape(config, origin):
    if not isinstance(config, dict):
        raise RolesError(f"{origin}: expected an object")
    budget = config.get("budget", "default")
    if budget not in BUDGETS:
        raise RolesError(f"{origin}: budget {budget!r} is not one of {', '.join(BUDGETS)}")
    if "mode" in config and config["mode"] not in MODES:
        raise ModeSettingsError(f"{origin}: mode {config['mode']!r} is not one of {', '.join(MODES)}")
    if "escalate" in config:
        escalate = config["escalate"]
        if not isinstance(escalate, list) or not all(isinstance(item, str) and item for item in escalate):
            raise ModeSettingsError(f"{origin}: escalate must be a list of strings")
        for item in escalate:
            try:
                canonical(item, "escalate pattern")  # traversal check only; the stored pattern stays raw
            except ModeSettingsError:
                raise ModeSettingsError(f"{origin}: escalate pattern {item!r} leaves the repository") from None
    roles = config.get("roles", {})
    if not isinstance(roles, dict):
        raise RolesError(f"{origin}: roles must be an object")
    for name, seats in roles.items():
        if name not in ROLES:
            raise RolesError(f"{origin}: unknown role {name!r}")
        if not isinstance(seats, list) or not seats:
            raise RolesError(f"{origin}: role {name!r} needs a non-empty list of seats")
        if name in SINGLE_ROLES and len(seats) != 1:
            raise RolesError(f"{origin}: role {name!r} takes exactly one seat")
        for seat in seats:
            if seat == INHERIT:
                continue
            if not isinstance(seat, dict) or not seat.get("providerInstanceId") or not seat.get("model"):
                raise RolesError(f"{origin}: role {name!r} has a seat without providerInstanceId and model")
            provider_id = seat["providerInstanceId"]
            model_id = seat["model"]
            refusal = cap_refusal(name, provider_id, model_id)
            if refusal:
                raise RolesError(f"{origin}: {refusal}")
    return config


def merged_config(cwd, user_path=None, project_path=None):
    user_path = Path(user_path) if user_path else user_config_path()
    project_path = Path(project_path) if project_path else project_config_path(cwd)
    user = check_shape(load_json(user_path) or {}, user_path)
    project = check_shape(load_json(project_path) or {}, project_path)
    roles, sources = {}, {}
    for origin, config in ((user_path, user), (project_path, project)):
        for name, seats in config.get("roles", {}).items():
            roles[name] = seats
            sources[name] = str(origin)
    if "mode" in project:
        mode = ModeDecision(project["mode"], ".pstack/t3-roles.json", str(project_path))
    elif "mode" in user:
        mode = ModeDecision(user["mode"], "roles.json", str(user_path))
    else:
        mode = DEFAULT_MODE
    escalate = list(project["escalate"]) if "escalate" in project else None
    budget = project["budget"] if "budget" in project else user.get("budget", "default")
    return {
        "budget": budget,
        "mode": mode,
        "escalate": escalate,
        "roles": roles,
        "sources": sources,
    }


def providers_by_id(catalog):
    return {provider["providerInstanceId"]: provider for provider in catalog["providers"]}


def models_of(provider):
    return (provider or {}).get("models") or []


def options_of(model):
    return (model or {}).get("options") or []


def runnable(provider):
    return bool(provider and provider.get("canRunChildTask") and models_of(provider))


def find_model(provider, model_id):
    return next((model for model in models_of(provider) if model["id"] == model_id), None)


def rank(value):
    return LADDER.get(value)


def family(model_id):
    """Model family from the model id's leading word: claude-opus-5-5 -> claude."""
    head = re.split(r"[-_.\d]", model_id.lower(), maxsplit=1)[0]
    return head or model_id.lower()


def effort_option(model):
    return next((option for option in options_of(model) if option["id"] in EFFORT_IDS and option.get("type") == "select"), None)


def check_option_values(seat, model):
    """Return problems for option values the catalog does not offer."""
    problems = []
    declared = {option["id"]: option for option in options_of(model)}
    for key, value in (seat.get("options") or {}).items():
        option = declared.get(key)
        if option is None:
            continue
        if option.get("type") == "boolean" and not isinstance(value, bool):
            problems.append(f"option {key}={value!r} must be true or false")
        elif option.get("type") == "select":
            choices = [choice["id"] for choice in option.get("options") or []]
            if value not in choices:
                problems.append(f"option {key}={value!r} is not one of {', '.join(choices)}")
    return problems


def apply_budget(seat, model, budget):
    cap = BUDGETS[budget]
    option = effort_option(model) if model else None
    if cap is None or option is None:
        return seat
    values = [choice["id"] for choice in option.get("options") or [] if choice["id"] not in SPECIAL and rank(choice["id"]) is not None]
    if not values:
        return seat
    # unlimited caps at max, below ultra. A model with nothing at or below the cap gets its lowest level.
    allowed = [value for value in values if rank(value) <= rank(cap)] or [min(values, key=rank)]
    current = (seat.get("options") or {}).get(option["id"])
    chosen = current if current in allowed else max(allowed, key=rank)
    return {**seat, "options": {**(seat.get("options") or {}), option["id"]: chosen}}


def model_tokens(model_id):
    return re.split(r"[-_.]", model_id.lower())


def default_effort_rank(model):
    """Rank of the model's default reasoning level. No effort select sorts first."""
    option = effort_option(model)
    if option is None:
        return -1
    choices = [choice for choice in option.get("options") or [] if choice["id"] not in SPECIAL and rank(choice["id"]) is not None]
    if not choices:
        return -1
    default = next((choice for choice in choices if choice.get("isDefault")), None)
    if default is None:
        return min(rank(choice["id"]) for choice in choices)
    return rank(default["id"])


def skill_tests_seat(catalog, allow_capped=False, launches_seats=False):
    """One bare seat. Prefer another family, then a small-tier id, then a lower default effort.

    The winning row names a model line. The seat is the newest version of that line.
    """
    parent = catalog.get("inheritedModel")
    parent_family = family(parent) if parent else None
    rows = []
    for provider_index, provider in enumerate(catalog["providers"]):
        if launches_seats and provider["providerInstanceId"] == "cursor":
            continue
        if not runnable(provider):
            continue
        for model_index, model in enumerate(models_of(provider)):
            model_id = model["id"]
            if prompt_cap(model_id) is not None and not allow_capped:
                continue
            other = parent_family is None or family(model_id) != parent_family
            small = bool(set(model_tokens(model_id)) & SMALL_TIER)
            rows.append((0 if other else 1, 0 if small else 1, default_effort_rank(model), provider_index, model_index, provider, model))
    if not rows:
        return INHERIT
    line = model_line(min(rows)[-1]["id"])
    pool = [row for row in rows if model_line(row[-1]["id"]) == line]
    newest = max(model_version(row[-1]["id"]) for row in pool)
    *_, provider, model = min(row for row in pool if model_version(row[-1]["id"]) == newest)
    return {"providerInstanceId": provider["providerInstanceId"], "model": model["id"]}


def _runnable_rows(catalog):
    rows = []
    for provider in catalog["providers"]:
        if not runnable(provider):
            continue
        for model in models_of(provider):
            if prompt_cap(model["id"]) is None:
                rows.append((provider, model))
    return rows


def _provider_for_exact(matches, wanted_family):
    """Prefer a provider whose first model shares the family. Otherwise catalog order."""
    fallback = None
    for provider, model in matches:
        if fallback is None:
            fallback = (provider, model)
        if family(models_of(provider)[0]["id"]) == wanted_family:
            return provider, model
    return fallback


def _preferred_seat(preference, catalog, budget="default", role=None):
    """Return a concrete runnable target and explanations of changed intent."""
    if no_runnable_provider(catalog):
        raise RolesError("no provider in the catalog can run child tasks")
    rows = _runnable_rows(catalog)
    if not rows:
        raise RolesError(all_capped_message(role or "bug-fix", catalog))
    wanted = preference.model_id
    wanted_family = family(wanted)
    exact = [(provider, model) for provider, model in rows if model["id"] == wanted]
    cause = None
    if exact:
        provider, model = _provider_for_exact(exact, wanted_family)
    else:
        same_family = [(provider, model) for provider, model in rows if family(model["id"]) == wanted_family]
        if same_family:
            provider, model = same_family[0]
            cause = "missing model"
        else:
            parent_id = catalog.get("inheritedProviderInstanceId")
            parent_model_id = catalog.get("inheritedModel")
            parent = providers_by_id(catalog).get(parent_id) if parent_id else None
            parent_model = find_model(parent, parent_model_id) if runnable(parent) and parent_model_id else None
            if parent_model is not None and prompt_cap(parent_model_id) is None:
                provider, model = parent, parent_model
            else:
                provider, model = rows[0]
            cause = "missing family"
    # xhigh is the built-in ceiling. unlimited replaces it with the highest level at or below max.
    stamp = "unlimited" if budget == "unlimited" else "large"
    seat = apply_budget({"providerInstanceId": provider["providerInstanceId"], "model": model["id"]}, model, stamp)
    notes = []
    if cause is not None:
        notes.append(f"wanted {wanted}, using {provider['providerInstanceId']}/{model['id']} ({cause})")
    option = effort_option(model)
    chosen = (seat.get("options") or {}).get(option["id"]) if option else None
    if chosen is not None and rank(chosen) is not None and rank(preference.effort_ceiling) is not None and rank(chosen) < rank(preference.effort_ceiling):
        notes.append(f"wanted {preference.effort_ceiling}, using {chosen}")
    declares_fast = any(item.get("id") == "fastMode" and item.get("type") == "boolean" for item in options_of(model))
    if preference.prefer_fast and declares_fast and family(model["id"]) == family(preference.model_id):
        seat = {**seat, "options": {**(seat.get("options") or {}), "fastMode": True}}
    return seat, tuple(notes)


def _first_uncapped(provider):
    return next((model for model in models_of(provider) if prompt_cap(model["id"]) is None), None)


def _verifier_seats(catalog, role="verifiers"):
    """One inherit seat for this thread, then one seat per new family. One seat is repeated to three."""
    parent = catalog.get("inheritedProviderInstanceId")
    parent_model = catalog.get("inheritedModel")
    parent_provider = providers_by_id(catalog).get(parent) if parent else None
    parent_runs = runnable(parent_provider) if parent else False
    parent_capped = bool(parent_model) and prompt_cap(parent_model) is not None
    seats, families = [], set()
    if parent_runs and parent_model and not parent_capped:
        seats.append(INHERIT)
        families.add(family(parent_model))
    for provider in catalog["providers"]:
        if not runnable(provider):
            continue
        # A capped parent has no inherit seat, so that provider still contributes its first uncapped model.
        if parent_runs and not parent_capped and provider["providerInstanceId"] == parent:
            continue
        model = _first_uncapped(provider)
        if model is None:
            continue
        model_id = model["id"]
        if family(model_id) in families:
            continue
        families.add(family(model_id))
        seats.append({"providerInstanceId": provider["providerInstanceId"], "model": model_id})
    if len(seats) == 1:
        return seats * 3
    if not seats:
        if no_runnable_provider(catalog):
            raise RolesError("no provider in the catalog can run child tasks")
        raise RolesError(all_capped_message(role, catalog))
    return seats


def _lost_diversity(name, seats):
    groups = {}
    for number, seat in enumerate(seats, 1):
        if not isinstance(seat, dict):
            continue
        groups.setdefault((seat["providerInstanceId"], seat["model"]), []).append(str(number))
    for (provider, model), numbers in groups.items():
        if len(numbers) > 1:
            joined = " and ".join(numbers)
            return f"{name}: seats {joined} both use {provider}/{model}, so the panel lost a distinct model"
    return None


def default_seats(name, catalog, budget="default"):
    """Resolve exactly the policy seats for this role from the live catalog."""
    policy = ROLE_DEFAULTS[name]
    if catalog is None:
        if policy is AdaptiveDefault.SKILL_TESTS:
            return DefaultSelection((INHERIT,))
        if policy is AdaptiveDefault.VERIFIERS:
            return DefaultSelection(DEFAULT_PANEL, (
                "expand from orchestrator_capabilities: this thread inherits, then one seat per runnable provider whose first model is a new model family",
            ))
        return DefaultSelection(CATALOG_REQUIRED, (
            "call orchestrator_capabilities and rerun roles.py show --catalog",
        ))
    if policy is AdaptiveDefault.SKILL_TESTS:
        return DefaultSelection((skill_tests_seat(catalog, allow_capped=True),))
    if policy is AdaptiveDefault.VERIFIERS:
        return DefaultSelection(tuple(_verifier_seats(catalog, name)))
    seats, notes = [], []
    for number, preference in enumerate(policy, 1):
        seat, seat_notes = _preferred_seat(preference, catalog, budget, name)
        seats.append(seat)
        notes.extend(f"{name} seat {number}: {note}" for note in seat_notes)
    diversity = _lost_diversity(name, seats)
    if diversity:
        notes.append(diversity)
    return DefaultSelection(tuple(seats), tuple(notes))


def inherit_with_budget(catalog, budget):
    """An inherit seat under a non-default budget becomes the parent's model with capped effort."""
    parent = catalog.get("inheritedProviderInstanceId")
    parent_model = catalog.get("inheritedModel")
    if BUDGETS[budget] is None or not parent or not parent_model:
        return INHERIT, None
    provider = providers_by_id(catalog).get(parent)
    model = find_model(provider, parent_model) if runnable(provider) else None
    if model is None or effort_option(model) is None:
        return INHERIT, None
    seat = apply_budget({"providerInstanceId": parent, "model": parent_model}, model, budget)
    return seat, f"inherit made explicit as {parent}/{parent_model} so the {budget} budget applies"


def _context_window_choice(model):
    option = next((item for item in options_of(model) if item.get("id") == "contextWindow" and item.get("type") == "select"), None)
    if option is None:
        return None
    ranked = []
    for choice in option.get("options") or []:
        tokens = window_tokens(choice.get("id"))
        if tokens is not None:
            ranked.append((tokens, choice["id"]))
    if not ranked:
        return None
    return min(ranked)[1]


def _with_window(seat, model):
    if prompt_cap(seat.get("model")) is None:
        return seat
    choice = _context_window_choice(model)
    if choice is None:
        return seat
    return {**seat, "options": {**(seat.get("options") or {}), "contextWindow": choice}}


def _explicit_parent_window(catalog, budget):
    """Bounded inherit of a capped parent becomes that seat when a context window is offered."""
    parent_id = catalog.get("inheritedProviderInstanceId")
    parent_model_id = catalog.get("inheritedModel")
    if not parent_id or not parent_model_id or prompt_cap(parent_model_id) is None:
        return None
    provider = providers_by_id(catalog).get(parent_id)
    model = find_model(provider, parent_model_id) if provider else None
    if model is None or _context_window_choice(model) is None:
        return None
    seat = apply_budget({"providerInstanceId": parent_id, "model": parent_model_id}, model, budget)
    return _with_window(seat, model)


def _resolve_inherit(catalog, budget, name):
    parent = inherit_parent(catalog)
    if name not in BOUNDED_ROLES and parent is not None and prompt_cap(parent.model) is not None:
        raise RolesError(cap_refusal(name, parent.provider, parent.model, inherit=True))
    value, note = inherit_with_budget(catalog, budget)
    if isinstance(value, dict):
        provider = providers_by_id(catalog).get(value["providerInstanceId"])
        model = find_model(provider, value["model"]) if provider else None
        if model is not None:
            value = _with_window(value, model)
        return value, note
    if name in BOUNDED_ROLES:
        explicit = _explicit_parent_window(catalog, budget)
        if explicit is not None:
            return explicit, note
    return value, note


def resolve_seat(seat, catalog, budget, name):
    """Return (resolved seat, notes, problems). Problems are seats the catalog rejects."""
    if seat == INHERIT:
        value, note = _resolve_inherit(catalog, budget, name)
        return value, [{"info": note}] if note else [], []
    provider = providers_by_id(catalog).get(seat["providerInstanceId"])
    if not runnable(provider):
        reason = "; ".join(provider.get("constraints") or []) if provider else "not in catalog"
        note = f"{seat['providerInstanceId']} is not runnable ({reason}); seat inherits the parent"
        value, budget_note = _resolve_inherit(catalog, budget, name)
        return value, [note] + ([{"info": budget_note}] if budget_note else []), [note]
    notes, problems = [], []
    model = find_model(provider, seat["model"])
    if model is None:
        if name in BOUNDED_ROLES:
            model = models_of(provider)[0]
        else:
            model = _first_uncapped(provider)
            if model is None:
                first = models_of(provider)[0]
                raise RolesError(cap_refusal(name, provider["providerInstanceId"], first["id"]))
        note = f"{seat['providerInstanceId']}/{seat['model']} is not in the catalog; using {model['id']}"
        notes.append(note)
        problems.append(note)
        seat = {**seat, "providerInstanceId": provider["providerInstanceId"], "model": model["id"]}
    known = {option["id"] for option in options_of(model)}
    dropped = sorted(set(seat.get("options") or {}) - known)
    if dropped:
        note = f"dropped unknown options {', '.join(dropped)}"
        notes.append(note)
        problems.append(note)
    invalid = check_option_values(seat, model)
    problems.extend(invalid)
    notes.extend(f"dropped {problem}" for problem in invalid)
    bad = {problem.split("=", 1)[0].removeprefix("option ") for problem in invalid}
    options = {key: value for key, value in (seat.get("options") or {}).items() if key in known and key not in bad}
    seat = {key: value for key, value in seat.items() if key != "options"}
    if options:
        seat["options"] = options
    return _with_window(apply_budget(seat, model, budget), model), notes, problems


def present_seat(seat, catalog, budget):
    if not isinstance(seat, dict) or catalog is None:
        return seat
    provider = providers_by_id(catalog).get(seat["providerInstanceId"])
    model = find_model(provider, seat["model"]) if provider else None
    if model is None:
        return seat
    return _with_window(apply_budget(dict(seat), model, budget), model)


def skill_tests_replacement(catalog, budget):
    if catalog is None:
        return CATALOG_REQUIRED, SKILL_TESTS_CAP_NOTE
    if no_runnable_provider(catalog):
        raise RolesError("no provider in the catalog can run child tasks")
    seat = skill_tests_seat(catalog, allow_capped=False)
    if not isinstance(seat, dict) or prompt_cap(seat.get("model")) is not None:
        raise RolesError(all_capped_message("skill tests", catalog))
    return [present_seat(seat, catalog, budget)], SKILL_TESTS_CAP_NOTE


def _seats_include_inherit(seats):
    if seats in (INHERIT, DEFAULT_PANEL):
        return True
    return isinstance(seats, list) and any(seat == INHERIT for seat in seats)


def _seats_include_capped(seats):
    if not isinstance(seats, list):
        return False
    return any(isinstance(seat, dict) and prompt_cap(seat.get("model")) is not None for seat in seats)


def settle_caps(name, seats, catalog, parent, budget):
    """Settle show's output: keep a capped model out of every emitted seat, and refuse a known capped parent outside skill tests.

    bounded-seat deliberately skips this so a capped seat under the target still launches.
    """
    capped_parent = parent is not None and prompt_cap(parent.model) is not None
    if _seats_include_inherit(seats) and capped_parent:
        if name in BOUNDED_ROLES:
            return skill_tests_replacement(catalog, budget)
        raise RolesError(cap_refusal(name, parent.provider, parent.model, inherit=True))
    if _seats_include_capped(seats):
        if name in BOUNDED_ROLES:
            return skill_tests_replacement(catalog, budget)
        for seat in seats:
            if isinstance(seat, dict):
                message = cap_refusal(name, seat.get("providerInstanceId"), seat.get("model"))
                if message:
                    raise RolesError(message)
    return seats, None


def _store_settled(entry, name, catalog, parent, budget):
    seats, note = settle_caps(name, entry["seats"], catalog, parent, budget)
    entry["seats"] = seats
    if note:
        entry["note"] = note


def resolve(config, catalog=None, names=None, parent=None):
    names = names or ROLES
    decision = config.get("mode", DEFAULT_MODE)
    budget = seat_budget(config["budget"], decision.mode)
    if catalog is not None:
        if parent is None:
            parent = inherit_parent(catalog)
        catalog = stamp_parent(catalog, parent)
    result = {
        "budget": config["budget"],
        "mode": decision.mode,
        "modeSource": decision.where,
        "escalate": config["escalate"] if "escalate" in config else None,
        "catalog": bool(catalog),
        "roles": {},
    }
    for name in names:
        if name not in ROLES:
            raise RolesError(f"unknown role {name!r}")
        configured = config["roles"].get(name)
        entry = {"source": config["sources"].get(name, "default")}
        if configured is None:
            selection = default_seats(name, catalog, budget)
            if isinstance(selection.seats, str):
                entry["seats"] = selection.seats
                if selection.notes:
                    entry["note"] = selection.notes[0]
                _store_settled(entry, name, catalog, parent, budget)
                result["roles"][name] = entry
                continue
            seats = list(selection.seats)
            selection_notes = list(selection.notes)
        else:
            seats = configured
            selection_notes = []
        if catalog is None:
            entry["seats"] = seats
        else:
            resolved, notes = [], []
            for seat in seats:
                value, seat_notes, _ = resolve_seat(seat, catalog, budget, name)
                resolved.append(value)
                notes.extend(seat_notes)
            entry["seats"] = resolved
            info = [note["info"] for note in notes if isinstance(note, dict)]
            problems = selection_notes + [note for note in notes if not isinstance(note, dict)]
            if problems:
                entry["notes"] = problems
            if info:
                entry["info"] = info
        _store_settled(entry, name, catalog, parent, budget)
        result["roles"][name] = entry
    return result


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, suffix=".tmp") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(handle.name, path)


def validate(config, catalog):
    problems = []
    for name, seats in config["roles"].items():
        for seat in seats:
            _, _, seat_problems = resolve_seat(seat, catalog, config["budget"], name)
            problems.extend(f"{name}: {problem}" for problem in seat_problems)
    return problems


def load_show_catalog(args):
    if args.catalog:
        return args.catalog, load_catalog(args.catalog)
    path = snapshot_path()
    if path.is_file():
        return path, load_catalog(path)
    return None, None


def catalog_for_check(args, catalog, catalog_path):
    """Parent for this invocation, and a catalog copy whose inherited keys match it."""
    parent = parent_for(args, catalog, catalog_path)
    if catalog is None:
        return parent, None
    return parent, stamp_parent(catalog, parent)


def command_show(args):
    given_parent(args)
    config = merged_config(args.cwd, args.config, args.project_config)
    decision = effective_mode(config, args.brief_mode, args.session_mode, args.coordinator_mode)
    config["mode"] = decision
    catalog_path, catalog = load_show_catalog(args)
    parent, catalog = catalog_for_check(args, catalog, catalog_path)
    print(json.dumps(resolve(config, catalog, args.role, parent), indent=2))
    line = light_cap_gap(decision, config["budget"], catalog is not None, args.parent is not None)
    if line:
        print(line, file=sys.stderr)


def command_validate(args):
    given_parent(args)
    config = merged_config(args.cwd, args.config, args.project_config)
    catalog = load_catalog(args.catalog)
    _parent, catalog = catalog_for_check(args, catalog, args.catalog)
    problems = validate(config, catalog)
    for problem in problems:
        print(problem)
    if not problems:
        print("ok")
    return 1 if problems else 0


def command_write(args):
    given_parent(args)
    catalog = load_catalog(args.catalog)
    target = project_config_path(args.cwd) if args.project else (Path(args.config) if args.config else user_config_path())
    try:
        loaded = load_json(target)
    except RolesError:
        if args.keep:
            raise
        loaded = None
    if loaded is None:
        loaded = {}
    if args.keep:
        raw = check_shape(loaded, target)
    elif isinstance(loaded, dict):
        raw = loaded
    else:
        raw = {}
    # Budget and roles survive a rewrite only with --keep. Escalate is separate.
    existing = raw if args.keep else {}
    roles = dict(existing.get("roles", {}))
    for assignment in args.set or []:
        name, equals, value = assignment.partition("=")
        name = name.strip()
        if not equals or name not in ROLES:
            raise RolesError(f"--set {assignment!r}: expected '<role>=<seat>[;<seat>...]' with a known role")
        roles[name] = [parse_seat(part) for part in value.split(";") if part.strip()]
    config = {"version": 1, "roles": roles}
    budget = args.budget or existing.get("budget")
    if budget is not None:
        config["budget"] = budget
    elif not args.project:
        config["budget"] = "default"
    if args.mode:
        config["mode"] = args.mode
    elif args.keep and "mode" in raw:
        config["mode"] = raw["mode"]
    elif not args.project:
        config["mode"] = "full"
    if args.project and not args.clear_escalate:
        if args.escalate is not None:
            config["escalate"] = list(args.escalate)
        elif "escalate" in raw:
            config["escalate"] = raw["escalate"]
    check_shape(config, target)
    if "escalate" in config:
        config["escalate"] = list(config["escalate"])
    _parent, checking = catalog_for_check(args, catalog, args.catalog)
    problems = validate({"budget": config.get("budget", "default"), "roles": roles, "sources": {}}, checking)
    if problems and not args.force:
        raise RolesError("refusing to write; these seats do not match the catalog:\n" + "\n".join(problems))
    write_atomic(target, config)
    if not args.project and not args.config:
        write_atomic(snapshot_path(), catalog)
    print(f"wrote {target}")


def check_reads(paths):
    for path in paths:
        if not Path(path).is_file():
            raise RolesError(f"--read {path}: not a file")


def prompt_estimate(brief_bytes, read_bytes, model_id):
    payload = brief_bytes + read_bytes
    tokens = OVERHEAD_TOKENS + (payload + BYTES_PER_TOKEN - 1) // BYTES_PER_TOKEN
    return {
        "overheadTokens": OVERHEAD_TOKENS,
        "briefBytes": brief_bytes,
        "readBytes": read_bytes,
        "tokens": tokens,
        "target": prompt_cap(model_id),
    }


def launch_model(seat, parent):
    """Model a launch of this resolved seat runs: the target's model, or the parent's for inherit."""
    if seat == INHERIT:
        return parent.model
    return seat["model"]


def launch_provider(seat, parent):
    """Provider a launch of this resolved seat runs: the target's provider, or the parent's for inherit."""
    if seat == INHERIT:
        return parent.provider
    return seat["providerInstanceId"]


def emit_bounded(seat, capped, estimate, reason, notes):
    print(json.dumps({
        "seat": seat,
        "capped": capped,
        "estimate": estimate,
        "reason": reason,
        "notes": notes,
    }, indent=2))


def command_bounded_seat(args):
    given_parent(args)
    config = merged_config(args.cwd, args.config, args.project_config)
    catalog = load_catalog(args.catalog)
    parent, catalog = catalog_for_check(args, catalog, args.catalog)
    brief = Path(args.brief)
    if not brief.is_file():
        raise RolesError(f"{args.brief}: brief not found")
    reads = args.read or []
    check_reads(reads)
    decision = effective_mode(config, args.brief_mode, args.session_mode, args.coordinator_mode)
    budget = seat_budget(config["budget"], decision.mode)
    launches_seats = args.launches_seats
    configured = (config.get("roles") or {}).get("skill tests")
    configured_cursor = (
        launches_seats
        and configured
        and isinstance(configured[0], dict)
        and configured[0]["providerInstanceId"] == "cursor"
    )
    if configured_cursor:
        candidate = skill_tests_seat(catalog, allow_capped=True, launches_seats=True)
    else:
        candidate = configured[0] if configured else skill_tests_seat(
            catalog, allow_capped=True, launches_seats=launches_seats,
        )

    def resolved(seat):
        value, raw_notes, _problems = resolve_seat(seat, catalog, budget, "skill tests")
        notes = [note["info"] if isinstance(note, dict) else note for note in raw_notes]
        if launches_seats and launch_provider(value, parent) == "cursor":
            raise RolesError("role 'skill tests' has no non-cursor seat for a child that launches seats")
        return value, notes

    seat, notes = resolved(candidate)
    model = launch_model(seat, parent)
    read_bytes = sum(Path(path).stat().st_size for path in reads)
    estimate = prompt_estimate(brief.stat().st_size, read_bytes, model)
    if prompt_cap(model) is None:
        emit_bounded(seat, False, estimate, None, notes)
        return 0
    if estimate["tokens"] <= estimate["target"]:
        emit_bounded(seat, True, estimate, None, notes)
        return 0
    reason = (
        f"estimate {estimate['tokens']} tokens is over the {estimate['target']}-token target "
        f"for {bare_id(model)}"
    )
    fallback, more = resolved(skill_tests_seat(catalog, allow_capped=False, launches_seats=launches_seats))
    if prompt_cap(launch_model(fallback, parent)) is not None:
        raise RolesError(
            "role 'skill tests' has no uncapped seat for this test: "
            f"{reason}, no runnable model in the catalog is uncapped, "
            f"and the parent {parent.provider}/{parent.model} is capped"
        )
    emit_bounded(fallback, False, estimate, reason, notes + more)
    return 0


def persona_path():
    return Path(__file__).resolve().parents[1] / "agents" / "poteto-agent.md"


def playbook_stems():
    root = Path(__file__).resolve().parents[2]
    rendered_tree = root / "poteto-mode" / "playbooks"
    source_checkout = root / "skills" / "poteto-mode" / "playbooks"
    candidates = (rendered_tree, source_checkout)
    directory = next((candidate for candidate in candidates if candidate.is_dir()), None)
    if directory is None:
        raise RolesError("no playbook directory: " + ", ".join(str(candidate) for candidate in candidates))
    stems = frozenset(
        entry.stem for entry in directory.iterdir() if entry.is_file() and entry.suffix == ".md"
    )
    if not stems:
        raise RolesError(f"{directory}: playbook directory is empty")
    return stems


def load_brief_rules():
    path = persona_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise RolesError(f"{path}: persona not found") from error
    except UnicodeDecodeError as error:
        raise RolesError(f"{path}: persona is not valid UTF-8") from error
    except OSError as error:
        raise RolesError(f"{path}: cannot read persona: {error.strerror}") from error
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise RolesError(f"{path}: persona file has no frontmatter")
    fences = [index for index, line in enumerate(lines) if line.strip() == "---"]
    if len(fences) < 2:
        raise RolesError(f"{path}: persona file has no frontmatter")
    body = "".join(lines[fences[1] + 1:]).strip()
    if not body:
        raise RolesError(f"{path}: persona body is empty")
    return body, playbook_stems()


def playbook_values(text):
    values = []
    for line in text.splitlines():
        match = re.fullmatch(r"Playbook:\s*(\S+)\s*", line)
        if match:
            values.append(match.group(1))
    return values


def playbook_stem(value):
    prefix = "playbooks/"
    suffix = ".md"
    if not value.startswith(prefix) or not value.endswith(suffix):
        return None
    stem = value[len(prefix):-len(suffix)]
    if not stem or "/" in stem or stem in (".", ".."):
        return None
    return stem


def brief_problems(text, persona_body, playbooks):
    problems = []
    collapsed_body = " ".join(persona_body.split())
    collapsed_brief = " ".join(text.split())
    if not collapsed_brief.startswith(collapsed_body):
        problems.append(
            f"missing persona: open the brief with the body of {persona_path()}, without its frontmatter"
        )
    names = ", ".join(sorted(playbooks))
    values = playbook_values(text)
    stem = None
    if not values:
        problems.append(
            "missing playbook: add one line 'Playbook: playbooks/<name>.md', where <name> is one of: " + names
        )
    elif len(values) > 1:
        problems.append("more than one Playbook line: keep one")
    else:
        candidate = playbook_stem(values[0])
        if candidate in playbooks:
            stem = candidate
        else:
            problems.append(f"unknown playbook '{values[0]}': expected one of: {names}")
    problems.extend(mode_problems(text, stem))
    return problems


def segments_match(pattern, path):
    """Whole-segment glob. ** matches zero or more segments. Other segments never cross a slash."""

    def match(pattern_parts, path_parts):
        if not pattern_parts:
            return not path_parts
        if pattern_parts[0] == "**":
            return any(match(pattern_parts[1:], path_parts[index:]) for index in range(len(path_parts) + 1))
        return (
            bool(path_parts)
            and fnmatch.fnmatchcase(path_parts[0], pattern_parts[0])
            and match(pattern_parts[1:], path_parts[1:])
        )

    pattern_parts = [] if pattern == "" else pattern.split("/")
    path_parts = [] if path == "" else path.split("/")
    return match(pattern_parts, path_parts)


def escalated_path(leases, patterns, tracked):
    """First covered path that matches a pattern. Candidates are sorted, so pattern order does not matter."""
    covered = set()
    for lease in leases:
        if lease == "":
            covered.update(tracked)
            continue
        covered.add(lease)
        prefix = lease + "/"
        covered.update(path for path in tracked if path.startswith(prefix))
    for path in sorted(covered):
        if any(segments_match(pattern, path) for pattern in patterns):
            return path
    return None


def tracked_files(cwd):
    """Repository-relative tracked paths. Any git failure means cwd is not a checkout."""
    message = f"--paths needs a git checkout to list tracked files, and {cwd} is not one"

    def git(args):
        try:
            return subprocess.run(args, capture_output=True)
        except OSError as error:
            raise RolesError(message) from error

    top = git(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"])
    lines = top.stdout.decode("utf-8", "surrogateescape").splitlines()
    if top.returncode != 0 or not lines:
        raise RolesError(message)
    listing = git(["git", "-C", lines[0], "ls-files", "-z"])
    if listing.returncode != 0:
        raise RolesError(message)
    parts = listing.stdout.decode("utf-8", "surrogateescape").split("\0")
    if parts and parts[-1] == "":
        del parts[-1]
    return parts


def escalation_reason(durable, hit, send_backs):
    """Durable reason first, then a covered path, then a second send-back."""
    if durable is not None:
        return durable
    if hit is not None:
        return f"lease covers {hit}"
    if send_backs >= 2:
        return "second send-back"
    return None


def escalate(decision, reason):
    """Escalation only moves toward full."""
    if reason is None:
        return decision
    label = f"escalated: {reason}"
    return ModeDecision("full", label, label)


class Waivers(dict):
    """Map (playbook stem, attempt kind) to waived step names, in table order.

    A missing playbook with a known attempt waives nothing. An unknown attempt kind is a KeyError.
    """

    def __missing__(self, key):
        if isinstance(key, tuple) and len(key) == 2 and key[1] in ATTEMPTS:
            return ()
        raise KeyError(key)


_WAIVER_ROWS = {
    "feature": (
        ("Arena", "Interrogate", "Comment Sicko"),
        ("How", "Architect", "Arena", "Interrogate", "Comment Sicko"),
    ),
    "bug-fix": (("Comment Sicko",), ("How", "Why", "Architect", "Comment Sicko")),
    "refactoring": (("Comment Sicko",), ("How", "Architect", "Comment Sicko")),
    "perf-issue": (("Comment Sicko",), ("How", "Architect", "Comment Sicko")),
    "hillclimb": (("Comment Sicko",), ("How", "Comment Sicko")),
    "authoring-a-skill": (
        ("Comment Sicko", "Second-provider test"),
        ("Comment Sicko", "Second-provider test"),
    ),
}
LIGHT_WAIVERS = Waivers({
    (playbook, attempt): first if attempt == "first" else retry
    for playbook, (first, retry) in _WAIVER_ROWS.items()
    for attempt in ATTEMPTS
})


def durable_reason(text):
    if text is None:
        return None
    if text.strip() == "" or "\n" in text or "\r" in text:
        raise ModeSettingsError("--escalated needs a one-line reason")
    return text.strip()


# The colon sits in the match so "Mode source" is not a "Mode" line.
GRAMMAR_LABELS = ("Mode", "Mode source", "Attempt", "Waived by mode", "Gate")
_GRAMMAR_LINE = re.compile("(" + "|".join(map(re.escape, GRAMMAR_LABELS)) + r"):(.*)")


def waived_value(mode, key):
    """Waiver text for one item, or None when the brief carries no such line.

    Full mode returns None and does not read key. An empty light tuple is None.
    """
    if mode != "light":
        return None
    return ", ".join(LIGHT_WAIVERS[key]) or None


def brief_lines(decision, key):
    """Playbook, Mode, Mode source, Attempt, and Waived by mode, each when it applies."""
    lines = [] if key is None else [f"Playbook: playbooks/{key[0]}.md"]
    lines.append(f"Mode: {decision.mode}")
    lines.append(f"Mode source: {decision.label}")
    if key is not None:
        lines.append(f"Attempt: {key[1]}")
        value = waived_value(decision.mode, key)
        if value is not None:
            lines.append(f"Waived by mode: {value}")
    return lines


def _grammar_lines(text):
    """Label to stripped values, in brief order."""
    found = {label: [] for label in GRAMMAR_LABELS}
    for line in text.splitlines():
        match = _GRAMMAR_LINE.fullmatch(line)
        if match:
            found[match.group(1)].append(match.group(2).strip())
    return found


def _waiver_problem(mode, key, expected, actual):
    if expected is None:
        if mode == "full":
            return "unexpected Waived by mode line: Mode: full waives nothing, remove it"
        return (
            f"unexpected Waived by mode line: playbooks/{key[0]}.md "
            f"waives nothing on attempt {key[1]}, remove it"
        )
    if actual is None:
        return f"missing Waived by mode line: add 'Waived by mode: {expected}'"
    return f"wrong Waived by mode line: expected 'Waived by mode: {expected}'"


MISSING_MODE = (
    "missing Mode: paste the lines 'roles.py mode --playbook <name> --attempt <kind>' prints, "
    "which include one line 'Mode: full' or 'Mode: light'"
)


def mode_problems(text, stem):
    lines = _grammar_lines(text)
    if not lines["Mode"]:
        return [MISSING_MODE]
    problems = [
        f"more than one {label} line: keep one"
        for label in GRAMMAR_LABELS
        if len(lines[label]) > 1
    ]
    if len(lines["Mode"]) > 1:
        return problems
    mode = lines["Mode"][0]
    if mode not in MODES:
        problems.append(f"bad Mode value '{mode}': expected {' or '.join(MODES)}")
        return problems

    attempt = None
    attempts = lines["Attempt"]
    if len(attempts) == 1:
        if attempts[0] in ATTEMPTS:
            attempt = attempts[0]
        else:
            problems.append(
                f"bad Attempt value '{attempts[0]}': expected one of: {', '.join(ATTEMPTS)}"
            )
    elif not attempts and mode == "light":
        problems.append(
            "missing Attempt: Mode: light needs one line 'Attempt: <kind>', "
            f"where <kind> is one of: {', '.join(ATTEMPTS)}"
        )

    waived = lines["Waived by mode"]
    if len(waived) > 1:
        return problems
    if mode == "light" and (stem is None or attempt is None):
        return problems
    key = None if mode == "full" else (stem, attempt)
    expected = waived_value(mode, key)
    actual = waived[0] if waived else None
    if actual != expected:
        problems.append(_waiver_problem(mode, key, expected, actual))
    return problems


def work_key(playbook, attempt):
    if (playbook is None) != (attempt is None):
        raise ModeSettingsError("--playbook and --attempt go together: pass both or neither")
    if playbook is None:
        return None
    stems = playbook_stems()
    if playbook not in stems:
        names = ", ".join(sorted(stems))
        raise ModeSettingsError(f"unknown playbook '{playbook}': expected one of: {names}")
    return (playbook, attempt)


def command_mode(args):
    config = merged_config(args.cwd, args.config, args.project_config)
    decision = effective_mode(config, args.brief_mode, args.session_mode, args.coordinator_mode)
    key = work_key(args.playbook, args.attempt)
    durable = durable_reason(args.escalated)
    leases = lease_paths(args.paths) if args.paths is not None else []
    patterns = [canonical(item) for item in (config["escalate"] or [])]
    hit = escalated_path(leases, patterns, tracked_files(args.cwd)) if leases and patterns else None
    print("\n".join(brief_lines(escalate(decision, escalation_reason(durable, hit, args.send_backs)), key)))


def command_check_brief(args):
    path = Path(args.brief)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise RolesError(f"{path}: brief not found") from error
    except UnicodeDecodeError as error:
        raise RolesError(f"{path}: brief is not valid UTF-8") from error
    except OSError as error:
        raise RolesError(f"{path}: cannot read brief: {error.strerror}") from error
    persona_body, playbooks = load_brief_rules()
    problems = brief_problems(text, persona_body, playbooks)
    for problem in problems:
        print(problem)
    if problems:
        return 1
    stem = playbook_stem(playbook_values(text)[0])
    print(f"ok playbooks/{stem}.md")
    return 0


def non_negative_int(text):
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid non-negative int value: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"invalid non-negative int value: {text!r}")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("show", "validate", "write"):
        command = sub.add_parser(name)
        command.add_argument("--cwd", default=os.getcwd())
        command.add_argument("--config", help="user roles file (default ~/.config/pstack-t3/roles.json)")
        if name != "write":
            command.add_argument("--project-config", help="project roles file (default <repo>/.pstack/t3-roles.json)")
        command.add_argument("--catalog", required=name != "show", help="saved orchestrator_capabilities JSON, or - for stdin")
        command.add_argument("--parent", help="this thread's provider/model from orchestrator_capabilities (inheritedProviderInstanceId/inheritedModel)")
    sub.choices["show"].add_argument("--role", action="append")
    write = sub.choices["write"]
    write.add_argument("--budget", choices=list(BUDGETS))
    write.add_argument("--mode", choices=MODES)
    group = write.add_mutually_exclusive_group()
    # None means the flag was omitted. An empty list would replace a stored project list.
    group.add_argument("--escalate", action="append", default=None)
    group.add_argument("--clear-escalate", action="store_true")
    write.add_argument("--set", action="append", help="'<role>=<seat>[;<seat>]', seat = inherit | provider/model[?option=value]")
    write.add_argument("--project", action="store_true", help="write the project file instead of the user file")
    write.add_argument("--keep", action="store_true", help="keep roles already in the target file")
    write.add_argument("--force", action="store_true", help="write even if seats do not match the catalog")
    bounded = sub.add_parser("bounded-seat")
    bounded.add_argument("--cwd", default=os.getcwd())
    bounded.add_argument("--config", help="user roles file (default ~/.config/pstack-t3/roles.json)")
    bounded.add_argument("--project-config", help="project roles file (default <repo>/.pstack/t3-roles.json)")
    bounded.add_argument("--catalog", required=True, help="saved orchestrator_capabilities JSON, or - for stdin")
    bounded.add_argument("--parent", required=True, help="this thread's provider/model from orchestrator_capabilities (inheritedProviderInstanceId/inheritedModel)")
    bounded.add_argument("--brief", required=True, help="brief file whose bytes are counted toward the cap")
    bounded.add_argument("--read", action="append", help="file counted toward the estimate")
    bounded.add_argument("--launches-seats", action="store_true",
                         help="skip cursor provider seats; the child will launch seats")
    mode = sub.add_parser("mode")
    mode.add_argument("--cwd", default=os.getcwd())
    mode.add_argument("--config", help="user roles file (default ~/.config/pstack-t3/roles.json)")
    mode.add_argument("--project-config", help="project roles file (default <repo>/.pstack/t3-roles.json)")
    mode.add_argument("--paths", help="comma-separated leases")
    mode.add_argument("--send-backs", type=non_negative_int, default=0,
                      help="send-back count; 2 or more forces full mode")
    mode.add_argument("--escalated",
                      help="recorded one-line escalation reason; forces full mode and wins over paths and send-backs")
    mode.add_argument("--playbook", help="playbook stem, such as bug-fix")
    mode.add_argument("--attempt", choices=ATTEMPTS)
    for command in (sub.choices["show"], bounded, mode):
        command.add_argument("--brief-mode", choices=MODES,
                             help="brief mode; overrides session, coordinator, project, and user modes")
        command.add_argument("--session-mode", choices=MODES,
                             help="session mode; used after brief and before coordinator, project, and user modes")
        command.add_argument("--coordinator-mode", choices=MODES,
                             help="coordinator mode; used after brief and session, before project and user modes")
    check_brief = sub.add_parser("check-brief")
    check_brief.add_argument("brief", help="brief file to check")
    args = parser.parse_args(argv)
    if args.command == "write" and not args.project and (args.escalate is not None or args.clear_escalate):
        parser.error("--escalate and --clear-escalate require --project")
    try:
        return {
            "show": command_show,
            "validate": command_validate,
            "write": command_write,
            "bounded-seat": command_bounded_seat,
            "check-brief": command_check_brief,
            "mode": command_mode,
        }[args.command](args) or 0
    except ModeSettingsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except RolesError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
