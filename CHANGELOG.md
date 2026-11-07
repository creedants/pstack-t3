# Changelog

## 0.10.0 (2026-11-07)

### New

- `ticket age` moves waiting tickets whose priority cell is `low` and whose kind is polish or upkeep to `notes.tsv` when they were filed more than the limit ago, then drops them. The limit sits in `restaurant.json` under `ticketAge`, defaults to 14 days, is set with `set --ticket-age N`, and 0 or `""` turns aging off. The evening wake runs `ticket age` before the report cue whenever aging is on, and `close` prints a `## Waiting list` section whenever a waiting ticket is on record or a ticket aged out since the last report.
- While a target is set, `ci-time` records the CI time of recent merged pull requests in `ci-times.tsv`, and `close` reports it
- `ci-time` reads the last merged pull requests through `gh`, times each from its earliest check start to its latest completion, and marks a run clean only when every run attempt is 1
- A failed job with a lost-communication annotation counts one runner loss for that job, however many such annotations it has, and a cancelled job counts one time-limit stop only with a maximum-execution-time annotation and a step that never completed on that same job
- When the two most recent clean runs stay over the target, `ci-time` files one `upkeep` ticket on `.github/workflows` naming both runs and the target, and a second breach after that ticket finishes adds a try-again-or-leave-it decision instead of another ticket
- `close` gains a `## CI time` section after the waiting list with the recent clean runs, the median, the longest, the count over target, the losses and stops of the last seven days, and any kept lines that are not rows
- The evening and merge sheets run `ci-time` while a target is set, and an open ci-time decision names its options while other work continues
- In light mode a coordinator gets one read-only look-back a week in place of the scheduled reflect it does not run. `set --look-back <days>`, default 7, holds the interval, and `0` or `""` turns it off, so every store has the look-back on until it is turned off
- `next evening` prints one `run look-back` cue while the effective mode is light, the setting is on, the interval since the last recorded look-back has passed, and at least one review round was recorded since then. A full-mode coordinator, an evening with nothing new, and an off setting add no cue
- `look-back` writes `look-backs/L<n>.md` and `look-backs/L<n>.json` under the store and prints the brief's path. The brief lists the rounds since the last look-back, 50 at most with the ones that went wrong kept ahead of the rest and the count of each result it left out named, each with its item, verdict, send-back reason, report file, and that item's worker and reviewer threads. It names the six categories, the proposal file format, and the rules, and it copies no report body. A second run while a brief has no stamp rewrites that brief at the newer moment instead of writing another, so `--record` stamps one brief
- `look-back --record <file>` stamps the run only when every proposal names a category, a fix type, a change, and evidence this store holds, which is a report file under `reports/`, a thread the brief lists, or an evidence token the brief lists. A refused proposal writes nothing, so the cue stays. `--record --none --reason "<one line>"` stamps a look-back that held nothing usable, and a second record for one brief stamps nothing
- `close` gains a `## Look-back` section listing the proposals recorded since the last report, in plain words, with the file that holds them. An off setting and an empty look-back print no section. Proposals are not tickets and not decisions, and only the owner's yes turns one into a ticket
- The brief has the reader check the repository's own check command and CI first, so a check that exists and is not connected is the finding. A mechanical mistake is proposed as a check, and only a judgement call is proposed as a reviewer rule
- The ready report-back wake prints one check line naming the resolved check command, so review runs the project's check at the worker's head before the verifier, and a failure reruns the brief instead of recording a round

### Fixed

- `watch` now judges each workflow on trunk by that workflow's own newest completed run, ignores GitHub's `dynamic` runs, and passes over a completed `cancelled` run when a newer run of the same workflow exists, so a green automatic dependency run created at `2026-11-03T15:49:10Z` no longer hid the `ci` run that failed at `2026-11-03T15:49:05Z`
- `trunk CI failing since` and its `before <createdAt>` form now follow the named workflow's own failure streak, and `cannot see trunk CI: conclusion cancelled` prints only when no newer run of that workflow exists
- `gh run list` is now asked for `event` as well as the fields it already asked for
- `verification/landing-queue/check.py` printed only the last 4,000 characters of a run that answered differently than the model says, so a failing trace lost its `Error:` line and its first states. It now prints that run's whole TLC output, so the `Error:` line naming the invariant and every state from `State 1` to the last one are there whatever the trace's length, and a parse failure prints all of TLC's output
- `tests/test_landing_queue_check.py` pins it with fake tools and a fake 12-state trace longer than 4,000 characters, so it needs no Java and no jar
- The ready report-back check line now wraps the resolved command as sh -c with shell quoting so a compound check stays inside one slot, and the review preflight names the check-failure relaunch as a new branch from the failed worker's head with no pass record and no round budget

### Changed

- Split each Linux suite into eight duration-balanced parts per Python version and gated pull request CI on them
- Moved the headless-browser probe into its own pull request Linux job and push macOS job with a shared anchor
- Moved the generated-files check into its own job that starts at once on both events
- Kept the whole suite on Python 3.10 and 3.12 for every pull request and skipped the Linux parts on push to main
- Added duration recording to the test runner, with a durations file that never breaks coverage
- Taught the part splitter to balance by recorded seconds with a default per test and per shard for a module the durations file does not name
- The part split now keeps a module's tests in one part and estimates each module from its recorded seconds per test plus the measured overhead of each shard it makes
- A module estimated above one part's share of the default worker count is placed one test at a time, so its tests can land in different parts
- A part run starts its most expensive shards first, so the longest one is not left as a tail
- The recording now writes one record per module instead of the slowest 104 tests, so it accounts for the time no single test owns
- One record per module is a measured seconds per test and the typical overhead of one shard of it, which keeps the file inside the size limit
- The 15000-byte budget is gone
- The part split now has a test where a module's recorded overhead changes which part its test lands in, with the expected parts written as literal numbers
- The part run launch order now has a test that lists the expected shard numbers as literals, so reversing the sort fails it
- A missing durations file now has a test over the real repository discovery that covers every discovered test exactly once in eight parts and leaves no part empty
- Four narration comments are gone from tests/runner_tests/test_parts.py
- Every job in .github/workflows/ci.yml now sets timeout-minutes, so a step that hangs while its runner still runs the job fails in minutes instead of holding it until GitHub's six-hour default
- The 16 part jobs use 20 minutes and the four macOS part jobs use 45
- The two gates, the generated job, and the two browser jobs use 10, which is at least three times the longest measured run of each
- A test in tests/runner_tests/test_ci_jobs.py fails when a job has no positive integer timeout-minutes, and it pins 20 for the part jobs and 45 for the macOS part jobs
- Made the weekly look-back keep every round that did not pass before the newest passes, and say how many of each result it left out
- Added queue refusals, failed main runs, and set-aside notes to the brief, each with an evidence token a proposal can cite
- Moved the queue read outside the store lock and made `through` the newest moment any source offered
- Read queue refusals from `land.py status --holder <holder> --json`, so queue text cannot add an entry, and read the notes ledger on its newline alone, so a note holding a line separator cannot stop the brief

- A TLA+ model under `verification/landing-queue/` covers the landing queue and its path leases, with one action per transaction or external call, except where the header map says why a death between folded parts cannot change a promise, and that map ties each action to the function it stands for
- Ten invariants state the lease-overlap, owner-floor, one-integrator, one-trunk-writer, checked-head, crash-recovery, and widen-coverage promises, each with the harm it prevents
- Sixteen broken variants each break one rule and each fail a named invariant, and two of them model the rule at the parent of pull request #474 and of commit `1adb24175`
- A project-local skill at `.claude/skills/verify-landing-queue/SKILL.md` says when a change touches the verified code, runs the check with one command through the land slot, tells a real bug from a modelling error, and records the commit the model was last compared with
- The model is split across sibling TLA+ modules under `verification/landing-queue/`, each within the 5000-token size limit, with `LandingQueue.tla` keeping the action-to-code map, the Next composition, and Spec
- The PR template now has Risk and How to revert prompts, and the brigade coordinator's body instruction names both

## Archive

- [0.9.0](docs/changelog/0.9.0.md)
- [0.9.0-2](docs/changelog/0.9.0-2.md)
- [0.9.0-3](docs/changelog/0.9.0-3.md)
- [0.9.0-4](docs/changelog/0.9.0-4.md)
- [0.8.0](docs/changelog/0.8.0.md)
- [0.8.0-2](docs/changelog/0.8.0-2.md)
- [0.8.0-3](docs/changelog/0.8.0-3.md)
- [0.8.0-4](docs/changelog/0.8.0-4.md)
- [0.8.0-5](docs/changelog/0.8.0-5.md)
- [0.8.0-6](docs/changelog/0.8.0-6.md)
- [0.8.0-7](docs/changelog/0.8.0-7.md)
- [0.8.0-8](docs/changelog/0.8.0-8.md)
- [0.7.0](docs/changelog/0.7.0.md)
- [0.7.0-2](docs/changelog/0.7.0-2.md)
- [0.7.0-3](docs/changelog/0.7.0-3.md)
- [0.7.0-4](docs/changelog/0.7.0-4.md)
- [0.6.0](docs/changelog/0.6.0.md)
- [0.6.0-2](docs/changelog/0.6.0-2.md)
- [0.5.0](docs/changelog/0.5.0.md)
- [0.5.0-2](docs/changelog/0.5.0-2.md)
- [0.5.0-3](docs/changelog/0.5.0-3.md)
- [0.5.0-4](docs/changelog/0.5.0-4.md)
- [0.4.0](docs/changelog/0.4.0.md)
- [0.4.0-2](docs/changelog/0.4.0-2.md)
- [0.3.0](docs/changelog/0.3.0.md)
- [0.3.0-2](docs/changelog/0.3.0-2.md)
- [0.2.0](docs/changelog/0.2.0.md)
- [0.2.0-2](docs/changelog/0.2.0-2.md)
- [0.1.0](docs/changelog/0.1.0.md)
