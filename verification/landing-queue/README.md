# The landing queue model

`LandingQueue.tla` models the landing queue and its path leases. `check.py` runs
TLC over it and over the broken variants. Run it from the repository root:

```sh
python3 skills/landing/scripts/land.py slot -- python3 verification/landing-queue/check.py --scratch-parent <parent>
```

`--scratch-parent` takes the machine's scratch directory, the one a brief or the
restaurant's `scratchDir` names. It is required, and `check.py` has no default.
Any existing writable directory works, so when neither names one, make one with
`mktemp -d` and delete it afterwards. For each config `check.py` creates a new
directory under the parent for TLC's own state and deletes that directory, by its
exact path, when TLC exits. Without the argument it prints a usage error naming
`--scratch-parent` and runs nothing.

The skill at `.claude/skills/verify-landing-queue/SKILL.md` says when to run it,
how to read a result, and how to keep it in step with the code.

TLA+ checks this model. It does not read the Python. A pass says the promises
hold for the modelled actions inside the stated bound. An unchanged model passes
after a real code regression, so a pass alone does not show the model matches
the current code.

## What is modelled

One action is one transaction (`Store.tx()`) or one external call or actor.
`Crash` can fire between any two actions of a run. It loses the flock and the
run's memory, and it keeps store rows, trunk, the forge, and the moved worktree.
Where parts stay folded in one action, the map says why a death between them
cannot change a promise.

Five base configs run the model.

| Config | Scope | Entries | Leases | Generations | TTLs | Clock ticks | Depth | Deepest reached |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `leases.cfg` | lease writes alone | none used | 2 | 2 | 2 | 2 | 12 | 8, exhausted |
| `publish-one.cfg` | publish mode, every lease write | 1 | 2 | 2 | 1 | 1 | 30 | 28, exhausted |
| `merge-one.cfg` | merge mode, small lease set | 1 | 2 | 1 | 1 | 1 | 32 | 29, exhausted |
| `publish.cfg` | publish mode, small lease set | 2 | 2 | 1 | 1 | 1 | 24 | 24, stopped by the bound |
| `merge.cfg` | merge mode, small lease set | 2 | 2 | 1 | 1 | 1 | 18 | 18, stopped by the bound |

The small lease set is `Claim`, `RaiseFloor`, and `ClockTick`. Every base config
has `Holders` = 2 and `Paths` = 2. `publish-one.cfg` and
`publish.cfg` have the commits c0, c1, c2, c4, c5. `merge-one.cfg` and
`merge.cfg` have c0, c1, c2, c4, c5, c6, s1, s2. `leases.cfg` has c0 only.

## The action-to-code map

The header of `LandingQueue.tla` carries this map with the reason for each fold.
Paths are under `t3/added/landing/scripts/landing_lib/`.

| Action | File and function | Stands for |
| --- | --- | --- |
| `Claim` | `leases.py:lease_claim` | transaction: owner check, admission, insert |
| `Renew` | `leases.py:lease_renew` | transaction: owner check, admission when expired, new expiry |
| `Release` | `leases.py:lease_release` | transaction: released, paths kept on the row |
| `Widen` | `leases.py:lease_widen` | transaction: folds the holder's other live leases, latest expiry wins |
| `RaiseFloor` | `_leases/owner.py:raise_floor` | transaction: a floor only rises |
| `ClockTick` | `seam.now` | the wall clock |
| `Submit` | `_queue/entries.py:submit` | transaction: owner, lease, coverage, delete a bounced row, insert, lease submitted |
| `SubmitDuplicate` | `_queue/entries.py:submit` | transaction that returns `already <state>` with no write |
| `DrainStart` | `drain.py:land` | external: the flock. Captures no base. |
| `ReconcileFetch` | `_queue/entries.py:reconcile` | external: the fetch |
| `ReconcileAttempt` | `_queue/entries.py:reconcile` | transaction: one publishing attempt, settled or requeued |
| `ReconcileLanding` | `_queue/entries.py:reconcile` | transaction: requeues every `landing` entry |
| `IntegrationReset` | `_queue/entries.py:attempt`, `_queue/prs.py:build_line`, `build` | external: fetch, base read, worktree reset |
| `ApplyRun` | `_queue/integration.py:apply` | external: cherry-pick and fingerprint compare, worktree only |
| `SettleAlready` | `_queue/entries.py:attempt`, `_queue/prs.py:build` | transaction: `settle_landed` at the run's base in `attempt`, at trunk in `build` |
| `BounceApply` | `_queue/entries.py:attempt`, `_queue/prs.py:build` | transaction: `settle_bounced` |
| `CheckRun` | `_queue/integration.py:check` | external: setup and checks, pass or fail |
| `BounceCheck` | `_queue/entries.py:attempt`, `_queue/prs.py:build` | transaction: `settle_bounced`, nothing lands |
| `DrainEnd` | `drain.py:land` | external: the flock is given back |
| `Crash` | a process death | external: a kill between any two actions |
| `RecordLanding` | `_queue/entries.py:attempt` | transaction: entry `landing` with its candidate |
| `BeginPublish` | `_queue/entries.py:attempt` | transaction: attempt row `publishing`, entry stays `landing` |
| `Publish` | `_queue/entries.py:publish` | external: the push, accepted |
| `PublishRefused` | `_queue/entries.py:publish` | external: the push, refused because the remote moved |
| `AbandonAttempt` | `_queue/entries.py:attempt` | transaction: attempt abandoned, entry requeued, candidate cleared |
| `SettleLanded` | `_queue/entries.py:attempt` | transaction: settles on the remembered push result, no tip compare |
| `ReleaseThenSettle` | no code | the broken order, only under `lease-released-before-settle` |
| `RecordCandidate` | `_queue/prs.py:build` | transaction: entry `awaiting-merge` with its candidate |
| `OpenPr` | `_queue/prs.py:ensure_pr` | external: `gh pr create` |
| `StorePr` | `_queue/prs.py:ensure_pr` | transaction: stores the URL |
| `AbsenceSeen` | `_merge/rollup.py:absent_since` | transaction: records when absence was first seen |
| `GraceEnds` | `_merge/rollup.py:absence_waits` | external: five minutes pass |
| `RequestMerge` | `_merge/required.py:request_merge` | external: `gh pr merge --auto --match-head-commit` |
| `ChecksPost` | the forge | external: required checks start |
| `ChecksFinish` | the forge | external: required checks pass or fail |
| `HeadMoves` | the forge | external: a push to the pull request's branch |
| `ForgeCompletes` | the forge | external: the merge, by merge, squash, or rebase |
| `SettleMerged` | `_merge/settle.py:take_pr` | transaction: `settle_landed` at the merged commit, pauses when the tree differs. `take_pr` bounces instead when the merged head is not the candidate; the model reaches that only under `no-match-head-commit` |
| `BounceOpenPr` | `_merge/settle.py:bounce_open_pr` | transaction: `settle_bounced` on failed checks |
| `OutsiderPush` | another writer on trunk | external: a forward push only |

`ClockTick` replaces the old `Expire` action. Expiry is now an ordered number, so
a widen can compare two leases. A lease stops being live when the clock passes
its stored expiry.

## The promises

Each is one invariant, one plain sentence, and the harm it prevents.

**P1 `NoOverlappingLeases`.** Two holders never hold live overlapping leases, and
a submitted lease counts as live whatever its expiry. Harm prevented: two agents
rewrite the same file and one silently loses its work.

**P2 `NoStaleWrite`.** A write below a holder prefix's owner floor never changes
the store. Harm prevented: a replaced coordinator puts work in the queue and a
change nobody owns reaches trunk.

**P3a `OneIntegration`.** At most one drain run integrates at a time. Harm
prevented: two runs share one integration worktree, so a check runs against a
tree that is not the candidate it reports on.

**P3b `LandedIsOnTrunk`.** Every entry the store records as landed is on trunk.
Harm prevented: the queue reports a change as landed and the change is lost with
no bounce.

**P3c `TrunkNeverRewinds`.** A queue push or a forge merge never takes a commit
off trunk. Harm prevented: the queue overwrites a commit another writer pushed.

**P4 `CheckedHeadOnly`.** What landed holds the entry's reviewed change, what the
queue put on trunk has a tree that a passing local check ran on unless the queue
paused to say otherwise, and the forge merged only the checked head on passed
checks or on absent checks after the grace. Harm prevented: a commit nobody
reviewed, or a commit whose checks failed or never ran, reaches trunk unnoticed.

This promise compares trees, not commits, because squash and rebase merges are
allowed and the landed commit can differ from the candidate. An entry settled as
already in trunk made no queue write to trunk, so it needs only a tree that holds
its change. A repository whose checks post after the grace still merges, and P4
accepts that. So does the code.

**P5a `NoStalledEntry`.** Once reconcile finishes, no entry is `landing` and no
attempt is `publishing`. Harm prevented: a run dies and an entry is stuck with no
bounce.

**P5b `EntryKeepsItsLease`.** An entry in flight always holds a lease in flight.
Harm prevented: another holder claims the paths under a queued entry.

**P5c `NoDoubleLanding`.** The same entry's change reaches trunk through the queue
at most once. Harm prevented: a crash and a reconciling run land the change twice.

**P6 `WidenKeepsCoverage`.** A widen's kept lease lasts at least as long as every
lease it folded. Harm prevented: a fold shortens an agent's coverage and another
holder claims the paths earlier than the agent was promised.

## Broken variants

A check you have never seen fail proves nothing. Each config in `variants/` names
one invariant on its `EXPECT:` line and breaks one rule. `Break` selects the rule.

| Variant | Depth | Invariant | What it changes |
| --- | --- | --- | --- |
| `no-clash` | 8 | P1 | `admission` stops refusing a second holder's live lease |
| `no-owner-check` | 8 | P2 | `check_owner` stops refusing a generation below the floor |
| `submit-no-owner-check` | 6 | P2 | `submit` alone stops calling `check_owner` |
| `widen-drops-live-coverage` | 8 | P6 | `lease_widen` as it was at the parent of `1adb24175` |
| `two-integrators` | 6 | P3a | `drain.py` stops taking `.queue.lock`. The model has one process, so this shows the lock refuses a second run and explores no interleaving of two runs |
| `settle-before-publish` | 16 | P3b | `attempt` settles the entry before the push |
| `publish-no-base-check` | 16 | P3c | `publish` uses a bare `--force`. A plain `git push` still refuses a non-fast-forward update. `--force-with-lease=refs/heads/<trunk>:<base>` also refuses when the remote moved, where a bare force overwrites it |
| `no-fingerprint` | 16 | P4 | `integration.apply` stops comparing the reviewed fingerprint |
| `ignore-failed-check` | 16 | P4 | `attempt` publishes after `integration.check` returned a failure |
| `merge-without-checks` | 18 | P4 | `request_merge` and the forge stop waiting for absent or pending checks |
| `absence-counts-drains` | 18 | P4 | `request_merge` as it was at the parent of `1259ac35c` |
| `no-tree-check` | 18 | P4 | `take_pr` stops pausing on a merged tree that differs from the candidate's |
| `no-match-head-commit` | 18 | P4 | `gh pr merge` loses `--match-head-commit` |
| `no-reconcile-attempt` | 16 | P5a | `reconcile` stops reading the attempt table |
| `lease-released-before-settle` | 16 | P5b | the lease is released in its own transaction before the settling one |
| `no-already-landed-check` | 24 | P5c | `reconcile` negates its git answer, so an attempt git says is on trunk is requeued and one git says is not on trunk settles as landed, and `apply` stops returning already-in-trunk |

Every earlier variant still means something and none was removed. Two changed.
`publish-no-base-check` now names P3c, because one run with a stale base loses no
recorded landing, and what a bare force harms is another writer's commit.
`no-already-landed-check` now says what negating `recognised` does.

## What history shows

Two variants model the rule at the parent of a real fix. Both invariants state the
promise the fix made, and the model was written after the fixes, so neither run
is a discovery. They show the model can express each defect.

**`widen-drops-live-coverage` models the rule at the parent of `1adb24175`.** That
commit added one line to `lease_widen`, `expires = max([expires, *(lease["expires"]
for lease in orphans)])`. Before it, a live kept lease kept its own expiry and an
expired kept lease took the TTL. The variant restores that rule on ordered
expiries, and `WidenKeepsCoverage` fails when the folded lease outlasts the kept
one. The brigade changes in that commit are not modelled.

**`absence-counts-drains` models the rule at the parent of pull request #474**,
commit `1259ac35c`. Before it, absent checks merged once an earlier drain had seen
the absence, so two quick land runs merged a pull request whose required checks
had not posted. The variant marks absence seen in an earlier drain and lets the
request through. Pending checks wait, as they did. `CheckedHeadOnly` fails because
it requires the grace, which is the rule the commit added. The grace is a boolean
and not five minutes. The old rule's wait for an entry ahead in the line is not
modelled, because the model's line holds one entry.

**`merge-without-checks` is related to pull request #474 and is not a replay.** It
permits merging with absent or pending checks. Pending checks already waited
before that commit.

## Bounds

TLC explores with one worker in breadth-first order. The bound is enforced in
`Next`, by `BoundNext == steps < Depth /\ Next /\ steps' = steps + 1`. There is no
`CONSTRAINT`. A state constraint stops expansion but still evaluates invariants on
successors, so it can report a violation at `Depth + 1` transitions. This bound
cannot.

`VIEW View` leaves `steps` out of a state's identity. Breadth-first search reaches
each state first by a shortest path, so TLC explores exactly the states reachable
in at most `Depth` transitions. Distinct counts below are distinct under the view.
The `deepest` column is TLC's reported depth less one, because TLC counts the
initial state as level 1. For a variant it is how far the search had got when the
violation stopped it.

A deepest value below `Depth` means TLC ran out of new states for those constants.
The two configs with two entries hit the bound, so they are not exhaustive.
Inferred by hand count from the action list, not measured: one full merge takes
about 16 transitions and a second entry's merge about 13 more, so `merge.cfg` at
18 does not reach a second full merge. No set of variants shows a bound is
sufficient, and this README makes no such claim.

## What the check does not cover

- Reservations, shares, the cap, and contests, including `unheld_queued`.
- Hour arithmetic beyond ordered clock ticks. Expiries are small numbers.
- Batches. One attempt carries one entry.
- `human`, `local`, and `push` modes, and the size and author policy checks.
- Rebuilt and stacked lines. Merge mode holds one `awaiting-merge` entry at a time,
  and a candidate whose base moved before the request waits with no rebuild.
- The review pause, the Infrastructure pause, `land.py resume`, and `rewound`. A
  paused queue stays paused in the model.
- A human merge of a moved head, and a pull request closed by hand.
- A rewound trunk. `OutsiderPush` moves trunk forward only, so P3c is about the
  queue's own writes and says nothing about another writer's force push.
- Interleaved drain runs, and required checks per head. One check state covers a
  pull request, and `HeadMoves` never returns to the candidate.
- `Renew`, `Release`, and `Widen` beside the queue, except in `publish-one.cfg`.
  No merge mode config runs them.
- A second generation beside the queue, except in `publish-one.cfg` and the
  `submit-no-owner-check` variant.
- Three leases. A widen folds at most one other lease.
- The base-moved retry loop in `_merge/forge.py`. `RequestMerge` stands for the
  merge request only; the plain `gh pr merge` retries after a base-moved refusal
  have no action. `OutsiderPush` can move trunk between modelled actions.
- The helpers the model assumes. `foundation.overlaps` is treated as a set
  intersection over two atomic, non-nesting paths; the real function is
  prefix-aware, so nested paths overlap and the model cannot show it.
  `foundation.covered` is a subset test, `foundation.fingerprint` is
  equal exactly when a replay carries the same change, and `Store.tx` is all or
  nothing. A change to any of them can invalidate a promise without moving a
  transaction boundary.

## Result of this round

21 runs. Every base config holds and every variant breaks its named invariant. The
rows of the last run add up to about 168 seconds with one TLC worker, and the whole
command took 2m48s from start to finish through the slot. The counts below are that
run.

| Config | States | Distinct |
| --- | --- | --- |
| `leases.cfg` | 133,513 | 7,714 |
| `merge-one.cfg` | 6,140,551 | 816,762 |
| `merge.cfg` | 4,570,849 | 898,400 |
| `publish-one.cfg` | 6,408,995 | 631,184 |
| `publish.cfg` | 2,668,895 | 633,592 |
