# Light mode

This is the design for a light mode in pstack-t3, one setting, `full` or `light`, chosen at start and changeable later, that cuts the model usage of poteto-mode playbooks and brigade coordinators while keeping the checks that catch real bugs. The intended reader is the user who runs low on a provider's usage budget, and the engineer who builds the changes below.

In this document a coordinator is one standing brigade coordinator thread and its store directory, a work item is one unit of work handed to a worker, and an attempt is one worker run on a work item. brigade's commands and files keep their kitchen names only where they name a command or a file, such as `fire`, `dishes.tsv`, and `restaurant.json`.

## Summary

Light mode keeps every gate that decides whether code lands, and cuts the fan-out around it.

| Kept in light mode | Cut or cheapened in light mode |
| --- | --- |
| Unit tests and the build gate (`land.py` contract checks, CI) | The architect panel: one runner, no cross-judge, no second `how` |
| The landing queue's checks and its linear-history and lease checks | `how` explorers: the simple path only |
| One review by a different model family at every head SHA before merge | `why` investigators other than source control, and the `why` synthesizer |
| The owner fences: leases, `--owner` generations, worktrees, the pass-at-head gate | The Comment Sicko child, folded into the worker and the gate review |
| A fresh worker for every send-back | `How` and `Architect` in a fix attempt after a send-back |
| One fresh-child test for a change to a skill's behavior | Second reviewers, arena, interrogate, reflect, and extra fresh-child tests |
| The code delegate, so the author is never the diff's only reader | Reasoning level: light caps seats at `medium` unless the budget says otherwise |

On this repository's history, light mode would have cut the child activity of a typical Feature change from about 610 items to about 240, and of a typical Bug fix from about 530 to about 280. That is a 47 to 60 percent cut in the measured unit, before the reasoning cap. See [Saving per change](#saving-per-change) for the method and its limits.

Light mode escalates a work item to `full` when its lease covers a configured high-risk file, on its second send-back, and when the coordinator or the worker judges the design contested. On this repository the configured files are `land.py`, `brigade.py`, and `install.py`. The file rule alone would have run 18 of the 24 work items whose reviews found a real bug in full mode.

Changes 1 to 6 under [Changes](#changes) build it in landing order.

## Method

Four sources fed the census and the estimates.

1. The skill sources under `t3/`, read for every `delegate_task`, `t3_thread_launch`, and routed skill call. Line numbers below are in those sources, not in the generated `skills/`.
2. The pstack-t3 coordinator store at `~/.local/state/pstack-t3/brigade/pstack-t3/pstack-t3`: `dishes.tsv` (74 work items), `pass.tsv` (129 verdicts), `log.tsv`, every file under `reports/`, and `review-briefs/trial-notes.txt`. A script computed attempts, review rounds, send-backs, and durations per work item. A second script mapped each merged item's PR to its squash commit on `main` and listed the files it changed.
3. T3 thread activity for 10 work items (D1, D8, D24, D50, D51, D64, D65, D70, D71, D72) and the coordinator thread, read with `t3_thread_list` and `t3_thread_read`. Each child thread's `itemCount` (messages, reasoning blocks, tool calls, and checkpoints on its timeline) is the size unit.
4. Every send-back review report, each classified as a real bug (wrong behavior, crash, lost record, race, broken invariant), an instruction or doc gap, or a style nit.

T3 records no token counts and no reasoning level on a thread. Activity items are the closest measured size, and they undercount a long reasoning block. Every cost figure in this document is in activity items or child counts, and every ratio is an estimate in that unit.

## Usage census

### What a work item costs today

The store holds 69 merged work items. Each attempt is one worker thread and exactly one gating verdict, so attempts equal review rounds on every merged item.

| Measure | Median | Mean | Max |
| --- | --- | --- | --- |
| Attempts per merged item | 2 | 1.87 | 6 (D24) |
| Send-backs per merged item | 1 | 0.86 | 5 (D24) |
| Worker run, in progress to in review | 12.8 min | 17.8 min | 30.7 min at p90 |
| Review, in review to verdict | 5.2 min | 6.6 min | 11.2 min at p90 |
| Work item, first start to merged | 41.9 min | 56.8 min | 118.5 min at p90 |

25 items passed their first review, 33 needed two attempts, 8 needed three, 2 needed four, and D24 needed six. 59 of 129 verdicts were send-backs.

In the 10 sampled work items, worker-side children produced 2290 activity items over 82 threads, and coordinator-side children produced 2082 over 29 threads. D64 alone holds 1005 of the coordinator-side items, from second reviewers and usage-limit replacements. The coordinator thread itself has 519 runs and 5076 items since 2026-10-05. That is a standing cost and not a per-item cost.

### Spawn points

Each row is one place a skill spawns a child or a thread. "Items" is the mean `itemCount` of that child in the sampled threads, or `n/s` when the sample holds none. Seats are the live `~/.config/pstack-t3/roles.json` on this machine, which seats five `architect runners`, five `arena runners`, five `interrogate reviewers`, and seven `verifiers`. The built-in defaults seat two in each panel.

| # | Spawn | Source | Children per run | When | Items |
| --- | --- | --- | --- | --- | --- |
| 1 | `how`, simple path | `t3/overrides/how/SKILL.md:17` | 1 explainer, `how explainer` (Opus) | Default for a narrow question | 19 |
| 2 | `how`, complex path | `t3/overrides/how/SKILL.md:18`, `:22` | 2 to 4 `how explorer` (Grok), then 1 explainer | A multi-file or cross-cutting question | n/s |
| 3 | `why` investigators | `t3/overrides/why/SKILL.md:99`, `:103` | 1 per evidence category with a source. Source control always | Bug fix step 2, architect when ownership changes, investigations of motivation | 110 to 200 per run |
| 4 | `why` synthesizer | `t3/overrides/why/SKILL.md:128` | 1 (Opus) | After the investigators | 14 |
| 5 | `architect` grounding | `t3/overrides/architect/SKILL.md:23` | a second `how` run (row 1 or 2) | Every non-greenfield architect run, after the playbook's own `how` | 19 |
| 6 | `architect` runners | `t3/overrides/architect/SKILL.md:35`, `:45` | 1 per `architect runners` seat. 5 live, 2 built in | Feature step 2. Bug fix, Refactoring, Perf when the change crosses a function boundary | 164 per 5-seat panel |
| 7 | `arena` cross-judge | `t3/overrides/arena/SKILL.md:50` | 1 from `arena cross-judge pool` | After every arena and architect panel | 27 |
| 8 | `arena` candidates | `t3/overrides/arena/SKILL.md:42` | 1 per `arena runners` seat | Feature step 4 when several shapes are valid, Eval | n/s |
| 9 | Code delegate | `t3/overrides/poteto-mode/playbooks/feature.md:12`, `bug-fix.md:9` | 1 on the playbook's code role, or `hardest tasks` | Every code-writing playbook. No skip | 53 |
| 10 | Comment Sicko | `t3/overrides/no-comments/SKILL.md:19` via `opening-a-pr.md:9` | 1 on `judgment and prose` (Opus) | Before review, at the end of every playbook that opens a change | 8 |
| 11 | `interrogate` | `t3/overrides/interrogate/SKILL.md:46` | 1 per `interrogate reviewers` seat (2 built in) | A contested design (Feature step 7), a child that opens a PR (`opening-a-pr.md:38`) | n/s |
| 12 | Fresh-child skill test | `t3/added/pstack-author-skill/SKILL.md:53`, `:55`, `:56` | 1 per test, a new child per retry, 1 more on another provider | Every change to a skill's behavior (house rule 7) | 10 as a leaf. 70 to 111 when the test runs a playbook |
| 13 | Description eval | `t3/added/pstack-author-skill/SKILL.md:63` | 5, plus a rerun per failure | When triggers are uncertain | n/s |
| 14 | `swarm` workers | `t3/overrides/swarm/SKILL.md:24` | N from the user or the shape | Explicit swarm, Autopilot swarm-verify | n/s |
| 15 | Autopilot swarm-verify | `t3/overrides/poteto-mode/playbooks/autopilot-full.md:8` | at least 5 lanes per code-ready head | Every Autopilot round | n/s |
| 16 | Multi-phase plan lanes | `t3/overrides/poteto-mode/playbooks/multi-phase-plan.md:13` | at least 14 per code-ready head, when the plan runs | Executing a multi-phase plan | n/s |
| 17 | Shipping verifier | `t3/overrides/poteto-mode/playbooks/shipping.md:7` | 1 per PR on one `verifiers` seat | Shipping a green stack | n/s |
| 18 | Orchestrate verifier | `t3/overrides/poteto-mode/playbooks/orchestrate.md:17` | 1, only for expensive or high-blast-radius checks | Orchestrate units | n/s |
| 19 | `reflect` | `t3/overrides/reflect/SKILL.md:25`, `:39` | 3 reviewers, then 1 synthesizer | The user says reflect, and brigade's weekly service | n/s |
| 20 | brigade worker | `t3/added/brigade/SKILL.md:135` | 1 top-level thread per attempt | Every work item, again after every send-back and bounce | 29 to 354 per worker |
| 21 | brigade gate verifier | `t3/added/brigade/SKILL.md:138` | 1 from `verifiers`, another family than the author's | Every attempt, at its head SHA | 38 |
| 22 | brigade second reviewer | not in the skill text | 1 trial model | 19 times, on D59 to D70 during the reviewer trial | 110 |
| 23 | brigade send-back | `t3/added/brigade/SKILL.md:142` | rows 20 and 21 again, with the playbook rerun | Every send-back, with no round cap | as rows 20 and 21 |
| 24 | brigade fix-the-recipe | `t3/added/brigade/SKILL.md:149` | 1 work item on the `correct` skill | Two items repeat one mistake | as one work item |
| 25 | Landing queue | `t3/added/landing/scripts/land.py` (`Integration.check`, `land`) | 0. Subprocess checks and `gh` reads only | Every submitted entry | 0 |

Rows 1, 5, 6, 7, 9, and 10 are a Feature attempt. Rows 1, 3, 4, 9, and 10 are a Bug fix attempt, plus rows 5 to 7 when the fix crosses a function boundary. Row 12 joins either one when the change edits a skill's behavior. `pass.tsv` records only rows 20 and 21. Every other row is visible only as a child thread.

### Where the items went

The sampled first attempts of Feature work show the shape.

| Work item | `how` | Architect panel and judge | Code delegate | Comment Sicko | Skill tests | Total |
| --- | --- | --- | --- | --- | --- | --- |
| D1 | 29 | 227 | 51 | 0 | 0 | 307 |
| D50 | 20 | 176 | 53 | 0 | 0 | 249 |
| D70 | 26 | 260 | 77 | 8 | 46 | 417 |

The architect panel and its judge took 62 to 74 percent of the worker-side items on every sampled Feature attempt. The code delegate took 17 to 21 percent. Comment Sicko is the cheapest child in the census.

Bug fix attempts put the weight on `why`. D71's first attempt spent 111 of 187 items on two `why` investigators. D72's retry spent 202 of 343 on three, and repeated a 5-seat architect panel for a one-paragraph change.

Send-backs repeat the whole tree. A fresh worker reruns the playbook from step 1, so a fix attempt costs about what a first attempt costs. D72's retry (343 items) cost more than its first attempt (216). Two sampled fix attempts, D24's sixth and D65's second, spawned no children at all. The playbook's fan-out is already uneven in practice.

## The setting

### Where it lives

The setting is one key, `"mode"`, with the values `"full"` and `"light"`, in the files that already hold `"budget"`. A missing key reads as `full`.

| Level | File | Written by |
| --- | --- | --- |
| User | `~/.config/pstack-t3/roles.json` | `setup-pstack`, `roles.py write --mode` |
| Project | `<repo>/.pstack/t3-roles.json` | `setup-pstack --project`, `roles.py write --project --mode` |
| Coordinator | `<store>/restaurant.json` | `brigade.py open --mode`, `brigade.py set --mode` |
| Session | the user's words: `$poteto-mode light`, "light mode", "full mode" | the user, for that thread only |
| Child | a `Mode: light` or `Mode: full` line in its brief | the parent that wrote the brief |

The highest level present wins: child brief, then session, then coordinator, then project, then user. A child never resolves the mode again. It obeys its brief's `Mode:` line, so a coordinator's choice reaches every worker and every code delegate under it.

`roles.py` owns the resolution, as it owns roles. `roles.py show` prints `"mode"` and `"modeSource"` next to `"budget"`. A new `roles.py mode --cwd <dir> [--coordinator-mode <m>] [--paths <paths>] [--attempt <n>] [--send-backs <n>]` prints the effective mode, its source, the escalation that applied if any, and the steps that mode waives for that attempt. brigade.py calls it. No skill rebuilds the table.

Light mode also caps reasoning. When the mode is `light` and the budget is `default`, seats resolve as if the budget were `medium`. An explicit budget wins in both directions, so `light` with `large` keeps xhigh seats, and `full` with `small` keeps the `medium` cap it already has. The cap is the only option light mode touches. It never sets `fastMode`, and a seat that falls back to the built-in Grok default keeps whatever that default sets today. This replaces the prose budget line in brigade's `menu.md` (`t3/added/brigade/scripts/brigade.py:84`), which no script reads today. The live pstack-t3 menu says `medium` while the roles file says `default`.

### Escalation

Light mode returns a work item to `full` for that attempt and every later one when any of these holds.

1. **A high-risk path.** The project file's `"escalate"` key lists paths. The item's lease covers one of them, meaning a lease path equals it or is a directory that contains it. For pstack-t3 the list is `t3/added/landing/scripts/land.py`, `t3/added/brigade/scripts/brigade.py`, and `scripts/install.py`. The default list for every project is `**/migrations/**` and `**/*lock*`.
2. **A second send-back.** The third attempt runs `full`.
3. **A contested design.** The coordinator passes `fire --mode full` when the tickets name a lock, a lease, a race, a migration, or history rewriting, or when the worker reports the design as contested. A worker that finds its design contested runs `interrogate` in `full` and says so in its report.

Escalation only moves toward `full`. Nothing moves an item from `full` to `light` mid-flight. `roles.py mode` prints the reason, as in `full (escalated: lease covers t3/added/landing/scripts/land.py)`.

On this repository's 69 merged items, rule 1 escalates 34, which carried 35 of the 59 send-backs and 18 of the 24 items whose reviews found a real bug. Rule 2 adds D16, D23, D25, D49, and D63 at their third attempt. Rule 1 is coarse because brigade leases whole directories, such as `t3/added/brigade`, so a skill-text change to `t3/added/brigade/SKILL.md` escalates too. [Change 6](#change-6-narrower-leases-for-skill-text) narrows that.

### How the user picks it

- **At setup.** `setup-pstack` asks for the mode right after the budget, with the host's question tool, and names the current value. The options are `full` (recommended when no provider is near its limit) and `light`. `roles.py write --mode` saves it.
- **When opening a coordinator.** brigade's Open step asks for the mode with the reporting level, and `brigade.py open --mode light|full` records it in `restaurant.json`. Leaving it out records nothing, so the project and user files decide.
- **For one session.** `$poteto-mode light` or "use light mode" in a request sets the session level. "full mode" sets it back.

### How it changes later

- `brigade.py set --mode light|full` changes a coordinator, and `set --mode ""` returns it to the project and user files. The change applies to the next brief. A running attempt keeps the mode its brief names, because a mode is a decision recorded at the start of an attempt.
- `roles.py write --mode` changes the user or project file. New sessions read it. A running coordinator reads it at its next service, when it runs `roles.py show`.
- When a provider in the `verifiers` or code roles hits its usage limit, the coordinator does not switch modes on its own. It parks the choice with `86 add`, defaulting to `light` until the limit resets, and switches only when the user answers or the menu's `## Budget` says to. A usage limit is the user's budget call, and Codex hit its limit three times on 2026-10-07 (D64, D70, D71 in `pass.tsv`) without anyone changing a setting.

### What the agent says

When light mode applies, the thread says so once, at the start, in a few sentences that name the source and the biggest cuts, such as "Light mode is on, from this coordinator's setting. This change gets one design sketch instead of a five-model panel, no separate comment review, and reasoning capped at medium. Tests, the build, and a review by another model family still run." On an escalation it says which rule fired: "This change runs in full mode because its lease covers `land.py`."

The worker report gains one `Mode:` line under its status, naming the mode, its source, and the steps the brief waived. Steps waived by the mode are not deviations. The coordinator's digest replies name the mode only when it changes.

## What light mode changes

Each row maps to the census row it changes. "Merged" means two passes become one. "Cheaper seat" means the same step on a lower reasoning level or a smaller model.

| Census row | Full | Light | Kind |
| --- | --- | --- | --- |
| 1 `how` simple | 1 explainer | 1 explainer | Kept |
| 2 `how` complex | 2 to 4 explorers and 1 explainer | 1 explainer, simple path always | Cut |
| 3 `why` investigators | 1 per category | Source control only | Cut |
| 4 `why` synthesizer | 1 child | The parent writes the synthesis | Merged |
| 5 `architect` grounding | A second `how` run | Reuses the playbook's `how` output | Merged |
| 6 `architect` runners | 1 per seat | 1 runner on the first `architect runners` seat, asked for two structurally distinct sketches | Merged |
| 7 Cross-judge | 1 child | The parent picks. The gate review reads the chosen design | Cut |
| 8 `arena` | 1 per seat and a judge | The single code delegate | Cut |
| 9 Code delegate | 1 | 1 | Kept |
| 10 Comment Sicko | 1 child | The worker applies the no-comments rules to its diff, and the gate verifier's brief adds a comments check | Merged |
| 11 `interrogate` | 1 per seat | Not run. A contested design escalates to `full` | Cut |
| 12 Fresh-child test | 1 per test, plus one on another provider | 1 on the `skill tests` seat. No second-provider child. The test is a leaf: its brief forbids spawning | Cheaper seat |
| 13 Description eval | 5 children | Only when the change edits a `description` | Cut |
| 14 `swarm` | N workers | At most 3 workers | Cut |
| 15 Autopilot swarm-verify | 5 or more lanes | 2 lanes: gates, and one audit lane on a seat of another family | Cut |
| 16 Multi-phase lanes | 14 or more | Gates, one live lane per surface, and one audit lane | Cut |
| 17 Shipping verifier | 1 per PR | 1 per PR | Kept |
| 18 Orchestrate verifier | When expensive | When expensive | Kept |
| 19 `reflect` | 3 and 1 | brigade's weekly reflect is skipped. A user's explicit reflect runs in full | Cut |
| 20 brigade worker | 1 per attempt | 1 per attempt, reasoning capped at `medium` | Cheaper seat |
| 21 Gate verifier | 1 per attempt | 1 per attempt, same seat, reasoning capped at `medium`. An escalated item keeps its full level | Cheaper seat |
| 22 Second reviewer | Trials | Never | Cut |
| 23 Send-back | Full playbook rerun | A fix attempt: fresh worker, brief with the findings, code delegate, tests, fresh-child test if a skill changed. `How` and `Architect` are waived by the mode | Cut |
| 24 Fix the recipe | A `correct` work item | Filed as a ticket and run when the mode returns to `full` | Cut |
| 25 Landing queue | Checks | Checks | Kept |

### Never cut

- **Tests and the build gate.** The worker runs `python3 -m unittest discover -s tests` and `python3 scripts/build.py` through `land.py slot --`. The queue reruns them.
- **The landing queue's checks.** `land.py` spends no model usage. Light mode has nothing to gain there.
- **One review by another model family at every head SHA.** `brigade.py dish --state queued` already refuses a SHA with no `pass` (`brigade.py:2121`), and `record_pass` refuses a same-family verifier without `--same-family` (`brigade.py:801`). Light mode keeps the verifier's model and family and changes only its reasoning level. It never moves the gate to an unproven model, because the trial models (Space Bunny, Muse Spark, Nemotron, Kimi) passed both D59 and D60, which Codex sent back for a mode-overwrite race and three lease failures. When the verifier's provider hits its usage limit, the next seat of another family takes the review, as today.
- **Re-review of every fix attempt over its whole diff.** Three later rounds caught a real bug that round 1 missed in the same diff (D42, D55, D61), so a fix attempt's review reads the full diff against trunk, not only the delta.
- **Owner fences.** Leases, the `--owner` generation, worktrees, and the pass-at-head gate are code, and they cost nothing.
- **A fresh worker for every send-back.** See the next section.
- **The code delegate.** The persona and `Playbook:` line still apply, and `roles.py check-brief` still gates the brief.

### Interaction with Deadlines

The runtime's Deadlines rule (`t3/runtime.md:33`) says a timebox orders the work and never waives a step, and brigade sends back a skip cited to the timebox (`t3/added/brigade/SKILL.md:138`). Light mode keeps that rule whole. A step light mode cuts is not a skip, because the mode removes it before the attempt starts and the brief records it.

`brigade.py brief` writes two lines from `roles.py mode`:

```text
Mode: light (from restaurant.json)
Waived by mode: How, Architect (fix attempt after a send-back)
```

The worker runs every playbook step that line does not name, under the Deadlines rule as written. It never adds a waiver of its own. At review, the coordinator compares the report's skipped steps with that line. A skip the line names passes. Any other skip, including one cited to the timebox, is still a send-back. A mode change mid-attempt does not change the line. It applies to the next brief.

### Interaction with `roles.py check-brief`

`check-brief` gains one rule. A brief may hold at most one `Mode:` line, its value is `full` or `light`, and a `Waived by mode:` line is allowed only under `Mode: light` and may name only steps from the light table that `roles.py mode` prints. A parent in light mode writes `Mode: light` into each code delegate's brief, so the delegate spawns nothing light mode cuts. The persona and `Playbook:` rules are unchanged.

### Interaction with the fresh-worker rule

Every send-back still launches a fresh worker with `t3_thread_launch` (`t3/added/brigade/SKILL.md:142`). The rule exists so that a fix never inherits a stale context or drops a directive, and light mode does not weaken it. Light mode makes the fresh worker cheaper. Its brief waives `How` and `Architect`, carries the verifier's findings file, and names the old branch head as its base. The design already passed one review, and the findings say what to fix. The sampled history supports the cut. D24's sixth attempt and D65's second spawned no children and passed, while D72's retry reran a five-seat panel for a one-paragraph change and cost more than its first attempt.

A queue bounce is not a send-back. Its fresh worker gets the same fix-attempt brief with the bounce reason, because a rebase or conflict fix needs no new design.

## Saving per change

The estimate uses the mean activity items per child from the sampled threads: `how` explainer 19, a 5-seat architect panel 164, a single Grok architect runner 54, cross-judge 27, code delegate 53, Comment Sicko 8, leaf skill test 10, gate verifier 38, two `why` investigators 150, `why` synthesizer 14. A typical change has 1.87 attempts, so 0.87 fix attempts.

| Change | Full first attempt | Full fix attempt | Light first attempt | Light fix attempt | Full per change | Light per change |
| --- | --- | --- | --- | --- | --- | --- |
| Feature | 19 + 19 + 164 + 27 + 53 + 8 = 290 | 290 | 19 + 54 + 53 = 126 | 53 | 290 + 0.87 × 290 + 1.87 × 38 = 613 | 126 + 0.87 × 53 + 1.87 × 38 = 243 |
| Bug fix | 19 + 150 + 14 + 53 + 8 = 244 | 244 | 19 + 90 + 53 = 162 | 53 | 244 + 0.87 × 244 + 71 = 527 | 162 + 46 + 71 = 279 |
| Skill change, add | + 10 per attempt | | + 10 per attempt | | + 19 | + 19 |

That is about 60 percent fewer child items for a Feature change and about 47 percent fewer for a Bug fix, With the built-in two-seat architect panel (Opus 29 and Grok 54 items), a full Feature change is about 460 items, and both kinds save about 47 percent. The worker thread's own items fall too, because it waits on fewer children, and the `medium` reasoning cap shortens every seat's reasoning. Neither is in these numbers, since T3 records no tokens. Escalated items save only the reasoning cap. On this repository about half the items escalate, so the measured saving across the whole history is about half of the per-change figure until [Change 6](#change-6-narrower-leases-for-skill-text) narrows the leases.

The estimate has three limits. The sample is 10 work items. Activity items are not tokens, and an Opus reasoning block counts as one item however long it runs. Light mode may raise the send-back rate, because fewer design candidates reach the code delegate, and a 20 percent rise in fix attempts would take back about 16 items per change, one fix attempt and one review for every sixth change.

## Risks

Light mode keeps the round-1 review, and round 1 is where this repository caught most of its real bugs. Of 59 send-backs, 35 rounds held 51 real-bug findings on 24 items, and 24 rounds held instruction or doc gaps. Each of those was found by the one gate verifier light mode keeps. The risks are in what light mode thins around that review.

1. **A weaker verifier.** The reviewer trial shows that a cheaper model misses concurrency bugs. Every trial model passed D59 and D60, which held a mode-overwrite race and three lease failures. Kimi as a second reviewer passed D62 and missed Codex's three crash-safety findings. Light mode therefore keeps the verifier's model and lowers only its reasoning level, and an escalated item keeps the full level. A `medium` cap on the gate is still untested. [Change 5](#change-5-a-measured-light-mode-trial) measures it before light becomes a recommendation.
2. **Later rounds that caught round-1 misses.** D42 round 2 found a duplicate-PR path (`reports/D42-review-2.md`), D55 round 2 found an option-shaped summary that breaks the printed retry (`reports/D55-review-2.md`), and D61 round 2 found an unskipped test that fails setup (`reports/D61-review-2.md`). All three are on `land.py` or `brigade.py`, so rule 1 escalates them. Light mode also keeps full-diff re-review.
3. **Fixes that introduce bugs.** D12 rounds 2 and 3 (tag publishing, a second push URL) and D24 rounds 2 to 5 (installer ownership) were bugs the fix itself created. Re-review at every head SHA caught them, and light mode keeps it. D24 is the costliest item in the store, and rule 2 would have run its third to sixth attempts in full.
4. **Second reviewers.** Space Bunny found an empty-path reservation that locks the repository on D61 (`reports/D61-review-space-bunny.md`) and three skill gaps on D64 that Grok missed. Light mode drops second reviewers, so that class of catch is lost. D61 escalates by rule 1. D64 escalates by its lease on `t3/added/brigade`. The same reviewer stalled or quit on 2 of 6 runs, so it was never a dependable gate.
5. **Skill-text gaps.** 24 send-back rounds were instruction gaps, such as D11's merge wait that hangs and D64's relay lines that `sync` cannot print. The cut Comment Sicko and the single architect runner touch this class most. The fresh-child test stays, and it is the check that shows an agent following the text.
6. **The six real-bug items rule 1 misses.** D3 (`measure_capacity.py`), D14 and D25 (`roles.py`), D49 (a concurrency design doc), D50 and D63 (test and shell snippets). Each was caught by its round-1 review, which light mode keeps. A project that wants them in full adds `t3/scripts/roles.py` to `"escalate"`, and the coordinator's rule-3 judgment covers a design doc about concurrency.

## Changes

Each change is one PR through the landing queue, with its own tests and its own changelog fragment. Every change that edits a skill's behavior needs a fresh-child test per the `pstack-author-skill` skill.

### Change 1. The mode setting in `roles.py`

- **What.** `check_shape` accepts `"mode"` (`full` or `light`) and `"escalate"` (a list of path strings). `merged_config` merges `mode` like `budget`, project over user, and keeps the project's `escalate` list. `show` prints `mode` and `modeSource`. `write --mode` saves it. Under `light` with budget `default`, `resolve` applies `medium`.
- **`roles.py mode`.** It prints `<mode> (<source>)` on the first line, `escalated: <reason>` when a rule fired, and one `waives: <step>` line per waived step. `--coordinator-mode`, `--paths`, `--attempt`, and `--send-backs` feed the precedence and the three escalation rules. The light table is one constant, `LIGHT_WAIVERS`, keyed by first attempt and fix attempt.
- **Files.** `t3/scripts/roles.py`, `tests/test_roles_cli.py`, generated `skills/pstack-runtime/`, its fragment.
- **Tests.** A user file with `"mode": "light"` and a project file with `"mode": "full"` prints `full (.pstack/t3-roles.json)`. `--coordinator-mode light` over a project `full` prints `light (restaurant.json)`. `--paths t3/added/landing` with `escalate` naming `t3/added/landing/scripts/land.py` prints `full` and the literal escalation line. `--send-backs 2` prints `full`. Under `light` and budget `default`, `show --role "verifiers"` caps a configured xhigh seat at `medium`, and under `light` and `large` it stays at xhigh. An unknown mode value exits 1 naming the file.

### Change 2. `Mode:` lines in briefs and `check-brief`

- **What.** `check-brief` refuses more than one `Mode:` line, a value other than `full` or `light`, a `Waived by mode:` line under `full`, and a waived step outside `LIGHT_WAIVERS`.
- **Files.** `t3/scripts/roles.py`, `tests/test_roles_cli.py`, generated `skills/pstack-runtime/`, its fragment.
- **Tests.** A brief with the persona, a `Playbook:` line, and `Mode: light` passes. Two `Mode:` lines fail with `more than one Mode line: keep one`. `Mode: cheap` fails and names the two values. `Waived by mode: How` under `Mode: full` fails. `Waived by mode: Tests` under `Mode: light` fails and names the allowed steps.

### Change 3. The runtime's Modes section and the playbook rules

- **What.** `t3/runtime.md` gains a `## Modes` section with the precedence, the light table from [What light mode changes](#what-light-mode-changes), the never-cut list, and the announcement sentence. `t3/overrides/poteto-mode/SKILL.md` reads `$poteto-mode light` and links that section. The `how`, `why`, `architect`, `arena`, `no-comments`, `swarm`, `interrogate`, and `pstack-author-skill` overrides each gain one sentence that points at the section for their light behavior. The Deadlines paragraph gains one sentence: a step the brief's `Waived by mode:` line names is not a skip.
- **Files.** `t3/runtime.md`, those overrides, `t3/added/pstack-author-skill/SKILL.md`, `t3/overrides.lock.json` only after review, generated `skills/`, `docs/skills.md`, its fragment.
- **Tests.** `scripts/check.py` fails when a skill restates the light table instead of linking it. A fresh-child test runs a Feature request with `Mode: light` in its brief and checks that it spawns one `how` explainer, one architect runner, and one code delegate, and no Comment Sicko child. A second fresh-child test runs the same request with no mode line and checks the full panel still runs.

### Change 4. brigade's mode

- **What.** `brigade.py open --mode` and `set --mode` record `mode` in `restaurant.json`, and `set --mode ""` removes it. `fire --mode full` records a per-item escalation in `dishes.tsv`. `brief` calls `roles.py mode` with the coordinator mode, the lease paths, the attempt number, and the send-back count, and writes its `Mode:` and `Waived by mode:` lines. `status` and `walk` print the mode. The skill's Open step asks for the mode, the Review step checks skips against the brief's waived line, the send-back step writes the fix-attempt brief, light mode drops the weekly reflect, and a usage limit parks the mode choice with `86 add`. The menu template's `## Budget` line drops the roles budget and keeps the worker cap.
- **Files.** `t3/added/brigade/scripts/brigade.py`, `t3/added/brigade/SKILL.md`, `t3/setup.md`, `tests/test_brigade.py`, generated `skills/brigade/` and `skills/setup-pstack/`, its fragment.
- **Tests.** `open --mode light` writes `"mode": "light"`. `brief` for a first attempt under light prints `Mode: light (from restaurant.json)` and no `How` waiver. `brief` after one send-back prints `Waived by mode: How, Architect`. `brief` after two send-backs prints `Mode: full` with the escalation line. `brief` for an item whose lease covers an `escalate` path prints `Mode: full`. `set --mode light` while an item is in progress leaves that item's written brief unchanged. A fresh-child test drives a coordinator through one light work item and checks the brief lines and the review step's skip check.

### Change 5. A measured light-mode trial

- **What.** Run the next six work items of one coordinator in light mode and six comparable ones in full, then compare child activity items per item, send-backs per item, and real-bug findings per round, using the scripts in [Method](#method). Record whether the `medium` gate verifier missed a finding that a full-level rerun on the same SHA catches. Run that rerun on each light item's final SHA.
- **Files.** `docs/light-mode.md` gains a Results section. No code.
- **Decision.** Light mode becomes the recommended choice under a usage limit only if the rerun finds no blocking finding the light gate missed. Otherwise the gate keeps its full level in light mode, and only the worker-side cuts remain.

### Change 6. Narrower leases for skill text

- **What.** brigade's Fire step leases files, not their directory, when a change edits only skill text, such as `t3/added/brigade/SKILL.md` instead of `t3/added/brigade`. Rule 1 then escalates only items that can touch the listed scripts.
- **Files.** `t3/added/brigade/SKILL.md`, `tests/test_brigade.py`, generated `skills/brigade/`, its fragment.
- **Tests.** The escalation script from [Method](#method) rerun over the store reports how many items escalate. A fresh-child test checks that a skill-text ticket fires with a file lease.

## Out of scope

- Changing which model family gates review. The trial evidence says the current gate is the strongest available, and light mode does not move it.
- An automatic switch on a usage limit. T3 reports a limit only as a failed child. The coordinator parks the choice for the user instead.
- A mode per playbook step. One switch with one table is easier to reason about than a matrix, and escalation covers the cases that need more.
- Coordinator wake frequency. At `digest` the liveness schedule already runs every 30 minutes, and the coordinator's own runs are a standing cost this design does not measure per item.
