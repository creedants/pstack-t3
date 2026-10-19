import time
from pathlib import Path


STOPPED_AFTER_TERM_MAIN = (
    "import os, signal, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "cap, grace, cap_max = sys.argv[2:5]\n"
    "if cap:\n"
    "    land.EXCLUSIVE_CAP = float(cap)\n"
    "if grace:\n"
    "    land.EXCLUSIVE_CAP_GRACE = float(grace)\n"
    "if cap_max:\n"
    "    land.EXCLUSIVE_CAP_MAX = float(cap_max)\n"
    "_original = land.exclusive_command.__globals__['_killpg_like_linux']\n"
    "_state = {'stopped': False}\n"
    "def _stop_after_term(process, sig):\n"
    "    result = _original(process, sig)\n"
    "    if sig == signal.SIGTERM and not _state['stopped']:\n"
    "        _state['stopped'] = True\n"
    "        os.kill(os.getpid(), signal.SIGSTOP)\n"
    "    return result\n"
    "land.exclusive_command.__globals__['_killpg_like_linux'] = _stop_after_term\n"
    "sys.exit(land.main(sys.argv[5:]))\n"
)


class SlotBase:

    def wait_for_pid(self, path, timeout=10):
        """Return the pid in path once the whole file text parses as one int.

        Path.write_text opens and truncates the file before it writes, so a reader that
        trusts the file's existence can see an empty file. An empty, missing, or
        non-integer file is not ready.
        """
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                text = path.read_text()
            except OSError:
                text = None
            if text is not None:
                last = text
                try:
                    return int(text)
                except ValueError:
                    pass
            time.sleep(0.02)
        self.fail(f"{path} held no whole pid in {timeout}s: {last!r}")
