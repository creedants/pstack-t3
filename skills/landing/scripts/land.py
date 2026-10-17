#!/usr/bin/env python3
"""Land many agents' work on one repository through one queue.

Each repository (one git common directory) gets a contract, a lease table
that keeps writers off each other's paths, and a queue that is the only
writer to trunk. The queue is a lock, not a process: whoever runs `land`
while holding it drains the queue. State lives in SQLite so every change is
one transaction. Heavy commands take a slot from a machine-wide governor.
"""

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

import sys as _sys
import types as _types
import uuid as _uuid
from pathlib import Path as _Path
_libdir = _Path(__file__).resolve().parent / "landing_lib"


def _drop_cached_landing(*names):
    """Drop every cached module under one of these names, so a load cannot reuse another load's copy."""
    for _n in [n for n in _sys.modules if any(n == name or n.startswith(name + ".") for name in names)]:
        del _sys.modules[_n]


_prior_landing = {n: m for n, m in _sys.modules.items() if n == "landing_lib" or n.startswith("landing_lib.")}
_drop_cached_landing("landing_lib")
_pkg = "_landing_ns_" + _uuid.uuid4().hex
_alias = _types.ModuleType(_pkg)
_alias.__path__ = [str(_libdir)]
_sys.modules[_pkg] = _alias
_sys.modules["landing_lib"] = _alias
_bw = _sys.dont_write_bytecode
_sys.dont_write_bytecode = True
try:
    from landing_lib import seam as _seam_bootstrap
    import landing_lib.foundation as _foundation_mod
    globals().update({k: v for k, v in vars(_foundation_mod).items() if not k.startswith("__") and k != "seam"})
    _seam_bootstrap.bind(globals())
    import landing_lib.governor as _governor_mod
    import landing_lib.store as _store_mod
    import landing_lib.policy as _policy_mod
    import landing_lib.leases as _leases_mod
    import landing_lib.queue as _queue_mod
    import landing_lib.merge as _merge_mod
    import landing_lib.contests as _contests_mod
    import landing_lib.drain as _drain_mod
    import landing_lib.times as _times_mod
    for _mod in (_governor_mod, _store_mod, _policy_mod, _leases_mod, _queue_mod,
                _merge_mod, _contests_mod, _drain_mod, _times_mod):
        globals().update({k: v for k, v in vars(_mod).items() if not k.startswith("__") and k != "seam"})
finally:
    _sys.dont_write_bytecode = _bw
    _drop_cached_landing("landing_lib", _pkg)
    _sys.modules.update(_prior_landing)
del _bw, _drop_cached_landing, _pkg, _alias, _libdir, _types, _uuid, _prior_landing
del _foundation_mod, _governor_mod, _store_mod, _policy_mod, _leases_mod, _queue_mod
del _merge_mod, _contests_mod, _drain_mod, _times_mod, _seam_bootstrap, _mod

def parser():
    top = argparse.ArgumentParser(prog="land.py", description=__doc__.splitlines()[0])
    top.add_argument("--repo", default=".", help="any checkout of the repository (default: current directory)")
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="write the repository's landing contract")
    p.add_argument("--trunk", required=True, help="branch on the remote, or a lane name in local mode")
    p.add_argument("--mode", choices=MODES + tuple(LEGACY_MODES), required=True,
                   help="human: PRs you merge; merge: PRs the queue merges; push: no PRs, the queue pushes trunk; local: a lane ref")
    p.add_argument("--remote", default="origin")
    p.add_argument("--base", default="", help="local mode: the commit the lane starts from")
    p.add_argument("--check", action="append", default=None,
                   help="command that must pass before landing; repeatable. Omitted, a rerun keeps the stored checks and a first init stores none")
    p.add_argument("--setup", default=None,
                   help="command run before checks in the clean integration worktree, such as npm ci. Omitted, a rerun keeps the stored setup and a first init stores none")
    p.add_argument("--batch", type=int, default=None,
                   help="entries checked together; a failed batch lands one at a time. Omitted, a rerun keeps the stored batch and a first init stores 1")
    p.add_argument("--timeout", type=int, default=None,
                   help="seconds before a check is killed. Omitted, a rerun keeps the stored timeout and a first init stores 1800")
    p.add_argument("--merge-method", choices=MERGE_METHODS, default=None,
                   help="merge mode: how the queue merges its PRs, checked against the repository when gh can report the "
                        "allowed methods, whenever it is set. "
                        "omitted in merge mode, a first init stores the only allowed method, or prefers merge then squash "
                        "when several are allowed, or merge when gh cannot say, and a rerun keeps the stored method while "
                        "the repository allows it")
    p.add_argument("--author-policy", choices=AUTHOR_POLICIES, default=None,
                   help="how the queue judges a reviewed commit's author. any skips the check. configured and credited "
                        "check only when the repository has a configured identity: configured requires that author, and "
                        "credited also lands an entry whose commits by another author each carry a Co-authored-by trailer "
                        "equal to that commit's author as name <email> and whose body has a line that reads Based on #<n> by "
                        "@<login>, optionally with a trailing period. Omitted, a rerun keeps the stored policy or stores any "
                        "when the contract has none, and a first init stores any. An unknown --author-policy value is refused "
                        "before the contract changes")
    p.add_argument("--pr-item-line", choices=("on", "off"), default=None,
                   help="whether the queue appends the Kind and Goal line to the PR body. on appends the line after one "
                        "blank line when the item has one. off sends the submitted title and body unchanged. Omitted, a rerun keeps the stored "
                        "setting and a first init stores off. An unknown value is refused before the contract changes")
    p.add_argument("--cap", type=int, help="most changes in flight on the repository at once; 0 clears it")
    p.add_argument("--generated", metavar="PATHS", default=None,
                   help="comma-separated repository-relative paths the queue treats as generated output, such as skills,docs/cli. "
                        "An empty value clears the stored list. Omitted, a rerun keeps the stored list and a first init stores none")
    p.add_argument("--rebuild", metavar="COMMAND", default=None,
                   help="command that regenerates the generated paths while landing, such as \"python3 scripts/build.py\". "
                        "An empty value clears the stored command. Omitted, a rerun keeps the stored command and a first init stores none")

    p = sub.add_parser("cap", help="set the most changes in flight on the repository at once; 0 clears it")
    p.add_argument("count", type=int)

    mode_help = "switch human, merge, or push when the queue is empty. --merge-method may change while entries are in flight"
    p = sub.add_parser("mode", help=mode_help, description=mode_help)
    p.add_argument("mode", choices=MODES + tuple(LEGACY_MODES))
    p.add_argument("--merge-method", choices=MERGE_METHODS)

    times_help = ("report submit-to-landed minutes for the last N landed entries, one line per entry then a summary line. "
                  "A line holds the entry's id, holder, PR, and minutes to one decimal, and the PR prints - when the entry has none. "
                  "The summary line is n=<count> median=<m> p90=<m> max=<m> skipped=<k>, where median and p90 are the "
                  "nearest-rank 50th and 90th percentiles. An entry in state landed with no landed log row is not a line and is "
                  "counted in skipped, even outside the --last window. --holder counts only that holder's entries. "
                  "With nothing landed the three statistics print - and the command exits 0")
    p = sub.add_parser("times", help=times_help, description=times_help)
    p.add_argument("--last", type=int, default=30, metavar="N",
                   help="report at most this many of the most recently landed entries (default: 30)")
    p.add_argument("--holder", metavar="PREFIX",
                   help="report this holder's entries; a value ending in / matches every holder under it")
    p.add_argument("--json", action="store_true",
                   help="print one JSON object instead of the lines; it holds entries, n, median, p90, max, and skipped, "
                        "an empty PR is null, and the statistics are null when nothing landed")

    owner_help = "<holder prefix>@<generation> of the coordinator writing; refused below that prefix's floor"
    ruling_help = "<prefix>@<generation> of the writer, such as .admin/@2; refused below that prefix's floor"
    p = sub.add_parser("owner", help="raise a holder prefix's generation floor; it never lowers")
    p.add_argument("--prefix", required=True, help="a holder prefix ending in /, such as docs/")
    p.add_argument("--generation", type=int, required=True)

    p = sub.add_parser("lease", help="claim, renew, release, or list path leases")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("claim")
    a.add_argument("--holder", required=True)
    a.add_argument("--paths", required=True, help="comma-separated files or directories; . is the whole repository")
    a.add_argument("--ttl-hours", type=float, default=6)
    a.add_argument("--owner", help=owner_help)
    a = t.add_parser("renew")
    a.add_argument("id")
    a.add_argument("--ttl-hours", type=float, default=6)
    a.add_argument("--if-live", action="store_true", help="refuse when the lease has expired instead of admitting it again")
    a.add_argument("--owner", help=owner_help)
    a = t.add_parser("release")
    a.add_argument("id")
    a.add_argument("--owner", help=owner_help)
    t.add_parser("list")
    a = t.add_parser("check", help="print what a claim would answer now; claims no lease and arms no reservation")
    a.add_argument("--holder", required=True)
    a.add_argument("--paths", required=True)
    a = t.add_parser("reserve", help="reserve paths for a holder prefix by a ruling; prints S<n>")
    a.add_argument("--for", dest="prefix", required=True, help="the winner's holder prefix, such as docs/")
    a.add_argument("--paths", required=True, help="comma-separated files or directories")
    a.add_argument("--ruling", required=True, help="the ruling this carries out, such as R4; a rerun returns the same S<n>")
    a.add_argument("--ttl-hours", type=float, default=2, help="hours the reservation stands once it arms")
    a.add_argument("--owner", required=True, help=ruling_help)
    a = t.add_parser("unreserve", help="lift a reservation")
    a.add_argument("id")
    a.add_argument("--owner", required=True, help=ruling_help)

    p = sub.add_parser("submit", help="queue a reviewed commit for landing")
    p.add_argument("--holder", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--sha", required=True, help="the exact SHA the review passed")
    p.add_argument("--lease", required=True)
    p.add_argument("--reviewer", required=True, help="provider/model of the reviewer that passed this SHA")
    p.add_argument("--title", default="", help="human mode: the PR title (default: the last commit subject)")
    p.add_argument("--body-file", default="", help="human mode: a file holding the PR body")
    p.add_argument("--owner", help=owner_help)

    p = sub.add_parser("share", help="set a holder prefix's share of the cap")
    p.add_argument("--for", dest="prefix", required=True, help="a holder prefix ending in /, such as docs/")
    p.add_argument("count", type=int)
    p.add_argument("--clear", action="store_true", help="remove the share; pass 0")
    p.add_argument("--owner", required=True, help=ruling_help)

    p = sub.add_parser("contest", help="hold two holders' entries, then order them; waits for a running land")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--holders", help="<a>,<b>: hold both holders' entries until settled; prints C<n>")
    g.add_argument("--settle", help="C<n>: with --first, land the first holder before the other")
    g.add_argument("--cancel", help="C<n>: remove the hold or the order")
    p.add_argument("--first", help="with --settle, the holder that lands first")
    p.add_argument("--owner", required=True, help=ruling_help)

    sub.add_parser("land", help="drain the queue unless another run holds it")
    sub.add_parser("resume", help="clear a pause after you did what its reason says")
    p = sub.add_parser("status")
    p.add_argument("id", nargs="?")
    p.add_argument("--holder", help="list this holder's entries; a value ending in / matches every holder under it")
    p.add_argument("--sha", help="with --holder, only entries at exactly this commit")
    p = sub.add_parser("slot", help="run a heavy command under a governor slot")
    p.add_argument("--exclusive", action="store_true", help="hold every slot: for benchmarks that need a quiet machine")
    p.add_argument("--slots", metavar="N", default=None,
                   help="take N worker slots in one admission. The default is 1. "
                        "A value above the governor's worker slots is refused")
    p.add_argument("--for", dest="slot_for", metavar="SECONDS", default=None,
                   help=f"cap this exclusive hold at SECONDS. The default is {EXCLUSIVE_CAP:g}. "
                        f"The maximum is {EXCLUSIVE_CAP_MAX:g}. A value over the default needs --reason")
    p.add_argument("--reason", dest="slot_reason", metavar="TEXT", default=None,
                   help="why this exclusive hold may pass the default cap. Waiters see it")
    p.add_argument("--repo", dest="slot_repo", metavar="REPO", default=None,
                   help="the repository to name in the holder record (default: current directory)")
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    return top


def run(argv):
    args = parser().parse_args(argv)
    if args.command == "slot":
        command = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
        if not command:
            raise LandError("slot needs a command after --")
        if args.exclusive:
            if args.slots is not None:
                raise LandError("--slots cannot be combined with --exclusive")
            return None, exclusive_command(
                command, args.slot_repo or args.repo, args.slot_for, args.slot_reason)
        if args.slot_for is not None or args.slot_reason is not None:
            raise LandError("--for and --reason are only for slot --exclusive")
        count = _parse_slot_count(args.slots)
        with slot(repo=args.slot_repo or args.repo, command=" ".join(command), count=count):
            return None, subprocess.run(command).returncode
    if args.command == "init":
        pr_item_line = None if args.pr_item_line is None else args.pr_item_line == "on"
        return init(args.repo, args.trunk, args.mode, args.remote, args.base, args.check, args.setup, args.batch, args.timeout,
                    args.merge_method, args.cap, args.generated, args.rebuild, args.author_policy, pr_item_line), 0
    if args.command == "times":
        return landing_times(args.repo, args.last, args.holder, args.json), 0
    with Store.for_repo(args.repo) as store:
        if args.command == "cap":
            return change_cap(store, args.count), 0
        if args.command == "owner":
            return raise_floor(store, args.prefix, args.generation), 0
        if args.command == "lease":
            if args.action == "claim":
                return lease_claim(store, args.holder, args.paths, args.ttl_hours, args.owner), 0
            if args.action == "check":
                return lease_check(store, args.holder, args.paths)
            if args.action == "list":
                return lease_list(store), 0
            if args.action == "reserve":
                return lease_reserve(store, args.prefix, args.paths, args.ruling, args.ttl_hours, args.owner), 0
            if args.action == "unreserve":
                return lease_unreserve(store, reservation_id(args.id), args.owner), 0
            number = lease_id(args.id)
            if args.action == "renew":
                return lease_renew(store, number, args.ttl_hours, args.if_live, args.owner), 0
            return lease_release(store, number, args.owner), 0
        if args.command == "mode":
            return change_mode(store, args.mode, args.merge_method), 0
        if args.command == "share":
            return change_share(store, args.prefix, args.count, args.clear, args.owner), 0
        if args.command == "contest":
            if (args.settle is None) != (args.first is None):
                raise LandError("--settle and --first go together")
            return contest(store, args.owner, args.holders, args.settle, args.first, args.cancel), 0
        if args.command == "submit":
            body = Path(args.body_file).read_text() if args.body_file else ""
            return submit(store, args.holder, args.branch, args.sha, args.lease, args.reviewer, args.title, body, args.owner), 0
        if args.command == "land":
            return land(store), 0
        if args.command == "resume":
            with store.tx() as db:
                if (store.contract.get("paused") or "").startswith(IDENTITY_PAUSE):
                    db.execute("DELETE FROM contract WHERE key = 'identity'")
                db.execute("INSERT OR REPLACE INTO contract VALUES ('paused', '\"\"')")
                db.execute("DELETE FROM contract WHERE key = 'tip'")
                store.log(db, "queue", 0, "resumed")
            return "queue resumed", 0
        if args.holder:
            if args.id:
                raise LandError("status takes an entry id or --holder, not both")
            return holder_status(store, args.holder, args.sha), 0
        if args.sha:
            raise LandError("status --sha needs --holder")
        line = status(store, args.id)
        if not args.id:
            note = generated_note(store.contract.get("generated") or [], store.contract.get("rebuild"))
            policy_note = author_policy_note(store.contract)
            item_note = pr_item_line_note(store.contract)
            text = " ".join(part for part in (note, policy_note, item_note) if part)
            if text:
                line = f"{line} {text}"
        return line, 0


def main(argv=None):
    try:
        output, code = run(sys.argv[1:] if argv is None else argv)
    except LandError as error:
        print(f"land: {error}", file=sys.stderr)
        return 1
    if output is not None:
        print(output)
    return code


if __name__ == "__main__":
    sys.exit(main())
