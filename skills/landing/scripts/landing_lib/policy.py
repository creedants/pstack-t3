import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import math
import os
import posixpath
import re
import shlex
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
from landing_lib import seam

class Infrastructure(seam.LandError):
    """A failure that is not the entry's fault. The queue pauses instead of bouncing."""


class AutoMergeStillEnabled(Infrastructure):
    """The message is the whole pause. land() stores it unchanged."""


class UncheckedTrunk(Infrastructure):
    pass


class UnverifiedMerge(Infrastructure):
    pass


class MergeNotOnTrunk(Infrastructure):
    pass


class HeadChanged(Infrastructure):
    pass


def trunk_ref(contract):
    if contract["mode"] == "local":
        return f"refs/landing/{contract['trunk']}"
    return f"refs/remotes/{contract['remote']}/{contract['trunk']}"


def allowed_merge_methods(repo):
    """The methods gh says this repository allows, or None when gh cannot say."""
    fields = ",".join(seam.MERGE_FLAGS[method] for method in seam.MERGE_METHODS)
    try:
        result = seam.gh("repo", "view", "--json", fields, cwd=repo)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    allowed = []
    for method in seam.MERGE_METHODS:
        flag = payload.get(seam.MERGE_FLAGS[method])
        if not isinstance(flag, bool):
            return None
        if flag:
            allowed.append(method)
    return tuple(allowed)


def allowed_list(allowed):
    return ", ".join(allowed) if allowed else "none"


def pick_merge_method(explicit, allowed):
    """The method init stores. When gh cannot say, an omitted method stays merge."""
    if allowed is None:
        return explicit or "merge"
    if explicit:
        if explicit not in allowed:
            raise seam.LandError(f"repository does not allow {explicit}; allowed: {allowed_list(allowed)}")
        return explicit
    if len(allowed) == 1:
        return allowed[0]
    if "merge" in allowed:
        return "merge"
    if "squash" in allowed:
        return "squash"
    raise seam.LandError(f"repository allows no merge method; allowed: {allowed_list(allowed)}")


def refused_merge_method(text):
    for method, phrase in seam.METHOD_REFUSAL:
        if phrase in (text or ""):
            return method
    return None


def suggested_merge_method(repo, problem):
    """An allowed method other than the one GitHub just refused, preferring squash."""
    refused = refused_merge_method(problem)
    if not refused:
        return None
    allowed = allowed_merge_methods(repo)
    if not allowed:
        return None
    for method in ("squash", "rebase", "merge"):
        if method in allowed and method != refused:
            return method
    return None


def busy_queue_message(count):
    noun = "entry is" if count == 1 else "entries are"
    return f"{count} {noun} queued, landing, or awaiting merge; change the mode when the queue is empty"


def init(path, trunk, mode, remote, base, checks, setup, batch, timeout, merge_method=None, cap=None):
    mode = seam.LEGACY_MODES.get(mode, mode)
    common = seam.common_dir(path)
    repo = common.parent if common.name == ".git" else Path(seam.git("rev-parse", "--show-toplevel", cwd=path).stdout.strip())
    directory = seam.state_home() / "landing" / seam.store_name(common)
    directory.mkdir(parents=True, exist_ok=True)

    def filled(stored, key, passed, default):
        if passed is not None:
            return passed
        return stored[key] if key in stored else default

    def merge_method_to_store(explicit, stored_method, mode, allowed):
        if explicit is not None:
            return pick_merge_method(explicit, allowed)
        if stored_method is None:
            return "merge" if mode != "merge" else pick_merge_method(None, allowed)
        if allowed is None or stored_method in allowed:
            return stored_method
        return pick_merge_method(None, allowed)

    stored = {}
    if (directory / "land.db").exists():
        with seam.Store(directory) as existing:
            stored = existing.contract
    stored_method = stored.get("mergeMethod")
    allowed = None
    if merge_method is not None or mode == "merge" or stored_method is not None:
        allowed = allowed_merge_methods(repo)
    # A refused method must fail before the store exists, so a first init leaves no database.
    merge_method_to_store(merge_method, stored_method, mode, allowed)
    fixed = {"commonDir": str(common), "trunk": trunk, "mode": mode, "remote": remote}

    def refuse_clash(current):
        current = {**current, "mode": seam.LEGACY_MODES.get(current.get("mode"), current.get("mode"))} if "mode" in current else current
        clash = [key for key in fixed if key in current and current[key] != fixed[key]]
        if clash:
            raise seam.LandError(f"the contract already sets {', '.join(f'{k}={current[k]!r}' for k in clash)}; "
                            "change the mode with land.py mode; changing trunk or remote needs a new repository contract")

    refuse_clash(stored)
    if mode in seam.REMOTE_MODES:
        if not seam.git("remote", "get-url", remote, cwd=repo, check=False).stdout.strip():
            raise seam.LandError(f"mode {mode} needs remote {remote!r}; use --mode local for a repository without one")
        seam.git("fetch", remote, trunk, cwd=repo)
    with seam.Store(directory) as store:
        with store.tx() as db:
            current = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT key, value FROM contract")}
            refuse_clash(current)
            wanted = {"commonDir": str(common), "repo": str(repo), "trunk": trunk, "mode": mode, "remote": remote,
                      "checks": filled(current, "checks", checks, []),
                      "setup": filled(current, "setup", setup, ""),
                      "batch": filled(current, "batch", batch, 1),
                      "timeout": filled(current, "timeout", timeout, 1800),
                      "mergeMethod": merge_method_to_store(merge_method, current.get("mergeMethod"), mode, allowed),
                      "paused": ""}
            for key, value in wanted.items():
                if key != "paused" or key not in current:
                    db.execute("INSERT OR REPLACE INTO contract VALUES (?, ?)", (key, json.dumps(value)))
            if cap is not None:
                set_cap(db, cap)
        if mode == "local" and seam.git("rev-parse", "--verify", "--quiet", trunk_ref(wanted), cwd=repo, check=False).returncode != 0:
            if not base:
                raise seam.LandError(f"local mode lands on {trunk_ref(wanted)}; pass --base <commit> to create it")
            seam.git("update-ref", trunk_ref(wanted), seam.git("rev-parse", "--verify", f"{base}^{{commit}}", cwd=repo).stdout.strip(), "", cwd=repo)
        return f"{'updated' if current else 'created'} {directory}"


def set_cap(db, cap):
    if cap < 0:
        raise seam.LandError(f"cap {cap} is negative; pass 0 to clear it")
    if cap:
        db.execute("INSERT OR REPLACE INTO contract VALUES ('cap', ?)", (json.dumps(cap),))
    else:
        db.execute("DELETE FROM contract WHERE key = 'cap'")


def change_cap(store, cap):
    with store.tx() as db:
        if cap > 0:
            rows = db.execute("SELECT prefix, count FROM share ORDER BY prefix").fetchall()
            total = sum(row["count"] for row in rows)
            if total > cap:
                listed = ", ".join(f"{row['prefix']} {row['count']}" for row in rows)
                raise seam.LandError(f"shares add up to {total} ({listed}), over the cap of {cap}")
        set_cap(db, cap)
        store.log(db, "queue", 0, f"cap {cap}")
    return f"repository cap is now {cap} changes in flight" if cap else "repository cap cleared"


def change_mode(store, mode, merge_method):
    """Switch remote modes while nothing is in flight. A merge method may change while entries wait, because it only affects the next gh pr merge."""
    mode = seam.LEGACY_MODES.get(mode, mode)
    seen = store.contract["mode"]
    if "local" in (mode, seen) and mode != seen:
        raise seam.LandError("local mode lands on refs/landing/<trunk>, not the remote trunk; switching to or from it needs a new contract")
    allowed = None
    if merge_method or mode == "merge":
        allowed = allowed_merge_methods(store.repo)
        if merge_method:
            pick_merge_method(merge_method, allowed)
    with store.tx() as db:
        # The read above can go stale while gh runs. This one decides the write.
        current = json.loads(db.execute("SELECT value FROM contract WHERE key = 'mode'").fetchone()["value"])
        current = seam.LEGACY_MODES.get(current, current)
        if "local" in (mode, current) and mode != current:
            raise seam.LandError("local mode lands on refs/landing/<trunk>, not the remote trunk; switching to or from it needs a new contract")
        busy = db.execute("SELECT count(*) FROM entry WHERE state IN ('queued', 'landing', 'awaiting-merge')").fetchone()[0]
        if busy and (mode != current or not merge_method):
            raise seam.LandError(busy_queue_message(busy))
        db.execute("INSERT OR REPLACE INTO contract VALUES ('mode', ?)", (json.dumps(mode),))
        method = merge_method
        if not method and mode == "merge" and allowed is not None:
            # Check the method stored now, not the one stored before gh ran.
            method = None if (store.contract.get("mergeMethod") or "merge") in allowed else pick_merge_method(None, allowed)
        if method:
            db.execute("INSERT OR REPLACE INTO contract VALUES ('mergeMethod', ?)", (json.dumps(method),))
        store.log(db, "queue", 0, f"mode {mode}", f"was {current}")
        holders = seam.holders_of(seam.in_flight(db))
    return (f"landing mode is now {mode}" + (f", merging with --{method}" if method else "")
            + (f"; leases held by {holders}" if holders else ""))
