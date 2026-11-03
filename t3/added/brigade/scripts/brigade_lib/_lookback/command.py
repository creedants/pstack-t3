from pathlib import Path

from brigade_lib import seam
from brigade_lib._lookback.brief import build
from brigade_lib._lookback.proposals import parse, problems, refused
from brigade_lib._lookback.setting import look_back_of
from brigade_lib._lookback.state import target, write
from brigade_lib.shared import BrigadeError, plural


def register(sub):
    p = sub.add_parser("look-back", help="write a read-only brief over the week's review rounds, and record a reader's proposals")
    p.add_argument("--record", nargs="?", metavar="FILE", const="",
                   help="record the proposals in this file against the newest brief; pass no file with --none")
    p.add_argument("--none", action="store_true",
                   help="with --record, stamp a pass that found nothing usable")
    p.add_argument("--reason", metavar="TEXT", help="one line saying why a --none pass found nothing usable")


def handle(restaurant, args):
    with restaurant.guarded():
        return seam.command(restaurant, args)


def handle_locked(restaurant, args, view):
    if args.command != "look-back":
        return None
    if args.record is not None:
        return _record(restaurant, args)
    if args.none or args.reason is not None:
        raise BrigadeError("--none and --reason go with --record")
    if not look_back_of(restaurant.meta):
        return "look-back is off; run set --look-back <days> to turn it on"
    data, text = build(restaurant, seam.now())
    brief = restaurant.dir / "look-backs" / f"{data['id']}.md"
    restaurant.write(f"look-backs/{data['id']}.md", text)
    write(restaurant, data)
    return f"{data['id']}: {plural(data['rounds'], 'round')} since the last look-back\n{brief}"


def _stamped(restaurant):
    """The brief this record call stamps, or a refusal naming why there is none."""
    brief = target(restaurant)
    if brief is None:
        raise BrigadeError("no look-back brief is recorded; run look-back first")
    return brief


def _record(restaurant, args):
    if args.record and args.none:
        raise BrigadeError("--none takes no file")
    if args.record:
        try:
            text = Path(args.record).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise BrigadeError(f"{args.record} cannot be read; nothing stamped") from None
        brief = _stamped(restaurant)
        recorded = brief.get("recorded")
        if recorded is not None:
            return f"{brief['id']} was recorded at {recorded.get('at') or ''}; nothing stamped"
        proposals = parse(text)
        found = problems(restaurant, brief, proposals)
        if found:
            refused(found)
        return _stamp(restaurant, brief, {"kind": "proposals", "reason": "", "proposals": proposals},
                      f"{plural(len(proposals), 'proposal')} in look-backs/{brief['id']}.json")
    if args.none:
        reason = args.reason
        if not reason or "\n" in reason:
            raise BrigadeError("--none needs --reason, one line")
        brief = _stamped(restaurant)
        recorded = brief.get("recorded")
        if recorded is not None:
            return f"{brief['id']} was recorded at {recorded.get('at') or ''}; nothing stamped"
        return _stamp(restaurant, brief, {"kind": "none", "reason": reason, "proposals": []},
                      f"no usable proposal, {reason}")
    if args.reason is not None:
        raise BrigadeError("--none and --reason go with --record")
    raise BrigadeError("--record needs a file, or --none with --reason")


def _stamp(restaurant, brief, recorded, words):
    """Write the brief's stamp in one file replace and return the line the command prints."""
    stamp = seam.now()
    recorded["at"] = stamp
    brief = dict(brief, recorded=recorded)
    write(restaurant, brief)
    return f"recorded {brief['id']} at {stamp}: {words}"
