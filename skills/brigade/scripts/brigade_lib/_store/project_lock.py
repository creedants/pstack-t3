import fcntl
import os
from contextlib import contextmanager


@contextmanager
def project_lock(parent):
    """Exclusive lock on a project's store directory, held across a directory claim or move.

    open and open --rename take it, so one target's creation and the other's move never interleave.
    ticket add or set with --after, ticket set with --state, and fire's final dependency checks and assignment
    take it too. These commands take it before a store lock, so their dependency validation and assignment
    are serialized with dependency and state changes made by those commands.
    The lock is on the directory itself, so a killed holder releases it and leaves no lock file.
    """
    fd = os.open(parent, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)
