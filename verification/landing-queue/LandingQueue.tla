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
\* The actions live in sibling modules, unchanged. LandingQueueBase.tla holds the
\* declarations, the static facts, Init, the writes shared by settling
\* transactions, and Polling. LandingQueueLeases.tla holds the lease helpers and
\* the lease actions. LandingQueueQueue.tla holds Submit, the drain, and the
\* environment. LandingQueuePublish.tla holds publish mode. LandingQueueMerge.tla
\* holds merge mode. LandingQueuePromises.tla holds the promises.
EXTENDS LandingQueueBase, LandingQueueLeases, LandingQueueQueue, LandingQueuePublish,
        LandingQueueMerge, LandingQueuePromises

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

Spec == Init /\ [][BoundNext]_<<View, steps>>

====
