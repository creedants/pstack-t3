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
from brigade_lib.reporting import add_ruling, end_ruling, log_rulings, overrule, republish, ruling_line, send_request, sync
from brigade_lib.shared import BrigadeError, clean
from brigade_lib.store import RULING_KINDS, RULING_RULES, RULING_STATES

COMMANDS = ("request", "rule", "sync")


def register_request(sub):
    p = sub.add_parser("request", help="executive admin: publish a request line into a coordinator's inbox")
    p.add_argument("line", nargs="?")
    p.add_argument("--to", help="the coordinator's directory name")
    p.add_argument("--republish", action="store_true", help="publish again each request a crash left unwritten")


def register_rule(sub):
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


def register_sync(sub):
    sub.add_parser("sync", help="executive admin: copy each coordinator's new log rows into this log")


def register(sub):
    register_request(sub)
    register_rule(sub)
    register_sync(sub)


def handle(restaurant, args):
    if args.command == "sync":
        text, failures = sync(restaurant)
        if failures:
            if text:
                print(text)
            raise BrigadeError("\nbrigade: ".join(failures))
        return text or "nothing new"
    if args.command == "request" and args.republish:
        if args.to or args.line:
            raise BrigadeError("request --republish takes no --to or line")
        finished = seam.finished_by_coordinator(restaurant)
        with restaurant.checked():
            return republish(restaurant, finished)
    if args.command in ("request", "rule"):
        with restaurant.checked():
            return seam.command(restaurant, args)
    return None


def handle_locked(restaurant, args, view):
    if args.command == "request":
        if not args.to or args.line is None:
            raise BrigadeError('request needs --to <coordinator> "<line>", or --republish')
        return send_request(restaurant, args.to, args.line)
    if args.command == "rule":
        if args.action != "list":
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
    return None
