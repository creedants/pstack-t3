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
from brigade_lib.compact import refuse_setting
from brigade_lib.leases import Claim, ClaimError, Hold, claim_lease, claim_paths, fired_line, holder, missing_checkout_paths, refusal_kind, refill_of, release_lease, running_workers, worker_cap
from brigade_lib.reviews import fix_round_refusal, thread_binding, write_threads
from brigade_lib.shared import BrigadeError, clean, slug, stored
from brigade_lib.store import IN_FLIGHT_STATES, KIND_CHOICE, LEASED_STATES, STATION_KIND, WORKER_ROLES
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


@dataclass(frozen=True)
class _SharedDoc:
    root: str
    kind: str


# A store's sharedDocs supplies every entry, so the tool builds in none.
_CATALOG = ()


@dataclass(frozen=True)
class DocPolicy:
    docs: tuple[_SharedDoc, ...]
    minutes: int


def _repo_relative(value):
    """The repository-relative path one sharedDocs entry names, or None when it is not one."""
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("\\", "/")
    if text.startswith("/") or ".." in text.split("/") or re.search(r"\s", text):
        return None
    made = posixpath.normpath(text).lstrip("/")
    if made in ("", "."):
        return None
    return made


def doc_policy(meta):
    """The shared-document catalog and timebox the store records.

    A missing key keeps its default. A present value that does not parse is refused through
    refuse_setting, so the message names the file, the key, and the value. A file entry names
    one document, and a file entry whose path ends with a slash names a directory prefix, so
    files under it are documents. A skill entry names a directory whose SKILL.md files are
    documents.
    """
    raw = meta.get("sharedDocs", [])
    if not isinstance(raw, list):
        refuse_setting("sharedDocs", raw, "it is not a list")
    docs = list(_CATALOG)
    for entry in raw:
        if not isinstance(entry, dict):
            refuse_setting("sharedDocs", raw, f"an entry is not an object: {entry!r}")
        unknown = [key for key in entry if key not in ("path", "kind")]
        if unknown:
            refuse_setting("sharedDocs", raw, f"an entry has unknown key {unknown[0]!r}")
        given = entry.get("path")
        path = _repo_relative(given)
        if path is None:
            refuse_setting("sharedDocs", raw, f"an entry's 'path' is not a repository path: {given!r}")
        kind = entry.get("kind")
        if kind not in ("file", "skill"):
            refuse_setting("sharedDocs", raw, f"an entry's 'kind' is not file or skill: {kind!r}")
        directory = kind == "skill" or given.replace("\\", "/").endswith("/")
        docs.append(_SharedDoc(path + "/" if directory else path, kind))
    minutes = meta.get("docTimebox", 90)
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
        refuse_setting("docTimebox", minutes, "it is not a whole number of minutes above zero")
    return DocPolicy(tuple(docs), minutes)


def _fixed_docs(catalog):
    return tuple(doc.root for doc in catalog if doc.kind == "file" and not doc.root.endswith("/"))


def _prefix_docs(catalog):
    return tuple(doc.root for doc in catalog if doc.kind == "file" and doc.root.endswith("/"))


def _skill_roots(catalog):
    return tuple(doc.root for doc in catalog if doc.kind == "skill")


def _covers(lease, doc):
    return lease == "" or doc == lease or doc.startswith(lease + "/")


def _is_skill_file(doc, roots):
    return doc.endswith("/SKILL.md") and doc.startswith(roots)


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


def _can_cover_catalog(parts, catalog):
    fixed = _fixed_docs(catalog)
    prefixes = _prefix_docs(catalog)
    roots = _skill_roots(catalog)
    for part in parts:
        if part == "":
            return True
        for doc in fixed:
            if doc == part or doc.startswith(part + "/"):
                return True
        for doc in prefixes:
            stripped = doc.rstrip("/")
            if part == stripped or stripped.startswith(part + "/") or part.startswith(doc):
                return True
        for root in roots:
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
# Every catalog file entry that does not end in a slash counts even when the index omits it.
# A SKILL dot md counts only when it ends in slash SKILL dot md and starts with one catalog skill root.
# The catalog is the store's sharedDocs, so no path is built into the tool.
# The changelog fragment that with fragment appends is not part of the check.
# When git ls files fails, every catalog file entry and file directory prefix still matches by prefix.
# When git ls files fails, a lease path that is exactly one skill directory counts as that path plus slash SKILL dot md.
# When git ls files fails, a wider path that covers a skill root counts as that root with trailing slash.
# When git ls files fails, a path inside a file directory prefix or a wider path that covers it counts as that prefix with trailing slash.
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


def _held_from_tracked(parts, tracked, gate_files=None, catalog=()):
    held = set()
    prefixes = _prefix_docs(catalog)
    roots = _skill_roots(catalog)
    for fixed in _fixed_docs(catalog):
        if any(_covers(part, fixed) for part in parts):
            held.add(fixed)
    if tracked is not None:
        for part in parts:
            if _is_skill_file(part, roots):
                held.add(part)
        files = gate_files if gate_files is not None else tracked.files
        for path in files:
            if path.endswith("/"):
                continue
            covered = any(_covers(part, path) for part in parts)
            if not covered:
                continue
            if _is_skill_file(path, roots) or any(path.startswith(prefix) for prefix in prefixes):
                held.add(path)
        return tuple(sorted(held))
    for part in parts:
        if _is_skill_file(part, roots):
            held.add(part)
        for root in roots:
            stripped = root.rstrip("/")
            if part == "" or part == stripped or stripped.startswith(part + "/"):
                held.add(root)
            elif part.startswith(root) and "/" not in part[len(root):]:
                held.add(part + "/SKILL.md")
        for prefix in prefixes:
            stripped = prefix.rstrip("/")
            if part == "" or part == stripped or stripped.startswith(part + "/") or part.startswith(prefix):
                held.add(prefix)
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
    _minutes: int
    _timebox: int
    _ticket_count: int
    _doc_item: bool

    def decide(self):
        held = tuple(sorted(self._held))
        if not held:
            return DocAllowed("doc-item" if self._doc_item else None)
        if self._doc_item:
            return DocAllowed("doc-item: " + ", ".join(held))
        if not (self._timebox > self._minutes or self._ticket_count > 1):
            return DocAllowed(None)
        if len(held) == 1:
            docs = f"{held[0]} is a shared document"
        elif len(held) == 2:
            docs = f"{held[0]} and {held[1]} are shared documents"
        else:
            docs = f"{', '.join(held[:-1])}, and {held[-1]} are shared documents"
        reasons = []
        if self._timebox > self._minutes:
            reasons.append(f"--timebox is {self._timebox}, over {self._minutes} minutes")
        if self._ticket_count > 1:
            reasons.append(f"--tickets names {self._ticket_count} tickets")
        reason = ", and ".join(reasons)
        drop = "drop it" if len(held) == 1 else "drop them"
        return DocRefused(f"{docs} and {reason}; {drop} from --paths and give the sentences as follow-ups, or pass --doc-item")


def prepare_doc_fire(project_root, paths, timebox, ticket_count, doc_item, policy):
    parts = _lease_parts(paths)
    if not parts:
        return PreparedDocFire((), policy.minutes, timebox, ticket_count, doc_item)
    if not policy.docs:
        return PreparedDocFire((), policy.minutes, timebox, ticket_count, doc_item)
    if not (doc_item or timebox > policy.minutes or ticket_count > 1):
        return PreparedDocFire((), policy.minutes, timebox, ticket_count, doc_item)
    if not _can_cover_catalog(parts, policy.docs):
        return PreparedDocFire((), policy.minutes, timebox, ticket_count, doc_item)
    tracked = tracked_paths(project_root)
    gate_files = _gate_inventory(project_root) if tracked is not None else None
    return PreparedDocFire(_held_from_tracked(parts, tracked, gate_files, policy.docs), policy.minutes, timebox, ticket_count,
                           doc_item)


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
            claimed = claim_paths(project_root, paths, known, doc_item) if paths else Claim("", ())
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
                return fired_line(dish, lease, holder(restaurant, dish), added)
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
    """Drop a dish and release its lease, only after its worker stopped. A requested interrupt is not a stop."""
    with restaurant.checked():
        restaurant.fence()
        _, dish = restaurant.find("dishes.tsv", ident)
        lease = dish["lease"] if dish["state"] not in ("merged", "dropped") else ""
        if lease and dish["thread"] and not stopped:
            raise BrigadeError(f"{ident} holds {lease} and its worker may still be running; "
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
    if lease:
        result = seam.land_result(restaurant.meta["projectRoot"], "lease", "release", lease, *restaurant.land_owner())
        error = result.stderr.strip().removeprefix("land: ")
        if result.returncode != 0 and not error.startswith(f"{lease} is not active"):
            raise BrigadeError(f"{ident} not dropped: could not release {lease}: {error}")
    with restaurant.checked():
        _, dish = restaurant.find("dishes.tsv", ident)
        later = {item for _, _, item in worker_bindings(restaurant, ident) - bindings}
        cell = stored("dishes.tsv", "thread", dish["thread"])
        if cell and cell != current:
            later.add(cell)
        evidence = [f"stopped {stopped}"] if stopped else []
        evidence += [f"released {lease}"] if lease else []
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
