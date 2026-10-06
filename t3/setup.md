---
name: setup-pstack
description: Configure which T3 providers and models pstack uses per role and at what reasoning budget. Reads the live T3 catalog and writes a roles file that every pstack skill reads. Use for /setup-pstack, "configure pstack models", "pstack budget", or changing pstack's model choices.
---

# Setup pstack

Write `~/.config/pstack-t3/roles.json`, the per-role seat table that every pstack-t3 skill reads through [the runtime](../pstack-runtime/SKILL.md). A project can override roles in `.pstack/t3-roles.json`.

`<runtime>` below is the `pstack-runtime` skill directory next to this one.

## Steps

### 1. Read the catalog

Call `orchestrator_capabilities`. Save its JSON result verbatim to a temporary file, for example `/tmp/pstack-t3-catalog.json`. That file is the only source of valid providers, models, and options. Never write a seat that is not in it.

List the runnable providers (`canRunChildTask: true`) with their first three models. List the providers that are not runnable with their `constraints`, such as "Provider is not authenticated", so the user knows what to fix in T3 settings.

### 2. Load current state

```bash
python3 <runtime>/scripts/roles.py show --cwd "$PWD" --catalog /tmp/pstack-t3-catalog.json
```

This prints every role with its seats and `source` (`default`, the user file, or the project file), already resolved against the catalog. `notes` name seats that no longer match, such as a model T3 dropped.

### 3. Budget, map, and confirm

**(a) Ask for a budget.** Use the host's question tool if it has one. Offer these labels, and name the current budget.

- `default — each model's own default reasoning`
- `unlimited — highest reasoning each model offers`
- `large — xhigh reasoning`
- `medium — high reasoning`
- `small — medium reasoning`

The budget sets the reasoning option (`effort`, `reasoningEffort`, `reasoning_effort`, or `reasoning`) of every seat to its level, or the closest lower level the model offers. A seat that names a lower level keeps it. A model without such an option is unaffected. `ultracode` and `ultrathink` are never set by a budget.

**(b) Propose roles.** Start from the current state. Then suggest a split by strength that uses only runnable providers. A good default when several providers are runnable:

- Code roles (`feature, refactoring`, `bug-fix`, `perf-issue`, `hillclimb`, `swarm workers`, `how explorer`, `why investigators`): the fastest strong coding model the user has.
- Judgment roles (`judgment and prose`, `hardest tasks`, `how explainer`, `why synthesizer`, `reflect judgment, divergent, synthesizer`): the strongest reasoning model.
- `reflect tooling`: a model from a different model family than the judgment model.
- Panel roles (`arena runners`, `arena cross-judge pool`, `architect runners`, `interrogate reviewers`, `verifiers`): one seat per model family (Claude, GPT, Grok, Gemini, and so on), each on a runnable provider. One provider can serve several families, and two providers can serve the same one, so count families, not providers.
- `skill tests`: one cheap fast model from a family other than the thread writing the skill, when the catalog has one. Leave the role unset so the built-in default stays.

Say which model you picked for each tier and why, in one line each. Marking a role `inherit` means it runs on whatever model the calling thread uses.

**(c) Confirm.** Show every role with its seats. Ask whether to accept as-is or change specific roles. For panel roles the seat count is the panel size.

### 4. Write

Build one `--set` per role you are writing. A seat is `inherit` or `provider/model`, with options as a query string. Separate panel seats with `;`.

```bash
python3 <runtime>/scripts/roles.py write --catalog /tmp/pstack-t3-catalog.json --budget large \
  --set "judgment and prose=claudeAgent/claude-opus-5-5?effort=max" \
  --set "swarm workers=grok/grok-4.7" \
  --set "interrogate reviewers=inherit;codex/gpt-6.1-sol;grok/grok-4.7"
```

The provider and model IDs above are examples. Use IDs from step 1.

- The command overwrites the whole file, so re-runs are idempotent. Add `--keep` to keep roles you did not pass.
- It refuses to write a seat that does not match the catalog and prints why. Fix the seat and rerun. Do not pass `--force` unless the user asks.
- Add `--project` to write `.pstack/t3-roles.json` for this repository instead.
- A user-level write also saves the catalog snapshot to `~/.config/pstack-t3/catalog.json`, which `roles.py show` uses later to resolve fallbacks.

### 5. Verify

Run `python3 <runtime>/scripts/roles.py show --cwd "$PWD" --parent "<inheritedProviderInstanceId>/<inheritedModel>"` and check that every role shows the seats you wrote, with no `notes`. `info` lines, such as an `inherit` seat made explicit for the budget, are expected. Then run one smoke delegation to each distinct provider in the table: `delegate_task` with `mode: "wait"`, `timeoutMs: 120000`, the seat's target, and the task "Reply with the single word ready." A seat that fails here is not usable. Fix it and rerun step 4.

### 6. Confirm

Tell the user which file was written, the budget, any provider they could enable in T3 settings to widen the panels, and that new sessions pick it up immediately. Re-running this skill updates it.

### 7. Offer a verification skill (optional)

Check whether the project has a way to drive the real app for proof, such as a `verify-*` skill or an existing harness. If not, offer once: "want a project-local verification skill, so agents can drive the app the way a user does and prove changes work? I can generate one with /create-verification-skill." On yes, invoke `create-verification-skill`. On no, move on.
