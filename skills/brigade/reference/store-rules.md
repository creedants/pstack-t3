# Store rules

Read this before triage, `fire`, `dish`, `open --workers`, `set`, or acting on a block. The core keeps the intake owner, the owner token short form, and the round budget gates. This file holds caps, paths, priority, drop, gates, modes, and the full owner-token rule.


## Owner token

The check runs under the store lock, and the lock is held through the write. A token that does not match exits 1 with `brigade: owner <token> is stale; this store is owned by <thread>@<generation>` and changes nothing. `set --thread --replace` first raises the landing floor for `<restaurant>/` with `land.py owner`, so `land.py` also refuses the replaced thread's `--owner <restaurant>/@<old generation>`. Before you run `set --thread`, read [Owner](../script.md#owner).


The cap counts dishes in progress or in review. `fire` refuses before it claims a lease when the count is already there. A refused `fire` leaves the tickets waiting and logs why. When the cap is full, `dish --state in-progress` or `in-review` refuses when that dish is not already one of those states. Moving between those states, or replacing the worker, does not. `dish --lease` and `dish --paths` record a lease claimed again, and refuse a merged or dropped dish. When `fire` cannot release a lease it claimed and then could not keep, it warns with the lease id and the landing queue's diagnostic. `open` warns when `workers` is at or above the repository cap and a sibling exists. `watch` prints `waiting on L<n> (<holder>)`, `waiting for room in the repository (<n> of <n> changes in flight)`, `waiting for a worker (<running> of <cap> running)`, or `unblocked` with a shell-quoted `fire` command. Run that command as printed. Before you run `open --workers` or `fire` or change a dish's lease, paths, or branch, read [Worker cap](../script.md#worker-cap). When `watch` or `ticket list` prints a worker, lease, or repository block, or `walk` prints a blocked count, read that section before acting on the block.


`dish --add-paths` runs `land.py lease widen` on the recorded lease, which keeps that lease's id and holder. It covers old paths plus new paths plus any generated copies and change-note fragment that `fire` would add. It folds the holder's other active unexpired leases into it and releases them. It records the same lease id and the new paths after checking that the dish still records the old lease and paths. An overlap refusal leaves the dish row and landing lease rows unchanged and names the other holder. A widen refusal rolls back its arming; the fresh-claim fallback may still arm a waiting reservation. A rerun with covered paths adds no paths and prints `already covers`. It still folds another active lease, and an expired lease is re-admitted with a fresh ttl. It refuses a merged or dropped dish. It refuses a submitted lease. When the recorded lease is released or missing, it records the holder's newest live lease, or claims a fresh lease and releases the old one.


`dish --state in-review` checks lease coverage before review. It skips the check when the dish holds no lease. It also skips the check when `land.py status` fails with exactly `land: <root> has no landing contract; run land.py init in it` and prints nothing to stdout. It refuses when the repository identity cannot be read. It refuses when any other status read fails or prints empty output. It refuses when a leased dish under a contract names no SHA. It skips when the status names no trunk ref. The lease must read as active. In remote mode it fetches the trunk first. A fetch, merge-base, or diff failure refuses. It compares source paths changed from merge-base of trunk ref to sha against lease paths. A directory path covers its files. Generated paths are dropped before the check. Uncovered paths refuse by name with the `--add-paths` command. The check runs outside the lock then rechecks under the lock. It refuses when lease id or paths changed between reads.


Every ticket has a priority. `urgent` is work that is broken, unsafe, or blocking, such as data loss, a broken install, a red trunk, a privacy or security hole, or an upstream sync. `normal` is wrong or missing behavior, a false statement in docs people follow, or a feature the user asked for. `low` is polish, a refactor, or an optional pointer. Set it at triage with `ticket add --priority`. Change it with `ticket set T<n> --priority` when the user says so or the facts change.


On a dish that holds a lease and records a worker thread, `dish --state dropped` refuses without `--stopped`, with `brigade: D3 holds L4 and its worker may still be running; wait for its run with t3_thread_wait, then pass --stopped <run id>`. The lease stays active, so no sibling claims its paths early. Before you run `dish --state dropped`, read [Dropping a dish](../script.md#dropping-a-dish).


## Round budget settings

A stored `roundBudget` puts its number in place of three.
`roundBudget` in `restaurant.json` sets the number. `ROUND_BUDGET` in `store.py` is its default of three. `set --round-budget N` writes it. `set --round-budget` empty clears it. Before you write report instructions for a worker or verifier, run `pass record` or `dish --reported`, or act on a review-report warning from `close`, read [Pass and round budget](../script.md#pass-and-round-budget).


Each item runs in full or light mode per [the runtime's Modes section](../../pstack-runtime/SKILL.md#modes). When the store changes meanwhile, `brief` resolves again, and after three changes it exits 1 with `brigade: D<n> changed while resolving its mode; run brief again`. `dish --mode full` refuses a queued, merged, or dropped dish. `--mode light` on an item exits 1. Before you run `brief` or pass `--mode` to `open`, `set`, `fire`, or `dish`, read [Mode](../script.md#mode).
