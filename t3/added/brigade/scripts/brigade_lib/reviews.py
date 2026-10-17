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
from brigade_lib.edits import HEADING, NEEDS_COMMANDS, commit_tree, replay, report_edits
from brigade_lib.reports import NOT_A_REPORT, REVIEW_FILE, in_reports, report_stat
from brigade_lib.shared import BrigadeError, clean, fence_close, one_of, stored, threads_fence_open
from brigade_lib.store import CHILD_ROLES, EDITS_VERDICT, OWING_STATES, REASONS, REASON_VERDICTS, ROUND_BUDGET, ROUND_DECISIONS, ROUND_KIND, ROUND_PARKED, THREADS_WRITERS, UNRECORDED_REASON, VERDICTS, WORKER_ROLES, roles_script

CLAIMS_LINE = re.compile(r"^[ \t#>*+\-_`\d.)]*Claims check", re.M)
CLAIMS_STATES = ("in-review", "passed")
# The heading a worker report puts its child threads under, and the form of one item under it.
THREADS_SECTION = (2, "Threads")
THREADS_ITEM = re.compile(r"^- ([a-z]+(?: [a-z]+)*) `([^`]+)` (\S+)$")
# A recognized list-item prefix triggers refusal when THREADS_ITEM does not match.
THREADS_ITEM_START = re.compile(r"[-*+\u2022]|\d+[.)]")
# A backticked thread id, that is a backticked value holding a colon, wherever it sits on the line.
THREADS_ITEM_THREAD = re.compile(r"`[^`]*:[^`]*`")
# Why report_threads cannot read one line of a report's ## Threads section. Each value finishes `line <n> `.
THREADS_REFUSED = {
    "item": "is not one list item of the form - <role> `<thread id>` <seat>",
    "id": "holds a thread id that the table would change",
    "deeper": "is a heading deeper than the ## Threads heading",
    "again": "is a second ## Threads heading",
    "role": "holds role {role!r}, which is not {roles}",
    "writer": "holds role {role!r}, which {writer} writes",
}
# One refusal for every line report_threads could not read. `lines` is the joined per-line reasons.
THREADS_REFUSED_LINE = "{dish}: reports/{name} cannot be read; {lines}; nothing recorded"


@functools.cache
def roles_family():
    """roles.py's family, loaded in process.

    pass record asks under the store lock, which forbids a subprocess.
    runpy writes no __pycache__ under skills/.
    """
    return runpy.run_path(str(roles_script()))["family"]


def cross_family(row):
    family = roles_family()
    return family(row["author"]) != family(row["verifier"]) and not row["note"].startswith("same model family")


def item_verdicts(rows, dish_id):
    """The rows that carry the item's verdict. A panel member's row is on record and decides nothing."""
    return [row for row in rows if row["dish"] == dish_id and not row["member"]]


def send_backs(rows, dish_id):
    return [row for row in item_verdicts(rows, dish_id) if row["verdict"] == "send-back"]


def round_count(restaurant, dish_id):
    """The item's send-backs written after the later of its last passing verdict and its last answered round decision.

    Only a row `86 add --round-budget` wrote is a round decision. Another decision's answer never settles the count.
    """
    verdicts = item_verdicts(restaurant.rows("pass.tsv"), dish_id)
    settled = [row["at"] for row in verdicts if row["verdict"] == "pass"]
    settled += [row["answered"] for row in restaurant.rows("86.tsv")
                if row["dish"] == dish_id and row["kind"] == ROUND_KIND and row["state"] == "answered"]
    since = max(settled, default="")
    return sum(row["verdict"] == "send-back" and row["at"] > since for row in verdicts)


def round_debt(restaurant, dish):
    """The item's round count when it owes its round decision, else 0.

    The budget stops another fix round, so only an item that could get one owes.
    """
    if dish["state"] not in OWING_STATES:
        return 0
    count = round_count(restaurant, dish["id"])
    return count if count >= ROUND_BUDGET else 0


def fix_round_refusal(restaurant, dish):
    """Why the item gets no fix round now, or None."""
    debt = round_debt(restaurant, dish)
    if not debt:
        return None
    return (f"{dish['id']} has {debt} send-backs; no fix round until its decision is answered; "
            f"run 86 add --dish {dish['id']} --round-budget")


def round_decision(restaurant, dish_id):
    """The question, options, and default of the decision an item owes at ROUND_BUDGET send-backs."""
    _, dish = restaurant.find("dishes.tsv", dish_id)
    if dish["state"] not in OWING_STATES:
        raise BrigadeError(f"{dish_id} is {dish['state']}; it owes no round decision")
    count = round_count(restaurant, dish_id)
    if count < ROUND_BUDGET:
        raise BrigadeError(f"{dish_id} has {count} send-back{'' if count == 1 else 's'} since its last pass or round decision; "
                           f"the decision opens at {ROUND_BUDGET}")
    question = (f"{dish_id} was sent back {count} times. Accept the remaining findings as known limits and land it, "
                "send it through architect for a redesign, or drop it?")
    report = send_backs(restaurant.rows("pass.tsv"), dish_id)[-1]["report"]
    if report:
        question += f" Findings: reports/{report}."
    return question, ", ".join((*ROUND_DECISIONS, ROUND_PARKED)), ROUND_PARKED


def latest_verdict(restaurant, dish_id, sha):
    restaurant.find("dishes.tsv", dish_id)
    verdicts = [row for row in item_verdicts(restaurant.rows("pass.tsv"), dish_id) if row["sha"] == sha]
    return verdicts[-1] if verdicts else None


def pass_json(row):
    return json.dumps({"verdict": row["verdict"], "author": row["author"], "verifier": row["verifier"],
                       "note": row["note"], "crossFamily": cross_family(row)}, indent=2)


def pass_check(restaurant, dish_id, sha):
    latest = latest_verdict(restaurant, dish_id, sha)
    if latest is None:
        return False, f"{dish_id} has no review verdict for {sha}"
    if latest["verdict"] != "pass":
        return False, f"{dish_id} at {sha}: {latest['verdict']} ({latest['note'] or 'no note'})"
    return True, f"{dish_id} at {sha} passed review by {latest['verifier']}"


def reviewed_items(rows, last):
    """The last `last` items that have a verdict, newest first, each with its non-member rows.

    An item is a dish with at least one non-member row of pass.tsv. Its rows keep the table's order, so its
    first row is its first verdict. An item's author is the author cell of that first verdict row.
    """
    by_dish = {}
    for row in rows:
        if not row["member"]:
            by_dish.setdefault(row["dish"], []).append(row)
    newest_first = sorted(by_dish.items(), key=lambda pair: pair[1][-1]["at"], reverse=True)
    return newest_first[:last]


def reason_counts(items):
    """How many send-back rows of these items carry each reason, and how many carry none.

    A row whose reason cell is empty, and a row whose reason cell is not one of REASONS, carries none.
    """
    counts = dict.fromkeys(REASONS, 0)
    counts[UNRECORDED_REASON] = 0
    for _, item_rows in items:
        for row in item_rows:
            if row["verdict"] == "send-back":
                counts[row["reason"] if row["reason"] in REASONS else UNRECORDED_REASON] += 1
    return counts


def pass_rate(passed, items):
    return f"{passed} of {items} ({100 * passed / items:.1f}%)"


def pass_stats(restaurant, last):
    """The first-pass rate overall and per author, and send-back counts by reason, over the last `last` items.

    An item's first pass is its first non-member row in table order, and it is a first pass when that row's
    verdict is `pass`; any other verdict, including pass-with-edits and blocked, is not. An item's author is
    that row's author. Send-back counts cover every non-member row of the selected items whose verdict is
    send-back, and a row with no reason, or a reason not in REASONS, counts as unrecorded. Items are newest
    first by their last verdict.
    """
    with restaurant.read_lock(create=False):
        items = reviewed_items(restaurant.rows("pass.tsv"), last)
    if not items:
        return "no items with a verdict"
    first = [item_rows[0] for _, item_rows in items]
    lines = [f"last {len(items)} item{'' if len(items) == 1 else 's'} with a verdict",
             f"first pass overall: {pass_rate(sum(row['verdict'] == 'pass' for row in first), len(items))}"]
    by_author = {}
    for row in first:
        by_author.setdefault(row["author"], []).append(row["verdict"] == "pass")
    for author in sorted(by_author):
        passes = by_author[author]
        lines.append(f"first pass {author}: {pass_rate(sum(passes), len(passes))}")
    counts = reason_counts(items)
    named = [f"{reason} {counts[reason]}" for reason in (*REASONS, UNRECORDED_REASON) if counts[reason]]
    lines.append(f"send-backs: {', '.join(named) if named else 'none'}")
    return "\n".join(lines)


def review_report(restaurant, dish_id, report):
    """The bare name of `report`.

    A name that is not a review report of the item is refused. So is a path that in_reports turns down, and so is a
    name for which report_entry does not call reports/<name> a `file`.
    """
    name = Path(report).name
    match = REVIEW_FILE.fullmatch(name)
    if not match or match.group(1) != dish_id:
        raise BrigadeError(f"{name} is not a review report of {dish_id}; name a file like reports/{dish_id}-review-1.md")
    if not in_reports(restaurant, report, name):
        raise BrigadeError(f"{report} is outside this store's reports/; name reports/{name}")
    kind = seam.report_entry(restaurant, name)
    if kind == "missing":
        raise BrigadeError(f"reports/{name} does not exist; write the review report first")
    if kind != "file":
        raise BrigadeError(f"reports/{name} {NOT_A_REPORT[kind]}; nothing recorded")
    return name


def threads_prose(line):
    """Whether a non-blank line of the ## Threads section is plain prose, so report_threads skips it.

    A prose line starts, after any leading whitespace, with neither a list marker nor a `#`, holds no
    backticked thread id, that is no backticked value holding a colon, and names no child role. A list
    marker is a hyphen, an asterisk, a plus, a bullet, or a number followed by a dot or a closing
    parenthesis. Every other line that fails THREADS_ITEM is refused by its number.
    """
    stripped = line.lstrip()
    if THREADS_ITEM_START.match(stripped) or stripped.startswith("#"):
        return False
    if THREADS_ITEM_THREAD.search(line):
        return False
    return not any(role in line for role in CHILD_ROLES)


def report_threads(text):
    """The children a report's ## Threads section lists, and every line that could not be read.

    The children are a tuple of dicts holding `role`, `thread`, and `seat`. The unreadable lines are a
    tuple of `(line number, reason)` pairs. A fenced block that opens after up to three spaces is
    skipped, so no line inside one is read. A report with no ## Threads section holds no child. A line
    with no text is skipped, and so is a line of the section that is plain prose, that is a line that
    starts, after any leading whitespace, with neither a list marker nor a `#`, holds no backticked thread
    id, and names no child role. A list marker is a hyphen, an asterisk, a plus, a bullet, or a number
    followed by a dot or a closing parenthesis. A line that is not prose and is not one item of the form
    - <role> `<thread id>` <seat> is reported by its number. A heading deeper than the section's own or a
    second ## Threads heading while the first is still open is reported by its number and ends the scan. An
    item whose thread id the table would change is reported by its number too. A role that is well formed
    but is not one of CHILD_ROLES is reported by its number, and named with the command that writes it when
    a role of THREADS_WRITERS names it.
    """
    children, unreadable = [], []
    level, fence = 0, None
    for number, line in enumerate(text.splitlines(), 1):
        if fence:
            if fence_close(line, fence):
                fence = None
            continue
        opened = threads_fence_open(line)
        if opened:
            fence = opened
            continue
        heading = HEADING.match(line)
        if heading:
            depth, name = len(heading.group(1)), heading.group(2)
            if (depth, name) == THREADS_SECTION:
                if level:
                    unreadable.append((number, THREADS_REFUSED["again"]))
                    break
                level = depth
            elif depth <= level:
                level = 0
            elif level:
                unreadable.append((number, THREADS_REFUSED["deeper"]))
                break
            continue
        if not level or not line.strip():
            continue
        item = THREADS_ITEM.match(line)
        if not item:
            if not threads_prose(line):
                unreadable.append((number, THREADS_REFUSED["item"]))
            continue
        role, thread, seat = item.groups()
        if clean(thread) != thread:
            unreadable.append((number, THREADS_REFUSED["id"]))
            continue
        if role in THREADS_WRITERS:
            unreadable.append((number, THREADS_REFUSED["writer"].format(role=role, writer=THREADS_WRITERS[role])))
            continue
        if role not in CHILD_ROLES:
            unreadable.append((number, THREADS_REFUSED["role"].format(role=role, roles=one_of(CHILD_ROLES))))
            continue
        children.append({"role": role, "thread": thread, "seat": seat})
    return tuple(children), tuple(unreadable)


def report_threads_refusal(restaurant, dish_id, unreadable):
    """One BrigadeError text naming the item, its report, and every line report_threads could not read."""
    lines = "; ".join(f"line {number} {reason}" for number, reason in unreadable)
    return THREADS_REFUSED_LINE.format(dish=dish_id, name=f"{dish_id}.md", lines=lines)


def worker_report(restaurant, dish_id):
    """The text of reports/<dish>.md, or None when the item has no report.

    A report that is a symbolic link, is not a regular file, or resolves outside this store's reports/ is
    refused with the words the store already uses for that kind. So is one that is not UTF-8.
    """
    name = f"{dish_id}.md"
    kind = seam.report_entry(restaurant, name)
    if kind == "missing":
        return None
    if kind != "file":
        raise BrigadeError(f"reports/{name} {NOT_A_REPORT[kind]}; nothing recorded")
    try:
        return (restaurant.dir / "reports" / name).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise BrigadeError(f"reports/{name} is not UTF-8; nothing recorded") from None


def thread_binding(row):
    """The identity `write_threads` de-duplicates a row on, as the table stores it."""
    return (row["attempt"], row["role"], stored("threads.tsv", "thread", row["thread"]))


def write_threads(restaurant, dish_id, threads, closing=False, keep_thread=""):
    """The one writer of threads.tsv. Returns True when it wrote a row or closed one.

    `threads` is a sequence of `(role, thread, seat)`. A worker role's attempt is one more than the worker
    rows the item already holds for another thread, and every other role's is that count, or 1 when the
    item holds none. A worker role's row is written open, with its start at this write, and its role is
    worker at attempt 1 and fix worker at every later one. Every other role's row is written closed, with
    its end at this write. A thread id is compared and written as the table stores it, because save_rows
    writes the cell through clean. `closing` writes this stamp into the end of every open worker row of
    the item but one whose stored thread is the stored `keep_thread`. A row whose item, attempt, role,
    and stored thread a row already holds is dropped, so a rerun that finds the same item, attempt, role,
    and thread writes no row. When every row is dropped and nothing closes, no file changes.
    """
    rows = restaurant.rows("threads.tsv")
    mine = [row for row in rows if row["dish"] == dish_id]
    stamp = seam.now()
    keep = stored("threads.tsv", "thread", keep_thread) if keep_thread else ""
    held = {thread_binding(row) for row in mine}
    written = []
    for role, thread, seat in threads:
        thread = stored("threads.tsv", "thread", thread)
        worker = role in WORKER_ROLES
        used = sum(1 for row in mine if row["role"] in WORKER_ROLES and row["thread"] != thread)
        number = used + 1 if worker else max(used, 1)
        if worker:
            role = "worker" if number == 1 else "fix worker"
        if (str(number), role, thread) in held:
            continue
        held.add((str(number), role, thread))
        written.append({"at": stamp, "dish": dish_id, "attempt": number, "role": role, "thread": thread,
                        "seat": seat, "start": stamp if worker else "", "end": "" if worker else stamp})
    closed = False
    if closing:
        for row in mine:
            if row["role"] in WORKER_ROLES and not row["end"] and row["thread"] != keep:
                row["end"] = stamp
                closed = True
    if not written and not closed:
        return False
    restaurant.save_rows("threads.tsv", rows + written)
    return True


def record_pass(restaurant, dish_id, pr, sha, verdict, author, verifier, note="", same_family=False, report="",
                member=False, late=False, edits="", reviewer_thread="", reason=""):
    _, dish = restaurant.find("dishes.tsv", dish_id)
    if verdict not in VERDICTS:
        raise BrigadeError(f"verdict must be one of {', '.join(VERDICTS)}")
    if reason and verdict not in REASON_VERDICTS:
        raise BrigadeError(f"--reason goes only with --verdict {', '.join(REASON_VERDICTS)}")
    if reviewer_thread and member:
        raise BrigadeError("--member takes no --reviewer-thread; record a panel member's row with "
                           f"thread {dish_id} --role \"panel member\" --thread <id> --seat <provider/model>")
    if roles_family()(author) == roles_family()(verifier) and not same_family:
        raise BrigadeError(f"verifier {verifier} is the same model family as author {author}; "
                           "pick a verifier from another family, or pass --same-family when no other family is runnable")
    if same_family:
        note = clean(f"same model family; {note}")
    if verdict == EDITS_VERDICT:
        if not edits:
            raise BrigadeError(f"--verdict {EDITS_VERDICT} needs --edits <review report>")
        if report:
            raise BrigadeError("--edits names the round's report; drop --report")
        report = edits
    elif edits:
        raise BrigadeError(f"--edits goes only with --verdict {EDITS_VERDICT}")
    if report:
        report = review_report(restaurant, dish_id, report)
    if verdict == EDITS_VERDICT:
        report_edits(restaurant, report)
    if verdict == "pass" and not member:
        edits_gate(restaurant, dish_id, sha, report)
    # Read the report before the append, so a line the parser refuses leaves no pass row and no thread row.
    body = worker_report(restaurant, dish_id)
    children, unreadable = report_threads(body) if body else ((), ())
    if unreadable:
        raise BrigadeError(report_threads_refusal(restaurant, dish_id, unreadable))
    restaurant.append("pass.tsv", {"at": seam.now(), "dish": dish_id, "pr": pr, "sha": sha, "verdict": verdict,
                                   "author": author, "verifier": verifier, "note": note, "report": report,
                                   "member": "yes" if member else "", "reason": reason})
    rows = [("reviewer", reviewer_thread, verifier)] if reviewer_thread else []
    rows += [(child["role"], child["thread"], child["seat"]) for child in children]
    write_threads(restaurant, dish_id, rows)
    if member:
        return f"{dish_id}: member {verdict} on record; {dish_id} stays {dish['state']}"
    if late:
        printed = f"{dish_id}: late {verdict} on record; {dish_id} stays {dish['state']}"
    else:
        dish = restaurant.update("dishes.tsv", dish_id, "dish", state=VERDICTS[verdict], pr=pr, sha=sha)
        printed = f"{dish_id} {dish['state']}"
    debt = round_debt(restaurant, dish) if verdict == "send-back" else 0
    if debt:
        return f"{printed}; {debt} send-backs, decision pending"
    return printed


def edits_gate(restaurant, dish_id, sha, report):
    """Refuse a pass while the item's last verdict row is pass-with-edits.

    Two things lift it. One is an edits.tsv row for that round's sha, this sha, and that round's report.
    The other is a `report` that is not that round's report.
    """
    verdicts = item_verdicts(restaurant.rows("pass.tsv"), dish_id)
    if not verdicts or verdicts[-1]["verdict"] != EDITS_VERDICT:
        return
    last = verdicts[-1]
    if any(row["dish"] == dish_id and (row["from"], row["to"], row["report"]) == (last["sha"], clean(sha), last["report"])
           for row in restaurant.rows("edits.tsv")):
        return
    if report and report != last["report"]:
        return
    raise BrigadeError(f"{dish_id}'s last verdict is {EDITS_VERDICT} at {last['sha']}; run edits verify {dish_id} "
                       f"--from {last['sha']} --to {sha}, or name a second review's report with --report")


def verify_edits(restaurant, dish_id, from_sha, to_sha):
    """Called from run(), because it starts subprocesses. It holds the store lock for its first read and for its write, and never while a subprocess runs."""
    with restaurant.checked():
        restaurant.fence()
        restaurant.find("dishes.tsv", dish_id)
        rounds = [row for row in item_verdicts(restaurant.rows("pass.tsv"), dish_id)
                  if row["verdict"] == EDITS_VERDICT and row["sha"] == from_sha]
        if not rounds:
            raise BrigadeError(f"{dish_id} has no {EDITS_VERDICT} round at {from_sha}")
        meta = restaurant.meta
        build, check = meta.get("buildCommand"), meta.get("checkCommand")
        if not build or not check:
            raise BrigadeError(NEEDS_COMMANDS)
    name = rounds[-1]["report"]
    kind = seam.report_entry(restaurant, name)
    if kind != "file":
        raise BrigadeError(f"reports/{name} {NOT_A_REPORT[kind]}; nothing verified")
    edits = report_edits(restaurant, name)
    (reviewed, before), (_, after) = (commit_tree(meta["projectRoot"], sha) for sha in (from_sha, to_sha))
    lines, verified = replay(meta["projectRoot"], reviewed, before, after, edits, build, check, from_sha)
    for line in lines:
        print(line)
    if not verified:
        raise BrigadeError(f"{dish_id}: edits not verified, {from_sha} to {to_sha}")
    row = {"dish": dish_id, "from": clean(from_sha), "to": clean(to_sha), "report": name}
    with restaurant.checked():
        restaurant.fence()
        if not any(all(recorded[key] == value for key, value in row.items()) for recorded in restaurant.rows("edits.tsv")):
            restaurant.append("edits.tsv", {"at": seam.now(), **row})
    return f"{dish_id}: edits verified, {from_sha} to {to_sha}"


def unrecorded_reviews(restaurant):
    """(name, kind) for each entry under reports/ named like a review report that no pass.tsv row accounts for.

    The kind is report_stat's. A row of the entry's dish that names it in `report` accounts for it, and nothing is read
    from that entry. A row that names no report cannot say which file it reviewed, so it accounts for every `file` of its
    dish written at or before it, and for no entry of another kind. The time compared is the one in report_stat's status.
    An entry that report_stat calls `missing` is left out.
    """
    rows = restaurant.rows("pass.tsv")
    reports = restaurant.dir / "reports"
    unrecorded = []
    for path in sorted(reports.iterdir()) if reports.is_dir() else []:
        match = REVIEW_FILE.fullmatch(path.name)
        if not match:
            continue
        mine = [row for row in rows if row["dish"] == match.group(1)]
        if any(row["report"] == path.name for row in mine):
            continue
        kind, status = report_stat(restaurant, path.name)
        if kind == "file" and any(datetime.fromisoformat(row["at"].replace("Z", "+00:00")).timestamp() >= status.st_mtime
                                  for row in mine if not row["report"]):
            continue
        if kind != "missing":
            unrecorded.append((path.name, kind))
    return unrecorded
