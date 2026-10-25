# Store configuration

Read this file before you run `set` on a store key it names.

## affectedTests

`affectedTests` sets the picker layout `affected` reads. Set it with `set --affected-tests`. The flag takes inline JSON, a JSON file, or `-` for stdin. For example `set --affected-tests config/affected.json` records the file contents.

| Key | Meaning |
| --- | --- |
| `always` | Required dotted module every modules result includes. |
| `workflowModule` | Optional dotted module one `.github/workflows/` path maps to. |
| `entryPoints` | Required list of repository paths the helper modules load or run. |
| `helpers` | Optional list of repository paths. |
| `suite` | Optional whole-suite command. |
| `python` | Optional absolute path of the second interpreter. |
| `importRoots` | Optional list of repository paths tried as prefixes for absolute imports. |
| `env` | Optional object of shell variable names to string values printed in front of the affected-module and whole-suite commands. |

The second interpreter file name is `python<major>` or `python<major>.<minor>`. The paragraph names it by that version. A layout that sets `python` needs a `suite` that starts with `python3`. That interpreter replaces the leading `python3`. `importRoots` is tried in candidate order after the loader directory and the root with every match kept. Relative imports never search it. Roots are normalized and deduped in order and never hold the root itself. An absent `importRoots` means the directories of `entryPoints` and `helpers`.

`env` is an optional object of shell variable names to string values. A name matches `[A-Za-z_][A-Za-z0-9_]*`. A value matches `[A-Za-z0-9_.,:/+=@%-]+`, so it is never empty. `set` refuses an `env` that is not an object, a name that does not match the name pattern, and a value that does not match the value pattern. An absent `env` means no assignment. The paragraph prints `env NAME=value ...` in front of the affected-module command and the whole-suite command, and in front of their second interpreter copies, with the pairs sorted by name.

## sharedDocs and docTimebox

`sharedDocs` plus `docTimebox` set the shared-document catalog and timebox `fire` reads. Set them with `set --shared-docs` and `set --doc-timebox`. `--shared-docs` takes inline JSON, a JSON file, or `-` for stdin. `--doc-timebox` takes a whole number of minutes. For example `set --shared-docs config/shared.json` records the file contents and `set --doc-timebox 60` records 60 minutes.

A `file` entry names one document. A `file` entry whose path ends with `/` names a directory prefix. Files under it are documents. A `skill` entry names a directory with or without its trailing slash. Its `SKILL.md` files are documents. `docTimebox` is a whole number of minutes above zero. It defaults to 90. `fire` refuses a lease holding a shared document when `--timebox` is over `docTimebox` or `--tickets` names more than one ticket, unless `--doc-item`. A successful `fire --doc-item` with no held shared document records the shared-document note `doc-item`. A successful `fire --doc-item` with held shared documents records `doc-item` followed by a colon and the sorted held documents.

## changeNoteDir

`changeNoteDir` sets the fragment directory `fire` leases. Set it with `set --change-note-dir`. For example `set --change-note-dir notes` records `notes`. Clear it with an empty value.

It is an optional repository-relative directory path. It defaults to unset. When it is set, `fire` leases `<dir>/<branch>.md` for the branch that attempt will use. `%` is written as `%25` and `/` is written as `%2F`. When it is unset, `fire` leases no fragment. `fire` with no paths reads no `changeNoteDir` and leases no fragment.

## houseRulesCap

`houseRulesCap` caps one role's house rules by bytes. `brief` and `next` read it. It is a positive integer count of bytes, written by hand in `restaurant.json` as a JSON integer. It is absent by default, and an absent key leaves both commands as they are. A present value that is a boolean, is not an integer, or is under 1 is refused by `brief` and by `next`.

The three roles are `worker`, `reviewer`, and `coordinator`. A rule is a line that starts at column 0 and the indented or blank lines under it. Its first role tag anywhere on its first line selects a role. A rule tagged for one role is left out of every other role's text. A rule tagged for the role loses the tag and at most one space after it. An untagged rule is in every role's text as it is. A role's size is the byte count of that text.

When a role's size is over the cap, `brief` exits 1 and writes no brief and no contract, and prints one line for each over-cap role that names the role, its size, and the cap. `next` prints the same line for each over-cap role on standard error and exits 0.

## prCredit

`prCredit` sets whether pull requests credit the models that did the work. Set it with `set --pr-credit` or at creation with `open --pr-credit`. Both flags take `on` or `off`. For example `set --pr-credit on` records `true`.

It is a JSON boolean and defaults to unset. A missing key reads as off. When it is off, `credit` prints `credit: off for this restaurant; set --pr-credit on to turn it on`. Onboard lists it as `pr_credit`, missing until it is recorded, and its fix is `open --pr-credit on or set --pr-credit on`. A present value that is not a boolean is refused.

## Refusals

`set` validates the prospective settings with the same readers `affected` and `fire` use. `layout` judges `affectedTests`. `doc_policy` judges `sharedDocs` and `docTimebox`. `set` refuses a malformed `changeNoteDir` with nothing written. `fire` with paths refuses a malformed `changeNoteDir` before it claims a lease. `fire` with no paths reads no `changeNoteDir` and leases no fragment. `fire` always reads `sharedDocs` and `docTimebox` and never reads `affectedTests`.

## Example

This section is an example. It holds example values. It names no default. The `affectedTests` block is a key fragment. Save its inner object to a file and pass that file with `set --affected-tests`, or pipe that object. Replace the placeholder interpreter path.

```json
"affectedTests": {
  "always": "tests.test_pstack_t3",
  "workflowModule": "tests.test_run_tests",
  "entryPoints": ["t3/added/brigade/scripts/brigade.py", "t3/added/landing/scripts/land.py"],
  "helpers": ["tests/brigade/support.py", "tests/landing/support.py"],
  "suite": "python3 scripts/run_tests.py -j 8",
  "python": "<path to a Python 3.10 interpreter>",
  "importRoots": ["scripts", "t3/scripts"]
}
```

```json
{
  "sharedDocs": [
    {"path": "README.md", "kind": "file"},
    {"path": "docs/guide.md", "kind": "file"},
    {"path": "t3/runtime.md", "kind": "file"},
    {"path": "t3/setup.md", "kind": "file"},
    {"path": "t3/added/", "kind": "skill"},
    {"path": "t3/overrides/", "kind": "skill"},
    {"path": "skills/", "kind": "skill"}
  ],
  "docTimebox": 90
}
```

```json
{
  "changeNoteDir": "changes"
}
```
