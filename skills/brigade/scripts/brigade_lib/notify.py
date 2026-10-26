"""The opt-in outside ping. The command the user sets runs once, with one plain line, when a decision needs them or work is stuck."""
import enum
import os
import re
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from brigade_lib.shared import BrigadeError, slug

KEY = "notify"
TIMEOUT_SECONDS = 10
STATUS_LINE = "outside ping: on"
REFUSED = '--notify takes a command line or ""'
LINE_CHARS = 200
SCRIPTS = Path(__file__).resolve().parents[1]
TOKEN_PREFIXES = ("sk-", "ghp", "gho", "ghs", "ghu", "github", "glpat", "xox", "akia", "bearer", "npm_")


class Trigger(enum.Enum):
    DECISION = enum.auto()
    STUCK = enum.auto()
    STATUS = enum.auto()


TAILS = {
    Trigger.DECISION: "a decision is waiting for your answer",
    Trigger.STUCK: "work hit a failure it cannot fix and needs you",
}


@dataclass(frozen=True)
class Watch:
    argv: tuple
    trigger: Trigger
    before: frozenset = frozenset()


def parse_notify(text):
    value = text.strip()
    try:
        argv = shlex.split(value)
    except ValueError:
        raise BrigadeError(REFUSED) from None
    if not argv:
        raise BrigadeError(REFUSED)
    return value


def shown(meta):
    if KEY not in meta:
        return meta
    return {**meta, KEY: "on"}


def watch(restaurant, meta, args):
    if KEY not in meta:
        return None
    try:
        argv = tuple(shlex.split(meta[KEY]))
    except ValueError:
        return None
    if args.command == "86" and args.action == "add":
        try:
            before = frozenset(row["id"] for row in restaurant.rows("86.tsv"))
        except BrigadeError:
            return None
        return Watch(argv, Trigger.DECISION, before)
    if args.command == "next" and getattr(args, "failure", None) == "stuck":
        return Watch(argv, Trigger.STUCK)
    if args.command == "status":
        return Watch(argv, Trigger.STATUS)
    return None


def finish(restaurant, watch, result):
    if watch.trigger is Trigger.STATUS:
        return f"{result}\n{STATUS_LINE}"
    if watch.trigger is Trigger.DECISION and result.strip() in watch.before:
        return result
    try:
        _send(restaurant, watch.argv, _line(restaurant, watch.trigger))
    except Exception:
        restaurant.warn("the outside ping failed")
    return result


def _privacy():
    prior = {n: m for n, m in sys.modules.items() if n == "activity_lib" or n.startswith("activity_lib.")}
    flag = sys.dont_write_bytecode
    sys.path.insert(0, str(SCRIPTS))
    sys.dont_write_bytecode = True
    try:
        from activity_lib.privacy import Privacy
        return Privacy
    finally:
        for name in [n for n in sys.modules if n == "activity_lib" or n.startswith("activity_lib.")]:
            del sys.modules[name]
        sys.modules.update(prior)
        sys.path.remove(str(SCRIPTS))
        sys.dont_write_bytecode = flag


def _secret_like(word):
    low = word.lower()
    return (len(word) > 32 or "://" in word or "www." in low or "@" in word or "_" in word or "=" in word
            or low.startswith(TOKEN_PREFIXES) or _mixed_run(word))


def _mixed_run(word):
    return any(len(run) > 16 and any(c.isalpha() for c in run) and any(c.isdigit() for c in run)
               for run in re.findall(r"[A-Za-z0-9]+", word))


def _name(privacy, text):
    if _secret_like(text) or privacy.clean(text) != text:
        return "brigade"
    try:
        name = slug(text)
    except BrigadeError:
        return "brigade"
    if _secret_like(name) or privacy.clean(name) != name:
        return "brigade"
    return name


def _line(restaurant, trigger):
    meta = restaurant.meta
    identifiers = [meta.get("thread"), meta.get("previousThread"), *(meta.get("schedules") or {}).values()]
    privacy = _privacy()(identifiers=[value for value in identifiers if value],
                         paths=(meta["projectRoot"], str(restaurant.dir)))
    name = _name(privacy, meta["restaurant"])
    text = privacy.clean(f"{name}: {TAILS[trigger]}")
    return " ".join(word for word in text.split() if not _secret_like(word))[:LINE_CHARS]


def _send(restaurant, argv, line):
    try:
        process = subprocess.Popen([*argv, line], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        restaurant.warn("the outside ping could not start")
        return
    try:
        returncode = process.wait(timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        restaurant.warn(f"the outside ping took longer than {TIMEOUT_SECONDS} seconds and was stopped")
        return
    if returncode != 0:
        restaurant.warn(f"the outside ping exited with status {returncode}")


def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    process.wait()
