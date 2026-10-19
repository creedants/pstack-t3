import json
import re
import shlex
from brigade_lib._leases.caps import running_workers, worker_cap
from brigade_lib._leases.claims import claim_paths, lease_check
from brigade_lib.shared import slug

def blocked_waiting(restaurant):
    latest = {}
    for event in restaurant.rows("log.tsv"):
        if event["kind"] == "ticket":
            latest[event["id"]] = event
    found = []
    for ticket in restaurant.rows("rail.tsv"):
        event = latest.get(ticket["id"])
        if ticket["state"] != "waiting" or not event or event["state"] != "blocked":
            continue
        try:
            note = json.loads(event["note"])
        except json.JSONDecodeError:
            continue
        if isinstance(note, dict):
            found.append((ticket["id"], note))
    return found


def fire_command(ident, note):
    """Shell-quoted fire line. Each option is one --name=value word, except --doc-item which is a bare word, so a value that starts with - stays the value."""
    tickets = ",".join(note.get("tickets") or [ident])
    pairs = [
        ("tickets", tickets),
        ("station", note.get("station", "")),
        ("summary", note.get("summary", "")),
    ]
    if note.get("branch"):
        pairs.append(("branch", note["branch"]))
    if note.get("paths"):
        pairs.append(("paths", note["paths"]))
    pairs.append(("timebox", note.get("timebox", 60)))
    if note.get("work"):
        pairs.append(("kind", note["work"]))
    if note.get("ride_along"):
        pairs.append(("ride-along", ",".join(note["ride_along"])))
    if note.get("doc_item"):
        pairs.append(("doc-item", None))
    if note.get("reason"):
        pairs += [("mode", "full"), ("reason", note["reason"])]
    words = ["fire"]
    for name, value in pairs:
        words.append(f"--{name}" if value is None else f"--{name}={value}")
    return " ".join(shlex.quote(str(word)) for word in words)


def lease_block(project_root, holder_name, paths, fallback_kind="lease"):
    code, out, err = lease_check(project_root, holder_name, paths)
    if code == 0:
        return None
    held = re.findall(r"^(L\d+) held by (\S+) on ", out, re.M)
    if held:
        return "lease", "waiting on " + ", ".join(f"{lease} ({held_by})" for lease, held_by in held)
    room = re.search(r"(\d+ of \d+ changes in flight)", out)
    if room:
        return "repository", f"waiting for room in the repository ({room.group(1)})"
    return fallback_kind, f"waiting on the landing queue ({out or err or 'lease check failed'})"


def block_holds(meta, prefix, ident, note, running, cap, next_dish):
    """The block on a waiting ticket that holds now, as (kind, text), or None. Workers come first, as in fire."""
    if running >= cap:
        return "workers", f"waiting for a worker ({running} of {cap} running)"
    requested = note.get("paths") or ""
    if not requested:
        return None
    known = note.get("branch") or f"{prefix}/{next_dish.lower()}"
    return lease_block(meta.get("projectRoot", ""), f"{prefix}/{ident}", claim_paths(meta, requested, known, note.get("doc_item", False)).text, note.get("kind") or "lease")


def block_inputs(restaurant):
    """What a block recheck reads from the store. Call it under the store lock."""
    return blocked_waiting(restaurant), running_workers(restaurant), worker_cap(restaurant.meta), restaurant.next_id("dishes.tsv")


def recheck_blocks(restaurant, inputs):
    """Each blocked ticket, its note, and the block that holds now or None. land.py runs here, outside the store lock."""
    blocked, running, cap, next_dish = inputs
    meta = restaurant.meta
    prefix = slug(meta["restaurant"])
    return [(ident, note, block_holds(meta, prefix, ident, note, running, cap, next_dish)) for ident, note in blocked]


def holding_blocks(restaurant):
    """The blocks that still hold, by ticket, rechecked as watch does."""
    with restaurant.checked():
        inputs = block_inputs(restaurant)
    return {ident: held for ident, _, held in recheck_blocks(restaurant, inputs) if held}
