from __future__ import annotations

import os
from contextlib import ExitStack, suppress
from dataclasses import dataclass

from . import layout
from . import moves
from . import records

@dataclass(frozen=True)
class Stray:
    """One thing found in a holder directory, and the one action the records allow.

    `kind` is "empty" for a holder with nothing in it, where `path` is the holder. Otherwise `path` is the entry, and
    `kind` is "home" to return it to `home`, "spare" to remove a second name of the entry at `twin`, "own" to remove
    this checkout's link whose `home` is taken, or "left" for an entry that stays because of `why`.
    `drop` is the backup path of a row that a "spare" finishes.
    """
    kind: str
    path: str
    home: str = ""
    twin: str = ""
    drop: str = ""
    why: str = ""

def holder_parents(scope, user, state, selected):
    groups, _refusals = layout.layout(scope, user, selected)
    filed_under = set(selected)
    for directory, harnesses in groups:
        filed_under.update(harnesses)
        yield "skills", directory
    backups = os.path.join(state, "backups")
    for stamp in moves.listing(backups):
        for name in moves.listing(os.path.join(backups, stamp)):
            if name in layout.HARNESSES and name not in filed_under:
                continue
            yield "backups", os.path.join(backups, stamp, name)

def owns(view, root, home):
    """Whether this checkout's claim, or a link row for it, names the slot of `home`."""
    return layout.slot_of(home) in records.records(view, root)

def contested_by(backup):
    if not os.path.lexists(backup):
        return None
    parent, name = os.path.split(backup)
    for _holder, directory in moves.holders(parent):
        aside = os.path.join(directory, name)
        if os.path.lexists(aside) and not moves.one_entry(aside, backup):
            return aside
    return None

def held_beside(backup):
    """Return the path of an entry named os.path.basename(backup) in a HOLDER directory in os.path.dirname(backup), or None."""
    parent, name = os.path.split(backup)
    # os.listdir raises ValueError for a path that holds a NUL byte.
    with suppress(ValueError):
        for _holder, directory in moves.holders(parent):
            aside = os.path.join(directory, name)
            if os.path.lexists(aside):
                return aside
    return None

def judge(view, root, side, aside, home):
    """Return the Stray for the entry at `aside`, which a HOLDER directory holds for `home`. Reads only."""
    if moves.one_entry(aside, home):
        return Stray("spare", aside, home, twin=home)
    rows = [row for row in view.backups if row.backup == home] if side == "backups" else []
    if rows:
        if os.path.lexists(home):
            return Stray("left", aside, home, why=f"{home} is the recorded backup of {rows[-1].original} and another entry is there now")
        for row in rows:
            if moves.one_entry(aside, row.original):
                return Stray("spare", aside, home, twin=row.original, drop=row.backup)
        return Stray("home", aside, home)
    if side == "skills" and owns(view, root, home) and layout.proves(root, aside, home):
        return Stray("own" if os.path.lexists(home) else "home", aside, home)
    if side == "backups":
        return Stray("left", aside, home, why=f"no backup record names {home}")
    return Stray("left", aside, home, why=f"it is not a link this checkout recorded at {home}")

def survey(view, scope, user, state, root, selected):
    found = []
    for side, parent in holder_parents(scope, user, state, selected):
        for name, directory in moves.holders(parent):
            entries = moves.listing(directory)
            if not entries:
                found.append(Stray("empty", directory))
            for entry in entries:
                aside, home = os.path.join(directory, entry), os.path.join(parent, entry)
                if name.startswith(moves.STRANGER):
                    found.append(Stray("left", aside, home, why=moves.STRANGER_LEFT))
                elif name.startswith(moves.SCRAP):
                    found.append(Stray("left", aside, home, why=moves.SCRAP_LEFT))
                else:
                    found.append(judge(view, root, side, aside, home))
    return tuple(found)

def backup_place(state, backup):
    """Return (stamp, harness) when `backup` is spelled <state>/backups/<stamp>/<harness>/<name>, else None.

    The inverse of the path `execute` builds for a move. It compares text only and resolves nothing.
    """
    top = os.path.join(str(state), "backups") + os.sep
    if not backup.startswith(top):
        return None
    parts = backup[len(top):].split(os.sep)
    if len(parts) != 3 or any(part in ("", ".", "..") for part in parts):
        return None
    return parts[0], parts[1]

def prune(state, backup):
    """Remove the <harness> directory `backup` was in if it is empty, then its <stamp> directory if that is empty.

    It removes only empty directories at those two levels, under `state`/backups. It never follows a symlink at
    backups/, <stamp>, or <harness>. A symlink at `state` or above it is followed like any other path to the state
    directory, so a state directory kept behind a symlink is pruned too.
    It removes nothing unless `backup_place` matches `backup` under `state` as given or as `os.path.abspath` spells it.
    Under the second spelling `empty_out_matched` does the removing, with that spelling as its `recorded`.
    Call it only inside `locked`, after this run took the entry at `backup` out. It never raises and prints nothing.
    """
    place = backup_place(state, backup)
    if place is not None:
        empty_out(state, *place)
        return
    # tempfile.mkdtemp returns an absolute path from Python 3.12 on, so a row can hold that spelling of a state path given relative or with "..".
    try:
        spelled = os.path.abspath(state)
    except OSError:
        return
    place = backup_place(spelled, backup)
    if place is not None:
        # abspath removes ".." as text and the system applies it after following a symlink, so the two spellings can name two directories.
        empty_out_matched(state, spelled, *place)

def empty_out(state, stamp, harness=None):
    """Remove <state>/backups/<stamp>/<harness> if `harness` is given and it is empty, then <stamp> if it is empty.

    The first rmdir that fails ends it. It never follows a symlink at backups/ or <stamp>.
    Call it only inside `locked`. It never raises and prints nothing.
    """
    if os.rmdir not in os.supports_dir_fd:
        return
    # O_NOFOLLOW refuses a symlink at backups/ or <stamp>, and rmdir refuses a symlink, a file, and a directory that holds anything.
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with suppress(OSError, ValueError):
        top = os.open(os.path.join(str(state), "backups"), flags)
        try:
            inner = os.open(stamp, flags, dir_fd=top)
            try:
                if harness is not None:
                    os.rmdir(harness, dir_fd=inner)
            finally:
                os.close(inner)
            os.rmdir(stamp, dir_fd=top)
        finally:
            os.close(top)

def empty_out_matched(state, recorded, stamp, harness):
    """Remove <state>/backups/<stamp>/<harness> if it is empty, then <stamp> if it is empty, when both match those under `recorded`.

    Under `state` and under `recorded` it opens backups/ by path, then <stamp> under that descriptor, then <harness>
    under the <stamp> descriptor, all with O_RDONLY | O_DIRECTORY | O_NOFOLLOW. To match, the two <harness> descriptors
    must have one device and inode, and so must the two <stamp> descriptors, read while all six are open. A failed open
    or a pair that does not match ends it with nothing removed. It removes <harness> by name under the <stamp>
    descriptor it compared, and <stamp> by name under the backups descriptor it opened that <stamp> from.
    The first rmdir that fails ends it. Call it only inside `locked`. It never raises and prints nothing.
    """
    if os.rmdir not in os.supports_dir_fd:
        return
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with suppress(OSError, ValueError), ExitStack() as stack:
        def opened(path, dir_fd=None):
            descriptor = os.open(path, flags, dir_fd=dir_fd)
            stack.callback(os.close, descriptor)
            return descriptor

        def levels(root):
            top = opened(os.path.join(str(root), "backups"))
            inner = opened(stamp, top)
            return top, inner, opened(harness, inner)

        top, inner, leaf = levels(state)
        _recorded_top, recorded_inner, recorded_leaf = levels(recorded)
        if os.path.samestat(os.fstat(leaf), os.fstat(recorded_leaf)) and os.path.samestat(os.fstat(inner), os.fstat(recorded_inner)):
            os.rmdir(harness, dir_fd=inner)
            os.rmdir(stamp, dir_fd=top)

def settle(strays, state, root, dry_run):
    """Act on each Stray and print one line for each that held an entry. Without `dry_run`, call it only inside `locked`.

    Every move is `place`, so nothing is overwritten. Only a second name and this checkout's own link are ever deleted,
    and a holder is removed only once it is empty. With `dry_run` it prints what it would do and changes nothing.
    """
    recovered, removed = ("would recover", "would remove") if dry_run else ("recovered", "removed")
    dropped = []
    for stray in strays:
        try:
            if stray.kind == "empty":
                if not dry_run:
                    with suppress(OSError):
                        os.rmdir(stray.path)
            elif stray.kind == "left":
                print(f"left {stray.path}: {stray.why}")
            elif stray.kind == "home":
                reason = None if dry_run else moves.put_back(stray.path, stray.home)
                if reason is None:
                    print(f"{recovered} {stray.home} from {stray.path}")
                else:
                    print(f"left {stray.path}: {reason}")
            elif stray.kind == "spare":
                if not dry_run:
                    if not moves.one_entry(stray.path, stray.twin):
                        print(f"left {stray.path}: it is no longer a second name for {stray.twin}")
                        continue
                    os.unlink(stray.path)
                    if stray.drop:
                        records.patch_legacy(state, (), (), (), (stray.drop,), False)
                        dropped.append(stray.drop)
                print(f"{removed} {stray.path}: it was a second name for {stray.twin}")
            elif stray.kind == "own":
                if not dry_run:
                    if not layout.proves(root, stray.path, stray.home):
                        print(f"left {stray.path}: it is no longer this checkout's link")
                        continue
                    os.unlink(stray.path)
                print(f"{removed} {stray.path}: it is this checkout's link and {stray.home} is taken")
        except OSError as error:
            print(f"left {stray.path}: {error}")
    if not dry_run:
        for holder in sorted({os.path.dirname(stray.path) for stray in strays if stray.kind != "empty"}):
            # rmdir refuses a directory that is not empty, so an entry that was left stays.
            with suppress(OSError):
                os.rmdir(holder)
        for backup in dropped:
            prune(state, backup)
