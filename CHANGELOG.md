# Changelog

## Unreleased

- Opening a `$brigade` restaurant asks one question: who lands work. `merge` is new: the queue opens a PR per change as its record and merges it itself, through GitHub auto-merge when the repository has required checks, so the user does no PR or git work. `human` keeps the user merging, `push` (formerly `auto`, still accepted) pushes trunk with no PRs, and `local` lands on a lane ref. `land.py mode` switches between `merge`, `human`, and `push` once the queue is empty. The head chef owns all git and PR housekeeping: PR text, links, branch and worktree cleanup, and fast-forwarding the user's checkout when it is clean.
- `$brigade` runs each worker as a worktree thread you can see and steer, and keeps reviewers as subagents. A liveness check every 10 minutes (`brigade.py watch`) finds finished work whose run never closed and work over its timebox, because worker threads send no completion notice. `brigade.py fire --paths` claims the landing lease before anything is recorded, and `brigade.py brief` renders the worker brief from the store and refuses a missing field. A decision is raised once, then only in reports, and a head chef deletes only branches it created. `land.py slot --exclusive` holds every slot so benchmarks run on a quiet machine, and `land.py submit --title --body-file` sets the PR text in human mode. Found in the Bridgekit pilot: 3 of 8 workers hung open after reporting, and the baseline was measured under load.
- `$landing`, a skill new to pstack-t3, lets many agents write to one repository at once. `land.py` keeps a per-repository contract (trunk, mode, checks), path leases that refuse overlapping work, and a queue that is the only writer to trunk. The queue pins each reviewed SHA, rebases it onto current trunk, refuses a change whose rebased diff differs from the reviewed one, runs the checks in a clean worktree, and pushes only if trunk is still at the tested base. State is SQLite, so a crash mid-landing is recovered from git. Modes: `human` opens PRs, `auto` pushes trunk, `local` lands on a ref no checkout uses. `$brigade` now lands through it, and its per-restaurant merge policy moved to the repository contract. See [the plan](docs/landing-plan.md).
- `$brigade`, a skill new to pstack-t3. It gives a project or focus area a pinned head chef thread that holds a purpose, groups incoming requests, delegates them to poteto-mode playbooks, gates every merge on a review by another model family at the exact head SHA, and reports only what changed since the last report. `brigade.py` keeps each restaurant's state under `~/.local/state/pstack-t3/brigade/`. See [the plan](docs/brigade-plan.md).
- Synced to upstream pstack 0.15.7 ([cursor/plugins@9511e60](https://github.com/cursor/plugins/commit/9511e60321f7e533a187d62854a3d53a53752874)). Adds `$correct`.
- Synced to upstream pstack 0.15.9 ([cursor/plugins@e43c7ee](https://github.com/cursor/plugins/commit/e43c7ee26e00)). `$architect` assumes an agent edits the code next and screens designs for split ownership, two ways to do one task, importable internals, and hand-synced lists. The Perf issue playbook replaces its eight strategy families with seven ordered performance mantras, and Hillclimb orders perf hypotheses by them.
- A README covering what pstack-t3 does, real run excerpts, a table of example prompts, a diagram, and an FAQ.
- A [guide](docs/guide.md) to the first hour, a [how-it-works](docs/how-it-works.md) page, and a generated [skills catalog](docs/skills.md) that the build keeps in sync.
- A banner and a social preview image in `docs/assets/`.
- The README and guide state the requirement: a T3 Code nightly with Orchestrator V2 (`0.0.46-nightly.20261003.2610` or later).
- A security policy, a pull request template, and Dependabot updates for GitHub Actions.

## 0.1.0

First public release, ported from upstream pstack 0.15.6 ([cursor/plugins@23e4138](https://github.com/cursor/plugins/tree/23e4138daa01c42d4969f7a5465f82704e64f798/pstack)).

- Every pstack skill and playbook runs on T3 Code's orchestrator V2 tools: `delegate_task`, `task_status`, `t3_thread_launch`, `schedule_task`, `t3_thread_read`, the preview tools, and `link_pull_request`.
- `pstack-runtime` holds the whole Cursor-to-T3 mapping in one skill.
- `roles.py` resolves each role to providers and models from T3's live catalog, with reasoning budgets and reported fallbacks. Default panels take one seat per model family you can run.
- `setup-pstack` writes `~/.config/pstack-t3/roles.json` from that catalog and smoke-tests each seat.
- `install.py` links the skills into Claude, Codex, Grok, and Cursor. It refuses conflicts by default, `--replace` keeps restorable backups, and `doctor` reports shadowed or stale copies.
- `pstack-author-skill` replaces Cursor's built-in skill authoring for every provider.
- The build fails on Cursor-only leftovers, broken links, invalid frontmatter, and overrides whose upstream file changed.
- Tested live in T3: `$interrogate` led by Codex with Claude, Cursor, Grok, and Codex reviewers, and `$swarm` led by Grok.
