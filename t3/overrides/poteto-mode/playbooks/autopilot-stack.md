### Autopilot-stack

[The runtime's Modes section](../../pstack-runtime/SKILL.md#modes) sets the mode lines of every brief this playbook writes and how its spawns run in light mode.

Run the Autopilot-full playbook, but do not merge.

1. **In place of step 5, stop at verified.** Stop when each PR is verified and green at its current head, then hand the PRs to the operator. After the handoff, nobody pushes without the operator's go. Link each PR with `link_pull_request`. Each owner watches its PR with `watch_pull_request` per `playbooks/babysit.md`. Every tick prompt names this file, so each tick sees the no-merge rule.
2. **Stack a PR only when it builds on another PR.** This is the one exception to step 3. Base the PR on the other PR's branch in place of trunk, per **Size and stacks** in `playbooks/opening-a-pr.md`, with its base set to its parent's branch. In place of step 2's rebase onto trunk, each PR rebases onto its parent PR's branch, and the stack root rebases onto trunk. A new head on the lower PR restacks the PRs above it, and a PR whose `git patch-id` the restack leaves unchanged keeps its verdict. Apply Autopilot-full step 5's test link by link, since a link's base is its parent.

**Reply:** list each PR with its verdict, in place of what merged.
