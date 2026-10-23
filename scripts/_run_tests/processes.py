from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import ACTIVE, ENTRY, POLL_SECONDS, PR_SET_CHILD_SUBREAPER, PR_SET_PDEATHSIG, SWEEP_SECONDS
from .events import Interrupted

try:
    import ctypes
    _prctl = ctypes.CDLL(None, use_errno=True).prctl
    _prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
except (ImportError, OSError, AttributeError):
    _prctl = None


_lifeline: tuple[int, int] | None = None


def lifeline() -> int | None:
    """Return the read end of the runner lifeline pipe. Return None where prctl is present."""
    if _prctl is not None:
        return None
    global _lifeline
    if _lifeline is None:
        _lifeline = os.pipe()
    return _lifeline[0]


def _watch(lifeline_fd: int, worker: int) -> None:
    """Block until the runner pipe reports EOF or fails, then send the worker SIGKILL."""
    try:
        while True:
            try:
                chunk = os.read(lifeline_fd, 65536)
            except OSError:
                break
            if not chunk:
                break
    finally:
        try:
            os.kill(worker, signal.SIGKILL)
        except ProcessLookupError:
            pass
        os._exit(0)


def follow_runner(runner: int, lifeline_fd: int | None) -> bool:
    """Return False when the parent is not the runner. Otherwise start the watcher when a lifeline is given and return True."""
    if os.getppid() != runner:
        return False
    if lifeline_fd is None:
        return True
    assert os.getpid() == os.getpgrp()
    worker = os.getpid()
    first = os.fork()
    if first != 0:
        _, status = os.waitpid(first, 0)
        if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
            raise RuntimeError("run_tests: the lifeline watcher failed to start")
        os.close(lifeline_fd)
        return True
    second = os.fork()
    if second != 0:
        os._exit(0)
    _watch(lifeline_fd, worker)


def die_with_parent() -> None:
    """Ask the kernel to send this process SIGKILL when the runner ends. Does nothing where prctl is absent."""
    if _prctl is not None:
        _prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)


def adopt_orphans() -> bool:
    """Try to make this process the child subreaper. Return whether Linux did."""
    try:
        import ctypes
        prctl = ctypes.CDLL(None, use_errno=True).prctl
        prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
        return prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    except (ImportError, OSError, AttributeError):
        return False


def children_by_parent() -> dict[int, list[int]]:
    """Map each process that has a child listed in /proc to those children. A process that ended and is not yet reaped is listed."""
    children: dict[int, list[int]] = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                stat = Path("/proc", entry, "stat").read_bytes()
                # The command name sits in parentheses and may itself hold spaces and parentheses.
                parent = int(stat[stat.rindex(b")") + 2:].split()[1])
            except (OSError, ValueError, IndexError):
                continue   # /proc gives no parent for it. It ended during the walk, or /proc hides it from this user.
            children.setdefault(parent, []).append(int(entry))
    return children


class Watch:
    """Notes SIGINT and SIGTERM, and holds the orphan handling described in scripts/run_tests.py."""

    def __init__(self) -> None:
        self.signals: list[int] = []
        signal.signal(signal.SIGINT, self.note)
        signal.signal(signal.SIGTERM, self.note)
        listed = sys.platform.startswith("linux") and Path("/proc/self/stat").exists()
        # The run started neither a child this process has now nor a process below such a child.
        self.inherited = listed and bool(children_by_parent().get(os.getpid()))
        # With no child now, a process that is below this one later was started by the run or by a process the run started.
        self.adopts = listed and not self.inherited and adopt_orphans()

    def note(self, signum, frame) -> None:
        self.signals.append(signum)

    def pause(self, owned: set[int]) -> None:
        """Sleep one poll. Raise Interrupted after a signal. Where adopt_orphans worked, reap each exited child up to the first one in owned, so nothing else in this process may wait for a child."""
        time.sleep(POLL_SECONDS)
        if self.signals:
            raise Interrupted
        while self.adopts:
            try:
                exited = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                return
            # The caller takes the status of a process in owned through its Popen, and the next pause goes on from there.
            if exited is None or exited.si_pid in owned:
                return
            os.waitpid(exited.si_pid, 0)

    def sweep(self, seconds: float = SWEEP_SECONDS) -> list[int]:
        """Do what scripts/run_tests.py describes after the runner's last child ends, for at most `seconds`. Return the pids still there."""
        if not self.adopts:
            return []
        deadline = time.monotonic() + seconds
        while True:
            children = children_by_parent()
            left, todo = [], list(children.get(os.getpid(), ()))
            while todo:
                left.append(todo.pop())
                todo.extend(children.get(left[-1], ()))
            if not left or time.monotonic() >= deadline:
                return left
            for pid in left:
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, os.WNOHANG)
                except (ProcessLookupError, PermissionError, ChildProcessError):
                    pass   # it is gone, it belongs to another user, or it is not a child of this process
            time.sleep(POLL_SECONDS / 5)


def child_env(environ: dict[str, str], home: Path, start: Path) -> dict[str, str]:
    # shutil.get_terminal_size reads COLUMNS and LINES, argparse wraps help text at its width, and tests compare help text.
    # A GIT_DIR or GIT_WORK_TREE from the caller names a repository that the git commands of a test then act on.
    env = {name: value for name, value in environ.items() if name not in ("COLUMNS", "LINES") and not name.startswith("GIT_")}
    env.update(TMPDIR=str(home / "tmp"), XDG_STATE_HOME=str(home / "state"), XDG_CONFIG_HOME=str(home / "config"))
    env[ACTIVE] = str(start)
    return env


def start_child(args: list[str], home: Path, start: Path, lifeline: int | None = None) -> subprocess.Popen:
    for name in ("tmp", "state", "config"):
        (home / name).mkdir(parents=True)
    # A file, not a pipe. A process that outlives its test cannot hold the runner's read open.
    # preexec_fn runs in the child after the fork, so the request names the child process itself.
    with open(home / "output", "wb") as output:
        return subprocess.Popen([sys.executable, str(ENTRY), *args], cwd=start.parent,
                                env=child_env(dict(os.environ), home, start), stdin=subprocess.DEVNULL,
                                stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                                preexec_fn=die_with_parent,
                                pass_fds=(lifeline,) if lifeline is not None else ())
