from typing import NamedTuple

from brigade_lib import seam
from brigade_lib._ci.github import fetch_runs
from brigade_lib._ci.ledger import append_runs, read_ledger
from brigade_lib._ci.setting import ci_target_of
from brigade_lib._ci.trigger import (ARMED, ASKING, CI_DECISION_KIND, CI_OPTIONS, CI_PATH, CI_REF,
                                      CI_SOURCE, COOLING, LEAVE_IT, RETRYING, STOPPED, TICKET_LIVE,
                                      _at, breach, phase_of)
from brigade_lib._reviews.decisions import append_decision
from brigade_lib.shared import MalformedTable, plural
from brigade_lib.store import Restaurant, siblings
from brigade_lib.tickets import (append_ticket, handoff_id, is_live, refuse_live_ref, taken_row)

COMMANDS = ("ci-time",)

LIMIT = 10

READ_FAILURES = (OSError, MalformedTable)


class Handoffs(NamedTuple):
    finished: dict
    unreadable: dict


class _Unreadable(Exception):
    """A store on the handoff chain could not be read."""


def _readable_rails(restaurant):
    rails, failures = {}, []
    for name in siblings(restaurant.dir, restaurant.meta.get("projectRoot")):
        directory = (restaurant.dir.parent / name).resolve()
        try:
            rails[directory] = Restaurant(directory).rows("rail.tsv")
        except READ_FAILURES as error:
            failures.append(error)
    return rails, failures


def _rail(target, rails):
    if target in rails:
        return rails[target]
    if not (target / "restaurant.json").is_file():
        return None
    try:
        return Restaurant(target).rows("rail.tsv")
    except READ_FAILURES as error:
        raise _Unreadable(f"{target.name}/rail.tsv") from error


def _log(directory):
    try:
        return Restaurant(directory).rows("log.tsv")
    except READ_FAILURES as error:
        raise _Unreadable(f"{directory.name}/log.tsv") from error


def _minutes(seconds):
    return f"{seconds / 60:.1f}"


def run_line(run):
    """The one dry-run and apply line for this fetched run."""
    line = (f"PR {run.pr}: {run.ci_seconds}s (about {_minutes(run.ci_seconds)} minutes), "
            f"{'clean' if run.clean else 'not clean'}")
    if run.lost:
        line += f", {run.lost} runner {'loss' if run.lost == 1 else 'losses'}"
    if run.time_limit:
        line += f", {plural(run.time_limit, 'time-limit stop')}"
    return line


def register(sub):
    p = sub.add_parser("ci-time", help="record the CI time of recent merged pull requests, and file one upkeep "
                                       "ticket when it stays over set --ci-target")
    p.add_argument("--dry-run", action="store_true", help="print what it would record and file; it changes no table")


def handle(restaurant, args):
    target = ci_target_of(restaurant.meta)
    if not target:
        return "ci-time is off; run set --ci-target <minutes> to turn it on"
    known = {run.pr for run in read_ledger(restaurant).runs}
    rails, failures = _readable_rails(restaurant)
    batch, error = fetch_runs(restaurant.meta["projectRoot"], known, LIMIT)
    if error is not None:
        return error
    args.ci_target = target
    args.ci_runs = batch.runs
    args.ci_notices = batch.notices
    args.ci_rails = rails
    handoffs = _finished(restaurant, {row["id"] for row in restaurant.rows("rail.tsv")
                                      if (row.get("source") or "") == CI_SOURCE}, rails)
    if failures and not handoffs.unreadable:
        raise failures[0]
    args.ci_finished = handoffs.finished
    args.ci_unreadable = handoffs.unreadable
    lock = restaurant.checked() if args.dry_run else restaurant.guarded()
    with lock:
        return seam.command(restaurant, args)


def _terminal_finish(restaurant, directory, row, rails):
    state = row.get("state") or ""
    if state in ("done", "dropped"):
        if directory == restaurant.dir.resolve():
            log = restaurant.rows("log.tsv")
        else:
            log = _log(directory)
        best = ""
        for event in log:
            if (event.get("kind") == "ticket" and event.get("id") == row["id"]
                    and event.get("state") in ("done", "dropped")):
                if not best or _at(event["at"]) > _at(best):
                    best = event["at"]
        return best or None
    if state != "moved":
        return None
    dish = row.get("dish") or ""
    if not dish.startswith("to:"):
        return None
    target = (directory.parent / dish[len("to:"):]).resolve()
    rows = _rail(target, rails)
    if rows is None:
        return None
    taken = taken_row(rows, handoff_id(directory, row["id"]))
    if taken is None:
        return None
    return _terminal_finish(restaurant, target, taken, rails)


def _finished(restaurant, idents, rails) -> Handoffs:
    own = restaurant.dir.resolve()
    rows = {row["id"]: row for row in restaurant.rows("rail.tsv")}
    finished, unreadable = {}, {}
    for ident in idents:
        row = rows.get(ident)
        if row is None:
            continue
        try:
            at = _terminal_finish(restaurant, own, row, rails)
        except _Unreadable as error:
            unreadable[ident] = str(error)
            continue
        if at is not None and (ident not in finished or _at(at) > _at(finished[ident])):
            finished[ident] = at
    return Handoffs(finished, unreadable)


def ticket_summary(found, target):
    """The summary of the upkeep ticket this breach files. It names both runs and the target."""
    return (f"CI time {found.newer.ci_seconds}s (PR {found.newer.pr}) and {found.older.ci_seconds}s "
            f"(PR {found.older.pr}) is over the {target}m target")


def decision_question(found, target):
    """The question of the decision this breach adds after a finished trigger. It names both runs."""
    return (f"CI time is still over the {target}m target ({found.newer.ci_seconds}s at PR {found.newer.pr}, "
            f"{found.older.ci_seconds}s at PR {found.older.pr}). Try again or leave it?")


def handle_locked(restaurant, args, view):
    if args.command != "ci-time":
        return None
    target = ci_target_of(restaurant.meta)
    if not target:
        return "ci-time is off; run set --ci-target <minutes> to turn it on"
    ledger = read_ledger(restaurant)
    have = {run.pr for run in ledger.runs}
    fresh = [run for run in args.ci_runs if run.pr not in have]
    if fresh and not args.dry_run:
        append_runs(restaurant, fresh)
    unreadable = dict(getattr(args, "ci_unreadable", None) or {})
    lines = [run_line(run) for run in args.ci_runs] + list(args.ci_notices)
    if unreadable:
        ident = sorted(unreadable)[0]
        return "\n".join([*lines, f"ci-time ticket {ident} is live; {unreadable[ident]} cannot be read; nothing filed"])
    tickets = restaurant.rows("rail.tsv")
    decided = restaurant.rows("86.tsv")
    own = restaurant.dir.resolve()
    rails = dict(getattr(args, "ci_rails", None) or {})
    rails.setdefault(own, tickets)
    live = {row["id"] for row in tickets
            if (row.get("source") or "") == CI_SOURCE and is_live(own, row, rails)}
    finished = dict(getattr(args, "ci_finished", None) or {})
    target_at = restaurant.meta.get("ciTargetAt") or ""
    phase = phase_of(tickets, decided, finished, target_at, live)
    ordered = tuple(sorted((*ledger.runs, *fresh), key=lambda run: (run.merged_at, run.pr)))
    since = ""
    for at in finished.values():
        if not since or _at(at) > _at(since):
            since = at
    found = breach(ordered, target, since)
    if found is None:
        return "\n".join([*lines, f"no breach of the {target}m target"])
    if phase == TICKET_LIVE:
        ident = sorted(live)[0]
        return "\n".join([*lines, f"ci-time ticket {ident} is live; nothing filed"])
    if phase == ASKING:
        ident = sorted(row["id"] for row in decided
                       if (row.get("kind") or "") == CI_DECISION_KIND and row["state"] == "open")[0]
        return "\n".join([*lines, f"ci-time decision {ident} is open; nothing filed"])
    if phase == STOPPED:
        return "\n".join([*lines, "ci-time is stopped; run set --ci-target <minutes> to start it again"])
    if phase == COOLING:
        question = decision_question(found, target)
        if args.dry_run:
            return "\n".join([*lines, f"would add a ci-time decision: {question}"])
        ident = append_decision(restaurant, dish="", question=question, options=CI_OPTIONS,
                                default=LEAVE_IT, kind=CI_DECISION_KIND)
        return "\n".join([*lines, f"added {ident}: {question}"])
    summary = ticket_summary(found, target)
    if args.dry_run:
        return "\n".join([*lines, f"would file a ci-time ticket: {summary}"])
    refuse_live_ref(restaurant, CI_REF, getattr(args, "ci_rails", None) or {})
    ident = append_ticket(restaurant, summary, CI_SOURCE, CI_REF, priority="normal",
                          paths=(CI_PATH,), kind="upkeep")
    return "\n".join([*lines, f"filed {ident}: {summary}"])
