import json
import sys
from pathlib import Path
from brigade_lib._store.settings import COOLDOWN_KEY, parse_cooldown_minutes
from brigade_lib.affected import layout
from brigade_lib.compact import parse_context_cap, parse_coordinator_provider
from brigade_lib.credit import KEY as CREDIT_KEY, parse_credit
from brigade_lib.items import doc_policy
from brigade_lib import notify
from brigade_lib.leases import require_workers
from brigade_lib.shared import BrigadeError, clean, command_words
from brigade_lib.store import CHANGE_NOTE_KEY, LIVENESS_KEY, LOW_RESERVE_KEY, MODES, ROUND_BUDGET_KEY, SCRATCH_KEY, parse_change_note_dir, parse_liveness_ms, parse_low_reserve, parse_round_budget, scratch_parent_text
from brigade_lib.tickets import cap_pairs, intake_list, review_pairs, set_intake


def _json_setting(args, name, flag):
    value = _read_json_setting(args, name, flag)
    key, reader = ("affectedTests", layout) if name == "affected_tests" else ("sharedDocs", doc_policy)
    try:
        reader({key: value})
    except BrigadeError as error:
        raise BrigadeError(f"{flag} {error}") from error
    return value


def _read_json_setting(args, name, flag):
    cached = f"_json_cache_{name}"
    if hasattr(args, cached):
        return getattr(args, cached)
    text = getattr(args, name)
    if text == "-":
        if sys.stdin.isatty():
            raise BrigadeError(f"{flag} - reads JSON from stdin")
        raw = sys.stdin.read()
        setattr(args, name, raw)
        text = raw
        try:
            value = json.loads(text)
        except ValueError as error:
            raise BrigadeError(f"{flag} {error}") from error
        setattr(args, cached, value)
        return value
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            value = json.loads(text)
        except ValueError as error:
            raise BrigadeError(f"{flag} {error}") from error
        setattr(args, cached, value)
        return value
    try:
        raw = Path(stripped).read_text(encoding="utf-8")
    except (ValueError, OSError) as error:
        raise BrigadeError(f"{flag} {error}") from error
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise BrigadeError(f"{flag} {error}") from error
    setattr(args, cached, value)
    return value


def parse_doc_timebox(text):
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise BrigadeError(f"--doc-timebox takes a whole number of 1 or more, got '{text}'") from None
    if value < 1:
        raise BrigadeError(f"--doc-timebox takes a whole number of 1 or more, got '{text}'")
    return value


def _clear_or_parse(value, key, parse, changes, drop):
    """One flag's setting change. Empty string drops the key, None leaves it, and any other value stores its parse."""
    if value == "":
        drop.append(key)
    elif value is not None:
        changes[key] = parse(value)


def setting_changes(restaurant, args):
    """The restaurant.json changes and the drops every set flag but --thread, --expect, --replace, and --stopped makes.

    One translation serves both the prospective settings a command validates and the write it makes, so a flag
    can never mean two things.
    """
    meta = restaurant.meta
    changes, drop = {}, []
    if args.intake is not None:
        changes["intake"] = set_intake(restaurant, intake_list(args.intake))["intake"]
    if args.reporting:
        changes["reporting"] = args.reporting
    if args.workers is not None:
        require_workers(args.workers)
        changes["workers"] = args.workers
    if args.mode is not None:
        if args.mode in MODES:
            changes["mode"] = args.mode
        elif args.mode == "":
            drop.append("mode")
        else:
            raise BrigadeError('--mode takes full, light, or ""')
    for key, value in (("buildCommand", args.build_command), ("checkCommand", args.check_command)):
        if value is None:
            continue
        if value.strip():
            command_words(value.strip())
            changes[key] = value.strip()
        else:
            drop.append(key)
    if args.autofire:
        changes["autofire"] = args.autofire
    for key, pairs in (("caps", cap_pairs(args.cap) if args.cap is not None else None),
                       ("review", review_pairs(args.review) if args.review is not None else None)):
        if pairs is None:
            continue
        existing = meta.get(key)
        merged = dict(existing) if (pairs and isinstance(existing, dict)) else ({} if pairs else {})
        for kind, value in pairs.items():
            if value:
                merged[kind] = value
            else:
                merged.pop(kind, None)
        if merged:
            changes[key] = merged
        else:
            drop.append(key)
    if args.reports_to is not None:
        if clean(args.reports_to):
            changes["reportsTo"] = clean(args.reports_to)
        else:
            drop.append("reportsTo")
    if args.scratch_dir is not None:
        scratch = scratch_parent_text(args.scratch_dir)
        if scratch:
            changes[SCRATCH_KEY] = scratch
        else:
            drop.append(SCRATCH_KEY)
    _clear_or_parse(args.low_reserve, LOW_RESERVE_KEY, parse_low_reserve, changes, drop)
    _clear_or_parse(args.round_budget, ROUND_BUDGET_KEY, parse_round_budget, changes, drop)
    _clear_or_parse(args.liveness_ms, LIVENESS_KEY, parse_liveness_ms, changes, drop)
    _clear_or_parse(args.change_note_dir, CHANGE_NOTE_KEY, parse_change_note_dir, changes, drop)
    _clear_or_parse(args.cooldown_minutes, COOLDOWN_KEY, parse_cooldown_minutes, changes, drop)
    _clear_or_parse(args.notify, notify.KEY, notify.parse_notify, changes, drop)
    if args.affected_tests is not None:
        if args.affected_tests == "":
            drop.append("affectedTests")
        else:
            changes["affectedTests"] = _json_setting(args, "affected_tests", "--affected-tests")
    if args.shared_docs is not None:
        if args.shared_docs == "":
            drop.append("sharedDocs")
        else:
            changes["sharedDocs"] = _json_setting(args, "shared_docs", "--shared-docs")
    _clear_or_parse(args.doc_timebox, "docTimebox", parse_doc_timebox, changes, drop)
    _clear_or_parse(args.context_cap, "contextCap", parse_context_cap, changes, drop)
    _clear_or_parse(args.coordinator_provider, "coordinatorProvider", parse_coordinator_provider, changes, drop)
    if args.pr_credit is not None:
        changes[CREDIT_KEY] = parse_credit(args.pr_credit)
    if args.schedule:
        schedules = dict(meta.get("schedules") or {})
        for pair in args.schedule:
            if "=" not in pair:
                raise BrigadeError(f"--schedule takes NAME=ID, got {pair!r}")
            name, ident = pair.split("=", 1)
            if ident:
                schedules[name] = ident
            else:
                schedules.pop(name, None)
        changes["schedules"] = schedules
    return changes, drop


def prospective_settings(restaurant, args):
    """The restaurant.json settings this command would leave with every flag but --thread, --expect, --replace, and --stopped applied."""
    changes, drop = setting_changes(restaurant, args)
    prospective = dict(restaurant.meta)
    prospective.update(changes)
    for key in drop:
        prospective.pop(key, None)
    return prospective
