# Modes

Read this file for the checks every mode keeps.

### Never cut

- Tests and the build. The worker runs the project's test command and build before every gate review, and the landing queue reruns them.
- The landing queue's checks.
- One review by another model family at every head SHA before merge, per [Gate review](light.md#gate-review).
- A review reads the whole diff against its base on round one. A later round reads the fix diff since the last reviewed SHA, per [Rounds after the first](../brigade/verdict-protocol.md#rounds-after-the-first).
- Owner fences. Leases, `--owner` generations, worktrees, and the pass-at-head gate.
- A fresh worker for every send-back, per [Fresh children by default](SKILL.md#fresh-children-by-default).
- The code delegate, with the persona, the `roles.py mode` lines, and `roles.py check-brief`.
- One executing fresh-child test per changed spawn or coordination behavior.
