# Child task delegation

Read this file before spawning a child task or writing its brief. Step 4 lives in [SKILL.md](SKILL.md#delegation).

## Spawn and collect

1. Call `orchestrator_capabilities` once per session before the first delegation, and again after a delegation fails on a target. It returns `parentThreadId` (this thread's own ID, for `t3_thread_read` on yourself), `inheritedProviderInstanceId` and `inheritedModel` (this thread's own seat), `providers[]`, each with `providerInstanceId`, `models[]` and their `options`, `canRunChildTask`, `canRunCrossProviderChildTask`, and `constraints`. Treat a provider with `canRunChildTask: false` as unavailable and report its `constraints` if a role needed it.
2. Resolve the role's targets per [Roles](SKILL.md#roles).

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

     When an OpenCode child's task is terminal and the report file its brief named is missing, the task is not finished. Read its activity with `t3_thread_read`. While a later turn on its thread still runs, wait on it per [waiting.md](waiting.md). Otherwise handle it per [Failure handling](SKILL.md#failure-handling).
   - Use a stable `clientRequestId` so a retried call does not spawn a duplicate.
   - Retain every returned `taskId` in your todo list or work log.

5. Collect results. Read [waiting.md](waiting.md) before you collect child results or wait on a child.
6. You own every child's output. Read the diff or the evidence yourself before you report it. A child's "done" is a claim, not a verification.

Same-provider native subagent tools (Claude's Agent tool, Codex's subagents) are fine for an `inherit` seat when they run the parent's model. Use `delegate_task` for every other seat, including same-provider seats on another model. Never substitute a top-level thread for a child task.

### Permissions

Children inherit this thread's runtime mode and interaction mode. Omit `runtimeMode` so the child inherits. Never raise it above the parent's, and never lower it. A child in `approval-required` stops at its first command on an approval request that no tool can answer, so it never finishes. A read-only reviewer is a read-only brief: say "do not edit files, commit, or push" in the task. T3 has no read-only flag that keeps MCP access, so the brief carries the constraint. Muse supports only `approval-required` and `full-access`. Under any other parent mode T3 runs a Muse child or launched Muse thread as `approval-required`, so it stalls at its first command. Read `runtimeMode` from `orchestrator_capabilities` before you seat Muse. Under a mode Muse lacks, replace the Muse seat with `"inherit"` per [Fallback](SKILL.md#fallback) and say why.

### Fresh children by default

Give new work to a fresh child with consolidated scope: the original brief, every later directive, and the prior child's report and branch. Message an existing child thread (`t3_thread_send`) only when the new work strictly needs state that lives with it, such as uncommitted changes in its worktree or a dev server it runs.

A `t3_thread_send` to a child whose task has already published its result can start a separate task for that message. Read the send result for a `taskId`. When it carries one, retain it and collect that task per [waiting.md](waiting.md). Its `summary` holds the answer, and the original task's `summary` does not change. When the result carries no `taskId`, wait with `t3_thread_wait` on the `childThreadId` and read the answer with `t3_thread_read`.

- Deduplicate returned `taskId` values. On T3 Code 0.1.1-nightly.20261018.3331 or later, a send that steers a running child turn returns that turn's existing `taskId` and adds no new task. A send to an idle child starts a new result turn and returns that turn's `taskId`. Collect the task the send returned. Read the answering turn with `t3_thread_read` and `afterPosition`. Do not trust the first `summary` alone. On T3 Code 0.1.1-nightly.20261019.3348 or later, `task_cancel` also cancels the task's held queued turn, and restart recovery does not restart a cancelled task. On stable 0.1.0 the paragraph above holds, so after a cancel call `t3_queue_list` on the `childThreadId` and cancel each held entry. Fresh children stay the default on both versions. Stall handling stays in [waiting.md](waiting.md) on both versions.
