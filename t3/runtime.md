---
name: pstack-runtime
description: How pstack-t3 skills delegate, pick models, isolate work, schedule, and verify inside T3 Code through the orchestrator V2 tools. Read before running any other pstack skill that spawns workers, picks a model, or schedules work.
---

# pstack-t3 runtime

pstack-t3 runs inside T3 Code. Every provider T3 drives (Claude, Codex, Grok, Cursor, OpenCode, ACP agents) gets the same `t3-code` MCP server. This file maps each pstack concept onto those tools, so a skill works the same whatever model runs it.

Tool names may carry a harness prefix, such as `mcp__t3-code__delegate_task` or `mcp__t3_code__delegate_task`. The semantics are the same. If the T3 tools do not appear in your first tool scan, make one direct call to `orchestrator_capabilities` before concluding they are missing. ACP agents that cannot see the tools use the terminal bridge in [ACP fallback](#acp-fallback).

## Vocabulary

| pstack says | In T3 |
| --- | --- |
| subagent, worker, delegate, reviewer, runner, judge | A child task created with `delegate_task`, owned by this thread. |
| cloud worker | A child task. T3 children run on this machine, so they can reach local files, browsers, and auth. |
| background, `run_in_background` | `delegate_task` with `mode: "async"`. |
| wait for a worker | End the turn and let the completion notification wake you, or call `task_status` mid-turn. |
| cancel a worker | `task_cancel`. |
| model slug, role model | A target `{providerInstanceId, model, options}` from `orchestrator_capabilities`, resolved through roles. See [Roles](#roles). |
| `inherit-parent`, `auto` | `"inherit"`. Omit `target` so the child inherits this thread's provider, model, and options. |
| separate chat, coordinator chat, PR owner thread | A top-level thread from `t3_thread_launch`, only where [Top-level threads](#top-level-threads) allows it. |
| worktree for a worker | A git worktree the child creates for itself, or a `t3_thread_launch` worktree binding for top-level threads. See [Isolation](#isolation). |
| `/loop`, hourly tick, automation, scheduled wakeup | `schedule_task`. See [Scheduling](#scheduling). |
| `scripts/watch-pr`, a poll loop, waiting on CI | `watch_pull_request` on the thread that owns the PR. See [Pull request watching](#pull-request-watching). |
| transcript, chat history, cloud-agent URL | A T3 thread, read with `t3_thread_search` and `t3_thread_read`. |
| control-ui, browser MCP | T3 preview tools: `preview_open`, `preview_snapshot`, `preview_click`, `preview_type`, `preview_evaluate`, `preview_recording_start`. Use `preview_hover` to reveal a menu or tooltip, `preview_drag` to drop one element on another, `preview_select` to choose an option in a native select, `preview_upload` to give the page files, and `preview_dialog` to accept or dismiss a browser dialog. |
| ask the user (`AskQuestion`) | The host's question tool if it has one, otherwise a short question in the reply. |
| todolist | The host's todo tool if it has one, otherwise a checklist in the work log. |
| PR you opened or now drive | Register it with `link_pull_request`. |

## Deadlines

A deadline or timebox sets the order of work. It never waives a step. This holds for Autopilot's early red-test-and-PR target, a brigade or Orchestrate timebox, and any "aim for N minutes" in a brief. Start the deliverable the deadline names first, then run every step the playbook prescribes before the next gate, such as `CODE-READY` or the final report. That includes How, Architect, investigation, and the code delegate. The clock is never a `skip:` reason. When the remaining steps cannot fit, stop at a verifiable point and report the steps that remain instead of skipping them.

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
   - Use a stable `clientRequestId` so a retried call does not spawn a duplicate.
   - Retain every returned `taskId` in your todo list or work log.
4. A child starts with only its brief. It sees none of this conversation. Put the goal, the exact paths or SHAs, how to verify, and the report shape in the brief. Point at files instead of pasting large context. Write tool steps as plain verbs ("read", "search the repo", "run"), because the child may be a different provider with different tool names. A code-writing child inside a poteto-mode playbook opens with the poteto-agent persona body and carries a `Playbook: playbooks/<name>.md` line, such as `Playbook: playbooks/feature.md`. Write that brief to a file and run `python3 <pstack-runtime>/scripts/roles.py check-brief <file>` before `delegate_task`. Exit 1 names what is missing. Fix the brief, run the check again, and pass the checked text unchanged. A seat that matches this thread's model, or an edit that looks small, is not a `skip:` reason for the code delegate.
5. Collect results.
   - If nothing else in this turn depends on the results, end the turn. Each completion wakes this thread.
   - If you need a result now, call `task_status` with the `taskId`. Reading a terminal result this way acknowledges it, so no completion notification follows. Process that result immediately, as if the notification had arrived. `workState: "result_available"` means done, and `summary` holds the result. `working` and `waiting_for_children` mean not done. Do not busy-poll. Do other work between checks.
   - `mode: "wait"` blocks for at most `timeoutMs`, ten minutes by default. Use it only for short children whose result gates the very next step. `waitTimedOut: true` does not cancel the child. Keep the `taskId`.
6. You own every child's output. Read the diff or the evidence yourself before you report it. A child's "done" is a claim, not a verification.

Same-provider native subagent tools (Claude's Agent tool, Codex's subagents) are fine for an `inherit` seat when they run the parent's model. Use `delegate_task` for every other seat, including same-provider seats on another model. Never substitute a top-level thread for a child task.

### Permissions

Children inherit this thread's runtime mode and interaction mode. Do not raise `runtimeMode` above the parent's. A read-only reviewer is a read-only brief: say "do not edit files, commit, or push" in the task. T3 has no read-only flag that keeps MCP access, so the brief carries the constraint.

### Failure handling

- A target is rejected: call `orchestrator_capabilities`, then fall back per [Roles](#roles), and say which seat changed and why.
- A child fails or returns nothing usable: proceed with N-1 and record the dropout. Respawn once with a fresh child for a required slice. Never resume a failed child to fix its own work.
- `task_status` shows `hasPendingChildRuns`: that child is still running nested work. It is not finished.

### Fresh children by default

Give new work to a fresh child with consolidated scope: the original brief, every later directive, and the prior child's report and branch. Message an existing child thread (`t3_thread_send`) only when the new work strictly needs state that lives with it, such as uncommitted changes in its worktree or a dev server it runs.

## Roles

Roles let one skill run on whatever providers the user has. A role value is a list of seats. Each seat is `"inherit"` or a target.

### Where roles live

1. `.pstack/t3-roles.json` in the project root. A role here replaces the same role from the user file.
2. `~/.config/pstack-t3/roles.json`, or `$XDG_CONFIG_HOME/pstack-t3/roles.json`.
3. Built-in defaults below.

Print the merged roles for the current project with:

```bash
python3 <pstack-runtime>/scripts/roles.py show --cwd "$PWD" --parent "<inheritedProviderInstanceId>/<inheritedModel>"
```

Pass `--parent` with the values from `orchestrator_capabilities`. The saved catalog records whichever thread ran setup, so without `--parent` the verifier panel treats no seat as this thread's own.

`<pstack-runtime>` is the directory holding this file. It sits next to every other pstack skill directory, so from a skill at `<dir>/swarm/SKILL.md` it is `<dir>/pstack-runtime`. Add `--role "<name>"` for one role. The output is small JSON with `source` per role. It resolves against the catalog snapshot that `setup-pstack` saved, if any. A role that reports `"seats": "catalog-required"`, or an adaptive panel that reports `"seats": "default-panel"`, needs a catalog. Call `orchestrator_capabilities`. Paste that tool result into this quoted heredoc. If the catalog result is large, save it to a temporary file with the host's file tool and pass that path to `--catalog`.

```bash
python3 <pstack-runtime>/scripts/roles.py show --cwd "$PWD" --catalog - --parent "<inheritedProviderInstanceId>/<inheritedModel>" --role "<name>" <<'JSON'
<the orchestrator_capabilities JSON>
JSON
```

Do not reconstruct role defaults in the calling skill. Pass a file path to `--catalog` when the catalog is already a file. Setup does that. One temporary file feeds `show`, `write`, and the saved snapshot.

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
| `swarm workers` | 1 | swarm, default model for every worker |
| `skill tests` | 1 | pstack-author-skill fresh-child test and description eval |
| `architect runners` | N | architect, one runner per seat |
| `interrogate reviewers` | N | interrogate, one reviewer per seat |
| `verifiers` | N | swarm-verify, autopilot, shipping, orchestrate verification |

### Built-in defaults

`roles.py show` owns this mapping. A skill must not rebuild it.

Code roles (`feature, refactoring`, `bug-fix`, `perf-issue`, `hillclimb`, `swarm workers`, `how explorer`, `why investigators`, `reflect tooling`) use `grok-4.7` at xhigh. Judgment roles (`judgment and prose`, `hardest tasks`, `how explainer`, `why synthesizer`, `reflect judgment, divergent, synthesizer`) use `claude-opus-5-5` at xhigh. `arena runners`, `arena cross-judge pool`, `architect runners`, and `interrogate reviewers` use those two seats in that order. A Grok seat sets `fastMode` only when the chosen model is in the grok family and declares that boolean option. A fallback to another family does not set it.

`skill tests` is one catalog seat. Prefer a runnable model whose family differs from this thread's model. A family is the leading word of the model id. In that pool, prefer an id token in `haiku`, `mini`, `nano`, `flash`, `lite`, `fast`, `small`, or `luna`, then the lowest default reasoning level, then earlier in the catalog. If every runnable model shares this thread's family, apply the same rule inside the family. No catalog, or no runnable model, leaves `["inherit"]`. The seat names no reasoning option. The budget cap still applies.

`verifiers` starts with `"inherit"` when this thread's provider can run children, then adds one seat per other runnable provider (`canRunChildTask: true`), using that provider's first listed model and skipping a family already seated. With only one runnable provider, `verifiers` is three `"inherit"` seats, and the report must say the models did not differ. The other panel roles do not use that three-seat rule.

A model family is the leading word of the model ID: `claude-opus-5-5` is `claude`, `gpt-6.1-sol` is `gpt`, `grok-4.7` is `grok`. Diversity rules compare families, never providers, because one provider can serve another's models.

A preferred seat that matches this thread stays an explicit target. It does not become `"inherit"`.

When the preferred model is missing, the seat stays and a numbered note names the role, the seat number, the wanted model, and the replacement. The replacement is the first runnable model of that family, then this thread's model when its provider can run children, then the first runnable model in the catalog. No runnable provider is an error. The panel keeps every seat. When two seats land on the same model, the note says the panel lost a distinct model.

Without a catalog, a preferred role reports `"seats": "catalog-required"`. `skill tests` reports `["inherit"]`. `verifiers` reports `"seats": "default-panel"`.

Agreement between seats on the same model is weak evidence. It shows the prompt is stable, not that the finding is right. Weigh consensus only across seats on different models, and say which kind you have.

### Budget

The config may carry `"budget"`: `default`, `small`, `medium`, `large`, or `unlimited`. It caps the reasoning option of every seat that has one (`effort`, `reasoningEffort`, `reasoning_effort`, or `reasoning`) at `medium`, `high`, `xhigh`, or the highest level at or below `max`, or the closest lower value the model offers. A seat that names its own reasoning level keeps it when it is at or below the budget level, and is lowered to the budget level otherwise. `default` leaves options alone. Built-in preferred seats name xhigh, so `default` leaves that level in place. The ladder stops at max. `unlimited` raises a built-in preferred seat to the model's highest level at or below max. The default Opus seat moves from xhigh to max. Grok stays at xhigh, the top of its ladder. `unlimited` lowers a configured `ultra` seat to max when the model offers a level at or below max. `default` keeps a configured `ultra` seat. When every offered level is above the cap, the seat gets the lowest level. Verifiers use those same levels. `ultracode` and `ultrathink` are never chosen by a budget. Under any budget other than `default`, an `inherit` seat becomes an explicit target on this thread's provider and model with the budgeted option, because an omitted `target` would pass the parent's reasoning level through. `roles.py show` reports the budgeted seats.

### Fallback

A configured seat whose provider is not runnable, or whose model is not in the catalog, falls back on its own. This path is separate from the built-in order above.

1. Use the same provider's first listed model.
2. If the provider is not runnable, use `"inherit"`.
3. Say which seat changed and why. Never silently drop a seat, because the seat count is the panel size.

Built-in preferred seats use the numbered notes from [Built-in defaults](#built-in-defaults). Those notes name each replacement, and the panel size never shrinks.

`roles.py validate --catalog <file>` checks a config against a saved catalog file. Use the file path there. The quoted heredoc above is for `show` when the catalog is still a tool result.

## Isolation

Two writers never share a checkout (principle-separate-before-serializing-shared-state).

- Read-only children share the current checkout.
- A writing child that may overlap with another writer gets its own git worktree, which the parent creates before delegating: `git worktree add <path> -b <branch> <base>`. `delegate_task` has no workspace argument, so the child's thread stays bound to this checkout and its default working directory is still here. The brief must say: "Work only in `<absolute worktree path>`. Use absolute paths under it for every read and edit, and prefix every command with `cd <absolute worktree path> &&`. Report the branch and head SHA." Check the child's diff landed in the worktree and not in this checkout before integrating. The parent removes the worktree afterwards.
- When a writer's isolation must not depend on the brief being obeyed, or the work is a long-lived independent unit, launch a top-level thread with a worktree strategy instead, where [Top-level threads](#top-level-threads) allows it.
- Long-lived owners that should appear in T3's sidebar with their own binding (PR owners in Autopilot and Orchestrate) are top-level threads launched with a worktree strategy. See below.
- Uncommitted changes are not copied into new worktrees. Commit or stash first, or point the brief at a pushed branch.

## Top-level threads

Create top-level threads only when the user asked for separate threads or invoked a playbook or skill that names them (Orchestrate, Autopilot-full, Autopilot-stack, brigade). Invoking those is that request. Everything else uses child tasks.

```json
{
  "title": "PR owner: <slug>",
  "workspaceStrategy": {"type": "worktree", "baseRef": "main", "branch": "pstack/<slug>", "startFromOrigin": false},
  "message": "<brief>",
  "modelSelection": {"instanceId": "claudeAgent", "model": "claude-opus-5-5"}
}
```

- For a stack, `baseRef` is the parent branch and `startFromOrigin` is false.
- Omitted `workspaceStrategy` means the project root, not your worktree.
- `t3_thread_launch` requires a full-access or default caller. In `approval-required` or `auto-accept-edits` it fails. Then fall back to child tasks isolated per [Isolation](#isolation), and tell the user that owners are children rather than threads.
- `t3_thread_launch` has no retry key. Retain the `threadId`. After an error or lost response, check `t3_thread_list` before retrying.
- Follow a thread with `t3_thread_wait` and read it with `t3_thread_read` (use `afterPosition` to read only what is new). Send follow-ups with `t3_thread_send`, interrupt with `t3_thread_interrupt`.
- A thread launched with `t3_thread_launch` has no parent. Its finished turn does not wake the launcher. A launcher that needs a report names the message the launched thread sends with `t3_thread_send`.
- `create_threads` makes up to 20 threads sharing this checkout. Use it only for read-only fan-out the user wants visible as threads.

## Scheduling

`schedule_task` creates recurring work in T3's scheduler. It runs even when no turn is active, so it replaces `/loop`, Cursor automations, and hourly ticks.

```json
{"title": "autopilot tick", "prompt": "<self-contained tick prompt>", "schedule": {"type": "interval", "everyMs": 3600000}, "clientRequestId": "<skill>-<slug>-tick"}
```

- Pass `schedule` as an object. `everyMs` is at least 60000. Wall-clock runs use `{"type": "fixed_time", "timeOfDay": "09:00", "weekdays": [1,2,3,4,5]}`.
- Runs post into this thread by default. Set `bindToCurrentThread: false` only when each run should start a fresh thread.
- The tick prompt must stand alone. Point it at the work log or store so a run can rebuild state from disk.
- Report the returned cadence and `nextRunAt`. Delete the schedule with `delete_scheduled_task` when the done predicate holds. List with `list_scheduled_tasks`.
- Pause a schedule with `update_scheduled_task` and `enabled: false`. Resume by setting it back to true.
- Do not schedule a tick to wait for a child task. Child completions already wake this thread.
- Do not schedule a tick to wait on a pull request's checks, reviews, or conflicts. That wait is [Pull request watching](#pull-request-watching). Keep `schedule_task` for a cadence with no PR event. Beside a watch, a fallback heartbeat uses `everyMs` of at least `3600000`. A required heartbeat whose job is to notice a merge may use `900000`, as that section states.

## Local state

Private working state that must survive the session but never be committed lives under `${XDG_STATE_HOME:-~/.local/state}/pstack-t3/`. Orchestrate stores go in `orchestrate/<project-slug>/`, private plans in `docs/<project-slug>/`. Children, launched threads, and scheduled runs reach it by the same absolute path from any worktree. Pass that path explicitly in every brief and tick prompt.

Resume notes from Pause safely go to `.pstack/resume/<slug>.md` in the repository, untracked (add `.pstack/` to `.git/info/exclude`), and are also posted as the thread's final message.

After a T3 restart, assume a child is gone unless `task_status` shows `working` or `waiting_for_children`. Pushed branches, worktrees, launched threads, and schedules persist. Reattach through `t3_thread_list` and `list_scheduled_tasks`.

## Verification surfaces

- Web or Electron UI: `preview_open` the dev server URL, then `preview_snapshot`, `preview_click`, `preview_type`, `preview_press`, `preview_wait_for`, `preview_evaluate`. Use `preview_hover` to reveal a menu or tooltip, `preview_drag` to drop one element on another, `preview_select` to choose an option in a native select, `preview_upload` to give the page files, and `preview_dialog` to accept or dismiss a browser dialog. Record proof with `preview_recording_start` and `preview_recording_stop`. Check `preview_status` first. Keep the `tabId` that `preview_open` returns and close each preview you opened with `t3_preview_close` and that `tabId`.
- Devices and simulators: `device_list`, `device_open`, `device_screenshot`, `device_close`.
- CLIs and TUIs: run them in the terminal and assert on output.
- A project `verify-*` skill beats all of these when one exists.

## History

- Find prior work: `t3_thread_search` with a topic, branch, or PR number. It covers the current project.
- Read it: `t3_thread_read` with `view: "messages"` for the conversation, `view: "activity"` for tool activity. Page with `afterPosition`. Recover long items with `itemId` and `textOffset`.
- A thread the user attached as context is readable even outside this project.
- Child tasks are threads too. A `childThreadId` from `delegate_task` is readable with `t3_thread_read`.
- `t3_thread_read` returns the thread's `worktreePath` and `branch`. `t3_thread_list` does not, so finding the threads bound to a worktree takes one read per thread. Threads from other projects are not visible.

## Pull requests

After you open a PR or start driving an existing one, call `link_pull_request` with its full URL. For a stack, link every layer. Linking attaches the PR to the calling thread. When a child opens a PR, it links it and the parent links it too. When a launched thread opens a PR, it links it and the launcher or coordinator links it too. Linking twice is safe. Before finishing PR work, call `list_thread_pull_requests` and link any missing PR. Report a link failure instead of claiming the PR is linked.

## Pull request watching

Call `watch_pull_request` after `link_pull_request`, when this thread is waiting on that PR's checks, reviews, or conflicts. Pass the PR URL, or the repository and number. T3 links the PR first if this thread has not linked it yet.

T3 checks the open PR every two minutes. It wakes this thread when a check fails, the required checks pass, someone else comments or reviews, or the branch starts to conflict with its base. Only comments posted after the call wake you, so handle the comments already on the PR, then end the turn.

One thread may hold several watches. Comments from the user's own account do not wake a watch, so a review left from that account needs another wake or the heartbeat. T3 documents both in [source control](https://github.com/pingdotgg/t3code/blob/main/docs/user/source-control.md).

A wake is news, not a merge decision. Read the PR and decide yourself before you merge.

A subagent cannot watch. The parent thread owns the PR. The child finishes and reports back. The thread that owns the PR calls `watch_pull_request`.

Call `unwatch_pull_request` when this thread stops driving the PR and hands that work back to the user. An interim status reply keeps the watch. The PR stays linked. While T3 watches, the thread stays in the user's Working list. Unwatching returns the thread to their inbox.

A merge ends the watch and does not wake the thread. A close ends the watch and does wake the thread. Watching also ends when the thread settles or is archived, when the user stops the thread, or when you call `unwatch_pull_request`. It also ends when T3 fails to read the PR 8 times in a row. A host rate limit only delays the next read. A watch ends after 10 wakes in a row that bring only comments, and that end posts a wake. Call `watch_pull_request` again after that wake when the loop is still running.

If the thread is settled, call `t3_thread_organize` with `action: "unsettle"` first. A settled thread's new watch ends on the next pass and posts no wake. A pinned thread does not auto-settle. A pinned coordinator runs that action only when someone settled the thread by hand.

pstack's `scripts/watch-pr` poll, a foreground `--watch`, and an interval tick that waits for CI, a review, or a conflict all become this call. The forge commands that classify a verdict stay. Run them after a wake. They are not the wait.

`schedule_task` stays for a cadence that has no PR event, such as an hourly audit, a morning report, or a soak. Beside a watch, a fallback heartbeat uses `everyMs` of at least `3600000`. When the event you are waiting for is the merge itself, create a `schedule_task` heartbeat beside the watch. That heartbeat is required. A merge never wakes the thread, so the heartbeat is how you learn that the PR merged. The required heartbeat may use `everyMs` `900000`. Use `900000` while a landing entry is awaiting merge in human mode, while the PR is behind a merge queue, and for any other wait whose predicate is the merge. In merge mode on a repository with required checks, when the PR is not behind a merge queue, the landing drain stays at `3600000`. The required-checks wake runs `land`. The hour covers a merge that finishes after that run, and a PR that never posts a check.

## Pending requests

A child or launched thread can stall on a question for the user. `t3_pending_request_list` shows those and `t3_pending_request_read` shows one. Answer with `t3_pending_request_respond` only from facts and decisions the user already gave.

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

T3's `$` picker lists each provider's native skills. A skill meant for every provider is written once and linked into each directory.

| Provider | User | Project |
| --- | --- | --- |
| Claude | `~/.claude/skills` (or `$CLAUDE_CONFIG_DIR/skills`) | `.claude/skills` |
| Codex | `~/.agents/skills` | `.agents/skills` |
| Grok | `~/.grok/skills` | `.grok/skills` |
| Cursor | `~/.cursor/skills` | `.cursor/skills`, and it also reads `.agents/skills` and `.claude/skills` |

Every child gets the `t3-code` server. Other MCP servers come from each provider's own configuration, so a child on another provider may lack one. A brief that needs a specific MCP server says so, and the child reports `MCP unavailable` instead of guessing.

## Personas

- `agents/poteto-agent.md` is the persona for code-writing delegates inside a poteto-mode playbook. Paste its body at the top of the child's brief, name the playbook, and run `roles.py check-brief` per [Delegation](#delegation) step 4.
- `agents/comment-sicko.md` is the persona for the no-comments review. Paste its body at the top of that reviewer's brief.
- Paste the body only, without the frontmatter.
