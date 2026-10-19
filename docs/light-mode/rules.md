Part of [the setting](../light-mode.md).

### Waivers

A waiver removes one named step for one attempt. `roles.py mode` prints the waivers from one table, `LIGHT_WAIVERS` in `t3/scripts/roles.py`, keyed by playbook and attempt kind. The attempt kinds are `first`, `fix` after a send-back, and `bounce` after a queue bounce. Step names are the playbook own words.

| Playbook | `first` | `fix` and `bounce` |
| --- | --- | --- |
| Feature | Arena, Interrogate, Comment Sicko | How, Architect, Arena, Interrogate, Comment Sicko |
| Bug fix | Comment Sicko | How, Why, Architect, Comment Sicko |
| Refactoring | Comment Sicko | How, Architect, Comment Sicko |
| Perf issue | Comment Sicko | How, Architect, Comment Sicko |
| Hillclimb | Comment Sicko | How, Comment Sicko |
| Authoring a skill | Comment Sicko, Second-provider test | Comment Sicko, Second-provider test |
| Every other playbook | none | none |

Interrogate is a waiver only in Feature, where `feature.md` step 7 runs it for a contested design. In light mode a contested design escalates instead. The other interrogate call, in a child that opens a PR, never runs inside brigade, whose worker brief forbids opening a PR. Outside brigade, light mode replaces it with the gate review.

The Bug fix fix-attempt row waives Bug fix step 2 `how` and `why` seeds. The review findings name the defect, so the worker confirms that mechanism with runtime evidence, which step 2 last sentence already requires, and goes to step 3. Step 1 still reproduces the finding.

A step that light mode reduces but does not remove is not a waiver. The single architect runner, the simple `how` path, and the source-control-only `why` are how those steps run in light mode, so a worker that runs them has skipped nothing.

### Brief lines

A brief carries these lines, each at most once.

```text
Playbook: playbooks/feature.md
Mode: light
Mode source: restaurant.json
Attempt: fix
Waived by mode: How, Architect, Arena, Interrogate, Comment Sicko
Gate: brigade
```

- `Mode:` holds only `full` or `light`. Nothing follows the value.
- `Mode source:` holds one of `brief`, `session`, `restaurant.json`, `.pstack/t3-roles.json`, `roles.json`, `default`, or `escalated: <reason>`. It names where the decision came from. Brigade reads an `escalated:` reason back to keep the escalation durable.
- `Attempt:` holds `first`, `fix`, or `bounce`.
- `Waived by mode:` lists the waivers, comma-separated, in table order.
- `Gate:` holds `brigade` in a brigade brief.

`roles.py mode` always prints `Mode:` and `Mode source:`. With `--playbook` and `--attempt`, it also prints `Playbook:` and `Attempt:`. It prints `Waived by mode:` only when that mode, playbook, and attempt have waivers. `brigade.py brief` writes the printed lines unchanged and then adds `Gate: brigade` and the seat rule.

### `roles.py check-brief`

`check-brief` validates the lines against the table, not against a global union.

- At most one of each line. `Mode:` is required in a code delegate brief.
- `Mode:` is `full` or `light`.
- Under `Mode: full`, no `Waived by mode:` line.
- Under `Mode: light`, `Attempt:` is required, and `Waived by mode:` must equal `LIGHT_WAIVERS[(playbook, attempt)]` exactly. The playbook comes from the `Playbook:` line. A first-attempt Feature brief with `Waived by mode: How` fails, because How runs on a first attempt. A first-attempt Feature brief without Comment Sicko in its waivers fails too, so a compliant light worker is never sent back for skipping it.

### Interaction with Deadlines

The runtime rule in [Deadlines](../../t3/runtime.md#deadlines) says a timebox orders the work and never waives a step. Brigade sends back a skip cited to the timebox. Light mode keeps that rule whole. A waived step is not a skip, because the mode removes it before the attempt starts and the brief records it.

The worker runs every playbook step its `Waived by mode:` line does not name, under the Deadlines rule as written. It never adds a waiver of its own. At review, the coordinator compares the report skipped steps with that line. A skip the line names passes. Any other skip, including one cited to the timebox, is still a send-back. A mode change mid-attempt does not change the line. It applies to the next brief.

### The fresh-worker rule

A send-back launches a fresh worker with `t3_thread_launch` unless the item is held or owes its round decision. The rule exists so that a fix never inherits a stale context or drops a directive, and light mode does not weaken it. Light mode makes the fresh worker cheaper. Its brief carries `Attempt: fix`, the waivers from the table, the verifier findings file, and the old branch head as its base.

A queue bounce is not a send-back. Its fresh worker gets `Attempt: bounce` and the bounce reason. It does not count toward rule 2.

### Fresh-child tests in light mode

The runtime requires one executing fresh-child test per changed spawn or coordination behavior.

- A leaf test runs on the `skill tests` seat and forbids spawning. It is allowed only when the changed behavior happens without delegation.
- An executing test runs on the `skill tests` seat. It is required once for each changed spawn or coordination behavior. One test covers one behavior.
- The second-provider test is waived in light mode. The executing test runs on the `skill tests` seat, which resolves to `inherit` when the user set no seat for it and the exclude list allows the inherited seat. A test whose child launches seats resolves that seat with `roles.py show --role "skill tests" --launches-seats`. A configured seat that resolves to the cursor driver is refused with `cannot launch seats`. An unset `skill tests` keeps a note and is not refused.
