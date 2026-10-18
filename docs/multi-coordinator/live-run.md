Part of [the design](../multi-coordinator.md).

## Live two-coordinator run

**Maintainers' own setup.** This run is planned for the pstack-t3 maintainers' own scratch repository, not yours. Record your own numbers from your own clone.

Run after change 7 lands, on a clone of `creedants/pstack-t3-sandbox`, the scratch repository the request for this design names for the live run. Changes 8 to 11 start only after its first part records its numbers. Opening issues there is still a GitHub write outside pstack-t3's own PR flow. The ref check compares strings, so the run uses an existing sandbox issue URL, or a made-up ref, rather than opening an issue.

The clone is not a git worktree of the sandbox's T3 project, so T3 refuses an `existing_worktree` strategy that points at it. Register the clone as its own T3 project with `t3_project_create`. Launch every coordinator and the admin in that project with `workspaceStrategy: {"type": "root"}`, as the brigade skill says. Workers then use the skill's `worktree` strategy unchanged.

1. `land.py init --trunk main --mode merge --merge-method <the method the repository allows> --cap 3 --check <the sandbox's test command>`. The sandbox allows only squash merges, so pass `--merge-method squash`. Open `core` with `--intake github`, and fill `core`'s `menu.md` before opening `docs`. Then open `docs`. Give each `--workers 2`. `docs`'s `open` prints `core`'s purpose and exclusions, and prints `purpose: not written yet` when `core`'s menu is still the template.
2. `docs` adds a ticket with `--source github` and is refused, naming `core`. `core` files the issue, and filing it again is refused.
3. `core` starts one item on `README.md` and one on `src/`. `docs`'s `fire` on `README.md` is refused. `docs`'s `watch` prints `waiting on L<n> (core/D1)`.
4. `docs` starts one item on a disjoint path, which makes 3 changes in flight. A third `fire` in `core` is refused by its own cap of 2. A second `fire` in `docs` is refused with the repository cap message, `3 of 3 changes in flight`.
5. `core`'s `README.md` item passes review from another model family, and `core` submits it and marks it `queued`. Then `docs`, not `core`, runs `land`, which opens the PR for `core`'s entry. `core`'s next liveness check prints `landed as E<n>` or `awaiting merge`, and `core` watches its own PR.
6. Within one liveness interval after `core`'s `README.md` lease is released, `docs`'s `watch` prints the `unblocked` line, and `docs` runs that `fire`.
7. `core` moves a ticket to `docs`. `docs` wakes on the message, and `ticket take` files it once.
8. `land.py mode human` while an entry is queued is refused. When idle it names the holders of unreleased leases, and `walk` shows one mode for both coordinators.
9. `core` drops its `src/` item while its worker runs. It calls `t3_thread_interrupt`, waits with `t3_thread_wait` until the run reports a terminal state, and only then runs `dish --state dropped --stopped <run id>`. A `docs` claim on those paths is refused until that command runs, and succeeds after it.

Record four numbers.

- Duplicate tickets filed. The target is 0.
- Items left `queued` after they landed. The target is 0.
- Minutes from a lease release to the retry. The target is at most one liveness interval, 10 minutes at `milestones` by default. `brigade.py set --liveness-ms` stores another interval.
- `fire` refusals, counted by kind (`lease`, `repository`, `workers`).

After change 10 lands, the same run continues with an executive admin.

1. `core` drops `github` from its intake. Open the admin with `open --admin --project-root <sandbox root>`, give it `--intake github`, launch its thread in the clone's T3 project, and have `core` and `docs` run `set --reports-to <its thread>`.
2. The user sends one request to the admin. It files the request with `ticket add --source user`, moves the ticket to `docs`, and sends the `ticket` line, and `docs` takes it.
3. When `docs`'s item merges, `docs` sends the `merged` line and sends the user nothing. The admin replies at the user's level, in plain language.
4. With `## Priorities` empty, `core` and `docs` both wait for `CONTRIBUTING.md` past an hour, while a third item holds it. Use a path neither purpose names. Each coordinator lists its sibling's purpose under `## Off the menu`, so a path one purpose names, such as `README.md` for a docs coordinator, is decided by `purpose` and never reaches age. Shorten the threshold for the run if no conflict lasts that long. The admin rules by age, records the ruling, and reserves the paths for the older side. When the lease frees, the other side's `fire` is refused with the reservation message, and the winner's succeeds. `lease list` shows no lease taken away.
5. The user overrules that ruling in plain words. The admin records the overrule, lifts the reservation, and reserves for the other side. Its next update lists both rulings.
6. `docs` finds that its passed item depends on one of `core`'s, opens a contest, sends the `contest` line, and then submits the item rather than holding it. Neither item lands until the admin settles it by dependency, and then `land` lands `core`'s item first.

That part records three more numbers.

- Replies to the user from coordinator threads that the user did not write to first. The target is 0 while sends deliver. A failed send expects the coordinator's direct reply instead, and the run checks that case once by sending to a thread id that does not exist.
- Rulings made, by kind and by deciding rule.
- Escalations, each with the reason the rules could not settle it.

## The experiment script

**Maintainers' own setup.** This script is the pstack-t3 maintainers' own audit tool, run against a commit of their repository. Point it at the same commit in your own checkout, as the paragraph below says.

This is the audit script, run against commit `5794d40`. It records what each command printed and asserts nothing. The tests in changes 2 to 7 are the asserting checks. Run it with the `skills/` directory of a checkout at `5794d40`, for example one made with `git worktree add /tmp/pstack-t3-5794d40 5794d40`, as `two_restaurants.sh /tmp/pstack-t3-5794d40/skills`. Never point it at a current `skills/` directory. After change 7, its `--landing` flags no longer parse. It writes only under a fresh `mktemp -d` and prints that path at the end.

```bash
#!/usr/bin/env bash
# Two coordinators on one scratch repository. Usage: two_restaurants.sh <skills dir>
set -u
SK=$1
T=$(mktemp -d)
export XDG_STATE_HOME=$T/state BRIGADE_STORE=$T/brigade
B="python3 $SK/brigade/scripts/brigade.py"
L="python3 $SK/landing/scripts/land.py --repo $T/app"
step() { printf '\n## %s\n' "$*"; }
run() { printf '$ %s\n' "${*/$T/\$T}"; "$@" 2>&1 | sed "s#$T#\$T#g"; printf '[exit %s]\n' "${PIPESTATUS[0]}"; }

git init -q --bare -b main $T/origin.git
git clone -q $T/origin.git $T/app 2>/dev/null
cd $T/app
git config user.email s@x; git config user.name s
mkdir -p docs src; for f in CHANGELOG.md README.md docs/guide.md src/a.py src/b.py src/c.py src/d.py; do echo x > $f; done
git add -A; git commit -qm init; git push -q origin main

step "contract and two coordinators"
run $L init --trunk main --mode push --check true
run $B open --project-root $T/app --name docs --landing push
run $B open --project-root $T/app --name engine --landing push
A="$B --at $BRIGADE_STORE/app/docs"; E="$B --at $BRIGADE_STORE/app/engine"
for r in docs engine; do sed -i "s/^What this restaurant exists.*/Purpose of $r: keep the app healthy./" $BRIGADE_STORE/app/$r/menu.md; done

step "gap 1: same --ref in both, and twice in one"
run $A ticket add --summary "issue 7" --source github --ref https://github.com/o/r/issues/7
run $E ticket add --summary "issue 7" --source github --ref https://github.com/o/r/issues/7
run $E ticket add --summary "issue 7 again" --source github --ref https://github.com/o/r/issues/7

step "gap 5: overlapping fire across coordinators"
run $A fire --tickets T1 --station feature --summary "docs change" --paths CHANGELOG.md,docs/guide.md
run $E fire --tickets T1 --station bug-fix --summary "engine fix" --paths src/a.py,CHANGELOG.md
run $E ticket list --state waiting
run $E status
run $L lease list

step "gap 2: no cap on work in progress"
for n in 4 5 6 7; do $E ticket add --summary "t$n" >/dev/null; done
run $E fire --tickets T2 --station feature --summary one --paths src/b.py
run $E fire --tickets T3 --station feature --summary two --paths src/c.py
run $E fire --tickets T4 --station feature --summary three --paths src/d.py
run $E fire --tickets T5 --station feature --summary four --paths src/e.py
run $E status; run $A status

step "gap 6: set --landing in one coordinator, then land.py mode"
run $A set --landing merge
run $L status
run grep -H '"landing"' $BRIGADE_STORE/app/docs/restaurant.json $BRIGADE_STORE/app/engine/restaurant.json
run $L mode human
run grep -H '"landing"' $BRIGADE_STORE/app/docs/restaurant.json $BRIGADE_STORE/app/engine/restaurant.json
run $L mode push

step "gap 9: one coordinator's land run lands the other's entry"
git checkout -q -b engine/d1 && echo y >> src/b.py && git commit -qam "engine b" && ESHA=$(git rev-parse HEAD) && git checkout -q main
git checkout -q -b docs/d1 && echo y >> CHANGELOG.md && git commit -qam "docs changelog" && DSHA=$(git rev-parse HEAD) && git checkout -q main
run $L submit --holder engine/D1 --branch engine/d1 --sha $ESHA --lease L2 --reviewer x/y
run $L submit --holder docs/D1 --branch docs/d1 --sha $DSHA --lease L1 --reviewer x/y
run $L land
run $L land
run $L status

step "land.py mode with a sibling's entry queued"
git checkout -q -b engine/d2 && echo y >> src/c.py && git commit -qam "engine c" && CSHA=$(git rev-parse HEAD) && git checkout -q main
run $L submit --holder engine/D2 --branch engine/d2 --sha $CSHA --lease L3 --reviewer x/y
run $L mode human

step "gaps 10 and 12: an expired lease, a sibling's claim, submit and renew"
run $L lease claim --holder docs/D9 --paths README.md --ttl-hours 0.0003
sleep 2
run $L lease claim --holder engine/D9 --paths README.md
git checkout -q -b docs/d9 && echo y >> README.md && git commit -qam "docs readme" && RSHA=$(git rev-parse HEAD) && git checkout -q main
run $L submit --holder docs/D9 --branch docs/d9 --sha $RSHA --lease L6 --reviewer x/y
run $L lease renew L6
run $L lease list

step "gap 11: another repository with the same directory name"
mkdir -p $T/other && git init -q -b main $T/other/app
run $B open --project-root $T/other/app --name docs --landing local
run grep projectRoot $BRIGADE_STORE/app/docs/restaurant.json

step "gap 3: no command moves a ticket"
run $E ticket move T6 --to docs

step "gap 4: a third coordinator says nothing about siblings"
run $B open --project-root $T/app --name release --landing push

step "gap 8: walk"
run $B walk
echo; echo "scratch: $T"
```

## Changes exercised in this run

### Change 7. One landing mode, one view per repository

Closes gaps 6 and 8.

- **Subtract.** Delete `set --landing` and the `--landing` flag of `open`. New `restaurant.json` files have no `landing` field, and an old field is ignored. The opener still asks the user for the mode, and passes it to `land.py init` or `land.py mode` as step 5 already does. The four tests that pass `--landing` move to the new form. Every doc line that shows the removed flags changes in this same change, per change 1's rule for removed names. That covers the brigade skill, `docs/guide.md`, and `docs/brigade-plan.md`.
- **`status`** prints the contract's mode, read from the first word of `land.py status`, as in `reporting: milestones, lands by merge, waiting tickets: 2`. With no contract it prints `no landing contract`.
- **`walk` groups by repository.** One header per `projectRoot` carries the `land.py status` line, followed by each coordinator's line without the old `lands by` field, its unreleased leases from `land.py lease list` matched by holder, and its blocked ticket count:

  ```
  ~/Projects/app: merge mode onto refs/remotes/origin/main. awaiting-merge: 1, landed: 40, leases held: 3, changes in flight: 3 of 4.
    docs (reports digest): in progress: 1, waiting to land: 1
      thread <id>, leases L41 (D7)
    engine (reports milestones): in progress: 2, waiting tickets: 1 (1 blocked)
      thread <id>, leases L42 (D12), L43 (D13)
  ```

  `walk --repo <root>` prints one repository.
- **Skill.** In Open steps 3 and 5, when the contract already has another mode, run `land.py mode`, which names the holders of unreleased leases, then tell each sibling with `t3_thread_send`, using the threads `walk --repo` prints. Standing orders do not restate the landing mode. A coordinator that finds one already stating it reports the duplicate, says the landing contract wins, and leaves the rule in place unless the user says to remove it. The skill's section that describes `walk` for the user covers the grouped form.
- **User docs.** `docs/guide.md` gains a "Several coordinators on one repository" section covering changes 1 to 7: the caps, intake ownership, handoff, blocked tickets, lease renewal, and the grouped `walk`. Change 11 adds the admin to it.
- **Files.** Same as change 3, plus `docs/guide.md` and `docs/brigade-plan.md`.
- **Tests.** `status` and `walk` print the contract mode after `land.py mode` changes it, with no brigade command run in between. `walk` groups two siblings under one header and a third coordinator under another, with the literal lines. `set --landing` exits 2.

### Change 8. Reservations, shares, and contests in `land.py`

Adds the script mechanisms that carry out the admin's rulings. Work on it starts only after the [live two-coordinator run](#live-two-coordinator-run) of changes 1 to 7 has recorded its numbers. It edits the same files as changes 2 and 6.

- **`lease reserve --for <holder prefix> --paths <paths> --ruling R<n> [--ttl-hours 2]`** prints `S<n>`. Reservations live in a new `reservation` table, because the `lease` table's state check cannot be altered in place. Every admission, `lease claim` and the re-admission in `lease renew`, refuses another holder's overlap with a standing reservation, with `paths reserved for docs/ by ruling R4 until <time>`. A reservation arms, which starts its clock, at the first admission check that finds no other holder's live lease overlapping and room for one more change under the cap and the reserved prefix's share. Arming is an admission in the claim transaction, oldest reservation first. An armed reservation counts toward the cap and the share as one change in flight until the winner holds a lease taken from it. A claim by a holder under the reserved prefix removes the paths it covers from the reservation, and the reservation ends when none remain. A second `reserve` with the same ruling returns the same `S<n>`, or refuses with `S<n> for ruling R4 expired` once it has. A reservation that overlaps another standing reservation is refused. `lease unreserve S<n>` lifts one. `lease list` and `lease check` show them.
- **`land.py share --for <holder prefix> <n>`** sets that prefix's share of the cap. Admission refuses a claim that would put the prefix over its share, with `docs/ is at its share: 2 of 2`. It refuses shares that add up to more than the cap. `share --for <prefix> 0 --clear` removes a share. A prefix with no share is limited only by the cap.
- **`land.py contest --holders <a>,<b>`** opens a contest and prints `C<n>`. It refuses when either holder has an entry that is `landing`, `awaiting-merge`, or `landed`. A `bounced` entry is history and does not count, so an item resubmitted after a bounce can still be contested. While a contest is open, `land` leaves every entry of both holders out of every batch and, in `human` and `merge` mode, out of PR opening. **`land.py contest --settle C<n> --first <holder>`** turns the hold into an order. `land` then holds the other holder's entries until an entry of the first holder lands. Settling again replaces the order while neither has landed. `--settle` refuses an order that would close a cycle with another settled contest. **`land.py contest --cancel C<n>`** removes the hold or the order, for a conflict that turned out not to exist, an item that was dropped, or an overrule that orders nothing. Contests live in a new `contest` table, and both commands take the queue lock, so they never race a `land` run.
- **Every ruling write checks its owner.** `lease reserve`, `lease unreserve`, `share`, and every form of `contest` take `--owner <prefix>@<generation>` and check it against change 6's floor inside their own transaction. The admin passes `--owner .admin/@<generation>`, and a coordinator opening a contest passes its own `<slug>/@<generation>`. An enforcement command the admin's old run started, finishing after a recovery raised the `.admin/` floor, exits 1 with the stale-owner message and changes nothing. So a stale `share`, reservation, or settle can never act after a newer ruling or a user's overrule.
- **Files.** `t3/added/landing/scripts/land.py`, `t3/added/landing/SKILL.md`, `tests/landing/`, generated `skills/landing/`, its fragment.
- **Tests.** A reservation for `docs/` refuses `engine/D3`'s overlapping claim with the literal message, and refuses the re-admission of `engine`'s expired lease on those paths. It admits `docs/D7`'s claim. A claim covering one of two reserved paths leaves the other reserved, and the winner's work counts once toward the cap. The clock does not start while another holder's live lease overlaps. With a cap of 1, two reservations waiting behind one lease arm one at a time, oldest first. Two processes claiming reserved paths at once, one from each coordinator, leave only the winner's lease. `share` refuses a sum over the cap, and admission refuses a claim over a share. While a contest is open, entries of both holders stay queued through a `land` run in each mode, including one submitted after the contest opened. After `--settle --first docs/D7`, the other holder's entry stays queued until `docs/D7` lands, including after `docs/D7` bounces and resubmits. Settling again in reverse works, and an order that closes a cycle is refused. Paused `share`, `lease reserve`, and `contest --settle` commands with `--owner .admin/@1`, released after `land.py owner --prefix .admin/ --generation 2`, each exit 1 with the literal stale-owner message, and the shares, reservations, and contest order are unchanged.
