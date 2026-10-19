import contextlib
import fcntl
import os
import signal
import stat
import sys
import time
from landing_lib import seam
from .base import _Admission, _Demand, _Hold, governor_dir, governor_slots
from .holders import _busy_names, _clear, _format_wait, _live_holders, _release_records, _report_due
from .tickets import _can_satisfy, _conflicts, _demand, _slot_names, _try_take, _turn, _waiting

def _raise_exit(signum, frame):
    raise SystemExit(128 + signum)

@contextlib.contextmanager
def _held(demand, repo, command, cap=None, reason=None):
    """Hold the slots demand needs, publish this holder, and clear the records while the flocks are still held."""
    admission = _admit(demand, repo, command, cap, reason)
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGHUP)}
    for signum in previous:
        signal.signal(signum, _raise_exit)
    try:
        yield _Hold(admission.acquired, admission.names)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        _clear()
        for handle in admission.handles:
            handle.close()
        _release_records(admission.records)

def _ticket_named(ticket):
    """True when this open file is still the sole regular-file name of its inode."""
    if ticket is None:
        return False
    try:
        info = os.fstat(ticket.fileno())
        named = os.lstat(ticket.name)
    except OSError:
        return False
    return (stat.S_ISREG(named.st_mode) and named.st_nlink == 1
            and (named.st_dev, named.st_ino) == (info.st_dev, info.st_ino))

def _mint_ticket(tickets, kind, waiting):
    """Create one exclusively locked ticket and return its number and file.

    The name is mode 0o600 from the moment it appears.
    """
    number = max((live for live, _ in waiting), default=0) + 1
    while os.path.lexists(tickets / f"{number}-{kind}"):
        number += 1
    previous = os.umask(0o077)
    try:
        # open asks for 0o666 and the 0o077 umask leaves 0o600, so the name is owner-readable when it appears.
        ticket = open(tickets / f"{number}-{kind}", "x")
    finally:
        os.umask(previous)
    try:
        fcntl.flock(ticket, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        ticket.close()
        raise
    return number, ticket

def _unlink_our_name(ticket):
    """Unlink ticket.name when lstat still names this open file. A missing path or a different inode stays."""
    try:
        info = os.lstat(ticket.name)
        held = os.fstat(ticket.fileno())
    except OSError:
        return
    if (info.st_dev, info.st_ino) != (held.st_dev, held.st_ino):
        return
    try:
        os.unlink(ticket.name)
    except FileNotFoundError:
        pass

def _admit(demand, repo=None, command=None, cap=None, reason=None):
    """Take the slots demand needs, or wait for them behind a ticket named with demand.kind. Returns an _Admission.

    handles are every opened slot file. names are the locked ones.
    acquired is _cap_monotonic() taken after the slot flocks succeed and before the holder records are written.
    A worker demand whose count passes the governor's worker slots during the wait removes its ticket and raises LandError.
    """
    kind = demand.kind
    directory = governor_dir()
    tickets = directory / "tickets"
    tickets.mkdir(parents=True, exist_ok=True)
    slots = governor_slots()
    handles = {name: open(directory / name, "a") for name in _slot_names("exclusive", slots)}
    mutex = ticket = number = None
    records = []
    try:
        mutex = open(directory / "tickets.lock", "a")
        started, reported = time.time(), None
        while True:
            _turn(mutex)
            try:
                _demand(kind, demand.count, governor_slots())
            except seam.LandError:
                if ticket is not None:
                    _unlink_our_name(ticket)
                raise
            waiting = _waiting(tickets, number)
            earlier = {waiter for _, waiter in waiting if _conflicts(waiter, kind)}
            taken = None
            if not any(_can_satisfy(waiter, handles, slots) for waiter in earlier):
                taken = _try_take(demand, handles, slots)
            if taken is not None:
                acquired = seam._cap_monotonic()
            if taken is None and not _ticket_named(ticket):
                if ticket is not None:
                    ticket.close()
                    ticket = None
                number, ticket = _mint_ticket(tickets, kind, waiting)
            if taken is None:
                busy = _busy_names(handles, _slot_names(kind, slots))
            else:
                records = seam._publish(kind, handles, taken, repo, command, cap, reason)
            if taken is not None and ticket is not None:
                _unlink_our_name(ticket)
            fcntl.flock(mutex, fcntl.LOCK_UN)
            if taken is not None:
                names = [name for name, handle in handles.items() if handle in taken]
                return _Admission(list(handles.values()), records, acquired, names)
            waited = time.time() - started
            if _report_due(waited, reported):
                line = _format_wait(waited, _live_holders(busy))
                try:
                    print(line, file=sys.stderr, flush=True)
                except (OSError, ValueError):
                    pass
                reported = waited
            time.sleep(0.5)
    except BaseException:
        _release_records(records)
        for handle in handles.values():
            handle.close()
        raise
    finally:
        for handle in (mutex, ticket):
            if handle is not None:
                handle.close()

def _show_hold(pool, names):
    os.environ["LAND_SLOT"] = pool
    os.environ["LAND_SLOT_FILES"] = " ".join(sorted(names))

def _hide_hold():
    os.environ.pop("LAND_SLOT", None)
    os.environ.pop("LAND_SLOT_FILES", None)

@contextlib.contextmanager
def exclusive_slot(repo=None, command=None, cap=None, reason=None):
    """Hold every slot, landing included, so a benchmark runs on a quiet machine.
    It waits without holding any slot, so other callers keep taking free slots until every slot is free at once.
    A later conflicting caller that waits on this governor leaves the slots when this caller could take every one of them."""
    if os.environ.get("LAND_SLOT"):
        raise seam.LandError("an exclusive slot must be the outermost slot; this command already runs inside one")
    with _held(_Demand("exclusive", 1), repo, command, cap, reason) as hold:
        _show_hold("exclusive", hold.names)
        try:
            yield hold.acquired
        finally:
            _hide_hold()

@contextlib.contextmanager
def slot(pool="worker", repo=None, command=None, *, count=1):
    """Hold `count` worker slots, or one slot of another pool. Landing checks use their own one-slot pool so workers never starve the queue.
    A command already inside a slot (LAND_SLOT set) runs without taking another, whatever its count, so nesting cannot deadlock.
    A caller that shares this governor leaves a free slot for an earlier conflicting ticket that can take it.
    A count above one locks that many worker files in one admission or none."""
    if os.environ.get("LAND_SLOT"):
        yield
        return
    with _held(_demand(pool, count, governor_slots()), repo, command) as hold:
        _show_hold(pool, hold.names)
        try:
            yield
        finally:
            _hide_hold()
