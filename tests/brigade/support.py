"""call reads os.environ.get("PSTACK_TEST_REAL_PROCESS") at the start of every call, before it installs a command env and before it loads. The value 1 sends that call through a real process. Any other value runs in this process. The read uses the process environment at the start of call. A command env mapping does not change that read.

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
import fcntl
import io
import json
import locale
import os
import runpy
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "t3/added/brigade/scripts/brigade.py"
CODEX = "codex/gpt-6.1-sol"
CLAUDE = "claudeAgent/claude-opus-5-5"
# Thread ids the threads.tsv tests use. No real thread id appears in this repository.
THREAD_A = "mcp:00000000-0000-4000-8000-000000000001"
THREAD_B = "mcp:00000000-0000-4000-8000-000000000002"
THREAD_C = "mcp:00000000-0000-4000-8000-000000000003"
THREAD_D = "mcp:00000000-0000-4000-8000-000000000004"
HELD_QUESTION = "D1: grok could not pass the seat's options: refused. Move this coordinator to a provider that passes them?"
HELD_LINE = (
    "D1: open decision Q1: D1: grok could not pass the seat's options: refused. "
    "Move this coordinator to a provider that passes them?; "
    "launch no worker or verifier until 86 answer Q1"
)
STILL_OPEN = "Q1 still open; D1 stays held until 86 answer Q1 --answer moved"
ITEM_DECISION = (
    "brigade: an item decision's default must be one of its options, "
    "with at least one other option that closes it"
)


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


def _race_args(mode, case, args):
    return ["--race-child", mode, str(case), *(str(arg) for arg in args)]


def _wait_for_path(path, timeout=8):
    end = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > end:
            raise TimeoutError(path)
        time.sleep(0.005)

def _shown_root(path):
    """The project root walk prints, with the home directory as ~."""
    path = Path(path).resolve()
    try:
        relative = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    if not relative.parts:
        return "~"
    return "~/" + relative.as_posix()

def _owner_words(directory):
    """The --owner a coordinator reads from status, as the skill's service step 1 does."""
    try:
        meta = json.loads((Path(directory) / "restaurant.json").read_text())
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(meta, dict) or meta.get("generation") is None:
        return ()
    return ("--owner", f"{meta.get('thread') or ''}@{meta['generation']}")

def _race_child(mode, case, args):
    case = Path(case)
    if mode == "land-pause":
        land = runpy.run_path(str(ROOT / "t3/added/landing/scripts/land.py"))
        store = land["run"].__globals__["Store"]
        original_tx = store.tx

        def paused_tx(self):
            (case / "paused").touch()
            _wait_for_path(case / "proceed", timeout=60)
            return original_tx(self)

        store.tx = paused_tx
        sys.exit(land["main"](args))
    mod = runpy.run_path(str(SCRIPT))
    # run_path returns a copy. The live functions read the original globals.
    glob = mod["run"].__globals__
    if mode == "owner":
        original = glob["write_atomic"]

        def hooked(path, content):
            original(path, content)
            if Path(path).name == "restaurant.json":
                (case / "metadata-ready").touch()
                _wait_for_path(case / "owner-proceed")

        glob["write_atomic"] = hooked
    elif mode == "loser":
        original_wait = glob["wait_for_meta"]

        def hooked_wait(directory):
            meta = original_wait(directory)
            (case / "loser-saw-meta").touch()
            _wait_for_path(case / "loser-after-meta")
            return meta

        glob["wait_for_meta"] = hooked_wait
        original_write = glob["write_atomic"]

        def hooked_write(path, content):
            if Path(path).name in ("menu.md", "house-rules.md"):
                (case / "loser-at-scaffold").touch()
                _wait_for_path(case / "loser-scaffold-proceed")
            return original_write(path, content)

        glob["write_atomic"] = hooked_write
        original_save = glob["Restaurant"].save_rows

        def hooked_save(self, table, rows):
            if table == "rail.tsv" and rows == []:
                (case / "loser-at-scaffold").touch()
                _wait_for_path(case / "loser-scaffold-proceed")
            return original_save(self, table, rows)

        glob["Restaurant"].save_rows = hooked_save
    elif mode == "append":
        directory, label = args
        restaurant = glob["Restaurant"](directory)
        _wait_for_path(case / "go")
        for number in range(200):
            restaurant.append("log.tsv", {"at": glob["now"](), "kind": "ticket", "id": f"{label}{number}",
                                          "state": "waiting", "note": f"{label} row {number}"})
        sys.exit(0)
    elif mode == "die-before-publish":
        original = glob["write_atomic"]

        def hooked(path, content):
            if Path(path).parent.name == "inbox":
                os._exit(9)
            return original(path, content)

        glob["write_atomic"] = hooked
    elif mode == "fake-land":
        glob["LAND"] = case / "land.py"
    elif mode == "pause":
        original_locked = glob["Restaurant"].locked

        def paused_locked(self):
            if not getattr(self, "paused_once", False):
                self.paused_once = True
                (case / "paused").touch()
                _wait_for_path(case / "proceed", timeout=60)
            return original_locked(self)

        glob["Restaurant"].locked = paused_locked
    elif mode == "pause-after-finished":
        original_finished = glob["finished_by_coordinator"]

        def paused_finished(admin):
            finished = original_finished(admin)
            (case / "paused").touch()
            _wait_for_path(case / "proceed", timeout=60)
            return finished

        glob["finished_by_coordinator"] = paused_finished
    elif mode == "die-before-log":
        glob["Restaurant"].log = lambda self, *rest: os._exit(9)
    elif mode == "die-before-item":
        original_append = glob["Restaurant"].append

        def dying_append(self, table, row):
            if table == "dishes.tsv":
                os._exit(9)
            return original_append(self, table, row)

        glob["Restaurant"].append = dying_append
    elif mode == "die-before-cursor":
        original_change = glob["Restaurant"].change_meta

        def dying_change(self, **fields):
            if "cursors" in fields:
                os._exit(9)
            return original_change(self, **fields)

        glob["Restaurant"].change_meta = dying_change
    elif mode == "die-before-thread":
        original_change = glob["Restaurant"].change_meta

        def dying_thread(self, drop=(), **fields):
            if "thread" in fields:
                os._exit(9)
            return original_change(self, drop, **fields)

        glob["Restaurant"].change_meta = dying_thread
    elif mode == "read16":
        reads = []

        def short_read(fd):
            data = os.read(fd, 16)
            if not reads:
                (case / "paused").touch()
                _wait_for_path(case / "proceed", timeout=60)
            reads.append(data)
            return data

        glob["read_chunk"] = short_read
    elif mode == "hold":
        original_append = glob["Restaurant"].append

        def holding_append(self, table, row):
            original_append(self, table, row)
            if table == "log.tsv" and not getattr(self, "held_once", False):
                self.held_once = True
                (case / "holding").touch()
                _wait_for_path(case / "proceed", timeout=60)

        glob["Restaurant"].append = holding_append
    elif mode == "brief-pause":
        original_roles = glob["roles_mode"]

        def paused_roles(inputs):
            lines = original_roles(inputs)
            if not (case / "paused").exists():
                (case / "paused").touch()
                _wait_for_path(case / "proceed", timeout=60)
            return lines

        glob["roles_mode"] = paused_roles
    elif mode == "brief-churn":
        original_roles = glob["roles_mode"]

        def churning_roles(inputs):
            lines = original_roles(inputs)
            path = Path(args[args.index("--at") + 1]) / "restaurant.json"
            meta = json.loads(path.read_text())
            meta["mode"] = "full" if meta.get("mode") == "light" else "light"
            path.write_text(json.dumps(meta))
            return lines

        glob["roles_mode"] = churning_roles
    elif mode == "append-row":
        directory, *fields = args
        restaurant = glob["Restaurant"](directory)
        restaurant.append("log.tsv", dict(zip(("at", "kind", "id", "state", "note"), fields)))
        sys.exit(0)
    elif mode == "die-inside-log-append":
        real_write = os.write

        def partial(fd, data):
            if b"\tfrom " in data:
                real_write(fd, data[:data.index(b"\tfrom ") + len(b"\tfro")])
                os._exit(9)
            return real_write(fd, data)

        os.write = partial
    elif mode == "die-inside-goal-append":
        real_write = os.write

        def partial(fd, data):
            if data.startswith(b"G1\t"):
                real_write(fd, data[:data.index(b"\topen\t") + len(b"\tope")])
                os._exit(9)
            return real_write(fd, data)

        os.write = partial
    else:
        raise SystemExit(f"unknown mode {mode}")
    sys.exit(mod["main"](args))

BULLETS = """# D1 report

Status: done

## Follow-ups

- A no-ref `--source user` add still has no CLI dedupe. The new Refusals rule tells the coordinator to read `ticket list` before refiling. A `brigade.py` guard would make that structural.
- `docs/guide.md` still describes intake as `gh issue list` and `gh pr list` when the standing orders name them. That file is outside this lease.
- PR #87 was not commented on, approved, or closed.
"""
NUMBERED = """## Follow-ups

1. **Hillclimb.** `t3/overrides/poteto-mode/playbooks/hillclimb.md` has a code-delegate line with the same missing step 5 pointer. It was outside the lease.
2. **`WAIT_RULE` in `t3/added/brigade/scripts/brigade.py`.** It states only the ten-minute clock for worker briefs. Decide whether it should point at the new step 5 bullet.
3. No README or `docs/guide.md` edit is needed.

Scratch is under `~/.cache/d87`.
"""
NESTED = """## Follow-ups

1. **D90-dependent wording.** These sentences hold for main and change if D90 lands:
   - Prompt caps step 1: "It still applies the Haiku prompt cap."
   - Step 4: the rerun of a seat-launching child on `inherit`.

   If D90 makes the flag skip capped models, step 1 should say the flag never returns a capped model.
2. **Docs batch.**
  - Change 4's Files list in `docs/light-mode.md` should add `t3/scripts/roles.py`.
  - `docs/light-mode.md` is not leased.
"""
FENCED = r"""## Follow-ups

1. Run this command to set the user's panel. It must use a fresh catalog.

   ```bash
   # write the panel
   python3 roles.py write --catalog <file> --keep \
     --set "review backups=opencode/step-5-preview-free?variant=high"
   ```

2. `family()` keeps the provider path for namespaced ids such as `opencode/...`. The panel compares bare ids locally.
"""
PROSE = """## Follow-ups

Schema setup in `land.py` still runs outside `BEGIN IMMEDIATE`.

`integration.head()` is still read inside two transactions.
Both sites predate this change.

No test under `tests/` asserts these sentences. That directory is outside this lease.

## Principles

- Fix root causes changed the order of the two reads.
"""
LEAD_IN = """## Follow-ups

The explainer found these in `brigade.py`. All are outside L81, so I did not fix them.

- `pass record` without `--pr` overwrites the item's recorded PR with an empty value, because `--pr` defaults to `""`.
- `--same-family` is not checked. It writes the `same model family;` note even when the families differ.
"""
TWO_SECTIONS = """# D1 report

## Follow-ups

1. `docs/light-mode.md`, in its review-file table, still describes one `<item>-review.md` file per item.
2. The panel sentences in step 7 still say "handle the send-back below" while that bullet sits above them.

## Decision trail

Kept in the store.

## Round 2

### Deviations

- None.

### Follow-ups

1. Decide whether `ticket move` should refuse a ticket of an item that owes its decision.

## Round 3

- The rule as implemented.
"""
PROSE_ABOVE_A_LEAD_IN = """## Follow-ups

**`README.md`.** Replace the `**Known limits.**` bullet (line 284), which still says "Nothing deletes it or moves it back", with these two bullets:

```markdown
- **Interrupted runs are recovered.** Every later install or uninstall looks in those directories.
- **Known limits.** Nothing returns or deletes it, so delete it by hand.
```

**`docs/guide.md`.** Replace the first bullet under "Known limits." (line 359) with this bullet, and keep the two bullets after it:

```markdown
- An entry in a `.pstack-t3-scrap-*` directory is an unfinished copy. Delete it by hand.
```

Other follow-ups:

- **Type the `side` that `holder_parents` yields**, from the comment review.
- **No test covers a skills directory inside the checkout**, which the sweep skips because `layout` refuses it.

Scratch files for this run are under `/tmp/d105-repro/`: `repro.py`, `before.txt`, and `full-suite.txt`.
"""
A_LIST_THEN_A_PROSE_SECTION = """## Follow-ups

1. `docs/light-mode.md`, in its review-file table, still describes one `<item>-review.md` file per item.

## Round 2

### Follow-ups

Schema setup in `land.py` still runs outside `BEGIN IMMEDIATE`.

`integration.head()` is still read inside two transactions.
"""
NO_WORK = """## Follow-ups

1. **Docs.** `README.md` and `docs/guide.md` do not mention the `recovered`, `removed`, and `left` lines.
2. None.
3. **None required.**
4. NONE NEEDED
5. _Nothing._
6. `Nothing needed`.
7. n/a
8. Not applicable.
9. **No follow-ups**.
"""
LONGER_THAN_A_NO_WORK_PHRASE = [
    "**No test covers a skills directory inside the checkout**, which the sweep now skips because `layout` refuses it.",
    "Docs. `README.md` and `docs/guide.md` do not name owner `queue` reports, so they need no change. The batched docs "
    "change could add a note to `docs/live-runs.md` that owners now use `auto`.",
    "`scripts/install.py` HARNESSES has no Muse entry. No Muse skills directory was confirmed, so nothing was added.",
    "The README roadmap and `docs/guide.md` do not name the script. No doc edit is needed unless the guide's release "
    "paragraph is touched.",
    "**Configure `escalate`.** The design lists `scripts/install.py` for this repository. None is set, so the trial ran "
    "light.",
    "None of the tests cover the retry path.",
    "Nothing in this change explains the release of lease L2.",
    "No lock is taken by `status`, though one is required.",
    "Decide whether `pass record --report` should refuse a path outside `reports/`, so it needs no second check.",
    "Add a test that verifies no edit is needed.",
    "No retry test exists and one is needed.",
    "None in the suite covers the retry path.",
    "None for `README.md` or `docs/guide.md`. They do not name this printed line.",
    "None required. The `changes/pstack-t3%2Fd57.md` fragment from the previous attempt still describes the change and "
    "was left as is.",
    "Nothing needed here until the guide's release paragraph moves.",
    "Not applicable. This lease holds no docs.",
    "No follow-ups for the docs batch, and one for the installer.",
    "Docs: none. No README or guide names the changed sentences.",
    "No README or `docs/guide.md` edit is needed.",
    "**No README or guide edit needed.** Neither file names these tools.",
    "`docs/guide.md` has no sentence about syncing upstream, so it needs no edit.",
]

REAL_FOLLOW_UPS = [
    ('`docs/guide.md:246`. "`ticket add` refuses a source this coordinator does not own, and a ref that is still '
     'open here or in a sibling." leaves out the same-summary refusal. Add after "So one issue becomes one '
     'ticket.": "A request from you with no ref is refused while a waiting or assigned ticket of that coordinator '
     'has the same summary, unless `--again` is passed."',
     'docs/guide.md'),
    ('`tests/test_pstack_t3.py:1869-1878` `test_history_states_the_thread_link_in_a_form_check_accepts` was written '
     'when a literal thread link failed the link check. `check_tree` now accepts it. Lines 1877-1878 (the runtime '
     'holds no link target starting `t3-thread:`) no longer guard anything. Once they go, the runtime can show the '
     'literal `[title](t3-thread://v1/<threadId>)`. The test file is outside L136.',
     'tests/test_pstack_t3.py'),
    ('**`changes/pstack-t3%2Fd116.md`, third bullet, at release time.** Its sentence "Each line names a command to '
     'run, or the entry or file to delete." is now true of each single sentence and each group head, and not of an '
     'item line. Its last two sentences still hold. The release item corrects superseded bullets, so it can fold '
     'this one into the D124 bullets.',
     'changes/pstack-t3%2Fd116.md'),
    ('`t3/scripts/roles.py`, `_resolve_inherit`, the excluded-parent refusal. For a parent on Claude Haiku 4.5 or a '
     'fast Grok model whose provider the catalog marks not runnable, the error ends "`<provider>` has no other '
     'model pstack may pick", even when that provider lists a pickable model. The true cause is that the provider '
     'cannot run children. A parent the runtime mode blocks no longer reaches that text. The catalog-marked case '
     'still does. The fix is to end that error with "`<provider>` is not runnable (`<constraints>`)" when '
     '`runnable(provider)` is false, with a test on a fixture provider that has `canRunChildTask: false`, '
     '`claude-haiku-4-5`, and one other model.',
     't3/scripts/roles.py'),
    ('**Move `OLD_INSTALLER` and the test wrappers to `tests/fixtures/`.** This is outside lease L104. '
     '`tests/test_install.py` is 1,688 lines, 276 of them the embedded installer.',
     'tests/fixtures,tests/test_install.py'),
    ('**Own-thread wait coverage.** It is measured on Claude (with `runId`) and Codex (without `runId`). Grok, '
     'Cursor, and ACP are untested.',
     ''),
    ("**`README.md`, `doctor`.** This replaces the `**`doctor` lists leftover records.**` bullet D116's second "
     'follow-up proposed, which has not landed. Add it after the bullet that starts `**Nothing is overwritten.**`, '
     "beside D116's `**A skipped step exits 3.**` bullet, which stands: ```markdown - **`doctor` lists leftover "
     "records.** Under each harness, `doctor` prints this checkout's recorded links that it can prove neither at "
     'their path nor in a recorded backup, backup records with nothing at their backup path, and owner files of '
     'other checkouts whose checkout directory is missing. Each names a command to run, or the entry or file to '
     'delete. Two or more under one harness with the same advice print as one group, a line with their count and '
     'the advice and then one line per record. `doctor` writes nothing, and these lines do not change its exit '
     'status. It names a record file it cannot read and still runs the checks that do not need that file. It exits '
     "1 when that file is `install-manifest.json` or this checkout's owner file. ```",
     'README.md'),
    ('`t3/added/brigade/SKILL.md:81` quotes the old malformed line, and the file is leased to another item. Replace '
     '``with `brigade: <table> line <n> is malformed; fix or remove it`.`` with ``with `brigade: '
     '<project>/<restaurant>/<table> line <n> is malformed; fix or remove it`, where `<project>/<restaurant>` is '
     'the last two parts of the restaurant directory that holds the table.``',
     't3/added/brigade/SKILL.md'),
]
TRACKED = ("README.md", "changes/pstack-t3%2Fd116.md", "docs/guide.md", "t3/added/brigade/SKILL.md", "t3/runtime.md",
           "t3/scripts/roles.py", "tests/fixtures/installer.py", "tests/test_install.py", "tests/test_pstack_t3.py")

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

class BrigadeTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_drop_entry, self)
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Path(self.temporary.name) / "store"
        self.project = Path(self.temporary.name) / "Bridge Kit"
        self.project.mkdir()
        self.at = self.store / "bridge-kit" / "perf"
        self.isolate_roles_config()

    def isolate_roles_config(self):
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(Path(self.temporary.name) / "config")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temporary.cleanup()

    def brigade(self, *args, ok=True, stdin=None, owner=True):
        return self.run_at(self.at, *args, ok=ok, stdin=stdin, owner=owner)

    def run_at(self, directory, *args, ok=True, stdin=None, owner=True):
        if owner and "--owner" not in args:
            args = (*_owner_words(directory), *args)
        outcome = call(
            self, SCRIPT,
            ["--store", str(self.store), "--at", str(directory), *(str(arg) for arg in args)],
            stdin=stdin,
        )
        self.assertEqual(outcome.code == 0, ok, outcome.stdout_text() + outcome.stderr_text())
        return (outcome.stdout_text() if ok else outcome.stderr_text()).strip()

    def assert_refused_choice(self, output, flag, value, choices):
        refusal = f"argument {flag}: invalid choice: '{value}'"
        self.assertIn(refusal, output)
        after = output[output.index(refusal) + len(refusal):]
        for choice in choices:
            self.assertIn(choice, after)

    def table_row(self, directory, table, ident):
        lines = [line for line in (directory / table).read_text().splitlines() if line]
        keys = lines[0].split("\t")
        for line in lines[1:]:
            row = dict(zip(keys, line.split("\t")))
            if row["id"] == ident:
                return row
        self.fail(f"no {ident} in {directory / table}")

    def blocked_note(self, directory, ident):
        lines = [line for line in (directory / "log.tsv").read_text().splitlines() if line]
        keys = lines[0].split("\t")
        note = None
        for line in lines[1:]:
            row = dict(zip(keys, line.split("\t")))
            if row["kind"] == "ticket" and row["id"] == ident and row["state"] == "blocked":
                note = json.loads(row["note"])
        self.assertIsNotNone(note, ident)
        return note

    def bash_unblocked(self, directory, line):
        # The assertion is the shell round trip.
        command = line.split("run ", 1)[1]
        invocation = " ".join([shlex.quote(sys.executable), shlex.quote(str(SCRIPT)),
                               "--store", shlex.quote(str(self.store)), "--at", shlex.quote(str(directory)),
                               command])
        return subprocess.run(["bash", "-c", invocation], capture_output=True, text=True, env=self.land_env())

    def open(self):
        return self.brigade("open", "--project-root", str(self.project), "--name", "Perf")

    def same_text_refusal(self, ticket, state):
        return f"brigade: same text as {ticket} ({state}); nothing added; pass --again to file a second ticket"

    def check_json(self, sha):
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                       "pass", "check", "D1", "--sha", sha, "--json"])
        return outcome.code, outcome.stdout_text(), outcome.stderr_text().strip()

    def record(self, sha, verdict, *extra, author=CLAUDE, verifier=CODEX):
        self.brigade("pass", "record", "D1", "--sha", sha, "--verdict", verdict, "--author", author, "--verifier", verifier, *extra)

    def fired_bug_fix(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")

    BEDROCK = "amazon-bedrock/us.anthropic.claude-sonnet-5-5-v1:0"
    NAMESPACED_MUSE = "opencode/opencode/muse-lite-2-free"

    def same_family_refusal(self, author, verifier):
        return (f"brigade: verifier {verifier} is the same model family as author {author}; "
                "pick a verifier from another family, or pass --same-family when no other family is runnable")

    def assert_one_family(self, author, verifier):
        self.fired_bug_fix()
        record = ("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", author, "--verifier", verifier)
        self.assertEqual(self.brigade(*record, ok=False), self.same_family_refusal(author, verifier))
        self.assertEqual(self.pass_rows(), [])
        self.assertEqual(self.brigade(*record, "--same-family"), "D1 passed")
        self.assertEqual([row[5:8] for row in self.pass_rows()], [[author, verifier, "same model family;"]])

    def pass_rows(self):
        return [line.split("\t") for line in (self.at / "pass.tsv").read_text().splitlines()[1:]]

    def review_file(self, name, body="findings\n"):
        path = self.at / "reports" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(body)
        return path

    def dish_fields(self, *keys):
        row = self.table_row(self.at, "dishes.tsv", "D1")
        return tuple(row[key] for key in keys)

    def store_files(self):
        """Every file of the store but restaurant.lock, which is empty and is not store data."""
        return {str(path.relative_to(self.at)): path.read_bytes() for path in sorted(self.at.rglob("*"))
                if path.is_file() and path.name != "restaurant.lock"}

    def tables(self):
        return {path.name: path.read_bytes() for path in sorted(self.at.glob("*.tsv"))}

    def thread_rows(self):
        """Every row of threads.tsv, with each timestamp replaced by `stamp` and an empty cell left empty."""
        lines = [line for line in (self.at / "threads.tsv").read_text().splitlines() if line]
        keys = lines[0].split("\t")
        return [["stamp" if key in ("at", "start", "end") and value else value
                 for key, value in zip(keys, line.split("\t"))] for line in lines[1:]]

    def follow_ups_in(self, body, *extra, name="D1.md", ok=True, owner=True):
        self.review_file(name, body)
        return self.brigade("ticket", "add", "--from-report", f"reports/{name}", *extra, ok=ok, owner=owner)

    def close_run(self, *flags):
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                       *_owner_words(self.at), "close", *flags])
        return outcome.code, outcome.stdout_text().strip(), outcome.stderr_text().strip()

    def no_row(self, name, dish="D1"):
        return (f"brigade: warning: reports/{name} has no review row; record it with pass record {dish} --report {name}, "
                f"and --late when {dish} has moved past that round")

    def aged(self, name, year):
        path = self.review_file(name)
        stamp = datetime(year, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def no_claims(self, state, dish="D1"):
        return f"brigade: warning: reports/{dish}.md has no Claims check line; {dish} is {state}"

    def not_a_file(self, name, words, advice):
        return f"brigade: warning: reports/{name} {words} and no review row of D1 names it; {advice}"

    def a_link(self, name):
        return self.not_a_file(name, "is a symbolic link", "remove the link and leave its target as it is")

    def under_a_linked_reports(self, name):
        return self.not_a_file(name, "resolves outside this store's reports/",
                               "replace the symbolic link at reports/ with a directory and leave the link's target as it is")

    def linked(self, name, target):
        link = self.at / "reports" / name
        link.parent.mkdir(exist_ok=True)
        link.symlink_to(Path(self.temporary.name) / target)
        return link

    def fire_one(self, station="perf-issue", timebox="60"):
        (self.at / "menu.md").write_text("# Menu: Perf\n\n## Purpose\n\nMake startup fast.\n\n## Budget\n\nsmall\n")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Startup is slow")
        return self.brigade("fire", "--tickets", "T1", "--station", station, "--summary", "Cut startup", "--branch", "perf/d1",
                            "--thread", "thread-9", "--timebox", timebox)

    def literal_brief_fields(self):
        return {
            "goal": "Run `$B status` and `$B close` before $(touch sentinel).",
            "acceptance": ["Median cold start below 400 ms"],
            "contract": "A cold start never reads `$HOME/.cache`.",
            "verify": "npm run perf",
            "paths": "src/boot.ts",
            "lease": "L4",
            "base": "origin/main",
            "context": ["notes/startup.md"],
        }

    def brief_from_flags(self, fields):
        return self.brigade("brief", "D1", "--contract", fields["contract"], "--goal", fields["goal"], "--acceptance", fields["acceptance"][0],
                            "--verify", fields["verify"], "--paths", fields["paths"], "--lease", fields["lease"],
                            "--base", fields["base"], "--context", fields["context"][0])

    NEEDS_CONTRACT = ("brigade: brief needs a contract; pass --contract <text> or the contract key in --fields, "
                      "and write none when the item has no safety, privacy, or integrity property")
    FLAGS = ("--goal", "g", "--acceptance", "a", "--verify", "v", "--paths", "src/boot.ts", "--lease", "L4",
             "--base", "origin/main")

    def briefable(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")

    VERBATIM = "\n  A refused write changes no table.  \n\n  - `log.tsv` gains no row.\nVERIFY: is not a heading here. \n"

    def contract_fields(self, contract):
        fields = self.at / "brief-fields.json"
        fields.write_text(json.dumps({"goal": "g", "acceptance": ["a"], "verify": "v", "base": "origin/main",
                                      "paths": "src/boot.ts", "lease": "L4", "contract": contract}), encoding="utf-8")
        return ("--fields", str(fields))

    def raw_stdout(self, *args):
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                       *_owner_words(self.at), *(str(arg) for arg in args)])
        self.assertEqual(outcome.code, 0, outcome.stderr)
        return outcome.stdout

    def log_rows(self):
        lines = (self.at / "log.tsv").read_text().splitlines()
        header = lines[0].split("\t")
        return [dict(zip(header, line.split("\t"))) for line in lines[1:] if line]

    def renamed(self, hook=None):
        """Fire D1 on perf/d1, then run dish D1 --branch perf/d1-r2 in this process, calling hook at its lease list."""
        self.open()
        self.brigade("set", "--thread", "coordinator")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                     "--branch", "perf/d1", "--paths", "src")
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        original = glob["land_result"]

        def hooked(project_root, *args):
            if hook and args[:2] == ("lease", "list"):
                hook()
            return original(project_root, *args)

        glob["land_result"] = hooked
        argv = ["--store", str(self.store), "--at", str(self.at), "--owner", "coordinator@1",
                "dish", "D1", "--branch", "perf/d1-r2"]
        try:
            return glob["run"](argv)
        except glob["BrigadeError"] as error:
            return f"brigade: {error}"

    def lease_lines(self):
        return [line.split(" until ")[0] + ": " + line.rsplit(": ", 1)[1]
                for line in self.land("lease", "list").splitlines()]

    def _spawn_race(self, mode, case, args):
        """Start one side of the owner and loser pair."""
        if mode not in ("owner", "loser"):
            raise SystemExit(f"_spawn_race starts owner or loser, not {mode}")
        support = Path(__file__).resolve()
        return real_popen(
            ProcessReason.RACE,
            [sys.executable, str(support), *_race_args(mode, case, args)],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)

    def _finish_race(self, proc):
        try:
            out, err = proc.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            self.fail(f"timed out\nstdout:\n{out}\nstderr:\n{err}")
        return proc.returncode, out.strip(), err.strip()

    def git_project(self):
        if (self.project / ".git").exists():
            return
        run = lambda *command: subprocess.run(command, cwd=self.project, capture_output=True, text=True, check=True)
        run("git", "init", "-q", "-b", "main")
        (self.project / "a.txt").write_text("a\n")
        run("git", "add", "-A")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    def land_env(self):
        return dict(os.environ, XDG_STATE_HOME=str(Path(self.temporary.name) / "state"))

    def land_repo(self, repo, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / "t3/added/landing/scripts/land.py"),
                                 "--repo", str(repo), *args],
                                capture_output=True, text=True, env=self.land_env())
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return (result.stdout if ok else result.stderr).strip()

    def land(self, *args, ok=True):
        return self.land_repo(self.project, *args, ok=ok)

    def init_landing(self, *args):
        self.git_project()
        return self.land("init", "--trunk", "lane", "--mode", "local", "--base", "main", *args)

    def landing_env(self):
        from unittest import mock
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]})
        patcher.start()
        self.addCleanup(patcher.stop)

    def started(self, *init, paths="README.md"):
        """A local landing contract, the Perf coordinator, and D1 holding L1."""
        self.init_landing(*init)
        self.landing_env()
        self.open()
        (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", paths),
                         "D1 (lease L1 held by perf/D1)")

    def backdate_attempt(self, minutes, ident="D1"):
        start = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        log = self.at / "log.tsv"
        lines = log.read_text().splitlines()
        rewritten = [lines[0]]
        found = False
        for line in lines[1:]:
            fields = line.split("\t")
            if fields[1:4] == ["dish", ident, "in-progress"]:
                fields[0] = start
                line = "\t".join(fields)
                found = True
            rewritten.append(line)
        self.assertTrue(found, ident)
        log.write_text("\n".join(rewritten) + "\n")

    def park_item(self, question=HELD_QUESTION, dish="D1"):
        dish_args = ("--dish", dish) if dish else ()
        return self.brigade("86", "add", *dish_args, "--question", question,
                            "--options", "moved, keep parked", "--default", "keep parked")

    def store_bytes(self):
        return tuple((self.at / name).read_bytes() for name in ("86.tsv", "log.tsv", "restaurant.json"))

    def open_admin(self):
        return self.run_at(self.store / "bridge-kit" / ".admin", "open", "--admin", "--project-root", str(self.project))

    def admin(self, *args, ok=True):
        return self.run_at(self.store / "bridge-kit" / ".admin", *args, ok=ok)

    def landing_db(self):
        import sqlite3
        database = next((Path(self.temporary.name) / "state").glob("pstack-t3/landing/*/land.db"))
        db = sqlite3.connect(database)
        db.row_factory = sqlite3.Row
        return db

    def lease_row(self, number):
        from contextlib import closing
        with closing(self.landing_db()) as db:
            return dict(db.execute("SELECT * FROM lease WHERE id = ?", (number,)).fetchone())

    def seed_entry(self, sha, state, note):
        """A queue entry for perf/D1 at another commit whose first 12 characters match, copied from E1."""
        from contextlib import closing
        with closing(self.landing_db()) as db, db:
            db.execute("INSERT INTO entry (at, holder, branch, sha, base, fingerprint, lease, reviewer, state, note) "
                       "SELECT at, holder, branch, ?, base, fingerprint, lease, reviewer, ?, ? FROM entry WHERE id = 1",
                       (sha, state, note))

    @staticmethod
    def same_prefix(sha):
        return sha[:12] + ("1" if sha[12] == "0" else "0") + sha[13:]

    def worker_commit(self, branch, files):
        path = Path(self.temporary.name) / branch.replace("/", "-")
        git = lambda *command, cwd=self.project: subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                                                check=True).stdout.strip()
        if not path.exists():
            git("git", "worktree", "add", "-q", "-b", branch, str(path), "main")
        for name, text in files.items():
            if text is None:
                (path / name).unlink()
            else:
                (path / name).write_text(text)
        git("git", "add", "-A", cwd=path)
        git("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", branch, cwd=path)
        return git("git", "rev-parse", "HEAD", cwd=path)

    def passed(self, sha):
        self.brigade("dish", "D1", "--state", "in-review", "--sha", sha)
        self.brigade("pass", "record", "D1", "--sha", sha, "--verdict", "pass", "--author", CODEX, "--verifier", CLAUDE)

    def submit(self, sha):
        return self.land("submit", "--holder", "perf/D1", "--branch", "perf/d1", "--sha", sha, "--lease", "L1",
                         "--reviewer", CLAUDE)

    PENDING = "D1: 3 send-backs, decision pending"
    NO_FIX_ROUND = ("brigade: D1 has 3 send-backs; no fix round until its decision is answered; "
                    "run 86 add --dish D1 --round-budget")
    ROUND_QUESTION = ("D1 was sent back 3 times. Accept the remaining findings as known limits and land it, "
                      "send it through architect for a redesign, or drop it?")
    ROUND_BUDGET = ("86", "add", "--dish", "D1", "--round-budget")

    def send_back(self, sha, *extra):
        return self.brigade("pass", "record", "D1", "--sha", sha, "--verdict", "send-back",
                            "--author", CODEX, "--verifier", CLAUDE, *extra)

    def sent_back(self, rounds, start=1):
        for number in range(start, start + rounds):
            if number > start:
                self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
            printed = self.send_back(f"a{number}")
        return printed

    def round_decision_lifts_the_refusal(self, answer, stored):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", answer), "Q1 answered")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q1")["answer"], stored)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: sent back")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 0 send-backs since its last pass or round decision; the decision opens at 3")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")

    PASS = ("pass", "record", "D1", "--verdict", "pass", "--author", CODEX, "--verifier", CLAUDE, "--sha")
    NO_DECISION = "brigade: D1 is {}; it owes no round decision"

    def unchanged(self, before=None):
        now = tuple((self.at / name).read_bytes() for name in ("dishes.tsv", "rail.tsv", "log.tsv", "86.tsv"))
        if before is not None:
            self.assertEqual(now, before)
        return now

    FIRE_AGAIN = ("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fourth fix", "--thread", "fresh-worker")
    NO_FIRE = ("brigade: T1 is D1's ticket; D1 has 3 send-backs; no fix round until its decision is answered; "
               "run 86 add --dish D1 --round-budget")

    FIX_BRIEF = ("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "origin/main")

    def mode_rows(self):
        return [line.split("\t")[1:] for line in (self.at / "log.tsv").read_text().splitlines()[1:]
                if line.split("\t")[1] == "mode"]

    SEAT_RULE = ("Seat rule. Copy the Mode value above into --brief-mode on every roles.py mode and roles.py show call you "
                 "make, and pass no other mode flag. Never pass --session-mode. Mode source names where your launcher's "
                 "decision came from. It does not make this thread a session.")
    WAIT = ("- Never end your turn while a child task you started with delegate_task is still open, per step 5 of the "
            "pstack-runtime skill's Delegation section. This overrides delegate_task's text that says to end the turn and "
            "wait for a notification. A completion wakes you only when the child's run ends, and a stalled child or a run "
            "left open never ends. Wait on each open child task with t3_thread_wait on its childThreadId and timeoutMs "
            "300000, then read it with task_status. A child whose run is still open and whose last message holds the result "
            "is stalled after two minutes with no new activity, per step 5. If that wait returns at once while workState is "
            "still working or waiting_for_children, the child's run ended with its task open. Then wait between task_status "
            "checks with t3_thread_wait and timeoutMs 120000 on your own thread, the parentThreadId from "
            "orchestrator_capabilities, with runId set to its activeRunId from t3_thread_read, never a shell sleep, and "
            "count ten minutes from its last activity item. "
            "A child task is open while its workState is working or "
            "waiting_for_children, whatever hasPendingChildRuns says. Cancel a child task with task_cancel when it runs "
            "past its budget or stalls, per the runtime's Failure handling. A thread you launched with t3_thread_launch "
            "has no parent, so its finished turn never wakes you. While it stays healthy, repeat t3_thread_wait on its "
            "threadId with timeoutMs 300000 and read its activity with t3_thread_read. A timeout alone never stops it. "
            "Stop it with t3_thread_interrupt and then a terminal t3_thread_wait only when it stalls, with no new activity "
            "item for ten minutes per step 5 of the runtime's Delegation section, or runs past its budget. "
            "A long-lived owner your playbook supervises, such as an Orchestrate PR owner, follows the runtime's Top-level "
            "threads section and its playbook instead, and does not hold your report. Write the report only once every "
            "child task and every other thread you launched is terminal.")
    CONTESTED = ("- If you find the design contested, do not run interrogate. Stop at a verifiable point, commit, and write "
                 "Contested: <one-line reason> under the status line. The coordinator moves the work to full mode and gives "
                 "your report to a fresh worker.")
    REPEAT = ("- Under the status line, repeat this brief's Mode: line, and its Waived by mode: line when it has one. "
              "A step that line names is not a deviation.")
    BRIEF = ("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--paths", "src/a.py", "--lease", "L1",
             "--base", "origin/main")

    def coordinator(self, station="feature", mode="light", *fire):
        self.open()
        (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
        self.brigade("set", "--thread", "c1")
        if mode:
            self.brigade("set", "--mode", mode)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("fire", "--tickets", "T1", "--station", station, "--summary", "s", "--branch", "perf/d1",
                     "--thread", "w1", *fire)

    def head(self, *lines, station="feature"):
        return "\n".join([f"Use the poteto-mode skill and its `{station}` playbook.", *lines, "Gate: brigade", self.SEAT_RULE])

    def paused_brief(self, case):
        """The child waits in `roles_mode`. The patch is installed before `main`. The test does not overlap this child with another started process. The test acts while the child is stopped, and `call` cannot return that status."""
        case.mkdir()
        support = Path(__file__).resolve()
        tail = _race_args("brief-pause", case, ["--store", str(self.store), "--at", str(self.at),
                                                *_owner_words(self.at), *self.BRIEF])
        return real_popen(
            ProcessReason.STOPPED,
            [sys.executable, str(support), *tail],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)

    def cells(self, ident):
        row = self.table_row(self.at, "rail.tsv", ident)
        return row["priority"], row["paths"], row["decision"]

    def old_rail(self, stamp="2026-10-01T00:00:00+00:00"):
        (self.at / "rail.tsv").write_text(
            "id\tat\tstate\tsource\tref\tdish\tsummary\n"
            f"T1\t{stamp}\twaiting\tuser\t\t\tFix login\n"
            f"T2\t{stamp}\twaiting\treport\tperf/reports/D1.md#1\t\tTidy the guide\n"
            f"T3\t{stamp}\twaiting\tupstream\tabc123\t\tSync upstream\n")

    OLD_DISHES = "id\tat\tstate\tstation\ttickets\ttask\tthread\tbranch\tpr\tsha\tsummary\ttimebox\tlease\tpaths\treported\n"

    def old_item(self, stamp="2026-10-01T00:00:00+00:00"):
        """One assigned ticket and its item in review, each in a row of the width the tool wrote before the kind and goal cells."""
        (self.at / "rail.tsv").write_text(
            "id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision\n"
            f"T1\t{stamp}\tassigned\tuser\t\tD1\tFix login\t\t\t\n")
        (self.at / "dishes.tsv").write_text(
            self.OLD_DISHES + f"D1\t{stamp}\tin-review\tbug-fix\tT1\t\tthread-1\t\t\tabc\tFix login\t60\t\t\t\n")

    def tracked_project(self, *files):
        for name in files:
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x\n")
        run = lambda *command: subprocess.run(command, cwd=self.project, capture_output=True, text=True, check=True)
        run("git", "init", "-q", "-b", "main")
        run("git", "add", "-A")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    def paths_cells(self):
        return {row[0]: row[8] for row in (line.split("\t") for line in (self.at / "rail.tsv").read_text().splitlines()[1:])}

    def unrecorded_tickets(self):
        self.tracked_project(*TRACKED)
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `docs/guide.md:246` and `tests/test_install.py`")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Cover the `runId` wait")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Reword `README.md`", "--paths", "t3/runtime.md")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Drop `t3/scripts/roles.py`")
        self.brigade("ticket", "set", "T4", "--state", "dropped")

    NONE_RUNNING ="workers: 0 of 2 running, 2 idle, auto-start: normal"

    def queue(self, *open_flags):
        self.init_landing()
        self.landing_env()
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", *open_flags)

    def filed(self, summary, *flags):
        return self.brigade("ticket", "add", "--kind", "fix", "--summary", summary, *flags)

    def refile(self, ident, **ago):
        stamp = (datetime.now(timezone.utc) - timedelta(**ago)).isoformat()
        rail = self.at / "rail.tsv"
        lines = [line.split("\t") for line in rail.read_text().splitlines()]
        for fields in lines[1:]:
            if fields[0] == ident:
                fields[1] = stamp
        rail.write_text("".join("\t".join(fields) + "\n" for fields in lines))

    def landing_rows(self):
        from contextlib import closing
        with closing(self.landing_db()) as db:
            return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")]
                    for table in ("lease", "log", "reservation", "owner", "entry")}

    HELD_BACK = "held back, low tickets leave 1 worker idle while an urgent or normal ticket waits"

    @staticmethod
    def edit_block(number, path, old, new, fence="```"):
        return f"### Edit {number}\n\nFile: `{path}`\n\nOld text:\n\n{fence}\n{old}\n{fence}\n\nNew text:\n\n{fence}text\n{new}\n{fence}\n"

    def edits_report(self, *blocks, name="D1-review-1.md", after="## Edits verified\n\nApplied in a scratch copy.\n"):
        body = "Verdict: pass with edits\n\n## Blockers\n\nNone.\n\n## Edits\n\n" + "\n".join(blocks)
        return self.review_file(name, body + ("\n" + after if after else ""))

    def record_edits(self, sha, *extra, ok=True):
        return self.brigade("pass", "record", "D1", "--sha", sha, "--verdict", "pass-with-edits",
                            "--author", CODEX, "--verifier", CLAUDE, *extra, ok=ok)

    SOURCE = "helo wrold\nsecond line\n"
    FIXED = "hello world\nsecond line\n"
    BUILD = ('import pathlib, sys\ntext = pathlib.Path("src/greeting.txt").read_text()\nif "unbuildable" in text:\n'
             '    print("build refuses unbuildable")\n    sys.exit(2)\n'
             'pathlib.Path("out").mkdir(exist_ok=True)\npathlib.Path("out/greeting.txt").write_text(text.upper())\n')
    CHECK = ('import pathlib, sys\nif "forbidden" in pathlib.Path("src/greeting.txt").read_text():\n'
             '    print("check saw forbidden")\n    sys.exit(3)\n')
    BUILD_COMMAND = f"{shlex.quote(sys.executable)} tools/build.py"
    CHECK_COMMAND = f"{shlex.quote(sys.executable)} tools/check.py"
    NEEDS_COMMANDS = ("brigade: edits verify needs a build command and a check command; "
                      "run set --build-command <command> --check-command <command>, with none for one the project lacks")

    def commit_files(self, files):
        git = lambda *command: subprocess.run(("git", *command), cwd=self.project, capture_output=True, text=True,
                                              check=True).stdout.strip()
        for name, text in files.items():
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
        return git("rev-parse", "HEAD")

    def head_commit(self, source, out=None, **files):
        return self.commit_files({"src/greeting.txt": source, "out/greeting.txt": source.upper() if out is None else out,
                                  **files})

    def edits_store(self, build=BUILD_COMMAND, check=CHECK_COMMAND, **files):
        """D1 in a store whose project is a git repository with a source file, a build script, and a check script.
        Returns the reviewed commit."""
        self.git_project()
        reviewed = self.commit_files({"src/greeting.txt": self.SOURCE, "out/greeting.txt": self.SOURCE.upper(),
                                      "tools/build.py": self.BUILD, "tools/check.py": self.CHECK, **files})
        self.fired_bug_fix()
        if build is not None:
            self.brigade("set", "--build-command", build, "--check-command", check)
        return reviewed

    def two_edits(self, **kwargs):
        return self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"),
                                 self.edit_block("2", "src/greeting.txt", "wrold", "world"), **kwargs)

    def verify(self, reviewed, head):
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                       *_owner_words(self.at), "edits", "verify", "D1", "--from", reviewed, "--to", head])
        return outcome.code, outcome.stdout_text().strip(), outcome.stderr_text().strip()

    def verified(self, reviewed, head):
        return f"D1: edits verified, {reviewed} to {head}"

    def not_verified(self, reviewed, head):
        return f"brigade: D1: edits not verified, {reviewed} to {head}"

    def edits_rows(self):
        return [line.split("\t")[1:] for line in (self.at / "edits.tsv").read_text().splitlines()[1:]]

    def record_pass_at(self, sha, *extra, ok=True):
        return self.brigade("pass", "record", "D1", "--sha", sha, "--verdict", "pass", "--author", CODEX,
                            "--verifier", CLAUDE, *extra, ok=ok)

    def gate(self, reviewed, sha):
        return (f"brigade: D1's last verdict is pass-with-edits at {reviewed}; run edits verify D1 --from {reviewed} "
                f"--to {sha}, or name a second review's report with --report")

    QUOTED_APPLIED = "hello wrold\nsecond line\n"
    SECTION_APPLIED = "helo world\nsecond line\n"

    def quoting_report(self, opening, closing):
        """A report whose Notes section quotes an Edits section with one edit inside one fence, above an Edits section with another edit."""
        quoted = "## Edits\n" + self.edit_block("8", "src/greeting.txt", "helo", "hello")
        return self.review_file("D1-review-1.md", f"Verdict: pass with edits\n\n## Notes\n\n{opening}\n{quoted}{closing}\n\n## Edits\n"
                                + self.edit_block("1", "src/greeting.txt", "wrold", "world"))

    def assert_the_quoted_edit_is_not_read(self, opening, closing):
        reviewed = self.edits_store()
        self.quoting_report(opening, closing)
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.QUOTED_APPLIED)
        self.assertEqual(self.verify(reviewed, head),
                         (1, "FAIL out/greeting.txt: differs from what the build writes\n"
                             f"FAIL src/greeting.txt: differs from {reviewed} with the edits applied",
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))
        applied = self.head_commit(self.SECTION_APPLIED)
        self.assertEqual(self.verify(reviewed, applied),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, applied)}", ""))

    NESTED_FENCE = ("has three backticks or tildes after nothing but indentation, block quote marks, and list marks; "
                    "start each fence at the first column")
    PARTS_REFUSAL = ("brigade: reports/D1-review-1.md: Edit {} must hold, once each and in this order, a File line, "
                     "an Old text line, a fenced block, a New text line, and a fenced block")

    def section(self):
        return "## Edits\n\n" + self.edit_block("1", "src/greeting.txt", "wrold", "world")

    def refused_edits(self, body):
        """What pass record prints when it refuses a pass-with-edits round on a report with this body."""
        path = self.review_file("D1-review-1.md")
        path.write_bytes(body if isinstance(body, bytes) else body.encode())
        refusal = self.record_edits("abc", "--edits", "D1-review-1.md", ok=False)
        self.assertEqual(self.pass_rows(), [])
        return refusal

    BELOW = (" is a File, Old text, or New text line of Edit 1; the line above it must be empty or only spaces and tabs, "
             "the edit's heading, the closing line of a fence, or another of those three lines")
    APART = (" opens a fenced block of Edit 1; every line between an Old text or New text line and the fenced block below "
             "it must be empty or only spaces and tabs")
    MALFORMED = (": line 7 of Edit 1 starts, once its indentation, block quote marks, list marks, and the whitespace at its "
                 "two ends are removed, with File: and a backtick; what follows must be a path that holds no backtick and "
                 "does not start with a space, then a backtick that no backtick follows directly")
    # A line to put directly above a File, Old text, or New text line, and a line to put directly below it or None.
    AROUND = (("> example", None), ("- example", None), ('[r]: /url "example', 'end"'), ("Example ``", "``"),
              ("[r]: /url 'example", "end'"), ("[r]: /url (example", "end)"), ("> > example", None), ("- - example", None),
              ("> - example", None), ("+ example", None), ("* example", None), ("1. example", None), ("2) example", None),
              ("> example  ", None), ("> example\\", None), ("- example  ", None), ("example", None),
              ("Example <span", 'title="x">'), ("Example [link", "](/u)"), ("Example *stressed", "words*"), ("[r]: /url", None),
              ("\u00a0", None), ("\u0661. example", None))

    def one_edit_report(self):
        return "Verdict: pass with edits\n\n" + self.section()

    def run_raw(self, *args):
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                       *_owner_words(self.at), *(str(arg) for arg in args)])
        return outcome.code, outcome.stdout_text(), outcome.stderr_text()

    def assert_edits_verify_and_pass_record_refuse(self, cases):
        """Each case is (body, refusal), and the refusal finishes the words `brigade: reports/D1-review-1.md`.

        A pass-with-edits round is on record for one_edit_report, and the head is that edit and the rebuilt output.
        With each body as the report, edits verify for that round and pass record for a new round each print that one
        line and nothing else, exit 1, and change no store file. A pass at the head is refused after the last case.
        """
        reviewed = self.edits_store()
        report = self.review_file("D1-review-1.md", self.one_edit_report())
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.SECTION_APPLIED)
        self.brigade("dish", "D1", "--state", "in-progress")
        for body, refusal in cases:
            with self.subTest(body=body):
                report.write_bytes(body.encode())
                before, line = self.store_files(), f"brigade: reports/D1-review-1.md{refusal}\n"
                self.assertEqual(self.run_raw("edits", "verify", "D1", "--from", reviewed, "--to", head), (1, "", line))
                self.assertEqual(self.run_raw("pass", "record", "D1", "--sha", head, "--verdict", "pass-with-edits",
                                              "--author", CODEX, "--verifier", CLAUDE, "--edits", "D1-review-1.md"), (1, "", line))
                self.assertEqual(self.store_files(), before)
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))

    def goal(self, name="activity view", *extra, ok=True, owner=True):
        return self.brigade("goal", "add", "--name", name, "--purpose", "One page of activity", "--done", "The page ships", *extra,
                            ok=ok, owner=owner)

    def rows_of(self, table):
        return [line.split("\t") for line in (self.at / table).read_text().splitlines()[1:]]

    def goal_log(self):
        return [row[2:] for row in self.rows_of("log.tsv") if row[1] == "goal"]

    def hand_edit(self, table, ident, **cells):
        """Change cells of one row the way an editor would, with no command."""
        path = self.at / table
        header, *lines = path.read_text().splitlines()
        keys = header.split("\t")
        rows = [dict(zip(keys, line.split("\t"))) for line in lines]
        for row in rows:
            if row["id"] == ident:
                row.update(cells)
        path.write_text("\n".join([header] + ["\t".join(row.get(key, "") for key in keys) for row in rows]) + "\n")

    NO_UNASSIGNED_WORK = "unassigned: 0 waiting, 0 in flight, 0 merged, 0 send-backs"

    NEEDS_KIND = "brigade: ticket add needs --kind <feature|fix|polish|upkeep|study>"

    def label(self, table, ident):
        row = self.table_row(self.at, table, ident)
        return row["kind"], row["goal"]

    def raw_cells(self, table, ident, *keys):
        """These cells of the row with this id, as the bytes in the file. A carriage return stays, which splitlines would cut."""
        header, *lines = (self.at / table).read_bytes().split(b"\n")[:-1]
        names = header.decode().split("\t")
        found = [row for row in (dict(zip(names, line.split(b"\t"))) for line in lines) if row["id"] == ident.encode()]
        self.assertEqual(len(found), 1, f"{ident!r} in {table}")
        return tuple(found[0][key] for key in keys)

    def assert_closed_goal_takes_no_ticket(self, state):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("goal", "set", "G1", "--state", state, "--evidence", "why")
        before = self.store_files()
        refusal = (f"brigade: G1 is {state}; a ticket takes an open or paused goal; "
                   "run goal set G1 --state open first, or pick another goal")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G1", ok=False), refusal)
        self.assertEqual(self.brigade("ticket", "set", "T1", "--goal", "G1", "--priority", "low", ok=False), refusal)
        self.assertEqual(self.store_files(), before)

    KIND_CHOICE = "<feature|fix|polish|upkeep|study>"

    def rail_before_kinds(self, *summaries):
        """rail.tsv as the tool wrote it before kinds and goals. Each row is a waiting user ticket of ten cells."""
        (self.at / "rail.tsv").write_text(
            "id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision\n"
            + "".join(f"T{number}\t2026-10-01T00:00:00+00:00\twaiting\tuser\t\t\t{summary}\t\t\t\n"
                      for number, summary in enumerate(summaries, 1)))

    def two_goals(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "9")
        self.goal("activity view")
        self.goal("current with upstream")

    def fake_land(self):
        """A land.py that records its words, waits at a claim until claim-proceed exists, and prints lease L7."""
        case = Path(self.temporary.name)
        (case / "land.py").write_text(
            "import sys, time\nfrom pathlib import Path\n"
            f"case = Path({str(case)!r})\n"
            "with open(case / 'land-calls', 'a') as f:\n    f.write(' '.join(sys.argv[3:]) + '\\n')\n"
            "if sys.argv[4] == 'claim':\n"
            "    (case / 'claiming').touch()\n"
            "    end = time.monotonic() + 30\n"
            "    while not (case / 'claim-proceed').exists() and time.monotonic() < end:\n        time.sleep(0.01)\n"
            "    print('L7')\n"
            "else:\n    print('L7 released')\n")
        return case

    def fire_with_fake_land(self, case, *words):
        """The child replaces `LAND` before `main`. The test does not overlap it with another started process."""
        support = Path(__file__).resolve()
        tail = _race_args("fake-land", case, ["--store", str(self.store), "--at", str(self.at), "fire", *words])
        proc = real_popen(
            ProcessReason.PRE_MAIN,
            [sys.executable, str(support), *tail],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def assert_goal_keeps_open(self, ident, held, advice):
        before = self.store_files()
        for state in ("done", "dropped"):
            with self.subTest(goal=ident, state=state):
                self.assertEqual(self.brigade("goal", "set", ident, "--state", state, "--evidence", "why", ok=False),
                                 f"brigade: {ident} still has work: {held}; {advice}; then run this again")
        self.assertEqual(self.store_files(), before)

    def worked_store(self):
        """Two goals, ten tickets, five items, and nine review rows. A test that reads it names the rows behind each number."""
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "9")
        self.goal("activity view", "--share", "2")
        self.goal("current with upstream")
        for summary, kind, *flags in (
                ("Activity view", "feature", "--goal", "G1"),
                ("Lease check arms a reservation", "fix", "--goal", "G1"),
                ("Tidy the lease help", "polish", "--goal", "G2", "--priority", "low"),
                ("Activity page drops a row", "fix", "--goal", "G1"),
                ("Tidy the lease docs", "polish", "--priority", "low"),
                ("Runtime follows the nightly", "upkeep", "--goal", "G2", "--priority", "urgent"),
                ("Capacity after the parallel runner", "study", "--priority", "low"),
                ("Installer keeps a relative config home", "fix"),
                ("Activity page filters by coordinator", "feature", "--goal", "G1"),
                ("Docs batch", "polish", "--priority", "low")):
            self.brigade("ticket", "add", "--summary", summary, "--kind", kind, *flags)
        self.hand_edit("rail.tsv", "T8", kind="")
        for tickets, station, *flags in (("T1", "feature"), ("T2,T3", "bug-fix", "--ride-along", "T3"), ("T6", "refactoring"),
                                         ("T7", "investigation"), ("T10", "docs")):
            self.brigade("fire", "--tickets", tickets, "--station", station, "--summary", f"Do {tickets}", *flags)
        self.brigade("goal", "set", "G2", "--state", "paused")
        for item, state in (("D1", "merged"), ("D2", "in-review"), ("D3", "sent-back"), ("D5", "merged")):
            self.hand_edit("dishes.tsv", item, state=state)
        for ticket in ("T1", "T10"):
            self.hand_edit("rail.tsv", ticket, state="done")
        with open(self.at / "pass.tsv", "a") as passes:
            for item, verdict, member in (("D1", "send-back", ""), ("D1", "send-back", ""), ("D1", "pass", ""),
                                          ("D2", "send-back", ""), ("D2", "send-back", "yes"),
                                          ("D3", "send-back", ""), ("D3", "pass-with-edits", ""),
                                          ("D5", "send-back", ""), ("D5", "pass", "")):
                passes.write(f"2026-10-01T00:00:00+00:00\t{item}\t\tabc\t{verdict}\t{CLAUDE}\t{CODEX}\t\t\t{member}\n")

    def close_setup(self):
        self.two_goals()
        for summary, kind, *flags in (("Capacity", "study"), ("Lease check", "fix", "--goal", "G1"),
                                      ("Tidy the help", "polish", "--goal", "G2", "--priority", "low"), ("Runtime", "upkeep"),
                                      ("Splash hangs", "fix", "--goal", "G2"), ("Drops a row", "fix", "--goal", "G1"),
                                      ("Later", "polish", "--goal", "G2"), ("Tidy the docs", "polish", "--priority", "low"),
                                      ("Installer", "fix")):
            self.brigade("ticket", "add", "--summary", summary, "--kind", kind, *flags)
        self.hand_edit("rail.tsv", "T9", kind="")
        self.brigade("fire", "--tickets", "T1", "--station", "investigation", "--summary", "Measure capacity")
        self.brigade("fire", "--tickets", "T2,T3", "--ride-along", "T3", "--station", "bug-fix", "--summary", "Fix the lease check")
        self.brigade("fire", "--tickets", "T4", "--station", "refactoring", "--summary", "Follow the nightly")
        self.brigade("fire", "--tickets", "T5", "--station", "bug-fix", "--summary", "Fix the splash")

    def printed(self):
        """What each of the five commands that print kinds or goals gives, as {command: (exit code, stdout, stderr)}."""
        return {" ".join(words): self.run_raw(*words)
                for words in (("status",), ("goal", "list"), ("ticket", "list"), ("startable",), ("close", "--dry-run"))}

    def assert_each_printing_command_warns(self, *warnings):
        """Each of the five commands exits 0 with exactly these warnings on standard error, and changes no file.

        Returns {command: its standard output with leading and trailing whitespace cut}.
        """
        before = self.store_files()
        printed = self.printed()
        for command, (code, _, err) in printed.items():
            with self.subTest(command=command):
                self.assertEqual((code, err), (0, "".join(f"brigade: warning: {text}\n" for text in warnings)))
        self.assertEqual(self.store_files(), before)
        return {command: out.strip() for command, (_, out, _) in printed.items()}

    NOT_A_KIND = "which is not feature, fix, polish, upkeep, or study; it reads as no kind"
    NO_GOAL_ROW = "which has no row in goals.tsv; it reads as unassigned"
    NOT_WHILE = "ticket set does not change this cell while"

    def copy_goal_row(self, ident, name):
        """Append to goals.tsv a copy of its first row with this id and this name, the way an editor would."""
        path = self.at / "goals.tsv"
        cells = path.read_text().splitlines()[1].split("\t")
        cells[0], cells[3] = ident, name
        path.write_text(path.read_text() + "\t".join(cells) + "\n")

    PADDED_GOAL_ROW = ("goals.tsv has a row with id ' G1 ', which has white space at an end or a carriage return in it; "
                       "it is not a goal; correct the id in goals.tsv by hand to an id no other row has, or remove the row")

    def item_with_two_bad_cells(self):
        """D1 in flight with a kind cell and a goal cell that are not legal, and its ticket T1 with a bad kind cell. Returns D1's warnings."""
        self.fired_bug_fix()
        self.hand_edit("dishes.tsv", "D1", kind="chore", goal="G9")
        self.hand_edit("rail.tsv", "T1", kind="chore")
        by_hand = "no command changes this cell; correct it in dishes.tsv by hand"
        return (f"brigade: warning: D1 has kind 'chore', {self.NOT_A_KIND}; {by_hand}\n"
                f"brigade: warning: D1 names goal 'G9', {self.NO_GOAL_ROW}; {by_hand}\n")

    def store_from_before_kinds(self, rows=True):
        """A store as the tool wrote it before kinds and goals. Ticket rows of 10 cells, item rows of 15, and no goals.tsv.

        With `rows` it holds a ticket in each state but moved, an item in each state, review rows, and an open and an answered decision.
        """
        self.queue()
        (self.at / "goals.tsv").unlink(missing_ok=True)
        stamp = datetime.now(timezone.utc).isoformat()
        tickets = [("T1", "assigned", "user", "", "D1", "Cold start is slow", "", "", ""),
                   ("T2", "assigned", "github", "https://github.com/o/r/issues/2", "D2", "Splash hangs", "urgent", "src/splash.py", ""),
                   ("T3", "assigned", "user", "", "D3", "Cache misses", "", "", ""),
                   ("T4", "assigned", "user", "", "D4", "Login loops", "", "", ""),
                   ("T5", "assigned", "user", "", "D5", "Export stalls", "", "", ""),
                   ("T6", "assigned", "user", "", "D6", "Footer overlaps", "low", "", ""),
                   ("T7", "done", "user", "", "D7", "Dates are wrong", "", "", ""),
                   ("T8", "waiting", "user", "", "", "Importer crashes", "", "", ""),
                   ("T9", "waiting", "user", "", "", "Tune the pool", "normal", "a.txt", ""),
                   ("T10", "waiting", "report", "perf/reports/D7.md#1", "", "Tidy the guide", "", "docs/guide.md", ""),
                   ("T11", "waiting", "user", "", "", "Pick a database", "urgent", "", "yes"),
                   ("T12", "waiting", "user", "", "", "Trim the pool", "low", "a.txt", ""),
                   ("T13", "dropped", "user", "", "", "Old idea", "", "", "")]
        items = [("D1", "in-progress", "bug-fix", "T1", "", "w1", "", "", "", "Fix the cold start", "60", "", "", ""),
                 ("D2", "in-review", "perf-issue", "T2", "", "w2", "perf/d2", "https://github.com/o/r/pull/2", "s2", "Fix the splash", "90", "", "", ""),
                 ("D3", "passed", "bug-fix", "T3", "", "w3", "", "", "s3", "Fix the cache", "60", "", "", ""),
                 ("D4", "sent-back", "bug-fix", "T4", "", "w4", "", "", "s4", "Fix the login", "60", "", "", ""),
                 ("D5", "blocked", "feature", "T5", "", "w5", "", "", "", "Rework the export", "60", "", "", ""),
                 ("D6", "queued", "bug-fix", "T6", "", "w6", "", "", "s6", "Fix the footer", "60", "", "", ""),
                 ("D7", "merged", "feature", "T7", "", "w7", "", "https://github.com/o/r/pull/7", "s7", "Fix the dates", "60", "", "", "yes"),
                 ("D8", "dropped", "refactoring", "T8", "", "w8", "", "", "", "Split the importer", "60", "", "", "")]
        passes = [("D3", "s3", "pass"), ("D4", "s4", "send-back"), ("D6", "s6", "pass"), ("D7", "s7", "pass")]
        decisions = [("Q1", "open", "", "Drop Windows 7 support?", "yes, no", "no", "", "", ""),
                     ("Q2", "answered", "D5", "Which export format?", "csv, json", "csv", "json", "", stamp)]
        log = [*(("ticket", ticket[0], "waiting", ticket[5]) for ticket in tickets),
               *(("dish", item[0], "in-progress", item[9]) for item in items),
               *(("ticket", ticket[0], "assigned", ticket[5]) for ticket in tickets[:7]),
               *(("dish", item[0], item[1], item[9]) for item in items[1:]),
               ("ticket", "T7", "done", "Dates are wrong"), ("ticket", "T13", "dropped", "Old idea"),
               ("86", "Q1", "open", "Drop Windows 7 support?"), ("86", "Q2", "answered", "json")]
        if not rows:
            tickets = items = passes = decisions = log = []
        tables = {
            "rail.tsv": ["id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision",
                         *("\t".join((ident, stamp, *rest)) for ident, *rest in tickets)],
            "dishes.tsv": [self.OLD_DISHES.rstrip("\n"), *("\t".join((ident, stamp, *rest)) for ident, *rest in items)],
            "pass.tsv": ["at\tdish\tpr\tsha\tverdict\tauthor\tverifier\tnote\treport\tmember",
                         *(f"{stamp}\t{item}\t\t{sha}\t{verdict}\t{CLAUDE}\t{CODEX}\t\t\t" for item, sha, verdict in passes)],
            "86.tsv": ["id\tat\tstate\tdish\tquestion\toptions\tdefault\tanswer\tkind\tanswered",
                       *("\t".join((ident, stamp, *rest)) for ident, *rest in decisions)],
            "log.tsv": ["at\tkind\tid\tstate\tnote", *("\t".join((stamp, *row)) for row in log)],
        }
        for name, lines in tables.items():
            (self.at / name).write_text("".join(f"{line}\n" for line in lines))

    OLD_COUNTS = ("waiting tickets: 5, in progress: 1, in review: 1, passed review: 1, waiting to land: 1, sent back: 1, "
                  "blocked: 1, merged: 1, decisions for you: 1")
    OLD_WORKERS = "workers: 2 of 2 running, 0 idle, auto-start: normal"

    def directory_files(self, directory):
        return {str(path.relative_to(directory)): path.read_bytes()
                for path in sorted(Path(directory).rglob("*")) if path.is_file() and path.name != "restaurant.lock"}

    def next_sheet(self, *args):
        text = self.brigade("next", *args)
        lines = text.splitlines()
        self.assertEqual([line for line in lines if line.startswith("reply:")], [lines[-1]])
        self.assertFalse(any(line.startswith("fire") for line in lines))
        for line in lines:
            if line.startswith("run "):
                self.assertNotIn(shlex.split(line[4:])[0], ("fire", "watch", "startable", "land.py"))
        return text

    def fresh(self):
        if self.at.exists():
            shutil.rmtree(self.at)
        self.fired_bug_fix()

    def conditional_runs(self, sheet):
        runs = []
        for line in sheet.splitlines():
            if line.startswith("if ") and ": run " in line:
                runs.append(shlex.split(line.split(": run ", 1)[1]))
        return runs

    def run_printed(self, directory, words):
        if words[0] == "land.py":
            result = subprocess.run(
                [sys.executable, str(ROOT / "t3/added/landing/scripts/land.py"), "--repo", str(self.project), *words[1:]],
                capture_output=True, text=True, env=self.land_env())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return
        self.run_at(directory, *words)

    def execute_commands(self, commands):
        self.assertTrue(commands)
        ran = []
        for words in commands:
            self.assertNotIn(words[0], ("fire", "watch", "startable"))
            copy = Path(self.temporary.name) / f"alone-{time.time_ns()}"
            shutil.copytree(self.at, copy)
            self.run_printed(copy, words)
            ran.append(" ".join(words))
        ordered = Path(self.temporary.name) / f"ordered-{time.time_ns()}"
        shutil.copytree(self.at, ordered)
        for words in commands:
            self.run_printed(ordered, words)
        self.assertEqual(ran, [" ".join(words) for words in commands])
        return ran

    def execute_sheet(self, sheet):
        commands = []
        for line in sheet.splitlines():
            if line.startswith("run "):
                words = shlex.split(line[4:])
                self.assertNotIn(words[0], ("fire", "watch", "startable", "land.py"))
                commands.append(words)
        self.execute_commands(commands)
        conditionals = self.conditional_runs(sheet)
        if conditionals:
            self.execute_commands(conditionals)

    def execute_conditionals(self, sheet):
        return self.execute_commands(self.conditional_runs(sheet))

    def assert_one_close_file(self, sheet):
        self.assertEqual([line for line in sheet.splitlines() if line == "run close --to-file"], ["run close --to-file"])

    def backdate_blocked(self, ident, hours):
        stamp = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="microseconds")
        path = self.at / "log.tsv"
        lines = path.read_text().splitlines()
        rewritten = [lines[0]]
        found = False
        for line in lines[1:]:
            fields = line.split("\t")
            if not found and fields[1:4] == ["ticket", ident, "blocked"]:
                fields[0] = stamp
                line = "\t".join(fields)
                found = True
            rewritten.append(line)
        self.assertTrue(found, ident)
        path.write_text("\n".join(rewritten) + "\n")
        return stamp

    SHARED_DOCS = ("README.md", "docs/guide.md", "t3/runtime.md", "t3/setup.md", "t3/added/brigade/SKILL.md",
                   "t3/overrides/brigade/SKILL.md", "skills/brigade/SKILL.md")

    def doc_tail(self, count):
        return ("drop it from --paths and give the sentences as follow-ups, or pass --doc-item" if count == 1
                else "drop them from --paths and give the sentences as follow-ups, or pass --doc-item")

    def doc_line(self, docs, timebox=None, count=None):
        docs = tuple(sorted(docs))
        if len(docs) == 1:
            head = f"{docs[0]} is a shared document"
        elif len(docs) == 2:
            head = f"{docs[0]} and {docs[1]} are shared documents"
        else:
            head = f"{', '.join(docs[:-1])}, and {docs[-1]} are shared documents"
        reasons = []
        if timebox is not None:
            reasons.append(f"--timebox is {timebox}, over 90 minutes")
        if count is not None:
            reasons.append(f"--tickets names {count} tickets")
        return f"brigade: {head} and {', and '.join(reasons)}; {self.doc_tail(len(docs))}"

    def doc_log_note(self, directory, dish):
        for line in (directory / "log.tsv").read_text().splitlines()[1:]:
            kind, ident, state, note = line.split("\t")[1:5]
            if kind == "doc" and ident == dish and state == "item":
                return note
        self.fail(f"no doc item row for {dish}")

class StoresTest(unittest.TestCase):
    """Two or three coordinators on one project root, as `app/<name>` under the store."""

    def setUp(self):
        self.addCleanup(_drop_entry, self)
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Path(self.temporary.name) / "store"
        self.project = (Path(self.temporary.name) / "app").resolve()
        self.project.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def dir(self, name):
        return self.store / "app" / name

    def run_brigade(self, name, *args):
        if "--owner" not in args:
            args = (*_owner_words(self.dir(name)), *args)
        argv = ["--store", str(self.store), "--at", str(self.dir(name)), *(str(arg) for arg in args)]
        command = [sys.executable, str(SCRIPT), *argv]
        return call(self, SCRIPT, argv).completed(command)

    def brigade(self, name, *args, ok=True):
        result = self.run_brigade(name, *args)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return (result.stdout if ok else result.stderr).strip()

    def open(self, name, *extra):
        return self.brigade(name, "open", "--project-root", str(self.project), "--name", name, *extra)

    def child(self, mode, name, *args):
        support = Path(__file__).resolve()
        tail = _race_args(mode, self.temporary.name,
                          ["--store", str(self.store), "--at", str(self.dir(name)), *args])
        return real_process(ProcessReason.EXIT_STATUS, support, tail, timeout=30,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))

    def paused(self, label, name, *args):
        """A command that has parsed its arguments and waits before it takes the store lock. Every caller starts two of these and lets both reach the wait before either resumes."""
        case = Path(self.temporary.name) / label
        case.mkdir()
        support = Path(__file__).resolve()
        tail = _race_args("pause", case, ["--store", str(self.store), "--at", str(self.dir(name)), *args])
        proc = real_popen(
            ProcessReason.RACE,
            [sys.executable, str(support), *tail],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        _wait_for_path(case / "paused", timeout=20)
        return case, proc

    def resume(self, case, proc):
        (case / "proceed").touch()
        _, err = proc.communicate(timeout=30)
        return proc.returncode, err.strip()

    def files(self, name):
        directory = self.dir(name)
        return {str(path.relative_to(directory)): path.read_bytes() for path in sorted(directory.rglob("*"))
                if path.is_file() and path.name != "restaurant.lock"}

    def rows(self, name, table):
        lines = (self.dir(name) / table).read_text().splitlines()
        return [line.split("\t") for line in lines[1:]]

    def inbox(self, name):
        return sorted(path.name for path in (self.dir(name) / "inbox").glob("*")) if (self.dir(name) / "inbox").exists() else []

    def handoff(self, source="core", target="engine", ref="https://github.com/o/r/issues/7"):
        """core owns github, files T1, and moves it to engine. Returns engine's inbox file."""
        self.open(source, "--intake", "github")
        self.open(target)
        self.brigade(source, "ticket", "add", "--kind", "fix", "--summary", "Fix the cache", "--source", "github", "--ref", ref)
        self.brigade(source, "ticket", "move", "T1", "--to", target)
        return self.dir(target) / "inbox" / f"app~{source}~T1.json"

LAND_SCRIPT = ROOT / "t3/added/landing/scripts/land.py"

def _land_has_rulings():
    """Change 8's share, lease reserve, and contest commands, which the admin's rulings run."""
    for words in (("share",), ("lease", "reserve"), ("contest",)):
        result = subprocess.run([sys.executable, str(LAND_SCRIPT), *words, "--help"], capture_output=True, text=True)
        if result.returncode != 0:
            return False
    return True

class SeatLaunchDocTest(unittest.TestCase):
    def setUp(self):
        self.skill = (ROOT / "skills/brigade/SKILL.md").read_text()
        service = self.skill.split("## Run a service", 1)[1].split("\n## ", 1)[0]
        self.launch = next(line for line in service.splitlines() if line.startswith("   3. Launch the worker"))
        self.review = next(line for line in service.splitlines() if line.startswith("6. **Review.**"))

    def test_step_4_3_links_both_runtime_anchors_and_records_after_the_match(self):
        self.assertIn("../pstack-runtime/SKILL.md#top-level-threads", self.launch)
        self.assertIn("../pstack-runtime/SKILL.md#delegation", self.launch)
        self.assertIn("Step 4.4 records the thread only after the readback matches.", self.launch)

    def test_step_6_reads_configuration_before_the_verdict(self):
        self.assertIn(
            "Right after the spawn, confirm its options with `t3_thread_configuration` on the returned `childThreadId` before the verdict counts",
            self.review,
        )

class LivenessIdleDocTest(unittest.TestCase):
    def test_step_5_nudges_an_idle_worker_with_its_timebox_open(self):
        text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        section = text.split("## Liveness check", 1)[1].split("\n## ", 1)[0]
        step = next(line for line in section.splitlines() if line.startswith("5. "))
        self.assertIn("activeRunId", step)
        self.assertIn("nudge-<dish>-<that run id>", step)
        self.assertIn("goes to step 4", step)

    def test_a_held_item_skips_liveness_steps_2_to_5(self):
        text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        section = text.split("## Liveness check", 1)[1].split("\n## ", 1)[0]
        self.assertIn(
            "`D1: open decision Q1: <question>; launch no worker or verifier until 86 answer Q1`",
            section,
        )
        self.assertIn("Launch nothing and spawn nothing.", section)
        self.assertIn("Skip Liveness steps 2 to 5 for that item.", section)
        self.assertIn("Step 4.4 records that thread", section)
        self.assertNotIn("Step 4.3 records that thread", section)
        self.assertNotIn("Skip this line while `$B 86 list` parks that dish.", section)

class UsageLimitDocTest(unittest.TestCase):
    def test_step_7_relaunches_a_usage_limit_before_the_light_mode_question(self):
        text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        service = text.split("## Run a service", 1)[1].split("\n## ", 1)[0]
        bullet = next(line for line in service.splitlines() if line.startswith("   - Usage limit:"))
        self.assertIn("../pstack-runtime/SKILL.md#failure-handling", bullet)
        self.assertIn("roles.py backup", bullet)
        self.assertIn("The dish keeps its lease", bullet)
        self.assertIn("Never call `t3_thread_send` on the old worker.", bullet)
        question = (
            '$B 86 add --question "<provider> hit its usage limit. '
            'Run new work in light mode until it resets?" '
            '--options "light, full" --default "light"'
        )
        self.assertIn(question, bullet)
        self.assertLess(bullet.index("t3_thread_interrupt"), bullet.index("roles.py backup"))
        self.assertLess(bullet.index("roles.py backup"), bullet.index(question))
        self.assertIn("../pstack-runtime/SKILL.md#modes", bullet)
        self.assertIn("../pstack-runtime/SKILL.md#gate-review", bullet)
        self.assertIn("each code delegate's `providerInstanceId/model`", bullet)
        self.assertIn("Never pass only the worker's model when a delegate wrote the code.", bullet)
        self.assertIn("--resume", bullet)
        self.assertIn("keeps its lease and its branch", bullet)
        self.assertIn("A new branch never widens the lease.", bullet)
        self.assertLess(bullet.index("On `park`"), bullet.index("--resume"))
        self.assertIn("A parked verifier stays in review.", bullet)
        self.assertNotIn("relaunch per this bullet on the first service after it", bullet)

    def test_step_7_runs_a_review_backups_panel(self):
        text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        service = text.split("## Run a service", 1)[1].split("\n## ", 1)[0]
        bullet = next(line for line in service.splitlines() if line.startswith("   - Usage limit:"))
        relaunch = "When it prints `relaunch`, spawn a fresh verifier per step 6 on the printed seat."
        panel = (
            "When it prints `panel`, spawn one fresh verifier per step 6 on each printed seat, "
            "keep the dish in review until every one is terminal, then apply the printed rule per "
            "[Failure handling](../pstack-runtime/SKILL.md#failure-handling)."
        )
        rows = (
            "Run `$B pass record` once for each member that returned a verdict, "
            "with that member's seat as `--verifier` and its file as `--report`."
        )
        passes = (
            "When the rule passes, record each passing member's `pass` with no other flag "
            "and every other member with `--member`."
        )
        resumed = "When `--resume` prints `panel`, run the panel the same way."
        for phrase in (
            panel,
            "`<restaurant dir>/reports/<dish>-review-<k>-panel-<n>.md`",
            rows,
            passes,
            "record that member's `send-back` with no other flag and every other member with `--member`",
            "When fewer than two pass and none reproduces a blocker, record every member with `--member`, "
            "and the dish stays in review as on `park`.",
            resumed,
        ):
            self.assertIn(phrase, bullet)
        self.assertNotIn("copy its findings", bullet)
        self.assertLess(bullet.index(relaunch), bullet.index(panel))
        self.assertLess(bullet.index(panel), bullet.index("On `park`"))
        self.assertLess(bullet.index("When `--resume` prints `relaunch` for `verifiers`"), bullet.index(resumed))

class RoundBudgetDocTest(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        self.service = self.text.split("## Run a service", 1)[1].split("\n## ", 1)[0].splitlines()

    def test_the_script_section_names_the_closing_answers_and_the_budget(self):
        script = self.text.split("## The script", 1)[1].split("\n## ", 1)[0]
        self.assertIn("A dish's round count is its send-backs written after the later of its last passing verdict "
                      "and its last answered round decision.", script)
        self.assertIn("A dish owes a round decision while that count is three or more and its state is "
                      "`in-progress`, `in-review`, `sent-back`, or `blocked`.", script)
        self.assertIn("Only `known limits`, `redesign`, or `drop` closes it.", script)
        self.assertIn("No other decision's answer restarts the count.", script)
        self.assertIn("The budget refuses nothing else.", script)
        for command in ("`dish --state in-progress`", "`dish --thread` or `--task`", "a `brief` whose fields parse",
                        "`fire` exits 1 on a ticket of that dish"):
            self.assertIn(command, script)
        self.assertIn("`ROUND_BUDGET`", script)
        for old in ("whatever state", "after the dish is dropped", "A queue bounce of that dish is refused"):
            self.assertNotIn(old, script)

    def test_step_6_records_every_round_with_its_report(self):
        review = next(line for line in self.service if line.startswith("6. **Review.**"))
        self.assertTrue(review.endswith(
            "Record every round, pass or fail, with `$B pass record` at the reviewed SHA and `--report <that file>`."
        ))

    def test_step_7_stops_fix_rounds_at_a_pending_decision(self):
        pending = [line for line in self.service
                   if line.startswith("   - When `pass record` or `watch` prints `<n> send-backs, decision pending`,")]
        self.assertEqual(len(pending), 1)
        bullet = pending[0]
        send_back = next(line for line in self.service if line.startswith("   - `send-back`:"))
        self.assertLess(self.service.index(bullet), self.service.index(send_back))
        self.assertIn("`$B 86 add --dish <id> --round-budget`", bullet)
        self.assertIn('`$B 86 answer Q<n> --answer "<option>"`', bullet)
        self.assertIn("On `known limits`", bullet)
        self.assertIn("On `redesign`", bullet)
        self.assertIn("On `drop`", bullet)
        self.assertIn("Finish with the old worker", bullet)
        self.assertIn("launch no fix worker", bullet)
        self.assertIn("Follow this bullet in place of the `send-back` bullet below.", bullet)
        self.assertNotIn("bounce", bullet)
        self.assertNotIn("decision pending", send_back)

    def test_the_liveness_check_points_a_pending_decision_at_step_7(self):
        section = self.text.split("## Liveness check", 1)[1].split("\n## ", 1)[0]
        lines = [line for line in section.splitlines() if line.startswith("   - `D1: 3 send-backs, decision pending`.")]
        self.assertEqual(len(lines), 1)
        self.assertIn("decision-pending bullet in Run a service step 7", lines[0])

class AnswerRelayDocTest(unittest.TestCase):
    def test_the_requests_answer_row_names_still_open(self):
        text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        requests = text.split("**Requests.**", 1)[1].split("\n**", 1)[0]
        row = next(line for line in requests.splitlines() if line.startswith("| `answer "))
        self.assertIn("still open", row)

class _TicketsRace:
    def test_watch_rechecks_a_lease_outside_the_store_lock(self):
        case = Path(self.temporary.name)
        (case / "land.py").write_text(
            "import sys, time\nfrom pathlib import Path\n"
            f"case = Path({str(case)!r})\n"
            "action = sys.argv[4]\n"
            "if action == 'claim':\n"
            "    print('paths overlap L1 held by engine/D1 on src', file=sys.stderr)\n"
            "    sys.exit(1)\n"
            "if action == 'check':\n"
            "    (case / 'checking').touch()\n"
            "    end = time.monotonic() + 30\n"
            "    while not (case / 'check-proceed').exists() and time.monotonic() < end:\n        time.sleep(0.01)\n"
            "    print('L1 held by engine/D1 on src')\n"
            "    sys.exit(1)\n"
            "print('ok')\n")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        refused = real_process(
            ProcessReason.EXIT_STATUS, Path(__file__).resolve(),
            _race_args("fake-land", case, ["--store", str(self.store), "--at", str(self.at),
                                           "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "src"]))
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertIn("blocked: lease", self.brigade("ticket", "list"))
        watching = real_popen(
            ProcessReason.RACE,
            [sys.executable, str(Path(__file__).resolve()), *_race_args(
                "fake-land", case, ["--store", str(self.store), "--at", str(self.at), "watch"])],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        try:
            _wait_for_path(case / "checking")
            # race between two processes
            added = real_process(
                ProcessReason.RACE, SCRIPT,
                ["--store", str(self.store), "--at", str(self.at),
                 "ticket", "add", "--kind", "fix", "--summary", "two"],
                timeout=10,
            )
            self.assertEqual(added.stdout.strip(), "T2", added.stderr)
            (case / "check-proceed").touch()
            out, err = watching.communicate(timeout=10)
        finally:
            watching.kill()
        self.assertEqual(watching.returncode, 0, err)
        self.assertEqual(out.strip(), "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: waiting on L1 (engine/D1)")


class _ItemsRace:
    def test_fire_warns_when_releasing_a_conflicting_lease_fails(self):
        case = Path(self.temporary.name)
        (case / "land.py").write_text(
            "import sys, time\nfrom pathlib import Path\n"
            f"case = Path({str(case)!r})\n"
            "with open(case / 'land-calls', 'a') as handle:\n    handle.write(' '.join(sys.argv[3:]) + '\\n')\n"
            "if sys.argv[4] == 'claim':\n"
            "    (case / 'claiming').touch()\n"
            "    end = time.monotonic() + 30\n"
            "    while not (case / 'claim-proceed').exists() and time.monotonic() < end:\n        time.sleep(0.01)\n"
            "    print('L7')\n"
            "else:\n"
            "    print('lease L7 is held elsewhere', file=sys.stderr)\n"
            "    sys.exit(1)\n")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        firing = real_popen(
            ProcessReason.RACE,
            [sys.executable, str(Path(__file__).resolve()), *_race_args(
                "fake-land", case, ["--store", str(self.store), "--at", str(self.at),
                                    "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "src"])],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        try:
            _wait_for_path(case / "claiming")
            # race between two processes
            other = real_process(
                ProcessReason.RACE, SCRIPT,
                ["--store", str(self.store), "--at", str(self.at),
                 "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "other"],
                timeout=10,
            )
            self.assertEqual(other.stdout.strip(), "D1", other.stderr)
            (case / "claim-proceed").touch()
            _, err = firing.communicate(timeout=10)
        finally:
            firing.kill()
        self.assertEqual(firing.returncode, 1)
        self.assertIn("nothing fired: T1 is assigned, not waiting", err)
        self.assertIn("L7", err)
        self.assertIn("lease L7 is held elsewhere", err)


class _ReportingRace:
    def test_fire_claims_outside_the_store_lock_and_rechecks_before_it_writes(self):
        case = Path(self.temporary.name)
        calls = case / "land-calls"
        (case / "land.py").write_text(
            "import sys, time\nfrom pathlib import Path\n"
            f"case = Path({str(case)!r})\n"
            "with open(case / 'land-calls', 'a') as f:\n    f.write(' '.join(sys.argv[3:]) + '\\n')\n"
            "if sys.argv[4] == 'claim':\n"
            "    (case / 'claiming').touch()\n"
            "    end = time.monotonic() + 30\n"
            "    while not (case / 'claim-proceed').exists() and time.monotonic() < end:\n        time.sleep(0.01)\n"
            "    print('L7')\n"
            "else:\n    print('L7 released')\n")
        self.open()
        (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        firing = real_popen(
            ProcessReason.RACE,
            [sys.executable, str(Path(__file__).resolve()), *_race_args(
                "fake-land", case, ["--store", str(self.store), "--at", str(self.at),
                                    "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "src"])],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        try:
            _wait_for_path(case / "claiming")
            # race between two processes
            other = real_process(
                ProcessReason.RACE, SCRIPT,
                ["--store", str(self.store), "--at", str(self.at),
                 "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "other"],
                timeout=10,
            )
            self.assertEqual(other.stdout.strip(), "D1", other.stderr)
            (case / "claim-proceed").touch()
            _, err = firing.communicate(timeout=10)
        finally:
            firing.kill()
        self.assertEqual(firing.returncode, 1)
        self.assertEqual(err.strip(), "brigade: nothing fired: T1 is assigned, not waiting")
        self.assertEqual(calls.read_text().splitlines(),
                         ["lease claim --holder perf/D1 --paths src,changes/perf%2Fd1.md", "lease release L7"])
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract, in progress: 1\nworkers: 1 of 2 running, 1 idle, auto-start: normal\nkinds: fix 1 in flight")


class _WakeRace:
    def test_a_failed_lease_check_keeps_the_landing_diagnostic(self):
        case = Path(self.temporary.name)
        (case / "land.py").write_text(
            "import sys\n"
            "action = sys.argv[4]\n"
            "if action == 'claim':\n"
            "    print('paths overlap L1 held by engine/D1 on src', file=sys.stderr)\n"
            "    sys.exit(1)\n"
            "if action == 'check':\n"
            "    print('queue database is locked', file=sys.stderr)\n"
            "    sys.exit(1)\n"
            "sys.exit(0)\n")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        refused = real_process(
            ProcessReason.EXIT_STATUS, Path(__file__).resolve(),
            _race_args("fake-land", case, ["--store", str(self.store), "--at", str(self.at),
                                           "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "src"]))
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        watched = real_process(
            ProcessReason.EXIT_STATUS, Path(__file__).resolve(),
            _race_args("fake-land", case, ["--store", str(self.store), "--at", str(self.at), "watch"]))
        self.assertEqual(watched.returncode, 0, watched.stderr)
        self.assertEqual(watched.stdout.strip(),
                         "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: waiting on the landing queue (queue database is locked)")


class _HandoffRace:
    def test_two_processes_appending_200_rows_each_leave_400_rows(self):
        self.open("core")
        support = Path(__file__).resolve()
        procs = [
            real_popen(
                ProcessReason.RACE,
                [sys.executable, str(support), *_race_args("append", self.temporary.name, [str(self.dir("core")), label])],
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                text=True)
            for label in ("a", "b")]
        (Path(self.temporary.name) / "go").touch()
        for proc in procs:
            _, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
        rows = self.rows("core", "log.tsv")
        self.assertEqual(len(rows), 400)
        self.assertTrue(all(len(row) == 5 for row in rows))
        self.assertEqual(sorted(row[4] for row in rows),
                         sorted(f"{label} row {number}" for label in "ab" for number in range(200)))
        self.assertEqual(self.brigade("core", "ticket", "list"), "")


class _AdminRace:
    def admin_child(self, mode, label, *args):
        """Every caller overlaps another started process before this one resumes."""
        case = Path(self.temporary.name) / label
        case.mkdir()
        support = Path(__file__).resolve()
        tail = _race_args(mode, case, ["--store", str(self.store), "--at", str(self.dir(".admin")), *args])
        proc = real_popen(
            ProcessReason.RACE,
            [sys.executable, str(support), *tail],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return case, proc

    def append_row(self, name, *fields):
        """The caller overlaps an already started pause child and does not wait on that child's lock."""
        support = Path(__file__).resolve()
        tail = _race_args("append-row", self.temporary.name, [str(self.dir(name)), *fields])
        proc = real_popen(
            ProcessReason.RACE,
            [sys.executable, str(support), *tail],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    @unittest.skipUnless(_land_has_rulings(), "land.py has no share, lease reserve, or contest yet; change 8 adds them, "
                                              "and this test runs once it lands")
    def test_after_a_recovery_the_old_runs_paused_land_rulings_change_nothing(self):
        state = Path(self.temporary.name) / "state"
        env = dict(os.environ, XDG_STATE_HOME=str(state))
        from unittest import mock
        patcher = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(state)})
        patcher.start()
        self.addCleanup(patcher.stop)

        def land(*args, ok=True):
            result = subprocess.run([sys.executable, str(LAND_SCRIPT), "--repo", str(self.project), *args],
                                    capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
            return (result.stdout if ok else result.stderr).strip()

        git = lambda *command: subprocess.run(command, cwd=self.project, capture_output=True, text=True, check=True)
        git("git", "init", "-q", "-b", "main")
        (self.project / "README.md").write_text("a\n")
        git("git", "add", "-A")
        git("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        land("init", "--trunk", "lane", "--mode", "local", "--base", "main", "--cap", "3")
        self.with_thread()
        contest = land("contest", "--holders", "docs/D1,engine/D1", "--owner", ".admin/@1")
        old = ("--owner", ".admin/@1")
        commands = [("share", "--for", "docs/", "1", *old),
                    ("lease", "reserve", "--for", "docs/", "--paths", "README.md", "--ruling", "R1", *old),
                    ("contest", "--settle", contest, "--first", "docs/D1", *old)]
        running = []
        for number, args in enumerate(commands):
            case = Path(self.temporary.name) / f"land{number}"
            case.mkdir()
            proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--race-child", "land-pause", str(case),
                                     "--repo", str(self.project), *args],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
            self.addCleanup(lambda proc=proc: proc.poll() is None and proc.kill())
            running.append((case, proc))
        for case, _ in running:
            _wait_for_path(case / "paused", timeout=20)
        self.admin("set", "--thread", "recovering:t9", "--replace", "--expect", "t1")
        self.admin("set", "--thread", "t2", "--replace", "--expect", "recovering:t9", "--stopped", "r1")
        before = (land("lease", "list"), land("status"))
        for case, proc in running:
            (case / "proceed").touch()
            code, _, err = self.finish(proc)
            self.assertEqual((code, err), (1, "land: owner .admin/@1 is stale; .admin/ is at generation 3"))
        self.assertEqual((land("lease", "list"), land("status")), before)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--race-child":
        try:
            _race_child(sys.argv[2], sys.argv[3], sys.argv[4:])
        except TimeoutError as error:
            print(f"race child timed out waiting for {error}", file=sys.stderr)
            sys.exit(2)
    else:
        unittest.main()
