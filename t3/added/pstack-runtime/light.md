# Light behavior

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
