# Contributing

Issues and pull requests are welcome.

## Where changes go

- **A skill behaves wrong in T3.** Change its port in `t3/overrides/`, or `t3/runtime.md` if the problem is how pstack maps onto T3's tools. Never edit `skills/` or `vendor/` by hand. `skills/` is generated, and `vendor/pstack` must stay byte-identical to upstream.
- **The engineering content itself (a playbook step, a principle, a rubric).** That belongs upstream in [cursor/plugins](https://github.com/cursor/plugins/tree/main/pstack). pstack-t3 picks it up on the next sync.
- **Installer or roles resolver.** `scripts/install.py` and `t3/scripts/roles.py`, with a test in `tests/`.

[AGENTS.md](AGENTS.md) has the porting rules. Agents working on this repo should read it first.

## For an outside contributor

The full suite takes about 16 minutes in one process. A run at `-j 8` took 842 s for 3441 tests, and the four parts CI runs took 338 s to 635 s each with `PSTACK_TEST_REAL_PROCESS=1`. While you work, run the module you changed, then `python3 -m unittest tests.test_pstack_t3`, `python3 scripts/build.py`, and `python3 scripts/check.py`.

An outside pull request is ported into a reviewed branch. That branch credits the author with a `Co-authored-by` trailer. The original pull request is then closed rather than merged.

## Before you open a PR

```bash
pip install pyyaml
python3 scripts/build.py
PSTACK_TEST_REAL_PROCESS=1 python3 scripts/run_tests.py -j 8
```

`scripts/run_tests.py` runs the tests under `tests/` in separate processes and prints one report. It treats the tests as trusted code, and `python3 scripts/run_tests.py --help` says what a run that exits 0 guarantees. `-j N` sets the largest number of worker processes that run at once. `python3 -m unittest discover -s tests` runs the same tests in one process.

CI sets `PSTACK_TEST_REAL_PROCESS=1` for every test part on pull requests and pushes to main. `tests.in_process.invoke` reads this value from the process environment and starts a real interpreter when it is `1`. Run `PSTACK_TEST_REAL_PROCESS=1 python3 scripts/run_tests.py` to use this mode locally.

Commit the regenerated `skills/` with your change. For a user-facing change, add one fragment under `changes/`. The file name is the branch name with `%` encoded as `%25` and `/` encoded as `%2F`, then `.md`. Encode `%` before `/`. Branch `pstack-t3/d50` writes `changes/pstack-t3%2Fd50.md`. The file holds markdown bullets only. A bullet starts with `- `. Indent a continuation line under that bullet. A branch reused before the release adds its next bullet to the same file. CI fails if `skills/` does not match what the build produces.

Edit `README.md` or `docs/guide.md` only when the ticket is about those files, or when the change removes or renames something they name. Otherwise list the doc edit under follow-ups. Those notes become one docs change.

If you changed a skill's behavior, run it in a real T3 thread and say in the PR which provider led and what it did.

## Syncing upstream

Before you sync, see what upstream changed. This reads upstream in a temporary clone and writes nothing in the checkout.

```bash
python3 scripts/sync_upstream.py --check
```

It prints `up to date (<commit>)` when upstream is at the pinned commit. Otherwise it prints the new commit and one row for each changed, added, or removed path under `pstack/`. Each row ends with what pstack-t3 does with that path, which is `overridden by <file under t3/>`, `dropped by t3/removed.txt`, `ships unchanged`, or `not shipped`. Add `--json` for one JSON object a script can read, `--ref` to read a branch, tag, or commit, and `--repository` to read another upstream. A git or network failure exits 1 with git's error.

Then sync.

```bash
python3 scripts/sync_upstream.py
```

The build then lists each override whose upstream file changed. Re-port each one against the new upstream text, then run `python3 scripts/build.py --update-lock`.

To merge first, run `python3 scripts/sync_upstream.py --merge`. For each override whose upstream file changed it either writes a clean three-way merge or leaves the override as it is and names it. Add `--dry-run` to write nothing in the checkout. Add `--conflicts <dir>` to also write git's conflict-marked merge of each override whose merge conflicts under `<dir>`, a path outside the checkout that does not exist yet. Review each merged override before `python3 scripts/build.py --update-lock`.

## Releasing

1. Print the section. This changes nothing.

```bash
python3 scripts/release.py --dry-run X.Y.Z
```

2. Read the section. It is a `## X.Y.Z (YYYY-MM-DD)` heading with today's date, then one group of bullets per fragment. `--date YYYY-MM-DD` sets another date.
3. Run the stable check. On the latest stable T3 Code release, run `setup-pstack` and one small real item. Record the result in the release notes file as `Stable check: T3 Code <version> passed`, with the version you ran. A real run refuses without that record. A real run also refuses unless the file passed with --notes-file has ## New, ## Fixed, and ## Before you upgrade, each with a non-empty line under it (use None. when there is nothing to report).

4. Run it for real. The script writes that section above the latest heading in `CHANGELOG.md` and deletes the fragments. It never stages, commits, tags, pushes, or calls `gh`. It prints steps 5 and 6 when it finishes.

```bash
python3 scripts/release.py X.Y.Z --notes-file <release notes file>
```

5. Commit `CHANGELOG.md` and the deleted fragments in one commit. Tag `vX.Y.Z` on the commit that lands on main, and push the tag.
6. Run `gh release create vX.Y.Z --notes-file <that changelog section>`.

The script orders the fragments by the commit that added each one, and it orders the fragments that one commit added by name. It reads the output of this command.

```bash
git log --reverse --diff-filter=A --no-renames --no-show-signature -z --format=%x01 --name-only -- changes/
```

The output is NUL-separated. A token of one `\x01` byte starts a commit, and the tokens after it are that commit's added paths under `changes/`, each with one leading newline the script removes. The script records only a path with one `/`, so `changes/<name>.md` and not a path in a subdirectory. When a commit adds more than one fragment, the script orders them by name.

To check the order by hand, read this log. It drops `-z`, the marker format, `--no-renames`, and `--no-show-signature` from the command above. Without `--no-renames`, git prints a renamed path as a rename, and `--diff-filter=A` leaves that line out, so this form misses a fragment that a commit renamed.

```bash
git log --reverse --diff-filter=A --format= --name-only -- changes/
```

Skip blank lines. Skip a path that is no longer a file. When the log lists a path more than once, the later line is the one that orders it.

The script exits 1 and changes nothing when one of these holds:

- `CHANGELOG.md` or `changes/` has uncommitted changes.
- `CHANGELOG.md` is missing or is a symlink.
- The version is not `X.Y.Z`, three dot-separated numbers with no leading zero.
- The `--date` value is not a real `YYYY-MM-DD` date.
- `CHANGELOG.md` already has a `## X.Y.Z` heading, or the version is not above the latest heading.
- `docs/changelog/` already has a heading for the version.
- `changes/` holds no fragments, or holds an entry that is not a `.md` file.
- A fragment's first line starts with `- ` and its remaining text after `str.strip()` starts with `kind` but is not `kind feature`, `kind fix`, `kind polish`, `kind upkeep`, or `kind study`.
- A fragment holds no bullet, not counting a first-line kind marker. A kind marker starts with `- ` and its remaining text after `str.strip()` is one of the five kind texts above.
- A fragment holds a line that is not a bullet or a continuation line.
- When `CHANGELOG.md` or `docs/changelog/` already holds a version, the version is not the one the fragment kinds give, which bumps the highest version's minor number and resets the patch number to zero when any fragment's first line starts with `- ` and its remaining text after `str.strip()` is `kind feature`, and otherwise bumps the highest version's patch number.
- `git log` emits a NUL-separated token that is not the commit marker and, after removing at most one leading newline, is nonempty and either occurs before the first commit marker or does not start with `changes/`. Empty tokens and tokens containing only one newline are skipped. Report the printed line and the output of `git --version`.
- The clone is shallow, or no commit added a fragment that sits in `changes/`.
- A real run has no `--notes-file`.
- A real run cannot read the notes file as UTF-8 or finds no `Stable check: T3 Code <version> passed` record in it.
- In a real run, the first `Stable check: T3 Code <version>` record in the notes file says `failed`.
- In a real run, the notes file lacks any of `## New`, `## Fixed`, or `## Before you upgrade` as an exact line after removing trailing spaces, or the first matching line for any heading has no body line containing a character other than space or tab. Lines are read with `str.splitlines()`. The body ends at the next line starting with `#` or `##` followed by whitespace or the end of the line.

The script writes `CHANGELOG.md` through a temporary file and then deletes the fragments. It holds `SIGINT`, `SIGTERM`, and `SIGHUP` for that whole step. When a write or a delete fails, or one of those signals arrives, it writes `CHANGELOG.md` back and re-creates each deleted fragment from the bytes it read first. It then checks every file against those bytes. It prints `nothing changed` and exits 1 only when the check passes. Otherwise it names each file that differs and prints the command below. A kill that cannot be caught, or power loss, can leave a partial run. The command below restores that too.

To undo a finished or partial run before you commit it, run this. It works whether or not you ran `git add`.

```bash
git restore --staged --worktree CHANGELOG.md changes/
```

Cut a release after each upstream sync and any user-facing fix.
