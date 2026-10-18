"""Shared helpers for installer behavior tests."""

import errno
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests import in_process

from .old_installer import OLD_INSTALLER

def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
    saved = dict(os.environ)

    def restore():
        os.environ.clear()
        os.environ.update(saved)

    unittest.addModuleCleanup(restore)
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull

def setUpModule():
    fence_git_environment()

ROOT = Path(__file__).resolve().parents[2]

NAMES = ("alpha", "pstack-runtime", "swarm")

OLD_SHA = "3936df1838da72e64c9870c6c73262929c27f828385be625286018a80039a33d"

KEPT = "kept {n} records whose links no longer point at this checkout; they apply again if the links come back"

ADOPTED = "adopted {n} links an older installer recorded"

UNTRACKED = "links already point at this checkout but are not tracked; uninstall leaves them"

CONTESTED = "kept backup {backup}: {aside} is held for that path and another entry is there now; remove the one that is not the backup and rerun uninstall"

def run_install(home, *args):
    return _run(home, ROOT, args)

def _env(home):
    env = os.environ.copy()
    env.pop("XDG_CONFIG_HOME", None)
    env.pop("CLAUDE_CONFIG_DIR", None)
    env["HOME"] = str(home)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env

def _run(home, checkout, args, env=None, cwd=None):
    return in_process.invoke(in_process.Invocation(
        program=Path(checkout) / "scripts" / "install.py",
        argv=tuple(args),
        env={**_env(home), **(env or {})},
        cwd=cwd or home,
    ))

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
        shutil.copytree(ROOT / "scripts" / "_install", checkout / "scripts" / "_install", dirs_exist_ok=True)
    for skill in NAMES:
        directory = checkout / "skills" / skill
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"{name}-{skill}\n")
    return checkout

def upgrade(checkout):
    shutil.copy(ROOT / "scripts" / "install.py", checkout / "scripts" / "install.py")
    shutil.copytree(ROOT / "scripts" / "_install", checkout / "scripts" / "_install", dirs_exist_ok=True)

def run(home, checkout, *args, env=None, cwd=None):
    return _run(home, checkout, args, env, cwd)

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

def set_aside(path):
    """Entries an uninstall kept aside for `path`, each in its own hidden directory beside it."""
    # Before Python 3.12, Path.glob skips a final symlink that points nowhere, so each holder is checked with lexists.
    return sorted(holder / path.name for holder in path.parent.glob(".pstack-t3-*/") if os.path.lexists(holder / path.name))


class OwnershipCase(unittest.TestCase):
    RELATIVE_HOME = {"HOME": "."}
    RELATIVE_XDG = {"XDG_CONFIG_HOME": ".config"}
    UNBUILT = "skills/ is missing; run python3 scripts/build.py first"
    NOT_A_DIRECTORY = "skills is not a directory; move it away, then run python3 scripts/build.py"
    INSTALL = "python3 scripts/install.py install --harness {harnesses}"
    UNINSTALL = "python3 scripts/install.py uninstall --harness {harnesses}"
    NO_MANIFEST = "doctor checked no claims and no backup rows"
    NO_CLAIMS = "doctor checked no claims of this checkout"
    STAMP = "20260101T000000-1-abcd"
    HELD = f".config/pstack-t3/backups/{STAMP}/grok/.pstack-t3-probe/swarm"
    NAMED = f".config/pstack-t3/backups/{STAMP}/grok/swarm"
    UNNAMED = f"left {HELD}: no backup record names {NAMED}"
    NOT_JSON = "open(manifest, 'w').write('{not json\\n')"
    NO_SPACE = "raise OSError(errno.ENOSPC, 'No space left on device')"
    ADRIFT = (
        "{file} records the relative path rel/skills/swarm, and the system could not name this run's working directory "
        "(No such file or directory); change to another directory and rerun"
    )
    RELATIVE_ROW = ".config/pstack-t3/backups/20260101T000000-1-abcd/grok/swarm"
    NO_SKILL = "skills/ holds no skill; run python3 scripts/build.py first"
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
    def skipped(self, result, *lines):
            self.assertEqual(result.returncode, 3, result.stderr + result.stdout)
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
    def holders(self):
            """The sorted `.pstack-t3-*` matches under the home."""
            return sorted(str(path) for path in self.home.rglob(".pstack-t3-*"))
    def two(self):
            return make_checkout(self.home, "a"), make_checkout(self.home, "b")
    def base(self):
            a, b = self.two()
            self.ok(run(self.home, a, "--harness", "grok"))
            self.ok(run(self.home, b, "--harness", "grok", "--replace"))
            return a, b
