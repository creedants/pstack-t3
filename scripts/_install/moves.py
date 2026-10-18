from __future__ import annotations

import ctypes
import errno
import os
import shutil
import stat
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from . import layout

RENAME_NOREPLACE = 1

AT_FDCWD = -100

# Darwin renamex_np flag from stdio.
RENAME_EXCL = 0x00000004

def rename_noreplace(source, target):
    """Rename `source` to `target` only while `target` is empty. Return 0, or the errno that stopped it."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except (OSError, AttributeError):
        return errno.ENOSYS
    raw_source = os.fsencode(source)
    raw_target = os.fsencode(target)
    rows = (
        ("renameat2", (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint),
         (AT_FDCWD, raw_source, AT_FDCWD, raw_target, RENAME_NOREPLACE)),
        ("renamex_np", (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint),
         (raw_source, raw_target, RENAME_EXCL)),
    )
    for symbol, argtypes, arguments in rows:
        call = getattr(libc, symbol, None)
        if call is None:
            continue
        call.argtypes = argtypes
        call.restype = ctypes.c_int
        if call(*arguments) == 0:
            return 0
        code = ctypes.get_errno()
        if code != errno.ENOSYS:
            return code
    return errno.ENOSYS

def place(source, path):
    """Move the entry at `source` to `path` only while `path` is still empty. Return 0, or the errno that stopped it.

    `source` is a name only the caller uses, because the link and the unlink each look it up again.
    Once the link worked the entry is at `path`, so a source that is gone has no name left to remove.
    """
    try:
        # A hard link of the link itself fails on a taken path, where a rename would replace it.
        os.link(source, path, follow_symlinks=False)
    except FileExistsError:
        return errno.EEXIST
    except (OSError, NotImplementedError) as error:
        # A rename cannot cross a filesystem that a hard link cannot.
        if getattr(error, "errno", None) == errno.EXDEV:
            return errno.EXDEV
        # A directory cannot be hard linked. A plain rename would replace whatever took the path since.
        return rename_noreplace(source, path)
    with suppress(FileNotFoundError):
        os.unlink(source)
    return 0

def refusal(path, code):
    """Why `place` left its entry where it was, or None when it moved it."""
    if code == 0:
        return None
    if code in (errno.EEXIST, errno.ENOTEMPTY):
        return f"{path} was taken again"
    return f"{path} cannot be refilled without risking an overwrite ({os.strerror(code)})"

def put_back(aside, path):
    """Return the entry at `aside` to `path` only while `path` is still empty. Return why it stayed aside."""
    return refusal(path, place(aside, path))

def discard(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.unlink(path)

HOLDER = ".pstack-t3-"

SCRAP = ".pstack-t3-scrap-"

SCRAP_LEFT = "an unfinished copy or an already restored backup; delete it by hand"

STRANGER = ".pstack-t3-stranger-"

STRANGER_LEFT = "a restore found it where the entry it held should have been"

TWIN = "twin"

MOVED = "moved"

@dataclass(frozen=True)
class Published:
    """Successful placement result for `path`.

    `proved` makes one after checking the live entry against the held descriptor. `drop_backup` takes this result.
    """

    path: str

class PrivateName(str):
    """A path inside a holder directory. Its parent directory name starts with HOLDER."""

    def __new__(cls, value):
        parent = os.path.basename(os.path.dirname(value))
        if not parent.startswith(HOLDER):
            raise ValueError(f"{value} is not inside a holder directory")
        return str.__new__(cls, value)

def listing(directory):
    """The names in `directory`, sorted. A directory that is missing or cannot be read has none."""
    try:
        return sorted(os.listdir(directory))
    except OSError:
        return []

def holders(parent):
    for name in listing(parent):
        directory = os.path.join(parent, name)
        if name.startswith(HOLDER) and not os.path.islink(directory) and os.path.isdir(directory):
            yield name, directory

def made_directory(parent, prefix):
    """Make a new directory in `parent` and return os.path.join(parent, <its name>), with `parent` as given.

    tempfile.mkdtemp makes it under `parent` as given on Python 3.10 and 3.12, and from 3.12 on returns the
    os.path.abspath spelling. Only the last name of what it returns is used.
    """
    return os.path.join(parent, os.path.basename(tempfile.mkdtemp(prefix=prefix, dir=parent)))

class Stranded(OSError):
    """An OSError that left entries in a holder. Its text is the original error plus where they are kept."""

    def __init__(self, error, kept):
        super().__init__(error.errno, error.strerror)
        self.error = error
        self.kept = tuple(kept)

    def __str__(self):
        return f"{self.error}; it is kept at {', '.join(self.kept)}"

class Holder:
    """A private directory beside `home`. `aside` is the one name an entry takes inside it.

    An entry at `aside` in a HOLDER directory was renamed or hard linked from `home`. A SCRAP directory holds either a copy that `place_copy` is building or an entry that `retire` moved there from a HOLDER directory. That entry is a backup whose copy is already in place, or a stranger that `park` could not move.
    A STRANGER entry was parked there from a holder.
    This restore could not prove it. The sweep never restores or deletes it.
    As a context manager it removes the directory when it leaves empty, and never deletes an entry. An OSError that
    leaves while entries are inside becomes a `Stranded` that names them.
    """

    def __init__(self, home, prefix=HOLDER):
        parent, name = os.path.split(home)
        # A directory this call just created holds nothing yet, so a move into it cannot land on an existing entry.
        # mkdtemp ends the name with characters from [a-z0-9_], so no HOLDER name starts with SCRAP or STRANGER
        # and no SCRAP name starts with STRANGER.
        self.directory = made_directory(parent, prefix)
        self.home = home
        self.aside = PrivateName(os.path.join(self.directory, name))

    def __enter__(self):
        return self

    def __exit__(self, kind, error, trace):
        left = tuple(os.path.join(self.directory, name) for name in listing(self.directory))
        if not left:
            with suppress(OSError):
                os.rmdir(self.directory)
        elif isinstance(error, Stranded):
            error.kept += left
        elif isinstance(error, OSError):
            raise Stranded(error, left) from error
        return False

def remove_link(path, root, original, noun):
    """Move the link at `path` aside, then delete it only if it is this checkout's link for `original`. Return why it was kept."""
    with Holder(path) as holder:
        aside = holder.aside
        # Another installer can replace the link after the plan proved it. The rename takes whatever is there now.
        os.rename(path, aside)
        if layout.proves(root, aside, original):
            os.unlink(aside)
            return None
        reason = put_back(aside, path)
        if reason is not None:
            return f"the {noun} no longer matches the recorded checkout and {reason}; it is kept at {aside}"
        return f"the {noun} no longer matches the recorded checkout"

def same_entry(path, fd):
    """Whether `path` names the entry `fd` holds open. An inode in use keeps its number, so no other entry can show it."""
    return layout.identity(os.lstat(path))[:2] == layout.identity(os.fstat(fd))[:2]

def one_entry(first, second):
    """Whether both paths name one entry that is not a directory, so that removing one name deletes nothing."""
    try:
        one, other = os.lstat(first), os.lstat(second)
    except OSError:
        return False
    return os.path.samestat(one, other) and not stat.S_ISDIR(one.st_mode)

def park(entry):
    """Move the holder entry at `entry` into a new STRANGER directory beside the holder directory that holds it. Return its new path.

    It takes a PrivateName only. It never takes a live path. It moves by `place`, so it replaces nothing.
    It stays on one filesystem, so it never needs a copy. Return None when it stayed.
    """
    if not isinstance(entry, PrivateName):
        raise TypeError("park takes a PrivateName only")
    parent = os.path.dirname(entry)
    beside = os.path.join(os.path.dirname(parent), os.path.basename(entry))
    with Holder(beside, STRANGER) as stranger:
        return stranger.aside if place(entry, stranger.aside) == 0 else None

def link_held(fd, path):
    """Hard link the entry `fd` holds open to `path` only while `path` is empty. Return 0, or the errno that stopped it.

    It links through `/proc/self/fd`, and it answers `ENOSYS` where the platform is not Linux. It only links. It never renames the backup, so the backup keeps the name the caller gave it.
    """
    if not sys.platform.startswith("linux"):
        return errno.ENOSYS
    try:
        # The descriptor is the entry itself, whatever its old path names now.
        # `src_dir_fd` makes Python call linkat, which follows the /proc link to that entry. Plain link would link the /proc entry itself and fail as a crossing.
        # The source is an absolute path, so the descriptor passed there is never used as a directory.
        os.link(f"/proc/self/fd/{fd}", path, src_dir_fd=fd, follow_symlinks=True)
    except (OSError, NotImplementedError) as error:
        return getattr(error, "errno", None) or errno.ENOSYS
    return 0
