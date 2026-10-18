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


class Part10Test(OwnershipCase):
    def assert_displaced(self, path, kind):
            if kind == "directory":
                self.assertEqual(os.listdir(path), ["SKILL.md"])
                self.assertEqual((path / "SKILL.md").read_bytes(), b"displaced\x00\xfe")
            elif kind == "link":
                self.assertEqual(os.readlink(path), "/foreign/displaced")
            else:
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.read_bytes(), b"displaced\x00\xfe")
    def displaced_by(self, kind):
            """Checkout a after `--replace` moved a foreign `kind` entry at swarm aside, with swarm and its one backup row."""
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            swarm.parent.mkdir(parents=True)
            if kind == "directory":
                swarm.mkdir()
                (swarm / "SKILL.md").write_bytes(b"displaced\x00\xfe")
            elif kind == "link":
                os.symlink("/foreign/displaced", swarm)
            else:
                swarm.write_bytes(b"displaced\x00\xfe")
            self.ok(run(self.home, a, "--harness", "grok", "--replace"), "linked 3 skills into grok")
            rows = read_legacy(self.home)["backups"]
            self.assertEqual([row["original"] for row in rows], [str(swarm)])
            return a, swarm, rows
    def plant(self, beside, holder=".pstack-t3-unproven"):
            """A file in a holder beside `beside` that no installer put there."""
            stray = beside.parent / holder / beside.name
            stray.parent.mkdir(parents=True)
            stray.write_bytes(b"stray\x00\xfd")
            return stray
    def share_cursor_with_agents(self):
            agents = self.home / ".agents" / "skills"
            agents.mkdir(parents=True)
            (agents / "swarm").write_bytes(b"foreign\x00file\n")
            cursor = self.home / ".cursor" / "skills"
            cursor.parent.mkdir(parents=True)
            os.symlink(agents, cursor)
            return agents
    def strand_backup(self, hook, kind="directory", *, reason=None):
            """Checkout a's uninstall, stopped by `hook` with the backup of a displaced swarm left aside."""
            a, swarm, rows = self.displaced_by(kind)
            backup = Path(rows[0]["backup"])
            raced = uninstall_hooked(self.home, a, hook, reason=reason)
            aside = set_aside(backup)
            self.assertEqual(len(aside), 1, raced.stdout + raced.stderr)
            self.assert_displaced(aside[0], kind)
            return a, swarm, backup, raced, aside[0]
    def strand_copied_backup(self):
            """Checkout a's uninstall across filesystems, stopped after the copy landed at swarm and before the backup was deleted."""
            a, swarm, rows = self.displaced_by("directory")
            backup = Path(rows[0]["backup"])
            hook = cross_device(state_dir(self.home) / "backups", swarm) + raising("discard")
            raced = uninstall_hooked(self.home, a, hook)
            self.assertGreater(crossings(self.home), 0, raced.stdout + raced.stderr)
            aside = set_aside(backup)
            self.assertEqual(len(aside), 1, raced.stdout + raced.stderr)
            self.assert_displaced(aside[0], "directory")
            self.assert_displaced(swarm, "directory")
            return a, swarm, backup, raced, aside[0]
    def strand_link(self):
            """Checkout a's uninstall, stopped with its own swarm link left aside and the claim still recorded."""
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "grok"))
            swarm = provider_link(self.home, "grok", "swarm")
            raced = uninstall_hooked(self.home, a, UNLINK_ASIDE)
            aside = set_aside(swarm)
            self.assertEqual(len(aside), 1, raced.stdout + raced.stderr)
            self.assertEqual(os.readlink(aside[0]), str(a / "skills" / "swarm"))
            self.assert_claims(a, [str(swarm)])
            return a, swarm, raced, aside[0]
    def test_a_copy_in_a_scrap_holder_is_never_placed(self):
            for copy in ("directory", "link"):
                with self.subTest(copy=copy):
                    self.use_fresh()
                    a, swarm, rows = self.displaced_by("directory")
                    os.unlink(swarm)
                    scrap = swarm.parent / ".pstack-t3-scrap-partial0" / "swarm"
                    scrap.parent.mkdir()
                    if copy == "directory":
                        scrap.mkdir()
                        (scrap / "SKILL.md").write_bytes(b"partial\x00")
                    else:
                        os.symlink(a / "skills" / "swarm", scrap)
                    self.assert_claims(a, grok_paths(self.home))
                    self.assertEqual(read_legacy(self.home)["backups"], rows)
                    line = f"left {scrap}: {SCRAP_LEFT}"
                    self.ok(run(self.home, a, "--harness", "grok"), line, "linked 1 skills into grok")
                    self.assert_grok_text(a)
                    self.ok(run(self.home, a, "--harness", "grok", "uninstall"), line, "removed 3 links, restored 1 entries")
                    self.assert_displaced(swarm, "directory")
                    if copy == "directory":
                        self.assertEqual(os.listdir(scrap), ["SKILL.md"])
                        self.assertEqual((scrap / "SKILL.md").read_bytes(), b"partial\x00")
                    else:
                        self.assertEqual(os.readlink(scrap), str(a / "skills" / "swarm"))
    def test_what_is_left_of_a_copied_backup_is_never_returned_to_the_backups_path(self):
            a, swarm, backup, raced, aside = self.strand_copied_backup()
            line = f"left {aside}: {SCRAP_LEFT}"
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), line, "removed 0 links, restored 0 entries")
            self.assertFalse(os.path.lexists(backup))
            self.assert_displaced(aside, "directory")
            self.assert_displaced(swarm, "directory")
            swarm.rename(swarm.parent / "kept")
            self.ok(run(self.home, a, "--harness", "grok"), line, "linked 3 skills into grok")
            self.assertFalse(os.path.lexists(backup))
            self.assert_displaced(aside, "directory")
            self.assert_grok_text(a)
    def test_a_second_name_of_a_backup_left_in_a_holder_is_removed(self):
            a, swarm, backup, raced, aside = self.strand_backup(NO_PROC + raising("same_entry"), kind="file")
            self.assertTrue(os.path.samefile(aside, backup))
            rows = read_legacy(self.home)["backups"]
            self.ok(
                run(self.home, a, "--harness", "grok"),
                f"removed {aside}: it was a second name for {backup}",
                "linked 3 skills into grok",
            )
            self.assert_displaced(backup, "file")
            self.assertEqual(backup.stat().st_nlink, 1)
            self.assertEqual(read_legacy(self.home)["backups"], rows)
            self.assertEqual(self.holders(), [])
    def test_a_second_name_of_a_restored_entry_left_in_a_holder_is_removed_with_its_row(self):
            a, swarm, backup, raced, aside = self.strand_backup(raising("same_entry", within="drop_backup"), kind="file")
            self.assertTrue(os.path.samefile(aside, swarm))
            self.assertEqual([row["backup"] for row in read_legacy(self.home)["backups"]], [str(backup)])
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall"),
                f"removed {aside}: it was a second name for {swarm}",
                "removed 0 links, restored 0 entries",
            )
            self.assert_displaced(swarm, "file")
            self.assertEqual(swarm.stat().st_nlink, 1)
            self.assert_empty_records()
            self.assertEqual(self.holders(), [])
    def test_a_foreign_entry_in_a_holder_beside_a_path_this_checkout_claims_is_left(self):
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "grok"))
            swarm = provider_link(self.home, "grok", "swarm")
            self.assert_claims(a, grok_paths(self.home))
            stray = self.plant(swarm)
            line = f"left {stray}: it is not a link this checkout recorded at {swarm}"
            self.ok(run(self.home, a, "--harness", "grok"), "linked 0 skills into nothing (already installed)", line)
            self.assertEqual(os.readlink(swarm), str(a / "skills" / "swarm"))
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries", line)
            self.assertEqual(stray.read_bytes(), b"stray\x00\xfd")
            self.assertFalse(os.path.lexists(swarm))
    def test_this_checkouts_link_in_a_holder_whose_path_is_taken_is_removed(self):
            a, swarm, raced, aside = self.strand_link()
            swarm.write_bytes(b"occupant\x00\xff")
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall"),
                f"removed {aside}: it is this checkout's link and {swarm} is taken",
            )
            self.assertFalse(swarm.is_symlink())
            self.assertEqual(swarm.read_bytes(), b"occupant\x00\xff")
            self.assertEqual(self.holders(), [])
    def test_a_link_in_a_holder_that_no_record_of_this_checkout_names_is_left(self):
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            aside = swarm.parent / ".pstack-t3-unnamed0" / "swarm"
            aside.parent.mkdir(parents=True)
            os.symlink(a / "skills" / "swarm", aside)
            line = f"left {aside}: it is not a link this checkout recorded at {swarm}"
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), line, "removed 0 links, restored 0 entries")
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), line, "removed 0 links, restored 0 entries")
            self.assertEqual(os.readlink(aside), str(a / "skills" / "swarm"))
            self.assertFalse(os.path.lexists(swarm))
    def test_a_dry_run_reports_holders_and_changes_nothing(self):
            a, swarm, raced, aside = self.strand_link()
            stray = self.plant(swarm.parent / "notes")
            before = snapshot(self.home)
            lines = (
                f"would recover {swarm} from {aside}",
                f"left {stray}: it is not a link this checkout recorded at {swarm.parent / 'notes'}",
            )
            self.ok(run(self.home, a, "--harness", "grok", "uninstall", "--dry-run"), "would remove 0 links, would restore 0 entries", *lines)
            self.ok(run(self.home, a, "--harness", "grok", "--dry-run"), "3 links planned", *lines)
            self.assertEqual(snapshot(self.home), before)
    def test_a_dry_run_that_finds_a_holder_creates_no_state_directory(self):
            a = make_checkout(self.home, "a")
            stray = self.plant(provider_link(self.home, "grok", "notes"))
            line = f"left {stray}: it is not a link this checkout recorded at {provider_link(self.home, 'grok', 'notes')}"
            self.ok(run(self.home, a, "--harness", "grok", "uninstall", "--dry-run"), line)
            self.ok(run(self.home, a, "--harness", "grok", "--dry-run"), line)
            self.assertFalse(state_dir(self.home).exists())
            self.assertEqual(stray.read_bytes(), b"stray\x00\xfd")
    def test_a_run_with_no_holders_and_no_steps_creates_no_state_directory(self):
            a = make_checkout(self.home, "a")
            provider_link(self.home, "grok", "swarm").parent.mkdir(parents=True)
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
            self.assertFalse(state_dir(self.home).exists())
    def test_the_state_lock_descriptor_is_closed_when_the_unlock_raises(self):
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "grok"))
            count = self.home / "descriptors"
            raced = uninstall_hooked(self.home, a, unlock_failure(state_dir(self.home), count))
            self.ok(raced, "removed 3 links, restored 0 entries", f"unlock raised: [Errno {errno.EIO}] injected unlock failure")
            self.assertEqual(count.read_text(), "0")
    def test_an_older_uninstall_restores_a_backup_the_new_installer_returned(self):
            a, swarm, backup, raced, aside = self.strand_backup(KILLED_ASIDE, reason=in_process.Reason.INTERPRETER_EXIT)
            b = make_checkout(self.home, "b", old=True)
            swarm.write_bytes(b"occupant")
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall"),
                f"recovered {backup} from {aside}",
                "removed 0 links, restored 0 entries",
            )
            swarm.unlink()
            self.assert_displaced(backup, "directory")
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 0 links, restored 1 entries")
            self.assert_displaced(swarm, "directory")
            self.assertFalse(os.path.lexists(backup))
    def test_a_holder_beside_a_skills_directory_does_not_stop_an_older_installer(self):
            b = make_checkout(self.home, "b", old=True)
            stray = self.plant(provider_link(self.home, "grok", "swarm"))
            self.ok(run(self.home, b, "--harness", "grok"), "linked 3 skills into grok")
            self.assert_grok_text(b)
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
            self.assert_gone()
            self.assertEqual(stray.read_bytes(), b"stray\x00\xfd")
    def test_a_run_for_one_harness_leaves_a_backup_of_another_harness_in_its_holder(self):
            a, swarm, backup, raced, aside = self.strand_backup(raising("same_entry"))
            other = run(self.home, a, "--harness", "claude", "uninstall")
            self.ok(other)
            self.assertEqual(other.stdout.splitlines(), ["removed 0 links, restored 0 entries"])
            self.assertFalse(os.path.lexists(backup))
            self.assert_displaced(aside, "directory")
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall"),
                f"recovered {backup} from {aside}",
                "removed 0 links, restored 1 entries",
            )
            self.assert_displaced(swarm, "directory")
            self.assertEqual(self.holders(), [])
    def test_a_run_for_one_harness_leaves_the_holders_in_the_skills_directory_of_another(self):
            a = make_checkout(self.home, "a")
            notes = provider_link(self.home, "grok", "notes")
            stray = self.plant(notes)
            empty = notes.parent / ".pstack-t3-empty000"
            empty.mkdir()
            removed = run(self.home, a, "--harness", "claude", "uninstall")
            self.ok(removed)
            self.assertEqual(removed.stdout.splitlines(), ["removed 0 links, restored 0 entries"])
            linked = run(self.home, a, "--harness", "claude")
            self.ok(linked)
            self.assertEqual(linked.stdout.splitlines(), ["linked 3 skills into claude", f"manifest: {legacy_file(self.home)}"])
            self.assertEqual(self.holders(), [str(empty), str(stray.parent)])
            self.assertEqual(stray.read_bytes(), b"stray\x00\xfd")
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall"),
                f"left {stray}: it is not a link this checkout recorded at {notes}",
                "removed 0 links, restored 0 entries",
            )
            self.assertEqual(self.holders(), [str(stray.parent)])
    def test_a_run_for_one_harness_of_a_shared_directory_returns_a_backup_filed_under_the_other(self):
            a = make_checkout(self.home, "a")
            agents = self.share_cursor_with_agents()
            self.ok(run(self.home, a, "--harness", "cursor", "--replace"), "linked 3 skills into codex, cursor")
            backup = Path(read_legacy(self.home)["backups"][0]["backup"])
            self.assertEqual(backup.parent.name, "codex")
            aside = backup.parent / ".pstack-t3-held0000" / "swarm"
            aside.parent.mkdir()
            backup.rename(aside)
            other = run(self.home, a, "--harness", "claude", "uninstall")
            self.ok(other)
            self.assertEqual(other.stdout.splitlines(), ["removed 0 links, restored 0 entries"])
            self.assertEqual(aside.read_bytes(), b"foreign\x00file\n")
            self.ok(
                run(self.home, a, "--harness", "cursor", "uninstall"),
                f"recovered {backup} from {aside}",
                "removed 0 links, restored 0 entries",
                "kept entries whose directory is shared with codex; select those harnesses too to remove them",
            )
            self.assertEqual(backup.read_bytes(), b"foreign\x00file\n")
            self.assertEqual(os.readlink(agents / "swarm"), str(a / "skills" / "swarm"))
            self.assertEqual(self.holders(), [])
    def test_an_install_refused_for_a_taken_path_reports_only_the_entries_it_leaves_in_holders(self):
            with self.subTest(held="an entry no record proves"):
                a = make_checkout(self.home, "a")
                swarm = provider_link(self.home, "grok", "swarm")
                stray = self.plant(swarm.parent / "notes")
                swarm.write_bytes(b"occupant\x00\xff")
                before = snapshot(self.home)
                refused = run(self.home, a, "--harness", "grok")
                self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
                self.assertEqual(
                    refused.stdout.splitlines(),
                    [f"left {stray}: it is not a link this checkout recorded at {swarm.parent / 'notes'}"],
                )
                self.assertIn("these skills already exist", refused.stderr)
                self.assertEqual(snapshot(self.home), before)
                self.assertFalse(state_dir(self.home).exists())
            with self.subTest(held="this checkout's link"):
                self.use_fresh()
                a, swarm, raced, aside = self.strand_link()
                provider_link(self.home, "grok", "alpha").write_bytes(b"occupant\x00\xff")
                before = snapshot(self.home)
                refused = run(self.home, a, "--harness", "grok")
                self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
                self.assertEqual(refused.stdout, "")
                self.assertIn("these skills already exist", refused.stderr)
                self.assertEqual(snapshot(self.home), before)
                self.assertEqual(os.readlink(aside), str(a / "skills" / "swarm"))
