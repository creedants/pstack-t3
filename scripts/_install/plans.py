from __future__ import annotations

import os
from dataclasses import dataclass

from . import layout
from . import records
from . import strays

# Linux opens the entry itself with O_PATH. macOS opens a symlink itself with O_SYMLINK, and O_NONBLOCK keeps a FIFO from blocking.
HOLD_FLAGS = (os.O_PATH | os.O_NOFOLLOW) if hasattr(os, "O_PATH") else (os.O_RDONLY | getattr(os, "O_SYMLINK", os.O_NOFOLLOW) | os.O_NONBLOCK)

@dataclass(frozen=True)
class Held:
    """A descriptor open on a backup from the plan to its restore. An inode in use keeps its number, so no new entry can share it."""
    fd: int | None
    entry: tuple | None
    error: str | None = None

def hold(path, holds):
    """Open the entry at `path` itself and keep it open until `holds` closes. Return what restore needs to prove it is still there."""
    try:
        fd = os.open(path, HOLD_FLAGS)
    except OSError as error:
        return Held(None, None, error.strerror or str(error))
    holds.callback(os.close, fd)
    return Held(fd, layout.identity(os.fstat(fd)))

@dataclass(frozen=True)
class Step:
    kind: str
    path: str
    backup: str | None = None
    place: str = ""
    harnesses: tuple = ()
    add_claims: tuple = ()
    add_links: tuple = ()
    add_backups: tuple = ()
    remove_claims: tuple = ()
    remove_links: tuple = ()
    remove_backups: tuple = ()
    adopted: bool = False
    held: Held | None = None

@dataclass(frozen=True)
class Plan:
    steps: tuple = ()
    conflicts: tuple = ()
    refusals: tuple = ()
    untracked: int = 0
    adopted: int = 0
    occupied: tuple = ()
    shared: tuple = ()
    kept: int = 0

def tagged_exists(view, path, root):
    return any(link.checkout == root and link.path == path for link in view.links)

def plan_install(view, scope, user, selected, names, root, replace):
    groups, refusals = layout.layout(scope, user, selected)
    owned = records.records(view, root)
    steps, conflicts = [], []
    untracked = adopted = 0
    for directory, harnesses in groups:
        for name in names:
            path = os.path.join(directory, name)
            if layout.proves(root, path, path):
                record = owned.get(layout.slot_of(path))
                if record is None:
                    untracked += 1
                else:
                    union = layout.ordered(record.harnesses + harnesses)
                    if not record.claim_paths or set(union) != set(record.harnesses):
                        if not record.claim_paths:
                            adopted += 1
                        steps.append(Step("record", path, harnesses=union, add_claims=((path, union),), adopted=not record.claim_paths))
                continue
            # Moving a path that is this checkout's own skill directory would relocate the checkout.
            if layout.inside_checkout(path):
                continue
            if os.path.lexists(path):
                conflicts.append((harnesses, path))
                if replace:
                    steps.append(Step("move", path, place=f"{harnesses[0]}/{name}", harnesses=harnesses))
            if not os.path.lexists(path) or replace:
                row = {"harnesses": list(harnesses), "path": path, "checkout": root}
                add_links = () if tagged_exists(view, path, root) else (row,)
                steps.append(Step("create", path, harnesses=harnesses, add_claims=((path, harnesses),), add_links=add_links))
    return Plan(tuple(steps), tuple(conflicts), tuple(refusals), untracked, adopted)

def occupied_note(row):
    path, backup = row.original, row.backup
    if os.path.islink(path):
        spelled = os.path.normpath(os.path.join(os.path.dirname(path), os.readlink(path)))
        parent = os.path.dirname(spelled)
        if os.path.basename(spelled) == os.path.basename(path) and os.path.basename(parent) == "skills":
            checkout = os.path.dirname(parent)
            if not os.path.exists(checkout):
                return (f"kept backup {backup}: {path} is occupied by a link into deleted checkout {checkout}; "
                        "remove it and rerun uninstall")
    return f"kept backup {backup}: {path} is occupied; clear it and rerun uninstall"

UNREACHED = ("a relative path with nothing at it from this working directory (recorded as the backup of {original}); "
             "uninstall reads a relative path from the directory it runs in")

KEPT_UNREACHED = "kept backup row {backup}: " + UNREACHED

def unreached(row):
    return not os.path.isabs(row.backup) and not os.path.lexists(row.backup)

def plan_uninstall(view, root, selected, holds):
    chosen = set(selected)
    shared = set()
    owned = records.records(view, root)
    present = records.stacks(view)

    def covered(harnesses):
        return set(harnesses) <= chosen

    def selected_row(harnesses):
        if covered(harnesses):
            return True
        have = set(harnesses)
        if have & chosen:
            shared.add(", ".join(sorted(have - chosen)))
        return False

    steps = []
    occupied = []
    withdrawn = set()
    uncovered = set()
    kept = 0
    for slot, record in owned.items():
        if not selected_row(record.harnesses):
            continue
        rows = present.get(slot, ())
        consumers = []
        for row in rows:
            if layout.proves(root, row.backup, row.original) and strays.contested_by(row.backup) is None:
                consumers.append(Step("withdraw", record.path, backup=row.backup, remove_backups=(row.backup,)))
                withdrawn.add(row.backup)
        unlinks = layout.proves(root, record.path, record.path)
        if unlinks:
            consumers.append(Step("unlink", record.path))
            uncovered.add(slot)
        steps.extend(consumers)
        # A live entry over a withdrawn top backup covered this link; any other live entry is a repoint.
        consumed = unlinks or not os.path.lexists(record.path) or bool(rows and rows[-1].backup in withdrawn)
        if not consumers or not consumed:
            kept += len(record.claim_paths)
            continue
        links = tuple(link.raw for link in record.tagged)
        if record.voucher is not None:
            links = links + (record.voucher,)
        if record.claim_paths or links:
            steps.append(Step("forget", record.path, remove_claims=record.claim_paths, remove_links=links))
    for slot, rows in present.items():
        remaining = [row for row in rows if row.backup not in withdrawn]
        if not remaining:
            continue
        top = remaining[-1]
        free = slot in uncovered or not os.path.lexists(top.original)
        if free and (slot in uncovered or selected_row(top.harnesses)):
            aside = strays.contested_by(top.backup)
            if aside is None:
                steps.append(Step("restore", top.original, backup=top.backup, remove_backups=(top.backup,), held=hold(top.backup, holds)))
            else:
                occupied.append(f"kept backup {top.backup}: {aside} is held for that path and another entry is there now; "
                                "remove the one that is not the backup and rerun uninstall")
        elif not free and selected_row(top.harnesses):
            occupied.append(occupied_note(top))
    for row in view.backups:
        if unreached(row) and covered(row.harnesses) and strays.held_beside(row.backup) is None:
            occupied.append(KEPT_UNREACHED.format(backup=row.backup, original=row.original))
    return Plan(tuple(steps), occupied=tuple(occupied), shared=tuple(sorted(shared)), kept=kept)

def ready(step, root):
    if step.kind == "create":
        return not os.path.lexists(step.path)
    if step.kind == "move":
        return bool(step.backup) and os.path.lexists(step.path) and not os.path.lexists(step.backup)
    if step.kind in ("record", "forget"):
        return True
    if step.kind == "unlink":
        return layout.proves(root, step.path, step.path)
    if step.kind == "withdraw":
        return layout.proves(root, step.backup, step.path)
    if step.kind == "restore":
        return os.path.lexists(step.backup) and not os.path.lexists(step.path)
    return False

def subject(step):
    return {
        "unlink": f"unlink {step.path}",
        "withdraw": f"withdraw {step.backup}",
        "restore": f"restore {step.backup}",
        "create": f"link {step.path}",
        "move": f"move {step.path}",
    }.get(step.kind)

def skip_note(step):
    reason = {
        "unlink": "the link no longer matches the recorded checkout",
        "withdraw": "the backup no longer matches the recorded checkout",
        "restore": f"{step.path} changed before uninstall",
        "create": "it changed before install",
        "move": "it changed before install",
    }.get(step.kind)
    return f"skipped {subject(step)}: {reason}" if reason else None

def count_steps(plan, kind):
    kinds = {kind} if isinstance(kind, str) else set(kind)
    return sum(1 for step in plan.steps if step.kind in kinds)
