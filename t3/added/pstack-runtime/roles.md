# Roles reference

Read this file before you write or validate a roles file, or when `roles.py` prints a note the runtime's Roles section does not explain.

## Built-in defaults

`roles.py show` owns this mapping. A skill must not rebuild it.

Code roles (`feature, refactoring`, `bug-fix`, `perf-issue`, `hillclimb`, `swarm workers`, `reflect tooling`) use `grok-4.7` at xhigh. Judgment roles (`judgment and prose`, `hardest tasks`, `how explainer`, `why synthesizer`, `reflect judgment, divergent, synthesizer`) use `claude-opus-5-5` at xhigh. `arena runners`, `arena cross-judge pool`, `architect runners`, and `interrogate reviewers` use those two seats in that order. Every seat follows [Excluded seats](#excluded-seats), so a `grok-4.7` seat on a provider whose model declares `fastMode` comes back with `"fastMode": false`.

`skill tests` uses `claude-haiku-5-5` at high. `how explorer` and `why investigators` use `claude-haiku-5-5` at medium, per [Claude Haiku 5.5](#claude-haiku-55).

`verifiers` starts with `"inherit"` when this thread's provider can run children, then adds one seat per other runnable provider (`canRunChildTask: true`), using that provider's first pickable model and skipping a family already seated. A thread on an excluded model gets no `"inherit"` seat, and its own provider joins with its first pickable model instead. With only one runnable provider, `verifiers` is three `"inherit"` seats, or three copies of the one runnable provider's first pickable model when this thread cannot be inherited, and the report must say the models did not differ. The other panel roles do not use that three-seat rule.

A model family is the leading word of the model ID. `claude-opus-5-5` is `claude`, `gpt-6.1-sol` is `gpt`, and `grok-4.7` is `grok`. The normalizer in [Excluded seats](#excluded-seats) runs first, so `us.anthropic.claude-sonnet-5-5-v1:0` is `claude`. Diversity rules compare families, never providers, because one provider can serve another's models.

A preferred seat that matches this thread stays an explicit target. It does not become `"inherit"`.

When the preferred model is missing, the seat stays and a numbered note names the role, the seat number, the wanted model, and the replacement. The replacement is the first pickable runnable model of that family, then this thread's model when its provider can run children and the model is pickable, then the first pickable runnable model in the catalog. No runnable provider is an error. The panel keeps every seat. When two seats land on the same model, the note says the panel lost a distinct model.

Without a catalog, a preferred role reports `"seats": "catalog-required"`. `skill tests` reports `"seats": "catalog-required"` like the other preferred roles. `verifiers` reports `"seats": "default-panel"`.

`review backups` has no built-in seats, so `show` reports `"seats": "unset"` until the user sets it. Once it is set, `show` needs a catalog and reports `"seats": "catalog-required"` without one. With a catalog it prints the seats `backup` keeps when no provider is out and no author is named, and a `dropped <provider>/<model>: <reason>` note for each other seat that [Excluded seats](#excluded-seats) does not skip first. When a seat is dropped, `validate` lists it and exits 1, and `write` refuses the config unless `--force` is passed. When the role keeps fewer than two seats, `validate` prints `review backups: has <n> usable seats and needs 2`, with `seat` in place of `seats` when `<n>` is 1, and exits 1. `write` refuses that config unless `--force` is passed. With a catalog, `show` adds the same clause, without the role name, as the role's last note.

Agreement between seats on the same model is weak evidence. It shows the prompt is stable, not that the finding is right. Weigh consensus only across seats on different models, and say which kind you have.

## Excluded seats

pstack never runs a fast Grok model or Claude Haiku 4.5 as a seat or a worker. A fast Grok seat is a grok-family model whose id carries a `fast` token, such as `grok-4.7-build-fast`, or a Grok seat with `fastMode` set to `true`. `grok-build` has no `fast` token. A Claude Haiku 4.5 seat has a model id such as `claude-haiku-4-5`, `claude-haiku-4-5-20251001`, `claude-haiku-4-5@20251001`, or `anthropic/claude-haiku-4.5`. One normalizer maps that id to its canonical form before the Haiku 4.5 exclusion and before the Haiku 5.5 identity check. It removes a provider path prefix such as `amazon-bedrock/` and folds case. It strips one leading Bedrock prefix, `anthropic.`, `us.anthropic.`, `eu.anthropic.`, `apac.anthropic.`, or `global.anthropic.`. It treats dots and underscores as hyphens. It strips a Vertex `@YYYYMMDD` suffix and a trailing `-YYYYMMDD` suffix. Haiku 4.5 then matches `claude-haiku-4-5` at a hyphen boundary. Haiku 5.5 matches the canonical id `claude-haiku-5-5`. A pickable model is one that is not excluded. Every automatic pick in this file takes a pickable model.

- No picker chooses an excluded id.
- Every seat on a Grok model that declares a boolean `fastMode` option comes back with `"fastMode": false`. No mode and no budget sets it to `true`. pstack never adds `fastMode` to a model of another family.
- An `inherit` seat on an excluded id becomes that provider's first pickable model, with the note `inherit replaced by <provider>/<model>`. When that provider cannot run children or has no pickable model, `show` refuses the role.
- An `inherit` seat on a Grok model that declares `fastMode` becomes an explicit seat on that model with `"fastMode": false`, and the info `inherit made explicit as <provider>/<model> so fastMode stays false`. When that provider cannot run children, `fastMode` cannot be pinned, so `show` refuses the role.
- `show` skips a configured excluded seat with the note `skipped configured seat <provider>/<model>`. When no configured seat is left, the role uses its built-in default. `validate` lists each one and exits 1. `write` refuses it with `refusing to write, even with --force`. With a catalog, a `review backups` role with no configured seat left reports `"seats": []`.
- `verifiers` gets no `inherit` seat for an excluded parent, with the note `skipped inherit of <provider>/<model>`, per [Built-in defaults](#built-in-defaults).
- Without a catalog, a role whose seats include `inherit`, and the `verifiers` default panel, report `"seats": "catalog-required"` when `--parent` names an excluded id. Call `orchestrator_capabilities` and rerun `show` with `--catalog`.

## Budget

The config may carry `"budget"`: `default`, `small`, `medium`, `large`, or `unlimited`. It caps the reasoning option of every seat that has one (`effort`, `reasoningEffort`, `reasoning_effort`, or `reasoning`) at `medium`, `high`, `xhigh`, or the highest level at or below `max`, or the closest lower value the model offers. A seat that names its own reasoning level keeps it when it is at or below the budget level, and is lowered to the budget level otherwise. `default` leaves options alone. The built-in Opus and Grok seats name xhigh, so `default` leaves that level in place. The ladder stops at max. `unlimited` raises those two seats to the model's highest level at or below max. The default Opus seat moves from xhigh to max. Grok stays at xhigh, the top of its ladder. `unlimited` does not raise the Claude Haiku 5.5 seats, per [Claude Haiku 5.5](#claude-haiku-55). `unlimited` lowers a configured `ultra` seat to max when the model offers a level at or below max. `default` keeps a configured `ultra` seat. When every offered level is above the cap, the seat gets the lowest level. Verifiers use those same levels. `ultracode` and `ultrathink` are never chosen by a budget. Under any budget other than `default`, an `inherit` seat becomes an explicit target on this thread's provider and model with the budgeted option, because an omitted `target` would pass the parent's reasoning level through. `roles.py show` reports the budgeted seats.

Under light with budget `default`, `roles.py show` resolves every role except `verifiers` and `review backups` as budget `small`, which caps reasoning at `medium`. Those two resolve on the configured budget, as in full mode, so a gate review seated from `verifiers` keeps its full-mode level. `roles.py` takes a role and no purpose, so every other child seated from `verifiers` keeps its level too. `roles.py backup` follows the same rule for the seat it relaunches or resumes and for the `review backups` panel it prints.

## Fallback

Built-in preferred seats use the numbered notes from [Built-in defaults](#built-in-defaults). Those notes name each replacement, and the panel size never shrinks.

`roles.py validate --catalog <file>` checks a config against a saved catalog file. Use the file path there. The quoted heredoc in [Where roles live](SKILL.md#where-roles-live) is for `show` when the catalog is still a tool result.

## Claude Haiku 5.5

`skill tests`, `how explorer`, and `why investigators` use Claude Haiku 5.5 by default. `skill tests` runs at high effort. `how explorer` and `why investigators` run at medium. Anthropic positions Haiku 5.5 for high-volume, latency-sensitive work such as classification, routing, extraction, and sub-agent tasks, and for reading-heavy workers that search, read, and extract with little hard reasoning. A worker that needs more judgment takes Sonnet, and hard work takes Opus. Effort defaults to medium, and high fits work where instruction following matters. `unlimited` does not raise these seats.

Anthropic documents two weak spots that the Haiku 5.5 brief answers. At low effort, Haiku 5.5 stops early in long agent prompts. At low or medium effort, it skips verification on coding work. `roles.py show` prints its two-paragraph Haiku 5.5 brief as `haikuBrief` on every role entry with a seat that runs Haiku 5.5, including `inherit` on a Haiku 5.5 thread. Paste it unchanged at the end of the child's brief, per [Delegation](SKILL.md#delegation) step 4.

A child launches seats when it passes a `target` to `delegate_task` or a `modelSelection` to `t3_thread_launch`. A skill-test child that launches seats resolves with `roles.py show --role "skill tests" --launches-seats`. Pass exactly one `--role "skill tests"`, a catalog, and `--parent`.

The pool first uses the providers named by explicit configured seats in single-seat roles, never cursor. If no such provider remains, it uses every runnable provider that serves a pickable model of a built-in default family, `claude` or `grok`, plus this thread's provider. It removes cursor from that pool too. Cursor's harness sends `target.options` as a JSON string, which T3 refuses. An empty pool refuses with `cannot launch seats`.

A configured `skill tests` seat on another provider uses the configured-seat resolution rules in [Fallback](SKILL.md#fallback). The pool does not limit that seat. A configured `skill tests` seat on cursor is ignored in favor of the built-in default. Ignoring cursor adds no note. A seat that resolves to cursor is refused with `cannot launch seats`, including `inherit` on a cursor thread.
