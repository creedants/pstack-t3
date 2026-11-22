---
name: why
description: "Use for 'why does X work this way', 'why we picked Y', design rationale, regressions, postmortems, or data-backed thresholds. Returns a cited read on decisions and tradeoffs. Use how for runtime behavior."
---

# Why

Read [the pstack-t3 runtime](../pstack-runtime/SKILL.md) before spawning workers, choosing models, scheduling, or isolating work. It maps those steps onto T3's orchestrator tools.

Answer from the record, not the code: read the commits and PR discussions that introduced the target and each later change, and other sources if the trail is thin. Cite what the authors wrote, mark your own inferences as inferences, and say what the record does not answer.

[The runtime's Modes section](../pstack-runtime/SKILL.md#modes) sets the mode lines of every brief this skill writes and how its spawns run in light mode.

## 1. Investigate

Spawn one `delegate_task` child with `mode: "async"`, a stable `clientRequestId` such as `why-<slug>`, and a read-only brief that keeps its tools and says "Do not edit files, commit, or push." Seat it on the `why investigators` role per [the runtime's Roles section](../pstack-runtime/SKILL.md#roles): `target` is the role's seat, omitted for `inherit`. If `delegate_task` rejects the target, apply the runtime's fallback and say which seat changed. Pass the seat the role this step names, per the Subagents section of the poteto-mode skill. Give it the target, the user's question, and these instructions: build the code anchor first (file paths and line ranges, key symbols, the last commits touching the target from `git blame` and `git log`, PR numbers from merge commits), then read each substantive commit's PR body and discussion with `gh pr view`, then query other available sources only where the trail is thin. Return what the record says, what can reasonably be inferred, and what it does not answer.

Collect the child per [the runtime's Delegation step 5](../pstack-runtime/SKILL.md#delegation).

## 2. Present

Present the investigator's output to the user. You may lightly edit for clarity or add context from the conversation, but keep the citations and do not rewrite inferences as facts.
