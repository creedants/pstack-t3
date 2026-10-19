import enum
import gc
import io
import locale
import os
import runpy
import sqlite3
import subprocess
import sys
import traceback
from .descriptors import _close_new_fds, _descriptors, _restore, _save


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
