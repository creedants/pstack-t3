---
name: landing
description: "Let many agents write to one repository at once without collisions: a per-repository landing contract, path leases claimed before work starts, one queue that is the only writer to trunk, and a machine-wide slot governor for builds and tests. Use for 'landing', 'land this', 'merge queue', 'many agents on one repo', 'where does this work land', or before any coordinator (brigade, Orchestrate, Autopilot, swarm) delegates writing work in parallel."
---

# Landing

Writing scales. Integration does not. Landing makes integration a property of the repository, decided once, so no coordinator asks where work lands and no two writers collide.

The rules:

1. One trunk per repository. Every change lands there through the queue.
2. A writer claims a lease on the paths it will change before it starts. Overlapping claims are refused.
3. Writers never integrate. A worker commits in its own worktree and stops. It never merges, rebases a shared branch, or pushes trunk.
4. The queue is the only writer to trunk. It is a lock, not a process. Whoever runs `land` while the lock is free drains the queue. Everyone else gets "queue busy" and moves on.
5. A review verdict holds only for the exact change reviewed. The queue lands the pinned SHA, and bounces it when the rebased change differs from the reviewed one.
6. Builds and tests run under a governor slot, so the machine is not overrun.

## The script

`<skills>/landing/scripts/land.py`, where `<skills>` is the directory holding this skill. Run it from any checkout of the repository, or pass `--repo <path>`. State lives in SQLite under `${XDG_STATE_HOME:-~/.local/state}/pstack-t3/landing/`, one store per repository.

```bash
L="python3 <skills>/landing/scripts/land.py --repo <checkout>"
$L init --trunk main --mode auto|human|local --check "<cmd>" [--check ...] [--setup "npm ci"] [--batch 4] [--timeout 1800] [--base <commit>]
$L lease claim --holder <restaurant>/<dish> --paths src/engine,package.json   # prints L<n>, or who holds the overlap
$L lease renew L3 ; $L lease release L3 ; $L lease list
$L submit --holder <restaurant>/<dish> --branch <b> --sha <reviewed sha> --lease L3 --reviewer <provider/model>   # prints Q<n>
$L land                      # drain the queue; prints what landed, bounced, or is still queued
$L status [Q3] ; $L resume
$L slot -- npm test          # run a heavy command under a governor slot
```

Every command takes `--help`.

## Set up a repository once

Run `init` the first time any coordinator writes to a repository. Pick the mode from facts, not a question.

| Mode | When | Landing does |
| --- | --- | --- |
| `human` | Default when the repository has a remote. | Rebases onto trunk, runs the checks, pushes `landing/q<n>`, opens a PR. Marks it landed when the PR merges. |
| `auto` | The user granted autonomous merges for this repository. | Rebases, runs the checks, pushes trunk only if the remote is still at the tested base. |
| `local` | No remote, or work the user wants to keep off the remote. | Lands on `refs/landing/<trunk>`, which no checkout uses. The user merges it into a branch when ready. Pass `--base`. |

Checks are the repository's own gates: test, type check, lint. `--setup` installs dependencies, because the queue's worktree is cleaned with `git clean -ffdx` before every attempt. Changing trunk, mode, or remote later needs a new contract. `init` refuses.

## Run writers through it

For every unit of writing work:

1. **Claim.** `lease claim` the files and directories the unit will change, before delegating. When the claim is refused, fold the unit into the holder's work or hold it until that lease is released. Never claim `.` unless the unit really touches everything.
2. **Brief.** Tell the worker: work only in its own worktree branched from `<remote>/<trunk>` (or `refs/landing/<trunk>` in local mode), change only the leased paths, keep history linear with no merge commits, run builds and tests through `land.py slot --`, commit, and report the branch and head SHA. It does not push to trunk or merge.
3. **Review** the exact SHA with a verifier from another model family, per the coordinator's own review step.
4. **Submit** that SHA with the lease and the reviewer. A submit that changes paths outside the lease is refused. Widen the lease or split the change.
5. **Land.** Run `land`. When it prints "queue busy", another run will take the entry.
6. **Settle.** Read `status Q<n>`.
   - `landed`: the lease is released. Record it in the coordinator's tables, then remove the worker's worktree with `git worktree remove` and delete its branch.
   - `bounced`: the lease is active again for the fix. Fire a fresh worker with the original brief, the bounce reason, and current trunk. A conflict or a changed rebase needs a new review after the fix.
   - `awaiting-merge` (human mode): the PR is open. The next `land` run marks it landed when it merges.

## Keep the queue moving

While any entry you submitted is `queued` or `awaiting-merge`, keep a drain schedule with `schedule_task`. Drains from other coordinators on the same repository are harmless, because the queue lock runs one at a time. Use the schedule `{"type": "interval", "everyMs": 900000}` with the prompt "Run `python3 <skills>/landing/scripts/land.py --repo <checkout> land` and act on what it prints per the landing skill." Delete it with `delete_scheduled_task` when `status` shows nothing queued or awaiting merge. A crashed run is recovered by the next one. It asks git whether the last attempt was published before it retries.

## When the queue pauses

`land` pauses the queue when trunk no longer contains the last landed commit: someone rewound or replaced it. That is the user's call. Report it as a decision with the two commits from the message. Run `resume` only after the user confirms trunk is right.

## Capacity

The governor defaults to one slot per four cores, at most four. Set it in `${XDG_STATE_HOME:-~/.local/state}/pstack-t3/governor/governor.json` as `{"slots": N}`. Landing checks use their own slot, so workers never starve the queue. A command already inside a slot runs nested commands without taking another. Dev servers do not take slots. Start them with the preview tools.
