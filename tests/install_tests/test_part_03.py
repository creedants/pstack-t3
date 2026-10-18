"""Installer ownership behavior."""

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


class Part03Test(OwnershipCase):
    def share_cursor_with_agents(self):
            agents = self.home / ".agents" / "skills"
            agents.mkdir(parents=True)
            (agents / "swarm").write_bytes(b"foreign\x00file\n")
            cursor = self.home / ".cursor" / "skills"
            cursor.parent.mkdir(parents=True)
            os.symlink(agents, cursor)
            return agents
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
