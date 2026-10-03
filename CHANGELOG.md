# Changelog

## Unreleased

- Synced to upstream pstack 0.15.7 ([cursor/plugins@9511e60](https://github.com/cursor/plugins/commit/9511e60321f7e533a187d62854a3d53a53752874)). Adds `$correct`.
- A README covering what pstack-t3 does, real run excerpts, a table of example prompts, a diagram, and an FAQ.
- A [guide](docs/guide.md) to the first hour, a [how-it-works](docs/how-it-works.md) page, and a generated [skills catalog](docs/skills.md) that the build keeps in sync.
- A banner and a social preview image in `docs/assets/`.

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
