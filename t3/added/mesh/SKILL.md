---
name: mesh
description: Run a finite peer mesh with durable independent T3 conversations, atomic SQLite work claims, direct messaging and reviewed Git integration. Reuse a project's peer contexts when useful. Use when independent units need peer coordination or retained expertise; use swarm for simple finite parallel coverage.
---

# Peer mesh

[The runtime's Modes section](../pstack-runtime/SKILL.md#modes) sets the mode lines of every brief this skill writes and how its spawns run in light mode.

Read [the runtime](../pstack-runtime/SKILL.md), [project](../project/SKILL.md) for a continuing interface, and the task's Poteto playbook. The bundled standard-library runtime lives in `scripts/pstack_mesh`. `scripts/meshctl.py` accepts `board`, `landing`, or `verify`, followed by that component's flags. It runs on native Windows and POSIX.

## Prepare a bounded goal

Use one external board per finite goal, an immutable baseline, explicit file scopes, fixed checks and a peer budget. `project.py start-mesh` prepares these from an accepted request. The task board remains outside all writing worktrees. Give peers the same objective and acceptance criteria. Roles are not permanent. Specialists emerge from work and retained context.

Use the existing pstack landing authority when the repository already has one. Do not run the bundled queue alongside it. This initial portable backend supports a project with its own mesh queue. Integrating mesh claims into an existing pstack queue requires lease admission before writing; absence of that adapter is a blocker, not permission to bypass the existing queue.

Ordinary durable threads retain workspace bindings, PR ownership and unrestricted normal messaging. Launch them with `t3_thread_launch` and the target project's explicit worktree strategy. Do not substitute a delegated task or fork for a durable ordinary peer. For native grouping, pass the project interface as `containerThreadId` only when the server advertises `visualPeerContainers`. App-owned children remain useful for bounded research, implementation and fresh review tasks.

Record each returned thread ID and inspect launch preparation. After an uncertain launch, list threads before retrying. Do not make the peer create an unbound checkout in its own shell. A writing peer must actually be bound to its isolated checkout before claiming work.

## Claim and communicate

1. Discover peers with `t3_thread_list` and the shared registry. Register `id`, actual `thread_id`, workspace and capabilities through the board's `register` command. Preserve identity on later turns.
2. Call `claim` with a 600-second lease and retain its token. Renew before expiry. A stale token cannot finish or renew work. Honor deadline, attempt and message budgets. Eligible tasks require successful dependencies.
3. Reserve every outbound message with the `message` command, using a stable ID and UTF-8 JSON file. In PowerShell quote every `@file` argument. Check exit code and returned reservation ID. A failed reservation means no transport call. Keep reservation, native send, and delivery recording sequential.
4. Call `t3_thread_send` directly to the relevant peer with the reserved ID as `clientRequestId`. Mark `delivered` with the actual returned `messageId`. Retry uncertain delivery with the same ID and body. Do not request acknowledgment loops.
5. Answer factual requests from existing context. Record actual received transport IDs and a concrete fact for a later follow-up. A test of context reuse corroborates the previous answer, follow-up and response against raw transcript messages. Repeated protocol boilerplate is not a project fact.

SQLite readiness alone wakes no model. The interface publishes a bounded wake when a goal starts, and a peer sends a bounded wake when it makes another peer's work eligible. Idle peers end turns. Recurring polling requires the user's scheduling authorization.

## Review and integrate

Verify scoped changes through the real feature, commit exactly one change with the goal baseline as sole parent, and request independent exact-SHA review. Preserve review evidence and limitations when only one provider family can run. Fresh delegated review rounds follow the runtime's rule and receive the entire brief, prior findings and unresolved objections.

The portable `Landing` API submits, reviews and serializes candidates under one repository Git lock. It runs the locally fixed check argv on the combined candidate before advancing the target. Conflicts, failures and superseded commits remain in history. A changed source commit needs fresh approval. Any peer may drive the queue; the interface is not an approval broker.

Task completion and checked integration are separate facts. Run `meshctl.py verify` against refreshed raw T3 exports to test the complete experiment. It preserves messaging protocol deviations as ISSUES, verifies exact source/candidate trees and event order, and reports evidence limits. Terminal thread states and self-reported PASS do not prove acceptance.

For follow-ups, retain the conversation and worktree identity, use a new goal board, and let `project.py prepare` safely advance the idle clean branch while archiving its earlier head. Do not carry expired tokens or frozen baseline settings into the next goal. Scale only after the finite goal's behavior is proved; no hierarchy performance advantage is claimed.
