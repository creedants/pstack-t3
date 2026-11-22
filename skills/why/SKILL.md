---
name: why
description: "Use for 'why does X work this way', 'why we picked Y', design rationale, regressions, postmortems, or data-backed thresholds. Returns a cited read on decisions and tradeoffs. Use how for runtime behavior."
---

# Why

Read [the pstack-t3 runtime](../pstack-runtime/SKILL.md) before spawning workers, choosing models, scheduling, or isolating work. It maps those steps onto T3's orchestrator tools.

Answer from the record, not the code: read the commits and PR discussions that introduced the target and each later change, and other sources if the trail is thin. Cite what the authors wrote, mark your own inferences as inferences, and say what the record does not answer.

Start from the code anchor. `git blame` and `git log` name the commits and the last touch of each line, and `gh pr view` gives each substantive commit's PR body and discussion. Query another source only where that trail is thin. Return what the record says, what can reasonably be inferred, and what it does not answer.
