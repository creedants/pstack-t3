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
from brigade_lib.shared import BrigadeError, clean, one_of, same_text, slug
from brigade_lib.store import CLOSED_GOAL_STATES, GENERATED_ROOT, GOAL_MOVES, GOAL_STATES, IN_FLIGHT_STATES, KINDS, KIND_CHOICE, LINE_SUFFIX, LIVE_TICKET_STATES, NOTHING_HANDED, Restaurant, SOURCE_PRIORITY, SOURCE_ROOT, intake_owner, refuse_owned_intake, siblings



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


def priority_of(row):
    return row["priority"] or SOURCE_PRIORITY.get(base_source(row["source"]), "normal")


def paths_of(row):
    return tuple(part for part in row["paths"].split(",") if part)


def ticket_paths(text):
    if text == "":
        return ()
    found = set()
    for part in text.split(","):
        path = posixpath.normpath(part.strip().replace("\\", "/")).lstrip("/")
        if path in ("", ".", "..") or path.startswith("../"):
            raise BrigadeError(f"--paths takes files or directories inside the repository, got {part!r}")
        found.add(path)
    return tuple(sorted(found))


@dataclass(frozen=True)
class Tracked:
    files: frozenset
    directories: frozenset
    names: dict


def unique_names(files):
    sources = {path.rsplit("/", 1)[-1] for path in files if path.startswith(SOURCE_ROOT)}
    by_name = {}
    for path in files:
        name = path.rsplit("/", 1)[-1]
        if not (path.startswith(GENERATED_ROOT) and name in sources):
            by_name.setdefault(name, []).append(path)
    return {name: paths[0] for name, paths in by_name.items() if len(paths) == 1}


def tracked_paths(project_root):
    try:
        result = subprocess.run(["git", "-C", str(project_root), "ls-files", "-z"], capture_output=True,
                                encoding="utf-8", errors="surrogateescape")
    except OSError:
        return None
    if result.returncode != 0:
        return None
    files = frozenset(name for name in result.stdout.split("\0") if name)
    directories = frozenset(str(above) for name in files for above in PurePosixPath(name).parents if str(above) != ".")
    return Tracked(files, directories, unique_names(files))


def pattern_directory(parts):
    marked = next(index for index, part in enumerate(parts) if re.search(r"[*?\[]", part))
    return "/".join(parts[:marked])


def quoted_paths(text, tracked):
    if tracked is None:
        return ()
    found = set()
    for quote in re.finditer(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", text):
        content = quote.group(2)
        span = re.sub(LINE_SUFFIX, "", content.strip())
        if not span or re.search(r"\s", span) or span.startswith("/") or ".." in span.split("/"):
            continue
        span = posixpath.normpath(span)
        if not re.search(r"[*?\[]", span):
            if span != "." and (span in tracked.files or span in tracked.directories):
                found.add(span)
            elif content not in tracked.files and content not in tracked.directories:
                name = re.sub(LINE_SUFFIX, "", content)
                if not re.search(r"[/\s*?\[]", name) and name in tracked.names:
                    found.add(tracked.names[name])
            continue
        directory = pattern_directory(span.split("/"))
        if directory and fnmatch.filter(tracked.files, span):
            found.add(directory)
    return tuple(sorted(found))


def record_paths(restaurant, tracked, write):
    rows = restaurant.rows("rail.tsv")
    lines, recorded = [], False
    for row in rows:
        if row["state"] != "waiting" or paths_of(row):
            continue
        paths = ",".join(quoted_paths(row["summary"], tracked))
        if paths:
            lines.append(f"{row['id']} {'paths' if write else 'would record'} {paths}")
            row["paths"], recorded = paths, True
        else:
            lines.append(f"{row['id']} quotes no tracked path")
    if write and recorded:
        restaurant.save_rows("rail.tsv", rows)
    return "\n".join(lines) or "every waiting ticket records paths"


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


@dataclass(frozen=True)
class Goal:
    """One row of goals.tsv as read_labels reads it.

    state is one of GOAL_STATES. share is the digits of a whole number of 1 or more with no leading zero, or "" for no limit.
    """
    id: str
    state: str
    name: str
    purpose: str
    done: str
    share: str
    closed: str


@dataclass(frozen=True)
class Label:
    """A ticket's or an item's kind and goal as read_labels reads them.

    kind is one of KINDS, or "" for no kind. goal is a key of Labels.goals, or "" for unassigned.
    """
    kind: str = ""
    goal: str = ""


@dataclass(frozen=True)
class Fault:
    """One warning of read_labels. table is the table the row is in, id is the row's id cell, and text is the warning."""
    table: str
    id: str
    text: str


@dataclass(frozen=True)
class ActsOn:
    """The rows of rail.tsv and dishes.tsv whose kind and goal a command acts on.

    read_labels prints the warnings of these rows. tickets holds ticket ids and items holds item ids.
    every is true for a command that lists or counts every row.
    """
    tickets: tuple = ()
    items: tuple = ()
    every: bool = False

    def covers(self, fault):
        """True when read_labels prints this fault. Every fault of goals.tsv is printed."""
        return (fault.table == "goals.tsv" or self.every
                or fault.id in {"rail.tsv": self.tickets, "dishes.tsv": self.items}[fault.table])


EVERY_ROW = ActsOn(every=True)
GOALS_ONLY = ActsOn()


@dataclass(frozen=True)
class Labels:
    """The kinds and goals of one store, read under one hold of the store lock.

    goals is {goal id: Goal} in file order. tickets is {ticket id: Label} for the rows of rail.tsv, and items is
    {item id: Label} for the rows of dishes.tsv. faults is one Fault per cell whose value is not legal,
    and one per id of goals.tsv with a row that is not a goal.
    """
    goals: dict
    tickets: dict
    items: dict
    faults: tuple


def whole_share(text):
    """The digits of a whole number of 1 or more with no leading zero, or "" when the text is not such a number."""
    return text.lstrip("0") if text.isascii() and text.isdigit() else ""


def read_goal(row, faults):
    state, share = row["state"], whole_share(row["share"])
    if state not in GOAL_STATES:
        faults.append(Fault("goals.tsv", row["id"], f"{row['id']} has state {state!r}, which is not {one_of(GOAL_STATES)}; "
                                                    f"it reads as open; run goal set {row['id']} --state <state>"))
        state = "open"
    if row["share"] and not share:
        faults.append(Fault("goals.tsv", row["id"], f"{row['id']} has share {row['share']!r}, which is not a whole number of "
                                                    f"1 or more; it reads as no limit; run goal set {row['id']} --share <n>"))
    return Goal(row["id"], state, row["name"], row["purpose"], row["done"], share, row["closed"])


def in_flight_tickets(restaurant):
    """{ticket id: the first row of dishes.tsv whose state is in IN_FLIGHT_STATES and whose tickets cell names the id}."""
    found = {}
    for item in restaurant.rows("dishes.tsv"):
        if item["state"] in IN_FLIGHT_STATES:
            for ident in item["tickets"].split(","):
                found.setdefault(ident, item)
    return found


def frozen_label(ticket, held):
    """Why ticket set does not change this ticket's kind and goal now, or "" when it does. `held` is in_flight_tickets().

    It changes them when the ticket is waiting and no item in flight names it. fire wrote an item's kind and goal when the
    item started, and no command writes the item's two cells after that.
    """
    if ticket["state"] != "waiting":
        return f"{ticket['id']} is {ticket['state']}"
    item = held.get(ticket["id"])
    return f"{ticket['id']} is a ticket of {item['id']}, which is {item['state']}" if item else ""


def read_label(table, row, goals, faults, by_hand):
    """One row's Label. `by_hand` is the end of the warning for a bad cell of a row whose cells ticket set does not change now.

    It is "" for any other row, and the warning then ends with the ticket set line to run.
    """
    ident = row["id"]
    kind = row["kind"] if row["kind"] in KINDS else ""
    goal = row["goal"] if row["goal"] in goals else ""
    if row["kind"] and not kind:
        faults.append(Fault(table, ident, f"{ident} has kind {row['kind']!r}, which is not {one_of(KINDS)}; it reads as no kind; "
                                          + (by_hand or f"run ticket set {ident} --kind <kind>")))
    if row["goal"] and not goal:
        faults.append(Fault(table, ident, f"{ident} names goal {row['goal']!r}, which has no row in goals.tsv; it reads as unassigned; "
                                          + (by_hand or f"run ticket set {ident} --goal G<n> or --no-goal")))
    return Label(kind, goal)


def read_labels(restaurant, acts_on):
    """The one reader of the kind and goal cells of rail.tsv and dishes.tsv, and of goals.tsv. It writes nothing to the store.

    It prints one warning line on standard error for each fault of goals.tsv and for each fault of a row in `acts_on`.
    A command prints a warning once, however many times it reads.
    Call it under the store lock. A goals.tsv that does not exist reads as no goals.
    A row of goals.tsv with an empty id is not a goal. Neither is a row whose id clean changes, which is an id with white
    space at an end or a carriage return in it. Of the other rows of goals.tsv with one id, the first is the goal.
    The rows with an empty id add one Fault between them. So do the rows with one id that are not a goal.
    A kind cell, a goal cell, and an id of goals.tsv are read as they are in the file, and stored writes each back as it
    is, so a rewrite changes the reading of none that the command did not set.
    A cell whose value is not legal reads as follows and adds one Fault.

    - A kind that is not one of KINDS reads as no kind.
    - A goal that is not the id of a goal reads as unassigned.
    - A goal's state that is not one of GOAL_STATES reads as open.
    - A goal's share that is not empty and not a whole number of 1 or more reads as no limit.
    """
    goals, faults, extra = {}, [], []
    for row in restaurant.rows("goals.tsv"):
        if row["id"] and clean(row["id"]) == row["id"] and row["id"] not in goals:
            goals[row["id"]] = read_goal(row, faults)
        elif row["id"] not in extra:
            extra.append(row["id"])
    for ident in extra:
        if not ident:
            text = "goals.tsv has a row with no id; it is not a goal; remove it from goals.tsv by hand"
        elif clean(ident) != ident:
            text = (f"goals.tsv has a row with id {ident!r}, which has white space at an end or a carriage return in it; "
                    "it is not a goal; correct the id in goals.tsv by hand to an id no other row has, or remove the row")
        else:
            text = (f"goals.tsv has more than one row with id {ident}; the first is the goal and the others are not read; "
                    "remove the others from goals.tsv by hand")
        faults.append(Fault("goals.tsv", ident, text))
    held, tickets = in_flight_tickets(restaurant), {}
    for row in restaurant.rows("rail.tsv"):
        frozen = frozen_label(row, held)
        tickets[row["id"]] = read_label("rail.tsv", row, goals, faults,
                                        frozen and f"ticket set does not change this cell while {frozen}; correct it in rail.tsv by hand")
    items = {row["id"]: read_label("dishes.tsv", row, goals, faults, "no command changes this cell; correct it in dishes.tsv by hand")
             for row in restaurant.rows("dishes.tsv")}
    for fault in faults:
        if acts_on.covers(fault):
            restaurant.warn(fault.text)
    return Labels(goals, tickets, items, tuple(faults))


@dataclass(frozen=True)
class Tally:
    """The work under one kind or one goal. waiting holds ticket ids. in_flight and merged hold item ids."""
    waiting: tuple = ()
    in_flight: tuple = ()
    merged: tuple = ()
    send_backs: int = 0


@dataclass(frozen=True)
class LabelCounts:
    """kinds is {kind: Tally} and goals is {goal id: Tally}. The key "" holds the work with no kind, and the unassigned work.

    riding is {goal id: ticket ids}. A key with no work is absent.
    """
    kinds: dict
    goals: dict
    riding: dict


def count_labels(restaurant, labels):
    """The counts by kind and by goal. Call it under the same hold of the store lock as the read_labels that made `labels`.

    Each row counts under its own kind and its own goal as `labels` gives them.

    - waiting. The rows of rail.tsv whose state is waiting.
    - in flight. The rows of dishes.tsv whose state is in IN_FLIGHT_STATES.
    - merged. The rows of dishes.tsv whose state is merged.
    - send-backs. The rows of pass.tsv whose verdict is send-back, whose member cell is empty, and whose dish cell is the id of
      a row of dishes.tsv. They count under that item's kind and goal, whatever the item's state.
    - riding, for a goal. The rows of rail.tsv whose state is assigned and whose goal is that goal, where the dish cell is not
      the id of an item under that goal.
    """
    kinds, goals, riding = {}, {}, {}

    def add(label, field, ident):
        for found, key in ((kinds, label.kind), (goals, label.goal)):
            found.setdefault(key, {"waiting": [], "in_flight": [], "merged": [], "send_backs": []})[field].append(ident)

    for row in restaurant.rows("rail.tsv"):
        label = labels.tickets[row["id"]]
        if row["state"] == "waiting":
            add(label, "waiting", row["id"])
        elif row["state"] == "assigned" and label.goal and labels.items.get(row["dish"], Label()).goal != label.goal:
            riding.setdefault(label.goal, []).append(row["id"])
    for row in restaurant.rows("dishes.tsv"):
        if row["state"] in IN_FLIGHT_STATES:
            add(labels.items[row["id"]], "in_flight", row["id"])
        elif row["state"] == "merged":
            add(labels.items[row["id"]], "merged", row["id"])
    for row in restaurant.rows("pass.tsv"):
        if row["verdict"] == "send-back" and not row["member"] and row["dish"] in labels.items:
            add(labels.items[row["dish"]], "send_backs", row["dish"])

    def tallies(found):
        return {key: Tally(tuple(lists["waiting"]), tuple(lists["in_flight"]), tuple(lists["merged"]), len(lists["send_backs"]))
                for key, lists in found.items()}

    return LabelCounts(tallies(kinds), tallies(goals), {goal: tuple(ids) for goal, ids in riding.items()})


def open_goal(labels, ident):
    """The goal a ticket is being given. Refuses an id with no row, and a goal that is done or dropped."""
    if not ident:
        raise BrigadeError('--goal "" names no goal; run goal list --all to see every goal')
    goal = labels.goals.get(ident)
    if goal is None:
        raise BrigadeError(f"no goal {ident}; run goal list --all to see every goal")
    if goal.state in CLOSED_GOAL_STATES:
        raise BrigadeError(f"{ident} is {goal.state}; a ticket takes an open or paused goal; "
                           f"run goal set {ident} --state open first, or pick another goal")
    return goal


def goal_share(text):
    """--share as the cell to store. Empty text stays empty. Refuses text that is not a whole number of 1 or more."""
    text = clean(text)
    if text and not whole_share(text):
        raise BrigadeError(f'--share takes a whole number of 1 or more, or "" for no limit, got {text!r}')
    return whole_share(text)


def add_goal(restaurant, name, purpose, done, share=""):
    """Append one open goal and log it. Returns the new id.

    Refuses when an open or paused goal has the same name by same_text, so a command run a second time adds no second goal.
    """
    name, purpose, done, share = clean(name), clean(purpose), clean(done), goal_share(share)
    if not (name and purpose and done):
        raise BrigadeError("goal add needs a nonempty --name, --purpose, and --done")
    for goal in read_labels(restaurant, GOALS_ONLY).goals.values():
        if goal.state not in CLOSED_GOAL_STATES and same_text(goal.name) == same_text(name):
            raise BrigadeError(f"same name as {goal.id} ({goal.state}); nothing added")
    ident = restaurant.next_id("goals.tsv")
    restaurant.append("goals.tsv", {"id": ident, "at": seam.now(), "state": "open", "name": name, "purpose": purpose,
                                    "done": done, "share": share})
    restaurant.log("goal", ident, "open", name)
    return ident


def goal_work(counted, ident):
    """What keeps a goal from closing, as (words, advice) pairs. Empty when no ticket of the goal waits or rides along and no item is in flight."""
    tally = counted.goals.get(ident, Tally())
    held = ((tally.waiting, "waiting", "give each waiting ticket another goal with ticket set --goal, or none with --no-goal; "
             "a waiting ticket that an item in flight names keeps its goal until that item is merged or dropped"),
            (tally.in_flight, "in flight", "finish or drop each item in flight"),
            (counted.riding.get(ident, ()), "riding along", "finish or drop the item each of those tickets rides along in"))
    return [(f"{', '.join(ids)} {words}", advice) for ids, words, advice in held if ids]


def set_goal(restaurant, ident, name=None, purpose=None, done=None, share=None, state=None, evidence=None):
    """Change one goal. Returns one line such as `G1 paused; share 2`.

    GOAL_MOVES is the state machine. A state the goal's cell already holds is accepted and changes neither the state nor
    the closed cell. A state cell that is not one of GOAL_STATES reads as open, and --state open rewrites it.
    A move to done or dropped needs --evidence, and is refused while goal_work finds work under the goal.
    One update call writes every change. It logs a goal row when the state changed.
    """
    labels = read_labels(restaurant, EVERY_ROW if state in CLOSED_GOAL_STATES else GOALS_ONLY)
    goal = labels.goals.get(ident)
    if goal is None:
        raise BrigadeError(f"no goal {ident}; run goal list --all to see every goal")
    if all(value is None for value in (name, purpose, done, share, state)):
        raise BrigadeError("goal set needs --state, --name, --purpose, --done, or --share")
    for flag, value in (("--name", name), ("--purpose", purpose), ("--done", done)):
        if value is not None and not clean(value):
            raise BrigadeError(f"goal set {flag} takes text; a goal keeps a name, a purpose, and a done test")
    if evidence is not None and state not in CLOSED_GOAL_STATES:
        raise BrigadeError("--evidence goes only with --state done or --state dropped")
    if share is not None:
        share = goal_share(share)
    _, row = restaurant.find("goals.tsv", ident)
    moving = state is not None and state != row["state"]
    closing = moving and state in CLOSED_GOAL_STATES
    if moving and state != goal.state and state not in GOAL_MOVES[goal.state]:
        raise BrigadeError(f"{ident} is {goal.state}; a {goal.state} goal moves only to open; run goal set {ident} --state open first")
    if closing:
        if not clean(evidence):
            raise BrigadeError(f"--state {state} needs --evidence <one line>")
        work = goal_work(count_labels(restaurant, labels), ident)
        if work:
            raise BrigadeError(f"{ident} still has work: {'; '.join(words for words, _ in work)}; "
                               f"{'; '.join(advice for _, advice in work)}; then run this again")
    closed = None
    if closing:
        closed = f"{seam.now()} {clean(evidence)}"
    elif moving and goal.state in CLOSED_GOAL_STATES:
        closed = ""
    said = []
    if state is not None:
        said.append(state if moving else f"already {state}")
    if name is not None:
        said.append(f"name {clean(name)}")
    if purpose is not None:
        said.append("purpose changed")
    if done is not None:
        said.append("done test changed")
    if share is not None:
        said.append(f"share {share}" if share else "no share")
    kept = clean(name) if name is not None else goal.name
    restaurant.update("goals.tsv", ident, "goal", note=f"{kept}: {clean(evidence)}" if closing else kept,
                      state=state, name=name, purpose=purpose, done=done, share=share, closed=closed)
    return f"{ident} {'; '.join(said)}"


def label_words(label):
    """What a ticket line prints for a label. ` fix G1`, ` fix`, ` G1`, or nothing for no kind and no goal."""
    return "".join(f" {word}" for word in (label.kind, label.goal) if word)


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


@dataclass(frozen=True)
class FollowUps:
    """What a report's follow-ups sections hold. A report with no such section is None, not this.

    Each of `items` becomes one ticket, and its position plus one is the `#<n>` in its ref.
    `asides` are the (reason, text) pairs the command prints and never files.
    """
    items: tuple
    asides: tuple


# A block that is one of these and nothing more says the report has no follow-up. Every longer block is read as work.
NO_WORK = ("none", "none required", "none needed", "nothing", "nothing needed", "n/a", "not applicable", "no follow-ups")


def says_no_work(text):
    """True when the block is one of NO_WORK and nothing more.

    Case does not count. Neither do list markers, the `*`, `_`, and backtick marks around it, or a final period.
    """
    text = re.sub(r"^(?:(?:[-*+]|\d+[.)])\s+)+", "", text.strip())
    return text.strip("*_` ").removesuffix(".").strip("*_` ").casefold() in NO_WORK


def follow_ups(text):
    """Every follow-ups section of a report, or None when no heading says follow-ups.

    A top-level list item or a paragraph is one block, and the indented lines, nested bullets, and fenced code under it
    are part of it. Each block is a follow-up, with three exceptions that are asides. A block that is one of NO_WORK
    and nothing more is one. So is a paragraph directly above a list item, which introduces the list, and a paragraph
    below its section's last list item, which closes the section.

    The text that follows the word on a follow-ups heading line is an aside too, whatever it says. It is the rest of the
    line without leading colons and surrounding whitespace.

    The text of a heading deeper than the open follow-ups heading that does not itself say follow-ups is an aside too,
    whatever it says. It is the line after its opening `#` marks, without surrounding whitespace. Such a heading with no
    text is an aside with no text. A paragraph with only such headings between it and a list item is directly above that item.

    A list item with no text is an aside with no text. It is not the list item a paragraph is directly above, and it is
    not its section's last list item. A paragraph looks past it as it looks past such a heading.
    """
    sections, level, fenced, blank, block = [], 0, False, True, None
    for line in text.splitlines():
        fence = line.strip().startswith("```")
        plain = not fence and not fenced
        fenced = fenced != fence
        heading = plain and re.match(r"(#{1,6})(\s|$)", line)
        if heading:
            depth = len(heading.group(1))
            named = re.match(r"#{1,6}\s+follow-?ups?\b", line, re.I)
            if named:
                level = depth
                sections.append([("heading", [re.sub(r"^[\s:]+", "", line[named.end():])])])
            elif depth <= level:
                level = 0
            elif level:
                sections[-1].append(("subheading", [line[depth:]]))
            block, blank = None, True
        elif not level:
            continue
        elif not line.strip():
            blank = plain
        else:
            marker = plain and re.match(r"(?:[-*+]|\d+[.)])(?:\s+|$)", line)
            if marker or block is None or plain and blank and not line[0].isspace():
                block = ("item" if marker else "para", [])
                sections[-1].append(block)
            block[1].append(line[marker.end():] if marker else line)
            blank = False
    if not sections:
        return None
    items, asides = [], []
    for blocks in sections:
        blocks = [(kind, " ".join(part.strip() for part in lines).strip()) for kind, lines in blocks]
        blocks = [("bare" if kind == "item" and not text else kind, text) for kind, text in blocks]
        blocks = [(kind, text) for kind, text in blocks if text or kind in ("subheading", "bare")]
        kinds = [kind for kind, _ in blocks] + ["end"]
        last = max((index for index, kind in enumerate(kinds) if kind == "item"), default=len(kinds))
        for index, (kind, text) in enumerate(blocks):
            if kind == "heading":
                asides.append(("text on the heading line", text))
            elif kind == "subheading":
                asides.append(("heading inside the section" if text else "heading inside the section with no text", text))
            elif kind == "bare":
                asides.append(("list item with no text", ""))
            elif says_no_work(text):
                asides.append(("says no work is needed", text))
            elif kind == "para" and next(below for below in kinds[index + 1:] if below not in ("subheading", "bare")) == "item":
                asides.append(("prose that introduces a list", text))
            elif kind == "para" and index > last:
                asides.append(("prose after the last list item", text))
            else:
                items.append(text)
    return FollowUps(tuple(items), tuple(asides))


def follow_up_kind(item_kind, priority):
    """The kind of a follow-up ticket. No kind when its item has none. Else fix at priority urgent or normal, and polish at low."""
    if not item_kind:
        return ""
    return "polish" if priority == "low" else "fix"


def file_follow_ups(restaurant, name, found, write, tracked):
    """One waiting ticket per follow-up whose text no ticket of this store holds, in any state.

    A done or dropped ticket counts, so a rerun on the same report after the coordinator dropped one files nothing.
    The ref ends in a position, which is not an identity, so the text is the only key and refuse_live_ref is not asked.
    Each ticket takes the goal of the report's item, whatever that goal's state, and follow_up_kind of that item's kind.
    """
    restaurant.find("dishes.tsv", name[:-3])
    label = read_labels(restaurant, ActsOn(items=(name[:-3],))).items[name[:-3]]
    kind = follow_up_kind(label.kind, priority_of({"priority": "", "source": "report"}))
    if found is None:
        return f"reports/{name} has no follow-ups section; nothing added"
    aside = [f"not filed, {reason}: {text}" if text else f"not filed, {reason}" for reason, text in found.asides]
    if not found.items:
        return "\n".join([f"reports/{name} lists no follow-ups; nothing added", *aside])
    holders = {}
    for row in sorted(restaurant.rows("rail.tsv"), key=lambda row: row["state"] not in LIVE_TICKET_STATES):
        holders.setdefault(same_text(row["summary"]), f"{row['id']} ({row['state']})")
    lines = []
    for number, text in enumerate(found.items, 1):
        ref = f"{restaurant.dir.name}/reports/{name}#{number}"
        key = same_text(text)
        if key in holders:
            lines.append(f"skipped {ref}, same text as {holders[key]}: {text}")
        elif write:
            ident = append_ticket(restaurant, text, "report", ref, paths=quoted_paths(text, tracked), kind=kind, goal=label.goal)
            holders[key] = f"{ident} (waiting)"
            lines.append(f"{ident} added from {ref}: {text}")
        else:
            holders[key] = f"#{number} above"
            lines.append(f"would add from {ref}: {text}")
    return "\n".join(lines + aside)


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
