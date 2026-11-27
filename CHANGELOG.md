# Changelog

## 0.10.2 (2026-11-27)

### Changed

- Sync the port to upstream pstack 0.15.46 at 0b46ec39: shorter skill bodies across the tree, deleted how, why, reflect, and architect reference files, deleted check-plan scripts and the Orchestrate playbook, a new worker.md kickoff rule, model slug and family updates, rewritten Babysit, Opening a PR, and Multi-phase plan playbooks, a one-sentence Comment Sicko change, and an upstream Setup role migration and budget change that pstack-t3 does not port.
- Re-port the overrides upstream's changes reach, keeping `show-me-your-work`'s different-model-family reviewer wording because it already states the family rule, and delete the overrides upstream deleted: how and reflect references and the poteto-mode check-plan scripts. Architect and Statechart now ship from upstream, including Statechart's native Windows launcher instructions.
- Move the Orchestrate playbook from t3/overrides to t3/added unchanged in substance, since upstream deleted it and pstack-t3 keeps it as its standing-program playbook with its bullet, its REQUIRED_TOOLS entry, and its tests.
- Collapse architect to the new upstream text and point large or contested designs at arena; the light table replaces the runner row with the new design behavior and keeps its separate grounding row.
- Rewrite how from the new short upstream text with the T3 mechanics that still apply: how keeps one `delegate_task` child on the `how explainer` role with a read-only brief, collected per the runtime's Delegation step 5, and delete the why override so upstream's one-paragraph text ships after the build removes its `disable-model-invocation` frontmatter key and adds the runtime pointer, since upstream deleted its investigator and synthesizer fan-out. Reflect keeps pstack-t3's three-lens reviewer panel and synthesizer even though upstream collapsed it to one inline pass; that fan-out, its roles, and its description are a deliberate deviation named below. Recall's shared-record wave is the same fan-out under another name, so it seats those investigators on the `why investigators` role, and the light recall row caps both recall waves together.
- Re-port the poteto-mode SKILL and its playbooks onto upstream's shortened text. The Playbooks section keeps upstream's one-line list of every playbook, which names them all, plus one dash bullet for Orchestrate, because the catalog reads dash bullets and reports any other list marker. The other 22 per-playbook bullet summaries do not fit the size allowance of vendor tokens plus 300, so they are not restored.
- The generated `docs/skills.md` playbook table lists only Orchestrate, because the catalog reads dash bullets.
- Land upstream's kickoff order for code delegates: the brief opens with Read <absolute path>/worker.md before anything else, then the poteto-agent persona body, then the roles.py mode lines, then the task. brief.py requires that leading line, accepts a worker path with spaces, and locates the kickoff block with the same per-line rules the Playbook and mode parsers use: it refuses a persona-first brief, any text between the persona body and the first line a parser accepts, and any non-blank line in the kickoff block that neither parser accepts, up through the printed mode block, so a malformed or indented heading cannot stand in for the block. Tests in tests.roles_tests.test_check_brief cover each refused shape and the task text that stays legal; the calling playbooks, the runtime, and the light code-delegate row name the same order.
- Keep the existing T3 Setup flow, including the T3 Code 0.1.3 version gate and the watch_pull_request requirement; take upstream's one-sentence Comment Sicko change and drop only its added model slug line from the poteto-agent frontmatter.
- Rewrite the Babysit port onto upstream's shorter text with the T3 wait: one `watch_pull_request` per open PR bottom to top after `link_pull_request`, one fallback `schedule_task` heartbeat for the stack, and one `unwatch_pull_request` per open watch plus a heartbeat delete when the loop ends. On GitHub, every mode runs `scripts/watch-pr/watch-pr --status-only` once per read, with `--queued-stack --stack-prs <bottom-to-top-numbers>` for a frozen stack, so the stack advance and completion come from the PR states in the frozen list rather than from `ADVANCE`, `COMPLETE`, or `WAITING` output no read produces.
- Update the tests the sync moved: read the moved Orchestrate playbook from t3/added, pin that the catalog extracts only the Orchestrate bullet and that every shipped playbook file exists, drop the deleted check-plan path pins, and drop architect from the modes-pointer sites with a test that it writes no brief and holds no pointer.
- Verify with a clean rebuild that leaves no diff, `scripts/check.py` printing ok with no size finding, regenerated skills and docs, and green runs of tests.test_pstack_t3, tests.test_build, tests.test_check, and tests.roles_tests.test_check_brief.
- Known limits: reflect keeps the fan-out upstream deleted, as a deliberate deviation rather than restored upstream text, and porting it to upstream's inline pass needs its description edited and the description eval run, so it stays a follow-up. The remaining kept lines are the light-mode trigger, the arena family-based judge, the `show-me-your-work` family-based reviewer, and the eval and recall seat wording. The poteto-mode override has seven tokens of headroom under its size allowance, and the playbook table in `docs/skills.md` lost its per-playbook rows, both reported as follow-ups.
- Move brigade’s built-in full-send reviewer seat from `codex/gpt-6.1-sol` to `codex/gpt-6.2-sol` at `high`. Update the full-send reference and the model-family example in `roles.md`.
- `roles.py check-brief` now checks placement below the persona through the printed mode block, including `Mode source:`, `Attempt:`, and `Waived by mode:` when present, and falls back to the last recognized mode-block line when the block cannot be read.
- Restore the Escalation sentence that nothing moves work from full to light mid-flight.
- List recall as the caller of the `why investigators` role.
- Pin the model-family test to `gpt-6.2-sol` and drop the extra `gpt-6.1-sol` example.
- The role table no longer names why as the caller of `why synthesizer`; no shipped skill seats it and the role is kept so roles files that name it still load.
- `activity.py` reports an unreadable T3 base directory as permission denied on Python 3.14.
- `roles.py check-brief` no longer reports the placement diagnostic for a mode label quoted in the task text after the printed block.
- Sync the port to upstream pstack 0.15.48 at b7b93c36, which adds the `reframe` skill from upstream #1324 and the vague-description step from #1326.
- Ship `reframe` unchanged from upstream. It teaches the expert words for what the operator is building, borrows terms from neighboring fields that solved the same shape of problem, checks every cited source, then rewrites the description. The build adds the runtime pointer and drops the `disable-model-invocation` frontmatter key, so the skill needs no override.
- Add upstream's reframe row to the `poteto-help` skill table after `$sharpen`, with `$reframe` and a link to `../reframe/SKILL.md`. The rest of the port keeps its T3 text.
- Update the doctor count in `docs/guide.md` and the skill count in `README.md` to 64, since the tree now ships one more skill.
- Pin the new routing in `tests/pstack_t3/test_modes.py`. The test fails when the poteto-help row is missing or links a skill that does not ship, and when the #1326 vague-description step is dropped from the generated skill.
- Verify with a clean rebuild that leaves no diff, `scripts/check.py` printing ok, and green runs of `tests.pstack_t3.test_modes`, `tests.test_build`, `tests.test_pstack_t3`, and `tests.test_check`.
- Known limits: upstream's reframe text mentions no T3 mechanism, so the port has no hand-written deviation to track.

## 0.10.1 (2026-11-22)

- CONTRIBUTING.md Releasing step 2 now says a release section groups its bullets under `### New`, `### Fixed`, and `### Changed` for feature, fix, and upkeep fragments, then polish and study bullets with no heading, each group in fragment commit order with an empty group left out

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
