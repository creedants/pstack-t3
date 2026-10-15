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

ROOT = Path(__file__).resolve().parents[1]
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

    def test_a_refused_command_leaves_cwd_environment_and_argv(self):
        self.init()
        cwd = os.getcwd()
        argv = sys.argv
        contents = list(sys.argv)
        before = dict(os.environ)
        widened = dict(before)
        widened["D231_LEAK_MARK"] = "set"
        code, out, err = drive(
            SCRIPT, ["--repo", str(self.work), "status", "E9"], env=widened, cwd=self.base)
        self.assertEqual((code, out, err), (1, "", "land: no E9\n"))
        self.assertEqual(os.getcwd(), cwd)
        self.assertEqual(dict(os.environ), before)
        self.assertIs(sys.argv, argv)
        self.assertEqual(list(sys.argv), contents)

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

    def test_status_refuses_a_q_number(self):
        self.init()
        self.assertEqual(self.queue_one(), "E1")
        self.assertIn("'Q1' is not an entry id such as E3", self.land("status", "Q1", ok=False))
        named = self.land("status", "E1")
        self.assertEqual(self.land("status", "1"), named)
        self.assertTrue(named.startswith("E1 queued"), named)

    def test_leases_refuse_overlap_including_aliases_and_the_whole_repo(self):
        self.init()
        self.assertEqual(self.land("lease", "claim", "--holder", "perf/D1", "--paths", "lib"), "L1")
        self.assertIn("L1 held by perf/D1 on lib", self.land("lease", "claim", "--holder", "bugs/D1", "--paths", "a.txt/../lib/x.py", ok=False))
        self.assertIn("L1 held by perf/D1", self.land("lease", "claim", "--holder", "bugs/D1", "--paths", ".", ok=False))
        self.assertIn("leaves the repository", self.land("lease", "claim", "--holder", "bugs/D1", "--paths", "../etc", ok=False))
        self.assertEqual(self.land("lease", "claim", "--holder", "bugs/D1", "--paths", "lib2,a.txt"), "L2")

    def claim(self, holder, paths, *extra, ok=True):
        return self.land("lease", "claim", "--holder", holder, "--paths", paths, *extra, ok=ok)

    def test_the_cap_refuses_a_claim_naming_the_holders_and_frees_on_release(self):
        self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", "--cap", "2")
        self.assertEqual(self.claim("a/D1", "a.txt"), "L1")
        self.assertEqual(self.claim("b/D1", "b.txt"), "L2")
        self.assertEqual(self.claim("c/D1", "lib", ok=False), "land: repository at its cap: 2 of 2 changes in flight (a/D1, b/D1)")
        self.assertEqual(self.land("lease", "release", "L1"), "L1 released")
        self.assertEqual(self.claim("c/D1", "lib"), "L3")

    def test_the_cap_is_set_and_cleared_on_its_own_and_shown_in_status(self):
        self.init()
        self.assertNotIn("changes in flight", self.land("status"))
        self.assertEqual(self.land("cap", "4"), "repository cap is now 4 changes in flight")
        self.claim("a/D1", "a.txt")
        self.assertEqual(self.land("status"), "push mode onto refs/remotes/origin/main. leases held: 1, changes in flight: 1 of 4.")
        self.assertEqual(self.land("cap", "0"), "repository cap cleared")
        self.assertEqual(self.land("status"), "push mode onto refs/remotes/origin/main. leases held: 1.")
        self.assertIn("negative", self.land("cap", "-1", ok=False))

    def test_ten_concurrent_claims_under_a_cap_of_three_make_exactly_three_leases(self):
        self.init()
        self.land("cap", "3")
        claims = [real_popen(
            ProcessReason.RACE,
            [sys.executable, str(SCRIPT), "--repo", str(self.work), "lease", "claim",
             "--holder", f"r/D{n}", "--paths", f"p{n}.txt"],
            env=os.environ.copy())
            for n in range(10)]
        results = [(claim.wait(), *claim.communicate()) for claim in claims]
        self.assertEqual(sorted(out.strip() for code, out, _ in results if code == 0), ["L1", "L2", "L3"])
        refused = [err for code, _, err in results if code != 0]
        self.assertEqual(len(refused), 7)
        self.assertTrue(all("repository at its cap: 3 of 3 changes in flight" in err for err in refused), refused)
        self.assertEqual(len(self.land("lease", "list").splitlines()), 3)

    def test_a_submitted_lease_counts_toward_the_cap_and_a_bounce_keeps_the_count(self):
        self.init()
        self.land("cap", "2")
        self.assertEqual(self.queue_one(text="BROKEN\n"), "E1")
        self.assertEqual(self.claim("r/D2", "b.txt"), "L2")
        self.assertIn("changes in flight: 2 of 2", self.land("status"))
        self.assertEqual(self.claim("r/D3", "lib", ok=False), "land: repository at its cap: 2 of 2 changes in flight (r/D1, r/D2)")
        self.assertIn("bounced E1 (r/D1)", self.land("land"))
        self.assertIn("changes in flight: 2 of 2", self.land("status"))
        self.assertIn("at its cap", self.claim("r/D3", "lib", ok=False))

    def test_an_expired_lease_does_not_count_toward_the_cap(self):
        self.init()
        self.land("cap", "1")
        self.assertEqual(self.claim("a/D1", "a.txt", "--ttl-hours", "0"), "L1")
        self.assertEqual(self.claim("b/D1", "b.txt"), "L2")

    def test_renewing_an_expired_lease_refuses_an_overlap_another_holder_now_has(self):
        self.init()
        self.claim("docs/D9", "a.txt", "--ttl-hours", "0")
        self.assertEqual(self.claim("engine/D9", "a.txt"), "L2")
        self.assertEqual(self.land("lease", "renew", "L1", ok=False),
                         "land: L1 expired and L2 held by engine/D9 now covers a.txt; claim again after it is released")
        listed = self.land("lease", "list").splitlines()
        self.assertEqual([line.split(" until ")[0] for line in listed], ["L2 active engine/D9"])

    def test_renewing_an_expired_lease_refuses_when_the_cap_filled(self):
        self.init()
        self.land("cap", "2")
        self.claim("a/D1", "a.txt", "--ttl-hours", "0")
        self.claim("b/D1", "b.txt")
        self.claim("c/D1", "lib")
        self.assertEqual(self.land("lease", "renew", "L1", ok=False), "land: repository at its cap: 2 of 2 changes in flight (b/D1, c/D1)")
        self.land("lease", "release", "L3")
        self.assertEqual(self.land("lease", "renew", "L1"), "L1 renewed")
        self.assertEqual([line.split(" until ")[0] for line in self.land("lease", "list").splitlines()], ["L1 active a/D1", "L2 active b/D1"])

    def test_renew_if_live_refuses_an_expired_lease_and_renews_a_live_one(self):
        self.init()
        self.claim("a/D1", "a.txt", "--ttl-hours", "0")
        self.claim("b/D1", "b.txt")
        self.assertRegex(self.land("lease", "renew", "L1", "--if-live", ok=False), r"^land: L1 expired at \d{4}-\d\d-\d\dT\d\d:\d\d$")
        self.assertEqual(self.land("lease", "renew", "L2", "--if-live", "--ttl-hours", "48"), "L2 renewed")
        self.assertEqual(self.land("lease", "list").split(" until ")[1][:10], stamp_in(48)[:10])

    def test_renew_names_a_submitted_a_released_and_a_missing_lease(self):
        self.init()
        self.queue_one()
        self.claim("r/D2", "b.txt")
        self.land("lease", "release", "L2")
        self.assertEqual(self.land("lease", "renew", "L1", ok=False), "land: L1 is submitted; the queue holds it")
        self.assertEqual(self.land("lease", "renew", "L2", ok=False), "land: L2 is released")
        self.assertEqual(self.land("lease", "renew", "L9", ok=False), "land: no L9")

    def lease_row(self, number):
        with land.Store.for_repo(self.work) as store:
            return tuple(store.db.execute("SELECT * FROM lease WHERE id = ?", (number,)).fetchone())

    def owner_state(self):
        with land.Store.for_repo(self.work) as store:
            return ([tuple(row) for row in store.db.execute("SELECT * FROM owner")],
                    store.db.execute("SELECT count(*) FROM log").fetchone()[0])

    def test_an_owner_floor_rises_and_never_lowers(self):
        self.init()
        self.assertEqual(self.land("owner", "--prefix", "docs/", "--generation", "2"), "docs/ at generation 2")
        before = self.owner_state()
        self.assertEqual(self.land("owner", "--prefix", "docs/", "--generation", "2"), "docs/ at generation 2")
        self.assertEqual(self.land("owner", "--prefix", "docs/", "--generation", "1", ok=False),
                         "land: docs/ is at generation 2; a floor never lowers")
        self.assertEqual(self.owner_state(), before)
        self.assertEqual(self.land("owner", "--prefix", "docs/", "--generation", "3"), "docs/ at generation 3")

    def test_an_interrupted_in_process_status_cannot_leave_land_db_open(self):
        self.init()
        database = next((self.base / "state").glob("pstack-t3/landing/*/land.db"))
        argv = ["--repo", str(self.work), "status"]
        env = os.environ.copy()
        fired = {"done": False}

        def _open_land_db(database):
            directory, paths = _fd_paths()
            prefix = os.fspath(database) if directory == "/proc/self/fd" else os.path.realpath(database)
            found = []
            for target in paths.values():
                if target.startswith(prefix):
                    found.append(target)
            for obj in gc.get_objects():
                if not isinstance(obj, sqlite3.Connection):
                    continue
                try:
                    rows = obj.execute("PRAGMA database_list").fetchall()
                except sqlite3.ProgrammingError:
                    continue
                for row in rows:
                    path = row[2]
                    if path and str(path).startswith(prefix):
                        found.append(str(path))
            return found

        def trace(frame, event, arg):
            if fired["done"] or event != "call":
                return trace
            filename = frame.f_globals.get("__file__", "")
            if frame.f_code.co_name == "status" and filename.endswith("land.py"):
                fired["done"] = True
                raise KeyboardInterrupt
            return trace

        previous = os.environ.get("PSTACK_TEST_REAL_PROCESS")
        os.environ["PSTACK_TEST_REAL_PROCESS"] = "0"
        sys.settrace(trace)
        try:
            with self.assertRaises(KeyboardInterrupt):
                drive(SCRIPT, argv, env=env)
        finally:
            sys.settrace(None)
            if previous is None:
                os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)
            else:
                os.environ["PSTACK_TEST_REAL_PROCESS"] = previous
        self.assertTrue(fired["done"])
        self.assertEqual(_open_land_db(database), [])
        code, out, err = drive(SCRIPT, argv, env=env)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(_open_land_db(database), [])

    @unittest.skipUnless(os.path.isdir("/proc"), "the test lists a real /proc/self/fd as /dev/fd")
    def test_descriptor_cleanup_keeps_a_pipe_whose_path_query_fails(self):
        """Pin that a pipe whose path query fails stays listed, and a later pipe is still cleaned up.

        The F_GETPATH mock raises for a FIFO, which models Darwin's rejection of a non-vnode
        descriptor. The second pipe is opened after the snapshot, and the cleanup closes it.
        """
        read_first, write_first = os.pipe()
        opened = [read_first, write_first]

        def labeled_directory():
            return "/dev/fd", os.open("/proc/self/fd", os.O_RDONLY | os.O_DIRECTORY)

        def get_path(descriptor, cmd, buffer):
            if stat.S_ISFIFO(os.fstat(descriptor).st_mode):
                raise OSError(errno.EINVAL, "Inappropriate ioctl for device")
            return os.readlink(f"/proc/self/fd/{descriptor}").encode()

        try:
            # Linux fcntl has no F_GETPATH, so the seam names its own command.
            with mock.patch.multiple(fcntl, fcntl=get_path, F_GETPATH=9999, create=True), \
                    mock.patch.object(sys.modules[__name__], "_open_fd_directory", labeled_directory):
                before = _descriptors()
                read_second, write_second = os.pipe()
                opened.extend((read_second, write_second))
                _close_new_fds(before)
            self.assertIn(read_first, before)
            self.assertIn(write_first, before)
            for number in (read_second, write_second):
                with self.assertRaises(OSError):
                    os.fstat(number)
        finally:
            for number in opened:
                try:
                    os.close(number)
                except OSError:
                    pass

    def test_a_retained_interrupt_cannot_close_a_descriptor_a_later_open_reused(self):
        self.init()
        target = next(
            number
            for number, line in enumerate(SCRIPT.read_text().splitlines(), 1)
            if line == "    out = Outcome([], [], [], [], [])"
        )
        captured = []
        opened = []

        def trace(frame, event, arg):
            if event != "line" or frame.f_lineno != target or frame.f_code.co_name != "land":
                return trace
            if not frame.f_code.co_filename.endswith("land.py"):
                return trace
            captured.append(frame.f_locals["handle"].fileno())
            raise KeyboardInterrupt

        previous = os.environ.get("PSTACK_TEST_REAL_PROCESS")
        os.environ["PSTACK_TEST_REAL_PROCESS"] = "0"
        retained = None
        sys.settrace(trace)
        try:
            try:
                drive(SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
            except KeyboardInterrupt as error:
                retained = error
            sys.settrace(None)
            self.assertEqual(len(captured), 1)
            self.assertIsNotNone(retained)
            self.assertIsNotNone(retained.__traceback__)
            number = captured[0]
            database = next((self.base / "state").glob("pstack-t3/landing/*/land.db"))
            probe = os.open(database.parent / ".queue.lock", os.O_RDWR)
            opened.append(probe)
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.close(probe)
            matched = None
            while matched is None:
                fd = os.open(self.base / "later-file", os.O_CREAT | os.O_RDWR, 0o600)
                opened.append(fd)
                if fd == number:
                    matched = fd
                elif fd > number:
                    break
            self.assertEqual(matched, number, opened)
            self.assertEqual(os.write(matched, b"before"), 6)
            retained.__traceback__ = None
            retained = None
            gc.collect()
            self.assertEqual(os.fstat(matched).st_size, 6)
            self.assertEqual(os.write(matched, b"after"), 5)
        finally:
            sys.settrace(None)
            if previous is None:
                os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)
            else:
                os.environ["PSTACK_TEST_REAL_PROCESS"] = previous
            for fd in opened:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_an_unrelated_objects_systemexit_cannot_stop_interrupted_descriptor_cleanup(self):
        """Pin review finding D240-review-3 R1. The object is gc tracked and owns no descriptor."""
        self.init()

        class UnrelatedObject:
            @property
            def fileno(self):
                raise SystemExit("unrelated attribute lookup")

        unrelated = UnrelatedObject()
        self.assertTrue(gc.is_tracked(unrelated))

        target = next(
            number
            for number, line in enumerate(SCRIPT.read_text().splitlines(), 1)
            if line == "    out = Outcome([], [], [], [], [])"
        )
        captured = []
        opened = []

        def trace(frame, event, arg):
            if event != "line" or frame.f_lineno != target or frame.f_code.co_name != "land":
                return trace
            if not frame.f_code.co_filename.endswith("land.py"):
                return trace
            captured.append(frame.f_locals["handle"].fileno())
            raise KeyboardInterrupt

        previous = os.environ.get("PSTACK_TEST_REAL_PROCESS")
        os.environ["PSTACK_TEST_REAL_PROCESS"] = "0"
        retained = None
        propagated = None
        sys.settrace(trace)
        try:
            try:
                drive(SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
            except BaseException as error:
                propagated = error
            sys.settrace(None)
            self.assertIsInstance(propagated, KeyboardInterrupt)
            retained = propagated
            self.assertEqual(len(captured), 1)
            database = next((self.base / "state").glob("pstack-t3/landing/*/land.db"))
            probe = os.open(database.parent / ".queue.lock", os.O_RDWR)
            opened.append(probe)
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            retained.__traceback__ = None
            retained = None
        finally:
            sys.settrace(None)
            if previous is None:
                os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)
            else:
                os.environ["PSTACK_TEST_REAL_PROCESS"] = previous
            for fd in opened:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_a_dead_weakref_proxy_cannot_stop_interrupted_descriptor_cleanup(self):
        self.init()

        class Referent:
            pass

        referent = Referent()
        proxy = weakref.proxy(referent)
        del referent

        target = next(
            number
            for number, line in enumerate(SCRIPT.read_text().splitlines(), 1)
            if line == "    out = Outcome([], [], [], [], [])"
        )
        captured = []
        opened = []

        def trace(frame, event, arg):
            if event != "line" or frame.f_lineno != target or frame.f_code.co_name != "land":
                return trace
            if not frame.f_code.co_filename.endswith("land.py"):
                return trace
            captured.append(frame.f_locals["handle"])
            raise KeyboardInterrupt

        previous = os.environ.get("PSTACK_TEST_REAL_PROCESS")
        os.environ["PSTACK_TEST_REAL_PROCESS"] = "0"
        retained = None
        sys.settrace(trace)
        try:
            try:
                drive(SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
            except KeyboardInterrupt as error:
                retained = error
            sys.settrace(None)
            self.assertEqual(len(captured), 1)
            self.assertIsInstance(retained, KeyboardInterrupt)
            self.assertTrue(captured[0].closed)
            database = next((self.base / "state").glob("pstack-t3/landing/*/land.db"))
            probe = os.open(database.parent / ".queue.lock", os.O_RDWR)
            opened.append(probe)
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            retained.__traceback__ = None
            retained = None
        finally:
            sys.settrace(None)
            if previous is None:
                os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)
            else:
                os.environ["PSTACK_TEST_REAL_PROCESS"] = previous
            for fd in opened:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_an_in_process_owner_line_cannot_ignore_pythonioencoding(self):
        self.init()
        prefix = "caf\u00e9/"
        previous = os.environ.get("PSTACK_TEST_REAL_PROCESS")
        os.environ["PSTACK_TEST_REAL_PROCESS"] = "0"
        try:
            for generation, encoding, expected in (
                ("1", "ascii", (1, "")),
                ("2", "ascii:replace", (0, "caf?/ at generation 2\n")),
            ):
                with self.subTest(encoding=encoding):
                    argv = ["--repo", str(self.work), "owner", "--prefix", prefix,
                            "--generation", generation]
                    env = os.environ.copy()
                    env["PYTHONIOENCODING"] = encoding
                    code, out, _err = drive(SCRIPT, argv, env=env)
                    child = real_process(ProcessReason.INHERITED_ENV, SCRIPT, argv, env=env)
                    self.assertEqual((code, out), (child.returncode, child.stdout))
                    self.assertEqual((code, out), expected)
        finally:
            if previous is None:
                os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)
            else:
                os.environ["PSTACK_TEST_REAL_PROCESS"] = previous

    def test_a_fresh_interpreter_prints_the_owner_line_when_pythonutf8_overrides_locale_c(self):
        self.init()
        argv = ["--repo", str(self.work), "owner", "--prefix", "caf\u00e9/", "--generation", "2"]
        env = os.environ.copy()
        env.pop("PYTHONIOENCODING", None)
        env.update({"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "1", "PYTHONCOERCECLOCALE": "0"})
        child = real_process(ProcessReason.INHERITED_ENV, SCRIPT, argv, env=env)
        self.assertEqual((child.returncode, child.stdout), (0, "caf\u00e9/ at generation 2\n"))

    def test_lease_writes_refuse_an_owner_below_the_floor_and_change_nothing(self):
        self.init()
        self.assertEqual(self.claim("docs/D3", "a.txt", "--owner", "docs/@1"), "L1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0", "--owner", "docs/@1")
        self.land("owner", "--prefix", "docs/", "--generation", "2")
        before = self.lease_row(1)
        stale = "land: owner docs/@1 is stale; docs/ is at generation 2"
        self.assertEqual(self.land("lease", "release", "L1", "--owner", "docs/@1", ok=False), stale)
        self.assertEqual(self.land("lease", "renew", "L1", "--owner", "docs/@1", ok=False), stale)
        self.assertEqual(self.claim("docs/D4", "b.txt", "--owner", "docs/@1", ok=False), stale)
        self.assertEqual(self.lease_row(1), before)
        self.assertEqual(self.land("lease", "renew", "L1", "--owner", "docs/@2"), "L1 renewed")
        self.assertEqual(self.land("lease", "release", "L1", "--owner", "docs/@2"), "L1 released")

    def test_a_floored_prefix_needs_an_owner_that_covers_the_holder(self):
        self.init()
        self.land("owner", "--prefix", "docs/", "--generation", "2")
        self.assertEqual(self.claim("docs/D1", "a.txt", ok=False), "land: docs/D1 is under docs/ at generation 2; pass --owner docs/@2")
        self.assertEqual(self.claim("docs/D1", "a.txt", "--owner", "engine/@2", ok=False),
                         "land: owner engine/@2 does not cover holder docs/D1")
        self.assertEqual(self.claim("docs/D1", "a.txt", "--owner", "docs/@2"), "L1")
        self.assertEqual(self.claim("engine/D1", "b.txt"), "L2")
        self.assertEqual(self.land("lease", "release", "L2"), "L2 released")

    def test_submit_refuses_a_stale_owner_and_queues_for_the_current_one(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "agent\n"})
        lease = self.claim("docs/D1", "a.txt", "--owner", "docs/@1")
        self.land("owner", "--prefix", "docs/", "--generation", "2")
        submit = ("submit", "--holder", "docs/D1", "--branch", "w1", "--sha", sha, "--lease", lease, "--reviewer", REVIEWER)
        self.assertEqual(self.land(*submit, "--owner", "docs/@1", ok=False), "land: owner docs/@1 is stale; docs/ is at generation 2")
        self.assertEqual(self.land("status", "--holder", "docs/D1"), "no entries held by docs/D1")
        self.assertEqual(self.land(*submit, "--owner", "docs/@2"), "E1")

    def lease_check(self, holder, paths):
        code, out, _err = drive(
            SCRIPT,
            ["--repo", str(self.work), "lease", "check", "--holder", holder, "--paths", paths],
            env=os.environ.copy())
        return code, out.strip()

    def test_lease_check_runs_admission_without_claiming(self):
        self.init()
        self.claim("a/D1", "a.txt")
        self.land("lease", "release", "L1")
        self.assertEqual(self.land("lease", "check", "--holder", "b/D1", "--paths", "a.txt"), "free")
        self.claim("c/D1", "a.txt,b.txt")
        self.assertEqual(self.lease_check("b/D1", "a.txt"), (1, "L2 held by c/D1 on a.txt, b.txt"))
        self.land("cap", "1")
        self.assertEqual(self.lease_check("b/D1", "lib"), (1, "repository at its cap: 1 of 1 changes in flight (c/D1)"))
        self.assertEqual(len(self.land("lease", "list").splitlines()), 1)

    def test_status_holder_lists_one_holder_or_a_prefix(self):
        self.init()
        sha1 = self.worker("w1", {"a.txt": "one\n"})
        sha2 = self.worker("w2", {"b.txt": "two\n"})
        sha3 = self.worker("w3", {"lib/x.py": "three\n"})
        for holder, branch, sha, path in [("engine/D1", "w1", sha1, "a.txt"), ("engine/D2", "w2", sha2, "b.txt"), ("enginex/D1", "w3", sha3, "lib")]:
            lease = self.claim(holder, path)
            self.land("submit", "--holder", holder, "--branch", branch, "--sha", sha, "--lease", lease, "--reviewer", REVIEWER)
        self.assertEqual(self.land("status", "--holder", "engine/D2"), f"E2 queued (engine/D2, {sha2[:12]})")
        self.land("land")
        landed = sh("git", "rev-parse", "main~2", cwd=self.base / "origin.git")
        self.assertEqual(self.land("status", "--holder", "engine/").splitlines(),
                         [f"E1 landed (engine/D1, {sha1[:12]}) as {landed[:12]}",
                          f"E2 landed (engine/D2, {sha2[:12]}) as {sh('git', 'rev-parse', 'main~1', cwd=self.base / 'origin.git')[:12]}"])
        self.assertEqual(self.land("status", "--holder", "docs/D1"), "no entries held by docs/D1")

    def test_status_holder_sha_matches_the_whole_commit(self):
        import sqlite3
        from contextlib import closing
        self.init()
        sha = self.worker("w1", {"a.txt": "one\n"})
        lease = self.claim("engine/D1", "a.txt")
        self.land("submit", "--holder", "engine/D1", "--branch", "w1", "--sha", sha, "--lease", lease, "--reviewer", REVIEWER)
        other = sha[:12] + ("1" if sha[12] == "0" else "0") + sha[13:]
        database = next((self.base / "state").glob("pstack-t3/landing/*/land.db"))
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("INSERT INTO entry (at, holder, branch, sha, base, fingerprint, lease, reviewer, state, note) "
                       "SELECT at, holder, branch, ?, base, fingerprint, lease, reviewer, 'bounced', 'other' FROM entry",
                       (other,))
        self.assertEqual(self.land("status", "--holder", "engine/D1").splitlines(),
                         [f"E1 queued (engine/D1, {sha[:12]})", f"E2 bounced (engine/D1, {sha[:12]}): other"])
        self.assertEqual(self.land("status", "--holder", "engine/D1", "--sha", sha), f"E1 queued (engine/D1, {sha[:12]})")
        self.assertEqual(self.land("status", "--holder", "engine/D1", "--sha", sha.upper()),
                         f"E1 queued (engine/D1, {sha[:12]})")
        self.assertEqual(self.land("status", "--holder", "engine/D1", "--sha", other),
                         f"E2 bounced (engine/D1, {sha[:12]}): other")
        self.assertEqual(self.land("status", "--holder", "engine/D2", "--sha", sha), f"no entries held by engine/D2 at {sha}")

    def test_status_holder_shows_a_bounce_reason(self):
        self.init()
        self.queue_one(text="BROKEN\n")
        self.land("land")
        line = self.land("status", "--holder", "r/D1")
        self.assertRegex(line, r"^E1 bounced \(r/D1, [0-9a-f]{12}\): checks failed: `./check.sh` exited 1")

    def test_the_mode_line_names_the_holders_of_unreleased_leases(self):
        with self.fake_gh():
            self.init(mode="human")
            self.claim("engine/D3", "a.txt")
            self.claim("docs/D1", "b.txt")
            self.claim("old/D1", "lib", "--ttl-hours", "0")
            self.assertEqual(self.land("mode", "merge"), "landing mode is now merge; leases held by docs/D1, engine/D3")
            self.land("lease", "release", "L1")
            self.land("lease", "release", "L2")
            self.assertEqual(self.land("mode", "push"), "landing mode is now push")

    def test_submit_requires_the_holders_own_lease_covering_every_changed_path(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "a2\n", "b.txt": "b2\n"})
        self.land("lease", "claim", "--holder", "perf/D1", "--paths", "a.txt")
        self.land("lease", "claim", "--holder", "perf/D2", "--paths", "b.txt")
        self.assertIn("not an active lease held by bugs/D9",
                      self.land("submit", "--holder", "bugs/D9", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER, ok=False))
        self.assertIn("outside L1: b.txt",
                      self.land("submit", "--holder", "perf/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER, ok=False))

    def test_a_batch_lands_in_one_push_and_resubmitting_is_idempotent(self):
        self.init(batch=4)
        shas = [self.worker(f"w{n}", {f: f"{f} v{n}\n"}) for n, f in enumerate(["a.txt", "b.txt", "lib/x.py"], 1)]
        for n, (sha, path) in enumerate(zip(shas, ["a.txt", "b.txt", "lib"]), 1):
            self.land("lease", "claim", "--holder", f"r/D{n}", "--paths", path)
            self.assertEqual(self.land("submit", "--holder", f"r/D{n}", "--branch", f"w{n}", "--sha", sha, "--lease", f"L{n}", "--reviewer", REVIEWER), f"E{n}")
        self.assertEqual(self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", shas[0], "--lease", "L1", "--reviewer", REVIEWER), "E1 already queued")
        self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2), E3 (r/D3)")
        self.assertEqual(self.origin_log(), ["w3", "w2", "w1", "init"])
        self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_a_failing_batch_lands_the_good_entries_alone_and_bounces_the_bad_one(self):
        self.init(batch=3)
        good1 = self.worker("w1", {"a.txt": "fine\n"})
        bad = self.worker("w2", {"b.txt": "BROKEN\n"})
        good2 = self.worker("w3", {"lib/x.py": "fine\n"})
        for n, (sha, path) in enumerate([(good1, "a.txt"), (bad, "b.txt"), (good2, "lib")], 1):
            self.land("lease", "claim", "--holder", f"r/D{n}", "--paths", path)
            self.land("submit", "--holder", f"r/D{n}", "--branch", f"w{n}", "--sha", sha, "--lease", f"L{n}", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("landed E1 (r/D1), E3 (r/D3)", out)
        self.assertIn("bounced E2 (r/D2): checks failed: `./check.sh` exited 1", out)
        self.assertEqual(self.origin_log(), ["w3", "w1", "init"])
        self.assertEqual(self.land("lease", "list").split(" until ")[0], "L2 active r/D2")

    def test_a_conflict_bounces_and_the_fixed_resubmission_lands(self):
        self.init()
        first = self.worker("w1", {"a.txt": "one\n"})
        second = self.worker("w2", {"a.txt": "two\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", first, "--lease", "L1", "--reviewer", REVIEWER)
        self.land("land")
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", second, "--lease", "L2", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "bounced E2 (r/D2): conflict with trunk")
        path = self.base / "w2"
        sh("git", "fetch", "-q", "origin", cwd=path)
        sh("git", "reset", "-q", "--hard", "origin/main", cwd=path)
        (path / "a.txt").write_text("one\ntwo\n")
        fixed = self.commit("w2 fixed", cwd=path)
        self.assertEqual(self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", fixed, "--lease", "L2", "--reviewer", REVIEWER), "E3")
        self.assertEqual(self.land("land"), "landed E3 (r/D2)")

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

    def test_a_bounced_entry_resubmitted_gets_a_new_id(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "BROKEN\n"})
        self.claim("r/D1", "a.txt")
        submit = ["submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER]
        self.assertEqual(self.land(*submit), "E1")
        self.assertIn("bounced E1 (r/D1): checks failed: `./check.sh` exited 1", self.land("land"))
        self.rebuild_entry_without_autoincrement()
        self.assertIn("E1 bounced", self.land("status", "E1"))
        self.assertEqual(self.land(*submit), "E2")
        self.assertEqual(self.land("status", "E1", ok=False), "land: no E1")
        self.assertIn("E2 queued", self.land("status", "E2"))

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

    def test_a_conflict_under_a_non_english_locale_bounces(self):
        translated = self.use_non_english_locale()
        self.init()
        first = self.worker("w1", {"a.txt": "one\n"})
        second = self.worker("w2", {"a.txt": "two\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", first, "--lease", "L1", "--reviewer", REVIEWER)
        self.land("land")
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", second, "--lease", "L2", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "bounced E2 (r/D2): conflict with trunk")
        if not translated:
            recorded = self.git_locale_log.read_text().splitlines()
            self.assertTrue(recorded)
            for line in recorded:
                self.assertEqual(line, "cherry-pick LC_ALL=C")

    def test_a_conflict_rerere_resolved_to_trunk_bounces(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "worker\n"})
        (self.work / "a.txt").write_text("human\n")
        self.commit("human")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        sh("git", "config", "rerere.enabled", "true", cwd=self.work)
        sh("git", "config", "rerere.autoupdate", "true", cwd=self.work)
        record = self.base / "record"
        sh("git", "worktree", "add", "-q", "--detach", str(record), "origin/main", cwd=self.work)
        conflict = self.git_raw("cherry-pick", sha, cwd=record)
        self.assertNotEqual(conflict.returncode, 0, conflict.stdout + conflict.stderr)
        (record / "a.txt").write_text("human\n")
        sh("git", "rerere", cwd=record)
        sh("git", "add", "a.txt", cwd=record)
        sh("git", "cherry-pick", "--abort", cwd=record)
        replay = self.git_raw("cherry-pick", sha, cwd=record)
        detail = replay.stdout + replay.stderr
        self.assertNotEqual(replay.returncode, 0, detail)
        self.assertIn("CONFLICT", detail)
        self.assertIn("could not apply", detail)
        self.assertEqual(self.git_raw("status", "--porcelain", cwd=record).stdout, "")
        self.assertEqual(self.git_raw("ls-files", "-u", cwd=record).stdout, "")
        sh("git", "cherry-pick", "--abort", cwd=record)
        self.assertEqual(self.land("land"), "bounced E1 (r/D1): conflict with trunk")
        status = self.land("status", "E1")
        self.assertIn("E1 bounced", status)
        self.assertIn("conflict with trunk", status)
        self.assertNotIn("already in trunk", status)
        self.assertEqual(self.land("lease", "list").split(" until ")[0], "L1 active r/D1")
        self.assertEqual(self.origin_log(), ["human", "init"])
        self.assertEqual(sh("git", "show", "main:a.txt", cwd=self.base / "origin.git"), "human")

    def test_landing_uses_the_pinned_sha_even_when_the_branch_moves_on(self):
        self.init()
        reviewed = self.worker("w1", {"a.txt": "reviewed\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", reviewed, "--lease", "L1", "--reviewer", REVIEWER)
        (self.base / "w1/a.txt").write_text("unreviewed\n")
        self.commit("sneaky", cwd=self.base / "w1")
        self.land("land")
        self.assertEqual(self.origin_log(), ["w1", "init"])
        self.assertEqual(sh("git", "show", "main:a.txt", cwd=self.base / "origin.git"), "reviewed")

    def test_trunk_moved_by_a_human_is_rebased_onto_and_a_rewind_pauses_the_queue(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "agent\n"})
        (self.work / "b.txt").write_text("human\n")
        self.commit("human")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.assertEqual(self.origin_log(), ["w1", "human", "init"])
        sh("git", "push", "-q", "--force", "origin", "HEAD~1:main", cwd=self.work)
        other = self.worker("w2", {"b.txt": "agent2\n"})
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "b.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", other, "--lease", "L2", "--reviewer", REVIEWER)
        self.assertIn("queue paused: trunk no longer contains the last landed commit", self.land("land"))
        self.assertIn("queue paused", self.land("land"))
        self.land("resume")
        self.assertEqual(self.land("land"), "landed E2 (r/D2)")

    def test_a_patch_already_on_trunk_lands_without_replaying_it(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "agent\n"})
        (self.work / "a.txt").write_text("agent\n")
        self.commit("human")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.assertEqual(self.origin_log(), ["human", "init"])
        self.assertIn("already in trunk", self.land("status", "E1"))

    def test_a_commit_that_started_empty_pauses_instead_of_landing(self):
        self.init()
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "a.txt").write_text("agent\n")
        self.commit("w1", cwd=path)
        sh("git", "commit", "-q", "--allow-empty", "-m", "empty", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        paused = self.land("land")
        self.assertIn("queue paused", paused)
        self.assertIn("cherry-pick failed", paused)
        self.assertNotIn("landed", paused)
        self.assertEqual(self.origin_log(), ["init"])

    def test_a_crash_after_the_push_is_recovered_as_landed_without_pushing_again(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "agent\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        real = land.publish

        def publish_then_crash(*args):
            real(*args)
            raise KeyboardInterrupt

        with land.Store.for_repo(self.work) as store:
            with mock.patch.object(land, "publish", publish_then_crash), self.assertRaises(KeyboardInterrupt):
                land.land(store)
        self.assertEqual(self.land_in_another_process("status", "E1").split(" (")[0], "E1 landing")
        self.assertEqual(self.land_in_another_process("land"), "nothing to land")
        self.assertIn("E1 landed", self.land_in_another_process("status", "E1"))
        self.assertIn("recovered after an interrupted run", self.land_in_another_process("status", "E1"))
        self.assertEqual(self.origin_log(), ["w1", "init"])

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

    def test_a_land_run_runs_no_subprocess_inside_a_write_transaction(self):
        self.init()
        self.queue_one()
        hits = self.subprocesses_during_land()
        self.assertEqual(self.origin_log()[0], "w1")
        self.assertIn("E1 landed", self.land("status", "E1"))
        self.land("mode", "human")
        sh("git", "fetch", "-q", "origin", cwd=self.work)
        sh("git", "merge", "-q", "--ff-only", "origin/main", cwd=self.work)
        sha = self.worker("w2", {"b.txt": "same\n"})
        (self.work / "b.txt").write_text("same\n")
        self.commit("human")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        lease = self.land("lease", "claim", "--holder", "r/D2", "--paths", "b.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", sha, "--lease", lease, "--reviewer", REVIEWER)
        with self.fake_gh():
            hits += self.subprocesses_during_land()
        self.assertIn("already in trunk", self.land("status", "E2"))
        self.assertEqual(hits, [])

    def test_a_held_queue_lock_reports_busy(self):
        self.init()
        with land.Store.for_repo(self.work) as store:
            import fcntl
            with open(store.dir / ".queue.lock", "a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                result = real_process(
                    ProcessReason.LOCK, SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("queue busy", result.stdout)

    def test_local_mode_lands_on_a_queue_owned_ref_and_never_moves_a_branch(self):
        self.init(mode="local", trunk="perf", base="main")
        sha = self.worker("w1", {"a.txt": "fast\n"}, base="main")
        self.land("lease", "claim", "--holder", "perf/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "perf/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        main_before = sh("git", "rev-parse", "main", cwd=self.work)
        self.assertEqual(self.land("land"), "landed E1 (perf/D1)")
        self.assertEqual(sh("git", "show", "refs/landing/perf:a.txt", cwd=self.work), "fast")
        self.assertEqual(sh("git", "rev-parse", "main", cwd=self.work), main_before)
        self.assertEqual(sh("git", "status", "--porcelain", cwd=self.work), "")

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

    def test_a_legacy_push_without_a_pr_opens_the_pr_on_landing_e(self):
        with self.fake_gh():
            self.require_pr_head_on_origin()
            previous = self.use_land_script(self.base_land_script())
            try:
                self.init(mode="human")
                self.queue_one()
                (self.base / "pr-create-fails").write_text("gh: not authenticated\n")
                paused = self.land("land")
                remote = self.base / "origin.git"
                candidate = sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote)
                self.assertFalse(self.ref_exists("refs/heads/landing/e1", remote))
                (self.base / "pr-create-fails").unlink()
                self.restore_land_script(previous)
                previous = None
                held = self.land("status", "E1")
                self.assertEqual(self.land("resume"), "queue resumed")
                opened = self.land("land")
            finally:
                if previous is not None:
                    self.restore_land_script(previous)
            self.assertIn("queue paused", paused)
            self.assertIn("awaiting-merge", held)
            self.assertNotIn("pull/9", held)
            self.assertNotIn("queue paused", opened)
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", remote), opened)
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/e1", cwd=remote), candidate)
            status = self.land("status", "E1")
            self.assertIn("awaiting-merge", status)
            self.assertIn("https://github.com/o/r/pull/9", status)
            self.assertNotIn("Paused", self.land("status"))

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

    def test_a_closed_pr_on_landing_q_is_ignored(self):
        self.ignore_non_open_pr_on_landing_q("CLOSED")

    def test_a_merged_pr_on_landing_q_is_ignored(self):
        self.ignore_non_open_pr_on_landing_q("MERGED")

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

    def test_an_older_queues_open_pr_on_landing_q_stays_open_by_design_and_a_new_pr_on_landing_e_lands(self):
        """Intended. The queue never reads, adopts, or deletes landing/q<n>. The old branch and PR are closed by hand."""
        with self.fake_gh():
            (self.base / "pr-seq").write_text("9\n")
            candidate = self.crashed_landing_q_pr()
            remote = self.base / "origin.git"
            opened = self.land("land")
            self.assertEqual(opened, "opened PRs for E1 (r/D1) https://github.com/o/r/pull/10")
            self.assertEqual(self.created_heads(), ["landing/q1", "landing/e1"])
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/e1", cwd=remote), candidate)
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote), candidate)
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.pr_views_of_landing_q(), [])
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote), candidate)
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertIn("landed as abc123", self.land("status", "E1"))
            self.assertEqual(self.land("lease", "list"), "no leases held")

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

    def test_a_merged_pr_stored_on_landing_q_lands_when_the_remote_is_not_github(self):
        with self.fake_gh():
            candidate = self.open_pr_on_landing_q_with_the_url_stored(remote_is_github=False)
            remote = self.base / "origin.git"
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote), candidate)
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertIn("landed as abc123", self.land("status", "E1"))
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertEqual(self.created_heads(), ["landing/q1"])

    def test_a_merged_pr_stored_on_landing_q_lands_when_the_remote_is_github(self):
        with self.fake_gh():
            candidate = self.open_pr_on_landing_q_with_the_url_stored(remote_is_github=True)
            remote = self.base / "origin.git"
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote), candidate)
            self.assertIn("landed as abc123", self.land("status", "E1"))
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def hide_a_landing_e1_the_older_queue_never_made(self, candidate):
        remote = self.base / "origin.git"
        sh("git", "update-ref", "refs/heads/landing/e1", candidate, cwd=remote)
        sh("git", "config", "receive.hideRefs", "refs/heads/landing/e1", cwd=remote)
        (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
        return remote

    def test_a_merged_pr_stored_on_landing_q_waits_when_the_forge_says_a_hidden_landing_e_exists(self):
        with self.fake_gh():
            candidate = self.open_pr_on_landing_q_with_the_url_stored(remote_is_github=True)
            remote = self.hide_a_landing_e1_the_older_queue_never_made(candidate)
            deletion = self.git_raw("push", "--no-follow-tags", "origin", "--delete", "landing/e1", cwd=self.work)
            self.assertIn("remote ref does not exist", deletion.stderr)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("branch-status 200", (self.base / "gh-calls").read_text())

    def test_accepted_limit_a_hidden_landing_e_is_left_in_place_and_the_entry_settles_when_the_remote_is_not_github(self):
        """Intended. With no forge to ask, a hidden branch looks missing, and waiting would never end."""
        with self.fake_gh():
            candidate = self.open_pr_on_landing_q_with_the_url_stored(remote_is_github=False)
            remote = self.hide_a_landing_e1_the_older_queue_never_made(candidate)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertNotIn("branch-status", (self.base / "gh-calls").read_text())

    def test_a_pr_stored_on_landing_q_that_merged_at_another_head_bounces_when_the_remote_is_not_github(self):
        with self.fake_gh():
            candidate = self.open_pr_on_landing_q_with_the_url_stored(remote_is_github=False)
            other = "0123456789abcdef0123456789abcdef01234567"
            (self.base / "pr-state").write_text(f"MERGED {other} abc123")
            settled = self.land("land")
            self.assertTrue(settled.startswith("bounced E1 (r/D1): merged at abc123 with head 0123456789ab"), settled)
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/q1", cwd=self.base / "origin.git"), candidate)
            self.assertIn("review what reached trunk", self.land("status", "E1"))

    def test_a_pr_stored_on_landing_q_that_closed_bounces_when_the_remote_is_not_github(self):
        with self.fake_gh():
            candidate = self.open_pr_on_landing_q_with_the_url_stored(remote_is_github=False)
            (self.base / "pr-state").write_text(f"CLOSED {candidate}")
            self.assertEqual(self.land("land"), "bounced E1 (r/D1): PR closed without merging")
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/q1", cwd=self.base / "origin.git"), candidate)
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_a_merged_pr_on_landing_e_waits_when_git_says_it_is_absent_and_the_remote_is_not_github(self):
        """The PR head is landing/e1, so git's absent line alone does not settle the entry."""
        with self.fake_gh():
            sh("git", "remote", "set-url", "origin", str(self.base / "origin.git"), cwd=self.work)
            self.init(mode="human")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs for E1 (r/D1) https://github.com/o/r/pull/9")
            remote = self.base / "origin.git"
            candidate = sh("git", "rev-parse", "refs/heads/landing/e1", cwd=remote)
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            sh("git", "update-ref", "-d", "refs/heads/landing/e1", cwd=remote)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))

    def test_an_older_queues_open_pr_on_landing_q_stays_open_by_design_and_a_closed_pr_on_landing_e_bounces(self):
        """Intended. The old branch and its PR stay when the new PR on landing/e<n> bounces."""
        with self.fake_gh():
            (self.base / "pr-seq").write_text("9\n")
            candidate = self.crashed_landing_q_pr()
            remote = self.base / "origin.git"
            self.assertEqual(self.land("land"), "opened PRs for E1 (r/D1) https://github.com/o/r/pull/10")
            (self.base / "pr-state").write_text(f"CLOSED {candidate}")
            self.assertEqual(self.land("land"), "bounced E1 (r/D1): PR closed without merging")
            self.assertEqual(self.created_heads(), ["landing/q1", "landing/e1"])
            self.assertEqual(self.pr_views_of_landing_q(), [])
            self.assertEqual(sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote), candidate)
            self.assertIn("PR closed without merging", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_a_merged_pr_on_landing_e_at_another_head_is_not_adopted(self):
        """A fresh contract opens its own PR when landing/e1 is an earlier contract's merged PR."""
        with self.fake_gh():
            self.record_created_pr_branch()
            self.require_pr_head_on_origin()
            self.init(mode="human")
            other = "0123456789abcdef0123456789abcdef01234567"
            (self.base / "pr-url").write_text("https://github.com/o/r/pull/9\n")
            (self.base / "pr-head").write_text("landing/e1")
            (self.base / "pr-seq").write_text("10\n")
            (self.base / "entry-pr-state").write_text(f"MERGED {other}\n")
            self.queue_one()
            reported = self.land("land")
        self.assertEqual(self.created_heads(), ["landing/e1"], reported)
        self.assertNotIn("pull/9", reported)
        status = self.land("status", "E1")
        self.assertIn("awaiting-merge", status)
        self.assertIn("https://github.com/o/r/pull/10", status)
        self.assertNotIn("https://github.com/o/r/pull/9", status)
        remote = self.base / "origin.git"
        self.assertNotEqual(sh("git", "rev-parse", "refs/heads/landing/e1", cwd=remote), other)

    def test_human_mode_opens_a_pr_and_marks_landed_when_it_merges(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs for E1 (r/D1) https://github.com/o/r/pull/9")
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            self.assertTrue(self.ref_exists("refs/remotes/origin/landing/e1", self.work))
            (self.base / "pr-state").write_text(f"OPEN {candidate} ")
            self.assertEqual(self.land("land"), "nothing to land")
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/e1", self.work))
            self.assertEqual(self.land("lease", "list"), "no leases held")

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

    def test_a_pr_opened_for_an_entry_left_without_one_is_reported(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.leave_awaiting_merge_without_a_pr()
            reported = self.land("land")
            self.assertEqual(reported, "opened PRs for E1 (r/D1) https://github.com/o/r/pull/9")
            status = self.land("status", "E1")
            self.assertIn("awaiting-merge", status)
            self.assertIn("https://github.com/o/r/pull/9", status)
            self.assertEqual(self.land("land"), "nothing to land")

    def test_merge_mode_reports_a_pr_opened_for_an_entry_left_without_one(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            self.init(mode="merge")
            self.queue_one()
            self.leave_awaiting_merge_without_a_pr()
            reported = self.land("land")
            self.assertEqual(
                reported,
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertEqual(self.land("land"), "nothing to land")

    def test_merge_mode_merges_its_own_pr_when_there_are_no_checks_to_wait_for(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", self.land("land"))
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(),
                             [self.pinned(9, "--auto", "--merge"), self.pinned(9, "--merge")])
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/e1", self.work))
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_lets_github_merge_after_required_checks_and_asks_once(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.assertEqual(self.land("land"), "nothing to land")
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            self.on_origin("update-ref", "refs/heads/main", candidate)
            (self.base / "pr-state").write_text(f"MERGED {candidate} {candidate}")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(len((self.base / "merge-calls").read_text().splitlines()), 1)

    def test_merge_mode_waits_for_running_checks_then_merges(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            self.init(mode="merge", merge_method="squash")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("pr merge https://github.com/o/r/pull/9 --squash", calls)
            self.assertEqual(self.land("land"), "nothing to land")
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertIn("pr merge https://github.com/o/r/pull/9 --squash", (self.base / "merge-calls").read_text())
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/e1", self.work))

    def test_merge_mode_bounces_a_pr_whose_required_checks_failed(self):
        with self.fake_gh():
            (self.base / "checks").write_text("failed")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            self.assertTrue(self.land("lease", "list").startswith("L1 active r/D1"))

    def test_merge_mode_does_not_merge_when_auto_would_succeed_with_a_pending_check(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            (self.base / "auto-merges-immediately").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertFalse((self.base / "merge-calls").exists())
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_merge_mode_bounces_when_a_check_fails_after_auto_merge_was_enabled(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            (self.base / "checks").write_text("failed")
            self.assertEqual(self.land("land"), "bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            self.assertTrue(self.land("lease", "list").startswith("L1 active r/D1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_merge_mode_does_not_merge_when_the_check_query_fails(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            (self.base / "checks-query-fails").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertEqual(
                out,
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9\n"
                "queue paused: could not read checks for https://github.com/o/r/pull/9: API unavailable. "
                "Fix it, then run land.py resume")
            self.assertIn("could not read checks", out)
            self.assertIn("queue paused", out)
            self.assertNotIn("landed E1", out)
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertFalse((self.base / "merge-calls").exists())

    def absent_marker(self):
        with land.Store.for_repo(self.work) as store:
            return store.contract.get("absentDrain") or {}

    def test_merge_mode_does_not_merge_a_just_opened_pr_with_zero_posted_checks(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.assertNotIn("landed", opened)
            self.assertNotIn("queue paused", opened)
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertNotIn("Paused", self.land("status"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse((self.base / "merge-calls").exists())
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(),
                             [self.pinned(9, "--auto", "--merge"),
                              self.pinned(9, "--merge")])
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_lands_a_merged_pr_when_a_later_check_fails(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            self.on_origin("update-ref", "refs/heads/main", candidate)
            (self.base / "pr-state").write_text(f"MERGED {candidate} {candidate}")
            (self.base / "checks").write_text("failed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertIn("E1 landed", self.land("status", "E1"))

    def test_merge_mode_pauses_on_a_merge_conflict_when_auto_merge_is_disabled(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            (self.base / "auto-merge-disabled").write_text("")
            (self.base / "plain-merge-fails").write_text("merge conflict")
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.assertNotIn("queue paused", opened)
            self.assertFalse((self.base / "merge-calls").exists())
            paused = self.land("land")
            self.assertIn("queue paused", paused)
            self.assertIn("merge conflict", paused)
            self.assertNotIn("landed", paused)
            self.assertIn("Paused", self.land("status"))
            self.assertIn("merge conflict", self.land("status"))
            calls = [
                self.pinned(9, "--auto", "--merge"),
                self.pinned(9, "--merge"),
            ]
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(), calls)
            self.assertIn("queue paused", self.land("land"))
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(), calls)

    def test_merge_mode_waits_when_auto_merge_is_disabled_and_policy_refuses(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            (self.base / "auto-merge-disabled").write_text("")
            (self.base / "policy-until-passed").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            still = self.land("land")
            self.assertNotIn("queue paused", still)
            self.assertNotIn("landed", still)
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertNotIn("Paused", self.land("status"))
            calls = (self.base / "merge-calls").read_text()
            self.assertIn("pr merge https://github.com/o/r/pull/9 --auto --merge", calls)
            self.assertIn("pr merge https://github.com/o/r/pull/9 --merge", calls)
            (self.base / "pr-checks.json").unlink()
            (self.base / "auto-merge-disabled").unlink()
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_merge_mode_waits_when_base_branch_policy_refuses_and_no_check_is_posted(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            (self.base / "policy-until-passed").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.assertFalse((self.base / "auto-merge-disabled").exists())
            still_empty = self.land("land")
            self.assertNotIn("queue paused", still_empty)
            self.assertNotIn("landed", still_empty)
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            (self.base / "pr-checks.json").unlink()
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_merge_mode_waits_when_policy_refuses_and_a_required_check_is_unposted(self):
        with self.fake_gh():
            parts = [
                {"name": f"part {number}", "status": "COMPLETED", "conclusion": "SUCCESS"}
                for number in range(1, 9)
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": parts}))
            (self.base / "policy-until-passed").write_text("")
            (self.base / "protection-required.json").write_text(json.dumps({
                "contexts": ["test (3.10)", "test (3.12)"],
                "checks": [{"context": "part 1", "app_id": 1}],
            }))
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", first)
            self.assertNotIn("queue paused", first)
            self.assertNotIn("landed", first)
            with land.Store.for_repo(self.work) as store:
                self.assertEqual(store.contract.get("paused"), "")
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertNotIn("Paused", self.land("status"))
            self.assertIn("protection/required_status_checks", (self.base / "gh-calls").read_text())
            (self.base / "pr-checks.json").unlink()
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            with land.Store.for_repo(self.work) as store:
                self.assertEqual(store.contract.get("paused"), "")

    def test_merge_mode_pauses_when_policy_refuses_and_every_required_check_is_posted(self):
        with self.fake_gh():
            posted = [
                {"name": "test (3.10)", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"context": "test (3.12)", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": posted}))
            (self.base / "policy-until-passed").write_text("")
            (self.base / "protection-required.json").write_text(json.dumps({
                "contexts": ["test (3.10)", "test (3.12)"],
                "checks": [{"context": "ghost", "app_id": 1}],
            }))
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertIn("queue paused", first)
            self.assertIn("the base branch policy prohibits the merge", first)
            self.assertNotIn("landed", first)
            with land.Store.for_repo(self.work) as store:
                self.assertIn(
                    "the base branch policy prohibits the merge",
                    store.contract["paused"])
            second = self.land("land")
            self.assertIn("queue paused", second)
            self.assertNotIn("landed", second)

    def test_merge_mode_pauses_when_policy_refuses_and_required_names_are_unreadable(self):
        with self.fake_gh():
            parts = [
                {"name": f"part {number}", "status": "COMPLETED", "conclusion": "SUCCESS"}
                for number in range(1, 9)
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": parts}))
            (self.base / "policy-until-passed").write_text("")
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertIn("queue paused", first)
            self.assertIn("the base branch policy prohibits the merge", first)
            self.assertNotIn("landed", first)
            with land.Store.for_repo(self.work) as store:
                self.assertIn(
                    "the base branch policy prohibits the merge",
                    store.contract["paused"])
            second = self.land("land")
            self.assertIn("queue paused", second)

    def test_merge_mode_pauses_when_policy_refuses_and_required_contexts_are_empty(self):
        with self.fake_gh():
            parts = [
                {"name": f"part {number}", "status": "COMPLETED", "conclusion": "SUCCESS"}
                for number in range(1, 9)
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": parts}))
            (self.base / "policy-until-passed").write_text("")
            (self.base / "protection-required.json").write_text(json.dumps({"contexts": []}))
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertIn("queue paused", first)
            self.assertIn("the base branch policy prohibits the merge", first)
            self.assertNotIn("landed", first)
            with land.Store.for_repo(self.work) as store:
                self.assertIn(
                    "the base branch policy prohibits the merge",
                    store.contract["paused"])
            second = self.land("land")
            self.assertIn("queue paused", second)
            self.assertNotIn("landed", second)

    def test_merge_mode_lands_a_green_rollup_whose_check_name_contains_a_quote(self):
        with self.fake_gh():
            posted = [
                {"name": "b\"q\\\t\n", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ]
            (self.base / "pr-checks.json").write_text(
                json.dumps({"reviewDecision": "", "statusCheckRollup": posted}))
            self.init(mode="merge")
            self.queue_one()
            first = self.land("land")
            self.assertNotIn("queue paused", first)
            self.assertIn("landed E1 (r/D1)", first)
            with land.Store.for_repo(self.work) as store:
                self.assertEqual(store.contract.get("paused"), "")
            self.assertEqual(self.land("land"), "nothing to land")

    def test_merge_mode_does_not_merge_when_a_pending_check_appears_between_reads(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"APPROVED","statusCheckRollup":[]}')
            (self.base / "checks-flip").write_text("pending")
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertNotIn("landed", opened)
            self.assertNotIn("queue paused", opened)
            self.assertFalse((self.base / "merge-calls").exists())
            self.assertIn("waiting for required checks", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))

    def test_merge_mode_does_not_merge_when_a_failed_check_appears_between_reads(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"APPROVED","statusCheckRollup":[]}')
            (self.base / "checks-flip").write_text("failed")
            self.arm_auto_merge()
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertNotIn("landed", opened)
            self.assertEqual((self.base / "merge-calls").read_text().strip(),
                             "pr merge https://github.com/o/r/pull/9 --disable-auto")
            self.assertIn("bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", opened)
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_merge_mode_drops_the_absent_marker_when_the_entry_lands(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.assertEqual(self.absent_marker(), {"1": 1})
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertNotIn("1", self.absent_marker())

    def test_merge_mode_drops_the_absent_marker_when_the_entry_bounces(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.assertEqual(self.absent_marker(), {"1": 1})
            (self.base / "pr-checks.json").unlink()
            (self.base / "checks").write_text("failed")
            self.assertIn("bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", self.land("land"))
            self.assertNotIn("1", self.absent_marker())
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_a_checked_out_queue_branch_is_named_when_land_cannot_delete_it(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            sh("git", "branch", "landing/e1", candidate, cwd=self.work)
            sh("git", "checkout", "-q", "landing/e1", cwd=self.work)
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            out = self.land("land")
            self.assertIn("landed E1 (r/D1)", out)
            self.assertIn("left local landing/e1", out)
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.work))
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertIn("E1 landed", self.land("status", "E1"))

    def test_merge_mode_lands_when_the_remote_deletes_the_queue_branch_during_push(self):
        with self.fake_gh():
            self.install_racing_branch_delete()
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertIn("refs/heads/landing/e1", (self.base / "race-deleted").read_text())
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/e1", self.work))
            self.assertIn("branch-status 404", (self.base / "gh-calls").read_text())

    def test_merge_mode_leaves_the_entry_when_the_queue_branch_delete_fails(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            sh("git", "config", "receive.denyDeletes", "true", cwd=self.base / "origin.git")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertIn("branch-status 200", (self.base / "gh-calls").read_text())
            sh("git", "config", "--unset", "receive.denyDeletes", cwd=self.base / "origin.git")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_leaves_the_entry_when_a_hidden_ref_refuses_deletion(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            sh("git", "config", "receive.denyDeletes", "true", cwd=remote)
            sh("git", "config", "uploadpack.hideRefs", "refs/heads/landing/e1", cwd=remote)
            listed = subprocess.run(["git", "ls-remote", "origin", "refs/heads/landing/e1"],
                                    cwd=self.work, capture_output=True, text=True)
            self.assertEqual((listed.returncode, listed.stdout.strip()), (0, ""))
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertIn("branch-status 200", (self.base / "gh-calls").read_text())

    def test_merge_mode_leaves_the_entry_when_a_hidden_ref_is_locked(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            sh("git", "config", "uploadpack.hideRefs", "refs/heads/landing/e1", cwd=remote)
            (remote / "refs/heads/landing/e1.lock").write_text("x")
            listed = subprocess.run(["git", "ls-remote", "origin", "refs/heads/landing/e1"],
                                    cwd=self.work, capture_output=True, text=True)
            self.assertEqual((listed.returncode, listed.stdout.strip()), (0, ""))
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertIn("branch-status 200", (self.base / "gh-calls").read_text())

    def test_merge_mode_leaves_the_entry_when_receive_pack_hides_the_queue_branch(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            sh("git", "config", "receive.hideRefs", "refs/heads/landing/e1", cwd=remote)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertIn("branch-status 200", (self.base / "gh-calls").read_text())

    def test_an_annotated_tag_stays_local_when_the_queue_pushes(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            sh("git", "tag", "-a", "private-local-tag", "-m", "local tag", cwd=self.work)
            sh("git", "config", "push.followTags", "true", cwd=self.work)
            log = self.record_pushes()
            try:
                self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", self.land("land"))
                self.merged_queue_branch()
                self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            finally:
                self.stop_recording_pushes()
            remote = self.base / "origin.git"
            self.assert_pushes_stay_on_their_refs(log)
            self.assertFalse(self.ref_exists("refs/tags/private-local-tag", remote))
            self.assertNotIn("branch-status", (self.base / "gh-calls").read_text())
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_push_mode_keeps_an_annotated_tag_local(self):
        self.init(mode="push")
        self.queue_one()
        sh("git", "tag", "-a", "private-local-tag", "-m", "local tag", cwd=self.work)
        sh("git", "config", "push.followTags", "true", cwd=self.work)
        log = self.record_pushes()
        try:
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        finally:
            self.stop_recording_pushes()
        self.assert_pushes_stay_on_their_refs(log)
        self.assertFalse(self.ref_exists("refs/tags/private-local-tag", self.base / "origin.git"))

    def test_merge_mode_settles_when_the_forge_reports_the_branch_gone(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            self.merged_queue_branch()
            sh("git", "update-ref", "-d", "refs/heads/landing/e1", cwd=remote)
            sh("git", "tag", "-a", "private-local-tag", "-m", "local tag", cwd=self.work)
            sh("git", "config", "push.followTags", "true", cwd=self.work)
            log = self.record_pushes()
            try:
                self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            finally:
                self.stop_recording_pushes()
            self.assertEqual(
                log.read_text().splitlines(),
                ["push --no-follow-tags origin --delete landing/e1"])
            self.assertFalse(self.ref_exists("refs/tags/private-local-tag", remote))
            self.assertIn("branch-status 404", (self.base / "gh-calls").read_text())
            self.assertIn(
                "api --include repos/{owner}/{repo}/branches/landing/e1",
                (self.base / "gh-calls").read_text())
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_leaves_the_entry_when_the_branch_query_fails(self):
        with self.fake_gh():
            self.open_pr_with_a_merge_requested()
            remote = self.base / "origin.git"
            self.merged_queue_branch()
            sh("git", "update-ref", "-d", "refs/heads/landing/e1", cwd=remote)
            (self.base / "branch-query-fails").write_text("x")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("branch-status 500", (self.base / "gh-calls").read_text())
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", remote))

    def test_merge_mode_leaves_the_entry_when_another_push_url_dropped_the_branch(self):
        with self.fake_gh():
            self.open_pr_with_a_merge_requested()
            remote = self.base / "origin.git"
            backup = self.base / "backup.git"
            ref = "refs/heads/landing/e1"
            sha = self.merged_queue_branch()
            sh("git", "checkout", "--detach", "-q", sha, cwd=self.work)
            sh("git", "clone", "--bare", "-q", str(remote), str(backup), cwd=self.base)
            sh("git", "update-ref", "-d", ref, cwd=backup)
            (remote / "refs/heads/landing/e1.lock").write_text("x")
            sh("git", "config", "--add", "remote.origin.pushurl", str(remote), cwd=self.work)
            sh("git", "config", "--add", "remote.origin.pushurl", str(backup), cwd=self.work)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists(ref, remote))
            self.assertFalse(self.ref_exists(ref, backup))
            self.assertNotIn("branches/", (self.base / "gh-calls").read_text())
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "E1"))

    def test_merge_mode_leaves_the_entry_when_the_other_push_url_keeps_the_branch(self):
        with self.fake_gh():
            self.open_pr_with_a_merge_requested()
            origin = self.base / "origin.git"
            backup = self.base / "backup.git"
            ref = "refs/heads/landing/e1"
            sha = self.merged_queue_branch()
            sh("git", "checkout", "--detach", "-q", sha, cwd=self.work)
            sh("git", "clone", "--bare", "-q", str(origin), str(backup), cwd=self.base)
            sh("git", "update-ref", "-d", ref, cwd=origin)
            lock = backup / "refs/heads/landing/e1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text("x")
            sh("git", "config", "--add", "remote.origin.pushurl", str(origin), cwd=self.work)
            sh("git", "config", "--add", "remote.origin.pushurl", str(backup), cwd=self.work)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertFalse(self.ref_exists(ref, origin))
            self.assertTrue(self.ref_exists(ref, backup))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "E1"))

    def test_merge_mode_leaves_the_entry_when_the_push_url_names_another_repo(self):
        with self.fake_gh():
            self.open_pr_with_a_merge_requested()
            remote = self.base / "origin.git"
            self.merged_queue_branch()
            sh("git", "remote", "set-url", "origin", "https://github.com/else/where.git", cwd=self.work)
            sh("git", "config", "receive.denyDeletes", "true", cwd=remote)
            (self.base / "forge-says-missing").write_text("x")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertNotIn("branches/", (self.base / "gh-calls").read_text())

    def test_merge_mode_leaves_the_entry_when_the_forge_repo_cannot_be_read(self):
        with self.fake_gh():
            self.open_pr_with_a_merge_requested()
            remote = self.base / "origin.git"
            self.merged_queue_branch()
            sh("git", "update-ref", "-d", "refs/heads/landing/e1", cwd=remote)
            (self.base / "repo-view-fails").write_text("x")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "E1"))

    def test_push_mode_settles_a_missing_branch_only_from_gits_absent_line(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            with land.Store.for_repo(self.work) as store:
                with store.tx() as db:
                    db.execute("INSERT OR REPLACE INTO contract VALUES ('mode', ?)", (json.dumps("push"),))
            with land.Store.for_repo(self.work) as store:
                self.assertEqual(store.contract["mode"], "push")
                remote = self.base / "origin.git"
                self.merged_queue_branch()
                sh("git", "update-ref", "-d", "refs/heads/landing/e1", cwd=remote)
                sh("git", "tag", "-a", "private-local-tag", "-m", "local tag", cwd=self.work)
                sh("git", "config", "push.followTags", "true", cwd=self.work)
                (self.base / "branch-query-fails").write_text("x")
                entry = store.entries("awaiting-merge")[0]
                log = self.record_pushes()
                try:
                    landed, bounced = [], []
                    self.assertTrue(land.take_pr(store, entry, landed, bounced))
                finally:
                    self.stop_recording_pushes()
                self.assertEqual(landed, [entry["id"]])
                self.assertEqual(bounced, [])
                self.assertEqual(
                    log.read_text().splitlines(),
                    ["push --no-follow-tags origin --delete landing/e1"])
                self.assertNotIn("branches/", (self.base / "gh-calls").read_text())
                self.assertFalse(self.ref_exists("refs/tags/private-local-tag", remote))
            self.assertEqual(self.land_in_another_process("status", "E1").splitlines()[0].split(". ")[0], "E1 landed (r/D1, w1)")
            self.assertEqual(self.land_in_another_process("lease", "list"), "no leases held")

    def test_merge_mode_lands_in_the_run_whose_plain_merge_succeeds(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            (self.base / "merge-state-lags").write_text("")
            self.init(mode="merge", merge_method="squash")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", opened)
            self.assertNotIn("landed", opened)
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/e1", self.work))
            self.assertIn("pr merge https://github.com/o/r/pull/9 --squash", (self.base / "merge-calls").read_text())

    def test_merge_mode_lands_in_the_run_whose_auto_merge_completes(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge", merge_method="squash")
            self.queue_one()
            self.land("land")
            (self.base / "auto-merges-immediately").write_text("")
            (self.base / "merge-state-lags").write_text("")
            (self.base / "pr-checks.json").unlink()
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
            calls = (self.base / "merge-calls").read_text().splitlines()
            self.assertEqual(calls[-1], self.pinned(9, "--auto", "--squash"))

    def test_merge_mode_reports_a_merge_that_lands_after_the_opening_state_read(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", self.land("land"))
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            (self.base / "pr-state").write_text(f"OPEN {candidate} ")
            self.on_origin("update-ref", "refs/heads/main", candidate)
            (self.base / "pr-state-after").write_text(f"MERGED {candidate} {candidate}")
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_merge_mode_pauses_for_review_without_calling_auto_merge(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text(
                '{"reviewDecision":"REVIEW_REQUIRED","statusCheckRollup":'
                '[{"name":"test (3.12)","status":"COMPLETED","conclusion":"SUCCESS"}]}')
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertIn("queue paused", out)
            self.assertIn("https://github.com/o/r/pull/9", out)
            self.assertIn("approving review", out)
            self.assertNotIn("landed", out)
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--auto", calls.split())
            self.assertIn("awaiting-merge", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))

    def test_merge_mode_pauses_when_changes_are_requested_without_calling_auto_merge(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text(
                '{"reviewDecision":"CHANGES_REQUESTED","statusCheckRollup":[]}')
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertIn("queue paused", out)
            self.assertIn("https://github.com/o/r/pull/9", out)
            self.assertIn("changes requested", out)
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--auto", calls.split())

    def test_merge_mode_disables_auto_merge_when_it_bounces_an_open_pr(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.arm_auto_merge()
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            (self.base / "checks").write_text("failed")
            out = self.land("land")
            self.assertIn("bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", out)
            self.assertIn("pr merge https://github.com/o/r/pull/9 --disable-auto", (self.base / "merge-calls").read_text())
            self.assertNotIn("pr comment", (self.base / "gh-calls").read_text())
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertTrue(self.land("lease", "list").startswith("L1 active r/D1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_merge_mode_pauses_when_it_cannot_disable_auto_merge_on_a_failed_pr(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            (self.base / "disable-auto-fails").write_text("API unavailable")
            self.arm_auto_merge()
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.land("land")
            (self.base / "checks").write_text("failed")
            self.assertEqual(self.land("land"), "queue paused: required checks failed on https://github.com/o/r/pull/9: test (3.12). "
                                                "Auto-merge is still enabled: API unavailable. Turn auto-merge off on https://github.com/o/r/pull/9, "
                                                "then run land.py resume and land.py land")
            self.assertTrue(self.land("status", "E1").startswith("E1 awaiting-merge"))
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            (self.base / "auto-merge-request.json").unlink()
            self.land("resume")
            self.assertIn("bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", self.land("land"))
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_merge_mode_skips_disable_auto_when_auto_merge_request_is_null(self):
        with self.fake_gh():
            (self.base / "checks").write_text("failed")
            (self.base / "auto-merge-request.json").write_text("null")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--disable-auto", calls)
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertNotIn("auto-merge still enabled", self.land("status", "E1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_merge_mode_pauses_on_a_disable_auto_failure_when_auto_merge_request_is_set(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.arm_auto_merge()
            (self.base / "disable-auto-fails").write_text("GraphQL: Auto merge is not enabled for this pull request")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.land("land")
            (self.base / "checks").write_text("failed")
            out = self.land("land")
            self.assertIn("queue paused: required checks failed on https://github.com/o/r/pull/9: test (3.12). "
                          "Auto-merge is still enabled: GraphQL: Auto merge is not enabled for this pull request", out)
            self.assertTrue(self.land("status", "E1").startswith("E1 awaiting-merge"))
            self.assertIn("pr merge https://github.com/o/r/pull/9 --disable-auto", (self.base / "merge-calls").read_text())
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())

    def test_merge_mode_pauses_when_github_requires_a_human_approval(self):
        with self.fake_gh():
            (self.base / "merge-refused").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertIn("queue paused: https://github.com/o/r/pull/9 needs an approving review", out)
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--auto", calls.split())

    def test_merge_mode_builds_each_queued_entry_on_the_candidate_of_the_entry_ahead(self):
        with self.fake_gh():
            self.init(mode="merge", merge_method="squash")
            self.queue_entries(3)
            self.assertEqual(
                self.land("land"),
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9, "
                "E2 (r/D2) https://github.com/o/r/pull/10, E3 (r/D3) https://github.com/o/r/pull/11")
            self.assertEqual(self.origin_files("landing/e1"), ["one", "b.txt", "lib/x.py"])
            self.assertEqual(self.origin_files("landing/e2"), ["one", "two", "lib/x.py"])
            self.assertEqual(self.origin_files("landing/e3"), ["one", "two", "three"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e1^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "landing/e1"))
            self.assertEqual(self.on_origin("rev-parse", "landing/e3^"), self.on_origin("rev-parse", "landing/e2"))
            self.assertEqual(self.origin_log(), ["init"])
            self.assertFalse((self.base / "merge-calls").exists())

    def test_merge_mode_lands_three_stacked_entries_in_order_in_the_run_after_their_checks_pass(self):
        with self.fake_gh():
            self.open_line(3)
            for number in (9, 10, 11):
                self.post_check(number, "passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2), E3 (r/D3)")
            self.assertEqual(self.origin_log(), ["w3", "w2", "w1", "init"])
            self.assertEqual([self.on_origin("show", "--format=", "--name-only", commit) for commit in ("main~2", "main~1", "main")],
                             ["a.txt", "b.txt", "lib/x.py"])
            self.assertEqual(self.origin_files("main"), ["one", "two", "three"])
            self.assertEqual(self.merge_calls(), [
                self.pinned(9, "--auto", "--squash"),
                self.pinned(9, "--squash"),
                self.pinned(10, "--auto", "--squash"),
                self.pinned(10, "--squash"),
                self.pinned(11, "--auto", "--squash"),
                self.pinned(11, "--squash"),
            ])
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_merges_nothing_while_the_first_entry_is_pending_notes_the_entry_ahead_on_the_passed_ones_then_lands_all_three(self):
        with self.fake_gh():
            self.open_line(3)
            self.post_check(9, "pending")
            self.post_check(10, "passed")
            self.post_check(11, "passed")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_log(), ["init"])
            self.assertEqual(self.land("status", "E1"),
                             "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. waiting for required checks before merging")
            self.assertEqual(self.land("status", "E2"),
                             "E2 awaiting-merge (r/D2, w2). https://github.com/o/r/pull/10. waiting for E1 to merge first")
            self.assertEqual(self.land("status", "E3"),
                             "E3 awaiting-merge (r/D3, w3). https://github.com/o/r/pull/11. waiting for E2 to merge first")
            self.post_check(9, "passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2), E3 (r/D3)")
            self.assertEqual(self.origin_log(), ["w3", "w2", "w1", "init"])

    def test_merge_mode_keeps_a_failed_entry_behind_a_pending_first_and_bounces_it_in_the_run_that_lands_the_first(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "pending")
            self.post_check(10, "failed")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.land("status", "E2"),
                             "E2 awaiting-merge (r/D2, w2). https://github.com/o/r/pull/10. waiting for E1 to merge first")
            self.assertEqual(self.listed(), ["L1 submitted r/D1: a.txt", "L2 submitted r/D2: b.txt"])
            self.post_check(9, "passed")
            self.assertEqual(self.land("land"),
                             "landed E1 (r/D1)\n"
                             "bounced E2 (r/D2): required checks failed on https://github.com/o/r/pull/10: test (3.12)")
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertEqual(self.merge_calls(), [
                self.pinned(9, "--auto", "--squash"),
                self.pinned(9, "--squash"),
            ])

    def test_merge_mode_with_no_posted_checks_opens_two_entries_and_lands_both_in_the_second_run(self):
        with self.fake_gh():
            self.init(mode="merge", merge_method="squash")
            self.queue_entries(2)
            self.assertEqual(
                self.land("land"),
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9, "
                "E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_log(), ["init"])
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "init"])

    def test_merge_mode_lands_both_entries_in_the_run_after_one_killed_right_after_the_first_merge(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            (self.base / "crash-after-merge").write_text("")
            self.killed_land()
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertEqual(self.land("status", "E1").split(". ")[0], "E1 awaiting-merge (r/D1, w1)")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "init"])
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_bounces_a_failed_first_entry_alone_rebuilds_the_two_behind_it_without_its_change_then_lands_both(self):
        with self.fake_gh():
            self.open_line(3)
            self.post_check(9, "failed")
            self.post_check(10, "passed")
            self.post_check(11, "passed")
            self.assertEqual(self.absent_marker(), {"1": 1, "2": 1, "3": 1})
            self.assertEqual(
                self.land("land"),
                "bounced E1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)\n"
                "rebuilt E2 (r/D2) https://github.com/o/r/pull/10\n"
                "rebuilt E3 (r/D3) https://github.com/o/r/pull/11")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            self.assertEqual(self.origin_files("landing/e3"), ["a.txt", "two", "three"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.on_origin("rev-parse", "landing/e3^"), self.on_origin("rev-parse", "landing/e2"))
            self.assertEqual(self.listed(), ["L1 active r/D1: a.txt", "L2 submitted r/D2: b.txt", "L3 submitted r/D3: lib/x.py"])
            self.assertEqual(self.absent_marker(), {"2": 2, "3": 2})
            self.assertEqual(self.land("status", "E2"),
                             "E2 awaiting-merge (r/D2, w2). https://github.com/o/r/pull/10. waiting for required checks before merging")
            self.post_check(10, "passed")
            self.post_check(11, "passed")
            self.assertEqual(self.land("land"), "landed E2 (r/D2), E3 (r/D3)")
            self.assertEqual(self.origin_log(), ["w3", "w2", "init"])
            self.assertEqual(self.origin_files("main"), ["a.txt", "two", "three"])

    def test_merge_mode_lands_the_first_entry_bounces_a_failed_second_and_rebuilds_the_third_without_the_second(self):
        with self.fake_gh():
            self.open_line(3)
            self.post_check(9, "passed")
            self.post_check(10, "failed")
            self.post_check(11, "passed")
            self.assertEqual(
                self.land("land"),
                "landed E1 (r/D1)\n"
                "bounced E2 (r/D2): required checks failed on https://github.com/o/r/pull/10: test (3.12)\n"
                "rebuilt E3 (r/D3) https://github.com/o/r/pull/11")
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertEqual(self.origin_files("landing/e3"), ["one", "b.txt", "three"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e3^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.merge_calls(), [
                self.pinned(9, "--auto", "--squash"),
                self.pinned(9, "--squash"),
            ])

    def test_merge_mode_bounces_an_entry_whose_rebuild_conflicts_with_trunk_and_rebuilds_the_entry_behind_it(self):
        with self.fake_gh():
            self.open_line(3)
            self.post_check(9, "passed")
            self.post_check(10, "pending")
            self.post_check(11, "pending")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.commit_on_origin("outside", path="b.txt")
            self.assertEqual(
                self.land("land"),
                "bounced E2 (r/D2): conflict with trunk\n"
                "rebuilt E3 (r/D3) https://github.com/o/r/pull/11")
            self.assertEqual(self.origin_files("landing/e3"), ["one", "outside", "three"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e3^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.listed(), ["L2 active r/D2: b.txt", "L3 submitted r/D3: lib/x.py"])
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertEqual(self.origin_log(), ["outside", "w1", "init"])

    def test_merge_mode_merges_nothing_and_rebuilds_both_passed_entries_when_trunk_gained_an_outside_commit_then_lands_both(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            self.commit_on_origin("outside")
            self.assertEqual(
                self.land("land"),
                "rebuilt E1 (r/D1) https://github.com/o/r/pull/9\n"
                "rebuilt E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_log(), ["outside", "init"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e1^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "landing/e1"))
            self.assertEqual(self.on_origin("show", "landing/e2:merged.txt"), "outside")
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "outside", "init"])

    def test_merge_mode_rebuilds_an_entry_again_after_a_run_killed_between_the_rebuild_push_and_its_row(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "failed")
            before = self.entry_fields(2, "candidate", "onto")
            with self.land_dies_after_its_push_to("landing/e2"):
                self.killed_land()
            self.assertEqual(self.land("status", "E1"),
                             "E1 bounced (r/D1, w1). https://github.com/o/r/pull/9. "
                             "required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            self.assertEqual(self.entry_fields(2, "candidate", "onto"), before)
            self.assertEqual(self.land("land"), "rebuilt E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.entry_fields(2, "candidate", "onto"),
                             (self.on_origin("rev-parse", "landing/e2"), self.on_origin("rev-parse", "main")))
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            self.assertEqual(self.land("status", "E2"),
                             "E2 awaiting-merge (r/D2, w2). https://github.com/o/r/pull/10. waiting for required checks before merging")

    def test_a_store_from_before_the_onto_column_keeps_its_first_entry_rebuilds_the_second_behind_it_then_lands_both(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "pending")
            first = self.on_origin("rev-parse", "landing/e1")
            alone = sh("git", "rev-parse", "w2", cwd=self.work)
            sh("git", "push", "-q", "--force", "origin", f"{alone}:refs/heads/landing/e2", cwd=self.work)
            self.entry_table_without_onto({2: alone})
            self.assertNotIn("onto", self.entry_columns())
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            log = self.record_pushes()
            try:
                self.assertEqual(self.land("land"), "rebuilt E2 (r/D2) https://github.com/o/r/pull/10")
            finally:
                self.stop_recording_pushes()
            self.assertIn("onto", self.entry_columns())
            self.assertEqual([line.rsplit(":", 1)[1] for line in log.read_text().splitlines()], ["refs/heads/landing/e2"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), first)
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), first)
            self.assertEqual(self.origin_files("landing/e2"), ["one", "two", "lib/x.py"])
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "init"])

    def test_merge_mode_lands_an_entry_and_pauses_when_its_merge_commit_holds_another_tree_than_its_candidate_and_resume_clears_it(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            (self.base / "outside-commit-before-merge").write_text("")
            out = self.land("land")
            merged = self.on_origin("rev-parse", "main")
            pause = (f"E1 merged as {merged[:12]}, whose tree is not the checked candidate {candidate[:12]}. "
                     "Trunk holds a change no check ran against. Check trunk, then run land.py resume")
            self.assertEqual(out, f"landed E1 (r/D1)\nqueue paused: {pause}")
            self.assertEqual(self.origin_log(), ["w1", "outside", "init"])
            self.assertEqual(self.land("status", "E1"), f"E1 landed (r/D1, w1). landed as {merged[:12]}. https://github.com/o/r/pull/9")
            self.assertEqual(self.land("status"), f"merge mode onto refs/remotes/origin/main. landed: 1. Paused: {pause}")
            self.assertEqual(self.land("land"), f"queue paused: {pause}")
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.land("status"), "merge mode onto refs/remotes/origin/main. landed: 1.")

    def test_merge_mode_pauses_and_leaves_the_entry_awaiting_merge_when_git_cannot_read_its_merge_commit_then_reports_the_other_tree_after_resume(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            (self.base / "outside-commit-before-merge").write_text("")
            with self.git_fails_for_the_merge_commit_of_pr_9("arg.startswith(merged)"):
                out = self.land("land")
                merged = self.on_origin("rev-parse", "main")
                pause = self.unread_commit_pause(merged)
                self.assertEqual(out, f"queue paused: {pause}")
                self.assertEqual(self.origin_log(), ["w1", "outside", "init"])
                self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. "
                                                            "merge requested by the queue")
                self.assertEqual(self.land("status"),
                                 f"merge mode onto refs/remotes/origin/main. awaiting-merge: 1, leases held: 1. Paused: {pause}")
                self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)
                self.assertEqual(self.land("resume"), "queue resumed")
                self.assertEqual(self.land("land"), f"queue paused: {pause}")
                self.assertEqual(self.land("resume"), "queue resumed")
            other_tree = (f"E1 merged as {merged[:12]}, whose tree is not the checked candidate {candidate[:12]}. "
                          "Trunk holds a change no check ran against. Check trunk, then run land.py resume")
            self.assertEqual(self.land("land"), f"landed E1 (r/D1)\nqueue paused: {other_tree}")

    def test_merge_mode_does_not_report_another_tree_when_the_tree_of_an_equal_merge_commit_cannot_be_read_and_lands_it_after_resume(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            checked_tree = self.on_origin("rev-parse", "landing/e1^{tree}")
            with self.git_fails_for_the_merge_commit_of_pr_9("arg == merged + '^{tree}'"):
                out = self.land("land")
                merged = self.on_origin("rev-parse", "main")
                self.assertEqual(self.on_origin("rev-parse", "main^{tree}"), checked_tree)
                self.assertEqual(out, f"queue paused: {self.unread_commit_pause(merged)}")
                self.assertNotIn("whose tree is not the checked candidate", out)
                self.assertEqual(self.land("status", "E1").split(". ")[0], "E1 awaiting-merge (r/D1, w1)")
                self.assertEqual(self.listed(), ["L1 submitted r/D1: a.txt"])
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.land("status"), "merge mode onto refs/remotes/origin/main. landed: 1.")
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_pauses_when_the_remote_does_not_serve_the_reported_merge_commit(self):
        with self.fake_gh():
            self.open_line(1)
            candidate = self.on_origin("rev-parse", "landing/e1")
            unknown = "1" * 40
            (self.base / "pr-state").write_text(f"MERGED {candidate} {unknown}")
            self.assertEqual(self.land("land"), f"queue paused: {self.unread_commit_pause(unknown)}")
            self.assertEqual(self.land("status", "E1").split(". ")[0], "E1 awaiting-merge (r/D1, w1)")
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)

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

    def test_merge_mode_pauses_when_the_state_read_of_a_merged_pr_names_no_merge_commit(self):
        self.pauses_on_a_merged_pr_whose_state_read_reports_the_merge_commit_as("")

    def test_merge_mode_pauses_when_the_state_read_of_a_merged_pr_names_an_abbreviated_merge_commit(self):
        self.pauses_on_a_merged_pr_whose_state_read_reports_the_merge_commit_as("abc123")

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

    def test_merge_mode_does_not_say_trunk_holds_an_unchecked_change_when_a_merge_with_another_tree_is_not_on_trunk_then_says_it_once_trunk_holds_it(self):
        with self.fake_gh():
            candidate, merged = self.pauses_on_a_merge_of_the_first_of_two_entries_that_trunk_was_rewound_past(outside=True)
            other_tree = (f"E1 merged as {merged[:12]}, whose tree is not the checked candidate {candidate[:12]}. "
                          "Trunk holds a change no check ran against. Check trunk, then run land.py resume")
            self.assertEqual(self.land("land"), f"landed E1 (r/D1)\nqueue paused: {other_tree}")
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "rebuilt E2 (r/D2) https://github.com/o/r/pull/10")
            self.post_check(10, "passed")
            self.assertEqual(self.land("land"), "landed E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "outside", "init"])
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_leaves_an_entry_awaiting_merge_when_its_merge_with_the_candidate_tree_is_not_on_trunk_then_lands_the_line_once_trunk_holds_it(self):
        with self.fake_gh():
            self.pauses_on_a_merge_of_the_first_of_two_entries_that_trunk_was_rewound_past(outside=False)
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "w1", "init"])
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_fetches_a_merge_commit_that_trunk_lost_during_the_run_and_pauses_with_the_entry_awaiting_merge(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            initial, candidate = self.on_origin("rev-parse", "main"), self.on_origin("rev-parse", "landing/e1")
            (self.base / "trunk-rewound-after-merge").write_text("")
            out = self.land("land")
            self.assertEqual(self.origin_log(), ["init"])
            merged = json.loads((self.base / "prs.json").read_text())["9"]["merge"]
            self.assertEqual(out, f"queue paused: {self.off_trunk_pause(merged, initial)}")
            self.assertEqual(sh("git", "log", "-1", "--format=%s", merged, cwd=self.work), "w1")
            self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. "
                                                        "merge requested by the queue")
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)

    def test_merge_mode_says_the_merge_could_not_be_checked_when_git_cannot_tell_whether_trunk_holds_the_merge_commit(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            (self.base / "outside-commit-before-merge").write_text("")
            with self.git_fails_for_the_merge_commit_of_pr_9("arg == '--is-ancestor'", code=128):
                out = self.land("land")
                merged = self.on_origin("rev-parse", "main")
                self.assertEqual(
                    out,
                    "queue paused: The merge of E1 could not be checked. "
                    f"Git could not tell whether trunk at {merged} holds merge commit {merged} "
                    "(git merge-base --is-ancestor exited 128), "
                    "so the tree of the merge commit was not compared with the tree of the candidate. "
                    "E1 stays awaiting merge. Check that git can read both commits here, "
                    "then run land.py resume and land.py land")
                self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. "
                                                            "merge requested by the queue")
                self.assertEqual(self.on_origin("rev-parse", "landing/e1"), candidate)
            self.assertEqual(self.land("resume"), "queue resumed")
            other_tree = (f"E1 merged as {merged[:12]}, whose tree is not the checked candidate {candidate[:12]}. "
                          "Trunk holds a change no check ran against. Check trunk, then run land.py resume")
            self.assertEqual(self.land("land"), f"landed E1 (r/D1)\nqueue paused: {other_tree}")

    def test_merge_mode_says_it_could_not_check_trunk_when_git_cannot_read_the_last_landed_commit(self):
        with self.fake_gh():
            self.open_line(1)
            candidate = self.on_origin("rev-parse", "landing/e1")
            other, unknown = "0123456789abcdef0123456789abcdef01234567", "2" * 40
            (self.base / "pr-state").write_text(f"MERGED {other} {unknown}")
            self.assertEqual(self.land("land"), f"bounced E1 (r/D1): merged at {unknown[:12]} with head {other[:12]}, "
                                                f"not the checked {candidate[:12]}; review what reached trunk")
            (self.base / "pr-state").unlink()
            self.assertEqual(
                self.land("land"),
                f"queue paused: could not check that trunk contains the last landed commit {unknown[:12]} "
                "(git merge-base --is-ancestor exited 128); check that git can read that commit here, then run land.py resume")
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "nothing to land")

    def test_merge_mode_pauses_when_the_pr_state_read_fails(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            (self.base / "state-query-fails").write_text("")
            self.assertEqual(
                self.land("land"),
                "queue paused: could not read the state of https://github.com/o/r/pull/9. gh said API unavailable. "
                "Fix it, then run land.py resume")
            self.assertEqual(self.merge_calls(), [])
            self.assertEqual(self.origin_log(), ["init"])
            (self.base / "state-query-fails").unlink()
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_merge_mode_does_not_bounce_a_merge_requested_entry_with_a_failed_check_when_the_second_state_read_fails(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "E1"))
            (self.base / "checks").write_text("failed")
            self.fail_state_reads_after_the_first()
            self.assertEqual(
                self.land("land"),
                "queue paused: could not read the state of https://github.com/o/r/pull/9. gh said API unavailable. "
                "Fix it, then run land.py resume")
            self.assertIn("E1 awaiting-merge", self.land("status", "E1"))
            self.assertEqual(self.listed(), ["L1 submitted r/D1: a.txt"])

    def test_human_mode_leaves_the_entry_and_does_not_pause_when_the_pr_state_read_fails(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            (self.base / "state-query-fails").write_text("")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.land("status"), "human mode onto refs/remotes/origin/main. awaiting-merge: 1, leases held: 1.")

    def test_merge_mode_pauses_without_merging_a_head_that_replaced_the_candidate_after_a_base_branch_was_modified_refusal_then_lands_the_restored_candidate(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            rogue = self.unreviewed_head()
            (self.base / "pr-9-base-moved").write_text("1")
            (self.base / "pr-9-head-replaced-on-refusal").write_text(rogue)
            pause = self.head_changed_pause(rogue, candidate)
            self.assertEqual(self.land("land"), f"queue paused: {pause}")
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), rogue)
            self.assertEqual((self.base / "pr-9-checks").read_text(), f"passed {rogue}")
            self.assertEqual(self.origin_log(), ["init"])
            self.assertFalse(self.ref_exists("main:rogue.txt", self.base / "origin.git"))
            self.assertEqual(self.merge_calls(), [self.pinned(9, "--auto", "--squash")] + [self.pinned(9, "--squash")] * 2)
            self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9. "
                                                        f"PR head changed to {rogue[:12]} outside the queue")
            self.assertEqual(self.land("status"),
                             f"merge mode onto refs/remotes/origin/main. awaiting-merge: 1, leases held: 1. Paused: {pause}")
            self.assertEqual(self.land("land"), f"queue paused: {pause}")
            sh("git", "push", "-q", "--force", "origin", f"{candidate}:refs/heads/landing/e1", cwd=self.work)
            self.post_check(9, "passed")
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertFalse(self.ref_exists("main:rogue.txt", self.base / "origin.git"))

    def test_merge_mode_pauses_without_merging_a_head_that_replaced_the_candidate_between_the_check_read_and_the_merge_request(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            rogue = self.unreviewed_head()
            (self.base / "pr-9-head-replaced-after-check-read").write_text(rogue)
            self.assertEqual(self.land("land"), f"queue paused: {self.head_changed_pause(rogue, candidate)}")
            self.assertEqual(self.on_origin("rev-parse", "landing/e1"), rogue)
            self.assertEqual(self.origin_log(), ["init"])
            self.assertEqual(self.merge_calls(), [self.pinned(9, "--auto", "--squash"), self.pinned(9, "--squash")])

    def test_merge_mode_pins_its_auto_merge_request_to_the_candidate_and_pauses_when_another_head_replaced_it_after_the_check_read(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.open_line(1)
            self.post_check(9, "passed")
            candidate = self.on_origin("rev-parse", "landing/e1")
            rogue = self.unreviewed_head()
            (self.base / "pr-9-head-replaced-after-check-read").write_text(rogue)
            self.assertEqual(self.land("land"), f"queue paused: {self.head_changed_pause(rogue, candidate)}")
            self.assertEqual(self.merge_calls(), [self.pinned(9, "--auto", "--squash"), self.pinned(9, "--squash")])
            self.assertNotIn("merge requested by the queue", self.land("status", "E1"))
            self.assertEqual(self.origin_log(), ["init"])

    def test_merge_mode_after_a_refused_merge_names_the_refusal_and_not_a_changed_head_when_the_state_read_fails(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            rogue = self.unreviewed_head()
            (self.base / "pr-9-head-replaced-after-check-read").write_text(rogue)
            self.fail_state_reads_after_the_first()
            self.assertEqual(
                self.land("land"),
                "queue paused: GitHub refused to merge https://github.com/o/r/pull/9: "
                "GraphQL: Head branch was modified. Review and try the merge again. (mergePullRequest). "
                "Fix it, then run land.py resume")
            self.assertEqual(self.origin_log(), ["init"])

    def test_merge_mode_asks_again_after_one_base_branch_was_modified_refusal_and_lands_both_entries_in_that_run(self):
        with self.fake_gh():
            self.open_line(2)
            self.post_check(9, "passed")
            self.post_check(10, "passed")
            (self.base / "pr-10-base-moved").write_text("1")
            self.assertEqual(self.land("land"), "landed E1 (r/D1), E2 (r/D2)")
            self.assertEqual(self.merge_calls(), [
                self.pinned(9, "--auto", "--squash"),
                self.pinned(9, "--squash"),
                self.pinned(10, "--auto", "--squash"),
                self.pinned(10, "--squash"),
                self.pinned(10, "--squash"),
            ])
            self.assertEqual(self.land("status"), "merge mode onto refs/remotes/origin/main. landed: 2.")

    def test_merge_mode_waits_without_a_pause_after_four_base_branch_was_modified_refusals_in_one_run_and_lands_in_the_next(self):
        with self.fake_gh():
            self.open_line(1)
            self.post_check(9, "passed")
            (self.base / "pr-9-base-moved").write_text("4")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertEqual(self.merge_calls(), [self.pinned(9, "--auto", "--squash")]
                             + [self.pinned(9, "--squash")] * 4)
            self.assertEqual(self.land("status"), "merge mode onto refs/remotes/origin/main. awaiting-merge: 1, leases held: 1.")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_merge_mode_under_rebase_opens_the_second_entry_in_the_run_that_lands_the_first(self):
        with self.fake_gh():
            self.init(mode="merge", merge_method="rebase")
            self.queue_entries(2)
            self.assertEqual(
                self.land("land"),
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9\n"
                "still queued: E2")
            self.assertEqual(self.created_heads(), ["landing/e1"])
            self.assertEqual(
                self.land("land"),
                "landed E1 (r/D1)\n"
                "opened PRs that merge when their checks pass: E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.created_heads(), ["landing/e1", "landing/e2"])
            self.assertEqual(self.origin_log(), ["w1", "init"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(self.origin_files("landing/e2"), ["one", "two", "lib/x.py"])

    def test_merge_mode_keeps_an_entry_queued_while_an_unmerged_entry_ahead_holds_its_change_then_settles_it_as_landed(self):
        with self.fake_gh():
            self.init(mode="merge", merge_method="squash")
            self.assertEqual(self.queue_one(text="same\n", name="w1"), "E1")
            self.assertEqual(self.queue_one(text="same\n", name="w2"), "E2")
            self.assertEqual(
                self.land("land"),
                "opened PRs that merge when their checks pass: E1 (r/D1) https://github.com/o/r/pull/9\n"
                "still queued: E2")
            self.assertEqual(self.land("status", "E2"), "E2 queued (r/D1, w2)")
            self.assertEqual(self.listed(), ["L1 submitted r/D1: a.txt", "L2 submitted r/D1: a.txt"])
            self.post_check(9, "passed")
            self.land("land")
            self.assertEqual(self.origin_log(), ["w1", "init"])
            merged = self.on_origin("rev-parse", "main")
            self.assertEqual(self.land("status", "E2"), f"E2 landed (r/D1, w2). landed as {merged[:12]}. already in trunk")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertEqual(self.created_heads(), ["landing/e1"])

    def test_a_merge_mode_land_run_that_lands_bounces_and_rebuilds_runs_no_subprocess_inside_a_write_transaction(self):
        with self.fake_gh():
            self.open_line(3)
            self.post_check(9, "passed")
            self.post_check(10, "failed")
            self.post_check(11, "passed")
            hits = self.subprocesses_during_land()
            self.assertEqual([self.land("status", entry).split(" (")[0] for entry in ("E1", "E2", "E3")],
                             ["E1 landed", "E2 bounced", "E3 awaiting-merge"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e3^"), self.on_origin("rev-parse", "main"))
            self.assertEqual(hits, [])

    def test_human_mode_builds_each_candidate_alone_on_trunk_and_lands_the_second_entry_while_the_first_awaits_its_merge(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_entries(2)
            self.assertEqual(
                self.land("land"),
                "opened PRs for E1 (r/D1) https://github.com/o/r/pull/9, E2 (r/D2) https://github.com/o/r/pull/10")
            self.assertEqual(self.origin_files("landing/e1"), ["one", "b.txt", "lib/x.py"])
            self.assertEqual(self.origin_files("landing/e2"), ["a.txt", "two", "lib/x.py"])
            self.assertEqual(self.on_origin("rev-parse", "landing/e2^"), self.on_origin("rev-parse", "main"))
            self.gh("pr", "merge", "https://github.com/o/r/pull/10", "--squash")
            self.assertEqual(self.land("land"), "landed E2 (r/D2)")
            self.assertEqual(self.origin_log(), ["w2", "init"])
            self.assertEqual(self.land("status", "E1"), "E1 awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9")

    def test_init_in_merge_mode_picks_an_allowed_method(self):
        with self.fake_gh():
            self.allow_methods(squash=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "squash")
            self.assertIn(
                "repo view --json mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed",
                (self.base / "gh-calls").read_text(),
            )
            self.allow_methods(rebase=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "rebase")
            self.allow_methods(squash=True, rebase=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "squash")
            self.allow_methods(merge=True, squash=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "merge")
            self.allow_methods(merge=True, squash=True, rebase=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "merge")
            self.allow_methods(merge=True, rebase=True)
            self.init(mode="merge", merge_method="rebase")
            self.assertEqual(self.stored_merge_method(), "rebase")

    def test_init_outside_merge_mode_does_not_ask_which_methods_are_allowed(self):
        with self.fake_gh():
            self.allow_methods(squash=True)
            self.init(mode="human")
            calls = (self.base / "gh-calls").read_text() if (self.base / "gh-calls").exists() else ""
            self.assertNotIn("mergeCommitAllowed", calls)
            self.assertEqual(self.stored_merge_method(), "merge")

    def test_init_refuses_a_merge_method_the_repository_disallows(self):
        with self.fake_gh():
            self.allow_methods(squash=True, rebase=True)
            self.assertEqual(
                self.land("init", "--trunk", "main", "--mode", "merge", "--check", "./check.sh",
                          "--merge-method", "merge", ok=False),
                "land: repository does not allow merge; allowed: squash, rebase",
            )
            self.assertIn("has no landing contract", self.land("status", ok=False))
            self.allow_methods()
            self.assertEqual(
                self.land("init", "--trunk", "main", "--mode", "merge", "--check", "./check.sh", ok=False),
                "land: repository allows no merge method; allowed: none",
            )

    def test_init_keeps_merge_when_gh_cannot_read_allowed_methods(self):
        with self.fake_gh():
            (self.base / "repo-view-fails").write_text("x")
            self.allow_methods(squash=True)
            self.init(mode="merge")
            self.assertEqual(self.stored_merge_method(), "merge")
            self.init(mode="merge", merge_method="rebase")
            self.assertEqual(self.stored_merge_method(), "rebase")

    def test_a_refused_merge_method_names_the_command_that_changes_it(self):
        refusal = "GraphQL: Merge commits are not allowed on this repository. (mergePullRequest)"
        with self.fake_gh():
            self.allow_methods(merge=True, squash=True, rebase=True)
            self.init(mode="merge")
            self.queue_one()
            self.assertIn("opened PRs that merge when their checks pass: E1 (r/D1)", self.land("land"))
            (self.base / "plain-merge-fails").write_text(refusal)
            self.allow_methods(squash=True)
            self.assertEqual(
                self.land("land"),
                "queue paused: GitHub refused to merge https://github.com/o/r/pull/9: "
                f"{refusal}. Run land.py mode merge --merge-method squash, then land.py resume",
            )
            self.assertEqual(
                self.land("mode", "human", ok=False),
                "land: 1 entry is queued, landing, or awaiting merge; change the mode when the queue is empty",
            )
            self.assertEqual(
                self.land("mode", "merge", "--merge-method", "merge", ok=False),
                "land: repository does not allow merge; allowed: squash",
            )
            self.assertEqual(
                self.land("mode", "merge", "--merge-method", "squash"),
                "landing mode is now merge, merging with --squash; leases held by r/D1",
            )
            (self.base / "plain-merge-fails").unlink()
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            self.assertIn("pr merge https://github.com/o/r/pull/9 --squash", (self.base / "merge-calls").read_text())
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_the_merge_method_changes_while_an_entry_is_queued(self):
        with self.fake_gh():
            self.allow_methods(merge=True, squash=True)
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(
                self.land("mode", "merge", "--merge-method", "squash"),
                "landing mode is now merge, merging with --squash; leases held by r/D1",
            )
            self.assertEqual(self.stored_merge_method(), "squash")
            self.assertEqual(
                self.land("mode", "push", ok=False),
                "land: 1 entry is queued, landing, or awaiting merge; change the mode when the queue is empty",
            )

    def test_a_method_change_paused_in_github_keeps_the_mode_a_second_process_set(self):
        with self.fake_gh():
            self.allow_methods(merge=True, squash=True)
            self.init(mode="merge")
            self.pause_repo_view()
            (self.base / "pause-repo-view").write_text("")
            child = real_popen(
                ProcessReason.RACE,
                [sys.executable, str(SCRIPT), "--repo", str(self.work),
                 "mode", "merge", "--merge-method", "squash"],
                env=os.environ.copy())
            try:
                deadline = time.monotonic() + 10
                while not (self.base / "repo-view-waiting").exists():
                    if child.poll() is not None or time.monotonic() > deadline:
                        out, err = child.communicate()
                        self.fail(f"repo view did not pause: code {child.returncode}\n{out}\n{err}")
                    time.sleep(0.01)
                self.assertEqual(self.land("mode", "human"), "landing mode is now human")
                self.assertEqual(self.queue_one(), "E1")
                (self.base / "repo-view-release").write_text("")
                out, err = child.communicate(timeout=10)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
            self.assertEqual(child.returncode, 1, out + err)
            self.assertEqual(
                err.strip(),
                "land: 1 entry is queued, landing, or awaiting merge; change the mode when the queue is empty",
            )
            self.assertEqual(self.stored_merge_method(), "merge")
            status = self.land("status")
            self.assertTrue(status.startswith("human mode onto"), status)
            self.assertIn("queued: 1", status)

    def test_mode_help_separates_a_switch_from_a_method_change(self):
        top = real_process(ProcessReason.HELP, SCRIPT, ["--help"], env=None)
        mode = real_process(ProcessReason.HELP, SCRIPT, ["mode", "--help"], env=None)
        self.assertEqual(top.returncode, 0, top.stderr)
        self.assertEqual(mode.returncode, 0, mode.stderr)
        for text in (top.stdout, mode.stdout):
            shown = " ".join(text.split())
            self.assertIn("when the queue is empty", shown)
            self.assertIn("--merge-method may change while entries are in flight", shown)
            self.assertNotIn("while nothing is in flight", shown)

    def test_one_busy_entry_is_singular_and_two_are_plural(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            self.assertEqual(
                self.land("mode", "merge", ok=False),
                "land: 1 entry is queued, landing, or awaiting merge; change the mode when the queue is empty",
            )
            self.queue_one(path="b.txt", name="w2", holder="r/D2")
            self.land("land")
            self.assertEqual(
                self.land("mode", "push", ok=False),
                "land: 2 entries are queued, landing, or awaiting merge; change the mode when the queue is empty",
            )

    def test_the_mode_changes_only_while_nothing_is_in_flight(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            self.assertIn("awaiting merge; change the mode when the queue is empty", self.land("mode", "merge", ok=False))
            candidate = sh("git", "rev-parse", "landing/e1", cwd=self.base / "origin.git")
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.land("land")
            self.assertEqual(self.land("mode", "merge", "--merge-method", "squash"), "landing mode is now merge, merging with --squash")
            self.assertTrue(self.land("status").startswith("merge mode onto"))
            self.assertIn("needs a new contract", self.land("mode", "local", ok=False))

    def test_a_contract_written_with_the_old_auto_mode_reads_as_push(self):
        self.init(mode="auto")
        self.assertTrue(self.land("status").startswith("push mode onto"))
        self.assertEqual(self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh").split()[0], "updated")

    def test_human_mode_adopts_the_pr_a_crashed_run_created(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            (self.base / "crash-on-create").write_text("")
            result = real_process(
                ProcessReason.KILLED_CALLER, SCRIPT, ["--repo", str(self.work), "land"], env=os.environ.copy())
            self.assertEqual(result.returncode, -9)
            self.assertEqual(self.land("land"), "adopted E1 (r/D1) https://github.com/o/r/pull/9")
            self.assertIn("awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9", self.land("status", "E1"))

    def test_human_mode_bounces_a_pr_merged_with_a_head_the_queue_did_not_check(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            (self.base / "pr-state").write_text("MERGED 0123456789abcdef deadbeef")
            self.assertIn("bounced E1 (r/D1): merged at deadbeef with head 0123456789ab", self.land("land"))

    def test_human_mode_pauses_when_trunk_loses_a_landed_commit(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            sh("git", "fetch", "-q", "origin", cwd=self.work)
            merged = self.commit_on_origin("human merge")
            (self.base / "pr-state").write_text(f"MERGED {sh('git', 'rev-parse', 'landing/e1', cwd=self.base / 'origin.git')} {merged}")
            self.assertEqual(self.land("land"), "landed E1 (r/D1)")
            sh("git", "push", "-q", "--force", "origin", "HEAD:main", cwd=self.work)
            self.assertIn("queue paused: trunk no longer contains the last landed commit", self.land("land"))

    def commit_on_origin(self, message, path="merged.txt"):
        clone = self.base / "merger"
        if not clone.exists():
            sh("git", "clone", "-q", "origin.git", "merger", cwd=self.base)
        sh("git", "pull", "-q", "origin", "main", cwd=clone)
        (clone / path).write_text(message)
        sha = self.commit(message, cwd=clone)
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=clone)
        return sha

    def test_a_failed_pin_leaves_nothing_queued(self):
        self.init()
        wrapper = self.base / "bin"
        wrapper.mkdir()
        real = sh("sh", "-c", "command -v git", cwd=self.base)
        (wrapper / "git").write_text(f'#!/bin/sh\ncase "$*" in *refs/landing/pins/*) exit 1;; esac\nexec {real} "$@"\n')
        (wrapper / "git").chmod(0o755)
        sha = self.worker("w1", {"a.txt": "agent\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        with mock.patch.dict(os.environ, {"PATH": f"{wrapper}:{os.environ['PATH']}"}):
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER, ok=False)
        self.assertEqual(self.land("status"), "push mode onto refs/remotes/origin/main. leases held: 1.")

    def test_a_policy_rejection_pauses_the_queue_after_one_check_run(self):
        self.land("init", "--trunk", "main", "--mode", "auto", "--check", f"echo run >> {self.base}/runs")
        hook = self.base / "origin.git/hooks/pre-receive"
        hook.write_text("#!/bin/sh\necho rejected by policy >&2\nexit 1\n")
        hook.chmod(0o755)
        self.queue_one()
        self.assertIn("queue paused: push rejected: remote: rejected by policy", self.land("land"))
        self.assertEqual((self.base / "runs").read_text(), "run\n")
        self.assertIn("E1 queued", self.land("status", "E1"))

    def test_a_check_that_escapes_its_group_cannot_hold_the_queue(self):
        script = self.base / "escape.py"
        script.write_text("import os, time\nos.setsid()\ntime.sleep(20)\n")
        self.land("init", "--trunk", "main", "--mode", "auto", "--check", f"{sys.executable} {script}; true", "--timeout", "1")
        self.queue_one()
        started = __import__("time").monotonic()
        self.assertIn("timed out", self.land("land"))
        self.assertLess(__import__("time").monotonic() - started, 12)

    def test_non_utf8_check_output_and_file_contents_land(self):
        self.land("init", "--trunk", "main", "--mode", "auto", "--check", "printf '\\377'")
        self.worker("w1", {"seed.txt": "seed\n"})
        (self.base / "w1/latin.txt").write_bytes(b"caf\xe9\n")
        sha = self.commit("latin", cwd=self.base / "w1")
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "seed.txt,latin.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_non_ascii_file_names_match_their_lease(self):
        self.init()
        self.assertEqual(self.queue_one(path="café.txt"), "E1")
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_landing_works_without_a_git_identity(self):
        self.init()
        self.queue_one()
        empty = self.base / "home"
        empty.mkdir()
        bare_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        bare_env.update({"HOME": str(empty), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})
        result = real_process(
            ProcessReason.INHERITED_ENV, SCRIPT, ["--repo", str(self.work), "land"], env=bare_env)
        self.assertEqual(result.stdout.strip(), "landed E1 (r/D1)", result.stderr)

    def test_fingerprint_sees_whitespace_and_ignores_line_numbers(self):
        repo = self.work
        base = sh("git", "rev-parse", "HEAD", cwd=repo)
        (repo / "lib/x.py").write_text("lib/x.py\n    return 1\n")
        indented = self.commit("indent")
        sh("git", "reset", "-q", "--hard", base, cwd=repo)
        (repo / "lib/x.py").write_text("lib/x.py\n  return 1\n")
        other = self.commit("indent 2")
        self.assertNotEqual(land.fingerprint(repo, base, indented), land.fingerprint(repo, base, other))
        sh("git", "reset", "-q", "--hard", base, cwd=repo)
        (repo / "lib/x.py").write_text("header\nlib/x.py\n")
        moved = self.commit("header")
        (repo / "lib/x.py").write_text("header\nlib/x.py\n    return 1\n")
        indented_after_header = self.commit("indent after header")
        self.assertEqual(land.fingerprint(repo, base, indented), land.fingerprint(repo, moved, indented_after_header))

    def test_a_check_that_hangs_is_killed_at_the_timeout(self):
        self.land("init", "--trunk", "main", "--mode", "auto", "--check", "sleep 30", "--timeout", "1")
        sha = self.worker("w1", {"a.txt": "agent\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertIn("bounced E1 (r/D1): checks failed: `sleep 30` timed out", self.land("land"))

    def test_the_contract_refuses_a_conflicting_init_and_other_clones(self):
        self.init()
        self.assertIn("already sets trunk='main'", self.land("init", "--trunk", "dev", "--mode", "auto", ok=False))
        sh("git", "clone", "-q", "origin.git", "other", cwd=self.base)
        _code, _out, err = self.run_land(self.base / "other", "status")
        self.assertIn("has no landing contract", err)

    def test_an_exclusive_slot_waits_for_every_other_slot_and_refuses_nesting(self):
        import time
        governor = self.base / "state/pstack-t3/governor"
        governor.mkdir(parents=True)
        (governor / "governor.json").write_text(json.dumps({"slots": 2}))
        marks = self.base / "marks"
        # land.py's slot gate reads LAND_SLOT. The queue sets it around this suite, so the
        # subprocesses under test must not inherit it. The nested command still sets it itself.
        env = {key: value for key, value in os.environ.items() if key != "LAND_SLOT"}
        worker = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, str(SCRIPT), "slot", "--", "sh", "-c", f"echo worker-start >> {marks}; sleep 2; echo worker-end >> {marks}"],
            env=env, stdout=None, stderr=None)
        time.sleep(0.5)
        bench = real_process(
            ProcessReason.GOVERNOR, SCRIPT,
            ["slot", "--exclusive", "--", "sh", "-c", f"echo bench >> {marks}"],
            env=env, timeout=30)
        worker.wait(timeout=30)
        self.assertEqual(bench.returncode, 0, bench.stderr)
        self.assertEqual(marks.read_text().split(), ["worker-start", "worker-end", "bench"])
        nested = real_process(
            ProcessReason.GOVERNOR, SCRIPT,
            ["slot", "--", sys.executable, str(SCRIPT), "slot", "--exclusive", "--", "true"],
            env=env, timeout=30)
        self.assertNotEqual(nested.returncode, 0)
        self.assertIn("must be the outermost slot", nested.stderr)

    def test_human_mode_uses_the_submitted_pr_title_and_body(self):
        log = self.base / "gh-log"
        with self.fake_gh():
            fake = self.base / "gh"
            fake.write_text(fake.read_text().replace('if args[:2] == ["pr", "create"]:',
                                                     f'open({str(log)!r}, "a").write(repr(args) + "\\n")\nif args[:2] == ["pr", "create"]:'))
            self.init(mode="human")
            sha = self.worker("w1", {"a.txt": "agent\n"})
            self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
            body = self.base / "body.md"
            body.write_text("Cuts load time by 300 ms.\n")
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER,
                      "--title", "Faster load", "--body-file", str(body))
            self.land("land")
        create = [line for line in log.read_text().splitlines() if "'create'" in line][0]
        self.assertIn("'--title', 'Faster load'", create)
        self.assertIn("'--body', 'Cuts load time by 300 ms.\\n'", create)
        self.assertNotIn("Queued by", create)
        self.assertNotIn("Reviewed by", create)

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

    def test_human_mode_without_a_submitted_body_uses_the_commit_body(self):
        create = self.create_args_without_a_submitted_body("-m", "Faster load", "-m", "Cuts load time by 300 ms.\n\nMeasured on a cold start.")
        self.assertEqual(create[create.index("--title") + 1], "Faster load")
        self.assertEqual(create[create.index("--body") + 1], "Cuts load time by 300 ms.\n\nMeasured on a cold start.")
        self.assertNotIn("Queued by", " ".join(create))
        self.assertNotIn("Reviewed by", " ".join(create))

    def test_human_mode_without_any_body_opens_the_pr_with_an_empty_body(self):
        create = self.create_args_without_a_submitted_body("-m", "Faster load")
        self.assertEqual(create[create.index("--body") + 1], "")
        self.assertNotIn("Queued by", " ".join(create))
        self.assertNotIn("Reviewed by", " ".join(create))

    def store_item(self, branch, kind, goal):
        store = self.base / "state/pstack-t3/brigade/proj/store"
        store.mkdir(parents=True, exist_ok=True)
        (store / "restaurant.json").write_text(json.dumps({"projectRoot": str(self.work)}))
        (store / "goals.tsv").write_text("id\tname\nG2\trules in the tool\n")
        (store / "dishes.tsv").write_text(
            "id\tat\tstate\tstation\ttickets\ttask\tthread\tbranch\tpr\tsha\tsummary\ttimebox\tlease\tpaths\t"
            f"reported\tkind\tgoal\nD255\t2026-10-14T13:29:20.318522+00:00\tin-progress\tbug-fix\tT606\t\t\t{branch}\t\t\t"
            f"batch\t90\tL293\t\t\t{kind}\t{goal}\n")

    def test_the_pr_body_carries_the_kind_and_the_goal_of_the_items_row(self):
        log = self.base / "gh-log"
        with self.fake_gh():
            fake = self.base / "gh"
            fake.write_text(fake.read_text().replace('if args[:2] == ["pr", "create"]:',
                                                     f'open({str(log)!r}, "a").write(repr(args) + "\\n")\nif args[:2] == ["pr", "create"]:'))
            self.init(mode="human")
            cases = (
                ("polish", "G2", "Kind: polish. Goal: rules in the tool"),
                ("polish", "", "Kind: polish"),
                ("", "G2", "Goal: rules in the tool"),
                ("", "", ""),
            )
            for index, (kind, goal, expected) in enumerate(cases, 1):
                with self.subTest(kind=kind, goal=goal):
                    branch = f"w{index}"
                    self.store_item(branch, kind, goal)
                    sh("git", "checkout", "-q", "-b", branch, "origin/main", cwd=self.work)
                    (self.work / "a.txt").write_text(f"agent {index}\n")
                    sha = self.commit(branch)
                    lease = self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
                    self.land("submit", "--holder", "r/D1", "--branch", branch, "--sha", sha, "--lease",
                              lease, "--reviewer", REVIEWER)
                    self.land("land")
                    self.assertTrue(log.exists(), "the queue opened no pull request")
                    create = ast.literal_eval([line for line in log.read_text().splitlines() if "'create'" in line][-1])
                    self.assertEqual(create[create.index("--body") + 1], expected)
                    log.unlink()

    def test_a_sole_store_without_a_matching_record_leaves_the_submitted_body_alone(self):
        """Pin review finding D255-review-1 R1. A sole foreign store must not lend its kind and goal."""
        log = self.base / "gh-log"
        with self.fake_gh():
            fake = self.base / "gh"
            fake.write_text(fake.read_text().replace('if args[:2] == ["pr", "create"]:',
                                                     f'open({str(log)!r}, "a").write(repr(args) + "\\n")\nif args[:2] == ["pr", "create"]:'))
            self.init(mode="human")
            body_file = self.base / "submitted.md"
            body_file.write_text("Reviewed body")
            cases = ("missing", "foreign")
            for index, case in enumerate(cases, 1):
                with self.subTest(record=case):
                    branch = f"v{index}"
                    self.store_item(branch, "polish", "G2")
                    store = self.base / "state/pstack-t3/brigade/proj/store"
                    if case == "missing":
                        (store / "restaurant.json").unlink()
                    else:
                        (store / "restaurant.json").write_text(json.dumps({"projectRoot": "/foreign/root"}))
                    sh("git", "checkout", "-q", "-b", branch, "origin/main", cwd=self.work)
                    (self.work / "a.txt").write_text(f"agent {index}\n")
                    sha = self.commit(branch)
                    lease = self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
                    self.land("submit", "--holder", "r/D1", "--branch", branch, "--sha", sha, "--lease",
                              lease, "--reviewer", REVIEWER, "--body-file", str(body_file))
                    self.land("land")
                    self.assertTrue(log.exists(), "the queue opened no pull request")
                    create = ast.literal_eval([line for line in log.read_text().splitlines() if "'create'" in line][-1])
                    self.assertEqual(create[create.index("--body") + 1], "Reviewed body")
                    log.unlink()

    def test_a_finished_row_before_a_non_utf8_unfinished_tail_is_read(self):
        path = self.base / "dishes.tsv"
        path.write_bytes(b"id\tbranch\nD1\tw1\n" + b"\xff\xfe")
        self.assertEqual(land._table_rows(path), [{"id": "D1", "branch": "w1"}])

    def test_a_store_from_before_pr_text_columns_is_migrated(self):
        self.init()
        with land.Store.for_repo(self.work) as store:
            store.db.execute("DROP TABLE entry")
            store.db.executescript(land.SCHEMA)
            self.assertNotIn("title", {row["name"] for row in store.db.execute("PRAGMA table_info(entry)")})
        self.assertEqual(self.queue_one(), "E1")
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_a_command_leaves_no_open_landing_database_after_it_returns(self):
        self.init()
        database = os.path.realpath(next((self.base / "state").glob("pstack-t3/landing/*/land.db")))
        opened = []
        real_connect = land.sqlite3.connect

        def connect(path, *args, **kwargs):
            connection = real_connect(path, *args, **kwargs)
            opened.append((connection, os.path.realpath(path)))
            return connection

        def call(argv):
            opened.clear()
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(land.sqlite3, "connect", connect), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = land.main(argv)
            return code, out.getvalue(), err.getvalue()

        def assert_closed():
            self.assertTrue(opened, "the command opened no landing database")
            for connection, path in opened:
                self.assertEqual(path, database)
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")

        base = ["--repo", str(self.work)]
        clash = ("land: the contract already sets trunk='main'; change the mode with land.py mode; "
                 "changing trunk or remote needs a new repository contract\n")
        self.assertEqual(call([*base, "status", "E9"]), (1, "", "land: no E9\n"))
        assert_closed()
        self.assertEqual(call([*base, "init", "--trunk", "other", "--mode", "auto"]), (1, "", clash))
        assert_closed()

        with land.Store.for_repo(self.work) as store:
            directory = store.dir
            common = store.contract["commonDir"]
            store.db.execute("INSERT OR REPLACE INTO contract VALUES ('commonDir', ?)", (json.dumps(str(self.base)),))
        opened.clear()
        with mock.patch.object(land.sqlite3, "connect", connect):
            with self.assertRaises(land.LandError) as caught:
                land.Store.for_repo(self.work)
        assert_closed()
        self.assertEqual(str(caught.exception), f"store {directory} belongs to {self.base}, not {common}")
        with land.Store(directory) as store:
            store.db.execute("INSERT OR REPLACE INTO contract VALUES ('commonDir', ?)", (json.dumps(common),))

        opened.clear()
        broken = OSError("migration refused")
        with mock.patch.object(land.sqlite3, "connect", connect), \
                mock.patch.object(land, "migrate_entry_ids", side_effect=broken):
            with self.assertRaises(OSError) as failed:
                land.Store(directory)
        self.assertIs(failed.exception, broken)
        assert_closed()

        self.assertEqual(call([*base, "init", "--trunk", "main", "--mode", "auto"]),
                         (0, f"updated {directory}\n", ""))
        assert_closed()

    def test_a_store_close_after_a_directory_failure_raises_nothing(self):
        """self.db is assigned before the directory conversion, so a close after that failure is a no-op."""
        store = land.Store.__new__(land.Store)
        with self.assertRaises(TypeError):
            store.__init__(None)
        store.close()
        store.close()

    def entry_columns(self):
        with self.raw_db() as db:
            return [row[1] for row in db.execute("PRAGMA table_info(entry)")]

    def test_a_store_without_onto_or_autoincrement_opens_with_both_and_keeps_its_entry(self):
        self.init()
        self.assertEqual(self.queue_one(), "E1")
        self.rebuild_entry_without_autoincrement()
        self.assertNotIn("onto", self.entry_columns())
        self.assertEqual(self.land("status", "E1"), "E1 queued (r/D1, w1)")
        self.assertIn("onto", self.entry_columns())
        with self.raw_db() as db:
            self.assertIn("AUTOINCREMENT", db.execute("SELECT sql FROM sqlite_master WHERE name = 'entry'").fetchone()[0])
            self.assertEqual(db.execute("SELECT id, holder, onto FROM entry").fetchall(), [(1, "r/D1", "")])

    def test_nested_slots_do_not_deadlock_with_one_slot(self):
        governor = self.base / "state/pstack-t3/governor"
        governor.mkdir(parents=True)
        (governor / "governor.json").write_text(json.dumps({"slots": 1}))
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT,
            ["slot", "--", sys.executable, str(SCRIPT), "slot", "--", "true"],
            env=os.environ.copy(), timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_worker_slots_span_directories_and_leave_landing_capacity(self):
        import signal

        governor = self.base / "state/pstack-t3/governor"
        governor.mkdir(parents=True)
        (governor / "governor.json").write_text(json.dumps({"slots": 2}))
        env = {key: value for key, value in os.environ.items() if key != "LAND_SLOT"}
        directories = [self.base / f"command-{index}" for index in range(3)]
        releases = [self.base / f"release-{index}" for index in range(3)]
        retry = self.base / "retry"
        handles = []
        processes = []

        def wait_for(path):
            deadline = time.monotonic() + 20
            while not path.exists():
                if time.monotonic() >= deadline:
                    self.fail(f"barrier not reached: {path}")
                time.sleep(0.01)

        command = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "release = os.open(sys.argv[1], os.O_RDONLY)\n"
            "Path('started').touch()\n"
            "assert os.read(release, 1) == b'x'\n"
            "os.close(release)\n"
            "Path('finished').touch()\n"
        )
        third = (
            "import os, sys\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(SCRIPT.parent)!r})\n"
            "import land\n"
            "count = 0\n"
            "def blocked(seconds):\n"
            "    global count\n"
            "    count += 1\n"
            "    gate = os.open(sys.argv[1], os.O_RDONLY)\n"
            "    Path(f'waiting-{count}').touch()\n"
            "    assert os.read(gate, 1) == b'x'\n"
            "    os.close(gate)\n"
            "land.time.sleep = blocked\n"
            "sys.exit(land.main(sys.argv[2:]))\n"
        )
        try:
            for path in [*releases, retry]:
                os.mkfifo(path)
                # Keep both ends open so a barrier write never waits for a reader.
                handles.append(os.open(path, os.O_RDWR | os.O_NONBLOCK))
            for directory in directories:
                directory.mkdir()
            for index in range(2):
                processes.append(real_popen(
                    ProcessReason.GOVERNOR,
                    [sys.executable, str(SCRIPT), "slot", "--",
                     sys.executable, "-c", command, str(releases[index])],
                    env=env, cwd=directories[index], start_new_session=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                wait_for(directories[index] / "started")
            processes.append(real_popen(
                ProcessReason.GOVERNOR,
                [sys.executable, "-c", third, str(retry), "slot", "--",
                 sys.executable, "-c", command, str(releases[2])],
                env=env, cwd=directories[2], start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait_for(directories[2] / "waiting-1")
            self.assertFalse((directories[2] / "started").exists())
            os.write(handles[3], b"x")
            wait_for(directories[2] / "waiting-2")
            self.assertFalse((directories[2] / "started").exists())
            self.assertTrue(all(process.poll() is None for process in processes))

            landing_mark = self.base / "landing-progress"
            probe = (
                "import sys\n"
                "from pathlib import Path\n"
                f"sys.path.insert(0, {str(SCRIPT.parent)!r})\n"
                "import land\n"
                "with land.slot('landing'):\n"
                f"    Path({str(landing_mark)!r}).touch()\n"
            )
            result = _run(
                [sys.executable, "-c", probe], env=env, timeout=20, reason=ProcessReason.GOVERNOR)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(landing_mark.exists())
            self.assertFalse((directories[2] / "started").exists())

            os.write(handles[0], b"x")
            self.assertEqual(processes[0].wait(timeout=20), 0)
            self.assertTrue((directories[0] / "finished").exists())
            os.write(handles[3], b"x")
            wait_for(directories[2] / "started")
            self.assertIsNone(processes[1].poll())
            for index in (1, 2):
                os.write(handles[index], b"x")
                self.assertEqual(processes[index].wait(timeout=20), 0)
                self.assertTrue((directories[index] / "finished").exists())
        finally:
            for process in processes:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=20)
            for handle in handles:
                os.close(handle)

    def test_a_slot_alone_returns_the_command_status_and_prints_nothing(self):
        env = {key: value for key, value in os.environ.items() if key != "LAND_SLOT"}

        def slot(*arguments):
            result = real_process(
                ProcessReason.GOVERNOR, SCRIPT, ["slot", *arguments], env=env, timeout=20)
            return result.returncode, result.stdout, result.stderr

        self.assertEqual(slot("--", "sh", "-c", "exit 7"), (7, "", ""))
        self.assertEqual(slot("--exclusive", "--", "true"), (0, "", ""))
        self.assertEqual(slot(), (1, "", "land: slot needs a command after --\n"))

    def test_a_file_finder_leaves_among_the_tickets_does_not_stop_a_slot(self):
        tickets = self.base / "state/pstack-t3/governor/tickets"
        tickets.mkdir(parents=True)
        (tickets / ".DS_Store").write_bytes(b"\0Bud1")
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT, ["slot", "--", "true"], env=self.slot_env(), timeout=20)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), [".DS_Store"])

    def test_a_ticket_shaped_fifo_directory_or_symlink_does_not_stop_a_slot(self):
        outside = self.base / "outside.pipe"
        os.mkfifo(outside)
        failures = []

        def admit(name, setup):
            home = self.base / "homes" / name
            tickets = home / "pstack-t3/governor/tickets"
            tickets.mkdir(parents=True)
            setup(tickets)
            env = {**self.slot_env(), "XDG_STATE_HOME": str(home)}
            try:
                result = real_process(
                    ProcessReason.GOVERNOR, SCRIPT, ["slot", "--", "true"], env=env, timeout=20)
            except subprocess.TimeoutExpired:
                failures.append(f"{name} timed out")
                return
            if (result.returncode, result.stderr) != (0, ""):
                tail = result.stderr.strip().splitlines()[-1] if result.stderr else ""
                failures.append(f"{name} rc={result.returncode} {tail}")

        admit("fifo", lambda tickets: os.mkfifo(tickets / "1-worker"))
        admit("directory", lambda tickets: (tickets / "1-worker").mkdir())
        admit("symlink", lambda tickets: (tickets / "1-worker").symlink_to(outside))
        self.assertEqual(failures, [])
        for name in ("fifo", "directory", "symlink"):
            left = self.base / "homes" / name / "pstack-t3/governor/tickets"
            self.assertEqual([path.name for path in left.iterdir()], [], name)
        self.assertTrue(outside.is_fifo())

        slots = SlotCallers(self, 1)
        tickets = slots.governor / "tickets"
        tickets.mkdir()
        directory = tickets / "1-worker"
        directory.mkdir()
        (directory / "child").write_text("stay")
        holder = slots.enter(slots.start("holder"))
        waiter = slots.queued(slots.start("waiter", marked=True))
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), ["1-worker", "2-worker"])
        self.assertTrue((tickets / "2-worker").is_file())
        slots.release(holder)
        slots.release(slots.enter(waiter))
        self.assertEqual([path.name for path in tickets.iterdir()], ["1-worker"])
        self.assertEqual((directory / "child").read_text(), "stay")

    def test_a_slot_probe_on_a_read_only_ticket_keeps_a_held_ticket_and_removes_a_free_one(self):
        tickets = self.base / "probe-tickets"
        tickets.mkdir()
        held_path = tickets / "1-worker"
        free_path = tickets / "2-worker"
        held_path.write_bytes(b"")
        free_path.write_bytes(b"")
        holder = real_popen(
            ProcessReason.LOCK,
            [sys.executable, "-c",
             "import fcntl, os, sys, time\n"
             "fd = os.open(sys.argv[1], os.O_RDWR)\n"
             "fcntl.flock(fd, fcntl.LOCK_EX)\n"
             "sys.stdout.write('held\\n')\n"
             "sys.stdout.flush()\n"
             "time.sleep(60)\n",
             str(held_path)],
            env=None, start_new_session=True)

        def stop():
            if holder.poll() is None:
                os.killpg(holder.pid, signal.SIGKILL)
            holder.wait(timeout=20)
            holder.stdout.close()
            holder.stderr.close()

        self.addCleanup(stop)
        self.assertEqual(holder.stdout.readline(), "held\n")
        recorded = []
        real = land.fcntl.flock

        def nfs_flock(handle, operation):
            descriptor = handle.fileno()
            read_only = (fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE) == os.O_RDONLY
            exclusive = (operation & (fcntl.LOCK_SH | fcntl.LOCK_EX | fcntl.LOCK_UN)) == fcntl.LOCK_EX
            if read_only and exclusive:
                raise OSError(errno.EBADF, "Bad file descriptor")
            recorded.append((read_only, operation))
            return real(handle, operation)

        with mock.patch.object(land.fcntl, "flock", nfs_flock):
            found = land._waiting(tickets, None)
        self.assertEqual(found, [(1, "worker")])
        self.assertEqual([path.name for path in tickets.iterdir()], ["1-worker"])
        shared = fcntl.LOCK_SH | fcntl.LOCK_NB
        self.assertEqual(recorded, [(True, shared), (True, shared)])

    def test_a_ticket_hard_linked_to_the_turn_mutex_does_not_stop_a_slot(self):
        governor = self.base / "state/pstack-t3/governor"
        tickets = governor / "tickets"
        tickets.mkdir(parents=True)
        lock = governor / "tickets.lock"
        lock.write_bytes(b"")
        os.link(lock, tickets / "1-worker")
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT, ["slot", "--", "true"],
            env=self.slot_env(), timeout=20)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual([path.name for path in tickets.iterdir()], [])
        self.assertEqual(lock.stat().st_nlink, 1)

    def test_a_sigkill_waiter_under_umask_0o444_does_not_stop_later_callers(self):
        """The waiter is its own process so the test can set that process's umask and kill it with SIGKILL."""
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        waiter = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, "-c",
             "import os, sys\n"
             "sys.path.insert(0, sys.argv[1])\n"
             "import land\n"
             "os.umask(0o444)\n"
             "sys.exit(land.main(['slot', '--', 'true']))\n",
             str(SCRIPT.parent)],
            env=slots.env, stdout=None, stderr=subprocess.PIPE, start_new_session=True)

        def stop():
            if waiter.poll() is None:
                os.kill(waiter.pid, signal.SIGKILL)
                waiter.wait(timeout=20)
            waiter.stderr.close()

        self.addCleanup(stop)
        ticket = slots.governor / "tickets" / "1-worker"
        slots.wait(lambda: ticket.exists(), "the restricted-umask waiter created its ticket")
        mode = ticket.stat().st_mode & 0o777
        os.kill(waiter.pid, signal.SIGKILL)
        waiter.wait(timeout=20)
        slots.release(holder)
        statuses = []
        for _ in range(2):
            result = real_process(
                ProcessReason.GOVERNOR, SCRIPT, ["slot", "--", "true"],
                env=slots.env, timeout=20)
            statuses.append(result.returncode)
        self.assertEqual((bool(mode & 0o400), statuses), (True, [0, 0]), oct(mode))

    def test_a_ticket_name_hard_linked_to_an_unreadable_file_does_not_stop_a_slot(self):
        """The slot command is a process because the test asserts its exit status."""
        tickets = self.base / "state/pstack-t3/governor/tickets"
        tickets.mkdir(parents=True)
        outside = self.base / "outside-unreadable"
        outside.write_bytes(b"keep")
        outside.chmod(0)
        os.link(outside, tickets / "1-worker")
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT, ["slot", "--", "true"],
            env=self.slot_env(), timeout=20)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        self.assertEqual([path.name for path in tickets.iterdir()], [])
        self.assertEqual(outside.stat().st_nlink, 1)
        outside.chmod(0o600)
        self.assertEqual(outside.read_bytes(), b"keep")

    def test_two_names_of_one_live_ticket_do_not_keep_a_free_landing_caller_waiting(self):
        """The waiter and the landing caller are processes so the test can order their attempts."""
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        waiter = slots.start("waiter", stepped=True)
        self.assertEqual(slots.settle(waiter), "waiting")
        tickets = slots.governor / "tickets"
        os.link(tickets / "1-worker", tickets / "2-exclusive")
        mutex = open(slots.governor / "tickets.lock", "a")
        self.addCleanup(mutex.close)
        fcntl.flock(mutex, fcntl.LOCK_EX)
        try:
            found = land._waiting(tickets, None)
        finally:
            fcntl.flock(mutex, fcntl.LOCK_UN)
        slots.release(holder)
        landing = slots.start("landing", pool="landing", stepped=True)
        landing_state = slots.settle(landing)
        self.assertEqual((found, landing_state), ([], "started"))
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), [])
        self.assertEqual(slots.step(waiter), "started")
        held = {}
        for word, name, *files in slots.events():
            if word == "exit":
                held.pop(name, None)
                continue
            own = set(files)
            for other, theirs in held.items():
                self.assertEqual(own & theirs, set(), f"{name} and {other}")
            held[name] = own
        slots.release(landing)
        slots.release(waiter)

    def test_a_slot_runs_when_its_ticket_is_removed_while_it_waits(self):
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        waiter = slots.start("waiter", stepped=True)
        self.assertEqual(slots.settle(waiter), "waiting")
        names = sorted(path.name for path in (slots.governor / "tickets").iterdir())
        self.assertEqual(names, ["1-worker"])
        (slots.governor / "tickets" / names[0]).unlink()
        slots.release(holder)
        self.assertEqual(slots.step(waiter), "started")
        slots.release(waiter)
        self.assertEqual(slots.entries(), ["holder", "waiter"])

    def test_a_displaced_exclusive_that_retries_while_the_slot_is_busy_keeps_later_callers_waiting(self):
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        tickets = slots.governor / "tickets"
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), ["1-exclusive"])
        (tickets / "1-exclusive").unlink()
        self.assertEqual(slots.step(exclusive), "waiting")
        self.assertEqual([path.stat().st_nlink for path in tickets.iterdir()], [1])
        ordinary = slots.start("ordinary", stepped=True)
        self.assertEqual(slots.settle(ordinary), "waiting")
        slots.release(holder)
        self.assertEqual(slots.step(ordinary), "waiting")
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)
        self.assertEqual(slots.step(ordinary), "started")
        slots.release(ordinary)
        self.assertEqual(slots.entries(), ["holder", "exclusive", "ordinary"])
        self.assertEqual(slots.peak(), 1)
        held = {}
        for word, name, *files in slots.events():
            if word == "exit":
                held.pop(name, None)
                continue
            own = set(files)
            for other, theirs in held.items():
                self.assertEqual(own & theirs, set(), f"{name} and {other}")
            held[name] = own

    def test_a_number_2_retry_with_a_number_3_alias_cannot_keep_a_landing_caller_waiting(self):
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        first = slots.start("first", stepped=True)
        self.assertEqual(slots.settle(first), "waiting")
        second = slots.start("second", stepped=True)
        self.assertEqual(slots.settle(second), "waiting")
        tickets = slots.governor / "tickets"
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), ["1-worker", "2-worker"])
        os.link(tickets / "1-worker", tickets / "3-exclusive")
        self.assertEqual(slots.step(second), "waiting")
        self.assertEqual(sorted(path.name for path in tickets.iterdir()), ["2-worker"])
        self.assertEqual((tickets / "2-worker").stat().st_nlink, 1)
        slots.release(holder)
        landing = slots.start("landing", pool="landing", stepped=True)
        self.assertEqual(slots.settle(landing), "started")
        self.assertFalse(first.started)
        self.assertFalse(second.started)
        slots.release(landing)
        self.assertEqual(slots.step(second), "started")
        slots.release(second)
        self.assertEqual(slots.step(first), "started")
        slots.release(first)
        self.assertEqual(slots.entries(), ["holder", "landing", "second", "first"])
        self.assertEqual(slots.peak(), 1)
        held = {}
        for word, name, *files in slots.events():
            if word == "exit":
                held.pop(name, None)
                continue
            own = set(files)
            for other, theirs in held.items():
                self.assertEqual(own & theirs, set(), f"{name} and {other}")
            held[name] = own

    def test_a_filesystem_that_refuses_the_turn_mutex_flock_does_not_run_the_command(self):
        self.governor(1)
        marker = self.base / "ran"
        real = fcntl.flock

        def slot_status(refused, code):
            def flock(handle, operation):
                if operation == refused:
                    raise OSError(code, os.strerror(code))
                return real(handle, operation)

            stderr = io.StringIO()
            with mock.patch.dict(os.environ), mock.patch.object(land.fcntl, "flock", flock), \
                    contextlib.redirect_stderr(stderr):
                os.environ.pop("LAND_SLOT", None)
                return land.main(["slot", "--", "touch", str(marker)]), stderr.getvalue()

        for code in (errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOLCK):
            self.assertEqual(
                slot_status(fcntl.LOCK_EX, code),
                (1, "land: this filesystem does not support the slot governor's ordering lock\n"))
        with self.assertRaises(OSError) as raised:
            slot_status(fcntl.LOCK_EX | fcntl.LOCK_NB, errno.ENOLCK)
        self.assertEqual(raised.exception.errno, errno.ENOLCK)
        self.assertFalse(marker.exists())
        self.assert_slots_free()

    def test_a_hold_names_its_slot_files_and_a_nested_slot_changes_neither_variable(self):
        self.governor(2)

        def seen():
            return os.environ.get("LAND_SLOT"), os.environ.get("LAND_SLOT_FILES")

        with mock.patch.dict(os.environ):
            os.environ.pop("LAND_SLOT", None)
            os.environ.pop("LAND_SLOT_FILES", None)
            with land.slot("worker"):
                self.assertEqual(seen(), ("worker", "worker-0"))
                with land.slot("landing"):
                    self.assertEqual(seen(), ("worker", "worker-0"))
                self.assertEqual(seen(), ("worker", "worker-0"))
            self.assertEqual(seen(), (None, None))
            with land.slot("worker", count=2):
                self.assertEqual(seen(), ("worker", "worker-0 worker-1"))
                with land.slot("worker", count=2):
                    self.assertEqual(seen(), ("worker", "worker-0 worker-1"))
                self.assertEqual(seen(), ("worker", "worker-0 worker-1"))
            self.assertEqual(seen(), (None, None))
            with land.slot("landing"):
                self.assertEqual(seen(), ("landing", "landing-0"))
            with land.exclusive_slot():
                self.assertEqual(seen(), ("exclusive", "landing-0 worker-0 worker-1"))
            self.assertEqual(seen(), (None, None))

    def test_an_earlier_ordinary_waiter_runs_before_a_later_exclusive(self):
        slots = SlotCallers(self, 2)
        holders = [slots.enter(slots.start(f"holder-{index}")) for index in range(2)]
        ordinary = slots.start("ordinary", stepped=True)
        self.assertEqual(slots.settle(ordinary), "waiting")
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        for holder in holders:
            slots.release(holder)
        self.assertEqual(slots.step(exclusive), "waiting")
        self.assertEqual(slots.step(ordinary), "started")
        self.assertEqual(slots.step(exclusive), "waiting")
        slots.release(ordinary)
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)
        self.assertEqual(slots.entries(), ["holder-0", "holder-1", "ordinary", "exclusive"])

    def test_an_earlier_exclusive_waiter_runs_before_a_later_ordinary_once_every_slot_is_free(self):
        slots = SlotCallers(self, 2)
        holders = [slots.enter(slots.start(f"holder-{index}")) for index in range(2)]
        holders.append(slots.enter(slots.start("landing", pool="landing")))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        ordinary = slots.start("ordinary", stepped=True)
        self.assertEqual(slots.settle(ordinary), "waiting")
        for holder in holders:
            slots.release(holder)
        self.assertEqual(slots.step(ordinary), "waiting")
        self.assertEqual(slots.step(exclusive), "started")
        self.assertEqual(slots.step(ordinary), "waiting")
        slots.release(exclusive)
        self.assertEqual(slots.step(ordinary), "started")
        slots.release(ordinary)
        self.assertEqual(slots.entries(), ["holder-0", "holder-1", "landing", "exclusive", "ordinary"])

    def test_an_ordinary_caller_takes_a_free_worker_slot_while_an_exclusive_waits(self):
        slots = SlotCallers(self, 2)
        holder = slots.enter(slots.start("holder"))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        ordinary = slots.enter(slots.start("ordinary"))
        self.assertFalse(exclusive.started)
        self.assertEqual(slots.step(exclusive), "waiting")
        slots.release(holder)
        slots.release(ordinary)
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)
        self.assertEqual(slots.entries(), ["holder", "ordinary", "exclusive"])

    def test_an_exclusive_waiter_holds_no_slot_lock_while_it_waits(self):
        slots = SlotCallers(self, 2)
        holder = slots.enter(slots.start("holder"))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        free = []
        for name in ("landing-0", "worker-0", "worker-1"):
            with open(slots.governor / name, "a") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                free.append(name)
        self.assertEqual(free, ["landing-0", "worker-1"])
        slots.release(holder)
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)

    def test_a_two_slot_caller_behind_one_busy_slot_holds_nothing(self):
        slots = SlotCallers(self, 2)
        holder = slots.enter(slots.start("holder"))
        wide = slots.start("wide", "--slots", "2", stepped=True)
        self.assertEqual(slots.settle(wide), "waiting")
        free = []
        for name in ("landing-0", "worker-0", "worker-1"):
            with open(slots.governor / name, "a") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                free.append(name)
        self.assertEqual(free, ["landing-0", "worker-1"])
        self.assertEqual(os.listdir(slots.governor / "tickets"), ["1-worker"])
        slots.release(holder)
        self.assertEqual(slots.step(wide), "started")
        slots.release(wide)

    def test_a_later_one_slot_caller_does_not_pass_an_earlier_two_slot_ticket(self):
        slots = SlotCallers(self, 2)
        holder = slots.enter(slots.start("holder"))
        wide = slots.start("wide", "--slots", "2", stepped=True)
        self.assertEqual(slots.settle(wide), "waiting")
        narrow = slots.start("narrow", stepped=True)
        self.assertEqual(slots.settle(narrow), "waiting")
        slots.release(holder)
        self.assertEqual(slots.step(wide), "started")
        self.assertIn(("enter", "wide", "worker-0", "worker-1"), slots.events())
        self.assertEqual(slots.step(narrow), "waiting")
        slots.release(wide)
        self.assertEqual(slots.step(narrow), "started")
        slots.release(narrow)
        self.assertEqual(slots.entries(), ["holder", "wide", "narrow"])

    def test_slots_over_the_governor_are_refused_and_the_command_does_not_run(self):
        directory = self.governor(2)
        marker = self.base / "ran"

        def slot_status(*flags):
            stderr = io.StringIO()
            with mock.patch.dict(os.environ), contextlib.redirect_stderr(stderr):
                os.environ.pop("LAND_SLOT", None)
                return land.main(["slot", *flags, "--", "touch", str(marker)]), stderr.getvalue()

        integer = "land: --slots must be a positive integer\n"
        self.assertEqual(slot_status("--slots", "3"),
                         (1, "land: --slots 3 is more than the 2 worker slots this governor has\n"))
        self.assertEqual(slot_status("--slots", "0"), (1, integer))
        self.assertEqual(slot_status("--slots", "-1"), (1, integer))
        self.assertEqual(slot_status("--slots", "nope"), (1, integer))
        self.assertEqual(slot_status("--slots", "2", "--exclusive"),
                         (1, "land: --slots cannot be combined with --exclusive\n"))
        self.assertFalse(marker.exists())
        self.assertFalse((directory / "worker-0").exists())
        with mock.patch.dict(os.environ, {"LAND_SLOT": "worker", "LAND_SLOT_FILES": "worker-0"}):
            with land.slot("worker", count=3):
                self.assertEqual(os.environ["LAND_SLOT_FILES"], "worker-0")
        self.assertFalse((directory / "worker-0").exists())

    def test_a_waiter_killed_with_sigkill_does_not_block_a_later_caller(self):
        slots = SlotCallers(self, 1)

        def kill(caller):
            self.assertFalse(caller.started)
            os.kill(caller.process.pid, signal.SIGKILL)
            self.assertEqual(caller.process.wait(timeout=20), -signal.SIGKILL)

        holder = slots.enter(slots.start("holder"))
        kill(slots.queued(slots.start("ticketed", marked=True)))
        slots.release(holder)
        slots.release(slots.enter(slots.start("after-ticketed")))
        self.assertEqual(list((slots.governor / "tickets").iterdir()), [])

        with open(slots.governor / "tickets.lock", "a") as mutex:
            fcntl.flock(mutex, fcntl.LOCK_EX)
            blocked = slots.start("blocked", marked=True)
            slots.wait(lambda: (blocked.directory / "blocking").exists(), "the waiter blocks on the turn mutex")
            kill(blocked)
        slots.release(slots.enter(slots.start("after-blocked")))
        self.assertEqual(slots.entries(), ["holder", "after-ticketed", "after-blocked"])

    def test_many_callers_never_exceed_the_slot_count(self):
        slots = SlotCallers(self, 2)
        waiting = [slots.start(f"worker-{index}", marked=True) for index in range(5)]
        while waiting:
            expected = min(2, len(waiting))
            slots.wait(lambda: sum(caller.started for caller in waiting) >= expected
                       and all(caller.started or (caller.directory / "waiting-1").exists() for caller in waiting),
                       "the slots are full and the rest wait")
            running = [caller for caller in waiting if caller.started]
            self.assertEqual(len(running), expected)
            slots.release(running[0])
            waiting.remove(running[0])

        exclusive = slots.enter(slots.start("exclusive", "--exclusive"))
        waiting = [slots.queued(slots.start(f"later-{index}", marked=True)) for index in range(3)]
        waiting.append(slots.queued(slots.start("landing", pool="landing", marked=True)))
        self.assertEqual([caller.name for caller in waiting if caller.started], [])
        slots.release(exclusive)
        while waiting:
            slots.wait(lambda: any(caller.started for caller in waiting), "a waiting caller started")
            running = next(caller for caller in waiting if caller.started)
            slots.release(running)
            waiting.remove(running)

        running, peak = set(), 0
        for word, name, *_ in slots.events():
            if word == "exit":
                running.remove(name)
                continue
            running.add(name)
            peak = max(peak, len(running - {"landing"}))
            if "exclusive" in running:
                self.assertEqual(running, {"exclusive"})
        self.assertEqual(peak, 2)
        self.assertEqual(len(slots.entries()), 10)

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

    def test_contending_callers_never_share_a_slot_and_never_pass_an_earlier_ticket(self):
        kinds = ["worker", "exclusive", "landing", "worker", "landing", "exclusive", "worker", "worker", "landing", "exclusive"]
        self.assertEqual(self.contend(1, kinds, hold=0.1), 1)
        self.admit_latest_first(1, kinds)

    def test_contending_callers_fill_two_worker_slots_and_never_pass_an_earlier_ticket(self):
        kinds = ["worker", "worker", "landing", "exclusive", "worker", "landing", "worker", "worker", "exclusive", "worker"]
        self.assertEqual(self.contend(2, kinds, hold=1.5), 2)
        self.admit_latest_first(2, kinds)

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

    def test_an_ofd_caller_and_a_flock_caller_reorder_without_sharing_a_slot(self):
        if not self.ofd_locks_exist():
            self.skipTest("this platform does not provide OFD locks")
        ofd = self.base / "ofd/land.py"
        ofd.parent.mkdir()
        ofd.write_text(OFD_SLOT_SCRIPT)
        slots = SlotCallers(self, 1)

        def parked(name, *flags, script=SCRIPT):
            caller = slots.start(name, *flags, script=script, stepped=True)
            self.assertEqual(slots.settle(caller), "waiting", name)
            return caller

        def later_waiter_first(holder, earlier, later):
            slots.release(holder)
            self.assertEqual(slots.step(later), "started", later.name)
            self.assertEqual(slots.step(earlier), "waiting", earlier.name)
            slots.release(later)
            self.assertEqual(slots.step(earlier), "started", earlier.name)
            slots.release(earlier)

        def worker_behind_exclusive(exclusive, worker):
            self.assertEqual(slots.step(worker), "waiting", worker.name)
            slots.release(exclusive)
            self.assertEqual(slots.step(worker), "started", worker.name)
            slots.release(worker)

        later_waiter_first(slots.enter(slots.start("ofd-holder", script=ofd)),
                           parked("ofd-earlier", script=ofd), parked("flock-later"))
        later_waiter_first(slots.enter(slots.start("flock-holder")),
                           parked("flock-earlier"), parked("ofd-later", script=ofd))
        worker_behind_exclusive(slots.enter(slots.start("flock-exclusive", "--exclusive")),
                                parked("ofd-worker", script=ofd))
        worker_behind_exclusive(slots.enter(slots.start("ofd-exclusive", "--exclusive", script=ofd)),
                                parked("flock-worker"))
        self.assertEqual(slots.entries(), [
            "ofd-holder", "flock-later", "ofd-earlier", "flock-holder", "ofd-later", "flock-earlier",
            "flock-exclusive", "ofd-worker", "ofd-exclusive", "flock-worker"])
        self.assertEqual(slots.peak(), 1)

        slots = SlotCallers(self, 2)
        holders = [slots.enter(slots.start("ofd-holder", script=ofd)), slots.enter(slots.start("flock-holder"))]
        workers = []
        for index in range(3):
            workers.append(slots.queued(slots.start(f"ofd-{index}", script=ofd, marked=True, hold=0.2)))
            workers.append(slots.queued(slots.start(f"flock-{index}", marked=True, hold=0.2)))
        for holder in holders:
            slots.release(holder)
        for worker in workers:
            self.assertEqual(worker.process.wait(timeout=120), 0, worker.name)
        self.assertEqual(len(slots.entries()), 8)
        self.assertEqual(slots.peak(), 2)

    def test_an_ofd_caller_sees_every_file_of_a_two_slot_hold(self):
        if not self.ofd_locks_exist():
            self.skipTest("this platform does not provide OFD locks")
        ofd = self.base / "ofd/land.py"
        ofd.parent.mkdir()
        ofd.write_text(OFD_SLOT_SCRIPT)
        slots = SlotCallers(self, 2)
        wide = slots.enter(slots.start("wide", "--slots", "2"))
        narrow = slots.start("ofd-narrow", script=ofd, stepped=True)
        self.assertEqual(slots.settle(narrow), "waiting")
        self.assertEqual(slots.step(narrow), "waiting")
        slots.release(wide)
        self.assertEqual(slots.step(narrow), "started")
        slots.release(narrow)
        self.assertEqual(slots.entries(), ["wide", "ofd-narrow"])
        self.assertEqual(slots.peak(), 1)

    def test_an_old_slot_holder_blocks_a_new_caller_and_the_reverse(self):
        slots = SlotCallers(self, 1)
        old = self.base / "old/land.py"
        old.parent.mkdir()
        old.write_text(PREVIOUS_SLOT_SCRIPT)

        holder = slots.enter(slots.start("old-holder", script=old))
        ordinary = slots.start("new-ordinary", stepped=True)
        self.assertEqual(slots.settle(ordinary), "waiting")
        exclusive = slots.start("new-exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        self.assertEqual(slots.step(ordinary), "waiting")
        self.assertEqual(slots.step(exclusive), "waiting")
        slots.release(holder)
        self.assertEqual(slots.step(ordinary), "started")
        slots.release(ordinary)
        self.assertEqual(slots.step(exclusive), "started")
        slots.release(exclusive)

        holder = slots.enter(slots.start("new-holder"))
        waiter = slots.start("old-waiter", script=old, stepped=True)
        self.assertEqual(slots.settle(waiter), "waiting")
        self.assertEqual(slots.step(waiter), "waiting")
        slots.release(holder)
        self.assertEqual(slots.step(waiter), "started")
        slots.release(waiter)
        self.assertEqual(slots.entries(), ["old-holder", "new-ordinary", "new-exclusive", "new-holder", "old-waiter"])

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

    def test_a_running_holder_is_released_before_the_fixture_directory_goes(self):
        self.governor(1)
        holders = []

        def check():
            self.assertEqual(holders[0].returncode, 0)
            self.assertTrue((self.base / "holder-worker" / "release").exists())

        self.addCleanup(check)
        holders.append(self.start_holder())
        self.assertIsNone(holders[0].poll())

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

    def test_a_waiter_behind_a_worker_holder_reports_the_holder_after_the_interval(self):
        self.governor(1)
        self.start_holder(kind="worker", command="pytest -q tests/integration")
        release = os.open(self.base / "holder-worker" / "release", os.O_WRONLY)
        try:
            _, stdout, stderr, line = self.run_waiter(
                WAIT_SCRIPT, release, command="npm run build", after="0.2")
        finally:
            os.close(release)
        self.assertEqual(stdout, "ran\n")
        self.assertEqual(stderr, line + "\n")
        self.assertRegex(line, rf"^land: waited \d+s for a slot; worker {re.escape(str(self.work))} for \d+s: "
                                rf"pytest -q tests/integration$")

    def test_a_waiter_that_gets_a_slot_at_once_prints_nothing(self):
        self.governor(1)
        result = _run(
            [sys.executable, "-c", WAIT_SCRIPT, str(SCRIPT.parent), "0.2", "60", "worker", str(self.work), "make"],
            env=self.slot_env(), cwd=self.base, timeout=20, reason=ProcessReason.GOVERNOR)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "ran\n")
        self.assertEqual(result.stderr, "")
        holds = self.base / "state/pstack-t3/governor/holds"
        self.assertFalse(holds.exists() and list(holds.iterdir()))

    def test_a_waiter_behind_an_exclusive_holder_names_it_exclusive(self):
        self.governor(1)
        self.start_holder(kind="exclusive", command="bench --all")
        release = os.open(self.base / "holder-exclusive" / "release", os.O_WRONLY)
        try:
            _, stdout, _, line = self.run_waiter(WAIT_SCRIPT, release, after="0.2")
        finally:
            os.close(release)
        self.assertEqual(stdout, "ran\n")
        self.assertIn(f"exclusive {self.work} for ", line)
        self.assertIn(": bench --all", line)

    def test_a_stderr_error_on_the_slot_wait_line_does_not_cancel_the_waiter(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="make test")
        release = self.base / "holder-worker" / "release"
        attempted = threading.Event()
        body = []

        class DeadStderr:
            def write(self, data):
                attempted.set()
                raise OSError(errno.EIO, "Input/output error")

            def flush(self):
                return None

        def release_holder():
            attempted.wait(timeout=5)
            with open(release, "w") as handle:
                handle.write("x")

        releaser = threading.Thread(target=release_holder)
        saved_after = land.REPORT_AFTER
        land.REPORT_AFTER = 0.05
        saved_stderr = sys.stderr
        releaser.start()
        try:
            sys.stderr = DeadStderr()
            with self.no_slot():
                with land.slot("worker", repo=str(self.work), command="npm test"):
                    body.append("ran")
        finally:
            sys.stderr = saved_stderr
            land.REPORT_AFTER = saved_after
            releaser.join(timeout=10)
            if holder.poll() is None:
                holder.wait(timeout=20)
        self.assertFalse(releaser.is_alive())
        self.assertTrue(attempted.is_set(), "the waiter never printed a wait line")
        self.assertEqual(body, ["ran"])

    def test_an_exclusive_command_that_outlives_its_cap_is_stopped(self):
        self.governor(1)
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(30)"],
            cap=0.4, grace=0.05, timeout=10)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "land: stopped the command at the 0.4s exclusive cap. "
            "--for sets a longer cap and needs --reason past 0.4s.\n")
        self.assert_slots_free()

    def test_an_exclusive_cap_stops_a_child_still_in_the_commands_process_group(self):
        self.governor(1)
        parent_path, child_path = self.base / "parent.pid", self.base / "child.pid"
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", GROUP_SLEEP, str(parent_path), str(child_path)],
            cap=1, grace=0.05, timeout=10)
        self.assertEqual(result.returncode, 124)
        self.assertIn("1s exclusive cap", result.stderr)
        self.assertIn("--for", result.stderr)
        self.assert_dead(int(parent_path.read_text()))
        self.assert_dead(int(child_path.read_text()))
        self.assert_slots_free()

    def test_an_exclusive_command_that_ignores_sigterm_is_killed_after_the_grace(self):
        self.governor(1)
        log_path = self.base / "term-log"
        command = (
            "import signal, sys, time\n"
            "from pathlib import Path\n"
            "log = Path(sys.argv[1])\n"
            "def note(word):\n"
            "    handle = log.open('a')\n"
            "    handle.write(f'{word} {time.time()}\\n')\n"
            "    handle.close()\n"
            "def on_term(signum, frame):\n"
            "    note('term')\n"
            "signal.signal(signal.SIGTERM, on_term)\n"
            "note('start')\n"
            "time.sleep(3600)\n"
        )
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", command, str(log_path)],
            cap=0.6, grace=0.3, timeout=4)
        self.assertEqual(result.returncode, 124)
        lines = log_path.read_text().splitlines()
        self.assertEqual([line.split()[0] for line in lines], ["start", "term"])
        start, term = (float(line.split()[1]) for line in lines)
        self.assertGreater(term - start, 0.2)
        self.assertGreater(time.time() - term, 0.12)
        self.assert_slots_free()

    def test_an_exclusive_command_that_finishes_before_the_cap_keeps_its_status_and_output(self):
        self.governor(1)
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c",
             "import sys; print('out'); print('err', file=sys.stderr); raise SystemExit(7)"],
            cap=2, grace=0.05, timeout=10)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "out\n")
        self.assertEqual(result.stderr, "err\n")
        self.assert_slots_free()

    def test_an_ordinary_ready_symlink_leaves_its_destination_absent(self):
        self.governor(1)
        link = self.base / "ready-link"
        destination = self.base / "ready-destination"
        link.symlink_to(destination)
        with mock.patch.dict(os.environ, {"LAND_EXCLUSIVE_READY": str(link)}):
            result = self.run_exclusive(
                ["slot", "--exclusive", "--", sys.executable, "-c", "pass"],
                cap=5, grace=0.05, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(destination.exists(), "an ordinary ready symlink created its destination")
        self.assertNotIn("LAND_EXCLUSIVE_READY", SCRIPT.read_text())

    def test_an_exclusive_cap_starts_when_the_hold_takes_the_slots(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="make test")
        release = self.base / "holder-worker" / "release"
        clock = self.base / "cap-clock"
        clock.write_text("1000")
        with mock.patch.dict(os.environ, {"LAND_CAP_CLOCK": str(clock)}):
            process = self.popen_exclusive(
                ["slot", "--exclusive", "--for", "2", "--", sys.executable, "-c",
                 "import time; time.sleep(0.2); print('ok', flush=True)"],
                main=CAP_CLOCK_MAIN)
            try:
                tickets = self.base / "state/pstack-t3/governor/tickets"
                deadline = time.monotonic() + 20
                seen = False
                while time.monotonic() < deadline:
                    try:
                        names = os.listdir(tickets)
                    except OSError:
                        names = []
                    if any(name.endswith("-exclusive") for name in names):
                        seen = True
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(seen, "the exclusive waiter took no ticket")
                self.assertIsNone(process.poll(), "the exclusive caller exited while the slots were still busy")
                clock.write_text("1010")
                with open(release, "w") as handle:
                    handle.write("x")
                stdout, stderr = process.communicate(timeout=20)
            finally:
                if process.poll() is None:
                    os.kill(process.pid, signal.SIGKILL)
                    process.wait(timeout=20)
                process.stdout.close()
                process.stderr.close()
                if holder.poll() is None:
                    holder.wait(timeout=20)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stdout, "ok\n")
        self.assertNotIn("exclusive cap", stderr)
        self.assert_slots_free()

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

    def test_an_exclusive_cap_kills_a_same_group_child_that_ignores_sigterm_after_the_leader_exits(self):
        self.governor(1)
        parent_path, child_path = self.base / "cap-parent.pid", self.base / "cap-child.pid"
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", IGNORING_GROUP,
             str(parent_path), str(child_path)],
            cap=0.4, grace=0.15)
        try:
            child_line = self.wait_for_line(child_path)
            code = process.wait(timeout=10)
            parent_pid = int(parent_path.read_text())
            child_pid, child_group = (int(part) for part in child_line.split())
            self.assertEqual(child_group, parent_pid)
            self.assert_dead(parent_pid)
            self.assert_dead(child_pid)
            stderr = process.stderr.read()
            self.assertEqual(code, 124)
            self.assertEqual(
                stderr,
                "land: stopped the command at the 0.4s exclusive cap. "
                "--for sets a longer cap and needs --reason past 0.4s.\n")
            self.assert_slots_free()
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
            self.release_recorded_group(parent_path, child_path)

    def test_sigint_to_an_exclusive_land_py_kills_a_same_group_child_that_ignores_sigterm(self):
        self.governor(1)
        code, stderr = self.signal_exclusive_ignoring_child(signal.SIGINT)
        self.assertEqual(code, -2)
        self.assertIn("KeyboardInterrupt", stderr)
        self.assertNotIn("exclusive cap", stderr)

    def test_sigterm_to_an_exclusive_land_py_kills_a_same_group_child_that_ignores_sigterm(self):
        self.governor(1)
        code, stderr = self.signal_exclusive_ignoring_child(signal.SIGTERM)
        self.assertEqual(code, 143)
        self.assertNotIn("exclusive cap", stderr)

    def test_sighup_to_an_exclusive_land_py_kills_a_same_group_child_that_ignores_sigterm(self):
        self.governor(1)
        code, stderr = self.signal_exclusive_ignoring_child(signal.SIGHUP)
        self.assertEqual(code, 129)
        self.assertNotIn("exclusive cap", stderr)

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

    def test_cap_cases_without_os_waitid(self):
        self.governor(1)

        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(30)"],
            cap=0.4, grace=0.05, timeout=10, main=EXCLUSIVE_MAIN_WITHOUT_WAITID)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (124, "", cap_line("0.4")))
        self.assert_slots_free()

        parent_path, child_path = self.base / "group-parent.pid", self.base / "group-child.pid"
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", GROUP_SLEEP, str(parent_path), str(child_path)],
            cap=1, grace=0.05, timeout=10, main=EXCLUSIVE_MAIN_WITHOUT_WAITID)
        self.assertEqual((result.returncode, result.stderr), (124, cap_line("1")))
        self.assert_dead(int(parent_path.read_text()))
        self.assert_dead(int(child_path.read_text()))
        self.assert_slots_free()

        parent_path, child_path = self.base / "ignoring-parent.pid", self.base / "ignoring-child.pid"
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", IGNORING_GROUP, str(parent_path), str(child_path)],
            cap=1, grace=0.15, main=EXCLUSIVE_MAIN_WITHOUT_WAITID)
        try:
            child_pid, child_group = (int(part) for part in self.wait_for_line(child_path).split())
            code = process.wait(timeout=10)
            parent_pid = int(parent_path.read_text())
            self.assertEqual(child_group, parent_pid)
            self.assert_dead(parent_pid)
            self.assert_dead(child_pid)
            self.assertEqual((code, process.stderr.read()), (124, cap_line("1")))
            self.assert_slots_free()
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
            self.release_recorded_group(parent_path, child_path)

    @unittest.skipUnless(os.path.isdir("/proc"), "the test reads a zombie state from /proc")
    def test_a_signal_during_grace_after_the_leader_exits_kills_the_group(self):
        self.governor(1)

        def zombie(pid):
            try:
                return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0] == "Z"
            except FileNotFoundError:
                return False

        statuses = {signal.SIGTERM: 143, signal.SIGHUP: 129, signal.SIGINT: -2}
        for label, main in (("waitid", EXCLUSIVE_MAIN), ("no-waitid", EXCLUSIVE_MAIN_WITHOUT_WAITID)):
            for signum, status in statuses.items():
                with self.subTest(signal=signum.name, os=label):
                    parent_path = self.base / f"grace-{label}-{signum.name}-parent.pid"
                    child_path = self.base / f"grace-{label}-{signum.name}-child.pid"
                    process = self.popen_exclusive(
                        ["slot", "--exclusive", "--", sys.executable, "-c", IGNORING_GROUP,
                         str(parent_path), str(child_path)],
                        cap=1, grace=30, main=main)
                    try:
                        child_pid, child_group = (int(part) for part in self.wait_for_line(child_path).split())
                        parent_pid = int(parent_path.read_text())
                        self.assertEqual(child_group, parent_pid)
                        deadline = time.monotonic() + 10
                        while not zombie(parent_pid) and time.monotonic() < deadline:
                            time.sleep(0.01)
                        self.assertTrue(zombie(parent_pid), "the leader did not exit at the cap")
                        os.kill(process.pid, signum)
                        code = process.wait(timeout=10)
                        self.assert_dead(child_pid)
                        self.assert_slots_free()
                        self.assertEqual(code, status)
                        self.assertNotIn("exclusive cap", process.stderr.read())
                    finally:
                        if process.poll() is None:
                            os.kill(process.pid, signal.SIGKILL)
                            process.wait(timeout=10)
                        process.stdout.close()
                        process.stderr.close()
                        self.release_recorded_group(parent_path, child_path)

    def test_an_exclusive_command_that_finishes_leaves_a_background_child(self):
        self.governor(1)
        child_path = self.base / "background-child.pid"
        child = None
        try:
            result = self.run_exclusive(
                ["slot", "--exclusive", "--", sys.executable, "-c", BACKGROUND_CHILD, str(child_path)],
                cap=30, timeout=10)
            child = int(child_path.read_text().split()[0])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("exclusive cap", result.stderr)
            time.sleep(0.2)
            os.kill(child, 0)
            self.assert_slots_free()
        finally:
            if child is None and child_path.exists():
                parts = child_path.read_text().split()
                if parts:
                    try:
                        child = int(parts[0])
                    except ValueError:
                        child = None
            if child is not None:
                try:
                    os.kill(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    try:
                        os.kill(child, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.02)

    def test_an_exclusive_cap_stops_when_darwin_eperm_follows_an_exited_leader(self):
        """A killpg that macOS refuses for an exited leader still stops the command at its cap."""
        self.governor(1)
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(30)"],
            cap=0.4, grace=0.05, timeout=20, main=_darwin_eperm_main("after-term"))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (124, "", cap_line("0.4")))
        self.assert_slots_free()

    def test_an_exclusive_darwin_eperm_is_reraised_when_the_witness_is_not_a_permitted_target(self):
        """Each witness that is not a permitted target fails the command, and the Linux branch does the same."""
        self.governor(1)
        rows = (
            ("live leader", dict(mode="always", probe="waitid"), True),
            ("unknown witness", dict(mode="always", probe="failed"), True),
            ("foreign uid", dict(mode="after-term", probe="foreign"), True),
            ("leader exits after a negative witness", dict(mode="always", kills=True, probe="waitid"), True),
            # darwin False keeps the host platform, so this row reaches the Linux branch on a Linux host.
            ("linux", dict(mode="always", probe="waitid"), False),
        )
        for label, options, darwin in rows:
            with self.subTest(witness=label):
                # The command ends on its own, so a caller that never signals it cannot outlive the test.
                result = self.run_exclusive(
                    ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(0.6)"],
                    cap=0.2, grace=0.05, timeout=20,
                    main=_darwin_eperm_main(darwin=darwin, **options))
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertIn("PermissionError", result.stderr)
                self.assertNotIn("exclusive cap", result.stderr)
                self.assert_slots_free()

    def test_an_exclusive_darwin_eperm_leaves_a_sigterm_ignoring_member_alive_after_the_cap(self):
        """An injected EPERM after the leader exits leaves a SIGTERM-ignoring member running.

        The command still stops at its cap and frees the slots, so the caller never learns that the
        group has a live member.
        """
        self.governor(1)
        parent_path = self.base / "member-parent.pid"
        child_path = self.base / "member-child.pid"
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", SURVIVING_IGNORING_GROUP,
             str(parent_path), str(child_path)],
            cap=1, grace=0.15, main=_darwin_eperm_main("after-term"))
        try:
            child_line = self.wait_for_line(child_path)
            code = process.wait(timeout=20)
            parent_pid = int(parent_path.read_text())
            child_pid, child_group = (int(part) for part in child_line.split())
            self.assertEqual(child_group, parent_pid)
            self.assert_dead(parent_pid)
            self.assertEqual((code, process.stderr.read()), (124, cap_line("1")))
            self.assert_slots_free()
            self.assert_live(child_pid)
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=20)
            process.stdout.close()
            process.stderr.close()
            self.release_recorded_group(parent_path, child_path)

    def test_an_interrupt_during_the_exclusive_grace_survives_a_darwin_eperm(self):
        """The interrupt that ends the grace wait is the error the caller sees, not the EPERM."""
        self.governor(1)
        result = self.run_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(30)"],
            cap=0.4, grace=0.4, timeout=20,
            main=_darwin_eperm_main("after-term", after=INTERRUPT_AFTER_THE_LEADER_EXITS))
        self.assertNotEqual(result.returncode, 124, result.stderr)
        self.assertIn("KeyboardInterrupt", result.stderr)
        self.assertNotIn("PermissionError", result.stderr)
        self.assert_slots_free()

    def test_the_interrupt_fixture_needs_no_os_waitid(self):
        """Pin that the interrupt fixture needs no os.waitid.

        Removing os.waitid from the child models macOS Python before 3.13, which does not expose it.
        """
        self.governor(1)
        original = _darwin_eperm_main

        def without_waitid(*args, **kwargs):
            return "import os\nvars(os).pop('waitid', None)\n" + original(*args, **kwargs)

        with mock.patch.object(sys.modules[__name__], "_darwin_eperm_main", without_waitid):
            result = self.run_exclusive(
                ["slot", "--exclusive", "--", sys.executable, "-c", "import time; time.sleep(30)"],
                cap=0.4, grace=0.4, timeout=20,
                main=_darwin_eperm_main("after-term", after=INTERRUPT_AFTER_THE_LEADER_EXITS))
        self.assertNotEqual(result.returncode, 124, result.stderr)
        self.assertIn("KeyboardInterrupt", result.stderr)
        self.assertNotIn("PermissionError", result.stderr)
        self.assert_slots_free()

    def test_supervised_treats_darwin_eperm_for_a_witnessed_zombie_like_a_successful_killpg(self):
        """A timed out command reports no status when macOS refuses the killpg for an exited leader."""
        self.governor(1)
        # The shell exits at once and leaves a member that holds the capture pipe, so the leader is an
        # unreaped zombie when the killpg runs and the member ends on its own.
        command = f"{sys.executable} -c 'import time; time.sleep(2)' & exit 0"
        with mock.patch.object(land.sys, "platform", "darwin"), \
                mock.patch.object(land.os, "killpg",
                                  side_effect=PermissionError(errno.EPERM, "Operation not permitted")):
            code, output = land.supervised(command, str(self.base), 0.4)
        self.assertIsNone(code)
        self.assertEqual(output, "")

    def test_an_exclusive_deadline_is_when_every_slot_is_locked(self):
        self.governor(1)
        result = _run(
            [sys.executable, "-c", SLOW_PUBLISH_MAIN, str(SCRIPT.parent),
             "slot", "--exclusive", "--for", "0.2", "--",
             sys.executable, "-c", "print('command-finished', flush=True)"],
            env=self.slot_env(), cwd=self.base, timeout=10, reason=ProcessReason.GOVERNOR)
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertIn("all-slots-taken", result.stdout)
        self.assertNotIn("command-finished", result.stdout)
        self.assertEqual(
            result.stderr,
            "land: stopped the command at the 0.2s exclusive cap. "
            "--for sets a longer cap and needs --reason past 600s.\n")
        lines = result.stdout.splitlines()
        self.assertEqual([line.split()[0] for line in lines], ["all-slots-taken", "exit"])
        taken = float(lines[0].split()[1])
        exited = float(lines[1].split()[1])
        self.assertGreaterEqual(exited - taken, 0.5)
        self.assert_slots_free()

    def test_bad_exclusive_flags_are_refused_before_a_slot_is_taken(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="make test")
        refused = "land: --for must be a positive number of seconds\n"
        only = "land: --for and --reason are only for slot --exclusive\n"
        needs = "land: --for over 600 seconds needs --reason\n"
        cases = [
            (["slot", "--exclusive", "--for", "601", "--", "true"], needs, "--for"),
            (["slot", "--exclusive", "--for", "0", "--", "true"], refused, "--for"),
            (["slot", "--exclusive", "--for", "-2", "--", "true"], refused, "--for"),
            (["slot", "--exclusive", "--for", "nope", "--", "true"], refused, "--for"),
            (["slot", "--exclusive", "--for", "inf", "--", "true"], refused, "--for"),
            (["slot", "--exclusive", "--for", "7201", "--", "true"],
             "land: --for cannot pass 7200 seconds\n", "--for"),
            (["slot", "--exclusive", "--for", "7200", "--", "true"], needs, "--for"),
            (["slot", "--exclusive", "--for", "601", "--reason", "\n\x01", "--", "true"], needs, "--reason"),
            (["slot", "--for", "10", "--", "true"], only, "--for"),
            (["slot", "--reason", "because", "--", "true"], only, "--reason"),
        ]
        try:
            for argv, stderr, flag in cases:
                with self.subTest(argv=argv):
                    result = self.run_exclusive(argv, timeout=2)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stderr, stderr)
                    self.assertIn(flag, result.stderr)
                    self.assert_worker_holder_keeps_the_slot(holder)
            with self.subTest(argv="missing command"):
                result = self.run_exclusive(["slot", "--exclusive", "--for", "10"], timeout=2)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stderr, "land: slot needs a command after --\n")
                self.assert_worker_holder_keeps_the_slot(holder)
        finally:
            if holder.poll() is None:
                with open(self.base / "holder-worker" / "release", "w") as handle:
                    handle.write("x")
                holder.wait(timeout=20)

    def test_an_exclusive_cap_at_the_default_needs_no_reason_and_the_maximum_needs_one(self):
        self.governor(1)
        plain = self.run_exclusive(
            ["slot", "--exclusive", "--for", "0.4", "--", sys.executable, "-c", "print('ok')"],
            cap=0.4, cap_max=0.8, grace=0.05, timeout=10)
        self.assertEqual((plain.returncode, plain.stdout), (0, "ok\n"), plain.stderr)
        self.assertNotIn("exclusive cap", plain.stderr)
        longest = self.run_exclusive(
            ["slot", "--exclusive", "--for", "0.8", "--reason", "cold pricing bench", "--",
             sys.executable, "-c", "print('max')"],
            cap=0.4, cap_max=0.8, grace=0.05, timeout=10)
        self.assertEqual((longest.returncode, longest.stdout), (0, "max\n"), longest.stderr)
        shorter = self.run_exclusive(
            ["slot", "--exclusive", "--for", "0.2", "--reason", "shorter cap", "--",
             sys.executable, "-c", "print('short')"],
            cap=0.4, cap_max=0.8, grace=0.05, timeout=10)
        self.assertEqual((shorter.returncode, shorter.stdout), (0, "short\n"), shorter.stderr)
        named = self.run_exclusive(
            ["slot", "--exclusive", "--reason", "cold pricing bench", "--",
             sys.executable, "-c", "print('why')"],
            cap=2, grace=0.05, timeout=10)
        self.assertEqual((named.returncode, named.stdout), (0, "why\n"), named.stderr)
        refused = self.run_exclusive(
            ["slot", "--exclusive", "--for", "0.8", "--", sys.executable, "-c", "print('no')"],
            cap=0.4, cap_max=0.8, timeout=10)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(refused.stdout, "")
        self.assertEqual(refused.stderr, "land: --for over 0.4 seconds needs --reason\n")

    def test_an_exclusive_holder_record_and_its_wait_line_carry_the_cap_and_the_reason(self):
        self.governor(1)
        directory = self.base / "capped"
        directory.mkdir()
        ready, release = directory / "ready", directory / "release"
        os.mkfifo(release)
        command = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "Path(sys.argv[1]).touch()\n"
            "os.read(os.open(sys.argv[2], os.O_RDONLY), 1)\n"
        )
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--repo", str(self.work), "--for", "30", "--reason", "cold pricing bench", "--",
             sys.executable, "-c", command, str(ready), str(release)])
        gate = None
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(0.01)
            detail = process.stderr.read() if process.poll() is not None else "the hold did not start"
            self.assertTrue(ready.exists(), detail)
            record = json.loads(land._hold_path("landing-0").read_text())
            self.assertEqual(record["kind"], "exclusive")
            self.assertEqual(record["pid"], process.pid)
            self.assertEqual(record["cap"], 30)
            self.assertEqual(record["reason"], "cold pricing bench")
            worker = json.loads(land._hold_path("worker-0").read_text())
            self.assertEqual((worker["cap"], worker["reason"]), (30, "cold pricing bench"))
            gate = os.open(release, os.O_RDWR | os.O_NONBLOCK)
            code, stdout, stderr, line = self.run_waiter(WAIT_SCRIPT, gate, after="0.2")
            process.wait(timeout=10)
        finally:
            if gate is not None:
                os.close(gate)
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(stdout, "ran\n")
        self.assertIn(f"exclusive {self.work} for ", line)
        self.assertIn("left of 30s", line)
        self.assertIn(", cold pricing bench: ", line)
        self.assertIn(f": {record['command']}\n", stderr)
        self.assertNotIn("\n", line)
        self.assert_slots_free()

    def test_an_old_exclusive_record_with_no_cap_prints_as_it_does_today(self):
        self.governor(1)
        holds = land.governor_dir() / "holds"
        holds.mkdir(parents=True, exist_ok=True)
        path = holds / "landing-0"
        payload = {
            "kind": "exclusive", "pid": os.getpid(), "repo": str(self.work),
            "started": time.time(), "command": "bench --all",
        }
        self.assertNotIn("cap", payload)
        self.assertNotIn("reason", payload)
        writer = os.open(path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o666)
        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            path.write_text(json.dumps(payload))
            holder = land._read_holder(path)
            self.assertIsNone(holder.cap)
            self.assertIsNone(holder.reason)
            line = land._format_wait(15, land._live_holders(["landing-0"]))
        finally:
            os.close(writer)
        self.assertRegex(
            line,
            rf"^land: waited 15s for a slot; exclusive {re.escape(str(self.work))} for \d+s: bench --all$")
        self.assertNotIn("left of", line)

    def test_an_exclusive_reason_drops_a_newline_and_a_control_character(self):
        self.governor(1)
        directory = self.base / "reason"
        directory.mkdir()
        ready, release = directory / "ready", directory / "release"
        os.mkfifo(release)
        command = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "Path(sys.argv[1]).touch()\n"
            "os.read(os.open(sys.argv[2], os.O_RDONLY), 1)\n"
        )
        raw = ("n" * 40) + "\n" + ("c" * 40) + "\x01\t" + ("z" * 40)
        expected = ("n" * 40) + ("c" * 40)
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--for", "30", "--reason", raw, "--",
             sys.executable, "-c", command, str(ready), str(release)])
        gate = None
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "the hold did not start")
            record = json.loads(land._hold_path("landing-0").read_text())
            self.assertEqual(record["reason"], expected)
            self.assertNotIn("\n", record["reason"])
            self.assertNotIn("z", record["reason"])
            gate = os.open(release, os.O_RDWR | os.O_NONBLOCK)
            code, stdout, _, line = self.run_waiter(WAIT_SCRIPT, gate, after="0.2")
            process.wait(timeout=10)
        finally:
            if gate is not None:
                os.close(gate)
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "ran\n")
        self.assertIn(expected, line)
        self.assertNotIn("z" * 40, line)
        self.assertNotIn("\n", line)
        self.assertNotIn("n" * 40 + " ", line)

    def test_a_direct_exclusive_slot_record_has_no_cap(self):
        self.governor(1)
        with self.no_slot():
            with land.exclusive_slot(repo=str(self.work), command="bench"):
                record = land._read_holder(land._hold_path("landing-0"))
                self.assertEqual(record.kind, "exclusive")
                self.assertIsNone(record.cap)
                self.assertIsNone(record.reason)
                line = land._format_wait(15, [record])
        self.assertNotIn("left of", line)
        self.assertIn(": bench", line)

    def test_sigterm_to_an_exclusive_land_py_stops_the_group_and_clears_the_record(self):
        self.governor(1)
        parent_path, child_path = self.base / "sig-parent.pid", self.base / "sig-child.pid"
        process = self.popen_exclusive(
            ["slot", "--exclusive", "--", sys.executable, "-c", GROUP_SLEEP, str(parent_path), str(child_path)],
            cap=30, grace=0.2)
        try:
            deadline = time.monotonic() + 10
            while not (parent_path.exists() and child_path.exists()) and time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(0.01)
            self.assertTrue(child_path.exists(), "the command group did not start")
            os.kill(process.pid, signal.SIGTERM)
            code = process.wait(timeout=10)
            stderr = process.stderr.read()
        finally:
            if process.poll() is None:
                os.kill(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
        self.assertEqual(code, 143)
        self.assertNotIn("exclusive cap", stderr)
        self.assert_dead(int(parent_path.read_text()))
        self.assert_dead(int(child_path.read_text()))
        self.assert_slots_free()

    def test_a_record_whose_holder_is_gone_is_not_shown_as_holding(self):
        self.governor(1)
        holds = land.governor_dir() / "holds"
        holds.mkdir(parents=True, exist_ok=True)
        (holds / "worker-0").write_text(json.dumps(
            {"kind": "worker", "pid": 2 ** 31 - 1, "repo": str(self.work), "started": time.time(), "command": "make"}))
        self.assertEqual(land._live_holders(["worker-0"]), [land._UNKNOWN])

    def test_an_unlocked_stale_record_reads_as_unknown_when_the_lock_fails_with_enolck(self):
        self.governor(1)
        directory = land.governor_dir()
        slot = os.open(directory / "worker-0", os.O_RDWR | os.O_CREAT, 0o666)
        fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(os.close, slot)
        holds = directory / "holds"
        holds.mkdir(parents=True, exist_ok=True)
        record = holds / "worker-0"
        record.write_text(json.dumps(
            {"kind": "worker", "pid": 2147483647, "repo": "/dead/repo", "started": 1, "command": "dead-command"}))
        self.assertEqual(land._record_holder(record), land._UNKNOWN)
        with mock.patch.object(land.fcntl, "flock", side_effect=OSError(errno.ENOLCK, "No locks available")):
            holders = land._live_holders(["worker-0"])
            line = land._format_wait(15, holders)
        self.assertEqual(holders, [land._UNKNOWN])
        self.assertEqual(line, "land: waited 15s for a slot; unknown")
        self.assertNotIn("dead-command", line)
        self.assertNotIn("/dead/repo", line)

    def test_a_malformed_record_prints_unknown_without_failing(self):
        self.governor(1)
        holds = land.governor_dir() / "holds"
        holds.mkdir(parents=True, exist_ok=True)
        record = holds / "worker-0"
        # The reader names a record only while its writer holds the record's own lock, so lock it.
        writer = os.open(record, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o666)
        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for content in ("{ not json", ""):
                record.write_text(content)
                holders = land._live_holders(["worker-0"])
                self.assertEqual(holders, [land._UNKNOWN])
                self.assertEqual(land._format_wait(15, holders), "land: waited 15s for a slot; unknown")
            record.unlink()
            self.assertEqual(land._live_holders(["worker-0"]), [land._UNKNOWN])
        finally:
            os.close(writer)

    def test_a_record_start_no_float_can_hold_reads_as_unknown(self):
        self.governor(1)
        holds = land.governor_dir() / "holds"
        holds.mkdir(parents=True, exist_ok=True)
        record = holds / "worker-0"
        writer = os.open(record, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o666)
        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            for started in ("-1e309", "1e400", "1" + "0" * 399):
                text = ('{"kind": "worker", "pid": %d, "repo": %s, "started": %s, "command": "make"}'
                        % (os.getpid(), json.dumps(str(self.work)), started)).encode()
                os.pwrite(writer, text, 0)
                os.ftruncate(writer, len(text))
                holders = land._live_holders(["worker-0"])
                self.assertEqual([holder.started for holder in holders], [None])
                self.assertIn("for unknown", land._format_wait(15, holders))
        finally:
            os.close(writer)

    def test_a_record_start_no_float_can_hold_leaves_the_waiter_running(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="make test")
        record = land._hold_path("worker-0")
        saved = json.loads(record.read_text())
        damaged = re.sub(r'"started": [^,}]+', '"started": -1e309', json.dumps(saved))
        self.assertNotEqual(damaged, json.dumps(saved))
        descriptor = os.open(record, os.O_WRONLY)
        try:
            os.pwrite(descriptor, damaged.encode(), 0)
            os.ftruncate(descriptor, len(damaged))
        finally:
            os.close(descriptor)
        release = os.open(self.base / "holder-worker" / "release", os.O_WRONLY)
        try:
            code, stdout, _, line = self.run_waiter(WAIT_SCRIPT, release, after="0.2")
        finally:
            os.close(release)
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "ran\n")
        self.assertRegex(line, rf"^land: waited \d+s for a slot; worker {re.escape(str(self.work))} "
                                rf"for unknown: make test$")

    def test_an_old_holder_with_no_record_names_an_unknown_holder(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="dead-command")
        self.assertEqual(land._read_holder(land._hold_path("worker-0")).command, "dead-command")
        os.kill(holder.pid, signal.SIGKILL)
        self.assertEqual(holder.wait(timeout=20), -signal.SIGKILL)
        old = self.base / "old/land.py"
        old.parent.mkdir()
        old.write_text(PREVIOUS_SLOT_SCRIPT)
        directory = self.base / "old-holder"
        directory.mkdir()
        release, took = directory / "release", directory / "took"
        os.mkfifo(release)
        read_byte = (
            "import os, sys\n"
            "from pathlib import Path\n"
            "Path(sys.argv[2]).touch()\n"
            "os.read(os.open(sys.argv[1], os.O_RDONLY), 1)\n"
        )
        previous = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, str(old), "slot", "--", sys.executable, "-c", read_byte, str(release), str(took)],
            env=self.slot_env(), cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop_previous():
            if previous.poll() is None:
                os.killpg(previous.pid, signal.SIGKILL)
            previous.wait(timeout=20)
        self.addCleanup(stop_previous)
        deadline = time.monotonic() + 20
        while not took.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(took.exists(), "the previous holder did not take the worker slot")

        gate = os.open(release, os.O_WRONLY)
        try:
            code, stdout, stderr, line = self.run_waiter(WAIT_SCRIPT, gate, after="0.2")
        finally:
            os.close(gate)
        self.assertEqual(code, 0)
        self.assertEqual(stdout, "ran\n")
        self.assertEqual(stderr, line + "\n")
        self.assertRegex(line, r"^land: waited \d+s for a slot; unknown$")

    def test_the_wrapped_command_exit_status_and_output_are_unchanged(self):
        self.governor(1)
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT,
            ["slot", "--repo", str(self.work), "--", "sh", "-c", "echo out; echo err >&2; exit 7"],
            env=self.slot_env(), cwd=self.base, timeout=20)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "out\n")
        self.assertEqual(result.stderr, "err\n")

    def test_the_record_holds_only_the_first_80_characters_of_the_command(self):
        self.governor(1)

        def stored(command):
            handles = {name: open(land.governor_dir() / name, "a") for name in land._slot_names("exclusive", 1)}
            try:
                records = land._publish("worker", handles, [handles["worker-0"]], str(self.work), command)
                return json.loads(land._hold_path("worker-0").read_text())
            finally:
                land._release_records(records)
                for handle in handles.values():
                    handle.close()

        record = stored("x" * 120)
        crlf = stored("x" * 60 + "\r\n" * 10 + "SECRET")
        self.assertEqual(record["command"], "x" * 80)
        self.assertEqual(len(record["command"]), 80)
        self.assertEqual(crlf["command"], "x" * 60 + " " * 10)
        self.assertNotIn("SECRET", crlf["command"])
        self.assertEqual(set(record), set(land._Holder._fields))
        self.assertIsNone(record["cap"])
        self.assertIsNone(record["reason"])
        self.assertIsNone(crlf["cap"])
        self.assertIsNone(crlf["reason"])

    def test_a_reporting_probe_cannot_let_a_later_landing_caller_pass_an_earlier_exclusive(self):
        slots = SlotCallers(self, 1)
        holder = slots.enter(slots.start("holder"))
        exclusive = slots.start("exclusive", "--exclusive", stepped=True)
        self.assertEqual(slots.settle(exclusive), "waiting")
        directory = self.base / "caller-reporter"
        directory.mkdir()
        marker, release = directory / "probe", directory / "release"
        os.mkfifo(release)
        # Keep both ends open so the write that resumes the probe never waits for a reader.
        gate = os.open(release, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, gate)
        reporter = real_popen(
            ProcessReason.GOVERNOR,
            [sys.executable, "-c", REPORT_SCRIPT, str(SCRIPT.parent), str(marker), str(release), "worker-0",
             "0.2", "0.2", str(self.work), "make test"],
            env=self.slot_env(), cwd=directory, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop_reporter():
            if reporter.poll() is None:
                os.killpg(reporter.pid, signal.SIGKILL)
            reporter.wait(timeout=20)
        self.addCleanup(stop_reporter)

        slots.release(holder)
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists(), "the reporting probe did not pause holding the free slot")

        landing = slots.start("landing", pool="landing", stepped=True)
        slots.wait(lambda: (landing.directory / "blocking").exists() or landing.started,
                   "the landing caller blocks on the turn mutex the paused probe holds")
        self.assertTrue((landing.directory / "blocking").exists())
        self.assertFalse(landing.started)

        os.write(gate, b"x")
        stop_reporter()
        # The landing caller ran on its own clock, so its next attempt could have taken the free slot
        # before the exclusive stepped. Settling it first shows its attempt ended without starting.
        self.assertEqual(slots.settle(landing), "waiting")
        self.assertEqual(slots.step(exclusive), "started")
        self.assertFalse(landing.started)
        slots.release(exclusive)
        self.assertEqual(slots.step(landing), "started")
        slots.release(landing)
        self.assertEqual(slots.entries(), ["holder", "exclusive", "landing"])

    def test_a_worker_waiter_names_the_worker_holder_and_not_the_landing_holder(self):
        self.governor(1)
        self.start_holder(kind="worker", command="worker-command")
        landing = self.start_holder(kind="landing", command="landing-command")
        release = os.open(self.base / "holder-worker" / "release", os.O_WRONLY)
        try:
            _, stdout, stderr, line = self.run_waiter(WAIT_SCRIPT, release, after="0.2")
            self.assertEqual(stdout, "ran\n")
            self.assertEqual(stderr, line + "\n")
            self.assertRegex(line, rf"^land: waited \d+s for a slot; worker {re.escape(str(self.work))} "
                                    rf"for \d+s: worker-command$")
            self.assertNotIn("landing-command", stderr)
            self.assertNotIn("landing ", line)
        finally:
            os.close(release)
            if landing.poll() is None:
                with open(self.base / "holder-landing" / "release", "w") as handle:
                    handle.write("x")
            landing.wait(timeout=20)

    def test_a_worker_waiter_never_locks_the_landing_slot_while_it_reports(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="worker-command")
        try:
            self._worker_waiter_leaves_the_landing_slot_unlocked()
        finally:
            if holder.poll() is None:
                with open(self.base / "holder-worker" / "release", "w") as handle:
                    handle.write("x")
            holder.wait(timeout=20)

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

    def test_a_holder_publishes_its_record_and_clears_it_on_release(self):
        self.governor(1)
        with self.no_slot():
            with land.slot("worker", repo=str(self.work), command="make test"):
                record = land._read_holder(land._hold_path("worker-0"))
                self.assertEqual((record.kind, record.repo, record.command), ("worker", str(self.work), "make test"))
                self.assertIsNone(record.cap)
                self.assertIsNone(record.reason)
                self.assertTrue(land._hold_path("worker-0").exists())
            self.assertFalse(land._hold_path("worker-0").exists())

    def test_a_failing_wrapped_command_clears_the_holders_record(self):
        self.governor(1)
        with self.no_slot():
            result = real_process(
                ProcessReason.GOVERNOR, SCRIPT,
                ["slot", "--", "sh", "-c", "exit 3"],
                env=self.slot_env(), cwd=self.base, timeout=20)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertFalse(land._hold_path("worker-0").exists())

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

    def test_a_crashed_holders_temporary_record_file_is_cleaned(self):
        self.governor(1)
        pid = self.dead_pid()
        stale, descriptor = self.temporary_hold(pid, locked=False)
        os.close(descriptor)
        with self.no_slot():
            with land.slot("worker", repo=str(self.work), command="make test"):
                pass
        self.assertFalse(stale.exists())

    def test_a_locked_temporary_record_file_is_kept(self):
        self.governor(1)
        # The pid comes from a process that has exited, so the name cannot collide with the pid this
        # process publishes its own temporary file under.
        pid = self.dead_pid()
        live, descriptor = self.temporary_hold(pid, locked=True)
        try:
            with self.no_slot():
                with land.slot("worker", repo=str(self.work), command="make test"):
                    pass
            self.assertTrue(live.exists())
        finally:
            os.close(descriptor)

    def test_a_live_temporary_record_that_locks_before_the_unlink_is_kept_for_a_race(self):
        """Pin review finding D255-review-1 R2. Starts a live writer process for a race. Orders the writer and the cleanup with FIFOs and no sleep."""
        import select
        self.governor(1)
        holds = self.base / "state/pstack-t3/governor/holds"
        holds.mkdir(parents=True, exist_ok=True)
        fifo_dir = self.base / "r2-fifos"
        fifo_dir.mkdir()
        c2p = fifo_dir / "c2p"
        p2c = fifo_dir / "p2c"
        os.mkfifo(c2p)
        os.mkfifo(p2c)
        c2p_fd = os.open(c2p, os.O_RDWR)
        self.addCleanup(os.close, c2p_fd)
        p2c_fd = os.open(p2c, os.O_RDWR)
        self.addCleanup(os.close, p2c_fd)
        child_code = (
            "import os, fcntl\n"
            f"c2p = os.open({str(c2p)!r}, os.O_RDWR)\n"
            f"p2c = os.open({str(p2c)!r}, os.O_RDWR)\n"
            f"holds = {str(holds)!r}\n"
            "tmp = os.path.join(holds, 'worker-0.%d.tmp' % os.getpid())\n"
            "tmp_fd = os.open(tmp, os.O_RDWR | os.O_CREAT, 0o666)\n"
            "os.write(c2p, b'O')\n"
            "assert os.read(p2c, 1) == b'P'\n"
            "try:\n"
            "    fcntl.flock(tmp_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "except OSError:\n"
            "    os.write(c2p, b'F')\n"
            "    raise SystemExit(1)\n"
            "os.pwrite(tmp_fd, str(os.getpid()).encode(), 0)\n"
            "os.write(c2p, b'L')\n"
            "assert os.read(p2c, 1) == b'D'\n"
            "fcntl.flock(tmp_fd, fcntl.LOCK_UN)\n"
            "os.close(tmp_fd)\n"
            "os.close(c2p)\n"
            "os.close(p2c)\n"
        )
        child = real_popen(
            ProcessReason.RACE,
            [sys.executable, "-c", child_code],
            env=self.slot_env(), cwd=self.base, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop():
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=20)

        self.addCleanup(stop)
        ready, _, _ = select.select([c2p_fd], [], [], 20)
        self.assertTrue(ready, "live writer did not open its record")
        self.assertEqual(os.read(c2p_fd, 1), b"O")
        found = sorted(holds.glob("worker-0.*.tmp"))
        self.assertEqual(len(found), 1)
        target = found[0]
        original_flock = land.fcntl.flock
        coordinated = []

        def coordinating_flock(fd, op):
            if op == fcntl.LOCK_UN and not coordinated:
                try:
                    number = fd.fileno() if hasattr(fd, "fileno") else fd
                    held = os.fstat(number)
                    named = os.stat(target)
                except (OSError, ValueError):
                    return original_flock(fd, op)
                if (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino):
                    result = original_flock(fd, op)
                    os.write(p2c_fd, b"P")
                    waiting, _, _ = select.select([c2p_fd], [], [], 20)
                    self.assertTrue(waiting, "live writer did not lock its record")
                    self.assertEqual(os.read(c2p_fd, 1), b"L")
                    coordinated.append(True)
                    return result
            return original_flock(fd, op)

        try:
            with mock.patch.object(land.fcntl, "flock", coordinating_flock):
                with self.no_slot():
                    with land.slot("worker", repo=str(self.work), command="make test"):
                        pass
            self.assertTrue(coordinated, "cleanup did not probe the live record")
            self.assertTrue(target.exists(), "cleanup removed a live record that locked before the unlink")
            self.assertEqual(target.read_bytes().decode(), str(child.pid))
        finally:
            try:
                os.write(p2c_fd, b"D")
            except OSError:
                pass
        self.assertEqual(child.wait(timeout=20), 0)
        self.assertTrue(target.exists())

    def test_a_publish_retries_when_cleanup_removes_the_temporary_name_before_the_lock(self):
        """Pin review finding D255-review-2 N1. Orders open-before-flock with FIFOs and no sleep. Starts a process for a race."""
        import select
        self.governor(1)
        holds = self.base / "state/pstack-t3/governor/holds"
        holds.mkdir(parents=True, exist_ok=True)
        fifo_dir = self.base / "n1-fifos"
        fifo_dir.mkdir()
        c2p = fifo_dir / "c2p"
        p2c = fifo_dir / "p2c"
        os.mkfifo(c2p)
        os.mkfifo(p2c)
        c2p_fd = os.open(c2p, os.O_RDWR)
        self.addCleanup(os.close, c2p_fd)
        p2c_fd = os.open(p2c, os.O_RDWR)
        self.addCleanup(os.close, p2c_fd)
        child_code = (
            "import os\n"
            f"c2p = os.open({str(c2p)!r}, os.O_RDWR)\n"
            f"p2c = os.open({str(p2c)!r}, os.O_RDWR)\n"
            f"holds = {str(holds)!r}\n"
            "import sys\n"
            f"sys.path.insert(0, {str(SCRIPT.parent)!r})\n"
            "import land\n"
            "from pathlib import Path\n"
            "assert os.read(p2c, 1) == b'O'\n"
            "found = sorted(Path(holds).glob('worker-0.*.tmp'))\n"
            "assert len(found) == 1\n"
            "land._tmp_kept(found[0])\n"
            "os.write(c2p, b'U')\n"
            "assert os.read(p2c, 1) == b'D'\n"
        )
        child = real_popen(
            ProcessReason.RACE,
            [sys.executable, "-c", child_code],
            env=self.slot_env(), cwd=self.base, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop():
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=20)

        self.addCleanup(stop)
        real_open = os.open
        paused = []

        def pausing_open(path, flags, *args, **kwargs):
            try:
                text = os.fspath(path)
            except (TypeError, ValueError):
                return real_open(path, flags, *args, **kwargs)
            if (isinstance(text, str) and text.endswith(f".{os.getpid()}.tmp")
                    and (flags & os.O_CREAT) and not paused):
                descriptor = real_open(path, flags, *args, **kwargs)
                os.write(p2c_fd, b"O")
                ready, _, _ = select.select([c2p_fd], [], [], 20)
                self.assertTrue(ready, "the child did not signal after its cleanup step")
                self.assertEqual(os.read(c2p_fd, 1), b"U")
                paused.append(True)
                return descriptor
            return real_open(path, flags, *args, **kwargs)

        handles = {name: open(land.governor_dir() / name, "a") for name in land._slot_names("exclusive", 1)}
        records = []
        try:
            with mock.patch.object(land.os, "open", pausing_open):
                records = land._publish("worker", handles, [handles["worker-0"]], str(self.work), "make test")
            self.assertTrue(paused, "the writer did not pause after opening its temporary record")
            self.assertEqual(len(records), 1, "cleanup removed the temporary name and the holder published no record")
            record = land._read_holder(land._hold_path("worker-0"))
            self.assertEqual((record.kind, record.command), ("worker", "make test"))
        finally:
            try:
                os.write(p2c_fd, b"D")
            except OSError:
                pass
            for handle in handles.values():
                handle.close()
            land._release_records(records)
        self.assertEqual(child.wait(timeout=20), 0)

    def test_a_store_directory_that_cannot_be_searched_leaves_the_body_alone(self):
        log = self.base / "gh-log"
        with self.fake_gh():
            fake = self.base / "gh"
            fake.write_text(fake.read_text().replace('if args[:2] == ["pr", "create"]:',
                                                     f'open({str(log)!r}, "a").write(repr(args) + "\\n")\nif args[:2] == ["pr", "create"]:'))
            self.init(mode="human")
            self.store_item("w1", "polish", "G2")
            sha = self.worker("w1", {"a.txt": "agent\n"})
            self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1",
                      "--reviewer", REVIEWER)
            # Read the store's tables with no search permission on the store itself, so a stat inside it fails.
            store = self.base / "state/pstack-t3/brigade/proj/store"
            os.chmod(store, 0o444)
            try:
                self.land("land")
            finally:
                os.chmod(store, 0o755)
            self.assertTrue(log.exists(), "the queue opened no pull request")
            create = ast.literal_eval([line for line in log.read_text().splitlines() if "'create'" in line][0])
            self.assertEqual(create[create.index("--body") + 1], "")

    def test_a_nested_slot_yields_without_a_second_record(self):
        self.governor(1)
        with self.no_slot():
            with land.slot("worker", repo=str(self.work), command="outer"):
                with land.slot("worker", repo=str(self.work), command="inner"):
                    self.assertEqual(land._read_holder(land._hold_path("worker-0")).command, "outer")
            self.assertFalse(land._hold_path("worker-0").exists())

    def test_a_nested_exclusive_slot_raises(self):
        self.governor(1)
        with self.no_slot():
            with land.slot("worker", repo=str(self.work), command="outer"):
                with self.assertRaises(land.LandError):
                    with land.exclusive_slot(repo=str(self.work), command="bench"):
                        pass

    def test_a_catchable_signal_clears_the_holders_record(self):
        self.governor(1)
        holder = self.start_holder(kind="worker", command="make test")
        self.assertTrue(land._hold_path("worker-0").exists())
        os.kill(holder.pid, signal.SIGTERM)
        self.assertEqual(holder.wait(timeout=20), 128 + signal.SIGTERM)
        self.assertFalse(land._hold_path("worker-0").exists())

    def test_the_slot_repo_flag_names_the_repository_from_either_position(self):
        self.governor(1)
        chosen = self.base / "chosen"
        chosen.mkdir()
        for position in ("before", "after"):
            with self.subTest(position=position):
                chosen_flag = ["--repo", str(chosen)] if position == "before" else []
                result = real_process(
                    ProcessReason.GOVERNOR, SCRIPT,
                    [*chosen_flag, "slot",
                     *([] if position == "before" else ["--repo", str(chosen)]), "--",
                     sys.executable, "-c", RECORD_REPO_SCRIPT],
                    env=self.slot_env(), cwd=self.base, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), str(chosen))

    def test_the_slot_repo_flag_defaults_to_the_working_directory(self):
        args = land.parser().parse_args(["slot", "--", "true"])
        self.assertEqual((args.slot_repo, args.repo), (None, "."))
        self.assertEqual(land._repo_text(args.slot_repo or args.repo), os.getcwd())

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

    def test_a_reservation_refuses_another_holders_claim_and_admits_the_winner(self):
        self.init()
        self.assertEqual(self.reserve("docs/", "a.txt,b.txt", "R4"), "S1")
        refusal = self.claim("engine/D3", "a.txt", ok=False)
        self.assertEqual(refusal, f"land: paths reserved for docs/ by ruling R4 until {self.reserved_until(1)}")
        self.assertEqual(self.lease_check("engine/D3", "b.txt"), (1, refusal.removeprefix("land: ")))
        self.assertEqual(self.claim("engine/D3", "lib"), "L1")
        self.assertEqual(self.claim("docs/D7", "a.txt,b.txt"), "L2")
        self.assertEqual(self.listed(), ["L1 active engine/D3: lib", "L2 active docs/D7: a.txt, b.txt"])
        self.assertEqual(self.ruling_rows("reservation")[0]["state"], "claimed")

    def test_a_reservation_refuses_readmitting_another_holders_expired_lease(self):
        self.init()
        self.assertEqual(self.claim("engine/D3", "a.txt", "--ttl-hours", "0"), "L1")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.assertEqual(self.land("lease", "renew", "L1", ok=False),
                         f"land: paths reserved for docs/ by ruling R4 until {self.reserved_until(1)}")
        self.assertEqual(self.listed(), ["S1 armed docs/ by R4: a.txt"])
        self.assertEqual(self.claim("docs/D7", "a.txt"), "L2")

    def test_a_claim_covering_one_reserved_path_leaves_the_other_and_counts_once(self):
        self.init()
        self.land("cap", "2")
        self.reserve("docs/", "a.txt,b.txt", "R4")
        self.assertIn("changes in flight: 1 of 2", self.land("status"))
        self.assertEqual(self.claim("docs/D7", "a.txt"), "L1")
        self.assertEqual(self.land("status"), "push mode onto refs/remotes/origin/main. leases held: 1, changes in flight: 1 of 2.")
        self.assertEqual(self.listed(), ["L1 active docs/D7: a.txt", "S1 armed docs/ by R4: b.txt"])
        self.assertIn("paths reserved for docs/ by ruling R4", self.claim("engine/D3", "b.txt", ok=False))
        self.assertEqual(self.claim("engine/D3", "lib"), "L2")
        self.assertEqual(self.claim("ops/D1", "c.txt", ok=False), "land: repository at its cap: 2 of 2 changes in flight (docs/D7, engine/D3)")

    def test_a_reservation_waits_to_arm_while_another_holders_live_lease_overlaps(self):
        self.init()
        self.claim("engine/D1", "a.txt")
        self.assertEqual(self.reserve("docs/", "a.txt,b.txt", "R4"), "S1")
        self.assertEqual(self.lease_check("ops/D1", "c.txt"), (0, "free"))
        self.assertEqual(self.listed(), ["L1 active engine/D1: a.txt", "S1 waiting docs/ by R4: a.txt, b.txt"])
        waiting = "paths reserved for docs/ by ruling R4 until 2 hours after it arms"
        self.assertEqual(self.claim("engine/D2", "b.txt", ok=False), f"land: {waiting}")
        self.assertEqual(self.lease_check("engine/D2", "b.txt"), (1, waiting))
        self.assertEqual(self.ruling_rows("reservation")[0]["armed"], "")
        self.land("lease", "release", "L1")
        self.assertEqual(self.listed(), ["S1 armed docs/ by R4: a.txt, b.txt"])
        self.assertEqual(self.lease_check("ops/D1", "c.txt"), (0, "free"))
        self.assertEqual(self.listed(), ["S1 armed docs/ by R4: a.txt, b.txt"])

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

    def test_lease_check_leaves_a_reservation_waiting_that_a_claim_would_arm(self):
        self.init()
        self.claim("engine/D1", "a.txt")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.assertEqual(self.listed(), ["S1 waiting docs/ by R4: a.txt"])
        for _ in range(2):
            self.assertEqual(self.check_with_dump_unchanged("ops/D2", "d.txt"), (0, "free"))
            code, refusal = self.check_with_dump_unchanged("engine/D9", "a.txt")
            self.assertEqual(code, 1)
            self.assert_reserved_for_two_hours(refusal)
            self.assertEqual(self.check_with_dump_unchanged("docs/D7", "a.txt"), (0, "free"))
        self.assertEqual(self.listed(), ["S1 waiting docs/ by R4: a.txt"])
        self.assertEqual(self.armed_log(), [])

    def test_a_reservations_hold_starts_at_the_first_claim_after_lease_checks(self):
        self.init()
        self.claim("engine/D1", "a.txt")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        for holder, paths in [("ops/D2", "d.txt"), ("engine/D9", "a.txt"), ("docs/D7", "a.txt")] * 2:
            self.lease_check(holder, paths)
        claimed = datetime.now(timezone.utc)
        self.assertEqual(self.claim("ops/D2", "d.txt"), "L2")
        row = self.ruling_rows("reservation")[0]
        armed = datetime.fromisoformat(row["armed"])
        self.assertGreaterEqual(armed, claimed)
        self.assertEqual(datetime.fromisoformat(row["expires"]) - armed, timedelta(hours=2))
        self.assertEqual(self.listed(), ["L2 active ops/D2: d.txt", "S1 armed docs/ by R4: a.txt"])
        self.assertEqual(self.armed_log(), [1])
        self.assert_reserved_for_two_hours(self.claim("engine/D9", "a.txt", ok=False))

    def test_lease_check_naming_an_overlapping_lease_claims_no_lease_arms_no_reservation_starts_no_hold_and_adds_no_log_row(self):
        self.init()
        self.claim("engine/D1", "a.txt")
        self.claim("ops/D1", "b.txt")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.assertEqual(self.check_with_dump_unchanged("engine/D9", "b.txt"), (1, "L2 held by ops/D1 on b.txt"))
        self.assertEqual(self.listed(), ["L2 active ops/D1: b.txt", "S1 waiting docs/ by R4: a.txt"])

    def test_lease_check_at_the_cap_claims_no_lease_arms_no_reservation_starts_no_hold_and_adds_no_log_row_and_the_refused_claim_arms(self):
        self.init()
        self.land("cap", "1")
        self.claim("ops/D1", "lib")
        self.assertEqual(self.reserve("docs/", "a.txt", "R1"), "S1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        at_cap = "repository at its cap: 1 of 1 changes in flight (S1 for docs/)"
        for _ in range(2):
            self.assertEqual(self.check_with_dump_unchanged("ops/D2", "c.txt"), (1, at_cap))
        self.assertEqual(self.listed(), ["S1 waiting docs/ by R1: a.txt"])
        self.assertEqual(self.claim("ops/D2", "c.txt", ok=False), f"land: {at_cap}")
        self.assertEqual(self.listed(), ["S1 armed docs/ by R1: a.txt"])
        self.assertEqual(self.armed_log(), [1])

    def test_lease_check_at_a_share_claims_no_lease_arms_no_reservation_starts_no_hold_and_adds_no_log_row(self):
        self.init()
        self.land("cap", "4")
        self.share("docs/", 2)
        self.claim("engine/D1", "a.txt")
        self.assertEqual(self.reserve("ops/", "a.txt", "R4"), "S1")
        self.claim("docs/D1", "b.txt")
        self.claim("docs/D2", "c.txt")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.assertEqual(self.check_with_dump_unchanged("docs/D3", "lib"), (1, "docs/ is at its share: 2 of 2"))
        self.assertEqual(self.listed(), ["L2 active docs/D1: b.txt", "L3 active docs/D2: c.txt", "S1 waiting ops/ by R4: a.txt"])
        self.assertEqual(self.claim("docs/D3", "lib", ok=False), "land: docs/ is at its share: 2 of 2")

    def test_lease_check_answers_free_past_a_reservation_whose_hold_is_zero_hours(self):
        self.init()
        self.claim("engine/D1", "a.txt")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4", "--ttl-hours", "0"), "S1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.assertEqual(self.check_with_dump_unchanged("engine/D9", "a.txt"), (0, "free"))
        self.assertEqual(self.claim("engine/D9", "a.txt"), "L2")

    def test_lease_check_on_an_older_store_upgrades_the_schema_and_leaves_lease_reservation_and_log_rows_unchanged(self):
        self.init()
        self.claim("engine/D1", "a.txt")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.rebuild_entry_without_autoincrement()

        def entry_sql():
            with self.raw_db() as db:
                return db.execute("SELECT sql FROM sqlite_master WHERE name = 'entry'").fetchone()[0]

        def rows():
            with self.raw_db() as db:
                return {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                        for table in ("contract", "lease", "attempt", "log", "owner", "reservation", "share", "contest")}

        self.assertNotIn("AUTOINCREMENT", entry_sql())
        self.assertNotIn("title", entry_sql())
        before = rows()
        code, refusal = self.lease_check("engine/D9", "a.txt")
        self.assertEqual(code, 1)
        self.assert_reserved_for_two_hours(refusal)
        self.assertIn("AUTOINCREMENT", entry_sql())
        self.assertIn("title", entry_sql())
        self.assertEqual(rows(), before, "rows of every table except entry, which the schema upgrade rebuilds, and sqlite_sequence, which gains an entry row")
        with self.raw_db() as db:
            self.assertEqual(db.execute("SELECT id, holder FROM lease").fetchall(), [(1, "engine/D1")])
            self.assertEqual(db.execute("SELECT armed, expires FROM reservation").fetchall(), [("", "")])
            self.assertEqual(db.execute("SELECT count(*) FROM log WHERE state = 'armed'").fetchall(), [(0,)])

    def test_releasing_one_lease_leaves_a_reservation_waiting_on_another(self):
        self.init()
        self.claim("engine/D1", "a.txt")
        self.claim("ops/D1", "b.txt")
        self.assertEqual(self.reserve("docs/", "a.txt,b.txt", "R4"), "S1")
        self.land("lease", "release", "L1")
        self.assertEqual(self.listed(), [
            "L2 active ops/D1: b.txt",
            "S1 waiting docs/ by R4: a.txt, b.txt",
        ])
        self.land("lease", "release", "L2")
        self.assertEqual(self.listed(), ["S1 armed docs/ by R4: a.txt, b.txt"])

    def test_landing_an_entry_arms_a_reservation_that_was_waiting_on_its_lease(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "landed\n"})
        self.claim("engine/D1", "a.txt")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.land("submit", "--holder", "engine/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (engine/D1)")
        self.assertEqual(self.listed(), ["S1 armed docs/ by R4: a.txt"])
        armed = next(row for row in self.ruling_rows("reservation") if row["id"] == 1)
        left = datetime.fromisoformat(armed["expires"]) - datetime.now(timezone.utc)
        self.assertLess(abs(left - timedelta(hours=2)), timedelta(minutes=1))

    def test_with_a_cap_of_one_two_waiting_reservations_arm_one_at_a_time_oldest_first(self):
        self.init()
        self.land("cap", "1")
        self.claim("ops/D1", "lib")
        self.assertEqual(self.reserve("docs/", "a.txt", "R1"), "S1")
        self.assertEqual(self.reserve("engine/", "b.txt", "R2"), "S2")
        self.assertEqual(self.listed(), ["L1 active ops/D1: lib", "S1 waiting docs/ by R1: a.txt", "S2 waiting engine/ by R2: b.txt"])
        self.land("lease", "release", "L1")
        self.assertEqual(self.lease_check("ops/D2", "c.txt"), (1, "repository at its cap: 1 of 1 changes in flight (S1 for docs/)"))
        self.assertEqual(self.listed(), ["S1 armed docs/ by R1: a.txt", "S2 waiting engine/ by R2: b.txt"])
        self.assertEqual(self.claim("docs/D7", "a.txt"), "L2")
        self.assertEqual(self.listed(), ["L2 active docs/D7: a.txt", "S2 waiting engine/ by R2: b.txt"])
        self.assertEqual(self.claim("engine/D3", "b.txt", ok=False), "land: repository at its cap: 1 of 1 changes in flight (docs/D7)")
        self.land("lease", "release", "L2")
        self.assertEqual(self.claim("engine/D3", "b.txt"), "L3")
        self.assertEqual(self.listed(), ["L3 active engine/D3: b.txt"])

    def test_two_coordinators_claiming_reserved_paths_at_once_leave_only_the_winners_lease(self):
        self.init()
        for n in range(1, 5):
            self.reserve("docs/", f"p{n}.txt", f"R{n}")
            results = self.concurrent(["lease", "claim", "--holder", f"engine/D{n}", "--paths", f"p{n}.txt"],
                                      ["lease", "claim", "--holder", f"docs/D{n}", "--paths", f"p{n}.txt"])
            (engine_code, _, engine_err), (docs_code, docs_out, _) = results
            self.assertEqual((engine_code, docs_code, docs_out), (1, 0, f"L{n}"), results)
            self.assertTrue(engine_err.startswith("land: paths "), engine_err)
        self.assertEqual(self.listed(), [f"L{n} active docs/D{n}: p{n}.txt" for n in range(1, 5)])

    def test_a_reservation_rerun_returns_it_and_an_ended_one_says_how_it_ended(self):
        self.init()
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4"), "S1")
        self.assertEqual(self.reserve("engine/", "a.txt,b.txt", "R5", ok=False), "land: paths overlap S1 reserved for docs/ by ruling R4 on a.txt")
        self.assertEqual(self.land("lease", "unreserve", "S1", *ADMIN), "S1 lifted")
        self.assertEqual(self.land("lease", "unreserve", "S1", *ADMIN), "S1 lifted")
        self.assertEqual(self.reserve("docs/", "a.txt", "R4", ok=False), "land: S1 for ruling R4 was lifted")
        self.assertEqual(self.claim("engine/D3", "a.txt"), "L1")
        self.assertEqual(self.reserve("docs/", "b.txt", "R6", "--ttl-hours", "0"), "S2")
        self.assertEqual(self.reserve("docs/", "b.txt", "R6", ok=False), "land: S2 for ruling R6 expired")
        self.assertEqual(self.claim("engine/D4", "b.txt"), "L2")
        self.assertRegex(self.land("lease", "unreserve", "S2", *ADMIN), r"^S2 expired at \d{4}-\d\d-\d\dT\d\d:\d\d$")
        self.assertEqual(self.land("lease", "unreserve", "S9", *ADMIN, ok=False), "land: no S9")
        self.assertEqual(self.reserve("docs", "c.txt", "R7", ok=False), "land: 'docs' is not a holder prefix such as docs/")
        self.assertEqual(self.reserve("docs/", "c.txt", "R8"), "S3")
        self.assertEqual(self.claim("docs/D7", "c.txt"), "L3")
        self.assertEqual(self.reserve("docs/", "c.txt", "R8"), "S3 was claimed in full")

    def test_a_reservation_refuses_the_whole_repository(self):
        self.init()
        refusal = "land: a reservation names the contested paths; it cannot hold the whole repository"
        self.assertEqual(self.reserve("docs/", "", "R4", ok=False), refusal)
        self.assertEqual(self.reserve("docs/", "a.txt,.", "R4", ok=False), refusal)
        self.assertEqual(self.claim("engine/D3", "lib"), "L1")
        self.assertEqual(self.listed(), ["L1 active engine/D3: lib"])

    def test_shares_refuse_a_sum_over_the_cap_and_admission_refuses_a_claim_over_a_share(self):
        self.init()
        self.land("cap", "4")
        self.assertEqual(self.share("docs/", 2), "docs/ share is 2")
        self.assertEqual(self.share("engine/", 3, ok=False), "land: shares would add up to 5, over the cap of 4")
        self.assertEqual(self.share("engine/", 2), "engine/ share is 2")
        self.assertEqual(self.share("engine/", 2), "engine/ share is 2")
        self.assertEqual(self.claim("docs/D1", "a.txt"), "L1")
        self.assertEqual(self.claim("docs/D2", "b.txt"), "L2")
        self.assertEqual(self.claim("docs/D3", "lib", ok=False), "land: docs/ is at its share: 2 of 2")
        self.assertEqual(self.lease_check("docs/D3", "lib"), (1, "docs/ is at its share: 2 of 2"))
        self.assertEqual(self.claim("ops/D1", "lib"), "L3")
        self.assertEqual(self.share("docs/", 1, "--clear", ok=False), "land: --clear takes 0")
        self.assertEqual(self.share("docs/", 0, "--clear"), "docs/ share cleared")
        self.assertEqual(self.claim("docs/D3", "c.txt"), "L4")
        self.assertEqual([(row["prefix"], row["count"]) for row in self.ruling_rows("share")], [("engine/", 2)])

    def test_a_cap_below_the_share_sum_is_refused_and_zero_still_clears(self):
        self.init()
        self.land("cap", "4")
        self.share("docs/", 2)
        self.share("engine/", 2)

        def snapshot():
            with land.Store.for_repo(self.work) as store:
                shares = [(row["prefix"], row["count"]) for row in store.db.execute(
                    "SELECT prefix, count FROM share ORDER BY prefix")]
                log = [(row["kind"], row["id"], row["state"], row["note"]) for row in store.db.execute(
                    "SELECT kind, id, state, note FROM log ORDER BY at")]
                return store.contract.get("cap"), shares, log

        before = snapshot()
        refusal = "land: shares add up to 4 (docs/ 2, engine/ 2), over the cap of 3"
        self.assertEqual(self.land("cap", "3", ok=False), refusal)
        self.assertEqual(snapshot(), before)
        self.assertIn("changes in flight: 0 of 4", self.land("status"))
        self.assertEqual(self.land("cap", "4"), "repository cap is now 4 changes in flight")
        self.assertEqual(self.land("cap", "0"), "repository cap cleared")
        cap, shares, _log = snapshot()
        self.assertIsNone(cap)
        self.assertEqual(shares, [("docs/", 2), ("engine/", 2)])
        self.assertNotIn("of ", self.land("status"))
        self.assertEqual(self.land("cap", "3", ok=False), refusal)
        self.assertEqual(self.land("cap", "4"), "repository cap is now 4 changes in flight")

    def test_an_armed_reservation_counts_toward_its_prefixs_share_until_the_winner_claims(self):
        self.init()
        self.share("docs/", 1)
        self.reserve("docs/", "a.txt", "R4")
        self.assertEqual(self.claim("docs/D9", "b.txt", ok=False), "land: docs/ is at its share: 1 of 1")
        self.assertEqual(self.claim("docs/D9", "a.txt"), "L1")
        self.assertEqual(self.claim("docs/D10", "b.txt", ok=False), "land: docs/ is at its share: 1 of 1")

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

    def test_a_reservation_counts_toward_the_cap_again_after_its_taken_lease_is_released(self):
        self.reservation_counts_again_after_its_lease_ends(
            "release", lambda: self.land("cap", "1"), "repository at its cap: 1 of 1 changes in flight (S1 for docs/)", "engine/D3")
        self.assertEqual(self.land("status"), "push mode onto refs/remotes/origin/main. leases held: 1, changes in flight: 1 of 1.")

    def test_a_reservation_counts_toward_the_cap_again_after_its_taken_lease_expires(self):
        self.reservation_counts_again_after_its_lease_ends(
            "expire", lambda: self.land("cap", "1"), "repository at its cap: 1 of 1 changes in flight (S1 for docs/)", "engine/D3")

    def test_a_reservation_counts_toward_its_share_again_after_its_taken_lease_is_released(self):
        self.reservation_counts_again_after_its_lease_ends(
            "release", lambda: self.share("docs/", 1), "docs/ is at its share: 1 of 1", "docs/D8")

    def test_a_reservation_counts_toward_its_share_again_after_its_taken_lease_expires(self):
        self.reservation_counts_again_after_its_lease_ends(
            "expire", lambda: self.share("docs/", 1), "docs/ is at its share: 1 of 1", "docs/D8")

    def test_a_reservation_stays_out_of_the_count_while_any_lease_taken_from_it_is_live(self):
        self.init()
        self.land("cap", "2")
        self.reserve("docs/", "a.txt,b.txt,c.txt", "R4")
        self.assertEqual(self.claim("docs/D7", "a.txt"), "L1")
        self.assertEqual(self.claim("docs/D7", "b.txt"), "L2")
        self.land("lease", "release", "L1")
        self.assertEqual(self.claim("engine/D3", "lib"), "L3")
        self.land("lease", "release", "L2")
        self.assertEqual(self.claim("ops/D1", "d.txt", ok=False),
                         "land: repository at its cap: 2 of 2 changes in flight (engine/D3, S1 for docs/)")
        self.assertEqual(self.claim("docs/D7", "c.txt"), "L4")
        self.assertEqual(self.listed(), ["L3 active engine/D3: lib", "L4 active docs/D7: c.txt"])

    def test_the_winner_renews_its_expired_lease_taken_from_a_reservation_at_a_full_cap(self):
        self.init()
        self.land("cap", "1")
        self.reserve("docs/", "a.txt,b.txt", "R4")
        self.assertEqual(self.claim("docs/D7", "a.txt", "--ttl-hours", "0"), "L1")
        self.assertEqual(self.land("lease", "renew", "L1"), "L1 renewed")
        self.assertEqual(self.claim("engine/D3", "lib", ok=False), "land: repository at its cap: 1 of 1 changes in flight (docs/D7)")
        self.assertEqual(self.listed(), ["L1 active docs/D7: a.txt", "S1 armed docs/ by R4: b.txt"])

    def contest_holds_both_holders(self, mode, **extra):
        self.init(mode=mode, **extra)
        self.assertEqual(self.queue_one(path="a.txt", name="w1", holder="docs/D7"), "E1")
        self.assertEqual(self.contest("--holders", "docs/D7,engine/D3"), "C1")
        self.assertEqual(self.contest("--holders", "engine/D3,docs/D7"), "C1")
        self.assertEqual(self.queue_one(path="b.txt", name="w2", holder="engine/D3"), "E2")
        self.assertEqual(self.land("land"), "still queued: E1 (held by C1), E2 (held by C1)")
        self.assertEqual(self.land("status", "--holder", "docs/D7").split(" (")[0], "E1 queued")
        self.assertEqual(self.land("status", "--holder", "engine/D3").split(" (")[0], "E2 queued")

    def test_an_open_contest_holds_both_holders_out_of_a_push_mode_batch(self):
        self.contest_holds_both_holders("push", batch=4)
        self.assertEqual(self.queue_one(path="lib/x.py", name="w3", holder="ops/D1"), "E3")
        self.assertEqual(self.land("land"), "landed E3 (ops/D1)\nstill queued: E1 (held by C1), E2 (held by C1)")
        self.assertEqual(self.origin_log(), ["w3", "init"])

    def test_an_open_contest_holds_both_holders_in_local_mode(self):
        self.contest_holds_both_holders("local", trunk="lane", base="main")
        self.assertEqual(sh("git", "rev-parse", "refs/landing/lane", cwd=self.work), sh("git", "rev-parse", "main", cwd=self.work))

    def test_an_open_contest_keeps_both_holders_out_of_pr_opening_in_human_mode(self):
        with self.fake_gh():
            self.contest_holds_both_holders("human")
            calls = (self.base / "gh-calls").read_text() if (self.base / "gh-calls").exists() else ""
            self.assertNotIn("pr create", calls)
            self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))

    def test_an_open_contest_keeps_both_holders_out_of_pr_opening_in_merge_mode(self):
        with self.fake_gh():
            self.contest_holds_both_holders("merge")
            self.assertNotIn("pr create", (self.base / "gh-calls").read_text())
            self.assertFalse((self.base / "merge-calls").exists())

    def test_a_settled_contest_holds_the_second_holder_until_the_first_lands_after_a_bounce(self):
        self.init()
        self.queue_one(path="a.txt", text="BROKEN\n", name="w1", holder="docs/D7")
        self.contest("--holders", "docs/D7,engine/D3")
        self.queue_one(path="b.txt", name="w2", holder="engine/D3")
        settled = "C1: docs/D7 lands first, engine/D3 waits"
        self.assertEqual(self.contest("--settle", "C1", "--first", "docs/D7"), settled)
        self.assertEqual(self.contest("--settle", "C1", "--first", "docs/D7"), settled)
        out = self.land("land")
        self.assertTrue(out.startswith("bounced E1 (docs/D7): checks failed:"), out)
        self.assertTrue(out.endswith("\nstill queued: E2 (held by C1)"), out)
        (self.base / "w1" / "a.txt").write_text("fixed\n")
        fixed = self.commit("fix", cwd=self.base / "w1")
        self.assertEqual(self.land("submit", "--holder", "docs/D7", "--branch", "w1", "--sha", fixed, "--lease", "L1", "--reviewer", REVIEWER), "E3")
        self.assertEqual(self.land("land"), "landed E3 (docs/D7), E2 (engine/D3)")
        self.assertEqual(self.origin_log(), ["w2", "fix", "w1", "init"])
        self.assertEqual(self.contest("--settle", "C1", "--first", "docs/D7"), settled)
        self.assertEqual(self.contest("--settle", "C1", "--first", "engine/D3", ok=False), "land: C1 is done: docs/D7 landed")

    def test_a_contest_settles_again_in_reverse_and_refuses_an_order_that_closes_a_cycle(self):
        self.init()
        self.assertEqual(self.contest("--holders", "docs/D7,engine/D3"), "C1")
        self.contest("--settle", "C1", "--first", "docs/D7")
        self.assertEqual(self.contest("--settle", "C1", "--first", "engine/D3"), "C1: engine/D3 lands first, docs/D7 waits")
        self.assertEqual(self.contest("--holders", "docs/D7,ops/D1"), "C2")
        self.contest("--settle", "C2", "--first", "docs/D7")
        self.assertEqual(self.contest("--holders", "ops/D1,engine/D3"), "C3")
        self.assertEqual(self.contest("--settle", "C3", "--first", "ops/D1", ok=False),
                         "land: C3 cannot order ops/D1 before engine/D3: it closes a cycle with C1, C2")
        self.assertEqual(self.contest("--settle", "C3", "--first", "engine/D3"), "C3: engine/D3 lands first, ops/D1 waits")
        self.assertEqual(self.contest("--cancel", "C1"), "C1 cancelled")
        self.assertEqual(self.contest("--settle", "C3", "--first", "ops/D1"), "C3: ops/D1 lands first, engine/D3 waits")
        self.assertEqual(self.contest("--settle", "C1", "--first", "docs/D7", ok=False), "land: C1 is cancelled")
        self.assertEqual(self.contest("--settle", "C2", "--first", "docs/D9", ok=False), "land: docs/D9 is not a holder in C2")
        self.assertEqual(self.contest("--holders", "docs/D7,docs/D7", ok=False), "land: a contest needs two different holders")
        self.assertEqual(self.contest("--cancel", "C9", ok=False), "land: no C9")

    def test_a_contest_refuses_work_already_landed_and_ignores_a_bounce(self):
        self.init()
        self.queue_one(path="a.txt", name="w1", holder="docs/D7")
        self.queue_one(path="b.txt", text="BROKEN\n", name="w2", holder="engine/D3")
        self.land("land")
        self.assertEqual(self.contest("--holders", "engine/D3,docs/D7", ok=False), "land: docs/D7 has E1 landed; there is nothing left to order")
        self.assertEqual(self.contest("--holders", "engine/D3,ops/D1"), "C1")

    def test_a_contest_waits_for_a_running_land_to_release_the_queue_lock(self):
        import fcntl
        self.init()
        with land.Store.for_repo(self.work) as store:
            with open(store.dir / ".queue.lock", "a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                proc = real_popen(
                    ProcessReason.LOCK,
                    [sys.executable, str(SCRIPT), "--repo", str(self.work), "contest", "--holders", "docs/D7,engine/D3", *ADMIN],
                    env=os.environ.copy())
                time.sleep(1)
                self.assertIsNone(proc.poll())
                self.assertEqual(self.ruling_rows("contest"), [])
            out, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, out.strip()), (0, "C1"), err)

    def test_every_ruling_write_needs_an_owner(self):
        self.init()
        for args in (["lease", "reserve", "--for", "docs/", "--paths", "a.txt", "--ruling", "R1"], ["lease", "unreserve", "S1"],
                     ["share", "--for", "docs/", "1"], ["contest", "--holders", "a/D1,b/D1"], ["contest", "--cancel", "C1"]):
            self.assertIn("--owner", self.land(*args, ok=False))

    def test_paused_ruling_writes_from_a_replaced_admin_change_nothing(self):
        self.init()
        self.land("cap", "4")
        self.share("docs/", 1)
        self.contest("--holders", "docs/D7,engine/D3")
        self.contest("--settle", "C1", "--first", "docs/D7")
        tables = ("share", "reservation", "contest")
        before = {table: self.ruling_rows(table) for table in tables}
        commands = [
            ["share", "--for", "docs/", "2", *ADMIN],
            ["lease", "reserve", "--for", "docs/", "--paths", "a.txt", "--ruling", "R9", *ADMIN],
            ["contest", "--settle", "C1", "--first", "engine/D3", *ADMIN],
        ]
        running = []
        for number, args in enumerate(commands):
            case = self.base / f"paused{number}"
            case.mkdir()
            proc = real_popen(
                ProcessReason.RACE,
                [sys.executable, str(Path(__file__).resolve()), "--paused-child", str(case), "--repo", str(self.work), *args],
                env=os.environ.copy())
            running.append((case, proc))
        for case, proc in running:
            deadline = time.monotonic() + 20
            while not (case / "paused").exists():
                if proc.poll() is not None or time.monotonic() > deadline:
                    self.fail(f"{case.name} did not pause: {proc.communicate()}")
                time.sleep(0.01)
        self.assertEqual(self.land("owner", "--prefix", ".admin/", "--generation", "2"), ".admin/ at generation 2")
        for case, proc in running:
            (case / "proceed").touch()
            out, err = proc.communicate(timeout=30)
            self.assertEqual((proc.returncode, err.strip()), (1, "land: owner .admin/@1 is stale; .admin/ is at generation 2"), out)
        self.assertEqual({table: self.ruling_rows(table) for table in tables}, before)
        current = ("--owner", ".admin/@2")
        self.assertEqual(self.land("share", "--for", "docs/", "2", *current), "docs/ share is 2")
        self.assertEqual(self.land("lease", "reserve", "--for", "docs/", "--paths", "a.txt", "--ruling", "R9", *current), "S1")
        self.assertEqual(self.land("contest", "--settle", "C1", "--first", "engine/D3", *current), "C1: engine/D3 lands first, docs/D7 waits")
        self.assertEqual(self.land("contest", "--cancel", "C1", *ADMIN, ok=False), "land: owner .admin/@1 is stale; .admin/ is at generation 2")

    def test_mode_merge_after_init_in_human_mode_swaps_a_disallowed_method(self):
        with self.fake_gh():
            self.allow_methods(squash=True)
            self.init(mode="human")
            self.assertEqual(self.stored_merge_method(), "merge")
            self.assertEqual(self.land("mode", "merge"), "landing mode is now merge, merging with --squash")
            self.assertEqual(self.stored_merge_method(), "squash")
            self.assertEqual(self.land("mode", "human", "--merge-method", "merge", ok=False),
                             "land: repository does not allow merge; allowed: squash")
            self.assertEqual(self.stored_merge_method(), "squash")
            self.assertTrue(self.land("status").startswith("merge mode onto"))
            self.assertEqual(self.land("mode", "human", "--merge-method", "squash"), "landing mode is now human, merging with --squash")

    def test_init_in_any_mode_refuses_a_merge_method_the_repository_disallows(self):
        with self.fake_gh():
            self.allow_methods(squash=True)
            self.assertEqual(self.land("init", "--trunk", "main", "--mode", "human", "--check", "./check.sh", "--merge-method", "merge", ok=False),
                             "land: repository does not allow merge; allowed: squash")
            self.assertIn("has no landing contract", self.land("status", ok=False))
            self.init(mode="push", merge_method="squash")
            self.assertEqual(self.stored_merge_method(), "squash")


    def test_a_settled_contest_keeps_its_order_once_the_first_holders_pr_is_open(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one(path="a.txt", name="w1", holder="docs/D7")
            self.contest("--holders", "docs/D7,engine/D3")
            self.queue_one(path="b.txt", name="w2", holder="engine/D3")
            self.contest("--settle", "C1", "--first", "docs/D7")
            self.assertEqual(self.land("land"), "opened PRs for E1 (docs/D7) https://github.com/o/r/pull/9\nstill queued: E2 (held by C1)")
            self.assertEqual(self.contest("--settle", "C1", "--first", "engine/D3", ok=False),
                             "land: C1 cannot put engine/D3 first: docs/D7 has E1 awaiting-merge")
            self.assertEqual(self.contest("--settle", "C1", "--first", "docs/D7"), "C1: docs/D7 lands first, engine/D3 waits")

    def test_mode_merge_checks_the_method_stored_when_it_writes(self):
        with self.fake_gh():
            self.allow_methods(squash=True)
            self.init(mode="human", merge_method="squash")
            self.pause_repo_view()
            (self.base / "pause-repo-view").write_text("")
            child = real_popen(
                ProcessReason.RACE,
                [sys.executable, str(SCRIPT), "--repo", str(self.work), "mode", "merge"],
                env=os.environ.copy())
            try:
                deadline = time.monotonic() + 10
                while not (self.base / "repo-view-waiting").exists():
                    if child.poll() is not None or time.monotonic() > deadline:
                        self.fail(f"repo view did not pause: {child.communicate()}")
                    time.sleep(0.01)
                (self.base / "pause-repo-view").unlink()
                self.init(mode="human")
                self.assertEqual(self.stored_merge_method(), "merge")
                (self.base / "repo-view-release").write_text("")
                out, err = child.communicate(timeout=10)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
            self.assertEqual((child.returncode, out.strip()), (0, "landing mode is now merge, merging with --squash"), err)
            self.assertEqual(self.stored_merge_method(), "squash")

    def test_mode_local_checks_an_explicit_merge_method(self):
        with self.fake_gh():
            self.allow_methods(squash=True)
            self.init(mode="local", base="main", merge_method="squash")
            self.assertEqual(self.land("mode", "local", "--merge-method", "merge", ok=False),
                             "land: repository does not allow merge; allowed: squash")
            self.assertEqual(self.stored_merge_method(), "squash")

    def test_a_settled_contest_keeps_its_order_while_a_failed_pr_still_has_auto_merge(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            (self.base / "disable-auto-fails").write_text("API unavailable")
            self.arm_auto_merge()
            self.init(mode="merge")
            self.queue_one(path="a.txt", name="w1", holder="docs/D7")
            self.contest("--holders", "docs/D7,engine/D3")
            self.queue_one(path="b.txt", name="w2", holder="engine/D3")
            self.contest("--settle", "C1", "--first", "docs/D7")
            self.land("land")
            self.land("land")
            (self.base / "checks").write_text("failed")
            self.assertIn("Auto-merge is still enabled: API unavailable", self.land("land"))
            sha = self.land("status", "--holder", "docs/D7").split(", ")[1][:12]
            self.assertEqual(self.land("submit", "--holder", "docs/D7", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER),
                             "E1 already awaiting-merge")
            self.assertEqual(self.contest("--settle", "C1", "--first", "engine/D3", ok=False),
                             "land: C1 cannot put engine/D3 first: docs/D7 has E1 awaiting-merge")

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

    def test_human_mode_bounces_an_entry_with_a_foreign_commit_author_and_opens_no_pr(self):
        with self.fake_gh():
            self.init(mode="human")
            self.configure_owner()
            self.queue_one()
            sha = sh("git", "rev-parse", "w1", cwd=self.work)
            self.assertEqual(self.land("land"), self.foreign_bounce(sha))
        self.assertFalse(self.ref_exists("refs/heads/landing/e1", self.base / "origin.git"))
        self.assertFalse((self.base / "gh-calls").exists())

    def test_human_mode_opens_a_pr_for_an_entry_by_the_configured_author(self):
        with self.fake_gh():
            self.init(mode="human")
            self.configure_owner()
            self.worker("w1", {"a.txt": "agent\n"})
            sh("git", "commit", "-q", "--amend", "--author", "Owner <owner@example.com>", "-m", "w1", cwd=self.base / "w1")
            sha = sh("git", "rev-parse", "HEAD", cwd=self.base / "w1")
            self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
            self.assertEqual(self.land("land"), "opened PRs for E1 (r/D1) https://github.com/o/r/pull/9")
        self.assertEqual(self.created_heads(), ["landing/e1"])

    def test_push_mode_bounces_an_entry_with_a_foreign_commit_author_and_leaves_trunk(self):
        sha, printed = self.land_one_by("t <t@t>", "-m", "w1")
        self.assertEqual(printed, self.foreign_bounce(sha))
        self.assertEqual(self.origin_log(), ["init"])

    def test_push_mode_lands_an_entry_by_the_configured_author(self):
        _, printed = self.land_one_by("Owner <owner@example.com>", "-m", "w1")
        self.assertEqual(printed, "landed E1 (r/D1)")
        self.assertEqual(self.on_origin("log", "-1", "--format=%an <%ae> %s", "main"), "Owner <owner@example.com> w1")

    def test_an_author_with_the_configured_email_and_another_name_bounces(self):
        sha, printed = self.land_one_by("Other <owner@example.com>", "-m", "w1")
        self.assertEqual(printed, self.foreign_bounce(sha, "Other <owner@example.com>"))

    def test_a_configured_name_with_surrounding_spaces_bounces_an_author_without_them(self):
        sha, printed = self.land_one_by("Owner <owner@example.com>", "-m", "w1", owner=(" Owner ", "owner@example.com"))
        self.assertEqual(printed, self.foreign_bounce(sha, "Owner <owner@example.com>", " Owner  <owner@example.com>"))
        self.assertEqual(self.origin_log(), ["init"])

    def test_a_configured_email_with_surrounding_spaces_bounces_an_author_without_them(self):
        sha, printed = self.land_one_by("Owner <owner@example.com>", "-m", "w1", owner=("Owner", " owner@example.com "))
        self.assertEqual(printed, self.foreign_bounce(sha, "Owner <owner@example.com>", "Owner < owner@example.com >"))
        self.assertEqual(self.origin_log(), ["init"])

    def test_an_author_name_with_a_carriage_return_bounces_under_a_configured_name_with_a_newline_there(self):
        sha, printed = self.land_one_by("A\rB <owner@example.com>", "-m", "w1", owner=("A\nB", "owner@example.com"))
        self.assertEqual(printed, self.foreign_bounce(sha, r"A\rB <owner@example.com>", r"A\nB <owner@example.com>"))
        self.assertEqual(self.origin_log(), ["init"])

    def test_without_a_configured_identity_an_entry_by_any_author_lands(self):
        self.init(mode="push")
        for key in ("user.name", "user.email"):
            self.assertEqual(self.git_raw("config", key, cwd=self.work).returncode, 1)
        self.queue_one()
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.assertEqual(self.on_origin("log", "-1", "--format=%an <%ae>", "main"), "t <t@t>")

    def test_with_user_name_set_and_no_user_email_an_entry_by_any_author_lands(self):
        self.init(mode="push")
        sh("git", "config", "user.name", "Owner", cwd=self.work)
        self.queue_one()
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_a_foreign_author_with_a_co_authored_by_trailer_and_a_based_on_line_lands(self):
        _, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee",
                                      "-m", "Co-authored-by: Ann Lee <ann@example.com>")
        self.assertEqual(printed, "landed E1 (r/D1)")
        self.assertEqual(self.on_origin("log", "-1", "--format=%an <%ae>", "main"), "Ann Lee <ann@example.com>")

    def test_a_based_on_line_with_a_final_period_counts(self):
        _, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee.",
                                      "-m", "Co-authored-by: Ann Lee <ann@example.com>")
        self.assertEqual(printed, "landed E1 (r/D1)")

    def test_a_based_on_line_inside_a_longer_line_does_not_count(self):
        sha, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee, with fixes",
                                        "-m", "Co-authored-by: Ann Lee <ann@example.com>")
        self.assertEqual(printed, self.foreign_bounce(sha, "Ann Lee <ann@example.com>"))

    def test_a_co_authored_by_trailer_without_a_based_on_line_bounces(self):
        sha, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Co-authored-by: Ann Lee <ann@example.com>")
        self.assertEqual(printed, self.foreign_bounce(sha, "Ann Lee <ann@example.com>"))
        self.assertEqual(self.origin_log(), ["init"])

    def test_a_based_on_line_without_a_co_authored_by_trailer_bounces(self):
        sha, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee")
        self.assertEqual(printed, self.foreign_bounce(sha, "Ann Lee <ann@example.com>"))

    def test_a_co_authored_by_trailer_folded_over_two_lines_bounces(self):
        sha, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee",
                                        "-m", "Co-authored-by: Ann Lee\n <ann@example.com>")
        self.assertEqual(printed, self.foreign_bounce(sha, "Ann Lee <ann@example.com>"))
        self.assertEqual(self.origin_log(), ["init"])

    def test_a_co_authored_by_trailer_that_names_someone_else_bounces(self):
        sha, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee",
                                        "-m", "Co-authored-by: Bo Chen <bo@example.com>")
        self.assertEqual(printed, self.foreign_bounce(sha, "Ann Lee <ann@example.com>"))

    def test_a_based_on_line_in_the_submitted_body_counts(self):
        _, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Co-authored-by: Ann Lee <ann@example.com>",
                                      body="Ports the parser.\n\nBased on #12 by @ann-lee\n")
        self.assertEqual(printed, "landed E1 (r/D1)")

    def test_a_submitted_body_without_the_based_on_line_bounces_though_the_commit_has_it(self):
        sha, printed = self.land_one_by("Ann Lee <ann@example.com>", "-m", "port", "-m", "Based on #12 by @ann-lee",
                                        "-m", "Co-authored-by: Ann Lee <ann@example.com>", body="Ports the parser.\n")
        self.assertEqual(printed, self.foreign_bounce(sha, "Ann Lee <ann@example.com>"))

    def test_an_entry_whose_second_commit_has_a_foreign_author_bounces_and_names_that_commit(self):
        self.init(mode="push")
        self.configure_owner()
        self.worker("w1", {"a.txt": "one\n"})
        path = self.base / "w1"
        sh("git", "commit", "-q", "--amend", "--author", "Owner <owner@example.com>", "-m", "first", cwd=path)
        (path / "a.txt").write_text("two\n")
        second = self.commit("second", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", second, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), self.foreign_bounce(second))
        self.assertEqual(self.origin_log(), ["init"])

    def test_an_entry_left_awaiting_merge_without_a_pr_bounces_on_the_next_run_when_its_author_is_foreign(self):
        with self.fake_gh():
            self.require_pr_head_on_origin()
            self.init(mode="human")
            self.queue_one()
            sha = sh("git", "rev-parse", "w1", cwd=self.work)
            (self.base / "pr-create-fails").write_text("")
            self.assertEqual(self.land("land"),
                             "queue paused: gh pr create failed: gh: not authenticated. Fix it, then run land.py resume")
            self.assertEqual(tuple(self.entry_fields(1, "state", "pr")), ("awaiting-merge", ""))
            (self.base / "pr-create-fails").unlink()
            self.configure_owner()
            self.assertEqual(self.land("resume"), "queue resumed")
            self.assertEqual(self.land("land"), self.foreign_bounce(sha))
        self.assertEqual(self.created_heads(), [])
        self.assertFalse((self.base / "prs.json").exists())

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

    def test_the_first_land_with_an_identity_configured_lands_the_entry_and_stores_the_pair(self):
        self.init(mode="push")
        self.configure_owner()
        self.queue_one_as("Owner", "owner@example.com")
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.assertEqual(self.stored_identity(), '["Owner", "owner@example.com"]')

    def test_push_mode_pauses_on_a_changed_name_and_leaves_trunk_and_the_entry(self):
        self.assert_push_mode_pauses(*self.IDENTITY_CHANGES["name"])

    def test_human_mode_pauses_on_a_changed_name_and_calls_no_gh_and_pushes_no_queue_branch_and_leaves_the_entry(self):
        self.assert_human_mode_pauses(*self.IDENTITY_CHANGES["name"])

    def test_push_mode_pauses_on_a_changed_email_and_leaves_trunk_and_the_entry(self):
        self.assert_push_mode_pauses(*self.IDENTITY_CHANGES["email"])

    def test_human_mode_pauses_on_a_changed_email_and_calls_no_gh_and_pushes_no_queue_branch_and_leaves_the_entry(self):
        self.assert_human_mode_pauses(*self.IDENTITY_CHANGES["email"])

    def test_push_mode_pauses_on_a_name_that_differs_only_in_surrounding_spaces(self):
        self.assert_push_mode_pauses(*self.IDENTITY_CHANGES["name_spaces"])

    def test_human_mode_pauses_on_a_name_that_differs_only_in_surrounding_spaces(self):
        self.assert_human_mode_pauses(*self.IDENTITY_CHANGES["name_spaces"])

    def test_push_mode_pauses_on_an_email_that_differs_only_in_surrounding_spaces(self):
        self.assert_push_mode_pauses(*self.IDENTITY_CHANGES["email_spaces"])

    def test_human_mode_pauses_on_an_email_that_differs_only_in_surrounding_spaces(self):
        self.assert_human_mode_pauses(*self.IDENTITY_CHANGES["email_spaces"])

    def test_push_mode_pauses_on_a_name_that_differs_only_in_letter_case(self):
        self.assert_push_mode_pauses(*self.IDENTITY_CHANGES["name_case"])

    def test_human_mode_pauses_on_a_name_that_differs_only_in_letter_case(self):
        self.assert_human_mode_pauses(*self.IDENTITY_CHANGES["name_case"])

    def test_push_mode_pauses_on_an_email_that_differs_only_in_letter_case(self):
        self.assert_push_mode_pauses(*self.IDENTITY_CHANGES["email_case"])

    def test_human_mode_pauses_on_an_email_that_differs_only_in_letter_case(self):
        self.assert_human_mode_pauses(*self.IDENTITY_CHANGES["email_case"])

    def test_push_mode_pauses_on_another_name_and_email_that_print_as_the_same_text(self):
        self.assert_push_mode_pauses(*self.IDENTITY_CHANGES["same_joined_text"])

    def test_human_mode_pauses_on_another_name_and_email_that_print_as_the_same_text(self):
        self.assert_human_mode_pauses(*self.IDENTITY_CHANGES["same_joined_text"])

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

    def test_a_second_land_while_paused_for_the_identity_prints_the_first_reason_after_another_change_and_changes_no_row_and_no_origin_ref(self):
        reason = self.land_one_then_pause_as_audit()
        self.configure_owner("third", "third@example.com")
        rows, refs = self.queue_rows(), self.on_origin("for-each-ref")
        self.assertEqual(self.land("land"), f"queue paused: {reason}")
        self.assertEqual(self.queue_rows(), rows)
        self.assertEqual(self.on_origin("for-each-ref"), refs)

    def test_status_shows_the_identity_pause_after_the_counts(self):
        reason = self.land_one_then_pause_as_audit()
        self.assertEqual(self.land("status"), f"push mode onto refs/remotes/origin/main. landed: 1, queued: 1, leases held: 1. Paused: {reason}")

    def test_resume_after_an_identity_pause_lets_land_run_and_store_the_new_pair_and_a_change_back_pauses_against_the_new_pair(self):
        self.land_one_then_pause_as_audit()
        self.assertEqual(self.land("resume"), "queue resumed")
        self.assertIsNone(self.stored_identity())
        self.assertEqual(self.land("land"), "landed E2 (r/D2)")
        self.assertEqual(self.stored_identity(), '["audit", "audit@example.com"]')
        self.assertEqual(self.on_origin("log", "-1", "--format=%an <%ae> %s", "main"), "audit <audit@example.com> w2")
        self.configure_owner()
        self.assertEqual(self.land("land"),
                         "queue paused: " + self.identity_pause("Owner <owner@example.com>", "audit <audit@example.com>"))

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

    def test_an_unset_user_name_pauses_and_changes_no_row_but_the_paused_row_and_one_new_log_row_and_no_origin_ref(self):
        self.assert_an_unset_identity_pauses(("--unset", "user.name"))
        self.assertEqual((self.configured_value("user.name"), self.configured_value("user.email")), (None, "owner@example.com"))

    def test_an_unset_user_email_pauses_and_after_resume_land_lands_and_stores_no_identity(self):
        self.assert_an_unset_identity_pauses(("--unset", "user.email"))
        self.assertEqual((self.configured_value("user.name"), self.configured_value("user.email")), ("Owner", None))
        self.assert_resume_then_land_lands_and_stores_no_identity()

    def test_an_unset_user_name_and_user_email_pause_and_after_resume_land_lands_and_stores_no_identity(self):
        self.assert_an_unset_identity_pauses(("--unset", "user.name"), ("--unset", "user.email"))
        self.assertEqual((self.configured_value("user.name"), self.configured_value("user.email")), (None, None))
        self.assert_resume_then_land_lands_and_stores_no_identity()

    def test_an_empty_user_email_pauses_and_after_resume_land_lands_and_stores_no_identity(self):
        self.assert_an_unset_identity_pauses(("user.email", ""))
        self.assertEqual((self.configured_value("user.name"), self.configured_value("user.email")), ("Owner", ""))
        self.assert_resume_then_land_lands_and_stores_no_identity()

    def test_resume_from_a_rewind_pause_keeps_the_stored_identity_and_the_next_land_pauses_on_the_changed_one(self):
        self.init(mode="push")
        self.configure_owner()
        start = sh("git", "rev-parse", "origin/main", cwd=self.work)
        self.queue_one_as("Owner", "owner@example.com")
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        landed = self.on_origin("rev-parse", "main")
        sh("git", "push", "-q", "--force", "origin", f"{start}:main", cwd=self.work)
        self.queue_one_as("Owner", "owner@example.com", number=2)
        self.assertEqual(self.land("land"), f"still queued: E2\nqueue paused: trunk no longer contains the last landed commit {landed[:12]} "
                                            f"(now {start[:12]}); check what happened to trunk, then run land.py resume")
        self.configure_owner("audit", "audit@example.com")
        self.assertEqual(self.land("resume"), "queue resumed")
        self.assertEqual(self.stored_identity(), '["Owner", "owner@example.com"]')
        self.assertEqual(self.land("land"), "queue paused: " + self.identity_pause("audit <audit@example.com>"))
        self.assertEqual(self.stored_identity(), '["Owner", "owner@example.com"]')

    def test_resume_on_a_queue_that_is_not_paused_keeps_the_stored_identity_and_land_pauses_on_a_changed_one(self):
        self.init(mode="push")
        self.configure_owner()
        self.assertEqual(self.land("land"), "nothing to land")
        self.assertEqual(self.land("resume"), "queue resumed")
        self.assertEqual(self.stored_identity(), '["Owner", "owner@example.com"]')
        self.configure_owner("audit", "audit@example.com")
        self.assertEqual(self.land("land"), "queue paused: " + self.identity_pause("audit <audit@example.com>"))

    def test_the_identity_pause_prints_a_newline_and_a_backslash_in_either_name_as_python_escapes(self):
        self.init(mode="push")
        self.configure_owner("A\nB\\C", "owner@example.com")
        self.assertEqual(self.land("land"), "nothing to land")
        self.configure_owner("X\\Y\nZ", "owner@example.com")
        self.assertEqual(self.configured_value("user.name"), "X\\Y\nZ")
        self.assertEqual(self.land("land"),
                         "queue paused: " + self.identity_pause(r"X\\Y\nZ <owner@example.com>", r"A\nB\\C <owner@example.com>"))

    def test_a_store_with_no_identity_key_runs_each_listed_command_with_status_0_and_land_stores_the_pair_and_lands(self):
        self.init(mode="push")
        self.queue_one()
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        with self.raw_db() as db:
            keys = [key for key, in db.execute("SELECT key FROM contract ORDER BY key")]
        self.assertEqual(keys, ["batch", "checks", "commonDir", "mergeMethod", "mode", "paused", "remote", "repo", "setup", "timeout",
                                "tip", "trunk"])
        self.configure_owner()
        self.worker("w2", {"b.txt": "agent\n"})
        sh("git", "commit", "-q", "--amend", "--author", "Owner <owner@example.com>", "-m", "w2", cwd=self.base / "w2")
        sha = sh("git", "rev-parse", "HEAD", cwd=self.base / "w2")
        commands = (
            ["init", "--trunk", "main", "--mode", "push", "--check", "./check.sh"],
            ["cap", "5"],
            ["mode", "push"],
            ["owner", "--prefix", "docs/", "--generation", "1"],
            ["lease", "claim", "--holder", "r/D2", "--paths", "b.txt"],
            ["lease", "renew", "L2"],
            ["lease", "claim", "--holder", "r/D3", "--paths", "lib"],
            ["lease", "release", "L3"],
            ["lease", "list"],
            ["lease", "check", "--holder", "r/D4", "--paths", "c.txt"],
            ["lease", "reserve", "--for", "docs/", "--paths", "docs", "--ruling", "R1", *ADMIN],
            ["lease", "unreserve", "S1", *ADMIN],
            ["submit", "--holder", "r/D2", "--branch", "w2", "--sha", sha, "--lease", "L2", "--reviewer", REVIEWER],
            ["share", "--for", "docs/", "1", *ADMIN],
            ["contest", "--holders", "x/D1,y/D1", *ADMIN],
            ["contest", "--cancel", "C1", *ADMIN],
            ["land"],
            ["resume"],
            ["status"],
            ["status", "E2"],
            ["status", "--holder", "r/"],
        )
        printed = {}
        for command in commands:
            with self.subTest(command=command[:2]):
                status, printed[command[0]], error = self.run_land(self.work, *command)
                self.assertEqual((status, error), (0, ""))
        self.assertEqual(printed["land"], "landed E2 (r/D2)\n")
        self.assertEqual(self.stored_identity(), '["Owner", "owner@example.com"]')

    def stray_worktree_line(self, value, quoted=None, shown=None):
        config = (self.work / ".git").resolve() / "config"
        return (f"land: {shown or config} sets core.worktree to {value}, where git does not find this repository; "
                f"remove it with git --work-tree=. config --file {quoted or config} --unset-all core.worktree\n")

    def run_land(self, repo, *args, cwd=None):
        """Run land.py with --repo set to repo. Returns its exit status, stdout, and stderr."""
        return drive(SCRIPT, ["--repo", str(repo), *args], env=os.environ.copy(), cwd=cwd)

    def test_a_core_worktree_that_names_a_missing_directory_stops_status_land_resume_lease_init_and_submit(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "agent\n"})
        gone = self.base / "gone"
        sh("git", "config", "core.worktree", str(gone), cwd=self.work)
        config = self.work / ".git" / "config"
        before = config.read_bytes()
        commands = (["status"], ["land"], ["resume"], ["lease", "list"],
                    ["init", "--trunk", "main", "--mode", "auto", "--check", "./check.sh"],
                    ["submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER])
        for repo in (self.work, self.base / "w1"):
            for command in commands:
                with self.subTest(repo=repo.name, command=command[0]):
                    self.assertEqual(self.run_land(repo, *command), (1, "", self.stray_worktree_line(gone)))
        self.assertEqual(config.read_bytes(), before)

    def test_a_core_worktree_that_names_a_directory_outside_the_repository_stops_status(self):
        self.init()
        sh("git", "config", "core.worktree", str(self.base), cwd=self.work)
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(self.base)))

    def test_the_printed_command_quotes_a_config_path_that_has_a_space(self):
        self.work = self.base / "spaced work"
        sh("git", "clone", "-q", "origin.git", "spaced work", cwd=self.base)
        gone = self.base / "gone"
        sh("git", "config", "core.worktree", str(gone), cwd=self.work)
        quoted = "'" + str((self.work / ".git").resolve() / "config") + "'"
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(gone, quoted)))

    def test_a_core_worktree_that_names_the_checkout_does_not_stop_status(self):
        self.init()
        for value in (str(self.work), ".."):
            with self.subTest(value=value):
                sh("git", "config", "core.worktree", value, cwd=self.work)
                self.assertEqual(self.run_land(self.work, "status"), (0, "push mode onto refs/remotes/origin/main. empty.\n", ""))

    def test_a_relative_core_worktree_that_names_a_missing_directory_stops_status(self):
        self.init()
        sh("git", "config", "core.worktree", "../gone", cwd=self.work)
        for repo in (self.work, self.base):
            with self.subTest(cwd=repo.name):
                self.assertEqual(self.run_land(self.work, "status", cwd=repo), (1, "", self.stray_worktree_line("../gone")))

    def test_the_printed_command_run_in_the_checkout_removes_a_relative_core_worktree(self):
        self.init()
        sh("git", "config", "core.worktree", "../gone", cwd=self.work)
        command = self.run_land(self.work, "status")[2].rstrip("\n").split("remove it with ", 1)[1]
        subprocess.run(command, shell=True, cwd=self.work, check=True, env=os.environ.copy())
        self.assertEqual(self.run_land(self.work, "status"), (0, "push mode onto refs/remotes/origin/main. empty.\n", ""))

    def test_the_last_core_worktree_value_decides(self):
        self.init()
        gone = self.base / "gone"
        sh("git", "config", "--add", "core.worktree", str(gone), cwd=self.work)
        sh("git", "config", "--file", str(self.work / ".git" / "config"), "--add", "core.worktree", str(self.work), cwd=self.base)
        self.assertEqual(self.run_land(self.work, "status"), (0, "push mode onto refs/remotes/origin/main. empty.\n", ""))
        sh("git", "config", "--file", str(self.work / ".git" / "config"), "--add", "core.worktree", str(gone), cwd=self.base)
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(gone)))

    def files_under(self, directory):
        """The SHA-256 of every file under a directory, by relative path."""
        return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(directory.rglob("*")) if path.is_file()}

    def test_a_core_worktree_that_is_the_checkout_path_and_a_newline_stops_status_and_lease_claim(self):
        self.init()
        sh("git", "config", "core.worktree", str(self.work) + "\n", cwd=self.work)
        before = self.files_under(self.work / ".git"), self.files_under(self.base / "state")
        for command in (["status"], ["lease", "claim", "--holder", "r/D1", "--paths", "a.txt"]):
            with self.subTest(command=command[0]):
                self.assertEqual(self.run_land(self.work, *command), (1, "", self.stray_worktree_line(str(self.work) + "\\n")))
        self.assertEqual((self.files_under(self.work / ".git"), self.files_under(self.base / "state")), before)
        with self.raw_db() as db:
            self.assertEqual(db.execute("SELECT id FROM lease").fetchall(), [])

    def test_the_printed_command_removes_a_core_worktree_that_is_the_checkout_path_and_a_newline(self):
        self.init()
        sh("git", "config", "core.worktree", str(self.work) + "\n", cwd=self.work)
        command = self.run_land(self.work, "status")[2].rstrip("\n").split("remove it with ", 1)[1]
        subprocess.run(command, shell=True, cwd=self.work, check=True, env=os.environ.copy())
        self.assertEqual(self.run_land(self.work, "status"), (0, "push mode onto refs/remotes/origin/main. empty.\n", ""))

    def test_a_core_worktree_with_a_carriage_return_is_not_judged_as_the_directory_with_a_newline_there(self):
        self.init()
        (self.work / "a\nb").mkdir()
        sh("git", "config", "core.worktree", str(self.work / "a\rb"), cwd=self.work)
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(str(self.work / "a") + "\\rb")))

    def test_the_core_worktree_line_is_one_line_when_the_value_holds_line_breaks_and_a_backslash(self):
        self.init()
        gone = str(self.base / "gone")
        sh("git", "config", "core.worktree", gone + "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029\\", cwd=self.work)
        status, printed, complaint = self.run_land(self.work, "status")
        self.assertEqual((status, printed), (1, ""))
        self.assertEqual(complaint, self.stray_worktree_line(gone + r"\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029\\"))
        self.assertEqual(len(complaint.splitlines()), 1)

    def test_a_core_worktree_of_300_letters_stops_status(self):
        self.init()
        sh("git", "config", "core.worktree", "x" * 300, cwd=self.work)
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line("x" * 300)))

    def test_the_printed_command_is_one_line_and_runs_when_the_config_path_holds_a_newline_and_a_backslash(self):
        self.work = self.base / "new\nline\\work"
        sh("git", "clone", "-q", "origin.git", str(self.work), cwd=self.base)
        gone = self.base / "gone"
        sh("git", "config", "core.worktree", str(gone), cwd=self.work)
        top = str(self.base.resolve())
        shown = top + r"/new\nline\\work/.git/config"
        quoted = "\"$(printf %b '" + top + r"/new\0012line\0134work/.git/config" + "')\""
        status, printed, complaint = self.run_land(self.work, "status")
        self.assertEqual((status, printed, complaint), (1, "", self.stray_worktree_line(gone, quoted, shown)))
        self.assertEqual(len(complaint.splitlines()), 1)
        subprocess.run(complaint.rstrip("\n").split("remove it with ", 1)[1], shell=True, cwd=self.work, check=True, env=os.environ.copy())
        self.assertEqual(self.git_raw("config", "--get-all", "core.worktree", cwd=self.work).returncode, 1)

    def test_a_git_directory_whose_name_ends_in_a_space_is_read_under_that_name(self):
        self.work = self.base / "apart"
        store = self.base / "store "
        sh("git", "clone", "-q", "--separate-git-dir", str(store), "origin.git", "apart", cwd=self.base)
        gone = self.base / "gone"
        sh("git", "config", "core.worktree", str(gone), cwd=self.work)
        config = str(store.resolve() / "config")
        self.assertEqual(self.run_land(self.work, "status"), (1, "", self.stray_worktree_line(gone, "'" + config + "'", config)))

    def test_slot_runs_with_a_core_worktree_that_names_a_missing_directory(self):
        self.init()
        sh("git", "config", "core.worktree", str(self.base / "gone"), cwd=self.work)
        env = {key: value for key, value in os.environ.items() if key != "LAND_SLOT"}
        result = real_process(
            ProcessReason.GOVERNOR, SCRIPT, ["slot", "--", "sh", "-c", "echo ran"],
            env=env, cwd=self.work, timeout=30)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "ran\n", ""))


def repository_state(repo):
    """Config bytes, HEAD, and every ref of a repository."""
    refs = subprocess.run(["git", "for-each-ref", "--format=%(refname) %(objectname)"], cwd=repo,
                          check=True, capture_output=True, text=True).stdout
    return (repo / ".git/config").read_bytes(), (repo / ".git/HEAD").read_text(), refs


class GitEnvironmentFenceTest(unittest.TestCase):
    def test_a_fixture_test_leaves_a_repository_named_by_the_callers_git_variables_unchanged(self):
        for names in (("GIT_DIR", "GIT_WORK_TREE"), ("GIT_DIR",)):
            with self.subTest(leaked=names), tempfile.TemporaryDirectory() as tmp:
                victim = Path(tmp) / "victim"
                subprocess.run(["git", "init", "-q", "-b", "main", str(victim)], check=True)
                subprocess.run(["git", "-c", "user.name=v", "-c", "user.email=v@v", "commit", "-q", "--allow-empty", "-m", "init"],
                               cwd=victim, check=True)
                before = repository_state(victim)
                leaked = {"GIT_DIR": str(victim / ".git"), "GIT_WORK_TREE": str(victim)}
                child = _run(
                    [sys.executable, str(Path(__file__).resolve()), "LandingTest.test_a_conflict_rerere_resolved_to_trunk_bounces"],
                    env={**os.environ, **{name: leaked[name] for name in names}},
                    timeout=300, reason=ProcessReason.INHERITED_ENV)
                self.assertEqual(repository_state(victim), before)
                self.assertEqual(child.returncode, 0, child.stderr)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--paused-child"]:
        paused_child(sys.argv[2], sys.argv[3:])
    unittest.main()
