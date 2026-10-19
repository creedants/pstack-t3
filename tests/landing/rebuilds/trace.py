import contextlib
import os
import signal
import subprocess
import sys


class _GroupTrace:
    """Record killpg and reaping waitpid as event pairs with pids."""

    def __init__(self, on_killpg=None):
        """Keep the callback that runs after the record and before the real killpg."""
        self._on_killpg = on_killpg
        self.events = []
        self._real_killpg = None
        self._real_waitpid = None

    def __enter__(self):
        """Patch os.killpg and os.waitpid and return self."""
        self._real_killpg = os.killpg
        self._real_waitpid = os.waitpid
        os.killpg = self._recording_killpg
        os.waitpid = self._recording_waitpid
        return self

    def __exit__(self, *exc):
        """Restore os.killpg and os.waitpid. Never swallows."""
        os.killpg = self._real_killpg
        os.waitpid = self._real_waitpid
        return False

    def clear(self):
        """Drop recorded events. Patches stay in place."""
        del self.events[:]

    def leader(self):
        """Return the one group id killpg named."""
        groups = sorted({pgid for name, pgid in self.events if name == "killpg"})
        assert len(groups) == 1
        return groups[0]

    def _recording_killpg(self, pgid, sig):
        self.events.append(("killpg", pgid))
        if self._on_killpg is not None:
            self._on_killpg()
        return self._real_killpg(pgid, sig)

    def _recording_waitpid(self, pid, options):
        try:
            result = self._real_waitpid(pid, options)
        except ChildProcessError:
            if pid > 0:
                self.events.append(("reaped", pid))
            raise
        if result[0] != 0:
            self.events.append(("reaped", result[0]))
        return result


def _names(events, pid):
    """Return event names for this pid in order."""
    return [name for name, seen in events if seen == pid]


def _signalled_after_reap(events):
    """Return True when a killpg names a pid that an earlier event reaped."""
    reaped = set()
    for name, pid in events:
        if name == "reaped":
            reaped.add(pid)
        if name == "killpg" and pid in reaped:
            return True
    return False


def _zombie(pid):
    """Return True when pid is a zombie on Linux and macOS."""
    try:
        with open(f"/proc/{pid}/stat") as handle:
            stat = handle.read()
        return stat.rpartition(")")[2].split()[0] == "Z"
    except OSError:
        pass
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return False
    if result.returncode != 0:
        return False
    return (result.stdout or "").strip().startswith("Z")


_HOST = "host"


_DARWIN = "darwin without waitid"


def _platforms():
    """Return the host row plus the simulated Darwin row, except on a Darwin host without os.waitid, where it returns only the Darwin row."""
    if sys.platform == "darwin" and not hasattr(os, "waitid"):
        return [_DARWIN]
    return [_HOST, _DARWIN]


@contextlib.contextmanager
def _platform(name):
    """Run the body on the host, or with sys.platform set to darwin and os.waitid removed."""
    if name == _HOST:
        yield
        return
    saved_platform = sys.platform
    saved_waitid = getattr(os, "waitid", None)
    had_waitid = hasattr(os, "waitid")
    sys.platform = "darwin"
    if had_waitid:
        del os.waitid
    try:
        yield
    finally:
        sys.platform = saved_platform
        if had_waitid:
            os.waitid = saved_waitid
