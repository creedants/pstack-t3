import json
import sys
from pathlib import Path
from brigade_lib.commands.once import Once
from brigade_lib.briefs import brief
from brigade_lib.shared import BrigadeError
from brigade_lib.store import NEEDS_CONTRACT

def brief_contract(value):
    """The contract as given. Refuses None and text that is only whitespace."""
    if not (value or "").strip():
        raise BrigadeError(NEEDS_CONTRACT)
    return value


def _brief_fields(args):
    flagged = any(getattr(args, name) is not None for name in ("goal", "contract", "verify", "base", "paths", "lease")) or args.acceptance or args.context
    if args.fields is not None:
        if flagged:
            raise BrigadeError("use either --fields or the field flags, not both")
        if args.fields == "-":
            if sys.stdin.isatty():
                raise BrigadeError("brief fields: --fields - reads JSON from stdin")
            raw = sys.stdin.read()
        else:
            try:
                raw = Path(args.fields).read_text(encoding="utf-8")
            except OSError as error:
                raise BrigadeError(f"brief fields: {error}") from error
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise BrigadeError(f"brief fields: {error}") from error
        if not isinstance(payload, dict):
            raise BrigadeError("brief fields: JSON must be an object")
        allowed = {"goal", "acceptance", "contract", "verify", "base", "context", "paths", "lease"}
        lists = {"acceptance", "context"}
        unknown = [key for key in payload if key not in allowed]
        if unknown:
            raise BrigadeError(f"brief fields: unknown key {unknown[0]}")
        for key in ("goal", "acceptance", "base"):
            if key not in payload:
                raise BrigadeError(f"brief fields: missing {key}")
        for key, value in payload.items():
            if value is None:
                raise BrigadeError(f"brief fields: {key} is null")
            if key in lists:
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    raise BrigadeError(f"brief fields: {key} must be a list of strings")
            elif not isinstance(value, str):
                raise BrigadeError(f"brief fields: {key} must be a string")
        if not payload["base"].strip():
            raise BrigadeError("brief --base takes a trunk ref, such as origin/main")
        return {
            "goal": payload["goal"],
            "acceptance": payload["acceptance"],
            "contract": brief_contract(payload.get("contract")),
            "verify": payload.get("verify", ""),
            "paths": payload["paths"] if "paths" in payload else "",
            "lease": payload["lease"] if "lease" in payload else "",
            "base": payload["base"],
            "context": payload["context"] if "context" in payload else [],
        }
    if args.goal is None or args.base is None:
        raise BrigadeError("brief needs --fields, or --goal and --base")
    if not args.base.strip():
        raise BrigadeError("brief --base takes a trunk ref, such as origin/main")
    return {
        "goal": args.goal,
        "acceptance": args.acceptance,
        "contract": brief_contract(args.contract),
        "verify": args.verify or "",
        "paths": args.paths or "",
        "lease": args.lease or "",
        "base": args.base,
        "context": args.context,
    }


def register_brief(sub):
    help_text = (
        "Render the worker brief for a dish. Refuses when a field or the coordinator thread is missing. "
        "House rules use the case-sensitive tags [worker], [reviewer], and [coordinator]. "
        "The first tag anywhere on a rule's first line selects its role. "
        "After outer whitespace is stripped, each non-empty line starting without a space or tab begins a rule. "
        "Following blank lines and lines starting with a space or tab continue that rule, even after a blank line. "
        "A brief keeps untagged and [worker] rules. It removes the [worker] tag and at most one following space. "
        "It leaves out [reviewer] and [coordinator] rules with their continuation lines."
    )
    p = sub.add_parser("brief", help=help_text, description=help_text)
    p.add_argument("id")
    p.add_argument("--fields", help="JSON file of brief fields, or - for stdin")
    p.add_argument("--goal", default=None, help="one sentence: the outcome")
    p.add_argument("--acceptance", action="append", default=[], help="a checkable criterion; repeatable, at least one")
    p.add_argument("--contract", default=None,
                   help="the item's contract, rendered as given under CONTRACT: in the brief; write none when the item has no "
                        "safety, privacy, or integrity property; a brief with no contract is refused")
    p.add_argument("--verify", default=None, help="exact commands that prove it, plus known gotchas")
    p.add_argument("--paths", default=None, action=Once, help="leased paths, when fire did not claim them; given once")
    p.add_argument("--lease", default=None, action=Once, help="the landing lease id, when fire did not claim it; given once")
    p.add_argument("--base", default=None, help="the trunk ref the worker's branch starts from, such as origin/main")
    p.add_argument("--context", action="append", default=[], help="a pointer to files, PRs, or upstream reports; repeatable")


def register_contract(sub):
    p = sub.add_parser("contract", help="print the contract the dish's last brief stored")
    p.add_argument("id")


def handle_brief(restaurant, args):
    fields = _brief_fields(args)
    return brief(restaurant, args.id, **fields)
