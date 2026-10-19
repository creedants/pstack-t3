Part of [the setting](../light-mode.md).

## What light mode changes

Each row names one spawn. "Merged" means two passes become one. "Cheaper seat" means the same step on a lower reasoning level. No light fan-out wave has more than 3 children in flight. A wave with more slices batches them into 3 briefs and keeps every slice covered. The normative list lives in [Light behavior](../../t3/runtime.md#light-behavior).

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
| Fresh-child test | 1 per test, plus one on another provider for a skill that must work under several providers | See [Fresh-child tests in light mode](rules.md#fresh-child-tests-in-light-mode) | Cheaper seat |
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

A send-back in light mode is a fix attempt with a fresh worker. Its brief carries the findings and waives the steps [Waivers](rules.md#waivers) names.


### Never cut

- Tests and the build gate. The worker runs the project test command and build before the gate. The queue runs its configured checks.
- The landing queue checks. `land.py` spends no model usage. Light mode has nothing to gain there.
- One review by another model family at every head SHA. [The gate review](#the-gate-review) defines it for brigade and for standalone playbooks.
- Re-review of every fix attempt. A review reads the whole diff against its base on round one. A later round reads the fix diff since the last reviewed SHA, per [Never cut](../../t3/runtime.md#never-cut).
- Owner fences. Leases, the `--owner` generation, worktrees, and the pass-at-head gate are code, and they cost nothing.
- A fresh worker for every send-back. See [The fresh-worker rule](rules.md#the-fresh-worker-rule).
- The code delegate. The persona and `Playbook:` line still apply, and `roles.py check-brief` still gates the brief.
- One executing fresh-child test per changed spawn behavior. See [Fresh-child tests in light mode](rules.md#fresh-child-tests-in-light-mode).


See [Never cut](../../t3/runtime.md#never-cut) for the full rule.

### The gate review

Light mode has one gate review per head SHA before merge. It is one read-only child on the first runnable `verifiers` seat whose model family differs from every model that wrote the diff. Authors include code delegates, workers that wrote code, and authors from earlier attempts on the branch. When no runnable seat qualifies, the verdict is `blocked`. The gate reads the whole diff against its base on round one, and a later round reads the fix diff since the last reviewed SHA, per [Never cut](../../t3/runtime.md#never-cut). Its brief adds three checks that folded children used to run.

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


See [Gate review](../../t3/runtime.md#gate-review) for the full gate rule.
