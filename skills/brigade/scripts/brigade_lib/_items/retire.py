import sys
from datetime import datetime, timezone
from brigade_lib import seam
from brigade_lib._items.rounds import ATTEMPT_AFTER, attempt_starts
from brigade_lib._leases.claims import holder, holder_leases
from brigade_lib._reviews.threads import recorded_children
from brigade_lib.reviews import report_threads, thread_binding, worker_report, write_threads
from brigade_lib.shared import BrigadeError, clean, stored
from brigade_lib.store import CHILD_ROLES, IN_FLIGHT_STATES, WORKER_ROLES

def retired_worker_threads(restaurant, ident):
    """Worker thread ids this dish already used on an earlier attempt."""
    return [row["note"] for row in restaurant.rows("log.tsv")
            if row["kind"] == "worker" and row["id"] == ident and row["state"] == "retired" and row["note"]]


ARCHIVE_PREFIX = "archive thread: "


def other_items_threads(restaurant, dish_id):
    """The thread ids items other than `dish_id` hold while still in flight."""
    flying = {row["id"] for row in restaurant.rows("dishes.tsv")
              if row["id"] != dish_id and row["state"] in IN_FLIGHT_STATES}
    held = {row["thread"] for row in restaurant.rows("dishes.tsv")
            if row["id"] in flying and row["thread"]}
    held.update(row["thread"] for row in restaurant.rows("threads.tsv")
                if row["dish"] in flying and row["thread"])
    return held


def worker_bindings(restaurant, ident):
    """The binding of each worker row of the item with a non-empty thread, the identity `write_threads` de-duplicates on."""
    return {thread_binding(row) for row in restaurant.rows("threads.tsv")
            if row["dish"] == ident and row["role"] in WORKER_ROLES and row["thread"]}


def round_helpers(restaurant, dish_id, sha="", late=False):
    """Stored helper threads the worker report lists and the table holds for the reviewed attempt.

    A late verdict announces no helper. An empty SHA announces no helper. The report is current
    only when reports/<dish>.md exists and its mtime is at or after the last attempt start.
    Attempt starts come from brigade_lib._items.rounds.attempt_starts. The same rule decides
    report currency. A stale report announces no helper. A departure
    is a dish log row whose state is in ATTEMPT_AFTER. record_pass calls write_threads, then
    restaurant.update unless the verdict is late or a member. A send-back verdict that is not late
    and not a member writes helper rows before its own departure in the same command. A late verdict
    writes helper rows and adds no departure in the same command. Block, resume and worker replacement
    are not departures. An earlier helper row is
    ignored in the earlier-row check only when a selected departure lies at or after the start of
    that row own attempt and strictly before that row written time. When no worker row with that
    attempt holds a parsable start, any departure strictly before that row written time selects the
    row. The start of an attempt is the minimum parsable start among worker rows with that attempt
    number that hold a start. The start is None when no row qualifies. The selected departure is
    the departure that meets the applicable bounds for that row. The ignored binding is the row attempt, role
    and stored thread. All other earlier rows still suppress. Otherwise each child attempt comes
    from child_attempt through recorded_children with the SHA and late context.
    """
    if late:
        return []
    if not sha:
        return []
    _, dish = restaurant.find("dishes.tsv", dish_id)
    path = restaurant.dir / "reports" / f"{dish_id}.md"
    if not path.exists():
        return []
    written = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    start = attempt_starts(restaurant, dish)[-1]
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if written < start:
        return []
    body = worker_report(restaurant, dish_id)
    if not body:
        return []
    children, _ = report_threads(body)
    if not children:
        return []
    rows = [row for row in restaurant.rows("threads.tsv") if row["dish"] == dish_id]
    worker_attempts = [int(row["attempt"]) for row in rows if row["role"] in WORKER_ROLES]
    current = str(max(worker_attempts)) if worker_attempts else "1"
    departures = _departures(restaurant, dish_id)
    ignored = _late_written(rows, current, departures)
    return recorded_children(rows, children, sha, late, ignored)


def _stamp(text):
    """Parse an ISO timestamp as UTC."""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _attempt_start(rows, attempt):
    """Minimum parsable start among worker rows with this attempt that hold a start. It returns None when no row qualifies."""
    starts = []
    for row in rows:
        if row["role"] not in WORKER_ROLES:
            continue
        if str(row["attempt"]) != str(attempt):
            continue
        if not row["start"]:
            continue
        try:
            starts.append(_stamp(row["start"]))
        except ValueError:
            continue
    return min(starts) if starts else None


def _departures(restaurant, dish_id):
    """Departure times for the dish."""
    found = []
    for row in restaurant.rows("log.tsv"):
        if row["kind"] != "dish" or row["id"] != dish_id:
            continue
        if row["state"] not in ATTEMPT_AFTER:
            continue
        try:
            found.append(_stamp(row["at"]))
        except ValueError:
            continue
    return found


def _late_written(rows, current, departures):
    """Earlier helper bindings with a departure before their write and, when known, at or after their worker start."""
    found = set()
    for row in rows:
        if row["role"] not in CHILD_ROLES:
            continue
        if int(row["attempt"]) >= int(current):
            continue
        try:
            written = _stamp(row["at"])
        except ValueError:
            continue
        begun = _attempt_start(rows, row["attempt"])
        selected = False
        for departure in departures:
            if begun is not None and departure < begun:
                continue
            if departure < written and (begun is None or begun <= departure):
                selected = True
                break
        if selected:
            found.add((str(row["attempt"]), row["role"], stored("threads.tsv", "thread", row["thread"])))
    return found


def announce_archive(restaurant, dish_id, occasion, thread="", replaced="", only=None, sha="", late=False):
    """Print one `archive thread: <id>` line on standard error for each thread to archive at a finish point.

    `occasion` is "review" for the thread a `pass record` recorded and each helper thread its report lists,
    "replaced" for the worker a `dish --thread` replaced, and "closed" for every worker thread a merged or
    dropped item used. `thread` is the recorded id, or the id a new worker was bound to. `replaced` is the
    dish's thread cell read before that write. `only` holds the ids the caller allows to print after an
    unlocked step. None means no restriction.
    `sha` is the reviewed SHA. `late` marks a late verdict. A review announces helpers only when the
    report belongs to the reviewed attempt.
    A set means only ids in it print. The coordinator's own thread, a thread another item in flight holds,
    a blank id, and a repeated id are left out. A review also leaves out the item's current worker and its
    open worker rows. The call writes no table.
    """
    rows = [row for row in restaurant.rows("threads.tsv") if row["dish"] == dish_id]
    workers = [row["thread"] for row in rows if row["role"] in WORKER_ROLES and row["thread"]]
    if occasion == "review":
        if clean(thread):
            found = [thread, *round_helpers(restaurant, dish_id, sha, late)]
        else:
            found = [thread]
    elif occasion == "replaced":
        if replaced and replaced != thread:
            found = [replaced]
        else:
            back = [item for item in retired_worker_threads(restaurant, dish_id) if item and item != thread]
            if not back:
                back = [row["thread"] for row in rows
                        if row["role"] in WORKER_ROLES and row["end"] and row["thread"] and row["thread"] != thread]
            found = back[-1:]
    else:
        found = workers + [item for item in retired_worker_threads(restaurant, dish_id) if item not in workers]
    if occasion == "closed":
        _, dish = restaurant.find("dishes.tsv", dish_id)
        if dish["thread"] and dish["thread"] not in found:
            found.append(dish["thread"])
    own = clean(restaurant.meta.get("thread") or "")
    live = other_items_threads(restaurant, dish_id)
    excluded = {own} | set(live)
    if occasion == "review":
        _, dish = restaurant.find("dishes.tsv", dish_id)
        current = stored("dishes.tsv", "thread", dish["thread"])
        if current:
            excluded.add(current)
        for row in rows:
            if row["role"] in WORKER_ROLES and not row["end"] and row["thread"]:
                excluded.add(stored("threads.tsv", "thread", row["thread"]))
    seen = set()
    for item in found:
        item = stored("threads.tsv", "thread", item)
        if not item or item in excluded or item in seen:
            continue
        if only is not None and item not in only:
            continue
        seen.add(item)
        print(f"{ARCHIVE_PREFIX}{item}", file=sys.stderr)


def drop(restaurant, ident, stopped):
    """Drop a dish and release the lease its row records, unless it is submitted, plus every live active lease of its exact holder, only after its worker stopped. A requested interrupt is not a stop."""
    swept = holder_leases(restaurant.meta["projectRoot"], holder(restaurant, ident))
    with restaurant.checked():
        restaurant.fence()
        _, dish = restaurant.find("dishes.tsv", ident)
        lease = dish["lease"] if dish["state"] not in ("merged", "dropped") else ""
        releases = list(dict.fromkeys(item for item in [lease, *swept] if item))
        if releases and dish["thread"] and not stopped:
            raise BrigadeError(f"{ident} holds {', '.join(releases)} and its worker may still be running; "
                               "wait for its run with t3_thread_wait, then pass --stopped <run id>")
        observed = set()
        current = stored("dishes.tsv", "thread", dish["thread"])
        if current:
            observed.add(current)
        bindings = worker_bindings(restaurant, ident)
        observed.update(item for _, _, item in bindings)
        for item in retired_worker_threads(restaurant, ident):
            item = stored("threads.tsv", "thread", item)
            if item:
                observed.add(item)
    released = []
    for item in releases:
        result = seam.land_result(restaurant.meta["projectRoot"], "lease", "release", item, *restaurant.land_owner())
        error = result.stderr.strip().removeprefix("land: ")
        if result.returncode != 0 and not error.startswith(f"{item} is not active"):
            raise BrigadeError(f"{ident} not dropped: could not release {item}: {error}")
        if result.returncode == 0:
            released.append(item)
    with restaurant.checked():
        _, dish = restaurant.find("dishes.tsv", ident)
        later = {item for _, _, item in worker_bindings(restaurant, ident) - bindings}
        cell = stored("dishes.tsv", "thread", dish["thread"])
        if cell and cell != current:
            later.add(cell)
        evidence = [f"stopped {stopped}"] if stopped else []
        evidence += [f"released {', '.join(released)}"] if released else []
        note = f"{dish['summary']} ({'; '.join(evidence)})" if evidence else None
        restaurant.update("dishes.tsv", ident, "dish", note=note, state="dropped")
        write_threads(restaurant, ident, (), closing=True)
        announce_archive(restaurant, ident, "closed", only=observed - later)
        rows = restaurant.rows("rail.tsv")
        back = [row for row in rows if row["state"] == "assigned" and row["dish"] == ident]
        for row in back:
            row["state"], row["dish"] = "waiting", ""
        if back:
            restaurant.save_rows("rail.tsv", rows)
        for row in back:
            restaurant.log("ticket", row["id"], "waiting", f"{row['summary']} (back from dropped {ident})")
    if back:
        return f"{ident} dropped; {', '.join(row['id'] for row in back)} waiting again"
    return f"{ident} dropped"
