import os
import selectors
import signal
import subprocess
import threading
import time
from .base import _ARMED, _BUILD_DRAIN_GRACE, _GROUP_KILLED, _REAPED, _RESTORED, _STARTED
from .exclusive import _killpg_like_linux

class _Build:
    """One shell command in its own process group, owned by exactly one runner.

    Only the runner sends SIGKILL to the group and only the runner reaps the leader. It sends SIGKILL only
    before it reaps. The SIGINT handler records the interrupt and wakes the runner. It never
    signals, reaps, raises, or calls the entry disposition. The entry disposition receives the
    postponed SIGINT from deliver, after close restores the disposition.
    No code outside this class waits on the Popen. Nothing calls its wait, poll, or communicate.
    """

    def __init__(self, previous):
        """Previous is what signal.getsignal(SIGINT) returned on entry. Opens no file descriptors."""
        self._previous = previous
        self._intercept = previous is signal.SIG_DFL or callable(previous)
        self.phase = None
        self._pending = None
        self._arrivals = 0
        self._popen = None
        self._wake_r = None
        self._wake_w = None
        self._selector = None
        self._stdout = None
        self._chunks = []
        self.returncode = None

    def start(self, command, cwd):
        """Open the wake pipe, install the interceptable handler, then spawn. An installed handler is in place before the fork."""
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        os.set_blocking(self._wake_w, False)
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._wake_r, selectors.EVENT_READ)
        self.phase = _ARMED
        if self._intercept:
            signal.signal(signal.SIGINT, self._on_sigint)
        self._popen = subprocess.Popen(command, cwd=cwd, shell=True, start_new_session=True,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.phase = _STARTED
        self._stdout = self._popen.stdout
        self._selector.register(self._stdout.fileno(), selectors.EVENT_READ)

    def run(self, timeout):
        """Drive STARTED to REAPED. Returns a pair. Timeout returns None as the status."""
        deadline = time.monotonic() + timeout
        ended = self._drain(deadline)
        if ended == "eof":
            ended = self._await_exit(deadline)
            if ended == "reaped":
                return self.returncode, self._text()
        self._kill_group()
        if self._drain(time.monotonic() + _BUILD_DRAIN_GRACE, self._arrivals) != "eof":
            self._close_stdout()
            self._chunks = []
        self._reap_killed()
        text = self._text()
        return (None, text) if ended == "deadline" else (self.returncode, text)

    def close(self):
        """Converge to RESTORED from any phase. Idempotent. Runs in the finally."""
        try:
            if self.phase == _STARTED:
                self._kill_group()
            if self.phase == _GROUP_KILLED:
                self._close_stdout()
                self._reap_killed()
        finally:
            self._restore()
            self._close_stdout()
            if self._wake_r is not None:
                try:
                    os.close(self._wake_r)
                except OSError:
                    pass
                self._wake_r = None
            if self._wake_w is not None:
                try:
                    os.close(self._wake_w)
                except OSError:
                    pass
                self._wake_w = None
            if self._selector is not None:
                try:
                    self._selector.close()
                except (OSError, ValueError):
                    pass
                self._selector = None

    def deliver(self):
        """Hand a postponed SIGINT to the entry disposition. Runs after close."""
        if self._pending is None:
            return
        signum, frame = self._pending
        self._pending = None
        if self._previous is signal.SIG_IGN:
            return
        if self._previous is signal.SIG_DFL:
            os.kill(os.getpid(), signal.SIGINT)
            raise KeyboardInterrupt
        if callable(self._previous):
            self._previous(signum, frame)
            return
        raise KeyboardInterrupt

    def _on_sigint(self, signum, frame):
        """Record the first SIGINT and wake the runner. Never signals, reaps, or raises."""
        self._arrivals += 1
        if self._pending is None:
            self._pending = (signum, frame)
        try:
            os.write(self._wake_w, b"\0")
        except OSError:
            pass

    def _drain(self, until, grace_mark=None):
        """Read stdout until EOF or the time limit. Return interrupt only in STARTED. With a grace mark, return cut when arrivals pass the mark and reach two. Never reaps."""
        while self._stdout is not None:
            if self._pending is not None and self.phase == _STARTED:
                return "interrupt"
            # A grace arrival that is the first arrival of the run does not cut.
            if grace_mark is not None and self._arrivals > grace_mark and self._arrivals >= 2:
                return "cut"
            remaining = until - time.monotonic()
            if remaining <= 0:
                return "deadline"
            for key, _ in self._selector.select(remaining):
                if key.fd == self._wake_r:
                    try:
                        os.read(self._wake_r, 512)
                    except OSError:
                        pass
                else:
                    try:
                        chunk = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        self._close_stdout()
                        return "eof"
                    self._chunks.append(chunk)
        return "eof"

    def _await_exit(self, deadline):
        """Poll the leader with WNOHANG reaps after EOF. This runner is the only signaller."""
        delay = 0.0
        while True:
            if self._try_reap():
                return "reaped"
            if self._pending is not None:
                return "interrupt"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "deadline"
            self._selector.select(min(delay, remaining))
            if self._wake_r is not None:
                try:
                    os.read(self._wake_r, 512)
                except OSError:
                    pass
            delay = min(max(delay * 2, 0.001), 0.05)

    def _kill_group(self):
        """Move STARTED to GROUP_KILLED and attempt SIGKILL once. A call after reap is a bug."""
        if self.phase not in (_STARTED, _GROUP_KILLED):
            raise RuntimeError("signal after reap")
        if self.phase == _GROUP_KILLED:
            return
        self.phase = _GROUP_KILLED
        try:
            _killpg_like_linux(self._popen, signal.SIGKILL)
        except OSError:
            pass

    def _try_reap(self):
        """Run one waitpid with WNOHANG. True when the leader is gone. Records our reap or a foreign reap."""
        try:
            pid, status = os.waitpid(self._popen.pid, os.WNOHANG)
        except ChildProcessError:
            self._reaped(None)
            return True
        if pid == 0:
            return False
        self._reaped(status)
        return True

    def _reap_killed(self):
        """Run one blocking waitpid after the SIGKILL attempt in GROUP_KILLED.

        This wait has no deadline. A pending SIGINT stays postponed until the leader exits.
        """
        try:
            _, status = os.waitpid(self._popen.pid, 0)
        except ChildProcessError:
            status = None
        self._reaped(status)

    def _reaped(self, status):
        """Record the single reap. Writes returncode so Popen never queues the pid."""
        self.returncode = -1 if status is None else os.waitstatus_to_exitcode(status)
        self._popen.returncode = self.returncode
        self.phase = _REAPED

    def _restore(self):
        """Restore the interceptable entry disposition. Blocks SIGINT for the swap where pthread_sigmask exists."""
        blocked = hasattr(signal, "pthread_sigmask")
        old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT}) if blocked else None
        try:
            if self._intercept and self.phase is not None:
                signal.signal(signal.SIGINT, self._previous)
            self.phase = _RESTORED
        finally:
            if blocked:
                signal.pthread_sigmask(signal.SIG_SETMASK, old)

    def _close_stdout(self):
        """Close the build pipe and forget it. Ignores close errors."""
        if self._stdout is not None:
            if self._selector is not None:
                try:
                    self._selector.unregister(self._stdout.fileno())
                except (KeyError, OSError, ValueError):
                    pass
            try:
                self._stdout.close()
            except (OSError, ValueError):
                pass
            self._stdout = None

    def _text(self):
        """Decode the bytes read so far with replacement. Keeps CRLF."""
        return b"".join(self._chunks).decode("utf-8", "replace")

def run_build(command, cwd, timeout):
    """Run a shell command in its own process group. Returns a pair. Timeout returns None as the status.

    One runner sends SIGKILL to the group and reaps the leader, and it sends SIGKILL only before it reaps.
    An interceptable SIGINT before the reap is postponed. The runner never sends SIGKILL after it reaps.
    It restores the entry disposition, and then delivers the SIGINT to it. SIG_IGN leaves the
    build running.
    """
    if threading.current_thread() is not threading.main_thread():
        raise ValueError("signal only works in main thread of the main interpreter")
    if signal.getsignal(signal.SIGCHLD) is signal.SIG_IGN:
        raise ValueError("run_build refuses to start while SIGCHLD is ignored")
    build = _Build(signal.getsignal(signal.SIGINT))
    try:
        build.start(command, cwd)
        result = build.run(timeout)
    finally:
        try:
            build.close()
        finally:
            build.deliver()
    return result
