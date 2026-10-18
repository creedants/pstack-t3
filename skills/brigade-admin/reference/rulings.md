# Rulings detail

Read this before `$B rule add` or acting on an overrule or appeal. The core keeps the kinds and the order. This file holds kinds, inputs, age and lease rules, carrying out, States, Replacing, Overrule, Appeals, and Escalate.


## Rulings

The admin rules on four kinds of conflict between coordinators. It applies the rules in order, and the first rule that separates the parties decides. A ruling stands until it is done, expires, is superseded, or the user overrules it. How a coordinator complies is in [Reporting to an executive admin](../../brigade/SKILL.md#reporting-to-an-executive-admin), under A ruling's side.

The rules read three inputs. Each coordinator's `menu.md`, with its purpose and `## Off the menu`. The user's priorities, the ranked names under `## Priorities` in the admin's `menu.md`. A coordinator the list does not name ranks below every named one, level with the other unnamed ones. Only the user changes the list. Age, how long the waiting work has waited. A blocked ticket's age counts from its first `blocked` row. A coordinator's age is its oldest waiting ticket's. Queue order counts from the `contest` line.

| Kind | Question | Rules, in order |
| --- | --- | --- |
| `contested-paths` | Which coordinator claims a contested path next, including a hot shared file such as `README.md` | 1. A path one purpose names and the other's `## Off the menu` excludes goes to the first (`purpose`). 2. The user's priorities (`priority`). 3. The older waiting work (`age`). |
| `ownership` | Which coordinator owns a request that fits two purposes | 1. A coordinator whose `## Off the menu` excludes it loses (`purpose`). 2. The user's priorities (`priority`). 3. The coordinator whose open work already touches the request's paths or ref (`related-work`). 4. Otherwise escalate. |
| `shares` | How many of the repository's changes in flight each coordinator may hold, out of the cap | 1. Each coordinator with waiting work gets one, by priorities, then age, until the cap runs out (`floor`). 2. The rest go by priorities, highest first, up to each coordinator's waiting work (`priority`). 3. A tie goes to the older waiting work (`age`). |
| `queue-order` | Which of two coordinators' passed items lands first when one would break or conflict with the other | 1. An item the other depends on, as a `contest` line stated, lands first (`dependency`). 2. The user's priorities (`priority`). 3. The older `contest` side (`age`). |

Age decides only when the earlier rules leave the parties level. Different ages then decide by `age`. Equal ages tie at the age rule. When priorities rank one side higher, the ruling is `priority` even when the ages are equal, because priority comes before age. The admin escalates only when no rule separates the parties. So every ruling has one outcome or escalates.

A live lease is never taken away. A contested-path ruling decides who claims next when the lease frees. What each side then does is in [Reporting to an executive admin](../../brigade/SKILL.md#reporting-to-an-executive-admin), under A ruling's side. A share counts changes in flight, as the cap does. Shrinking a share stops no running work.

**The user's ruling.** When the user settles a conflict between coordinators that no ruling covers, such as an order for two items, record it with `$B rule add --kind <kind> --parties <a>,<b> --question "..." --rule user --decision "<the user's words>"`. Then carry it out and send the `ruling` lines like any other ruling.

Carry out each ruling with its script, after `$B rule add` recorded it.

- `contested-paths`: `$L lease reserve --for <winner>/ --paths <paths> --ruling R<n> --owner .admin/@<generation>`. It prints `S<n>`. Every other holder's claim on those paths is refused until the winner claims them or the reservation expires. It arms, and starts its 2-hour clock, once no other holder's live lease overlaps it and both the cap and the winner's share have room for one more change. A reservation for a winner whose share is 0 stays waiting, so that winner also needs a shares ruling.
- `ownership`: `$B ticket move <id> --to <winner>` when the admin holds the ticket. Otherwise the holder acts on the `ruling` line as [Reporting to an executive admin](../../brigade/SKILL.md#reporting-to-an-executive-admin) says, under A ruling's side.
- `shares`: `$L share --for <coordinator>/ <n> --owner .admin/@<generation>` for each coordinator. It refuses a share that would bring the sum over the current cap.
  `$L cap` leaves existing shares as they are. A positive `$L cap N` refuses when the shares add up to more than N, and it writes nothing. When the user asks for a new cap, rule on shares again in the same service, in this order:
  1. Record the new ruling with `$B rule add --kind shares ... --supersedes R<n>`, naming the old shares ruling.
  2. For each share the ruling lowers, run `$L share --for <coordinator>/ <n> --owner .admin/@<generation>`. When that write is refused, clear that share with `$L share --for <coordinator>/ 0 --clear --owner .admin/@<generation>`.
  3. Run `$L cap N`. When that command is refused, finish step 2. Then run `$L cap N` again.
  4. For each share the ruling raises, and for each share step 2 cleared, run `$L share --for <coordinator>/ <n> --owner .admin/@<generation>`.
  Do not run `$L cap 0` to get past a refusal.
- `queue-order`: when no contest covers the two holders, such as an order the user states, first run `$L contest --holders <first holder>,<second holder> --owner .admin/@<generation>`. It prints `C<n>`, and a rerun prints the same id. Then `$L contest --settle C<n> --first <holder> --owner .admin/@<generation>`. What `land` then holds is in [Reporting to an executive admin](../../brigade/SKILL.md#reporting-to-an-executive-admin), under A ruling's side.

Then send the `ruling` line to each coordinator involved.

**States.** A ruling starts `in-force`. Mark it `done` with `$B rule set R<n> --state done` when its condition ends. A rerun of its `lease reserve` prints `S<n> was claimed in full`, the moved ticket appears in the winner's `rail.tsv`, the share is in place, or `$L status --holder <first holder>` shows a `landed` entry. Mark it `expired` when the rerun refuses with `S<n> for ruling R<n> expired`, or its entry left the queue unordered. Rule again on the next service if the conflict remains. Recording comes first, so a crash never leaves a ruling carried out but unlogged. A rerun of `lease reserve` with the same ruling, `ticket move`, `share`, and `contest --settle` gives the same result.

**Replacing a ruling.** A new ruling that changes an earlier one names it with `--supersedes R<n>`. Remove the old constraint before carrying out the new one, with `$L lease unreserve S<n> --owner .admin/@<generation>`, a new `contest --settle`, `$L contest --cancel C<n> --owner .admin/@<generation>`, or new shares.

**Overrule.** The user can overrule any ruling by describing it. Run `$B rule overrule R<n> --decision "<the user's words>"`, which records a ruling decided by `user` that supersedes it, then carry the new one out. An overrule changes what happens next. It cannot undo a lease already claimed or work already landed. Say so. When the overrule states a general preference, ask whether to add it to `## Priorities`, and add it only when the user says yes.

**Appeals.** A coordinator appeals as [Reporting to an executive admin](../../brigade/SKILL.md#reporting-to-an-executive-admin) says, under A ruling's side. When an `appeal` says compliance would be irreversible, escalate it with `$B 86 add` and act on it only after the user answers. On any other `appeal` line, recheck the ruling with the appeal's facts, such as a dependency it did not know. When the rules now decide differently, record a new ruling that supersedes the old one. Otherwise keep it, and list the appeal in the next update for the user.

**Escalate** only what the rules cannot settle. Park each with `$B 86 add --question "..." --options "..." --default "..."`. The default is the option that keeps things as they are, such as the current holder keeping the paths or the ticket staying with the admin. When no option does, such as two waiting parties, the default is the party whose name sorts first. `86 add` does not check for repeats, so first read `$B 86 list`, and when it already lists that conflict, park nothing new. When the user answers one of the admin's own questions, run `$B 86 answer Q<n> --answer "<the user's words>"` first. Act on that conflict only after that command. An answer to a coordinator's decision, including one that keeps it parked, goes back as an `answer` line. What the coordinator does with it is in [Reporting to an executive admin](../../brigade/SKILL.md#reporting-to-an-executive-admin), under Requests.

- A real priority call. No rule separates the parties. One coordinator lost three rulings in a row on the same paths, or its work has waited on rulings for more than 24 hours. An ordered entry has waited more than 24 hours on an item that keeps bouncing.
- A change to a purpose. The same two purposes collided in more than three rulings in a week, or a request fits no purpose. Propose new wording. Never edit a coordinator's `menu.md`.
- Anything irreversible. Dropping or closing another coordinator's work, deleting a branch, and changing the repository cap or the landing mode belong to the user. Run `$L cap <n>` and `$L mode <mode>` only when the user asks, with no `--owner`, because neither is a ruling write, and after `$L mode`, tell every coordinator with a `from-user` line.
