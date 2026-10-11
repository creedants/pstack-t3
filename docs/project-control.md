# Project control

Use one project conversation for follow-ups. The `$project` workflow keeps the repository's purpose, sourced decisions, finite goals, and peer identities in SQLite outside every Git worktree. It restores that record after compaction or a provider restart. The conversation itself remains an ordinary T3 thread.

Poteto chooses an execution strategy for each request. A persistent project does not require parallel workers on every turn.

| Work | Default strategy |
| --- | --- |
| Answer or small known low-risk edit | Direct work by the lead |
| One unit with unknown scope or material risk | One delegated worker |
| Independent finite slices | Swarm with one combined report |
| Independent work requiring peer communication or retained expertise | Mesh of ordinary durable threads |
| Competing implementations to compare | Arena |
| Explicit standing intake, with the POSIX runtime available | Brigade |

Explicit choices and opt-out override automatic routing. The policy bounds worker count and preserves the existing Poteto playbooks. It is a routing heuristic, not a measured guarantee of the fastest model or workflow.

## Enable the default

From this checkout, on Windows or POSIX with Python 3.10 or later:

```powershell
python scripts/build.py --skills-only
python scripts/activate.py install --home C:/Users/your-name --harness codex --dry-run
python scripts/activate.py install --home C:/Users/your-name --harness codex
python scripts/activate.py status --home C:/Users/your-name --harness codex
```

Use your actual home directory. For Claude, choose `--harness claude`. The installer writes Codex's `~/.codex/AGENTS.md` or Claude's `~/.claude/CLAUDE.md` and copies generated skills into that provider's skill directory. Open a fresh conversation so its instructions and skill catalog are loaded. This activation does not install defaults for other providers.

Existing skills are backed up under `~/.pstack-t3/activation/<harness>`. Unrelated instructions remain intact. Rerun install after rebuilding to update managed copies. Edited managed files cause a refusal instead of an overwrite. After a hard interruption, inspect `status` and its named transaction files before manual recovery.

```powershell
python scripts/activate.py uninstall --home C:/Users/your-name --harness codex
```

Uninstall restores replaced entries and removes only its owned instruction block. The original symlink installer and Brigade landing runtime remain POSIX workflows. Portable activation does not make those commands native Windows programs.

## Start and continue

Tell a fresh T3 conversation what you want to accomplish. For example:

```text
Keep this conversation as my project interface. Build the first version of
the export service, retain its design decisions, and choose the smallest
appropriate execution strategy for each follow-up.
```

The interface records each request with acceptance criteria and an idempotent request key. A mesh gets a separate task board and frozen Git baseline per goal. Peers claim their own work, ask each other questions directly, and review exact authored commits. One queue checks and integrates results. Completed peer identities can be reused on a later goal after verifying that their native runs are idle and their worktrees are clean. Previous Git heads are preserved in history refs.

For an existing repository with a P-stack landing authority, retain that queue. The bundled mesh queue refuses to create a second authority. An adapter to submit mesh work into the existing P-stack queue is not included; use its canonical workflow for integration.

For manual routing inspection, call the installed project CLI with UTF-8 JSON:

```powershell
python C:/Users/your-name/.codex/skills/project/scripts/project.py route --data '{"small":true,"scope_known":true}'
```

For larger command payloads, use `--data '@request.json'`. Quote the `@file` argument in PowerShell. The [project skill](../skills/project/SKILL.md) and [mesh skill](../skills/mesh/SKILL.md) describe the lifecycle and verification steps.

## Keep peers together visually

The companion T3 source patch adds an optional visual container for ordinary threads. Peers appear under their interface and remain independent agents with separate worktrees and conversations. Attaching or detaching a peer does not wake it, stop it, change its permissions, or create a parent-owned delegated task.

The skills check `orchestrator_capabilities.features.visualPeerContainers` before using this feature. Install a T3 build containing that patch to enable it. Existing nightlies do not acquire the feature by installing skills. On an older build, completed peers can be settled to reduce active clutter, and their histories remain accessible.

Persistence applies to the conversation and shared records. Provider context compaction, restarts, rate limits, and available models still constrain execution. A same-family peer review is reported as such when no other model family can run.
