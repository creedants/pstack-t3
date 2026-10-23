Part of [the guide](../guide.md).

## 8. Open a standing coordinator

A coordinator is a pinned T3 thread for one project or one focus area. It takes requests, hands each unit of work to a playbook, has another model family review the result, or a same-family read when only the author's family has a runnable seat, and lands what passes. It never writes code. For one finite program with a done condition, use `$poteto-mode`. Its Orchestrate playbook stops when that work is done.

Every command and flag of `activity.py`, `brigade.py`, `land.py`, and `roles.py`, with its choices and defaults, is in the [command-line reference](../cli/README.md). This guide shows the commands you need and does not list every flag.

Type this in any thread.

```
$brigade open a standing coordinator for <project> focused on <goal>.
```

The opener asks who lands the work, unless you already named a mode. It recommends `merge` when the repository has a remote. The mode belongs to the repository. Every coordinator on that repository shares it. If the repository already has a landing contract, the opener tells you its mode. Choosing another mode switches it for every coordinator on that repository.

The opener also asks how often the coordinator replies, unless you already named a level. It recommends `milestones`.

### Runs on this project

38 of the 39 pull requests from #8 to #46 landed through the same queue. The queue then added a line to each body naming the reviewer and the commit that reviewer checked. It no longer adds that line, and the reviewer stays in the queue's local store. Timed runs are in [Live runs](../live-runs.md). The earlier pilot is audited under [Pilot: Bridgekit performance, 2026-10-04](../brigade-plan.md#pilot-bridgekit-performance-2026-10-04).

### What is frozen

Two pieces are frozen. A frozen piece keeps the tests it has and gets no new work. A fix for data loss or for a broken install still lands.

- **The executive admin.** The `brigade-admin` skill, opened with `$brigade-admin`.
- **The `local` and `push` landing modes.** The `merge` and `human` modes are not frozen.

### Give it work

Send the pinned thread a request.

```
Investigate the slow startup. Reproduce it, fix the cause, and verify the result.
```

It records the request and groups related requests into one unit of work.

### What the coordinator does without you

It wakes on your messages, on a worker's report-back, on a reviewer finishing, and on its schedules. Each worker calls `t3_thread_send` on the coordinator thread as its last step, after it writes the report. That message wakes the coordinator, which marks the attempt reported and reviews the work.

A morning run is every day at 09:00. A report is every day at 18:00. When `menu.md` names a source, intake also runs on an interval of at least one hour, matched to the sources in `house-rules.md`. A landing drain runs while work is queued or awaiting merge. In `merge` mode, when the repository has required checks and the pull request is not in a merge queue, that drain is hourly. While an entry is awaiting merge in `human` mode, or the pull request is in a merge queue, the drain is every 15 minutes.

A liveness check runs while work is in progress, every 10 minutes at `every-turn` and `milestones` and every 30 minutes at `digest` by default. `brigade.py set --liveness-ms` stores another interval. At `digest`, worker report-backs and pull request watches are the primary wakes. `brigade.py watch` prints `report written, no report-back` while the work is in progress, when the report was written for this attempt, and when this attempt is not marked reported. The report counts as written for this attempt when its modification time is at or after the attempt's start. A report left by a replaced worker does not count.

The line `report written, no report-back` is a defect. The check reads the worker thread, finds why the message never arrived, and fires a fix at that cause. The liveness check remains the backstop when a worker hangs or never sends the message.

- **Intake.** It reads `menu.md` and `house-rules.md` and records each request. It reads open work from the tracker AGENTS.md names, with the commands AGENTS.md names, and runs `gh issue list` only when AGENTS.md names no other tracker. It runs `gh pr list` only when the standing orders name pull requests. A ref the request names is its ref. Otherwise it matches the request against the tracker's open items, and a user request is still filed when the tracker cannot be read. It creates an item there only when AGENTS.md's filing rules call for one and you or the house rules allow that write.
- **Delegation.** It groups related requests, claims a path lease, writes the brief with `brigade.py brief`, and launches one worktree thread per unit. In-flight work stays under the worker cap, which `fire` enforces. Each ticket carries a priority of `urgent`, `normal`, or `low`. `brigade.py startable` lists the waiting tickets in start order with why each can or cannot start now, and it starts nothing. `brigade.py set --autofire` sets the lowest priority that `startable` lists as startable, or `off`.
- **Cross-family review.** It reads the worker's report and diff. One reviewer from another model family checks that exact commit. A pass is recorded against that commit. A reviewer from the author's family is used only when no other family can run, and the verdict says so. Every round, pass or fail, is written to its own file under the store's `reports/` and recorded with that file by [`pass record`](../cli/brigade.md#brigadepy-pass-record). Its `--member` and `--late` flags record a panel member's row and a round found unrecorded after the unit moved on. `brigade.py close` warns on stderr about a review report that no row names. A unit that has been sent back three times since its last pass or its last answered round decision gets no further fix round until you answer one decision. [`86 add --round-budget`](../cli/brigade.md#brigadepy-86-add) writes it with the options `known limits`, `redesign`, and `drop`. `keep parked` is the default and leaves it open. Until you answer it, `dish`, `brief`, and `fire` refuse another fix round for that unit.
- **Follow-ups.** On a pass, the coordinator runs [`ticket add --from-report`](../cli/brigade.md#brigadepy-ticket-add) on the worker's report. It files at most one waiting ticket per item, with source `report`, only for a follow-up under a `Follow-ups` heading marked `Safety:`, `Privacy:`, or `Correctness:`. It skips text that a ticket already holds in any state. Every other follow-up goes to `notes.tsv`. Run `ticket notes --paths a.py,b.py` to list notes for those repository paths, or `ticket notes --all` to include notes with no tracked path. A worker's brief asks for a `## Follow-ups` heading with one top-level list item for each follow-up, or `None.`
- **Landing.** On a pass it submits that commit to the queue and runs `land.py land`. Workers never merge. In `merge` and `human` mode the PR title and body go with the submit. A conflict or a changed rebase goes to a fresh worker and needs a new review.
- **Cleanup.** After a unit merges, is dropped, or is sent back, it removes that unit's worktree and branch. It deletes `landing/e<n>` after that PR merges or closes. It deletes only branches it created. It fast-forwards your checkout with `git merge --ff-only` only when that checkout is clean and on trunk.
- **Reports.** The reporting level decides which wakes get a reply. `brigade.py close` runs only on the occasions named under [Reporting levels](reporting.md#reporting-levels). Its output lists each request and unit once, under its latest state since the last report, with PR links. Any other reply stays plain and does not run `close`.

To list every coordinator on this machine, run `python3 ~/pstack-t3/skills/brigade/scripts/brigade.py walk` ([reference](../cli/brigade.md#brigadepy-walk)). If your checkout is not `~/pstack-t3`, use that checkout's `skills/brigade/scripts/brigade.py`. It groups coordinators by repository. Each repository's header shows its landing mode and queue. Each coordinator shows its reporting level, counts, thread, leases, and open decisions. A coordinator idle for more than 24 hours is marked. [Several coordinators on one repository](several-coordinators.md#several-coordinators-on-one-repository) shows the layout.

To stop one, ask it to close. It deletes its schedules, writes a last report, and unpins its thread. The store stays.
