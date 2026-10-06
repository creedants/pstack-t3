#!/usr/bin/env python3
"""Bookkeeping for brigade restaurants.

A restaurant is one directory under the brigade store. Its tables hold current
state and log.tsv holds every change, so a report can say what is new since
the last one. The head chef thread is the only writer.

Kitchen words name files and commands. Output is plain engineering prose.
"""

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

TICKET_STATES = ("waiting", "assigned", "moved", "done", "dropped")
LIVE_TICKET_STATES = ("waiting", "assigned")
DISH_STATES = ("in-progress", "in-review", "passed", "sent-back", "blocked", "queued", "merged", "dropped")
VERDICTS = {"pass": "passed", "send-back": "sent-back", "blocked": "blocked"}
OPEN_RUN_MINUTES = 10

TABLES = {
    "rail.tsv": ("id", "at", "state", "source", "ref", "dish", "summary"),
    "dishes.tsv": ("id", "at", "state", "station", "tickets", "task", "thread", "branch", "pr", "sha", "summary", "timebox", "lease", "paths", "reported"),
    "pass.tsv": ("at", "dish", "pr", "sha", "verdict", "author", "verifier", "note"),
    "86.tsv": ("id", "at", "state", "dish", "question", "options", "default", "answer"),
    "log.tsv": ("at", "kind", "id", "state", "note"),
}
PREFIX = {"rail.tsv": "T", "dishes.tsv": "D", "86.tsv": "Q"}
# A report shows each ticket and dish once, under the latest state it reached since the last report.
SECTIONS = {
    ("dish", "merged"): "Merged",
    ("dish", "passed"): "Passed review, not submitted",
    ("dish", "queued"): "Waiting to land",
    ("dish", "sent-back"): "Sent back after review",
    ("dish", "blocked"): "Blocked",
    ("dish", "in-progress"): "In progress",
    ("dish", "in-review"): "In progress",
    ("ticket", "waiting"): "New tickets, not started",
    ("ticket", "moved"): "Handed to another coordinator",
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
2. Work lands only through the repository's landing queue (the landing skill). Workers never merge, rebase shared branches, or push trunk.
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
        try:
            handle.write(text)
        except BaseException:
            handle.close()
            os.unlink(handle.name)
            raise
    os.replace(handle.name, path)


def is_timestamp(value):
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return "T" in value


class Restaurant:
    def __init__(self, directory):
        self.dir = Path(directory)
        if not (self.dir / "restaurant.json").is_file():
            raise BrigadeError(f"{self.dir} is not a restaurant (no restaurant.json); run brigade.py open")
        self._lock_fd = None
        self._lock_depth = 0

    @contextmanager
    def locked(self):
        """Every write in this store holds an exclusive lock on restaurant.lock.

        The lock is on a sidecar file because rewrites replace a table's inode.
        It is reentrant within one Restaurant, so a command can hold it around
        its reads and writes while each write also takes it.
        """
        if self._lock_depth == 0:
            fd = os.open(self.dir / "restaurant.lock", os.O_RDWR | os.O_CREAT, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._lock_fd = fd
        self._lock_depth += 1
        try:
            yield
        finally:
            self._lock_depth -= 1
            if self._lock_depth == 0:
                os.close(self._lock_fd)
                self._lock_fd = None

    @contextmanager
    def checked(self):
        """One command's checks and writes, run against tables that all parse.

        Nothing inside may wait on stdin, a subprocess, or a sleep. That would stop every other command on this store.
        """
        with self.locked():
            for table in TABLES:
                self.rows(table)
            yield

    def write(self, relative, text):
        with self.locked():
            write_atomic(self.dir / relative, text)

    @property
    def meta(self):
        return json.loads((self.dir / "restaurant.json").read_text())

    def save_meta(self, meta):
        self.write("restaurant.json", json.dumps(meta, indent=2) + "\n")

    def rows(self, table):
        path = self.dir / table
        if not path.exists():
            return []
        header = TABLES[table]
        at = header.index("at")
        rows = []
        # The last element is the text after the final newline: empty, or the tail of a killed append.
        for number, line in enumerate(path.read_text().split("\n")[1:-1], start=2):
            fields = line.split("\t")
            if len(fields) != len(header) or not is_timestamp(fields[at]):
                raise BrigadeError(f"{table} line {number} is malformed; fix or remove it")
            rows.append(dict(zip(header, fields)))
        return rows

    def save_rows(self, table, rows):
        header = TABLES[table]
        body = ["\t".join(header)] + ["\t".join(clean(row.get(key, "")) for key in header) for row in rows]
        self.write(table, "\n".join(body) + "\n")

    def append(self, table, row):
        data = ("\t".join(clean(row.get(key, "")) for key in TABLES[table]) + "\n").encode()
        path = self.dir / table
        with self.locked():
            if not path.exists():
                self.save_rows(table, [])
            fd = os.open(path, os.O_RDWR | os.O_APPEND)
            try:
                end = os.fstat(fd).st_size
                if end and os.pread(fd, 1, end - 1) != b"\n":
                    # A killed append left an unfinished tail. Writing after it would join two rows.
                    end = os.pread(fd, end, 0).rfind(b"\n") + 1
                    os.ftruncate(fd, end)
                written = os.write(fd, data)
                if written != len(data):
                    os.ftruncate(fd, end)
                    raise BrigadeError(f"wrote {written} of {len(data)} bytes to {table}; nothing appended")
            finally:
                os.close(fd)

    def log(self, kind, ident, state, note=""):
        with self.locked():
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
        moved = "state" in changed and changed["state"] != row.get("state")
        row.update(changed)
        self.save_rows(table, rows)
        if moved:
            self.log(kind, ident, changed["state"], row.get("summary") or row.get("question", ""))
        return row


LANDING = ("human", "merge", "push", "local")
REPORTING = ("every-turn", "milestones", "digest")
CLAIM_WAIT_SECONDS = 1.0
PURPOSE_TEMPLATE = "What this restaurant exists to achieve"
OFF_MENU_TEMPLATE = "Work this restaurant does not take"


def reporting_of(meta):
    value = meta.get("reporting")
    if value in (None, ""):
        return "milestones"
    return value


def menu_section(text, heading):
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else ""


def siblings(directory, project_root):
    """The other coordinators in this project directory on the same project root, by directory name."""
    found = {}
    for meta_path in sorted(directory.parent.glob("*/restaurant.json")):
        if meta_path.parent == directory:
            continue
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("projectRoot") == project_root:
            found[meta_path.parent.name] = meta
    return found


def sibling_lines(restaurant):
    blocks = []
    for directory_name, meta in siblings(restaurant.dir, restaurant.meta.get("projectRoot")).items():
        meta_path = restaurant.dir.parent / directory_name / "restaurant.json"
        menu_path = meta_path.parent / "menu.md"
        menu = menu_path.read_text() if menu_path.is_file() else ""
        purpose = menu_section(menu, "Purpose")
        if not purpose or purpose.startswith(PURPOSE_TEMPLATE):
            purpose = "not written yet"
        else:
            purpose = " ".join(purpose.split())
        off = menu_section(menu, "Off the menu")
        if not off or off.startswith(OFF_MENU_TEMPLATE):
            off_line = "not written yet"
        else:
            off_line = "; ".join(line.strip() for line in off.splitlines() if line.strip())
        thread = (meta.get("thread") or "").strip() or "not recorded"
        name = meta.get("restaurant") or meta_path.parent.name
        blocks.append(
            f"sibling {name} ({meta_path.parent}), thread {thread}\n"
            f"  purpose: {purpose}\n"
            f"  off the menu: {off_line}")
    return blocks


def _read_meta(path):
    try:
        text = path.read_text()
    except OSError:
        return None, "missing"
    try:
        return json.loads(text), "ok"
    except json.JSONDecodeError:
        return None, "invalid"


def wait_for_meta(directory):
    path = directory / "restaurant.json"
    deadline = time.monotonic() + CLAIM_WAIT_SECONDS
    while True:
        meta, state = _read_meta(path)
        if state == "ok":
            return meta
        if time.monotonic() >= deadline:
            if state == "invalid":
                raise BrigadeError(f"{path} is not valid JSON")
            raise BrigadeError(f"{directory} has no restaurant.json after one second")
        time.sleep(0.01)


def intake_owner(directory, project_root, source):
    for name, meta in siblings(directory, project_root).items():
        if source in (meta.get("intake") or []):
            return name
    return None


def refuse_owned_intake(directory, project_root, sources):
    for source in sources:
        owner = intake_owner(directory, project_root, source)
        if owner:
            raise BrigadeError(f"{owner} already owns intake from {source}; move tickets to it instead")


def intake_list(text):
    sources = []
    for item in text.split(","):
        item = clean(item)
        if item and item not in sources:
            sources.append(item)
    return sources


def base_source(source):
    """A taken ticket's source is `<source> (from <handoff id>)`. The intake source is the part before."""
    return source.split(" (from ", 1)[0]


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


def taken_row(directory, handoff):
    """The ticket a take filed for this handoff in the coordinator at directory, if any."""
    if not (directory / "restaurant.json").is_file():
        return None
    marker = f"(from {handoff})"
    return next((row for row in Restaurant(directory).rows("rail.tsv") if row["source"].endswith(marker)), None)


def is_live(directory, row):
    """waiting and assigned are live. moved is as live as the ticket it became. A handoff not yet taken is live."""
    if row["state"] != "moved":
        return row["state"] in LIVE_TICKET_STATES
    target = directory.parent / row["dish"].removeprefix("to:")
    taken = taken_row(target, handoff_id(directory, row["id"]))
    return taken is None or is_live(target, taken)


def refuse_live_ref(restaurant, ref):
    if not ref:
        return
    stores = [(restaurant.dir, restaurant.rows("rail.tsv"))]
    for name in siblings(restaurant.dir, restaurant.meta.get("projectRoot")):
        directory = restaurant.dir.parent / name
        stores.append((directory, Restaurant(directory).rows("rail.tsv")))
    for directory, rows in stores:
        for row in rows:
            if row["ref"] == ref and is_live(directory, row):
                where = "" if directory == restaurant.dir else f" in {directory.name}"
                raise BrigadeError(f"{ref} is already {row['id']}{where} ({row['state']}); nothing added")


def add_ticket(restaurant, summary, source, ref):
    source, ref = clean(source), clean(ref)
    meta = restaurant.meta
    if source != "user":
        owner = intake_owner(restaurant.dir, meta.get("projectRoot"), source)
        if owner:
            raise BrigadeError(f"{owner} owns intake from {source}; ask it to file this and move it here")
        if source not in (meta.get("intake") or []):
            raise BrigadeError(f"no coordinator owns intake from {source}; the one that reads it runs set --intake {source}")
    refuse_live_ref(restaurant, ref)
    ident = restaurant.next_id("rail.tsv")
    restaurant.append("rail.tsv", {"id": ident, "at": now(), "state": "waiting", "source": source,
                                   "ref": ref, "summary": summary})
    restaurant.log("ticket", ident, "waiting", summary)
    return ident


def move_ticket(restaurant, ident, to):
    meta = restaurant.meta
    names = siblings(restaurant.dir, meta.get("projectRoot"))
    name = to if to in names else next((n for n in names if slug(names[n].get("restaurant") or n) == slug(to)), None)
    if name is None:
        raise BrigadeError(f"{to} is not a sibling coordinator on {meta.get('projectRoot')}")
    target = restaurant.dir.parent / name
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
    if taken_row(target, handoff) is None:
        write_atomic(inbox_file(target, handoff), json.dumps({
            "handoff": handoff, "summary": row["summary"], "source": base_source(row["source"]), "ref": row["ref"],
        }, indent=2) + "\n")
    thread = (names[name].get("thread") or "").strip()
    return f"{ident} moved to {name}; " + (f"tell thread {thread}" if thread else f"no thread recorded for {name}")


def take_tickets(restaurant):
    lines = []
    for path in sorted((restaurant.dir / "inbox").glob("*.json")):
        try:
            handoff = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            raise BrigadeError(f"{path} is not valid JSON: {error}") from error
        source = handoff["handoff"]
        row = taken_row(restaurant.dir, source)
        if row is None:
            rows = restaurant.rows("rail.tsv")
            row = {"id": restaurant.next_id("rail.tsv"), "at": now(), "state": "waiting",
                   "source": f"{handoff['source']} (from {source})", "ref": handoff["ref"], "summary": handoff["summary"]}
            restaurant.save_rows("rail.tsv", rows + [row])
        if not any(event["kind"] == "ticket" and event["note"] == f"from {source}" for event in restaurant.rows("log.tsv")):
            restaurant.log("ticket", row["id"], "waiting", f"from {source}")
        path.unlink()
        lines.append(f"{row['id']} from {source}: {row['summary']}")
    return "\n".join(lines) or "nothing handed to you"


def handed_count(restaurant):
    return len(list((restaurant.dir / "inbox").glob("*.json")))


def open_restaurant(root, project_root, name, landing, reporting="milestones", intake=()):
    project_root = Path(project_root).resolve()
    directory = root / slug(project_root.name) / slug(name)
    # The project directory is shared by every coordinator on this path slug.
    # The coordinator directory is the claim, so mkdir must fail when it exists.
    directory.parent.mkdir(parents=True, exist_ok=True)
    try:
        directory.mkdir()
    except FileExistsError:
        created = False
    else:
        created = True
    if not created:
        meta = wait_for_meta(directory)
        if meta.get("projectRoot") != str(project_root):
            other = meta.get("projectRoot")
            raise BrigadeError(f"{directory} already holds a coordinator for {other}; pick another --name")
        return Restaurant(directory), False
    try:
        refuse_owned_intake(directory, str(project_root), intake)
    except BrigadeError:
        directory.rmdir()
        raise
    for filename, template in (("menu.md", MENU), ("house-rules.md", HOUSE_RULES)):
        write_atomic(directory / filename, template.format(restaurant=name))
    for table in TABLES:
        header = TABLES[table]
        write_atomic(directory / table, "\t".join(header) + "\n")
    meta = {"restaurant": name, "projectRoot": str(project_root), "landing": landing,
            "reporting": reporting, "openedAt": now(), "lastActivityAt": now(),
            "lastReportAt": None, "thread": None, "schedules": {}, "intake": list(intake)}
    write_atomic(directory / "restaurant.json", json.dumps(meta, indent=2) + "\n")
    return Restaurant(directory), True


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
    path = None
    if write:
        path = restaurant.dir / "closeouts" / f"{stamp[:26].replace(':', '')}.md"
        restaurant.write(path.relative_to(restaurant.dir), text)
        meta["lastReportAt"] = stamp
        restaurant.save_meta(meta)
    return text, path


MEASURING_STATIONS = ("perf-issue", "hillclimb", "eval")
LAND = Path(__file__).resolve().parents[2] / "landing" / "scripts" / "land.py"


def holder(restaurant, dish):
    return f"{slug(restaurant.meta['restaurant'])}/{dish}"


def claim_lease(restaurant, dish, paths):
    """Claim the dish's paths in the repository's landing queue. A refused claim fires nothing."""
    result = subprocess.run([sys.executable, str(LAND), "--repo", restaurant.meta["projectRoot"], "lease", "claim",
                             "--holder", holder(restaurant, dish), "--paths", paths], capture_output=True, text=True)
    if result.returncode != 0:
        raise BrigadeError("nothing fired: " + result.stderr.strip().removeprefix("land: "))
    return result.stdout.strip()


def release_lease(restaurant, lease):
    subprocess.run([sys.executable, str(LAND), "--repo", restaurant.meta["projectRoot"], "lease", "release", lease],
                   capture_output=True, text=True)


def unfireable(restaurant, ids):
    for ident in ids:
        _, ticket = restaurant.find("rail.tsv", ident)
        if ticket["state"] != "waiting":
            return f"{ident} is {ticket['state']}, not waiting"
    return None


def fire(restaurant, ids, station, task, thread, branch, summary, timebox, paths):
    with restaurant.checked():
        refusal = unfireable(restaurant, ids)
        if refusal:
            raise BrigadeError(refusal)
        dish = restaurant.next_id("dishes.tsv")
    while True:
        # land.py can wait on the landing database, so the claim runs outside the store lock.
        # Another command may fire or take the tickets meanwhile, so the checks run again before the write.
        lease = claim_lease(restaurant, dish, paths) if paths else ""
        with restaurant.checked():
            refusal = unfireable(restaurant, ids)
            current = restaurant.next_id("dishes.tsv")
            if not refusal and current == dish:
                restaurant.append("dishes.tsv", {"id": dish, "at": now(), "state": "in-progress", "station": station,
                                                 "tickets": ",".join(ids), "task": task, "thread": thread,
                                                 "branch": branch, "summary": summary, "timebox": timebox,
                                                 "lease": lease, "paths": paths})
                restaurant.log("dish", dish, "in-progress", summary)
                rows = restaurant.rows("rail.tsv")
                for row in rows:
                    if row["id"] in ids:
                        row["state"], row["dish"] = "assigned", dish
                restaurant.save_rows("rail.tsv", rows)
                for row in rows:
                    if row["id"] in ids:
                        restaurant.log("ticket", row["id"], "assigned", row["summary"])
                return f"{dish} (lease {lease} held by {holder(restaurant, dish)})" if lease else dish
        if lease:
            release_lease(restaurant, lease)
        if refusal:
            raise BrigadeError(f"nothing fired: {refusal}")
        dish = current


def menu_purpose(restaurant):
    text = (restaurant.dir / "menu.md").read_text()
    match = re.search(r"^## Purpose\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    purpose = match.group(1).strip() if match else ""
    if not purpose or purpose.startswith("What this restaurant exists to achieve"):
        raise BrigadeError("menu.md has no purpose yet; write the Purpose section before briefing a worker")
    return purpose


def brief(restaurant, ident, goal, acceptance, verify, paths, lease, base, context):
    """The worker brief, assembled from the store so no field is left out or left as a placeholder."""
    _, dish = restaurant.find("dishes.tsv", ident)
    paths, lease = paths or dish.get("paths", ""), lease or dish.get("lease", "")
    if not paths or not lease:
        raise BrigadeError(f"{ident} has no lease; fire with --paths, or pass --paths and --lease")
    if not acceptance:
        raise BrigadeError("a brief needs at least one --acceptance criterion")
    if dish["state"] not in ("in-progress", "sent-back"):
        raise BrigadeError(f"{ident} is {dish['state']}; brief a dish that is in progress or sent back")
    thread = (restaurant.meta.get("thread") or "").strip()
    if not thread:
        raise BrigadeError("no coordinator thread recorded; run brigade.py set --thread")
    if not dish["branch"]:
        dish = restaurant.update("dishes.tsv", ident, "dish", branch=f"{slug(restaurant.meta['restaurant'])}/{ident.lower()}")
    tickets = {row["id"]: row for row in restaurant.rows("rail.tsv")}
    land = LAND
    report = restaurant.dir / "reports" / f"{ident}.md"
    findings = restaurant.dir / "reports" / f"{ident}-review.md"
    lines = [
        f"Use the poteto-mode skill and its `{dish['station']}` playbook.", "",
        f"GOAL: {goal}",
        f"PURPOSE: {menu_purpose(restaurant)}",
        f"TICKETS: " + "; ".join(f"{t}: {tickets[t]['summary']}" for t in dish["tickets"].split(",") if t in tickets), "",
        "SCOPE:",
        f"- You run in your own git worktree on branch `{dish['branch']}`, started from `{base}`. Work only there, and commit on that branch.",
        f"- Change only these paths, leased to you as {lease}: {paths}. A change outside them is refused when it lands. Report it instead of making it.",
        "- Keep history linear: no merge commits. Never merge, rebase a shared branch, push trunk, or open a PR. The landing queue does that.",
        "", "CONTEXT:", *([f"- {item}" for item in context] or ["- None beyond the tickets."]),
        *([f"- A reviewer sent an earlier attempt back. Its findings: {findings}"] if findings.exists() else []),
        "", "ACCEPTANCE:", *[f"- {item}" for item in acceptance],
        "", "VERIFY:", f"- {verify}",
        f"- Run builds and tests through `python3 {land} slot -- <command>`.",
        *([f"- Run every measurement through `python3 {land} slot --exclusive -- <command>`, so no other work shares the machine while it runs."]
          if dish["station"] in MEASURING_STATIONS else []),
        "", f"TIMEBOX: {dish.get('timebox') or 60} minutes. At the limit, write the report with what you have and stop.",
        "", "REPORT:",
        f"- Write it to {report}: status, branch, head SHA, what you ran and its output, before and after numbers with the method, deviations, follow-ups.",
        f"- After that file is written, call t3_thread_send to thread {thread} with mode \"auto\" and the one-line message \"{ident} done: report at {report}\".",
        "- Then end your turn with one line naming the report path.",
        "", "STANDING ORDERS:", (restaurant.dir / "house-rules.md").read_text().strip(),
    ]
    text = "\n".join(lines) + "\n"
    restaurant.write(Path("briefs") / f"{ident}.md", text)
    return text


def started_at(restaurant, ident):
    """When the current attempt started: the last in-progress log row.

    Replacing the worker appends that row again without leaving in-progress.
    """
    moves = [row["at"] for row in restaurant.rows("log.tsv") if row["kind"] == "dish" and row["id"] == ident and row["state"] == "in-progress"]
    return datetime.fromisoformat(moves[-1]) if moves else None


def watch(restaurant):
    lines = []
    moment = datetime.now(timezone.utc)
    for dish in restaurant.rows("dishes.tsv"):
        if dish["state"] != "in-progress":
            continue
        start = started_at(restaurant, dish["id"]) or datetime.fromisoformat(dish["at"])
        minutes = int((moment - start).total_seconds() // 60)
        timebox = int(dish.get("timebox") or 60)
        report = restaurant.dir / "reports" / f"{dish['id']}.md"
        where = f"thread {dish['thread']}" if dish["thread"] else (f"task {dish['task']}" if dish["task"] else "no worker recorded")
        written = datetime.fromtimestamp(report.stat().st_mtime, timezone.utc) if report.exists() else None
        if written and written >= start:
            if dish.get("reported") == "yes":
                ago = int((moment - written).total_seconds() // 60)
                if ago > OPEN_RUN_MINUTES:
                    lines.append(f"{dish['id']}: reported, run still open {ago}m ({where})")
                else:
                    lines.append(f"{dish['id']}: report written {ago}m ago; review it even if the worker's run is still open ({where})")
            else:
                lines.append(f"{dish['id']}: report written, no report-back ({where})")
        elif minutes > timebox:
            lines.append(f"{dish['id']}: over its {timebox}m timebox at {minutes}m with no report; read its thread and decide ({where})")
        else:
            lines.append(f"{dish['id']}: running {minutes}m of {timebox}m ({where})")
    for ticket in restaurant.rows("rail.tsv"):
        if ticket["state"] != "moved":
            continue
        name = ticket["dish"].removeprefix("to:")
        target = restaurant.dir.parent / name
        handoff = handoff_id(restaurant.dir, ticket["id"])
        # take writes the ticket before it deletes the file, so a missing file with no ticket was never delivered.
        if inbox_file(target, handoff).exists():
            if taken_row(target, handoff) is None:
                lines.append(f"{ticket['id']}: moved to {name}, waiting for ticket take")
        elif taken_row(target, handoff) is None:
            lines.append(f"{ticket['id']}: moved to {name}, not delivered; run ticket move {ticket['id']} --to {name} again")
    handed = handed_count(restaurant)
    if handed:
        lines.append(f"handed to you: {handed}; run ticket take")
    return "\n".join(lines) or "no work in progress"


def record_hang(restaurant, ident, provider, minutes):
    if not clean(provider):
        raise BrigadeError("hang needs a provider")
    if minutes < 0:
        raise BrigadeError("minutes must be 0 or more")
    _, dish = restaurant.find("dishes.tsv", ident)
    if dish.get("reported") != "yes":
        raise BrigadeError("no report-back on this attempt")
    start = started_at(restaurant, ident) or datetime.fromisoformat(dish["at"])
    for row in restaurant.rows("log.tsv"):
        if row["kind"] == "hang" and row["id"] == ident and datetime.fromisoformat(row["at"]) >= start:
            return f"{ident}: hang already recorded"
    restaurant.log("hang", ident, "open", f"{provider} {minutes}m")
    return f"{ident}: {provider} open {minutes}m"


def walk(root, stale_hours=24):
    lines = []
    for meta_path in sorted(root.glob("*/*/restaurant.json")):
        restaurant = Restaurant(meta_path.parent)
        meta = restaurant.meta
        age = datetime.now(timezone.utc) - datetime.fromisoformat(meta["lastActivityAt"])
        idle = f", idle {int(age.total_seconds() // 3600)}h" if age.total_seconds() > stale_hours * 3600 else ""
        landing = f", lands by {meta['landing']}" if meta.get("landing") else ""
        lines.append(f"{meta['restaurant']} ({meta['projectRoot']}{landing}, reports {reporting_of(meta)}){idle}: {status_line(restaurant)}")
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
    p.add_argument("--landing", choices=LANDING, required=True,
                   help="who lands work: human (PRs you merge), merge (PRs the queue merges), push (no PRs), local (a lane ref)")
    p.add_argument("--reporting", choices=REPORTING, default="milestones",
                   help="how often the coordinator replies (default: milestones)")
    p.add_argument("--intake", default="", help="comma-separated intake sources this coordinator owns, such as github")

    p = sub.add_parser("set", help="record the head chef thread, a schedule id, or the reporting level")
    p.add_argument("--thread")
    p.add_argument("--replace", action="store_true", help="replace a recorded coordinator thread")
    p.add_argument("--schedule", action="append", default=[], metavar="NAME=ID")
    p.add_argument("--landing", choices=LANDING, help="record a landing mode changed with land.py mode")
    p.add_argument("--reporting", choices=REPORTING, help="how often the coordinator replies")
    p.add_argument("--intake", help="comma-separated intake sources this coordinator owns; replaces the list, and \"\" clears it")

    p = sub.add_parser("ticket", help="add, list, update, move, or take tickets on the rail")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("add")
    a.add_argument("--summary", required=True)
    a.add_argument("--source", default="user")
    a.add_argument("--ref", default="")
    a = t.add_parser("list")
    a.add_argument("--state", choices=TICKET_STATES)
    a = t.add_parser("set")
    a.add_argument("id")
    a.add_argument("--state", choices=[state for state in TICKET_STATES if state != "moved"], required=True)
    a = t.add_parser("move", help="hand a waiting ticket to a sibling coordinator through its inbox")
    a.add_argument("id")
    a.add_argument("--to", required=True, help="the sibling's directory name")
    t.add_parser("take", help="file every ticket a sibling handed to this coordinator")

    p = sub.add_parser("fire", help="group tickets into one dish and assign it to a station")
    p.add_argument("--tickets", required=True, help="comma-separated ticket ids")
    p.add_argument("--station", required=True, help="the pstack playbook the worker runs")
    p.add_argument("--summary", required=True)
    for field in ("task", "thread", "branch"):
        p.add_argument(f"--{field}", default="")
    p.add_argument("--timebox", type=int, default=60, help="minutes before the liveness check flags the dish")
    p.add_argument("--paths", default="", help="paths the dish will change; fire claims a landing lease on them first")

    p = sub.add_parser("brief", help="render the worker brief for a dish; refuses when a field or the coordinator thread is missing")
    p.add_argument("id")
    p.add_argument("--fields", help="JSON file of brief fields, or - for stdin")
    p.add_argument("--goal", default=None, help="one sentence: the outcome")
    p.add_argument("--acceptance", action="append", default=[], help="a checkable criterion; repeatable, at least one")
    p.add_argument("--verify", default=None, help="exact commands that prove it, plus known gotchas")
    p.add_argument("--paths", default=None, help="leased paths, when fire did not claim them")
    p.add_argument("--lease", default=None, help="the landing lease id, when fire did not claim it")
    p.add_argument("--base", default=None, help="the trunk ref the worker's branch starts from, such as origin/main")
    p.add_argument("--context", action="append", default=[], help="a pointer to files, PRs, or upstream reports; repeatable")

    sub.add_parser("watch", help="liveness: which dishes have reports, are running, or are over their timebox")

    p = sub.add_parser("hang", help="record one open run after report-back, once per attempt")
    p.add_argument("id")
    p.add_argument("--provider", required=True)
    p.add_argument("--minutes", type=int, required=True)

    p = sub.add_parser("dish", help="update a dish")
    p.add_argument("id")
    p.add_argument("--state", choices=DISH_STATES)
    for field in ("task", "thread", "branch", "pr", "sha"):
        p.add_argument(f"--{field}")
    p.add_argument("--timebox", type=int, help="minutes; raise it once for a worker that is still making progress")
    p.add_argument("--reported", action="store_true", help="record that this attempt's report-back arrived")

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
    output = p.add_mutually_exclusive_group()
    output.add_argument("--dry-run", action="store_true")
    output.add_argument("--to-file", action="store_true",
                        help="print only the path of the written report, not its text")
    p = sub.add_parser("walk", help="every restaurant's counts and open decisions")
    p.add_argument("--stale-hours", type=float, default=24)
    return top


def _brief_fields(args):
    flagged = any(getattr(args, name) is not None for name in ("goal", "verify", "base", "paths", "lease")) or args.acceptance or args.context
    if args.fields is not None:
        if flagged:
            raise BrigadeError("use either --fields or the field flags, not both")
        if args.fields == "-":
            if sys.stdin.isatty():
                raise BrigadeError("brief fields: --fields - reads JSON from stdin")
            raw = sys.stdin.read()
        else:
            try:
                raw = Path(args.fields).read_text(encoding="utf-8")
            except OSError as error:
                raise BrigadeError(f"brief fields: {error}") from error
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise BrigadeError(f"brief fields: {error}") from error
        if not isinstance(payload, dict):
            raise BrigadeError("brief fields: JSON must be an object")
        allowed = {"goal", "acceptance", "verify", "base", "context", "paths", "lease"}
        lists = {"acceptance", "context"}
        unknown = [key for key in payload if key not in allowed]
        if unknown:
            raise BrigadeError(f"brief fields: unknown key {unknown[0]}")
        for key in ("goal", "acceptance", "verify", "base"):
            if key not in payload:
                raise BrigadeError(f"brief fields: missing {key}")
        for key, value in payload.items():
            if value is None:
                raise BrigadeError(f"brief fields: {key} is null")
            if key in lists:
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    raise BrigadeError(f"brief fields: {key} must be a list of strings")
            elif not isinstance(value, str):
                raise BrigadeError(f"brief fields: {key} must be a string")
        return {
            "goal": payload["goal"],
            "acceptance": payload["acceptance"],
            "verify": payload["verify"],
            "paths": payload["paths"] if "paths" in payload else "",
            "lease": payload["lease"] if "lease" in payload else "",
            "base": payload["base"],
            "context": payload["context"] if "context" in payload else [],
        }
    if args.goal is None or args.verify is None or args.base is None:
        raise BrigadeError("brief needs --fields, or --goal, --verify, and --base")
    return {
        "goal": args.goal,
        "acceptance": args.acceptance,
        "verify": args.verify,
        "paths": args.paths or "",
        "lease": args.lease or "",
        "base": args.base,
        "context": args.context,
    }


def run(argv):
    args = parser().parse_args(argv)
    root = store_root(args.store)
    if args.command == "open":
        restaurant, created = open_restaurant(root, args.project_root, args.name, args.landing, args.reporting,
                                              intake_list(args.intake))
        lines = [f"{'opened' if created else 'exists'} {restaurant.dir}"]
        meta = restaurant.meta
        thread = (meta.get("thread") or "").strip()
        if not created and thread:
            lines.append(f"thread {thread} already recorded")
        intake = meta.get("intake") or []
        if not created and args.intake and intake_list(args.intake) != intake:
            lines.append(f"intake stays {', '.join(intake) or 'empty'}; change it with set --intake")
        lines.extend(sibling_lines(restaurant))
        return "\n".join(lines)
    if args.command == "walk":
        return walk(root, args.stale_hours)
    if not args.at:
        raise BrigadeError("pass --at <restaurant dir> or set BRIGADE_DIR")
    restaurant = Restaurant(args.at)
    if args.command == "brief":
        # A stalled stdin or file must not hold the store lock, so the fields are read and checked first.
        fields = _brief_fields(args)
        with restaurant.checked():
            return brief(restaurant, args.id, **fields)
    if args.command == "fire":
        ids = [ident.strip() for ident in args.tickets.split(",") if ident.strip()]
        return fire(restaurant, ids, args.station, args.task, args.thread, args.branch, args.summary, args.timebox,
                    args.paths)
    with restaurant.checked():
        return command(restaurant, args)


def command(restaurant, args):
    if args.command == "set":
        meta = restaurant.meta
        if args.intake is not None:
            meta = set_intake(restaurant, intake_list(args.intake))
        if args.thread:
            current = (meta.get("thread") or "").strip()
            if current and args.thread != current and not args.replace:
                raise BrigadeError(f"thread {current} already recorded")
            meta["thread"] = args.thread
        if args.landing:
            meta["landing"] = args.landing
        if args.reporting:
            meta["reporting"] = args.reporting
        for pair in args.schedule:
            if "=" not in pair:
                raise BrigadeError(f"--schedule takes NAME=ID, got {pair!r}")
            name, ident = pair.split("=", 1)
            if ident:
                meta["schedules"][name] = ident
            else:
                meta["schedules"].pop(name, None)
        restaurant.save_meta(meta)
        return json.dumps(meta, indent=2)

    if args.command == "ticket":
        if args.action == "add":
            return add_ticket(restaurant, args.summary, args.source, args.ref)
        if args.action == "move":
            return move_ticket(restaurant, args.id, args.to)
        if args.action == "take":
            return take_tickets(restaurant)
        if args.action == "list":
            rows = [row for row in restaurant.rows("rail.tsv") if not args.state or row["state"] == args.state]
            return "\n".join(f"{r['id']} {r['state']} [{r['source']}] {r['summary']}" + (f" {r['ref']}" if r["ref"] else "") for r in rows)
        _, ticket = restaurant.find("rail.tsv", args.id)
        if ticket["state"] == "moved":
            raise BrigadeError(f"{args.id} moved to {ticket['dish'].removeprefix('to:')}; it is that coordinator's ticket now")
        restaurant.update("rail.tsv", args.id, "ticket", state=args.state)
        return f"{args.id} {args.state}"

    if args.command == "dish":
        _, current = restaurant.find("dishes.tsv", args.id)
        if args.state in ("queued", "merged"):
            ok, why = pass_check(restaurant, args.id, args.sha or current["sha"])
            if not ok:
                raise BrigadeError(f"only reviewed work lands: {why}")
        entering = args.state == "in-progress" and current["state"] != "in-progress"
        replacing = (bool(args.thread) and args.thread != current.get("thread", "")
                     and current["state"] == "in-progress" and args.state in (None, "in-progress"))
        reported = "" if entering or replacing else ("yes" if args.reported else None)
        row = restaurant.update("dishes.tsv", args.id, "dish", state=args.state, task=args.task, thread=args.thread,
                                branch=args.branch, pr=args.pr, sha=args.sha, timebox=args.timebox, reported=reported)
        if replacing:
            restaurant.log("dish", args.id, "in-progress", row.get("summary", ""))
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

    if args.command == "watch":
        return watch(restaurant)
    if args.command == "hang":
        return record_hang(restaurant, args.id, args.provider, args.minutes)
    if args.command == "status":
        level = f"reporting: {reporting_of(restaurant.meta)}"
        counts_text = status_line(restaurant)
        if counts_text == "nothing on record":
            return level
        return f"{level}, {counts_text}"
    if args.command == "close":
        text, path = report(restaurant, write=not args.dry_run)
        if args.to_file:
            return str(path.resolve())
        return text
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
