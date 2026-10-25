"""The landing contract's setup command, read once for the worker brief line."""
import json
import os
from pathlib import Path

from brigade_lib._affected.report import _common_dir, _contract_rows
from brigade_lib.edits import NO_COMMAND


def contract_setup(root):
    """The landing contract's setup command for root, or an empty string.

    It takes the setup value from the contract table of the land.db whose commonDir resolves to root's common git directory.
    A root git cannot name, an unreadable store, a missing key, a value that is not a string, a blank value, and the
    literal none all read as no setup command.
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
            rows = _contract_rows(path)
            if rows is None:
                continue
            recorded = Path(json.loads(rows.get("commonDir"))).resolve()
        except Exception:
            continue
        if recorded != common:
            continue
        try:
            value = json.loads(rows["setup"])
        except Exception:
            return ""
        if not isinstance(value, str):
            return ""
        text = value.strip()
        return "" if not text or text == NO_COMMAND else text
    return ""


def setup_lines(root):
    """The worker brief's setup bullet for root, or an empty tuple."""
    setup = contract_setup(root)
    if not setup:
        return ()
    return (f"- Before your first build or test, run the project's setup command through `land.py slot --` unless the worktree already has it run: {setup}.",)
