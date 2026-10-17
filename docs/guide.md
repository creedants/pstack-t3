# Guide: your first hour with pstack-t3

This walks through installing pstack-t3, choosing models, and running real work in T3 Code. For the philosophy behind the workflows, Lauren Tan's [original pstack guide](../vendor/pstack/docs/guide/README.md) is the deep read. It is written for Cursor, but the ideas carry over unchanged.

## 1. Install (two minutes)

You need [T3 Code](https://github.com/pingdotgg/t3code/releases) `0.1.0` or later, which ships Orchestrator V2 and the pull request watching pstack-t3 relies on. The skills call its orchestrator tools, which stable releases through `v0.0.45` don't ship. pstack-t3 runs on the orchestrator V2 tools (`delegate_task`, `t3_thread_launch`, `schedule_task`, `watch_pull_request`).

You also need git and Python 3.10 or later.

Complete each prerequisite before using the feature it names.

- Install the GitHub CLI and run `gh auth login`. Reading GitHub issues or pull requests needs `gh`, and so does landing in `merge` and `human` modes. User requests need no `gh`.
- To make the first commit in a new repository, run `git config --local user.name "Your Name"` and `git config --local user.email "you@example.com"` in that repository. `land.py init --base` needs an existing commit, and a clone already has one. A global identity is optional.
- Run `pip install pyyaml` before the test suite. `scripts/check.py` skips YAML frontmatter validation when PyYAML is missing.
- Confirm `orchestrator_capabilities` is in the T3 thread's tool list. `$setup-pstack` calls it first.

```bash
git clone https://github.com/creedants/pstack-t3.git ~/pstack-t3
cd ~/pstack-t3
python3 scripts/install.py
python3 scripts/install.py doctor
```

`doctor` should show `59/59 pstack-t3` for each provider you use. If it reports other copies of the same skills, you have an older pstack installed. Rerun with `python3 scripts/install.py --replace` to move it aside. `uninstall` puts it back when it can do so without overwriting anything, as the [Uninstall](#uninstall) section describes.

`install` exits 3 when the run printed a line that begins `skipped`, as the **A skipped step exits 3.** bullet under [Uninstall](#uninstall) describes.

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
| `default` | A configured seat keeps its level. An `inherit` seat stays `inherit`. |
| `small` | Medium reasoning everywhere. The cheapest. |
| `medium` | High reasoning |
| `large` | Extra-high reasoning |
| `unlimited` | A seat that names no level gets the highest offered level at or below max. When every offered level is above that cap, the seat gets the lowest offered level. An `inherit` seat whose parent model declares a reasoning option becomes this thread's model at that level. A configured seat keeps its level when the level is at or below max. When its level is above max, it drops to the highest level at or below max, or to the lowest offered level when every offered level is above that cap. |

Then it proposes a provider and model for each role, reasoning from the live catalog. It weighs each role's work, its cost, and whether the work is read-only, and it spreads review seats across model families. You can accept it or change any role. No role has a built-in model. A role set in no roles file resolves to `inherit`, so it runs on the thread's own seat, and `roles.py show` prints a note that says so. `review backups` stays unset until you set it. [`roles.py show`](cli/roles.md#rolespy-show) adds two paragraphs as `haikuBrief` to each role whose resolved seat runs Claude Haiku 5.5, and the lead pastes them at the end of that child's brief. An `exclude` entry is an object naming a `model` glob and/or `options`, such as `{"model": "grok-4.7-build-fast"}`. `roles.py show` skips a refused seat and says so. `validate` reports it and exits 1, and `write` refuses it even with `--force`. A skill test whose child launches seats resolves with `roles.py show --role "skill tests" --launches-seats`. That form refuses a seat that runs on the Cursor driver, because Cursor's harness cannot pass a seat's options object. `verifiers` is whatever panel you seat. Add one seat per model family you want represented. It finishes with a one-word smoke test to every provider it picked, so a signed-out provider shows up now rather than mid-task.

You can skip setup entirely. Every role except `review backups` then resolves to `inherit`, so children run on the thread's own seat, and `review backups` stays unset. A seat whose model you cannot run falls back, and the report names each replacement.

### When a provider hits its usage limit

A usage limit relaunches the failed seat on a `backups` seat when one is free. The lead reads the failed child's error text and runs `roles.py backup` with it. The command prints `relaunch`, `panel`, `park`, or `not-usage-limit`. On `relaunch` the lead starts a fresh child on the printed seat. On `panel` the lead seats the printed `review backups` panel. On `park` the lead launches nothing, and that item waits for the reset.

| Failed seat | Backup |
| --- | --- |
| A worker, any role that is not a reviewer | The role's first `backups` seat whose provider is not the failed provider and is not out |
| A reviewer role, such as `verifiers`, `interrogate reviewers`, or `arena cross-judge pool` | The role's first `backups` seat whose provider is not failed or out and whose model family did not write the diff |
| A `review backups` seat | None. A `review backups` seat has no backup, so it counts as no pass |
| A `verifiers` seat whose `backups` seats are all out, when `review backups` is set | The `review backups` panel, at least two seats |

The backup comes only from the `backups` key in your roles file. Set a `backups` list per role to name the seats `roles.py backup` tries in order. A candidate is skipped when its provider is the failed provider, is already out, is not runnable or not in the catalog, the `exclude` list refuses it, or, for a review role, its model family wrote the diff. A seat parks when no candidate is left. A `review backups` seat has no backup. After the reset time in the error, `roles.py backup --resume` returns the original seat, whatever its provider, once that provider is back. Otherwise it returns the next `backups` seat. The backup is one hop, so a second limit parks a worker. A standing coordinator relaunches first. Whatever `backup` prints, it then asks whether to run new work in [light mode](light-mode.md) until the limit resets, and it changes the mode only on your answer or when the menu's budget says to. Every `roles.py` command and flag is in the [command-line reference](cli/roles.md).

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

Four rules keep a long run from stalling or piling up.

- **Owner reports.** An Autopilot or Orchestrate owner sends its report lines to the lead with `t3_thread_send` and `mode: "auto"`. `auto` starts an idle lead, steers a running turn, or queues behind a turn that cannot take steering yet. The lead checks each head a line names, and arrival order never makes a head current. A queued correction that sits behind a long turn can be moved into the owner's active run with `t3_queue_promote_to_steer`. It counts as delivered only when the owner's activity shows it.
- **Command capacity.** Local builds, tests, and verifier reruns run through `land.py slot --`, and timing measurements through `land.py slot --exclusive --`. The slot limits heavy commands on the machine. It never caps how many owners or children run.
- **Decision-only pauses.** A finite program, such as Autopilot or Orchestrate, pauses an audit schedule when its next run could only repeat a question you have not answered. It raises the question once and resumes the same schedule when your answer permits work. A schedule that renews a lease, drains the queue, takes intake, or notices a merge never pauses, and a coordinator's schedules are never paused this way.
- **Run now.** When an enabled interval schedule's work is ready, the lead can run it at once with `run_scheduled_task_now` instead of waiting for the next tick. The result means the run was dispatched, not that it finished.

The lead can also fork a thread, move an owner to another model, and publish a page.

- **Forks.** `t3_thread_fork` starts a separate read-only planning or investigation thread from another thread's context, and only when you asked for a separate thread. A fork never replaces a child agent or a review round. `t3_thread_merge_back` carries a fork's reasoning back to a related thread only when you authorized it.
- **Owner model changes.** `t3_thread_configure` moves an idle launched owner to another model only when a playbook step already calls for it, such as Orchestrate's retry after a tool error. The owner keeps its history, gates, scope, and retry count, so it is never a fresh worker or a fresh reviewer. It is never used on a verifier or reviewer thread.
- **Visual reports.** When a reply is already due and a table, a timeline, or a chart carries it better than prose, the lead publishes that part as a page with `html_preview` and `html_render`. Every decision that waits on you, every PR link, and the store and report paths stay in the reply text. A coordinator at `digest` renders no page except the activity page you ask for.

## 7. Pick up where you left off

```
$recall what was I doing on the billing migration?
```

`$recall` searches your T3 threads in the project, plus git and PR state, and returns a short current-state brief. To take over another thread's in-flight work, ask `$poteto-mode` to pick up that thread. Its Session pickup playbook reads the thread, its child tasks, its branch, and any resume note.

## 8. Open a standing coordinator

A coordinator is a pinned T3 thread for one project or one focus area. It takes requests, hands each unit of work to a playbook, has another model family review the result, and lands what passes. It never writes code. For one finite program with a done condition, use `$poteto-mode`. Its Orchestrate playbook stops when that work is done.

Every command and flag of `activity.py`, `brigade.py`, `land.py`, and `roles.py`, with its choices and defaults, is in the [command-line reference](cli/README.md). This guide shows the commands you need and does not list every flag.

Type this in any thread.

```
$brigade open a standing coordinator for <project> focused on <goal>.
```

The opener asks who lands the work, unless you already named a mode. It recommends `merge` when the repository has a remote. The mode belongs to the repository. Every coordinator on that repository shares it. If the repository already has a landing contract, the opener tells you its mode. Choosing another mode switches it for every coordinator on that repository.

The opener also asks how often the coordinator replies, unless you already named a level. It recommends `milestones`.

### Landing modes

| Mode | What reaches trunk | Are you a gate? |
| --- | --- | --- |
| `merge` | The queue rebases each entry onto the last candidate of [the line](../skills/landing/SKILL.md#the-line), or onto trunk when the line is empty, runs the checks, pushes `landing/e<n>`, and opens a PR. It merges the first PR of the line when the check read allows it and trunk holds the tree the candidate was built on. | No. You review what landed. A required approving review pauses the queue until you relax that rule or switch to `human`. |
| `human` | The queue rebases each entry onto trunk, runs the checks, pushes `landing/e<n>`, and opens a PR that it leaves open. The entry counts as landed when someone merges that checked head. | Yes. You merge every PR. |
| `push` | Rebased commits that passed the checks, pushed to trunk with `git push --force-with-lease` only while the remote is still at the tested base. There is no PR. | No. You review the history afterward. |
| `local` | Nothing on the real trunk. The queue moves `refs/landing/<trunk>`. The remote does not change. | Yes, for the real trunk. You merge that ref into a branch when you are ready. |

In `merge` mode, `land` reads posted checks before any `gh pr merge`, including `--auto`. A pending posted check waits. A failed posted check bounces the first entry of the line. The first `land` run that sees no posted checks records that run and waits. A later `land` merges when checks are still absent. With no posted check, the entry waits, and the queue does not pause when `gh pr merge` is refused because the base branch policy prohibits the merge. A plain merge that fails for any other reason pauses the queue, including when `--auto` reports that auto-merge is disabled. A failed or unreadable check read pauses the queue and does not merge. A later `land` reads the PR state before it acts on a failed check. A PR already merged at the candidate head is landed in that same run once git shows trunk holds its merge commit, the tree comparison is made, and its queue branch is gone, even when a check fails afterward. An open PR noted `merge requested by the queue` bounces when a check fails, and the lease becomes active. After the PR merges, `land` deletes remote `landing/e<n>`, and the entry lands when the server accepts the delete. When the delete fails, `land` asks the forge whether the branch is gone. HTTP 404 lands the entry only when the contract remote has one push URL and that URL names the same owner and repository that `gh repo view --json nameWithOwner` resolves. Where the forge cannot be asked, a failed delete lands the entry only when git says `landing/e<n>` does not exist and `gh pr view` shows the PR was opened from another branch. Any other result leaves the entry for the next run. `land` also deletes a local `landing/e<n>` when git can. When that local delete fails, the entry still counts as landed and `land` prints the branch it left. [Pull request lifecycle](../skills/landing/SKILL.md#pull-request-lifecycle) in the landing skill states each of these rules once.

`push` is the name to use. `auto` is an older alias of `push`.

[`land.py mode`](cli/land.md#landpy-mode) switches among `merge`, `human`, and `push` only while nothing is queued, landing, or awaiting merge. A move to or from `local` needs a new contract. [How work lands](how-it-works.md#how-work-lands) describes the queue those modes share.

### Reporting levels

The level is stored for that coordinator. `brigade.py open --reporting` sets it, and `brigade.py set --reporting` changes it later. Opening an existing coordinator does not change its level. The choices and the default are in the reference for [`open`](cli/brigade.md#brigadepy-open) and [`set`](cli/brigade.md#brigadepy-set).

| Level | What the coordinator sends |
| --- | --- |
| `every-turn` | A short reply after every wake. |
| `milestones` | A reply when work merges, a review sends work back or blocks it, a decision needs you, something fails or the queue pauses, or you send a message. A routine wake ends with no reply, or with one line when the host requires text. A liveness check with nothing new, a liveness check while a review is pending, a review starting, and a worker launching are routine. |
| `digest` | A reply only for a decision you must make, a failure or a paused queue the coordinator cannot fix itself, one summary when a batch drains, the 18:00 report, and a message from you. Every other wake ends with no reply text at all, not even a status line. That includes a worker reporting back, a review starting or finishing, a send-back, a queue bounce, a worker launching or being replaced, a merge that does not drain the batch, a pull request wake, a landing run that neither drains the batch nor pauses the queue, a liveness check, intake with nothing new, and a schedule created, recreated, or deleted. |

A batch has drained when `brigade.py status` shows none of `in progress`, `in review`, `passed review`, or `waiting to land`. Status omits a count of zero, so a missing label is a count of zero. `waiting to land` includes a pull request that awaits merge.

Every level sends the scheduled 18:00 report. The 09:00 run is not a report. A message from you gets at least one line, and a direct question gets an answer. [How a coordinator reports](how-it-works.md#how-a-coordinator-reports) is the same rule from the coordinator's side.

`brigade.py close` runs on the 18:00 report, on a reply that raises a decision for you, at the end of a turn begun by your message, and on the reply that closes the coordinator. At `digest` every reply runs it once, including the summary sent when a batch drains and a reply about a failure. At `every-turn` and `milestones` every other reply stays plain and does not run `close`. `close` writes that report and records its time. At `every-turn` and `milestones` the reply pastes the `close` output. At `digest` the coordinator runs `brigade.py close --to-file`, which writes the same report and prints only its path.

A `digest` message is written for you, not for an engineer. It is a few plain sentences on what got done and what it means, what comes next, and anything you must decide. It carries no work or ticket IDs, SHAs, file paths, review-round counts, or tool names. Merged pull requests may follow as a short list of plain titles linked to each PR. Commits, IDs, and schedule times stay in the store and the full report. The last line names the path of the full report. The next report starts from that time, so it omits what this run already listed.

Ask a coordinator what its agents are doing and it shows the activity page. [`activity.py`](cli/activity.md) builds that page from the coordinator's store and T3 Code's state database. The page shows what is running now, what is queued, a timeline of agents and sub-agents grouped by unit, and the units in flight with their state and pull request link. It covers the last 3 hours by default. When the document exceeds its size limit, the page can fold finished rows into summaries or omit rows, and it counts the agents and work items those changes cover. A pull request link is shown only as an `https://host/owner/repository/pull/number` address. A note counts the work items the page shows without their link, for that reason or for size. Every string on the page passes one filter that removes each word it reads as a T3 Code thread or sub-agent id, an absolute or home-relative file path, a `file:` address, or an email address. The [reference](cli/activity.md) states what it reads as each.

The opener runs [`brigade.py open`](cli/brigade.md#brigadepy-open) with the project root, a name, and the reporting level. It prints `opened` or `exists`, then the store path `${XDG_STATE_HOME:-~/.local/state}/pstack-t3/brigade/<project-slug>/<name-slug>/`. Each slug is the project directory name or the name you gave, in lowercase. Each run of characters other than a-z and 0-9 becomes one hyphen, and leading or trailing hyphens are dropped.

The store holds these files.

- `menu.md`, with the headings `Purpose`, `What good looks like`, `Off the menu`, and `Budget`.
- `house-rules.md`.
- `restaurant.json`.
- `rail.tsv`, `dishes.tsv`, `pass.tsv`, `86.tsv`, `goals.tsv`, `edits.tsv`, and `log.tsv`, each with a header and no rows.

The opener drafts `menu.md` from the README, AGENTS.md, open work in the repository's tracker, and recent threads, and asks you for the purpose when evidence does not settle it. `brigade.py brief` refuses to write a worker brief while `Purpose` is empty or still the template. The opener appends standing orders to `house-rules.md`. They name forbidden paths, the verification bar, intake sources, and the repository's hot shared files. They do not restate the landing mode, which lives only in the repository's landing contract. The worker cap is `open --workers`, and `brigade.py fire` enforces it.

The opener launches the coordinator with `t3_thread_launch` on the project root. The sidebar title is `Head chef: <name>`. On its first run the coordinator pins that thread. If that thread is in another project, the thread you typed in cannot read it afterward.

### Runs on this project

38 of the 39 pull requests from #8 to #46 landed through the same queue. The queue then added a line to each body naming the reviewer and the commit that reviewer checked. It no longer adds that line, and the reviewer stays in the queue's local store. Timed runs are in [Live runs](live-runs.md). The earlier pilot is audited under [Pilot: Bridgekit performance, 2026-10-04](brigade-plan.md#pilot-bridgekit-performance-2026-10-04).

### What is frozen

Two pieces are frozen. A frozen piece keeps the tests it has and gets no new work. A fix for data loss or for a broken install still lands.

- **The executive admin.** The `brigade-admin` skill, opened with `$brigade-admin`.
- **The `local` and `push` landing modes.** The `merge` and `human` modes are not frozen.

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

- **Intake.** It reads `menu.md` and `house-rules.md` and records each request. It reads open work from the tracker AGENTS.md names, with the commands AGENTS.md names, and runs `gh issue list` only when AGENTS.md names no other tracker. It runs `gh pr list` only when the standing orders name pull requests. A ref the request names is its ref. Otherwise it matches the request against the tracker's open items, and a user request is still filed when the tracker cannot be read. It creates an item there only when AGENTS.md's filing rules call for one and you or the house rules allow that write.
- **Delegation.** It groups related requests, claims a path lease, writes the brief with `brigade.py brief`, and launches one worktree thread per unit. In-flight work stays under the worker cap, which `fire` enforces. Each ticket carries a priority of `urgent`, `normal`, or `low`. `brigade.py startable` lists the waiting tickets in start order with why each can or cannot start now, and it starts nothing. `brigade.py set --autofire` sets the lowest priority that `startable` lists as startable, or `off`.
- **Cross-family review.** It reads the worker's report and diff. One reviewer from another model family checks that exact commit. A pass is recorded against that commit. A reviewer from the author's family is used only when no other family can run, and the verdict says so. Every round, pass or fail, is written to its own file under the store's `reports/` and recorded with that file by [`pass record`](cli/brigade.md#brigadepy-pass-record). Its `--member` and `--late` flags record a panel member's row and a round found unrecorded after the unit moved on. `brigade.py close` warns on stderr about a review report that no row names. A unit that has been sent back three times since its last pass or its last answered round decision gets no further fix round until you answer one decision. [`86 add --round-budget`](cli/brigade.md#brigadepy-86-add) writes it with the options `known limits`, `redesign`, and `drop`. `keep parked` is the default and leaves it open. Until you answer it, `dish`, `brief`, and `fire` refuse another fix round for that unit.
- **Follow-ups.** On a pass, the coordinator runs [`ticket add --from-report`](cli/brigade.md#brigadepy-ticket-add) on the worker's report. It files one waiting ticket, with source `report`, for each follow-up under a `Follow-ups` heading, and skips text that a ticket already holds in any state. A worker's brief asks for a `## Follow-ups` heading with one top-level list item for each follow-up, or `None.`
- **Landing.** On a pass it submits that commit to the queue and runs `land.py land`. Workers never merge. In `merge` and `human` mode the PR title and body go with the submit. A conflict or a changed rebase goes to a fresh worker and needs a new review.
- **Cleanup.** After a unit merges, is dropped, or is sent back, it removes that unit's worktree and branch. It deletes `landing/e<n>` after that PR merges or closes. It deletes only branches it created. It fast-forwards your checkout with `git merge --ff-only` only when that checkout is clean and on trunk.
- **Reports.** The reporting level decides which wakes get a reply. `brigade.py close` runs only on the occasions named under [Reporting levels](#reporting-levels). Its output lists each request and unit once, under its latest state since the last report, with PR links. Any other reply stays plain and does not run `close`.

To list every coordinator on this machine, run `python3 ~/pstack-t3/skills/brigade/scripts/brigade.py walk` ([reference](cli/brigade.md#brigadepy-walk)). If your checkout is not `~/pstack-t3`, use that checkout's `skills/brigade/scripts/brigade.py`. It groups coordinators by repository. Each repository's header shows its landing mode and queue. Each coordinator shows its reporting level, counts, thread, leases, and open decisions. A coordinator idle for more than 24 hours is marked. [Several coordinators on one repository](#several-coordinators-on-one-repository) shows the layout.

To stop one, ask it to close. It deletes its schedules, writes a last report, and unpins its thread. The store stays.

### Several coordinators on one repository

You can open more than one coordinator on a repository, each with its own purpose. One might own the docs while another owns the engine. Coordinators whose `restaurant.json` names the same project root are siblings. They share the repository's landing contract, its leases, and its queue. Each keeps its own store and is the only writer of it.

**Opening a second one.** `brigade.py open` prints a block for each sibling, with its store, its thread, its purpose, and what it does not take. The opener keeps the new purpose clear of those and lists each sibling's purpose under `## Off the menu`. Two openers racing for one name get one coordinator. The one that created the directory prints `opened` and launches the thread. The other prints `exists`. A coordinator's store is `<project directory slug>/<name slug>`. When another repository's directory has the same slug and a coordinator there has the same name slug, `open` refuses with `pick another --name`. Coordinators in repositories whose directories have different slugs can reuse a name. Rerun `open` on an existing coordinator at any time to see its current siblings.

**Caps.** Two caps keep coordinators from crowding each other.

- The repository cap counts changes in flight across every coordinator. It counts every submitted lease and every active lease that has not expired. A submitted lease counts until it is released, even past its expiry, so a change waiting to land always counts. An active lease that expires stops counting before it is released, and a sibling can claim that room. It counts again only after `land.py lease renew` admits it again, which refuses while the repository is at its cap. Set it with `--cap` on [`land.py init`](cli/land.md#landpy-init), or later with [`land.py cap`](cli/land.md#landpy-cap). A count of 0 clears it. A claim at the cap is refused with `repository at its cap: 3 of 3 changes in flight`, followed by the holders.
- The coordinator cap counts that coordinator's units in progress or in review. Set it with `--workers` on [`brigade.py open`](cli/brigade.md#brigadepy-open) or [`set`](cli/brigade.md#brigadepy-set). A missing value reads as 2. `open` warns when it is at or above the repository cap while a sibling exists, because one coordinator could then fill the repository alone.

**Intake.** Each intake source, such as `github`, has exactly one owning coordinator. `--intake` on [`open`](cli/brigade.md#brigadepy-open) or [`set`](cli/brigade.md#brigadepy-set) records it, and both refuse a source a sibling already owns. `ticket add` refuses a source this coordinator does not own, and a ref that is still open here or in a sibling. So one issue becomes one ticket. A request from you with no ref is refused while a waiting or assigned ticket of that coordinator has the same summary, unless `--again` is passed. A request from you is filed with the `user` source, which every coordinator accepts.

**Handing a ticket over.** When the owner of a source files a ticket that is a sibling's work, it runs [`ticket move`](cli/brigade.md#brigadepy-ticket-move) to hand it to the sibling and messages that sibling's thread. The sibling runs [`ticket take`](cli/brigade.md#brigadepy-ticket-take), which files it once, even after a crash or a retry. Until the sibling takes it, the sender's `watch` prints `T6: moved to engine, waiting for ticket take`, and the sibling's `watch` and `status` print `handed to you: 1`.

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

#### An executive admin

Everything above works with no admin. Add one when several coordinators share a repository and you would rather hear from one thread than from each of them. The executive admin works for you on one repository. It forwards your requests, files shared intake once, settles conflicts between coordinators by published rules, and sends you one plain update. It never writes code, starts work, lands work, or overrides a review. Each coordinator still owns its own work, reviews, and queue entries. If the admin's thread goes away, every coordinator keeps working and replies to you directly again.

**Opening it.** Type this in any thread.

```
$brigade-admin open an executive admin for <project>.
```

The opener asks how often it replies, unless you already said. It recommends `digest`, because the admin exists so you hear less. It runs [`brigade.py open --admin`](cli/brigade.md#brigadepy-open) with the project root and `--reporting digest`, which creates the store `<project directory slug>/.admin`. There is one admin per repository. A second open prints `exists`. The output lists every coordinator on the repository with its purpose and what it does not take. The admin store refuses `fire`, `brief`, `dish`, `pass`, and `watch` with `the executive admin routes work and never runs it`.

The opener then asks you for two things.

- **Your priorities.** A ranked list of coordinator names under `## Priorities` in the admin's `menu.md`, highest first. The rules below read it. A coordinator the list does not name ranks below every named one. Only you change the list. Without one, the rules fall back to purposes and age.
- **Shared intake.** Which sources the admin should own, such as `github`. Each coordinator that owns that source gives it up first, after it finishes or moves its open tickets from that source. It runs `set --intake` with its remaining sources, or `set --intake ""` when that source was its only one. Then the admin takes it with `set --intake github`. The admin files each issue once and routes it to the coordinator whose purpose fits.

Last, the opener launches the admin's thread in the repository's project and pins it. On its first run the admin tells each coordinator to report to it, and each records that with [`set --reports-to`](cli/brigade.md#brigadepy-set) and the admin's thread. A coordinator's `status` then prints `reports to <thread>`, and `walk --repo <root>` lists the admin first in that repository's group, as `executive admin (reports digest)`.

**Sending it requests.** Write to the admin as you would to a coordinator. It routes each request to the coordinator whose purpose fits. When two fit, it makes an ownership ruling. When none fits, it asks you. A request that is an instruction rather than new work, such as "pause the engine work", goes to that coordinator with your words quoted. That coordinator can decline with a reason, and the admin passes the reason on. Each request is a file in the coordinator's `inbox/`, so a retried message never files the same work twice. The coordinator acts on it with `inbox take` and `inbox done <id>`, and `ticket add --request <id>` refuses a second ticket for one request. While requests wait, the coordinator's `status` prints `requests from the executive admin: N`.

You can still write to any coordinator directly. It answers you as it always did.

**Rulings.** The admin settles four kinds of conflict on its own authority, and logs each one.

| Kind | The question | Rules, in order. The first that separates the two coordinators decides. |
| --- | --- | --- |
| Contested paths | Which coordinator claims a contested path next, including `README.md` or `docs/guide.md` | The coordinator whose purpose names the path, when the other's `## Off the menu` excludes it. Then your priorities. Then the work that has waited longer. |
| Ownership | Which coordinator owns a request that fits two purposes | A coordinator whose `## Off the menu` excludes it loses. Then your priorities. Then the coordinator whose open work already touches it. Otherwise the admin asks you. |
| Shares of the cap | How many changes in flight each coordinator may hold, out of the repository cap you set | One each for every coordinator with waiting work, by your priorities and then age, until the cap runs out. The rest by your priorities, up to each coordinator's waiting work. A tie goes to the older waiting work. |
| Queue order | Which of two passed changes lands first when one would break the other | The change the other depends on. Then your priorities. Then the side that was waiting first. |

When no rule separates the two, such as an exact tie, the admin asks you instead, with options and a default. It also asks you when the rules keep ruling against one coordinator, when two purposes keep colliding, and before anything irreversible. It never changes the repository cap, the landing mode, your priorities, or any purpose on its own. It runs `land.py cap` or `land.py mode` only when you ask, and then tells every coordinator.

A coordinator must comply with a ruling. When it disagrees, it complies and appeals. The admin rechecks the ruling with the new facts, and either replaces it or keeps it and lists the appeal in its next update for you. When compliance would be irreversible, the coordinator holds instead of complying, and the admin asks you.

**Overruling.** Every update lists the rulings made since the last one, each with the rule that decided it. To overrule one, describe it in plain words, such as "let the engine coordinator take the README first." The admin runs [`rule overrule`](cli/brigade.md#brigadepy-rule-overrule) with that ruling and your words, which marks the old ruling `overruled` and records yours in its place, then carries yours out. An overrule changes what happens next. It cannot undo a lease already claimed or work already landed, and the admin says so. When your words state a general preference, the admin asks whether to add it to `## Priorities`. `rule list` in the admin's store prints the log.

```
R1 overruled contested-paths (docs, engine): Who claims README.md next? Decided by age: docs claims README.md next.
R2 in-force contested-paths (docs, engine): Who claims README.md next? Decided by user: engine goes first. Supersedes R1.
```

**How rulings are enforced.** A script carries out each ruling, so a ruling holds even while two coordinators act at once. No ruling takes away a live lease. The ruling writes `lease reserve`, `lease unreserve`, `share`, and `contest` each require `--owner`. The admin passes `--owner .admin/@<generation>`, so a write from a replaced admin thread is refused. A coordinator that opens a contest passes its own writer token, `--owner <coordinator>/@<generation>`. The [land.py reference](cli/land.md) shows which commands take `--owner`.

- **Reservations.** A contested-path ruling runs [`land.py lease reserve`](cli/land.md#landpy-lease-reserve) for the winner's prefix, the paths, and the ruling, which prints `S<n>`. Every other coordinator's claim on those paths is then refused with `paths reserved for docs/ by ruling R4 until <time>`. The current holder keeps its lease and finishes. The reservation arms, and its 2-hour clock starts, only when no other lease overlaps it and there is room under both the cap and the winner's share. An armed reservation holds that room, so the winner can claim. A reservation the winner never uses expires, and the admin rules again if the conflict remains. `lease list` shows reservations. `lease unreserve S<n>` lifts one.
- **Shares.** [`land.py share`](cli/land.md#landpy-share) with `--for docs/` and a count of 2 limits that coordinator to 2 changes in flight. A claim over the share is refused with `docs/ is at its share: 2 of 2`. Setting a share refuses one that would make the shares add up to more than the current cap. Shrinking a share stops no running work. It only refuses the next claim until that coordinator is back under its share. `--clear` with a count of 0 removes one. A coordinator's own worker cap still applies.
- **Contests.** When a coordinator finds that its passed change and another coordinator's would conflict in the queue, it runs [`land.py contest`](cli/land.md#landpy-contest) with `--holders` naming its unit and the other unit, which prints `C<n>`, and tells the admin. Until the admin rules, `land` keeps both sides' entries queued and prints `still queued: E1 (held by C1), E2 (held by C1)`. The ruling runs it with `--settle C<n>` and `--first <unit>`. From then on the second side waits until the first side lands, even if the first bounces and resubmits. `contest --cancel C<n>` removes the hold for a conflict that turned out not to exist.

**What you hear.** You hear from the admin, not from each coordinator. A coordinator that reports to the admin sends it every event and replies to you only when you write to it directly. The admin's reporting level uses the same three values as a coordinator's, listed under [Reporting levels](#reporting-levels), with these differences. At `every-turn` it replies after each of its own wakes. A coordinator's routine event is not one of them. At `milestones` it replies when any coordinator's work merges, when a review sends work back or blocks it, when you have a decision to make, and when a failure arrives. At `digest` it replies for a decision you must make, a failure no coordinator can fix itself, one summary when every coordinator has drained, and an evening update at 18:30, after the coordinators' 18:00 reports. A ruling alone is never a reason to reply at `milestones` or `digest`. It waits for the next update. Each reply is a few plain sentences per purpose on what changed, what is next, and what you must decide, with no ids, paths, or tool names. It ends with one line naming the full update file, which holds each ruling's id.

**Recovery.** The admin's store outlives its thread. If the thread breaks, ask any thread to recover the executive admin. Recovery claims the store with `set --thread recovering:<its thread> --replace --expect <old thread>`, so two recoveries never both win. It interrupts the old thread and waits for its run to end, then launches a new thread and records it with `set --thread <new> --replace --expect recovering:<its thread> --stopped <run id>`. Without `--stopped`, that command refuses with `the old run has not been confirmed stopped`. Each replacement raises the store's generation, so any write the old thread still makes is refused with `owner ... is stale` and changes nothing. Reads such as `status` still work. Tickets, owned intake, and rulings survive in the store, and the new thread's first run picks them up.

To retire the admin, ask it to close. It routes or drops its waiting tickets, gives up its intake, deletes its schedules, sends a last update, and clears its thread. Each coordinator then runs `set --reports-to ""` and replies to you directly again.

## Tips and pitfalls

- **Run fan-out work in a mode that allows commands and edits.** Children inherit the lead thread's runtime mode. In approval-required mode, a child can stall on an approval prompt that no tool can answer, and it looks idle while it waits.
- **Panels multiply cost.** Three reviewers cost about three reviews. Use the `small` budget for routine work and `large` or `unlimited` when it matters.
- **One provider is enough to start.** Everything works with a single provider. You lose model diversity, and reports say so.
- **Prefer the user install over a project install.** Claude and Grok load user-level skills over project-level ones with the same name.
- **Check the installed skills with `doctor`** whenever a skill seems to behave like an older version.
- **`doctor` lists leftover records.** A claim is a link that this checkout's file under `install-owners/` records. Under each harness, `doctor` prints this checkout's claims that it can prove neither at their path nor in a recorded backup, backup records with nothing at their backup path, and owner files of other checkouts whose checkout directory is missing. A record that prints alone names on its line a command to run, or the entry or file to delete. Two or more under one harness with the same advice print as one group, a line with their count and the advice and then one line per record. A backup record with a relative path and no entry held beside it never joins a group, and neither does an owner file of another checkout. For that backup record `doctor` prints `backup row <backup>: a relative path with nothing at it from this working directory (recorded as the backup of <original>); uninstall reads a relative path from the directory it runs in; to drop the row, delete it from "backups" in <manifest>`. `doctor` writes nothing, and these lines do not change its exit status. It names a record file it cannot read and still runs the checks that do not need that file. It exits 1 when that file is `install-manifest.json` or this checkout's owner file. In a checkout with no `skills/` it prints only `skills/ is missing; run python3 scripts/build.py first` and exits 1. When `skills` exists and is neither a directory nor a link to one, it prints only `skills is not a directory; move it away, then run python3 scripts/build.py` and exits 1.

### Install paths

| Provider | User directory | Project directory |
| --- | --- | --- |
| Claude | `~/.claude/skills` (or `$CLAUDE_CONFIG_DIR/skills`) | `.claude/skills` |
| Codex | `~/.agents/skills` | `.agents/skills` |
| Grok | `~/.grok/skills` | `.grok/skills` |
| Cursor | `~/.cursor/skills` | `.cursor/skills` |

## Uninstall

```bash
python3 scripts/install.py uninstall
```

`--project /path/to/repo` and `--harness claude,codex` select what to uninstall, and `--dry-run` prints the plan. Uninstall acts for the checkout you run it from.

- **It removes only links to its own checkout.** Each checkout records its links in its own file under `install-owners/`, in `~/.config/pstack-t3` (or `$XDG_CONFIG_HOME/pstack-t3` when that variable is an absolute path) for a user install and `<repo>/.pstack` for a project install. Uninstall removes a link only after it proves the link points at this checkout's `skills/<name>`. That includes this checkout's links that another checkout's `--replace` moved aside, and uninstall says how many. It leaves a link that points somewhere else, so worktrees and clones on one machine do not remove each other's skills.
- **A relative `XDG_CONFIG_HOME` is ignored.** A command of the installer or of `roles.py` that reads the variable then uses `~/.config/pstack-t3` and prints `XDG_CONFIG_HOME=<quoted-value> is not an absolute path, so it is ignored and the config home is <quoted-path>` on stderr. The quoted placeholders use Python's `repr`. The installer reads the variable only without `--project`. To keep using a directory an earlier run used under a relative value, set `XDG_CONFIG_HOME` to the full path of that config home. To move to the default location, run `uninstall` from the earlier working directory under that full path, unset the variable, and run `install`.
- **It restores what `--replace` moved aside.** Uninstall restores a backup only to an empty path, and only while the backup is still the entry it read when it planned. A backup that is no longer that entry is never put back in its place. Uninstall leaves whatever it finds at the backup path. When it detects the change, it prints a line that begins `skipped restore` or `kept` and names the path. A backup on another filesystem is restored by copy.
- **A taken original path keeps the backup.** When uninstall finds the original path of a backup taken, it keeps the backup and prints a line that begins `kept backup` or `skipped restore`. When the occupant is a link into a deleted checkout, the `kept backup` line says so. Clear the path and rerun uninstall.
- **Interrupted runs are recovered.** A stop or an error in the middle of a move can leave an entry in a `.pstack-t3-*` directory beside the path it left. When a step fails with an entry left there, its `skipped` line ends with `; it is kept at <path>`. Every later install or uninstall looks in those directories, under the skills directories of the harnesses you ran it for and in the backups filed for them. That includes a harness that shares a skills directory with one of them. It prints `recovered <path> from <entry>` and moves the entry back when a backup record or this checkout's own record proves where it belongs, and it never overwrites a taken path. It deletes only a second name for an entry that is already in place, and this checkout's own link when its path is taken, and prints `removed <entry>: <reason>`. It prints `left <entry>: <reason>` on every run for an entry it cannot prove. Move or delete that entry by hand. `--dry-run` prints `would recover`, `would remove`, and `left` lines and changes nothing. An install that stops on a taken path prints the `left` lines and recovers nothing.
- **A contested backup restores neither.** When a backup is in a `.pstack-t3-*` directory and another entry has taken its backup path, uninstall restores neither and keeps the backup's record. Its `left` line names both. Remove the one that is not the backup and rerun uninstall.
- **A state file that is not valid stops the run.** If `install-manifest.json` or this checkout's owner file is not a valid JSON object, install and uninstall stop, name the file, and change nothing. Fix or move the file and rerun. They stop the same way, with the line `<file> could not be read (<error>); clear that error and rerun`, when either file is a directory, or when the user is not root and either file or `install-owners/` is of mode 000. They also stop, with the line `<file> records the relative path <path>, and the system could not name this run's working directory (<error>); change to another directory and rerun`, when a link row's path, a backup row's original, or a claim of this checkout is relative and the working directory cannot be named. They also stop, with the line `<NAME>=<quoted-value> is not an absolute path, and the system could not name this run's working directory (<error>); change to another directory and rerun`, when `HOME` is set and not absolute, or `CLAUDE_CONFIG_DIR` is not empty and not absolute, and the working directory cannot be named. The `<quoted-value>` placeholder uses Python's `repr`. When uninstall cannot remove this checkout's owner file with its last record, it stops with the line `<file> could not be removed (<error>); clear that error and rerun`. By then it has removed at least one link.
- **A skipped step exits 3.** `install` and `uninstall` exit 3 when the run printed a line that begins `skipped`. The run continues and prints its summary line. `--dry-run` never exits 3. Lines that begin `kept` or `left` do not change the exit status. A run that stops before its summary exits 1, for example `install` on a taken path without `--replace`, or either command on a state file that is not valid. A usage error exits 2.

Uninstall never deletes `install-manifest.json`. Once it has removed every record, the file's `links` and `backups` lists are empty. A checkout's file under `install-owners/` is removed with its last record, and the empty `install-owners/` directory stays. An install that moves an entry aside files it under `backups/<stamp>/<harness>/`. Where the platform allows it, uninstall removes that backup's `<harness>` directory and then its `<stamp>` directory after it restores or withdraws the backup, when each is empty and nothing from `backups/` down is a symlink. It never removes `backups/` or a directory that holds anything. Your `~/.config/pstack-t3/roles.json` is left in place.

Known limits.

- An entry in a `.pstack-t3-scrap-*` directory is an unfinished copy or a backup that was already restored. Nothing returns or deletes it. Delete it by hand.
- If another entry replaces a backup and no `.pstack-t3-*` directory holds the original, uninstall cannot tell the two apart and restores the entry it finds.
- A process of the same user that works inside a `.pstack-t3-*` directory during a run can swap an entry there.
- Off Linux, uninstall keeps a backup it cannot move without risking an overwrite, such as a directory, and says why. Tests exercise that route by simulation only.

## What you can say

| You type | What happens |
| --- | --- |
| `$poteto-mode the export button sometimes downloads an empty file. repro first, then fix and verify.` | Bug fix playbook. It reproduces, finds the root cause, fixes, and proves the reproduction now passes. |
| `$poteto-mode add rate limiting to the public API.` | Feature playbook. It names the data shape, settles the design, builds in verifiable steps, and verifies. |
| `$interrogate review this branch.` | Reviewers on different model families attack the diff. You get one verdict. |
| `$arena write the caching layer.` | Several candidates on different models. It picks a base and grafts in the best ideas. |
| `$swarm check every API route for missing auth.` | One worker per slice, with a single report of PASS, ISSUES, or BLOCKED. |
| `$how does session refresh work?` | An explorer agent maps the code, then an explainer agent turns it into a walkthrough. |
| `$why did we pick Postgres here?` | A cited answer from git history and whatever doc and issue tools are connected. |
| `$poteto-mode babysit PR 482 until it's green.` | It calls `watch_pull_request` and waits on CI, reviews, and conflicts. It fixes what it can and reports. |
| `$poteto-mode i'm going to bed. land the stack. everything merged by morning.` | An autonomous run with a decision log. It waits on each pull request with `watch_pull_request`, uses `schedule_task` as the merge heartbeat, and verifies each pull request before merge. |
| `$recall where did I leave off on the billing migration?` | A current-state brief rebuilt from your past T3 threads, git, and PRs. |
| `$correct` | A census of the mistakes agents repeat here, each fixed at the highest level that holds, from architecture through types, lint, and tests, plus a rule table. |
| `$brigade open a standing coordinator for bridgekit focused on startup performance.` | A pinned thread that owns that goal. It groups incoming requests, hands each to a pstack playbook, has a reviewer, from another model family when one can run, check every result against the goal, and reports what landed. |
| `$landing set up this repo so several agents can land work at once.` | A landing contract with your test commands as checks. Every coordinator then claims leases before delegating and lands through one queue. |
| `$poteto-help which skill should I use to review this branch?` | It points at the skill or playbook and hands you a prompt. It does not start the work. |

## Earlier examples

The next two runs are against a small repository with a planted bug that its own tests passed. Replies are excerpted and trimmed. They predate the move to roles resolved against the live catalog.

**A Codex thread ran a four-family review.**

```
$interrogate review the last commit, "Fix median for even-length input".
```

> **Reviewers.** Resolved `interrogate reviewers` with `roles.py`. Launched all four through `delegate_task` with identical read-only briefs.
> - A: Codex / gpt-6.1-sol
> - B: Claude / claude-opus-5-5
> - C: Cursor / Auto
> - D: Grok / grok-4.7
>
> **Act on.**
> 1. Incorrect even-length median (A, B, C, D). `stats.py:5` uses `// 2`, flooring the average. `[1, 2]` returns `1` instead of `1.5`.
> 2. The regression test asserts the bug (A, B, C, D). It expects `2` for `[4, 1, 3, 2]`. The correct median is `2.5`.
>
> I verified that all three existing tests pass despite these errors. **The commit should not pass review.**
>
> **Dismissed.** Grok's claim that `-3` is a full unit below `-2.5` is incorrect. The difference is `0.5`. Its flooring finding remains valid.

**A Grok thread ran a coverage swarm.**

```
$swarm audit stats.py: one read-only worker per public function.
```

> Coverage swarm: one read-only worker for `median` and one for `mean`. `swarm workers` resolved to `inherit`, so both run on grok / grok-4.7.
>
> **median: ISSUES.** I reran its commands and they match.
> - `median([])` raises `IndexError`.
> - Even counts return the floor: `median([1, 2])` is `1`, `median([-5, -2])` is `-4`.
> - An infinite middle value becomes `nan`: `median([1.0, inf])`.

## FAQ

<details>
<summary><b>Do I need every provider?</b></summary>

No. Everything works with one provider. No panel has a built-in model. A seat whose model you cannot run falls back, and the report names each replacement. `verifiers` is whatever panel you seat in your roles file, so signing in to more providers lets you spread it across model families. Add a provider or another model in a roles file.
</details>

<details>
<summary><b>How is this different from pstack?</b></summary>

The playbooks, principles, rubrics, and their wording are the same. The plumbing is different. Upstream uses Cursor's subagents, cloud agents, `/loop`, and a fixed list of Cursor model names. pstack-t3 uses T3's `delegate_task`, worktree threads, `schedule_task`, and roles resolved against T3's live model list. pstack-t3 also adds `$brigade`, `$landing`, and `pstack-author-skill`, which upstream does not have. The full mapping is under [What changed from upstream](#what-changed-from-upstream).
</details>

<details>
<summary><b>Does it cost more?</b></summary>

`$interrogate` costs one review per seat in your `interrogate reviewers` role, so a two-seat panel costs about two reviews. A roles file can add seats. Single-agent playbooks cost about the same as doing the work by hand, plus verification. Use the `small` budget in `$setup-pstack` for routine work, or [light mode](light-mode.md) to cut the fan-out around the checks that catch real bugs. Run skill tests and the read-only explorers in `$how` and `$why` on a cheap, high-volume model to keep their cost down.
</details>

<details>
<summary><b>Will it overwrite my existing skills?</b></summary>

No. If a skill with the same name exists, the installer lists the taken paths, links nothing, and exits. `--replace` moves each one aside and records it instead, and `python3 scripts/install.py uninstall` restores it when it can do so without overwriting anything. See [Install paths](#install-paths).
</details>

<details>
<summary><b>Why do child agents stall sometimes?</b></summary>

Children inherit the lead thread's runtime mode. In approval-required mode, a child can wait on an approval prompt that no tool can answer. Run fan-out work in a mode that allows commands and edits. See [Tips and pitfalls](#tips-and-pitfalls).
</details>

<details>
<summary><b>Does it work outside T3 Code?</b></summary>

It is built for T3. Outside T3, the skills fall back to the host's own subagents on the current model, without cross-provider panels or scheduling. For Cursor itself, use [upstream pstack](https://github.com/cursor/plugins/tree/main/pstack).
</details>

<details>
<summary><b>How do I update?</b></summary>

`git pull` in the checkout, then `python3 scripts/install.py`. The installer is safe to rerun.
</details>

<details>
<summary><b>Is this official?</b></summary>

No. It is an independent project, not affiliated with or endorsed by Lauren Tan, Cursor, T3 Tools, Anthropic, OpenAI, or xAI.
</details>

## What changed from upstream

| Upstream (Cursor) | pstack-t3 (T3) |
| --- | --- |
| `Task` subagents with `subagent_type` and `model` | `delegate_task` children with a `role` and a resolved `target` |
| Cloud agents | Local child tasks, or `t3_thread_launch` threads bound to their own worktree |
| A Cursor rule file of model names | `roles.json` resolved against T3's live catalog. No role has a built-in model, an `exclude` list refuses a model, and a usage limit relaunches on a `backups` seat when one is free and parks otherwise. |
| A fixed default panel of four Cursor models | Each panel role is a list of seats you set in `roles.json`, resolved against T3's live catalog. |
| `/loop`, automations, hourly ticks | `schedule_task` for a cadence with no pull request event. A wait on checks, reviews, or conflicts is `watch_pull_request`. A wait whose predicate is the merge also keeps the `schedule_task` heartbeat the runtime's Pull request watching section requires. |
| Cursor transcripts and cloud-agent URLs | T3 threads |
| `control-ui` from `cursor-team-kit` | T3 preview and device tools |
| Cursor's built-in `create-skill` | `pstack-author-skill`, for every provider |
| A worktree audit that reads Cursor's chat storage on macOS | Git inventory on Linux and macOS, plus T3 thread bindings |
| | `link_pull_request` on every PR a playbook opens or drives |

## Why these are skills

**Why skills, not an MCP server or a plugin?** T3 already gives every provider its orchestration server, so pstack-t3 needs no server of its own. T3's `$` picker lists each provider's native skills, so `$poteto-mode` appears whichever model you pick. Muse is the exception. T3's picker does not list Muse's skills, though Muse still loads them. A Claude Code plugin would namespace the skills and hide them from that picker, and the other providers have no plugin format.

## Roadmap

- More live end-to-end runs of the longest playbooks on real multi-PR projects. Autopilot has run once, on a four-PR scratch queue. Orchestrate has not run yet. See [Live runs](live-runs.md) for what merged and the defects each run found.
- Testing on T3's other providers (OpenCode, Antigravity, ACP agents).
- A T3-native port of Lauren's long-form guide.
- Tracking upstream pstack releases from the repository, so a new upstream commit opens porting work without a person watching. `scripts/sync_upstream.py --check` reports what upstream changed and what pstack-t3 does with each changed path. Nothing runs it on a schedule or files the porting work yet, and syncing is a manual `scripts/sync_upstream.py` run.
- Cloud workers through local cua container sandboxes. Today landing still only needs a branch, a path lease, and a reviewed commit.
- Batch bisection in the landing queue. Today a failed batch tries one entry alone, then batches the rest again. `--batch` of [`land.py init`](cli/land.md#landpy-init) applies to `push` and `local` only. `merge` and `human` open one PR per change.
- A cap on running workers across repositories. Today [`brigade.py set --workers`](cli/brigade.md#brigadepy-set) caps one coordinator's units in progress or in review, and `fire` refuses past it. [`land.py cap`](cli/land.md#landpy-cap) caps changes in flight on one repository, and [`land.py slot`](cli/land.md#landpy-slot) limits heavy commands on the machine. Nothing counts workers across repositories.

Ideas and bug reports are welcome in [issues](https://github.com/creedants/pstack-t3/issues).
