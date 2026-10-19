"""Exclude and backup problems, and config validation."""


from _roles.base import INHERIT, PANEL_BACKUP_ROLE, RolesError
from _roles.config import inherit_parent
from _roles.panel import panel_shortfall, panel_verdicts
from _roles.resolve import cursor_note, resolve_seat
from _roles.seats import check_option_values, exclude_label, exclude_reason, find_model, model_matches, models_of, options_of, provider_matches, providers_by_id, runnable


def excluded_problems(roles, exclude=()):
    return [
        f"{name}: {seat['providerInstanceId']}/{seat['model']}: {reason}"
        for name, seats in roles.items()
        for seat in seats
        if isinstance(seat, dict) and (reason := exclude_reason(seat, exclude, name))
    ]


def excluded_backup_problems(backups, exclude=()):
    return [
        f"backups.{name}: {seat['providerInstanceId']}/{seat['model']}: {reason}"
        for name, seats in backups.items()
        for seat in seats
        if (reason := exclude_reason(seat, exclude, name))
    ]


def backup_option_problems(name, seat, catalog, require_runnable=True):
    """Problems for one backups seat, each naming backups.<name>. With require_runnable False a seat the catalog lacks is no problem."""
    where = f"backups.{name}: {seat['providerInstanceId']}/{seat['model']}"
    provider = providers_by_id(catalog).get(seat["providerInstanceId"]) if catalog else None
    model = find_model(provider, seat["model"]) if runnable(provider) else None
    if model is None:
        return [f"{where}: not runnable or not in the catalog"] if require_runnable else []
    known = {option["id"] for option in options_of(model)}
    problems = [
        f"{where}: option {key} is not in the catalog"
        for key in (seat.get("options") or {})
        if key not in known
    ]
    problems.extend(f"{where}: {problem}" for problem in check_option_values(seat, model))
    return problems


def backup_problems(backups, catalog, exclude=(), require_runnable=True):
    """Problems for the backups key. An excluded seat is one, and a seat the catalog lacks is one unless require_runnable is False."""
    problems = []
    for name, seats in backups.items():
        for seat in seats:
            where = f"backups.{name}: {seat['providerInstanceId']}/{seat['model']}"
            reason = exclude_reason(seat, exclude, name)
            if reason is not None:
                problems.append(f"{where}: {reason}")
                continue
            problems.extend(backup_option_problems(name, seat, catalog, require_runnable))
    return problems


def exclude_option_problems(number, rule, catalog):
    """Problems for one exclude rule's options, against the catalog models the rule names.

    A rule whose provider or model glob matches no catalog model is not checked,
    because a later catalog may add the model.
    """
    if catalog is None or "options" not in rule:
        return []
    candidates = [
        model
        for provider in catalog.get("providers") or []
        for model in models_of(provider)
        if ("provider" not in rule or provider_matches(rule["provider"], provider.get("providerInstanceId")))
        and ("model" not in rule or model_matches(rule["model"], model["id"]))
    ]
    if not candidates:
        return []
    problems = {
        f"exclude entry {number} ({exclude_label(rule)}): option {key}={value!r} matches no catalog option"
        for key, value in rule["options"].items()
        if not any(
            any(option["id"] == key for option in options_of(model))
            and not check_option_values({"options": {key: value}}, model)
            for model in candidates
        )
    }
    return sorted(problems)


def validate(config, catalog, exclude=(), backups=None):
    backups = backups or {}
    problems = excluded_problems(config["roles"], exclude)
    problems.extend(backup_problems(backups, catalog, exclude))
    for number, rule in enumerate(exclude, 1):
        problems.extend(exclude_option_problems(number, rule, catalog))
    skill_seats = None
    for name, seats in config["roles"].items():
        seats = [seat for seat in seats if exclude_reason(seat, exclude, name) is None]
        if name == PANEL_BACKUP_ROLE:
            verdicts = panel_verdicts(seats, catalog, config["budget"], exclude=exclude)
            problems.extend(f"{name}: {problem}" for verdict in verdicts for problem in verdict.problems)
            shortfall = panel_shortfall([verdict.seat for verdict in verdicts if verdict.seat is not None])
            if shortfall is not None:
                problems.append(f"{name}: {shortfall}")
            continue
        resolved = []
        for seat in seats:
            try:
                value, _, seat_problems = resolve_seat(seat, catalog, config["budget"], name, exclude)
            except RolesError as error:
                problems.append(f"{name}: {error}")
                continue
            resolved.append(value)
            problems.extend(f"{name}: {problem}" for problem in seat_problems)
        if name == "skill tests":
            skill_seats = resolved
    line = cursor_note("skill tests", skill_seats if skill_seats is not None else [INHERIT], catalog, inherit_parent(catalog))
    if line:
        problems.append(line)
    return problems


__all__ = [
    "excluded_problems",
    "excluded_backup_problems",
    "backup_option_problems",
    "backup_problems",
    "exclude_option_problems",
    "validate",
]
