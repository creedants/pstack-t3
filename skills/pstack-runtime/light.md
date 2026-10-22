# Light mode reference

Read this file before every spawn and run the row for that spawn.

| Spawn | Light behavior |
| --- | --- |
| `how` | Take the simple path for every question. Spawn one `how explainer`. Spawn no `how explorer`. |
| `why` | Spawn the source-control investigator only. Spawn no synthesizer. The parent writes the synthesis under `why`'s evidence and confidence rules. |
| `architect` grounding | Reuse the `how` output the calling playbook already holds. Run `how` once only when there is none. |
| `architect` runners | Spawn one runner on the first `architect runners` seat and ask it for two structurally distinct sketches. Screen both against `references/design-red-flags.md`. The parent picks one. |
| Arena cross-judge | Spawn no judge. The parent picks. The [gate review](SKILL.md#gate-review) reads the chosen design. |
| Arena candidates | Hand code to the single code delegate. Eval keeps arena and its judge, because comparing candidates is its whole purpose. |
| Code delegate | Keep it, with the persona, the `roles.py mode` lines, and `roles.py check-brief`. Hillclimb runs one live hypothesis at a time. |
| Comment Sicko | Spawn no Comment Sicko child. The worker applies `agents/comment-sicko.md` to its own diff. The [gate review](SKILL.md#gate-review) checks the same rules. |
| Comment Sicko follow-ups | Spawn no follow-up `how`, `why`, rerun, or `architect`. A finding that needs one goes into the gate verdict as a send-back. The contract in [Gate review](SKILL.md#gate-review) step 2 tells the reviewer the same. |
| `interrogate` | No playbook runs it. A contested design escalates per [Resolve and carry the mode](SKILL.md#resolve-and-carry-the-mode). A child that opens a PR runs the [gate review](SKILL.md#gate-review) in its place. |
| Fresh-child skill tests | Run one executing test per changed spawn or coordination behavior, on the `skill tests` seat. A leaf test may forbid spawning only when the behavior needs no delegation. Skip the second-provider test. |
| Description eval | Run it only when the change edits a `description`. |
| `swarm` | Spawn at most 3 workers. A respawn replaces a worker and does not raise the count. |
| Trail reviewer | When a gate review runs at the hand-back head SHA, it reads the trail and no trail reviewer runs. A code delegate never launches one, because its parent's gate reads the trail. With no gate at that head, the trail reviewer runs and is the gate, under [Gate review](SKILL.md#gate-review) steps 1 to 4. |
| `reflect` | A scheduled reflect, such as a coordinator's weekly service, does not run. A user's explicit reflect runs in full. Its `roles.py show` calls pass `--session-mode full`, so the light `medium` cap does not apply. |
| `recall` | Spawn at most 3 slice children. Run no `why` wave unless the user asks for one. |
| `automate-me` | Spawn one miner over the whole history window. Its brief lists every `threadId` that `t3_thread_list` returns in the window, across every page. The parent filters and samples none, and tells the miner to read each one. |
| Verification source wave | Spawn at most 3 source children, each reading a batch of feature files. |
| Multi-phase exploration | Spawn one read-only explorer. |
| Multi-phase verification | Write the plan's lane checklist as the playbook and `check-plan.mjs` require. At each code-ready head, launch the gates lane, one live lane per surface, and one audit lane on another model family. That audit lane is the gate review and runs [Gate review](SKILL.md#gate-review) steps 1 to 4. Each live lane's brief carries the boot recipe and every **Verify, live** lane box that drives its surface, and tells the lane to run and report each box. Launch no perf lane. The perf boxes stay in the plan and are not a merge gate. |
| Autopilot owner | One owner per PR. Each owner `message`, including a replacement's, carries the mode lines and the seat rule from [Resolve and carry the mode](SKILL.md#resolve-and-carry-the-mode). |
| Autopilot verification | Two lane children per round. The gates lane reruns the gates at that SHA and keeps the patch-id rule in `playbooks/shipping.md`. One audit lane on another model family is the gate review. It runs [Gate review](SKILL.md#gate-review) steps 1 to 4 at every new head SHA. |
| Orchestrate sub-coordinator, worker, and long-lived owner | Keep them. Each brief or `message` carries the mode lines and the seat rule from [Resolve and carry the mode](SKILL.md#resolve-and-carry-the-mode). |
| Orchestrate verifier | Keep it when verification is expensive. A cheap unit's merge still needs the gate review at its head, per [Gate review](SKILL.md#gate-review) steps 1 to 4. |
| Shipping verifier | One per PR. It is the gate review. It runs [Gate review](SKILL.md#gate-review) steps 1 to 4 with Shipping's live test as its own tasks, and it reuses a verdict only per [Gate review](SKILL.md#gate-review). |
| Visual parity owner | One owner per component, at most 3 in flight. Each `message` carries the mode lines and the seat rule from [Resolve and carry the mode](SKILL.md#resolve-and-carry-the-mode). |
| Worktree cleanup summarizer | Spawn none. The parent reads the last page of each long thread with `t3_thread_read` and `limit`. |
| Landing writer | One writer per unit. Its brief or `message` carries the mode lines and the seat rule from [Resolve and carry the mode](SKILL.md#resolve-and-carry-the-mode). |
| Autonomous run watcher | Keep it. |
| Setup smoke test | Keep one per provider. |

## Light behavior

In light mode, run the row for the spawn you are about to make. A step its row does not change runs as the skill says. A smaller fan-out in a row is how that step runs, not a waiver. Waived steps come only from the brief's `Waived by mode:` line, which `roles.py mode` prints from `LIGHT_WAIVERS`. Never add a waiver of your own. No light fan-out wave has more than 3 children in flight. A wave with more slices batches them into 3 briefs and keeps every slice covered.

A send-back in light mode is a fix attempt with a fresh worker. Its brief carries the findings, the old head, and the `Attempt: fix` lines `roles.py mode` prints.

## Gate review

Light mode removes the panels that give full mode its model diversity. The gate review puts one read by another model family back before merge. Full mode keeps each playbook's own review steps.

Every child that acts as the gate review runs steps 1 to 4. That includes the audit lane, the Shipping verifier, a cheap Orchestrate unit's gate, and a trail reviewer that runs with no gate at its head. Its step 2 brief adds that child's own verification tasks below the contract. Launch no second child for the same gate.

1. **Seat.** Resolve `verifiers` per [Roles](SKILL.md#roles) with this work's mode flag. Take the first seat whose model family differs from the author's. The author is the code delegate's `providerInstanceId/model`, or this thread's when it wrote the code. When several models wrote the diff, the verifier's family differs from all of them. When the catalog holds no runnable seat of another family, take the first runnable `verifiers` seat whose model differs from the author's. When `verifiers` has no such seat, take a fresh child on any runnable seat of the author's family. Mark that verifier `same-family` in the verdict. A `same-family` verdict is not qualifying for reuse per [Reusing a verdict](gate-review.md#reusing-a-verdict). Seat resolution returns `blocked` when the catalog holds a runnable seat of another family and `verifiers` has no runnable seat from it, and when the catalog holds no runnable seat at all.
2. **Brief.** A read-only brief. On round one, give the base ref and the full head SHA, and ask for a read of `git diff <base>...<head>`, the whole diff. On round two and later, give the previous reviewed SHA and the previous round's review file, ask for a read of `git diff <previous reviewed sha>...<head>`, and add a `FOCUS` section that carries the round rules from [Rounds after the first](../brigade/verdict-protocol.md#rounds-after-the-first). Paste the body of `agents/comment-sicko.md` unchanged and ask for its findings on the diff. Right after that body, paste the gate contract below unchanged. Add the child's own verification tasks after the contract. Name the chosen design's path when the light architect step picked one of two sketches. Name the decision log path and the run's `threadId` when the run kept a trail per `show-me-your-work`, and ask the reviewer to run that skill's audit checks itself with `t3_thread_read`. Flag weak evidence, skipped or unproven verification, misleading readiness, and a shell success that hides a failed check. When step 1 fell back to a same-family seat, the brief also asks the reviewer to write `same-family` beside the verifier in its final line.

   ```text
   Gate review contract. These rules override the persona above and the tasks below where they differ.
   Do not edit files, commit, or push. Do not post on the PR. Return your report to the parent.
   Launch no child task, thread, or subagent. Do not run the how, why, architect, or interrogate skill. Do not launch show-me-your-work's trail reviewer.
   Read the diff the brief names and the nearby code yourself. You may run git log -L and git blame. Run the named verification commands yourself.
   When a claim or finding needs investigation beyond those reads, return send-back. Name the file, the line, the claim, and the question the fix must answer.
   Report each comment the persona would delete as a send-back finding with its path and line. Make no edit.
   End with pass, send-back, or blocked, the full head SHA, the author, and the verifier.
   ```
3. **Verdict.** `pass`, `send-back`, or `blocked`, with the full head SHA, the author, and the verifier. Add `same-family` beside the verifier when step 1 fell back. Record it in this run's work log. The parent posts the verdict where the child's playbook posts a report.
4. **After.** A `send-back` or `blocked` stops the PR. A fresh code delegate fixes the findings, and a new gate runs at the new head SHA. On `pass`, write `Gate: pass at <short sha> by <provider/model>` under the PR body's `## Verification`. When step 1 fell back, add ` (same-family)` after the seat in that line. The SHA is the PR's current head. Keep one `Gate:` line.

A worker whose brief carries `Gate: brigade` runs no gate of its own, because the coordinator's verifier is its gate.

Launch a new gate unless a current qualifying verdict exists at the exact head SHA.

- **Current.** It is the latest verdict for that SHA. A later `send-back` or `blocked` at the same SHA voids an earlier `pass`.
- **Qualifying.** It is `pass`, and the author and verifier are of different model families.

Before you reuse a gate verdict, read [Reusing a verdict](gate-review.md#reusing-a-verdict). That file holds the two sources and the four exclusions and states the patch-id split. For a brigade item, the coordinator source runs `pass check <item> --sha <head> --json`.

A lane receipt never stands in for the gate review. Read the head again right before you create the PR, mark it ready, or merge. When it moved, apply this section again.

## Announcement

The rule lives in [Announcement](SKILL.md#announcement).
