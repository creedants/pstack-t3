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
from brigade_lib.commands.once import OncePaths
from brigade_lib.commands import admin as _admin
from brigade_lib.commands import items as _items
from brigade_lib.commands import liveness as _liveness
from brigade_lib.commands import next as _next
from brigade_lib.commands import rail as _rail
from brigade_lib.commands import report as _report
from brigade_lib.commands import reviews as _reviews
from brigade_lib.commands import setup as _setup
from brigade_lib.commands import tools as _tools


@dataclass(frozen=True)
class Command:
    name: str
    register: object
    handle: object
    handle_locked: object


@dataclass
class LockedView:
    contract: object = None
    rails: object = None


COMMANDS = (
    Command("open", _setup.register_open, _setup.handle_open, None),
    Command("set", _setup.register_set, _setup.handle_set, _setup.handle_locked),
    Command("ticket", _rail.register_ticket, _rail.handle_ticket, _rail.handle_locked),
    Command("goal", _rail.register_goal, _rail.handle, _rail.handle_locked),
    Command("fire", _items.register_fire, _items.handle_fire, None),
    Command("brief", _items.register_brief, _items.handle_brief, None),
    Command("contract", _items.register_contract, _items.handle, _items.handle_locked),
    Command("watch", _liveness.register_watch, _liveness.handle, None),
    Command("startable", _liveness.register_startable, _liveness.handle, None),
    Command("hang", _liveness.register_hang, _liveness.handle, _liveness.handle_locked),
    Command("model", _liveness.register_model, _liveness.handle, _liveness.handle_locked),
    Command("thread", _reviews.register_thread, _reviews.handle, _reviews.handle_locked),
    Command("dish", _items.register_dish, _items.handle_dish, _items.handle_locked),
    Command("pass", _reviews.register_pass, _reviews.handle, _reviews.handle_locked),
    Command("edits", _reviews.register_edits, _reviews.handle, None),
    Command("86", _reviews.register_86, _reviews.handle, _reviews.handle_locked),
    Command("inbox", _rail.register_inbox, _rail.handle, _rail.handle_locked),
    Command("request", _admin.register_request, _admin.handle, _admin.handle_locked),
    Command("rule", _admin.register_rule, _admin.handle, _admin.handle_locked),
    Command("sync", _admin.register_sync, _admin.handle, None),
    Command("status", _report.register_status, _report.handle_status, _report.handle_locked),
    Command("close", _report.register_close, _report.handle, _report.handle_locked),
    Command("walk", _report.register_walk, _report.handle_walk, None),
    Command("next", _next.register_next, _next.handle, None),
    Command("launch", _tools.register_launch, _tools.handle, None),
    Command("spend", _tools.register_spend, _tools.handle, None),
)

BY_NAME = {item.name: item for item in COMMANDS}
