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


class Part15Test(OwnershipCase):
    def assert_displaced(self, path, kind):
            if kind == "directory":
                self.assertEqual(os.listdir(path), ["SKILL.md"])
                self.assertEqual((path / "SKILL.md").read_bytes(), b"displaced\x00\xfe")
            elif kind == "link":
                self.assertEqual(os.readlink(path), "/foreign/displaced")
            else:
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.read_bytes(), b"displaced\x00\xfe")
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
    def harness_line(self, harness, rest):
            return f"{harness:7} {provider_link(self.home, harness, 'swarm').parent}: {rest}"
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
    def installed_for_grok(self):
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "grok"), "linked 3 skills into grok")
            return a, provider_link(self.home, "grok", "swarm")
    def relink_line(self, swarm):
            return (
                f'        claim {swarm}: nothing is there; run "python3 scripts/install.py install --harness grok" to link it again, '
                'then "python3 scripts/install.py uninstall --harness grok" removes the link and this claim'
            )
    def test_doctor_does_not_name_a_claim_whose_link_another_checkout_moved_aside(self):
            a, b = self.base()
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [self.harness_line("grok", "0/3 pstack-t3, 3 taken by other copies (alpha, pstack-runtime, swarm)")],
            )
    def test_doctor_does_not_name_the_owner_file_of_a_checkout_that_still_exists(self):
            a, b = self.two()
            self.ok(run(self.home, a, "--harness", "grok"), "linked 3 skills into grok")
            self.ok(run(self.home, b, "--harness", "codex"), "linked 3 skills into codex")
            self.assertEqual(
                self.doctor(a, 1, "--harness", "codex,grok"),
                [
                    self.harness_line("codex", "0/3 pstack-t3, 3 taken by other copies (alpha, pstack-runtime, swarm)"),
                    self.harness_line("grok", "3/3 pstack-t3"),
                ],
            )
    def test_doctor_for_claude_prints_none_of_the_lines_it_prints_for_grok(self):
            a, swarm = self.installed_for_grok()
            swarm.unlink()
            row = self.inert_row()
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [self.harness_line("grok", "2/3 pstack-t3, 1 missing"), self.relink_line(swarm), row],
            )
            self.assertEqual(self.doctor(a, 1, "--harness", "claude"), [self.harness_line("claude", "0/3 pstack-t3, 3 missing")])
    def test_doctor_changes_nothing_in_a_home_with_a_stale_claim_an_inert_row_and_an_away_owner_file(self):
            a, swarm = self.installed_for_grok()
            swarm.unlink()
            row = self.inert_row()
            away = self.away_owner()
            before = snapshot(self.home)
            self.assertEqual(
                self.doctor(a, 1, "--harness", "codex,grok"),
                [
                    self.harness_line("codex", "0/3 pstack-t3, 3 missing"),
                    away,
                    self.harness_line("grok", "2/3 pstack-t3, 1 missing"),
                    self.relink_line(swarm),
                    row,
                ],
            )
            self.assertEqual(snapshot(self.home), before)
    def test_doctor_on_a_fresh_home_creates_no_state_directory(self):
            a = make_checkout(self.home, "a")
            self.assertEqual(self.doctor(a, 1, "--harness", "grok"), [self.harness_line("grok", "0/3 pstack-t3, 3 missing")])
            self.assertFalse(state_dir(self.home).exists())
            self.assertFalse(provider_link(self.home, "grok", "swarm").parent.exists())
    def test_doctor_exits_0_on_a_fully_linked_home_with_an_inert_row_and_an_away_owner_file(self):
            a, swarm = self.installed_for_grok()
            away = self.away_owner()
            for name in NAMES:
                provider_link(self.home, "codex", name).unlink()
            self.ok(run(self.home, a, "--harness", "codex"), "linked 3 skills into codex")
            row = self.inert_row()
            self.assertEqual(
                self.doctor(a, 0, "--harness", "codex,grok"),
                [self.harness_line("codex", "3/3 pstack-t3"), away, self.harness_line("grok", "3/3 pstack-t3"), row],
            )
    def assert_stops(self, checkout, text):
            for command in (("install",), ("uninstall",), ("uninstall", "--dry-run")):
                stopped = run(self.home, checkout, *command, "--harness", "grok")
                self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
                self.assertEqual(stopped.stderr, text + "\n")
                self.assertEqual(stopped.stdout, "")
    def test_doctor_names_a_manifest_that_is_not_json_and_still_checks_the_owner_files(self):
            a, swarm = self.installed_for_grok()
            away = self.away_owner()
            legacy_file(self.home).write_text("{not json\n")
            refusal = f"{legacy_file(self.home)} is not valid JSON; fix or move it and rerun"
            self.assertEqual(
                self.doctor(a, 1, "--harness", "codex,grok"),
                [
                    self.harness_line("codex", "0/3 pstack-t3, 3 missing"),
                    away,
                    self.harness_line("grok", "3/3 pstack-t3"),
                    f"{refusal} (doctor checked no claims and no backup rows)",
                ],
            )
            self.assert_stops(a, refusal)
            self.assertEqual(legacy_file(self.home).read_text(), "{not json\n")
    def test_doctor_names_this_checkouts_owner_file_that_is_not_json_and_still_checks_the_backup_rows(self):
            a, swarm = self.installed_for_grok()
            row = self.inert_row()
            owner = owner_file(self.home, a)
            owner.write_text("{not json\n")
            refusal = f"{owner} is not valid JSON; fix or move it and rerun"
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [self.harness_line("grok", "3/3 pstack-t3"), row, f"{refusal} (doctor checked no claims of this checkout)"],
            )
            self.assert_stops(a, refusal)
            self.assertEqual(owner.read_text(), "{not json\n")
    def test_doctor_names_another_checkouts_owner_file_it_cannot_read_a_checkout_from_and_still_checks_the_claims(self):
            cases = (
                ("{not json\n", "{file} is not valid JSON; fix or move it and rerun (doctor read no checkout from it)"),
                ('{"links": {}}\n', "{file} names no checkout (doctor read no checkout from it)"),
            )
            for text, line in cases:
                with self.subTest(text=text):
                    self.use_fresh()
                    a, swarm = self.installed_for_grok()
                    swarm.unlink()
                    other = owner_file(self.home, a).with_name("00aa11bb22cc33dd.json")
                    other.write_text(text)
                    self.assertEqual(
                        self.doctor(a, 1, "--harness", "grok"),
                        [self.harness_line("grok", "2/3 pstack-t3, 1 missing"), self.relink_line(swarm), line.format(file=other)],
                    )
                    self.ok(run(self.home, a, "--harness", "grok"), "linked 1 skills into grok")
                    self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
                    self.assertEqual(other.read_text(), text)
    def relink_group(self, count, harnesses, paths):
            return [
                f'        {count} claims have nothing at their paths; run "{self.INSTALL.format(harnesses=harnesses)}" to link them again, '
                f'then "{self.UNINSTALL.format(harnesses=harnesses)}" removes the links and these claims:',
                *(f"          {path}" for path in paths),
            ]
    def test_doctor_prints_two_claims_with_nothing_at_their_paths_as_one_group_and_the_advised_commands_clear_them(self):
            a, swarm = self.installed_for_grok()
            alpha = provider_link(self.home, "grok", "alpha")
            alpha.unlink()
            swarm.unlink()
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [self.harness_line("grok", "1/3 pstack-t3, 2 missing"), *self.relink_group(2, "grok", (alpha, swarm))],
            )
            self.ok(self.follow(a, self.INSTALL.format(harnesses="grok")), "linked 2 skills into grok")
            self.assert_grok_text(a)
            self.ok(self.follow(a, self.UNINSTALL.format(harnesses="grok")), "removed 3 links, restored 0 entries")
            self.assertIsNone(read_owner(self.home, a))
            self.assert_gone()
    def test_doctor_prints_one_group_under_each_of_two_harnesses_with_that_harness_in_its_commands(self):
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "codex,grok"), "linked 6 skills into codex, grok")
            for harness in ("codex", "grok"):
                for name in NAMES:
                    provider_link(self.home, harness, name).unlink()
            self.assertEqual(
                self.doctor(a, 1, "--harness", "codex,grok"),
                [
                    self.harness_line("codex", "0/3 pstack-t3, 3 missing"),
                    *self.relink_group(3, "codex", (provider_link(self.home, "codex", name) for name in NAMES)),
                    self.harness_line("grok", "0/3 pstack-t3, 3 missing"),
                    *self.relink_group(3, "grok", (provider_link(self.home, "grok", name) for name in NAMES)),
                ],
            )
    def test_doctor_prints_three_claims_at_a_file_a_directory_and_nothing_as_one_group_that_says_what_is_at_each(self):
            a, swarm = self.installed_for_grok()
            alpha, runtime = (provider_link(self.home, "grok", name) for name in ("alpha", "pstack-runtime"))
            for link in (alpha, runtime, swarm):
                link.unlink()
            alpha.write_bytes(b"taken\x00\xfe")
            runtime.mkdir()
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [
                    self.harness_line("grok", "0/3 pstack-t3, 2 taken by other copies (alpha, pstack-runtime), 1 missing"),
                    '        3 claims; "python3 scripts/install.py install --harness grok" stops on 2 taken paths; with --replace it '
                    "moves them aside and links the path of each claim (uninstall restores them and removes these claims):",
                    f"          {alpha}: a file is there",
                    f"          {runtime}: a directory is there",
                    f"          {swarm}: nothing is there",
                ],
            )
    def test_doctor_prints_two_claims_install_plans_no_link_for_as_one_group_that_names_the_owner_file_once(self):
            a, swarm = self.installed_for_grok()
            alpha = provider_link(self.home, "grok", "alpha")
            for link in (alpha, swarm):
                link.unlink()
                shutil.rmtree(a / "skills" / link.name)
            self.assertEqual(
                self.doctor(a, 0, "--harness", "grok"),
                [
                    self.harness_line("grok", "1/1 pstack-t3"),
                    "        2 claims; install plans no link at the path of any of them, so no command clears them; "
                    f"to drop one, delete the entry named by its path from {owner_file(self.home, a)}:",
                    f"          {alpha}: nothing is there",
                    f"          {swarm}: nothing is there",
                ],
            )
    def test_doctor_prints_two_backup_rows_with_nothing_at_their_backup_paths_as_one_group_that_names_the_manifest_once(self):
            a, swarm = self.installed_for_grok()
            stamp = state_dir(self.home) / "backups" / "20260101T000000-1-abcd" / "grok"
            rows = [(provider_link(self.home, "grok", name), stamp / name) for name in ("notes", "todo")]
            data = read_legacy(self.home)
            data["backups"].extend({"harnesses": ["grok"], "original": str(original), "backup": str(backup)} for original, backup in rows)
            legacy_file(self.home).write_text(json.dumps(data, indent=2) + "\n")
            self.assertEqual(
                self.doctor(a, 0, "--harness", "grok"),
                [
                    self.harness_line("grok", "3/3 pstack-t3"),
                    "        2 backup rows have nothing at their backup paths; uninstall skips each row while its backup path is empty; "
                    f'to drop one, delete the row from "backups" in {legacy_file(self.home)}:',
                    *(f"          {backup} (recorded as the backup of {original})" for original, backup in rows),
                ],
            )
    def test_doctor_prints_a_claim_and_a_backup_row_that_each_have_an_entry_in_a_holder_as_one_group(self):
            a, swarm, rows = self.displaced_by("directory")
            alpha = provider_link(self.home, "grok", "alpha")
            backup = Path(rows[0]["backup"])
            raced = uninstall_hooked(
                self.home,
                a,
                UNLINK_ASIDE.replace("'/swarm'", "'/alpha'") + KILLED_ASIDE,
                reason=in_process.Reason.INTERPRETER_EXIT,
            )
            held_link, held_backup = set_aside(alpha), set_aside(backup)
            self.assertEqual((len(held_link), len(held_backup)), (1, 1), raced.stdout + raced.stderr)
            self.assertEqual(os.readlink(held_link[0]), str(a / "skills" / "alpha"))
            self.assert_displaced(held_backup[0], "directory")
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [
                    self.harness_line("grok", "0/3 pstack-t3, 3 missing"),
                    '        2 records; "python3 scripts/install.py uninstall --dry-run" prints what the next run does with '
                    "the entry held for each:",
                    f"          claim {alpha}: nothing is there, and {held_link[0]} holds this checkout's link for it",
                    f"          backup row {backup}: nothing is there, and {held_backup[0]} holds an entry under that name",
                ],
            )
