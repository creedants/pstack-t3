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


class Part07Test(OwnershipCase):
    def assert_displaced(self, path, kind):
            if kind == "directory":
                self.assertEqual(os.listdir(path), ["SKILL.md"])
                self.assertEqual((path / "SKILL.md").read_bytes(), b"displaced\x00\xfe")
            elif kind == "link":
                self.assertEqual(os.readlink(path), "/foreign/displaced")
            else:
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.read_bytes(), b"displaced\x00\xfe")
    def assert_restore_kept(self, raced, swarm, rows, kind, reason):
            """The raced restore saw the occupant arrive, left no set-aside copy, and kept the backup and its row where they were."""
            self.assertEqual((self.home / "filled").read_text(), "True")
            backup = Path(rows[0]["backup"])
            self.skipped(raced, "removed 3 links, restored 0 entries", f"skipped restore {backup}: {reason}")
            self.assert_displaced(backup, kind)
            self.assertEqual(os.listdir(backup.parent), [backup.name])
            self.assertEqual(read_legacy(self.home)["backups"], rows)
            self.assertEqual(set_aside(swarm), [])
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
    def noreplace(self, rename):
            """What the no-replace rename returned in the last hooked run. `symbol` and `libc` must have taken it away."""
            code = noreplace_result(self.home)
            if rename in ("symbol", "libc"):
                self.assertEqual(code, errno.ENOSYS)
            else:
                self.assertEqual(code, 0)
            return code
    def cross_device_uninstall(self, swarm, a, occupy=None, rename=None, crossed=True):
            hook = cross_device(state_dir(self.home) / "backups", swarm, occupy=occupy, rename=rename)
            raced = uninstall_hooked(self.home, a, hook)
            if crossed:
                self.assertGreater(crossings(self.home), 0, raced.stdout + raced.stderr)
            return raced
    def assert_no_copy_left(self, swarm):
            self.assertEqual(list(swarm.parent.glob(".pstack-t3-*")), [])
    def test_a_restore_across_filesystems_copies_the_backup_into_place(self):
            times = (1_500_000_000_123_456_789, 1_600_000_000_123_456_789)
            for kind in ("file", "link", "directory"):
                with self.subTest(kind=kind):
                    self.use_fresh()
                    a, swarm, rows = self.displaced_by(kind)
                    backup = Path(rows[0]["backup"])
                    if kind == "file":
                        os.chmod(backup, 0o604)
                        os.utime(backup, ns=times)
                        # A filesystem with whole-second timestamps keeps only the seconds.
                        stored = backup.stat().st_mtime_ns
                    elif kind == "directory":
                        os.symlink("SKILL.md", backup / "alias")
                        os.symlink("/foreign/dangling", backup / "dangling")
                        os.chmod(backup / "SKILL.md", 0o604)
                        os.utime(backup / "SKILL.md", ns=times)
                        stored = (backup / "SKILL.md").stat().st_mtime_ns
                    raced = self.cross_device_uninstall(swarm, a)
                    self.ok(raced, "removed 3 links, restored 1 entries")
                    if kind == "file":
                        self.assert_displaced(swarm, kind)
                        self.assertEqual(swarm.stat().st_mode & 0o777, 0o604)
                        self.assertEqual(swarm.stat().st_mtime_ns, stored)
                    elif kind == "link":
                        self.assert_displaced(swarm, kind)
                    else:
                        self.assertFalse(swarm.is_symlink())
                        self.assertEqual(sorted(os.listdir(swarm)), ["SKILL.md", "alias", "dangling"])
                        self.assertEqual((swarm / "SKILL.md").read_bytes(), b"displaced\x00\xfe")
                        self.assertEqual((swarm / "SKILL.md").stat().st_mode & 0o777, 0o604)
                        self.assertEqual((swarm / "SKILL.md").stat().st_mtime_ns, stored)
                        self.assertEqual(os.readlink(swarm / "alias"), "SKILL.md")
                        self.assertEqual(os.readlink(swarm / "dangling"), "/foreign/dangling")
                    self.assertFalse(os.path.lexists(backup))
                    self.assert_no_copy_left(swarm)
                    self.assert_empty_records()
    def test_a_restore_across_filesystems_never_replaces_an_entry_that_took_the_path(self):
            for kind in ("file", "link", "directory"):
                for occupy in ("file", "directory"):
                    with self.subTest(kind=kind, occupy=occupy):
                        self.use_fresh()
                        a, swarm, rows = self.displaced_by(kind)
                        backup = Path(rows[0]["backup"])
                        raced = self.cross_device_uninstall(swarm, a, occupy=occupy)
                        self.assert_restore_kept(raced, swarm, rows, kind, f"{swarm} was taken again")
                        self.assert_no_copy_left(swarm)
                        if occupy == "file":
                            self.assertEqual(swarm.read_bytes(), b"occupant\x00\xff")
                            swarm.unlink()
                        else:
                            self.assertEqual(os.listdir(swarm), ["precious"])
                            self.assertEqual((swarm / "precious").read_bytes(), b"directory bytes\x00")
                            shutil.rmtree(swarm)
                        self.ok(self.cross_device_uninstall(swarm, a), "removed 0 links, restored 1 entries")
                        self.assert_displaced(swarm, kind)
                        self.assertFalse(os.path.lexists(backup))
                        self.assert_no_copy_left(swarm)
                        self.assert_empty_records()
    def test_a_restore_across_filesystems_with_no_safe_rename_keeps_the_backup(self):
            for rename in ("symbol", "libc"):
                with self.subTest(rename=rename):
                    self.use_fresh()
                    a, swarm, rows = self.displaced_by("directory")
                    backup = Path(rows[0]["backup"])
                    # With no safe rename the directory never leaves its place, so no move is tried.
                    raced = self.cross_device_uninstall(swarm, a, rename=rename, crossed=False)
                    self.assertEqual(crossings(self.home), 0)
                    code = self.noreplace(rename)
                    self.skipped(
                        raced,
                        "removed 3 links, restored 0 entries",
                        f"skipped restore {backup}: {swarm} cannot be refilled without risking an overwrite ({os.strerror(code)})",
                    )
                    self.assertFalse(os.path.lexists(swarm))
                    self.assert_displaced(backup, "directory")
                    self.assertEqual(read_legacy(self.home)["backups"], rows)
                    self.assert_no_copy_left(swarm)
    def test_renamex_np_dispatch_uses_excl_flag(self):
            """Darwin has no `renameat2`. Its `renamex_np` must take flag 4."""
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "grok"))
            swarm = provider_link(self.home, "grok", "swarm")
            hook = darwin_renamex() + put_back_race(swarm, "directory", occupy="directory", rename=None)
            raced = uninstall_hooked(self.home, a, hook)
            self.assertEqual(noreplace_result(self.home), 0)
            aside = set_aside(swarm)
            self.assertEqual(len(aside), 1, aside)
            self.skipped(
                raced,
                "removed 2 links, restored 0 entries",
                f"skipped unlink {swarm}: the link no longer matches the recorded checkout and {swarm} was taken again; "
                f"it is kept at {aside[0]}",
            )
    def test_a_directory_restore_through_renamex_np_restores_the_backup(self):
            """A directory backup restores where only `renamex_np` gives a safe rename."""
            a, swarm, rows = self.displaced_by("directory")
            backup = Path(rows[0]["backup"])
            raced = uninstall_hooked(self.home, a, darwin_renamex() + NO_PROC)
            self.ok(raced, "removed 3 links, restored 1 entries")
            self.assert_displaced(swarm, "directory")
            self.assertFalse(os.path.lexists(backup))
            self.assert_empty_records()
    def test_a_file_restore_with_every_hard_link_refused_restores_through_renamex_np(self):
            """A file backup restores through `renamex_np` where every hard link is refused."""
            a, swarm, rows = self.displaced_by("file")
            backup = Path(rows[0]["backup"])
            denied = (
                "import errno\n"
                "def denied(*args, **kwargs):\n"
                "    raise OSError(errno.EPERM, 'hard links prohibited')\n"
                "os.link = denied\n"
            )
            raced = uninstall_hooked(self.home, a, darwin_renamex() + denied)
            self.ok(raced, "removed 3 links, restored 1 entries")
            self.assert_displaced(swarm, "file")
            self.assertFalse(os.path.lexists(backup))
            self.assert_empty_records()
    def backup_race_uninstall(self, a, swarm, backup, *events, crossing=False, **options):
            """Uninstall with every event fired against the one backup. `crossing` puts the backups on another filesystem. `crossing` as "refused" asserts that no move left the backups by name and that no copy is left, instead of asserting that a crossing happened."""
            hook = backup_race(backup, swarm, events, **options)
            if crossing:
                hook = cross_device(state_dir(self.home) / "backups", swarm) + hook
            raced = uninstall_hooked(self.home, a, hook)
            self.assertEqual(fired(self.home), len(events), raced.stdout + raced.stderr)
            if crossing == "refused":
                self.assertEqual(crossings_by_name(self.home), 0, raced.stdout + raced.stderr)
                self.assert_no_copy_left(swarm)
            elif crossing:
                self.assertGreater(crossings(self.home), 0, raced.stdout + raced.stderr)
                self.assert_no_copy_left(swarm)
            return raced
    def assert_foreign(self, path, foreign="file"):
            if foreign == "directory":
                self.assertEqual(os.listdir(path), ["precious"])
                self.assertEqual((path / "precious").read_bytes(), b"foreign directory\x00")
            else:
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.read_bytes(), b"foreign\x00\xff")
    def assert_swap_refused(self, raced, swarm, rows, kind, foreign="file"):
            """The restore moved nothing. The foreign entry is at the backup's path, the displaced backup is at `<backup>.saved`, and the row stays."""
            backup = Path(rows[0]["backup"])
            self.skipped(
                raced,
                "removed 3 links, restored 0 entries",
                f"skipped restore {backup}: the backup is no longer the entry uninstall read",
            )
            self.assertFalse(os.path.lexists(swarm))
            self.assert_foreign(backup, foreign)
            self.assert_displaced(Path(f"{backup}.saved"), kind)
            self.assertEqual(sorted(os.listdir(backup.parent)), [backup.name, f"{backup.name}.saved"])
            self.assertEqual(read_legacy(self.home)["backups"], rows)
    def assert_swap_survived(self, raced, a, swarm, rows, kind, foreign="file", saved=True, noted=True):
            """The restore put the backup uninstall read at swarm. The foreign entry is still at the backup's path, and the row is gone."""
            backup = Path(rows[0]["backup"])
            self.ok(raced, "removed 3 links, restored 1 entries")
            note = f"kept {backup}: it is not the backup uninstall restored to {swarm}"
            self.assertEqual(note in raced.stdout.splitlines(), noted, raced.stdout)
            self.assert_displaced(swarm, kind)
            self.assert_foreign(backup, foreign)
            names = [backup.name]
            if saved:
                self.assert_displaced(Path(f"{backup}.saved"), kind)
                names.append(f"{backup.name}.saved")
            self.assertEqual(sorted(os.listdir(backup.parent)), names)
            self.assert_empty_records()
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
            self.assert_displaced(swarm, kind)
            self.assert_foreign(backup, foreign)
    def test_an_entry_that_takes_the_backups_path_around_the_hard_link_is_never_moved_or_deleted(self):
            for kind in ("file", "link"):
                for when in ("before", "after"):
                    with self.subTest(kind=kind, when=when):
                        self.use_fresh()
                        a, swarm, rows = self.displaced_by(kind)
                        raced = self.backup_race_uninstall(a, swarm, rows[0]["backup"], ("link", "onto", when, "swap"))
                        self.assert_swap_survived(raced, a, swarm, rows, kind)
    def test_an_entry_that_takes_the_backups_path_is_never_restored_without_proc(self):
            for kind in ("file", "link"):
                with self.subTest(kind=kind, when="before the private name"):
                    self.use_fresh()
                    a, swarm, rows = self.displaced_by(kind)
                    raced = self.backup_race_uninstall(a, swarm, rows[0]["backup"], ("link", "from", "before", "swap"), proc=False)
                    self.assert_swap_refused(raced, swarm, rows, kind)
                for event in (("link", "from", "after", "swap"), ("link", "onto", "after", "swap")):
                    with self.subTest(kind=kind, event=event):
                        self.use_fresh()
                        a, swarm, rows = self.displaced_by(kind)
                        raced = self.backup_race_uninstall(a, swarm, rows[0]["backup"], event, proc=False)
                        self.assert_swap_survived(raced, a, swarm, rows, kind)
    def test_an_entry_that_takes_a_directory_backups_path_is_put_back(self):
            for foreign in ("directory", "file"):
                with self.subTest(foreign=foreign):
                    self.use_fresh()
                    a, swarm, rows = self.displaced_by("directory")
                    event = ("noreplace", "from", "before", "swap")
                    raced = self.backup_race_uninstall(a, swarm, rows[0]["backup"], event, foreign=foreign)
                    self.assert_swap_refused(raced, swarm, rows, "directory", foreign)
