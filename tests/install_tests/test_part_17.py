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


class Part17Test(OwnershipCase):
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
    def under_backups(self):
            backups = state_dir(self.home) / "backups"
            return sorted(str(path.relative_to(backups)) for path in backups.rglob("*"))
    @unittest.skipIf(os.geteuid() == 0, "root reads a file of mode 000")
    def test_doctor_exits_0_and_names_another_checkouts_owner_file_of_mode_000_and_install_exits_0(self):
            a, swarm = self.installed_for_grok()
            other = owner_file(self.home, a).with_name("00aa11bb22cc33dd.json")
            other.write_text("{}\n")
            other.chmod(0)
            self.addCleanup(other.chmod, 0o644)
            self.assertEqual(
                self.doctor(a, 0, "--harness", "grok"),
                [
                    self.harness_line("grok", "3/3 pstack-t3"),
                    f"{other} could not be read (Permission denied); clear that error and rerun (doctor read no checkout from it)",
                ],
            )
            self.ok(run(self.home, a, "install", "--harness", "grok"))
    def test_doctor_exits_0_and_prints_only_the_harness_line_on_a_fully_linked_home_with_a_symlink_loop_at_the_manifest_path(self):
            a, swarm = self.installed_for_grok()
            legacy_file(self.home).unlink()
            os.symlink(legacy_file(self.home).name, legacy_file(self.home))
            self.assertEqual(self.doctor(a, 0, "--harness", "grok"), [self.harness_line("grok", "3/3 pstack-t3")])
    def test_doctor_exits_0_and_prints_only_the_harness_line_on_a_fully_linked_home_with_a_file_where_install_owners_belongs(self):
            a, swarm = self.installed_for_grok()
            owners = owner_file(self.home, a).parent
            shutil.rmtree(owners)
            owners.write_text("not a directory\n")
            self.assertEqual(self.doctor(a, 0, "--harness", "grok"), [self.harness_line("grok", "3/3 pstack-t3")])
    @unittest.skipIf(os.geteuid() == 0, "root reads a file of mode 000")
    def test_an_uninstall_whose_manifest_turns_mode_000_before_the_sweep_unlinks_a_second_name_exits_1_with_one_line_and_no_left_line(self):
            a, swarm, backup, raced, aside = self.strand_backup(raising("same_entry", within="drop_backup"), kind="file")
            manifest = legacy_file(self.home)
            self.addCleanup(manifest.chmod, 0o644)
            stopped = uninstall_hooked(
                self.home,
                a,
                "real = module.settle\n"
                "def settle(strays, state, root, dry_run):\n"
                "    if not dry_run:\n"
                f"        os.chmod({str(manifest)!r}, 0)\n"
                "    return real(strays, state, root, dry_run)\n"
                "module.settle = settle\n",
            )
            self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
            self.assertEqual(stopped.stderr, f"{manifest} could not be read (Permission denied); clear that error and rerun\n")
            self.assertEqual(stopped.stdout, "")
            self.assertFalse(os.path.lexists(aside))
    def test_doctor_with_six_stale_claims_under_two_harness_lists_calls_plan_install_four_times(self):
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "codex,grok"), "linked 6 skills into codex, grok")
            for harness in ("codex", "grok"):
                for name in NAMES:
                    provider_link(self.home, harness, name).unlink()
            counted = doctor_counting_plans(self.home, a, "--harness", "codex,grok")
            self.assertEqual(counted.returncode, 1, counted.stdout + counted.stderr)
            heads = [line for line in counted.stdout.splitlines() if line.startswith("        3 claims have nothing at their paths")]
            self.assertEqual(len(heads), 2, counted.stdout)
            self.assertEqual(counted.stderr, "4")
    def test_doctor_with_one_held_claim_calls_plan_install_no_times(self):
            a, swarm, raced, aside = self.strand_link()
            counted = doctor_counting_plans(self.home, a, "--harness", "grok")
            self.assertEqual(counted.returncode, 1, counted.stdout + counted.stderr)
            self.assertIn(f"{aside} holds this checkout's link for it", counted.stdout)
            self.assertEqual(counted.stderr, "0")
    def test_doctor_exits_0_on_a_fully_linked_home_where_only_another_checkouts_owner_file_is_not_json(self):
            a, swarm = self.installed_for_grok()
            other = owner_file(self.home, a).with_name("00aa11bb22cc33dd.json")
            other.write_text("{not json\n")
            self.assertEqual(
                self.doctor(a, 0, "--harness", "grok"),
                [
                    self.harness_line("grok", "3/3 pstack-t3"),
                    f"{other} is not valid JSON; fix or move it and rerun (doctor read no checkout from it)",
                ],
            )
    def taken_swarm(self, harnesses=("grok",)):
            """Checkout b and a user's swarm file at each harness's path, before any install."""
            b = make_checkout(self.home, "b")
            for harness in harnesses:
                swarm = provider_link(self.home, harness, "swarm")
                swarm.parent.mkdir(parents=True)
                swarm.write_bytes(f"mine\x00{harness}\n".encode())
            return b, provider_link(self.home, "grok", "swarm")
    def refused(self, swarm):
            return f"skipped move {swarm}: [Errno 13] Permission denied: '{swarm}'"
    def test_a_replace_whose_only_move_raises_before_any_copy_leaves_nothing_under_backups(self):
            b, swarm = self.taken_swarm()
            failed = run_failing(self.home, b, "shutil.move", swarm, "--harness", "grok", "--replace")
            self.skipped(failed, "linked 2 skills into grok", self.refused(swarm))
            self.assertEqual(swarm.read_bytes(), b"mine\x00grok\n")
            self.assertTrue((state_dir(self.home) / "backups").is_dir())
            self.assertEqual(self.under_backups(), [])
    def test_a_replace_whose_only_move_is_skipped_because_its_path_vanished_leaves_nothing_under_backups(self):
            b, swarm = self.taken_swarm()
            vanish = (
                "checking = module.ready\n"
                "def ready(step, root):\n"
                "    if step.kind == 'move':\n"
                "        os.unlink(step.path)\n"
                "    return checking(step, root)\n"
                "module.ready = ready\n"
            )
            raced = install_hooked(self.home, b, vanish, "--harness", "grok", "--replace")
            self.skipped(raced, "linked 3 skills into grok", f"skipped move {swarm}: it changed before install")
            self.assertEqual(read_legacy(self.home)["backups"], [])
            self.assertTrue((state_dir(self.home) / "backups").is_dir())
            self.assertEqual(self.under_backups(), [])
    def test_a_replace_for_two_harnesses_where_one_move_raises_keeps_only_the_other_harness_backup_directory(self):
            b, swarm = self.taken_swarm(("codex", "grok"))
            failed = run_failing(self.home, b, "shutil.move", swarm, "--harness", "codex,grok", "--replace")
            self.skipped(failed, "linked 5 skills into codex, grok", self.refused(swarm))
            self.assertEqual(swarm.read_bytes(), b"mine\x00grok\n")
            stamp = self.under_backups()[0]
            self.assertEqual(self.under_backups(), [stamp, f"{stamp}/codex", f"{stamp}/codex/swarm"])
            self.assertEqual((state_dir(self.home) / "backups" / stamp / "codex" / "swarm").read_bytes(), b"mine\x00codex\n")
    def test_an_install_that_stops_on_an_unreadable_record_after_it_made_its_stamp_leaves_nothing_under_backups(self):
            b, swarm = self.taken_swarm()
            stop = (
                "def add_records(step, state, root):\n"
                "    if step.kind == 'move':\n"
                "        raise module.Unreadable('stop')\n"
                "    return lambda: None\n"
                "module.add_records = add_records\n"
            )
            stopped = install_hooked(self.home, b, stop, "--harness", "grok", "--replace")
            self.assertEqual((stopped.returncode, stopped.stderr), (1, "stop\n"), stopped.stdout)
            self.assertEqual(swarm.read_bytes(), b"mine\x00grok\n")
            self.assertTrue((state_dir(self.home) / "backups").is_dir())
            self.assertEqual(self.under_backups(), [])
    def failed_move_through_symlink(self, level):
            """A `--replace` whose move raises after the run's `level` directory became a symlink to a directory elsewhere.

            Return the symlink and the directory it points at, which holds the levels below it.
            """
            b, swarm = self.taken_swarm()
            real = self.home / "elsewhere" / level
            real.parent.mkdir()
            up = {"backups": "os.path.dirname(os.path.dirname(os.path.dirname(destination)))",
                  "stamp": "os.path.dirname(os.path.dirname(destination))", "harness": "os.path.dirname(destination)"}[level]
            hook = refused_move(
                swarm,
                f"link = {up}",
                f"os.rename(link, {str(real)!r})",
                f"os.symlink({str(real)!r}, link)",
                f"open({str(self.home / 'link')!r}, 'w').write(link)",
            )
            self.skipped(install_hooked(self.home, b, hook, "--harness", "grok", "--replace"), self.refused(swarm))
            link = Path((self.home / "link").read_text())
            self.assertEqual(os.readlink(link), str(real))
            self.assertEqual(swarm.read_bytes(), b"mine\x00grok\n")
            return link, real
    def test_a_failed_move_with_backups_swapped_for_a_symlink_leaves_the_stamp_and_harness_directories_behind_it(self):
            link, real = self.failed_move_through_symlink("backups")
            self.assertEqual(link, state_dir(self.home) / "backups")
            stamps = os.listdir(real)
            self.assertEqual(len(stamps), 1, stamps)
            self.assertEqual(os.listdir(real / stamps[0]), ["grok"])
            self.assertEqual(os.listdir(real / stamps[0] / "grok"), [])
    def test_a_failed_move_with_its_stamp_swapped_for_a_symlink_leaves_the_link_and_the_harness_directory_behind_it(self):
            link, real = self.failed_move_through_symlink("stamp")
            self.assertEqual(os.listdir(link.parent), [link.name])
            self.assertEqual(os.listdir(real), ["grok"])
            self.assertEqual(os.listdir(real / "grok"), [])
    def test_a_failed_move_with_its_harness_directory_swapped_for_a_symlink_leaves_the_link_its_stamp_and_the_directory(self):
            link, real = self.failed_move_through_symlink("harness")
            self.assertEqual(os.listdir(link.parent), ["grok"])
            self.assertEqual(os.listdir(link.parent.parent), [link.parent.name])
            self.assertEqual(os.listdir(real), [])
    def test_a_file_written_beside_a_backup_that_never_arrived_keeps_its_harness_and_stamp_directories(self):
            b, swarm = self.taken_swarm()
            hook = refused_move(swarm, "open(os.path.join(os.path.dirname(destination), 'notes.txt'), 'wb').write(b'keep\\x00me\\n')")
            self.skipped(install_hooked(self.home, b, hook, "--harness", "grok", "--replace"), self.refused(swarm))
            stamp = self.under_backups()[0]
            self.assertEqual(self.under_backups(), [stamp, f"{stamp}/grok", f"{stamp}/grok/notes.txt"])
            self.assertEqual((state_dir(self.home) / "backups" / stamp / "grok" / "notes.txt").read_bytes(), b"keep\x00me\n")
    def test_a_harness_directory_this_run_did_not_make_in_its_stamp_stops_the_move_and_stays(self):
            b, swarm = self.taken_swarm()
            planted = (
                "import tempfile\n"
                "making = tempfile.mkdtemp\n"
                "def mkdtemp(*args, **kwargs):\n"
                "    made = making(*args, **kwargs)\n"
                "    os.mkdir(os.path.join(made, 'grok'))\n"
                "    return made\n"
                "tempfile.mkdtemp = mkdtemp\n"
            )
            raced = install_hooked(self.home, b, planted, "--harness", "grok", "--replace")
            stamp = self.under_backups()[0]
            self.skipped(
                raced,
                "linked 2 skills into grok",
                f"skipped move {swarm}: [Errno 17] File exists: '{state_dir(self.home) / 'backups' / stamp / 'grok'}'",
            )
            self.assertEqual(self.under_backups(), [stamp, f"{stamp}/grok"])
            self.assertEqual(swarm.read_bytes(), b"mine\x00grok\n")
            self.assertEqual(read_legacy(self.home)["backups"], [])
    def test_a_failed_move_leaves_an_empty_harness_directory_under_another_stamp(self):
            b, swarm = self.taken_swarm()
            other = state_dir(self.home) / "backups" / "20260101T000000-1-abcd" / "grok"
            other.mkdir(parents=True)
            failed = run_failing(self.home, b, "shutil.move", swarm, "--harness", "grok", "--replace")
            self.skipped(failed, "linked 2 skills into grok", self.refused(swarm))
            self.assertEqual(self.under_backups(), ["20260101T000000-1-abcd", "20260101T000000-1-abcd/grok"])
    def held_beside_absolute_row(self):
            """Checkout a, a row whose backup is the absolute spelling of <stamp>/grok/swarm, and a file held beside that path.

            Return the checkout, the path of swarm, the backup path, and the held file.
            """
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            saved = state_dir(self.home) / "backups" / self.STAMP / "grok" / "swarm"
            write_legacy(self.home, [], [{"harnesses": ["grok"], "original": str(swarm), "backup": str(saved)}])
            return a, swarm, saved, self.plant(saved, ".pstack-t3-probe")
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
    def test_an_uninstall_under_a_relative_home_leaves_an_entry_held_beside_a_backup_recorded_as_an_absolute_path_and_changes_nothing(self):
            a, swarm, saved, held = self.held_beside_absolute_row()
            self.assert_left(a, self.RELATIVE_HOME, self.UNNAMED)
            self.assertEqual(held.read_bytes(), b"stray\x00\xfd")
            self.assertFalse(os.path.lexists(swarm))
            self.assertEqual(read_legacy(self.home)["backups"], [{"harnesses": ["grok"], "original": str(swarm), "backup": str(saved)}])
