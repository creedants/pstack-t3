"""call reads os.environ.get("PSTACK_TEST_REAL_PROCESS") at the start of every call, before it installs a command env and before it loads. The value 1 sends that call through a real process. Any other value runs in this process. The read uses the process environment at the start of call. A command env mapping does not change that read.
call_in_process runs the script in this process and does not read that variable.

After an in-process call, including when main raises, the helper restores the process. When a restore step raises, the helper still runs the later steps, then raises the first restore error.

The helper saves the cwd path and changes back to it with os.chdir.
The helper saves a copy of os.environ before the call. It clears os.environ and updates that same object from the copy.
The helper saves the sys.argv object and a copy of its contents. When the call replaced sys.argv, the helper assigns the saved object back. The helper then sets that object's contents back to the copy.
The helper assigns the saved sys.stdin, sys.stdout, and sys.stderr objects back.
The helper saves the umask by calling os.umask and restoring it at once, before main. It restores that saved mask after the call.
For each signal from 1 to signal.NSIG - 1 that signal.getsignal can read, the helper puts the saved handler back when the handler after the call differs. It skips a signal that getsignal or signal refuses.
The helper assigns the saved sys.dont_write_bytecode back.
Standard-library imports stay in sys.modules and direct writes to file descriptors 1 and 2 are not captured.
"""
import enum
import io
import locale
import os
import runpy
import signal
import subprocess
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path


class ProcessReason(enum.Enum):
    """Why this call is a separate interpreter."""

    RACE = "race between two processes"
    SIGNAL = "signal"
    EXIT_STATUS = "process exit status"
    INHERITED_ENV = "inherited environment"
    HELP = "-h output"
    OTHER_LOCK = "lock held by another process"
    STOPPED = "child stopped while the test runs in-process"
    PRE_MAIN = "patch installed before main"


def _pipe_encoding():
    getencoding = getattr(locale, "getencoding", None)
    if getencoding is None:
        return locale.getpreferredencoding(False)
    return getencoding()


def _text_from_pipe(data):
    """Decode the way subprocess text mode decodes a pipe."""
    return data.decode(_pipe_encoding(), "strict").replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class Outcome:
    """The exit code and the captured stdout and stderr bytes."""

    code: int
    stdout: bytes
    stderr: bytes

    def stdout_text(self):
        """Universal newlines, the locale encoding, and strict errors."""
        return _text_from_pipe(self.stdout)

    def stderr_text(self):
        """Universal newlines, the locale encoding, and strict errors."""
        return _text_from_pipe(self.stderr)

    def completed(self, args):
        """A text CompletedProcess whose stdout and stderr are the decoded strings."""
        return subprocess.CompletedProcess(list(args), self.code, self.stdout_text(), self.stderr_text())


@dataclass(frozen=True)
class _ProcessState:
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


def _save_process():
    umask = os.umask(0)
    os.umask(umask)
    handlers = []
    for signum in range(1, signal.NSIG):
        try:
            handlers.append((signum, signal.getsignal(signum)))
        except (OSError, ValueError):
            continue
    return _ProcessState(
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
    )


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
    if first is not None:
        raise first


def _drop_entry(test):
    """Forget this test's loads. Drop brigade_lib and _brigade_ns_ names from sys.modules."""
    test.__dict__.pop("_brigade_entry", None)
    for name in list(sys.modules):
        if name == "brigade_lib" or name.startswith("brigade_lib.") or name.startswith("_brigade_ns_"):
            del sys.modules[name]


def _reset_load(loaded):
    """Reset READ_ONLY and clear HELD_LOCKS when they occur in run's globals. Clear roles_family's cache when cache_clear is available. Other globals and caches remain until this test's load is discarded."""
    run = loaded.get("run")
    if run is None:
        return
    glob = run.__globals__
    if "READ_ONLY" in glob:
        glob["READ_ONLY"] = False
    held = glob.get("HELD_LOCKS")
    clear = getattr(held, "clear", None)
    if clear is not None:
        clear()
    roles = glob.get("roles_family")
    cache_clear = getattr(roles, "cache_clear", None)
    if cache_clear is not None:
        cache_clear()


def _load(test, script):
    """Load this path on the first call for this test. Later calls in the test reuse it."""
    script = Path(script)
    if "_brigade_entry" not in test.__dict__:
        test.__dict__["_brigade_entry"] = {}
        test.addCleanup(_drop_entry, test)
    entry = test.__dict__["_brigade_entry"]
    loaded = entry.get(script)
    if loaded is None:
        loaded = runpy.run_path(str(script))
        entry[script] = loaded
    _reset_load(loaded)
    return loaded


def run_globals(test, script):
    """Load this script for this test on the first call and return the run globals of its module."""
    return _load(test, script)["run"].__globals__


def _capture_stream(line_buffering):
    raw = io.BytesIO()
    wrapper = io.TextIOWrapper(
        raw,
        encoding=_pipe_encoding(),
        errors="strict",
        newline=None,
        line_buffering=line_buffering,
    )
    return raw, wrapper


def _stdin_reader(text):
    """The interpreter reads stdin with newline set to \\n, so a carriage return stays a character."""
    raw = io.BytesIO(text.encode(_pipe_encoding(), "strict"))
    raw.seek(0)
    return io.TextIOWrapper(raw, encoding=_pipe_encoding(), errors="strict", newline="\n")


def _code_from_main(action):
    try:
        result = action()
    except SystemExit as exit_error:
        status = exit_error.code
        if status is None:
            return 0
        if isinstance(status, int):
            return status
        print(status, file=sys.stderr)
        return 1
    except Exception as error:
        traceback.print_exception(error)
        return 1
    if result is None:
        return 0
    return result


def _run_interpreter(command, *, stdin, env, cwd, timeout):
    encoded = None if stdin is None else stdin.encode(_pipe_encoding(), "strict")
    result = subprocess.run(
        list(command),
        input=encoded,
        capture_output=True,
        cwd=None if cwd is None else os.fspath(cwd),
        env=None if env is None else dict(env),
        timeout=timeout,
    )
    return Outcome(result.returncode, result.stdout, result.stderr)


def _call_in_process(test, script, argv, *, stdin, env, cwd):
    saved = _save_process()
    arguments = [str(arg) for arg in argv]
    try:
        # The interpreter line-buffers stderr and block-buffers a pipe's stdout.
        out_raw, out = _capture_stream(False)
        err_raw, err = _capture_stream(True)
        sys.stdout = out
        sys.stderr = err
        if cwd is not None:
            os.chdir(cwd)
        if env is not None:
            replacement = dict(env)
            os.environ.clear()
            os.environ.update(replacement)
        sys.argv = [str(script), *arguments]
        if stdin is not None:
            sys.stdin = _stdin_reader(stdin)
        code = _code_from_main(lambda: _load(test, script)["main"](list(arguments)))
        out.flush()
        err.flush()
        return Outcome(code, out_raw.getvalue(), err_raw.getvalue())
    finally:
        _restore(saved)


def call(test, script, argv, *, stdin=None, env=None, cwd=None):
    """Run the script for argv.

    argv is the arguments after the script name. stdin None leaves the call's standard input in place.
    A string is that call's stdin only, and isatty on the replacement is false.
    env None uses the live os.environ. A mapping replaces os.environ for that call only.
    cwd None keeps the working directory. A path is the working directory for that call only.
    An in-process call sets sys.argv to the script path plus argv and passes argv to main without the script name.
    """
    if os.environ.get("PSTACK_TEST_REAL_PROCESS") == "1":
        command = [sys.executable, str(script), *(str(arg) for arg in argv)]
        return _run_interpreter(command, stdin=stdin, env=env, cwd=cwd, timeout=None)
    return _call_in_process(test, script, argv, stdin=stdin, env=env, cwd=cwd)


def call_in_process(test, script, argv, *, stdin=None, env=None, cwd=None):
    """Run the script for argv in this test's process whatever PSTACK_TEST_REAL_PROCESS says.

    A test that installs a fake roles.py script, or patches the loaded module, in this process needs the
    command to read those globals, which a separate interpreter does not share. The other arguments follow call.
    """
    return _call_in_process(test, script, argv, stdin=stdin, env=env, cwd=cwd)


def real_process(reason, script, argv, *, stdin=None, env=None, cwd=None, timeout=None):
    """Start a real interpreter and return a text CompletedProcess. timeout raises subprocess.TimeoutExpired."""
    command = [sys.executable, str(script), *(str(arg) for arg in argv)]
    return _run_interpreter(command, stdin=stdin, env=env, cwd=cwd, timeout=timeout).completed(command)


def real_popen(reason, argv, *, stdin=None, env=None, cwd=None, text=True):
    """Return a Popen with stdout and stderr piped. argv includes the interpreter. The caller calls communicate."""
    return subprocess.Popen(
        list(argv),
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=None if cwd is None else os.fspath(cwd),
        env=None if env is None else dict(env),
        text=text,
    )

