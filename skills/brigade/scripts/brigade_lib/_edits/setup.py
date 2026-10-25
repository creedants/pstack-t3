import os
import signal
import subprocess
import sys
from brigade_lib.shared import BrigadeError

SETUP_TIMEOUT = 1800


def prepare(scratch, setup):
    """Run the contract's setup command in the scratch copy, and return "" or the one failure line.

    An empty command returns "" and starts no process. The command runs through the platform shell in its
    own process group, with stdin closed and its output captured, because the landing queue runs the same
    contract string through a shell and a setup such as `cd web && npm ci` must work here too.
    A non-zero exit returns `FAIL setup: <command> exited <code>`. A run that reaches SETUP_TIMEOUT kills
    the process group and returns `FAIL setup: <command> timed out`. The command's own output goes to
    stderr, the way exit_code sends a failed build's output there. A shell that cannot start raises
    BrigadeError, as exit_code does.
    """
    if not setup.strip():
        return ""
    try:
        process = subprocess.Popen(setup, shell=True, cwd=scratch, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as error:
        raise BrigadeError(f"cannot run {setup}: {error.strerror}") from None
    try:
        output, _ = process.communicate(timeout=SETUP_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except AttributeError:
            process.kill()
        except OSError:
            pass
        output, _ = process.communicate()
        sys.stderr.buffer.write(output or b"")
        sys.stderr.flush()
        return f"FAIL setup: {setup} timed out"
    if process.returncode:
        sys.stderr.buffer.write(output or b"")
        sys.stderr.flush()
        return f"FAIL setup: {setup} exited {process.returncode}"
    return ""
