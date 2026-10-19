Part of [the guide](../guide.md).
See [the rule set](../multi-coordinator/admin-rulings.md#rulings) for the full ruling rules.

#### An executive admin

A repository works with no admin. Add one when several coordinators share a repository and you would rather hear from one thread than from each of them. The executive admin works for you on one repository. It forwards your requests, files shared intake once, settles conflicts between coordinators by published rules, and sends you one plain update. It never writes code, starts work, lands work, or overrides a review. Each coordinator still owns its own work, reviews, and queue entries. If the admin's thread goes away, every coordinator keeps working and replies to you directly again.

**Opening it.** Type this in any thread.

```
$brigade-admin open an executive admin for <project>.
```

The opener asks how often it replies, unless you already said. It recommends `digest`, because the admin exists so you hear less. It runs [`brigade.py open --admin`](../cli/brigade.md#brigadepy-open) with the project root and `--reporting digest`, which creates the store `<project directory slug>/.admin`. There is one admin per repository. A second open prints `exists`. The output lists every coordinator on the repository with its purpose and what it does not take. The admin store refuses `fire`, `brief`, `dish`, `pass`, and `watch` with `the executive admin routes work and never runs it`.

The opener then asks you for two things.

- **Your priorities.** A ranked list of coordinator names under `## Priorities` in the admin's `menu.md`, highest first. The rules below read it. A coordinator the list does not name ranks below every named one. Only you change the list. Without one, the rules fall back to purposes and age.
- **Shared intake.** Which sources the admin should own, such as `github`. Each coordinator that owns that source gives it up first, after it finishes or moves its open tickets from that source. It runs `set --intake` with its remaining sources, or `set --intake ""` when that source was its only one. Then the admin takes it with `set --intake github`. The admin files each issue once and routes it to the coordinator whose purpose fits.

The opener also asks you for the intake interval in whole minutes and the morning and evening times as `HH:MM`. It offers one hour, `09:00`, and `18:30` as the defaults.

Last, the opener launches the admin's thread in the repository's project and pins it. On its first run the admin tells each coordinator to report to it, and each records that with [`set --reports-to`](../cli/brigade.md#brigadepy-set) and the admin's thread. A coordinator's `status` then prints `reports to <thread>`, and `walk --repo <root>` lists the admin first in that repository's group, as `executive admin (reports digest)`.

**Sending it requests.** Write to the admin as you would to a coordinator. It routes each request to the coordinator whose purpose fits. When two fit, it makes an ownership ruling. When none fits, it asks you. A request that is an instruction rather than new work, such as "pause the engine work", goes to that coordinator with your words quoted. That coordinator can decline with a reason, and the admin passes the reason on. Each request is a file in the coordinator's `inbox/`, so a retried message never files the same work twice. The coordinator acts on it with `inbox take` and `inbox done <id>`, and `ticket add --request <id>` refuses a second ticket for one request. While requests wait, the coordinator's `status` prints `requests from the executive admin: N`.

You can still write to any coordinator directly. It answers you as it always did.

The admin settles four kinds of conflict by published rules. See [the rule set](../multi-coordinator/admin-rulings.md#rulings) for the table and the order. It runs `land.py cap` or `land.py mode` only when you ask, and then tells every coordinator.

A coordinator must comply with a ruling. When it disagrees, it complies and appeals. The admin rechecks the ruling with the new facts, and either replaces it or keeps it and lists the appeal in its next update for you. When compliance would be irreversible, the coordinator holds instead of complying, and the admin asks you.

**Overruling.** Every update lists the rulings made since the last one, each with the rule that decided it. To overrule one, describe it in plain words, such as "let the engine coordinator take the README first." The admin runs [`rule overrule`](../cli/brigade.md#brigadepy-rule-overrule) with that ruling and your words, which marks the old ruling `overruled` and records yours in its place, then carries yours out. An overrule changes what happens next. It cannot undo a lease already claimed or work already landed, and the admin says so. When your words state a general preference, the admin asks whether to add it to `## Priorities`. `rule list` in the admin's store prints the log.

```
R1 overruled contested-paths (docs, engine): Who claims README.md next? Decided by age: docs claims README.md next.
R2 in-force contested-paths (docs, engine): Who claims README.md next? Decided by user: engine goes first. Supersedes R1.
```

A script carries out each ruling. See [how rulings are enforced](../multi-coordinator/admin-rulings.md#rulings) for the reservation and share commands.

**Recovery.** If the thread breaks, ask any thread to recover the executive admin. See [Recovery and retirement](../multi-coordinator/admin-rulings.md#recovery-and-retirement) for the replacement steps. Reads such as `status` still work.

To retire the admin, ask it to close.
