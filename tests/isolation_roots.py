"""Carry a guarded process's trusted roots to a child that inherits no environment.

The guard keeps its protected roots in memory and in the environment. A child started with
`env={"PATH": ...}` inherits neither, so it used to protect only the real home from pwd and wrote
into the roots its parent protected. One record per guarded process, keyed by pid, closes that gap.
It lives in a fixed per-user directory under the first temporary directory that is a directory and
is not inside a protected root, because a child with no environment cannot be told where to look.
Every function here takes and returns real absolute path strings.

A record that exists is proof the parent was guarded, so a child that cannot read it stops the
process instead of running with fewer roots. That is the fail-closed rule.
"""

from __future__ import annotations

import json
import os
import stat

_TEMPORARIES = ("/tmp", "/var/tmp", "/usr/tmp")
_PREFIX = "pstack-t3-isolation-"


def _fail(problem: str) -> SystemExit:
    return SystemExit(f"pstack-t3 test isolation: {problem}")


def _real(path) -> str:
    return os.path.realpath(os.fsdecode(os.fspath(path)))


def _under(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def bases() -> tuple[str, ...]:
    """Return the real paths of the candidate temporary directories that exist."""
    return tuple(_real(path) for path in _TEMPORARIES if os.path.isdir(path))


def directory(protected) -> str:
    """Return this user's record directory under the first candidate outside every protected root.

    Create it with mode 0700 when it is absent. Stop the process when the entry is not a directory
    this user owns with no group or other write bit, or when no candidate qualifies.
    """
    for base in bases():
        if any(_under(base, root) for root in protected):
            continue
        path = os.path.join(base, _PREFIX + str(os.getuid()))
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


def publish(pid: int, roots, protected) -> str:
    """Write the record of pid's trusted roots with mode 0600 and return its path."""
    path = os.path.join(directory(protected), f"{pid}.json")
    body = json.dumps({"pid": pid, "uid": os.getuid(), "roots": [_real(root) for root in roots]})
    try:
        number = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(number, "w", encoding="utf-8") as record:
            record.write(body)
    except OSError as problem:
        raise _fail(f"cannot write the record {path!r}: {problem}") from None
    return path


def record(pid: int) -> str | None:
    """Return the path of the record for pid when a candidate directory holds one, else None."""
    for base in bases():
        path = os.path.join(base, _PREFIX + str(os.getuid()), f"{pid}.json")
        if os.path.lexists(path):
            return path
    return None


def _roots_of(path: str, pid: int) -> tuple[str, ...]:
    """Return the real roots the record at path holds for pid, or stop the process when it is unusable."""
    try:
        with open(path, encoding="utf-8") as handle:
            body = json.load(handle)
    except (OSError, ValueError) as problem:
        raise _fail(f"the record {path!r} cannot be read: {problem}") from None
    if not isinstance(body, dict):
        raise _fail(f"the record {path!r} is not a JSON object")
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


def inherited() -> tuple[str, ...]:
    """Return the real trusted roots this process or its parent published, or () when neither did.

    This process's own record counts, because a guarded process that replaces itself with a stripped
    environment keeps its pid and its parent is then the grandparent. A record that cannot be read, is
    not JSON, is not an object, names another uid or another pid, or holds a root that is not an
    absolute path stops the process.
    """
    found: list[str] = []
    for pid in (os.getpid(), os.getppid()):
        path = record(pid)
        if path is None:
            continue
        for root in _roots_of(path, pid):
            if root not in found:
                found.append(root)
    return tuple(found)


def prune(protected) -> None:
    """Remove the records in this user's directory whose pid names no live process.

    Call it only before the guard is armed, because the guard refuses a delete outside the run root.
    """
    folder = directory(protected)
    for name in os.listdir(folder):
        stem, dot, suffix = name.partition(".")
        if not dot or suffix != "json" or not stem.isdigit() or int(stem) <= 0:
            continue
        try:
            os.kill(int(stem), 0)
            continue
        except ProcessLookupError:
            pass
        except OSError:
            continue
        try:
            os.unlink(os.path.join(folder, name))
        except FileNotFoundError:
            pass
