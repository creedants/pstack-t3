import os
import signal
import subprocess
import sys
import time
from landing_lib import seam
from .admission import exclusive_slot
from .base import EXCLUSIVE_CAP_STATUS
from .holders import _parse_exclusive_cap, _report_cap

def _pid_reserved(pid):
    """True when pid is still in the process table, including a zombie. Does not reap."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True

def _finish_reaped(process):
    """The leader is already reaped, so its pid no longer proves the command's group. Do not signal."""
    if process.returncode is None:
        try:
            process.wait()
        except ChildProcessError:
            process.returncode = -1

def _ps_line(pid, columns):
    """Stdout from ps for these columns, or None when ps cannot answer. Does not reap."""
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            ["ps", "-o", columns, "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout or ""

def _leader_is_permitted_zombie(pid):
    """True when this exited unreaped leader is a signal target this process may signal.

    The caller has not reaped pid, so the kernel cannot reuse that pid and the credentials stay frozen.
    os.waitid with WNOHANG and WNOWAIT returns a result once the child has exited and None while it lives.
    An alive result stops here and does not run ps.
    Without os.waitid, ps must print a state that starts with Z.
    os.kill returns success for every zombie on Darwin and does not check the signal permission,
    so os.kill(pid, 0) cannot show that this process may signal the leader.
    Root may signal any leader. Otherwise the real or the effective uid of this process must match the
    leader's real or saved uid.
    A failed or unreadable probe is not a permitted target.
    """
    if hasattr(os, "waitid"):
        try:
            exited = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except OSError:
            return False
        if exited is None:
            return False
    else:
        state = _ps_line(pid, "stat=")
        if state is None or not state.strip().startswith("Z"):
            return False
    if os.geteuid() == 0:
        return True
    text = _ps_line(pid, "uid=,ruid=,svuid=")
    if text is None:
        return False
    fields = text.split()
    if len(fields) != 3:
        return False
    try:
        _effective, real, saved = (int(field) for field in fields)
    except ValueError:
        return False
    callers = (os.getuid(), os.geteuid())
    return real in callers or saved in callers

def _killpg_like_linux(process, sig):
    """Signal the command's group the way Linux killpg does.

    Linux killpg succeeds when some member of the group is a permitted signal target and raises
    EPERM when no member is. macOS raises EPERM when a zombie is the only permitted member.
    Treat that EPERM as a successful killpg only when this process observed its own exited unreaped
    leader as a permitted signal target before the attempt.
    Where os.waitid exists, an alive leader on Darwin costs one waitid and does not run ps.
    The ps query runs only after that leader has exited, or when os.waitid is absent.
    A live leader, an unknown leader state, a leader this process may not signal, and any
    PermissionError on another platform raise the original EPERM again.
    A leader that exits after a negative observation and before the attempt raises the original
    EPERM again.
    ProcessLookupError propagates.
    The EPERM is not proof that the group is empty.
    """
    if sys.platform != "darwin":
        os.killpg(process.pid, sig)
        return
    # Witness first. A permitted zombie found after the attempt cannot establish permission for it.
    # A leader that exits in the gap before killpg is a conservative miss, and the original EPERM is raised again.
    witness = _leader_is_permitted_zombie(process.pid)
    try:
        os.killpg(process.pid, sig)
    except PermissionError:
        if not witness:
            raise

def exclusive_command(command, repo, for_text, reason_text):
    """Hold every slot, run command, and stop its process group at the cap.

    command is an argv list. for_text and reason_text are the raw flag strings, or None.
    The deadline starts when every slot is locked, before the holder records are written.
    A command whose cap has already passed does not start.
    Returns the command's status, or EXCLUSIVE_CAP_STATUS.
    """
    seconds, reason = _parse_exclusive_cap(for_text, reason_text)

    def wait_with_real_sleep(process, timeout):
        current = time.sleep
        time.sleep = seam._real_sleep
        try:
            return process.wait(timeout=timeout)
        finally:
            time.sleep = current

    def stop_group(process):
        if process.returncode is not None:
            return
        # The unreaped leader is how SIGKILL stays on this command's group. Its pid is the process group id, and a zombie holds that pid until it is reaped, so the kernel cannot reuse the id for another process group.
        try:
            try:
                _killpg_like_linux(process, signal.SIGTERM)
            except ProcessLookupError:
                if process.returncode is None:
                    process.wait()
                return
            grace_ends = time.monotonic() + seam.EXCLUSIVE_CAP_GRACE
            if hasattr(os, "waitid"):
                while True:
                    try:
                        exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    except ChildProcessError:
                        _finish_reaped(process)
                        return
                    remaining = grace_ends - time.monotonic()
                    if exited is not None:
                        if remaining > 0:
                            seam._real_sleep(remaining)
                        break
                    if remaining <= 0:
                        break
                    seam._real_sleep(min(0.05, remaining))
            else:
                # Nothing here reaps, so a leader that exits stays a zombie through the grace.
                while True:
                    if not _pid_reserved(process.pid):
                        _finish_reaped(process)
                        return
                    remaining = grace_ends - time.monotonic()
                    if remaining <= 0:
                        break
                    seam._real_sleep(min(0.05, remaining))
        except BaseException:
            try:
                _killpg_like_linux(process, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if process.returncode is None:
                process.wait()
            raise
        try:
            _killpg_like_linux(process, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.returncode is None:
            process.wait()

    with exclusive_slot(repo=repo, command=" ".join(command), cap=seconds, reason=reason) as acquired:
        if seconds - (seam._cap_monotonic() - acquired) <= 0:
            _report_cap(seconds)
            return EXCLUSIVE_CAP_STATUS
        process = subprocess.Popen(command, start_new_session=True)
        try:
            seam._exclusive_ready_ack()
            if hasattr(process, "_sigint_wait_secs"):
                process._sigint_wait_secs = 0  # CPython waits 0.25s on SIGINT after setsid, and the child did not see that signal.
            remaining = seconds - (seam._cap_monotonic() - acquired)
            try:
                wait_with_real_sleep(process, max(0.0, remaining))
            except subprocess.TimeoutExpired:
                stop_group(process)
                _report_cap(seconds)
                return EXCLUSIVE_CAP_STATUS
            return process.returncode
        finally:
            if process.returncode is None:
                stop_group(process)
