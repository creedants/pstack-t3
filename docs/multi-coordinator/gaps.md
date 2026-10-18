Part of [the design](../multi-coordinator.md).

## What happens today

**Sample record.** Each section below reports what the pstack-t3 maintainers found in their own store, their own scratch experiment, and the code and skill text they read, by observation or by inference. Measure your own store and your own run, where the findings will differ.

### 1. Duplicate intake

Observed. `ticket add --ref https://github.com/o/r/issues/7` returned `T1` in `docs` and `T1` in `engine`. A second add of the same ref in `engine` returned `T2`. `ticket add` checks no ref, in its own `rail.tsv` or a sibling's. Two coordinators whose standing orders both name `gh issue list` as an intake source therefore each file every issue, and each starts its own work for it.

### 2. No cap across coordinators

Observed. `engine` started four work items in a row with no refusal while `docs` held a fifth. The cap in pstack-t3's `menu.md` and standing orders ("at most 3 workers in flight") is prose. `fire` reads neither, so there is no enforced cap per coordinator, per repository, or per machine. The governor (`land.py slot`) already caps builds and tests machine-wide. Agent threads have no cap.

### 3. No handoff

Observed. `ticket move` exits 2 with `invalid choice: 'move'`. Today a coordinator can only drop the ticket and ask the user, or message the sibling's thread and hope it files the ticket. The original ticket keeps no record of where the work went.

### 4. Overlapping purposes

Observed. A third `open` on the same project root printed `opened $T/brigade/app/release` and nothing else. The opening steps in the skill draft `menu.md` from the README, issues, and threads, and never from the siblings' `menu.md` files.

### 5. Blocked tickets go dark

Observed. `engine`'s `fire --paths src/a.py,CHANGELOG.md` failed with `nothing fired: paths overlap L1 held by docs/D1 on CHANGELOG.md, docs/guide.md`. The ticket stayed `waiting`, `status` printed `waiting tickets: 2`, and `log.tsv` gained no row. `land.py` logs no refused claim either. The skill says to leave such tickets waiting until the lease is released. Nothing tells the coordinator when that happens. `watch` reports only work in progress, and the liveness schedule is deleted when `watch` prints `no work in progress`, so a coordinator whose only work is a blocked ticket wakes again only on intake, the morning service, or the evening report.

### 6. Mode drift

The known description of this gap, that `set --landing` changes the mode for every coordinator, is half right. `set --landing merge` in `docs` changed only `docs/restaurant.json`. `land.py status` still said `push mode`, and `engine` still recorded `push`. The repository's mode changes only through `land.py mode`, and that change does reach every coordinator. `land.py mode human` succeeded with no output about who else was affected, and both `restaurant.json` files kept their old values. `land.py mode` does refuse while any entry is queued (`1 entries are queued, landing, or awaiting merge`), so a mode change never strands a sibling's queued work.

The real defect is two sources of truth. `walk` printed `lands by merge` for `docs` and `lands by push` for `engine` while the contract said `push`. pstack-t3's own standing orders restate the mode too, so a sibling's `land.py mode` makes every brief it pastes wrong.

### 7. Hot shared files

Measured from the real stores. 17 of the 49 work items leased at least one of `CHANGELOG.md`, `README.md`, and `docs/guide.md`. Counted as leases on the exact path, `docs/guide.md` had 12, `README.md` 10, and `CHANGELOG.md` 9, which puts them among the most leased paths in the repository. The 12 leases on `docs/guide.md` belong to 11 work items, because D42 held two of them, L42 and L46. Leases holding one of the three covered 10.6 of the 40.0 hours (27%). The median hold was 35 minutes. Three hot-file leases began within one minute of the previous one ending, which is what work waiting on a lease looks like. That last point is inferred, because refused claims are not logged anywhere. With one coordinator the cost was sequencing. With two coordinators that each touch docs at this rate, the same three files are leased for a larger share of the day. That share is a guess, not a measurement.

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

Observed. `docs/D9` claimed `README.md` as L6 with a short TTL. After it expired, `engine/D9` claimed `README.md` as L7. `lease renew L6` printed `L6 renewed`, and `lease list` then showed both L6 and L7 active on `README.md`. `renew` checks only that the lease state is `active`, not that it has expired or that its paths are still free. Once change 6 has `watch` renew leases, this bug would trigger every time a lease lapses, so change 2 fixes it first.


## Changes in this part

### Change 1. Changelog fragments and a docs batch rule

Closes gap 7 for `CHANGELOG.md`, and sets the rule for `README.md` and `docs/guide.md`.

- **Repository practice.** Each user-facing change adds one file directly under `changes/` holding its one Unreleased bullet. The file name is the branch name with `%` written as `%25` and `/` written as `%2F`, plus `.md`, so `docs/a` writes `changes/docs%2Fa.md` and `docs-a` writes `changes/docs-a.md`. The encoding is reversible and the directory is flat, so two branch names never share a path. A coordinator's branches are `<slug>/<item id>`, and item ids never repeat, so its names are never reused. Any other branch name reused before a release adds its bullet to the same file. `CHANGELOG.md` loses its `## Unreleased` section, and its current bullet moves into a fragment. At release, `CONTRIBUTING.md` lists the fragments in the order they were added (`git log --reverse --diff-filter=A --format= --name-only -- changes/`), writes their bullets under the new version heading in `CHANGELOG.md`, and deletes them in the same commit.
- **Why fragments and not PR bodies.** The other candidate keeps changelog lines in PR bodies and collects them at release. Fragments work in every landing mode, and `push` and `local` open no PR. They need no `gh` call at release time, and the reviewer reads the line in the same diff as the change.
- **README.md and docs/guide.md.** These are prose, so fragments do not fit. A house rule says that a change edits `README.md` or `docs/guide.md` only when its ticket is about them, or when it removes or renames something they name. Otherwise the worker lists the needed doc edit under follow-ups in its report, and the coordinator files it as a ticket. When a sibling's `menu.md` claims the docs, the coordinator moves that ticket to it with `ticket move` (change 4). When no coordinator owns them, each coordinator batches its own doc tickets into one docs item, and the lease orders the batches. The pstack-t3 coordinator already batched doc follow-ups this way twice, by hand. Change 3 adds the generic sentence to the brigade skill.
- **Files.** `CHANGELOG.md`, `CONTRIBUTING.md`, `tests/test_pstack_t3.py`, and this change's own fragment.
- **Test.** `tests/test_pstack_t3.py` fails when `CHANGELOG.md` has a `## Unreleased` heading, with a message that names `changes/`, and when a file under `changes/` holds anything but bullets or sits in a subdirectory. A worker that adds a changelog line the old way fails the suite. The release cut is the end-to-end check. It must produce the same `CHANGELOG.md` section that the fragments hold.
- **Coordinator use.** pstack-t3's standing orders gain the docs batch rule, and `CONTRIBUTING.md` drops "Add a line under Unreleased".

### Change 2. Repository change cap, a safe renew, and per-holder status in `land.py`

Closes gap 2 for the repository, gap 12, and the landing half of gap 9.

- **What the cap counts.** The repository cap bounds changes in flight, which are leases not yet released. That is every `submitted` lease and every `active` lease that has not expired. A worker that is running, a review in progress, and an entry waiting to land all hold such a lease. The count rises only on admission, and falls on release or when an active lease expires. A bounce moves a lease from `submitted` back to `active` and leaves the count alone, so no path exceeds the cap. An item started without `--paths` writes nothing and holds no lease, so it counts only toward its coordinator's cap (change 5).
- **`land.py init ... --cap N` and `land.py cap N`.** These store a `cap` key in the contract. `0` clears it. Absent means no cap.
- **Admission.** `lease claim` admits a lease inside its existing transaction. After the overlap check, it counts unreleased leases. When the count is at the cap it refuses with `repository at its cap: 3 of 3 changes in flight (docs/D1, engine/D1, engine/D2)`. The holders in the message are the facts a coordinator needs to say who it waits on.
- **`lease renew`.** Renewing a lease that has not expired works as today. Renewing an expired lease is a new admission in the same `BEGIN IMMEDIATE` transaction. It refuses when any other holder's unreleased lease overlaps, with `L1 expired and L2 held by engine/D1 now covers README.md; claim again after it is released`. It also refuses when the cap is full, with the admission message. An expired lease with no overlap and room under the cap renews.
- **`land.py status --holder <holder>`.** It prints the entries of exactly that holder in id order, one per line, with the SHA: `E1 landed (engine/D1, 1ebeef80f473) as 0f3a9c1d2e4b`, `E3 awaiting-merge (engine/D2, 34a807b9493f) <pr url>`, `E4 bounced (engine/D3, a1f7ed73e60c): conflict with trunk`. With `--holder` ending in `/`, it matches that prefix.
- **`lease check --holder <holder> --paths <paths>`.** It runs the admission test of `lease claim` without claiming. It prints `free`, or each overlapping lease with its holder, or the cap message. `watch` uses it to recheck a blocked ticket's requested paths (change 5).
- **`lease renew --if-live`** renews only a lease that has not expired, and otherwise refuses with `L4 expired at <time>`. `watch` uses it, so re-admitting an expired lease is always an explicit step a coordinator takes after it stops the old worker (change 6).
- **`lease renew` names why it refused.** The refusal names the lease's state, as in `L4 is submitted; the queue holds it`, `L4 is released`, the overlap message, or the cap message. `watch` maps each to its own line (change 6).
- **`land.py status`.** It adds `changes in flight: 3 of 4` to its line when a cap is set.
- **`land.py mode`.** Its success line ends with `; leases held by docs/D1, engine/D3` when any lease is unreleased, so the person changing the mode sees whom it affects.
- **Files.** `t3/added/landing/scripts/land.py`, `t3/added/landing/SKILL.md`, `tests/landing/`, generated `skills/landing/`, its fragment.
- **Tests.** With a cap of 2, claims by `a/D1` and `b/D1` succeed, `c/D1` is refused with the literal message, and after `lease release L1` the same claim returns `L3`. Ten processes claiming disjoint paths at once under a cap of 3 produce exactly 3 leases. A submitted lease counts toward the cap, and a bounce leaves the count unchanged. An expired lease does not count. Renew of an expired lease after another holder's overlapping claim exits 1 with the literal message, and `lease list` shows one lease on the path. Renew of an expired lease after the cap filled exits 1. Renew of a submitted lease and of a released lease each print their own message. `lease check` on paths a released lease held prints `free`, and on paths a new lease holds names that lease. `status --holder engine/D2` lists only that holder.
- **Coordinator use.** The coordinator that opens first on a repository sets the cap with the mode, as `land.py init --trunk main --mode merge --cap 4`. The landing skill's setup section says the cap belongs to the repository like the mode does.
