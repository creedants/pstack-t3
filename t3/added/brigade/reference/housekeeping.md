# Housekeeping

Read this after a merge, drop, send-back, or bounce, and on the first service. The core keeps short pointers. This file holds Git and PR work, House rules, and First service schedules.


## Git and PR housekeeping

The head chef owns every git and PR chore its work creates. In `merge` and `push` mode the user has no step. In `human` mode the user merges each PR. In `local` mode the user merges `refs/landing/<trunk>` into a branch.

- Write each PR's title and body through `submit`, and link every PR with `link_pull_request`.
- Keep the landing drain schedule while anything is queued or awaiting merge, per the [landing skill](../../landing/SKILL.md#keep-the-queue-moving). After the queue opens a PR, also watch it as that section says. Keep that watch across reports while the entry is queued or awaiting merge. When you delete the schedule, run `$B set --schedule drain=`. Call `unwatch_pull_request` only when this thread stops driving that PR.
- Finish with the old worker when a dish merges, is dropped, is sent back, bounces, or is replaced after a contested report. The old worker is the thread the dish row names before any `$B dish <id> --thread` records a replacement. Name that thread id in every call below, so the replacement's open run is never touched. Read it with `t3_thread_read`. If its run is still open after the report-back, run step 1 of the three drop steps in [Run a service](../SKILL.md#run-a-service) step 7, call `$B hang <id> --provider <provider> --minutes <n>` with the provider and the minutes that read showed, then run step 2 and read the worker's activity again. Archive with `t3_thread_organize`, `action: "archive"`, and that `threadId` only after the worker's run is terminal and its child work is no longer pending. When the wait times out or child work remains, keep the thread and finish cleanup on a later service. A worker that an earlier attempt launched counts too. Check each attempt's thread, and for a run a replaced attempt left open, add `--attempt <n>`, counting attempts from 1 in launch order. If the run has already closed, archive it only when its child work is no longer pending. When that child work remains, keep the thread and finish cleanup on a later service. On a send-back or a bounce, finish before `$B dish <id> --state in-progress`. Entering in progress starts a new attempt, and `hang` then refuses with `brigade: no report-back on this attempt`. At `digest` reading, interrupting, logging a hang, and archiving send no reply of their own. A merge that drains the batch still sends the drain summary.
- After a dish merges, is dropped, is sent back, or is replaced after a contested report, remove the old worker's worktree, delete its branch locally and on the remote, and delete the queue's `landing/e<n>` branch, including a leftover `landing/q<n>`, once its PR merged or closed. After a send-back or a contested replacement, delete the old branch only once the fresh worker is launched, because its branch starts from the old head.
- After a landing, when the user's checkout at the project root is clean and on trunk, fast-forward it with `git merge --ff-only`. Otherwise leave it alone.
- A bounce or a conflict is a dish for a fresh worker, never a manual rebase.
- Never force-push trunk, rewrite published history, change branch protection, or delete a branch this restaurant did not create.


## House rules

`house-rules.md` holds the rules a worker brief pastes. `open` writes the file once, from a template, and never rewrites it. The template holds two project rules, the worker size limit, and the test standard. A store may edit or delete the last two.

A store created before the template held them gains them only when the user pastes them into its `house-rules.md`. Paste this text at the end of the file:

- You may write the change yourself when it changes one file outside tests, the added and removed lines outside tests are about 40 or fewer across the whole item, and the contract does not call its guarantee safety, privacy, or integrity. Count those lines from `git diff --numstat` against the item's base commit, quote that output in the report, and treat every round and commit as one diff. Past that size, or more than one file outside tests, the code delegate writes the change. The claims check and the gate review are never skipped.

1. A test earns its place when it fails for a defect that no other check reports; name that defect in the test name.
2. A test that pins a reviewed defect stays; remove it only with a mutation that shows another check fails for the same change.
3. Use a rule in the project's static check, when it has one, when the property is visible in the text or the tree without running the code, with one passing and one failing fixture test.
4. Use a test when the property is behavior: exit codes, written files, ordering, refusals.
5. Call the command in the test process through the project's in-process test helper, when it has one; start a process only for a race, a signal, exit status, the environment, or help output, and say which.
6. Never assert a library's own wording.
7. Add a case to an existing table when the setup is the same.
8. A test that sleeps to order two processes names the review finding it pins.
9. Do not add a test for what the project's build already refuses.
10. The reviewer asks of each new test: which change to the code makes this fail and nothing else fail?


## First service

A head chef's first wake runs [First service](../opening.md#first-service) in opening.md, then Run a service.

   - While this restaurant has dishes queued, keep a landing drain schedule per the [landing skill](../../landing/SKILL.md#keep-the-queue-moving), and delete it when none are. Name the reporting level in that prompt, and say to follow Run a service step 9. After the queue opens a PR, that section has this thread call `watch_pull_request` and run `land` on each wake. Other restaurants' drains on the same repository are harmless. The queue lock runs one at a time. When you delete that schedule, run `$B set --schedule drain=`.
   - While `$B watch` prints anything other than "no work in progress", and on the first refused `fire`, keep a liveness schedule. `watch` renews every live lease, so this schedule is what keeps a lease from expiring. Take `everyMs` from the `liveness:` line of `$B status` every time you create or recreate the schedule, so the new schedule uses the store's stored interval, or the reporting level's default when none is stored: `{"type": "interval", "everyMs": <that number>}`. The prompt is "Use the brigade skill. You are the head chef for the restaurant at `<restaurant dir>`. Reporting level: `<level>`. Follow Run a service step 9. Run the liveness check." Delete it when `$B watch` prints "no work in progress". When you delete that schedule, run `$B set --schedule liveness=`. After `$B set --reporting` moves the level to or from `digest`, read `$B status` again and, when the number on its `liveness:` line changed, delete the running liveness schedule, create it again at that number, and record the new id with `$B set --schedule liveness=<id>`.
   - Record each ID with `$B set --schedule <name>=<id>`. When the landing drain schedule is recreated, record the new id with `$B set --schedule drain=<id>`. Report schedules only in a reply Run a service step 9 already allows. At `digest`, creating, recreating, or deleting a schedule is not a reply occasion. The IDs stay in `restaurant.json`, and `list_scheduled_tasks` shows each cadence and `nextRunAt`. A first service started by opening the restaurant, and a reply to a user's level change, may name the schedules within Digest messages. This overrides the runtime's "Report the returned cadence and `nextRunAt`" for a head chef at `digest`.
   - After an authorized seat change, follow [Scheduling](../../pstack-runtime/threads.md#scheduling) for the liveness and drain schedules, and record each replacement id.
