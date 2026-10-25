import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from brigade_lib._edits.replay import BLOB_MODES, NO_COMMAND, SMALL_FIX, SMALL_FIX_LINES, applied_count, apply_edits, commit_tree, digests, exit_code
from brigade_lib._edits.setup import prepare
from brigade_lib.scope import covered
from brigade_lib.shared import BrigadeError


def _git(root, *words, data=None):
    try:
        return subprocess.run(["git", "-C", str(root), *words], input=data, capture_output=True)
    except OSError as error:
        raise BrigadeError(f"cannot run git: {error.strerror}") from None


def _blob(root, oid):
    found = _git(root, "cat-file", "blob", oid)
    if found.returncode:
        raise BrigadeError(f"cannot read {oid} in {root}")
    return found.stdout


def _tip(root, branch):
    found = _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    return None if found.returncode else found.stdout.decode().strip()


def _refuse_inside(root, dish, target):
    """Refuse a worktree path that lies in the checkout, a registered worktree, or the git directory."""
    places = {Path(root).resolve()}
    for word in ("--git-dir", "--git-common-dir"):
        found = _git(root, "rev-parse", word)
        if found.returncode:
            raise BrigadeError(f"{dish}: cannot read the git directory of {root}")
        places.add((Path(root) / found.stdout.decode().strip()).resolve())
    listing = _git(root, "worktree", "list", "--porcelain")
    if listing.returncode:
        raise BrigadeError(f"{dish}: cannot list the worktrees of {root}")
    for line in listing.stdout.decode(errors="replace").splitlines():
        if line.startswith("worktree "):
            places.add(Path(line[len("worktree "):]).resolve())
    for place in sorted(places):
        if target == place or place in target.parents:
            raise BrigadeError(f"{dish}: worktree {target} is inside {place}")


def _cleanup(root, worktree, branch, added, made, new_branch):
    """Undo what this run created and return what could not be undone. It prunes nothing else."""
    left = []
    if added:
        _git(root, "worktree", "unlock", worktree)
        removed = _git(root, "worktree", "remove", "--force", worktree)
        if removed.returncode:
            left.append(f"worktree {worktree}")
    if made and not left:
        shutil.rmtree(worktree, ignore_errors=True)
        if os.path.lexists(worktree):
            left.append(f"directory {worktree}")
    if new_branch and _tip(root, branch) is not None and _git(root, "branch", "-D", branch).returncode:
        left.append(f"branch {branch}")
    return left


def apply_review(root, dish, name, edits, from_sha, branch, worktree, build, setup, generated):
    """Apply a pass-with-edits report's edits on a new branch in a new git worktree and commit them.

    The command's own writes go to the new worktree, the new branch, and the git records that
    worktree needs. The setup and build commands run in that worktree with the caller's access, and
    the build runs the edited code. The edit text is a model's review output, so it is untrusted and
    can run code with the caller's access during setup and the build, as it does in `edits verify`
    and in a fix worker's test runs. That is an accepted limit. The caller is the coordinator, and
    it must read the edits it applies.
    """
    full, tree = commit_tree(root, from_sha)
    seen = {}
    for edit in edits:
        entry = tree.get(edit.path)
        kind = BLOB_MODES.get(entry.mode, ("other", False))[0] if entry is not None else "other"
        if kind != "file":
            raise BrigadeError(f"{dish}: Edit {edit.number} names {edit.path}; no regular file at {from_sha}")
        if edit.path not in seen:
            seen[edit.path] = _blob(root, entry.oid)
    contents = {edit.path: seen[edit.path] for edit in edits}
    changed, lines = apply_edits(contents, edits, from_sha)
    if lines:
        raise BrigadeError(f"{dish}: edits not applied, " + "; ".join(lines))
    for edit in edits:
        if covered(edit.path, generated or ()):
            raise BrigadeError(f"{dish}: Edit {edit.number} names {edit.path}; {edit.path} is generated output")
    count = applied_count(contents, changed)
    if count > SMALL_FIX_LINES:
        raise BrigadeError(SMALL_FIX.format(count=count, cap=SMALL_FIX_LINES))
    if all(data == contents[path] for path, data in changed.items()):
        raise BrigadeError(f"{dish}: edits change nothing at {from_sha}")
    if _git(root, "check-ref-format", "--branch", branch).returncode:
        raise BrigadeError(f"{dish}: {branch} is not a valid branch name")
    if _tip(root, branch) is not None:
        raise BrigadeError(f"{dish}: branch {branch} already exists")
    made = worktree is None
    if made:
        target = Path(tempfile.mkdtemp(prefix=f"brigade-{dish}-edits-"))
    else:
        if os.path.lexists(worktree):
            raise BrigadeError(f"{dish}: worktree {worktree} already exists")
        target = Path(worktree).resolve()
        _refuse_inside(root, dish, target)
    absolute = str(target)
    added = _git(root, "worktree", "add", "-b", branch, absolute, full)
    if added.returncode:
        detail = (added.stderr or added.stdout).decode(errors="replace").strip().splitlines()
        left = _cleanup(root, absolute, branch, False, made, _tip(root, branch) == full)
        raise BrigadeError(f"{dish}: cannot create worktree: {detail[0] if detail else added.returncode}"
                           + (f"; cleanup incomplete: {', '.join(left)}" if left else ""))
    try:
        return _apply_in(absolute, dish, name, changed, tree, from_sha, full, branch, build, setup)
    except BaseException as error:
        left = _cleanup(root, absolute, branch, True, False, True)
        if left and isinstance(error, BrigadeError):
            raise BrigadeError(f"{error}; cleanup incomplete: {', '.join(left)}") from None
        raise


def _apply_in(absolute, dish, name, changed, tree, from_sha, full, branch, build, setup):
    for path, data in changed.items():
        try:
            (Path(absolute) / path).write_bytes(data)
        except OSError as error:
            raise BrigadeError(f"{dish}: cannot write {path}: {error.strerror}") from None
    edited = digests(absolute)
    build_text = (build or "").strip()
    skip_build = not build_text or build_text == NO_COMMAND
    if setup and setup.strip():
        failure = prepare(absolute, setup)
        if failure:
            raise BrigadeError(failure)
        prepared = digests(absolute)
        touched = sorted(path for path in tree if prepared.get(path) != edited.get(path))
        if touched:
            raise BrigadeError(f"FAIL setup: {setup} changed {touched[0]}")
    else:
        prepared = edited
    if skip_build:
        built = prepared
    else:
        code = exit_code(absolute, build_text)
        if code:
            raise BrigadeError(f"FAIL build: {build_text} exited {code}")
        built = digests(absolute)
    wanted = set(changed)
    for path in set(prepared) | set(built):
        if prepared.get(path) != built.get(path):
            wanted.add(path)
    ordered = sorted(wanted)
    if _git(absolute, "reset", "-q", full).returncode:
        raise BrigadeError(f"{dish}: cannot reset the index to {full}")
    data = b"\0".join(os.fsencode(f":(literal){path}") for path in ordered) + (b"\0" if ordered else b"")
    staged = _git(absolute, "add", "-A", "--pathspec-from-file", "-",
                  "--pathspec-file-nul", data=data)
    if staged.returncode:
        raise BrigadeError(f"{dish}: cannot stage: {staged.returncode}")
    listed = _git(absolute, "diff", "--cached", "--name-only", "-z", full)
    extra_paths = set(os.fsdecode(item) for item in listed.stdout.split(b"\0") if item) - wanted
    if listed.returncode or extra_paths:
        raise BrigadeError(f"{dish}: the index holds {sorted(extra_paths)[0] if extra_paths else 'unreadable paths'}, "
                           "which no edit or build output names")
    ident = _git(absolute, "var", "GIT_COMMITTER_IDENT")
    extra = [] if not ident.returncode else ["-c", "user.name=brigade", "-c", "user.email=brigade@localhost"]
    subject = f"{dish}: apply the review's edits"
    body = f"Applies the review's edits from reports/{name} at {full}."
    committed = _git(absolute, *extra, "commit", "-m", subject, "-m", body)
    if committed.returncode:
        detail = (committed.stderr or committed.stdout).decode(errors="replace").strip().splitlines()
        raise BrigadeError(f"{dish}: edits change nothing at {from_sha}" if not detail else
                           f"{dish}: cannot commit: {detail[0]}")
    head = _git(absolute, "rev-parse", "HEAD")
    if head.returncode:
        raise BrigadeError(f"{dish}: cannot read the new head")
    lines = [f"{dish}: review edits applied on {branch}", f"head {head.stdout.decode().strip()}", f"worktree {absolute}"]
    return "\n".join((["no build command recorded; the build was skipped"] if skip_build else []) + lines)
