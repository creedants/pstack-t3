---
name: verify-landing-queue
description: "Check the landing queue and its path leases against the TLA+ model in verification/landing-queue/. Use when a change touches t3/added/landing/scripts/landing_lib/, before you trust a claim about lease overlap, queue order, one trunk writer, what merges, or recovery after a crash."
---

# Verify the landing queue

`verification/landing-queue/LandingQueue.tla` is a hand-written TLA+ model of the
landing queue and its path leases. TLC checks it exhaustively inside a stated
bound. A passing run means the promises below hold for every ordering of the
modelled actions in that bound. It says nothing about code the model does not
have an action for.

Read every result with the pstack `/statechart` skill. It turns an invariant
violation into the shortest path to it in plain words, and draws the state
graph when nothing breaks. Use it rather than reading `tlc.out` yourself.

## Does my change touch the verified code?

Run the check when a change touches any of these, in
`t3/added/landing/scripts/landing_lib/`, and read the action that covers it:

| Code | Action in the model | Promise |
| --- | --- | --- |
| `leases.py`: `lease_claim`, `lease_renew`, `lease_release`, `lease_widen` | `Claim`, `Renew`, `Release`, `Widen` | P1, P6 |
| `_leases/store.py`: `admission`, `in_flight`, `arm_reservations`, `take` | `Clash`, `InFlight` inside every lease write | P1 |
| `_leases/owner.py`: `check_owner`, `raise_floor` | `StaleAllowed`, `RaiseFloor` | P2 |
| `_queue/entries.py`: `submit` | `Submit` | P2 |
| `drain.py`: `land` and the `.queue.lock` flock | `DrainStart`, `DrainEnd` | P3a |
| `_queue/entries.py`: `publish` | `Publish`, `PublishRefused` | P3b |
| `_queue/entries.py`: `attempt`, `reconcile` | `BeginPublish`, `SettleLanded`, `Reconcile` | P3b, P4, P5 |
| `_queue/integration.py`: `apply`, `check` | `Apply`, `Check` | P4 |
| `_queue/prs.py`: `ensure_pr` | `OpenPr` | P4 |
| `_merge/required.py`: `request_merge`; `_merge/settle.py`: `take_pr` | `ForgeMerges` | P4 |
| `_queue/entries.py`: `settle_bounced` | `Bounce` | P5 |

A change anywhere else in `landing_lib/` does not need this check. Neither does
a change to `land.py`'s argument parsing or to a message string, unless the
string is the one a promise names.

If the change moves a transaction boundary, adds or removes a `Store.tx()`,
adds an external call between two transactions, or changes when the queue lock
is taken, the model needs updating even when the table above does not name the
function. Say which transaction moved.

## Run it

One command, from the repository root:

```sh
python3 skills/landing/scripts/land.py slot -- python3 verification/landing-queue/check.py
```

It runs three base configs and every config in `variants/`, and exits 0 only
when each answers what the model says it should. It prints one row per config
with the states generated, the distinct states found, and the wall time.

Bounds. TLC explores with one worker, in breadth-first order, at most `Depth`
steps from the initial state. `Depth` is 13 in the three base configs and is set
per config in the `CONSTANTS` block. The base configs use `Holders` = 2,
`Paths` = 2, `Leases` = 2, `Entries` = 2, `Commits` = 3. What is left out:
reservations and shares, contests, the cap, `--ttl-hours` arithmetic beyond a
lease being live or expired, batching more than one entry per attempt, `human`
and `local` modes, the size and author policy checks, and every forge detail
other than head, required checks, and the merge itself.

A base config must report `holds`. A variant must report `breaks <invariant>`
with the invariant its `EXPECT:` line names.

## Tell a real bug from a modelling error

The model is written by hand from the code, so it can describe the design and
miss the code. Before you report a bug, check these in order.

1. Does the model's action match the function the map above names, line for
   line? A missing guard, a guard in the wrong order, or two transactions
   folded into one action is a modelling error. Fix the model, then re-run.
2. Is the counterexample a sequence the code can really produce? Read each step
   against the function it names. A step the code cannot take is a modelling
   error.
3. Only then is it a bug in the code. Do not weaken the invariant to make it
   pass and do not change `landing_lib/` to match the model. Write the
   counterexample as a numbered list of steps in the code's words, reproduce it
   in a throwaway repository and store, and file it as `Correctness:` or
   `Safety:`.

A variant that reports `holds` is a modelling error in the variant. A base
config that reports `breaks` is a finding against the code, or a modelling
error, decided by steps 1 and 2.

## When the change is intended

1. Read the code at the new commit and the action that covers it.
2. Update the action so it still stands for one transaction or one external
   call. If a guard moved, move it.
3. If a promise changed, change the sentence in `README.md` and in the model's
   `===` section together, and add or change a variant in `variants/` that shows
   the new promise failing when its check is removed. A promise with no failing
   variant is unproven and belongs in the report as a gap.
4. Run the check. Every base config `holds` and every variant `breaks` its own
   invariant.
5. Move `Verified at` below to the new commit, and only after steps 1 to 4 pass.

## Coverage record

- Model: `verification/landing-queue/LandingQueue.tla`, configs beside it and in `variants/`.
- Runner: `verification/landing-queue/check.py`.
- Verified at: `91251ca92` (the commit this model was written against).
- Last full run: 14 runs, every base config held and every variant broke its
  invariant, in one minute and 24 seconds through the slot. States: leases
  340,784 generated and 17,950 distinct; publish 4,191,244 and 665,552; merge
  4,558,920 and 673,972.

A change to a file in the table above after `Verified at` means the model may
describe an older code. Run the check; if it passes and the change is intended,
move `Verified at` forward. If it fails, work through "Tell a real bug from a
modelling error".

For the reverse case, a change to verified code that never re-ran the check, see
the `/maintain-formal-verification-skill` skill.

## Known limits

TLA+ checks the model, not the Python. A pass covers the actions in the map and
nothing else. The model treats Git, GitHub, and the clock as external actors
that can move trunk, post a check, or let a lease expire between any two
actions. A hand-written model drifts from the code unless someone reruns it,
which is what `Verified at` is for.
