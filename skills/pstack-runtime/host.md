# Host notes

Read this file when a thread settles, snoozes, or runs on a host older than the versions this file names, or before you install or locate skills.

## Host version

- Read `t3_environment_read` and inspect `serverVersion` before you rely on a nightly rule in this file. When the read is absent or ambiguous, use the stable 0.1.0 behavior. On T3 Code 0.1.0, use the stable fallback beside each nightly rule. On T3 Code 0.1.1-nightly.20261019.3360 or later, use the nightly rule beside it.

## Settle on turn end

- T3 runs the first effective project action with `runOnSettle: true` each time a thread settles in its own worktree, including auto-settlement. A thread in the main checkout skips it. The effective list is the project's override, else the environment defaults. Actions in a repository `t3.json` count only once imported into project settings, and either list can replace them. `t3_project_read` returns the project's saved `scripts`, which can differ from the effective list. Before you settle a worktree thread, read T3's `settings.json` (`~/.t3/userdata/settings.json` by default). Take `projectSettingsOverrides.<projectId>.defaultProjectScripts` when present, else `defaultProjectScripts`, and name its first `runOnSettle: true` action or none. When `projectSettingsFolded` is not `true`, older saved lists still count, so treat the action as unknown. The action can run beside another terminal command. Its terminal closes on success and stays open on failure. It is a cleanup hook, not a wake, so never schedule a tick to watch for it.

If the thread is settled, call `t3_thread_organize` with `action: "unsettle"` (no `threadId`, acting on the caller on purpose) first. A settled thread's new watch ends on the next pass and posts no wake. A pinned thread does not auto-settle. A pinned coordinator runs that action only when someone settled the thread by hand. `t3_thread_organize` with `action: "settle"` and no `threadId` settles this thread when the turn completes, and returns `settlesWhenTurnEnds: true`. A call that targets another thread follows the `threadId` rule in [History](SKILL.md#history). That return is an accepted request, not a settled thread. A failed or interrupted turn, or a queued message, leaves the thread active, and a T3 restart before the turn ends drops the request. Settle this thread only as the last call of a turn whose work is done.

## Older hosts

- An older host, or a child that needs a chosen branch or a different base, uses a worktree the parent creates before delegating with `git worktree add <path> -b <branch> <base>`. Omit `isolation`. The child's default working directory stays in the parent's checkout. The brief says "Work only in `<absolute worktree path>`. Use absolute paths under it for every read and edit, and prefix every command with `cd <absolute worktree path> &&`. Report the branch and head SHA." Check that the diff landed there and remove that worktree after integrating. On T3 Code 0.1.1-nightly.20261019.3360 or later, `delegate_task` with `isolation` `worktree` returns `invalid_request` when the parent checkout is outside the project repository. Bind the project to the actual repository before you use automatic isolation. When the checkout is foreign, use the manual worktree in this section. Never use shared concurrent writing as a fallback. Two writers never share a checkout on any host. On T3 Code 0.1.0, run the same binding preflight and use the manual worktree in this section. A long-lived independent owner uses `t3_thread_launch` with a worktree strategy only where [Top-level threads](SKILL.md#top-level-threads) allows it.

### Pre-0.1.0 nightlies

- On T3 Code 0.0.46-nightly.20261015.3145 or later, a child's question for the user does wake this thread, per [Pending requests](SKILL.md#pending-requests).
- On T3 Code 0.0.46-nightly.20261015.3145 or later, `hasPendingQuestion` is `true` while a question blocks the child.
- On T3 Code 0.0.46-nightly.20261015.3145 or later, a question that blocks the child also ends the `mode: "wait"` call, with `hasPendingQuestion: true` and `waitTimedOut: false`.
- On T3 Code 0.0.46-nightly.20261008.2813 or later, a turn held in a stopped queue does not count toward `hasPendingChildRuns` on `task_status`, and startup recovery on that host repairs a task an older build left `working` behind a held queue.
- On T3 Code 0.0.46-nightly.20261015.3145 or later, `task_status` also shows `hasPendingQuestion: true` while a question blocks the child.
- On T3 Code 0.0.46-nightly.20261014.3093 or later, a writing child that may overlap with another writer uses `delegate_task` with `isolation: "worktree"`. T3 branches from the parent's current local commit and runs project setup. Blocking setup finishes before the child starts. An async setup action can keep running. Uncommitted edits are not copied. The brief tells the child to report its branch and head SHA. Read the child's binding with `t3_thread_read` and check its diff before integrating.
- On T3 Code 0.0.46-nightly.20261014.3105 or later, new file-change items keep only a bounded prefix of their edit text.
- On T3 Code 0.0.46-nightly.20261015.3145 or later, a delegated child's question wakes this thread.

## Root setup

- On T3 Code 0.1.1-nightly.20261019.3360 or later, a root launch runs project setup only with a worktree path. On T3 Code 0.1.0, a root launch can run setup actions. Inspect effective actions before you launch a coordinator. Reuse an existing coordinator or stop with a clear explanation when the actions are inappropriate. Never promise that a root launch has no setup side effects.

## History notes

- On T3 Code nightlies 20261012.3010 through 20261013.3042, Claude 5.1 or later can have pre-tool thinking stored as an assistant message, so the messages view can contain working notes as well as replies. On T3 Code nightlies 20261013.3053 through 20261014.3118, new native Claude thinking stays a reasoning item and is absent from the messages view. On T3 Code 0.0.46-nightly.20261014.3132 or later, a progress update that Claude 5.1 or later writes between tool calls is an assistant message. A lone thinking block before a tool call stays a reasoning item. Older threads can still contain assistant messages promoted before the upgrade.
- On T3 Code 0.0.46-nightly.20261015.3145 or later, a thread dropped from another environment arrives as a saved transcript file. Read that file. It holds the history up to the drop and no later messages.

## Snooze

- `t3_thread_list` and `t3_thread_read` report `snoozed` and `snoozedUntil`. Pass `snoozed: true` to `t3_thread_list` to find snoozed owners. `t3_thread_organize` with `action: "snooze"` requires `snoozedUntil`. The tool takes one optional `threadId`, acts on the calling thread when `threadId` is missing, and ignores unknown keys. Every call that targets another thread passes exactly one explicit `threadId`, one call per thread. Omitting `threadId` is only for acting on yourself. A snoozed thread wakes early when it asks for something, fails, or completes. Snooze is a sidebar state. It is not a schedule, a completion, or a settle. Wait with `schedule_task` or `watch_pull_request`, never with `snoozedUntil`.

## Skill locations

T3's `$` picker lists each provider's native skills, except Muse's. Muse still loads its own skills. Run `muse skills` in the terminal to manage them. A skill meant for every provider is written once and linked into each directory.

| Provider | User | Project |
| --- | --- | --- |
| Claude | `~/.claude/skills` (or `$CLAUDE_CONFIG_DIR/skills`) | `.claude/skills` |
| Codex | `~/.agents/skills` | `.agents/skills` |
| Grok | `~/.grok/skills` | `.grok/skills` |
| Cursor | `~/.cursor/skills` | `.cursor/skills`, and it also reads `.agents/skills` and `.claude/skills` |

Every child gets the `t3-code` server. Other MCP servers come from each provider's own configuration, so a child on another provider may lack one. A brief that needs a specific MCP server says so, and the child reports `MCP unavailable` instead of guessing.

## Local state

Private working state that must survive the session but never be committed lives under `${XDG_STATE_HOME:-~/.local/state}/pstack-t3/`. Orchestrate stores go in `orchestrate/<project-slug>/`, private plans in `docs/<project-slug>/`. Children, launched threads, and scheduled runs reach it by the same absolute path from any worktree. Pass that path explicitly in every brief and tick prompt.

Resume notes from Pause safely go to `.pstack/resume/<slug>.md` in the repository, untracked (add `.pstack/` to `.git/info/exclude`), and are also posted as the thread's final message. Rely on the final message, because storage cleanup can delete the file with its worktree.

After a T3 restart, or when you resume work after one, follow [failures.md](failures.md#after-a-restart) before you read a saved child's result, replace a child, or use a saved worktree path. Resume through the thread's binding with `t3_thread_read`, and check that `worktreePath` exists before you use a saved path.

## History

Before you use a saved transcript file from another environment, read [History notes](host.md#history-notes).

Before you read Claude conversation history, read [History notes](host.md#history-notes).

- Find prior work: `t3_thread_search` with a topic, branch, or PR number. It covers the current project.
- Read it: `t3_thread_read` with `view: "messages"` for the conversation, `view: "activity"` for tool activity. Page with `afterPosition`. Read the worker's explicit report and the result its brief required before treating narration as a decision or completion. Use the activity view to check the turn's order, and collect a child task with `task_status` per [Delegation](delegation.md#spawn-and-collect) step 5.
- Page long stored items with `itemId` and `textOffset`. On T3 Code 0.0.46-nightly.20261014.3105 or later, new file-change items keep only a bounded prefix of their edit text, and paging cannot recover the discarded suffix. On older hosts, see [Older hosts](host.md#older-hosts). Read `git diff <base>...<head>` in the worker's checkout for the whole change.
- A thread the user attached as context is readable even outside this project.
- Child tasks are threads too. A `childThreadId` from `delegate_task` is readable with `t3_thread_read`.
- Reconstruct a PR set with [Pull requests](watching.md#pull-requests). That section names the nightly array and the stable fallback.
- `t3_thread_read` returns the thread's `worktreePath` and `branch`. `t3_thread_list` does not, so finding the threads bound to a worktree takes one read per thread. Threads from other projects are not visible.
- Link a thread in a user-facing report as a Markdown link whose text is the thread's title and whose target is `t3-thread://v1/<threadId>`. Copy the `threadId` exactly as a tool returned it. Do not URL-encode it, decode its `%` escapes, or add an environment ID. T3 Code shows the thread's current title. List, read, and launch results carry no `link` field, so build the link from `threadId` or `childThreadId`.

## Pending requests

A child or launched thread can stall on a question for the user. `t3_pending_request_list` shows those and `t3_pending_request_read` shows one. Pass the child's `childThreadId`, or the launched thread's `threadId`, as `threadId` on `t3_pending_request_list`, `t3_pending_request_read`, and `t3_pending_request_respond`. Without `threadId` each tool acts on this thread. Answer with `t3_pending_request_respond` only from the brief and the facts and decisions the user already gave. Otherwise ask the user with the host's question tool, then answer. A playbook that parks decisions parks the question instead.

On older hosts, see [Older hosts](host.md#older-hosts). On T3 Code 0.0.46-nightly.20261015.3145 or later, the wake is a message that names the child's thread and the request ID. On T3 Code 0.0.46-nightly.20261015.3145 or later, a question that blocks the child also ends a `mode: "wait"` call, per [Older hosts](host.md#older-hosts). A Codex child can ask without stopping, and its wake says it is still working. When the question no longer shows in `t3_pending_request_list`, someone answered or dismissed it, so send nothing. A task in `result_available` that completed or failed keeps its child's open questions. Do not answer one. Answering such a question sends a message to the child's thread and does not reopen the task. Give the answer to a fresh child per [Fresh children by default](SKILL.md#fresh-children-by-default).

Approval (permission) requests are not listed and cannot be answered by these tools. A child stalled on one looks idle in `task_status`. Avoid the stall: children inherit this thread's runtime mode, so a child that must run commands or edit needs a parent in a mode that allows it. If a child stalls anyway, tell the user which thread is waiting for approval. Never raise a child's `runtimeMode` to get around it.
