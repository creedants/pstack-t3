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


class Part13Test(OwnershipCase):
    def replaced_file(self, harnesses=("grok",)):
            """Checkout a after one `--replace` run moved a user's swarm file aside for each harness."""
            a = make_checkout(self.home, "a")
            for harness in harnesses:
                swarm = provider_link(self.home, harness, "swarm")
                swarm.parent.mkdir(parents=True)
                swarm.write_bytes(f"mine\x00{harness}\n".encode())
            self.ok(run(self.home, a, "--harness", ",".join(harnesses), "--replace"), f"linked {3 * len(harnesses)} skills into {', '.join(sorted(harnesses))}")
            return a
    def restore_recorded_as(self, backup):
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            saved = state_dir(self.home) / "backups" / "20260101T000000-1-abcd" / "grok" / "swarm"
            saved.parent.mkdir(parents=True)
            saved.write_bytes(b"saved\x00\xfe")
            write_legacy(self.home, [], [{"harnesses": ["grok"], "original": str(swarm), "backup": backup}])
            self.ok(run(self.home, a, "--harness", "grok", "uninstall", env=self.RELATIVE_HOME), "removed 0 links, restored 1 entries")
            self.assertEqual(swarm.read_bytes(), b"saved\x00\xfe")
    def restore_through_symlink(self, level, env=None):
            """Restore swarm from a backup whose `level` directory is a symlink to a directory outside the state directory.

            The uninstall runs with `env` added to its environment. Return the symlink, the directory it points at, and the real <harness> directory the backup was in.
            """
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            backups = state_dir(self.home) / "backups"
            stamp = backups / "20260101T000000-1-abcd"
            link = {"backups": backups, "stamp": stamp, "harness": stamp / "grok"}[level]
            real = self.home / "elsewhere" / level
            emptied = real / {"backups": Path(stamp.name) / "grok", "stamp": "grok", "harness": ""}[level]
            emptied.mkdir(parents=True)
            (emptied / "swarm").write_bytes(b"saved\x00\xfe")
            link.parent.mkdir(parents=True)
            os.symlink(real, link)
            write_legacy(self.home, [], [{"harnesses": ["grok"], "original": str(swarm), "backup": str(stamp / "grok" / "swarm")}])
            self.ok(run(self.home, a, "--harness", "grok", "uninstall", env=env), "removed 0 links, restored 1 entries")
            self.assertEqual(swarm.read_bytes(), b"saved\x00\xfe")
            self.assertEqual(os.readlink(link), str(real))
            self.assertTrue(emptied.is_dir())
            self.assertEqual(os.listdir(emptied), [])
            return link, real, emptied
    def under_backups(self):
            backups = state_dir(self.home) / "backups"
            return sorted(str(path.relative_to(backups)) for path in backups.rglob("*"))
    def test_a_restore_under_a_relative_home_whose_working_directory_is_removed_before_the_prune_restores_the_file_with_no_traceback(self):
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            saved = state_dir(self.home) / "backups" / "20260101T000000-1-abcd" / "grok" / "swarm"
            saved.parent.mkdir(parents=True)
            saved.write_bytes(b"saved\x00\xfe")
            write_legacy(self.home, [], [{"harnesses": ["grok"], "original": str(swarm), "backup": str(saved)}])
            gone = self.home / "gone"
            code = (
                "os.environ['HOME'] = '.'\n"
                "original = module.remove_records\n"
                "def removing(*args):\n"
                "    original(*args)\n"
                f"    os.mkdir({str(gone)!r})\n"
                f"    os.chdir({str(gone)!r})\n"
                f"    os.rmdir({str(gone)!r})\n"
                "module.remove_records = removing\n"
            )
            result = uninstall_hooked(self.home, a, code)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(swarm.read_bytes(), b"saved\x00\xfe")
    def test_a_restore_under_a_relative_home_from_a_backup_recorded_as_a_relative_path_leaves_backups_empty(self):
            self.restore_recorded_as(".config/pstack-t3/backups/20260101T000000-1-abcd/grok/swarm")
            self.assertEqual(self.under_backups(), [])
    def test_a_restore_under_a_relative_home_through_a_symlink_at_each_level_leaves_the_link_and_the_emptied_harness_directory(self):
            behind = {
                "backups": lambda link, real, emptied: (os.listdir(real), os.listdir(emptied.parent)),
                "stamp": lambda link, real, emptied: (os.listdir(real), os.listdir(link.parent)),
                "harness": lambda link, real, emptied: (os.listdir(link.parent), os.listdir(link.parent.parent)),
            }
            kept = {
                "backups": (["20260101T000000-1-abcd"], ["grok"]),
                "stamp": (["grok"], ["20260101T000000-1-abcd"]),
                "harness": (["grok"], ["20260101T000000-1-abcd"]),
            }
            for level in ("backups", "stamp", "harness"):
                with self.subTest(level=level):
                    self.use_fresh()
                    self.assertEqual(behind[level](*self.restore_through_symlink(level, self.RELATIVE_HOME)), kept[level])
    def test_a_restore_under_a_relative_home_from_a_backup_recorded_with_dot_dot_leaves_both_harness_directories(self):
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            backups = state_dir(self.home) / "backups"
            named = backups / "20260101T000000-1-abcd" / "grok"
            named.mkdir(parents=True)
            other = backups / "20260202T000000-2-ef01" / "codex"
            other.mkdir(parents=True)
            (other / "swarm").write_bytes(b"saved\x00\xfe")
            backup = f"{named}/../../20260202T000000-2-ef01/codex/swarm"
            write_legacy(self.home, [], [{"harnesses": ["grok"], "original": str(swarm), "backup": backup}])
            self.ok(run(self.home, a, "--harness", "grok", "uninstall", env=self.RELATIVE_HOME), "removed 0 links, restored 1 entries")
            self.assertEqual(swarm.read_bytes(), b"saved\x00\xfe")
            self.assertEqual(
                self.under_backups(),
                ["20260101T000000-1-abcd", "20260101T000000-1-abcd/grok", "20260202T000000-2-ef01", "20260202T000000-2-ef01/codex"],
            )
    def test_a_restore_under_a_config_home_whose_dot_dot_follows_a_symlink_leaves_the_stamp_and_harness_directories_in_the_state_directory(self):
            a = make_checkout(self.home, "a")
            (self.home / "else" / "sub").mkdir(parents=True)
            os.symlink(self.home / "else" / "sub", self.home / "x")
            env = {"XDG_CONFIG_HOME": f"{self.home}/x/../.config"}
            state = self.home / "else" / ".config" / "pstack-t3"
            swarm = provider_link(self.home, "grok", "swarm")
            saved = state_dir(self.home) / "backups" / "20260101T000000-1-abcd" / "grok" / "swarm"
            saved.parent.mkdir(parents=True)
            saved.write_bytes(b"user backup\x00\xff")
            unrelated = state / "backups" / "20260101T000000-1-abcd" / "grok"
            unrelated.mkdir(parents=True)
            manifest = state / "install-manifest.json"
            manifest.write_text(json.dumps({"links": [], "backups": [{"harnesses": ["grok"], "original": str(swarm), "backup": str(saved)}]}))
            self.ok(run(self.home, a, "--harness", "grok", "uninstall", env=env), "removed 0 links, restored 1 entries")
            self.assertEqual(swarm.read_bytes(), b"user backup\x00\xff")
            self.assertTrue(unrelated.is_dir())
            self.assertEqual(os.listdir(unrelated.parent), ["grok"])
            self.assertEqual(os.listdir(saved.parent), [])
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
    def test_a_restore_with_another_directory_mounted_at_the_stamp_under_the_state_directory_leaves_its_harness_directory(self):
            reason = bind_mounts_refused()
            if reason:
                self.skipTest(reason)
            a, env, state, saved = self.divergent_state()
            saved.parent.mkdir(parents=True)
            saved.write_bytes(b"user backup\x00\xff")
            unrelated = self.home / "unrelated_stamp" / "grok"
            unrelated.mkdir(parents=True)
            recorded = state_dir(self.home) / "backups"
            binds = ((recorded, state / "backups"), (unrelated.parent, state / "backups" / "20260101T000000-1-abcd"))
            self.ok(run_bound(self.home, a, binds, "--harness", "grok", "uninstall", env=env), "removed 0 links, restored 1 entries")
            self.assertEqual(provider_link(self.home, "grok", "swarm").read_bytes(), b"user backup\x00\xff")
            self.assertTrue(unrelated.is_dir())
            self.assertEqual(os.listdir(saved.parent), [])
    def test_a_restore_through_a_mount_of_a_harness_directory_of_the_state_directory_leaves_that_directory_and_its_stamp(self):
            reason = bind_mounts_refused()
            if reason:
                self.skipTest(reason)
            a, env, state, saved = self.divergent_state()
            real = state / "backups" / "20260101T000000-1-abcd" / "grok"
            real.mkdir(parents=True)
            (real / "swarm").write_bytes(b"user backup\x00\xff")
            saved.parent.mkdir(parents=True)
            self.ok(run_bound(self.home, a, ((real, saved.parent),), "--harness", "grok", "uninstall", env=env), "removed 0 links, restored 1 entries")
            self.assertEqual(provider_link(self.home, "grok", "swarm").read_bytes(), b"user backup\x00\xff")
            self.assertTrue(real.is_dir())
            self.assertEqual(os.listdir(real), [])
    def test_a_restore_whose_stamp_opens_under_backups_as_another_directory_leaves_its_harness_directory(self):
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            saved = state_dir(self.home) / "backups" / "20260101T000000-1-abcd" / "grok" / "swarm"
            saved.parent.mkdir(parents=True)
            saved.write_bytes(b"saved\x00\xfe")
            unrelated = self.home / "unrelated_stamp" / "grok"
            unrelated.mkdir(parents=True)
            write_legacy(self.home, [], [{"harnesses": ["grok"], "original": str(swarm), "backup": str(saved)}])
            # With a directory mounted at <stamp> below the state path as given, that name opens as the mounted directory under a backups descriptor opened through that path.
            code = (
                "os.environ['HOME'] = '.'\n"
                "opening, closing = os.open, os.close\n"
                "given = set()\n"
                "def mounted(path, flags, mode=0o777, *, dir_fd=None):\n"
                "    if dir_fd in given and path == '20260101T000000-1-abcd':\n"
                f"        return opening({str(unrelated.parent)!r}, flags, mode)\n"
                "    descriptor = opening(path, flags, mode, dir_fd=dir_fd)\n"
                "    if path == '.config/pstack-t3/backups':\n"
                "        given.add(descriptor)\n"
                "    return descriptor\n"
                "def closed(descriptor):\n"
                "    given.discard(descriptor)\n"
                "    closing(descriptor)\n"
                "os.open, os.close = mounted, closed\n"
            )
            self.ok(uninstall_hooked(self.home, a, code), "removed 0 links, restored 1 entries")
            self.assertEqual(swarm.read_bytes(), b"saved\x00\xfe")
            self.assertTrue(unrelated.is_dir())
            self.assertEqual(os.listdir(saved.parent), [])
    def test_an_uninstall_with_the_state_directory_behind_a_symlink_removes_the_emptied_directories_in_the_real_one(self):
            places = (
                ("~/.config/pstack-t3 is the symlink", lambda: state_dir(self.home), lambda real: real),
                ("~/.config is the symlink", lambda: state_dir(self.home).parent, lambda real: real / "pstack-t3"),
            )
            for where, link, real_state in places:
                with self.subTest(where=where):
                    self.use_fresh()
                    real = self.home / "dotfiles" / "kept"
                    real.mkdir(parents=True)
                    link().parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(real, link())
                    a = self.replaced_file()
                    swarm = provider_link(self.home, "grok", "swarm")
                    backups = real_state(real) / "backups"
                    stamps = os.listdir(backups)
                    self.assertEqual(len(stamps), 1, stamps)
                    self.assertEqual((backups / stamps[0] / "grok" / "swarm").read_bytes(), b"mine\x00grok\n")
                    self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
                    self.assertFalse(swarm.is_symlink())
                    self.assertEqual(swarm.read_bytes(), b"mine\x00grok\n")
                    self.assertEqual(os.readlink(link()), str(real))
                    self.assertFalse(backups.is_symlink())
                    self.assertEqual(os.listdir(backups), [])
                    self.assertEqual(sorted(os.listdir(real_state(real))), ["backups", "install-manifest.json", "install-owners"])
                    self.assertEqual(sorted(os.listdir(self.home / "dotfiles")), ["kept"])
    def follow(self, checkout, command):
            """Run `command`, a string doctor printed, the way a person who copied it does: in a shell, from the checkout.

            `python3` in that shell is this test run's interpreter.
            """
            with tempfile.TemporaryDirectory() as shims:
                shim = Path(shims) / "python3"
                shim.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
                shim.chmod(0o755)
                env = _env(self.home)
                env["PATH"] = shims + os.pathsep + env.get("PATH", os.defpath)
                return subprocess.run(["sh", "-c", command], env=env, cwd=checkout, capture_output=True, text=True)
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
    def inert_row(self):
            """Add a backup row with nothing at its backup path. Return the line doctor prints for it."""
            notes = provider_link(self.home, "grok", "notes")
            backup = state_dir(self.home) / "backups" / "20260101T000000-1-abcd" / "grok" / "notes"
            data = read_legacy(self.home)
            data["backups"].append({"harnesses": ["grok"], "original": str(notes), "backup": str(backup)})
            legacy_file(self.home).write_text(json.dumps(data, indent=2) + "\n")
            return (
                f"        backup row {backup}: nothing is there (recorded as the backup of {notes}); uninstall skips the row "
                f'while that path is empty; to drop it, delete the row from "backups" in {legacy_file(self.home)}'
            )
    def away_owner(self):
            """Install a second checkout for codex and delete it. Return the line doctor prints for its owner file."""
            gone = make_checkout(self.home, "gone")
            self.ok(run(self.home, gone, "--harness", "codex"), "linked 3 skills into codex")
            shutil.rmtree(gone)
            return (
                f"        {owner_file(self.home, gone)}: claims 3 links here for checkout {gone}, and no directory is at {gone}; "
                "delete this file unless that checkout will be back at that path; deleting it removes that checkout's claims "
                f"and removes no link they name and no row of {legacy_file(self.home)}"
            )
    def relink_line(self, swarm):
            return (
                f'        claim {swarm}: nothing is there; run "python3 scripts/install.py install --harness grok" to link it again, '
                'then "python3 scripts/install.py uninstall --harness grok" removes the link and this claim'
            )
    def test_doctor_names_a_claim_with_nothing_at_its_path_and_the_advised_commands_clear_it(self):
            a, swarm = self.installed_for_grok()
            swarm.unlink()
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [self.harness_line("grok", "2/3 pstack-t3, 1 missing"), self.relink_line(swarm)],
            )
            self.ok(self.follow(a, "python3 scripts/install.py install --harness grok"), "linked 1 skills into grok")
            self.ok(self.follow(a, "python3 scripts/install.py uninstall --harness grok"), "removed 3 links, restored 0 entries")
            self.assertIsNone(read_owner(self.home, a))
            self.assert_gone()
