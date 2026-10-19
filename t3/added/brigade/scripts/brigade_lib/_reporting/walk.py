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
from contextlib import contextmanager, redirect_stderr
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from brigade_lib import seam, spend
from brigade_lib.leases import holding_blocks
from brigade_lib.reviews import CLAIMS_LINE, CLAIMS_STATES
from brigade_lib.shared import BrigadeError, MalformedTable, clean, plural
from brigade_lib.store import CLOSED_GOAL_STATES, KINDS, Restaurant, holder_prefix, is_admin, parse_row, reporting_of, siblings, store_path
from brigade_lib.tickets import EVERY_ROW, Label, Tally, finished_requests, handed_count, intake_list, read_labels, request_files, sibling_named, tell
from brigade_lib._reporting.status import status_line

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


