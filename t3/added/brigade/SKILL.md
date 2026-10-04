---
name: brigade
description: "Give a project or a focus area its own standing head chef: one long-lived T3 thread that holds a purpose, takes incoming work, delegates it to pstack playbooks, reviews every result against the purpose with another model family, and reports what landed. Use for 'brigade', 'open a restaurant', 'head chef for X', 'chief of staff for this project', 'a standing coordinator for this goal', or running one of those threads. For one finite program with a done predicate, use poteto-mode's Orchestrate playbook."
---

# Brigade

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
| Station | A worker running one poteto-mode playbook. |
| Pass | The review of a dish against its tickets and the menu (`pass.tsv`). |
| 86 | A decision only the user can make (`86.tsv`). |

These words name files, commands, and steps. They never appear in speech. Replies, reports, briefs, commits, and PRs use plain engineering prose: "merged", "blocked", "needs your decision". Never "plated", "86'd", "heard", or "chef".

## Operating stance

- Run to the next real blocker. "Should I continue" is never a question. Progress updates wait for the report or the next real decision.
- A real decision is a product or preference call no evidence settles, an irreversible action the menu does not authorize, or a contradiction between the menu and reality. Park it with `86 add`, give a default, route other work around it, and keep going.
- Never write code yourself. Grouping tickets, writing briefs, reviewing evidence, and landing a verified commit are your work. Code changes, conflict resolution, and restacks are dishes.

## The script

`<skills>/brigade/scripts/brigade.py`, where `<skills>` is the directory holding this skill. Every command but `open` and `walk` takes `--at <restaurant dir>` or `BRIGADE_DIR`. It prints one line or a short block, in plain prose.

```bash
B="python3 <skills>/brigade/scripts/brigade.py --at <restaurant dir>"
$B status                                    # one line of counts
$B set --thread <id> --schedule <name>=<id> --merge-policy pass|pr-only|local-only
$B ticket add --summary "<request>" --source user|github|<feed> [--ref <url>]   # prints T<n>
$B ticket list [--state waiting|assigned|done|dropped]
$B ticket set T3 --state dropped
$B fire --tickets T1,T3 --station bug-fix --summary "<outcome>" [--task <taskId>] [--thread <threadId>] [--branch <b>]   # prints D<n>
$B dish D2 --state in-progress|in-review|merged|dropped [--pr <url>] [--sha <head>] [--task ...] [--thread ...] [--branch ...]
$B pass record D2 --sha <head> --verdict pass|send-back|blocked --author <provider/model> --verifier <provider/model> [--pr <url>] [--note "..."] [--same-family]
$B pass check D2 --sha <head>                # exits 1 unless that SHA passed
$B 86 add --question "..." --options "a, b" --default "a" [--dish D2]   # prints Q<n>
$B 86 answer Q1 --answer "..." ; $B 86 list
$B close [--dry-run]                         # report of what changed since the last one
python3 <skills>/brigade/scripts/brigade.py walk
```

Every command takes `--help`. Workers write their reports to `<restaurant dir>/reports/<dish>.md`.

Under merge policy `pass`, `dish --state merged` fails unless the dish has a `pass` verdict at its current head SHA. `pass record` refuses a verifier from the author's model family unless you pass `--same-family`, which you use only when `orchestrator_capabilities` shows no other runnable family.

## Open a restaurant

Run from any thread.

1. Name the target project and focus. List projects with `t3_project_list`. A restaurant lives in exactly one T3 project, because a head chef can only read and steer threads in its own project.
2. Draft the menu from evidence: the repo's README and AGENTS.md, open issues (`gh issue list`), and recent threads via the **recall** skill. Ask the user only for what the evidence cannot settle, normally the purpose itself. Use the **grilling** skill when the purpose is vague.
3. Run `python3 <skills>/brigade/scripts/brigade.py open --project-root <root> --name "<restaurant>" --merge-policy pass|pr-only|local-only`. It prints the restaurant directory. Fill `menu.md`. Append house rules: base branch, forbidden paths, verification bar, intake sources, worker cap.
4. Launch the head chef with `t3_thread_launch`: `projectId` of the target, `workspaceStrategy: {"type": "root"}`, title `Head chef: <restaurant>`, and a `message` that says "Use the brigade skill. You are the head chef for the restaurant at `<restaurant dir>`. Run your first service." Record the returned `threadId` with `$B set --thread <id>`.
5. Tell the user where the thread is. If it is in another project, you cannot read or message it after launch. That is expected.

## First service

1. `t3_thread_organize` with `action: "pin"` and no `threadId`.
2. Call `orchestrator_capabilities` and resolve roles per [the runtime's Roles section](../pstack-runtime/SKILL.md#roles). Apply the menu's budget.
3. Create three schedules with `schedule_task`, bound to this thread, each with a self-contained prompt: "Use the brigade skill. You are the head chef for the restaurant at `<restaurant dir>`. Run a service." Add the purpose of the run to each.
   - Morning service: `{"type": "fixed_time", "timeOfDay": "09:00"}`.
   - Intake: an interval matched to the sources in the house rules, at least `3600000`. Skip it when the menu names no source.
   - Evening report: `{"type": "fixed_time", "timeOfDay": "18:00"}`, prompt adds "Write the report."
   - Record each ID with `$B set --schedule <name>=<id>` and report each `nextRunAt`.
4. Run a service.

## Run a service

Every wake runs this: a user message, a child completion, or a schedule.

1. **Read.** `menu.md`, `house-rules.md`, `$B status`, `$B 86 list`. Re-read the menu every service. It is the purpose every decision answers to.
2. **Take tickets.** Each user request or supplier finding becomes `$B ticket add`. Fetch supplier sources (`gh issue list`, `gh pr list`, notifications) only when the house rules name them. Drop a ticket that is off the menu with `$B ticket set <id> --state dropped` and say why in the report.
3. **Group before firing.** Read the waiting tickets together. Several reports of one cause are one dish. Fire a ticket alone only when it is urgent or unrelated to the rest. A ticket that is a whole program with a done predicate runs as one dish whose station is poteto-mode's Orchestrate playbook.
4. **Fire.** Pick the station: the poteto-mode playbook that matches (bug fix, feature, refactoring, perf issue, investigation). Write the brief per the Orchestrate playbook's brief template (`../poteto-mode/playbooks/orchestrate.md`): goal, scope, context, acceptance, verify, report, plus the menu's purpose line, the house rules verbatim, and "write your report to `<restaurant dir>/reports/<dish>.md`". Open it with "Use the poteto-mode skill and its `<station>` playbook." Delegate per [the runtime](../pstack-runtime/SKILL.md#delegation): a bounded dish is a `delegate_task` child with `mode: "async"`, isolated per [Isolation](../pstack-runtime/SKILL.md#isolation). A dish that needs a PR owner is a `t3_thread_launch` worktree thread. Run `$B fire` with the task or thread ID and branch. Keep in-flight dishes under the house-rules cap.
5. **End the turn** while dishes run. Completions wake you. Never schedule a wake to wait for a child.
6. **Review.** On a completion, read the diff and evidence yourself. A worker's "done" is a claim. Run `$B dish <id> --state in-review --pr <url> --sha <head>`. Spawn one verifier from the `verifiers` role on a model family other than the author's. Its read-only brief: the tickets, the menu, the diff at that SHA, and two questions: does it work on the real surface, and does it serve the menu without scope the tickets did not ask for. Record the verdict with `$B pass record`.
7. **Act on the verdict.**
   - `pass`: land per the merge policy. `pass`: the owner merges, or you fast-forward a clean verified commit. `pr-only`: leave the PR for the user. `local-only`: merge into the base branch without pushing. Then `$B dish <id> --state merged` (or leave it `passed` under `pr-only`). Link every PR with `link_pull_request`.
   - `send-back`: a fresh worker with consolidated scope: the original brief, the verifier's findings, and the branch. Never resume the old worker.
   - `blocked`: `86 add` if only the user can unblock it. Otherwise fix the environment and run the pass again.
8. **Fix the recipe.** When two dishes repeat the same mistake, fire a dish that runs the **correct** skill to make it impossible (lint, type, test, or skill). Run the **reflect** skill over this restaurant's threads once a week.
9. **Report** when the schedule says so, when an 86 needs the user, or at the end of a service the user started. Run `$B close`. Your reply is at most three sentences on what the changes mean for the menu, then the `close` output verbatim. It lists each ticket and dish once, under its latest state since the last report, with PR links. Write no other report file.

## Executive chef's view

`python3 <skills>/brigade/scripts/brigade.py walk` prints every restaurant's counts, open decisions, thread ID, and store path. A restaurant idle past 24 hours is marked, so a stalled head chef shows.

## Close a restaurant

Delete its schedules with `delete_scheduled_task`, run `$B close` one last time, and unpin the thread with `t3_thread_organize`. Leave the store. It is the record.
