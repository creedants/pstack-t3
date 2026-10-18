from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from . import layout
from . import moves
from . import plans
from . import records
from . import strays

def points_here(path):
    return layout.proves(str(layout.ROOT), os.fspath(path), os.fspath(path))

@dataclass(frozen=True)
class Finding:
    """One record that no longer matches the disk.

    `line` is the whole sentence doctor prints when the record is alone in its head, advice included.
    `head` is the sentence doctor prints once over every record with the same `head`, without its leading count.
    `item` is this record's line under that head. A finding with an empty `head` is never grouped.
    `harnesses` are the harnesses it prints under. A finding with none prints after the last harness.
    """
    harnesses: tuple
    line: str
    head: str = ""
    item: str = ""

@dataclass(frozen=True)
class Unread:
    """One record file doctor could not use. `line` names the file and says what doctor did not check.

    `blinds` is true for the manifest and for this checkout's owner file, the two files install and uninstall stop on.
    """
    line: str
    blinds: bool

@dataclass(frozen=True)
class Planned:
    """What install plans right now for one harness list.

    `plain` and `forced` are the slots, as `slot_of` returns them, of the create steps in the plans returned by
    `plan_install` without and with --replace. `taken` is the number of conflicts in the plan without --replace.
    """
    plain: frozenset
    forced: frozenset
    taken: int

@dataclass(frozen=True)
class Audit:
    """What doctor found in the records. `findings` are Findings and `unread` are Unreads."""
    findings: tuple
    unread: tuple

RELINK = ('claim {path}: nothing is there; run "{install}" to link it again, '
          'then "{uninstall}" removes the link and this claim')

RELINK_MANY = ('claims have nothing at their paths; run "{install}" to link them again, '
               'then "{uninstall}" removes the links and these claims:')

REPLACE = ('claim {path}: {there}; "{install}" stops on {n} taken paths; with --replace it moves them aside '
           'and links this path (uninstall restores them and removes this claim)')

REPLACE_MANY = ('claims; "{install}" stops on {n} taken paths; with --replace it moves them aside '
                'and links the path of each claim (uninstall restores them and removes these claims):')

EDIT = ('claim {path}: {there}; install plans no link at that path, so no command clears this claim; '
        'to drop it, delete the "{path}" entry from {owner_file}')

EDIT_MANY = ('claims; install plans no link at the path of any of them, so no command clears them; '
             'to drop one, delete the entry named by its path from {owner_file}:')

HELD_CLAIM = ('claim {path}: {there}, and {aside} holds this checkout\'s link for it; '
              '"{dry_run}" prints what the next run does with it')

HELD_MANY = 'records; "{dry_run}" prints what the next run does with the entry held for each:'

INERT_ROW = ('backup row {backup}: nothing is there (recorded as the backup of {original}); uninstall skips the row '
             'while that path is empty; to drop it, delete the row from "backups" in {manifest}')

INERT_ROW_MANY = ('backup rows have nothing at their backup paths; uninstall skips each row while its backup path is empty; '
                  'to drop one, delete the row from "backups" in {manifest}:')

UNREACHED_ROW = "backup row {backup}: " + plans.UNREACHED + '; to drop the row, delete it from "backups" in {manifest}'

HELD_ROW = ('backup row {backup}: nothing is there, and {aside} holds an entry under that name; '
            '"{dry_run}" prints what the next run does with it')

AWAY = ('{file}: claims {n} links here for checkout {checkout}, and no directory is at {checkout}; '
        'delete this file unless that checkout will be back at that path; deleting it removes that checkout\'s claims '
        'and removes no link they name and no row of {manifest}')

def command(args, *words):
    """One shell command for a person to copy and run from this checkout. Every argument is quoted for a POSIX shell as needed."""
    project = (f"--project={args.project}",) if args.project else ()
    return shlex.join(("python3", "scripts/install.py", *words, *project))

def claim_finding(args, state, root, path, harnesses, aside, planned):
    """The Finding for one stale claim.

    `planned` returns the Planned for a harness list. A claim with nothing held aside for it takes its advice from that.
    """
    there = f"a {layout.describe(path)} is there" if os.path.lexists(path) else "nothing is there"
    if aside:
        dry_run = command(args, "uninstall", "--dry-run")
        return Finding(harnesses, HELD_CLAIM.format(path=path, there=there, aside=aside, dry_run=dry_run),
                       HELD_MANY.format(dry_run=dry_run), f"claim {path}: {there}, and {aside} holds this checkout's link for it")
    plans = planned(harnesses)
    slot = layout.slot_of(path)
    install = command(args, "install", "--harness", ",".join(harnesses))
    if slot in plans.plain and not plans.taken:
        uninstall = command(args, "uninstall", "--harness", ",".join(harnesses))
        return Finding(harnesses, RELINK.format(path=path, install=install, uninstall=uninstall),
                       RELINK_MANY.format(install=install, uninstall=uninstall), path)
    if slot in plans.forced:
        return Finding(harnesses, REPLACE.format(path=path, there=there, install=install, n=plans.taken),
                       REPLACE_MANY.format(install=install, n=plans.taken), f"{path}: {there}")
    owner_file = layout.owner_path(state, root)
    return Finding(harnesses, EDIT.format(path=path, there=there, owner_file=owner_file),
                   EDIT_MANY.format(owner_file=owner_file), f"{path}: {there}")

def row_finding(args, state, row, aside):
    """The Finding for one backup row with nothing at its backup path."""
    if aside:
        dry_run = command(args, "uninstall", "--dry-run")
        return Finding(row.harnesses, HELD_ROW.format(backup=row.backup, aside=aside, dry_run=dry_run),
                       HELD_MANY.format(dry_run=dry_run),
                       f"backup row {row.backup}: nothing is there, and {aside} holds an entry under that name")
    manifest = Path(state) / layout.LEGACY_NAME
    if not os.path.isabs(row.backup):
        return Finding(row.harnesses, UNREACHED_ROW.format(backup=row.backup, original=row.original, manifest=manifest))
    return Finding(row.harnesses, INERT_ROW.format(backup=row.backup, original=row.original, manifest=manifest),
                   INERT_ROW_MANY.format(manifest=manifest), f"{row.backup} (recorded as the backup of {row.original})")

def grouped(findings):
    """The lines doctor prints for `findings`.

    Findings with the same non-empty `head` print as one group at the position of the first of them: the count and
    the head, then the `item` of each, two columns deeper. A finding alone in its head prints its `line`.
    """
    groups = {}
    for index, finding in enumerate(findings):
        groups.setdefault(finding.head or index, []).append(finding)
    lines = []
    for head, members in groups.items():
        if len(members) == 1:
            lines.append(members[0].line)
        else:
            lines.append(f"{len(members)} {head}")
            lines.extend(f"  {member.item}" for member in members)
    return lines

def audit(args, scope, user, names):
    """Read the records and return an Audit. Reads only: no lock, no directory made, no file written."""
    state, root = layout.state_dir(scope, user), str(layout.ROOT)
    stale, away, unread = [], [], []
    mine = layout.owner_path(state, root)
    for name in moves.listing(state / layout.OWNERS_DIR):
        file = state / layout.OWNERS_DIR / name
        if not name.endswith(".json") or file == mine:
            continue
        try:
            data = records.read_object(file)
        except records.Unreadable as error:
            unread.append(Unread(f"{error} (doctor read no checkout from it)", False))
            continue
        if data is None:
            continue
        checkout = data.get("checkout")
        if not isinstance(checkout, str):
            unread.append(Unread(f"{file} names no checkout (doctor read no checkout from it)", False))
            continue
        if os.path.isdir(checkout):
            continue
        claimed = records.claims_in(data)
        for harness in layout.HARNESSES:
            count = sum(1 for harnesses in claimed.values() if harness in harnesses)
            if count:
                away.append(Finding((harness,), AWAY.format(file=file, n=count, checkout=checkout, manifest=state / layout.LEGACY_NAME)))
    try:
        manifest = records.read_legacy(state, scope, user)
    except (records.Unreadable, OSError) as error:
        manifest = None
        unread.append(Unread(f"{error} (doctor checked no claims and no backup rows)", True))
    try:
        claims = records.current_claims(state, root)
    except records.Unreadable as error:
        claims = {}
        unread.append(Unread(f"{error} (doctor checked no claims of this checkout)", True))
    if manifest is None:
        return Audit(tuple(away), tuple(unread))
    view = records.View(claims, *manifest)
    kept = {}

    def created(plan):
        return frozenset(layout.slot_of(step.path) for step in plan.steps if step.kind == "create")

    def planned(harnesses):
        if harnesses not in kept:
            plain = plans.plan_install(view, scope, user, harnesses, names, root, replace=False)
            forced = plans.plan_install(view, scope, user, harnesses, names, root, replace=True)
            kept[harnesses] = Planned(created(plain), created(forced), len(plain.conflicts))
        return kept[harnesses]

    surveyed = strays.survey(view, scope, user, state, root, layout.HARNESSES)
    present = records.stacks(view)
    for path, harnesses in claims.items():
        if layout.proves(root, path, path) or records.set_aside(present, root, layout.slot_of(path)):
            continue
        aside = next((stray.path for stray in surveyed
                      if stray.kind in ("home", "own") and layout.slot_of(stray.home) == layout.slot_of(path)), None)
        stale.append(claim_finding(args, state, root, path, harnesses, aside, planned))
    held_for = {stray.home: stray.path for stray in surveyed if stray.kind != "empty"}
    for row in view.backups:
        if not os.path.lexists(row.backup):
            aside = held_for.get(row.backup) or (None if os.path.isabs(row.backup) else strays.held_beside(row.backup))
            stale.append(row_finding(args, state, row, aside))
    return Audit(tuple(stale + away), tuple(unread))

def link_health(harness, directory, names, scope, user):
    """Print the pstack-t3 link state of `directory` for `harness`.

    Return True when `directory` resolves to SKILLS and False for other paths inside SKILLS.
    Otherwise check its named skills, the directories from extra_dirs, and user-scope shadowing in project scope.
    """
    if Path(os.path.realpath(directory)) == Path(os.path.realpath(layout.SKILLS)):
        print(f"{harness:7} {directory}: resolves to the pstack-t3 skills tree itself")
        return True
    if layout.inside_checkout(directory):
        print(f"{harness:7} {directory}: points inside one pstack-t3 skill, so the other skills are invisible")
        return False
    installed = [name for name in names if points_here(directory / name)]
    foreign = [name for name in names if (directory / name).exists() and not points_here(directory / name)]
    missing = [name for name in names if not (directory / name).exists()]
    healthy = not foreign and not missing
    print(f"{harness:7} {directory}: {len(installed)}/{len(names)} pstack-t3" +
          (f", {len(foreign)} taken by other copies ({', '.join(foreign[:5])}{'...' if len(foreign) > 5 else ''})" if foreign else "") +
          (f", {len(missing)} missing" if missing else ""))
    for extra in layout.extra_dirs(scope, user)[harness]:
        stale = [name for name in names if (extra / name).exists() and not points_here(extra / name)]
        if stale:
            healthy = False
            print(f"        {extra} also holds other copies of {len(stale)} of these "
                  f"({', '.join(stale[:5])}{'...' if len(stale) > 5 else ''}); {harness} may load those instead")
    if not user:
        # Claude and Grok load the user copy when both scopes define a name.
        user_directory = layout.skill_dirs(None, True)[harness]
        shadowing = [name for name in installed if (user_directory / name).exists() and not points_here(user_directory / name)]
        if shadowing:
            healthy = False
            print(f"        user scope {user_directory} has other copies of {len(shadowing)} of these "
                  f"({', '.join(shadowing[:5])}{'...' if len(shadowing) > 5 else ''}); providers that prefer user scope will load those instead")
    return healthy

def doctor(args):
    stop = layout.unbuilt()
    if stop:
        print(stop)
        return 1
    user = args.project is None
    scope = layout.scope_of(args)
    names = layout.skill_names()
    found = audit(args, scope, user, names)
    healthy = True
    for harness, directory in layout.skill_dirs(scope, user).items():
        if harness not in args.harness:
            continue
        healthy &= link_health(harness, directory, names, scope, user)
        for line in grouped([finding for finding in found.findings if harness in finding.harnesses]):
            print(f"        {line}")
    for line in grouped([finding for finding in found.findings if not finding.harnesses]):
        print(line)
    for item in found.unread:
        print(item.line)
    return 0 if healthy and not any(item.blinds for item in found.unread) else 1
