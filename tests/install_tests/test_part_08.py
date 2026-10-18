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


class Part08Test(OwnershipCase):
    def assert_displaced(self, path, kind):
            if kind == "directory":
                self.assertEqual(os.listdir(path), ["SKILL.md"])
                self.assertEqual((path / "SKILL.md").read_bytes(), b"displaced\x00\xfe")
            elif kind == "link":
                self.assertEqual(os.readlink(path), "/foreign/displaced")
            else:
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.read_bytes(), b"displaced\x00\xfe")
    def assert_foreign(self, path, foreign="file"):
            if foreign == "directory":
                self.assertEqual(os.listdir(path), ["precious"])
                self.assertEqual((path / "precious").read_bytes(), b"foreign directory\x00")
            else:
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.read_bytes(), b"foreign\x00\xff")
    def assert_no_copy_left(self, swarm):
            self.assertEqual(list(swarm.parent.glob(".pstack-t3-*")), [])
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
    def cross_device_uninstall(self, swarm, a, occupy=None, rename=None, crossed=True):
            hook = cross_device(state_dir(self.home) / "backups", swarm, occupy=occupy, rename=rename)
            raced = uninstall_hooked(self.home, a, hook)
            if crossed:
                self.assertGreater(crossings(self.home), 0, raced.stdout + raced.stderr)
            return raced
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
    def test_an_entry_that_takes_a_backups_path_is_put_back_without_hard_links(self):
            a, swarm, rows = self.displaced_by("file")
            event = ("noreplace", "from", "before", "swap")
            raced = self.backup_race_uninstall(a, swarm, rows[0]["backup"], event, links=False)
            self.assert_swap_refused(raced, swarm, rows, "file")
    def test_an_entry_that_arrives_after_a_directory_backup_left_is_never_touched(self):
            a, swarm, rows = self.displaced_by("directory")
            event = ("noreplace", "from", "after", "swap")
            raced = self.backup_race_uninstall(a, swarm, rows[0]["backup"], event, foreign="directory")
            # The backup's path was empty when the entry arrived, so the restore never looks at it again.
            self.assert_swap_survived(raced, a, swarm, rows, "directory", "directory", saved=False, noted=False)
    def test_a_directory_backup_that_can_go_neither_way_is_kept_aside(self):
            a, swarm, rows = self.displaced_by("directory")
            backup = Path(rows[0]["backup"])
            events = (("noreplace", "onto", "before", "swap"), ("noreplace", "onto", "before", "occupy"))
            raced = self.backup_race_uninstall(a, swarm, backup, *events)
            aside = set_aside(backup)
            self.assertEqual(len(aside), 1, aside)
            self.skipped(
                raced,
                "removed 3 links, restored 0 entries",
                f"skipped restore {backup}: {swarm} was taken again and {backup} was taken again; it is kept at {aside[0]}",
            )
            self.assert_displaced(aside[0], "directory")
            self.assert_foreign(backup)
            self.assertEqual(swarm.read_bytes(), b"occupant\x00\xff")
            self.assertEqual(read_legacy(self.home)["backups"], rows)
    def test_an_entry_that_cannot_go_back_to_the_backups_path_is_kept_aside(self):
            a, swarm, rows = self.displaced_by("file")
            backup = Path(rows[0]["backup"])
            events = (("link", "onto", "after", "swap"), ("rename", "from", "after", "squat"))
            raced = self.backup_race_uninstall(a, swarm, backup, *events)
            aside = set_aside(backup)
            self.assertEqual(len(aside), 1, raced.stdout + raced.stderr)
            self.ok(
                raced,
                "removed 3 links, restored 1 entries",
                f"kept {aside[0]}: it took the place of the backup uninstall restored to {swarm} and {backup} was taken again",
            )
            self.assert_displaced(swarm, "file")
            self.assert_displaced(Path(f"{backup}.saved"), "file")
            self.assert_foreign(aside[0])
            self.assertEqual(backup.read_bytes(), b"squatter\x00")
            self.assert_empty_records()
    def test_a_backup_whose_directory_vanishes_after_the_hard_link_is_restored(self):
            for proc in (True, False):
                with self.subTest(proc=proc):
                    self.use_fresh()
                    a, swarm, rows = self.displaced_by("file")
                    backup = Path(rows[0]["backup"])
                    raced = self.backup_race_uninstall(a, swarm, backup, ("link", "onto", "after", "vanish"), proc=proc)
                    self.ok(raced, "removed 3 links, restored 1 entries")
                    self.assert_displaced(swarm, "file")
                    self.assertFalse(os.path.lexists(backup.parent))
                    self.assert_empty_records()
    def test_a_copy_across_filesystems_that_fails_keeps_the_backup_at_its_path(self):
            for kind in ("file", "link", "directory"):
                with self.subTest(kind=kind):
                    self.use_fresh()
                    a, swarm, rows = self.displaced_by(kind)
                    backup = Path(rows[0]["backup"])
                    full = (
                        "def full(*args, **kwargs):\n"
                        "    raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))\n"
                        "shutil.copytree = shutil.copy2 = full\n"
                    )
                    hook = cross_device(state_dir(self.home) / "backups", swarm) + full
                    raced = uninstall_hooked(self.home, a, hook)
                    self.assertGreater(crossings(self.home), 0, raced.stdout + raced.stderr)
                    self.skipped(
                        raced,
                        "removed 3 links, restored 0 entries",
                        f"skipped restore {backup}: [Errno {errno.ENOSPC}] {os.strerror(errno.ENOSPC)}",
                    )
                    self.assertFalse(os.path.lexists(swarm))
                    self.assert_displaced(backup, kind)
                    self.assertEqual(os.listdir(backup.parent), [backup.name])
                    self.assertEqual(read_legacy(self.home)["backups"], rows)
                    self.assert_no_copy_left(swarm)
                    self.ok(self.cross_device_uninstall(swarm, a), "removed 0 links, restored 1 entries")
                    self.assert_displaced(swarm, kind)
                    self.assert_empty_records()
    def test_an_entry_that_takes_the_backups_path_before_a_copy_across_filesystems_is_never_copied(self):
            for kind, event, foreign in (
                ("file", ("link", "from", "before", "swap"), "file"),
                ("link", ("link", "from", "before", "swap"), "file"),
                ("directory", ("noreplace", "from", "before", "swap"), "directory"),
            ):
                with self.subTest(kind=kind):
                    self.use_fresh()
                    a, swarm, rows = self.displaced_by(kind)
                    raced = self.backup_race_uninstall(a, swarm, rows[0]["backup"], event, crossing="refused", foreign=foreign)
                    self.assert_swap_refused(raced, swarm, rows, kind, foreign)
    def test_an_entry_that_takes_the_backups_path_after_a_copy_across_filesystems_is_never_discarded(self):
            for kind in ("file", "link"):
                with self.subTest(kind=kind):
                    self.use_fresh()
                    a, swarm, rows = self.displaced_by(kind)
                    event = ("link", "onto", "after", "swap")
                    raced = self.backup_race_uninstall(a, swarm, rows[0]["backup"], event, crossing=True)
                    self.assert_swap_survived(raced, a, swarm, rows, kind)
            with self.subTest(kind="directory"):
                self.use_fresh()
                a, swarm, rows = self.displaced_by("directory")
                event = ("noreplace", "onto", "after", "swap")
                raced = self.backup_race_uninstall(a, swarm, rows[0]["backup"], event, crossing=True, foreign="directory")
                # The directory was already out of its path when the entry arrived, so only the proven directory is discarded.
                self.assert_swap_survived(raced, a, swarm, rows, "directory", "directory", saved=False, noted=False)
    def test_an_entry_swapped_just_before_the_move_is_left_at_the_path_with_the_row_kept(self):
            """A swap in the instant before the move leaves the foreign entry at the live path and keeps the row."""
            for kind in ("file", "directory"):
                with self.subTest(kind=kind):
                    self.use_fresh()
                    a, swarm, rows = self.displaced_by(kind)
                    backup = Path(rows[0]["backup"])
                    saved = self.home / "saved"
                    if kind == "file":
                        actor = "import os, sys; from pathlib import Path; os.rename(sys.argv[1], sys.argv[2]); Path(sys.argv[1]).write_bytes(b'foreign\\x00\\xff')"
                    else:
                        actor = "import os, sys; from pathlib import Path; os.rename(sys.argv[1], sys.argv[2]); os.mkdir(sys.argv[1]); Path(sys.argv[1], 'precious').write_bytes(b'foreign directory\\x00')"
                    swapping = (
                        "import subprocess\n"
                        f"saved = {str(saved)!r}\n"
                        f"actor = {actor!r}\n"
                        "moved = False\n"
                        "real_place = module.place\n"
                        "def swapping_place(source, path):\n"
                        "    global moved\n"
                        "    if not moved and '.pstack-t3-' in str(source):\n"
                        "        moved = True\n"
                        "        subprocess.run([sys.executable, '-c', actor, str(source), saved], check=True)\n"
                        "    return real_place(source, path)\n"
                        "module.place = swapping_place\n"
                    )
                    hook = (NO_PROC if kind == "file" else "") + swapping
                    raced = uninstall_hooked(self.home, a, hook)
                    reason = f"the entry moved to {swarm} could not be confirmed"
                    self.skipped(raced, "removed 3 links, restored 0 entries", f"skipped restore {backup}: {reason}")
                    if kind == "file":
                        self.assertEqual(swarm.read_bytes(), b"foreign\x00\xff")
                        self.assert_displaced(backup, kind)
                        self.assert_displaced(saved, kind)
                        self.assertTrue(os.path.samefile(backup, saved))
                    else:
                        self.assertEqual(os.listdir(swarm), ["precious"])
                        self.assertEqual((swarm / "precious").read_bytes(), b"foreign directory\x00")
                        self.assert_displaced(saved, kind)
                        self.assertFalse(os.path.lexists(backup))
                    self.assertEqual(read_legacy(self.home)["backups"], rows)
                    self.assertEqual(list(swarm.parent.glob(".pstack-t3-stranger-*")), [])
                    self.assert_no_copy_left(swarm)
    def test_a_completed_copy_swap_is_left_at_the_path_with_the_row_kept(self):
            """A swap at the completed copy move leaves the occupant at the live path and keeps the row."""
            before_actor = "import os, sys; os.rename(sys.argv[1], sys.argv[2]); open(sys.argv[1], 'wb').write(b'occupant\\x00\\xff')"
            after_actor = "import os, sys; os.rename(sys.argv[1], sys.argv[2]); open(sys.argv[1], 'wb').write(b'occupant\\x00\\xff')"
            for kind in ("file", "directory", "link"):
                for timing in ("before", "after"):
                    with self.subTest(kind=kind, timing=timing):
                        self.use_fresh()
                        a, swarm, rows = self.displaced_by(kind)
                        backup = Path(rows[0]["backup"])
                        saved = self.home / "saved"
                        if timing == "before":
                            swapping = (
                                "import subprocess\n"
                                f"saved = {str(saved)!r}\n"
                                f"actor = {before_actor!r}\n"
                                "moved = False\n"
                                "real_place = module.place\n"
                                "def swapping_place(source, path):\n"
                                "    global moved\n"
                                "    if not moved and '.pstack-t3-scrap-' in str(source):\n"
                                "        moved = True\n"
                                "        subprocess.run([sys.executable, '-c', actor, str(source), saved], check=True)\n"
                                "    return real_place(source, path)\n"
                                "module.place = swapping_place\n"
                            )
                        else:
                            swapping = (
                                "import subprocess\n"
                                f"saved = {str(saved)!r}\n"
                                f"actor = {after_actor!r}\n"
                                "moved = False\n"
                                "real_place = module.place\n"
                                "def swapping_place(source, path):\n"
                                "    global moved\n"
                                "    result = real_place(source, path)\n"
                                "    if not moved and '.pstack-t3-scrap-' in str(source) and result == 0:\n"
                                "        moved = True\n"
                                "        subprocess.run([sys.executable, '-c', actor, str(path), saved], check=True)\n"
                                "    return result\n"
                                "module.place = swapping_place\n"
                            )
                        hook = cross_device(state_dir(self.home) / "backups", swarm) + darwin_renamex() + NO_PROC + swapping
                        raced = uninstall_hooked(self.home, a, hook)
                        self.assertGreater(crossings(self.home), 0, raced.stdout + raced.stderr)
                        self.skipped(
                            raced,
                            "removed 3 links, restored 0 entries",
                            f"skipped restore {backup}: the entry moved to {swarm} could not be confirmed",
                        )
                        self.assertEqual(swarm.read_bytes(), b"occupant\x00\xff")
                        self.assert_displaced(saved, kind)
                        self.assert_displaced(backup, kind)
                        self.assertEqual(read_legacy(self.home)["backups"], rows)
                        self.assert_no_copy_left(swarm)
                        self.assertEqual(list(swarm.parent.glob(".pstack-t3-stranger-*")), [])
