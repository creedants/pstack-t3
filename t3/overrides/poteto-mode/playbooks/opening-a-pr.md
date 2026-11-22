### Opening a PR

Invoked at the end of every other playbook.

[The runtime's Modes section](../../pstack-runtime/SKILL.md#modes) sets the mode lines of every brief this playbook writes and how its spawns run in light mode.

**Worktree.** Work from a git worktree off trunk, and keep unrelated work out of it. Multiple writing `delegate_task` children on the same branch each get their own worktree per [the runtime's Isolation section](../../pstack-runtime/SKILL.md#isolation). Rebase into small, landable commits. Follow the host's commit and PR attribution instructions exactly, such as a co-author trailer or a generated-by footer. Add none they leave out.

**PRs.** Run the `deslop` skill (`/deslop`) over the diff before commit if it is installed. Otherwise strip slop from the diff yourself per the **unslop** skill before you commit. Run `/no-comments` before review.

**Descriptions.** Title: `type(scope): subject`, naming a real symbol when one carries the change. Body: a one-minute briefing under 40 lines with these `##` headings in order, dropping empty ones: `## Why`, `## What changed`, `## Scope`, `## Tradeoffs` (only rejected alternatives a reviewer would otherwise ask about), `## Blast Radius`, `## Verification`.

**Gate.** This step runs only in light mode. Full mode skips it and runs `/no-comments` as the **PRs.** paragraph says. In light mode, run the gate review in [the runtime's Modes section](../../pstack-runtime/SKILL.md#gate-review) after the diff is final and before you create the PR or mark it ready. Reuse only a current qualifying verdict at the exact head SHA, as that section says. For a brigade item, that includes a coordinator's pass that `brigade.py pass check <item> --sha <head> --json` reports, per [the runtime's Gate review](../../pstack-runtime/SKILL.md#gate-review). In light mode, the worker applies `agents/comment-sicko.md` to its own diff before the gate in place of the `/no-comments` child. A `send-back` or `blocked` verdict stops the PR until a fresh code delegate fixes the findings and a new gate passes at the new head. Write the passing verdict's `Gate:` line under `## Verification`. A worker whose brief carries `Gate: brigade` skips this paragraph.

**Forge.** GitHub CLI (`gh`) is the default. When Origin's CLI resolves the repository, use `origin pr` for its operations. Never require Graphite (`gt`). Stack follow-ups by base branch.

**Link.** After you create a PR, call `link_pull_request` with its full URL per [the runtime's Pull requests section](../../pstack-runtime/SKILL.md#pull-requests). Link every layer of a stack. Before you finish, call `list_thread_pull_requests` and link any PR still missing. Report a link failure instead of claiming the PR is linked.

**Babysit.** A new PR starts no babysit. Post its URL and keep building.

A child task that opens a PR runs `/deslop` if installed and `/no-comments`, and posts the URL. The parent calls `link_pull_request` with that URL so the PR shows on the parent thread. The child returns to the parent without babysitting, unless it is an Autopilot-full or Autopilot-stack owner. That owner's brief carries the babysit loop and is the ask `playbooks/babysit.md` waits for. The owner starts the loop after its code-ready report and reports merge-ready. In [light mode](../../pstack-runtime/SKILL.md#modes), that child runs the **Gate** paragraph above in place of `/no-comments`.
