# Replies

Read this before any reply. The core keeps the `reports to` gate and the `digest` pointer. This file holds step 9 levels and Digest messages.


9. **Report.** When `$B status` prints `reports to <thread>`, this restaurant reports through the executive admin, and this paragraph replaces the rest of this step, levels included. Follow [admin-reporting.md](../admin-reporting.md#step-9) in place of the rest of this step. Without a `reports to` line, read `reporting` from `restaurant.json`. A missing field means `milestones`. Send the scheduled evening report at every level. The 09:00 morning service is not a report. A batch has drained when `$B status` omits `in progress`, `in review`, `passed review`, and `waiting to land`. Status omits a count of zero, so a missing label is a count of zero. `waiting to land` is work queued to land, including a pull request that awaits merge. Send the drain summary on the wake that first finds the batch drained. A later wake that still sees the batch drained does not send that summary again. A service the user started is a turn begun by a user message. A worker report, a verifier completion, a schedule, or a pull-request wake is not a service the user started. A message from the user gets at least a one-line acknowledgment at every level. A direct question gets an answer. Each level below adds the replies it names. It does not drop the evening report, the user message, or the direct question.
   - `every-turn`. Send a short reply after every wake. Run `$B close` on the scheduled evening report, a reply that raises a decision for the user, the end of a service the user started, and closing the restaurant.
   - `milestones`. Reply when work merges, when a review sends work back or blocks it, when a decision needs the user, when something fails or the queue pauses, when the user sends a message, or on a liveness or merge wake while the stored trunk is red. A routine wake ends with no reply, or with a single line when the host requires text. A liveness check with nothing new and no red trunk, a liveness check while a review is pending and no red trunk is stored, a review starting, and a worker launching are routine wakes. Run `$B close` on the scheduled evening report, a reply that raises a decision for the user, the end of a service the user started, and closing the restaurant.
   - `digest`. Reply only on these wakes:
     1. A decision the user must make. A blocked verdict that needs the user is a decision.
     2. A failure, or a paused queue, that this thread cannot fix itself. A send-back is not a failure. A queue bounce this thread hands to a fresh worker is not a failure.
     3. One summary when a batch drains.
     4. The scheduled evening report.
     5. A message from the user.
      6. A liveness or merge wake while the stored trunk is red.
     Every other wake ends the turn with no reply text at all. Those wakes include a worker's report-back, a verifier's completion, a send-back, a queue bounce, a worker launching or being replaced, a timebox raised, a review starting, a merge that does not drain the batch and has no red trunk, a `watch_pull_request` wake, a landing drain run that neither drains the batch nor pauses the queue, a liveness check with no red trunk, intake with nothing new or only dropped tickets, a fix dish fired by step 8 or the liveness check, logging a hang for a run left open after its report-back, and creating, recreating, or deleting a schedule. A status line is still a reply. Do not send one on those wakes, even a single line. Every digest reply runs `$B close --to-file` once, including a failure reply and closing the restaurant. Write each reply per Digest messages below.
   At `milestones` and `digest`, a delegated child's question is not a reply occasion by itself. Answer it with `t3_pending_request` and `action: "respond"` only from facts and decisions the user already gave, per [the runtime's Pending requests section](../../pstack-runtime/SKILL.md#pending-requests). When the question is a real decision, per [Operating stance](../SKILL.md#operating-stance), park it with `86 add`, and reply as this level replies to a decision. The `every-turn` level sends a short reply after every wake, so a question wake gets that reply there.
   On the native Claude adapter, a non-empty thinking block from Claude 5.1 or later shows as reply text when it immediately precedes a tool call and directly follows another non-empty thinking block in the same message. A lone thinking block stays reasoning. On a wake that sends no reply, write no progress update between tool calls. Older thread history can hold assistant messages promoted before the upgrade, which [History notes](../../pstack-runtime/host.md#history-notes) records. Keep the no-reply rule and do not write an extra status to explain those paragraphs.
   Run `$B close` only on the replies named for that level above, including closing the restaurant. At `digest` that command is `$B close --to-file`. When one wake qualifies for more than one of those replies, send one reply and run `$B close` once. Close runs once per reply. On a reply that runs `$B close`, answer a direct question first. That answer sits outside the three-sentence cap. At `every-turn` and `milestones`, keep the rest to at most three sentences on what the changes mean for the menu, then the `close` output verbatim. It lists each ticket and dish once, under its latest state since the last report, with PR links. Write no other report file. At those two levels every other reply is plain and does not run `$B close`.


### Digest messages

At `digest`, write every reply for a person who has not followed the work. Use a few plain sentences: what got done and what it means for the menu, what is next, and anything the user must decide with its default. Describe each change by what it does for the user, not by how it was built.

- Leave out dish, ticket, and decision IDs (`D<n>`, `T<n>`, `Q<n>`), SHAs, file paths, branch names, review-round counts, verdicts, and tool or command names.
- Merged pull requests may follow as a short list, one line each, a plain title linked to its PR URL.
- `$B close --to-file` writes the full report under `<restaurant dir>/closeouts/` and prints its path. End the message with one line that names that path, such as "Full report: <path>". That line is the only path in the message. Never paste the `close` output.
- Name the mode only in the first reply after `set --mode` changes it.
- Put commits, IDs, and other engineering detail in the store, not the message. The two commits a pause names, which the [landing skill](../../landing/pauses.md#when-the-queue-pauses) asks for, go in the question text, and the full report carries them.

A drain summary reads like this:

> Startup is about twice as fast, and the settings page no longer freezes when it opens. Both changes are merged. Next I start on the slow search results you reported this morning. Nothing needs your decision.
>
> - [Faster app startup](https://github.com/acme/app/pull/41)
> - [Fix the settings page freeze](https://github.com/acme/app/pull/42)
>
> Full report: ~/.local/state/pstack-t3/brigade/app/perf/closeouts/2026-10-06T230012.481207.md

### Outside ping

When `$B status` ends with `outside ping: on`, the user set a command that `$B` runs by itself, once, with one plain line when `86 add` parks a new decision or `next --failure stuck` runs for a paused queue or a provider failure. It runs at no other time. Send the reply for that wake as usual, because the ping does not replace it. The ping is not a reply, so a routine wake still ends with no reply text. Do not run it by hand. [store-config.md](../store-config.md#notify) says what it carries and how it fails.
