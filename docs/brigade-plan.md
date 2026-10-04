# brigade plan

brigade gives each project, or each focus area inside a project, its own standing coordinator thread. It replicates the setup Lauren Tan describes in her livestream with Matt Pocock ([video](https://www.youtube.com/watch?v=MN9dGgmLyso), around 39:00 to 53:00). She runs more than ten coordinators, one per area. Each delegates and supervises and never does the work itself. She moves between them and reviews what landed.

brigade has no overall coordinator. The user moves between restaurants.

## Roles

| Term | Meaning | Built on |
| --- | --- | --- |
| Executive chef | The user. | |
| Restaurant | One project, or one focus area inside a project. A project may hold several. | T3 project |
| Head chef | One long-lived top-level thread per restaurant, pinned in that project. It never writes code. | `t3_thread_launch` with `projectId`, `t3_thread_organize` pin |
| Menu | The restaurant's purpose, what good looks like, non-goals, and budget. | `menu.md` |
| House rules | Standing orders pasted into every brief: merge policy, forbidden paths, verification bar. | `house-rules.md` |
| Rail | Append-only intake. The head chef groups related tickets before it fires any. | `rail.tsv` |
| Suppliers | Scheduled intake from GitHub issues, labels, and notifications. | `schedule_task` |
| Station | A worker that runs one pstack playbook. Small dishes are child tasks. A dish that needs a PR owner is a worktree thread. | `delegate_task`, `t3_thread_launch`, `$poteto-mode` |
| Commis | Read-only prep: research, repro, scouting. | Read-only children, `how`, `why` |
| Banquet | A one-off program with an end. | Orchestrate playbook |
| Pass | Every result is checked against its ticket and the menu by a verifier on a different model family than the author. | `verifiers` role, `interrogate`, `pass.tsv` keyed by PR and head SHA |
| 86 board | Items that need the user: blocked work, irreversible actions, product calls. The only interrupt. | `86.md` |
| Line check, close-out | Morning brief and end-of-service report: what landed, how it served the menu, what to sample. | `schedule_task` `fixed_time` bound to the head chef thread |
| Recipe book | A mistake several stations repeat becomes a lint, type, or skill. | `correct` |
| Inspection | Weekly review of the restaurant's threads. | `reflect` |

## Voice

The kitchen terms name files, commands, and process steps only. They never reach speech. A head chef, station, or verifier writes in pstack's voice from `poteto-mode`: plain, short, direct engineering prose. Reports say "merged", "blocked", "needs your decision", never "plated", "86'd", or "heard, chef". The SKILL.md states this rule, and every brief brigade writes repeats it in its house rules.

## Commands

- `$brigade open` runs from any thread. It interviews the user with `grilling` for the menu and house rules, writes the store, then launches the head chef in the target project. In its first turn the head chef creates its own supplier, line-check, and close-out schedules, so each run posts into its own thread.
- `$brigade` inside a head chef thread is the operating manual: take tickets, group them, fire stations, run the pass, update the 86 board, write the close-out.
- `$brigade walk` prints every restaurant's 86 board and last close-out from the store. It is a script, not an agent.

## Store

`${XDG_STATE_HOME:-~/.local/state}/pstack-t3/brigade/<project-slug>/<restaurant>/`

- `restaurant.json`: name, project root, merge policy, head chef thread, schedule IDs, last activity, last report.
- `menu.md`, `house-rules.md`: written from templates at opening, then edited by the head chef.
- `rail.tsv` (tickets), `dishes.tsv` (grouped work assigned to a station), `pass.tsv` (one verdict per dish and head SHA), `86.tsv` (decisions for the user): current state, updated in place.
- `log.tsv`: every state change, append-only.
- `closeouts/<timestamp>.md`: one report per close.
- `decisions.tsv`: owned by `show-me-your-work`.

The head chef thread is the only writer. Nothing is committed.

## Defaults

- Merge policy `pass`: `brigade.py dish <id> --state merged` fails unless the dish has a `pass` verdict at its current head SHA. A menu may set `pr-only` or `local-only`.
- `pass record` refuses a verifier from the author's model family unless `--same-family` is given, and then notes it on the verdict.
- A report lists only log entries after `lastReportAt`, so nothing the user already saw repeats.
- `walk` marks a restaurant idle after 24 hours without activity, so a stalled head chef shows up.

## Repo layout

- `t3/added/brigade/SKILL.md`
- `t3/added/brigade/scripts/brigade.py` with `open`, `ticket`, `fire`, `pass`, `86`, `close`, `walk`
- `tests/test_brigade.py`
- `docs/skills.md`, `README.md`, `CHANGELOG.md`

## Phases

0. Confirm T3 behavior. Done on 2026-10-04 with a probe thread launched into Bridgekit from the pstack-t3 project:
   - The launched thread ran in `full-access` and pinned itself with `t3_thread_organize`.
   - Its own `schedule_task` bound to it, and a forced run posted into its thread as a user message.
   - It delegated a child, read the child's thread, and saw the child in `t3_thread_list`.
   - The launching thread got `thread_not_found` from `t3_thread_read` on it. `t3_thread_read`, `t3_thread_send`, `t3_thread_list`, and `t3_thread_organize` only reach the calling project. So `$brigade open` hands off and never steers the head chef afterward. The user talks to the head chef in its own thread, and `walk` reads the store.
1. `brigade.py` and tests. Done: `t3/added/brigade/scripts/brigade.py`, `tests/test_brigade.py`.
2. `SKILL.md`, then fresh-child tests. Done. Two services ran on a scratch repo with a planted bug.
   - Codex as head chef: grouped two reports of one bug into one dish, dropped an off-menu ticket, had Grok fix it, reviewed it with Codex at the exact SHA, and fast-forwarded `main`.
   - Grok as head chef: fixed the empty-mean ticket, parked the empty-median policy as a decision with a default instead of asking, and dropped a feature request.
   - The tests found three defects, all fixed: the report repeated a dish under every state it passed through, ticket assignment was not logged, and the head chef had to read the script source because the skill listed only some commands.
3. Pilot one restaurant (Bridgekit) for a few services, then a second.
4. Docs and release.
