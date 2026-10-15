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
from brigade_lib.compact import status_lines
from brigade_lib.leases import contract_mode, running_workers, workers_line
from brigade_lib.reporting import REPAIR, log_rulings, report, reporting_of, status_label_lines, status_line, unclaimed_reports, walk
from brigade_lib.reports import NOT_A_REPORT
from brigade_lib.reviews import unrecorded_reviews
from brigade_lib.shared import BrigadeError
from brigade_lib.store import is_admin
from brigade_lib.tickets import EVERY_ROW, count_labels, read_labels

COMMANDS = ("status", "close", "walk")


def register_status(sub):
    sub.add_parser("status", help="the thread line first, then counts, then workers outside the executive admin's store, "
                                  "then a kinds line, a goals line, and an untagged line when each has something to count, "
                                  "then reports to, mode, and owner when present")


def register_close(sub):
    p = sub.add_parser("close", help="write the report of what changed since the last one")
    output = p.add_mutually_exclusive_group()
    output.add_argument("--dry-run", action="store_true")
    output.add_argument("--to-file", action="store_true",
                        help="print only the path of the written report, not its text")


def register_walk(sub):
    p = sub.add_parser("walk", help="every restaurant's counts and open decisions, grouped by repository")
    p.add_argument("--stale-hours", type=float, default=24)
    p.add_argument("--repo", help="print only this repository")


def register(sub):
    register_status(sub)
    register_close(sub)
    register_walk(sub)


def handle_walk(args, root):
    text, failures = walk(root, args.stale_hours, args.repo)
    if failures:
        print(text)
        raise BrigadeError("\nbrigade: ".join(failures))
    return text


def handle_status(restaurant, args):
    contract = contract_mode(restaurant.meta["projectRoot"])
    with restaurant.checked():
        return seam.command(restaurant, args, contract)


def handle(restaurant, args):
    if args.command == "status":
        return handle_status(restaurant, args)
    if args.command == "close":
        with restaurant.checked():
            return seam.command(restaurant, args)
    return None


def handle_locked(restaurant, args, view):
    if args.command == "status":
        contract = view.contract
        meta = restaurant.meta
        thread = (meta.get("thread") or "").strip()
        level = f"reporting: {reporting_of(meta)}, {contract}"
        counts_text = status_line(restaurant)
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
        lines.extend(status_lines(meta))
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
                kind = "elsewhere" if linked else kind
                print(f"brigade: warning: reports/{name} {NOT_A_REPORT[kind]} and no review row of {dish} names it; {REPAIR[kind]}",
                      file=sys.stderr)
        for ident, state in unclaimed_reports(restaurant):
            print(f"brigade: warning: reports/{ident}.md has no Claims check line; {ident} is {state}", file=sys.stderr)
        if args.to_file:
            return str(path.resolve())
        return text
    return None
