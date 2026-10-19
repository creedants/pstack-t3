from __future__ import annotations

import errno
import os
import shutil
import sys
from contextlib import ExitStack, suppress

from . import layout
from . import moves
from . import plans

CHANGED = "the backup is no longer the entry uninstall read"


def _parked(aside, reason):
    parked = moves.park(aside)
    where = f"is kept at {parked}" if parked else f"is still at {aside}"
    return f"{reason}; an entry that took {aside} during the restore {where}"


def _returned(aside, backup, reason):
    stuck = moves.put_back(aside, backup)
    return reason if stuck is None else f"{reason} and {stuck}; it is kept at {aside}"

def place_copy(source, path, source_fd):
    """Copy the entry at `source` beside `path`, hold the copy, then move it. Return Published or a refusal.

    It checks `same_entry` on `source` first. It copies into a SCRAP holder beside `path`. It holds the completed copy open, then `prove_moved` moves the copy and proves the live entry against the held copy. A Published result retires `source` first. A mismatch keeps the backup and its row with a refusal naming the live path. It never compares the copy with the original source descriptor. The finally block discards any entry left at the copy path.
    """
    try:
        if not moves.same_entry(source, source_fd):
            return CHANGED
    except OSError:
        return CHANGED
    with moves.Holder(path, moves.SCRAP) as holder:
        copy = holder.aside
        try:
            if os.path.isdir(source) and not os.path.islink(source):
                shutil.copytree(source, copy, symlinks=True)
            else:
                shutil.copy2(source, copy, follow_symlinks=False)
            with ExitStack() as holds:
                held = plans.hold(copy, holds)
                if held.fd is None:
                    return f"the copy could not be held open to prove it is the entry uninstall read ({held.error})"
                result = prove_moved(copy, path, held.fd)
                if isinstance(result, moves.Published):
                    try:
                        retire(source, source_fd)
                    except FileNotFoundError:
                        pass
                return result
        finally:
            if os.path.lexists(copy):
                moves.discard(copy)

def proved(path, fd):
    """Return Published when `path` names the entry `fd` holds. Else return a refusal naming `path`.

    It moves nothing. It deletes nothing. Only `proved` makes a Published.
    """
    try:
        if moves.same_entry(path, fd):
            return moves.Published(path)
    except OSError:
        pass
    return f"the entry moved to {path} could not be confirmed"

def prove_moved(source, path, fd):
    """Move the entry at `source` to `path`, then prove the live entry. Return Published or a refusal."""
    try:
        code = moves.place(source, path)
    except OSError as error:
        return str(error)
    if code != 0:
        return moves.refusal(path, code)
    return proved(path, fd)

def drop_backup(backup, aside, published, fd):
    """Delete the backup name after the caller reports successful placement in `published`.

    A backup entry that fails the identity check below stays and is reported.
    """
    path = published.path
    try:
        # The rename takes whatever the path names now into a directory only this call uses, so the check and the delete see one entry.
        os.rename(backup, aside)
    except FileNotFoundError:
        return
    if moves.same_entry(aside, fd):
        os.unlink(aside)
        return
    reason = moves.put_back(aside, backup)
    if reason is None:
        print(f"kept {backup}: it is not the backup uninstall restored to {path}")
    else:
        print(f"kept {aside}: it took the place of the backup uninstall restored to {path} and {reason}")

def place_proven(aside, path, fd):
    """Move the proven entry at `aside` to `path`, by a copy when `path` is on another filesystem. Return Published or a refusal.

    It tries `place(aside, path)`. A successful same-filesystem move calls `proved` immediately. Placement failure returns a refusal and leaves `aside` for the caller. Proof failure leaves the live entry where it is and keeps the backup row. The caller's refusal cleanup can remove a held twin at the private name. Only `proved` makes a Published.
    """
    try:
        code = moves.place(aside, path)
    except OSError as error:
        return str(error)
    if code == 0:
        return proved(path, fd)
    if code != errno.EXDEV:
        return moves.refusal(path, code)
    try:
        return place_copy(aside, path, fd)
    except OSError as error:
        return str(error)

def retire(aside, fd):
    """Retire the holder entry at `aside` after a cross-filesystem copy placed its copy.

    It takes a PrivateName only. It moves into a SCRAP directory, then checks its identity there. A match is discarded. A mismatch triggers parking and a message naming the retained path. The identity check and deletion remain separate calls.
    """
    if not isinstance(aside, moves.PrivateName):
        raise TypeError("retire takes a PrivateName only")
    holder, name = os.path.split(aside)
    with moves.Holder(os.path.join(os.path.dirname(holder), name), moves.SCRAP) as scrap:
        os.rename(aside, scrap.aside)
        if moves.same_entry(scrap.aside, fd):
            moves.discard(scrap.aside)
        else:
            parked = moves.park(scrap.aside)
            print(f"kept {parked or scrap.aside}: {moves.STRANGER_LEFT}")

def give_back(taken, backup, aside, reason):
    """Try to return a moved entry to `backup`, or remove a twin's second name or park its replacement.

    Return `reason` with a failed put-back or parking result when there is one. Twin unlink errors are suppressed.
    """
    if taken == moves.TWIN:
        if moves.one_entry(aside, backup):
            with suppress(OSError):
                os.unlink(aside)
            return reason
        return _parked(aside, reason)
    return _returned(aside, backup, reason)

def keep(taken, backup, aside, fd, reason):
    """After `place_proven` returned a refusal, drop the second name for a twin and return the moved entry to `backup` or keep it at `aside`. Park what is not the held entry."""
    if not os.path.lexists(aside):
        return reason
    if moves.same_entry(aside, fd):
        if taken == moves.TWIN:
            with suppress(OSError):
                os.unlink(aside)
            return reason
        return _returned(aside, backup, reason)
    return _parked(aside, reason)

def restore_aside(backup, aside, path, fd):
    """Restore through the private name `aside` where the descriptor cannot be linked. Return why the backup was kept.

    It checks `same_entry` before the move to authorize it, then `place_proven` proves the live entry right after the move. Proof success finishes the restore. Proof failure leaves the live entry where it is and keeps the backup row. The refusal cleanup can remove a held twin at the private name.
    """
    try:
        # A second name keeps the backup name until place_proven reports successful placement.
        os.link(backup, aside, follow_symlinks=False)
        taken = moves.TWIN
    except (OSError, NotImplementedError):
        # A directory cannot be hard linked. The no-replace rename that moves it here is the one that can put it back.
        code = moves.rename_noreplace(backup, aside)
        if code != 0:
            return CHANGED if code == errno.ENOENT else moves.refusal(path, code)
        taken = moves.MOVED
    if not moves.same_entry(aside, fd):
        # For a moved entry it returns the entry to `backup`. For a twin it drops the second name, or parks what took its place.
        return give_back(taken, backup, aside, CHANGED)
    placed = place_proven(aside, path, fd)
    if isinstance(placed, moves.Published):
        if taken == moves.TWIN:
            drop_backup(backup, aside, placed, fd)
        return None
    return keep(taken, backup, aside, fd, placed)

def restore_backup(backup, path, held):
    """Move the backup to `path` only while `path` is empty and the backup is the entry the plan held. Return why it was kept.

    It checks the backup before it links or moves it, links it by descriptor or else gives it a private name by link or rename, then proves the live entry right after it reaches `path`. Proof failure leaves the live entry where it is and keeps the backup row. The refusal cleanup can remove a held twin at the private name.
    """
    if held.fd is None:
        return f"the backup could not be held open to prove it is the entry uninstall read ({held.error})"
    try:
        now = layout.identity(os.lstat(backup))
    except OSError:
        now = None
    # ext4 hands a freed inode number, and with small inodes its whole-second change time, to the next new entry, so equal numbers alone prove nothing.
    # The held descriptor keeps the planned inode in use, so an entry made since has other numbers. The change time also catches one changed in place.
    if now is None or now[:2] != layout.identity(os.fstat(held.fd))[:2] or now != held.entry:
        return CHANGED
    # The backup path can name another entry from here on, so every later step acts on the held entry, on a name this call makes in a holder directory, or on one first moved where only this call looks.
    # Another installer can take `path` after the plan saw it empty. Each step refuses it instead of replacing it.
    code = moves.link_held(held.fd, path)
    if code == errno.EEXIST:
        return moves.refusal(path, code)
    if code == 0:
        proven = proved(path, held.fd)
        if not isinstance(proven, moves.Published):
            return proven
        try:
            holder = moves.Holder(backup)
        except FileNotFoundError:
            return None
        with holder:
            drop_backup(backup, holder.aside, proven, held.fd)
            return None
    try:
        holder = moves.Holder(backup)
    except FileNotFoundError:
        # The backup left with its directory, so no name is left to clean up or to restore from.
        return CHANGED
    with holder:
        return restore_aside(backup, holder.aside, path, held.fd)
