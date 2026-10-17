# Roles reference

Read this file before you write or validate a roles file, or when `roles.py` prints a note the runtime's Roles section does not explain.

## Built-in defaults

`roles.py show` owns this mapping. A skill must not rebuild it.

There are no built-in model picks. A role set in no roles file resolves to `"inherit"`, with the note `unset in roles.json, so it inherits this thread's seat; run setup to choose a seat`. The one exception is `review backups`, which stays `"unset"` with its own note. No unset role resolves to a hard-coded model.

The roles file may carry two optional keys. `exclude` is a list of rules that refuse a seat, per [Excluded seats](#excluded-seats). `backups` maps a role name to a non-empty ordered list of seats (`{"providerInstanceId", "model", ...}`) that `roles.py backup` tries when a seat hits a usage limit, per [failures.md](failures.md#usage-limit). An `inherit` seat is refused in `backups`. `validate` checks both keys, and `roles.py write` keeps them.

A model family is the leading word of the model ID after the normalizer runs. `claude-opus-5-5` is `claude`, `gpt-6.1-sol` is `gpt`, and `grok-4.7` is `grok`. The normalizer in [Claude Haiku 5.5](#claude-haiku-55) runs first, so `us.anthropic.claude-sonnet-5-5-v1:0` is `claude`. Diversity rules compare families, never providers, because one provider can serve another's models.

`review backups` has no built-in seats, so `show` reports `"seats": "unset"` until the user sets it. Once it is set, `show` needs a catalog and reports `"seats": "catalog-required"` without one. With a catalog it prints the seats `backup` keeps when no provider is out and no author is named, and a `dropped <provider>/<model>: <reason>` note for each other seat that [Excluded seats](#excluded-seats) does not skip first. When a seat is dropped, `validate` lists it and exits 1, and `write` refuses the config unless `--force` is passed. When the role keeps fewer than two seats, `validate` prints `review backups: has <n> usable seats and needs 2`, with `seat` in place of `seats` when `<n>` is 1, and exits 1. `write` refuses that config unless `--force` is passed. With a catalog, `show` adds the same clause, without the role name, as the role's last note.

Agreement between seats on the same model is weak evidence. It shows the prompt is stable, not that the finding is right. Weigh consensus only across seats on different models, and say which kind you have.

## Excluded seats

The roles file may carry `"exclude"`, a list of rules that refuse a seat. An entry is an object with `model` (a glob) and/or `options` (an object of option key to value). A string entry is refused. A model glob such as `grok-*-fast` goes in `model`, and the object `{"model": "grok-4.7", "options": {"fastMode": true}}` refuses by both. A rule holds when every part holds. A model glob matches when it names the bare id or the normalized id. An option pair holds only when the seat declares that option at that value. A rule with no model applies to every model.

A seat the exclude list refuses is refused in each place a seat is chosen: a configured role seat, a backup candidate, and an inherit replacement. The refusal sentence is `the roles file's exclude list refuses that model in every role, backup, and inherit replacement`.

- `show` skips a refused configured seat with the note `skipped configured seat <provider>/<model>: <reason>`. When no configured seat is left, the role resolves to `"inherit"`. A `review backups` role with no configured seat left reports `"seats": []` with a catalog.
- An `inherit` seat whose parent the exclude list refuses becomes that provider's first model the list allows, with the note `inherit replaced by <provider>/<model>`. When that provider cannot run children or has no allowed model, `show` refuses the role.
- `roles.py backup` skips a refused backup seat.
- `validate` lists each refused seat and exits 1. `write` refuses the config, even with `--force`.
- Without a catalog, `show` cannot settle an `inherit` seat whose parent the exclude list refuses, and reports `"seats": "catalog-required"`. Call `orchestrator_capabilities` and rerun `show` with `--catalog`.

The Cursor `skill tests` rule stays. Its cause is technical: the cursor driver cannot launch seats. It is matched by `driverKind` (`runs_on_cursor`), not by instance id. `show` adds a note on the `skill tests` entry when a resolved seat runs on the cursor driver. `validate` prints the same sentence as a problem and exits 1. `--launches-seats` refuses a `skill tests` seat that resolves to the cursor driver, including `inherit` on a cursor thread.

## Budget

The config may carry `"budget"`: `default`, `small`, `medium`, `large`, or `unlimited`. It caps the reasoning option of every seat that has one (`effort`, `reasoningEffort`, `reasoning_effort`, or `reasoning`) at `medium`, `high`, `xhigh`, or the highest level at or below `max`, or the closest lower value the model offers. A seat that names its own reasoning level keeps it when it is at or below the budget level, and is lowered to the budget level otherwise. `default` leaves options alone. `unlimited` lowers a configured `ultra` seat to max when the model offers a level at or below max. `default` keeps a configured `ultra` seat. When every offered level is above the cap, the seat gets the lowest level. `ultracode` and `ultrathink` are never chosen by a budget. Under any budget other than `default`, a configured `inherit` seat whose parent model declares a reasoning option and whose provider can run children becomes an explicit target on this thread's provider and model with the budgeted option, because an omitted `target` would pass the parent's reasoning level through. Otherwise it stays `inherit`. A role set in no roles file stays `inherit` under every budget. `roles.py show` reports the budgeted seats.

Under light with budget `default`, `roles.py show` resolves every role except `verifiers` and `review backups` as budget `small`, which caps reasoning at `medium`. Those two resolve on the configured budget, as in full mode, so a gate review seated from `verifiers` keeps its full-mode level. `roles.py` takes a role and no purpose, so every other child seated from `verifiers` keeps its level too. `roles.py backup` follows the same rule for the seat it relaunches or resumes and for the `review backups` panel it prints.

## Fallback

There is no built-in order. A configured seat whose provider is not runnable, or whose model is not in the catalog, falls back on its own. A configured seat the exclude list refuses never takes this path. A `review backups` seat never takes it either. `show` drops a `review backups` seat with a note instead. [Excluded seats](#excluded-seats) says how each command handles it.

1. When the model is missing, use the same provider's first model the exclude list allows. When it allows none, `show` refuses the seat with the exclude sentence.
2. If the provider is not runnable, use `"inherit"`, settled per [Excluded seats](#excluded-seats).
3. Say which seat changed and why. Never silently drop a seat, because the seat count is the panel size.

`roles.py validate --catalog <file>` checks a config against a saved catalog file. Use the file path there. The quoted heredoc in [Where roles live](SKILL.md#where-roles-live) is for `show` when the catalog is still a tool result.

## Claude Haiku 5.5

No role uses Claude Haiku 5.5 by default. The normalizer maps a model id to its canonical form for the Haiku 5.5 identity check. It removes a provider path prefix such as `amazon-bedrock/` and folds case. It strips one leading Bedrock prefix, `anthropic.`, `us.anthropic.`, `eu.anthropic.`, `apac.anthropic.`, or `global.anthropic.`. It treats dots and underscores as hyphens. It strips a Vertex `@YYYYMMDD` suffix and a trailing `-YYYYMMDD` suffix. Haiku 5.5 matches the canonical id `claude-haiku-5-5`. The same normalizer defines the leading word a model family uses, per [Built-in defaults](#built-in-defaults).

Anthropic documents two weak spots that the Haiku 5.5 brief answers. At low effort, Haiku 5.5 stops early in long agent prompts. At low or medium effort, it skips verification on coding work. `roles.py show` prints its two-paragraph Haiku 5.5 brief as `haikuBrief` on every role entry with a seat that runs Haiku 5.5, including `inherit` on a Haiku 5.5 thread. Paste it unchanged at the end of the child's brief, per [Delegation](SKILL.md#delegation) step 4.

A child launches seats when it passes a `target` to `delegate_task` or a `modelSelection` to `t3_thread_launch`. A skill-test child that launches seats resolves with `roles.py show --role "skill tests" --launches-seats`. Pass exactly one `--role "skill tests"`, a catalog, and `--parent`. That form refuses a resolved seat that runs on the cursor driver, including `inherit` on a cursor thread. A configured non-cursor seat is not limited by `--launches-seats`. It still takes the catalog fallback and the budget cap in [Budget](SKILL.md#budget).
