---
name: arena
description: "Spawn N parallel candidates at the same task, pick a base, graft the strongest parts of the losers into it. Use for /arena, 'arena this', 'throw it in the arena', or when one attempt at a non-trivial artifact would lock in the wrong shape."
---

# Arena

Read [the pstack-t3 runtime](../pstack-runtime/SKILL.md) before spawning workers, choosing models, scheduling, or isolating work. It maps those steps onto T3's orchestrator tools.

Open a todolist with one entry per phase (Frame, Fan out, Cross-judge, Pick, Graft, Verify) before launching anything.

[The runtime's Modes section](../pstack-runtime/SKILL.md#modes) sets the mode lines of every brief this skill writes and how its spawns run in light mode.

## Phase A: Frame

Every candidate gets the same prompt, so the prompt is the contract.

1. State the artifact each candidate is producing.
2. Derive the rubric, 3-6 concrete gradeable criteria for what success looks like on *this* task. It is the picker's tool in Phase D. Candidates only see the task.
3. Pick the runners. Call `orchestrator_capabilities`. Paste that tool result into this quoted heredoc. If the catalog result is large, save it to a temporary file with the host's file tool and pass that path to `--catalog`.

```bash
python3 <pstack-runtime>/scripts/roles.py show --cwd "$PWD" --catalog - --parent "<inheritedProviderInstanceId>/<inheritedModel>" --role "arena runners" <<'JSON'
<the orchestrator_capabilities JSON>
JSON
```

The quoted heredoc sends the JSON unchanged. The command does not write the catalog into the repository, and parallel children do not share a file. It resolves the `arena runners` role per [the runtime's Roles section](../pstack-runtime/SKILL.md#roles). One candidate per seat. The seat count is the panel size. An `inherit` seat means the parent model, so omit `target` for it. A seat that falls back follows the runtime's fallback, and the synthesis note says which seat fell back and whether the models actually differed. Spawn more runners when the arena covers multiple design directions. Use the same model N times when the work is generation-bound rather than judgment-sensitive.

4. Give each candidate its own output location (a git worktree where possible, per [the runtime's Isolation section](../pstack-runtime/SKILL.md#isolation), otherwise `/tmp/arena-<slug>/candidate-<n>/`), per the **separate-before-serializing-shared-state** principle skill.

## Phase B: Fan out

Spawn all N candidates in one message, one `delegate_task` call per seat, with `mode: "async"`, `role: "design"` (or `implementation` when the artifact is code), the seat's `target` (omitted for `inherit`), and a stable `clientRequestId` such as `arena-<slug>-<n>`. Each brief carries the task, the path to the shared grounding, its own output path, and instructions to produce the artifact and a short rationale naming the alternatives it considered and what it rejected. A candidate that writes code for a poteto-mode playbook also follows step 4 of [the runtime's Delegation section](../pstack-runtime/SKILL.md#delegation): the poteto-agent persona first, the lines `roles.py mode --playbook <name> --attempt <kind>` prints for the playbook it serves, which start with `Playbook: playbooks/<name>.md`, and `roles.py check-brief` before `delegate_task`. Retain every returned `taskId`.

If a candidate produces no output, proceed with N-1 and note the dropout.

## Phase C: Cross-judge

After all Phase B candidates complete (collect them per [the runtime's Delegation step 5](../pstack-runtime/SKILL.md#delegation)), choose one seat from the `arena cross-judge pool` role. Resolve it with the same quoted heredoc and `--role "arena cross-judge pool"`. Prefer a seat whose model family differs from the parent's model (`inheritedModel` in `orchestrator_capabilities`). Compare models, not providers, because one provider can serve another's models. If every seat shares the parent's family, say the judge is not cross-family. Spawn one judge child on that seat with `delegate_task`, `mode: "async"`, `role: "review"`, and a read-only brief that says "do not edit files, commit, or push." It sees the rubric and the candidates by path label, scores each criterion, and recommends a base with rationale. It runs in parallel with your reading in Phase D. Don't spawn it while candidates are still writing.

## Phase D: Pick a base

Read every candidate end to end before picking. Score each against the rubric criterion by criterion, not on holistic feel, and compare with the cross-judge. Agreement on the base confirms the pick. Disagreement means one of you is biased or the rubric was ambiguous. Read both rationales before deciding.

Pick the base a future maintainer can extend most easily without breaking invariants. When two feel tied, prefer the cleaner boundary or smaller API, per the Laziness Protocol.

## Phase E: Graft

Walk each losing candidate once more for what is worth porting into the base, usually one or two things per candidate, not most of it. Fold each graft in by hand, per the **redesign-from-first-principles** principle skill, so the result stays coherent under one mental model. Don't paste mechanically.

If the candidates converge on one shape, that is strong agreement. Note it and ship the consensus shape. No graft is needed. If they wildly diverge, Phase A was under-specified. Reframe and re-run rather than averaging the divergence.

## Phase F: Verify

Verify the synthesized artifact like any other output, per the **prove-it-works** principle skill. If verification finds a problem the arena missed, either Phase A was wrong (re-frame and re-run) or a candidate caught it and you missed the graft (go back to Phase E). Don't paper over it.

## Outputs

One synthesized artifact, with a short synthesis note alongside naming the base and why, the cross-judge's verdict, each graft with its source candidate, what was rejected and why, any convergence or dropouts, the seats that fell back, whether the models actually differed, and the verification result. Remove candidate worktrees once the graft is done.
