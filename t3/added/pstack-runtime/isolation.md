# Isolation and command capacity

Read this file before isolating a writing child or running a heavy command.

## Isolation

Two writers never share a checkout (principle-separate-before-serializing-shared-state).

Before you end a worktree turn that can settle, read [Settle on turn end](host.md#settle-on-turn-end).

Before you delegate a child that needs a chosen branch or a different base, read [Older hosts](host.md#older-hosts).

- Read-only children share the current checkout.
- On T3 Code 0.0.46-nightly.20261014.3093 or later, a writing child that may overlap with another writer uses `delegate_task` with `isolation: "worktree"`. T3 branches from the parent's current local commit and runs project setup. Blocking setup finishes before the child starts. An async setup action can keep running. Uncommitted edits are not copied. The brief tells the child to report its branch and head SHA. Read the child's binding with `t3_thread_read` and check its diff before integrating. On older hosts, see [Older hosts](host.md#older-hosts).
- Long-lived owners that should appear in T3's sidebar with their own binding (PR owners in Autopilot and Orchestrate) are top-level threads launched with a worktree strategy. See [Top-level threads](threads.md#top-level-threads).
- T3 storage cleanup can remove that worktree after the thread ends. See [Local state](SKILL.md#local-state).
- Uncommitted changes are not copied into new worktrees. Commit or stash first, or point the brief at a pushed branch.

## Local command capacity

Children, launched threads, and this thread run on this machine, so their builds and tests share its cores. Run each local build, test suite, and verifier rerun through `python3 <skills>/landing/scripts/land.py slot -- <command>`, where `<skills>` is the directory that holds `<pstack-runtime>`. Run each benchmark or timing measurement through `python3 <skills>/landing/scripts/land.py slot --exclusive -- <command>`. It must be the outermost slot. The slot count, its config file, and the landing queue's reserved slot are in the landing skill's [Capacity](../landing/SKILL.md#capacity) section. A brief links that section and does not copy them.

- A slot wraps one command and ends when that command exits. Never hold a slot across a model turn, a `t3_thread_wait`, a `task_status` check, a `watch_pull_request` wait, or any other wait on children. Start dev servers with the preview tools, not in a slot.
- Slots limit heavy commands, not agents. Do not cap owners, children, or launched threads at the slot count.
