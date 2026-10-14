import argparse
import fcntl
import fnmatch
import functools
import hashlib
import io
import json
import os
import posixpath
import re
import runpy
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from brigade_lib import seam
from brigade_lib.leases import ClaimError, claim_lease, holder, refusal_kind, release_lease, running_workers, with_fragment, worker_cap
from brigade_lib.reviews import fix_round_refusal, write_threads
from brigade_lib.shared import BrigadeError, clean, slug
from brigade_lib.store import KIND_CHOICE, LEASED_STATES, STATION_KIND
from brigade_lib.tickets import ActsOn, Label, paths_of, priority_of, read_labels, tracked_paths

ATTEMPT_AFTER = {"sent-back": "fix", "queued": "bounce"}


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


@dataclass(frozen=True)
class _SharedDoc:
    root: str
    kind: str


_CATALOG = (
    _SharedDoc("README.md", "file"),
    _SharedDoc("docs/guide.md", "file"),
    _SharedDoc("t3/runtime.md", "file"),
    _SharedDoc("t3/setup.md", "file"),
    _SharedDoc("t3/added/", "skill"),
    _SharedDoc("t3/overrides/", "skill"),
    _SharedDoc("skills/", "skill"),
)


def _fixed_docs():
    return tuple(doc.root for doc in _CATALOG if doc.kind == "file")


def _skill_roots():
    return tuple(doc.root for doc in _CATALOG if doc.kind == "skill")


def _covers(lease, doc):
    return lease == "" or doc == lease or doc.startswith(lease + "/")


def _is_skill_file(doc):
    return doc.endswith("/SKILL.md") and doc.startswith(("t3/added/", "t3/overrides/", "skills/"))


def _lease_parts(paths):
    if not paths:
        return []
    found = []
    for raw in paths.split(","):
        if raw.strip() == "":
            if "" not in found:
                found.append("")
            continue
        value = posixpath.normpath(raw.strip().replace("\\", "/")).lstrip("/")
        if value in (".", ""):
            value = ""
        if value == ".." or value.startswith("../"):
            continue
        if value not in found:
            found.append(value)
    return found


def _can_cover_catalog(parts):
    for part in parts:
        if part == "":
            return True
        for fixed in _fixed_docs():
            if fixed == part or fixed.startswith(part + "/"):
                return True
        for root in _skill_roots():
            stripped = root.rstrip("/")
            if part == stripped or stripped.startswith(part + "/") or part.startswith(stripped + "/"):
                return True
    return False


# Each comma separated paths part uses backslash to slash, then normpath, then strips leading slash.
# A part that becomes dot dot or starts with dot dot slash is outside the repository and is ignored here.
# Empty paths holds nothing and reads no git.
# A blank component or dot becomes empty string and covers every path in the repository.
# A lease path covers a document when the path is empty string.
# A lease path also covers a document when the document equals the path.
# A lease path also covers a document when the document starts with the path plus slash.
# For example t3 added brigade covers t3 added brigade SKILL dot md.
# The path t3 added brigade scripts does not cover that file.
# The file list is the gate own inventory from the repository the lease is for.
# The gate inventory holds tracked plus untracked files and serves directory coverage only.
# The gate walks an opaque directory entry on disk, so a file inside it is seen even when git reports the directory as one entry.
# That walk follows no symbolic link and skips a nested repository's git directory.
# Other commands keep their tracked only behavior.
# An explicit SKILL dot md path counts by its form whether or not the index holds it.
# The check never opens the user path and never reads outside that repository.
# The four fixed names count even when the index omits them.
# A SKILL dot md counts only when it ends in slash SKILL dot md and starts with one of the three roots.
# Vendor is not in the catalog.
# The changelog fragment that with fragment appends is not part of the check.
# When git ls files fails, the four fixed names still match by prefix.
# When git ls files fails, a lease path that is exactly one skill directory counts as that path plus slash SKILL dot md.
# When git ls files fails, a wider path that covers a skill root counts as that root with trailing slash.
def _walk_opaque(directory, prefix):
    found = set()
    for current, directories, filenames in os.walk(directory, followlinks=False):
        directories[:] = [name for name in directories
                          if name != ".git" and not os.path.islink(os.path.join(current, name))]
        relative = os.path.relpath(current, directory)
        head = prefix if relative == os.curdir else f"{prefix}{relative.replace(os.sep, '/')}/"
        found.update(f"{head}{name}" for name in filenames if not os.path.islink(os.path.join(current, name)))
    return found


def _gate_inventory(project_root):
    try:
        result = subprocess.run(["git", "-C", str(project_root), "ls-files", "--cached", "--others", "-z"],
                                capture_output=True, encoding="utf-8", errors="surrogateescape")
    except OSError:
        return None
    if result.returncode != 0:
        return None
    names = set(name for name in result.stdout.split("\0") if name)
    root = Path(str(project_root))
    for name in [name for name in names if name.endswith("/")]:
        names.update(_walk_opaque(str(root / name), name))
    return frozenset(names)


def _held_from_tracked(parts, tracked, gate_files=None):
    held = set()
    for fixed in _fixed_docs():
        if any(_covers(part, fixed) for part in parts):
            held.add(fixed)
    if tracked is not None:
        for part in parts:
            if _is_skill_file(part):
                held.add(part)
        files = gate_files if gate_files is not None else tracked.files
        for path in files:
            if _is_skill_file(path) and any(_covers(part, path) for part in parts):
                held.add(path)
        return tuple(sorted(held))
    for part in parts:
        if _is_skill_file(part):
            held.add(part)
        comps = part.split("/") if part else []
        if len(comps) == 3 and comps[0] == "t3" and comps[1] in ("added", "overrides") and comps[2]:
            held.add(part + "/SKILL.md")
        elif len(comps) == 2 and comps[0] == "skills" and comps[1]:
            held.add(part + "/SKILL.md")
        for root in _skill_roots():
            stripped = root.rstrip("/")
            if part == "" or part == stripped or stripped.startswith(part + "/"):
                held.add(root)
    return tuple(sorted(held))


@dataclass(frozen=True)
class DocRefused:
    line: str


@dataclass(frozen=True)
class DocAllowed:
    _note: str | None

    def record(self, restaurant, dish_id):
        if self._note is None:
            return
        restaurant.log("doc", dish_id, "item", self._note)


@dataclass(frozen=True)
class PreparedDocFire:
    _held: tuple[str, ...]
    _timebox: int
    _ticket_count: int
    _doc_item: bool

    def decide(self):
        held = tuple(sorted(self._held))
        if not held:
            return DocAllowed("doc-item" if self._doc_item else None)
        if self._doc_item:
            return DocAllowed("doc-item: " + ", ".join(held))
        if not (self._timebox > 90 or self._ticket_count > 1):
            return DocAllowed(None)
        if len(held) == 1:
            docs = f"{held[0]} is a shared document"
        elif len(held) == 2:
            docs = f"{held[0]} and {held[1]} are shared documents"
        else:
            docs = f"{', '.join(held[:-1])}, and {held[-1]} are shared documents"
        reasons = []
        if self._timebox > 90:
            reasons.append(f"--timebox is {self._timebox}, over 90 minutes")
        if self._ticket_count > 1:
            reasons.append(f"--tickets names {self._ticket_count} tickets")
        reason = ", and ".join(reasons)
        drop = "drop it" if len(held) == 1 else "drop them"
        return DocRefused(f"{docs} and {reason}; {drop} from --paths and give the sentences as follow-ups, or pass --doc-item")


def prepare_doc_fire(project_root, paths, timebox, ticket_count, doc_item):
    parts = _lease_parts(paths)
    if not parts:
        return PreparedDocFire((), timebox, ticket_count, doc_item)
    if not (doc_item or timebox > 90 or ticket_count > 1):
        return PreparedDocFire((), timebox, ticket_count, doc_item)
    if not _can_cover_catalog(parts):
        return PreparedDocFire((), timebox, ticket_count, doc_item)
    tracked = tracked_paths(project_root)
    gate_files = _gate_inventory(project_root) if tracked is not None else None
    return PreparedDocFire(_held_from_tracked(parts, tracked, gate_files), timebox, ticket_count, doc_item)


def fire(restaurant, ids, station, task, thread, branch, summary, timebox, paths, reason=None, kind="", ride_along=(), doc_item=False):
    """Start one item for the tickets in `ids`. The item takes the kind and goal fire_label gives.

    The kind and goal check runs twice, each time under the store lock. The first run comes after the owner check, unfireable, and the worker
    cap, so a fire that one of those refuses or blocks gets the words it got before kinds and goals. It comes before the
    lease claim, so a fire it refuses claims no lease. The second run sees a change made while the claim ran.
    The lead ticket's kind cell takes the item's kind in a rewrite of its own, before the item row is appended. A fire
    killed between its writes leaves no item whose kind its lead ticket lacks. No other kind or goal cell
    of a ticket changes, so a ticket that rides along keeps its own.
    """
    while True:
        project_root = restaurant.meta.get("projectRoot", "")
        prepared = prepare_doc_fire(project_root, paths, timebox, len(ids), doc_item)
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
            leased = with_fragment(paths, known) if paths else ""
            _, refusal = checked_fire_label(restaurant, ids, station, kind, ride_along)
            if refusal:
                raise BrigadeError(refusal)
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
        prepared = prepare_doc_fire(project_root, paths, timebox, len(ids), doc_item)
        with restaurant.checked():
            refusal = unfireable(restaurant, ids)
            gate = prepared.decide()
            if not refusal and isinstance(gate, DocRefused):
                refusal = gate.line
            current = restaurant.next_id("dishes.tsv")
            label = None
            if not refusal:
                label, refusal = checked_fire_label(restaurant, ids, station, kind, ride_along)
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
                return f"{dish} (lease {lease} held by {holder(restaurant, dish)})" if lease else dish
            if full and not refusal:
                record_blocked(restaurant, ids, "workers", station, summary, paths, timebox, branch, reason, kind, ride_along,
                               doc_item)
                refusal = full
        if lease:
            release_lease(restaurant, lease)
        if refusal:
            raise BrigadeError(f"nothing fired: {refusal}")


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


def retired_worker_threads(restaurant, ident):
    """Worker thread ids this dish already used on an earlier attempt."""
    return [row["note"] for row in restaurant.rows("log.tsv")
            if row["kind"] == "worker" and row["id"] == ident and row["state"] == "retired" and row["note"]]


def drop(restaurant, ident, stopped):
    """Drop a dish and release its lease, only after its worker stopped. A requested interrupt is not a stop."""
    with restaurant.checked():
        restaurant.fence()
        _, dish = restaurant.find("dishes.tsv", ident)
        lease = dish["lease"] if dish["state"] not in ("merged", "dropped") else ""
        if lease and dish["thread"] and not stopped:
            raise BrigadeError(f"{ident} holds {lease} and its worker may still be running; "
                               "wait for its run with t3_thread_wait, then pass --stopped <run id>")
    if lease:
        result = seam.land_result(restaurant.meta["projectRoot"], "lease", "release", lease, *restaurant.land_owner())
        error = result.stderr.strip().removeprefix("land: ")
        if result.returncode != 0 and not error.startswith(f"{lease} is not active"):
            raise BrigadeError(f"{ident} not dropped: could not release {lease}: {error}")
    with restaurant.checked():
        _, dish = restaurant.find("dishes.tsv", ident)
        evidence = [f"stopped {stopped}"] if stopped else []
        evidence += [f"released {lease}"] if lease else []
        note = f"{dish['summary']} ({'; '.join(evidence)})" if evidence else None
        restaurant.update("dishes.tsv", ident, "dish", note=note, state="dropped")
        write_threads(restaurant, ident, (), closing=True)
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
