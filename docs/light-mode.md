# Light mode

Light mode is one setting with the values `full` and `light`. Full mode runs every step as each skill says. Light mode keeps every check that decides whether code lands and cuts the fan-out around it. The light behavior of each spawn lives in [the runtime's Modes section](../t3/runtime.md#modes). This page describes the setting as it works today.

In this document a coordinator is one standing brigade coordinator thread and its store directory. A work item is one unit of work handed to a worker. An attempt is one worker run on a work item.

The setting is on `main`.


Part of the pstack-t3 docs. Each part below names its file. Read the part it names for the full rule.
## The setting

### Where it lives

The setting is one key named `mode` with the values `full` and `light`. The user and project levels live in the files that already hold `budget`. The coordinator level lives in `restaurant.json`. When no level sets a mode, the default is `light`.

| Level | Where | Written by |
| --- | --- | --- |
| User | `~/.config/pstack-t3/roles.json` | `setup-pstack`, `roles.py write --mode` |
| Project | `<repo>/.pstack/t3-roles.json` | `setup-pstack --project`, `roles.py write --project --mode` |
| Coordinator | `<store>/restaurant.json` | `brigade.py open --mode`, `brigade.py set --mode` |
| Session | The user words for that thread only | The user |
| Brief | A `Mode:` line in a child brief | The parent that wrote the brief |

### One effective mode

The highest level present wins. The order is brief, then session, then coordinator, then project, then user, then `light`. `roles.py` owns this order in `effective_mode` in `t3/scripts/roles.py`.

`roles.py show` and `roles.py mode` both accept `--brief-mode`, `--session-mode`, and `--coordinator-mode`. Each takes `full` or `light`. `show` prints `mode` and `modeSource` keys. `mode` prints `Mode:` and `Mode source:` lines. `show` then resolves every seat under that mode.

A brief `Mode:` line is the frozen decision. A child copies that line bare value into `--brief-mode` on every `roles.py mode` and `roles.py show` call it makes. It passes no other mode flag. It never resolves the mode again.

A parent writes `Mode: <effective mode>` into every brief whose child resolves seats or spawns. That covers brigade workers, code delegates, Autopilot and Orchestrate owners, sub-coordinators, unit workers, visual-parity owners, and landing writers. Read-only leaves such as a `how` explainer get no line because they resolve nothing.

A session in light mode passes `--session-mode light` to every `roles.py` call with no brief value, except an explicit reflect, which passes `--session-mode full`. It writes `Mode: light` into its children briefs.

The brief has to carry the decision because a child can miss the project file. `.gitignore` lists `/.pstack/`. A new worktree has no `.pstack/t3-roles.json`. `project_config_path` in `t3/scripts/roles.py` stops at the worktree `.git` file. A worker that ran `show --cwd` in its worktree would read only the user file. For the same reason `roles.py mode` runs with `--cwd` set to the project root, where the project `escalate` list lives.

The coordinator resolves the worker `modelSelection` when it launches the worker. It resolves the gate verifier seat when it launches the verifier. It resolves both with `show --brief-mode <the item mode>`. It never uses `--coordinator-mode` there. An escalated item worker and gate get full seats.

A coordinator set to `light` over a project set to `full` writes `Mode: light` into its worker brief, unless the item kind has a recorded review depth that says otherwise. The worker runs `roles.py show --brief-mode light`. With budget `default` the seats it and its delegates resolve outside the `verifiers` and `review backups` roles use budget `small`. A child escalated to `full` under a project set to `light` gets `Mode: full`. Its `roles.py show --brief-mode full` applies no light cap.

### The reasoning cap

When the effective mode is `light` and the merged budget is `default`, `show` resolves the seats of every role except `verifiers` and `review backups` as budget `small`. `BUDGETS` in `t3/scripts/roles.py` maps `small` to a `medium` reasoning cap. An explicit budget wins in both directions. `light` with `large` keeps xhigh seats. `full` with `small` keeps the `medium` cap it already has. Under budget `small`, a configured `inherit` seat becomes an explicit target with a budgeted effort option when the catalog names the parent, the parent provider can run children, and the parent model has an effort option. The budget chooses the highest offered reasoning level at or below `medium`, or the lowest offered level when every level is above it. That budget conversion needs a catalog and the right parent. Without a catalog `resolve` caps nothing. With a catalog and no `--parent`, explicit seats are capped but an `inherit` seat either stays `inherit` or converts against the catalog saved parent. Under `light` with budget `default`, `show` without a catalog prints an `info` line that no seat was capped. With a catalog but without `--parent`, it prints an `info` line that an `inherit` seat can keep the parent reasoning. Neither line prints when every role the call resolves is `verifiers` or `review backups`. [The runtime's Modes section](../t3/runtime.md#modes) already requires both.

The cap is the only option light mode changes. It sets no other option, so it never changes `fastMode`. A user refuses a fast model by listing it in the roles file's `exclude` list. [Excluded seats](../t3/runtime.md#excluded-seats) states the rule.

An escalated item resolves as `full`. It keeps its full reasoning level.

### Escalation

`roles.py mode` decides escalation from inputs it can compute when brigade writes a brief. Escalation only moves toward `full`.

1. A high-risk path. The project file `escalate` key lists path patterns. There is no built-in default. `setup-pstack --project` suggests `**/migrations/**` and the project own lock and installer scripts. For pstack-t3 those scripts are `t3/added/landing/scripts/land.py`, `t3/added/brigade/scripts/brigade.py`, and `scripts/install.py`.
2. A second send-back. `roles.py mode --send-backs <n>` escalates at 2. Brigade counts `n` from the item `send-back` rows in `pass.tsv`. It never counts from the attempt number. A replacement worker or a queue bounce adds an attempt without a send-back.
3. A contested design. A light worker that finds its design contested does not run `interrogate` because light mode forbids it. It stops at a verifiable point and reports `Contested: <reason>` under its status. The coordinator then records the escalation and launches a fresh worker in `full` with that report. The new attempt is a replacement, not a send-back. The gate does not review the stopped light attempt. The coordinator records it with `brigade.py --owner <thread>@<generation> dish <item> --mode full --reason "<text>"` after a worker report says `Contested: <reason>`. At fire time it uses `fire --mode full --reason "<text>"` when the tickets name a lock, a lease, a race, a migration, or history rewriting.

The path rule has exact semantics.

- Normalization. A lease path or pattern goes through `canonical` in `t3/scripts/roles.py`. That is the function that already normalizes leases. The result is POSIX and relative to the repository root. Backslashes become `/`. Then `normpath` runs. Then a leading `/` drops. An empty result is the repository root. A path that leaves the repository is an error.
- Pattern match. Patterns match whole segments. `**` matches zero or more segments. Inside one segment `*`, `?`, and `[...]` follow `fnmatch.fnmatchcase` and never cross a `/`.
- Coverage. A lease covers its own path and every tracked file under it, from `git ls-files` in the coordinator checkout when the brief is written. A file lease therefore covers one path, tracked or new. A directory lease covers its tracked descendants. The root lease covers every tracked file.
- Rule. The item escalates when any covered path matches any pattern.

The escalation is durable. When rule 1, 2, or 3 first fires, `brigade.py` appends a `log.tsv` row with kind `mode`, the item id, state `full`, and the reason as its note. It appends under the store lock and the `--owner` fence, like every other store write. `log.tsv` is append-only and needs no new column. Existing stores need no migration. Every later `brief` for the item reads its latest `mode` row first and passes `--escalated "<reason>"` to `roles.py mode`. A later change to the coordinator, project, or user setting cannot return the item to `light`. Nothing moves an item from `full` to `light` mid-flight.

`roles.py mode` prints the result in brief grammar, as in [Brief lines](light-mode/rules.md#brief-lines).

### How the user picks it

- At setup. `setup-pstack` asks for the mode right after the budget, with the host question tool, and names the current value. The options are `light` (recommended) and `full`. `roles.py write --mode` saves it.
- When opening a coordinator. The brigade Open step asks for the mode with the reporting level. `brigade.py open --mode light|full` records it in `restaurant.json`. Leaving it out records nothing. The project and user files then decide.
- For one session. `$poteto-mode light` or "use light mode" in a request sets the session level. "full mode" sets it back.

### How it changes later

- `brigade.py set --mode light|full` changes a coordinator. `set --mode ""` returns it to the project and user files. The change applies to the next brief. A running attempt keeps the mode its brief names.
- `roles.py write --mode` changes the user or project file. New sessions read it. A running coordinator reads it at its next brief.
- When a provider in the `verifiers` or code roles hits its usage limit, the coordinator first relaunches or resumes the failed worker or verifier with `roles.py backup`, per [Failure handling](../t3/runtime.md#failure-handling). The mode stays as it was. The coordinator does not switch modes on its own. Whatever `backup` prints, it then parks the choice with `86 add`, defaulting to `light` until the limit resets. It switches only when the user answers or the menu `## Budget` says to. A usage limit is the user budget call.

### What the agent says

When light mode applies, the thread says so once, at the start, in two or three short sentences. One example is "Light mode is on, from this coordinator setting. This change gets one design sketch and no separate comment review. Tests, the build, and review run, with a fallback if only the author's family can run."

On an escalation it names the rule. One example is "This change runs in full mode because its lease covers `land.py`."

The worker report repeats the brief `Mode:` and `Waived by mode:` lines under its status. Steps waived by the mode are not deviations. The coordinator digest replies name the mode only when it changes.

## Decision

The gate review keeps its full level in light mode. A trial paired reviews at `medium` and at full level on 8 heads. The `medium` review missed a blocking finding at 3 of 8 heads. The worker-side cuts remain.

## Map

The setting and the decision stay here. The changes live in parts.

| Section | Part |
| --- | --- |
| What light mode changes plus never cut plus gate review | [changes](light-mode/changes.md#what-light-mode-changes) |
| Waivers plus brief lines plus checks plus fresh worker | [rules](light-mode/rules.md#waivers) |
