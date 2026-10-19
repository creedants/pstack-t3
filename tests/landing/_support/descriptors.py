import fcntl
import gc
import io
import os
import signal
import socket
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class _Saved:
    cwd: str
    environ: dict
    argv: list
    argv_contents: list
    stdin: object
    stdout: object
    stderr: object
    umask: int
    signals: tuple
    dont_write_bytecode: bool
    fds: dict


def _open_fd_directory():
    """Open the descriptor directory and return it with the descriptor that reads it.

    The directory is /proc/self/fd when this host provides it, and /dev/fd otherwise.
    """
    try:
        return "/proc/self/fd", os.open("/proc/self/fd", os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        return "/dev/fd", os.open("/dev/fd", os.O_RDONLY | os.O_DIRECTORY)


def _fd_numbers():
    """Return the descriptor directory and every descriptor number it lists except the probe.

    The probe is the descriptor that reads the directory, and its number is left out.
    """
    directory, probe = _open_fd_directory()
    try:
        names = os.listdir(probe)
    finally:
        os.close(probe)
    numbers = set()
    probe_name = str(probe)
    for name in names:
        if name == probe_name or not name.isdigit():
            continue
        numbers.add(int(name))
    return directory, numbers


def _fd_paths():
    """Return the descriptor directory and the path of every open descriptor it can read except the probe.

    The probe is the descriptor that reads the directory, and its number is left out. An entry under
    /proc/self/fd is a symlink. An entry under /dev/fd is not, and F_GETPATH names it.
    """
    directory, numbers = _fd_numbers()
    found = {}
    for number in numbers:
        try:
            if directory == "/proc/self/fd":
                found[number] = os.readlink(f"/proc/self/fd/{number}")
            else:
                found[number] = fcntl.fcntl(number, fcntl.F_GETPATH, bytes(1024)).rstrip(b"\0").decode()
        except OSError:
            continue
    return directory, found


def _descriptors():
    """Return the device and inode of every listed descriptor at or above 3 that it can fstat.

    No pathname is read, so a descriptor whose pathname query would fail is still listed.
    """
    _, numbers = _fd_numbers()
    found = {}
    for number in numbers:
        if number < 3:
            continue
        try:
            info = os.fstat(number)
        except OSError:
            continue
        found[number] = (info.st_dev, info.st_ino)
    return found


def _save():
    umask = os.umask(0)
    os.umask(umask)
    handlers = []
    for signum in range(1, signal.NSIG):
        try:
            handlers.append((signum, signal.getsignal(signum)))
        except (OSError, ValueError):
            continue
    return _Saved(
        cwd=os.getcwd(),
        environ=dict(os.environ),
        argv=sys.argv,
        argv_contents=list(sys.argv),
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        umask=umask,
        signals=tuple(handlers),
        dont_write_bytecode=sys.dont_write_bytecode,
        fds=_descriptors(),
    )


def _close_live_owners(numbers):
    stale = set(numbers)
    if not stale:
        return
    owners = []
    for obj in gc.get_objects():
        # Call fileno only on known descriptor owners. A ctypes library resolves fileno as a C
        # function when the loaded library exports that name, and calling glibc fileno with no FILE
        # pointer can crash the process with SIGSEGV. A dead weakref proxy raises ReferenceError from the
        # type check. An unrelated object can raise a BaseException such as SystemExit.
        # Both are skipped. KeyboardInterrupt propagates.
        try:
            if not isinstance(obj, (io.IOBase, socket.socket)):
                continue
            fileno = getattr(obj, "fileno", None)
            if not callable(fileno):
                continue
            number = fileno()
        except KeyboardInterrupt:
            raise
        except BaseException:
            continue
        if type(number) is not int or number not in stale:
            continue
        owners.append(obj)

    def depth(obj):
        seen = set()
        current = obj
        steps = 0
        while id(current) not in seen:
            seen.add(id(current))
            try:
                nxt = getattr(current, "buffer", None)
                if nxt is None:
                    nxt = getattr(current, "raw", None)
            except KeyboardInterrupt:
                raise
            except BaseException:
                break
            if nxt is None:
                break
            current = nxt
            steps += 1
        return steps

    # Deepest first, so the wrapper flushes its buffer before the raw file closes.
    owners.sort(key=depth, reverse=True)
    for obj in owners:
        try:
            obj.close()
        except (OSError, ValueError):
            pass


def _close_new_fds(before):
    first = _descriptors()
    second = _descriptors()
    stale = []
    for number in first.keys() & second.keys():
        if first[number] != second[number]:
            continue
        if before.get(number) == first[number]:
            continue
        stale.append(number)
    _close_live_owners(stale)
    for number in stale:
        try:
            os.close(number)
        except OSError:
            continue


def _restore(saved):
    """Each step runs even when an earlier step raises. Then the first error is raised."""
    first = None

    def step(action):
        nonlocal first
        try:
            action()
        except Exception as error:
            if first is None:
                first = error

    step(lambda: os.chdir(saved.cwd))

    def environ():
        os.environ.clear()
        os.environ.update(saved.environ)

    step(environ)

    def argv():
        if sys.argv is not saved.argv:
            sys.argv = saved.argv
        saved.argv[:] = saved.argv_contents

    step(argv)

    def stdio():
        sys.stdin = saved.stdin
        sys.stdout = saved.stdout
        sys.stderr = saved.stderr

    step(stdio)
    step(lambda: os.umask(saved.umask))

    def signals():
        for signum, handler in saved.signals:
            try:
                current = signal.getsignal(signum)
            except (OSError, ValueError):
                continue
            if current == handler:
                continue
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                continue

    step(signals)
    step(lambda: setattr(sys, "dont_write_bytecode", saved.dont_write_bytecode))
    # Collect unreachable connections so their descriptors close before os.close.
    step(gc.collect)
    step(lambda: _close_new_fds(saved.fds))
    if first is not None:
        raise first
