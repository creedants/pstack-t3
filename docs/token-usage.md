# Token usage sources

This page records which token counts to trust for what a piece of work cost. A study run on 2026-10-10 and 2026-10-11 UTC compared T3 Code's stored counts with Claude's and Codex's session logs and OpenCode's message database on one machine. T3 Code was 0.0.46-nightly.20261010.2922. The study attempted joins for all 902 finished Claude and 436 finished Codex provider threads in T3's state database, and for 108 OpenCode provider threads. The counts below came from read-only scripts over that database and those logs. The scripts are not in this repository.

## The two objects on a provider turn

T3 Code defines two optional token objects in `payload_json` on rows of `orchestration_v2_projection_provider_turns`. A row can hold either, both, or neither.

| Object | What it held | Summed across turns |
| --- | --- | --- |
| `tokenUsage` | The prompt size of the turn's last model call. T3's schema comment calls it live context usage. | Not a spend total |
| `turnTokenUsage` | The main agent's reported tokens for the turn. `usageStatus` can be `complete`, `partial`, or `unavailable`. | Use as a spend total only when usage is complete, with the Claude gaps below |

In `turnTokenUsage`, `inputTokens` includes cache reads and cache writes, and `outputTokens` includes reasoning tokens. `cachedInputTokens`, `cacheCreationTokens`, and `reasoningTokens` are parts of those two totals.

A turn held more than one model call in 2,332 of 2,488 Claude turns and 421 of 428 Codex turns. For the turns started on 2026-10-10 UTC, `tokenUsage.inputTokens` summed to 245 million and `turnTokenUsage.inputTokens` summed to 2,184 million. This repository's brigade coordinator thread held 74.4 percent of the first sum and 31.3 percent of the second.

On Claude, `tokenUsage.outputTokens` was below the last call's logged output in 1,057 of 2,488 turns, and `reasoningOutputTokens` was 0 in all of them.

## How `turnTokenUsage` compared with the provider's log

| Provider | Result |
| --- | --- |
| Codex | Equal to the log on input, cached input, output, and reasoning in 428 of 428 completed turns |
| OpenCode | Equal to the sum of OpenCode's message rows in 91 of 91 threads whose turns all had complete usage |
| Claude | Equal to the log on input and output in 2,418 of 2,494 turns with complete usage |
| Cursor, Grok | T3 stored neither object on any of 229 Cursor turns and 579 Grok turns |

Summed over 899 finished Claude threads, `turnTokenUsage.inputTokens` was 5.57 percent below the log. Three causes made up 5.56 points of it.

- A message delivered into a running turn. T3 then held only the calls after that message. This was 3.79 percent of the log's input tokens, in 61 turns and in one more turn after a compaction.
- A turn that was interrupted, failed, or cancelled. T3 held no complete count for it. This was 1.06 percent.
- Claude's own subagents. `usageScope` is `main_agent`, and their calls are not in the count. This was 0.71 percent.

## What a spend command reads

| Provider | Source | Join from a T3 thread |
| --- | --- | --- |
| Claude | Claude Code's session log, one `<session id>.jsonl` per session and a `<session id>/subagents` directory beside it. Count each `message.id` once. | `nativeThreadRef.nativeId` on the provider thread row is the session id and the file name |
| Codex | `turnTokenUsage` for turns with complete usage, or the rollout file's `token_usage_record` lines. Flag missing log usage as incomplete. | `nativeThreadRef.nativeId` is the Codex thread id. `nativeTurnRef.nativeId` is the turn id. |
| OpenCode | `turnTokenUsage` for turns with complete usage, or the `session_message` rows of OpenCode's database. Rows with usage can also hold `cost`. Flag missing message usage as incomplete. | `nativeThreadRef.nativeId` is the OpenCode session id |
| Cursor, Grok | This study measured no source for them | Not measured |

Join by provider thread, not by T3 thread. Two of 2,142 T3 threads had two provider threads, on two drivers. No native id belonged to two provider threads.

The join found a log for 899 of 902 finished Claude threads, 431 of 436 Codex threads, and 108 of 108 OpenCode threads. The eight threads without a log had no complete usage in T3 either. Seven of 922 Claude log sessions and 20 of 452 Codex log threads matched no T3 thread.

## OpenCode's cost field

In the measured OpenCode database, 13 assistant messages held neither `tokens` nor `cost`. Messages with recorded usage held `cost` in dollars. For `opencode-go/deepseek-v4.1-flash` it matched the message's tokens times the prices in OpenCode's local model cache within 0.01 percent on 316 of 316 messages, with reasoning tokens priced as output. Messages on the free models recorded a cost of 0. T3 reads OpenCode Go's usage endpoint as a percent for a rolling, a weekly, and a monthly window, so the two are in different units.

## Cache reads and the Claude subscription

Claude Code's cost page says a long session re-reads its history "at the cached token rate" and "still draws usage for the whole conversation". Anthropic's prompt caching page prices a cache read on Opus 5.5 at 0.05 times base input and a one-hour cache write at 2 times.

T3's provider logs held 1,097 readings of the five-hour utilization between 06:49 UTC on 2026-10-10 and 00:01 UTC the next day. Four fits of the change in that reading against the logged tokens gave 0.046 to 0.062 percent of the five-hour limit per million cache-read tokens, with an Opus 5.5 token counted as 1 in all four. In the fit through zero over 31 half-hour bins, output, one-hour cache writes, and cache reads weighted 5, 2, and 0.05 times input gave uncentered R2 0.993. The same fit with cache reads at 1 times input gave uncentered R2 0.950. In that fit the output weight and the scale for the other models came from the prices in T3's rates file.

## Not known

- The formula Anthropic uses for the five-hour and weekly limits. The pages read name no unit for those limits and give no formula.
- Whether the fit holds on another day, plan, or model mix. It covers one day on one account, and usage from another device would not be in the logs.
- Whether OpenCode Go's percent moves in step with the summed `cost`. The study did not read the endpoint.
