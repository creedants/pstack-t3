---
name: pstack-runtime
description: How pstack-t3 skills delegate, pick models, isolate work, schedule, and verify inside T3 Code through the orchestrator V2 tools. Read before running any other pstack skill that spawns workers, picks a model, or schedules work.
---

# pstack-t3 runtime

pstack-t3 runs inside T3 Code. Every provider T3 drives (Claude, Codex, Grok, Cursor, Pi, OpenCode, Muse, ACP agents) gets the same `t3-code` MCP server. This file maps each pstack concept onto those tools, so a skill works the same whatever model runs it.

Tool names may carry a harness prefix, such as `mcp__t3-code__delegate_task` or `mcp__t3_code__delegate_task`. The semantics are the same. If a T3 tool this skill names is not in your tool list, find it with the host's tool search when the host has one, and call it by the name the search returns. When the host has no tool search, call the tool directly. Start with `orchestrator_capabilities`, and do not conclude the tools are missing until a search or a direct call has failed. ACP agents that cannot see the tools use the terminal bridge in [ACP fallback](#acp-fallback).

## Vocabulary

| pstack says | In T3 |
| --- | --- |
| subagent, worker, delegate, reviewer, runner, judge | A child task created with `delegate_task`, owned by this thread. |
| cloud worker | A child task. T3 children run on this machine, so they can reach local files, browsers, and auth. |
| background, `run_in_background` | `delegate_task` with `mode: "async"`. |
| wait for a worker | A thread with a schedule of its own ends the turn, and its completions wake it. Any other thread keeps a bounded wait with `t3_thread_wait`. Call `task_status` for a result mid-turn. See [Delegation](#delegation) step 5. |
| cancel a worker | `task_cancel`. |
| model slug, role model | A target `{providerInstanceId, model, options}` from `orchestrator_capabilities`, resolved through roles. See [Roles](#roles). |
| `inherit-parent`, `auto` | `"inherit"`. Omit `target` so the child inherits this thread's provider, model, and options. |
| separate chat, coordinator chat, PR owner thread | A top-level thread from `t3_thread_launch`, only where [Top-level threads](#top-level-threads) allows it. |
| worktree for a worker | A worktree from `delegate_task` with `isolation: "worktree"`, or a `t3_thread_launch` worktree binding for top-level threads. See [Isolation](#isolation). |
| `/loop`, hourly tick, automation, scheduled wakeup | `schedule_task`. See [Scheduling](#scheduling). |
| `scripts/watch-pr`, a poll loop, waiting on CI | `watch_pull_request` on the thread that owns the PR. See [Pull request watching](#pull-request-watching). |
| transcript, chat history, cloud-agent URL | A T3 thread, read with `t3_thread_search` and `t3_thread_read`. |
| control-ui, browser MCP | T3 preview tools: `preview_open`, `preview_snapshot`, `preview_click`, `preview_type`, `preview_evaluate`, `preview_recording_start`. Use `preview_hover` to reveal a menu or tooltip, `preview_drag` to drop one element on another, `preview_select` to choose an option in a native select, `preview_upload` to give the page files, and `preview_dialog` to accept or dismiss a browser dialog. |
| ask the user (`AskQuestion`) | The host's question tool if it has one, otherwise a short question in the reply. |
| todolist | The host's todo tool if it has one, otherwise a checklist in the work log. |
| PR you opened or now drive | Register it with `link_pull_request`. |
| status page, dashboard, report table, chart | A page published with `html_preview`, then `html_render`, inside a reply already due. See [Visual reports](#visual-reports). |

## Deadlines

A deadline or timebox sets the order of work. It never waives a step. This holds for Autopilot's early red-test-and-PR target, a brigade or Orchestrate timebox, and any "aim for N minutes" in a brief. Start the deliverable the deadline names first, then run every step the playbook prescribes before the next gate, such as `CODE-READY` or the final report. That includes How, Architect, investigation, and the code delegate. The clock is never a `skip:` reason. When the remaining steps cannot fit, stop at a verifiable point and report the steps that remain instead of skipping them. A step that the brief's `Waived by mode:` line names is not a skip, because the mode removed it before the attempt started.

## Delegation

1. Call `orchestrator_capabilities` once per session before the first delegation, and again after a delegation fails on a target. It returns `parentThreadId` (this thread's own ID, for `t3_thread_read` on yourself), `inheritedProviderInstanceId` and `inheritedModel` (this thread's own seat), `providers[]`, each with `providerInstanceId`, `models[]` and their `options`, `canRunChildTask`, `canRunCrossProviderChildTask`, and `constraints`. Treat a provider with `canRunChildTask: false` as unavailable and report its `constraints` if a role needed it.
2. Resolve the role's targets per [Roles](#roles).
3. Spawn every independent child in one message:

   ```json
   {
     "task": "<self-contained brief>",
     "title": "<role>: <slice>",
     "role": "review",
     "mode": "async",
     "target": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}},
     "clientRequestId": "<skill>-<slug>-<seat>"
   }
   ```

   - `role` is one of `implementation`, `research`, `review`, `design`, `test`, `general`.
   - Omit `target` for an `inherit` seat.
   - After a `t3_thread_launch` or `delegate_task` call whose seat has `options`, read the applied options from the launch result's `modelSelection.options` or from `t3_thread_configuration` on the child's `childThreadId`, and never call again with the options removed. Each applied option is one object `{"id": <key>, "value": <value>}`. A missing option or a different value is a mismatch. On a mismatch, or when the call is refused for its `options`, stop that child. Report the tool's error text. Call `task_cancel` for a child task. Call `t3_thread_interrupt` and then `t3_thread_wait` for a thread. A Cursor harness sent `options` as a string, T3 refused the call, and the child then dropped the options.
   - OpenCode moves a long command to the background, tells the model to end its turn, and resumes the thread when the command finishes. T3 publishes the task's result when the first turn ends, and the later turn does not reopen the task. Paste this paragraph unchanged into the brief of every child seated on OpenCode.

     > Nobody resumes you after your turn ends, so a command left running in the background is lost. Run every long command in the foreground with a long enough timeout, for example a bash timeout of 600000 ms. If your shell moves a command to the background anyway, keep checking its output, with `sleep 60` between checks, until it finishes. Do not end your turn until your report is written.

     When an OpenCode child's task is terminal and the report file its brief named is missing, the task is not finished. Read its activity with `t3_thread_read`. While a later turn on its thread still runs, wait on it per step 5. Otherwise handle it per [Failure handling](#failure-handling).
   - Use a stable `clientRequestId` so a retried call does not spawn a duplicate.
   - Retain every returned `taskId` in your todo list or work log.
4. A child starts with only its brief. It sees none of this conversation. Put the goal, the exact paths or SHAs, how to verify, and the report shape in the brief. Point at files instead of pasting large context. Write tool steps as plain verbs ("read", "search the repo", "run"), because the child may be a different provider with different tool names. When the role entry `roles.py show` printed for the child's seat has `haikuBrief`, paste its paragraphs unchanged, in order, at the end of the brief, per [Claude Haiku 5.5](#claude-haiku-55). A code-writing child inside a poteto-mode playbook opens with the poteto-agent persona body. Below it, paste unchanged the lines `python3 <pstack-runtime>/scripts/roles.py mode --cwd "$PWD" --playbook <name> --attempt <kind> [--brief-mode <value> | --session-mode light]` prints, per [Modes](#modes). They start with the `Playbook: playbooks/<name>.md` line, such as `Playbook: playbooks/feature.md`, and carry the required `Mode:` line. Write no `Playbook:` line of your own, because a second one fails the check. See [Resolve and carry the mode](#resolve-and-carry-the-mode) for the `Attempt:` and `Waived by mode:` lines. Copy the printed `Mode:` value into `--brief-mode` on the `roles.py show` call that seats the delegate, and pass no other mode flag. Write that brief to a file and run `python3 <pstack-runtime>/scripts/roles.py check-brief <file>` before `delegate_task`. Exit 1 names what is missing. Fix the brief, run the check again, and pass the checked text unchanged. A seat that matches this thread's model, or an edit that looks small, is not a `skip:` reason for the code delegate.
5. Collect results.
   - If nothing else in this turn depends on the results, and this thread has a schedule of its own that wakes it, end the turn. Each completion wakes this thread.
   - A completion arrives only when the child's run ends. A child stalled on an approval request, or a run that stays open after its final message, never ends, so no wake comes. On T3 Code 0.0.46-nightly.20261015.3145 or later, a child's question for the user does wake this thread, per [Pending requests](#pending-requests). That wake queues behind this thread's active turn. Check for the question per [Failure handling](#failure-handling) while this turn stays open. A question that blocks the child also ends a `mode: "wait"` call, per the bullet below. That question sends no wake while the call blocks. An approval request still sends no wake. A thread with no schedule of its own, such as a worker running a brief, does not end its turn while a child is open. This holds even though the `delegate_task` tool text says to end the turn. A coordinator's liveness check is not this thread's schedule. Call `t3_thread_wait` on the child's `childThreadId` with `timeoutMs: 300000`. When it returns a terminal status, call `task_status` to read and acknowledge the result, because `t3_thread_wait` does not acknowledge it. When it returns `timedOut: true`, read the child with `t3_thread_read`, `view: "activity"`, and `afterPosition`. A child with no new item for 10 minutes is stalled. A child with a question that blocks it is not stalled, per [Failure handling](#failure-handling). Handle a stalled child per [Failure handling](#failure-handling), then wait on the next open child.
   - A child with a final assistant message that holds the result the brief asked for, no pending tool or child run, and no new activity for two minutes is stalled. A message that says more work follows does not hold the result. A last message that says the child waits on a background command does not hold the result either, so this two-minute rule does not apply to it. After you see that message while the child's run is still open, call `t3_thread_wait` on the child's `childThreadId` with `timeoutMs: 120000`. Then read its activity again and call `task_status`. Handle it per [Failure handling](#failure-handling) only when the wait timed out, `workState` is still `working`, that message is still the last item, and no tool or child run is pending. Otherwise the 10-minute rule above decides that child.
   - A child's run can end while its task stays open. A Claude child that leaves a background shell job running is one case. Then `t3_thread_wait` on its `childThreadId` returns at once, because an idle thread returns immediately, while `task_status` still reports `working` or `waiting_for_children`. Do not repeat that call. Wait with `t3_thread_wait` and `timeoutMs: 120000` on another open child whose run is still going. With none, wait on this thread's own run. Read the `activeRunId` of the `parentThreadId` from `orchestrator_capabilities` with `t3_thread_read`, and pass them as `threadId` and `runId`. Without `runId`, the call picks this thread's latest run, which can be a queued run already cancelled, and returns at once. This thread's run stays open during the call, so it returns `timedOut: true` after 120 seconds. Never wait with a shell `sleep`, which some harnesses refuse. After each wait, read the child with `t3_thread_read`, `view: "activity"`, and `afterPosition`, then call `task_status`. Count 10 minutes from the child's last activity item. A new item restarts the count, and a `task_status` check does not. A child with no new item for 10 minutes is stalled, even when its last message holds the result, because the two-minute rule above needs an open run. Handle it per [Failure handling](#failure-handling). When its run starts again, go back to the waits above.
   - If you need a result now, call `task_status` with the `taskId`. Reading a terminal result this way acknowledges it, so no completion notification follows. Process that result immediately, as if the notification had arrived. `workState: "result_available"` means done, and `summary` holds the result. `working` and `waiting_for_children` mean not done. `hasPendingChildRuns` does not change that. `hasPendingQuestion` does not change it either. On T3 Code 0.0.46-nightly.20261015.3145 or later it is `true` while a question blocks the child. See [Pending requests](#pending-requests). See [Failure handling](#failure-handling). Do not busy-poll. Do other work between checks.
   - `mode: "wait"` blocks for at most `timeoutMs`, ten minutes by default. Use it only for short children whose result gates the very next step. `waitTimedOut: true` does not cancel the child. Keep the `taskId`. On T3 Code 0.0.46-nightly.20261015.3145 or later, a question that blocks the child also ends the wait, with `hasPendingQuestion: true` and `waitTimedOut: false`. The task is still open. Answer the question per [Pending requests](#pending-requests). The task's completion still wakes this thread.
6. You own every child's output. Read the diff or the evidence yourself before you report it. A child's "done" is a claim, not a verification.

Same-provider native subagent tools (Claude's Agent tool, Codex's subagents) are fine for an `inherit` seat when they run the parent's model. Use `delegate_task` for every other seat, including same-provider seats on another model. Never substitute a top-level thread for a child task.

### Permissions

Children inherit this thread's runtime mode and interaction mode. Omit `runtimeMode` so the child inherits. Never raise it above the parent's, and never lower it. A child in `approval-required` stops at its first command on an approval request that no tool can answer, so it never finishes. A read-only reviewer is a read-only brief: say "do not edit files, commit, or push" in the task. T3 has no read-only flag that keeps MCP access, so the brief carries the constraint. Muse supports only `approval-required` and `full-access`. Under any other parent mode T3 runs a Muse child or launched Muse thread as `approval-required`, so it stalls at its first command. Read `runtimeMode` from `orchestrator_capabilities` before you seat Muse. Under a mode Muse lacks, replace the Muse seat with `"inherit"` per [Fallback](#fallback) and say why.

### Failure handling

- A target is rejected: call `orchestrator_capabilities`, then fall back per [Roles](#roles), and say which seat changed and why.
- A child fails or returns nothing usable: proceed with N-1 and record the dropout. Respawn once with a fresh child for a required slice. Never resume a failed child to fix its own work. When the failure carries a provider error, follow the next bullet before that respawn. A failure that reads `Killed: out of memory.` is an out-of-memory kill that T3 reports. Lower the load before you respawn that child. Run fewer children at once, or run builds through the slot in [Local command capacity](#local-command-capacity). Report a second kill as a blocker.
- Check a usage limit only for a failed child that did not return the brief's result. When a failed child's error names a usage limit, follow [failures.md](failures.md#usage-limit) before you respawn it.
- `hasPendingChildRuns` on `task_status` does not mean the child is still running. It reports later turns on the child's thread, even after the task ended, and it does not reopen the task, including a turn you start with `t3_thread_send`. Decide completion by `workState` alone. On T3 Code 0.0.46-nightly.20261008.2813 or later, a turn held in a stopped queue does not count, and startup recovery on that host repairs a task an older build left `working` behind a held queue.
- A stalled child (see [Delegation](#delegation) step 5): first call `t3_pending_request_list` with the child's `childThreadId` as `threadId`. On T3 Code 0.0.46-nightly.20261015.3145 or later, `task_status` also shows `hasPendingQuestion: true` while a question blocks the child. A child with a question that blocks it waits on an answer and is not stalled. Answer it per [Pending requests](#pending-requests), and count its 10 minutes again from the answer. Otherwise read its last items. When its last assistant message holds the result the brief asked for, use that message as the result and call `task_cancel` on the task. When its last item is an approval request that is still waiting, call `task_cancel` and respawn once with no `runtimeMode`. Otherwise call `task_cancel` and respawn once for a required slice.

### Fresh children by default

Give new work to a fresh child with consolidated scope: the original brief, every later directive, and the prior child's report and branch. Message an existing child thread (`t3_thread_send`) only when the new work strictly needs state that lives with it, such as uncommitted changes in its worktree or a dev server it runs.

A `t3_thread_send` to a child whose task has already published its result can start a separate task for that message. Read the send result for a `taskId`. When it carries one, retain it and collect that task per [Delegation](#delegation) step 5. Its `summary` holds the answer, and the original task's `summary` does not change. When the result carries no `taskId`, wait with `t3_thread_wait` on the `childThreadId` and read the answer with `t3_thread_read`.

## Roles

Roles let one skill run on whatever providers the user has. A role value is a list of seats. Each seat is `"inherit"` or a target.

### Where roles live

1. `.pstack/t3-roles.json` in the project root. A role here replaces the same role from the user file.
2. `~/.config/pstack-t3/roles.json`, or `$XDG_CONFIG_HOME/pstack-t3/roles.json`.
3. No file. The role resolves to `"inherit"`, per [Built-in defaults](#built-in-defaults).

Print the merged roles for the current project with:

```bash
python3 <pstack-runtime>/scripts/roles.py show --cwd "$PWD" --parent "<inheritedProviderInstanceId>/<inheritedModel>" [--brief-mode <value> | --session-mode light]
```

When the brief this call seats has a `Mode:` line, replace the bracket with `--brief-mode <value>`. Copy that line's bare value, and pass no other mode flag. When the brief has no `Mode:` line and the session is light, replace the bracket with `--session-mode light`, except for a user's explicit reflect, which passes `--session-mode full`. Otherwise delete the bracket. A call with no mode flag takes its mode from the roles files.

Always pass `--parent` with the values from `orchestrator_capabilities`. The saved catalog records whichever thread ran setup, so it never names this thread. Without `--parent`, `show` cannot settle an `inherit` seat on a thread whose model the exclude list refuses. `show`, `validate`, and `write` reject a malformed `--parent`. See [Excluded seats](#excluded-seats).

`<pstack-runtime>` is the directory holding this file. It sits next to every other pstack skill directory, so from a skill at `<dir>/swarm/SKILL.md` it is `<dir>/pstack-runtime`. Add `--role "<name>"` for one role. The output is small JSON with `source` per role. It resolves against the catalog snapshot that `setup-pstack` saved, if any. A role that reports `"seats": "catalog-required"` needs a catalog. Call `orchestrator_capabilities`. Paste that tool result into this quoted heredoc. If the catalog result is large, save it to a temporary file with the host's file tool and pass that path to `--catalog`.

```bash
python3 <pstack-runtime>/scripts/roles.py show --cwd "$PWD" --catalog - --parent "<inheritedProviderInstanceId>/<inheritedModel>" --role "<name>" [--brief-mode <value> | --session-mode light] <<'JSON'
<the orchestrator_capabilities JSON>
JSON
```

Do not reconstruct role defaults in the calling skill. Pass a file path to `--catalog` when the catalog is already a file. Setup does that. One temporary file feeds `show`, `write`, and the saved snapshot. Fill the bracket by the same rule as the first command.

### Role names

| Role | Seats | Used by |
| --- | --- | --- |
| `feature, refactoring` | 1 | Feature and Refactoring code delegates |
| `bug-fix` | 1 | Bug fix code delegates |
| `perf-issue` | 1 | Perf issue code delegates |
| `hillclimb` | 1 | Hillclimb experiment delegates |
| `judgment and prose` | 1 | Prose, judgment, briefs, synthesis |
| `hardest tasks` | 1 | Cross-cutting design, concurrency, subtle algorithms |
| `how explorer` | 1 | how |
| `how explainer` | 1 | how |
| `why investigators` | 1 | why, one seat model for every investigator |
| `why synthesizer` | 1 | why |
| `reflect tooling` | 1 | reflect |
| `reflect judgment, divergent, synthesizer` | 1 | reflect |
| `arena runners` | N | arena, one candidate per seat |
| `arena cross-judge pool` | N | arena, pick one seat whose model family differs from the parent's |
| `swarm workers` | 1 | swarm, one seat for every worker |
| `skill tests` | 1 | pstack-author-skill fresh-child test and description eval |
| `architect runners` | N | architect, one runner per seat |
| `interrogate reviewers` | N | interrogate, one reviewer per seat |
| `verifiers` | N | swarm-verify, autopilot, shipping, orchestrate verification |
| `review backups` | N | `roles.py backup`, when `review backups` is set and no `verifiers` backups seat is left |

### Built-in defaults

Before you resolve a role's seats or weigh agreement between seats, read [roles.md](roles.md).

Read [roles.md](roles.md) before you write or validate a roles file, or when `roles.py` prints a note this section does not explain.

There are no built-in model picks. A role set in no roles file resolves to `"inherit"`, with the note `unset in roles.json, so it inherits this thread's seat; run setup to choose a seat`. When the exclude list refuses the inherited parent, the role resolves to what [Excluded seats](roles.md#excluded-seats) gives instead, and it carries no unset note. The one exception is `review backups`, which stays `"unset"` with its own note. `roles.py show` owns this mapping. A skill must not rebuild it.

A model family is the leading word of the model ID after the normalizer runs. `claude-opus-5-5` is `claude`, `gpt-6.1-sol` is `gpt`, and `grok-4.7` is `grok`. The normalizer in [roles.md](roles.md#claude-haiku-55) runs first, so `us.anthropic.claude-sonnet-5-5-v1:0` is `claude`. Diversity rules compare families, never providers, because one provider can serve another's models.

### Excluded seats

Read [roles.md](roles.md) before you write or validate a roles file, or when `roles.py` prints a note this section does not explain.

The roles file may carry `"exclude"`, a list of rules that refuse a seat. An entry is an object with at least one of `model` (a glob), `options` (an object of option key to value), `provider` (a glob on the provider instance id), and `roles` (a list of role names the rule applies to; an absent `roles` applies to every role). A string entry is refused. A rule holds when every part holds. A model glob matches the seat's bare id or its normalized id. An option pair holds only when the seat declares that option at that value. A rule with no model applies to every model. A seat the exclude list refuses is refused in each place a new seat is chosen: a configured role seat, a backup candidate, and an inherit replacement. The refusal sentence is `the roles file's exclude list refuses this seat for this role`.

[roles.md](roles.md#excluded-seats) lists the exact rule shape and the per-command behavior. In short, `show` skips a refused configured seat with a note, `validate` lists each refused seat and exits 1, and `write` refuses a seat the rule matches on its own form, even with `--force`. An `inherit` seat whose parent the exclude list refuses becomes that provider's first model the list allows, with the note `inherit replaced by <provider>/<model>`. When that provider cannot run children or has no allowed model, `show` refuses the role.

The Cursor `skill tests` rule stays, for a technical cause: the cursor driver cannot launch seats. It is matched by `driverKind` (`runs_on_cursor`), not by instance id. `show` adds a note on the `skill tests` entry when a resolved seat runs on the cursor driver. `validate` prints the same sentence as a problem and exits 1. `--launches-seats` refuses a configured skill tests seat that resolves to the cursor driver. An unset `skill tests` keeps the note and is not refused.

### Budget

Read [roles.md](roles.md) before you write or validate a roles file, or when `roles.py` prints a note this section does not explain.

The config may carry `"budget"`: `default`, `small`, `medium`, `large`, or `unlimited`. It caps the reasoning option of every seat that has one at `medium`, `high`, `xhigh`, or the highest level at or below `max`, or the closest lower value the model offers. `default` leaves options alone. `ultracode` and `ultrathink` are never chosen by a budget.

### Fallback

Read [roles.md](roles.md) before you write or validate a roles file, or when `roles.py` prints a note this section does not explain.

There is no built-in order. A configured seat whose provider is not runnable, or whose model is not in the catalog, falls back on its own. A configured seat the exclude list refuses never takes this path. A `review backups` seat never takes it either. `show` drops a `review backups` seat with a note instead. [Excluded seats](#excluded-seats) says how each command handles it.

1. When the model is missing, use the same provider's first model the exclude list allows. When it allows none, `show` refuses the seat with the exclude sentence `the roles file's exclude list refuses this seat for this role`.
2. If the provider is not runnable, use `"inherit"`, settled per [Excluded seats](#excluded-seats).
3. Say which seat changed and why. Never silently drop a seat, because the seat count is the panel size.

### Claude Haiku 5.5

Read [roles.md](roles.md) before you write or validate a roles file, or when `roles.py` prints a note this section does not explain.

No role uses Claude Haiku 5.5 by default. When a role's resolved seat runs Haiku 5.5 by normalized model id, `roles.py show` prints the Haiku 5.5 brief as `haikuBrief` on that role entry. An `inherit` seat on a Haiku 5.5 thread counts too. Paste it per [Delegation](#delegation) step 4.

A child launches seats when it passes a `target` to `delegate_task` or a `modelSelection` to `t3_thread_launch`. A skill-test child that launches seats resolves with `roles.py show --role "skill tests" --launches-seats`. Pass exactly one `--role "skill tests"`, a catalog, and `--parent`. That form refuses a configured seat that runs on the cursor driver, including a configured `inherit` on a cursor thread. An unset `skill tests` keeps the note and is not refused.

## Modes

pstack-t3 runs each piece of work in `full` or `light` mode. Full mode runs every step as each skill says. Light mode keeps every check that decides whether code lands and cuts the fan-out around it. A skill or playbook that spawns links here. Its light behavior is the row in [light.md](light.md) that names it, and its full behavior is its own text.

### Resolve and carry the mode

`roles.py mode` and `roles.py show` resolve one effective mode. The highest level present wins. The order is the brief, then the session, then the coordinator, then the project file `.pstack/t3-roles.json`, then the user file `roles.json`, then `full`.

- A brief's `Mode:` line is the frozen decision. A child copies that line's bare value into `--brief-mode` on every `roles.py mode` and `roles.py show` call it makes, and passes no other mode flag. It never resolves the mode again.
- The user's words set the session. `$poteto-mode light`, "light mode", or "use light mode" in a request sets this thread to light. "full mode" sets it back. A light session passes `--session-mode light` on every `roles.py` call that has no brief value, except the explicit reflect below.
- With neither, pass no mode flag. The roles files decide, and a missing key is `full`.
- A user's explicit reflect passes `--session-mode full` on every `roles.py show` call it makes, whatever the session or the roles files say. A call with no mode flag would fall through to a roles file that stores light. The session stays light after it.

Every brief whose child resolves seats or spawns carries the mode. Get the lines from `roles.py mode`, never from memory.

```bash
python3 <pstack-runtime>/scripts/roles.py mode --cwd "$PWD" [--playbook <name> --attempt <kind>] [--brief-mode <value> | --session-mode light]
```

- A code delegate's brief passes `--playbook <name> --attempt <kind>`. `<kind>` is `first` for new work, `fix` after a send-back, and `bounce` after a queue bounce. Paste the printed lines unchanged under the persona body. They start with the `Playbook:` line, so write no `Playbook:` line of your own. The printed lines include `Attempt:`. Under `Mode: light` they also include any `Waived by mode:` line. Under `Mode: full` there is no `Waived by mode:` line, and the brief must not add one. See [Delegation](#delegation) step 4.
- An owner, sub-coordinator, unit worker, visual-parity owner, or landing writer is not a code delegate. Run the command without `--playbook` and `--attempt`, and paste its `Mode:` and `Mode source:` lines into the brief or the `message`. Right after them, paste the seat rule below unchanged. A launched thread has no parent to ask, and a `Mode source: session` line reads like a session of its own without it.

  ```text
  Seat rule. Copy the Mode value above into --brief-mode on every roles.py mode and roles.py show call you make, and pass no other mode flag. Never pass --session-mode. Mode source names where your launcher's decision came from. It does not make this thread a session.
  ```
- A read-only leaf, such as a `how` explainer or a reviewer, gets no `Mode:` line. It resolves no seat.
- A light session with no brief runs the same command with `--playbook <name> --attempt first` for its own playbook. Its `Waived by mode:` line names the steps this thread skips.

Before a light `roles.py show` or `roles.py backup` call, read [roles.md](roles.md#budget).

An explicit budget wins. Pass the live catalog and `--parent` on every light `show` call, per [Roles](#roles), so a configured `inherit` seat of a capped role gets the cap.

Escalation only moves work toward `full`. `roles.py mode` prints `Mode: full` and `Mode source: escalated: <reason>` when a lease covers a pattern in the project's `"escalate"` list, at the second send-back, or when `--escalated` carries a recorded reason. A light thread that finds its design contested does not run `interrogate`. A thread whose brief has a `Mode:` line stops at a verifiable point and reports `Contested: <reason>` under its status. A thread whose mode came from the session or the roles files switches that work to full, announces the escalation, and runs the step full mode prescribes, such as `interrogate`. Nothing moves work from full to light mid-flight.

### Light behavior

In light mode, run the row for the spawn you are about to make. A step its row does not change runs as the skill says. A smaller fan-out in a row is how that step runs, not a waiver. Waived steps come only from the brief's `Waived by mode:` line, which `roles.py mode` prints from `LIGHT_WAIVERS`. Never add a waiver of your own. No light fan-out wave has more than 3 children in flight. A wave with more slices batches them into 3 briefs and keeps every slice covered.

In light mode, read [light.md](light.md) before every spawn and run the row for that spawn.

A send-back in light mode is a fix attempt with a fresh worker. Its brief carries the findings, the old head, and the `Attempt: fix` lines `roles.py mode` prints.

### Never cut

- Tests and the build. The worker runs the project's test command and build before every gate review, and the landing queue reruns them.
- The landing queue's checks.
- One review by another model family at every head SHA before merge, per [Gate review](#gate-review).
- A review reads the whole diff against its base on round one. A later round reads the fix diff since the last reviewed SHA, per [Rounds after the first](../brigade/verdict-protocol.md#rounds-after-the-first).
- Owner fences. Leases, `--owner` generations, worktrees, and the pass-at-head gate.
- A fresh worker for every send-back, per [Fresh children by default](#fresh-children-by-default).
- The code delegate, with the persona, the `roles.py mode` lines, and `roles.py check-brief`.
- One executing fresh-child test per changed spawn or coordination behavior.

### Gate review

Light mode removes the panels that give full mode its model diversity. The gate review puts one read by another model family back before merge. Full mode keeps each playbook's own review steps.

Every child that acts as the gate review runs steps 1 to 4. That includes the audit lane, the Shipping verifier, a cheap Orchestrate unit's gate, and a trail reviewer that runs with no gate at its head. Its step 2 brief adds that child's own verification tasks below the contract. Launch no second child for the same gate.

1. **Seat.** Resolve `verifiers` per [Roles](#roles) with this work's mode flag. Take the first seat whose model family differs from the author's. The author is the code delegate's `providerInstanceId/model`, or this thread's when it wrote the code. When several models wrote the diff, the verifier's family differs from all of them. When no runnable seat qualifies, the verdict is `blocked`.
2. **Brief.** A read-only brief. On round one, give the base ref and the full head SHA, and ask for a read of `git diff <base>...<head>`, the whole diff. On round two and later, give the previous reviewed SHA and the previous round's review file, ask for a read of `git diff <previous reviewed sha>...<head>`, and add a `FOCUS` section that carries the round rules from [Rounds after the first](../brigade/verdict-protocol.md#rounds-after-the-first). Paste the body of `agents/comment-sicko.md` unchanged and ask for its findings on the diff. Right after that body, paste the gate contract below unchanged. Add the child's own verification tasks after the contract. Name the chosen design's path when the light architect step picked one of two sketches. Name the decision log path and the run's `threadId` when the run kept a trail per `show-me-your-work`, and ask the reviewer to run that skill's audit checks itself with `t3_thread_read`. Flag weak evidence, skipped or unproven verification, misleading readiness, and a shell success that hides a failed check.

   ```text
   Gate review contract. These rules override the persona above and the tasks below where they differ.
   Do not edit files, commit, or push. Do not post on the PR. Return your report to the parent.
   Launch no child task, thread, or subagent. Do not run the how, why, architect, or interrogate skill. Do not launch show-me-your-work's trail reviewer.
   Read the diff the brief names and the nearby code yourself. You may run git log -L and git blame. Run the named verification commands yourself.
   When a claim or finding needs investigation beyond those reads, return send-back. Name the file, the line, the claim, and the question the fix must answer.
   Report each comment the persona would delete as a send-back finding with its path and line. Make no edit.
   End with pass, send-back, or blocked, the full head SHA, the author, and the verifier.
   ```
3. **Verdict.** `pass`, `send-back`, or `blocked`, with the full head SHA, the author, and the verifier. Record it in this run's work log. The parent posts the verdict where the child's playbook posts a report.
4. **After.** A `send-back` or `blocked` stops the PR. A fresh code delegate fixes the findings, and a new gate runs at the new head SHA. On `pass`, write `Gate: pass at <short sha> by <provider/model>` under the PR body's `## Verification`. The SHA is the PR's current head. Keep one `Gate:` line.

A worker whose brief carries `Gate: brigade` runs no gate of its own, because the coordinator's verifier is its gate.

Launch a new gate unless a current qualifying verdict exists at the exact head SHA.

- **Current.** It is the latest verdict for that SHA. A later `send-back` or `blocked` at the same SHA voids an earlier `pass`.
- **Qualifying.** It is `pass`, and the author and verifier are of different model families.

Before you reuse a gate verdict, read [Reusing a verdict](gate-review.md#reusing-a-verdict). That file holds the two sources and the four exclusions and states the patch-id split. For a brigade item, the coordinator source runs `pass check <item> --sha <head> --json`.

A lane receipt never stands in for the gate review. Read the head again right before you create the PR, mark it ready, or merge. When it moved, apply this section again.

### Announcement

When light mode applies, say so once, at the start, in two or three short sentences. Name the source from the `Mode source:` line, what this work cuts, and what still runs, as in "Light mode is on, from this session. This change gets one design sketch and no separate comment review. Tests, the build, and a review by another model family still run." On an escalation, name the rule, as in "This change runs in full mode because its lease covers `land.py`." A worker's report repeats its brief's `Mode:` and `Waived by mode:` lines under its status. A step that line names is not a deviation. Name the mode again only when it changes.

## Isolation

Two writers never share a checkout (principle-separate-before-serializing-shared-state).

Read [host.md](host.md) when a thread settles, snoozes, or runs on a host older than the versions this file names, or before you install or locate skills.

Before you end a worktree turn that can settle, read [Settle on turn end](host.md#settle-on-turn-end).

Before you delegate a child that needs a chosen branch or a different base, read [Older hosts](host.md#older-hosts).

- Read-only children share the current checkout.
- On T3 Code 0.0.46-nightly.20261014.3093 or later, a writing child that may overlap with another writer uses `delegate_task` with `isolation: "worktree"`. T3 branches from the parent's current local commit and runs project setup. Blocking setup finishes before the child starts. An async setup action can keep running. Uncommitted edits are not copied. The brief tells the child to report its branch and head SHA. Read the child's binding with `t3_thread_read` and check its diff before integrating.
- Long-lived owners that should appear in T3's sidebar with their own binding (PR owners in Autopilot and Orchestrate) are top-level threads launched with a worktree strategy. See below.
- T3 storage cleanup can remove that worktree after the thread ends. See [Local state](#local-state).
- Uncommitted changes are not copied into new worktrees. Commit or stash first, or point the brief at a pushed branch.

## Local command capacity

Children, launched threads, and this thread run on this machine, so their builds and tests share its cores. Run each local build, test suite, and verifier rerun through `python3 <skills>/landing/scripts/land.py slot -- <command>`, where `<skills>` is the directory that holds `<pstack-runtime>`. Run each benchmark or timing measurement through `python3 <skills>/landing/scripts/land.py slot --exclusive -- <command>`. It must be the outermost slot. The slot count, its config file, and the landing queue's reserved slot are in the landing skill's [Capacity](../landing/SKILL.md#capacity) section. A brief links that section and does not copy them.

- A slot wraps one command and ends when that command exits. Never hold a slot across a model turn, a `t3_thread_wait`, a `task_status` check, a `watch_pull_request` wait, or any other wait on children. Start dev servers with the preview tools, not in a slot.
- Slots limit heavy commands, not agents. Do not cap owners, children, or launched threads at the slot count.

## Top-level threads

Create top-level threads only when the user asked for separate threads or invoked a playbook or skill that names them (Orchestrate, Autopilot-full, Autopilot-stack, brigade). Invoking those is that request. Everything else uses child tasks.

Before you call `t3_thread_configure`, `t3_queue_promote_to_steer`, `t3_thread_fork`, `t3_thread_merge_back`, or `run_scheduled_task_now`, or pause a finite program's schedule, read its section in [threads.md](threads.md).

```json
{
  "title": "PR owner: <slug>",
  "workspaceStrategy": {"type": "worktree", "baseRef": "main", "branch": "pstack/<slug>", "startFromOrigin": false},
  "message": "<brief>",
  "modelSelection": {"instanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}}
}
```

- `modelSelection` is the seat with `providerInstanceId` renamed to `instanceId`, `model` copied, and `options` copied unchanged as the same object. Omit `modelSelection` for an `inherit` seat.
- Confirm a launched thread per [Delegation](#delegation) step 3.
- For a stack, `baseRef` is the parent branch and `startFromOrigin` is false.
- Omitted `workspaceStrategy` means the project root, not your worktree.
- `t3_thread_launch` requires a full-access or default caller. In `approval-required` or `auto-accept-edits` it fails. Then fall back to child tasks isolated per [Isolation](#isolation), and tell the user that owners are children rather than threads.
- `t3_thread_launch` has no retry key. Retain the `threadId`. After an error or lost response, check `t3_thread_list` before retrying. Report it to the user as a thread link per [History](#history).
- Follow a thread with `t3_thread_wait` and read it with `t3_thread_read` (use `afterPosition` to read only what is new). Send follow-ups with `t3_thread_send`, interrupt with `t3_thread_interrupt`.
- A thread launched with `t3_thread_launch` has no parent. Its finished turn does not wake the launcher. A launcher that needs a report names the message the launched thread sends with `t3_thread_send`.
- Autopilot-full, Autopilot-stack, and Orchestrate owners send their report lines to the root or coordinator with `t3_thread_send` and `mode: "auto"`. `auto` starts an idle recipient, steers a fully active turn, and queues behind a turn that cannot accept steering yet. It does not merge reports into one turn. The recipient handles every report, steered or queued, and runs the playbook's head-specific checks on the head each report names. Arrival order never makes a head current. Brigade's event lines to an executive admin stay on `mode: "queue"`, as [Reporting to an executive admin](../brigade/SKILL.md#reporting-to-an-executive-admin) states.
- A correction you queued earlier to an active owner or coordinator can sit behind a long turn. Read [Promote a queued correction](threads.md#promote-a-queued-correction) before you deliver it. Never promote brigade's event lines to an executive admin, a digest message, or a message that needs a turn of its own.
- Change an existing launched thread's model with `t3_thread_configure` only when a playbook step already calls for that owner to run on another model, such as Orchestrate's tool-error retry. Never call it because an owner keeps failing a gate. Read [Configure an owner](threads.md#configure-an-owner) before the call.
- `create_threads` makes up to 20 threads sharing this checkout. Use it only for read-only fan-out the user wants visible as threads.

### Forks

Before you call `t3_thread_configure`, `t3_queue_promote_to_steer`, `t3_thread_fork`, `t3_thread_merge_back`, or `run_scheduled_task_now`, or pause a finite program's schedule, read its section in [threads.md](threads.md).

`t3_thread_fork` starts a new top-level thread from another thread's context. `t3_thread_merge_back` moves context between related threads in one project. Use them only for the cases [Forks](threads.md#forks) allows. A fork never stands in for a child task, an Orchestrate sub-coordinator, or a fresh review round, and `task_status` does not apply to it. Read the fork with `t3_thread_read` for its `worktreePath`, `branch`, and `activeRunId` before you send anything. Treat a checkout it shares with another thread as read-only. Writers stay isolated per [Isolation](#isolation).

## Scheduling

`schedule_task` creates recurring work in T3's scheduler. It runs even when no turn is active, so it replaces `/loop`, Cursor automations, and hourly ticks.

Before you call `t3_thread_configure`, `t3_queue_promote_to_steer`, `t3_thread_fork`, `t3_thread_merge_back`, or `run_scheduled_task_now`, or pause a finite program's schedule, read its section in [threads.md](threads.md).

```json
{"title": "autopilot tick", "prompt": "<self-contained tick prompt>", "schedule": {"type": "interval", "everyMs": 3600000}, "clientRequestId": "<skill>-<slug>-tick"}
```

- Pass `schedule` as an object. `everyMs` is at least 60000. Wall-clock runs use `{"type": "fixed_time", "timeOfDay": "09:00", "weekdays": [1,2,3,4,5]}`.
- Runs post into this thread by default. Set `bindToCurrentThread: false` only when each run should start a fresh thread.
- The tick prompt must stand alone. Point it at the work log or store so a run can rebuild state from disk.
- Report the returned cadence and `nextRunAt`. Delete the schedule with `delete_scheduled_task` when the done predicate holds. List with `list_scheduled_tasks`.
- Pause a schedule with `update_scheduled_task` and `enabled: false`. Resume by setting it back to true.
- Never pause a schedule that renews a lease, drains queued work, consumes standing intake, or notices a merge. A required merge heartbeat and a fallback heartbeat beside `watch_pull_request` keep running. This rule never pauses a brigade schedule. Brigade holds an item on a decision and keeps its liveness schedule renewing the lease. Read [Pause a finite program](threads.md#pause-a-finite-program) before you pause a schedule.
- Do not schedule a tick to wait for a child task. Child completions wake this thread, and [Delegation](#delegation) step 5 bounds the wait for a child that never completes.
- Do not schedule a tick to wait on a pull request's checks, reviews, or conflicts. That wait is [Pull request watching](#pull-request-watching). Keep `schedule_task` for a cadence with no PR event. Beside a watch, a fallback heartbeat uses `everyMs` of at least `3600000`. A required heartbeat whose job is to notice a merge may use `900000`, as that section states.
- To run an enabled `interval` schedule's work now, read [Run a schedule now](threads.md#run-a-schedule-now). Never run a paused schedule before the answer permits work.

## Local state

Private working state that must survive the session but never be committed lives under `${XDG_STATE_HOME:-~/.local/state}/pstack-t3/`. Orchestrate stores go in `orchestrate/<project-slug>/`, private plans in `docs/<project-slug>/`. Children, launched threads, and scheduled runs reach it by the same absolute path from any worktree. Pass that path explicitly in every brief and tick prompt.

Resume notes from Pause safely go to `.pstack/resume/<slug>.md` in the repository, untracked (add `.pstack/` to `.git/info/exclude`), and are also posted as the thread's final message. Rely on the final message, because storage cleanup can delete the file with its worktree.

After a T3 restart, or when you resume work after one, follow [failures.md](failures.md#after-a-restart) before you read a saved child's result, replace a child, or use a saved worktree path. Resume through the thread's binding with `t3_thread_read`, and check that `worktreePath` exists before you use a saved path.

## Verification surfaces

Before you verify a change in a UI, on a device, or in a CLI, read [verification.md](verification.md) and use the surface it names.

A project `verify-*` skill beats all of these when one exists.

## Visual reports

A page is for a reply that is already due. When the playbook that runs this thread sends a reply, and a table of units, a timeline, a frontier, or a chart would carry it better than prose, publish that part as a page. A short status with no table stays text.

1. Write one self-contained HTML document. Every number and link on it comes from the store or the tables the text report would read.
2. Call `html_preview` with it. Fix every console error and every clipped or overlapping element. When the page holds a wide table, preview it again with `width: 390`.
3. Call `html_render` with the document, a `title`, and the preview's `contentHeight` as `height`, raised to 80 or capped at 2000. A capped frame scrolls the rest.
4. Write the reply after the render. It does not announce the page and adds only what the page does not show. Every decision that waits on the user, every PR link, and the store and report paths stay in the reply text, so a capped frame never hides them.

Leave `html`, `body`, and the outermost element with no background color, and style the page with the theme variables `html_render` describes. When either tool is missing or `html_render` fails, send the same facts as text.

A rendered page is a reply the user reads, even with no reply text. Render none on a wake that sends no reply. Brigade at `digest` renders no page except the one [Agent activity](../brigade/SKILL.md#agent-activity) names, so its replies stay in the plain form [Digest messages](../brigade/SKILL.md#digest-messages) sets. At every level, step 9 of brigade's [Run a service](../brigade/SKILL.md#run-a-service) decides which wakes reply before any page is drawn.

## History

Read [host.md](host.md) when a thread settles, snoozes, or runs on a host older than the versions this file names, or before you install or locate skills.

Before you use a saved transcript file from another environment, read [History notes](host.md#history-notes).

Before you read Claude conversation history, read [History notes](host.md#history-notes).

- Find prior work: `t3_thread_search` with a topic, branch, or PR number. It covers the current project.
- Read it: `t3_thread_read` with `view: "messages"` for the conversation, `view: "activity"` for tool activity. Page with `afterPosition`. Read the worker's explicit report and the result its brief required before treating narration as a decision or completion. Use the activity view to check the turn's order, and collect a child task with `task_status` per Delegation step 5.
- Page long stored items with `itemId` and `textOffset`. On T3 Code 0.0.46-nightly.20261014.3105 or later, new file-change items keep only a bounded prefix of their edit text. Paging cannot recover the discarded suffix. Read `git diff <base>...<head>` in the worker's checkout for the whole change.
- A thread the user attached as context is readable even outside this project.
- Child tasks are threads too. A `childThreadId` from `delegate_task` is readable with `t3_thread_read`.
- `t3_thread_read` returns the thread's `worktreePath` and `branch`. `t3_thread_list` does not, so finding the threads bound to a worktree takes one read per thread. Threads from other projects are not visible.
- Link a thread in a user-facing report as a Markdown link whose text is the thread's title and whose target is `t3-thread://v1/<threadId>`. Copy the `threadId` exactly as a tool returned it. Do not URL-encode it, decode its `%` escapes, or add an environment ID. T3 Code shows the thread's current title. List, read, and launch results carry no `link` field, so build the link from `threadId` or `childThreadId`.

## Pull requests

After you open a PR or start driving an existing one, call `link_pull_request` with its full URL. For a stack, link every layer. Linking attaches the PR to the calling thread. When a child opens a PR, it links it and the parent links it too. When a launched thread opens a PR, it links it and the launcher or coordinator links it too. Linking twice is safe. Before finishing PR work, call `list_thread_pull_requests` and link any missing PR. Report a link failure instead of claiming the PR is linked.

## Pull request watching

An owner that may already be settled reads [Settle on turn end](host.md#settle-on-turn-end) before calling `watch_pull_request`.

Call `watch_pull_request` after `link_pull_request`, when this thread is waiting on that PR's checks, reviews, or conflicts. Pass the PR URL, or the repository and number. T3 links the PR first if this thread has not linked it yet.

T3 checks the open PR every two minutes and wakes this thread when checks, reviews, or conflicts need it. Only comments posted after the call wake you, so handle the comments already on the PR, then end the turn.

One thread may hold several watches. Comments from the user's own account do not wake a watch, so a review left from that account needs another wake or the heartbeat. T3 documents both in [source control](https://github.com/pingdotgg/t3code/blob/main/docs/user/source-control.md).

A wake is news, not a merge decision. Read the PR and decide yourself before you merge.

A subagent cannot watch. The parent thread owns the PR. The child finishes and reports back. The thread that owns the PR calls `watch_pull_request`.

Call `unwatch_pull_request` when this thread stops driving the PR and hands that work back to the user. An interim status reply keeps the watch. The PR stays linked. While T3 watches, the thread stays in the user's Working list. Unwatching returns the thread to their inbox.

A merge ends the watch and does not wake the thread. A close ends the watch and does wake the thread. Watching also ends when the thread settles or is archived, when the user stops the thread, or when you call `unwatch_pull_request`. It also ends when T3 fails to read the PR 8 times in a row. A host rate limit only delays the next read. A watch ends after 10 wakes in a row that bring only comments, and that end posts a wake. Call `watch_pull_request` again after that wake when the loop is still running.

Read [host.md](host.md) when a thread settles, snoozes, or runs on a host older than the versions this file names, or before you install or locate skills. Never settle an owner whose PR watch or `schedule_task` loop is still needed, because settling ends the watch.

pstack's `scripts/watch-pr` poll, a foreground `--watch`, and an interval tick that waits for CI, a review, or a conflict all become this call. The forge commands that classify a verdict stay. Run them after a wake. They are not the wait.

`schedule_task` stays for a cadence with no PR event, such as an hourly audit, a morning report, or a soak. Beside a watch, a fallback heartbeat uses `everyMs` of at least `3600000`. When the wait is for the merge itself, add a `schedule_task` heartbeat beside the watch. That heartbeat is required. A merge never wakes the thread. The heartbeat is how you learn the PR merged. The required heartbeat may use `everyMs` `900000`. Use `900000` while a landing entry awaits merge in human mode, while the PR sits behind a merge queue, and for any other wait whose predicate is the merge. In merge mode with required checks and no merge queue, the landing drain stays at `3600000`. The required-checks wake runs `land`. The hour covers a merge that finishes after that run and a PR that never posts a check.

## Pending requests

A child or launched thread can stall on a question for the user. `t3_pending_request_list` shows those and `t3_pending_request_read` shows one. Pass the child's `childThreadId`, or the launched thread's `threadId`, as `threadId` on `t3_pending_request_list`, `t3_pending_request_read`, and `t3_pending_request_respond`. Without `threadId` each tool acts on this thread. Answer with `t3_pending_request_respond` only from the brief and the facts and decisions the user already gave. Otherwise ask the user with the host's question tool, then answer. A playbook that parks decisions parks the question instead.

On T3 Code 0.0.46-nightly.20261015.3145 or later, a delegated child's question wakes this thread. The wake is a message that names the child's thread and the request ID. A question that blocks the child also ends a `mode: "wait"` call, per [Delegation](#delegation) step 5. A Codex child can ask without stopping, and its wake says it is still working. When the question no longer shows in `t3_pending_request_list`, someone answered or dismissed it, so send nothing. A task in `result_available` that completed or failed keeps its child's open questions. Do not answer one. Answering such a question sends a message to the child's thread and does not reopen the task. Give the answer to a fresh child per [Fresh children by default](#fresh-children-by-default).

Approval (permission) requests are not listed and cannot be answered by these tools. A child stalled on one looks idle in `task_status`. Avoid the stall: children inherit this thread's runtime mode, so a child that must run commands or edit needs a parent in a mode that allows it. If a child stalls anyway, tell the user which thread is waiting for approval. Never raise a child's `runtimeMode` to get around it.

## ACP fallback

Some ACP agents accept the injected MCP server but do not expose its tools. When the T3 tools are absent and `T3_ACP_MCP_NODE` is set, call the same tools through the terminal:

```bash
ELECTRON_RUN_AS_NODE=1 "$T3_ACP_MCP_NODE" ${T3_ACP_MCP_ENTRYPOINT:+"$T3_ACP_MCP_ENTRYPOINT"} acp-mcp-call orchestrator_capabilities '{}'
ELECTRON_RUN_AS_NODE=1 "$T3_ACP_MCP_NODE" ${T3_ACP_MCP_ENTRYPOINT:+"$T3_ACP_MCP_ENTRYPOINT"} acp-mcp-call delegate_task '{"task":"...","mode":"async","clientRequestId":"..."}'
```

This is the supported transport, not a shell substitute for delegation.

## Outside T3

If neither the tools nor the ACP bridge exist, you are not running in T3. Use the host's native subagents on inherited models, run panels sequentially if needed, and state that model diversity and scheduling were unavailable.

## Skill locations

Read [host.md](host.md) when a thread settles, snoozes, or runs on a host older than the versions this file names, or before you install or locate skills.

Before you write a child brief that needs a specific MCP server, read [Skill locations](host.md#skill-locations).

## Personas

- `agents/poteto-agent.md` is the persona for code-writing delegates inside a poteto-mode playbook. Paste its body at the top of the child's brief, paste the `roles.py mode` lines, and run `roles.py check-brief` per [Delegation](#delegation) step 4.
- `agents/comment-sicko.md` is the persona for the no-comments review. Paste its body at the top of that reviewer's brief.
- Paste the body only, without the frontmatter.
