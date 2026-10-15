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
from brigade_lib.layout import _layout, _one
from brigade_lib.leases import STANDINGS, StandingText, autofire_of, block_inputs, claim_paths, entry_line, fire_command, lease_block, paths_inside, paths_overlap, recheck_blocks, refill_of, renew_line, running_workers, worker_cap, workers_line
from brigade_lib.reviews import item_verdicts, pass_check, round_count, round_debt, unrecorded_reviews
from brigade_lib.shared import BrigadeError, MalformedTable, age, clean, slug
from brigade_lib.store import LEASED_STATES, LOW_FLAG_DAYS, LOW_RESERVE, MESSAGE_ONLY, OPEN_LINES, OPEN_RUN_MINUTES, PRIORITIES_BY_RANK, ROUND_BUDGET, ROUND_KIND, TABLES, reporting_of, store_path
from brigade_lib.tickets import EVERY_ROW, Label, handed_count, handoff_id, inbox_file, label_words, paths_of, priority_of, read_labels, request_files, sibling_rails, taken_row



def _goal_paused(goal, refill):
    if not goal:
        return False
    found = refill.goals.get(goal)
    return found is not None and found.state == "paused"


NO_WORKER = "waiting for an idle worker"
STARTS_AFTER = "starts after {tickets}"
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


@dataclass(frozen=True)
class Waiting:
    id: str
    priority: str
    filed: datetime
    paths: tuple
    decision: bool
    summary: str
    label: Label = Label()


@dataclass(frozen=True)
class Standing:
    ticket: Waiting
    kind: str
    why: str
    overdue: bool


def waiting_ticket(row, label=Label()):
    filed = datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
    if filed.tzinfo is None:
        filed = filed.replace(tzinfo=timezone.utc)
    return Waiting(row["id"], priority_of(row), filed, paths_of(row), row["decision"] == "yes", row["summary"], label)


def plan(tickets, refill, running, cap, level, refusals, now, expanded=None):
    allowed = () if level == "off" else PRIORITIES_BY_RANK[:PRIORITIES_BY_RANK.index(level) + 1]
    idle = max(0, cap - running)
    priorities = {ticket.id: ticket.priority for ticket in tickets}
    higher_waits = any(ticket.priority != "low" for ticket in tickets)
    started = {}
    standings = []
    for ticket in sorted(tickets, key=lambda ticket: (PRIORITIES_BY_RANK.index(ticket.priority), ticket.filed,
                                                     int(ticket.id[1:]) if ticket.id[1:].isdigit() else 0)):
        low = ticket.priority == "low"
        eff = expanded.get(ticket.id, ticket.paths) if expanded else ticket.paths
        shared = [ident for ident, paths in started.items() if paths_overlap(eff, paths)]
        kind, why = "start", ""
        if ticket.decision:
            kind = "decision"
        elif not ticket.paths:
            kind = "unknown"
        elif ticket.id in refusals:
            kind, why = "blocked", refusals[ticket.id]
        elif ticket.label.goal and _goal_paused(ticket.label.goal, refill):
            kind, why = "paused", "its goal is paused"
        elif (low and len(shared) == 1 and priorities[shared[0]] != "low"
              and paths_inside(eff, started[shared[0]])):
            kind, why = "rides", shared[0]
        elif shared:
            kind, why = "blocked", STARTS_AFTER.format(tickets=", ".join(shared))
        elif ticket.priority not in allowed:
            kind = "below"
        elif (hold := refill.hold(ticket.label)):
            kind, why = hold.standing, hold.words
        elif not idle:
            kind, why = "blocked", NO_WORKER
        elif low and higher_waits and idle <= LOW_RESERVE:
            kind = "reserve"
        else:
            idle -= 1
            started[ticket.id] = eff
            refill = refill.started(ticket.label)
        standings.append(Standing(ticket, kind, why, low and now - ticket.filed > timedelta(days=LOW_FLAG_DAYS)))
    return standings


def lease_refusals(meta, tickets, next_dish):
    root, prefix = meta["projectRoot"], slug(meta["restaurant"])
    asked, refusals = {}, {}
    for ticket in tickets:
        if ticket.decision or not ticket.paths:
            continue
        if ticket.paths not in asked:
            asked[ticket.paths] = lease_block(root, f"{prefix}/{ticket.id}",
                                              claim_paths(root, ",".join(ticket.paths), f"{prefix}/{next_dish.lower()}").text)
        if asked[ticket.paths]:
            refusals[ticket.id] = clean(asked[ticket.paths][1])
    return refusals


def expanded_paths(tickets, lay):
    found = {}
    for ticket in tickets:
        if not ticket.paths or lay is None:
            found[ticket.id] = ticket.paths
            continue
        seen = set(ticket.paths)
        extras = []
        for raw in ticket.paths:
            try:
                got = _one(raw, lay)
            except KeyboardInterrupt:
                raise
            except Exception:
                continue
            for one in got:
                if one not in seen:
                    seen.add(one)
                    extras.append(one)
        found[ticket.id] = tuple([*ticket.paths, *extras])
    return found


def startable_lines(standings, meta, running, now):
    lines = [workers_line(meta, running)]
    if not standings:
        return "\n".join([*lines, "no waiting tickets"])
    kinds = [standing.kind for standing in standings]
    lines.append(", ".join([f"waiting tickets: {len(standings)}",
                            *(f"{text.count_label}: {kinds.count(kind)}" for kind, text in STANDINGS.items() if kind in kinds)]))
    for standing in standings:
        ticket = standing.ticket
        flag = " (over a week)" if standing.overdue else ""
        paths = f"; {','.join(ticket.paths)}" if ticket.paths else ""
        words = STANDINGS[standing.kind].line_words.format(why=standing.why)
        lines.append(f"{ticket.id} {ticket.priority}{label_words(ticket.label)}, {age(now - ticket.filed)}{flag}: {words}{paths}")
        if standing.kind in ("start", "rides"):
            lines.append(f"  {ticket.summary}")
    return "\n".join(lines)


def startable(restaurant):
    with restaurant.checked():
        meta, labels = restaurant.meta, read_labels(restaurant, EVERY_ROW)
        tickets = [waiting_ticket(row, labels.tickets[row["id"]]) for row in restaurant.rows("rail.tsv") if row["state"] == "waiting"]
        running, next_dish = running_workers(restaurant), restaurant.next_id("dishes.tsv")
        refill = refill_of(meta, restaurant.rows("dishes.tsv"), labels)
    try:
        lay = _layout(meta["projectRoot"])
    except KeyboardInterrupt:
        raise
    except Exception:
        lay = None
    expanded = expanded_paths(tickets, lay)
    refusals = lease_refusals(meta, tickets, next_dish)
    moment = datetime.now(timezone.utc)
    return startable_lines(plan(tickets, refill, running, worker_cap(meta), autofire_of(meta), refusals, moment, expanded), meta, running, moment)


def ticket_lines(restaurant, state, held):
    labels, lines = read_labels(restaurant, EVERY_ROW), []
    for row in restaurant.rows("rail.tsv"):
        if state and row["state"] != state:
            continue
        line = f"{row['id']} {row['state']} {priority_of(row)}{label_words(labels.tickets[row['id']])} [{row['source']}] {row['summary']}"
        if row["ref"]:
            line += f" {row['ref']}"
        if row["state"] == "waiting" and row["id"] in held:
            line += f" blocked: {held[row['id']][0]}"
        lines.append(line)
    return "\n".join(lines)


def option_key(text):
    return text.strip().rstrip(".").casefold()


def closing_options(row):
    if not row.get("dish", "").strip():
        return []
    default = option_key(row.get("default", ""))
    options = []
    for option in row.get("options", "").split(","):
        option = option.strip()
        if option and option_key(option) != default:
            options.append(option)
    return options


def open_item_decisions(rows):
    decisions = {}
    for row in rows:
        dish = row["dish"]
        if row["state"] == "open" and dish and dish not in decisions:
            decisions[dish] = row
    return decisions


def worker_where(dish):
    """The worker words a progress sentence names."""
    if dish["thread"]:
        return f"thread {dish['thread']}"
    if dish["task"]:
        return f"task {dish['task']}"
    return "no worker recorded"


def attempt_count(restaurant, dish):
    """The attempt this dish is on, counted from 1."""
    return len(attempt_starts(restaurant, dish))


def ended_row(restaurant, dish):
    """The log row that records this attempt's worker end, or None.

    The attempt number is the row's state cell, so no row is rewritten when a
    replacement worker starts another attempt.
    """
    number = str(attempt_count(restaurant, dish))
    found = None
    for row in restaurant.rows("log.tsv"):
        if row["kind"] == "ended" and row["id"] == dish["id"] and row["state"] == number:
            found = row
    return found


def recovery_lines(dish, error):
    """The flag line and the four recovery steps for a dish whose worker ended with no report."""
    ident = dish["id"]
    branch = dish.get("branch") or ""
    context = shlex.quote(error)
    # A brief needs a goal, an acceptance, a verify, a base, and a contract, so the line names them
    # instead of a command brief refuses.
    steps = [f"  keep the commits on {shlex.quote(branch)} and record no new branch; the dish keeps its lease"
             if branch else "  keep the commits where the worker left them; the dish keeps its lease",
             f"  write the continuation brief: brief {ident} with --base <trunk ref>, --context {context}, "
             "and the goal, acceptance, verify, and contract it needs",
             f"  launch a fresh thread in the same worktree and record it with dish {ident} --thread <new thread id>"]
    # The record needs a thread, but the cell can be cleared afterwards without starting another attempt.
    steps.append(f"  archive the dead thread {shlex.quote(dish['thread'])}" if dish["thread"]
                 else "  archive the dead worker")
    return [f"{ident}: worker ended with no report; the provider error is {error}", *steps]


def dish_progress(restaurant, dish, moment, held=False):
    """The in-progress lines watch prints for one dish. Empty when the dish is held or not in progress.

    The report file's mtime is read here. Call this under the store lock.
    """
    if held or dish["state"] != "in-progress":
        return []
    starts = attempt_starts(restaurant, dish)
    if not dish["thread"] and len(starts) > 1:
        return [f"{dish['id']}: in progress with no worker thread; launch a fresh worker"]
    start = starts[-1]
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    minutes = int((moment - start).total_seconds() // 60)
    timebox = int(dish.get("timebox") or 60)
    report_path = restaurant.dir / "reports" / f"{dish['id']}.md"
    where = worker_where(dish)
    written = datetime.fromtimestamp(report_path.stat().st_mtime, timezone.utc) if report_path.exists() else None
    if written and written >= start:
        if dish.get("reported") == "yes":
            ago = int((moment - written).total_seconds() // 60)
            if ago > OPEN_RUN_MINUTES:
                return [f"{dish['id']}: reported {ago}m ago, not in review; read the thread ({where})"]
            return [f"{dish['id']}: report written {ago}m ago; review it even if the worker's run is still open ({where})"]
        return [f"{dish['id']}: report written, no report-back ({where})"]
    # A dead worker is neither running nor over its timebox, so its lines come before both.
    ended = ended_row(restaurant, dish)
    if ended:
        return recovery_lines(dish, ended["note"])
    if minutes > timebox:
        return [f"{dish['id']}: over its {timebox}m timebox at {minutes}m with no report; read its thread and decide ({where})"]
    return [f"{dish['id']}: running {minutes}m of {timebox}m ({where})"]


HANDOFF_BYTES = 40000


def handoff_size_line(directory):
    path = directory / "handoff.md"
    try:
        if not path.is_file():
            return None
        size = path.stat().st_size
    except OSError:
        return None
    if size <= HANDOFF_BYTES:
        return None
    return f"handoff.md is {size} bytes. Rewrite its current-state section in place and move the old text to an archive file."


def watch(restaurant):
    rails = sibling_rails(restaurant)
    with restaurant.checked():
        restaurant.fence()
        progress = {}
        moment = datetime.now(timezone.utc)
        dishes = [dish for dish in restaurant.rows("dishes.tsv") if dish["state"] not in ("merged", "dropped")]
        decisions = open_item_decisions(restaurant.rows("86.tsv"))
        debts = {dish["id"]: round_debt(restaurant, dish) for dish in dishes}
        for dish in dishes:
            held = bool(decisions.get(dish["id"]) or debts[dish["id"]])
            sentences = dish_progress(restaurant, dish, moment, held)
            if sentences:
                progress[dish["id"]] = sentences
        lines = []
        for ticket in restaurant.rows("rail.tsv"):
            if ticket["state"] != "moved":
                continue
            name = ticket["dish"].removeprefix("to:")
            target = restaurant.dir.parent / name
            handoff = handoff_id(restaurant.dir, ticket["id"])
            taken = taken_row(rails.get(target.resolve()), handoff)
            # take writes the ticket before it deletes the file, so a missing file with no ticket was never delivered.
            if inbox_file(target, handoff).exists():
                if taken is None:
                    lines.append(f"{ticket['id']}: moved to {name}, waiting for ticket take")
            elif taken is None:
                lines.append(f"{ticket['id']}: moved to {name}, not delivered; run ticket move {ticket['id']} --to {name} again")
        handed = handed_count(restaurant)
        if handed:
            lines.append(f"handed to you: {handed}; run ticket take")
        inputs = block_inputs(restaurant)
        workers = workers_line(restaurant.meta, inputs[1])
        # A skipped seat is a restaurant-wide fact, not a dish line, so it sits between the two.
        skipped = skip_lines(restaurant)
    # land.py waits on the landing database, so every call runs outside the store lock.
    item_lines, answered = [], True
    for dish in dishes:
        decision = decisions.get(dish["id"])
        pending = debts[dish["id"]]
        found = list(progress.get(dish["id"], []))
        entry, submitted = entry_line(restaurant, dish) if dish["state"] in ("passed", "queued") else (None, False)
        # A submitted entry owns the lease now, so a released lease is not a reason to claim again.
        if dish["lease"] and dish["state"] in LEASED_STATES and not submitted:
            line, ok = renew_line(restaurant, dish)
            answered = answered and ok
            found += [line] if line else []
        found += [entry] if entry else []
        if pending:
            item_lines.append(f"{dish['id']}: {debts[dish['id']]} send-backs, decision pending")
        if decision:
            item_lines.append(f"{dish['id']}: open decision {decision['id']}: {decision['question']}; "
                              f"launch no worker or verifier until 86 answer {decision['id']}")
        if decision or pending:
            item_lines += found
        else:
            item_lines += found or [f"{dish['id']}: {OPEN_LINES[dish['state']]}"]
    for ident, note, held in recheck_blocks(restaurant, inputs):
        lines.append(f"{ident}: {held[1]}" if held else f"{ident}: unblocked; run {fire_command(ident, note)}")
    if answered:
        # walk --stale-hours then measures whether this coordinator still keeps its leases alive.
        restaurant.change_meta(lastActivityAt=seam.now())
    text = "\n".join([workers, *skipped, *(item_lines + lines or ["no work in progress"])])
    sentence = handoff_size_line(restaurant.dir)
    if sentence:
        restaurant.warn(sentence)
    return text


def hang_attempt(row, starts):
    """The attempt a hang row belongs to: its `(attempt <n>)` suffix, or the attempt running when it was logged."""
    match = re.search(r" \(attempt (\d+)\)$", row["note"])
    if match:
        return int(match.group(1))
    return sum(start <= datetime.fromisoformat(row["at"]) for start in starts)


def record_hang(restaurant, ident, provider, minutes, attempt=None):
    if not clean(provider):
        raise BrigadeError("hang needs a provider")
    if minutes < 0:
        raise BrigadeError("minutes must be 0 or more")
    _, dish = restaurant.find("dishes.tsv", ident)
    starts = attempt_starts(restaurant, dish)
    current = len(starts)
    if attempt is not None and not 1 <= attempt <= current:
        raise BrigadeError(f"{ident} has attempts 1 to {current}")
    # An earlier attempt was replaced, so only the current one waits for its report-back.
    if attempt in (None, current) and dish.get("reported") != "yes":
        earlier = f"; for a run left open by an earlier attempt, pass --attempt {'1' if current == 2 else f'1 to {current - 1}'}"
        raise BrigadeError("no report-back on this attempt" + (earlier if current > 1 else ""))
    number = attempt or current
    for row in restaurant.rows("log.tsv"):
        if row["kind"] == "hang" and row["id"] == ident and hang_attempt(row, starts) == number:
            return f"{ident}: hang already recorded"
    suffix = f" (attempt {attempt})" if attempt else ""
    restaurant.log("hang", ident, "open", f"{provider} {minutes}m{suffix}")
    return f"{ident}: {provider} open {minutes}m{suffix}"


def require_seat(seat):
    """The cleaned provider/model seat. A seat is one slash, both sides non-empty, and no whitespace."""
    value = clean(seat)
    provider, _, model = value.partition("/")
    if not (provider and model) or "/" in model or any(character.isspace() for character in value):
        raise BrigadeError("a model is provider/model")
    return value


def latest_models(restaurant):
    """The latest model log row per seat. The latest row for a seat wins."""
    found = {}
    for row in restaurant.rows("log.tsv"):
        if row["kind"] == "model":
            found[row["id"]] = row
    return found


def skip_lines(restaurant):
    """One line per seat whose latest model row is skipped, sorted by seat."""
    return [f"skipped {seat} ({row['note']}); run model clear {shlex.quote(seat)} after a smoke test passes"
            for seat, row in sorted(latest_models(restaurant).items()) if row["state"] == "skipped"]


def record_model_skip(restaurant, seat, cause):
    """Record one provider/model seat as skipped. The same cause writes no row while the seat stays skipped with it."""
    name = require_seat(seat)
    latest = latest_models(restaurant).get(name)
    if latest is not None and latest["state"] == "skipped" and latest["note"] == cause:
        return f"{name}: already skipped"
    restaurant.log("model", name, "skipped", cause)
    return f"{name}: recorded as skipped"


def record_model_clear(restaurant, seat):
    """Record a passing smoke test for one seat and clear its skip."""
    name = require_seat(seat)
    latest = latest_models(restaurant).get(name)
    if latest is None:
        raise BrigadeError(f"{name} is not skipped")
    if latest["state"] == "cleared":
        return f"{name}: already cleared"
    restaurant.log("model", name, "cleared", "smoke test passed")
    return f"{name}: no longer skipped"


def record_worker_end(restaurant, ident, error):
    """Record that this attempt's worker ended or failed and wrote no report.

    The log row is the only dish write, so the dish keeps its lease, its branch,
    and its thread cell.
    """
    reason = clean(error)
    if not reason:
        raise BrigadeError("--worker-ended needs the provider error")
    _, dish = restaurant.find("dishes.tsv", ident)
    if dish["state"] != "in-progress":
        raise BrigadeError(f"{ident} is {dish['state']}; --worker-ended records an in-progress dish")
    if not dish["thread"]:
        raise BrigadeError(f"{ident} has no worker thread recorded")
    if dish.get("reported") == "yes":
        raise BrigadeError(f"{ident} has a report-back on this attempt")
    if report_is_current(restaurant, dish):
        raise BrigadeError(f"{ident} has a report file for this attempt")
    attempt = str(attempt_count(restaurant, dish))
    for row in restaurant.rows("log.tsv"):
        if row["kind"] == "ended" and row["id"] == ident and row["state"] == attempt:
            if row["note"] == reason:
                return f"{ident}: worker end already recorded"
            raise BrigadeError(f"{ident} already records a different worker end for this attempt")
    restaurant.log("ended", ident, attempt, reason)
    return f"{ident}: worker ended with no report; the provider error is {reason}"


@dataclass(frozen=True)
class Run:
    argv: tuple


@dataclass(frozen=True)
class See:
    words: str


@dataclass(frozen=True)
class If:
    condition: str
    action: str


@dataclass(frozen=True)
class Reply:
    yes: bool
    occasion: str
    detail: str
    close: tuple


@dataclass(frozen=True)
class Trigger:
    wake: str
    dish: str = ""
    request: str = ""
    verdict: str = ""
    result: str = ""
    question: bool = False


@dataclass(frozen=True)
class Debt:
    dish: str
    count: int
    at: str


@dataclass(frozen=True)
class Snapshot:
    """One read of the store. It holds no plan. warn prints on standard error and writes nothing."""

    reporting: str
    reports_to: str
    last_report_at: str
    owner_block: str
    restaurant: str
    intake: tuple
    dishes: tuple
    tickets: tuple
    decisions: tuple
    debts: tuple
    round_counts: dict
    events: tuple
    counts: dict
    progress: tuple
    unrecorded: tuple
    inbox: tuple
    requests: tuple
    passes: dict
    blocked_since: tuple
    moment: datetime
    close_refusal: str
    warn: object
    place: str
    report_gaps: tuple


def dish_by_id(snapshot, ident):
    for dish in snapshot.dishes:
        if dish["id"] == ident:
            return dish
    return None


def open_for(snapshot, ident):
    for row in snapshot.decisions:
        if row["dish"] == ident:
            return row
    return None


def lowest_id(idents, warn):
    """The lowest id. A suffix that is not a base-10 integer warns and sorts after every numeric suffix."""
    ranked = []
    for ident in idents:
        try:
            ranked.append((0, int(ident[1:], 10), ident))
        except ValueError:
            warn(f"{ident} has no base-10 id suffix")
            ranked.append((1, 0, ident))
    return min(ranked)[2]


def crossing_send_back(restaurant, dish_id):
    """The at of the send-back that reached ROUND_BUDGET, using the same rows round_count counts."""
    verdicts = item_verdicts(restaurant.rows("pass.tsv"), dish_id)
    settled = [row["at"] for row in verdicts if row["verdict"] == "pass"]
    settled += [row["answered"] for row in restaurant.rows("86.tsv")
                if row["dish"] == dish_id and row["kind"] == ROUND_KIND and row["state"] == "answered"]
    since = max(settled, default="")
    backs = [row["at"] for row in verdicts if row["verdict"] == "send-back" and row["at"] > since]
    if len(backs) < ROUND_BUDGET:
        return ""
    return backs[ROUND_BUDGET - 1]


def load_inbox(restaurant):
    """Each json handoff take_tickets would read, as (name, ok, problem). A bad file is not a traceback."""
    found = []
    inbox = restaurant.dir / "inbox"
    for path in sorted(inbox.glob("*.json")):
        try:
            raw = path.read_text()
        except (OSError, UnicodeError) as error:
            found.append((path.name, False, f"could not be read ({error})"))
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            found.append((path.name, False, f"is not valid JSON ({error.msg})"))
            continue
        if not isinstance(data, dict):
            found.append((path.name, False, "is not a JSON object"))
            continue
        missing = [key for key in ("handoff", "source", "ref", "summary") if key not in data]
        if missing:
            found.append((path.name, False, f"has no {missing[0]} key"))
            continue
        found.append((path.name, True, ""))
    return tuple(found)


def report_is_current(restaurant, dish):
    """True when reports/<id>.md exists and its mtime is at least the last attempt start."""
    path = restaurant.dir / "reports" / f"{dish['id']}.md"
    if not path.exists():
        return False
    written = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    start = attempt_starts(restaurant, dish)[-1]
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return written >= start


def counted_timebox(restaurant, dish):
    """The row next passes to dish_progress. An empty timebox stays empty. A timebox that is not a base-10 integer warns and reads as 60."""
    cell = dish.get("timebox") or ""
    if cell == "":
        return dish
    try:
        int(cell, 10)
    except ValueError:
        restaurant.warn(f"{dish['id']} timebox {cell} is not a base-10 integer")
        return {**dish, "timebox": "60"}
    return dish


def malformed_close(restaurant):
    """The first malformed row rows names, in TABLES order, or empty when every table parses."""
    for table in TABLES:
        try:
            restaurant.rows(table)
        except MalformedTable as error:
            return str(error)
    return ""


def load_snapshot(restaurant, moment):
    """Read the store under the caller's lock. The caller already refused a bad owner."""
    read_labels(restaurant, EVERY_ROW)
    meta = restaurant.meta
    since = meta.get("lastReportAt") or ""
    dishes = tuple(restaurant.rows("dishes.tsv"))
    tickets = tuple(restaurant.rows("rail.tsv"))
    decisions = tuple(row for row in restaurant.rows("86.tsv") if row["state"] == "open")
    open_dishes = {row["dish"] for row in decisions if row["dish"]}
    progress = []
    gaps = []
    for dish in dishes:
        if dish["state"] != "in-progress" or dish.get("reported") != "yes":
            continue
        if dish["id"] in open_dishes:
            continue
        dish = counted_timebox(restaurant, dish)
        if not report_is_current(restaurant, dish):
            gaps.append((dish["id"], worker_where(dish)))
            continue
        for sentence in dish_progress(restaurant, dish, moment):
            if "no report-back" in sentence:
                continue
            if sentence.startswith(f"{dish['id']}: reported ") or sentence.startswith(f"{dish['id']}: report written "):
                progress.append((dish["id"], sentence))
    debts = []
    round_counts = {}
    for dish in dishes:
        round_counts[dish["id"]] = round_count(restaurant, dish["id"])
        count = round_debt(restaurant, dish)
        if count:
            debts.append(Debt(dish["id"], count, crossing_send_back(restaurant, dish["id"])))
    passes = {}
    for dish in dishes:
        sha = dish.get("sha") or ""
        if dish["state"] == "queued" or sha:
            ok, why = pass_check(restaurant, dish["id"], sha)
            passes[dish["id"]] = (ok, why)
    blocked_since = {}
    for event in restaurant.rows("log.tsv"):
        if event["kind"] == "ticket" and event["state"] == "blocked" and event["id"] not in blocked_since:
            blocked_since[event["id"]] = event["at"]
    pending = tuple((path.stem, path.read_text().strip()) for path in request_files(restaurant)[0])
    block = restaurant.owner_block() or ""
    return Snapshot(
        reporting_of(meta), meta.get("reportsTo") or "", since, block, slug(meta["restaurant"]),
        tuple(meta.get("intake") or ()), dishes, tickets, decisions, tuple(debts), round_counts,
        tuple(seam.events_since(restaurant, since).values()), seam.counts(restaurant), tuple(progress),
        tuple(unrecorded_reviews(restaurant)), load_inbox(restaurant), pending, passes,
        tuple(blocked_since.items()), moment, malformed_close(restaurant), restaurant.warn,
        store_path(restaurant.dir), tuple(gaps))


def inbox_ready(snapshot):
    """True when inbox take would read every json handoff and there is something to take."""
    if any(not ok for _, ok, _ in snapshot.inbox):
        return False
    return bool(snapshot.inbox) or bool(snapshot.requests)


def accepted_argv(snapshot, argv):
    """The argv when that command would exit 0 on this store. None means this sheet prints no run of it."""
    if snapshot.owner_block:
        return None
    if argv == ("inbox", "take"):
        return argv if inbox_ready(snapshot) else None
    if argv[:2] == ("ticket", "paths"):
        return None
    if len(argv) == 3 and argv[0] == "dish" and argv[2] == "--reported":
        dish = dish_by_id(snapshot, argv[1])
        if dish and dish["state"] == "in-progress" and dish.get("reported") != "yes":
            return argv
        return None
    if len(argv) == 5 and argv[0] == "86" and argv[1] == "add" and argv[2] == "--dish" and argv[4] == "--round-budget":
        debt = next((row for row in snapshot.debts if row.dish == argv[3]), None)
        if debt and not open_for(snapshot, debt.dish) and debt.at > snapshot.last_report_at:
            return argv
        return None
    if argv in (("close",), ("close", "--to-file")):
        return None if snapshot.close_refusal else argv
    if len(argv) == 4 and argv[0] == "dish" and argv[2:] == ("--state", "merged"):
        dish = dish_by_id(snapshot, argv[1])
        passed = snapshot.passes.get(argv[1])
        if dish and dish["state"] == "queued" and passed and passed[0]:
            return argv
        return None
    return None


def maybe_run(snapshot, argv):
    accepted = accepted_argv(snapshot, argv)
    if accepted is None:
        return None
    return Run(tuple(accepted))


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
    return count >= ROUND_BUDGET


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


def cue_line(cue):
    if isinstance(cue, Run):
        return "run " + " ".join(shlex.quote(word) for word in cue.argv)
    if isinstance(cue, See):
        return f"see {cue.words}"
    return f"if {cue.condition}: {cue.action}"


def format_sheet(snapshot, trigger, cues, reply):
    lines = [f"wake: {wake_label(trigger)}", f"reporting: {snapshot.reporting}"]
    if snapshot.reports_to:
        lines.append(f"reports to: {snapshot.reports_to}")
    lines.extend(cue_line(cue) for cue in cues)
    if not cues:
        lines.append("nothing else owed")
    token = "yes" if reply.yes else "no"
    lines.append(f"reply: {token} ({reply.occasion}: {reply.detail})")
    return "\n".join(lines)


def holder_of(snapshot, dish_id):
    return f"{snapshot.restaurant}/{dish_id}"


def land_follow(dish_id):
    return If(f"land or watch reports {dish_id} landed", f"run next merge {dish_id}")


def pause_if():
    return If("the queue is paused and this thread cannot fix it and this is not a bounce handed to a fresh worker and --failure stuck was not passed",
              "run next --failure stuck")


def pass_words(snapshot, dish):
    branch = dish.get("branch") or "none"
    sha = dish.get("sha") or "none"
    lease = dish.get("lease") or "none"
    return (f"File follow-ups from reports/{dish['id']}.md, write the pull request file, submit, and land. "
            f"Holder {holder_of(snapshot, dish['id'])}, branch {branch}, sha {sha}, lease {lease}.")


def merge_why(snapshot, dish):
    if dish["state"] != "queued":
        return f"{dish['id']} is {dish['state']}, not queued"
    ok, why = snapshot.passes.get(dish["id"], (False, f"{dish['id']} has no review verdict for {dish.get('sha') or ''}"))
    if not ok:
        return f"only reviewed work lands: {why}"
    return f"{dish['id']} cannot be marked merged"


def housekeeping(dish):
    thread = dish.get("thread") or "none"
    branch = dish.get("branch") or "none"
    pr = dish.get("pr") or "none"
    return (f"Finish with the old worker for thread {thread}, branch {branch}, and pull request {pr} for {dish['id']}. "
            f"If its run is still open, interrupt it and hang the attempt, then archive it. "
            f"Remove the worktree and delete the branch after that. Do not claim they are already gone.")


def prefix_cues(snapshot, trigger, failure):
    wake, dish_id = trigger.wake, trigger.dish
    if wake == "report-back":
        cues = []
        run = maybe_run(snapshot, ("dish", dish_id, "--reported"))
        if run:
            cues.append(run)
        owes = any(debt.dish == dish_id and debt.at > snapshot.last_report_at for debt in snapshot.debts)
        if owes or open_for(snapshot, dish_id):
            return cues
        cues.append(See(f"Read reports/{dish_id}.md and the diff. t3_thread_read the worker thread. "
                        f"If that run is still open, interrupt it, hang the attempt, then review per Run a service step 6."))
        return cues
    if wake == "review":
        if open_for(snapshot, dish_id):
            return []
        dish = dish_by_id(snapshot, dish_id)
        files = [name for name, _kind in snapshot.unrecorded if name.startswith(f"{dish_id}-review")]
        extra = f" Unrecorded review file {files[0]}." if files else ""
        sha = dish.get("sha") or ""
        if sha:
            recorded = f"Record pass record for {dish_id} at sha {sha} with verdict {trigger.verdict}.{extra}"
        else:
            recorded = f"Record pass record for {dish_id} with verdict {trigger.verdict}. No sha is recorded.{extra}"
        cues = [See(recorded)]
        if trigger.verdict in ("send-back", "pass-with-edits"):
            run = maybe_run(snapshot, ("86", "add", "--dish", dish_id, "--round-budget"))
            already = snapshot.round_counts.get(dish_id, 0) >= ROUND_BUDGET
            if not run and not already and budget_reached(snapshot, trigger):
                cues.append(If(f"{dish_id} reaches the round budget and no decision is open",
                               f"run 86 add --dish {dish_id} --round-budget"))
            elif not run and not already:
                cues.append(If(f"the old worker's wait for {dish_id} is terminal and {dish_id} may take a fix round",
                               f"run dish {dish_id} --state in-progress"))
        elif trigger.verdict == "pass":
            cues.append(See(pass_words(snapshot, dish)))
        elif trigger.verdict == "blocked" and failure != "fixable":
            cues.append(If(f"only the user can unblock {dish_id}",
                           f"ask with 86 add --dish {dish_id} --question --options --default"))
        return cues
    if wake == "checks":
        dish = dish_by_id(snapshot, dish_id)
        cues = [See(f"Run land for holder {holder_of(snapshot, dish_id)}.")]
        if dish.get("pr"):
            cues.append(See(f"watch_pull_request {dish['pr']}."))
        cues.append(land_follow(dish_id))
        if trigger.result == "fail" and failure != "stuck" and snapshot.reporting == "digest":
            cues.append(If("this thread cannot fix the failed check",
                           f"run next checks {dish_id} --result fail --failure stuck"))
        return cues
    if wake == "merge":
        dish = dish_by_id(snapshot, dish_id)
        run = maybe_run(snapshot, ("dish", dish_id, "--state", "merged"))
        cues = [run] if run else [See(merge_why(snapshot, dish))]
        cues.append(See(housekeeping(dish)))
        return cues
    if wake == "liveness":
        cues = [See("watch")]
        for dish in snapshot.dishes:
            if dish["state"] == "queued":
                cues.append(land_follow(dish["id"]))
        cues.append(pause_if())
        cues.append(If("watch printed no work in progress", "run set --schedule liveness="))
        return cues
    if wake in ("intake", "morning"):
        if snapshot.intake:
            listed = ", ".join(snapshot.intake)
            cues = [See(f"Intake sources are {listed}. File tickets from those sources with ticket add.")]
        else:
            cues = [See("No intake source is recorded. File a user request with ticket add when the message asks for work.")]
        if wake == "morning":
            cues.append(See("Morning service is not a report."))
        return cues
    if wake == "evening":
        return [See("Write the evening report for this reporting level. At digest, plain sentences and the closeout path. "
                    "Otherwise at most three sentences and then the close output.")]
    if wake == "user":
        first = "Answer the question first." if trigger.question else "Answer the user first."
        return [See(first), See("File the work the message asks for with ticket add.")]
    if wake == "admin":
        text = request_text(snapshot, trigger)
        cues = [See(f"{trigger.request}: {text}")]
        if text.startswith("from-user "):
            cues.append(See(f"Treat {trigger.request} as a message from the user. File its work with ticket add --request {trigger.request}. "
                            f"Answer with the reply line."))
        elif text.startswith("answer "):
            cues.append(See(f"Run 86 answer for the decision {trigger.request} names."))
        elif text.startswith("reports-to "):
            cues.append(See(f"Run set --reports-to with the thread {trigger.request} names, or clear it when the thread is none."))
        elif text.startswith("ruling "):
            cues.append(See(f"Read the ruling {trigger.request} names. Carry it out when it is in force."))
        else:
            cues.append(See(f"{text} matches no row of the Requests table."))
        cues.append(If(f"the action for {trigger.request} is done", f"run inbox done {trigger.request}"))
        return cues
    return []


def older_than_hour(at, moment):
    try:
        stamp = datetime.fromisoformat(at)
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return moment - stamp > timedelta(hours=1)


def admin_sends(snapshot, trigger, failure, after_close):
    """Owed event lines for the admin thread. report and drained follow the close cue.

    A blocked row is an admin event only while that ticket's rail state is waiting.
    """
    if not snapshot.reports_to:
        return []
    thread = snapshot.reports_to
    name = snapshot.restaurant

    def send(line):
        return See(f"t3_thread_send {thread} mode queue: {line}")

    if after_close:
        cues = []
        if trigger.wake == "evening":
            cues.append(send(f"report {name}: the path close --to-file prints"))
        if is_drained(snapshot, trigger):
            cues.append(send(f"drained {name}: the path close --to-file prints"))
        return cues
    cues = []
    if merge_accepted(snapshot, trigger):
        dish = dish_by_id(snapshot, trigger.dish)
        title = dish.get("summary") or trigger.dish
        pr = f" [{dish['pr']}]" if dish.get("pr") else ""
        cues.append(send(f"merged {name}: {title}{pr}"))
    if trigger.wake == "review" and trigger.verdict in ("send-back", "pass-with-edits", "blocked"):
        cues.append(send(f"sent-back {name} {trigger.dish}: {trigger.verdict}"))
    if decision_holds(snapshot, trigger, failure):
        questions, _, _ = fresh_decision_ids(snapshot)
        if questions:
            row = next(item for item in snapshot.decisions if item["id"] == lowest_id(questions, snapshot.warn))
            cues.append(send(f"decision {name} {row['id']}: {row['question']} Options: {row['options']}. Default: {row['default']}."))
        else:
            cues.append(send(f"decision {name}: sent after 86 add prints the id"))
    if failure == "stuck":
        cues.append(send(f"failed {name}: cannot fix this"))
    waiting = {row["id"] for row in snapshot.tickets if row["state"] == "waiting"}
    for ident, at in snapshot.blocked_since:
        if ident not in waiting or not older_than_hour(at, snapshot.moment):
            continue
        cues.append(If(f"watch still shows {ident} blocked",
                       f"t3_thread_send {thread} mode queue clientRequestId blocked:{snapshot.place}/{ident}: "
                       f"blocked {name} {ident}: waiting since {at}. Lease ids are not in the store."))
    if from_user_request(snapshot, trigger):
        cues.append(send(f"reply {name}: sent after the answer"))
    return cues


def tail_cues(snapshot, trigger, failure, reply):
    named = {trigger.dish} if trigger.dish and trigger.wake in ("report-back", "review", "checks", "merge") else set()
    if trigger.wake == "liveness":
        named.update(dish["id"] for dish in snapshot.dishes if dish["state"] == "queued")
    cues = []
    for row in snapshot.decisions:
        if not row["dish"]:
            cues.append(See(f"{row['id']}: {row['question']}. Launch nothing until 86 answer {row['id']}."))
            continue
        cues.append(See(f"{row['dish']}: open decision {row['id']}: {row['question']}; "
                        f"launch no worker or verifier until 86 answer {row['id']}"))
    held = {row["dish"] for row in snapshot.decisions if row["dish"]}
    for dish_id, sentence in snapshot.progress:
        if dish_id not in named and dish_id not in held:
            cues.append(See(sentence))
    for dish_id, where in snapshot.report_gaps:
        if dish_id in held:
            continue
        cues.append(If(f"reports/{dish_id}.md is absent or older than the last attempt start",
                       f"read the worker thread ({where}) and get the report"))
    seen = set()
    for debt in snapshot.debts:
        run = maybe_run(snapshot, ("86", "add", "--dish", debt.dish, "--round-budget"))
        if run and run.argv not in seen:
            seen.add(run.argv)
            cues.append(run)
    for dish in snapshot.dishes:
        if dish["state"] != "passed" or dish["id"] in named:
            continue
        cues.append(See(f"{dish['id']}: passed, not submitted"))
        if dish.get("sha"):
            cues.append(If(f"the landing queue has an entry for {dish['id']}",
                           f"run land.py status --holder {holder_of(snapshot, dish['id'])} --sha {dish['sha']}"))
    queued = [dish for dish in snapshot.dishes if dish["state"] == "queued"]
    for dish in queued:
        if dish["id"] in named:
            continue
        cues.append(See(f"Run land for {dish['id']}."))
        cues.append(land_follow(dish["id"]))
    claiming = trigger.dish if merge_accepted(snapshot, trigger) else ""
    for event in snapshot.events:
        if event["kind"] == "dish" and event["state"] == "merged" and event["id"] != claiming:
            cues.append(See(f"{event['id']} merged and not closed out"))
    if any(row["state"] == "waiting" for row in snapshot.tickets):
        cues.append(See("Run startable and take the first ticket it prints as startable, "
                        "with every ticket it prints as rides with that ticket."))
    if queued and trigger.wake != "liveness":
        cues.append(pause_if())
    cues.extend(admin_sends(snapshot, trigger, failure, False))
    if reply.close:
        if snapshot.close_refusal:
            cues.append(See(snapshot.close_refusal))
        else:
            cues.append(Run(reply.close))
    cues.extend(admin_sends(snapshot, trigger, failure, True))
    return cues


def inbox_cues(snapshot):
    bad = [(name, problem) for name, ok, problem in snapshot.inbox if not ok]
    if bad:
        return [See(f"inbox/{name} {problem}, so inbox take would stop") for name, problem in bad]
    run = maybe_run(snapshot, ("inbox", "take"))
    return [run] if run else []


def compile_sheet(snapshot, trigger, failure, unsent):
    """The cues for this wake. The reply's close line comes before a report or drained line. It is a run when close would exit 0, and a see of the malformed row when close would refuse."""
    reply = reply_for(snapshot, trigger, failure, unsent)
    return [*inbox_cues(snapshot), *prefix_cues(snapshot, trigger, failure), *tail_cues(snapshot, trigger, failure, reply)]


def trigger_from(args):
    return Trigger(args.wake or "unspecified", getattr(args, "dish", "") or "", getattr(args, "request", "") or "",
                   getattr(args, "verdict", "") or "", getattr(args, "result", "") or "", bool(getattr(args, "question", False)))


def next_text(restaurant, args):
    """Print the steps this wake owes and one reply. The flag makes a write or land.py raise."""
    seam.set("READ_ONLY", True)
    try:
        trigger = trigger_from(args)
        failure = getattr(args, "failure", None)
        unsent = getattr(args, "unsent", None)
        with restaurant.read_lock(create=False):
            block = restaurant.owner_block()
            if block:
                raise BrigadeError(block)
            if trigger.dish:
                restaurant.find("dishes.tsv", trigger.dish)
            if trigger.request:
                pending = {path.stem for path in request_files(restaurant)[0]}
                if trigger.request not in pending:
                    raise BrigadeError(f"no request {trigger.request} in the inbox")
            snapshot = load_snapshot(restaurant, datetime.now(timezone.utc))
        reply = reply_for(snapshot, trigger, failure, unsent)
        return format_sheet(snapshot, trigger, compile_sheet(snapshot, trigger, failure, unsent), reply)
    finally:
        seam.set("READ_ONLY", False)
