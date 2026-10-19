# Pull request linking and watching

Read this file before linking a PR or waiting on its checks.

## Pull requests

After you open a PR or start driving an existing one, call `link_pull_request` with its full URL. For a stack, link every layer. Linking attaches the PR to the calling thread. When a child opens a PR, it links it and the parent links it too. When a launched thread opens a PR, it links it and the launcher or coordinator links it too. Linking twice is safe. Before finishing PR work, call `list_thread_pull_requests` and link any missing PR. Report a link failure instead of claiming the PR is linked.

- On T3 Code 0.1.1-nightly.20261019.3360 or later, prefer the optional `pullRequests` array from `t3_thread_read` or `t3_thread_list` when reconstructing an owner's PR set. Treat a null state as unknown, not closed. A legacy `linkedPullRequest` can be null while the array is nonempty. Keep the final own-thread `list_thread_pull_requests` check. On T3 Code 0.1.0, call `list_thread_pull_requests` with the explicit owner `threadId`. When that read is refused, use recorded URLs. See [Host version](host.md#host-version) for the version read.

## Pull request watching

An owner that may already be settled reads [Settle on turn end](host.md#settle-on-turn-end) before calling `watch_pull_request`.

Call `watch_pull_request` after `link_pull_request`, when this thread is waiting on that PR's checks, reviews, or conflicts. Pass the PR URL, or the repository and number. T3 links the PR first if this thread has not linked it yet.

T3 checks the open PR every two minutes and wakes this thread when checks, reviews, or conflicts need it. Only comments posted after the call wake you, so handle the comments already on the PR, then end the turn.

One thread may hold several watches. Comments from the user's own account do not wake a watch, so a review left from that account needs another wake or the heartbeat. T3 documents both in [source control](https://github.com/pingdotgg/t3code/blob/main/docs/user/source-control.md).

A wake is news, not a merge decision. Read the PR and decide yourself before you merge.

A subagent cannot watch. The parent thread owns the PR. The child finishes and reports back. The thread that owns the PR calls `watch_pull_request`.

Call `unwatch_pull_request` when this thread stops driving the PR and hands that work back to the user. An interim status reply keeps the watch. The PR stays linked. While T3 watches, the thread stays in the user's Working list. Unwatching returns the thread to their inbox.

A merge ends the watch and does not wake the thread. A close ends the watch and does wake the thread. Watching also ends when the thread settles or is archived, when the user stops the thread, or when you call `unwatch_pull_request`. It also ends when T3 fails to read the PR 8 times in a row. A host rate limit only delays the next read. A watch ends after 10 wakes in a row that bring only comments, and that end posts a wake. Call `watch_pull_request` again after that wake when the loop is still running.

Never settle an owner whose PR watch or `schedule_task` loop is still needed, because settling ends the watch.

pstack's `scripts/watch-pr` poll, a foreground `--watch`, and an interval tick that waits for CI, a review, or a conflict all become this call. The forge commands that classify a verdict stay. Run them after a wake. They are not the wait.

`schedule_task` stays for a cadence with no PR event, such as an hourly audit, a morning report, or a soak. Beside a watch, a fallback heartbeat uses `everyMs` of at least `3600000`. A landing contract may set a longer interval. The landing skill reads it from land.py status. When the wait is for the merge itself, add a `schedule_task` heartbeat beside the watch. That heartbeat is required. A merge never wakes the thread. The heartbeat is how you learn the PR merged. The required heartbeat may use `everyMs` `900000`. Use `900000` while a landing entry awaits merge in human mode, while the PR sits behind a merge queue, and for any other wait whose predicate is the merge. A landing contract may set a longer interval. The landing skill reads it from land.py status. In merge mode with required checks and no merge queue, the landing drain uses the fallback interval from `land.py status`, or `3600000` when status names none. The required-checks wake runs `land`. The fallback heartbeat covers a merge that finishes after that run and a PR that never posts a check.
