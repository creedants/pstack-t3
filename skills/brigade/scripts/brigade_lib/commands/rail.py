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
from brigade_lib._notes import note_lines
from brigade_lib.commands.once import OncePaths
from brigade_lib.leases import holding_blocks
from brigade_lib.reports import item_report
from brigade_lib.shared import BrigadeError
from brigade_lib.store import GOAL_STATES, KINDS, NOTHING_HANDED, PRIORITIES_BY_RANK, TICKET_STATES
from brigade_lib.tickets import EVERY_ROW, GOALS_ONLY, add_goal, add_ticket, file_follow_ups, finish_request, follow_ups, frozen_label, in_flight_tickets, count_labels, move_ticket, open_goal, read_labels, record_paths, set_goal, sibling_rails, take_inbox, take_tickets, ticket_paths, tracked_paths
from brigade_lib.reporting import goal_lines
from brigade_lib.wake import ticket_lines

COMMANDS = ("ticket", "goal", "inbox")


def register_ticket(sub):
    p = sub.add_parser("ticket", help="add, list, update, move, take, or record paths on tickets on the rail")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("add")
    how = a.add_mutually_exclusive_group(required=True)
    how.add_argument("--summary")
    how.add_argument("--from-report", metavar="FILE",
                     help="an item report under reports/, such as reports/D2.md; files at most one waiting ticket per "
                          "item, and only when no ticket from that report exists yet, for the first follow-up marked "
                          "Safety:, Privacy:, or Correctness: whose text is not on the rail; case does not count; any "
                          "other marked follow-up whose text is not on the rail prints not filed and goes to notes.tsv; "
                          "every other follow-up whose text is not on the rail goes to notes.tsv keyed by the tracked "
                          "paths it quotes; changes the ticket table, the log, and lastActivityAt when it files a ticket, "
                          "and notes.tsv when it notes one; each ticket takes the goal of the report's item, and when that "
                          "item has a kind the ticket takes fix at priority urgent or normal and polish at low; a "
                          "follow-up is filed with no --priority, so it is low")
    a.add_argument("--dry-run", action="store_true", help="with --from-report: print what it would add or note, and what is already noted; it changes no store file")
    a.add_argument("--source", default="user")
    a.add_argument("--ref", default="")
    a.add_argument("--request", default="", help="the admin request id this ticket carries out; refuses a second ticket for it")
    a.add_argument("--again", action="store_true",
                   help="with --source user and no --ref: add the ticket even when a waiting or assigned ticket of this store "
                        "has the same summary. Case, leading and trailing whitespace, and the length of a whitespace run "
                        "do not count")
    a.add_argument("--priority", choices=PRIORITIES_BY_RANK,
                   help="missing reads as urgent from upstream, low from report, and normal from every other source")
    a.add_argument("--paths", default="", action=OncePaths, help="comma-separated files and directories the work will touch; given once")
    a.add_argument("--decision", action="store_true",
                   help="the ticket asks the owner to decide; the startable command never lists it as startable")
    a.add_argument("--kind", choices=KINDS, help="the kind of work; a ticket added with --summary is refused without it")
    a.add_argument("--goal", metavar="G<n>",
                   help="with --summary: the goal this ticket belongs to, which must be open or paused; without it the "
                        "ticket is unassigned")
    a = t.add_parser("list")
    a.add_argument("--state", choices=TICKET_STATES)
    a = t.add_parser("set")
    a.add_argument("id")
    a.add_argument("--state", choices=[state for state in TICKET_STATES if state != "moved"])
    a.add_argument("--priority", choices=PRIORITIES_BY_RANK)
    a.add_argument("--paths", action=OncePaths, help='replaces the recorded paths; "" clears them; given once')
    a.add_argument("--decision", action=argparse.BooleanOptionalAction, default=None,
                   help="mark or unmark the ticket as one that asks the owner to decide")
    a.add_argument("--kind", choices=KINDS, help="replaces the kind of a waiting ticket; refused on a ticket in another state, "
                        "and on a waiting ticket that an item in flight names")
    goal = a.add_mutually_exclusive_group()
    goal.add_argument("--goal", metavar="G<n>",
                      help="replaces the goal of a waiting ticket with one that is open or paused; refused on a ticket in "
                           "another state, and on a waiting ticket that an item in flight names")
    goal.add_argument("--no-goal", action="store_true",
                      help="clears the goal of a waiting ticket, so it is unassigned; refused on a ticket in another state, "
                           "and on a waiting ticket that an item in flight names")
    a = t.add_parser("move", help="hand a waiting ticket to a sibling coordinator through its inbox")
    a.add_argument("id")
    a.add_argument("--to", required=True, help="the sibling's directory name")
    t.add_parser("take", help="file every ticket a sibling handed to this coordinator")
    a = t.add_parser("paths", help="record the tracked paths each waiting ticket's summary quotes, on tickets that record none; "
                                    "a quote that is no tracked path and that, with one trailing :<line> or :<line>-<line> "
                                    "dropped, holds no slash, no whitespace, and none of * ? [ and is no tracked path records "
                                    "the tracked file of that name when exactly one has that name, and a file under skills/ "
                                    "is not counted when a file of the same name is tracked under t3/")
    a.add_argument("--dry-run", action="store_true", help="print what it would record; it changes no table")
    a = t.add_parser("notes", help="list the follow-ups the store noted instead of filing as tickets")
    picked = a.add_mutually_exclusive_group(required=True)
    picked.add_argument("--paths", help="comma-separated files and directories; prints the notes that name them")
    picked.add_argument("--all", action="store_true", help="prints every note")


def register_goal(sub):
    p = sub.add_parser("goal", help="add, change, or list goals; a goal is a named line of work")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("add", help="record a new open goal and print its id; refused when an open or paused goal has the same "
                                 "name, where case, leading and trailing whitespace, and the length of a whitespace run "
                                 "do not count")
    a.add_argument("--name", required=True, help='what the goal is called, such as "activity view"')
    a.add_argument("--purpose", required=True, help="one sentence that says why this line of work exists")
    a.add_argument("--done", required=True, help="one sentence that states the test that shows the goal is reached")
    a.add_argument("--share", default="",
                   help="a whole number of 1 or more, recorded as the goal's share of the worker slots; goal list prints it, "
                        "and the refill holds a ticket of the goal back once that many slots hold the goal's items")
    a = t.add_parser("set", help="change a goal's state, name, purpose, done test, or share")
    a.add_argument("id")
    a.add_argument("--state", choices=GOAL_STATES,
                   help="paused starts no ticket of the goal, whatever its priority or the auto-start level. The goal's items in flight finish "
                        "and land. "
                        "A move to done or to dropped needs --evidence, and is refused while a ticket of the goal is "
                        "waiting, an item of the goal is in progress, in review, passed, sent back, blocked, or waiting "
                        "to land, or an assigned ticket of the goal is not in an item of the goal. "
                        "A done or dropped goal moves only to open.")
    a.add_argument("--evidence",
                   help="one line, stored with the time in the goal's closed cell when the goal moves to done or dropped; "
                        "with --state done, what shows the done test is met; with --state dropped, why the goal is "
                        "dropped; refused without --state done or --state dropped")
    a.add_argument("--name", help="replaces the goal's name; refused when it is empty or only whitespace")
    a.add_argument("--purpose", help="replaces the goal's purpose; refused when it is empty or only whitespace")
    a.add_argument("--done", help="replaces the goal's done test; refused when it is empty or only whitespace")
    a.add_argument("--share",
                   help='a whole number of 1 or more, recorded as the goal\'s share of the worker slots; "" clears it; '
                        "goal list prints it, and the refill holds a ticket of the goal back once that many slots "
                        "hold the goal's items")
    a = t.add_parser("list",
                     help="the state, name, counts, purpose, and done test of each open or paused goal and of each done or "
                          "dropped goal that has a waiting ticket, an item in flight, or a ticket riding along, then the "
                          "counts of unassigned work; waiting counts waiting tickets; in flight counts items in progress, "
                          "in review, passed, sent back, blocked, or waiting to land; merged counts merged items; "
                          "send-backs counts the send-back verdicts on the goal's items, in any state, that are not a "
                          "panel member's row; riding along counts assigned tickets of the goal that are not in an item "
                          "of the goal")
    a.add_argument("--all", action="store_true", help="also print each other done or dropped goal")


def register_inbox(sub):
    p = sub.add_parser("inbox", help="take handed tickets and the admin's requests, or finish a request")
    t = p.add_subparsers(dest="action", required=True)
    t.add_parser("take", help="file handed tickets and print each request file")
    a = t.add_parser("done", help="record a request as acted on and delete its file")
    a.add_argument("id")


def register(sub):
    register_ticket(sub)
    register_goal(sub)
    register_inbox(sub)


def handle_ticket(restaurant, args):
    if args.action == "list":
        held = holding_blocks(restaurant)
        with restaurant.checked():
            return ticket_lines(restaurant, args.state, held)
    if args.action == "add" and args.from_report is not None:
        if args.source != "user" or args.ref or args.request or args.again:
            raise BrigadeError("--from-report takes no --source, --ref, --request, or --again")
        if args.priority or args.paths or args.decision:
            raise BrigadeError("--from-report takes no --priority, nonempty --paths, or --decision")
        if args.kind or args.goal is not None:
            raise BrigadeError("--from-report takes no --kind or --goal; each ticket's kind and goal follow from the report's item")
        name = item_report(restaurant, args.from_report)
        try:
            found = follow_ups((restaurant.dir / "reports" / name).read_text(encoding="utf-8"))
        except UnicodeDecodeError as error:
            raise BrigadeError(f"reports/{name} is not UTF-8 text; nothing added") from error
        tracked = tracked_paths(restaurant.meta["projectRoot"])
        with restaurant.checked():
            return file_follow_ups(restaurant, name, found, not args.dry_run, tracked)
    if args.action == "paths":
        project = restaurant.meta["projectRoot"]
        tracked = tracked_paths(project)
        if tracked is None:
            raise BrigadeError(f"cannot list tracked files in {project}")
        with restaurant.checked():
            return record_paths(restaurant, tracked, not args.dry_run)
    if args.action == "add" and args.dry_run:
        raise BrigadeError("--dry-run needs --from-report")
    if args.action in ("add", "set") and args.paths is not None:
        args.paths = ticket_paths(args.paths)
    rails = None
    if args.action == "move" or args.action == "add" and args.ref:
        rails = sibling_rails(restaurant)
    with restaurant.checked():
        return seam.command(restaurant, args, rails=rails)


def handle(restaurant, args):
    if args.command == "ticket":
        return handle_ticket(restaurant, args)
    if args.command in ("goal", "inbox"):
        with restaurant.checked():
            return seam.command(restaurant, args)
    return None


def handle_locked(restaurant, args, view):
    if args.command == "inbox":
        if args.action == "take":
            return take_inbox(restaurant)
        return finish_request(restaurant, args.id)
    if args.command == "goal":
        if args.action == "add":
            return add_goal(restaurant, args.name, args.purpose, args.done, args.share)
        if args.action == "set":
            return set_goal(restaurant, args.id, args.name, args.purpose, args.done, args.share, args.state, args.evidence)
        labels = read_labels(restaurant, EVERY_ROW)
        return goal_lines(labels, count_labels(restaurant, labels), args.all)
    if args.command == "ticket":
        if args.action == "notes":
            if args.all:
                with restaurant.checked():
                    return note_lines(restaurant, (), True)
            wanted = tuple(part.strip() for part in (args.paths or "").split(","))
            wanted = tuple(part for part in wanted if part)
            with restaurant.checked():
                return note_lines(restaurant, wanted, False)
        rails = view.rails if view.rails is not None else {}
        if args.action == "add":
            return add_ticket(restaurant, args.summary, args.source, args.ref, args.request, rails, again=args.again,
                              priority=args.priority or "", paths=args.paths, decision=args.decision,
                              kind=args.kind or "", goal=args.goal)
        if args.action == "move":
            return move_ticket(restaurant, args.id, args.to, rails)
        if args.action == "take":
            return "\n".join(take_tickets(restaurant)) or NOTHING_HANDED
        _, ticket = restaurant.find("rail.tsv", args.id)
        if ticket["state"] == "moved":
            raise BrigadeError(f"{args.id} moved to {ticket['dish'].removeprefix('to:')}; it is that coordinator's ticket now")
        held = in_flight_tickets(restaurant)
        frozen = frozen_label(ticket, held)
        if (args.kind or args.goal is not None or args.no_goal) and frozen:
            if ticket["state"] != "waiting":
                kept = (f"; {ticket['dish']} keeps the kind and goal it took when it started"
                        if ticket["state"] == "assigned" and ticket["dish"] else "")
                raise BrigadeError(f"{frozen}; --kind, --goal, and --no-goal change a waiting ticket only{kept}")
            item = held[args.id]["id"]
            raise BrigadeError(f"{frozen}; --kind, --goal, and --no-goal change a waiting ticket that no item in flight names; "
                               f"{item} keeps the kind and goal it took when it started; drop {item}, and every other item in flight that names {args.id}, first, "
                               f"or leave {args.id} as it is")
        changes, said = {}, []
        if args.state:
            changes["state"] = args.state
            said.append(args.state)
        if args.priority:
            changes["priority"] = args.priority
            said.append(f"priority {args.priority}")
        if args.paths is not None:
            changes["paths"] = ",".join(args.paths)
            said.append(f"paths {changes['paths']}" if args.paths else "no paths")
        if args.decision is not None:
            changes["decision"] = "yes" if args.decision else ""
            said.append("decision" if args.decision else "no decision")
        if args.kind:
            changes["kind"] = args.kind
            said.append(f"kind {args.kind}")
        if args.goal is not None:
            changes["goal"] = open_goal(read_labels(restaurant, GOALS_ONLY), args.goal).id
            said.append(f"goal {args.goal}")
        if args.no_goal:
            changes["goal"] = ""
            said.append("unassigned")
        if not said:
            raise BrigadeError("ticket set needs --state, --priority, --paths, --decision, --no-decision, --kind, --goal, or --no-goal")
        restaurant.update("rail.tsv", args.id, "ticket", **changes)
        return f"{args.id} {'; '.join(said)}"
    return None
