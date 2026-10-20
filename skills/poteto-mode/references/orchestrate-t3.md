# Orchestrate on T3

Read this file when the orchestrate playbook links to it.

## Owner reporting

- Spawn, resume, drain, and follow agents per the runtime's [Delegation](../../pstack-runtime/SKILL.md#delegation) and [Top-level threads](../../pstack-runtime/SKILL.md#top-level-threads).
- Carry the coordinator's thread ID in each owner brief.
- A report is a queue event even when it steers into a turn. Push it to the inbox with `orch inbox push` and return to what you were doing.
- Drain only `delegate_task` children from their completion notifications. A launched owner's finished turn does not wake the coordinator, so follow it with `t3_thread_read`. The owner reports with a one-line `t3_thread_send` and `mode: "auto"`, per [Top-level threads](../../pstack-runtime/SKILL.md#top-level-threads).

## Liveness probes

- Probe read-only: the ledger, `units.tsv`, `gh`, pushed branches, `task_status` for a child, and `t3_thread_read` with `afterPosition` for a launched thread.
- A `task_status` read acknowledges a terminal result, so push it to the inbox with `orch inbox push` at once, or the queue event is lost.
- A thread's last-updated time is not liveness.

## Stack safety on T3

- Chain rebases run in the stacker's own worktree, never in the coordinator's checkout.
- A chain rebase at this scale can take the machine down, so run one at a time.

## Recovery after a restart

After a T3 restart or provider switch:

- Treat a child as dead unless `task_status` shows `workState` `working` or `waiting_for_children`. A sub-coordinator waiting on its children is alive. Do not respawn its track.
- Pushed branches and launched threads persist, and a worktree persists unless storage cleanup removed it, per [the runtime's Local state](../../pstack-runtime/SKILL.md#local-state).
- Re-read the standing orders and `units.tsv`, and recompute the frontier.
- Reattach work by PR and branch rather than task id, and use `t3_thread_list` for launched threads.
- Respawn a sub-coordinator from its stored brief plus current state only for a track whose sub-coordinator is not alive.
- Drain, then resume.
- The dead session's store lock clears itself on the next write. `orch` replaces a lock whose holder pid is gone.
