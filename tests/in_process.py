"""Run a program file as __main__ in this process, or start a real interpreter on that same file."""

import atexit
import enum
import io
import locale
import os
import pkgutil
import runpy
import signal
import subprocess
import sys
import time
import traceback
import warnings
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["Reason", "Invocation", "invoke", "finished", "spawn"]

_MISSING = object()
_depth = 0
_interpreter_filters = list(warnings.filters)


class Reason(enum.Enum):
    RACE = "a race"
    SIGNAL = "a signal"
    INTERPRETER_EXIT = "exit status of the interpreter itself"
    INHERITED_ENVIRONMENT = "inherited environment"
    HELP = "help output"


@dataclass(frozen=True)
class Invocation:
    """Everything one start needs. invoke copies env. The program is a filesystem path, executed as __main__."""

    program: str | os.PathLike
    argv: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None
    cwd: str | os.PathLike | None = None
    stdin: str | None = None
    text: bool = True
    clock: float | None = None


@dataclass(frozen=True)
class _State:
    cwd: str
    environ: dict[str, str]
    environ_object: dict
    argv: list
    argv_contents: list
    stdin: object
    stdout: object
    stderr: object
    path: list
    path_contents: list
    modules: dict
    dont_write_bytecode: bool
    time_time: object
    umask: int
    signals: tuple
    atexit_count: int
    atexit_register: object
    namespaces: tuple
    platform: str
    filters: list
    filters_contents: list


def _pipe_encoding():
    getencoding = getattr(locale, "getencoding", None)
    if getencoding is None:
        return locale.getpreferredencoding(False)
    return getencoding()


def _text_from_pipe(data, encoding):
    return data.decode(encoding, "strict").replace("\r\n", "\n").replace("\r", "\n")


def _stream_encoding(environ):
    encoding = _pipe_encoding()
    errors = "strict"
    raw = environ.get("PYTHONIOENCODING")
    if not raw:
        return encoding, errors
    left, separator, right = raw.partition(":")
    if left:
        encoding = left
    if separator and right:
        errors = right
    return encoding, errors


def _capture(encoding, errors, line_buffering):
    raw = io.BytesIO()
    wrapper = io.TextIOWrapper(
        raw,
        encoding=encoding,
        errors=errors,
        newline=None,
        line_buffering=line_buffering,
    )
    return raw, wrapper


def _stdin(text, encoding, errors):
    """The interpreter reads stdin with newline set to \\n, so a carriage return stays a character."""
    raw = io.BytesIO(text.encode(encoding, errors))
    raw.seek(0)
    return io.TextIOWrapper(raw, encoding=encoding, errors=errors, newline="\n")


def _namespaces():
    saved = []
    seen = set()
    for module in tuple(sys.modules.values()):
        if module is None or id(module) in seen:
            continue
        namespace = getattr(module, "__dict__", None)
        if not isinstance(namespace, dict):
            continue
        seen.add(id(module))
        saved.append((module, dict(namespace)))
    return tuple(saved)


def _save():
    umask = os.umask(0)
    os.umask(umask)
    handlers = []
    for signum in range(1, signal.NSIG):
        try:
            handlers.append((signum, signal.getsignal(signum)))
        except (OSError, ValueError):
            continue
    return _State(
        cwd=os.getcwd(),
        environ=dict(os.environ),
        environ_object=os.environ,
        argv=sys.argv,
        argv_contents=list(sys.argv),
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
        path=sys.path,
        path_contents=list(sys.path),
        modules=dict(sys.modules),
        dont_write_bytecode=sys.dont_write_bytecode,
        time_time=time.time,
        umask=umask,
        signals=tuple(handlers),
        atexit_count=atexit._ncallbacks(),
        atexit_register=atexit.register,
        namespaces=_namespaces(),
        platform=sys.platform,
        filters=warnings.filters,
        filters_contents=list(warnings.filters),
    )


def _restore_namespace(module, saved):
    if module is None or saved is None:
        return
    current = module.__dict__
    for key in [key for key in current if key not in saved]:
        del current[key]
    for key, value in saved.items():
        if current.get(key, _MISSING) is not value:
            current[key] = value


def _restore(saved):
    first = None

    def step(action):
        nonlocal first
        try:
            action()
        except Exception as error:
            if first is None:
                first = error

    for module, namespace in saved.namespaces:
        step(lambda module=module, namespace=namespace: _restore_namespace(module, namespace))
    step(lambda: os.chdir(saved.cwd))

    def environ():
        saved.environ_object.clear()
        saved.environ_object.update(saved.environ)

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

    def path():
        if sys.path is not saved.path:
            sys.path = saved.path
        saved.path[:] = saved.path_contents

    step(path)

    def modules():
        current = sys.modules
        for key in [key for key in current if key not in saved.modules]:
            del current[key]
        for key, value in saved.modules.items():
            if current.get(key, _MISSING) is not value:
                current[key] = value

    step(modules)

    def filters():
        if warnings.filters is not saved.filters:
            warnings.filters = saved.filters
        saved.filters[:] = saved.filters_contents
        warnings._filters_mutated()

    step(filters)
    step(lambda: setattr(sys, "dont_write_bytecode", saved.dont_write_bytecode))
    step(lambda: setattr(time, "time", saved.time_time))
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
    step(lambda: setattr(atexit, "register", saved.atexit_register))
    step(lambda: setattr(sys, "platform", saved.platform))
    if first is not None:
        raise first


class _TypedArgv(list):
    """sys.argv for one run. A real interpreter keeps argv[0] as the program was typed, so a plain item assignment to index 0 is ignored."""

    def __setitem__(self, index, value):
        if index != 0:
            super().__setitem__(index, value)


def _command(invocation):
    program = os.fspath(invocation.program)
    args = [os.fspath(part) for part in invocation.argv]
    if invocation.clock is None:
        return [sys.executable, program, *args]
    from tests.test_activity import FROZEN
    return [sys.executable, "-c", FROZEN, repr(invocation.clock), program, *args]


def _child_env(invocation):
    if invocation.env is None:
        return None
    return dict(invocation.env)


def _cwd(invocation):
    if invocation.cwd is None:
        return None
    return os.fspath(invocation.cwd)


def _child_input(invocation):
    if invocation.stdin is None:
        return None
    if invocation.text:
        return invocation.stdin
    return invocation.stdin.encode(_pipe_encoding(), "strict")


def _require(reason):
    if not isinstance(reason, Reason):
        raise TypeError("reason is a Reason member")


def _run_real(invocation, timeout):
    return subprocess.run(
        _command(invocation),
        input=_child_input(invocation),
        capture_output=True,
        text=invocation.text,
        cwd=_cwd(invocation),
        env=_child_env(invocation),
        timeout=timeout,
    )


def _code_from_exit(status):
    if status is None:
        return 0
    if isinstance(status, int):
        return status
    print(status, file=sys.stderr)
    return 1


def _bytecode_flag(environ):
    return bool(environ.get("PYTHONDONTWRITEBYTECODE"))


def _drain_atexit(saved, recorded):
    # CPython atexit.unregister leaves the slot and does not shrink _ncallbacks().
    try:
        delta = atexit._ncallbacks() - saved.atexit_count
        if delta != len(recorded):
            raise RuntimeError("atexit._ncallbacks() cannot separate handlers registered during the call")
        for func, args, kwargs in list(reversed(recorded)):
            try:
                func(*args, **kwargs)
            except (SystemExit, Exception):
                traceback.print_exc()
    finally:
        for func, _args, _kwargs in recorded:
            atexit.unregister(func)


def _execute(saved, invocation):
    typed = invocation.program
    if invocation.cwd is not None:
        os.chdir(invocation.cwd)
    if invocation.env is not None:
        os.environ.clear()
        os.environ.update(dict(invocation.env))
    # A real interpreter resolves its script argument against the directory it starts in and
    # runs that path, so the traceback and __file__ name that path.
    program = os.path.join(os.getcwd(), os.fspath(typed))
    sys.argv = _TypedArgv([os.fspath(typed), *[os.fspath(part) for part in invocation.argv]])
    if pkgutil.get_importer(program) is None:
        # runpy.run_path runs a plain script with sys.path untouched, while a real interpreter
        # puts the script's own directory first, with symlinks resolved. A directory or zipfile
        # is a valid sys.path entry, and run_path inserts it itself.
        sys.path.insert(0, os.path.dirname(os.path.realpath(program)))
    sys.dont_write_bytecode = _bytecode_flag(os.environ)
    if invocation.clock is not None:
        frozen = float(repr(invocation.clock))
        time.time = lambda: frozen
    encoding, errors = _stream_encoding(os.environ)
    out_raw, out = _capture(encoding, errors, False)
    err_raw, err = _capture(encoding, errors, True)
    sys.stdout = out
    sys.stderr = err
    if invocation.stdin is not None:
        sys.stdin = _stdin(invocation.stdin, encoding, errors)
    recorded = []
    original = saved.atexit_register

    def recording(func, *args, **kwargs):
        original(func, *args, **kwargs)
        recorded.append((func, args, kwargs))
        return func

    atexit.register = recording
    warnings.filters[:] = list(_interpreter_filters)
    warnings._filters_mutated()
    try:
        try:
            runpy.run_path(program, run_name="__main__")
            code = 0
        except SystemExit as exit_error:
            code = _code_from_exit(exit_error.code)
        except Exception:
            kind, value, tb = sys.exc_info()
            helper = os.path.realpath(__file__)
            runpy_file = os.path.realpath(runpy.__file__)
            while tb is not None:
                filename = tb.tb_frame.f_code.co_filename
                real = os.path.realpath(filename)
                if real != helper and real != runpy_file and filename != "<frozen runpy>":
                    break
                tb = tb.tb_next
            traceback.print_exception(kind, value, tb)
            code = 1
        _drain_atexit(saved, recorded)
        out.flush()
        err.flush()
        return code, out_raw.getvalue(), err_raw.getvalue()
    finally:
        for func, _args, _kwargs in recorded:
            atexit.unregister(func)


def _run_here(invocation):
    parent_encoding = _pipe_encoding()
    saved = _save()
    try:
        code, stdout, stderr = _execute(saved, invocation)
    finally:
        _restore(saved)
    if invocation.text:
        stdout = _text_from_pipe(stdout, parent_encoding)
        stderr = _text_from_pipe(stderr, parent_encoding)
    return subprocess.CompletedProcess(_command(invocation), code, stdout, stderr)


def invoke(invocation):
    """Execute invocation.program as __main__.

    Reads PSTACK_TEST_REAL_PROCESS from the process environment before applying invocation.env.
    The value 1 starts a real interpreter on that same file. Any other value runs the file here.
    A re-entrant call during that in-process run raises RuntimeError.
    """
    global _depth
    if _depth:
        raise RuntimeError("invoke cannot re-enter")
    if os.environ.get("PSTACK_TEST_REAL_PROCESS") == "1":
        return _run_real(invocation, timeout=None)
    _depth += 1
    try:
        return _run_here(invocation)
    finally:
        _depth -= 1


def finished(reason, invocation, *, timeout=None):
    """Start a real interpreter on invocation.program and wait. reason is required and is not read."""
    _require(reason)
    return _run_real(invocation, timeout)


def spawn(reason, invocation):
    """Return the live interpreter. The caller waits. Same reason rule as finished."""
    _require(reason)
    proc = subprocess.Popen(
        _command(invocation),
        stdin=None if invocation.stdin is None else subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=invocation.text,
        cwd=_cwd(invocation),
        env=_child_env(invocation),
    )
    if invocation.stdin is not None:
        proc.stdin.write(_child_input(invocation))
        proc.stdin.close()
    return proc
