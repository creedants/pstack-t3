#!/usr/bin/env python3
"""Install pstack-t3 skills into every provider skill directory T3 reads.

T3's `$` picker lists each provider's native skills, so pstack-t3 links its
generated skills into each provider's directory. Every link and every entry
moved aside is recorded in a manifest so `uninstall` restores the prior state.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
HARNESSES = ("claude", "codex", "grok", "cursor")


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


def ours(path):
    """A link whose target is a skill directory in this checkout. Never follows loops."""
    if not path.is_symlink():
        return False
    target = Path(os.path.normpath(os.path.join(path.parent, os.readlink(path))))
    return target.parent in (SKILLS, Path(os.path.realpath(SKILLS)))


def inside_checkout(path):
    """True when the path is, or resolves into, this checkout's skills tree."""
    real = Path(os.path.realpath(path))
    skills = Path(os.path.realpath(SKILLS))
    return real == skills or skills in real.parents


def describe(path):
    if path.is_symlink():
        return f"link to {os.readlink(path)}"
    return "directory" if path.is_dir() else "file"


def load_manifest(file):
    if file.exists():
        return json.loads(file.read_text())
    return {"links": [], "backups": []}


def save_manifest(file, manifest):
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n")
    os.replace(temporary, file)


def plan(targets, names):
    """Return (actions, conflicts). Directories sharing a real path are visited once,
    and each action names every harness that shares it."""
    actions, conflicts, seen = [], [], {}
    for harness, directory in targets.items():
        real = Path(os.path.realpath(directory))
        if real in seen:
            seen[real].append(harness)
            continue
        seen[real] = [harness]
        if inside_checkout(directory):
            print(f"{harness}: {directory} already resolves to {SKILLS}; nothing to link")
            continue
        for name in names:
            link = directory / name
            if ours(link):
                continue
            if inside_checkout(link):
                # Moving this aside would move pstack-t3's own skill directory.
                continue
            if link.exists() or link.is_symlink():
                conflicts.append((seen[real], link))
            actions.append((seen[real], link))
    return actions, conflicts


def install(args):
    if not SKILLS.is_dir():
        sys.exit("skills/ is missing; run python3 scripts/build.py first")
    user = args.project is None
    scope = Path(args.project).resolve() if args.project else None
    targets = {h: d for h, d in skill_dirs(scope, user).items() if h in args.harness}
    names = sorted(p.name for p in SKILLS.iterdir() if (p / "SKILL.md").is_file())
    actions, conflicts = plan(targets, names)
    if conflicts and not args.replace:
        lines = [f"  {'/'.join(harnesses)}: {link} ({describe(link)})" for harnesses, link in conflicts]
        sys.exit("these skills already exist; rerun with --replace to move them aside (uninstall restores them):\n" + "\n".join(lines))
    if args.dry_run:
        for harnesses, link in actions:
            print(f"would link {link} -> {SKILLS / link.name}" + (f" (replacing {describe(link)})" if (harnesses, link) in conflicts else ""))
        print(f"{len(actions)} links planned")
        return
    state = state_dir(scope, user)
    manifest_file = state / "install-manifest.json"
    manifest = load_manifest(manifest_file)
    # Create the stamp directory on the first skill that has to be moved aside.
    backup_root = None
    for harnesses, link in actions:
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.exists() or link.is_symlink():
            if backup_root is None:
                (state / "backups").mkdir(parents=True, exist_ok=True)
                backup_root = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%dT%H%M%S-"), dir=state / "backups"))
            backup = backup_root / harnesses[0] / link.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            # Record before moving, so a crash mid-move still leaves a restorable entry.
            manifest["backups"].append({"harnesses": harnesses, "original": str(link), "backup": str(backup)})
            save_manifest(manifest_file, manifest)
            shutil.move(str(link), str(backup))
        link.symlink_to(SKILLS / link.name, target_is_directory=True)
        manifest["links"].append({"harnesses": harnesses, "path": str(link)})
        save_manifest(manifest_file, manifest)
    print(f"linked {len(actions)} skills into {', '.join(sorted(set(h for hs, _ in actions for h in hs))) or 'nothing (already installed)'}")
    print(f"manifest: {manifest_file}")


def entry_harnesses(entry):
    """Harnesses that share an entry's directory. Unknown means all of them."""
    if not isinstance(entry, dict):
        return list(HARNESSES)
    if "harnesses" in entry:
        return entry["harnesses"]
    return [entry["harness"]] if "harness" in entry else list(HARNESSES)


def entry_path(entry):
    return Path(entry["path"] if isinstance(entry, dict) else entry)


def uninstall(args):
    user = args.project is None
    scope = Path(args.project).resolve() if args.project else None
    manifest_file = state_dir(scope, user) / "install-manifest.json"
    manifest = load_manifest(manifest_file)
    shared = set()

    def selected(entry):
        # A directory shared by several harnesses goes only when all of them are selected.
        harnesses = entry_harnesses(entry)
        if set(harnesses) <= set(args.harness):
            return True
        if set(harnesses) & set(args.harness):
            shared.add(", ".join(sorted(set(harnesses) - set(args.harness))))
        return False
    removed, kept_links = 0, []
    for entry in manifest["links"]:
        link = entry_path(entry)
        if not selected(entry):
            kept_links.append(entry)
        elif ours(link):
            if not args.dry_run:
                link.unlink()
            removed += 1
    restored, kept_backups = 0, []
    for entry in reversed(manifest["backups"]):
        original, backup = Path(entry["original"]), Path(entry["backup"])
        present = backup.exists() or backup.is_symlink()
        if not selected(entry) or not present:
            if present:
                kept_backups.append(entry)
            continue
        occupied = original.exists() or original.is_symlink()
        # In a dry run our own links are still in place but would be removed first.
        if occupied and not (args.dry_run and ours(original)):
            print(f"kept backup {backup}: {original} is occupied; clear it and rerun uninstall")
            kept_backups.append(entry)
            continue
        if not args.dry_run:
            shutil.move(str(backup), str(original))
        restored += 1
    print(f"would remove {removed} links, would restore {restored} entries" if args.dry_run else f"removed {removed} links, restored {restored} entries")
    for others in sorted(shared):
        print(f"kept entries whose directory is shared with {others}; select those harnesses too to remove them")
    if args.dry_run:
        return 0
    if kept_links or kept_backups:
        save_manifest(manifest_file, {"links": kept_links, "backups": list(reversed(kept_backups))})
    elif manifest_file.exists():
        manifest_file.unlink()
    return 0


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
        installed = [n for n in names if ours(directory / n)]
        foreign = [n for n in names if (directory / n).exists() and not ours(directory / n)]
        missing = [n for n in names if not (directory / n).exists()]
        healthy &= not foreign and not missing
        print(f"{harness:7} {directory}: {len(installed)}/{len(names)} pstack-t3" +
              (f", {len(foreign)} taken by other copies ({', '.join(foreign[:5])}{'...' if len(foreign) > 5 else ''})" if foreign else "") +
              (f", {len(missing)} missing" if missing else ""))
        for extra in extra_dirs(scope, user)[harness]:
            stale = [n for n in names if (extra / n).exists() and not ours(extra / n)]
            if stale:
                healthy = False
                print(f"        {extra} also holds other copies of {len(stale)} of these "
                      f"({', '.join(stale[:5])}{'...' if len(stale) > 5 else ''}); {harness} may load those instead")
        if not user:
            # Claude and Grok load the user copy when both scopes define a name.
            user_directory = skill_dirs(None, True)[harness]
            shadowing = [n for n in installed if (user_directory / n).exists() and not ours(user_directory / n)]
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
