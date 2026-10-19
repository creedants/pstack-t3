import json
import math
import os
import time
from collections import namedtuple
from landing_lib import seam

def governor_dir():
    return seam.state_home() / "governor"

def governor_slots():
    config = governor_dir() / "governor.json"
    if config.exists():
        return max(1, int(json.loads(config.read_text()).get("slots", 1)))
    return max(1, min(4, (os.cpu_count() or 4) // 4))

def exclusive_max():
    """The longest exclusive hold in seconds. The governor exclusiveMax, or the built-in maximum when absent."""
    try:
        maximum = seam.EXCLUSIVE_CAP_MAX
    except AttributeError:
        maximum = 7200
    config = governor_dir() / "governor.json"
    if not config.exists():
        return float(maximum)
    try:
        payload = json.loads(config.read_text())
    except (OSError, ValueError):
        raise seam.LandError("governor exclusiveMax must be a positive number of seconds")
    if not isinstance(payload, dict) or "exclusiveMax" not in payload:
        return float(maximum)
    value = payload["exclusiveMax"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise seam.LandError("governor exclusiveMax must be a positive number of seconds")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise seam.LandError("governor exclusiveMax must be a positive number of seconds")
    if not math.isfinite(number) or number <= 0:
        raise seam.LandError("governor exclusiveMax must be a positive number of seconds")
    try:
        floor = seam.EXCLUSIVE_CAP
    except AttributeError:
        floor = 600
    if number < floor:
        raise seam.LandError(f"governor exclusiveMax must be at or above {floor:g} seconds")
    return number

REPORT_AFTER = 15

REPORT_EVERY = 60

EXCLUSIVE_CAP = 600

EXCLUSIVE_CAP_GRACE = 5

EXCLUSIVE_CAP_MAX = 7200

EXCLUSIVE_CAP_STATUS = 124

EXCLUSIVE_REASON_MAX = 80

_Holder = namedtuple("_Holder", "kind pid repo started command cap reason")

_UNKNOWN = _Holder(None, None, None, None, None, None, None)

_Admission = namedtuple("_Admission", "handles records acquired names")

_Demand = namedtuple("_Demand", "kind count")

_Hold = namedtuple("_Hold", "acquired names")

def _short_command(command):
    """The wrapped command's first 80 characters, with CR and LF flattened to spaces."""
    if not command:
        return None
    return command[:80].replace("\r\n", " ").replace("\r", " ").replace("\n", " ") or None

def _repo_text(repo):
    """The repository as an absolute path string, or None when the path cannot be resolved.

    A repo of None selects the working directory.
    """
    try:
        return os.path.abspath(repo or os.getcwd())
    except (OSError, ValueError, TypeError):
        return None

def _finite(value):
    """True for a start time a record may hold and formatting can use."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False

def _short_reason(text):
    """One line of reason text, or None.

    Keeps characters for which str.isprintable() is true, strips the ends,
    and cuts to EXCLUSIVE_REASON_MAX. Reads that limit at call time.
    Tabs, newlines, and other non-printable characters are deleted.
    """
    if not isinstance(text, str):
        return None
    kept = "".join(character for character in text if character.isprintable()).strip()
    return kept[:EXCLUSIVE_REASON_MAX] or None

def _cap_seconds(value):
    """A positive finite cap in seconds, or None. A bool is not a cap."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number

_cap_monotonic = time.monotonic

_real_sleep = time.sleep

def _exclusive_ready_ack():
    """Run once the command starts, after the slots are locked and the holder records are written.
    Production does nothing. A test swaps in a function that marks a ready file, so the signal test can prove the supervisor reached the hook just before its protected wait before it signals."""
    return None

_BUILD_DRAIN_GRACE = 5.0

_ARMED = "armed"

_STARTED = "started"

_GROUP_KILLED = "group-killed"

_REAPED = "reaped"

_RESTORED = "restored"
