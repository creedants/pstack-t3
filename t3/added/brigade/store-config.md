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

The second interpreter file name is `python<major>` or `python<major>.<minor>`. The paragraph names it by that version. A layout that sets `python` needs a `suite` that starts with `python3`. That interpreter replaces the leading `python3`. `importRoots` is tried in candidate order after the loader directory and the root with every match kept. Relative imports never search it. Roots are normalized and deduped in order and never hold the root itself. An absent `importRoots` means the directories of `entryPoints` and `helpers`.

## sharedDocs and docTimebox

`sharedDocs` plus `docTimebox` set the shared-document catalog and timebox `fire` reads. Set them with `set --shared-docs` and `set --doc-timebox`. Each shared flag takes inline JSON, a JSON file, or `-` for stdin. For example `set --shared-docs config/shared.json` records the file contents and `set --doc-timebox 60` records 60 minutes.

A `file` entry names one document. A `file` entry whose path ends with `/` names a directory prefix. Files under it are documents. A `skill` entry names a directory with or without its trailing slash. Its `SKILL.md` files are documents. `docTimebox` is a whole number of minutes above zero. It defaults to 90. `fire` refuses a lease holding a shared document when `--timebox` is over `docTimebox` or `--tickets` names more than one ticket, unless `--doc-item`. A successful `fire --doc-item` records the shared-document note `doc-item`.

## changeNoteDir

`changeNoteDir` sets the fragment directory `fire` leases. Set it with `set --change-note-dir`. For example `set --change-note-dir notes` records `notes`. Clear it with an empty value.

It is an optional repository-relative directory path. It defaults to unset. When it is set, `fire` leases `<dir>/<branch>.md` for the branch that attempt will use. `%` is written as `%25` and `/` is written as `%2F`. When it is unset, `fire` leases no fragment. `fire` with no paths leases nothing and reads nothing.

## Refusals

`set` validates the prospective settings with the same readers `affected` and `fire` use. `layout` judges `affectedTests`. `doc_policy` judges `sharedDocs` and `docTimebox`. A present value that does not parse is refused, with nothing written and no lease claimed.

## Example

This section is an example. It holds this repository values. It names no default.

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
