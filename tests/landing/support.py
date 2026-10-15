import ast
import contextlib
import enum
import errno
import fcntl
import gc
import hashlib
import inspect
import io
import json
import locale
import os
import re
import runpy
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
import weakref
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "t3/added/landing/scripts/land.py"
sys.path.insert(0, str(SCRIPT.parent))

import land  # noqa: E402

REVIEWER = "codex/gpt-6.1-sol"
ADMIN = ("--owner", ".admin/@1")


class ProcessReason(enum.Enum):
    """Why this call is a separate interpreter."""

    GOVERNOR = "slot governor used across processes"
    KILLED_CALLER = "killing a caller"
    LOCK = "process-level lock held across calls"
    HELP = "-h output"
    INHERITED_ENV = "inherited environment"
    RACE = "race between processes"


def _pipe_encoding():
    getencoding = getattr(locale, "getencoding", None)
    if getencoding is None:
        return locale.getpreferredencoding(False)
    return getencoding()


def _text_from_pipe(data):
    """Decode pipe bytes with the encoding locale.getencoding returns when that function exists.

    Otherwise use the encoding locale.getpreferredencoding(False) returns. Errors are strict. CR LF and CR become LF.
    """
    return data.decode(_pipe_encoding(), "strict").replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class _Saved:
    cwd: str
    environ: dict
    argv: list
    argv_contents: list
    stdin: object
    stdout: object
    stderr: object
    umask: int
    signals: tuple
    dont_write_bytecode: bool
    fds: dict


def _open_fd_directory():
    """Open the descriptor directory and return it with the descriptor that reads it.

    The directory is /proc/self/fd when this host provides it, and /dev/fd otherwise.
    """
    try:
        return "/proc/self/fd", os.open("/proc/self/fd", os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        return "/dev/fd", os.open("/dev/fd", os.O_RDONLY | os.O_DIRECTORY)


def _fd_numbers():
    """Return the descriptor directory and every descriptor number it lists except the probe.

    The probe is the descriptor that reads the directory, and its number is left out.
    """
    directory, probe = _open_fd_directory()
    try:
        names = os.listdir(probe)
    finally:
        os.close(probe)
    numbers = set()
    probe_name = str(probe)
    for name in names:
        if name == probe_name or not name.isdigit():
            continue
        numbers.add(int(name))
    return directory, numbers


def _fd_paths():
    """Return the descriptor directory and the path of every open descriptor it can read except the probe.

    The probe is the descriptor that reads the directory, and its number is left out. An entry under
    /proc/self/fd is a symlink. An entry under /dev/fd is not, and F_GETPATH names it.
    """
    directory, numbers = _fd_numbers()
    found = {}
    for number in numbers:
        try:
            if directory == "/proc/self/fd":
                found[number] = os.readlink(f"/proc/self/fd/{number}")
            else:
                found[number] = fcntl.fcntl(number, fcntl.F_GETPATH, bytes(1024)).rstrip(b"\0").decode()
        except OSError:
            continue
    return directory, found


def _descriptors():
    """Return the device and inode of every listed descriptor at or above 3 that it can fstat.

    No pathname is read, so a descriptor whose pathname query would fail is still listed.
    """
    _, numbers = _fd_numbers()
    found = {}
    for number in numbers:
        if number < 3:
            continue
        try:
            info = os.fstat(number)
        except OSError:
            continue
        found[number] = (info.st_dev, info.st_ino)
    return found


def _save():
    umask = os.umask(0)
    os.umask(umask)
    handlers = []
    for signum in range(1, signal.NSIG):
        try:
            handlers.append((signum, signal.getsignal(signum)))
        except (OSError, ValueError):
            continue
    return _Saved(
        cwd=os.getcwd(),
        environ=dict(os.environ),
        argv=sys.argv,
        argv_contents=list(sys.argv),
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        umask=umask,
        signals=tuple(handlers),
        dont_write_bytecode=sys.dont_write_bytecode,
        fds=_descriptors(),
    )


def _close_live_owners(numbers):
    stale = set(numbers)
    if not stale:
        return
    owners = []
    for obj in gc.get_objects():
        # A dead weakref proxy raises ReferenceError from the lookup. An unrelated object can raise a
        # BaseException such as SystemExit. Both are skipped. KeyboardInterrupt is raised on.
        try:
            fileno = getattr(obj, "fileno", None)
            if not callable(fileno):
                continue
            number = fileno()
        except KeyboardInterrupt:
            raise
        except BaseException:
            continue
        if type(number) is not int or number not in stale:
            continue
        owners.append(obj)

    def depth(obj):
        seen = set()
        current = obj
        steps = 0
        while id(current) not in seen:
            seen.add(id(current))
            try:
                nxt = getattr(current, "buffer", None)
                if nxt is None:
                    nxt = getattr(current, "raw", None)
            except KeyboardInterrupt:
                raise
            except BaseException:
                break
            if nxt is None:
                break
            current = nxt
            steps += 1
        return steps

    # Deepest first, so the wrapper flushes its buffer before the raw file closes.
    owners.sort(key=depth, reverse=True)
    for obj in owners:
        try:
            obj.close()
        except (OSError, ValueError):
            pass


def _close_new_fds(before):
    first = _descriptors()
    second = _descriptors()
    stale = []
    for number in first.keys() & second.keys():
        if first[number] != second[number]:
            continue
        if before.get(number) == first[number]:
            continue
        stale.append(number)
    _close_live_owners(stale)
    for number in stale:
        try:
            os.close(number)
        except OSError:
            continue


def _restore(saved):
    """Each step runs even when an earlier step raises. Then the first error is raised."""
    first = None

    def step(action):
        nonlocal first
        try:
            action()
        except Exception as error:
            if first is None:
                first = error

    step(lambda: os.chdir(saved.cwd))

    def environ():
        os.environ.clear()
        os.environ.update(saved.environ)

    step(environ)

    def argv():
        if sys.argv is not saved.argv:
            sys.argv = saved.argv
        saved.argv[:] = saved.argv_contents

    step(argv)

    def stdio():
        sys.stdin = saved.stdin
        sys.stdout = saved.stdout
        sys.stderr = saved.stderr

    step(stdio)
    step(lambda: os.umask(saved.umask))

    def signals():
        for signum, handler in saved.signals:
            try:
                current = signal.getsignal(signum)
            except (OSError, ValueError):
                continue
            if current == handler:
                continue
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                continue

    step(signals)
    step(lambda: setattr(sys, "dont_write_bytecode", saved.dont_write_bytecode))
    # Collect unreachable connections so their descriptors close before os.close.
    step(gc.collect)
    step(lambda: _close_new_fds(saved.fds))
    if first is not None:
        raise first


def _child_codecs(env):
    """Return ((stdout encoding, stdout errors), (stderr encoding, stderr errors)).

    env None means os.environ. Read PYTHONIOENCODING from that mapping.
    Absent, empty, and ':' keep sys.stdout.encoding and sys.stdout.errors.
    'name' and 'name:' use that encoding with strict. ':handler' keeps the
    process encoding and uses the handler. 'name:handler' uses both.
    Stderr uses the same encoding and backslashreplace.
    """
    mapping = os.environ if env is None else env
    encoding = sys.stdout.encoding
    errors = sys.stdout.errors
    raw = mapping.get("PYTHONIOENCODING")
    if raw:
        name, _, handler = raw.partition(":")
        if name:
            encoding = name
        if handler:
            errors = handler
        elif name:
            errors = "strict"
    return (encoding, errors), (encoding, "backslashreplace")


def _capture_stream(line_buffering, encoding, errors):
    raw = io.BytesIO()
    wrapper = io.TextIOWrapper(
        raw, encoding=encoding, errors=errors, newline=None, line_buffering=line_buffering)
    return raw, wrapper


def _status(action):
    try:
        result = action()
    except SystemExit as exit_error:
        status = exit_error.code
        if status is None:
            return 0
        if isinstance(status, int):
            return status & 0xFF
        print(status, file=sys.stderr)
        return 1
    except Exception as error:
        traceback.print_exception(error)
        return 1
    if result is None:
        return 0
    if isinstance(result, int):
        return result & 0xFF
    print(result, file=sys.stderr)
    return 1


def _load(script):
    namespace = runpy.run_path(str(script))
    sys.modules.pop("<run_path>", None)
    return namespace["main"]


def _run(command, *, env, cwd=None, timeout=None, reason=None):
    if reason is not None and not isinstance(reason, ProcessReason):
        raise TypeError(type(reason).__name__)
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        cwd=None if cwd is None else os.fspath(cwd),
        env=None if env is None else dict(env),
        timeout=timeout,
    )


def _popen(reason, command, *, env, cwd=None, stdout=subprocess.PIPE,
           stderr=subprocess.PIPE, text=True, start_new_session=False):
    if not isinstance(reason, ProcessReason):
        raise TypeError(type(reason).__name__)
    return subprocess.Popen(
        list(command),
        stdout=stdout,
        stderr=stderr,
        cwd=None if cwd is None else os.fspath(cwd),
        env=None if env is None else dict(env),
        text=text,
        start_new_session=start_new_session,
    )


def drive(script, argv, *, env, cwd=None):
    """Run one land.py command and return its exit code, stdout, and stderr.

    PSTACK_TEST_REAL_PROCESS is read from the process environment on entry, before env is installed and before the script loads. The value 1 starts a real process. Any other value runs in this process.
    After an in-process call, including when main raises or KeyboardInterrupt leaves the call, cwd, os.environ, sys.argv, the standard streams, the umask, signal handlers, and sys.dont_write_bytecode are restored.
    An in-process call closes unreachable file objects before the script loads. When the call ends, it closes each connection opened through sqlite3.connect during the call, then os.close runs for each descriptor at or above 3 whose two later snapshots report one inode that the entry snapshot does not report for that descriptor.
    Stdout and stderr use the codecs _child_codecs reads from env before that env is installed.
    Each in-process call loads the script again. The imported land module is not the module that runs.
    """
    arguments = [str(arg) for arg in argv]
    if os.environ.get("PSTACK_TEST_REAL_PROCESS") == "1":
        result = _run([sys.executable, str(script), *arguments], env=env, cwd=cwd)
        return result.returncode, result.stdout, result.stderr
    saved = _save()
    stdout_codec, stderr_codec = _child_codecs(env)
    connections = []
    previous_connect = sqlite3.connect

    def connect(*args, **kwargs):
        connection = previous_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    try:
        # Collect cyclic garbage before this run opens land.db.
        gc.collect()
        out_raw, out = _capture_stream(False, *stdout_codec)
        err_raw, err = _capture_stream(True, *stderr_codec)
        sys.stdout = out
        sys.stderr = err
        if cwd is not None:
            os.chdir(cwd)
        if env is not None:
            os.environ.clear()
            os.environ.update(dict(env))
        sys.argv = [str(script), *arguments]
        sqlite3.connect = connect
        code = _status(lambda: _load(script)(list(arguments)))
        out.flush()
        err.flush()
        return code, _text_from_pipe(out_raw.getvalue()), _text_from_pipe(err_raw.getvalue())
    finally:
        sqlite3.connect = previous_connect
        try:
            for connection in connections:
                try:
                    connection.close()
                except sqlite3.ProgrammingError:
                    pass
        finally:
            _restore(saved)


def real_process(reason, script, argv, *, env, cwd=None, timeout=None):
    """Start a real interpreter and return its text CompletedProcess."""
    if not isinstance(reason, ProcessReason):
        raise TypeError(type(reason).__name__)
    command = [sys.executable, str(script), *(str(arg) for arg in argv)]
    return _run(command, env=env, cwd=cwd, timeout=timeout, reason=reason)


def real_popen(reason, command, *, env, cwd=None, stdout=subprocess.PIPE,
               stderr=subprocess.PIPE, text=True, start_new_session=False):
    """Return a Popen for command."""
    return _popen(
        reason, command, env=env, cwd=cwd, stdout=stdout, stderr=stderr,
        text=text, start_new_session=start_new_session)


def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
    saved = dict(os.environ)

    def restore():
        os.environ.clear()
        os.environ.update(saved)

    unittest.addModuleCleanup(restore)
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull


def setUpModule():
    fence_git_environment()


def stamp_in(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def git_env():
    """Identity for fixture commits. LC_ALL=C keeps git diagnostics in English."""
    return {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            "LC_ALL": "C"}


def sh(*args, cwd):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=git_env())
    if result.returncode != 0:
        raise AssertionError(f"{args} failed: {result.stderr}")
    return result.stdout.strip()


def paused_child(case, args):
    """Run land.py with its first transaction held until <case>/proceed exists, as a command started before a recovery."""
    case = Path(case)
    original = land.Store.tx

    def paused_tx(self):
        (case / "paused").touch()
        deadline = time.monotonic() + 60
        while not (case / "proceed").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        return original(self)

    land.Store.tx = paused_tx
    sys.exit(land.main(args))


SLOT_HOLD = (
    "import os, sys, time\n"
    "from pathlib import Path\n"
    "log, name, fifo, *hold = sys.argv[1:]\n"
    "def mark(word):\n"
    "    files = os.environ.get('LAND_SLOT_FILES', '')\n"
    "    line = f'{word} {name}' + (f' {files}' if files else '')\n"
    "    out = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)\n"
    "    os.write(out, (line + '\\n').encode())\n"
    "    os.close(out)\n"
    "release = os.open(fifo, os.O_RDONLY)\n"
    "mark('enter')\n"
    "Path('started').touch()\n"
    "if hold:\n"
    "    time.sleep(float(hold[0]))\n"
    "else:\n"
    "    assert os.read(release, 1) == b'x'\n"
    "mark('exit')\n"
    "Path('finished').touch()\n"
)
# A stepped caller waits for the test between attempts. A marked caller sleeps for real. Both mark each failed attempt,
# and touch blocking before a lock call that has to wait.
SLOT_WRAPPED = (
    "import fcntl, os, subprocess, sys, time\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "flock = fcntl.flock\n"
    "def announced(handle, operation):\n"
    "    if operation == fcntl.LOCK_EX:\n"
    "        try:\n"
    "            return flock(handle, operation | fcntl.LOCK_NB)\n"
    "        except BlockingIOError:\n"
    "            Path('blocking').touch()\n"
    "    return flock(handle, operation)\n"
    "land.fcntl.flock = announced\n"
    "count = 0\n"
    "sleep = time.sleep\n"
    "def waiting(seconds):\n"
    "    global count\n"
    "    count += 1\n"
    "    Path(f'waiting-{count}').touch()\n"
    "    if sys.argv[2] == 'marked':\n"
    "        sleep(seconds)\n"
    "        return\n"
    "    gate = os.open(sys.argv[2], os.O_RDONLY)\n"
    "    assert os.read(gate, 1) == b'x'\n"
    "    os.close(gate)\n"
    "if sys.argv[2]:\n"
    "    land.time.sleep = waiting\n"
    "if sys.argv[3] == 'landing':\n"
    "    with land.slot('landing'):\n"
    "        sys.exit(subprocess.run(sys.argv[4:]).returncode)\n"
    "sys.exit(land.main(['slot', *sys.argv[4:]]))\n"
)

# A holder that keeps its slot until the release fifo is written, so a test can read a waiter's report.
HOLD_SCRIPT = (
    "import os, sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "kind, repo, command, ready, release = sys.argv[2:7]\n"
    "slot = land.exclusive_slot if kind == 'exclusive' else (lambda **kw: land.slot(kind, **kw))\n"
    "with slot(repo=repo, command=command):\n"
    "    Path(ready).touch()\n"
    "    os.read(os.open(release, os.O_RDONLY), 1)\n"
)

# A child that runs land.main. Empty cap, grace, or maximum strings leave those constants alone.
EXCLUSIVE_MAIN = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "cap, grace, cap_max = sys.argv[2:5]\n"
    "if cap:\n"
    "    land.EXCLUSIVE_CAP = float(cap)\n"
    "if grace:\n"
    "    land.EXCLUSIVE_CAP_GRACE = float(grace)\n"
    "if cap_max:\n"
    "    land.EXCLUSIVE_CAP_MAX = float(cap_max)\n"
    "sys.exit(land.main(sys.argv[5:]))\n"
)

# EXCLUSIVE_MAIN after os.waitid is removed, so the grace uses the fallback.
EXCLUSIVE_MAIN_WITHOUT_WAITID = "import os\nvars(os).pop('waitid', None)\n" + EXCLUSIVE_MAIN

# EXCLUSIVE_MAIN with the cap clock driven by a file. The test writes the file to advance time.
CAP_CLOCK_MAIN = (
    "import os, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "from pathlib import Path\n"
    "cap, grace, cap_max = sys.argv[2:5]\n"
    "if cap:\n"
    "    land.EXCLUSIVE_CAP = float(cap)\n"
    "if grace:\n"
    "    land.EXCLUSIVE_CAP_GRACE = float(grace)\n"
    "if cap_max:\n"
    "    land.EXCLUSIVE_CAP_MAX = float(cap_max)\n"
    "clock = Path(os.environ['LAND_CAP_CLOCK'])\n"
    "def file_clock():\n"
    "    try:\n"
    "        return float(clock.read_text().strip())\n"
    "    except (OSError, ValueError):\n"
    "        return 1000.0\n"
    "land._cap_monotonic = file_clock\n"
    "sys.exit(land.main(sys.argv[5:]))\n"
)

# EXCLUSIVE_MAIN with the ready acknowledgement swapped for a touch of the path in LAND_EXCLUSIVE_READY.
# Production leaves the hook a no-op, so the signal test overrides it here to observe the hook just before the protected wait.
READY_MAIN = (
    "import os, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "from pathlib import Path\n"
    "cap, grace, cap_max = sys.argv[2:5]\n"
    "if cap:\n"
    "    land.EXCLUSIVE_CAP = float(cap)\n"
    "if grace:\n"
    "    land.EXCLUSIVE_CAP_GRACE = float(grace)\n"
    "if cap_max:\n"
    "    land.EXCLUSIVE_CAP_MAX = float(cap_max)\n"
    "ready = Path(os.environ['LAND_EXCLUSIVE_READY'])\n"
    "def ack():\n"
    "    try:\n"
    "        ready.touch()\n"
    "    except OSError:\n"
    "        pass\n"
    "land._exclusive_ready_ack = ack\n"
    "sys.exit(land.main(sys.argv[5:]))\n"
)

# A witness probe that cannot answer. os.waitid is gone, so land.py falls back to ps, and every ps
# call raises.
FAILED_WITNESS_PROBE = (
    "import os, subprocess\n"
    "vars(os).pop('waitid', None)\n"
    "_real_run = subprocess.run\n"
    "def _ps(*args, **kwargs):\n"
    "    command = args[0] if args else kwargs.get('args')\n"
    "    if isinstance(command, (list, tuple)) and command[:1] == ['ps']:\n"
    "        raise OSError('ps is unavailable')\n"
    "    return _real_run(*args, **kwargs)\n"
    "subprocess.run = _ps\n"
)

# A witness probe that reports a zombie leader this process may not signal. The host's waitid or ps
# answers the exit question, and a ps uid query prints a uid line that matches no uid of this process.
# land.py sees an effective uid that is not root, so root does not short circuit the check.
FOREIGN_UID_WITNESS_PROBE = (
    "import os, subprocess\n"
    "land.os.geteuid = lambda: 1000\n"
    "_real_run = subprocess.run\n"
    "_uid = next(uid for uid in (1, 2, 3) if uid not in (os.getuid(), 1000))\n"
    "_line = '%d %d %d\\n' % (_uid, _uid, _uid)\n"
    "def _ps(*args, **kwargs):\n"
    "    command = args[0] if args else kwargs.get('args')\n"
    "    if isinstance(command, (list, tuple)) and any('uid=' in str(part) for part in command):\n"
    "        return subprocess.CompletedProcess(list(command), 0, stdout=_line)\n"
    "    return _real_run(*args, **kwargs)\n"
    "subprocess.run = _ps\n"
)

# A stub for os.killpg. "after-term" delivers the first signal and raises EPERM for every later one.
# It does not deliver those later signals, even if the group still has a live permitted member.
# "always" raises EPERM for every signal, and kills the leader itself first when kills is set.
# These seams exercise the witness check. Native macOS killpg can still signal live permitted members
# when the leader is a zombie.
EPERM_KILLPG_STUB = (
    "import errno, os, signal\n"
    "_real_killpg = os.killpg\n"
    "_sent = []\n"
    "_leader = []\n"
    "def _killpg(pid, sig):\n"
    "    _leader.append(pid)\n"
    "    if {kills!r}:\n"
    "        os.kill(pid, signal.SIGKILL)\n"
    "    if {mode!r} == 'always' or ({mode!r} == 'after-term' and _sent):\n"
    "        raise PermissionError(errno.EPERM, 'Operation not permitted')\n"
    "    _sent.append(sig)\n"
    "    _real_killpg(pid, sig)\n"
    "os.killpg = _killpg\n"
)

# A _real_sleep that raises KeyboardInterrupt once the leader is an unreaped exited child this process
# may signal. The probe is land._leader_is_permitted_zombie, the witness the killpg wrapper uses, and
# it never reaps, so the interrupt lands after the SIGTERM has killed the leader.
INTERRUPT_AFTER_THE_LEADER_EXITS = (
    "import os, time\n"
    "_real_time_sleep = time.sleep\n"
    "def _interrupt(seconds):\n"
    "    if _leader and land._leader_is_permitted_zombie(_leader[0]):\n"
    "        raise KeyboardInterrupt()\n"
    "    _real_time_sleep(seconds)\n"
    "land._real_sleep = _interrupt\n"
)


def _darwin_eperm_main(mode="after-term", kills=False, probe="waitid", darwin=True, after=""):
    """Return EXCLUSIVE_MAIN with os.killpg replaced by a stub, forcing the Darwin platform when darwin is true.

    mode picks the signals that raise EPERM, and kills kills the leader itself first. probe picks the witness probe, and is one of
    waitid, failed, or foreign. darwin False leaves the platform alone, so the Linux branch runs on a Linux host.
    after holds extra lines the wrapper runs after the killpg stub, and may read that stub's state.
    """
    lines = ["import land\n"]
    if darwin:
        lines.append("land.sys.platform = 'darwin'\n")
    if probe == "failed":
        lines.append(FAILED_WITNESS_PROBE)
    elif probe == "foreign":
        lines.append(FOREIGN_UID_WITNESS_PROBE)
    lines.append(EPERM_KILLPG_STUB.format(mode=mode, kills=kills))
    lines.append(after)
    return EXCLUSIVE_MAIN.replace("import land\n", "".join(lines), 1)


def cap_line(cap):
    return (f"land: stopped the command at the {cap}s exclusive cap. "
            f"--for sets a longer cap and needs --reason past {cap}s.\n")


# A command that forks one child in the same process group and writes both pids.
GROUP_SLEEP = (
    "import os, sys, time\n"
    "from pathlib import Path\n"
    "child = os.fork()\n"
    "if child == 0:\n"
    "    Path(sys.argv[2]).write_text(str(os.getpid()))\n"
    "    time.sleep(3600)\n"
    "Path(sys.argv[1]).write_text(str(os.getpid()))\n"
    "time.sleep(3600)\n"
)

# The parent keeps the default SIGTERM handler. The child ignores SIGTERM and records its pid and process group.
IGNORING_GROUP = (
    "import os, signal, sys, time\n"
    "from pathlib import Path\n"
    "parent_path, child_path = sys.argv[1], sys.argv[2]\n"
    "Path(parent_path).write_text(str(os.getpid()))\n"
    "child = os.fork()\n"
    "if child == 0:\n"
    "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "    Path(child_path).write_text(f'{os.getpid()} {os.getpgrp()}\\n')\n"
    "    time.sleep(3600)\n"
    "time.sleep(3600)\n"
)

# The same command with a child that closes the standard streams, so a child that outlives the command
# does not hold the capture pipes of the caller that waits for it.
SURVIVING_IGNORING_GROUP = (
    "import os, signal, sys, time\n"
    "from pathlib import Path\n"
    "parent_path, child_path = sys.argv[1], sys.argv[2]\n"
    "Path(parent_path).write_text(str(os.getpid()))\n"
    "child = os.fork()\n"
    "if child == 0:\n"
    "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "    Path(child_path).write_text(f'{os.getpid()} {os.getpgrp()}\\n')\n"
    "    os.close(0)\n"
    "    os.close(1)\n"
    "    os.close(2)\n"
    "    time.sleep(3600)\n"
    "time.sleep(3600)\n"
)

# The parent exits 0 after the child file has a pid line. The child ignores SIGTERM and sleeps.
# It closes the standard streams so its inherited pipe does not keep the capture open.
BACKGROUND_CHILD = (
    "import os, signal, sys, time\n"
    "from pathlib import Path\n"
    "child_path = sys.argv[1]\n"
    "child = os.fork()\n"
    "if child == 0:\n"
    "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "    Path(child_path).write_text(f'{os.getpid()} {os.getpgrp()}\\n')\n"
    "    os.close(0)\n"
    "    os.close(1)\n"
    "    os.close(2)\n"
    "    time.sleep(3600)\n"
    "path = Path(child_path)\n"
    "deadline = time.monotonic() + 10\n"
    "while '\\n' not in (path.read_text() if path.exists() else '') and time.monotonic() < deadline:\n"
    "    time.sleep(0.01)\n"
    "raise SystemExit(0)\n"
)

# The wrapper sleeps inside _publish. The cap can pass after the slots lock and before the command starts.
SLOW_PUBLISH_MAIN = (
    "import sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "land.EXCLUSIVE_CAP_GRACE = 0.05\n"
    "original = land._publish\n"
    "def slow_publish(*args, **kwargs):\n"
    "    print(f'all-slots-taken {time.monotonic()}', flush=True)\n"
    "    time.sleep(0.6)\n"
    "    return original(*args, **kwargs)\n"
    "land._publish = slow_publish\n"
    "code = land.main(sys.argv[2:])\n"
    "print(f'exit {time.monotonic()}', flush=True)\n"
    "sys.exit(code)\n"
)

# A waiter with a shortened report interval. It runs its wrapped command once it holds a slot.
WAIT_SCRIPT = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "land.REPORT_AFTER = float(sys.argv[2])\n"
    "land.REPORT_EVERY = float(sys.argv[3])\n"
    "kind, repo, command = sys.argv[4:7]\n"
    "slot = land.exclusive_slot if kind == 'exclusive' else (lambda **kw: land.slot('worker', **kw))\n"
    "with slot(repo=repo, command=command):\n"
    "    print('ran', flush=True)\n"
)

# A waiter whose busy-slot probe stops after it takes the named free slot. It touches the marker and
# waits on the fifo before it unlocks that handle, so a test holds the probe's slot lock.
REPORT_SCRIPT = (
    "import fcntl, os, sys\n"
    "from pathlib import Path\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import land\n"
    "marker, release, target, after, every, repo, command = sys.argv[2:9]\n"
    "land.REPORT_AFTER = float(after)\n"
    "land.REPORT_EVERY = float(every)\n"
    "paused = []\n"
    "def slow_busy(handles, name):\n"
    "    try:\n"
    "        fcntl.flock(handles[name], fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "    except BlockingIOError:\n"
    "        return True\n"
    "    if name != target or paused:\n"
    "        fcntl.flock(handles[name], fcntl.LOCK_UN)\n"
    "        return False\n"
    "    paused.append(name)\n"
    "    Path(marker).touch()\n"
    "    os.read(os.open(release, os.O_RDONLY), 1)\n"
    "    fcntl.flock(handles[name], fcntl.LOCK_UN)\n"
    "    return False\n"
    "land._slot_busy = slow_busy\n"
    "with land.slot('worker', repo=repo, command=command):\n"
    "    print('ran', flush=True)\n"
)

# A command that prints the repository its own holder record names, so a test reads a real record.
RECORD_REPO_SCRIPT = (
    "import json, os\n"
    "from pathlib import Path\n"
    "print(json.loads(Path(os.environ['XDG_STATE_HOME'], 'pstack-t3', 'governor', 'holds', 'worker-0').read_text())['repo'])\n"
)

# The fixture is the slot code of the last version before the turn file and must not be updated to match the script.
# LandError and state_home are the names that code calls. main only parses slot and runs the command inside slot or exclusive_slot.
PREVIOUS_SLOT_SCRIPT = '''\
import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path


class LandError(Exception):
    pass


def state_home():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "pstack-t3"


def governor_dir():
    return state_home() / "governor"


def governor_slots():
    config = governor_dir() / "governor.json"
    if config.exists():
        return max(1, int(json.loads(config.read_text()).get("slots", 1)))
    return max(1, min(4, (os.cpu_count() or 4) // 4))


@contextlib.contextmanager
def exclusive_slot():
    """Hold every slot, landing included, so a benchmark runs on a quiet machine.
    Slots are taken in a fixed order with blocking locks, so two exclusive callers cannot deadlock."""
    if os.environ.get("LAND_SLOT"):
        raise LandError("an exclusive slot must be the outermost slot; this command already runs inside one")
    directory = governor_dir()
    directory.mkdir(parents=True, exist_ok=True)
    names = ["landing-0"] + [f"worker-{index}" for index in range(governor_slots())]
    handles = [open(directory / name, "a") for name in names]
    try:
        for handle in handles:
            fcntl.flock(handle, fcntl.LOCK_EX)
        os.environ["LAND_SLOT"] = "exclusive"
        yield
    finally:
        os.environ.pop("LAND_SLOT", None)
        for handle in handles:
            handle.close()


@contextlib.contextmanager
def slot(pool="worker"):
    """Hold one slot of a pool. Landing checks use their own one-slot pool so workers never starve the queue.
    A command already inside a slot (LAND_SLOT set) runs without taking another, so nesting cannot deadlock."""
    if os.environ.get("LAND_SLOT"):
        yield
        return
    directory = governor_dir()
    directory.mkdir(parents=True, exist_ok=True)
    count = 1 if pool == "landing" else governor_slots()
    handles = [open(directory / f"{pool}-{index}", "a") for index in range(count)]
    held = None
    try:
        while held is None:
            for handle in handles:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = handle
                    break
                except BlockingIOError:
                    continue
            if held is None:
                time.sleep(0.5)
        os.environ["LAND_SLOT"] = pool
        yield
    finally:
        os.environ.pop("LAND_SLOT", None)
        for handle in handles:
            handle.close()


def main(argv=None):
    try:
        argv = sys.argv[1:] if argv is None else argv
        parser = argparse.ArgumentParser(prog="land.py")
        sub = parser.add_subparsers(dest="command", required=True)
        slot_parser = sub.add_parser("slot")
        slot_parser.add_argument("--exclusive", action="store_true")
        slot_parser.add_argument("cmd", nargs=argparse.REMAINDER)
        args = parser.parse_args(argv)
        command = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
        if not command:
            raise LandError("slot needs a command after --")
        with (exclusive_slot() if args.exclusive else slot()):
            return subprocess.run(command).returncode
    except LandError as error:
        print(f"land: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
'''


# The fixture is the slot code of the last version that took turns with OFD byte locks on governor/turn
# and must not be updated to match the script. main is the slot command of PREVIOUS_SLOT_SCRIPT.
OFD_SLOT_SCRIPT = r'''
import argparse
import contextlib
import errno
import fcntl
import json
import math
import os
import signal
import struct
import subprocess
import sys
import time
from collections import namedtuple
from pathlib import Path


class LandError(Exception):
    pass


def state_home():
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "pstack-t3"


def governor_dir():
    return state_home() / "governor"


def governor_slots():
    config = governor_dir() / "governor.json"
    if config.exists():
        return max(1, int(json.loads(config.read_text()).get("slots", 1)))
    return max(1, min(4, (os.cpu_count() or 4) // 4))


TURN_MUTEX = 4096
TURN_KIND = 1048576
TURN_PLACE = 2097152
TICKET_KINDS = {b"w": "worker", b"l": "landing", b"e": "exclusive"}
REPORT_AFTER = 15
REPORT_EVERY = 60
EXCLUSIVE_REASON_MAX = 80
_Holder = namedtuple("_Holder", "kind pid repo started command cap reason")
_UNKNOWN = _Holder(None, None, None, None, None, None, None)


def _slot_names(kind, slots):
    """Return the slot filenames for kind, in lock order."""
    workers = [f"worker-{index}" for index in range(slots)]
    return {"worker": workers, "landing": ["landing-0"], "exclusive": ["landing-0"] + workers}[kind]


def _conflicts(earlier, later):
    """True when both can need the same slot file. Worker and landing never do."""
    return earlier == later or "exclusive" in (earlier, later)


def _ofd_excl(fd, byte, blocking):
    """Take an exclusive OFD lock on one byte of fd. Nonblocking returns False when the byte is already held."""
    try:
        # F_OFD_SETLK is 37 and F_OFD_SETLKW is 38 on Linux. This fcntl module does not name them.
        fcntl.fcntl(fd, 38 if blocking else 37, struct.pack("hhqqi4x", fcntl.F_WRLCK, os.SEEK_SET, byte, 1, 0))
    except OSError as error:
        if error.errno in (errno.EAGAIN, errno.EACCES):
            return False
        if error.errno in (errno.EINVAL, errno.ENOTSUP):
            raise LandError("this filesystem does not support the slot governor's ordering lock") from None
        raise
    return True


def _ofd_unlock(fd, byte):
    fcntl.fcntl(fd, 37, struct.pack("hhqqi4x", fcntl.F_UNLCK, os.SEEK_SET, byte, 1, 0))


def _try_take(kind, handles, slots):
    """Flock the slot files kind needs, without blocking. Returns the locked files, or None with none locked.
    Worker and landing need one file. Exclusive needs every file."""
    names = _slot_names(kind, slots)
    wanted = len(names) if kind == "exclusive" else 1
    taken = []
    for name in names:
        try:
            fcntl.flock(handles[name], fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if kind == "exclusive":
                break
            continue
        taken.append(handles[name])
        if len(taken) == wanted:
            return taken
    for handle in taken:
        fcntl.flock(handle, fcntl.LOCK_UN)
    return None


def _can_satisfy(kind, handles, slots):
    """True when a waiter of kind could take its slots now. Holds nothing on return."""
    taken = _try_take(kind, handles, slots)
    for handle in taken or []:
        fcntl.flock(handle, fcntl.LOCK_UN)
    return taken is not None


def _earlier_waiters(gate, ticket, kind):
    """Return the kinds of the live tickets that conflict with kind and came before this caller.
    A ticket is live while its byte is locked. Runs under the turn mutex."""
    low, upcoming = struct.unpack("<QQ", os.pread(gate, 16, 0).ljust(16, b"\0"))
    first = low = low or 1
    kinds = set()
    live = False
    for number in range(low, upcoming):
        if number == ticket:
            break
        if _ofd_excl(gate, TURN_PLACE + number, False):
            _ofd_unlock(gate, TURN_PLACE + number)
            if not live:
                low = number + 1
            continue
        live = True
        earlier = TICKET_KINDS[os.pread(gate, 1, TURN_KIND + number)]
        if _conflicts(earlier, kind):
            kinds.add(earlier)
    if low != first or not upcoming:
        os.pwrite(gate, struct.pack("<QQ", low, max(upcoming, 1)), 0)
    return kinds


def _take_ticket(gate, place, kind):
    """Issue the next ticket to this caller and lock its byte on place. Runs under the turn mutex."""
    low, ticket = struct.unpack("<QQ", os.pread(gate, 16, 0))
    os.pwrite(gate, struct.pack("<QQ", low, ticket + 1), 0)
    os.pwrite(gate, kind[:1].encode(), TURN_KIND + ticket)
    _ofd_excl(place, TURN_PLACE + ticket, False)
    return ticket


def _short_command(command):
    """The wrapped command's first 80 characters, with CR and LF flattened to spaces."""
    if not command:
        return None
    return command[:80].replace("\r\n", " ").replace("\r", " ").replace("\n", " ") or None


def _repo_text(repo):
    """The repository as an absolute path string, or None when the path cannot be resolved.

    A repo of None selects the working directory.
    """
    try:
        return os.path.abspath(repo or os.getcwd())
    except (OSError, ValueError, TypeError):
        return None


def _hold_path(name):
    return governor_dir() / "holds" / name


def _dump_holder(path, holder):
    """Write one holder record atomically, replacing whatever the previous holder left.

    The record is written to a temporary file whose descriptor is already locked, and that inode
    is renamed into place, so the record lock travels with the record.
    Returns the descriptor, still locked, for the caller to release when it lets the slot go.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o666)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.pwrite(descriptor, json.dumps(dict(zip(_Holder._fields, holder))).encode(), 0)
        os.replace(temporary, path)
    except BaseException:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    return descriptor


def _release_records(records):
    """Unlock and close the record descriptors this process published."""
    for descriptor in records:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _finite(value):
    """True for a start time a record may hold and formatting can use."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _short_reason(text):
    """One line of reason text, or None.

    Keeps characters for which str.isprintable() is true, strips the ends,
    and cuts to EXCLUSIVE_REASON_MAX. Reads that limit at call time.
    Tabs, newlines, and other non-printable characters are deleted.
    """
    if not isinstance(text, str):
        return None
    kept = "".join(character for character in text if character.isprintable()).strip()
    return kept[:EXCLUSIVE_REASON_MAX] or None


def _cap_seconds(value):
    """A positive finite cap in seconds, or None. A bool is not a cap."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _read_holder(path):
    """The holder a record names, with each unreadable field as unknown. A missing, empty, or malformed record is unknown."""
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return _UNKNOWN
    if not isinstance(record, dict):
        return _UNKNOWN
    kind, pid, repo, started, command, cap, reason = (record.get(field) for field in _Holder._fields)
    if not (isinstance(command, str) and command and len(command) <= 80
            and "\n" not in command and "\r" not in command):
        command = None
    return _Holder(
        kind if kind in ("worker", "landing", "exclusive") else None,
        pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        repo if isinstance(repo, str) and repo else None,
        started if _finite(started) else None,
        command,
        _cap_seconds(cap),
        _short_reason(reason),
    )


def _record_holder(path):
    """The holder a record names when a conflicting lock proves a writer, or unknown.

    The record is read only when the nonblocking shared lock fails with errno EAGAIN or
    EWOULDBLOCK. A successful lock, a failed open, or any other lock error returns unknown.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return _UNKNOWN
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            return _read_holder(path)
        return _UNKNOWN
    finally:
        os.close(descriptor)
    return _UNKNOWN


def _slot_busy(handles, name):
    """True when the slot file is held by someone else. Probes the waiter's own unlocked handle and releases it at once."""
    try:
        fcntl.flock(handles[name], fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    fcntl.flock(handles[name], fcntl.LOCK_UN)
    return False


def _busy_names(handles, names):
    """The slot names held by someone else. Runs under the turn mutex."""
    return [name for name in names if _slot_busy(handles, name)]


def _publish(kind, handles, taken, repo, command, cap=None, reason=None):
    """Write one holder record for each slot just taken, before the turn mutex unlocks.

    Returns the locked record descriptors, one per slot whose write succeeded.
    Stores cap and reason only when kind is exclusive.
    """
    if kind != "exclusive":
        cap = None
        reason = None
    holder = _Holder(kind, os.getpid(), _repo_text(repo), time.time(), _short_command(command), cap, reason)
    records = []
    for name, handle in handles.items():
        if handle not in taken:
            continue
        try:
            records.append(_dump_holder(_hold_path(name), holder))
        except OSError:
            pass
    return records


def _clear():
    """Remove this process's records while its slot flocks are still held."""
    for path in (governor_dir() / "holds").glob("*"):
        if _read_holder(path).pid == os.getpid():
            try:
                path.unlink()
            except OSError:
                pass


def _live_holders(names):
    """The holders of the named busy slots, distinct by pid when pid is set, otherwise by the whole holder.

    Each named slot checks its own record. The record is read only when the nonblocking
    shared lock fails with errno EAGAIN or EWOULDBLOCK. A successful lock, a failed open,
    or any other lock error returns unknown for that slot.
    """
    holders, seen = [], set()
    for name in names:
        holder = _record_holder(_hold_path(name))
        key = holder.pid if holder.pid is not None else holder
        if key in seen:
            continue
        seen.add(key)
        holders.append(holder)
    return holders


def _held_for(holder):
    if holder.started is None:
        return "unknown"
    return f"{int(max(0, time.time() - holder.started))}s"


def _span(seconds):
    """A duration for a wait line. Whole minutes when seconds is a positive multiple of 60."""
    if seconds > 0 and seconds % 60 == 0:
        return f"{seconds / 60:g}m"
    if float(seconds).is_integer():
        return f"{int(seconds)}s"
    return f"{seconds:g}s"


def _cap_clause(holder):
    """The wait-line cap phrase for an exclusive holder, or None."""
    if holder.kind != "exclusive":
        return None
    cap = _cap_seconds(holder.cap)
    if cap is None:
        return None
    cap_text = _span(cap)
    if not _finite(holder.started):
        return f"cap {cap_text}"
    left = max(0.0, cap - (time.time() - holder.started))
    return f"{_span(left)} left of {cap_text}"


def _format_wait(elapsed, holders):
    """One line naming each proven holder by kind, repository, hold duration, and short command.

    A holder with no proven record reads as unknown, and so does each field its record
    does not carry. Zero holders produce no holder text. An exclusive holder with a
    usable cap names the time left when its start time is usable. It names the cap
    alone when the start time is not. The line includes the reason when the record
    has one.
    """
    named = []
    for holder in holders:
        if holder == _UNKNOWN:
            named.append("unknown")
            continue
        clause = _cap_clause(holder)
        extra = ""
        if clause:
            extra = f", {clause}"
            if holder.reason:
                extra += f", {holder.reason}"
        named.append(f"{holder.kind or 'unknown'} {holder.repo or 'unknown'} for {_held_for(holder)}{extra}: "
                     f"{holder.command or 'unknown'}")
    if not named:
        return f"land: waited {int(elapsed)}s for a slot"
    return f"land: waited {int(elapsed)}s for a slot; " + "; ".join(named)


def _report_due(elapsed, last):
    if elapsed < REPORT_AFTER:
        return False
    return last is None or elapsed - last >= REPORT_EVERY


def _raise_exit(signum, frame):
    raise SystemExit(128 + signum)


@contextlib.contextmanager
def _held(kind, repo, command, cap=None, reason=None):
    """Hold the slots kind needs, publish this holder, and clear the records while the flocks are still held."""
    held, records, acquired = _admit(kind, repo, command, cap, reason)
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGHUP)}
    for signum in previous:
        signal.signal(signum, _raise_exit)
    try:
        yield acquired
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        _clear()
        for handle in held:
            handle.close()
        _release_records(records)


def _admit(kind, repo=None, command=None, cap=None, reason=None):
    directory = governor_dir()
    directory.mkdir(parents=True, exist_ok=True)
    slots = governor_slots()
    handles = {name: open(directory / name, "a") for name in _slot_names("exclusive", slots)}
    turns = []
    records = []
    try:
        # Two opens, so that unlocking the mutex byte on gate keeps the ticket byte locked on place.
        for _ in range(2):
            turns.append(os.open(directory / "turn", os.O_RDWR | os.O_CREAT, 0o666))
        gate, place = turns
        ticket = None
        started, reported = time.time(), None
        while True:
            _ofd_excl(gate, TURN_MUTEX, True)
            earlier = _earlier_waiters(gate, ticket, kind)
            taken = None
            if not any(_can_satisfy(waiter, handles, slots) for waiter in earlier):
                taken = _try_take(kind, handles, slots)
            if taken is not None:
                acquired = time.monotonic()
            if taken is None and ticket is None:
                ticket = _take_ticket(gate, place, kind)
            if taken is None:
                busy = _busy_names(handles, _slot_names(kind, slots))
            else:
                records = _publish(kind, handles, taken, repo, command, cap, reason)
            if taken is not None and ticket is not None:
                _ofd_unlock(place, TURN_PLACE + ticket)
            _ofd_unlock(gate, TURN_MUTEX)
            if taken is not None:
                return list(handles.values()), records, acquired
            waited = time.time() - started
            if _report_due(waited, reported):
                line = _format_wait(waited, _live_holders(busy))
                try:
                    print(line, file=sys.stderr, flush=True)
                except (OSError, ValueError):
                    pass
                reported = waited
            time.sleep(0.5)
    except BaseException:
        _release_records(records)
        for handle in handles.values():
            handle.close()
        raise
    finally:
        for descriptor in turns:
            os.close(descriptor)


@contextlib.contextmanager
def exclusive_slot(repo=None, command=None, cap=None, reason=None):
    """Hold every slot, landing included, so a benchmark runs on a quiet machine.
    It waits without holding any slot, so other callers keep taking free slots until every slot is free at once.
    A later conflicting caller that waits on this turn file leaves the slots when this caller could take every one of them."""
    if os.environ.get("LAND_SLOT"):
        raise LandError("an exclusive slot must be the outermost slot; this command already runs inside one")
    with _held("exclusive", repo, command, cap, reason) as acquired:
        os.environ["LAND_SLOT"] = "exclusive"
        try:
            yield acquired
        finally:
            os.environ.pop("LAND_SLOT", None)


@contextlib.contextmanager
def slot(pool="worker", repo=None, command=None):
    """Hold one slot of a pool. Landing checks use their own one-slot pool so workers never starve the queue.
    A command already inside a slot (LAND_SLOT set) runs without taking another, so nesting cannot deadlock.
    A caller that shares this turn file leaves a free slot for an earlier conflicting ticket that can take it."""
    if os.environ.get("LAND_SLOT"):
        yield
        return
    with _held(pool, repo, command):
        os.environ["LAND_SLOT"] = pool
        try:
            yield
        finally:
            os.environ.pop("LAND_SLOT", None)


def main(argv=None):
    try:
        argv = sys.argv[1:] if argv is None else argv
        parser = argparse.ArgumentParser(prog="land.py")
        sub = parser.add_subparsers(dest="command", required=True)
        slot_parser = sub.add_parser("slot")
        slot_parser.add_argument("--exclusive", action="store_true")
        slot_parser.add_argument("cmd", nargs=argparse.REMAINDER)
        args = parser.parse_args(argv)
        command = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
        if not command:
            raise LandError("slot needs a command after --")
        with (exclusive_slot() if args.exclusive else slot()):
            return subprocess.run(command).returncode
    except LandError as error:
        print(f"land: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
'''


class SlotCaller:
    def __init__(self, name, process, directory, release, retry):
        self.name, self.process, self.directory, self.release, self.retry = name, process, directory, release, retry
        self.waits = 0

    @property
    def started(self):
        return (self.directory / "started").exists()


class SlotCallers:
    """Real slot processes on one scratch governor. Each command holds its slot until the test writes to its fifo.

    A stepped caller runs land.py with time.sleep replaced. It marks each failed attempt with a waiting-N file and
    makes its next attempt when the test writes to its retry fifo, so the test decides which waiter tries next.
    A marked caller writes the same waiting-N files and retries on its own.
    Each instance on one fixture writes its own log and its own caller directories.
    """

    def __init__(self, test, slots):
        self.test = test
        self.governor = test.base / "state/pstack-t3/governor"
        self.governor.mkdir(parents=True, exist_ok=True)
        (self.governor / "governor.json").write_text(json.dumps({"slots": slots}))
        # The suite may itself run inside a slot. A child that inherits LAND_SLOT takes no lock,
        # and an inherited LAND_SLOT_FILES would name the suite's slot files.
        self.env = {key: value for key, value in os.environ.items() if key not in {"LAND_SLOT", "LAND_SLOT_FILES"}}
        self.stamp = 0
        while (test.base / f"slot-log-{self.stamp}").exists():
            self.stamp += 1
        self.log = test.base / f"slot-log-{self.stamp}"
        self.log.touch()
        self.callers = []
        self.descriptors = []
        test.addCleanup(self.close)

    def close(self):
        for caller in self.callers:
            if caller.process.poll() is None:
                os.killpg(caller.process.pid, signal.SIGKILL)
            caller.process.wait(timeout=20)
        for descriptor in self.descriptors:
            os.close(descriptor)

    def fifo(self, path):
        os.mkfifo(path)
        # Keep both ends open so a write never waits for a reader.
        self.descriptors.append(os.open(path, os.O_RDWR | os.O_NONBLOCK))
        return self.descriptors[-1]

    def start(self, name, *flags, script=SCRIPT, stepped=False, marked=False, pool="worker", hold=None):
        """Start a slot caller. With hold, its command leaves the slot after hold seconds and not on release."""
        directory = self.test.base / f"caller-{self.stamp}-{name}"
        directory.mkdir()
        release, retry = self.fifo(directory / "release"), self.fifo(directory / "retry")
        command = [*flags, "--", sys.executable, "-c", SLOT_HOLD, str(self.log), name, str(directory / "release"),
                   *([] if hold is None else [str(hold)])]
        if stepped or marked or pool == "landing":
            mode = str(directory / "retry") if stepped else "marked" if marked else ""
            argv = [sys.executable, "-c", SLOT_WRAPPED, str(Path(script).parent),
                    mode, pool, *(command if pool == "worker" else command[1:])]
        else:
            argv = [sys.executable, str(script), "slot", *command]
        process = real_popen(
            ProcessReason.GOVERNOR, argv, env=self.env, cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.callers.append(SlotCaller(name, process, directory, release, retry))
        return self.callers[-1]

    def wait(self, reached, barrier):
        deadline = time.monotonic() + 20
        while not reached():
            if time.monotonic() >= deadline:
                self.test.fail(f"barrier not reached: {barrier}")
            time.sleep(0.01)

    def enter(self, caller):
        self.wait(lambda: caller.started, f"{caller.name} started")
        return caller

    def settle(self, caller):
        """Wait for a stepped caller's attempt to end. Returns "started" or "waiting"."""
        waiting = caller.directory / f"waiting-{caller.waits + 1}"
        self.wait(lambda: caller.started or waiting.exists(), f"{caller.name} finished an attempt")
        if caller.started:
            return "started"
        caller.waits += 1
        return "waiting"

    def step(self, caller):
        os.write(caller.retry, b"x")
        return self.settle(caller)

    def queued(self, caller):
        """Wait until a marked caller has failed its first attempt, which it does only after it takes a ticket."""
        self.wait(lambda: (caller.directory / "waiting-1").exists(), f"{caller.name} waits with a ticket")
        return caller

    def release(self, caller):
        os.write(caller.release, b"x")
        self.test.assertEqual(caller.process.wait(timeout=20), 0)

    def events(self):
        return [tuple(line.split()) for line in self.log.read_text().splitlines()]

    def entries(self):
        return [fields[1] for fields in self.events() if fields[0] == "enter"]

    def peak(self, counted=lambda name: True):
        """Return the most counted callers inside a slot at once."""
        running, peak = set(), 0
        for word, name, *_ in self.events():
            if word == "exit":
                running.remove(name)
                continue
            running.add(name)
            peak = max(peak, sum(counted(caller) for caller in running))
        return peak


class LandingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        # Cleanups run after tearDown, last registered first, so this one runs after every holder a test leaves running is stopped.
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.base / "state")})
        self.env.start()
        sh("git", "init", "-q", "--bare", "-b", "main", "origin.git", cwd=self.base)
        sh("git", "clone", "-q", "origin.git", "work", cwd=self.base)
        self.work = self.base / "work"
        sh("git", "checkout", "-q", "-b", "main", cwd=self.work)
        for name in ("a.txt", "b.txt", "lib/x.py"):
            (self.work / name).parent.mkdir(exist_ok=True)
            (self.work / name).write_text(f"{name}\n")
        (self.work / "check.sh").write_text("#!/bin/sh\n! grep -rq BROKEN --include=*.txt .\n")
        (self.work / "check.sh").chmod(0o755)
        self.commit("init")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.install_github_remote()

    def install_github_remote(self):
        """Point origin at https://github.com/o/r.git and put a git stand-in first on PATH.

        git remote get-url still prints the GitHub URL. When push, fetch, ls-remote, or pull is the
        stand-in's first argument and git remote get-url origin exits 0 with a URL that starts with
        https://github.com/, the stand-in runs git with that URL rewritten to origin.git.
        """
        git = shlex.quote(shutil.which("git"))
        bindir = self.base / "git-wrap"
        bindir.mkdir()
        rewrite = shlex.quote("url." + str(self.base / "origin.git") + "/.insteadOf=")
        script = bindir / "git"
        script.write_text(
            "#!/bin/sh\n"
            "case $1 in\n"
            "push|fetch|ls-remote|pull)\n"
            f"  if url=$({git} remote get-url origin 2>/dev/null); then\n"
            "    case $url in\n"
            f"    https://github.com/*) exec {git} -c {rewrite}\"$url\" \"$@\" ;;\n"
            "    esac\n"
            "  fi ;;\n"
            "esac\n"
            f"exec {git} \"$@\"\n"
        )
        script.chmod(0o755)
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
        sh("git", "remote", "set-url", "origin", "https://github.com/o/r.git", cwd=self.work)

    def tearDown(self):
        self.env.stop()

    def commit(self, message, cwd=None):
        sh("git", "add", "-A", cwd=cwd or self.work)
        sh("git", "commit", "-qm", message, cwd=cwd or self.work)
        return sh("git", "rev-parse", "HEAD", cwd=cwd or self.work)

    def git_raw(self, *args, cwd):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=git_env())

    def land(self, *args, ok=True):
        code, out, err = drive(SCRIPT, ["--repo", str(self.work), *args], env=os.environ.copy())
        self.assertEqual(code == 0, ok, out + err)
        return (out if ok else err).strip()

    def land_in_another_process(self, *args, ok=True):
        """Run land.py in a separate process. After the exit check, return stripped stdout when the exit code is 0 and stripped stderr otherwise."""
        result = real_process(
            ProcessReason.LOCK, SCRIPT, ["--repo", str(self.work), *args], env=os.environ.copy())
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return (result.stdout if result.returncode == 0 else result.stderr).strip()

    def init(self, mode="auto", batch=1, **extra):
        args = ["init", "--trunk", extra.get("trunk", "main"), "--mode", mode, "--check", "./check.sh", "--batch", str(batch)]
        if "merge_method" in extra:
            args += ["--merge-method", extra["merge_method"]]
        if "base" in extra:
            args += ["--base", extra["base"]]
        return self.land(*args)

    def worker(self, name, edits, base="origin/main"):
        """A worker branch in its own worktree with one commit. Returns its SHA."""
        path = self.base / name
        sh("git", "worktree", "add", "-q", "-b", name, str(path), base, cwd=self.work)
        for file, text in edits.items():
            (path / file).parent.mkdir(parents=True, exist_ok=True)
            (path / file).write_text(text)
        return self.commit(name, cwd=path)

    def origin_log(self):
        return sh("git", "log", "--format=%s", "main", cwd=self.base / "origin.git").splitlines()

    def ref_exists(self, ref, cwd):
        return subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=cwd, capture_output=True).returncode == 0

    def install_racing_branch_delete(self):
        """While git push --delete runs, another deleter holds the ref lock and removes the ref.

        git 2.55 then rejects the push with "File exists" and "reference already exists".
        That stderr does not contain "does not exist", and ls-remote shows the ref gone.
        """
        hook = self.base / "origin.git" / "hooks" / "pre-receive"
        marker = self.base / "race-deleted"
        hook.write_text(f"""#!/bin/sh
marker={marker}
while read old new ref; do
  if [ "$new" = "0000000000000000000000000000000000000000" ]; then
    path=$(git rev-parse --git-path "$ref")
    echo x > "$path.lock"
    rm -f "$path"
    echo "$ref" >> "$marker"
  fi
done
exit 0
""")
        hook.chmod(0o755)

    def record_pushes(self):
        """Log each git push while later commands run. The real git stays behind the wrapper."""
        log = self.base / "git-pushes"
        bindir = self.base / "bin"
        bindir.mkdir(exist_ok=True)
        real = subprocess.run(["which", "git"], capture_output=True, text=True, check=True).stdout.strip()
        script = bindir / "git"
        script.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = push ]; then\n"
            f"  printf '%s\\n' \"$*\" >> '{log}'\n"
            "fi\n"
            f"exec '{real}' \"$@\"\n"
        )
        script.chmod(0o755)
        self._push_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bindir) + os.pathsep + self._push_path
        return log

    def stop_recording_pushes(self):
        os.environ["PATH"] = self._push_path

    def assert_pushes_stay_on_their_refs(self, log):
        lines = log.read_text().splitlines()
        self.assertTrue(lines)
        for line in lines:
            self.assertTrue(line.startswith("push --no-follow-tags "), line)
            self.assertNotIn("--dry-run", line)
            self.assertFalse(any(part.startswith(":") for part in line.split()), line)

    def open_pr_with_a_merge_requested(self):
        """One merge-mode entry noted merge requested by the queue, with its PR open."""
        (self.base / "required-checks").write_text("")
        self.init(mode="merge")
        self.queue_one()
        self.land("land")
        self.assertEqual(self.land("land"), "nothing to land")
        self.assertIn("merge requested by the queue", self.land("status", "E1"))

    def merged_queue_branch(self):
        """Save the queue head, move trunk to it, then mark the PR merged at that head with that head as the merge commit."""
        remote = self.base / "origin.git"
        sha = sh("git", "rev-parse", "refs/heads/landing/e1", cwd=remote)
        sh("git", "update-ref", "refs/heads/main", sha, cwd=remote)
        (self.base / "pr-state").write_text(f"MERGED {sha} {sha}")
        return sha

    def claim(self, holder, paths, *extra, ok=True):
        return self.land("lease", "claim", "--holder", holder, "--paths", paths, *extra, ok=ok)

    def lease_row(self, number):
        with land.Store.for_repo(self.work) as store:
            return tuple(store.db.execute("SELECT * FROM lease WHERE id = ?", (number,)).fetchone())

    def owner_state(self):
        with land.Store.for_repo(self.work) as store:
            return ([tuple(row) for row in store.db.execute("SELECT * FROM owner")],
                    store.db.execute("SELECT count(*) FROM log").fetchone()[0])

    def lease_check(self, holder, paths):
        code, out, _err = drive(
            SCRIPT,
            ["--repo", str(self.work), "lease", "check", "--holder", holder, "--paths", paths],
            env=os.environ.copy())
        return code, out.strip()

    def rebuild_entry_without_autoincrement(self):
        """Rewrite entry as an older database: the same rows, and no AUTOINCREMENT."""
        with land.Store.for_repo(self.work) as store:
            rows = [dict(row) for row in store.db.execute("SELECT * FROM entry")]
            store.db.execute("DROP TABLE entry")
            store.db.executescript(land.SCHEMA.replace(" AUTOINCREMENT", ""))
            columns = [info["name"] for info in store.db.execute("PRAGMA table_info(entry)")]
            for row in rows:
                present = [name for name in columns if name in row]
                store.db.execute(
                    f"INSERT INTO entry ({', '.join(present)}) VALUES ({', '.join('?' * len(present))})",
                    [row[name] for name in present])
            if store.db.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
                store.db.execute("DELETE FROM sqlite_sequence WHERE name = 'entry'")

    def use_non_english_locale(self):
        """Set LANG and LC_ALL to de_DE.UTF-8 for the rest of this test.

        Returns whether git on this machine prints German under that locale.
        When it does not, git on PATH hides the English conflict strings unless
        that process has LC_ALL=C, and records each cherry-pick environment.
        """
        self.git_locale_log = self.base / "git-locale.log"
        translated = False
        try:
            locpath = self.base / "locales"
            locpath.mkdir()
            compiled = subprocess.run(
                ["localedef", "-f", "UTF-8", "-i", "de_DE", str(locpath / "de_DE.UTF-8")],
                capture_output=True)
        except OSError:
            compiled = None
        if compiled is not None and compiled.returncode == 0:
            os.environ["LOCPATH"] = str(locpath)
            env = os.environ.copy()
            env["LANG"] = "de_DE.UTF-8"
            env["LC_ALL"] = "de_DE.UTF-8"
            probe = subprocess.run(["git", "status"], cwd=self.work, capture_output=True, text=True, env=env)
            translated = "Auf Branch" in probe.stdout
        os.environ["LANG"] = "de_DE.UTF-8"
        os.environ["LC_ALL"] = "de_DE.UTF-8"
        if not translated:
            self.install_c_locale_gate()
        return translated

    def install_c_locale_gate(self):
        """Hide English conflict text unless this git process has LC_ALL=C."""
        current = shutil.which("git")
        bindir = self.base / "locale-gate"
        bindir.mkdir()
        script = bindir / "git"
        script.write_text(
            "#!" + sys.executable + "\n"
            "import os, subprocess, sys\n"
            f"real = {current!r}\n"
            f"log = {str(self.git_locale_log)!r}\n"
            "args = sys.argv[1:]\n"
            "if 'cherry-pick' in args:\n"
            "    with open(log, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('cherry-pick LC_ALL=%s\\n' % os.environ.get('LC_ALL', ''))\n"
            "proc = subprocess.run([real, *args], capture_output=True)\n"
            "out, err = proc.stdout, proc.stderr\n"
            "if os.environ.get('LC_ALL') != 'C':\n"
            "    out = out.replace(b'CONFLICT', b'KONFLIKT').replace(b'could not apply', b'nicht anwenden')\n"
            "    err = err.replace(b'CONFLICT', b'KONFLIKT').replace(b'could not apply', b'nicht anwenden')\n"
            "sys.stdout.buffer.write(out)\n"
            "sys.stderr.buffer.write(err)\n"
            "raise SystemExit(proc.returncode)\n"
        )
        script.chmod(0o755)
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ["PATH"]

    def subprocesses_during_land(self):
        """Commands this land run starts while a write transaction is open."""
        with land.Store.for_repo(self.work) as store:
            depth = {"n": 0}
            hits = []
            original_tx = land.Store.tx
            original_run = land.subprocess.run
            original_popen = land.subprocess.Popen

            @contextlib.contextmanager
            def watching_tx(store_self):
                depth["n"] += 1
                try:
                    with original_tx(store_self) as db:
                        yield db
                finally:
                    depth["n"] -= 1

            def record(command):
                if isinstance(command, (list, tuple)):
                    hits.append(" ".join(str(part) for part in command))
                else:
                    hits.append(str(command))

            def watching_run(*args, **kwargs):
                if depth["n"]:
                    record(args[0] if args else kwargs.get("args"))
                return original_run(*args, **kwargs)

            def watching_popen(*args, **kwargs):
                # subprocess.run calls Popen for the same command. Record a direct Popen only.
                if depth["n"] and inspect.stack()[1].function != "run":
                    record(args[0] if args else kwargs.get("args"))
                return original_popen(*args, **kwargs)

            with mock.patch.object(land.Store, "tx", watching_tx), \
                 mock.patch.object(land.subprocess, "run", watching_run), \
                 mock.patch.object(land.subprocess, "Popen", watching_popen):
                land.land(store)
            return hits

    def fake_gh(self):
        fake = self.base / "gh"
        fake.write_text(r"""#!PYTHON
import json, os, signal, subprocess, sys
from pathlib import Path
base = Path(BASE)
args = sys.argv[1:]
with open(base / "gh-calls", "a") as calls:
    print(" ".join(args), file=calls)


def git(*command, stdin=None, index=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "gh", "GIT_AUTHOR_EMAIL": "gh@gh",
           "GIT_COMMITTER_NAME": "gh", "GIT_COMMITTER_EMAIL": "gh@gh"}
    if index:
        env["GIT_INDEX_FILE"] = index
    return subprocess.run(["git", "--git-dir", str(base / "origin.git"), *command],
                          input=stdin, capture_output=True, text=True, env=env)


def prs():
    path = base / "prs.json"
    return json.loads(path.read_text()) if path.exists() else {}


def save(table):
    (base / "prs.json").write_text(json.dumps(table))


def number_of(target):
    return target.rsplit("/", 1)[-1] if "/pull/" in target else ""


def head_of(pr):
    if pr["state"] == "MERGED":
        return pr["oid"]
    return git("rev-parse", "--verify", "--quiet", "refs/heads/" + pr["head"]).stdout.strip()


def refuse(text):
    print(text, file=sys.stderr)
    sys.exit(1)


def jq(query, doc):
    ran = subprocess.run(["jq", "-r", query], input=json.dumps(doc), capture_output=True, text=True)
    if ran.returncode != 0:
        refuse(ran.stderr)
    sys.stdout.write(ran.stdout if ran.stdout.endswith("\n") else ran.stdout + "\n")


def outside_commit():
    index = str(base / "gh-index")
    git("read-tree", "refs/heads/main", index=index)
    blob = git("hash-object", "-w", "--stdin", stdin="outside\n").stdout.strip()
    git("update-index", "--add", "--cacheinfo", "100644," + blob + ",outside.txt", index=index)
    tree = git("write-tree", index=index).stdout.strip()
    commit = git("commit-tree", tree, "-p", "refs/heads/main", "-m", "outside").stdout.strip()
    git("update-ref", "refs/heads/main", commit)


def replace_head(number, marker):
    path = base / ("pr-" + number + "-" + marker)
    pr = prs().get(number)
    if not path.exists() or pr is None:
        return
    replacement = path.read_text().strip()
    path.unlink()
    git("update-ref", "refs/heads/" + pr["head"], replacement)
    (base / ("pr-" + number + "-checks")).write_text("passed " + replacement)


def merge(number, pr, head):
    main = git("rev-parse", "refs/heads/main").stdout.strip()
    unmergeable = "GraphQL: Pull Request is not mergeable (mergePullRequest)"
    if "--rebase" in args:
        merged = main
        fork = git("merge-base", main, head).stdout.strip()
        for commit in git("rev-list", "--reverse", fork + ".." + head).stdout.split():
            tree = git("merge-tree", "--write-tree", "--merge-base", commit + "^", merged, commit)
            if tree.returncode != 0:
                refuse(unmergeable)
            message = git("log", "-1", "--format=%B", commit).stdout.strip()
            merged = git("commit-tree", tree.stdout.split()[0], "-p", merged, "-m", message).stdout.strip()
    else:
        tree = git("merge-tree", "--write-tree", main, head)
        if tree.returncode != 0:
            refuse(unmergeable)
        if "--squash" in args:
            parents = ["-p", main]
            message = pr.get("title") or "pull " + number
            if pr.get("body"):
                message += "\n\n" + pr["body"]
        else:
            parents = ["-p", main, "-p", head]
            message = "Merge pull request #" + number + " from " + pr["head"]
        merged = git("commit-tree", tree.stdout.split()[0], *parents, "-m", message).stdout.strip()
    git("update-ref", "refs/heads/main", merged, main)
    return merged


if args[:2] == ["repo", "view"]:
    if (base / "repo-view-fails").exists():
        print("repo view failed", file=sys.stderr)
        sys.exit(1)
    requested = ""
    if "--json" in args:
        requested = args[args.index("--json") + 1]
    if "mergeCommitAllowed" in requested.split(","):
        path = base / "repo-merge.json"
        if path.exists():
            print(path.read_text().strip())
        else:
            print(json.dumps({"mergeCommitAllowed": True, "squashMergeAllowed": True, "rebaseMergeAllowed": True}))
        sys.exit(0)
    print(json.dumps({"nameWithOwner": "o/r"}))
    sys.exit(0)
if args[:1] == ["api"] and any("required_status_checks" in arg for arg in args):
    required = base / "protection-required.json"
    if required.exists():
        text = required.read_text()
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        sys.exit(0)
    print("required checks unavailable", file=sys.stderr)
    sys.exit(1)
if args[:1] == ["api"] and any("/branches/" in arg for arg in args):
    endpoint = next(arg for arg in args if "/branches/" in arg)
    branch = endpoint.split("/branches/", 1)[1]
    if (base / "forge-says-missing").exists():
        print("branch-status 404", file=open(base / "gh-calls", "a"))
        print("HTTP/2.0 404 Not Found")
        print()
        print(json.dumps({"message": "Branch not found", "status": "404"}))
        sys.exit(1)
    if (base / "branch-query-fails").exists():
        print("branch-status 500", file=open(base / "gh-calls", "a"))
        print("HTTP/2.0 500 Internal Server Error")
        print()
        print(json.dumps({"message": "unavailable"}))
        sys.exit(1)
    exists = git("rev-parse", "--verify", "--quiet", "refs/heads/" + branch).returncode == 0
    status = "200" if exists else "404"
    print("branch-status " + status, file=open(base / "gh-calls", "a"))
    print("HTTP/2.0 " + ("200 OK" if exists else "404 Not Found"))
    print()
    if exists:
        print(json.dumps({"name": branch}))
        sys.exit(0)
    print(json.dumps({"message": "Branch not found", "status": "404"}))
    sys.exit(1)
if args[:2] == ["pr", "create"]:
    table = prs()
    head = args[args.index("--head") + 1] if "--head" in args else ""
    if head:
        (base / "pr-head").write_text(head)
    seq = base / "pr-seq"
    if seq.exists():
        number = seq.read_text().strip() or "9"
        seq.write_text(str(int(number) + 1) + "\n")
    else:
        number = str(max([8, *map(int, table)]) + 1)
    table[number] = {"head": head, "state": "OPEN",
                     "title": args[args.index("--title") + 1] if "--title" in args else "",
                     "body": args[args.index("--body") + 1] if "--body" in args else ""}
    save(table)
    if (base / "crash-on-create").exists():
        (base / "crash-on-create").unlink()
        os.kill(os.getppid(), signal.SIGKILL)
    print("https://github.com/o/r/pull/" + number)
elif args[:2] == ["pr", "merge"]:
    print(" ".join(args), file=open(base / "merge-calls", "a"))
    if "--disable-auto" in args:
        if (base / "disable-auto-fails").exists():
            print((base / "disable-auto-fails").read_text() or "API unavailable", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    if "--match-head-commit" in args:
        pinned = prs().get(number_of(args[2]))
        if pinned and pinned["state"] == "OPEN" and head_of(pinned) != args[args.index("--match-head-commit") + 1]:
            refuse("GraphQL: Head branch was modified. Review and try the merge again. (mergePullRequest)")
    if (base / "merge-refused").exists():
        refuse("GraphQL: At least 1 approving review is required by reviewers with write access.")
    if "--auto" in args and (base / "auto-merge-disabled").exists():
        refuse("GraphQL: Auto merge is not allowed for this repository (enablePullRequestAutoMerge)")
    if "--auto" not in args and (base / "plain-merge-fails").exists():
        refuse((base / "plain-merge-fails").read_text().strip() or "merge conflict")
    if "--auto" in args and not (base / "auto-merges-immediately").exists():
        if not (base / "required-checks").exists():
            refuse("GraphQL: Pull request is in clean status (enablePullRequestAutoMerge)")
        sys.exit(0)
    checks_now = (base / "checks").read_text().strip() if (base / "checks").exists() else ""
    if (base / "policy-until-passed").exists() and checks_now != "passed":
        refuse("X Pull request o/r#9 is not mergeable: the base branch policy prohibits the merge.")
    number = number_of(args[2])
    moved = base / ("pr-" + number + "-base-moved")
    if moved.exists() and int(moved.read_text()) > 0:
        moved.write_text(str(int(moved.read_text()) - 1))
        replace_head(number, "head-replaced-on-refusal")
        refuse("GraphQL: Base branch was modified. Review and try the merge again. (mergePullRequest)")
    table = prs()
    pr = table.get(number)
    head = head_of(pr) if pr and pr["state"] == "OPEN" else ""
    if not head:
        refuse("GraphQL: Pull Request is not mergeable (mergePullRequest)")
    if (base / "outside-commit-before-merge").exists():
        (base / "outside-commit-before-merge").unlink()
        outside_commit()
    before = git("rev-parse", "refs/heads/main").stdout.strip()
    pr.update(state="MERGED", oid=head, merge=merge(number, pr, head),
              lag=1 if (base / "merge-state-lags").exists() else 0)
    save(table)
    if (base / "trunk-rewound-after-merge").exists():
        git("update-ref", "refs/heads/main", before)
    if (base / "crash-after-merge").exists():
        (base / "crash-after-merge").unlink()
        os.kill(os.getppid(), signal.SIGKILL)
elif args[:2] == ["pr", "view"] and any("statusCheckRollup" in arg for arg in args):
    if (base / "checks-query-fails").exists():
        refuse("API unavailable")
    number = number_of(args[2])
    pr = prs().get(number)
    own = base / ("pr-" + number + "-checks")
    if (base / "pr-checks.json").exists() and not own.exists():
        payload = json.loads((base / "pr-checks.json").read_text())
    else:
        kind = (base / "checks").read_text().strip() if (base / "checks").exists() else ""
        if own.exists():
            kind, _, posted_for = own.read_text().strip().partition(" ")
            if pr is None or posted_for != head_of(pr):
                kind = ""
        review = "REVIEW_REQUIRED" if (base / "merge-refused").exists() else "APPROVED"
        rollup = {
            "pending": [{"name": "test", "status": "IN_PROGRESS"}],
            "failed": [{"name": "test (3.12)", "status": "COMPLETED", "conclusion": "FAILURE"}],
            "passed": [
                {"name": "test (3.10)", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"name": "test (3.12)", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        }.get(kind, [])
        payload = {"reviewDecision": review, "statusCheckRollup": rollup}
    if "autoMergeRequest" not in payload:
        marker = base / "auto-merge-request.json"
        payload["autoMergeRequest"] = json.loads(marker.read_text()) if marker.exists() else None
    if "headRefOid" not in payload:
        payload["headRefOid"] = head_of(pr) if pr else ""
    jq(args[args.index("-q") + 1], payload)
    replace_head(number, "head-replaced-after-check-read")
    flip = base / "checks-flip"
    if flip.exists():
        (base / "checks").write_text(flip.read_text())
        flip.unlink()
        saved = base / "pr-checks.json"
        if saved.exists():
            saved.unlink()
elif args[:2] == ["pr", "view"] and "--json" in args and args[args.index("--json") + 1] == "headRefName":
    pr = prs().get(number_of(args[2]))
    created = (base / "pr-head").read_text().strip() if (base / "pr-head").exists() else ""
    print(pr["head"] if pr else created or "landing/e1")
elif args[:2] == ["pr", "view"] and "--json" in args and "url" in args[args.index("--json") + 1].split(","):
    fields = args[args.index("--json") + 1].split(",")
    target = args[2] if len(args) > 2 and not args[2].startswith("-") else ""
    table = prs()
    on_branch = [number for number, pr in table.items() if pr["head"] == target]
    state = "OPEN"
    head_oid = ""
    if on_branch:
        number = max(on_branch, key=int)
        url = "https://github.com/o/r/pull/" + number
        state = table[number]["state"]
        head_oid = head_of(table[number])
    elif (base / "pr-url").exists():
        url = (base / "pr-url").read_text().strip()
    else:
        sys.exit(1)
    prior = None
    if target.startswith("landing/q"):
        prior = base / "legacy-pr-state"
    elif target.startswith("landing/e"):
        prior = base / "entry-pr-state"
    if prior is not None and prior.exists():
        parts = prior.read_text().strip().split()
        if parts:
            state = parts[0] or "OPEN"
        if len(parts) > 1:
            head_oid = parts[1]
    doc = {"url": url} if "url" in fields else {}
    if "state" in fields:
        doc["state"] = state
    if "headRefOid" in fields:
        doc["headRefOid"] = head_oid
    if "-q" not in args:
        print(url)
    else:
        jq(args[args.index("-q") + 1], doc)
elif args[:2] == ["pr", "view"]:
    if (base / "state-query-fails").exists():
        refuse("API unavailable")
    table = prs()
    pr = table.get(number_of(args[2]))
    if (base / "pr-state").exists():
        print((base / "pr-state").read_text())
    elif pr is None:
        print("OPEN")
    elif pr["state"] == "MERGED" and not pr["lag"]:
        print("MERGED " + pr["oid"] + " " + pr["merge"])
    else:
        print("OPEN " + head_of(pr) + " ")
        if pr["state"] == "MERGED":
            pr["lag"] -= 1
            save(table)
    nxt = base / "pr-state-after"
    if nxt.exists():
        (base / "pr-state").write_text(nxt.read_text())
        nxt.unlink()
""".replace("PYTHON", sys.executable, 1).replace("BASE", repr(str(self.base)), 1))
        fake.chmod(0o755)
        return mock.patch.dict(os.environ, {"LAND_GH": str(fake)})

    def pause_repo_view(self):
        """Block gh repo view until repo-view-release appears. The waiting file is the barrier."""
        real = os.environ["LAND_GH"]
        wrapper = self.base / "gh-pause-view"
        wrapper.write_text(
            f"""#!{sys.executable}
import os, sys, time
from pathlib import Path
base = Path({str(self.base)!r})
real = {real!r}
args = sys.argv[1:]
if args[:2] == ["repo", "view"] and (base / "pause-repo-view").exists():
    (base / "repo-view-waiting").touch()
    end = time.monotonic() + 30
    while not (base / "repo-view-release").exists() and time.monotonic() < end:
        time.sleep(0.01)
os.execv(real, [real, *args])
"""
        )
        wrapper.chmod(0o755)
        os.environ["LAND_GH"] = str(wrapper)

    def allow_methods(self, merge=False, squash=False, rebase=False):
        (self.base / "repo-merge.json").write_text(json.dumps({
            "mergeCommitAllowed": merge,
            "squashMergeAllowed": squash,
            "rebaseMergeAllowed": rebase,
        }))

    def stored_merge_method(self):
        with land.Store.for_repo(self.work) as store:
            return store.contract["mergeMethod"]

    def arm_auto_merge(self):
        (self.base / "auto-merge-request.json").write_text(
            '{"authorEmail":null,"commitBody":null,"commitHeadline":null,'
            '"mergeMethod":"SQUASH","enabledAt":"2026-10-05T02:54:42Z",'
            '"enabledBy":{"login":"octocat","id":"U_1","name":"Octo Cat"}}'
        )

    def queue_one(self, path="a.txt", text="agent\n", name="w1", holder="r/D1"):
        sha = self.worker(name, {path: text})
        lease = self.land("lease", "claim", "--holder", holder, "--paths", path)
        return self.land("submit", "--holder", holder, "--branch", name, "--sha", sha, "--lease", lease, "--reviewer", REVIEWER)

    def queue_entries(self, count):
        edits = [("a.txt", "one\n"), ("b.txt", "two\n"), ("lib/x.py", "three\n")]
        for number, (path, text) in enumerate(edits[:count], 1):
            self.assertEqual(self.queue_one(path=path, text=text, name=f"w{number}", holder=f"r/D{number}"), f"E{number}")

    def on_origin(self, *args):
        return sh("git", *args, cwd=self.base / "origin.git")

    def origin_files(self, ref):
        return [self.on_origin("show", f"{ref}:{path}") for path in ("a.txt", "b.txt", "lib/x.py")]

    def post_check(self, number, kind):
        branch = json.loads((self.base / "prs.json").read_text())[str(number)]["head"]
        (self.base / f"pr-{number}-checks").write_text(f"{kind} {self.on_origin('rev-parse', branch)}")

    def merge_calls(self):
        path = self.base / "merge-calls"
        return path.read_text().splitlines() if path.exists() else []

    def pinned(self, number, *flags):
        """The merge request for PR <number> with these flags, pinned to the candidate its entry stores."""
        candidate = self.entry_fields(number - 8, "candidate")[0]
        return f"pr merge https://github.com/o/r/pull/{number} {' '.join(flags)} --match-head-commit {candidate}"

    def unreviewed_head(self):
        """A commit on top of landing/e1 that no entry submitted, present on origin under another branch."""
        rogue = self.worker("rogue", {"rogue.txt": "UNREVIEWED\n"}, base=self.on_origin("rev-parse", "landing/e1"))
        sh("git", "push", "-q", "origin", f"{rogue}:refs/heads/rogue", cwd=self.work)
        return rogue

    def head_changed_pause(self, rogue, candidate):
        return (f"GitHub refused to merge https://github.com/o/r/pull/9, and its head is now {rogue[:12]}, "
                f"not the checked candidate {candidate[:12]}. E1 was not merged. Close that PR to bounce E1, "
                f"or push {candidate[:12]} back to that PR's branch. Then run land.py resume and land.py land")

    def unread_commit_pause(self, commit):
        return ("The merge of E1 could not be checked. "
                f"Git could not read commit {commit} even after asking origin for it, "
                "so the tree of the merge commit was not compared with the tree of the candidate. "
                "E1 stays awaiting merge. Check that origin serves that commit, then run land.py resume and land.py land")

    @contextlib.contextmanager
    def git_fails_for_the_merge_commit_of_pr_9(self, names, code=1):
        """Once the fake gh has merged PR 9, exit <code> from every git command with an argument <names> accepts.

        <names> is an expression over arg and merged, the merge commit."""
        bindir = self.base / "unreadable-bin"
        bindir.mkdir()
        real = shutil.which("git")
        (bindir / "git").write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            f"table = Path({str(self.base / 'prs.json')!r})\n"
            "merged = json.loads(table.read_text()).get('9', {}).get('merge', '') if table.exists() else ''\n"
            f"if merged and any({names} for arg in sys.argv[1:]):\n"
            f"    sys.exit({code})\n"
            f"os.execv({real!r}, [{real!r}, *sys.argv[1:]])\n")
        (bindir / "git").chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ["PATH"]}):
            yield

    def open_line(self, count):
        self.init(mode="merge", merge_method="squash")
        self.queue_entries(count)
        self.land("land")

    def killed_land(self):
        result = real_process(
            ProcessReason.KILLED_CALLER, SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stdout + result.stderr)

    @contextlib.contextmanager
    def land_dies_after_its_push_to(self, branch):
        bindir = self.base / "kill-bin"
        bindir.mkdir()
        real = shutil.which("git")
        marker = bindir / "armed"
        marker.write_text("")
        (bindir / "git").write_text(
            "#!/bin/sh\n"
            f"if [ \"$1\" = push ] && [ -e '{marker}' ]; then\n"
            f"  case \"$*\" in *:refs/heads/{branch}) '{real}' \"$@\"; rm '{marker}'; kill -9 $PPID; exit 0;; esac\n"
            "fi\n"
            f"exec '{real}' \"$@\"\n")
        (bindir / "git").chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": str(bindir) + os.pathsep + os.environ["PATH"]}):
            yield

    def entry_fields(self, ident, *names):
        with self.raw_db() as db:
            return db.execute(f"SELECT {', '.join(names)} FROM entry WHERE id = ?", (ident,)).fetchone()

    def entry_table_without_onto(self, candidates):
        with self.raw_db() as db:
            newer = db.execute("SELECT sql FROM sqlite_master WHERE name = 'entry'").fetchone()[0]
            older = newer.replace(", onto TEXT NOT NULL DEFAULT ''", "")
            self.assertNotEqual(older, newer)
            kept = ", ".join(name for name in self.entry_columns() if name != "onto")
            db.execute("ALTER TABLE entry RENAME TO entry_newer")
            db.execute(older)
            db.execute(f"INSERT INTO entry ({kept}) SELECT {kept} FROM entry_newer")
            db.execute("DROP TABLE entry_newer")
            for ident, candidate in candidates.items():
                db.execute("UPDATE entry SET candidate = ? WHERE id = ?", (candidate, ident))
            db.commit()

    def fail_state_reads_after_the_first(self):
        """The next PR state read answers. Every one after it fails with API unavailable."""
        real = os.environ["LAND_GH"]
        wrapper = self.base / "gh-state-fails"
        wrapper.write_text(
            f"""#!{sys.executable}
import os, sys
from pathlib import Path
base = Path({str(self.base)!r})
args = sys.argv[1:]
if args[:2] == ["pr", "view"] and "state,headRefOid,mergeCommit" in args:
    if (base / "state-read-answered").exists():
        (base / "state-query-fails").touch()
    (base / "state-read-answered").touch()
os.execv({real!r}, [{real!r}, *args])
"""
        )
        wrapper.chmod(0o755)
        os.environ["LAND_GH"] = str(wrapper)

    def gh(self, *args):
        result = subprocess.run([os.environ["LAND_GH"], *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def base_land_script(self):
        """Pre-rename land.py. The fixture replaces git show, which fails in a shallow clone."""
        path = self.base / "land-3678d11.py"
        source = ROOT / "tests/fixtures/land_before_e_rename.py"
        path.write_bytes(source.read_bytes())
        return path

    def use_land_script(self, path):
        global SCRIPT
        previous = SCRIPT
        SCRIPT = path
        return previous

    def restore_land_script(self, previous):
        global SCRIPT
        SCRIPT = previous

    def require_pr_head_on_origin(self):
        """GitHub rejects pr create when the head branch is not on the remote."""
        real = os.environ["LAND_GH"]
        wrapper = self.base / "gh-head"
        wrapper.write_text(
            f"""#!{sys.executable}
import os, subprocess, sys
from pathlib import Path
base = Path({str(self.base)!r})
real = {real!r}
args = sys.argv[1:]
if args[:2] == ["pr", "create"]:
    if (base / "pr-create-fails").exists():
        print("gh: not authenticated", file=sys.stderr)
        sys.exit(1)
    head = args[args.index("--head") + 1]
    present = subprocess.run(
        ["git", "--git-dir", str(base / "origin.git"), "rev-parse", "--verify", "--quiet", "refs/heads/" + head],
        capture_output=True).returncode == 0
    if not present:
        print("pull request create failed: GraphQL: Head ref must be a branch (createPullRequest)", file=sys.stderr)
        sys.exit(1)
os.execv(real, [real, *args])
"""
        )
        wrapper.chmod(0o755)
        os.environ["LAND_GH"] = str(wrapper)

    def record_created_pr_branch(self):
        """A PR is visible to `gh pr view` only for the head `pr create` used."""
        real = os.environ["LAND_GH"]
        wrapper = self.base / "gh-branch"
        wrapper.write_text(
            f"""#!{sys.executable}
import os, sys
from pathlib import Path
base = Path({str(self.base)!r})
args = sys.argv[1:]
if args[:2] == ["pr", "create"] and "--head" in args:
    (base / "pr-head").write_text(args[args.index("--head") + 1])
def asks_url(argv):
    if "--json" not in argv:
        return False
    return "url" in argv[argv.index("--json") + 1].split(",")
if args[:2] == ["pr", "view"] and asks_url(args) and len(args) > 2 and args[2].startswith("landing/"):
    recorded = (base / "pr-head").read_text().strip() if (base / "pr-head").exists() else ""
    if recorded != args[2]:
        sys.exit(1)
os.execv({real!r}, [{real!r}, *args])
"""
        )
        wrapper.chmod(0o755)
        os.environ["LAND_GH"] = str(wrapper)

    def created_heads(self):
        calls = (self.base / "gh-calls").read_text().splitlines()
        return [line.split("--head ", 1)[1].split(" ", 1)[0]
                for line in calls if line.startswith("pr create ")]

    def crashed_landing_q_pr(self):
        """An older land.py opened the PR on landing/q<n> and died before storing the URL."""
        self.record_created_pr_branch()
        self.require_pr_head_on_origin()
        previous = self.use_land_script(self.base_land_script())
        try:
            self.init(mode="human")
            self.queue_one()
            (self.base / "crash-on-create").write_text("")
            crashed = real_process(
                ProcessReason.KILLED_CALLER, SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
            self.assertEqual(crashed.returncode, -9, crashed.stdout + crashed.stderr)
            self.restore_land_script(previous)
            previous = None
        finally:
            if previous is not None:
                self.restore_land_script(previous)
        return sh("git", "rev-parse", "refs/heads/landing/q1", cwd=self.base / "origin.git")

    def pr_views_of_landing_q(self):
        calls = (self.base / "gh-calls").read_text().splitlines()
        return [line for line in calls if line.startswith("pr view landing/q")]

    def ignore_non_open_pr_on_landing_q(self, state):
        """The queue opens its own pull request whatever state the one on landing/q<n> is in."""
        with self.fake_gh():
            (self.base / "pr-seq").write_text("9\n")
            candidate = self.crashed_landing_q_pr()
            (self.base / "legacy-pr-state").write_text(f"{state} 0123456789abcdef0123456789abcdef01234567\n")
            reported = self.land("land")
        self.assertEqual(self.created_heads(), ["landing/q1", "landing/e1"], reported)
        self.assertEqual(self.pr_views_of_landing_q(), [])
        remote = self.base / "origin.git"
        self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/e1", cwd=remote), candidate)
        self.assertNotIn("adopted", reported)
        status = self.land("status", "E1")
        self.assertIn("awaiting-merge", status)
        self.assertIn("https://github.com/o/r/pull/10", status)
        self.assertNotIn("https://github.com/o/r/pull/9", status)

    def open_pr_on_landing_q_with_the_url_stored(self, remote_is_github):
        """An older queue opened the PR on landing/q1 and stored its URL. landing/e1 was never created."""
        if not remote_is_github:
            sh("git", "remote", "set-url", "origin", str(self.base / "origin.git"), cwd=self.work)
        previous = self.use_land_script(self.base_land_script())
        try:
            self.init(mode="human")
            self.queue_one()
            opened = self.land("land")
        finally:
            self.restore_land_script(previous)
        remote = self.base / "origin.git"
        self.assertIn("https://github.com/o/r/pull/9", opened)
        self.assertEqual((self.base / "pr-head").read_text().strip(), "landing/q1")
        self.assertFalse(self.ref_exists("refs/heads/landing/e1", remote))
        return sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote)

    def hide_a_landing_e1_the_older_queue_never_made(self, candidate):
        remote = self.base / "origin.git"
        sh("git", "update-ref", "refs/heads/landing/e1", candidate, cwd=remote)
        sh("git", "config", "receive.hideRefs", "refs/heads/landing/e1", cwd=remote)
        (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
        return remote

    def leave_awaiting_merge_without_a_pr(self):
        """Push landing/e<n>, mark the entry awaiting merge, and die before gh pr create."""
        with land.Store.for_repo(self.work) as store:
            with mock.patch.object(land, "ensure_pr", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    land.land(store)
        status = self.land_in_another_process("status", "E1")
        self.assertIn("awaiting-merge", status)
        self.assertNotIn("pull/", status)
        self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def absent_marker(self):
        with land.Store.for_repo(self.work) as store:
            return store.contract.get("absentDrain") or {}

    def pauses_on_a_merged_pr_whose_state_read_reports_the_merge_commit_as(self, reported):
        with self.fake_gh():
            self.open_line(1)
            candidate = self.on_origin("rev-parse", "landing/e1")
            (self.base / "pr-state").write_text(f"MERGED {candidate} {reported}")
            self.assertEqual(
                self.land("land"),
                "queue paused: The merge of E1 could not be checked. "
                "The state read of https://github.com/o/r/pull/9 gave no full commit id for the merge commit, "
                "so the tree of the merge commit was not compared with the tree of the candidate. "
                "E1 stays awaiting merge. Check what gh pr view https://github.com/o/r/pull/9 --json mergeCommit prints, "
                "then run land.py resume and land.py land")
            self.assertEqual(self.land("status", "E1").split(". ")[0], "E1 awaiting-merge (r/D1, w1)")
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)

    def off_trunk_pause(self, merged, trunk):
        return (f"https://github.com/o/r/pull/9 is recorded as merged at {merged[:12]}, and trunk at {trunk[:12]} "
                "does not hold that commit. E1 stays awaiting merge. Check what happened to trunk. "
                "Once trunk holds that commit again, run land.py resume and land.py land")

    def pauses_on_a_merge_of_the_first_of_two_entries_that_trunk_was_rewound_past(self, outside):
        """PR 9 merges outside the queue and trunk is put back to init before land runs.

        With <outside>, trunk gains another commit just before that merge, so the merge commit holds another tree
        than the candidate. Returns the candidate and the merge commit, with the merge commit back on trunk and
        the queue resumed."""
        self.open_line(2)
        self.post_check(9, "passed")
        self.post_check(10, "passed")
        initial, candidate = self.on_origin("rev-parse", "main"), self.on_origin("rev-parse", "landing/e1")
        if outside:
            (self.base / "outside-commit-before-merge").write_text("")
        self.gh("pr", "merge", "https://github.com/o/r/pull/9", "--squash", "--match-head-commit", candidate)
        merged = self.on_origin("rev-parse", "main")
        self.assertEqual(self.on_origin("rev-parse", f"{merged}^{{tree}}") != self.on_origin("rev-parse", f"{candidate}^{{tree}}"),
                         outside)
        self.on_origin("update-ref", "refs/heads/main", initial)
        self.assertFalse(self.ref_exists(f"{merged}^{{commit}}", self.work))
        pause = self.off_trunk_pause(merged, initial)
        for _run in range(2):
            self.assertEqual(self.land("land"), f"queue paused: {pause}")
            self.assertEqual(self.origin_log(), ["init"])
            self.assertTrue(self.ref_exists(f"{merged}^{{commit}}", self.work))
            self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. "
                                                        "waiting for required checks before merging")
            self.assertEqual(self.listed(), ["L1 submitted r/D1: a.txt", "L2 submitted r/D2: b.txt"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)
            self.assertEqual(self.land("resume"), "queue resumed")
        self.on_origin("update-ref", "refs/heads/main", merged)
        return candidate, merged

    def commit_on_origin(self, message, path="merged.txt"):
        clone = self.base / "merger"
        if not clone.exists():
            sh("git", "clone", "-q", "origin.git", "merger", cwd=self.base)
        sh("git", "pull", "-q", "origin", "main", cwd=clone)
        (clone / path).write_text(message)
        sha = self.commit(message, cwd=clone)
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=clone)
        return sha

    def create_args_without_a_submitted_body(self, *message):
        """Land a worker commit with this message and no --body-file. Returns the gh pr create arguments."""
        log = self.base / "gh-log"
        with self.fake_gh():
            fake = self.base / "gh"
            fake.write_text(fake.read_text().replace('if args[:2] == ["pr", "create"]:',
                                                     f'open({str(log)!r}, "a").write(repr(args) + "\\n")\nif args[:2] == ["pr", "create"]:'))
            self.init(mode="human")
            self.worker("w1", {"a.txt": "agent\n"})
            sh("git", "commit", "-q", "--amend", *message, cwd=self.base / "w1")
            sha = sh("git", "rev-parse", "HEAD", cwd=self.base / "w1")
            self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
            self.land("land")
        return ast.literal_eval([line for line in log.read_text().splitlines() if "'create'" in line][0])

    def store_item(self, branch, kind, goal):
        store = self.base / "state/pstack-t3/brigade/proj/store"
        store.mkdir(parents=True, exist_ok=True)
        (store / "restaurant.json").write_text(json.dumps({"projectRoot": str(self.work)}))
        (store / "goals.tsv").write_text("id\tname\nG2\trules in the tool\n")
        (store / "dishes.tsv").write_text(
            "id\tat\tstate\tstation\ttickets\ttask\tthread\tbranch\tpr\tsha\tsummary\ttimebox\tlease\tpaths\t"
            f"reported\tkind\tgoal\nD255\t2026-10-14T13:29:20.318522+00:00\tin-progress\tbug-fix\tT606\t\t\t{branch}\t\t\t"
            f"batch\t90\tL293\t\t\t{kind}\t{goal}\n")

    def entry_columns(self):
        with self.raw_db() as db:
            return [row[1] for row in db.execute("PRAGMA table_info(entry)")]

    def assert_slot_log(self, callers, kind_of):
        """Return the most workers running at once.

        No two intervals on one slot file overlap, and an exclusive interval overlaps no other caller.
        """
        running, spans, slot_of, peak = set(), {}, {}, 0
        for position, fields in enumerate(callers.events()):
            word, name, *files = fields
            self.assertTrue(files, f"{name} recorded no slot file")
            if word == "enter":
                running.add(name)
                spans[name] = [position, None]
                slot_of[name] = tuple(files)
            else:
                running.remove(name)
                spans[name][1] = position
                self.assertEqual(tuple(files), slot_of[name], name)
            peak = max(peak, sum(kind_of[caller] == "worker" for caller in running))
        self.assertEqual(sorted(spans), sorted(kind_of))

        def overlaps(left, right):
            return left[0] < right[1] and right[0] < left[1]

        by_slot = {}
        for name, files in slot_of.items():
            for slot_file in files:
                by_slot.setdefault(slot_file, []).append(name)
        for slot_file, names in by_slot.items():
            for index, name in enumerate(names):
                for other in names[index + 1:]:
                    self.assertFalse(overlaps(spans[name], spans[other]), f"{name} and {other} overlapped on {slot_file}")
        for name, span in spans.items():
            if kind_of[name] != "exclusive":
                continue
            for other, other_span in spans.items():
                if other != name:
                    self.assertFalse(overlaps(span, other_span), f"{name} overlapped {other}")
        return peak

    def full_slots(self, slots):
        """Start callers that hold every worker slot and the landing slot. Returns the callers, the holders, and each kind."""
        callers = SlotCallers(self, slots)
        kind_of = {f"holder-{index}": "worker" for index in range(slots)}
        holders = [callers.enter(callers.start(name)) for name in kind_of]
        kind_of["holder-landing"] = "landing"
        holders.append(callers.enter(callers.start("holder-landing", pool="landing")))
        return callers, holders, kind_of

    def contend(self, slots, kinds, hold):
        """Queue one free-running caller per kind, in order, behind full slots, then free the slots.

        Each caller holds its slot for hold seconds. The slot log names the slot file each caller holds.
        Returns the most worker slots in use at once.
        """
        callers, holders, kind_of = self.full_slots(slots)
        for index, kind in enumerate(kinds):
            name = f"{index}-{kind}"
            kind_of[name] = kind
            flags = ["--exclusive"] if kind == "exclusive" else []
            pool = "landing" if kind == "landing" else "worker"
            callers.queued(callers.start(name, *flags, marked=True, pool=pool, hold=hold))
        for holder in holders:
            callers.release(holder)
        for caller in callers.callers:
            self.assertEqual(caller.process.wait(timeout=120), 0, caller.name)
        return self.assert_slot_log(callers, kind_of)

    def admit_latest_first(self, slots, kinds):
        """Park one stepped caller per kind behind full slots, free the slots, then let the latest parked caller try first.

        A caller may start before an earlier parked caller only when that earlier caller could not take a slot.
        Waiters sit on their retry fifo while a slot is released, so a release cannot land between the admission
        check and the take.
        """
        def can_take(kind, busy):
            if kind == "exclusive":
                return not busy
            if kind == "landing":
                return "landing" not in busy and "exclusive" not in busy
            return "exclusive" not in busy and busy.count("worker") < slots

        callers, holders, kind_of = self.full_slots(slots)
        queue = []
        for index, kind in enumerate(kinds):
            name = f"{index}-{kind}"
            kind_of[name] = kind
            flags = ["--exclusive"] if kind == "exclusive" else []
            pool = "landing" if kind == "landing" else "worker"
            caller = callers.start(name, *flags, stepped=True, pool=pool)
            self.assertEqual(callers.settle(caller), "waiting", name)
            queue.append(caller)
        for holder in holders:
            callers.release(holder)

        pending = list(queue)
        running = []
        while pending:
            started = None
            for caller in reversed(pending):
                if callers.step(caller) == "started":
                    started = caller
                    break
            if started is None:
                self.assertTrue(running, "nobody holds a slot and nobody could take one")
                callers.release(running.pop(0))
                continue
            busy = [kind_of[caller.name] for caller in running]
            for other in pending:
                if queue.index(other) >= queue.index(started):
                    continue
                if not land._conflicts(kind_of[other.name], kind_of[started.name]):
                    continue
                self.assertFalse(can_take(kind_of[other.name], busy),
                                 f"{started.name} entered before {other.name} while {other.name} could take a slot, running {busy}")
            pending.remove(started)
            running.append(started)
        for caller in running:
            callers.release(caller)
        self.assert_slot_log(callers, kind_of)

    def ofd_locks_exist(self):
        """True when this platform accepts an OFD byte lock."""
        import struct
        path = self.base / "ofd-probe"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.fcntl(descriptor, 37, struct.pack("hhqqi4x", fcntl.F_WRLCK, os.SEEK_SET, 0, 1, 0))
        except OSError:
            return False
        else:
            return True
        finally:
            os.close(descriptor)
            os.unlink(path)

    def governor(self, slots=1):
        directory = self.base / "state/pstack-t3/governor"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "governor.json").write_text(json.dumps({"slots": slots}))
        return directory

    def slot_env(self):
        return {key: value for key, value in os.environ.items() if key != "LAND_SLOT"}

    @contextlib.contextmanager
    def no_slot(self):
        saved = os.environ.pop("LAND_SLOT", None)
        try:
            yield
        finally:
            if saved is not None:
                os.environ["LAND_SLOT"] = saved

    def start_holder(self, kind="worker", repo=None, command="make test", slots=1):
        directory = self.base / f"holder-{kind}"
        directory.mkdir()
        ready, release = directory / "ready", directory / "release"
        os.mkfifo(release)
        process = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, "-c", HOLD_SCRIPT, str(SCRIPT.parent), kind, repo or str(self.work), command,
             str(ready), str(release)],
            env=self.slot_env(), cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop():
            if process.poll() is None:
                with open(release, "w") as handle:
                    handle.write("x")
            process.wait(timeout=20)
        self.addCleanup(stop)
        deadline = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(ready.exists(), f"{kind} holder did not take its slot")
        return process

    def exclusive_args(self, argv, cap=None, grace=None, cap_max=None, main=EXCLUSIVE_MAIN):
        return [sys.executable, "-c", main, str(SCRIPT.parent),
                "" if cap is None else str(cap),
                "" if grace is None else str(grace),
                "" if cap_max is None else str(cap_max),
                *argv]

    def run_exclusive(self, argv, cap=None, grace=None, cap_max=None, timeout=20, main=EXCLUSIVE_MAIN):
        return _run(
            self.exclusive_args(argv, cap, grace, cap_max, main),
            env=self.slot_env(), cwd=self.base, timeout=timeout, reason=ProcessReason.GOVERNOR)

    def popen_exclusive(self, argv, cap=None, grace=None, cap_max=None, main=EXCLUSIVE_MAIN):
        return real_popen(
            ProcessReason.GOVERNOR, self.exclusive_args(argv, cap, grace, cap_max, main),
            env=self.slot_env(), cwd=self.base, start_new_session=True)

    def assert_slots_free(self):
        directory = self.base / "state/pstack-t3/governor"
        for name in ("landing-0", "worker-0"):
            descriptor = os.open(directory / name, os.O_RDWR)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)
        holds = directory / "holds"
        self.assertFalse(holds.exists() and any(holds.iterdir()))

    def assert_dead(self, pid):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        self.fail(f"pid {pid} is still running")

    def assert_live(self, pid):
        # A zombie answers os.kill(pid, 0) too, so the member's state read is the liveness witness.
        state = land._ps_line(pid, "stat=")
        self.assertIsNotNone(state, f"ps could not read pid {pid}")
        self.assertFalse(state.strip().startswith("Z"), f"pid {pid} is a zombie")

    def assert_worker_holder_keeps_the_slot(self, holder):
        self.assertIsNone(holder.poll())
        record = json.loads((land.governor_dir() / "holds" / "worker-0").read_text())
        self.assertEqual(record["pid"], holder.pid)
        self.assertEqual(record["command"], "make test")
        descriptor = os.open(land.governor_dir() / "worker-0", os.O_RDWR)
        try:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)

    def wait_for_line(self, path, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists() and "\n" in path.read_text():
                return path.read_text().strip()
            time.sleep(0.02)
        self.fail(f"no report line in {path}")

    def run_waiter(self, script, release, repo=None, command="npm run build", after="0.2", every="60", kind="worker"):
        out, err = self.base / "waiter.out", self.base / "waiter.err"
        handles = [open(out, "w"), open(err, "w")]
        process = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, "-c", script, str(SCRIPT.parent), after, every, kind, repo or str(self.work), command],
            env=self.slot_env(), cwd=self.base, start_new_session=True,
            stdout=handles[0], stderr=handles[1], text=False)
        line = self.wait_for_line(err)
        if release is not None:
            os.write(release, b"x")
        code = process.wait(timeout=20)
        for handle in handles:
            handle.close()
        return code, out.read_text(), err.read_text(), line

    def release_recorded_group(self, *paths):
        pids = []
        for path in paths:
            if not path.exists():
                continue
            for part in path.read_text().split():
                try:
                    pids.append(int(part))
                except ValueError:
                    continue
        if not pids:
            return
        try:
            os.killpg(pids[0], signal.SIGKILL)
        except ProcessLookupError:
            pass
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def signal_exclusive_ignoring_child(self, signum):
        parent_path = self.base / f"signal-{signum}-parent.pid"
        child_path = self.base / f"signal-{signum}-child.pid"
        ready_path = self.base / f"signal-{signum}-ready"
        try:
            ready_path.unlink()
        except OSError:
            pass
        with mock.patch.dict(os.environ, {"LAND_EXCLUSIVE_READY": str(ready_path)}):
            process = self.popen_exclusive(
                ["slot", "--exclusive", "--", sys.executable, "-c", IGNORING_GROUP,
                 str(parent_path), str(child_path)],
                cap=30, grace=0.2, main=READY_MAIN)
            try:
                child_pid, child_group = (int(part) for part in self.wait_for_line(child_path).split())
                self.assertEqual(child_group, int(parent_path.read_text()))
                deadline = time.monotonic() + 20
                while not ready_path.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(ready_path.exists(), "the ready marker was not written before the signal")
                os.kill(process.pid, signum)
                code = process.wait(timeout=30)
                self.assert_dead(child_pid)
                self.assert_slots_free()
                stderr = process.stderr.read()
                return code, stderr
            finally:
                if process.poll() is None:
                    os.kill(process.pid, signal.SIGKILL)
                    process.wait(timeout=30)
                process.stdout.close()
                process.stderr.close()
                self.release_recorded_group(parent_path, child_path)

    def _worker_waiter_leaves_the_landing_slot_unlocked(self):
        directory = self.base / "reporter"
        directory.mkdir()
        marker, release = directory / "probe", directory / "release"
        os.mkfifo(release)
        gate = os.open(release, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, gate)
        err = directory / "err"
        err_handle = open(err, "w")
        reporter = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, "-c", REPORT_SCRIPT, str(SCRIPT.parent), str(marker), str(release), "landing-0",
             "0.2", "60", str(self.work), "make test"],
            env=self.slot_env(), cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=err_handle, text=False)

        def stop_reporter():
            if reporter.poll() is None:
                os.killpg(reporter.pid, signal.SIGKILL)
            reporter.wait(timeout=20)
            err_handle.close()
        self.addCleanup(stop_reporter)

        deadline = time.monotonic() + 5
        line = ""
        while time.monotonic() < deadline:
            if marker.exists():
                self.fail("the worker waiter locked the landing slot while reporting")
            if err.exists() and "\n" in err.read_text():
                line = err.read_text().strip().splitlines()[0]
                break
            time.sleep(0.02)
        else:
            self.fail("the worker waiter printed no report")
        self.assertRegex(line, rf"^land: waited \d+s for a slot; worker {re.escape(str(self.work))} "
                                rf"for \d+s: worker-command$")
        self.assertFalse(marker.exists())

        old = self.base / "old/land.py"
        old.parent.mkdir()
        old.write_text(PREVIOUS_SLOT_SCRIPT)
        entered, old_release = directory / "entered", directory / "old-release"
        os.mkfifo(old_release)
        old_gate = os.open(old_release, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, old_gate)
        driver = (
            "import importlib.util, os, sys\n"
            "from pathlib import Path\n"
            "spec = importlib.util.spec_from_file_location('oldland', sys.argv[1])\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "with module.slot('landing'):\n"
            "    Path(sys.argv[2]).touch()\n"
            "    os.read(os.open(sys.argv[3], os.O_RDONLY), 1)\n"
        )
        caller = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, "-c", driver, str(old), str(entered), str(old_release)],
            env=self.slot_env(), cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop_caller():
            if caller.poll() is None:
                try:
                    os.write(old_gate, b"x")
                except OSError:
                    pass
                try:
                    caller.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(caller.pid, signal.SIGKILL)
                    caller.wait(timeout=20)
        self.addCleanup(stop_caller)

        deadline = time.monotonic() + 5
        while not entered.exists() and time.monotonic() < deadline:
            if marker.exists():
                self.fail("the worker waiter locked the landing slot while the old caller waited")
            time.sleep(0.02)
        self.assertTrue(entered.exists(), "the pre-turn-file landing caller did not enter")
        self.assertFalse(marker.exists())

    def temporary_hold(self, pid, locked):
        """Create governor/holds/worker-0.<pid>.tmp, and lock it when locked. Returns its path and descriptor."""
        holds = self.base / "state/pstack-t3/governor/holds"
        holds.mkdir(parents=True, exist_ok=True)
        path = holds / f"worker-0.{pid}.tmp"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o666)
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return path, descriptor

    def dead_pid(self):
        return int(sh(sys.executable, "-c", "import os; print(os.getpid())", cwd=self.base))

    def reserve(self, prefix, paths, ruling, *extra, ok=True):
        return self.land("lease", "reserve", "--for", prefix, "--paths", paths, "--ruling", ruling, *ADMIN, *extra, ok=ok)

    def share(self, prefix, count, *extra, ok=True):
        return self.land("share", "--for", prefix, str(count), *ADMIN, *extra, ok=ok)

    def contest(self, *args, ok=True):
        return self.land("contest", *args, *ADMIN, ok=ok)

    def ruling_rows(self, table):
        with land.Store.for_repo(self.work) as store:
            return [dict(row) for row in store.db.execute(f"SELECT * FROM {table} ORDER BY 1")]

    def reserved_until(self, number):
        return next(row for row in self.ruling_rows("reservation") if row["id"] == number)["expires"][:16]

    def listed(self):
        """lease list with each expiry cut out, so lines compare as literals."""
        return [re.sub(r" until \S+:", ":", line) for line in self.land("lease", "list").splitlines()]

    def concurrent(self, *commands):
        procs = [real_popen(
            ProcessReason.RACE,
            [sys.executable, str(SCRIPT), "--repo", str(self.work), *command],
            env=os.environ.copy())
            for command in commands]
        return [(proc.wait(), *(part.strip() for part in proc.communicate())) for proc in procs]

    def raw_db(self):
        """land.db opened without Store, which upgrades an older schema when it opens one."""
        return contextlib.closing(sqlite3.connect(next((self.base / "state").glob("pstack-t3/landing/*/land.db"))))

    def dumped(self):
        with self.raw_db() as db:
            return "\n".join(db.iterdump())

    def armed_log(self):
        return [row["id"] for row in self.ruling_rows("log") if (row["kind"], row["state"]) == ("reservation", "armed")]

    def check_with_dump_unchanged(self, holder, paths):
        before = self.dumped()
        answer = self.lease_check(holder, paths)
        self.assertEqual(self.dumped(), before)
        return answer

    def assert_reserved_for_two_hours(self, text):
        match = re.fullmatch(r"(?:land: )?paths reserved for docs/ by ruling R4 until (\d{4}-\d\d-\d\dT\d\d:\d\d)", text)
        self.assertIsNotNone(match, text)
        left = datetime.fromisoformat(match[1]).replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
        self.assertLess(abs(left - timedelta(hours=2)), timedelta(minutes=2))

    def reservation_counts_again_after_its_lease_ends(self, end, limit, refused, outsider):
        self.init()
        limit()
        self.assertEqual(self.reserve("docs/", "a.txt,b.txt", "R4"), "S1")
        self.assertEqual(self.claim("docs/D7", "a.txt", *(["--ttl-hours", "0"] if end == "expire" else [])), "L1")
        if end == "release":
            self.assertEqual(self.land("lease", "release", "L1"), "L1 released")
        self.assertEqual(self.listed(), ["S1 armed docs/ by R4: b.txt"])
        self.assertEqual(self.claim(outsider, "lib", ok=False), f"land: {refused}")
        self.assertEqual(self.lease_check(outsider, "lib"), (1, refused))
        self.assertEqual(self.claim("docs/D7", "b.txt"), "L2")
        self.assertEqual(self.listed(), ["L2 active docs/D7: b.txt"])
        self.assertEqual(self.ruling_rows("reservation")[0]["state"], "claimed")

    def contest_holds_both_holders(self, mode, **extra):
        self.init(mode=mode, **extra)
        self.assertEqual(self.queue_one(path="a.txt", name="w1", holder="docs/D7"), "E1")
        self.assertEqual(self.contest("--holders", "docs/D7,engine/D3"), "C1")
        self.assertEqual(self.contest("--holders", "engine/D3,docs/D7"), "C1")
        self.assertEqual(self.queue_one(path="b.txt", name="w2", holder="engine/D3"), "E2")
        self.assertEqual(self.land("land"), "still queued: E1 (held by C1), E2 (held by C1)")
        self.assertEqual(self.land("status", "--holder", "docs/D7").split(" (")[0], "E1 queued")
        self.assertEqual(self.land("status", "--holder", "engine/D3").split(" (")[0], "E2 queued")

    def configure_owner(self, name="Owner", email="owner@example.com"):
        sh("git", "config", "user.name", name, cwd=self.work)
        sh("git", "config", "user.email", email, cwd=self.work)

    def foreign_bounce(self, sha, author="t <t@t>", configured="Owner <owner@example.com>"):
        return (f"bounced E1 (r/D1): commit {sha[:12]} has author {author}, not the configured {configured}; "
                "recommit it as the configured author")

    def land_one_by(self, author, *message, body=None, owner=("Owner", "owner@example.com")):
        """Submit one commit by this author under the owner identity in push mode, then run land. Returns the commit id and what land printed."""
        self.init(mode="push")
        self.configure_owner(*owner)
        self.worker("w1", {"a.txt": "agent\n"})
        sh("git", "commit", "-q", "--amend", "--author", author, *message, cwd=self.base / "w1")
        sha = sh("git", "rev-parse", "HEAD", cwd=self.base / "w1")
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        submitted = []
        if body is not None:
            (self.base / "body.md").write_text(body)
            submitted = ["--body-file", str(self.base / "body.md")]
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER, *submitted)
        return sha, self.land("land")

    def identity_pause(self, now, before="Owner <owner@example.com>"):
        return (f"the configured git identity is now {now} and the queue last ran as {before}; "
                f"check git config --show-origin user.email in {os.path.realpath(self.work)}, then run land.py resume")

    UNSET = "unset (user.name and user.email do not both have a non-empty value)"

    def configured_value(self, key):
        """What git config -z --get prints for key in the checkout, with the final NUL removed. None when git exits 1."""
        result = subprocess.run(["git", "config", "-z", "--get", key], cwd=self.work, capture_output=True, env=git_env())
        return None if result.returncode == 1 else result.stdout.decode().removesuffix("\0")

    def stored_identity(self):
        """The JSON text under the contract key identity, or None when the key is absent."""
        with self.raw_db() as db:
            row = db.execute("SELECT value FROM contract WHERE key = 'identity'").fetchone()
        return row and row[0]

    def queue_one_as(self, name, email, number=1):
        """Queue E<number> for r/D<number> with one commit whose author git builds from this name and email."""
        path, branch = ("a.txt", "b.txt", "lib/x.py")[number - 1], f"w{number}"
        self.worker(branch, {path: "agent\n"})
        subprocess.run(["git", "commit", "-q", "--amend", "--reset-author", "-m", branch], cwd=self.base / branch, check=True,
                       capture_output=True, env={**git_env(), "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email})
        sha = sh("git", "rev-parse", "HEAD", cwd=self.base / branch)
        lease = self.land("lease", "claim", "--holder", f"r/D{number}", "--paths", path)
        self.assertEqual(self.land("submit", "--holder", f"r/D{number}", "--branch", branch, "--sha", sha, "--lease", lease,
                                   "--reviewer", REVIEWER), f"E{number}")

    def queue_rows(self):
        """Every row of every table in the store, by table name. The contract rows are sorted."""
        with self.raw_db() as db:
            tables = [name for name, in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            rows = {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in tables}
        return {**rows, "contract": sorted(rows["contract"])}

    def remember_then_configure(self, mode, before, after):
        """Init in mode, run land with before configured and nothing queued, and configure after.

        Queue E1 with the author git builds from after.
        """
        self.init(mode=mode)
        self.configure_owner(*before)
        self.assertEqual(self.land("land"), "nothing to land")
        self.assertEqual(self.stored_identity(), json.dumps(before))
        self.configure_owner(*after)
        self.assertEqual((self.configured_value("user.name"), self.configured_value("user.email")), after)
        self.queue_one_as(*after)

    def assert_push_mode_pauses(self, before, after, now, last):
        self.remember_then_configure("push", before, after)
        entries, refs = self.queue_rows()["entry"], self.on_origin("for-each-ref")
        self.assertEqual(self.land("land"), "queue paused: " + self.identity_pause(now, last))
        self.assertEqual(self.origin_log(), ["init"])
        self.assertEqual(self.on_origin("for-each-ref"), refs)
        self.assertEqual(tuple(self.entry_fields(1, "state", "pr")), ("queued", ""))
        self.assertEqual(self.queue_rows()["entry"], entries)

    def assert_human_mode_pauses(self, before, after, now, last):
        with self.fake_gh():
            self.remember_then_configure("human", before, after)
            calls = self.base / "gh-calls"
            before_calls = calls.read_text() if calls.exists() else None
            entries, refs = self.queue_rows()["entry"], self.on_origin("for-each-ref")
            self.assertEqual(self.land("land"), "queue paused: " + self.identity_pause(now, last))
            self.assertEqual(calls.read_text() if calls.exists() else None, before_calls)
        self.assertEqual(self.on_origin("for-each-ref"), refs)
        self.assertEqual(tuple(self.entry_fields(1, "state", "pr")), ("queued", ""))
        self.assertEqual(self.queue_rows()["entry"], entries)

    OWNER = ("Owner", "owner@example.com")
    IDENTITY_CHANGES = {
        "name": (OWNER, ("Other", "owner@example.com"), "Other <owner@example.com>", "Owner <owner@example.com>"),
        "email": (OWNER, ("Owner", "other@example.com"), "Owner <other@example.com>", "Owner <owner@example.com>"),
        "name_spaces": (OWNER, (" Owner ", "owner@example.com"), " Owner  <owner@example.com>", "Owner <owner@example.com>"),
        "email_spaces": (OWNER, ("Owner", " owner@example.com "), "Owner < owner@example.com >", "Owner <owner@example.com>"),
        "name_case": (OWNER, ("owner", "owner@example.com"), "owner <owner@example.com>", "Owner <owner@example.com>"),
        "email_case": (OWNER, ("Owner", "Owner@example.com"), "Owner <Owner@example.com>", "Owner <owner@example.com>"),
        "same_joined_text": (("A <b>", "c"), ("A", "b> <c"), "A <b> <c>", "A <b> <c>"),
    }

    def land_one_then_pause_as_audit(self):
        """Push mode. E1 lands as Owner, the identity becomes audit, E2 by audit is queued, and land pauses. Returns the pause reason."""
        self.init(mode="push")
        self.configure_owner()
        self.queue_one_as("Owner", "owner@example.com")
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.configure_owner("audit", "audit@example.com")
        self.queue_one_as("audit", "audit@example.com", number=2)
        reason = self.identity_pause("audit <audit@example.com>")
        self.assertEqual(self.land("land"), f"queue paused: {reason}")
        return reason

    def assert_an_unset_identity_pauses(self, *config):
        """E1 lands as Owner, each git config command in config runs, and E2 by t is queued.

        land must pause, set the paused row, add one log row, and change no other row and no origin ref.
        """
        self.init(mode="push")
        self.configure_owner()
        self.queue_one_as("Owner", "owner@example.com")
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        for command in config:
            sh("git", "config", *command, cwd=self.work)
        self.queue_one_as("t", "t@t", number=2)
        before, refs = self.queue_rows(), self.on_origin("for-each-ref")
        reason = self.identity_pause(self.UNSET)
        self.assertEqual(self.land("land"), f"queue paused: {reason}")
        after = self.queue_rows()
        self.assertEqual(after["log"][:-1], before["log"])
        self.assertEqual(after["log"][-1][1:], ("queue", 0, "paused", reason))
        self.assertEqual(after["contract"], [(key, json.dumps(reason) if key == "paused" else value) for key, value in before["contract"]])
        self.assertEqual({**after, "log": before["log"], "contract": before["contract"]}, before)
        self.assertEqual(self.on_origin("for-each-ref"), refs)

    def assert_resume_then_land_lands_and_stores_no_identity(self):
        self.assertEqual(self.land("resume"), "queue resumed")
        self.assertEqual(self.land("land"), "landed E2 (r/D2)")
        self.assertIsNone(self.stored_identity())

    def stray_worktree_line(self, value, quoted=None, shown=None):
        config = (self.work / ".git").resolve() / "config"
        return (f"land: {shown or config} sets core.worktree to {value}, where git does not find this repository; "
                f"remove it with git --work-tree=. config --file {quoted or config} --unset-all core.worktree\n")

    def run_land(self, repo, *args, cwd=None):
        """Run land.py with --repo set to repo. Returns its exit status, stdout, and stderr."""
        return drive(SCRIPT, ["--repo", str(repo), *args], env=os.environ.copy(), cwd=cwd)

    def files_under(self, directory):
        """The SHA-256 of every file under a directory, by relative path."""
        return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(directory.rglob("*")) if path.is_file()}


def repository_state(repo):
    """Config bytes, HEAD, and every ref of a repository."""
    refs = subprocess.run(["git", "for-each-ref", "--format=%(refname) %(objectname)"], cwd=repo,
                          check=True, capture_output=True, text=True).stdout
    return (repo / ".git/config").read_bytes(), (repo / ".git/HEAD").read_text(), refs


if __name__ == "__main__":
    if sys.argv[1:2] == ["--paused-child"]:
        paused_child(sys.argv[2], sys.argv[3:])
    unittest.main()
