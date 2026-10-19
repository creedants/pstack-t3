"""Re-exports names from _wake. Patch a name in the _wake module that reads it; patching it here does not reach callers in _wake."""
from brigade_lib._wake.cues import admin_sends, compile_sheet, cue_line, format_sheet, holder_of, housekeeping, inbox_cues, land_follow, merge_why, older_than_hour, pass_words, pause_if, prefix_cues, tail_cues
from brigade_lib._wake.hangs import hang_attempt, record_hang, ticket_lines
from brigade_lib._wake.occasions import CLOSE_OCCASIONS, DRAIN_LABELS, OCCASION_ORDER, ROUTINE_DETAILS, YES_OCCASIONS, blocked_needs_user, budget_reached, decision_detail, decision_holds, fresh_decision_ids, from_user_request, held_occasions, is_drained, merge_accepted, note_unsent, occasion_yes, reply_for, request_text, routine_detail, wake_label
from brigade_lib._wake.snapshot import Debt, If, Reply, Run, See, Snapshot, Trigger, _trunk_cues, _trunk_red, accepted_argv, counted_timebox, crossing_send_back, dish_by_id, inbox_ready, load_inbox, load_snapshot, lowest_id, malformed_close, maybe_run, open_for
from brigade_lib.startable import NO_WORKER, STARTS_AFTER, Standing, Waiting, lease_refusals, plan, startable, startable_lines, waiting_ticket
from brigade_lib.watch import _latest_trunk, closing_options, dish_progress, open_item_decisions, option_key, record_model_clear, record_model_skip, record_worker_end, report_is_current, require_seat, watch, worker_where
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
from brigade_lib.items import attempt_starts
from brigade_lib.reviews import item_verdicts, pass_check, round_count, round_debt, unrecorded_reviews
from brigade_lib.shared import BrigadeError, MalformedTable, clean, slug
from brigade_lib.store import MESSAGE_ONLY, ROUND_KIND, TABLES, reporting_of, round_budget_of, store_path
from brigade_lib.tickets import EVERY_ROW, label_words, priority_of, read_labels, request_files

def trigger_from(args):
    return Trigger(args.wake or "unspecified", getattr(args, "dish", "") or "", getattr(args, "request", "") or "",
                   getattr(args, "verdict", "") or "", getattr(args, "result", "") or "", bool(getattr(args, "question", False)))


def next_text(restaurant, args):
    """Print the steps this wake owes and one reply. The flag makes a write or land.py raise."""
    seam.set("READ_ONLY", True)
    try:
        trigger = trigger_from(args)
        failure = getattr(args, "failure", None)
        unsent = getattr(args, "unsent", None)
        with restaurant.read_lock(create=False):
            block = restaurant.owner_block()
            if block:
                raise BrigadeError(block)
            if trigger.dish:
                restaurant.find("dishes.tsv", trigger.dish)
            if trigger.request:
                pending = {path.stem for path in request_files(restaurant)[0]}
                if trigger.request not in pending:
                    raise BrigadeError(f"no request {trigger.request} in the inbox")
            snapshot = load_snapshot(restaurant, datetime.now(timezone.utc))
        reply = reply_for(snapshot, trigger, failure, unsent)
        return format_sheet(snapshot, trigger, compile_sheet(snapshot, trigger, failure, unsent), reply)
    finally:
        seam.set("READ_ONLY", False)



