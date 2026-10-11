### Opening a PR

Invoked at the end of every other playbook.

[The runtime's Modes section](../../pstack-runtime/SKILL.md#modes) sets the mode lines of every brief this playbook writes and how its spawns run in light mode.

**Worktree.** Work from a git worktree off main. Child tasks start in it. Multiple writing `delegate_task` children on the same branch each get their own worktree per [the runtime's Isolation section](../../pstack-runtime/SKILL.md#isolation), or `git fetch && git reset --hard origin/<branch>` between them. Dirty branch with unrelated work: patch out, fresh worktree, apply. Snarled worktree: reset from main, redo minimally.

**Commits.** Commit liberally. Rebase into small, ordered commits before opening PRs. Each commit is a future PR: landable, ordered to tell the story. Amend when the fix belongs in a just-made commit. New commit when separable. Follow the host's commit and PR attribution instructions exactly, such as a co-author trailer or a generated-by footer. Add none they leave out. Commit bodies don't restate the subject.

**PRs.** Run the `deslop` skill (`/deslop`) over the diff before commit if it is installed. Otherwise strip slop from the diff yourself per the **unslop** skill and the Comments section. Run `/no-comments` before review. Write every PR title, description, and commit body with `/technical-writing` (every layer except Diátaxis, and apply the STE vocabulary rules: one word per action, keep articles, a plain verb over `-ing` where one works), then apply `/unslop`.

**Titles.** Conventional Commits `type(scope): subject`, no trailing period. Type such as `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, or `perf`. Scope is the area, such as `pstack` or `poteto-mode`. Subject is short, imperative, and names a real symbol when one carries the change, for example `fix(pstack): retarget opening-a-pr babysit trigger`.

**Descriptions.** The PR body is a one-minute briefing, not a lab notebook, for a reviewer who has the diff. It says why the change exists, what it leaves out, what it could break, and how you proved it works. Write short, simple sentences with few identifiers, no walls of text. The body becomes the squash commit body, so keep that commit under about 40 lines. Use `##` headings, not bold lead-ins, in this order, and drop a section with nothing to say:

- `## Why`: the problem and approach, 1–3 short sentences. No SHAs, rebase genealogy, or "based on main" preamble.
- `## What changed`: 1–3 short bullets. Name a real symbol or path only when it carries the change. Name both sides of a rename or retarget.
- `## Scope`: always. 1–3 short items on what it covers and what it deliberately leaves out (a follow-up, a known gap). No symbols, paths, or file-by-file essay.
- `## Tradeoffs`: only rejected alternatives a reviewer would otherwise ask about. Skip when there was no real choice.
- `## Blast Radius`: 1–2 sentences on who or what it touches and why that is safe or risky. If main is red, state the cost of leaving it red.
- `## Verification`: 1–3 bullets, each a real run path and its outcome. Perf: one primary number with its unit, `before → after`, and a link to the arena or swarm directory for the rest. No sample-size methodology, swarm recitals, or metric tables.

After the sections, attach videos or screenshots when they prove a claim. Full SHAs, swarm or arena lane recitals, lever-correction essays, file-by-file checklists, and "CLEAN" verdicts go in a linked artifact, not the body.

**Gate.** This step runs only in light mode. Full mode skips it and runs `/no-comments` as the **PRs.** paragraph says. In light mode, run the gate review in [the runtime's Modes section](../../pstack-runtime/SKILL.md#gate-review) after the diff is final and before you create the PR or mark it ready. Reuse only a current qualifying verdict at the exact head SHA, as that section says. For a brigade item, that includes a coordinator's pass that `brigade.py pass check <item> --sha <head> --json` reports, per [the runtime's Gate review](../../pstack-runtime/SKILL.md#gate-review). In light mode, the worker applies `agents/comment-sicko.md` to its own diff before the gate in place of the `/no-comments` child. A `send-back` or `blocked` verdict stops the PR until a fresh code delegate fixes the findings and a new gate passes at the new head. Write the passing verdict's `Gate:` line under `## Verification`. A worker whose brief carries `Gate: brigade` skips this paragraph.

**Forge.** Resolve the forge before the first PR operation and keep that choice for create, edit, view, watch, and merge. GitHub CLI (`gh`) is the default. Origin is an optional merge CLI. When an `origin` CLI is available (`command -v origin` succeeds) and Origin can resolve the repository, prefer `origin pr ...`. If Origin is absent or cannot resolve the repository, stay on `gh` and record the fallback. Do not require Graphite (`gt`).

**Built-in PR tool.** When the run provides a built-in PR tool, create, edit, retarget, and mark ready through it, never through a forge CLI. A CLI-made PR misses what the tool tracks. Use the resolved forge for everything the tool does not cover, and for every PR operation when the run has no such tool.

**Size and stacks.** Prefer five narrow PRs to one large PR. A stack is a base-branch chain. The root PR targets trunk. Each child branch rebases onto its parent's exact tip and its PR targets the parent branch. Without a built-in PR tool, per the resolved forge, create a child with `origin pr create --status open --base <parent-branch>` or `gh pr create --base <parent-branch>`, and retarget one with `origin pr edit <pr> --base <parent-branch>` or `gh pr edit <pr> --base <parent-branch>`. Branch from trunk only for independent work. Rebase on trunk before substantial stack work.

**Readiness.** Open every PR ready, never as a draft. A built-in PR tool can default to draft, so set `draft: false` on every creation call through it. With Origin, pass `--status open`. With `gh`, omit `--draft`. If a PR still opens as a draft, mark it ready through the PR tool, or run `origin pr ready <number>` or `gh pr ready <number>` according to the resolved forge. Run `origin pr view <number>` or `gh pr view <number>` before you refer to PR status.

**Link.** After you create a PR, call `link_pull_request` with its full URL per [the runtime's Pull requests section](../../pstack-runtime/SKILL.md#pull-requests). Link every layer of a stack. Before you finish, call `list_thread_pull_requests` and link any PR still missing. Report a link failure instead of claiming the PR is linked.

**Babysit.** Opening a PR does not start a babysit. Post the URL and keep building. Finish the phase or stack first. Run a separate babysit pass only when the user asks for one after the whole stack exists. A babysit for each new PR stalls the build and spends checks on commits later waves restart. Push back when feedback drifts from intent.

A child task that opens a PR runs `interrogate`, `/deslop` if installed, and `/no-comments`, and posts the URL. The parent calls `link_pull_request` with that URL so the PR shows on the parent thread. It returns to the parent without babysitting, unless it is an Autopilot-full or Autopilot-stack owner. That owner's brief assigns the babysit loop and is the ask `playbooks/babysit.md` waits for. The owner starts the loop after its code-ready report, reports merge-ready or STACK-READY per its playbook, and is exempt from the rules here and in `playbooks/babysit.md` that hold babysitting until a whole stack is built. In [light mode](../../pstack-runtime/SKILL.md#modes), that child runs the **Gate** paragraph above in place of `interrogate` and `/no-comments`.
