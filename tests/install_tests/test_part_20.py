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


class Part20Test(OwnershipCase):
    def add_rows(self, links=(), backups=()):
            data = read_legacy(self.home)
            data["links"].extend(links)
            data["backups"].extend(backups)
            legacy_file(self.home).write_text(json.dumps(data, indent=2) + "\n")
    def another_directory(self):
            work = self.home / "work"
            work.mkdir()
            return work
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
    def doctor(self, checkout, status, *args):
            result = run(self.home, checkout, "doctor", *args)
            self.assertEqual(result.returncode, status, result.stdout + result.stderr)
            self.assertEqual(result.stderr, "")
            return result.stdout.splitlines()
    def harness_line(self, harness, rest):
            return f"{harness:7} {provider_link(self.home, harness, 'swarm').parent}: {rest}"
    def installed_for_grok(self):
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "grok"), "linked 3 skills into grok")
            return a, provider_link(self.home, "grok", "swarm")
    def plant(self, beside, holder=".pstack-t3-unproven"):
            """A file in a holder beside `beside` that no installer put there."""
            stray = beside.parent / holder / beside.name
            stray.parent.mkdir(parents=True)
            stray.write_bytes(b"stray\x00\xfd")
            return stray
    def relative_row(self):
            a, swarm = self.installed_for_grok()
            saved = self.home / self.RELATIVE_ROW
            saved.parent.mkdir(parents=True)
            saved.write_bytes(b"saved\x00\xfe")
            self.add_rows(backups=[{"harnesses": ["grok"], "original": str(swarm), "backup": self.RELATIVE_ROW}])
            return a, swarm, saved
    def test_doctor_from_another_directory_prints_the_relative_path_line_for_a_backup_row_recorded_as_a_relative_path(self):
            a, swarm, saved = self.relative_row()
            work = self.another_directory()
            before = snapshot(self.home)
            checked = run(self.home, a, "doctor", "--harness", "grok", cwd=work)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertEqual(checked.stderr, "")
            self.assertEqual(
                checked.stdout.splitlines(),
                [
                    self.harness_line("grok", "3/3 pstack-t3"),
                    f"        backup row {self.RELATIVE_ROW}: a relative path with nothing at it from this working directory (recorded as the backup of {swarm}); "
                    "uninstall reads a relative path from the directory it runs in; "
                    f'to drop the row, delete it from "backups" in {legacy_file(self.home)}',
                ],
            )
            self.assertEqual(self.doctor(a, 0, "--harness", "grok"), [self.harness_line("grok", "3/3 pstack-t3")])
            self.assertEqual(snapshot(self.home), before)
    def empty_relative_row(self):
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            write_legacy(self.home, [], [{"harnesses": ["grok"], "original": str(swarm), "backup": self.NAMED}])
            return a, swarm
    def assert_dry_run(self, checkout, env, *lines):
            before = snapshot(self.home)
            dry = run(self.home, checkout, "--harness", "grok", "uninstall", "--dry-run", env=env)
            self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
            self.assertEqual(dry.stdout.splitlines(), [*lines, "would remove 0 links, would restore 0 entries"])
            self.assertEqual(dry.stderr, "")
            self.assertEqual(snapshot(self.home), before)
    def test_a_dry_run_under_a_relative_home_prints_only_would_recover_and_the_counts_line_for_a_backup_row_recorded_as_a_relative_path_with_its_entry_held_beside_it(self):
            a, swarm = self.empty_relative_row()
            self.plant(self.home / self.NAMED, ".pstack-t3-probe")
            self.assert_dry_run(a, self.RELATIVE_HOME, f"would recover {self.NAMED} from {self.HELD}")
    def test_a_dry_run_under_a_relative_home_prints_only_would_remove_and_the_counts_line_for_a_backup_row_recorded_as_a_relative_path_with_a_second_name_of_its_original_held_beside_it(self):
            a, swarm = self.empty_relative_row()
            swarm.parent.mkdir(parents=True)
            swarm.write_bytes(b"mine\x00grok\n")
            held = self.home / self.HELD
            held.parent.mkdir(parents=True)
            os.link(swarm, held)
            self.assert_dry_run(a, self.RELATIVE_HOME, f"would remove {self.HELD}: it was a second name for {swarm}")
    def test_an_uninstall_from_the_home_directory_prints_only_the_left_line_and_the_counts_line_for_a_backup_row_recorded_as_a_relative_path_with_an_entry_held_beside_it_and_changes_nothing(self):
            a, swarm = self.empty_relative_row()
            held = self.plant(self.home / self.NAMED, ".pstack-t3-probe")
            self.assert_left(a, None, f"left {held}: no backup record names {self.home / self.NAMED}")
    def test_doctor_from_the_home_directory_prints_the_held_line_for_a_backup_row_recorded_as_a_relative_path_with_an_entry_held_beside_it(self):
            a, swarm = self.empty_relative_row()
            self.plant(self.home / self.NAMED, ".pstack-t3-probe")
            before = snapshot(self.home)
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [
                    self.harness_line("grok", "0/3 pstack-t3, 3 missing"),
                    f"        backup row {self.NAMED}: nothing is there, and {self.HELD} holds an entry under that name; "
                    '"python3 scripts/install.py uninstall --dry-run" prints what the next run does with it',
                ],
            )
            self.assertEqual(snapshot(self.home), before)
    def test_a_backup_row_recorded_as_a_relative_path_whose_directory_holds_a_nul_byte_gets_the_kept_backup_row_line_from_a_dry_run_and_the_relative_path_line_from_doctor(self):
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            backup = "rel/a\x00b/swarm"
            write_legacy(self.home, [], [{"harnesses": ["grok"], "original": str(swarm), "backup": backup}])
            self.assert_dry_run(
                a,
                None,
                f"kept backup row {backup}: a relative path with nothing at it from this working directory (recorded as the backup of {swarm}); "
                "uninstall reads a relative path from the directory it runs in",
            )
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [
                    self.harness_line("grok", "0/3 pstack-t3, 3 missing"),
                    f"        backup row {backup}: a relative path with nothing at it from this working directory (recorded as the backup of {swarm}); "
                    "uninstall reads a relative path from the directory it runs in; "
                    f'to drop the row, delete it from "backups" in {legacy_file(self.home)}',
                ],
            )
    @unittest.skipIf(os.geteuid() == 0, "root removes a file from a directory of mode 000")
    def test_an_uninstall_whose_install_owners_turns_mode_000_before_its_owner_file_is_removed_exits_1_with_one_could_not_be_removed_line(self):
            a, swarm = self.installed_for_grok()
            owner = owner_file(self.home, a)
            self.addCleanup(owner.parent.chmod, 0o755)
            # Stands in for another program that changes the directory's mode between the read of the owner file and its removal.
            code = (
                "real = module.write_claims\n"
                "def write_claims(state, root, claims):\n"
                "    if not claims:\n"
                f"        os.chmod({str(owner.parent)!r}, 0)\n"
                "    return real(state, root, claims)\n"
                "module.write_claims = write_claims\n"
            )
            stopped = uninstall_hooked(self.home, a, code)
            self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
            self.assertEqual(stopped.stderr, f"{owner} could not be removed (Permission denied); clear that error and rerun\n")
            self.assertEqual(stopped.stdout, "")
    def test_an_uninstall_whose_owner_file_is_gone_before_it_is_removed_exits_0(self):
            a, swarm = self.installed_for_grok()
            owner = owner_file(self.home, a)
            # Stands in for another program that removes the owner file between its read and its removal.
            code = (
                "real = module.write_claims\n"
                "def write_claims(state, root, claims):\n"
                "    if not claims:\n"
                f"        os.unlink({str(owner)!r})\n"
                "    return real(state, root, claims)\n"
                "module.write_claims = write_claims\n"
            )
            self.ok(uninstall_hooked(self.home, a, code), "removed 3 links, restored 0 entries")
            self.assertFalse(os.path.lexists(owner))
    def dot_dot_config(self):
            """A config home whose `..` follows a symlink. Return checkout a, the environment, and the state directory the system reaches."""
            a = make_checkout(self.home, "a")
            (self.home / "else" / "sub").mkdir(parents=True)
            os.symlink(self.home / "else" / "sub", self.home / "x")
            return a, {"XDG_CONFIG_HOME": f"{self.home}/x/../.config"}, self.home / "else" / ".config" / "pstack-t3"
    def test_an_install_under_a_config_home_whose_dot_dot_follows_a_symlink_links_every_skill_and_writes_its_records_in_the_state_directory(self):
            a, env, state = self.dot_dot_config()
            self.ok(
                run(self.home, a, "--harness", "grok", env=env),
                "linked 3 skills into grok",
                f"manifest: {self.home}/x/../.config/pstack-t3/install-manifest.json",
            )
            for name in NAMES:
                self.assertEqual(os.readlink(provider_link(self.home, "grok", name)), str(a / "skills" / name))
            self.assertEqual(sorted(os.listdir(state)), ["install-manifest.json", "install-owners"])
            self.assertEqual(os.listdir(state / "install-owners"), [owner_file(self.home, a).name])
            self.assertFalse(os.path.lexists(self.home / ".config"))
    @unittest.skipIf(os.geteuid() == 0, "root writes in a directory of mode 555")
    def test_an_install_under_a_config_home_whose_dot_dot_follows_a_symlink_succeeds_when_the_directory_its_abspath_spelling_names_cannot_be_written(self):
            a, env, state = self.dot_dot_config()
            other = state_dir(self.home)
            (other / "install-owners").mkdir(parents=True)
            for directory in (other / "install-owners", other):
                directory.chmod(0o555)
                self.addCleanup(directory.chmod, 0o755)
            self.ok(run(self.home, a, "--harness", "grok", env=env), "linked 3 skills into grok")
            self.assertEqual(sorted(os.listdir(state)), ["install-manifest.json", "install-owners"])
            self.assertEqual(os.listdir(other), ["install-owners"])
            self.assertEqual(os.listdir(other / "install-owners"), [])
    def test_an_install_with_replace_under_a_config_home_whose_dot_dot_follows_a_symlink_records_the_backup_under_the_config_home_as_given(self):
            a, env, state = self.dot_dot_config()
            swarm = provider_link(self.home, "grok", "swarm")
            swarm.parent.mkdir(parents=True)
            swarm.write_bytes(b"mine\x00grok\n")
            self.ok(run(self.home, a, "--harness", "grok", "--replace", env=env), "linked 3 skills into grok")
            stamps = os.listdir(state / "backups")
            self.assertEqual(len(stamps), 1, stamps)
            rows = json.loads((state / "install-manifest.json").read_text())["backups"]
            self.assertEqual([row["backup"] for row in rows], [f"{self.home}/x/../.config/pstack-t3/backups/{stamps[0]}/grok/swarm"])
            self.assertEqual((state / "backups" / stamps[0] / "grok" / "swarm").read_bytes(), b"mine\x00grok\n")
    def row_spelled_as_given(self, fill):
            """Under `dot_dot_config`, a backup of swarm made by `fill` and a row that spells it under the config home as given.

            The directory the config home's abspath spelling names exists. Return the checkout, the environment, the state directory, and the path of swarm.
            """
            a, env, state = self.dot_dot_config()
            state_dir(self.home).mkdir(parents=True)
            swarm = provider_link(self.home, "grok", "swarm")
            saved = state / "backups" / self.STAMP / "grok" / "swarm"
            saved.parent.mkdir(parents=True)
            fill(saved)
            backup = f"{self.home}/x/../.config/pstack-t3/backups/{self.STAMP}/grok/swarm"
            (state / "install-manifest.json").write_text(json.dumps({"links": [], "backups": [{"harnesses": ["grok"], "original": str(swarm), "backup": backup}]}))
            return a, env, state, swarm
    def test_an_uninstall_under_a_config_home_whose_dot_dot_follows_a_symlink_restores_a_file_from_a_row_spelled_as_given_and_leaves_one_name_and_no_holder(self):
            a, env, state, swarm = self.row_spelled_as_given(lambda saved: saved.write_bytes(b"saved\x00\xfe"))
            self.ok(run(self.home, a, "--harness", "grok", "uninstall", env=env), "removed 0 links, restored 1 entries")
            self.assertEqual(swarm.read_bytes(), b"saved\x00\xfe")
            self.assertEqual(os.stat(swarm).st_nlink, 1)
            self.assertEqual(self.holders(), [])
            self.assertEqual(json.loads((state / "install-manifest.json").read_text())["backups"], [])
    def test_an_uninstall_under_a_config_home_whose_dot_dot_follows_a_symlink_restores_a_directory_from_a_row_spelled_as_given(self):
            def fill(saved):
                saved.mkdir()
                (saved / "SKILL.md").write_bytes(b"saved\x00\xfe")

            a, env, state, swarm = self.row_spelled_as_given(fill)
            self.ok(run(self.home, a, "--harness", "grok", "uninstall", env=env), "removed 0 links, restored 1 entries")
            self.assertEqual((swarm / "SKILL.md").read_bytes(), b"saved\x00\xfe")
            self.assertEqual(os.listdir(swarm), ["SKILL.md"])
    def test_an_install_with_replace_under_a_relative_home_records_the_backup_under_the_config_home_as_given(self):
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            swarm.parent.mkdir(parents=True)
            swarm.write_bytes(b"mine\x00grok\n")
            self.ok(run(self.home, a, "--harness", "grok", "--replace", env=self.RELATIVE_HOME), "linked 3 skills into grok")
            stamps = os.listdir(state_dir(self.home) / "backups")
            self.assertEqual(len(stamps), 1, stamps)
            self.assertEqual([row["backup"] for row in read_legacy(self.home)["backups"]], [f".config/pstack-t3/backups/{stamps[0]}/grok/swarm"])
