import hashlib
import io
import os
import stat
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from brigade_lib import seam
from brigade_lib.compact import SETTINGS_MODE, SETTINGS_RELATIVE, settings_cap_only
from brigade_lib.shared import BrigadeError, command_words

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


def _cap_only(project_root, before, after):
    """True when the settings file's two tree states differ by nothing but the autoCompactWindow member.

    Every tree entry that holds the settings file must be in SETTINGS_MODE, the mode of the file sync_meta writes, so
    a symbolic link and an executable file are compared whatever else they hold. An earlier state that holds no file
    is a settings file that was created, and a later state that holds no file gets no allowance. The later state must
    hold exactly the cap splice written over the earlier one, or hold nothing but the cap when the earlier holds no
    file.
    """
    if (after is None or after.mode != SETTINGS_MODE
            or (before is not None and before.mode != after.mode)):
        return False
    earlier = None if before is None else git(project_root, "cat-file", "blob", before.oid).stdout
    return settings_cap_only(earlier, git(project_root, "cat-file", "blob", after.oid).stdout)


def compared_paths(project_root, before, after, extracted, edited, built):
    """The paths either commit's tree holds where the two trees differ or the scratch copy changed in the edits or the build, sorted.

    The settings file is compared like every other path, except that it is left out when its two tree states
    differ by nothing but the cap splice and the scratch copy agrees at that path at every stage, meaning the
    extracted, edited, and built digests there are equal.
    """
    found = {path for path in before.keys() | after.keys()
             if before.get(path) != after.get(path) or extracted.get(path) != edited.get(path)
             or edited.get(path) != built.get(path)}
    return sorted(path for path in found
                  if path != SETTINGS_RELATIVE or not _cap_only(project_root, before.get(path), after.get(path))
                  or extracted.get(path) != edited.get(path) or edited.get(path) != built.get(path))


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
        paths = compared_paths(project_root, before, after, extracted, edited, built)
        head = {path: head_digest(project_root, after[path]) for path in paths if path in after}
        lines, clean = path_lines(paths, edited, built, head, sha)
        if clean and check != NO_COMMAND:
            code = exit_code(scratch, check)
            lines.append(f"FAIL check: {check} exited {code}" if code else "ok check")
            clean = not code
        return lines, clean
