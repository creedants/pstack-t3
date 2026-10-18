import re
from datetime import datetime
from brigade_lib.shared import BrigadeError, clean
from brigade_lib.store import LEASED_STATES

ATTEMPT_AFTER = {"sent-back": "fix", "queued": "bounce"}


def item_escalation(mode, reason):
    if mode is None:
        if reason is not None:
            raise BrigadeError("--reason goes with --mode full")
        return None
    if mode == "light":
        raise BrigadeError("an item's mode only moves to full; change the coordinator with set --mode light")
    if mode != "full":
        raise BrigadeError(f"--mode takes full, got {mode!r}")
    if not clean(reason):
        raise BrigadeError('--mode full needs --reason "<one line>"')
    return clean(reason)


def latest_mode_note(events, ident):
    notes = [event["note"] for event in events if event["kind"] == "mode" and event["id"] == ident]
    return notes[-1] if notes else None


def record_escalation(restaurant, ident, reason):
    restaurant.fence()
    _, dish = restaurant.find("dishes.tsv", ident)
    if dish["state"] not in LEASED_STATES:
        raise BrigadeError(f"{ident} is {dish['state']}; only open work moves to full mode")
    recorded = latest_mode_note(restaurant.rows("log.tsv"), ident)
    if recorded is not None:
        return recorded
    restaurant.log("mode", ident, "full", reason)
    return reason


def menu_purpose(restaurant):
    text = (restaurant.dir / "menu.md").read_text()
    match = re.search(r"^## Purpose\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    purpose = match.group(1).strip() if match else ""
    if not purpose or purpose.startswith("What this restaurant exists to achieve"):
        raise BrigadeError("menu.md has no purpose yet; write the Purpose section before briefing a worker")
    return purpose


def attempt_kind(events, ident):
    kind = "first"
    for event in events:
        if event["kind"] == "dish" and event["id"] == ident:
            kind = ATTEMPT_AFTER.get(event["state"], kind)
    return kind


def attempt_starts(restaurant, dish):
    """When each attempt started: the dish's in-progress log rows, oldest first.

    Replacing the worker appends that row again without leaving in-progress.
    """
    moves = [row["at"] for row in restaurant.rows("log.tsv") if row["kind"] == "dish" and row["id"] == dish["id"] and row["state"] == "in-progress"]
    return [datetime.fromisoformat(at) for at in moves or [dish["at"]]]
