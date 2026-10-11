---
name: reflect
description: Spawn three parallel review children over the active T3 thread, surface learnings, and route each to a concrete edit on an existing skill. Use when the user says reflect.
---

# Reflect

Read [the pstack-t3 runtime](../pstack-runtime/SKILL.md) before spawning workers, choosing models, scheduling, or isolating work. It maps those steps onto T3's orchestrator tools.

Mine the current conversation for durable learnings, then route them into skill edits.

Invoke when the user says "reflect" or "/reflect". Skip when the conversation is trivial, off-topic, or already covered by an existing skill the parent followed correctly. One-offs are not learnings.

[The runtime's Modes section](../pstack-runtime/SKILL.md#modes) sets the mode lines of every brief this skill writes and how its spawns run in light mode.

## 1. Locate the active thread

Before fanning out, find this conversation's T3 thread, which the reviewers read with `t3_thread_read` (see the runtime's [History section](../pstack-runtime/SKILL.md#history)). Do not read threads from other projects. That reads private chats from unrelated work.

Use the thread id if the host already names it. Otherwise call `t3_thread_list` with `statuses: ["running"]` and take the newest candidates. Child task threads this session spawned are threads too. List them with `t3_thread_list` and `includeSubagents: true` when a reviewer needs a child's work.

For each candidate, call `t3_thread_read` with `view: "messages"` and `limit: 1`. Take the `threadId` of the thread whose first user message is the conversation's opening user prompt. If none matches, pass a tight digest of the session instead.

## 2. Spawn three reviewers in parallel

One message, three `delegate_task` calls with `mode: "async"` and `role: "review"`. Reviewers need MCP access to look up the tickets, chat threads, and observability traces the thread references, so keep the child's tools and let the brief carry the read-only constraint.

Each reviewer and the synthesizer name a role. Set `target` to the target resolved from that role per the [runtime's Roles section](../pstack-runtime/SKILL.md#roles). Omit `target` for an `inherit` seat. If T3 rejects a target, fall back per the runtime and say which seat changed.

| Lens | Role | Prompt template |
|---|---|---|
| Judgment | `reflect judgment, divergent, synthesizer` | `references/judgment-reviewer.md` |
| Tooling | `reflect tooling` | `references/tooling-reviewer.md` |
| Divergent | `reflect judgment, divergent, synthesizer` | `references/divergent-reviewer.md` |

Pass each template verbatim, substituting the thread id or digest where marked. Reviewers return findings in their final message, which arrives as the task's `summary`.

## 3. Synthesize

One `delegate_task` call with `role: "review"` and the target from the `reflect judgment, divergent, synthesizer` role. It spot-verifies citations through MCP, so it keeps its tools and the template carries the read-only constraint. Pass `references/synthesizer.md` verbatim, with each reviewer's full output inlined where marked. It returns an Accepted / Rejected / Backlog list.

## 4. Structural enforcement check

Move any Accepted item that a lint rule, script, metadata flag, or runtime check would enforce more reliably to Backlog. See the **encode-lessons-in-structure** principle skill.

## 5. Apply

Present the synthesizer's full Accepted / Rejected / Backlog output and wait for explicit approval before applying any Accepted edit. The user picks the subset and may redirect routings. Skill changes affect every future agent in the org. Do not auto-apply.

File each Backlog item to your team's devex or backlog tracker without waiting. Only the Accepted list waits for approval.

Follow each approved row's Routing exactly:

- Trivial existing-skill edit (a one-line bullet, a tightened sentence, a stale fact corrected): the parent does it directly.
- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): hand to the [`pstack-author-skill`](../pstack-author-skill/SKILL.md) skill and run its draft / test / iterate loop.
- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): hand to `pstack-author-skill` and run its description loop.
- `new skill via pstack-author-skill: <kebab-name>`: hand creation to `pstack-author-skill`. Do not invent the shape ad hoc.

If your environment ships a SKILL.md validator, run it on every touched skill before declaring done.

## 6. Summarize for the user

Short list, no preamble:

- Edits applied: `<skill path>`. What changed, one line each.
- New skills created: `<skill path>`. One line each (rare).
- Backlog filed to the devex tracker: `<issue title>` (`<tags>`). One line each.
- Dropped: one line per rejected finding + reason from the synthesizer.
