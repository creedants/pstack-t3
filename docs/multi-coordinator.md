# Several coordinators on one repository

This is the design for running two or more brigade coordinators on one repository, each with its own purpose, so that they share leases, the landing queue, and intake without duplicating work or blocking each other. The intended reader is the user who opens a second coordinator on a repository that already has one, and the engineer who builds the changes below.

In this document a coordinator is one standing coordinator thread and its store directory, and a work item is one unit of work handed to a worker. brigade's commands and files use kitchen names, and this document keeps those names only for commands and files, such as `fire`, `ticket add`, `dishes.tsv`, and `restaurant.json`. Two coordinators are siblings when their `restaurant.json` files name the same `projectRoot`.

**Sample records.** The design below applies to any repository. The counts, caps, and lease times quoted in [Method](#method) and [What happens today](multi-coordinator/gaps.md#what-happens-today) come from the pstack-t3 maintainers' own setup, not yours. They are not facts about your repository. Measure those counts in your own store and your own run before you rely on them.


Part of the pstack-t3 docs. Each part below names its file. Read the part it names for the full rule.
## Summary

**Sample record.** The work-item count in the table comes from the pstack-t3 maintainers' own store. Measure the share of work items that lease hot shared files in your own store.

| # | Gap | What happens today | Change |
| --- | --- | --- | --- |
| 1 | Duplicate intake | `ticket add` accepts the same `--ref` in two coordinators, and twice in one | 4 |
| 2 | No cap across coordinators | `fire` enforces no cap at all, not even the standing-orders cap of one coordinator | 2, 5 |
| 3 | No handoff | No command moves a ticket to a sibling | 4 |
| 4 | Overlapping purposes | `open` prints only the directory and says nothing about siblings | 3 |
| 5 | Blocked tickets go dark | A refused `fire` records nothing. No command shows the blocking lease or notices that it freed | 5 |
| 6 | Mode drift | `set --landing` changes only its own `restaurant.json`. `land.py mode` changes every coordinator's mode, and siblings keep a stale value | 7 |
| 7 | Hot shared files | 17 of 49 work items leased `CHANGELOG.md`, `README.md`, or `docs/guide.md` | 1, 3 |
| 8 | No combined view | `walk` lists coordinators flat, with stale modes and no queue or lease state | 7 |
| 9 | Work landed by a sibling | A sibling's `land` run settles your entry. Your next `land` prints `nothing to land`, and brigade never recorded the entry id | 2, 6 |
| 10 | Lease expiry | `fire` claims a 6-hour lease and nothing renews it. After expiry a sibling can claim the same paths, and the holder's `submit` fails | 2, 6 |
| 11 | Store collision | Two repositories with the same directory name share a store directory. `open` returns the other repository's coordinator | 3 |
| 12 | Renew breaks the lease invariant | `lease renew` revives an expired lease after a sibling claimed the same paths, leaving two active leases on one file | 2 |

Gaps 1 to 8 were known before this audit. Gaps 9 to 12 are new. The Map table below lists where each change lives. Changes 1 to 7 close every gap without a new role, and they are built and tested in a [live two-coordinator run](multi-coordinator/live-run.md#live-two-coordinator-run) before any admin work starts. Changes 8 to 10 then add the optional [executive admin](multi-coordinator/admin.md#the-executive-admin) the user asked for, for one repository. It keeps the user up to date across every coordinator on that repository. It settles conflicts between them by published rules, and logs every ruling for the user to review and overrule.

## Method

**Sample record.** The counts below come from the pstack-t3 maintainers' own store and landing database, not yours. Measure your own work items and leases.

Two sources fed the audit.

1. The code in `t3/added/brigade/scripts/brigade.py`, `t3/added/landing/scripts/land.py`, both `SKILL.md` files, and `docs/guide.md`.
2. A scratch experiment, rerunnable from the [experiment script](multi-coordinator/live-run.md#the-experiment-script) in the live run part. It creates a repository under `mktemp -d` with a local bare origin, sets `XDG_STATE_HOME` and `BRIGADE_STORE` to scratch directories, runs `land.py init --mode push --check true`, and opens coordinators `docs` and `engine` on it with the generated `skills/` scripts. It then adds tickets with the same `--ref`, starts overlapping work, submits and lands from one coordinator, changes the mode from one, lets a lease expire, and runs `walk`. The real stores under `~/.local/state` were only read, never written.

The numbers about real use come from the pstack-t3 coordinator's store (`dishes.tsv`, `log.tsv`) and its landing database (`land.db`, opened read-only). They cover 49 work items and 50 leases over the 40 hours between the first lease claim and the last release.

## Design rules

- **Shared repository facts live in the landing store.** The lease table already serializes every claim in one SQLite `BEGIN IMMEDIATE` transaction. A repository cap enforced inside that transaction cannot be raced by two coordinators starting work at once. A cap computed by reading sibling `dishes.tsv` files can be raced.
- **Remove a shared write before locking it.** Each intake source has one owning coordinator, so no two coordinators file tickets for one source. Where a shared step remains, it is one atomic operation. A store directory is claimed by `mkdir` without `exist_ok`, and a handoff is published by writing a temporary file and renaming it. Reading a sibling's tables is for display and recovery, never for exclusion.
- **Siblings are read from the existing layout.** A coordinator's siblings are the other `<store>/<project>/*/restaurant.json` files in its own project directory whose `projectRoot` equals its own. The only new shared files are the handoff and request files in each coordinator's `inbox/`.
- **Each coordinator stays the only writer of its tables.** A handoff writes a new file into the target's `inbox/`. It never appends to a sibling's `rail.tsv`.
- **`watch` reports and the coordinator decides.** Nothing starts automatically when a lease frees. Grouping tickets is the coordinator's judgment.
- **The liveness schedule follows `watch`.** A coordinator keeps its liveness schedule while `watch` prints anything other than `no work in progress`, and creates it the first time `watch` would print a line. `watch` prints a line for every item not `merged` or `dropped`, every blocked ticket, and every undelivered handoff.
- **Authority is checked at the write.** Every store write and every landing write carries its owner's token and checks it inside the same lock or transaction as the write (change 6). Stopping a run is a courtesy, because T3 does not promise that a run's commands end with it.
- **Delete duplicated state instead of syncing it.** The landing mode lives only in the landing contract.


## Changes

Changes 1 through 7 close the gaps above. Changes 8 through 11 add the admin and the docs. Each change keeps its number as its heading in its part.


## Map

Each gap and each change names its part. Read the part for the full text.

| Gap or change | Part |
| --- | --- |
| Gaps 1 through 12 | [gaps](multi-coordinator/gaps.md#what-happens-today) |
| Change 1 changelog fragments | [gaps](multi-coordinator/gaps.md#change-1-changelog-fragments-and-a-docs-batch-rule) |
| Change 2 cap and renew | [gaps](multi-coordinator/gaps.md#change-2-repository-change-cap-a-safe-renew-and-per-holder-status-in-landpy) |
| Change 3 sibling awareness | [intake](multi-coordinator/intake.md#change-3-sibling-awareness-in-open) |
| Change 4 intake and handoff | [intake](multi-coordinator/intake.md#change-4-one-intake-owner-per-source-and-ticket-handoff) |
| Change 5 blocked tickets | [intake](multi-coordinator/intake.md#change-5-blocked-tickets-and-the-per-coordinator-cap) |
| Change 6 watch and leases | [landing](multi-coordinator/landing.md#change-6-watch-keeps-leases-alive-and-settles-work-from-the-queue) |
| Change 7 mode and view | [live run](multi-coordinator/live-run.md#change-7-one-landing-mode-one-view-per-repository) |
| Change 8 reservations and contests | [live run](multi-coordinator/live-run.md#change-8-reservations-shares-and-contests-in-landpy) |
| Change 9 admin store | [delivery](multi-coordinator/admin-delivery.md#change-9-the-executive-admins-store-and-commands) |
| Change 10 admin skill | [delivery](multi-coordinator/admin-delivery.md#change-10-the-executive-admin-in-the-brigade-skill) |
| Change 11 user docs | [delivery](multi-coordinator/admin-delivery.md#change-11-user-docs-for-several-coordinators) |
| Lease plan | [landing](multi-coordinator/landing.md#lease-plan) |
| Live run | [live run](multi-coordinator/live-run.md#live-two-coordinator-run) |
| Experiment script | [live run](multi-coordinator/live-run.md#the-experiment-script) |
| Executive admin behavior | [admin](multi-coordinator/admin.md#the-executive-admin) |
| Rulings | [rulings](multi-coordinator/admin-rulings.md#rulings) |
## Out of scope

- **A machine-wide worker cap across repositories.** The governor already caps builds and tests machine-wide. [capacity.md](capacity.md) records 40 idle agents the pstack-t3 maintainers ran on their own host, with 5.9 GiB of memory still free. The sum of per-repository caps is the machine budget the user picks.
- **Coordination between repositories.** Coordinators on different repositories share no leases, queue, or intake. The first admin serves one repository. An admin that spans repositories or T3 projects needs repository-qualified intake, cross-project routing, and a live test of `t3_thread_send` across projects, so it is a later change.
- **Compare-and-swap recovery for ordinary coordinators.** Only the admin's store gets `set --thread --expect` and the `--stopped` requirement on `set --thread`. A coordinator keeps atomic opening and change 3's refusal to replace a recorded thread without `--replace`. It still uses `dish --stopped` to drop an item, and change 6's owner token fences its writes.
- **A record of message sends.** Messages are wakes. Events and requests already have durable identities, a `log.tsv` position and a request id, so no sent or delivered rows are kept.
- **Siblings matched across worktrees.** Siblings match by `projectRoot` string. Two coordinators on one repository opened with different checkouts are not siblings. Matching by git common directory, as `land.py` does, can come later if anyone opens coordinators that way.
- **Leases finer than a path.** No line or section leases.
- **Starting work automatically when a lease frees.** `watch` reports it, and the coordinator decides.
- **Priority decided by the scripts.** The scripts enforce the cap, shares, reservations, and contest holds, but they decide nothing. The admin decides by its rules, and the user sets the priorities those rules read.
- **The one-hour threshold for a `blocked` line** is a starting guess. The live run should show whether it is too slow or too noisy.
- **A `lease widen` command.** `submit`'s error says "widen the lease", and no command does that. This affects one coordinator as much as several, so it belongs in its own change.
