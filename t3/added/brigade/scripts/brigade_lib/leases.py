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
from brigade_lib.layout import _layout, _one
from brigade_lib.shared import BrigadeError, slug
from brigade_lib.store import COUNTED_STATES, KINDS, LOW_RESERVE, MODES, holder_prefix, is_admin
from brigade_lib.tickets import tracked_paths

NOT_STOPPED = "the old run has not been confirmed stopped; wait for it with t3_thread_wait, then pass --stopped <run id>"


def holder(restaurant, dish):
    return f"{slug(restaurant.meta['restaurant'])}/{dish}"


class ClaimError(BrigadeError):
    """land.py refused a lease claim. fire records the block and adds its own prefix."""


def worker_cap(meta):
    value = meta.get("workers")
    if value in (None, ""):
        return 2
    return int(value)


def autofire_of(meta):
    return meta.get("autofire") or "normal"


@dataclass(frozen=True)
class KindLimits:
    """The per-kind settings restaurant.json holds. caps is {kind: cap} and review is {kind: mode}.

    A kind that is absent has no cap, or no review depth.
    """
    caps: dict
    review: dict


@dataclass(frozen=True)
class Flight:
    """How many items hold a worker slot, by kind and by goal. A kind or a goal with none is absent."""
    by_kind: dict
    by_goal: dict


@dataclass(frozen=True)
class Hold:
    """Why a spend control holds an item, as the standing's kind and the words both doors print."""
    standing: str
    words: str


def kind_limits(meta):
    """The caps and review depths restaurant.json holds, as a KindLimits.

    set --cap and set --review are the boundary that refuses bad text, so an entry this drops was written by
    hand. A cap is a whole number of 1 or more and a depth is full or light. A non-object value reads as no
    setting.
    """
    caps, review = {}, {}
    for key, found in (("caps", caps), ("review", review)):
        stored = meta.get(key)
        if not isinstance(stored, dict):
            continue
        for kind, value in stored.items():
            if kind not in KINDS:
                continue
            if key == "review":
                if value in MODES:
                    review[kind] = value
            elif isinstance(value, int) and not isinstance(value, bool) and value >= 1:
                caps[kind] = value
    return KindLimits(caps, review)


def counted_pairs(dishes, labels):
    """(kind, goal) per row of dishes.tsv that holds a worker slot, as read_labels reads the two cells."""
    return tuple((labels.items[row["id"]].kind, labels.items[row["id"]].goal)
                 for row in dishes if row["state"] in COUNTED_STATES)


def flight_of(pairs):
    """The Flight these pairs give. An empty kind or goal is not counted."""
    by_kind, by_goal = {}, {}
    for kind, goal in pairs:
        if kind:
            by_kind[kind] = by_kind.get(kind, 0) + 1
        if goal:
            by_goal[goal] = by_goal.get(goal, 0) + 1
    return Flight(by_kind, by_goal)


def with_item(flight, label):
    """The Flight one more item of this label gives."""
    by_kind, by_goal = dict(flight.by_kind), dict(flight.by_goal)
    if label.kind:
        by_kind[label.kind] = by_kind.get(label.kind, 0) + 1
    if label.goal:
        by_goal[label.goal] = by_goal.get(label.goal, 0) + 1
    return Flight(by_kind, by_goal)


@dataclass(frozen=True)
class StandingText:
    count_label: str
    line_words: str


STANDINGS = {
    "start": StandingText("startable", "startable"),
    "rides": StandingText("riding", "rides with {why}"),
    "blocked": StandingText("blocked", "blocked, {why}"),
    "unknown": StandingText("unknown", "unknown, no paths recorded"),
    "decision": StandingText("decisions", "decision for the owner"),
    "below": StandingText("below auto-start", "below auto-start"),
    "paused": StandingText("goal paused", "{why}"),
    "cap": StandingText("kind cap", "{why}"),
    "share": StandingText("goal share", "{why}"),
    "reserve": StandingText("held back", f"held back, low tickets leave {LOW_RESERVE} worker idle while an urgent or normal ticket waits"),
}


def admit(label, goal, flight, limits):
    """Why a spend control holds an item of this kind under this goal, or None.

    `goal` is the Goal the ticket's goal names, or None for unassigned work. A paused goal comes first, then a
    kind cap, then a goal share. Each compares what is in flight with the setting.
    """
    if goal is not None and goal.state == "paused":
        return Hold("paused", "its goal is paused")
    held = flight.by_kind.get(label.kind, 0)
    cap = limits.caps.get(label.kind)
    if cap is not None and held >= cap:
        return Hold("cap", f"the {label.kind} cap holds ({held} of {cap})")
    if goal is not None and goal.share and flight.by_goal.get(goal.id, 0) >= int(goal.share):
        at = flight.by_goal.get(goal.id, 0)
        return Hold("share", f"the {goal.id} share holds ({at} of {goal.share})")
    return None


def review_mode(limits, kind):
    """The review depth recorded for this kind, or None when the kind has none."""
    return limits.review.get(kind)


@dataclass(frozen=True)
class Refill:
    """What the spend controls hold now and what is in flight under them, as the refill and fire read them.

    limits is what kind_limits read, goals is {goal id: Goal} from goals.tsv, and flight counts the dishes that
    hold a worker slot by kind and by goal.
    """
    limits: KindLimits
    goals: dict
    flight: Flight

    def hold(self, label):
        """Why a spend control holds an item of this label, or None."""
        return admit(label, self.goals.get(label.goal), self.flight, self.limits)

    def started(self, label):
        """The Refill one more item of this label gives, for the next ticket in the same pass."""
        return Refill(self.limits, self.goals, with_item(self.flight, label))


def refill_of(meta, dishes, labels):
    """The Refill of this store, read under the caller's store lock."""
    return Refill(kind_limits(meta), labels.goals, flight_of(counted_pairs(dishes, labels)))


def workers_line(meta, running):
    cap = worker_cap(meta)
    return f"workers: {running} of {cap} running, {max(0, cap - running)} idle, auto-start: {autofire_of(meta)}"


def require_workers(workers):
    if workers is not None and workers < 1:
        raise BrigadeError("workers must be 1 or more")


def running_workers(restaurant):
    return sum(row["state"] in COUNTED_STATES for row in restaurant.rows("dishes.tsv"))


def changelog_fragment(branch):
    encoded = branch.replace("%", "%25").replace("/", "%2F")
    return f"changes/{encoded}.md"


def with_fragment(paths, branch):
    if not paths or not branch:
        return paths
    frag = changelog_fragment(branch)
    parts = [part for part in paths.split(",") if part]
    if frag not in parts:
        parts.append(frag)
    return ",".join(parts)


@dataclass(frozen=True)
class Claim:
    text: str
    added: tuple


def _claim_norm(raw):
    cleaned = raw.strip().replace("\\", "/")
    if not cleaned:
        return ""
    made = posixpath.normpath(cleaned).lstrip("/")
    if made in (".", ""):
        return ""
    if made == ".." or made.startswith("../"):
        return None
    return made


def _check_checkout(project_root, raw_list):
    tracked = tracked_paths(project_root)
    if tracked is None:
        raise BrigadeError("could not read the git index")
    missing = []
    seen = set()
    for raw in raw_list:
        made = _claim_norm(raw)
        if made == "":
            continue
        display = made if made is not None else raw.strip().replace("\\", "/")
        if not display:
            continue
        if made is not None and (made in tracked.files or made in tracked.directories):
            continue
        if display not in seen:
            seen.add(display)
            missing.append(display)
    if not missing:
        return
    if len(missing) == 1:
        raise BrigadeError(f"{missing[0]} is not in the checkout. Drop it from --paths")
    if len(missing) == 2:
        raise BrigadeError(f"{missing[0]} and {missing[1]} are not in the checkout. Drop them from --paths")
    raise BrigadeError(f"{', '.join(missing[:-1])}, and {missing[-1]} are not in the checkout. Drop them from --paths")


def claim_paths(project_root, paths, branch, doc_item=False):
    if not paths:
        return Claim("", ())
    raw_list = [part for part in paths.split(",") if part]
    if not raw_list:
        return Claim("", ())
    try:
        lay = _layout(project_root)
    except KeyboardInterrupt:
        raise
    except Exception:
        lay = None
    if lay is None:
        return Claim(with_fragment(paths, branch), ())
    _check_checkout(project_root, raw_list)
    seen = set()
    for raw in raw_list:
        made = _claim_norm(raw)
        if made:
            seen.add(made)
    extras = []
    for raw in raw_list:
        made = _claim_norm(raw)
        if not made:
            continue
        try:
            found = _one(made, lay, doc_item)
        except KeyboardInterrupt:
            raise
        except Exception:
            continue
        for one in found:
            if one not in seen:
                seen.add(one)
                extras.append(one)
    return Claim(with_fragment(",".join([*raw_list, *extras]), branch), tuple(extras))


def fired_line(dish, lease, holder_name, added):
    base = f"{dish} (lease {lease} held by {holder_name})" if lease else dish
    if added:
        return f"{base}\nadded {', '.join(added)}"
    return base


def refusal_kind(message):
    if "repository at its cap" in message:
        return "repository"
    return "lease"


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


def land_result(project_root, *args):
    if seam.READ_ONLY:
        raise BrigadeError("brigade: next is read-only")
    return subprocess.run([sys.executable, str(seam.LAND), "--repo", str(project_root), *args],
                          capture_output=True, text=True)


def contract_mode(project_root):
    """`lands by merge`, or `no landing contract` when land.py status fails."""
    result = seam.land_result(project_root, "status")
    if result.returncode != 0 or not result.stdout.strip():
        return "no landing contract"
    return f"lands by {result.stdout.split(None, 1)[0]}"


def repository_cap(project_root):
    result = seam.land_result(project_root, "status")
    if result.returncode != 0:
        return None
    match = re.search(r"changes in flight: \d+ of (\d+)", result.stdout)
    return int(match.group(1)) if match else None


def claim_lease(restaurant, dish, paths):
    """Claim the dish's paths in the repository's landing queue. A refused claim fires nothing."""
    result = seam.land_result(restaurant.meta["projectRoot"], "lease", "claim",
                         "--holder", holder(restaurant, dish), "--paths", paths, *restaurant.land_owner())
    if result.returncode != 0:
        raise ClaimError(result.stderr.strip().removeprefix("land: "))
    return result.stdout.strip()


def release_lease(restaurant, lease):
    result = seam.land_result(restaurant.meta["projectRoot"], "lease", "release", lease, *restaurant.land_owner())
    if result.returncode != 0:
        diagnostic = (result.stderr or result.stdout).strip().removeprefix("land: ")
        print(f"brigade: warning: could not release {lease}: {diagnostic}", file=sys.stderr)


def lease_check(project_root, holder_name, paths):
    result = seam.land_result(project_root, "lease", "check", "--holder", holder_name, "--paths", paths)
    diagnostic = result.stderr.strip().removeprefix("land: ")
    return result.returncode, result.stdout.strip(), diagnostic


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


def block_holds(project_root, prefix, ident, note, running, cap, next_dish):
    """The block on a waiting ticket that holds now, as (kind, text), or None. Workers come first, as in fire."""
    if running >= cap:
        return "workers", f"waiting for a worker ({running} of {cap} running)"
    requested = note.get("paths") or ""
    if not requested:
        return None
    known = note.get("branch") or f"{prefix}/{next_dish.lower()}"
    return lease_block(project_root, f"{prefix}/{ident}", claim_paths(project_root, requested, known, note.get("doc_item", False)).text, note.get("kind") or "lease")


def block_inputs(restaurant):
    """What a block recheck reads from the store. Call it under the store lock."""
    return blocked_waiting(restaurant), running_workers(restaurant), worker_cap(restaurant.meta), restaurant.next_id("dishes.tsv")


def recheck_blocks(restaurant, inputs):
    """Each blocked ticket, its note, and the block that holds now or None. land.py runs here, outside the store lock."""
    blocked, running, cap, next_dish = inputs
    root, prefix = restaurant.meta["projectRoot"], slug(restaurant.meta["restaurant"])
    return [(ident, note, block_holds(root, prefix, ident, note, running, cap, next_dish)) for ident, note in blocked]


def holding_blocks(restaurant):
    """The blocks that still hold, by ticket, rechecked as watch does."""
    with restaurant.checked():
        inputs = block_inputs(restaurant)
    return {ident: held for ident, _, held in recheck_blocks(restaurant, inputs) if held}


def paths_overlap(first, second):
    return any(a == b or a.startswith(b + "/") or b.startswith(a + "/") for a in first for b in second)


def paths_inside(paths, within):
    return all(any(a == b or a.startswith(b + "/") for b in within) for a in paths)


def renew_line(restaurant, dish):
    """Renew a live lease. Returns the line to print, or None, and whether the landing store answered."""
    lease = dish["lease"]
    result = seam.land_result(restaurant.meta["projectRoot"], "lease", "renew", lease, "--if-live", *restaurant.land_owner())
    error = result.stderr.strip().removeprefix("land: ")
    if result.returncode == 0 or error.startswith(f"{lease} is submitted"):
        return None, True
    if error == f"{lease} is released":
        return f"{dish['id']}: lease {lease} was released; claim again before submitting", True
    if error.startswith(f"{lease} expired at "):
        return f"{dish['id']}: lease {lease} expired; stop its worker, then run lease renew {lease}", True
    return f"{dish['id']}: could not renew {lease}: {error or result.stdout.strip()}", False


ENTRY_LINE = re.compile(r"^E(\d+) (\S+) \((\S+), ([0-9a-f]+)\)(.*)$")


def entry_line(restaurant, dish):
    """The queue's entry for this dish: its holder's highest entry at exactly the dish's SHA.

    Returns the line to print, or None, and whether that entry holds the dish's submission.
    """
    sha = dish["sha"].lower()
    if not sha:
        return (None if dish["state"] == "passed" else f"{dish['id']}: queued, but no SHA recorded"), False
    result = seam.land_result(restaurant.meta["projectRoot"], "status", "--holder", holder(restaurant, dish["id"]), "--sha", sha)
    if result.returncode != 0:
        return f"{dish['id']}: could not read the queue: {result.stderr.strip().removeprefix('land: ')}", False
    found = None
    for line in result.stdout.splitlines():
        found = ENTRY_LINE.match(line) or found
    if not found:
        return (None if dish["state"] == "passed" else f"{dish['id']}: queued, but no entry at {sha[:12]}"), False
    number, state, rest = found.group(1), found.group(2), found.group(5)
    ident = dish["id"]
    if state == "bounced":
        return f"{ident}: E{number} bounced{rest}", False
    if dish["state"] == "passed":
        return f"{ident}: E{number} already submitted; mark it queued", True
    if state == "landed":
        return f"{ident}: landed as E{number} ({rest.removeprefix(' as ')}); mark it merged", True
    if state == "awaiting-merge":
        return f"{ident}: E{number} awaiting merge {rest.strip()}; watch that PR", True
    return f"{ident}: E{number} {state}", True


def lease_states(project_root):
    """Each held lease's state in the landing queue, active or submitted, by id."""
    result = seam.land_result(project_root, "lease", "list")
    return dict(line.split()[:2] for line in result.stdout.splitlines() if line.startswith("L"))


def fragment_lines(restaurant, ident, branch):
    """The commands that lease the new branch's changelog fragment. They are printed, not run.

    land.py cannot add paths to a lease. A claim made here would stay unrecorded when a drop or a new owner
    wins the store between the claim and its record.
    """
    states = lease_states(restaurant.meta["projectRoot"])
    with restaurant.checked():
        dish = restaurant.find("dishes.tsv", ident)[1]
    lease, paths = dish["lease"], with_fragment(dish["paths"], branch)
    if not lease or dish["state"] in ("merged", "dropped") or paths == dish["paths"]:
        return None
    fragment = changelog_fragment(branch)
    if states.get(lease) == "submitted":
        return (f"{ident}: {lease} is submitted, so it cannot take {fragment}; the submitted commit lands as it is. "
                f"Run dish {ident} --branch {branch} again if its entry bounces")
    owner = " ".join(restaurant.land_owner())
    owner = f" {owner}" if owner else ""
    active = states.get(lease) == "active"
    lines = [f"{ident}: {lease} {'does not cover' if active else 'is not active, so nothing covers'} {fragment}; "
             "cover it with these commands:",
             f"  $L lease claim --holder {holder(restaurant, ident)} --paths {shlex.quote(paths)}{owner}",
             f"  $B dish {ident} --lease <new lease> --paths {shlex.quote(paths)}"]
    if active:
        lines.append(f"  $L lease release {lease}{owner}")
    return "\n".join(lines)


def next_owner(meta, thread, replace, expect=None, stopped=None):
    """The generation set --thread records, and whether it changes the recorded thread.

    The executive admin's thread changes only by compare-and-swap: --expect names the thread it replaces.
    """
    current = (meta.get("thread") or "").strip()
    generation = meta.get("generation")
    if expect is not None:
        if not is_admin(meta):
            raise BrigadeError("--expect works only in the executive admin's store")
        if not replace:
            raise BrigadeError("the executive admin's thread changes only with --replace --expect <old>")
        if current != expect:
            raise BrigadeError(f"thread is {current or 'not recorded'}, not {expect or 'empty'}; nothing replaced")
        if thread == current:
            return generation or 1, False
        if current.startswith("recovering:") and not stopped:
            raise BrigadeError(NOT_STOPPED)
        return (generation or 0) + 1, True
    if is_admin(meta) and (current or generation) and thread != current:
        raise BrigadeError("the executive admin's thread changes only with --replace --expect <old>")
    if current and thread != current:
        if not replace:
            raise BrigadeError(f"thread {current} already recorded")
        return (generation or 1) + 1, True
    return generation or 1, not current


def set_thread(restaurant, args):
    """Raise the landing floor first, outside the store lock, so a crash before the rewrite is reached again."""
    while True:
        with restaurant.locked():
            target = next_owner(restaurant.meta, args.thread, args.replace, args.expect, args.stopped)
        generation, changing = target
        if changing and generation > 1:
            prefix = holder_prefix(restaurant.meta)
            result = seam.land_result(restaurant.meta["projectRoot"], "owner", "--prefix", prefix, "--generation", str(generation))
            error = result.stderr.strip().removeprefix("land: ")
            # A project with no landing contract has no leases to fence.
            if result.returncode != 0 and "has no landing contract" not in error and "not a git repository" not in error:
                raise BrigadeError(f"thread not replaced: could not raise the landing floor for {prefix}: {error}")
        with restaurant.checked():
            if next_owner(restaurant.meta, args.thread, args.replace, args.expect, args.stopped) != target:
                continue
            # Recording the same thread again is an ordinary write, so it keeps the fence.
            restaurant.unfenced = changing
            return seam.command(restaurant, args)
