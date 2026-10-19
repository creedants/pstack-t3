Part of [the design](../multi-coordinator.md).

### Rulings

The admin rules on four kinds of conflict between coordinators, on its own authority. It decides by the rules below, applied in order, and the first rule that separates the parties decides. A ruling binds the coordinators involved until it is done, expires, is superseded, or the user overrules it.

The rules read three inputs.

- Each coordinator's `menu.md`, its purpose and its `## Off the menu` exclusions.
- The user's priorities, a ranked list of coordinator names under `## Priorities` in the admin's own `menu.md`, highest first. A coordinator the list does not name ranks below every named one, level with the other unnamed ones. Only the user changes the list.
- Age, meaning how long the waiting work has waited. For a blocked ticket it counts from its first `blocked` row. For a coordinator, it is the age of its oldest waiting ticket. For queue order it counts from the `contest` line.

| Kind | Question | Rules, in order |
| --- | --- | --- |
| Contested paths | Which coordinator claims a contested path next, including a hot shared file such as `README.md` | 1. A path that one coordinator's purpose names and the other's `## Off the menu` excludes goes to the first. 2. The user's priorities. 3. The older waiting work. |
| Ownership | Which coordinator owns a request that fits two purposes | 1. A coordinator whose `## Off the menu` excludes it loses. 2. The user's priorities. 3. The coordinator whose open work already touches the request's paths or ref. 4. Otherwise it is a real priority call, and the admin escalates. |
| Shares of the cap | How many of the repository's changes in flight each coordinator may hold, out of the cap the user set | 1. Each coordinator with waiting work gets one, in the order of the user's priorities, then age, until the cap runs out. 2. What is left goes by the user's priorities, highest first, up to each coordinator's waiting work. 3. A tie goes to the older waiting work. |
| Queue order | Which of two coordinators' passed items lands first when one would break or conflict with the other | 1. An item the other depends on lands first, as either coordinator or its verifier stated in a `contest` line. 2. The user's priorities. 3. The older `contest` side, meaning the item whose coordinator was waiting first. |

When the two ages differ, age decides. When they are equal, no rule separates the parties, and the admin escalates. So every ruling either has one outcome or escalates.

A live lease is never taken away. A contested-path ruling decides who claims next when the lease frees, and keeps the holder from starting new work on those paths while the winner waits. A share counts in the same unit as the cap, changes in flight, which are unreleased leases (change 2). Shrinking a share stops no running work. It only refuses that coordinator's next admission until it is back under its share.

Each ruling is carried out by a script wherever one exists (change 8).

- **Contested paths.** The admin runs `land.py lease reserve --for <winner>/ --paths <paths> --ruling R<n>`. Every admission, whether `lease claim` or the re-admission of an expired lease in `lease renew`, refuses another holder's overlap with a standing reservation. A reservation arms, which starts its 2-hour clock, only when no other holder's live lease overlaps it and the cap and the winner's share have room for one more change. Arming is itself an admission, done in the same transaction as claims, oldest reservation first. An armed reservation counts toward the cap and the share as one change in flight, so the winner always has room to claim. It stops counting once the winner holds a lease taken from it, so one piece of work never counts twice. A winner's claim releases only the reserved paths it covers. The rest stay reserved. A reservation that expires unused ends its ruling as `expired`, and the admin rules again on its next service if the conflict remains.
- **Ownership.** `ticket move` to the winner.
- **Shares of the cap.** The admin runs `land.py share --for <coordinator>/ <n>` for each coordinator, which `lease claim` and re-admission enforce in the same transaction as the cap. `land.py share` refuses shares that add up to more than the cap. A coordinator's `--workers` (change 5) stays its own limit on running agents, and is not a share.
- **Queue order.** The coordinator that finds the conflict runs `land.py contest --holders <its item's holder>,<the other item's holder>` before it sends the `contest` line. That prints `C<n>`. While the contest is open, `land` holds every entry of both holders, queued now or submitted later, out of every batch and, in `human` and `merge` mode, out of PR opening. So neither side can land before the ruling. When one holder's item is already landing, awaiting merge, or landed, `contest` refuses, because there is nothing left to order. An earlier bounced entry does not count. The admin carries out the ruling with `land.py contest --settle C<n> --first <holder>`. From then on `land` holds the second holder's entries until an entry of the first holder lands, so a bounce and a resubmit of the first item keep the order. Settling again with the other holder first replaces the order while neither has landed. `--settle` refuses an order that would close a cycle with another settled contest.

**The ruling log.** Before it carries a ruling out, the admin records it with `brigade.py rule add` in `rulings.tsv` in its own store. Each row holds an id `R<n>`, the time, the kind, the parties, the question, the rule that decided, the decision, the ruling it supersedes, and a state. The deciding rule is one of `purpose`, `priority`, `age`, `related-work`, `dependency`, `floor`, or `user`.

A ruling starts `in-force`. It becomes `done` when its condition ends. That means the reservation was claimed in full, the moved ticket appears in the winner's `rail.tsv`, the share is in place, or the first holder's entry landed after a settled contest. It becomes `expired` when its reservation runs out or its entry left the queue unordered. It becomes `superseded` or `overruled` when a later ruling replaces it.

Recording comes first, so a crash never leaves a ruling carried out but unlogged. Every service first reconciles each `in-force` ruling with what the scripts report, and marks it `done` or `expired` when its condition already ended. Then it carries out the ones still `in-force` again, which repeats nothing. `lease reserve` with the same ruling returns the same reservation. `ticket move`, `land.py share`, and `land.py contest --settle` give the same result when rerun.

**Replacing a ruling.** A new ruling that changes an earlier one, whether from an appeal or an overrule, names it in `supersedes`. The admin removes the old ruling's constraint before it carries out the new one. It runs `lease unreserve` on the old reservation, settles the contest again with the new first holder or cancels it with `land.py contest --cancel`, or sets the new shares. A coordinator checks the ruling's state in the admin's `rulings.tsv` before it acts on a `ruling` line, and drops a line whose ruling is no longer `in-force`. So a stale request file replayed after a replacement does nothing.

**Review and overrule.** Every update lists the rulings made since the last one, each with the rule that decided it, in plain words. The user can overrule any ruling by describing it. The admin runs `rule overrule R<n> --decision "<the user's words>"`, which marks it `overruled` and records a new ruling decided by `user` that supersedes it, then carries the new one out. A conflict the user settles before any ruling covers it, such as which of two items lands first, is recorded the same way, with `rule add --rule user`. An overrule changes what happens next. It cannot undo what the old ruling already caused, such as a lease already claimed or work already landed, and the admin says so. When the overrule states a general preference, the admin asks whether to add it to `## Priorities`, and adds it only when the user says yes.

**What it escalates.** The admin asks the user only what the rules cannot settle. It parks each as a decision with `86 add`, with options and a default, and acts on that conflict only after the answer.

- A real priority call. No rule separates the parties, as in an ownership case where no exclusion, priority, or related work decides, or an exact tie. Or the rules keep ruling against one coordinator, which lost three rulings in a row on the same paths, or whose work has waited on rulings for more than 24 hours. Or an ordered entry has waited more than 24 hours for an item that keeps bouncing. Those thresholds are starting guesses for the live run to tune.
- A change to a purpose. The same two purposes collided in more than three rulings in a week, or a request fits no purpose. The admin proposes new wording. It never edits a coordinator's `menu.md`.
- Anything irreversible. That covers dropping or closing another coordinator's work, deleting a branch, and changing the repository cap or the landing mode, which belong to the user.

**Coordinators comply, and can appeal.** A coordinator carries out a ruling for its own side. When it disagrees, it still complies and sends `appeal`. The admin rechecks the ruling with the appeal's facts, such as a dependency it did not know. When the rules now decide differently, it records a new ruling that supersedes the old one. Otherwise it keeps the ruling and lists the appeal in the next update for the user to review. When compliance would be irreversible, the coordinator holds instead of complying, and the admin escalates.

### Recovery and retirement

The store outlives its thread. Recovery is a user request, run from one thread, and it claims the store before it touches anything. It runs `set --thread recovering:<its own thread id> --replace --expect <old>`, a compare-and-swap. Of two recoveries racing, one wins and the other exits 1 before it changes anything. The claim records the old thread as `previousThread`.

**The owner token is the safety.** The admin's `restaurant.json` holds the recorded thread and a `generation`, and every replacement of the thread adds 1 to it. A service passes the token it read at its fence to every command. Each `brigade.py` write checks that token under the store lock, and holds the lock through the write. A token that no longer matches exits 1 with `brigade: owner <token> is stale; this store is owned by <thread>@<generation>`, and changes nothing. `set --thread` raises the landing store's floor for `.admin/` to the new generation before it rewrites `restaurant.json`. Each `land.py` write the admin makes checks its `--owner` against that floor in the same transaction as the write (change 8). So a command the old run started, whenever it finishes, either completed before the claim or is refused after it. That holds for a command T3 did not kill when the run ended, and for a run that never ends.

The steps below stop the old run before a new thread starts, so its commands end instead of failing one by one. They are a courtesy. The fence does not depend on them.

1. Delete the schedule ids the claim printed with `delete_scheduled_task`, and clear their names, so no new wake starts.
2. When the old thread still exists, call `t3_thread_interrupt` on it. A result of `status: "interrupt_requested"` means the run has not stopped yet.
3. Call `t3_thread_wait` on the run id that `t3_thread_interrupt` returned, or on the thread with no run id when it returned none. An idle thread returns at once with `status: "idle"` and no run id.
4. When the wait returns `timedOut: true`, recovery stays pending. The store keeps `recovering:<its own thread id>`. The recovering thread tells the user, launches nothing, and waits again on its next turn, on the thread in `previousThread`.
5. When the wait reports a terminal state, archive the old thread. Launch the new thread with the waiting message from opening step 5. Run `set --thread <new> --replace --expect recovering:<its own thread id> --stopped <run id>` with the run id the wait reported, `--stopped idle` when the wait reported an idle thread, or `--stopped gone` when the old thread no longer exists. Then send the start message.

`set --thread` refuses to replace a `recovering:` value without `--stopped`. `--stopped` is evidence the recovering thread asserts, recorded in `log.tsv` for audit. The script cannot check it, and the fence does not rely on it. The new thread's first service sends every coordinator the `reports-to` line. Pending tickets, owned intake, and every ruling the old thread recorded before the claim survive in the stores, and that service reconciles them as any service does. When the recovering thread is itself gone, a later recovery claims with `--expect recovering:<that thread id>`, keeps `previousThread`, and runs the same steps.

Retiring the admin routes or drops its waiting tickets, then clears its intake with `set --intake ""`. It deletes its schedules with `delete_scheduled_task`, clears each name with `set --schedule <name>=`, sends a last update, and unpins its thread. Last, `set --thread "" --replace --expect <its own id>` clears the recorded thread and raises the generation, so any later wake stops at the fence and any command still running exits as a stale owner. Each coordinator then runs `set --reports-to ""`, and takes a shared source back with `set --intake <source>` where the user wants it. Restarting a retired admin is recovery with `--expect ""` in place of the old thread and `--stopped gone`.

### What the user hears

The user hears from one thread. A coordinator whose `restaurant.json` has `reportsTo` replies to the user only when the user writes to it directly. Its own reporting level stops mattering. It sends every event in the table above, whatever its level, because the admin's level filters replies, and it cannot filter events it never received. Its `close --to-file` still writes its full report into its own store.

The admin's level uses the same three values as a coordinator's. At `every-turn` it replies after each of its own wakes, and each message is a wake. A coordinator's routine wake sends no line, so the user stops hearing about routine wakes. At `milestones` it replies when any coordinator's work merges, when a review sends work back or blocks it, when the user has a decision to make, or when a failure arrives. At `digest` it replies for a decision, a failure that no coordinator can fix itself, one summary when every coordinator has drained, and the evening update. A ruling is never a reply occasion on its own at `milestones` or `digest`. Each update lists the rulings made since the last one in plain words, with the rule that decided each, and says that the user can overrule any of them by describing it. The full update file holds each ruling's id.

Every admin reply is plain language at every level, written as brigade's Digest messages rules say. A few sentences say what changed for each purpose, what is next, and what the user must decide, each decision with its default. It leaves out work ids, SHAs, paths, branch names, and tool names. It names a coordinator by its purpose, not its store name. Merged PRs may follow as plain titles linked to their URLs.

The reply ends with one line naming the full update, which `close --to-file` writes in its store. The service copies each coordinator's events into the admin's own `log.tsv`. So that update is the admin's ordinary report of its own log, plus the newest report path of each coordinator.

A message from the user gets at least a one-line acknowledgment at every level, and a direct question gets an answer, as for any coordinator. A coordinator's decision keeps its home in that coordinator's `86.tsv`, and the user's answer goes back as an `answer` line. The admin's own questions, such as a ticket no purpose fits, live in its own `86.tsv`.

### Replaces or adds, gap by gap

| # | Gap | With an executive admin |
| --- | --- | --- |
| 1 | Duplicate intake | Replaces the owner. For a repository the user names, the admin owns the shared source, so no coordinator does. Change 4's mechanism is unchanged. |
| 2 | No cap across coordinators | Replaces each coordinator choosing its own share. `land.py cap` still enforces the total the user sets, and the admin rules each coordinator's share. |
| 3 | No handoff | Adds. `ticket move` stays the mechanism, and coordinators keep moving tickets to siblings themselves (change 4). The admin also routes the user's requests and shared intake, and rules ownership when a request fits two purposes. |
| 4 | Overlapping purposes | Adds. `open` still prints siblings. The admin shows the user any overlap before a new coordinator launches. |
| 5 | Blocked tickets | Replaces the race. `watch` still reports. When a block lasts an hour, the admin rules who claims next and reserves the paths for the winner, so when the lease frees only the winner's `fire` succeeds. |
| 6 | Mode drift | Replaces one step. Change 7's coordinator running `land.py mode` and telling each sibling becomes the admin, acting when the user asks. |
| 7 | Hot shared files | Replaces "each coordinator batches its own". Hot shared files get the contested-path rules. Doc follow-ups go to the admin, which routes them by the ownership rules. |
| 8 | No combined view | Replaces the user-facing part. The admin's update is the combined view of its repository. Grouped `walk` (change 7) stays as its data source, and the view across repositories. |
| 9 | Work landed by a sibling | Neither. Each coordinator settles its own entries (change 6). |
| 10 | Lease expiry | Adds a little. `walk --stale-hours 3` shows a coordinator idle for 3 hours, and the admin asks the user before that coordinator's 6-hour leases lapse. |
| 11 | Store collision | Neither. |
| 12 | Renew breaks the lease invariant | Neither. |

Queue order between two coordinators' work is a conflict the gap list did not name. The admin rules it, and `land.py contest` (change 8) enforces it.
