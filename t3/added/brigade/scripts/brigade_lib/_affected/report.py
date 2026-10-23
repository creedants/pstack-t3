import json
import os
import posixpath
import sqlite3
import subprocess
from pathlib import Path
from brigade_lib._affected.model import AFFECTED_KEY, _Full, _PYTHON_NAME, layout
from brigade_lib.edits import NO_COMMAND
from brigade_lib.shared import BrigadeError
from brigade_lib.store import CHANGE_NOTE_KEY, change_note_dir, store_root

def _python_label(python):
    """The paragraph's name for the second interpreter, taken from the version in its file name."""
    return "Python " + _PYTHON_NAME.match(posixpath.basename(python)).group(1)


def _alternate(command, python):
    """The suite command with its leading python3 replaced by the second interpreter.

    layout refuses a suite that does not start with python3 when python is set, so the first word is python3.
    """
    words = command.split()
    words[0] = python
    return " ".join(words)


def _with_env(layout, command):
    if not layout.env:
        return command
    return "env " + "".join(f"{name}={value} " for name, value in layout.env) + command


def _paragraph(result, land, layout, build, check):
    land_text = str(land)
    tail = [f"Run `python3 {land_text} slot -- {build}` and leave no regeneration diff. "] if build else []
    if check:
        tail.append(f"Run `python3 {land_text} slot -- {check}`. ")
    tail.append(f"Keep each build and test in a foreground slot.")
    if isinstance(result, _Full):
        names = ", ".join(f"`{name}`" for name in result.unmapped)
        head = [f"No affected mapping for {names}. ",
                f"Run the full suite in the foreground as `python3 {land_text} slot --slots 2 -- "
                f"{_with_env(layout, layout.suite)}`. "]
        second = [f"Run the same suite on {_python_label(layout.python)} in the foreground as "
                  f"`python3 {land_text} slot --slots 2 -- "
                  f"{_with_env(layout, _alternate(layout.suite, layout.python))}`. "] \
            if layout.python else []
        return "".join(head + second + tail)
    names = ", ".join(f"`{name}`" for name in result.names)
    joined = " ".join(result.names)
    modules = _with_env(layout, f"python3 -m unittest {joined}")
    head = [f"Affected test modules are {names}. ",
            f"Run them in the foreground as `python3 {land_text} slot -- {modules}`. "]
    second = [f"Run the same modules on {_python_label(layout.python)} in the foreground as "
              f"`python3 {land_text} slot -- {_with_env(layout, f'{layout.python} -m unittest {joined}')}`. "] \
        if layout.python else []
    return "".join(head + second + tail)


def _stored_restaurants(root, store=None):
    """The path and meta of every readable store under store whose resolved projectRoot is root, with plain reads and no lock.

    store is the store root the caller resolved from its own --store flag, and None takes the default.
    """
    resolved = Path(str(root)).resolve()
    found = []
    try:
        paths = sorted(store_root(store).glob("*/*/restaurant.json"))
    except OSError:
        return found
    for path in paths:
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(value, dict):
            continue
        project = value.get("projectRoot")
        if not isinstance(project, str):
            continue
        try:
            same = Path(project).resolve() == resolved
        except (OSError, ValueError):
            continue
        if same:
            found.append((path, value))
    return found


def _check_command(meta):
    value = meta.get("checkCommand")
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return "" if text == NO_COMMAND else text


def records_check(meta):
    """True when meta records a check command, including the word none."""
    value = meta.get("checkCommand")
    return isinstance(value, str) and bool(value.strip())


def _common_dir(root):
    """The repository's git common directory for root as a resolved Path, or None when root is empty or git cannot name one."""
    if not str(root or "").strip():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "--work-tree=.", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=10)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return Path(r.stdout.strip()).resolve()
    except (OSError, ValueError):
        return None


def contract_check(root):
    """The landing contract's check command for root, or an empty string.

    It reads the checks value from the contract table of the land.db whose commonDir resolves to root's common git directory.
    It returns that value stripped when it is a list of exactly one string other than "none", and an empty string in every other case.
    """
    common = _common_dir(root)
    if common is None:
        return ""
    base = Path(os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")) / "pstack-t3" / "landing"
    try:
        candidates = list(base.glob("*/land.db"))
    except (OSError, ValueError):
        return ""
    for path in candidates:
        try:
            connection = sqlite3.connect(str(path))
            try:
                rows = dict(connection.execute("SELECT key, value FROM contract").fetchall())
            finally:
                connection.close()
            recorded = Path(json.loads(rows.get("commonDir"))).resolve()
        except Exception:
            continue
        if recorded != common:
            continue
        try:
            checks = json.loads(rows["checks"])
        except Exception:
            return ""
        if isinstance(checks, list) and len(checks) == 1 and isinstance(checks[0], str):
            text = checks[0].strip()
            return "" if text == NO_COMMAND else text
        return ""
    return ""


def _build_command(meta):
    value = meta.get("buildCommand")
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return "" if text == NO_COMMAND else text


def _settings(root, meta, store=None):
    """Layout and commands from matching stores.

    When meta is given that store supplies the layout, the build command, the check command, and the change note directory, except that the check command is the landing contract's when the store records no checkCommand.
    Otherwise matching stores are every readable restaurant.json under store root in sorted path order whose projectRoot resolves to root.
    Unreadable or non JSON stores are skipped.
    Build and check take a store's string only when it is non-empty after stripping and is not "none", and the scan takes the first such string.
    When no matched store records a checkCommand the check command is the landing contract's.
    The landing contract supplies the check command only when it records exactly one check.
    Later different values are ignored without warning.
    ChangeNoteDir compares every matched store with unset as empty and refuses on difference.
    AffectedTests compares only stores that record it by raw value and refuses on difference.
    """
    if meta is not None:
        check = _check_command(meta) if records_check(meta) else contract_check(root)
        return layout(meta), _build_command(meta), check, change_note_dir(meta)
    pick = None
    picked_path = None
    picked_value = None
    noted = ""
    noted_path = None
    build = ""
    check = ""
    recorded = False
    for path, one in _stored_restaurants(root, store):
        if not build:
            build = _build_command(one)
        if not check:
            check = _check_command(one)
        if records_check(one):
            recorded = True
        note = change_note_dir(one)
        if noted_path is None:
            noted, noted_path = note, path
        elif note != noted:
            raise BrigadeError(f"restaurant.json {CHANGE_NOTE_KEY} differs across {noted_path} and {path}; keep one value")
        if AFFECTED_KEY not in one:
            continue
        got = layout(one)
        if picked_path is None:
            pick, picked_path, picked_value = got, path, one[AFFECTED_KEY]
        elif one[AFFECTED_KEY] != picked_value:
            raise BrigadeError(f"restaurant.json {AFFECTED_KEY} differs across {picked_path} and {path}; keep one value")
    if not check and not recorded:
        check = contract_check(root)
    return pick, build, check, noted


def _fallback(land, check):
    land_text = str(land)
    if check:
        return (f"Run the check command in the foreground as `python3 {land_text} slot -- {check}`. "
                f"The affected-test picker is off because this store sets no `{AFFECTED_KEY}` layout.")
    return (f"No verify command is set. The affected-test picker is off because this store sets no "
            f"`{AFFECTED_KEY}` layout; record the project's check with `brigade.py set --check-command <command>`.")
