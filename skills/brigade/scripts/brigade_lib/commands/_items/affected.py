from brigade_lib import seam
from brigade_lib.affected import _diff_names as _affected_diff, verify_text as _affected_text
from brigade_lib.shared import BrigadeError

def register_affected(sub):
    p = sub.add_parser("affected", help="print the verify paragraph for the changed paths")
    p.add_argument("--root", default=None, help="project root to scan")
    p.add_argument("--paths", action="append", default=[], help="repo path the dish will change; repeatable")
    p.add_argument("--diff", default=None, help="git diff range to name changed files, such as origin/main...HEAD")


def handle_affected(args, store):
    """store is the store root the entry resolved from --store; the picker reads its layout and check command from it."""
    paths = args.paths or []
    has_paths = bool([part for one in paths for part in str(one).split(",") if part.strip() != ""])
    has_diff = args.diff is not None and str(args.diff).strip() != ""
    if has_paths and has_diff:
        raise BrigadeError("use either --paths or --diff, not both")
    if not has_paths and not has_diff:
        raise BrigadeError("affected needs --paths or --diff")
    project = args.root or "."
    if has_diff:
        names = _affected_diff(project, str(args.diff))
        return _affected_text(project, names, seam.LAND, store=store)
    return _affected_text(project, paths, seam.LAND, store=store)
