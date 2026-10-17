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
from brigade_lib.shared import BrigadeError
from brigade_lib.wake import record_hang, record_model_clear, record_model_skip, startable, watch

COMMANDS = ("watch", "startable", "hang", "model")


def register_watch(sub):
    sub.add_parser("watch", help="liveness: which dishes have reports, are running, or are over their timebox")


def register_startable(sub):
    sub.add_parser("startable", help="waiting tickets in the order they should start, each with why it can or cannot "
                                     "start now; a low ticket that could otherwise start and does not ride a "
                                     "higher ticket's paths is held back while an urgent or normal ticket can start "
                                     "now and the workers still idle are at most lowReserve, the restaurant.json "
                                     "setting that reads as 1 when absent and holds no low ticket back at 0; it "
                                     "starts nothing")


def register_hang(sub):
    p = sub.add_parser("hang", help="record one open run after report-back, once per attempt")
    p.add_argument("id")
    p.add_argument("--attempt", type=int, help="an earlier attempt, counted from 1, whose run was left open")
    p.add_argument("--provider", required=True)
    p.add_argument("--minutes", type=int, required=True)


def register_model(sub):
    p = sub.add_parser("model", help="record a provider/model seat as skipped, or clear it after a smoke test")
    t = p.add_subparsers(dest="action", required=True)
    a = t.add_parser("skip", help="record a provider/model seat as skipped until model clear records a passing smoke test")
    a.add_argument("model", help="the seat as provider/model")
    a.add_argument("--cause", choices=("rate-limit", "failed"), required=True)
    a = t.add_parser("clear", help="record a passing smoke test and clear that seat")
    a.add_argument("model", help="the seat as provider/model")


def register(sub):
    register_watch(sub)
    register_startable(sub)
    register_hang(sub)
    register_model(sub)


def handle(restaurant, args):
    if args.command == "watch":
        return watch(restaurant)
    if args.command == "startable":
        return startable(restaurant)
    if args.command in ("hang", "model"):
        with restaurant.checked():
            return seam.command(restaurant, args)
    return None


def handle_locked(restaurant, args, view):
    if args.command == "hang":
        return record_hang(restaurant, args.id, args.provider, args.minutes, args.attempt)
    if args.command == "model":
        if args.action == "skip":
            return record_model_skip(restaurant, args.model, args.cause)
        return record_model_clear(restaurant, args.model)
    return None
