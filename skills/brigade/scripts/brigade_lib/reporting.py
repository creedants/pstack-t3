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
from brigade_lib.leases import holding_blocks
from brigade_lib.reviews import CLAIMS_LINE, CLAIMS_STATES
from brigade_lib.shared import BrigadeError, MalformedTable, clean, plural
from brigade_lib.store import CLOSED_GOAL_STATES, KINDS, Restaurant, holder_prefix, is_admin, parse_row, reporting_of, siblings, store_path
from brigade_lib.tickets import EVERY_ROW, Label, Tally, finished_requests, handed_count, intake_list, read_labels, request_files, sibling_named, tell

# A report shows each ticket and dish once, under the latest state it reached since the last report.
SECTIONS = {
    ("dish", "merged"): "Merged",
    ("dish", "passed"): "Passed review, not submitted",
    ("dish", "queued"): "Waiting to land",
    ("dish", "sent-back"): "Sent back after review",
    ("dish", "blocked"): "Blocked",
    ("mode", "full"): "Moved to full mode",
    ("dish", "in-progress"): "In progress",
    ("dish", "in-review"): "In progress",
    ("ticket", "waiting"): "New tickets, not started",
    ("ticket", "moved"): "Handed to another coordinator",
    ("dish", "dropped"): "Dropped",
    ("ticket", "dropped"): "Dropped",
}
# The report sections whose lines print a kind and can be grouped by goal.
GROUPED_SECTIONS = tuple(SECTIONS[key] for key in (("dish", "merged"), ("dish", "in-progress"), ("ticket", "waiting")))


def tally_words(tally):
    return (f"{len(tally.waiting)} waiting, {len(tally.in_flight)} in flight, {len(tally.merged)} merged, "
            f"{plural(tally.send_backs, 'send-back')}")


def goal_lines(labels, counted, every):
    """The text of goal list.

    A goal is listed when it is open or paused, or when it has a ticket waiting, an item in flight, or a ticket riding along.
    `every` lists each other goal too. The unassigned line is last.
    """
    lines = []
    for goal in labels.goals.values():
        tally, riding = counted.goals.get(goal.id, Tally()), counted.riding.get(goal.id, ())
        closed = goal.state in CLOSED_GOAL_STATES
        if closed and not every and not (tally.waiting or tally.in_flight or riding):
            continue
        words = tally_words(tally) + (f", {len(riding)} riding along" if riding else "") + (f", share {goal.share}" if goal.share else "")
        lines += [f"{goal.id} {goal.state} {goal.name}: {words}", f"  purpose: {goal.purpose}", f"  done when: {goal.done}"]
        if closed:
            lines.append(f"  closed: {goal.closed}")
    if not lines:
        lines.append("no goals" if every else "no open or paused goals")
    return "\n".join([*lines, f"unassigned: {tally_words(counted.goals.get('', Tally()))}"])


def status_label_lines(labels, counted):
    """Up to three lines of status. The counts by kind, the counts by goal, and the waiting tickets with no kind.

        kinds: feature 1 waiting; fix 1 waiting, 1 in flight; upkeep 1 in flight
        goals: G1 2 waiting, 1 in flight; G2 paused, 1 in flight; unassigned 2 waiting, 1 in flight
        untagged: 1

    An entry holds the waiting count and the in flight count that are not 0, and is absent when both are 0.
    kinds has an entry per kind in KINDS order. An item in flight with no kind is absent from kinds.
    goals has an entry per goal in file order and then one for unassigned work. A goal that is not open has its state
    after its id. The line is absent when goals.tsv has no row.
    A line with no entry is absent, and so is untagged at 0.
    """
    def entry(name, tally):
        found = [f"{len(ids)} {word}" for ids, word in ((tally.waiting, "waiting"), (tally.in_flight, "in flight")) if ids]
        return [f"{name} {', '.join(found)}"] if found else []

    lines = []
    kinds = [text for kind in KINDS for text in entry(kind, counted.kinds.get(kind, Tally()))]
    if kinds:
        lines.append(f"kinds: {'; '.join(kinds)}")
    goals = [text for goal in labels.goals.values()
             for text in entry(goal.id if goal.state == "open" else f"{goal.id} {goal.state},", counted.goals.get(goal.id, Tally()))]
    if labels.goals:
        goals += entry("unassigned", counted.goals.get("", Tally()))
    if goals:
        lines.append(f"goals: {'; '.join(goals)}")
    untagged = len(counted.kinds.get("", Tally()).waiting)
    if untagged:
        lines.append(f"untagged: {untagged}")
    return lines
# What `close` advises for an entry of each kind it warns about. Each value finishes the warning line.
REPAIR = {
    "link": "remove the link and leave its target as it is",
    "other": "move it out of reports/",
    "elsewhere": "replace the symbolic link at reports/ with a directory and leave the link's target as it is",
}


def counts(restaurant):
    tickets = [row["state"] for row in restaurant.rows("rail.tsv")]
    dishes = [row["state"] for row in restaurant.rows("dishes.tsv")]
    questions = [row for row in restaurant.rows("86.tsv") if row["state"] == "open"]
    return {
        "waiting tickets": tickets.count("waiting"),
        "in progress": dishes.count("in-progress"),
        "in review": dishes.count("in-review"),
        "passed review": dishes.count("passed"),
        "waiting to land": dishes.count("queued"),
        "sent back": dishes.count("sent-back"),
        "blocked": dishes.count("blocked"),
        "merged": dishes.count("merged"),
        "decisions for you": len(questions),
        "handed to you": handed_count(restaurant),
        "requests from the executive admin": len(request_files(restaurant)[0]),
    }


def status_line(restaurant):
    return ", ".join(f"{label}: {value}" for label, value in counts(restaurant).items() if value) or "nothing on record"


def unclaimed_reports(restaurant):
    """(id, state) of each dish in CLAIMS_STATES whose reports/<id>.md is a `file` with no line CLAIMS_LINE matches.

    The report is decoded as UTF-8 with replacement. An entry that report_stat does not call a `file` is not read.
    """
    unclaimed = []
    for dish in restaurant.rows("dishes.tsv"):
        name = f"{dish['id']}.md"
        if dish["state"] not in CLAIMS_STATES or seam.report_entry(restaurant, name) != "file":
            continue
        text = (restaurant.dir / "reports" / name).read_bytes().decode("utf-8", "replace")
        if not CLAIMS_LINE.search(text):
            unclaimed.append((dish["id"], dish["state"]))
    return unclaimed


def section_lines(title, items, dishes, labels):
    """The lines of one report section under its title.

    A line of a section in GROUPED_SECTIONS has the kind of its item or ticket before the id, when it has one.
    When a line of such a section has a goal, the section prints its lines under one `### <goal id> <goal name>` heading
    per goal in goals.tsv order, then `### Unassigned`. A heading with no line is absent.
    """
    grouped, bullets = title in GROUPED_SECTIONS, []
    for event in items:
        of_item = event["kind"] in ("dish", "mode")
        dish = dishes.get(event["id"], {}) if of_item else {}
        tickets = f" ({dish['tickets'].replace(',', ', ')})" if dish.get("tickets") else ""
        pr = f" {dish['pr']}" if dish.get("pr") else ""
        label = (labels.items if of_item else labels.tickets).get(event["id"], Label())
        kind = f"{label.kind} " if grouped and label.kind else ""
        bullets.append((label.goal, f"- {kind}{event['id']}{tickets}: {event['note']}{pr}"))
    if not grouped or not any(goal for goal, _ in bullets):
        return [*(text for _, text in bullets), ""]
    lines = []
    headings = [*((goal.id, f"### {goal.id} {goal.name}") for goal in labels.goals.values()), ("", "### Unassigned")]
    for goal, heading in headings:
        under = [text for found, text in bullets if found == goal]
        if under:
            lines += [heading, "", *under, ""]
    return lines


def events_since(restaurant, since):
    """The latest log row of each kind and id newer than since."""
    latest = {}
    for event in restaurant.rows("log.tsv"):
        if event["at"] > since:
            latest[(event["kind"], event["id"])] = event
    return latest


def report(restaurant, write=True):
    meta = restaurant.meta
    since = meta.get("lastReportAt") or ""
    latest = events_since(restaurant, since)
    sections = {title: [] for title in SECTIONS.values()}
    for (kind, _), event in latest.items():
        title = SECTIONS.get((kind, event["state"]))
        if title:
            sections[title].append(event)
    dishes = {row["id"]: row for row in restaurant.rows("dishes.tsv")}
    labels = read_labels(restaurant, EVERY_ROW)
    stamp = seam.now()
    lines = [f"# {meta['restaurant']} report", "", f"Since {since or 'opening'}. {status_line(restaurant)}.", ""]
    for title, items in sections.items():
        if items:
            lines += [f"## {title}", "", *section_lines(title, items, dishes, labels)]
    if is_admin(meta):
        lines += admin_sections(restaurant, since)
    questions = [row for row in restaurant.rows("86.tsv") if row["state"] == "open"]
    if questions:
        lines += ["## Decisions for you", ""]
        lines += [f"- {q['id']}: {q['question']} Options: {q['options']}. Default if no answer: {q['default']}." for q in questions]
        lines.append("")
    if len(lines) == 4:
        lines += ["Nothing new.", ""]
    text = "\n".join(lines)
    path = None
    if write:
        path = restaurant.dir / "closeouts" / f"{stamp[:26].replace(':', '')}.md"
        restaurant.write(path.relative_to(restaurant.dir), text)
        restaurant.change_meta(lastReportAt=stamp)
    return text, path


def display_root(path):
    path = Path(path)
    try:
        relative = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    if not relative.parts:
        return "~"
    return "~/" + relative.as_posix()


def leases_for(listing, prefix):
    """Lease ids from one `lease list`, held under this store's prefix. A failed listing holds none."""
    if listing.returncode != 0:
        return []
    found = []
    for line in listing.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3 or not re.fullmatch(r"L\d+", parts[0]):
            continue
        holder = parts[2]
        if holder.startswith(prefix):
            found.append(f"{parts[0]} ({holder.removeprefix(prefix)})")
    return found


def walk(root, stale_hours=24, repo=None):
    """Coordinators grouped by projectRoot. One land.py status and one lease list per root, outside any store lock."""
    wanted = str(Path(repo).resolve()) if repo is not None else None
    groups = {}
    for meta_path in sorted(root.glob("*/*/restaurant.json")):
        restaurant = Restaurant(meta_path.parent)
        project = restaurant.meta.get("projectRoot", "")
        if wanted is not None and project != wanted:
            continue
        groups.setdefault(project, []).append(restaurant)
    if not groups:
        if wanted is not None:
            return f"no restaurants for {wanted}", []
        return f"no restaurants under {root}", []
    lines, failures = [], []
    for project in sorted(groups):
        # The executive admin comes first in its repository.
        coordinators = sorted(groups[project], key=lambda restaurant: (not is_admin(restaurant.meta),
                                                                       str(restaurant.dir / "restaurant.json")))
        status = seam.land_result(project, "status")
        listing = seam.land_result(project, "lease", "list")
        header = status.stdout.strip() if status.returncode == 0 else "no landing contract"
        lines.append(f"{display_root(project)}: {header}")
        for restaurant in coordinators:
            meta = restaurant.meta
            age = datetime.now(timezone.utc) - datetime.fromisoformat(meta["lastActivityAt"])
            idle = f", idle {int(age.total_seconds() // 3600)}h" if age.total_seconds() > stale_hours * 3600 else ""
            try:
                counts = status_line(restaurant)
                blocked = len(holding_blocks(restaurant))
                questions = [row for row in restaurant.rows("86.tsv") if row["state"] == "open"]
            except MalformedTable as error:
                lines.append(f"  {meta['restaurant']}: {error}")
                failures.append(str(error))
                continue
            if blocked:
                counts = re.sub(r"(waiting tickets: \d+)", rf"\1 ({blocked} blocked)", counts, count=1)
            mode = f", mode {meta['mode']}" if meta.get("mode") else ""
            lines.append(f"  {meta['restaurant']} (reports {reporting_of(meta)}{mode}){idle}: {counts}")
            leases = leases_for(listing, holder_prefix(meta))
            lease_text = f", leases {', '.join(leases)}" if leases else ""
            lines.append(f"    thread {meta.get('thread') or 'not recorded'}{lease_text}")
            for question in questions:
                lines.append(f"    {question['id']}: {question['question']}")
    return "\n".join(lines), failures


REQUEST_NOTE = re.compile(r"^to (\S+): (.*)$")


def admin_requests(admin):
    """Each request the admin recorded, as (id, coordinator directory name, line)."""
    found = []
    for row in admin.rows("log.tsv"):
        match = REQUEST_NOTE.match(row["note"]) if row["kind"] == "request" else None
        if match:
            found.append((row["id"], match.group(1), match.group(2)))
    return found


def send_request(admin, to, line):
    """Record the request in the admin's log, then publish it into the coordinator's inbox under the admin's lock."""
    name, sibling = sibling_named(admin, to)
    line = clean(line)
    if not line:
        raise BrigadeError("a request needs its line")
    ident = f"A{max((int(ident[1:]) for ident, _, _ in admin_requests(admin)), default=0) + 1}"
    admin.log("request", ident, "sent", f"to {name}: {line}")
    admin.publish(admin.dir.parent / name / "inbox" / f"{ident}.line", line + "\n")
    return f"{ident} for {name}; {tell(name, sibling)}"


def relayed(admin):
    """The rows sync copied, as {relay id: the coordinator's row}."""
    return {row["id"]: json.loads(row["note"]) for row in admin.rows("log.tsv") if row["kind"] == "relay"}


def finished_by_coordinator(admin):
    """Each coordinator's finished requests, read from its own log.tsv. Run it before the admin's lock, as sync reads."""
    names = {name for _, name, _ in admin_requests(admin)}
    return {name: finished_requests(Restaurant(admin.dir.parent / name)) for name in names
            if (admin.dir.parent / name / "restaurant.json").is_file()}


def republish(admin, finished):
    """Publish again each request whose file is gone and whose coordinator has no inbox-done row for it."""
    lines = []
    for ident, name, line in admin_requests(admin):
        path = admin.dir.parent / name / "inbox" / f"{ident}.line"
        if path.exists() or ident in finished.get(name, ()):
            continue
        admin.publish(path, line + "\n")
        lines.append(f"{ident} republished for {name}")
    return "\n".join(lines) or "nothing to republish"


def ruling_line(row):
    line = (f"{row['id']} {row['state']} {row['kind']} ({row['parties']}): {row['question']} "
            f"Decided by {row['rule']}: {row['decision']}.")
    return line + (f" Supersedes {row['supersedes']}." if row["supersedes"] else "")


def _ruling_note(row, replacer):
    """The log note for a ruling row's current state. replacer maps an id to the ruling that replaced it."""
    state = row["state"]
    if state == "in-force":
        return row["decision"]
    if state in ("superseded", "overruled"):
        return f"replaced by {replacer[row['id']]}"
    return f"{row['decision']} ({state})"


def unlogged_rulings(admin):
    """Each rulings.tsv row whose state is not the latest ruling event, as (id, state, note).

    rulings.tsv is the authority. A new in-force row comes before the row it replaces, which is the order a ruling command logs them.
    """
    rows = admin.rows("rulings.tsv")
    latest = {}
    for event in admin.rows("log.tsv"):
        if event["kind"] == "ruling":
            latest[event["id"]] = event["state"]
    replacer = {row["supersedes"]: row["id"] for row in rows if row["supersedes"]}
    pending = sorted((row for row in rows if latest.get(row["id"]) != row["state"]), key=lambda row: row["state"] != "in-force")
    return [(row["id"], row["state"], _ruling_note(row, replacer)) for row in pending]


def log_rulings(admin):
    """Append a ruling event for each rulings.tsv row the log does not yet record. A rerun appends nothing."""
    with admin.guarded():
        for ident, state, note in unlogged_rulings(admin):
            admin.log("ruling", ident, state, note)


def add_ruling(admin, kind, parties, question, rule, decision, supersedes="", ended="superseded"):
    """Record a ruling, and mark the one it replaces, in one rewrite of rulings.tsv. Recording comes before carrying out."""
    rows = admin.rows("rulings.tsv")
    if supersedes:
        old = next((row for row in rows if row["id"] == supersedes), None)
        if old is None:
            raise BrigadeError(f"no {supersedes} in rulings.tsv")
        if old["state"] in ("superseded", "overruled"):
            raise BrigadeError(f"{supersedes} is already {old['state']}; replace the ruling that replaced it")
        old["state"] = ended
    ident = admin.next_id("rulings.tsv")
    rows.append({"id": ident, "at": seam.now(), "kind": kind, "parties": ", ".join(intake_list(parties)), "question": question,
                 "rule": rule, "decision": decision, "supersedes": supersedes, "state": "in-force"})
    admin.save_rows("rulings.tsv", rows)
    log_rulings(admin)
    return ident


def overrule(admin, ident, decision):
    _, old = admin.find("rulings.tsv", ident)
    return add_ruling(admin, old["kind"], old["parties"], old["question"], "user", decision, ident, ended="overruled")


def end_ruling(admin, ident, state):
    rows, row = admin.find("rulings.tsv", ident)
    if row["state"] == state:
        return f"{ident} {state}"
    if row["state"] != "in-force":
        raise BrigadeError(f"{ident} is {row['state']}; only an in-force ruling ends")
    row["state"] = state
    admin.save_rows("rulings.tsv", rows)
    log_rulings(admin)
    return f"{ident} {state}"


def log_rows(data, start):
    """The complete rows of a log.tsv snapshot that starts at byte start.

    Returns (offset, row) pairs, the byte just past the last newline consumed, and the offset of a malformed line or None.
    """
    rows, position = [], 0
    while (newline := data.find(b"\n", position)) >= 0:
        offset = start + position
        try:
            line = data[position:newline].decode()
        except UnicodeDecodeError:
            return rows, offset, offset
        if offset > 0:
            row = parse_row("log.tsv", line)
            if row is None:
                return rows, offset, offset
            rows.append((offset, row))
        position = newline + 1
    return rows, start + position, None


def sync(admin):
    """Copy each coordinator's new log.tsv rows into the admin's log as relay rows, once each.

    Each coordinator's bytes are copied under its shared lock. The admin's lock is taken only after that lock is
    released, so no command holds two stores' locks. A relay id is the coordinator's store path and the row's byte
    offset, which a tail repair never moves, so a rerun after a crash copies nothing twice.
    A document item row is relayed but prints nothing.
    Returns the lines to print and the malformed-line failures.
    """
    cursors = admin.meta.get("cursors") or {}
    copied, failures = [], []
    for name in siblings(admin.dir, admin.meta.get("projectRoot")):
        directory = admin.dir.parent / name
        store = store_path(directory)
        start = int(cursors.get(store, 0))
        data = Restaurant(directory).snapshot("log.tsv", start)
        if data is None:
            continue
        rows, end, malformed = log_rows(data, start)
        copied.append((name, store, rows, end))
        if malformed is not None:
            failures.append(f"{store}/log.tsv at byte {malformed} is malformed; nothing past it relayed")
    lines = []
    with admin.guarded():
        have = relayed(admin)
        cursors = dict(admin.meta.get("cursors") or {})
        for name, store, rows, end in copied:
            for offset, row in rows:
                ident = f"{store}@{offset}"
                if ident in have:
                    continue
                admin.append("log.tsv", {"at": seam.now(), "kind": "relay", "id": ident, "state": "copied",
                                         "note": json.dumps(row, separators=(",", ":"))})
                if row["kind"] != "doc":
                    lines.append(f"{name} {row['at']} {row['kind']} {row['id']} {row['state']}: {row['note']}")
            # A sync that started later may have read further and moved this cursor already.
            cursors[store] = max(end, int(cursors.get(store, 0)))
        admin.change_meta(cursors=cursors, lastActivityAt=seam.now())
    return "\n".join(lines), failures


def admin_sections(admin, since):
    """The admin's report adds the rulings recorded or changed since the last one, each coordinator's relayed
    events except document items, and each coordinator's newest report."""
    events = [event for event in admin.rows("log.tsv") if event["at"] > since]
    changed = {event["id"] for event in events if event["kind"] == "ruling"}
    changed.update(ident for ident, _, _ in unlogged_rulings(admin))
    lines = []
    rulings = [row for row in admin.rows("rulings.tsv") if row["id"] in changed]
    if rulings:
        lines += ["## Rulings", "", *(f"- {ruling_line(row)}" for row in rulings), ""]
    by_store = {}
    for event in events:
        if event["kind"] == "relay":
            by_store.setdefault(event["id"].rpartition("@")[0], []).append(json.loads(event["note"]))
    blocks = []
    for name in siblings(admin.dir, admin.meta.get("projectRoot")):
        directory = admin.dir.parent / name
        closeouts = sorted((directory / "closeouts").glob("*.md"))
        rows = [row for row in by_store.get(store_path(directory), []) if row["kind"] != "doc"]
        if not rows and not closeouts:
            continue
        blocks += [f"### {name}", "", *(f"- {row['at']} {row['kind']} {row['id']} {row['state']}: {row['note']}" for row in rows)]
        blocks += [f"- Newest report: {closeouts[-1]}"] if closeouts else []
        blocks.append("")
    if blocks:
        lines += ["## From each coordinator", "", *blocks]
    return lines
