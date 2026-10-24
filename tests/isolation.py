"""Isolate a test process from the user's real home.

Importing this module has no side effect. `install()` is the one entry. It makes or adopts a run
root, points HOME, the five XDG variables, and TMPDIR into it, and installs one audit hook. The hook
refuses a delete outside the run root and a write inside a protected real root. An activation failure
raises SystemExit, so a process can never fall back to a real path.

The defect this replaces: a helper that returned None for a pathlib.Path concluded the real
XDG_STATE_HOME was safe, and a cleanup rmtree then deleted the user's state directory.
"""

from __future__ import annotations

import os
import pwd
import sys
import tempfile
import threading
from pathlib import Path

POINTER = "PSTACK_TEST_HOME_ROOT"

_LAYOUT = {
    "HOME": "home",
    "XDG_STATE_HOME": "state",
    "XDG_CONFIG_HOME": "config",
    "XDG_DATA_HOME": "data",
    "XDG_CACHE_HOME": "cache",
    "XDG_RUNTIME_DIR": "runtime",
    "TMPDIR": "tmp",
}

_CHECKOUT = Path(__file__).resolve().parents[1]
_WRITE_FLAGS = os.O_CREAT | os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_TRUNC

_root: str | None = None
_protected: tuple[str, ...] = ()
_local = threading.local()


def _text(path) -> str:
    """Return the absolute lexical form of a str, bytes, or path-like value. Calls os.fspath on it."""
    return os.path.normpath(os.path.abspath(os.fsdecode(os.fspath(path))))


def _within(path, base) -> bool:
    """Return whether path is base or below it, comparing whole components of the lexical paths."""
    path, base = _text(path), _text(base)
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def _real_roots(root: str | None) -> tuple[str, ...]:
    """Return the real home and the inherited home variables that sit outside root, resolved as paths only.

    A value that is root or below it belongs to root, so it is not protected. A value that holds root
    is an enclosing run root, which a nested runner makes, so it is not protected either. The real home
    from pwd is always protected, even when it holds root, so adopting a root inside it is refused.
    Nothing here lists a directory.
    """
    found: list[str] = []
    home = ""
    try:
        home = pwd.getpwuid(os.getuid()).pw_dir
    except (KeyError, OSError):
        pass
    candidates = [home, os.environ.get("HOME", "")]
    candidates.extend(os.environ.get(name, "") for name in _LAYOUT if name.startswith("XDG_"))
    home = _text(home) if home else ""
    for value in candidates:
        if not value or not os.path.isabs(value):
            continue
        text = _text(value)
        if root is not None and text != home:
            if _within(text, root):
                continue
            if _within(root, text):
                continue
        if text not in found:
            found.append(text)
    return tuple(found)


def _protected_write(path) -> bool:
    """Return whether a write at path lands in a protected real root, with the checkout allowed."""
    text = _text(path)
    if _within(text, _CHECKOUT):
        return False
    return any(_within(text, real) for real in _protected)


def layout(root) -> dict[str, str]:
    """Return the one mapping from each isolated variable to its directory under root."""
    base = _text(root)
    return {name: os.path.join(base, part) for name, part in _LAYOUT.items()}


def worker_env(env: dict[str, str], home) -> dict[str, str]:
    """Return env with the seven variables and the pointer set to the shard home, creating its directories."""
    values = layout(home)
    for name, path in values.items():
        os.makedirs(path, mode=0o700 if name == "XDG_RUNTIME_DIR" else 0o777, exist_ok=True)
    environ = dict(env)
    environ.update(values)
    environ[POINTER] = _text(home)
    return environ


def inside(path) -> bool:
    """Return whether path is the active run root or below it. Never returns None to mean safe."""
    text = _text(path)
    return _root is not None and _within(text, _root)


def root() -> Path:
    """Return the active run root."""
    if _root is None:
        raise RuntimeError("tests.isolation.install() has not run in this process")
    return Path(_root)


def install() -> Path:
    """Create or adopt the run root, point the environment at it, and install the guard. Idempotent per process."""
    global _root, _protected
    if _root is not None:
        return Path(_root)
    adopted = POINTER in os.environ
    if adopted:
        pointer = os.environ[POINTER]
        if not pointer or not os.path.isabs(pointer):
            raise SystemExit(f"pstack-t3 test isolation: {POINTER} is not an absolute path: {pointer!r}")
        active = _text(pointer)
        protected = _real_roots(active)
        _verify_adopted(active, protected)
    else:
        protected = _real_roots(None)
        active = _create(protected)
    _root, _protected = active, protected
    sys.addaudithook(_hook)
    if adopted:
        _check_adopted(protected)
    else:
        _check(active, protected)
    _probe(protected)
    return Path(active)


def _fail(problem: str) -> SystemExit:
    return SystemExit(f"pstack-t3 test isolation: {problem}")


def _verify_adopted(active: str, protected: tuple[str, ...]) -> None:
    """Raise SystemExit unless the pointer is a real directory outside every protected real root and every
    home variable is set, absolute, and outside every protected real root.

    A test may point a child at its own fixture config or state directory, so a value need not be the
    layout directory of the run root. What it may never do is name a path in the real home.
    """
    for real in protected:
        if _within(active, real):
            raise _fail(f"{POINTER} names {active!r}, which is inside the real path {real!r}")
    if not os.path.isdir(active):
        raise _fail(f"{POINTER} names {active!r}, which is not a directory")
    _verify_home_values(protected)
    for name in ("HOME", "TMPDIR"):
        if not os.path.isdir(os.environ[name]):
            raise _fail(f"{name} names {os.environ[name]!r}, which is not a directory")


def _verify_home_values(protected: tuple[str, ...]) -> None:
    """Raise SystemExit unless each of the seven variables is set, absolute, and outside every protected real root."""
    for name in _LAYOUT:
        got = os.environ.get(name)
        if not got or not os.path.isabs(got):
            raise _fail(f"{name} is {got!r} and does not name an absolute directory")
        for real in protected:
            if _within(got, real):
                raise _fail(f"{name} is {got!r}, which is inside the real path {real!r}")


def _create(protected: tuple[str, ...]) -> str:
    """Make a fresh run root outside every protected real root, point the environment at it, and return it."""
    base = _base(protected)
    active = _text(tempfile.mkdtemp(prefix="pstack-tests-", dir=base))
    for real in protected:
        if _within(active, real):
            raise _fail(f"the new run root {active!r} is inside the real path {real!r}")
    values = layout(active)
    for name, path in values.items():
        os.makedirs(path, mode=0o700 if name == "XDG_RUNTIME_DIR" else 0o777, exist_ok=True)
    os.environ.update(values)
    os.environ[POINTER] = active
    # tempfile caches the first gettempdir() result, so drop it for the TMPDIR just set.
    tempfile.tempdir = None
    return active


def _base(protected: tuple[str, ...]) -> str:
    """Return the first temporary directory outside every protected real root."""
    # No tempfile.gettempdir() here, because its writability probe would create a file before the guard exists.
    candidates = [os.environ.get("TMPDIR", ""), "/tmp", "/var/tmp", "/usr/tmp"]
    for value in candidates:
        if not value or not os.path.isabs(value):
            continue
        text = _text(value)
        if not os.path.isdir(text):
            continue
        if any(_within(text, real) for real in protected):
            continue
        return text
    raise _fail(f"no temporary directory outside {protected!r} to hold the run root")


def _check(active: str, protected: tuple[str, ...]) -> None:
    """Reread the environment instead of trusting what was assigned, and refuse the first value that escapes."""
    values = layout(active)
    for name, want in values.items():
        got = os.environ.get(name)
        if got != want or not os.path.isdir(got):
            raise _fail(f"{name} is {got!r} after activation and should be the directory {want!r}")
        for real in protected:
            if _within(want, real):
                raise _fail(f"{name} names {want!r}, which is inside the real path {real!r}")
    if not _within(Path.home(), active) or not _within(os.path.expanduser("~"), active):
        raise _fail(f"Path.home() is {Path.home()!r} and not inside the run root {active!r}")
    if not _within(tempfile.gettempdir(), active):
        raise _fail(f"tempfile.gettempdir() is {tempfile.gettempdir()!r} and not inside the run root {active!r}")


def _check_adopted(protected: tuple[str, ...]) -> None:
    """Reread the adopted environment and refuse the first value that escapes."""
    _verify_home_values(protected)
    for path in (Path.home(), os.path.expanduser("~")):
        for real in protected:
            if _within(path, real):
                raise _fail(f"{path!r} is inside the real path {real!r}")
    for name in ("HOME", "TMPDIR"):
        if not os.path.isdir(os.environ[name]):
            raise _fail(f"{name} names {os.environ[name]!r}, which is not a directory")


def _probe(protected: tuple[str, ...]) -> None:
    """Delete a name that does not exist under a protected real root and refuse unless the guard stops it first."""
    for real in protected:
        target = os.path.join(real, f".pstack-t3-isolation-probe-{os.getpid()}")
        if os.path.lexists(target):
            continue
        try:
            os.remove(target)
        except PermissionError:
            return
        except OSError:
            raise _fail(f"the guard let os.remove({target!r}) through, so a test could delete a real file") from None
        raise _fail(f"the guard let os.remove({target!r}) delete nothing, so it does not refuse a real path")
    raise _fail("no protected real root is left to prove the guard refuses a delete")


def _name(path, dir_fd) -> str | None:
    """Return the lexical path a guarded call acts on, joining a relative path to its dir_fd, or None to refuse."""
    try:
        text = os.fsdecode(os.fspath(path))
    except TypeError:
        return None
    if dir_fd is not None and isinstance(dir_fd, int) and dir_fd >= 0 and not os.path.isabs(text):
        try:
            anchor = os.readlink(f"/proc/self/fd/{dir_fd}")
        except OSError:
            return None
        text = os.path.join(anchor, text)
    return _text(text)


def _refuse(event: str, action: str, path, detail: str) -> PermissionError:
    return PermissionError(f"{event} refused: the test isolation guard blocks the {action} of {path!r} {detail}")


def _guard_delete(event: str, args: tuple) -> None:
    path = args[0]
    if isinstance(path, int):
        return
    target = _name(path, args[1] if len(args) > 1 else None)
    if target is None:
        raise _refuse(event, "delete", path, "because its directory descriptor cannot be resolved")
    if not inside(target):
        raise _refuse(event, "delete", target, f"outside the run root {_root!r}")


def _guard_write(event: str, args: tuple, pairs: tuple[tuple[int, int | None], ...]) -> None:
    for index, fd_index in pairs:
        if index >= len(args):
            continue
        path = args[index]
        if isinstance(path, int):
            continue
        target = _name(path, args[fd_index] if fd_index is not None and fd_index < len(args) else None)
        if target is None:
            raise _refuse(event, "write", path, "because its directory descriptor cannot be resolved")
        if _protected_write(target):
            raise _refuse(event, "write", target, "inside a protected real home")


def _write_intent(mode, flags) -> bool:
    if isinstance(mode, str) and any(mark in mode for mark in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _guard_open(event: str, args: tuple) -> None:
    path = args[0]
    if isinstance(path, int):
        return
    if not _write_intent(args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None):
        return
    target = _name(path, None)
    if target is None or _protected_write(target):
        raise _refuse(event, "write", target if target is not None else path, "inside a protected real home")


def _guard_putenv(event: str, args: tuple) -> None:
    key = os.fsdecode(args[0]) if isinstance(args[0], bytes) else str(args[0])
    if key != "HOME" and not key.startswith("XDG_"):
        return
    value = os.fsdecode(args[1]) if isinstance(args[1], bytes) else str(args[1])
    if not value or not os.path.isabs(value):
        return
    target = _text(value)
    for real in _protected:
        if _within(target, real):
            raise _refuse(event, "assignment", target, f"as {key}, inside the real path {real!r}")


_HANDLERS = {
    "shutil.rmtree": _guard_delete,
    "os.remove": _guard_delete,
    "os.rmdir": _guard_delete,
    "os.mkdir": lambda event, args: _guard_write(event, args, ((0, 2),)),
    "os.rename": lambda event, args: _guard_write(event, args, ((0, 2), (1, 3))),
    "os.symlink": lambda event, args: _guard_write(event, args, ((1, 2),)),
    "os.link": lambda event, args: _guard_write(event, args, ((0, 2), (1, 3))),
    "os.truncate": lambda event, args: _guard_write(event, args, ((0, None),)),
    "open": _guard_open,
    "os.putenv": _guard_putenv,
}


def _hook(event: str, args: tuple) -> None:
    handler = _HANDLERS.get(event)
    if handler is None or getattr(_local, "busy", False):
        return
    _local.busy = True
    try:
        handler(event, args)
    finally:
        _local.busy = False
