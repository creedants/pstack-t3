#!/usr/bin/env python3
"""Install pstack-t3 skills into every provider skill directory T3 reads.

T3's `$` picker lists each provider's native skills, so pstack-t3 links its
generated skills into each provider's directory. Every link and every entry
moved aside is recorded in a manifest so `uninstall` restores the prior state.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
HARNESSES = ("claude", "codex", "grok", "cursor")
V2_NAME = "install-manifest-v2.json"
LEGACY_NAME = "install-manifest.json"


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


def key(path):
    """One spelling per directory entry: the real parent plus the entry's own name."""
    path = os.fspath(path)
    return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))


def link_text(path):
    return os.readlink(path) if os.path.islink(path) else None


def lexical(text, at):
    return os.path.normpath(os.path.join(os.path.dirname(at), text))


@dataclass(frozen=True)
class LinkRow:
    path: str
    harnesses: tuple
    checkout: str

    @property
    def target(self):
        return os.path.join(self.checkout, "skills", os.path.basename(self.path))


@dataclass(frozen=True)
class BackupRow:
    original: str
    backup: str
    harnesses: tuple
    displaced: LinkRow | None


@dataclass(frozen=True)
class Manifest:
    links: tuple
    backups: tuple

    def live(self, path):
        return next((row for row in self.links if row.path == path), None)

    def with_live(self, row):
        kept = tuple(item for item in self.links if item.path != row.path)
        return replace(self, links=kept + (row,))

    def without_live(self, path):
        return replace(self, links=tuple(row for row in self.links if row.path != path))

    def without_backup(self, backup):
        return replace(self, backups=tuple(row for row in self.backups if row is not backup))


def union_harnesses(left, right):
    have = set(left) | set(right)
    return tuple(harness for harness in HARNESSES if harness in have)


def link_json(row):
    return {"path": row.path, "harnesses": list(row.harnesses), "checkout": row.checkout}


def manifest_json(manifest):
    return {
        "links": [link_json(row) for row in manifest.links],
        "backups": [{
            "original": row.original,
            "harnesses": list(row.harnesses),
            "backup": row.backup,
            "displaced": link_json(row.displaced) if row.displaced else None,
        } for row in manifest.backups],
    }


def alive(checkout):
    return checkout == str(ROOT) or os.path.isfile(os.path.join(checkout, "scripts", "install.py"))


def holds(row, text, at):
    """The link text at `at` is still the skill directory this row recorded."""
    if text is None or not alive(row.checkout):
        return False
    return lexical(text, at) == row.target


def legacy_owner(text, at):
    """The checkout a pre-owner link names, or None. An alias spelling is not one."""
    if text is None:
        return None
    spelled = lexical(text, at)
    if os.path.basename(spelled) != os.path.basename(at) or os.path.basename(os.path.dirname(spelled)) != "skills":
        return None
    root = os.path.dirname(os.path.dirname(spelled))
    if os.path.realpath(root) != root:
        return None
    marker = os.path.join(root, "skills", "pstack-runtime", "SKILL.md")
    # install.py alone is not enough: a lookalike project can have that file.
    if root == str(ROOT) or (os.path.isfile(os.path.join(root, "scripts", "install.py")) and os.path.isfile(marker)):
        return root
    return None


def entry_harnesses(entry, at, layout):
    """Harnesses recorded on the row. A bare path uses whichever provider directory holds it now."""
    if isinstance(entry, dict) and isinstance(entry.get("harnesses"), list):
        return tuple(entry["harnesses"])
    if isinstance(entry, dict) and isinstance(entry.get("harness"), str):
        return (entry["harness"],)
    found = tuple(harness for harness, real in layout if real == os.path.dirname(at))
    return found or tuple(HARNESSES)


def list_of(data, name):
    if not isinstance(data, dict):
        return []
    value = data.get(name)
    return value if isinstance(value, list) else []


def take_link(entry, legacy_file, links, layout, blocked):
    raw = entry["path"] if isinstance(entry, dict) else entry
    if not isinstance(raw, str):
        return
    current = not legacy_file and isinstance(entry, dict) and isinstance(entry.get("checkout"), str)
    if current:
        if raw != key(raw):
            return
        at = raw
        row = LinkRow(at, entry_harnesses(entry, at, layout), entry["checkout"])
    else:
        at = key(raw)
        if at in blocked:
            return
        owner = legacy_owner(link_text(at), at)
        if owner is None:
            return
        row = LinkRow(at, entry_harnesses(entry, at, layout), owner)
    if not holds(row, link_text(at), at):
        return
    previous = links.get(at)
    if previous is None:
        links[at] = row
    elif previous.checkout == row.checkout:
        links[at] = replace(previous, harnesses=union_harnesses(previous.harnesses, row.harnesses))


def parse_displaced(entry, layout):
    if not isinstance(entry, dict) or not isinstance(entry.get("checkout"), str):
        return None
    path = entry.get("path")
    if not isinstance(path, str) or path != key(path):
        return None
    return LinkRow(path, entry_harnesses(entry, path, layout), entry["checkout"])


def prepare_backup(entry, legacy_file, layout):
    if not isinstance(entry, dict):
        return None
    original_raw = entry.get("original")
    backup = entry.get("backup")
    if not isinstance(original_raw, str) or not isinstance(backup, str):
        return None
    current = not legacy_file and "displaced" in entry
    if current and original_raw != key(original_raw):
        return None
    original = original_raw if current else key(original_raw)
    missing = not os.path.lexists(backup)
    if missing and not current:
        return None
    harnesses = entry_harnesses(entry, original, layout)
    displaced = parse_displaced(entry.get("displaced"), layout) if current else None
    return {"original": original, "backup": backup, "harnesses": harnesses, "displaced": displaced, "missing": missing}


def reconcile(current, legacy, layout):
    """The manifest as the disk proves it. Forgets and migrates rows, and does not write."""
    links = {}
    for entry in list_of(current, "links"):
        take_link(entry, False, links, layout, ())
    blocked = set(links)
    for entry in list_of(legacy, "links"):
        take_link(entry, True, links, layout, blocked)
    prepared = []
    seen_backups = set()
    for entry in list_of(current, "backups"):
        item = prepare_backup(entry, False, layout)
        if item is not None:
            prepared.append(item)
            seen_backups.add(item["backup"])
    legacy_items = []
    for entry in list_of(legacy, "backups"):
        item = prepare_backup(entry, True, layout)
        if item is not None and item["backup"] not in seen_backups:
            legacy_items.append(item)
    prepared = legacy_items + prepared
    backups = []
    for index, item in enumerate(prepared):
        original = item["original"]
        top = not any(later["original"] == original for later in prepared[index + 1:])
        displaced = item["displaced"]
        if item["missing"]:
            if top and original not in links and displaced is not None and holds(displaced, link_text(original), original):
                links[original] = displaced
            continue
        if displaced is not None and not holds(displaced, link_text(item["backup"]), original):
            displaced = None
        backups.append(BackupRow(original, item["backup"], item["harnesses"], displaced))
    return Manifest(tuple(links.values()), tuple(backups))


def read_manifest(file):
    if not file.exists():
        return {}
    return json.loads(file.read_text())


def load(scope, user):
    state = state_dir(scope, user)
    layout = [(harness, os.path.realpath(directory)) for harness, directory in skill_dirs(scope, user).items()]
    return reconcile(read_manifest(state / V2_NAME), read_manifest(state / LEGACY_NAME), layout)


def save_manifest(file, manifest):
    if not manifest.links and not manifest.backups:
        if file.exists():
            file.unlink()
        return
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest_json(manifest), indent=2) + "\n")
    os.replace(temporary, file)


@dataclass(frozen=True)
class CreateLink:
    row: LinkRow


@dataclass(frozen=True)
class MoveAside:
    original: str
    harnesses: tuple
    displaced: LinkRow | None
    backup: str = ""


@dataclass(frozen=True)
class Record:
    row: LinkRow


@dataclass(frozen=True)
class Unlink:
    row: LinkRow


@dataclass(frozen=True)
class Withdraw:
    entry: BackupRow


@dataclass(frozen=True)
class Restore:
    entry: BackupRow


# Manifest first, then the disk. The other kinds change the disk first.
SAVE_THEN_ACT = (CreateLink, MoveAside, Record)


def apply(manifest, action):
    if isinstance(action, (CreateLink, Record)):
        return manifest.with_live(action.row)
    if isinstance(action, MoveAside):
        moved = manifest.without_live(action.original)
        backup = BackupRow(action.original, action.backup, action.harnesses, action.displaced)
        return replace(moved, backups=moved.backups + (backup,))
    if isinstance(action, Unlink):
        return manifest.without_live(action.row.path)
    if isinstance(action, Withdraw):
        return manifest.without_backup(action.entry)
    if isinstance(action, Restore):
        restored = manifest.without_backup(action.entry)
        if action.entry.displaced is None:
            return restored
        return restored.with_live(action.entry.displaced)
    raise TypeError(action)


def act(action):
    if isinstance(action, CreateLink):
        Path(action.row.path).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(action.row.target, action.row.path, target_is_directory=True)
    elif isinstance(action, MoveAside):
        Path(action.backup).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(action.original, action.backup)
    elif isinstance(action, Unlink):
        os.unlink(action.row.path)
    elif isinstance(action, Withdraw):
        os.unlink(action.entry.backup)
    elif isinstance(action, Restore):
        Path(action.entry.original).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(action.entry.backup, action.entry.original)


def still_valid(action):
    if isinstance(action, Unlink):
        return holds(action.row, link_text(action.row.path), action.row.path)
    if isinstance(action, Withdraw):
        entry = action.entry
        return entry.displaced is not None and holds(entry.displaced, link_text(entry.backup), entry.original)
    if isinstance(action, Restore):
        return os.path.lexists(action.entry.backup) and not os.path.lexists(action.entry.original)
    return True


def drop(manifest, action):
    if isinstance(action, Unlink):
        return manifest.without_live(action.row.path)
    if isinstance(action, (Withdraw, Restore)):
        return manifest.without_backup(action.entry)
    return manifest


def changed_note(action):
    if isinstance(action, Unlink):
        return f"skipped unlink {action.row.path}: the link no longer matches the recorded checkout"
    if isinstance(action, Withdraw):
        return f"skipped withdraw {action.entry.backup}: the backup no longer matches the recorded checkout"
    return f"skipped restore {action.entry.backup}: {action.entry.original} changed before uninstall"


def execute(scope, user, manifest, actions):
    state = state_dir(scope, user)
    manifest_file = state / V2_NAME
    save_manifest(manifest_file, manifest)
    legacy = state / LEGACY_NAME
    if legacy.exists():
        legacy.unlink()
    skipped = {"removed": 0, "restored": 0, "withdrawn": 0}
    stamp = None
    for action in actions:
        if isinstance(action, MoveAside):
            if stamp is None:
                (state / "backups").mkdir(parents=True, exist_ok=True)
                stamp = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%dT%H%M%S-"), dir=state / "backups"))
            name = os.path.basename(action.original)
            action = replace(action, backup=str(stamp / action.harnesses[0] / name))
        if isinstance(action, (Unlink, Withdraw, Restore)) and not still_valid(action):
            manifest = drop(manifest, action)
            save_manifest(manifest_file, manifest)
            print(changed_note(action))
            if isinstance(action, Restore):
                skipped["restored"] += 1
            else:
                skipped["removed"] += 1
                if isinstance(action, Withdraw):
                    skipped["withdrawn"] += 1
            continue
        if isinstance(action, SAVE_THEN_ACT):
            manifest = apply(manifest, action)
            save_manifest(manifest_file, manifest)
            if not isinstance(action, Record):
                act(action)
        else:
            act(action)
            manifest = apply(manifest, action)
            save_manifest(manifest_file, manifest)
    return skipped


def inside_checkout(path):
    """True when the path is, or resolves into, this checkout's skills tree."""
    real = Path(os.path.realpath(path))
    skills = Path(os.path.realpath(SKILLS))
    return real == skills or skills in real.parents


def describe(path, live):
    if live is not None:
        return f"installed by {live.checkout}"
    if os.path.islink(path):
        return f"link to {os.readlink(path)}"
    return "directory" if os.path.isdir(path) else "file"


def occupied_note(entry, path):
    if os.path.islink(path):
        spelled = lexical(os.readlink(path), path)
        parent = os.path.dirname(spelled)
        if os.path.basename(spelled) == os.path.basename(path) and os.path.basename(parent) == "skills":
            root = os.path.dirname(parent)
            if not os.path.exists(root):
                return (f"kept backup {entry.backup}: {path} is occupied by a link into deleted checkout {root}; "
                        "remove it and rerun uninstall")
    return f"kept backup {entry.backup}: {path} is occupied; clear it and rerun uninstall"


def plan_install(manifest, scope, user, selected, names):
    all_dirs = skill_dirs(scope, user)
    user_by_real = {}
    if not user:
        for directory in skill_dirs(None, True).values():
            user_by_real.setdefault(os.path.realpath(directory), directory)
    groups = {}
    for harness in HARNESSES:
        directory = all_dirs[harness]
        groups.setdefault(os.path.realpath(directory), []).append(harness)
    actions, conflicts = [], []
    untracked = 0
    chosen = set(selected)
    for real, members in groups.items():
        if not chosen.intersection(members):
            continue
        harnesses = tuple(members)
        label = next(harness for harness in harnesses if harness in chosen)
        directory = all_dirs[label]
        if real in user_by_real:
            print(f"{label}: {directory} already resolves to {user_by_real[real]}; nothing to link")
            continue
        if inside_checkout(directory):
            print(f"{label}: {directory} already resolves to {SKILLS}; nothing to link")
            continue
        for name in names:
            at = key(directory / name)
            row = LinkRow(at, harnesses, str(ROOT))
            live = manifest.live(at)
            if live is not None and live.checkout == str(ROOT):
                grown = union_harnesses(live.harnesses, harnesses)
                if set(grown) != set(live.harnesses):
                    actions.append(Record(replace(live, harnesses=grown)))
                continue
            text = link_text(at)
            # An exact link also resolves inside this checkout, so count it before that guard.
            if live is None and text is not None and lexical(text, at) == row.target:
                untracked += 1
                continue
            if inside_checkout(at):
                # Moving this aside would move pstack-t3's own skill directory.
                continue
            if os.path.lexists(at):
                conflicts.append((harnesses, at, live))
                actions.append(MoveAside(at, harnesses, live))
            actions.append(CreateLink(row))
    return actions, conflicts, untracked


def plan_uninstall(manifest, selected):
    actions, notes = [], []
    shared = set()
    removed = restored = withdrawn = 0
    chosen = set(selected)

    def selected_row(harnesses):
        have = set(harnesses)
        if have <= chosen:
            return True
        if have & chosen:
            shared.add(", ".join(sorted(have - chosen)))
        return False

    paths = list(dict.fromkeys([row.path for row in manifest.links] + [row.original for row in manifest.backups]))
    for path in paths:
        remaining = []
        for entry in manifest.backups:
            if entry.original != path:
                continue
            displaced = entry.displaced
            if displaced is not None and displaced.checkout == str(ROOT) and selected_row(displaced.harnesses):
                actions.append(Withdraw(entry))
                removed += 1
                withdrawn += 1
                continue
            if displaced is None or displaced.checkout != str(ROOT):
                selected_row(entry.harnesses)
            remaining.append(entry)
        live = manifest.live(path)
        uncovered = False
        if live is not None and live.checkout == str(ROOT):
            if selected_row(live.harnesses):
                actions.append(Unlink(live))
                removed += 1
                uncovered = True
                live = None
        elif live is not None:
            selected_row(live.harnesses)
        if live is not None or not remaining:
            continue
        top = remaining[-1]
        if not (uncovered or selected_row(top.harnesses)):
            continue
        if not uncovered and os.path.lexists(path):
            notes.append(occupied_note(top, path))
            continue
        actions.append(Restore(top))
        restored += 1
    return actions, removed, restored, withdrawn, notes, shared


def say_untracked(count):
    if count:
        print(f"{count} links already point at this checkout but are not tracked; uninstall leaves them")


def install(args):
    if not SKILLS.is_dir():
        sys.exit("skills/ is missing; run python3 scripts/build.py first")
    user = args.project is None
    scope = Path(args.project).resolve() if args.project else None
    names = sorted(p.name for p in SKILLS.iterdir() if (p / "SKILL.md").is_file())
    manifest = load(scope, user)
    actions, conflicts, untracked = plan_install(manifest, scope, user, args.harness, names)
    if conflicts and not args.replace:
        lines = [f"  {'/'.join(harnesses)}: {at} ({describe(at, live)})" for harnesses, at, live in conflicts]
        sys.exit("these skills already exist; rerun with --replace to move them aside (uninstall restores them):\n" + "\n".join(lines))
    links = [action for action in actions if isinstance(action, CreateLink)]
    if args.dry_run:
        replaced = {action.original for action in actions if isinstance(action, MoveAside)}
        for action in links:
            suffix = f" (replacing {describe(action.row.path, manifest.live(action.row.path))})" if action.row.path in replaced else ""
            print(f"would link {action.row.path} -> {action.row.target}{suffix}")
        say_untracked(untracked)
        print(f"{len(links)} links planned")
        return
    execute(scope, user, manifest, actions)
    joined = ", ".join(sorted({harness for action in links for harness in action.row.harnesses}))
    print(f"linked {len(links)} skills into {joined or 'nothing (already installed)'}")
    say_untracked(untracked)
    print(f"manifest: {state_dir(scope, user) / V2_NAME}")


def uninstall(args):
    user = args.project is None
    scope = Path(args.project).resolve() if args.project else None
    manifest = load(scope, user)
    actions, removed, restored, withdrawn, notes, shared = plan_uninstall(manifest, args.harness)
    for note in notes:
        print(note)
    if not args.dry_run:
        skipped = execute(scope, user, manifest, actions)
        removed -= skipped["removed"]
        restored -= skipped["restored"]
        withdrawn -= skipped["withdrawn"]
    summary = f"would remove {removed} links, would restore {restored} entries" if args.dry_run else f"removed {removed} links, restored {restored} entries"
    print(summary)
    if withdrawn:
        print(f"{withdrawn} of them had been moved aside by another checkout's --replace")
    for others in sorted(shared):
        print(f"kept entries whose directory is shared with {others}; select those harnesses too to remove them")
    return 0


def points_here(path):
    text = link_text(path)
    if text is None:
        return False
    at = key(path)
    return holds(LinkRow(at, (), str(ROOT)), text, at)


def doctor(args):
    user = args.project is None
    scope = Path(args.project).resolve() if args.project else None
    names = sorted(p.name for p in SKILLS.iterdir() if (p / "SKILL.md").is_file()) if SKILLS.is_dir() else []
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
        installed = [n for n in names if points_here(directory / n)]
        foreign = [n for n in names if (directory / n).exists() and not points_here(directory / n)]
        missing = [n for n in names if not (directory / n).exists()]
        healthy &= not foreign and not missing
        print(f"{harness:7} {directory}: {len(installed)}/{len(names)} pstack-t3" +
              (f", {len(foreign)} taken by other copies ({', '.join(foreign[:5])}{'...' if len(foreign) > 5 else ''})" if foreign else "") +
              (f", {len(missing)} missing" if missing else ""))
        for extra in extra_dirs(scope, user)[harness]:
            stale = [n for n in names if (extra / n).exists() and not points_here(extra / n)]
            if stale:
                healthy = False
                print(f"        {extra} also holds other copies of {len(stale)} of these "
                      f"({', '.join(stale[:5])}{'...' if len(stale) > 5 else ''}); {harness} may load those instead")
        if not user:
            # Claude and Grok load the user copy when both scopes define a name.
            user_directory = skill_dirs(None, True)[harness]
            shadowing = [n for n in installed if (user_directory / n).exists() and not points_here(user_directory / n)]
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
    args.harness = HARNESSES if args.harness == "all" else tuple(h.strip() for h in args.harness.split(","))
    unknown = set(args.harness) - set(HARNESSES)
    if unknown:
        parser.error(f"unknown harness {', '.join(sorted(unknown))}")
    return {"install": install, "uninstall": uninstall, "doctor": doctor}[args.command](args) or 0


if __name__ == "__main__":
    sys.exit(main())
