import json
import posixpath
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
    """command with each of the layout's environment assignments in front, as NAME=value and one space."""
    return "".join(f"{name}={value} " for name, value in layout.env) + command


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


def _build_command(meta):
    value = meta.get("buildCommand")
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return "" if text == NO_COMMAND else text


def _settings(root, meta, store=None):
    """Layout and commands from matching stores.

    When meta is given that store alone supplies all four values.
    Otherwise matching stores are every readable restaurant.json under store root in sorted path order whose projectRoot resolves to root.
    Unreadable or non JSON stores are skipped.
    Build and check take the first string that is non-empty after stripping and is not "none".
    Later different values are ignored without warning.
    ChangeNoteDir compares every matched store with unset as empty and refuses on difference.
    AffectedTests compares only stores that record it by raw value and refuses on difference.
    """
    if meta is not None:
        return layout(meta), _build_command(meta), _check_command(meta), change_note_dir(meta)
    pick = None
    picked_path = None
    picked_value = None
    noted = ""
    noted_path = None
    build = ""
    check = ""
    for path, one in _stored_restaurants(root, store):
        if not build:
            build = _build_command(one)
        if not check:
            check = _check_command(one)
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
    return pick, build, check, noted


def _fallback(land, check):
    land_text = str(land)
    if check:
        return (f"Run the check command in the foreground as `python3 {land_text} slot -- {check}`. "
                f"The affected-test picker is off because this store sets no `{AFFECTED_KEY}` layout.")
    return (f"No verify command is set. The affected-test picker is off because this store sets no "
            f"`{AFFECTED_KEY}` layout; record the project's check with `brigade.py set --check-command <command>`.")
