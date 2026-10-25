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
from brigade_lib.credit import credit_text
from brigade_lib.items import announce_archive
from brigade_lib.reviews import latest_verdict, pass_check, pass_hits, pass_json, pass_stats, reason_refusal, record_pass, round_decision, verify_edits, write_threads
from brigade_lib.shared import BrigadeError, clean
from brigade_lib.store import EDITS_VERDICT, REASONS, ROUND_KIND, THREAD_COMMAND_ROLES, VERDICTS
from brigade_lib.wake import closing_options, open_item_decisions, option_key

COMMANDS = ("thread", "pass", "edits", "86", "credit")


def non_negative_int(text):
    """An argparse type that returns a non-negative integer and refuses anything else."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid non-negative integer value: {text!r}")
    if value < 0:
        raise argparse.ArgumentTypeError(f"{value} is negative")
    return value


def register_thread(sub):
    p = sub.add_parser("thread", help="record one thread an item used, for a role no other command writes")
    p.add_argument("dish")
    p.add_argument("--role", required=True, choices=THREAD_COMMAND_ROLES,
                   help="the row's role; the other six roles are written by dish --thread, --reviewer-thread, "
                        "or a report")
    p.add_argument("--thread", required=True, help="the thread id")
    p.add_argument("--seat", required=True, help="the thread's seat as provider/model")


def register_pass(sub):
    p = sub.add_parser("pass", help="record or check a review verdict for a dish at a head SHA")
    t = p.add_subparsers(dest="action", required=True)
    threads_help = (
        "Records the verdict, and writes one row of threads.tsv for the reviewer and for each child the worker's "
        "report lists. The reviewer's row comes from --reviewer-thread and takes its seat from --verifier. The "
        "child rows come from a ## Threads section of reports/<dish>.md, which this command reads and takes no "
        "flag for.\n"
        "\n"
        "That section starts with the heading `## Threads` on a line of its own. A line of it that opens a fence "
        "after up to three spaces is not read, and neither is a line until that fence closes. Every other "
        "non-blank line of it is skipped when it is plain prose, that is when it starts, after any leading "
        "whitespace, with neither a list marker nor a `#`, and holds no backticked thread id. A list marker is a "
        "hyphen, an asterisk, a plus, a bullet, or a number followed by a dot or a closing parenthesis.\n"
        "\n"
        "This example uses made-up ids. A report whose worker used two children holds the heading `## Threads` and "
        "then two items, one for a code delegate whose thread id is `mcp:00000000-0000-4000-8000-000000000001` and "
        "whose seat is `opencode-go/step-5-preview-free`, and one for a claims check whose thread id is "
        "`mcp:00000000-0000-4000-8000-000000000002` and whose seat is `codex/gpt-6.1-sol`.\n"
        "\n"
        "The three roles a child may take are code delegate, claims check, and architect runner. A child whose "
        "role is any of the other four is refused, because dish --thread writes the worker rows, "
        "--reviewer-thread writes the reviewer row, and the thread command writes the panel member row. A "
        "report with no ## Threads section adds no row and is not an error. A non-blank line that is read, is not "
        "prose and is not of that form is refused by its number. An item whose thread id the table would change is "
        "refused by "
        "its number too. "
        "So is a heading deeper than the section's own, and so is a second ## Threads "
        "heading while the first is still open, and those two end the scan. A role that is well formed but is not "
        "one of the three is refused by its number and named.\n"
        "\n"
        "Any refusal happens before the pass row is written, so nothing is recorded and no partial row is written."
    )
    a = t.add_parser("record", help=threads_help)
    a.add_argument("dish")
    a.add_argument("--pr", default="", help="PR URL; omit for local-only work")
    a.add_argument("--sha", required=True)
    a.add_argument("--verdict", choices=VERDICTS, required=True)
    a.add_argument("--author", required=True, help="provider/model of the worker")
    a.add_argument("--verifier", required=True, help="provider/model of the reviewer")
    a.add_argument("--note", default="")
    a.add_argument("--same-family", action="store_true", help="allow it when no other family is runnable")
    a.add_argument("--report", default="", help="the round's findings file under reports/, such as D2-review-1.md")
    a.add_argument("--edits", default="",
                   help=f"the round's review report under reports/, such as D2-review-1.md; --verdict {EDITS_VERDICT} needs it, "
                        "and no other verdict takes it; refused with one line that says why when its edits cannot be read")
    a.add_argument("--reviewer-thread", default="",
                   help="the reviewer's thread id; writes the reviewer row of threads.tsv for this attempt")
    a.add_argument("--reason", default="", choices=REASONS,
                   help=f"why the review sent the work back, one of {', '.join(REASONS)}; a send-back verdict needs "
                        "it, and no other verdict takes it")
    kind = a.add_mutually_exclusive_group()
    kind.add_argument("--member", action="store_true",
                      help="a panel member's row that does not carry the item's verdict; the dish is left as it is")
    kind.add_argument("--late", action="store_true",
                      help="a round found unrecorded after the dish moved on; the dish is left as it is")
    a = t.add_parser("check")
    a.add_argument("dish")
    a.add_argument("--sha", required=True)
    a.add_argument("--json", action="store_true", help="print verdict, author, verifier, note, and crossFamily of the latest row")
    stats_help = (
        "Print the first-pass rate overall and per author model. An item has at least one non-member pass.tsv row. "
        "Its first non-member row in table order sets its author and first verdict. The rate is the percentage of items whose first verdict is pass. Every other verdict counts as a failure. "
        "Select the last N items by the at cell of their last non-member row, descending. Ties keep the order in which items first occur in the table. Count all non-member send-back rows of those items by reason. "
        f"An empty reason or one outside {', '.join(REASONS)} counts as unrecorded. An empty window prints no items with a verdict."
    )
    a = t.add_parser("stats", help=stats_help, description=stats_help)
    a.add_argument("--last", type=non_negative_int, default=20,
                   help="how many items to cover, counting back from the most recently reviewed; default 20")
    hits_help = (
        "Print a per-reviewer table of rounds, passes, pass-with-edits rounds, send-backs, upheld and overruled "
        "findings, and the upheld rate, over the last N days.\n"
        "\n"
        "A row counts for the verifier its verifier cell names, including a --member row and a --late row. "
        "Rows are judged in table order. A round is one pass.tsv row whose at cell is at or after the window's "
        "start, which is the given number of days back from now.\n"
        "\n"
        "A send-back is upheld when the same dish's next recorded round is a pass at a different SHA, which is the next review accepting the change. A later round at another SHA that is not a pass upholds nothing and counts in neither upheld nor overruled. It is overruled "
        "when the dish's next recorded verdict is a pass at the same SHA, or when the dish's round-budget decision "
        "was answered known limits or drop after the send-back row's at cell and no later non-member row of the "
        "dish is at another SHA. A send-back that neither rule settles counts in neither upheld nor overruled. "
        "A pass-with-edits round is "
        "upheld when edits.tsv recorded the edits for the dish from that row's SHA and that row's report, and "
        "counts in neither otherwise. A pass round and a blocked round are rounds and nothing more, and a "
        "send-back that is still pending counts as a send-back round.\n"
        "\n"
        "The upheld rate is upheld over upheld plus overruled, one decimal place, and n/a when a seat has "
        "neither. A member row settles nothing for the dish, so the next recorded round is always a non-member "
        "row, but a member row still counts as a round for its own seat and is judged by these definitions. An "
        "empty window prints no reviewed rounds in the last N days."
    )
    a = t.add_parser("hits", help=hits_help, description=hits_help)
    a.add_argument("--last", type=non_negative_int, default=7,
                   help="how many days to cover, counting back from now; default 7")


def register_edits(sub):
    p = sub.add_parser("edits", help="compare a head with a reviewed commit that has its pass-with-edits round's edits applied and the build run")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("verify",
                     help="apply the edits of the item's last pass-with-edits round at --from to a scratch copy of that "
                          "commit, run the build command there unless it is none, and compare the copy with --to at every "
                          "path either commit holds where the two commits differ or the edits or the build changed the "
                          "copy; when every path agrees, run the check command there unless it is none; a run with no "
                          "failure adds one edits.tsv row for the pair unless an equal row exists; no worktree is read or "
                          "written")
    a.add_argument("dish")
    a.add_argument("--from", dest="from_sha", metavar="SHA", required=True,
                   help="the reviewed commit, as the pass-with-edits row records it")
    a.add_argument("--to", metavar="SHA", required=True, help="the head to compare")


def register_86(sub):
    p = sub.add_parser("86", help="park or answer a decision that needs the user")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("add")
    a.add_argument("--question")
    a.add_argument("--options")
    a.add_argument("--default")
    a.add_argument("--dish", default="")
    a.add_argument("--round-budget", action="store_true",
                   help="with --dish: write the decision a dish owes at its stored roundBudget send-backs; takes no question, options, or default")
    a = t.add_parser("answer")
    a.add_argument("id")
    a.add_argument("--answer", required=True)
    t.add_parser("list")


def register_credit(sub):
    p = sub.add_parser("credit", help="print the credit line a pull request body carries")
    p.add_argument("dish")
    p.add_argument("--coordinator", default="", metavar="PROVIDER/MODEL",
                   help="the coordinator's model; adds the Coordinated by part")


def register(sub):
    register_thread(sub)
    register_pass(sub)
    register_edits(sub)
    register_86(sub)
    register_credit(sub)


def handle(restaurant, args):
    if args.command == "credit":
        return credit_text(restaurant, args.dish, args.coordinator)
    if args.command == "edits":
        return verify_edits(restaurant, args.dish, args.from_sha, args.to)
    if args.command == "pass" and args.action == "stats":
        return pass_stats(restaurant, args.last)
    if args.command == "pass" and args.action == "hits":
        return pass_hits(restaurant, args.last)
    if args.command == "pass" and args.action == "record" and (refusal := reason_refusal(args.verdict, args.reason)):
        raise BrigadeError(refusal)
    if args.command in ("thread", "pass", "86"):
        with restaurant.checked():
            return seam.command(restaurant, args)
    return None


def handle_locked(restaurant, args, view):
    if args.command == "thread":
        restaurant.find("dishes.tsv", args.dish)
        added = write_threads(restaurant, args.dish, [(args.role, args.thread, args.seat)])
        return f"{args.dish}: {args.role} {'on record' if added else 'already on record'}"
    if args.command == "pass":
        if args.action == "record":
            result = record_pass(restaurant, args.dish, args.pr, args.sha, args.verdict, args.author, args.verifier,
                                 args.note, args.same_family, args.report, args.member, args.late, args.edits,
                                 args.reviewer_thread, args.reason)
            announce_archive(restaurant, args.dish, "review", thread=args.reviewer_thread, sha=args.sha, late=args.late)
            return result
        ok, why = pass_check(restaurant, args.dish, args.sha)
        latest = latest_verdict(restaurant, args.dish, args.sha) if args.json else None
        if latest is not None:
            if ok:
                return pass_json(latest)
            print(pass_json(latest))
        if not ok:
            raise BrigadeError(why)
        return why
    if args.command == "86":
        if args.action == "add":
            given = [value for value in (args.question, args.options, args.default) if value is not None]
            if args.round_budget:
                if given or not args.dish:
                    raise BrigadeError("86 add --round-budget takes --dish and no --question, --options, or --default")
                restaurant.find("dishes.tsv", args.dish)
            elif len(given) < 3:
                raise BrigadeError("86 add needs --question, --options, and --default")
            open_row = open_item_decisions(restaurant.rows("86.tsv")).get(args.dish)
            if open_row:
                if args.round_budget and open_row["kind"] != ROUND_KIND:
                    raise BrigadeError(f"{args.dish} is held by {open_row['id']}; answer it, "
                                       f"then run 86 add --dish {args.dish} --round-budget")
                return open_row["id"]
            if args.round_budget:
                args.question, args.options, args.default = round_decision(restaurant, args.dish)
            question, options, default = clean(args.question), clean(args.options), clean(args.default)
            if args.dish:
                keys = {option_key(option) for option in options.split(",") if option.strip()}
                if option_key(default) not in keys or len(keys) < 2:
                    raise BrigadeError("an item decision's default must be one of its options, "
                                       "with at least one other option that closes it")
            ident = restaurant.next_id("86.tsv")
            restaurant.append("86.tsv", {"id": ident, "at": seam.now(), "state": "open", "dish": args.dish,
                                         "question": question, "options": options, "default": default,
                                         "kind": ROUND_KIND if args.round_budget else ""})
            restaurant.log("decision", ident, "open", question)
            return ident
        if args.action == "answer":
            _, row = restaurant.find("86.tsv", args.id)
            if not row["dish"].strip():
                restaurant.update("86.tsv", args.id, "decision", state="answered", answer=args.answer, answered=seam.now())
                return f"{args.id} answered"
            if row["state"] == "answered":
                return f"{args.id} answered"
            closes = closing_options(row)
            if not closes:
                restaurant.update("86.tsv", args.id, "decision", state="answered", answer=args.answer, answered=seam.now())
                return f"{args.id} answered"
            match = next((option for option in closes if option_key(option) == option_key(args.answer)), None)
            if match is None:
                quoted = " or ".join(shlex.quote(option) for option in closes)
                return f"{args.id} still open; {row['dish']} stays held until 86 answer {args.id} --answer {quoted}"
            restaurant.update("86.tsv", args.id, "decision", state="answered", answer=match, answered=seam.now())
            return f"{args.id} answered"
        lines = []
        for row in restaurant.rows("86.tsv"):
            if row["state"] != "open":
                continue
            label = f"{row['id']} for {row['dish']}" if row["dish"] else row["id"]
            lines.append(f"{label}: {row['question']} Options: {row['options']}. Default: {row['default']}.")
        return "\n".join(lines) or "no open decisions"
    return None
