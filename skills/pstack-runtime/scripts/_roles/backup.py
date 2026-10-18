"""The backup command: one relaunch seat, a panel, or a park."""


import json
import sys
from dataclasses import replace


from _roles.base import AUTHOR_CHECKED_ROLES, Backup, INHERIT, PANEL_BACKUP_ROLE, PANEL_GATE_ROLE, PANEL_RULE, REVIEW_ROLES, ROLES, RolesError
from _roles.commands import load_show_catalog
from _roles.config import allow_for, backups_for, given_parent, given_runtime_mode, merged_config
from _roles.mode import effective_mode, seat_budget
from _roles.panel import _backup_panel, _catalog_pair, _emit_backup, author_families, usage_limited
from _roles.problems import backup_problems
from _roles.resolve import configured_seats
from _roles.seats import exclude_reason, family, for_runtime_mode, opencode_allow_lines


def backup_seat(role, failed, text, catalog, budget, out, authors, resume=False, review_backups=None, backups=(), exclude=(), *, panel_budget):
    """Pick the one backup seat, a review backups panel, or park.

    The candidates are the role's seats in the backups key, in order. A
    candidate is skipped when its provider is the failed provider, is already
    out, or cannot resolve through _catalog_pair, when the exclude list refuses
    it, and, for a review role, when its model family wrote the diff.
    With resume, the original seat comes back first when its provider is not out.
    review_backups is configured_seats' (seats, notes) for that role, or None when it is unset.
    panel_budget is the budget of the review backups seats.
    """
    provider_id = failed["provider"]
    model_id = failed["model"]
    label = f"{provider_id}/{model_id}"
    out = set(out or ())
    blocked = {provider_id} | out
    if resume:
        reviews_itself = role in AUTHOR_CHECKED_ROLES and family(model_id) in author_families(authors)
        if reviews_itself and role == PANEL_BACKUP_ROLE:
            report = f"{role}: {label} is in an author's family, so it does not resume and counts as no pass"
            return Backup("park", report)
        original = None if reviews_itself else _catalog_pair(catalog, provider_id, model_id, out)
        if original is not None:
            return _emit_backup(role, label, *original, {}, failed.get("options"), budget, True, ())
    elif not usage_limited(text):
        report = f"{role}: {label} failed without a usage limit; respawn per Failure handling"
        return Backup("not-usage-limit", report)
    skip = author_families(authors) if role in REVIEW_ROLES else set()
    for candidate in backups_for(role, backups):
        if family(candidate["model"]) in skip:
            continue
        if exclude_reason(candidate, exclude, role) is not None:
            continue
        chosen = _catalog_pair(catalog, candidate["providerInstanceId"], candidate["model"], blocked)
        if chosen is None:
            continue
        try:
            return _emit_backup(
                role, label, *chosen, candidate.get("options") or {}, failed.get("options"), budget, resume, exclude
            )
        except RolesError:
            continue
    if role == PANEL_BACKUP_ROLE:
        report = f"{role}: {label} hit its usage limit; a review backups seat has no backup, so it counts as no pass"
        return Backup("park", report)
    if role == PANEL_GATE_ROLE and review_backups is not None:
        return _backup_panel(role, label, review_backups, catalog, panel_budget, blocked, authors, resume, exclude)
    if resume:
        report = f"{role}: {label} is still out after the reset; no backup seat, so the work waits for the reset"
    else:
        report = f"{role}: {label} hit its usage limit; no backup seat, so the work waits for the reset"
    return Backup("park", report)


def parse_applied_options(text):
    """Options from t3_thread_configuration, or a target's options object."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise RolesError(f"--options: invalid JSON: {error}") from error
    if isinstance(data, dict):
        return data
    if not isinstance(data, list):
        raise RolesError("--options must be a JSON object or an array of id and value")
    options = {}
    for item in data:
        if not isinstance(item, dict) or "id" not in item or "value" not in item:
            raise RolesError("--options array entries need id and value")
        options[item["id"]] = item["value"]
    return options


def command_backup(args):
    if args.catalog == "-":
        raise RolesError("--catalog - is refused because stdin carries the error text")
    given_parent(args)
    mode = given_runtime_mode(args)
    if args.provider == INHERIT:
        raise RolesError("backup refuses provider 'inherit'. Read the seat with t3_thread_configuration")
    if args.role not in ROLES:
        raise RolesError(f"unknown role {args.role!r}")
    authors = list(args.author or [])
    if args.role in REVIEW_ROLES and not authors:
        raise RolesError(f"role {args.role!r} needs --author, the model whose work it judges")
    if args.role == PANEL_BACKUP_ROLE and args.resume and not authors:
        raise RolesError(f"role {args.role!r} needs --author with --resume, the model whose work it judges")
    text = "" if args.resume else sys.stdin.read()
    config = merged_config(args.cwd, args.config, args.project_config)
    exclude = config.get("exclude", ())
    decision = effective_mode(config, args.brief_mode, args.session_mode, args.coordinator_mode)
    budget = seat_budget(config["budget"], decision.mode, args.role)
    panel_budget = seat_budget(config["budget"], decision.mode, PANEL_BACKUP_ROLE)
    _catalog_path, catalog = load_show_catalog(args)
    if catalog is None:
        raise RolesError("backup needs a catalog. Pass --catalog, or save one with setup-pstack")
    catalog = for_runtime_mode(catalog, mode)
    backups = config.get("backups", {})
    if args.role in backups:
        problems = backup_problems({args.role: backups[args.role]}, catalog, require_runnable=False)
        if problems:
            raise RolesError("the roles file's backups key has a problem:\n" + "\n".join(problems))
    options = parse_applied_options(args.options) if args.options else {}
    failed = {"provider": args.provider, "model": args.model, "options": options}
    review_backups = configured_seats(config, PANEL_BACKUP_ROLE, exclude) if PANEL_BACKUP_ROLE in config["roles"] else None
    result = backup_seat(
        args.role, failed, text, catalog, budget, args.out or [], authors, args.resume, review_backups,
        backups, exclude,
        panel_budget=panel_budget,
    )
    kept, _skipped = configured_seats(config, args.role, exclude)
    if kept:
        extra = opencode_allow_lines({args.role: kept}, allow_for(config, args.role), catalog)
        if extra:
            result = replace(result, notes=(*result.notes, *extra))
    if result.decision == "panel" and review_backups is not None:
        panel_notes = opencode_allow_lines(
            {PANEL_BACKUP_ROLE: result.seats},
            allow_for(config, PANEL_BACKUP_ROLE),
            catalog,
        )
        if panel_notes:
            result = replace(result, notes=(*result.notes, *panel_notes))
    payload = {
        "decision": result.decision,
        "role": args.role,
        "failed": f"{args.provider}/{args.model}",
    }
    if result.seat is not None:
        payload["seat"] = result.seat
    if result.seats is not None:
        payload["seats"] = list(result.seats)
        payload["rule"] = PANEL_RULE
    if result.notes:
        payload["notes"] = list(result.notes)
    payload["report"] = result.report
    print(json.dumps(payload, indent=2))
    return 0


__all__ = [
    "backup_seat",
    "parse_applied_options",
    "command_backup",
]
