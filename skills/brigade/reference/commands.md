# Commands

Read this when a `$B` command refuses, prints a line the block in [SKILL.md](../SKILL.md#the-script) does not name, or needs a flag that block does not show. The core block keeps `$B ticket add`, `$B fire`, `B=`, and `L=`. This file holds the rest.


```bash
$B next [--failure fixable|stuck] [--unsent <kind>] [report-back <dish> | review <dish> --verdict pass|send-back|blocked|pass-with-edits | checks <dish> --result pass|fail | merge <dish> | liveness | intake | morning | evening | user [--question] | admin <request>]   # steps this wake owes and reply
$B status                                    # thread <id> or thread not recorded, the counts, reports to <thread> when set, and owner <thread>@<generation> when a generation is recorded
$B set --thread <id> [--replace] --schedule <name>=<id>
$B set --reporting every-turn|milestones|digest
$B set --liveness-ms <N>                     # the interval a new liveness schedule uses, a whole number of at least 60000; "" removes it
$B set --schedule <name>=                    # drops that name from restaurant.json
$B set --intake github,<feed>                # the intake sources this restaurant owns
$B set --workers <n>                         # dishes in progress or in review at once
$B set --autofire off|urgent|normal|low      # the lowest ticket priority startable lists as startable
$B set --reports-to <thread>                 # the executive admin this restaurant reports through
$B set --mode light|full                     # this restaurant's mode from the next brief
$B set --pr-credit on|off                    # whether pull requests credit the models that did the work
$B set --build-command "<command>" --check-command "<command>"   # the two commands edits verify runs
$B set --cap KIND=N[,KIND=N]                 # the cap on items of that kind holding a worker slot
$B set --review KIND=full|light[,KIND=MODE]   # the default mode for a new item of that kind
$B set --cooldown-minutes <N>                 # the minutes a cooled seat stays out of a launch; "" clears it to 60
$B ticket list [--state waiting|assigned|moved|done|dropped]
$B ticket set T3 [--state dropped] [--priority urgent|normal|low] [--paths src/a,src/b] [--decision | --no-decision]   # at least one flag
$B ticket set T3 --kind fix [--goal G1 | --no-goal]   # changes a waiting ticket
$B ticket move T3 --to <sibling>             # hands a waiting ticket to a sibling. Prints the thread to tell
$B ticket take                               # files every ticket a sibling handed to this restaurant
$B ticket paths [--dry-run]                  # on each waiting ticket that records no paths, records the tracked paths its summary quotes in backticks
$B ticket notes --paths a.py,b.py | --all   # prints the notes that name those paths, or every note
$B ticket sweep --older-than <n> [--drop --confirm <digest> | --goal G<n> --confirm <digest>]   # lists waiting tickets filed more than <n> days ago that have no kind or no goal, with a count and a digest; with --drop drops them, and with --goal gives each ticket with no goal that goal
$B startable                                 # the workers line, then a count line and each waiting ticket in start order with why it can or cannot start now, or no waiting tickets. It starts nothing
$B inbox take                                # ticket take, then prints each request from the executive admin as A<n>: <line>
$B inbox done A<n>                           # records the request as acted on and deletes its file
$B goal add --name "<name>" --purpose "<why>" --done "<test>" [--share <n>]   # prints G<n>
$B goal set G1 --state open|paused|done|dropped [--evidence "<one line>"]   # a move to done or dropped needs --evidence
$B goal set G<n> --state paused   # no ticket of that goal starts, not even as a ride-along
$B goal set G<n> --share N   # a ceiling of N worker slots holding that goal's items
$B goal list [--all]   # each open or paused goal, and each done or dropped goal that still has a waiting ticket, an item in flight, or a ticket riding along, with its state, name, counts, purpose, and done test, then the counts of unassigned work
$B brief D2 --fields <path>                 # JSON file, or --fields - for stdin. Writes briefs/D2.md, then briefs/D2-<k>.md after a worker row, and prints the path and the launch message. Send the last printed line as t3_thread_launch message. Paste the brief file contents only when the worker cannot read the store path
$B brief D2 --goal '...' --acceptance '...' [--acceptance ...] --contract '...' --verify '...' --base main [--context ...]   # same brief. Single-quote every field
$B contract D2                               # prints the contract the last brief of D2 stored
$B watch                                     # a line for every open dish; renews each live lease. A dish that owes its round decision gets <n> send-backs, decision pending. It prints one trunk line while it sees a red or unseen remote mode trunk. It stays silent with no contract or in local mode.
$B hang D1 --provider <provider> --minutes <n> [--attempt <n>]   # one open run after report-back, once per attempt
$B model skip <provider/model> --cause rate-limit|failed   # records that seat as skipped
$B model clear <provider/model>   # records a passing smoke test on that seat and clears its skip
$B thread <dish> --role "panel member" --thread <id> --seat <provider/model>   # records one thread an item used, for a role no other command writes
$B dish D2 --state dropped [--stopped <run id>|idle]   # releases the dish's lease and returns its tickets to waiting
$B dish D2 --state in-progress|in-review|queued|merged [--thread <id>] [--task <id>] [--pr <url>] [--sha <head>] [--timebox <m>] [--reported] [--lease <id>] [--paths <paths>] [--branch <name>]   # --thread writes the open worker row of threads.tsv for the current attempt
$B dish D2 --add-paths <paths>   # takes no other dish flag
$B dish D2 --mode full --reason "<one line>"   # records the item's move to full once
$B dish D1 --worker-ended "<reason>"   # records that this attempt's worker ended or failed and wrote no report
$B dish D1 --usage-limit <provider/model> --role <roles.py role> --error <file>|- --catalog <file> --parent <provider/model> [--author <provider/model> ...] [--options <json>]   # records a cooldown row for that seat and one cooldown event, prints one next step, and takes no other dish flag
$B pass record D2 --sha <head> --verdict pass|send-back|blocked|pass-with-edits [--reason <reason>] --author <provider/model> --verifier <provider/model> [--pr <url>] [--note "..."] [--same-family] [--report <file>] [--edits <file>] [--reviewer-thread <id>] [--member | --late]   # --reviewer-thread writes the reviewer row of threads.tsv for this attempt. A send-back needs --reason, one of false-wording, missing-test, wrong-behavior, out-of-lease, scope, other, and no other verdict takes it. Once the dish owes its round decision, each send-back it counts ends its line with <n> send-backs, decision pending
$B pass check D2 --sha <head> [--json]       # exits 1 unless that SHA passed. --json prints verdict, author, verifier, note, and crossFamily
$B pass stats [--last N]   # first-pass rate overall and per author model over the last N items; default 20
$B credit D2 [--coordinator <provider/model>]   # credit line for the PR body when prCredit is on; otherwise prints the off line
$B edits verify D2 --from <reviewed sha> --to <head>   # exits 1 when a compared path differs or the build or check command fails
$B edits apply D2 --report <D2-review-1.md> --from <reviewed sha> --branch <new branch> [--worktree <path>]   # applies the report's edits to a new branch from the reviewed commit in a new git worktree, runs the contract's setup and the build command there, commits once, and prints the head and the worktree path. Exits 1 and leaves no branch or worktree when an old text does not occur exactly once, a path is generated or not a file at the reviewed commit, the contract's generated paths cannot be read, a `--worktree` path exists or lies inside the checkout, a registered worktree, or the git directory, or the setup, build, or commit fails (a commit hook that rejects, or that adds a path no edit or build names, fails it). A cleanup that cannot finish says what remains. A refusal prints one line, and a failed setup or build prints the command's output first. The command's own writes go to the new worktree and to git's records of that worktree and branch. The setup and build run the edited code with your access, as `edits verify` and a fix worker's test runs do, so read the edits before you apply them. It never pushes and records no verdict
$B 86 add --question "..." --options "a, b" --default "a" [--dish D2]   # prints Q<n>
$B 86 add --dish D2 --round-budget           # the decision D2 owes at its stored roundBudget send-backs. prints Q<n>
$B 86 answer Q1 --answer "..." ; $B 86 list
$B close [--dry-run | --to-file]             # report of what changed since the last one. --to-file prints only the written file's path
python3 <skills>/brigade/scripts/brigade.py walk [--repo <project root>]
python3 <skills>/brigade/scripts/brigade.py affected --paths <file> [--paths <file>...] | --diff <base>   # verify paragraph for those paths; reads the store's restaurant.json for the affectedTests layout and prints the store's check command when no layout is set
```


Run affected to print the verify paragraph for a path change. It scans the tree and names the modules or the full suite. Use the picked modules for worker verification. Leave the full suite to CI and the merge queue unless affected reports an unmapped path.


`affectedTests` sets the picker layout `affected` reads. Set it with `set --affected-tests`. Keys and an example are in [store-config.md](../store-config.md).


`sharedDocs` plus `docTimebox` set the shared-document catalog and timebox `fire` reads. Set them with `set --shared-docs` and `set --doc-timebox`. Rules and an example are in [store-config.md](../store-config.md). Without `sharedDocs`, `fire` refuses nothing for shared documents.


`changeNoteDir` sets the fragment directory `fire` leases. Set it with `set --change-note-dir`. Rules and an example are in [store-config.md](../store-config.md).

`--usage-limit` reads the reset time out of the error text. It takes three shapes and nothing else: an ISO 8601 date-time anywhere in the text, a clock time followed by `UTC`, `GMT`, or `Z`, and a relative span such as `try again in 30 minutes` or `retry after 20s`. A clock time counts only when it is still ahead of now. A timestamp with no zone is not read. A text with no such shape, a parsed time that is not after now, and a relative span too large to represent end the cooldown at now plus `cooldownMinutes`. Every provider with a live cooldown is passed to `roles.py backup` as `--out`, and a cooldown whose end has passed is not passed.


`set` refuses a malformed `changeNoteDir` with nothing written. `fire` with paths refuses a malformed `changeNoteDir` before it claims a lease. `fire` with no paths reads no `changeNoteDir` and leases no fragment.

`fire` prints one advisory line to stderr when a `--paths` entry is a directory holding more than 5 tracked files, naming the directory, its tracked-file count, and up to 5 files under it that the fired tickets' summaries quote in backticks. It leaves the lease unchanged.


Pass brief fields as a JSON file with `--fields <path>`, or from stdin with `--fields -`. Put every flag value in single quotes so the shell does not expand it. Before you write brief fields, read [Brief fields](../script.md#brief-fields).


## Intake detail

`set --intake`, and `open --intake` when it creates a restaurant, refuse a source a sibling already lists, with `brigade: <sibling> already owns intake from <source>; move tickets to it instead`. On an existing one it changes nothing. `ticket add --ref` refuses a ref that is already live here or in a sibling, with `brigade: <ref> is already T1 (waiting); nothing added`. Read [Intake and refs](../script.md#intake-and-refs) before changing intake or fixing a malformed table line.


## Filing detail

Coordinators follow this before each `ticket add`. The tracker is the one AGENTS.md names. Read its open items with the commands AGENTS.md names. Run `gh issue list` only when AGENTS.md names no other tracker.

1. **Match.** A ref the request names is its ref. Otherwise read the open items and find the one item that is the same work as the request, by its summary. Pass that item's ref as `--ref` and create nothing. When more than one item could be the same work, create nothing and add the ticket with no `--ref`. A scope that forbids tracker writes still passes an existing item's ref.
2. **File.** When no item matches, follow AGENTS.md's filing rules. Create an item only when those rules call for one and the user or the house rules allow that write. Pass the ref the tracker prints for it. When the rules or the permission do not allow the write, create nothing and add the ticket with no `--ref`.
3. **Unavailable.** When the tracker cannot be read, report it and fetch nothing in its place. A user request is still filed. Pass the ref it names as `--ref`, and omit `--ref` only when it names none. A supplier finding with no ref adds no ticket.
4. **Refusals.** A refused `ticket add` is not an error to work around. Four of its refusals are about the request. A live ticket here or in a sibling has the ref. A ticket of this restaurant already carries the `--request` id. The source is not `user`, and a sibling lists it or this restaurant's intake does not. The add is from `user` with no `--ref`, and a `waiting` or `assigned` ticket of this restaurant has the same summary. Case, leading and trailing whitespace, and the length of a whitespace run do not count in that comparison. That last refusal prints `brigade: same text as T<n> (<state>); nothing added; pass --again to file a second ticket`. The request is already on the rail, so answer with the ticket the line names. Pass `--again` only when the request is separate work in the same words. `--again` lifts no other refusal. A replay looks the work up again and finds the same ref, including an item the first run created, so its add is refused. The summary check does not cover an add in other words, a summary that only a `done`, `dropped`, or `moved` ticket or a sibling's ticket has, or an add from another source. So read `$B ticket list` before you file a no-ref request again.

