<p align="center">
  <img src="docs/assets/banner.svg" alt="pstack-t3: careful engineering from every model you have, and a standing coordinator for each project" width="100%">
</p>

<p align="center">
  <a href="https://github.com/creedants/pstack-t3/actions/workflows/ci.yml"><img src="https://github.com/creedants/pstack-t3/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="license: MIT"></a>
  <a href="upstream.json"><img src="https://img.shields.io/badge/upstream%20pstack-0.15.25-555.svg" alt="upstream pstack 0.15.25"></a>
  <a href="https://github.com/creedants/pstack-t3/releases"><img src="https://img.shields.io/github/v/release/creedants/pstack-t3" alt="release"></a>
</p>

<p align="center">
  <a href="docs/guide.md">Guide</a> ·
  <a href="docs/skills.md">All skills</a> ·
  <a href="docs/cli/README.md">Command-line reference</a> ·
  <a href="docs/how-it-works.md">How it works</a> ·
  <a href="docs/live-runs.md">Live runs</a>
</p>

**pstack-t3 makes any model in T3 Code do careful engineering, and gives each project a standing coordinator that turns your requests into reviewed, landed changes.**

<p align="center">
  <img src="docs/assets/activity-view.png" alt="Activity page for the demo coordinator kit, showing three running agents, one queued worker, four sub-agents, items working, in review, landing, and merged, and timeline bars for Claude, Codex, Grok, and OpenCode models" width="100%">
</p>

## What it is

pstack is Lauren Tan's set of skills, playbooks, and principles for careful agent engineering. Lauren Tan wrote that set for Cursor. pstack-t3 ships an upstream file unchanged when the file needs no T3 change. It swaps each Cursor mechanism for its T3 Code equivalent. The same skills then run on any provider T3 drives. pstack-t3 adds a standing project coordinator.

Run `python3 scripts/sync_upstream.py --check` to see what upstream changed. Nothing runs that check on a schedule. The check reads the commit pinned in `upstream.json`. `vendor/pstack` stays byte-identical to that commit. `t3/overrides` replaces each vendored file the port changes. pstack-t3 requires T3 Code nightly `0.0.46-nightly.20261005.2702` or later. It does not update T3 Code.

## What it does

- **Picks the right workflow for your request.** `$poteto-mode` matches your task to one of its playbooks, such as bug fix, feature, refactor, or investigation. It follows the playbook steps in a visible todo list.
- **Has different models check each other.** `$interrogate` sends your diff to reviewers on different model families. You get one verdict with verified claims and agreement mapped across families.
- **Runs work in parallel without collisions.** `$swarm` splits work across workers. `$arena` runs several attempts and grafts the best parts into one. Workers that write get their own git worktree.
- **Proves the change works.** It reproduces bugs on the real surface and verifies against the real artifact rather than trusting a compile.
- **Keeps going while you are away.** Long runs use child agents, separate worktree threads, and a scheduled check. They still stop for anything irreversible you did not authorize.

## Quick start

Start with [1. Install (two minutes)](docs/guide.md#1-install-two-minutes).

See [all 59 skills and every playbook](docs/skills.md).

pstack is by [Lauren Tan (poteto)](https://x.com/poteto). MIT licensed. See [LICENSE](LICENSE).
