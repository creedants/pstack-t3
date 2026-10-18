import json
import re
from brigade_lib import seam
from brigade_lib._tickets.goals import open_goal
from brigade_lib._tickets.labels import ActsOn, GOALS_ONLY, read_labels
from brigade_lib.shared import BrigadeError, clean, same_text, slug
from brigade_lib.store import KIND_CHOICE, KINDS, LIVE_TICKET_STATES, NOTHING_HANDED, Restaurant, intake_owner, refuse_owned_intake, siblings

def intake_list(text):
    sources = []
    for item in text.split(","):
        item = clean(item)
        if item and item not in sources:
            sources.append(item)
    return sources


def base_source(source):
    """A taken ticket's source is `<source> (from <handoff id>)`, and a request's ticket `<source> (request A<n>)`.

    The intake source is the part before.
    """
    return re.split(r" \((?:from|request) ", source, maxsplit=1)[0]


def set_intake(restaurant, sources):
    meta = restaurant.meta
    refuse_owned_intake(restaurant.dir, meta.get("projectRoot"), sources)
    for source in meta.get("intake") or []:
        if source in sources:
            continue
        held = [row for row in restaurant.rows("rail.tsv")
                if base_source(row["source"]) == source and row["state"] in LIVE_TICKET_STATES]
        if held:
            raise BrigadeError(f"{held[0]['id']} from {source} is {held[0]['state']}; finish, drop, or move it before dropping {source} from intake")
    meta["intake"] = sources
    return meta


def handoff_id(directory, ticket):
    """Unique across the store: the source store's path under the store root, then the ticket."""
    return f"{directory.parent.name}/{directory.name}/{ticket}"


def inbox_file(directory, handoff):
    return directory / "inbox" / f"{handoff.replace('/', '~')}.json"


def sibling_rails(restaurant):
    """Each sibling's rail.tsv, read before this store's lock is taken.

    The caller's lock never held a sibling still, so reading it first is the same check.
    """
    rails = {}
    for name in siblings(restaurant.dir, restaurant.meta.get("projectRoot")):
        directory = (restaurant.dir.parent / name).resolve()
        rails[directory] = Restaurant(directory).rows("rail.tsv")
    return rails


def taken_row(rows, handoff):
    """The ticket filed for this handoff among these rail rows, if any."""
    if not rows:
        return None
    marker = f"(from {handoff})"
    return next((row for row in rows if row["source"].endswith(marker)), None)


def is_live(directory, row, rails):
    """waiting and assigned are live. moved is as live as the ticket it became. A handoff not yet taken is live."""
    if row["state"] != "moved":
        return row["state"] in LIVE_TICKET_STATES
    target = (directory.parent / row["dish"].removeprefix("to:")).resolve()
    if target in rails:
        rows = rails[target]
    elif not (target / "restaurant.json").is_file():
        return True
    else:
        rows = Restaurant(target).rows("rail.tsv")
    taken = taken_row(rows, handoff_id(directory, row["id"]))
    return taken is None or is_live(target, taken, rails)


def refuse_live_ref(restaurant, ref, rails):
    if not ref:
        return
    own = restaurant.dir.resolve()
    own_rows = restaurant.rows("rail.tsv")
    rails = {**rails, own: own_rows}
    stores = [(own, own_rows)]
    stores.extend((directory, rows) for directory, rows in rails.items() if directory != own)
    for directory, rows in stores:
        for row in rows:
            if row["ref"] == ref and is_live(directory, row, rails):
                where = "" if directory == own else f" in {directory.name}"
                raise BrigadeError(f"{ref} is already {row['id']}{where} ({row['state']}); nothing added")


def add_ticket(restaurant, summary, source, ref, request="", rails=None, again=False, priority="", paths=(), decision=False,
               kind="", goal=None):
    source, ref, request = clean(source), clean(ref), clean(request)
    meta = restaurant.meta
    if request:
        # A replayed request file must not file a second ticket, whatever state the first reached.
        for row in restaurant.rows("rail.tsv"):
            if row["source"].endswith(f"(request {request})"):
                raise BrigadeError(f"request {request} is already {row['id']}; nothing added")
    if source != "user":
        owner = intake_owner(restaurant.dir, meta.get("projectRoot"), source)
        if owner:
            raise BrigadeError(f"{owner} owns intake from {source}; ask it to file this and move it here")
        if source not in (meta.get("intake") or []):
            raise BrigadeError(f"no coordinator owns intake from {source}; the one that reads it runs set --intake {source}")
    refuse_live_ref(restaurant, ref, rails or {})
    if source == "user" and not ref and not again:
        for row in restaurant.rows("rail.tsv"):
            if row["state"] in LIVE_TICKET_STATES and same_text(row["summary"]) == same_text(summary):
                raise BrigadeError(f"same text as {row['id']} ({row['state']}); nothing added; "
                                   "pass --again to file a second ticket")
    if request:
        source = f"{source} (request {request})"
    with restaurant.guarded():
        # The kind and goal checks run after the owner fence and after each refusal above, so a ticket add that was
        # refused before kinds and goals existed is refused with the same words.
        if not kind:
            raise BrigadeError(f"ticket add needs --kind {KIND_CHOICE}")
        if goal is not None:
            open_goal(read_labels(restaurant, GOALS_ONLY), goal)
        return append_ticket(restaurant, summary, source, ref, priority, paths, decision, kind, goal or "")


def append_ticket(restaurant, summary, source, ref, priority="", paths=(), decision=False, kind="", goal=""):
    ident = restaurant.next_id("rail.tsv")
    restaurant.append("rail.tsv", {"id": ident, "at": seam.now(), "state": "waiting", "source": source,
                                   "ref": ref, "summary": summary, "priority": priority,
                                   "paths": ",".join(paths), "decision": "yes" if decision else "",
                                   "kind": kind, "goal": goal})
    restaurant.log("ticket", ident, "waiting", summary)
    return ident


def sibling_named(restaurant, to):
    """A sibling's directory name and restaurant.json, found by directory name or restaurant name."""
    meta = restaurant.meta
    names = siblings(restaurant.dir, meta.get("projectRoot"))
    name = to if to in names else next((n for n in names if slug(names[n].get("restaurant") or n) == slug(to)), None)
    if name is None:
        raise BrigadeError(f"{to} is not a sibling coordinator on {meta.get('projectRoot')}")
    return name, names[name]


def tell(name, meta):
    thread = (meta.get("thread") or "").strip()
    return f"tell thread {thread}" if thread else f"no thread recorded for {name}"


def move_ticket(restaurant, ident, to, rails):
    name, sibling = sibling_named(restaurant, to)
    target = (restaurant.dir.parent / name).resolve()
    destination = f"to:{name}"
    rows, row = restaurant.find("rail.tsv", ident)
    if row["state"] == "moved" and row["dish"] != destination:
        raise BrigadeError(f"{ident} is already moved to {row['dish'].removeprefix('to:')}")
    if row["state"] not in ("waiting", "moved"):
        raise BrigadeError(f"{ident} is {row['state']}; only a waiting ticket moves")
    if row["state"] == "waiting":
        # State and destination commit in one rewrite, so the ref stays live here from this point on.
        row["state"], row["dish"] = "moved", destination
        restaurant.save_rows("rail.tsv", rows)
    if not any(event["kind"] == "ticket" and event["id"] == ident and event["state"] == "moved"
               for event in restaurant.rows("log.tsv")):
        restaurant.log("ticket", ident, "moved", f"{row['summary']} (to {name})")
    handoff = handoff_id(restaurant.dir, ident)
    target_rows = rails.get(target)
    if target_rows is None:
        target_rows = Restaurant(target).rows("rail.tsv")
    # The kind goes with the ticket. The goal stays, because a goal id names a row of this store's goals.tsv only.
    label = read_labels(restaurant, ActsOn(tickets=(ident,))).tickets[ident]
    if taken_row(target_rows, handoff) is None:
        restaurant.publish(inbox_file(target, handoff), json.dumps({
            "handoff": handoff, "summary": row["summary"], "source": base_source(row["source"]), "ref": row["ref"],
            "priority": row["priority"], "paths": row["paths"], "decision": row["decision"],
            **({"kind": label.kind} if label.kind else {}),
        }, indent=2) + "\n")
    left = f"; it leaves {label.goal} here and arrives unassigned" if label.goal else ""
    return f"{ident} moved to {name}; {tell(name, sibling)}{left}"


def take_tickets(restaurant):
    lines = []
    for path in sorted((restaurant.dir / "inbox").glob("*.json")):
        try:
            handoff = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            raise BrigadeError(f"{path} is not valid JSON: {error}") from error
        source = handoff["handoff"]
        row = taken_row(restaurant.rows("rail.tsv"), source)
        if row is None:
            rows = restaurant.rows("rail.tsv")
            given = handoff.get("kind") or ""
            kind = given if given in KINDS else ""
            row = {"id": restaurant.next_id("rail.tsv"), "at": seam.now(), "state": "waiting",
                   "source": f"{handoff['source']} (from {source})", "ref": handoff["ref"], "summary": handoff["summary"],
                   **{key: handoff.get(key, "") for key in ("priority", "paths", "decision")}, "kind": kind}
            if given and not kind:
                # The warning comes before the write, so a take killed after the write has printed it.
                restaurant.warn(f"{row['id']} came from {source} with kind {given!r}, which is not one this version knows, "
                                f"so it has no kind; run ticket set {row['id']} --kind {KIND_CHOICE}")
            restaurant.save_rows("rail.tsv", rows + [row])
        if not any(event["kind"] == "ticket" and event["note"] == f"from {source}" for event in restaurant.rows("log.tsv")):
            restaurant.log("ticket", row["id"], "waiting", f"from {source}")
        restaurant.remove(path)
        lines.append(f"{row['id']} from {source}: {row['summary']}")
    return lines


def request_files(restaurant):
    """Request files the executive admin published into this inbox, oldest first, as (pending, finished).

    A file whose id has an inbox-done row is finished. request --republish reads completions before
    the admin's lock, so it can publish a request inbox done finished meanwhile. Nothing counts that file.
    """
    found = sorted((path for path in (restaurant.dir / "inbox").glob("A*.line") if re.fullmatch(r"A\d+", path.stem)),
                   key=lambda path: int(path.stem[1:]))
    finished = finished_requests(restaurant)
    return [path for path in found if path.stem not in finished], [path for path in found if path.stem in finished]


def finished_requests(restaurant):
    return {row["id"] for row in restaurant.rows("log.tsv") if row["kind"] == "inbox-done"}


def take_inbox(restaurant):
    """File handed tickets, then print each request waiting for inbox done. A finished request's file is deleted."""
    lines = take_tickets(restaurant)
    pending, finished = request_files(restaurant)
    for path in finished:
        restaurant.remove(path)
    lines.extend(f"{path.stem}: {path.read_text().strip()}" for path in pending)
    return "\n".join(lines) or NOTHING_HANDED


def finish_request(restaurant, ident):
    """Record the request as acted on, then delete its file. A rerun after a crash between the two finishes the delete."""
    path = restaurant.dir / "inbox" / f"{ident}.line"
    if ident not in finished_requests(restaurant):
        if not path.exists():
            raise BrigadeError(f"no request {ident} in the inbox")
        restaurant.log("inbox-done", ident, "done", path.read_text().strip())
    if path.exists():
        restaurant.remove(path)
    return f"{ident} done"


def handed_count(restaurant):
    return len(list((restaurant.dir / "inbox").glob("*.json")))
