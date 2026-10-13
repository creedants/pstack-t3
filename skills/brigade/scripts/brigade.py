#!/usr/bin/env python3
"""Bookkeeping for brigade restaurants.

A restaurant is one directory under the brigade store. Its tables hold current
state and log.tsv holds every change, so a report can say what is new since
the last one. The head chef thread is the only writer.

Kitchen words name files and commands. Output is plain engineering prose.
"""
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

import sys as _sys
import types as _types
import uuid as _uuid
from pathlib import Path as _Path
_libdir = _Path(__file__).resolve().parent / "brigade_lib"


def _drop_cached_brigade(*names):
    """Drop every cached module under one of these names, so a load cannot reuse another load's copy."""
    for _n in [n for n in _sys.modules if any(n == name or n.startswith(name + ".") for name in names)]:
        del _sys.modules[_n]


_drop_cached_brigade("brigade_lib")
_pkg = "_brigade_ns_" + _uuid.uuid4().hex
_alias = _types.ModuleType(_pkg)
_alias.__path__ = [str(_libdir)]
_sys.modules[_pkg] = _alias
_sys.modules["brigade_lib"] = _alias
_bw = _sys.dont_write_bytecode
_sys.dont_write_bytecode = True
try:
    from brigade_lib import seam as _seam_bootstrap
    from brigade_lib.briefs import ESCALATED, MEASURING_STATIONS, MODE_RESOLUTIONS, ModeInputs, ModeLines, SEAT_RULE, WAIT_RULE, brief, brief_dish, contract_file, mode_inputs, roles_mode, stored_contract, write_brief
    from brigade_lib.edits import BLANK, BLOB_MODES, Digest, EDITS_REFUSED, EDITS_SECTION, EDIT_FAILS, EDIT_MARKS, EDIT_TITLE, Edit, EditsRefused, FILE_LINE, HEADING, LABELS, NEEDS_COMMANDS, NESTED, NO_COMMAND, PATH_LINES, TreeEntry, UNDERLINE, UNREAD, apply_edits, commit_tree, compared_paths, digests, edit_mark, exit_code, git, head_digest, occurrences, one_edit, parse_edits, path_lines, replay, report_edits, report_parts
    from brigade_lib.items import ATTEMPT_AFTER, DocAllowed, DocRefused, PreparedDocFire, _CATALOG, _SharedDoc, _can_cover_catalog, _covers, _fixed_docs, _gate_inventory, _held_from_tracked, _is_skill_file, _lease_parts, _skill_roots, _walk_opaque, attempt_kind, attempt_starts, block_note, checked_fire_label, drop, fire, fire_label, item_escalation, latest_mode_note, menu_purpose, prepare_doc_fire, record_blocked, record_escalation, retired_worker_threads, unfireable, workers_full, write_lead_kind
    from brigade_lib.leases import ClaimError, ENTRY_LINE, NOT_STOPPED, autofire_of, block_holds, block_inputs, blocked_waiting, changelog_fragment, claim_lease, contract_mode, entry_line, fire_command, fragment_lines, holder, holding_blocks, land_result, lease_block, lease_check, lease_states, next_owner, paths_inside, paths_overlap, recheck_blocks, refusal_kind, release_lease, renew_line, repository_cap, require_workers, running_workers, set_thread, with_fragment, worker_cap, workers_line
    from brigade_lib.reporting import GROUPED_SECTIONS, REPAIR, REQUEST_NOTE, SECTIONS, _ruling_note, add_ruling, admin_requests, admin_sections, counts, display_root, end_ruling, events_since, finished_by_coordinator, goal_lines, leases_for, log_rows, log_rulings, overrule, relayed, report, republish, ruling_line, section_lines, send_request, status_label_lines, status_line, sync, tally_words, unclaimed_reports, unlogged_rulings, walk
    from brigade_lib.reports import ITEM_REPORT, NOT_A_REPORT, REVIEW_FILE, in_reports, item_report, report_entry, report_stat
    from brigade_lib.reviews import CLAIMS_LINE, CLAIMS_STATES, THREADS_ITEM, THREADS_REFUSED, THREADS_REFUSED_LINE, THREADS_SECTION, cross_family, edits_gate, fix_round_refusal, item_verdicts, latest_verdict, pass_check, pass_json, record_pass, report_threads, report_threads_refusal, review_report, roles_family, round_count, round_debt, round_decision, send_backs, unrecorded_reviews, verify_edits, worker_report, write_threads
    from brigade_lib.shared import BrigadeError, FENCE_OPEN, FENCE_OPEN_INDENTED, KEPT_CELLS, MalformedTable, age, clean, command_words, fence_close, fence_open, now, one_of, plural, same_text, slug, stored, threads_fence_open
    from brigade_lib.store import ADDED_COLUMNS, ADMIN_COMMANDS, ADMIN_DIR, ADMIN_LINES, ADMIN_MENU, ADMIN_NAME, AUTOFIRE, CHILD_ROLES, CLAIM_WAIT_SECONDS, CLOSED_GOAL_STATES, COUNTED_STATES, DISH_STATES, EDITS_VERDICT, GENERATED_ROOT, GOAL_MOVES, GOAL_STATES, HELD_LOCKS, HOUSE_RULES, IN_FLIGHT_STATES, KINDS, KIND_CHOICE, LEASED_STATES, LINE_SUFFIX, LIVE_TICKET_STATES, LOW_FLAG_DAYS, LOW_RESERVE, MENU, MESSAGE_ONLY, MODES, NEEDS_CONTRACT, NOTHING_HANDED, OFF_MENU_TEMPLATE, OPEN_LINES, OPEN_RUN_MINUTES, OWING_STATES, PREFIX, PRIORITIES_BY_RANK, PURPOSE_TEMPLATE, REPORTING, ROUND_BUDGET, ROUND_DECISIONS, ROUND_KIND, ROUND_PARKED, RULING_KINDS, RULING_RULES, RULING_STATES, Restaurant, SNAPSHOT_CHUNK, SOURCE_PRIORITY, SOURCE_ROOT, STATION_KIND, TABLES, THREADS_WRITERS, THREAD_COMMAND_ROLES, THREAD_ROLES, TICKET_STATES, UNSENT, VERDICTS, WORKER_ROLES, WORK_COMMANDS, _read_meta, holder_prefix, intake_owner, is_admin, is_timestamp, menu_section, open_restaurant, parse_row, read_chunk, refuse_owned_intake, reporting_of, roles_script, sibling_lines, siblings, store_path, store_root, wait_for_meta, write_atomic
    from brigade_lib.tickets import ActsOn, EVERY_ROW, Fault, FollowUps, GOALS_ONLY, Goal, Label, LabelCounts, Labels, NO_WORK, Tally, Tracked, add_goal, add_ticket, append_ticket, base_source, count_labels, file_follow_ups, finish_request, finished_requests, follow_up_kind, follow_ups, frozen_label, goal_share, goal_work, handed_count, handoff_id, in_flight_tickets, inbox_file, intake_list, is_live, label_words, move_ticket, open_goal, paths_of, pattern_directory, priority_of, quoted_paths, read_goal, read_label, read_labels, record_paths, refuse_live_ref, request_files, says_no_work, set_goal, set_intake, sibling_named, sibling_rails, take_inbox, take_tickets, taken_row, tell, ticket_paths, tracked_paths, unique_names, whole_share
    from brigade_lib.wake import CLOSE_OCCASIONS, DRAIN_LABELS, Debt, If, NO_WORKER, OCCASION_ORDER, ROUTINE_DETAILS, Reply, Run, STANDINGS, STARTS_AFTER, See, Snapshot, Standing, StandingText, Trigger, Waiting, YES_OCCASIONS, accepted_argv, admin_sends, blocked_needs_user, budget_reached, closing_options, compile_sheet, counted_timebox, crossing_send_back, cue_line, decision_detail, decision_holds, dish_by_id, dish_progress, format_sheet, fresh_decision_ids, from_user_request, hang_attempt, held_occasions, holder_of, housekeeping, inbox_cues, inbox_ready, is_drained, land_follow, lease_refusals, load_inbox, load_snapshot, lowest_id, malformed_close, maybe_run, merge_accepted, merge_why, next_text, note_unsent, occasion_yes, older_than_hour, open_for, open_item_decisions, option_key, pass_words, pause_if, plan, prefix_cues, record_hang, reply_for, report_is_current, request_text, routine_detail, startable, startable_lines, tail_cues, ticket_lines, trigger_from, waiting_ticket, wake_label, watch, worker_where
finally:
    _sys.dont_write_bytecode = _bw
    del _bw
from brigade_lib import seam
seam.bind(globals())
_drop_cached_brigade("brigade_lib", _pkg)
del _drop_cached_brigade, _pkg, _alias, _libdir, _types, _uuid

# next sets this. A write or a land.py call then raises instead of changing a store.
READ_ONLY = False
LAND = Path(__file__).resolve().parents[2] / "landing" / "scripts" / "land.py"
BUILT_ROLES = Path(__file__).resolve().parents[2] / "pstack-runtime" / "scripts" / "roles.py"
SOURCE_ROLES = Path(__file__).resolve().parents[3] / "scripts" / "roles.py"


def parser():
    top = argparse.ArgumentParser(prog="brigade.py", description=__doc__.splitlines()[0])
    top.add_argument("--store", help="store root (default $BRIGADE_STORE or $XDG_STATE_HOME/pstack-t3/brigade)")
    top.add_argument("--at", default=os.environ.get("BRIGADE_DIR"), help="restaurant directory (default $BRIGADE_DIR)")
    top.add_argument("--owner", help="<thread>@<generation> from status; every write in a store with a generation needs it")
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("open", help="create a restaurant, or print an existing one")
    p.add_argument("--project-root", required=True)
    p.add_argument("--name")
    p.add_argument("--admin", action="store_true", help="open the repository's executive admin at <project>/.admin; takes no --name")
    p.add_argument("--reporting", choices=REPORTING, default="milestones",
                   help="how often the coordinator replies (default: milestones)")
    p.add_argument("--intake", default="", help="comma-separated intake sources this coordinator owns, such as github")
    p.add_argument("--workers", type=int, help="how many dishes may be in progress or in review; missing reads as 2")
    p.add_argument("--mode", choices=MODES, help="this restaurant's light or full mode; missing leaves it to the roles files")

    p = sub.add_parser("set", help="record the head chef thread, a schedule id, or the reporting level")
    p.add_argument("--thread")
    p.add_argument("--replace", action="store_true", help="replace a recorded coordinator thread")
    p.add_argument("--expect", help="executive admin only: replace the thread only while it is still this value")
    p.add_argument("--stopped", help="with --expect recovering:<id>: the run id t3_thread_wait reported terminal, idle, or gone")
    p.add_argument("--reports-to", help="the executive admin's thread this coordinator reports to; \"\" clears it")
    p.add_argument("--schedule", action="append", default=[], metavar="NAME=ID")
    p.add_argument("--reporting", choices=REPORTING, help="how often the coordinator replies")
    p.add_argument("--intake", help="comma-separated intake sources this coordinator owns; replaces the list, and \"\" clears it")
    p.add_argument("--workers", type=int, help="how many dishes may be in progress or in review")
    p.add_argument("--mode", help="full or light from the next brief; \"\" leaves it to the roles files")
    p.add_argument("--build-command",
                   help="the build command edits verify runs; none records that the project has no build, and \"\" removes "
                        "the setting")
    p.add_argument("--check-command",
                   help="the check command edits verify runs; none records that the project has no check, and \"\" removes "
                        "the setting")
    p.add_argument("--autofire", choices=AUTOFIRE,
                   help="the lowest ticket priority the startable command lists as startable, or off; missing reads as normal")

    p = sub.add_parser("ticket", help="add, list, update, move, take, or record paths on tickets on the rail")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("add")
    how = a.add_mutually_exclusive_group(required=True)
    how.add_argument("--summary")
    how.add_argument("--from-report", metavar="FILE",
                     help="an item report under reports/, such as reports/D2.md; files one waiting ticket per follow-up in it "
                          "and changes only the ticket table, the log, and lastActivityAt; each ticket takes the goal of the "
                          "report's item, and when that item has a kind the ticket takes fix at priority urgent or normal "
                          "and polish at low; a follow-up is filed with no --priority, so it is low")
    a.add_argument("--dry-run", action="store_true", help="with --from-report: print what it would add; it changes no table and no log")
    a.add_argument("--source", default="user")
    a.add_argument("--ref", default="")
    a.add_argument("--request", default="", help="the admin request id this ticket carries out; refuses a second ticket for it")
    a.add_argument("--again", action="store_true",
                   help="with --source user and no --ref: add the ticket even when a waiting or assigned ticket of this store "
                        "has the same summary. Case, leading and trailing whitespace, and the length of a whitespace run "
                        "do not count")
    a.add_argument("--priority", choices=PRIORITIES_BY_RANK,
                   help="missing reads as urgent from upstream, low from report, and normal from every other source")
    a.add_argument("--paths", default="", help="comma-separated files and directories the work will touch")
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
    a.add_argument("--paths", help='replaces the recorded paths; "" clears them')
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
                        "and it does not affect which tickets start")
    a = t.add_parser("set", help="change a goal's state, name, purpose, done test, or share")
    a.add_argument("id")
    a.add_argument("--state", choices=GOAL_STATES,
                   help="paused is recorded and printed. It does not affect which tickets start. "
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
                        "goal list prints it, and it does not affect which tickets start")
    a = t.add_parser("list",
                     help="the state, name, counts, purpose, and done test of each open or paused goal and of each done or "
                          "dropped goal that has a waiting ticket, an item in flight, or a ticket riding along, then the "
                          "counts of unassigned work; waiting counts waiting tickets; in flight counts items in progress, "
                          "in review, passed, sent back, blocked, or waiting to land; merged counts merged items; "
                          "send-backs counts the send-back verdicts on the goal's items, in any state, that are not a "
                          "panel member's row; riding along counts assigned tickets of the goal that are not in an item "
                          "of the goal")
    a.add_argument("--all", action="store_true", help="also print each other done or dropped goal")

    p = sub.add_parser("fire", help="group tickets into one dish and assign it to a station")
    p.add_argument("--tickets", required=True,
                   help="comma-separated ticket ids; the first is the lead ticket; the item takes the lead ticket's goal, "
                        "and its kind when it has one")
    p.add_argument("--station", required=True, help="the pstack playbook the worker runs")
    p.add_argument("--summary", required=True)
    for field in ("task", "thread", "branch"):
        p.add_argument(f"--{field}", default="")
    p.add_argument("--timebox", type=int, default=60, help="minutes before the liveness check flags the dish")
    p.add_argument("--paths", default="", help="paths the dish will change; fire claims a landing lease on them first")
    p.add_argument("--mode", help="full, to start the dish in full mode; needs --reason")
    p.add_argument("--reason", help="one line: why the dish runs in full mode")
    p.add_argument("--kind", choices=KINDS,
                   help="the item's kind when the lead ticket has none or --tickets names no ticket; a lead ticket with no "
                        "kind records it too; refused when the lead ticket has another kind; with no --kind and no kind "
                        "from the lead ticket, station bug-fix or perf-issue gives fix, station investigation gives study, "
                        "and fire is refused on any other station")
    p.add_argument("--ride-along", default="",
                   help="comma-separated ids from --tickets, not the first, that may be under another goal than the first "
                        "ticket's; each must have priority low and must not be a feature; every other ticket after the "
                        "first must have the first ticket's goal or no goal")
    p.add_argument("--doc-item", action="store_true",
                   help="allow a shared document in --paths and record it as a document item")

    p = sub.add_parser("brief", help="render the worker brief for a dish; refuses when a field or the coordinator thread is missing")
    p.add_argument("id")
    p.add_argument("--fields", help="JSON file of brief fields, or - for stdin")
    p.add_argument("--goal", default=None, help="one sentence: the outcome")
    p.add_argument("--acceptance", action="append", default=[], help="a checkable criterion; repeatable, at least one")
    p.add_argument("--contract", default=None,
                   help="the item's contract, rendered as given under CONTRACT: in the brief; write none when the item has no "
                        "safety, privacy, or integrity property; a brief with no contract is refused")
    p.add_argument("--verify", default=None, help="exact commands that prove it, plus known gotchas")
    p.add_argument("--paths", default=None, help="leased paths, when fire did not claim them")
    p.add_argument("--lease", default=None, help="the landing lease id, when fire did not claim it")
    p.add_argument("--base", default=None, help="the trunk ref the worker's branch starts from, such as origin/main")
    p.add_argument("--context", action="append", default=[], help="a pointer to files, PRs, or upstream reports; repeatable")

    p = sub.add_parser("contract", help="print the contract the dish's last brief stored")
    p.add_argument("id")

    sub.add_parser("watch", help="liveness: which dishes have reports, are running, or are over their timebox")
    sub.add_parser("startable", help="waiting tickets in the order they should start, each with why it can or cannot start now; "
                                     "a low ticket that would take the last idle worker is held back while any urgent "
                                     "or normal ticket is waiting, whether or not that ticket can start now; "
                                     "it starts nothing")

    p = sub.add_parser("hang", help="record one open run after report-back, once per attempt")
    p.add_argument("id")
    p.add_argument("--attempt", type=int, help="an earlier attempt, counted from 1, whose run was left open")
    p.add_argument("--provider", required=True)
    p.add_argument("--minutes", type=int, required=True)

    p = sub.add_parser("thread", help="record one thread an item used, for a role no other command writes")
    p.add_argument("dish")
    p.add_argument("--role", required=True, choices=THREAD_COMMAND_ROLES,
                   help="the row's role; the other six roles are written by dish --thread, --reviewer-thread, "
                        "or a report")
    p.add_argument("--thread", required=True, help="the thread id")
    p.add_argument("--seat", required=True, help="the thread's seat as provider/model")

    p = sub.add_parser("dish", help="update a dish")
    p.add_argument("id")
    p.add_argument("--state", choices=DISH_STATES)
    for field in ("task", "thread", "branch", "pr", "sha"):
        p.add_argument(f"--{field}")
    p.add_argument("--timebox", type=int, help="minutes; raise it once for a worker that is still making progress")
    p.add_argument("--reported", action="store_true", help="record that this attempt's report-back arrived")
    p.add_argument("--lease", help="record a lease id claimed again")
    p.add_argument("--paths", help="record the paths that lease covers")
    p.add_argument("--stopped", help="with --state dropped: the run id t3_thread_wait reported terminal, or idle")
    p.add_argument("--mode", help="full, to move open work to full mode once; needs --reason")
    p.add_argument("--reason", help="one line: why the dish moves to full mode")

    p = sub.add_parser("pass", help="record or check a review verdict for a dish at a head SHA")
    t = p.add_subparsers(dest="action", required=True)
    threads_help = (
        "Records the verdict, and writes one row of threads.tsv for the reviewer and for each child the worker's "
        "report lists. The reviewer's row comes from --reviewer-thread and takes its seat from --verifier. The "
        "child rows come from a ## Threads section of reports/<dish>.md, which this command reads and takes no "
        "flag for.\n"
        "\n"
        "That section starts with the heading `## Threads` on a line of its own, and every other line of it that "
        "is not blank, is not a heading, and is not inside a fenced block that opens after up to three spaces is "
        "one list item of the form - <role> `<thread id>` <seat>.\n"
        "\n"
        "This example uses made-up ids. A report whose worker used two children holds the heading `## Threads` and "
        "then two items, one for a code delegate whose thread id is `mcp:00000000-0000-4000-8000-000000000001` and "
        "whose seat is `opencode-go/step-5-preview-free`, and one for a claims check whose thread id is "
        "`mcp:00000000-0000-4000-8000-000000000002` and whose seat is `codex/gpt-6.1-sol`.\n"
        "\n"
        "The three roles a child may take are code delegate, claims check, and architect runner. A child whose "
        "role is any of the other four is refused, because dish --thread writes the worker rows, "
        "--reviewer-thread writes the reviewer row, and the thread command writes the panel member row. A "
        "report with no ## Threads section adds no row and is not an error. A line that is not one such item is "
        "refused by its number. An item whose thread id the table would change is refused by its number too. "
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
    kind = a.add_mutually_exclusive_group()
    kind.add_argument("--member", action="store_true",
                      help="a panel member's row that does not carry the item's verdict; the dish is left as it is")
    kind.add_argument("--late", action="store_true",
                      help="a round found unrecorded after the dish moved on; the dish is left as it is")
    a = t.add_parser("check")
    a.add_argument("dish")
    a.add_argument("--sha", required=True)
    a.add_argument("--json", action="store_true", help="print verdict, author, verifier, note, and crossFamily of the latest row")

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

    p = sub.add_parser("86", help="park or answer a decision that needs the user")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("add")
    a.add_argument("--question")
    a.add_argument("--options")
    a.add_argument("--default")
    a.add_argument("--dish", default="")
    a.add_argument("--round-budget", action="store_true",
                   help=f"with --dish: write the decision a dish owes at {ROUND_BUDGET} send-backs; takes no question, options, or default")
    a = t.add_parser("answer")
    a.add_argument("id")
    a.add_argument("--answer", required=True)
    t.add_parser("list")

    p = sub.add_parser("inbox", help="take handed tickets and the admin's requests, or finish a request")
    t = p.add_subparsers(dest="action", required=True)
    t.add_parser("take", help="file handed tickets and print each request file")
    a = t.add_parser("done", help="record a request as acted on and delete its file")
    a.add_argument("id")

    p = sub.add_parser("request", help="executive admin: publish a request line into a coordinator's inbox")
    p.add_argument("line", nargs="?")
    p.add_argument("--to", help="the coordinator's directory name")
    p.add_argument("--republish", action="store_true", help="publish again each request a crash left unwritten")

    p = sub.add_parser("rule", help="executive admin: record, overrule, end, or list rulings")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("add")
    a.add_argument("--kind", choices=RULING_KINDS, required=True)
    a.add_argument("--parties", required=True, help="comma-separated coordinator names")
    a.add_argument("--question", required=True)
    a.add_argument("--rule", choices=RULING_RULES, required=True, help="the rule that decided it")
    a.add_argument("--decision", required=True)
    a.add_argument("--supersedes", default="", help="the ruling this one replaces")
    a = t.add_parser("overrule", help="record the user's ruling in place of this one")
    a.add_argument("id")
    a.add_argument("--decision", required=True)
    a = t.add_parser("set", help="record how a ruling ended")
    a.add_argument("id")
    a.add_argument("--state", choices=("done", "expired"), required=True)
    a = t.add_parser("list")
    a.add_argument("--state", choices=RULING_STATES)

    sub.add_parser("sync", help="executive admin: copy each coordinator's new log rows into this log")

    sub.add_parser("status", help="the thread line first, then counts, then workers outside the executive admin's store, "
                                  "then a kinds line, a goals line, and an untagged line when each has something to count, "
                                  "then reports to, mode, and owner when present")
    p = sub.add_parser("close", help="write the report of what changed since the last one")
    output = p.add_mutually_exclusive_group()
    output.add_argument("--dry-run", action="store_true")
    output.add_argument("--to-file", action="store_true",
                        help="print only the path of the written report, not its text")
    p = sub.add_parser("walk", help="every restaurant's counts and open decisions, grouped by repository")
    p.add_argument("--stale-hours", type=float, default=24)
    p.add_argument("--repo", help="print only this repository")

    p = sub.add_parser("next", help="print the steps this wake owes and whether the wake is a reply. "
                                    "Leave the wake off when this wake has no known cause")
    p.add_argument("--failure", choices=("fixable", "stuck"), default=None,
                   help="fixable or stuck, for a failure this wake already knows")
    p.add_argument("--unsent", choices=UNSENT, default=None,
                   help="a line to the executive admin that did not send")
    wakes = p.add_subparsers(dest="wake")

    def wake_parser(name, help_text):
        parser = wakes.add_parser(name, help=help_text)
        parser.add_argument("--failure", choices=("fixable", "stuck"), default=argparse.SUPPRESS,
                            help="fixable or stuck, for a failure this wake already knows")
        parser.add_argument("--unsent", choices=UNSENT, default=argparse.SUPPRESS,
                            help="a line to the executive admin that did not send")
        return parser

    wake_parser("report-back", "a worker report arrived for this dish").add_argument("dish")
    review = wake_parser("review", "a review verdict for this dish")
    review.add_argument("dish")
    review.add_argument("--verdict", choices=VERDICTS, required=True)
    checks = wake_parser("checks", "a pull request check for this dish")
    checks.add_argument("dish")
    checks.add_argument("--result", choices=("pass", "fail"), required=True)
    wake_parser("merge", "the queue may have landed this dish").add_argument("dish")
    wake_parser("liveness", "the liveness schedule woke")
    wake_parser("intake", "the intake schedule woke")
    wake_parser("morning", "the morning service woke")
    wake_parser("evening", "the evening report is due")
    user = wake_parser("user", "a message from the user")
    user.add_argument("--question", action="store_true", help="the message asks a question")
    wake_parser("admin", "a request from the executive admin").add_argument("request")
    return top


def brief_contract(value):
    """The contract as given. Refuses None and text that is only whitespace."""
    if not (value or "").strip():
        raise BrigadeError(NEEDS_CONTRACT)
    return value


def _brief_fields(args):
    flagged = any(getattr(args, name) is not None for name in ("goal", "contract", "verify", "base", "paths", "lease")) or args.acceptance or args.context
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
        allowed = {"goal", "acceptance", "contract", "verify", "base", "context", "paths", "lease"}
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
            "contract": brief_contract(payload.get("contract")),
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
        "contract": brief_contract(args.contract),
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
        if args.admin and args.name:
            raise BrigadeError("open --admin takes no --name")
        if not args.admin and not args.name:
            raise BrigadeError("open needs --name, or --admin")
        require_workers(args.workers)
        restaurant, created = open_restaurant(root, args.project_root, args.name, args.reporting,
                                              intake_list(args.intake), args.workers, args.admin, args.mode)
        lines = [f"{'opened' if created else 'exists'} {restaurant.dir}"]
        meta = restaurant.meta
        thread = (meta.get("thread") or "").strip()
        if not created and thread:
            lines.append(f"thread {thread} already recorded")
        intake = meta.get("intake") or []
        if not created and args.intake and intake_list(args.intake) != intake:
            lines.append(f"intake stays {', '.join(intake) or 'empty'}; change it with set --intake")
        if not created and args.mode is not None and args.mode != meta.get("mode"):
            lines.append(f"mode stays {meta.get('mode') or 'unset'}; change it with set --mode")
        lines.extend(sibling_lines(restaurant))
        if siblings(restaurant.dir, restaurant.meta.get("projectRoot")) and not is_admin(meta):
            cap = repository_cap(restaurant.meta["projectRoot"])
            workers = worker_cap(restaurant.meta)
            if cap is not None and workers >= cap:
                lines.append(f"warning: workers {workers} is at or above the repository cap of {cap} while a sibling exists")
        return "\n".join(lines)
    if args.command == "walk":
        text, failures = walk(root, args.stale_hours, args.repo)
        if failures:
            print(text)
            raise BrigadeError("\nbrigade: ".join(failures))
        return text
    if not args.at:
        raise BrigadeError("pass --at <restaurant dir> or set BRIGADE_DIR")
    restaurant = Restaurant(args.at, args.owner)
    admin = is_admin(restaurant.meta)
    if admin and args.command in WORK_COMMANDS:
        raise BrigadeError("the executive admin routes work and never runs it")
    if not admin and args.command in ADMIN_COMMANDS:
        raise BrigadeError(f"{args.command} works only in the executive admin's store")
    if args.command == "next":
        return next_text(restaurant, args)
    if args.command == "sync":
        # The admin's lock is taken only after each coordinator's read lock is released, so this runs outside checked.
        text, failures = sync(restaurant)
        if failures:
            if text:
                print(text)
            raise BrigadeError("\nbrigade: ".join(failures))
        return text or "nothing new"
    if args.command == "request" and args.republish:
        if args.to or args.line:
            raise BrigadeError("request --republish takes no --to or line")
        finished = finished_by_coordinator(restaurant)
        with restaurant.checked():
            return republish(restaurant, finished)
    if args.command == "status":
        # land.py can wait on the landing database, so the contract is read before the store lock.
        contract = contract_mode(restaurant.meta["projectRoot"])
        with restaurant.checked():
            return command(restaurant, args, contract)
    if args.command == "set" and (args.thread or args.expect is not None):
        if args.thread is None:
            raise BrigadeError("--expect needs --thread")
        return set_thread(restaurant, args)
    if args.command in ("fire", "dish"):
        args.reason = item_escalation(args.mode, args.reason)
        if args.reason and args.command == "dish" and args.state in ("queued", "merged", "dropped"):
            raise BrigadeError(f"--mode full needs open work; run --state {args.state} without it")
    if args.command == "dish" and args.state == "dropped":
        return drop(restaurant, args.id, args.stopped)
    if args.command == "brief":
        # A stalled stdin or file must not hold the store lock, so the fields are read and checked first.
        fields = _brief_fields(args)
        return brief(restaurant, args.id, **fields)
    if args.command == "edits":
        return verify_edits(restaurant, args.dish, args.from_sha, args.to)
    if args.command == "fire":
        ids = [ident.strip() for ident in args.tickets.split(",") if ident.strip()]
        ride_along = [ident.strip() for ident in args.ride_along.split(",") if ident.strip()]
        return fire(restaurant, ids, args.station, args.task, args.thread, args.branch, args.summary, args.timebox,
                    args.paths, args.reason, args.kind or "", ride_along, args.doc_item)
    if args.command == "watch":
        return watch(restaurant)
    if args.command == "startable":
        return startable(restaurant)
    if args.command == "ticket" and args.action == "list":
        held = holding_blocks(restaurant)
        with restaurant.checked():
            return ticket_lines(restaurant, args.state, held)
    if args.command == "dish" and args.branch and args.lease is None:
        with restaurant.checked():
            result = command(restaurant, args)
        lines = fragment_lines(restaurant, args.id, args.branch)
        return f"{result}\n{lines}" if lines else result
    if args.command == "ticket" and args.action == "add" and args.from_report is not None:
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
    if args.command == "ticket" and args.action == "paths":
        project = restaurant.meta["projectRoot"]
        tracked = tracked_paths(project)
        if tracked is None:
            raise BrigadeError(f"cannot list tracked files in {project}")
        with restaurant.checked():
            return record_paths(restaurant, tracked, not args.dry_run)
    if args.command == "ticket" and args.action == "add" and args.dry_run:
        raise BrigadeError("--dry-run needs --from-report")
    if args.command == "ticket" and args.action in ("add", "set") and args.paths is not None:
        args.paths = ticket_paths(args.paths)
    rails = None
    if args.command == "ticket" and (args.action == "move" or args.action == "add" and args.ref):
        rails = sibling_rails(restaurant)
    with restaurant.checked():
        return command(restaurant, args, rails=rails)


def command(restaurant, args, contract=None, rails=None):
    if args.command == "set":
        meta = restaurant.meta
        changes, drop = {}, []
        if args.intake is not None:
            changes["intake"] = set_intake(restaurant, intake_list(args.intake))["intake"]
        if args.thread is not None and (args.thread or args.expect is not None):
            current = (meta.get("thread") or "").strip()
            generation, changing = next_owner(meta, args.thread, args.replace, args.expect, args.stopped)
            changes.update(generation=generation, thread=args.thread)
            if changing and current and not current.startswith("recovering:"):
                changes["previousThread"] = current
            if changing and is_admin(meta):
                # The row is the transition record. A crash before the metadata write keeps the stop evidence, and a retry finds the row.
                note = f"replaced {current}" if current else ("restarted" if meta.get("generation") else "first thread")
                note += f"; stopped {clean(args.stopped)}" if args.stopped else ""
                ident = f"{args.thread}@{generation}"
                if not any(row["kind"] == "thread" and row["id"] == ident for row in restaurant.rows("log.tsv")):
                    restaurant.log("thread", ident, "recorded", note)
        if args.reporting:
            changes["reporting"] = args.reporting
        if args.workers is not None:
            require_workers(args.workers)
            changes["workers"] = args.workers
        if args.mode is not None:
            if args.mode in MODES:
                changes["mode"] = args.mode
            elif args.mode == "":
                drop.append("mode")
            else:
                raise BrigadeError('--mode takes full, light, or ""')
        for key, value in (("buildCommand", args.build_command), ("checkCommand", args.check_command)):
            if value is None:
                continue
            if value.strip():
                command_words(value.strip())
                changes[key] = value.strip()
            else:
                drop.append(key)
        if args.autofire:
            changes["autofire"] = args.autofire
        if args.reports_to is not None:
            if clean(args.reports_to):
                changes["reportsTo"] = clean(args.reports_to)
            else:
                drop.append("reportsTo")
        if args.schedule:
            schedules = dict(meta.get("schedules") or {})
            for pair in args.schedule:
                if "=" not in pair:
                    raise BrigadeError(f"--schedule takes NAME=ID, got {pair!r}")
                name, ident = pair.split("=", 1)
                if ident:
                    schedules[name] = ident
                else:
                    schedules.pop(name, None)
            changes["schedules"] = schedules
        meta = restaurant.change_meta(drop, **changes)
        return json.dumps(meta, indent=2)

    if args.command == "contract":
        return stored_contract(restaurant, args.id)

    if args.command == "inbox":
        if args.action == "take":
            return take_inbox(restaurant)
        return finish_request(restaurant, args.id)

    if args.command == "request":
        if not args.to or args.line is None:
            raise BrigadeError('request needs --to <coordinator> "<line>", or --republish')
        return send_request(restaurant, args.to, args.line)

    if args.command == "rule":
        if args.action != "list":
            # A ruling command killed before its log row left an event to write first.
            log_rulings(restaurant)
        if args.action == "add":
            return add_ruling(restaurant, args.kind, args.parties, clean(args.question), args.rule, clean(args.decision),
                              clean(args.supersedes))
        if args.action == "overrule":
            return overrule(restaurant, args.id, clean(args.decision))
        if args.action == "set":
            return end_ruling(restaurant, args.id, args.state)
        rows = [row for row in restaurant.rows("rulings.tsv") if not args.state or row["state"] == args.state]
        return "\n".join(ruling_line(row) for row in rows) or "no rulings"

    if args.command == "goal":
        if args.action == "add":
            return add_goal(restaurant, args.name, args.purpose, args.done, args.share)
        if args.action == "set":
            return set_goal(restaurant, args.id, args.name, args.purpose, args.done, args.share, args.state, args.evidence)
        labels = read_labels(restaurant, EVERY_ROW)
        return goal_lines(labels, count_labels(restaurant, labels), args.all)

    if args.command == "ticket":
        if args.action == "add":
            return add_ticket(restaurant, args.summary, args.source, args.ref, args.request, rails or {}, again=args.again,
                              priority=args.priority or "", paths=args.paths, decision=args.decision,
                              kind=args.kind or "", goal=args.goal)
        if args.action == "move":
            return move_ticket(restaurant, args.id, args.to, rails or {})
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

    if args.command == "thread":
        restaurant.find("dishes.tsv", args.dish)
        added = write_threads(restaurant, args.dish, [(args.role, args.thread, args.seat)])
        return f"{args.dish}: {args.role} {'on record' if added else 'already on record'}"

    if args.command == "dish":
        _, current = restaurant.find("dishes.tsv", args.id)
        worker = any(value and value != current.get(field, "") for field, value in (("thread", args.thread), ("task", args.task)))
        if args.state == "in-progress" or worker:
            refusal = fix_round_refusal(restaurant, current)
            if refusal:
                raise BrigadeError(refusal)
        if args.state in COUNTED_STATES and current["state"] not in COUNTED_STATES:
            full = workers_full(restaurant)
            if full:
                raise BrigadeError(full)
        if (args.lease or args.paths) and current["state"] in ("merged", "dropped"):
            raise BrigadeError(f"{args.id} is {current['state']}; it holds no lease")
        if args.state in ("queued", "merged"):
            ok, why = pass_check(restaurant, args.id, args.sha or current["sha"])
            if not ok:
                raise BrigadeError(f"only reviewed work lands: {why}")
        entering = args.state == "in-progress" and current["state"] != "in-progress"
        restart = entering and current["state"] in ("sent-back", "queued")
        replacing = (bool(args.thread) and args.thread != current.get("thread", "")
                     and current["state"] == "in-progress" and args.state in (None, "in-progress"))
        thread = "" if restart and args.thread is None else args.thread
        recorded = set(retired_worker_threads(restaurant, args.id))
        earlier = set(recorded)
        if restart and current.get("thread"):
            earlier.add(current["thread"])
        if thread and thread in earlier:
            raise BrigadeError(f"thread {thread} is an earlier attempt of {args.id}; a send-back launches a fresh worker")
        escalated = None
        if args.reason:
            escalated = record_escalation(restaurant, args.id, args.reason)
        next_thread = current.get("thread", "") if thread is None else thread
        if current.get("thread") and next_thread != current["thread"] and (restart or next_thread):
            if current["thread"] not in recorded:
                restaurant.log("worker", args.id, "retired", current["thread"])
        reported = "" if entering or replacing else ("yes" if args.reported else None)
        transition = bool(args.state) and args.state != current["state"]
        ends = transition and args.state != "in-progress"
        closing = args.reported or ends or (thread and thread != current.get("thread", ""))
        keep = args.thread if args.thread and args.thread != current.get("thread", "") else ""
        row = restaurant.update("dishes.tsv", args.id, "dish", state=args.state, task=args.task, thread=thread,
                                branch=args.branch, pr=args.pr, sha=args.sha, timebox=args.timebox, reported=reported,
                                lease=args.lease, paths=args.paths)
        if replacing:
            restaurant.log("dish", args.id, "in-progress", row.get("summary", ""))
        if args.thread:
            write_threads(restaurant, args.id, [("worker", args.thread, "")], closing=closing, keep_thread=keep)
        elif closing:
            write_threads(restaurant, args.id, (), closing=True)
        if args.state == "merged":
            for ticket in filter(None, row["tickets"].split(",")):
                restaurant.update("rail.tsv", ticket, "ticket", state="done")
        if escalated is not None:
            return f"{args.id} {row['state']}, mode full: {escalated}"
        return f"{args.id} {row['state']}"

    if args.command == "pass":
        if args.action == "record":
            return record_pass(restaurant, args.dish, args.pr, args.sha, args.verdict, args.author, args.verifier,
                               args.note, args.same_family, args.report, args.member, args.late, args.edits,
                               args.reviewer_thread)
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
            # Validate the text the row will store, since the append turns tabs and newlines into spaces.
            question, options, default = clean(args.question), clean(args.options), clean(args.default)
            if args.dish:
                keys = {option_key(option) for option in options.split(",") if option.strip()}
                if option_key(default) not in keys or len(keys) < 2:
                    raise BrigadeError("an item decision's default must be one of its options, "
                                       "with at least one other option that closes it")
            ident = restaurant.next_id("86.tsv")
            restaurant.append("86.tsv", {"id": ident, "at": now(), "state": "open", "dish": args.dish,
                                         "question": question, "options": options, "default": default,
                                         "kind": ROUND_KIND if args.round_budget else ""})
            restaurant.log("decision", ident, "open", question)
            return ident
        if args.action == "answer":
            _, row = restaurant.find("86.tsv", args.id)
            if not row["dish"].strip():
                restaurant.update("86.tsv", args.id, "decision", state="answered", answer=args.answer, answered=now())
                return f"{args.id} answered"
            if row["state"] == "answered":
                return f"{args.id} answered"
            closes = closing_options(row)
            if not closes:
                restaurant.update("86.tsv", args.id, "decision", state="answered", answer=args.answer, answered=now())
                return f"{args.id} answered"
            match = next((option for option in closes if option_key(option) == option_key(args.answer)), None)
            if match is None:
                quoted = " or ".join(shlex.quote(option) for option in closes)
                return f"{args.id} still open; {row['dish']} stays held until 86 answer {args.id} --answer {quoted}"
            restaurant.update("86.tsv", args.id, "decision", state="answered", answer=match, answered=now())
            return f"{args.id} answered"
        lines = []
        for row in restaurant.rows("86.tsv"):
            if row["state"] != "open":
                continue
            label = f"{row['id']} for {row['dish']}" if row["dish"] else row["id"]
            lines.append(f"{label}: {row['question']} Options: {row['options']}. Default: {row['default']}.")
        return "\n".join(lines) or "no open decisions"

    if args.command == "hang":
        return record_hang(restaurant, args.id, args.provider, args.minutes, args.attempt)
    if args.command == "status":
        meta = restaurant.meta
        thread = (meta.get("thread") or "").strip()
        level = f"reporting: {reporting_of(meta)}, {contract}"
        counts_text = status_line(restaurant)
        # The thread comes first, so a service's fence step compares it before anything else.
        lines = [f"thread {thread}" if thread else "thread not recorded",
                 level if counts_text == "nothing on record" else f"{level}, {counts_text}"]
        if not is_admin(meta):
            lines.append(workers_line(meta, running_workers(restaurant)))
        labels = read_labels(restaurant, EVERY_ROW)
        lines += status_label_lines(labels, count_labels(restaurant, labels))
        if meta.get("reportsTo"):
            lines.append(f"reports to {meta['reportsTo']}")
        if meta.get("mode"):
            lines.append(f"mode {meta['mode']}")
        if meta.get("generation") is not None:
            lines.append(f"owner {thread}@{meta['generation']}")
        return "\n".join(lines)
    if args.command == "close":
        if not args.dry_run and is_admin(restaurant.meta):
            log_rulings(restaurant)
        text, path = report(restaurant, write=not args.dry_run)
        linked = (restaurant.dir / "reports").is_symlink()
        for name, kind in unrecorded_reviews(restaurant):
            dish = name.split("-")[0]
            if kind == "file":
                print(f"brigade: warning: reports/{name} has no review row; record it with pass record {dish} --report {name}, "
                      f"and --late when {dish} has moved past that round", file=sys.stderr)
            else:
                # Under a reports/ that is a symbolic link, an entry of every kind resolves outside this store's reports/.
                kind = "elsewhere" if linked else kind
                print(f"brigade: warning: reports/{name} {NOT_A_REPORT[kind]} and no review row of {dish} names it; {REPAIR[kind]}",
                      file=sys.stderr)
        for ident, state in unclaimed_reports(restaurant):
            print(f"brigade: warning: reports/{ident}.md has no Claims check line; {ident} is {state}", file=sys.stderr)
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
