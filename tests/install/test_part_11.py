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


class Part11Test(OwnershipCase):
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
    def holders(self):
            """Every hidden directory an installer made to move an entry through, anywhere under the home."""
            return sorted(str(path) for path in self.home.rglob(".pstack-t3-*"))
    def plant(self, beside, holder=".pstack-t3-unproven"):
            """A file in a holder beside `beside` that no installer put there."""
            stray = beside.parent / holder / beside.name
            stray.parent.mkdir(parents=True)
            stray.write_bytes(b"stray\x00\xfd")
            return stray
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
    def test_a_run_that_only_leaves_entries_creates_no_state_directory(self):
            a = make_checkout(self.home, "a")
            notes = provider_link(self.home, "grok", "notes")
            stray = self.plant(notes)
            line = f"left {stray}: it is not a link this checkout recorded at {notes}"
            removed = run(self.home, a, "--harness", "grok", "uninstall")
            self.ok(removed)
            self.assertEqual(removed.stdout.splitlines(), [line, "removed 0 links, restored 0 entries"])
            self.assertFalse(state_dir(self.home).exists())
            plant_links(self.home, a)
            linked = run(self.home, a, "--harness", "grok")
            self.ok(linked)
            self.assertEqual(
                linked.stdout.splitlines(),
                [line, "linked 0 skills into nothing (already installed)", f"3 {UNTRACKED}", f"manifest: {legacy_file(self.home)}"],
            )
            self.assertFalse(state_dir(self.home).exists())
            self.assertEqual(stray.read_bytes(), b"stray\x00\xfd")
    def test_a_run_that_only_leaves_entries_waits_for_the_lock_of_a_state_directory_that_exists(self):
            a = make_checkout(self.home, "a")
            notes = provider_link(self.home, "grok", "notes")
            stray = self.plant(notes)
            state_dir(self.home).mkdir(parents=True)
            lock = os.open(state_dir(self.home), os.O_RDONLY)
            self.addCleanup(os.close, lock)
            fcntl.flock(lock, fcntl.LOCK_EX)
            waiting = in_process.spawn(in_process.Reason.RACE, in_process.Invocation(
                program=a / "scripts" / "install.py",
                argv=("--harness", "grok", "uninstall"),
                env=_env(self.home),
                cwd=self.home,
            ))
            self.addCleanup(waiting.kill)
            with self.assertRaises(subprocess.TimeoutExpired):
                waiting.communicate(timeout=1)
            fcntl.flock(lock, fcntl.LOCK_UN)
            stdout, stderr = waiting.communicate(timeout=30)
            self.assertEqual(waiting.returncode, 0, stderr + stdout)
            self.assertEqual(
                stdout.splitlines(),
                [f"left {stray}: it is not a link this checkout recorded at {notes}", "removed 0 links, restored 0 entries"],
            )
            self.assertEqual(stray.read_bytes(), b"stray\x00\xfd")
    def test_an_entry_at_the_path_of_a_backup_left_in_a_holder_is_not_restored_in_its_place(self):
            a, swarm, backup, raced, aside = self.strand_backup(raising("same_entry"))
            backup.write_bytes(b"occupant\x00\xff")
            self.ok(run(self.home, a, "--harness", "grok"), "linked 3 skills into grok")
            rows = read_legacy(self.home)["backups"]
            lines = [
                f"left {aside}: {backup} is the recorded backup of {swarm} and another entry is there now",
                CONTESTED.format(backup=backup, aside=aside),
            ]
            planned = run(self.home, a, "--harness", "grok", "uninstall", "--dry-run")
            self.ok(planned)
            self.assertEqual(planned.stdout.splitlines(), lines + ["would remove 3 links, would restore 0 entries"])
            removed = run(self.home, a, "--harness", "grok", "uninstall")
            self.ok(removed)
            self.assertEqual(removed.stdout.splitlines(), lines + ["removed 3 links, restored 0 entries"])
            self.assertFalse(os.path.lexists(swarm))
            self.assertEqual(backup.read_bytes(), b"occupant\x00\xff")
            self.assert_displaced(aside, "directory")
            self.assertEqual(read_legacy(self.home)["backups"], rows)
            backup.unlink()
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall"),
                f"recovered {backup} from {aside}",
                "removed 0 links, restored 1 entries",
            )
            self.assert_displaced(swarm, "directory")
            self.assert_empty_records()
            self.assertEqual(self.holders(), [])
    def test_this_checkouts_link_at_the_path_of_a_backup_left_in_a_holder_is_not_withdrawn(self):
            a, swarm, backup, raced, aside = self.strand_backup(raising("same_entry"))
            os.symlink(a / "skills" / "swarm", backup)
            self.ok(run(self.home, a, "--harness", "grok"), "linked 3 skills into grok")
            rows = read_legacy(self.home)["backups"]
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall"),
                f"left {aside}: {backup} is the recorded backup of {swarm} and another entry is there now",
                CONTESTED.format(backup=backup, aside=aside),
                "removed 3 links, restored 0 entries",
            )
            self.assertFalse(os.path.lexists(swarm))
            self.assertEqual(os.readlink(backup), str(a / "skills" / "swarm"))
            self.assert_displaced(aside, "directory")
            self.assertEqual(read_legacy(self.home)["backups"], rows)
    def test_an_entry_at_the_path_of_a_copied_backup_left_in_a_scrap_holder_is_not_restored(self):
            a, swarm, backup, raced, aside = self.strand_copied_backup()
            rows = read_legacy(self.home)["backups"]
            self.assertEqual([row["backup"] for row in rows], [str(backup)])
            backup.write_bytes(b"occupant\x00\xff")
            shutil.rmtree(swarm)
            removed = run(self.home, a, "--harness", "grok", "uninstall")
            self.ok(removed)
            self.assertEqual(
                removed.stdout.splitlines(),
                [f"left {aside}: {SCRAP_LEFT}", CONTESTED.format(backup=backup, aside=aside), "removed 0 links, restored 0 entries"],
            )
            self.assertFalse(os.path.lexists(swarm))
            self.assertEqual(backup.read_bytes(), b"occupant\x00\xff")
            self.assert_displaced(aside, "directory")
            self.assertEqual(read_legacy(self.home)["backups"], rows)
    def test_an_uninstall_that_fails_to_remove_one_of_three_links_exits_3_and_removes_the_other_two(self):
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "grok"), "linked 3 skills into grok")
            swarm = provider_link(self.home, "grok", "swarm")
            failed = run_failing(self.home, a, "os.rename", swarm, "--harness", "grok", "uninstall")
            self.assertEqual(failed.returncode, 3, failed.stdout + failed.stderr)
            self.assertEqual(
                failed.stdout.splitlines(),
                [
                    f"skipped unlink {swarm}: [Errno 13] Permission denied: '{swarm}'",
                    "removed 2 links, restored 0 entries",
                    KEPT.format(n=1),
                ],
            )
            self.assertEqual(os.readlink(swarm), str(a / "skills" / "swarm"))
            self.assert_gone(("alpha", "pstack-runtime"))
    def test_an_install_that_fails_to_create_one_of_three_links_exits_3_and_creates_the_other_two(self):
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            failed = run_failing(self.home, a, "os.symlink", swarm, "--harness", "grok")
            self.assertEqual(failed.returncode, 3, failed.stdout + failed.stderr)
            self.assertEqual(
                failed.stdout.splitlines(),
                [
                    f"skipped link {swarm}: [Errno 13] Permission denied: '{swarm}'",
                    "linked 2 skills into grok",
                    f"manifest: {legacy_file(self.home)}",
                ],
            )
            self.assertFalse(os.path.lexists(swarm))
            self.assert_grok_text(a, ("alpha", "pstack-runtime"))
    def test_an_unknown_harness_exits_2(self):
            a = make_checkout(self.home, "a")
            refused = run(self.home, a, "--harness", "grok,nope")
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            self.assertIn("unknown harness nope", refused.stderr)
            self.assertFalse(state_dir(self.home).exists())
    def replaced_file(self, harnesses=("grok",)):
            """Checkout a after one `--replace` run moved a user's swarm file aside for each harness."""
            a = make_checkout(self.home, "a")
            for harness in harnesses:
                swarm = provider_link(self.home, harness, "swarm")
                swarm.parent.mkdir(parents=True)
                swarm.write_bytes(f"mine\x00{harness}\n".encode())
            self.ok(run(self.home, a, "--harness", ",".join(harnesses), "--replace"), f"linked {3 * len(harnesses)} skills into {', '.join(sorted(harnesses))}")
            return a
    def under_backups(self):
            backups = state_dir(self.home) / "backups"
            return sorted(str(path.relative_to(backups)) for path in backups.rglob("*"))
    def test_an_uninstall_after_a_dry_run_restores_a_replaced_file_and_leaves_backups_empty(self):
            a = self.replaced_file()
            swarm = provider_link(self.home, "grok", "swarm")
            before = snapshot(self.home)
            self.ok(run(self.home, a, "--harness", "grok", "uninstall", "--dry-run"), "would remove 3 links, would restore 1 entries")
            self.assertEqual(snapshot(self.home), before)
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
            self.assertFalse(swarm.is_symlink())
            self.assertEqual(swarm.read_bytes(), b"mine\x00grok\n")
            self.assertTrue((state_dir(self.home) / "backups").is_dir())
            self.assertEqual(self.under_backups(), [])
    def test_an_uninstall_of_one_harness_removes_its_backup_directory_and_keeps_the_other_harness_backup(self):
            a = self.replaced_file(("codex", "grok"))
            stamps = self.under_backups()
            self.assertEqual(len(stamps), 5, stamps)
            stamp = stamps[0]
            self.assertEqual(stamps, [stamp, f"{stamp}/codex", f"{stamp}/codex/swarm", f"{stamp}/grok", f"{stamp}/grok/swarm"])
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
            self.assertEqual(self.under_backups(), [stamp, f"{stamp}/codex", f"{stamp}/codex/swarm"])
            self.assertEqual((state_dir(self.home) / "backups" / stamp / "codex" / "swarm").read_bytes(), b"mine\x00codex\n")
            self.ok(run(self.home, a, "--harness", "codex", "uninstall"), "removed 3 links, restored 1 entries")
            self.assertEqual(provider_link(self.home, "codex", "swarm").read_bytes(), b"mine\x00codex\n")
            self.assertEqual(provider_link(self.home, "grok", "swarm").read_bytes(), b"mine\x00grok\n")
            self.assertTrue((state_dir(self.home) / "backups").is_dir())
            self.assertEqual(self.under_backups(), [])
    def test_withdrawing_links_another_checkout_moved_aside_removes_the_emptied_backup_directories(self):
            a, b = self.base()
            self.assertEqual(len(self.under_backups()), 5)
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall"),
                "removed 3 links, restored 0 entries",
                "3 of them had been moved aside by another checkout's --replace",
            )
            self.assert_grok_text(b)
            self.assertTrue((state_dir(self.home) / "backups").is_dir())
            self.assertEqual(self.under_backups(), [])
    def test_the_sweep_that_finishes_an_interrupted_restore_removes_the_emptied_backup_directories(self):
            a, swarm, backup, raced, aside = self.strand_backup(raising("same_entry", within="drop_backup"), kind="file")
            self.assertTrue(os.path.samefile(aside, swarm))
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall"),
                f"removed {aside}: it was a second name for {swarm}",
                "removed 0 links, restored 0 entries",
            )
            self.assert_displaced(swarm, "file")
            self.assertTrue((state_dir(self.home) / "backups").is_dir())
            self.assertEqual(self.under_backups(), [])
    def test_a_file_a_person_put_beside_a_backup_keeps_its_directory_after_the_restore(self):
            a = self.replaced_file()
            stamp = self.under_backups()[0]
            note = state_dir(self.home) / "backups" / stamp / "grok" / "notes.txt"
            note.write_bytes(b"keep\x00me\n")
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
            self.assertEqual(provider_link(self.home, "grok", "swarm").read_bytes(), b"mine\x00grok\n")
            self.assertEqual(self.under_backups(), [stamp, f"{stamp}/grok", f"{stamp}/grok/notes.txt"])
            self.assertEqual(note.read_bytes(), b"keep\x00me\n")
    def test_a_restore_from_a_backup_outside_the_stamp_and_harness_layout_leaves_its_parent_directory(self):
            places = (
                ("outside the state directory", lambda: self.home / "elsewhere" / "keep"),
                ("directly under a stamp", lambda: state_dir(self.home) / "backups" / "20260101T000000-1-abcd"),
            )
            for where, parent in places:
                with self.subTest(where=where):
                    self.use_fresh()
                    a = make_checkout(self.home, "a")
                    swarm = provider_link(self.home, "grok", "swarm")
                    saved = parent() / "swarm"
                    saved.parent.mkdir(parents=True)
                    saved.write_bytes(b"saved\x00\xfe")
                    write_legacy(self.home, [], [{"harnesses": ["grok"], "original": str(swarm), "backup": str(saved)}])
                    self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 1 entries")
                    self.assertEqual(swarm.read_bytes(), b"saved\x00\xfe")
                    self.assertFalse(os.path.lexists(saved))
                    self.assertTrue(saved.parent.is_dir())
                    self.assertEqual(os.listdir(saved.parent), [])
