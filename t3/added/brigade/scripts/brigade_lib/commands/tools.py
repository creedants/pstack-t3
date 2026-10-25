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
from brigade_lib._reviews.threads import cooldown_seats, moment_of
# dish.py imports brief_mode from this module while it loads, so this binds the module object that
# import already made, and reads its backup runner and seat check once it is complete.
from brigade_lib.commands._items import dish as _dish
from brigade_lib.shared import BrigadeError
from brigade_lib.spend import add_arguments, spend_text
from brigade_lib.store import roles_script

COMMANDS = ("launch", "spend")


def launch_role(station):
    """The code-delegate role for one station. The map lives only here."""
    roles = {
        "bug-fix": "bug-fix",
        "perf-issue": "perf-issue",
        "hillclimb": "hillclimb",
        "feature": "feature, refactoring",
        "refactoring": "feature, refactoring",
    }
    try:
        return roles[station]
    except KeyError:
        raise BrigadeError(f"{station} has no code-delegate role") from None


def brief_mode(ident, text):
    """The one Mode line in a brief, or None when the brief names none."""
    if text is None:
        return None
    found = [line.removeprefix("Mode: ") for line in text.splitlines() if line.startswith("Mode: ")]
    if not found:
        return None
    if len(found) != 1 or found[0] not in ("full", "light"):
        raise BrigadeError(f"briefs/{ident}.md needs one Mode line of full or light")
    return found[0]


def selection_from_show(payload, role):
    """The modelSelection object for one role. payload is the roles.py show JSON object."""
    roles = payload.get("roles") if isinstance(payload, dict) else None
    if not isinstance(roles, dict) or role not in roles:
        raise BrigadeError(f"{role} is missing from roles.py show")
    entry = roles[role]
    seats = entry.get("seats") if isinstance(entry, dict) else None
    if isinstance(seats, str):
        raise BrigadeError(f"{role} seats are {seats}, not one seat")
    if not isinstance(seats, list) or len(seats) != 1:
        count = len(seats) if isinstance(seats, list) else 0
        raise BrigadeError(f"{role} has {count} seats")
    seat = seats[0]
    if isinstance(seat, str):
        raise BrigadeError(f"{role} seats are {seat}, not one seat")
    provider = seat.get("providerInstanceId") if isinstance(seat, dict) else None
    model = seat.get("model") if isinstance(seat, dict) else None
    if not isinstance(provider, str) or not isinstance(model, str):
        raise BrigadeError(f"{role} seat lacks providerInstanceId or model")
    selected = {"instanceId": provider, "model": model, "options": {}}
    if "options" in seat:
        if not isinstance(seat["options"], dict):
            raise BrigadeError(f"{role} seat options are not an object")
        selected["options"] = seat["options"]
    return selected


def catalog_refusal(path):
    """Explain a nonzero exit for a directory, a stat error, non-UTF-8 bytes, JSON that raises RecursionError or ValueError when parsed, or JSON that is not an object. Return None for any other path."""
    try:
        if Path(path).is_dir():
            return f"{path} is a directory"
    except OSError as error:
        return f"roles.py show failed on {path}: {type(error).__name__}: {error}"
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return f"{path} is not UTF-8 text"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    except (RecursionError, ValueError) as error:
        return f"roles.py show failed on {path}: {type(error).__name__}: {error}"
    if not isinstance(data, dict):
        return f"{path} is JSON that is not an object"
    return None


def show_failure(catalog, code, stderr):
    reason = ""
    for line in stderr.splitlines():
        if line.strip():
            reason = line.strip()
    had = reason.startswith("error: ")
    rest = reason[len("error: "):] if had else reason
    if not rest or "Traceback" in rest or re.search(r'File ".+", line \d+, in ', rest):
        return f"roles.py show failed on {catalog}: exit {code} with no message"
    if had:
        return "roles.py show refused the seat: " + rest
    return f"roles.py show failed on {catalog}: {reason}"


def show_payload(catalog, argv):
    try:
        result = subprocess.run(argv, capture_output=True)
    except (OSError, subprocess.SubprocessError, ValueError, RecursionError, UnicodeDecodeError) as error:
        raise BrigadeError(f"roles.py show failed on {catalog}: {type(error).__name__}: {error}") from None
    try:
        out_text = result.stdout.decode("utf-8")
        err_text = result.stderr.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BrigadeError(f"roles.py show failed on {catalog}: {type(error).__name__}: {error}") from None
    if result.returncode == 0:
        try:
            payload = json.loads(out_text)
        except json.JSONDecodeError:
            raise BrigadeError("roles.py show printed no JSON object") from None
        except (RecursionError, ValueError) as error:
            raise BrigadeError(f"roles.py show failed on {catalog}: {type(error).__name__}: {error}") from None
        if not isinstance(payload, dict):
            raise BrigadeError("roles.py show printed no JSON object")
        return payload
    refusal = catalog_refusal(catalog)
    if refusal:
        raise BrigadeError(refusal)
    raise BrigadeError(show_failure(catalog, result.returncode, err_text))


def launch_backup(project_root, args, role, mode, selected, live):
    """The roles.py backup payload for a launch whose resolved seat's provider is cooling.

    roles.py may run git, so this runs outside the store lock. The argument shape and the exclusion
    list match the usage-limit path. --resume keeps backup from reading launch's empty text as not a
    usage limit, and the failed seat's provider is out, so it falls through to the role's backups.
    """
    argv = [sys.executable, str(roles_script()), "backup", "--cwd", project_root, "--catalog", args.catalog,
            "--parent", args.parent, "--role", role,
            "--provider", selected["instanceId"], "--model", selected["model"]]
    if selected["options"]:
        argv += ["--options", json.dumps(selected["options"])]
    # roles.py reads --out as a provider, so one provider with two cooling seats is passed once.
    for name in dict.fromkeys(entry[0].partition("/")[0] for entry in live):
        argv += ["--out", name]
    if mode is not None:
        argv += ["--brief-mode", mode]
    return _dish.backup_payload(argv + ["--resume"], args.catalog, "")


def launch_text(restaurant, args):
    """One JSON object for t3_thread_launch modelSelection. The command writes nothing to stdout but the object."""
    if seam.HELD_LOCKS:
        raise BrigadeError(f"roles.py may run git; release {seam.HELD_LOCKS[0]}'s store lock before resolving the mode")
    ident = args.dish
    with restaurant.read_lock(create=False):
        _, dish = restaurant.find("dishes.tsv", ident)
        station = dish["station"]
        project_root = restaurant.meta["projectRoot"]
        brief_path = restaurant.dir / "briefs" / f"{ident}.md"
        brief_bytes = brief_path.read_bytes() if brief_path.is_file() else None
        live = cooldown_seats(restaurant, moment_of(seam.now()))
    if brief_bytes is None:
        text = None
    else:
        try:
            text = brief_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise BrigadeError(f"briefs/{ident}.md is not UTF-8 text") from None
    role = launch_role(station)
    mode = brief_mode(ident, text)
    argv = [sys.executable, str(roles_script()), "show", "--cwd", project_root, "--catalog", args.catalog,
            "--parent", args.parent, "--role", role]
    if mode is not None:
        argv += ["--brief-mode", mode]
    payload = show_payload(args.catalog, argv)
    selected = selection_from_show(payload, role)
    # A cooldown covers every model on its provider, because the usage-limit path excludes by provider.
    cooling = [entry for entry in live if entry[0].partition("/")[0] == selected["instanceId"]]
    if not cooling:
        return json.dumps(selected)
    own = f"{selected['instanceId']}/{selected['model']}"
    seat, end, _ = next((entry for entry in cooling if entry[0] == own), cooling[0])
    backup = launch_backup(project_root, args, role, mode, selected, live)
    if backup.get("decision") != "relaunch":
        earliest = min(live, key=lambda entry: moment_of(entry[1]))[1]
        raise BrigadeError(f"{ident}: {seat} cooling until {end}; no seat is runnable before {earliest}")
    replacement = backup.get("seat")
    words = _dish.seat_word(replacement)
    print(f"{ident}: {seat} cooling until {end}; launch on {words}", file=sys.stderr)
    return json.dumps(selection_from_show({"roles": {role: {"seats": [replacement]}}}, role))


def register_launch(sub):
    p = sub.add_parser(
        "launch",
        help=(
            "print one JSON object for the t3_thread_launch modelSelection of this dish. The command does not change the store.\n"
            "\n"
            "The object always names instanceId, model, and options. A seat with no options object prints an empty options object. "
            "providerInstanceId is renamed to instanceId. "
            "A brief with one Mode line of full or light is passed as --brief-mode. "
            "bug-fix, perf-issue, and hillclimb use that role name. "
            "feature and refactoring use the role \"feature, refactoring\". "
            "investigation has no code-delegate role and the command exits 1.\n"
            "\n"
            "When the resolved seat's provider has a live cooldown, roles.py backup excludes every cooling provider while choosing a replacement. "
            "A replacement object goes to stdout, and one line naming the cooling seat and its end goes to stderr. "
            "The command exits 1 when the role is missing, seats are a string, or the seat count is not one. "
            "It exits 1 when the seat lacks providerInstanceId or model, and when options are not an object. "
            "With a cooling resolved provider and no runnable backup, it exits 1 and names the earliest cooldown end. "
            "The refusal is printed on stderr."
        ),
    )
    p.add_argument("dish", help="dish id, such as D1")
    p.add_argument("--parent", required=True, help="this thread's provider/model")
    p.add_argument("--catalog", required=True, help="orchestrator_capabilities JSON for roles.py show")


def register_spend(sub):
    add_arguments(sub)


def register(sub):
    register_launch(sub)
    register_spend(sub)


def handle(restaurant, args):
    if args.command == "spend":
        return spend_text(restaurant, args)
    if args.command == "launch":
        return launch_text(restaurant, args)
    return None


def handle_locked(restaurant, args, view):
    return None
