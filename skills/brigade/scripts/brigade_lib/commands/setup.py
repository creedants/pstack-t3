import argparse
import fcntl
import fnmatch
import functools
import hashlib
import io
import json
import os
import posixpath
import re
import runpy
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from brigade_lib import seam
from brigade_lib._store.opening import rename_restaurant
from brigade_lib._store.settings import COOLDOWN_KEY, cooldown_minutes, parse_cooldown_minutes
from brigade_lib.affected import layout
from brigade_lib.compact import _load, drift_line, effective_cap, effective_provider, parse_context_cap, parse_coordinator_provider, sync_meta
from brigade_lib.items import doc_policy
from brigade_lib.leases import kind_limits, next_owner, repository_cap, require_workers, set_thread, worker_cap
from brigade_lib.shared import BrigadeError, clean, command_words
from brigade_lib.store import AUTOFIRE, CHANGE_NOTE_KEY, LIVENESS_KEY, LOW_RESERVE_KEY, MODES, REPORTING, ROUND_BUDGET_KEY, SCRATCH_KEY, change_note_dir, is_admin, liveness_of, low_reserve_of, open_restaurant, parse_change_note_dir, parse_liveness_ms, parse_low_reserve, parse_round_budget, round_budget_of, sibling_lines, siblings, scratch_parent_text
from brigade_lib.tickets import cap_pairs, intake_list, review_pairs, set_intake

COMMANDS = ("open", "set")


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


def register_open(sub):
    p = sub.add_parser("open", help="create a restaurant, or print an existing one")
    p.add_argument("--project-root", required=True)
    p.add_argument("--name")
    p.add_argument("--rename", metavar="NAME", help="rename the --name restaurant and move its store; refused while landing work or an item is held")
    p.add_argument("--admin", action="store_true", help="open the repository's executive admin at <project>/.admin; takes no --name")
    p.add_argument("--reporting", choices=REPORTING, default="milestones",
                   help="how often the coordinator replies, default milestones")
    p.add_argument("--intake", default="", help="comma-separated intake sources this coordinator owns, such as github")
    p.add_argument("--workers", type=int, help="dishes in progress or in review at once; missing reads as 2")
    p.add_argument("--mode", choices=MODES, help="this restaurant's light or full mode; missing leaves it to the roles files")
    p.add_argument("--context-cap", default=None,
                   help="on creation, set the context cap; missing reads as 300000; creation with a cap, or with an explicit provider, writes the cap to the project's .claude/settings.local.json when the provider is claude, replacing any autoCompactWindow already there; a plain open writes nothing under the project root; an existing store is left alone")
    p.add_argument("--coordinator-provider", default=None,
                   help="on creation, choose claude or other; missing uses claude; an existing store uses its saved provider")


def register_set(sub):
    p = sub.add_parser("set", help="record a setting")
    p.add_argument("--thread")
    p.add_argument("--replace", action="store_true", help="replace a recorded coordinator thread")
    p.add_argument("--expect", help="executive admin only: replace the thread while it is still this value")
    p.add_argument("--stopped", help="with --expect recovering:<id>: the run id t3_thread_wait reported terminal, idle, or gone")
    p.add_argument("--reports-to", help="the executive admin's thread to report to; \"\" clears it")
    p.add_argument("--schedule", action="append", default=[], metavar="NAME=ID")
    p.add_argument("--reporting", choices=REPORTING, help="how often the coordinator replies")
    p.add_argument("--intake", help="comma-separated intake sources this coordinator owns; replaces the list; \"\" clears it")
    p.add_argument("--workers", type=int, help="dishes in progress or in review at once")
    p.add_argument("--mode", help="full or light from the next brief; \"\" leaves it to the roles files")
    p.add_argument("--build-command",
                   help="the build command edits verify runs; none means the project has no build; \"\" removes it")
    p.add_argument("--check-command",
                   help="the check command edits verify runs; none means the project has no check; \"\" removes it")
    p.add_argument("--autofire", choices=AUTOFIRE,
                   help="the lowest ticket priority startable lists, or off; missing reads as normal")
    p.add_argument("--cap", metavar="KIND=N",
                   help="comma-separated kind=number pairs, at most that many items of that kind in a worker slot; "
                        "kind= clears that kind's cap; \"\" clears every cap")
    p.add_argument("--review", metavar="KIND=MODE",
                   help="comma-separated kind=mode pairs, the default mode for a new item of that kind; "
                        "kind= clears that kind's depth; \"\" clears every depth")
    p.add_argument("--scratch-dir", metavar="DIR",
                   help="the parent directory briefs pass to mktemp -d -p; an absolute path outside /tmp; "
                        "\"\" clears it")
    p.add_argument("--low-reserve", metavar="N",
                   help="the workers still idle while a low ticket waits behind an urgent or normal one; "
                        "\"\" clears it to 1")
    p.add_argument("--round-budget", metavar="N",
                   help="the send-backs that owe a round decision; \"\" clears it to 3")
    p.add_argument("--liveness-ms", metavar="N",
                   help="the interval a liveness schedule uses, a whole number of at least 60000; \"\" clears it")
    p.add_argument("--change-note-dir", metavar="DIR",
                   help="the repository-relative directory holding one change-note fragment per branch; "
                        "\"\" clears it, and an unset key leases no fragment")
    p.add_argument("--cooldown-minutes", metavar="N",
                   help="the minutes a cooled seat stays out of a launch; \"\" clears it to 60")
    p.add_argument("--affected-tests", metavar="JSON",
                   help="JSON object or file for the affectedTests layout; \"\" clears it; - reads stdin")
    p.add_argument("--shared-docs", metavar="JSON",
                   help="JSON list or file for the sharedDocs catalog; \"\" clears it; - reads stdin")
    p.add_argument("--doc-timebox", metavar="N",
                   help="the shared-document timebox in minutes, 1 or more; \"\" clears it to 90")
    p.add_argument("--context-cap", default=None,
                   help="the auto-compact window written to .claude/settings.local.json when the provider is claude; \"\" clears it to 300000")
    p.add_argument("--coordinator-provider", default=None,
                   help="claude or other; \"\" clears it to claude; other writes no settings file")


def register(sub):
    register_open(sub)
    register_set(sub)


def validate_settings(meta):
    """Refuse a stored setting that does not parse, before this command writes it over.

    It checks contextCap, coordinatorProvider, caps, review, lowReserve, roundBudget, livenessMs, affectedTests, sharedDocs,
    docTimebox, changeNoteDir, and cooldownMinutes, by calling each setting's reader predicate, so a value accepted here is a value that reader
    accepts. A missing key keeps its default.
    """
    effective_cap(meta)
    effective_provider(meta)
    kind_limits(meta)
    low_reserve_of(meta)
    round_budget_of(meta)
    liveness_of(meta)
    layout(meta)
    doc_policy(meta)
    change_note_dir(meta)
    cooldown_minutes(meta)


def preflight_settings(prospective, args):
    """Read the settings file the set path would write, before set_thread raises the landing floor.

    sync_meta reads that file before it writes, so a refusal there would leave the floor raised while no file
    changes. This reads it under the same condition, so the refusal is the same sentence.
    """
    if args.context_cap is None or effective_provider(prospective) != "claude":
        return
    _load(prospective["projectRoot"])


def handle_open(args, root):
    if args.admin and args.name:
        raise BrigadeError("open --admin takes no --name")
    if not args.admin and not args.name:
        raise BrigadeError("open needs --name, or --admin")
    if args.rename is not None:
        return rename_restaurant(root, args)
    require_workers(args.workers)
    context_cap = None
    if args.context_cap not in (None, ""):
        context_cap = parse_context_cap(args.context_cap)
    coordinator_provider = None
    if args.coordinator_provider not in (None, ""):
        coordinator_provider = parse_coordinator_provider(args.coordinator_provider)
    restaurant, created = open_restaurant(root, args.project_root, args.name, args.reporting,
                                          intake_list(args.intake), args.workers, args.admin, args.mode,
                                          context_cap=context_cap,
                                          coordinator_provider=coordinator_provider)
    validate_settings(restaurant.meta)
    lines = [f"{'opened' if created else 'exists'} {restaurant.dir}"]
    meta = restaurant.meta
    thread = (meta.get("thread") or "").strip()
    if not created and thread:
        lines.append(f"thread {thread} already recorded")
    intake = meta.get("intake") or []
    if not created and args.intake and intake_list(args.intake) != intake:
        lines.append(f"intake stays {', '.join(intake) or 'empty'}; change it with set --intake")
    if not created and args.mode is not None and args.mode != meta.get("mode"):
        lines.append(f"mode stays {meta.get('mode') or 'unset'}; change it with set --mode")
    if not created and context_cap is not None and context_cap != effective_cap(meta):
        lines.append(f"context-cap stays {effective_cap(meta)}; change it with set --context-cap")
    if not created and coordinator_provider is not None and coordinator_provider != effective_provider(meta):
        lines.append(f"coordinator-provider stays {effective_provider(meta)}; change it with set --coordinator-provider")
    lines.extend(sibling_lines(restaurant))
    if siblings(restaurant.dir, restaurant.meta.get("projectRoot")) and not is_admin(meta):
        cap = repository_cap(restaurant.meta["projectRoot"])
        workers = worker_cap(restaurant.meta)
        if cap is not None and workers >= cap:
            lines.append(f"warning: workers {workers} is at or above the repository cap of {cap} while a sibling exists")
    meta = restaurant.meta
    if not created:
        line = drift_line(meta)
        if line is not None:
            lines.append(line)
    return "\n".join(lines)


def handle_set(restaurant, args):
    if args.thread or args.expect is not None:
        if args.thread is None:
            raise BrigadeError("--expect needs --thread")
        prospective = prospective_settings(restaurant, args)
        validate_settings(prospective)
        preflight_settings(prospective, args)
        return set_thread(restaurant, args)
    with restaurant.checked():
        return seam.command(restaurant, args)


def handle(restaurant, args):
    if args.command == "set":
        return handle_set(restaurant, args)
    return None


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
    if args.low_reserve is not None:
        if args.low_reserve == "":
            drop.append(LOW_RESERVE_KEY)
        else:
            changes[LOW_RESERVE_KEY] = parse_low_reserve(args.low_reserve)
    if args.round_budget is not None:
        if args.round_budget == "":
            drop.append(ROUND_BUDGET_KEY)
        else:
            changes[ROUND_BUDGET_KEY] = parse_round_budget(args.round_budget)
    if args.liveness_ms is not None:
        if args.liveness_ms == "":
            drop.append(LIVENESS_KEY)
        else:
            changes[LIVENESS_KEY] = parse_liveness_ms(args.liveness_ms)
    if args.change_note_dir is not None:
        if args.change_note_dir == "":
            drop.append(CHANGE_NOTE_KEY)
        else:
            changes[CHANGE_NOTE_KEY] = parse_change_note_dir(args.change_note_dir)
    if args.cooldown_minutes is not None:
        if args.cooldown_minutes == "":
            drop.append(COOLDOWN_KEY)
        else:
            changes[COOLDOWN_KEY] = parse_cooldown_minutes(args.cooldown_minutes)
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
    if args.doc_timebox is not None:
        if args.doc_timebox == "":
            drop.append("docTimebox")
        else:
            changes["docTimebox"] = parse_doc_timebox(args.doc_timebox)
    if args.context_cap is not None:
        if args.context_cap == "":
            drop.append("contextCap")
        else:
            changes["contextCap"] = parse_context_cap(args.context_cap)
    if args.coordinator_provider is not None:
        if args.coordinator_provider == "":
            drop.append("coordinatorProvider")
        else:
            changes["coordinatorProvider"] = parse_coordinator_provider(args.coordinator_provider)
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


def handle_locked(restaurant, args, view):
    if args.command != "set":
        return None
    meta = restaurant.meta
    changes, drop, logged = {}, [], None
    if args.thread is not None and (args.thread or args.expect is not None):
        current = (meta.get("thread") or "").strip()
        generation, changing = next_owner(meta, args.thread, args.replace, args.expect, args.stopped)
        changes.update(generation=generation, thread=args.thread)
        if changing and current and not current.startswith("recovering:"):
            changes["previousThread"] = current
        if changing and is_admin(meta):
            note = f"replaced {current}" if current else ("restarted" if meta.get("generation") else "first thread")
            note += f"; stopped {clean(args.stopped)}" if args.stopped else ""
            ident = f"{args.thread}@{generation}"
            if not any(row["kind"] == "thread" and row["id"] == ident for row in restaurant.rows("log.tsv")):
                logged = ("thread", ident, "recorded", note)
    settings, drop = setting_changes(restaurant, args)
    changes.update(settings)
    prospective = prospective_settings(restaurant, args)
    validate_settings(prospective)
    if logged is not None:
        restaurant.log(*logged)
    if args.context_cap is not None and effective_provider(prospective) == "claude":
        sync_meta(prospective)
    meta = restaurant.change_meta(drop, **changes)
    return json.dumps(meta, indent=2)
