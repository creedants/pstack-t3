"""Isolate a test process from the user's real home.

Importing this module only registers it as `pstack_test_isolation` in `sys.modules`. `install()` is
the one entry. Without the pointer it makes a run root, marks it, and points HOME, the five XDG
variables, and TMPDIR into it. With the pointer it adopts the run root the pointer names, after
proving that a live run of this user marked it with the token in the environment and that every
variable resolves inside it. Every install, the first and each repeat, rereads the environment and
raises SystemExit on the first value that escapes, so an install that finds a variable outside the
run root stops the process instead of letting it use a real path.

The guard is one audit hook, wrappers around os.open, os.mkfifo, and os.mknod, and a replacement
os.environ.clear. Its filesystem path checks resolve symlinks in the directory part first. A checked
delete or rename acts on a final symlink name itself. Checked deletes, rename sources, and replaced
rename destinations must sit inside the run root. The write handlers check protected real roots
outside the checkout. SQLite ATTACH remains unguarded. Removing a guarded variable is refused, and
so is setting one of them except the token to a relative or protected value. Setting any of them, the
token included, to an empty value is refused. os.environ.clear() keeps the guarded variables.

The defect this replaces: a helper that returned None for a pathlib.Path concluded the real
XDG_STATE_HOME was safe, and a cleanup rmtree then deleted the user's state directory.
"""

from __future__ import annotations

import contextlib
import json
import os
import pwd
import secrets
import stat
import sys
import tempfile
import threading
import urllib.parse
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
_GUARDED = frozenset(_LAYOUT) | {POINTER, TOKEN}

_CHECKOUT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
_WRITE_FLAGS = os.O_CREAT | os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_TRUNC

_root: str | None = None
_protected: tuple[str, ...] = ()
_armed = False
_created: set[str] = set()
_local = threading.local()
_os_open = os.open
_os_mkfifo = getattr(os, "mkfifo", None)
_os_mknod = getattr(os, "mknod", None)


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

    A runner calls this once, before it rewrites the environment of each child, so the child protects
    the caller's original home directories as well as its own run root.
    """
    pointer = os.environ.get(POINTER, "")
    roots = _real_roots(_real(pointer) if os.path.isabs(pointer) else None, os.environ)
    os.environ[ORIGINALS] = os.pathsep.join(roots)
    return roots


def _protected_write(real: str) -> bool:
    """Return whether a write at the real path lands in a protected real root, with the checkout allowed."""
    if _within(real, _CHECKOUT):
        return False
    return any(_within(real, base) for base in _protected)


def _owned(entry: str) -> bool:
    """Return whether a delete of the real entry stays inside the run root and outside every protected root."""
    return _root is not None and _within(entry, _root) and not any(_within(entry, base) for base in _protected)


@contextlib.contextmanager
def protecting(*paths):
    """Treat each path as one more protected real root until the block ends. It only adds protection."""
    global _protected
    before = _protected
    _protected = before + tuple(_real(path) for path in paths)
    try:
        yield
    finally:
        _protected = before


def layout(root) -> dict[str, str]:
    """Return the one mapping from each isolated variable to its directory under root."""
    base = os.path.abspath(os.fspath(root))
    return {name: os.path.join(base, part) for name, part in _LAYOUT.items()}


def allocate(base=None) -> Path:
    """Make a directory this process owns, under base or in a temporary directory outside every protected
    root, and return it. A guarded process hands out a directory only inside its run root or one this
    process allocated, so every path made here is recorded."""
    parent = _real(base) if base is not None else _base(_protected)
    created = _real(tempfile.mkdtemp(prefix="pstack-tests-", dir=parent))
    _created.add(created)
    return Path(created)


def _handout(path) -> str:
    """Return the real path a guarded process may write a marker or a hand-built home into.

    An unarmed process may write anywhere. An armed one may write inside its run root, or into a
    directory it allocated here. Any other directory is one this process did not make, so a child
    handed it could delete data this run never made.
    """
    real = _real(path)
    if not _armed or _root is None or _within(real, _root) or real in _created:
        return real
    raise PermissionError(
        f"the test isolation guard refuses {real!r} because it is outside the run root {_root!r} "
        "and this process did not create it"
    )


def mark(root) -> str:
    """Write the marker that lets a child adopt root, owned by this process and this user, and return its token."""
    token = secrets.token_hex(16)
    active = _handout(root)
    info = os.lstat(active)
    path = os.path.join(active, MARKER)
    with open(path, "w", encoding="utf-8") as marker:
        json.dump({"uid": os.getuid(), "pid": os.getpid(), "token": token,
                   "device": info.st_dev, "inode": info.st_ino}, marker)
    return token


def worker_env(env: dict[str, str], home) -> dict[str, str]:
    """Return env with the seven variables, the pointer, and the token set to the shard home, creating its directories and marker."""
    active = _handout(home)
    values = layout(active)
    for name, path in values.items():
        os.makedirs(path, mode=0o700 if name == "XDG_RUNTIME_DIR" else 0o777, exist_ok=True)
    environ = dict(env)
    environ.update(values)
    environ[POINTER] = active
    environ[TOKEN] = mark(active)
    # The child protects the caller's roots outside its own home, so a hand-built environment carries them too.
    environ[ORIGINALS] = os.pathsep.join(_real_roots(active, env))
    return environ


def inside(path) -> bool:
    """Return whether path, resolved through every symlink, is the active run root or below it. Never returns None to mean safe."""
    return _root is not None and _within(_real(path), _root)


def root() -> Path:
    """Return the active run root."""
    if _root is None:
        raise RuntimeError("tests.isolation.install() has not run in this process")
    return Path(_root)


def install() -> Path:
    """Adopt and verify the run root the pointer names, or create one when the environment names none, then
    arm the guard once and verify the environment it leaves. A repeat call verifies again from the environment."""
    global _root, _protected
    if POINTER in os.environ:
        pointer = os.environ[POINTER]
        protected = _real_roots(_real(pointer) if os.path.isabs(pointer) else None, os.environ)
        active = _adopt(os.environ, protected)
    else:
        protected = _real_roots(None, os.environ)
        active = _create(protected)
    if _root is not None and active != _root:
        raise _fail(f"{POINTER} now names {active!r}, not the run root {_root!r} this process installed")
    if _root is None:
        # tempfile caches its first gettempdir() result, so drop it for the TMPDIR verified here.
        tempfile.tempdir = None
        # The import system writes bytecode by renaming a temporary file over its entry in __pycache__. The guard
        # refuses a rename in the checkout, so bytecode for this process goes under the run root instead.
        sys.pycache_prefix = os.path.join(active, "pycache")
    _root = active
    _protected = tuple(dict.fromkeys(_protected + protected))
    # A subprocess of a test inherits these, so a nested runner keeps protecting the originals.
    os.environ[ORIGINALS] = os.pathsep.join(_protected)
    _arm()
    _check(active)
    _probe(active)
    return Path(active)


def _fail(problem: str) -> SystemExit:
    return SystemExit(f"pstack-t3 test isolation: {problem}")


def _adopt(environ, protected: tuple[str, ...]) -> str:
    """Return the real run root environ's pointer names, or raise SystemExit unless this user's live run marked
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
    """Raise SystemExit unless active holds this user's marker, with token, written by a process that is still alive."""
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
        raise _fail(f"the marker of {active!r} was written for another directory")
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


def _create(protected: tuple[str, ...]) -> str:
    """Make a fresh marked run root outside every protected real root, point the environment at it, and return it."""
    active = str(allocate(_base(protected)))
    for real in protected:
        if _within(active, real):
            raise _fail(f"the new run root {active!r} is inside the real path {real!r}")
    values = layout(active)
    for name, path in values.items():
        os.makedirs(path, mode=0o700 if name == "XDG_RUNTIME_DIR" else 0o777, exist_ok=True)
    os.environ.update(values)
    os.environ[POINTER] = active
    os.environ[TOKEN] = mark(active)
    return active


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


def _arm() -> None:
    """Add the audit hook, the function wrappers, and the os.environ.clear replacement, once per process."""
    global _armed
    if _armed:
        return
    sys.addaudithook(_hook)
    os.open = _open
    os.supports_dir_fd.add(_open)
    if _os_mkfifo is not None:
        os.mkfifo = _mkfifo
        os.supports_dir_fd.add(_mkfifo)
    if _os_mknod is not None:
        os.mknod = _mknod
        os.supports_dir_fd.add(_mknod)
    os.environ.clear = _clear_environ
    _armed = True


def _open(path, flags, mode=0o777, *, dir_fd=None):
    """os.open, refusing a write through dir_fd outside the run root or inside a protected root.

    The open audit event leaves out dir_fd, so the hook alone would judge a relative name against the working directory.
    """
    if dir_fd is not None and flags & _WRITE_FLAGS and not isinstance(path, int):
        target = _at(path, dir_fd)
        if target is None:
            raise _refuse("os.open", "write", path, "because its directory descriptor cannot be resolved")
        target = _real(target)
        if not inside(target) or any(_within(target, base) for base in _protected):
            raise _refuse("os.open", "write", target, f"outside the run root {_root!r} or inside a protected root")
    return _os_open(path, flags, mode, dir_fd=dir_fd)


def _mkfifo(path, mode=0o666, *, dir_fd=None):
    """os.mkfifo, refusing an entry inside a protected real root outside the checkout.

    CPython raises no audit event for this call, so the guard wraps it as it wraps os.open.
    """
    _deny_write("os.mkfifo", path, dir_fd, _entry)
    return _os_mkfifo(path, mode, dir_fd=dir_fd)


def _mknod(path, mode=0o600, device=0, *, dir_fd=None):
    """os.mknod, refusing an entry inside a protected real root outside the checkout."""
    _deny_write("os.mknod", path, dir_fd, _entry)
    return _os_mknod(path, mode, device, dir_fd=dir_fd)


def _clear_environ() -> None:
    """Remove every variable but the guarded ones, so clear() then update() still restores a saved environment."""
    for name in [name for name in os.environ if name not in _GUARDED]:
        del os.environ[name]


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


def _deny_delete(event: str, path, dir_fd) -> None:
    target = _at(path, dir_fd)
    if target is None:
        raise _refuse(event, "delete", path, "because its directory descriptor cannot be resolved")
    entry = _entry(target)
    if not _owned(entry):
        raise _refuse(event, "delete", entry, f"outside the run root {_root!r} or inside a protected root")


def _deny_write(event: str, path, dir_fd, resolve) -> None:
    if isinstance(path, int):
        target = _fd_target(path)
        if target == "":
            return
    else:
        target = _at(path, dir_fd)
    if target is None:
        raise _refuse(event, "write", path, "because its descriptor cannot be resolved")
    real = resolve(target)
    if _protected_write(real):
        raise _refuse(event, "write", real, "inside a protected real home")


def _guard_delete(event: str, args: tuple) -> None:
    if not isinstance(args[0], int):
        _deny_delete(event, args[0], _arg(args, 1))


def _guard_rename(event: str, args: tuple) -> None:
    """A rename removes its source and any destination it replaces, so both follow the delete rule. A new destination is a create."""
    source, dest = args[0], args[1]
    _deny_delete(event, source, _arg(args, 2))
    target = _at(dest, _arg(args, 3))
    if target is not None and os.path.lexists(target):
        _deny_delete(event, dest, _arg(args, 3))
    else:
        _deny_write(event, dest, _arg(args, 3), _entry)


def _guard_create(path_index: int, fd_index: int | None):
    def guard(event: str, args: tuple) -> None:
        _deny_write(event, args[path_index], _arg(args, fd_index), _entry)
    return guard


def _guard_link(event: str, args: tuple) -> None:
    """A hard link is a second name for its source, so a write through it lands in the source. The source follows the write rule."""
    _deny_write(event, args[0], _arg(args, 2), _real)
    _deny_write(event, args[1], _arg(args, 3), _entry)


def _guard_change(fd_index: int | None):
    def guard(event: str, args: tuple) -> None:
        _deny_write(event, args[0], _arg(args, fd_index), _real)
    return guard


def _write_intent(mode, flags) -> bool:
    if isinstance(mode, str) and any(mark in mode for mark in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _guard_open(event: str, args: tuple) -> None:
    path = args[0]
    if isinstance(path, int) or not _write_intent(_arg(args, 1), _arg(args, 2)):
        return
    _deny_write(event, path, None, _real)


def _guard_sqlite(event: str, args: tuple) -> None:
    database = os.fsdecode(os.fspath(args[0]))
    if database in ("", ":memory:"):
        return
    if database.startswith("file:"):
        parts = urllib.parse.urlsplit(database)
        name = urllib.parse.unquote(parts.path)
        if parts.netloc not in ("", "localhost") or not name:
            raise _refuse(event, "write", database, "because its file URI names no local path")
        if name == ":memory:":
            return
        database = name
    _deny_write(event, database, None, _real)


def _assignment_problem(key: str, value: str) -> str | None:
    """Return why the guard refuses value as the guarded name key, or None when it accepts it."""
    if not value:
        return "because it is empty"
    if key == TOKEN:
        return None
    if not os.path.isabs(value):
        return "because it is not an absolute path"
    target = _real(value)
    for real in _protected:
        if _within(target, real):
            return f"because {target!r} is inside the real path {real!r}"
    return None


def admits(env) -> bool:
    """Return whether this process can take env as its whole environment once the guard is armed.

    The guard refuses to remove a guarded name or to give it a refused value, so a caller that swaps
    the environment in place needs env to keep every guarded name it has now with a value it accepts.
    """
    if not _armed:
        return True
    for key in _GUARDED:
        if key in env:
            if _assignment_problem(key, env[key]) is not None:
                return False
        elif key in os.environ:
            return False
    return True


def _guard_putenv(event: str, args: tuple) -> None:
    key = os.fsdecode(args[0])
    if key not in _GUARDED:
        return
    value = os.fsdecode(args[1])
    problem = _assignment_problem(key, value)
    if problem is not None:
        raise _refuse(event, "assignment", value, f"as {key}, {problem}")


def _guard_unsetenv(event: str, args: tuple) -> None:
    key = os.fsdecode(args[0])
    if key in _GUARDED:
        raise _refuse(event, "removal", key, "because it is required by the test isolation environment")


_HANDLERS = {
    "shutil.rmtree": _guard_delete,
    "os.remove": _guard_delete,
    "os.rmdir": _guard_delete,
    "os.rename": _guard_rename,
    "os.mkdir": _guard_create(0, 2),
    "os.symlink": _guard_create(1, 2),
    "os.link": _guard_link,
    "os.truncate": _guard_change(None),
    "os.chmod": _guard_change(2),
    "os.utime": _guard_change(3),
    "os.chown": _guard_change(3),
    "open": _guard_open,
    "sqlite3.connect": _guard_sqlite,
    "os.putenv": _guard_putenv,
    "os.unsetenv": _guard_unsetenv,
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


# A runner child loads this file by path as pstack_test_isolation before discovery imports it as
# tests.isolation. Both names give the first copy, so a process holds one guard and one run root.
_first = sys.modules.setdefault("pstack_test_isolation", sys.modules[__name__])
if _first is not sys.modules[__name__] and os.path.realpath(getattr(_first, "__file__", "") or "") == os.path.realpath(__file__):
    sys.modules[__name__] = _first
