"""Seat settling and role resolution."""


from _roles.base import BUDGETS, CATALOG_REQUIRED, DefaultSelection, EXCLUDE_RULE, HAIKU_BRIEF, INHERIT, PANEL_BACKUP_ROLE, PANEL_CATALOG_NOTE, ROLES, RUNTIME_MODE_GAP, RolesError, UNSET_NOTE, UNSET_ROLE_NOTE
from _roles.config import DEFAULT_MODE, OpenCodeAllow, allow_for, inherit_parent, stamp_parent
from _roles.mode import seat_budget
from _roles.seats import allow_refuses, apply_budget, check_option_values, effort_option, exclude_reason, find_model, models_of, normalized_bare, opencode_allow_lines, options_of, providers_by_id, runnable, runs_on_cursor, search_hints
from _roles import seam


def _first_pickable(provider, catalog, allow, exclude=(), role=None, prefer=None):
    """The provider's first model both lists allow, preferring one id, or None."""
    candidates = [
        model
        for model in models_of(provider)
        if exclude_reason(
            {"providerInstanceId": provider["providerInstanceId"], "model": model["id"]}, exclude, role
        ) is None
        and not allow_refuses(catalog, provider["providerInstanceId"], model["id"], allow)
    ]
    if prefer is not None:
        for model in candidates:
            if model["id"] == prefer:
                return model
    return next(iter(candidates), None)


def role_allow(config, name):
    """The allow list a role's substitutes answer to.

    A configured role uses the list from the file that configures it. A role set
    in no file uses every merged list, because no file names its seat.
    """
    if config["sources"].get(name):
        return allow_for(config, name)
    merged = OpenCodeAllow.empty()
    for allow in (config.get("allowByFile") or {}).values():
        merged = merged.add_all(allow.models)
    return merged


def _no_pick_detail(provider, catalog, allow, exclude, role):
    allowed = [
        model["id"]
        for model in models_of(provider)
        if exclude_reason(
            {"providerInstanceId": provider["providerInstanceId"], "model": model["id"]}, exclude, role
        ) is None
    ]
    if not allowed:
        return f"{provider['providerInstanceId']} has no model the exclude list allows for {role!r}; {EXCLUDE_RULE}"
    shown = ", ".join(allowed[:3])
    if len(allowed) > 3:
        shown += f" and {len(allowed) - 3} more"
    pronoun = "it" if len(allowed) == 1 else "them"
    return (
        f"{provider['providerInstanceId']} has no model the exclude list and the allow list allow "
        f"for {role!r}; the exclude list allows {shown}, and the allow list does not name {pronoun}. "
        f"Add one with --allow-opencode {allowed[0]}"
    )


def default_seats(name):
    """An unset role inherits this thread's seat, except review backups, which is unset."""
    if name == PANEL_BACKUP_ROLE:
        return DefaultSelection("unset", (UNSET_NOTE,))
    return DefaultSelection(INHERIT, (UNSET_ROLE_NOTE,))


def inherit_replacement(catalog, budget, name, exclude=(), allow=None):
    """The seat that replaces an inherited parent the exclude list refuses, or None when it does not.

    It returns None when there is no parent, or the parent is not refused.
    """
    parent = inherit_parent(catalog)
    if parent is None:
        return None
    provider = providers_by_id(catalog).get(parent.provider)
    parent_seat = {"providerInstanceId": parent.provider, "model": parent.model}
    reason = exclude_reason(parent_seat, exclude, name)
    if reason is None:
        return None
    if provider is None or not provider.get("canRunChildTask"):
        detail = "; ".join((provider or {}).get("constraints") or []) or "cannot run child tasks"
        raise RolesError(
            f"role {name!r} cannot inherit {parent.provider}/{parent.model}: {reason}; "
            f"{parent.provider} is not runnable ({detail})"
        )
    model = _first_pickable(provider, catalog, allow, exclude, name, parent.model) if runnable(provider) else None
    if model is None:
        raise RolesError(
            f"role {name!r} cannot inherit {parent.provider}/{parent.model}: {reason}; "
            + _no_pick_detail(provider, catalog, allow, exclude, name)
        )
    seat = finish_seat({"providerInstanceId": parent.provider, "model": model["id"]}, model, budget, exclude, name)
    return seat, [f"inherit replaced by {parent.provider}/{model['id']}: {reason}"]


def _resolve_inherit(catalog, budget, name, exclude=(), allow=None):
    """Settle an inherit seat and return resolve_seat's (seat, notes) shape.

    A parent the exclude list refuses becomes the same provider's first allowed
    model, or the role is refused when that provider cannot run children or has
    no allowed model, per inherit_replacement. A parent whose provider carries
    RUNTIME_MODE_GAP is refused. A budget other than "default" makes inherit
    explicit on the parent's own model when that model declares an effort option
    and its provider can run children.
    """
    parent = inherit_parent(catalog)
    if parent is None:
        return INHERIT, []
    provider = providers_by_id(catalog).get(parent.provider)
    gap = (provider or {}).get(RUNTIME_MODE_GAP)
    if gap:
        raise RolesError(
            f"role {name!r} cannot inherit {parent.provider}/{parent.model}: "
            f"{parent.provider} is not runnable ({gap})"
        )
    replacement = inherit_replacement(catalog, budget, name, exclude, allow)
    if replacement is not None:
        return replacement
    parent_seat = {"providerInstanceId": parent.provider, "model": parent.model}
    model = find_model(provider, parent.model) if provider else None
    if model is None:
        return INHERIT, []
    if BUDGETS[budget] is not None and effort_option(model) is not None and runnable(provider):
        seat = finish_seat(parent_seat, model, budget, exclude, name)
        return seat, [{"info": f"inherit made explicit as {parent.provider}/{parent.model} so the {budget} budget applies"}]
    return INHERIT, []


def settle_options(seat, model):
    """(seat, notes, problems) after dropping options the catalog does not offer."""
    notes, problems = [], []
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
    return seat, notes, problems


def resolve_seat(seat, catalog, budget, name, exclude=(), allow=None):
    """Return (resolved seat, notes, problems). Problems are seats the catalog rejects."""
    if seat == INHERIT:
        value, notes = _resolve_inherit(catalog, budget, name, exclude, allow)
        return value, notes, []
    provider = providers_by_id(catalog).get(seat["providerInstanceId"])
    if not runnable(provider):
        reason = "; ".join(provider.get("constraints") or []) if provider else "not in catalog"
        note = f"{seat['providerInstanceId']} is not runnable ({reason}); seat inherits the parent"
        value, inherit_notes = _resolve_inherit(catalog, budget, name, exclude, allow)
        return value, [note] + inherit_notes, [note]
    notes, problems = [], []
    model = find_model(provider, seat["model"])
    if model is None:
        parent = inherit_parent(catalog)
        prefer = None
        if parent is not None and parent.provider == provider["providerInstanceId"]:
            prefer = parent.model
        model = _first_pickable(provider, catalog, allow, exclude, name, prefer)
        if model is None:
            raise RolesError(_no_pick_detail(provider, catalog, allow, exclude, name))
        note = f"{seat['providerInstanceId']}/{seat['model']} is not in the catalog; using {model['id']}"
        notes.append(note)
        problems.append(note)
        seat = {**seat, "providerInstanceId": provider["providerInstanceId"], "model": model["id"]}
    seat, option_notes, option_problems = settle_options(seat, model)
    notes.extend(option_notes)
    problems.extend(option_problems)
    seat = finish_seat(seat, model, budget, exclude, name)
    return seat, notes, problems


def finish_seat(seat, model, budget, exclude=(), role=None):
    """Apply the budget to an explicit seat, then raise when the exclude list refuses the result."""
    seat = apply_budget(seat, model, budget)
    reason = exclude_reason(seat, exclude, role)
    if reason is not None:
        raise RolesError(f"{seat['providerInstanceId']}/{seat['model']}: {reason}")
    return seat


def _seats_include_inherit(seats):
    if seats == INHERIT:
        return True
    return isinstance(seats, list) and any(seat == INHERIT for seat in seats)


def settle_inherit_catalog(name, seats, catalog, parent, exclude=()):
    """When inherit needs a catalog to settle a refused parent, report catalog-required."""
    if catalog is None and parent is not None and _seats_include_inherit(seats):
        seat = {"providerInstanceId": parent.provider, "model": parent.model}
        reason = exclude_reason(seat, exclude, name)
        if reason is not None:
            return CATALOG_REQUIRED, (
                f"the parent {parent.provider}/{parent.model} {reason}, so inherit needs the catalog: "
                f"call orchestrator_capabilities and rerun roles.py show --catalog"
            )
    return seats, None


def _store_settled(entry, name, catalog, parent, budget, exclude=(), allow=None):
    """Settle the entry's seats. An inherit seat whose parent the exclude list refuses becomes a real seat."""
    seats = entry["seats"]
    if catalog is not None and _seats_include_inherit(seats):
        replacement = inherit_replacement(catalog, budget, name, exclude, allow)
        if replacement is not None:
            entry["seats"], notes = replacement
            entry["notes"] = [*entry.get("notes", []), *notes]
            if entry.get("note") == UNSET_ROLE_NOTE:
                del entry["note"]
            return
    seats, note = settle_inherit_catalog(name, seats, catalog, parent, exclude)
    entry["seats"] = seats
    if note:
        entry["note"] = note


def runs_haiku_55(seat, parent):
    if seat == INHERIT:
        return parent is not None and normalized_bare(parent.model) == "claude-haiku-5-5"
    if isinstance(seat, dict):
        return normalized_bare(seat.get("model", "")) == "claude-haiku-5-5"
    return False


def attach_haiku_brief(entry, seats, parent):
    if seats == INHERIT:
        if runs_haiku_55(INHERIT, parent):
            entry["haikuBrief"] = list(HAIKU_BRIEF)
        return
    if isinstance(seats, list) and any(runs_haiku_55(seat, parent) for seat in seats):
        entry["haikuBrief"] = list(HAIKU_BRIEF)


def cursor_note(name, seats, catalog, parent):
    """The skill tests sentence when a resolved seat runs on the cursor driver."""
    if name != "skill tests" or catalog is None:
        return None
    if seats in (INHERIT, CATALOG_REQUIRED, "unset"):
        candidates = [INHERIT] if seats == INHERIT else []
    elif isinstance(seats, list):
        candidates = seats
    else:
        candidates = []
    for seat in candidates:
        if seat == INHERIT and parent is None:
            continue
        provider_id = seam.launch_provider(seat, parent)
        if not runs_on_cursor(catalog, provider_id):
            continue
        model_id = seat["model"] if isinstance(seat, dict) else (parent.model if parent else "")
        return f"skill tests: {provider_id}/{model_id} runs on the cursor driver, which cannot launch seats"
    return None


def finish_entry(entry, name, catalog, parent, budget, exclude, allow=None):
    _store_settled(entry, name, catalog, parent, budget, exclude, allow)
    attach_haiku_brief(entry, entry["seats"], parent)
    line = cursor_note(name, entry["seats"], catalog, parent)
    if line:
        entry["notes"] = [*entry.get("notes", []), line]


def configured_seats(config, name, exclude=()):
    """Return configured seats without excluded seats and a note for each skip.

    Return None for seats when no configured seat remains.
    """
    seats = config["roles"].get(name)
    if seats is None:
        return None, []
    kept, notes = [], []
    for seat in seats:
        reason = exclude_reason(seat, exclude, name)
        if reason is None:
            kept.append(seat)
        else:
            notes.append(f"skipped configured seat {seat['providerInstanceId']}/{seat['model']}: {reason}")
    return (kept or None), notes


def resolve(config, catalog=None, names=None, parent=None, launches_seats=False):
    names = names or ROLES
    decision = config.get("mode", DEFAULT_MODE)
    exclude = config.get("exclude", ())
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
        budget = seat_budget(config["budget"], decision.mode, name)
        allow = role_allow(config, name)
        configured, skipped = configured_seats(config, name, exclude)
        if configured is None and name == PANEL_BACKUP_ROLE and name in config["roles"]:
            configured = []
        entry = {"source": config["sources"].get(name, "default") if configured is not None else "default"}
        from_file = False
        if configured is None:
            selection = default_seats(name)
            entry["seats"] = selection.seats
            if skipped:
                entry["notes"] = skipped
            if selection.notes:
                entry["note"] = selection.notes[0]
            finish_entry(entry, name, catalog, parent, budget, exclude, allow)
            result["roles"][name] = entry
            continue
        seats = configured
        selection_notes = skipped
        from_file = True
        if name == PANEL_BACKUP_ROLE and catalog is None:
            entry["seats"] = CATALOG_REQUIRED
            entry["note"] = PANEL_CATALOG_NOTE
            if selection_notes:
                entry["notes"] = selection_notes
        elif catalog is None:
            entry["seats"] = seats
            if selection_notes:
                entry["notes"] = selection_notes
        elif name == PANEL_BACKUP_ROLE:
            verdicts = seam.panel_verdicts(seats, catalog, budget, exclude=exclude)
            entry["seats"] = [verdict.seat for verdict in verdicts if verdict.seat is not None]
            panel_notes = selection_notes + [note for verdict in verdicts for note in verdict.notes]
            shortfall = seam.panel_shortfall(entry["seats"])
            if shortfall is not None:
                panel_notes.append(shortfall)
            if panel_notes:
                entry["notes"] = panel_notes
        else:
            resolved, notes = [], []
            for seat in seats:
                value, seat_notes, _ = resolve_seat(seat, catalog, budget, name, exclude, allow)
                if launches_seats and name == "skill tests" and runs_on_cursor(catalog, seam.launch_provider(value, parent)):
                    raise RolesError(seam.launch_refusal(f"the seat resolved to {seam.launch_provider(value, parent)}"))
                resolved.append(value)
                notes.extend(seat_notes)
            entry["seats"] = resolved
            info = [note["info"] for note in notes if isinstance(note, dict)]
            problems = selection_notes + [note for note in notes if not isinstance(note, dict)]
            if problems:
                entry["notes"] = problems
            if info:
                entry["info"] = info
        if from_file:
            lines = opencode_allow_lines({name: list(configured)}, allow_for(config, name), catalog)
            if lines:
                entry["notes"] = [*entry.get("notes", []), *lines]
            hints = search_hints(configured, catalog)
            if hints:
                entry["search"] = hints
        finish_entry(entry, name, catalog, parent, budget, exclude, allow)
        result["roles"][name] = entry
    return result


__all__ = [
    "default_seats",
    "inherit_replacement",
    "settle_options",
    "resolve_seat",
    "finish_seat",
    "settle_inherit_catalog",
    "runs_haiku_55",
    "attach_haiku_brief",
    "cursor_note",
    "finish_entry",
    "configured_seats",
    "resolve",
]
