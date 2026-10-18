import posixpath
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from brigade_lib.shared import BrigadeError, fence_close, fence_open

EDITS_SECTION = (2, "Edits")
EDIT_TITLE = re.compile(r"Edit (\S.*)")
HEADING = re.compile(r"(#{1,6}) (?!(?:.*[ \t])?#+$)(\S(?:.*\S)?)")

FILE_LINE = re.compile(r"File: `([^` ][^`]*)`(?!`)")
LABELS = {"Old text:": "old", "New text:": "new"}
# The marks edit_mark gives the five parts of one edit that one_edit reads, in the only order it reads them.
EDIT_MARKS = ["file", "old", "fence", "new", "fence"]
BLANK = re.compile(r"[ \t]*")
# What a block quote, a list item, and indentation put in front of a line's text.
NESTED = re.compile(r"[ \t]*(?:(?:>|(?:[-+*]|[0-9]{1,9}[.)])(?=[ \t]))[ \t]*)*")
UNDERLINE = re.compile(NESTED.pattern + r"(?:=+|-+)[ \t]*")
# What starts a line outside a fence that HEADING does not match and report_parts refuses. Each key is a key of EDITS_REFUSED.
UNREAD = (
    ("fence", re.compile(r"(?![`~])" + NESTED.pattern + r"(?:`{3}|~{3})")),
    ("heading", re.compile(NESTED.pattern + r"#{1,6}(?:[ \t]|$)")),
    ("html", re.compile(r" {0,3}<[A-Za-z/!?]")),
)
# Why parse_edits refuses a review report's edits. Each value finishes the words `reports/<name>`.
EDITS_REFUSED = {
    "none": " holds no edit under a ## Edits heading",
    "return": " holds a carriage return",
    "nul": " holds a NUL character",
    "mark": " starts with a byte order mark",
    "twice": " holds {count} headings whose text is Edits",
    "fence": (": line {line} has three backticks or tildes after nothing but indentation, block quote marks, and list marks; "
              "start each fence at the first column"),
    "open": ": line {line} opens a fence that no later line closes",
    "heading": (": line {line} starts like a heading and is not one to six #, one space, and the text, from the first column "
                "with nothing after the text"),
    "html": ": line {line} starts, after at most three spaces, with < and then an ASCII letter, /, !, or ?",
    "underline": (": line {line} is only = or only - once its indentation, block quote marks, list marks, and trailing "
                  "spaces and tabs are removed, and the line above it is not empty and not only spaces and tabs"),
    "inside": ": line {line} is a heading in the ## Edits section that is neither ### Edit <n> nor a heading of one or two #",
    "above": ": line {line} starts a fenced block in the ## Edits section above its first ### Edit heading",
    "parts": (": Edit {number} must hold, once each and in this order, a File line, an Old text line, a fenced block, "
              "a New text line, and a fenced block"),
    "near": (": Edit {number} has a File, Old text, or New text line after whitespace, a block quote mark, or a list mark, "
             "or an Old text or New text line that ends in whitespace"),
    "malformed": (": line {line} of Edit {number} starts, once its indentation, block quote marks, list marks, and the "
                  "whitespace at its two ends are removed, with File: and a backtick; what follows must be a path that "
                  "holds no backtick and does not start with a space, then a backtick that no backtick follows directly"),
    "below": (": line {line} is a File, Old text, or New text line of Edit {number}; the line above it must be empty or only "
              "spaces and tabs, the edit's heading, the closing line of a fence, or another of those three lines"),
    "apart": (": line {line} opens a fenced block of Edit {number}; every line between an Old text or New text line and "
              "the fenced block below it must be empty or only spaces and tabs"),
    "empty": ": Edit {number} has an empty old text",
    "path": ": Edit {number} names {path}; a path is relative to the repository and has no .. part",
}


class EditsRefused(BrigadeError):
    """A refusal from parse_edits. Its text is a value of EDITS_REFUSED with its fields filled in."""


@dataclass(frozen=True)
class Edit:
    number: str
    path: str
    old: str
    new: str



def report_parts(lines):
    """A report's lines as (kind, value, number) parts. The number is that of the part's first line, counted from 1.

    A `fence` is the text between a fence's two lines.
    A fence opens at a line that starts at the first column with three or more backticks and holds no other backtick,
    or that starts there with three or more tildes.
    It closes at the next line that is at most three spaces, at least as many of the same character, and then only
    spaces and tabs.
    A `heading` is the (count of `#`, text) of a line outside a fence that HEADING matches in full. That is one to six `#`,
    one space, and a text with no whitespace at its two ends and no run of `#` at its end after a space or a tab.
    A `line` is any other line outside a fence.
    Raises EditsRefused for a fence that no later line closes, and for the first `line` that an UNREAD pattern matches at
    its start or that UNDERLINE matches in full directly below a `line` that BLANK does not match in full.
    """
    parts, index = [], 0
    while index < len(lines):
        line, number = lines[index], index + 1
        opened = fence_open(line)
        if opened:
            close = next((at for at in range(index + 1, len(lines)) if fence_close(lines[at], opened)), None)
            if close is None:
                raise EditsRefused(EDITS_REFUSED["open"].format(line=number))
            parts.append(("fence", "\n".join(lines[index + 1:close]), number))
            index = close + 1
            continue
        heading = HEADING.fullmatch(line)
        if heading:
            parts.append(("heading", (len(heading.group(1)), heading.group(2)), number))
        else:
            unread = next((key for key, pattern in UNREAD if pattern.match(line)), None)
            if not unread and UNDERLINE.fullmatch(line) and parts and parts[-1][0] == "line" and not BLANK.fullmatch(parts[-1][1]):
                unread = "underline"
            if unread:
                raise EditsRefused(EDITS_REFUSED[unread].format(line=number))
            parts.append(("line", line, number))
        index += 1
    return parts


def edit_mark(kind, value):
    """What one part of an edit is to one_edit.

    A fence is `fence`. A line that FILE_LINE matches at its start is `file`. That is `File: `, a backtick, a path, and a
    backtick that no backtick follows directly, where the path holds no backtick and does not start with a space.
    A line that is `Old text:` is `old`, and one that is `New text:` is `new`. A line that BLANK matches in full is `blank`.
    Another line that is `file`, `old`, or `new` once what NESTED matches at its start and then the whitespace at its
    two ends are removed is `unread`. Another line that then starts with `File: ` and a backtick is `malformed`.
    Every line left is None.
    """
    if kind == "fence":
        return "fence"
    if FILE_LINE.match(value):
        return "file"
    if value in LABELS:
        return LABELS[value]
    if BLANK.fullmatch(value):
        return "blank"
    text = value[NESTED.match(value).end():].strip()
    if FILE_LINE.match(text) or text in LABELS:
        return "unread"
    return "malformed" if text.startswith("File: `") else None


def one_edit(number, parts):
    """The Edit of the (kind, value, line number) parts between an edit's heading and the next heading.

    Raises EditsRefused for a part that edit_mark calls `unread`, and for the first part that it calls `malformed`.
    Raises it unless the parts that edit_mark calls `file`, `old`, `new`, or `fence` are, in this order, one `file`,
    one `old`, one `fence`, one `new`, and one `fence`.
    Raises it for the first part that is a `file`, `old`, or `new` directly below a part that edit_mark calls None, or
    a `fence` whose nearest part above it that is not `blank` is neither `old` nor `new`.
    """
    marks = [edit_mark(kind, value) for kind, value, _ in parts]
    if "unread" in marks:
        raise EditsRefused(EDITS_REFUSED["near"].format(number=number))
    if "malformed" in marks:
        raise EditsRefused(EDITS_REFUSED["malformed"].format(line=parts[marks.index("malformed")][2], number=number))
    if [mark for mark in marks if mark in EDIT_MARKS] != EDIT_MARKS:
        raise EditsRefused(EDITS_REFUSED["parts"].format(number=number))
    above = nearest = "heading"
    for mark, (_, _, line) in zip(marks, parts):
        if mark in ("file", "old", "new") and above is None:
            raise EditsRefused(EDITS_REFUSED["below"].format(line=line, number=number))
        if mark == "fence" and nearest not in ("old", "new"):
            raise EditsRefused(EDITS_REFUSED["apart"].format(line=line, number=number))
        above = mark
        nearest = nearest if mark == "blank" else mark
    marked = [value for mark, (_, value, _) in zip(marks, parts) if mark in EDIT_MARKS]
    path, old, new = FILE_LINE.match(marked[0]).group(1), marked[2], marked[4]
    if not old:
        raise EditsRefused(EDITS_REFUSED["empty"].format(number=number))
    if path.startswith("/") or ".." in PurePosixPath(path).parts:
        raise EditsRefused(EDITS_REFUSED["path"].format(number=number, path=path))
    return Edit(number, posixpath.normpath(path), old, new)


def parse_edits(text):
    """The edits of a review report's `## Edits` section, in the report's order.

    The section starts after the report's one heading whose text is `Edits`, when that heading has two `#`.
    It ends before the next heading of one or two `#`, or where the report's parts end.
    In it a heading of three `#` whose text is `Edit <n>` starts an edit, and the edit ends before the next heading.
    The `<n>` is any text that starts with a character other than whitespace.
    Raises EditsRefused for a carriage return, for a NUL character, for a byte order mark as the first character,
    for what report_parts refuses, for a second heading whose text is `Edits`, for any other heading in the section,
    for a fence in the section above its first edit, for a section with no edit, and for the first edit that one_edit
    refuses.
    """
    for key, found in (("return", "\r" in text), ("nul", "\0" in text), ("mark", text.startswith("\ufeff"))):
        if found:
            raise EditsRefused(EDITS_REFUSED[key])
    parts = report_parts(text.split("\n"))
    named = sum(kind == "heading" and value[1] == EDITS_SECTION[1] for kind, value, _ in parts)
    if named > 1:
        raise EditsRefused(EDITS_REFUSED["twice"].format(count=named))
    section, edits = iter(parts), []
    if any((kind, value) == ("heading", EDITS_SECTION) for kind, value, _ in section):
        for kind, value, number in section:
            if kind == "heading":
                level, title = value
                if level < 3:
                    break
                numbered = EDIT_TITLE.fullmatch(title) if level == 3 else None
                if not numbered:
                    raise EditsRefused(EDITS_REFUSED["inside"].format(line=number))
                edits.append((numbered.group(1), []))
            elif edits:
                edits[-1][1].append((kind, value, number))
            elif kind == "fence":
                raise EditsRefused(EDITS_REFUSED["above"].format(line=number))
    if not edits:
        raise EditsRefused(EDITS_REFUSED["none"])
    return tuple(one_edit(number, parts) for number, parts in edits)


def report_edits(restaurant, name):
    """The edits of reports/<name>.

    Refuses a report that cannot be read and one that is not UTF-8. The caller has checked that the entry is a `file`.
    """
    try:
        return parse_edits((restaurant.dir / "reports" / name).read_bytes().decode("utf-8"))
    except OSError as error:
        raise BrigadeError(f"reports/{name} cannot be read: {error.strerror}") from None
    except UnicodeDecodeError:
        raise BrigadeError(f"reports/{name} is not UTF-8") from None
    except EditsRefused as error:
        raise BrigadeError(f"reports/{name}{error}") from None
