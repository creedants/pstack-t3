import errno
import fcntl
import json
import math
import os
import sys
import time
from landing_lib import seam
from .base import EXCLUSIVE_CAP_MAX, EXCLUSIVE_REASON_MAX, REPORT_AFTER, REPORT_EVERY, _Holder, _UNKNOWN, _cap_seconds, _finite, _repo_text, _short_command, _short_reason, exclusive_max, governor_dir

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
    previous = os.umask(0o077)
    try:
        # os.open asks for 0o666 and the 0o077 umask leaves 0o600, so the name is owner-readable when it appears.
        descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o666)
    finally:
        os.umask(previous)
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

def _parse_exclusive_cap(text, reason):
    """Return the exclusive cap in seconds and its short reason, or raise LandError.

    Reads EXCLUSIVE_CAP and EXCLUSIVE_REASON_MAX at call time, and the maximum from exclusive_max.
    A refusal takes no slot.
    """
    if text is None:
        seconds = float(seam.EXCLUSIVE_CAP)
    else:
        try:
            seconds = float(text)
        except (TypeError, ValueError):
            raise seam.LandError("--for must be a positive number of seconds") from None
        if not math.isfinite(seconds) or seconds <= 0:
            raise seam.LandError("--for must be a positive number of seconds")
    limit = exclusive_max()
    if seconds > limit:
        raise seam.LandError(f"--for cannot pass {limit:g} seconds")
    short = _short_reason(reason)
    if seconds > seam.EXCLUSIVE_CAP and short is None:
        raise seam.LandError(f"--for over {seam.EXCLUSIVE_CAP:g} seconds needs --reason")
    return seconds, short

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
    return [name for name in names if seam._slot_busy(handles, name)]

def _publish(kind, handles, taken, repo, command, cap=None, reason=None):
    """Write one holder record for each slot just taken, before the turn mutex unlocks.

    Returns the locked record descriptors, one per slot whose write succeeded.
    Stores cap and reason only when kind is exclusive. When the rename fails with FileNotFoundError,
    the write retries with a fresh temporary
    record, up to three attempts per slot. The failed descriptor closes before the retry,
    releasing its record lock. Previously published record locks and slot flocks stay held.
    """
    if kind != "exclusive":
        cap = None
        reason = None
    holder = _Holder(kind, os.getpid(), _repo_text(repo), time.time(), _short_command(command), cap, reason)
    records = []
    for name, handle in handles.items():
        if handle not in taken:
            continue
        for _ in range(3):
            try:
                records.append(_dump_holder(_hold_path(name), holder))
                break
            except FileNotFoundError:
                continue
            except OSError:
                break
    return records

def _tmp_kept(path):
    """True when this temporary record file stays in place rather than being removed.

    The name is removed only when two nonblocking exclusive flocks on read-only opens both succeed.
    The second flock is held through the unlink, then unlocked and closed. A writer locks the file
    it opened before it writes the record and holds the lock until it lets the slot go. A failed open
    and a failed exclusive lock leave the name, since neither proves the writer is gone. A writer that
    has not locked by the second flock loses the name.
    """
    try:
        first = os.open(path, os.O_RDONLY)
    except OSError:
        return True
    try:
        try:
            fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        try:
            fcntl.flock(first, fcntl.LOCK_UN)
        except OSError:
            return True
    finally:
        os.close(first)
    try:
        second = os.open(path, os.O_RDONLY)
    except OSError:
        return True
    try:
        try:
            fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        try:
            path.unlink()
        except OSError:
            pass
        return path.exists()
    finally:
        try:
            fcntl.flock(second, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(second)

def _clear():
    """Remove this process's records while its slot flocks are still held.

    A name in governor/holds that ends in .tmp and is not this process's own record is removed too,
    unless _tmp_kept keeps it. A holder that died before it renamed its file into place left that name
    behind holding no lock, and so does a writer that opened its file and has not yet locked it.
    """
    holds = governor_dir() / "holds"
    for path in holds.glob("*"):
        if _read_holder(path).pid == os.getpid():
            try:
                path.unlink()
            except OSError:
                pass
        elif path.name.endswith(".tmp"):
            _tmp_kept(path)

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

def _cap_stopped_line(seconds):
    """The one stderr line for a cap stop. Reads EXCLUSIVE_CAP at call time."""
    return (f"land: stopped the command at the {seconds:g}s exclusive cap. "
            f"--for sets a longer cap and needs --reason past {seam.EXCLUSIVE_CAP:g}s.")

def _report_cap(seconds):
    """Print the cap line. An OSError or ValueError from print is ignored."""
    line = _cap_stopped_line(seconds)
    try:
        print(line, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass

def _format_wait(elapsed, holders):
    """One line naming each proven holder by kind, repository, hold duration, and short command.

    A holder with no proven record reads as unknown, and so does each field its record
    does not carry. Zero holders produce no holder text. An exclusive holder with a
    usable cap names the time left when its start time is usable. It names the cap
    alone when the start time is not. The line includes the reason when the record
    has one.
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
    if elapsed < seam.REPORT_AFTER:
        return False
    return last is None or elapsed - last >= seam.REPORT_EVERY
