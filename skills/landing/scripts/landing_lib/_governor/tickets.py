import errno
import fcntl
import os
import re
import stat
from landing_lib import seam
from .base import _Demand

def _slot_table(slots):
    workers = [f"worker-{index}" for index in range(slots)]
    return {"worker": workers, "landing": ["landing-0"], "exclusive": ["landing-0"] + workers}

def _slot_names(kind, slots):
    """Return the slot filenames for kind, in lock order."""
    return _slot_table(slots)[kind]

TICKET = re.compile("([1-9][0-9]*)-(" + "|".join(_slot_table(0)) + ")")

def _conflicts(earlier, later):
    """True when both can need the same slot file. Worker and landing never do."""
    return earlier == later or "exclusive" in (earlier, later)

def _turn(mutex):
    try:
        fcntl.flock(mutex, fcntl.LOCK_EX)
    except OSError as error:
        if error.errno in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOLCK):
            raise seam.LandError("this filesystem does not support the slot governor's ordering lock") from None
        raise

def _parse_slot_count(text):
    """The --slots token as an int. None means the flag was omitted and returns 1."""
    if text is None:
        return 1
    try:
        count = int(text)
    except ValueError:
        count = 0
    if count < 1:
        raise seam.LandError("--slots must be a positive integer")
    return count

def _demand(kind, count, slots):
    """Build a _Demand, or raise LandError for a count that kind or a governor of `slots` worker slots cannot hold."""
    if kind != "worker" and count != 1:
        raise seam.LandError("only a worker slot can take more than one")
    if count < 1:
        raise seam.LandError("--slots must be a positive integer")
    if kind == "worker" and count > slots:
        raise seam.LandError(f"--slots {count} is more than the {slots} worker slots this governor has")
    return _Demand(kind, count)

def _try_take(demand, handles, slots):
    """Flock the slot files demand needs, without blocking. Returns the locked files, or None with none locked.
    Worker needs demand.count files and skips a busy one. Landing needs its one file. Exclusive needs every file."""
    kind = demand.kind
    names = _slot_names(kind, slots)
    wanted = len(names) if kind == "exclusive" else demand.count
    taken = []
    for name in names:
        try:
            fcntl.flock(handles[name], fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if kind == "exclusive":
                break
            continue
        taken.append(handles[name])
        if len(taken) == wanted:
            return taken
    for handle in taken:
        fcntl.flock(handle, fcntl.LOCK_UN)
    return None

def _can_satisfy(kind, handles, slots):
    """True when a probe of this kind could lock now. Holds nothing on return.

    A ticket carries a kind and no count. The worker probe locks one file.
    Exclusive still needs every file.
    """
    taken = _try_take(_Demand(kind, 1), handles, slots)
    for handle in taken or []:
        fcntl.flock(handle, fcntl.LOCK_UN)
    return taken is not None

def _discard_ticket(path):
    """Remove a directory entry that is not a regular file. A regular file stays.

    An empty directory is removed. A symlink is unlinked and its target stays.
    A directory that still has children stays.
    """
    try:
        mode = os.lstat(path).st_mode
        if stat.S_ISDIR(mode):
            os.rmdir(path)
        elif not stat.S_ISREG(mode):
            os.unlink(path)
    except OSError:
        pass

def _drop_aliased(tickets):
    """Unlink every regular-file ticket-shaped name in this directory whose link count is not 1.

    Names that fail TICKET are ignored. An lstat or unlink failure skips that name.
    A name that is not a regular file stays for _open_ticket.
    """
    stale = []
    for name in os.listdir(tickets):
        if TICKET.fullmatch(name) is None:
            continue
        path = tickets / name
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink == 1:
            continue
        stale.append(path)
    for path in stale:
        try:
            os.unlink(path)
        except OSError:
            pass

def _open_ticket(path):
    """Return a read-only file opened for a shared probe, or None when path is not a live ticket.

    O_NONBLOCK keeps a fifo from blocking the open, and O_NOFOLLOW keeps a symlink from being followed.
    An entry whose lstat or open fails is not a live waiter and does not stop the scan.
    After a successful open, an fstat whose link count is not 1 closes the descriptor, calls _drop_aliased on the parent, and returns None.
    An unreadable single-link open returns None and leaves the name.
    """
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        _discard_ticket(path)
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno == errno.ELOOP:
            _discard_ticket(path)
        return None
    try:
        info = os.fstat(descriptor)
    except OSError:
        os.close(descriptor)
        return None
    if info.st_nlink != 1:
        os.close(descriptor)
        _drop_aliased(path.parent)
        return None
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        _discard_ticket(path)
        return None
    return os.fdopen(descriptor, "r")

def _waiting(tickets, before):
    """Return (number, kind) for each live ticket below before, in ticket order. before None means every ticket.

    _drop_aliased runs first. A ticket is live while its waiter holds the exclusive flock on its file, so the shared probe fails.
    Each scanned ticket whose shared probe succeeds is deleted. An unreadable single-link entry is skipped and stays in place. Runs under the turn mutex.
    The probe is a shared lock because an exclusive flock on a read-only open fails with EBADF where flock is a POSIX lock.
    The caller passes its own number, because its own ticket would probe free where flock is per process.
    """
    _drop_aliased(tickets)
    found = sorted((int(match[1]), match[2], match[0]) for match in map(TICKET.fullmatch, os.listdir(tickets))
                   if match and (before is None or int(match[1]) < before))
    live = []
    for number, kind, name in found:
        probe = _open_ticket(tickets / name)
        if probe is None:
            continue
        with probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                live.append((number, kind))
                continue
        (tickets / name).unlink(missing_ok=True)
    return live
