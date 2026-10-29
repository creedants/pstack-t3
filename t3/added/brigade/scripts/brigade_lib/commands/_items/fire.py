from brigade_lib.commands.once import Once
from brigade_lib.items import fire, item_escalation
from brigade_lib.store import KINDS

def add_paths_alone(args):
    """True when --add-paths is the only dish flag given."""
    def given(name):
        value = getattr(args, name, None)
        return value is not None and value is not False
    return not any(given(name) for name in ("state", "task", "thread", "branch", "pr", "sha", "timebox", "reported",
                                            "lease", "paths", "worker_ended", "stopped", "mode", "reason"))


def register_fire(sub):
    p = sub.add_parser("fire", help="group tickets into one dish and assign it to a station")
    p.add_argument("--tickets", required=True, action=Once,
                   help="comma-separated ticket ids; the first is the lead ticket; the item takes the lead ticket's goal, "
                        "and its kind when it has one; given once")
    p.add_argument("--station", required=True,
                   help="the pstack playbook the worker runs, or full-send for tstack's full-send method, only when the owner asked for it")
    p.add_argument("--summary", required=True)
    for field in ("task", "thread", "branch"):
        p.add_argument(f"--{field}", default="")
    p.add_argument("--timebox", type=int, default=60, help="minutes before the liveness check flags the dish")
    p.add_argument("--paths", default="", action=Once, help="paths the dish will change. Fire claims a landing lease on them, plus the generated copies and doc pages the contract map makes from them when the contract lists generated paths and a map; given once")
    p.add_argument("--mode", help="full, to start the dish in full mode; needs --reason")
    p.add_argument("--reason", help="one line: why the dish runs in full mode")
    p.add_argument("--kind", choices=KINDS,
                   help="the item's kind when the lead ticket has none or --tickets names no ticket; a lead ticket with no "
                        "kind records it too; refused when the lead ticket has another kind; with no --kind and no kind "
                        "from the lead ticket, station bug-fix or perf-issue gives fix, station investigation gives study, "
                        "and fire is refused on any other station")
    p.add_argument("--ride-along", default="",
                   help="comma-separated ids from --tickets, not the first, that may be under another goal than the first "
                        "ticket's; each must have priority low and must not be a feature; every other ticket after the "
                        "first must have the first ticket's goal or no goal")
    p.add_argument("--doc-item", action="store_true",
                   help="allow a shared document in --paths and record it as a document item")


def handle_fire(restaurant, args):
    args.reason = item_escalation(args.mode, args.reason)
    ids = [ident.strip() for ident in args.tickets.split(",") if ident.strip()]
    ride_along = [ident.strip() for ident in args.ride_along.split(",") if ident.strip()]
    return fire(restaurant, ids, args.station, args.task, args.thread, args.branch, args.summary, args.timebox,
                args.paths, args.reason, args.kind or "", ride_along, args.doc_item)
