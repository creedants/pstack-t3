"""Isolate a test process from the user's real home.

Importing this module only registers it as `pstack_test_isolation` in `sys.modules`. `install()` is
the one entry. Without the pointer it makes a run root, marks it, and points HOME, the five XDG
variables, and TMPDIR into it. With the pointer it adopts the run root the pointer names, after
checking marker metadata against the environment token and checking that every
variable resolves inside it. Every install, the first and each repeat, rereads the environment and
raises SystemExit on the first value that escapes, so an install that finds a variable outside the
run root stops the process instead of letting it use a real path.

The guard is one audit hook, wrappers around os.open, os.mkfifo, os.mknod, os.chown, and os.lchown,
and a replacement os.environ.clear. Its filesystem path checks resolve symlinks in the directory
part first. A checked delete or rename acts on a final symlink name itself. Checked deletes, rename
sources, and replaced rename destinations must sit inside the run root. A descriptor's path comes from
/proc/self/fd, then from macOS F_GETPATH checked by device and inode. A call on a descriptor that
resolves through neither is refused, and a write placed through a descriptor must also sit inside the
run root. The write handlers check protected real roots outside the checkout. SQLite ATTACH remains
unguarded. Removing a guarded variable or setting one to an empty value is refused. Guarded path
variables reject relative or protected paths. The token and root record have their own assignment
rules. os.environ.clear() keeps the guarded variables.

PSTACK_TEST_PROTECTED_ROOTS carries a JSON array of absolute paths. This process keeps its trusted
roots in memory, and a run root's marker carries the roots of the process that marked it, so a child
that adopts the root protects them whatever record its environment holds. Assignments cannot drop
those roots, and os.environ.clear() keeps the record. A malformed inherited root record stops the
process. A marker with the inherited token and missing or malformed roots also stops the process.
A malformed assignment is refused. os.chown with follow_symlinks False and os.lchown are wrapped,
because the os.chown audit event carries no follow_symlinks field, so a protected link
name whose target is outside protection stays refused.

The defect this replaces: a helper that returned None for a pathlib.Path concluded the real
XDG_STATE_HOME was safe, and a cleanup rmtree then deleted the user's state directory.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path


def _sibling(name: str, filename: str):
    """Return the module named name, loading filename from this directory when it is not loaded yet."""
    module = sys.modules.get(name)
    if module is None:
        from importlib.util import module_from_spec, spec_from_file_location

        spec = spec_from_file_location(name, Path(__file__).with_name(filename))
        module = module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


_sibling("pstack_test_isolation_paths", "isolation_paths.py")
# MARKER stays exported because the adoption tests read isolation.MARKER to reach a run root's marker file.
from pstack_test_isolation_paths import (
    MARKER, ORIGINALS, POINTER, TOKEN, _LAYOUT, _WRITE_FLAGS, _adopt, _arg, _at, _base, _check,
    _entry, _fail, _fd_target, _placed, _probe, _real, _real_roots, _refuse, _within,
    _write_intent, encode_roots, layout, parse_roots, write_marker,
)


_GUARDED = frozenset(_LAYOUT) | {POINTER, TOKEN, ORIGINALS}

_CHECKOUT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

_root: str | None = None
_protected: tuple[str, ...] = ()
_originals: tuple[str, ...] = ()
_armed = False
_created: set[str] = set()
_local = threading.local()
_os_open = os.open
_os_mkfifo = getattr(os, "mkfifo", None)
_os_mknod = getattr(os, "mknod", None)
_os_chown = getattr(os, "chown", None)
_os_lchown = getattr(os, "lchown", None)


def _protected_write(real: str) -> bool:
    """Return whether a write at the real path lands in a protected real root, with the checkout allowed."""
    if _within(real, _CHECKOUT):
        return False
    return any(_within(real, base) for base in _protected)


def _owned(real: str) -> bool:
    """Return whether the real path stays inside the run root and outside every protected root."""
    return _root is not None and _within(real, _root) and not any(_within(real, base) for base in _protected)


def _record(roots) -> None:
    """Merge roots into the trusted record, publish it in ORIGINALS, and protect it from now on.

    The environment write happens before the in-memory state changes, so a refused write changes
    nothing. A repeat call with a subset changes nothing.
    """
    global _originals, _protected
    merged = tuple(dict.fromkeys(_originals + tuple(_real(root) for root in roots)))
    os.environ[ORIGINALS] = encode_roots(merged)
    _originals = merged
    _protected = tuple(dict.fromkeys(_protected + merged))


@contextlib.contextmanager
def protecting(*paths):
    """Treat each path as one more protected real root until the block ends. It only adds protection."""
    global _protected
    before = _protected
    _protected = before + tuple(_real(path) for path in paths)
    try:
        yield
    finally:
        _protected = tuple(dict.fromkeys(before + _originals))


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
    directory it allocated here. Any other directory lacks an allocation record here, so a child
    handed it could delete data this run never made.
    """
    real = _real(path)
    if not _armed or _root is None or _within(real, _root) or real in _created:
        return real
    raise PermissionError(
        f"the test isolation guard refuses {real!r} because it is outside the run root {_root!r} "
        "and allocate() has no record of it"
    )


def mark(root, roots=None) -> str:
    """Write the marker that lets a child adopt root and carries the trusted roots, and return its token."""
    return write_marker(_handout(root), _originals if roots is None else tuple(roots))


def worker_env(env: dict[str, str], home) -> dict[str, str]:
    """Return a copy of env with the shard's layout, pointer, token, and protected-root record.

    Create the shard directories and a marker that carries the merged roots. Merge the trusted roots
    held in memory with the roots env names, so an empty or rewritten input drops none.
    """
    active = _handout(home)
    roots = tuple(dict.fromkeys(_originals + _real_roots(active, env)))
    values = layout(active)
    for name, path in values.items():
        os.makedirs(path, mode=0o700 if name == "XDG_RUNTIME_DIR" else 0o777, exist_ok=True)
    environ = dict(env)
    environ.update(values)
    environ[POINTER] = active
    environ[TOKEN] = mark(active, roots)
    environ[ORIGINALS] = encode_roots(roots)
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
    # Publish the trusted record. The first install runs before _arm(); a repeat install writes a superset.
    _record(protected)
    _arm()
    _check(active)
    _probe(active)
    return Path(active)


def remember_originals() -> tuple[str, ...]:
    """Record the protected roots this process starts with, before a runner rewrites a child environment."""
    pointer = os.environ.get(POINTER, "")
    _record(_real_roots(_real(pointer) if os.path.isabs(pointer) else None, os.environ))
    return _originals


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
    os.environ[TOKEN] = mark(active, protected)
    return active


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
    for original, replacement, name in ((_os_chown, _chown, "chown"), (_os_lchown, _lchown, "lchown")):
        if original is None:
            continue
        setattr(os, name, replacement)
        for supported in (os.supports_dir_fd, os.supports_fd, os.supports_follow_symlinks):
            if original in supported:
                supported.add(replacement)
    os.environ.clear = _clear_environ
    _armed = True


def _open(path, flags, mode=0o777, *, dir_fd=None):
    """os.open, refusing a write through dir_fd outside the run root or inside a protected root.

    The open audit event leaves out dir_fd, so the hook alone would judge a relative name against the working directory.
    """
    path = os.fspath(path)
    if dir_fd is not None and flags & _WRITE_FLAGS and not isinstance(path, int):
        target = _at(path, dir_fd)
        if target is None:
            raise _refuse("os.open", "write", path, "because its directory descriptor cannot be resolved")
        target = _real(target)
        if not _owned(target):
            raise _refuse("os.open", "write", target, f"outside the run root {_root!r} or inside a protected root")
    return _os_open(path, flags, mode, dir_fd=dir_fd)


def _mkfifo(path, mode=0o666, *, dir_fd=None):
    """os.mkfifo, refusing an entry inside a protected real root outside the checkout.

    CPython raises no audit event for this call, so the guard wraps it as it wraps os.open.
    """
    path = os.fspath(path)
    _deny_write("os.mkfifo", path, dir_fd, _entry)
    return _os_mkfifo(path, mode, dir_fd=dir_fd)


def _mknod(path, mode=0o600, device=0, *, dir_fd=None):
    """os.mknod, refusing an entry inside a protected real root outside the checkout."""
    path = os.fspath(path)
    _deny_write("os.mknod", path, dir_fd, _entry)
    return _os_mknod(path, mode, device, dir_fd=dir_fd)


def _chown(path, uid, gid, *, dir_fd=None, follow_symlinks=True):
    """os.chown, refusing a change to the link name itself inside a protected real root outside the checkout."""
    path = os.fspath(path)
    if not follow_symlinks:
        _deny_write("os.chown", path, dir_fd, _entry)
    return _os_chown(path, uid, gid, dir_fd=dir_fd, follow_symlinks=follow_symlinks)


def _lchown(path, uid, gid):
    """os.lchown, refusing a change to the link name itself inside a protected real root outside the checkout."""
    path = os.fspath(path)
    _deny_write("os.lchown", path, None, _entry)
    return _os_lchown(path, uid, gid)


def _clear_environ() -> None:
    """Remove every variable but the guarded ones, so clear() then update() still restores a saved environment."""
    for name in [name for name in os.environ if name not in _GUARDED]:
        del os.environ[name]


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
    if _placed(path, dir_fd) and not _owned(real):
        raise _refuse(event, "write", real, f"outside the run root {_root!r} or inside a protected root")


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
    if key == ORIGINALS:
        try:
            roots = parse_roots(value)
        except ValueError as problem:
            return f"because it is not a record of absolute paths: {problem}"
        for real in _originals:
            if real not in roots:
                return f"because it drops the recorded root {real!r}"
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
