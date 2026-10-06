#!/usr/bin/env python3
"""Resolve pstack-t3 roles into T3 delegate_task targets.

Roles map a pstack role name to a list of seats. A seat is "inherit" or a
target {"providerInstanceId", "model", "options"} drawn from the catalog that
T3's orchestrator_capabilities tool returns.
"""

import argparse
import json
import os
import re
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
BUDGETS = {"default": None, "small": "medium", "medium": "high", "large": "xhigh", "unlimited": "max-available"}
EFFORT_IDS = ("effort", "reasoningEffort", "reasoning_effort", "reasoning")
LADDER = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4, "xhigh": 5, "extra-high": 5, "extra_high": 5, "max": 6, "ultra": 7}
SPECIAL = {"ultracode", "ultrathink"}
INHERIT = "inherit"
SMALL_TIER = frozenset({"haiku", "mini", "nano", "flash", "lite", "fast", "small", "luna"})
CATALOG_REQUIRED = "catalog-required"
DEFAULT_PANEL = "default-panel"


class RolesError(Exception):
    pass


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


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as error:
        raise RolesError(f"{path}: invalid JSON: {error}") from error


def load_catalog(path):
    data = json.load(sys.stdin) if str(path) == "-" else load_json(path)
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


def check_shape(config, origin):
    if not isinstance(config, dict):
        raise RolesError(f"{origin}: expected an object")
    budget = config.get("budget", "default")
    if budget not in BUDGETS:
        raise RolesError(f"{origin}: budget {budget!r} is not one of {', '.join(BUDGETS)}")
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
    budget = project["budget"] if "budget" in project else user.get("budget", "default")
    return {"budget": budget, "roles": roles, "sources": sources}


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
    ceiling = max(rank(value) for value in values) if cap == "max-available" else rank(cap)
    allowed = [value for value in values if rank(value) <= ceiling] or [min(values, key=rank)]
    current = (seat.get("options") or {}).get(option["id"])
    if current in values and rank(current) <= ceiling:
        chosen = current
    else:
        chosen = max(allowed, key=rank)
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


def skill_tests_seat(catalog):
    """One bare seat. Prefer another family, then a small-tier id, then a lower default effort."""
    parent = catalog.get("inheritedModel")
    parent_family = family(parent) if parent else None
    rows = []
    for provider_index, provider in enumerate(catalog["providers"]):
        if not runnable(provider):
            continue
        for model_index, model in enumerate(models_of(provider)):
            model_id = model["id"]
            other = parent_family is None or family(model_id) != parent_family
            small = bool(set(model_tokens(model_id)) & SMALL_TIER)
            rows.append((0 if other else 1, 0 if small else 1, default_effort_rank(model), provider_index, model_index, provider, model))
    if not rows:
        return INHERIT
    *_, provider, model = min(rows)
    return {"providerInstanceId": provider["providerInstanceId"], "model": model["id"]}


def _runnable_rows(catalog):
    rows = []
    for provider in catalog["providers"]:
        if not runnable(provider):
            continue
        for model in models_of(provider):
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


def _preferred_seat(preference, catalog, budget="default"):
    """Return a concrete runnable target and explanations of changed intent."""
    rows = _runnable_rows(catalog)
    if not rows:
        raise RolesError("no provider in the catalog can run child tasks")
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
            if parent_model is not None:
                provider, model = parent, parent_model
            else:
                provider, model = rows[0]
            cause = "missing family"
    # xhigh is the built-in ceiling. unlimited replaces it with the model's highest non-special level.
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


def _verifier_seats(catalog):
    """One inherit seat for this thread, then one seat per new family. One seat is repeated to three."""
    parent = catalog.get("inheritedProviderInstanceId")
    parent_model = catalog.get("inheritedModel")
    parent_runs = runnable(providers_by_id(catalog).get(parent)) if parent else False
    seats, families = [], set()
    if parent_runs and parent_model:
        seats.append(INHERIT)
        families.add(family(parent_model))
    for provider in catalog["providers"]:
        if not runnable(provider) or (parent_runs and provider["providerInstanceId"] == parent):
            continue
        model = models_of(provider)[0]["id"]
        if family(model) in families:
            continue
        families.add(family(model))
        seats.append({"providerInstanceId": provider["providerInstanceId"], "model": model})
    if len(seats) == 1:
        return seats * 3
    if not seats:
        raise RolesError("no provider in the catalog can run child tasks")
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
        return DefaultSelection((skill_tests_seat(catalog),))
    if policy is AdaptiveDefault.VERIFIERS:
        return DefaultSelection(tuple(_verifier_seats(catalog)))
    seats, notes = [], []
    for number, preference in enumerate(policy, 1):
        seat, seat_notes = _preferred_seat(preference, catalog, budget)
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


def resolve_seat(seat, catalog, budget):
    """Return (resolved seat, notes, problems). Problems are seats the catalog rejects."""
    if seat == INHERIT:
        value, note = inherit_with_budget(catalog, budget)
        return value, [{"info": note}] if note else [], []
    provider = providers_by_id(catalog).get(seat["providerInstanceId"])
    if not runnable(provider):
        reason = "; ".join(provider.get("constraints") or []) if provider else "not in catalog"
        note = f"{seat['providerInstanceId']} is not runnable ({reason}); seat inherits the parent"
        value, budget_note = inherit_with_budget(catalog, budget)
        return value, [note] + ([{"info": budget_note}] if budget_note else []), [note]
    notes, problems = [], []
    model = find_model(provider, seat["model"])
    if model is None:
        model = models_of(provider)[0]
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
    return apply_budget(seat, model, budget), notes, problems


def resolve(config, catalog=None, names=None):
    names = names or ROLES
    result = {"budget": config["budget"], "catalog": bool(catalog), "roles": {}}
    for name in names:
        if name not in ROLES:
            raise RolesError(f"unknown role {name!r}")
        configured = config["roles"].get(name)
        entry = {"source": config["sources"].get(name, "default")}
        if configured is None:
            selection = default_seats(name, catalog, config["budget"])
            if isinstance(selection.seats, str):
                entry["seats"] = selection.seats
                if selection.notes:
                    entry["note"] = selection.notes[0]
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
                value, seat_notes, _ = resolve_seat(seat, catalog, config["budget"])
                resolved.append(value)
                notes.extend(seat_notes)
            entry["seats"] = resolved
            info = [note["info"] for note in notes if isinstance(note, dict)]
            problems = selection_notes + [note for note in notes if not isinstance(note, dict)]
            if problems:
                entry["notes"] = problems
            if info:
                entry["info"] = info
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
            _, _, seat_problems = resolve_seat(seat, catalog, config["budget"])
            problems.extend(f"{name}: {problem}" for problem in seat_problems)
    return problems


def command_show(args):
    config = merged_config(args.cwd, args.config, args.project_config)
    catalog_path = args.catalog or (snapshot_path() if snapshot_path().is_file() else None)
    catalog = load_catalog(catalog_path) if catalog_path else None
    if catalog is not None and (args.parent or not args.catalog):
        # A saved snapshot records whichever thread ran setup, not this one.
        provider, _, model = (args.parent or "").partition("/")
        catalog = {**catalog, "inheritedProviderInstanceId": provider or None, "inheritedModel": model or None}
    print(json.dumps(resolve(config, catalog, [args.role] if args.role else None), indent=2))


def command_validate(args):
    config = merged_config(args.cwd, args.config, args.project_config)
    problems = validate(config, load_catalog(args.catalog))
    for problem in problems:
        print(problem)
    if not problems:
        print("ok")
    return 1 if problems else 0


def command_write(args):
    catalog = load_catalog(args.catalog)
    target = project_config_path(args.cwd) if args.project else (Path(args.config) if args.config else user_config_path())
    existing = check_shape(load_json(target) or {}, target) if args.keep else {}
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
    check_shape(config, target)
    problems = validate({"budget": config.get("budget", "default"), "roles": roles, "sources": {}}, catalog)
    if problems and not args.force:
        raise RolesError("refusing to write; these seats do not match the catalog:\n" + "\n".join(problems))
    write_atomic(target, config)
    if not args.project and not args.config:
        write_atomic(snapshot_path(), catalog)
    print(f"wrote {target}")


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
    sub.choices["show"].add_argument("--role")
    sub.choices["show"].add_argument("--parent", help="this thread's provider/model from orchestrator_capabilities (inheritedProviderInstanceId/inheritedModel)")
    write = sub.choices["write"]
    write.add_argument("--budget", choices=list(BUDGETS))
    write.add_argument("--set", action="append", help="'<role>=<seat>[;<seat>]', seat = inherit | provider/model[?option=value]")
    write.add_argument("--project", action="store_true", help="write the project file instead of the user file")
    write.add_argument("--keep", action="store_true", help="keep roles already in the target file")
    write.add_argument("--force", action="store_true", help="write even if seats do not match the catalog")
    args = parser.parse_args(argv)
    try:
        return {"show": command_show, "validate": command_validate, "write": command_write}[args.command](args) or 0
    except RolesError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
