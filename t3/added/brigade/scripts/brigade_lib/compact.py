"""The coordinator context cap and its settings file sync."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

from brigade_lib.shared import BrigadeError


DEFAULT_CONTEXT_CAP = 300000
MIN_CONTEXT_CAP = 50000
PROVIDERS = ("claude", "other")
DEFAULT_PROVIDER = "claude"
SETTINGS_RELATIVE = ".claude/settings.local.json"
SETTINGS_KEY = "autoCompactWindow"


def is_managed(path):
    """True for a project path brigade writes itself. Tree comparisons skip these paths."""
    return path == SETTINGS_RELATIVE


def effective_cap(meta):
    """The cap this restaurant enforces. A missing or non-integer contextCap reads as the default."""
    value = meta.get("contextCap")
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_CONTEXT_CAP
    return value


def effective_provider(meta):
    """The coordinator host. A missing or unknown coordinatorProvider reads as claude."""
    value = meta.get("coordinatorProvider")
    if value in PROVIDERS:
        return value
    return DEFAULT_PROVIDER


def parse_context_cap(text):
    """The integer a --context-cap flag stores. Empty string is the caller's clear signal, not this function's."""
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise BrigadeError(f"--context-cap takes an integer of at least {MIN_CONTEXT_CAP}, got '{text}'") from None
    if value < MIN_CONTEXT_CAP:
        raise BrigadeError(f"--context-cap takes an integer of at least {MIN_CONTEXT_CAP}, got '{text}'")
    return value


def parse_coordinator_provider(text):
    """The provider a --coordinator-provider flag stores."""
    if text in PROVIDERS:
        return text
    raise BrigadeError(f"--coordinator-provider takes claude or other, got '{text}'")


def settings_path(project_root):
    """The settings file open and set sync."""
    return Path(project_root) / SETTINGS_RELATIVE


def _tracked(project_root):
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "ls-files", "--error-unmatch", "--", SETTINGS_RELATIVE],
            capture_output=True)
    except OSError:
        return False
    return result.returncode == 0


def sync_settings(project_root, cap):
    """Write autoCompactWindow into the settings file and keep every other key.

    A tracked file and a file holding invalid JSON refuse with exit 1 and stay unchanged.
    A file holding valid JSON that is not an object holds no keys, so the write starts from an empty object.
    """
    path = settings_path(project_root)
    if _tracked(project_root):
        raise BrigadeError(f"{path} is tracked by git; untrack it before brigade writes {SETTINGS_KEY}")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        data = {}
    except OSError as error:
        raise BrigadeError(f"cannot read {path}: {error}") from error
    else:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise BrigadeError(f"{path} holds invalid JSON; fix or remove it") from None
        if not isinstance(data, dict):
            data = {}
    data[SETTINGS_KEY] = cap
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=str(path.parent), delete=False, suffix=".tmp",
                                     encoding="utf-8") as handle:
        try:
            handle.write(json.dumps(data, indent=2) + "\n")
        except BaseException:
            handle.close()
            os.unlink(handle.name)
            raise
    os.replace(handle.name, path)


def _drift(path, cap):
    return f"{path} needs {SETTINGS_KEY} {cap}; run brigade.py set --context-cap {cap}"


def status_lines(meta):
    """At most one status line for the cap. An empty list means the file matches."""
    cap = effective_cap(meta)
    if effective_provider(meta) != "claude":
        return [f"context cap {cap} is not enforced on this host"]
    path = settings_path(meta["projectRoot"])
    try:
        raw = path.read_bytes()
    except OSError:
        return [_drift(path, cap)]
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return [_drift(path, cap)]
    if not isinstance(data, dict) or data.get(SETTINGS_KEY) != cap:
        return [_drift(path, cap)]
    return []
