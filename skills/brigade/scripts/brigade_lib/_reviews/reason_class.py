import re
from pathlib import Path
from brigade_lib.reports import report_entry
from brigade_lib.store import REASONS

REASON_LINE = re.compile(r"^Reason:[ \t]*(\S+)[ \t]*$", re.M)


def report_class(restaurant, report):
    """The reason class the review report named by a pass.tsv row's report cell states, or None.

    A cell that is not the bare name or reports/<name>, and a name that report_entry does not call a file,
    returns None. So does a report with no Reason: line, and one whose first such line names no member of REASONS.
    An entry the operating system refuses to look at, and a name it refuses to stat, return None too, so no store
    makes this raise.
    """
    if not report:
        return None
    name = Path(report).name
    if report not in (name, f"reports/{name}"):
        return None
    try:
        if report_entry(restaurant, name) != "file":
            return None
        text = (restaurant.dir / "reports" / name).read_bytes().decode("utf-8", "replace")
    except (OSError, ValueError):
        return None
    match = REASON_LINE.search(text)
    if not match or match.group(1) not in REASONS:
        return None
    return match.group(1)
