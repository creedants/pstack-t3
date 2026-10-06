# Guide: your first hour with pstack-t3

This walks through installing pstack-t3, choosing models, and running real work in T3 Code. For the philosophy behind the workflows, Lauren Tan's [original pstack guide](../vendor/pstack/docs/guide/README.md) is the deep read. It is written for Cursor, but the ideas carry over unchanged.

## 1. Install (two minutes)

You need a [T3 Code nightly](https://github.com/pingdotgg/t3code/releases) with Orchestrator V2 and the pull request watching pstack-t3 relies on (`0.0.46-nightly.20261005.2702` or later). The skills call its orchestrator tools, which stable releases through `v0.0.45` don't ship.

Complete each prerequisite before using the feature it names.

- Install the GitHub CLI and run `gh auth login`. GitHub intake sources named in the house rules, `gh issue list` and `gh pr list`, need `gh`. Landing in `merge` and `human` modes needs `gh` too. User requests need no `gh`.
- To make the first commit in a new repository, run `git config --local user.name "Your Name"` and `git config --local user.email "you@example.com"` in that repository. `land.py init --base` needs an existing commit, and a clone already has one. A global identity is optional.
- Run `pip install pyyaml` before the test suite. `scripts/check.py` skips YAML frontmatter validation when PyYAML is missing.
- Confirm `orchestrator_capabilities` is in the T3 thread's tool list. `$setup-pstack` calls it first.

```bash
git clone https://github.com/creedants/pstack-t3.git ~/pstack-t3
cd ~/pstack-t3
python3 scripts/install.py
python3 scripts/install.py doctor
```

`doctor` should show `55/55 pstack-t3` for each provider you use. If it reports other copies of the same skills, you have an older pstack installed. Rerun with `python3 scripts/install.py --replace` to move it aside. `uninstall` puts it back.

Open a **new** T3 thread afterwards. Providers scan their skills when a session starts. Type `$` in the composer and you should see `poteto-mode`, `poteto-help`, `interrogate`, `swarm`, and the rest. Ask `$poteto-help` when you are stuck or cannot tell which skill fits. It answers and hands you a prompt. It does not start the work.

To update later, run `git pull` in the checkout, then `python3 scripts/install.py`. The installed skills link into the checkout, so the pull updates them in place. Rerunning the installer is safe and links any skills that are new.

## 2. Choose your models

In any thread, run:

```
$setup-pstack
```

It calls `orchestrator_capabilities` first. That tool must be in the thread's tool list. It reads which providers and models T3 can run right now, then asks for a reasoning budget:

| Budget | Effect |
| --- | --- |
| `default` | Built-in Opus and Grok seats stay at xhigh. A configured seat keeps its level. An `inherit` `verifiers` seat stays `inherit`. Any other `verifiers` seat gets no level, so that model's own default applies. |
| `small` | Medium reasoning everywhere. The cheapest. |
| `medium` | High reasoning |
| `large` | Extra-high reasoning |
| `unlimited` | A seat that names no level gets the highest offered level at or below max. When every offered level is above that cap, the seat gets the lowest offered level. That includes a `verifiers` seat. An `inherit` seat becomes this thread's model at that level. A configured seat keeps its level when the level is at or below max. When its level is above max, it drops to the highest level at or below max, or to the lowest offered level when every offered level is above that cap. |

Then it proposes a provider and model for each role. Unset single roles use Claude Opus (`claude-opus-5-5`) at xhigh for judgment and Grok (`grok-4.7`) at xhigh for code. `skill tests` prefers a runnable model from another family and names no reasoning level. Arena, architect, and interrogate panels are those two seats. `arena cross-judge pool` uses the same two seats. `verifiers` is this thread's model plus one seat per other model family you can run. You can accept it or change any role. It finishes with a one-word smoke test to every provider it picked, so a signed-out provider shows up now rather than mid-task.

You can skip setup entirely. The defaults in the previous paragraph still apply. A seat whose model you cannot run falls back, and the report names each replacement.

## 3. Your first rigorous task

Pick something real, such as a bug you can describe.

```
$poteto-mode the export button sometimes downloads an empty file. repro first, then fix and verify.
```

What happens:

1. **It picks a playbook.** Here that's Bug fix. It opens a todo list whose first items are the playbook's steps, copied in verbatim, so you can see the plan.
2. **It reproduces before fixing.** No fix lands without a failing reproduction on the same surface you reported. For a web UI, it can drive the page itself with T3's preview tools.
3. **It finds the root cause.** It traces the symptom back instead of adding a guard that hides it.
4. **It delegates where it helps.** Code changes may go to a child agent on your `bug-fix` model, with the poteto-agent persona and a self-contained brief. The lead reviews that child's diff itself.
5. **It proves the fix.** The same reproduction now passes, plus whatever checks the project has.
6. **It reports plainly.** Short sentences, every claim labeled measured, inferred, or guess, and the principles that shaped each decision named.

`$poteto-mode` applies from the message that names it and fades as the thread moves on. Name it again for the next task. Say so if you want it off.

## 4. Get several opinions

Before merging anything important:

```
$interrogate review this branch.
```

The lead states the change's intent, then sends the diff to one reviewer per seat in your `interrogate reviewers` role. They all get the same read-only brief and rubric, and run in parallel on different model families. The lead ends its turn while they work, and T3 wakes it as each one finishes. It then checks each claim itself and returns a verdict: what to act on, what to consider, what it dismissed and why, and where reviewers agreed.

Agreement across different model families is the strongest signal. If every reviewer ran on the same model, the report says so, because that agreement counts for much less.

## 5. Go parallel

| You want | Use |
| --- | --- |
| Coverage across slices: one worker per module, file, or endpoint | `$swarm` |
| A race: several workers on the same brief, then pick the best | `$swarm` with a race rule |
| Several attempts at a design or artifact, then the best parts merged into one | `$arena` |
| Competing designs before you write code across a boundary | `$architect` |

Workers that only read share your checkout. Workers that write get their own git worktree, so they never step on each other.

## 6. Leave it running

```
$poteto-mode i'm going to bed. land the stack even if ci flakes. i want everything merged by morning.
```

Long work uses three T3 features:

- **Child agents** do the units of work and wake the lead when they finish.
- **Separate threads in their own worktrees** own long-lived pieces, such as one PR each in Autopilot. They show up in your T3 sidebar.
- **`schedule_task`** runs a recurring check, usually hourly, that audits progress against the playbook and fixes drift. It is deleted when the work is done.

It keeps a decision log you can audit afterwards (`show-me-your-work`). It still pauses for anything irreversible that you didn't authorize, such as a force-push to a shared branch, a deploy, or deleting data.

To stop it, interrupt the thread. The playbooks interrupt their own owner threads and cancel their child agents when told to stand down.

## 7. Pick up where you left off

```
$recall what was I doing on the billing migration?
```

`$recall` searches your T3 threads in the project, plus git and PR state, and returns a short current-state brief. To take over another thread's in-flight work, ask `$poteto-mode` to pick up that thread. Its Session pickup playbook reads the thread, its child tasks, its branch, and any resume note.

## 8. Open a standing coordinator

A coordinator is a pinned T3 thread for one project or one focus area. It takes requests, hands each unit of work to a playbook, has another model family review the result, and lands what passes. It never writes code. For one finite program with a done condition, use `$poteto-mode`. Its Orchestrate playbook stops when that work is done.

Type this in any thread.

```
$brigade open a standing coordinator for <project> focused on <goal>.
```

The opener asks who lands the work, unless you already named a mode. It recommends `merge` when the repository has a remote. The mode belongs to the repository. Every coordinator on that repository shares it. If the repository already has a landing contract, the opener tells you its mode. Choosing another mode switches it for every coordinator on that repository.

The opener also asks how often the coordinator replies, unless you already named a level. It recommends `milestones`.

### Landing modes

| Mode | What reaches trunk | Are you a gate? |
| --- | --- | --- |
| `merge` | The queue rebases onto trunk, runs the checks, pushes `landing/q<n>`, and opens a PR. It merges that PR when the check read allows it. | No. You review what landed. A required approving review pauses the queue until you relax that rule or switch to `human`. |
| `human` | The same PR, left open. It counts as landed when someone merges that checked head. | Yes. You merge every PR. |
| `push` | Rebased commits that passed the checks, pushed to trunk with `git push --force-with-lease` only while the remote is still at the tested base. There is no PR. | No. You review the history afterward. |
| `local` | Nothing on the real trunk. The queue moves `refs/landing/<trunk>`. The remote does not change. | Yes, for the real trunk. You merge that ref into a branch when you are ready. |

In `merge` mode, `land` reads posted checks before any `gh pr merge`, including `--auto`. A pending posted check waits. A failed posted check bounces. The first `land` run that sees no posted checks records that run and waits. A later `land` merges when checks are still absent. With no posted check, the entry waits, and the queue does not pause when `gh pr merge` is refused because the base branch policy prohibits the merge. A plain merge that fails for any other reason pauses the queue, including when `--auto` reports that auto-merge is disabled. A failed or unreadable check read pauses the queue and does not merge. A later `land` reads the PR state before it acts on a failed check. A PR already merged at the candidate head is landed in that same run, and its queue branch is deleted, even when a check fails afterward. An open PR noted `merge requested by the queue` bounces when a check fails, and the lease becomes active. After the PR merges, `land` deletes remote `landing/q<n>`. When that delete fails, `land` asks the forge whether the branch is gone. HTTP 404 records the merge in that run only when the contract remote has one push URL and that URL names the same owner and repository that `gh repo view --json nameWithOwner` resolves. Any other result leaves the entry for the next run. It deletes a local `landing/q<n>` when git can. When that local delete fails, the entry still counts as landed and `land` prints the branch it left.

`push` is the name to use. `auto` is an older alias of `push`.

`land.py mode` switches among `merge`, `human`, and `push` only while nothing is queued, landing, or awaiting merge. A move to or from `local` needs a new contract. [How work lands](how-it-works.md#how-work-lands) describes the queue those modes share.

### Reporting levels

The level is stored for that coordinator. `brigade.py open --reporting` sets it. The default is `milestones` when you omit the flag. Opening an existing coordinator does not change its level. Change it later with `brigade.py set --reporting` and one of `every-turn`, `milestones`, or `digest`.

| Level | What the coordinator sends |
| --- | --- |
| `every-turn` | A short reply after every wake. |
| `milestones` | A reply when work merges, a review sends work back or blocks it, a decision needs you, something fails or the queue pauses, or you send a message. A routine wake ends with no reply, or with one line when the host requires text. A liveness check with nothing new, a liveness check while a review is pending, a review starting, and a worker launching are routine. |
| `digest` | A reply only for a decision you must make, a failure or a paused queue the coordinator cannot fix itself, one summary when a batch drains, the 18:00 report, and a message from you. Every other wake ends with no reply text at all, not even a status line. That includes a worker reporting back, a review starting or finishing, a send-back, a worker launching, a merge that does not drain the batch, a pull request wake, a liveness check, and intake with nothing new. |

A batch has drained when `brigade.py status` shows none of `in progress`, `in review`, `passed review`, or `waiting to land`. Status omits a count of zero, so a missing label is a count of zero. `waiting to land` includes a pull request that awaits merge.

Every level sends the scheduled 18:00 report. The 09:00 run is not a report. A message from you gets at least one line, and a direct question gets an answer. [How a coordinator reports](how-it-works.md#how-a-coordinator-reports) is the same rule from the coordinator's side.

`brigade.py close` runs on the 18:00 report, on a reply that raises a decision for you, at the end of a turn begun by your message, and on the reply that closes the coordinator. At `digest` it also runs on the summary sent when a batch drains. Every other reply stays plain and does not run `close`. `close` writes that report and records its time. At `every-turn` and `milestones` the reply pastes the `close` output. At `digest` the coordinator runs `brigade.py close --to-file`, which writes the same report and prints only its path.

A `digest` message is written for you, not for an engineer. It is a few plain sentences on what got done and what it means, what comes next, and anything you must decide. It carries no work or ticket IDs, SHAs, file paths, review-round counts, or tool names. Merged pull requests may follow as a short list of plain titles linked to each PR. The last line names the path of the full report. The next report starts from that time, so it omits what this run already listed.

The opener runs `brigade.py open --project-root <root> --name "<name>" --landing <choice> --reporting <level>`. It prints `opened` or `exists`, then the store path `${XDG_STATE_HOME:-~/.local/state}/pstack-t3/brigade/<project-slug>/<name-slug>/`. Each slug is the project directory name or the name you gave, in lowercase. Each run of characters other than a-z and 0-9 becomes one hyphen, and leading or trailing hyphens are dropped.

The store holds these files.

- `menu.md`, with the headings `Purpose`, `What good looks like`, `Off the menu`, and `Budget`.
- `house-rules.md`.
- `restaurant.json`.
- `rail.tsv`, `dishes.tsv`, `pass.tsv`, `86.tsv`, and `log.tsv`, each with a header and no rows.

The opener drafts `menu.md` from the README, AGENTS.md, open issues, and recent threads, and asks you for the purpose when evidence does not settle it. `brigade.py brief` refuses to write a worker brief while `Purpose` is empty or still the template. The opener appends standing orders to `house-rules.md`. They name forbidden paths, the verification bar, intake sources, and a worker cap. The worker cap is an instruction to the coordinator. The script does not count running workers.

The opener launches the coordinator with `t3_thread_launch` on the project root. The sidebar title is `Head chef: <name>`. On its first run the coordinator pins that thread. If that thread is in another project, the thread you typed in cannot read it afterward.

### Give it work

Send the pinned thread a request.

```
Investigate the slow startup. Reproduce it, fix the cause, and verify the result.
```

It records the request and groups related requests into one unit of work.

### What the coordinator does without you

It wakes on your messages, on a worker's report-back, on a reviewer finishing, and on its schedules. Each worker calls `t3_thread_send` on the coordinator thread as its last step, after it writes the report. That message wakes the coordinator, which marks the attempt reported and reviews the work.

A morning run is every day at 09:00. A report is every day at 18:00. When `menu.md` names a source, intake also runs on an interval of at least one hour, matched to the sources in `house-rules.md`. A landing drain runs while work is queued or awaiting merge. In `merge` mode, when the repository has required checks and the pull request is not in a merge queue, that drain is hourly. While an entry is awaiting merge in `human` mode, or the pull request is in a merge queue, the drain is every 15 minutes.

A liveness check runs while work is in progress, every 10 minutes at `every-turn` and `milestones` and every 30 minutes at `digest`. At `digest`, worker report-backs and pull request watches are the primary wakes. `brigade.py watch` prints `report written, no report-back` while the work is in progress, when the report was written for this attempt, and when this attempt is not marked reported. The report counts as written for this attempt when its modification time is at or after the attempt's start. A report left by a replaced worker does not count.

The line `report written, no report-back` is a defect. The check reads the worker thread, finds why the message never arrived, and fires a fix at that cause. The liveness check remains the backstop when a worker hangs or never sends the message.

- **Intake.** It reads `menu.md` and `house-rules.md` and records each request. It runs `gh issue list` and `gh pr list` only when the standing orders name those sources.
- **Delegation.** It groups related requests, claims a path lease, writes the brief with `brigade.py brief`, and launches one worktree thread per unit. In-flight work stays under the worker cap in `house-rules.md`.
- **Cross-family review.** It reads the worker's report and diff. One reviewer from another model family checks that exact commit. A pass is recorded against that commit. A reviewer from the author's family is used only when no other family can run, and the verdict says so.
- **Landing.** On a pass it submits that commit to the queue and runs `land.py land`. Workers never merge. In `merge` and `human` mode the PR title and body go with the submit. A conflict or a changed rebase goes to a fresh worker and needs a new review.
- **Cleanup.** After a unit merges, is dropped, or is sent back, it removes that unit's worktree and branch. It deletes `landing/q<n>` after that PR merges or closes. It deletes only branches it created. It fast-forwards your checkout with `git merge --ff-only` only when that checkout is clean and on trunk.
- **Reports.** The reporting level decides which wakes get a reply. `brigade.py close` runs only on the occasions named under [Reporting levels](#reporting-levels). Its output lists each request and unit once, under its latest state since the last report, with PR links. Any other reply stays plain and does not run `close`.

To list every coordinator on this machine, run `python3 ~/pstack-t3/skills/brigade/scripts/brigade.py walk`. If your checkout is not `~/pstack-t3`, use that checkout's `skills/brigade/scripts/brigade.py`. It prints the reporting level, the counts, the landing mode, open decisions, the thread id, and the store path. A coordinator idle for more than 24 hours is marked.

To stop one, ask it to close. It deletes its schedules, writes a last report, and unpins its thread. The store stays.

## Tips and pitfalls

- **Run fan-out work in a mode that allows commands and edits.** Children inherit the lead thread's runtime mode. In approval-required mode, a child can stall on an approval prompt that no tool can answer, and it looks idle while it waits.
- **Panels multiply cost.** Three reviewers cost about three reviews. Use the `small` budget for routine work and `large` or `unlimited` when it matters.
- **One provider is enough to start.** Everything works with a single provider. You lose model diversity, and reports say so.
- **Prefer the user install over a project install.** Claude and Grok load user-level skills over project-level ones with the same name.
- **Check the installed skills with `doctor`** whenever a skill seems to behave like an older version.

## Uninstall

```bash
python3 scripts/install.py uninstall
```

This removes every link pstack-t3 created and restores anything `--replace` moved aside. Your `~/.config/pstack-t3/roles.json` is left in place.
