import argparse
import fcntl
import fnmatch
import functools
import hashlib
import io
import json
import os
import posixpath
import re
import runpy
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from brigade_lib import seam
from brigade_lib.compact import is_managed
from brigade_lib.shared import BrigadeError, command_words, fence_close, fence_open

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
NEEDS_COMMANDS = ("edits verify needs a build command and a check command; "
                  "run set --build-command <command> --check-command <command>, with none for one the project lacks")
NO_COMMAND = "none"
# Why an edit cannot apply to the reviewed commit.
EDIT_FAILS = {
    "missing": "FAIL {path}: no regular file at {sha}",
    "count": "FAIL {path}: old text of Edit {number} occurs {count} times at {sha}",
    "overlap": "FAIL {path}: Edit {number} overlaps another edit",
}
# One line per compared path, keyed by whether the build changed the path in the scratch copy and whether the copy and the head agree at it.
PATH_LINES = {
    (False, True): "edited {path}",
    (True, True): "built {path}",
    (False, False): "FAIL {path}: differs from {sha} with the edits applied",
    (True, False): "FAIL {path}: differs from what the build writes",
}
# The kind and the execute bit of each tree mode that edits verify compares by content.
BLOB_MODES = {"100644": ("file", False), "100755": ("file", True), "120000": ("link", False)}


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


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    oid: str


@dataclass(frozen=True)
class Digest:
    """What edits verify compares at one path. The kind is `file`, `link`, or `other`, and a link's hash is of its target."""
    kind: str
    sha256: str
    executable: bool


def occurrences(content, old):
    """The offset of every place old starts in content. Places that overlap each count."""
    found, at = [], content.find(old)
    while at != -1:
        found.append(at)
        at = content.find(old, at + 1)
    return found


def apply_edits(contents, edits, sha):
    """({path: new bytes}, lines). A run with any line changes no path.

    contents holds, for each path an edit names, the file's bytes at the reviewed commit, or None when no file is there.
    Every old text is placed in those bytes, never in the result of another edit.
    """
    found = [None if contents[edit.path] is None else occurrences(contents[edit.path], edit.old.encode()) for edit in edits]
    spans = [(edit.path, starts[0], starts[0] + len(edit.old.encode())) if starts is not None and len(starts) == 1 else None
             for edit, starts in zip(edits, found)]
    lines = []
    for index, (edit, starts, span) in enumerate(zip(edits, found, spans)):
        if starts is None:
            case = "missing"
        elif span is None:
            case = "count"
        elif any(other is not None and at != index and other[0] == span[0] and other[1] < span[2] and span[1] < other[2]
                 for at, other in enumerate(spans)):
            case = "overlap"
        else:
            continue
        line = EDIT_FAILS[case].format(path=edit.path, number=edit.number, count=len(starts or ()), sha=sha)
        if line not in lines:
            lines.append(line)
    if lines:
        return {}, lines
    changed = {}
    for path in {edit.path for edit in edits}:
        pieces, at = [], 0
        placed = sorted(((span[1], span[2], edit.new.encode()) for edit, span in zip(edits, spans) if edit.path == path),
                        key=lambda piece: piece[0])
        for start, end, new in placed:
            pieces += [contents[path][at:start], new]
            at = end
        changed[path] = b"".join(pieces) + contents[path][at:]
    return changed, []


def compared_paths(before, after, extracted, edited, built):
    """The paths either commit's tree holds where the two trees differ or the scratch copy changed in the edits or the build, sorted."""
    # open and set write the managed settings file into the project, so a head that sweeps it in is not a worker change.
    return sorted(path for path in before.keys() | after.keys()
                  if not is_managed(path)
                  and (before.get(path) != after.get(path) or extracted.get(path) != edited.get(path)
                       or edited.get(path) != built.get(path)))


def path_lines(paths, edited, built, head, sha):
    """One PATH_LINES line per path, and whether the scratch copy and the head agree at every path."""
    agree = [built.get(path) == head.get(path) for path in paths]
    lines = [PATH_LINES[edited.get(path) != built.get(path), same].format(path=path, sha=sha) for path, same in zip(paths, agree)]
    return lines, all(agree)


def git(project_root, *words):
    return subprocess.run(["git", "-C", project_root, *words], capture_output=True)


def commit_tree(project_root, sha):
    """(the commit's full id, {path: TreeEntry}) of a commit in the project's repository."""
    found = git(project_root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{sha}^{{commit}}")
    if found.returncode:
        raise BrigadeError(f"{sha} is not a commit in {project_root}")
    commit = found.stdout.decode().strip()
    if not commit.startswith(sha.lower()):
        raise BrigadeError(f"{sha} names a commit by something other than the start of its id; pass the commit id")
    tree = {}
    for record in git(project_root, "ls-tree", "-r", "-z", commit).stdout.split(b"\0")[:-1]:
        entry, _, path = record.partition(b"\t")
        mode, _, oid = entry.decode().split(" ")
        tree[os.fsdecode(path)] = TreeEntry(mode, oid)
    return commit, tree


def head_digest(project_root, entry):
    kind, executable = BLOB_MODES.get(entry.mode, ("other", False))
    if kind == "other":
        return Digest(kind, entry.oid, executable)
    blob = git(project_root, "cat-file", "blob", entry.oid).stdout
    return Digest(kind, hashlib.sha256(blob).hexdigest(), executable)


def digests(root):
    """{path: Digest} of every entry under root that is not a directory. No link is followed."""
    found = {}
    for directory, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(directory) / name
            status = path.lstat()
            if stat.S_ISDIR(status.st_mode):
                continue
            relative = path.relative_to(root).as_posix()
            if stat.S_ISLNK(status.st_mode):
                found[relative] = Digest("link", hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest(), False)
            elif stat.S_ISREG(status.st_mode):
                found[relative] = Digest("file", hashlib.sha256(path.read_bytes()).hexdigest(), bool(status.st_mode & stat.S_IXUSR))
            else:
                found[relative] = Digest("other", "", False)
    return found


def exit_code(scratch, command):
    """Run a command in the scratch copy with no shell and no stdin. Its output goes to stderr when it exits non-zero."""
    try:
        result = subprocess.run(command_words(command), cwd=scratch, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as error:
        raise BrigadeError(f"cannot run {command}: {error.strerror}") from None
    if result.returncode:
        sys.stderr.buffer.write(result.stdout)
        sys.stderr.flush()
    return result.returncode


def replay(project_root, reviewed, before, after, edits, build, check, sha):
    """The lines of one verify, and whether it is clean. One scratch copy holds the reviewed commit, then the edits, then the build's output."""
    with seam.tempfile.TemporaryDirectory() as directory:
        scratch = Path(directory)
        archive = git(project_root, "archive", "--format=tar", reviewed).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            # extractall has no filter argument in Python 3.10 before 3.10.12 or in 3.11 before 3.11.4.
            tar.extractall(scratch, **({"filter": "tar"} if hasattr(tarfile, "tar_filter") else {}))
        extracted = digests(scratch)
        files = {path for path, digest in extracted.items() if digest.kind == "file"}
        contents = {edit.path: (scratch / edit.path).read_bytes() if edit.path in files else None for edit in edits}
        changed, lines = apply_edits(contents, edits, sha)
        if lines:
            return lines, False
        for path, data in changed.items():
            (scratch / path).write_bytes(data)
        edited = digests(scratch)
        if build != NO_COMMAND:
            code = exit_code(scratch, build)
            if code:
                return [f"FAIL build: {build} exited {code}"], False
        built = digests(scratch)
        paths = compared_paths(before, after, extracted, edited, built)
        head = {path: head_digest(project_root, after[path]) for path in paths if path in after}
        lines, clean = path_lines(paths, edited, built, head, sha)
        if clean and check != NO_COMMAND:
            code = exit_code(scratch, check)
            lines.append(f"FAIL check: {check} exited {code}" if code else "ok check")
            clean = not code
        return lines, clean
