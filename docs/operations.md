# Operating scripts

`ops/` holds one script that the operator of a T3 Code machine runs from the checkout, and nothing installs it.

- `ops/t3-archive-threads.py` archives finished agent threads in one T3 Code project.

It runs only when you call it.

## Configure once

```bash
mkdir -p ~/.config/pstack-t3
cp ops/config.example ~/.config/pstack-t3/ops.conf
```

Set every key. The script reads the keys with its own prefix and needs every one of them.

The script takes its config file from the first of these that applies.

1. `--config FILE`, as the first two arguments.
2. The `PSTACK_T3_OPS_CONFIG` environment variable, when it is not empty.
3. `$XDG_CONFIG_HOME/pstack-t3/ops.conf`, when `XDG_CONFIG_HOME` starts with `/`.
4. `~/.config/pstack-t3/ops.conf`.

The script never falls back from one to the next. When the file it chose does not exist, the script prints one line on standard error, exits 78, and does nothing else.

```text
t3-archive-threads.py: no config file at <file>. Copy ops/config.example to that path and set every key.
```

That holds for every argument list, `--help` and no arguments included. Every other config problem is also one line on standard error and exit 78. `--config` with no file after it prints `<script>: --config needs a file` and exits 2.

Keep the real config outside the checkout. The repository's `.gitignore` has no entry for `ops/`, so nothing in the repository keeps git from adding a config saved there.

## Config file

- One `KEY=value` per line. A key is an uppercase letter followed by uppercase letters, digits, and underscores.
- A line whose first character after any blanks is `#` is a comment. A line that is empty or holds only blanks is skipped.
- A value runs to the end of the line. Spaces, tabs, and carriage returns around it are dropped.
- Nothing is expanded. A quote, a `$`, or a `~` is a plain character, and no key allows one in its value.
- A key appears at most once in the file. A second line for the same key is refused.
- The script checks the syntax of every line. It reads `ARCHIVE_T3_HOME`, refuses any other key with the `ARCHIVE_` prefix, and ignores every other key, including `NIGHTLY_` keys left in an older file.
- The key below is required. It has no default.

| Key | What the script does with it | What the value may hold |
|---|---|---|
| `ARCHIVE_T3_HOME` | T3 Code's own directory. The archive script reads `userdata/statev2.sqlite` and `userdata/server-runtime.json` under it. | It starts with `/`. Letters, digits, `.`, `_`, `/`, and `-`. |

## Archive finished threads

```bash
ops/t3-archive-threads.py --project pstack-t3 --dry-run
ops/t3-archive-threads.py --project pstack-t3 --coordinator <thread id> --keep <thread id>
ops/t3-archive-threads.py --project pstack-t3 --top-level-settled --older-than-hours 48
```

Direct archiving requires `T3_CODE_MCP_AUTHORIZATION` in the session. Native Claude sessions on T3 Code nightly 20261013.3053 do not receive that variable, so use `--selection-only` and archive each selected thread with the host's `t3_thread_organize` tool. It archives through T3's own `t3_thread_organize` tool. It posts to `/mcp` at the `origin` address in `userdata/server-runtime.json` and sends the session's credential from `T3_CODE_MCP_AUTHORIZATION` with every request. It opens the database `userdata/statev2.sqlite` read-only to choose the threads and never writes to it. Archiving is reversible in T3.

On T3 Code 0.0.46-nightly.20261014.3105 or later, only one server can own a T3 home at a time. When startup reports another owner, stop that server before retrying, or use a separate T3 home and pair with the running server. Never unlink `server-owner.sqlite` to bypass the owner.

`--dry-run` reads the database, prints the first output line, and stops. It sends no request and needs no credential.

| Flag | Meaning |
|---|---|
| `--config FILE` | The config file. It counts only as the first two arguments. |
| `--project TITLE` | Required. The project's title, compared without regard to case. Exactly one project must have it. |
| `--older-than-hours N` | The age a thread needs before it is archived. A number, 24 when the flag is absent. |
| `--coordinator ID` | A top-level thread. A run in progress in it does not put the threads under it in `under a running thread`. `--top-level-settled` never archives it. |
| `--keep ID` | A thread to leave alone. It can be given more than once. |
| `--top-level-settled` | Also archive settled top-level threads. |
| `--dry-run` | Choose and print. Archive nothing. |
| `--selection-only` | Print the candidate thread ids as a sorted JSON array of strings on stdout and exit without constructing the MCP client and without reading `T3_CODE_MCP_AUTHORIZATION`. |

The script prints two lines.

```text
to archive: <n>; skipped: live <n>, recent <n>, under a running thread <n>, kept <n>
archived <n>, failed <n>
```

Between them it prints one `failed:` line for each of the first five threads that T3 did not archive. It carries on with the rest. With `--dry-run`, or when it chose no thread, it prints the first line only.

| Exit | Meaning |
|---|---|
| 0 | Every chosen thread was archived, or the run was a dry run, or no thread was chosen. |
| 1 | At least one thread was not archived, or the title did not match exactly one project. |
| 2 | The flags were not valid, or `--config` had no file after it. |
| 78 | The config file is missing or not valid. Nothing ran. |

### Which threads it archives

A run in progress is a run whose status is running, queued, starting, preparing, or waiting. A sub-agent thread is a thread that has a parent. The top-level thread above a thread is the one reached by following parents to the end. The age of a thread counts from its last update.

- A thread with a run in progress is never archived.
- The script shields the ancestors of a live thread, so a thread above live work is never archived.
- A pending, running, or waiting execution node or provider-native subagent does not keep a thread when its run is interrupted, cancelled, failed, or rolled back.
- A thread under a top-level thread that has a run in progress is skipped, unless that top-level thread is the one named with `--coordinator` or is itself archived.
- A sub-agent thread under a top-level thread that has no run in progress is archived by age, whoever owns that top-level thread.
- To protect another long-running thread's children while it is idle, name it with `--keep`.
- `--keep` on a top-level thread protects it and everything under it. When that thread is also the `--coordinator`, `--keep` protects the thread itself and nothing under it.
- `--keep` on a sub-agent thread protects that thread only.
- A top-level thread is archived only with `--top-level-settled`, and then only when it is settled, not pinned, not the `--coordinator`, and old enough.
- A pinned thread is protected only as a top-level thread under `--top-level-settled`. A pinned sub-agent thread is archived by age.
- Deleted and already archived threads are never chosen.
- Other projects' threads are never chosen.

## Limits

- A value outside its key's characters is refused, so a path with a space in it cannot be configured.
- `--help` and the usage text need a valid config, like every other call.
- A thread row with no update time ends the archive script with a traceback and exit 1 when its age is compared.
- A missing database ends the archive script with a traceback and exit 1. So does a missing `server-runtime.json` or a missing `T3_CODE_MCP_AUTHORIZATION` when there is a thread to archive and the run is not a dry run.

`tests/test_ops.py` runs the script against a stand-in. No test covers the following.

- A run with `HOME` not set.
- T3's live database, which T3 Code has open while the script reads it.
- The real tool endpoint and its credential.

The agent activity view is not in `ops/`. The repository already holds it as the brigade skill's `scripts/activity.py`.
