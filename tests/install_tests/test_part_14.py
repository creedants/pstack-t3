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


class Part14Test(OwnershipCase):
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
    def empty_skills(self, checkout):
            shutil.rmtree(checkout / "skills")
            (checkout / "skills").mkdir()
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
    def test_doctor_names_a_claim_whose_path_is_taken_and_the_advised_commands_clear_it(self):
            foreign = "foreign-dir"
            kinds = (
                ("file", lambda swarm: swarm.write_bytes(b"taken\x00\xfe"), "a file is there"),
                ("directory", lambda swarm: swarm.mkdir(), "a directory is there"),
                ("repointed link", lambda swarm: os.symlink(self.home / foreign, swarm), "a link to {foreign} is there"),
            )
            for kind, take, there in kinds:
                with self.subTest(kind=kind):
                    self.use_fresh()
                    a, swarm = self.installed_for_grok()
                    (self.home / foreign).mkdir()
                    swarm.unlink()
                    take(swarm)
                    self.assertEqual(
                        self.doctor(a, 1, "--harness", "grok"),
                        [
                            self.harness_line("grok", "2/3 pstack-t3, 1 taken by other copies (swarm)"),
                            f"        claim {swarm}: {there.format(foreign=self.home / foreign)}; "
                            '"python3 scripts/install.py install --harness grok" stops on 1 taken paths; with --replace it moves '
                            "them aside and links this path (uninstall restores them and removes this claim)",
                        ],
                    )
                    taken = snapshot(swarm.parent)
                    advised = "python3 scripts/install.py install --harness grok"
                    stopped = self.follow(a, advised)
                    self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
                    self.assertIn("these skills already exist", stopped.stderr)
                    self.ok(self.follow(a, advised + " --replace"), "linked 1 skills into grok")
                    self.assert_grok_text(a)
                    self.ok(run(self.home, a, "uninstall", "--harness", "grok"), "removed 3 links, restored 1 entries")
                    self.assertIsNone(read_owner(self.home, a))
                    self.assertEqual(snapshot(swarm.parent), tuple(row for row in taken if row[0] == "swarm"))
    def test_doctor_names_a_claim_install_plans_no_link_for(self):
            a, swarm = self.installed_for_grok()
            swarm.unlink()
            shutil.rmtree(a / "skills" / "swarm")
            self.assertEqual(
                self.doctor(a, 0, "--harness", "grok"),
                [
                    self.harness_line("grok", "2/2 pstack-t3"),
                    f"        claim {swarm}: nothing is there; install plans no link at that path, so no command clears this claim; "
                    f'to drop it, delete the "{swarm}" entry from {owner_file(self.home, a)}',
                ],
            )
    def test_doctor_in_a_checkout_with_no_skills_directory_prints_one_line_and_exits_1(self):
            def fresh():
                return make_checkout(self.home, "a")

            def dangling():
                return self.installed_for_grok()[0]

            def stale():
                a, swarm = self.installed_for_grok()
                for name in NAMES:
                    provider_link(self.home, "grok", name).unlink()
                return a

            for home, build in (("fresh", fresh), ("links that dangle", dangling), ("stale claims", stale)):
                with self.subTest(home=home):
                    self.use_fresh()
                    a = build()
                    (a / "skills").rename(a / "skills.away")
                    existed = state_dir(self.home).exists()
                    self.assertEqual(self.doctor(a, 1, "--harness", "grok"), [self.UNBUILT])
                    self.assertEqual(state_dir(self.home).exists(), existed)
                    self.assertEqual(existed, home != "fresh")
    def test_doctor_in_a_checkout_with_no_skills_directory_and_a_manifest_that_is_not_json_prints_only_the_skills_line(self):
            a, swarm = self.installed_for_grok()
            legacy_file(self.home).write_text("{not json\n")
            (a / "skills").rename(a / "skills.away")
            self.assertEqual(self.doctor(a, 1, "--harness", "grok"), [self.UNBUILT])
    def test_install_in_a_checkout_with_no_skills_directory_exits_1_with_one_line_on_stderr(self):
            a, swarm = self.installed_for_grok()
            swarm.unlink()
            (a / "skills").rename(a / "skills.away")
            stopped = run(self.home, a, "install", "--harness", "grok")
            self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
            self.assertEqual(stopped.stdout, "")
            self.assertEqual(stopped.stderr, self.UNBUILT + "\n")
    def test_install_and_doctor_in_a_checkout_whose_skills_is_a_file_or_a_dangling_symlink_print_the_not_a_directory_line_and_exit_1(self):
            def file(skills):
                skills.write_bytes(b"mine\x00\xfe")

            def dangling(skills):
                os.symlink("nowhere", skills)

            for entry, place in (("file", file), ("dangling symlink", dangling)):
                with self.subTest(entry=entry):
                    self.use_fresh()
                    a = make_checkout(self.home, "a")
                    shutil.rmtree(a / "skills")
                    place(a / "skills")
                    before = snapshot(self.home)
                    stopped = run(self.home, a, "install", "--harness", "grok")
                    self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
                    self.assertEqual(stopped.stdout, "")
                    self.assertEqual(stopped.stderr, self.NOT_A_DIRECTORY + "\n")
                    self.assertEqual(self.doctor(a, 1, "--harness", "grok"), [self.NOT_A_DIRECTORY])
                    self.assertEqual(snapshot(self.home), before)
    def test_doctor_in_a_checkout_whose_skills_directory_holds_no_skill_prints_one_line_and_exits_1(self):
            def fresh():
                return make_checkout(self.home, "a")

            def dangling():
                return self.installed_for_grok()[0]

            def stale():
                a, swarm = self.installed_for_grok()
                for name in NAMES:
                    provider_link(self.home, "grok", name).unlink()
                return a

            for home, build in (("fresh", fresh), ("links that dangle", dangling), ("stale claims", stale)):
                with self.subTest(home=home):
                    self.use_fresh()
                    a = build()
                    self.empty_skills(a)
                    existed = state_dir(self.home).exists()
                    self.assertEqual(self.doctor(a, 1, "--harness", "grok"), [self.NO_SKILL])
                    self.assertEqual(state_dir(self.home).exists(), existed)
                    self.assertEqual(existed, home != "fresh")
    def test_the_commands_doctor_prints_for_a_project_path_with_a_space_quotes_and_a_semicolon_clear_the_claim_in_a_shell(self):
            a = make_checkout(self.home, "a")
            project = self.home / """a "b" 'c'; touch ADVICE_RAN"""
            self.clear_the_claim_as_printed(a, project, ("--project", str(project)), str(project))
    def test_the_commands_doctor_prints_for_a_project_path_that_begins_with_a_dash_clear_the_claim_in_a_shell(self):
            a = make_checkout(self.home, "a")
            install, uninstall = self.clear_the_claim_as_printed(a, a / "-project", ("--project=-project",), "-project")
            self.assertEqual(install, "python3 scripts/install.py install --harness grok --project=-project")
            self.assertEqual(uninstall, "python3 scripts/install.py uninstall --harness grok --project=-project")
    def clear_the_claim_as_printed(self, a, project, given, value):
            """Strand one claim in `project`, then run the two commands doctor prints for it. Returns them as printed.

            `given` is how the caller names the project and `value` is the path inside it. Every run starts in the checkout,
            which is where the printed commands say to run them.
            """

            def call(*args):
                return in_process.invoke(in_process.Invocation(
                    program="scripts/install.py",
                    argv=(*args, *given, "--harness", "grok"),
                    env=_env(self.home),
                    cwd=a,
                ))

            def doctor(status):
                result = call("doctor")
                self.assertEqual((result.returncode, result.stderr), (status, ""), result.stdout)
                return result.stdout.splitlines()

            def outside():
                return [record for record in snapshot(a) if project not in (a / record[0], *(a / record[0]).parents)]

            project.mkdir()
            self.ok(call("install"), "linked 3 skills into grok")
            swarm = project / ".grok" / "skills" / "swarm"
            swarm.unlink()
            owner = project / ".pstack" / "install-owners" / owner_file(self.home, a).name
            self.assertEqual(set(json.loads(owner.read_text())["links"]), {str(swarm.parent / name) for name in NAMES})
            lines = doctor(1)
            self.assertEqual(len(lines), 2, lines)
            printed = re.fullmatch(
                re.escape(f"        claim {swarm}: nothing is there; ")
                + r'run "(.+)" to link it again, then "(.+)" removes the link and this claim',
                lines[1],
            )
            self.assertIsNotNone(printed, lines[1])
            install, uninstall = printed.groups()
            self.assertEqual(
                lines,
                [
                    f"grok    {swarm.parent}: 2/3 pstack-t3, 1 missing",
                    f'        claim {swarm}: nothing is there; run "{install}" to link it again, '
                    f'then "{uninstall}" removes the link and this claim',
                ],
            )
            self.assertEqual(shlex.split(install), ["python3", "scripts/install.py", "install", "--harness", "grok", f"--project={value}"])
            self.assertEqual(shlex.split(uninstall), ["python3", "scripts/install.py", "uninstall", "--harness", "grok", f"--project={value}"])
            checkout = outside()
            linked = self.follow(a, install)
            self.assertEqual((linked.returncode, linked.stderr), (0, ""), linked.stdout)
            self.assertEqual(linked.stdout.splitlines(), ["linked 1 skills into grok", f"manifest: {project / '.pstack' / 'install-manifest.json'}"])
            self.assertEqual(os.readlink(swarm), str(a / "skills" / "swarm"))
            self.assertEqual(doctor(0), [f"grok    {swarm.parent}: 3/3 pstack-t3"])
            removed = self.follow(a, uninstall)
            self.assertEqual((removed.returncode, removed.stderr), (0, ""), removed.stdout)
            self.assertEqual(removed.stdout.splitlines(), ["removed 3 links, restored 0 entries"])
            self.assertFalse(owner.exists())
            self.assertEqual(os.listdir(swarm.parent), [])
            self.assertEqual(outside(), checkout)
            self.assertEqual(sorted(os.listdir(self.home)), sorted({"checkouts", project.relative_to(self.home).parts[0]}))
            self.assertEqual(os.listdir(self.home / "checkouts"), ["a"])
            self.assertEqual(sorted(os.listdir(project)), [".grok", ".pstack"])
            return install, uninstall
    def test_doctor_names_a_claim_whose_link_sits_in_a_holder_beside_it(self):
            a, swarm, raced, aside = self.strand_link()
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [
                    self.harness_line("grok", "0/3 pstack-t3, 3 missing"),
                    f"        claim {swarm}: nothing is there, and {aside} holds this checkout's link for it; "
                    '"python3 scripts/install.py uninstall --dry-run" prints what the next run does with it',
                ],
            )
    def test_doctor_names_a_backup_row_with_nothing_at_its_backup_path(self):
            a, swarm = self.installed_for_grok()
            row = self.inert_row()
            self.assertEqual(self.doctor(a, 0, "--harness", "grok"), [self.harness_line("grok", "3/3 pstack-t3"), row])
    def test_doctor_names_a_backup_row_whose_entry_sits_in_a_holder_beside_it(self):
            a, swarm, backup, raced, aside = self.strand_backup(KILLED_ASIDE, reason=in_process.Reason.INTERPRETER_EXIT)
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [
                    self.harness_line("grok", "0/3 pstack-t3, 3 missing"),
                    f"        backup row {backup}: nothing is there, and {aside} holds an entry under that name; "
                    '"python3 scripts/install.py uninstall --dry-run" prints what the next run does with it',
                ],
            )
    def test_doctor_names_another_checkouts_owner_file_whose_checkout_directory_is_gone(self):
            a, swarm = self.installed_for_grok()
            away = self.away_owner()
            self.assertEqual(
                self.doctor(a, 1, "--harness", "codex,grok"),
                [self.harness_line("codex", "0/3 pstack-t3, 3 missing"), away, self.harness_line("grok", "3/3 pstack-t3")],
            )
