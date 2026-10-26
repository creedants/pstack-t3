import re
from pathlib import Path
from brigade_lib.reports import report_entry
from brigade_lib.store import REASONS

REASON_LINE = re.compile(r"^Reason:[ \t]*(\S+)[ \t]*$", re.M)

# A fence delimiter: up to three leading spaces, then three or more backticks or tildes.
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")


def _without_fenced(text):
    """Return text with every line of a fenced code block blanked out.

    Fenced blocks are Markdown code fences: an opening line with up to three
    leading spaces and three or more backticks or tildes, closed by a line with
    the same character at least as long and no info string. An opening backtick
    fence whose info string holds a backtick is not a fence. An unclosed fence
    runs to the end of the text. A carriage return at the end of a line counts
    as part of the line ending when detecting a fence.
    """
    out = []
    fence = None
    for line in text.split("\n"):
        body = line[:-1] if line.endswith("\r") else line
        if fence is None:
            opening = _FENCE.match(body)
            if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
                fence = (opening.group(1)[0], len(opening.group(1)))
                out.append("")
            else:
                out.append(line)
        else:
            closing = _CLOSE.match(body)
            if closing and closing.group(1)[0] == fence[0] and len(closing.group(1)) >= fence[1]:
                fence = None
            out.append("")
    return "\n".join(out)


def report_class(restaurant, report):
    """The reason class the review report named by a pass.tsv row's report cell states, or None.

    A cell that is not the bare name or reports/<name>, and a name that report_entry does not call a file,
    returns None. So does a report with no Reason: line, and one whose first such line names no member of REASONS.
    An entry the operating system refuses to look at, and a name it refuses to stat, return None too, so no store
    makes this raise.

    Lines inside fenced Markdown code blocks are ignored, since they are examples rather than the reviewer's own
    classification. A report that is not valid UTF-8 states no class.
    """
    if not report:
        return None
    name = Path(report).name
    if report not in (name, f"reports/{name}"):
        return None
    try:
        if report_entry(restaurant, name) != "file":
            return None
        text = (restaurant.dir / "reports" / name).read_bytes().decode("utf-8")
    except (OSError, ValueError):
        return None
    match = REASON_LINE.search(_without_fenced(text))
    if not match or match.group(1) not in REASONS:
        return None
    return match.group(1)
