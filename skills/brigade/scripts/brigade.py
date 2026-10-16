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


_prior_brigade = {n: m for n, m in _sys.modules.items() if n == "brigade_lib" or n.startswith("brigade_lib.")}
_drop_cached_brigade("brigade_lib")
_pkg = "_brigade_ns_" + _uuid.uuid4().hex
# The alias keeps the brigade_lib name so each file loads once under that name.
_alias = _types.ModuleType("brigade_lib")
_alias.__path__ = [str(_libdir)]
_sys.modules[_pkg] = _alias
_sys.modules["brigade_lib"] = _alias
_bw = _sys.dont_write_bytecode
_sys.dont_write_bytecode = True
try:
    from brigade_lib import seam as _seam_bootstrap
    from brigade_lib.compact import effective_provider, parse_context_cap, parse_coordinator_provider, status_lines, sync_meta
    from brigade_lib.briefs import ESCALATED, MEASURING_STATIONS, MODE_RESOLUTIONS, ModeInputs, ModeLines, SEAT_RULE, WAIT_RULE, brief, brief_dish, contract_file, mode_inputs, roles_mode, stored_contract, write_brief
    from brigade_lib.edits import BLANK, BLOB_MODES, Digest, EDITS_REFUSED, EDITS_SECTION, EDIT_FAILS, EDIT_MARKS, EDIT_TITLE, Edit, EditsRefused, FILE_LINE, HEADING, LABELS, NEEDS_COMMANDS, NESTED, NO_COMMAND, PATH_LINES, TreeEntry, UNDERLINE, UNREAD, apply_edits, commit_tree, compared_paths, digests, edit_mark, exit_code, git, head_digest, occurrences, one_edit, parse_edits, path_lines, replay, report_edits, report_parts
    from brigade_lib.items import ATTEMPT_AFTER, DocAllowed, DocRefused, PreparedDocFire, _CATALOG, _SharedDoc, _can_cover_catalog, _covers, _fixed_docs, _gate_inventory, _held_from_tracked, _is_skill_file, _lease_parts, _skill_roots, _walk_opaque, attempt_kind, attempt_starts, block_note, checked_fire_label, drop, fire, fire_label, item_escalation, latest_mode_note, menu_purpose, prepare_doc_fire, record_blocked, record_escalation, retired_worker_threads, unfireable, workers_full, write_lead_kind
    from brigade_lib.leases import Claim, ClaimError, ENTRY_LINE, NOT_STOPPED, STANDINGS, StandingText, autofire_of, block_holds, block_inputs, blocked_waiting, changelog_fragment, claim_lease, claim_paths, contract_mode, entry_line, fire_command, fired_line, fragment_lines, holder, holding_blocks, land_result, lease_block, lease_check, lease_states, next_owner, paths_inside, paths_overlap, recheck_blocks, refusal_kind, release_lease, renew_line, repository_cap, require_workers, running_workers, set_thread, worker_cap, workers_line
    from brigade_lib.reporting import GROUPED_SECTIONS, REPAIR, REQUEST_NOTE, SECTIONS, _ruling_note, add_ruling, admin_requests, admin_sections, counts, display_root, end_ruling, events_since, finished_by_coordinator, goal_lines, leases_for, log_rows, log_rulings, overrule, relayed, report, republish, ruling_line, section_lines, send_request, status_label_lines, status_line, sync, tally_words, unclaimed_reports, unlogged_rulings, walk
    from brigade_lib.reports import ITEM_REPORT, NOT_A_REPORT, REVIEW_FILE, in_reports, item_report, report_entry, report_stat
    from brigade_lib.reviews import CLAIMS_LINE, CLAIMS_STATES, THREADS_ITEM, THREADS_REFUSED, THREADS_REFUSED_LINE, THREADS_SECTION, cross_family, edits_gate, fix_round_refusal, item_verdicts, latest_verdict, pass_check, pass_json, record_pass, report_threads, report_threads_refusal, review_report, roles_family, round_count, round_debt, round_decision, send_backs, unrecorded_reviews, verify_edits, worker_report, write_threads
    from brigade_lib.spend import add_arguments, spend_text
    from brigade_lib.shared import BrigadeError, FENCE_OPEN, FENCE_OPEN_INDENTED, KEPT_CELLS, MalformedTable, age, clean, command_words, fence_close, fence_open, now, one_of, plural, same_text, slug, stored, threads_fence_open
    from brigade_lib.store import ADDED_COLUMNS, ADMIN_COMMANDS, ADMIN_DIR, ADMIN_LINES, ADMIN_MENU, ADMIN_NAME, AUTOFIRE, CHILD_ROLES, CLAIM_WAIT_SECONDS, CLOSED_GOAL_STATES, COUNTED_STATES, DISH_STATES, EDITS_VERDICT, GENERATED_ROOT, GOAL_MOVES, GOAL_STATES, HELD_LOCKS, HOUSE_RULES, IN_FLIGHT_STATES, KINDS, KIND_CHOICE, LEASED_STATES, LINE_SUFFIX, LIVE_TICKET_STATES, LOW_FLAG_DAYS, LOW_RESERVE, MENU, MESSAGE_ONLY, MODES, NEEDS_CONTRACT, NOTHING_HANDED, OFF_MENU_TEMPLATE, OPEN_LINES, OPEN_RUN_MINUTES, OWING_STATES, PREFIX, PRIORITIES_BY_RANK, PURPOSE_TEMPLATE, REPORTING, ROUND_BUDGET, ROUND_DECISIONS, ROUND_KIND, ROUND_PARKED, RULING_KINDS, RULING_RULES, RULING_STATES, Restaurant, SCRATCH_KEY, SNAPSHOT_CHUNK, SOURCE_PRIORITY, SOURCE_ROOT, STATION_KIND, TABLES, THREADS_WRITERS, THREAD_COMMAND_ROLES, THREAD_ROLES, TICKET_STATES, UNSENT, VERDICTS, WORKER_ROLES, WORK_COMMANDS, _read_meta, holder_prefix, intake_owner, is_admin, is_timestamp, menu_section, open_restaurant, parse_row, read_chunk, refuse_owned_intake, reporting_of, roles_script, scratch_parent_text, sibling_lines, siblings, store_path, store_root, wait_for_meta, write_atomic
    from brigade_lib.tickets import ActsOn, EVERY_ROW, Fault, FollowUps, GOALS_ONLY, Goal, Label, LabelCounts, Labels, NO_WORK, Tally, Tracked, add_goal, add_ticket, append_ticket, base_source, cap_pairs, count_labels, file_follow_ups, finish_request, finished_requests, follow_up_kind, follow_ups, frozen_label, goal_share, goal_work, handed_count, handoff_id, in_flight_tickets, inbox_file, intake_list, is_live, label_words, move_ticket, open_goal, paths_of, pattern_directory, priority_of, quoted_paths, read_goal, read_label, read_labels, record_paths, refuse_live_ref, request_files, review_pairs, says_no_work, set_goal, set_intake, sibling_named, sibling_rails, take_inbox, take_tickets, taken_row, tell, ticket_paths, tracked_paths, unique_names, whole_share
    from brigade_lib.wake import CLOSE_OCCASIONS, DRAIN_LABELS, Debt, If, NO_WORKER, OCCASION_ORDER, ROUTINE_DETAILS, Reply, Run, STARTS_AFTER, See, Snapshot, Standing, Trigger, Waiting, YES_OCCASIONS, accepted_argv, admin_sends, blocked_needs_user, budget_reached, closing_options, compile_sheet, counted_timebox, crossing_send_back, cue_line, decision_detail, decision_holds, dish_by_id, dish_progress, format_sheet, fresh_decision_ids, from_user_request, hang_attempt, held_occasions, holder_of, housekeeping, inbox_cues, inbox_ready, is_drained, land_follow, lease_refusals, load_inbox, load_snapshot, lowest_id, malformed_close, maybe_run, merge_accepted, merge_why, next_text, note_unsent, occasion_yes, older_than_hour, open_for, open_item_decisions, option_key, pass_words, pause_if, plan, prefix_cues, record_hang, record_model_clear, record_model_skip, record_worker_end, reply_for, report_is_current, request_text, require_seat, routine_detail, startable, startable_lines, tail_cues, ticket_lines, trigger_from, waiting_ticket, wake_label, watch, worker_where
    from brigade_lib.commands import BY_NAME, COMMANDS, LockedView
    from brigade_lib import seam
    seam.bind(globals())
finally:
    _sys.dont_write_bytecode = _bw
    _drop_cached_brigade("brigade_lib", _pkg)
    _sys.modules.update(_prior_brigade)
del _bw, _drop_cached_brigade, _pkg, _alias, _libdir, _types, _uuid, _prior_brigade

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
    for item in COMMANDS:
        item.register(sub)
    return top


def run(argv):
    args = parser().parse_args(argv)
    root = store_root(args.store)
    if args.command in ("open", "walk"):
        return BY_NAME[args.command].handle(args, root)
    if not args.at:
        raise BrigadeError("pass --at <restaurant dir> or set BRIGADE_DIR")
    restaurant = Restaurant(args.at, args.owner)
    admin = is_admin(restaurant.meta)
    if admin and args.command in WORK_COMMANDS:
        raise BrigadeError("the executive admin routes work and never runs it")
    if admin and args.command == "model":
        raise BrigadeError("model works only in a coordinator's store")
    if not admin and args.command in ADMIN_COMMANDS:
        raise BrigadeError(f"{args.command} works only in the executive admin's store")
    return BY_NAME[args.command].handle(restaurant, args)


def command(restaurant, args, contract=None, rails=None):
    item = BY_NAME.get(args.command)
    if item is None or item.handle_locked is None:
        raise BrigadeError(f"unknown command {args.command}")
    return item.handle_locked(restaurant, args, LockedView(contract, rails))


def main(argv=None):
    try:
        print(run(sys.argv[1:] if argv is None else argv))
    except BrigadeError as error:
        print(f"brigade: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
