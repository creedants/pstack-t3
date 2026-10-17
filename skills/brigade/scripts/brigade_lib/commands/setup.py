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
from brigade_lib.affected import layout
from brigade_lib.compact import _load, drift_line, effective_cap, effective_provider, parse_context_cap, parse_coordinator_provider, sync_meta
from brigade_lib.leases import kind_limits, next_owner, repository_cap, require_workers, set_thread, worker_cap
from brigade_lib.shared import BrigadeError, clean, command_words
from brigade_lib.store import AUTOFIRE, LIVENESS_KEY, LOW_RESERVE_KEY, MODES, REPORTING, SCRATCH_KEY, is_admin, liveness_of, low_reserve_of, open_restaurant, parse_liveness_ms, parse_low_reserve, sibling_lines, siblings, scratch_parent_text
from brigade_lib.tickets import cap_pairs, intake_list, review_pairs, set_intake

COMMANDS = ("open", "set")


def register_open(sub):
    p = sub.add_parser("open", help="create a restaurant, or print an existing one")
    p.add_argument("--project-root", required=True)
    p.add_argument("--name")
    p.add_argument("--admin", action="store_true", help="open the repository's executive admin at <project>/.admin; takes no --name")
    p.add_argument("--reporting", choices=REPORTING, default="milestones",
                   help="how often the coordinator replies (default: milestones)")
    p.add_argument("--intake", default="", help="comma-separated intake sources this coordinator owns, such as github")
    p.add_argument("--workers", type=int, help="how many dishes may be in progress or in review; missing reads as 2")
    p.add_argument("--mode", choices=MODES, help="this restaurant's light or full mode; missing leaves it to the roles files")
    p.add_argument("--context-cap", default=None,
                   help="on creation, set the context cap; missing adopts the file's own value, or writes 300000 when it holds none; an existing store is left alone")
    p.add_argument("--coordinator-provider", default=None,
                   help="on creation, choose claude or other; missing uses claude; an existing store uses its saved provider")


def register_set(sub):
    p = sub.add_parser("set", help="record the head chef thread, a schedule id, or the reporting level")
    p.add_argument("--thread")
    p.add_argument("--replace", action="store_true", help="replace a recorded coordinator thread")
    p.add_argument("--expect", help="executive admin only: replace the thread only while it is still this value")
    p.add_argument("--stopped", help="with --expect recovering:<id>: the run id t3_thread_wait reported terminal, idle, or gone")
    p.add_argument("--reports-to", help="the executive admin's thread this coordinator reports to; \"\" clears it")
    p.add_argument("--schedule", action="append", default=[], metavar="NAME=ID")
    p.add_argument("--reporting", choices=REPORTING, help="how often the coordinator replies")
    p.add_argument("--intake", help="comma-separated intake sources this coordinator owns; replaces the list, and \"\" clears it")
    p.add_argument("--workers", type=int, help="how many dishes may be in progress or in review")
    p.add_argument("--mode", help="full or light from the next brief; \"\" leaves it to the roles files")
    p.add_argument("--build-command",
                   help="the build command edits verify runs; none records that the project has no build, and \"\" removes "
                        "the setting")
    p.add_argument("--check-command",
                   help="the check command edits verify runs; none records that the project has no check, and \"\" removes "
                        "the setting")
    p.add_argument("--autofire", choices=AUTOFIRE,
                   help="the lowest ticket priority the startable command lists as startable, or off; missing reads as normal")
    p.add_argument("--cap", metavar="KIND=N",
                   help="comma-separated kind=number pairs, at most that many items of the kind holding a worker "
                        "slot; a pair with no number clears that kind's cap; an empty value clears every cap")
    p.add_argument("--review", metavar="KIND=MODE",
                   help="comma-separated kind=mode pairs, the default mode for a new item of that kind, full or "
                        "light; a pair with no mode clears that kind's depth; an empty value clears every depth")
    p.add_argument("--scratch-dir", metavar="DIR",
                   help="the parent directory worker briefs pass to mktemp -d -p; an absolute directory path outside "
                        "/tmp; \"\" clears the setting")
    p.add_argument("--low-reserve", metavar="N",
                   help="the most workers still idle when a low ticket is held back while an urgent or normal "
                        "ticket can start; 0 holds no low ticket back; \"\" clears it to 1")
    p.add_argument("--liveness-ms", metavar="N",
                   help="the interval a new liveness schedule uses, a whole number of at least 60000; \"\" removes the setting")
    p.add_argument("--context-cap", default=None,
                   help="the auto-compact window written to .claude/settings.local.json; \"\" clears it to 300000")
    p.add_argument("--coordinator-provider", default=None,
                   help="claude or other; \"\" clears it to claude; other writes no settings file")


def register(sub):
    register_open(sub)
    register_set(sub)


def validate_settings(meta):
    """Refuse a stored setting that does not parse, before this command writes it over.

    It checks contextCap, coordinatorProvider, caps, review, lowReserve, livenessMs, and affectedTests, by calling
    each setting's reader predicate, so a value accepted here is a value that reader accepts. A missing key keeps
    its default.
    """
    effective_cap(meta)
    effective_provider(meta)
    kind_limits(meta)
    low_reserve_of(meta)
    liveness_of(meta)
    layout(meta)


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
    if args.liveness_ms is not None:
        if args.liveness_ms == "":
            drop.append(LIVENESS_KEY)
        else:
            changes[LIVENESS_KEY] = parse_liveness_ms(args.liveness_ms)
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
