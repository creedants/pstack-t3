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


class Part02Test(OwnershipCase):
    def share_skills(self, source, dest):
            shutil.rmtree(dest / "skills")
            os.symlink(source / "skills", dest / "skills")
    def test_row_11b_deleted_checkout_links_are_restored_beside_a_live_owner(self):
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
    def test_project_scope_leaves_holders_in_a_directory_shared_with_user_scope(self):
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "grok"))
            shared = self.home / ".grok" / "skills"
            project = self.home / "project"
            (project / ".grok").mkdir(parents=True)
            os.symlink(shared, project / ".grok" / "skills")
            empty = shared / ".pstack-t3-live0000"
            empty.mkdir()
            stray = shared / ".pstack-t3-other000" / "notes"
            stray.parent.mkdir()
            stray.write_bytes(b"stray\x00\xfd")
            for command in ((), ("uninstall",)):
                with self.subTest(command=command):
                    result = run(self.home, a, "--project", str(project), "--harness", "grok", *command)
                    self.ok(result)
                    self.assertNotIn(".pstack-t3-", result.stdout + result.stderr)
                    self.assertEqual(os.listdir(empty), [])
                    self.assertEqual(stray.read_bytes(), b"stray\x00\xfd")
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
