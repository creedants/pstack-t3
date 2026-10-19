from __future__ import annotations

import fcntl
import os
import shutil
import sys
import time
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path

from . import layout
from . import moves
from . import plans
from . import records
from . import restore
from . import strays
def _tally_lines(plan):
    if plan.untracked:
        print(f"{plan.untracked} links already point at this checkout but are not tracked; uninstall leaves them")
    if plan.adopted:
        print(f"adopted {plan.adopted} links an older installer recorded")
def _context(args):
    user = args.project is None
    scope = layout.scope_of(args)
    root = str(layout.ROOT)
    state = layout.state_dir(scope, user)
    return user, scope, root, state

def act(step, root):
    """Do the step's change. Return why it was not done when it declined without an error."""
    if step.kind == "create":
        os.makedirs(os.path.dirname(step.path), exist_ok=True)
        os.symlink(layout.link_target(root, step.path), step.path, target_is_directory=True)
    elif step.kind == "move":
        shutil.move(step.path, step.backup)
    elif step.kind == "unlink":
        return moves.remove_link(step.path, root, step.path, "link")
    elif step.kind == "withdraw":
        return moves.remove_link(step.backup, root, step.path, "backup")
    elif step.kind == "restore":
        os.makedirs(os.path.dirname(step.path), exist_ok=True)
        return restore.restore_backup(step.backup, step.path, step.held)

def execute(plan, state, root):
    counts = {"linked": 0, "removed": 0, "restored": 0, "withdrawn": 0, "kept_extra": 0, "skipped": 0}
    failed = set()
    stamp = None
    made = []
    try:
        for step in plan.steps:
            if step.kind == "move":
                if stamp is None:
                    backups = Path(state) / "backups"
                    backups.mkdir(parents=True, exist_ok=True)
                    stamp = moves.made_directory(backups, f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-")
                backup = os.path.join(stamp, step.place)
                step = replace(step, backup=backup, add_backups=({"harnesses": list(step.harnesses), "original": step.path, "backup": backup},))
            # A backup row that holds this checkout's entry needs the claim to stay owned after it is restored.
            if step.kind == "forget" and (step.path in failed or records.buried(state, root, step.path)):
                counts["kept_extra"] += len(step.remove_claims)
                continue
            if not plans.ready(step, root):
                note = plans.skip_note(step)
                if note:
                    print(note)
                    counts["skipped"] += 1
                failed.add(step.path)
                continue
            undo = records.add_records(step, state, root)
            try:
                if step.kind == "move":
                    harness = os.path.basename(os.path.dirname(step.backup))
                    if harness not in made:
                        # mkdir refuses a <harness> that is already there, so this run never uses one it did not make.
                        os.mkdir(os.path.dirname(step.backup))
                        made.append(harness)
                declined = act(step, root)
            except OSError as error:
                print(f"skipped {plans.subject(step)}: {error}")
                counts["skipped"] += 1
                failed.add(step.path)
                # A move can fail after copying part of the entry, and then the row is the only record of that copy.
                if not (step.kind == "move" and os.path.lexists(step.backup)):
                    undo()
                continue
            if declined:
                print(f"skipped {plans.subject(step)}: {declined}")
                counts["skipped"] += 1
                failed.add(step.path)
                undo()
                continue
            records.remove_records(step, state, root)
            for backup in step.remove_backups:
                strays.prune(state, backup)
            if step.kind == "create":
                counts["linked"] += 1
            elif step.kind == "unlink":
                counts["removed"] += 1
            elif step.kind == "withdraw":
                counts["removed"] += 1
                counts["withdrawn"] += 1
            elif step.kind == "restore":
                counts["restored"] += 1
    finally:
        if stamp is not None:
            name = os.path.basename(stamp)
            for harness in made:
                strays.empty_out(state, name, harness)
            strays.empty_out(state, name)
    return counts

@contextmanager
def locked(state):
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    fd = os.open(state, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

def needs_lock(plan, strays, state):
    writes = bool(plan.steps) or any(stray.kind != "left" for stray in strays)
    another_run_can_hold_it = os.path.isdir(state)
    return writes or (bool(strays) and another_run_can_hold_it)

def report_install(plan, state, root, executed, dry_run):
    for line in plan.refusals:
        print(line)
    if dry_run:
        moved = {step.path for step in plan.steps if step.kind == "move"}
        for step in plan.steps:
            if step.kind != "create":
                continue
            suffix = f" (replacing {layout.describe(step.path)})" if step.path in moved else ""
            print(f"would link {step.path} -> {layout.link_target(root, step.path)}{suffix}")
        _tally_lines(plan)
        print(f"{plans.count_steps(plan, 'create')} links planned")
        return
    linked = 0 if executed is None else executed["linked"]
    joined = ", ".join(sorted({harness for step in plan.steps if step.kind == "create" for harness in step.harnesses}))
    print(f"linked {linked} skills into {joined or 'nothing (already installed)'}")
    _tally_lines(plan)
    print(f"manifest: {Path(state) / layout.LEGACY_NAME}")

def report_uninstall(plan, executed, dry_run):
    for line in plan.occupied:
        print(line)
    if dry_run:
        removed = plans.count_steps(plan, ("unlink", "withdraw"))
        restored = plans.count_steps(plan, "restore")
        withdrawn = plans.count_steps(plan, "withdraw")
        print(f"would remove {removed} links, would restore {restored} entries")
    else:
        removed = 0 if executed is None else executed["removed"]
        restored = 0 if executed is None else executed["restored"]
        withdrawn = 0 if executed is None else executed["withdrawn"]
        print(f"removed {removed} links, restored {restored} entries")
    if withdrawn:
        print(f"{withdrawn} of them had been moved aside by another checkout's --replace")
    for others in plan.shared:
        print(f"kept entries whose directory is shared with {others}; select those harnesses too to remove them")
    kept = plan.kept + (0 if executed is None else executed["kept_extra"])
    if kept:
        print(f"kept {kept} records whose links no longer point at this checkout; they apply again if the links come back")

def install(args):
    stop = layout.unbuilt()
    if stop:
        sys.exit(stop)
    user, scope, root, state = _context(args)
    names = layout.skill_names()

    def make_plan(view):
        return plans.plan_install(view, scope, user, args.harness, names, root, args.replace)

    def reject(plan):
        if plan.conflicts and not args.replace:
            for line in plan.refusals:
                print(line)
            lines = [f"  {'/'.join(harnesses)}: {path} ({layout.describe(path)})" for harnesses, path in plan.conflicts]
            sys.exit("these skills already exist; rerun with --replace to move them aside (uninstall restores them):\n" + "\n".join(lines))

    def find_strays(view):
        return strays.survey(view, scope, user, state, root, args.harness)

    view = records.load(state, scope, user)
    plan = make_plan(view)
    found = find_strays(view)
    if plan.conflicts and not args.replace:
        strays.settle(tuple(stray for stray in found if stray.kind == "left"), state, root, True)
    reject(plan)
    if args.dry_run or not needs_lock(plan, found, state):
        strays.settle(found, state, root, True)
        report_install(plan, state, root, None, args.dry_run)
        return 0
    with locked(state):
        strays.settle(find_strays(records.load(state, scope, user)), state, root, False)
        plan = make_plan(records.load(state, scope, user))
        reject(plan)
        executed = execute(plan, state, root) if plan.steps else None
        report_install(plan, state, root, executed, False)
    return layout.SKIPPED if executed and executed["skipped"] else 0

def uninstall(args):
    user, scope, root, state = _context(args)

    def make_plan(view, holds):
        return plans.plan_uninstall(view, root, args.harness, holds)

    def find_strays(view):
        return strays.survey(view, scope, user, state, root, args.harness)

    view = records.load(state, scope, user)
    with ExitStack() as holds:
        plan = make_plan(view, holds)
    found = find_strays(view)
    if args.dry_run or not needs_lock(plan, found, state):
        strays.settle(found, state, root, True)
        report_uninstall(plan, None, args.dry_run)
        return 0
    with locked(state), ExitStack() as holds:
        strays.settle(find_strays(records.load(state, scope, user)), state, root, False)
        plan = make_plan(records.load(state, scope, user), holds)
        executed = execute(plan, state, root) if plan.steps else None
        report_uninstall(plan, executed, False)
    return layout.SKIPPED if executed and executed["skipped"] else 0
