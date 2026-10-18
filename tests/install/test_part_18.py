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


class Part18Test(OwnershipCase):
    def assert_left(self, checkout, env, line):
            """Assert that uninstall --dry-run and then uninstall exit 0 with `line` and a counts line of 0 links and 0 entries, and change nothing under the home."""
            before = snapshot(self.home)
            for command, counts in (
                (("uninstall", "--dry-run"), "would remove 0 links, would restore 0 entries"),
                (("uninstall",), "removed 0 links, restored 0 entries"),
            ):
                done = run(self.home, checkout, "--harness", "grok", *command, env=env)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(done.stdout.splitlines(), [line, counts])
                self.assertEqual(snapshot(self.home), before)
    def divergent_state(self):
            """A config home whose `..` follows a symlink, with a manifest row for a backup of swarm under its absolute spelling.

            Return the checkout, the environment, the state directory the system reaches, and the backup path in the row.
            """
            a = make_checkout(self.home, "a")
            (self.home / "else" / "sub").mkdir(parents=True)
            os.symlink(self.home / "else" / "sub", self.home / "x")
            state = self.home / "else" / ".config" / "pstack-t3"
            saved = state_dir(self.home) / "backups" / "20260101T000000-1-abcd" / "grok" / "swarm"
            (state / "backups").mkdir(parents=True)
            row = {"harnesses": ["grok"], "original": str(provider_link(self.home, "grok", "swarm")), "backup": str(saved)}
            (state / "install-manifest.json").write_text(json.dumps({"links": [], "backups": [row]}))
            return a, {"XDG_CONFIG_HOME": f"{self.home}/x/../.config"}, state, saved
    def held_beside_absolute_row(self):
            """Checkout a, a row whose backup is the absolute spelling of <stamp>/grok/swarm, and a file held beside that path.

            Return the checkout, the path of swarm, the backup path, and the held file.
            """
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            saved = state_dir(self.home) / "backups" / self.STAMP / "grok" / "swarm"
            write_legacy(self.home, [], [{"harnesses": ["grok"], "original": str(swarm), "backup": str(saved)}])
            return a, swarm, saved, self.plant(saved, ".pstack-t3-probe")
    def plant(self, beside, holder=".pstack-t3-unproven"):
            """A file in a holder beside `beside` that no installer put there."""
            stray = beside.parent / holder / beside.name
            stray.parent.mkdir(parents=True)
            stray.write_bytes(b"stray\x00\xfd")
            return stray
    def test_doctor_under_a_relative_home_prints_the_delete_the_row_line_for_a_backup_row_recorded_as_an_absolute_path_with_an_entry_held_beside_it(self):
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "grok", env=self.RELATIVE_HOME), "linked 3 skills into grok")
            notes = provider_link(self.home, "grok", "notes")
            saved = state_dir(self.home) / "backups" / self.STAMP / "grok" / "notes"
            data = read_legacy(self.home)
            data["backups"].append({"harnesses": ["grok"], "original": str(notes), "backup": str(saved)})
            legacy_file(self.home).write_text(json.dumps(data, indent=2) + "\n")
            self.plant(saved, ".pstack-t3-probe")
            before = snapshot(self.home)
            checked = run(self.home, a, "doctor", "--harness", "grok", env=self.RELATIVE_HOME)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertEqual(checked.stderr, "")
            self.assertEqual(
                checked.stdout.splitlines(),
                [
                    "grok    .grok/skills: 3/3 pstack-t3",
                    f"        backup row {saved}: nothing is there (recorded as the backup of {notes}); uninstall skips the row "
                    'while that path is empty; to drop it, delete the row from "backups" in .config/pstack-t3/install-manifest.json',
                ],
            )
            self.assertEqual(snapshot(self.home), before)
    def test_an_uninstall_under_a_relative_home_leaves_a_second_name_held_beside_a_backup_recorded_as_an_absolute_path_and_leaves_the_row(self):
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            swarm.parent.mkdir(parents=True)
            swarm.write_bytes(b"mine\x00grok\n")
            saved = state_dir(self.home) / "backups" / self.STAMP / "grok" / "swarm"
            held = saved.parent / ".pstack-t3-probe" / "swarm"
            held.parent.mkdir(parents=True)
            os.link(swarm, held)
            row = {"harnesses": ["grok"], "original": str(swarm), "backup": str(saved)}
            write_legacy(self.home, [], [row])
            self.assert_left(a, self.RELATIVE_HOME, self.UNNAMED)
            self.assertEqual(held.read_bytes(), b"mine\x00grok\n")
            self.assertEqual(os.stat(swarm).st_nlink, 2)
            self.assertEqual(read_legacy(self.home)["backups"], [row])
    def test_a_dry_run_under_a_relative_home_prints_left_and_kept_backup_for_a_backup_recorded_as_an_absolute_path_with_an_entry_held_beside_it_and_changes_nothing(self):
            a, swarm, saved, held = self.held_beside_absolute_row()
            saved.write_bytes(b"other\x00\xfc")
            before = snapshot(self.home)
            dry = run(self.home, a, "--harness", "grok", "uninstall", "--dry-run", env=self.RELATIVE_HOME)
            self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
            self.assertEqual(
                dry.stdout.splitlines(),
                [
                    self.UNNAMED,
                    f"kept backup {saved}: {held} is held for that path and another entry is there now; remove the one that is not the backup and rerun uninstall",
                    "would remove 0 links, would restore 0 entries",
                ],
            )
            self.assertEqual(snapshot(self.home), before)
    def test_an_uninstall_under_a_relative_home_whose_config_home_is_a_symlink_leaves_an_entry_held_beside_a_backup_recorded_as_an_absolute_path_and_changes_nothing(self):
            real = self.home / "dotfiles" / "kept"
            real.mkdir(parents=True)
            os.symlink(real, self.home / ".config")
            a, swarm, saved, held = self.held_beside_absolute_row()
            self.assert_left(a, self.RELATIVE_HOME, self.UNNAMED)
            self.assertEqual((real / "pstack-t3" / "backups" / self.STAMP / "grok" / ".pstack-t3-probe" / "swarm").read_bytes(), b"stray\x00\xfd")
    def test_an_uninstall_under_a_config_home_whose_dot_dot_follows_a_symlink_leaves_an_entry_held_in_the_state_directory_when_the_row_names_the_other_directory(self):
            a, env, state, saved = self.divergent_state()
            saved.parent.mkdir(parents=True)
            held = self.plant(state / "backups" / self.STAMP / "grok" / "swarm", ".pstack-t3-probe")
            given = f"{self.home}/x/../.config/pstack-t3/backups/{self.STAMP}/grok"
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall", env=env),
                f"left {given}/.pstack-t3-probe/swarm: no backup record names {given}/swarm",
                "removed 0 links, restored 0 entries",
            )
            self.assertEqual(held.read_bytes(), b"stray\x00\xfd")
            self.assertEqual(os.listdir(saved.parent), [])
    def test_an_uninstall_under_a_relative_home_leaves_an_entry_held_beside_a_backup_recorded_through_a_missing_directory_and_dot_dot(self):
            a, swarm, saved, held = self.held_beside_absolute_row()
            backup = f"{state_dir(self.home)}/nope/../backups/{self.STAMP}/grok/swarm"
            write_legacy(self.home, [], [{"harnesses": ["grok"], "original": str(swarm), "backup": backup}])
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall", env=self.RELATIVE_HOME),
                self.UNNAMED,
                "removed 0 links, restored 0 entries",
            )
            self.assertEqual(held.read_bytes(), b"stray\x00\xfd")
    def two_harness_directories(self):
            """`divergent_state` with two directories made, <stamp>/grok under the state directory and the directory the row's backup path is in.

            Return the checkout, the environment, the first directory, the second, and the `no backup record names` line for swarm
            held in `.pstack-t3-probe` in the first, spelled under the config home as given.
            """
            a, env, state, saved = self.divergent_state()
            actual = state / "backups" / self.STAMP / "grok"
            actual.mkdir(parents=True)
            saved.parent.mkdir(parents=True)
            given = f"{self.home}/x/../.config/pstack-t3/backups/{self.STAMP}/grok"
            return a, env, actual, saved.parent, f"left {given}/.pstack-t3-probe/swarm: no backup record names {given}/swarm"
    def assert_left_under_mounts(self, checkout, env, binds, line):
            """Assert that an uninstall with `binds` mounted exits 0 with `line` and `removed 0 links, restored 0 entries`, and changes nothing under the home."""
            before = snapshot(self.home)
            done = run_bound(self.home, checkout, binds, "--harness", "grok", "uninstall", env=env)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(done.stdout.splitlines(), [line, "removed 0 links, restored 0 entries"])
            self.assertEqual(snapshot(self.home), before)
    def test_an_uninstall_with_an_unrelated_directory_mounted_at_the_harness_under_the_state_directory_leaves_the_entry_held_in_it(self):
            reason = bind_mounts_refused()
            if reason:
                self.skipTest(reason)
            a, env, actual, recorded, line = self.two_harness_directories()
            unrelated = self.home / "unrelated"
            unrelated.mkdir()
            held = self.plant(unrelated / "swarm", ".pstack-t3-probe")
            self.assert_left_under_mounts(a, env, ((unrelated, actual),), line)
            self.assertEqual(held.read_bytes(), b"stray\x00\xfd")
    def test_an_uninstall_with_an_unrelated_directory_mounted_at_the_stamp_under_the_state_directory_leaves_the_entry_held_under_it(self):
            reason = bind_mounts_refused()
            if reason:
                self.skipTest(reason)
            a, env, actual, recorded, line = self.two_harness_directories()
            unrelated = self.home / "unrelated"
            (unrelated / "grok").mkdir(parents=True)
            held = self.plant(unrelated / "grok" / "swarm", ".pstack-t3-probe")
            self.assert_left_under_mounts(a, env, ((unrelated, actual.parent),), line)
            self.assertEqual(held.read_bytes(), b"stray\x00\xfd")
    def test_an_uninstall_with_the_recorded_harness_directory_mounted_at_the_harness_under_the_state_directory_leaves_the_entry_held_in_it(self):
            reason = bind_mounts_refused()
            if reason:
                self.skipTest(reason)
            a, env, actual, recorded, line = self.two_harness_directories()
            held = self.plant(recorded / "swarm", ".pstack-t3-probe")
            self.assert_left_under_mounts(a, env, ((recorded, actual),), line)
            self.assertEqual(held.read_bytes(), b"stray\x00\xfd")
            self.assertFalse(os.path.lexists(provider_link(self.home, "grok", "swarm")))
    def second_name_and_held_backup(self, second_name):
            """Under `two_harness_directories`, a file at swarm with a second name at `second_name`, and another file held beside the path the row names.

            Return the path of swarm and the held file.
            """
            swarm = provider_link(self.home, "grok", "swarm")
            swarm.parent.mkdir(parents=True)
            swarm.write_bytes(b"FOREIGN\x00\xfe")
            second_name.parent.mkdir(parents=True, exist_ok=True)
            os.link(swarm, second_name)
            held = state_dir(self.home) / "backups" / self.STAMP / "grok" / ".pstack-t3-probe" / "swarm"
            held.parent.mkdir()
            held.write_bytes(b"TRUE BACKUP\x00\xff")
            return swarm, held
    def assert_row_second_name_and_held_backup_kept(self, swarm, second_name, held):
            row = {"harnesses": ["grok"], "original": str(swarm), "backup": str(held.parent.parent / "swarm")}
            manifest = self.home / "else" / ".config" / "pstack-t3" / "install-manifest.json"
            self.assertEqual(json.loads(manifest.read_text())["backups"], [row])
            self.assertEqual(second_name.read_bytes(), b"FOREIGN\x00\xfe")
            self.assertEqual(os.stat(swarm).st_nlink, 2)
            self.assertEqual(held.read_bytes(), b"TRUE BACKUP\x00\xff")
    def test_an_uninstall_with_the_recorded_harness_directory_mounted_at_the_harness_under_the_state_directory_and_a_directory_that_holds_a_second_name_for_the_original_mounted_at_the_holder_leaves_the_row_that_name_and_the_held_backup(self):
            reason = bind_mounts_refused()
            if reason:
                self.skipTest(reason)
            a, env, actual, recorded, line = self.two_harness_directories()
            unrelated = self.home / "unrelated"
            swarm, held = self.second_name_and_held_backup(unrelated / "swarm")
            self.assert_left_under_mounts(a, env, ((recorded, actual), (unrelated, actual / ".pstack-t3-probe")), line)
            self.assert_row_second_name_and_held_backup_kept(swarm, unrelated / "swarm", held)
    def test_an_uninstall_for_which_the_two_harness_directories_compare_as_one_leaves_the_row_a_second_name_for_the_original_held_under_the_state_directory_and_the_held_backup(self):
            a, env, actual, recorded, line = self.two_harness_directories()
            second_name = actual / ".pstack-t3-probe" / "swarm"
            swarm, held = self.second_name_and_held_backup(second_name)
            # A mount of the recorded <harness> directory at the one under the state directory gives the two one device and inode. The hook makes os.path.samestat true for that pair with no mount.
            code = (
                f"os.environ['XDG_CONFIG_HOME'] = {env['XDG_CONFIG_HOME']!r}\n"
                f"pair = {{(found.st_dev, found.st_ino) for found in (os.stat({str(actual)!r}), os.stat({str(recorded)!r}))}}\n"
                "real = os.path.samestat\n"
                "def samestat(one, other):\n"
                "    return real(one, other) or {(one.st_dev, one.st_ino), (other.st_dev, other.st_ino)} == pair\n"
                "os.path.samestat = samestat\n"
            )
            done = uninstall_hooked(self.home, a, code)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(done.stdout.splitlines(), [line, "removed 0 links, restored 0 entries"])
            self.assert_row_second_name_and_held_backup_kept(swarm, second_name, held)
    def failing_link(self, fails, breaks):
            """Run checkout a's install with a first step that runs `breaks` on the manifest path and then `fails`. Return the run and the manifest path."""
            a = make_checkout(self.home, "a")
            manifest = legacy_file(self.home)
            # Stands in for a step that fails while another program rewrites the manifest or changes its mode between the step's record and its undo.
            code = (
                "import errno\n"
                "def act(step, root):\n"
                f"    manifest = {str(manifest)!r}\n"
                f"    {breaks}\n"
                f"    {fails}\n"
                "module.act = act\n"
            )
            return install_hooked(self.home, a, code, "--harness", "grok"), manifest
    def test_an_install_whose_manifest_stops_being_json_while_a_link_fails_prints_the_skipped_line_and_exits_1_with_the_manifest_line(self):
            stopped, manifest = self.failing_link(self.NO_SPACE, self.NOT_JSON)
            self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
            self.assertEqual(stopped.stdout, f"skipped link {provider_link(self.home, 'grok', 'alpha')}: [Errno 28] No space left on device\n")
            self.assertEqual(stopped.stderr, f"{manifest} is not valid JSON; fix or move it and rerun\n")
