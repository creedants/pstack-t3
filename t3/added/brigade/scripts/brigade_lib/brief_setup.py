"""Read the landing contract's setup command for the worker brief and for brigade opening."""
import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parents[1])
    sys.dont_write_bytecode = True

from brigade_lib._affected.report import _common_dir, _contract_rows
from brigade_lib.edits import NO_COMMAND


def contract_setup(root):
    """The landing contract's setup command for root, or an empty string."""
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=".", help="checkout whose contract holds the setup command")
    args = parser.parse_args(argv)
    command = contract_setup(args.project_root)
    if command:
        print(command)
    return 0


if __name__ == "__main__":
    sys.exit(main())
