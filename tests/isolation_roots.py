"""Carry a guarded process's trusted roots to a child that inherits no environment.

The guard keeps its protected roots in memory and in the environment. A child started with
`env={"PATH": ...}` inherits neither, so it used to protect only the real home from pwd and wrote
into the roots its parent protected. One record per guarded process, keyed by pid, closes that gap.
It lives in a fixed per-user directory under the first temporary directory that is a directory and
is not inside a protected root, because a child with no environment cannot be told where to look.
Roots written to and returned from records are real absolute path strings.

A child reads every candidate record rather than the first one it finds, so a stale or forged record
cannot hide the genuine one. A child stops when a record it finds cannot be read or parsed. It also
stops when a candidate directory is not a directory this user owns with no group or other write bit. A
record carries the process start time when /proc gives one. Where /proc gives the live start, a record
a dead process left under a reused pid is ignored instead of trusted. A child reads its own pid's
record, which covers a process that replaced itself with a stripped environment, and its parent's,
which covers a fresh child of a guarded process. Every write and delete acts through a descriptor of a
verified directory with a single-component name, and the guard lets exactly those calls through, so
the record machinery cannot damage a protected file.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import sys

_TEMPORARIES = ("/tmp", "/var/tmp", "/usr/tmp")
_PREFIX = "pstack-t3-isolation-"
_RECORD = re.compile(r"\A(\d+)\.json\Z")
_TEMPORARY = re.compile(r"\A\.(\d+)\.json\.[0-9a-f]+\Z")
_ANCHORS: set[int] = set()
_PROTECTION: tuple[str, ...] = ()


def _fail(problem: str) -> SystemExit:
    return SystemExit(f"pstack-t3 test isolation: {problem}")


def _real(path) -> str:
    return os.path.realpath(os.fsdecode(os.fspath(path)))


def _under(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def _arg(args: tuple, index: int):
    return args[index] if index < len(args) else None


def _start(pid: int) -> int | None:
    """Return field 22 of /proc/<pid>/stat, the process start time, or None when /proc cannot answer."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            fields = handle.read().rsplit(b")", 1)[1].split()
    except (OSError, IndexError):
        return None
    if len(fields) < 20 or not fields[19].isdigit():
        return None
    return int(fields[19])


def _plain(name) -> bool:
    """Return whether name is one relative component, so a descriptor names an entry of the directory it holds."""
    if isinstance(name, int) or not isinstance(name, (str, bytes)):
        return False
    text = os.fsdecode(name)
    separators = (os.sep,) if os.altsep is None else (os.sep, os.altsep)
    return bool(text) and text not in (os.curdir, os.pardir) and not os.path.isabs(text) and not any(
        separator in text for separator in separators)


@contextlib.contextmanager
def anchored(number: int):
    """Act through a verified record descriptor, so the guard lets this module's own calls through."""
    _ANCHORS.add(number)
    try:
        yield
    finally:
        _ANCHORS.discard(number)


def anchored_fd(number, name=None) -> bool:
    """Return whether number is a descriptor this module is acting through right now, for name when one is given."""
    if not isinstance(number, int) or number not in _ANCHORS:
        return False
    return name is None or _plain(name)


def anchored_call(event: str, args: tuple) -> bool:
    """Return whether an audit event is one of this module's own calls through an anchored descriptor and a plain name."""
    if event == "os.remove":
        return anchored_fd(_arg(args, 1), _arg(args, 0))
    if event == "os.rename":
        return (anchored_fd(_arg(args, 2), _arg(args, 0))
                and anchored_fd(_arg(args, 3), _arg(args, 1)))
    return False


def bases() -> tuple[str, ...]:
    """Return the real paths of the candidate temporary directories that exist."""
    return tuple(_real(path) for path in _TEMPORARIES if os.path.isdir(path))


def records(pid: int) -> tuple[str, ...]:
    """Return the candidate record path for pid in every base directory, in base order."""
    return tuple(os.path.join(base, _PREFIX + str(os.getuid()), f"{pid}.json") for base in bases())


def record(pid: int) -> str | None:
    """Return the first candidate record for pid that exists, else None."""
    for path in records(pid):
        if os.path.lexists(path):
            return path
    return None


def _reaches(path: str, protected) -> bool:
    """Return whether the path reaches protected storage by mount, through the guard's mount module."""
    module = sys.modules.get("pstack_test_isolation_mounts")
    if module is None:
        raise _fail(f"the mount table is not loaded, so the record directory {path!r} cannot be judged")
    return module._reaches(path, protected)


def _trusted_dir(path: str):
    """Return (descriptor, stat) for the trusted directory at path, None when it is absent, or stop the process."""
    try:
        number = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as problem:
        raise _fail(f"the record directory {path!r} cannot be opened: {problem}") from None
    try:
        info = os.fstat(number)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise _fail(f"the record directory {path!r} is not a directory this user owns with no "
                        "group or other write bit")
    except BaseException:
        os.close(number)
        raise
    return number, info


def directory(protected) -> str:
    """Return this user's record directory under the first candidate outside every protected root.

    Create it with mode 0700 when it is absent. Skip a candidate whose record directory is a protected
    root or inside one, by real path and by the storage it reaches. Stop the process when an existing
    entry is not a directory this user owns with no group or other write bit, or when no candidate
    qualifies.
    """
    for base in bases():
        if any(_under(base, root) for root in protected):
            continue
        path = os.path.join(base, _PREFIX + str(os.getuid()))
        real = _real(path)
        if any(_under(real, root) for root in protected) or _reaches(real, protected):
            continue
        if not os.path.lexists(path):
            try:
                os.mkdir(path, 0o700)
            except FileExistsError:
                pass
            except OSError as problem:
                raise _fail(f"cannot make the record directory {path!r}: {problem}") from None
        info = os.lstat(path)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise _fail(f"the record directory {path!r} is not a directory this user owns with no "
                        "group or other write bit")
        return path
    raise _fail(f"no temporary directory outside {tuple(protected)!r} holds the record directory")


def _record_body(number: int, path: str) -> dict:
    """Return the JSON object the verified record descriptor holds, or stop the process."""
    info = os.fstat(number)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        raise _fail(f"the record {path!r} is not a private single-link file this user owns")
    chunks = []
    while True:
        chunk = os.read(number, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks).decode("utf-8"))
    except ValueError as problem:
        raise _fail(f"the record {path!r} is not JSON: {problem}") from None
    if not isinstance(body, dict):
        raise _fail(f"the record {path!r} is not a JSON object")
    return body


def _body_roots(body: dict, path: str, pid: int) -> tuple[str, ...]:
    """Return the real roots the record body holds for pid, or stop the process."""
    if body.get("uid") != os.getuid():
        raise _fail(f"the record {path!r} names uid {body.get('uid')!r}, not this user")
    if body.get("pid") != pid:
        raise _fail(f"the record {path!r} names pid {body.get('pid')!r}, not {pid}")
    roots = body.get("roots")
    if not isinstance(roots, list):
        raise _fail(f"the record {path!r} holds no root array")
    found: list[str] = []
    for root in roots:
        if not isinstance(root, str) or not os.path.isabs(root):
            raise _fail(f"the record {path!r} holds {root!r}, which is not an absolute path")
        real = _real(root)
        if real not in found:
            found.append(real)
    return tuple(found)


def _live(body: dict, start: int | None) -> bool:
    """Return whether the record's start is the pid's live start, or True when /proc gives no live start."""
    if start is None:
        return True
    recorded = body.get("start")
    return isinstance(recorded, int) and recorded == start


def inherited() -> tuple[str, ...]:
    """Return the real trusted roots every valid record names for this process or its parent.

    Every candidate directory is read, so a stale or forged first record cannot hide a later one. A
    record whose start differs from its pid's live start, when /proc gives one, belongs to a dead
    process and is ignored. A directory that is not one this user owns with no group or other write
    bit, and a record that
    cannot be opened, parsed, or validated, stop the process.
    """
    found: list[str] = []
    for pid in (os.getpid(), os.getppid()):
        start = _start(pid)
        for path in records(pid):
            opened = _trusted_dir(os.path.dirname(path))
            if opened is None:
                continue
            number, _info = opened
            try:
                try:
                    handle = os.open(os.path.basename(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                     dir_fd=number)
                except FileNotFoundError:
                    continue
                except OSError as problem:
                    raise _fail(f"the record {path!r} cannot be opened: {problem}") from None
                try:
                    body = _record_body(handle, path)
                finally:
                    os.close(handle)
            finally:
                os.close(number)
            if not _live(body, start):
                continue
            for root in _body_roots(body, path, pid):
                if root not in found:
                    found.append(root)
    return tuple(found)


def publish(pid: int, roots, protected) -> str:
    """Write pid's trusted roots and return the record path. New files request mode 0600.

    The record directory is opened with O_NOFOLLOW and must sit outside every protected root. A fresh
    file under a temporary name is renamed over the record name through the directory descriptor, so
    a symlink or a shared file at that name is replaced and never written through.
    """
    global _PROTECTION
    folder = directory(protected)
    opened = _trusted_dir(folder)
    if opened is None:
        raise _fail(f"the record directory {folder!r} does not exist")
    number, _info = opened
    name = f"{pid}.json"
    path = os.path.join(folder, name)
    temporary = f".{pid}.json.{os.urandom(8).hex()}"
    body = json.dumps({"pid": pid, "uid": os.getuid(), "start": _start(pid),
                       "roots": [_real(root) for root in roots]}).encode("utf-8")
    try:
        with anchored(number):
            handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                             dir_fd=number)
            try:
                os.write(handle, body)
            finally:
                os.close(handle)
            os.rename(temporary, name, src_dir_fd=number, dst_dir_fd=number)
    except OSError as problem:
        raise _fail(f"the record {path!r} cannot be written: {problem}") from None
    finally:
        os.close(number)
    _PROTECTION = tuple(protected)
    return path


def after_fork() -> None:
    """Publish the inherited protection under the child's own pid, or stop the child rather than run with fewer roots.

    The parent's anchored numbers mean nothing in this process, so the child starts with none.
    """
    _ANCHORS.clear()
    try:
        publish(os.getpid(), _PROTECTION, _PROTECTION)
    except BaseException as problem:
        os.write(2, f"pstack-t3 test isolation: the forked child cannot publish its record: {problem}\n".encode())
        os._exit(70)


def prune(protected) -> None:
    """Remove the records in this user's directory whose pid names no live process.

    The verified directory descriptor is the only anchor, so a directory swapped for a symlink after
    it was checked is not followed. An entry that cannot be verified is left in place.
    """
    folder = directory(protected)
    opened = _trusted_dir(folder)
    if opened is None:
        return
    number, _info = opened
    try:
        with anchored(number):
            for name in os.listdir(number):
                match = _RECORD.match(name) or _TEMPORARY.match(name)
                if match is None or int(match.group(1)) <= 0:
                    continue
                try:
                    info = os.stat(name, dir_fd=number, follow_symlinks=False)
                except OSError:
                    continue
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_mode & 0o077 or info.st_nlink != 1):
                    continue
                try:
                    os.kill(int(match.group(1)), 0)
                    continue
                except ProcessLookupError:
                    pass
                except OSError:
                    continue
                try:
                    os.unlink(name, dir_fd=number)
                except FileNotFoundError:
                    pass
    finally:
        os.close(number)
