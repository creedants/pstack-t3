---- MODULE LandingQueuePromises ----
\* Moved from LandingQueue.tla unchanged: the promises.
EXTENDS LandingQueueLeases

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

====
