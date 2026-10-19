# Guide: your first hour with pstack-t3

This walks through installing pstack-t3, choosing models, and running real work in T3 Code. For the philosophy behind the workflows, Lauren Tan's [original pstack guide](../vendor/pstack/docs/guide/README.md) is the deep read. It is written for Cursor, but the ideas carry over unchanged.


Part of the pstack-t3 docs. Each part below names its file. Read the part it names for the full rule.
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

`doctor` should show `60/60 pstack-t3` for each provider you use. If it reports other copies of the same skills, you have an older pstack installed. Rerun with `python3 scripts/install.py --replace` to move it aside. `uninstall` puts it back when it can do so without overwriting anything, as the [Uninstall](guide/uninstall.md#uninstall) section describes.

`install` exits 3 when the run printed a line that begins `skipped`, as the **A skipped step exits 3.** bullet under [Uninstall](guide/uninstall.md#uninstall) describes.

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
| `unlimited` | A seat that names no level gets the highest offered level at or below max. When every offered level is above that cap, the seat gets the lowest offered level. A configured `inherit` seat whose parent model declares a reasoning option, on a provider that can run children, becomes this thread's model at that level. A configured seat keeps its level when the level is at or below max. When its level is above max, it drops to the highest level at or below max, or to the lowest offered level when every offered level is above that cap. |

Then it asks for the mode. Light is the default. Full is the choice.

Then it proposes a provider and model for each role, reasoning from the live catalog. It weighs each role's work, its cost, and whether the work is read-only, and it spreads review seats across model families. You can accept it or change any role. No role has a built-in model. A role set in no roles file resolves to `inherit`, so it runs on the thread's own seat, and `roles.py show` prints a note that says so, unless the `exclude` list refuses that seat. `review backups` stays unset until you set it. [`roles.py show`](cli/roles.md#rolespy-show) adds two paragraphs as `haikuBrief` to each role whose resolved seat runs Claude Haiku 5.5, and the lead pastes them at the end of that child's brief. An `exclude` entry is an object naming a `model` glob, `options`, a `provider` glob, `roles`, or a combination, such as `{"model": "grok-4.7-build-fast"}`. `roles.py show` skips a refused seat and says so. A seat the budget cap is what pushes into a rule refuses the role, and a `review backups` seat is dropped. `validate` reports a refused seat and exits 1, and `write` refuses a seat the rule matches on its own form even with `--force`. A skill test whose child launches seats resolves with `roles.py show --role "skill tests" --launches-seats`. That form refuses a configured seat that resolves to the Cursor driver, because Cursor's harness cannot pass a seat's options object. An unset `skill tests` keeps a note and is not refused. `verifiers` is whatever panel you seat. Add one seat per model family you want represented. It finishes with a one-word smoke test to every provider it picked, so a signed-out provider shows up now rather than mid-task.

You can skip setup entirely. Every role except `review backups` then resolves to `inherit`, so children run on the thread's own seat, unless the `exclude` list refuses that seat, and `review backups` stays unset. A seat whose model you cannot run falls back, and the report names each replacement.


A usage limit relaunches the failed seat on a `backups` seat when one is free. See [the usage limit detail](guide/reference.md#when-a-provider-hits-its-usage-limit) for the table and the resume rule.
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


## Map

This index keeps sections 1 through 7 plus tips and install paths. The parts hold the rest.

| Section | Part |
| --- | --- |
| Open a standing coordinator | [coordinator](guide/coordinator.md#8-open-a-standing-coordinator) |
| Landing modes and reporting levels | [reporting](guide/reporting.md#landing-modes) |
| Several coordinators | [several coordinators](guide/several-coordinators.md#several-coordinators-on-one-repository) |
| Executive admin user view | [executive admin](guide/executive-admin.md#an-executive-admin) |
| Uninstall | [uninstall](guide/uninstall.md#uninstall) |
| Usage limit and reference | [reference](guide/reference.md#when-a-provider-hits-its-usage-limit) |

### Landing modes

See [reporting](guide/reporting.md#landing-modes) for the four modes.

### Reporting levels

See [reporting](guide/reporting.md#reporting-levels) for the levels.

Ask a coordinator what its agents are doing and it shows the activity page. [`activity.py`](cli/activity.md) builds that page from the coordinator's store and T3 Code's state database. The page shows what is running now, what is queued, a timeline of agents and sub-agents grouped by unit, and the units in flight with their state and pull request link. It covers the last 3 hours by default. When the document exceeds its size limit, the page can fold finished rows into summaries or omit rows, and it counts the agents and work items those changes cover. A pull request link is shown only as an `https://host/owner/repository/pull/number` address. A note counts the work items the page shows without their link, for that reason or for size. Every string on the page passes one filter that removes each word it reads as a T3 Code thread or sub-agent id, an absolute or home-relative file path, a `file:` address, or an email address. The [reference](cli/activity.md) states what it reads as each.

#### An executive admin

See [executive admin](guide/executive-admin.md#an-executive-admin) for the user view.
