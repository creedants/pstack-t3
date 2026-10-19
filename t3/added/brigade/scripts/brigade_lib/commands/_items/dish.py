from brigade_lib import seam
from brigade_lib.commands._items.fire import add_paths_alone
from brigade_lib.items import ATTEMPT_AFTER, announce_archive, drop, item_escalation, record_escalation, retired_worker_threads, workers_full
from brigade_lib.leases import fragment_lines
from brigade_lib.reviews import fix_round_refusal, pass_check, write_threads
from brigade_lib.scope import gate_outside, gate_recheck, widen
from brigade_lib.shared import BrigadeError, stored
from brigade_lib.commands.once import Once
from brigade_lib.store import COUNTED_STATES, DISH_STATES, WORKER_ROLES
from brigade_lib.wake import record_worker_end

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
        snapshot = gate_outside(restaurant, args.id, sha or "")
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


def dish_locked(restaurant, args, view):
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
