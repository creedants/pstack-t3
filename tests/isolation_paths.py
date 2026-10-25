"""Path rules, protected real roots, and run-root admission for the test isolation guard.

`tests/isolation.py` imports from here and holds the guard state, the audit hook, the wrappers, and
`install()`. `_protected_write` and `_owned` also live here, because each needs the mount table.

A descriptor's path comes from `/proc/self/fd` where that exists, and otherwise from the macOS `fcntl`
`F_GETPATH`, accepted only when a stat of the path names the same device and inode as the descriptor. A
descriptor that resolves through neither has no path, and every caller refuses it. `_placed` names the
operations whose target arrives through a descriptor: the descriptor itself, or a relative name and the
directory descriptor it is relative to, and `_owned` keeps such a target inside the run root. Path
checks resolve symlinks in the directory part first, and a checked delete or rename acts on a final
symlink name itself.

`os.path.realpath` follows symlinks and not mounts, so a bind mount inside the run root makes a
protected directory answer to a run-root name. `_mount_table` reads `/proc/self/mountinfo` at check time,
and `_identity` turns a path into the device and on-device path of the longest mount point above it. The
same directory then has one identity under every spelling. A host without that file, which includes
macOS, has no table, and the spelling rules stand alone there.
"""

from __future__ import annotations

import fcntl
import json
import os
import pwd
import secrets
import stat
import tempfile
from pathlib import Path

POINTER = "PSTACK_TEST_HOME_ROOT"
TOKEN = "PSTACK_TEST_HOME_TOKEN"
ORIGINALS = "PSTACK_TEST_PROTECTED_ROOTS"
MARKER = ".pstack-t3-test-home"

_LAYOUT = {
    "HOME": "home",
    "XDG_STATE_HOME": "state",
    "XDG_CONFIG_HOME": "config",
    "XDG_DATA_HOME": "data",
    "XDG_CACHE_HOME": "cache",
    "XDG_RUNTIME_DIR": "runtime",
    "TMPDIR": "tmp",
}


_WRITE_FLAGS = os.O_CREAT | os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_TRUNC


def _real(path) -> str:
    """Return the real path of a str, bytes, or path-like value, resolving every symlink, the last one too."""
    return os.path.realpath(os.fsdecode(os.fspath(path)))


def _entry(path) -> str:
    """Return the real path of the directory holding path, joined to its last name unresolved.

    A delete or rename acts on that name itself, so a symlink there is the link, not its target. A
    last name of `.` or `..` resolves the whole path.
    """
    text = os.fsdecode(os.fspath(path))
    head, tail = os.path.split(text)
    if tail in ("", ".", ".."):
        return _real(text)
    return os.path.join(_real(head or "."), tail)


def _within(path: str, base: str) -> bool:
    """Return whether the real path is the real base or below it, comparing whole components."""
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


_ESCAPES = {b"040": b" ", b"011": b"\t", b"012": b"\n", b"134": b"\\"}
_MOUNTINFO = "/proc/self/mountinfo"


def _unescape(field: bytes) -> str:
    """Return the field with mountinfo's octal escapes for space, tab, newline, and the escape decoded."""
    out = bytearray()
    index = 0
    while index < len(field):
        code = field[index + 1:index + 4]
        if field[index:index + 1] == b"\\" and code in _ESCAPES:
            out += _ESCAPES[code]
            index += 4
        else:
            out.append(field[index])
            index += 1
    return os.fsdecode(bytes(out))


def _mount_table() -> tuple[tuple[str, str, str], ...]:
    """Return (mount point, device, on-device root) for every mount, or () when the host has no table."""
    try:
        with open(_MOUNTINFO, "rb") as table:
            lines = table.read().splitlines()
    except OSError:
        return ()
    found = []
    for line in lines:
        fields = line.split(b" - ", 1)[0].split()
        if len(fields) < 5:
            continue
        found.append((_unescape(fields[4]), os.fsdecode(fields[2]), _unescape(fields[3])))
    return tuple(found)


def _identity(path: str, table) -> tuple[str, str] | None:
    """Return the device and on-device path of the longest mount point that covers the path, or None."""
    best = None
    for entry in table:
        mount = entry[0]
        if path == mount or path.startswith(mount.rstrip(os.sep) + os.sep):
            if best is None or len(mount) > len(best[0]):
                best = entry
    if best is None:
        return None
    mount, device, root = best
    return device, os.path.normpath(os.path.join(root, path[len(mount):].lstrip(os.sep)))


def _identities(path: str, table) -> tuple[tuple[str, str], ...]:
    """Return the device and on-device path of every mount point that covers the path.

    A mount that a later mount hides stays in the table, so a hidden entry must not answer for a path a
    visible entry also covers. One path therefore carries one identity per mount above it, and the
    storage it reaches is protected when any of them is.
    """
    found = []
    for mount, device, root in table:
        if path == mount or path.startswith(mount.rstrip(os.sep) + os.sep):
            found.append((device, os.path.normpath(os.path.join(root, path[len(mount):].lstrip(os.sep)))))
    return tuple(found)


def _covers(identity, base) -> bool:
    """Return whether the identity is the base identity or below it."""
    return base is not None and identity[0] == base[0] and _within(identity[1], base[1])


def _reaches(path: str, bases) -> bool:
    """Return whether the path sits on the storage a base names or below it, whatever its spelling."""
    table = _mount_table()
    here = _identities(path, table)
    if not here:
        return False
    return any(_covers(identity, _identity(base, table)) for identity in here for base in bases)


def _protected_write(real: str, checkout: str, protected) -> bool:
    """Return whether a write at the real path lands in a protected root, by spelling or by the storage it
    reaches, with the checkout allowed."""
    if _within(real, checkout):
        return False
    return any(_within(real, base) for base in protected) or _reaches(real, protected)


def _owned(real: str, root: str | None, protected) -> bool:
    """Return whether the real path stays inside the run root, outside every protected root, and off protected
    storage."""
    return (root is not None and _within(real, root)
            and not any(_within(real, base) for base in protected)
            and not _reaches(real, protected))


def _real_roots(root: str | None, environ) -> tuple[str, ...]:
    """Return the real home and the home variables of environ that sit outside root, as real paths.

    A value that is root or below it belongs to root, so it is not protected. A value that holds root
    is not protected either, which covers an enclosing run root that a nested runner makes. The real
    home from pwd is always protected, even when it holds root, so adopting a root inside it is refused.
    The roots a caller recorded in ORIGINALS are appended whatever else the environment says, and so
    are the roots the marker of the pointer's run root records for environ's token. Nothing here lists
    a directory.
    """
    found: list[str] = []
    home = ""
    try:
        home = _real(pwd.getpwuid(os.getuid()).pw_dir)
    except (KeyError, OSError):
        pass
    candidates = [home, environ.get("HOME", "")]
    candidates.extend(environ.get(name, "") for name in _LAYOUT if name.startswith("XDG_"))
    for value in candidates:
        if not value or not os.path.isabs(value):
            continue
        text = _real(value)
        if root is not None and text != home and (_within(text, root) or _within(root, text)):
            continue
        if text not in found:
            found.append(text)
    for text in _original_roots(environ):
        if text not in found:
            found.append(text)
    pointer = environ.get(POINTER, "")
    if os.path.isabs(pointer):
        for text in marker_roots(_real(pointer), environ.get(TOKEN, "")):
            if text not in found:
                found.append(text)
    return tuple(found)


def encode_roots(roots) -> str:
    """Return the unambiguous record ORIGINALS carries: a JSON array of the roots, in order."""
    return json.dumps([os.fspath(root) for root in roots])


def _path_items(items) -> tuple[str, ...]:
    """Return the real path of each absolute path string in a JSON array, or raise ValueError."""
    if not isinstance(items, list):
        raise ValueError("it is not a JSON array")
    found: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError(f"it holds {item!r}, which is not a path string")
        if not os.path.isabs(item):
            raise ValueError(f"it holds {item!r}, which is not an absolute path")
        text = _real(item)
        if text not in found:
            found.append(text)
    return tuple(found)


def parse_roots(value: str) -> tuple[str, ...]:
    """Return the real paths value records, or raise ValueError when the record is malformed.

    A record is a JSON array of absolute path strings. An empty string, text that is not JSON,
    anything that is not an array, and any item that is not an absolute path are malformed.
    "[]" is a valid empty record.
    """
    try:
        items = json.loads(value)
    except ValueError as problem:
        raise ValueError(f"it is not JSON: {problem}") from None
    return _path_items(items)


def _original_roots(environ) -> tuple[str, ...]:
    """Return the protected real roots a caller recorded in environ. A malformed record stops the process."""
    value = environ.get(ORIGINALS)
    if value is None:
        return ()
    try:
        return parse_roots(value)
    except ValueError as problem:
        raise _fail(f"{ORIGINALS} is not a record of absolute paths: {problem}") from None


def layout(root) -> dict[str, str]:
    """Return the one mapping from each isolated variable to its directory under root."""
    base = os.path.abspath(os.fspath(root))
    return {name: os.path.join(base, part) for name, part in _LAYOUT.items()}


def _fail(problem: str) -> SystemExit:
    return SystemExit(f"pstack-t3 test isolation: {problem}")


def _adopt(environ, protected: tuple[str, ...]) -> str:
    """Return the pointer's real root, or raise SystemExit unless a marker claims this user's live run marked
    it with environ's token, it sits outside every protected real root, and every isolated variable resolves inside it."""
    pointer = environ.get(POINTER, "")
    if not pointer or not os.path.isabs(pointer):
        raise _fail(f"{POINTER} is not an absolute path: {pointer!r}")
    active = _real(pointer)
    for base in protected:
        if _within(active, base):
            raise _fail(f"{POINTER} names {pointer!r}, which resolves to {active!r} inside the real path {base!r}")
    try:
        info = os.lstat(active)
    except OSError:
        raise _fail(f"{POINTER} names {active!r}, which does not exist") from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise _fail(f"{POINTER} names {active!r}, which is not a directory this user owns")
    _verify_marker(active, environ.get(TOKEN, ""))
    for name in _LAYOUT:
        value = environ.get(name, "")
        if not value or not os.path.isabs(value):
            raise _fail(f"{name} is {value!r} and does not name an absolute directory")
        if not _within(_real(value), active):
            raise _fail(f"{name} is {value!r}, which resolves outside the run root {active!r}")
    return active


def write_marker(active: str, roots) -> str:
    """Write the marker of active with its owner, pid, token, device, inode, and trusted roots, and return the token."""
    token = secrets.token_hex(16)
    info = os.lstat(active)
    record = {"uid": os.getuid(), "pid": os.getpid(), "token": token,
              "device": info.st_dev, "inode": info.st_ino, "roots": list(roots)}
    with open(os.path.join(active, MARKER), "w", encoding="utf-8") as marker:
        json.dump(record, marker)
    return token


def marker_roots(active: str, token: str) -> tuple[str, ...]:
    """Return the trusted real roots the marker of active records for token.

    Return () when the marker is missing or names another token. A marker that names token but holds
    no well-formed root array stops the process.
    """
    try:
        with open(os.path.join(active, MARKER), encoding="utf-8") as marker:
            record = json.load(marker)
    except OSError:
        return ()
    except ValueError as problem:
        raise _fail(f"the marker of {active!r} is not JSON: {problem}") from None
    if not isinstance(record, dict) or record.get("token") != token:
        return ()
    try:
        return _path_items(record["roots"])
    except (KeyError, ValueError) as problem:
        raise _fail(f"the marker of {active!r} does not record its trusted roots: {problem}") from None


def _verify_marker(active: str, token: str) -> None:
    """Check the marker owner, UID, live PID, token, device, and inode. Forged fields can pass these checks."""
    path = os.path.join(active, MARKER)
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("not a regular file this user owns")
        with open(path, encoding="utf-8") as marker:
            record = json.load(marker)
        uid, pid, written = record["uid"], record["pid"], record["token"]
        device, inode = record["device"], record["inode"]
    except (OSError, ValueError, KeyError, TypeError) as problem:
        raise _fail(f"the run root {active!r} has no readable marker {MARKER}: {problem}") from None
    if uid != os.getuid():
        raise _fail(f"the marker of {active!r} names uid {uid!r}, not this user")
    try:
        current = os.lstat(active)
    except OSError:
        raise _fail(f"the run root {active!r} has no marker {MARKER} to compare") from None
    if (device, inode) != (current.st_dev, current.st_ino):
        raise _fail(f"the marker of {active!r} records a different device or inode")
    if not token or written != token:
        raise _fail(f"the marker of {active!r} does not hold the token in {TOKEN}")
    if not isinstance(pid, int) or pid <= 0:
        raise _fail(f"the marker of {active!r} names no process: {pid!r}")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        raise _fail(f"the run that marked {active!r} has ended, so the root is stale") from None
    except OSError:
        raise _fail(f"the run that marked {active!r} belongs to another user") from None


def _base(protected: tuple[str, ...]) -> str:
    """Return the first temporary directory outside every protected real root."""
    # No tempfile.gettempdir() here, because its writability probe would create a file before the guard exists.
    candidates = [os.environ.get("TMPDIR", ""), "/tmp", "/var/tmp", "/usr/tmp"]
    for value in candidates:
        if not value or not os.path.isabs(value):
            continue
        text = _real(value)
        if not os.path.isdir(text):
            continue
        if any(_within(text, real) for real in protected):
            continue
        return text
    raise _fail(f"no temporary directory outside {protected!r} to hold the run root")


def _check(active: str) -> None:
    """Reread the environment instead of trusting what was assigned or verified, and refuse the first value that escapes."""
    for name in _LAYOUT:
        got = os.environ.get(name, "")
        if not got or not os.path.isabs(got) or not _within(_real(got), active):
            raise _fail(f"{name} is {got!r} and does not resolve inside the run root {active!r}")
    for name in ("HOME", "TMPDIR"):
        if not os.path.isdir(os.environ[name]):
            raise _fail(f"{name} names {os.environ[name]!r}, which is not a directory")
    for label, value in (("Path.home()", Path.home()), ("expanduser('~')", os.path.expanduser("~")),
                         ("tempfile.gettempdir()", tempfile.gettempdir())):
        if not _within(_real(value), active):
            raise _fail(f"{label} is {str(value)!r} and not inside the run root {active!r}")


def _probe(active: str) -> None:
    """Delete a name that does not exist beside the run root and refuse unless the guard stops it first."""
    target = os.path.join(os.path.dirname(active), f".pstack-t3-isolation-probe-{secrets.token_hex(8)}")
    try:
        os.rmdir(target)
    except PermissionError:
        return
    except OSError:
        raise _fail(f"the guard let os.rmdir({target!r}) through, so a test could delete outside its run root") from None
    raise _fail(f"the guard let os.rmdir({target!r}) remove a directory outside the run root")


_FD_DIR = "/proc/self/fd/%d"
_PATH_LIMIT = 1024


def _fd_path(fd: int) -> str | None:
    """Return the path fd names, or None when neither /proc nor macOS F_GETPATH proves one.

    /proc/self/fd answers first and its answer is returned as it stands, because a descriptor for a
    socket or pipe answers with text that is not a path and callers already tell those apart. macOS has
    no /proc, so the path there comes from `fcntl` `F_GETPATH`, which fills a NUL-padded buffer. That
    buffer is trusted only when the path in it is absolute and `os.stat` of the path names the same
    device and inode as `os.fstat(fd)`, so a stale answer is not adopted and a path through an alias is
    judged as its target. None means no path could be read, and every caller refuses the call.
    """
    try:
        return os.readlink(_FD_DIR % fd)
    except OSError:
        pass
    getpath = getattr(fcntl, "F_GETPATH", None)
    if getpath is None:
        return None
    try:
        raw = fcntl.fcntl(fd, getpath, b"\0" * _PATH_LIMIT)
    except (OSError, ValueError):
        return None
    path = os.fsdecode(raw.split(b"\0", 1)[0]) if isinstance(raw, bytes) else ""
    if not path or not os.path.isabs(path):
        return None
    try:
        info = os.stat(path)
        same = (info.st_dev, info.st_ino) == (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
    except OSError:
        return None
    return path if same else None


def _placed(path, dir_fd) -> bool:
    """Return whether a call names its target through a descriptor: path is one, or path is relative to dir_fd."""
    if isinstance(path, int):
        return True
    return isinstance(dir_fd, int) and dir_fd >= 0 and not os.path.isabs(os.fsdecode(os.fspath(path)))


def _at(path, dir_fd) -> str | None:
    """Return the path a call acts on, joined to the directory dir_fd names when it is relative, or None when dir_fd cannot be read."""
    text = os.fsdecode(os.fspath(path))
    if not _placed(text, dir_fd):
        return text
    anchor = _fd_path(dir_fd)
    if anchor is None:
        return None
    return os.path.join(anchor, text)


def _fd_target(fd: int) -> str | None:
    """Return the path an open descriptor names, "" when it names no filesystem path, or None when it cannot be read."""
    target = _fd_path(fd)
    if target is None:
        return None
    return target if os.path.isabs(target) else ""


def _refuse(event: str, action: str, path, detail: str) -> PermissionError:
    return PermissionError(f"{event} refused: the test isolation guard blocks the {action} of {path!r} {detail}")


def _arg(args: tuple, index: int | None):
    return args[index] if index is not None and index < len(args) else None


def _write_intent(mode, flags) -> bool:
    if isinstance(mode, str) and any(mark in mode for mark in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _frozen(path):
    """Return the one value a call acts on: the descriptor itself, or the path the syscall will read, as str or bytes."""
    return path if isinstance(path, int) else os.fspath(path)
