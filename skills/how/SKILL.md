---
name: how
description: "Use for \"how does X work\", code walkthroughs before changing something, and placement / ownership / layering questions (\"where should this live\", \"which package owns this\", \"is this the right layer\"). Explains subsystem architecture, runtime flow, onboarding mental models. Use why for motivation."
---

# How

Read [the pstack-t3 runtime](../pstack-runtime/SKILL.md) before spawning workers, choosing models, scheduling, or isolating work. It maps those steps onto T3's orchestrator tools.

Explore the codebase to answer "how does X work?" questions.

If the scope is ambiguous, state your interpretation and explore. The user can redirect.

[The runtime's Modes section](../pstack-runtime/SKILL.md#modes) sets the mode lines of every brief this skill writes and how its spawns run in light mode.

## 1. Explain

Spawn one `delegate_task` child with `mode: "async"`, `role: "general"`, a stable `clientRequestId` such as `how-<slug>`, and a read-only brief. Seat it on the `how explainer` role per [the runtime's Roles section](../pstack-runtime/SKILL.md#roles): `target` is the role's seat, omitted for `inherit`. If `delegate_task` rejects the target, apply the runtime's fallback and say which seat changed. Pass the seat the role this step names, per the Subagents section of the poteto-mode skill. The brief says "You have read-only access to the codebase. Do not edit files, commit, or push." Give it the user's question and this brief:

> You are writing an architectural explanation for a senior engineer. Explore the codebase and write an explanation that a senior engineer unfamiliar with this area could read and walk away with a solid mental model. They should understand the architecture well enough to start working in it confidently. You have read-only access to the codebase.

Collect the child per [the runtime's Delegation step 5](../pstack-runtime/SKILL.md#delegation).

## 2. Present

Present the child's output. Light edits for clarity or context from the conversation are fine. Do not substantially rewrite it.
