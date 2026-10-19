Part of [the design](../multi-coordinator.md).

## The executive admin

The user stays in charge of every coordinator, as brigade's skill already says. The executive admin works for the user on one repository, in two ways. As an assistant, it keeps the user up to date across every coordinator on that repository, forwards requests, files shared intake once, and writes one plain-language update. As a coordinator of coordinators, it settles conflicts between them on its own authority, by published rules, and logs every ruling so the user can review and overrule it. It escalates only what the rules cannot settle. Each coordinator keeps owning its own work, its reviews, its queue entries, and its `menu.md`.

### Who decides what

- **The scripts enforce.** The repository cap stays in `land.py` (change 2), path exclusion stays in leases, and duplicate intake is prevented by intake ownership (change 4). A thread cannot enforce a cap against two coordinators starting work at once, so every ruling is carried out through a script wherever one exists.
- **The admin rules on conflicts between coordinators.** It decides contested paths, ownership of a request that fits two purposes, each coordinator's share of the repository cap, and queue order between two coordinators' work, by the rules in [Rulings](admin-rulings.md#rulings). A coordinator must comply, and may appeal.
- **The user sets the rules' inputs and has the last word.** The user sets the repository cap, the landing mode, every purpose, and the priorities the rules read. The user answers every decision, and can overrule any ruling.
- **Each coordinator keeps its own work.** What it builds, how it reviews, and when it submits, absent a conflict, stay its own. A `from-user` request is a message from the user, and a coordinator can decline one with a reason, which the admin passes on. A ruling is not a request. When the admin's thread is gone, every coordinator keeps working and falls back to replying to the user itself. The admin is optional. Changes 1 to 7 work without it.

### What it does

| Duty | How |
| --- | --- |
| Routing requests | A request the user sends to the admin goes to the coordinator whose purpose fits, by `ticket move` and the `ticket` line. When two fit, an ownership ruling decides. When none fits, it asks the user. A coordinator that finds a routed ticket off its purpose moves it back and sends `misrouted`. |
| Shared intake | For a repository where several coordinators would read the same source, such as its GitHub issues, the admin is the intake owner from change 4. It files each issue once and routes it. It starts nothing. |
| Splitting the cap | It splits the repository cap the user set into shares by the share rules, and sets them with `land.py share`. When a repository sits at its cap while tickets wait for an hour, it tells the user and proposes a new cap with a default. It runs `land.py cap` only when the user asks. |
| Ruling on conflicts | It rules on contested paths, including hot shared files, and on queue order between coordinators, by [Rulings](admin-rulings.md#rulings). It finds conflicts in `blocked` rows older than an hour, in `contest` lines, and while routing. It never takes a lease away. |
| The landing mode | It runs `land.py mode` only when the user asks, and then tells every coordinator on that repository. |
| One update | It writes one plain-language update across all coordinators, at the user's reporting level. |
| Opening coordinators | When the user asks it to open a coordinator, it runs the opening steps, asks the user the opening questions, shows any overlap with the purposes of coordinators already on that repository, and launches the new thread once the user agrees. |

### One admin per repository

The first version serves one repository, the one in the user's request. Its store is `<store>/<project>/.admin/`, in the same project directory as the coordinators it serves, and its `restaurant.json` names their `projectRoot` and holds `role: "admin"`. `slug` never produces a leading dot, so `.admin` never collides with a coordinator's name. `walk`'s `*/*/restaurant.json` pattern does list it, because `pathlib`'s glob matches names that start with a dot, so `walk` prints it by its role.

The admin is a sibling of every coordinator on its repository, so the sibling rules carry it with no new mechanism. Intake ownership (change 4) counts its `intake` list, `ticket move` reaches it and leaves it, and handoff ids stay qualified by store path, as `app/docs/T6`. Its thread runs in the repository's T3 project, so every message stays inside one project. A coordinator with `reportsTo` keeps an hourly schedule that runs a service, even when it owns no intake, and every service begins with `inbox take`. The admin reads each coordinator's events from its `log.tsv` on every service, so it misses nothing when a coordinator's message fails.

An admin that spans several repositories or T3 projects is a later change. It needs repository-qualified intake, cross-project routing, and a test of whether `t3_thread_send` reaches a thread in another project. None of that is needed to coordinate, rule, route, and report for one repository.

### Messages

A thread launched with `t3_thread_launch` does not wake its launcher, so every exchange is an explicit `t3_thread_send`, following the convention Autopilot already uses. A coordinator sends with `mode: "queue"`, so it never interrupts a turn in progress. The admin sends with `mode: "auto"`, so the coordinator wakes. Each message is one line that starts with a fixed word and names the coordinator.

Messages are wakes, and the stores are the record. No message carries state that is not already durable in a store, so there is no separate record of sends. A send that fails is retried or left to the next scheduled wake. A wake that arrives twice is harmless, because each side consumes by a durable identity.

- **A coordinator event** is a row in that coordinator's `log.tsv`, identified by its store path and the byte offset where the row starts. The admin consumes events only through `sync`, which reads rows past a cursor and records each one once (change 9). A second `merged` line for one landing wakes the admin, and `sync` finds nothing new.
- **An admin request** is a file in the coordinator's `inbox/`, named by its request id `A<n>`. The admin passes that id as the `clientRequestId` of the `t3_thread_send` that wakes the coordinator, so a retried send delivers once. The coordinator acts on the file and runs `inbox done <id>`. A replayed file repeats nothing, because every action is keyed by its id. A routed ticket's wake uses its handoff id the same way.

The admin's service reads every coordinator's `log.tsv` from a cursor, the byte offset where its last service stopped reading that coordinator. It keeps one offset per coordinator in its own `restaurant.json`. A cursor always sits at a record boundary, just past a newline that ends a complete row. Every append discards an unfinished tail before it writes one complete row (change 4), so every complete row past the cursor is new, whatever its timestamp. `sync` copies the bytes past the cursor under a shared lock on the coordinator's `restaurant.lock`, so no append or tail repair runs during the copy (change 9). An unfinished last line is never read, and the writer's next append discards it. A lost message delays a reply and never drops an event.

Coordinator to admin:

| Line | Sent when |
| --- | --- |
| `merged <coordinator>: <title> [<pr url>]` | work lands. `push` and `local` modes have no PR URL |
| `sent-back <coordinator> D<n>: <one line>` | a review sends work back or blocks it |
| `decision <coordinator> Q<n>: <question> Options: <options>. Default: <default>.` | it parks a decision for the user |
| `failed <coordinator>: <one line>` | a failure or a paused queue it cannot fix itself |
| `drained <coordinator>: <closeout path>` | its batch drains |
| `report <coordinator>: <closeout path>` | its evening report is written |
| `blocked <coordinator> T<n>: waiting on L<n> held by <holder> since <time>` | a ticket has been blocked for more than an hour |
| `misrouted <coordinator> T<n>: <why>` | a routed ticket is off its purpose, after it moved the ticket back |
| `reply <coordinator>: <one line>` | its answer to a request from the user, including a decline and its reason |
| `contest <coordinator> C<n> D<n>: <one line>` | its item and another coordinator's would conflict in the queue, after it opened contest `C<n>`, naming which depends on which |
| `appeal <coordinator> R<n>: <why>` | it disagrees with a ruling, after it complied, or instead of complying when compliance would be irreversible |

Admin to coordinator:

| Line | The coordinator |
| --- | --- |
| `ticket <coordinator>: run ticket take` | takes the routed ticket |
| `from-user <coordinator>: <the user's words>` | treats it as a message from the user, and answers with `reply` |
| `answer <coordinator> Q<n>: <answer>` | records the user's answer with `86 answer`, then acts on it |
| `reports-to <coordinator> <thread>` | runs `set --reports-to <thread>` |
| `ruling <coordinator> R<n>: <decision>` | checks that the ruling is still `in-force` in the admin's `rulings.tsv`, then carries out its side, which for most rulings means waiting while a script enforces it |

Contested paths, ownership, shares, and queue order are recorded as rulings, and a `ruling` line tells each coordinator involved. A mode change and any other instruction from the user travel as `from-user`, with the user's words quoted.

Every admin line is durable. Before it sends, the admin runs `request --to <coordinator> "<line>"`, which writes the line as a request file in the coordinator's `inbox/` and prints its id (change 9). A routed ticket's line is the exception, because the handoff file is already its record.

A coordinator's `inbox take` files routed tickets, as `ticket take` does, and prints each request file's id and line. The coordinator acts on it as the table says, then runs `inbox done <id>`. A crash between acting and `inbox done` replays the request, so every action is keyed by the request id. A `from-user` request becomes work only through `ticket add --request <id>`, which refuses a second ticket for the same request id. Work starts only from tickets, which `fire` refuses once they are no longer waiting. Setting a value, recording an answer, and carrying out a ruling give the same result when repeated. So a replayed request repeats nothing.

Every coordinator event is durable too, because it is a row in the coordinator's own `log.tsv`. The admin's service reads those rows past its cursor and acts on them. A decision is an open row in `86.tsv`. A blocked ticket is a `blocked` row from change 5 with no later start. A merge and a send-back are state rows. A drain is a status with no work in progress. The message lines in the table above only wake the admin sooner. When a coordinator's send fails, it also falls back to replying to the user at its own reporting level, so the user still hears.

### What it never does

- Decide what belongs to the user. It sets no repository cap, landing mode, priority, or purpose, and never answers a coordinator's decision. It rules only on the four kinds of conflict, by the rules, and logs every ruling.
- Direct a coordinator's own work. Rulings settle conflicts between coordinators. What a coordinator builds, how it reviews, and when it submits, absent a conflict, stay its own.
- Write code, or edit any file in a repository.
- Land work. It never runs `land.py submit` or `land.py land`, and never merges or deletes a branch. Each coordinator owns its queue entries, PRs, and watches (change 6).
- Override a review. It never records or changes a verdict, never asks a coordinator to submit work that did not pass, and never messages a worker. When the user disagrees with a verdict, the user's words go to that coordinator as `from-user`.
- Write into a coordinator's store, except new files in its `inbox/`, which are routed tickets and request files. `Restaurant.log` in `brigade.py` rewrites `restaurant.json` on every event, so a second writer there would lose updates.
- Release, renew, or claim a lease, or start work. It only reserves contested paths for the winner of a ruling. The admin store refuses `fire`, `brief`, `dish`, `pass`, and `watch`.

### How the user opens it

The user asks any thread for an executive admin, through the brigade-admin skill.

1. Ask the user for the reporting level with the host's question tool, unless the user already said it. Recommend `digest`, because the admin exists so the user hears less.
2. `python3 <skills>/brigade/scripts/brigade.py open --admin --project-root <root> --reporting <level>`. It creates `<store>/<project>/.admin/` with `mkdir` and no `exist_ok`, so there is one per repository. A second open prints `exists`, and only the caller that got `opened` launches a thread. It prints every coordinator on the repository, with its purpose, as `open` prints siblings.
3. Write the user's priorities under `## Priorities` in its `menu.md`, with the user, highest first. Add which intake sources it should own. The rules read the priorities. Without them, the rules fall back to purposes and age.
4. Move shared intake. Each coordinator that lists the shared source runs `set --intake` without it. Change 4 refuses that while it still has waiting or assigned tickets from the source, so it finishes them first or moves them to the admin with `ticket move`. Then the admin store runs `set --intake <source>`.
5. Launch the thread with `t3_thread_launch` in the repository's T3 project, with `workspaceStrategy: {"type": "root"}`, title `Executive admin`, and the message "Use the brigade-admin skill. You are the executive admin for the store at `<store>/<project>/.admin`. Wait for the start message." Record it with `set --thread`. Then send it "Run your first service." with `t3_thread_send` and mode `"auto"`. Recording the thread before its first service lets that service pass the fence below.
6. The admin's first service sends each coordinator the `reports-to` line.

### Its services

First service pins the thread with `t3_thread_organize`, and creates three schedules bound to it, recorded with `set --schedule`. The opener asks for the intake interval in whole minutes and the morning and evening times as `HH:MM`. It refuses an interval above 120 minutes and asks again. Each service also runs the idle check, which must run before a coordinator's 6-hour leases lapse. A start message that carries no value for one of the three takes the default, `3600000` ms for intake, `09:00` for morning, and `18:30` for evening. The morning service runs routing and checks. The evening update runs after the coordinators' 18:00 reports. It creates no liveness or drain schedule, because it runs no workers and owns no queue entries.

Every wake, whether a schedule, a message, or a user message, runs one service.

1. Run `status`, which prints `owner <thread>@<generation>`. When it names another thread, end the turn with no action. Otherwise every command in this service runs as `$B --owner <thread>@<generation>`, and every `land.py` write passes `--owner .admin/@<generation>`. This step ends a stale wake early. The owner check inside each write is what stops a replaced or retired thread from writing (change 9).
2. Read its `menu.md`, `status`, `86 list`, and `walk --repo <root> --stale-hours 3`.
3. `inbox take`, for tickets moved back. Then intake from its owned sources with `ticket add --source <source>`. Then `request --republish`, which publishes again any request file a crash left unwritten.
4. Route each waiting ticket with `ticket move` and the `ticket` line. A ticket that fits two purposes gets an ownership ruling. A ticket no purpose fits becomes a question for the user with `86 add`.
5. Run `sync`, which reads each coordinator's `log.tsv` rows past its cursor, records them in the admin's own `log.tsv`, and advances the cursor. Rule on each conflict it finds, such as a block older than an hour, a `contest` or `appeal` line, or a changed share, per [Rulings](admin-rulings.md#rulings). Record each ruling with `rule add` before carrying it out. Then reconcile every `in-force` ruling with what the scripts report, mark the ones whose condition ended `done` or `expired`, and carry out the rest again, which repeats nothing. Pass coordinators' decisions, and whatever the rules cannot settle, to the user.
6. A coordinator that `walk` marks idle while it holds leases becomes a question for the user, before its 6-hour leases lapse.
7. Reply per the user's reporting level.
