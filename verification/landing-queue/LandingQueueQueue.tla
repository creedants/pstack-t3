---- MODULE LandingQueueQueue ----
\* Moved from LandingQueue.tla unchanged: Submit, the drain, and the environment.
EXTENDS LandingQueueLeases

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

====
