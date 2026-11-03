---- MODULE LandingQueue ----
\* A hand-written model of the landing queue and its path leases.
\*
\* One action stands for one transaction or one external call in
\* t3/added/landing/scripts/landing_lib/. Where a process death between two
\* parts cannot change a promise, the parts stay one action and the map says why.
\* TLA+ checks this model. It does not read the Python.
\*
\* The action-to-code map. TX is one Store.tx(). EXT is one external call or
\* actor. PROC is a step inside one process that writes nothing durable.
\*
\* Leases
\*   Claim             leases.py:lease_claim             TX  check_owner, admission, INSERT active
\*   Renew             leases.py:lease_renew             TX  check_owner, admission when expired, new expiry
\*                     An `if_live` refusal on an expired lease writes nothing, so it has no action.
\*   Release           leases.py:lease_release           TX  state released, the row keeps its paths
\*   Widen             leases.py:lease_widen             TX  folds the holder's other live leases, latest expiry wins
\*                     A TTL that leaves the lease expired raises in the TX and writes nothing, so it has no action.
\*   RaiseFloor        _leases/owner.py:raise_floor      TX  a floor only rises
\*   ClockTick         the wall clock, seam.now          EXT every lease past its stored expiry stops being live
\*
\* Submit
\*   Submit            _queue/entries.py:submit          TX  check_owner, lease and coverage checks, DELETE a bounced
\*                                                           row, INSERT queued, lease submitted
\*                     The git reads, the fingerprint, and the pin run before the TX and stay folded. They write
\*                     no store row, the sha they read is pinned, and the TX rechecks the owner, the lease, and
\*                     its coverage, so a death before the TX leaves the store as it was.
\*   SubmitDuplicate   _queue/entries.py:submit          TX  `already <state>` returns from inside the TX, no write
\*
\* Drain, both modes
\*   DrainStart        drain.py:land                     EXT flock on .queue.lock, LOCK_EX | LOCK_NB. A paused queue
\*                                                           returns early. It captures no base.
\*   ReconcileFetch    _queue/entries.py:reconcile       EXT `git fetch` of trunk
\*   ReconcileAttempt  _queue/entries.py:reconcile       TX  one publishing attempt, settled when git says the
\*                                                           candidate is an ancestor of trunk, otherwise requeued
\*   ReconcileLanding  _queue/entries.py:reconcile       TX  requeues every `landing` entry, candidate cleared
\*   IntegrationReset  _queue/entries.py:attempt or      EXT fetch, read the base, `integration.reset(base)`
\*                     _queue/prs.py:build_line, build       Folded. They move only the tracking ref and the
\*                                                           integration worktree, and every run repeats them.
\*   ApplyRun          _queue/integration.py:apply       EXT cherry-pick, fingerprint compare. Moves the worktree only.
\*   SettleAlready     entries.py:attempt, prs.py:build  TX  settle_landed at the run's base in
\*                                                           `attempt`, at trunk in `build`. The model
\*                                                           has the attempt's base and calls it `base`.
\*   BounceApply       entries.py:attempt, prs.py:build  TX  settle_bounced with a fresh expiry. The model's
\*                                                           clock unit is a tick, not an hour, so the code's
\*                                                           fixed six hours is Max(Ttls). The clean_bounced call before it stays
\*                                                           folded: a death leaves the entry queued, and no model
\*                                                           step reads the landing branch it deletes.
\*   CheckRun          _queue/integration.py:check       EXT setup and every check, pass or fail. A check that dirties
\*                                                           the worktree raises LandError, which leaves the store
\*                                                           exactly as Crash does, so it has no action of its own.
\*   BounceCheck       entries.py:attempt, prs.py:build  TX  settle_bounced with "checks failed", nothing lands
\*   DrainEnd          drain.py:land, the finally        EXT handle.close() gives the flock back
\*   Crash             the process dying                 EXT a kill between any two actions. Store rows, trunk, the
\*                                                           forge, and the moved worktree survive. The flock and the
\*                                                           run's memory do not.
\*
\* Publish mode
\*   RecordLanding     _queue/entries.py:attempt         TX  entry landing with its candidate
\*   BeginPublish      _queue/entries.py:attempt         TX  INSERT attempt publishing. The entry stays `landing`.
\*   Publish           _queue/entries.py:publish         EXT `git push --force-with-lease` accepted. The is-ancestor
\*                                                           read before it and the fetch after it stay folded: both
\*                                                           are read-only and the next run fetches first.
\*   PublishRefused    _queue/entries.py:publish         EXT the same push refused because the remote moved
\*   AbandonAttempt    _queue/entries.py:attempt         TX  attempt abandoned, entry requeued, candidate cleared
\*   SettleLanded      _queue/entries.py:attempt         TX  settles on the remembered push result. Trunk may have
\*                                                           moved on since, and this does not compare tips.
\*   ReleaseThenSettle no code                               the broken order only, Break = lease-released-before-settle
\*
\* Merge mode
\*   RecordCandidate   _queue/prs.py:build               TX  entry awaiting-merge with its candidate. The force push of
\*                                                           landing/e<n> before it stays folded: a death leaves the
\*                                                           entry queued and the next build pushes again.
\*   OpenPr            _queue/prs.py:ensure_pr           EXT `gh pr create`. The adoption read and the absent-branch
\*                                                           push before it are idempotent and stay folded.
\*   StorePr           _queue/prs.py:ensure_pr           TX  stores the URL. After a death the next poll adopts the
\*                                                           pull request and stores it.
\*   AbsenceSeen       _merge/rollup.py:absent_since     TX  records when absence was first seen for this candidate
\*   GraceEnds         _merge/rollup.py:absence_waits    EXT five minutes pass after that record
\*   RequestMerge      _merge/required.py:request_merge  EXT `gh pr merge --auto --match-head-commit`. The checks read,
\*                                                           the fetch, and same_tree stay folded because they are
\*                                                           read-only. The note TX after it stays folded because no
\*                                                           promise reads that note.
\*   ChecksPost        the forge                         EXT required checks start on the pull request's head
\*   ChecksFinish      the forge                         EXT required checks pass or fail
\*   HeadMoves         the forge                         EXT someone pushes to the pull request's branch
\*   ForgeCompletes    the forge                         EXT the merge. Merge, squash, or rebase, so the merged commit
\*                                                           can differ from the candidate.
\*   SettleMerged      _merge/settle.py:take_pr          TX  settle_landed at the merged commit. A tree that differs
\*                                                           from the candidate also pauses the queue. The state read,
\*                                                           fetch, tree compare, and branch delete before it stay
\*                                                           folded: the reads are read-only, delete_queue_branch
\*                                                           tolerates a gone branch, and a death leaves the entry
\*                                                           awaiting-merge for the next poll to repeat them all.
\*   BounceOpenPr      _merge/settle.py:bounce_open_pr   TX  settle_bounced on failed checks. The disarm, close, and
\*                                                           delete before it stay folded: after a death take_pr
\*                                                           finds the pull request closed and settles the same bounce.
\*
\* The environment
\*   OutsiderPush      another writer on trunk           EXT a forward push only. A rewound trunk is not modelled.
\*
\* Break names a broken variant. `none` is the code as it is. See README.md.
\*
\* `checkedAt`, `publishes`, `staleWrite`, `droppedCoverage`, `trunkRewound`,
\* and `uncheckedMerge` are history variables, the standard way to state a
\* promise about a transition as an invariant over states. No action reads them.
\* ---------------------------------------------------------------------------

EXTENDS Naturals, FiniteSets, TLC

CONSTANTS Holders, Paths, Leases, Entries, Commits, Generations, Ttls, MaxClock,
          Mode, Scope, Break, Depth

VARIABLES leaseState, leaseHolder, leasePaths, leaseExpiry, clock, floor,
          entryState, entryLease, candidate, landedAs,
          attempt, run, worktree, tip, paused, secondDrain,
          prOpen, prStored, prHead, checkState, armed, mergedAs, absence, seenEarlier,
          checkedAt, publishes, staleWrite, droppedCoverage, trunkRewound, uncheckedMerge,
          steps

leaseVars == <<leaseState, leaseHolder, leasePaths, leaseExpiry>>
entryVars == <<entryState, entryLease, candidate, landedAs>>
drainVars == <<attempt, run, worktree, paused, secondDrain>>
forgeVars == <<prOpen, prStored, prHead, checkState, armed, mergedAs, absence, seenEarlier>>
historyVars == <<checkedAt, publishes, staleWrite, droppedCoverage, trunkRewound, uncheckedMerge>>

\* Every variable but `steps`. TLC keeps one copy of each state under this view,
\* reached by its shortest path, because it searches breadth-first. See README.md.
View == <<leaseVars, clock, floor, entryVars, drainVars, tip, forgeVars, historyVars>>

NoCommit == "none"
NoEntry == "none"

\* The publishing row, if any. Attempt rows already published or abandoned are
\* history that no code reads, so the model keeps only the live one.
NoAttempt == [entry |-> NoEntry, cand |-> NoCommit]

\* What one drain process remembers. A Crash loses all of it. `pc` is where the
\* process stands, including the outcome of the external call it just made.
IdleRun(p) == [pc |-> p, entry |-> NoEntry, base |-> NoCommit, cand |-> NoCommit, fetched |-> NoCommit]

\* ---------------------------------------------------------------------------
\* Static facts.
\* ---------------------------------------------------------------------------

Max(s) == CHOOSE x \in s : \A y \in s : y <= x

PrefixOf(h) == IF h = "docs/A" THEN "docs/" ELSE "engine/"

\* The paths one entry's reviewed commits touch, and its stored fingerprint.
EntryPaths(e) == IF e = "E1" THEN {"a"} ELSE {"b"}
EntryPrint(e) == IF e = "E1" THEN "p1" ELSE "p2"

\* Every commit the model can name. `change` is what the commit adds over its
\* parent, and `anc` is `git merge-base --is-ancestor` as a set. The s commits
\* are squash commits. Each has the same parent and change as the commit it
\* squashes, so a different id with the same tree.
Table == [
  c0 |-> [parent |-> "none", change |-> "p0", anc |-> {"c0"}],
  c1 |-> [parent |-> "c0", change |-> "p1", anc |-> {"c0", "c1"}],
  c2 |-> [parent |-> "c0", change |-> "p2", anc |-> {"c0", "c2"}],
  c3 |-> [parent |-> "c1", change |-> "p1", anc |-> {"c0", "c1", "c3"}],
  c4 |-> [parent |-> "c1", change |-> "p2", anc |-> {"c0", "c1", "c4"}],
  c5 |-> [parent |-> "c2", change |-> "p1", anc |-> {"c0", "c2", "c5"}],
  c6 |-> [parent |-> "s1", change |-> "p2", anc |-> {"c0", "s1", "c6"}],
  s1 |-> [parent |-> "c0", change |-> "p1", anc |-> {"c0", "s1"}],
  s2 |-> [parent |-> "c0", change |-> "p2", anc |-> {"c0", "s2"}]
]

Squashes == {"s1", "s2"}

Parent(c) == Table[c].parent
Change(c) == Table[c].change
Ancestors(c) == Table[c].anc

\* What a commit's tree holds. `same_tree` compares this.
Tree(c) == {Change(a) : a \in Ancestors(c)} \ {"p0"}
SameTree(a, b) == Tree(a) = Tree(b)

\* The commits a cherry-pick can produce on top of this commit.
Replays(c) == {r \in Commits \ Squashes : Parent(r) = c}

\* The commits the forge can produce when it merges `c` into trunk at `t`.
\* `c` itself under rebase or fast-forward, a squash commit, or, when trunk moved,
\* a commit on the new trunk with the same change and a different tree.
MergeCommits(c, t) == {m \in Commits : Parent(m) = t /\ Change(m) = Change(c)}

\* ---------------------------------------------------------------------------
\* Lease helpers.
\* ---------------------------------------------------------------------------

\* `_leases/store.py:in_flight`. A submitted lease counts whatever its expiry.
Live(l) == leaseExpiry[l] > clock
InFlight(l) == leaseState[l] = "submitted" \/ (leaseState[l] = "active" /\ Live(l))

Overlaps(a, b) == a \cap b # {}

\* `_leases/store.py:admission`.
Clash(h, ps) == \E l \in Leases :
                  InFlight(l) /\ leaseHolder[l] # h /\ Overlaps(leasePaths[l], ps)
Admitted(h, ps) == Break = "no-clash" \/ ~Clash(h, ps)

\* `_leases/owner.py:check_owner`.
Stale(h, g) == g < floor /\ PrefixOf(h) = "docs/"
OwnerAllowed(h, g) == Break = "no-owner-check" \/ ~Stale(h, g)

\* Lease rows take increasing ids, so a claim takes the lowest free one.
FreeLease == CHOOSE l \in {m \in Leases : leaseState[m] = "free"} :
               \A m \in Leases : leaseState[m] = "free" => l <= m

\* ---------------------------------------------------------------------------
\* Init
\* ---------------------------------------------------------------------------

Init ==
  /\ leaseState = [l \in Leases |-> "free"]
  /\ leaseHolder = [l \in Leases |-> "none"]
  /\ leasePaths = [l \in Leases |-> {}]
  /\ leaseExpiry = [l \in Leases |-> 0]
  /\ clock = 0
  /\ floor = 1
  /\ entryState = [e \in Entries |-> "none"]
  /\ entryLease = [e \in Entries |-> Max(Leases)]
  /\ candidate = [e \in Entries |-> NoCommit]
  /\ landedAs = [e \in Entries |-> NoCommit]
  /\ attempt = NoAttempt
  /\ run = IdleRun("idle")
  /\ worktree = "c0"
  /\ tip = "c0"
  /\ paused = FALSE
  /\ secondDrain = FALSE
  /\ prOpen = [e \in Entries |-> FALSE]
  /\ prStored = [e \in Entries |-> FALSE]
  /\ prHead = [e \in Entries |-> NoCommit]
  /\ checkState = [e \in Entries |-> "absent"]
  /\ armed = [e \in Entries |-> FALSE]
  /\ mergedAs = [e \in Entries |-> NoCommit]
  /\ absence = [e \in Entries |-> "none"]
  /\ seenEarlier = [e \in Entries |-> FALSE]
  /\ checkedAt = [e \in Entries |-> NoCommit]
  /\ publishes = [e \in Entries |-> 0]
  /\ staleWrite = FALSE
  /\ droppedCoverage = FALSE
  /\ trunkRewound = FALSE
  /\ uncheckedMerge = FALSE
  /\ steps = 0

\* ---------------------------------------------------------------------------
\* Leases.
\* ---------------------------------------------------------------------------

Claim(h, ps, ttl, g) ==
  /\ ps # {}
  /\ \E l \in Leases : leaseState[l] = "free"
  /\ OwnerAllowed(h, g)
  /\ Admitted(h, ps)
  /\ LET l == FreeLease IN
       /\ leaseState' = [leaseState EXCEPT ![l] = "active"]
       /\ leaseHolder' = [leaseHolder EXCEPT ![l] = h]
       /\ leasePaths' = [leasePaths EXCEPT ![l] = ps]
       /\ leaseExpiry' = [leaseExpiry EXCEPT ![l] = clock + ttl]
  /\ staleWrite' = (staleWrite \/ Stale(h, g))
  /\ UNCHANGED <<clock, floor, entryVars, drainVars, tip, forgeVars, checkedAt,
                 publishes, droppedCoverage, trunkRewound, uncheckedMerge>>

Renew(l, ttl, g) ==
  /\ leaseState[l] = "active"
  /\ OwnerAllowed(leaseHolder[l], g)
  /\ (Live(l) \/ Admitted(leaseHolder[l], leasePaths[l]))
  /\ leaseExpiry' = [leaseExpiry EXCEPT ![l] = clock + ttl]
  /\ staleWrite' = (staleWrite \/ Stale(leaseHolder[l], g))
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, clock, floor, entryVars,
                 drainVars, tip, forgeVars, checkedAt, publishes, droppedCoverage,
                 trunkRewound, uncheckedMerge>>

Release(l, g) ==
  /\ leaseState[l] = "active"
  /\ OwnerAllowed(leaseHolder[l], g)
  /\ leaseState' = [leaseState EXCEPT ![l] = "released"]
  /\ staleWrite' = (staleWrite \/ Stale(leaseHolder[l], g))
  /\ UNCHANGED <<leaseHolder, leasePaths, leaseExpiry, clock, floor, entryVars,
                 drainVars, tip, forgeVars, checkedAt, publishes, droppedCoverage,
                 trunkRewound, uncheckedMerge>>

\* The kept expiry is the latest of its own expiry, the TTL when expired, and
\* every folded expiry. Break = widen-drops-live-coverage is the code at the
\* parent of commit 1adb24175, which took no folded expiry into account.
Widen(l, ps, ttl, g) ==
  LET h      == leaseHolder[l]
      folded == {m \in Leases : m # l /\ leaseState[m] = "active" /\ Live(m) /\ leaseHolder[m] = h}
      merged == leasePaths[l] \cup ps \cup UNION {leasePaths[m] : m \in folded}
      own    == IF Live(l) THEN leaseExpiry[l] ELSE clock + ttl
      kept   == IF Break = "widen-drops-live-coverage"
                THEN own
                ELSE Max({own} \cup {leaseExpiry[m] : m \in folded})
  IN
  /\ ps # {}
  /\ leaseState[l] = "active"
  /\ OwnerAllowed(h, g)
  /\ Admitted(h, merged)
  /\ leaseState' = [m \in Leases |-> IF m \in folded THEN "released" ELSE leaseState[m]]
  /\ leasePaths' = [leasePaths EXCEPT ![l] = merged]
  /\ leaseExpiry' = [leaseExpiry EXCEPT ![l] = kept]
  /\ staleWrite' = (staleWrite \/ Stale(h, g))
  /\ droppedCoverage' = (droppedCoverage \/ \E m \in folded : leaseExpiry[m] > kept)
  /\ UNCHANGED <<leaseHolder, clock, floor, entryVars, drainVars, tip, forgeVars,
                 checkedAt, publishes, trunkRewound, uncheckedMerge>>

RaiseFloor(g) ==
  /\ g > floor
  /\ floor' = g
  /\ UNCHANGED <<leaseVars, clock, entryVars, drainVars, tip, forgeVars, historyVars>>

ClockTick ==
  /\ clock < MaxClock
  /\ clock' = clock + 1
  /\ UNCHANGED <<leaseVars, floor, entryVars, drainVars, tip, forgeVars, historyVars>>

\* ---------------------------------------------------------------------------
\* Submit.
\* ---------------------------------------------------------------------------

\* A new entry, or a bounced entry resubmitted by its holder. The old row goes
\* and a new row comes in the same transaction.
Submit(e, l, g) ==
  /\ entryState[e] \in {"none", "bounced"}
  /\ entryState[e] = "bounced" => leaseHolder[l] = leaseHolder[entryLease[e]]
  /\ leaseState[l] = "active"
  /\ Live(l)
  /\ EntryPaths(e) \subseteq leasePaths[l]
  /\ (Break = "submit-no-owner-check" \/ OwnerAllowed(leaseHolder[l], g))
  /\ entryState' = [entryState EXCEPT ![e] = "queued"]
  /\ entryLease' = [entryLease EXCEPT ![e] = l]
  /\ leaseState' = [leaseState EXCEPT ![l] = "submitted"]
  /\ staleWrite' = (staleWrite \/ Stale(leaseHolder[l], g))
  /\ UNCHANGED <<leaseHolder, leasePaths, leaseExpiry, clock, floor, candidate,
                 landedAs, drainVars, tip, forgeVars, checkedAt, publishes,
                 droppedCoverage, trunkRewound, uncheckedMerge>>

\* `already <state>`. Nothing changes, and the view folds it into its own state.
SubmitDuplicate(e) ==
  /\ entryState[e] \notin {"none", "bounced"}
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, drainVars, tip, forgeVars, historyVars>>

\* ---------------------------------------------------------------------------
\* Writes shared by more than one settling transaction.
\* ---------------------------------------------------------------------------

\* settle_bounced. The lease comes back for the fix with a fresh expiry, and
\* the entry's candidate is cleared.
Bounced(e) ==
  /\ entryState' = [entryState EXCEPT ![e] = "bounced"]
  /\ candidate' = [candidate EXCEPT ![e] = NoCommit]
  /\ leaseState' = [leaseState EXCEPT ![entryLease[e]] = "active"]
  /\ leaseExpiry' = [leaseExpiry EXCEPT ![entryLease[e]] = clock + Max(Ttls)]
  /\ UNCHANGED <<entryLease, landedAs, leaseHolder, leasePaths>>

\* settle_landed. forget_absent and release_lease in one transaction.
Landed(e, commit) ==
  /\ entryState' = [entryState EXCEPT ![e] = "landed"]
  /\ landedAs' = [landedAs EXCEPT ![e] = commit]
  /\ leaseState' = [leaseState EXCEPT ![entryLease[e]] = "released"]
  /\ UNCHANGED <<entryLease, candidate, leaseHolder, leasePaths, leaseExpiry>>

Requeued(e) ==
  /\ entryState' = [entryState EXCEPT ![e] = "queued"]
  /\ candidate' = [candidate EXCEPT ![e] = NoCommit]
  /\ UNCHANGED <<entryLease, landedAs>>

\* ---------------------------------------------------------------------------
\* Drain, both modes.
\* ---------------------------------------------------------------------------

\* The flock. Break = two-integrators drops it. The model has one process, so
\* that variant shows the lock is what refuses a second run, and explores no
\* interleaving of two runs. Break = absence-counts-drains is the rule at the
\* parent of pull request #474: a mark from an earlier drain counts as a repeat.
DrainStart ==
  /\ ~paused
  /\ (Break = "two-integrators" \/ run.pc = "idle")
  /\ secondDrain' = (run.pc # "idle")
  /\ run' = IdleRun("locked")
  /\ seenEarlier' = IF Break = "absence-counts-drains"
                    THEN [e \in Entries |-> seenEarlier[e] \/ absence[e] = "waiting"]
                    ELSE seenEarlier
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, attempt, worktree, paused, tip,
                 prOpen, prStored, prHead, checkState, armed, mergedAs, absence, historyVars>>

ReconcileFetch ==
  /\ run.pc = "locked"
  /\ run' = [run EXCEPT !.pc = "fetched", !.fetched = tip]
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, attempt, worktree, paused,
                 secondDrain, tip, forgeVars, historyVars>>

\* Asks git, through the fetch above, what happened to the interrupted push.
\* Break = no-already-landed-check negates the answer: an attempt git says is
\* on trunk is requeued, and one git says is not on trunk settles as landed.
ReconcileAttempt ==
  LET e          == attempt.entry
      recognised == attempt.cand \in Ancestors(run.fetched)
      settle     == IF Break = "no-already-landed-check" THEN ~recognised ELSE recognised
  IN
  /\ Break # "no-reconcile-attempt"
  /\ run.pc = "fetched"
  /\ attempt # NoAttempt
  /\ attempt' = NoAttempt
  /\ IF settle
     THEN Landed(e, attempt.cand)
     ELSE /\ Requeued(e)
          /\ UNCHANGED <<leaseVars>>
  /\ UNCHANGED <<clock, floor, run, worktree, paused, secondDrain, tip, forgeVars, historyVars>>

\* The separate later transaction. Every `landing` entry goes back to queued.
ReconcileLanding ==
  /\ run.pc = "fetched"
  /\ (attempt = NoAttempt \/ Break = "no-reconcile-attempt")
  /\ entryState' = [e \in Entries |-> IF entryState[e] = "landing" THEN "queued" ELSE entryState[e]]
  /\ candidate' = [e \in Entries |-> IF entryState[e] = "landing" THEN NoCommit ELSE candidate[e]]
  /\ run' = IdleRun("reconciled")
  /\ UNCHANGED <<leaseVars, clock, floor, entryLease, landedAs, attempt, worktree,
                 paused, secondDrain, tip, forgeVars, historyVars>>

\* `attempt` captures the base here, once, and passes it to `publish`. Merge
\* mode's line holds one entry in this model, so the build waits for it.
IntegrationReset ==
  /\ run.pc \in {"reconciled", "ready"}
  /\ \E e \in Entries : entryState[e] = "queued"
  /\ (Mode = "merge" => \A e \in Entries : entryState[e] # "awaiting-merge")
  /\ worktree' = tip
  /\ run' = [IdleRun("based") EXCEPT !.base = tip]
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, attempt, paused, secondDrain,
                 tip, forgeVars, historyVars>>

\* One replay. It moves the worktree and writes no store row, so a Crash here
\* leaves the worktree moved and the entry queued. The outcome is process state.
\* The bounce outcome stands for a conflict or a fingerprint that differs.
ApplyRun(e) ==
  LET held == EntryPrint(e) \in Tree(worktree) IN
  /\ run.pc = "based"
  /\ entryState[e] = "queued"
  /\ \/ /\ held
        /\ Break # "no-already-landed-check"
        /\ run' = [run EXCEPT !.pc = "apply-already", !.entry = e]
        /\ UNCHANGED worktree
     \/ /\ (~held \/ Break = "no-already-landed-check")
        /\ \E c \in Replays(worktree) :
             /\ (Change(c) = EntryPrint(e) \/ Break = "no-fingerprint")
             /\ run' = [run EXCEPT !.pc = "apply-ok", !.entry = e, !.cand = c]
             /\ worktree' = c
     \/ /\ ~held
        /\ run' = [run EXCEPT !.pc = "apply-bounce", !.entry = e]
        /\ UNCHANGED worktree
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, attempt, paused, secondDrain,
                 tip, forgeVars, historyVars>>

SettleAlready ==
  /\ run.pc = "apply-already"
  /\ Landed(run.entry, run.base)
  /\ run' = IdleRun("ready")
  /\ UNCHANGED <<clock, floor, attempt, worktree, paused, secondDrain, tip, forgeVars, historyVars>>

BounceApply ==
  /\ run.pc = "apply-bounce"
  /\ Bounced(run.entry)
  /\ run' = IdleRun("ready")
  /\ UNCHANGED <<clock, floor, attempt, worktree, paused, secondDrain, tip, forgeVars, historyVars>>

\* Publish mode records the candidate before the check. Merge mode's build runs
\* the check straight after the replay, with no transaction between them.
\* `checkedAt` holds the commit the entry's latest check passed on, or none.
CheckRun ==
  /\ (run.pc = "recorded" \/ (Mode = "merge" /\ run.pc = "apply-ok"))
  /\ \/ /\ run' = [run EXCEPT !.pc = "check-pass"]
        /\ checkedAt' = [checkedAt EXCEPT ![run.entry] = worktree]
     \/ /\ run' = [run EXCEPT !.pc = "check-fail"]
        /\ checkedAt' = [checkedAt EXCEPT ![run.entry] = NoCommit]
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, attempt, worktree, paused,
                 secondDrain, tip, forgeVars, publishes, staleWrite, droppedCoverage,
                 trunkRewound, uncheckedMerge>>

BounceCheck ==
  /\ run.pc = "check-fail"
  /\ Bounced(run.entry)
  /\ run' = IdleRun("ready")
  /\ UNCHANGED <<clock, floor, attempt, worktree, paused, secondDrain, tip, forgeVars, historyVars>>

\* ---------------------------------------------------------------------------
\* Publish mode.
\* ---------------------------------------------------------------------------

RecordLanding ==
  /\ Mode = "publish"
  /\ run.pc = "apply-ok"
  /\ entryState' = [entryState EXCEPT ![run.entry] = "landing"]
  /\ candidate' = [candidate EXCEPT ![run.entry] = run.cand]
  /\ run' = [run EXCEPT !.pc = "recorded"]
  /\ UNCHANGED <<leaseVars, clock, floor, entryLease, landedAs, attempt, worktree,
                 paused, secondDrain, tip, forgeVars, historyVars>>

\* Break = ignore-failed-check is a caller that takes a failed check for a pass.
BeginPublish ==
  /\ Mode = "publish"
  /\ (run.pc = "check-pass" \/ (Break = "ignore-failed-check" /\ run.pc = "check-fail"))
  /\ attempt' = [entry |-> run.entry, cand |-> run.cand]
  /\ run' = [run EXCEPT !.pc = "inserted"]
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, worktree, paused, secondDrain,
                 tip, forgeVars, historyVars>>

\* The lease moves trunk only from exactly `base`. Break = publish-no-base-check
\* is a bare `--force` in its place. A plain `git push` would still refuse a
\* non-fast-forward update, so it is not what this variant stands for.
Publish ==
  /\ run.pc = "inserted"
  /\ (tip = run.base \/ Break = "publish-no-base-check")
  /\ tip' = run.cand
  /\ publishes' = [publishes EXCEPT ![run.entry] = publishes[run.entry] + 1]
  /\ trunkRewound' = (trunkRewound \/ tip \notin Ancestors(run.cand))
  /\ run' = [run EXCEPT !.pc = "pushed"]
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, attempt, worktree, paused,
                 secondDrain, prOpen, prStored, prHead, checkState, armed, mergedAs,
                 absence, seenEarlier, checkedAt, staleWrite, droppedCoverage, uncheckedMerge>>

PublishRefused ==
  /\ run.pc = "inserted"
  /\ tip # run.base
  /\ Break # "publish-no-base-check"
  /\ run' = [run EXCEPT !.pc = "refused"]
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, attempt, worktree, paused,
                 secondDrain, tip, forgeVars, historyVars>>

AbandonAttempt ==
  /\ run.pc = "refused"
  /\ attempt' = NoAttempt
  /\ Requeued(run.entry)
  /\ run' = IdleRun("ready")
  /\ UNCHANGED <<leaseVars, clock, floor, worktree, paused, secondDrain, tip, forgeVars, historyVars>>

\* Settles on the remembered push result. Break = settle-before-publish settles
\* with no push behind it. Break = lease-released-before-settle comes after
\* ReleaseThenSettle.
SettleLanded ==
  /\ CASE Break = "settle-before-publish"        -> run.pc = "inserted"
       [] Break = "lease-released-before-settle" -> run.pc = "released"
       [] OTHER                                  -> run.pc = "pushed"
  /\ attempt' = NoAttempt
  /\ Landed(run.entry, run.cand)
  /\ run' = IdleRun("ready")
  /\ UNCHANGED <<clock, floor, worktree, paused, secondDrain, tip, forgeVars, historyVars>>

ReleaseThenSettle ==
  /\ Break = "lease-released-before-settle"
  /\ run.pc = "pushed"
  /\ leaseState' = [leaseState EXCEPT ![entryLease[run.entry]] = "released"]
  /\ run' = [run EXCEPT !.pc = "released"]
  /\ UNCHANGED <<leaseHolder, leasePaths, leaseExpiry, clock, floor, entryVars,
                 attempt, worktree, paused, secondDrain, tip, forgeVars, historyVars>>

\* ---------------------------------------------------------------------------
\* Merge mode. `poll` reads the store each time, so these need no process state
\* beyond holding the flock, and the model allows them in any order. A Crash can
\* land between any two.
\* ---------------------------------------------------------------------------

Polling == run.pc \in {"reconciled", "ready"}

RecordCandidate ==
  /\ Mode = "merge"
  /\ run.pc = "check-pass"
  /\ entryState' = [entryState EXCEPT ![run.entry] = "awaiting-merge"]
  /\ candidate' = [candidate EXCEPT ![run.entry] = run.cand]
  /\ prHead' = [prHead EXCEPT ![run.entry] = run.cand]
  /\ checkState' = [checkState EXCEPT ![run.entry] = "absent"]
  /\ absence' = [absence EXCEPT ![run.entry] = "none"]
  /\ seenEarlier' = [seenEarlier EXCEPT ![run.entry] = FALSE]
  /\ run' = IdleRun("ready")
  /\ UNCHANGED <<leaseVars, clock, floor, entryLease, landedAs, attempt, worktree,
                 paused, secondDrain, tip, prOpen, prStored, armed, mergedAs, historyVars>>

OpenPr(e) ==
  /\ Mode = "merge"
  /\ Polling
  /\ entryState[e] = "awaiting-merge"
  /\ ~prOpen[e]
  /\ prOpen' = [prOpen EXCEPT ![e] = TRUE]
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, drainVars, tip, prStored, prHead,
                 checkState, armed, mergedAs, absence, seenEarlier, historyVars>>

StorePr(e) ==
  /\ Mode = "merge"
  /\ Polling
  /\ entryState[e] = "awaiting-merge"
  /\ prOpen[e]
  /\ ~prStored[e]
  /\ prStored' = [prStored EXCEPT ![e] = TRUE]
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, drainVars, tip, prOpen, prHead,
                 checkState, armed, mergedAs, absence, seenEarlier, historyVars>>

ChecksPost(e) ==
  /\ prOpen[e]
  /\ mergedAs[e] = NoCommit
  /\ checkState[e] = "absent"
  /\ checkState' = [checkState EXCEPT ![e] = "pending"]
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, drainVars, tip, prOpen, prStored,
                 prHead, armed, mergedAs, absence, seenEarlier, historyVars>>

ChecksFinish(e) ==
  /\ prOpen[e]
  /\ mergedAs[e] = NoCommit
  /\ checkState[e] = "pending"
  /\ \E r \in {"passed", "failed"} : checkState' = [checkState EXCEPT ![e] = r]
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, drainVars, tip, prOpen, prStored,
                 prHead, armed, mergedAs, absence, seenEarlier, historyVars>>

HeadMoves(e) ==
  /\ prOpen[e]
  /\ mergedAs[e] = NoCommit
  /\ \E c \in Commits \ Squashes :
       /\ c \notin {prHead[e], candidate[e], "c0"}
       /\ prHead' = [prHead EXCEPT ![e] = c]
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, drainVars, tip, prOpen, prStored,
                 checkState, armed, mergedAs, absence, seenEarlier, historyVars>>

AbsenceSeen(e) ==
  /\ Polling
  /\ entryState[e] = "awaiting-merge"
  /\ prStored[e]
  /\ prHead[e] = candidate[e]
  /\ checkState[e] = "absent"
  /\ absence[e] = "none"
  /\ absence' = [absence EXCEPT ![e] = "waiting"]
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, drainVars, tip, prOpen, prStored,
                 prHead, checkState, armed, mergedAs, seenEarlier, historyVars>>

\* A boolean, not a duration. It says the five minutes have passed.
GraceEnds(e) ==
  /\ absence[e] = "waiting"
  /\ absence' = [absence EXCEPT ![e] = "over"]
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, drainVars, tip, prOpen, prStored,
                 prHead, checkState, armed, mergedAs, seenEarlier, historyVars>>

\* What request_merge lets through. Passed checks go ahead. Absent checks go
\* ahead only after the grace, and a repository with no required checks merges
\* that way. Break = absence-counts-drains is the rule at the parent of pull
\* request #474. Break = merge-without-checks waits for neither.
ChecksAllowMerge(e) ==
  \/ checkState[e] = "passed"
  \/ (checkState[e] = "absent" /\ absence[e] = "over")
  \/ (checkState[e] = "absent" /\ Break = "absence-counts-drains" /\ seenEarlier[e])
  \/ (checkState[e] \in {"absent", "pending"} /\ Break = "merge-without-checks")

RequestMerge(e) ==
  /\ Mode = "merge"
  /\ Polling
  /\ entryState[e] = "awaiting-merge"
  /\ prStored[e]
  /\ ~armed[e]
  /\ prHead[e] = candidate[e]
  /\ ChecksAllowMerge(e)
  /\ SameTree(Parent(candidate[e]), tip)
  /\ armed' = [armed EXCEPT ![e] = TRUE]
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, drainVars, tip, prOpen, prStored,
                 prHead, checkState, mergedAs, absence, seenEarlier, historyVars>>

\* HeadMoves never returns to the candidate, so one check state per pull request
\* always describes the head the queue reads. `--match-head-commit` holds the merge to the checked head, and required
\* checks that have started hold it until they pass. The merged commit may be a
\* squash or a rebase, and trunk may have moved since the request. Break =
\* merge-without-checks lets a pending check through. Break =
\* no-match-head-commit drops the pin on the head.
ForgeCompletes(e) ==
  LET head     == prHead[e]
      allowed  == checkState[e] \in {"passed", "absent"}
                    \/ (Break = "merge-without-checks" /\ checkState[e] = "pending")
      honoured == /\ head = candidate[e]
                  /\ \/ checkState[e] = "passed"
                     \/ (checkState[e] = "absent" /\ absence[e] = "over")
  IN
  /\ entryState[e] = "awaiting-merge"
  /\ armed[e]
  /\ mergedAs[e] = NoCommit
  /\ (head = candidate[e] \/ Break = "no-match-head-commit")
  /\ allowed
  /\ \E m \in MergeCommits(head, tip) :
       /\ tip' = m
       /\ mergedAs' = [mergedAs EXCEPT ![e] = m]
       /\ trunkRewound' = (trunkRewound \/ tip \notin Ancestors(m))
  /\ publishes' = [publishes EXCEPT ![e] = publishes[e] + 1]
  /\ uncheckedMerge' = (uncheckedMerge \/ ~honoured)
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, drainVars, prOpen, prStored, prHead,
                 checkState, armed, absence, seenEarlier, checkedAt, staleWrite, droppedCoverage>>

\* take_pr compares trees, not parents. A tree that differs settles landed and
\* pauses the queue in the same transaction. Break = no-tree-check drops the pause.
SettleMerged(e) ==
  /\ Polling
  /\ entryState[e] = "awaiting-merge"
  /\ mergedAs[e] # NoCommit
  /\ Landed(e, mergedAs[e])
  /\ paused' = (paused \/ (Break # "no-tree-check" /\ ~SameTree(mergedAs[e], candidate[e])))
  /\ UNCHANGED <<clock, floor, attempt, run, worktree, secondDrain, tip, forgeVars, historyVars>>

BounceOpenPr(e) ==
  /\ Polling
  /\ entryState[e] = "awaiting-merge"
  /\ prStored[e]
  /\ mergedAs[e] = NoCommit
  /\ prHead[e] = candidate[e]
  /\ checkState[e] = "failed"
  /\ Bounced(e)
  /\ prOpen' = [prOpen EXCEPT ![e] = FALSE]
  /\ prStored' = [prStored EXCEPT ![e] = FALSE]
  /\ prHead' = [prHead EXCEPT ![e] = NoCommit]
  /\ checkState' = [checkState EXCEPT ![e] = "absent"]
  /\ armed' = [armed EXCEPT ![e] = FALSE]
  /\ absence' = [absence EXCEPT ![e] = "none"]
  /\ seenEarlier' = [seenEarlier EXCEPT ![e] = FALSE]
  /\ UNCHANGED <<clock, floor, drainVars, tip, mergedAs, historyVars>>

\* ---------------------------------------------------------------------------
\* The environment.
\* ---------------------------------------------------------------------------

OutsiderPush(c) ==
  /\ c \notin Squashes
  /\ Parent(c) = tip
  /\ tip' = c
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, drainVars, forgeVars, historyVars>>

Crash ==
  /\ run.pc # "idle"
  /\ run' = IdleRun("idle")
  /\ secondDrain' = FALSE
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, attempt, worktree, paused, tip,
                 forgeVars, historyVars>>

DrainEnd ==
  /\ Polling
  /\ run' = IdleRun("idle")
  /\ secondDrain' = FALSE
  /\ UNCHANGED <<leaseVars, clock, floor, entryVars, attempt, worktree, paused, tip,
                 forgeVars, historyVars>>

\* ---------------------------------------------------------------------------
\* Next. `leases` runs the lease writes alone. `queue` runs the queue in the Mode
\* the config names, with SmallLeaseNext. `all` runs the queue with every lease write.
\* ---------------------------------------------------------------------------

LeaseNext ==
  \/ \E h \in Holders, ps \in SUBSET Paths, ttl \in Ttls, g \in Generations : Claim(h, ps, ttl, g)
  \/ \E l \in Leases, ttl \in Ttls, g \in Generations : Renew(l, ttl, g)
  \/ \E l \in Leases, g \in Generations : Release(l, g)
  \/ \E l \in Leases, ps \in SUBSET Paths, ttl \in Ttls, g \in Generations : Widen(l, ps, ttl, g)
  \/ \E g \in Generations : RaiseFloor(g)
  \/ ClockTick

\* The lease writes a queue run cannot do without. `all` adds Renew, Release, and
\* Widen, so they interleave with a queue that holds a lease.
SmallLeaseNext ==
  \/ \E h \in Holders, ps \in SUBSET Paths, ttl \in Ttls, g \in Generations : Claim(h, ps, ttl, g)
  \/ \E g \in Generations : RaiseFloor(g)
  \/ ClockTick

SharedNext ==
  \/ IF Scope = "all" THEN LeaseNext ELSE SmallLeaseNext
  \/ \E e \in Entries, l \in Leases, g \in Generations : Submit(e, l, g)
  \/ \E e \in Entries : SubmitDuplicate(e)
  \/ DrainStart
  \/ ReconcileFetch
  \/ ReconcileAttempt
  \/ ReconcileLanding
  \/ IntegrationReset
  \/ \E e \in Entries : ApplyRun(e)
  \/ SettleAlready
  \/ BounceApply
  \/ CheckRun
  \/ BounceCheck
  \/ \E c \in Commits : OutsiderPush(c)
  \/ Crash
  \/ DrainEnd

PublishNext ==
  \/ RecordLanding
  \/ BeginPublish
  \/ Publish
  \/ PublishRefused
  \/ AbandonAttempt
  \/ SettleLanded
  \/ ReleaseThenSettle

MergeNext ==
  \/ RecordCandidate
  \/ \E e \in Entries :
       \/ OpenPr(e)
       \/ StorePr(e)
       \/ ChecksPost(e)
       \/ ChecksFinish(e)
       \/ HeadMoves(e)
       \/ AbsenceSeen(e)
       \/ GraceEnds(e)
       \/ RequestMerge(e)
       \/ ForgeCompletes(e)
       \/ SettleMerged(e)
       \/ BounceOpenPr(e)

Next ==
  IF Scope = "leases" THEN LeaseNext
  ELSE IF Mode = "publish" THEN SharedNext \/ PublishNext
  ELSE SharedNext \/ MergeNext

\* The bound is enforced here, in Next. No behaviour takes more than Depth
\* transitions, so a violation is never reported past Depth.
BoundNext == steps < Depth /\ Next /\ steps' = steps + 1

\* ---------------------------------------------------------------------------
\* The promises. README.md states each in one sentence with its harm.
\* ---------------------------------------------------------------------------

\* P1. Two holders never hold live overlapping leases.
NoOverlappingLeases ==
  \A a, b \in Leases :
    a # b /\ InFlight(a) /\ InFlight(b) /\ leaseHolder[a] # leaseHolder[b]
      => ~Overlaps(leasePaths[a], leasePaths[b])

\* P2. A write below a holder prefix's owner floor never changes the store.
NoStaleWrite == ~staleWrite

\* P3a. At most one drain run integrates at a time.
OneIntegration == ~secondDrain

\* P3b. Every entry the store records as landed is on trunk.
LandedIsOnTrunk ==
  \A e \in Entries : entryState[e] = "landed" => landedAs[e] \in Ancestors(tip)

\* P3c. A queue push or a forge merge never takes a commit off trunk.
TrunkNeverRewinds == ~trunkRewound

\* P4. What landed holds the entry's reviewed change. What the queue put on trunk
\* has a tree that a passing local check ran on, unless the queue has paused to
\* say otherwise. The landed commit may differ from the candidate because squash
\* and rebase are allowed, so this compares trees. The forge merged only the
\* checked head, on passed checks or on absent checks after the grace.
CheckedHeadOnly ==
  /\ ~uncheckedMerge
  /\ \A e \in Entries :
       entryState[e] = "landed" =>
         /\ EntryPrint(e) \in Tree(landedAs[e])
         /\ publishes[e] > 0 =>
              \/ paused
              \/ checkedAt[e] # NoCommit /\ SameTree(landedAs[e], checkedAt[e])

\* P5a. Once reconcile finishes, no entry is `landing` and no attempt is `publishing`.
NoStalledEntry ==
  run.pc = "reconciled" =>
    /\ attempt = NoAttempt
    /\ \A e \in Entries : entryState[e] # "landing"

\* P5b. An entry in flight always holds a lease that is in flight.
EntryKeepsItsLease ==
  \A e \in Entries :
    entryState[e] \in {"queued", "landing", "awaiting-merge"} => InFlight(entryLease[e])

\* P5c. The same entry's change reaches trunk through the queue at most once.
NoDoubleLanding == \A e \in Entries : publishes[e] <= 1

\* P6. A widen's kept lease lasts at least as long as every lease it folded.
WidenKeepsCoverage == ~droppedCoverage

Spec == Init /\ [][BoundNext]_<<View, steps>>

====
