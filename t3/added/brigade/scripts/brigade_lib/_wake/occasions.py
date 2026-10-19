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
from brigade_lib.items import attempt_starts
from brigade_lib.reviews import item_verdicts, pass_check, round_count, round_debt, unrecorded_reviews
from brigade_lib.shared import BrigadeError, MalformedTable, clean, slug
from brigade_lib.store import MESSAGE_ONLY, ROUND_KIND, TABLES, reporting_of, round_budget_of, store_path
from brigade_lib.tickets import EVERY_ROW, label_words, priority_of, read_labels, request_files
from brigade_lib.startable import NO_WORKER, STARTS_AFTER, Standing, Waiting, lease_refusals, plan, startable, startable_lines, waiting_ticket
from brigade_lib.watch import _latest_trunk, closing_options, dish_progress, open_item_decisions, option_key, record_model_clear, record_model_skip, record_worker_end, report_is_current, require_seat, watch, worker_where
from brigade_lib._wake.snapshot import Reply, _trunk_red, dish_by_id, lowest_id, maybe_run, open_for

DRAIN_LABELS = ("in progress", "in review", "passed review", "waiting to land")


YES_OCCASIONS = {
    "every-turn": frozenset(("user", "evening", "decision", "failure", "stuck", "merged", "sent-back", "blocked", "every-turn")),
    "milestones": frozenset(("user", "evening", "decision", "failure", "stuck", "merged", "sent-back", "blocked")),
    "digest": frozenset(("user", "evening", "decision", "stuck", "drained")),
}


CLOSE_OCCASIONS = frozenset(("evening", "decision", "user"))


OCCASION_ORDER = ("user", "decision", "evening", "drained", "merged", "sent-back", "blocked", "stuck", "failure")


ROUTINE_DETAILS = {
    "unspecified": "unspecified",
    "report-back": "worker report",
    "checks": "pull request",
    "merge": "merge",
    "liveness": "liveness",
    "intake": "intake",
    "morning": "morning service",
    "evening": "evening report",
    "user": "user message",
    "admin": "admin request",
}


def routine_detail(trigger):
    if trigger.wake == "review":
        return trigger.verdict
    return ROUTINE_DETAILS.get(trigger.wake, trigger.wake)


def fresh_decision_ids(snapshot):
    """New question ids, new decision-ticket ids, and dishes that owe a raise."""
    since = snapshot.last_report_at
    questions = [row["id"] for row in snapshot.decisions if row["at"] > since]
    tickets = [row["id"] for row in snapshot.tickets
               if row["state"] in ("waiting", "assigned") and row.get("decision") == "yes" and row["at"] > since]
    dishes = [debt.dish for debt in snapshot.debts if debt.at > since and not open_for(snapshot, debt.dish)]
    return questions, tickets, dishes


def budget_reached(snapshot, trigger):
    if trigger.wake != "review" or trigger.verdict not in ("send-back", "pass-with-edits"):
        return False
    if open_for(snapshot, trigger.dish):
        return False
    count = snapshot.round_counts.get(trigger.dish, 0)
    if trigger.verdict == "send-back":
        count += 1
    return count >= snapshot.round_budget


def blocked_needs_user(snapshot, trigger, failure):
    return (trigger.wake == "review" and trigger.verdict == "blocked" and failure != "fixable"
            and not open_for(snapshot, trigger.dish))


def decision_holds(snapshot, trigger, failure):
    questions, tickets, dishes = fresh_decision_ids(snapshot)
    if questions or tickets or dishes:
        return True
    if budget_reached(snapshot, trigger):
        return True
    return blocked_needs_user(snapshot, trigger, failure)


def decision_detail(snapshot, trigger, failure):
    questions, tickets, dishes = fresh_decision_ids(snapshot)
    if budget_reached(snapshot, trigger) or blocked_needs_user(snapshot, trigger, failure):
        dishes = [*dishes, trigger.dish]
    if questions:
        return lowest_id(questions, snapshot.warn)
    if tickets:
        return lowest_id(tickets, snapshot.warn)
    if dishes:
        return lowest_id(dishes, snapshot.warn)
    return "decision"


def merge_accepted(snapshot, trigger):
    return trigger.wake == "merge" and maybe_run(snapshot, ("dish", trigger.dish, "--state", "merged")) is not None


def is_drained(snapshot, trigger):
    """The four counts are zero after an accepted merge claim, and the claim or a newer merged or dropped row exists."""
    if not snapshot.dishes and not snapshot.tickets:
        return False
    counts = dict(snapshot.counts)
    claimed = merge_accepted(snapshot, trigger)
    if claimed:
        counts["waiting to land"] = max(0, counts.get("waiting to land", 0) - 1)
    if any(counts.get(label, 0) for label in DRAIN_LABELS):
        return False
    newer = any(event["kind"] == "dish" and event["state"] in ("merged", "dropped") for event in snapshot.events)
    return bool(newer or claimed)


def request_text(snapshot, trigger):
    for ident, text in snapshot.requests:
        if ident == trigger.request:
            return text
    return ""


def from_user_request(snapshot, trigger):
    return trigger.wake == "admin" and request_text(snapshot, trigger).startswith("from-user ")


def note_unsent(holding, unsent, failure):
    if not unsent or unsent == "misrouted":
        return
    if unsent == "blocked":
        if failure != "stuck":
            holding["failure"] = ("unsent blocked", False)
        return
    holding[unsent] = (f"unsent {unsent}", unsent in MESSAGE_ONLY)


def held_occasions(snapshot, trigger, failure, unsent):
    """The occasions this wake holds, earliest first. A forced pair is yes at every level."""
    holding = {}
    if trigger.wake == "user" or (from_user_request(snapshot, trigger) and not snapshot.reports_to):
        detail = "question" if trigger.wake == "user" and trigger.question else "message"
        holding["user"] = (detail, False)
    if decision_holds(snapshot, trigger, failure):
        holding["decision"] = (decision_detail(snapshot, trigger, failure), False)
    if trigger.wake == "evening":
        holding["evening"] = ("report", False)
    if snapshot.reporting == "digest" and is_drained(snapshot, trigger):
        holding["drained"] = ("batch", False)
    if merge_accepted(snapshot, trigger):
        holding["merged"] = (trigger.dish, False)
    if trigger.wake == "review" and trigger.verdict in ("send-back", "pass-with-edits") and not budget_reached(snapshot, trigger):
        holding["sent-back"] = (trigger.dish, False)
    if trigger.wake == "review" and trigger.verdict == "blocked" and not blocked_needs_user(snapshot, trigger, failure):
        if failure != "fixable":
            holding["blocked"] = (trigger.dish, False)
    if failure == "stuck":
        holding["stuck"] = ("stuck", False)
    if failure == "fixable" or (trigger.wake == "checks" and trigger.result == "fail" and not failure):
        holding["failure"] = ("fixable" if failure == "fixable" else "fail", False)
    if trigger.wake in ("liveness", "merge") and _trunk_red(snapshot) is not None:
        red = _trunk_red(snapshot)
        holding["red-trunk"] = (red.get("id") or "trunk", True)
    note_unsent(holding, unsent, failure)
    found = [(name, *holding[name]) for name in OCCASION_ORDER if name in holding]
    for name, pair in holding.items():
        if name not in OCCASION_ORDER:
            found.append((name, *pair))
    if snapshot.reporting == "every-turn" and not found:
        found.append(("every-turn", "wake", False))
    return found


def occasion_yes(level, occasion, forced):
    if forced:
        return True
    return occasion in YES_OCCASIONS[level]


def reply_for(snapshot, trigger, failure, unsent):
    """The one reply. A store that reports to the executive admin uses that rule first, unless --unsent is set. The reply is yes when any held occasion qualifies at this reporting level. The printed occasion is the first qualifying one, and the first held occasion when none qualify."""
    if snapshot.reports_to and not unsent:
        close = ("close", "--to-file") if trigger.wake == "evening" or is_drained(snapshot, trigger) else None
        if trigger.wake == "user":
            return Reply(True, "user", "question" if trigger.question else "message", None)
        return Reply(False, "reports to the executive admin", routine_detail(trigger), close)
    found = held_occasions(snapshot, trigger, failure, unsent)
    if not found:
        return Reply(False, "routine", routine_detail(trigger), None)
    qualifying = [(name, detail) for name, detail, flag in found if occasion_yes(snapshot.reporting, name, flag)]
    if qualifying:
        occasion, detail = qualifying[0]
        yes = True
    else:
        occasion, detail = found[0][0], found[0][1]
        yes = False
    close = None
    if snapshot.reporting == "digest" and yes:
        close = ("close", "--to-file")
    elif any(name in CLOSE_OCCASIONS for name, _detail in qualifying):
        close = ("close",)
    return Reply(yes, occasion, detail, close)


def wake_label(trigger):
    if trigger.wake in ("report-back", "review", "checks", "merge"):
        return f"{trigger.wake} {trigger.dish}"
    if trigger.wake == "admin":
        return f"admin {trigger.request}"
    return trigger.wake


