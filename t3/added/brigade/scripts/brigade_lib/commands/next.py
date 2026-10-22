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
from brigade_lib.briefs import house_rules_cap_lines
from brigade_lib.shared import BrigadeError
from brigade_lib.store import UNSENT, VERDICTS
from brigade_lib.wake import next_text

COMMANDS = ("next",)


def register_next(sub):
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


def register(sub):
    register_next(sub)


def handle(restaurant, args):
    if args.command == "next":
        text = next_text(restaurant, args)
        for line in house_rules_cap_lines(restaurant):
            restaurant.warn(line)
        return text
    return None


def handle_locked(restaurant, args, view):
    return None
