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
from brigade_lib.affected import _diff_names as _affected_diff, verify_text as _affected_text
from brigade_lib.briefs import brief, stored_contract
from brigade_lib.commands.once import Once
from brigade_lib.items import ATTEMPT_AFTER, announce_archive, drop, fire, item_escalation, record_escalation, retired_worker_threads, workers_full
from brigade_lib.leases import fragment_lines
from brigade_lib.reviews import fix_round_refusal, pass_check, write_threads
from brigade_lib.scope import gate_outside, gate_recheck, widen
from brigade_lib.shared import BrigadeError, stored
from brigade_lib.store import COUNTED_STATES, DISH_STATES, KINDS, NEEDS_CONTRACT, WORKER_ROLES
from brigade_lib.wake import record_worker_end

COMMANDS = ("fire", "dish", "brief", "affected", "contract")


def brief_contract(value):
    """The contract as given. Refuses None and text that is only whitespace."""
    if not (value or "").strip():
        raise BrigadeError(NEEDS_CONTRACT)
    return value


def _brief_fields(args):
    flagged = any(getattr(args, name) is not None for name in ("goal", "contract", "verify", "base", "paths", "lease")) or args.acceptance or args.context
    if args.fields is not None:
        if flagged:
            raise BrigadeError("use either --fields or the field flags, not both")
        if args.fields == "-":
            if sys.stdin.isatty():
                raise BrigadeError("brief fields: --fields - reads JSON from stdin")
            raw = sys.stdin.read()
        else:
            try:
                raw = Path(args.fields).read_text(encoding="utf-8")
            except OSError as error:
                raise BrigadeError(f"brief fields: {error}") from error
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise BrigadeError(f"brief fields: {error}") from error
        if not isinstance(payload, dict):
            raise BrigadeError("brief fields: JSON must be an object")
        allowed = {"goal", "acceptance", "contract", "verify", "base", "context", "paths", "lease"}
        lists = {"acceptance", "context"}
        unknown = [key for key in payload if key not in allowed]
        if unknown:
            raise BrigadeError(f"brief fields: unknown key {unknown[0]}")
        for key in ("goal", "acceptance", "base"):
            if key not in payload:
                raise BrigadeError(f"brief fields: missing {key}")
        for key, value in payload.items():
            if value is None:
                raise BrigadeError(f"brief fields: {key} is null")
            if key in lists:
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    raise BrigadeError(f"brief fields: {key} must be a list of strings")
            elif not isinstance(value, str):
                raise BrigadeError(f"brief fields: {key} must be a string")
        if not payload["base"].strip():
            raise BrigadeError("brief --base takes a trunk ref, such as origin/main")
        return {
            "goal": payload["goal"],
            "acceptance": payload["acceptance"],
            "contract": brief_contract(payload.get("contract")),
            "verify": payload.get("verify", ""),
            "paths": payload["paths"] if "paths" in payload else "",
            "lease": payload["lease"] if "lease" in payload else "",
            "base": payload["base"],
            "context": payload["context"] if "context" in payload else [],
        }
    if args.goal is None or args.base is None:
        raise BrigadeError("brief needs --fields, or --goal and --base")
    if not args.base.strip():
        raise BrigadeError("brief --base takes a trunk ref, such as origin/main")
    return {
        "goal": args.goal,
        "acceptance": args.acceptance,
        "contract": brief_contract(args.contract),
        "verify": args.verify or "",
        "paths": args.paths or "",
        "lease": args.lease or "",
        "base": args.base,
        "context": args.context,
    }


def _rebound_for_restart(restaurant, ident, thread):
    """True when `thread` is the one open worker row started after the latest sent-back dish row, no queued or in-progress dish row follows that row, and that id is not retired."""
    if not thread or thread in set(retired_worker_threads(restaurant, ident)):
        return False
    departed = ""
    for row in restaurant.rows("log.tsv"):
        if row["id"] != ident or row["kind"] != "dish":
            continue
        if row["state"] == "sent-back":
            departed = row["at"]
        elif row["state"] in ("queued", "in-progress"):
            departed = ""
    if not departed:
        return False
    open_rows = [row for row in restaurant.rows("threads.tsv")
                 if row["dish"] == ident and row["role"] in WORKER_ROLES and not row["end"]
                 and row["thread"] == thread and row["start"] > departed]
    return len(open_rows) == 1


def worker_ended_alone(args):
    """True when --worker-ended is the only dish flag given.

    A dish change on the same argv races the record, because `--state dropped`
    dispatches to drop before command runs, and a new non-empty `--thread` on an
    in-progress dish appends the attempt-start row the attempt number is counted
    from. An empty value is a change too: `--thread ""` clears the cell the
    archive step names.
    """
    def given(name):
        value = getattr(args, name, None)
        return value is not None and value is not False
    return not any(given(name) for name in ("state", "task", "thread", "branch", "pr", "sha", "timebox", "reported",
                                            "lease", "paths", "add_paths", "stopped", "mode", "reason"))


def add_paths_alone(args):
    """True when --add-paths is the only dish flag given."""
    def given(name):
        value = getattr(args, name, None)
        return value is not None and value is not False
    return not any(given(name) for name in ("state", "task", "thread", "branch", "pr", "sha", "timebox", "reported",
                                            "lease", "paths", "worker_ended", "stopped", "mode", "reason"))


def register_fire(sub):
    p = sub.add_parser("fire", help="group tickets into one dish and assign it to a station")
    p.add_argument("--tickets", required=True, action=Once,
                   help="comma-separated ticket ids; the first is the lead ticket; the item takes the lead ticket's goal, "
                        "and its kind when it has one; given once")
    p.add_argument("--station", required=True, help="the pstack playbook the worker runs")
    p.add_argument("--summary", required=True)
    for field in ("task", "thread", "branch"):
        p.add_argument(f"--{field}", default="")
    p.add_argument("--timebox", type=int, default=60, help="minutes before the liveness check flags the dish")
    p.add_argument("--paths", default="", action=Once, help="paths the dish will change. Fire claims a landing lease on them, plus the generated copies and doc pages the contract map makes from them when the contract lists generated paths and a map; given once")
    p.add_argument("--mode", help="full, to start the dish in full mode; needs --reason")
    p.add_argument("--reason", help="one line: why the dish runs in full mode")
    p.add_argument("--kind", choices=KINDS,
                   help="the item's kind when the lead ticket has none or --tickets names no ticket; a lead ticket with no "
                        "kind records it too; refused when the lead ticket has another kind; with no --kind and no kind "
                        "from the lead ticket, station bug-fix or perf-issue gives fix, station investigation gives study, "
                        "and fire is refused on any other station")
    p.add_argument("--ride-along", default="",
                   help="comma-separated ids from --tickets, not the first, that may be under another goal than the first "
                        "ticket's; each must have priority low and must not be a feature; every other ticket after the "
                        "first must have the first ticket's goal or no goal")
    p.add_argument("--doc-item", action="store_true",
                   help="allow a shared document in --paths and record it as a document item")


def register_brief(sub):
    help_text = (
        "Render the worker brief for a dish. Refuses when a field or the coordinator thread is missing. "
        "House rules use the case-sensitive tags [worker], [reviewer], and [coordinator]. "
        "The first tag anywhere on a rule's first line selects its role. "
        "After outer whitespace is stripped, each non-empty line starting without a space or tab begins a rule. "
        "Following blank lines and lines starting with a space or tab continue that rule, even after a blank line. "
        "A brief keeps untagged and [worker] rules. It removes the [worker] tag and at most one following space. "
        "It leaves out [reviewer] and [coordinator] rules with their continuation lines."
    )
    p = sub.add_parser("brief", help=help_text, description=help_text)
    p.add_argument("id")
    p.add_argument("--fields", help="JSON file of brief fields, or - for stdin")
    p.add_argument("--goal", default=None, help="one sentence: the outcome")
    p.add_argument("--acceptance", action="append", default=[], help="a checkable criterion; repeatable, at least one")
    p.add_argument("--contract", default=None,
                   help="the item's contract, rendered as given under CONTRACT: in the brief; write none when the item has no "
                        "safety, privacy, or integrity property; a brief with no contract is refused")
    p.add_argument("--verify", default=None, help="exact commands that prove it, plus known gotchas")
    p.add_argument("--paths", default=None, action=Once, help="leased paths, when fire did not claim them; given once")
    p.add_argument("--lease", default=None, action=Once, help="the landing lease id, when fire did not claim it; given once")
    p.add_argument("--base", default=None, help="the trunk ref the worker's branch starts from, such as origin/main")
    p.add_argument("--context", action="append", default=[], help="a pointer to files, PRs, or upstream reports; repeatable")


def register_contract(sub):
    p = sub.add_parser("contract", help="print the contract the dish's last brief stored")
    p.add_argument("id")


def register_affected(sub):
    p = sub.add_parser("affected", help="print the verify paragraph for the changed paths")
    p.add_argument("--root", default=None, help="project root to scan")
    p.add_argument("--paths", action="append", default=[], help="repo path the dish will change; repeatable")
    p.add_argument("--diff", default=None, help="git diff range to name changed files, such as origin/main...HEAD")


def register_dish(sub):
    p = sub.add_parser("dish", help="update a dish")
    p.add_argument("id")
    p.add_argument("--state", choices=DISH_STATES)
    for field in ("task", "thread", "branch", "pr", "sha"):
        p.add_argument(f"--{field}")
    p.add_argument("--timebox", type=int, help="minutes; raise it once for a worker that is still making progress")
    p.add_argument("--reported", action="store_true", help="record that this attempt's report-back arrived")
    p.add_argument("--worker-ended", help="record that this attempt's worker ended or failed and wrote no report")
    p.add_argument("--lease", action=Once, help="record a lease id claimed again; given once")
    p.add_argument("--paths", action=Once, help="record the paths that lease covers; given once")
    p.add_argument("--add-paths", action=Once, help="widen the lease to cover these comma-separated paths; given once")
    p.add_argument("--stopped", help="with --state dropped: the run id t3_thread_wait reported terminal, or idle")
    p.add_argument("--mode", help="full, to move open work to full mode once; needs --reason")
    p.add_argument("--reason", help="one line: why the dish moves to full mode")


def register(sub):
    register_fire(sub)
    register_brief(sub)
    register_affected(sub)
    register_contract(sub)
    register_dish(sub)


def handle_fire(restaurant, args):
    args.reason = item_escalation(args.mode, args.reason)
    ids = [ident.strip() for ident in args.tickets.split(",") if ident.strip()]
    ride_along = [ident.strip() for ident in args.ride_along.split(",") if ident.strip()]
    return fire(restaurant, ids, args.station, args.task, args.thread, args.branch, args.summary, args.timebox,
                args.paths, args.reason, args.kind or "", ride_along, args.doc_item)


def handle_brief(restaurant, args):
    fields = _brief_fields(args)
    return brief(restaurant, args.id, **fields)


def handle_affected(args, store):
    """store is the store root the entry resolved from --store; the picker reads its layout and check command from it."""
    paths = args.paths or []
    has_paths = bool([part for one in paths for part in str(one).split(",") if part.strip() != ""])
    has_diff = args.diff is not None and str(args.diff).strip() != ""
    if has_paths and has_diff:
        raise BrigadeError("use either --paths or --diff, not both")
    if not has_paths and not has_diff:
        raise BrigadeError("affected needs --paths or --diff")
    project = args.root or "."
    if has_diff:
        names = _affected_diff(project, str(args.diff))
        return _affected_text(project, names, seam.LAND, store=store)
    return _affected_text(project, paths, seam.LAND, store=store)


def handle_dish(restaurant, args):
    args.reason = item_escalation(args.mode, args.reason)
    if args.reason and args.state in ("queued", "merged", "dropped"):
        raise BrigadeError(f"--mode full needs open work; run --state {args.state} without it")
    if getattr(args, "add_paths", None) is not None:
        if not add_paths_alone(args):
            raise BrigadeError("--add-paths takes no other dish flag")
        return widen(restaurant, args.id, args.add_paths)
    if args.worker_ended is not None:
        if not worker_ended_alone(args):
            raise BrigadeError("--worker-ended takes no other dish flag")
        with restaurant.checked():
            return record_worker_end(restaurant, args.id, args.worker_ended)
    if args.state == "dropped":
        return drop(restaurant, args.id, args.stopped)
    if args.state == "in-review":
        sha = args.sha
        if sha is None:
            with restaurant.checked():
                _, current = restaurant.find("dishes.tsv", args.id)
                sha = current.get("sha", "")
        if sha:
            snapshot = gate_outside(restaurant, args.id, sha)
            if snapshot is None:
                with restaurant.checked():
                    return seam.command(restaurant, args)
            with restaurant.checked():
                gate_recheck(restaurant, args.id, snapshot)
                return seam.command(restaurant, args)
    if args.branch and args.lease is None:
        with restaurant.checked():
            result = seam.command(restaurant, args)
        lines = fragment_lines(restaurant, args.id, args.branch)
        return f"{result}\n{lines}" if lines else result
    with restaurant.checked():
        return seam.command(restaurant, args)


def handle(restaurant, args):
    if args.command == "fire":
        return handle_fire(restaurant, args)
    if args.command == "brief":
        return handle_brief(restaurant, args)
    if args.command == "dish":
        return handle_dish(restaurant, args)
    if args.command == "contract":
        with restaurant.checked():
            return seam.command(restaurant, args)
    return None


def handle_locked(restaurant, args, view):
    if args.command == "contract":
        return stored_contract(restaurant, args.id)
    if args.command != "dish":
        return None
    _, current = restaurant.find("dishes.tsv", args.id)
    bound = stored("dishes.tsv", "thread", args.thread or "")
    if args.thread:
        canonical = stored("dishes.tsv", "thread", args.thread)
        if canonical:
            args.thread = canonical
    worker = any(value and value != current.get(field, "") for field, value in (("thread", args.thread), ("task", args.task)))
    if args.state == "in-progress" or worker:
        refusal = fix_round_refusal(restaurant, current)
        if refusal:
            raise BrigadeError(refusal)
    if args.state in COUNTED_STATES and current["state"] not in COUNTED_STATES:
        full = workers_full(restaurant)
        if full:
            raise BrigadeError(full)
    if (args.lease or args.paths) and current["state"] in ("merged", "dropped"):
        raise BrigadeError(f"{args.id} is {current['state']}; it holds no lease")
    if args.state in ("queued", "merged"):
        ok, why = pass_check(restaurant, args.id, args.sha or current["sha"])
        if not ok:
            raise BrigadeError(f"only reviewed work lands: {why}")
    entering = args.state == "in-progress" and current["state"] != "in-progress"
    restart = entering and current["state"] in ATTEMPT_AFTER
    replacing = (bool(args.thread) and args.thread != current.get("thread", "")
                 and current["state"] == "in-progress" and args.state in (None, "in-progress"))
    rebound = _rebound_for_restart(restaurant, args.id, current.get("thread", "")) if restart else False
    if restart and args.thread is None and rebound:
        thread = current.get("thread", "")
    else:
        thread = "" if restart and args.thread is None else args.thread
    recorded = set(retired_worker_threads(restaurant, args.id))
    earlier = set(recorded)
    if restart and current.get("thread") and not rebound:
        earlier.add(current["thread"])
    if thread and thread in earlier:
        raise BrigadeError(f"thread {thread} is an earlier attempt of {args.id}; a send-back launches a fresh worker")
    escalated = None
    if args.reason:
        escalated = record_escalation(restaurant, args.id, args.reason)
    next_thread = current.get("thread", "") if thread is None else thread
    if current.get("thread") and next_thread != current["thread"] and (restart or next_thread):
        if current["thread"] not in recorded:
            restaurant.log("worker", args.id, "retired", current["thread"])
    reported = "" if entering or replacing else ("yes" if args.reported else None)
    transition = bool(args.state) and args.state != current["state"]
    ends = transition and args.state != "in-progress"
    closing = args.reported or ends or (thread and thread != current.get("thread", ""))
    keep = args.thread if args.thread and args.thread != current.get("thread", "") else ""
    row = restaurant.update("dishes.tsv", args.id, "dish", state=args.state, task=args.task, thread=thread,
                            branch=args.branch, pr=args.pr, sha=args.sha, timebox=args.timebox, reported=reported,
                            lease=args.lease, paths=args.paths)
    if replacing:
        restaurant.log("dish", args.id, "in-progress", row.get("summary", ""))
    if args.thread:
        write_threads(restaurant, args.id, [("worker", args.thread, "")], closing=closing, keep_thread=keep)
    elif closing:
        write_threads(restaurant, args.id, (), closing=True)
    if args.state == "merged":
        announce_archive(restaurant, args.id, "closed")
    elif bound and bound != current.get("thread", ""):
        announce_archive(restaurant, args.id, "replaced", thread=bound, replaced=current.get("thread", ""))
    if args.state == "merged":
        for ticket in filter(None, row["tickets"].split(",")):
            restaurant.update("rail.tsv", ticket, "ticket", state="done")
    if escalated is not None:
        return f"{args.id} {row['state']}, mode full: {escalated}"
    return f"{args.id} {row['state']}"
