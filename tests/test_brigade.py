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

    def test_open_creates_the_store_once_and_keeps_edits(self):
        self.assertEqual(self.open(), f"opened {self.at}")
        (self.at / "menu.md").write_text("# Menu: Perf\n\nKeep startup under 400 ms.\n")
        self.assertEqual(self.open(), f"exists {self.at}")
        self.assertEqual((self.at / "menu.md").read_text(), "# Menu: Perf\n\nKeep startup under 400 ms.\n")
        self.assertIn("only through the repository's landing queue", (self.at / "house-rules.md").read_text())
        self.assertIn("Never use them in replies", (self.at / "house-rules.md").read_text())

    def test_fire_groups_waiting_tickets_into_one_dish(self):
        self.open()
        self.brigade("set", "--intake", "github")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Startup is slow on cold boot", "--source", "github", "--ref", "#12"), "T1")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Splash screen hangs"), "T2")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--station", "perf-issue", "--summary", "Cut cold start time"), "D1")
        self.assertEqual(self.brigade("ticket", "list"),
                         "T1 assigned normal fix [github] Startup is slow on cold boot #12\nT2 assigned normal fix [user] Splash screen hangs")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "again", ok=False),
                         "brigade: T1 is assigned, not waiting")

    def same_text_refusal(self, ticket, state):
        return f"brigade: same text as {ticket} ({state}); nothing added; pass --again to file a second ticket"

    def test_ticket_add_refuses_a_user_request_with_the_summary_of_a_waiting_ticket(self):
        self.open()
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page"), "T1")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "fix  the LOGIN page", ok=False),
                         self.same_text_refusal("T1", "waiting"))
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "  Fix the\tlogin\npage  ", ok=False),
                         self.same_text_refusal("T1", "waiting"))
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the login page")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the loginpage"), "T2")
        self.assertEqual(self.brigade("ticket", "list"),
                         "T1 waiting normal fix [user] Fix the login page\nT2 waiting normal fix [user] Fix the loginpage")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "fix  the LOGIN page", "--again"), "T3")

    def test_ticket_add_refuses_a_user_request_with_the_summary_of_an_assigned_ticket(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix login")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", ok=False),
                         self.same_text_refusal("T1", "assigned"))

    def test_ticket_add_takes_a_user_request_with_the_summary_of_a_done_or_dropped_ticket(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page")
        self.brigade("ticket", "set", "T1", "--state", "done")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page"), "T2")
        self.brigade("ticket", "set", "T2", "--state", "dropped")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page"), "T3")

    def test_ticket_add_names_a_waiting_ticket_of_another_source_with_the_same_summary(self):
        self.open()
        self.brigade("set", "--intake", "github")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--source", "github", "--ref", "#12")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", ok=False),
                         self.same_text_refusal("T1", "waiting"))

    def test_ticket_add_with_a_request_id_still_refuses_the_summary_of_a_waiting_ticket(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--request", "A2", ok=False),
                         self.same_text_refusal("T1", "waiting"))

    def test_ticket_add_with_a_ref_takes_the_summary_of_a_waiting_ticket(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--ref", "#12"), "T2")

    def test_again_lifts_neither_the_ref_refusal_nor_the_request_refusal_nor_the_source_refusal(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--ref", "#12", "--request", "A2")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--ref", "#12", "--again", ok=False),
                         "brigade: #12 is already T1 (waiting); nothing added")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--request", "A2", "--again", ok=False),
                         "brigade: request A2 is already T1; nothing added")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--source", "github", "--again", ok=False),
                         "brigade: no coordinator owns intake from github; the one that reads it runs set --intake github")
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user (request A2)] Fix the login page #12")

    def test_the_skill_quotes_the_same_summary_refusal_ticket_add_prints(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page")
        line = self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", ok=False)
        text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        section = text.split("## Filing tracker work", 1)[1].split("\n## ", 1)[0]
        step = next(row for row in section.splitlines() if row.startswith("4. **Refusals.**"))
        self.assertEqual(line, "brigade: same text as T1 (waiting); nothing added; pass --again to file a second ticket")
        self.assertIn("`" + line.replace("T1 (waiting)", "T<n> (<state>)") + "`", step)
        self.assertNotIn("never refused", step)

    def test_pass_refuses_a_verifier_from_the_author_family(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        error = self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "pass",
                             "--author", CLAUDE, "--verifier", "cursor/claude-sonnet-5-5", ok=False)
        self.assertIn("same model family", error)
        self.assertEqual(self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "pass",
                                      "--author", CLAUDE, "--verifier", "cursor/claude-sonnet-5-5", "--same-family"), "D1 passed")
        self.assertIn("same model family", (self.at / "pass.tsv").read_text())

    def test_queueing_or_merging_needs_a_pass_at_the_current_sha(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued", "--sha", "def", ok=False),
                         "brigade: only reviewed work lands: D1 has no review verdict for def")
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued"), "D1 queued")
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract, waiting to land: 1\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nkinds: fix 1 in flight")
        self.assertEqual(self.brigade("dish", "D1", "--state", "merged"), "D1 merged")
        self.assertEqual(self.brigade("ticket", "list", "--state", "done"), "T1 done normal fix [user] s")

    def test_send_back_blocks_landing(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "send-back",
                     "--author", CLAUDE, "--verifier", CODEX, "--note", "test asserts the bug")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False),
                         "brigade: D1 at abc: send-back (test asserts the bug)")
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued", ok=False),
                         "brigade: only reviewed work lands: D1 at abc: send-back (test asserts the bug)")

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

    def test_pass_check_json_prints_a_cross_family_pass(self):
        self.fired_bug_fix()
        self.record("abc", "pass")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"verdict": "pass", "author": CLAUDE, "verifier": CODEX, "note": "", "crossFamily": True})
        self.assertIn('"crossFamily": true', out)
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc"), f"D1 at abc passed review by {CODEX}")

    def test_pass_check_json_exits_1_when_a_send_back_voids_the_pass(self):
        self.fired_bug_fix()
        self.record("abc", "pass")
        self.record("abc", "send-back", "--note", "test asserts the bug")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (1, "brigade: D1 at abc: send-back (test asserts the bug)"))
        self.assertIn('"verdict": "send-back"', out)
        self.assertEqual(json.loads(out)["note"], "test asserts the bug")

    def test_pass_check_json_marks_a_same_family_pass(self):
        self.fired_bug_fix()
        self.record("abc", "pass", "--same-family", verifier="cursor/claude-sonnet-5-5")
        self.record("def", "pass", "--same-family")
        with (self.at / "pass.tsv").open("a") as table:
            table.write(f"2026-10-08T00:00:00+00:00\tD1\t\tghi\tpass\t{CLAUDE}\tcursor/claude-sonnet-5-5\t\n")
        for sha, note in (("abc", "same model family;"), ("def", "same model family;"), ("ghi", "")):
            code, out, err = self.check_json(sha)
            self.assertEqual((code, err), (0, ""))
            self.assertIn('"crossFamily": false', out)
            self.assertEqual(json.loads(out)["note"], note)

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

    def test_pass_record_refuses_a_bedrock_claude_author_with_a_claude_verifier(self):
        self.assert_one_family(self.BEDROCK, CLAUDE)

    def test_pass_record_refuses_an_underscored_claude_author_with_a_claude_verifier(self):
        self.assert_one_family("anthropic/claude_opus_5", "cursor/claude-sonnet-5-5")

    def test_pass_record_refuses_a_dated_claude_author_with_a_dated_claude_verifier(self):
        self.assert_one_family("vertex/claude-sonnet-5-5@20260101", "claudeAgent/claude-haiku-5-5-20260301")

    def test_pass_record_refuses_a_namespaced_muse_author_with_a_muse_verifier(self):
        self.assert_one_family(self.NAMESPACED_MUSE, "openrouter/muse-lite-2")

    def test_pass_record_records_two_families_with_no_flag(self):
        self.fired_bug_fix()
        for sha, author, verifier in (("abc", CLAUDE, CODEX), ("def", self.NAMESPACED_MUSE, CLAUDE)):
            self.assertEqual(self.brigade("pass", "record", "D1", "--sha", sha, "--verdict", "pass",
                                          "--author", author, "--verifier", verifier), "D1 passed")
        self.assertEqual([row[5:8] for row in self.pass_rows()], [[CLAUDE, CODEX, ""], [self.NAMESPACED_MUSE, CLAUDE, ""]])

    def test_pass_check_json_reads_a_stored_bedrock_row_as_one_family(self):
        self.fired_bug_fix()
        with (self.at / "pass.tsv").open("a") as table:
            table.write(f"2026-10-08T00:00:00+00:00\tD1\t\tabc\tpass\t{self.BEDROCK}\t{CLAUDE}\t\n")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (0, ""))
        self.assertIn('"crossFamily": false', out)

    def test_the_built_brigade_refuses_a_bedrock_claude_author_with_a_claude_verifier(self):
        self.fired_bug_fix()
        built = ROOT / "skills/brigade/scripts/brigade.py"
        result = subprocess.run([sys.executable, str(built), "--store", str(self.store), "--at", str(self.at),
                                 *_owner_words(self.at), "pass", "record", "D1", "--sha", "abc", "--verdict", "pass",
                                 "--author", self.BEDROCK, "--verifier", CLAUDE], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertEqual(result.stderr.strip(), self.same_family_refusal(self.BEDROCK, CLAUDE))
        self.assertEqual(self.pass_rows(), [])

    def test_pass_record_without_roles_py_refuses_and_records_nothing(self):
        self.fired_bug_fix()
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        missing = Path(self.temporary.name) / "missing"
        glob["BUILT_ROLES"], glob["SOURCE_ROLES"] = missing / "built.py", missing / "source.py"
        argv = ["--store", str(self.store), "--at", str(self.at), *_owner_words(self.at), "pass", "record", "D1",
                "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX]
        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            code = glob["main"](argv)
        self.assertEqual(code, 1)
        self.assertEqual(stderr.getvalue(), f"brigade: cannot find roles.py at {missing / 'built.py'} or "
                                            f"{missing / 'source.py'}; build or reinstall pstack-t3\n")
        self.assertEqual(self.pass_rows(), [])

    def test_pass_check_json_at_another_sha_has_no_verdict(self):
        self.fired_bug_fix()
        self.record("abc", "pass")
        self.assertEqual(self.check_json("def"), (1, "", "brigade: D1 has no review verdict for def"))

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

    def test_a_pass_table_holds_rows_written_before_and_after_the_report_and_member_columns(self):
        self.fired_bug_fix()
        stamp = "2026-10-08T00:00:00+00:00"
        old = f"{stamp}\tD1\t\tabc\tpass\t{CLAUDE}\t{CODEX}\told row"
        table = self.at / "pass.tsv"
        table.write_text("at\tdish\tpr\tsha\tverdict\tauthor\tverifier\tnote\n" + old + "\n"
                         + f"{stamp}\tD1\t\tdef\tsend-back\t{CLAUDE}\t{CODEX}\tnew row\tD1-review-2.md\t\n")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc"), f"D1 at abc passed review by {CODEX}")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "def", ok=False),
                         "brigade: D1 at def: send-back (new row)")
        self.record("ghi", "pass")
        self.assertEqual(table.read_text().splitlines()[1], old)
        self.assertEqual(self.pass_rows()[2][3:], ["ghi", "pass", CLAUDE, CODEX, "", "", ""])
        for fields, line in ((old.split("\t")[:7], 5), ([*old.split("\t"), "r.md", "", "extra"], 5)):
            table.write_text(table.read_text() + "\t".join(fields) + "\n")
            self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False),
                             f"brigade: bridge-kit/perf/pass.tsv line {line} is malformed; fix or remove it")
            table.write_text("\n".join(table.read_text().splitlines()[:-1]) + "\n")

    def test_pass_record_stores_the_bare_name_of_an_existing_review_report(self):
        self.fired_bug_fix()
        path = self.review_file("D1-review-1.md")
        self.review_file("D1-review.md")
        self.review_file("D1-review-2-panel-1.md")
        self.record("abc", "send-back", "--report", str(path))
        self.record("abc", "send-back", "--report", "reports/D1-review.md")
        self.record("abc", "send-back", "--report", "D1-review-2-panel-1.md")
        self.assertEqual([row[8] for row in self.pass_rows()], ["D1-review-1.md", "D1-review.md", "D1-review-2-panel-1.md"])
        before = (self.at / "pass.tsv").read_bytes()
        record = ("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.brigade(*record, "--report", "D1-review-9.md", ok=False),
                         "brigade: reports/D1-review-9.md does not exist; write the review report first")
        self.review_file("D2-review-1.md")
        self.review_file("D1.md")
        self.review_file("D11-review.md")
        for name in ("D2-review-1.md", "D1.md", "D11-review.md"):
            self.assertEqual(self.brigade(*record, "--report", name, ok=False),
                             f"brigade: {name} is not a review report of D1; name a file like reports/D1-review-1.md")
        self.assertEqual((self.at / "pass.tsv").read_bytes(), before)
        self.assertEqual(self.dish_fields("state"), ("sent-back",))

    def test_pass_record_refuses_a_report_path_outside_the_reports_directory_of_the_store(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md")
        elsewhere = Path(self.temporary.name) / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "D1-review-1.md").write_text("another file\n")
        before = (self.at / "pass.tsv").read_bytes()
        record = ("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        for report in (str(elsewhere / "D1-review-1.md"), "no/such/dir/D1-review-1.md"):
            self.assertEqual(self.brigade(*record, "--report", report, ok=False),
                             f"brigade: {report} is outside this store's reports/; name reports/D1-review-1.md")
        self.assertEqual((self.at / "pass.tsv").read_bytes(), before)

    def test_pass_record_refuses_a_directory_a_live_link_a_dangling_link_and_a_file_under_a_linked_reports(self):
        self.fired_bug_fix()
        reports = self.review_file("D1-review-4.md").parent
        outside = Path(self.temporary.name) / "outside"
        (outside / "reports").mkdir(parents=True)
        (outside / "findings.md").write_text("findings\n")
        (outside / "reports" / "D1-review-5.md").write_text("findings\n")
        (reports / "D1-review-1.md").mkdir()
        (reports / "D1-review-2.md").symlink_to(outside / "findings.md")
        (reports / "D1-review-3.md").symlink_to(outside / "gone.md")
        self.record("abc", "send-back", "--report", "D1-review-4.md")
        before = (self.at / "pass.tsv").read_bytes()
        record = ("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.brigade(*record, "--report", "D1-review-1.md", ok=False),
                         "brigade: reports/D1-review-1.md is not a regular file; nothing recorded")
        self.assertEqual(self.brigade(*record, "--report", "D1-review-2.md", ok=False),
                         "brigade: reports/D1-review-2.md is a symbolic link; nothing recorded")
        self.assertEqual(self.brigade(*record, "--report", "D1-review-3.md", ok=False),
                         "brigade: reports/D1-review-3.md is a symbolic link; nothing recorded")
        reports.rename(self.at / "kept")
        reports.symlink_to(outside / "reports")
        self.assertEqual(self.brigade(*record, "--report", "D1-review-5.md", ok=False),
                         "brigade: reports/D1-review-5.md resolves outside this store's reports/; nothing recorded")
        self.assertEqual((self.at / "pass.tsv").read_bytes(), before)
        self.assertEqual([row[8] for row in self.pass_rows()], ["D1-review-4.md"])

    def store_files(self):
        """Every file of the store but restaurant.lock, which is empty and is not store data."""
        return {str(path.relative_to(self.at)): path.read_bytes() for path in sorted(self.at.rglob("*"))
                if path.is_file() and path.name != "restaurant.lock"}

    def tables(self):
        return {path.name: path.read_bytes() for path in sorted(self.at.glob("*.tsv"))}

    def follow_ups_in(self, body, *extra, name="D1.md", ok=True, owner=True):
        self.review_file(name, body)
        return self.brigade("ticket", "add", "--from-report", f"reports/{name}", *extra, ok=ok, owner=owner)

    def test_from_report_files_one_waiting_ticket_per_bullet_and_a_rerun_adds_nothing(self):
        self.fired_bug_fix()
        first = ("A no-ref `--source user` add still has no CLI dedupe. The new Refusals rule tells the coordinator to read "
                 "`ticket list` before refiling. A `brigade.py` guard would make that structural.")
        second = ("`docs/guide.md` still describes intake as `gh issue list` and `gh pr list` when the standing orders name "
                  "them. That file is outside this lease.")
        third = "PR #87 was not commented on, approved, or closed."
        self.assertEqual(self.follow_ups_in(BULLETS).splitlines(), [
            f"T2 added from perf/reports/D1.md#1: {first}",
            f"T3 added from perf/reports/D1.md#2: {second}",
            f"T4 added from perf/reports/D1.md#3: {third}",
        ])
        self.assertEqual(self.brigade("ticket", "list").splitlines()[-1],
                         f"T4 waiting low polish [report] {third} perf/reports/D1.md#3")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2")["summary"], first)
        self.assertIn(f"\tticket\tT4\twaiting\t{third}\n", (self.at / "log.tsv").read_text())
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.follow_ups_in(BULLETS).splitlines(), [
            f"skipped perf/reports/D1.md#1, same text as T2 (waiting): {first}",
            f"skipped perf/reports/D1.md#2, same text as T3 (waiting): {second}",
            f"skipped perf/reports/D1.md#3, same text as T4 (waiting): {third}",
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)
        self.brigade("ticket", "set", "T4", "--state", "dropped")
        self.brigade("ticket", "set", "T3", "--state", "done")
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.follow_ups_in(BULLETS).splitlines()[1:], [
            f"skipped perf/reports/D1.md#2, same text as T3 (done): {second}",
            f"skipped perf/reports/D1.md#3, same text as T4 (dropped): {third}",
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

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

    def test_from_report_dry_run_changes_no_table_and_no_log_and_needs_no_owner(self):
        self.fired_bug_fix()
        self.brigade("set", "--thread", "t1")
        before = self.tables()
        self.assertIn("log.tsv", before)
        self.assertEqual(self.follow_ups_in(BULLETS, "--dry-run", owner=False).splitlines()[2],
                         "would add from perf/reports/D1.md#3: PR #87 was not commented on, approved, or closed.")
        self.assertEqual(self.follow_ups_in(BULLETS, ok=False, owner=False),
                         "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status")
        self.assertEqual(self.tables(), before)

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

    def test_from_report_changes_only_the_ticket_table_the_log_and_last_activity(self):
        self.fired_bug_fix()
        self.review_file("D1.md", BULLETS)

        def changed_by(*command):
            before = self.store_files()
            self.brigade(*command)
            after = self.store_files()
            return before, sorted(name for name in after if after[name] != before.get(name))

        _, by_hand = changed_by("ticket", "add", "--kind", "fix", "--summary", "by hand")
        (self.at / "restaurant.lock").unlink()
        before, from_report = changed_by("ticket", "add", "--from-report", "reports/D1.md")
        self.assertEqual(from_report, ["log.tsv", "rail.tsv", "restaurant.json"])
        self.assertEqual(from_report, by_hand)
        old, new = json.loads(before["restaurant.json"]), json.loads((self.at / "restaurant.json").read_text())
        self.assertEqual([key for key in new if new[key] != old.get(key)], ["lastActivityAt"])

    def test_from_report_skips_text_a_ticket_already_holds_whatever_its_spacing_and_case(self):
        self.fired_bug_fix()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "pr #87   was NOT commented on, approved, or closed.")
        self.assertEqual(self.follow_ups_in(BULLETS).splitlines()[2],
                         "skipped perf/reports/D1.md#3, same text as T2 (waiting): "
                         "PR #87 was not commented on, approved, or closed.")
        self.assertEqual(self.brigade("ticket", "list").splitlines()[-1].split(" ", 1)[0], "T4")

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

    def test_from_report_refuses_the_flags_of_a_hand_written_ticket(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "## Follow-ups\n\n- One thing.\n")
        before = (self.at / "rail.tsv").read_bytes()
        add = ("ticket", "add", "--from-report", "reports/D1.md")
        for extra in (("--source", "github"), ("--ref", "x"), ("--request", "A1"), ("--again",)):
            self.assertEqual(self.brigade(*add, *extra, ok=False),
                             "brigade: --from-report takes no --source, --ref, --request, or --again")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "x", "--dry-run", ok=False),
                         "brigade: --dry-run needs --from-report")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "x", "--source", "report", ok=False),
                         "brigade: no coordinator owns intake from report; the one that reads it runs set --intake report")
        for flags, message in ((("--summary", "x", "--from-report", "reports/D1.md"),
                                "argument --from-report: not allowed with argument --summary"),
                               ((), "one of the arguments --summary --from-report is required")):
            result = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
                                     "ticket", "add", *flags], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn(message, result.stderr)
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)

    def test_a_member_send_back_recorded_before_the_passing_rows_changes_no_verdict(self):
        self.fired_bug_fix()
        self.assertEqual(self.brigade("pass", "record", "D1", "--pr", "u/9", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", "grok/grok-4.7", "--note", "nit", "--member"),
                         "D1: member send-back on record; D1 stays in-progress")
        self.assertEqual(self.dish_fields("state", "pr", "sha"), ("in-progress", "", ""))
        self.assertEqual(self.check_json("abc"), (1, "", "brigade: D1 has no review verdict for abc"))
        self.record("abc", "pass")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc"), f"D1 at abc passed review by {CODEX}")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"verdict": "pass", "author": CLAUDE, "verifier": CODEX, "note": "", "crossFamily": True})
        self.assertEqual([row[4:] for row in self.pass_rows()],
                         [["send-back", CLAUDE, "grok/grok-4.7", "nit", "", "yes"], ["pass", CLAUDE, CODEX, "", "", ""]])

    def test_a_member_send_back_recorded_after_the_passing_rows_changes_no_verdict(self):
        self.fired_bug_fix()
        self.record("abc", "pass")
        self.assertEqual(self.brigade("pass", "record", "D1", "--pr", "u/9", "--sha", "abc", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", "grok/grok-4.7", "--note", "nit", "--member"),
                         "D1: member send-back on record; D1 stays passed")
        self.assertEqual(self.dish_fields("state", "pr", "sha"), ("passed", "", "abc"))
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc"), f"D1 at abc passed review by {CODEX}")
        code, out, err = self.check_json("abc")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"verdict": "pass", "author": CLAUDE, "verifier": CODEX, "note": "", "crossFamily": True})
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued"), "D1 queued")

    def test_a_member_row_follows_the_family_rule_and_takes_no_late_flag(self):
        self.fired_bug_fix()
        member = ("pass", "record", "D1", "--sha", "abc", "--verdict", "send-back", "--author", CLAUDE, "--member")
        self.assertIn("same model family", self.brigade(*member, "--verifier", "cursor/claude-sonnet-5-5", ok=False))
        self.assertIn("not allowed with argument", self.brigade(*member, "--verifier", CODEX, "--late", ok=False))
        self.assertEqual(self.pass_rows(), [])

    def test_pass_record_for_an_unknown_item_leaves_no_row(self):
        self.fired_bug_fix()
        for flags in ((), ("--member",), ("--late",)):
            self.assertEqual(self.brigade("pass", "record", "D9", "--sha", "abc", "--verdict", "pass",
                                          "--author", CLAUDE, "--verifier", CODEX, *flags, ok=False),
                             "brigade: no D9 in dishes.tsv")
        self.assertEqual(self.pass_rows(), [])

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

    def test_close_warns_about_the_report_of_an_item_in_review_that_has_no_claims_check_line(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "# D1\n\nStatus: done\n\nThe Claims check found nothing.\nclaims check: 3 statements\n")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        code, before, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, self.no_claims("in-review")))
        code, out, err = self.close_run("--to-file")
        self.assertEqual((code, err), (0, self.no_claims("in-review")))
        self.assertEqual(Path(out).read_text().strip(), before)
        code, out, err = self.close_run()
        self.assertEqual((code, err), (0, self.no_claims("in-review")))
        self.assertNotIn("warning", out)
        self.assertEqual(self.close_run()[2], self.no_claims("in-review"))

    def test_close_warns_about_the_report_of_a_passed_item_that_has_no_claims_check_line(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "# D1\n\nStatus: done\n")
        self.record("abc", "pass")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_claims("passed")))

    def test_close_prints_the_claims_check_warning_after_the_warning_for_an_unrecorded_review(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "# D1\n")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.review_file("D1-review-1.md")
        self.assertEqual(self.close_run("--dry-run")[::2],
                         (0, self.no_row("D1-review-1.md") + "\n" + self.no_claims("in-review")))

    def test_close_accepts_each_shape_of_a_claims_check_line(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        for line in ("Claims check: 14 statements checked, 2 corrected", "## Claims check", "### Claims check",
                     "- Claims check: 3 statements checked", "**Claims check.**", "1. **Claims check**",
                     "  > Claims check: none", "2) `Claims check`", "_Claims check_"):
            self.review_file("D1.md", f"# D1\n\nStatus: done\n\n{line}\n\nMore.\n")
            self.assertEqual(self.close_run("--dry-run")[::2], (0, ""), line)

    def test_close_reads_a_report_that_is_not_utf8_for_its_claims_check_line(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        report = self.review_file("D1.md")
        report.write_bytes(b"# D1 \xff\xfe\n\nStatus: done\n")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_claims("in-review")))
        report.write_bytes(b"# D1 \xff\xfe\n\n## Claims check\n")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))

    def test_close_prints_no_claims_check_warning_for_an_item_in_progress_sent_back_blocked_or_merged(self):
        self.fired_bug_fix()
        self.review_file("D1.md", "# D1\n\nStatus: done\n")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.record("abc", "send-back")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.record("abc", "blocked")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        self.record("abc", "pass")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_claims("passed")))
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))

    def test_close_prints_no_claims_check_warning_for_a_report_that_is_missing_or_a_link(self):
        self.fired_bug_fix()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        target = Path(self.temporary.name) / "elsewhere.md"
        target.write_text("# D1\n")
        (self.at / "reports").mkdir()
        (self.at / "reports" / "D1.md").symlink_to(target)
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))
        (self.at / "reports" / "D1.md").unlink()
        self.review_file("D1.md", "# D1\n")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_claims("in-review")))

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

    def test_report_entry_names_the_kind_of_each_entry_under_reports(self):
        self.fired_bug_fix()
        reports = self.review_file("file.md").parent
        (Path(self.temporary.name) / "findings.md").write_text("findings\n")
        self.linked("live.md", "findings.md")
        self.linked("dangling.md", "gone.md")
        (reports / "directory.md").mkdir()
        expected = {"file.md": "file", "live.md": "link", "dangling.md": "link", "directory.md": "other", "absent.md": "missing"}
        if hasattr(os, "mkfifo"):
            os.mkfifo(reports / "fifo.md")
            expected["fifo.md"] = "other"
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        restaurant = glob["Restaurant"](self.at)
        self.assertEqual({name: glob["report_entry"](restaurant, name) for name in expected}, expected)
        reports.rename(self.at / "kept")
        reports.symlink_to(self.at / "kept")
        self.assertEqual({name: glob["report_entry"](restaurant, name) for name in expected},
                         {**expected, "file.md": "elsewhere"})
        reports.unlink()
        reports.write_text("not a directory\n")
        self.assertEqual({glob["report_entry"](restaurant, name) for name in expected}, {"missing"})

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

    def test_close_warns_about_a_dangling_link_that_only_a_row_of_another_item_names(self):
        self.fired_bug_fix()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "t")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Fix t")
        self.review_file("D2-review-1.md")
        self.brigade("pass", "record", "D2", "--sha", "abc", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX,
                     "--report", "D2-review-1.md")
        table = self.at / "pass.tsv"
        table.write_text(table.read_text().replace("D2-review-1.md", "D1-review-7.md"))
        (self.at / "reports" / "D2-review-1.md").unlink()
        self.assertEqual([(row[1], row[8]) for row in self.pass_rows()], [("D2", "D1-review-7.md")])
        self.linked("D1-review-7.md", "gone.md")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.a_link("D1-review-7.md")))

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

    def test_set_landing_exits_2(self):
        self.open()
        before = (self.at / "restaurant.json").read_bytes()
        result = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
                                 "set", "--landing", "merge"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

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

    def test_a_state_is_logged_only_when_it_changes(self):
        self.open()
        self.fire_one()
        self.brigade("dish", "D1", "--state", "in-progress", "--task", "t-1")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", "grok/grok-4.7", "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.brigade("ticket", "set", "T1", "--state", "done")
        log = (self.at / "log.tsv").read_text()
        self.assertEqual(log.count("\tdish\tD1\tin-progress\t"), 1)
        self.assertEqual(log.count("\tticket\tT1\tdone\t"), 1)

    def test_brief_assembles_every_field_and_adds_the_exclusive_rule_for_measuring_stations(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")
        text = self.brigade("brief", "D1", "--contract", "none", "--goal", "Cold start under 400 ms.", "--acceptance", "Median cold start below 400 ms",
                            "--verify", "npm run perf", "--paths", "src/boot.ts", "--lease", "L4", "--base", "origin/main")
        self.assertTrue(text.startswith("Use the poteto-mode skill and its `perf-issue` playbook."))
        report = f"{self.at}/reports/D1.md"
        for part in ("PURPOSE: Make startup fast.", "TICKETS: T1: Startup is slow", "branch `perf/d1`, started from `origin/main`",
                     "leased to you as L4: src/boot.ts", "- Median cold start below 400 ms", "slot --exclusive --",
                     "TIMEBOX: 60 minutes. The timebox orders the work and never waives a playbook step (How, Architect, investigation, or the implementation delegate). At the limit, write the report with what remains instead of skipping steps.",
                     f"Write it to {report}", "1. Write in plain engineering prose.",
                     f'call t3_thread_send to thread thread-coord with mode "auto" and the one-line message "D1 done: report at {report}".'):
            self.assertIn(part, text)
        self.assertEqual((self.at / "briefs/D1.md").read_text().strip(), text)

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

    def test_brief_refuses_when_no_coordinator_thread_is_recorded(self):
        self.open()
        self.fire_one(station="bug-fix")
        error = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v",
                             "--paths", "a", "--lease", "L1", "--base", "origin/main", ok=False)
        self.assertIn("brigade.py set --thread", error)
        self.assertFalse((self.at / "briefs" / "D1.md").exists())

    def test_brief_refuses_a_menu_without_a_purpose_or_a_missing_acceptance(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("set", "--thread", "thread-coord")
        args = ["brief", "D1", "--contract", "none", "--goal", "g", "--verify", "v", "--paths", "a", "--lease", "L1", "--base", "origin/main"]
        self.assertIn("menu.md has no purpose yet", self.brigade(*args, "--acceptance", "a", ok=False))
        (self.at / "menu.md").write_text("## Purpose\n\nFix bugs.\n")
        self.assertIn("at least one --acceptance", self.brigade(*args, ok=False))
        self.assertNotIn("slot --exclusive", self.brigade(*args, "--acceptance", "a"))

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

    def test_a_send_back_clears_the_worker_and_refuses_the_old_thread(self):
        self.open()
        self.fire_one()
        self.brigade("dish", "D1", "--state", "sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-8"), "D1 sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-9", ok=False),
                         "brigade: thread thread-9 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "thread-8")
        refused = self.brigade("dish", "D1", "--state", "in-progress", "--thread", "thread-9", ok=False)
        self.assertEqual(refused, "brigade: thread thread-9 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "sent-back")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "thread-8")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", "thread-10"), "D1 in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "thread-10")
        self.brigade("dish", "D1", "--state", "sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in progress with no worker thread; launch a fresh worker")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-9", ok=False),
                         "brigade: thread thread-9 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-10", ok=False),
                         "brigade: thread thread-10 is an earlier attempt of D1; a send-back launches a fresh worker")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["thread"], "")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-11"), "D1 in-progress")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "thread-11"), "D1 in-progress")

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

    def test_fire_claims_the_lease_and_a_refused_claim_fires_nothing(self):
        import os
        from unittest import mock
        run = lambda *a: subprocess.run(a, cwd=self.project, capture_output=True, text=True, check=True)
        run("git", "init", "-q", "-b", "main")
        (self.project / "a.txt").write_text("a\n")
        run("git", "add", "-A")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        land = ROOT / "t3/added/landing/scripts/land.py"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(Path(self.temporary.name) / "state")}):
            subprocess.run([sys.executable, str(land), "--repo", str(self.project), "init", "--trunk", "lane", "--mode", "local", "--base", "main"],
                           check=True, capture_output=True)
            self.open()
            (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "src"),
                             "D1 (lease L1 held by perf/D1)")
            self.assertIn("nothing fired: paths overlap L1 held by perf/D1",
                          self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "s", "--paths", "src/x.py", ok=False))
            self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] two blocked: lease")
            self.brigade("set", "--thread", "thread-coord")
            text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "refs/landing/lane")
            self.assertIn("leased to you as L1: src,changes/perf%2Fd1.md.", text)
            self.assertIn("branch `perf/d1`", text)

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

    def test_tabs_and_newlines_in_input_cannot_break_a_table(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "line one\nline\ttwo")
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] line one line two")

    def test_commands_outside_a_restaurant_fail_with_the_fix(self):
        self.assertIn("run brigade.py open", self.brigade("status", ok=False))

    def test_set_schedule_drops_a_recorded_name_and_a_missing_one(self):
        self.open()
        self.brigade("set", "--schedule", "liveness=sched-4", "--schedule", "drain=sched-5")
        self.brigade("set", "--schedule", "liveness=")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["schedules"], {"drain": "sched-5"})
        self.brigade("set", "--schedule", "evening=")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["schedules"], {"drain": "sched-5"})
        self.brigade("set", "--schedule", "morning=sched-1")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["schedules"],
                         {"drain": "sched-5", "morning": "sched-1"})
        error = self.brigade("set", "--schedule", "drain", ok=False)
        self.assertIn("NAME=ID", error)
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["schedules"],
                         {"drain": "sched-5", "morning": "sched-1"})

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

    def test_set_reporting_changes_the_level_and_rejects_an_unknown_one(self):
        self.open()
        self.brigade("set", "--reporting", "every-turn")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["reporting"], "every-turn")
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: every-turn, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal")
        self.brigade("set", "--reporting", "digest")
        before = (self.at / "restaurant.json").read_text()
        error = self.brigade("set", "--reporting", "hourly", ok=False)
        for level in ("every-turn", "milestones", "digest"):
            self.assertIn(level, error)
        self.assertEqual((self.at / "restaurant.json").read_text(), before)
        opened = self.brigade("open", "--project-root", str(self.project), "--name", "Loud",
                              "--reporting", "hourly", ok=False)
        for level in ("every-turn", "milestones", "digest"):
            self.assertIn(level, opened)
        self.assertFalse((self.store / "bridge-kit" / "loud").exists())

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

    def test_brief_fields_file_matches_flags_and_keeps_shell_text_literal(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")
        fields = self.literal_brief_fields()
        flagged = self.brief_from_flags(fields)
        path = self.at / "brief-fields.json"
        path.write_text(json.dumps(fields), encoding="utf-8")
        filed = self.brigade("brief", "D1", "--fields", str(path))
        self.assertEqual(filed, flagged)
        goal = fields["goal"]
        self.assertIn(goal, filed)
        for part in ("`", "$B status", "$B close", "$(touch sentinel)"):
            self.assertIn(part, filed)
        self.assertIsNone(json.loads((self.at / "restaurant.json").read_text())["lastReportAt"])
        self.assertEqual(list((self.at / "closeouts").glob("*")), [])

    def test_brief_fields_stdin_matches_the_file(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")
        fields = self.literal_brief_fields()
        raw = json.dumps(fields)
        path = self.at / "brief-fields.json"
        path.write_text(raw, encoding="utf-8")
        filed = self.brigade("brief", "D1", "--fields", str(path))
        piped = self.brigade("brief", "D1", "--fields", "-", stdin=raw)
        self.assertEqual(piped, filed)
        self.assertIn("$(touch sentinel)", piped)

    def test_a_brief_waiting_on_stdin_leaves_the_store_to_other_commands(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")
        waiting = subprocess.Popen([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
                                    *_owner_words(self.at),
                                    "brief", "D1", "--fields", "-"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(0.3)
            self.assertIsNone(waiting.poll())
            others = [subprocess.Popen([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
                                        *_owner_words(self.at), *args],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                      for args in (("status",), ("watch",), ("ticket", "add", "--kind", "fix", "--summary", "second"))]
            outputs = [other.communicate(timeout=10) for other in others]
            self.assertEqual([other.returncode for other in others], [0, 0, 0], outputs)
            self.assertEqual(outputs[2][0].strip(), "T2")
            out, err = waiting.communicate(json.dumps(self.literal_brief_fields()), timeout=10)
        finally:
            waiting.kill()
        self.assertEqual(waiting.returncode, 0, err)
        self.assertIn("TICKETS: T1: Startup is slow", out)

    def test_a_shell_reads_brief_fields_without_running_them(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")
        fields = self.literal_brief_fields()
        flagged = self.brief_from_flags(fields)
        work = Path(self.temporary.name) / "shell"
        work.mkdir()
        script = (
            f"FIELDS='{work / 'fields.json'}'\n"
            f"B='{sys.executable} {SCRIPT} --store {self.store} --at {self.at} {' '.join(_owner_words(self.at))}'\n"
            "cat > \"$FIELDS\" <<'JSON'\n"
            + json.dumps(fields) + "\n"
            "JSON\n"
            "$B brief D1 --fields \"$FIELDS\"\n"
        )
        result = subprocess.run(["bash", "-c", script], cwd=work, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), flagged)
        self.assertFalse((work / "sentinel").exists())
        self.assertIsNone(json.loads((self.at / "restaurant.json").read_text())["lastReportAt"])

    def test_brief_fields_rejects_a_mix_and_bad_json(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")
        brief = self.at / "briefs" / "D1.md"
        fields = self.at / "brief-fields.json"
        error = self.brigade("brief", "D1", "--fields", str(fields), "--goal", "g", ok=False)
        self.assertIn("use either --fields or the field flags, not both", error)
        self.assertFalse(brief.exists())
        missing = self.at / "no-such-fields.json"
        error = self.brigade("brief", "D1", "--fields", str(missing), ok=False)
        self.assertIn("brief fields:", error)
        self.assertIn("no-such-fields.json", error)
        self.assertFalse(brief.exists())
        fields.write_text("[]\n", encoding="utf-8")
        error = self.brigade("brief", "D1", "--fields", str(fields), ok=False)
        self.assertIn("JSON must be an object", error)
        self.assertFalse(brief.exists())
        fields.write_text(json.dumps({"goal": "g", "acceptance": ["a"], "contract": "none", "verify": "v", "base": "main", "nope": "x"}),
                          encoding="utf-8")
        error = self.brigade("brief", "D1", "--fields", str(fields), ok=False)
        self.assertIn("unknown key nope", error)
        self.assertFalse(brief.exists())
        fields.write_text(json.dumps({"goal": "g", "acceptance": "a", "contract": "none", "verify": "v", "base": "main"}), encoding="utf-8")
        error = self.brigade("brief", "D1", "--fields", str(fields), ok=False)
        self.assertIn("acceptance must be a list of strings", error)
        self.assertFalse(brief.exists())

    NEEDS_CONTRACT = ("brigade: brief needs a contract; pass --contract <text> or the contract key in --fields, "
                      "and write none when the item has no safety, privacy, or integrity property")
    FLAGS = ("--goal", "g", "--acceptance", "a", "--verify", "v", "--paths", "src/boot.ts", "--lease", "L4",
             "--base", "origin/main")

    def briefable(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")

    def test_brief_refuses_flags_with_no_contract_or_a_contract_of_only_whitespace(self):
        self.briefable()
        self.assertEqual(self.brigade("brief", "D1", *self.FLAGS, ok=False), self.NEEDS_CONTRACT)
        self.assertEqual(self.brigade("brief", "D1", *self.FLAGS, "--contract", " \n\t", ok=False), self.NEEDS_CONTRACT)
        self.assertFalse((self.at / "briefs" / "D1.md").exists())
        self.assertFalse((self.at / "briefs" / "D1.contract.md").exists())

    def test_brief_refuses_a_fields_file_that_has_goal_acceptance_verify_and_base_and_no_contract(self):
        self.briefable()
        fields = self.at / "brief-fields.json"
        fields.write_text(json.dumps({"goal": "g", "acceptance": ["a"], "verify": "v", "base": "origin/main"}),
                          encoding="utf-8")
        self.assertEqual(self.brigade("brief", "D1", "--fields", str(fields), ok=False), self.NEEDS_CONTRACT)
        fields.write_text(json.dumps({"goal": "g", "acceptance": ["a"], "verify": "v", "base": "origin/main",
                                      "contract": "  "}), encoding="utf-8")
        self.assertEqual(self.brigade("brief", "D1", "--fields", str(fields), ok=False), self.NEEDS_CONTRACT)
        fields.write_text(json.dumps({"goal": "g", "acceptance": ["a"], "verify": "v", "base": "origin/main",
                                      "contract": None}), encoding="utf-8")
        self.assertEqual(self.brigade("brief", "D1", "--fields", str(fields), ok=False),
                         "brigade: brief fields: contract is null")
        self.assertFalse((self.at / "briefs" / "D1.md").exists())

    def test_brief_refuses_a_contract_flag_next_to_a_fields_file(self):
        self.briefable()
        fields = self.at / "brief-fields.json"
        fields.write_text(json.dumps(self.literal_brief_fields()), encoding="utf-8")
        self.assertEqual(self.brigade("brief", "D1", "--fields", str(fields), "--contract", "none", ok=False),
                         "brigade: use either --fields or the field flags, not both")

    def test_brief_renders_the_contract_none_between_acceptance_and_verify(self):
        self.briefable()
        text = self.brigade("brief", "D1", *self.FLAGS, "--contract", "none")
        self.assertIn("\nACCEPTANCE:\n- a\n\nCONTRACT:\nnone\n\nVERIFY:\n- v\n", text)
        self.assertEqual((self.at / "briefs" / "D1.contract.md").read_text(), "none\n")

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

    def test_brief_renders_and_stores_a_contract_as_given_from_the_flag_and_from_a_fields_file(self):
        self.briefable()
        for form, words in (("flag", (*self.FLAGS, "--contract", self.VERBATIM)), ("fields", self.contract_fields(self.VERBATIM))):
            with self.subTest(form=form):
                text = self.brigade("brief", "D1", *words)
                self.assertIn(f"\nACCEPTANCE:\n- a\n\nCONTRACT:\n{self.VERBATIM}\n\nVERIFY:\n- v\n", text)
                self.assertEqual((self.at / "briefs" / "D1.contract.md").read_bytes(), (self.VERBATIM + "\n").encode())
                (self.at / "briefs" / "D1.contract.md").unlink()

    def test_contract_prints_the_given_text_and_one_newline_for_a_text_with_whitespace_at_its_ends_and_one_with_a_carriage_return(self):
        self.briefable()
        returned = "First line.\r\nSecond line."
        for contract in (self.VERBATIM, returned, "none"):
            for form, words in (("flag", (*self.FLAGS, "--contract", contract)), ("fields", self.contract_fields(contract))):
                with self.subTest(contract=contract, form=form):
                    brief = self.raw_stdout("brief", "D1", *words)
                    rendered = f"\nCONTRACT:\n{contract}\n\nVERIFY:\n".encode()
                    self.assertIn(rendered, brief)
                    self.assertIn(rendered, (self.at / "briefs" / "D1.md").read_bytes())
                    self.assertEqual(self.raw_stdout("contract", "D1"), (contract + "\n").encode())

    def test_contract_prints_the_text_a_brief_stored_from_flags_and_from_a_fields_file(self):
        self.briefable()
        contract = "A refused write changes no table.\n\n  - `log.tsv` gains no row."
        self.brigade("brief", "D1", *self.FLAGS, "--contract", contract)
        self.assertEqual(self.brigade("contract", "D1"), contract)
        fields = self.at / "brief-fields.json"
        other = "A reader never sees a half-written row.\nNothing else."
        fields.write_text(json.dumps({"goal": "g", "acceptance": ["a"], "verify": "v", "base": "origin/main",
                                      "paths": "src/boot.ts", "lease": "L4", "contract": other}), encoding="utf-8")
        self.brigade("brief", "D1", "--fields", str(fields))
        self.assertEqual(self.brigade("contract", "D1"), other)

    def test_contract_refuses_an_item_with_no_contract_file_and_an_item_that_does_not_exist(self):
        self.briefable()
        self.brigade("brief", "D1", *self.FLAGS, "--contract", "none")
        (self.at / "briefs" / "D1.contract.md").unlink()
        self.assertEqual(self.brigade("contract", "D1", ok=False),
                         "brigade: D1 has no contract on record; run brief with --contract")
        self.assertEqual(self.brigade("contract", "D9", ok=False), "brigade: no D9 in dishes.tsv")

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

    def test_dropping_an_item_returns_its_tickets_to_waiting(self):
        from unittest import mock
        self.init_landing()
        ref = "https://github.com/o/r/issues/1"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("set", "--intake", "github")
            self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "bug", "--source", "github", "--ref", ref), "T1")
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "fix",
                                          "--paths", "a.py"), "D1 (lease L1 held by perf/D1)")
            self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
            self.assertEqual(self.brigade("ticket", "list"), f"T1 waiting normal fix [github] bug {ref}")
            self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["dish"], "")
            self.assertEqual([(row["kind"], row["id"], row["state"], row["note"]) for row in self.log_rows()][-2:],
                             [("dish", "D1", "dropped", "fix (released L1)"),
                              ("ticket", "T1", "waiting", "bug (back from dropped D1)")])
            self.assertIn("waiting tickets: 1", self.brigade("status"))
            self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "bug again", "--source", "github", "--ref", ref,
                                          ok=False), f"brigade: {ref} is already T1 (waiting); nothing added")
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "fix",
                                          "--paths", "a.py"), "D2 (lease L2 held by perf/D2)")
            self.assertEqual(self.brigade("dish", "D2", "--state", "dropped"), "D2 dropped; T1 waiting again")
            self.brigade("ticket", "set", "T1", "--state", "dropped")
            self.brigade("set", "--intake", "")
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_dropping_an_item_leaves_its_other_tickets_alone(self):
        self.open()
        for summary in ("one", "two"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "a")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b")
        self.brigade("ticket", "set", "T2", "--state", "done")
        self.assertEqual(self.brigade("dish", "D2", "--state", "dropped"), "D2 dropped")
        self.assertEqual(self.brigade("ticket", "list"), "T1 assigned normal fix [user] one\nT2 done normal fix [user] two")

    def test_blocked_counts_follow_the_block_that_holds_now(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the gate")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the gate",
                         "--paths", "src", ok=False)
            self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the gate blocked: lease")
            self.assertIn("Perf (reports milestones): waiting tickets: 1 (1 blocked)", self.brigade("walk"))
            self.land("lease", "release", "L1")
            self.assertTrue(self.brigade("watch").splitlines()[1].startswith("T1: unblocked; run fire"))
            self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the gate")
            self.assertIn("Perf (reports milestones): waiting tickets: 1\n", self.brigade("walk") + "\n")
            self.land("lease", "claim", "--holder", "engine/D2", "--paths", "src")
            self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the gate blocked: lease")

    def test_fire_and_watch_name_the_worker_cap_first_when_both_caps_hold(self):
        from unittest import mock
        self.init_landing("--cap", "1")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "one", "--paths", "src")
            refused = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "two",
                                   "--paths", "docs", ok=False)
            self.assertEqual(refused, "brigade: nothing fired: 1 of 1 workers running")
            self.assertEqual(self.brigade("watch").splitlines()[-1], "T2: waiting for a worker (1 of 1 running)")
            self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] two blocked: workers")
            self.brigade("set", "--workers", "2")
            self.assertEqual(self.brigade("watch").splitlines()[-1],
                             "T2: waiting for room in the repository (1 of 1 changes in flight)")
            self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"),
                             "T2 waiting normal fix [user] two blocked: repository")

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

    def test_a_new_branch_prints_the_commands_that_lease_its_fragment(self):
        from unittest import mock
        self.init_landing("--cap", "3")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            wide = "src,changes/perf%2Fd1.md,changes/perf%2Fd1-r2.md"
            self.assertEqual(self.renamed(), "\n".join([
                "D1 in-progress",
                "D1: L1 does not cover changes/perf%2Fd1-r2.md; cover it with these commands:",
                f"  $L lease claim --holder perf/D1 --paths {wide} --owner perf/@1",
                f"  $B dish D1 --lease <new lease> --paths {wide}",
                "  $L lease release L1 --owner perf/@1",
            ]))
            row = self.table_row(self.at, "dishes.tsv", "D1")
            self.assertEqual((row["branch"], row["lease"], row["paths"]), ("perf/d1-r2", "L1", "src,changes/perf%2Fd1.md"))
            self.assertEqual(self.lease_lines(), ["L1 active perf/D1: changes/perf%2Fd1.md, src"])
            self.assertEqual(self.land("lease", "claim", "--holder", "perf/D1", "--paths", wide, "--owner", "perf/@1"), "L2")
            self.assertEqual(self.brigade("dish", "D1", "--lease", "L2", "--paths", wide), "D1 in-progress")
            self.land("lease", "release", "L1", "--owner", "perf/@1")
            self.assertEqual(self.lease_lines(), ["L2 active perf/D1: changes/perf%2Fd1-r2.md, changes/perf%2Fd1.md, src"])
            self.assertEqual(self.brigade("dish", "D1", "--branch", "perf/d1-r2"), "D1 in-progress")

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

    def test_a_branch_rename_by_a_replaced_owner_claims_nothing(self):
        from unittest import mock
        self.init_landing("--cap", "3")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            taken = []
            result = self.renamed(lambda: taken or taken.append(
                self.brigade("set", "--thread", "replacement", "--replace")))
            self.assertEqual(len(taken), 1)
            self.assertIn("$L lease claim --holder perf/D1", result)
            self.assertEqual(self.lease_lines(), ["L1 active perf/D1: changes/perf%2Fd1.md, src"])
            stale = self.run_at(self.at, "--owner", "coordinator@1", "dish", "D1", "--branch", "perf/d1-r3", ok=False)
            self.assertEqual(stale, "brigade: owner coordinator@1 is stale; this store is owned by replacement@2")
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["branch"], "perf/d1-r2")
            self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_a_branch_rename_after_submit_says_the_lease_cannot_change(self):
        self.init_landing("--cap", "3")
        from unittest import mock
        with mock.patch.dict(os.environ, self.land_env()):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "one",
                         "--branch", "perf/d1", "--paths", "a.txt")
            sha = self.worker_commit("perf/d1", {"a.txt": "changed\n"})
            self.passed(sha)
            self.submit(sha)
            self.brigade("dish", "D1", "--state", "queued")
            self.assertEqual(self.brigade("dish", "D1", "--branch", "perf/d1-r2"), "\n".join([
                "D1 queued",
                "D1: L1 is submitted, so it cannot take changes/perf%2Fd1-r2.md; the submitted commit lands as it is. "
                "Run dish D1 --branch perf/d1-r2 again if its entry bounces",
            ]))
            self.assertEqual(self.lease_lines(), ["L1 submitted perf/D1: a.txt, changes/perf%2Fd1.md"])

    def test_open_prints_the_sibling_on_the_same_project_root(self):
        self.open()
        menu = (self.at / "menu.md").read_text()
        menu = menu.replace(
            "What this restaurant exists to achieve, in one or two sentences.",
            "Keep startup under 400 ms.")
        menu = menu.replace(
            "Work this restaurant does not take, even when asked.",
            "Docs and release notes\nVendor upgrades")
        (self.at / "menu.md").write_text(menu)
        self.brigade("set", "--thread", "thread-perf")
        docs_dir = self.store / "bridge-kit" / "docs"
        self.assertEqual(
            self.brigade("open", "--project-root", str(self.project), "--name", "Docs"),
            "\n".join([
                f"opened {docs_dir}",
                f"sibling Perf ({self.at}), thread thread-perf",
                "  purpose: Keep startup under 400 ms.",
                "  off the menu: Docs and release notes; Vendor upgrades",
            ]))
        self.assertEqual(
            self.brigade("open", "--project-root", str(self.project), "--name", "Perf"),
            "\n".join([
                f"exists {self.at}",
                "thread thread-perf already recorded",
                f"sibling Docs ({docs_dir}), thread not recorded",
                "  purpose: not written yet",
                "  off the menu: not written yet",
            ]))

    def test_open_refuses_a_directory_that_holds_another_project_root(self):
        first = (Path(self.temporary.name) / "left" / "app").resolve()
        second = (Path(self.temporary.name) / "right" / "app").resolve()
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        directory = self.store / "app" / "docs"
        self.assertEqual(
            self.brigade("open", "--project-root", str(first), "--name", "Docs"),
            f"opened {directory}")
        before = (directory / "restaurant.json").read_bytes()
        error = self.brigade("open", "--project-root", str(second), "--name", "Docs", ok=False)
        self.assertEqual(
            error,
            f"brigade: {directory} already holds a coordinator for {first}; pick another --name")
        self.assertEqual((directory / "restaurant.json").read_bytes(), before)

    def test_two_processes_opening_one_name_on_different_roots_leave_one_store(self):
        roots = []
        for label in ("left", "right"):
            path = (Path(self.temporary.name) / label / "app").resolve()
            path.mkdir(parents=True)
            roots.append(path)
        directory = self.store / "app" / "docs"
        procs = [
            subprocess.Popen(
                [sys.executable, str(SCRIPT), "--store", str(self.store),
                 "open", "--project-root", str(root), "--name", "Docs"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for root in roots
        ]
        finished = []
        for proc in procs:
            out, err = proc.communicate(timeout=15)
            finished.append((proc.returncode, out, err))
        self.assertEqual(sorted(code for code, _, _ in finished), [0, 1])
        self.assertEqual(len(list((directory.parent).glob("*/restaurant.json"))), 1)
        meta = json.loads((directory / "restaurant.json").read_text())
        winner = next(item for item in finished if item[0] == 0)
        loser = next(item for item in finished if item[0] == 1)
        self.assertIn(meta["projectRoot"], [str(root) for root in roots])
        self.assertTrue(winner[1].startswith(f"opened {directory}"))
        self.assertIn(f"already holds a coordinator for {meta['projectRoot']}", loser[2])
        self.assertIn("pick another --name", loser[2])

    def test_set_thread_refuses_to_replace_a_recorded_thread(self):
        self.open()
        self.brigade("set", "--thread", "thread-1")
        self.brigade("set", "--thread", "thread-1")
        before = (self.at / "restaurant.json").read_bytes()
        error = self.brigade("set", "--thread", "thread-2", ok=False)
        self.assertEqual(error, "brigade: thread thread-1 already recorded")
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)
        self.brigade("set", "--thread", "thread-2", "--replace")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["thread"], "thread-2")

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

    def test_a_losing_open_keeps_the_owners_ticket_and_menu(self):
        case = Path(self.temporary.name) / "race"
        case.mkdir()
        project = Path(self.temporary.name) / "app"
        project.mkdir()
        directory = self.store / "app" / "docs"
        args = ["--store", str(self.store), "open", "--project-root", str(project), "--name", "Docs"]
        owner = loser = None
        try:
            owner = self._spawn_race("owner", case, args)
            _wait_for_path(case / "metadata-ready")
            loser = self._spawn_race("loser", case, args)
            _wait_for_path(case / "loser-saw-meta")
            self.assertIsNone(owner.poll())
            self.assertIsNone(loser.poll())
            if not (directory / "rail.tsv").exists():
                (case / "loser-after-meta").touch()
                _wait_for_path(case / "loser-at-scaffold")
                (case / "owner-proceed").touch()
                owner_code, owner_out, owner_err = self._finish_race(owner)
                owner = None
            else:
                owner_code = owner_out = owner_err = None
            added = subprocess.run(
                [sys.executable, str(SCRIPT), "--at", str(directory), "ticket", "add", "--kind", "fix", "--summary", "Keep this ticket"],
                capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            self.assertEqual((added.returncode, added.stdout), (0, "T1\n"), added.stderr)
            menu = directory / "menu.md"
            menu.write_text(menu.read_text() + "Keep the menu sentence.\n")
            if (case / "loser-at-scaffold").exists():
                (case / "loser-scaffold-proceed").touch()
            else:
                (case / "loser-after-meta").touch()
            loser_code, loser_out, loser_err = self._finish_race(loser)
            loser = None
            if owner is not None:
                (case / "owner-proceed").touch()
                owner_code, owner_out, owner_err = self._finish_race(owner)
                owner = None
        finally:
            for proc in (owner, loser):
                if proc is not None and proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
        self.assertEqual((owner_code, owner_err), (0, ""), owner_out)
        self.assertEqual(owner_out, f"opened {directory}")
        self.assertEqual((loser_code, loser_err), (0, ""), loser_out)
        self.assertEqual(loser_out, f"exists {directory}")
        self.assertIn("Keep this ticket", (directory / "rail.tsv").read_text())
        self.assertIn("Keep the menu sentence.", (directory / "menu.md").read_text())

    def test_open_rejects_a_restaurant_json_that_is_not_valid_json(self):
        self.at.mkdir(parents=True)
        (self.at / "restaurant.json").write_text('{"restaurant":')
        started = time.monotonic()
        error = self.brigade("open", "--project-root", str(self.project), "--name", "Perf", ok=False)
        elapsed = time.monotonic() - started
        self.assertEqual(error, f"brigade: {self.at / 'restaurant.json'} is not valid JSON")
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 3)
        self.assertEqual((self.at / "restaurant.json").read_text(), '{"restaurant":')
        self.assertFalse((self.at / "menu.md").exists())
        self.assertFalse((self.at / "rail.tsv").exists())

    def test_open_accepts_restaurant_json_that_becomes_valid_within_one_second(self):
        self.at.mkdir(parents=True)
        path = self.at / "restaurant.json"
        path.write_text('{"restaurant":')
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--store", str(self.store), "open",
             "--project-root", str(self.project), "--name", "Perf"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        try:
            time.sleep(0.2)
            self.assertIsNone(proc.poll())
            path.write_text(json.dumps({
                "restaurant": "Perf",
                "projectRoot": str(self.project.resolve()),
                "landing": "merge",
                "thread": None,
                "schedules": {},
            }))
            out, err = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            self.fail(f"timed out\nstdout:\n{out}\nstderr:\n{err}")
        self.assertEqual((proc.returncode, err.strip()), (0, ""), out)
        self.assertTrue(out.startswith(f"exists {self.at}"))


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

    def test_a_refused_fire_stays_blocked_until_the_lease_is_free(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the gate")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            refused = self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the gate",
                                   "--paths", "src", "--timebox", "60", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by engine/D1", refused)
            self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] Fix the gate blocked: lease")
            self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: waiting on L1 (engine/D1)")
            self.land("lease", "release", "L1")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: waiting on L2 (engine/D1)")
            self.land("lease", "release", "L2")
            line = "T1: unblocked; run fire --tickets=T1 --station=bug-fix '--summary=Fix the gate' --paths=src --timebox=60"
            self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + line)
            started = self.brigade(*shlex.split(line.split("run ", 1)[1]))
            self.assertEqual(started, "D1 (lease L3 held by perf/D1)")
            self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["paths"], "src,changes/perf%2Fd1.md")
            self.assertEqual(self.brigade("ticket", "list"), "T1 assigned normal fix [user] Fix the gate")

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

    def test_the_worker_cap_defaults_to_two_and_a_later_fire_clears_the_block(self):
        self.open()
        self.assertNotIn("workers", json.loads((self.at / "restaurant.json").read_text()))
        for summary in ("one", "two", "three"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "a"), "D1")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b"), "D2")
        refused = self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "c", ok=False)
        self.assertIn("nothing fired: 2 of 2 workers running", refused)
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"),
                         "T3 waiting normal fix [user] three blocked: workers")
        self.assertIn("T3: waiting for a worker (2 of 2 running)", self.brigade("watch"))
        self.brigade("set", "--workers", "3")
        self.assertEqual(self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "c"), "D3")
        self.assertNotIn("blocked:", self.brigade("ticket", "list"))

    def test_workers_one_refuses_fire_and_a_dish_that_is_not_already_counted(self):
        opened = self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                              "--workers", "1")
        self.assertEqual(opened, f"opened {self.at}")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["workers"], 1)
        for summary in ("one", "two", "three"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "a"), "D1")
        refused = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b", ok=False)
        self.assertIn("nothing fired: 1 of 1 workers running", refused)
        self.brigade("dish", "D1", "--state", "sent-back")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b"), "D2")
        self.brigade("dish", "D2", "--state", "blocked")
        self.assertEqual(self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "c"), "D3")
        sent = self.brigade("dish", "D1", "--state", "in-progress", ok=False)
        review = self.brigade("dish", "D2", "--state", "in-review", ok=False)
        self.assertIn("1 of 1 workers running", sent)
        self.assertIn("1 of 1 workers running", review)
        self.assertEqual(self.brigade("dish", "D3", "--state", "in-review"), "D3 in-review")
        self.assertEqual(self.brigade("dish", "D3", "--state", "in-progress", "--thread", "worker-2"), "D3 in-progress")
        before = (self.at / "restaurant.json").read_text()
        error = self.brigade("set", "--workers", "0", ok=False)
        self.assertIn("workers must be 1 or more", error)
        self.assertEqual((self.at / "restaurant.json").read_text(), before)
        missing = self.brigade("open", "--project-root", str(self.project), "--name", "Nope",
                               "--workers", "0", ok=False)
        self.assertIn("workers must be 1 or more", missing)
        self.assertFalse((self.store / "bridge-kit" / "nope").exists())

    def test_open_warns_when_workers_meet_the_repository_cap_and_a_sibling_exists(self):
        from unittest import mock
        self.init_landing("--cap", "2")
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            first = self.brigade("open", "--project-root", str(self.project), "--name", "Perf",
                                 "--workers", "4")
            self.assertNotIn("warning:", first)
            second = self.brigade("open", "--project-root", str(self.project), "--name", "Docs",
                                  "--workers", "2")
            self.assertIn("warning: workers 2 is at or above the repository cap of 2 while a sibling exists", second)
            third = self.brigade("open", "--project-root", str(self.project), "--name", "Quiet",
                                 "--workers", "1")
            self.assertNotIn("warning:", third)

    def test_fire_leases_the_branch_changelog_fragment_and_dish_records_a_new_lease(self):
        from unittest import mock
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
            self.brigade("set", "--thread", "thread-coord")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
            self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                         "--branch", "docs/a", "--paths", "src/a.py")
            text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "origin/main")
            self.assertIn("leased to you as L1: src/a.py,changes/docs%2Fa.md.", text)
            self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "s",
                         "--branch", "docs/a%b", "--paths", "src/b.py")
            text = self.brigade("brief", "D2", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "origin/main")
            self.assertIn("leased to you as L2: src/b.py,changes/docs%2Fa%25b.md.", text)
            self.assertEqual(text.count("changes/docs%2Fa%25b.md"), 1)
            self.brigade("dish", "D1", "--lease", "L9", "--paths", "src/a.py,changes/docs%2Fa.md")
            text = self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "origin/main")
            self.assertIn("leased to you as L9: src/a.py,changes/docs%2Fa.md.", text)

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
        self.assertIn("nothing fired: T1 is assigned, not waiting", err)
        self.assertIn("L7", err)
        self.assertIn("lease L7 is held elsewhere", err)

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
        refused = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--race-child", "fake-land", str(case),
                                  "--store", str(self.store), "--at", str(self.at),
                                  "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s", "--paths", "src"],
                                 capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
        self.assertIn("blocked: lease", self.brigade("ticket", "list"))
        watching = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--race-child", "fake-land", str(case),
                                     "--store", str(self.store), "--at", str(self.at), "watch"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        try:
            _wait_for_path(case / "checking")
            added = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at),
                                    "ticket", "add", "--kind", "fix", "--summary", "two"],
                                   capture_output=True, text=True, timeout=10)
            self.assertEqual(added.stdout.strip(), "T2", added.stderr)
            (case / "check-proceed").touch()
            out, err = watching.communicate(timeout=10)
        finally:
            watching.kill()
        self.assertEqual(watching.returncode, 0, err)
        self.assertEqual(out.strip(), "workers: 0 of 2 running, 2 idle, auto-start: normal\nT1: waiting on L1 (engine/D1)")

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

    def test_the_third_send_back_refuses_another_fix_round(self):
        self.started()
        self.assertEqual(self.sent_back(2), "D1 sent-back")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: sent back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.send_back("a3"), "D1 sent-back; 3 send-backs, decision pending")
        before = tuple((self.at / name).read_bytes() for name in ("dishes.tsv", "log.tsv", "86.tsv"))
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", "w9", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(tuple((self.at / name).read_bytes() for name in ("dishes.tsv", "log.tsv", "86.tsv")), before)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", "a3"), "D1 in-review")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)

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

    def test_86_add_round_budget_is_refused_below_three_send_backs(self):
        self.started()
        table = (self.at / "86.tsv").read_bytes()
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 0 send-backs since its last pass or round decision; the decision opens at 3")
        self.sent_back(1)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 1 send-back since its last pass or round decision; the decision opens at 3")
        self.assertEqual(self.brigade("86", "add", "--dish", "D9", "--round-budget", ok=False), "brigade: no D9 in dishes.tsv")
        self.assertEqual((self.at / "86.tsv").read_bytes(), table)

    def test_86_add_takes_a_question_or_the_round_budget(self):
        self.started()
        self.sent_back(3)
        needs = "brigade: 86 add needs --question, --options, and --default"
        self.assertEqual(self.brigade("86", "add", ok=False), needs)
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?", "--options", "a, b", ok=False), needs)
        alone = "brigade: 86 add --round-budget takes --dish and no --question, --options, or --default"
        self.assertEqual(self.brigade("86", "add", "--round-budget", ok=False), alone)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, "--question", "Move?", ok=False), alone)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, "--default", "drop", ok=False), alone)
        self.assertEqual((self.at / "86.tsv").read_text().splitlines(),
                         ["id\tat\tstate\tdish\tquestion\toptions\tdefault\tanswer\tkind\tanswered"])
        self.assertEqual(self.brigade("86", "add", "--question", "Ship it?", "--options", "yes, no", "--default", "no"), "Q1")
        self.assertEqual(self.park_item(), "Q2")

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

    def test_known_limits_closes_the_round_decision_and_lifts_the_refusal(self):
        self.round_decision_lifts_the_refusal("Known limits.", "known limits")

    def test_redesign_closes_the_round_decision_and_lifts_the_refusal(self):
        self.round_decision_lifts_the_refusal("redesign", "redesign")

    def test_drop_closes_the_round_decision_and_lifts_the_refusal(self):
        self.round_decision_lifts_the_refusal("drop", "drop")

    def test_the_count_starts_again_after_a_round_decision(self):
        self.started()
        self.sent_back(3)
        self.brigade(*self.ROUND_BUDGET)
        self.brigade("86", "answer", "Q1", "--answer", "redesign")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.sent_back(2, start=4), "D1 sent-back")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.send_back("a6"), "D1 sent-back; 3 send-backs, decision pending")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q2")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q2")["question"], self.ROUND_QUESTION)

    PASS = ("pass", "record", "D1", "--verdict", "pass", "--author", CODEX, "--verifier", CLAUDE, "--sha")
    NO_DECISION = "brigade: D1 is {}; it owes no round decision"

    def test_a_pass_at_three_send_backs_settles_the_count(self):
        self.started()
        self.assertEqual(self.sent_back(3), "D1 sent-back; 3 send-backs, decision pending")
        self.assertEqual(self.brigade(*self.PASS, "a3"), "D1 passed")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: passed, not submitted")
        self.assertEqual(self.brigade(*self.FIX_BRIEF, ok=False),
                         "brigade: D1 is passed; brief a dish that is in progress or sent back")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("passed"))
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued"), "D1 queued")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("queued"))
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", "bounce-worker"), "D1 in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (thread bounce-worker)")
        self.assertEqual(self.send_back("a4"), "D1 sent-back")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 1 send-back since its last pass or round decision; the decision opens at 3")
        self.assertEqual(self.brigade("86", "list"), "no open decisions")

    def test_a_merged_item_owes_nothing_and_its_ticket_starts_a_new_count(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade(*self.PASS, "a4"), "D1 passed")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "a4", "--verdict", "send-back", "--author", CODEX,
                                      "--verifier", CLAUDE, "--member"),
                         "D1: member send-back on record; D1 stays passed")
        self.assertEqual(self.brigade("dish", "D1", "--state", "merged"), "D1 merged")
        for sha in ("m1", "m2", "m3"):
            self.assertEqual(self.send_back(sha, "--late"), "D1: late send-back on record; D1 stays merged")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("merged"))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--state", "waiting"), "T1 waiting")
        self.assertEqual(self.brigade(*self.FIRE_AGAIN), "D2")
        self.assertEqual(self.brigade("pass", "record", "D2", "--sha", "b1", "--verdict", "send-back", "--author", CODEX,
                                      "--verifier", CLAUDE), "D2 sent-back")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD2: sent back")
        self.assertEqual(self.brigade("86", "list"), "no open decisions")

    def test_a_dropped_item_owes_nothing_and_its_ticket_starts_a_new_count(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("dropped"))
        self.assertEqual(self.brigade(*self.FIRE_AGAIN), "D2")
        self.assertEqual(self.brigade("pass", "record", "D2", "--sha", "b1", "--verdict", "send-back", "--author", CODEX,
                                      "--verifier", CLAUDE), "D2 sent-back")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD2: sent back")
        self.assertEqual(self.brigade("86", "list"), "no open decisions")

    def test_a_dropped_item_put_back_in_progress_owes_its_decision_again(self):
        self.started()
        self.sent_back(3)
        self.brigade("dish", "D1", "--state", "dropped")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.brigade("watch").splitlines()[1], self.PENDING)
        self.assertEqual(self.brigade("dish", "D1", "--thread", "fresh-worker", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade(*self.FIRE_AGAIN, ok=False), self.NO_FIRE)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")

    def test_a_late_send_back_written_before_a_pass_stops_counting_at_the_pass(self):
        self.started()
        self.sent_back(2)
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.send_back("a0", "--late"),
                         "D1: late send-back on record; D1 stays in-progress; 3 send-backs, decision pending")
        self.passed("a3")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: passed, not submitted")
        self.brigade("dish", "D1", "--state", "queued")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.send_back("a4"), "D1 sent-back")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 1 send-back since its last pass or round decision; the decision opens at 3")

    def test_late_send_backs_written_after_a_pass_count_once_the_item_is_back_in_progress(self):
        self.started()
        self.passed("a1")
        for sha in ("m1", "m2", "m3"):
            self.assertEqual(self.send_back(sha, "--late"), "D1: late send-back on record; D1 stays passed")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: passed, not submitted")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), self.NO_DECISION.format("passed"))
        self.brigade("dish", "D1", "--state", "queued")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade("dish", "D1", "--thread", "bounce-worker", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "known limits"), "Q1 answered")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "bounce-worker"), "D1 in-progress")

    def unchanged(self, before=None):
        now = tuple((self.at / name).read_bytes() for name in ("dishes.tsv", "rail.tsv", "log.tsv", "86.tsv"))
        if before is not None:
            self.assertEqual(now, before)
        return now

    def test_a_detour_through_review_starts_no_fix_round(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review"), "D1 in-review")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        before = self.unchanged()
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", "--thread", "fresh-worker", ok=False),
                         self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--thread", "fresh-worker", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("dish", "D1", "--task", "fresh-task", ok=False), self.NO_FIX_ROUND)
        self.unchanged(before)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade("86", "list"), "no open decisions")

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

    def test_a_late_send_back_below_the_budget_names_no_decision(self):
        self.started()
        self.sent_back(1)
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.send_back("a0", "--late"), "D1: late send-back on record; D1 stays in-progress")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in progress with no worker thread; launch a fresh worker")
        self.assertEqual(self.brigade("dish", "D1", "--thread", "w2"), "D1 in-progress")

    FIRE_AGAIN = ("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fourth fix", "--thread", "fresh-worker")
    NO_FIRE = ("brigade: T1 is D1's ticket; D1 has 3 send-backs; no fix round until its decision is answered; "
               "run 86 add --dish D1 --round-budget")

    def test_fire_refuses_a_ticket_of_an_item_that_owes_its_decision(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade(*self.FIRE_AGAIN, ok=False), self.NO_FIRE)
        self.assertEqual(self.brigade("ticket", "set", "T1", "--state", "waiting"), "T1 waiting")
        before = self.unchanged()
        self.assertEqual(self.brigade(*self.FIRE_AGAIN, ok=False), self.NO_FIRE)
        self.unchanged(before)
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] one")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        self.assertEqual(self.brigade(*self.FIRE_AGAIN, ok=False), self.NO_FIRE)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "redesign"), "Q1 answered")
        self.assertEqual(self.brigade(*self.FIRE_AGAIN), "D2")

    def test_fire_takes_the_ticket_of_an_item_under_its_budget(self):
        self.started()
        self.sent_back(2)
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
        self.assertEqual(self.brigade(*self.FIRE_AGAIN), "D2")

    FIX_BRIEF = ("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--base", "origin/main")

    def test_brief_refuses_an_item_that_owes_its_decision(self):
        self.started()
        self.brigade("set", "--thread", "c1")
        self.sent_back(2)
        self.assertIn("GOAL: g\n", self.brigade(*self.FIX_BRIEF))
        self.brigade("dish", "D1", "--state", "in-progress")
        self.send_back("a3")
        written = (self.at / "briefs" / "D1.md").read_bytes()
        before = self.unchanged()
        self.assertEqual(self.brigade(*self.FIX_BRIEF, ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--verify", "v", "--base", "origin/main", ok=False),
                         self.NO_FIX_ROUND)
        self.unchanged(before)
        self.brigade("dish", "D1", "--state", "in-review")
        self.assertEqual(self.brigade(*self.FIX_BRIEF, ok=False), self.NO_FIX_ROUND)
        self.assertEqual((self.at / "briefs" / "D1.md").read_bytes(), written)
        self.brigade(*self.ROUND_BUDGET)
        self.brigade("86", "answer", "Q1", "--answer", "redesign")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertIn("GOAL: g\n", self.brigade(*self.FIX_BRIEF))

    def test_an_ordinary_decision_answered_with_a_round_option_lifts_no_refusal(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Redesign the title wording?",
                                      "--options", "redesign, keep parked", "--default", "keep parked"), "Q1")
        held = "brigade: D1 is held by Q1; answer it, then run 86 add --dish D1 --round-budget"
        table = (self.at / "86.tsv").read_bytes()
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False), held)
        self.assertEqual((self.at / "86.tsv").read_bytes(), table)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "redesign"), "Q1 answered")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q1")["kind"], "")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q2")
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q2")["kind"], "round-budget")
        self.assertEqual(self.brigade("86", "answer", "Q2", "--answer", "redesign"), "Q2 answered")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")

    def test_late_rows_recorded_while_the_decision_is_open_stop_counting_at_its_answer(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q1")
        for count in (4, 5, 6):
            self.assertEqual(self.send_back(f"missed{count}", "--late"),
                             f"D1: late send-back on record; D1 stays sent-back; {count} send-backs, decision pending")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "redesign"), "Q1 answered")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertGreater(row["answered"], self.pass_rows()[-1][0])
        self.assertGreater(row["answered"], row["at"])
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: sent back")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.send_back("missed7", "--late"), "D1: late send-back on record; D1 stays in-progress")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 1 send-back since its last pass or round decision; the decision opens at 3")

    def test_a_decision_table_written_before_the_kind_and_answered_columns_still_reads(self):
        self.started()
        self.assertEqual(self.sent_back(3), "D1 sent-back; 3 send-backs, decision pending")
        stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        old = ["\t".join(["Q1", stamp, "answered", "D1", "Drop this item?", "drop, keep parked", "keep parked", "drop"]),
               "\t".join(["Q2", stamp, "open", "", "Ship it?", "yes, no", "no", ""])]
        table = self.at / "86.tsv"
        table.write_text("id\tat\tstate\tdish\tquestion\toptions\tdefault\tanswer\n" + "\n".join(old) + "\n")
        self.assertEqual(self.brigade("86", "list"), "Q2: Ship it? Options: yes, no. Default: no.")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q3")
        lines = table.read_text().splitlines()
        self.assertEqual(lines[1:3], old)
        self.assertEqual(lines[3].split("\t")[5:], ["known limits, redesign, drop, keep parked", "keep parked", "",
                                                     "round-budget", ""])
        self.assertEqual(self.brigade("86", "answer", "Q3", "--answer", "drop"), "Q3 answered")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        stored = [self.table_row(self.at, "86.tsv", ident) for ident in ("Q1", "Q2", "Q3")]
        self.assertEqual([(row["state"], row["answer"], row["kind"], bool(row["answered"])) for row in stored],
                         [("answered", "drop", "", False), ("open", "", "", False), ("answered", "drop", "round-budget", True)])
        for fields, line in ((old[1].split("\t")[:7], 5), ([*old[1].split("\t"), "", "", "extra"], 5)):
            table.write_text(table.read_text() + "\t".join(fields) + "\n")
            self.assertEqual(self.brigade("86", "list", ok=False),
                             f"brigade: bridge-kit/perf/86.tsv line {line} is malformed; fix or remove it")
            table.write_text("\n".join(table.read_text().splitlines()[:-1]) + "\n")

    def test_an_unrelated_answered_item_decision_lifts_no_refusal(self):
        self.started()
        self.sent_back(3)
        self.assertEqual(self.park_item(), "Q1")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "moved"), "Q1 answered")
        self.assertNotEqual(self.table_row(self.at, "86.tsv", "Q1")["answered"], "")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress", ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\n" + self.PENDING)
        self.assertEqual(self.brigade(*self.ROUND_BUDGET), "Q2")

    def test_member_send_backs_do_not_count_toward_the_round_budget(self):
        self.started()
        self.sent_back(2)
        for seat in ("grok/grok-4.7", "opencode/kimi-k3"):
            self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "a2", "--verdict", "send-back", "--author", CODEX,
                                          "--verifier", seat, "--member"),
                             "D1: member send-back on record; D1 stays sent-back")
        self.assertEqual(self.brigade("watch"), "workers: 0 of 2 running, 2 idle, auto-start: normal\nD1: sent back")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 2 send-backs since its last pass or round decision; the decision opens at 3")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")

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

    def test_86_list_names_the_item_and_keeps_a_blank_dish_in_the_old_format(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.assertEqual(self.brigade("86", "add", "--question", "Ship it?", "--options", "yes, no", "--default", "no"), "Q2")
        self.assertEqual(self.brigade("86", "list"), "\n".join([
            f"Q1 for D1: {HELD_QUESTION} Options: moved, keep parked. Default: keep parked.",
            "Q2: Ship it? Options: yes, no. Default: no.",
        ]))

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

    def test_a_replayed_keep_parked_relay_logs_inbox_done_once(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.open_admin()
        before = (self.at / "86.tsv").read_bytes()
        self.admin("request", "--to", "perf", "answer perf Q1: keep parked")
        self.assertEqual(self.brigade("inbox", "take"), "A1: answer perf Q1: keep parked")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        self.assertEqual(self.brigade("inbox", "take"), "A1: answer perf Q1: keep parked")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        self.assertEqual(self.brigade("inbox", "done", "A1"), "A1 done")
        self.assertEqual(self.brigade("inbox", "take"), "nothing handed to you")
        self.assertEqual(self.brigade("inbox", "done", "A1"), "A1 done")
        self.assertEqual((self.at / "86.tsv").read_bytes(), before)
        done = [row for row in self.log_rows() if row["kind"] == "inbox-done"]
        self.assertEqual([(row["id"], row["state"], row["note"]) for row in done],
                         [("A1", "done", "answer perf Q1: keep parked")])

    def test_moved_closes_an_item_decision_and_a_later_answer_writes_nothing(self):
        self.started()
        ordinary = self.brigade("watch")
        self.assertEqual(ordinary, "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (no worker recorded)")
        self.park_item()
        opened = [row for row in self.log_rows() if row["kind"] == "decision"]
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "Moved."), "Q1 answered")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["state"], row["answer"]), ("answered", "moved"))
        answered = [row for row in self.log_rows() if row["kind"] == "decision" and row["state"] == "answered"]
        self.assertEqual(len(answered), 1)
        self.assertEqual(len([row for row in self.log_rows() if row["kind"] == "decision"]), len(opened) + 1)
        self.assertEqual(self.brigade("watch"), ordinary)
        before = self.store_bytes()
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), "Q1 answered")
        self.assertEqual(self.store_bytes(), before)

    def test_a_second_refusal_after_keep_parked_prints_the_open_id(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"), STILL_OPEN)
        table = (self.at / "86.tsv").read_text()
        decisions = [row for row in self.log_rows() if row["kind"] == "decision"]
        other = "D1: cursor could not pass the seat's options: refused again. Move this coordinator to a provider that passes them?"
        self.assertEqual(self.park_item(other), "Q1")
        self.assertEqual((self.at / "86.tsv").read_text(), table)
        self.assertEqual([row for row in self.log_rows() if row["kind"] == "decision"], decisions)

    def test_free_text_keeps_the_hold_and_writes_nothing(self):
        self.started()
        self.assertEqual(self.park_item(), "Q1")
        before = self.store_bytes()
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "I moved it"), STILL_OPEN)
        self.assertEqual(self.store_bytes(), before)
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\n" + HELD_LINE)

    def test_an_item_decision_needs_a_default_and_a_closing_option(self):
        self.started()
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                      "--options", "keep parked", "--default", "keep parked", ok=False), ITEM_DECISION)
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                      "--options", "moved, keep parked", "--default", "ship it", ok=False), ITEM_DECISION)
        self.assertEqual((self.at / "86.tsv").read_text().splitlines(),
                         ["id\tat\tstate\tdish\tquestion\toptions\tdefault\tanswer\tkind\tanswered"])
        self.assertEqual(self.park_item(), "Q1")
        table = (self.at / "86.tsv").read_text()
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                      "--options", "keep parked", "--default", "keep parked"), "Q1")
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                      "--options", "moved, keep parked", "--default", "ship it"), "Q1")
        self.assertEqual((self.at / "86.tsv").read_text(), table)

    def test_an_item_decision_whose_options_collapse_to_the_default_once_stored_is_refused(self):
        self.started()
        before = tuple((self.at / name).read_bytes() for name in ("86.tsv", "log.tsv"))
        for options in ("keep parked, keep\nparked", "keep parked, keep\tparked", "keep parked, keep\r\nparked"):
            self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move?",
                                          "--options", options, "--default", "keep parked", ok=False),
                             ITEM_DECISION, repr(options))
            self.assertEqual(tuple((self.at / name).read_bytes() for name in ("86.tsv", "log.tsv")), before,
                             repr(options))

    def test_an_item_decision_stores_its_options_cleaned_and_closes_only_on_the_other_option(self):
        self.started()
        self.assertEqual(self.brigade("86", "add", "--dish", "D1", "--question", "Move\tthis?",
                                      "--options", "moved\r\nelsewhere, keep\nparked",
                                      "--default", "keep\tparked"), "Q1")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["question"], row["options"], row["default"]),
                         ("Move this?", "moved elsewhere, keep parked", "keep parked"))
        before = self.store_bytes()
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "keep parked"),
                         "Q1 still open; D1 stays held until 86 answer Q1 --answer 'moved elsewhere'")
        self.assertEqual(self.store_bytes(), before)
        self.assertEqual(self.brigade("86", "answer", "Q1", "--answer", "Moved elsewhere."), "Q1 answered")
        row = self.table_row(self.at, "86.tsv", "Q1")
        self.assertEqual((row["state"], row["answer"]), ("answered", "moved elsewhere"))

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

    def test_dropping_a_queued_item_whose_entry_bounced_releases_its_lease(self):
        self.started("--check", "test ! -e BROKEN", paths="README.md,BROKEN")
        self.brigade("dish", "D1", "--thread", "w1")
        sha = self.worker_commit("perf/d1", {"BROKEN": "x\n"})
        self.passed(sha)
        self.submit(sha)
        self.brigade("dish", "D1", "--state", "queued")
        self.land("land")
        self.assertIn("D1 holds L1", self.brigade("dish", "D1", "--state", "dropped", ok=False))
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped", "--stopped", "idle"), "D1 dropped; T1 waiting again")
        self.assertEqual(self.land("lease", "list"), "no leases held")

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

    def test_a_drop_waits_for_the_worker_and_then_releases_the_lease(self):
        self.started()
        self.brigade("dish", "D1", "--thread", "w1")
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped", ok=False),
                         "brigade: D1 holds L1 and its worker may still be running; "
                         "wait for its run with t3_thread_wait, then pass --stopped <run id>")
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "in-progress")
        self.assertEqual(self.land("lease", "list").split(" until ")[0], "L1 active perf/D1")
        self.assertIn("L1 held by perf/D1",
                      self.land("lease", "claim", "--holder", "engine/D1", "--paths", "README.md", ok=False))
        self.worker_commit("perf/d1", {"README.md": "late\n"})
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped", "--stopped", "r1"), "D1 dropped; T1 waiting again")
        dropped = [line for line in (self.at / "log.tsv").read_text().splitlines() if "\tD1\tdropped\t" in line]
        self.assertEqual(len(dropped), 1)
        self.assertIn("stopped r1", dropped[0])
        self.assertEqual(self.land("lease", "claim", "--holder", "engine/D1", "--paths", "README.md"), "L2")

    def test_a_late_worker_submit_after_the_drop_is_refused(self):
        self.started()
        self.brigade("dish", "D1", "--thread", "w1")
        self.worker_commit("perf/d1", {"README.md": "first\n"})
        case = Path(self.temporary.name)
        worktree = case / "perf-d1"
        script = (f"while [ ! -e {shlex.quote(str(case / 'go'))} ]; do sleep 0.01; done; "
                  f"echo late > README.md && git add -A && git -c user.name=t -c user.email=t@t commit -qm late && "
                  f"{shlex.quote(sys.executable)} {shlex.quote(str(ROOT / 't3/added/landing/scripts/land.py'))} "
                  f"--repo {shlex.quote(str(self.project))} submit --holder perf/D1 --branch perf/d1 "
                  f"--sha $(git rev-parse HEAD) --lease L1 --reviewer {CLAUDE}")
        worker = subprocess.Popen(["bash", "-c", script], cwd=worktree, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True)
        try:
            self.assertEqual(self.brigade("dish", "D1", "--state", "dropped", "--stopped", "r1"), "D1 dropped; T1 waiting again")
            engine = self.store / "bridge-kit" / "engine"
            self.run_at(engine, "open", "--project-root", str(self.project), "--name", "Engine")
            (engine / "menu.md").write_text("## Purpose\n\nEngine.\n")
            self.run_at(engine, "ticket", "add", "--kind", "fix", "--summary", "engine work")
            self.assertEqual(self.run_at(engine, "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "e",
                                         "--paths", "README.md"), "D1 (lease L2 held by engine/D1)")
            (case / "go").touch()
            _, err = worker.communicate(timeout=30)
        finally:
            worker.kill()
        self.assertEqual(worker.returncode, 1)
        self.assertEqual(err.strip(), "land: L1 is not an active lease held by perf/D1; claim one before submitting")
        self.assertEqual(self.land("status", "--holder", "perf/D1"), "no entries held by perf/D1")
        self.assertEqual([line.split(" until ")[0] for line in self.land("lease", "list").splitlines()],
                         ["L2 active engine/D1"])
        self.assertEqual(self.table_row(engine, "dishes.tsv", "D1")["lease"], "L2")

    def test_a_replaced_coordinators_paused_commands_change_nothing(self):
        self.init_landing()
        self.landing_env()
        docs = self.store / "bridge-kit" / "docs"
        self.run_at(docs, "open", "--project-root", str(self.project), "--name", "Docs")
        (docs / "menu.md").write_text("## Purpose\n\nDocs.\n")
        self.run_at(docs, "set", "--thread", "t1")
        old = ("--owner", "t1@1")
        self.assertIn("owner t1@1", self.run_at(docs, *old, "status"))
        self.run_at(docs, *old, "ticket", "add", "--kind", "fix", "--summary", "one")
        self.assertEqual(self.run_at(docs, *old, "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "s",
                                     "--paths", "README.md"), "D1 (lease L1 held by docs/D1)")
        self.run_at(docs, *old, "dish", "D1", "--thread", "w1")
        self.land("lease", "renew", "L1", "--ttl-hours", "0", "--owner", "docs/@1")
        stale_brigade = "brigade: owner t1@1 is stale; this store is owned by t2@2"
        stale_land = "land: owner docs/@1 is stale; docs/ is at generation 2"
        land_script = str(ROOT / "t3/added/landing/scripts/land.py")
        commands = [
            ("pause", ["--store", str(self.store), "--at", str(docs), *old, "ticket", "add", "--kind", "fix", "--summary", "late"], stale_brigade),
            ("pause", ["--store", str(self.store), "--at", str(docs), *old, "dish", "D1", "--state", "dropped", "--stopped", "r1"],
             stale_brigade),
            ("land-pause", ["--repo", str(self.project), "lease", "release", "L1", "--owner", "docs/@1"], stale_land),
            ("land-pause", ["--repo", str(self.project), "lease", "renew", "L1", "--owner", "docs/@1"], stale_land),
        ]
        running = []
        for number, (mode, args, message) in enumerate(commands):
            case = Path(self.temporary.name) / f"stale{number}"
            case.mkdir()
            running.append((case, self._spawn_race(mode, case, args), message))
        for case, _, _ in running:
            _wait_for_path(case / "paused", timeout=20)
        self.run_at(docs, "set", "--thread", "t2", "--replace")
        self.assertIn("owner t2@2", self.run_at(docs, "--owner", "t2@2", "status"))
        tables = ("rail.tsv", "log.tsv", "dishes.tsv", "restaurant.json")
        after_replace = {name: (docs / name).read_bytes() for name in tables}
        row = self.lease_row(1)
        for case, proc, message in running:
            (case / "proceed").touch()
            code, _, err = self._finish_race(proc)
            self.assertEqual((code, err), (1, message))
        self.assertEqual(self.run_at(docs, *old, "set", "--thread", "t2", "--workers", "9", ok=False), stale_brigade)
        self.assertEqual(self.run_at(docs, *old, "set", "--thread", "t1", ok=False), "brigade: thread t2 already recorded")
        self.assertEqual({name: (docs / name).read_bytes() for name in tables}, after_replace)
        self.assertEqual(self.lease_row(1), row)
        new = ("--owner", "t2@2")
        self.assertEqual(self.run_at(docs, *new, "ticket", "add", "--kind", "fix", "--summary", "late"), "T2")
        self.assertEqual(self.land("lease", "renew", "L1", "--owner", "docs/@2"), "L1 renewed")
        self.assertEqual(self.land("lease", "release", "L1", "--owner", "docs/@2"), "L1 released")
        self.assertEqual(self.run_at(docs, *new, "dish", "D1", "--state", "dropped", "--stopped", "r1"), "D1 dropped; T1 waiting again")
        self.assertEqual(self.land("owner", "--prefix", "docs/", "--generation", "2"), "docs/ at generation 2")
        self.assertEqual(self.land("owner", "--prefix", "docs/", "--generation", "1", ok=False),
                         "land: docs/ is at generation 2; a floor never lowers")

    def test_a_store_without_a_generation_works_until_set_thread_records_one(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        meta["thread"] = "t1"
        (self.at / "restaurant.json").write_text(json.dumps(meta))
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "legacy"), "T1")
        self.brigade("set", "--thread", "t1")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "unowned", ok=False, owner=False),
                         "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status")
        self.assertEqual(self.brigade("--owner", "t1@1", "ticket", "add", "--kind", "fix", "--summary", "owned"), "T2")


    def mode_rows(self):
        return [line.split("\t")[1:] for line in (self.at / "log.tsv").read_text().splitlines()[1:]
                if line.split("\t")[1] == "mode"]

    def test_open_mode_records_light_and_set_mode_clears_it(self):
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--mode", "light"),
                         f"opened {self.at}")
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["mode"], "light")
        self.assertIn("## Budget\n\nAny cap on parallel workers, and whether a provider's usage limit switches this restaurant "
                      "to light mode.\n", (self.at / "menu.md").read_text())
        self.brigade("set", "--thread", "c1")
        self.assertEqual(self.brigade("status"),
                         "thread c1\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nmode light\nowner c1@1")
        self.assertIn("  Perf (reports milestones, mode light): nothing on record", self.brigade("walk"))
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--mode", "full"),
                         f"exists {self.at}\nthread c1 already recorded\nmode stays light; change it with set --mode")
        self.assertEqual(json.loads(self.brigade("set", "--mode", "full"))["mode"], "full")
        self.assertEqual(self.brigade("set", "--mode", "fast", ok=False), 'brigade: --mode takes full, light, or ""')
        self.assertNotIn("mode", json.loads(self.brigade("set", "--mode", "")))
        self.assertEqual(self.brigade("status"), "thread c1\nreporting: milestones, no landing contract\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nowner c1@1")
        self.assertIn("  Perf (reports milestones): nothing on record", self.brigade("walk"))
        self.assertEqual(self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--mode", "full"),
                         f"exists {self.at}\nthread c1 already recorded\nmode stays unset; change it with set --mode")

    def test_item_mode_flags_refuse_light_and_a_missing_reason_with_exit_1(self):
        self.fired_bug_fix()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "t")
        before = (self.at / "log.tsv").read_bytes()
        refusals = [
            (("dish", "D1", "--mode", "light", "--reason", "r"),
             "brigade: an item's mode only moves to full; change the coordinator with set --mode light"),
            (("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "t", "--mode", "light", "--reason", "r"),
             "brigade: an item's mode only moves to full; change the coordinator with set --mode light"),
            (("dish", "D1", "--mode", "full"), 'brigade: --mode full needs --reason "<one line>"'),
            (("dish", "D1", "--mode", "full", "--reason", " "), 'brigade: --mode full needs --reason "<one line>"'),
            (("dish", "D1", "--reason", "r"), "brigade: --reason goes with --mode full"),
            (("dish", "D1", "--mode", "fast", "--reason", "r"), "brigade: --mode takes full, got 'fast'"),
            (("dish", "D1", "--mode", "full", "--reason", "r", "--state", "dropped"),
             "brigade: --mode full needs open work; run --state dropped without it"),
            (("dish", "D1", "--mode", "full", "--reason", "r", "--state", "queued"),
             "brigade: --mode full needs open work; run --state queued without it"),
        ]
        for args, message in refusals:
            result = subprocess.run([sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.at), *args],
                                    capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stderr.strip()), (1, message), args)
        self.assertEqual((self.at / "log.tsv").read_bytes(), before)
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] t")

    def test_dish_mode_full_twice_keeps_the_first_reason(self):
        self.fired_bug_fix()
        self.assertEqual(self.brigade("dish", "D1", "--mode", "full", "--reason", "contested: x"),
                         "D1 in-progress, mode full: contested: x")
        self.assertEqual(self.brigade("dish", "D1", "--mode", "full", "--reason", "y"),
                         "D1 in-progress, mode full: contested: x")
        self.assertEqual([row[:4] for row in self.mode_rows()], [["mode", "D1", "full", "contested: x"]])
        self.assertEqual(self.brigade("dish", "D1", "--state", "blocked", "--mode", "full", "--reason", "y"),
                         "D1 blocked, mode full: contested: x")
        self.record("abc", "pass")
        self.brigade("dish", "D1", "--state", "queued")
        self.assertEqual(self.brigade("dish", "D1", "--mode", "full", "--reason", "z", ok=False),
                         "brigade: D1 is queued; only open work moves to full mode")
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.brigade("dish", "D1", "--mode", "full", "--reason", "z", ok=False),
                         "brigade: D1 is merged; only open work moves to full mode")
        self.assertEqual(len(self.mode_rows()), 1)

    def test_dish_mode_full_with_a_stale_owner_appends_nothing(self):
        self.fired_bug_fix()
        self.brigade("set", "--thread", "c1")
        self.brigade("set", "--thread", "c2", "--replace")
        stale = "brigade: owner c1@1 is stale; this store is owned by c2@2"
        before = (self.at / "log.tsv").read_bytes()
        self.assertEqual(self.brigade("--owner", "c1@1", "dish", "D1", "--mode", "full", "--reason", "r", ok=False), stale)
        self.assertEqual((self.at / "log.tsv").read_bytes(), before)
        self.brigade("dish", "D1", "--mode", "full", "--reason", "r")
        after = (self.at / "log.tsv").read_bytes()
        self.assertEqual(self.brigade("--owner", "c1@1", "dish", "D1", "--mode", "full", "--reason", "r", ok=False), stale)
        self.assertEqual((self.at / "log.tsv").read_bytes(), after)
        self.record("abc", "pass")
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.brigade("--owner", "c1@1", "dish", "D1", "--mode", "full", "--reason", "r", ok=False), stale)

    def test_fire_mode_full_records_the_escalation_after_the_dish_row(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "lock order")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix the lock order",
                                      "--mode", "full", "--reason", "tickets name a lock"), "D1")
        rows = [line.split("\t")[1:4] for line in (self.at / "log.tsv").read_text().splitlines()[1:]]
        self.assertEqual(rows[-3:], [["dish", "D1", "in-progress"], ["mode", "D1", "full"], ["ticket", "T1", "assigned"]])
        self.assertEqual(self.mode_rows()[0][3], "tickets name a lock")

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

    def test_the_brief_pastes_the_runtime_seat_rule_unchanged(self):
        runtime = (ROOT / "t3/runtime.md").read_text()
        self.assertIn(f"  ```text\n  {self.SEAT_RULE}\n  ```", runtime)

    def test_the_runtime_still_states_the_bounded_wait(self):
        runtime = (ROOT / "t3/runtime.md").read_text()
        self.assertIn("does not end its turn while a child is open", runtime)
        self.assertIn("Decide completion by `workState` alone", runtime)
        self.assertIn("Its finished turn does not wake the launcher.", runtime)
        self.assertIn("interrupt with `t3_thread_interrupt`", runtime)

    def test_wait_rule_waits_out_an_ended_run(self):
        rule = runpy.run_path(str(SCRIPT))["WAIT_RULE"]
        for phrase in (
            "returns at once while workState is still working or waiting_for_children",
            "t3_thread_wait and timeoutMs 120000 on your own thread",
            "with runId set to its activeRunId from t3_thread_read",
            "never a shell sleep",
            "whose run is still open and whose last message holds the result is stalled after two minutes with no new activity, per step 5",
            "count ten minutes from its last activity item",
        ):
            self.assertIn(phrase, rule)
        self.assertEqual(rule, self.WAIT)

    def test_the_report_section_opens_with_the_bounded_wait(self):
        self.coordinator()
        text = self.brigade(*self.BRIEF)
        lines = text.splitlines()
        start = lines.index("REPORT:")
        write = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("- Write it to "))
        self.assertEqual(lines[start:write + 1], ["REPORT:", self.WAIT, lines[write]])

    def test_an_orchestrate_brief_carries_the_same_bounded_wait(self):
        self.coordinator(station="orchestrate", mode="full")
        text = self.brigade(*self.BRIEF)
        self.assertTrue(text.startswith("Use the poteto-mode skill and its `orchestrate` playbook.\n"), text[:80])
        self.assertEqual(text.split("REPORT:\n", 1)[1].splitlines()[0], self.WAIT)

    def test_a_first_light_feature_brief_prints_the_first_waivers(self):
        self.coordinator()
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], self.head(
            "Playbook: playbooks/feature.md", "Mode: light", "Mode source: restaurant.json", "Attempt: first",
            "Waived by mode: Arena, Interrogate, Comment Sicko"))
        report = text.split("REPORT:\n", 1)[1].splitlines()
        self.assertEqual(report[2:4], [self.REPEAT, self.CONTESTED])
        self.assertTrue(report[4].startswith("- After that file is written, call t3_thread_send"), report[4])
        self.assertEqual((self.at / "briefs/D1.md").read_text().strip(), text)
        self.assertEqual(self.mode_rows(), [])

    def test_a_brief_without_a_mode_prints_the_default(self):
        self.coordinator(mode=None)
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], self.head(
            "Playbook: playbooks/feature.md", "Mode: full", "Mode source: default", "Attempt: first"))
        self.assertIn(self.REPEAT, text)
        self.assertNotIn("Contested:", text)

    def test_one_send_back_briefs_a_fix_attempt(self):
        self.coordinator()
        self.record("a1", "send-back")
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], self.head(
            "Playbook: playbooks/feature.md", "Mode: light", "Mode source: restaurant.json", "Attempt: fix",
            "Waived by mode: How, Architect, Arena, Interrogate, Comment Sicko"))

    def test_a_replaced_worker_keeps_the_attempt_and_adds_no_send_back(self):
        self.coordinator()
        self.brigade("dish", "D1", "--thread", "w2")
        self.brigade("dish", "D1", "--thread", "w3")
        self.assertIn("Mode: light\nMode source: restaurant.json\nAttempt: first\n", self.brigade(*self.BRIEF))
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.brigade("dish", "D1", "--thread", "w4")
        self.brigade("dish", "D1", "--thread", "w5")
        self.assertIn("Mode: light\nMode source: restaurant.json\nAttempt: fix\n", self.brigade(*self.BRIEF))
        self.assertEqual(self.mode_rows(), [])

    def test_member_send_backs_do_not_move_the_item_to_full(self):
        self.coordinator()
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back", "--member", verifier="grok/grok-4.7")
        self.record("a2", "send-back", "--member", verifier="opencode/kimi-k3")
        self.assertIn("Mode: light\nMode source: restaurant.json\nAttempt: fix\n", self.brigade(*self.BRIEF))
        self.assertEqual(self.mode_rows(), [])

    def test_a_late_send_back_counts_and_leaves_the_fix_attempt_running(self):
        self.coordinator()
        self.record("a2", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "a1", "--verdict", "send-back",
                                      "--author", CLAUDE, "--verifier", CODEX, "--note", "round 1", "--late"),
                         "D1: late send-back on record; D1 stays in-progress")
        self.assertEqual(self.dish_fields("state", "pr", "sha", "thread"), ("in-progress", "", "a2", ""))
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "a1", ok=False),
                         "brigade: D1 at a1: send-back (round 1)")
        self.assertEqual(self.pass_rows()[1][3:], ["a1", "send-back", CLAUDE, CODEX, "round 1", "", ""])
        self.assertIn("Mode: full\nMode source: escalated: second send-back\nAttempt: fix\n", self.brigade(*self.BRIEF))

    def test_a_fix_brief_names_the_report_of_the_latest_send_back(self):
        self.coordinator()
        first, second, panel = (self.review_file(f"D1-review-{name}.md") for name in ("1", "2", "2-panel-2"))
        line = "- A reviewer sent an earlier attempt back. Its findings: "
        self.record("a1", "send-back", "--report", first.name)
        self.assertIn(f"{line}{first}\n", self.brigade(*self.BRIEF))
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back", "--report", second.name)
        self.record("a2", "send-back", "--member", "--report", panel.name, verifier="grok/grok-4.7")
        self.assertIn(f"{line}{second}\n", self.brigade(*self.BRIEF))
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a3", "send-back")
        self.brigade("86", "add", "--dish", "D1", "--round-budget")
        self.brigade("86", "answer", "Q1", "--answer", "known limits")
        self.assertNotIn(line, self.brigade(*self.BRIEF))
        plain = self.review_file("D1-review.md")
        self.assertIn(f"{line}{plain}\n", self.brigade(*self.BRIEF))

    def test_a_queue_bounce_briefs_a_bounce_attempt(self):
        self.coordinator(station="bug-fix")
        self.record("a1", "pass")
        self.brigade("dish", "D1", "--state", "queued")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.brigade(*self.BRIEF).split("\n\n", 1)[0], self.head(
            "Playbook: playbooks/bug-fix.md", "Mode: light", "Mode source: restaurant.json", "Attempt: bounce",
            "Waived by mode: How, Why, Architect, Comment Sicko", station="bug-fix"))

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

    def test_the_escalation_lives_in_the_log_and_a_stale_write_changes_no_row(self):
        self.coordinator()
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back")
        self.brigade(*self.BRIEF)
        (self.at / "pass.tsv").write_text("\t".join(("at", "dish", "pr", "sha", "verdict", "author", "verifier", "note")) + "\n")
        self.brigade("set", "--mode", "light")
        self.assertIn("Mode: full\nMode source: escalated: second send-back\n", self.brigade(*self.BRIEF))
        self.brigade("set", "--thread", "c2", "--replace")
        before = (self.at / "log.tsv").read_bytes()
        self.assertEqual(self.brigade("--owner", "c1@1", "dish", "D1", "--mode", "full", "--reason", "late", ok=False),
                         "brigade: owner c1@1 is stale; this store is owned by c2@2")
        self.assertEqual(self.brigade("--owner", "c1@1", *self.BRIEF, ok=False),
                         "brigade: owner c1@1 is stale; this store is owned by c2@2")
        self.assertEqual((self.at / "log.tsv").read_bytes(), before)

    def test_set_mode_leaves_a_written_brief_unchanged(self):
        self.coordinator(mode="full")
        self.brigade(*self.BRIEF)
        written = (self.at / "briefs/D1.md").read_bytes()
        self.brigade("set", "--mode", "light")
        self.assertEqual((self.at / "briefs/D1.md").read_bytes(), written)
        self.assertIn("Mode: light\n", self.brigade(*self.BRIEF))

    def test_fire_mode_full_carries_its_reason_into_every_brief(self):
        self.coordinator("bug-fix", "light", "--mode", "full", "--reason", "tickets name a lock")
        self.assertIn("Mode: full\nMode source: escalated: tickets name a lock\nAttempt: first\nGate: brigade\n",
                      self.brigade(*self.BRIEF))
        self.assertEqual(len(self.mode_rows()), 1)

    def test_a_lease_over_a_configured_path_escalates_once(self):
        self.git_project()
        (self.project / ".pstack").mkdir()
        (self.project / ".pstack/t3-roles.json").write_text(json.dumps({"escalate": ["src/lock.py"]}))
        self.coordinator()
        brief = [*self.BRIEF]
        brief[brief.index("src/a.py")] = "src/lock.py"
        self.assertIn("Mode: full\nMode source: escalated: lease covers src/lock.py\n", self.brigade(*brief))
        self.assertIn("Mode: full\nMode source: escalated: lease covers src/lock.py\n", self.brigade(*self.BRIEF))
        self.assertEqual([row[:4] for row in self.mode_rows()], [["mode", "D1", "full", "lease covers src/lock.py"]])

    def test_a_station_that_is_not_a_playbook_briefs_only_the_mode(self):
        self.coordinator(station="correct")
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], self.head("Mode: light", "Mode source: restaurant.json", station="correct"))

    def test_roles_mode_refuses_to_run_under_a_store_lock(self):
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        glob["HELD_LOCKS"].append(Path("/store/held"))
        with self.assertRaises(glob["BrigadeError"]) as caught:
            glob["roles_mode"](None)
        self.assertEqual(str(caught.exception),
                         "roles.py may run git; release /store/held's store lock before resolving the mode")

    def test_a_roles_failure_refuses_the_brief_and_writes_nothing(self):
        self.coordinator()
        config = Path(os.environ["XDG_CONFIG_HOME"]) / "pstack-t3" / "roles.json"
        config.parent.mkdir(parents=True)
        config.write_text("{")
        before = (self.at / "log.tsv").read_bytes()
        error = self.brigade(*self.BRIEF, ok=False)
        self.assertTrue(error.startswith(f"brigade: roles.py mode refused the brief: {config}: invalid JSON"), error)
        self.assertFalse((self.at / "briefs/D1.md").exists())
        self.assertEqual((self.at / "log.tsv").read_bytes(), before)

    def paused_brief(self, case):
        case.mkdir()
        return self._spawn_race("brief-pause", case, ["--store", str(self.store), "--at", str(self.at),
                                                      *_owner_words(self.at), *self.BRIEF])

    def test_two_briefs_racing_append_one_mode_row(self):
        self.coordinator()
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back")
        case = Path(self.temporary.name) / "race"
        first = self.paused_brief(case)
        _wait_for_path(case / "paused", timeout=20)
        second = self.brigade(*self.BRIEF)
        (case / "proceed").touch()
        code, out, err = self._finish_race(first)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, second)
        self.assertEqual(len(self.mode_rows()), 1)

    def test_a_mode_set_while_roles_runs_is_in_the_brief(self):
        self.coordinator()
        case = Path(self.temporary.name) / "race"
        first = self.paused_brief(case)
        _wait_for_path(case / "paused", timeout=20)
        self.brigade("set", "--mode", "full")
        (case / "proceed").touch()
        code, out, err = self._finish_race(first)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Mode: full\nMode source: restaurant.json\n", out)

    def test_a_store_that_keeps_changing_refuses_the_brief_after_three_resolutions(self):
        self.coordinator()
        case = Path(self.temporary.name) / "race"
        case.mkdir()
        churning = self._spawn_race("brief-churn", case, ["--store", str(self.store), "--at", str(self.at),
                                                           *_owner_words(self.at), *self.BRIEF])
        self.assertEqual(self._finish_race(churning), (1, "", "brigade: D1 changed while resolving its mode; run brief again"))
        self.assertFalse((self.at / "briefs/D1.md").exists())


    def cells(self, ident):
        row = self.table_row(self.at, "rail.tsv", ident)
        return row["priority"], row["paths"], row["decision"]

    def old_rail(self, stamp="2026-10-01T00:00:00+00:00"):
        (self.at / "rail.tsv").write_text(
            "id\tat\tstate\tsource\tref\tdish\tsummary\n"
            f"T1\t{stamp}\twaiting\tuser\t\t\tFix login\n"
            f"T2\t{stamp}\twaiting\treport\tperf/reports/D1.md#1\t\tTidy the guide\n"
            f"T3\t{stamp}\twaiting\tupstream\tabc123\t\tSync upstream\n")

    def test_ticket_list_prints_the_priority_that_ticket_add_or_ticket_set_recorded(self):
        self.open()
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--priority", "urgent"), "T1")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "b", "--priority", "low"), "T2")
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "c", "--priority", "normal"), "T3")
        self.assertEqual(self.brigade("ticket", "list"),
                         "T1 waiting urgent fix [user] a\nT2 waiting low fix [user] b\nT3 waiting normal fix [user] c")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--priority", "low"), "T1 priority low")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--priority", "urgent"), "T2 priority urgent")
        self.assertEqual(self.brigade("ticket", "list"),
                         "T1 waiting low fix [user] a\nT2 waiting urgent fix [user] b\nT3 waiting normal fix [user] c")
        self.assertEqual([self.cells(ident)[0] for ident in ("T1", "T2", "T3")], ["low", "urgent", "normal"])

    def test_ticket_add_and_ticket_set_refuse_a_priority_outside_urgent_normal_and_low(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a")
        choices = ("urgent", "normal", "low")
        self.assert_refused_choice(
            self.brigade("ticket", "add", "--kind", "fix", "--summary", "b", "--priority", "high", ok=False), "--priority", "high", choices)
        self.assert_refused_choice(
            self.brigade("ticket", "set", "T1", "--priority", "high", ok=False), "--priority", "high", choices)
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] a")

    def test_a_ticket_with_an_empty_priority_cell_reads_urgent_from_upstream_low_from_report_and_normal_from_other_sources(self):
        self.open()
        self.brigade("set", "--intake", "upstream,report,github")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--source", "upstream")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "b", "--source", "report")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "c", "--source", "github")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "d")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "e", "--source", "upstream", "--request", "A1")
        self.assertEqual(self.brigade("ticket", "list").splitlines(), [
            "T1 waiting urgent fix [upstream] a",
            "T2 waiting low fix [report] b",
            "T3 waiting normal fix [github] c",
            "T4 waiting normal fix [user] d",
            "T5 waiting urgent fix [upstream (request A1)] e",
        ])
        self.assertEqual([self.cells(f"T{number}") for number in range(1, 6)], [("", "", "")] * 5)

    def test_a_recorded_priority_wins_over_the_default_of_the_source(self):
        self.open()
        self.brigade("set", "--intake", "upstream")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--source", "upstream", "--priority", "low")
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting low fix [upstream] a")

    def test_ticket_list_reads_rail_rows_written_before_the_priority_paths_and_decision_cells(self):
        self.open()
        self.old_rail()
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.brigade("ticket", "list").splitlines(), [
            "T1 waiting normal [user] Fix login",
            "T2 waiting low [report] Tidy the guide perf/reports/D1.md#1",
            "T3 waiting urgent [upstream] Sync upstream abc123",
        ])
        self.assertEqual((self.at / "rail.tsv").read_bytes(), before)
        self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "New", "--priority", "urgent", "--paths", "a.txt"), "T4")
        self.assertEqual((self.at / "rail.tsv").read_bytes()[:len(before)], before)
        self.assertEqual(self.brigade("ticket", "list").splitlines()[-1], "T4 waiting urgent fix [user] New")
        self.assertEqual((self.at / "rail.tsv").read_text().splitlines()[-1].split("\t")[5:],
                         ["", "New", "urgent", "a.txt", "", "fix", ""])

    def test_a_rail_row_with_six_fields_or_thirteen_is_malformed(self):
        self.open()
        stamp = "2026-10-01T00:00:00+00:00"
        for fields in (["T1", stamp, "waiting", "user", "", ""], ["T1", stamp, "waiting", "user", "", "", "s", *[""] * 6]):
            (self.at / "rail.tsv").write_text("id\n" + "\t".join(fields) + "\n")
            self.assertEqual(self.brigade("ticket", "list", ok=False),
                             "brigade: bridge-kit/perf/rail.tsv line 2 is malformed; fix or remove it")

    def test_a_rail_row_with_seven_ten_eleven_or_twelve_fields_is_read(self):
        self.open()
        stamp = "2026-10-01T00:00:00+00:00"
        for extra in ([], ["low", "a.txt", ""], ["low", "a.txt", "", ""], ["low", "a.txt", "", "", ""]):
            (self.at / "rail.tsv").write_text("id\n" + "\t".join(["T1", stamp, "waiting", "user", "", "", "s", *extra]) + "\n")
            self.assertEqual(self.brigade("ticket", "list"), f"T1 waiting {'low' if extra else 'normal'} [user] s", extra)

    OLD_DISHES = "id\tat\tstate\tstation\ttickets\ttask\tthread\tbranch\tpr\tsha\tsummary\ttimebox\tlease\tpaths\treported\n"

    def old_item(self, stamp="2026-10-01T00:00:00+00:00"):
        """One assigned ticket and its item in review, each in a row of the width the tool wrote before the kind and goal cells."""
        (self.at / "rail.tsv").write_text(
            "id\tat\tstate\tsource\tref\tdish\tsummary\tpriority\tpaths\tdecision\n"
            f"T1\t{stamp}\tassigned\tuser\t\tD1\tFix login\t\t\t\n")
        (self.at / "dishes.tsv").write_text(
            self.OLD_DISHES + f"D1\t{stamp}\tin-review\tbug-fix\tT1\t\tthread-1\t\t\tabc\tFix login\t60\t\t\t\n")

    def test_a_15_cell_dishes_row_and_a_10_cell_rail_row_are_read_and_a_read_leaves_both_tables_byte_identical(self):
        self.open()
        self.old_item()
        before = self.tables()
        self.assertEqual(self.brigade("ticket", "list"), "T1 assigned normal [user] Fix login")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: in review")
        self.assertEqual(self.brigade("status").splitlines()[1], "reporting: milestones, no landing contract, in review: 1")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False), "brigade: D1 has no review verdict for abc")
        self.assertEqual(self.tables(), before)

    def test_a_rewrite_of_an_old_table_keeps_each_cell_and_adds_an_empty_kind_and_goal_cell_to_each_row(self):
        self.open()
        self.old_item()
        rail, dishes = ((self.at / name).read_text().splitlines()[1].split("\t") for name in ("rail.tsv", "dishes.tsv"))
        self.assertEqual(self.brigade("dish", "D1", "--timebox", "90"), "D1 in-review")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--priority", "low"), "T1 priority low")
        dishes[11], rail[7] = "90", "low"
        self.assertEqual((self.at / "dishes.tsv").read_text().splitlines()[1].split("\t"), [*dishes, "", ""])
        self.assertEqual((self.at / "rail.tsv").read_text().splitlines()[1].split("\t"), [*rail, "", ""])
        self.assertEqual((self.at / "dishes.tsv").read_text().splitlines()[0].split("\t")[-3:], ["reported", "kind", "goal"])
        self.assertEqual((self.at / "rail.tsv").read_text().splitlines()[0].split("\t")[-3:], ["decision", "kind", "goal"])

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

    def test_open_writes_goals_tsv_with_its_header_and_no_row_into_a_new_store_and_into_the_admins_store(self):
        self.open()
        self.open_admin()
        for directory in (self.at, self.store / "bridge-kit" / ".admin"):
            self.assertEqual((directory / "goals.tsv").read_text(), "id\tat\tstate\tname\tpurpose\tdone\tshare\tclosed\n")

    def test_a_store_with_no_goals_table_runs_status_watch_startable_ticket_list_and_close_and_gains_no_table(self):
        self.fired_bug_fix()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "next")
        (self.at / "goals.tsv").unlink()
        files = sorted(self.store_files())
        self.assertEqual(self.brigade("status").splitlines()[0], "thread not recorded")
        self.assertEqual(self.brigade("watch"), "workers: 1 of 2 running, 1 idle, auto-start: normal\nD1: running 0m of 60m (no worker recorded)")
        self.assertEqual(self.brigade("startable").splitlines()[:2],
                         ["workers: 1 of 2 running, 1 idle, auto-start: normal", "waiting tickets: 1, unknown: 1"])
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] next")
        code, out, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[0], "# Perf report")
        self.assertEqual(sorted(self.store_files()), files)
        self.assertFalse((self.at / "goals.tsv").exists())

    def test_a_priority_paths_or_decision_change_adds_no_log_row_and_keeps_a_blocked_ticket_blocked(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "one")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "two", ok=False),
                         "brigade: nothing fired: 1 of 1 workers running")
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting normal fix [user] two blocked: workers")
        before = ((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes())
        self.assertEqual(self.brigade("ticket", "set", "T2", "--priority", "urgent"), "T2 priority urgent")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--paths", "a.txt"), "T2 paths a.txt")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--decision"), "T2 decision")
        self.assertEqual(((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes()), before)
        self.assertEqual(self.cells("T2"), ("urgent", "a.txt", "yes"))
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting"), "T2 waiting urgent fix [user] two blocked: workers")

    def test_ticket_set_prints_each_given_change_in_one_line_and_refuses_a_call_with_none(self):
        self.open()
        for summary in ("a", "b", "c"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        self.assertEqual(self.brigade("ticket", "set", "T1", "--state", "dropped"), "T1 dropped")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--state", "dropped", "--priority", "normal"),
                         "T2 dropped; priority normal")
        self.assertEqual(self.brigade("ticket", "set", "T3", "--decision", "--paths", "b,a", "--priority", "low",
                                      "--state", "waiting"), "T3 waiting; priority low; paths a,b; decision")
        self.assertEqual(self.cells("T3"), ("low", "a,b", "yes"))
        self.assertEqual(self.brigade("ticket", "set", "T3", "--paths", "", "--no-decision"), "T3 no paths; no decision")
        self.assertEqual(self.cells("T3"), ("low", "", ""))
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "set", "T3", ok=False),
                         "brigade: ticket set needs --state, --priority, --paths, --decision, --no-decision, --kind, --goal, or --no-goal")
        self.assertEqual(self.brigade("ticket", "set", "T9", ok=False), "brigade: no T9 in rail.tsv")
        self.assertEqual(self.store_files(), before)

    def test_ticket_add_and_ticket_set_record_paths_sorted_normalized_and_without_repeats(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--paths", "src/b.py, docs/,src/b.py,./a.txt")
        self.assertEqual(self.cells("T1"), ("", "a.txt,docs,src/b.py", ""))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--paths", "x\\y,/abs/z,new/file.py"),
                         "T1 paths abs/z,new/file.py,x/y")
        self.assertEqual(self.cells("T1"), ("", "abs/z,new/file.py,x/y", ""))

    def test_ticket_set_with_empty_paths_clears_the_recorded_paths(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--paths", "a.txt")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--paths", ""), "T1 no paths")
        self.assertEqual(self.cells("T1"), ("", "", ""))

    def test_ticket_add_and_ticket_set_refuse_a_path_that_is_empty_or_the_repository_or_outside_it(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--paths", "a.txt")
        before = self.store_files()
        for value, part in (("..", ".."), ("src,../x", "../x"), ("a,,b", ""), (".", "."), ("/", "/"), ("a/../..", "a/../.."),
                            (" ", " "), ("a, ./", " ./")):
            refusal = f"brigade: --paths takes files or directories inside the repository, got {part!r}"
            self.assertEqual(self.brigade("ticket", "add", "--kind", "fix", "--summary", "b", "--paths", value, ok=False), refusal, value)
            self.assertEqual(self.brigade("ticket", "set", "T1", "--paths", value, ok=False), refusal, value)
        self.assertEqual(self.store_files(), before)

    def test_ticket_add_and_ticket_set_record_and_clear_the_decision_cell(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "a", "--decision")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "b")
        self.assertEqual([self.cells("T1"), self.cells("T2")], [("", "", "yes"), ("", "", "")])
        self.assertEqual(self.brigade("ticket", "set", "T1", "--no-decision"), "T1 no decision")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--decision"), "T2 decision")
        self.assertEqual([self.cells("T1"), self.cells("T2")], [("", "", ""), ("", "", "yes")])

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

    def test_set_autofire_refuses_a_value_outside_off_urgent_normal_and_low(self):
        self.open()
        before = (self.at / "restaurant.json").read_bytes()
        self.assert_refused_choice(self.brigade("set", "--autofire", "high", ok=False), "--autofire", "high",
                                   ("off", "urgent", "normal", "low"))
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_a_missing_or_empty_autofire_key_reads_as_normal(self):
        self.open()
        meta = json.loads((self.at / "restaurant.json").read_text())
        self.assertNotIn("autofire", meta)
        line = "workers: 0 of 2 running, 2 idle, auto-start: normal"
        self.assertEqual(self.brigade("status").splitlines()[2], line)
        (self.at / "restaurant.json").write_text(json.dumps(meta | {"autofire": ""}, indent=2) + "\n")
        self.assertEqual(self.brigade("status").splitlines()[2], line)

    def test_the_executive_admin_status_has_no_workers_line(self):
        self.open_admin()
        self.assertEqual(self.admin("status"), "thread not recorded\nreporting: milestones, no landing contract")

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

    def test_ticket_paths_records_quoted_paths_on_waiting_tickets_that_record_none_and_logs_nothing(self):
        self.unrecorded_tickets()
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "paths"),
                         "T1 paths docs/guide.md,tests/test_install.py\nT2 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": "docs/guide.md,tests/test_install.py", "T2": "", "T3": "t3/runtime.md", "T4": ""})
        after = self.store_files()
        self.assertEqual(sorted(name for name in after if after[name] != before[name]), ["rail.tsv"])

    def test_a_second_ticket_paths_run_changes_no_store_file(self):
        self.unrecorded_tickets()
        self.brigade("ticket", "paths")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "paths"), "T2 quotes no tracked path")
        self.assertEqual(self.store_files(), before)

    def test_ticket_paths_dry_run_prints_what_it_would_record_and_changes_no_store_file(self):
        self.unrecorded_tickets()
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "paths", "--dry-run", owner=False),
                         "T1 would record docs/guide.md,tests/test_install.py\nT2 quotes no tracked path")
        self.assertEqual(self.store_files(), before)

    def test_ticket_paths_says_so_when_every_waiting_ticket_records_paths(self):
        self.tracked_project(*TRACKED)
        self.open()
        self.assertEqual(self.brigade("ticket", "paths"), "every waiting ticket records paths")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Reword `README.md`", "--paths", "t3/runtime.md")
        self.assertEqual(self.brigade("ticket", "paths"), "every waiting ticket records paths")

    def test_ticket_paths_refuses_a_project_that_is_not_a_git_repository_and_changes_no_store_file(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `docs/guide.md`")
        before = self.store_files()
        for flags in ((), ("--dry-run",)):
            self.assertEqual(self.brigade("ticket", "paths", *flags, ok=False),
                             f"brigade: cannot list tracked files in {self.project}")
        self.assertEqual(self.store_files(), before)

    def test_ticket_paths_records_the_one_tracked_file_that_has_a_quoted_file_name_with_no_directory(self):
        self.tracked_project("src/app/tool.py", "docs/guide.md")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths src/app/tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "src/app/tool.py"})

    def test_ticket_paths_records_nothing_for_a_quoted_file_name_that_no_tracked_file_has(self):
        self.tracked_project("src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `nope.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

    def test_ticket_paths_records_nothing_for_a_quoted_file_name_that_two_tracked_files_outside_skills_have(self):
        self.tracked_project("src/app/tool.py", "lib/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

    def test_ticket_paths_records_the_t3_file_for_a_quoted_file_name_that_a_file_at_the_same_path_under_skills_also_has(self):
        self.tracked_project("t3/added/x/scripts/tool.py", "skills/x/scripts/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths t3/added/x/scripts/tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "t3/added/x/scripts/tool.py"})

    def test_ticket_paths_records_the_t3_file_for_a_quoted_file_name_that_a_file_at_another_path_under_skills_also_has(self):
        self.tracked_project("t3/scripts/roles.py", "skills/pstack-runtime/scripts/roles.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `roles.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths t3/scripts/roles.py")
        self.assertEqual(self.paths_cells(), {"T1": "t3/scripts/roles.py"})

    def test_ticket_paths_records_nothing_for_a_quoted_file_name_that_files_under_skills_under_t3_and_elsewhere_have(self):
        self.tracked_project("skills/x/tool.py", "t3/added/x/tool.py", "vendor/x/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

    def test_ticket_paths_records_a_file_under_skills_for_a_quoted_file_name_that_no_other_tracked_file_has(self):
        self.tracked_project("skills/x/scripts/tool.py", "t3/added/x/scripts/other.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths skills/x/scripts/tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "skills/x/scripts/tool.py"})

    def test_ticket_paths_records_the_one_tracked_file_that_has_a_quoted_file_name_with_a_line_suffix(self):
        self.tracked_project("src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py:12` and `tool.py:30-34`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths src/app/tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "src/app/tool.py"})

    def test_ticket_paths_records_a_quoted_file_name_that_is_a_tracked_file_at_the_root_when_another_directory_tracks_that_name(self):
        self.tracked_project("tool.py", "src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "tool.py"})

    def test_ticket_paths_dry_run_prints_the_one_tracked_file_that_has_a_quoted_file_name_and_changes_no_store_file(self):
        self.tracked_project("src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "paths", "--dry-run", owner=False), "T1 would record src/app/tool.py")
        self.assertEqual(self.store_files(), before)

    def test_from_report_records_the_tracked_file_for_each_quoted_file_name_with_no_directory_that_resolves_to_one_and_none_for_names_with_none_or_several(self):
        self.tracked_project("t3/added/x/scripts/tool.py", "skills/x/scripts/tool.py", "src/app/one.py", "lib/two.py",
                             "src/two.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n"
                           "- Fix `tool.py:7` and `one.py`.\n"
                           "- Fix `two.py` and `nope.py`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "src/app/one.py,t3/added/x/scripts/tool.py", "T3": ""})

    def test_ticket_paths_records_nothing_for_a_file_name_only_one_nested_file_has_quoted_with_a_dot_slash_before_it_a_slash_after_it_or_spaces_around_it(self):
        self.tracked_project("src/app/tool.py", "tool.py:12")
        self.open()
        for quote in ("./tool.py", "tool.py/", "./tool.py:12", " tool.py "):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", f"Fix `{quote}`")
        self.assertEqual(self.brigade("ticket", "paths"), "\n".join(f"T{number} quotes no tracked path" for number in (1, 2, 3, 4)))
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "", "T3": "", "T4": ""})

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

    def test_ticket_paths_records_nothing_for_a_quote_that_is_a_tracked_file_named_as_one_nested_file_with_a_line_number_added(self):
        self.tracked_project("tool.py:12", "src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py:12`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

    def test_from_report_records_nothing_for_a_quote_that_is_a_tracked_file_named_as_one_nested_file_with_a_line_number_added(self):
        self.tracked_project("tool.py:12", "src/app/tool.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n- Fix `tool.py:12`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": ""})

    def test_ticket_paths_records_nothing_for_a_quote_that_is_a_tracked_directory_named_as_one_nested_file_with_a_line_number_added(self):
        self.tracked_project("tool.py:12/notes.md", "src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py:12`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

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

    def test_startable_is_refused_in_the_executive_admin_store(self):
        self.open_admin()
        self.assertEqual(self.admin("startable", ok=False), "brigade: the executive admin routes work and never runs it")

    def test_from_report_prints_a_list_item_with_no_text_as_not_filed_and_gives_it_no_number(self):
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Fix a.\n- \n*   \n1. \n- Fix b.\n").splitlines(), [
            "T2 added from perf/reports/D1.md#1: Fix a.",
            "T3 added from perf/reports/D1.md#2: Fix b.",
            "not filed, list item with no text",
            "not filed, list item with no text",
            "not filed, list item with no text",
        ])
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting").splitlines(), [
            "T2 waiting low polish [report] Fix a. perf/reports/D1.md#1",
            "T3 waiting low polish [report] Fix b. perf/reports/D1.md#2",
        ])

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

    def test_a_pass_with_edits_row_moves_the_item_to_sent_back_and_names_the_report(self):
        self.fired_bug_fix()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.assertEqual(self.record_edits("abc", "--edits", "reports/D1-review-1.md"), "D1 sent-back")
        self.assertEqual(self.dish_fields("state", "sha"), ("sent-back", "abc"))
        self.assertEqual([row[3:5] + row[8:] for row in self.pass_rows()], [["abc", "pass-with-edits", "D1-review-1.md", ""]])

    def test_pass_check_fails_at_the_sha_of_a_pass_with_edits_row(self):
        self.fired_bug_fix()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.record_edits("abc", "--edits", "D1-review-1.md", "--note", "two wording edits")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "abc", ok=False),
                         "brigade: D1 at abc: pass-with-edits (two wording edits)")
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued", ok=False),
                         "brigade: only reviewed work lands: D1 at abc: pass-with-edits (two wording edits)")

    def test_pass_with_edits_rows_do_not_count_toward_the_round_budget(self):
        self.started()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.sent_back(2)
        self.brigade("dish", "D1", "--state", "in-progress")
        for sha in ("b1", "b2"):
            self.assertEqual(self.record_edits(sha, "--edits", "D1-review-1.md"), "D1 sent-back")
            self.assertEqual(self.brigade("dish", "D1", "--state", "in-progress"), "D1 in-progress")
        self.assertEqual(self.brigade(*self.ROUND_BUDGET, ok=False),
                         "brigade: D1 has 2 send-backs since its last pass or round decision; the decision opens at 3")
        self.assertEqual(self.send_back("a3"), "D1 sent-back; 3 send-backs, decision pending")

    def test_close_prints_no_unrecorded_review_warning_for_the_report_a_pass_with_edits_row_names(self):
        self.fired_bug_fix()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.assertEqual(self.close_run("--dry-run")[::2], (0, self.no_row("D1-review-1.md")))
        self.record_edits("abc", "--edits", "D1-review-1.md")
        self.assertEqual(self.close_run("--dry-run")[::2], (0, ""))

    def test_pass_record_refuses_pass_with_edits_without_edits_and_edits_with_another_verdict_or_a_report(self):
        self.fired_bug_fix()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"))
        self.assertEqual(self.record_edits("abc", ok=False), "brigade: --verdict pass-with-edits needs --edits <review report>")
        self.assertEqual(self.record_edits("abc", "--report", "D1-review-1.md", ok=False),
                         "brigade: --verdict pass-with-edits needs --edits <review report>")
        for verdict in ("pass", "send-back", "blocked"):
            self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", verdict, "--author", CODEX,
                                          "--verifier", CLAUDE, "--edits", "D1-review-1.md", ok=False),
                             "brigade: --edits goes only with --verdict pass-with-edits", verdict)
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-1.md", "--report", "D1-review-1.md", ok=False),
                         "brigade: --edits names the round's report; drop --report")
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-9.md", ok=False),
                         "brigade: reports/D1-review-9.md does not exist; write the review report first")
        self.assertEqual(self.record_edits("abc", "--edits", "D1.md", ok=False),
                         "brigade: D1.md is not a review report of D1; name a file like reports/D1-review-1.md")
        self.assertEqual(self.pass_rows(), [])
        self.assertEqual(self.dish_fields("state"), ("in-progress",))

    def test_pass_record_refuses_a_pass_with_edits_report_whose_edits_do_not_parse(self):
        self.fired_bug_fix()
        self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n## Blockers\n\nNone.\n\n## Edits verified\n\nYes.\n")
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-1.md", ok=False),
                         "brigade: reports/D1-review-1.md holds no edit under a ## Edits heading")
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"),
                          "### Edit 2\n\nFile: `src/greeting.txt`\n\nOld text:\n\n```\nwrold\n```\n")
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-1.md", ok=False), self.PARTS_REFUSAL.format(2))
        self.assertEqual(self.pass_rows(), [])
        self.assertEqual(self.dish_fields("state"), ("in-progress",))

    def test_a_fix_brief_points_at_the_report_of_a_pass_with_edits_round(self):
        self.briefable()
        self.review_file("D1-review-1.md", "findings\n")
        self.send_back("a1", "--report", "D1-review-1.md")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"), name="D1-review-2.md")
        self.record_edits("a2", "--edits", "D1-review-2.md")
        self.brigade("dish", "D1", "--state", "in-progress")
        text = self.brigade("brief", "D1", *self.FLAGS, "--contract", "none")
        self.assertIn(f"\n- A reviewer sent an earlier attempt back. Its findings: {self.at}/reports/D1-review-2.md\n", text)
        self.assertNotIn("D1-review-1.md", text)

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

    def test_set_stores_the_build_and_check_commands_and_an_empty_value_removes_one(self):
        self.open()
        meta = json.loads(self.brigade("set", "--build-command", " make  gen ", "--check-command", "none"))
        self.assertEqual((meta["buildCommand"], meta["checkCommand"]), ("make  gen", "none"))
        meta = json.loads(self.brigade("set", "--build-command", ""))
        self.assertNotIn("buildCommand", meta)
        self.assertEqual(meta["checkCommand"], "none")
        refusal = self.brigade("set", "--check-command", "make 'gen", ok=False)
        self.assertTrue(refusal.startswith("brigade: \"make 'gen\" is not a command line: "), refusal)
        self.assertEqual(json.loads((self.at / "restaurant.json").read_text())["checkCommand"], "none")

    def test_set_refuses_a_command_that_is_not_a_command_line_once_the_whitespace_at_its_two_ends_is_removed(self):
        self.open()
        refusal = self.brigade("set", "--build-command", "make\\ ", ok=False)
        self.assertTrue(refusal.startswith("brigade: 'make\\\\' is not a command line: "), refusal)
        self.assertNotIn("buildCommand", json.loads((self.at / "restaurant.json").read_text()))

    def test_edits_verify_passes_a_head_that_is_the_edits_and_the_rebuilt_output_and_records_the_pair_once(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.edits_rows(), [])
        clean = (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", "")
        self.assertEqual(self.verify(reviewed, head), clean)
        self.assertEqual(self.edits_rows(), [["D1", reviewed, head, "D1-review-1.md"]])
        self.assertEqual(self.verify(reviewed, head), clean)
        self.assertEqual(self.edits_rows(), [["D1", reviewed, head, "D1-review-1.md"]])

    def test_edits_verify_ignores_an_edit_under_a_notes_edits_heading(self):
        reviewed = self.edits_store()
        self.two_edits(after="## Edits verified\n\nApplied in a scratch copy.\n\n## Notes edits\n\n"
                             + self.edit_block("1", "/srv/notes/review.md", "helo", "goodbye"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    def test_edits_verify_parses_an_edits_section_that_ends_the_report(self):
        reviewed = self.edits_store()
        self.two_edits(after="")
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    def test_edits_verify_fails_a_head_with_one_more_changed_line_in_a_source_file(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello world\nsecond line, changed\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, "FAIL out/greeting.txt: differs from what the build writes\n"
                             f"FAIL src/greeting.txt: differs from {reviewed} with the edits applied",
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_a_head_that_applied_an_edit_with_one_character_different(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello World\nsecond line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"built out/greeting.txt\nFAIL src/greeting.txt: differs from {reviewed} with the edits applied",
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_an_old_text_that_occurs_twice_at_the_reviewed_commit(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"),
                          self.edit_block("2", "src/greeting.txt", "n", "N"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello wrold\nsecoNd liNe\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL src/greeting.txt: old text of Edit 2 occurs 2 times at {reviewed}",
                          self.not_verified(reviewed, head)))

    def test_edits_verify_fails_two_edits_whose_old_texts_overlap_and_an_edit_to_a_path_with_no_file(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo wr", "hello wr"),
                          self.edit_block("2", "src/greeting.txt", "wrold", "world"),
                          self.edit_block("3", "src/absent.txt", "helo", "hello"),
                          self.edit_block("4", "src/greeting.txt", "second", "third"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello world\nthird line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, "FAIL src/greeting.txt: Edit 1 overlaps another edit\n"
                             "FAIL src/greeting.txt: Edit 2 overlaps another edit\n"
                             f"FAIL src/absent.txt: no regular file at {reviewed}",
                          self.not_verified(reviewed, head)))

    def test_edits_verify_fails_an_edit_that_names_a_symbolic_link_at_the_reviewed_commit(self):
        self.edits_store()
        (self.project / "src/link.txt").symlink_to("greeting.txt")
        reviewed = self.commit_files({})
        self.edits_report(self.edit_block("1", "src/link.txt", "helo", "hello"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL src/link.txt: no regular file at {reviewed}", self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_reads_the_edits_section_and_not_an_edits_line_in_an_earlier_fence(self):
        reviewed = self.edits_store()
        quoted = "## Edits\n\n" + self.edit_block("1", "tools/check.py", "import", "export")
        self.review_file("D1-review-1.md", f"Verdict: pass with edits\n\n## Blockers\n\n````\n{quoted}````\n\n## Edits\n\n"
                         + self.edit_block("1", "src/greeting.txt", "helo", "hello") + "\n"
                         + self.edit_block("2", "src/greeting.txt", "wrold", "world"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

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

    def test_edits_verify_fails_a_head_that_applied_an_edit_quoted_in_a_tilde_fence_and_not_the_edit_of_the_edits_section(self):
        self.assert_the_quoted_edit_is_not_read("~~~text", "~~~")

    def test_edits_verify_fails_a_head_that_applied_an_edit_quoted_in_a_backtick_fence_with_two_info_words_and_not_the_edit_of_the_edits_section(self):
        self.assert_the_quoted_edit_is_not_read("````text title", "````")

    def test_edits_verify_and_pass_record_refuse_a_report_with_a_fence_that_starts_after_three_spaces(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "wrold", "world"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        self.quoting_report("   ````", "   ````")
        head = self.head_commit(self.QUOTED_APPLIED)
        refusal = "brigade: reports/D1-review-1.md: line 5 " + self.NESTED_FENCE
        self.assertEqual(self.verify(reviewed, head), (1, "", refusal))
        self.assertEqual(self.edits_rows(), [])
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.record_edits(head, "--edits", "D1-review-1.md", ok=False), refusal)
        self.assertEqual(len(self.pass_rows()), 1)

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

    def test_pass_record_refuses_a_report_with_a_heading_that_is_not_number_signs_one_space_and_its_text_from_the_first_column(self):
        self.fired_bug_fix()
        for heading in (" ## Edits", "## Edits ", "## Edits\t", "## Edits ##", "##  Edits", "##\tEdits", "> ## Edits", "- ## Edits",
                        "1. ## Edits", "    ## Edits", "\t## Edits", "##", "> - ### Edit 2", "### Edit 2 "):
            with self.subTest(heading=heading):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{heading}\n\nNotes.\n\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md: line 3 starts like a heading and is not one to six #, one space, "
                                 "and the text, from the first column with nothing after the text")

    def test_pass_record_refuses_a_report_with_three_backticks_or_tildes_after_indentation_block_quote_marks_or_list_marks(self):
        self.fired_bug_fix()
        for opening in (" ```", "   ~~~~", "    ```", "\t~~~", "> ```", ">```", "- ```text", "1. ~~~", "  + ```", "> - ```", "2)\t~~~"):
            with self.subTest(opening=opening):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{opening}\nquoted\n\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md: line 3 " + self.NESTED_FENCE)

    def test_pass_record_refuses_a_report_with_a_fence_that_no_later_line_closes(self):
        self.fired_bug_fix()
        for opening, last in (("````text", "```"), ("~~~", "```"), ("```", "``"), ("```", "    ```"), ("~~~~", "~~~~ ~")):
            with self.subTest(opening=opening, last=last):
                body = (f"Verdict: pass with edits\n\n{self.section()}\n{opening}\nThe sentence then reads so.\n{last}\n\n"
                        "### Edit 2\n\nFile: `src/greeting.txt`\n\nOld text:\n")
                self.assertEqual(self.refused_edits(body),
                                 "brigade: reports/D1-review-1.md: line 21 opens a fence that no later line closes")

    def test_pass_record_refuses_a_report_with_a_line_of_only_equals_signs_or_only_hyphens_after_its_nesting_and_below_a_line_that_is_not_empty_and_not_only_spaces_and_tabs(self):
        self.fired_bug_fix()
        for above, underline in (("Edits", "---"), ("Edits", "====="), ("Edits", " -"), ("Edits", "=  "), ("> Edits", "> ---"),
                                 ("> Edits", ">="), ("- Edits", "  ==="), ("+ Edits", "\t-"), ("1. Edits", "   --- \t"),
                                 ("> - Edits", "  >   ="), ("Edits", "- -"), ("\u00a0", "---"), ("\x0c", "==="),
                                 (" \u2003", "-"), ("\x1f\t", "=")):
            with self.subTest(above=above, underline=underline):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{above}\n{underline}\n\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md: line 4 is only = or only - once its indentation, block quote "
                                 "marks, list marks, and trailing spaces and tabs are removed, and the line above it is not empty "
                                 "and not only spaces and tabs")

    def test_pass_record_refuses_a_report_with_a_line_that_starts_with_an_angle_bracket_and_then_an_ascii_letter_a_slash_an_exclamation_mark_or_a_question_mark(self):
        self.fired_bug_fix()
        for line in ("<details>", "   <!--", "</summary>", "<?php", "<item>: edits verified"):
            with self.subTest(line=line):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{line}\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md: line 3 starts, after at most three spaces, with < and then an "
                                 "ASCII letter, /, !, or ?")

    def test_pass_record_refuses_a_report_with_two_headings_whose_text_is_edits(self):
        self.fired_bug_fix()
        for other in ("## Edits", "# Edits", "#### Edits"):
            with self.subTest(other=other):
                quoted = self.edit_block("8", "src/greeting.txt", "helo", "hello")
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{other}\n\n{quoted}\n{self.section()}"),
                                 "brigade: reports/D1-review-1.md holds 2 headings whose text is Edits")

    def test_pass_record_refuses_a_heading_in_the_edits_section_that_is_not_an_edit_heading_and_has_three_or_more_number_signs(self):
        self.fired_bug_fix()
        for heading in ("#### Edit 2", "### Edit", "### Edit  2", "### Notes on the edits", "###### Edit 2"):
            with self.subTest(heading=heading):
                body = (f"Verdict: pass with edits\n\n{self.section()}\n{heading}\n\n"
                        + self.edit_block("2", "src/greeting.txt", "helo", "hello").split("\n", 1)[1])
                self.assertEqual(self.refused_edits(body),
                                 f"brigade: reports/D1-review-1.md: line {body.split(chr(10)).index(heading) + 1} is a heading in the "
                                 "## Edits section that is neither ### Edit <n> nor a heading of one or two #")

    def test_edits_verify_does_not_read_an_edit_below_a_heading_of_one_number_sign_that_follows_the_edits_section(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"),
                          after="# Appendix\n\n" + self.edit_block("2", "src/greeting.txt", "wrold", "world"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.QUOTED_APPLIED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    def test_pass_record_refuses_a_fenced_block_in_the_edits_section_above_its_first_edit_heading(self):
        self.fired_bug_fix()
        unheaded = self.edit_block("1", "src/greeting.txt", "helo", "hello").split("\n", 2)[2]
        self.assertEqual(self.refused_edits("Verdict: pass with edits\n\n## Edits\n\n" + unheaded + "\n"
                                            + self.edit_block("2", "src/greeting.txt", "wrold", "world")),
                         "brigade: reports/D1-review-1.md: line 9 starts a fenced block in the ## Edits section above its first "
                         "### Edit heading")

    def test_pass_record_refuses_an_edit_with_a_second_file_line_old_text_line_new_text_line_or_a_third_fenced_block(self):
        self.fired_bug_fix()
        unheaded = self.edit_block("2", "src/greeting.txt", "helo", "hello").split("\n", 2)[2]
        for more in ("File: `src/other.txt`\n", "Old text:\n", "New text:\n", "```\nThe sentence then reads so.\n```\n", unheaded):
            with self.subTest(more=more):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{self.section()}\n{more}"),
                                 self.PARTS_REFUSAL.format(1))

    def test_pass_record_refuses_an_edit_with_a_file_old_text_or_new_text_line_after_whitespace_a_block_quote_mark_or_a_list_mark_or_a_label_line_that_ends_in_whitespace(self):
        self.fired_bug_fix()
        for more in ("  File: `src/other.txt`", "1. File: `src/other.txt`, line 2", "> Old text:", "- New text:", "\tOld text:",
                     "Old text: ", "New text:\t", "   > - New text:  ", "\u00a0File: `src/other.txt`", "\u00a0Old text:",
                     "\x0cNew text:", "Old text:\u00a0", "> \u2003New text:"):
            with self.subTest(more=more):
                self.assertEqual(self.refused_edits(f"Verdict: pass with edits\n\n{self.section()}\n{more}\n"),
                                 "brigade: reports/D1-review-1.md: Edit 1 has a File, Old text, or New text line after "
                                 "whitespace, a block quote mark, or a list mark, or an Old text or New text line that ends in "
                                 "whitespace")

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

    def test_edits_verify_and_pass_record_refuse_a_file_old_text_or_new_text_line_directly_below_a_line_that_is_none_of_empty_only_spaces_and_tabs_the_edit_heading_a_closing_fence_and_those_three_lines(self):
        cases = []
        for line in ("File: `src/greeting.txt`", "Old text:", "New text:"):
            for above, below in self.AROUND:
                body = self.one_edit_report().replace(line, "\n".join(filter(None, (above, line, below))))
                cases.append((body, f": line {body.split(chr(10)).index(line) + 1}{self.BELOW}"))
        self.assert_edits_verify_and_pass_record_refuse(cases)

    def test_edits_verify_and_pass_record_refuse_an_edit_with_a_line_that_is_not_empty_and_not_only_spaces_and_tabs_between_an_old_text_or_new_text_line_and_the_fence_below_it(self):
        cases = []
        for label, fence in (("Old text:", "```"), ("New text:", "```text")):
            for between in ("A note.\n", "\nA note.\n\n", "- item\n", "\u00a0\n", "> quoted\n\n"):
                body = self.one_edit_report().replace(f"{label}\n\n", f"{label}\n{between}")
                cases.append((body, f": line {body.split(chr(10)).index(fence) + 1}{self.APART}"))
        body = self.one_edit_report().replace("File: `src/greeting.txt`\n\nOld text:\n\n",
                                              "File: `src/greeting.txt` ``\nOld text:\n`` and a note\n\n")
        self.assert_edits_verify_and_pass_record_refuse(cases + [(body, ": line 11" + self.APART)])

    def test_edits_verify_and_pass_record_refuse_an_edit_with_a_line_that_starts_once_its_indentation_block_quote_marks_list_marks_and_end_whitespace_are_removed_with_file_and_a_backtick_and_a_path_that_starts_with_a_space_or_a_backtick_ends_at_no_backtick_or_ends_at_two(self):
        report = self.one_edit_report()
        cases = [report.replace("File: `src/greeting.txt`", line)
                 for line in ("File: `src/greeting.txt`` b`", "File: ` src/greeting.txt `", "File: ``src/greeting.txt``",
                              "File: `src/greeting.txt", "File: ``")]
        cases += [report.replace("### Edit 1\n", f"### Edit 1\n\n{line}\n")
                  for line in ("> File: ``src/other.txt``", "  - File: ` src/other.txt `", "\u00a0File: `src/other.txt``")]
        self.assert_edits_verify_and_pass_record_refuse([(body, self.MALFORMED) for body in cases])

    def test_edits_verify_and_pass_record_refuse_a_report_that_starts_with_a_byte_order_mark_and_one_that_holds_a_nul_character(self):
        report = self.one_edit_report()
        self.assert_edits_verify_and_pass_record_refuse([("\ufeff" + report, " starts with a byte order mark"),
                                                         (report.replace("wrold", "wr\0old"), " holds a NUL character"),
                                                         ("Notes\0\n\n" + report, " holds a NUL character")])

    def test_edits_verify_reads_an_edit_whose_lines_stand_directly_below_one_another_in_a_report_with_lines_that_start_with_a_digit_that_is_not_ascii_and_a_period_or_a_closing_parenthesis(self):
        reviewed = self.edits_store()
        notes = ("## Notes\n\n\u0661. ```\n\n\uff11) ~~~\n\nNote\n\u0661. ---\n\n\u0661. ## Edits\n\n"
                 "\u0967. <details>\n\n")
        self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n" + notes + "## Edits\n\n"
                         "### Edit 1\nFile: `src/greeting.txt`, line 1.\nOld text:\n```\nhelo\n```\nNew text:\n```text\nhello\n```\n\n"
                         "\u0661. Old text:\n\n\u0661. File: `src/other.txt`\n\n"
                         "### Edit 2\n\nFile: `src/greeting.txt`\nOld text:\n\n```\nwrold\n```\nNew text:\n```\nworld\n```\n")
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    @unittest.skipIf(os.geteuid() == 0, "the superuser reads a file with no read permission")
    def test_pass_record_refuses_a_report_that_cannot_be_read(self):
        self.fired_bug_fix()
        report = self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n" + self.section())
        report.chmod(0)
        self.assertEqual(self.record_edits("abc", "--edits", "D1-review-1.md", ok=False),
                         "brigade: reports/D1-review-1.md cannot be read: Permission denied")
        self.assertEqual(self.pass_rows(), [])

    def test_pass_record_refuses_a_report_that_holds_a_carriage_return(self):
        self.fired_bug_fix()
        report = "Verdict: pass with edits\n\n" + self.section()
        for body in (report.replace("\n", "\r\n"), report.replace("wrold", "wrold\r"), "Notes\r" + report):
            with self.subTest(body=body):
                self.assertEqual(self.refused_edits(body), "brigade: reports/D1-review-1.md holds a carriage return")

    def test_pass_record_refuses_a_report_that_is_not_utf_8(self):
        self.fired_bug_fix()
        report = ("Verdict: pass with edits\n\n" + self.section()).encode()
        self.assertEqual(self.refused_edits(report.replace(b"world", b"w\xf6rld")), "brigade: reports/D1-review-1.md is not UTF-8")

    def test_edits_verify_reads_tilde_fences_and_closing_fences_with_spaces_around_them_below_notes_that_report_parts_does_not_refuse(self):
        reviewed = self.edits_store()
        notes = ("## Notes\n\n> quoted\n\n- item\n\n      nested\n\n-```\n\n```a`b\n\n``\n\n    <details>\n\n<\u00e9\n\n"
                 "< 3 items\n\n---\n\n## Rule\n---\n\n```\ncode\n```\n---\n\n- File: `src/other.txt`\n\n  Old text:\n\n"
                 "#hashtag\n\n####### seven\n\n")
        self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n" + notes + "## Edits\n\n"
                         "### Edit 1\n\nFile: `src/greeting.txt`\n\nOld text:\n\n~~~\nhelo\n~~~\n\n"
                         "New text:\n\n~~~~text one `two`\nhello\n  ~~~~~ \t\n\n"
                         "### Edit 2\n\nFile: `src/greeting.txt`\n\nOld text:\n\n```\nwrold\n   ```\n\nNew text:\n\n```\nworld\n```  \n")
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, head)}", ""))

    def test_edits_verify_refuses_a_build_command_that_names_a_program_that_does_not_exist(self):
        command = "no-such-program-d152 gen"
        reviewed = self.edits_store(build=command)
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        code, out, err = self.verify(reviewed, head)
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith(f"brigade: cannot run {command}: "), err)
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_refuses_a_to_and_a_from_that_name_a_commit_by_a_ref(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        refusal = "brigade: {} names a commit by something other than the start of its id; pass the commit id"
        self.assertEqual(self.verify(reviewed, "HEAD"), (1, "", refusal.format("HEAD")))
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record_edits("main", "--edits", "D1-review-1.md")
        self.assertEqual(self.verify("main", head), (1, "", refusal.format("main")))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_a_head_that_left_an_edit_unapplied(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello wrold\nsecond line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, "FAIL out/greeting.txt: differs from what the build writes\n"
                             f"FAIL src/greeting.txt: differs from {reviewed} with the edits applied",
                          self.not_verified(reviewed, head)))

    def test_edits_verify_refuses_a_report_that_no_longer_has_an_edits_section(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.review_file("D1-review-1.md", "Verdict: pass with edits\n\n## Blockers\n\nNone.\n\n## Edits verified\n\nYes.\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, "", "brigade: reports/D1-review-1.md holds no edit under a ## Edits heading"))
        (self.at / "reports" / "D1-review-1.md").unlink()
        self.assertEqual(self.verify(reviewed, head),
                         (1, "", "brigade: reports/D1-review-1.md does not exist; nothing verified"))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_a_head_whose_build_output_is_stale(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED, out=self.SOURCE.upper())
        self.assertEqual(self.verify(reviewed, head),
                         (1, "FAIL out/greeting.txt: differs from what the build writes\nedited src/greeting.txt",
                          self.not_verified(reviewed, head)))

    def test_edits_verify_fails_when_the_check_command_exits_non_zero(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "second", "forbidden"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("helo wrold\nforbidden line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"built out/greeting.txt\nedited src/greeting.txt\nFAIL check: {self.CHECK_COMMAND} exited 3",
                          f"check saw forbidden\n{self.not_verified(reviewed, head)}"))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_fails_when_the_build_command_exits_non_zero(self):
        reviewed = self.edits_store()
        self.edits_report(self.edit_block("1", "src/greeting.txt", "second", "unbuildable"))
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("helo wrold\nunbuildable line\n")
        self.assertEqual(self.verify(reviewed, head),
                         (1, f"FAIL build: {self.BUILD_COMMAND} exited 2",
                          f"build refuses unbuildable\n{self.not_verified(reviewed, head)}"))

    def test_edits_verify_reads_a_four_backtick_fence_around_three_backticks_and_an_empty_new_text(self):
        note = "Intro\n```\ncode\n```\nremove me\nEnd\n"
        reviewed = self.edits_store(**{"docs/note.md": note})
        self.edits_report(self.edit_block("1", "docs/note.md", "```\ncode\n```", "```\ncoda\n```", fence="````"),
                          "### Edit 2\n\nFile: `docs/note.md`:5\n\nOld text:\n\n```\nremove me\n```\n\nNew text:\n\n```\n```\n")
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.SOURCE, **{"docs/note.md": "Intro\n```\ncoda\n```\n\nEnd\n"})
        self.assertEqual(self.verify(reviewed, head),
                         (0, f"edited docs/note.md\nok check\n{self.verified(reviewed, head)}", ""))

    def test_edits_verify_and_pass_record_refuse_a_path_that_is_absolute_or_has_a_dot_dot_part(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        for path in ("/srv/app/greeting.txt", "src/../../greeting.txt"):
            self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"), self.edit_block("7b", path, "helo", "hello"))
            refusal = (f"brigade: reports/D1-review-1.md: Edit 7b names {path}; "
                       "a path is relative to the repository and has no .. part")
            self.assertEqual(self.verify(reviewed, head), (1, "", refusal))
            self.assertEqual(self.record_edits(reviewed, "--edits", "D1-review-1.md", ok=False), refusal)
        self.edits_report(self.edit_block("3", "src/greeting.txt", "", "hello"))
        self.assertEqual(self.verify(reviewed, head), (1, "", "brigade: reports/D1-review-1.md: Edit 3 has an empty old text"))
        self.assertEqual(self.edits_rows(), [])

    def test_edits_verify_refuses_a_store_with_no_build_or_check_command_and_takes_none_for_both(self):
        reviewed = self.edits_store(build=None)
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED, out=self.SOURCE.upper())
        self.assertEqual(self.verify(reviewed, head), (1, "", self.NEEDS_COMMANDS))
        self.brigade("set", "--build-command", "none")
        self.assertEqual(self.verify(reviewed, head), (1, "", self.NEEDS_COMMANDS))
        self.brigade("set", "--build-command", "", "--check-command", "none")
        self.assertEqual(self.verify(reviewed, head), (1, "", self.NEEDS_COMMANDS))
        self.brigade("set", "--build-command", "none")
        self.assertEqual(self.verify(reviewed, head), (0, f"edited src/greeting.txt\n{self.verified(reviewed, head)}", ""))
        rebuilt = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, rebuilt),
                         (1, f"FAIL out/greeting.txt: differs from {reviewed} with the edits applied\nedited src/greeting.txt",
                          self.not_verified(reviewed, rebuilt)))

    def test_edits_verify_refuses_a_from_with_no_pass_with_edits_round_and_a_sha_that_is_no_commit(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.brigade("dish", "D1", "--state", "in-progress")
        self.send_back(head)
        self.assertEqual(self.verify(head, head), (1, "", f"brigade: D1 has no pass-with-edits round at {head}"))
        self.assertEqual(self.verify(reviewed[:12], head),
                         (1, "", f"brigade: D1 has no pass-with-edits round at {reviewed[:12]}"))
        absent = "0" * 40
        self.assertEqual(self.verify(reviewed, absent),
                         (1, "", f"brigade: {absent} is not a commit in {self.project.resolve()}"))

    def test_edits_verify_leaves_the_worktree_and_its_head_unchanged(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        (self.project / "untracked.txt").write_text("mine\n")
        (self.project / "src/greeting.txt").write_text("half an edit\n")
        state = lambda: tuple(subprocess.run(("git", *command), cwd=self.project, capture_output=True, text=True,
                                             check=True).stdout
                              for command in (("status", "--porcelain"), ("rev-parse", "HEAD"), ("diff",)))
        before = state()
        self.assertEqual(before[0], " M src/greeting.txt\n?? untracked.txt\n")
        self.assertEqual(self.verify(reviewed, head)[0], 0)
        self.assertEqual(self.verify(reviewed, reviewed)[0], 1)
        self.assertEqual(state(), before)

    def test_a_pass_after_a_pass_with_edits_round_is_refused_until_the_edits_are_verified_for_that_head(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))
        self.assertEqual(self.record_pass_at(head, "--late", ok=False), self.gate(reviewed, head))
        self.assertEqual(len(self.pass_rows()), 1)
        self.assertEqual(self.verify(reviewed, head)[0], 0)
        self.assertEqual(self.record_pass_at(head), "D1 passed")
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", head), f"D1 at {head} passed review by {CLAUDE}")

    def test_verified_edits_do_not_lift_the_refusal_of_a_pass_at_another_head_or_at_the_same_head_written_shorter(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.FIXED)
        self.assertEqual(self.verify(reviewed, head)[0], 0)
        later = self.head_commit(self.FIXED + "one more line\n")
        self.assertEqual(self.record_pass_at(later, ok=False), self.gate(reviewed, later))
        self.assertEqual(self.record_pass_at(head[:12], ok=False), self.gate(reviewed, head[:12]))
        self.assertEqual(len(self.pass_rows()), 1)

    def test_a_second_review_report_lifts_the_refusal_of_a_pass_and_the_edits_report_does_not(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit("hello world\nsecond line, changed\n")
        self.assertEqual(self.verify(reviewed, head)[0], 1)
        self.assertEqual(self.record_pass_at(head, "--report", "reports/D1-review-1.md", ok=False), self.gate(reviewed, head))
        self.review_file("D1-review-2.md", "Verdict: pass\n")
        self.assertEqual(self.brigade("dish", "D1", "--state", "in-review", "--sha", head), "D1 in-review")
        self.assertEqual(self.record_pass_at(head, "--report", "reports/D1-review-2.md"), "D1 passed")

    def test_a_member_pass_after_a_pass_with_edits_round_is_recorded_without_verified_edits(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "anyhead", "--verdict", "pass", "--author", CODEX,
                                      "--verifier", "grok/grok-4.7", "--member"),
                         "D1: member pass on record; D1 stays sent-back")
        self.assertEqual(self.record_pass_at("anyhead", ok=False), self.gate(reviewed, "anyhead"))

    def test_a_send_back_after_a_pass_with_edits_round_ends_the_refusal_of_a_pass(self):
        reviewed = self.edits_store()
        self.two_edits()
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.send_back("h2")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.record_pass_at("h3"), "D1 passed")

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

    def test_ticket_add_with_a_summary_and_no_kind_is_refused_and_changes_no_file(self):
        self.open()
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "add", "--summary", "Tidy the lease docs", ok=False), self.NEEDS_KIND)
        self.assertEqual(self.brigade("ticket", "add", "--summary", "Tidy the lease docs", "--priority", "urgent", "--paths", "docs",
                                      "--decision", ok=False), self.NEEDS_KIND)
        self.assertEqual(self.store_files(), before)

    def test_ticket_add_with_no_kind_and_a_stale_owner_or_no_owner_prints_the_owner_refusal_first(self):
        self.open()
        self.brigade("set", "--thread", "t1")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "add", "--summary", "a", ok=False, owner=False),
                         "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status")
        self.assertEqual(self.brigade("--owner", "t0@1", "ticket", "add", "--summary", "a", ok=False),
                         "brigade: owner t0@1 is stale; this store is owned by t1@1")
        self.assertEqual(self.brigade("--owner", "t1@1", "ticket", "add", "--summary", "a", ok=False), self.NEEDS_KIND)
        self.assertEqual(self.store_files(), before)

    def test_ticket_add_with_no_kind_prints_the_request_source_ref_and_same_text_refusals_first(self):
        self.open()
        self.brigade("set", "--intake", "github")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the login page", "--request", "A2")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix the cache", "--source", "github", "--ref", "#12")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "add", "--summary", "other", "--request", "A2", ok=False),
                         "brigade: request A2 is already T1; nothing added")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "other", "--source", "linear", ok=False),
                         "brigade: no coordinator owns intake from linear; the one that reads it runs set --intake linear")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "other", "--source", "github", "--ref", "#12", ok=False),
                         "brigade: #12 is already T2 (waiting); nothing added")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "fix the login page", ok=False),
                         self.same_text_refusal("T1", "waiting"))
        self.assertEqual(self.store_files(), before)

    def test_ticket_add_records_each_of_the_five_kinds_and_refuses_a_word_that_is_none_of_them(self):
        self.open()
        for number, kind in enumerate(("feature", "fix", "polish", "upkeep", "study"), 1):
            self.assertEqual(self.brigade("ticket", "add", "--summary", f"work {number}", "--kind", kind), f"T{number}")
            self.assertEqual(self.label("rail.tsv", f"T{number}"), (kind, ""))
        before = self.store_files()
        self.assert_refused_choice(self.brigade("ticket", "add", "--summary", "other", "--kind", "chore", ok=False), "--kind", "chore",
                                   ("feature", "fix", "polish", "upkeep", "study"))
        self.assert_refused_choice(self.brigade("ticket", "set", "T1", "--kind", "chore", ok=False), "--kind", "chore",
                                   ("feature", "fix", "polish", "upkeep", "study"))
        self.assertEqual(self.store_files(), before)

    def test_ticket_add_goal_records_an_open_goal_and_a_paused_goal(self):
        self.open()
        self.goal("activity view")
        self.goal("current with upstream")
        self.brigade("goal", "set", "G2", "--state", "paused")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1"), "T1")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "b", "--kind", "upkeep", "--goal", "G2"), "T2")
        self.assertEqual((self.label("rail.tsv", "T1"), self.label("rail.tsv", "T2")), (("fix", "G1"), ("upkeep", "G2")))

    def test_ticket_add_and_ticket_set_refuse_a_goal_id_with_no_row_and_an_empty_goal_and_change_no_file(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G7", ok=False),
                         "brigade: no goal G7; run goal list --all to see every goal")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--goal", "G7", "--priority", "low", ok=False),
                         "brigade: no goal G7; run goal list --all to see every goal")
        self.assertEqual(self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "", ok=False),
                         'brigade: --goal "" names no goal; run goal list --all to see every goal')
        self.assertEqual(self.brigade("ticket", "set", "T1", "--goal", "", "--priority", "low", ok=False),
                         'brigade: --goal "" names no goal; run goal list --all to see every goal')
        self.assertEqual(self.store_files(), before)

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

    def test_ticket_add_and_ticket_set_refuse_a_done_goal_and_change_no_file(self):
        self.assert_closed_goal_takes_no_ticket("done")

    def test_ticket_add_and_ticket_set_refuse_a_dropped_goal_and_change_no_file(self):
        self.assert_closed_goal_takes_no_ticket("dropped")

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

    def test_ticket_set_kind_goal_and_no_goal_change_the_two_cells_of_a_waiting_ticket_and_add_no_log_row(self):
        self.open()
        self.goal("activity view")
        self.goal("current with upstream")
        self.brigade("goal", "set", "G2", "--state", "paused")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        before = ((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes())
        self.assertEqual(self.brigade("ticket", "set", "T1", "--goal", "G1", "--kind", "polish"), "T1 kind polish; goal G1")
        self.assertEqual(self.label("rail.tsv", "T1"), ("polish", "G1"))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--goal", "G2"), "T1 goal G2")
        self.assertEqual(self.label("rail.tsv", "T1"), ("polish", "G2"))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--no-goal"), "T1 unassigned")
        self.assertEqual(self.label("rail.tsv", "T1"), ("polish", ""))
        self.assertEqual(self.brigade("ticket", "set", "T1", "--kind", "study", "--priority", "low", "--no-goal", "--state", "dropped"),
                         "T1 dropped; priority low; kind study; unassigned")
        self.assertEqual(self.label("rail.tsv", "T1"), ("study", ""))
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T1")["state"], "dropped")
        self.assertEqual((self.at / "log.tsv").read_bytes().count(b"\n"), before[0].count(b"\n") + 1)

    def test_ticket_set_with_goal_and_no_goal_together_exits_2_and_changes_no_file(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        before = self.store_files()
        code, out, _ = self.run_raw("ticket", "set", "T1", "--goal", "G1", "--no-goal")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(self.store_files(), before)

    def test_ticket_set_kind_goal_and_no_goal_are_refused_on_a_done_ticket_and_on_a_dropped_ticket_and_change_no_file(self):
        self.open()
        self.goal()
        for number, state in enumerate(("done", "dropped"), 1):
            self.brigade("ticket", "add", "--summary", f"work {number}", "--kind", "fix", "--goal", "G1")
            self.brigade("ticket", "set", f"T{number}", "--state", state)
        before = self.store_files()
        for number, state in enumerate(("done", "dropped"), 1):
            for flags in (("--kind", "polish"), ("--goal", "G1"), ("--no-goal",), ("--priority", "low", "--kind", "polish"),
                          ("--state", "waiting", "--no-goal")):
                with self.subTest(state=state, flags=flags):
                    self.assertEqual(self.brigade("ticket", "set", f"T{number}", *flags, ok=False),
                                     f"brigade: T{number} is {state}; --kind, --goal, and --no-goal change a waiting ticket only")
        self.assertEqual(self.store_files(), before)

    def test_ticket_set_kind_goal_and_no_goal_are_refused_on_an_assigned_ticket_with_its_items_id_and_change_no_file(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        before = self.store_files()
        for flags in (("--kind", "polish"), ("--goal", "G1"), ("--no-goal",), ("--priority", "low", "--kind", "polish")):
            with self.subTest(flags=flags):
                self.assertEqual(self.brigade("ticket", "set", "T1", *flags, ok=False),
                                 "brigade: T1 is assigned; --kind, --goal, and --no-goal change a waiting ticket only; "
                                 "D1 keeps the kind and goal it took when it started")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("ticket", "set", "T1", "--priority", "low"), "T1 priority low")

    def test_ticket_set_kind_goal_and_no_goal_are_refused_on_a_waiting_ticket_that_an_item_in_flight_names_and_change_no_file_and_priority_alone_is_accepted(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G2", "--priority", "low")
        self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "feature", "--summary", "Build a")
        for ticket in ("T1", "T2"):
            self.assertEqual(self.brigade("ticket", "set", ticket, "--state", "waiting"), f"{ticket} waiting")
        for state in ("in-progress", "in-review", "passed", "sent-back", "blocked", "queued"):
            self.hand_edit("dishes.tsv", "D1", state=state)
            before = self.store_files()
            for ticket, flags in (("T1", ("--kind", "polish")), ("T1", ("--goal", "G2")), ("T1", ("--no-goal",)),
                                  ("T1", ("--priority", "urgent", "--kind", "polish")), ("T2", ("--goal", "G1"))):
                with self.subTest(state=state, ticket=ticket, flags=flags):
                    self.assertEqual(self.brigade("ticket", "set", ticket, *flags, ok=False),
                                     f"brigade: {ticket} is a ticket of D1, which is {state}; --kind, --goal, and --no-goal change "
                                     "a waiting ticket that no item in flight names; D1 keeps the kind and goal it took when it "
                                     f"started; drop D1, and every other item in flight that names {ticket}, first, or leave {ticket} as it is")
            self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("ticket", "set", "T1", "--priority", "low"), "T1 priority low")

    def test_ticket_set_kind_and_goal_change_a_ticket_set_back_to_waiting_whose_item_is_merged_or_dropped_and_leave_the_items_cells(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.brigade("ticket", "set", "T1", "--state", "waiting")
        self.hand_edit("dishes.tsv", "D1", state="merged")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--kind", "polish", "--goal", "G2"), "T1 kind polish; goal G2")
        self.assertEqual((self.label("rail.tsv", "T1"), self.label("dishes.tsv", "D1")), (("polish", "G2"), ("fix", "G1")))
        self.hand_edit("dishes.tsv", "D1", state="dropped")
        self.assertEqual(self.brigade("ticket", "set", "T1", "--kind", "upkeep", "--no-goal"), "T1 kind upkeep; unassigned")
        self.assertEqual((self.label("rail.tsv", "T1"), self.label("dishes.tsv", "D1")), (("upkeep", ""), ("fix", "G1")))

    def test_goal_set_refuses_done_and_dropped_while_a_ticket_of_the_goal_waits_and_names_each_such_ticket(self):
        self.open()
        self.goal("activity view")
        self.goal("current with upstream")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G2")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "feature", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "d", "--kind", "polish")
        before = self.store_files()
        for state in ("done", "dropped"):
            with self.subTest(state=state):
                self.assertEqual(self.brigade("goal", "set", "G1", "--state", state, "--evidence", "why", ok=False),
                                 "brigade: G1 still has work: T1, T3 waiting; give each waiting ticket another goal with "
                                 "ticket set --goal, or none with --no-goal; a waiting ticket that an item in flight names keeps its goal until "
                                 "that item is merged or dropped; then run this again")
        self.assertEqual(self.store_files(), before)
        self.brigade("ticket", "set", "T1", "--no-goal")
        self.brigade("ticket", "set", "T3", "--goal", "G2")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "why"), "G1 done")

    def test_a_dropped_ticket_and_a_done_ticket_of_a_goal_do_not_keep_the_goal_open(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "set", "T1", "--state", "dropped")
        self.brigade("ticket", "set", "T2", "--state", "done")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "dropped", "--evidence", "why"), "G1 dropped")

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

    def test_fire_gives_the_item_the_kind_and_goal_of_its_lead_ticket_and_leaves_that_tickets_two_cells_as_they_were(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "polish", "--goal", "G2")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"), "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("polish", "G2"))
        self.assertEqual(self.label("rail.tsv", "T1"), ("polish", "G2"))

    def test_fire_kind_gives_the_kind_to_the_item_and_to_a_lead_ticket_that_has_none(self):
        self.open()
        self.rail_before_kinds("a")
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "upkeep"), "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("upkeep", ""))
        self.assertEqual(self.label("rail.tsv", "T1"), ("upkeep", ""))

    def test_fire_with_no_kind_from_the_ticket_or_the_flag_gives_the_item_and_the_lead_ticket_the_kind_of_its_station(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "9")
        self.rail_before_kinds("a", "b", "c")
        for number, (station, kind) in enumerate((("bug-fix", "fix"), ("perf-issue", "fix"), ("investigation", "study")), 1):
            with self.subTest(station=station):
                self.assertEqual(self.brigade("fire", "--tickets", f"T{number}", "--station", station, "--summary", f"Do {number}"),
                                 f"D{number}")
                self.assertEqual(self.label("dishes.tsv", f"D{number}"), (kind, ""))
                self.assertEqual(self.label("rail.tsv", f"T{number}"), (kind, ""))

    def test_fire_takes_the_lead_tickets_kind_over_the_kind_of_its_station(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "upkeep")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("upkeep", ""), ("upkeep", "")))

    def test_fire_kind_is_taken_over_the_kind_of_the_station(self):
        self.open()
        self.rail_before_kinds("a")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a", "--kind", "polish")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("polish", ""), ("polish", "")))

    def test_fire_of_a_lead_ticket_with_no_kind_on_a_station_that_gives_none_is_refused_and_changes_no_file(self):
        self.open()
        self.rail_before_kinds("a")
        before = self.store_files()
        for station in ("feature", "refactoring", "docs"):
            with self.subTest(station=station):
                self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", station, "--summary", "Do a", ok=False),
                                 f"brigade: T1 has no kind and station {station} gives none; pass --kind {self.KIND_CHOICE}")
        self.assertEqual(self.store_files(), before)

    def test_fire_with_paths_of_a_lead_ticket_with_no_kind_is_refused_before_it_claims_a_lease_and_changes_no_file(self):
        case = self.fake_land()
        (case / "claim-proceed").touch()
        self.open()
        self.rail_before_kinds("a")
        before = self.store_files()
        proc = self.fire_with_fake_land(case, "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--paths", "src")
        _, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()),
                         (1, f"brigade: T1 has no kind and station feature gives none; pass --kind {self.KIND_CHOICE}"))
        self.assertFalse((case / "land-calls").exists())
        self.assertEqual(self.store_files(), before)

    def test_fire_with_no_ticket_needs_a_kind_from_the_flag_or_the_station(self):
        self.open()
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", ",", "--station", "feature", "--summary", "No ticket", ok=False),
                         "brigade: no ticket gives this item a kind and station feature gives none; "
                         f"pass --kind {self.KIND_CHOICE}")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("fire", "--tickets", ",", "--station", "feature", "--summary", "No ticket", "--kind", "study"), "D1")
        self.assertEqual(self.brigade("fire", "--tickets", ",", "--station", "bug-fix", "--summary", "No ticket"), "D2")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("dishes.tsv", "D2")), (("study", ""), ("fix", "")))

    def test_fire_kind_that_differs_from_the_lead_tickets_kind_is_refused_and_an_equal_one_is_accepted(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "feature", ok=False),
                         "brigade: T1 has kind fix, and --kind feature does not match it; "
                         "drop --kind, or run ticket set T1 --kind feature first when no item in flight names T1")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "fix"), "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("fix", ""))

    def test_fire_kind_refuses_a_word_that_is_none_of_the_five(self):
        self.open()
        self.rail_before_kinds("a")
        before = self.store_files()
        self.assert_refused_choice(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "chore",
                                                ok=False), "--kind", "chore", ("feature", "fix", "polish", "upkeep", "study"))
        self.assertEqual(self.store_files(), before)

    def test_fire_refuses_a_ticket_after_the_lead_that_is_under_another_goal_and_takes_it_as_a_ride_along(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2", "--priority", "low")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both", ok=False),
                         "brigade: T2 is under G2 and the lead ticket T1 is under G1; "
                         "name T2 in --ride-along, which takes a low ticket that is not a feature, or start it on its own")
        self.assertEqual(self.store_files(), before)
        rail = self.table_row(self.at, "rail.tsv", "T2")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "Do both"),
                         "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("fix", "G1"))
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2"), {**rail, "state": "assigned", "dish": "D1"})
        self.assertEqual(self.label("rail.tsv", "T2"), ("polish", "G2"))

    def test_fire_refuses_a_ticket_under_a_goal_after_an_unassigned_lead_ticket_and_takes_it_as_a_ride_along(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G1", "--priority", "low")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both", ok=False),
                         "brigade: T2 is under G1 and the lead ticket T1 is unassigned; "
                         "name T2 in --ride-along, which takes a low ticket that is not a feature, or start it on its own")
        self.assertEqual(self.store_files(), before)
        self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "Do both")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T2")), (("fix", ""), ("polish", "G1")))

    def test_fire_takes_a_ticket_of_the_lead_tickets_goal_and_an_unassigned_ticket_with_no_ride_along_and_each_keeps_its_two_cells(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "feature", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "polish")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2,T3", "--station", "bug-fix", "--summary", "Do all"), "D1")
        self.assertEqual(self.label("dishes.tsv", "D1"), ("fix", "G1"))
        self.assertEqual([self.label("rail.tsv", ident) for ident in ("T1", "T2", "T3")],
                         [("fix", "G1"), ("feature", "G1"), ("polish", "")])

    def test_fire_refuses_a_ride_along_id_that_is_not_in_tickets_and_one_that_is_the_lead_ticket(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1", "--priority", "low")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2", "--priority", "low")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x", ok=False),
                         "brigade: --ride-along names T2, which is not in --tickets; add it there or drop it")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T1,T2", "--station", "bug-fix", "--summary", "x",
                                      ok=False),
                         "brigade: T1 is the lead ticket, and the lead ticket cannot ride along; "
                         "put the ticket whose goal the item takes first in --tickets")
        self.assertEqual(self.store_files(), before)

    def test_fire_refuses_a_ride_along_ticket_of_priority_normal_or_urgent(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "polish", "--goal", "G2", "--priority", "urgent")
        before = self.store_files()
        for ident, priority in (("T2", "normal"), ("T3", "urgent")):
            with self.subTest(ident=ident):
                self.assertEqual(self.brigade("fire", "--tickets", f"T1,{ident}", "--ride-along", ident, "--station", "bug-fix",
                                              "--summary", "x", ok=False),
                                 f"brigade: {ident} is {priority}; only a low ticket rides along; "
                                 f"start it on its own, or run ticket set {ident} --priority low")
        self.assertEqual(self.store_files(), before)
        self.brigade("ticket", "set", "T2", "--priority", "low")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x"), "D1")

    def test_fire_takes_a_report_ticket_with_an_empty_priority_cell_as_a_ride_along(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.follow_ups_in("## Follow-ups\n\n- Tidy the guide.\n")
        self.assertEqual(self.table_row(self.at, "rail.tsv", "T2")["priority"], "")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "fix", "--goal", "G2")
        self.assertEqual(self.brigade("fire", "--tickets", "T3,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x"), "D2")
        self.assertEqual((self.label("dishes.tsv", "D2"), self.label("rail.tsv", "T2")), (("fix", "G2"), ("polish", "G1")))

    def test_fire_refuses_a_ride_along_ticket_that_is_a_feature(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "feature", "--goal", "G2", "--priority", "low")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x",
                                      ok=False),
                         "brigade: T2 is a feature, and a feature does not ride along; start it as its own item")
        self.assertEqual(self.store_files(), before)

    def test_fire_refuses_a_low_feature_named_in_ride_along_that_is_under_the_lead_tickets_goal(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "feature", "--goal", "G1", "--priority", "low")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "x",
                                      ok=False),
                         "brigade: T2 is a feature, and a feature does not ride along; start it as its own item")
        self.assertEqual(self.brigade("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "x"), "D1")

    def test_a_goal_change_while_fire_claims_its_lease_is_caught_before_the_write_and_the_lease_is_released(self):
        case = self.fake_land()
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        proc = self.fire_with_fake_land(case, "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both", "--paths", "src")
        _wait_for_path(case / "claiming")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--goal", "G2"), "T2 goal G2")
        before = self.store_files()
        (case / "claim-proceed").touch()
        _, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()),
                         (1, "brigade: nothing fired: T2 is under G2 and the lead ticket T1 is under G1; "
                             "name T2 in --ride-along, which takes a low ticket that is not a feature, or start it on its own"))
        self.assertEqual((case / "land-calls").read_text().splitlines(),
                         ["lease claim --holder perf/D1 --paths src,changes/perf%2Fd1.md", "lease release L7"])
        self.assertEqual(self.store_files(), before)

    def test_fire_of_a_lead_ticket_whose_kind_cell_holds_no_kind_warns_needs_a_kind_and_writes_it_over_the_cell(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.hand_edit("rail.tsv", "T1", kind="bugfix")
        warning = ("brigade: warning: T1 has kind 'bugfix', which is not feature, fix, polish, upkeep, or study; it reads as no kind; "
                   "run ticket set T1 --kind <kind>\n")
        before = self.store_files()
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a"),
                         (1, "", warning + f"brigade: T1 has no kind and station feature gives none; "
                                           f"pass --kind {self.KIND_CHOICE}\n"))
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--kind", "polish"),
                         (0, "D1\n", warning))
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("polish", ""), ("polish", "")))

    def test_fire_of_a_lead_ticket_whose_kind_cell_holds_no_kind_writes_the_kind_of_its_station_over_the_cell(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.hand_edit("rail.tsv", "T1", kind="bugfix")
        code, out, err = self.run_raw("fire", "--tickets", "T1", "--station", "investigation", "--summary", "Do a")
        self.assertEqual((code, out), (0, "D1\n"))
        self.assertIn("T1 has kind 'bugfix'", err)
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("study", ""), ("study", "")))

    def test_fire_of_a_lead_ticket_whose_goal_has_no_row_warns_starts_an_unassigned_item_and_leaves_the_tickets_cell(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G2")
        self.hand_edit("rail.tsv", "T1", goal="G9")
        self.hand_edit("rail.tsv", "T2", goal="G8")
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"),
                         (0, "D1\n", "brigade: warning: T1 names goal 'G9', which has no row in goals.tsv; it reads as unassigned; "
                                     "run ticket set T1 --goal G<n> or --no-goal\n"))
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("fix", ""), ("fix", "G9")))

    def test_fire_warns_of_a_bad_cell_on_a_ticket_after_the_lead_and_reads_that_ticket_as_unassigned(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G2")
        self.hand_edit("rail.tsv", "T2", goal="G9")
        self.assertEqual(self.run_raw("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both"),
                         (0, "D1\n", "brigade: warning: T2 names goal 'G9', which has no row in goals.tsv; it reads as unassigned; "
                                     "run ticket set T2 --goal G<n> or --no-goal\n"))
        self.assertEqual(self.label("rail.tsv", "T2"), ("fix", "G9"))

    def test_fire_of_a_ticket_with_no_kind_prints_the_owner_refusal_and_the_not_waiting_refusal_first(self):
        self.open()
        self.rail_before_kinds("a", "b")
        self.brigade("ticket", "set", "T2", "--state", "done")
        self.brigade("set", "--thread", "t1")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "x", ok=False, owner=False),
                         "brigade: this store is owned by t1@1; pass --owner <thread>@<generation> from status")
        self.assertEqual(self.brigade("--owner", "t0@1", "fire", "--tickets", "T1", "--station", "feature", "--summary", "x", ok=False),
                         "brigade: owner t0@1 is stale; this store is owned by t1@1")
        self.assertEqual(self.brigade("fire", "--tickets", "T2", "--station", "feature", "--summary", "x", ok=False),
                         "brigade: T2 is done, not waiting")
        self.assertEqual(self.store_files(), before)

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

    def test_fire_of_a_ticket_with_no_kind_on_paths_another_lease_holds_is_refused_for_the_kind_and_logs_no_block(self):
        self.init_landing()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": self.land_env()["XDG_STATE_HOME"]}):
            self.open()
            self.rail_before_kinds("a")
            self.land("lease", "claim", "--holder", "engine/D1", "--paths", "src")
            leases, before = self.land("lease", "list"), self.store_files()
            self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--paths", "src",
                                          ok=False),
                             f"brigade: T1 has no kind and station feature gives none; pass --kind {self.KIND_CHOICE}")
            self.assertEqual((self.land("lease", "list"), self.store_files()), (leases, before))
            refused = self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a", "--paths", "src",
                                   "--kind", "upkeep", ok=False)
            self.assertIn("nothing fired: paths overlap L1 held by engine/D1", refused)
            self.assertEqual(self.blocked_note(self.at, "T1"),
                             {"kind": "lease", "tickets": ["T1"], "station": "feature", "summary": "Do a", "paths": "src",
                              "timebox": 60, "work": "upkeep"})

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

    def test_a_blocked_fire_with_no_kind_flag_and_no_ride_along_writes_the_note_keys_it_wrote_before_kinds(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Do b", "--branch", "docs/gate", ok=False)
        self.assertEqual(self.blocked_note(self.at, "T2"),
                         {"kind": "workers", "tickets": ["T2"], "station": "bug-fix", "summary": "Do b", "paths": "", "timebox": 60,
                          "branch": "docs/gate"})

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

    def test_a_dish_update_and_a_review_pass_leave_the_items_kind_and_goal_cells_as_fire_wrote_them(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc", "--thread", "w1")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T1")), (("feature", "G1"), ("feature", "G1")))
        self.assertEqual(self.table_row(self.at, "dishes.tsv", "D1")["state"], "merged")

    def test_a_dropped_items_lead_ticket_waits_again_with_the_kind_fire_wrote_on_it(self):
        self.open()
        self.rail_before_kinds("a")
        self.brigade("fire", "--tickets", "T1", "--station", "investigation", "--summary", "Look at a")
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1 waiting again")
        row = self.table_row(self.at, "rail.tsv", "T1")
        self.assertEqual((row["state"], row["kind"], row["goal"]), ("waiting", "study", ""))
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a"), "D2")
        self.assertEqual(self.label("dishes.tsv", "D2"), ("study", ""))

    def assert_goal_keeps_open(self, ident, held, advice):
        before = self.store_files()
        for state in ("done", "dropped"):
            with self.subTest(goal=ident, state=state):
                self.assertEqual(self.brigade("goal", "set", ident, "--state", state, "--evidence", "why", ok=False),
                                 f"brigade: {ident} still has work: {held}; {advice}; then run this again")
        self.assertEqual(self.store_files(), before)

    def test_goal_set_refuses_done_and_dropped_while_an_item_of_the_goal_is_in_flight_and_names_the_item(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        for state in ("in-progress", "in-review", "sent-back", "blocked"):
            with self.subTest(item=state):
                self.brigade("dish", "D1", "--state", state, "--sha", "abc")
                self.assert_goal_keeps_open("G1", "D1 in flight", "finish or drop each item in flight")
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.assert_goal_keeps_open("G1", "D1 in flight", "finish or drop each item in flight")
        self.brigade("dish", "D1", "--state", "queued")
        self.assert_goal_keeps_open("G1", "D1 in flight", "finish or drop each item in flight")
        self.brigade("dish", "D1", "--state", "merged")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "merged"), "G1 done")

    def test_goal_set_refuses_done_and_dropped_while_a_ticket_of_the_goal_rides_along_in_another_goals_item(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2", "--priority", "low")
        self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "Do both")
        self.assert_goal_keeps_open("G2", "T2 riding along", "finish or drop the item each of those tickets rides along in")
        self.assertEqual(self.brigade("dish", "D1", "--state", "dropped"), "D1 dropped; T1, T2 waiting again")
        self.assert_goal_keeps_open("G2", "T2 waiting", "give each waiting ticket another goal with ticket set --goal, or none with --no-goal; a waiting "
                                    "ticket that an item in flight names keeps its goal until that item is merged or dropped")

    def test_goal_set_names_the_waiting_tickets_the_items_in_flight_and_the_tickets_riding_along_in_one_refusal(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish", "--goal", "G2", "--priority", "low")
        self.brigade("ticket", "add", "--summary", "c", "--kind", "fix", "--goal", "G2")
        self.brigade("ticket", "add", "--summary", "d", "--kind", "fix", "--goal", "G2")
        self.brigade("fire", "--tickets", "T1,T2", "--ride-along", "T2", "--station", "bug-fix", "--summary", "Do a and b")
        self.brigade("fire", "--tickets", "T3", "--station", "bug-fix", "--summary", "Do c")
        self.assert_goal_keeps_open(
            "G2", "T4 waiting; D2 in flight; T2 riding along",
            "give each waiting ticket another goal with ticket set --goal, or none with --no-goal; a waiting ticket that an item in "
            "flight names keeps its goal until that item is merged or dropped; finish or drop each item in flight; "
            "finish or drop the item each of those tickets rides along in")

    def test_from_report_gives_each_ticket_the_kind_polish_and_the_goal_of_the_reports_item(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "feature", "--goal", "G2")
        self.brigade("fire", "--tickets", "T1", "--station", "feature", "--summary", "Do a")
        self.assertEqual(len(self.follow_ups_in(BULLETS).splitlines()), 3)
        self.assertEqual([self.label("rail.tsv", ident) for ident in ("T2", "T3", "T4")], [("polish", "G2")] * 3)

    def test_from_report_on_an_item_with_no_kind_and_no_goal_files_tickets_with_no_kind_and_no_goal(self):
        self.open()
        self.rail_before_kinds("a")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a")
        self.hand_edit("dishes.tsv", "D1", kind="")
        self.follow_ups_in(BULLETS)
        self.assertEqual([self.label("rail.tsv", ident) for ident in ("T2", "T3", "T4")], [("", "")] * 3)

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

    def test_follow_up_kind_is_empty_for_an_item_with_no_kind_and_else_fix_at_urgent_or_normal_and_polish_at_low(self):
        follow_up_kind = runpy.run_path(str(SCRIPT))["follow_up_kind"]
        self.assertEqual([follow_up_kind("", priority) for priority in ("urgent", "normal", "low")], ["", "", ""])
        self.assertEqual([follow_up_kind("feature", priority) for priority in ("urgent", "normal", "low")], ["fix", "fix", "polish"])

    def test_goal_add_prints_the_new_id_appends_one_open_row_and_logs_one_goal_row(self):
        self.open()
        self.assertEqual(self.goal("activity view", "--share", "2"), "G1")
        self.assertEqual(self.goal("current with upstream"), "G2")
        rows = self.rows_of("goals.tsv")
        self.assertEqual([row[:1] + row[2:] for row in rows],
                         [["G1", "open", "activity view", "One page of activity", "The page ships", "2", ""],
                          ["G2", "open", "current with upstream", "One page of activity", "The page ships", "", ""]])
        self.assertEqual(self.goal_log(), [["G1", "open", "activity view"], ["G2", "open", "current with upstream"]])

    def test_goal_add_refuses_a_name_a_purpose_or_a_done_test_that_is_only_whitespace_and_changes_no_file(self):
        self.open()
        before = self.store_files()
        for flag in ("--name", "--purpose", "--done"):
            words = {"--name": "n", "--purpose": "p", "--done": "d", flag: " \t\n"}
            with self.subTest(flag=flag):
                self.assertEqual(self.brigade("goal", "add", *[word for pair in words.items() for word in pair], ok=False),
                                 "brigade: goal add needs a nonempty --name, --purpose, and --done")
        self.assertEqual(self.store_files(), before)

    def test_goal_add_refuses_the_name_of_an_open_goal_and_of_a_paused_goal_and_adds_no_row(self):
        self.open()
        self.goal("Activity view")
        before = self.store_files()
        self.assertEqual(self.goal("activity view", ok=False), "brigade: same name as G1 (open); nothing added")
        self.assertEqual(self.goal("  activity\tVIEW ", ok=False), "brigade: same name as G1 (open); nothing added")
        self.assertEqual(self.store_files(), before)
        self.brigade("goal", "set", "G1", "--state", "paused")
        self.assertEqual(self.goal("activity view", ok=False), "brigade: same name as G1 (paused); nothing added")
        self.assertEqual(len(self.rows_of("goals.tsv")), 1)

    def test_goal_add_takes_the_name_of_a_done_goal_and_of_a_dropped_goal(self):
        self.open()
        self.goal("activity view")
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "shipped")
        self.assertEqual(self.goal("activity view"), "G2")
        self.brigade("goal", "set", "G2", "--state", "dropped", "--evidence", "not wanted")
        self.assertEqual(self.goal("activity view"), "G3")

    def test_goal_add_and_goal_set_refuse_a_share_that_is_not_a_whole_number_of_1_or_more_and_change_no_file(self):
        self.open()
        self.goal("kept")
        before = self.store_files()
        for share in ("0", "00", "-1", "two", "1.5", "+2", "\u0663"):
            refusal = f'brigade: --share takes a whole number of 1 or more, or "" for no limit, got {share!r}'
            with self.subTest(share=share):
                self.assertEqual(self.goal("new", f"--share={share}", ok=False), refusal)
                self.assertEqual(self.brigade("goal", "set", "G1", f"--share={share}", ok=False), refusal)
        self.assertEqual(self.store_files(), before)

    def test_goal_add_and_goal_set_store_a_share_without_its_leading_zeros_and_an_empty_share_as_an_empty_cell(self):
        self.open()
        self.goal("a", "--share", "02")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["share"], "2")
        self.assertEqual(self.brigade("goal", "set", "G1", "--share", "010"), "G1 share 10")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["share"], "10")
        self.assertEqual(self.brigade("goal", "set", "G1", "--share", ""), "G1 no share")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["share"], "")

    def test_goal_set_refuses_an_id_with_no_row_and_a_call_with_no_change_flag_and_changes_no_file(self):
        self.open()
        self.goal()
        before = self.store_files()
        self.assertEqual(self.brigade("goal", "set", "G7", "--state", "paused", ok=False),
                         "brigade: no goal G7; run goal list --all to see every goal")
        self.assertEqual(self.brigade("goal", "set", "G1", ok=False),
                         "brigade: goal set needs --state, --name, --purpose, --done, or --share")
        self.assertEqual(self.brigade("goal", "set", "G1", "--evidence", "x", ok=False),
                         "brigade: goal set needs --state, --name, --purpose, --done, or --share")
        self.assertEqual(self.store_files(), before)

    def test_goal_set_refuses_a_name_a_purpose_or_a_done_test_that_is_only_whitespace_and_changes_no_file(self):
        self.open()
        self.goal()
        before = self.store_files()
        for flag in ("--name", "--purpose", "--done"):
            with self.subTest(flag=flag):
                self.assertEqual(self.brigade("goal", "set", "G1", flag, " ", "--state", "paused", ok=False),
                                 f"brigade: goal set {flag} takes text; a goal keeps a name, a purpose, and a done test")
        self.assertEqual(self.store_files(), before)

    def test_goal_set_refuses_evidence_beside_state_open_state_paused_or_no_state_and_changes_no_file(self):
        self.open()
        self.goal()
        before = self.store_files()
        for flags in (("--state", "paused"), ("--state", "open"), ("--name", "other")):
            with self.subTest(flags=flags):
                self.assertEqual(self.brigade("goal", "set", "G1", *flags, "--evidence", "x", ok=False),
                                 "brigade: --evidence goes only with --state done or --state dropped")
        self.assertEqual(self.store_files(), before)

    def test_goal_set_refuses_a_move_to_done_and_a_move_to_dropped_with_no_evidence_or_with_evidence_that_is_only_whitespace(self):
        self.open()
        self.goal()
        before = self.store_files()
        for state in ("done", "dropped"):
            with self.subTest(state=state):
                self.assertEqual(self.brigade("goal", "set", "G1", "--state", state, ok=False),
                                 f"brigade: --state {state} needs --evidence <one line>")
                self.assertEqual(self.brigade("goal", "set", "G1", "--state", state, "--evidence", " ", ok=False),
                                 f"brigade: --state {state} needs --evidence <one line>")
        self.assertEqual(self.store_files(), before)

    def test_goal_set_state_done_and_state_dropped_store_the_time_and_the_evidence_in_the_closed_cell_and_log_the_name_and_the_evidence(self):
        self.open()
        self.goal("activity view")
        self.goal("current with upstream")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "The page\tshipped in #170"), "G1 done")
        self.assertEqual(self.brigade("goal", "set", "G2", "--state", "dropped", "--evidence", "Upstream is frozen"), "G2 dropped")
        first, second = (self.table_row(self.at, "goals.tsv", ident) for ident in ("G1", "G2"))
        self.assertEqual((first["state"], second["state"]), ("done", "dropped"))
        stamp, evidence = first["closed"].split(" ", 1)
        self.assertEqual(evidence, "The page shipped in #170")
        self.assertEqual(datetime.fromisoformat(stamp).tzinfo, timezone.utc)
        self.assertEqual(second["closed"].split(" ", 1)[1], "Upstream is frozen")
        self.assertEqual(self.goal_log()[2:], [["G1", "done", "activity view: The page shipped in #170"],
                                               ["G2", "dropped", "current with upstream: Upstream is frozen"]])

    def test_goal_set_moves_an_open_goal_to_paused_and_a_paused_goal_to_open_and_logs_each_move(self):
        self.open()
        self.goal()
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "paused"), "G1 paused")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["state"], "paused")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "open"), "G1 open")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["state"], "open")
        self.assertEqual(self.goal_log(), [["G1", "open", "activity view"], ["G1", "paused", "activity view"],
                                           ["G1", "open", "activity view"]])

    def test_goal_set_closes_a_paused_goal_with_evidence(self):
        self.open()
        self.goal()
        self.brigade("goal", "set", "G1", "--state", "paused")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "dropped", "--evidence", "not wanted"), "G1 dropped")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["state"], "dropped")

    def test_goal_set_refuses_a_move_from_done_or_dropped_to_a_state_other_than_open_and_changes_no_file(self):
        self.open()
        self.goal("a")
        self.goal("b")
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "shipped")
        self.brigade("goal", "set", "G2", "--state", "dropped", "--evidence", "not wanted")
        before = self.store_files()
        for ident, state, flags in (("G1", "done", ("--state", "dropped", "--evidence", "x")), ("G1", "done", ("--state", "paused")),
                                    ("G2", "dropped", ("--state", "done", "--evidence", "x")), ("G2", "dropped", ("--state", "paused"))):
            with self.subTest(ident=ident, flags=flags):
                self.assertEqual(self.brigade("goal", "set", ident, *flags, ok=False),
                                 f"brigade: {ident} is {state}; a {state} goal moves only to open; "
                                 f"run goal set {ident} --state open first")
        self.assertEqual(self.store_files(), before)

    def test_goal_set_state_open_on_a_done_goal_and_on_a_dropped_goal_empties_the_closed_cell_and_logs_the_move(self):
        self.open()
        self.goal("a")
        self.goal("b")
        self.brigade("goal", "set", "G1", "--state", "done", "--evidence", "shipped")
        self.brigade("goal", "set", "G2", "--state", "dropped", "--evidence", "not wanted")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "open"), "G1 open")
        self.assertEqual(self.brigade("goal", "set", "G2", "--state", "open"), "G2 open")
        for ident in ("G1", "G2"):
            row = self.table_row(self.at, "goals.tsv", ident)
            self.assertEqual((row["state"], row["closed"]), ("open", ""))
        self.assertEqual(self.goal_log()[2:], [["G1", "done", "a: shipped"], ["G2", "dropped", "b: not wanted"],
                                               ["G1", "open", "a"], ["G2", "open", "b"]])

    def test_goal_set_with_the_state_the_goal_has_prints_already_and_changes_no_file(self):
        self.open()
        self.goal("a")
        self.goal("b")
        self.goal("c")
        self.brigade("goal", "set", "G2", "--state", "paused")
        self.brigade("goal", "set", "G3", "--state", "done", "--evidence", "shipped")
        before = self.store_files()
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "open"), "G1 already open")
        self.assertEqual(self.brigade("goal", "set", "G2", "--state", "paused"), "G2 already paused")
        self.assertEqual(self.brigade("goal", "set", "G3", "--state", "done"), "G3 already done")
        self.assertEqual(self.brigade("goal", "set", "G3", "--state", "done", "--evidence", "other words"), "G3 already done")
        self.assertEqual(self.store_files(), before)

    def test_goal_set_prints_its_changes_in_the_order_state_name_purpose_done_test_share_and_writes_each(self):
        self.open()
        self.goal()
        self.assertEqual(self.brigade("goal", "set", "G1", "--share", "3", "--done", "Two pages ship", "--purpose", "Why",
                                      "--name", "Activity\tpage", "--state", "paused"),
                         "G1 paused; name Activity page; purpose changed; done test changed; share 3")
        row = self.table_row(self.at, "goals.tsv", "G1")
        self.assertEqual([row[key] for key in ("state", "name", "purpose", "done", "share", "closed")],
                         ["paused", "Activity page", "Why", "Two pages ship", "3", ""])
        self.assertEqual(self.goal_log()[-1], ["G1", "paused", "Activity page"])

    def test_goal_set_that_changes_no_state_adds_no_log_row(self):
        self.open()
        self.goal()
        before = ((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes())
        self.assertEqual(self.brigade("goal", "set", "G1", "--name", "other", "--share", "4"), "G1 name other; share 4")
        self.assertEqual(((self.at / "log.tsv").read_bytes(), (self.at / "restaurant.json").read_bytes()), before)

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

    def test_a_goal_state_that_is_none_of_the_four_reads_as_open_with_a_warning_and_goal_set_state_rewrites_the_cell(self):
        self.open()
        self.goal()
        self.hand_edit("goals.tsv", "G1", state="finished")
        warning = ("brigade: warning: G1 has state 'finished', which is not open, paused, done, or dropped; it reads as open; "
                   "run goal set G1 --state <state>\n")
        code, out, err = self.run_raw("goal", "list")
        self.assertEqual((code, err), (0, warning))
        self.assertEqual(out.splitlines()[0], "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs")
        self.assertEqual(self.goal("activity view", ok=False), warning + "brigade: same name as G1 (open); nothing added")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "open"), "G1 open")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["state"], "open")
        self.assertEqual(self.run_raw("goal", "list")[2], "")
        self.hand_edit("goals.tsv", "G1", state="finished")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "paused"), "G1 paused")
        self.assertEqual(self.table_row(self.at, "goals.tsv", "G1")["state"], "paused")

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

    def test_goal_list_in_a_store_with_no_goals_table_creates_none_and_the_first_goal_add_writes_the_table_with_its_header(self):
        self.open()
        (self.at / "goals.tsv").unlink()
        self.assertEqual(self.brigade("goal", "list"), f"no open or paused goals\n{self.NO_UNASSIGNED_WORK}")
        self.assertEqual(self.brigade("goal", "set", "G1", "--state", "paused", ok=False),
                         "brigade: no goal G1; run goal list --all to see every goal")
        self.assertFalse((self.at / "goals.tsv").exists())
        self.assertEqual(self.goal(), "G1")
        header, row = (self.at / "goals.tsv").read_text().splitlines()
        self.assertEqual(header, "id\tat\tstate\tname\tpurpose\tdone\tshare\tclosed")
        self.assertEqual(row.split("\t")[2:], ["open", "activity view", "One page of activity", "The page ships", "", ""])

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

    def test_goal_set_state_refuses_a_state_that_is_none_of_the_four(self):
        self.open()
        self.goal()
        self.assert_refused_choice(self.brigade("goal", "set", "G1", "--state", "finished", ok=False), "--state", "finished",
                                   ("open", "paused", "done", "dropped"))


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

    def test_the_worked_store_holds_the_kind_and_goal_cells_its_tests_count(self):
        self.worked_store()
        self.assertEqual([(row[0], row[2], row[5], *row[10:]) for row in self.rows_of("rail.tsv")], [
            ("T1", "done", "D1", "feature", "G1"), ("T2", "assigned", "D2", "fix", "G1"), ("T3", "assigned", "D2", "polish", "G2"),
            ("T4", "waiting", "", "fix", "G1"), ("T5", "waiting", "", "polish", ""), ("T6", "assigned", "D3", "upkeep", "G2"),
            ("T7", "assigned", "D4", "study", ""), ("T8", "waiting", "", "", ""), ("T9", "waiting", "", "feature", "G1"),
            ("T10", "done", "D5", "polish", "")])
        self.assertEqual([(row[0], row[2], row[4], *row[15:]) for row in self.rows_of("dishes.tsv")], [
            ("D1", "merged", "T1", "feature", "G1"), ("D2", "in-review", "T2,T3", "fix", "G1"), ("D3", "sent-back", "T6", "upkeep", "G2"),
            ("D4", "in-progress", "T7", "study", ""), ("D5", "merged", "T10", "polish", "")])

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

    def test_ticket_list_prints_the_kind_then_the_goal_after_the_priority_and_neither_for_a_ticket_with_no_kind_and_no_goal(self):
        self.worked_store()
        self.hand_edit("rail.tsv", "T9", kind="")
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting").splitlines(), [
            "T4 waiting normal fix G1 [user] Activity page drops a row",
            "T5 waiting low polish [user] Tidy the lease docs",
            "T8 waiting normal [user] Installer keeps a relative config home",
            "T9 waiting normal G1 [user] Activity page filters by coordinator",
        ])
        self.assertEqual(self.brigade("ticket", "list", "--state", "assigned").splitlines()[:2], [
            "T2 assigned normal fix G1 [user] Lease check arms a reservation",
            "T3 assigned low polish G2 [user] Tidy the lease help",
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

    def test_close_groups_in_progress_and_new_tickets_by_goal_and_prints_the_kind_on_the_lines_of_merged_in_progress_and_new_tickets(self):
        self.close_setup()
        self.brigade("dish", "D1", "--state", "in-review", "--sha", "abc")
        self.record("abc", "pass")
        self.brigade("dish", "D1", "--state", "merged")
        self.brigade("dish", "D4", "--state", "in-review", "--sha", "def")
        self.brigade("pass", "record", "D4", "--sha", "def", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX,
                     "--note", "still hangs")
        code, out, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[4:], [
            "## Merged", "",
            "- study D1 (T1): Measure capacity", "",
            "## Sent back after review", "",
            "- D4 (T5): Fix the splash", "",
            "## In progress", "",
            "### G1 activity view", "",
            "- fix D2 (T2, T3): Fix the lease check", "",
            "### Unassigned", "",
            "- upkeep D3 (T4): Follow the nightly", "",
            "## New tickets, not started", "",
            "### G1 activity view", "",
            "- fix T6: Drops a row", "",
            "### G2 current with upstream", "",
            "- polish T7: Later", "",
            "### Unassigned", "",
            "- polish T8: Tidy the docs",
            "- T9: Installer",
        ])

    def test_close_prints_no_goal_heading_in_a_store_whose_tickets_and_items_have_kinds_and_no_goal(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "polish")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.assertEqual(self.close_run("--dry-run")[1].splitlines()[4:],
                         ["## In progress", "", "- fix D1 (T1): Fix a", "", "## New tickets, not started", "", "- polish T2: b"])

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

    def test_a_waiting_ticket_whose_kind_cell_holds_no_kind_is_warned_of_by_each_printing_command_and_counts_as_untagged(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.hand_edit("rail.tsv", "T1", kind="bugfix")
        out = self.assert_each_printing_command_warns(f"T1 has kind 'bugfix', {self.NOT_A_KIND}; run ticket set T1 --kind <kind>")
        self.assertEqual(out["status"].splitlines()[3:], ["untagged: 1"])
        self.assertEqual(out["ticket list"], "T1 waiting normal [user] a")
        self.assertEqual(out["startable"].splitlines()[2], "T1 normal, 0m: unknown, no paths recorded")
        self.assertEqual(out["close --dry-run"].splitlines()[4:], ["## New tickets, not started", "", "- T1: a"])
        self.assertNotIn("bugfix", "".join(out.values()))

    def test_an_item_and_an_assigned_ticket_whose_kind_cells_hold_no_kind_are_warned_of_and_are_in_no_count_by_kind(self):
        self.fired_bug_fix()
        self.hand_edit("rail.tsv", "T1", kind="chore")
        self.hand_edit("dishes.tsv", "D1", kind="chore")
        out = self.assert_each_printing_command_warns(
            f"T1 has kind 'chore', {self.NOT_A_KIND}; {self.NOT_WHILE} T1 is assigned; correct it in rail.tsv by hand",
            f"D1 has kind 'chore', {self.NOT_A_KIND}; no command changes this cell; correct it in dishes.tsv by hand")
        self.assertEqual(len(out["status"].splitlines()), 3)
        self.assertEqual(out["ticket list"], "T1 assigned normal [user] s")
        self.assertEqual(out["close --dry-run"].splitlines()[4:], ["## In progress", "", "- D1 (T1): Fix s"])
        self.assertNotIn("chore", "".join(out.values()))

    def test_a_waiting_ticket_that_an_item_in_flight_names_is_warned_of_for_a_bad_kind_and_a_bad_goal_with_that_items_id_and_state(self):
        self.fired_bug_fix()
        self.brigade("ticket", "set", "T1", "--state", "waiting")
        self.hand_edit("rail.tsv", "T1", kind="chore", goal="G9")
        held = f"{self.NOT_WHILE} T1 is a ticket of D1, which is in-progress; correct it in rail.tsv by hand"
        self.assert_each_printing_command_warns(f"T1 has kind 'chore', {self.NOT_A_KIND}; {held}",
                                                f"T1 names goal 'G9', {self.NO_GOAL_ROW}; {held}")

    def copy_goal_row(self, ident, name):
        """Append to goals.tsv a copy of its first row with this id and this name, the way an editor would."""
        path = self.at / "goals.tsv"
        cells = path.read_text().splitlines()[1].split("\t")
        cells[0], cells[3] = ident, name
        path.write_text(path.read_text() + "\t".join(cells) + "\n")

    def test_a_goals_tsv_row_with_no_id_is_warned_of_is_not_listed_as_a_goal_and_adds_nothing_to_the_unassigned_counts(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        self.copy_goal_row("", "no id")
        out = self.assert_each_printing_command_warns("goals.tsv has a row with no id; it is not a goal; remove it from goals.tsv by hand")
        self.assertEqual(out["status"].splitlines()[3:], ["kinds: fix 2 waiting", "goals: G1 1 waiting; unassigned 1 waiting"])
        self.assertEqual(out["goal list"].splitlines()[::3],
                         ["G1 open activity view: 1 waiting, 0 in flight, 0 merged, 0 send-backs",
                          "G2 open current with upstream: 0 waiting, 0 in flight, 0 merged, 0 send-backs",
                          "unassigned: 1 waiting, 0 in flight, 0 merged, 0 send-backs"])
        self.assertEqual(out["close --dry-run"].count("T1: a"), 1)
        self.assertNotIn("no id", "".join(out.values()))

    def test_goal_set_refuses_an_empty_id_and_goal_add_gives_the_next_id_when_goals_tsv_has_a_row_with_no_id(self):
        self.two_goals()
        self.copy_goal_row("", "no id")
        before = self.store_files()
        self.assertEqual(self.brigade("goal", "set", "", "--name", "x", ok=False),
                         "brigade: warning: goals.tsv has a row with no id; it is not a goal; remove it from goals.tsv by hand\n"
                         "brigade: no goal ; run goal list --all to see every goal")
        self.assertEqual(self.store_files(), before)
        self.assertEqual(self.brigade("goal", "add", "--name", "third", "--purpose", "p", "--done", "d"), "G3")

    def test_a_goals_tsv_row_whose_id_an_earlier_row_has_is_warned_of_once_for_the_id_and_goal_list_prints_and_goal_set_changes_the_first_row(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.copy_goal_row("G1", "second")
        self.copy_goal_row("G1", "third")
        out = self.assert_each_printing_command_warns("goals.tsv has more than one row with id G1; the first is the goal and the "
                                                      "others are not read; remove the others from goals.tsv by hand")
        self.assertEqual(out["goal list"].splitlines()[::3],
                         ["G1 open activity view: 1 waiting, 0 in flight, 0 merged, 0 send-backs",
                          "G2 open current with upstream: 0 waiting, 0 in flight, 0 merged, 0 send-backs",
                          self.NO_UNASSIGNED_WORK])
        for name in ("second", "third"):
            self.assertNotIn(name, "".join(out.values()))
        self.assertEqual(self.brigade("goal", "set", "G1", "--name", "renamed"), "G1 name renamed")
        self.assertEqual([row[3] for row in self.rows_of("goals.tsv")], ["renamed", "current with upstream", "second", "third"])

    PADDED_GOAL_ROW = ("goals.tsv has a row with id ' G1 ', which has white space at an end or a carriage return in it; "
                       "it is not a goal; correct the id in goals.tsv by hand to an id no other row has, or remove the row")

    def test_a_goals_tsv_row_whose_id_has_white_space_at_an_end_is_warned_of_by_each_printing_command_and_is_not_listed_as_a_goal(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.hand_edit("goals.tsv", "G1", id=" G1 ")
        out = self.assert_each_printing_command_warns(
            self.PADDED_GOAL_ROW, f"T1 names goal 'G1', {self.NO_GOAL_ROW}; run ticket set T1 --goal G<n> or --no-goal")
        self.assertEqual(out["status"].splitlines()[3:], ["kinds: fix 1 waiting", "goals: unassigned 1 waiting"])
        self.assertEqual(out["goal list"].splitlines()[::3],
                         ["G2 open current with upstream: 0 waiting, 0 in flight, 0 merged, 0 send-backs",
                          "unassigned: 1 waiting, 0 in flight, 0 merged, 0 send-backs"])
        self.assertEqual(out["ticket list"], "T1 waiting normal fix [user] a")
        self.assertNotIn("activity view", "".join(out.values()))
        self.assertEqual(self.raw_cells("goals.tsv", " G1 ", "id"), (b" G1 ",))

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

    def test_fire_ride_along_of_a_low_ticket_whose_kind_cell_is_a_padded_feature_warns_and_leaves_the_cell_as_it_was(self):
        self.open()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "feature", "--priority", "low")
        self.hand_edit("rail.tsv", "T2", kind=" feature ")
        has = f"brigade: warning: T2 has kind ' feature ', {self.NOT_A_KIND}; "
        self.assertEqual(self.run_raw("fire", "--tickets", "T1,T2", "--station", "bug-fix", "--summary", "Do both", "--ride-along", "T2"),
                         (0, "D1\n", has + "run ticket set T2 --kind <kind>\n"))
        self.assertEqual(self.raw_cells("rail.tsv", "T2", "state", "kind", "goal"), (b"assigned", b" feature ", b""))
        code, out, err = self.run_raw("ticket", "list")
        self.assertEqual((code, err), (0, has + f"{self.NOT_WHILE} T2 is assigned; correct it in rail.tsv by hand\n"))
        self.assertEqual(out, "T1 assigned normal fix [user] a\nT2 assigned low [user] b\n")

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

    def test_ticket_add_goal_and_ticket_set_goal_refuse_an_id_whose_goals_tsv_row_has_a_space_at_each_end_warn_of_the_row_and_change_no_file(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.hand_edit("goals.tsv", "G1", id=" G1 ")
        before = self.store_files()
        warning = f"brigade: warning: {self.PADDED_GOAL_ROW}\n"
        for goal in (" G1 ", "G1"):
            with self.subTest(goal=goal):
                refusal = warning + f"brigade: no goal {goal}; run goal list --all to see every goal\n"
                self.assertEqual(self.run_raw("ticket", "add", "--summary", "Fix bug", "--kind", "fix", "--goal", goal), (1, "", refusal))
                self.assertEqual(self.run_raw("ticket", "set", "T1", "--goal", goal), (1, "", refusal))
        self.assertEqual(self.store_files(), before)

    def test_fire_of_a_ticket_whose_goal_cell_and_whose_goals_tsv_row_id_are_the_same_padded_text_warns_of_both_and_starts_an_unassigned_item(self):
        self.open()
        self.goal()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.hand_edit("goals.tsv", "G1", id=" G1 ")
        self.hand_edit("rail.tsv", "T1", goal=" G1 ")
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"),
                         (0, "D1\n", f"brigade: warning: {self.PADDED_GOAL_ROW}\n"
                                     f"brigade: warning: T1 names goal ' G1 ', {self.NO_GOAL_ROW}; "
                                     "run ticket set T1 --goal G<n> or --no-goal\n"))
        self.assertEqual(self.raw_cells("dishes.tsv", "D1", "kind", "goal"), (b"fix", b""))
        self.assertEqual(self.raw_cells("rail.tsv", "T1", "state", "kind", "goal"), (b"assigned", b"fix", b" G1 "))
        self.assertEqual(self.raw_cells("goals.tsv", " G1 ", "id"), (b" G1 ",))

    def test_ticket_add_goal_ticket_set_goal_and_fire_warn_of_a_goals_tsv_row_whose_id_an_earlier_row_has(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.copy_goal_row("G1", "second")
        warning = ("brigade: warning: goals.tsv has more than one row with id G1; the first is the goal and the others are not read; "
                   "remove the others from goals.tsv by hand\n")
        self.assertEqual(self.run_raw("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1"), (0, "T2\n", warning))
        self.assertEqual(self.run_raw("ticket", "set", "T1", "--goal", "G1"), (0, "T1 goal G1\n", warning))
        self.assertEqual(self.run_raw("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Do a"), (0, "D1\n", warning))
        self.assertEqual((self.label("dishes.tsv", "D1"), self.label("rail.tsv", "T2")), (("fix", "G1"), ("fix", "G1")))

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

    def test_goal_add_and_goal_set_warn_of_each_bad_goals_tsv_row_and_of_no_ticket(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix")
        self.hand_edit("rail.tsv", "T1", kind="chore", goal="G9")
        self.hand_edit("goals.tsv", "G2", state="finished")
        self.copy_goal_row("", "no id")
        warnings = ("brigade: warning: G2 has state 'finished', which is not open, paused, done, or dropped; it reads as open; "
                    "run goal set G2 --state <state>\n"
                    "brigade: warning: goals.tsv has a row with no id; it is not a goal; remove it from goals.tsv by hand\n")
        self.assertEqual(self.run_raw("goal", "add", "--name", "third", "--purpose", "p", "--done", "d"), (0, "G3\n", warnings))
        self.assertEqual(self.run_raw("goal", "set", "G1", "--name", "renamed"), (0, "G1 name renamed\n", warnings))
        self.assertEqual(self.run_raw("goal", "set", "G1", "--state", "paused"), (0, "G1 paused\n", warnings))
        self.assertEqual(self.raw_cells("rail.tsv", "T1", "kind", "goal"), (b"chore", b"G9"))

    def test_goal_set_state_done_warns_of_a_waiting_ticket_whose_goal_cell_is_that_goals_id_padded(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.hand_edit("rail.tsv", "T1", goal=" G1 ")
        self.assertEqual(self.run_raw("goal", "set", "G2", "--state", "paused"), (0, "G2 paused\n", ""))
        self.assertEqual(self.run_raw("goal", "set", "G1", "--state", "done", "--evidence", "shipped"),
                         (0, "G1 done\n", f"brigade: warning: T1 names goal ' G1 ', {self.NO_GOAL_ROW}; "
                                          "run ticket set T1 --goal G<n> or --no-goal\n"))
        self.assertEqual(self.raw_cells("rail.tsv", "T1", "state", "goal"), (b"waiting", b" G1 "))

    def test_a_ticket_and_an_item_that_name_a_goal_with_no_row_are_warned_of_and_count_as_unassigned(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        self.hand_edit("rail.tsv", "T1", goal="")
        self.hand_edit("rail.tsv", "T2", goal="G9")
        self.hand_edit("dishes.tsv", "D1", goal="G7")
        out = self.assert_each_printing_command_warns(
            f"T2 names goal 'G9', {self.NO_GOAL_ROW}; run ticket set T2 --goal G<n> or --no-goal",
            f"D1 names goal 'G7', {self.NO_GOAL_ROW}; no command changes this cell; correct it in dishes.tsv by hand")
        self.assertEqual(out["status"].splitlines()[3:],
                         ["kinds: fix 1 waiting, 1 in flight", "goals: unassigned 1 waiting, 1 in flight"])
        self.assertEqual(out["goal list"].splitlines()[0], "G1 open activity view: 0 waiting, 0 in flight, 0 merged, 0 send-backs")
        self.assertEqual(out["goal list"].splitlines()[-1], "unassigned: 1 waiting, 1 in flight, 0 merged, 0 send-backs")
        self.assertEqual(out["ticket list"], "T1 assigned normal fix [user] a\nT2 waiting normal fix [user] b")
        self.assertEqual(out["startable"].splitlines()[2], "T2 normal fix, 0m: unknown, no paths recorded")
        self.assertEqual(out["close --dry-run"].splitlines()[4:],
                         ["## In progress", "", "- fix D1 (T1): Fix a", "", "## New tickets, not started", "", "- fix T2: b"])
        for value in ("G9", "G7"):
            self.assertNotIn(value, "".join(out.values()))

    def test_with_goals_tsv_deleted_each_goal_cell_is_warned_of_its_row_counts_as_unassigned_and_no_command_writes_the_table(self):
        self.two_goals()
        self.brigade("ticket", "add", "--summary", "a", "--kind", "fix", "--goal", "G1")
        self.brigade("ticket", "add", "--summary", "b", "--kind", "fix", "--goal", "G1")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix a")
        (self.at / "goals.tsv").unlink()
        out = self.assert_each_printing_command_warns(
            f"T1 names goal 'G1', {self.NO_GOAL_ROW}; {self.NOT_WHILE} T1 is assigned; correct it in rail.tsv by hand",
            f"T2 names goal 'G1', {self.NO_GOAL_ROW}; run ticket set T2 --goal G<n> or --no-goal",
            f"D1 names goal 'G1', {self.NO_GOAL_ROW}; no command changes this cell; correct it in dishes.tsv by hand")
        self.assertEqual(out["status"].splitlines()[3:], ["kinds: fix 1 waiting, 1 in flight"])
        self.assertEqual(out["goal list"], "no open or paused goals\nunassigned: 1 waiting, 1 in flight, 0 merged, 0 send-backs")
        self.assertEqual(out["ticket list"], "T1 assigned normal fix [user] a\nT2 waiting normal fix [user] b")
        self.assertEqual(out["close --dry-run"].splitlines()[4:],
                         ["## In progress", "", "- fix D1 (T1): Fix a", "", "## New tickets, not started", "", "- fix T2: b"])
        self.assertFalse((self.at / "goals.tsv").exists())


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

    def test_ticket_list_startable_watch_86_list_close_walk_pass_check_and_contract_print_for_a_store_from_before_kinds_what_the_tool_printed_before_kinds_and_change_no_table(self):
        self.store_from_before_kinds()
        before = self.tables()
        waiting = ["T8 waiting normal [user] Importer crashes",
                   "T9 waiting normal [user] Tune the pool",
                   "T10 waiting low [report] Tidy the guide perf/reports/D7.md#1",
                   "T11 waiting urgent [user] Pick a database",
                   "T12 waiting low [user] Trim the pool"]
        self.assertEqual(self.brigade("ticket", "list").splitlines(), [
            "T1 assigned normal [user] Cold start is slow",
            "T2 assigned urgent [github] Splash hangs https://github.com/o/r/issues/2",
            "T3 assigned normal [user] Cache misses",
            "T4 assigned normal [user] Login loops",
            "T5 assigned normal [user] Export stalls",
            "T6 assigned low [user] Footer overlaps",
            "T7 done normal [user] Dates are wrong",
            *waiting,
            "T13 dropped normal [user] Old idea"])
        self.assertEqual(self.brigade("ticket", "list", "--state", "waiting").splitlines(), waiting)
        self.assertEqual(self.brigade("startable").splitlines(), [
            self.OLD_WORKERS,
            "waiting tickets: 5, blocked: 1, unknown: 1, decisions: 1, below auto-start: 2",
            "T11 urgent, 0m: decision for the owner",
            "T8 normal, 0m: unknown, no paths recorded",
            "T9 normal, 0m: blocked, waiting for an idle worker; a.txt",
            "T10 low, 0m: below auto-start; docs/guide.md",
            "T12 low, 0m: below auto-start; a.txt"])
        self.assertEqual(self.brigade("watch").splitlines(), [
            self.OLD_WORKERS, "D1: running 0m of 60m (thread w1)", "D2: in review", "D3: passed, not submitted", "D4: sent back",
            "D5: parked", "D6: queued, but no entry at s6"])
        self.assertEqual(self.brigade("86", "list"), "Q1: Drop Windows 7 support? Options: yes, no. Default: no.")
        code, report, err = self.close_run("--dry-run")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(report.splitlines(), [
            "# Perf report", "",
            f"Since opening. {self.OLD_COUNTS}.", "",
            "## Merged", "", "- D7 (T7): Fix the dates https://github.com/o/r/pull/7", "",
            "## Passed review, not submitted", "", "- D3 (T3): Fix the cache", "",
            "## Waiting to land", "", "- D6 (T6): Fix the footer", "",
            "## Sent back after review", "", "- D4 (T4): Fix the login", "",
            "## Blocked", "", "- D5 (T5): Rework the export", "",
            "## In progress", "", "- D1 (T1): Fix the cold start", "- D2 (T2): Fix the splash https://github.com/o/r/pull/2", "",
            "## New tickets, not started", "",
            "- T8: Importer crashes", "- T9: Tune the pool", "- T10: Tidy the guide", "- T11: Pick a database", "- T12: Trim the pool", "",
            "## Dropped", "", "- T13: Old idea", "- D8 (T8): Split the importer", "",
            "## Decisions for you", "", "- Q1: Drop Windows 7 support? Options: yes, no. Default if no answer: no."])
        self.assertEqual(self.brigade("walk").splitlines(), [
            f"{_shown_root(self.project)}: local mode onto refs/landing/lane. empty.",
            f"  Perf (reports milestones): {self.OLD_COUNTS}",
            "    thread not recorded",
            "    Q1: Drop Windows 7 support?"])
        self.assertEqual(self.brigade("pass", "check", "D3", "--sha", "s3"), f"D3 at s3 passed review by {CODEX}")
        self.assertEqual(self.brigade("pass", "check", "D2", "--sha", "s2", ok=False), "brigade: D2 has no review verdict for s2")
        self.assertEqual(self.brigade("contract", "D1", ok=False), "brigade: D1 has no contract on record; run brief with --contract")
        self.assertEqual(self.tables(), before)
        self.assertFalse((self.at / "goals.tsv").exists())

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

    def test_a_ticket_added_to_a_store_from_before_kinds_leaves_each_older_row_of_the_ticket_table_byte_identical(self):
        self.store_from_before_kinds()
        before = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(self.brigade("ticket", "add", "--summary", "New", "--kind", "fix"), "T14")
        after = (self.at / "rail.tsv").read_bytes()
        self.assertEqual(after[:len(before)], before)
        self.assertEqual(after[len(before):].decode().rstrip("\n").split("\t")[5:], ["", "New", "", "", "", "fix", ""])
        self.assertEqual(self.brigade("ticket", "list").splitlines()[-1], "T14 waiting normal fix [user] New")

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

    def test_next_accepts_commands(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        for number in range(3):
            self.brigade("pass", "record", "D1", "--sha", f"s{number}", "--verdict", "send-back",
                         "--author", CLAUDE, "--verifier", CODEX, "--late")
        inbox = self.at / "inbox"
        inbox.mkdir()
        (inbox / "hand.json").write_text(json.dumps(
            {"handoff": "app/other/T1", "summary": "Fix a", "source": "user", "ref": ""}))
        sheet = self.next_sheet("report-back", "D1")
        self.assertEqual(sheet, "\n".join([
            "wake: report-back D1",
            "reporting: milestones",
            "run inbox take",
            "run dish D1 --reported",
            "run 86 add --dish D1 --round-budget",
            "run close",
            "reply: yes (decision: D1)",
        ]))
        self.execute_sheet(sheet)
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        sheet = self.next_sheet("merge", "D1")
        self.assertEqual(sheet, "\n".join([
            "wake: merge D1",
            "reporting: milestones",
            "run inbox take",
            "run dish D1 --state merged",
            "see Finish with the old worker for thread none, branch none, and pull request none for D1. "
            "If its run is still open, interrupt it and hang the attempt, then archive it. "
            "Remove the worktree and delete the branch after that. Do not claim they are already gone.",
            "if the queue is paused and this thread cannot fix it and this is not a bounce handed to a fresh worker and --failure stuck was not passed: "
            "run next --failure stuck",
            "reply: yes (merged: D1)",
        ]))
        self.execute_sheet(sheet)

    def test_next_runs_each_conditional_action(self):
        self.fired_bug_fix()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        liveness = self.next_sheet("liveness")
        self.assertEqual(self.execute_conditionals(liveness), [
            "next merge D1",
            "next --failure stuck",
            "set --schedule liveness=",
        ])
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        checks = self.next_sheet("checks", "D1", "--result", "fail")
        self.assertEqual(self.execute_conditionals(checks), [
            "next merge D1",
            "next checks D1 --result fail --failure stuck",
        ])
        self.fresh()
        review = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.execute_conditionals(review), ["dish D1 --state in-progress"])
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("pass", "record", "D1", "--sha", "s2", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        owed = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertIn("if D1 reaches the round budget and no decision is open: run 86 add --dish D1 --round-budget", owed)
        self.brigade("pass", "record", "D1", "--sha", "s3", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.execute_commands([["86", "add", "--dish", "D1", "--round-budget"]]),
                         ["86 add --dish D1 --round-budget"])
        shutil.rmtree(self.at)
        self.open()
        inbox = self.at / "inbox"
        inbox.mkdir()
        (inbox / "A1.line").write_text("please fix the gate\n")
        admin = self.next_sheet("admin", "A1")
        self.assertEqual(self.execute_conditionals(admin), ["inbox done A1"])
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        landed = self.next_sheet()
        self.assertIn("if the landing queue has an entry for D1: run land.py status --holder perf/D1 --sha abc", landed)
        self.init_landing()
        self.assertEqual(self.execute_conditionals(landed), ["land.py status --holder perf/D1 --sha abc"])

    def test_next_omits_the_fix_round_at_the_round_budget(self):
        self.fresh()
        below = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertIn("if the old worker's wait for D1 is terminal and D1 may take a fix round: run dish D1 --state in-progress", below)
        self.assertNotIn("round-budget", below)
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.execute_conditionals(below), ["dish D1 --state in-progress"])
        self.fresh()
        for number in range(3):
            self.brigade("pass", "record", "D1", "--sha", f"s{number}", "--verdict", "send-back",
                         "--author", CLAUDE, "--verifier", CODEX)
        owed = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertIn("\nrun 86 add --dish D1 --round-budget\n", f"\n{owed}\n")
        self.assertNotIn("run dish D1 --state in-progress", owed)
        self.execute_sheet(owed)
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("pass", "record", "D1", "--sha", "s2", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        crossing = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertIn("if D1 reaches the round budget and no decision is open: run 86 add --dish D1 --round-budget", crossing)
        self.assertNotIn("run dish D1 --state in-progress", crossing)
        self.brigade("pass", "record", "D1", "--sha", "s3", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.execute_conditionals(crossing), ["86 add --dish D1 --round-budget"])

    def test_next_restarts_a_pass_with_edits_review_after_two_send_backs(self):
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("pass", "record", "D1", "--sha", "s2", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        sheet = self.next_sheet("review", "D1", "--verdict", "pass-with-edits")
        self.assertIn(
            "if the old worker's wait for D1 is terminal and D1 may take a fix round: run dish D1 --state in-progress",
            sheet)
        self.assertNotIn("round-budget", sheet)
        self.assertEqual(sheet.splitlines()[-1], "reply: yes (sent-back: D1)")
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"), name="D1-review-3.md")
        self.assertEqual(self.brigade("pass", "record", "D1", "--sha", "s3", "--verdict", "pass-with-edits",
                                      "--author", CLAUDE, "--verifier", CODEX, "--edits", "D1-review-3.md"),
                         "D1 sent-back")
        self.assertEqual(self.execute_conditionals(sheet), ["dish D1 --state in-progress"])
        self.fresh()
        for number in range(3):
            self.brigade("pass", "record", "D1", "--sha", f"s{number}", "--verdict", "send-back",
                         "--author", CLAUDE, "--verifier", CODEX)
        at_budget = self.next_sheet("review", "D1", "--verdict", "pass-with-edits")
        self.assertNotIn("run dish D1 --state in-progress", at_budget)
        self.assertIn("run 86 add --dish D1 --round-budget", at_budget)

    def test_next_facts(self):
        self.fired_bug_fix()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        pause = ("if the queue is paused and this thread cannot fix it and this is not a bounce handed to a fresh worker and --failure stuck was not passed: "
                 "run next --failure stuck")
        follow = "if land or watch reports D1 landed: run next merge D1"
        unspecified = self.next_sheet()
        self.assertEqual(unspecified, "\n".join([
            "wake: unspecified", "reporting: milestones", "see Run land for D1.", follow, pause,
            "reply: no (routine: unspecified)"]))
        self.assertNotIn("run dish D1 --state merged", unspecified)
        checks = self.next_sheet("checks", "D1", "--result", "pass")
        self.assertEqual(checks, "\n".join([
            "wake: checks D1", "reporting: milestones", "see Run land for holder perf/D1.", follow, pause,
            "reply: no (routine: pull request)"]))
        self.assertNotIn("run dish D1 --state merged", checks)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "other")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Other")
        review = self.next_sheet("review", "D2", "--verdict", "send-back")
        self.assertIn("see Record pass record for D2 with verdict send-back. No sha is recorded.", review)
        self.assertNotIn("sha abc", review)
        self.assertNotIn("D2 is send-back", review)
        self.assertNotIn("D2 sent-back", review)
        self.assertIn("if the old worker's wait for D2 is terminal and D2 may take a fix round: run dish D2 --state in-progress", review)
        self.assertNotIn("run pass", review)
        self.assertFalse(any(line.startswith("run dish D2") for line in review.splitlines()))
        (self.at / "inbox").mkdir()
        (self.at / "inbox" / "bad.json").write_text("{")
        bad = self.next_sheet()
        self.assertIn("see inbox/bad.json is not valid JSON", bad)
        self.assertNotIn("run inbox take", bad)
        self.assertTrue(any(line.startswith("if ") for line in bad.splitlines()))

    def test_next_reply(self):
        self.open()
        pause = ("if the queue is paused and this thread cannot fix it and this is not a bounce handed to a fresh worker and --failure stuck was not passed: "
                 "run next --failure stuck")
        self.assertEqual(self.next_sheet("morning"), "\n".join([
            "wake: morning", "reporting: milestones",
            "see No intake source is recorded. File a user request with ticket add when the message asks for work.",
            "see Morning service is not a report.",
            "reply: no (routine: morning service)"]))
        self.assertEqual(self.next_sheet("evening"), "\n".join([
            "wake: evening", "reporting: milestones",
            "see Write the evening report for this reporting level. At digest, plain sentences and the closeout path. "
            "Otherwise at most three sentences and then the close output.",
            "run close",
            "reply: yes (evening: report)"]))
        self.assertEqual(self.next_sheet("user"), "\n".join([
            "wake: user", "reporting: milestones",
            "see Answer the user first.",
            "see File the work the message asks for with ticket add.",
            "run close",
            "reply: yes (user: message)"]))
        self.assertEqual(self.next_sheet("user", "--question").splitlines()[-1], "reply: yes (user: question)")
        self.assertEqual(self.next_sheet("liveness"), "\n".join([
            "wake: liveness", "reporting: milestones", "see watch", pause,
            "if watch printed no work in progress: run set --schedule liveness=",
            "reply: no (routine: liveness)"]))
        self.brigade("set", "--reporting", "every-turn")
        every = self.next_sheet()
        self.assertEqual(every.splitlines()[-1], "reply: yes (every-turn: wake)")
        self.assertNotIn("run close", every)
        self.assertEqual(self.next_sheet("--unsent", "misrouted").splitlines()[-1], "reply: yes (every-turn: wake)")
        self.brigade("set", "--reporting", "milestones")
        self.assertEqual(self.next_sheet("--unsent", "misrouted").splitlines()[-1], "reply: no (routine: unspecified)")
        report = self.next_sheet("--unsent", "report")
        self.assertEqual(report.splitlines()[-1], "reply: yes (report: unsent report)")
        self.assertNotIn("run close", report)
        self.brigade("set", "--intake", "github")
        intake = self.next_sheet("intake")
        self.assertIn("see Intake sources are github. File tickets from those sources with ticket add.", intake)
        self.assertEqual(intake.splitlines()[-1], "reply: no (routine: intake)")
        self.assertNotIn("run close", intake)
        self.fresh()
        self.park_item()
        self.brigade("close")
        for level, last in (("milestones", "reply: no (routine: unspecified)"),
                            ("digest", "reply: no (routine: unspecified)"),
                            ("every-turn", "reply: yes (every-turn: wake)")):
            self.brigade("set", "--reporting", level)
            self.assertEqual(self.next_sheet().splitlines()[-1], last)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.next_sheet("review", "D1", "--verdict", "send-back").splitlines()[-1], "reply: no (sent-back: D1)")
        self.brigade("pass", "record", "D1", "--sha", "s2", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        crossed = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertEqual(crossed.splitlines()[-1], "reply: yes (decision: D1)")
        self.assertIn("run close --to-file", crossed)
        blocked = self.next_sheet("review", "D1", "--verdict", "blocked")
        self.assertEqual(blocked.splitlines()[-1], "reply: yes (decision: D1)")
        self.assertIn("run close --to-file", blocked)
        self.assertIn("if only the user can unblock D1: ask with 86 add --dish D1 --question --options --default", blocked)
        self.assertNotIn("if only the user can unblock D1: run 86 add --dish D1", blocked)
        fixable = self.next_sheet("review", "D1", "--verdict", "blocked", "--failure", "fixable")
        self.assertEqual(fixable.splitlines()[-1], "reply: no (failure: fixable)")
        self.assertNotIn("run close", fixable)
        self.assertNotIn("86 add --dish D1", fixable)
        self.assertEqual(self.next_sheet("checks", "D1", "--result", "fail").splitlines()[-1], "reply: no (failure: fail)")
        stuck = self.next_sheet("checks", "D1", "--result", "fail", "--failure", "stuck")
        self.assertEqual(stuck.splitlines()[-1], "reply: yes (stuck: stuck)")
        self.assertIn("run close --to-file", stuck)
        self.assertEqual(self.next_sheet("--unsent", "blocked").splitlines()[-1], "reply: no (failure: unsent blocked)")
        self.assertEqual(self.next_sheet("--unsent", "blocked", "--failure", "stuck").splitlines()[-1], "reply: yes (stuck: stuck)")
        digest_report = self.next_sheet("--unsent", "report")
        self.assertEqual(digest_report.splitlines()[-1], "reply: yes (report: unsent report)")
        self.assertIn("run close --to-file", digest_report)
        refused = self.next_sheet("merge", "D1")
        self.assertEqual(refused.splitlines()[-1], "reply: no (routine: merge)")
        self.assertNotIn("run dish D1 --state merged", refused)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        drained = self.next_sheet("merge", "D1")
        self.assertEqual(drained.splitlines()[-1], "reply: yes (drained: batch)")
        self.assertIn("run dish D1 --state merged", drained)
        self.assertIn("run close --to-file", drained)
        for line in drained.splitlines():
            if line.startswith("run "):
                self.brigade(*shlex.split(line[4:]))
        self.assertEqual(self.next_sheet().splitlines()[-1], "reply: no (routine: unspecified)")
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        merged = self.next_sheet("merge", "D1")
        self.assertEqual(merged.splitlines()[-1], "reply: yes (merged: D1)")
        self.assertNotIn("run close", merged)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "second")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        held = self.next_sheet("merge", "D1")
        self.assertEqual(held.splitlines()[-1], "reply: no (merged: D1)")
        self.assertNotIn("run close", held)
        self.fresh()
        same = self.run_raw("next", "--failure", "stuck", "merge", "D1")
        other = self.run_raw("next", "merge", "D1", "--failure", "stuck")
        self.assertEqual(same, other)
        self.assertEqual(same[0], 0)
        self.assertEqual(same[1].splitlines()[-1], "reply: yes (stuck: stuck)")
        self.fresh()
        self.brigade("set", "--reports-to", "admin-thread")
        evening = self.next_sheet("evening")
        self.assertEqual(evening.splitlines()[-1], "reply: no (reports to the executive admin: evening report)")
        self.assertIn("run close --to-file", evening)
        lines = evening.splitlines()
        report_line = "see t3_thread_send admin-thread mode queue: report perf: the path close --to-file prints"
        self.assertLess(lines.index("run close --to-file"), lines.index(report_line))
        user = self.next_sheet("user")
        self.assertEqual(user.splitlines()[-1], "reply: yes (user: message)")
        self.assertNotIn("run close", user)
        self.assertEqual(self.next_sheet("user", "--question").splitlines()[-1], "reply: yes (user: question)")
        failed = self.next_sheet("--unsent", "failed")
        self.assertEqual(failed.splitlines()[-1], "reply: yes (failed: unsent failed)")
        self.open_admin()
        published = self.admin("request", "--to", "perf", "from-user perf: please fix the gate")
        ident = published.split()[0]
        routed = self.next_sheet("admin", ident)
        self.assertEqual(routed.splitlines()[-1], "reply: no (reports to the executive admin: admin request)")
        self.assertIn(f"see t3_thread_send admin-thread mode queue: reply perf: sent after the answer", routed)
        self.brigade("set", "--reports-to", "")
        direct = self.next_sheet("admin", ident)
        self.assertEqual(direct.splitlines()[-1], "reply: yes (user: message)")
        self.assertIn("run close\n", direct + "\n")
        self.assertNotIn("run close --to-file", direct)

    def assert_one_close_file(self, sheet):
        self.assertEqual([line for line in sheet.splitlines() if line == "run close --to-file"], ["run close --to-file"])

    def test_next_replies_yes_when_a_later_occasion_qualifies(self):
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        stuck = self.next_sheet("review", "D1", "--verdict", "send-back", "--failure", "stuck")
        self.assertEqual(stuck.splitlines()[-1], "reply: yes (stuck: stuck)")
        self.assert_one_close_file(stuck)
        unsent = self.next_sheet("review", "D1", "--verdict", "send-back", "--unsent", "report")
        self.assertEqual(unsent.splitlines()[-1], "reply: yes (report: unsent report)")
        self.assert_one_close_file(unsent)
        failed = self.next_sheet("checks", "D1", "--result", "fail", "--unsent", "failed")
        self.assertEqual(failed.splitlines()[-1], "reply: yes (failed: unsent failed)")
        self.assert_one_close_file(failed)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "second")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        merged = self.next_sheet("merge", "D1", "--failure", "stuck")
        self.assertEqual(merged.splitlines()[-1], "reply: yes (stuck: stuck)")
        self.assert_one_close_file(merged)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("set", "--reports-to", "admin-thread")
        quiet = self.next_sheet("review", "D1", "--verdict", "send-back", "--failure", "stuck")
        self.assertEqual(quiet.splitlines()[-1], "reply: no (reports to the executive admin: send-back)")
        routed = self.next_sheet("review", "D1", "--verdict", "send-back", "--unsent", "report")
        self.assertEqual(routed.splitlines()[-1], "reply: yes (report: unsent report)")
        self.assert_one_close_file(routed)
        routed_fail = self.next_sheet("checks", "D1", "--result", "fail", "--unsent", "failed")
        self.assertEqual(routed_fail.splitlines()[-1], "reply: yes (failed: unsent failed)")
        self.assert_one_close_file(routed_fail)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("set", "--reports-to", "admin-thread")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "second")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        admin_merge = self.next_sheet("merge", "D1", "--failure", "stuck")
        self.assertEqual(admin_merge.splitlines()[-1], "reply: no (reports to the executive admin: merge)")

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

    def test_next_asks_about_a_blocked_ticket_only_while_it_waits(self):
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", "--workers", "1")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "two")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "One")
        refused = self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Two", ok=False)
        self.assertEqual(refused, "brigade: nothing fired: 1 of 1 workers running")
        self.brigade("set", "--reports-to", "admin-thread")
        young = self.next_sheet("liveness")
        self.assertNotIn("watch still shows T2 blocked", young)
        stamp = self.backdate_blocked("T2", 2)
        waiting = self.next_sheet("liveness")
        self.assertIn(
            "if watch still shows T2 blocked: t3_thread_send admin-thread mode queue "
            f"clientRequestId blocked:bridge-kit/perf/T2: blocked perf T2: waiting since {stamp}. "
            "Lease ids are not in the store.",
            waiting)
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Two")
        self.assertIn("T2 assigned", self.brigade("ticket", "list"))
        assigned = self.next_sheet("liveness")
        self.assertNotIn("watch still shows T2 blocked", assigned)
        self.assertNotIn("clientRequestId blocked:", assigned)

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

    def test_next_admin_lines_match_the_events_table(self):
        text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        rows = []
        started = False
        for line in text.splitlines():
            if line.startswith("| Line |"):
                started = True
                continue
            if not started:
                continue
            if not line.startswith("|"):
                break
            if set(line.replace("|", "").replace("-", "").strip()) == set():
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            kind = cells[0].replace("`", "").split()[0]
            store = " ".join(cells[2].replace("`", "").split())
            rows.append((kind, store))
        recorded = [(kind, store) for kind, store, _only in runpy.run_path(str(SCRIPT))["ADMIN_LINES"]]
        self.assertEqual(recorded, rows)


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
