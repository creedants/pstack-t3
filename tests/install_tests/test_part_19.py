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


class Part19Test(OwnershipCase):
    def another_directory(self):
            work = self.home / "work"
            work.mkdir()
            return work
    def doctor(self, checkout, status, *args):
            result = run(self.home, checkout, "doctor", *args)
            self.assertEqual(result.returncode, status, result.stdout + result.stderr)
            self.assertEqual(result.stderr, "")
            return result.stdout.splitlines()
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
    def harness_line(self, harness, rest):
            return f"{harness:7} {provider_link(self.home, harness, 'swarm').parent}: {rest}"
    def installed_for_grok(self):
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "grok"), "linked 3 skills into grok")
            return a, provider_link(self.home, "grok", "swarm")
    def under_backups(self):
            backups = state_dir(self.home) / "backups"
            return sorted(str(path.relative_to(backups)) for path in backups.rglob("*"))
    @unittest.skipIf(os.geteuid() == 0, "root reads a file of mode 000")
    def test_an_install_whose_manifest_turns_mode_000_while_a_link_fails_prints_the_skipped_line_and_exits_1_with_the_manifest_line(self):
            self.addCleanup(lambda: legacy_file(self.home).chmod(0o644))
            stopped, manifest = self.failing_link(self.NO_SPACE, "os.chmod(manifest, 0)")
            self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
            self.assertEqual(stopped.stdout, f"skipped link {provider_link(self.home, 'grok', 'alpha')}: [Errno 28] No space left on device\n")
            self.assertEqual(stopped.stderr, f"{manifest} could not be read (Permission denied); clear that error and rerun\n")
    def test_an_install_whose_manifest_stops_being_json_while_a_link_declines_prints_the_skipped_line_and_exits_1_with_the_manifest_line(self):
            stopped, manifest = self.failing_link("return 'the step declined'", self.NOT_JSON)
            self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
            self.assertEqual(stopped.stdout, f"skipped link {provider_link(self.home, 'grok', 'alpha')}: the step declined\n")
            self.assertEqual(stopped.stderr, f"{manifest} is not valid JSON; fix or move it and rerun\n")
    def from_removed_directory(self, checkout, *args, env=None):
            """Run this checkout's installer with `args` from a working directory that is removed before the installer starts.

            A subprocess cannot start in a removed directory, so the hook makes one, enters it, and removes it.
            The hook first sets each variable of `env`.
            """
            gone = str(self.home / "gone")
            code = f"os.environ.update({env or {}!r})\nos.mkdir({gone!r})\nos.chdir({gone!r})\nos.rmdir({gone!r})\n"
            return install_hooked(self.home, checkout, code, *args)
    def assert_adrift(self, checkout, file, unchecked):
            """From a removed working directory, assert that install, uninstall, and uninstall --dry-run exit 1 with the ADRIFT line for `file` alone on stderr,

            that doctor prints its harness line and that line with `unchecked` in parentheses at exit 1, and that no record or link changed.
            """
            line = self.ADRIFT.format(file=file)
            kept = (state_dir(self.home), provider_link(self.home, "grok", "swarm").parent)
            before = [snapshot(path) for path in kept]
            for command in (("install",), ("uninstall",), ("uninstall", "--dry-run")):
                stopped = self.from_removed_directory(checkout, *command, "--harness", "grok")
                self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
                self.assertEqual(stopped.stderr, line + "\n")
                self.assertEqual(stopped.stdout, "")
            checked = self.from_removed_directory(checkout, "doctor", "--harness", "grok")
            self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
            self.assertEqual(checked.stderr, "")
            self.assertEqual(checked.stdout.splitlines(), [self.harness_line("grok", "3/3 pstack-t3"), f"{line} ({unchecked})"])
            self.assertEqual([snapshot(path) for path in kept], before)
    def add_rows(self, links=(), backups=()):
            data = read_legacy(self.home)
            data["links"].extend(links)
            data["backups"].extend(backups)
            legacy_file(self.home).write_text(json.dumps(data, indent=2) + "\n")
    def test_a_link_row_that_is_a_relative_string_read_from_a_removed_working_directory_stops_install_and_uninstall_with_one_line_and_gives_doctor_that_line_at_exit_1(self):
            a, swarm = self.installed_for_grok()
            self.add_rows(links=["rel/skills/swarm"])
            self.assert_adrift(a, legacy_file(self.home), self.NO_MANIFEST)
    def test_a_backup_row_with_no_harnesses_and_a_relative_original_read_from_a_removed_working_directory_stops_install_and_uninstall_with_one_line_and_gives_doctor_that_line_at_exit_1(self):
            a, swarm = self.installed_for_grok()
            self.add_rows(backups=[{"original": "rel/skills/swarm", "backup": "/nonexistent/b"}])
            self.assert_adrift(a, legacy_file(self.home), self.NO_MANIFEST)
    def test_a_link_row_with_harnesses_and_a_relative_path_read_from_a_removed_working_directory_stops_install_and_uninstall_with_one_line_and_gives_doctor_that_line_at_exit_1(self):
            a, swarm = self.installed_for_grok()
            self.add_rows(links=[{"harnesses": ["grok"], "path": "rel/skills/swarm"}])
            self.assert_adrift(a, legacy_file(self.home), self.NO_MANIFEST)
    def test_a_claim_keyed_by_a_relative_path_read_from_a_removed_working_directory_stops_install_and_uninstall_with_one_line_and_gives_doctor_that_line_at_exit_1(self):
            a, swarm = self.installed_for_grok()
            write_owner(self.home, a, [*grok_paths(self.home), "rel/skills/swarm"])
            self.assert_adrift(a, owner_file(self.home, a), self.NO_CLAIMS)
    def test_a_backup_row_with_harnesses_and_a_relative_original_read_from_a_removed_working_directory_stops_install_and_uninstall_with_one_line_and_gives_doctor_that_line_at_exit_1(self):
            a, swarm = self.installed_for_grok()
            kept = self.home / "kept-backup"
            kept.write_bytes(b"saved\x00\xfe")
            self.add_rows(backups=[{"harnesses": ["grok"], "original": "rel/skills/swarm", "backup": str(kept)}])
            self.assert_adrift(a, legacy_file(self.home), self.NO_MANIFEST)
    def test_a_relative_project_path_given_from_a_removed_working_directory_stops_install_uninstall_and_doctor_with_one_line_on_stderr(self):
            a, swarm = self.installed_for_grok()
            line = (
                "the --project path rel is relative, and the system could not name this run's working directory "
                "(No such file or directory); change to another directory and rerun, or give --project a full path\n"
            )
            for command in (("install",), ("uninstall",), ("uninstall", "--dry-run"), ("doctor",)):
                with self.subTest(command=command):
                    stopped = self.from_removed_directory(a, *command, "--harness", "grok", "--project=rel")
                    self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
                    self.assertEqual(stopped.stderr, line)
                    self.assertEqual(stopped.stdout, "")
    def test_a_relative_or_empty_home_or_a_relative_claude_config_dir_from_a_removed_working_directory_stops_install_uninstall_and_doctor_with_one_line_on_stderr(self):
            a = make_checkout(self.home, "a")
            line = "{name}={value!r} is not an absolute path, and the system could not name this run's working directory (No such file or directory); change to another directory and rerun\n"
            for name, value in (("HOME", "."), ("HOME", ""), ("CLAUDE_CONFIG_DIR", "cc")):
                for command in (("install",), ("uninstall",), ("uninstall", "--dry-run"), ("doctor",)):
                    with self.subTest(name=name, value=value, command=command):
                        stopped = self.from_removed_directory(a, *command, "--harness", "grok", env={name: value})
                        self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
                        self.assertEqual(stopped.stderr, line.format(name=name, value=value))
                        self.assertEqual(stopped.stdout, "")
            self.assertFalse(os.path.lexists(state_dir(self.home)))
    def test_install_and_doctor_under_a_relative_home_from_a_removed_working_directory_in_a_checkout_with_no_skills_directory_print_the_skills_is_missing_line(self):
            a = make_checkout(self.home, "a")
            (a / "skills").rename(a / "skills.away")
            for command, stdout, stderr in (("install", "", self.UNBUILT + "\n"), ("doctor", self.UNBUILT + "\n", "")):
                with self.subTest(command=command):
                    stopped = self.from_removed_directory(a, command, "--harness", "grok", env=self.RELATIVE_HOME)
                    self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
                    self.assertEqual(stopped.stdout, stdout)
                    self.assertEqual(stopped.stderr, stderr)
    def test_doctor_with_a_relative_project_path_under_a_relative_home_from_a_removed_working_directory_prints_the_project_path_line(self):
            a = make_checkout(self.home, "a")
            stopped = self.from_removed_directory(a, "doctor", "--harness", "grok", "--project=p", env=self.RELATIVE_HOME)
            self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
            self.assertEqual(
                stopped.stderr,
                "the --project path p is relative, and the system could not name this run's working directory "
                "(No such file or directory); change to another directory and rerun, or give --project a full path\n",
            )
            self.assertEqual(stopped.stdout, "")
    def test_doctor_under_a_relative_home_from_a_working_directory_that_exists_prints_nothing_on_stderr(self):
            a = make_checkout(self.home, "a")
            checked = run(self.home, a, "doctor", "--harness", "grok", env=self.RELATIVE_HOME)
            self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
            self.assertEqual(checked.stdout, "grok    .grok/skills: 0/3 pstack-t3, 3 missing\n")
            self.assertEqual(checked.stderr, "")
    def test_a_link_row_that_is_a_relative_string_read_from_a_working_directory_that_exists_leaves_the_dry_run_and_doctor_output_as_it_was(self):
            a, swarm = self.installed_for_grok()
            self.add_rows(links=["rel/skills/swarm"])
            dry = run(self.home, a, "uninstall", "--dry-run", "--harness", "grok")
            self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
            self.assertEqual(dry.stdout, "would remove 3 links, would restore 0 entries\n")
            self.assertEqual(dry.stderr, "")
            self.assertEqual(self.doctor(a, 0, "--harness", "grok"), [self.harness_line("grok", "3/3 pstack-t3")])
    def test_a_backup_row_with_a_relative_backup_path_read_from_a_removed_working_directory_does_not_stop_uninstall(self):
            a, swarm = self.installed_for_grok()
            notes = provider_link(self.home, "grok", "notes")
            self.add_rows(backups=[{"harnesses": ["grok"], "original": str(notes), "backup": "rel/backups/notes"}])
            removed = self.from_removed_directory(a, "uninstall", "--harness", "grok")
            self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
            self.assertEqual(
                removed.stdout,
                f"kept backup row rel/backups/notes: a relative path with nothing at it from this working directory (recorded as the backup of {notes}); "
                "uninstall reads a relative path from the directory it runs in\n"
                "removed 3 links, restored 0 entries\n",
            )
            self.assertEqual(removed.stderr, "")
            self.assertFalse(os.path.lexists(swarm))
    def relative_row(self):
            a, swarm = self.installed_for_grok()
            saved = self.home / self.RELATIVE_ROW
            saved.parent.mkdir(parents=True)
            saved.write_bytes(b"saved\x00\xfe")
            self.add_rows(backups=[{"harnesses": ["grok"], "original": str(swarm), "backup": self.RELATIVE_ROW}])
            return a, swarm, saved
    def test_an_uninstall_from_the_home_directory_restores_from_a_backup_row_recorded_as_a_relative_path_and_leaves_its_stamp_and_harness_directories(self):
            a, swarm, saved = self.relative_row()
            removed = run(self.home, a, "uninstall", "--harness", "grok")
            self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
            self.assertEqual(removed.stdout, "removed 3 links, restored 1 entries\n")
            self.assertEqual(removed.stderr, "")
            self.assertEqual(swarm.read_bytes(), b"saved\x00\xfe")
            self.assertEqual(read_legacy(self.home)["backups"], [])
            self.assertEqual(self.under_backups(), ["20260101T000000-1-abcd", "20260101T000000-1-abcd/grok"])
    def test_an_uninstall_from_another_directory_keeps_a_backup_row_recorded_as_a_relative_path_and_prints_the_kept_backup_row_line(self):
            a, swarm, saved = self.relative_row()
            work = self.another_directory()
            row = {"harnesses": ["grok"], "original": str(swarm), "backup": self.RELATIVE_ROW}
            kept = (
                f"kept backup row {self.RELATIVE_ROW}: a relative path with nothing at it from this working directory (recorded as the backup of {swarm}); "
                "uninstall reads a relative path from the directory it runs in"
            )
            for command, counts in (
                (("uninstall", "--dry-run"), "would remove 3 links, would restore 0 entries"),
                (("uninstall",), "removed 3 links, restored 0 entries"),
            ):
                with self.subTest(command=command):
                    removed = run(self.home, a, *command, "--harness", "grok", cwd=work)
                    self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
                    self.assertEqual(removed.stdout.splitlines(), [kept, counts])
                    self.assertEqual(removed.stderr, "")
                    self.assertEqual(read_legacy(self.home)["backups"], [row])
                    self.assertEqual(saved.read_bytes(), b"saved\x00\xfe")
            self.assertFalse(os.path.lexists(swarm))
            self.assertEqual(os.listdir(work), [])
            restored = run(self.home, a, "uninstall", "--harness", "grok")
            self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
            self.assertEqual(restored.stdout, "removed 0 links, restored 1 entries\n")
            self.assertEqual(swarm.read_bytes(), b"saved\x00\xfe")
