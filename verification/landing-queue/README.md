# The landing queue model

`LandingQueue.tla` models the landing queue and its path leases. `check.py` runs
TLC over it and over one broken variant per promise. Run it from the repository
root:

```sh
python3 skills/landing/scripts/land.py slot -- python3 verification/landing-queue/check.py
```

The skill at `.claude/skills/verify-landing-queue/SKILL.md` says when to run it,
how to read a result, and how to keep it in step with the code.

## What is modelled

One action in the model is one transaction (`Store.tx()`) or one external call
(`git`, `gh`, the clock, another writer on trunk). The header of
`LandingQueue.tla` maps every action to the file and function it stands for.
Where the code runs several of those in a row with no transaction around them,
the model has one action for each, and `Crash` may fire between any two.

Three configs cover the base behaviour. `leases.cfg` runs the lease writes
alone. `publish.cfg` runs the queue in publish mode: claim, submit, the
integration worktree, the check, the transaction that records the attempt, the
lease-guarded push, and recovery after a crash. `merge.cfg` runs the queue in
merge mode, where a pull request carries the checked candidate and the forge
merges it.

Two actors are modelled rather than derived. `Expire` lets a lease pass its
stored expiry. `OutsiderPush` moves trunk under a running drain, which is the
only reason `publish` passes `--force-with-lease=refs/heads/<trunk>:<base>`.

## The promises

Each is one invariant, one plain sentence, and the harm it prevents.

**P1 `NoOverlappingLeases`.** Two holders never hold live overlapping leases. A
submitted lease counts as live whatever its expiry. Harm prevented: two agents
rewrite the same file and one lands over the other, so one of them silently
loses its work.

**P2 `NoStaleWrite`.** A write below a holder prefix's owner floor never changes
the store. Harm prevented: a coordinator that a replacement took over puts work
into the queue after the replacement began, and a change nobody now owns reaches
trunk.

**P3a `OneIntegration`.** At most one drain run integrates at a time. Harm
prevented: two runs share the one integration worktree, so a check runs against
a tree that is not the candidate it reports on.

**P3b `LandedIsOnTrunk`.** Every entry the store records as landed is on trunk.
Harm prevented: the queue reports a change as landed while trunk does not hold
it, and the change is lost with no bounce.

**P4 `CheckedHeadOnly`.** What reaches trunk through the queue is the reviewed
head, and the checks that passed ran on the content that landed. Harm
prevented: a merge takes a commit nobody reviewed, or a commit whose required
checks never ran.

**P5a `NoStalledEntry`.** Once the queue has reconciled, no entry is left
`landing` or `publishing`. Harm prevented: a run dies mid-flight and the entry
is stuck forever with no bounce and no lease.

**P5b `EntryKeepsItsLease`.** An entry in flight always holds a lease that is in
flight. Harm prevented: an entry is stuck with its lease gone, so nobody can
recover it and another holder can claim the paths under it.

**P5c `NoDoubleLanding`.** The same entry's change reaches trunk at most once.
Harm prevented: a crash and a reconciling run land the change twice.

**P6 `WidenKeepsCoverage`.** A widen never drops live coverage it folded. Harm
prevented: an agent widens its lease to cover a new path, the fold releases the
lease that covered the old ones, and the agent can no longer submit.

## Why every promise has a broken variant

A check you have never seen fail proves nothing. Each config in `variants/`
names one promise in its `EXPECT:` line and removes or reorders one thing:

| Variant | Promise | What it removes |
| --- | --- | --- |
| `no-clash` | P1 | `admission` stops refusing a second holder's live lease |
| `no-owner-check` | P2 | `check_owner` stops refusing a generation below the floor |
| `widen-drops-live-coverage` | P6 | `lease_widen` keeps only its own expiry, not the latest folded one |
| `two-integrators` | P3a | `drain.py` stops taking `.queue.lock` |
| `publish-no-base-check` | P3b | `publish` drops `--force-with-lease=...:<base>` |
| `settle-before-publish` | P3b | `attempt` settles the entry before the push |
| `no-fingerprint` | P4 | `integration.apply` stops comparing the reviewed fingerprint |
| `merge-without-checks` | P4 | `request_merge` merges while required checks have not posted |
| `no-reconcile-attempt` | P5a | `reconcile` stops reading the attempt table |
| `lease-released-before-settle` | P5b | `settle_landed` releases the lease in its own transaction first |
| `no-already-landed-check` | P5c | the two places that recognise work already on trunk stop asking git |

### Two variants replay real history

**`merge-without-checks` replays pull request #474**, commit `1259ac35c`,
"Landing: absent required checks wait on wall time, not a drain count". Before
it, `request_merge` merged a pull request whose required checks had not posted
once two land runs had happened, because absence was counted per drain rather
than on the clock. The queue merged a change whose checks never ran. The variant
allows the forge to merge while the check state is `absent` or `pending`, which
is the rule that commit removed, and `CheckedHeadOnly` fails at once.

**`widen-drops-live-coverage` replays the widen fold**, commits `adefef6d4`,
`1adb24175`, and `51b47dc65` (pull request #555), "Leases: widen an item's lease
in place". `lease_widen` folds a holder's other live leases into the one it
keeps and releases them in the same transaction. Before `1adb24175` the kept
lease kept its own expiry instead of the latest of its own and every folded one,
so a fold could release live coverage and leave the holder with none. The
variant restores that rule and `WidenKeepsCoverage` fails.

## What the check does not cover

Reservations, shares, the cap, contests, the `--ttl-hours` arithmetic beyond
live or expired, more than one entry per attempt, `human` and `local` modes, the
size and author policy checks, and every forge behaviour other than head,
required checks, and the merge itself. TLC explores at most `Depth` steps from
the initial state, one worker, breadth-first. The three base configs set `Depth`
13 and each variant sets its own. The base configs use two holders, two paths,
two leases, two entries, and three commits. The whole check takes about a minute
and a half through the slot.
