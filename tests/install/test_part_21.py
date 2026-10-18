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


class Part21Test(OwnershipCase):
    def add_rows(self, links=(), backups=()):
            data = read_legacy(self.home)
            data["links"].extend(links)
            data["backups"].extend(backups)
            legacy_file(self.home).write_text(json.dumps(data, indent=2) + "\n")
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
    def test_an_uninstall_with_a_backup_row_for_a_linked_path_whose_backup_holds_a_nul_byte_removes_every_link_and_exits_0(self):
            a, swarm = self.installed_for_grok()
            row = {"harnesses": ["grok"], "original": str(swarm), "backup": f"{state_dir(self.home)}/backups/a\x00b/grok/swarm"}
            self.add_rows(backups=[row])
            removed = run(self.home, a, "uninstall", "--harness", "grok")
            self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
            self.assertEqual(removed.stdout, "removed 3 links, restored 0 entries\n")
            self.assertEqual(removed.stderr, "")
            self.assert_gone()
            self.assertEqual(read_legacy(self.home), {"links": [], "backups": [row]})
            self.assertIsNone(read_owner(self.home, a))
    def test_prune_empty_out_and_empty_out_matched_given_a_stamp_that_holds_a_nul_byte_return_without_raising(self):
            a = make_checkout(self.home, "a")
            state = str(state_dir(self.home))
            os.makedirs(f"{state}/backups")
            calls = (
                f"module.prune({state!r}, {state + '/backups/a' + chr(0) + 'b/grok/swarm'!r})",
                f"module.empty_out({state!r}, {'a' + chr(0) + 'b'!r}, 'grok')",
                f"module.empty_out_matched({state!r}, {state!r}, {'a' + chr(0) + 'b'!r}, 'grok')",
            )
            for call in calls:
                with self.subTest(call=call):
                    done = install_hooked(self.home, a, f"{call}\nprint('returned')\nsys.exit(0)")
                    self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                    self.assertEqual(done.stdout, "returned\n")
                    self.assertEqual(done.stderr, "")
            self.assertEqual(os.listdir(f"{state}/backups"), [])
    def doctor_with_surrogate_row(self, handler, surrogate):
            a, swarm = self.installed_for_grok()
            notes = provider_link(self.home, "grok", "notes")
            backup = f"{state_dir(self.home)}/backups/20260101T000000-1-abcd/grok/no{{surrogate}}tes"
            self.add_rows(backups=[{"harnesses": ["grok"], "original": str(notes), "backup": backup.format(surrogate=surrogate)}])
            checked = in_process.invoke(in_process.Invocation(
                program=a / "scripts" / "install.py",
                argv=("doctor", "--harness", "grok"),
                env={**_env(self.home), "PYTHONIOENCODING": f"utf-8:{handler}"},
                cwd=self.home,
                text=False,
            ))
            lines = (
                f"{self.harness_line('grok', '3/3 pstack-t3')}\n"
                f"        backup row {backup}: nothing is there (recorded as the backup of {notes}); uninstall skips the row "
                f'while that path is empty; to drop it, delete the row from "backups" in {legacy_file(self.home)}\n'
            )
            return checked, lines
    def test_doctor_prints_a_lone_surrogate_in_a_backup_row_as_a_backslash_escape_when_the_stdout_error_handler_refuses_it(self):
            refused = (("strict", "\ud800", "\\ud800"), ("surrogateescape", "\ud800", "\\ud800"), ("strict", "\udc80", "\\udc80"))
            for handler, surrogate, escape in refused:
                with self.subTest(handler=handler, escape=escape):
                    self.use_fresh()
                    checked, lines = self.doctor_with_surrogate_row(handler, surrogate)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertEqual(checked.stdout, lines.format(surrogate=escape).encode())
                    self.assertEqual(checked.stderr, b"")
    def test_doctor_under_a_surrogateescape_stdout_prints_the_byte_0x80_for_u_dc80_in_a_backup_row(self):
            checked, lines = self.doctor_with_surrogate_row("surrogateescape", "\udc80")
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            before, after = lines.split("{surrogate}")
            self.assertEqual(checked.stdout, before.encode() + b"\x80" + after.encode())
            self.assertEqual(checked.stderr, b"")
    def test_doctor_under_a_stdout_error_handler_name_that_is_not_registered_prints_a_backup_row_that_is_ascii(self):
            checked, lines = self.doctor_with_surrogate_row("unregistered", "")
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertEqual(checked.stdout, lines.format(surrogate="").encode())
            self.assertEqual(checked.stderr, b"")
    def test_the_record_files_an_install_writes_have_mode_600(self):
            a, swarm = self.installed_for_grok()
            self.assertEqual(os.stat(legacy_file(self.home)).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(owner_file(self.home, a)).st_mode & 0o777, 0o600)
    def empty_skills(self, checkout):
            shutil.rmtree(checkout / "skills")
            (checkout / "skills").mkdir()
    def test_install_in_a_checkout_whose_skills_directory_holds_no_skill_exits_1_with_one_line_on_stderr(self):
            a = make_checkout(self.home, "a")
            self.empty_skills(a)
            for command in (("install",), ("install", "--dry-run")):
                with self.subTest(command=command):
                    stopped = run(self.home, a, *command, "--harness", "grok")
                    self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
                    self.assertEqual(stopped.stdout, "")
                    self.assertEqual(stopped.stderr, self.NO_SKILL + "\n")
            self.assertFalse(state_dir(self.home).exists())
    def test_doctor_in_a_checkout_whose_skill_directories_hold_no_skill_md_prints_one_line_and_exits_1(self):
            a = make_checkout(self.home, "a")
            for name in NAMES:
                (a / "skills" / name / "SKILL.md").unlink()
            self.assertEqual(self.doctor(a, 1, "--harness", "grok"), [self.NO_SKILL])
    def test_uninstall_in_a_checkout_whose_skills_directory_holds_no_skill_removes_its_links(self):
            a, swarm = self.installed_for_grok()
            self.empty_skills(a)
            self.ok(run(self.home, a, "uninstall", "--harness", "grok"), "removed 3 links, restored 0 entries")
            for name in NAMES:
                self.assertFalse(os.path.lexists(provider_link(self.home, "grok", name)))
    def test_a_dry_run_under_a_relative_home_with_a_backup_row_recorded_as_an_absolute_path_whose_stamp_holds_a_nul_byte_prints_only_the_counts_line_and_exits_0(self):
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            (state_dir(self.home) / "backups").mkdir(parents=True)
            backup = f"{state_dir(self.home)}/backups/a\x00b/grok/swarm"
            write_legacy(self.home, [], [{"harnesses": ["grok"], "original": str(swarm), "backup": backup}])
            dry = run(self.home, a, "uninstall", "--dry-run", "--harness", "grok", env=self.RELATIVE_HOME)
            self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
            self.assertEqual(dry.stdout, "would remove 0 links, would restore 0 entries\n")
            self.assertNotIn("Traceback", dry.stderr)
