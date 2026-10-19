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

