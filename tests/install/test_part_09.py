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


class Part09Test(OwnershipCase):
    def assert_displaced(self, path, kind):
            if kind == "directory":
                self.assertEqual(os.listdir(path), ["SKILL.md"])
                self.assertEqual((path / "SKILL.md").read_bytes(), b"displaced\x00\xfe")
            elif kind == "link":
                self.assertEqual(os.readlink(path), "/foreign/displaced")
            else:
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.read_bytes(), b"displaced\x00\xfe")
    def assert_no_copy_left(self, swarm):
            self.assertEqual(list(swarm.parent.glob(".pstack-t3-*")), [])
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
    def test_a_directory_restore_preserves_hard_links_xattrs_fifo_modes_and_relative_symlinks(self):
            """A same-filesystem directory move keeps hard links, FIFO type, modes, and relative symlink targets, and xattrs where the platform offers them."""
            self.use_fresh()
            a, swarm, rows = self.displaced_by("directory")
            backup = Path(rows[0]["backup"])
            os.chmod(backup, 0o751)
            os.chmod(backup / "SKILL.md", 0o640)
            (backup / "nested").mkdir(mode=0o711)
            os.chmod(backup / "nested", 0o711)
            (backup / "nested" / "deep").write_bytes(b"deep")
            os.symlink("../SKILL.md", backup / "nested" / "alias")
            os.mkfifo(backup / "pipe")
            os.chmod(backup / "pipe", 0o620)
            os.link(backup / "SKILL.md", backup / "hard")
            # Linux offers user xattrs. macOS offers xattrs through libc. Where neither works omit xattr asserts but keep other checks.
            can_xattr = extended_attribute(backup / "SKILL.md", "user.review", b"metadata")
            raced = run(self.home, a, "--harness", "grok", "uninstall")
            self.ok(raced, "removed 3 links, restored 1 entries")
            self.assertEqual(stat.S_IMODE(swarm.stat().st_mode), 0o751)
            self.assertEqual((swarm / "SKILL.md").read_bytes(), b"displaced\x00\xfe")
            self.assertEqual(stat.S_IMODE((swarm / "SKILL.md").stat().st_mode), 0o640)
            if can_xattr:
                self.assertEqual(extended_attribute(swarm / "SKILL.md", "user.review"), b"metadata")
            self.assertTrue(os.path.samefile(swarm / "SKILL.md", swarm / "hard"))
            self.assertEqual(stat.S_IMODE((swarm / "nested").stat().st_mode), 0o711)
            self.assertEqual((swarm / "nested" / "deep").read_bytes(), b"deep")
            self.assertEqual(os.readlink(swarm / "nested" / "alias"), "../SKILL.md")
            self.assertTrue(stat.S_ISFIFO((swarm / "pipe").stat().st_mode))
            self.assertFalse(os.path.lexists(backup))
            self.assert_no_copy_left(swarm)
            self.assert_empty_records()
    def test_an_entry_that_takes_a_withdrawn_backups_path_is_never_deleted(self):
            a, b = self.base()
            rows = read_legacy(self.home)["backups"]
            swarm = provider_link(self.home, "grok", "swarm")
            backup = Path(next(row["backup"] for row in rows if row["original"] == str(swarm)))
            raced = uninstall_hooked(self.home, a, put_back_race(backup, "link"))
            self.skipped(
                raced,
                "removed 2 links, restored 0 entries",
                f"skipped withdraw {backup}: the backup no longer matches the recorded checkout",
            )
            self.assertEqual(os.readlink(backup), "/foreign/swarm")
            self.assertEqual(os.listdir(backup.parent), [backup.name])
            self.assertEqual(read_legacy(self.home)["backups"], [row for row in rows if row["backup"] == str(backup)])
            self.assert_claims(a, [str(swarm)])
            self.assert_grok_text(b)
    def holders(self):
            """Every hidden directory an installer made to move an entry through, anywhere under the home."""
            return sorted(str(path) for path in self.home.rglob(".pstack-t3-*"))
    def strand_backup(self, hook, kind="directory", *, reason=None):
            """Checkout a's uninstall, stopped by `hook` with the backup of a displaced swarm left aside."""
            a, swarm, rows = self.displaced_by(kind)
            backup = Path(rows[0]["backup"])
            raced = uninstall_hooked(self.home, a, hook, reason=reason)
            aside = set_aside(backup)
            self.assertEqual(len(aside), 1, raced.stdout + raced.stderr)
            self.assert_displaced(aside[0], kind)
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
    def plant(self, beside, holder=".pstack-t3-unproven"):
            """A file in a holder beside `beside` that no installer put there."""
            stray = beside.parent / holder / beside.name
            stray.parent.mkdir(parents=True)
            stray.write_bytes(b"stray\x00\xfd")
            return stray
    def test_a_restore_that_raises_names_where_the_backup_is_kept(self):
            with self.subTest(stage="directory moved aside"):
                a, swarm, backup, raced, aside = self.strand_backup(raising("same_entry"))
                self.skipped(
                    raced,
                    "removed 3 links, restored 0 entries",
                    f"skipped restore {backup}: {injected('same_entry')}; it is kept at {aside}",
                )
                self.assertFalse(os.path.lexists(swarm))
            with self.subTest(stage="backup already linked to its path"):
                self.use_fresh()
                a, swarm, backup, raced, aside = self.strand_backup(raising("same_entry", within="drop_backup"), kind="file")
                self.skipped(
                    raced,
                    "removed 3 links, restored 0 entries",
                    f"skipped restore {backup}: {injected('same_entry')}; it is kept at {aside}",
                )
                self.assert_displaced(swarm, "file")
    def test_a_put_back_that_raises_names_where_the_entry_is_kept(self):
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "grok"))
            swarm = provider_link(self.home, "grok", "swarm")
            raced = uninstall_hooked(self.home, a, put_back_race(swarm, "link") + raising("put_back"))
            aside = set_aside(swarm)
            self.assertEqual(len(aside), 1, raced.stdout + raced.stderr)
            self.skipped(
                raced,
                "removed 2 links, restored 0 entries",
                f"skipped unlink {swarm}: {injected('put_back')}; it is kept at {aside[0]}",
            )
            self.assertEqual(os.readlink(aside[0]), "/foreign/swarm")
            self.assertFalse(os.path.lexists(swarm))
    def test_a_discard_that_raises_after_a_copy_landed_names_where_the_backup_is_kept(self):
            a, swarm, backup, raced, aside = self.strand_copied_backup()
            self.skipped(
                raced,
                "removed 3 links, restored 0 entries",
                f"skipped restore {backup}: {injected('discard')}; it is kept at {aside}",
            )
    def test_a_link_removal_that_raises_names_where_the_link_is_kept(self):
            a, swarm, raced, aside = self.strand_link()
            self.skipped(
                raced,
                "removed 2 links, restored 0 entries",
                f"skipped unlink {swarm}: {injected('unlink')}; it is kept at {aside}",
            )
    def test_a_later_uninstall_restores_a_backup_left_in_a_holder(self):
            for stopped, hook, reason in (
                ("error", raising("same_entry"), None),
                ("kill", KILLED_ASIDE, in_process.Reason.INTERPRETER_EXIT),
            ):
                with self.subTest(stopped=stopped):
                    self.use_fresh()
                    a, swarm, backup, raced, aside = self.strand_backup(hook, reason=reason)
                    self.assertEqual(raced.returncode, 9 if stopped == "kill" else 3, raced.stdout + raced.stderr)
                    self.ok(
                        run(self.home, a, "--harness", "grok", "uninstall"),
                        f"recovered {backup} from {aside}",
                        "removed 0 links, restored 1 entries",
                    )
                    self.assert_displaced(swarm, "directory")
                    self.assertFalse(os.path.lexists(backup))
                    self.assert_empty_records()
                    self.assertEqual(self.holders(), [])
    def test_a_later_install_returns_a_backup_left_in_a_holder_before_it_links(self):
            a, swarm, backup, raced, aside = self.strand_backup(raising("same_entry"))
            self.ok(
                run(self.home, a, "--harness", "grok"),
                f"recovered {backup} from {aside}",
                "linked 3 skills into grok",
            )
            self.assert_displaced(backup, "directory")
            self.assert_grok_text(a)
            self.assertEqual(self.holders(), [])
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
            self.assert_displaced(swarm, "directory")
            self.assert_empty_records()
    def test_a_later_uninstall_returns_this_checkouts_link_left_in_a_holder(self):
            a, swarm, raced, aside = self.strand_link()
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall"),
                f"recovered {swarm} from {aside}",
                "removed 1 links, restored 0 entries",
            )
            self.assert_gone()
            self.assert_empty_records()
            self.assertEqual(self.holders(), [])
    def test_a_later_install_returns_this_checkouts_link_left_in_a_holder(self):
            a, swarm, raced, aside = self.strand_link()
            self.ok(
                run(self.home, a, "--harness", "grok"),
                f"recovered {swarm} from {aside}",
                "linked 2 skills into grok",
            )
            self.assert_grok_text(a)
            self.assert_claims(a, grok_paths(self.home))
            self.assertEqual([row["path"] for row in read_legacy(self.home)["links"]].count(str(swarm)), 1)
            self.assertEqual(self.holders(), [])
    def test_an_entry_no_record_proves_is_left_and_reported_on_every_run(self):
            a, swarm, rows = self.displaced_by("file")
            backup = Path(rows[0]["backup"])
            beside_skill = self.plant(swarm.parent / "notes")
            beside_backup = self.plant(backup.parent / "notes")
            lines = (
                f"left {beside_skill}: it is not a link this checkout recorded at {swarm.parent / 'notes'}",
                f"left {beside_backup}: no backup record names {backup.parent / 'notes'}",
            )
            self.ok(run(self.home, a, "--harness", "grok"), "linked 0 skills into nothing (already installed)", *lines)
            self.ok(run(self.home, a, "--harness", "grok"), "linked 0 skills into nothing (already installed)", *lines)
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries", *lines)
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries", *lines)
            self.assertEqual(beside_skill.read_bytes(), b"stray\x00\xfd")
            self.assertEqual(beside_backup.read_bytes(), b"stray\x00\xfd")
            self.assertFalse(os.path.lexists(swarm.parent / "notes"))
            self.assertFalse(os.path.lexists(backup.parent / "notes"))
    def test_an_entry_whose_recorded_path_is_taken_is_never_placed_over_the_occupant(self):
            a, swarm, backup, raced, aside = self.strand_backup(raising("same_entry"))
            backup.write_bytes(b"occupant\x00\xff")
            line = f"left {aside}: {backup} is the recorded backup of {swarm} and another entry is there now"
            self.ok(run(self.home, a, "--harness", "grok"), line, "linked 3 skills into grok")
            self.ok(run(self.home, a, "--harness", "grok"), line, "linked 0 skills into nothing (already installed)")
            self.assert_displaced(aside, "directory")
            self.assertEqual(backup.read_bytes(), b"occupant\x00\xff")
    def test_empty_holders_are_removed_without_a_line(self):
            a, swarm, rows = self.displaced_by("file")
            backup = Path(rows[0]["backup"])
            for parent in (swarm.parent, backup.parent):
                (parent / ".pstack-t3-empty000").mkdir()
                (parent / ".pstack-t3-scrap-empty000").mkdir()
            result = run(self.home, a, "--harness", "grok")
            self.ok(result)
            self.assertEqual(
                result.stdout.splitlines(),
                ["linked 0 skills into nothing (already installed)", f"manifest: {legacy_file(self.home)}"],
            )
            self.assertEqual(self.holders(), [])
            self.assert_displaced(backup, "file")
            self.assert_grok_text(a)
