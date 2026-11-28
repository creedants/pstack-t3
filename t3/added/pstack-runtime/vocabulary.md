# Vocabulary

Read this file when a pstack skill names a Cursor mechanism and you need its T3 equivalent.

| pstack says | In T3 |
| --- | --- |
| subagent, worker, delegate, reviewer, runner, judge | A child task created with `delegate_task`, owned by this thread. |
| cloud worker | A child task. T3 children run on this machine, so they can reach local files, browsers, and auth. |
| background, `run_in_background` | `delegate_task` with `mode: "async"`. |
| wait for a worker | A thread with a schedule of its own ends the turn, and its completions wake it. Any other thread keeps a bounded wait with `t3_thread_wait`. See [waiting.md](waiting.md). |
| cancel a worker | `task_cancel`. |
| model slug, role model | A target `{providerInstanceId, model, options}` from `orchestrator_capabilities`, resolved through roles. See [Roles](SKILL.md#roles). |
| `inherit-parent`, `auto` | `"inherit"`. Omit `target` so the child inherits this thread's provider, model, and options. |
| separate chat, coordinator chat, PR owner thread | A top-level thread from `t3_thread_launch`, only where [Top-level threads](SKILL.md#top-level-threads) allows it. |
| worktree for a worker | A worktree from `delegate_task` with `isolation: "worktree"`, or a `t3_thread_launch` worktree binding for top-level threads. See [Isolation](SKILL.md#isolation). |
| `/loop`, hourly tick, automation, scheduled wakeup | `schedule_task`. See [Scheduling](SKILL.md#scheduling). |
| `scripts/watch-pr`, a poll loop, waiting on CI | `watch_pull_request` on the thread that owns the PR. See [Pull request watching](SKILL.md#pull-request-watching). |
| transcript, chat history, cloud-agent URL | A T3 thread, read with `t3_thread_search` and `t3_thread_read`. |
| control-ui, browser MCP | T3 preview tools: `preview_open`, `preview_snapshot`, `preview_act`, `preview_evaluate`, `preview_recording_start`. `preview_act` takes one `action`: `click`, `type`, `press`, `hover`, `drag`, `select`, `upload`, or `scroll`. `preview_dialog` accepts or dismisses a browser dialog. |
| ask the user (`AskQuestion`) | The host's question tool if it has one, otherwise a short question in the reply. |
| todolist | The host's todo tool if it has one, otherwise a checklist in the work log. |
| PR you opened or now drive | Register it with `link_pull_request`. |
| status page, dashboard, report table, chart | A page published with `html_preview`, then `html_render`, inside a reply already due. See [Visual reports](SKILL.md#visual-reports). |
