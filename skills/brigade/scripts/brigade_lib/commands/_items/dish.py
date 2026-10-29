import json
import re
import subprocess
import sys
from pathlib import Path
from brigade_lib import full_send, seam
from brigade_lib.commands._items.fire import add_paths_alone
from brigade_lib.commands.tools import current_brief_mode
from brigade_lib.items import ATTEMPT_AFTER, announce_archive, drop, item_escalation, record_escalation, retired_worker_threads, workers_full
from brigade_lib.leases import fragment_lines
from brigade_lib.reviews import fix_round_refusal, pass_check, write_threads
from brigade_lib.scope import gate_outside, gate_recheck, widen
from brigade_lib.shared import BrigadeError, clean, stored
from brigade_lib.commands.once import Once
from brigade_lib.store import COUNTED_STATES, DISH_STATES, WORKER_ROLES, roles_script
from brigade_lib.wake import record_worker_end
from brigade_lib._reviews.threads import cooldown_seats, moment_of, record_cooldown, retry_chain
from brigade_lib._store.settings import cooldown_minutes
from brigade_lib._watch.progress import attempt_count
from brigade_lib._watch.seats import cooldown_end, require_seat

COOLDOWN_FLAGS = ("error", "role", "author", "catalog", "parent", "options")


DISH_FLAGS = ("state", "task", "thread", "branch", "pr", "sha", "timebox", "reported", "worker_ended",
              "lease", "paths", "add_paths", "stopped", "mode", "reason")


def _given(args, name):
    value = getattr(args, name, None)
    return value is not None and value is not False


def usage_limit_refusal(args):
    """The sentence that refuses the usage-limit flag family, or None when the flags are in order.

    --usage-limit takes the six flags the backup run needs and no other dish flag, and each of those
    six is refused without it.
    """
    if args.usage_limit is not None:
        return "--usage-limit takes no other dish flag" if any(_given(args, name) for name in DISH_FLAGS) else None
    for name in COOLDOWN_FLAGS:
        if _given(args, name):
            return f"--{name} takes --usage-limit"
    return None


def error_text(path):
    """The error text --error names. `-` reads standard input, and any other value reads that file."""
    if path in (None, "-"):
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise BrigadeError(f"--error {error}") from None


def backup_payload(argv, catalog, text):
    """The JSON object roles.py backup prints, run with the error text on its standard input.

    A nonzero exit names the last non-empty stderr line, and a traceback frame counts as no message.
    """
    try:
        result = subprocess.run(argv, input=text.encode(), capture_output=True)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise BrigadeError(f"roles.py backup failed on {catalog}: {type(error).__name__}: {error}") from None
    try:
        out_text = result.stdout.decode("utf-8")
        err_text = result.stderr.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BrigadeError(f"roles.py backup failed on {catalog}: {type(error).__name__}: {error}") from None
    if result.returncode == 0:
        try:
            payload = json.loads(out_text)
        except ValueError as error:
            raise BrigadeError(f"roles.py backup printed no JSON object: {error}") from None
        if not isinstance(payload, dict):
            raise BrigadeError("roles.py backup printed no JSON object")
        return payload
    reason = ""
    for line in err_text.splitlines():
        if line.strip():
            reason = line.strip()
    rest = reason.removeprefix("error: ")
    if not rest or "Traceback" in rest or re.search(r'File ".+", line \d+, in ', rest):
        raise BrigadeError(f"roles.py backup failed on {catalog}: exit {result.returncode} with no message")
    raise BrigadeError(f"roles.py backup failed on {catalog}: {rest}")


def seat_word(seat):
    """The `provider/model` words for one seat object roles.py printed."""
    if not isinstance(seat, dict) or not seat.get("providerInstanceId") or not seat.get("model"):
        raise BrigadeError("roles.py backup printed no seat for its decision")
    return f"{seat['providerInstanceId']}/{seat['model']}"


def target_word(seat):
    """The complete target `roles.py backup` printed: provider/model, and its options JSON when it names options."""
    words = seat_word(seat)
    options = seat.get("options") or {}
    return f"{words} options {json.dumps(options, sort_keys=True)}" if options else words


def retry_spent(restaurant, ident, role, chain):
    """True when a cooldown event for this dish, role, and retry chain already sent its one retry out."""
    spent = False
    for row in restaurant.rows("log.tsv"):
        if row["kind"] != "cooldown" or row["id"] != ident:
            continue
        try:
            note = json.loads(row["note"])
        except ValueError:
            continue
        if isinstance(note, dict) and note.get("role") == role and note.get("chain") == chain:
            spent = bool(note.get("spent"))
    return spent


def record_usage_limit(restaurant, args):
    """Record a usage limit for the dish, ask roles.py backup for a seat unless it is a full-send item, and print one next step.

    The cooldown row and the log event are the only writes, and a park also blocks a dish in progress.
    """
    seat = require_seat(args.usage_limit)
    if not clean(args.role):
        raise BrigadeError("--usage-limit takes --role")
    if not (clean(args.catalog) and clean(args.parent)):
        raise BrigadeError("--usage-limit takes --catalog and --parent")
    text = error_text(args.error)
    with restaurant.read_lock(create=False):
        _, dish = restaurant.find("dishes.tsv", args.id)
        attempt = str(attempt_count(restaurant, dish))
        minutes = cooldown_minutes(restaurant.meta)
        live = cooldown_seats(restaurant, moment_of(seam.now()))
        mode = current_brief_mode(restaurant, dish)
        project_root = restaurant.meta["projectRoot"]
    provider, _, model = seat.partition("/")
    argv = [sys.executable, str(roles_script()), "backup", "--cwd", project_root, "--catalog", args.catalog,
            "--parent", args.parent, "--role", args.role, "--provider", provider, "--model", model]
    if args.options:
        argv += ["--options", args.options]
    for author in args.author or ():
        argv += ["--author", author]
    # roles.py reads --out as a provider, so one provider with two cooling seats is passed once.
    for name in dict.fromkeys(entry[0].partition("/")[0] for entry in live):
        argv += ["--out", name]
    if mode is not None:
        argv += ["--brief-mode", mode]
    # A full-send item resolves no seat through roles.py and has no backup seat, so a usage limit parks it.
    payload = {"decision": "park"} if dish["station"] == full_send.STATION else backup_payload(argv, args.catalog, text)
    decision = payload.get("decision")
    if decision == "not-usage-limit":
        return f"{args.id}: {seat} failed without a usage limit; respawn per Failure handling"
    start = seam.now()
    end = cooldown_end(text, start, minutes)
    replacement, seats, rule = "", [], {}
    if decision == "relaunch":
        replacement = target_word(payload.get("seat"))
    elif decision == "panel":
        seats = [target_word(item) for item in payload.get("seats") or ()]
        replacement = ", ".join(seats)
        rule = payload.get("rule") or {}
    with restaurant.checked():
        _, dish = restaurant.find("dishes.tsv", args.id)
        chain = retry_chain(restaurant, args.id)
        spent = retry_spent(restaurant, args.id, args.role, chain)
        parked = decision == "park" or spent
        record_cooldown(restaurant, args.id, attempt, seat, start, end)
        restaurant.log("cooldown", args.id, attempt, json.dumps({
            "seat": seat, "role": args.role, "start": start, "end": end,
            "chain": chain, "spent": spent or decision in ("relaunch", "panel"),
            "replacement": "" if parked else replacement, "seats": [] if parked else seats,
            "rule": {} if parked else rule}))
        if parked and dish["state"] == "in-progress":
            restaurant.update("dishes.tsv", args.id, "dish", state="blocked")
    if parked:
        # The earliest live end is the first moment any seat may be used again, and a resume names one.
        ends = [moment_of(end)] + [moment_of(entry[1]) for entry in live]
        earliest = min(found for found in ends if found is not None)
        return f"{args.id}: parked until {earliest.isoformat()}; launch nothing before then"
    if decision == "panel":
        step = f"panel on {', '.join(seats)}"
        if rule:
            step += f"; rule {json.dumps(rule, sort_keys=True)}"
        return f"{args.id}: {seat} cooling until {end}; {step}"
    return f"{args.id}: {seat} cooling until {end}; relaunch on {replacement}"

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
    p.add_argument("--usage-limit", metavar="PROVIDER/MODEL",
                   help="the seat that hit its usage limit; records a cooldown and prints one next step, and takes no other dish flag")
    p.add_argument("--error", help="with --usage-limit: the error text; missing or - reads stdin")
    p.add_argument("--role", help="with --usage-limit: the roles.py role of the failed seat")
    p.add_argument("--author", action="append", help="with --usage-limit: a model that wrote the diff; repeat for each")
    p.add_argument("--catalog", help="with --usage-limit: orchestrator_capabilities JSON, as on launch")
    p.add_argument("--parent", help="with --usage-limit: this thread's provider/model, as on launch")
    p.add_argument("--options", help="with --usage-limit: the failed seat's options JSON")


def handle_dish(restaurant, args):
    refusal = usage_limit_refusal(args)
    if refusal:
        raise BrigadeError(refusal)
    if args.usage_limit is not None:
        return record_usage_limit(restaurant, args)
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
