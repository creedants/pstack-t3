import json
import math
import os
import re
import runpy
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

from brigade_lib.reviews import item_verdicts, send_backs
from brigade_lib.shared import BrigadeError, age
from brigade_lib.store import PREFIX, WORKER_ROLES
from brigade_lib._spend.model import MODEL_NAME, NATIVE_ID, _Bare, _Call, _Counts, _add

def _claude_base():
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured is not None and configured.strip() != "":
        return Path(configured)
    return Path.home() / ".claude"


def _contained(path, root):
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _claude_totals(native):
    if not isinstance(native, str) or NATIVE_ID.fullmatch(native) is None:
        return None
    projects = _claude_base() / "projects"
    try:
        children = list(projects.iterdir()) if projects.is_dir() else []
        sessions = [
            child / f"{native}.jsonl"
            for child in children
            if child.is_dir() and (child / f"{native}.jsonl").is_file()
        ]
    except OSError:
        return None
    if len(sessions) != 1:
        return None
    session = sessions[0]
    files = [session]
    subagents = session.with_suffix("") / "subagents"
    try:
        if subagents.is_dir():
            files.extend(sorted(
                (path for path in subagents.iterdir() if path.is_file() and path.suffix == ".jsonl"),
                key=lambda path: path.name,
            ))
    except OSError:
        return None
    if any(not _contained(path, projects) for path in files):
        return None
    try:
        calls = [call for path in files for call in _read_log(path)]
        kept = _dedupe(calls)
    except _Bare:
        return None
    if not kept:
        return None
    totals = {}
    for call in kept.values():
        counts = _Counts(call.incoming, call.cache_read, call.cache_write, call.outgoing, call.reasoning)
        totals[call.model] = _add(totals[call.model], counts) if call.model in totals else counts
    return totals


def _read_log(path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise _Bare() from None
    calls = []
    for line in text.splitlines():
        if line.strip() == "":
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            raise _Bare() from None
        call = _call_from(obj)
        if call is not None:
            calls.append(call)
    return calls


def _usage_count(usage, key, required):
    if key not in usage:
        return None if required else 0
    value = usage[key]
    if type(value) is not int or value < 0:
        return None
    return value


def _thinking_tokens(usage):
    details = usage.get("output_tokens_details")
    if not isinstance(details, dict):
        return None
    value = details.get("thinking_tokens")
    if type(value) is not int or value < 0:
        return None
    return value


def _call_from(obj):
    if not isinstance(obj, dict):
        return None
    message = obj.get("message")
    if not isinstance(message, dict):
        return None
    if obj.get("type") != "assistant" and message.get("role") != "assistant":
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    ident = message.get("id")
    model = message.get("model")
    incoming = _usage_count(usage, "input_tokens", True)
    outgoing = _usage_count(usage, "output_tokens", True)
    cache_read = _usage_count(usage, "cache_read_input_tokens", False)
    cache_write = _usage_count(usage, "cache_creation_input_tokens", False)
    if not isinstance(ident, str) or ident == "" or None in (incoming, outgoing, cache_read, cache_write):
        raise _Bare()
    if not isinstance(model, str):
        model = ""
    if MODEL_NAME.fullmatch(model) is None:
        if (incoming, outgoing, cache_read, cache_write) == (0, 0, 0, 0):
            return None
        raise _Bare()
    return _Call(ident, model, incoming, cache_read, cache_write, outgoing, _thinking_tokens(usage))


def _dedupe(calls):
    found = {}
    for call in calls:
        previous = found.get(call.ident)
        if previous is None or call.outgoing > previous.outgoing:
            found[call.ident] = call
            continue
        if call.outgoing == previous.outgoing and call != previous:
            raise _Bare()
    return found


