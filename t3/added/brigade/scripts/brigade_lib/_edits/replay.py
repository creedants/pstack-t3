import ast
import hashlib
import io
import os
import posixpath
import stat
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from brigade_lib import seam
from brigade_lib._edits.setup import prepare
from brigade_lib.compact import SETTINGS_MODE, SETTINGS_RELATIVE, settings_cap_only
from brigade_lib.shared import BrigadeError, command_words

NEEDS_COMMANDS = ("edits verify needs a build command and a check command; "
                  "run set --build-command <command> --check-command <command>, with none for one the project lacks")
NO_COMMAND = "none"
# The most lines a small code fix changes.
SMALL_FIX_LINES = 20
# The diff algorithms the count asks git for.
GIT_ALGORITHMS = ("myers", "minimal", "patience", "histogram")
SMALL_FIX = ("the applied change counts {count} lines, and a small code fix counts at most {cap}; it needs a second "
             "review. The count is, for each changed file, the largest added plus deleted total git diff --numstat "
             "reports between the file at the reviewed commit and that file with the edits applied under myers, "
             "minimal, patience, and histogram, and every line of both files when git calls either one binary. A path "
             "ending in .md is not counted, and neither is a .py path whose AST is the same once its docstrings are "
             "removed, so in a .py path a change to a comment, a docstring, or formatting alone is not counted. A "
             "change to a printed message or to code on a test line changes that AST and is counted")
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
# The line for the one added path a verify leaves out: this round's change-note fragment.
SKIPPED_LINE = "skipped {path}: this round's change note"
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


def without_docstrings(tree):
    """The tree with every docstring removed.

    A docstring is the first statement of a module, class, function, or async function body when that statement is an
    expression whose value is a string constant.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                node.body = node.body[1:]
    return tree


def ast_dump(source):
    """ast.dump of the source once its docstrings are removed, or None when the source does not parse."""
    try:
        return ast.dump(without_docstrings(ast.parse(source)))
    except (SyntaxError, ValueError):
        return None


def exempt(path, before, after):
    """True when a changed path is wording, which the cap does not count.

    That is a path that ends in .md, and a path that ends in .py when both sides parse and their ASTs are equal once
    every docstring is removed.
    """
    if path.endswith(".md"):
        return True
    if path.endswith(".py"):
        earlier, later = ast_dump(before), ast_dump(after)
        return earlier is not None and later is not None and earlier == later
    return False


def line_count(data):
    """The lines data holds. A newline ends one, and bytes after the last newline make one more."""
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def counting_git(directory):
    """A function that runs one git command in a new bare repository under directory.

    The environment holds no GIT_ variable the caller set, no system or global config, and no attribute file outside
    that directory, so the count depends on the bytes and the installed git alone. A command that exits non-zero raises
    BrigadeError.
    """
    environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1",
                       HOME=str(directory), XDG_CONFIG_HOME=str(directory))
    store = str(directory / "count.git")

    def run(*words, data=None):
        try:
            result = subprocess.run(["git", *words], input=data, capture_output=True, env=environment,
                                    cwd=str(directory))
        except OSError as error:
            raise BrigadeError(f"cannot run git while counting the applied change: {error.strerror}") from None
        if result.returncode:
            raise BrigadeError(f"git exited {result.returncode} while counting the applied change")
        return result.stdout

    run("init", "--bare", "--quiet", store)
    return lambda *words, data=None: run("--git-dir", store, *words, data=data)


def numstat_bound(count, before, after):
    """The lines one changed path adds to the applied change's count.

    The largest added plus deleted total git diff --numstat reports between the two blobs over GIT_ALGORITHMS, and
    every line of both blobs when git calls either one binary.
    """
    if before == after:
        return 0
    old = count("hash-object", "-w", "--stdin", data=before).strip()
    new = count("hash-object", "-w", "--stdin", data=after).strip()
    totals = []
    for algorithm in GIT_ALGORITHMS:
        counted = count("diff", "--numstat", "--no-renames", "--no-ext-diff", "--no-textconv",
                        f"--diff-algorithm={algorithm}", old, new)
        if not counted.strip():
            totals.append(0)
            continue
        added, deleted, _ = counted.split(b"\t", 2)
        if added == b"-":
            return line_count(before) + line_count(after)
        totals.append(int(added) + int(deleted))
    return max(totals)


def applied_count(contents, changed):
    """The lines the applied change counts, which is numstat_bound at each changed path that exempt leaves counted."""
    with seam.tempfile.TemporaryDirectory() as directory:
        count = counting_git(Path(directory))
        return sum(numstat_bound(count, contents[path], data) for path, data in changed.items()
                   if not exempt(path, contents[path], data))


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


def compared_paths(project_root, before, after, extracted, edited, prepared, built):
    """The paths either commit's tree holds where the two trees differ or the scratch copy changed in the edits or the build, sorted.

    The build is measured against the copy after the setup, so a path the setup created and the build left alone is not
    compared. The settings file is compared like every other path, except that it is left out when its two tree states
    differ by nothing but the cap splice and the scratch copy agrees at that path at every stage, meaning the
    extracted, edited, prepared, and built digests there are equal.
    """
    found = {path for path in before.keys() | after.keys()
             if before.get(path) != after.get(path) or extracted.get(path) != edited.get(path)
             or prepared.get(path) != built.get(path)}
    return sorted(path for path in found
                  if path != SETTINGS_RELATIVE or not _cap_only(project_root, before.get(path), after.get(path))
                  or extracted.get(path) != edited.get(path) or edited.get(path) != prepared.get(path)
                  or prepared.get(path) != built.get(path))


def renamed_note(before, after, skipped):
    """The one .md file in the skipped fragment's directory that the reviewed tree holds and the new head does not.

    Only a regular .md file in the fragment's directory is a candidate, so another file there, such as a deleted
    .json, is never the note. The empty string when there is not exactly one such file.
    """
    directory = posixpath.dirname(skipped)
    found = [path for path, entry in before.items()
             if posixpath.dirname(path) == directory and path.endswith(".md") and path not in after
             and BLOB_MODES.get(entry.mode, ("other", False))[0] == "file"]
    return found[0] if len(found) == 1 else ""


def path_lines(paths, prepared, built, head, sha, skipped=""):
    """One line per path, keyed on whether the build changed the path after the setup, and whether the scratch copy and the head agree at every compared path.

    The skipped path prints SKIPPED_LINE instead of its PATH_LINES line, and it decides nothing.
    """
    agree, lines = [], []
    for path in paths:
        if path == skipped:
            lines.append(SKIPPED_LINE.format(path=path))
            continue
        same = built.get(path) == head.get(path)
        agree.append(same)
        lines.append(PATH_LINES[prepared.get(path) != built.get(path), same].format(path=path, sha=sha))
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


def replay(project_root, reviewed, before, after, edits, build, check, sha, skip="", setup=""):
    """The lines of one verify, and whether it is clean. One scratch copy holds the reviewed commit, then the edits, then the setup's output, then the build's output.

    The setup runs after the edits and before the build, in the copy, and a failing setup, or a setup that changes a path
    the reviewed commit holds, returns its one line. A path named by skip is left out of the comparison when the reviewed commit holds no entry at it and the new head
    does, and it prints SKIPPED_LINE instead. When the reviewed commit holds exactly one regular .md file in that
    path's directory that the new head does not, that file is the same note under its old name and is compared at
    skip's digest. A file there that does not end in .md is never the old note, so a deleted .json is not one.
    Raises BrigadeError when the applied change counts more than SMALL_FIX_LINES lines.
    """
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
        count = applied_count(contents, changed)
        if count > SMALL_FIX_LINES:
            raise BrigadeError(SMALL_FIX.format(count=count, cap=SMALL_FIX_LINES))
        for path, data in changed.items():
            (scratch / path).write_bytes(data)
        edited = digests(scratch)
        if setup:
            failure = prepare(scratch, setup)
            if failure:
                return [failure], False
            prepared = digests(scratch)
            touched = sorted(path for path in before if prepared.get(path) != edited.get(path))
            if touched:
                return [f"FAIL setup: {setup} changed {touched[0]}"], False
        else:
            prepared = edited
        if build != NO_COMMAND:
            code = exit_code(scratch, build)
            if code:
                return [f"FAIL build: {build} exited {code}"], False
        built = digests(scratch)
        paths = compared_paths(project_root, before, after, extracted, edited, prepared, built)
        head = {path: head_digest(project_root, after[path]) for path in paths if path in after}
        skipped = skip if skip and skip not in before and skip in after else ""
        if skipped:
            old = renamed_note(before, after, skipped)
            if old:
                head[old] = head[skipped]
        lines, clean = path_lines(paths, prepared, built, head, sha, skipped)
        if clean and check != NO_COMMAND:
            code = exit_code(scratch, check)
            lines.append(f"FAIL check: {check} exited {code}" if code else "ok check")
            clean = not code
        return lines, clean
