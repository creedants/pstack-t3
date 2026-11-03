---- MODULE LandingQueueBase ----
\* Moved from LandingQueue.tla unchanged: the declarations, the static facts,
\* Init, the writes shared by settling transactions, and Polling.

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

Polling == run.pc \in {"reconciled", "ready"}

====
