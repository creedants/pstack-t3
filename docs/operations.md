# Operating scripts

`ops/` holds two scripts that the operator of a T3 Code machine runs from the checkout, and nothing installs them.

- `ops/t3-nightly-update.sh` updates T3 Code and a list of command-line tools.
- `ops/t3-archive-threads.py` archives finished agent threads in one T3 Code project.

Neither script runs unless you call it.

## Configure once

```bash
mkdir -p ~/.config/pstack-t3
cp ops/config.example ~/.config/pstack-t3/ops.conf
```

Set every key. Each script reads the keys with its own prefix and needs every one of them.

Each script takes its config file from the first of these that applies.

1. `--config FILE`, as the first two arguments.
2. The `PSTACK_T3_OPS_CONFIG` environment variable, when it is not empty.
3. `$XDG_CONFIG_HOME/pstack-t3/ops.conf`, when `XDG_CONFIG_HOME` starts with `/`.
4. `~/.config/pstack-t3/ops.conf`.

A script never falls back from one to the next. When the file it chose does not exist, the script prints one line on standard error, exits 78, and does nothing else.

```text
t3-nightly-update.sh: no config file at <file>. Copy ops/config.example to that path and set every key.
```

That holds for every argument list, `--help` and no arguments included. Every other config problem is also one line on standard error and exit 78. `--config` with no file after it prints `<script>: --config needs a file` and exits 2.

Keep the real config outside the checkout. The repository's `.gitignore` has no entry for `ops/`, so git shows a config saved there as a new file to commit.

## Config file

- One `KEY=value` per line. A key is an uppercase letter followed by uppercase letters, digits, and underscores.
- A line whose first character after any blanks is `#` is a comment. A line that is empty or holds only blanks is skipped.
- A value runs to the end of the line. Spaces, tabs, and carriage returns around it are dropped.
- Nothing is expanded. A quote, a `$`, or a `~` is a plain character, and no key allows one in its value.
- A list is words separated by spaces or tabs.
- A key appears at most once in the file. A second line for the same key is refused.
- Each script checks the syntax of every line. It reads the keys that start with its own prefix, refuses a key with that prefix it does not know, and ignores every other key.
- Every key below is required by the script that owns its prefix. No key has a default.

| Key | What the script does with it | What the value may hold |
|---|---|---|
| `NIGHTLY_APPS_DIR` | The directory that holds the installed build, the staged build, the downloads, the log `t3-nightly-update.log`, and the state file `t3-nightly-update.state`. | It starts with `/`. Letters, digits, `.`, `_`, `/`, and `-`. |
| `NIGHTLY_APP_NAME` | The installed build is the directory of this name inside `NIGHTLY_APPS_DIR`. The program is the file `t3code` in it. The staged build is the directory of this name with `.new` added. | It starts with a letter or a digit. Letters, digits, `.`, `_`, and `-`. |
| `NIGHTLY_RESTART_ENV` | A file that `restart` sources with bash before each start of T3 Code. It can hold credentials. | It starts with `/`. Letters, digits, `.`, `_`, `/`, and `-`. |
| `NIGHTLY_MISE_TOOLS` | A list. `check` passes it to `mise outdated` and `clis` passes it to `mise upgrade`. | One word or more. Each word starts with a letter or a digit. Letters, digits, `.`, `_`, `:`, `@`, `/`, `+`, and `-`. |
| `NIGHTLY_VERSION_COMMANDS` | A list. `clis` logs the first line of `<command> --version` for each command that is installed. | One word or more. Each word starts with a letter or a digit. Letters, digits, `.`, `_`, `+`, and `-`. |
| `ARCHIVE_T3_HOME` | T3 Code's own directory. The archive script reads `userdata/statev2.sqlite` and `userdata/server-runtime.json` under it. | It starts with `/`. Letters, digits, `.`, `_`, `/`, and `-`. |

## Nightly update

```bash
ops/t3-nightly-update.sh check
ops/t3-nightly-update.sh clis
ops/t3-nightly-update.sh stage
setsid -f ops/t3-nightly-update.sh restart </dev/null >/dev/null 2>&1
```

The script runs one step per call, and only the step you name. With no step or an unknown step it prints its usage and exits 2. Every line a step logs goes to standard output and to `t3-nightly-update.log` in `NIGHTLY_APPS_DIR`, with the time in front.

`restart` stops T3 Code, so it stops every T3 thread, the one that started it included. Start it detached, as the fourth line does. That line discards the output, so it does not show a config refusal. Run `check` in the foreground after every config edit.

### `check`

`check` prints the following and changes nothing.

- The installed version. It is the content of `.nightly-version` in the installed build. Without that file it is the last, in sort order, of the version strings that `strings` finds in `resources/app.asar`.
- The latest nightly. `gh release list -R pingdotgg/t3code --limit 40` is filtered to the tags that contain `-nightly.`, and the one published last is taken.
- The staged version, only when a staged build exists. It is `unknown` when the staged build has no `.nightly-version`.
- The output of `mise outdated` for the tools in `NIGHTLY_MISE_TOOLS`, run with `MISE_MINIMUM_RELEASE_AGE=0`.
- The `cursor-cli` version that `pacman -Q cursor-cli` reports.

### `clis`

Run `clis` only while no agent is working. The script does not check that.

1. It runs `mise upgrade` with `MISE_MINIMUM_RELEASE_AGE=0` for the tools in `NIGHTLY_MISE_TOOLS` only. It appends the output of `mise` to the log and logs `clis: mise upgrade ok` or `clis: mise upgrade FAILED, see log`. It carries on in both cases.
2. It upgrades `cursor-cli` with `yay -S --needed --noconfirm cursor-cli` only when `yay` is on `PATH` and `pacman -Q cursor-cli` succeeds. It logs `clis: cursor-cli ok` or `clis: cursor-cli FAILED, see log`.
3. It logs one version line for each command in `NIGHTLY_VERSION_COMMANDS` that is installed.

A failed upgrade does not change the exit status. `clis` exits 1 when the last command in `NIGHTLY_VERSION_COMMANDS` is not installed.

### `stage`

`stage` prepares the new build beside the installed one. It does not touch the installed build or the running T3 Code.

1. It reads the installed version and the latest nightly, as `check` does.
2. It downloads `T3-Code-<version>-x86_64.AppImage` from the release `v<version>` into `NIGHTLY_APPS_DIR` with `gh release download`. The download goes to a `.part` file that is renamed when it is complete. When the download fails, it removes the `.part` file. A file of the final name that is already there is used as it is.
3. It reads `nightly-linux.yml` from the same release and takes the `sha512` value listed for that file. It compares the value with the base64 SHA-512 of the file from `openssl`. When the manifest lists no value or the two differ, it renames the file with `.bad` added and stages nothing.
4. It makes the file executable, removes an older `squashfs-root` and an older staged build, and runs the file with `--appimage-extract` inside `NIGHTLY_APPS_DIR`. When the unpacked tree has no executable `t3code`, it removes the tree and stages nothing.
5. It moves the unpacked `squashfs-root` to the staged build's path, sets that directory's mode to 755, writes the version to `.nightly-version` in it, and writes the state file.

### `restart`

1. It checks that a staged build exists. The staged build is a directory with an executable `t3code` in it.
2. It names the place for the previous build. The name is the installed build's path, then `.prev-`, then the part of the installed version after `nightly.`. When that name is taken, it adds `.` and the time as six digits.
3. It waits 5 seconds. It sends `TERM` to the oldest process whose command line is the path of the installed `t3code`. It checks once a second, for at most 60 seconds, until no process has that path in its command line. Then it sends `KILL` to every process that still has it and waits 1 second.
4. It moves the installed build to the previous build's place and the staged build to the installed build's path.
5. It sources `NIGHTLY_RESTART_ENV`, starts `t3code` with `setsid -f`, and checks once a second, for at most 45 seconds, that it is running.
6. When it is running, it waits 15 seconds and checks again. When it is still running, it writes the state file, logs `restart: running <version>`, and exits 0.

When step 5 or step 6 finds no running build, `restart` rolls back. See [Rollback](#rollback).

When the first move of step 4 fails, `restart` logs `restart: could not move the old build aside; starting it again`, starts the installed build as in step 5, and exits 1. When the second move fails, it moves the previous build back, logs `restart: swap FAILED, old build restored`, starts it as in step 5, and exits 1.

## Order of a nightly run

1. Pause. The caller starts no new work and waits until no T3 thread it wants to keep is running.
2. `check`.
3. `clis`.
4. `stage`.
5. `restart`, started detached.
6. Post-check. The caller reads `t3-nightly-update.state` in `NIGHTLY_APPS_DIR` and runs `check` once T3 Code is back.

The script has no pause step and no post-check step. Both are the caller's work.

## State file

The state file is `t3-nightly-update.state` in `NIGHTLY_APPS_DIR`. It holds `key=value` lines. Each time is the output of `date -Is`.

| Written | Lines, in order |
|---|---|
| By `stage`, after it staged a build | `from`, `to`, `staged` |
| By `restart`, after a good restart | `from`, `to`, `restarted`, `result=ok`, `prev` |
| By `restart`, after a rollback | `from`, `to`, `restarted`, `result=rolled-back` |
| By `restart`, after a rollback that did not start | `from`, `to`, `restarted`, `result=down` |

`from` is the version that was installed when the step began. `to` is the staged version. `restart` writes `unknown` for it when the staged build has no `.nightly-version`. `staged` and `restarted` are times. `prev` is the path of the previous build.

`stage` removes the state file when the installed build is already the latest. `restart` writes the state file only in the three outcomes above. When it exits 1 before it starts the new build, it leaves the file as it was.

## Skip conditions

- `stage` exits 3 and removes the state file when the installed build is the latest. Skip `restart`.
- `stage` exits 1 when it cannot read the latest nightly, the download fails, the checksum does not match, or the unpack fails. Skip `restart`.
- `restart` exits 1 with `restart: nothing staged` and stops nothing when no staged build exists.
- Skip `restart` while any thread is still running, because `restart` stops every T3 thread. This is an instruction to the caller. The script does not check for running threads.

## Rollback

### Automatic

`restart` rolls back when the new build is not running after the 45 one-second checks, or is gone 15 seconds later.

1. It logs `restart: <new version> did not stay up; rolling back to <previous version>`.
2. It sends `KILL` to every process that has the installed `t3code` path in its command line and waits 1 second.
3. It moves the new build to the installed build's path with `.failed-` and the part of the new version after `nightly.` added.
4. It moves the previous build back to the installed build's path. When step 3 failed, it skips this move, and the new build stays at the installed build's path.
5. It starts the build at the installed build's path as `restart` starts any build, with `NIGHTLY_RESTART_ENV` and the 45 one-second checks.
6. When that build is running, it writes `result=rolled-back`. When it is not, it writes `result=down`.
7. It sends one critical desktop notification with `notify-send`. The title is `T3 Code update rolled back` or `T3 Code is down`.
8. It exits 1.

### By hand

After a good restart the previous build stays where step 2 of `restart` put it, and the `prev` line of the state file holds that path. The script never deletes a `.prev-` directory or a `.failed-` directory.

The script has no step for a rollback by hand. Do it in this order.

1. Stop T3 Code.
2. Move the current build aside.
3. Move the `.prev-` directory to the installed build's path.
4. Start T3 Code.

## Exit codes of the nightly script

| Exit | Meaning |
|---|---|
| 0 | The step finished. |
| 1 | The step failed. `clis` exits 1 when the last command in `NIGHTLY_VERSION_COMMANDS` is not installed. |
| 2 | No step or an unknown step was named, or `--config` had no file after it. |
| 3 | `stage` found that the installed build is the latest. |
| 78 | The config file is missing or not valid. Nothing ran. |

## Archive finished threads

```bash
ops/t3-archive-threads.py --project pstack-t3 --dry-run
ops/t3-archive-threads.py --project pstack-t3 --coordinator <thread id> --keep <thread id>
ops/t3-archive-threads.py --project pstack-t3 --top-level-settled --older-than-hours 48
```

Run it from inside a T3 agent session. It archives through T3's own `t3_thread_organize` tool. It posts to `/mcp` at the `origin` address in `userdata/server-runtime.json` and sends the session's credential from `T3_CODE_MCP_AUTHORIZATION` with every request. It opens the database `userdata/statev2.sqlite` read-only to choose the threads and never writes to it. Archiving is reversible in T3.

`--dry-run` reads the database, prints the first output line, and stops. It sends no request and needs no credential.

| Flag | Meaning |
|---|---|
| `--config FILE` | The config file. It counts only as the first two arguments. |
| `--project TITLE` | Required. The project's title, compared without regard to case. Exactly one project must have it. |
| `--older-than-hours N` | The age a thread needs before it is archived. A number, 24 when the flag is absent. |
| `--coordinator ID` | A top-level thread. Threads under it are not skipped while it has a run in progress, and `--top-level-settled` never archives it. |
| `--keep ID` | A thread to leave alone. It can be given more than once. |
| `--top-level-settled` | Also archive settled top-level threads. |
| `--dry-run` | Choose and print. Archive nothing. |

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
- A thread under a top-level thread that has a run in progress is skipped, unless that top-level thread is the one named with `--coordinator` or is itself archived.
- A sub-agent thread under a top-level thread that has no run in progress is archived by age, whoever owns that top-level thread.
- To protect another long-running thread's children while it is idle, name it with `--keep`.
- `--keep` on a top-level thread protects it and everything under it. When that thread is also the `--coordinator`, `--keep` protects the thread itself and nothing under it.
- `--keep` on a sub-agent thread protects that thread only.
- A thread whose child is running is not protected by that child.
- A top-level thread is archived only with `--top-level-settled`, and then only when it is settled, not pinned, not the `--coordinator`, and old enough.
- A pinned thread is protected only as a top-level thread under `--top-level-settled`. A pinned sub-agent thread is archived by age.
- Deleted and already archived threads are never chosen.
- Other projects' threads are never chosen.

## Limits

- `clis` exits 1 when the last command in `NIGHTLY_VERSION_COMMANDS` is not installed, even when every upgrade worked.
- The installed build's path is used as a regular expression by `pgrep -f` and `pkill -f`. A `.` in it matches any character.
- Only the `x86_64` AppImage asset is handled.
- When the file `NIGHTLY_RESTART_ENV` names is missing, bash prints an error and T3 Code is started without it.
- A value outside its key's characters is refused, so a path with a space in it cannot be configured.
- `--help` and the usage text need a valid config, like every other call.
- The nightly script never deletes a downloaded AppImage, a `.bad` file, or its log.
- A thread row with no update time ends the archive script with a traceback and exit 1 when its age is compared.
- A missing database ends the archive script with a traceback and exit 1. So does a missing `server-runtime.json` or a missing `T3_CODE_MCP_AUTHORIZATION` when there is a thread to archive and the run is not a dry run.

`tests/test_ops.py` runs both scripts against stand-ins. No test covers the following.

- The real restart path. The tests replace `kill`, `pgrep`, `pkill`, `setsid`, `sleep`, and the application with stand-ins and count seconds instead of waiting.
- The real `gh` and its network, the filter that picks the latest nightly included.
- The real `mise`, `yay`, `pacman`, `strings`, and `notify-send`.
- The unpack of a real AppImage.
- A real environment file.
- A run with `HOME` not set.
- T3's live database, which T3 Code has open while the script reads it.
- The real tool endpoint and its credential.

The activity prototype is not in `ops/`, because the brigade skill's `activity.py` replaces it.
