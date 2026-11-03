---- MODULE LandingQueueMerge ----
\* Moved from LandingQueue.tla unchanged: merge mode. Polling is in
\* LandingQueueBase.tla, where the drain end reads it as well.
EXTENDS LandingQueueBase

\* ---------------------------------------------------------------------------
\* Merge mode. `poll` reads the store each time, so these need no process state
\* beyond holding the flock, and the model allows them in any order. A Crash can
\* land between any two.
\* ---------------------------------------------------------------------------

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

====
