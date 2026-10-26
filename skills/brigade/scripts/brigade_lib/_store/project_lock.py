import fcntl
import os
from contextlib import contextmanager


@contextmanager
def project_lock(parent):
    """Exclusive lock on a project's store directory, held across a directory claim or move.

    open and open --rename take it, so one target's creation and the other's move never interleave.
    ticket add or set with --after or --state, and the dependency checks and write of fire, take it too, so a
    sibling's dependency cannot change between the check and the write. It is taken before any store lock.
    The lock is on the directory itself, so a killed holder releases it and leaves no lock file.
    """
    fd = os.open(parent, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)
