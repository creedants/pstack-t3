import sys
from brigade_lib import seam
from brigade_lib._leases.claims import holder, holder_leases
from brigade_lib.reviews import thread_binding, write_threads
from brigade_lib.shared import BrigadeError, clean, stored
from brigade_lib.store import IN_FLIGHT_STATES, WORKER_ROLES

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


def announce_archive(restaurant, dish_id, occasion, thread="", replaced="", only=None):
    """Print one `archive thread: <id>` line on standard error for each thread to archive at a finish point.

    `occasion` is "review" for the thread a `pass record` recorded, "replaced" for the worker a
    `dish --thread` replaced, and "closed" for every worker thread a merged or dropped item used.
    `thread` is the recorded id, or the id a new worker was bound to. `replaced` is the dish's thread
    cell read before that write. `only` holds the ids the caller allows to print after an unlocked step.
    None means no restriction.
    A set means only ids in it print. The coordinator's own thread, a thread another item in flight holds,
    a blank id, and a repeated id are left out. A review also leaves out the item's current worker and its
    open worker rows. The call writes no table.
    """
    rows = [row for row in restaurant.rows("threads.tsv") if row["dish"] == dish_id]
    workers = [row["thread"] for row in rows if row["role"] in WORKER_ROLES and row["thread"]]
    if occasion == "review":
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
    """Drop a dish and release every active lease of its holder, only after its worker stopped. A requested interrupt is not a stop."""
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
    for item in releases:
        result = seam.land_result(restaurant.meta["projectRoot"], "lease", "release", item, *restaurant.land_owner())
        error = result.stderr.strip().removeprefix("land: ")
        if result.returncode != 0 and not error.startswith(f"{item} is not active"):
            raise BrigadeError(f"{ident} not dropped: could not release {item}: {error}")
    with restaurant.checked():
        _, dish = restaurant.find("dishes.tsv", ident)
        later = {item for _, _, item in worker_bindings(restaurant, ident) - bindings}
        cell = stored("dishes.tsv", "thread", dish["thread"])
        if cell and cell != current:
            later.add(cell)
        evidence = [f"stopped {stopped}"] if stopped else []
        evidence += [f"released {', '.join(releases)}"] if releases else []
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
