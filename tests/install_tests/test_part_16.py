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


class Part16Test(OwnershipCase):
    def assert_stops(self, checkout, text):
            for command in (("install",), ("uninstall",), ("uninstall", "--dry-run")):
                stopped = run(self.home, checkout, *command, "--harness", "grok")
                self.assertEqual(stopped.returncode, 1, stopped.stdout + stopped.stderr)
                self.assertEqual(stopped.stderr, text + "\n")
                self.assertEqual(stopped.stdout, "")
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
    def installed_for_grok(self):
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "grok"), "linked 3 skills into grok")
            return a, provider_link(self.home, "grok", "swarm")
    def relink_group(self, count, harnesses, paths):
            return [
                f'        {count} claims have nothing at their paths; run "{self.INSTALL.format(harnesses=harnesses)}" to link them again, '
                f'then "{self.UNINSTALL.format(harnesses=harnesses)}" removes the links and these claims:',
                *(f"          {path}" for path in paths),
            ]
    def test_doctor_prints_a_group_where_its_first_claim_is_and_a_claim_alone_in_its_advice_as_todays_sentence(self):
            a, swarm = self.installed_for_grok()
            alpha, runtime = (provider_link(self.home, "grok", name) for name in ("alpha", "pstack-runtime"))
            self.assertEqual(list(read_owner(self.home, a)["links"]), [str(alpha), str(runtime), str(swarm)])
            for link in (alpha, runtime, swarm):
                link.unlink()
            shutil.rmtree(a / "skills" / "pstack-runtime")
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [
                    self.harness_line("grok", "0/2 pstack-t3, 2 missing"),
                    *self.relink_group(2, "grok", (alpha, swarm)),
                    f"        claim {runtime}: nothing is there; install plans no link at that path, so no command clears this claim; "
                    f'to drop it, delete the "{runtime}" entry from {owner_file(self.home, a)}',
                ],
            )
    def test_doctor_prints_two_claims_under_one_harness_whose_commands_name_different_harnesses_as_two_sentences(self):
            a = make_checkout(self.home, "a")
            shared = provider_link(self.home, "codex", "swarm").parent
            own = provider_link(self.home, "cursor", "swarm").parent
            shared.mkdir(parents=True)
            own.parent.mkdir()
            os.symlink(shared, own)
            self.ok(run(self.home, a, "--harness", "codex,cursor"), "linked 3 skills into codex, cursor")
            own.unlink()
            own.mkdir()
            alpha, swarm = (shared / name for name in ("alpha", "swarm"))
            swarm.unlink()
            self.ok(run(self.home, a, "--harness", "codex"), "linked 1 skills into codex")
            links = read_owner(self.home, a)["links"]
            self.assertEqual((links[str(alpha)], links[str(swarm)]), ({"harnesses": ["codex", "cursor"]}, {"harnesses": ["codex"]}))
            alpha.unlink()
            swarm.unlink()

            def relink(path, harnesses):
                return (
                    f'        claim {path}: nothing is there; run "{self.INSTALL.format(harnesses=harnesses)}" to link it again, '
                    f'then "{self.UNINSTALL.format(harnesses=harnesses)}" removes the link and this claim'
                )

            self.assertEqual(
                self.doctor(a, 1, "--harness", "codex,cursor"),
                [
                    self.harness_line("codex", "1/3 pstack-t3, 2 missing"),
                    relink(alpha, "codex,cursor"),
                    relink(swarm, "codex"),
                    self.harness_line("cursor", "0/3 pstack-t3, 3 missing"),
                    relink(alpha, "codex,cursor"),
                ],
            )
    def test_the_two_commands_of_a_group_for_a_project_path_with_a_space_relink_every_listed_path_and_clear_every_claim(self):
            a = make_checkout(self.home, "a")
            project = self.home / "my project"
            project.mkdir()

            def call(*args):
                return in_process.invoke(in_process.Invocation(
                    program="scripts/install.py",
                    argv=(*args, "--project", str(project), "--harness", "grok"),
                    env=_env(self.home),
                    cwd=a,
                ))

            self.ok(call("install"), "linked 3 skills into grok")
            skills = project / ".grok" / "skills"
            owner = project / ".pstack" / "install-owners" / owner_file(self.home, a).name
            (skills / "alpha").unlink()
            (skills / "swarm").unlink()
            reported = call("doctor")
            self.assertEqual((reported.returncode, reported.stderr), (1, ""), reported.stdout)
            lines = reported.stdout.splitlines()
            self.assertEqual(len(lines), 4, lines)
            printed = re.fullmatch(
                r'        2 claims have nothing at their paths; run "(.+)" to link them again, '
                r'then "(.+)" removes the links and these claims:',
                lines[1],
            )
            self.assertIsNotNone(printed, lines[1])
            install, uninstall = printed.groups()
            self.assertEqual(shlex.split(install), ["python3", "scripts/install.py", "install", "--harness", "grok", f"--project={project}"])
            self.assertEqual(shlex.split(uninstall), ["python3", "scripts/install.py", "uninstall", "--harness", "grok", f"--project={project}"])
            self.assertEqual(
                lines,
                [f"grok    {skills}: 1/3 pstack-t3, 2 missing", lines[1], f"          {skills / 'alpha'}", f"          {skills / 'swarm'}"],
            )
            self.ok(self.follow(a, install), "linked 2 skills into grok")
            for line in lines[2:]:
                listed = Path(line.strip())
                self.assertEqual(os.readlink(listed), str(a / "skills" / listed.name))
            self.assertEqual(set(json.loads(owner.read_text())["links"]), {str(skills / name) for name in NAMES})
            self.ok(self.follow(a, uninstall), "removed 3 links, restored 0 entries")
            self.assertFalse(owner.exists())
            self.assertEqual(os.listdir(skills), [])
    def test_after_the_owner_file_doctor_names_is_deleted_doctor_prints_no_line_for_it_and_this_checkouts_uninstall_leaves_that_checkouts_links_and_manifest_rows(self):
            a, swarm = self.installed_for_grok()
            away = self.away_owner()
            gone = self.home / "checkouts" / "gone"
            codex = self.harness_line("codex", "0/3 pstack-t3, 3 missing")
            grok = self.harness_line("grok", "3/3 pstack-t3")
            self.assertEqual(self.doctor(a, 1, "--harness", "codex,grok"), [codex, away, grok])
            rows = [row for row in read_legacy(self.home)["links"] if row["checkout"] == str(gone)]
            self.assertEqual([row["path"] for row in rows], [str(provider_link(self.home, "codex", name)) for name in NAMES])
            owner_file(self.home, gone).unlink()
            self.assertEqual(self.doctor(a, 1, "--harness", "codex,grok"), [codex, grok])
            self.ok(run(self.home, a, "uninstall", "--harness", "codex,grok"), "removed 3 links, restored 0 entries")
            for name in NAMES:
                self.assertEqual(os.readlink(provider_link(self.home, "codex", name)), str(gone / "skills" / name))
            self.assertEqual(sorted(os.listdir(provider_link(self.home, "codex", "swarm").parent)), sorted(NAMES))
            self.assertEqual(read_legacy(self.home)["links"], rows)
    def test_doctor_prints_two_claims_that_name_no_harness_as_one_unindented_group_after_the_last_harness_and_before_an_unreadable_owner_file(self):
            a, swarm = self.installed_for_grok()
            owner = owner_file(self.home, a)
            alpha = provider_link(self.home, "grok", "alpha")
            data = read_owner(self.home, a)
            for link in (alpha, swarm):
                data["links"][str(link)] = {"harnesses": []}
                link.unlink()
                shutil.rmtree(a / "skills" / link.name)
            owner.write_text(json.dumps(data, indent=2) + "\n")
            other = owner_file(self.home, self.home / "checkouts" / "b")
            other.write_text("{not json\n")
            self.assertEqual(
                self.doctor(a, 0, "--harness", "grok"),
                [
                    self.harness_line("grok", "1/1 pstack-t3"),
                    "2 claims; install plans no link at the path of any of them, so no command clears them; "
                    f"to drop one, delete the entry named by its path from {owner}:",
                    f"  {alpha}: nothing is there",
                    f"  {swarm}: nothing is there",
                    f"{other} is not valid JSON; fix or move it and rerun (doctor read no checkout from it)",
                ],
            )
    def test_doctor_exits_1_on_a_fully_linked_home_whose_manifest_is_not_json(self):
            a, swarm = self.installed_for_grok()
            legacy_file(self.home).write_text("{not json\n")
            refusal = f"{legacy_file(self.home)} is not valid JSON; fix or move it and rerun"
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [self.harness_line("grok", "3/3 pstack-t3"), f"{refusal} (doctor checked no claims and no backup rows)"],
            )
            self.assert_stops(a, refusal)
    def test_doctor_exits_1_on_a_fully_linked_home_whose_owner_file_for_this_checkout_records_another_checkout(self):
            a, swarm = self.installed_for_grok()
            owner = owner_file(self.home, a)
            data = read_owner(self.home, a)
            data["checkout"] = "/elsewhere/checkout"
            owner.write_text(json.dumps(data, indent=2) + "\n")
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [
                    self.harness_line("grok", "3/3 pstack-t3"),
                    f"{owner} records /elsewhere/checkout, not this checkout (doctor checked no claims of this checkout)",
                ],
            )
    def test_doctor_exits_1_and_names_the_manifest_then_this_checkouts_owner_file_when_neither_is_json(self):
            a, swarm = self.installed_for_grok()
            owner = owner_file(self.home, a)
            legacy_file(self.home).write_text("{not json\n")
            owner.write_text("{not json\n")
            self.assertEqual(
                self.doctor(a, 1, "--harness", "grok"),
                [
                    self.harness_line("grok", "3/3 pstack-t3"),
                    f"{legacy_file(self.home)} is not valid JSON; fix or move it and rerun (doctor checked no claims and no backup rows)",
                    f"{owner} is not valid JSON; fix or move it and rerun (doctor checked no claims of this checkout)",
                ],
            )
    def assert_unread(self, checkout, file, reason, unchecked):
            """On a fully linked home where `file` cannot be read, assert what doctor and the commands `assert_stops` runs print.

            Doctor prints its harness line, then the could-not-be-read sentence with `unchecked` in parentheses, and exits 1.
            Each command exits 1 with that sentence alone on stderr.
            """
            sentence = f"{file} could not be read ({reason}); clear that error and rerun"
            self.assertEqual(
                self.doctor(checkout, 1, "--harness", "grok"),
                [self.harness_line("grok", "3/3 pstack-t3"), f"{sentence} ({unchecked})"],
            )
            self.assert_stops(checkout, sentence)
    @unittest.skipIf(os.geteuid() == 0, "root reads a file of mode 000")
    def test_a_manifest_of_mode_000_gives_doctor_the_could_not_be_read_line_at_exit_1_and_stops_install_and_uninstall_with_that_sentence(self):
            a, swarm = self.installed_for_grok()
            before = snapshot(self.home)
            legacy_file(self.home).chmod(0)
            self.addCleanup(legacy_file(self.home).chmod, 0o644)
            self.assert_unread(a, legacy_file(self.home), "Permission denied", self.NO_MANIFEST)
            legacy_file(self.home).chmod(0o644)
            self.assertEqual(snapshot(self.home), before)
    def test_a_directory_at_the_manifest_path_gives_doctor_the_could_not_be_read_line_at_exit_1_and_stops_install_and_uninstall_with_that_sentence(self):
            a, swarm = self.installed_for_grok()
            legacy_file(self.home).unlink()
            legacy_file(self.home).mkdir()
            before = snapshot(self.home)
            self.assert_unread(a, legacy_file(self.home), "Is a directory", self.NO_MANIFEST)
            self.assertEqual(snapshot(self.home), before)
    @unittest.skipIf(os.geteuid() == 0, "root reads a file of mode 000")
    def test_this_checkouts_owner_file_of_mode_000_gives_doctor_the_could_not_be_read_line_at_exit_1_and_stops_install_and_uninstall_with_that_sentence(self):
            a, swarm = self.installed_for_grok()
            owner = owner_file(self.home, a)
            before = snapshot(self.home)
            owner.chmod(0)
            self.addCleanup(owner.chmod, 0o644)
            self.assert_unread(a, owner, "Permission denied", self.NO_CLAIMS)
            owner.chmod(0o644)
            self.assertEqual(snapshot(self.home), before)
    def test_a_directory_at_this_checkouts_owner_file_path_gives_doctor_the_could_not_be_read_line_at_exit_1_and_stops_install_and_uninstall_with_that_sentence(self):
            a, swarm = self.installed_for_grok()
            owner = owner_file(self.home, a)
            owner.unlink()
            owner.mkdir()
            before = snapshot(self.home)
            self.assert_unread(a, owner, "Is a directory", self.NO_CLAIMS)
            self.assertEqual(snapshot(self.home), before)
    @unittest.skipIf(os.geteuid() == 0, "root reads inside a directory of mode 000")
    def test_an_install_owners_directory_of_mode_000_gives_doctor_the_could_not_be_read_line_at_exit_1_and_stops_install_and_uninstall_with_that_sentence(self):
            a, swarm = self.installed_for_grok()
            owner = owner_file(self.home, a)
            before = snapshot(self.home)
            owner.parent.chmod(0)
            self.addCleanup(owner.parent.chmod, 0o755)
            self.assert_unread(a, owner, "Permission denied", self.NO_CLAIMS)
            owner.parent.chmod(0o755)
            self.assertEqual(snapshot(self.home), before)
