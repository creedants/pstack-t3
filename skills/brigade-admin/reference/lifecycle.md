# Lifecycle

Read this when opening, recovering, retiring, or moving intake. The core keeps step 1 and short stubs. This file holds store paths, shared intake, recovery steps 2 to 6, and retirement.


## Store paths

`<store>` is the store root. Without `--store`, it is `$BRIGADE_STORE` when it is set and not empty, else `$XDG_STATE_HOME/pstack-t3/brigade`, and `$XDG_STATE_HOME` defaults to `~/.local/state` when it is unset or empty. `<root>` is the repository's project root. `<project>` is the last component of `<root>` after symlinks resolve, lowercased, with each run of characters outside `a-z0-9` replaced by `-` and any leading or trailing `-` removed.


## Shared intake

4. Move shared intake. Each coordinator that lists the shared source runs `set --intake <its other sources>`, or `set --intake ""` when that source was its only one. [The script](../../brigade/SKILL.md#the-script) says when that is refused. To clear a refusal, the coordinator finishes that source's tickets or moves them to the admin with `ticket move <id> --to .admin`. Then run `python3 <skills>/brigade/scripts/brigade.py --at <store>/<project>/.admin set --intake <source>` with no `--owner`. The admin has no thread until step 5, so no owner token exists yet. [The script](../../brigade/SKILL.md#the-script) says which writes need one after step 5.


## Recovery steps 2 to 6

2. Read each recorded schedule's cadence from `list_scheduled_tasks`, matching the ids recorded under `intake`, `morning`, and `evening`, before anything is deleted. Delete each schedule with `delete_scheduled_task`, and clear each name with `$B set --schedule <name>=`. Build the start message in step 6 from one value per name, the cadence read for that name, or the default when no cadence was read, `3600000` ms for intake, `09:00` for morning, and `18:30` for evening. The new thread's first service creates each schedule from those values.
3. When the old thread still exists, call `t3_thread_interrupt` on it. `status: "interrupt_requested"` means its run has not stopped yet.
4. Call `t3_thread_wait` on the run id it returned, or on the thread when it returned none.
5. When the wait returns `timedOut: true`, launch nothing. The store stays `recovering:<this thread>`. Tell the user. Recovery answers the user's request, and this thread is not the admin, so the admin's level does not apply. Wait again on the next turn on the thread in `previousThread`.
6. When the wait reports a terminal state, archive the old thread. Launch the new thread with the message from opening step 5. Run `$B set --thread <new> --replace --expect recovering:<this thread> --stopped <run id>`, `--stopped idle` for an idle thread, or `--stopped gone` when the old thread no longer exists. Without `--stopped` it exits 1 with `brigade: the old run has not been confirmed stopped; wait for it with t3_thread_wait, then pass --stopped <run id>`. Then send the start message with those three values. The new thread's first service sends every coordinator the `reports-to` line.


## Retirement

To retire the admin, route or drop its waiting tickets, then run `$B set --intake ""`. Delete its schedules and clear each name. Send each coordinator the `reports-to` line with `none` as `<thread>`, and send a last update. Unpin its thread. Last, run `$B set --thread "" --replace --expect <this thread>`. A later wake then finds `thread not recorded` at service step 1 and ends. A write the old thread still runs gets the stale-owner refusal in [The script](../../brigade/SKILL.md#the-script). A write with `--owner @<new generation>`, the token `status` prints as `owner @<new generation>`, exits with `brigade: this store has no recorded thread; it was retired, and only set --thread --expect restarts it`. Each coordinator takes a shared source back with `set --intake <source>` where the user wants it. Restarting a retired admin is recovery with `--expect ""` and `--stopped gone`.
