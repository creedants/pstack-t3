import json
from typing import NamedTuple

from brigade_lib._leases.claims import land_result
from brigade_lib._lookback.state import moment
from brigade_lib._notes import read_notes
from brigade_lib.shared import BrigadeError
from brigade_lib.store import HELD_LOCKS

MAX_TEXT = 200
NOTE_CAP = 50


CONTROLS = {code: " " for code in (*range(32), *range(127, 160), 0x2028, 0x2029)}


def flatten(text, limit=MAX_TEXT):
    """One line: control characters and line separators become spaces, then the text is trimmed and cut to limit characters."""
    one = str(text).translate(CONTROLS).strip()
    return one[:limit].rstrip()


def inside(at, bound):
    """True when at is later than bound, or bound is None."""
    return bound is None or at > bound


class Queue(NamedTuple):
    entries: tuple
    problem: str


UNREAD = Queue((), "the queue could not be read")


def read_queue(project_root, prefix):
    if HELD_LOCKS:
        raise BrigadeError(f"look-back reads the queue; release {HELD_LOCKS[0]}'s store lock before it")
    try:
        result = land_result(project_root, "status", "--holder", prefix, "--json")
    except (BrigadeError, OSError):
        return UNREAD
    if result.returncode != 0:
        return UNREAD
    try:
        entries = json.loads(result.stdout)
    except ValueError:
        return UNREAD
    if not isinstance(entries, list):
        return UNREAD
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id") or not entry.get("state") or not entry.get("holder"):
            return UNREAD
    return Queue(tuple(entries), "")


def refusals(queue, prefix, seen):
    """The queue refusal entries of this holder prefix that no earlier look-back listed, oldest id first."""
    if queue.problem:
        return []
    found = []
    for entry in queue.entries:
        holder = flatten(entry["holder"])
        if entry["state"] != "bounced" or not holder.startswith(prefix):
            continue
        ident = flatten(entry["id"])
        if ident in seen:
            continue
        found.append(
            {
                "id": ident,
                "token": f"queue:{ident}",
                "holder": holder,
                "reason": flatten(entry.get("note") or ""),
            }
        )
    found.sort(key=lambda item: int(item["id"][1:]))
    return found


class Found(NamedTuple):
    items: tuple
    newest: str


NONE = Found((), "")


def red_runs(restaurant, since):
    """The failed main runs in the window, newest first, one per workflow and since."""
    bound = moment(since) if since else None
    candidates = []
    for row in restaurant.rows("log.tsv"):
        if (row.get("kind") or "") != "trunk" or (row.get("state") or "") != "red":
            continue
        at = moment(row.get("at") or "")
        if at is None or not inside(at, bound):
            continue
        note = row.get("note") or ""
        try:
            data = json.loads(note)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            data = None
        if data is None:
            candidates.append(
                {
                    "at": row.get("at") or "",
                    "json": False,
                    "sha": "",
                    "workflow": "",
                    "url": "",
                    "since": "",
                    "note": flatten(note),
                    "token": "",
                }
            )
            continue
        url = flatten(data.get("url") or "")
        candidates.append(
            {
                "at": row.get("at") or "",
                "json": True,
                "sha": flatten(data.get("sha") or ""),
                "workflow": flatten(data.get("workflow") or ""),
                "url": url,
                "since": flatten(data.get("since") or ""),
                "note": "",
                "token": f"run:{url}" if url else "",
            }
        )
    candidates.sort(key=lambda item: moment(item["at"]), reverse=True)
    kept = []
    keys = set()
    for item in candidates:
        key = (item["workflow"], item["since"]) if item["json"] else f"note {item['note']}"
        if key in keys:
            continue
        keys.add(key)
        kept.append(item)
    newest = candidates[0]["at"] if candidates else ""
    return Found(tuple(kept), newest)


def notes(restaurant, since):
    """The set-aside notes in the window, newest first, every row kept."""
    bound = moment(since) if since else None
    rows = []
    for _path, item, at, text in read_notes(restaurant):
        stamp = moment(at)
        if stamp is None or not inside(stamp, bound):
            continue
        item = flatten(item)
        rows.append({"at": at, "item": item, "text": flatten(text), "token": f"note:{item}:{at}"})
    rows.sort(key=lambda row: (moment(row["at"]), row["item"]), reverse=True)
    newest = rows[0]["at"] if rows else ""
    return Found(tuple(rows), newest)
