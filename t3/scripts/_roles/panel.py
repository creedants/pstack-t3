"""Usage-limit panels over configured review backups seats."""


from dataclasses import dataclass


from _roles.base import Backup, EFFORT_IDS, LADDER, PANEL_BACKUP_ROLE, PANEL_MINIMUM_PASSES, RolesError, USAGE_LIMIT_PATTERNS
from _roles.resolve import finish_seat, resolve_seat, settle_options
from _roles.seats import apply_level, exclude_reason, family, find_model, providers_by_id, runnable


def usage_limited(text):
    return any(pattern.search(text or "") for pattern in USAGE_LIMIT_PATTERNS)


def seat_level(options):
    """The failed seat's level, from an effort id or variant. None when the seat named none."""
    if not isinstance(options, dict):
        return None
    for key in (*EFFORT_IDS, "variant"):
        value = options.get(key)
        if isinstance(value, str) and value in LADDER:
            return value
    return None


def author_families(authors):
    return {family(author) for author in authors or () if author}


def _catalog_pair(catalog, provider_id, model_id, blocked=frozenset()):
    if provider_id in blocked:
        return None
    provider = providers_by_id(catalog).get(provider_id)
    if not runnable(provider):
        return None
    model = find_model(provider, model_id)
    if model is None:
        return None
    return provider, model


def _emit_backup(role, label, provider, model, declared_options, failed_options, budget, resumed, exclude=()):
    """Launch the candidate seat.

    declared_options are the candidate's own. They are settled through the
    catalog. When they name no effort option, the failed seat's level applies.
    """
    seat = {"providerInstanceId": provider["providerInstanceId"], "model": model["id"]}
    if declared_options:
        seat["options"] = dict(declared_options)
        seat, _option_notes, _option_problems = settle_options(seat, model)
    declared = seat.get("options") or {}
    level = seat_level(declared)
    if level is None and not any(key in declared for key in (*EFFORT_IDS, "variant")):
        level = seat_level(failed_options)
    if level is not None:
        seat = apply_level(seat, model, level)
    seat = finish_seat(seat, model, budget, exclude, role)
    applied = seat_level(seat.get("options"))
    where = f"{seat['providerInstanceId']}/{seat['model']}"
    at = f" at {applied}" if applied else ""
    if resumed:
        report = f"{role}: {label} resumed on {where}{at} after the reset"
    else:
        report = f"{role}: {label} hit its usage limit; relaunched on {where}{at}"
    return Backup("relaunch", report, seat)


@dataclass(frozen=True)
class PanelVerdict:
    """What backup does with one configured review backups seat.

    Exactly one of seat and reason is set. seat is resolve_seat's output for a
    pair _catalog_pair accepted, so it is never inherit and never a substituted model.
    """

    where: str
    seat: dict | None = None
    reason: str | None = None
    option_notes: tuple = ()
    option_problems: tuple = ()

    @property
    def notes(self):
        if self.reason is not None:
            return (f"dropped {self.where}: {self.reason}",)
        return tuple(f"{self.where}: {note}" for note in self.option_notes)

    @property
    def problems(self):
        if self.reason is not None:
            return self.notes
        return tuple(f"{self.where}: {problem}" for problem in self.option_problems)


def panel_verdicts(configured, catalog, budget, blocked=frozenset(), authors=(), exclude=()):
    """One verdict per configured review backups seat, in seat order.

    The caller removes excluded seats first. A seat is dropped for the first of
    these that holds: the exclude list, a blocked provider, an author's family,
    a provider that is not runnable or a model not in the catalog, a family an
    earlier kept seat has.
    """
    skip = author_families(authors)
    verdicts, seated = [], set()
    for seat in configured:
        provider_id, model_id = seat["providerInstanceId"], seat["model"]
        where = f"{provider_id}/{model_id}"
        seat_family = family(model_id)
        reason = exclude_reason(seat, exclude, PANEL_BACKUP_ROLE)
        if reason is not None:
            pass
        elif provider_id in blocked:
            reason = f"{provider_id} is out"
        elif seat_family in skip:
            reason = f"{seat_family} wrote the diff"
        elif _catalog_pair(catalog, provider_id, model_id) is None:
            reason = "not runnable or not in the catalog"
        elif seat_family in seated:
            reason = f"family {seat_family} already seated"
        else:
            reason = None
        if reason is not None:
            verdicts.append(PanelVerdict(where, reason=reason))
            continue
        try:
            value, seat_notes, seat_problems = resolve_seat(seat, catalog, budget, PANEL_BACKUP_ROLE, exclude)
        except RolesError as error:
            detail = str(error)
            prefix = where + ": "
            if detail.startswith(prefix):
                detail = detail[len(prefix):]
            verdicts.append(PanelVerdict(where, reason=detail))
            continue
        seated.add(seat_family)
        verdicts.append(PanelVerdict(where, value, None, tuple(seat_notes), tuple(seat_problems)))
    return verdicts


def panel_shortfall(seats):
    """None when seats can run a panel, else the clause that says how far short they are."""
    if len(seats) >= PANEL_MINIMUM_PASSES:
        return None
    return f"has {len(seats)} usable seat{'' if len(seats) == 1 else 's'} and needs {PANEL_MINIMUM_PASSES}"


def _backup_panel(role, label, review_backups, catalog, panel_budget, blocked, authors, resume, exclude=()):
    configured, skipped = review_backups
    verdicts = panel_verdicts(configured or [], catalog, panel_budget, blocked, authors, exclude)
    seats = [verdict.seat for verdict in verdicts if verdict.seat is not None]
    notes = skipped + [note for verdict in verdicts for note in verdict.notes]
    lead = f"{role}: {label} is still out after the reset" if resume else f"{role}: {label} hit its usage limit"
    shortfall = panel_shortfall(seats)
    if shortfall is None:
        report = (
            f"{lead}; every paid reviewer backup is out, so review backups runs {len(seats)} seats; "
            "land only if no reviewer reproduces a blocker and at least two pass"
        )
        return Backup("panel", report, seats=tuple(seats), notes=tuple(notes))
    report = f"{lead}; review backups {shortfall}, so the work waits for the reset"
    if notes:
        report += f" ({'; '.join(notes)})"
    return Backup("park", report)


__all__ = [
    "usage_limited",
    "seat_level",
    "author_families",
    "PanelVerdict",
    "panel_verdicts",
    "panel_shortfall",
]
