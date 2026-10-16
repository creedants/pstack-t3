# Host notes

Read this file when a thread settles, snoozes, or runs on a host older than the versions this file names, or before you install or locate skills.

## Settle on turn end

- T3 runs the first effective project action with `runOnSettle: true` each time a thread settles in its own worktree, including auto-settlement. A thread in the main checkout skips it. The effective list is the project's override, else the environment defaults. Actions in a repository `t3.json` count only once imported into project settings, and either list can replace them. `t3_project_read` returns the project's saved `scripts`, which can differ from the effective list. Before you settle a worktree thread, read T3's `settings.json` (`~/.t3/userdata/settings.json` by default). Take `projectSettingsOverrides.<projectId>.defaultProjectScripts` when present, else `defaultProjectScripts`, and name its first `runOnSettle: true` action or none. When `projectSettingsFolded` is not `true`, older saved lists still count, so treat the action as unknown. The action can run beside another terminal command. Its terminal closes on success and stays open on failure. It is a cleanup hook, not a wake, so never schedule a tick to watch for it.

If the thread is settled, call `t3_thread_organize` with `action: "unsettle"` (no `threadId`, acting on the caller on purpose) first. A settled thread's new watch ends on the next pass and posts no wake. A pinned thread does not auto-settle. A pinned coordinator runs that action only when someone settled the thread by hand. `t3_thread_organize` with `action: "settle"` and no `threadId` settles this thread when the turn completes, and returns `settlesWhenTurnEnds: true`. A call that targets another thread follows the `threadId` rule in [History](SKILL.md#history). That return is an accepted request, not a settled thread. A failed or interrupted turn, or a queued message, leaves the thread active, and a T3 restart before the turn ends drops the request. Settle this thread only as the last call of a turn whose work is done.

## Older hosts

- An older host, or a child that needs a chosen branch or a different base, uses a worktree the parent creates before delegating with `git worktree add <path> -b <branch> <base>`. Omit `isolation`. The child's default working directory stays in the parent's checkout. The brief says "Work only in `<absolute worktree path>`. Use absolute paths under it for every read and edit, and prefix every command with `cd <absolute worktree path> &&`. Report the branch and head SHA." Check that the diff landed there and remove that worktree after integrating. A long-lived independent owner uses `t3_thread_launch` with a worktree strategy only where [Top-level threads](SKILL.md#top-level-threads) allows it.

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
