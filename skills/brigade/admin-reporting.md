# Reporting to an executive admin

A restaurant reports through the repository's executive admin while `$B status` prints `reports to <thread>`. What the admin does, its side of each line below, and its rulings are in the [brigade-admin skill](../brigade-admin/SKILL.md). [What the admin never does](../brigade-admin/SKILL.md#what-the-admin-never-does) lists its limits. The user can still write to this thread directly. In the lines below, `<restaurant>` is this restaurant's directory name.

**Events.** Send each line to the admin's thread with `t3_thread_send` and mode `"queue"`, so it never interrupts a turn in progress. Send every event, whatever this restaurant's reporting level. The table's last column says which lines also leave a row in this restaurant's `log.tsv`. `failed`, `drained`, `report`, `reply`, `contest`, and `appeal` leave none, so they are message-only. How the admin reads each kind is in [Admin service](../brigade-admin/SKILL.md#admin-service) step 5. When a send fails, reply to the user about that event per Run a service step 9: at this restaurant's own level for a line with a store row, and at every level for a message-only line. A send to an admin thread that no longer exists is a failed send, so the user still hears every event.

| Line | Send when | Store row |
| --- | --- | --- |
| `merged <restaurant>: <title> [<pr url>]` | work lands. `push` and `local` modes have no PR URL | `dish` `merged` |
| `sent-back <restaurant> D<n>: <one line>` | a review sends work back or blocks it | `dish` `sent-back` or `blocked` |
| `decision <restaurant> Q<n>: <question> Options: <options>. Default: <default>.` | it parks a decision for the user | `decision` `open` |
| `failed <restaurant>: <one line>` | a failure or a paused queue it cannot fix itself | none |
| `drained <restaurant>: <closeout path>` | its batch drains. Run `$B close --to-file` for the path | none |
| `report <restaurant>: <closeout path>` | its evening report is written with `$B close --to-file` | none |
| `blocked <restaurant> T<n>: waiting on L<n> held by <holder>[ and L<n> held by <holder>] since <time>` | a ticket has been blocked for more than an hour. Name every lease it waits on, joined with `and`. `<time>` is its first `blocked` row in `log.tsv` | `ticket` `blocked` |
| `misrouted <restaurant> T<n>: <why>` | a routed ticket is off the menu, after `$B ticket move T<n> --to .admin` | `ticket` `moved` |
| `reply <restaurant>: <one line>` | it answers a `from-user` request, including a decline and its reason | none |
| `contest <restaurant> C<n> D<n>: <one line>` | its item and another coordinator's would conflict in the queue, or its passed item depends on another coordinator's unlanded item, after it opened contest `C<n>`. Name which depends on which | none |
| `appeal <restaurant> R<n>: <why>` | it disagrees with a ruling, after it complied, or instead of complying when compliance would be irreversible | none |

Send `blocked` from the Liveness check, once per ticket, with `clientRequestId` `blocked:<store path>/T<n>`, such as `blocked:app/docs/T4`, where the store path is the last two parts of `<restaurant dir>`, so a later check that sends it again delivers nothing new.

**Requests.** The admin sends each request as [Admin messages](../brigade-admin/SKILL.md#admin-messages) says. `$B inbox take` prints it as `A<n>: <line>`. Act on it, then run `$B inbox done A<n>`. A crash between the two replays the request, so key every action by its id.

| Line | Do |
| --- | --- |
| `from-user <restaurant>: <the user's words>` | Treat it as a message from the user. Work it asks for becomes `$B ticket add --summary "..." --kind <kind> --request A<n>`, which refuses a second ticket for that id. Answer with the `reply` line. |
| `answer <restaurant> Q<n>: <answer>` | `$B 86 answer Q<n> --answer "<answer>"`. When it prints `Q<n> still open`, the item stays held, so launch and spawn nothing for it. When it prints `Q<n> answered`, act on the answer. |
| `reports-to <restaurant> <thread>` | `$B set --reports-to <thread>`, or `$B set --reports-to ""` when `<thread>` is `none`. On a new thread, create the hourly service schedule from First service step 3 when `restaurant.json` records no `intake` schedule. On `none`, delete that schedule if the menu names no intake source. |
| `ruling <restaurant> R<n>: <decision>` | Read the ruling's state with `python3 <skills>/brigade/scripts/brigade.py --at <restaurant dir>/../.admin rule list`. When `R<n>` is not `in-force`, do nothing. Otherwise carry out this restaurant's side. |

The `ticket <restaurant>: run ticket take` line from the admin is a routed ticket, and `inbox take` files it. Take it per Run a service step 2. When it is off the menu, move it back with `$B ticket move <id> --to .admin` and send `misrouted`.

**A ruling's side.** A coordinator complies with every ruling, and may appeal.

- Contested paths. The admin reserves the paths as [Rulings](../brigade-admin/SKILL.md#rulings) says. The winner fires when `watch` prints `unblocked`. The other side starts no new work on those paths.
- Ownership. When the ruling gives this restaurant's ticket to another coordinator, run `$B ticket move <id> --to <winner>` and send the `ticket` line per Run a service step 2.
- Shares. `fire` refuses a claim past this restaurant's share. Nothing else changes.
- Queue order. `land` holds the second holder's entries until an entry of the first holder lands. Nothing else changes.

When this restaurant disagrees, it complies first, then sends `appeal`. When compliance would be irreversible, such as dropping work or deleting a branch, it holds and sends `appeal`. What the admin does with an appeal is in [Rulings](../brigade-admin/SKILL.md#rulings).

**Contests.** When this restaurant's item and another coordinator's would break or conflict with each other in the queue, or when this restaurant's passed item depends on another coordinator's item that has not landed, run `$L contest --holders <restaurant>/D<n>,<sibling>/D<n> --owner <restaurant>/@<generation>`, such as `--holders docs/D4,core/D2 --owner docs/@1`. Each holder is the one `submit` takes: the restaurant's directory name, a slash, and the dish id. Read the other holder from `$L lease list`. It prints `C<n>`, and `land` holds both holders' entries until the admin settles it. Then send the `contest` line, naming which item depends on which. A contest also holds entries submitted after it, so open it before the dependent dish's `submit` and `land`, then submit per Run a service step 7. The coordinator opens the contest because it knows the dependency. How the admin orders the two, and when it opens a contest itself, is in [Rulings](../brigade-admin/SKILL.md#rulings). A refusal that says `there is nothing left to order` means one side already landed or is landing. Send nothing.

**What the user hears.** While `reportsTo` is set, reply to the user only in a service the user started. Every other wake ends with no assistant text at all, not even a one-line status, as [Step 9](#step-9) says. This restaurant's reporting level decides only the reply for a failed send of a line with a store row. A failed send of a message-only line gets a reply at every level. `$B close --to-file` still writes each report into this restaurant's store. Once `reportsTo` is cleared, reply at this restaurant's own level again.

## Step 9

Send each event line per [Reporting to an executive admin](SKILL.md#reporting-to-an-executive-admin). A send-back, a merge, or a failure is an event line to the admin and not a reply. For the scheduled evening report, run `$B close --to-file` and send the `report` line with that path. When the batch drains, as defined below, run `$B close --to-file` and send the `drained` line. Neither is a reply to the user. Reply to the user only in a service the user started, or when a send to the admin fails. Every other wake ends the turn with no assistant text at all, whatever this restaurant's level. That covers a schedule, a liveness check, a worker's report-back, a verifier's completion, a pull-request wake, and a request from the admin. The `milestones` allowance of a single line when the host requires text does not apply. T3 requires no closing text, and a one-line status is still a reply the user reads. A failed send of a line with a store row gets a reply at this restaurant's own level, per the rest of this step. A failed send of a message-only line gets a reply at every level, because nothing else carries it. Without a `reports to` line, read `reporting` from `restaurant.json`. A missing field means `milestones`. Send the scheduled evening report at every level. The 09:00 morning service is not a report. A batch has drained when `$B status` omits `in progress`, `in review`, `passed review`, and `waiting to land`. Status omits a count of zero, so a missing label is a count of zero. `waiting to land` is work queued to land, including a pull request that awaits merge. Send the drain summary on the wake that first finds the batch drained. A later wake that still sees the batch drained does not send that summary again. A service the user started is a turn begun by a user message. A worker report, a verifier completion, a schedule, or a pull-request wake is not a service the user started. A message from the user gets at least a one-line acknowledgment at every level. A direct question gets an answer. Each level in Run a service step 9 adds the replies it names. It does not drop the evening report, the user message, or the direct question.

## Contests

When the dish depends on another coordinator's item that has not landed and `$B status` prints `reports to <thread>`, never hold it outside the queue. First open a contest per **Contests** above and send the `contest` line. The contest holds this dish's entry until the admin orders the two.

## Liveness

When `$B status` prints `reports to <thread>`, end the turn with no assistant text at all, whatever the level.

When `$B status` prints `reports to <thread>` and a ticket's first `blocked` row in `log.tsv` is more than an hour old, send the `blocked` line per [Reporting to an executive admin](SKILL.md#reporting-to-an-executive-admin).
