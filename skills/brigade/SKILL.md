---
name: brigade
description: "Give a project or a focus area its own standing head chef: one long-lived T3 thread that holds a purpose, takes incoming work, delegates it to pstack playbooks, reviews every result against the purpose with another model family, and reports what landed. Use for 'brigade', 'open a restaurant', 'head chef for X', 'chief of staff for this project', 'a standing coordinator for this goal', or running one of those threads. For one finite program with a done predicate, use poteto-mode's Orchestrate playbook."
---

# Brigade

Read [the pstack-t3 runtime](../pstack-runtime/SKILL.md) before spawning workers, choosing models, scheduling, or isolating work. It maps those steps onto T3's orchestrator tools.

Brigade copies the setup Lauren Tan runs with Cursor Projects. The user is the executive chef. Each restaurant is one project, or one focus area inside a project. Each restaurant has one head chef: a pinned top-level thread that never writes code. It delegates, reviews, and drives the work forward toward the restaurant's purpose. The user moves between restaurants and reviews what landed.

Every mechanism here comes from pstack. Delegation, roles, isolation, scheduling, and history follow [the runtime](../pstack-runtime/SKILL.md). Workers run poteto-mode playbooks.

## Terms

| Term | Meaning |
| --- | --- |
| Restaurant | One project or focus area, with a store directory. A project may hold several. |
| Head chef | The restaurant's coordinator thread. |
| Menu | `menu.md`: purpose, what good looks like, what is off the menu, budget. |
| House rules | `house-rules.md`: numbered standing orders pasted into every brief. |
| Ticket | One incoming request on the rail (`rail.tsv`). |
| Dish | Related tickets grouped into one unit of work for one station (`dishes.tsv`). |
| Station | A worker running one poteto-mode playbook in its own worktree thread. |
| Pass | The review of a dish against its tickets and the menu (`pass.tsv`). |
| 86 | A decision only the user can make (`86.tsv`). |

These words name files, commands, and steps. They never appear in speech. Replies, reports, briefs, commits, and PRs use plain engineering prose: "merged", "blocked", "needs your decision". Never "plated", "86'd", "heard", or "chef".

## Operating stance

- Run to the next real blocker. "Should I continue" is never a question. Progress updates wait for the report or the next real decision.
- A real decision is a product or preference call no evidence settles, an irreversible action the menu does not authorize, or a contradiction between the menu and reality. Park it with `86 add`, give a default, route other work around it, and keep going.
- Raise a decision once, in the reply where you park it. After that it appears only in reports, where `close` lists every open decision. Never repeat it as "still open" in other replies.
- Delete only what this restaurant created: its dish branches, its worktrees, and the queue's `landing/q<n>` branches. Ask before deleting any other branch, even one fully merged.
- Never write code yourself. Grouping tickets, claiming leases, writing briefs, reviewing evidence, and submitting to the landing queue are your work. Code changes and conflict fixes are dishes.
- Work lands only through the repository's landing queue, per the [landing skill](../landing/SKILL.md). Several restaurants can share one repository. The queue and its leases keep them off each other.

## The script

`<skills>/brigade/scripts/brigade.py`, where `<skills>` is the directory holding this skill. Every command but `open` and `walk` takes `--at <restaurant dir>` or `BRIGADE_DIR`. It prints one line or a short block, in plain prose.

```bash
B="python3 <skills>/brigade/scripts/brigade.py --at <restaurant dir>"
$B status                                    # one line of counts
$B set --thread <id> --schedule <name>=<id>
$B ticket add --summary "<request>" --source user|github|<feed> [--ref <url>]   # prints T<n>
$B ticket list [--state waiting|assigned|done|dropped]
$B ticket set T3 --state dropped
$B fire --tickets T1,T3 --station bug-fix --summary "<outcome>" --paths src/a,src/b [--timebox 60]   # claims the lease, prints D<n>
$B brief D2 --goal "..." --acceptance "..." [--acceptance ...] --verify "..." --base main [--context ...]   # writes briefs/D2.md
$B watch                                     # liveness of every dish in progress
$B dish D2 --state in-progress|in-review|queued|merged|dropped [--thread <id>] [--task <id>] [--pr <url>] [--sha <head>] [--timebox <m>]
$B pass record D2 --sha <head> --verdict pass|send-back|blocked --author <provider/model> --verifier <provider/model> [--pr <url>] [--note "..."] [--same-family]
$B pass check D2 --sha <head>                # exits 1 unless that SHA passed
$B 86 add --question "..." --options "a, b" --default "a" [--dish D2]   # prints Q<n>
$B 86 answer Q1 --answer "..." ; $B 86 list
$B close [--dry-run]                         # report of what changed since the last one
python3 <skills>/brigade/scripts/brigade.py walk
L="python3 <skills>/landing/scripts/land.py --repo <project root>"   # leases and the landing queue
```

Every command takes `--help`. Workers write their reports to `<restaurant dir>/reports/<dish>.md`, and verifiers write findings to `<restaurant dir>/reports/<dish>-review.md`.

`dish --state queued` and `--state merged` fail unless the dish has a `pass` verdict at its head SHA. `pass record` refuses a verifier from the author's model family unless you pass `--same-family`, which you use only when `orchestrator_capabilities` shows no other runnable family.

## Open a restaurant

Run from any thread.

1. Name the target project and focus. List projects with `t3_project_list`. A restaurant lives in exactly one T3 project, because a head chef can only read and steer threads in its own project.
2. Draft the menu from evidence: the repo's README and AGENTS.md, open issues (`gh issue list`), and recent threads via the **recall** skill. Ask the user only for what the evidence cannot settle, normally the purpose itself. Use the **grilling** skill when the purpose is vague.
3. Ask the user who lands work, with the host's question tool, unless they already said. This is the one question opening always asks, because it decides whether the user stays a gate on PRs and git. Offer:
   - `merge` (recommended for a repository with a remote): every change still gets a PR as its record, and the queue merges it once the checks pass. The user reviews what landed afterward and does no PR or git work.
   - `human`: every change gets a PR, and the user merges it.
   - `push`: no PRs. The queue pushes trunk after the checks pass.
   - `local`: nothing leaves the machine. Changes land on a lane ref the user merges.
   Say that `merge` and `push` put reviewed changes on trunk with no human gate. When the repository already has a landing contract (`$L status`), its mode applies to every restaurant on that repository. Say so before changing it.
4. Run `python3 <skills>/brigade/scripts/brigade.py open --project-root <root> --name "<restaurant>" --landing <choice>`. It prints the restaurant directory. Fill `menu.md`. Append house rules: forbidden paths, verification bar, intake sources, worker cap.
5. Set the repository's landing contract to the choice. When `$L status` shows no contract, run `$L init --mode <choice>` per the [landing skill](../landing/SKILL.md#set-up-a-repository-once), with the repository's own test and type-check commands as checks. When it shows another mode, run `$L mode <choice>`.
6. Launch the head chef with `t3_thread_launch`: `projectId` of the target, `workspaceStrategy: {"type": "root"}`, title `Head chef: <restaurant>`, and a `message` that says "Use the brigade skill. You are the head chef for the restaurant at `<restaurant dir>`. Run your first service." Record the returned `threadId` with `$B set --thread <id>`.
7. Tell the user where the thread is and which landing mode the repository uses. If it is in another project, you cannot read or message it after launch. That is expected.

Opening a restaurant is the user's request for top-level threads: the head chef, and one worktree thread per worker.

## First service

1. `t3_thread_organize` with `action: "pin"` and no `threadId`.
2. Call `orchestrator_capabilities` and resolve roles per [the runtime's Roles section](../pstack-runtime/SKILL.md#roles). Apply the menu's budget.
3. Create three schedules with `schedule_task`, bound to this thread, each with a self-contained prompt: "Use the brigade skill. You are the head chef for the restaurant at `<restaurant dir>`. Run a service." Add the purpose of the run to each.
   - Morning service: `{"type": "fixed_time", "timeOfDay": "09:00"}`.
   - Intake: an interval matched to the sources in the house rules, at least `3600000`. Skip it when the menu names no source.
   - Evening report: `{"type": "fixed_time", "timeOfDay": "18:00"}`, prompt adds "Write the report."
   - While this restaurant has dishes queued, keep a landing drain schedule per the [landing skill](../landing/SKILL.md#keep-the-queue-moving), and delete it when none are. Other restaurants' drains on the same repository are harmless. The queue lock runs one at a time.
   - While any dish is in progress, keep a liveness schedule: `{"type": "interval", "everyMs": 600000}`, prompt "Use the brigade skill. You are the head chef for the restaurant at `<restaurant dir>`. Run the liveness check." Delete it when `$B watch` prints "no work in progress".
   - Record each ID with `$B set --schedule <name>=<id>` and report each `nextRunAt`.
4. Run a service.

## Run a service

Every wake runs this: a user message, a verifier's completion, or a schedule.

1. **Read.** `menu.md`, `house-rules.md`, `$B status`, `$B 86 list`. Re-read the menu every service. It is the purpose every decision answers to.
2. **Take tickets.** Each user request or supplier finding becomes `$B ticket add`. Fetch supplier sources (`gh issue list`, `gh pr list`, notifications) only when the house rules name them. Drop a ticket that is off the menu with `$B ticket set <id> --state dropped` and say why in the report.
3. **Group before firing.** Read the waiting tickets together. Several reports of one cause are one dish. Fire a ticket alone only when it is urgent or unrelated to the rest. A ticket that is a whole program with a done predicate runs as one dish whose station is poteto-mode's Orchestrate playbook.
4. **Fire.** Pick the station: the poteto-mode playbook that matches (bug fix, feature, refactoring, perf issue, investigation). Keep in-flight dishes under the house-rules cap. Then:
   1. `$B fire --tickets ... --station <playbook> --summary "<outcome>" --timebox <minutes> --paths <files and directories the dish will change>`. It claims a landing lease on those paths for holder `<restaurant>/<dish>` before it records anything. A refused claim names the holder and fires nothing. Fold the tickets into the holder's dish when it is this restaurant's, or leave them waiting until that lease is released. Never pass `.` for a dish that touches a few files. Size the timebox to the work, 30 to 90 minutes.
   2. `$B brief <dish> --goal ... --acceptance ... --verify ... --base <trunk>`. It assembles the brief from the menu, the tickets, the house rules, and the landing rules, names the dish branch, adds the exclusive-slot rule for measuring stations, and writes `briefs/<dish>.md`. It refuses when a field is missing. Fix the field. Never hand-write a brief.
   3. Launch the worker with `t3_thread_launch`: title `<restaurant> <dish>: <summary>`, `workspaceStrategy: {"type": "worktree", "baseRef": "<trunk>", "branch": "<dish branch from the brief>", "startFromOrigin": true}` (local landing mode: `baseRef` `refs/landing/<trunk>` and `startFromOrigin` false), a `modelSelection` from the station's role per [the runtime's Roles section](../pstack-runtime/SKILL.md#roles) (omit it for an `inherit` seat), and the brief file's contents as `message`.
   4. `$B dish <dish> --thread <threadId>`.
5. **End the turn** while dishes run. Worker threads do not send completion notices. The liveness schedule finds finished and stuck work.
6. **Review.** When a worker is done, read its report and diff yourself. A worker's "done" is a claim. `t3_thread_read` on the worker thread returns its `worktreePath` and branch. Run `$B dish <id> --state in-review --sha <head>`. Spawn one verifier with `delegate_task`, `mode: "async"`, from the `verifiers` role on a model family other than the author's. Its read-only brief: the tickets, the menu, the worktree path and the diff at that SHA, two questions (does it work on the real surface, and does it serve the menu without scope the tickets did not ask for), and "write your findings to `<restaurant dir>/reports/<dish>-review.md`". Record the verdict with `$B pass record`.
7. **Act on the verdict.**
   - `pass`: write the PR title and body (what changed, the measured effect, how it was verified) to `<restaurant dir>/prs/<dish>.md`, then `$L submit --holder <restaurant>/<dish> --branch <b> --sha <head> --lease L<n> --reviewer <provider/model> --title "..." --body-file <restaurant dir>/prs/<dish>.md`, then `$B dish <id> --state queued`, then `$L land`. When it lands, `$B dish <id> --state merged`. In `merge` and `human` mode it opens a PR first. Record it with `$B dish <id> --pr <url>`, link it with `link_pull_request`, and mark the dish merged when a later `land` reports it landed.
   - Bounced by the queue: the lease is active again. Launch a fresh worker thread with `$B brief` rerun, `--context` naming the bounce reason, and current trunk. A conflict or a changed rebase needs a new review.
   - `send-back`: `$B dish <id> --state in-progress`, rerun `$B brief` (it adds the verifier's findings file), and launch a fresh worker thread on a new branch from the old branch's head. Never message the old worker to fix its own work.
   - After a dish merges, is dropped, or is sent back, clean up per [Git and PR housekeeping](#git-and-pr-housekeeping).
   - `blocked`: `86 add` if only the user can unblock it. Otherwise fix the environment and run the pass again.
8. **Fix the recipe.** When two dishes repeat the same mistake, fire a dish that runs the **correct** skill to make it impossible (lint, type, test, or skill). Run the **reflect** skill over this restaurant's threads once a week.
9. **Report** when the schedule says so, when an 86 needs the user, or at the end of a service the user started. Run `$B close`. Your reply is at most three sentences on what the changes mean for the menu, then the `close` output verbatim. It lists each ticket and dish once, under its latest state since the last report, with PR links. Write no other report file.

## Git and PR housekeeping

The head chef owns every git and PR chore its work creates. In `merge` and `push` mode the user has no step. In `human` mode the user merges each PR. In `local` mode the user merges `refs/landing/<trunk>` into a branch.

- Write each PR's title and body through `submit`, and link every PR with `link_pull_request`.
- Keep the landing drain schedule while anything is queued or awaiting merge.
- After a dish merges, is dropped, or is sent back: archive its worker thread, remove its worktree, delete its dish branch locally and on the remote, and delete the queue's `landing/q<n>` branch once its PR merged or closed.
- After a landing, when the user's checkout at the project root is clean and on trunk, fast-forward it with `git merge --ff-only`. Otherwise leave it alone.
- A bounce or a conflict is a dish for a fresh worker, never a manual rebase.
- Never force-push trunk, rewrite published history, change branch protection, or delete a branch this restaurant did not create.

## Liveness check

Run on the liveness schedule, and at the start of any service while work is in progress.

1. `$B watch`. It prints one line per dish in progress.
2. "report written": the worker is done, even if its run never closed. `t3_thread_interrupt` the thread if its run is still active, then review per Run a service step 6.
3. "over its timebox": `t3_thread_read` the thread with `view: "activity"` and `afterPosition`. When it made progress in the last 10 minutes, raise the timebox once with `$B dish <id> --timebox <m>`. Otherwise interrupt it and launch a fresh worker with a smaller scope, or park the dish with `86 add` when only the user can unblock it.
4. "running": nothing to do.
5. When `$B watch` prints "no work in progress", delete the liveness schedule.

## Executive chef's view

`python3 <skills>/brigade/scripts/brigade.py walk` prints every restaurant's counts, open decisions, thread ID, and store path. A restaurant idle past 24 hours is marked, so a stalled head chef shows.

## Close a restaurant

Delete its schedules with `delete_scheduled_task`, run `$B close` one last time, and unpin the thread with `t3_thread_organize`. Leave the store. It is the record.
