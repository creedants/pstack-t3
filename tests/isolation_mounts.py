"""The mount table, and the storage identity a path reaches, for the test isolation guard.

`os.path.realpath` follows symlinks and not mounts, so a bind mount inside the run root makes a
protected directory answer to a run-root name. `_mount_table` reads `/proc/self/mountinfo` at check
time, and `_identities` gives a path a candidate storage identity for every mount point above it,
including entries a later mount hides. `_reaches` compares every candidate the target has with every
candidate every protected root has, so an alias that reaches either backing storage is refused.

A non-Linux platform, which includes macOS, has no table, and the spelling rules stand alone there.
On Linux a table that cannot be read, is empty, or holds a malformed entry stops the process, and so
does a path or protected root that no mount covers.
"""

from __future__ import annotations

import os
import sys

_ESCAPES = {b"040": b" ", b"011": b"\t", b"012": b"\n", b"134": b"\\"}
_MOUNTINFO = "/proc/self/mountinfo"
_LINUX = sys.platform.startswith("linux")


def _fail(problem: str) -> SystemExit:
    return SystemExit(f"pstack-t3 test isolation: {problem}")


def _within(path: str, base: str) -> bool:
    """Return whether the real path is the real base or below it, comparing whole components."""
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


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
    if not _LINUX:
        return ()
    try:
        with open(_MOUNTINFO, "rb") as table:
            lines = table.read().splitlines()
    except OSError as problem:
        raise _fail(f"the mount table {_MOUNTINFO!r} cannot be read: {problem}") from None
    found = []
    for line in lines:
        before, separator, _rest = line.partition(b" - ")
        fields = before.split()
        if not separator or len(fields) < 5 or not fields[0].isdigit():
            raise _fail(f"the mount table {_MOUNTINFO!r} holds the malformed line {line!r}")
        found.append((_unescape(fields[4]), os.fsdecode(fields[2]), _unescape(fields[3])))
    if not found:
        raise _fail(f"the mount table {_MOUNTINFO!r} names no mount")
    return tuple(found)


def _identities(path: str, table) -> tuple[tuple[str, str], ...]:
    """Return the device and on-device path of every mount point that covers the path.

    A hidden entry stays in the table and still names storage an alias can reach, so the comparison
    keeps every entry on both sides.
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
    """Return whether the path sits on the storage any base names or below it, whatever its spelling."""
    table = _mount_table()
    here = _identities(path, table)
    if not here:
        if _LINUX:
            raise _fail(f"the mount table names no mount above the path {path!r}")
        return False
    theirs = []
    for base in bases:
        found = _identities(base, table)
        if not found and _LINUX:
            raise _fail(f"the mount table names no mount above the protected root {base!r}")
        theirs.extend(found)
    return any(_covers(identity, base) for identity in here for base in theirs)
