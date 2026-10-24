"""Installer ownership behavior."""

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

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

from .support import setUpModule, OwnershipCase
from .support import _env, _run
from .support import *
from .hooks import *
from .races import *
from .old_installer import *


class Part04Test(OwnershipCase):
    def _pruned(self):
            a = make_checkout(self.home, "a", old=True)
            c = make_checkout(self.home, "c")
            shutil.rmtree(a / "skills" / "pstack-runtime")
            self.ok(run(self.home, a, "--harness", "grok"), "linked 2 skills into grok")
            self.ok(run(self.home, c, "--harness", "codex"))
            return a, c
    def repoint_swarm(self, target):
            link = provider_link(self.home, "grok", "swarm")
            link.unlink()
            os.symlink(target, link)
            return str(target)
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
            self.skipped(blocked, "removed 3 links, restored 0 entries")
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
            self.skipped(blocked, "removed 3 links, restored 2 entries")
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
            self.addCleanup(lambda: layer.exists() and layer.chmod(0o755))
            failed = run(self.home, a, "--harness", "grok", "uninstall")
            self.skipped(failed, "removed 3 links, restored 0 entries", KEPT.format(n=3))
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
