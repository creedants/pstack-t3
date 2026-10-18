Part of [the guide](../guide.md).

### Landing modes

| Mode | What reaches trunk | Are you a gate? |
| --- | --- | --- |
| `merge` | The queue rebases each entry onto the last candidate of [the line](../../skills/landing/SKILL.md#the-line), or onto trunk when the line is empty, runs the checks, pushes `landing/e<n>`, and opens a PR. It merges the first PR of the line when the check read allows it and trunk holds the tree the candidate was built on. | No. You review what landed. A required approving review pauses the queue until you relax that rule or switch to `human`. |
| `human` | The queue rebases each entry onto trunk, runs the checks, pushes `landing/e<n>`, and opens a PR that it leaves open. The entry counts as landed when someone merges that checked head. | Yes. You merge every PR. |
| `push` | Rebased commits that passed the checks, pushed to trunk with `git push --force-with-lease` only while the remote is still at the tested base. There is no PR. | No. You review the history afterward. |
| `local` | Nothing on the real trunk. The queue moves `refs/landing/<trunk>`. The remote does not change. | Yes, for the real trunk. You merge that ref into a branch when you are ready. |

In `merge` mode, `land` reads posted checks before any `gh pr merge`, including `--auto`. A pending posted check waits. A failed posted check bounces the first entry of the line. The first `land` run that sees no posted checks records that run and waits. A later `land` merges when checks are still absent. With no posted check, the entry waits, and the queue does not pause when `gh pr merge` is refused because the base branch policy prohibits the merge. A plain merge that fails for any other reason pauses the queue, including when `--auto` reports that auto-merge is disabled. A failed or unreadable check read pauses the queue and does not merge. A later `land` reads the PR state before it acts on a failed check. A PR already merged at the candidate head is landed in that same run once git shows trunk holds its merge commit, the tree comparison is made, and its queue branch is gone, even when a check fails afterward. An open PR noted `merge requested by the queue` bounces when a check fails, and the lease becomes active. After the PR merges, `land` deletes remote `landing/e<n>`, and the entry lands when the server accepts the delete. When the delete fails, `land` asks the forge whether the branch is gone. HTTP 404 lands the entry only when the contract remote has one push URL and that URL names the same owner and repository that `gh repo view --json nameWithOwner` resolves. Where the forge cannot be asked, a failed delete lands the entry only when git says `landing/e<n>` does not exist and `gh pr view` shows the PR was opened from another branch. Any other result leaves the entry for the next run. `land` also deletes a local `landing/e<n>` when git can. When that local delete fails, the entry still counts as landed and `land` prints the branch it left. [Pull request lifecycle](../../skills/landing/SKILL.md#pull-request-lifecycle) in the landing skill states each of these rules once.

`push` is the name to use. `auto` is an older alias of `push`.

[`land.py mode`](../cli/land.md#landpy-mode) switches among `merge`, `human`, and `push` only while nothing is queued, landing, or awaiting merge. A move to or from `local` needs a new contract. [How work lands](../how-it-works.md#how-work-lands) describes the queue those modes share.

### Reporting levels

The level is stored for that coordinator. `brigade.py open --reporting` sets it, and `brigade.py set --reporting` changes it later. Opening an existing coordinator does not change its level. The choices and the default are in the reference for [`open`](../cli/brigade.md#brigadepy-open) and [`set`](../cli/brigade.md#brigadepy-set).

| Level | What the coordinator sends |
| --- | --- |
| `every-turn` | A short reply after every wake. |
| `milestones` | A reply when work merges, a review sends work back or blocks it, a decision needs you, something fails or the queue pauses, or you send a message. A routine wake ends with no reply, or with one line when the host requires text. A liveness check with nothing new, a liveness check while a review is pending, a review starting, and a worker launching are routine. |
| `digest` | A reply only for a decision you must make, a failure or a paused queue the coordinator cannot fix itself, one summary when a batch drains, the 18:00 report, and a message from you. Every other wake ends with no reply text at all, not even a status line. That includes a worker reporting back, a review starting or finishing, a send-back, a queue bounce, a worker launching or being replaced, a merge that does not drain the batch, a pull request wake, a landing run that neither drains the batch nor pauses the queue, a liveness check, intake with nothing new, and a schedule created, recreated, or deleted. |

A batch has drained when `brigade.py status` shows none of `in progress`, `in review`, `passed review`, or `waiting to land`. Status omits a count of zero, so a missing label is a count of zero. `waiting to land` includes a pull request that awaits merge.

Every level sends the scheduled 18:00 report. The 09:00 run is not a report. A message from you gets at least one line, and a direct question gets an answer. [How a coordinator reports](../how-it-works.md#how-a-coordinator-reports) is the same rule from the coordinator's side.

`brigade.py close` runs on the 18:00 report, on a reply that raises a decision for you, at the end of a turn begun by your message, and on the reply that closes the coordinator. At `digest` every reply runs it once, including the summary sent when a batch drains and a reply about a failure. At `every-turn` and `milestones` every other reply stays plain and does not run `close`. `close` writes that report and records its time. At `every-turn` and `milestones` the reply pastes the `close` output. At `digest` the coordinator runs `brigade.py close --to-file`, which writes the same report and prints only its path.

A `digest` message is written for you, not for an engineer. It is a few plain sentences on what got done and what it means, what comes next, and anything you must decide. It carries no work or ticket IDs, SHAs, file paths, review-round counts, or tool names. Merged pull requests may follow as a short list of plain titles linked to each PR. Commits, IDs, and schedule times stay in the store and the full report. The last line names the path of the full report. The next report starts from that time, so it omits what this run already listed.

Ask a coordinator what its agents are doing and it shows the activity page. [`activity.py`](../cli/activity.md) builds that page from the coordinator's store and T3 Code's state database. The page shows what is running now, what is queued, a timeline of agents and sub-agents grouped by unit, and the units in flight with their state and pull request link. It covers the last 3 hours by default. When the document exceeds its size limit, the page can fold finished rows into summaries or omit rows, and it counts the agents and work items those changes cover. A pull request link is shown only as an `https://host/owner/repository/pull/number` address. A note counts the work items the page shows without their link, for that reason or for size. Every string on the page passes one filter that removes each word it reads as a T3 Code thread or sub-agent id, an absolute or home-relative file path, a `file:` address, or an email address. The [reference](../cli/activity.md) states what it reads as each.

The opener runs [`brigade.py open`](../cli/brigade.md#brigadepy-open) with the project root, a name, and the reporting level. It prints `opened` or `exists`, then the store path `${XDG_STATE_HOME:-~/.local/state}/pstack-t3/brigade/<project-slug>/<name-slug>/`. Each slug is the project directory name or the name you gave, in lowercase. Each run of characters other than a-z and 0-9 becomes one hyphen, and leading or trailing hyphens are dropped.

The store holds these files.

- `menu.md`, with the headings `Purpose`, `What good looks like`, `Off the menu`, and `Budget`.
- `house-rules.md`.
- `restaurant.json`.
- `rail.tsv`, `dishes.tsv`, `pass.tsv`, `86.tsv`, `goals.tsv`, `edits.tsv`, and `log.tsv`, each with a header and no rows.

The opener drafts `menu.md` from the README, AGENTS.md, open work in the repository's tracker, and recent threads, and asks you for the purpose when evidence does not settle it. `brigade.py brief` refuses to write a worker brief while `Purpose` is empty or still the template. The opener appends standing orders to `house-rules.md`. They name forbidden paths, the verification bar, intake sources, and the repository's hot shared files. They do not restate the landing mode, which lives only in the repository's landing contract. The worker cap is `open --workers`, and `brigade.py fire` enforces it.

The opener launches the coordinator with `t3_thread_launch` on the project root. The sidebar title is `Head chef: <name>`. On its first run the coordinator pins that thread. If that thread is in another project, the thread you typed in cannot read it afterward.
