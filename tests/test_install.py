"""Installer behavior a fresh home can observe."""

import errno
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("alpha", "pstack-runtime", "swarm")
OLD_SHA = "3936df1838da72e64c9870c6c73262929c27f828385be625286018a80039a33d"
KEPT = "kept {n} records whose links no longer point at this checkout; they apply again if the links come back"
ADOPTED = "adopted {n} links an older installer recorded"
UNTRACKED = "links already point at this checkout but are not tracked; uninstall leaves them"

OLD_INSTALLER = r'''#!/usr/bin/env python3
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
'''


def run_install(home, *args):
    return _run(home, ROOT, args)


def _env(home):
    env = os.environ.copy()
    env.pop("XDG_CONFIG_HOME", None)
    env.pop("CLAUDE_CONFIG_DIR", None)
    env["HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(home, checkout, args):
    return subprocess.run(
        [sys.executable, str(Path(checkout) / "scripts" / "install.py"), *args],
        env=_env(home),
        cwd=home,
        capture_output=True,
        text=True,
    )


class FreshInstallTest(unittest.TestCase):
    def test_fresh_install_links_every_skill_and_rerun_adds_no_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            names = sorted(p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").is_file())
            first = run_install(home)
            self.assertEqual(first.returncode, 0, first.stderr)
            for harness in (".claude", ".agents", ".grok", ".cursor"):
                for name in names:
                    link = home / harness / "skills" / name
                    self.assertTrue(link.is_symlink(), link)
                    self.assertEqual(link.resolve(), (ROOT / "skills" / name).resolve(), link)
                    self.assertEqual((link / "SKILL.md").read_bytes(), (ROOT / "skills" / name / "SKILL.md").read_bytes())
            backups = home / ".config/pstack-t3/backups"
            self.assertFalse(backups.exists(), "a fresh install created a backup directory")
            again = run_install(home)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("already installed", again.stdout)
            self.assertFalse(backups.exists(), "rerunning install created a backup directory")

    def test_replace_saves_the_old_skill_and_rerun_adds_no_empty_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            swarm = home / ".grok/skills/swarm"
            swarm.mkdir(parents=True)
            (swarm / "SKILL.md").write_text("foreign-skill\n")
            refused = run_install(home, "--harness", "grok")
            self.assertNotEqual(refused.returncode, 0)
            self.assertFalse((home / ".grok/skills/poteto-mode").exists())
            replaced = run_install(home, "--harness", "grok", "--replace")
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
            saved = list((home / ".config/pstack-t3/backups").rglob("SKILL.md"))
            self.assertEqual([path.read_text() for path in saved], ["foreign-skill\n"])
            stamps = sorted(p.name for p in (home / ".config/pstack-t3/backups").iterdir() if p.is_dir())
            self.assertEqual(len(stamps), 1)
            again = run_install(home, "--harness", "grok")
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("already installed", again.stdout)
            stamps_after = sorted(p.name for p in (home / ".config/pstack-t3/backups").iterdir() if p.is_dir())
            self.assertEqual(stamps_after, stamps)
            removed = run_install(home, "--harness", "grok", "uninstall")
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertEqual((swarm / "SKILL.md").read_text(), "foreign-skill\n")
            self.assertFalse((home / ".grok/skills/poteto-mode").exists())


def fresh_home():
    wrapper = tempfile.TemporaryDirectory()
    return wrapper, Path(os.path.realpath(wrapper.name))


def provider_link(home, harness, name):
    folder = {"claude": ".claude", "codex": ".agents", "grok": ".grok", "cursor": ".cursor"}[harness]
    return home / folder / "skills" / name


def state_dir(home):
    return home / ".config" / "pstack-t3"


def legacy_file(home):
    return state_dir(home) / "install-manifest.json"


def owner_file(home, checkout):
    digest = hashlib.sha256(str(checkout).encode()).hexdigest()[:16]
    return state_dir(home) / "install-owners" / f"{digest}.json"


def make_checkout(home, name, old=False):
    checkout = home / "checkouts" / name
    (checkout / "scripts").mkdir(parents=True)
    target = checkout / "scripts" / "install.py"
    if old:
        target.write_bytes(OLD_INSTALLER.encode())
    else:
        shutil.copy(ROOT / "scripts" / "install.py", target)
    for skill in NAMES:
        directory = checkout / "skills" / skill
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"{name}-{skill}\n")
    return checkout


def upgrade(checkout):
    shutil.copy(ROOT / "scripts" / "install.py", checkout / "scripts" / "install.py")


def run(home, checkout, *args):
    return _run(home, checkout, args)


def snapshot(root):
    records = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted(dirnames):
            path = Path(dirpath) / name
            rel = str(path.relative_to(root))
            if path.is_symlink():
                records.append((rel, "link", os.readlink(path)))
            else:
                records.append((rel, "dir", None))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = str(path.relative_to(root))
            if path.is_symlink():
                records.append((rel, "link", os.readlink(path)))
            elif path.is_file():
                records.append((rel, "file", path.read_bytes()))
    return tuple(records)


def read_legacy(home):
    return json.loads(legacy_file(home).read_text())


def read_owner(home, checkout):
    file = owner_file(home, checkout)
    if not file.exists():
        return None
    return json.loads(file.read_text())


def grok_paths(home):
    return [str(provider_link(home, "grok", name)) for name in NAMES]


def plant_links(home, checkout, harness="grok"):
    for name in NAMES:
        link = provider_link(home, harness, name)
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        os.symlink(checkout / "skills" / name, link)


def write_owner(home, checkout, paths, harnesses=("grok",)):
    file = owner_file(home, checkout)
    file.parent.mkdir(parents=True, exist_ok=True)
    links = {path: {"harnesses": list(harnesses)} for path in paths}
    file.write_text(json.dumps({"checkout": str(checkout), "links": links}, indent=2) + "\n")


def write_legacy(home, links, backups):
    file = legacy_file(home)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({"links": links, "backups": backups}, indent=2) + "\n")


def pause_after_load(home, checkout, args):
    ready = home / f"ready-{checkout.name}"
    go = home / f"go-{checkout.name}"
    wrapper = home / f"pause-{checkout.name}.py"
    installer = str(checkout / "scripts" / "install.py")
    wrapper.write_text(
        "import importlib.util, sys, time\n"
        "from pathlib import Path\n"
        f"spec = importlib.util.spec_from_file_location('installer', {installer!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "original = module.load\n"
        "def paused(*args):\n"
        "    result = original(*args)\n"
        f"    Path({str(ready)!r}).touch()\n"
        "    deadline = time.monotonic() + 30\n"
        f"    while not Path({str(go)!r}).exists():\n"
        "        if time.monotonic() > deadline:\n"
        "            raise RuntimeError('barrier timeout')\n"
        "        time.sleep(0.01)\n"
        "    return result\n"
        "module.load = paused\n"
        "sys.exit(module.main())\n"
    )
    proc = subprocess.Popen(
        [sys.executable, str(wrapper), *args],
        env=_env(home),
        cwd=home,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc, ready, go


def squat_uninstall(home, checkout, path, payload, *args):
    wrapper = home / f"squat-{checkout.name}.py"
    installer = str(checkout / "scripts" / "install.py")
    wrapper.write_text(
        "import importlib.util, sys, os\n"
        "from pathlib import Path\n"
        f"spec = importlib.util.spec_from_file_location('installer', {installer!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "changed = False\n"
        "def after(original):\n"
        "    def race(target, *args, **kwargs):\n"
        "        global changed\n"
        "        result = original(target, *args, **kwargs)\n"
        f"        if str(target) == {str(path)!r} and not changed:\n"
        "            changed = True\n"
        f"            Path(target).write_bytes({payload!r})\n"
        "        return result\n"
        "    return race\n"
        "os.rename = after(os.rename)\n"
        "os.unlink = after(os.unlink)\n"
        "sys.exit(module.main())\n"
    )
    return subprocess.run(
        [sys.executable, str(wrapper), *args],
        env=_env(home),
        cwd=home,
        capture_output=True,
        text=True,
    )


def run_failing(home, checkout, call, path, *args):
    module, name = call.split(".")
    wrapper = home / f"fail-{checkout.name}.py"
    installer = str(checkout / "scripts" / "install.py")
    wrapper.write_text(
        "import errno, importlib.util, os, shutil, sys\n"
        f"spec = importlib.util.spec_from_file_location('installer', {installer!r})\n"
        "installer = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = installer\n"
        "spec.loader.exec_module(installer)\n"
        f"original = getattr({module}, {name!r})\n"
        "def failing(*args, **kwargs):\n"
        f"    if {str(path)!r} in [str(arg) for arg in args]:\n"
        f"        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), {str(path)!r})\n"
        "    return original(*args, **kwargs)\n"
        f"setattr({module}, {name!r}, failing)\n"
        "sys.exit(installer.main())\n"
    )
    return subprocess.run(
        [sys.executable, str(wrapper), *args],
        env=_env(home),
        cwd=home,
        capture_output=True,
        text=True,
    )


def interleave_removal(home, checkout, paths, call, *args, occupy=None):
    """Run `call` once, just before this installer first moves or deletes any of `paths`, then write `occupy` there."""
    wrapper = home / f"interleave-{checkout.name}.py"
    installer = str(checkout / "scripts" / "install.py")
    wrapper.write_text(
        "import importlib.util, os, subprocess, sys\n"
        f"spec = importlib.util.spec_from_file_location('installer', {installer!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "fired = False\n"
        "def before(original):\n"
        "    def hooked(target, *args, **kwargs):\n"
        "        global fired\n"
        f"        if not fired and str(target) in {[str(path) for path in paths]!r}:\n"
        "            fired = True\n"
        f"            child = subprocess.run({[str(part) for part in call]!r}, env=os.environ, capture_output=True, text=True)\n"
        "            assert child.returncode == 0, child.stdout + child.stderr\n"
        "            result = original(target, *args, **kwargs)\n"
        f"            if {occupy!r} is not None:\n"
        f"                open(target, 'wb').write({occupy!r})\n"
        "            return result\n"
        "        return original(target, *args, **kwargs)\n"
        "    return hooked\n"
        "os.rename = before(os.rename)\n"
        "os.unlink = before(os.unlink)\n"
        "status = module.main()\n"
        "assert fired, 'the interleaved call never ran'\n"
        "sys.exit(status)\n"
    )
    return subprocess.run(
        [sys.executable, str(wrapper), *args],
        env=_env(home),
        cwd=home,
        capture_output=True,
        text=True,
    )


def set_aside(path):
    """Entries an uninstall kept aside for `path`, each in its own hidden directory beside it."""
    return sorted(path.parent.glob(f".pstack-t3-*/{path.name}"))


def uninstall_hooked(home, checkout, code):
    """Run this checkout's uninstall with `code` run first against the loaded installer, named `module`."""
    wrapper = home / f"hooked-{checkout.name}.py"
    installer = str(checkout / "scripts" / "install.py")
    wrapper.write_text(
        "import importlib.util, os, sys\n"
        "from pathlib import Path\n"
        f"spec = importlib.util.spec_from_file_location('installer', {installer!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        f"{code}\n"
        "sys.exit(module.main())\n"
    )
    return subprocess.run(
        [sys.executable, str(wrapper), "--harness", "grok", "uninstall"],
        env=_env(home),
        cwd=home,
        capture_output=True,
        text=True,
    )


def put_back_race(path, swap, occupy=None, link_error=False, no_primitive=False):
    """Hook code that swaps `path` for a foreign `swap` entry just before the move aside.

    `occupy` arrives at `path` when the put-back calls a rename onto it, after any check for vacancy.
    """
    return (
        "import ctypes, errno\n"
        f"target = {str(path)!r}\n"
        "moved = False\n"
        "def hooked(original):\n"
        "    def call(source, destination, *args, **kwargs):\n"
        "        global moved\n"
        "        if not moved and str(source) == target:\n"
        "            moved = True\n"
        "            os.unlink(target)\n"
        f"            if {swap!r} == 'directory':\n"
        "                os.mkdir(target)\n"
        "                Path(target, 'precious').write_bytes(b'directory bytes\\x00')\n"
        "            else:\n"
        "                os.symlink('/foreign/swarm', target)\n"
        "        elif moved and str(destination) == target:\n"
        f"            if {occupy!r} == 'directory':\n"
        "                os.mkdir(target)\n"
        f"            elif {occupy!r} == 'file':\n"
        "                Path(target).write_bytes(b'occupant\\x00\\xff')\n"
        "        return original(source, destination, *args, **kwargs)\n"
        "    return call\n"
        "os.rename = hooked(os.rename)\n"
        "if hasattr(module, 'rename_noreplace'):\n"
        "    module.rename_noreplace = hooked(module.rename_noreplace)\n"
        f"if {link_error!r}:\n"
        "    def denied(*args, **kwargs):\n"
        "        raise OSError(errno.EPERM, 'hard links prohibited')\n"
        "    os.link = denied\n"
        f"if {no_primitive!r}:\n"
        "    def missing(*args, **kwargs):\n"
        "        raise OSError('no libc')\n"
        "    ctypes.CDLL = missing\n"
    )


class OwnershipTest(unittest.TestCase):
    def setUp(self):
        self.use_fresh()

    def use_fresh(self):
        wrapper, self.home = fresh_home()
        self.addCleanup(wrapper.cleanup)

    def ok(self, result, *lines):
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        got = result.stdout.splitlines()
        for line in lines:
            self.assertIn(line, got)

    def assert_empty_records(self):
        self.assertTrue(legacy_file(self.home).is_file())
        data = read_legacy(self.home)
        self.assertEqual(data["links"], [])
        self.assertEqual(data["backups"], [])
        owners = state_dir(self.home) / "install-owners"
        found = list(owners.glob("*.json")) if owners.is_dir() else []
        self.assertEqual(found, [])

    def assert_claims(self, checkout, paths):
        data = read_owner(self.home, checkout)
        self.assertIsNotNone(data, checkout)
        self.assertEqual(data["checkout"], str(checkout))
        self.assertEqual(set(data["links"]), set(paths))

    def assert_grok_text(self, checkout, names=NAMES):
        for name in names:
            link = provider_link(self.home, "grok", name)
            self.assertEqual(os.readlink(link), str(checkout / "skills" / name), name)
            self.assertEqual((link / "SKILL.md").read_bytes(), (checkout / "skills" / name / "SKILL.md").read_bytes())

    def assert_gone(self, names=NAMES, harness="grok"):
        for name in names:
            self.assertFalse(os.path.lexists(provider_link(self.home, harness, name)))

    def two(self):
        return make_checkout(self.home, "a"), make_checkout(self.home, "b")

    def base(self):
        a, b = self.two()
        self.ok(run(self.home, a, "--harness", "grok"))
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        return a, b

    def test_old_installer_matches_397d163(self):
        self.assertEqual(hashlib.sha256(OLD_INSTALLER.encode()).hexdigest(), OLD_SHA)

    def test_row_1_replaced_owner_stays_tracked(self):
        a, b = self.base()
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_claims(a, grok_paths(self.home))
        self.assert_grok_text(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assert_empty_records()

    def test_row_1b_owner_withdraws_links_another_checkout_moved_aside(self):
        a, b = self.base()
        self.ok(
            run(self.home, a, "--harness", "grok", "uninstall"),
            "removed 3 links, restored 0 entries",
            "3 of them had been moved aside by another checkout's --replace",
        )
        self.assert_grok_text(b)
        self.assert_claims(b, grok_paths(self.home))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assert_empty_records()

    def repoint_swarm(self, target):
        link = provider_link(self.home, "grok", "swarm")
        link.unlink()
        os.symlink(target, link)
        return str(target)

    def assert_repoint_survives(self, a, text):
        link = provider_link(self.home, "grok", "swarm")
        self.ok(
            run(self.home, a, "--harness", "grok", "uninstall"),
            "removed 2 links, restored 0 entries",
            KEPT.format(n=1),
        )
        self.assertEqual(os.readlink(link), text)
        self.assert_claims(a, [str(link)])
        before = snapshot(self.home)
        self.ok(
            run(self.home, a, "--harness", "grok", "uninstall"),
            "removed 0 links, restored 0 entries",
            KEPT.format(n=1),
        )
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(os.readlink(link), text)

    def test_row_2_repointed_link_to_an_existing_directory_is_left_alone(self):
        a, b = self.base()
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        target = self.home / "unrelated-dir"
        target.mkdir()
        text = self.repoint_swarm(target)
        self.assert_repoint_survives(a, text)
        link = provider_link(self.home, "grok", "swarm")
        link.unlink()
        os.symlink(a / "skills" / "swarm", link)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 1 links, restored 0 entries")
        self.assertFalse(os.path.lexists(link))
        self.assert_empty_records()

    def test_row_3_repointed_dangling_link_is_left_alone(self):
        a, b = self.base()
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        text = self.repoint_swarm(self.home / "missing-target")
        self.assert_repoint_survives(a, text)

    def foreign_then_both_uninstall(self, target):
        a, b = self.two()
        link = provider_link(self.home, "grok", "swarm")
        link.parent.mkdir(parents=True)
        os.symlink(target, link)
        text = str(target)
        self.ok(run(self.home, a, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
        self.assertEqual(os.readlink(link), text)
        self.assertEqual((link / "SKILL.md").read_bytes(), (target / "SKILL.md").read_bytes() if (target / "SKILL.md").exists() else (link / "SKILL.md").read_bytes())
        for name in ("alpha", "pstack-runtime"):
            self.assertFalse(os.path.lexists(provider_link(self.home, "grok", name)))
        self.assert_empty_records()
        before = snapshot(self.home)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assertEqual(snapshot(self.home), before)

    def test_row_4_foreign_symlink_comes_back_untracked(self):
        target = self.home / "foreign-target"
        target.mkdir()
        (target / "SKILL.md").write_text("foreign-skill\n")
        self.foreign_then_both_uninstall(target)
        self.assertEqual((provider_link(self.home, "grok", "swarm") / "SKILL.md").read_text(), "foreign-skill\n")

    def test_row_5_repointed_lookalike_link_is_left_alone(self):
        a, b = self.base()
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        lookalike = self.home / "lookalike"
        (lookalike / "scripts").mkdir(parents=True)
        (lookalike / "scripts" / "install.py").write_text("print('unrelated installer')\n")
        (lookalike / "skills" / "swarm").mkdir(parents=True)
        (lookalike / "skills" / "swarm" / "SKILL.md").write_text("lookalike\n")
        text = self.repoint_swarm(lookalike / "skills" / "swarm")
        self.assert_repoint_survives(a, text)
        self.assertEqual((lookalike / "scripts" / "install.py").read_text(), "print('unrelated installer')\n")
        self.assertEqual((lookalike / "skills" / "swarm" / "SKILL.md").read_text(), "lookalike\n")

    def test_row_6_lookalike_symlink_comes_back_untracked(self):
        lookalike = self.home / "lookalike"
        (lookalike / "scripts").mkdir(parents=True)
        (lookalike / "scripts" / "install.py").write_text("print('unrelated installer')\n")
        (lookalike / "skills" / "swarm").mkdir(parents=True)
        (lookalike / "skills" / "swarm" / "SKILL.md").write_text("lookalike\n")
        self.foreign_then_both_uninstall(lookalike / "skills" / "swarm")
        self.assertEqual((lookalike / "scripts" / "install.py").read_text(), "print('unrelated installer')\n")
        self.assertEqual((provider_link(self.home, "grok", "swarm") / "SKILL.md").read_text(), "lookalike\n")

    def alias_of(self, checkout):
        alias = self.home / "a-alias"
        os.symlink(checkout, alias)
        return alias

    def test_row_7_alias_spelled_link_stays_untracked(self):
        a, b = self.two()
        alias = self.alias_of(a)
        self.ok(run(self.home, a, "--harness", "grok"))
        link = provider_link(self.home, "grok", "swarm")
        link.unlink()
        text = str(alias / "skills" / "swarm")
        os.symlink(text, link)
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.ok(
            run(self.home, alias, "--harness", "grok", "uninstall"),
            "removed 2 links, restored 0 entries",
            KEPT.format(n=1),
        )
        self.assertEqual(os.readlink(link), text)
        self.assertEqual((link / "SKILL.md").read_bytes(), (a / "skills" / "swarm" / "SKILL.md").read_bytes())
        self.assert_claims(a, [str(link)])
        before = snapshot(self.home)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(os.readlink(link), text)

    def test_row_7u_legacy_rows_do_not_adopt_an_alias_spelling(self):
        a, b = self.two()
        alias = self.alias_of(a)
        self.ok(run(self.home, a, "--harness", "grok"))
        paths = grok_paths(self.home)
        owner = owner_file(self.home, a)
        if owner.exists():
            owner.unlink()
        write_legacy(self.home, [{"harnesses": ["grok"], "path": path} for path in paths], [])
        link = provider_link(self.home, "grok", "swarm")
        link.unlink()
        text = str(alias / "skills" / "swarm")
        os.symlink(text, link)
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.ok(run(self.home, alias, "--harness", "grok", "uninstall"), "removed 2 links, restored 0 entries")
        self.assertEqual(os.readlink(link), text)
        self.assertEqual((link / "SKILL.md").read_bytes(), (a / "skills" / "swarm" / "SKILL.md").read_bytes())
        left = read_legacy(self.home)["links"]
        self.assertEqual(left, [{"harnesses": ["grok"], "path": str(link)}])
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assertEqual(os.readlink(link), text)
        self.assertEqual(read_legacy(self.home)["links"], [{"harnesses": ["grok"], "path": str(link)}])

    def test_row_8_relative_link_text_is_restored_exactly(self):
        a, b = self.two()
        self.ok(run(self.home, a, "--harness", "grok"))
        expected = {}
        for name in NAMES:
            link = provider_link(self.home, "grok", name)
            relative = os.path.relpath(os.readlink(link), link.parent)
            link.unlink()
            os.symlink(relative, link)
            expected[name] = relative
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        for name in NAMES:
            link = provider_link(self.home, "grok", name)
            self.assertEqual(os.readlink(link), expected[name])
            self.assertEqual((link / "SKILL.md").read_bytes(), (a / "skills" / name / "SKILL.md").read_bytes())
        self.assert_claims(a, grok_paths(self.home))
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assert_empty_records()

    def test_row_9_alias_invocation_records_the_canonical_checkout(self):
        a, b = self.two()
        alias = self.alias_of(a)
        self.ok(run(self.home, alias, "--harness", "grok"))
        self.assert_grok_text(a)
        self.assert_claims(a, grok_paths(self.home))
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_claims(a, grok_paths(self.home))
        self.ok(run(self.home, alias, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assert_empty_records()

    def share_skills(self, source, dest):
        shutil.rmtree(dest / "skills")
        os.symlink(source / "skills", dest / "skills")

    def assert_shared_tree(self, installer, other):
        self.ok(run(self.home, installer, "--harness", "grok"))
        self.ok(run(self.home, other, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assert_grok_text(installer)
        self.assert_claims(installer, grok_paths(self.home))
        self.ok(run(self.home, installer, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assert_empty_records()

    def test_row_10_forward_shared_skills_tree(self):
        a, b = self.two()
        self.share_skills(a, b)
        self.assert_shared_tree(b, a)

    def test_row_10_reverse_shared_skills_tree(self):
        a, b = self.two()
        self.share_skills(b, a)
        self.assert_shared_tree(b, a)

    def test_row_11a_deleted_checkout_links_stay_and_the_other_owner_remains(self):
        a, b = self.two()
        later = make_checkout(self.home, "l")
        self.ok(run(self.home, later, "--harness", "codex"))
        self.ok(run(self.home, a, "--harness", "grok"))
        texts = {name: os.readlink(provider_link(self.home, "grok", name)) for name in NAMES}
        shutil.rmtree(a)
        self.ok(run(self.home, b, "uninstall"), "removed 0 links, restored 0 entries")
        for name in NAMES:
            link = provider_link(self.home, "grok", name)
            self.assertTrue(os.path.lexists(link))
            self.assertFalse(os.path.exists(link))
            self.assertEqual(os.readlink(link), texts[name])
        self.assert_claims(a, grok_paths(self.home))
        codex_paths = [str(provider_link(self.home, "codex", name)) for name in NAMES]
        self.assert_claims(later, codex_paths)
        self.ok(run(self.home, later, "--harness", "codex", "uninstall"), "removed 3 links, restored 0 entries")
        for name in NAMES:
            self.assertEqual(os.readlink(provider_link(self.home, "grok", name)), texts[name])
        self.assert_claims(a, grok_paths(self.home))
        tagged = [row for row in read_legacy(self.home)["links"] if row.get("checkout") == str(a)]
        self.assertEqual({row["path"] for row in tagged}, set(grok_paths(self.home)))
        self.assertIsNone(read_owner(self.home, later))

    def test_row_11b_deleted_owner_is_restored_untracked_beside_a_live_owner(self):
        a, b = self.two()
        later = make_checkout(self.home, "l")
        self.ok(run(self.home, a, "--harness", "grok"))
        texts = {name: os.readlink(provider_link(self.home, "grok", name)) for name in NAMES}
        self.ok(run(self.home, later, "--harness", "codex"))
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        shutil.rmtree(a)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        for name in NAMES:
            link = provider_link(self.home, "grok", name)
            self.assertTrue(os.path.lexists(link))
            self.assertFalse(os.path.exists(link))
            self.assertEqual(os.readlink(link), texts[name])
        self.assert_claims(a, grok_paths(self.home))
        self.assert_claims(later, [str(provider_link(self.home, "codex", name)) for name in NAMES])
        self.ok(run(self.home, later, "--harness", "codex", "uninstall"), "removed 3 links, restored 0 entries")
        for name in NAMES:
            self.assertEqual(os.readlink(provider_link(self.home, "grok", name)), texts[name])
        self.assert_claims(a, grok_paths(self.home))
        self.assert_gone(harness="codex")

    def test_row_12a_dry_run_matches_a_covered_foreign_file(self):
        a, b = self.two()
        self.share_skills(a, b)
        swarm = provider_link(self.home, "grok", "swarm")
        swarm.parent.mkdir(parents=True)
        swarm.write_bytes(b"foreign\x00file\n")
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        before = snapshot(self.home)
        dry = run(self.home, a, "--harness", "grok", "uninstall", "--dry-run")
        self.ok(dry, "would remove 0 links, would restore 0 entries")
        self.assertIn(f"{swarm} is occupied; clear it and rerun uninstall", dry.stdout)
        self.assertEqual(snapshot(self.home), before)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assertEqual(snapshot(self.home), before)
        self.assert_grok_text(b)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
        self.assertEqual(swarm.read_bytes(), b"foreign\x00file\n")
        self.assertFalse(swarm.is_symlink())
        self.assert_empty_records()

    def test_row_12b_dry_run_counts_each_path_once(self):
        a, b = self.base()
        before = snapshot(self.home)
        self.ok(
            run(self.home, b, "--harness", "grok", "uninstall", "--dry-run"),
            "would remove 3 links, would restore 3 entries",
        )
        self.assertEqual(snapshot(self.home), before)
        self.assert_grok_text(b)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_grok_text(a)
        self.assert_claims(a, grok_paths(self.home))

    def test_row_13_three_replace_cycles_keep_one_row_per_path(self):
        a, b = self.two()
        self.ok(run(self.home, a, "--harness", "grok"))
        paths = grok_paths(self.home)
        for _ in range(3):
            self.ok(run(self.home, b, "--harness", "grok", "--replace"))
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
            self.assert_claims(a, paths)
            tagged = [row for row in read_legacy(self.home)["links"] if row.get("checkout") == str(a)]
            self.assertEqual(len(tagged), 3)
            self.assertEqual({row["path"] for row in tagged}, set(paths))
            self.assertIsNone(read_owner(self.home, b))
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assert_empty_records()

    def legacy_links(self, shape, paths):
        links = []
        for path in paths:
            if shape == "harnesses":
                row = {"harnesses": ["grok"], "path": path}
            elif shape == "harness":
                row = {"harness": "grok", "path": path}
            else:
                row = path
            links.append(row)
            links.append(json.loads(json.dumps(row)))
        return links

    def plant_legacy(self, shape):
        a = make_checkout(self.home, "a")
        self.ok(run(self.home, a, "--harness", "grok"))
        paths = grok_paths(self.home)
        owner = owner_file(self.home, a)
        if owner.exists():
            owner.unlink()
        write_legacy(self.home, self.legacy_links(shape, paths), [])
        return a, paths

    def legacy_rows(self):
        return read_legacy(self.home)["links"]

    def legacy_shape(self, shape):
        a, paths = self.plant_legacy(shape)
        planted = self.legacy_rows()
        adopted = run(self.home, a, "--harness", "grok")
        self.ok(adopted, "linked 0 skills into nothing (already installed)", ADOPTED.format(n=3))
        self.assertNotIn(UNTRACKED, adopted.stdout)
        self.assertEqual(self.legacy_rows(), planted)
        self.assert_claims(a, paths)
        self.assert_grok_text(a)

        self.use_fresh()
        a, paths = self.plant_legacy(shape)
        b = make_checkout(self.home, "b")
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_grok_text(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assertIsNone(read_owner(self.home, a))
        self.assertEqual(len(self.legacy_rows()), 3)
        self.assertEqual(set(self._row_path(row) for row in self.legacy_rows()), set(paths))

        self.use_fresh()
        a, paths = self.plant_legacy(shape)
        b = make_checkout(self.home, "b")
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assert_grok_text(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assertEqual(len(self.legacy_rows()), 3)

    def _row_path(self, row):
        return row["path"] if isinstance(row, dict) else row

    def test_row_14_harnesses_shape(self):
        self.legacy_shape("harnesses")

    def test_row_14_harness_shape(self):
        self.legacy_shape("harness")

    def test_row_14_bare_string_shape(self):
        self.legacy_shape("bare")

    def test_row_14b_legacy_backup_restores_an_untracked_link(self):
        a, b = self.two()
        plant_links(self.home, b)
        backup_dir = state_dir(self.home) / "backups" / "old" / "grok"
        backup_dir.mkdir(parents=True)
        links = []
        backups = []
        for name in NAMES:
            original = str(provider_link(self.home, "grok", name))
            backup = backup_dir / name
            os.symlink(a / "skills" / name, backup)
            links.append({"harnesses": ["grok"], "path": original})
            links.append({"harnesses": ["grok"], "path": original})
            backups.append({"harnesses": ["grok"], "original": original, "backup": str(backup)})
        write_legacy(self.home, links, backups)
        sentinel = self.home / "sentinel.txt"
        sentinel.write_text("keep\n")
        skill_bytes = {
            checkout / "skills" / name / "SKILL.md": (checkout / "skills" / name / "SKILL.md").read_bytes()
            for checkout in (a, b)
            for name in NAMES
        }
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_grok_text(a)
        for path, content in skill_bytes.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(sentinel.read_text(), "keep\n")
        adopted = run(self.home, a, "--harness", "grok")
        self.ok(adopted, "linked 0 skills into nothing (already installed)", ADOPTED.format(n=3))
        self.assertNotIn(UNTRACKED, adopted.stdout)
        self.assert_claims(a, grok_paths(self.home))
        self.assert_grok_text(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assertEqual(sentinel.read_text(), "keep\n")
        for path, content in skill_bytes.items():
            self.assertEqual(path.read_bytes(), content)

    def test_row_14c_old_manifest_file_cannot_erase_current_rows(self):
        a = make_checkout(self.home, "a")
        other = make_checkout(self.home, "c")
        self.ok(run(self.home, a, "--harness", "grok"))
        before = owner_file(self.home, a).read_bytes()
        plant_links(self.home, other, "codex")
        legacy_file(self.home).unlink()
        self.assertEqual(owner_file(self.home, a).read_bytes(), before)
        self.assertFalse(legacy_file(self.home).exists())
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assertFalse(legacy_file(self.home).exists())
        self.assertIsNone(read_owner(self.home, a))
        for name in NAMES:
            link = provider_link(self.home, "codex", name)
            self.assertEqual(os.readlink(link), str(other / "skills" / name))
            self.assertEqual((link / "SKILL.md").read_bytes(), (other / "skills" / name / "SKILL.md").read_bytes())

    def test_project_scope_refuses_a_directory_shared_with_user_scope(self):
        a = make_checkout(self.home, "a")
        self.ok(run(self.home, a, "--harness", "grok"))
        before = snapshot(state_dir(self.home))
        project = self.home / "project"
        project_skills = project / ".grok" / "skills"
        project_skills.parent.mkdir(parents=True)
        os.symlink(self.home / ".grok" / "skills", project_skills)
        refusal = f"grok: {project_skills} already resolves to {self.home / '.grok' / 'skills'}; nothing to link"
        self.ok(run(self.home, a, "--project", str(project), "--harness", "grok"), refusal)
        self.assertEqual(snapshot(state_dir(self.home)), before)
        self.assert_grok_text(a)
        self.assertFalse((project / ".pstack").exists())

    def share_cursor_with_agents(self):
        agents = self.home / ".agents" / "skills"
        agents.mkdir(parents=True)
        (agents / "swarm").write_bytes(b"foreign\x00file\n")
        cursor = self.home / ".cursor" / "skills"
        cursor.parent.mkdir(parents=True)
        os.symlink(agents, cursor)
        return agents

    def test_c3_shared_provider_directory_carries_both_harnesses(self):
        a, b = self.two()
        agents = self.share_cursor_with_agents()
        self.ok(run(self.home, a, "--harness", "codex", "--replace"))
        self.ok(run(self.home, b, "--harness", "cursor", "--replace"))
        self.ok(
            run(self.home, a, "--harness", "codex", "uninstall"),
            "removed 0 links, restored 0 entries",
            "kept entries whose directory is shared with cursor; select those harnesses too to remove them",
        )
        self.assertEqual(os.readlink(agents / "swarm"), str(b / "skills" / "swarm"))
        self.ok(
            run(self.home, a, "--harness", "codex,cursor", "uninstall"),
            "removed 3 links, restored 0 entries",
            "3 of them had been moved aside by another checkout's --replace",
        )
        self.assertEqual(os.readlink(agents / "swarm"), str(b / "skills" / "swarm"))
        self.assertEqual((agents / "swarm" / "SKILL.md").read_bytes(), (b / "skills" / "swarm" / "SKILL.md").read_bytes())
        self.ok(run(self.home, b, "--harness", "codex,cursor", "uninstall"), "removed 3 links, restored 1 entries")
        self.assertEqual((agents / "swarm").read_bytes(), b"foreign\x00file\n")
        self.assertFalse((agents / "swarm").is_symlink())
        self.assert_empty_records()

    def test_partial_selection_does_not_restore_over_a_live_link(self):
        a = make_checkout(self.home, "a")
        agents = self.share_cursor_with_agents()
        self.ok(run(self.home, a, "--harness", "codex", "--replace"))
        self.ok(run(self.home, a, "--harness", "cursor"), "linked 0 skills into nothing (already installed)")
        text = os.readlink(agents / "swarm")
        self.assertEqual(text, str(a / "skills" / "swarm"))
        saved = [path.read_bytes() for path in (state_dir(self.home) / "backups").rglob("swarm") if path.is_file()]
        self.assertEqual(saved, [b"foreign\x00file\n"])
        before = snapshot(self.home)
        self.ok(
            run(self.home, a, "--harness", "codex", "uninstall"),
            "removed 0 links, restored 0 entries",
            "kept entries whose directory is shared with cursor; select those harnesses too to remove them",
        )
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(os.readlink(agents / "swarm"), text)
        saved_after = [path.read_bytes() for path in (state_dir(self.home) / "backups").rglob("swarm") if path.is_file()]
        self.assertEqual(saved_after, [b"foreign\x00file\n"])

    def test_crash_between_action_halves_converges(self):
        a = make_checkout(self.home, "a")
        foreign = provider_link(self.home, "grok", "alpha")
        foreign.parent.mkdir(parents=True)
        foreign.write_bytes(b"keep-me\n")
        swarm = provider_link(self.home, "grok", "swarm")
        write_owner(self.home, a, [str(swarm)])
        self.ok(
            run(self.home, a, "--harness", "grok", "uninstall"),
            "removed 0 links, restored 0 entries",
            KEPT.format(n=1),
        )
        self.assertFalse(os.path.lexists(swarm))
        self.assertEqual(foreign.read_bytes(), b"keep-me\n")
        self.assert_claims(a, [str(swarm)])

        self.use_fresh()
        a = make_checkout(self.home, "a")
        plant_links(self.home, a)
        paths = grok_paths(self.home)
        backups = []
        for name in NAMES:
            link = str(provider_link(self.home, "grok", name))
            backups.append({
                "original": link,
                "harnesses": ["grok"],
                "backup": str(state_dir(self.home) / "backups" / "missing" / name),
            })
        write_owner(self.home, a, paths)
        write_legacy(self.home, [], backups)
        kept = self.home / "kept.txt"
        kept.write_text("kept\n")
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assertEqual(kept.read_text(), "kept\n")
        self.assertEqual(len(read_legacy(self.home)["backups"]), 3)
        self.assertIsNone(read_owner(self.home, a))

        self.use_fresh()
        a = make_checkout(self.home, "a")
        plant_links(self.home, a)
        swarm = provider_link(self.home, "grok", "swarm")
        swarm.unlink()
        swarm.write_bytes(b"foreign\x00file\n")
        write_owner(self.home, a, grok_paths(self.home))
        self.ok(
            run(self.home, a, "--harness", "grok", "uninstall"),
            "removed 2 links, restored 0 entries",
            KEPT.format(n=1),
        )
        self.assertEqual(swarm.read_bytes(), b"foreign\x00file\n")
        self.assertFalse(swarm.is_symlink())
        for name in ("alpha", "pstack-runtime"):
            self.assertFalse(os.path.lexists(provider_link(self.home, "grok", name)))
        self.assert_claims(a, [str(swarm)])

        self.use_fresh()
        a, b = self.two()
        plant_links(self.home, b)
        paths = grok_paths(self.home)
        write_owner(self.home, b, paths)
        write_owner(self.home, a, paths)
        backups = []
        for name in NAMES:
            link = str(provider_link(self.home, "grok", name))
            backups.append({
                "original": link,
                "harnesses": ["grok"],
                "backup": str(state_dir(self.home) / "backups" / "gone" / name),
            })
        write_legacy(self.home, [], backups)
        kept = self.home / "kept.txt"
        kept.write_text("kept\n")
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assert_grok_text(b)
        self.assert_claims(a, paths)
        self.assert_claims(b, paths)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assertEqual(kept.read_text(), "kept\n")
        self.assert_claims(a, paths)
        self.assertIsNone(read_owner(self.home, b))
        self.assertEqual(len(read_legacy(self.home)["backups"]), 3)

        self.use_fresh()
        a = make_checkout(self.home, "a")
        swarm = provider_link(self.home, "grok", "swarm")
        swarm.parent.mkdir(parents=True)
        swarm.write_bytes(b"foreign\x00file\n")
        write_legacy(self.home, [], [{
            "original": str(swarm),
            "harnesses": ["grok"],
            "backup": str(state_dir(self.home) / "backups" / "gone" / "swarm"),
            "note": "keep",
        }])
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assertEqual(swarm.read_bytes(), b"foreign\x00file\n")
        self.assertEqual(read_legacy(self.home)["backups"][0]["note"], "keep")

        self.use_fresh()
        a = make_checkout(self.home, "a")
        plant_links(self.home, a)
        paths = grok_paths(self.home)
        backups = []
        for name in NAMES:
            link = str(provider_link(self.home, "grok", name))
            backups.append({
                "original": link,
                "harnesses": ["grok"],
                "backup": str(state_dir(self.home) / "backups" / "gone" / name),
                "note": "keep",
            })
        write_owner(self.home, a, paths)
        write_legacy(self.home, [], backups)
        kept = self.home / "kept.txt"
        kept.write_text("kept\n")
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assertEqual(kept.read_text(), "kept\n")
        self.assertEqual([row["note"] for row in read_legacy(self.home)["backups"]], ["keep", "keep", "keep"])
        self.assertIsNone(read_owner(self.home, a))

    def resume(self, proc, ready, go, during):
        try:
            deadline = time.monotonic() + 30
            while not ready.exists():
                if proc.poll() is not None:
                    out, err = proc.communicate()
                    self.fail(f"paused install exited early\n{out}\n{err}")
                if time.monotonic() > deadline:
                    self.fail("paused install did not reach load")
                time.sleep(0.01)
            during()
            go.touch()
            out, err = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        self.assertEqual(proc.returncode, 0, err + out)
        return out

    def test_f1a_paused_noop_race_when_legacy_is_created(self):
        a = make_checkout(self.home, "a")
        c = make_checkout(self.home, "c", old=True)
        self.ok(run(self.home, a, "--harness", "grok"))
        proc, ready, go = pause_after_load(self.home, a, ["--harness", "grok"])
        out = self.resume(proc, ready, go, lambda: self.ok(run(self.home, c, "--harness", "codex")))
        self.assertIn("already installed", out)
        self.ok(run(self.home, c, "--harness", "codex", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone(harness="codex")
        self.assert_grok_text(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()

    def test_f1a_paused_noop_race_when_legacy_already_exists(self):
        a = make_checkout(self.home, "a", old=True)
        c = make_checkout(self.home, "c", old=True)
        self.ok(run(self.home, a, "--harness", "grok"))
        self.assertTrue(legacy_file(self.home).is_file())
        upgrade(a)
        proc, ready, go = pause_after_load(self.home, a, ["--harness", "grok"])
        out = self.resume(proc, ready, go, lambda: self.ok(run(self.home, c, "--harness", "codex")))
        self.assertIn("already installed", out)
        self.ok(run(self.home, c, "--harness", "codex", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone(harness="codex")
        self.assert_grok_text(a)
        upgrade(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()

    def test_f1b_older_operator_still_uninstalls(self):
        a = make_checkout(self.home, "a", old=True)
        b = make_checkout(self.home, "b")
        swarm = provider_link(self.home, "grok", "swarm")
        swarm.parent.mkdir(parents=True)
        swarm.write_bytes(b"operator foreign\x00bytes")
        self.ok(run(self.home, a, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "codex"))
        self.ok(run(self.home, b, "--harness", "codex", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone(harness="codex")
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
        self.assertEqual(swarm.read_bytes(), b"operator foreign\x00bytes")
        self.assertFalse(swarm.is_symlink())
        self.assert_gone(("alpha", "pstack-runtime"))

    def test_f1c_mixed_replacement_keeps_the_first_owner(self):
        a = make_checkout(self.home, "a")
        b = make_checkout(self.home, "b", old=True)
        c = make_checkout(self.home, "c")
        self.ok(run(self.home, a, "--harness", "grok"))
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        self.ok(run(self.home, c, "--harness", "codex"))
        upgrade(b)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_grok_text(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.ok(run(self.home, c, "--harness", "codex", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone(harness="codex")
        self.assert_empty_records()

    def test_f1d_mixed_replacement_restores_the_newer_layer(self):
        a = make_checkout(self.home, "a")
        b = make_checkout(self.home, "b", old=True)
        c = make_checkout(self.home, "c")
        swarm = provider_link(self.home, "grok", "swarm")
        swarm.parent.mkdir(parents=True)
        swarm.write_bytes(b"initial foreign\x00")
        self.ok(run(self.home, a, "--harness", "grok", "--replace"))
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        self.ok(run(self.home, c, "--harness", "codex"))
        upgrade(b)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_grok_text(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
        self.assertEqual(swarm.read_bytes(), b"initial foreign\x00")
        self.assertFalse(swarm.is_symlink())
        self.assert_gone(("alpha", "pstack-runtime"))

    def _pruned(self):
        a = make_checkout(self.home, "a", old=True)
        c = make_checkout(self.home, "c")
        shutil.rmtree(a / "skills" / "pstack-runtime")
        self.ok(run(self.home, a, "--harness", "grok"), "linked 2 skills into grok")
        self.ok(run(self.home, c, "--harness", "codex"))
        return a, c

    def test_f1e_pruned_old_tree_uninstalls(self):
        a, c = self._pruned()
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 2 links, restored 0 entries")
        self.assert_gone(("alpha", "swarm"))
        self.assertEqual((a / "skills" / "swarm" / "SKILL.md").read_text(), "a-swarm\n")
        for name in NAMES:
            link = provider_link(self.home, "codex", name)
            self.assertEqual(os.readlink(link), str(c / "skills" / name))
        self.ok(run(self.home, c, "--harness", "codex", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone(harness="codex")

    def test_f1e_pruned_old_tree_uninstalls_after_upgrade(self):
        a, c = self._pruned()
        upgrade(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 2 links, restored 0 entries")
        self.assert_gone(("alpha", "swarm"))
        for name in NAMES:
            link = provider_link(self.home, "codex", name)
            self.assertEqual(os.readlink(link), str(c / "skills" / name))
            self.assertEqual((link / "SKILL.md").read_bytes(), (c / "skills" / name / "SKILL.md").read_bytes())
        self.ok(run(self.home, c, "--harness", "codex", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone(harness="codex")

    def test_f2a_blocked_restore_keeps_the_backup(self):
        b = make_checkout(self.home, "b")
        swarm = provider_link(self.home, "grok", "swarm")
        swarm.parent.mkdir(parents=True)
        swarm.write_bytes(b"original foreign\x00bytes")
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        blocked = squat_uninstall(self.home, b, swarm, b"new occupant\x00bytes", "--harness", "grok", "uninstall")
        self.ok(blocked, "removed 3 links, restored 0 entries")
        self.assertIn("skipped restore", blocked.stdout)
        self.assertEqual(swarm.read_bytes(), b"new occupant\x00bytes")
        swarm.unlink()
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 0 links, restored 1 entries")
        self.assertEqual(swarm.read_bytes(), b"original foreign\x00bytes")
        self.assertFalse(swarm.is_symlink())

    def test_f2b_blocked_restore_keeps_the_displaced_owner(self):
        a, b = self.two()
        self.ok(run(self.home, a, "--harness", "grok"))
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        swarm = provider_link(self.home, "grok", "swarm")
        blocked = squat_uninstall(self.home, b, swarm, b"new occupant", "--harness", "grok", "uninstall")
        self.ok(blocked, "removed 3 links, restored 2 entries")
        self.assertIn("skipped restore", blocked.stdout)
        self.assertEqual(swarm.read_bytes(), b"new occupant")
        self.ok(
            run(self.home, a, "--harness", "grok", "uninstall"),
            "removed 3 links, restored 0 entries",
            "1 of them had been moved aside by another checkout's --replace",
        )
        self.assertEqual(swarm.read_bytes(), b"new occupant")
        self.assertFalse(swarm.is_symlink())
        self.assert_gone(("alpha", "pstack-runtime"))
        before = swarm.read_bytes()
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
        self.assertEqual(swarm.read_bytes(), before)

    def test_f3a_provider_directory_move_keeps_ownership(self):
        a = make_checkout(self.home, "a")
        b = make_checkout(self.home, "b")
        self.ok(run(self.home, a, "--harness", "grok"))
        skills = self.home / ".grok" / "skills"
        dest = self.home / "relocated-skills"
        skills.rename(dest)
        skills.symlink_to(dest)
        self.assert_grok_text(a)
        self.ok(run(self.home, b, "--harness", "codex", "uninstall"), "removed 0 links, restored 0 entries")
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()

    def test_f3b_dotfiles_move_restores_at_the_current_path(self):
        a = make_checkout(self.home, "a")
        dots1 = self.home / "dots1" / "grok"
        (dots1 / "skills" / "swarm").mkdir(parents=True)
        (dots1 / "skills" / "swarm" / "SKILL.md").write_bytes(b"user directory\x00bytes")
        (self.home / ".grok").symlink_to(dots1)
        self.ok(run(self.home, a, "--harness", "grok", "--replace"))
        (self.home / "dots1").rename(self.home / "dots2")
        (self.home / ".grok").unlink()
        (self.home / ".grok").symlink_to(self.home / "dots2" / "grok")
        doctor = run(self.home, a, "--harness", "grok", "doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        self.assertIn("3/3 pstack-t3", doctor.stdout)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
        swarm = self.home / ".grok" / "skills" / "swarm"
        self.assertEqual((swarm / "SKILL.md").read_bytes(), b"user directory\x00bytes")
        self.assertFalse(swarm.is_symlink())
        self.assertFalse((self.home / "dots1").exists())
        self.assert_gone(("alpha", "pstack-runtime"))

    def test_f4a_temporary_absence_keeps_ownership(self):
        a = make_checkout(self.home, "a")
        c = make_checkout(self.home, "c")
        self.ok(run(self.home, a, "--harness", "grok"))
        away = self.home / "a-away"
        a.rename(away)
        self.ok(run(self.home, c, "--harness", "claude"))
        away.rename(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        for name in NAMES:
            link = provider_link(self.home, "claude", name)
            self.assertEqual(os.readlink(link), str(c / "skills" / name))
        self.ok(run(self.home, c, "--harness", "claude", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone(harness="claude")

    def test_f4b_displaced_owner_survives_temporary_absence(self):
        a, b = self.two()
        c = make_checkout(self.home, "c")
        self.ok(run(self.home, a, "--harness", "grok"))
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        away = self.home / "a-away"
        a.rename(away)
        self.ok(run(self.home, c, "--harness", "claude"))
        self.ok(run(self.home, c, "--harness", "claude", "uninstall"), "removed 3 links, restored 0 entries")
        away.rename(a)
        self.ok(
            run(self.home, a, "--harness", "grok", "uninstall"),
            "removed 3 links, restored 0 entries",
            "3 of them had been moved aside by another checkout's --replace",
        )
        self.assert_grok_text(b)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assert_empty_records()

    def test_f4b_absent_owner_is_restored_by_the_covering_checkout(self):
        a, b = self.two()
        c = make_checkout(self.home, "c")
        self.ok(run(self.home, a, "--harness", "grok"))
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        away = self.home / "a-away"
        a.rename(away)
        self.ok(run(self.home, c, "--harness", "claude"))
        self.ok(run(self.home, c, "--harness", "claude", "uninstall"), "removed 3 links, restored 0 entries")
        away.rename(a)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_grok_text(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assert_empty_records()

    def _chain(self, foreign=False):
        a = make_checkout(self.home, "a")
        b = make_checkout(self.home, "b")
        c = make_checkout(self.home, "c", old=True)
        if foreign:
            swarm = provider_link(self.home, "grok", "swarm")
            swarm.parent.mkdir(parents=True)
            swarm.write_bytes(b"stack foreign\x00")
        for owner in (a, b, c):
            self.ok(run(self.home, owner, "--harness", "grok", "--replace"))
        return a, b, c

    def test_f5a_mixed_generation_chain_restores_the_top_layer(self):
        a, b, c = self._chain()
        upgrade(c)
        self.ok(run(self.home, c, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_grok_text(b)
        self.ok(
            run(self.home, a, "--harness", "grok", "uninstall"),
            "removed 3 links, restored 0 entries",
            "3 of them had been moved aside by another checkout's --replace",
        )
        self.assert_grok_text(b)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assert_empty_records()

    def test_f5b_old_uninstall_in_the_chain_still_cleans_up(self):
        a, b, c = self._chain()
        self.ok(run(self.home, c, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_grok_text(b)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_grok_text(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assert_empty_records()

    def test_f5c_foreign_bytes_survive_the_mixed_chain(self):
        a, b, c = self._chain(foreign=True)
        upgrade(c)
        swarm = provider_link(self.home, "grok", "swarm")
        self.ok(run(self.home, c, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_grok_text(b)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_grok_text(b)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
        self.assertEqual(swarm.read_bytes(), b"stack foreign\x00")
        self.assertFalse(swarm.is_symlink())
        self.assert_gone(("alpha", "pstack-runtime"))

    def backup_dir(self, harness="grok"):
        found = sorted((state_dir(self.home) / "backups").glob(f"*/{harness}"))
        self.assertEqual(len(found), 1, found)
        return found[0]

    def test_failed_withdraw_keeps_the_claim_for_the_next_uninstall(self):
        a, b = self.base()
        for name in NAMES:
            provider_link(self.home, "grok", name).unlink()
        self.ok(run(self.home, a, "--harness", "grok"), "linked 3 skills into grok")
        layer = self.backup_dir()
        layer.chmod(0o555)
        self.addCleanup(layer.chmod, 0o755)
        failed = run(self.home, a, "--harness", "grok", "uninstall")
        self.ok(failed, "removed 3 links, restored 0 entries", KEPT.format(n=3))
        self.assertIn("skipped withdraw", failed.stdout)
        self.assert_claims(a, grok_paths(self.home))
        layer.chmod(0o755)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assertIsNone(read_owner(self.home, a))
        data = read_legacy(self.home)
        self.assertEqual(data["backups"], [])
        self.assertEqual([row for row in data["links"] if row.get("checkout") == str(a)], [])

    def test_withdraw_beneath_a_repointed_link_keeps_its_claim(self):
        a, b = self.base()
        self.ok(run(self.home, a, "--harness", "grok", "--replace"), "linked 3 skills into grok")
        foreign = self.home / "foreign-dir"
        foreign.mkdir()
        self.repoint_swarm(foreign)
        swarm = provider_link(self.home, "grok", "swarm")
        self.ok(
            run(self.home, a, "--harness", "grok", "uninstall"),
            "removed 5 links, restored 2 entries",
            KEPT.format(n=1),
        )
        self.assert_claims(a, [str(swarm)])
        self.assertEqual(os.readlink(swarm), str(foreign))
        swarm.unlink()
        os.symlink(a / "skills" / "swarm", swarm)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 1 links, restored 1 entries")
        self.assertIsNone(read_owner(self.home, a))
        self.assert_grok_text(b)
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assert_empty_records()

    def test_withdraw_through_a_backup_keeps_the_covering_old_rows(self):
        a = make_checkout(self.home, "a")
        c = make_checkout(self.home, "c", old=True)
        d = make_checkout(self.home, "d", old=True)
        self.ok(run(self.home, a, "--harness", "grok"))
        self.ok(run(self.home, d, "uninstall"), "removed 0 links, restored 0 entries")
        self.ok(run(self.home, c, "--harness", "grok", "--replace"))
        self.ok(
            run(self.home, a, "--harness", "grok", "uninstall"),
            "removed 3 links, restored 0 entries",
            "3 of them had been moved aside by another checkout's --replace",
        )
        self.assertIsNone(read_owner(self.home, a))
        self.assert_grok_text(c)
        self.ok(run(self.home, c, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()

    def test_failed_move_leaves_no_backup_row(self):
        b = make_checkout(self.home, "b")
        swarm = provider_link(self.home, "grok", "swarm")
        swarm.parent.mkdir(parents=True)
        swarm.write_bytes(b"foreign\x00bytes")
        failed = run_failing(self.home, b, "shutil.move", swarm, "--harness", "grok", "--replace")
        self.ok(
            failed,
            "linked 2 skills into grok",
            f"skipped move {swarm}: [Errno 13] Permission denied: '{swarm}'",
        )
        self.assertEqual(read_legacy(self.home)["backups"], [])
        self.assertEqual(swarm.read_bytes(), b"foreign\x00bytes")
        alpha, runtime = (str(provider_link(self.home, "grok", name)) for name in ("alpha", "pstack-runtime"))
        self.assert_claims(b, [alpha, runtime])

    def test_failed_link_leaves_no_claim_or_row(self):
        b = make_checkout(self.home, "b")
        swarm = provider_link(self.home, "grok", "swarm")
        failed = run_failing(self.home, b, "os.symlink", swarm, "--harness", "grok")
        self.ok(
            failed,
            "linked 2 skills into grok",
            f"skipped link {swarm}: [Errno 13] Permission denied: '{swarm}'",
        )
        alpha, runtime = (str(provider_link(self.home, "grok", name)) for name in ("alpha", "pstack-runtime"))
        self.assert_claims(b, [alpha, runtime])
        self.assertEqual(sorted(row["path"] for row in read_legacy(self.home)["links"]), [alpha, runtime])

    def assert_refused(self, result, file):
        self.assertNotEqual(result.returncode, 0)
        lines = result.stderr.strip().splitlines()
        self.assertEqual(len(lines), 1, result.stderr)
        self.assertIn(str(file), lines[0])

    def test_legacy_file_that_is_not_an_object_is_refused_unchanged(self):
        a = make_checkout(self.home, "a")
        for text in ('["kept"]\n', '{"links": {"kept": 1}}\n', "{not json\n"):
            legacy_file(self.home).parent.mkdir(parents=True, exist_ok=True)
            legacy_file(self.home).write_text(text)
            self.assert_refused(run(self.home, a, "--harness", "grok"), legacy_file(self.home))
            self.assertEqual(legacy_file(self.home).read_text(), text)
            self.assert_gone()

    def test_owner_file_that_is_not_json_is_refused_unchanged(self):
        a = make_checkout(self.home, "a")
        owner = owner_file(self.home, a)
        owner.parent.mkdir(parents=True)
        owner.write_text("{not json\n")
        self.assert_refused(run(self.home, a, "--harness", "grok"), owner)
        self.assertEqual(owner.read_text(), "{not json\n")
        self.assert_gone()

    def test_backup_row_without_harnesses_matches_its_directory(self):
        a = make_checkout(self.home, "a")
        swarm = provider_link(self.home, "grok", "swarm")
        swarm.parent.mkdir(parents=True)
        saved = state_dir(self.home) / "backups" / "old" / "grok" / "swarm"
        saved.mkdir(parents=True)
        (saved / "SKILL.md").write_text("saved\n")
        write_legacy(self.home, [], [{"original": str(swarm), "backup": str(saved)}])
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 1 entries")
        self.assertEqual((swarm / "SKILL.md").read_text(), "saved\n")

    def test_partial_move_keeps_the_row_for_its_copy(self):
        b = make_checkout(self.home, "b")
        skills = provider_link(self.home, "grok", "swarm").parent
        swarm = skills / "swarm"
        swarm.mkdir(parents=True)
        (swarm / "SKILL.md").write_text("precious\n")
        skills.chmod(0o555)
        self.addCleanup(skills.chmod, 0o755)
        self.ok(run(self.home, b, "--harness", "grok", "--replace"), "linked 0 skills into grok")
        rows = read_legacy(self.home)["backups"]
        self.assertEqual([row["original"] for row in rows], [str(swarm)])
        self.assertEqual((Path(rows[0]["backup"]) / "SKILL.md").read_text(), "precious\n")

    def test_old_replace_before_a_removal_keeps_its_link_and_our_claim(self):
        a = make_checkout(self.home, "a")
        b = make_checkout(self.home, "b", old=True)
        self.ok(run(self.home, a, "--harness", "grok"))
        replace = [sys.executable, b / "scripts" / "install.py", "--harness", "grok", "--replace"]
        raced = interleave_removal(self.home, a, grok_paths(self.home), replace, "--harness", "grok", "uninstall")
        self.ok(raced, "removed 0 links, restored 0 entries", KEPT.format(n=3))
        self.assert_grok_text(b)
        self.assert_claims(a, grok_paths(self.home))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_grok_text(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assertIsNone(read_owner(self.home, a))
        self.assertEqual(sorted(os.listdir(provider_link(self.home, "grok", "swarm").parent)), [])

    def test_old_replace_and_a_new_occupant_keep_the_moved_link_aside(self):
        a = make_checkout(self.home, "a")
        b = make_checkout(self.home, "b", old=True)
        self.ok(run(self.home, a, "--harness", "grok"))
        alpha = provider_link(self.home, "grok", "alpha")
        replace = [sys.executable, b / "scripts" / "install.py", "--harness", "grok", "--replace"]
        raced = interleave_removal(self.home, a, [alpha], replace, "--harness", "grok", "uninstall", occupy=b"occupant\x00")
        self.ok(raced, "removed 0 links, restored 0 entries", KEPT.format(n=3))
        aside = set_aside(alpha)
        self.assertEqual(len(aside), 1, aside)
        self.assertIn(
            f"skipped unlink {alpha}: the link no longer matches the recorded checkout and {alpha} was taken again; "
            f"it is kept at {aside[0]}",
            raced.stdout.splitlines(),
        )
        self.assertEqual(os.readlink(aside[0]), str(b / "skills" / "alpha"))
        self.assertEqual(alpha.read_bytes(), b"occupant\x00")
        self.assert_grok_text(b, ("pstack-runtime", "swarm"))
        self.assert_claims(a, grok_paths(self.home))

    def test_entry_moved_into_a_backup_during_uninstall_keeps_its_claim(self):
        a = make_checkout(self.home, "a")
        b = make_checkout(self.home, "b", old=True)
        self.ok(run(self.home, a, "--harness", "grok"))
        self.ok(run(self.home, b, "--harness", "grok", "--replace"))
        for name in NAMES:
            provider_link(self.home, "grok", name).unlink()
        relink_then_replace = (
            "import os, subprocess, sys\n"
            f"for name in {list(NAMES)!r}:\n"
            f"    os.symlink(os.path.join({str(a / 'skills')!r}, name), os.path.join({str(provider_link(self.home, 'grok', 'x').parent)!r}, name))\n"
            f"subprocess.run([sys.executable, {str(b / 'scripts' / 'install.py')!r}, '--harness', 'grok', '--replace'], check=True)\n"
        )
        layer = str(self.backup_dir())
        withdrawn = [os.path.join(layer, name) for name in NAMES]
        raced = interleave_removal(
            self.home, a, withdrawn, [sys.executable, "-c", relink_then_replace], "--harness", "grok", "uninstall"
        )
        self.ok(raced, "removed 3 links, restored 0 entries", KEPT.format(n=3))
        self.assert_grok_text(b)
        self.assert_claims(a, grok_paths(self.home))
        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
        self.assert_grok_text(a)
        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
        self.assert_gone()
        self.assertIsNone(read_owner(self.home, a))

    def test_a_hidden_entry_present_before_uninstall_survives_every_move_aside(self):
        for kind in ("file", "link"):
            with self.subTest(kind=kind):
                self.use_fresh()
                a = make_checkout(self.home, "a")
                self.ok(run(self.home, a, "--harness", "grok"))
                parent = provider_link(self.home, "grok", "swarm").parent
                # Every hidden name an uninstall could pick first is already taken by an entry it did not create.
                plant = (
                    "import secrets, tempfile\n"
                    "secrets.token_hex = lambda n: 'deadbeef'\n"
                    "candidates = tempfile._get_candidate_names\n"
                    "def colliding():\n"
                    "    yield 'deadbeef'\n"
                    "    yield from candidates()\n"
                    "tempfile._get_candidate_names = colliding\n"
                    f"for name in ['.swarm.pstack-t3-%d-deadbeef' % os.getpid(), '.pstack-t3-deadbeef']:\n"
                    f"    hidden = os.path.join({str(parent)!r}, name)\n"
                    f"    if {kind!r} == 'file':\n"
                    "        Path(hidden).write_bytes(b'hidden\\x00\\xff')\n"
                    "    else:\n"
                    "        os.symlink('/foreign/hidden', hidden)\n"
                )
                raced = uninstall_hooked(self.home, a, plant)
                self.ok(raced, "removed 3 links, restored 0 entries")
                self.assert_gone()
                hidden = sorted(name for name in os.listdir(parent))
                self.assertEqual(len(hidden), 2, hidden)
                self.assertEqual(hidden[0], ".pstack-t3-deadbeef")
                self.assertRegex(hidden[1], r"^\.swarm\.pstack-t3-\d+-deadbeef$")
                for name in hidden:
                    if kind == "file":
                        self.assertEqual((parent / name).read_bytes(), b"hidden\x00\xff")
                    else:
                        self.assertEqual(os.readlink(parent / name), "/foreign/hidden")
                self.assertIsNone(read_owner(self.home, a))

    def test_a_directory_put_back_never_replaces_a_directory_that_took_the_path(self):
        a = make_checkout(self.home, "a")
        self.ok(run(self.home, a, "--harness", "grok"))
        swarm = provider_link(self.home, "grok", "swarm")
        raced = uninstall_hooked(self.home, a, put_back_race(swarm, "directory", occupy="directory"))
        self.assertEqual(os.listdir(swarm), [])
        aside = set_aside(swarm)
        self.assertEqual(len(aside), 1, aside)
        self.ok(
            raced,
            "removed 2 links, restored 0 entries",
            f"skipped unlink {swarm}: the link no longer matches the recorded checkout and {swarm} was taken again; "
            f"it is kept at {aside[0]}",
        )
        self.assertEqual((aside[0] / "precious").read_bytes(), b"directory bytes\x00")
        self.assert_claims(a, [str(swarm)])

    def test_a_foreign_directory_moved_aside_goes_back_to_its_empty_path(self):
        a = make_checkout(self.home, "a")
        self.ok(run(self.home, a, "--harness", "grok"))
        swarm = provider_link(self.home, "grok", "swarm")
        raced = uninstall_hooked(self.home, a, put_back_race(swarm, "directory"))
        self.ok(
            raced,
            "removed 2 links, restored 0 entries",
            f"skipped unlink {swarm}: the link no longer matches the recorded checkout",
        )
        self.assertEqual((swarm / "precious").read_bytes(), b"directory bytes\x00")
        self.assertEqual(os.listdir(swarm.parent), ["swarm"])
        self.assert_claims(a, [str(swarm)])

    def test_a_put_back_without_hard_links_never_replaces_a_file_that_took_the_path(self):
        a = make_checkout(self.home, "a")
        self.ok(run(self.home, a, "--harness", "grok"))
        swarm = provider_link(self.home, "grok", "swarm")
        raced = uninstall_hooked(self.home, a, put_back_race(swarm, "link", occupy="file", link_error=True))
        self.assertFalse(swarm.is_symlink())
        self.assertEqual(swarm.read_bytes(), b"occupant\x00\xff")
        aside = set_aside(swarm)
        self.assertEqual(len(aside), 1, aside)
        self.ok(
            raced,
            "removed 2 links, restored 0 entries",
            f"skipped unlink {swarm}: the link no longer matches the recorded checkout and {swarm} was taken again; "
            f"it is kept at {aside[0]}",
        )
        self.assertEqual(os.readlink(aside[0]), "/foreign/swarm")
        self.assert_claims(a, [str(swarm)])

    def test_a_put_back_with_no_safe_rename_keeps_the_entry_aside(self):
        a = make_checkout(self.home, "a")
        self.ok(run(self.home, a, "--harness", "grok"))
        swarm = provider_link(self.home, "grok", "swarm")
        raced = uninstall_hooked(self.home, a, put_back_race(swarm, "link", link_error=True, no_primitive=True))
        self.assertFalse(os.path.lexists(swarm))
        aside = set_aside(swarm)
        self.assertEqual(len(aside), 1, aside)
        self.ok(
            raced,
            "removed 2 links, restored 0 entries",
            f"skipped unlink {swarm}: the link no longer matches the recorded checkout and {swarm} cannot be refilled "
            f"without risking an overwrite ({os.strerror(errno.ENOSYS)}); it is kept at {aside[0]}",
        )
        self.assertEqual(os.readlink(aside[0]), "/foreign/swarm")
        self.assert_claims(a, [str(swarm)])
