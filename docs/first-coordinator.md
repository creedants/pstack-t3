# First coordinator

This page takes a stranger from install to one landed change. The target is about 20 minutes. It uses a throwaway repository and a throwaway bare remote, so nothing reaches a repository you care about. The commands are the ones `scripts/outsider_smoke.py` runs in CI, and CI runs that smoke in `python:3.12-slim`. Every shell command below is a command the smoke executes. The smoke cannot start T3 threads, so the four steps that need T3 each carry a `T3 action` line.

## 1. Prepare the repository

Create a directory `~/my-repo` holding two files. `README.md` holds any text. `a.txt` holds the single line `first line`. The work later adds a second line to `a.txt`. Then create the throwaway remote and push the first commit.

```sh
git init -b main ~/my-repo
git init --bare ~/my-remote.git
git -C ~/my-repo remote add origin ~/my-remote.git
git -C ~/my-repo add README.md a.txt
git -C ~/my-repo commit --message "outsider smoke"
git -C ~/my-repo push origin main
```

`~/my-remote.git` is the remote. The landing queue pushes trunk to it, so this walkthrough needs no hosted repository. The smoke creates its own remote the same way.

## 2. Install

Run the install from your pstack-t3 checkout.

```sh
python3 scripts/install.py
```

## 3. Setup

> **T3 action:** Run `$setup-pstack` in a new T3 thread. It reads the live catalog and writes your roles.

The smoke runs the roles command below against a fixture catalog it writes. On your machine, a user-level `$setup-pstack` run writes the snapshot to `~/.config/pstack-t3/catalog.json`, or under `$XDG_CONFIG_HOME` when you set it to an absolute path. Use the path where your snapshot landed in the command below. Replace `grok/grok-4.7` with a provider and model you run.

```sh
python3 scripts/install.py doctor
```

```sh
python3 skills/pstack-runtime/scripts/roles.py show --catalog ~/.config/pstack-t3/catalog.json --parent grok/grok-4.7 --role "feature, refactoring"
```

## 4. Open the coordinator

> **T3 action:** Open the coordinator thread. In that thread, `$brigade` opens the restaurant. Pin the thread; `brigade open` does not pin it.

The smoke runs the same open command. It prints the restaurant path. Use that path as `$RESTAURANT` in the commands below.

```sh
python3 skills/brigade/scripts/brigade.py open --project-root ~/my-repo --name smoke
```

Bind the restaurant to the coordinator thread. `$THREAD` is the reader T3 thread id. Later brigade writes carry `--owner $THREAD@1`. The smoke binds with `smoke-thread`.

```sh
python3 skills/brigade/scripts/brigade.py --at $RESTAURANT set --thread $THREAD
```

## 5. Write the landing contract

The queue needs a contract before any work can be fired. The `local` and `push` modes are frozen, per [the guide](guide/coordinator.md). Push mode is supported and frozen. This walkthrough uses push mode.

```sh
python3 skills/landing/scripts/land.py --repo ~/my-repo init --trunk main --mode push --remote origin
```

The command prints the landing store path. That store holds the contract, the queue, and the lease table.

## 6. File a ticket

File the request on the restaurant's rail.

```sh
python3 skills/brigade/scripts/brigade.py --at $RESTAURANT --owner $THREAD@1 ticket add --summary "Add a second work line" --kind feature --paths a.txt
```

The command prints `T1`. `--paths` names the file the work will change, so fire can lease it.

## 7. Fire the work

Fire the ticket into one unit of work for the feature playbook.

```sh
python3 skills/brigade/scripts/brigade.py --at $RESTAURANT --owner $THREAD@1 fire --tickets T1 --station feature --summary "Add a second work line" --paths a.txt
```

The command prints the unit and its lease. Fire claims the landing lease on `a.txt`.

## 8. Run the worker

> **T3 action:** Render the worker brief with `brigade brief` and launch the worker thread with that brief. The worker runs the feature playbook in its own worktree.

The smoke cannot start a thread. It stands in for the worker with these commands on branch `smoke/d1`. The two lines show the change, and the commit records it.

```sh
git -C ~/my-repo worktree add ~/work -b smoke/d1 origin/main
printf 'first line\nsecond line\n' > ~/work/a.txt
git -C ~/work add a.txt
git -C ~/work commit -m "Add a second work line"
SHA=$(git -C ~/work rev-parse HEAD)
```

`SHA` is the commit the review checks and the queue lands.

## 9. Review the commit

> **T3 action:** Ask another model family to review that commit. Recording a pass refuses a verifier from the author's model family unless you pass `--same-family`.

Record the branch and the commit on the unit, then record the pass. `--author` and `--verifier` take provider and model names. The values below are the smoke's examples, so use the models you run.

```sh
python3 skills/brigade/scripts/brigade.py --at $RESTAURANT --owner $THREAD@1 dish D1 --branch smoke/d1 --sha $SHA --state in-review
```

```sh
python3 skills/brigade/scripts/brigade.py --at $RESTAURANT --owner $THREAD@1 pass record D1 --sha $SHA --verdict pass --author grok/grok-4.7 --verifier claude/claude-opus-5-5
```

The unit cannot move to merged until it has a pass at this commit.

## 10. Submit and land

Submit the reviewed commit to the queue, then drain the queue. The queue runs the contract's checks and pushes trunk to `origin`. This walkthrough sets no check, so the contract's check list is empty.

```sh
python3 skills/landing/scripts/land.py --repo ~/my-repo submit --holder smoke/D1 --branch smoke/d1 --sha $SHA --lease L1 --reviewer claude/claude-opus-5-5 --owner smoke/@1
```

```sh
python3 skills/landing/scripts/land.py --repo ~/my-repo land
```

The queue replays the reviewed commit onto trunk.

## 11. Check the trunk

`$REMOTE` is the remote's path, `~/my-remote.git`. Read the landed file from the remote's trunk.

```sh
git --git-dir $REMOTE show main:a.txt
```

The output shows the landed file. It holds both lines.

## 12. Mark it merged and close

Record that the unit merged. Then print the report. `close --dry-run` writes nothing.

```sh
python3 skills/brigade/scripts/brigade.py --at $RESTAURANT --owner $THREAD@1 dish D1 --state merged
```

```sh
python3 skills/brigade/scripts/brigade.py --at $RESTAURANT close --dry-run
```

The report counts one merged item. The store keeps the merged unit.

## What the smoke proves

The smoke runs every command above against a home it mints and a throwaway remote it creates. It checks each command's exit status and, where a step has one, its expected output. It then checks three results. The file on the remote's trunk holds both lines. The unit is merged. The report counts one merged item. CI runs the smoke on every pull request. The four `T3 action` steps are the only parts it cannot run.
