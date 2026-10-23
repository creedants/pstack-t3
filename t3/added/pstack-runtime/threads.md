# Thread and schedule operations

Read this file before you call `t3_thread_configure`, `t3_queue_promote_to_steer`, `t3_thread_fork`, `t3_thread_merge_back`, or `run_scheduled_task_now`, or pause a finite program's schedule.

## Configure an owner

- Change an existing launched thread's model with `t3_thread_configure` only when a playbook step already calls for that owner to run on another model, such as Orchestrate's tool-error retry. Never call it because an owner keeps failing a gate. Resolve the seat per [Roles](SKILL.md#roles) with `roles.py show`. `t3_thread_configure` requires a concrete `modelSelection`, so always pass one, even for an `inherit` seat. Build it from the resolved seat with `providerInstanceId` renamed to `instanceId`, `model` copied, and `options` copied unchanged. When the resolved seat is `inherit`, use this thread's own provider and model, `inheritedProviderInstanceId` and `inheritedModel` from `orchestrator_capabilities`, as the concrete seat when it passes [Excluded seats](SKILL.md#excluded-seats). When it fails that check, do not configure the thread, and report that no replacement seat could be selected. Call it when `t3_thread_read` shows no `activeRunId`, or after `t3_thread_interrupt` and `t3_thread_wait` end the run. Before the thread's next message, read `t3_thread_configuration` and compare `instanceId`, `model`, and every option per [Delegation](delegation.md#spawn-and-collect) step 3. On a refusal or a mismatch, send nothing to the thread and report the tool's error text. The call does not change the thread's permission modes, and it keeps the thread's retry count, scope, standing orders, and gates. The thread keeps its history, so it never counts as a fresh worker or a fresh reviewer. Never configure a verifier or reviewer thread. A failed child task gets a fresh child per [Failure handling](SKILL.md#failure-handling). A send-back and a usage-limit relaunch get the fresh worker their playbook or `roles.py backup` names. None of them uses `t3_thread_configure`.

## Promote a queued correction

- A correction you queued earlier to an active owner or coordinator can sit behind a long turn. To deliver it into the run, call `t3_queue_list` on the recipient and find the `queuedRunId` of that exact message. Read the recipient's `activeRunId` with `t3_thread_read`. Call `t3_queue_promote_to_steer` with that `queuedRunId`, the `activeRunId` as `targetRunId`, and the recipient's `threadId`. Its `sequence` result means T3 accepted the call, not that the run received the message. Read the recipient with `t3_thread_read`, `view: "activity"`, and `afterPosition`, and find the correction's text in that run. Until that read shows it, the delivery is unresolved. A `cancelled` queued run alone proves nothing. When the active run changed or T3 refused the promotion, read `t3_queue_list` and the recipient again before you act, and never resend a correction that may already be delivered. Never promote brigade's event lines to an executive admin, a digest message, or a message that needs a turn of its own. Send a new correction with `mode: "steer"` or `mode: "auto"` instead of queuing it.

## Forks

`t3_thread_fork` starts a new top-level thread from another thread's context. `t3_thread_merge_back` moves context between related threads in one project. Use them only for a separate thread this section allows. Child work stays on `delegate_task` per [Delegation](SKILL.md#delegation) and [Fresh children by default](SKILL.md#fresh-children-by-default). A fork never stands in for a child task, an Orchestrate sub-coordinator, or a fresh review round, and `task_status` does not apply to it.

- Fork only a read-only planner or investigation whose source context is the part it needs. Pass `sourcePoint` as `{"type": "latest_stable"}`, `{"type": "run", "runId": "<id>"}`, or `{"type": "checkpoint", "checkpointId": "<id>"}`. Keep the returned `targetThreadId`.
- The fork inherits the source's configuration. Read it with `t3_thread_configuration` before the fork's first message, and check it against [Excluded seats](SKILL.md#excluded-seats) and the user's roles. Inheritance is not an exception. When the inherited seat fails that check, send the fork nothing and report it.
- Read the fork with `t3_thread_read` for its `worktreePath`, `branch`, and `activeRunId` before you send anything. Treat a checkout it shares with another thread as read-only. Writers stay isolated per [Isolation](SKILL.md#isolation).
- A fork keeps PR links but starts with no active watch. Ending a fork watch leaves the source watch alone. The source keeps its watch and its `schedule_task` heartbeat and reads current PR state on every wake.
- Send its scope with `t3_thread_send`. The message names the slice, says "do not edit files, commit, or push", and names the report line and the thread ID that line goes to with `t3_thread_send`. Follow the fork as a launched thread with `t3_thread_wait` and `t3_thread_read`.
- A one-line report stays the default. Call `t3_thread_merge_back` only when the user authorized it and the target needs the fork's reasoning, not only its result. Pass `targetThreadId` and `sourcePoint`, and `sourceThreadId` when the source is not this thread. Read `t3_thread_transfers` and the target's later activity with `t3_thread_read` to see what the target received. A transfer moves context, not code, and it does not make a review independent.

## Run a schedule now

- To run an enabled `interval` schedule's work now, call `run_scheduled_task_now` with its ID as `taskId`. Take that ID from the work log or from `scheduledTaskId` in `list_scheduled_tasks`. Call it only when the work its next run does is ready and neither this turn nor a dispatched run is doing it. No event calls it by rule, a merge or an answer included. Each call is a new manual run. Before you retry a lost or failed response, read `list_scheduled_tasks` and the bound thread with `t3_thread_read`. The result means T3 dispatched the run, not that its turn finished. Follow a returned `threadId` with `t3_thread_read` when the result matters. Report the returned `nextRunAt` as T3 returned it. Never run a paused schedule before the answer permits work. Never run one to wait on a child, to wait on a pull request's checks, reviews, or conflicts, or to repeat a `watch_pull_request` wake. It requires a full-access or default caller. Under another mode, leave the work to the next scheduled run, or do it in this turn only when this thread owns that work and its mode allows the edits.

## Pause a finite program

- A finite program pauses a schedule that can only repeat an unanswered user decision. A finite program has a done predicate, as in Autopilot-full, Autopilot-stack, Orchestrate, and Autonomous run. The rule covers an audit or progress schedule when no item has runnable work and its next run can only raise the same user decision again.
- Write the decision and that schedule's ID to the work log or store the tick prompt names. Raise the decision once. Then call `update_scheduled_task` with that ID and `enabled: false`, and end the turn.
- On an answer that permits work, call `update_scheduled_task` with the same ID and `enabled: true`. On an answer that keeps the work parked, record the answer and leave the schedule paused. When the done predicate holds, delete the schedule with `delete_scheduled_task`. After a T3 restart, find the paused schedule with `list_scheduled_tasks` and the recorded ID.
- A tick queued before the pause can still run after it. It reads the record, does not raise the decision again, and does not re-enable the schedule.
- Never pause a schedule that renews a lease, drains queued work, consumes standing intake, or notices a merge. A required merge heartbeat and a fallback heartbeat beside `watch_pull_request` keep running. This rule never pauses a brigade schedule. Brigade holds an item on a decision and keeps its liveness schedule renewing the lease.

## Top-level threads

Create top-level threads only when the user asked for separate threads or invoked a playbook or skill that names them (Orchestrate, Autopilot-full, Autopilot-stack, brigade). Invoking those is that request. Everything else uses child tasks.

```json
{
  "title": "PR owner: <slug>",
  "workspaceStrategy": {"type": "worktree", "baseRef": "main", "branch": "pstack/<slug>", "startFromOrigin": false},
  "message": "<brief>",
  "modelSelection": {"instanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}}
}
```

- `modelSelection` is the seat with `providerInstanceId` renamed to `instanceId`, `model` copied, and `options` copied unchanged as the same object. Omit `modelSelection` for an `inherit` seat.
- Confirm a launched thread per [Delegation](delegation.md#spawn-and-collect) step 3.
- For a stack, `baseRef` is the parent branch and `startFromOrigin` is false.
- Omitted `workspaceStrategy` means the project root, not your worktree.
- `t3_thread_launch` requires a full-access or default caller. In `approval-required` or `auto-accept-edits` it fails. Then fall back to child tasks isolated per [Isolation](SKILL.md#isolation), and tell the user that owners are children rather than threads.
- `t3_thread_launch` has no retry key. Retain the `threadId`. After an error or lost response, check `t3_thread_list` before retrying. Report it to the user as a thread link per [History](SKILL.md#history).
- Follow a thread with `t3_thread_wait` and read it with `t3_thread_read` (use `afterPosition` to read only what is new). Send follow-ups with `t3_thread_send`, interrupt with `t3_thread_interrupt`.
- A thread launched with `t3_thread_launch` has no parent. Its finished turn does not wake the launcher. A launcher that needs a report names the message the launched thread sends with `t3_thread_send`.
- Autopilot-full, Autopilot-stack, and Orchestrate owners send their report lines to the root or coordinator with `t3_thread_send` and `mode: "auto"`. `auto` starts an idle recipient, steers a fully active turn, and queues behind a turn that cannot accept steering yet. It does not merge reports into one turn. The recipient handles every report, steered or queued, and runs the playbook's head-specific checks on the head each report names. Arrival order never makes a head current. Brigade's event lines to an executive admin stay on `mode: "queue"`, as [Reporting to an executive admin](../brigade/SKILL.md#reporting-to-an-executive-admin) states.
- A correction you queued earlier to an active owner or coordinator can sit behind a long turn. Read [Promote a queued correction](threads.md#promote-a-queued-correction) before you deliver it. Never promote brigade's event lines to an executive admin, a digest message, or a message that needs a turn of its own.
- Change an existing launched thread's model with `t3_thread_configure` only when a playbook step already calls for that owner to run on another model, such as Orchestrate's tool-error retry. Never call it because an owner keeps failing a gate. Read [Configure an owner](threads.md#configure-an-owner) before the call.
- `create_threads` makes up to 20 threads sharing this checkout. Use it only for read-only fan-out the user wants visible as threads.

## Scheduling

Call `schedule_task` with this shape.

```json
{"title": "autopilot tick", "prompt": "<self-contained tick prompt>", "schedule": {"type": "interval", "everyMs": 3600000}, "clientRequestId": "<skill>-<slug>-tick"}
```

- Pass `schedule` as an object. `everyMs` is at least 60000. Wall-clock runs use `{"type": "fixed_time", "timeOfDay": "09:00", "weekdays": [1,2,3,4,5]}`.
- Runs post into this thread by default. Set `bindToCurrentThread: false` only when each run should start a fresh thread.
- A webhook schedule runs on each request to a public URL. Read [Webhook schedules](webhooks.md) before you create one or ask for a signing secret.
- Each run of a schedule bound to this thread uses the thread's current provider and model. Read [Configure an owner](threads.md#configure-an-owner) before a seat change.
- The tick prompt must stand alone. Point it at the work log or store so a run can rebuild state from disk.
- Report the returned cadence and `nextRunAt`. Delete the schedule with `delete_scheduled_task` when the done predicate holds. List with `list_scheduled_tasks`.
- Pause a schedule with `update_scheduled_task` and `enabled: false`. Resume by setting it back to true.
- Never pause a schedule that renews a lease, drains queued work, consumes standing intake, or notices a merge. A required merge heartbeat and a fallback heartbeat beside `watch_pull_request` keep running. This rule never pauses a brigade schedule. Brigade holds an item on a decision and keeps its liveness schedule renewing the lease. Read [Pause a finite program](threads.md#pause-a-finite-program) before you pause a schedule.
- Do not schedule a tick to wait for a child task. Child completions wake this thread, and [Delegation](delegation.md#spawn-and-collect) step 5 bounds the wait for a child that never completes.
- Do not schedule a tick to wait on a pull request's checks, reviews, or conflicts. That wait is [Pull request watching](SKILL.md#pull-request-watching). Keep `schedule_task` for a cadence with no PR event. Beside a watch, a fallback heartbeat uses `everyMs` of at least `3600000`. A landing contract may set a longer interval. The landing skill reads it from land.py status. A required heartbeat whose job is to notice a merge may use `900000`, as that section states. A landing contract may set a longer interval. The landing skill reads it from land.py status.
- To run an enabled `interval` schedule's work now, read [Run a schedule now](threads.md#run-a-schedule-now). Never run a paused schedule before the answer permits work.
