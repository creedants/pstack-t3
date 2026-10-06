---
name: setup-pstack
description: Configure which T3 providers and models pstack uses per role and at what reasoning budget. Reads the live T3 catalog and writes a roles file that every pstack skill reads. Use for /setup-pstack, "configure pstack models", "pstack budget", or changing pstack's model choices.
---

# Setup pstack

Write `~/.config/pstack-t3/roles.json`, the per-role seat table that every pstack-t3 skill reads through [the runtime](../pstack-runtime/SKILL.md). A project can override roles in `.pstack/t3-roles.json`.

`<runtime>` below is the `pstack-runtime` skill directory next to this one.

## Steps

### 1. Check the host and read the catalog

Check the host's tool list for `watch_pull_request`. Accept the T3 harness prefixes described in [the runtime](../pstack-runtime/SKILL.md). A tool listed by name counts as present, including a harness prefix. A deferred `watch_pull_request` counts as present and does not fail this gate. If tools load lazily, use the host's tool discovery and make one bounded `orchestrator_capabilities` call before checking again. A provider catalog does not prove that the watch tool exists. Do not call `watch_pull_request` with a dummy PR to test it.

If `watch_pull_request` is absent, stop setup before writing roles or a saved catalog. Say "Setup cannot finish. T3 Code 0.0.46-nightly.20261005.2702 or later is required because this host does not expose watch_pull_request." Ask the user to update T3 Code and rerun setup. Do not report setup complete.

Call `orchestrator_capabilities`. Save its JSON result verbatim to a temporary file, for example `/tmp/pstack-t3-catalog.json`. That file is the only source of valid providers, models, and options. Never write a seat that is not in it.

List the runnable providers (`canRunChildTask: true`) with their first three models. List the providers that are not runnable with their `constraints`, such as "Provider is not authenticated", so the user knows what to fix in T3 settings.

### 2. Load current state

```bash
python3 <runtime>/scripts/roles.py show --cwd "$PWD" --catalog /tmp/pstack-t3-catalog.json
```

This prints every role with its seats and `source` (`default`, the user file, or the project file), already resolved against the catalog. `notes` name seats that no longer match, such as a model T3 dropped.

### 3. Budget, map, and confirm

**(a) Ask for a budget.** Use the host's question tool if it has one. Offer these labels, and name the current budget.

- `default — built-in role reasoning or configured seat options`
- `unlimited — highest reasoning each model offers`
- `large — xhigh reasoning`
- `medium — high reasoning`
- `small — medium reasoning`

The budget caps the reasoning option (`effort`, `reasoningEffort`, `reasoning_effort`, or `reasoning`). A seat at or below the cap keeps its level. A seat above the cap drops to the cap, or the closest lower level the model offers. A seat that names no level receives the cap. `default` leaves a built-in or configured level as it is. `unlimited` does not raise a seat that already names xhigh. A model without such an option is unaffected. `ultracode` and `ultrathink` are never set by a budget.

**(b) Propose roles.** Start from `roles.py show` in step 2. Keep configured roles unless the user changes them. Use the built-in seats for roles with `source: "default"`. Offer `large` for a new setup. It matches the built-in xhigh ceiling. Show each fallback note beside its role and seat. Codex and every other runnable configured provider remain available as user choices. Leave `skill tests` unset so its adaptive default stays.

**(c) Confirm.** Show every role with its seats. Ask whether to accept as-is or change specific roles. For panel roles the seat count is the panel size.

### 4. Write

Build one `--set` per role you are writing. A seat is `inherit` or `provider/model`, with options as a query string. Separate panel seats with `;`.

```bash
python3 <runtime>/scripts/roles.py write --catalog /tmp/pstack-t3-catalog.json --budget large \
  --set "judgment and prose=claudeAgent/claude-opus-5-5?effort=xhigh" \
  --set "swarm workers=grok/grok-4.7?reasoningEffort=xhigh" \
  --set "interrogate reviewers=claudeAgent/claude-opus-5-5?effort=xhigh;grok/grok-4.7?reasoningEffort=xhigh"
```

The provider, model, and option IDs above are examples. Use IDs from step 1. Add `fastMode` only when that catalog model declares the boolean option.

- The command overwrites the whole file, so re-runs are idempotent. Add `--keep` to keep roles you did not pass.
- It refuses to write a seat that does not match the catalog and prints why. Fix the seat and rerun. Do not pass `--force` unless the user asks.
- Add `--project` to write `.pstack/t3-roles.json` for this repository instead.
- A user-level write also saves the catalog snapshot to `~/.config/pstack-t3/catalog.json`, which `roles.py show` uses later to resolve fallbacks.

### 5. Verify

Run `python3 <runtime>/scripts/roles.py show --cwd "$PWD" --parent "<inheritedProviderInstanceId>/<inheritedModel>"`. Check that configured seats resolve with no mismatch notes. For unset roles, review and report each default fallback note. A default fallback does not invalidate an otherwise runnable setup. `info` lines, such as an `inherit` seat made explicit for the budget, are expected. Then run one smoke delegation to each distinct provider in the table: `delegate_task` with `mode: "wait"`, `timeoutMs: 120000`, the seat's target, and the task "Reply with the single word ready." A seat that fails here is not usable. Fix it and rerun step 4.

### 6. Confirm

Do this only after step 1 found `watch_pull_request` and step 5's smoke delegations succeeded. Tell the user which file was written, the budget, any provider they could enable in T3 settings to widen the panels, and that new sessions pick it up immediately. Re-running this skill updates it.

### 7. Offer a verification skill (optional)

Check whether the project has a way to drive the real app for proof, such as a `verify-*` skill or an existing harness. If not, offer once: "want a project-local verification skill, so agents can drive the app the way a user does and prove changes work? I can generate one with /create-verification-skill." On yes, invoke `create-verification-skill`. On no, move on.
