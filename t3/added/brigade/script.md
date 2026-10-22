## Brief fields

The object has `goal`, `acceptance`, `contract`, `verify`, and `base`. `contract` is one string that holds the item's guarantee, trust model, and known limits, or `none` when the item has no safety, privacy, or integrity property. `acceptance` is a list of strings. Optional `context` is a list of strings. Optional `paths` and `lease` fall back to the row from fire when omitted. The flag form prints the same brief. A double-quoted value lets the shell run backticks and expand `$` before `brigade.py` starts, so the brief can contain command output. An unquoted heredoc expands backticks and `$` the same way. Write the file with a file tool, or with a heredoc whose delimiter is single-quoted, such as `<<'EOF'`.

## Intake and refs

`open --intake` records the list when it creates a restaurant. `ticket add --source <s>` for any source but `user` needs `<s>` in this restaurant's intake and in no sibling's. Its refusal names the owner. A `waiting`, `assigned`, or `moved` ticket is live until the ticket it was handed to is `done` or `dropped`. A ref names the work's item in the project's tracker. Use the item's URL when the tracker gives items URLs, such as the URL `gh` prints for a GitHub issue. Otherwise use the id with the project prefix the tracker prints, such as `app-7f3`. Never pass a bare number such as `123`, because another tracker can use it too. Refs compare as exact strings, so write each item in that one form. A table line that does not parse stops every command that takes `--at`, except `sync`, with `brigade: <project>/<restaurant>/<table> line <n> is malformed; fix or remove it`, where `<project>/<restaurant>` is the last two parts of the restaurant directory that holds the table. Fix or remove that line by hand. `--decision` on `ticket add` or `ticket set` marks a ticket that asks the user to decide something, and `ticket set T<n> --no-decision` removes the mark. Pass `--paths` on `ticket add --summary` whenever the files are known. `ticket add --from-report` records the tracked paths it finds in a follow-up's backtick quotes. `ticket paths` does the same on each waiting ticket that records none.

## Worker cap

`open --workers` records the cap when it creates the restaurant. On an existing restaurant it leaves the cap unchanged. Change it with `set --workers`. `watch` rechecks the worker count and then the lease, in `fire`'s order, so both name the same reason when both caps hold. `ticket list` and `walk` run the same recheck, so `ticket list` appends `blocked: workers`, `blocked: lease`, or `blocked: repository` and `walk` counts the ticket blocked only while that block still holds. A refused `fire` logs the paths and `--branch` it was given. When the store sets `changeNoteDir`, `watch` adds that directory's `<branch>.md` fragment for the branch the next `fire` would use when it rechecks the lease, and the command includes `--branch` when one was recorded; an unset key adds no fragment. When the key is set, `fire --paths` also leases that fragment for the branch it names, or for the default branch name when `--branch` is omitted, with `%` written as `%25` and `/` written as `%2F`. A lease check that fails prints the landing queue's diagnostic. `dish --branch` records the branch and claims or releases nothing. When the dish's lease does not cover the new fragment and is not submitted, `dish --branch` prints that the lease does not cover the fragment when active and that it is not active so nothing covers it otherwise, then prints `$B dish <id> --add-paths <fragment>` to widen it. Run that command. When the lease is submitted, it prints that the submitted commit lands as it is, and to run the same `dish --branch` again if the entry bounces.

## Owner

`restaurant.json` holds the recorded `thread` and a `generation`. `set --thread` sets the generation to 1 when it records a first thread, and `--replace` adds 1. `brigade.py` passes `--owner <restaurant>/@<generation>` from its own token on every `land.py` write it makes. A store opened before generations has no `generation` and needs no `--owner`. Run `$B set --thread <its recorded thread>` once to record generation 1.

## Dropping a dish

`dish --state dropped` releases the dish's active lease, including a queued dish whose entry bounced. `--stopped` records the run id in `log.tsv` as evidence. The script cannot check it. The dish's `assigned` tickets go back to `waiting`, each with a log row naming the dropped dish, and the command prints `D3 dropped; T2 waiting again`. Fire them again, or drop them with `ticket set`.

## Pass and round budget

Workers write their reports to `<restaurant dir>/reports/<dish>.md`, and verifiers write round `<k>`'s findings to `<restaurant dir>/reports/<dish>-review-<k>.md`, where `<k>` counts that dish's reviews from 1.

`--report <file>` stores the name of the round's findings file. It refuses a path that is none of the bare file name, `reports/<name>`, or a path whose directory resolves to `<restaurant dir>/reports/`. It also refuses a file that is missing from `<restaurant dir>/reports/`, is not a review report of that dish, is a symbolic link, is not a regular file, or resolves outside `<restaurant dir>/reports/`. `--member` records a panel member's row that does not carry the dish's verdict. It leaves the dish as it is, `pass check` ignores it, and it never counts as a send-back. `--late` records a round found unrecorded after the dish moved on. It leaves the dish as it is, and its `send-back` counts. `close` prints `brigade: warning: reports/<file> has no review row` on stderr and still exits 0, for each review report that no row names and that is newer than every row of its dish without a report. Record that round with `$B pass record <id> --report <file>`, and add `--late` when the dish has moved past it. `close` also prints `brigade: warning: reports/<file> <what it is> and no review row of <id> names it; <what to do>` on stderr, and still exits 0, for an entry named like a review report that no row of its dish names and that is a symbolic link, is not a regular file, or resolves outside `<restaurant dir>/reports/`. When `reports/` is itself a symbolic link, that line for every such entry says to replace that link with a directory and leave the link's target as it is. Otherwise, for a symbolic link it says to remove the link and leave its target as it is, and for another entry that is not a regular file it says to move it out of `reports/`. `$B dish <id> --reported` records that this attempt's report-back arrived. A new attempt clears that mark. The attempt is new when the dish enters in progress, and when `$B dish <id> --thread <id>` records a different worker while the dish stays in progress. That command logs a new start. `$B watch` compares the report file to that start. Recording the same thread again leaves the attempt alone. When that report-back is recorded and the report file is more than 10 minutes old while the dish is still in progress, `$B watch` prints `D<n>: reported Nm ago, not in review; read the thread (thread <id>)`. The number is minutes since the report file was written, and the parentheses name the worker thread, or `task <id>` for a delegated task.

## Mode

A change applies from the next brief, and a running worker keeps the mode its brief names. `brief` runs `roles.py mode` in the project root with that mode, the dish's leased paths, the station as the playbook, the attempt kind, and the count of `send-back` rows in `pass.tsv`, `--member` rows excluded. It writes the printed lines unchanged, then `Gate: brigade` and the runtime's seat rule. The attempt kind is `fix` after a send-back, `bounce` after a queue bounce, and `first` otherwise. A replaced worker keeps the kind. A station that is not a poteto-mode playbook, such as `correct`, gets only the `Mode:` and `Mode source:` lines. `roles.py` runs outside the store lock. The first time `roles.py` prints `Mode source: escalated: <reason>`, `brief` appends one `mode` row with state `full` to `log.tsv`. Every later brief passes that reason back, so no setting returns the item to light. `fire --mode full --reason` and `dish <id> --mode full --reason` append the same row. A second call appends nothing and prints the first reason.

## Command output

Each command's text below is the text that moved out of the command block in [the script](SKILL.md#the-script), word for word.

### next

**`$B next`.** `wake: <label>`, then `reporting: <level>`, then `reports to: <thread>` when one is set, then each cue as `run <argv>`, `see <words>`, or `if <cond>: <action>`, or the line `nothing else owed` when there is no cue, then `reply: yes|no (<occasion>: <detail>)`. `<label>` is the wake word, plus the dish or the request when the wake takes one. A wake left off prints `wake: unspecified`.

### status

**`$B status`.** thread <id> or thread not recorded, then reporting level, landing mode, and counts, then the workers line, then a kinds line, a goals line, and an untagged line when each has something to count, then reports to <thread>, mode <full|light>, and owner <thread>@<generation> when set.

### set

**`$B set --schedule <name>=`.** drops that name from restaurant.json. A name that is not recorded is already absent, and the command still succeeds. Other names stay.

**`$B set --intake`.** the intake sources this restaurant owns. Replaces the list. `--intake ""` clears it.

**`$B set --workers`.** dishes in progress or in review at once. A missing value reads as 2.

**`$B set --autofire`.** the lowest ticket priority startable lists as startable. A missing value reads as normal.

**`$B set --reports-to`.** the executive admin this restaurant reports through. `--reports-to ""` clears it.

**`$B set --mode`.** this restaurant's mode from the next brief. `--mode ""` returns it to the roles files.

**`$B set --build-command --check-command`.** the two commands edits verify runs. none for one the project lacks.

**`$B set --cap`.** at most that many items of the kind holding a worker slot. A pair with no number clears that kind's cap. An empty value clears every cap. Read back through the JSON that set prints.

**`$B set --review`.** the default mode for a new item of that kind. A pair with no mode clears that kind's depth. An empty value clears every depth. Read back through the JSON that set prints.

**`$B set --change-note-dir`.** the repository-relative directory holding one change-note fragment per branch. `""` clears it, and an unset key leases no fragment. Read back through the JSON that set prints.

### ticket

**`$B ticket add`.** prints T<n>. `--request` refuses a second ticket for that request. `--again` lifts the same-summary refusal in Filing tracker work step 4. `--kind` is required with `--summary`. `--goal` takes an open or paused goal, and without it the ticket is unassigned.

**`$B ticket add --from-report`.** only a follow-up marked `Safety:`, `Privacy:`, or `Correctness:` becomes a ticket, case does not count, and leading whitespace and `*`, `_`, or backtick marks before the word do not count. At most one ticket per item, and only when no ticket from that report exists yet. Any other marked follow-up whose text is not on the rail prints `not filed, <name> takes one ticket per item` and goes to `notes.tsv`. A follow-up whose text is already on the rail in any state is skipped. Every other follow-up goes to `notes.tsv` keyed by the tracked paths it quotes. A run that files a ticket changes the ticket table, the log, and `lastActivityAt`, and a run that notes a follow-up changes `notes.tsv`. `--dry-run` prints `would add` for a ticket it would file, `would note` for a note it would add, `already noted` for a note already in `notes.tsv`, and `not filed` for a marked follow-up it would not file, and changes no store file. It takes no `--kind` and no `--goal`. Each ticket takes the goal of the report's item, whatever that goal's state. When that item has a kind, the ticket is polish, because a follow-up is filed with no priority and a report ticket with no priority is low. When the item has no kind, the ticket has no kind.

**`$B ticket notes --paths a.py,b.py` and `--all`.** prints the notes that name those paths, or every note.

**`$B ticket set T<n>`.** at least one flag. `--paths ""` clears the recorded paths.

**`$B ticket set T<n> --kind`.** changes a waiting ticket. Refused on a ticket in another state, and on a waiting ticket that an item in flight names. `--goal` takes an open or paused goal.

**`$B ticket move`.** hands a waiting ticket to a sibling. Prints the thread to tell.

**`$B ticket take`.** files every ticket a sibling handed to this restaurant.

**`$B ticket paths`.** on each waiting ticket that records no paths, records the tracked paths it finds in the summary's backtick quotes.

### goal

**`$B goal add`.** prints G<n>. `--share` is a whole number of 1 or more.

**`$B goal set --state`.** a move to done or dropped needs `--evidence` and is refused while a ticket of the goal is waiting, an item of the goal is in progress, in review, passed, sent back, blocked, or waiting to land, or an assigned ticket of the goal is not in an item of the goal. A done or dropped goal moves only to open. `--evidence` on open or paused is refused.

**`$B goal set --state paused`.** no ticket of that goal starts, not even as a ride-along. Items in flight finish and land. `$B goal set G<n> --state open` resumes.

**`$B goal set --share`.** a ceiling of N worker slots holding that goal's items. Clear with `--share ""`.

**`$B goal list`.** each open or paused goal, and each done or dropped goal that still has a waiting ticket, an item in flight, or a ticket riding along, with its state, name, counts, purpose, and done test, then the counts of unassigned work. A ticket riding along is an assigned ticket of that goal that is not in an item of that goal. `--all` also prints each other done or dropped goal.

### startable

**`$B startable`.** the workers line, then a count line and each waiting ticket in start order with why it can or cannot start now, or no waiting tickets. It starts nothing. `startable` waits with `its goal is paused`, `the <kind> cap holds (<held> of <cap>)`, and `the <goal> share holds (<held> of <share>)`.

### inbox

**`$B inbox take`.** ticket take, then prints each request from the executive admin as `A<n>: <line>`.

**`$B inbox done`.** records the request as acted on and deletes its file.

### fire

**`$B fire`.** claims the lease, prints D<n>. The item takes the first ticket's goal, and its kind when it has one. A `--kind` that differs from that kind is refused. Pass `--doc-item` when the item is about a shared document. It lets the fire through and records a doc item row in log.tsv.

### brief

**`$B brief --fields`.** JSON file, or `--fields -` for stdin. Writes briefs/D2.md.

**`$B brief --goal ...`.** same brief. Single-quote every field.

### contract

**`$B contract`.** prints the contract the last brief of D2 stored.

### watch

**`$B watch`.** a line for every open dish; renews each live lease.

### hang

**`$B hang`.** one open run after report-back, once per attempt.

### model

**`$B model skip`.** records that seat as skipped. It needs `--cause rate-limit` or `failed`.

**`$B model clear`.** records a passing smoke test on that seat and clears its skip.

### thread

**`$B thread`.** records one thread an item used, for a role no other command writes. The other six roles are written by `dish --thread`, `--reviewer-thread`, or a report.

### dish

**`$B dish --state dropped`.** releases the dish's lease, returns its tickets to waiting.

**`$B dish --state in-progress`.** `--thread` writes the open worker row of threads.tsv for the current attempt with an empty seat. It closes the previous open worker row. A later attempt is stored as fix worker. The same thread again writes no new row. A call that also has `--reported`, or a state change to a state other than in-progress, closes the item's open worker row. A call that names a new thread leaves that new thread's row open.

**`$B dish --mode full`.** records the item's move to full once. `--mode light` exits 1.

**`$B dish --worker-ended`.** records that this attempt's worker ended or failed and wrote no report. It takes no other dish flag. It writes one ended log row and changes no dish field.

### pass

**`$B pass record`.** `--reviewer-thread` writes the reviewer row of threads.tsv for this attempt. It takes its seat from `--verifier`.

**`$B pass check`.** exits 1 unless that SHA passed. `--json` prints verdict, author, verifier, note, and crossFamily.

### edits

**`$B edits verify`.** compares `<head>` with the reviewed commit that has that round's edits applied and the build run. Exits 1 when a compared path differs or the build or check command fails.

### 86

**`$B 86 add`.** prints Q<n>.

**`$B 86 add --round-budget`.** the decision D2 owes at its round budget, set by `roundBudget` in `restaurant.json` and defaulting to three send-backs. prints Q<n>.

### close

**`$B close`.** report of what changed since the last one. `--to-file` prints only the written file's path.

### land

**`$L`.** leases and the landing queue. Lease, submit, share, and contest writes pass `--owner <restaurant>/@<generation>`. cap and mode take none.
