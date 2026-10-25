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


def _cleanup(root, worktree, branch, created):
    _git(root, "worktree", "remove", "--force", str(worktree))
    _git(root, "branch", "-D", branch)
    _git(root, "worktree", "prune")
    if created:
        try:
            shutil.rmtree(worktree, ignore_errors=True)
        except Exception:
            pass


def apply_review(root, dish, name, edits, from_sha, branch, worktree, build, setup, generated):
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
    if not _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode:
        raise BrigadeError(f"{dish}: branch {branch} already exists")
    created = worktree is None
    if created:
        target = Path(tempfile.mkdtemp(prefix=f"brigade-{dish}-edits-"))
    else:
        target = Path(worktree)
        if target.exists():
            empty = target.is_dir() and not any(target.iterdir())
            if not empty:
                raise BrigadeError(f"{dish}: worktree {target} exists and is not an empty directory")
    absolute = str(target.resolve())
    added = _git(root, "worktree", "add", "-b", branch, absolute, full)
    if added.returncode:
        detail = (added.stderr or added.stdout).decode(errors="replace").strip().splitlines()
        _cleanup(root, absolute, branch, created)
        raise BrigadeError(f"{dish}: cannot create worktree: {detail[0] if detail else added.returncode}")
    failed = True
    try:
        for path, data in changed.items():
            (Path(absolute) / path).write_bytes(data)
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
            print("no build command recorded; the build was skipped")
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
        data = b"\0".join(os.fsencode(f":(literal){path}") for path in ordered) + (b"\0" if ordered else b"")
        staged = _git(absolute, "add", "-A", "--pathspec-from-file", "-",
                      "--pathspec-file-nul", data=data)
        if staged.returncode:
            raise BrigadeError(f"{dish}: cannot stage: {staged.returncode}")
        ident = _git(absolute, "var", "GIT_COMMITTER_IDENT")
        extra = [] if not ident.returncode else ["-c", "user.name=brigade", "-c", "user.email=brigade@localhost"]
        subject = f"{dish}: apply the review's edits"
        body = f"Applies the review's edits from reports/{name} at {full}."
        committed = _git(absolute, *extra, "commit", "--no-verify", "-m", subject, "-m", body)
        if committed.returncode:
            detail = (committed.stderr or committed.stdout).decode(errors="replace").strip().splitlines()
            raise BrigadeError(f"{dish}: edits change nothing at {from_sha}" if not detail else
                               f"{dish}: cannot commit: {detail[0]}")
        head = _git(absolute, "rev-parse", "HEAD")
        if head.returncode:
            raise BrigadeError(f"{dish}: cannot read the new head")
        failed = False
        return f"{dish}: review edits applied on {branch}\nhead {head.stdout.decode().strip()}\nworktree {absolute}"
    finally:
        if failed:
            _cleanup(root, absolute, branch, created)
