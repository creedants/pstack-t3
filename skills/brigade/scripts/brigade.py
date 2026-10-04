#!/usr/bin/env python3
"""Bookkeeping for brigade restaurants.

A restaurant is one directory under the brigade store. Its tables hold current
state and log.tsv holds every change, so a report can say what is new since
the last one. The head chef thread is the only writer.

Kitchen words name files and commands. Output is plain engineering prose.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TICKET_STATES = ("waiting", "assigned", "done", "dropped")
DISH_STATES = ("in-progress", "in-review", "passed", "sent-back", "blocked", "merged", "dropped")
VERDICTS = {"pass": "passed", "send-back": "sent-back", "blocked": "blocked"}
MERGE_POLICIES = ("pass", "pr-only", "local-only")

TABLES = {
    "rail.tsv": ("id", "at", "state", "source", "ref", "dish", "summary"),
    "dishes.tsv": ("id", "at", "state", "station", "tickets", "task", "thread", "branch", "pr", "sha", "summary"),
    "pass.tsv": ("at", "dish", "pr", "sha", "verdict", "author", "verifier", "note"),
    "86.tsv": ("id", "at", "state", "dish", "question", "options", "default", "answer"),
    "log.tsv": ("at", "kind", "id", "state", "note"),
}
PREFIX = {"rail.tsv": "T", "dishes.tsv": "D", "86.tsv": "Q"}
# A report shows each ticket and dish once, under the latest state it reached since the last report.
SECTIONS = {
    ("dish", "merged"): "Merged",
    ("dish", "passed"): "Passed review, not merged",
    ("dish", "sent-back"): "Sent back after review",
    ("dish", "blocked"): "Blocked",
    ("dish", "in-progress"): "In progress",
    ("dish", "in-review"): "In progress",
    ("ticket", "waiting"): "New tickets, not started",
    ("dish", "dropped"): "Dropped",
    ("ticket", "dropped"): "Dropped",
}

MENU = """# Menu: {restaurant}

## Purpose

What this restaurant exists to achieve, in one or two sentences.

## What good looks like

Checkable outcomes, one per line.

## Off the menu

Work this restaurant does not take, even when asked.

## Budget

The pstack roles budget (`default`, `small`, `medium`, `large`, `unlimited`) and any cap on parallel workers.
"""

HOUSE_RULES = """# House rules: {restaurant}

Paste these into every brief verbatim.

1. Write in plain engineering prose. brigade's kitchen terms name its files and commands only. Never use them in replies, reports, commits, or PRs.
2. Merge policy: {merge_policy}.
"""


class BrigadeError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def slug(text):
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not value:
        raise BrigadeError(f"cannot make a slug from {text!r}")
    return value


def store_root(flag=None):
    if flag:
        return Path(flag)
    if os.environ.get("BRIGADE_STORE"):
        return Path(os.environ["BRIGADE_STORE"])
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "pstack-t3" / "brigade"


def clean(value):
    return re.sub(r"[\t\r\n]+", " ", str(value if value is not None else "")).strip()


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, suffix=".tmp") as handle:
        handle.write(text)
    os.replace(handle.name, path)


class Restaurant:
    def __init__(self, directory):
        self.dir = Path(directory)
        if not (self.dir / "restaurant.json").is_file():
            raise BrigadeError(f"{self.dir} is not a restaurant (no restaurant.json); run brigade.py open")

    @property
    def meta(self):
        return json.loads((self.dir / "restaurant.json").read_text())

    def save_meta(self, meta):
        write_atomic(self.dir / "restaurant.json", json.dumps(meta, indent=2) + "\n")

    def rows(self, table):
        path = self.dir / table
        if not path.exists():
            return []
        lines = path.read_text().splitlines()
        header = TABLES[table]
        return [dict(zip(header, line.split("\t"))) for line in lines[1:] if line]

    def save_rows(self, table, rows):
        header = TABLES[table]
        body = ["\t".join(header)] + ["\t".join(clean(row.get(key, "")) for key in header) for row in rows]
        write_atomic(self.dir / table, "\n".join(body) + "\n")

    def append(self, table, row):
        path = self.dir / table
        if not path.exists():
            self.save_rows(table, [])
        with path.open("a") as handle:
            handle.write("\t".join(clean(row.get(key, "")) for key in TABLES[table]) + "\n")

    def log(self, kind, ident, state, note=""):
        self.append("log.tsv", {"at": now(), "kind": kind, "id": ident, "state": state, "note": note})
        meta = self.meta
        meta["lastActivityAt"] = now()
        self.save_meta(meta)

    def next_id(self, table):
        numbers = [int(row["id"][1:]) for row in self.rows(table) if row.get("id", "")[1:].isdigit()]
        return f"{PREFIX[table]}{max(numbers, default=0) + 1}"

    def find(self, table, ident):
        rows = self.rows(table)
        for row in rows:
            if row["id"] == ident:
                return rows, row
        raise BrigadeError(f"no {ident} in {table}")

    def update(self, table, ident, kind, **fields):
        rows, row = self.find(table, ident)
        changed = {key: value for key, value in fields.items() if value is not None}
        row.update(changed)
        self.save_rows(table, rows)
        if "state" in changed:
            self.log(kind, ident, changed["state"], row.get("summary") or row.get("question", ""))
        return row


def open_restaurant(root, project_root, name, merge_policy="pass"):
    project_root = Path(project_root).resolve()
    directory = root / slug(project_root.name) / slug(name)
    created = not (directory / "restaurant.json").exists()
    if created:
        directory.mkdir(parents=True, exist_ok=True)
        meta = {"restaurant": name, "projectRoot": str(project_root), "mergePolicy": merge_policy,
                "openedAt": now(), "lastActivityAt": now(), "lastReportAt": None, "thread": None, "schedules": {}}
        write_atomic(directory / "restaurant.json", json.dumps(meta, indent=2) + "\n")
    meta = json.loads((directory / "restaurant.json").read_text())
    for filename, template in (("menu.md", MENU), ("house-rules.md", HOUSE_RULES)):
        if not (directory / filename).exists():
            write_atomic(directory / filename, template.format(restaurant=meta["restaurant"], merge_policy=meta["mergePolicy"]))
    restaurant = Restaurant(directory)
    for table in TABLES:
        if not (directory / table).exists():
            restaurant.save_rows(table, [])
    return restaurant, created


def counts(restaurant):
    tickets = [row["state"] for row in restaurant.rows("rail.tsv")]
    dishes = [row["state"] for row in restaurant.rows("dishes.tsv")]
    questions = [row for row in restaurant.rows("86.tsv") if row["state"] == "open"]
    return {
        "waiting tickets": tickets.count("waiting"),
        "in progress": dishes.count("in-progress"),
        "in review": dishes.count("in-review"),
        "passed review": dishes.count("passed"),
        "sent back": dishes.count("sent-back"),
        "blocked": dishes.count("blocked"),
        "merged": dishes.count("merged"),
        "decisions for you": len(questions),
    }


def status_line(restaurant):
    return ", ".join(f"{label}: {value}" for label, value in counts(restaurant).items() if value) or "nothing on record"


def pass_check(restaurant, dish_id, sha):
    _, dish = restaurant.find("dishes.tsv", dish_id)
    verdicts = [row for row in restaurant.rows("pass.tsv") if row["dish"] == dish_id and row["sha"] == sha]
    if not verdicts:
        return False, f"{dish_id} has no review verdict for {sha}"
    latest = verdicts[-1]
    if latest["verdict"] != "pass":
        return False, f"{dish_id} at {sha}: {latest['verdict']} ({latest['note'] or 'no note'})"
    return True, f"{dish_id} at {sha} passed review by {latest['verifier']}"


def record_pass(restaurant, dish_id, pr, sha, verdict, author, verifier, note="", same_family=False):
    if verdict not in VERDICTS:
        raise BrigadeError(f"verdict must be one of {', '.join(VERDICTS)}")
    family = lambda model: model.split("/")[-1].split("-")[0].lower()
    if family(author) == family(verifier) and not same_family:
        raise BrigadeError(f"verifier {verifier} is the same model family as author {author}; "
                           "pick a verifier from another family, or pass --same-family when no other family is runnable")
    if same_family:
        note = clean(f"same model family; {note}")
    restaurant.append("pass.tsv", {"at": now(), "dish": dish_id, "pr": pr, "sha": sha, "verdict": verdict,
                                   "author": author, "verifier": verifier, "note": note})
    return restaurant.update("dishes.tsv", dish_id, "dish", state=VERDICTS[verdict], pr=pr, sha=sha)


def report(restaurant, write=True):
    meta = restaurant.meta
    since = meta.get("lastReportAt") or ""
    latest = {}
    for event in restaurant.rows("log.tsv"):
        if event["at"] > since:
            latest[(event["kind"], event["id"])] = event
    sections = {title: [] for title in SECTIONS.values()}
    for (kind, _), event in latest.items():
        title = SECTIONS.get((kind, event["state"]))
        if title:
            sections[title].append(event)
    dishes = {row["id"]: row for row in restaurant.rows("dishes.tsv")}
    stamp = now()
    lines = [f"# {meta['restaurant']} report", "", f"Since {since or 'opening'}. {status_line(restaurant)}.", ""]
    for title, items in sections.items():
        if not items:
            continue
        lines += [f"## {title}", ""]
        for event in items:
            dish = dishes.get(event["id"], {}) if event["kind"] == "dish" else {}
            tickets = f" ({dish['tickets'].replace(',', ', ')})" if dish.get("tickets") else ""
            pr = f" {dish['pr']}" if dish.get("pr") else ""
            lines.append(f"- {event['id']}{tickets}: {event['note']}{pr}")
        lines.append("")
    questions = [row for row in restaurant.rows("86.tsv") if row["state"] == "open"]
    if questions:
        lines += ["## Decisions for you", ""]
        lines += [f"- {q['id']}: {q['question']} Options: {q['options']}. Default if no answer: {q['default']}." for q in questions]
        lines.append("")
    if len(lines) == 4:
        lines += ["Nothing new.", ""]
    text = "\n".join(lines)
    if write:
        write_atomic(restaurant.dir / "closeouts" / f"{stamp[:26].replace(':', '')}.md", text)
        meta["lastReportAt"] = stamp
        restaurant.save_meta(meta)
    return text


def walk(root, stale_hours=24):
    lines = []
    for meta_path in sorted(root.glob("*/*/restaurant.json")):
        restaurant = Restaurant(meta_path.parent)
        meta = restaurant.meta
        age = datetime.now(timezone.utc) - datetime.fromisoformat(meta["lastActivityAt"])
        idle = f", idle {int(age.total_seconds() // 3600)}h" if age.total_seconds() > stale_hours * 3600 else ""
        lines.append(f"{meta['restaurant']} ({meta['projectRoot']}){idle}: {status_line(restaurant)}")
        lines.append(f"  thread {meta.get('thread') or 'not recorded'}, store {restaurant.dir}")
        for question in (row for row in restaurant.rows("86.tsv") if row["state"] == "open"):
            lines.append(f"  {question['id']}: {question['question']}")
    return "\n".join(lines) or f"no restaurants under {root}"


def parser():
    top = argparse.ArgumentParser(prog="brigade.py", description=__doc__.splitlines()[0])
    top.add_argument("--store", help="store root (default $BRIGADE_STORE or $XDG_STATE_HOME/pstack-t3/brigade)")
    top.add_argument("--at", default=os.environ.get("BRIGADE_DIR"), help="restaurant directory (default $BRIGADE_DIR)")
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("open", help="create a restaurant, or print an existing one")
    p.add_argument("--project-root", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--merge-policy", choices=MERGE_POLICIES, default="pass")

    p = sub.add_parser("set", help="record the head chef thread or a schedule id")
    p.add_argument("--thread")
    p.add_argument("--schedule", action="append", default=[], metavar="NAME=ID")
    p.add_argument("--merge-policy", choices=MERGE_POLICIES)

    p = sub.add_parser("ticket", help="add, list, or update tickets on the rail")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("add")
    a.add_argument("--summary", required=True)
    a.add_argument("--source", default="user")
    a.add_argument("--ref", default="")
    a = t.add_parser("list")
    a.add_argument("--state", choices=TICKET_STATES)
    a = t.add_parser("set")
    a.add_argument("id")
    a.add_argument("--state", choices=TICKET_STATES, required=True)

    p = sub.add_parser("fire", help="group tickets into one dish and assign it to a station")
    p.add_argument("--tickets", required=True, help="comma-separated ticket ids")
    p.add_argument("--station", required=True, help="the pstack playbook the worker runs")
    p.add_argument("--summary", required=True)
    for field in ("task", "thread", "branch"):
        p.add_argument(f"--{field}", default="")

    p = sub.add_parser("dish", help="update a dish")
    p.add_argument("id")
    p.add_argument("--state", choices=DISH_STATES)
    for field in ("task", "thread", "branch", "pr", "sha"):
        p.add_argument(f"--{field}")

    p = sub.add_parser("pass", help="record or check a review verdict for a dish at a head SHA")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("record")
    a.add_argument("dish")
    a.add_argument("--pr", default="", help="PR URL; omit for local-only work")
    a.add_argument("--sha", required=True)
    a.add_argument("--verdict", choices=VERDICTS, required=True)
    a.add_argument("--author", required=True, help="provider/model of the worker")
    a.add_argument("--verifier", required=True, help="provider/model of the reviewer")
    a.add_argument("--note", default="")
    a.add_argument("--same-family", action="store_true", help="allow it when no other family is runnable")
    a = t.add_parser("check")
    a.add_argument("dish")
    a.add_argument("--sha", required=True)

    p = sub.add_parser("86", help="park or answer a decision that needs the user")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("add")
    a.add_argument("--question", required=True)
    a.add_argument("--options", required=True)
    a.add_argument("--default", required=True)
    a.add_argument("--dish", default="")
    a = t.add_parser("answer")
    a.add_argument("id")
    a.add_argument("--answer", required=True)
    t.add_parser("list")

    sub.add_parser("status", help="one line of counts")
    p = sub.add_parser("close", help="write the report of what changed since the last one")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("walk", help="every restaurant's counts and open decisions")
    p.add_argument("--stale-hours", type=float, default=24)
    return top


def run(argv):
    args = parser().parse_args(argv)
    root = store_root(args.store)
    if args.command == "open":
        restaurant, created = open_restaurant(root, args.project_root, args.name, args.merge_policy)
        return f"{'opened' if created else 'exists'} {restaurant.dir}"
    if args.command == "walk":
        return walk(root, args.stale_hours)
    if not args.at:
        raise BrigadeError("pass --at <restaurant dir> or set BRIGADE_DIR")
    restaurant = Restaurant(args.at)

    if args.command == "set":
        meta = restaurant.meta
        if args.thread:
            meta["thread"] = args.thread
        if args.merge_policy:
            meta["mergePolicy"] = args.merge_policy
        for pair in args.schedule:
            name, _, ident = pair.partition("=")
            if not ident:
                raise BrigadeError(f"--schedule takes NAME=ID, got {pair!r}")
            meta["schedules"][name] = ident
        restaurant.save_meta(meta)
        return json.dumps(meta, indent=2)

    if args.command == "ticket":
        if args.action == "add":
            ident = restaurant.next_id("rail.tsv")
            restaurant.append("rail.tsv", {"id": ident, "at": now(), "state": "waiting", "source": args.source,
                                           "ref": args.ref, "summary": args.summary})
            restaurant.log("ticket", ident, "waiting", args.summary)
            return ident
        if args.action == "list":
            rows = [row for row in restaurant.rows("rail.tsv") if not args.state or row["state"] == args.state]
            return "\n".join(f"{r['id']} {r['state']} [{r['source']}] {r['summary']}" + (f" {r['ref']}" if r["ref"] else "") for r in rows)
        restaurant.update("rail.tsv", args.id, "ticket", state=args.state)
        return f"{args.id} {args.state}"

    if args.command == "fire":
        ids = [ident.strip() for ident in args.tickets.split(",") if ident.strip()]
        for ident in ids:
            _, ticket = restaurant.find("rail.tsv", ident)
            if ticket["state"] != "waiting":
                raise BrigadeError(f"{ident} is {ticket['state']}, not waiting")
        dish = restaurant.next_id("dishes.tsv")
        restaurant.append("dishes.tsv", {"id": dish, "at": now(), "state": "in-progress", "station": args.station,
                                         "tickets": ",".join(ids), "task": args.task, "thread": args.thread,
                                         "branch": args.branch, "summary": args.summary})
        restaurant.log("dish", dish, "in-progress", args.summary)
        rows = restaurant.rows("rail.tsv")
        for row in rows:
            if row["id"] in ids:
                row["state"], row["dish"] = "assigned", dish
        restaurant.save_rows("rail.tsv", rows)
        for row in rows:
            if row["id"] in ids:
                restaurant.log("ticket", row["id"], "assigned", row["summary"])
        return dish

    if args.command == "dish":
        if args.state == "merged" and restaurant.meta["mergePolicy"] == "pass":
            _, current = restaurant.find("dishes.tsv", args.id)
            ok, why = pass_check(restaurant, args.id, args.sha or current["sha"])
            if not ok:
                raise BrigadeError(f"merge policy is pass and {why}")
        row = restaurant.update("dishes.tsv", args.id, "dish", state=args.state, task=args.task, thread=args.thread,
                                branch=args.branch, pr=args.pr, sha=args.sha)
        if args.state == "merged":
            for ticket in filter(None, row["tickets"].split(",")):
                restaurant.update("rail.tsv", ticket, "ticket", state="done")
        return f"{args.id} {row['state']}"

    if args.command == "pass":
        if args.action == "record":
            row = record_pass(restaurant, args.dish, args.pr, args.sha, args.verdict, args.author, args.verifier,
                              args.note, args.same_family)
            return f"{args.dish} {row['state']}"
        ok, why = pass_check(restaurant, args.dish, args.sha)
        if not ok:
            raise BrigadeError(why)
        return why

    if args.command == "86":
        if args.action == "add":
            ident = restaurant.next_id("86.tsv")
            restaurant.append("86.tsv", {"id": ident, "at": now(), "state": "open", "dish": args.dish,
                                         "question": args.question, "options": args.options, "default": args.default})
            restaurant.log("decision", ident, "open", args.question)
            return ident
        if args.action == "answer":
            restaurant.update("86.tsv", args.id, "decision", state="answered", answer=args.answer)
            return f"{args.id} answered"
        rows = [row for row in restaurant.rows("86.tsv") if row["state"] == "open"]
        return "\n".join(f"{q['id']}: {q['question']} Options: {q['options']}. Default: {q['default']}." for q in rows) or "no open decisions"

    if args.command == "status":
        return status_line(restaurant)
    if args.command == "close":
        return report(restaurant, write=not args.dry_run)
    raise BrigadeError(f"unknown command {args.command}")


def main(argv=None):
    try:
        print(run(sys.argv[1:] if argv is None else argv))
    except BrigadeError as error:
        print(f"brigade: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
