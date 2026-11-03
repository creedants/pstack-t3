---- MODULE LandingQueue ----
\* A hand-written model of the landing queue and its path leases.
\*
\* Every action below stands for one transaction or one external call in
\* t3/added/landing/scripts/landing_lib/. The action-to-code map:
\*
\*   Claim          leases.py:lease_claim               one Store.tx() with check_owner, admission, INSERT
\*   Renew          leases.py:lease_renew               one Store.tx()
\*   Release        leases.py:lease_release             one Store.tx()
\*   Widen          leases.py:lease_widen               one Store.tx()
\*   RaiseFloor     _leases/owner.py:raise_floor        one Store.tx()
\*   Submit         _queue/entries.py:submit            the git reads, then one Store.tx()
\*   DrainStart     drain.py:land                       fcntl.flock on .queue.lock, LOCK_EX | LOCK_NB
\*   Reconcile      _queue/entries.py:reconcile         fetch, then one Store.tx() per publishing attempt
\*   Apply          _queue/integration.py:apply         one `git cherry-pick` in the integration worktree
\*   Check          _queue/integration.py:check         one external run of setup and checks
\*   BeginPublish   _queue/entries.py:attempt           the Store.tx() that INSERTs attempt state 'publishing'
\*   Publish        _queue/entries.py:publish           one `git push --force-with-lease`
\*   SettleLanded   _queue/entries.py:attempt           the Store.tx() that settles the entry and the attempt row
\*   Bounce         _queue/entries.py:settle_bounced    one Store.tx() that gives the lease back
\*   OpenPr         _queue/prs.py:ensure_pr             one `gh pr create`
\*   ChecksPost     the forge, external                 required checks appear on the pull request
\*   ForgeMerges    _merge/required.py:request_merge and _merge/settle.py:take_pr
\*                                                      `gh pr merge --match-head-commit`, then one state read
\*   Expire         the clock, external                 a lease passes its stored expiry
\*   OutsiderPush   another writer on trunk             the remote moving under a running drain
\*   Crash          the process dying                   not code: a kill between any two actions
\*
\* Where the code runs several transactions and external calls in sequence
\* with none around them, the model has one action for each and Crash may
\* fire between them.
\*
\* Mode picks the two ways trunk takes a change. `publish` is the integration
\* worktree checking a candidate and then one lease-guarded push. `merge` is a
\* pull request at the candidate, required checks, then `gh pr merge
\* --match-head-commit candidate`.
\*
\* Break names a broken variant of this model. `none` is the code as it is.
\* Every other value removes one check or reorders one step, and each is shown
\* to fail a named invariant. See README.md.
\*
\* Three variables are history variables, the standard way to check a promise
\* about a transition as an invariant over states. `staleWrite` becomes TRUE
\* only if a write below the owner floor changes the store.
\* `droppedCoverage` becomes TRUE only if a widen releases live coverage it
\* does not keep. `publishes` counts how often an entry's change reached trunk.
\* ---------------------------------------------------------------------------

EXTENDS Naturals, FiniteSets, TLC

CONSTANTS Holders, Paths, Leases, Entries, Commits, Mode, Scope, Break, Depth

VARIABLES leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
          entryState, entryLease, candidate, checkedAt, landedAs, prHead,
          checkState, attempt, attemptEntry, attemptBase, tip,
          floor, queueLock, secondDrain, integration, settled, staleWrite,
          droppedCoverage, publishes, steps

vars == <<leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
          entryState, entryLease, candidate, checkedAt, landedAs, prHead,
          checkState, attempt, attemptEntry, attemptBase, tip,
          floor, queueLock, secondDrain, integration, settled, staleWrite,
          droppedCoverage, publishes>>

NoCommit == "none"

\* ---------------------------------------------------------------------------
\* Static facts. Keep in step with the config when the code's constants change.
\* ---------------------------------------------------------------------------

PrefixOf(h) == IF h = "docs/A" THEN "docs/" ELSE "engine/"

\* The paths one entry's reviewed commits touch.
EntryPaths(e) == IF e = "E1" THEN {"a"} ELSE {"b"}

\* The fingerprint submit stores for one entry: the reviewed content.
EntryPrint(e) == IF e = "E1" THEN "p1" ELSE "p2"

\* A rebased commit's parent is the trunk it was replayed onto.
Parent(c) == CASE c = "c1" -> "c0" [] c = "c2" -> "c0"
                [] c = "c3" -> "c1" [] c = "c4" -> "c1" [] OTHER -> "c0"

\* Ancestors of a commit, itself included. `git merge-base --is-ancestor`.
Ancestors(c) == CASE c = "c1" -> {"c0", "c1"} [] c = "c2" -> {"c0", "c2"}
                   [] c = "c3" -> {"c0", "c1", "c3"} [] c = "c4" -> {"c0", "c1", "c4"}
                   [] OTHER   -> {"c0"}

\* What a commit's tree holds. seam.fingerprint compares this.
Content(c) == CASE c = "c1" -> "p1" [] c = "c2" -> "p2"
              [] c = "c3" -> "p1" [] c = "c4" -> "p2" [] OTHER -> "p0"

\* The commits the integration can produce from a base.
BuiltOn(base) == {c \in Commits : Parent(c) = base}

\* A submitted lease is in flight whatever its expiry. `_leases/store.py:in_flight`.
InFlight(l) == leaseState[l] = "submitted" \/ (leaseState[l] = "active" /\ leaseLive[l])

Overlaps(a, b) == a \cap b # {}

\* `_leases/store.py:admission`. Another holder's live lease on a wanted path refuses.
Clash(h, ps) == \E l \in Leases :
                     InFlight(l) /\ leaseHolder[l] # h /\ Overlaps(leasePaths[l], ps)

\* `_leases/owner.py:check_owner`. A write below the prefix's floor is refused.
Stale(h, g) == g < floor /\ PrefixOf(h) = "docs/"

StaleAllowed(h, g) == Break = "no-owner-check" \/ ~Stale(h, g)

\* ---------------------------------------------------------------------------
\* Init
\* ---------------------------------------------------------------------------

Init ==
  /\ leaseState = [l \in Leases |-> "free"]
  /\ leaseHolder = [l \in Leases |-> "none"]
  /\ leasePaths = [l \in Leases |-> {}]
  /\ leaseLive = [l \in Leases |-> FALSE]
  /\ leaseFolded = [l \in Leases |-> FALSE]
  /\ entryState = [e \in Entries |-> "none"]
  /\ entryLease = [e \in Entries |-> "L1"]
  /\ candidate = [e \in Entries |-> NoCommit]
  /\ checkedAt = [e \in Entries |-> NoCommit]
  /\ landedAs = [e \in Entries |-> NoCommit]
  /\ prHead = [e \in Entries |-> NoCommit]
  /\ checkState = [e \in Entries |-> "absent"]
  /\ attempt = "none"
  /\ attemptEntry = "E1"
  /\ attemptBase = "c0"
  /\ tip = "c0"
  /\ floor = 1
  /\ queueLock = "free"
  /\ secondDrain = FALSE
  /\ integration = "none"
  /\ settled = TRUE
  /\ staleWrite = FALSE
  /\ droppedCoverage = FALSE
  /\ publishes = [e \in Entries |-> 0]
  /\ steps = 0

\* ---------------------------------------------------------------------------
\* Leases. Each action is one Store.tx().
\* ---------------------------------------------------------------------------

\* lease_claim. g is the --owner generation the caller passed.
Claim(h, ps, g) ==
  /\ ps # {}
  /\ \E l \in Leases : leaseState[l] = "free"
  /\ StaleAllowed(h, g)
  /\ (Break = "no-clash" \/ ~Clash(h, ps))
  /\ \E l \in Leases :
       /\ leaseState[l] = "free"
       /\ leaseState' = [leaseState EXCEPT ![l] = "active"]
       /\ leaseHolder' = [leaseHolder EXCEPT ![l] = h]
       /\ leasePaths' = [leasePaths EXCEPT ![l] = ps]
       /\ leaseLive' = [leaseLive EXCEPT ![l] = TRUE]
       /\ leaseFolded' = [leaseFolded EXCEPT ![l] = FALSE]
       /\ staleWrite' = (staleWrite \/ Stale(h, g))
       /\ UNCHANGED <<entryState, entryLease, candidate, checkedAt, landedAs,
                      prHead, checkState, attempt, attemptEntry, attemptBase,
                      tip, floor, queueLock, secondDrain, integration, settled,
                      droppedCoverage, publishes>>

\* lease_renew. Extending a live lease keeps it live. Renewing an expired one
\* is admitted again, exactly as a fresh claim would be, so another holder's
\* live lease on those paths refuses it.
Renew(l, g) ==
  /\ leaseState[l] = "active"
  /\ StaleAllowed(leaseHolder[l], g)
  /\ (leaseLive[l] \/ ~Clash(leaseHolder[l], leasePaths[l]))
  /\ leaseLive' = [leaseLive EXCEPT ![l] = TRUE]
  /\ staleWrite' = (staleWrite \/ Stale(leaseHolder[l], g))
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseFolded, entryState,
                 entryLease, candidate, checkedAt, landedAs, prHead, checkState,
                 attempt, attemptEntry, attemptBase, tip, floor, queueLock,
                 secondDrain, integration, settled, droppedCoverage, publishes>>

\* lease_release.
Release(l, g) ==
  /\ leaseState[l] = "active"
  /\ StaleAllowed(leaseHolder[l], g)
  /\ leaseState' = [leaseState EXCEPT ![l] = "released"]
  /\ leaseLive' = [leaseLive EXCEPT ![l] = FALSE]
  /\ leasePaths' = [leasePaths EXCEPT ![l] = {}]
  /\ staleWrite' = (staleWrite \/ Stale(leaseHolder[l], g))
  /\ UNCHANGED <<leaseHolder, leaseFolded, entryState, entryLease, candidate,
                 checkedAt, landedAs, prHead, checkState, attempt, attemptEntry,
                 attemptBase, tip, floor, queueLock, secondDrain, integration,
                 settled, droppedCoverage, publishes>>

\* lease_widen. It keeps its own paths and folded paths, folds every other
\* active live lease of the same holder into itself, and releases those leases
\* in the same transaction.
Widen(l, ps, g) ==
  LET own    == leasePaths[l]
      folded == {m \in Leases : m # l /\ leaseState[m] = "active"
                               /\ leaseLive[m] /\ leaseHolder[m] = leaseHolder[l]}
      merged == own \cup ps \cup (UNION {leasePaths[m] : m \in folded})
      \* leases.py:63 takes the latest of its own expiry and every folded
      \* expiry. Before commit 1adb24175 it took its own expiry only, so an
      \* expired lease that folded a live one left the holder with no live
      \* coverage at all.
      after  == IF Break = "widen-drops-live-coverage" THEN leaseLive[l]
                ELSE leaseLive[l] \/ (folded # {})
      lost   == \E m \in folded : leaseLive[m] /\ ~after
  IN
  /\ leaseState[l] = "active"
  /\ StaleAllowed(leaseHolder[l], g)
  /\ (Break = "no-clash" \/ ~Clash(leaseHolder[l], merged))
  /\ leaseState' = [m \in Leases |-> IF m = l THEN "active"
                                   ELSE IF m \in folded THEN "released"
                                   ELSE leaseState[m]]
  /\ leasePaths' = [m \in Leases |-> IF m = l THEN merged
                                  ELSE IF m \in folded THEN {}
                                  ELSE leasePaths[m]]
  /\ leaseLive' = [m \in Leases |-> IF m = l THEN after
                                  ELSE IF m \in folded THEN FALSE
                                  ELSE leaseLive[m]]
  /\ leaseFolded' = [leaseFolded EXCEPT ![l] = TRUE]
  /\ staleWrite' = (staleWrite \/ Stale(leaseHolder[l], g))
  /\ droppedCoverage' = (droppedCoverage \/ lost)
  /\ UNCHANGED <<leaseHolder, entryState, entryLease, candidate, checkedAt,
                 landedAs, prHead, checkState, attempt, attemptEntry,
                 attemptBase, tip, floor, queueLock, secondDrain, integration,
                 settled, publishes>>

\* `land.py owner --prefix --generation`. A floor never lowers.
RaiseFloor(g) ==
  /\ g > floor
  /\ floor' = g
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
                 entryState, entryLease, candidate, checkedAt, landedAs, prHead,
                 checkState, attempt, attemptEntry, attemptBase, tip, queueLock,
                 secondDrain, integration, settled, staleWrite,
                 droppedCoverage, publishes>>

\* ---------------------------------------------------------------------------
\* The queue.
\* ---------------------------------------------------------------------------

\* submit. The git reads run outside the transaction and change nothing the
\* model tracks. The transaction is all of the effect below.
Submit(e, h, l, g) ==
  /\ entryState[e] = "none"
  /\ leaseHolder[l] = h
  /\ leaseState[l] = "active"
  /\ leaseLive[l]
  /\ EntryPaths(e) \subseteq leasePaths[l]
  /\ StaleAllowed(h, g)
  /\ entryState' = [entryState EXCEPT ![e] = "queued"]
  /\ entryLease' = [entryLease EXCEPT ![e] = l]
  /\ leaseState' = [leaseState EXCEPT ![l] = "submitted"]
  /\ staleWrite' = (staleWrite \/ Stale(h, g))
  /\ UNCHANGED <<leaseHolder, leasePaths, leaseLive, leaseFolded, candidate,
                 checkedAt, landedAs, prHead, checkState, attempt, attemptEntry,
                 attemptBase, tip, floor, queueLock, secondDrain, integration,
                 settled, droppedCoverage, publishes>>

\* drain.py:land. Whoever takes the flock owns the queue. Breaking the lock
\* lets a second drain start while the first still holds the one integration
\* worktree.
DrainStart ==
  /\ (Break = "two-integrators" \/ queueLock = "free")
  /\ (queueLock = "free" => secondDrain' = FALSE)
  /\ (queueLock = "held" => secondDrain' = TRUE)
  /\ queueLock' = "held"
  /\ attemptBase' = tip
  /\ settled' = FALSE
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
                 entryState, entryLease, candidate, checkedAt, landedAs, prHead,
                 checkState, attempt, attemptEntry, tip, floor, integration,
                 staleWrite, droppedCoverage, publishes>>

\* reconcile. It asks git what actually happened to an interrupted publish,
\* settles the entry it finds on trunk, requeues the entry it does not, and
\* requeues every entry a dead run left in `landing`.
Reconcile ==
  /\ queueLock = "held"
  /\ LET offered   == {e \in Entries : entryState[e] = "publishing"}
         recognised == attempt = "publishing" /\ candidate[attemptEntry] \in Ancestors(tip)
         settle    == Break # "no-reconcile-attempt"
                       /\ IF Break = "no-already-landed-check" THEN ~recognised ELSE recognised
         handled   == IF Break = "no-reconcile-attempt" THEN {} ELSE offered
         clear     == IF settle THEN handled ELSE {}
         requeued  == (handled \ clear) \cup {e \in Entries : entryState[e] = "landing"}
     IN
     /\ entryState' = [e \in Entries |->
                          IF e \in clear THEN "landed"
                          ELSE IF e \in requeued THEN "queued"
                          ELSE entryState[e]]
     /\ landedAs' = [e \in Entries |-> IF e \in clear THEN candidate[e] ELSE landedAs[e]]
     /\ candidate' = [e \in Entries |-> IF e \in requeued THEN NoCommit ELSE candidate[e]]
     /\ checkedAt' = [e \in Entries |-> IF e \in requeued THEN NoCommit ELSE checkedAt[e]]
     /\ publishes' = publishes
     /\ leaseState' = [l \in Leases |->
                           IF \E e \in clear : entryLease[e] = l /\ leaseState[l] = "submitted"
                           THEN "released" ELSE leaseState[l]]
     /\ attempt' = IF attempt = "publishing" /\ settle
                   THEN "published"
                   ELSE IF attempt = "publishing" /\ Break # "no-reconcile-attempt"
                        THEN "abandoned"
                        ELSE attempt
     /\ settled' = TRUE
     /\ UNCHANGED <<leaseHolder, leasePaths, leaseLive, leaseFolded, entryLease,
                    prHead, checkState, attemptEntry, attemptBase, tip, floor,
                    queueLock, secondDrain, integration, staleWrite,
                    droppedCoverage, publishes>>

\* _queue/integration.py:apply. One cherry-pick of the pinned commits onto the
\* base the attempt was built on. A replay whose content differs from the
\* reviewed content is bounced here, by seam.fingerprint.
Apply(e) ==
  /\ queueLock = "held"
  /\ integration = "none"
  /\ attempt # "publishing"
  /\ entryState[e] = "queued"
  /\ ( \/ \E c \in BuiltOn(attemptBase) :
            /\ (Break = "no-fingerprint" \/ Content(c) = EntryPrint(e))
            /\ entryState' = [entryState EXCEPT ![e] = "landing"]
            /\ candidate' = [candidate EXCEPT ![e] = c]
            /\ integration' = e
            /\ settled' = FALSE
            /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive,
                           leaseFolded, entryLease, checkedAt, landedAs, prHead,
                           checkState, attempt, attemptEntry, attemptBase, tip,
                           floor, queueLock, secondDrain, staleWrite,
                           droppedCoverage, publishes>>
       \/ \E c \in BuiltOn(attemptBase) :
            /\ Content(c) # EntryPrint(e)
            /\ entryState' = [entryState EXCEPT ![e] = "bounced"]
            /\ leaseState' = [l \in Leases |->
                                 IF l = entryLease[e] /\ leaseState[l] = "submitted"
                                 THEN "active" ELSE leaseState[l]]
            /\ leaseLive' = [l \in Leases |-> IF l = entryLease[e] THEN TRUE ELSE leaseLive[l]]
            /\ integration' = "none"
            /\ settled' = FALSE
            /\ UNCHANGED <<leaseHolder, leasePaths, leaseFolded, entryLease,
                           candidate, checkedAt, landedAs, prHead, checkState,
                           attempt, attemptEntry, attemptBase, tip, floor,
                           queueLock, secondDrain, staleWrite, droppedCoverage,
                           publishes>>
       \/ \* already-in-trunk. The replay changes nothing, so the entry settles at
          \* trunk without a push. This is the `sources` empty branch.
          /\ Break # "no-already-landed-check"
          /\ Content(attemptBase) = EntryPrint(e)
          /\ entryState' = [entryState EXCEPT ![e] = "landed"]
          /\ landedAs' = [landedAs EXCEPT ![e] = attemptBase]
          /\ integration' = "none"
          /\ settled' = FALSE
          /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive,
                         leaseFolded, entryLease, candidate, checkedAt, prHead,
                         checkState, attempt, attemptEntry, attemptBase, tip,
                         floor, queueLock, secondDrain, staleWrite,
                         droppedCoverage, publishes>> )

\* _queue/integration.py:check. One external run of setup and the required
\* checks on the candidate in the integration worktree.
Check(e) ==
  /\ queueLock = "held"
  /\ integration = e
  /\ entryState[e] = "landing"
  /\ checkedAt' = [checkedAt EXCEPT ![e] = candidate[e]]
  /\ integration' = "none"
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
                 entryState, entryLease, candidate, landedAs, prHead, checkState,
                 attempt, attemptEntry, attemptBase, tip, floor, queueLock,
                 secondDrain, settled, staleWrite, droppedCoverage, publishes>>

\* The transaction that INSERTs attempt state 'publishing' before the push.
BeginPublish(e) ==
  /\ Mode = "publish"
  /\ queueLock = "held"
  /\ entryState[e] = "landing"
  /\ checkedAt[e] = candidate[e]
  /\ attempt # "publishing"
  /\ attempt' = "publishing"
  /\ attemptEntry' = e
  /\ entryState' = [entryState EXCEPT ![e] = "publishing"]
  /\ settled' = FALSE
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
                 entryLease, candidate, checkedAt, landedAs, prHead, checkState,
                 attemptBase, tip, floor, queueLock, secondDrain, integration,
                 staleWrite, droppedCoverage, publishes>>

\* publish. One push that moves trunk from exactly `base` to the candidate.
\* `--force-with-lease=refs/heads/<trunk>:<base>` refuses when trunk moved.
Publish ==
  /\ attempt = "publishing"
  /\ Parent(candidate[attemptEntry]) = attemptBase
  /\ (Break = "publish-no-base-check" \/ tip = attemptBase)
  /\ tip' = candidate[attemptEntry]
  /\ publishes' = [e \in Entries |-> IF e = attemptEntry THEN publishes[e] + 1
                                  ELSE publishes[e]]
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
                 entryState, entryLease, candidate, checkedAt, landedAs, prHead,
                 checkState, attempt, attemptEntry, attemptBase, floor,
                 queueLock, secondDrain, integration, settled, staleWrite,
                 droppedCoverage>>

\* The same push refused with "trunk moved during landing", which requeues the
\* entry and leaves the attempt abandoned.
PublishRefused ==
  /\ attempt = "publishing"
  /\ Break # "publish-no-base-check"
  /\ tip # attemptBase
  /\ attempt' = "abandoned"
  /\ entryState' = [entryState EXCEPT ![attemptEntry] = "queued"]
  /\ candidate' = [candidate EXCEPT ![attemptEntry] = NoCommit]
  /\ checkedAt' = [checkedAt EXCEPT ![attemptEntry] = NoCommit]
  /\ settled' = FALSE
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
                 entryLease, landedAs, prHead, checkState, attemptEntry,
                 attemptBase, tip, floor, queueLock, secondDrain, integration,
                 staleWrite, droppedCoverage, publishes>>

\* The transaction that settles the attempt and the entry in it, and gives the
\* lease back.
SettleLanded(e) ==
  /\ attempt = "publishing"
  /\ attemptEntry = e
  /\ (Break = "settle-before-publish" \/ tip = candidate[e])
  /\ Break # "lease-released-before-settle"
  /\ attempt' = "published"
  /\ entryState' = [entryState EXCEPT ![e] = "landed"]
  /\ landedAs' = [landedAs EXCEPT ![e] = candidate[e]]
  /\ leaseState' = [l \in Leases |->
                       IF l = entryLease[e] /\ leaseState[l] = "submitted"
                       THEN "released" ELSE leaseState[l]]
  /\ UNCHANGED <<leaseHolder, leasePaths, leaseLive, leaseFolded, entryLease,
                 candidate, checkedAt, prHead, checkState, attemptEntry,
                 attemptBase, tip, floor, queueLock, secondDrain, integration,
                 settled, staleWrite, droppedCoverage, publishes>>

\* The same settle with the lease given back first, in its own transaction.
ReleaseThenSettle(e) ==
  /\ Break = "lease-released-before-settle"
  /\ attempt = "publishing"
  /\ attemptEntry = e
  /\ leaseState' = [l \in Leases |->
                       IF l = entryLease[e] /\ leaseState[l] = "submitted"
                       THEN "released" ELSE leaseState[l]]
  /\ UNCHANGED <<leaseHolder, leasePaths, leaseLive, leaseFolded, entryState,
                 entryLease, candidate, checkedAt, landedAs, prHead, checkState,
                 attempt, attemptEntry, attemptBase, tip, floor, queueLock,
                 secondDrain, integration, settled, staleWrite,
                 droppedCoverage, publishes>>

\* settle_bounced. The entry keeps its lease for the fix, with a fresh expiry.
Bounce(e) ==
  /\ entryState[e] \in {"queued", "landing"}
  /\ entryState' = [entryState EXCEPT ![e] = "bounced"]
  /\ leaseState' = [l \in Leases |->
                       IF l = entryLease[e] /\ leaseState[l] = "submitted"
                       THEN "active" ELSE leaseState[l]]
  /\ leaseLive' = [l \in Leases |-> IF l = entryLease[e] THEN TRUE ELSE leaseLive[l]]
  /\ settled' = FALSE
  /\ UNCHANGED <<leaseHolder, leasePaths, leaseFolded, entryLease, candidate,
                 checkedAt, landedAs, prHead, checkState, attempt, attemptEntry,
                 attemptBase, tip, floor, queueLock, secondDrain, integration,
                 staleWrite, droppedCoverage, publishes>>

\* ---------------------------------------------------------------------------
\* Merge mode. The forge is an external actor.
\* ---------------------------------------------------------------------------

\* ensure_pr. One `gh pr create` on landing/E<n>.
OpenPr(e) ==
  /\ Mode = "merge"
  /\ entryState[e] = "landing"
  /\ checkedAt[e] = candidate[e]
  /\ entryState' = [entryState EXCEPT ![e] = "awaiting-merge"]
  /\ prHead' = [prHead EXCEPT ![e] = candidate[e]]
  /\ checkState' = [checkState EXCEPT ![e] = "absent"]
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
                 entryLease, candidate, checkedAt, landedAs, attempt,
                 attemptEntry, attemptBase, tip, floor, queueLock, secondDrain,
                 integration, settled, staleWrite, droppedCoverage, publishes>>

\* The forge starts the required checks on the pull request's head.
ChecksPost(e) ==
  /\ Mode = "merge"
  /\ entryState[e] = "awaiting-merge"
  /\ checkState[e] = "absent"
  /\ checkState' = [checkState EXCEPT ![e] = "pending"]
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
                 entryState, entryLease, candidate, checkedAt, landedAs, prHead,
                 attempt, attemptEntry, attemptBase, tip, floor, queueLock,
                 secondDrain, integration, settled, staleWrite, droppedCoverage,
                 publishes>>

ChecksFinish(e) ==
  /\ Mode = "merge"
  /\ entryState[e] = "awaiting-merge"
  /\ checkState[e] = "pending"
  /\ \E r \in {"passed", "failed"} :
       checkState' = [checkState EXCEPT ![e] = r]
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
                 entryState, entryLease, candidate, checkedAt, landedAs, prHead,
                 attempt, attemptEntry, attemptBase, tip, floor, queueLock,
                 secondDrain, integration, settled, staleWrite, droppedCoverage,
                 publishes>>

\* `gh pr merge --auto --match-head-commit <candidate>`, then `take_pr`.
\* GitHub merges only while the head matches the candidate and the required
\* checks passed. Before pull request #474 the queue merged while required
\* checks had not posted, counting two drain runs instead of wall time.
ForgeMerges(e) ==
  /\ Mode = "merge"
  /\ entryState[e] = "awaiting-merge"
  /\ prHead[e] = candidate[e]
  /\ Parent(candidate[e]) = tip
  /\ (checkState[e] = "passed"
      \/ (Break = "merge-without-checks" /\ checkState[e] \in {"absent", "pending"}))
  /\ tip' = candidate[e]
  /\ entryState' = [entryState EXCEPT ![e] = "landed"]
  /\ landedAs' = [landedAs EXCEPT ![e] = candidate[e]]
  /\ publishes' = [publishes EXCEPT ![e] = publishes[e] + 1]
  /\ leaseState' = [l \in Leases |->
                       IF l = entryLease[e] /\ leaseState[l] = "submitted"
                       THEN "released" ELSE leaseState[l]]
  /\ UNCHANGED <<leaseHolder, leasePaths, leaseLive, leaseFolded, entryLease,
                 candidate, checkedAt, prHead, checkState, attempt, attemptEntry,
                 attemptBase, floor, queueLock, secondDrain, integration,
                 settled, staleWrite, droppedCoverage>>

\* A pull request whose head moved under the queue.
HeadMoves(e) ==
  /\ Mode = "merge"
  /\ entryState[e] = "awaiting-merge"
  /\ \E c \in Commits : c # candidate[e] /\ prHead' = [prHead EXCEPT ![e] = c]
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
                 entryState, entryLease, candidate, checkedAt, landedAs,
                 checkState, attempt, attemptEntry, attemptBase, tip, floor,
                 queueLock, secondDrain, integration, settled, staleWrite,
                 droppedCoverage, publishes>>

\* ---------------------------------------------------------------------------
\* The environment.
\* ---------------------------------------------------------------------------

\* Time passes and a lease's stored expiry goes by.
Expire(l) ==
  /\ leaseState[l] = "active"
  /\ leaseLive[l]
  /\ leaseLive' = [leaseLive EXCEPT ![l] = FALSE]
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseFolded, entryState,
                 entryLease, candidate, checkedAt, landedAs, prHead, checkState,
                 attempt, attemptEntry, attemptBase, tip, floor, queueLock,
                 secondDrain, integration, settled, staleWrite,
                 droppedCoverage, publishes>>

\* Someone outside the queue pushes to trunk.
OutsiderPush(c) ==
  /\ Parent(c) = tip
  /\ tip' = c
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
                 entryState, entryLease, candidate, checkedAt, landedAs, prHead,
                 checkState, attempt, attemptEntry, attemptBase, floor,
                 queueLock, secondDrain, integration, settled, staleWrite,
                 droppedCoverage, publishes>>

\* The process dies. The flock goes with it and the integration worktree is
\* left as it was. Every transaction already committed stays committed.
Crash ==
  /\ queueLock' = "free"
  /\ secondDrain' = FALSE
  /\ integration' = "none"
  /\ settled' = FALSE
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
                 entryState, entryLease, candidate, checkedAt, landedAs, prHead,
                 checkState, attempt, attemptEntry, attemptBase, tip, floor,
                 staleWrite, droppedCoverage, publishes>>

\* A run that has nothing left to do gives the lock back (drain.py's finally).
DrainEnd ==
  /\ queueLock = "held"
  /\ queueLock' = "free"
  /\ secondDrain' = FALSE
  /\ UNCHANGED <<leaseState, leaseHolder, leasePaths, leaseLive, leaseFolded,
                 entryState, entryLease, candidate, checkedAt, landedAs, prHead,
                 checkState, attempt, attemptEntry, attemptBase, tip, floor,
                 integration, settled, staleWrite, droppedCoverage, publishes>>

\* The scope picks one half of the model so each half runs small. `leases`
\* leaves out the queue. `queue` leaves out the lease writes no queue step needs.
InScope(kind) == Scope = kind \/ Scope = "both"

Next ==
  \/ /\ InScope("leases")
     /\ \/ \E h \in Holders, ps \in SUBSET Paths, g \in {1, 2} : Claim(h, ps, g)
        \/ \E l \in Leases, g \in {1, 2} : Renew(l, g)
        \/ \E l \in Leases, g \in {1, 2} : Release(l, g)
        \/ \E l \in Leases, ps \in SUBSET Paths, g \in {1, 2} : Widen(l, ps, g)
        \/ RaiseFloor(2)
        \/ \E l \in Leases : Expire(l)
  \/ /\ InScope("queue")
     /\ \/ \E h \in Holders, ps \in SUBSET Paths, g \in {1, 2} : Claim(h, ps, g)
        \/ \E l \in Leases : Expire(l)
        \/ \E e \in Entries, h \in Holders, l \in Leases, g \in {1, 2} : Submit(e, h, l, g)
        \/ DrainStart
        \/ Reconcile
        \/ \E e \in Entries : Apply(e)
        \/ \E e \in Entries : Check(e)
        \/ \E e \in Entries : BeginPublish(e)
        \/ Publish
        \/ PublishRefused
        \/ \E e \in Entries : SettleLanded(e)
        \/ \E e \in Entries : ReleaseThenSettle(e)
        \/ \E e \in Entries : Bounce(e)
        \/ \E e \in Entries : OpenPr(e)
        \/ \E e \in Entries : ChecksPost(e)
        \/ \E e \in Entries : ChecksFinish(e)
        \/ \E e \in Entries : ForgeMerges(e)
        \/ \E e \in Entries : HeadMoves(e)
        \/ \E c \in Commits : OutsiderPush(c)
        \/ Crash
        \/ DrainEnd

\* One more step. The config's CONSTRAINT bounds how many the check explores.
Bound == steps <= Depth

BoundNext == Next /\ steps' = steps + 1

\* ---------------------------------------------------------------------------
\* The promises. README.md states each in one sentence with its harm.
\* ---------------------------------------------------------------------------

\* P1. Two holders never hold live overlapping leases.
NoOverlappingLeases ==
  \A a, b \in Leases :
    a # b /\ InFlight(a) /\ InFlight(b) /\ leaseHolder[a] # leaseHolder[b]
      => ~Overlaps(leasePaths[a], leasePaths[b])

\* P2. A write below a holder prefix's owner floor never changes the store.
NoStaleWrite == staleWrite = FALSE

\* P3a. At most one drain run integrates at a time.
OneIntegration == queueLock = "held" => secondDrain = FALSE

\* P3b. Trunk has one writer. Every entry recorded as landed is on trunk.
LandedIsOnTrunk ==
  \A e \in Entries : entryState[e] = "landed" => landedAs[e] \in Ancestors(tip)

\* P4. What reaches trunk is the reviewed head, and the checks that passed ran
\* on the content that landed. An entry that settles as already in trunk pushes
\* nothing, so the merge-mode clauses are the only ones that bind it.
CheckedHeadOnly ==
  \A e \in Entries :
    entryState[e] = "landed"
      => /\ Content(landedAs[e]) = EntryPrint(e)
         /\ (checkedAt[e] # NoCommit
             => Content(checkedAt[e]) = Content(landedAs[e]))
         /\ (Mode = "merge" /\ prHead[e] # NoCommit
             => landedAs[e] = candidate[e] /\ checkState[e] = "passed")

\* P5a. No entry is left mid-flight once the queue has reconciled.
NoStalledEntry ==
  settled = TRUE => \A e \in Entries : entryState[e] \notin {"landing", "publishing"}

\* P5b. An entry in flight always holds a lease that is in flight.
EntryKeepsItsLease ==
  \A e \in Entries :
    entryState[e] \in {"queued", "landing", "publishing"} => InFlight(entryLease[e])

\* P5c. The same entry's change reaches trunk at most once.
NoDoubleLanding ==
  \A e \in Entries : publishes[e] <= 1

\* P6. A widen never drops live coverage it folded.
WidenKeepsCoverage == droppedCoverage = FALSE

Spec == Init /\ [][Next]_vars

====
