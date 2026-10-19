Part of [the guide](../guide.md).

### When a provider hits its usage limit

A usage limit relaunches the failed seat on a `backups` seat when one is free. The lead reads the failed child's error text and runs `roles.py backup` with it. The command prints `relaunch`, `panel`, `park`, or `not-usage-limit`. On `relaunch` the lead starts a fresh child on the printed seat. On `panel` the lead seats the printed `review backups` panel. On `park` the lead launches nothing, and that item waits for the reset.

| Failed seat | Backup |
| --- | --- |
| A worker, any role that is not a reviewer | The role's first usable `backups` seat |
| A reviewer role, such as `verifiers`, `interrogate reviewers`, or `arena cross-judge pool` | The role's first usable `backups` seat whose model family did not write the diff |
| A `review backups` seat | None. A `review backups` seat has no backup, so it counts as no pass |
| A `verifiers` seat with no usable `backups` seat, when `review backups` is set | The `review backups` panel when it keeps at least two usable seats, otherwise the item parks |

The backup comes only from the `backups` key in your roles file. Set a `backups` list per role to name the seats `roles.py backup` tries in order. A candidate is skipped when its provider is the failed provider, is already out, is not runnable or not in the catalog, the `exclude` list refuses it, or, for a review role, its model family wrote the diff. A usable seat is one this sentence does not skip. A seat parks when no candidate is left. A `review backups` seat has no backup. After the reset time in the error, `roles.py backup --resume` returns the original seat, whatever its provider, once that provider is back. Otherwise it returns the first usable `backups` seat. After a backup also hits a limit, pass each provider still out as `--out`. The command can choose another usable `backups` seat. A standing coordinator relaunches first. Whatever `backup` prints, it then asks whether to run new work in [light mode](../light-mode.md) until the limit resets, and it changes the mode only on your answer or when the menu's budget says to. Every `roles.py` command and flag is in the [command-line reference](../cli/roles.md).

## What you can say

| You type | What happens |
| --- | --- |
| `$poteto-mode the export button sometimes downloads an empty file. repro first, then fix and verify.` | Bug fix playbook. It reproduces, finds the root cause, fixes, and proves the reproduction now passes. |
| `$poteto-mode add rate limiting to the public API.` | Feature playbook. It names the data shape, settles the design, builds in verifiable steps, and verifies. |
| `$interrogate review this branch.` | Reviewers on different model families attack the diff. You get one verdict. |
| `$arena write the caching layer.` | Several candidates on different models. It picks a base and grafts in the best ideas. |
| `$swarm check every API route for missing auth.` | One worker per slice, with a single report of PASS, ISSUES, or BLOCKED. |
| `$how does session refresh work?` | An explorer agent maps the code, then an explainer agent turns it into a walkthrough. |
| `$why did we pick Postgres here?` | A cited answer from git history and whatever doc and issue tools are connected. |
| `$poteto-mode babysit PR 482 until it's green.` | It calls `watch_pull_request` and waits on CI, reviews, and conflicts. It fixes what it can and reports. |
| `$poteto-mode i'm going to bed. land the stack. everything merged by morning.` | An autonomous run with a decision log. It waits on each pull request with `watch_pull_request`, uses `schedule_task` as the merge heartbeat, and verifies each pull request before merge. |
| `$recall where did I leave off on the billing migration?` | A current-state brief rebuilt from your past T3 threads, git, and PRs. |
| `$correct` | A census of the mistakes agents repeat here, each fixed at the highest level that holds, from architecture through types, lint, and tests, plus a rule table. |
| `$brigade open a standing coordinator for bridgekit focused on startup performance.` | A pinned thread that owns that goal. It groups incoming requests, hands each to a pstack playbook, has a reviewer, from another model family when one can run, check every result against the goal, and reports what landed. |
| `$landing set up this repo so several agents can land work at once.` | A landing contract with your test commands as checks. Every coordinator then claims leases before delegating and lands through one queue. |
| `$poteto-help which skill should I use to review this branch?` | It points at the skill or playbook and hands you a prompt. It does not start the work. |

## Earlier examples

The next two runs are against a small repository with a planted bug that its own tests passed. Replies are excerpted and trimmed. They predate the move to roles resolved against the live catalog.

**A Codex thread ran a four-family review.**

```
$interrogate review the last commit, "Fix median for even-length input".
```

> **Reviewers.** Resolved `interrogate reviewers` with `roles.py`. Launched all four through `delegate_task` with identical read-only briefs.
> - A: Codex / gpt-6.1-sol
> - B: Claude / claude-opus-5-5
> - C: Cursor / Auto
> - D: Grok / grok-4.7
>
> **Act on.**
> 1. Incorrect even-length median (A, B, C, D). `stats.py:5` uses `// 2`, flooring the average. `[1, 2]` returns `1` instead of `1.5`.
> 2. The regression test asserts the bug (A, B, C, D). It expects `2` for `[4, 1, 3, 2]`. The correct median is `2.5`.
>
> I verified that all three existing tests pass despite these errors. **The commit should not pass review.**
>
> **Dismissed.** Grok's claim that `-3` is a full unit below `-2.5` is incorrect. The difference is `0.5`. Its flooring finding remains valid.

**A Grok thread ran a coverage swarm.**

```
$swarm audit stats.py: one read-only worker per public function.
```

> Coverage swarm: one read-only worker for `median` and one for `mean`. `swarm workers` resolved to `inherit`, so both run on grok / grok-4.7.
>
> **median: ISSUES.** I reran its commands and they match.
> - `median([])` raises `IndexError`.
> - Even counts return the floor: `median([1, 2])` is `1`, `median([-5, -2])` is `-4`.
> - An infinite middle value becomes `nan`: `median([1.0, inf])`.

## FAQ

<details>
<summary><b>Do I need every provider?</b></summary>

No. Everything works with one provider. No panel has a built-in model. A seat whose model you cannot run falls back, and the report names each replacement. `verifiers` is whatever panel you seat in your roles file, so signing in to more providers lets you spread it across model families. Add a provider or another model in a roles file.
</details>

<details>
<summary><b>How is this different from pstack?</b></summary>

The playbooks, principles, rubrics, and their wording are the same. The plumbing is different. Upstream uses Cursor's subagents, cloud agents, `/loop`, and a fixed list of Cursor model names. pstack-t3 uses T3's `delegate_task`, worktree threads, `schedule_task`, and roles resolved against T3's live model list. pstack-t3 also adds `$brigade`, `$landing`, and `pstack-author-skill`, which upstream does not have. The full mapping is under [What changed from upstream](#what-changed-from-upstream).
</details>

<details>
<summary><b>Does it cost more?</b></summary>

`$interrogate` costs one review per seat in your `interrogate reviewers` role, so a two-seat panel costs about two reviews. A roles file can add seats. Single-agent playbooks cost about the same as doing the work by hand, plus verification. Use the `small` budget in `$setup-pstack` for routine work, or [light mode](../light-mode.md) to cut the fan-out around the checks that catch real bugs. Run skill tests and the read-only explorers in `$how` and `$why` on a cheap, high-volume model to keep their cost down.
</details>

<details>
<summary><b>Will it overwrite my existing skills?</b></summary>

No. If a skill with the same name exists, the installer lists the taken paths, links nothing, and exits. `--replace` moves each one aside and records it instead, and `python3 scripts/install.py uninstall` restores it when it can do so without overwriting anything. See [Install paths](../guide.md#install-paths).
</details>

<details>
<summary><b>Why do child agents stall sometimes?</b></summary>

Children inherit the lead thread's runtime mode. In approval-required mode, a child can wait on an approval prompt that no tool can answer. Run fan-out work in a mode that allows commands and edits. See [Tips and pitfalls](../guide.md#tips-and-pitfalls).
</details>

<details>
<summary><b>Does it work outside T3 Code?</b></summary>

It is built for T3. Outside T3, the skills fall back to the host's own subagents on the current model, without cross-provider panels or scheduling. For Cursor itself, use [upstream pstack](https://github.com/cursor/plugins/tree/main/pstack).
</details>

<details>
<summary><b>How do I update?</b></summary>

`git pull` in the checkout, then `python3 scripts/install.py`. The installer is safe to rerun.
</details>

<details>
<summary><b>Is this official?</b></summary>

No. It is an independent project, not affiliated with or endorsed by Lauren Tan, Cursor, T3 Tools, Anthropic, OpenAI, or xAI.
</details>

## What changed from upstream

| Upstream (Cursor) | pstack-t3 (T3) |
| --- | --- |
| `Task` subagents with `subagent_type` and `model` | `delegate_task` children with a `role` and a resolved `target` |
| Cloud agents | Local child tasks, or `t3_thread_launch` threads bound to their own worktree |
| A Cursor rule file of model names | `roles.json` resolved against T3's live catalog. No role has a built-in model, an `exclude` list refuses a model, and a usage limit relaunches on a `backups` seat when one is free and parks otherwise. |
| A fixed default panel of four Cursor models | Each panel role is the seats you set in `roles.json`, resolved against T3's live catalog. Unset, a panel role inherits this thread's seat, except `review backups`, which stays unset. |
| `/loop`, automations, hourly ticks | `schedule_task` for a cadence with no pull request event. A wait on checks, reviews, or conflicts is `watch_pull_request`. A wait whose predicate is the merge also keeps the `schedule_task` heartbeat the runtime's Pull request watching section requires. |
| Cursor transcripts and cloud-agent URLs | T3 threads |
| `control-ui` from `cursor-team-kit` | T3 preview and device tools |
| Cursor's built-in `create-skill` | `pstack-author-skill`, for every provider |
| A worktree audit that reads Cursor's chat storage on macOS | Git inventory on Linux and macOS, plus T3 thread bindings |
| | `link_pull_request` on every PR a playbook opens or drives |

## Why these are skills

**Why skills, not an MCP server or a plugin?** T3 already gives every provider its orchestration server, so pstack-t3 needs no server of its own. T3's `$` picker lists each provider's native skills, so `$poteto-mode` appears whichever model you pick. Muse is the exception. T3's picker does not list Muse's skills, though Muse still loads them. A Claude Code plugin would namespace the skills and hide them from that picker, and the other providers have no plugin format.

## Roadmap

- More live end-to-end runs of the longest playbooks on real multi-PR projects. Autopilot has run once, on a four-PR scratch queue. Orchestrate has not run yet. See [Live runs](../live-runs.md) for what merged and the defects each run found.
- Testing on T3's other providers (OpenCode, Antigravity, ACP agents).
- A T3-native port of Lauren's long-form guide.
- Tracking upstream pstack releases from the repository, so a new upstream commit opens porting work without a person watching. `scripts/sync_upstream.py --check` reports what upstream changed and what pstack-t3 does with each changed path. Nothing runs it on a schedule or files the porting work yet, and syncing is a manual `scripts/sync_upstream.py` run.
- Cloud workers through local cua container sandboxes. Today landing still only needs a branch, a path lease, and a reviewed commit.
- Batch bisection in the landing queue. Today a failed batch tries one entry alone, then batches the rest again. `--batch` of [`land.py init`](../cli/land.md#landpy-init) applies to `push` and `local` only. `merge` and `human` open one PR per change.
- A cap on running workers across repositories. Today [`brigade.py set --workers`](../cli/brigade.md#brigadepy-set) caps one coordinator's units in progress or in review, and `fire` refuses past it. [`land.py cap`](../cli/land.md#landpy-cap) caps changes in flight on one repository, and [`land.py slot`](../cli/land.md#landpy-slot) limits heavy commands on the machine. Nothing counts workers across repositories.

Ideas and bug reports are welcome in [issues](https://github.com/creedants/pstack-t3/issues).
