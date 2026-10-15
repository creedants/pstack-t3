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


def _reject_constant(name):
    """NaN, Infinity, and -Infinity are not JSON, so a document holding one is not read."""
    raise ValueError(name)


def _object(raw):
    """The JSON object the settings bytes hold, or None when they hold no strict JSON object."""
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _git_refusal(real):
    """Why brigade refuses to write the resolved destination, or None when git leaves it writable.

    A pathspec that matches no tracked file, and a directory in no repository, both leave it writable. A rev-parse
    whose error names not a git repository is a directory in no repository. A git command that fails for any other
    reason refuses, because an untracked state that cannot be established is not one.
    """
    directory = real.parent
    while not directory.is_dir():
        directory = directory.parent
    words = ["git", "-C", str(directory)]
    try:
        listed = subprocess.run([*words, "ls-files", "--error-unmatch", "--", str(real.relative_to(directory))],
                                capture_output=True)
        if listed.returncode == 0:
            state = "tracked"
        elif listed.returncode == 1:
            state = "untracked"
        else:
            found = subprocess.run([*words, "rev-parse", "--git-dir"], capture_output=True)
            if found.returncode == 0 or b"not a git repository" not in found.stderr:
                state = "unreadable"
            else:
                state = "no repository"
    except OSError as error:
        return f"cannot run git in {directory}: {error.strerror}"
    if state in ("untracked", "no repository"):
        return None
    if state == "unreadable":
        return f"git cannot say whether {real} is tracked; fix that repository and run this again"
    return f"{real} is tracked by git; untrack it before brigade writes {SETTINGS_KEY}"


def _load(project_root):
    """The settings path and the text its file holds, or None when it holds none.

    The directory that holds the settings file must sit below the project root with no symbolic link at any component
    on the way. The file's real destination must be one git leaves writable. A file that exists must hold a strict
    JSON object. A refusal raises BrigadeError and changes no file.
    """
    path = settings_path(project_root)
    directory = Path(project_root)
    for part in Path(SETTINGS_RELATIVE).parent.parts:
        directory = directory / part
        if directory.is_symlink():
            raise BrigadeError(f"{directory} is a symbolic link; brigade writes {SETTINGS_RELATIVE} through no link")
    refusal = _git_refusal(path.resolve())
    if refusal:
        raise BrigadeError(refusal)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return path, None
    except OSError as error:
        raise BrigadeError(f"cannot read {path}: {error}") from error
    try:
        text = raw.decode("utf-8")
        value = json.loads(text, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError):
        raise BrigadeError(f"{path} holds invalid JSON; fix or remove it") from None
    if not isinstance(value, dict):
        raise BrigadeError(f"{path} holds no JSON object; fix or remove it")
    return path, text


def _space(text, index):
    """The index of the first character at or after index that is not JSON whitespace."""
    while index < len(text) and text[index] in " \t\n\r":
        index += 1
    return index


def _members(text):
    """(name, the start of its value, the end of its value) for every member of one top-level JSON object, and the
    index of that object's closing brace.

    A name that occurs more than once keeps every member. None when the text is not one JSON object.
    """
    decoder = json.JSONDecoder(parse_constant=_reject_constant)
    opening = _space(text, 0)
    if text[opening:opening + 1] != "{":
        return None
    index = _space(text, opening + 1)
    members = []
    while text[index:index + 1] != "}":
        name, index = decoder.raw_decode(text, index)
        index = _space(text, index)
        if text[index:index + 1] != ":":
            return None
        index = _space(text, index + 1)
        _, end = decoder.raw_decode(text, index)
        members.append((name, index, end))
        index = _space(text, end)
        if text[index:index + 1] == ",":
            index = _space(text, index + 1)
        elif text[index:index + 1] != "}":
            return None
    return members, index


def _with_cap(text, cap):
    """The document text with one autoCompactWindow member holding cap and every other byte of it kept.

    The value of the last member named autoCompactWindow is replaced, or one member is placed after the last member
    there is, in the document's own spacing, when it holds none.
    """
    members, closing = _members(text)
    member = f'"{SETTINGS_KEY}": {cap}'
    for name, start, end in reversed(members):
        if name == SETTINGS_KEY:
            return text[:start] + str(cap) + text[end:]
    opening = _space(text, 0)
    at = members[-1][2] if members else closing
    spacing = text[opening + 1:_space(text, opening + 1)]
    return text[:at] + ("," if members else "") + spacing + member + text[at:]


def sync_meta(meta):
    """Write autoCompactWindow into the project's settings file and keep every other byte of it.

    A file that holds no strict JSON object, a settings directory reached through a symbolic link, a destination that
    git reports as tracked or cannot report on, and a git command that cannot start all refuse with exit 1 and change
    no file.
    """
    cap = effective_cap(meta)
    path, text = _load(meta["projectRoot"])
    published = json.dumps({SETTINGS_KEY: cap}, indent=2) + "\n" if text is None else _with_cap(text, cap)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=str(path.parent), delete=False, suffix=".tmp",
                                     encoding="utf-8", newline="") as handle:
        try:
            handle.write(published)
        except BaseException:
            handle.close()
            os.unlink(handle.name)
            raise
    try:
        os.replace(handle.name, path)
    except OSError:
        os.unlink(handle.name)
        raise


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
    value = _object(raw)
    if value is None or value.get(SETTINGS_KEY) != cap:
        return [_drift(path, cap)]
    return []


def settings_cap_only(before, after):
    """Whether two settings file states differ by nothing but the autoCompactWindow member.

    Either byte string is None when that state holds no file. The later state must hold a strict JSON object with a
    top-level autoCompactWindow member holding an integer, and, when the earlier state is a file, must be exactly what
    the cap splice writes over it. A state that holds no file is a settings file that was created, so it differs from
    a document that holds nothing but the cap and from nothing else.
    """
    value = None if after is None else _object(after)
    cap = None if value is None else value.get(SETTINGS_KEY)
    if isinstance(cap, bool) or not isinstance(cap, int):
        return False
    if before is None:
        return list(value) == [SETTINGS_KEY]
    return after == _with_cap(before.decode("utf-8"), cap).encode("utf-8")
