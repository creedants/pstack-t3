#!/usr/bin/env python3
"""Install pstack-t3 skills into every provider skill directory T3 reads.

T3's `$` picker lists each provider's native skills, so pstack-t3 links its
generated skills into each provider's directory. Every link and every entry
moved aside is recorded in a manifest so `uninstall` restores the prior state.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
HARNESSES = ("claude", "codex", "grok", "cursor")
LEGACY_NAME = "install-manifest.json"
OWNERS_DIR = "install-owners"


def skill_dirs(scope_root, user):
    home = Path(os.environ.get("HOME", str(Path.home())))
    claude = Path(os.environ["CLAUDE_CONFIG_DIR"]) if user and os.environ.get("CLAUDE_CONFIG_DIR") else (home / ".claude" if user else scope_root / ".claude")
    base = home if user else scope_root
    return {
        "claude": claude / "skills",
        "codex": base / ".agents" / "skills",
        "grok": base / ".grok" / "skills",
        "cursor": base / ".cursor" / "skills",
    }


def extra_dirs(scope_root, user):
    """Other directories each provider also reads skills from, where stale copies can compete."""
    home = Path(os.environ.get("HOME", str(Path.home())))
    base = home if user else scope_root
    claude = skill_dirs(scope_root, user)["claude"]
    return {
        "claude": [],
        "codex": [base / ".codex" / "skills"],
        "grok": [],
        "cursor": [base / ".agents" / "skills", base / ".codex" / "skills", claude],
    }


def state_dir(scope_root, user):
    if user:
        config = Path(os.environ.get("XDG_CONFIG_HOME") or Path(os.environ.get("HOME", str(Path.home()))) / ".config")
        return config / "pstack-t3"
    return scope_root / ".pstack"


def owner_path(state, checkout):
    digest = hashlib.sha256(str(checkout).encode()).hexdigest()[:16]
    return Path(state) / OWNERS_DIR / f"{digest}.json"


def link_target(checkout, path):
    return os.path.join(str(checkout), "skills", os.path.basename(path))


def proves(checkout, entry, original):
    try:
        text = os.readlink(entry)
    except OSError:
        return False
    base = os.path.realpath(os.path.dirname(original))
    return os.path.normpath(os.path.join(base, text)) == link_target(checkout, original)


def slot_of(path):
    return (os.path.realpath(os.path.dirname(path)), os.path.basename(path))


def ordered(harnesses):
    have = {harness for harness in harnesses if harness in HARNESSES}
    return tuple(harness for harness in HARNESSES if harness in have)


def inside_checkout(path):
    real = Path(os.path.realpath(path))
    skills = Path(os.path.realpath(SKILLS))
    return real == skills or skills in real.parents


def describe(path):
    if os.path.islink(path):
        return f"link to {os.readlink(path)}"
    return "directory" if os.path.isdir(path) else "file"


@dataclass(frozen=True)
class LinkRec:
    raw: object
    path: str
    harnesses: tuple
    checkout: str | None


@dataclass(frozen=True)
class BackupRec:
    raw: object
    original: str
    backup: str
    harnesses: tuple


@dataclass(frozen=True)
class View:
    claims: dict
    links: tuple
    backups: tuple


@dataclass(frozen=True)
class Mine:
    path: str
    harnesses: tuple
    claim_paths: tuple
    tagged: tuple
    voucher: object


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


def harnesses_for(path, entry, scope, user):
    if isinstance(entry, dict) and isinstance(entry.get("harnesses"), list):
        return ordered(entry["harnesses"])
    if isinstance(entry, dict) and isinstance(entry.get("harness"), str) and entry["harness"] in HARNESSES:
        return (entry["harness"],)
    parent = os.path.realpath(os.path.dirname(path))
    found = [harness for harness, directory in skill_dirs(scope, user).items() if os.path.realpath(directory) == parent]
    return ordered(found) if found else HARNESSES


def parse_link(entry, scope, user):
    if isinstance(entry, str):
        return LinkRec(entry, entry, harnesses_for(entry, None, scope, user), None)
    if not isinstance(entry, dict):
        return None
    path = entry.get("path")
    if not isinstance(path, str):
        return None
    checkout = entry.get("checkout") if isinstance(entry.get("checkout"), str) else None
    return LinkRec(entry, path, harnesses_for(path, entry, scope, user), checkout)


def parse_backup(entry, scope, user):
    if not isinstance(entry, dict):
        return None
    original, backup = entry.get("original"), entry.get("backup")
    if not isinstance(original, str) or not isinstance(backup, str):
        return None
    return BackupRec(entry, original, backup, harnesses_for(original, entry, scope, user))


def read_object(path):
    try:
        data = json.loads(path.read_text())
    except ValueError:
        sys.exit(f"{path} is not valid JSON; fix or move it and rerun")
    if not isinstance(data, dict):
        sys.exit(f"{path} is not a JSON object; fix or move it and rerun")
    return data


def legacy_lists(path):
    data = read_object(path)
    found = []
    for key in ("links", "backups"):
        value = data.get(key, [])
        if not isinstance(value, list):
            sys.exit(f"{path} has a {key} entry that is not a list; fix or move it and rerun")
        found.append(value)
    return data, found[0], found[1]


def read_legacy(state, scope, user):
    path = Path(state) / LEGACY_NAME
    if not path.exists():
        return (), ()
    _data, raw_links, raw_backups = legacy_lists(path)
    links = tuple(item for item in (parse_link(entry, scope, user) for entry in raw_links) if item)
    backups = tuple(item for item in (parse_backup(entry, scope, user) for entry in raw_backups) if item)
    return links, backups


def load(scope, user):
    state = state_dir(scope, user)
    root = str(ROOT)
    links, backups = read_legacy(state, scope, user)
    return View(current_claims(state, root), links, backups)


def records(view, root):
    parts = {}

    def bucket(path):
        return parts.setdefault(slot_of(path), {"claims": [], "tagged": [], "untagged": []})

    for path, harnesses in view.claims.items():
        bucket(path)["claims"].append((path, harnesses))
    for link in view.links:
        if link.checkout == root:
            bucket(link.path)["tagged"].append(link)
        elif link.checkout is None:
            bucket(link.path)["untagged"].append(link)
    present = stacks(view)
    found = {}
    for slot, group in parts.items():
        live = None
        for path, _harnesses in group["claims"]:
            if proves(root, path, path):
                live = path
                break
        if live is None:
            for link in group["tagged"] + group["untagged"]:
                if proves(root, link.path, link.path):
                    live = link.path
                    break
        proving_live = live is not None
        proving = proving_live or any(proves(root, row.backup, row.original) for row in present.get(slot, ()))
        claims = group["claims"]
        tagged = group["tagged"]
        untagged = group["untagged"]
        if not (claims or tagged or (untagged and proving)):
            continue
        if live is None:
            if claims:
                live = claims[0][0]
            elif tagged:
                live = tagged[0].path
            else:
                live = untagged[0].path
        harnesses = ordered(
            [harness for _path, owned in claims for harness in owned]
            + [harness for link in tagged + untagged for harness in link.harnesses]
        )
        voucher = untagged[-1].raw if untagged and proving_live and not tagged else None
        found[slot] = Mine(live, harnesses, tuple(path for path, _owned in claims), tuple(tagged), voucher)
    return found


def stacks(view):
    found = {}
    for row in view.backups:
        if os.path.lexists(row.backup):
            found.setdefault(slot_of(row.original), []).append(row)
    return found


def layout(scope, user, selected):
    directories = skill_dirs(scope, user)
    user_dirs = {}
    if not user:
        for directory in skill_dirs(None, True).values():
            user_dirs.setdefault(os.path.realpath(directory), directory)
    grouped = {}
    for harness in HARNESSES:
        directory = directories[harness]
        grouped.setdefault(os.path.realpath(directory), []).append((harness, directory))
    chosen = set(selected)
    groups, refusals = [], []
    for real, members in grouped.items():
        harnesses = tuple(harness for harness, _directory in members)
        if not chosen.intersection(harnesses):
            continue
        directory = members[0][1]
        label = next(harness for harness, _directory in members if harness in chosen)
        shown = directories[label]
        if real in user_dirs:
            refusals.append(f"{label}: {shown} already resolves to {user_dirs[real]}; nothing to link")
            continue
        if inside_checkout(directory):
            refusals.append(f"{label}: {shown} already resolves to {SKILLS}; nothing to link")
            continue
        groups.append((str(directory), harnesses))
    return groups, refusals


def tagged_exists(view, path, root):
    return any(link.checkout == root and link.path == path for link in view.links)


def plan_install(view, scope, user, selected, names, root, replace):
    groups, refusals = layout(scope, user, selected)
    owned = records(view, root)
    steps, conflicts = [], []
    untracked = adopted = 0
    for directory, harnesses in groups:
        for name in names:
            path = os.path.join(directory, name)
            if proves(root, path, path):
                record = owned.get(slot_of(path))
                if record is None:
                    untracked += 1
                else:
                    union = ordered(record.harnesses + harnesses)
                    if not record.claim_paths or set(union) != set(record.harnesses):
                        if not record.claim_paths:
                            adopted += 1
                        steps.append(Step("record", path, harnesses=union, add_claims=((path, union),), adopted=not record.claim_paths))
                continue
            # Moving a path that is this checkout's own skill directory would relocate the checkout.
            if inside_checkout(path):
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


def plan_uninstall(view, root, selected):
    chosen = set(selected)
    shared = set()
    owned = records(view, root)
    present = stacks(view)

    def selected_row(harnesses):
        have = set(harnesses)
        if have <= chosen:
            return True
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
            if proves(root, row.backup, row.original):
                consumers.append(Step("withdraw", record.path, backup=row.backup, remove_backups=(row.backup,)))
                withdrawn.add(row.backup)
        unlinks = proves(root, record.path, record.path)
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
            steps.append(Step("restore", top.original, backup=top.backup, remove_backups=(top.backup,)))
        elif not free and selected_row(top.harnesses):
            occupied.append(occupied_note(top))
    return Plan(tuple(steps), occupied=tuple(occupied), shared=tuple(sorted(shared)), kept=kept)


def atomic_write(directory, name, text):
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(temporary, directory / name)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def write_claims(state, root, claims):
    path = owner_path(state, root)
    if not claims:
        if path.exists():
            path.unlink()
        return
    body = {"checkout": root, "links": {key: {"harnesses": list(claims[key])} for key in claims}}
    atomic_write(path.parent, path.name, json.dumps(body, indent=2) + "\n")


def current_claims(state, root):
    path = owner_path(state, root)
    if not path.exists():
        return {}
    data = read_object(path)
    recorded = data.get("checkout")
    if recorded != root:
        sys.exit(f"{path} records {recorded}, not this checkout")
    links = data.get("links")
    if not isinstance(links, dict):
        return {}
    claims = {}
    for key, value in links.items():
        harnesses = value.get("harnesses") if isinstance(value, dict) else None
        if isinstance(key, str) and isinstance(harnesses, list):
            claims[key] = ordered(harnesses)
    return claims


def patch_legacy(state, add_links, add_backups, remove_links, remove_backups, adding):
    """Return the link and backup rows this call appended."""
    if not add_links and not add_backups and not remove_links and not remove_backups:
        return (), ()
    path = Path(state) / LEGACY_NAME
    # Older installers still read this file, so a cleanup leaves empty lists in place.
    if path.exists():
        data, links, backups = legacy_lists(path)
    elif adding:
        data, links, backups = {}, [], []
    else:
        return (), ()
    added_links, added_backups = [], []
    changed = False
    if adding:
        for row in add_links:
            if isinstance(row, dict) and any(
                isinstance(have, dict) and have.get("path") == row.get("path") and have.get("checkout") == row.get("checkout")
                for have in links
            ):
                continue
            links.append(row)
            added_links.append(row)
            changed = True
        for row in add_backups:
            backups.append(row)
            added_backups.append(row["backup"])
            changed = True
    else:
        for raw in remove_links:
            for index in range(len(links) - 1, -1, -1):
                if links[index] == raw:
                    del links[index]
                    changed = True
                    break
        for backup in remove_backups:
            for index in range(len(backups) - 1, -1, -1):
                have = backups[index]
                if isinstance(have, dict) and have.get("backup") == backup:
                    del backups[index]
                    changed = True
                    break
    if changed:
        data["links"] = links
        data["backups"] = backups
        atomic_write(Path(state), LEGACY_NAME, json.dumps(data, indent=2) + "\n")
    return tuple(added_links), tuple(added_backups)


def add_records(step, state, root):
    """Save a step's adds before its act. Return a function that takes back exactly the records that were new."""
    previous = {}
    if step.add_claims:
        claims = current_claims(state, root)
        for path, harnesses in step.add_claims:
            previous[path] = claims.get(path)
            claims[path] = ordered(harnesses)
        write_claims(state, root, claims)
    links, backups = patch_legacy(state, step.add_links, step.add_backups, (), (), True)

    def undo():
        patch_legacy(state, (), (), links, backups, False)
        if previous:
            claims = current_claims(state, root)
            for path, harnesses in previous.items():
                if harnesses is None:
                    claims.pop(path, None)
                else:
                    claims[path] = harnesses
            write_claims(state, root, claims)

    return undo


def remove_records(step, state, root):
    # Owner file first on add and last on remove, so a crash keeps an extra claim.
    patch_legacy(state, (), (), step.remove_links, step.remove_backups, False)
    if step.remove_claims:
        claims = current_claims(state, root)
        for path in step.remove_claims:
            claims.pop(path, None)
        write_claims(state, root, claims)


def ready(step, root):
    if step.kind == "create":
        return not os.path.lexists(step.path)
    if step.kind == "move":
        return bool(step.backup) and os.path.lexists(step.path) and not os.path.lexists(step.backup)
    if step.kind in ("record", "forget"):
        return True
    if step.kind == "unlink":
        return proves(root, step.path, step.path)
    if step.kind == "withdraw":
        return proves(root, step.backup, step.path)
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


def act(step, root):
    if step.kind == "create":
        os.makedirs(os.path.dirname(step.path), exist_ok=True)
        os.symlink(link_target(root, step.path), step.path, target_is_directory=True)
    elif step.kind == "move":
        os.makedirs(os.path.dirname(step.backup), exist_ok=True)
        shutil.move(step.path, step.backup)
    elif step.kind == "unlink":
        os.unlink(step.path)
    elif step.kind == "withdraw":
        os.unlink(step.backup)
    elif step.kind == "restore":
        os.makedirs(os.path.dirname(step.path), exist_ok=True)
        shutil.move(step.backup, step.path)


def execute(plan, state, root):
    counts = {"linked": 0, "removed": 0, "restored": 0, "withdrawn": 0, "kept_extra": 0}
    failed = set()
    stamp = None
    for step in plan.steps:
        if step.kind == "move":
            if stamp is None:
                backups = Path(state) / "backups"
                backups.mkdir(parents=True, exist_ok=True)
                stamp = tempfile.mkdtemp(prefix=f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-", dir=backups)
            backup = os.path.join(stamp, step.place)
            step = replace(step, backup=backup, add_backups=({"harnesses": list(step.harnesses), "original": step.path, "backup": backup},))
        if step.kind == "forget" and step.path in failed:
            counts["kept_extra"] += len(step.remove_claims)
            continue
        if not ready(step, root):
            note = skip_note(step)
            if note:
                print(note)
            failed.add(step.path)
            continue
        undo = add_records(step, state, root)
        try:
            act(step, root)
        except OSError as error:
            undo()
            print(f"skipped {subject(step)}: {error}")
            failed.add(step.path)
            continue
        remove_records(step, state, root)
        if step.kind == "create":
            counts["linked"] += 1
        elif step.kind == "unlink":
            counts["removed"] += 1
        elif step.kind == "withdraw":
            counts["removed"] += 1
            counts["withdrawn"] += 1
        elif step.kind == "restore":
            counts["restored"] += 1
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
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def count_steps(plan, kind):
    kinds = {kind} if isinstance(kind, str) else set(kind)
    return sum(1 for step in plan.steps if step.kind in kinds)


def report_install(plan, state, root, executed, dry_run):
    for line in plan.refusals:
        print(line)
    if dry_run:
        moved = {step.path for step in plan.steps if step.kind == "move"}
        for step in plan.steps:
            if step.kind != "create":
                continue
            suffix = f" (replacing {describe(step.path)})" if step.path in moved else ""
            print(f"would link {step.path} -> {link_target(root, step.path)}{suffix}")
        if plan.untracked:
            print(f"{plan.untracked} links already point at this checkout but are not tracked; uninstall leaves them")
        if plan.adopted:
            print(f"adopted {plan.adopted} links an older installer recorded")
        print(f"{count_steps(plan, 'create')} links planned")
        return
    linked = 0 if executed is None else executed["linked"]
    joined = ", ".join(sorted({harness for step in plan.steps if step.kind == "create" for harness in step.harnesses}))
    print(f"linked {linked} skills into {joined or 'nothing (already installed)'}")
    if plan.untracked:
        print(f"{plan.untracked} links already point at this checkout but are not tracked; uninstall leaves them")
    if plan.adopted:
        print(f"adopted {plan.adopted} links an older installer recorded")
    print(f"manifest: {Path(state) / LEGACY_NAME}")


def report_uninstall(plan, executed, dry_run):
    for line in plan.occupied:
        print(line)
    if dry_run:
        removed = count_steps(plan, ("unlink", "withdraw"))
        restored = count_steps(plan, "restore")
        withdrawn = count_steps(plan, "withdraw")
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


def skill_names():
    return sorted(path.name for path in SKILLS.iterdir() if (path / "SKILL.md").is_file())


def install(args):
    if not SKILLS.is_dir():
        sys.exit("skills/ is missing; run python3 scripts/build.py first")
    user = args.project is None
    scope = Path(args.project).resolve() if args.project else None
    root = str(ROOT)
    state = state_dir(scope, user)
    names = skill_names()

    def make_plan(view):
        return plan_install(view, scope, user, args.harness, names, root, args.replace)

    def reject(plan):
        if plan.conflicts and not args.replace:
            for line in plan.refusals:
                print(line)
            lines = [f"  {'/'.join(harnesses)}: {path} ({describe(path)})" for harnesses, path in plan.conflicts]
            sys.exit("these skills already exist; rerun with --replace to move them aside (uninstall restores them):\n" + "\n".join(lines))

    plan = make_plan(load(scope, user))
    reject(plan)
    if args.dry_run or not plan.steps:
        report_install(plan, state, root, None, args.dry_run)
        return 0
    with locked(state):
        plan = make_plan(load(scope, user))
        reject(plan)
        if not plan.steps:
            report_install(plan, state, root, None, False)
            return 0
        report_install(plan, state, root, execute(plan, state, root), False)
    return 0


def uninstall(args):
    user = args.project is None
    scope = Path(args.project).resolve() if args.project else None
    root = str(ROOT)
    state = state_dir(scope, user)

    def make_plan(view):
        return plan_uninstall(view, root, args.harness)

    plan = make_plan(load(scope, user))
    if args.dry_run or not plan.steps:
        report_uninstall(plan, None, args.dry_run)
        return 0
    with locked(state):
        plan = make_plan(load(scope, user))
        if not plan.steps:
            report_uninstall(plan, None, False)
            return 0
        report_uninstall(plan, execute(plan, state, root), False)
    return 0


def points_here(path):
    return proves(str(ROOT), os.fspath(path), os.fspath(path))


def doctor(args):
    user = args.project is None
    scope = Path(args.project).resolve() if args.project else None
    names = skill_names() if SKILLS.is_dir() else []
    healthy = True
    for harness, directory in skill_dirs(scope, user).items():
        if harness not in args.harness:
            continue
        if Path(os.path.realpath(directory)) == Path(os.path.realpath(SKILLS)):
            print(f"{harness:7} {directory}: resolves to the pstack-t3 skills tree itself")
            continue
        if inside_checkout(directory):
            print(f"{harness:7} {directory}: points inside one pstack-t3 skill, so the other skills are invisible")
            healthy = False
            continue
        installed = [name for name in names if points_here(directory / name)]
        foreign = [name for name in names if (directory / name).exists() and not points_here(directory / name)]
        missing = [name for name in names if not (directory / name).exists()]
        healthy &= not foreign and not missing
        print(f"{harness:7} {directory}: {len(installed)}/{len(names)} pstack-t3" +
              (f", {len(foreign)} taken by other copies ({', '.join(foreign[:5])}{'...' if len(foreign) > 5 else ''})" if foreign else "") +
              (f", {len(missing)} missing" if missing else ""))
        for extra in extra_dirs(scope, user)[harness]:
            stale = [name for name in names if (extra / name).exists() and not points_here(extra / name)]
            if stale:
                healthy = False
                print(f"        {extra} also holds other copies of {len(stale)} of these "
                      f"({', '.join(stale[:5])}{'...' if len(stale) > 5 else ''}); {harness} may load those instead")
        if not user:
            # Claude and Grok load the user copy when both scopes define a name.
            user_directory = skill_dirs(None, True)[harness]
            shadowing = [name for name in installed if (user_directory / name).exists() and not points_here(user_directory / name)]
            if shadowing:
                healthy = False
                print(f"        user scope {user_directory} has other copies of {len(shadowing)} of these "
                      f"({', '.join(shadowing[:5])}{'...' if len(shadowing) > 5 else ''}); providers that prefer user scope will load those instead")
    return 0 if healthy else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "uninstall", "doctor"), nargs="?", default="install")
    parser.add_argument("--project", help="install into this repository instead of the user's skill directories")
    parser.add_argument("--harness", default="all", help="comma list of " + ",".join(HARNESSES) + ", or all")
    parser.add_argument("--replace", action="store_true", help="move conflicting skills aside; uninstall restores them")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.harness = HARNESSES if args.harness == "all" else tuple(item.strip() for item in args.harness.split(","))
    unknown = set(args.harness) - set(HARNESSES)
    if unknown:
        parser.error(f"unknown harness {', '.join(sorted(unknown))}")
    return {"install": install, "uninstall": uninstall, "doctor": doctor}[args.command](args) or 0


if __name__ == "__main__":
    sys.exit(main())
