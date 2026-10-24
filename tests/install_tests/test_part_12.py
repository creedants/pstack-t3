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


class Part12Test(OwnershipCase):
    def under_backups(self):
            backups = state_dir(self.home) / "backups"
            return sorted(str(path.relative_to(backups)) for path in backups.rglob("*"))
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
    def test_a_restore_through_a_symlink_at_backups_leaves_the_stamp_and_harness_directories_it_reaches(self):
            link, real, emptied = self.restore_through_symlink("backups")
            self.assertEqual(os.listdir(real), ["20260101T000000-1-abcd"])
            self.assertEqual(os.listdir(emptied.parent), ["grok"])
    def test_a_restore_through_a_symlink_at_a_stamp_leaves_the_harness_directory_it_reaches(self):
            link, real, emptied = self.restore_through_symlink("stamp")
            self.assertEqual(os.listdir(real), ["grok"])
            self.assertEqual(os.listdir(link.parent), [link.name])
    def test_a_restore_through_a_symlink_at_a_harness_leaves_the_link_its_stamp_and_the_directory_it_points_at(self):
            link, real, emptied = self.restore_through_symlink("harness")
            self.assertEqual(real, emptied)
            self.assertEqual(os.listdir(link.parent), ["grok"])
            self.assertEqual(os.listdir(link.parent.parent), [link.parent.name])
    def ignored_line(self):
            return f"XDG_CONFIG_HOME='.config' is not an absolute path, so it is ignored and the config home is {str(self.home / '.config')!r}\n"
    def another_directory(self):
            work = self.home / "work"
            work.mkdir()
            return work
    def test_an_install_with_replace_and_an_uninstall_under_a_relative_xdg_config_home_run_from_another_directory_keep_their_records_under_the_default_config_home(self):
            a = make_checkout(self.home, "a")
            work = self.another_directory()
            swarm = provider_link(self.home, "grok", "swarm")
            swarm.parent.mkdir(parents=True)
            swarm.write_bytes(b"mine\x00grok\n")
            linked = run(self.home, a, "--harness", "grok", "--replace", env=self.RELATIVE_XDG, cwd=work)
            self.assertEqual(linked.returncode, 0, linked.stdout + linked.stderr)
            self.assertEqual(linked.stdout, f"linked 3 skills into grok\nmanifest: {legacy_file(self.home)}\n")
            self.assertEqual(linked.stderr, self.ignored_line())
            stamps = os.listdir(state_dir(self.home) / "backups")
            self.assertEqual(len(stamps), 1, stamps)
            self.assertEqual([row["backup"] for row in read_legacy(self.home)["backups"]], [f"{state_dir(self.home)}/backups/{stamps[0]}/grok/swarm"])
            removed = run(self.home, a, "--harness", "grok", "uninstall", env=self.RELATIVE_XDG, cwd=work)
            self.assertEqual(removed.returncode, 0, removed.stdout + removed.stderr)
            self.assertEqual(removed.stdout, "removed 3 links, restored 1 entries\n")
            self.assertEqual(removed.stderr, self.ignored_line())
            self.assertFalse(swarm.is_symlink())
            self.assertEqual(swarm.read_bytes(), b"mine\x00grok\n")
            self.assertEqual(self.under_backups(), [])
            self.assertEqual(os.listdir(work), [])
    def test_each_command_under_a_relative_xdg_config_home_prints_the_ignored_line_once_and_the_stdout_and_exit_status_it_has_with_the_variable_unset(self):
            for variable, env in (("unset", None), ("relative", self.RELATIVE_XDG)):
                self.use_fresh()
                a = make_checkout(self.home, "a")
                work = self.another_directory()
                directory = provider_link(self.home, "grok", "swarm").parent
                manifest = f"manifest: {legacy_file(self.home)}"
                planned = [f"would link {directory / name} -> {a / 'skills' / name}" for name in NAMES]
                runs = (
                    (("doctor",), 1, [f"grok    {directory}: 0/3 pstack-t3, 3 missing"]),
                    (("install", "--dry-run"), 0, [*planned, "3 links planned"]),
                    (("install",), 0, ["linked 3 skills into grok", manifest]),
                    (("install",), 0, ["linked 0 skills into nothing (already installed)", manifest]),
                    (("doctor",), 0, [f"grok    {directory}: 3/3 pstack-t3"]),
                    (("uninstall", "--dry-run"), 0, ["would remove 3 links, would restore 0 entries"]),
                    (("uninstall",), 0, ["removed 3 links, restored 0 entries"]),
                )
                for index, (command, status, lines) in enumerate(runs):
                    with self.subTest(variable=variable, index=index, command=command):
                        done = run(self.home, a, *command, "--harness", "grok", env=env, cwd=work)
                        self.assertEqual(done.returncode, status, done.stdout + done.stderr)
                        self.assertEqual(done.stdout.splitlines(), lines)
                        self.assertEqual(done.stderr, self.ignored_line() if env else "")
                self.assertEqual(os.listdir(work), [])
    def test_a_command_that_does_not_read_xdg_config_home_prints_no_ignored_line(self):
            a = make_checkout(self.home, "a")
            work = self.another_directory()
            project = self.home / "project"
            project.mkdir()
            for command in ("install", "doctor", "uninstall"):
                with self.subTest(command=command, project=True):
                    done = run(self.home, a, command, "--harness", "grok", f"--project={project}", env=self.RELATIVE_XDG, cwd=work)
                    self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                    self.assertEqual(done.stderr, "")
            for flags, status in ((("--help",), 0), (("--harness", "nope"), 2)):
                with self.subTest(flags=flags):
                    if flags == ("--help",):
                        done = in_process.finished(in_process.Reason.HELP, in_process.Invocation(
                            program=a / "scripts" / "install.py",
                            argv=flags,
                            env={**_env(self.home), **self.RELATIVE_XDG},
                            cwd=work,
                        ))
                    else:
                        done = run(self.home, a, *flags, env=self.RELATIVE_XDG, cwd=work)
                    self.assertEqual(done.returncode, status, done.stdout + done.stderr)
                    self.assertNotIn("XDG_CONFIG_HOME", done.stderr)
            (a / "skills").rename(a / "skills.away")
            stopped = run(self.home, a, "install", "--harness", "grok", env=self.RELATIVE_XDG, cwd=work)
            self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
            self.assertEqual(stopped.stderr, self.UNBUILT + "\n")
            checked = run(self.home, a, "doctor", "--harness", "grok", env=self.RELATIVE_XDG, cwd=work)
            self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
            self.assertEqual(checked.stdout, self.UNBUILT + "\n")
            self.assertEqual(checked.stderr, "")
    def test_an_empty_or_absolute_xdg_config_home_prints_nothing_on_stderr(self):
            values = (
                ("empty", lambda: "", lambda: self.home / ".config"),
                ("absolute", lambda: f"{self.home}/kept", lambda: f"{self.home}/kept"),
                ("absolute with dot dot", lambda: f"{self.home}/x/../kept", lambda: f"{self.home}/x/../kept"),
            )
            for name, value, config in values:
                with self.subTest(value=name):
                    self.use_fresh()
                    a = make_checkout(self.home, "a")
                    (self.home / "x").mkdir()
                    done = run(self.home, a, "--harness", "grok", env={"XDG_CONFIG_HOME": value()}, cwd=self.another_directory())
                    self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                    self.assertEqual(done.stdout, f"linked 3 skills into grok\nmanifest: {config()}/pstack-t3/install-manifest.json\n")
                    self.assertEqual(done.stderr, "")
    def test_no_command_under_a_relative_xdg_config_home_changes_an_installation_recorded_under_that_path_in_the_working_directory(self):
            a = make_checkout(self.home, "a", old=True)
            work = self.another_directory()
            swarm = provider_link(self.home, "grok", "swarm")
            swarm.parent.mkdir(parents=True)
            swarm.write_bytes(b"mine\x00grok\n")
            self.ok(run(self.home, a, "--harness", "grok", "--replace", env=self.RELATIVE_XDG, cwd=work), "linked 3 skills into grok")
            self.assertEqual(sorted(os.listdir(work / ".config" / "pstack-t3")), ["backups", "install-manifest.json"])
            upgrade(a)
            before = snapshot(work)
            directory = swarm.parent
            manifest = f"manifest: {legacy_file(self.home)}"
            runs = (
                (("doctor",), [f"grok    {directory}: 3/3 pstack-t3"]),
                (("install", "--dry-run"), [f"3 {UNTRACKED}", "0 links planned"]),
                (("install",), ["linked 0 skills into nothing (already installed)", f"3 {UNTRACKED}", manifest]),
                (("uninstall", "--dry-run"), ["would remove 0 links, would restore 0 entries"]),
                (("uninstall",), ["removed 0 links, restored 0 entries"]),
            )
            for command, lines in runs:
                with self.subTest(command=command):
                    done = run(self.home, a, *command, "--harness", "grok", env=self.RELATIVE_XDG, cwd=work)
                    self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                    self.assertEqual(done.stdout.splitlines(), lines)
                    self.assertEqual(done.stderr, self.ignored_line())
                    self.assertEqual(snapshot(work), before)
            self.assertFalse(os.path.lexists(self.home / ".config"))
            self.assert_grok_text(a)
    def test_an_uninstall_under_a_config_home_spelled_with_dot_dot_restores_a_replaced_file_and_leaves_backups_empty(self):
            a = make_checkout(self.home, "a")
            (self.home / "x").mkdir()
            env = {"XDG_CONFIG_HOME": f"{self.home}/x/../.config"}
            swarm = provider_link(self.home, "grok", "swarm")
            swarm.parent.mkdir(parents=True)
            swarm.write_bytes(b"mine\x00grok\n")
            self.ok(run(self.home, a, "--harness", "grok", "--replace", env=env), "linked 3 skills into grok")
            self.assertEqual(len(self.under_backups()), 3)
            self.ok(run(self.home, a, "--harness", "grok", "uninstall", env=env), "removed 3 links, restored 1 entries")
            self.assertFalse(swarm.is_symlink())
            self.assertEqual(swarm.read_bytes(), b"mine\x00grok\n")
            self.assertEqual(self.under_backups(), [])
    def restore_recorded_as(self, backup):
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            saved = state_dir(self.home) / "backups" / "20260101T000000-1-abcd" / "grok" / "swarm"
            saved.parent.mkdir(parents=True)
            saved.write_bytes(b"saved\x00\xfe")
            write_legacy(self.home, [], [{"harnesses": ["grok"], "original": str(swarm), "backup": backup}])
            self.ok(run(self.home, a, "--harness", "grok", "uninstall", env=self.RELATIVE_HOME), "removed 0 links, restored 1 entries")
            self.assertEqual(swarm.read_bytes(), b"saved\x00\xfe")
    def test_a_restore_under_a_relative_home_from_a_backup_recorded_as_an_absolute_path_leaves_backups_empty(self):
            self.restore_recorded_as(f"{self.home}/.config/pstack-t3/backups/20260101T000000-1-abcd/grok/swarm")
            self.assertEqual(self.under_backups(), [])
    def test_a_restore_under_a_relative_home_whose_config_home_is_a_symlink_from_a_backup_recorded_as_an_absolute_path_leaves_backups_empty(self):
            real = self.home / "dotfiles" / "kept"
            real.mkdir(parents=True)
            os.symlink(real, self.home / ".config")
            self.restore_recorded_as(f"{self.home}/.config/pstack-t3/backups/20260101T000000-1-abcd/grok/swarm")
            self.assertEqual(os.listdir(real / "pstack-t3" / "backups"), [])
