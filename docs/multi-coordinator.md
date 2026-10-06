# Several coordinators on one repository

This is the design for running two or more brigade coordinators on one repository, each with its own purpose, so that they share leases, the landing queue, and intake without duplicating work or blocking each other. The intended reader is the user who opens a second coordinator on a repository that already has one, and the engineer who builds the changes below.

In this document a coordinator is one brigade restaurant and its head chef thread, and a work item is one dish. Commands and files keep their brigade names, such as `fire`, `ticket add`, `dishes.tsv`, and `restaurant.json`. Two coordinators are siblings when their `restaurant.json` files name the same `projectRoot`.

## Summary

| # | Gap | What happens today | Change |
| --- | --- | --- | --- |
| 1 | Duplicate intake | `ticket add` accepts the same `--ref` in two coordinators, and twice in one | 4 |
| 2 | No cap across coordinators | `fire` enforces no cap at all, not even the house-rules cap of one coordinator | 2, 5 |
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

Gaps 1 to 8 were known before this audit. Gaps 9 to 12 are new. Changes 1 to 8 are listed in landing order under [Changes](#changes).

## Method

Two sources fed the audit.

1. The code in `t3/added/brigade/scripts/brigade.py`, `t3/added/landing/scripts/land.py`, both `SKILL.md` files, and `docs/guide.md`.
2. A scratch experiment, rerunnable from the [experiment script](#the-experiment-script) at the end of this document. It creates a repository under `mktemp -d` with a local bare origin, sets `XDG_STATE_HOME` and `BRIGADE_STORE` to scratch directories, runs `land.py init --mode push --check true`, and opens coordinators `docs` and `engine` on it with the generated `skills/` scripts. It then adds tickets with the same `--ref`, fires overlapping work, submits and lands from one coordinator, changes the mode from one, lets a lease expire, and runs `walk`. The real stores under `~/.local/state` were only read, never written.

The numbers about real use come from the pstack-t3 coordinator's store (`dishes.tsv`, `log.tsv`) and its landing database (`land.db`, opened read-only). They cover 49 work items and 50 leases over the 40 hours between the first lease claim and the last release.

## What happens today

### 1. Duplicate intake

Observed. `ticket add --ref https://github.com/o/r/issues/7` returned `T1` in `docs` and `T1` in `engine`. A second add of the same ref in `engine` returned `T2`. `ticket add` checks no ref, in its own rail or a sibling's. Two coordinators whose house rules both name `gh issue list` as an intake source therefore each file every issue, and each fires its own work for it.

### 2. No cap across coordinators

Observed. `engine` fired four work items in a row with no refusal while `docs` held a fifth. The cap in pstack-t3's `menu.md` and house rules ("at most 3 workers in flight") is prose. `fire` reads neither, so there is no enforced cap per coordinator, per repository, or per machine. The governor (`land.py slot`) already caps builds and tests machine-wide. Agent threads have no cap.

### 3. No handoff

Observed. `ticket move` exits 2 with `invalid choice: 'move'`. Today a coordinator can only drop the ticket and ask the user, or message the sibling's thread and hope it files the ticket. The original ticket keeps no record of where the work went.

### 4. Overlapping purposes

Observed. A third `open` on the same project root printed `opened $T/brigade/app/release` and nothing else. The open steps in the skill draft the menu from the README, issues, and threads, and never from sibling menus.

### 5. Blocked tickets go dark

Observed. `engine`'s `fire --paths src/a.py,CHANGELOG.md` failed with `nothing fired: paths overlap L1 held by docs/D1 on CHANGELOG.md, docs/guide.md`. The ticket stayed `waiting`, `status` printed `waiting tickets: 2`, and `log.tsv` gained no row. `land.py` logs no refused claim either. The skill says to leave such tickets waiting until the lease is released. Nothing tells the coordinator when that happens. `watch` reports only work in progress, and the liveness schedule is deleted when `watch` prints `no work in progress`, so a coordinator whose only work is a blocked ticket wakes again only on intake, the morning service, or the evening report.

### 6. Mode drift

The known description of this gap, that `set --landing` changes the mode for every coordinator, is half right. `set --landing merge` in `docs` changed only `docs/restaurant.json`. `land.py status` still said `push mode`, and `engine` still recorded `push`. The repository's mode changes only through `land.py mode`, and that change does reach every coordinator. `land.py mode human` succeeded with no output about who else was affected, and both `restaurant.json` files kept their old values. `land.py mode` does refuse while any entry is queued (`1 entries are queued, landing, or awaiting merge`), so a mode change never strands a sibling's queued work.

The real defect is two sources of truth. `walk` printed `lands by merge` for `docs` and `lands by push` for `engine` while the contract said `push`. pstack-t3's own house rules restate the mode too, so a sibling's `land.py mode` makes every brief it pastes wrong.

### 7. Hot shared files

Measured from the real stores. 17 of the 49 work items leased at least one of `CHANGELOG.md`, `README.md`, and `docs/guide.md`. `docs/guide.md` was leased 11 times, `README.md` 10 times, and `CHANGELOG.md` 9 times, which puts them among the most leased paths in the repository. Leases holding one of the three covered 10.6 of the 40.0 hours (27%). The median hold was 35 minutes. Three hot-file leases began within one minute of the previous one ending, which is what work waiting on a lease looks like. That last point is inferred, because refused claims are not logged anywhere. With one coordinator the cost was sequencing. With two coordinators that each touch docs at this rate, the same three files are leased for a larger share of the day. That share is a guess, not a measurement.

### 8. No combined view

Observed. `walk` printed one line per coordinator in store order, each with its own recorded mode. It shows no repository grouping, no queue state, no leases, and no blocked tickets. `close` reports one coordinator.

### 9. Work landed by a sibling

Observed in push mode. `engine/D1` submitted `E1` and `docs/D1` submitted `E2`. `docs` ran `land` and printed `landed E1 (engine/D1), E2 (docs/D1)`. `engine`'s next `land` printed `nothing to land`. `land.py status` without an id printed the mode, the trunk ref, and counts, with no holders. `dishes.tsv` has no column for the entry id, so `engine` learns its work landed only if its thread still remembers `E1` and asks `status E1`.

In merge mode the same run opens the PR. The landing skill tells "this thread" to call `watch_pull_request` on the PR that its `land` opened, so `docs` would watch `engine`'s PR, and `engine` would never learn the URL. This is inferred from the skill text, not observed.

### 10. Lease expiry

Observed. A lease claimed with a short TTL expired. A sibling's claim on the same path succeeded, and the original holder's `submit` failed with `L6 is not an active lease held by docs/D9`. `fire` always claims the 6-hour default, and neither brigade nor its skill renews a lease. In the real store the longest lease was held 2.2 hours, and no lease outlived its expiry, so this gap has not bitten yet. It would bite when a coordinator stalls for hours with work in review, such as across a T3 restart overnight.

### 11. Store collision

Observed. The store directory for a project is the slug of the project root's last path component. `open --project-root $T/other/app --name docs` printed `exists $T/brigade/app/docs`, and that `restaurant.json` names `$T/app`. The caller then runs a coordinator for the wrong repository.

### 12. Renew breaks the lease invariant

Observed. `docs/D9` claimed `README.md` as L6 with a short TTL. After it expired, `engine/D9` claimed `README.md` as L7. `lease renew L6` printed `L6 renewed`, and `lease list` then showed both L6 and L7 active on `README.md`. `renew` checks only that the lease state is `active`, not that it has expired or that its paths are still free. Once change 6 has `watch` renew leases, this bug would fire every time a lease lapses, so change 2 fixes it first.

## Design rules

- **Shared repository facts live in the landing store.** The lease table already serializes every claim in one SQLite `BEGIN IMMEDIATE` transaction. A repository cap enforced inside that transaction cannot be raced by two coordinators firing at once. A cap computed by reading sibling `dishes.tsv` files can be raced.
- **Remove a shared write before locking it.** Each intake source has one owning coordinator, so no two coordinators file tickets for one source. Where a shared step remains, it is one atomic operation. A store directory is claimed by `mkdir` without `exist_ok`, and a handoff is published by writing a temporary file and renaming it. Reading a sibling's tables is for display and recovery, never for exclusion.
- **Siblings are read from the existing layout.** A coordinator's siblings are the other `<store>/<project>/*/restaurant.json` files in its own project directory whose `projectRoot` equals its own. The only new shared files are the handoff files in each coordinator's `inbox/`.
- **Each coordinator stays the only writer of its tables.** A handoff writes a new file into the target's `inbox/`. It never appends to a sibling's `rail.tsv`.
- **`watch` reports and the coordinator decides.** Nothing fires automatically when a lease frees. Grouping tickets is the coordinator's judgment.
- **The liveness schedule follows `watch`.** A coordinator keeps its liveness schedule while `watch` prints anything other than `no work in progress`, and creates it the first time `watch` would print a line. `watch` prints a line for every item not `merged` or `dropped`, every blocked ticket, and every undelivered handoff.
- **Delete duplicated state instead of syncing it.** The landing mode lives only in the landing contract.

## Changes

Each change is one PR through the landing queue, with its own tests and its own changelog fragment (change 1). Every change that edits a skill's behavior needs a fresh-child test per the `pstack-author-skill` skill. Every exclusion the design promises across two coordinators gets a test that runs two processes at once, because the experiment ran commands one after another.

### Change 1. Changelog fragments and a docs batch rule

Closes gap 7 for `CHANGELOG.md`, and sets the rule for `README.md` and `docs/guide.md`.

- **Repository practice.** Each user-facing change adds one file directly under `changes/` holding its one Unreleased bullet. The file name is the branch name with `%` written as `%25` and `/` written as `%2F`, plus `.md`, so `docs/a` writes `changes/docs%2Fa.md` and `docs-a` writes `changes/docs-a.md`. The encoding is reversible and the directory is flat, so two branch names never share a path. A coordinator's branches are `<slug>/<item id>`, and item ids never repeat, so its names are never reused. Any other branch name reused before a release adds its bullet to the same file. `CHANGELOG.md` loses its `## Unreleased` section, and its current bullet moves into a fragment. At release, `CONTRIBUTING.md` lists the fragments in the order they were added (`git log --reverse --diff-filter=A --format= --name-only -- changes/`), writes their bullets under the new version heading in `CHANGELOG.md`, and deletes them in the same commit.
- **Why fragments and not PR bodies.** The other candidate keeps changelog lines in PR bodies and collects them at release. Fragments work in every landing mode, and `push` and `local` open no PR. They need no `gh` call at release time, and the reviewer reads the line in the same diff as the change.
- **README.md and docs/guide.md.** These are prose, so fragments do not fit. A house rule says that a change edits `README.md` or `docs/guide.md` only when its ticket is about them, or when it removes or renames something they name. Otherwise the worker lists the needed doc edit under follow-ups in its report, and the coordinator files it as a ticket. When a sibling's menu owns the docs, the coordinator moves that ticket to it with `ticket move` (change 4). When no coordinator owns them, each coordinator batches its own doc tickets into one docs item, and the lease orders the batches. The pstack-t3 coordinator already batched doc follow-ups this way twice, by hand. Change 3 adds the generic sentence to the brigade skill.
- **Files.** `CHANGELOG.md`, `CONTRIBUTING.md`, `tests/test_pstack_t3.py`, and this change's own fragment.
- **Test.** `tests/test_pstack_t3.py` fails when `CHANGELOG.md` has a `## Unreleased` heading, with a message that names `changes/`, and when a file under `changes/` holds anything but bullets or sits in a subdirectory. A worker that adds a changelog line the old way fails the suite. The release cut is the end-to-end check. It must produce the same `CHANGELOG.md` section that the fragments hold.
- **Coordinator use.** pstack-t3's house rules gain the docs batch rule, and `CONTRIBUTING.md` drops "Add a line under Unreleased".

### Change 2. Repository change cap, a safe renew, and per-holder status in `land.py`

Closes gap 2 for the repository, gap 12, and the landing half of gap 9.

- **What the cap counts.** The repository cap bounds changes in flight, which are leases not yet released. That is every `submitted` lease and every `active` lease that has not expired. A worker that is running, a review in progress, and an entry waiting to land all hold such a lease. The count rises only on admission, and falls on release or when an active lease expires. A bounce moves a lease from `submitted` back to `active` and leaves the count alone, so no path exceeds the cap. An item fired without `--paths` writes nothing and holds no lease, so it counts only toward its coordinator's cap (change 5).
- **`land.py init ... --cap N` and `land.py cap N`.** These store a `cap` key in the contract. `0` clears it. Absent means no cap.
- **Admission.** `lease claim` admits a lease inside its existing transaction. After the overlap check, it counts unreleased leases. When the count is at the cap it refuses with `repository at its cap: 3 of 3 changes in flight (docs/D1, engine/D1, engine/D2)`. The holders in the message are the facts a coordinator needs to say who it waits on.
- **`lease renew`.** Renewing a lease that has not expired works as today. Renewing an expired lease is a new admission in the same `BEGIN IMMEDIATE` transaction. It refuses when any other holder's unreleased lease overlaps, with `L1 expired and L2 held by engine/D1 now covers README.md; claim again after it is released`. It also refuses when the cap is full, with the admission message. An expired lease with no overlap and room under the cap renews.
- **`land.py status --holder <holder>`.** It prints the entries of exactly that holder in id order, one per line, with the SHA: `E1 landed (engine/D1, 1ebeef80f473) as 0f3a9c1d2e4b`, `E3 awaiting-merge (engine/D2, 34a807b9493f) <pr url>`, `E4 bounced (engine/D3, a1f7ed73e60c): conflict with trunk`. With `--holder` ending in `/`, it matches that prefix.
- **`lease check --holder <holder> --paths <paths>`.** It runs the admission test of `lease claim` without claiming. It prints `free`, or each overlapping lease with its holder, or the cap message. `watch` uses it to recheck a blocked ticket's requested paths (change 5).
- **`lease renew --if-live`** renews only a lease that has not expired, and otherwise refuses with `L4 expired at <time>`. `watch` uses it, so re-admitting an expired lease is always an explicit step a coordinator takes after it stops the old worker (change 6).
- **`lease renew` names why it refused.** The refusal names the lease's state, as in `L4 is submitted; the queue holds it`, `L4 is released`, the overlap message, or the cap message. `watch` maps each to its own line (change 6).
- **`land.py status`.** It adds `changes in flight: 3 of 4` to its line when a cap is set.
- **`land.py mode`.** Its success line ends with `; leases held by docs/D1, engine/D3` when any lease is unreleased, so the person changing the mode sees whom it affects.
- **Files.** `t3/added/landing/scripts/land.py`, `t3/added/landing/SKILL.md`, `tests/test_landing.py`, generated `skills/landing/`, its fragment.
- **Tests.** With a cap of 2, claims by `a/D1` and `b/D1` succeed, `c/D1` is refused with the literal message, and after `lease release L1` the same claim returns `L3`. Ten processes claiming disjoint paths at once under a cap of 3 produce exactly 3 leases. A submitted lease counts toward the cap, and a bounce leaves the count unchanged. An expired lease does not count. Renew of an expired lease after another holder's overlapping claim exits 1 with the literal message, and `lease list` shows one lease on the path. Renew of an expired lease after the cap filled exits 1. Renew of a submitted lease and of a released lease each print their own message. `lease check` on paths a released lease held prints `free`, and on paths a new lease holds names that lease. `status --holder engine/D2` lists only that holder.
- **Coordinator use.** The coordinator that opens the first restaurant on a repository sets the cap with the mode, as `land.py init --trunk main --mode merge --cap 4`. The landing skill's setup section says the cap belongs to the repository like the mode does.

### Change 3. Sibling awareness in `open`

Closes gaps 4 and 11, and adds the hot-files sentence for gap 7.

- **`open` claims its directory atomically.** It creates the coordinator directory with `mkdir` and no `exist_ok`, then writes `restaurant.json`. The process that created the directory owns it. Any other process, whether it lost a race or found an existing coordinator, reads `restaurant.json`, waiting up to one second for it to appear. When that file names another `projectRoot`, `open` exits 1 with `brigade: <dir> already holds a coordinator for <other root>; pick another --name`. Making the project directory unique per repository would change the store layout for every existing coordinator. Refusing is enough, because siblings are matched by `projectRoot` and not by directory.
- **One head chef per coordinator.** `open` on an existing coordinator prints `head chef thread <id> already recorded` when one is. `set --thread` refuses to replace a recorded thread unless `--replace` is passed. The skill's Open step 6 launches a head chef only when `open` printed `opened`. That word goes to exactly one caller, the one whose `mkdir` created the directory. An `exists` coordinator with no recorded thread is the user's call, so two threads never write one store.
- **`open` prints siblings.** After `opened <dir>` or `exists <dir>`, it prints one block per sibling:

  ```
  sibling engine (<store>/app/engine), thread <id or not recorded>
    purpose: Keep the engine fast and correct.
    off the menu: Docs and release notes; Vendor upgrades
  ```

  A sibling whose menu still has the template purpose prints `purpose: not written yet`.
- **Skill.** Open a restaurant step 4 says "Fill `menu.md`. Keep its Purpose clear of every sibling `open` printed, and list each sibling's purpose under Off the menu as that sibling's work." Two coordinators opened at the same moment both see template menus, so First service gains a step that reruns `open` and compares the purposes it prints with this menu. Step 4's house-rules list gains "the repository's hot shared files, and where their changes go instead", pointing at the fragment and docs batch rules.
- **Files.** `t3/added/brigade/scripts/brigade.py`, `t3/added/brigade/SKILL.md`, `tests/test_brigade.py`, generated `skills/brigade/`, its fragment.
- **Tests.** Open `Perf` with a written purpose and off-menu list, then open `Docs` on the same root, and assert the exact sibling block. Open `Docs` on another root whose directory has the same name, and assert exit 1, the literal message, and an unchanged `restaurant.json`. Two processes opening the same name on different roots at once produce one `restaurant.json` and one refusal. `set --thread` on a coordinator with a recorded thread exits 1 without `--replace`.
- **Coordinator use.** Rerunning `open` on an existing coordinator reprints its siblings, so a coordinator can check for new siblings at any time.

### Change 4. One intake owner per source, and ticket handoff

Closes gaps 1 and 3.

- **Why ownership and not a shared claim.** Two coordinators that both read GitHub share one write, the ticket for each issue. Locking that write across two stores leaves windows, because `rail.tsv` and any shared claim cannot commit together. Giving each source one owner removes the shared write. The owner's own rail is then the only record of refs from that source, and its single writer makes the duplicate check race-free.
- **`restaurant.json` gains `intake`,** a list of source names set by `open --intake github` and `set --intake github`. Setting a source that a sibling already lists exits 1 with `brigade: docs already owns intake from github; move tickets to it instead`. Dropping a source exits 1 while this coordinator holds a `waiting` or `assigned` ticket from it. Two coordinators setting the same source at the same moment is the one window left. It is a one-time setup step, and the next check fails safe.
- **`ticket add --source <s>`** for any source other than `user` exits 1 unless this coordinator lists `<s>` in `intake` and no sibling also lists it. The refusal names the owner, as `brigade: core owns intake from github; ask it to file this and move it here`. When two siblings list one source, both refuse, so a setup race files nothing twice. When `--ref` is not empty, it also exits 1 when this coordinator holds a ticket with the same ref in state `waiting`, `assigned`, or `moved`, with `brigade: <ref> is already T1 (waiting); nothing added`. A `done` or `dropped` ticket does not block, so a reopened issue gets a new ticket. It also refuses a ref that a sibling holds in one of those states. That read of other coordinators' files is not exclusion. It covers an owner that took a source over from a sibling, whose old tickets stay live in the sibling's rail. Ownership is what excludes, so two limits remain and are accepted. Two coordinators can file the same ref as `user` tickets at the same moment, and one issue can arrive through two different sources that two coordinators own. Refs compare as exact strings after trimming whitespace. The intake step records the URL `gh` prints, so one issue has one ref.
- **`ticket move T6 --to <sibling>`.** The target must be a sibling. T6 must be `waiting`, or already moved to the same target.
  1. One atomic rewrite of `rail.tsv` sets T6 to the new state `moved` and its `dish` field to `to:<sibling>`. State and destination commit together. From here this coordinator cannot fire T6, and its ref stays live here, so the owner's intake never refiles the issue.
  2. Publish `<sibling dir>/inbox/<slug>-T6.json`, holding `summary`, `source`, `ref`, and the handoff id `<slug>/T6`. The file is written under a temporary name in `inbox/` and renamed into place.
  It prints `T6 moved to engine; tell thread <id>`. A rerun after a crash repeats step 2 unless the target's `rail.tsv` already holds the handoff id.
- **`ticket take`.** For each `*.json` file in this coordinator's inbox, in name order, it looks for a rail row whose `source` ends with `(from <handoff id>)`. When none exists, it rewrites the whole `rail.tsv` through the existing atomic temp-and-rename helper, with one new row whose `source` is `<source> (from <handoff id>)`. A plain append is not used, because a killed append can leave a partial row. When the row exists and `log.tsv` lacks `from <handoff id>`, it writes that log row. Then it deletes the file. `rows` ignores a last line with no newline, so a killed append to `log.tsv` leaves no partial row for a replay to misread. A crash at any point leaves either no ticket, or a complete ticket that the next take recognizes and finishes logging. Dedupe uses the handoff id, not the ref.
- **Handoffs stay visible until taken.** Until the target's rail holds the handoff id, the source's `watch` prints `T6: moved to engine, waiting for ticket take` while the inbox file exists, and `T6: moved to engine, not delivered; run ticket move T6 --to engine again` when it does not. The target's `watch` prints `handed to you: 1; run ticket take`. `status` adds `handed to you: N` while the inbox holds files. `close` gains a section for `moved` tickets, `Handed to another coordinator`.
- **Skill.** Open step 4 sets `--intake` for each source in the house rules, and leaves a source to the sibling that already owns it. Run a service step 2 starts with `$B ticket take`. A ticket that is a sibling's work is moved with `ticket move`, followed by `t3_thread_send` to the printed thread with mode `"auto"` and the line `Ticket from <coordinator>: run ticket take.` Siblings share a project root, so they share a T3 project and can message each other. When a moved issue reopens, the owner marks its old ticket `done` before filing the new one.
- **Files.** Same as change 3.
- **Tests.** `set --intake github` in a second sibling is refused with the literal message. With `intake` written into both siblings' `restaurant.json` by hand, `ticket add --source github` exits 1 in both. The same ref is refused within the owner while live, and accepted after `done`. `ticket move` interrupted after step 1, then rerun, publishes one inbox file. `ticket take` run twice with the file copied back between runs files one ticket. A take killed after the rail rewrite and before the log row files one ticket and one log row on the next take. Failures injected inside the temp-file write and inside the log append, not only between steps, leave the same end state after the next take. Dropping `github` from `intake` with a waiting github ticket exits 1. After a source moves to a new owner, that owner refuses a ref the old owner still holds live. `watch` prints the undelivered line when the inbox file is deleted before take.

### Change 5. Blocked tickets and the per-coordinator cap

Closes gap 5, and gap 2 for one coordinator.

- **`restaurant.json` gains `workers`,** set by `open --workers N` and `set --workers N`. A missing field reads as 2. The cap counts items with an agent running, which are `in-progress` and `in-review` items. `fire` refuses before it claims a lease when the count is at the cap, with `nothing fired: 2 of 2 workers running`. `dish --state in-progress` and `dish --state in-review` enforce the same cap whenever the item is not already counted, because a send-back, a bounce, and a retried review enter those states without `fire`. Moving from `in-progress` to `in-review`, and replacing the worker of an item, leave the count alone. This cap bounds running agents. It does not bound one coordinator's share of the repository cap, because its submitted entries hold leases with no agent running. Fairness between coordinators is out of scope. `open` warns when `workers` is at or above the repository cap and a sibling exists.
- **A refused `fire` is recorded with what it needs.** On any refusal, `fire` logs a `blocked` row for each ticket in `log.tsv`. The note holds everything a later `fire` needs, as JSON with `kind` (`lease`, `repository`, or `workers`), `tickets`, `station`, `summary`, `paths`, and `timebox`. The ticket stays `waiting`, and `rail.tsv` keeps its columns. A later fire of that ticket clears the block.
- **`ticket list`** appends `blocked: <reason>` to a waiting ticket whose latest log row is `blocked`.
- **`watch` rechecks the real condition.** It runs `land.py lease check` on the recorded paths, and counts this coordinator's running workers. It prints `T2: waiting on L7 (docs/D3)`, `T5: waiting for room in the repository (4 of 4 changes in flight)`, or `T7: waiting for a worker (2 of 2 running)`, whichever holds now. A different lease that took the same paths after the first was released still blocks. When nothing blocks, it prints a runnable command built from the note, `T2: unblocked; run fire --tickets T2 --station bug-fix --summary '<summary>' --paths src/a.py,CHANGELOG.md --timebox 60`.
- **Skill.** Run a service step 4 drops "Keep in-flight dishes under the house-rules cap", because `fire` enforces it. A refused fire leaves the tickets waiting. The liveness schedule follows `watch` per the design rule, so First service step 3 creates it on the first refused fire, and the Liveness check deletes it only on `no work in progress`. The Liveness check gains the `unblocked` line, which reruns Run a service steps 3 and 4 for those tickets. Open step 4 replaces "worker cap" in the house-rules list with `--workers`.
- **Files.** Same as change 3.
- **Tests.** A sibling's lease refuses `fire`. Then `ticket list` shows the reason, and `watch` prints `waiting on L1`. After `lease release L1` and a new claim on the same paths, `watch` prints `waiting on L2`. After that release, it prints the literal `unblocked` line, and running that line fires the item. With `--workers 1`, a second `fire` is refused, and so are `dish --state in-progress` on a sent-back item and `dish --state in-review` on a blocked one. A repository-cap refusal prints the repository line.

### Change 6. `watch` keeps leases alive and settles work from the queue

Closes gap 10, and the brigade half of gap 9.

- **Lease renewal.** For each item in `in-progress`, `in-review`, `passed`, `sent-back`, or `blocked` that has a lease, `watch` runs `land.py lease renew --if-live` and reads its answer. A live lease gets a fresh 6-hour expiry, and `watch` prints nothing for it. `watch` never needs `lease list`, which hides expired leases. A submitted lease is left to the queue. An expired lease prints `D3: lease L4 expired; interrupt its worker, then run lease renew L4`. The coordinator interrupts the worker and runs that renew itself. It re-admits the lease, or refuses with the overlap message or the cap message from change 2. On a refusal the coordinator parks the item and runs the renew again on a later service. A released lease prints `D3: lease L4 was released; claim again before submitting`. A landing store that cannot be read prints `D3: could not renew L4: <error>`.
- **A line for every open item.** Besides those, `watch` prints one line for each item not `merged` or `dropped` that has none yet, such as `D2: in review`, `D3: passed, not submitted`, or `D4: parked`. So a coordinator with only reviews, parked work, or passed work keeps its liveness schedule, and its leases keep renewing.
- **Leases end with the work, after the worker stops.** Dropping an item first interrupts its worker thread with `t3_thread_interrupt`, then runs `dish --state dropped`, which releases the item's active lease. The skill's Close a restaurant section does the same for every active lease. `brigade.py close` writes reports and releases nothing. A parked item keeps renewing, because its paths are still reserved for it. A lease expires only when its coordinator has not run `watch` for 6 hours. From expiry until the old worker is interrupted, another coordinator may claim and edit the same paths. That gap is the guarantee's limit, and the interrupt-then-renew order keeps it from growing after the coordinator returns.
- **Entries are matched exactly.** For each item in `passed` or `queued`, `watch` runs `land.py status --holder <slug>/<item>` and takes the entry with the highest id whose SHA is the item's `sha`. It prints `D1: landed as E1 (0f3a9c1d2e4b); mark it merged`, `D2: E3 awaiting merge <pr url>; watch that PR`, `D4: E4 bounced: conflict with trunk`, or, for a `passed` item, `D5: E6 already submitted; mark it queued`. That last line recovers a crash between `submit` and `dish --state queued`. An older bounced entry at another SHA is ignored.
- **Bounce fix.** The skill's bounce step runs `dish --state in-progress` before it reruns `brief`. Today it reruns `brief` on a `queued` item, which `brief` refuses.
- **Skill.** The Liveness check acts on these lines with the steps the skill already has for a landing, an open PR, and a bounce. The landing skill's Keep the queue moving section says to act only on lines whose holder is this coordinator's own, and to call `watch_pull_request` only on those PRs. A sibling's PR is the sibling's to watch, and the owner learns its URL from the `awaiting merge` line.
- **Files.** Same as change 3, plus `t3/added/landing/SKILL.md` and generated `skills/landing/` for the sentence on holders.
- **Tests.** A short-TTL lease on an in-progress item is renewed by `watch` before it expires, and its expiry moves forward. An expired lease makes `watch` print the expired line and stay expired, and the explicit `lease renew` then re-admits it, or prints the overlap message when another holder claimed the paths. A coordinator whose only item is `in-review`, `passed`, or `blocked` gets a `watch` line, not `no work in progress`. An item whose entry another holder's `land` landed makes `watch` print `landed as E1`. A passed item with a submitted entry prints `already submitted`. An item with a bounced entry at an old SHA and a queued entry at its current SHA prints only the queued state. `dish --state dropped` leaves no active lease. These run against a scratch repository with a `local` contract, as the existing `fire` test does.

### Change 7. One landing mode, one view per repository

Closes gaps 6 and 8.

- **Subtract.** Delete `set --landing` and the `--landing` flag of `open`. New `restaurant.json` files have no `landing` field, and an old field is ignored. The opener still asks the user for the mode, and passes it to `land.py init` or `land.py mode` as step 5 already does. The four tests that pass `--landing` move to the new form. Every doc line that shows the removed flags changes in this same change, per change 1's rule for removed names. That covers the brigade skill, `docs/guide.md`, and `docs/brigade-plan.md`.
- **`status`** prints the contract's mode, read from the first word of `land.py status`, as in `reporting: milestones, lands by merge, waiting tickets: 2`. With no contract it prints `no landing contract`.
- **`walk` groups by repository.** One header per `projectRoot` carries the `land.py status` line, followed by each coordinator's line without the old `lands by` field, its unreleased leases from `land.py lease list` matched by holder, and its blocked ticket count:

  ```
  ~/Projects/app: merge mode onto refs/remotes/origin/main. awaiting-merge: 1, landed: 40, changes in flight: 3 of 4.
    docs (reports digest): in progress: 1, waiting to land: 1
      thread <id>, leases L41 (D7)
    engine (reports milestones): in progress: 2, waiting tickets: 1 (1 blocked)
      thread <id>, leases L42 (D12), L43 (D13)
  ```

  `walk --repo <root>` prints one repository.
- **Skill.** In Open steps 3 and 5, when the contract already has another mode, run `land.py mode`, which names the holders of unreleased leases, then tell each sibling with `t3_thread_send`, using the threads `walk --repo` prints. House rules do not restate the landing mode. A coordinator whose house rules already state it deletes that rule on its next service. pstack-t3's rule 3 is one. The Executive chef's view section describes the grouped `walk`.
- **Files.** Same as change 3, plus `docs/guide.md` and `docs/brigade-plan.md`.
- **Tests.** `status` and `walk` print the contract mode after `land.py mode` changes it, with no brigade command run in between. `walk` groups two siblings under one header and a third coordinator under another, with the literal lines. `set --landing` exits 2.

### Change 8. User docs for several coordinators

- **Content.** A "Several coordinators on one repository" section in `docs/guide.md`, covering the caps, intake ownership, handoff, blocked tickets, and the grouped `walk`. A row in `docs/how-it-works.md`. This change is the docs batch that change 1's rule asks for, so changes 2 to 6 leave these files alone.
- **Files.** `docs/guide.md`, `docs/how-it-works.md`, its fragment.
- **Test.** `python3 scripts/build.py` passes. Every command the section shows appears in a unit test from changes 2 to 7.

## Lease plan

| Change | Leased paths | Can run beside |
| --- | --- | --- |
| 1 | `CHANGELOG.md`, `CONTRIBUTING.md`, `tests/test_pstack_t3.py`, its fragment | 2, 3 |
| 2 | `t3/added/landing`, `skills/landing`, `tests/test_landing.py`, its fragment | 1, 3, 4, 5 |
| 3 | `t3/added/brigade`, `skills/brigade`, `tests/test_brigade.py`, its fragment | 1, 2 |
| 4 | same as 3, with its own fragment | 2 |
| 5 | same as 3, with its own fragment | 2 |
| 6 | same as 3, plus `t3/added/landing/SKILL.md`, `skills/landing`, with its own fragment | none |
| 7 | same as 3, plus `docs/guide.md`, `docs/brigade-plan.md`, with its own fragment | none |
| 8 | `docs/guide.md`, `docs/how-it-works.md`, its fragment | none |

A fragment path is unique to its branch, so fragments never overlap. Changes 2 and 3 can run beside change 1 because they write a fragment and never touch `CHANGELOG.md`. If one lands first, its fragment waits in `changes/` for change 1's release step. Changes 3 to 7 all edit `brigade.py`, the brigade skill, and its tests, so they land one after another. Splitting `brigade.py` into modules to allow parallel leases would cost more than the sequencing does. Change 6 needs change 2's renew rule and `status --holder`, so 2 lands before 6. Change 5's repository-cap line reads the count change 2 adds to `land.py status`, so 2 also lands before 5. Change 7 edits `docs/guide.md`, so it lands before change 8. A coordinator running this plan uses `fire --paths` with exactly the paths in this table.

## Live two-coordinator run

Run after change 7 lands, on a clone of `creedants/pstack-t3-sandbox`, the scratch repository the request for this design names for the live run. Opening issues there is still a GitHub write outside pstack-t3's own PR flow. The ref check compares strings, so the run uses an existing sandbox issue URL, or a made-up ref, rather than opening an issue.

1. `land.py init --trunk main --mode merge --cap 3 --check <the sandbox's test command>`. Open `core` with `--intake github` and then `docs`, each with `--workers 2`. `docs`'s `open` prints `core`'s purpose and off-menu lines.
2. `docs` adds a ticket with `--source github` and is refused, naming `core`. `core` files the issue, and filing it again is refused.
3. `core` fires one item on `README.md` and one on `src/`. `docs` fires on `README.md` and is refused. `docs`'s `watch` prints `waiting on L<n> (core/D1)`.
4. `docs` fires one item on a disjoint path, which makes 3 changes in flight. A third `fire` in `core` is refused by its own cap of 2. A second `fire` in `docs` is refused with the repository cap message, `3 of 3 changes in flight`.
5. `core`'s `README.md` item passes review from another model family, and `core` submits it and marks it `queued`. Then `docs`, not `core`, runs `land`, which opens the PR for `core`'s entry. `core`'s next liveness check prints `landed as E<n>` or `awaiting merge`, and `core` watches its own PR.
6. Within one liveness interval after `core`'s `README.md` lease is released, `docs`'s `watch` prints the `unblocked` line, and `docs` fires it.
7. `core` moves a ticket to `docs`. `docs` wakes on the message, and `ticket take` files it once.
8. `land.py mode human` while an entry is queued is refused. When idle it names the holders of unreleased leases, and `walk` shows one mode for both coordinators.

Record four numbers.

- Duplicate tickets filed. The target is 0.
- Items left `queued` after they landed. The target is 0.
- Minutes from a lease release to the refire. The target is at most one liveness interval, 10 minutes at `milestones`.
- `fire` refusals, counted by kind (`lease`, `repository`, `workers`).

## Out of scope

- **A machine-wide worker cap across repositories.** The governor already caps builds and tests machine-wide. [capacity.md](capacity.md) measured 40 idle agents on this machine with 5.9 GiB of memory still free. The sum of per-repository caps is the machine budget the user picks.
- **Coordinators in different T3 projects.** A head chef cannot read or message threads in another project.
- **Siblings matched across worktrees.** Siblings match by `projectRoot` string. Two coordinators on one repository opened with different checkouts are not siblings. Matching by git common directory, as `land.py` does, can come later if anyone opens coordinators that way.
- **Leases finer than a path.** No line or section leases.
- **Firing automatically when a lease frees.** `watch` reports it, and the coordinator decides.
- **Fairness or priority between coordinators** beyond the per-coordinator cap.
- **A `lease widen` command.** `submit`'s error says "widen the lease", and no command does that. This affects one coordinator as much as several, so it belongs in its own change.

## The experiment script

This is the audit script, run against commit `5794d40`. It records what each command printed and asserts nothing. The tests in changes 2 to 7 are the asserting checks. Run it with the path to a `skills/` directory. It writes only under a fresh `mktemp -d` and prints that path at the end. After change 7, its `--landing` flags no longer parse.

```bash
#!/usr/bin/env bash
# Two coordinators on one scratch repository. Usage: two_restaurants.sh <skills dir>
set -u
SK=$1
T=$(mktemp -d)
export XDG_STATE_HOME=$T/state BRIGADE_STORE=$T/brigade
B="python3 $SK/brigade/scripts/brigade.py"
L="python3 $SK/landing/scripts/land.py --repo $T/app"
step() { printf '\n## %s\n' "$*"; }
run() { printf '$ %s\n' "${*/$T/\$T}"; "$@" 2>&1 | sed "s#$T#\$T#g"; printf '[exit %s]\n' "${PIPESTATUS[0]}"; }

git init -q --bare -b main $T/origin.git
git clone -q $T/origin.git $T/app 2>/dev/null
cd $T/app
git config user.email s@x; git config user.name s
mkdir -p docs src; for f in CHANGELOG.md README.md docs/guide.md src/a.py src/b.py src/c.py src/d.py; do echo x > $f; done
git add -A; git commit -qm init; git push -q origin main

step "contract and two coordinators"
run $L init --trunk main --mode push --check true
run $B open --project-root $T/app --name docs --landing push
run $B open --project-root $T/app --name engine --landing push
A="$B --at $BRIGADE_STORE/app/docs"; E="$B --at $BRIGADE_STORE/app/engine"
for r in docs engine; do sed -i "s/^What this restaurant exists.*/Purpose of $r: keep the app healthy./" $BRIGADE_STORE/app/$r/menu.md; done

step "gap 1: same --ref in both, and twice in one"
run $A ticket add --summary "issue 7" --source github --ref https://github.com/o/r/issues/7
run $E ticket add --summary "issue 7" --source github --ref https://github.com/o/r/issues/7
run $E ticket add --summary "issue 7 again" --source github --ref https://github.com/o/r/issues/7

step "gap 5: overlapping fire across coordinators"
run $A fire --tickets T1 --station feature --summary "docs change" --paths CHANGELOG.md,docs/guide.md
run $E fire --tickets T1 --station bug-fix --summary "engine fix" --paths src/a.py,CHANGELOG.md
run $E ticket list --state waiting
run $E status
run $L lease list

step "gap 2: no cap on work in progress"
for n in 4 5 6 7; do $E ticket add --summary "t$n" >/dev/null; done
run $E fire --tickets T2 --station feature --summary one --paths src/b.py
run $E fire --tickets T3 --station feature --summary two --paths src/c.py
run $E fire --tickets T4 --station feature --summary three --paths src/d.py
run $E fire --tickets T5 --station feature --summary four --paths src/e.py
run $E status; run $A status

step "gap 6: set --landing in one coordinator, then land.py mode"
run $A set --landing merge
run $L status
run grep -H '"landing"' $BRIGADE_STORE/app/docs/restaurant.json $BRIGADE_STORE/app/engine/restaurant.json
run $L mode human
run grep -H '"landing"' $BRIGADE_STORE/app/docs/restaurant.json $BRIGADE_STORE/app/engine/restaurant.json
run $L mode push

step "gap 9: one coordinator's land run lands the other's entry"
git checkout -q -b engine/d1 && echo y >> src/b.py && git commit -qam "engine b" && ESHA=$(git rev-parse HEAD) && git checkout -q main
git checkout -q -b docs/d1 && echo y >> CHANGELOG.md && git commit -qam "docs changelog" && DSHA=$(git rev-parse HEAD) && git checkout -q main
run $L submit --holder engine/D1 --branch engine/d1 --sha $ESHA --lease L2 --reviewer x/y
run $L submit --holder docs/D1 --branch docs/d1 --sha $DSHA --lease L1 --reviewer x/y
run $L land
run $L land
run $L status

step "land.py mode with a sibling's entry queued"
git checkout -q -b engine/d2 && echo y >> src/c.py && git commit -qam "engine c" && CSHA=$(git rev-parse HEAD) && git checkout -q main
run $L submit --holder engine/D2 --branch engine/d2 --sha $CSHA --lease L3 --reviewer x/y
run $L mode human

step "gaps 10 and 12: an expired lease, a sibling's claim, submit and renew"
run $L lease claim --holder docs/D9 --paths README.md --ttl-hours 0.0003
sleep 2
run $L lease claim --holder engine/D9 --paths README.md
git checkout -q -b docs/d9 && echo y >> README.md && git commit -qam "docs readme" && RSHA=$(git rev-parse HEAD) && git checkout -q main
run $L submit --holder docs/D9 --branch docs/d9 --sha $RSHA --lease L6 --reviewer x/y
run $L lease renew L6
run $L lease list

step "gap 11: another repository with the same directory name"
mkdir -p $T/other && git init -q -b main $T/other/app
run $B open --project-root $T/other/app --name docs --landing local
run grep projectRoot $BRIGADE_STORE/app/docs/restaurant.json

step "gap 3: no command moves a ticket"
run $E ticket move T6 --to docs

step "gap 4: a third coordinator says nothing about siblings"
run $B open --project-root $T/app --name release --landing push

step "gap 8: walk"
run $B walk
echo; echo "scratch: $T"
```
