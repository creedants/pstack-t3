import os
import posixpath
import re
from pathlib import Path
from brigade_lib import seam
from brigade_lib._store.vocab import COOLDOWN_MINUTES, LOW_RESERVE, ROUND_BUDGET
from brigade_lib.compact import refuse_setting
from brigade_lib.shared import BrigadeError

SCRATCH_KEY = "scratchDir"


SCRATCH_REFUSAL = "--scratch-dir takes an absolute directory path outside /tmp, or empty string to clear it"


LOW_RESERVE_KEY = "lowReserve"


ROUND_BUDGET_KEY = "roundBudget"


LIVENESS_KEY = "livenessMs"


LIVENESS_FLOOR = 60000


CHANGE_NOTE_KEY = "changeNoteDir"


COOLDOWN_KEY = "cooldownMinutes"


CHANGE_NOTE_REFUSAL = ("it is not a repository-relative directory path with no leading slash, "
                       "no .., no comma, and no whitespace")


LIVENESS_DEFAULT_MS = {"every-turn": 600000, "milestones": 600000, "digest": 1800000}


def store_root(flag=None):
    if flag:
        return Path(flag)
    if os.environ.get("BRIGADE_STORE"):
        return Path(os.environ["BRIGADE_STORE"])
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "pstack-t3" / "brigade"


def scratch_parent_text(value):
    """The parent directory briefs pass to mktemp -d -p, or empty string when the setting is unset.

    None, empty string, and whitespace-only mean unset. Any other value must be an absolute path outside /tmp, and an existing path must be a directory.
    """
    if value is None:
        return ""
    text = str(value)
    if not text.strip():
        return ""
    resolved = Path(text).resolve() if Path(text).is_absolute() else None
    tmp = Path("/tmp").resolve()
    refused = (any(bad in text for bad in ("\n", "\r", "~")) or resolved is None
               or resolved == Path("/tmp") or Path("/tmp") in resolved.parents
               or resolved == tmp or tmp in resolved.parents
               or (resolved.exists() and not resolved.is_dir()))
    if refused:
        raise BrigadeError(SCRATCH_REFUSAL)
    return str(resolved)


def low_reserve_of(meta):
    """The low-priority worker reserve this store enforces.

    A missing key takes LOW_RESERVE. A present value that is a boolean, is not an integer, or is negative is refused.
    """
    if LOW_RESERVE_KEY not in meta:
        return LOW_RESERVE
    value = meta[LOW_RESERVE_KEY]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        refuse_setting(LOW_RESERVE_KEY, value, "it is not a whole number of 0 or more")
    return value


def parse_low_reserve(text):
    """The integer a --low-reserve flag stores. Empty string is the caller's clear signal, not this function's."""
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise BrigadeError(f"--low-reserve takes a whole number of 0 or more, got '{text}'") from None
    if value < 0:
        raise BrigadeError(f"--low-reserve takes a whole number of 0 or more, got '{text}'")
    return value


def round_budget_of(meta):
    """The round budget this store enforces.

    A missing key takes ROUND_BUDGET. A present value that is a boolean, is not an integer, or is under 1 is refused.
    """
    if ROUND_BUDGET_KEY not in meta:
        return ROUND_BUDGET
    value = meta[ROUND_BUDGET_KEY]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        refuse_setting(ROUND_BUDGET_KEY, value, "it is not a whole number of 1 or more")
    return value


def parse_round_budget(text):
    """The integer a --round-budget flag stores. Empty string is the caller's clear signal, not this function's."""
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise BrigadeError(f"--round-budget takes a whole number of 1 or more, got '{text}'") from None
    if value < 1:
        raise BrigadeError(f"--round-budget takes a whole number of 1 or more, got '{text}'")
    return value


def cooldown_minutes(meta):
    """The minutes a cooled seat stays out of a launch.

    A missing key takes COOLDOWN_MINUTES. A present value that is a boolean, is not an integer, or is under 1 is refused.
    """
    if COOLDOWN_KEY not in meta:
        return COOLDOWN_MINUTES
    value = meta[COOLDOWN_KEY]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        refuse_setting(COOLDOWN_KEY, value, "it is not a whole number of 1 or more")
    return value


def parse_cooldown_minutes(text):
    """The integer a --cooldown-minutes flag stores. Empty string is the caller's clear signal, not this function's."""
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise BrigadeError(f"--cooldown-minutes takes a whole number of 1 or more, got '{text}'") from None
    if value < 1:
        raise BrigadeError(f"--cooldown-minutes takes a whole number of 1 or more, got '{text}'")
    return value


def liveness_of(meta):
    """The effective liveness interval in milliseconds and the reporting level it is the default for.

    A missing livenessMs key returns the store's reporting level's default and that level, and a reporting level
    the table does not hold reads as milestones. A present value returns it and "". A present value that is a
    boolean, is not an integer, or is under LIVENESS_FLOOR is refused.
    """
    if LIVENESS_KEY not in meta:
        level = reporting_of(meta)
        if level not in LIVENESS_DEFAULT_MS:
            level = "milestones"
        return LIVENESS_DEFAULT_MS[level], level
    value = meta[LIVENESS_KEY]
    if isinstance(value, bool) or not isinstance(value, int):
        refuse_setting(LIVENESS_KEY, value, "it is not an integer")
    if value < LIVENESS_FLOOR:
        refuse_setting(LIVENESS_KEY, value, f"it is under {LIVENESS_FLOOR}")
    return value, ""


def parse_liveness_ms(text):
    """The integer a --liveness-ms flag stores. Empty string is the caller's clear signal, not this function's."""
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise BrigadeError(f"--liveness-ms takes a whole number of at least {LIVENESS_FLOOR}, got '{text}'") from None
    if value < LIVENESS_FLOOR:
        raise BrigadeError(f"--liveness-ms takes a whole number of at least {LIVENESS_FLOOR}, got '{text}'")
    return value


def _change_note_norm(value):
    text = value.replace("\\", "/")
    if text.startswith("/") or ".." in text.split("/") or "," in text or re.search(r"\s", text):
        return None
    made = posixpath.normpath(text).lstrip("/")
    if made in ("", ".", "/") or made == ".." or made.startswith("../"):
        return None
    return made


def change_note_dir(meta):
    """The change-note directory this store records, or empty string when it records none.

    A missing key and an empty string both read as unset. A present value that is not a
    repository-relative directory path is refused through refuse_setting.
    """
    if CHANGE_NOTE_KEY not in meta:
        return ""
    value = meta[CHANGE_NOTE_KEY]
    if value == "":
        return ""
    if not isinstance(value, str):
        refuse_setting(CHANGE_NOTE_KEY, value, CHANGE_NOTE_REFUSAL)
    made = _change_note_norm(value)
    if made is None:
        refuse_setting(CHANGE_NOTE_KEY, value, CHANGE_NOTE_REFUSAL)
    return made


def parse_change_note_dir(text):
    """The directory a --change-note-dir flag stores, normalized. Empty string is the caller's clear signal, not this function's."""
    if not isinstance(text, str):
        raise BrigadeError("--change-note-dir takes a repository-relative directory path with no leading slash, "
                           f"no .., no comma, and no whitespace, got '{text}'")
    made = _change_note_norm(text)
    if made is None:
        raise BrigadeError("--change-note-dir takes a repository-relative directory path with no leading slash, "
                           f"no .., no comma, and no whitespace, got '{text}'")
    return made


def reporting_of(meta):
    value = meta.get("reporting")
    if value in (None, ""):
        return "milestones"
    return value


def roles_script():
    for path in (seam.BUILT_ROLES, seam.SOURCE_ROLES):
        if path.is_file():
            return path
    raise BrigadeError(f"cannot find roles.py at {seam.BUILT_ROLES} or {seam.SOURCE_ROLES}; build or reinstall pstack-t3")
