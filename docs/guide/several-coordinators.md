Part of [the guide](../guide.md).

### Several coordinators on one repository

You can open more than one coordinator on a repository, each with its own purpose. One might own the docs while another owns the engine. Coordinators whose `restaurant.json` names the same project root are siblings. They share the repository's landing contract, its leases, and its queue. Each keeps its own store and is the only writer of it.

**Opening a second one.** `brigade.py open` prints a block for each sibling, with its store, its thread, its purpose, and what it does not take. The opener keeps the new purpose clear of those and lists each sibling's purpose under `## Off the menu`. Two openers racing for one name get one coordinator. The one that created the directory prints `opened` and launches the thread. The other prints `exists`. A coordinator's store is `<project directory slug>/<name slug>`. When another repository's directory has the same slug and a coordinator there has the same name slug, `open` refuses with `pick another --name`. Coordinators in repositories whose directories have different slugs can reuse a name. Rerun `open` on an existing coordinator at any time to see its current siblings.

**Caps.** Two caps keep coordinators from crowding each other.

- The repository cap counts changes in flight across every coordinator. It counts every submitted lease and every active lease that has not expired. A submitted lease counts until it is released, even past its expiry, so a change waiting to land always counts. An active lease that expires stops counting before it is released, and a sibling can claim that room. It counts again only after `land.py lease renew` admits it again, which refuses while the repository is at its cap. Set it with `--cap` on [`land.py init`](../cli/land.md#landpy-init), or later with [`land.py cap`](../cli/land.md#landpy-cap). A count of 0 clears it. A claim at the cap is refused with `repository at its cap: 3 of 3 changes in flight`, followed by the holders.
- The coordinator cap counts that coordinator's units in progress or in review. Set it with `--workers` on [`brigade.py open`](../cli/brigade.md#brigadepy-open) or [`set`](../cli/brigade.md#brigadepy-set). A missing value reads as 2. `open` warns when it is at or above the repository cap while a sibling exists, because one coordinator could then fill the repository alone.

**Intake.** Each intake source, such as `github`, has exactly one owning coordinator. `--intake` on [`open`](../cli/brigade.md#brigadepy-open) or [`set`](../cli/brigade.md#brigadepy-set) records it, and both refuse a source a sibling already owns. `ticket add` refuses a source this coordinator does not own, and a ref that is still open here or in a sibling. So one issue becomes one ticket. A request from you with no ref is refused while a waiting or assigned ticket of that coordinator has the same summary, unless `--again` is passed. A request from you is filed with the `user` source, which every coordinator accepts.

**Handing a ticket over.** When the owner of a source files a ticket that is a sibling's work, it runs [`ticket move`](../cli/brigade.md#brigadepy-ticket-move) to hand it to the sibling and messages that sibling's thread. The sibling runs [`ticket take`](../cli/brigade.md#brigadepy-ticket-take), which files it once, even after a crash or a retry. Until the sibling takes it, the sender's `watch` prints `T6: moved to engine, waiting for ticket take`, and the sibling's `watch` and `status` print `handed to you: 1`.

**Blocked tickets.** When a lease, the repository cap, or the coordinator cap refuses `fire`, the ticket stays waiting and records why. `ticket list` shows `blocked: <reason>`. On each check `watch` looks again and prints whichever still holds:

- `T2: waiting on L7 (docs/D3)`, when another lease covers those paths.
- `T5: waiting for room in the repository (4 of 4 changes in flight)`.
- `T7: waiting for a worker (2 of 2 running)`.
- `T2: unblocked; run fire ...`, a command the coordinator runs as printed.

Nothing starts by itself when a lease frees. The coordinator decides.

**Leases stay alive while work is open.** A lease expires after 6 hours without renewal. Every `watch` renews the lease of each unit in progress, in review, passed, sent back, or parked. A live lease prints nothing. `walk` marks a coordinator idle when its leases stopped renewing. When a lease has expired, `watch` prints `D3: lease L4 expired; stop its worker, then run lease renew L4`. The coordinator stops the worker first, and only then renews. The renew refuses when another coordinator claimed those paths or the repository is at its cap. A unit is dropped the same way. The coordinator interrupts the worker, waits until its run has ended, and then runs `dish D3 --state dropped --stopped <run id>`, which releases the lease. Without `--stopped` the drop is refused while a worker is recorded, so a sibling never claims paths a running worker may still write.

**Work another coordinator landed.** One coordinator's `land` run can land a sibling's queued work. `watch` reads the queue entry at the unit's exact commit and prints `D1: landed as E1 (...); mark it merged`, `D2: E3 awaiting merge <pr url>; watch that PR`, or the bounce. Each coordinator watches only its own pull requests.

**One owner per store.** Each store records its thread and a generation. `status` prints `owner <thread>@<generation>`, and every write passes it as `--owner <thread>@<generation>`. Replacing the thread with `set --thread <new> --replace` adds 1 to the generation and raises the landing floor for that coordinator. Any command the old thread started, even one still running, is then refused with `owner ... is stale` and changes nothing. A coordinator opened before generations has none and keeps working without `--owner`. Upgrade it once with `brigade.py set --thread <its recorded thread>`, which records generation 1. From then on pass `--owner <thread>@1` on every write.

**Shared files.** `CHANGELOG.md`, `README.md`, and `docs/guide.md` are hot, because nearly every change wants to touch them. A change never edits `CHANGELOG.md`. It adds its one bullet to `changes/<branch>.md`, with `%` written as `%25` and `/` written as `%2F`, so `docs/d3` writes `changes/docs%2Fd3.md`. `fire` leases that file with the change. A change edits `README.md` or `docs/guide.md` only when its ticket is about them, or when it removes or renames something they name. Other doc edits are follow-ups that the coordinator batches into one docs change.

**One landing mode.** The mode lives only in the repository's landing contract. `brigade.py status` prints it as `lands by merge`, or `no landing contract`. To change it, run `land.py mode <mode>`. It names the holders of unreleased leases. Then tell each sibling, whose threads `walk --repo <root>` prints. Standing orders do not restate the mode.

**The view across coordinators.** `brigade.py walk` groups coordinators under their repository.

```
~/Projects/app: merge mode onto refs/remotes/origin/main. awaiting-merge: 1, landed: 40, leases held: 3, changes in flight: 3 of 4.
  docs (reports digest): in progress: 1, waiting to land: 1
    thread <id>, leases L41 (D7)
  engine (reports milestones): in progress: 2, waiting tickets: 1 (1 blocked)
    thread <id>, leases L42 (D12), L43 (D13)
```

The header is the queue's own status line. Each coordinator shows its reporting level, its counts, and how many waiting tickets are blocked. The second line shows its thread and the leases its units hold. Open decisions follow. `walk --repo <root>` prints one repository.
