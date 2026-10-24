"""Path rules, protected real roots, and run-root admission for the test isolation guard.

`tests/isolation.py` imports from here and holds the guard state, the audit hook, the wrappers, and `install()`.
"""

from __future__ import annotations

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


def _real_roots(root: str | None, environ) -> tuple[str, ...]:
    """Return the real home and the home variables of environ that sit outside root, as real paths.

    A value that is root or below it belongs to root, so it is not protected. A value that holds root
    is not protected either, which covers an enclosing run root that a nested runner makes. The real
    home from pwd is always protected, even when it holds root, so adopting a root inside it is refused.
    The roots a caller recorded in ORIGINALS are appended whatever else the environment says.
    Nothing here lists a directory.
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
    return tuple(found)


def _original_roots(environ) -> tuple[str, ...]:
    """Return the protected real roots a caller recorded in environ, without an empty or relative entry."""
    found: list[str] = []
    for entry in environ.get(ORIGINALS, "").split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        text = _real(entry)
        if text not in found:
            found.append(text)
    return tuple(found)


def remember_originals() -> tuple[str, ...]:
    """Record the protected real roots this process starts with in ORIGINALS, and return them.

    A runner calls this before rewriting child environments. Propagation requires unchanged
    ORIGINALS and paths without colons. An empty worker_env input can also drop original roots.
    """
    pointer = os.environ.get(POINTER, "")
    roots = _real_roots(_real(pointer) if os.path.isabs(pointer) else None, os.environ)
    os.environ[ORIGINALS] = os.pathsep.join(roots)
    return roots


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


def _at(path, dir_fd) -> str | None:
    """Return the path a call acts on, joined to the directory dir_fd names when it is relative, or None when dir_fd cannot be read."""
    text = os.fsdecode(os.fspath(path))
    if isinstance(dir_fd, int) and dir_fd >= 0 and not os.path.isabs(text):
        try:
            anchor = os.readlink(f"/proc/self/fd/{dir_fd}")
        except OSError:
            return None
        text = os.path.join(anchor, text)
    return text


def _fd_target(fd: int) -> str | None:
    """Return the path an open descriptor names, "" when it names no filesystem path, or None when it cannot be read."""
    try:
        target = os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
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
