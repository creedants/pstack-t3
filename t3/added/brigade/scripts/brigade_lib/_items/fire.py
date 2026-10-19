import json
import posixpath
import sys
from brigade_lib import seam
from brigade_lib._items.doc_gate import DocRefused, doc_policy, prepare_doc_fire
from brigade_lib._items.rounds import record_escalation
from brigade_lib.leases import Claim, ClaimError, Hold, claim_lease, claim_paths, fired_line, holder, missing_checkout_paths, refusal_kind, refill_of, release_lease, running_workers, worker_cap
from brigade_lib.reviews import fix_round_refusal
from brigade_lib.shared import BrigadeError, slug
from brigade_lib.store import KIND_CHOICE, STATION_KIND
from brigade_lib.tickets import ActsOn, Label, paths_of, priority_of, quoted_paths, read_labels, tracked_paths

BROAD_DIRECTORY_FILES = 5


def broad_path_lines(rows, paths, tracked):
    """Advisory lines for `--paths` entries that are directories holding more than BROAD_DIRECTORY_FILES tracked files.

    `rows` are the fired tickets' rail rows. The lease is unchanged. The helper returns no line when `tracked` is None.
    """
    if tracked is None:
        return []
    quoted = {path for row in rows for path in quoted_paths(row["summary"], tracked) if path in tracked.files}
    lines, seen = [], set()
    for raw in paths.split(","):
        entry = posixpath.normpath(raw.strip().replace("\\", "/")).lstrip("/")
        if entry in ("", ".", "..") or entry.startswith("../") or entry not in tracked.directories or entry in seen:
            continue
        seen.add(entry)
        under = sorted(path for path in tracked.files if path.startswith(entry + "/"))
        if len(under) <= BROAD_DIRECTORY_FILES:
            continue
        found = [path for path in under if path in quoted][:BROAD_DIRECTORY_FILES]
        if found:
            lines.append(f"fire: {entry} holds {len(under)} tracked files; the fired tickets name {', '.join(found)}")
        else:
            lines.append(f"fire: {entry} holds {len(under)} tracked files; no fired ticket names a file under it")
    return lines


def workers_full(restaurant):
    cap = worker_cap(restaurant.meta)
    running = running_workers(restaurant)
    if running >= cap:
        return f"{running} of {cap} workers running"
    return None


def block_note(kind, ids, station, summary, paths, timebox, branch="", reason="", work="", ride_along=(), doc_item=False):
    """`kind` is the kind of block. `work` is the --kind fire was given, and `ride_along` the ids --ride-along named."""
    note = {"kind": kind, "tickets": list(ids), "station": station, "summary": summary,
            "paths": paths, "timebox": timebox}
    if branch:
        note["branch"] = branch
    if reason:
        note["reason"] = reason
    if work:
        note["work"] = work
    if ride_along:
        note["ride_along"] = list(ride_along)
    if doc_item:
        note["doc_item"] = True
    return json.dumps(note, separators=(",", ":"))


def record_fire_paths(restaurant, ids, paths):
    """Write `paths` onto each id in `ids` that records no paths.

    An empty `paths` writes nothing. A ticket that already records paths keeps them.
    `paths` is the raw string `fire` was given.
    """
    if not paths:
        return
    wanted = set(ids)
    rows = restaurant.rows("rail.tsv")
    changed = False
    for row in rows:
        if row["id"] in wanted and not paths_of(row):
            row["paths"] = paths
            changed = True
    if changed:
        restaurant.save_rows("rail.tsv", rows)


def record_blocked(restaurant, ids, kind, station, summary, paths, timebox, branch="", reason="", work="", ride_along=(), doc_item=False):
    waiting = {row["id"] for row in restaurant.rows("rail.tsv") if row["state"] == "waiting"}
    note = block_note(kind, ids, station, summary, paths, timebox, branch, reason, work, ride_along, doc_item)
    record_fire_paths(restaurant, [ident for ident in ids if ident in waiting], paths)
    for ident in ids:
        if ident in waiting:
            restaurant.log("ticket", ident, "blocked", note)


def unfireable(restaurant, ids):
    # A ticket fired again is the same work, so an item that owes its round decision holds its tickets.
    for dish in restaurant.rows("dishes.tsv"):
        held = [ident for ident in ids if ident in dish["tickets"].split(",")]
        refusal = fix_round_refusal(restaurant, dish) if held else None
        if refusal:
            return f"{held[0]} is {dish['id']}'s ticket; {refusal}"
    for ident in ids:
        _, ticket = restaurant.find("rail.tsv", ident)
        if ticket["state"] != "waiting":
            return f"{ident} is {ticket['state']}, not waiting"
    return None


def fire_label(labels, rail, ids, station, kind, ride_along):
    """The kind and goal the new item takes, as (Label, None), or (None, refusal). `rail` is {ticket id: row}.

    Call it after unfireable has found each id in `ids` and found it waiting. The lead ticket is ids[0].
    The item takes the lead ticket's goal, and no goal when `ids` is empty.
    The kind is the first of these that gives one. The lead ticket's kind, then `kind`, then STATION_KIND[station].
    `kind` beside a lead ticket of another kind is refused.
    Each id in `ride_along` must be in `ids`, must not be the lead ticket, must have priority low, and must not be a feature.
    Each other ticket after the lead must have the lead ticket's goal or no goal.
    """
    lead = ids[0] if ids else None
    mine = labels.tickets[lead] if lead else Label()
    if kind and mine.kind and kind != mine.kind:
        return None, (f"{lead} has kind {mine.kind}, and --kind {kind} does not match it; "
                      f"drop --kind, or run ticket set {lead} --kind {kind} first when no item in flight names {lead}")
    found = mine.kind or kind or STATION_KIND.get(station, "")
    if not found:
        return None, ((f"{lead} has no kind" if lead else "no ticket gives this item a kind")
                      + f" and station {station} gives none; pass --kind {KIND_CHOICE}")
    for ident in ride_along:
        if ident not in ids:
            return None, f"--ride-along names {ident}, which is not in --tickets; add it there or drop it"
        if ident == lead:
            return None, (f"{ident} is the lead ticket, and the lead ticket cannot ride along; "
                          "put the ticket whose goal the item takes first in --tickets")
        if priority_of(rail[ident]) != "low":
            return None, (f"{ident} is {priority_of(rail[ident])}; only a low ticket rides along; "
                          f"start it on its own, or run ticket set {ident} --priority low")
        if labels.tickets[ident].kind == "feature":
            return None, f"{ident} is a feature, and a feature does not ride along; start it as its own item"
    for ident in ids[1:]:
        theirs = labels.tickets[ident].goal
        if ident not in ride_along and theirs and theirs != mine.goal:
            return None, (f"{ident} is under {theirs} and the lead ticket {lead} is {'under ' + mine.goal if mine.goal else 'unassigned'}; "
                          f"name {ident} in --ride-along, which takes a low ticket that is not a feature, or start it on its own")
    return Label(found, mine.goal), None


def checked_fire_label(restaurant, ids, station, kind, ride_along):
    """fire_label on the store as it is now, read under the caller's lock.

    read_labels prints the warnings of goals.tsv and of the tickets in `ids`.
    """
    labels = read_labels(restaurant, ActsOn(tickets=tuple(ids)))
    rail = {row["id"]: row for row in restaurant.rows("rail.tsv")}
    return fire_label(labels, rail, ids, station, kind, ride_along)


def spend_hold(restaurant, label, ids=()):
    """Why a spend control holds this item, or None. `label` is the kind and goal the label check gave.

    `ids` are the selected tickets; a paused goal of any one of them holds the fire, so a
    paused goal starts nothing whatever the item's label.
    """
    labels = read_labels(restaurant, ActsOn(items=(), tickets=()))
    for ident in ids:
        ticket_label = labels.tickets.get(ident, Label())
        goal = ticket_label.goal
        if goal and labels.goals.get(goal) is not None and labels.goals[goal].state == "paused":
            return Hold("paused", "its goal is paused")
    return refill_of(restaurant.meta, restaurant.rows("dishes.tsv"), labels).hold(label)


def write_lead_kind(restaurant, ids, kind):
    """Write `kind` into the kind cell of the lead ticket, ids[0], when the cell holds another value. It logs nothing.

    fire calls it before it appends the item row. A fire killed after it leaves no item whose kind its lead ticket lacks.
    """
    rows = restaurant.rows("rail.tsv")
    changed = [row for row in rows if row["id"] in ids[:1] and row["kind"] != kind]
    for row in changed:
        row["kind"] = kind
    if changed:
        restaurant.save_rows("rail.tsv", rows)


def fire(restaurant, ids, station, task, thread, branch, summary, timebox, paths, reason=None, kind="", ride_along=(), doc_item=False):
    """Start one item for the tickets in `ids`. The item takes the kind and goal fire_label gives.

    The label check runs twice, each time under the store lock. It covers kind, goal, and ride-along rules. Both runs come
    before the worker cap. The first run comes after the owner check and unfireable, so a fire that one of those refuses
    gets the words it got before the label check. It comes before the lease claim, so a fire it refuses claims no lease.
    The second run sees a change made while the claim ran.
    Each run is followed by the spend controls, which come after the label check and before the worker cap. A control
    hold fires nothing, claims no lease in the first phase, and releases a lease the second phase claimed.
    The lead ticket's kind cell takes the item's kind in a rewrite of its own, before the item row is appended. A fire
    killed between its writes leaves no item whose kind its lead ticket lacks. No other kind or goal cell
    of a ticket changes, so a ticket that rides along keeps its own.
    """
    while True:
        meta = restaurant.meta
        project_root = meta.get("projectRoot", "")
        policy = doc_policy(meta)
        prepared = prepare_doc_fire(project_root, paths, timebox, len(ids), doc_item, policy)
        with restaurant.checked():
            restaurant.fence()
            refusal = unfireable(restaurant, ids)
            if refusal:
                raise BrigadeError(refusal)
            gate = prepared.decide()
            if isinstance(gate, DocRefused):
                raise BrigadeError(gate.line)
            dish = restaurant.next_id("dishes.tsv")
            known = branch or f"{slug(restaurant.meta['restaurant'])}/{dish.lower()}"
            claimed = claim_paths(meta, paths, known, doc_item) if paths else Claim("", ())
            leased = claimed.text
            added = claimed.added
            label, refusal = checked_fire_label(restaurant, ids, station, kind, ride_along)
            if refusal:
                raise BrigadeError(refusal)
            held = spend_hold(restaurant, label, ids)
            if held:
                raise BrigadeError(f"nothing fired: {held.words}")
            full = workers_full(restaurant)
            if full:
                record_blocked(restaurant, ids, "workers", station, summary, paths, timebox, branch, reason, kind, ride_along,
                               doc_item)
                raise BrigadeError(f"nothing fired: {full}")
        # land.py can wait on the landing database, so the claim runs outside the store lock.
        # Another command may fire or take the tickets meanwhile, so the checks run again before the write.
        try:
            lease = claim_lease(restaurant, dish, leased) if leased else ""
        except ClaimError as error:
            with restaurant.checked():
                record_blocked(restaurant, ids, refusal_kind(str(error)), station, summary, paths, timebox, branch, reason,
                               kind, ride_along, doc_item)
            raise BrigadeError(f"nothing fired: {error}") from error
        prepared = prepare_doc_fire(project_root, paths, timebox, len(ids), doc_item, policy)
        with restaurant.checked():
            refusal = unfireable(restaurant, ids)
            gate = prepared.decide()
            if not refusal and isinstance(gate, DocRefused):
                refusal = gate.line
            current = restaurant.next_id("dishes.tsv")
            label = None
            if not refusal:
                label, refusal = checked_fire_label(restaurant, ids, station, kind, ride_along)
            if label:
                hold = spend_hold(restaurant, label, ids)
                if hold:
                    label, refusal = None, hold.words
            full = None if refusal else workers_full(restaurant)
            if label and not full and current == dish:
                write_lead_kind(restaurant, ids, label.kind)
                restaurant.append("dishes.tsv", {"id": dish, "at": seam.now(), "state": "in-progress", "station": station,
                                                 "tickets": ",".join(ids), "task": task, "thread": thread,
                                                 "branch": branch, "summary": summary, "timebox": timebox,
                                                 "lease": lease, "paths": leased, "kind": label.kind, "goal": label.goal})
                restaurant.log("dish", dish, "in-progress", summary)
                gate.record(restaurant, dish)
                if reason:
                    record_escalation(restaurant, dish, reason)
                record_fire_paths(restaurant, ids, paths)
                rows = restaurant.rows("rail.tsv")
                for row in rows:
                    if row["id"] in ids:
                        row["state"], row["dish"] = "assigned", dish
                restaurant.save_rows("rail.tsv", rows)
                for row in rows:
                    if row["id"] in ids:
                        restaurant.log("ticket", row["id"], "assigned", row["summary"])
                if paths:
                    for stale in missing_checkout_paths(restaurant.meta.get("projectRoot", ""),
                                                        [part for part in paths.split(",") if part]):
                        print(f"fire: {stale} is not in the checkout; a stale ticket path leases nothing real",
                              file=sys.stderr)
                    fired = [row for row in rows if row["id"] in ids]
                    for line in broad_path_lines(fired, paths, tracked_paths(restaurant.meta.get("projectRoot", ""))):
                        print(line, file=sys.stderr)
                return fired_line(dish, lease, holder(restaurant, dish), added)
            if full and not refusal:
                record_blocked(restaurant, ids, "workers", station, summary, paths, timebox, branch, reason, kind, ride_along,
                               doc_item)
                refusal = full
        if lease:
            release_lease(restaurant, lease)
        if refusal:
            raise BrigadeError(f"nothing fired: {refusal}")
