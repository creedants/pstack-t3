OFD_SLOT_SCRIPT = r'''
import argparse
import contextlib
import errno
import fcntl
import json
import math
import os
import signal
import struct
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path


class LandError(Exception):
    pass


def state_home():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "pstack-t3"


def governor_dir():
    return state_home() / "governor"


def governor_slots():
    config = governor_dir() / "governor.json"
    if config.exists():
        return max(1, int(json.loads(config.read_text()).get("slots", 1)))
    return max(1, min(4, (os.cpu_count() or 4) // 4))


TURN_MUTEX = 4096
TURN_KIND = 1048576
TURN_PLACE = 2097152
TICKET_KINDS = {b"w": "worker", b"l": "landing", b"e": "exclusive"}
REPORT_AFTER = 15
REPORT_EVERY = 60
EXCLUSIVE_REASON_MAX = 80
_Holder = namedtuple("_Holder", "kind pid repo started command cap reason")
_UNKNOWN = _Holder(None, None, None, None, None, None, None)


def _slot_names(kind, slots):
    """Return the slot filenames for kind, in lock order."""
    workers = [f"worker-{index}" for index in range(slots)]
    return {"worker": workers, "landing": ["landing-0"], "exclusive": ["landing-0"] + workers}[kind]


def _conflicts(earlier, later):
    """True when both can need the same slot file. Worker and landing never do."""
    return earlier == later or "exclusive" in (earlier, later)


def _ofd_excl(fd, byte, blocking):
    """Take an exclusive OFD lock on one byte of fd. Nonblocking returns False when the byte is already held."""
    try:
        # F_OFD_SETLK is 37 and F_OFD_SETLKW is 38 on Linux. This fcntl module does not name them.
        fcntl.fcntl(fd, 38 if blocking else 37, struct.pack("hhqqi4x", fcntl.F_WRLCK, os.SEEK_SET, byte, 1, 0))
    except OSError as error:
        if error.errno in (errno.EAGAIN, errno.EACCES):
            return False
        if error.errno in (errno.EINVAL, errno.ENOTSUP):
            raise LandError("this filesystem does not support the slot governor's ordering lock") from None
        raise
    return True


def _ofd_unlock(fd, byte):
    fcntl.fcntl(fd, 37, struct.pack("hhqqi4x", fcntl.F_UNLCK, os.SEEK_SET, byte, 1, 0))


def _try_take(kind, handles, slots):
    """Flock the slot files kind needs, without blocking. Returns the locked files, or None with none locked.
    Worker and landing need one file. Exclusive needs every file."""
    names = _slot_names(kind, slots)
    wanted = len(names) if kind == "exclusive" else 1
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
    """True when a waiter of kind could take its slots now. Holds nothing on return."""
    taken = _try_take(kind, handles, slots)
    for handle in taken or []:
        fcntl.flock(handle, fcntl.LOCK_UN)
    return taken is not None


def _earlier_waiters(gate, ticket, kind):
    """Return the kinds of the live tickets that conflict with kind and came before this caller.
    A ticket is live while its byte is locked. Runs under the turn mutex."""
    low, upcoming = struct.unpack("<QQ", os.pread(gate, 16, 0).ljust(16, b"\0"))
    first = low = low or 1
    kinds = set()
    live = False
    for number in range(low, upcoming):
        if number == ticket:
            break
        if _ofd_excl(gate, TURN_PLACE + number, False):
            _ofd_unlock(gate, TURN_PLACE + number)
            if not live:
                low = number + 1
            continue
        live = True
        earlier = TICKET_KINDS[os.pread(gate, 1, TURN_KIND + number)]
        if _conflicts(earlier, kind):
            kinds.add(earlier)
    if low != first or not upcoming:
        os.pwrite(gate, struct.pack("<QQ", low, max(upcoming, 1)), 0)
    return kinds


def _take_ticket(gate, place, kind):
    """Issue the next ticket to this caller and lock its byte on place. Runs under the turn mutex."""
    low, ticket = struct.unpack("<QQ", os.pread(gate, 16, 0))
    os.pwrite(gate, struct.pack("<QQ", low, ticket + 1), 0)
    os.pwrite(gate, kind[:1].encode(), TURN_KIND + ticket)
    _ofd_excl(place, TURN_PLACE + ticket, False)
    return ticket


def _short_command(command):
    """The wrapped command's first 80 characters, with CR and LF flattened to spaces."""
    if not command:
        return None
    return command[:80].replace("\r\n", " ").replace("\r", " ").replace("\n", " ") or None


def _repo_text(repo):
    """The repository as an absolute path string, or None when the path cannot be resolved.

    A repo of None selects the working directory.
    """
    try:
        return os.path.abspath(repo or os.getcwd())
    except (OSError, ValueError, TypeError):
        return None


def _hold_path(name):
    return governor_dir() / "holds" / name


def _dump_holder(path, holder):
    """Write one holder record atomically, replacing whatever the previous holder left.

    The record is written to a temporary file whose descriptor is already locked, and that inode
    is renamed into place, so the record lock travels with the record.
    Returns the descriptor, still locked, for the caller to release when it lets the slot go.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o666)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.pwrite(descriptor, json.dumps(dict(zip(_Holder._fields, holder))).encode(), 0)
        os.replace(temporary, path)
    except BaseException:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    return descriptor


def _release_records(records):
    """Unlock and close the record descriptors this process published."""
    for descriptor in records:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _finite(value):
    """True for a start time a record may hold and formatting can use."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _short_reason(text):
    """One line of reason text, or None.

    Keeps characters for which str.isprintable() is true, strips the ends,
    and cuts to EXCLUSIVE_REASON_MAX. Reads that limit at call time.
    Tabs, newlines, and other non-printable characters are deleted.
    """
    if not isinstance(text, str):
        return None
    kept = "".join(character for character in text if character.isprintable()).strip()
    return kept[:EXCLUSIVE_REASON_MAX] or None


def _cap_seconds(value):
    """A positive finite cap in seconds, or None. A bool is not a cap."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _read_holder(path):
    """The holder a record names, with each unreadable field as unknown. A missing, empty, or malformed record is unknown."""
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return _UNKNOWN
    if not isinstance(record, dict):
        return _UNKNOWN
    kind, pid, repo, started, command, cap, reason = (record.get(field) for field in _Holder._fields)
    if not (isinstance(command, str) and command and len(command) <= 80
            and "\n" not in command and "\r" not in command):
        command = None
    return _Holder(
        kind if kind in ("worker", "landing", "exclusive") else None,
        pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        repo if isinstance(repo, str) and repo else None,
        started if _finite(started) else None,
        command,
        _cap_seconds(cap),
        _short_reason(reason),
    )


def _record_holder(path):
    """The holder a record names when a conflicting lock proves a writer, or unknown.

    The record is read only when the nonblocking shared lock fails with errno EAGAIN or
    EWOULDBLOCK. A successful lock, a failed open, or any other lock error returns unknown.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return _UNKNOWN
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            return _read_holder(path)
        return _UNKNOWN
    finally:
        os.close(descriptor)
    return _UNKNOWN


def _slot_busy(handles, name):
    """True when the slot file is held by someone else. Probes the waiter's own unlocked handle and releases it at once."""
    try:
        fcntl.flock(handles[name], fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    fcntl.flock(handles[name], fcntl.LOCK_UN)
    return False


def _busy_names(handles, names):
    """The slot names held by someone else. Runs under the turn mutex."""
    return [name for name in names if _slot_busy(handles, name)]


def _publish(kind, handles, taken, repo, command, cap=None, reason=None):
    """Write one holder record for each slot just taken, before the turn mutex unlocks.

    Returns the locked record descriptors, one per slot whose write succeeded.
    Stores cap and reason only when kind is exclusive.
    """
    if kind != "exclusive":
        cap = None
        reason = None
    holder = _Holder(kind, os.getpid(), _repo_text(repo), time.time(), _short_command(command), cap, reason)
    records = []
    for name, handle in handles.items():
        if handle not in taken:
            continue
        try:
            records.append(_dump_holder(_hold_path(name), holder))
        except OSError:
            pass
    return records


def _clear():
    """Remove this process's records while its slot flocks are still held."""
    for path in (governor_dir() / "holds").glob("*"):
        if _read_holder(path).pid == os.getpid():
            try:
                path.unlink()
            except OSError:
                pass


def _live_holders(names):
    """The holders of the named busy slots, distinct by pid when pid is set, otherwise by the whole holder.

    Each named slot checks its own record. The record is read only when the nonblocking
    shared lock fails with errno EAGAIN or EWOULDBLOCK. A successful lock, a failed open,
    or any other lock error returns unknown for that slot.
    """
    holders, seen = [], set()
    for name in names:
        holder = _record_holder(_hold_path(name))
        key = holder.pid if holder.pid is not None else holder
        if key in seen:
            continue
        seen.add(key)
        holders.append(holder)
    return holders


def _held_for(holder):
    if holder.started is None:
        return "unknown"
    return f"{int(max(0, time.time() - holder.started))}s"


def _span(seconds):
    """A duration for a wait line. Whole minutes when seconds is a positive multiple of 60."""
    if seconds > 0 and seconds % 60 == 0:
        return f"{seconds / 60:g}m"
    if float(seconds).is_integer():
        return f"{int(seconds)}s"
    return f"{seconds:g}s"


def _cap_clause(holder):
    """The wait-line cap phrase for an exclusive holder, or None."""
    if holder.kind != "exclusive":
        return None
    cap = _cap_seconds(holder.cap)
    if cap is None:
        return None
    cap_text = _span(cap)
    if not _finite(holder.started):
        return f"cap {cap_text}"
    left = max(0.0, cap - (time.time() - holder.started))
    return f"{_span(left)} left of {cap_text}"


def _format_wait(elapsed, holders):
    """One line naming each proven holder by kind, repository, hold duration, and short command.

    A holder with no proven record reads as unknown, and so does each field its record
    does not carry. Zero holders produce no holder text. An exclusive holder with a
    usable cap names the time left when its start time is usable. It names the cap
    alone when the start time is not. The line includes the reason only when the holder has a cap clause.
    """
    named = []
    for holder in holders:
        if holder == _UNKNOWN:
            named.append("unknown")
            continue
        clause = _cap_clause(holder)
        extra = ""
        if clause:
            extra = f", {clause}"
            if holder.reason:
                extra += f", {holder.reason}"
        named.append(f"{holder.kind or 'unknown'} {holder.repo or 'unknown'} for {_held_for(holder)}{extra}: "
                     f"{holder.command or 'unknown'}")
    if not named:
        return f"land: waited {int(elapsed)}s for a slot"
    return f"land: waited {int(elapsed)}s for a slot; " + "; ".join(named)


def _report_due(elapsed, last):
    if elapsed < REPORT_AFTER:
        return False
    return last is None or elapsed - last >= REPORT_EVERY


def _raise_exit(signum, frame):
    raise SystemExit(128 + signum)


@contextlib.contextmanager
def _held(kind, repo, command, cap=None, reason=None):
    """Hold the slots kind needs, publish this holder, and clear the records while the flocks are still held."""
    held, records, acquired = _admit(kind, repo, command, cap, reason)
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGHUP)}
    for signum in previous:
        signal.signal(signum, _raise_exit)
    try:
        yield acquired
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        _clear()
        for handle in held:
            handle.close()
        _release_records(records)


def _admit(kind, repo=None, command=None, cap=None, reason=None):
    directory = governor_dir()
    directory.mkdir(parents=True, exist_ok=True)
    slots = governor_slots()
    handles = {name: open(directory / name, "a") for name in _slot_names("exclusive", slots)}
    turns = []
    records = []
    try:
        # Two opens, so that unlocking the mutex byte on gate keeps the ticket byte locked on place.
        for _ in range(2):
            turns.append(os.open(directory / "turn", os.O_RDWR | os.O_CREAT, 0o666))
        gate, place = turns
        ticket = None
        started, reported = time.time(), None
        while True:
            _ofd_excl(gate, TURN_MUTEX, True)
            earlier = _earlier_waiters(gate, ticket, kind)
            taken = None
            if not any(_can_satisfy(waiter, handles, slots) for waiter in earlier):
                taken = _try_take(kind, handles, slots)
            if taken is not None:
                acquired = time.monotonic()
            if taken is None and ticket is None:
                ticket = _take_ticket(gate, place, kind)
            if taken is None:
                busy = _busy_names(handles, _slot_names(kind, slots))
            else:
                records = _publish(kind, handles, taken, repo, command, cap, reason)
            if taken is not None and ticket is not None:
                _ofd_unlock(place, TURN_PLACE + ticket)
            _ofd_unlock(gate, TURN_MUTEX)
            if taken is not None:
                return list(handles.values()), records, acquired
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
        for descriptor in turns:
            os.close(descriptor)


@contextlib.contextmanager
def exclusive_slot(repo=None, command=None, cap=None, reason=None):
    """Hold every slot, landing included, so a benchmark runs on a quiet machine.
    It waits without holding any slot, so other callers keep taking free slots until every slot is free at once.
    A later conflicting caller that waits on this turn file leaves the slots when this caller could take every one of them."""
    if os.environ.get("LAND_SLOT"):
        raise LandError("an exclusive slot must be the outermost slot; this command already runs inside one")
    with _held("exclusive", repo, command, cap, reason) as acquired:
        os.environ["LAND_SLOT"] = "exclusive"
        try:
            yield acquired
        finally:
            os.environ.pop("LAND_SLOT", None)


@contextlib.contextmanager
def slot(pool="worker", repo=None, command=None):
    """Hold one slot of a pool. Landing checks use their own one-slot pool so workers never starve the queue.
    A command already inside a slot (LAND_SLOT set) runs without taking another, so nesting cannot deadlock.
    A caller that shares this turn file leaves a free slot for an earlier conflicting ticket that can take it."""
    if os.environ.get("LAND_SLOT"):
        yield
        return
    with _held(pool, repo, command):
        os.environ["LAND_SLOT"] = pool
        try:
            yield
        finally:
            os.environ.pop("LAND_SLOT", None)


def main(argv=None):
    try:
        argv = sys.argv[1:] if argv is None else argv
        parser = argparse.ArgumentParser(prog="land.py")
        sub = parser.add_subparsers(dest="command", required=True)
        slot_parser = sub.add_parser("slot")
        slot_parser.add_argument("--exclusive", action="store_true")
        slot_parser.add_argument("cmd", nargs=argparse.REMAINDER)
        args = parser.parse_args(argv)
        command = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
        if not command:
            raise LandError("slot needs a command after --")
        with (exclusive_slot() if args.exclusive else slot()):
            return subprocess.run(command).returncode
    except LandError as error:
        print(f"land: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
'''
