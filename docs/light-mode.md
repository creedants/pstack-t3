# Light mode

Light mode is one setting with the values `full` and `light`. Full mode runs every step as each skill says. Light mode keeps every check that decides whether code lands and cuts the fan-out around it. The light behavior of each spawn lives in [the runtime's Modes section](../t3/runtime.md#modes). This page describes the setting as it works today.

In this document a coordinator is one standing brigade coordinator thread and its store directory. A work item is one unit of work handed to a worker. An attempt is one worker run on a work item.

The setting is on `main`.

## The setting

### Where it lives

The setting is one key named `mode` with the values `full` and `light`. The user and project levels live in the files that already hold `budget`. The coordinator level lives in `restaurant.json`. A missing key reads as `full`.

| Level | Where | Written by |
| --- | --- | --- |
| User | `~/.config/pstack-t3/roles.json` | `setup-pstack`, `roles.py write --mode` |
| Project | `<repo>/.pstack/t3-roles.json` | `setup-pstack --project`, `roles.py write --project --mode` |
| Coordinator | `<store>/restaurant.json` | `brigade.py open --mode`, `brigade.py set --mode` |
| Session | The user words for that thread only | The user |
| Brief | A `Mode:` line in a child brief | The parent that wrote the brief |

### One effective mode

The highest level present wins. The order is brief, then session, then coordinator, then project, then user, then `full`. `roles.py` owns this order in `effective_mode` in `t3/scripts/roles.py`.

`roles.py show` and `roles.py mode` both accept `--brief-mode`, `--session-mode`, and `--coordinator-mode`. Each takes `full` or `light`. `show` prints `mode` and `modeSource` keys. `mode` prints `Mode:` and `Mode source:` lines. `show` then resolves every seat under that mode.

A brief `Mode:` line is the frozen decision. A child copies that line bare value into `--brief-mode` on every `roles.py mode` and `roles.py show` call it makes. It passes no other mode flag. It never resolves the mode again.

A parent writes `Mode: <effective mode>` into every brief whose child resolves seats or spawns. That covers brigade workers, code delegates, Autopilot and Orchestrate owners, sub-coordinators, unit workers, visual-parity owners, and landing writers. Read-only leaves such as a `how` explainer get no line because they resolve nothing.

A session in light mode passes `--session-mode light` to every `roles.py` call with no brief value, except an explicit reflect, which passes `--session-mode full`. It writes `Mode: light` into its children briefs.

The brief has to carry the decision because a child can miss the project file. `.gitignore` lists `/.pstack/`. A new worktree has no `.pstack/t3-roles.json`. `project_config_path` in `t3/scripts/roles.py` stops at the worktree `.git` file. A worker that ran `show --cwd` in its worktree would read only the user file. For the same reason `roles.py mode` runs with `--cwd` set to the project root, where the project `escalate` list lives.

The coordinator resolves the worker `modelSelection` when it launches the worker. It resolves the gate verifier seat when it launches the verifier. It resolves both with `show --brief-mode <the item mode>`. It never uses `--coordinator-mode` there. An escalated item worker and gate get full seats.

A coordinator set to `light` over a project set to `full` writes `Mode: light` into its worker brief, unless the item kind has a recorded review depth that says otherwise. The worker runs `roles.py show --brief-mode light`. With budget `default` the seats it and its delegates resolve outside the `verifiers` and `review backups` roles use budget `small`. A child escalated to `full` under a project set to `light` gets `Mode: full`. Its `roles.py show --brief-mode full` applies no light cap.

### The reasoning cap

When the effective mode is `light` and the merged budget is `default`, `show` resolves the seats of every role except `verifiers` and `review backups` as budget `small`. `BUDGETS` in `t3/scripts/roles.py` maps `small` to a `medium` reasoning cap. An explicit budget wins in both directions. `light` with `large` keeps xhigh seats. `full` with `small` keeps the `medium` cap it already has. Under budget `small`, an `inherit` seat becomes an explicit target with a budgeted effort option when the catalog names the parent, the parent provider can run children, and the parent model has an effort option. The budget chooses the highest offered reasoning level at or below `medium`, or the lowest offered level when every level is above it. That budget conversion needs a catalog and the right parent. Without a catalog `resolve` caps nothing. With a catalog and no `--parent`, explicit seats are capped but an `inherit` seat either stays `inherit` or converts against the catalog saved parent. Under `light` with budget `default`, `show` without a catalog prints an `info` line that no seat was capped. With a catalog but without `--parent`, it prints an `info` line that an `inherit` seat can keep the parent reasoning. Neither line prints when every role the call resolves is `verifiers` or `review backups`. [The runtime's Modes section](../t3/runtime.md#modes) already requires both.

The cap is the only option light mode changes. Light mode never sets `fastMode` to true. With a catalog, a Grok seat on a model that declares `fastMode` comes back from `show` with `fastMode` set to false. That includes a seat that falls back to the built-in Grok default. [Excluded seats](../t3/runtime.md#excluded-seats) states the rule.

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

`roles.py mode` prints the result in brief grammar, as in [Brief lines](#brief-lines).

### How the user picks it

- At setup. `setup-pstack` asks for the mode right after the budget, with the host question tool, and names the current value. The options are `full`, recommended when no provider is near its limit, and `light`. `roles.py write --mode` saves it.
- When opening a coordinator. The brigade Open step asks for the mode with the reporting level. `brigade.py open --mode light|full` records it in `restaurant.json`. Leaving it out records nothing. The project and user files then decide.
- For one session. `$poteto-mode light` or "use light mode" in a request sets the session level. "full mode" sets it back.

### How it changes later

- `brigade.py set --mode light|full` changes a coordinator. `set --mode ""` returns it to the project and user files. The change applies to the next brief. A running attempt keeps the mode its brief names.
- `roles.py write --mode` changes the user or project file. New sessions read it. A running coordinator reads it at its next brief.
- When a provider in the `verifiers` or code roles hits its usage limit, the coordinator first relaunches or resumes the failed worker or verifier with `roles.py backup`, per [Failure handling](../t3/runtime.md#failure-handling). The mode stays as it was. The coordinator does not switch modes on its own. Whatever `backup` prints, it then parks the choice with `86 add`, defaulting to `light` until the limit resets. It switches only when the user answers or the menu `## Budget` says to. A usage limit is the user budget call.

### What the agent says

When light mode applies, the thread says so once, at the start, in two or three short sentences. One example is "Light mode is on, from this coordinator setting. This change gets one design sketch and no separate comment review. Tests, the build, and a review by another model family still run."

On an escalation it names the rule. One example is "This change runs in full mode because its lease covers `land.py`."

The worker report repeats the brief `Mode:` and `Waived by mode:` lines under its status. Steps waived by the mode are not deviations. The coordinator digest replies name the mode only when it changes.

## What light mode changes

Each row names one spawn. "Merged" means two passes become one. "Cheaper seat" means the same step on a lower reasoning level. No light fan-out wave has more than 3 children in flight. A wave with more slices batches them into 3 briefs and keeps every slice covered. The normative list lives in [Light behavior](../t3/runtime.md#light-behavior).

| Spawn | Full | Light | Kind |
| --- | --- | --- | --- |
| `how` simple | 1 explainer | 1 explainer | Kept |
| `how` complex | 2 to 4 explorers and 1 explainer | 1 explainer, simple path always | Cut |
| `why` investigators | 1 per category | Source control only | Cut |
| `why` synthesizer | 1 child | The parent writes the synthesis | Merged |
| `architect` grounding | A second `how` run | Reuses the playbook `how` output | Merged |
| `architect` runners | 1 per seat | 1 runner on the first `architect runners` seat, asked for two structurally distinct sketches | Merged |
| Cross-judge | 1 child | The parent picks. The gate review reads the chosen design | Cut |
| Arena | 1 per seat and a judge | The single code delegate. Eval keeps arena, because comparing candidates is its whole purpose | Cut |
| Code delegate | 1 | 1. Hillclimb runs one live hypothesis at a time | Kept |
| Comment Sicko | 1 child | The worker applies the no-comments rules to its diff. The gate review brief adds the comments check | Merged |
| Comment Sicko follow-ups | `how`, `why`, rerun, `architect` | None. A finding that needs them goes into the gate verdict as a send-back | Cut |
| `interrogate` | 1 per seat | Not run. A contested design escalates to `full`. Outside brigade, the gate review replaces the PR-opening child run | Cut |
| Fresh-child test | 1 per test, plus one on another provider for a skill that must work under several providers | See [Fresh-child tests in light mode](#fresh-child-tests-in-light-mode) | Cheaper seat |
| Description eval | 5 prompts, each sent to a fresh child | Only when the change edits a `description` | Cut |
| `swarm` | N workers | At most 3 workers | Cut |
| Trail reviewer | 1 child | Folded into the gate review when one runs at that head. Otherwise kept as the gate | Merged |
| `reflect` | 3 and 1 | A scheduled reflect does not run. A user explicit reflect runs in full | Cut |
| Recall slices | 1 per slice | At most 3 slices, and no `why` wave unless asked | Cut |
| Automate-me miners | 1 per slice | 1 miner over the whole window | Cut |
| Verification source wave | 1 per feature file | At most 3 children, each reading a batch of feature files | Cut |
| Multi-phase exploration | Several | 1 explorer | Cut |
| Multi-phase verification | Full lanes | Gates, one live lane per surface, and one audit lane on another family. No perf lane | Cut |
| Autopilot owner | 1 per PR | 1 per PR, with `Mode: light` in its brief | Kept |
| Autopilot verification | Full lanes | 2 lanes. Gates, which keeps the patch-id rule, and one audit lane on another family that is the gate review and runs at every new head SHA | Cut |
| Orchestrate sub-coordinator | 1 per track | 1 per track, with `Mode: light` in its brief | Kept |
| Orchestrate worker | Fewer, broader | Same, with `Mode: light` in each brief | Kept |
| Orchestrate long-lived owner | 1 per owner | Same, with `Mode: light` in each brief | Kept |
| Orchestrate verifier | When expensive | When expensive. A cheap unit merge still needs the gate review at its head | Kept |
| Shipping verifier | 1 per PR | 1 per PR. It is the gate review | Kept |
| Visual parity owner | 1 per component | 1 per component, at most 3 in flight, with `Mode: light` in each brief | Cut |
| Worktree cleanup summarizer | 1 per long thread | None. The parent reads the last page of each thread with `t3_thread_read` and `limit` | Cut |
| Autonomous run watcher | 1 | 1 | Kept |
| Coordinator thread | 1 | 1 | Kept |
| Executive admin thread | 1 | 1 | Kept |
| brigade worker | 1 per attempt | 1 per attempt, on the station seat with the light cap | Cheaper seat |
| Gate verifier | 1 per attempt | 1 per attempt, same seat, same reasoning level | Kept |
| Landing writer | 1 per unit | 1 per unit, with `Mode: light` in its brief | Kept |
| Landing queue | Checks | Checks | Kept |
| Setup smoke test | 1 per provider | 1 per provider | Kept |

A send-back in light mode is a fix attempt with a fresh worker. Its brief carries the findings and waives the steps [Waivers](#waivers) names.

### Never cut

- Tests and the build gate. The worker runs the project test command and build before the gate. The queue runs its configured checks.
- The landing queue checks. `land.py` spends no model usage. Light mode has nothing to gain there.
- One review by another model family at every head SHA. [The gate review](#the-gate-review) defines it for brigade and for standalone playbooks.
- Re-review of every fix attempt. A review reads the whole diff against its base on round one. A later round reads the fix diff since the last reviewed SHA, per [Never cut](../t3/runtime.md#never-cut).
- Owner fences. Leases, the `--owner` generation, worktrees, and the pass-at-head gate are code, and they cost nothing.
- A fresh worker for every send-back. See [The fresh-worker rule](#the-fresh-worker-rule).
- The code delegate. The persona and `Playbook:` line still apply, and `roles.py check-brief` still gates the brief.
- One executing fresh-child test per changed spawn behavior. See [Fresh-child tests in light mode](#fresh-child-tests-in-light-mode).

### The gate review

Light mode has one gate review per head SHA before merge. It is one read-only child on the first runnable `verifiers` seat whose model family differs from every model that wrote the diff. Authors include code delegates, workers that wrote code, and authors from earlier attempts on the branch. When no runnable seat qualifies, the verdict is `blocked`. The gate reads the whole diff against its base on round one, and a later round reads the fix diff since the last reviewed SHA, per [Never cut](../t3/runtime.md#never-cut). Its brief adds three checks that folded children used to run.

- The no-comments rules from `agents/comment-sicko.md`, applied to the diff.
- The chosen design, when the light architect step picked one of two sketches.
- The decision trail, when the run kept one per `show-me-your-work`. The gate reads the log and the run thread and keeps that skill checks. It flags weak evidence, skipped or unproven verification, misleading readiness, and a shell success that hides a failed check.

The gate verdict is `pass`, `send-back`, or `blocked`, with the head SHA, the author, and the verifier.

In brigade the gate is the existing verifier. The coordinator records it with `pass record`, and `dish --state queued` refuses a SHA without a pass. A brigade worker brief carries `Gate: brigade`, and the worker runs no gate of its own.

In a standalone playbook, Opening a PR runs the gate before it opens or marks the PR ready. Full mode has no such gate today. Opening a PR seats no `verifiers`. Light mode adds the gate because it removes the panels that gave full mode its diversity.

The standalone gate reuses a current qualifying pass instead of launching a duplicate. Reuse is by exact head SHA only.

- Current. The verdict is the latest one for that item at that SHA. A later `send-back` or `blocked` at the same SHA voids an earlier pass, as it does in `pass_check`, which `dish --state queued` already uses.
- Qualifying. The verdict is `pass`, and its author and verifier are of different families.

Two sources can show both today.

1. brigade `pass.tsv`. The parent runs `brigade.py pass check <item> --sha <head> --json`. It selects the row with the same code as `pass_check`, which `dish --state queued` already uses, and prints `verdict`, `author`, `verifier`, `note`, and `crossFamily`. `crossFamily` is false when `record_pass` family rule finds one family, or when the note starts with `same model family`. The parent reuses the pass only when the command exits 0 and prints `"crossFamily": true`. The parent never reads `pass.tsv` itself.
2. A verdict this run launched at that exact SHA. It must be the latest verdict this run holds for that SHA. A Shipping or Autopilot root own verdict at that SHA counts.

These do not qualify.

- An Orchestrate `ledger.tsv` row. Its verdicts are verification levels, not `pass`, and it records no author.
- A Shipping or Autopilot verdict from another run. It lives only in that root thread messages.
- A verdict at another head SHA, even when the two heads `git patch-id` match. A patch-id ignores whitespace, so a matching patch-id does not prove the same behavior.

Full mode keeps its own patch-id policy unchanged. Shipping, Autopilot-full, and Autopilot-stack keep a code verdict across a head whose patch-id matches. Light mode does not apply that policy to the gate review. Light mode keeps it for lane receipts. A Shipping or Autopilot root in light mode can still reuse a tests, build, or mergeability result wherever the Shipping playbook allows, and reruns the rest as that rule says. A lane receipt never stands in for the gate review. At a new head SHA the root launches a new gate review unless a current qualifying pass exists at that exact SHA.

This costs one gate review per rewritten head. An Autopilot-stack rebase rewrites every SHA above it, so a light stack pays one gate per rewritten PR where full mode keeps the verdict. Light mode accepts that cost, because it removed the panels and lanes that give full mode its other review coverage.

The parent records the verdict it used in the PR body `## Verification` section as one line, `Gate: pass at <short sha> by <provider/model>`. The SHA is the PR current head.

### Waivers

A waiver removes one named step for one attempt. `roles.py mode` prints the waivers from one table, `LIGHT_WAIVERS` in `t3/scripts/roles.py`, keyed by playbook and attempt kind. The attempt kinds are `first`, `fix` after a send-back, and `bounce` after a queue bounce. Step names are the playbook own words.

| Playbook | `first` | `fix` and `bounce` |
| --- | --- | --- |
| Feature | Arena, Interrogate, Comment Sicko | How, Architect, Arena, Interrogate, Comment Sicko |
| Bug fix | Comment Sicko | How, Why, Architect, Comment Sicko |
| Refactoring | Comment Sicko | How, Architect, Comment Sicko |
| Perf issue | Comment Sicko | How, Architect, Comment Sicko |
| Hillclimb | Comment Sicko | How, Comment Sicko |
| Authoring a skill | Comment Sicko, Second-provider test | Comment Sicko, Second-provider test |
| Every other playbook | none | none |

Interrogate is a waiver only in Feature, where `feature.md` step 7 runs it for a contested design. In light mode a contested design escalates instead. The other interrogate call, in a child that opens a PR, never runs inside brigade, whose worker brief forbids opening a PR. Outside brigade, light mode replaces it with the gate review.

The Bug fix fix-attempt row waives Bug fix step 2 `how` and `why` seeds. The review findings name the defect, so the worker confirms that mechanism with runtime evidence, which step 2 last sentence already requires, and goes to step 3. Step 1 still reproduces the finding.

A step that light mode reduces but does not remove is not a waiver. The single architect runner, the simple `how` path, and the source-control-only `why` are how those steps run in light mode, so a worker that runs them has skipped nothing.

### Brief lines

A brief carries these lines, each at most once.

```text
Playbook: playbooks/feature.md
Mode: light
Mode source: restaurant.json
Attempt: fix
Waived by mode: How, Architect, Arena, Interrogate, Comment Sicko
Gate: brigade
```

- `Mode:` holds only `full` or `light`. Nothing follows the value.
- `Mode source:` holds one of `brief`, `session`, `restaurant.json`, `.pstack/t3-roles.json`, `roles.json`, `default`, or `escalated: <reason>`. It names where the decision came from. Brigade reads an `escalated:` reason back to keep the escalation durable.
- `Attempt:` holds `first`, `fix`, or `bounce`.
- `Waived by mode:` lists the waivers, comma-separated, in table order.
- `Gate:` holds `brigade` in a brigade brief.

`roles.py mode` always prints `Mode:` and `Mode source:`. With `--playbook` and `--attempt`, it also prints `Playbook:` and `Attempt:`. It prints `Waived by mode:` only when that mode, playbook, and attempt have waivers. `brigade.py brief` writes the printed lines unchanged and then adds `Gate: brigade` and the seat rule.

### `roles.py check-brief`

`check-brief` validates the lines against the table, not against a global union.

- At most one of each line. `Mode:` is required in a code delegate brief.
- `Mode:` is `full` or `light`.
- Under `Mode: full`, no `Waived by mode:` line.
- Under `Mode: light`, `Attempt:` is required, and `Waived by mode:` must equal `LIGHT_WAIVERS[(playbook, attempt)]` exactly. The playbook comes from the `Playbook:` line. A first-attempt Feature brief with `Waived by mode: How` fails, because How runs on a first attempt. A first-attempt Feature brief without Comment Sicko in its waivers fails too, so a compliant light worker is never sent back for skipping it.

### Interaction with Deadlines

The runtime rule in [Deadlines](../t3/runtime.md#deadlines) says a timebox orders the work and never waives a step. Brigade sends back a skip cited to the timebox. Light mode keeps that rule whole. A waived step is not a skip, because the mode removes it before the attempt starts and the brief records it.

The worker runs every playbook step its `Waived by mode:` line does not name, under the Deadlines rule as written. It never adds a waiver of its own. At review, the coordinator compares the report skipped steps with that line. A skip the line names passes. Any other skip, including one cited to the timebox, is still a send-back. A mode change mid-attempt does not change the line. It applies to the next brief.

### The fresh-worker rule

A send-back launches a fresh worker with `t3_thread_launch` unless the item is held or owes its round decision. The rule exists so that a fix never inherits a stale context or drops a directive, and light mode does not weaken it. Light mode makes the fresh worker cheaper. Its brief carries `Attempt: fix`, the waivers from the table, the verifier findings file, and the old branch head as its base.

A queue bounce is not a send-back. Its fresh worker gets `Attempt: bounce` and the bounce reason. It does not count toward rule 2.

### Fresh-child tests in light mode

The runtime requires one executing fresh-child test per changed spawn or coordination behavior.

- A leaf test runs on the `skill tests` seat and forbids spawning. It is allowed only when the changed behavior happens without delegation.
- An executing test runs on the `skill tests` seat. It is required once for each changed spawn or coordination behavior. One test covers one behavior.
- The second-provider test is waived in light mode. The executing test runs on the `skill tests` seat, which defaults to Claude Haiku 5.5 at high, or at medium under the light mode reasoning cap. A test whose child launches seats resolves that seat with `roles.py show --role "skill tests" --launches-seats`. A seat that resolves to Cursor is refused with `cannot launch seats`.

## Decision

The gate review keeps its full level in light mode. A trial paired reviews at `medium` and at full level on 8 heads. The `medium` review missed a blocking finding at 3 of 8 heads. The worker-side cuts remain.
