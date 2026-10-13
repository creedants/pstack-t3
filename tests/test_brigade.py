import fcntl
import io
import json
import os
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
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
        result = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(directory), *args],
                                capture_output=True, text=True, input=stdin)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return (result.stdout if ok else result.stderr).strip()

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
        result = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
                                 "pass", "check", "D1", "--sha", sha, "--json"], capture_output=True, text=True)
        return result.returncode, result.stdout, result.stderr.strip()

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


    def test_from_report_files_a_numbered_list_and_prints_the_paragraph_after_it(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(NUMBERED, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: **Hillclimb.** `t3/overrides/poteto-mode/playbooks/hillclimb.md` has a "
            "code-delegate line with the same missing step 5 pointer. It was outside the lease.",
            "would add from perf/reports/D1.md#2: **`WAIT_RULE` in `t3/added/brigade/scripts/brigade.py`.** It states only "
            "the ten-minute clock for worker briefs. Decide whether it should point at the new step 5 bullet.",
            "would add from perf/reports/D1.md#3: No README or `docs/guide.md` edit is needed.",
            "not filed, prose after the last list item: Scratch is under `~/.cache/d87`.",
        ])

    def test_from_report_files_prose_that_neither_introduces_a_list_nor_follows_the_last_item(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(PROSE_ABOVE_A_LEAD_IN, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: **`README.md`.** Replace the `**Known limits.**` bullet (line 284), "
            "which still says \"Nothing deletes it or moves it back\", with these two bullets: ```markdown "
            "- **Interrupted runs are recovered.** Every later install or uninstall looks in those directories. "
            "- **Known limits.** Nothing returns or deletes it, so delete it by hand. ```",
            "would add from perf/reports/D1.md#2: **`docs/guide.md`.** Replace the first bullet under \"Known limits.\" "
            "(line 359) with this bullet, and keep the two bullets after it: ```markdown - An entry in a "
            "`.pstack-t3-scrap-*` directory is an unfinished copy. Delete it by hand. ```",
            "would add from perf/reports/D1.md#3: **Type the `side` that `holder_parents` yields**, from the comment review.",
            "would add from perf/reports/D1.md#4: **No test covers a skills directory inside the checkout**, which the "
            "sweep skips because `layout` refuses it.",
            "not filed, prose that introduces a list: Other follow-ups:",
            "not filed, prose after the last list item: Scratch files for this run are under `/tmp/d105-repro/`: "
            "`repro.py`, `before.txt`, and `full-suite.txt`.",
        ])

    def test_from_report_files_the_prose_of_a_section_with_no_list_beside_a_section_with_one(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(A_LIST_THEN_A_PROSE_SECTION, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: `docs/light-mode.md`, in its review-file table, still describes one "
            "`<item>-review.md` file per item.",
            "would add from perf/reports/D1.md#2: Schema setup in `land.py` still runs outside `BEGIN IMMEDIATE`.",
            "would add from perf/reports/D1.md#3: `integration.head()` is still read inside two transactions.",
        ])

    def test_from_report_does_not_file_a_block_that_is_one_of_eight_no_work_phrases_and_nothing_more(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(NO_WORK, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: **Docs.** `README.md` and `docs/guide.md` do not mention the "
            "`recovered`, `removed`, and `left` lines.",
            "not filed, says no work is needed: None.",
            "not filed, says no work is needed: **None required.**",
            "not filed, says no work is needed: NONE NEEDED",
            "not filed, says no work is needed: _Nothing._",
            "not filed, says no work is needed: `Nothing needed`.",
            "not filed, says no work is needed: n/a",
            "not filed, says no work is needed: Not applicable.",
            "not filed, says no work is needed: **No follow-ups**.",
        ])

    def test_from_report_files_every_block_longer_than_a_no_work_phrase(self):
        self.fired_bug_fix()
        body = "## Follow-ups\n\n" + "".join(f"- {text}\n" for text in LONGER_THAN_A_NO_WORK_PHRASE)
        self.assertEqual(self.follow_ups_in(body, "--dry-run").splitlines(), [
            f"would add from perf/reports/D1.md#{number}: {text}"
            for number, text in enumerate(LONGER_THAN_A_NO_WORK_PHRASE, 1)])
        for text in LONGER_THAN_A_NO_WORK_PHRASE[-3:]:
            self.assertEqual(self.follow_ups_in(f"## Follow-ups\n\n{text}\n", "--dry-run"),
                             f"would add from perf/reports/D1.md#1: {text}")

    def test_from_report_folds_nested_bullets_and_an_indented_paragraph_into_their_item(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(NESTED, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: **D90-dependent wording.** These sentences hold for main and change if "
            "D90 lands: - Prompt caps step 1: \"It still applies the Haiku prompt cap.\" - Step 4: the rerun of a "
            "seat-launching child on `inherit`. If D90 makes the flag skip capped models, step 1 should say the flag never "
            "returns a capped model.",
            "would add from perf/reports/D1.md#2: **Docs batch.** - Change 4's Files list in `docs/light-mode.md` should "
            "add `t3/scripts/roles.py`. - `docs/light-mode.md` is not leased.",
        ])

    def test_from_report_keeps_a_fenced_block_in_its_item_and_reads_no_heading_inside_it(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(FENCED, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Run this command to set the user's panel. It must use a fresh catalog. "
            "```bash # write the panel python3 roles.py write --catalog <file> --keep \\ "
            "--set \"review backups=opencode/step-5-preview-free?variant=high\" ```",
            "would add from perf/reports/D1.md#2: `family()` keeps the provider path for namespaced ids such as "
            "`opencode/...`. The panel compares bare ids locally.",
        ])

    def test_from_report_files_each_paragraph_of_a_section_with_no_list(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(PROSE, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Schema setup in `land.py` still runs outside `BEGIN IMMEDIATE`.",
            "would add from perf/reports/D1.md#2: `integration.head()` is still read inside two transactions. "
            "Both sites predate this change.",
            "would add from perf/reports/D1.md#3: No test under `tests/` asserts these sentences. "
            "That directory is outside this lease.",
        ])

    def test_from_report_adds_nothing_for_a_section_that_says_none_or_a_report_without_one(self):
        self.fired_bug_fix()
        before = (self.at / "rail.tsv").read_bytes()
        for written, text in (("None.", "None."), ("**None.**", "**None.**"), ("  - n/a", "- n/a"),
                              ("No\nfollow-ups.", "No follow-ups.")):
            self.assertEqual(self.follow_ups_in(f"## Follow-ups\n\n{written}\n").splitlines(), [
                "reports/D1.md lists no follow-ups; nothing added",
                f"not filed, says no work is needed: {text}",
            ])
        self.assertEqual(self.follow_ups_in("# D1 report\n\nStatus: done\n\n## Deviations\n\n- None.\n"),
                         "reports/D1.md has no follow-ups section; nothing added")
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_reads_five_heading_forms(self):
        self.fired_bug_fix()
        for heading, rest in (("## Follow-ups (outside lease L91)", ["(outside lease L91)"]), ("### Follow-ups", []),
                              ("## Follow-up", []), ("## Follow-ups outside my lease", ["outside my lease"]),
                              ("## follow-ups", [])):
            self.assertEqual(self.follow_ups_in(f"# D1 report\n\n{heading}\n\n- One thing.\n", "--dry-run").splitlines(),
                             ["would add from perf/reports/D1.md#1: One thing.",
                              *(f"not filed, text on the heading line: {text}" for text in rest)], heading)

    def test_from_report_prints_text_on_the_heading_line_and_does_not_file_it(self):
        self.fired_bug_fix()
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.follow_ups_in("## Follow-ups: None of the tests cover the retry path.\n").splitlines(), [
            "reports/D1.md lists no follow-ups; nothing added",
            "not filed, text on the heading line: None of the tests cover the retry path.",
        ])
        self.assertEqual(self.follow_ups_in("## Follow-ups\t: none\n").splitlines(), [
            "reports/D1.md lists no follow-ups; nothing added",
            "not filed, text on the heading line: none",
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)
        self.assertEqual(self.follow_ups_in("## Follow-ups (outside lease L91)\n\n- One thing.\n", "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: One thing.",
            "not filed, text on the heading line: (outside lease L91)",
        ])
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- One thing.\n", "--dry-run"),
                         "would add from perf/reports/D1.md#1: One thing.")

    def test_from_report_prints_a_deeper_heading_inside_the_section_and_does_not_file_it(self):
        self.fired_bug_fix()
        before = (self.at / "rail.tsv").read_bytes()
        body = "## Follow-ups\n\nLead in.\n\n### Parser\n\n- fix a\n- fix b\n\n### Docs\n\nClosing note.\n"
        self.assertEqual(self.follow_ups_in(body, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: fix a",
            "would add from perf/reports/D1.md#2: fix b",
            "not filed, prose that introduces a list: Lead in.",
            "not filed, heading inside the section: Parser",
            "not filed, heading inside the section: Docs",
            "not filed, prose after the last list item: Closing note.",
        ])
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n### None of these block the merge\n").splitlines(), [
            "reports/D1.md lists no follow-ups; nothing added",
            "not filed, heading inside the section: None of these block the merge",
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_prints_a_deeper_heading_with_no_text_and_not_the_follow_ups_heading_with_no_text(self):
        self.fired_bug_fix()
        before = (self.at / "rail.tsv").read_bytes()
        for extra in ((), ("--dry-run",)):
            self.assertEqual(self.follow_ups_in("## Follow-ups\n\n###\n", *extra).splitlines(), [
                "reports/D1.md lists no follow-ups; nothing added",
                "not filed, heading inside the section with no text",
            ])
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\nLead in.\n\n###   \n\n- fix a\n", "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: fix a",
            "not filed, prose that introduces a list: Lead in.",
            "not filed, heading inside the section with no text",
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_prints_a_lead_in_above_a_list_and_does_not_file_it(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(LEAD_IN, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: `pass record` without `--pr` overwrites the item's recorded PR with an "
            "empty value, because `--pr` defaults to `\"\"`.",
            "would add from perf/reports/D1.md#2: `--same-family` is not checked. It writes the `same model family;` note "
            "even when the families differ.",
            "not filed, prose that introduces a list: The explainer found these in `brigade.py`. All are outside L81, "
            "so I did not fix them.",
        ])

    def test_from_report_numbers_the_items_of_two_sections_as_one_list(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in(TWO_SECTIONS, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: `docs/light-mode.md`, in its review-file table, still describes one "
            "`<item>-review.md` file per item.",
            "would add from perf/reports/D1.md#2: The panel sentences in step 7 still say \"handle the send-back below\" "
            "while that bullet sits above them.",
            "would add from perf/reports/D1.md#3: Decide whether `ticket move` should refuse a ticket of an item that owes "
            "its decision.",
        ])


    def test_from_report_dry_run_waits_for_a_writer_that_holds_the_store_lock(self):
        self.fired_bug_fix()
        self.review_file("D1.md", BULLETS)
        held = os.open(self.at / "restaurant.lock", os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, held)
        fcntl.flock(held, fcntl.LOCK_EX)
        dry_run = subprocess.Popen([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
                                    "ticket", "add", "--from-report", "reports/D1.md", "--dry-run"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(0.3)
            self.assertIsNone(dry_run.poll())
            fcntl.flock(held, fcntl.LOCK_UN)
            out, err = dry_run.communicate(timeout=10)
        finally:
            dry_run.kill()
        self.assertEqual([line.split(":")[0] for line in out.splitlines()],
                         [f"would add from perf/reports/D1.md#{number}" for number in (1, 2, 3)], err)



    def test_from_report_skips_the_second_of_two_items_with_the_same_text(self):
        self.fired_bug_fix()
        twice = "## Follow-ups\n\n- Same thing.\n- same  THING.\n- Other thing.\n"
        self.assertEqual(self.follow_ups_in(twice, "--dry-run").splitlines(), [
            "would add from perf/reports/D1.md#1: Same thing.",
            "skipped perf/reports/D1.md#2, same text as #1 above: same  THING.",
            "would add from perf/reports/D1.md#3: Other thing.",
        ])
        self.assertEqual(self.follow_ups_in(twice).splitlines(), [
            "T2 added from perf/reports/D1.md#1: Same thing.",
            "skipped perf/reports/D1.md#2, same text as T2 (waiting): same  THING.",
            "T3 added from perf/reports/D1.md#3: Other thing.",
        ])

    def test_from_report_dry_run_prints_a_list_item_with_no_text_as_not_filed(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- \n- One thing.\n", "--dry-run"),
                         "would add from perf/reports/D1.md#1: One thing.\nnot filed, list item with no text")

    def test_from_report_takes_the_report_by_name_or_by_a_path_into_this_store(self):
        self.fired_bug_fix()
        path = self.review_file("D1.md", "## Follow-ups\n\n- One thing.\n")
        for spelling in ("D1.md", "reports/D1.md", str(path)):
            self.assertEqual(self.brigade("ticket", "add", "--from-report", spelling, "--dry-run"),
                             "would add from perf/reports/D1.md#1: One thing.", spelling)

    def test_from_report_refuses_what_is_not_an_item_report_of_this_store(self):
        self.fired_bug_fix()
        body = "## Follow-ups\n\n- One thing.\n"
        self.review_file("D1.md", body)
        before = (self.at / "rail.tsv").read_bytes()
        add = ("ticket", "add", "--from-report")
        for name in ("D1-review-1.md", "notes.md", "D1-attempt1.md"):
            self.review_file(name, body)
            self.assertEqual(self.brigade(*add, f"reports/{name}", ok=False),
                             f"brigade: {name} is not an item report; name a file like reports/D2.md")
        elsewhere = Path(self.temporary.name) / "elsewhere" / "D1.md"
        elsewhere.parent.mkdir()
        elsewhere.write_text(body)
        self.assertEqual(self.brigade(*add, str(elsewhere), ok=False),
                         f"brigade: {elsewhere} is outside this store's reports/; name reports/D1.md")
        self.assertEqual(self.brigade(*add, "reports/D7.md", ok=False),
                         "brigade: reports/D7.md does not exist; nothing added")
        self.review_file("D9.md", body)
        self.assertEqual(self.brigade(*add, "reports/D9.md", ok=False), "brigade: no D9 in dishes.tsv")
        (self.at / "reports" / "D1.md").write_bytes(b"## Follow-ups\n\n- caf\xe9\n")
        self.assertEqual(self.brigade(*add, "reports/D1.md", ok=False),
                         "brigade: reports/D1.md is not UTF-8 text; nothing added")
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_refuses_a_directory_named_like_an_item_report(self):
        self.fired_bug_fix()
        (self.at / "reports" / "D1.md").mkdir(parents=True)
        before = (self.at / "rail.tsv").read_bytes()
        for extra in ((), ("--dry-run",)):
            self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md", *extra, ok=False),
                             "brigade: reports/D1.md is not a regular file; nothing added")
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_refuses_a_symbolic_link_and_a_file_that_resolves_outside_reports(self):
        self.fired_bug_fix()
        body = "## Follow-ups\n\n- Outside file was read.\n"
        report = self.at / "reports" / "D1.md"
        outside = Path(self.temporary.name) / "outside"
        (outside / "reports").mkdir(parents=True)
        (outside / "D1.md").write_text(body)
        (outside / "reports" / "D1.md").write_text(body)
        notes = self.review_file("notes.md", body)
        before = (self.at / "rail.tsv").read_bytes()
        add = ("ticket", "add", "--from-report")
        for target in (outside / "D1.md", notes):
            report.symlink_to(target)
            for spelling in ("D1.md", "reports/D1.md", str(report)):
                for extra in ((), ("--dry-run",)):
                    self.assertEqual(self.brigade(*add, spelling, *extra, ok=False),
                                     "brigade: reports/D1.md is a symbolic link; nothing added")
            self.assertEqual(self.brigade(*add, str(outside / "D1.md"), "--dry-run", ok=False),
                             f"brigade: {outside / 'D1.md'} is outside this store's reports/; name reports/D1.md")
            report.unlink()
        notes.unlink()
        (self.at / "reports").rmdir()
        (self.at / "reports").symlink_to(outside / "reports")
        for extra in ((), ("--dry-run",)):
            self.assertEqual(self.brigade(*add, "reports/D1.md", *extra, ok=False),
                             "brigade: reports/D1.md resolves outside this store's reports/; nothing added")
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_from_report_refuses_a_path_with_a_dot_dot_component_wherever_it_resolves(self):
        self.fired_bug_fix()
        reports = self.review_file("D1.md", "## Follow-ups\n\n- One thing.\n").parent
        before = (self.at / "rail.tsv").read_bytes()
        for spelling in ("reports/../reports/D1.md", "../D1.md", f"{reports}/../reports/D1.md",
                         f"{reports}/../../../../outside/D1.md", "reports/.."):
            for extra in ((), ("--dry-run",)):
                self.assertEqual(self.brigade("ticket", "add", "--from-report", spelling, *extra, ok=False),
                                 f"brigade: {spelling} has a .. component; name a file like reports/D2.md")
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)




























    def close_run(self, *flags):
        result = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
                                 *_owner_words(self.at), "close", *flags], capture_output=True, text=True)
        return result.returncode, result.stdout.strip(), result.stderr.strip()

    def no_row(self, name, dish="D1"):
        return (f"brigade: warning: reports/{name} has no review row; record it with pass record {dish} --report {name}, "
                f"and --late when {dish} has moved past that round")

    def aged(self, name, year):
        path = self.review_file(name)
        stamp = datetime(year, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def test_close_warns_about_a_review_report_with_no_row_and_still_exits_0(self):
        self.fired_bug_fix()
        code, before, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.review_file("D1-review-2.md")
        self.assertEqual(self.close_run("--dry-run"), (0, before, self.no_row("D1-review-2.md")))
        code, out, err = self.close_run("--to-file")
        self.assertEqual((code, err), (0, self.no_row("D1-review-2.md")))
        self.assertEqual(Path(out).read_text().strip(), before)
        code, out, err = self.close_run()
        self.assertEqual((code, err), (0, self.no_row("D1-review-2.md")))
        self.assertEqual(out.splitlines()[0], "# Perf report")
        self.assertNotIn("warning", out)

    def no_claims(self, state, dish="D1"):
        return f"brigade: warning: reports/{dish}.md has no Claims check line; {dish} is {state}"


    def test_close_warns_about_the_report_of_a_passed_item_that_has_no_claims_check_line(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "# D1\n\nStatus: done\n")
        self.record("abc", "pass")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_claims("passed")))






    def test_close_does_not_warn_about_a_report_a_row_names(self):
        self.fired_bug_fix()
        self.aged("D1-review-1.md", 2099)
        self.aged("D1-review-1-panel-2.md", 2099)
        self.record("abc", "send-back", "--report", "D1-review-1.md")
        self.record("abc", "pass", "--member", "--report", "D1-review-1-panel-2.md", verifier="grok/grok-4.7")
        self.assertEqual(self.close_run("--dry-run")[2], "")

    def test_close_matches_a_row_that_names_no_report_to_the_reports_written_before_it(self):
        self.fired_bug_fix()
        self.aged("D1-review.md", 2020)
        self.aged("D1-review-bunny.md", 2020)
        self.assertEqual(self.close_run("--dry-run")[2],
                         "\n".join([self.no_row("D1-review-bunny.md"), self.no_row("D1-review.md")]))
        self.record("abc", "send-back")
        self.assertEqual(self.close_run("--dry-run")[2], "")
        self.aged("D1-review-2.md", 2099)
        self.assertEqual(self.close_run("--dry-run")[2], self.no_row("D1-review-2.md"))

    def test_a_row_that_names_a_report_matches_no_other_report(self):
        self.fired_bug_fix()
        self.aged("D1-review-1.md", 2020)
        self.aged("D1-review-2.md", 2020)
        self.aged("D7-review.md", 2020)
        self.record("abc", "send-back", "--report", "D1-review-2.md")
        self.assertEqual(self.close_run("--dry-run")[2],
                         "\n".join([self.no_row("D1-review-1.md"), self.no_row("D7-review.md", "D7")]))

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

    def test_close_warns_about_a_dangling_link_named_like_a_review_report_and_still_exits_0(self):
        self.fired_bug_fix()
        code, before, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.linked("D1-review-7.md", "gone.md")
        warning = ("brigade: warning: reports/D1-review-7.md is a symbolic link and no review row of D1 names it; "
                   "remove the link and leave its target as it is")
        self.assertEqual(self.close_run("--dry-run"), (0, before, warning))
        self.assertEqual(self.close_run(), (0, before, warning))

    def test_close_warns_about_a_review_report_replaced_by_a_dangling_link_while_close_classifies_it(self):
        self.fired_bug_fix()
        entry = self.review_file("D1-review-7.md")
        pending = self.linked("pending.md", "gone.md")
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        replaced = []

        def replacing(real):
            def hooked(path):
                result = real(path)
                if not replaced and path.name == entry.name and path.parent.name == "reports":
                    replaced.append(path)
                    os.replace(pending, entry)
                return result
            return hooked

        argv = ["--store", str(self.store), "--at", str(self.at), *_owner_words(self.at), "close", "--dry-run"]
        with mock.patch.object(Path, "lstat", replacing(Path.lstat)), \
                mock.patch.object(Path, "is_symlink", replacing(Path.is_symlink)), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            glob["run"](argv)
        self.assertEqual(len(replaced), 1)
        self.assertTrue(entry.is_symlink())
        self.assertFalse(entry.exists())
        self.assertEqual(stderr.getvalue().strip(), self.no_row("D1-review-7.md"))
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.a_link("D1-review-7.md")))


    def test_close_warns_about_a_live_link_and_a_directory_named_like_review_reports_and_following_its_advice_ends_each_warning(self):
        self.fired_bug_fix()
        target = Path(self.temporary.name) / "findings.md"
        target.write_text("findings\n")
        link = self.linked("D1-review-7.md", "findings.md")
        directory = self.at / "reports" / "D1-review-8.md"
        directory.mkdir()
        (directory / "notes.md").write_text("notes\n")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, "\n".join([
            self.a_link("D1-review-7.md"),
            self.not_a_file("D1-review-8.md", "is not a regular file", "move it out of reports/"),
        ])))
        link.unlink()
        directory.rename(self.at / "D1-review-8.md")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.assertEqual(target.read_text(), "findings\n")
        self.assertEqual((self.at / "D1-review-8.md" / "notes.md").read_text(), "notes\n")


    def test_close_does_not_warn_about_a_dangling_link_a_row_names(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md")
        self.record("abc", "send-back", "--report", "D1-review-1.md")
        (self.at / "reports" / "D1-review-1.md").unlink()
        self.linked("D1-review-1.md", "gone.md")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))

    def test_a_row_that_names_no_report_accounts_for_no_link_to_a_file_written_before_it(self):
        self.fired_bug_fix()
        target = Path(self.temporary.name) / "findings.md"
        target.write_text("findings\n")
        stamp = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(target, (stamp, stamp))
        self.linked("D1-review-1.md", "findings.md")
        self.record("abc", "send-back")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.a_link("D1-review-1.md")))

    def test_close_advice_for_every_entry_under_a_linked_reports_leaves_the_files_outside_the_store_in_place(self):
        self.fired_bug_fix()
        outside = Path(self.temporary.name) / "outside" / "reports"
        outside.mkdir(parents=True)
        (outside / "D1-review-5.md").write_text("findings\n")
        (outside / "D1-review-6.md").symlink_to(outside / "D1-review-5.md")
        (outside / "D1-review-7.md").mkdir()
        before = sorted(path.name for path in outside.iterdir())
        reports = self.at / "reports"
        reports.symlink_to(outside)
        self.assertEqual(self.close_run("--dry-run")[::2], (0, "\n".join(
            self.under_a_linked_reports(f"D1-review-{number}.md") for number in (5, 6, 7))))
        reports.unlink()
        reports.mkdir()
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.assertEqual(sorted(path.name for path in outside.iterdir()), before)
        self.assertEqual((outside / "D1-review-5.md").read_text(), "findings\n")

    def test_close_ignores_a_file_that_is_not_a_review_report(self):
        self.fired_bug_fix()
        for name in ("D1.md", "notes.md", "D1-review.txt", "D1-reviewed.md", "xD1-review.md"):
            self.aged(name, 2099)
        self.assertEqual(self.close_run("--dry-run")[2], "")

    def test_report_lists_only_what_changed_since_the_last_report(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Startup is slow")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Splash hangs")
        self.brigade("fire", "--tickets", "T1", "--station", "perf-issue", "--summary", "Cut cold start time")
        self.brigade("86", "add", "--question", "Drop Windows 7 support?", "--options", "yes, no", "--default", "no")
        first = self.brigade("close")
        self.assertIn("## In progress\n\n- fix D1 (T1): Cut cold start time\n", first)
        self.assertIn("## New tickets, not started\n\n- fix T2: Splash hangs\n", first)
        self.assertIn("- Q1: Drop Windows 7 support? Options: yes, no. Default if no answer: no.", first)
        self.brigade("pass", "record", "D1", "--pr", "https://github.com/o/r/pull/7", "--sha", "abc", "--verdict", "pass",
                     "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.brigade("86", "answer", "Q1", "--answer", "no")
        second = self.brigade("close")
        self.assertIn("## Merged\n\n- fix D1 (T1): Cut cold start time https://github.com/o/r/pull/7", second)
        self.assertNotIn("In progress", second)
        self.assertNotIn("Passed review", second)
        self.assertNotIn("Decisions for you", second)
        self.assertIn("Nothing new.", self.brigade("close"))
        self.assertEqual(len(list((self.at / "closeouts").glob("*.md"))), 3)

    def test_a_dish_that_moves_through_every_state_in_one_report_appears_once(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "UI", "--source", "user")
        self.brigade("ticket", "set", "T2", "--state", "dropped")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", "grok/grok-4.7", "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.brigade("close"), "\n".join([
            "# Perf report", "",
            f"Since opening. merged: 1.", "",
            "## Merged", "", "- fix D1 (T1): Fix s", "",
            "## Dropped", "", "- T2: UI",
        ]))

    def test_close_to_file_prints_the_written_path_and_advances_the_report(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "UI", "--source", "user")
        self.brigade("ticket", "set", "T2", "--state", "dropped")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", "grok/grok-4.7", "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        written = self.brigade("close", "--to-file")
        files = list((self.at / "closeouts").glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertEqual(written, str(files[0].resolve()))
        self.assertIn("## Merged\n\n- fix D1 (T1): Fix s", files[0].read_text())
        self.assertIn("Nothing new.", self.brigade("close"))

    def test_close_to_file_with_dry_run_writes_no_closeout(self):
        self.open()
        self.brigade("close", "--to-file", "--dry-run", ok=False)
        self.assertEqual(list((self.at / "closeouts").glob("*.md")), [])

    def test_status_help_names_the_thread_line_then_counts(self):
        env = os.environ | {"COLUMNS": "300"}
        result = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("status              the thread line first, then counts, then workers outside the executive admin's store, then a kinds line, a goals line, and an untagged line when each has something to count, then reports to, mode, and owner when present", result.stdout)
        self.assertNotIn("one line of counts", result.stdout)

    def test_status_and_walk_speak_plain_engineering_prose(self):
        self.open()
        self.brigade("set", "--thread", "thread-1", "--schedule", "report=s-1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("86", "add", "--question", "Ship it?", "--options", "yes, no", "--default", "no")
        self.assertEqual(self.brigade("status"),
                         "thread thread-1\nreporting: milestones, no landing contract, waiting tickets: 1, decisions for you: 1\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nkinds: fix 1 waiting\nowner thread-1@1")
        self.assertEqual(self.brigade("walk"), "\n".join([
            f"{_shown_root(self.project)}: no landing contract",
            "  Perf (reports milestones): waiting tickets: 1, decisions for you: 1",
            "    thread thread-1",
            "    Q1: Ship it?",
        ]))
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["schedules"], {"report": "s-1"})
        for word in ("86", "plate", "heard", "chef", "fire", "pass "):
            self.assertNotIn(word, self.brigade("status").lower())

    def test_status_without_a_contract_ignores_a_stored_landing_field(self):
        self.open()
        self.assertNotIn("landing", json.loads((self.at / "restaurant.json").read_text()))
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal")
        meta = json.loads((self.at / "restaurant.json").read_text())
        meta["landing"] = "merge"
        (self.at / "restaurant.json").write_text(json.dumps(meta, indent=2) + "\n")
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal")
        walked = self.brigade("walk")
        self.assertEqual(walked.splitlines()[0], f"{_shown_root(self.project)}: no landing contract")
        self.assertNotIn("lands by", walked)


    def test_walk_prints_a_home_directory_as_a_tilde(self):
        from unittest import mock
        project = Path.home() / "brigade-no-such-contract-d57"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            directory = self.store / "brigade-no-such-contract-d57" / "docs"
            self.assertEqual(self.brigade("open", "--project-root", str(project), "--name", "Docs"), f"opened {directory}")
            self.assertEqual(self.brigade("walk"), "\n".join([
                "~/brigade-no-such-contract-d57: no landing contract",
                "  Docs (reports milestones): nothing on record",
                "    thread not recorded",
            ]))

    def test_status_and_walk_follow_a_landing_mode_change(self):
        from unittest import mock
        self.git_project()
        remote = Path(self.temporary.name) / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=self.project, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=self.project, check=True)
        self.land("init", "--trunk", "main", "--mode", "human")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.land("mode", "merge")
            self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, lands by merge\nworkers: 0 of 2 running, 2 idle, auto-start: normal")
            header = self.brigade("walk").splitlines()[0]
            self.assertEqual(header, f"{_shown_root(self.project)}: {self.land('status')}")
            self.assertTrue(header.startswith(f"{_shown_root(self.project)}: merge mode onto "))

    def test_walk_groups_repositories_and_repo_selects_one(self):
        from unittest import mock
        other = Path(self.temporary.name) / "other-app"
        other.mkdir()
        self.init_landing("--cap", "4")
        run = lambda *command: subprocess.run(command, cwd=other, capture_output=True, text=True, check=True)
        run("git", "init", "-q", "-b", "main")
        (other / "a.txt").write_text("a\n")
        run("git", "add", "-A")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.land_repo(other, "init", "--trunk", "lane", "--mode", "local", "--base", "main", "--cap", "4")
        docs = self.store / "bridge-kit" / "docs"
        engine = self.store / "bridge-kit" / "engine"
        core = self.store / "other-app" / "core"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.assertEqual(self.brigade("walk"), f"no restaurants under {self.store}")
            self.run_at(docs, "open", "--project-root", str(self.project), "--name", "Docs")
            self.run_at(engine, "open", "--project-root", str(self.project), "--name", "Engine")
            self.run_at(core, "open", "--project-root", str(other), "--name", "Core")
            self.run_at(docs, "set", "--thread", "thread-docs")
            self.run_at(engine, "set", "--thread", "thread-engine")
            self.run_at(core, "set", "--thread", "thread-core")
            self.run_at(docs, "ticket", "add", "--kind", "fix", "--summary", "Write the guide")
            self.assertEqual(self.run_at(docs, "fire", "--tickets", "T1", "--station", "bug-fix",
                                         "--summary", "Write the guide", "--paths", "a.txt"),
                             "D1 (lease L1 held by docs/D1)")
            self.run_at(engine, "ticket", "add", "--kind", "fix", "--summary", "Blocked edit")
            refused = self.run_at(engine, "fire", "--tickets", "T1", "--station", "bug-fix",
                                  "--summary", "Blocked edit", "--paths", "a.txt", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by docs/D1", refused)
            self.assertEqual(self.land("lease", "claim", "--holder", "engine/D12", "--paths", "src"), "L2")
            self.assertEqual(self.land("lease", "claim", "--holder", "engine/D13", "--paths", "lib"), "L3")
            self.assertEqual(self.run_at(docs, "86", "add", "--question", "Ship the guide?",
                                         "--options", "yes, no", "--default", "no"), "Q1")
            app = _shown_root(self.project)
            other_root = _shown_root(other)
            expected = "\n".join([
                f"{app}: local mode onto refs/landing/lane. leases held: 3, changes in flight: 3 of 4.",
                "  Docs (reports milestones): in progress: 1, decisions for you: 1",
                "    thread thread-docs, leases L1 (D1)",
                "    Q1: Ship the guide?",
                "  Engine (reports milestones): waiting tickets: 1 (1 blocked)",
                "    thread thread-engine, leases L2 (D12), L3 (D13)",
                f"{other_root}: local mode onto refs/landing/lane. changes in flight: 0 of 4.",
                "  Core (reports milestones): nothing on record",
                "    thread thread-core",
            ])
            self.assertEqual(self.brigade("walk"), expected)
            self.assertEqual(self.brigade("walk", "--repo", str(self.project)), "\n".join(expected.splitlines()[:6]))
            self.assertEqual(self.brigade("walk", "--repo", str(other)), "\n".join(expected.splitlines()[6:]))
            missing = Path(self.temporary.name) / "empty-repo"
            missing.mkdir()
            self.assertEqual(self.brigade("walk", "--repo", str(missing)), f"no restaurants for {missing.resolve()}")

    def fire_one(self, station="perf-issue", timebox="60"):
        (self.at / "menu.md").write_text("# Menu: Perf\n\n## Purpose\n\nMake startup fast.\n\n## Budget\n\nsmall\n")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Startup is slow")
        return self.brigade("fire", "--tickets", "T1", "--station", station, "--summary", "Cut startup", "--branch", "perf/d1",
                            "--thread", "thread-9", "--timebox", timebox)



    def test_a_report_that_follows_the_brief_is_read_by_ticket_add_from_report(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")
        text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--paths", "src/boot.ts",
                            "--lease", "L4", "--base", "origin/main")
        line = next(line for line in text.splitlines() if line.startswith("- Write it to "))
        asked = "List each follow-up as one top-level list item under a `"
        heading = line.split(asked, 1)[1].split("`", 1)[0]
        self.assertTrue(line.endswith(f"{asked}{heading}` heading, or write `None.` there."), line)
        self.review_file("D1.md", f"# D1\n\nStatus: done\n\n{heading}\n\n- Warm start is still slow.\n")
        self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md"),
                         "T2 added from perf/reports/D1.md#1: Warm start is still slow.")
        self.review_file("D1.md", f"# D1\n\nStatus: done\n\n{heading}\n\nNone.\n")
        self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md"),
                         "reports/D1.md lists no follow-ups; nothing added\nnot filed, says no work is needed: None.")



    def test_watch_reports_running_finished_and_overdue_work(self):
        self.open()
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")
        self.fire_one(timebox="30")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 30m (thread thread-9)")
        log = self.at / "log.tsv"
        log.write_text(log.read_text().replace(f"{__import__('datetime').date.today().year}-", "2020-"))
        self.assertIn("D1: over its 30m timebox", self.brigade("watch"))
        (self.at / "reports").mkdir()
        (self.at / "reports/D1.md").write_text("done")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written, no report-back (thread thread-9)")
        self.assertEqual(self.brigade("dish", "D1", "--reported"), "D1 in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written 0m ago; review it even if the worker's run is still open (thread thread-9)")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in progress with no worker thread; launch a fresh worker")
        self.brigade("dish", "D1", "--thread", "thread-10")
        (self.at / "reports/D1.md").write_text("second attempt")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written, no report-back (thread thread-10)")


    def test_replacing_an_in_progress_worker_starts_a_new_attempt(self):
        self.open()
        self.fire_one(timebox="60")
        log = self.at / "log.tsv"
        log.write_text(log.read_text().replace(f"{__import__('datetime').date.today().year}-", "2020-"))
        report = self.at / "reports" / "D1.md"
        report.parent.mkdir()
        report.write_text("partial from the overdue worker")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written, no report-back (thread thread-9)")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", "fresh-worker"), "D1 in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (thread fresh-worker)")
        report.write_text("partial from the fresh worker")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written, no report-back (thread fresh-worker)")
        self.brigade("dish", "D1", "--timebox", "90", "--task", "t-2")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written, no report-back (thread fresh-worker)")
        self.brigade("dish", "D1", "--state", "in-progress", "--thread", "fresh-worker")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written, no report-back (thread fresh-worker)")
        self.brigade("dish", "D1", "--reported")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written 0m ago; review it even if the worker's run is still open (thread fresh-worker)")
        self.brigade("dish", "D1", "--thread", "worker-3")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 90m (thread worker-3)")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-9", ok=False),
                         "brigade: thread thread-9 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "worker-3")
        report.write_text("partial from worker-3")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written, no report-back (thread worker-3)")

    def test_an_idle_replacement_with_an_earlier_report_reaches_the_nudge(self):
        self.open()
        self.fire_one(timebox="60")
        log = self.at / "log.tsv"
        log.write_text(log.read_text().replace(f"{__import__('datetime').date.today().year}-", "2020-"))
        report = self.at / "reports" / "D1.md"
        report.parent.mkdir()
        report.write_text("report from the replaced worker")
        self.brigade("dish", "D1", "--state", "in-progress", "--thread", "fresh-worker")
        line = self.brigade("watch").splitlines()[1]
        text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        section = text.split("## Liveness check", 1)[1].split("\n## ", 1)[0]
        step = next(row for row in section.splitlines() if row.startswith("5. "))
        trigger = step.split("`", 2)[1].split("Nm")[0].replace("D<n>", "D1")
        self.assertEqual((report.exists(), line), (True, "D1: running 0m of 60m (thread fresh-worker)"))
        self.assertTrue(line.startswith(trigger), (trigger, line))
        self.assertIn("no report for the current attempt", step)
        self.assertNotIn("no report file exists", step)


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
        firing = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--race-child", "fake-land", str(case),
                                   "--store", str(self.store), "--at", str(self.at),
                                   "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "src"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        try:
            _wait_for_path(case / "claiming")
            other = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
                                    "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "other"],
                                   capture_output=True, text=True, timeout=10)
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


    def test_commands_outside_a_restaurant_fail_with_the_fix(self):
        self.assertIn("run brigade.py open", self.brigade("status", ok=False))


    def test_open_records_reporting_and_defaults_to_milestones(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        self.assertEqual(meta["reporting"], "milestones")
        self.assertNotIn("landing", meta)
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal")
        self.assertIn("reports milestones", self.brigade("walk"))
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                                      "--reporting", "every-turn"), f"exists {self.at}")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["reporting"], "milestones")
        other = self.brigade("open", "--project-root", str(self.project), "--name", "Quiet",
                             "--reporting", "digest")
        quiet = self.store / "bridge-kit" / "quiet"
        self.assertEqual(other, "\n".join([
            f"opened {quiet}",
            f"sibling Perf ({self.at}), thread not recorded",
            "  purpose: not written yet",
            "  off the menu: not written yet",
        ]))
        self.assertEqual(json.loads((quiet / "restaurant.json").read_text())["reporting"], "digest")
        self.assertIn("reports digest", self.brigade("walk"))


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
        result = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
                                 *_owner_words(self.at), *args], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout





    def test_a_restaurant_file_without_reporting_reads_as_milestones(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        del meta["reporting"]
        (self.at / "restaurant.json").write_text(json.dumps(meta, indent=2) + "\n")
        self.assertNotIn("reporting", json.loads((self.at / "restaurant.json").read_text()))
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal")
        self.assertIn("reports milestones", self.brigade("walk"))
        self.assertNotIn("reporting", json.loads((self.at / "restaurant.json").read_text()))

    def log_rows(self):
        lines = (self.at / "log.tsv").read_text().splitlines()
        header = lines[0].split("\t")
        return [dict(zip(header, line.split("\t"))) for line in lines[1:] if line]

    def test_watch_flags_a_reported_run_still_open_past_ten_minutes(self):
        self.open()
        self.fire_one()
        report = self.at / "reports" / "D1.md"
        report.parent.mkdir()
        report.write_text("done")
        self.brigade("dish", "D1", "--reported")
        self.assertEqual(self.brigade("watch"),
                         "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written 0m ago; review it even if the worker's run is still open (thread thread-9)")
        start = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat(timespec="microseconds")
        log = self.at / "log.tsv"
        lines = log.read_text().splitlines()
        rewritten = [lines[0]]
        for line in lines[1:]:
            fields = line.split("\t")
            if fields[1:4] == ["dish", "D1", "in-progress"]:
                fields[0] = start
                line = "\t".join(fields)
            rewritten.append(line)
        log.write_text("\n".join(rewritten) + "\n")
        moment = datetime.now(timezone.utc) - timedelta(minutes=12, seconds=30)
        os.utime(report, (moment.timestamp(), moment.timestamp()))
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: reported 12m ago, not in review; read the thread (thread thread-9)")
        moment = datetime.now(timezone.utc) - timedelta(minutes=10, seconds=10)
        os.utime(report, (moment.timestamp(), moment.timestamp()))
        self.assertEqual(self.brigade("watch"),
                         "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: report written 10m ago; review it even if the worker's run is still open (thread thread-9)")

    def test_hang_records_one_open_run_per_attempt(self):
        self.open()
        self.fire_one()
        error = self.brigade("hang", "D1", "--provider", "grok/grok-4.7", "--minutes", "20", ok=False)
        self.assertIn("no report-back on this attempt", error)
        self.assertEqual([row for row in self.log_rows() if row["kind"] == "hang"], [])
        report = self.at / "reports" / "D1.md"
        report.parent.mkdir()
        report.write_text("done")
        self.brigade("dish", "D1", "--reported")
        self.assertEqual(self.brigade("hang", "D1", "--provider", "grok/grok-4.7", "--minutes", "20"),
                         "D1: grok/grok-4.7 open 20m")
        hangs = [row for row in self.log_rows() if row["kind"] == "hang"]
        self.assertEqual([(row["kind"], row["id"], row["state"], row["note"]) for row in hangs],
                         [("hang", "D1", "open", "grok/grok-4.7 20m")])
        self.assertEqual(self.brigade("hang", "D1", "--provider", "grok/grok-4.7", "--minutes", "5"),
                         "D1: hang already recorded")
        self.assertEqual(len([row for row in self.log_rows() if row["kind"] == "hang"]), 1)
        closed = self.brigade("close", "--dry-run")
        self.assertIn("Cut startup", closed)
        self.assertNotIn("grok/grok-4.7 20m", closed)
        self.brigade("dish", "D1", "--thread", "worker-3")
        error = self.brigade("hang", "D1", "--provider", "grok/grok-4.7", "--minutes", "20", ok=False)
        self.assertIn("no report-back", error)
        report.write_text("done again")
        self.brigade("dish", "D1", "--reported")
        self.brigade("hang", "D1", "--provider", "grok/grok-4.7", "--minutes", "20")
        self.assertEqual(len([row for row in self.log_rows() if row["kind"] == "hang"]), 2)

    def test_hang_records_a_run_left_open_by_an_earlier_attempt(self):
        self.open()
        self.fire_one()
        report = self.at / "reports" / "D1.md"
        report.parent.mkdir()
        report.write_text("done")
        self.brigade("dish", "D1", "--reported")
        self.brigade("dish", "D1", "--thread", "worker-3")
        error = self.brigade("hang", "D1", "--provider", "grok", "--minutes", "13", ok=False)
        self.assertEqual(error, "brigade: no report-back on this attempt; "
                                "for a run left open by an earlier attempt, pass --attempt 1")
        self.assertEqual(self.brigade("hang", "D1", "--attempt", "1", "--provider", "grok", "--minutes", "13"),
                         "D1: grok open 13m (attempt 1)")
        self.assertEqual(self.brigade("hang", "D1", "--attempt", "1", "--provider", "grok", "--minutes", "14"),
                         "D1: hang already recorded")
        self.assertEqual(self.brigade("hang", "D1", "--attempt", "3", "--provider", "grok", "--minutes", "1", ok=False),
                         "brigade: D1 has attempts 1 to 2")
        report.write_text("done again")
        self.brigade("dish", "D1", "--reported")
        self.assertEqual(self.brigade("hang", "D1", "--provider", "grok", "--minutes", "2"), "D1: grok open 2m")
        self.assertEqual(self.brigade("hang", "D1", "--attempt", "2", "--provider", "grok", "--minutes", "2"),
                         "D1: hang already recorded")
        self.assertEqual([row["note"] for row in self.log_rows() if row["kind"] == "hang"],
                         ["grok 13m (attempt 1)", "grok 2m"])

    def test_hang_without_attempt_counts_toward_the_attempt_it_was_recorded_in(self):
        self.open()
        self.fire_one()
        report = self.at / "reports" / "D1.md"
        report.parent.mkdir()
        report.write_text("done")
        self.brigade("dish", "D1", "--reported")
        self.brigade("hang", "D1", "--provider", "grok", "--minutes", "20")
        self.brigade("dish", "D1", "--thread", "worker-3")
        self.assertEqual(self.brigade("hang", "D1", "--attempt", "1", "--provider", "grok", "--minutes", "20"),
                         "D1: hang already recorded")





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


    def test_a_branch_rename_during_a_drop_leaves_no_lease(self):
        from unittest import mock
        self.init_landing("--cap", "3")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            dropped = []
            result = self.renamed(lambda: dropped or dropped.append(self.brigade("dish", "D1", "--state", "dropped")))
            self.assertEqual(dropped, ["D1 dropped; T1 waiting again"])
            self.assertEqual(result, "D1 in-progress")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            refused = self.brigade("dish", "D1", "--lease", "L2", "--paths", "src", ok=False)
            self.assertEqual(refused, "brigade: D1 is dropped; it holds no lease")
            self.assertEqual(self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src"), "L2")
            self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")







    def _spawn_race(self, mode, case, args):
        return subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--race-child", mode, str(case), *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))

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


    def test_an_unblocked_command_round_trips_through_bash(self):
        from unittest import mock
        marker = Path(self.temporary.name) / "shell-marker"
        summary = f"Fix O'Reilly; touch {marker}; # $(touch {marker}) & spaces"
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "quoted")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src/app.py")
            refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", summary,
                                   "--branch", "docs/gate", "--paths", "src/app.py", "--timebox", "45", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by engine/D1", refused)
            self.land("lease", "release", "L1")
            line = self.brigade("watch")
            ran = self.bash_unblocked(self.at, line)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        self.assertFalse(marker.exists(), ran.stdout + ran.stderr)
        row = self.table_row(self.at, "dishes.tsv", "D1")
        self.assertEqual(row["summary"], summary)
        self.assertEqual(row["paths"], "src/app.py,changes/docs%2Fgate.md")
        self.assertEqual(row["timebox"], "45")
        self.assertEqual(row["branch"], "docs/gate")

    def test_an_option_leading_summary_round_trips_through_bash(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                     "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "seed")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "seed")
        blocker = "D1"
        cases = (("-Werror", "T2", "D2"), ("--help", "T3", "D3"))
        for summary, ticket, dish in cases:
            self.brigade("ticket", "add", "--kind", "fix", f"--summary={summary}")
            refused = self.brigade("fire", "--tickets", ticket, "--station", "bug-fix",
                                   f"--summary={summary}", "--timebox", "45", ok=False)
            self.assertIn("nothing fired: 1 of 1 workers running", refused)
            self.brigade("dish", blocker, "--state", "sent-back")
            line = next(part for part in self.brigade("watch").splitlines() if part.startswith(f"{ticket}: unblocked"))
            ran = self.bash_unblocked(self.at, line)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            row = self.table_row(self.at, "dishes.tsv", dish)
            self.assertEqual(row["summary"], summary)
            self.assertEqual(row["timebox"], "45")
            self.assertEqual(row["station"], "bug-fix")
            blocker = dish

    def test_a_later_fire_does_not_keep_the_refused_attempts_fragment(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            directory = self.store / "bridge-kit" / "review-more"
            opened = self.brigade("open", "--project-root", str(self.project), "--name", "Review more")
            self.assertEqual(opened, f"opened {directory}")
            self.run_at(directory, "ticket", "add", "--kind", "fix", "--summary", "Blocked on src")
            self.run_at(directory, "ticket", "add", "--kind", "fix", "--summary", "Takes the first id")
            self.land("lease", "claim", "--holder", "engine/D9", "--paths", "src")
            refused = self.run_at(directory, "fire", "--tickets", "T1", "--station", "bug-fix",
                                  "--summary", "Blocked on src", "--paths", "src", "--timebox", "30", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by engine/D9", refused)
            note = self.blocked_note(directory, "T1")
            self.assertEqual(note["paths"], "src")
            self.assertNotIn("branch", note)
            started = self.run_at(directory, "fire", "--tickets", "T2", "--station", "bug-fix",
                                  "--summary", "Takes the first id", "--paths", "other")
            self.assertEqual(started, "D1 (lease L2 held by review-more/D1)")
            self.assertEqual(self.table_row(directory, "dishes.tsv", "D1")["paths"], "other,changes/review-more%2Fd1.md")
            self.land("lease", "release", "L1")
            line = self.run_at(directory, "watch")
            self.assertNotIn("waiting on", line)
            self.assertIn("--paths=src --timebox=30", line)
            self.assertNotIn("changes/review-more%2Fd1.md", line)
            ran = self.bash_unblocked(directory, line)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        row = self.table_row(directory, "dishes.tsv", "D2")
        self.assertEqual(row["paths"], "src,changes/review-more%2Fd2.md")
        self.assertEqual(row["branch"], "")
        self.assertEqual(row["timebox"], "30")
        self.assertEqual(row["summary"], "Blocked on src")

    def test_a_refused_fire_keeps_an_explicit_branch(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            directory = self.store / "bridge-kit" / "review"
            opened = self.brigade("open", "--project-root", str(self.project), "--name", "Review")
            self.assertEqual(opened, f"opened {directory}")
            self.run_at(directory, "ticket", "add", "--kind", "fix", "--summary", "Explicit branch")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            refused = self.run_at(directory, "fire", "--tickets", "T1", "--station", "bug-fix",
                                  "--summary", "Explicit branch", "--branch", "docs/a%b/c",
                                  "--paths", "src,changes/docs%2Fa%25b%2Fc.md", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by engine/D1", refused)
            note = self.blocked_note(directory, "T1")
            self.assertEqual(note["paths"], "src,changes/docs%2Fa%25b%2Fc.md")
            self.assertEqual(note.get("branch"), "docs/a%b/c")
            self.land("lease", "release", "L1")
            line = self.run_at(directory, "watch")
            self.assertIn("--branch", line)
            self.assertIn("docs/a%b/c", line)
            ran = self.bash_unblocked(directory, line)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        row = self.table_row(directory, "dishes.tsv", "D1")
        self.assertEqual(row["branch"], "docs/a%b/c")
        self.assertEqual(row["paths"], "src,changes/docs%2Fa%25b%2Fc.md")
        self.assertEqual(row["summary"], "Explicit branch")
        self.assertNotIn("changes/review%2Fd1.md", row["paths"])

    def test_a_worker_refusal_prints_the_running_count(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "3")
        for number, summary in enumerate(("one", "two", "three"), start=1):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
            self.brigade("fire", "--tickets", f"T{number}", "--station", "bug-fix", "--summary", summary)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "four")
        self.brigade("set", "--workers", "1")
        refused = self.brigade("fire", "--tickets", "T4", "--station", "bug-fix", "--summary", "four", ok=False)
        self.assertIn("nothing fired: 3 of 1 workers running", refused)
        self.assertIn("T4: waiting for a worker (3 of 1 running)", self.brigade("watch"))

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
        refused = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--race-child", "fake-land", str(case),
                                  "--store", str(self.store), "--at", str(self.at),
                                  "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "src"],
                                 capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        watched = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--race-child", "fake-land", str(case),
                                  "--store", str(self.store), "--at", str(self.at), "watch"],
                                 capture_output=True, text=True)
        self.assertEqual(watched.returncode, 0, watched.stderr)
        self.assertEqual(watched.stdout.strip(),
                         "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: waiting on the landing queue (queue database is locked)")

    def test_a_repository_cap_refusal_prints_the_repository_line(self):
        from unittest import mock
        self.init_landing("--cap", "1")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Room")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "other.txt")
            refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Room",
                                   "--paths", "src", ok=False)
            self.assertIn("nothing fired: repository at its cap: 1 of 1 changes in flight", refused)
            self.assertIn("blocked: repository", self.brigade("ticket", "list"))
            self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: waiting for room in the repository (1 of 1 changes in flight)")







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

    def test_watch_renews_a_live_lease_and_records_activity(self):
        self.started()
        self.land("lease", "renew", "L1", "--ttl-hours", "0.01")
        before = json.loads((self.at / "restaurant.json").read_text())["lastActivityAt"]
        self.assertLess(self.lease_row(1)["expires"], (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (no worker recorded)")
        self.assertGreater(self.lease_row(1)["expires"], (datetime.now(timezone.utc) + timedelta(hours=5.9)).isoformat())
        self.assertGreater(json.loads((self.at / "restaurant.json").read_text())["lastActivityAt"], before)

    def test_an_expired_lease_stays_expired_until_an_explicit_renew(self):
        self.started()
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        expired = self.lease_row(1)["expires"]
        self.assertEqual(self.brigade("watch").splitlines(),
                         ["workers: 1 of 2 running, 1 idle, auto-start: normal",
                          "D1: running 0m of 60m (no worker recorded)",
                          "D1: lease L1 expired; stop its worker, then run lease renew L1"])
        self.assertEqual(self.lease_row(1)["expires"], expired)
        self.assertEqual(self.land("lease", "renew", "L1"), "L1 renewed")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (no worker recorded)")

    def test_re_admitting_an_expired_lease_names_the_holder_that_claimed_its_paths(self):
        self.started()
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.assertEqual(self.land("lease", "claim", "--holder", "engine/D1", "--paths", "README.md"), "L2")
        self.assertIn("D1: lease L1 expired; stop its worker, then run lease renew L1", self.brigade("watch"))
        self.assertEqual(self.land("lease", "renew", "L1", ok=False),
                         "land: L1 expired and L2 held by engine/D1 now covers README.md; claim again after it is released")

    def test_a_released_or_unreadable_lease_gets_its_own_line(self):
        self.started()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc1234")
        self.land("lease", "release", "L1")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: lease L1 was released; claim again before submitting")
        before = (self.at / "restaurant.json").read_text()
        (Path(self.temporary.name) / "state").rename(Path(self.temporary.name) / "moved")
        self.assertEqual(self.brigade("watch"),
                         f"workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: could not renew L1: {self.project} has no landing contract; run land.py init in it")
        self.assertEqual((self.at / "restaurant.json").read_text(), before)

    def test_review_passed_parked_and_sent_back_items_each_get_a_watch_line_and_renew(self):
        self.started()
        verdict = ("pass", "record", "D1", "--sha", "abc1234", "--author", CODEX, "--verifier", CLAUDE, "--verdict")
        steps = [(("dish", "D1", "--state", "in-review", "--sha", "abc1234"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review"),
                 ((*verdict, "send-back"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: sent back"),
                 ((*verdict, "blocked"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: parked"),
                 ((*verdict, "pass"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: passed, not submitted")]
        for command, line in steps:
            self.brigade(*command)
            self.land("lease", "renew", "L1", "--ttl-hours", "0.01")
            self.assertEqual(self.brigade("watch"), line)
            self.assertGreater(self.lease_row(1)["expires"],
                               (datetime.now(timezone.utc) + timedelta(hours=5.9)).isoformat(), line)

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


    def test_86_add_round_budget_writes_the_three_option_decision(self):
        self.started()
        self.sent_back(2)
        self.brigade("dish", "D1", "--state", "in-progress")
        self.review_file("D1-review-3.md")
        self.send_back("a3", "--report", "D1-review-3.md")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["state"], row["dish"], row["question"], row["options"], row["default"], row["kind"],
                          row["answered"]),
                         ("open", "D1", f"{self.ROUND_QUESTION} Findings: reports/D1-review-3.md.",
                          "known limits, redesign, drop, keep parked", "keep parked", "round-budget", ""))
        table = (self.at / "86.tsv").read_bytes()
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        self.assertEqual((self.at / "86.tsv").read_bytes(), table)
        self.assertEqual(self.brigade("watch").splitlines(), [
            "workers: 0 of 2 running, 2 idle, auto-start: normal",
            self.PENDING,
            f"D1: open decision Q1: {self.ROUND_QUESTION} Findings: reports/D1-review-3.md.; "
            "launch no worker or verifier until 86 answer Q1",
        ])



    def test_keep_parked_leaves_the_round_decision_open_and_the_refusal_in_place(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q1")["question"], self.ROUND_QUESTION)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"),
                         "Q1 still open; D1 stays held until 86 answer Q1 --answer 'known limits' or redesign or drop")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("watch").splitlines()[1], self.PENDING)

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


    def test_watch_names_the_pending_decision_in_review_and_parked(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review"), "D1 in-review")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "a3", "--verdict", "blocked", "--author", CODEX,
                                      "--verifier", CLAUDE), "D1 blocked")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)

    def test_a_late_third_send_back_holds_an_item_in_progress(self):
        self.started()
        self.sent_back(2)
        self.brigade("dish", "D1", "--state", "in-progress", "--thread", "w3")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (thread w3)")
        self.assertEqual(self.send_back("a0", "--late"),
                         "D1: late send-back on record; D1 stays in-progress; 3 send-backs, decision pending")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        before = self.unchanged()
        self.assertEqual(self.brigade("dish", "D1", "--thread", "fresh-worker", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--task", "fresh-task", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.unchanged(before)
        self.assertEqual(self.brigade("dish", "D1", "--thread", "w3", "--reported"), "D1 in-progress")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", "a3"), "D1 in-review")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)


    FIRE_AGAIN = ("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fourth fix", "--thread", "fresh-worker")
    NO_FIRE = ("brigade: T1 is D1's ticket; D1 has 3 send-backs; no fix round until its decision is answered; "
               "run 86 add --dish D1 --round-budget")



    FIX_BRIEF = ("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "origin/main")







    def test_an_open_item_decision_replaces_an_over_timebox_line_and_renews_the_lease(self):
        self.started()
        self.brigade("dish", "D1", "--timebox", "30")
        self.backdate_attempt(45)
        self.land("lease", "renew", "L1", "--ttl-hours", "0.01")
        self.assertLess(self.lease_row(1)["expires"], (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        self.assertEqual(self.park_item(), "Q1")
        before = json.loads((self.at / "restaurant.json").read_text())["lastActivityAt"]
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
        self.assertGreater(self.lease_row(1)["expires"],
                           (datetime.now(timezone.utc) + timedelta(hours=5.9)).isoformat())
        self.assertGreater(json.loads((self.at / "restaurant.json").read_text())["lastActivityAt"], before)

    def test_an_open_item_decision_replaces_the_no_worker_line(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)

    def test_an_open_item_decision_replaces_the_in_review_line(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc1234")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)

    def test_an_expired_lease_under_a_held_item_follows_the_decision_line(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0")
        self.assertEqual(self.brigade("watch").splitlines(), [
            "workers: 1 of 2 running, 1 idle, auto-start: normal",
            HELD_LINE,
            "D1: lease L1 expired; stop its worker, then run lease renew L1",
        ])

    def test_answering_an_item_decision_restores_the_over_timebox_line(self):
        self.started()
        self.brigade("dish", "D1", "--timebox", "30")
        self.backdate_attempt(45)
        self.park_item()
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "moved"), "Q1 answered")
        self.assertRegex(
            self.brigade("watch"),
            r"^workers: 1 of 2 running, 1 idle, auto-start: normal\n"
            r"D1: over its 30m timebox at \d+m with no report; read its thread and decide \(no worker recorded\)$",
        )

    def test_answering_an_item_decision_restores_the_no_worker_line(self):
        self.started()
        self.park_item()
        self.brigade("dish", "D1", "--state", "sent-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "moved"), "Q1 answered")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in progress with no worker thread; launch a fresh worker")

    def test_answering_an_item_decision_restores_the_in_review_line(self):
        self.started()
        self.park_item()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc1234")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "moved"), "Q1 answered")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")


    def test_a_blank_dish_decision_does_not_hold_the_item(self):
        self.started()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc1234")
        self.assertEqual(self.brigade("86", "add", "--question", "Ship it?", "--options", "yes, no", "--default", "no"), "Q1")
        self.assertEqual(self.brigade("86", "list"), "Q1: Ship it? Options: yes, no. Default: no.")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")

    def test_a_second_86_add_for_a_held_item_prints_the_open_id_and_appends_nothing(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        table = (self.at / "86.tsv").read_text()
        decisions = [row for row in self.log_rows() if row["kind"] == "decision"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(len([line for line in table.splitlines() if line]) - 1, 1)
        other = "D1: cursor could not pass the seat's options: refused again. Move this coordinator to a provider that passes them?"
        self.assertEqual(self.park_item(other), "Q1")
        self.assertEqual((self.at / "86.tsv").read_text(), table)
        self.assertEqual([row for row in self.log_rows() if row["kind"] == "decision"], decisions)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)

    def test_keep_parked_leaves_the_item_decision_open_and_writes_nothing(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        before = self.store_bytes()
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        self.assertEqual(self.store_bytes(), before)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)

    def test_a_relayed_keep_parked_leaves_the_item_held(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.open_admin()
        before = (self.at / "86.tsv").read_bytes()
        self.admin("request", "--to", "perf", "answer perf Q1: keep parked")
        self.assertEqual(self.brigade("inbox", "take"), "A1: answer perf Q1: keep parked")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        self.assertEqual(self.brigade("inbox", "done", "A1"), "A1 done")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)
        self.assertEqual((self.at / "86.tsv").read_bytes(), before)




    def test_free_text_keeps_the_hold_and_writes_nothing(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        before = self.store_bytes()
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "I moved it"), STILL_OPEN)
        self.assertEqual(self.store_bytes(), before)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)




    def test_a_blank_dish_decision_still_closes_on_any_text(self):
        self.started()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc1234")
        self.assertEqual(self.brigade("86", "add", "--question", "Ship it?",
                                      "--options", "keep parked", "--default", "keep parked"), "Q1")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), "Q1 answered")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["state"], row["answer"]), ("answered", "keep parked"))
        self.assertEqual(self.brigade("86", "add", "--question", "Really?",
                                      "--options", "yes", "--default", "no"), "Q2")
        self.assertEqual(self.brigade("86", "answer", "Q2", "--answer", "whatever"), "Q2 answered")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q2")["answer"], "whatever")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")

    def test_a_legacy_item_row_with_no_closing_option_closes_on_any_answer(self):
        self.started()
        ordinary = self.brigade("watch")
        question = "Hold this item?"
        path = self.at / "86.tsv"
        header = path.read_text().splitlines()[0]
        row = "\t".join(["Q1", "2026-10-06T00:00:00+00:00", "open", "D1", question, "keep parked", "keep parked", ""])
        path.write_text(header + "\n" + row + "\n")
        self.assertEqual(
            self.brigade("watch"),
            f"workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: open decision Q1: {question}; launch no worker or verifier until 86 answer Q1",
        )
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), "Q1 answered")
        stored = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((stored["state"], stored["answer"]), ("answered", "keep parked"))
        self.assertEqual(self.brigade("watch"), ordinary)

    def test_a_passed_item_with_a_submitted_entry_says_mark_it_queued(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "fast\n"})
        self.passed(sha)
        self.assertEqual(self.submit(sha), "E1")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: E1 already submitted; mark it queued")

    def test_a_passed_item_whose_entry_landed_is_not_told_to_claim_again(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "fast\n"})
        self.passed(sha)
        self.submit(sha)
        self.land("land")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: E1 already submitted; mark it queued")


    def test_an_entry_another_run_landed_says_mark_it_merged(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "fast\n"})
        self.passed(sha)
        self.submit(sha)
        self.brigade("dish", "D1", "--state", "queued")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: E1 queued")
        self.land("land")
        landed = subprocess.run(["git", "rev-parse", "refs/landing/lane"], cwd=self.project, capture_output=True,
                                text=True, check=True).stdout.strip()
        self.assertEqual(self.brigade("watch"), f"workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: landed as E1 ({landed[:12]}); mark it merged")

    def test_an_old_bounced_entry_is_ignored_for_the_current_sha(self):
        self.started("--check", "test ! -e BROKEN", paths="README.md,BROKEN")
        old = self.worker_commit("perf/d1", {"README.md": "fast\n", "BROKEN": "x\n"})
        self.passed(old)
        self.submit(old)
        self.brigade("dish", "D1", "--state", "queued")
        self.land("land")
        self.assertRegex(self.brigade("watch").splitlines()[1], r"^D1: E1 bounced: checks failed")
        self.brigade("dish", "D1", "--thread", "worker-1")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "")
        self.assertIn("D1: in progress with no worker thread; launch a fresh worker", self.brigade("watch"))
        self.assertEqual(self.brigade("dish", "D1", "--thread", "worker-1", ok=False),
                         "brigade: thread worker-1 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.brigade("dish", "D1", "--thread", "worker-2")
        new = self.worker_commit("perf/d1", {"BROKEN": None})
        self.passed(new)
        self.assertEqual(self.submit(new), "E2")
        self.brigade("dish", "D1", "--state", "queued")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: E2 queued")

    def test_an_old_bounce_at_a_sha_sharing_the_items_prefix_is_ignored(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "reviewed\n"})
        self.passed(sha)
        self.submit(sha)
        from contextlib import closing
        with closing(self.landing_db()) as db, db:
            db.execute("UPDATE entry SET sha = ?, state = 'bounced', note = 'old different SHA' WHERE id = 1",
                       (self.same_prefix(sha),))
            db.execute("UPDATE lease SET state = 'active' WHERE id = 1")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: passed, not submitted")

    def test_a_later_bounce_sharing_the_prefix_does_not_hide_the_queued_entry(self):
        self.started()
        sha = self.worker_commit("perf/d1", {"README.md": "reviewed\n"})
        self.passed(sha)
        self.assertEqual(self.submit(sha), "E1")
        self.brigade("dish", "D1", "--state", "queued")
        self.seed_entry(self.same_prefix(sha), "bounced", "other SHA")
        self.assertEqual(self.land("status", "--holder", "perf/D1").splitlines()[1],
                         f"E2 bounced (perf/D1, {sha[:12]}): other SHA")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: E1 queued")






    def mode_rows(self):
        return [line.split("\t")[1:] for line in (self.at / "log.tsv").read_text().splitlines()[1:]
                if line.split("\t")[1] == "mode"]






    def test_a_refused_fire_mode_full_keeps_the_reason_in_the_unblocked_command(self):
        self.open()
        self.brigade("set", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "a")
        self.assertIn("nothing fired: 1 of 1 workers running",
                      self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Fix the lock order",
                                   "--mode", "full", "--reason", "tickets name a lock", ok=False))
        self.brigade("dish", "D1", "--state", "blocked")
        line = next(line for line in self.brigade("watch").splitlines() if line.startswith("T2:"))
        self.assertEqual(line, "T2: unblocked; run fire --tickets=T2 --station=bug-fix '--summary=Fix the lock order' "
                               "--timebox=60 --mode=full '--reason=tickets name a lock'")
        result = self.bash_unblocked(self.at, line)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, "D2"), result.stderr)
        self.assertEqual([row[:4] for row in self.mode_rows()], [["mode", "D2", "full", "tickets name a lock"]])

    def test_close_lists_an_escalation_with_its_tickets(self):
        self.fired_bug_fix()
        self.brigade("close")
        self.brigade("dish", "D1", "--mode", "full", "--reason", "contested: two owners")
        text = self.brigade("close", "--dry-run")
        self.assertEqual(text.split("\n\n", 2)[2], "## Moved to full mode\n\n- D1 (T1): contested: two owners")


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














    def test_the_second_send_back_moves_the_item_to_full_once(self):
        self.coordinator()
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back")
        escalated = self.head("Playbook: playbooks/feature.md", "Mode: full", "Mode source: escalated: second send-back",
                              "Attempt: fix")
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], escalated)
        self.assertNotIn("Contested:", text)
        self.assertEqual([row[:4] for row in self.mode_rows()], [["mode", "D1", "full", "second send-back"]])
        self.brigade(*self.BRIEF)
        self.assertEqual(len(self.mode_rows()), 1)
        self.brigade("set", "--mode", "light")
        self.assertEqual(self.brigade(*self.BRIEF).split("\n\n", 1)[0], escalated)
        self.assertEqual(self.brigade("close", "--dry-run").split("## Moved to full mode\n\n", 1)[1].split("\n")[0],
                         "- D1 (T1): second send-back")








    def paused_brief(self, case):
        case.mkdir()
        return self._spawn_race("brief-pause", case, ["--store", str(self.store), "--at", str(self.at),
                                                      *_owner_words(self.at), *self.BRIEF])





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



    def test_a_dishes_row_with_fourteen_fields_or_eighteen_is_malformed_and_one_with_sixteen_is_read(self):
        self.open()
        self.old_item()
        row = (self.at / "dishes.tsv").read_text().splitlines()[1].split("\t")
        for fields in (row[:14], [*row, "", "", ""]):
            (self.at / "dishes.tsv").write_text(self.OLD_DISHES + "\t".join(fields) + "\n")
            self.assertEqual(self.brigade("watch", ok=False),
                             "brigade: bridge-kit/perf/dishes.tsv line 2 is malformed; fix or remove it")
        (self.at / "dishes.tsv").write_text(self.OLD_DISHES + "\t".join([*row, ""]) + "\n")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")









    def test_status_prints_the_workers_line_third_before_reports_to_mode_and_owner(self):
        self.open()
        self.assertEqual(self.brigade("status"),
                         "thread not recorded\nreporting: milestones, no landing contract\n"
                         "workers: 0 of 2 running, 2 idle, auto-start: normal")
        self.brigade("set", "--thread", "t1", "--reports-to", "admin-thread", "--mode", "light")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a")
        self.assertEqual(self.brigade("status").splitlines(), [
            "thread t1",
            "reporting: milestones, no landing contract, waiting tickets: 1",
            "workers: 0 of 2 running, 2 idle, auto-start: normal",
            "kinds: fix 1 waiting",
            "reports to admin-thread",
            "mode light",
            "owner t1@1",
        ])

    def test_set_autofire_records_each_level_and_status_prints_it_as_auto_start(self):
        self.open()
        for level in ("off", "urgent", "normal", "low"):
            self.assertEqual(json.loads(self.brigade("set", "--autofire", level))["autofire"], level)
            status = self.brigade("status")
            self.assertEqual(status.splitlines()[2], f"workers: 0 of 2 running, 2 idle, auto-start: {level}")
            self.assertNotIn("autofire", status)


    def test_a_missing_or_empty_autofire_key_reads_as_normal(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        self.assertNotIn("autofire", meta)
        line = "workers: 0 of 2 running, 2 idle, auto-start: normal"
        self.assertEqual(self.brigade("status").splitlines()[2], line)
        (self.at / "restaurant.json").write_text(json.dumps(meta | {"autofire": ""}, indent=2) + "\n")
        self.assertEqual(self.brigade("status").splitlines()[2], line)


    def test_status_and_watch_count_items_in_progress_and_in_review_as_running_workers(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "3")
        for summary in ("a", "b", "c"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "a")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b")
        self.brigade("dish", "D2", "--state", "in-review", "--sha", "abc1234")
        workers = "workers: 2 of 3 running, 1 idle, auto-start: normal"
        self.assertEqual(self.brigade("status"),
                         "thread not recorded\n"
                         "reporting: milestones, no landing contract, waiting tickets: 1, in progress: 1, in review: 1\n"
                         + workers + "\nkinds: fix 1 waiting, 2 in flight")
        self.assertEqual(self.brigade("watch"), workers + "\nD1: running 0m of 60m (no worker recorded)\nD2: in review")
        self.brigade("pass", "record", "D2", "--sha", "abc1234", "--author", CODEX, "--verifier", CLAUDE, "--verdict", "send-back")
        workers = "workers: 1 of 3 running, 2 idle, auto-start: normal"
        self.assertEqual(self.brigade("status").splitlines()[2], workers)
        self.assertEqual(self.brigade("watch"), workers + "\nD1: running 0m of 60m (no worker recorded)\nD2: sent back")

    def test_the_workers_line_prints_no_idle_worker_when_more_run_than_the_cap(self):
        self.open()
        for ticket in ("T1", "T2"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", ticket)
            self.brigade("fire", "--tickets", ticket, "--station", "bug-fix", "--summary", ticket)
        self.brigade("set", "--workers", "1")
        workers = "workers: 2 of 1 running, 0 idle, auto-start: normal"
        self.assertEqual(self.brigade("status").splitlines()[2], workers)
        self.assertEqual(self.brigade("watch").splitlines()[0], workers)

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

    def test_from_report_records_the_tracked_paths_each_real_follow_up_quotes(self):
        self.tracked_project(*TRACKED)
        self.fired_bug_fix()
        printed = self.follow_ups_in("## Follow-ups\n\n" + "".join(f"- {text}\n" for text, _ in REAL_FOLLOW_UPS))
        self.assertEqual(printed.splitlines(), [f"T{number} added from perf/reports/D1.md#{number - 1}: {text}"
                                                for number, (text, _) in enumerate(REAL_FOLLOW_UPS, 2)])
        self.assertEqual(self.paths_cells(), {"T1": "", **{f"T{number}": paths
                                                           for number, (_, paths) in enumerate(REAL_FOLLOW_UPS, 2)}})

    def test_from_report_records_the_directory_before_the_first_marked_component_of_a_pattern_that_matches(self):
        self.tracked_project(*TRACKED)
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n"
                           "- Reword `t3/**/*.md` and `tests/test_*.py`.\n"
                           "- Nothing matches `docs/*.rst`, `**` or `*.md` has no directory, and `src/*.py` is not tracked.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "t3,tests", "T3": ""})

    def test_from_report_records_the_paths_quoted_beside_a_double_backtick_code_span(self):
        self.tracked_project("src/a/one.py", "src/b/two.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n"
                           "- Replace ``with `x` `` in `src/a/one.py` and `src/b/two.py`\n"
                           "- Change ``rows[0]`` in `src/a/one.py`\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "src/a/one.py,src/b/two.py", "T3": "src/a/one.py"})

    def test_from_report_skips_absolute_dot_dot_repository_and_non_numeric_suffix_quotes_and_records_the_normalized_path_of_a_line_suffixed_or_directory_quote(self):
        self.tracked_project(*TRACKED)
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n- See `../README.md`, `/README.md`, `.`, `docs/../README.md`, "
                           "and `docs/guide.md:x`.\n- Fix `./docs//guide.md:7` and `docs/`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "", "T3": "docs,docs/guide.md"})

    def test_from_report_records_no_paths_when_the_project_is_not_a_git_repository(self):
        (self.project / "docs").mkdir()
        (self.project / "docs/guide.md").write_text("x\n")
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Fix `docs/guide.md`.\n"),
                         "T2 added from perf/reports/D1.md#1: Fix `docs/guide.md`.")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": ""})

    def test_from_report_records_a_quoted_path_in_a_repository_that_tracks_a_file_name_that_is_not_utf_8(self):
        try:
            with open(os.fsencode(self.project) + b"/\xff.md", "wb") as handle:
                handle.write(b"x\n")
        except OSError:
            self.skipTest("the file system refuses a file name that is not UTF-8")
        self.tracked_project("guide.md")
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Fix `guide.md`.\n"),
                         "T2 added from perf/reports/D1.md#1: Fix `guide.md`.")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "guide.md"})

    def test_from_report_dry_run_in_a_git_project_changes_no_store_file(self):
        self.tracked_project(*TRACKED)
        self.fired_bug_fix()
        self.review_file("D1.md", "## Follow-ups\n\n- Fix `docs/guide.md`.\n")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md", "--dry-run"),
                         "would add from perf/reports/D1.md#1: Fix `docs/guide.md`.")
        self.assertEqual(self.store_files(), before)

    def unrecorded_tickets(self):
        self.tracked_project(*TRACKED)
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `docs/guide.md:246` and `tests/test_install.py`")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Cover the `runId` wait")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Reword `README.md`", "--paths", "t3/runtime.md")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Drop `t3/scripts/roles.py`")
        self.brigade("ticket", "set", "T4", "--state", "dropped")
















    def test_from_report_records_the_tracked_file_for_each_quoted_file_name_with_no_directory_that_resolves_to_one_and_none_for_names_with_none_or_several(self):
        self.tracked_project("t3/added/x/scripts/tool.py", "skills/x/scripts/tool.py", "src/app/one.py", "lib/two.py",
                             "src/two.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n"
                           "- Fix `tool.py:7` and `one.py`.\n"
                           "- Fix `two.py` and `nope.py`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "src/app/one.py,t3/added/x/scripts/tool.py", "T3": ""})


    def test_from_report_records_nothing_for_a_file_name_only_one_nested_file_has_quoted_with_a_dot_slash_before_it_a_slash_after_it_or_spaces_or_tabs_around_it(self):
        self.tracked_project("src/app/tool.py", "tool.py:12")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n"
                           "- One `./tool.py`.\n"
                           "- Two `tool.py/`.\n"
                           "- Three `./tool.py:12`.\n"
                           "- Four ` tool.py `.\n"
                           "- Five `\ttool.py\t`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "", "T3": "", "T4": "", "T5": "", "T6": ""})


    def test_from_report_records_nothing_for_a_quote_that_is_a_tracked_file_named_as_one_nested_file_with_a_line_number_added(self):
        self.tracked_project("tool.py:12", "src/app/tool.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n- Fix `tool.py:12`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": ""})


    def test_from_report_records_nothing_for_a_quote_that_is_a_tracked_directory_named_as_one_nested_file_with_a_line_number_added(self):
        self.tracked_project("tool.py:12/notes.md", "src/app/tool.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n- Fix `tool.py:12`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": ""})

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

    def test_startable_lists_a_ticket_whose_paths_are_free_as_startable_with_its_summary(self):
        self.queue()
        self.filed("Fix a", "--paths", "a.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, startable: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
        ]))

    def test_startable_names_the_lease_and_holder_that_block_a_ticket(self):
        self.queue()
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
        self.filed("Fix the app", "--paths", "src/app.py")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, blocked: 1",
            "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src/app.py",
        ]))

    def test_startable_lists_a_ticket_that_records_no_paths_as_unknown(self):
        self.queue()
        self.filed("Fix something")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 1, unknown: 1",
            "T1 normal fix, 0m: unknown, no paths recorded",
        ]))

    def test_startable_lists_a_decision_ticket_as_a_decision_with_or_without_paths(self):
        self.queue()
        self.filed("Pick a name", "--decision", "--paths", "a.txt", "--priority", "urgent")
        self.filed("Pick a color", "--decision")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, decisions: 2",
            "T1 urgent fix, 0m: decision for the owner; a.txt",
            "T2 normal fix, 0m: decision for the owner",
        ]))

    def test_startable_lists_free_tickets_below_the_auto_start_level_as_below_it(self):
        self.queue()
        self.brigade("set", "--autofire", "urgent")
        self.filed("Sync", "--paths", "a.txt", "--priority", "urgent")
        self.filed("Fix b", "--paths", "b.txt")
        self.filed("Tidy c", "--paths", "c.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: urgent",
            "waiting tickets: 3, startable: 1, below auto-start: 2",
            "T1 urgent fix, 0m: startable; a.txt",
            "  Sync",
            "T2 normal fix, 0m: below auto-start; b.txt",
            "T3 low fix, 0m: below auto-start; c.txt",
        ]))

    def test_startable_lists_no_ticket_as_startable_at_level_off(self):
        self.queue()
        self.brigade("set", "--autofire", "off")
        self.filed("Sync", "--paths", "a.txt", "--priority", "urgent")
        self.filed("Tidy c", "--paths", "c.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: off",
            "waiting tickets: 2, below auto-start: 2",
            "T1 urgent fix, 0m: below auto-start; a.txt",
            "T2 low fix, 0m: below auto-start; c.txt",
        ]))

    def test_startable_lists_a_low_ticket_on_a_startable_ticket_file_or_under_its_directory_as_riding_with_it(self):
        self.queue()
        self.filed("Fix the guide and the scripts", "--paths", "docs/guide.md,scripts")
        self.filed("Tidy the guide", "--paths", "docs/guide.md", "--priority", "low")
        self.filed("Tidy the installer", "--paths", "scripts/install.py", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, riding: 2",
            "T1 normal fix, 0m: startable; docs/guide.md,scripts",
            "  Fix the guide and the scripts",
            "T2 low fix, 0m: rides with T1; docs/guide.md",
            "  Tidy the guide",
            "T3 low fix, 0m: rides with T1; scripts/install.py",
            "  Tidy the installer",
        ]))

    def test_startable_blocks_a_second_normal_ticket_that_shares_a_path_with_a_startable_one(self):
        self.queue()
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Fix a and b", "--paths", "a.txt,b.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 normal fix, 0m: blocked, starts after T1; a.txt,b.txt",
        ]))

    def test_startable_blocks_a_low_ticket_that_shares_paths_with_two_startable_tickets_and_names_both(self):
        self.queue("--workers", "3")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Fix b", "--paths", "b.txt")
        self.filed("Tidy both", "--paths", "a.txt,b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 3 running, 3 idle, auto-start: normal",
            "waiting tickets: 3, startable: 2, blocked: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 normal fix, 0m: startable; b.txt",
            "  Fix b",
            "T3 low fix, 0m: blocked, starts after T1, T2; a.txt,b.txt",
        ]))

    def test_startable_blocks_a_low_ticket_with_one_path_on_a_startable_ticket_file_and_one_path_outside_its_paths(self):
        self.queue()
        self.filed("Fix the guide", "--paths", "docs/guide.md")
        self.filed("Tidy the docs", "--paths", "docs/guide.md,docs/cli.md", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: startable; docs/guide.md",
            "  Fix the guide",
            "T2 low fix, 0m: blocked, starts after T1; docs/cli.md,docs/guide.md",
        ]))

    def test_startable_blocks_a_low_ticket_on_a_directory_above_a_startable_ticket_file_at_a_cap_of_one(self):
        self.queue("--workers", "1")
        self.filed("Fix one", "--paths", "src/a/one.py", "--priority", "urgent")
        self.filed("Fix two", "--paths", "src/b/two.py")
        self.filed("Tidy the source", "--paths", "src", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: normal",
            "waiting tickets: 3, startable: 1, blocked: 2",
            "T1 urgent fix, 0m: startable; src/a/one.py",
            "  Fix one",
            "T2 normal fix, 0m: blocked, waiting for an idle worker; src/b/two.py",
            "T3 low fix, 0m: blocked, starts after T1; src",
        ]))

    def test_startable_blocks_a_free_ticket_when_the_worker_cap_is_reached(self):
        self.queue("--workers", "1")
        self.filed("Running")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Running")
        self.filed("Fix b", "--paths", "b.txt")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 1 of 1 running, 0 idle, auto-start: normal",
            "waiting tickets: 1, blocked: 1",
            "T2 normal fix, 0m: blocked, waiting for an idle worker; b.txt",
        ]))

    HELD_BACK = "held back, low tickets leave 1 worker idle while an urgent or normal ticket waits"

    def test_startable_holds_back_a_free_low_ticket_from_the_last_idle_worker_while_a_normal_ticket_waits(self):
        self.queue()
        self.brigade("set", "--autofire", "low")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Tidy b", "--paths", "b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: low",
            "waiting tickets: 2, startable: 1, held back: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            f"T2 low fix, 0m: {self.HELD_BACK}; b.txt",
        ]))

    def test_startable_lists_a_free_low_ticket_as_startable_at_a_cap_of_one_when_no_urgent_or_normal_ticket_waits(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 1, startable: 1",
            "T1 low fix, 0m: startable; a.txt",
            "  Tidy a",
        ]))

    def test_startable_lists_two_free_low_tickets_as_startable_at_a_cap_of_two_when_no_urgent_or_normal_ticket_waits(self):
        self.queue()
        self.brigade("set", "--autofire", "low")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.filed("Tidy b", "--paths", "b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 2 running, 2 idle, auto-start: low",
            "waiting tickets: 2, startable: 2",
            "T1 low fix, 0m: startable; a.txt",
            "  Tidy a",
            "T2 low fix, 0m: startable; b.txt",
            "  Tidy b",
        ]))

    def test_startable_blocks_a_free_low_ticket_for_no_idle_worker_when_a_normal_ticket_takes_the_one_worker(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Tidy b", "--paths", "b.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T2 low fix, 0m: blocked, waiting for an idle worker; b.txt",
        ]))

    def test_startable_holds_back_a_free_low_ticket_while_a_normal_ticket_blocked_by_a_lease_waits(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
        self.filed("Fix the app", "--paths", "src/app.py")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, blocked: 1, held back: 1",
            "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src/app.py",
            f"T2 low fix, 0m: {self.HELD_BACK}; a.txt",
        ]))

    def test_startable_holds_back_a_free_low_ticket_while_an_urgent_ticket_with_no_paths_waits(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Fix something", "--priority", "urgent")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, unknown: 1, held back: 1",
            "T1 urgent fix, 0m: unknown, no paths recorded",
            f"T2 low fix, 0m: {self.HELD_BACK}; a.txt",
        ]))

    def test_startable_holds_back_a_free_low_ticket_while_a_normal_decision_ticket_waits(self):
        self.queue("--workers", "1")
        self.brigade("set", "--autofire", "low")
        self.filed("Pick a name", "--decision")
        self.filed("Tidy a", "--paths", "a.txt", "--priority", "low")
        self.assertEqual(self.brigade("startable"), "\n".join([
            "workers: 0 of 1 running, 1 idle, auto-start: low",
            "waiting tickets: 2, decisions: 1, held back: 1",
            "T1 normal fix, 0m: decision for the owner",
            f"T2 low fix, 0m: {self.HELD_BACK}; a.txt",
        ]))

    def test_startable_flags_a_low_ticket_filed_more_than_seven_days_ago_and_no_other(self):
        self.queue()
        self.filed("Old and low", "--paths", "a.txt", "--priority", "low")
        self.filed("Low", "--paths", "b.txt", "--priority", "low")
        self.filed("Old", "--paths", "c.txt")
        self.refile("T1", days=7, minutes=1)
        self.refile("T2", days=6, hours=23)
        self.refile("T3", days=9)
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, below auto-start: 2",
            "T3 normal fix, 9d: startable; c.txt",
            "  Old",
            "T1 low fix, 7d (over a week): below auto-start; a.txt",
            "T2 low fix, 6d: below auto-start; b.txt",
        ]))

    def test_startable_with_no_waiting_ticket_prints_the_workers_line_and_says_so(self):
        self.queue()
        self.filed("Done already")
        self.brigade("ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("startable"), self.NONE_RUNNING + "\nno waiting tickets")

    def test_startable_orders_tickets_by_priority_then_oldest_filed_then_the_number_in_the_id(self):
        self.open()
        stamp = (datetime.now(timezone.utc) - timedelta(hours=5, minutes=1)).isoformat()
        rows = [("T10", stamp, "", "ten"), ("T9", stamp, "", "nine"), ("T11", stamp, "low", "eleven"),
                ("T12", datetime.now(timezone.utc).isoformat(), "urgent", "twelve"),
                ("T13", (datetime.now(timezone.utc) - timedelta(days=2, minutes=1)).isoformat(), "", "thirteen")]
        (self.at / "rail.tsv").write_text("id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision\n" + "".join(
            f"{ident}\t{at}\twaiting\tuser\t\t\t{summary}\t{priority}\t\t\n" for ident, at, priority, summary in rows))
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 5, unknown: 5",
            "T12 urgent, 0m: unknown, no paths recorded",
            "T13 normal, 2d: unknown, no paths recorded",
            "T9 normal, 5h: unknown, no paths recorded",
            "T10 normal, 5h: unknown, no paths recorded",
            "T11 low, 5h: unknown, no paths recorded",
        ]))

    def test_startable_lists_rail_rows_written_before_the_paths_cell_as_unknown(self):
        self.open()
        self.old_rail((datetime.now(timezone.utc) - timedelta(days=3, minutes=1)).isoformat())
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, unknown: 3",
            "T3 urgent, 3d: unknown, no paths recorded",
            "T1 normal, 3d: unknown, no paths recorded",
            "T2 low, 3d: unknown, no paths recorded",
        ]))
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_startable_without_an_owner_changes_no_store_file_and_no_landing_row(self):
        self.queue()
        self.brigade("set", "--thread", "t1")
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
        self.filed("Fix the app", "--paths", "src/app.py")
        self.filed("Fix a", "--paths", "a.txt")
        self.filed("Fix something")
        store, landing = self.store_files(), self.landing_rows()
        self.assertEqual(self.brigade("startable", owner=False), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 3, startable: 1, blocked: 1, unknown: 1",
            "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src/app.py",
            "T2 normal fix, 0m: startable; a.txt",
            "  Fix a",
            "T3 normal fix, 0m: unknown, no paths recorded",
        ]))
        self.assertEqual((self.store_files(), self.landing_rows()), (store, landing))
        self.assertEqual(len(landing["lease"]), 1)

    def test_startable_agrees_with_lease_check_on_a_directory_lease_and_on_a_name_that_shares_its_prefix(self):
        self.queue()
        self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
        self.filed("Fix x", "--paths", "src/app/x.py")
        self.filed("Fix srcx", "--paths", "srcx")
        checks = [subprocess.run([sys.executable, str(ROOT / "t3/added/landing/scripts/land.py"), "--repo", str(self.project),
                                  "lease", "check", "--holder", holder, "--paths", paths],
                                 capture_output=True, text=True, env=self.land_env())
                  for holder, paths in (("perf/T1", "src/app/x.py"), ("perf/T2", "srcx"))]
        self.assertEqual([(check.returncode, check.stdout.strip()) for check in checks],
                         [(1, "L1 held by engine/D1 on src"), (0, "free")])
        self.assertEqual(self.brigade("startable"), "\n".join([
            self.NONE_RUNNING,
            "waiting tickets: 2, startable: 1, blocked: 1",
            "T1 normal fix, 0m: blocked, waiting on L1 (engine/D1); src/app/x.py",
            "T2 normal fix, 0m: startable; srcx",
            "  Fix srcx",
        ]))

    def test_startable_prints_one_line_for_a_ticket_the_landing_queue_refuses_in_a_project_that_is_not_a_git_repository(self):
        self.open()
        self.filed("Fix a", "--paths", "src/a.py")
        lines = self.brigade("startable").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[2].startswith("T1 normal fix, 0m: blocked, waiting on the landing queue ("), lines[2])



    def test_from_report_on_a_report_whose_only_follow_up_is_a_list_item_with_no_text_adds_nothing(self):
        self.fired_bug_fix()
        before = self.tables()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- \n"),
                         "reports/D1.md lists no follow-ups; nothing added\nnot filed, list item with no text")
        self.assertEqual(self.tables(), before)

    def test_from_report_prints_a_line_that_is_only_a_list_marker_as_a_list_item_with_no_text_and_adds_nothing(self):
        self.fired_bug_fix()
        before = self.tables()
        for marker in ("-", "*", "1."):
            with self.subTest(marker=marker):
                self.assertEqual(self.follow_ups_in(f"## Follow-ups\n\n{marker}\n"),
                                 "reports/D1.md lists no follow-ups; nothing added\nnot filed, list item with no text")
                self.assertEqual(self.tables(), before)

    def test_from_report_files_a_line_that_is_only_three_hyphens_as_a_follow_up(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n---\n"), "T2 added from perf/reports/D1.md#1: ---")

    def test_a_paragraph_above_a_list_item_with_no_text_is_filed_when_no_other_list_item_follows(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\nReword the guide.\n\n- \n").splitlines(), [
            "T2 added from perf/reports/D1.md#1: Reword the guide.",
            "not filed, list item with no text",
        ])

    def test_a_paragraph_above_a_list_item_with_no_text_and_then_a_list_item_introduces_the_list(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\nTwo things remain.\n\n- \n- Fix a.\n").splitlines(), [
            "T2 added from perf/reports/D1.md#1: Fix a.",
            "not filed, prose that introduces a list: Two things remain.",
            "not filed, list item with no text",
        ])

    def test_a_paragraph_below_a_list_item_with_no_text_that_follows_the_last_list_item_with_text_closes_the_section(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Fix a.\n\n- \n\nThat is all.\n").splitlines(), [
            "T2 added from perf/reports/D1.md#1: Fix a.",
            "not filed, list item with no text",
            "not filed, prose after the last list item: That is all.",
        ])

    def test_from_report_refuses_priority_nonempty_paths_and_decision(self):
        self.fired_bug_fix()
        before = self.store_files()
        for extra in (("--priority", "low"), ("--paths", "a.txt"), ("--decision",)):
            self.assertEqual(self.follow_ups_in(BULLETS, *extra, ok=False),
                             "brigade: --from-report takes no --priority, nonempty --paths, or --decision", extra)
        self.review_file("D1.md", BULLETS)
        self.assertEqual({name: data for name, data in self.store_files().items() if name != "reports/D1.md"}, before)

    @staticmethod
    def edit_block(number, path, old, new, fence="```"):
        return f"### Edit {number}\n\nFile: `{path}`\n\nOld text:\n\n{fence}\n{old}\n{fence}\n\nNew text:\n\n{fence}text\n{new}\n{fence}\n"

    def edits_report(self, *blocks, name="D1-review-1.md", after="## Edits verified\n\nApplied in a scratch copy.\n"):
        body = "Verdict: pass with edits\n\n## Blockers\n\nNone.\n\n## Edits\n\n" + "\n".join(blocks)
        return self.review_file(name, body + ("\n" + after if after else ""))

    def record_edits(self, sha, *extra, ok=True):
        return self.brigade("pass", "record", "D1", "--sha", sha, "--verdict", "pass-with-edits",
                            "--author", CODEX, "--verifier", CLAUDE, *extra, ok=ok)




    def test_close_prints_no_unrecorded_review_warning_for_the_report_a_pass_with_edits_row_names(self):
        self.fired_bug_fix()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_row("D1-review-1.md")))
        self.record_edits("abc", "--edits", "D1-review-1.md")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))




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
        result = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
                                 *_owner_words(self.at), "edits", "verify", "D1", "--from", reviewed, "--to", head],
                                capture_output=True, text=True)
        return result.returncode, result.stdout.strip(), result.stderr.strip()

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
        result = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
                                 *_owner_words(self.at), *args], capture_output=True, text=True)
        return result.returncode, result.stdout, result.stderr

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



























    def test_a_store_with_no_edits_table_runs_status_watch_startable_a_pass_and_close_and_gains_no_table(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        (self.at / "edits.tsv").unlink()
        self.assertEqual(self.brigade("status").splitlines()[0], "thread not recorded")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")
        self.assertEqual(self.brigade("startable"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nno waiting tickets")
        self.assertEqual(self.record_pass_at("abc"), "D1 passed")
        code, out, err = self.close_run()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[0], "# Perf report")
        self.assertFalse((self.at / "edits.tsv").exists())

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



    def test_ticket_add_from_report_refuses_kind_and_goal_and_files_no_ticket(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        (self.at / "reports").mkdir(exist_ok=True)
        (self.at / "reports" / "D1.md").write_text(BULLETS)
        before = self.store_files()
        refusal = "brigade: --from-report takes no --kind or --goal; each ticket's kind and goal follow from the report's item"
        for flags in (("--kind", "fix"), ("--goal", "G1"), ("--goal", ""), ("--kind", "polish", "--goal", "G1")):
            with self.subTest(flags=flags):
                self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md", *flags, ok=False), refusal)
                self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md", "--dry-run", *flags, ok=False), refusal)
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
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--race-child", "fake-land", str(case),
                                 "--store", str(self.store), "--at", str(self.at), "fire", *words],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

























    def test_fire_of_a_ticket_with_no_kind_is_blocked_by_a_full_worker_cap_with_the_words_it_had_before_kinds(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.rail_before_kinds("a", "b")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "feature", "--summary", "Do b", ok=False),
                         "brigade: nothing fired: 1 of 1 workers running")
        self.assertEqual(self.blocked_note(self.at, "T2"),
                         {"kind": "workers", "tickets": ["T2"], "station": "feature", "summary": "Do b", "paths": "", "timebox": 60})
        self.brigade("dish", "D1", "--state", "sent-back")
        line = "T2: unblocked; run fire --tickets=T2 --station=feature '--summary=Do b' --timebox=60"
        self.assertIn(line, self.brigade("watch").splitlines())
        ran = self.bash_unblocked(self.at, line)
        self.assertEqual((ran.returncode, ran.stderr.strip()),
                         (1, f"brigade: T2 has no kind and station feature gives none; pass --kind {self.KIND_CHOICE}"))


    def test_a_block_note_written_before_kinds_reprints_the_fire_line_it_printed_then(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "Fix the gate", "--kind", "fix")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        note = ('{"kind":"workers","tickets":["T2"],"station":"bug-fix","summary":"Fix the gate","paths":"","timebox":45,'
                '"branch":"docs/gate","reason":"wide change"}')
        with open(self.at / "log.tsv", "a") as log:
            log.write(f"2026-10-01T00:00:00+00:00\tticket\tT2\tblocked\t{note}\n")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.assertIn("T2: unblocked; run fire --tickets=T2 --station=bug-fix '--summary=Fix the gate' --branch=docs/gate "
                      "--timebox=45 --mode=full '--reason=wide change'", self.brigade("watch").splitlines())


    def test_a_blocked_fire_with_kind_and_ride_along_records_both_and_the_fire_line_watch_reprints_carries_them_and_runs(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.goal("activity view")
        self.goal("current with upstream")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        self.hand_edit("rail.tsv", "T2", kind="")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "polish", "--goal", "G2", "--priority", "low")
        self.assertEqual(self.brigade("fire", "--tickets", "T2,T3", "--ride-along", "T3", "--kind", "upkeep", "--station", "feature",
                                      "--summary", "Do b and c", ok=False), "brigade: nothing fired: 1 of 1 workers running")
        note = {"kind": "workers", "tickets": ["T2", "T3"], "station": "feature", "summary": "Do b and c", "paths": "", "timebox": 60,
                "work": "upkeep", "ride_along": ["T3"]}
        self.assertEqual((self.blocked_note(self.at, "T2"), self.blocked_note(self.at, "T3")), (note, note))
        self.brigade("dish", "D1", "--state", "sent-back")
        line = ("T2: unblocked; run fire --tickets=T2,T3 --station=feature '--summary=Do b and c' --timebox=60 --kind=upkeep "
                "--ride-along=T3")
        self.assertIn(line, self.brigade("watch").splitlines())
        ran = self.bash_unblocked(self.at, line)
        self.assertEqual((ran.returncode, ran.stdout.strip()), (0, "D2"), ran.stderr)
        self.assertEqual((self.label("dishes.tsv", "D2"), self.label("rail.tsv", "T2"), self.label("rail.tsv", "T3")),
                         (("upkeep", "G1"), ("upkeep", "G1"), ("polish", "G2")))



    def assert_goal_keeps_open(self, ident, held, advice):
        before = self.store_files()
        for state in ("done", "dropped"):
            with self.subTest(goal=ident, state=state):
                self.assertEqual(self.brigade("goal", "set", ident, "--state", state, "--evidence", "why", ok=False),
                                 f"brigade: {ident} still has work: {held}; {advice}; then run this again")
        self.assertEqual(self.store_files(), before)






    def test_from_report_files_a_ticket_under_the_goal_of_its_item_when_that_goal_is_done_and_goal_list_prints_that_goal(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "merged")
        self.follow_ups_in("## Follow-ups\n\n- Tidy the guide.\n")
        self.assertEqual(self.label("rail.tsv", "T2"), ("polish", "G1"))
        self.assertEqual(self.brigade("goal", "list").splitlines()[0],
                         "G1 done activity view: 1 waiting, 0 in flight, 1 merged, 0 send-backs")




















    def test_goal_list_in_a_store_with_no_goal_prints_that_and_the_unassigned_line(self):
        self.open()
        before = self.store_files()
        self.assertEqual(self.run_raw("goal", "list"), (0, f"no open or paused goals\n{self.NO_UNASSIGNED_WORK}\n", ""))
        self.assertEqual(self.run_raw("goal", "list", "--all"), (0, f"no goals\n{self.NO_UNASSIGNED_WORK}\n", ""))
        self.assertEqual(self.store_files(), before)

    def test_goal_list_prints_each_open_and_paused_goal_in_file_order_with_its_share_and_all_adds_each_done_and_dropped_goal_with_its_closed_cell(self):
        self.open()
        self.goal("activity view", "--share", "2")
        self.goal("old lint")
        self.goal("current with upstream")
        self.goal("windows port")
        self.brigade("goal", "set", "G2", "--state", "done", "--evidence", "shipped in #12")
        self.brigade("goal", "set", "G3", "--state", "paused")
        self.brigade("goal", "set", "G4", "--state", "dropped", "--evidence", "no users")
        closed = {ident: self.table_row(self.at, "goals.tsv", ident)["closed"] for ident in ("G2", "G4")}
        self.assertEqual(closed["G2"].split(" ", 1)[1], "shipped in #12")
        counts = "0 waiting, 0 in flight, 0 merged, 0 send-backs"
        detail = ["  purpose: One page of activity", "  done when: The page ships"]
        self.assertEqual(self.brigade("goal", "list").splitlines(),
                         [f"G1 open activity view: {counts}, share 2", *detail,
                          f"G3 paused current with upstream: {counts}", *detail,
                          self.NO_UNASSIGNED_WORK])
        self.assertEqual(self.brigade("goal", "list", "--all").splitlines(),
                         [f"G1 open activity view: {counts}, share 2", *detail,
                          f"G2 done old lint: {counts}", *detail, f"  closed: {closed['G2']}",
                          f"G3 paused current with upstream: {counts}", *detail,
                          f"G4 dropped windows port: {counts}", *detail, f"  closed: {closed['G4']}",
                          self.NO_UNASSIGNED_WORK])

    def test_goal_list_with_only_done_and_dropped_goals_prints_no_open_or_paused_goals(self):
        self.open()
        self.goal()
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "shipped")
        self.assertEqual(self.brigade("goal", "list"), f"no open or paused goals\n{self.NO_UNASSIGNED_WORK}")


    def test_a_goal_share_that_is_not_a_whole_number_of_1_or_more_reads_as_no_limit_with_a_warning_and_goal_set_share_rewrites_the_cell(self):
        self.open()
        self.goal()
        for share in ("lots", "0", "-3"):
            with self.subTest(share=share):
                self.hand_edit("goals.tsv", "G1", share=share)
                code, out, err = self.run_raw("goal", "list")
                self.assertEqual((code, err), (0, f"brigade: warning: G1 has share {share!r}, which is not a whole number of 1 or more; "
                                                  "it reads as no limit; run goal set G1 --share <n>\n"))
                self.assertEqual(out.splitlines()[0], "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs")
        self.assertEqual(self.brigade("goal", "set", "G1", "--share", "2"), "G1 share 2")
        code, out, err = self.run_raw("goal", "list")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[0], "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs, share 2")

    def test_a_goal_share_of_five_thousand_digits_is_read_and_printed(self):
        self.open()
        self.goal()
        self.hand_edit("goals.tsv", "G1", share="7" * 5000)
        code, out, err = self.run_raw("goal", "list")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.splitlines()[0].endswith(", share " + "7" * 5000))


    def test_goal_add_and_goal_set_in_a_store_with_a_generation_need_the_owner_and_goal_list_does_not(self):
        self.open()
        self.brigade("set", "--thread", "t1")
        refusal = "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status"
        self.assertEqual(self.goal(owner=False, ok=False), refusal)
        self.assertEqual(self.goal(), "G1")
        before = self.store_files()
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "paused", ok=False, owner=False), refusal)
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("goal", "list", owner=False).splitlines()[0],
                         "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs")



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


    def test_goal_list_prints_for_each_goal_the_counts_a_hand_join_of_the_three_tables_gives(self):
        self.worked_store()
        self.assertEqual(self.brigade("goal", "list").splitlines(), [
            # 2 waiting. T4 and T9 wait under G1. 1 in flight. D2 is in review under G1. 1 merged. D1.
            # 3 send-backs. Two on D1 and one on D2. The D2 row with member yes does not count.
            "G1 open activity view: 2 waiting, 1 in flight, 1 merged, 3 send-backs, share 2",
            "  purpose: One page of activity",
            "  done when: The page ships",
            # 0 waiting. T3 and T6 are under G2 and assigned. 1 in flight. D3 is sent back under G2. 0 merged.
            # 1 send-back. One on D3. Its pass-with-edits row does not count. 1 riding along. T3, assigned to D2, a G1 item.
            "G2 paused current with upstream: 0 waiting, 1 in flight, 0 merged, 1 send-back, 1 riding along",
            "  purpose: One page of activity",
            "  done when: The page ships",
            # 2 waiting. T5 and T8. 1 in flight. D4. 1 merged. D5. 1 send-back. One on D5.
            "unassigned: 2 waiting, 1 in flight, 1 merged, 1 send-back",
        ])

    def test_status_prints_the_waiting_and_in_flight_counts_by_kind_and_by_goal_a_hand_join_gives_after_its_workers_line(self):
        self.worked_store()
        self.assertEqual(self.brigade("status").splitlines(), [
            "thread not recorded",
            "reporting: milestones, no landing contract, waiting tickets: 4, in progress: 1, in review: 1, sent back: 1, merged: 2",
            "workers: 2 of 9 running, 7 idle, auto-start: normal",
            # feature. T9 waits. fix. T4 waits and D2 is in flight. polish. T5 waits, and T3 is assigned so it is in no entry.
            # upkeep. D3 is in flight. study. D4 is in flight.
            "kinds: feature 1 waiting; fix 1 waiting, 1 in flight; polish 1 waiting; upkeep 1 in flight; study 1 in flight",
            # G1. T4 and T9 wait and D2 is in flight. G2. D3 is in flight. unassigned. T5 and T8 wait and D4 is in flight.
            "goals: G1 2 waiting, 1 in flight; G2 paused, 1 in flight; unassigned 2 waiting, 1 in flight",
            # T8 waits with no kind. The waiting counts by kind and this one sum to the 4 waiting tickets.
            "untagged: 1",
        ])


    def test_status_prints_no_kinds_line_no_goals_line_and_no_untagged_line_when_no_ticket_waits_and_no_item_is_in_flight(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "set", "T1", "--state", "dropped")
        self.assertEqual(self.brigade("status").splitlines(), [
            "thread not recorded", "reporting: milestones, no landing contract",
            "workers: 0 of 9 running, 9 idle, auto-start: normal"])

    def test_status_prints_the_state_of_a_done_goal_that_has_a_waiting_ticket(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a")
        self.hand_edit("dishes.tsv", "D1", state="merged")
        self.hand_edit("rail.tsv", "T1", state="done")
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "merged")
        self.follow_ups_in("## Follow-ups\n\n- Tidy the guide.\n")
        self.assertEqual(self.brigade("status").splitlines()[3:], ["kinds: polish 1 waiting", "goals: G1 done, 1 waiting"])

    def test_status_prints_the_unassigned_entry_alone_when_the_store_has_a_goal_and_no_work_under_it(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.assertEqual(self.brigade("status").splitlines()[3:], ["kinds: fix 1 waiting", "goals: unassigned 1 waiting"])

    def test_startable_gives_the_same_standings_in_the_same_order_with_kinds_and_goals_as_without_them(self):
        self.queue("--workers", "3")
        self.goal("activity view", "--share", "1")
        self.goal("current with upstream")
        self.filed("Fix a", "--paths", "a.txt", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "Add b", "--kind", "feature", "--paths", "b.txt", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "Sync c", "--kind", "upkeep", "--paths", "c.txt", "--goal", "G2",
                     "--priority", "urgent")
        self.brigade("ticket", "add", "--summary", "Tidy d", "--kind", "polish", "--paths", "d.txt", "--priority", "low")
        self.brigade("ticket", "add", "--summary", "Look at e", "--kind", "study")
        self.brigade("ticket", "add", "--summary", "Add f", "--kind", "feature", "--paths", "a.txt", "--goal", "G2")
        self.brigade("goal", "set", "G2", "--state", "paused")
        lines = ["workers: 0 of 3 running, 3 idle, auto-start: normal",
                 "waiting tickets: 6, startable: 3, blocked: 1, unknown: 1, below auto-start: 1",
                 "T3 urgent{}, 0m: startable; c.txt", "  Sync c",
                 "T1 normal{}, 0m: startable; a.txt", "  Fix a",
                 "T2 normal{}, 0m: startable; b.txt", "  Add b",
                 "T5 normal{}, 0m: unknown, no paths recorded",
                 "T6 normal{}, 0m: blocked, starts after T1; a.txt",
                 "T4 low{}, 0m: below auto-start; d.txt"]
        words = iter((" upkeep G2", " fix G1", " feature G1", " study", " feature G2", " polish"))
        self.assertEqual(self.brigade("startable").splitlines(),
                         [line.format(next(words)) if "{}" in line else line for line in lines])
        for number in range(1, 7):
            self.hand_edit("rail.tsv", f"T{number}", kind="", goal="")
        self.assertEqual(self.brigade("startable").splitlines(), [line.format("") for line in lines])

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


    def test_fire_of_a_ticket_whose_goal_cell_is_a_padded_id_warns_starts_an_unassigned_item_and_leaves_the_cell_as_it_was(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.hand_edit("rail.tsv", "T1", goal=" G1 ")
        names = f"brigade: warning: T1 names goal ' G1 ', {self.NO_GOAL_ROW}; "
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"),
                         (0, "D1\n", names + "run ticket set T1 --goal G<n> or --no-goal\n"))
        self.assertEqual(self.raw_cells("dishes.tsv", "D1", "kind", "goal"), (b"fix", b""))
        self.assertEqual(self.raw_cells("rail.tsv", "T1", "state", "kind", "goal"), (b"assigned", b"fix", b" G1 "))
        code, out, err = self.run_raw("status")
        self.assertEqual((code, err), (0, names + f"{self.NOT_WHILE} T1 is assigned; correct it in rail.tsv by hand\n"))
        self.assertEqual(out.splitlines()[3:], ["kinds: fix 1 in flight", "goals: unassigned 1 in flight"])


    def test_a_rewrite_of_its_table_leaves_each_kept_cell_byte_for_byte_and_the_warning_for_it_still_prints(self):
        self.two_goals()
        for summary in ("a", "b", "c", "d"):
            self.brigade("ticket", "add", "--summary", summary, "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "Do c")
        self.brigade("fire", "--tickets", "T4", "--station", "bug-fix", "--summary", "Do d")
        # Per table, the row whose cell is padded, and a command that rewrites the table to change another row.
        rewrites = {"rail.tsv": ("T1", lambda cell: ("ticket", "set", "T2", "--paths", f"docs/{cell}")),
                    "dishes.tsv": ("D1", lambda cell: ("dish", "D2", "--thread", f"thread-{cell}")),
                    "goals.tsv": ("G1", lambda cell: ("goal", "set", "G2", "--name", f"renamed {cell}"))}
        kept = runpy.run_path(str(SCRIPT))["KEPT_CELLS"]
        self.assertEqual(sorted(kept), sorted(rewrites))
        for table, cells in kept.items():
            ident, rewrite = rewrites[table]
            for cell in cells:
                with self.subTest(table=table, cell=cell):
                    legal, = self.raw_cells(table, ident, cell)
                    self.assertTrue(legal)
                    padded = b" " + legal + b" "
                    self.hand_edit(table, ident, **{cell: padded.decode()})
                    held = padded.decode() if cell == "id" else ident
                    warned = self.run_raw("status")
                    self.assertEqual(warned[0], 0)
                    self.assertIn(repr(padded.decode()), warned[2])
                    before = (self.at / table).read_bytes()
                    self.assertEqual(self.run_raw(*rewrite(cell))[0], 0)
                    self.assertNotEqual((self.at / table).read_bytes(), before)
                    self.assertEqual(self.raw_cells(table, held, cell), (padded,))
                    self.assertEqual(self.run_raw("status"), warned)
                    self.hand_edit(table, held, **{cell: legal.decode()})
                    self.assertEqual(self.run_raw("status")[2], "")

    def test_a_goal_cell_that_ends_in_a_carriage_return_still_reads_as_unassigned_after_another_ticket_is_rewritten(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix")
        rail = self.at / "rail.tsv"
        self.assertEqual(rail.read_bytes().count(b"\tG1\n"), 1)
        rail.write_bytes(rail.read_bytes().replace(b"\tG1\n", b"\tG1\r\n"))
        warning = (f"brigade: warning: T1 names goal 'G1\\r', {self.NO_GOAL_ROW}; "
                   "run ticket set T1 --goal G<n> or --no-goal\n")
        unassigned = ["kinds: fix 2 waiting", "goals: unassigned 2 waiting"]
        code, out, err = self.run_raw("status")
        self.assertEqual((code, err, out.splitlines()[3:]), (0, warning, unassigned))
        self.assertEqual(self.run_raw("ticket", "set", "T2", "--priority", "low"), (0, "T2 priority low\n", ""))
        self.assertEqual(self.raw_cells("rail.tsv", "T2", "priority"), (b"low",))
        self.assertEqual(self.raw_cells("rail.tsv", "T1", "kind", "goal"), (b"fix", b"G1\r"))
        code, out, err = self.run_raw("status")
        self.assertEqual((code, err, out.splitlines()[3:]), (0, warning, unassigned))




    def item_with_two_bad_cells(self):
        """D1 in flight with a kind cell and a goal cell that are not legal, and its ticket T1 with a bad kind cell. Returns D1's warnings."""
        self.fired_bug_fix()
        self.hand_edit("dishes.tsv", "D1", kind="chore", goal="G9")
        self.hand_edit("rail.tsv", "T1", kind="chore")
        by_hand = "no command changes this cell; correct it in dishes.tsv by hand"
        return (f"brigade: warning: D1 has kind 'chore', {self.NOT_A_KIND}; {by_hand}\n"
                f"brigade: warning: D1 names goal 'G9', {self.NO_GOAL_ROW}; {by_hand}\n")

    def test_ticket_add_from_report_of_an_item_with_a_bad_kind_cell_and_a_bad_goal_cell_warns_of_both_and_files_a_ticket_with_no_kind_and_no_goal(self):
        warnings = self.item_with_two_bad_cells()
        self.review_file("D1.md", "## Follow-ups\n\n- Tidy guide.\n")
        self.assertEqual(self.run_raw("ticket", "add", "--from-report", "reports/D1.md"),
                         (0, "T2 added from perf/reports/D1.md#1: Tidy guide.\n", warnings))
        self.assertEqual(self.raw_cells("rail.tsv", "T2", "state", "kind", "goal"), (b"waiting", b"", b""))
        self.assertEqual(self.raw_cells("dishes.tsv", "D1", "kind", "goal"), (b"chore", b"G9"))

    def test_ticket_add_from_report_dry_run_prints_the_same_warnings_and_changes_no_file(self):
        warnings = self.item_with_two_bad_cells()
        self.review_file("D1.md", "## Follow-ups\n\n- Tidy guide.\n")
        before = self.store_files()
        self.assertEqual(self.run_raw("ticket", "add", "--from-report", "reports/D1.md", "--dry-run"),
                         (0, "would add from perf/reports/D1.md#1: Tidy guide.\n", warnings))
        self.assertEqual(self.store_files(), before)






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


    def test_status_on_a_store_from_before_kinds_prints_its_three_lines_and_then_the_count_of_waiting_tickets_as_untagged(self):
        self.store_from_before_kinds()
        before = self.store_files()
        self.assertEqual(self.run_raw("status"), (0, "\n".join([
            "thread not recorded", f"reporting: milestones, lands by local, {self.OLD_COUNTS}", self.OLD_WORKERS,
            "untagged: 5"]) + "\n", ""))
        self.assertEqual(self.store_files(), before)

    def test_goal_list_on_a_store_from_before_kinds_counts_each_ticket_and_item_as_unassigned_and_writes_nothing(self):
        self.store_from_before_kinds()
        before = self.store_files()
        # Waiting. T8 to T12. In flight. D1 to D6. Merged. D7. Send-backs. The one row on D4.
        self.assertEqual(self.brigade("goal", "list"),
                         "no open or paused goals\nunassigned: 5 waiting, 6 in flight, 1 merged, 1 send-back")
        self.assertEqual(self.store_files(), before)

    def test_status_ticket_list_startable_watch_86_list_close_and_walk_print_for_an_empty_store_from_before_kinds_what_the_tool_printed_before_kinds_and_change_no_table(self):
        self.store_from_before_kinds(rows=False)
        before = self.tables()
        idle = "workers: 0 of 2 running, 2 idle, auto-start: normal"
        self.assertEqual(self.run_raw("status"), (0, f"thread not recorded\nreporting: milestones, lands by local\n{idle}\n", ""))
        self.assertEqual(self.brigade("ticket", "list"), "")
        self.assertEqual(self.brigade("startable"), f"{idle}\nno waiting tickets")
        self.assertEqual(self.brigade("watch"), f"{idle}\nno work in progress")
        self.assertEqual(self.brigade("86", "list"), "no open decisions")
        self.assertEqual(self.close_run("--dry-run"), (0, "# Perf report\n\nSince opening. nothing on record.\n\nNothing new.", ""))
        self.assertEqual(self.brigade("walk").splitlines(), [
            f"{_shown_root(self.project)}: local mode onto refs/landing/lane. empty.",
            "  Perf (reports milestones): nothing on record",
            "    thread not recorded"])
        self.assertEqual(self.tables(), before)


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

    def test_next_readonly(self):
        self.queue()
        lock = self.at / "restaurant.lock"
        lock.unlink(missing_ok=True)
        before, landing = self.store_files(), self.landing_rows()
        self.assertEqual(self.brigade("next"), "\n".join([
            "wake: unspecified", "reporting: milestones", "nothing else owed", "reply: no (routine: unspecified)"]))
        self.assertFalse(lock.exists())
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.landing_rows(), landing)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.open_admin()
        self.admin("request", "--to", "perf", "from-user perf: please fix the gate")
        admin_dir = self.store / "bridge-kit" / ".admin"
        before, landing = self.store_files(), self.landing_rows()
        admin_before = self.directory_files(admin_dir)
        triggers = [
            ("next",),
            ("next", "report-back", "D1"),
            ("next", "review", "D1", "--verdict", "pass"),
            ("next", "review", "D1", "--verdict", "send-back"),
            ("next", "review", "D1", "--verdict", "blocked"),
            ("next", "review", "D1", "--verdict", "pass-with-edits"),
            ("next", "checks", "D1", "--result", "pass"),
            ("next", "checks", "D1", "--result", "fail"),
            ("next", "merge", "D1"),
            ("next", "liveness",),
            ("next", "intake",),
            ("next", "morning",),
            ("next", "evening",),
            ("next", "user",),
            ("next", "user", "--question"),
            ("next", "admin", "A1"),
            ("next", "--failure", "fixable"),
            ("next", "--failure", "stuck"),
            ("next", "--failure", "stuck", "merge", "D1"),
            ("next", "merge", "D1", "--failure", "stuck"),
            ("next", "--unsent", "failed"),
            ("next", "--unsent", "report", "liveness"),
            ("next", "liveness", "--unsent", "report"),
        ]
        for args in triggers:
            with self.subTest(args=args):
                self.brigade(*args)
                self.assertEqual(self.store_files(), before)
                self.assertEqual(self.landing_rows(), landing)
                self.assertEqual(self.directory_files(admin_dir), admin_before)
        for level in ("every-turn", "milestones", "digest"):
            self.brigade("set", "--reporting", level)
            before, landing = self.store_files(), self.landing_rows()
            self.brigade("next")
            self.assertEqual(self.store_files(), before)
            self.assertEqual(self.landing_rows(), landing)
        self.brigade("set", "--reports-to", "admin-thread")
        before, landing = self.store_files(), self.landing_rows()
        self.assertIn("reports to: admin-thread", self.brigade("next"))
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.landing_rows(), landing)
        for args, sentence in (
            (("next", "report-back", "D99"), "brigade: no D99 in dishes.tsv\n"),
            (("next", "review", "D99", "--verdict", "pass"), "brigade: no D99 in dishes.tsv\n"),
            (("next", "checks", "D99", "--result", "fail"), "brigade: no D99 in dishes.tsv\n"),
            (("next", "merge", "D99"), "brigade: no D99 in dishes.tsv\n"),
            (("next", "admin", "A9"), "brigade: no request A9 in the inbox\n"),
        ):
            with self.subTest(args=args):
                before = self.store_files()
                self.assertEqual(self.run_raw(*args), (1, "", sentence))
                self.assertEqual(self.store_files(), before)
        self.assertEqual(self.admin("next", ok=False), "brigade: the executive admin routes work and never runs it")
        self.assertEqual(self.directory_files(admin_dir), admin_before)
        self.store_from_before_kinds()
        self.assertFalse((self.at / "goals.tsv").exists())
        before, landing = self.store_files(), self.landing_rows()
        self.brigade("next")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.landing_rows(), landing)
        self.assertFalse((self.at / "goals.tsv").exists())
        self.brigade("set", "--thread", "t1")
        bare = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at), "next"],
                              capture_output=True, text=True)
        self.assertEqual((bare.returncode, bare.stdout, bare.stderr),
                         (1, "", "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status\n"))
        self.assertEqual(self.brigade("--owner", "other@1", "next", ok=False),
                         "brigade: owner other@1 is stale; this store is owned by t1@1")
        meta_path = self.at / "restaurant.json"
        meta = json.loads(meta_path.read_text())
        meta["thread"] = ""
        meta_path.write_text(json.dumps(meta) + "\n")
        before, landing = self.store_files(), self.landing_rows()
        self.assertEqual(self.brigade("next", ok=False),
                         "brigade: this store has no recorded thread; it was retired, and only set --thread --expect restarts it")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.landing_rows(), landing)
        mod = runpy.run_path(str(SCRIPT))
        glob = mod["run"].__globals__
        glob["READ_ONLY"] = True
        try:
            restaurant = mod["Restaurant"](str(self.at))
            with self.assertRaises(mod["BrigadeError"]) as raised:
                restaurant.write("probe.txt", "nope\n")
            self.assertEqual(str(raised.exception), "brigade: next is read-only")
            self.assertFalse((self.at / "probe.txt").exists())
            with self.assertRaises(mod["BrigadeError"]) as raised:
                mod["land_result"](str(self.project), "status")
            self.assertEqual(str(raised.exception), "brigade: next is read-only")
        finally:
            glob["READ_ONLY"] = False
        meta["thread"] = "t1"
        meta_path.write_text(json.dumps(meta) + "\n")
        mod["Restaurant"](str(self.at), "t1@1").write("probe.txt", "ok\n")
        self.assertEqual((self.at / "probe.txt").read_text(), "ok\n")







    def assert_one_close_file(self, sheet):
        self.assertEqual([line for line in sheet.splitlines() if line == "run close --to-file"], ["run close --to-file"])


    def test_next_wrong_id(self):
        self.fired_bug_fix()
        before = self.store_files()
        self.assertEqual(self.run_raw("next", "report-back", "D99"), (1, "", "brigade: no D99 in dishes.tsv\n"))
        self.assertEqual(self.run_raw("next", "admin", "A3"), (1, "", "brigade: no request A3 in the inbox\n"))
        self.assertEqual(self.store_files(), before)

    def test_next_warns_when_a_timebox_is_not_a_base_10_integer(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--reported")
        self.brigade("set", "--reporting", "digest")
        path = self.at / "dishes.tsv"
        lines = path.read_text().splitlines()
        header = lines[0].split("\t")
        fields = lines[1].split("\t")
        self.assertLess(header.index("timebox"), len(fields))
        self.assertIn("T", fields[header.index("at")])
        self.assertEqual(fields[header.index("reported")], "yes")
        fields[header.index("timebox")] = "sixty"
        lines[1] = "\t".join(fields)
        path.write_text("\n".join(lines) + "\n")
        code, out, err = self.run_raw("next")
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err + out)
        self.assertIn("brigade: warning: D1 timebox sixty is not a base-10 integer\n", err)
        sheet = out.splitlines()
        self.assertEqual([line for line in sheet if line.startswith("reply:")], [sheet[-1]])
        fields[header.index("timebox")] = ""
        lines[1] = "\t".join(fields)
        path.write_text("\n".join(lines) + "\n")
        code, out, err = self.run_raw("next")
        self.assertEqual(code, 0)
        self.assertNotIn("timebox", err)
        self.assertNotIn("Traceback", err + out)

    def test_next_warns_when_an_id_suffix_is_not_a_base_10_integer(self):
        self.fired_bug_fix()
        self.park_item()
        path = self.at / "86.tsv"
        text = path.read_text()
        self.assertIn("\nQ1\t", text)
        path.write_text(text.replace("\nQ1\t", "\nQx\t", 1))
        code, out, err = self.run_raw("next")
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err + out)
        self.assertEqual(err.count("brigade: warning: Qx has no base-10 id suffix"), 1)
        sheet = out.splitlines()
        self.assertEqual([line for line in sheet if line.startswith("reply:")], [sheet[-1]])

    def test_next_sees_a_malformed_edits_row_instead_of_close(self):
        self.fired_bug_fix()
        self.brigade("set", "--reporting", "digest")
        self.brigade("set", "--reports-to", "admin-thread")
        (self.at / "edits.tsv").write_text("at\tdish\tfrom\tto\treport\nnot-a-row\n")
        code, out, err = self.run_raw("next", "evening")
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err + out)
        self.assertNotIn("run close", out)
        self.assertIn("see bridge-kit/perf/edits.tsv line 2 is malformed; fix or remove it", out)
        self.assertIn("see t3_thread_send admin-thread mode queue: report perf: the path close --to-file prints", out)
        sheet = out.splitlines()
        self.assertTrue(sheet[-1].startswith("reply:"))

    def test_next_asks_for_a_report_the_row_says_was_written(self):
        self.fired_bug_fix()
        self.brigade("set", "--reporting", "digest")
        self.brigade("dish", "D1", "--thread", "thread-9")
        self.brigade("dish", "D1", "--reported")
        line = ("if reports/D1.md is absent or older than the last attempt start: "
                "read the worker thread (thread thread-9) and get the report")
        bare = self.next_sheet()
        self.assertIn(line, bare)
        self.assertEqual(bare.splitlines()[-1], "reply: no (routine: unspecified)")
        self.assertNotIn("running ", bare)
        morning = self.next_sheet("morning")
        self.assertIn(line, morning)
        named = self.next_sheet("report-back", "D1")
        self.assertIn(line, named)
        self.park_item("which way")
        held = self.next_sheet()
        self.assertNotIn("reports/D1.md is absent", held)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("dish", "D1", "--thread", "thread-9")
        self.brigade("dish", "D1", "--reported")
        reports = self.at / "reports"
        reports.mkdir()
        current_path = reports / "D1.md"
        current_path.write_text("done\n")
        current = self.next_sheet()
        self.assertIn("see D1: report written ", current)
        self.assertNotIn("reports/D1.md is absent", current)
        old = time.time() - 7200
        os.utime(current_path, (old, old))
        stale = self.next_sheet()
        self.assertIn(line, stale)
        self.assertNotIn("see D1: report written ", stale)
        self.assertNotIn("see D1: reported ", stale)
        self.assertNotIn("running ", stale)

        def stamp_attempt(at):
            path = self.at / "log.tsv"
            lines = path.read_text().splitlines()
            rewritten = [lines[0]]
            found = False
            for row in lines[1:]:
                fields = row.split("\t")
                if not found and fields[1:4] == ["dish", "D1", "in-progress"]:
                    fields[0] = at
                    row = "\t".join(fields)
                    found = True
                rewritten.append(row)
            self.assertTrue(found)
            path.write_text("\n".join(rewritten) + "\n")

        self.fresh()
        self.brigade("dish", "D1", "--reported")
        self.brigade("86", "add", "--dish", "D1", "--question", "Which way?", "--options", "a,b", "--default", "a")
        reports = self.at / "reports"
        reports.mkdir()
        (reports / "D1.md").write_text("held\n")
        stamp_attempt("2026-10-11T03:00:00")
        code, out, err = self.run_raw("next")
        self.assertEqual((code, err), (0, ""))
        self.assertNotIn("Traceback", out)
        self.assertEqual(out.splitlines(), [
            "wake: unspecified",
            "reporting: milestones",
            "see D1: open decision Q1: Which way?; launch no worker or verifier until 86 answer Q1",
            "run close",
            "reply: yes (decision: Q1)",
        ])
        self.fresh()
        self.brigade("dish", "D1", "--reported")
        reports = self.at / "reports"
        reports.mkdir()
        (reports / "D1.md").write_text("current\n")
        stamp_attempt("2020-01-01T00:00:00")
        code, out, err = self.run_raw("next")
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err + out)
        self.assertTrue(any(line.startswith("see D1: report written ") for line in out.splitlines()))
        self.assertNotIn("reports/D1.md is absent", out)

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


    def test_next_old_store(self):
        self.store_from_before_kinds()
        self.assertFalse((self.at / "goals.tsv").exists())
        code, out, err = self.run_raw("next")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("wake: unspecified\n"))
        lines = out.splitlines()
        self.assertEqual([line for line in lines if line.startswith("reply:")], [lines[-1]])
        self.assertFalse((self.at / "goals.tsv").exists())
        path = self.at / "dishes.tsv"
        rows = path.read_text().splitlines()
        fields = rows[1].split("\t")
        self.assertEqual((fields[0], len(fields)), ("D1", 15))
        rows[1] = "\t".join(fields + ["nope"])
        path.write_text("\n".join(rows) + "\n")
        code, out, err = self.run_raw("next")
        status = self.run_raw("status")
        self.assertEqual(code, 0)
        self.assertEqual(err, status[2])
        self.assertIn("D1 has kind 'nope'", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual([line for line in out.splitlines() if line.startswith("reply:")], [out.splitlines()[-1]])
        self.assertFalse((self.at / "goals.tsv").exists())

    def test_next_malformed(self):
        self.fired_bug_fix()
        path = self.at / "dishes.tsv"
        rows = path.read_text().splitlines()
        rows[1] = "not-a-row"
        path.write_text("\n".join(rows) + "\n")
        code, out, err = self.run_raw("next")
        self.assertEqual((code, out, err),
                         (1, "", "brigade: bridge-kit/perf/dishes.tsv line 2 is malformed; fix or remove it\n"))
        self.assertNotIn("Traceback", err)


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












    def test_fire_doc_item_keeps_workers_kind_and_lease_checks(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "First")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Second")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "First")
            self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second",
                                          "--paths", "docs/guide.md", "--timebox", "120", "--doc-item", ok=False),
                             "brigade: nothing fired: 1 of 1 workers running")
            self.assertEqual(self.blocked_note(self.at, "T2"),
                             {"kind": "workers", "tickets": ["T2"], "station": "bug-fix", "summary": "Second",
                              "paths": "docs/guide.md", "timebox": 120, "doc_item": True})
            self.brigade("dish", "D1", "--state", "sent-back")
            unblocked = next(part for part in self.brigade("watch").splitlines() if "unblocked; run" in part)
            self.assertIn("--doc-item", unblocked)
            ran = self.bash_unblocked(self.at, unblocked)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            dish = ran.stdout.strip().split()[0]
            self.assertEqual(self.doc_log_note(self.at, dish), "doc-item: docs/guide.md")








    def test_next_points_at_startable_with_one_see_line(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix a")
        lines = self.brigade("next").splitlines()
        see = [line for line in lines if line.startswith("see ")]
        self.assertEqual(see, ["see Run startable and take the first ticket it prints as startable, "
                               "with every ticket it prints as rides with that ticket."])


class EntryLoadTest(unittest.TestCase):
    """Two loadings of the entry in one process, as a caller that loads it by path makes."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Path(self.temporary.name) / "store"
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        self.at = self.store / "project" / "core"
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(Path(self.temporary.name) / "config")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def globals_of(self, load):
        """The live module globals one load's functions read. run_path hands back a copy of them."""
        return load["run"].__globals__

    def open_store(self):
        return runpy.run_path(str(SCRIPT))["run"](["--store", str(self.store), "--at", str(self.at), "open",
                                                   "--project-root", str(self.project), "--name", "Core"])

    def add_ticket(self, load, summary):
        return load["run"](["--store", str(self.store), "--at", str(self.at), "ticket", "add",
                            "--summary", summary, "--kind", "fix"])

    def rail(self):
        lines = (self.at / "rail.tsv").read_text().splitlines()
        keys = lines[0].split("\t")
        return [dict(zip(keys, line.split("\t"))) for line in lines[1:]]

    def test_a_second_load_does_not_reset_the_first_loads_read_only_guard(self):
        self.open_store()
        first = runpy.run_path(str(SCRIPT))
        self.globals_of(first)["READ_ONLY"] = True
        second = runpy.run_path(str(SCRIPT))
        self.globals_of(second)["now"] = lambda: "2001-01-01T00:00:00Z"
        with self.assertRaises(first["BrigadeError"]) as raised:
            self.add_ticket(first, "Guarded")
        self.assertEqual(str(raised.exception), "brigade: next is read-only")
        self.assertEqual(self.rail(), [])
        self.assertEqual(self.add_ticket(second, "Unguarded"), "T1")

    def test_each_load_keeps_its_own_patched_clock(self):
        self.open_store()
        first = runpy.run_path(str(SCRIPT))
        self.globals_of(first)["now"] = lambda: "2000-01-01T00:00:00Z"
        second = runpy.run_path(str(SCRIPT))
        self.globals_of(second)["now"] = lambda: "2001-01-01T00:00:00Z"
        self.assertEqual(self.add_ticket(first, "First"), "T1")
        self.assertEqual(self.add_ticket(second, "Second"), "T2")
        self.assertEqual([(row["id"], row["summary"], row["at"]) for row in self.rail()],
                         [("T1", "First", "2000-01-01T00:00:00Z"), ("T2", "Second", "2001-01-01T00:00:00Z")])

    def test_two_loads_do_not_share_held_locks(self):
        first = runpy.run_path(str(SCRIPT))
        first["HELD_LOCKS"].append(Path("/store/held"))
        second = runpy.run_path(str(SCRIPT))
        self.assertEqual(first["HELD_LOCKS"], [Path("/store/held")])
        self.assertEqual(second["HELD_LOCKS"], [])

    def test_a_restaurant_from_the_first_load_still_writes_after_a_second_load(self):
        self.open_store()
        first = runpy.run_path(str(SCRIPT))
        restaurant = first["Restaurant"](str(self.at))
        second = runpy.run_path(str(SCRIPT))
        self.globals_of(second)["READ_ONLY"] = True
        restaurant.write("notes.txt", "written by the first load\n")
        self.assertEqual((self.at / "notes.txt").read_text(), "written by the first load\n")
        with self.assertRaises(second["BrigadeError"]) as raised:
            second["Restaurant"](str(self.at)).write("notes.txt", "written by the second load\n")
        self.assertEqual(str(raised.exception), "brigade: next is read-only")
        self.assertEqual((self.at / "notes.txt").read_text(), "written by the first load\n")

    def test_two_entry_paths_load_independent_area_modules(self):
        self.open_store()
        first = runpy.run_path(str(SCRIPT))
        second = runpy.run_path(str(ROOT / "skills/brigade/scripts/brigade.py"))
        first["HELD_LOCKS"].append(Path("/store/held"))
        self.assertEqual(first["HELD_LOCKS"], [Path("/store/held")])
        self.assertEqual(second["HELD_LOCKS"], [])
        self.globals_of(second)["now"] = lambda: "2001-01-01T00:00:00Z"
        restaurant = first["Restaurant"](str(self.at))
        restaurant.write("notes.txt", "written by the source load\n")
        self.assertEqual((self.at / "notes.txt").read_text(), "written by the source load\n")
        self.assertEqual(self.add_ticket(second, "From the built copy"), "T1")
        self.assertEqual([row["at"] for row in self.rail()], ["2001-01-01T00:00:00Z"])
        self.assertIsNot(first["Restaurant"], second["Restaurant"])
        self.assertIsNot(first["seam"], second["seam"])


class StoresTest(unittest.TestCase):
    """Two or three coordinators on one project root, as `app/<name>` under the store."""

    def setUp(self):
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
        return subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.dir(name)), *args],
                              capture_output=True, text=True)

    def brigade(self, name, *args, ok=True):
        result = self.run_brigade(name, *args)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return (result.stdout if ok else result.stderr).strip()

    def open(self, name, *extra):
        return self.brigade(name, "open", "--project-root", str(self.project), "--name", name, *extra)

    def child(self, mode, name, *args):
        return subprocess.run([sys.executable, str(Path(__file__).resolve()), "--race-child", mode, self.temporary.name,
                               "--store", str(self.store), "--at", str(self.dir(name)), *args],
                              capture_output=True, text=True, timeout=30, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))

    def paused(self, label, name, *args):
        """A command that has parsed its arguments and waits before it takes the store lock."""
        case = Path(self.temporary.name) / label
        case.mkdir()
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--race-child", "pause", str(case),
                                 "--store", str(self.store), "--at", str(self.dir(name)), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
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


class HandoffTest(StoresTest):
    def test_ticket_add_takes_a_user_request_with_the_summary_of_a_moved_ticket(self):
        self.open("core")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache"), "T2")

    def test_from_report_files_the_same_report_name_in_two_siblings(self):
        body = "## Follow-ups\n\n- Shared text.\n"
        for name in ("core", "engine"):
            self.open(name)
            self.brigade(name, "ticket", "add", "--kind", "fix", "--summary", "s")
            self.brigade(name, "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
            (self.dir(name) / "reports").mkdir(exist_ok=True)
            (self.dir(name) / "reports" / "D1.md").write_text(body)
            self.assertEqual(self.brigade(name, "ticket", "add", "--from-report", "reports/D1.md"),
                             f"T2 added from {name}/reports/D1.md#1: Shared text.")
        self.assertEqual(self.rows("engine", "rail.tsv")[1][2:], ["waiting", "report", "engine/reports/D1.md#1", "", "Shared text.", "", "", "", "polish", ""])

    def test_a_sibling_cannot_claim_a_source_another_owns(self):
        self.open("docs", "--intake", "github")
        self.assertEqual(json.loads((self.dir("docs") / "restaurant.json").read_text())["intake"], ["github"])
        self.open("engine")
        self.assertEqual(self.brigade("engine", "set", "--intake", "github", ok=False),
                         "brigade: docs already owns intake from github; move tickets to it instead")
        self.assertEqual(self.brigade("engine", "open", "--project-root", str(self.project), "--name", "release",
                                      "--intake", "github", ok=False),
                         "brigade: docs already owns intake from github; move tickets to it instead")
        self.assertFalse(self.dir("release").exists())
        self.assertEqual(self.brigade("engine", "open", "--project-root", str(self.project), "--name", "engine",
                                      "--intake", "github"),
                         "\n".join([f"exists {self.dir('engine')}",
                                    "intake stays empty; change it with set --intake",
                                    f"sibling docs ({self.dir('docs')}), thread not recorded",
                                    "  purpose: not written yet",
                                    "  off the menu: not written yet"]))
        self.assertEqual(json.loads((self.dir("engine") / "restaurant.json").read_text())["intake"], [])

    def test_two_owners_of_one_source_both_refuse_to_file(self):
        self.open("core")
        self.open("docs")
        for name in ("core", "docs"):
            path = self.dir(name) / "restaurant.json"
            meta = json.loads(path.read_text())
            meta["intake"] = ["github"]
            path.write_text(json.dumps(meta))
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "x", "--source", "github", ok=False),
                         "brigade: docs owns intake from github; ask it to file this and move it here")
        self.assertEqual(self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "x", "--source", "github", ok=False),
                         "brigade: core owns intake from github; ask it to file this and move it here")
        self.assertEqual(self.rows("core", "rail.tsv") + self.rows("docs", "rail.tsv"), [])
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "from the user"), "T1")

    def test_one_ref_is_filed_once_while_live(self):
        ref = "https://github.com/o/r/issues/7"
        self.open("core", "--intake", "github")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a", "--source", "github", "--ref", ref), "T1")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "b", "--source", "github", "--ref", f"  {ref} ", ok=False),
                         f"brigade: {ref} is already T1 (waiting); nothing added")
        self.brigade("core", "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "fix")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "b", "--ref", ref, ok=False),
                         f"brigade: {ref} is already T1 (assigned); nothing added")
        self.brigade("core", "ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "reopened", "--source", "github", "--ref", ref), "T2")

    def test_dropping_a_source_with_a_waiting_ticket_is_refused(self):
        self.open("core", "--intake", "github,feed")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a", "--source", "github")
        self.assertEqual(self.brigade("core", "set", "--intake", "feed", ok=False),
                         "brigade: T1 from github is waiting; finish, drop, or move it before dropping github from intake")
        self.brigade("core", "ticket", "set", "T1", "--state", "dropped")
        self.brigade("core", "set", "--intake", "feed")
        self.assertEqual(json.loads((self.dir("core") / "restaurant.json").read_text())["intake"], ["feed"])

    def test_move_hands_a_ticket_to_a_sibling_and_take_files_it(self):
        self.open("core", "--intake", "github")
        self.open("engine")
        self.brigade("engine", "set", "--thread", "thread-engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache", "--source", "github", "--ref", "R7")
        self.assertEqual(self.brigade("core", "ticket", "move", "T1", "--to", "engine"),
                         "T1 moved to engine; tell thread thread-engine")
        self.assertEqual(self.brigade("core", "ticket", "list"), "T1 moved normal fix [github] Fix the cache R7")
        self.assertEqual(self.brigade("core", "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "x", ok=False),
                         "brigade: T1 is moved, not waiting")
        self.assertEqual(self.brigade("core", "ticket", "set", "T1", "--state", "waiting", ok=False),
                         "brigade: T1 moved to engine; it is that coordinator's ticket now")
        self.assertEqual(json.loads((self.dir("engine") / "inbox" / "app~core~T1.json").read_text()),
                         {"handoff": "app/core/T1", "summary": "Fix the cache", "source": "github", "ref": "R7",
                          "priority": "", "paths": "", "decision": "", "kind": "fix"})
        self.assertEqual(self.brigade("core", "watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: moved to engine, waiting for ticket take")
        self.assertEqual(self.brigade("engine", "watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nhanded to you: 1; run ticket take")
        self.assertEqual(self.brigade("engine", "status"),
                         "thread thread-engine\nreporting: milestones, no landing contract, handed to you: 1\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nowner thread-engine@1")
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/core/T1: Fix the cache")
        self.assertEqual(self.brigade("engine", "ticket", "list"), "T1 waiting normal fix [github (from app/core/T1)] Fix the cache R7")
        self.assertEqual(self.inbox("engine"), [])
        self.assertEqual(self.brigade("core", "watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")
        self.assertEqual(self.brigade("engine", "watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")
        self.assertIn("## Handed to another coordinator\n\n- T1: Fix the cache (to engine)",
                      self.brigade("core", "close", "--dry-run"))
        self.assertEqual(self.brigade("engine", "ticket", "take"), "nothing handed to you")
        (self.dir("engine") / "inbox" / "app~core~T1.json").write_text("{}")
        self.assertEqual(self.brigade("core", "watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")

    def test_a_moved_ref_stays_live_through_each_move_until_the_end_of_the_chain_finishes(self):
        ref = "R7"
        self.open("core", "--intake", "github")
        self.open("docs")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "s", "--source", "github", "--ref", ref)
        self.brigade("core", "ticket", "move", "T1", "--to", "docs")
        refused = f"brigade: {ref} is already T1 (moved); nothing added"
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "s", "--source", "github", "--ref", ref, ok=False), refused)
        self.brigade("docs", "ticket", "take")
        self.brigade("docs", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "s", "--source", "github", "--ref", ref, ok=False), refused)
        self.brigade("engine", "ticket", "take")
        self.assertEqual(self.brigade("engine", "ticket", "list"), f"T1 waiting normal fix [github (from app/docs/T1)] s {ref}")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "s", "--source", "github", "--ref", ref, ok=False), refused)
        self.brigade("engine", "ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "s", "--source", "github", "--ref", ref), "T2")

    def test_a_new_owner_refuses_a_ref_the_old_owner_still_holds_live(self):
        self.handoff(source="core", target="docs", ref="R7")
        self.open("engine")
        self.brigade("core", "set", "--intake", "")
        self.brigade("engine", "set", "--intake", "github")
        self.assertEqual(self.brigade("engine", "ticket", "add", "--kind", "fix", "--summary", "s", "--source", "github", "--ref", "R7", ok=False),
                         "brigade: R7 is already T1 in core (moved); nothing added")
        self.assertEqual(self.brigade("engine", "ticket", "add", "--kind", "fix", "--summary", "s", "--source", "github", "--ref", "R8"), "T1")

    def test_a_replaced_owners_paused_move_does_not_republish_the_handoff(self):
        self.open("core", "--intake", "github")
        self.open("engine")
        self.brigade("core", "set", "--thread", "t1")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache", "--source", "github", "--ref", "R7")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        # The handoff was never delivered, so a rerun of the move would publish it again.
        (self.dir("engine") / "inbox" / "app~core~T1.json").unlink()
        stale = self.paused("stale", "core", "--owner", "t1@1", "ticket", "move", "T1", "--to", "engine")
        missing = self.paused("missing", "core", "ticket", "move", "T1", "--to", "engine")
        self.brigade("core", "--owner", "t1@1", "set", "--thread", "t2", "--replace")
        before = {"core": self.files("core"), "engine": self.files("engine")}
        self.assertEqual(self.resume(*stale), (1, "brigade: owner t1@1 is stale; this store is owned by t2@2"))
        self.assertEqual(self.resume(*missing),
                         (1, "brigade: this store is owned by t2@2; pass --owner <thread>@<generation> from status"))
        self.assertEqual({"core": self.files("core"), "engine": self.files("engine")}, before)
        self.assertEqual(self.inbox("engine"), [])
        self.brigade("core", "--owner", "t2@2", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(self.inbox("engine"), ["app~core~T1.json"])

    def test_a_replaced_owners_paused_take_does_not_delete_the_inbox_file(self):
        inbox = self.handoff()
        self.brigade("engine", "set", "--thread", "e1")
        handed = inbox.read_bytes()
        self.assertEqual(self.brigade("engine", "--owner", "e1@1", "ticket", "take"), "T1 from app/core/T1: Fix the cache")
        # A take that filed the ticket and died before deleting the file leaves exactly this.
        inbox.write_bytes(handed)
        stale = self.paused("stale", "engine", "--owner", "e1@1", "ticket", "take")
        missing = self.paused("missing", "engine", "ticket", "take")
        self.brigade("engine", "--owner", "e1@1", "set", "--thread", "e2", "--replace")
        before = self.files("engine")
        self.assertEqual(self.resume(*stale), (1, "brigade: owner e1@1 is stale; this store is owned by e2@2"))
        self.assertEqual(self.resume(*missing),
                         (1, "brigade: this store is owned by e2@2; pass --owner <thread>@<generation> from status"))
        self.assertEqual(self.files("engine"), before)
        self.assertEqual(self.inbox("engine"), ["app~core~T1.json"])
        self.assertEqual(self.brigade("engine", "--owner", "e2@2", "ticket", "take"), "T1 from app/core/T1: Fix the cache")
        self.assertEqual(self.inbox("engine"), [])

    def test_a_move_interrupted_after_the_rewrite_publishes_once_on_rerun(self):
        self.open("core", "--intake", "github")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache", "--source", "github", "--ref", "R7")
        died = self.child("die-before-publish", "core", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.brigade("core", "ticket", "list"), "T1 moved normal fix [github] Fix the cache R7")
        self.assertEqual(self.inbox("engine"), [])
        self.assertEqual(self.brigade("core", "watch"),
                         "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: moved to engine, not delivered; run ticket move T1 --to engine again")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(self.inbox("engine"), ["app~core~T1.json"])
        self.assertEqual([row[1:4] for row in self.rows("core", "log.tsv") if row[3] == "moved"], [["ticket", "T1", "moved"]])
        self.brigade("engine", "ticket", "take")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(self.inbox("engine"), [])

    def test_watch_says_undelivered_when_the_inbox_file_is_deleted_before_take(self):
        self.handoff().unlink()
        self.assertEqual(self.brigade("core", "watch"),
                         "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: moved to engine, not delivered; run ticket move T1 --to engine again")

    def test_take_twice_with_the_file_copied_back_files_one_ticket(self):
        inbox = self.handoff()
        saved = inbox.read_bytes()
        self.brigade("engine", "ticket", "take")
        inbox.write_bytes(saved)
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/core/T1: Fix the cache")
        self.assertEqual(len(self.rows("engine", "rail.tsv")), 1)
        self.assertEqual([row for row in self.rows("engine", "log.tsv") if row[4] == "from app/core/T1"],
                         [[self.rows("engine", "log.tsv")[0][0], "ticket", "T1", "waiting", "from app/core/T1"]])
        self.assertEqual(self.inbox("engine"), [])

    def test_a_moved_ticket_keeps_its_priority_paths_and_decision_after_ticket_take(self):
        self.open("core")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix the cache", "--priority", "urgent",
                     "--paths", "src/cache.py,docs", "--decision")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.assertEqual(json.loads((self.dir("engine") / "inbox" / "app~core~T1.json").read_text()),
                         {"handoff": "app/core/T1", "summary": "Fix the cache", "source": "user", "ref": "",
                          "priority": "urgent", "paths": "docs,src/cache.py", "decision": "yes", "kind": "fix"})
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/core/T1: Fix the cache")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][2:],
                         ["waiting", "user (from app/core/T1)", "", "", "Fix the cache", "urgent", "docs,src/cache.py", "yes", "fix", ""])
        self.assertEqual(self.brigade("engine", "ticket", "list"), "T1 waiting urgent fix [user (from app/core/T1)] Fix the cache")

    def test_a_moved_ticket_with_an_empty_priority_cell_reads_the_default_of_its_source_after_ticket_take(self):
        self.open("core", "--intake", "upstream")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Sync", "--source", "upstream")
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.brigade("engine", "ticket", "take")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][7:], ["", "", "", "fix", ""])
        self.assertEqual(self.brigade("engine", "ticket", "list"), "T1 waiting urgent fix [upstream (from app/core/T1)] Sync")

    def test_ticket_take_files_a_handoff_written_without_priority_paths_and_decision(self):
        self.open("core")
        self.open("engine")
        inbox = self.dir("engine") / "inbox"
        inbox.mkdir()
        (inbox / "app~core~T1.json").write_text(json.dumps(
            {"handoff": "app/core/T1", "summary": "Tidy the guide", "source": "report", "ref": "core/reports/D1.md#1"}))
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][2:],
                         ["waiting", "report (from app/core/T1)", "core/reports/D1.md#1", "", "Tidy the guide", "", "", "", "", ""])
        self.assertEqual(self.brigade("engine", "ticket", "list"),
                         "T1 waiting low [report (from app/core/T1)] Tidy the guide core/reports/D1.md#1")

    def labelled_handoff(self):
        """core files T1 as a polish ticket under its goal G1 and moves it to engine. Returns the line ticket move printed."""
        self.open("core")
        self.open("engine")
        self.brigade("core", "goal", "add", "--name", "activity view", "--purpose", "p", "--done", "d")
        self.brigade("core", "ticket", "add", "--summary", "Tidy the guide", "--kind", "polish", "--goal", "G1")
        return self.brigade("core", "ticket", "move", "T1", "--to", "engine")

    def test_ticket_move_writes_the_kind_and_no_goal_into_the_handoff_and_says_which_goal_the_ticket_leaves(self):
        said = "T1 moved to engine; no thread recorded for engine; it leaves G1 here and arrives unassigned"
        self.assertEqual(self.labelled_handoff(), said)
        self.assertEqual(json.loads((self.dir("engine") / "inbox" / "app~core~T1.json").read_text()),
                         {"handoff": "app/core/T1", "summary": "Tidy the guide", "source": "user", "ref": "",
                          "priority": "", "paths": "", "decision": "", "kind": "polish"})
        self.assertEqual(self.rows("core", "rail.tsv")[0][2:],
                         ["moved", "user", "", "to:engine", "Tidy the guide", "", "", "", "polish", "G1"])
        self.assertEqual(self.brigade("core", "ticket", "move", "T1", "--to", "engine"), said)

    def test_ticket_take_files_a_moved_ticket_with_its_kind_and_no_goal(self):
        self.labelled_handoff()
        self.brigade("engine", "goal", "add", "--name", "docs", "--purpose", "p", "--done", "d")
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][2:],
                         ["waiting", "user (from app/core/T1)", "", "", "Tidy the guide", "", "", "", "polish", ""])

    def test_ticket_move_of_a_ticket_with_no_kind_and_no_goal_writes_the_seven_keys_the_handoff_had_before_kinds(self):
        self.open("core")
        self.open("engine")
        (self.dir("core") / "rail.tsv").write_text(
            "id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision\n"
            "T1\t2026-10-01T00:00:00+00:00\twaiting\tuser\t\t\tTidy the guide\tlow\tdocs\t\n")
        self.assertEqual(self.brigade("core", "ticket", "move", "T1", "--to", "engine"),
                         "T1 moved to engine; no thread recorded for engine")
        self.assertEqual((self.dir("engine") / "inbox" / "app~core~T1.json").read_text(), json.dumps(
            {"handoff": "app/core/T1", "summary": "Tidy the guide", "source": "user", "ref": "",
             "priority": "low", "paths": "docs", "decision": ""}, indent=2) + "\n")

    def test_ticket_move_of_a_ticket_whose_kind_cell_holds_no_kind_writes_no_kind_into_the_handoff(self):
        self.open("core")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--summary", "Tidy the guide", "--kind", "polish")
        rail = self.dir("core") / "rail.tsv"
        rail.write_text(rail.read_text().replace("\tpolish\t", "\tbugfix\t"))
        self.brigade("core", "ticket", "move", "T1", "--to", "engine")
        self.assertNotIn("kind", json.loads((self.dir("engine") / "inbox" / "app~core~T1.json").read_text()))
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["", ""])

    def handed(self, *extras):
        """engine's inbox holds one handoff file per dict, from core's T1 on. Each holds the seven keys from before kinds, and that dict."""
        self.open("core")
        self.open("engine")
        inbox = self.dir("engine") / "inbox"
        inbox.mkdir()
        for number, extra in enumerate(extras, 1):
            (inbox / f"app~core~T{number}.json").write_text(json.dumps(
                {"handoff": f"app/core/T{number}", "summary": "Tidy the guide", "source": "user", "ref": "",
                 "priority": "low", "paths": "docs", "decision": "", **extra}))

    def take_handoff(self, **extra):
        """engine takes a handoff file that holds the seven keys from before kinds, and `extra`."""
        self.handed(extra)
        return self.brigade("engine", "ticket", "take")

    def unknown_kind(self, ident, quoted):
        """The warning of a take for the ticket `ident` whose handoff kind, in quotes, is `quoted`."""
        return (f"brigade: warning: {ident} came from app/core/{ident} with kind {quoted}, which is not one this version knows, "
                f"so it has no kind; run ticket set {ident} --kind <feature|fix|polish|upkeep|study>\n")

    def test_ticket_take_files_a_handoff_with_no_kind_key_as_a_ticket_with_no_kind_and_no_goal(self):
        self.assertEqual(self.take_handoff(), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][2:],
                         ["waiting", "user (from app/core/T1)", "", "", "Tidy the guide", "low", "docs", "", "", ""])

    def test_ticket_take_files_a_handoff_with_a_kind_as_a_ticket_of_that_kind(self):
        self.assertEqual(self.take_handoff(kind="upkeep"), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["upkeep", ""])

    def test_ticket_take_files_a_handoff_with_a_kind_that_is_none_of_the_five_as_a_ticket_with_no_kind_and_warns_on_standard_error(self):
        self.handed({"kind": "chore"})
        took = self.run_brigade("engine", "ticket", "take")
        self.assertEqual((took.returncode, took.stdout, took.stderr),
                         (0, "T1 from app/core/T1: Tidy the guide\n",
                          "brigade: warning: T1 came from app/core/T1 with kind 'chore', which is not one this version knows, "
                          "so it has no kind; run ticket set T1 --kind <feature|fix|polish|upkeep|study>\n"))
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["", ""])
        again = self.run_brigade("engine", "ticket", "take")
        self.assertEqual((again.returncode, again.stdout, again.stderr), (0, "nothing handed to you\n", ""))

    def test_inbox_take_warns_on_standard_error_of_a_handoff_kind_that_is_a_padded_fix(self):
        self.handed({"kind": " fix "})
        took = self.run_brigade("engine", "inbox", "take")
        self.assertEqual((took.returncode, took.stdout, took.stderr),
                         (0, "T1 from app/core/T1: Tidy the guide\n", self.unknown_kind("T1", "' fix '")))
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["", ""])

    def test_a_take_of_two_handoffs_with_unknown_kinds_prints_one_warning_for_each(self):
        self.handed({"kind": "chore"}, {"kind": "chore"})
        took = self.run_brigade("engine", "ticket", "take")
        self.assertEqual((took.returncode, took.stdout, took.stderr),
                         (0, "T1 from app/core/T1: Tidy the guide\nT2 from app/core/T2: Tidy the guide\n",
                          self.unknown_kind("T1", "'chore'") + self.unknown_kind("T2", "'chore'")))
        self.assertEqual([row[10:] for row in self.rows("engine", "rail.tsv")], [["", ""], ["", ""]])

    def test_ticket_move_of_a_ticket_with_a_bad_kind_cell_and_a_bad_goal_cell_warns_of_both_on_standard_error_and_again_on_a_second_move(self):
        self.open("core")
        self.open("engine")
        self.brigade("core", "ticket", "add", "--summary", "Tidy the guide", "--kind", "polish")
        rail = self.dir("core") / "rail.tsv"
        rail.write_text(rail.read_text().replace("\tpolish\t\n", "\tchore\tG9\n"))
        by_hand = "ticket set does not change this cell while T1 is moved; correct it in rail.tsv by hand\n"
        said = (0, "T1 moved to engine; no thread recorded for engine\n",
                "brigade: warning: T1 has kind 'chore', which is not feature, fix, polish, upkeep, or study; it reads as no kind; " + by_hand
                + "brigade: warning: T1 names goal 'G9', which has no row in goals.tsv; it reads as unassigned; " + by_hand)
        for _ in range(2):
            moved = self.run_brigade("core", "ticket", "move", "T1", "--to", "engine")
            self.assertEqual((moved.returncode, moved.stdout, moved.stderr), said)
        self.assertEqual(self.rows("core", "rail.tsv")[0][10:], ["chore", "G9"])
        self.assertEqual(json.loads((self.dir("engine") / "inbox" / "app~core~T1.json").read_text()),
                         {"handoff": "app/core/T1", "summary": "Tidy the guide", "source": "user", "ref": "",
                          "priority": "", "paths": "", "decision": ""})

    def test_ticket_take_files_a_handoff_whose_kind_is_empty_and_one_whose_kind_is_null_as_tickets_with_no_kind(self):
        self.open("core")
        self.open("engine")
        inbox = self.dir("engine") / "inbox"
        inbox.mkdir()
        for number, kind in ((1, ""), (2, None)):
            (inbox / f"app~core~T{number}.json").write_text(json.dumps(
                {"handoff": f"app/core/T{number}", "summary": f"Tidy guide {number}", "source": "user", "ref": "",
                 "priority": "", "paths": "", "decision": "", "kind": kind}))
        self.assertEqual(self.brigade("engine", "ticket", "take"),
                         "T1 from app/core/T1: Tidy guide 1\nT2 from app/core/T2: Tidy guide 2")
        self.assertEqual([row[10:] for row in self.rows("engine", "rail.tsv")], [["", ""], ["", ""]])

    def test_ticket_take_ignores_a_goal_key_in_the_handoff(self):
        self.assertEqual(self.take_handoff(kind="fix", goal="G1"), "T1 from app/core/T1: Tidy the guide")
        self.assertEqual(self.rows("engine", "rail.tsv")[0][10:], ["fix", ""])

    def test_ticket_set_with_no_flag_on_a_moved_ticket_prints_the_moved_refusal(self):
        self.handoff()
        self.assertEqual(self.brigade("core", "ticket", "set", "T1", ok=False),
                         "brigade: T1 moved to engine; it is that coordinator's ticket now")

    def test_ticket_set_kind_or_no_goal_on_a_moved_ticket_prints_the_moved_refusal(self):
        self.handoff()
        for flags in (("--kind", "polish"), ("--no-goal",)):
            self.assertEqual(self.brigade("core", "ticket", "set", "T1", *flags, ok=False),
                             "brigade: T1 moved to engine; it is that coordinator's ticket now")

    def assert_one_handed_ticket(self, name="engine"):
        self.assertEqual([row[2:] for row in self.rows(name, "rail.tsv")],
                         [["waiting", "github (from app/core/T1)", "https://github.com/o/r/issues/7", "", "Fix the cache", "", "", "", "fix", ""]])
        log = (self.dir(name) / "log.tsv").read_text()
        self.assertTrue(log.endswith("\n"))
        self.assertTrue(all(len(line.split("\t")) == 5 for line in log.splitlines()))
        self.assertEqual([row[1:] for row in self.rows(name, "log.tsv") if "from app/core/T1" in row[4]],
                         [["ticket", "T1", "waiting", "from app/core/T1"]])
        self.assertEqual(self.inbox(name), [])
        self.assertEqual(sorted(path.name for path in self.dir(name).glob("*.tmp")), [])

    def test_a_take_killed_before_its_log_row_finishes_on_the_next_take(self):
        self.handoff()
        died = self.child("die-before-log", "engine", "ticket", "take")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(len(self.rows("engine", "rail.tsv")), 1)
        self.assertEqual(self.rows("engine", "log.tsv"), [])
        self.brigade("engine", "ticket", "take")
        self.assert_one_handed_ticket()

    def test_a_take_killed_inside_the_log_append_recovers_the_tail(self):
        self.handoff()
        died = self.child("die-inside-log-append", "engine", "ticket", "take")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertRegex((self.dir("engine") / "log.tsv").read_text(), r"\n[^\n]+\tticket\tT1\twaiting\tfro$")
        self.brigade("engine", "ticket", "take")
        self.assert_one_handed_ticket()

    def test_a_failure_inside_the_temp_file_write_leaves_the_same_end_state(self):
        self.handoff()
        module = runpy.run_path(str(SCRIPT))
        glob = module["run"].__globals__

        class FailingTempfile:
            @staticmethod
            def NamedTemporaryFile(*args, **kwargs):
                handle = tempfile.NamedTemporaryFile(*args, **kwargs)
                write = handle.write

                def fail(text):
                    write(text[:10])
                    handle.flush()
                    raise OSError(28, "No space left on device")

                handle.write = fail
                return handle

        glob["tempfile"] = FailingTempfile
        with self.assertRaises(OSError):
            glob["run"](["--at", str(self.dir("engine")), "ticket", "take"])
        glob["tempfile"] = tempfile
        self.assertEqual(self.rows("engine", "rail.tsv"), [])
        self.assertEqual(self.inbox("engine"), ["app~core~T1.json"])
        self.brigade("engine", "ticket", "take")
        self.assert_one_handed_ticket()

    def test_a_joined_line_stops_the_read_with_the_malformed_message(self):
        self.open("core")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        log = self.dir("core") / "log.tsv"
        stamp = "2026-10-06T00:00:00.000000+00:00"
        log.write_text(log.read_text() + f"{stamp}\tticket\tT6\twaiting\tfro{stamp}\tticket\tT7\twaiting\tnote\n")
        self.assertEqual(self.brigade("core", "ticket", "list", ok=False), "brigade: app/core/log.tsv line 3 is malformed; fix or remove it")
        log.write_text(log.read_text().replace(f"fro{stamp}\tticket\tT7\twaiting\tnote", "fro"))
        self.assertEqual(self.brigade("core", "ticket", "list"), "T1 waiting normal fix [user] a")
        log.write_text(log.read_text() + f"{stamp[:19]}\tticket")
        self.assertEqual(self.brigade("core", "ticket", "list"), "T1 waiting normal fix [user] a")
        log.write_text(log.read_text() + f"{stamp}\tticket\tT8\twaiting\tnote\n")
        self.assertEqual(self.brigade("core", "ticket", "list", ok=False), "brigade: app/core/log.tsv line 4 is malformed; fix or remove it")
        log.write_text(log.read_text().replace(f"{stamp[:19]}\tticket{stamp}", "2026-10-06"))
        self.assertEqual(self.brigade("core", "ticket", "list", ok=False), "brigade: app/core/log.tsv line 4 is malformed; fix or remove it")

    def test_a_table_that_is_not_utf8_names_the_line(self):
        self.open("core")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        rail = self.dir("core") / "rail.tsv"
        rail.write_bytes(rail.read_bytes() + b"\xff\n")
        self.assertEqual(self.brigade("core", "ticket", "list", ok=False),
                         "brigade: app/core/rail.tsv line 3 is malformed; fix or remove it")

    def test_a_malformed_line_is_named_with_its_store_from_a_sibling_from_walk_and_from_the_store_itself(self):
        self.open("core")
        self.open("docs")
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "a")
        rail = self.dir("docs") / "rail.tsv"
        rail.write_text(rail.read_text() + "not a row\n")
        line = "brigade: app/docs/rail.tsv line 3 is malformed; fix or remove it"
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "x", "--source", "user", "--ref", "gh#1", ok=False),
                         line)
        self.assertEqual(self.brigade("core", "watch", ok=False), line)
        self.assertEqual(self.brigade("docs", "ticket", "list", ok=False), line)
        for words, cwd in ((("walk",), None), (("--at", ".", "ticket", "list"), self.dir("docs"))):
            result = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), *words],
                                    capture_output=True, text=True, cwd=cwd)
            self.assertEqual((result.returncode, result.stderr.strip()), (1, line), words)

    def test_walk_prints_every_other_store_and_exits_1_when_one_store_has_a_malformed_table(self):
        for name in ("core", "docs", "engine"):
            self.open(name)
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "b")
        rail = self.dir("docs") / "rail.tsv"
        rail.write_text(rail.read_text() + "not a row\n")
        walked = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "walk"], capture_output=True, text=True)
        self.assertEqual((walked.returncode, walked.stdout, walked.stderr), (1, "\n".join([
            f"{_shown_root(self.project)}: no landing contract",
            "  core (reports milestones): waiting tickets: 1",
            "    thread not recorded",
            "  docs: app/docs/rail.tsv line 3 is malformed; fix or remove it",
            "  engine (reports milestones): nothing on record",
            "    thread not recorded",
        ]) + "\n", "brigade: app/docs/rail.tsv line 3 is malformed; fix or remove it\n"))

    def test_walk_names_each_malformed_table_on_stderr_when_two_stores_have_one(self):
        for name in ("core", "docs", "engine"):
            self.open(name)
        rail = self.dir("core") / "rail.tsv"
        rail.write_text(rail.read_text() + "not a row\n")
        verdicts = self.dir("engine") / "pass.tsv"
        verdicts.write_text(verdicts.read_text() + "not a row\n")
        walked = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "walk"], capture_output=True, text=True)
        self.assertEqual((walked.returncode, walked.stdout, walked.stderr), (1, "\n".join([
            f"{_shown_root(self.project)}: no landing contract",
            "  core: app/core/rail.tsv line 2 is malformed; fix or remove it",
            "  docs (reports milestones): nothing on record",
            "    thread not recorded",
            "  engine: app/engine/pass.tsv line 2 is malformed; fix or remove it",
        ]) + "\n", "brigade: app/core/rail.tsv line 2 is malformed; fix or remove it\n"
                   "brigade: app/engine/pass.tsv line 2 is malformed; fix or remove it\n"))

    def test_a_tail_cut_inside_a_multi_byte_character_is_skipped_by_a_read_and_dropped_by_the_next_append(self):
        self.open("core")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        rail = self.dir("core") / "rail.tsv"
        complete = rail.read_bytes()
        rail.write_bytes(complete + "T9\t2026-10-10T00:00:00+00:00\twaiting\tuser\t\tcafé".encode()[:-1])
        self.assertEqual(self.brigade("core", "ticket", "list"), "T1 waiting normal fix [user] a")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "b"), "T2")
        after = rail.read_bytes()
        self.assertEqual(after[:len(complete)], complete)
        added = after[len(complete):].decode()
        self.assertEqual((added.count("\n"), added[-1]), (1, "\n"))
        fields = added[:-1].split("\t")
        self.assertEqual(fields[:1] + fields[2:], ["T2", "waiting", "user", "", "", "b", "", "", "", "fix", ""])

    def test_a_short_append_restores_the_last_complete_row(self):
        from unittest import mock
        self.open("core")
        self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "a")
        log = self.dir("core") / "log.tsv"
        complete = log.read_text()
        log.write_text(complete + "2026-10-06T00:00:00.000000+00:00\tticket\tT9\twai")
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        restaurant = glob["Restaurant"](self.dir("core"))
        row = {"at": "2026-10-06T00:00:01.000000+00:00", "kind": "ticket", "id": "T2", "state": "waiting", "note": "b"}
        real_write = os.write
        with mock.patch.object(os, "write", lambda fd, data: real_write(fd, data[:7])):
            with self.assertRaises(glob["BrigadeError"]) as raised:
                restaurant.append("log.tsv", row)
        self.assertEqual(str(raised.exception), "wrote 7 of 53 bytes to log.tsv; nothing appended")
        self.assertEqual(log.read_text(), complete)
        restaurant.append("log.tsv", row)
        self.assertEqual(log.read_text(), complete + "2026-10-06T00:00:01.000000+00:00\tticket\tT2\twaiting\tb\n")

    def test_two_processes_appending_200_rows_each_leave_400_rows(self):
        self.open("core")
        procs = [subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--race-child", "append",
                                   self.temporary.name, str(self.dir("core")), label],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
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


LAND_SCRIPT = ROOT / "t3/added/landing/scripts/land.py"


def _land_has_rulings():
    """Change 8's share, lease reserve, and contest commands, which the admin's rulings run."""
    for words in (("share",), ("lease", "reserve"), ("contest",)):
        result = subprocess.run([sys.executable, str(LAND_SCRIPT), *words, "--help"], capture_output=True, text=True)
        if result.returncode != 0:
            return False
    return True


class GoalWriteTest(StoresTest):
    """A goal command that is killed or that fails partway through its writes."""

    ADD = ("goal", "add", "--name", "activity view", "--purpose", "One page", "--done", "It ships")
    NO_UNASSIGNED_WORK = "unassigned: 0 waiting, 0 in flight, 0 merged, 0 send-backs"

    def goals(self):
        return [row[:1] + row[2:] for row in self.rows("core", "goals.tsv")]

    def goal_log(self):
        return [row[2:] for row in self.rows("core", "log.tsv") if row[1] == "goal"]

    def test_a_goal_add_killed_inside_its_append_leaves_a_tail_that_goal_list_skips_and_the_next_goal_add_drops(self):
        self.open("core")
        died = self.child("die-inside-goal-append", "core", *self.ADD)
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertRegex((self.dir("core") / "goals.tsv").read_text(), r"\tclosed\nG1\t[^\t\n]+\tope$")
        self.assertEqual(self.brigade("core", "goal", "list"), f"no open or paused goals\n{self.NO_UNASSIGNED_WORK}")
        self.assertEqual(self.brigade("core", *self.ADD), "G1")
        self.assertEqual(self.goals(), [["G1", "open", "activity view", "One page", "It ships", "", ""]])
        self.assertEqual(self.goal_log(), [["G1", "open", "activity view"]])

    def test_a_goal_add_killed_before_its_log_row_leaves_the_goal_and_the_same_command_again_is_refused_and_adds_no_row(self):
        self.open("core")
        died = self.child("die-before-log", "core", *self.ADD)
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.goals(), [["G1", "open", "activity view", "One page", "It ships", "", ""]])
        self.assertEqual(self.goal_log(), [])
        self.assertEqual(self.brigade("core", *self.ADD, ok=False), "brigade: same name as G1 (open); nothing added")
        self.assertEqual(len(self.goals()), 1)
        self.assertEqual(self.goal_log(), [])
        self.assertEqual(self.brigade("core", "goal", "list").splitlines()[0],
                         "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs")

    def test_a_goal_set_killed_before_its_log_row_leaves_the_new_state_and_the_same_command_again_prints_already(self):
        self.open("core")
        self.brigade("core", *self.ADD)
        died = self.child("die-before-log", "core", "goal", "set", "G1", "--state", "done", "--evidence", "shipped")
        self.assertEqual(died.returncode, 9, died.stderr)
        state, closed = self.goals()[0][1], self.goals()[0][6]
        self.assertEqual((state, closed.split(" ", 1)[1]), ("done", "shipped"))
        self.assertEqual(self.goal_log(), [["G1", "open", "activity view"]])
        self.assertEqual(self.brigade("core", "goal", "set", "G1", "--state", "done", "--evidence", "shipped"), "G1 already done")
        self.assertEqual(self.goals()[0][6], closed)
        self.assertEqual(self.goal_log(), [["G1", "open", "activity view"]])

    def test_a_goal_set_whose_temp_file_write_fails_leaves_goals_tsv_and_the_log_as_they_were_and_no_temp_file(self):
        self.open("core")
        self.brigade("core", *self.ADD)
        before = self.files("core")
        module = runpy.run_path(str(SCRIPT))
        glob = module["run"].__globals__

        class FailingTempfile:
            @staticmethod
            def NamedTemporaryFile(*args, **kwargs):
                handle = tempfile.NamedTemporaryFile(*args, **kwargs)
                write = handle.write

                def fail(text):
                    write(text[:10])
                    handle.flush()
                    raise OSError(28, "No space left on device")

                handle.write = fail
                return handle

        glob["tempfile"] = FailingTempfile
        with self.assertRaises(OSError):
            glob["run"](["--at", str(self.dir("core")), "goal", "set", "G1", "--state", "paused", "--share", "2"])
        glob["tempfile"] = tempfile
        self.assertEqual(self.files("core"), before)
        self.assertEqual(self.brigade("core", "goal", "set", "G1", "--state", "paused", "--share", "2"), "G1 paused; share 2")
        self.assertEqual(self.goals(), [["G1", "paused", "activity view", "One page", "It ships", "2", ""]])
        self.assertEqual(self.goal_log(), [["G1", "open", "activity view"], ["G1", "paused", "activity view"]])


class FireWriteTest(StoresTest):
    """A fire that is killed partway through its writes."""

    FIRE = ("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix bug")

    def before_kinds(self):
        """core holds T1, a waiting ticket as the tool wrote it before kinds and goals."""
        self.open("core")
        (self.dir("core") / "rail.tsv").write_text(
            "id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision\n"
            "T1\t2026-10-01T00:00:00+00:00\twaiting\tuser\t\t\tFix bug\t\t\t\n")

    def labelled(self, table):
        """The id, state, kind, and goal of each row of core's table. A row from before kinds has no kind and no goal."""
        header, *lines = (self.dir("core") / table).read_text().splitlines()
        rows = [dict(zip(header.split("\t"), line.split("\t"))) for line in lines]
        return [tuple(row.get(key, "") for key in ("id", "state", "kind", "goal")) for row in rows]

    def killed(self, mode, *fire):
        died = self.child(mode, "core", *fire)
        self.assertEqual((died.returncode, died.stdout, died.stderr), (9, "", ""))

    def test_a_fire_killed_before_its_log_row_leaves_the_item_and_its_lead_ticket_with_the_same_kind_and_status_warns_of_nothing(self):
        self.before_kinds()
        self.killed("die-before-log", *self.FIRE)
        self.assertEqual(self.labelled("dishes.tsv"), [("D1", "in-progress", "fix", "")])
        self.assertEqual(self.labelled("rail.tsv"), [("T1", "waiting", "fix", "")])
        self.assertEqual(self.rows("core", "log.tsv"), [])
        status = self.run_brigade("core", "status")
        self.assertEqual((status.returncode, status.stderr), (0, ""))
        self.assertEqual(status.stdout.splitlines()[3:], ["kinds: fix 1 waiting, 1 in flight"])

    def test_a_fire_killed_before_its_log_row_is_dropped_and_the_same_fire_again_starts_the_ticket_with_the_same_kind(self):
        self.before_kinds()
        self.killed("die-before-log", *self.FIRE)
        self.assertEqual(self.brigade("core", "dish", "D1", "--state", "dropped"), "D1 dropped")
        self.assertEqual(self.labelled("rail.tsv"), [("T1", "waiting", "fix", "")])
        self.assertEqual(self.brigade("core", *self.FIRE), "D2")
        self.assertEqual(self.labelled("dishes.tsv"), [("D1", "dropped", "fix", ""), ("D2", "in-progress", "fix", "")])
        self.assertEqual(self.labelled("rail.tsv"), [("T1", "assigned", "fix", "")])

    def test_a_fire_killed_before_its_item_row_leaves_the_lead_ticket_waiting_with_the_kind_and_no_item_and_the_same_fire_again_starts_it(self):
        self.before_kinds()
        self.killed("die-before-item", *self.FIRE)
        self.assertEqual(self.labelled("dishes.tsv"), [])
        self.assertEqual(self.labelled("rail.tsv"), [("T1", "waiting", "fix", "")])
        self.assertEqual(self.rows("core", "log.tsv"), [])
        self.assertEqual(self.brigade("core", *self.FIRE), "D1")
        self.assertEqual(self.labelled("dishes.tsv"), [("D1", "in-progress", "fix", "")])
        self.assertEqual(self.labelled("rail.tsv"), [("T1", "assigned", "fix", "")])

    def test_a_fire_with_kind_killed_before_its_item_row_leaves_a_lead_ticket_whose_kind_ticket_set_changes(self):
        self.before_kinds()
        self.killed("die-before-item", *self.FIRE, "--kind", "polish")
        self.assertEqual((self.labelled("dishes.tsv"), self.labelled("rail.tsv")), ([], [("T1", "waiting", "polish", "")]))
        changed = self.run_brigade("core", "ticket", "set", "T1", "--kind", "fix")
        self.assertEqual((changed.returncode, changed.stdout, changed.stderr), (0, "T1 kind fix\n", ""))
        self.assertEqual(self.labelled("rail.tsv"), [("T1", "waiting", "fix", "")])


class AdminTest(StoresTest):
    """The executive admin at `app/.admin`, beside the coordinators on the same root."""

    STAMP = "2026-10-06T00:00:00Z"

    def admin(self, *args, ok=True):
        return self.brigade(".admin", *args, ok=ok)

    def open_admin(self, *extra, ok=True):
        return self.brigade(".admin", "open", "--admin", "--project-root", str(self.project), *extra, ok=ok)

    def meta(self, name):
        return json.loads((self.dir(name) / "restaurant.json").read_text())

    def test_status_in_the_executive_admins_store_prints_the_kinds_line_and_the_goals_line_after_its_counts_line(self):
        self.open_admin()
        self.admin("goal", "add", "--name", "one inbox", "--purpose", "p", "--done", "d")
        self.admin("ticket", "add", "--kind", "upkeep", "--summary", "Add a FAQ", "--goal", "G1")
        self.assertEqual(self.admin("status").splitlines(),
                         ["thread not recorded", "reporting: milestones, no landing contract, waiting tickets: 1",
                          "kinds: upkeep 1 waiting", "goals: G1 1 waiting"])

    def test_sync_relays_a_coordinators_goal_log_row_and_the_admins_close_prints_it_in_that_coordinators_block(self):
        self.open("docs")
        self.open_admin()
        self.brigade("docs", "goal", "add", "--name", "activity view", "--purpose", "p", "--done", "d")
        self.assertRegex(self.admin("sync"), r"^docs \S+ goal G1 open: activity view$")
        self.assertEqual([(row["kind"], row["id"], row["state"], row["note"]) for _, row in self.relays()],
                         [("goal", "G1", "open", "activity view")])
        report = self.admin("close", "--dry-run").splitlines()
        block = report[report.index("### docs") + 2]
        self.assertRegex(block, r"^- \S+ goal G1 open: activity view$")

    def test_sync_and_close_hide_a_doc_row_but_keep_its_relay(self):
        self.open("docs")
        self.open_admin()
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "Work")
        self.brigade("docs", "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Work")
        self.assertNotEqual(self.admin("sync"), "nothing new")
        before_close = self.admin("close", "--dry-run")
        self.assertEqual(self.admin("sync"), "nothing new")
        log = self.dir("docs") / "log.tsv"
        log.write_text(log.read_text() + "2026-10-12T00:00:00+00:00\tdoc\tD1\titem\tdoc-item\n")
        self.assertEqual(self.admin("sync"), "nothing new")
        self.assertEqual(self.admin("close", "--dry-run"), before_close)
        self.assertIn(("doc", "D1", "item", "doc-item"),
                      [(row["kind"], row["id"], row["state"], row["note"]) for _, row in self.relays()])
        self.assertNotIn(" doc ", self.admin("close", "--dry-run"))

    def test_goal_add_goal_set_and_goal_list_work_in_the_executive_admins_store(self):
        self.open_admin()
        self.assertEqual(self.admin("goal", "add", "--name", "routing", "--purpose", "Requests reach an owner", "--done", "Standing",
                                    "--share", "1"), "G1")
        self.assertEqual(self.admin("goal", "set", "G1", "--state", "paused"), "G1 paused")
        self.assertEqual(self.admin("goal", "list"), "\n".join([
            "G1 paused routing: 0 waiting, 0 in flight, 0 merged, 0 send-backs, share 1",
            "  purpose: Requests reach an owner",
            "  done when: Standing",
            "unassigned: 0 waiting, 0 in flight, 0 merged, 0 send-backs"]))
        self.assertEqual([row[1:] for row in self.rows(".admin", "log.tsv") if row[1] == "goal"],
                         [["goal", "G1", "open", "routing"], ["goal", "G1", "paused", "routing"]])

    def test_the_executive_admins_ticket_add_needs_a_kind_and_takes_a_goal_and_its_moved_ticket_carries_the_kind(self):
        self.open_admin()
        self.open("core")
        self.admin("goal", "add", "--name", "routing", "--purpose", "p", "--done", "d")
        self.assertEqual(self.admin("ticket", "add", "--summary", "Fix the cache", ok=False),
                         "brigade: ticket add needs --kind <feature|fix|polish|upkeep|study>")
        self.assertEqual(self.admin("ticket", "add", "--summary", "Fix the cache", "--kind", "fix", "--goal", "G1"), "T1")
        self.assertEqual(self.rows(".admin", "rail.tsv")[0][10:], ["fix", "G1"])
        self.assertEqual(self.admin("ticket", "set", "T1", "--no-goal"), "T1 unassigned")
        self.assertEqual(self.admin("ticket", "set", "T1", "--goal", "G1"), "T1 goal G1")
        self.assertEqual(self.admin("ticket", "move", "T1", "--to", "core"),
                         "T1 moved to core; no thread recorded for core; it leaves G1 here and arrives unassigned")
        self.brigade("core", "ticket", "take")
        self.assertEqual(self.rows("core", "rail.tsv")[0][10:], ["fix", ""])

    def relays(self):
        """The admin's relay rows as (store@offset, the copied row)."""
        return [(row[2], json.loads(row[4])) for row in self.rows(".admin", "log.tsv") if row[1] == "relay"]

    def ruling_events(self):
        """Ruling log rows as (id, state, note)."""
        return [tuple(row[2:5]) for row in self.rows(".admin", "log.tsv") if row[1] == "ruling"]

    def cursor(self, name):
        return self.meta(".admin").get("cursors", {}).get(f"app/{name}", 0)

    def size(self, name, table="log.tsv"):
        return len((self.dir(name) / table).read_bytes())

    def admin_child(self, mode, label, *args):
        """A race child running brigade.py on the admin store, started and not yet finished."""
        case = Path(self.temporary.name) / label
        case.mkdir()
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--race-child", mode, str(case),
                                 "--store", str(self.store), "--at", str(self.dir(".admin")), *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return case, proc

    def append_row(self, name, *fields):
        """Restaurant.append of one log.tsv row in its own process, started and not yet finished."""
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--race-child", "append-row", self.temporary.name,
                                 str(self.dir(name)), *fields],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def finish(self, proc):
        out, err = proc.communicate(timeout=30)
        return proc.returncode, out.strip(), err.strip()

    def with_thread(self, thread="t1"):
        """docs, engine, and the admin, with the admin's thread recorded at generation 1."""
        self.open("docs")
        self.open("engine")
        self.open_admin()
        self.admin("set", "--thread", thread)

    # The store.

    def test_two_admin_opens_at_once_leave_one_store(self):
        procs = [subprocess.Popen([sys.executable, str(SCRIPT), "--store", str(self.store), "open", "--admin",
                                   "--project-root", str(self.project)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        words = sorted(proc.communicate(timeout=30)[0].split()[0] for proc in procs)
        self.assertEqual(words, ["exists", "opened"])
        self.assertEqual(sorted(path.name for path in (self.store / "app").iterdir()), [".admin"])
        meta = self.meta(".admin")
        self.assertEqual((meta["role"], meta["projectRoot"]), ("admin", str(self.project)))
        self.assertTrue((self.dir(".admin") / "rulings.tsv").is_file())
        self.assertIn("## Priorities", (self.dir(".admin") / "menu.md").read_text())

    def test_open_admin_refuses_a_second_root_with_the_same_directory_name(self):
        other = Path(self.temporary.name) / "other" / "app"
        other.mkdir(parents=True)
        self.open_admin()
        before = (self.dir(".admin") / "restaurant.json").read_bytes()
        self.assertEqual(self.brigade(".admin", "open", "--admin", "--project-root", str(other), ok=False),
                         f"brigade: {self.dir('.admin')} already holds a coordinator for {self.project}; pick another --name")
        self.assertEqual((self.dir(".admin") / "restaurant.json").read_bytes(), before)
        self.assertEqual(self.open_admin("--name", "boss", ok=False), "brigade: open --admin takes no --name")
        self.assertEqual(self.brigade("docs", "open", "--project-root", str(self.project), ok=False),
                         "brigade: open needs --name, or --admin")

    def test_open_admin_prints_every_coordinator_and_walk_puts_it_first(self):
        self.open("docs")
        (self.dir("docs") / "menu.md").write_text("## Purpose\n\nKeep the docs right.\n\n## Off the menu\n\nEngine work\n")
        self.assertEqual(self.open_admin("--reporting", "digest"), "\n".join([
            f"opened {self.dir('.admin')}",
            f"sibling docs ({self.dir('docs')}), thread not recorded",
            "  purpose: Keep the docs right.",
            "  off the menu: Engine work",
        ]))
        self.open("engine")
        self.assertIn(f"sibling executive admin ({self.dir('.admin')}), thread not recorded",
                      self.brigade("engine", "open", "--project-root", str(self.project), "--name", "engine"))
        walked = self.brigade("docs", "walk", "--repo", str(self.project)).splitlines()
        self.assertEqual(walked[1:], [
            "  executive admin (reports digest): nothing on record",
            "    thread not recorded",
            "  docs (reports milestones): nothing on record",
            "    thread not recorded",
            "  engine (reports milestones): nothing on record",
            "    thread not recorded",
        ])

    def test_the_admin_store_refuses_work_commands(self):
        self.open_admin()
        refusal = "brigade: the executive admin routes work and never runs it"
        for args in (("fire", "--tickets", "T1", "--station", "feature", "--summary", "s"),
                     ("brief", "D1", "--contract", "none", "--goal", "g", "--verify", "v", "--base", "main", "--acceptance", "a"),
                     ("contract", "D1"),
                     ("edits", "verify", "D1", "--from", "abc", "--to", "def"),
                     ("dish", "D1", "--state", "merged"),
                     ("dish", "D1", "--state", "dropped", "--stopped", "r1"),
                     ("pass", "check", "D1", "--sha", "abc"),
                     ("watch",)):
            self.assertEqual(self.admin(*args, ok=False), refusal, args)
        self.assertEqual(self.admin("ticket", "add", "--kind", "fix", "--summary", "Add a FAQ"), "T1")
        self.assertEqual(self.admin("86", "add", "--question", "Which purpose?", "--options", "docs, engine",
                                    "--default", "docs"), "Q1")
        self.assertEqual(self.admin("status"),
                         "thread not recorded\nreporting: milestones, no landing contract, waiting tickets: 1, decisions for you: 1\n"
                         "kinds: fix 1 waiting")

    def test_a_ref_moved_through_the_admin_frees_at_every_step_once_done(self):
        ref = "https://github.com/o/r/issues/7"
        self.open("core", "--intake", "github")
        for name in ("docs", "engine"):
            self.open(name)
        self.open_admin()

        def refused(ident):
            self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "again", "--source", "github", "--ref", ref,
                                          ok=False), f"brigade: {ref} is already {ident} (moved); nothing added")

        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix", "--source", "github", "--ref", ref), "T1")
        self.brigade("core", "ticket", "move", "T1", "--to", ".admin")
        refused("T1")
        self.assertEqual(self.admin("inbox", "take"), "T1 from app/core/T1: Fix")
        refused("T1")
        self.assertEqual(self.admin("ticket", "move", "T1", "--to", "docs"), "T1 moved to docs; no thread recorded for docs")
        refused("T1")
        self.assertEqual(self.brigade("docs", "inbox", "take"), "T1 from app/.admin/T1: Fix")
        refused("T1")
        self.brigade("docs", "ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix", "--source", "github", "--ref", ref), "T2")
        self.brigade("core", "ticket", "move", "T2", "--to", ".admin")
        self.assertEqual(self.admin("inbox", "take"), "T2 from app/core/T2: Fix")
        self.admin("ticket", "move", "T2", "--to", "docs")
        self.assertEqual(self.brigade("docs", "inbox", "take"), "T2 from app/.admin/T2: Fix")
        self.brigade("docs", "ticket", "move", "T2", "--to", ".admin")
        refused("T2")
        self.assertEqual(self.admin("inbox", "take"), "T3 from app/docs/T2: Fix")
        refused("T2")
        self.admin("ticket", "move", "T3", "--to", "engine")
        refused("T2")
        self.assertEqual(self.brigade("engine", "ticket", "take"), "T1 from app/.admin/T3: Fix")
        refused("T2")
        self.brigade("engine", "ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", "Fix", "--source", "github", "--ref", ref), "T3")

    def test_a_tracker_ref_the_admin_files_is_refused_again_and_reaches_the_coordinator_unchanged(self):
        ref = "app-7f3"
        self.open("engine")
        self.open_admin("--intake", "beads")

        def refused(store, ident, where=""):
            self.assertEqual(self.brigade(store, "ticket", "add", "--kind", "fix", "--summary", "again", "--source", "user", "--ref", ref,
                                          ok=False), f"brigade: {ref} is already {ident}{where}; nothing added")

        self.assertEqual(self.admin("ticket", "add", "--kind", "fix", "--summary", "Fix login", "--source", "beads", "--ref", ref), "T1")
        self.assertEqual(self.brigade("engine", "ticket", "add", "--kind", "fix", "--summary", "Fix login", "--source", "beads", "--ref", ref,
                                      ok=False), "brigade: .admin owns intake from beads; ask it to file this and move it here")
        refused(".admin", "T1", " (waiting)")
        refused("engine", "T1", " in .admin (waiting)")
        self.assertEqual(self.admin("ticket", "move", "T1", "--to", "engine"), "T1 moved to engine; no thread recorded for engine")
        refused(".admin", "T1", " (moved)")
        self.assertEqual(self.brigade("engine", "inbox", "take"), "T1 from app/.admin/T1: Fix login")
        self.assertEqual(self.brigade("engine", "ticket", "list"), "T1 waiting normal fix [beads (from app/.admin/T1)] Fix login app-7f3")
        refused("engine", "T1", " (waiting)")
        refused(".admin", "T1", " (moved)")

    def test_rule_overrule_leaves_two_rows_and_close_lists_both(self):
        self.open("docs")
        self.open_admin()
        self.assertEqual(self.brigade("docs", "rule", "list", ok=False), "brigade: rule works only in the executive admin's store")
        self.assertEqual(self.admin("rule", "add", "--kind", "contested-paths", "--parties", "docs,engine",
                                    "--question", "Who claims README.md next?", "--rule", "age",
                                    "--decision", "docs claims README.md next"), "R1")
        self.assertEqual(self.admin("rule", "overrule", "R1", "--decision", "engine goes first"), "R2")
        rows = self.rows(".admin", "rulings.tsv")
        self.assertEqual([(row[0], row[2], row[3], row[5], row[6], row[7], row[8]) for row in rows], [
            ("R1", "contested-paths", "docs, engine", "age", "docs claims README.md next", "", "overruled"),
            ("R2", "contested-paths", "docs, engine", "user", "engine goes first", "R1", "in-force"),
        ])
        self.assertEqual(self.admin("rule", "list"), "\n".join([
            "R1 overruled contested-paths (docs, engine): Who claims README.md next? Decided by age: docs claims README.md next.",
            "R2 in-force contested-paths (docs, engine): Who claims README.md next? Decided by user: engine goes first. Supersedes R1.",
        ]))
        self.assertEqual(self.admin("rule", "list", "--state", "in-force").splitlines()[0][:12], "R2 in-force ")
        report = self.admin("close")
        self.assertIn("\n".join([
            "## Rulings", "",
            "- R1 overruled contested-paths (docs, engine): Who claims README.md next? Decided by age: docs claims README.md next.",
            "- R2 in-force contested-paths (docs, engine): Who claims README.md next? Decided by user: engine goes first. Supersedes R1.",
        ]), report)
        self.assertNotIn("## Rulings", self.admin("close"))
        self.assertEqual(self.admin("rule", "add", "--kind", "ownership", "--parties", "docs,engine", "--question", "Who owns T4?",
                                    "--rule", "priority", "--decision", "docs", "--supersedes", "R2"), "R3")
        self.assertEqual(self.admin("rule", "set", "R1", "--state", "done", ok=False),
                         "brigade: R1 is overruled; only an in-force ruling ends")
        self.assertEqual(self.admin("rule", "set", "R3", "--state", "expired"), "R3 expired")
        self.assertEqual(self.admin("rule", "set", "R3", "--state", "expired"), "R3 expired")
        self.assertEqual([row[8] for row in self.rows(".admin", "rulings.tsv")], ["overruled", "superseded", "expired"])
        self.assertEqual(self.admin("rule", "list", "--state", "in-force"), "no rulings")
        self.assertIn("invalid choice: 'guess'", self.admin("rule", "add", "--kind", "ownership", "--parties", "docs",
                                                          "--question", "q", "--rule", "guess", "--decision", "d", ok=False))

    def _ruling_section(self, *lines):
        return "\n".join(["## Rulings", "", *lines])

    def test_a_ruling_killed_before_its_log_row_is_named_by_the_next_close(self):
        self.open_admin()
        died = self.child("die-before-log", ".admin", "rule", "add", "--kind", "ownership", "--parties", "docs",
                          "--question", "Who owns this?", "--rule", "priority", "--decision", "docs")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.admin("rule", "list"),
                         "R1 in-force ownership (docs): Who owns this? Decided by priority: docs.")
        section = self._ruling_section(
            "- R1 in-force ownership (docs): Who owns this? Decided by priority: docs.")
        self.assertIn(section, self.admin("close", "--dry-run"))
        self.assertEqual(self.ruling_events(), [])
        self.assertIn(section, self.admin("close"))
        self.assertEqual(self.ruling_events(), [("R1", "in-force", "docs")])
        self.assertNotIn("## Rulings", self.admin("close"))

    def test_an_overrule_killed_before_its_log_rows_is_named_by_the_next_close(self):
        self.open_admin()
        self.admin("rule", "add", "--kind", "contested-paths", "--parties", "docs,engine",
                   "--question", "Who claims README.md next?", "--rule", "age",
                   "--decision", "docs claims README.md next")
        died = self.child("die-before-log", ".admin", "rule", "overrule", "R1", "--decision", "engine goes first")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.admin("rule", "list"), "\n".join([
            "R1 overruled contested-paths (docs, engine): Who claims README.md next? Decided by age: docs claims README.md next.",
            "R2 in-force contested-paths (docs, engine): Who claims README.md next? Decided by user: engine goes first. Supersedes R1.",
        ]))
        section = self._ruling_section(
            "- R1 overruled contested-paths (docs, engine): Who claims README.md next? Decided by age: docs claims README.md next.",
            "- R2 in-force contested-paths (docs, engine): Who claims README.md next? Decided by user: engine goes first. Supersedes R1.",
        )
        self.assertIn(section, self.admin("close", "--dry-run"))
        self.assertEqual(self.ruling_events(), [("R1", "in-force", "docs claims README.md next")])
        self.assertIn(section, self.admin("close"))
        self.assertEqual(self.ruling_events(), [
            ("R1", "in-force", "docs claims README.md next"),
            ("R2", "in-force", "engine goes first"),
            ("R1", "overruled", "replaced by R2"),
        ])
        self.assertNotIn("## Rulings", self.admin("close"))

    def test_an_ended_ruling_killed_before_its_log_row_is_named_by_the_next_close(self):
        self.open_admin()
        self.admin("rule", "add", "--kind", "ownership", "--parties", "docs",
                   "--question", "Who owns this?", "--rule", "priority", "--decision", "docs")
        died = self.child("die-before-log", ".admin", "rule", "set", "R1", "--state", "done")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.admin("rule", "list"),
                         "R1 done ownership (docs): Who owns this? Decided by priority: docs.")
        section = self._ruling_section(
            "- R1 done ownership (docs): Who owns this? Decided by priority: docs.")
        self.assertIn(section, self.admin("close", "--dry-run"))
        self.assertEqual(self.ruling_events(), [("R1", "in-force", "docs")])
        self.assertIn(section, self.admin("close"))
        self.assertEqual(self.ruling_events(), [
            ("R1", "in-force", "docs"),
            ("R1", "done", "docs (done)"),
        ])
        self.assertNotIn("## Rulings", self.admin("close"))

    def test_the_next_rule_add_logs_a_killed_ruling_once(self):
        self.open_admin()
        died = self.child("die-before-log", ".admin", "rule", "add", "--kind", "ownership", "--parties", "docs",
                          "--question", "Who owns this?", "--rule", "priority", "--decision", "docs")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.admin("rule", "add", "--kind", "ownership", "--parties", "engine",
                                    "--question", "Who owns T4?", "--rule", "priority", "--decision", "engine"), "R2")
        self.assertEqual(self.ruling_events(), [
            ("R1", "in-force", "docs"),
            ("R2", "in-force", "engine"),
        ])

    # Requests.

    def test_a_request_waits_in_the_inbox_until_done(self):
        self.open("docs")
        self.open_admin()
        self.brigade("docs", "set", "--thread", "th-docs")
        self.assertEqual(self.brigade("docs", "request", "--to", "docs", "x", ok=False),
                         "brigade: request works only in the executive admin's store")
        self.assertEqual(self.admin("request", "--to", "engine", "x", ok=False),
                         f"brigade: engine is not a sibling coordinator on {self.project}")
        line = "from-user docs: add a FAQ"
        self.assertEqual(self.admin("request", "--to", "docs", line), "A1 for docs; tell thread th-docs")
        self.assertEqual(self.inbox("docs"), ["A1.line"])
        # A failed send leaves the file. The coordinator's next wake prints it again.
        for _ in range(2):
            self.assertEqual(self.brigade("docs", "inbox", "take"), f"A1: {line}")
        self.assertIn("requests from the executive admin: 1", self.brigade("docs", "status"))
        self.assertEqual(self.brigade("docs", "inbox", "done", "A1"), "A1 done")
        self.assertEqual(self.brigade("docs", "inbox", "done", "A1"), "A1 done")
        self.assertEqual(self.brigade("docs", "inbox", "done", "A2", ok=False), "brigade: no request A2 in the inbox")
        self.assertEqual(self.inbox("docs"), [])
        self.assertEqual(self.brigade("docs", "inbox", "take"), "nothing handed to you")
        self.assertNotIn("requests from", self.brigade("docs", "status"))
        self.assertEqual([row[1:4] for row in self.rows("docs", "log.tsv") if row[1] == "inbox-done"], [["inbox-done", "A1", "done"]])
        self.assertEqual([row[1:] for row in self.rows(".admin", "log.tsv") if row[1] == "request"],
                         [["request", "A1", "sent", f"to docs: {line}"]])

    def test_status_walk_and_close_count_every_pending_request_as_from_the_executive_admin(self):
        self.open("docs")
        self.open_admin()
        self.admin("request", "--to", "docs", "reports-to docs th-admin")
        self.admin("request", "--to", "docs", "from-user docs: add a FAQ")
        for printed in (self.brigade("docs", "status"), self.brigade("docs", "walk", "--repo", str(self.project)),
                        self.brigade("docs", "close")):
            self.assertIn("requests from the executive admin: 2", printed)
            self.assertNotIn("requests from the user", printed)

    def test_a_request_killed_before_its_file_is_republished_once(self):
        self.open("docs")
        self.open_admin()
        died = self.child("die-before-publish", ".admin", "request", "--to", "docs", "from-user docs: add a FAQ")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.inbox("docs"), [])
        self.assertEqual(self.admin("request", "--republish"), "A1 republished for docs")
        self.assertEqual(self.admin("request", "--republish"), "nothing to republish")
        self.assertEqual(self.inbox("docs"), ["A1.line"])
        self.brigade("docs", "inbox", "done", "A1")
        self.assertEqual(self.admin("request", "--republish"), "nothing to republish")
        self.assertEqual(self.inbox("docs"), [])
        self.admin("sync")
        self.assertEqual(self.admin("request", "--republish"), "nothing to republish")
        self.assertEqual(self.inbox("docs"), [])

    def test_republish_before_sync_skips_a_request_the_coordinator_finished(self):
        self.open("core")
        self.open_admin()
        self.brigade("core", "set", "--thread", "core-thread")
        self.admin("set", "--thread", "admin-thread")
        self.assertEqual(self.admin("request", "--to", "core", "reports-to core admin-thread"),
                         "A1 for core; tell thread core-thread")
        self.assertEqual(self.brigade("core", "inbox", "take"), "A1: reports-to core admin-thread")
        self.assertEqual(self.brigade("core", "inbox", "done", "A1"), "A1 done")
        self.assertEqual(self.admin("request", "--republish"), "nothing to republish")
        self.assertEqual(self.inbox("core"), [])
        self.assertNotIn("requests from", self.brigade("core", "status"))

    def test_a_request_finished_while_republish_runs_stays_finished(self):
        self.open("core")
        self.open_admin()
        self.brigade("core", "set", "--thread", "core-thread")
        self.admin("request", "--to", "core", "reports-to core admin-thread")
        case, proc = self.admin_child("pause-after-finished", "republish", "request", "--republish")
        _wait_for_path(case / "paused", timeout=20)
        self.assertEqual(self.brigade("core", "inbox", "done", "A1"), "A1 done")
        self.assertEqual(self.inbox("core"), [])
        (case / "proceed").touch()
        code, out, err = self.finish(proc)
        self.assertEqual(code, 0, err)
        self.assertNotIn("requests from", self.brigade("core", "status"))
        self.assertEqual(self.brigade("core", "inbox", "take"), "nothing handed to you")
        self.assertEqual(self.inbox("core"), [])
        self.assertEqual(self.admin("request", "--republish"), "nothing to republish")
        self.assertEqual(self.inbox("core"), [])

    def test_a_replayed_from_user_request_files_no_second_ticket(self):
        self.open("docs")
        self.assertEqual(self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "Add a FAQ", "--request", "A1"), "T1")
        refusal = "brigade: request A1 is already T1; nothing added"
        self.assertEqual(self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "Add a FAQ", "--request", "A1", ok=False), refusal)
        self.brigade("docs", "ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "Add a FAQ", "--request", "A1", ok=False), refusal)
        self.assertEqual(self.brigade("docs", "ticket", "list"), "T1 done normal fix [user (request A1)] Add a FAQ")
        self.assertEqual(self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "Other", "--request", "A2"), "T2")

    # Sync.

    def test_sync_relays_each_row_once_and_a_kill_before_the_cursor_copies_nothing_twice(self):
        self.open("docs")
        self.open_admin()
        start = self.size("docs")
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "one")
        sync = self.admin("sync")
        self.assertRegex(sync, r"^docs \S+ ticket T1 waiting: one$")
        self.assertEqual([(ident, row["kind"], row["id"], row["state"], row["note"]) for ident, row in self.relays()],
                         [(f"app/docs@{start}", "ticket", "T1", "waiting", "one")])
        self.assertEqual(self.cursor("docs"), self.size("docs"))
        self.assertEqual(self.admin("sync"), "nothing new")
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "two")
        before = self.cursor("docs")
        died = self.child("die-before-cursor", ".admin", "sync")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual(self.cursor("docs"), before)
        self.assertEqual(len(self.relays()), 2)
        self.assertEqual(self.admin("sync"), "nothing new")
        self.assertEqual([row["note"] for _, row in self.relays()], ["one", "two"])
        self.assertEqual(self.cursor("docs"), self.size("docs"))

    def test_a_row_appended_while_sync_runs_is_relayed_once_even_with_an_older_timestamp(self):
        self.open("docs")
        self.open_admin()
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "one")
        case, proc = self.admin_child("pause", "sync", "sync")
        _wait_for_path(case / "paused", timeout=20)
        older = "2020-01-01T00:00:00Z"
        appended = self.append_row("docs", older, "ticket", "T9", "waiting", "late")
        self.assertEqual(self.finish(appended)[0], 0)
        (case / "proceed").touch()
        code, out, err = self.finish(proc)
        self.assertEqual(code, 0, err)
        self.assertEqual([row["note"] for _, row in self.relays()], ["one"])
        self.assertRegex(self.admin("sync"), rf"^docs {older} ticket T9 waiting: late$")
        self.assertEqual(self.admin("sync"), "nothing new")
        self.assertEqual([row["note"] for _, row in self.relays()], ["one", "late"])

    def test_an_older_sync_finishing_late_never_moves_the_cursor_back(self):
        self.open("docs")
        self.open_admin()
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "one")
        case, outer = self.admin_child("pause", "outer", "sync")
        _wait_for_path(case / "paused", timeout=20)
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "two")
        self.admin("sync")
        consumed = self.size("docs")
        self.assertEqual(self.cursor("docs"), consumed)
        (case / "proceed").touch()
        code, out, err = self.finish(outer)
        self.assertEqual((code, out), (0, "nothing new"), err)
        self.assertEqual(self.cursor("docs"), consumed)
        self.assertEqual([row["note"] for _, row in self.relays()], ["one", "two"])

    def test_sync_leaves_an_unfinished_tail_and_relays_the_repaired_row_once(self):
        self.open("engine")
        self.open("core")
        self.open_admin()
        for number in range(1, 7):
            self.brigade("engine", "ticket", "add", "--kind", "fix", "--summary", f"engine {number}")
        for number in range(1, 6):
            self.brigade("core", "ticket", "add", "--kind", "fix", "--summary", f"core {number}")
        self.brigade("engine", "ticket", "move", "T6", "--to", "core")
        self.admin("sync")
        died = self.child("die-inside-log-append", "core", "ticket", "take")
        self.assertEqual(died.returncode, 9, died.stderr)
        tail = self.cursor("core")
        self.assertRegex((self.dir("core") / "log.tsv").read_bytes()[tail:].decode(), r"^[^\n]+\tticket\tT6\twaiting\tfro$")
        relayed = len(self.relays())
        self.assertEqual(self.admin("sync"), "nothing new")
        self.assertEqual((len(self.relays()), self.cursor("core")), (relayed, tail))
        self.brigade("core", "ticket", "take")
        self.admin("sync")
        new = self.relays()[relayed:]
        self.assertEqual([(ident, row["kind"], row["id"], row["state"], row["note"]) for ident, row in new],
                         [(f"app/core@{tail}", "ticket", "T6", "waiting", "from app/engine/T6")])

    def test_a_joined_line_stops_sync_at_that_line(self):
        self.open("docs")
        self.open_admin()
        self.admin("sync")
        log = self.dir("docs") / "log.tsv"
        good = f"{self.STAMP}\tticket\tT1\twaiting\tgood\n"
        joined = f"{self.STAMP}\tticket\tT6\twaiting\tfro{self.STAMP}\tticket\tT7\twaiting\tnote\n"
        at = self.size("docs") + len(good)
        log.write_text(log.read_text() + good + joined + f"{self.STAMP}\tticket\tT8\twaiting\tafter\n")
        self.assertEqual(self.admin("sync", ok=False), f"brigade: app/docs/log.tsv at byte {at} is malformed; nothing past it relayed")
        self.assertEqual(self.cursor("docs"), at)
        self.assertEqual([row["note"] for _, row in self.relays()], ["good"])
        self.assertEqual(self.admin("sync", ok=False), f"brigade: app/docs/log.tsv at byte {at} is malformed; nothing past it relayed")
        self.assertEqual([row["note"] for _, row in self.relays()], ["good"])

    def test_a_reader_racing_a_tail_repair_relays_only_the_repaired_row(self):
        self.open("core")
        self.open_admin()
        self.admin("sync")
        log = self.dir("core") / "log.tsv"
        tail = self.size("core")
        self.assertEqual(self.cursor("core"), tail)
        log.write_bytes(log.read_bytes() + f"{self.STAMP}\tticket\tT6\twaiting\tfro".encode())
        case, sync = self.admin_child("read16", "reader", "sync")
        _wait_for_path(case / "paused", timeout=20)
        append = self.append_row("core", "2026-10-06T00:00:01Z", "ticket", "T7", "waiting", "from app/engine/T7")
        time.sleep(1)
        self.assertIsNone(append.poll(), "the append waits for the reader's lock")
        (case / "proceed").touch()
        code, out, err = self.finish(sync)
        self.assertEqual((code, out), (0, "nothing new"), err)
        self.assertEqual(self.cursor("core"), tail)
        self.assertEqual(self.finish(append)[0], 0)
        self.assertEqual(self.relays(), [])
        self.admin("sync")
        self.assertEqual(self.relays(), [(f"app/core@{tail}", {"at": "2026-10-06T00:00:01Z", "kind": "ticket", "id": "T7",
                                                                 "state": "waiting", "note": "from app/engine/T7"})])
        self.assertEqual(log.read_bytes()[tail:], b"2026-10-06T00:00:01Z\tticket\tT7\twaiting\tfrom app/engine/T7\n")

    def test_snapshot_under_another_stores_lock_raises_and_returns_nothing(self):
        self.open("docs")
        self.open_admin()
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        admin = glob["Restaurant"](self.dir(".admin"))
        docs = glob["Restaurant"](self.dir("docs"))
        log = docs.dir / "log.tsv"
        start = log.stat().st_size
        tail = b"2026-10-06T00:00:00Z\tticket\tT6\twaiting\tfro"
        log.write_bytes(log.read_bytes() + tail)
        fabricated = b"2026-10-06T00:00:00Z\tticket\tT6\twaiting\tfrom app/engine/T7\n"
        reads = [0]

        def chunk(fd):
            data = os.read(fd, 16)
            reads[0] += len(data)
            if reads[0] == 42 and data:
                proc = subprocess.run([sys.executable, "-B", "-c",
                                       "import runpy,sys; g=runpy.run_path(sys.argv[1]); r=g['Restaurant'](sys.argv[2]); "
                                       "r.append('log.tsv',{'at':'2026-10-06T00:00:01Z','kind':'ticket','id':'T7',"
                                       "'state':'waiting','note':'from app/engine/T7'})",
                                       str(SCRIPT), str(docs.dir)],
                                      capture_output=True, text=True, timeout=3)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            return data

        original_read = glob["read_chunk"]
        glob["read_chunk"] = chunk
        copied = b""
        with admin.locked():
            try:
                copied = docs.snapshot("log.tsv", start)
            except glob["BrigadeError"] as error:
                self.assertEqual(str(error),
                                 f"cannot read {docs.dir} while holding {admin.dir.resolve()}'s lock; "
                                 "read other stores before taking a lock")
            else:
                self.fail(f"snapshot returned {copied!r}")
        self.assertEqual(copied, b"")
        self.assertNotEqual(log.read_bytes()[start:], fabricated)
        glob["read_chunk"] = original_read
        with docs.locked():
            self.assertEqual(docs.snapshot("log.tsv", start), tail)

    # Recovery.

    def test_two_recoveries_racing_leave_one_claim(self):
        self.with_thread()
        procs = [subprocess.Popen([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.dir(".admin")),
                                   "set", "--thread", f"recovering:{name}", "--replace", "--expect", "t1"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for name in ("a", "b")]
        results = [(proc.communicate(timeout=30), proc.returncode) for proc in procs]
        self.assertEqual(sorted(code for _, code in results), [0, 1])
        meta = self.meta(".admin")
        self.assertIn(meta["thread"], ("recovering:a", "recovering:b"))
        self.assertEqual((meta["generation"], meta["previousThread"]), (2, "t1"))
        loser = next(err for (_, err), code in results if code == 1)
        self.assertEqual(loser.strip(), f"brigade: thread is {meta['thread']}, not t1; nothing replaced")

    def test_recovery_needs_a_stopped_run_and_retirement_raises_the_generation(self):
        self.with_thread()
        self.admin("set", "--schedule", "intake=s-1")
        self.assertEqual(self.admin("set", "--thread", "t9", "--replace", ok=False),
                         "brigade: the executive admin's thread changes only with --replace --expect <old>")
        claim = json.loads(self.admin("set", "--thread", "recovering:t9", "--replace", "--expect", "t1"))
        self.assertEqual((claim["thread"], claim["generation"], claim["previousThread"], claim["schedules"]),
                         ("recovering:t9", 2, "t1", {"intake": "s-1"}))
        before = (self.dir(".admin") / "restaurant.json").read_bytes()
        self.assertEqual(self.admin("set", "--thread", "t2", "--replace", "--expect", "recovering:t9", ok=False),
                         "brigade: the old run has not been confirmed stopped; wait for it with t3_thread_wait, then pass --stopped <run id>")
        self.assertEqual((self.dir(".admin") / "restaurant.json").read_bytes(), before)
        self.admin("set", "--thread", "t2", "--replace", "--expect", "recovering:t9", "--stopped", "r1")
        self.assertEqual(self.admin("status").splitlines()[0], "thread t2")
        self.assertEqual(self.admin("status").splitlines()[-1], "owner t2@3")
        self.assertEqual(self.meta(".admin")["previousThread"], "t1")
        self.assertEqual([row[1:] for row in self.rows(".admin", "log.tsv") if row[1] == "thread"], [
            ["thread", "t1@1", "recorded", "first thread"],
            ["thread", "recovering:t9@2", "recorded", "replaced t1"],
            ["thread", "t2@3", "recorded", "replaced recovering:t9; stopped r1"],
        ])
        self.admin("set", "--thread", "", "--replace", "--expect", "t2")
        self.assertEqual([row[2:] for row in self.rows(".admin", "log.tsv") if row[1] == "thread" and row[2] == "@4"],
                         [["@4", "recorded", "replaced t2"]])
        self.assertEqual(self.admin("--owner", "t2@3", "status").splitlines()[0], "thread not recorded")
        self.assertEqual(self.admin("--owner", "t2@3", "ticket", "add", "--kind", "fix", "--summary", "late", ok=False),
                         "brigade: owner t2@3 is stale; this store is owned by @4")
        self.admin("set", "--thread", "recovering:t5", "--replace", "--expect", "")
        self.admin("set", "--thread", "t6", "--replace", "--expect", "recovering:t5", "--stopped", "gone")
        meta = self.meta(".admin")
        self.assertEqual((meta["thread"], meta["generation"], meta["previousThread"]), ("t6", 6, "t2"))

    def test_a_replacement_killed_before_the_metadata_write_retries_with_one_row(self):
        self.with_thread()
        self.admin("set", "--thread", "recovering:t9", "--replace", "--expect", "t1")
        died = self.child("die-before-thread", ".admin", "set", "--thread", "t2", "--replace",
                          "--expect", "recovering:t9", "--stopped", "r1")
        self.assertEqual(died.returncode, 9, died.stderr)
        meta = self.meta(".admin")
        self.assertEqual((meta["thread"], meta["generation"]), ("recovering:t9", 2))
        self.assertEqual([row[1:] for row in self.rows(".admin", "log.tsv") if row[2] == "t2@3"], [
            ["thread", "t2@3", "recorded", "replaced recovering:t9; stopped r1"],
        ])
        self.admin("set", "--thread", "t2", "--replace", "--expect", "recovering:t9", "--stopped", "r1")
        meta = self.meta(".admin")
        self.assertEqual((meta["thread"], meta["generation"], meta["previousThread"]), ("t2", 3, "t1"))
        self.assertEqual([row[2] for row in self.rows(".admin", "log.tsv") if row[1] == "thread" and row[2] == "t2@3"],
                         ["t2@3"])

    def test_a_replacement_killed_inside_the_log_leaves_the_old_thread(self):
        self.with_thread()
        self.admin("set", "--thread", "recovering:t9", "--replace", "--expect", "t1")
        before = (self.dir(".admin") / "restaurant.json").read_bytes()
        died = self.child("die-before-log", ".admin", "set", "--thread", "t2", "--replace",
                          "--expect", "recovering:t9", "--stopped", "r1")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual((self.dir(".admin") / "restaurant.json").read_bytes(), before)
        self.assertNotIn("stopped r1", (self.dir(".admin") / "log.tsv").read_text())
        self.admin("set", "--thread", "t2", "--replace", "--expect", "recovering:t9", "--stopped", "r1")
        meta = self.meta(".admin")
        self.assertEqual((meta["thread"], meta["generation"], meta["previousThread"]), ("t2", 3, "t1"))
        self.assertEqual([row[2:] for row in self.rows(".admin", "log.tsv") if "stopped r1" in row[4]],
                         [["t2@3", "recorded", "replaced recovering:t9; stopped r1"]])

    def test_the_claim_raises_the_admin_floor_in_the_landing_store(self):
        env = dict(os.environ, XDG_STATE_HOME=str(Path(self.temporary.name) / "state"))
        git = lambda *command: subprocess.run(command, cwd=self.project, capture_output=True, text=True, check=True)
        git("git", "init", "-q", "-b", "main")
        (self.project / "a.txt").write_text("a\n")
        git("git", "add", "-A")
        git("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        land = lambda *args: subprocess.run([sys.executable, str(LAND_SCRIPT), "--repo", str(self.project), *args],
                                            capture_output=True, text=True, env=env)
        self.assertEqual(land("init", "--trunk", "lane", "--mode", "local", "--base", "main").returncode, 0)
        self.open_admin()
        for args in (("set", "--thread", "t1"), ("set", "--thread", "recovering:t9", "--replace", "--expect", "t1")):
            result = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.dir(".admin")), *args],
                                    capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(land("owner", "--prefix", ".admin/", "--generation", "1").stderr.strip(),
                         "land: .admin/ is at generation 2; a floor never lowers")

    def test_expect_works_only_in_the_admin_store(self):
        self.open("docs")
        self.brigade("docs", "set", "--thread", "t1")
        self.assertEqual(self.brigade("docs", "set", "--thread", "t2", "--replace", "--expect", "t1", ok=False),
                         "brigade: --expect works only in the executive admin's store")
        self.assertEqual(self.meta("docs")["thread"], "t1")

    def test_a_log_write_that_read_restaurant_json_before_a_replacement_keeps_the_new_thread(self):
        self.with_thread()
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        restaurant = glob["Restaurant"](self.dir(".admin"), "t1@1")
        self.assertEqual(restaurant.meta["thread"], "t1")
        self.admin("set", "--thread", "recovering:t9", "--replace", "--expect", "t1")
        after_claim = self.files(".admin")
        with self.assertRaises(glob["BrigadeError"]) as raised:
            restaurant.log("ticket", "T1", "waiting", "one")
        self.assertEqual(str(raised.exception), "owner t1@1 is stale; this store is owned by recovering:t9@2")
        self.assertEqual(self.files(".admin"), after_claim)
        unfenced = glob["Restaurant"](self.dir(".admin"))
        unfenced.unfenced = True
        unfenced.log("ticket", "T1", "waiting", "one")
        meta = self.meta(".admin")
        self.assertEqual((meta["thread"], meta["generation"], meta["previousThread"]), ("recovering:t9", 2, "t1"))

    def test_reports_to_is_recorded_cleared_and_shown_by_status(self):
        self.open("docs")
        self.brigade("docs", "set", "--thread", "th-docs")
        self.brigade("docs", "set", "--reports-to", "th-admin")
        self.assertEqual(self.meta("docs")["reportsTo"], "th-admin")
        self.assertEqual(self.brigade("docs", "status"),
                         "thread th-docs\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nreports to th-admin\nowner th-docs@1")
        self.brigade("docs", "set", "--reports-to", "")
        self.assertNotIn("reportsTo", self.meta("docs"))
        self.assertEqual(self.brigade("docs", "status"), "thread th-docs\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nowner th-docs@1")

    # The owner fence.

    def test_claim_first_then_a_paused_rule_add_is_refused(self):
        self.with_thread()
        case, proc = self.admin_child("pause", "rule", "--owner", "t1@1", "rule", "add", "--kind", "ownership",
                                      "--parties", "docs,engine", "--question", "Who owns T1?", "--rule", "priority",
                                      "--decision", "docs")
        _wait_for_path(case / "paused", timeout=20)
        self.admin("set", "--thread", "recovering:t9", "--replace", "--expect", "t1")
        after_claim = self.files(".admin")
        (case / "proceed").touch()
        code, _, err = self.finish(proc)
        self.assertEqual((code, err), (1, "brigade: owner t1@1 is stale; this store is owned by recovering:t9@2"))
        self.assertEqual(self.files(".admin"), after_claim)
        self.assertEqual(self.rows(".admin", "rulings.tsv"), [])

    def test_after_a_terminal_wait_the_old_runs_paused_commands_change_nothing(self):
        self.with_thread()
        self.brigade("docs", "ticket", "add", "--kind", "fix", "--summary", "one")
        self.admin("ticket", "add", "--kind", "fix", "--summary", "route me")
        old = ("--owner", "t1@1")
        paused = [self.admin_child("pause", f"stale{number}", *old, *args) for number, args in enumerate((
            ("rule", "add", "--kind", "ownership", "--parties", "docs,engine", "--question", "q", "--rule", "priority", "--decision", "d"),
            ("request", "--to", "docs", "from-user docs: late"),
            ("sync",),
            ("ticket", "move", "T1", "--to", "docs"),
        ))]
        for case, _ in paused:
            _wait_for_path(case / "paused", timeout=20)
        self.admin("set", "--thread", "recovering:t9", "--replace", "--expect", "t1")
        self.admin("set", "--thread", "t2", "--replace", "--expect", "recovering:t9", "--stopped", "r1")
        self.assertEqual(self.admin("status").splitlines()[-1], "owner t2@3")
        self.assertIn("docs", self.admin("sync"))
        admin_after, docs_after = self.files(".admin"), self.files("docs")
        for case, proc in paused:
            (case / "proceed").touch()
            code, _, err = self.finish(proc)
            self.assertEqual((code, err), (1, "brigade: owner t1@1 is stale; this store is owned by t2@3"))
        self.assertEqual(self.files(".admin"), admin_after)
        self.assertEqual(self.files("docs"), docs_after)
        self.assertEqual(self.inbox("docs"), [])

    def test_a_write_that_took_the_lock_before_the_claim_finishes_first(self):
        self.with_thread()
        case, proc = self.admin_child("hold", "hold", "--owner", "t1@1", "rule", "add", "--kind", "ownership",
                                      "--parties", "docs,engine", "--question", "Who owns T1?", "--rule", "priority",
                                      "--decision", "docs")
        _wait_for_path(case / "holding", timeout=20)
        claim = subprocess.Popen([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.dir(".admin")),
                                  "set", "--thread", "recovering:t9", "--replace", "--expect", "t1"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: claim.poll() is None and claim.kill())
        time.sleep(0.5)
        self.assertIsNone(claim.poll(), "the claim waits for the lock")
        (case / "proceed").touch()
        self.assertEqual(self.finish(proc)[:2], (0, "R1"))
        self.assertEqual(self.finish(claim)[0], 0)
        kinds = [(row[1], row[2]) for row in self.rows(".admin", "log.tsv")]
        self.assertLess(kinds.index(("ruling", "R1")), kinds.index(("thread", "recovering:t9@2")))
        self.assertEqual(self.meta(".admin")["thread"], "recovering:t9")

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


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--race-child":
        try:
            _race_child(sys.argv[2], sys.argv[3], sys.argv[4:])
        except TimeoutError as error:
            print(f"race child timed out waiting for {error}", file=sys.stderr)
            sys.exit(2)
    else:
        unittest.main()
