import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import math
import os
import posixpath
import re
import select
import shlex
import signal
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
from landing_lib import seam

def governor_dir():
    return seam.state_home() / "governor"


def governor_slots():
    config = governor_dir() / "governor.json"
    if config.exists():
        return max(1, int(json.loads(config.read_text()).get("slots", 1)))
    return max(1, min(4, (os.cpu_count() or 4) // 4))


REPORT_AFTER = 15


REPORT_EVERY = 60


EXCLUSIVE_CAP = 600


EXCLUSIVE_CAP_GRACE = 5


EXCLUSIVE_CAP_MAX = 7200


EXCLUSIVE_CAP_STATUS = 124


EXCLUSIVE_REASON_MAX = 80


_Holder = namedtuple("_Holder", "kind pid repo started command cap reason")


_UNKNOWN = _Holder(None, None, None, None, None, None, None)


_Admission = namedtuple("_Admission", "handles records acquired names")


_Demand = namedtuple("_Demand", "kind count")


_Hold = namedtuple("_Hold", "acquired names")


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


def _parse_exclusive_cap(text, reason):
    """Return the exclusive cap in seconds and its short reason, or raise LandError.

    Reads EXCLUSIVE_CAP, EXCLUSIVE_CAP_MAX, and EXCLUSIVE_REASON_MAX at call time.
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
    if seconds > seam.EXCLUSIVE_CAP_MAX:
        raise seam.LandError(f"--for cannot pass {seam.EXCLUSIVE_CAP_MAX:g} seconds")
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
    """Create one exclusively locked ticket and return its number and file."""
    number = max((live for live, _ in waiting), default=0) + 1
    while os.path.lexists(tickets / f"{number}-{kind}"):
        number += 1
    ticket = open(tickets / f"{number}-{kind}", "x")
    try:
        # open creates the file as 0o666 masked by the umask. fchmod sets 0o600 anyway.
        os.fchmod(ticket.fileno(), 0o600)
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


_cap_monotonic = time.monotonic


_real_sleep = time.sleep


def _exclusive_ready_ack():
    """Run once the command starts, after the slots are locked and the holder records are written.
    Production does nothing. A test swaps in a function that marks a ready file, so the signal test can prove the supervisor reached the hook just before its protected wait before it signals."""
    return None


def _pid_reserved(pid):
    """True when pid is still in the process table, including a zombie. Does not reap."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _finish_reaped(process):
    """The leader is already reaped, so its pid no longer proves the command's group. Do not signal."""
    if process.returncode is None:
        try:
            process.wait()
        except ChildProcessError:
            process.returncode = -1


def _ps_line(pid, columns):
    """Stdout from ps for these columns, or None when ps cannot answer. Does not reap."""
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            ["ps", "-o", columns, "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout or ""


def _leader_is_permitted_zombie(pid):
    """True when this exited unreaped leader is a signal target this process may signal.

    The caller has not reaped pid, so the kernel cannot reuse that pid and the credentials stay frozen.
    os.waitid with WNOHANG and WNOWAIT returns a result once the child has exited and None while it lives.
    An alive result stops here and does not run ps.
    Without os.waitid, ps must print a state that starts with Z.
    os.kill returns success for every zombie on Darwin and does not check the signal permission,
    so os.kill(pid, 0) cannot show that this process may signal the leader.
    Root may signal any leader. Otherwise the real or the effective uid of this process must match the
    leader's real or saved uid.
    A failed or unreadable probe is not a permitted target.
    """
    if hasattr(os, "waitid"):
        try:
            exited = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except OSError:
            return False
        if exited is None:
            return False
    else:
        state = _ps_line(pid, "stat=")
        if state is None or not state.strip().startswith("Z"):
            return False
    if os.geteuid() == 0:
        return True
    text = _ps_line(pid, "uid=,ruid=,svuid=")
    if text is None:
        return False
    fields = text.split()
    if len(fields) != 3:
        return False
    try:
        _effective, real, saved = (int(field) for field in fields)
    except ValueError:
        return False
    callers = (os.getuid(), os.geteuid())
    return real in callers or saved in callers


def _killpg_like_linux(process, sig):
    """Signal the command's group the way Linux killpg does.

    Linux killpg succeeds when some member of the group is a permitted signal target and raises
    EPERM when no member is. macOS raises EPERM when a zombie is the only permitted member.
    Treat that EPERM as a successful killpg only when this process observed its own exited unreaped
    leader as a permitted signal target before the attempt.
    Where os.waitid exists, an alive leader on Darwin costs one waitid and does not run ps.
    The ps query runs only after that leader has exited, or when os.waitid is absent.
    A live leader, an unknown leader state, a leader this process may not signal, and any
    PermissionError on another platform raise the original EPERM again.
    A leader that exits after a negative observation and before the attempt raises the original
    EPERM again.
    ProcessLookupError propagates.
    The EPERM is not proof that the group is empty.
    """
    if sys.platform != "darwin":
        os.killpg(process.pid, sig)
        return
    # Witness first. A permitted zombie found after the attempt cannot establish permission for it.
    # A leader that exits in the gap before killpg is a conservative miss, and the original EPERM is raised again.
    witness = _leader_is_permitted_zombie(process.pid)
    try:
        os.killpg(process.pid, sig)
    except PermissionError:
        if not witness:
            raise


def exclusive_command(command, repo, for_text, reason_text):
    """Hold every slot, run command, and stop its process group at the cap.

    command is an argv list. for_text and reason_text are the raw flag strings, or None.
    The deadline starts when every slot is locked, before the holder records are written.
    A command whose cap has already passed does not start.
    Returns the command's status, or EXCLUSIVE_CAP_STATUS.
    """
    seconds, reason = _parse_exclusive_cap(for_text, reason_text)

    def wait_with_real_sleep(process, timeout):
        current = time.sleep
        time.sleep = seam._real_sleep
        try:
            return process.wait(timeout=timeout)
        finally:
            time.sleep = current

    def stop_group(process):
        if process.returncode is not None:
            return
        try:
            _killpg_like_linux(process, signal.SIGTERM)
        except ProcessLookupError:
            if process.returncode is None:
                process.wait()
            return
        # The unreaped leader is how SIGKILL stays on this command's group. Its pid is the process group id, and a zombie holds that pid until it is reaped, so the kernel cannot reuse the id for another process group.
        try:
            grace_ends = time.monotonic() + seam.EXCLUSIVE_CAP_GRACE
            if hasattr(os, "waitid"):
                while True:
                    try:
                        exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    except ChildProcessError:
                        _finish_reaped(process)
                        return
                    remaining = grace_ends - time.monotonic()
                    if exited is not None:
                        if remaining > 0:
                            seam._real_sleep(remaining)
                        break
                    if remaining <= 0:
                        break
                    seam._real_sleep(min(0.05, remaining))
            else:
                # Nothing here reaps, so a leader that exits stays a zombie through the grace.
                while True:
                    if not _pid_reserved(process.pid):
                        _finish_reaped(process)
                        return
                    remaining = grace_ends - time.monotonic()
                    if remaining <= 0:
                        break
                    seam._real_sleep(min(0.05, remaining))
        except BaseException:
            try:
                _killpg_like_linux(process, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if process.returncode is None:
                process.wait()
            raise
        try:
            _killpg_like_linux(process, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.returncode is None:
            process.wait()

    with exclusive_slot(repo=repo, command=" ".join(command), cap=seconds, reason=reason) as acquired:
        if seconds - (seam._cap_monotonic() - acquired) <= 0:
            _report_cap(seconds)
            return EXCLUSIVE_CAP_STATUS
        process = subprocess.Popen(command, start_new_session=True)
        try:
            seam._exclusive_ready_ack()
            if hasattr(process, "_sigint_wait_secs"):
                process._sigint_wait_secs = 0  # CPython waits 0.25s on SIGINT after setsid, and the child did not see that signal.
            remaining = seconds - (seam._cap_monotonic() - acquired)
            try:
                wait_with_real_sleep(process, max(0.0, remaining))
            except subprocess.TimeoutExpired:
                stop_group(process)
                _report_cap(seconds)
                return EXCLUSIVE_CAP_STATUS
            return process.returncode
        finally:
            if process.returncode is None:
                stop_group(process)


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


_BUILD_DRAIN_GRACE = 5.0


_ARMED = "armed"


_STARTED = "started"


_GROUP_KILLED = "group-killed"


_REAPED = "reaped"


_RESTORED = "restored"


class _Build:
    """One shell command in its own process group, owned by exactly one runner.

    Only the runner sends SIGKILL to the group and only the runner reaps the leader. It sends SIGKILL only
    before it reaps. The SIGINT handler records the interrupt and wakes the runner. It never
    signals, reaps, raises, or calls the entry disposition. The entry disposition receives the
    postponed SIGINT from deliver, after close restores the disposition.
    No code outside this class waits on the Popen. Nothing calls its wait, poll, or communicate.
    """

    def __init__(self, previous):
        """Previous is what signal.getsignal(SIGINT) returned on entry. Allocates nothing."""
        self._previous = previous
        self._intercept = previous is signal.SIG_DFL or callable(previous)
        self.phase = None
        self._pending = None
        self._popen = None
        self._wake_r = None
        self._wake_w = None
        self._stdout = None
        self._chunks = []
        self.returncode = None

    def start(self, command, cwd):
        """Open the wake pipe, install the interceptable handler, then spawn. An installed handler is in place before the fork."""
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        os.set_blocking(self._wake_w, False)
        self.phase = _ARMED
        if self._intercept:
            signal.signal(signal.SIGINT, self._on_sigint)
        self._popen = subprocess.Popen(command, cwd=cwd, shell=True, start_new_session=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.phase = _STARTED
        self._stdout = self._popen.stdout

    def run(self, timeout):
        """Drive STARTED to REAPED. Returns a pair. Timeout returns None as the status."""
        deadline = time.monotonic() + timeout
        ended = self._drain(deadline)
        if ended == "eof":
            ended = self._await_exit(deadline)
            if ended == "reaped":
                return self.returncode, self._text()
        self._kill_group()
        if self._drain(time.monotonic() + _BUILD_DRAIN_GRACE) != "eof":
            self._close_stdout()
            self._chunks = []
        self._reap_killed()
        text = self._text()
        return (None, text) if ended == "deadline" else (self.returncode, text)

    def close(self):
        """Converge to RESTORED from any phase. Idempotent. Runs in the finally."""
        try:
            if self.phase == _STARTED:
                self._kill_group()
            if self.phase == _GROUP_KILLED:
                self._close_stdout()
                self._reap_killed()
        finally:
            self._restore()
            self._close_stdout()
            if self._wake_r is not None:
                try:
                    os.close(self._wake_r)
                except OSError:
                    pass
                self._wake_r = None
            if self._wake_w is not None:
                try:
                    os.close(self._wake_w)
                except OSError:
                    pass
                self._wake_w = None

    def deliver(self):
        """Hand a postponed SIGINT to the entry disposition. Runs after close."""
        if self._pending is None:
            return
        signum, frame = self._pending
        self._pending = None
        if self._previous is signal.SIG_IGN:
            return
        if self._previous is signal.SIG_DFL:
            os.kill(os.getpid(), signal.SIGINT)
            raise KeyboardInterrupt
        if callable(self._previous):
            self._previous(signum, frame)
            return
        raise KeyboardInterrupt

    def _on_sigint(self, signum, frame):
        """Record the first SIGINT and wake the runner. Never signals, reaps, or raises."""
        if self._pending is None:
            self._pending = (signum, frame)
        try:
            os.write(self._wake_w, b"\0")
        except OSError:
            pass

    def _drain(self, until):
        """Read stdout until EOF or the time limit. Return interrupt only in STARTED. Never reaps."""
        while self._stdout is not None:
            if self._pending is not None and self.phase == _STARTED:
                return "interrupt"
            remaining = until - time.monotonic()
            if remaining <= 0:
                return "deadline"
            readers = [self._wake_r] if self._wake_r is not None else []
            if self._stdout is not None:
                try:
                    readers.append(self._stdout.fileno())
                except (OSError, ValueError):
                    self._close_stdout()
                    return "eof"
            try:
                ready, _, _ = select.select(readers, [], [], remaining)
            except (OSError, ValueError):
                continue
            for descriptor in ready:
                if descriptor == self._wake_r:
                    try:
                        os.read(self._wake_r, 512)
                    except OSError:
                        pass
                else:
                    try:
                        chunk = os.read(descriptor, 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        self._close_stdout()
                        return "eof"
                    self._chunks.append(chunk)
        return "eof"

    def _await_exit(self, deadline):
        """Poll the leader with WNOHANG reaps after EOF. This runner is the only signaller."""
        delay = 0.0
        while True:
            if self._try_reap():
                return "reaped"
            if self._pending is not None:
                return "interrupt"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "deadline"
            try:
                select.select([self._wake_r] if self._wake_r is not None else [], [], [], min(delay, remaining))
            except (OSError, ValueError):
                pass
            if self._wake_r is not None:
                try:
                    os.read(self._wake_r, 512)
                except OSError:
                    pass
            delay = min(max(delay * 2, 0.001), 0.05)

    def _kill_group(self):
        """Move STARTED to GROUP_KILLED with one SIGKILL. A call after reap is a bug."""
        if self.phase not in (_STARTED, _GROUP_KILLED):
            raise RuntimeError("signal after reap")
        if self.phase == _GROUP_KILLED:
            return
        self.phase = _GROUP_KILLED
        try:
            _killpg_like_linux(self._popen, signal.SIGKILL)
        except OSError:
            pass

    def _try_reap(self):
        """Run one waitpid with WNOHANG. True when the leader is gone. Records our reap or a foreign reap."""
        try:
            pid, status = os.waitpid(self._popen.pid, os.WNOHANG)
        except ChildProcessError:
            self._reaped(None)
            return True
        if pid == 0:
            return False
        self._reaped(status)
        return True

    def _reap_killed(self):
        """Run one blocking waitpid. Legal only in GROUP_KILLED after SIGKILL."""
        try:
            _, status = os.waitpid(self._popen.pid, 0)
        except ChildProcessError:
            status = None
        self._reaped(status)

    def _reaped(self, status):
        """Record the single reap. Writes returncode so Popen never queues the pid."""
        self.returncode = -1 if status is None else os.waitstatus_to_exitcode(status)
        self._popen.returncode = self.returncode
        self.phase = _REAPED

    def _restore(self):
        """Restore the interceptable entry disposition. Blocks SIGINT for the swap where pthread_sigmask exists."""
        blocked = hasattr(signal, "pthread_sigmask")
        old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT}) if blocked else None
        try:
            if self._intercept and self.phase is not None:
                signal.signal(signal.SIGINT, self._previous)
            self.phase = _RESTORED
        finally:
            if blocked:
                signal.pthread_sigmask(signal.SIG_SETMASK, old)

    def _close_stdout(self):
        """Close the build pipe and forget it. Ignores close errors."""
        if self._stdout is not None:
            try:
                self._stdout.close()
            except (OSError, ValueError):
                pass
            self._stdout = None

    def _text(self):
        """Decode the bytes read so far with replacement. Keeps CRLF."""
        return b"".join(self._chunks).decode("utf-8", "replace")


def run_build(command, cwd, timeout):
    """Run a shell command in its own process group. Returns a pair. Timeout returns None as the status.

    One runner sends SIGKILL to the group and reaps the leader, and it sends SIGKILL only before it reaps.
    An interceptable SIGINT before the reap is postponed. The runner never sends SIGKILL after it reaps.
    It restores the entry disposition, and then delivers the SIGINT to it. SIG_IGN leaves the
    build running.
    """
    if threading.current_thread() is not threading.main_thread():
        raise ValueError("signal only works in main thread of the main interpreter")
    build = _Build(signal.getsignal(signal.SIGINT))
    try:
        build.start(command, cwd)
        result = build.run(timeout)
    finally:
        try:
            build.close()
        finally:
            build.deliver()
    return result


def supervised(command, cwd, timeout):
    """Run a shell command in its own process group and stop the group at the timeout."""
    return run_build(command, cwd, timeout)
