# Roles reference

Read this file before you write or validate a roles file, or when `roles.py` prints a note the runtime's Roles section does not explain.

## Built-in defaults

`roles.py show` owns this mapping. A skill must not rebuild it.

There are no built-in model picks. A role set in no roles file resolves to `"inherit"`, with the note `unset in roles.json, so it inherits this thread's seat; run setup to choose a seat`. When the exclude list refuses the inherited parent, the role resolves to what [Excluded seats](#excluded-seats) gives instead, and it carries no unset note. The one exception is `review backups`, which stays `"unset"` with its own note. No unset role resolves to a hard-coded model.

The roles file may carry two optional keys. `exclude` is a list of rules that refuse a seat, per [Excluded seats](#excluded-seats). `backups` maps a role name to a non-empty ordered list of seats (`{"providerInstanceId", "model", ...}`) that `roles.py backup` tries when a seat hits a usage limit, per [failures.md](failures.md#usage-limit). An `inherit` seat is refused in `backups`. `validate` checks both keys, and `roles.py write` keeps them.

A model family is the leading word of the model ID after the normalizer runs. `claude-opus-5-5` is `claude`, `gpt-6.1-sol` is `gpt`, and `grok-4.7` is `grok`. The normalizer in [Claude Haiku 5.5](#claude-haiku-55) runs first, so `us.anthropic.claude-sonnet-5-5-v1:0` is `claude`. Diversity rules compare families, never providers, because one provider can serve another's models.

`review backups` has no built-in seats, so `show` reports `"seats": "unset"` until the user sets it. Once it is set, `show` needs a catalog and reports `"seats": "catalog-required"` without one. With a catalog it prints the seats `backup` keeps when no provider is out and no author is named, and a `dropped <provider>/<model>: <reason>` note for each other seat that [Excluded seats](#excluded-seats) does not skip first. When a seat is dropped, `validate` lists it and exits 1, and `write` refuses the config unless `--force` is passed. When the role keeps fewer than two seats, `validate` prints `review backups: has <n> usable seats and needs 2`, with `seat` in place of `seats` when `<n>` is 1, and exits 1. `write` refuses that config unless `--force` is passed. With a catalog, `show` adds the same clause, without the role name, as the role's last note.

Agreement between seats on the same model is weak evidence. It shows the prompt is stable, not that the finding is right. Weigh consensus only across seats on different models, and say which kind you have.

## Excluded seats

The merged exclude list is the union of the user and project lists. User rules come first, then project rules not already present. The roles file may carry `"exclude"`, a list of rules that refuse a seat. An entry is an object with at least one of `model` (a glob), `options` (an object of option key to value), `provider` (a glob on the provider instance id), and `roles` (a list of role names the rule applies to; an absent `roles` applies to every role). A string entry is refused. A model glob such as `grok-*-fast` goes in `model`, and the object `{"model": "grok-4.7", "options": {"fastMode": true}}` refuses by both. A rule holds when every part holds. A model glob matches when it names the bare id or the normalized id. An option pair holds only when the seat declares that option at that value. A rule with no model applies to every model. `validate` reports an `options` pair no named catalog model can accept, and it does not check a rule whose `provider` or `model` glob matches no catalog model, because a later catalog may add the model.

A seat the exclude list refuses is refused in each place a new seat is chosen: a configured role seat, a backup candidate, and an inherit replacement. The refusal sentence is `the roles file's exclude list refuses this seat for this role`.

- `show` skips a refused configured seat with the note `skipped configured seat <provider>/<model>: <reason>`. A role other than `review backups` that is left with no configured seat resolves to `"inherit"`, settled per the next bullet, or to `"catalog-required"` without a catalog, per the last bullet. A `review backups` role with no configured seat left reports `"seats": []` with a catalog.
- An `inherit` seat whose parent the exclude list refuses becomes that provider's first model the exclude list and the role's OpenCode allow list both allow, preferring the parent's model, with the note `inherit replaced by <provider>/<model>`. When that provider cannot run children or has no allowed model, `show` refuses the role.
- `roles.py backup` skips a refused backup seat.
- `validate` lists each refused seat and exits 1. `write` refuses a seat the rule matches on its own form, even with `--force`.
- Without a catalog, `show` cannot settle an `inherit` seat whose parent the exclude list refuses, and reports `"seats": "catalog-required"`. Call `orchestrator_capabilities` and rerun `show` with `--catalog`.

The Cursor `skill tests` rule stays. Its cause is technical: the cursor driver cannot launch seats. It is matched by `driverKind` (`runs_on_cursor`), not by instance id. `show` adds a note on the `skill tests` entry when a resolved seat runs on the cursor driver. `validate` prints the same sentence as a problem and exits 1. `--launches-seats` refuses a configured `skill tests` seat that resolves to the cursor driver. An unset `skill tests` keeps the note and is not refused.

## Budget

The config may carry `"budget"`: `default`, `small`, `medium`, `large`, or `unlimited`. It caps the reasoning option of every seat that has one (`effort`, `reasoningEffort`, `reasoning_effort`, `reasoning`, or a non-empty `variant` whose every value is a reasoning level and whose type is `select`) at `medium`, `high`, `xhigh`, or the highest level at or below `max`, or the closest lower value the model offers. A `variant` option whose values are not all reasoning levels is not a reasoning option, so a budget leaves it alone. A seat that names its own reasoning level keeps it when it is at or below the budget level, and is lowered to the budget level otherwise. `default` leaves options alone. `unlimited` lowers a configured `ultra` seat to max when the model offers a level at or below max. `default` keeps a configured `ultra` seat. When every offered level is above the cap, the seat gets the lowest level. `ultracode` and `ultrathink` are never chosen by a budget. Under any budget other than `default`, a configured `inherit` seat whose parent model declares a reasoning option and whose provider can run children becomes an explicit target on this thread's provider and model with the budgeted option, because an omitted `target` would pass the parent's reasoning level through. Otherwise it stays `inherit`. A role set in no roles file stays `inherit` under every budget. `roles.py show` reports the budgeted seats.

Under light with budget `default`, `roles.py show` resolves every role except `verifiers` and `review backups` as budget `small`, which caps reasoning at `medium`. Those two resolve on the configured budget, as in full mode, so a gate review seated from `verifiers` keeps its full-mode level. `roles.py` takes a role and no purpose, so every other child seated from `verifiers` keeps its level too. `roles.py backup` follows the same rule for the seat it relaunches or resumes and for the `review backups` panel it prints.

## Fallback

There is no built-in order. A configured seat whose provider is not runnable, or whose model is not in the catalog, falls back on its own. A configured seat the exclude list refuses never takes this path. A `review backups` seat never takes it either. `show` drops a `review backups` seat with a note instead. [Excluded seats](#excluded-seats) says how each command handles it.

1. When the model is missing, use the same provider's first model the exclude list and the role's OpenCode allow list both allow. Prefer the parent's own model when the parent runs on that provider. When neither list leaves a model, `show` refuses the seat.
2. If the provider is not runnable, use `"inherit"`, settled per [Excluded seats](#excluded-seats).
3. Say which seat changed and why. Never silently drop a seat, because the seat count is the panel size.

`roles.py validate --catalog <file>` checks a config against a saved catalog file. Use the file path there. The quoted heredoc in [Where roles live](SKILL.md#where-roles-live) is for `show` when the catalog is still a tool result.

## Claude Haiku 5.5

No role uses Claude Haiku 5.5 by default. The normalizer maps a model id to its canonical form for the Haiku 5.5 identity check. It removes a provider path prefix such as `amazon-bedrock/` and folds case. It strips one leading Bedrock prefix, `anthropic.`, `us.anthropic.`, `eu.anthropic.`, `apac.anthropic.`, or `global.anthropic.`. It treats dots and underscores as hyphens. It strips a Vertex `@YYYYMMDD` suffix and a trailing `-YYYYMMDD` suffix. Haiku 5.5 matches the canonical id `claude-haiku-5-5`. The same normalizer defines the leading word a model family uses, per [Built-in defaults](#built-in-defaults).

Anthropic documents two weak spots that the Haiku 5.5 brief answers. At low effort, Haiku 5.5 stops early in long agent prompts. At low or medium effort, it skips verification on coding work. `roles.py show` prints its two-paragraph Haiku 5.5 brief as `haikuBrief` on every role entry with a seat that runs Haiku 5.5, including `inherit` on a Haiku 5.5 thread. Paste it unchanged at the end of the child's brief, per [Delegation](SKILL.md#delegation) step 4.

A child launches seats when it passes a `target` to `delegate_task` or a `modelSelection` to `t3_thread_launch`. A skill-test child that launches seats resolves with `roles.py show --role "skill tests" --launches-seats`. Pass exactly one `--role "skill tests"`, a catalog, and `--parent`. That form refuses a configured seat that runs on the cursor driver, including a configured `inherit` on a cursor thread. An unset `skill tests` keeps the note and is not refused. A configured non-cursor seat is not limited by `--launches-seats`. It still takes the catalog fallback and the budget cap in [Budget](SKILL.md#budget).

## Where roles live

1. `.pstack/t3-roles.json` in the project root. A role here replaces the same role from the user file.
2. `~/.config/pstack-t3/roles.json`, or `$XDG_CONFIG_HOME/pstack-t3/roles.json`.
3. No file. The role resolves to `"inherit"`, per [Built-in defaults](#built-in-defaults).

Print the merged roles for the current project with:

```bash
python3 <pstack-runtime>/scripts/roles.py show --cwd "$PWD" --parent "<inheritedProviderInstanceId>/<inheritedModel>" [--brief-mode <value> | --session-mode light]
```

When the brief this call seats has a `Mode:` line, replace the bracket with `--brief-mode <value>`. Copy that line's bare value, and pass no other mode flag. When the brief has no `Mode:` line and the session is light, replace the bracket with `--session-mode light`, except for a user's explicit reflect, which passes `--session-mode full`. Otherwise delete the bracket. A call with no mode flag takes its mode from the roles files.

Always pass `--parent` with the values from `orchestrator_capabilities`. The saved catalog records whichever thread ran setup, so it never names this thread. Without `--parent`, `show` cannot settle an `inherit` seat on a thread whose model the exclude list refuses. `show`, `validate`, and `write` reject a malformed `--parent`. See [Excluded seats](#excluded-seats).

`<pstack-runtime>` is the directory holding this file. It sits next to every other pstack skill directory, so from a skill at `<dir>/swarm/SKILL.md` it is `<dir>/pstack-runtime`. Add `--role "<name>"` for one role. The output is small JSON with `source` per role. It resolves against the catalog snapshot that `setup-pstack` saved, if any. A role that reports `"seats": "catalog-required"` needs a catalog. Call `orchestrator_capabilities`. Paste that tool result into this quoted heredoc. If the catalog result is large, save it to a temporary file with the host's file tool and pass that path to `--catalog`.

```bash
python3 <pstack-runtime>/scripts/roles.py show --cwd "$PWD" --catalog - --parent "<inheritedProviderInstanceId>/<inheritedModel>" --role "<name>" [--brief-mode <value> | --session-mode light] <<'JSON'
<the orchestrator_capabilities JSON>
JSON
```

Do not reconstruct role defaults in the calling skill. Pass a file path to `--catalog` when the catalog is already a file. Setup does that. One temporary file feeds `show`, `write`, and the saved snapshot. Fill the bracket by the same rule as the first command.

## Role names

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
| `swarm workers` | 1 | swarm, one seat for every worker |
| `skill tests` | 1 | pstack-author-skill fresh-child test and description eval |
| `architect runners` | N | architect, one runner per seat |
| `interrogate reviewers` | N | interrogate, one reviewer per seat |
| `verifiers` | N | swarm-verify, autopilot, shipping, orchestrate verification |
| `review backups` | N | `roles.py backup`, when `review backups` is set and no `verifiers` backups seat is left |
