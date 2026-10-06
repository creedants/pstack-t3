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
$L init --trunk main --mode human|merge|push|local --check "<cmd>" [--check ...] [--setup "npm ci"] [--batch 4] [--timeout 1800] [--merge-method merge|squash|rebase] [--base <commit>]
$L mode merge [--merge-method squash]   # switch between human, merge, and push while nothing is in flight
$L lease claim --holder <restaurant>/<dish> --paths src/engine,package.json   # prints L<n>, or who holds the overlap
$L lease renew L3 ; $L lease release L3 ; $L lease list
$L submit --holder <restaurant>/<dish> --branch <b> --sha <reviewed sha> --lease L3 --reviewer <provider/model> [--title "..." --body-file pr.md]   # prints Q<n>
$L land                      # drain the queue; prints what landed, bounced, or is still queued
$L status [Q3] ; $L resume
$L slot -- npm test          # run a heavy command under a governor slot
$L slot --exclusive -- npm run bench   # hold every slot: nothing else heavy runs while it measures
```

Every command takes `--help`.

## Set up a repository once

Run `init` the first time any coordinator writes to a repository. The mode decides whether the user stays a gate on landing, so it is the user's choice. brigade asks it when a restaurant opens. Any other coordinator asks once per repository with the host's question tool, unless the user already said.

| Mode | Landing does | The user |
| --- | --- | --- |
| `merge` | Rebases onto trunk, runs the checks, pushes `landing/q<n>`, and opens a PR. It reads posted checks before it runs `gh pr merge`, including `--auto`. A pending posted check waits. A failed posted check bounces. A later `land` reads the PR state before it acts on a failed check, and reads it again after that check read. A PR merged by then is landed in that same run, and its queue branch is deleted, even when a check fails afterward. An open PR noted `merge requested by the queue` bounces when a check fails, and the lease becomes active. The check read includes `autoMergeRequest`. The bounce runs `gh pr merge --disable-auto` only when that field is not null, and leaves the PR open. It posts no comment. Any other bounce of an open PR in this mode does the same. When the field is null, the bounce does not call `--disable-auto`. When that call fails, the bounce line says auto-merge is still enabled. It merges when a check read succeeds and no posted check is pending or failed. The first land that sees no posted check records that run and waits. A later `land` merges when checks are still absent. When no check is posted, a later `land` still runs the plain `gh pr merge` after `--auto`. The entry waits and the queue does not pause only when that plain merge is refused because the base branch policy prohibits the merge. A plain merge that fails for any other reason pauses the queue and names that failure, including when the `--auto` attempt said auto-merge is disabled. A failed or unreadable check read pauses the queue and does not merge. After the PR merges, it deletes remote `landing/q<n>`. When that push fails, it asks `gh api repos/{owner}/{repo}/branches/landing/q<n>`. HTTP 404 lands the entry. The 404 settles the entry only when the contract remote has exactly one push URL and that URL names the same owner/repo `gh repo view --json nameWithOwner` resolves. HTTP 200, or any other result, leaves the entry for the next run. It deletes a local `landing/q<n>` branch when one exists and git can delete it. When that delete fails, `land` still marks the entry landed and prints the branch it left. | Reviews after the fact. Nothing waits on them. |
| `human` | Rebases onto trunk, runs the checks, pushes `landing/q<n>`, and opens a PR. Leaves the PR open. Marks it landed when someone merges it and deletes remote `landing/q<n>`. When that push fails, it asks `gh api repos/{owner}/{repo}/branches/landing/q<n>`. HTTP 404 lands the entry. The 404 settles the entry only when the contract remote has exactly one push URL and that URL names the same owner/repo `gh repo view --json nameWithOwner` resolves. HTTP 200, or any other result, leaves the entry for the next run. It prints a local branch it could not delete. | Merges every PR. |
| `push` | Rebases, runs the checks, and pushes trunk only if the remote is still at the tested base. No PRs. | Reviews after the fact, in the commit history. |
| `local` | Lands on `refs/landing/<trunk>`, which no checkout uses. Pass `--base`. | Merges the lane into a branch when ready. |

`merge` and `push` let reviewed changes reach trunk with no human gate. When the PR needs an approving review, or when it has changes requested, the queue pauses and names the PR and that requirement. It does not run `gh pr merge --auto`. The user then either relaxes the rule for the queue or switches to `human`.

Checks are the repository's own gates: test, type check, lint. `--setup` installs dependencies, because the queue's worktree is cleaned with `git clean -ffdx` before every attempt. `land.py mode` switches between `merge`, `human`, and `push` once nothing is queued, landing, or awaiting merge. Changing trunk or remote, or moving to or from `local`, needs a new contract.

## Run writers through it

For every unit of writing work:

1. **Claim.** `lease claim` the files and directories the unit will change, before delegating. When the claim is refused, fold the unit into the holder's work or hold it until that lease is released. Never claim `.` unless the unit really touches everything.
2. **Brief.** A writer is a worktree thread from `t3_thread_launch` when it runs long or the user should see it, and a `delegate_task` child otherwise. Tell the worker: work only in its own worktree branched from `<remote>/<trunk>` (or `refs/landing/<trunk>` in local mode), change only the leased paths, keep history linear with no merge commits, run builds and tests through `land.py slot --` and every benchmark or timing measurement through `land.py slot --exclusive --`, commit, and report the branch and head SHA. It does not push to trunk or merge.
3. **Review** the exact SHA with a verifier from another model family, per the coordinator's own review step.
4. **Submit** that SHA with the lease and the reviewer. In `merge` and `human` mode, pass `--title` and `--body-file` so the PR says what changed and how it was verified. The queue appends the reviewer and SHA. A submit that changes paths outside the lease is refused. Widen the lease or split the change.
5. **Land.** Run `land`. When it prints "queue busy", another run will take the entry.
6. **Settle.** Read `status Q<n>`.
   - `landed`: the lease is released. Record it in the coordinator's tables, then remove the worker's worktree with `git worktree remove` and delete its branch.
   - `bounced`: the lease is active again for the fix. Call `unwatch_pull_request` on the PR the bounce left open. Fire a fresh worker with the original brief, the bounce reason, and current trunk. A conflict or a changed rebase needs a new review after the fix.
   - `awaiting-merge` (`merge` and `human` mode): the PR is open. In `merge` mode the queue merges it when a check read succeeds and no posted check is pending or failed. The first land that sees no posted check waits. A later `land` merges when checks are still absent. It waits without pausing only when the plain merge is refused because the base branch policy prohibits the merge. Any other plain-merge failure pauses the queue and names that failure, even when `--auto` said auto-merge is disabled. A failed posted check bounces an open PR, including one already noted `merge requested by the queue`, and makes the lease active. The bounce reads `autoMergeRequest` on that check read and runs `gh pr merge --disable-auto` only when the field is not null. It leaves the PR open and posts no comment. When the field is null, it does not call `--disable-auto`. A failed disable is part of the bounce line. The queue reads the PR state first, and again after the check read. A PR already merged at the candidate head is landed in that run even when a check fails. That `land` deletes remote `landing/q<n>`. When that push fails, it asks `gh api repos/{owner}/{repo}/branches/landing/q<n>`. HTTP 404 lands the entry. The 404 settles the entry only when the contract remote has exactly one push URL and that URL names the same owner/repo `gh repo view --json nameWithOwner` resolves. HTTP 200, or any other result, leaves the entry for the next run. It prints a local branch it could not delete.

## Keep the queue moving

While any entry you submitted is `queued` or `awaiting-merge`, keep the queue moving. Drains from other coordinators on the same repository are harmless, because the queue lock runs one at a time.

In `merge` and `human` mode, once `land` has opened a PR, this thread calls `watch_pull_request` on that PR per [Pull request watching](../pstack-runtime/SKILL.md#pull-request-watching). This thread owns the PR. A worker child cannot watch. On each wake, run `land` and act on what it prints. When that wake's `land` prints `queue busy`, run `land` again before ending the turn, at most 3 more times, waiting 5 seconds before each try. The passed-checks wake is spent, so this thread would otherwise notice the merge only from the hourly heartbeat. Keep the watch while the entry is queued or awaiting merge. An interim status reply keeps the watch. Call `unwatch_pull_request` only when this thread stops driving that PR or hands the queue back to the user. Driving stops when that PR closes, when the queue bounces it and leaves it open, or when this thread stops owning the queue. When `land` reports a bounce and leaves the PR open, call `unwatch_pull_request` on that PR.

Keep one `schedule_task` heartbeat for a `queued` entry that has no open PR yet, and beside every watch. Create that heartbeat only if none is running, and reuse it across frontiers. The heartbeat exists because a PR with no posted check never wakes the watch, and because a merge never wakes the watch. In `merge` mode on a repository with required checks, when the PR is not in a merge queue, use `{"type": "interval", "everyMs": 3600000}`. The required-checks wake runs `land`. The hour covers a merge that finishes after that run, and a PR that posts no check. While an entry is `awaiting-merge` in `human` mode, or `gh api graphql` reports that `PullRequest.isInMergeQueue` is true for that PR, the heartbeat is required and uses `{"type": "interval", "everyMs": 900000}`. That interval is the exception in [Pull request watching](../pstack-runtime/SKILL.md#pull-request-watching). The prompt is "Run `python3 <skills>/landing/scripts/land.py --repo <checkout> land` and act on what it prints per the landing skill." If the running heartbeat has the other interval, delete it with `delete_scheduled_task` and create it again. Do not drop the heartbeat in `merge` mode. Delete it with `delete_scheduled_task` when `status` shows nothing queued or awaiting merge. A crashed run is recovered by the next one. It asks git whether the last attempt was published before it retries.

## When the queue pauses

`land` pauses the queue when trunk no longer contains the last landed commit: someone rewound or replaced it. That is the user's call. Report it as a decision with the two commits from the message. Run `resume` only after the user confirms trunk is right.

## Capacity

The governor defaults to one slot per four cores, at most four. Set it in `${XDG_STATE_HOME:-~/.local/state}/pstack-t3/governor/governor.json` as `{"slots": N}`. Landing checks use their own slot, so workers never starve the queue. A command already inside a slot runs nested commands without taking another. Dev servers do not take slots. Start them with the preview tools.

A measurement taken while other work loads the machine is wrong, not noisy. `slot --exclusive` takes the landing slot and every worker slot in a fixed order, waits for running heavy commands to finish, and holds them until the measurement ends. It must be the outermost slot. Nested inside another slot it refuses.
