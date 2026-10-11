from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from activate import Activation, ActivationError, BEGIN, END, content_hash

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "activate.py"


def inventory(root):
    if not root.exists():
        return {}
    result = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[name] = ("link", os.readlink(path))
        elif path.is_dir():
            result[name] = ("directory",)
        else:
            result[name] = ("file", path.read_bytes())
    return result


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkout = self.root / "checkout"
        self.home = self.root / "home"
        self.contents = {}
        for name in ("poteto-mode", "pstack-runtime", "swarm", "project", "mesh"):
            self.generated(name, f"---\nname: {name}\ndescription: Test fixture.\n---\n\n{name} v1\n".encode())
        self.activation = Activation(self.checkout, self.home)
        self.skills = self.home / ".codex" / "skills"
        self.instructions = self.home / ".codex" / "AGENTS.md"
        self.state = self.home / ".pstack-t3" / "activation" / "codex"

    def generated(self, name, data):
        path = self.checkout / "skills" / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "SKILL.md").write_bytes(data)
        self.contents[name] = data
        return path

    def original(self, name="poteto-mode", data=b"original local skill\n"):
        path = self.skills / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "SKILL.md").write_bytes(data)
        return path

    def cli(self, *arguments, expected=0, env=None):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments, "--home", str(self.home), "--checkout", str(self.checkout)],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def link(self, target, path, directory=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(target, path, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Platform cannot create fixture symlinks: {error}")

    def test_fresh_default_copies_complete_package_and_installs_pointer(self):
        result = self.activation.install()
        self.assertEqual(result["codex"]["state"], "installed")
        self.assertEqual(set(result), {"codex"})
        self.assertEqual(set(result["codex"]["skills"]), set(self.contents))
        for name, data in self.contents.items():
            self.assertEqual((self.skills / name / "SKILL.md").read_bytes(), data)
            self.assertFalse((self.skills / name).is_symlink())
        data = self.instructions.read_text(encoding="utf-8")
        pointer = (self.skills / "poteto-mode" / "SKILL.md").as_posix()
        self.assertIn(f"[Poteto SKILL](<{pointer}>)", data)
        self.assertEqual(data.count(BEGIN), 1)
        self.assertEqual(data.count(END), 1)
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".agents").exists())
        self.assertEqual(json.loads((self.state / "manifest.json").read_text())["home"], str(self.home))

    def test_preserves_existing_directory_file_and_unrelated_skills(self):
        old = self.original()
        (old / "private.txt").write_bytes(b"keep me")
        cache = old / "__pycache__"
        cache.mkdir()
        (cache / "local.pyc").write_bytes(b"original cache also preserved")
        self.skills.mkdir(parents=True, exist_ok=True)
        (self.skills / "swarm").write_bytes(b"original same-name file")
        self.original("unrelated", b"unrelated bytes")
        before = inventory(self.skills)
        self.activation.install()
        self.assertEqual((self.state / "backups" / "poteto-mode" / "private.txt").read_bytes(), b"keep me")
        self.assertEqual((self.state / "backups" / "swarm").read_bytes(), b"original same-name file")
        self.activation.uninstall()
        self.assertEqual(inventory(self.skills), before)
        self.assertFalse(self.instructions.exists())
        self.assertEqual(self.activation.status()["codex"]["state"], "not-installed")
        self.assertEqual(list((self.state / "backups").iterdir()), [])

    def test_repeated_install_is_identical_and_has_one_backup(self):
        self.original()
        self.activation.install()
        before = inventory(self.home)
        self.activation.install()
        self.assertEqual(inventory(self.home), before)
        self.assertEqual([p.name for p in (self.state / "backups").iterdir()], ["poteto-mode"])

    def test_source_update_keeps_first_original_for_uninstall(self):
        self.original(data=b"first original")
        self.activation.install()
        self.generated("poteto-mode", b"new generated source\n")
        self.activation.install()
        self.assertEqual((self.skills / "poteto-mode" / "SKILL.md").read_bytes(), b"new generated source\n")
        self.assertEqual((self.state / "backups" / "poteto-mode" / "SKILL.md").read_bytes(), b"first original")
        self.assertEqual(self.instructions.read_bytes().count(BEGIN.encode()), 1)
        self.activation.uninstall()
        self.assertEqual((self.skills / "poteto-mode" / "SKILL.md").read_bytes(), b"first original")

    def test_user_edit_blocks_install_and_uninstall_without_changing_any_entry(self):
        self.original()
        self.activation.install()
        edited = self.skills / "swarm" / "SKILL.md"
        edited.write_bytes(b"user edit")
        self.generated("poteto-mode", b"pending source update")
        before = inventory(self.home)
        for operation in (self.activation.install, self.activation.uninstall):
            with self.assertRaisesRegex(ActivationError, "Managed skill missing or edited"):
                operation()
            self.assertEqual(inventory(self.home), before)
        self.assertEqual(self.activation.status()["codex"]["state"], "gap")
        self.assertEqual(edited.read_bytes(), b"user edit")

    def test_added_managed_file_is_a_user_edit(self):
        self.activation.install()
        (self.skills / "mesh" / "notes.md").write_bytes(b"local notes")
        with self.assertRaisesRegex(ActivationError, "Managed skill missing or edited"):
            self.activation.uninstall()
        self.assertEqual((self.skills / "mesh" / "notes.md").read_bytes(), b"local notes")

    def test_unrelated_instruction_edits_survive_update_and_uninstall_byte_for_byte(self):
        self.instructions.parent.mkdir(parents=True)
        original = b"\xef\xbb\xbfMy original instructions.\r\nNo final newline"
        self.instructions.write_bytes(original)
        self.activation.install()
        installed = self.instructions.read_bytes()
        self.instructions.write_bytes(b"New leading text\n" + installed + b"\nAn appended rule.\r\n")
        self.generated("swarm", b"updated swarm")
        self.activation.install()
        self.activation.uninstall()
        self.assertEqual(self.instructions.read_bytes(), b"New leading text\n" + original + b"\nAn appended rule.\r\n")

    def test_empty_preexisting_instruction_file_survives(self):
        self.instructions.parent.mkdir(parents=True)
        self.instructions.write_bytes(b"")
        self.activation.install()
        self.activation.uninstall()
        self.assertTrue(self.instructions.is_file())
        self.assertEqual(self.instructions.read_bytes(), b"")

    def test_new_instruction_file_with_user_append_survives_uninstall(self):
        self.activation.install()
        with self.instructions.open("ab") as stream:
            stream.write(b"A user rule.\n")
        self.activation.uninstall()
        self.assertEqual(self.instructions.read_bytes(), b"A user rule.\n")

    def test_edited_or_deleted_managed_block_blocks_mutation(self):
        self.activation.install()
        self.instructions.write_bytes(self.instructions.read_bytes().replace(b"smallest effective", b"my chosen"))
        before = inventory(self.home)
        with self.assertRaisesRegex(ActivationError, "Managed instruction block missing or edited"):
            self.activation.uninstall()
        with self.assertRaises(ActivationError):
            self.activation.install()
        self.assertEqual(inventory(self.home), before)

    def test_unowned_markers_are_not_adopted(self):
        self.instructions.parent.mkdir(parents=True)
        self.instructions.write_text(BEGIN + "\nunknown user block\n" + END, encoding="utf-8")
        before = inventory(self.home)
        with self.assertRaisesRegex(ActivationError, "Unowned activation markers"):
            self.activation.install()
        self.assertEqual(inventory(self.home), before)

    def test_backup_edit_is_reported_and_preserved(self):
        self.original()
        self.activation.install()
        backup = self.state / "backups" / "poteto-mode" / "SKILL.md"
        backup.write_bytes(b"changed recovery data")
        before = inventory(self.home)
        with self.assertRaisesRegex(ActivationError, "Backup missing or edited"):
            self.activation.uninstall()
        self.assertEqual(inventory(self.home), before)

    def test_failed_fresh_install_rolls_back_originals_and_instructions(self):
        self.original()
        self.instructions.write_bytes(b"original instructions\n")
        original_skills = inventory(self.skills)
        replace = os.replace

        def fail_instruction(source, target):
            if Path(target) == self.instructions and Path(source).name.startswith(".pstack-t3-"):
                raise OSError("injected instruction replacement failure")
            return replace(source, target)

        with mock.patch("activate.os.replace", side_effect=fail_instruction):
            with self.assertRaisesRegex(OSError, "injected instruction"):
                self.activation.install()
        self.assertEqual(inventory(self.skills), original_skills)
        self.assertEqual(self.instructions.read_bytes(), b"original instructions\n")
        self.assertFalse((self.state / "manifest.json").exists())
        self.assertFalse((self.state / "pending").exists())
        self.assertFalse((self.state / "lock").exists())
        self.assertEqual(self.activation.status()["codex"]["state"], "not-installed")
        self.activation.install()
        self.assertEqual(self.activation.status()["codex"]["state"], "installed")

    def test_failed_update_rolls_back_to_old_owned_install(self):
        self.original()
        self.activation.install()
        before = inventory(self.home)
        self.generated("poteto-mode", b"update")
        replace = os.replace

        def fail_manifest(source, target):
            if Path(target) == self.state / "manifest.json" and Path(source).name.startswith(".pstack-t3-"):
                raise OSError("injected manifest replacement failure")
            return replace(source, target)

        with mock.patch("activate.os.replace", side_effect=fail_manifest):
            with self.assertRaisesRegex(OSError, "injected manifest"):
                self.activation.install()
        self.assertEqual(inventory(self.home), before)
        self.assertEqual(self.activation.status()["codex"]["state"], "installed")

    def test_failed_uninstall_restores_active_install_and_backups(self):
        self.original()
        self.activation.install()
        before = inventory(self.home)
        replace = os.replace

        def fail_instruction(source, target):
            if Path(source) == self.instructions:
                raise OSError("injected uninstall failure")
            return replace(source, target)

        with mock.patch("activate.os.replace", side_effect=fail_instruction):
            with self.assertRaisesRegex(OSError, "injected uninstall"):
                self.activation.uninstall()
        self.assertEqual(inventory(self.home), before)
        self.assertEqual(self.activation.status()["codex"]["state"], "installed")
        self.activation.uninstall()
        self.assertEqual((self.skills / "poteto-mode" / "SKILL.md").read_bytes(), b"original local skill\n")

    def test_rollback_refuses_edit_and_retains_recovery_data(self):
        self.original()
        apply = self.activation._apply_change

        def edit_then_fail(pending, changes, target, *args, **kwargs):
            apply(pending, changes, target, *args, **kwargs)
            if Path(target) == self.skills / "poteto-mode":
                (Path(target) / "SKILL.md").write_bytes(b"concurrent user edit")
                raise OSError("injected failure after external edit")

        with mock.patch.object(self.activation, "_apply_change", side_effect=edit_then_fail):
            with self.assertRaisesRegex(ActivationError, "Rollback ownership gap"):
                self.activation.install()
        self.assertEqual((self.skills / "poteto-mode" / "SKILL.md").read_bytes(), b"concurrent user edit")
        self.assertTrue((self.state / "pending" / "journal.json").is_file())
        self.assertEqual((self.state / "backups" / "poteto-mode" / "SKILL.md").read_bytes(), b"original local skill\n")
        self.assertEqual(self.activation.status()["codex"]["state"], "gap")

    def test_hard_interruption_is_a_concrete_gap_and_rerun_refuses(self):
        self.original()
        code = (
            "import os,sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(SCRIPT.parent)!r}); from activate import Activation; "
            f"a=Activation(Path({str(self.checkout)!r}),Path({str(self.home)!r})); "
            "apply=a._apply_change\n"
            "def crash(*args, **kwargs):\n"
            " apply(*args, **kwargs)\n"
            " os._exit(17)\n"
            "a._apply_change=crash\n"
            "a.install()\n"
        )
        process = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(process.returncode, 17, process.stderr)
        result = json.loads(self.cli("status", expected=1).stdout)["codex"]
        self.assertEqual(result["state"], "gap")
        self.assertTrue(any(str(self.state / "pending") in gap for gap in result["gaps"]))
        self.assertTrue((self.state / "pending" / "journal.json").is_file())
        before = inventory(self.home)
        self.cli("install", expected=1)
        self.assertEqual(inventory(self.home), before)

    def test_dry_run_api_and_cli_never_create_home(self):
        self.assertEqual(self.activation.install(dry_run=True)["codex"]["state"], "dry-run")
        self.cli("install", "--dry-run")
        self.assertFalse(self.home.exists())
        self.assertEqual(self.activation.status()["codex"]["state"], "not-installed")

    def test_cli_install_status_uninstall_and_selection(self):
        selected = json.loads(self.cli("install", "--skills", "swarm").stdout)["codex"]
        self.assertEqual(selected["skills"], ["poteto-mode", "project", "pstack-runtime", "swarm"])
        self.assertFalse((self.skills / "mesh").exists())
        self.assertEqual(json.loads(self.cli("status").stdout)["codex"]["state"], "installed")
        before = inventory(self.home)
        self.cli("uninstall", "--dry-run")
        self.assertEqual(inventory(self.home), before)
        self.assertEqual(json.loads(self.cli("uninstall").stdout)["codex"]["state"], "not-installed")
        self.cli("uninstall")

    def test_cli_conflict_exit_and_unknown_harness_do_not_write(self):
        self.cli("install")
        (self.skills / "mesh" / "SKILL.md").write_bytes(b"edited through user tool")
        before = inventory(self.home)
        self.assertIn("Managed skill missing or edited", self.cli("uninstall", expected=1).stderr)
        self.cli("install", "--harness", "grok", expected=2)
        self.assertEqual(inventory(self.home), before)

    def test_selected_install_requires_generated_project_before_writes(self):
        (self.checkout / "skills" / "project").rename(self.root / "missing-project")
        self.assertIn("Missing generated skill", self.cli("install", "--skills", "swarm", expected=1).stderr)
        self.assertFalse(self.home.exists())

    def test_older_selected_install_can_update_or_uninstall_without_project_record(self):
        self.original()
        for operation in ("uninstall", "install"):
            with self.subTest(operation=operation):
                self.activation.install(["swarm"])
                # Recreate the version-1 manifest and targets of an earlier selected install.
                (self.skills / "project" / "SKILL.md").unlink()
                (self.skills / "project").rmdir()
                path = self.state / "manifest.json"
                manifest = json.loads(path.read_text(encoding="utf-8"))
                del manifest["skills"]["project"]
                path.write_text(json.dumps(manifest), encoding="utf-8")
                self.assertEqual(json.loads(self.cli("status").stdout)["codex"]["state"], "installed")
                self.cli(operation, *(('--skills', 'swarm') if operation == "install" else ()))
                if operation == "install":
                    self.assertEqual((self.skills / "project" / "SKILL.md").read_bytes(), self.contents["project"])
                    self.cli("uninstall")
                self.assertEqual((self.skills / "poteto-mode" / "SKILL.md").read_bytes(), b"original local skill\n")

    def test_environment_provider_overrides_are_not_inferred(self):
        outside = self.root / "outside-config"
        env = dict(os.environ, CODEX_HOME=str(outside), CLAUDE_CONFIG_DIR=str(outside))
        self.cli("install", env=env)
        self.assertTrue(self.instructions.is_file())
        self.assertFalse(outside.exists())

    def test_independent_multiple_harness_ownership(self):
        claude = self.home / ".claude"
        claude.mkdir(parents=True)
        (claude / "CLAUDE.md").write_bytes(b"Claude's original\n")
        both = Activation(self.checkout, self.home, ("codex", "claude"))
        result = both.install()
        self.assertEqual({h: v["state"] for h, v in result.items()}, {"codex": "installed", "claude": "installed"})
        claude_before = inventory(claude)
        claude_manifest = (self.home / ".pstack-t3" / "activation" / "claude" / "manifest.json").read_bytes()
        self.activation.uninstall()
        self.assertEqual(inventory(claude), claude_before)
        self.assertEqual((self.home / ".pstack-t3" / "activation" / "claude" / "manifest.json").read_bytes(), claude_manifest)
        Activation(self.checkout, self.home, ("claude",)).uninstall()
        self.assertEqual((claude / "CLAUDE.md").read_bytes(), b"Claude's original\n")

    def test_multi_harness_conflict_preflights_before_any_mutation(self):
        both = Activation(self.checkout, self.home, ("codex", "claude"))
        both.install()
        (self.home / ".claude" / "skills" / "swarm" / "SKILL.md").write_bytes(b"user edit")
        before = inventory(self.home)
        with self.assertRaises(ActivationError):
            both.uninstall()
        self.assertEqual(inventory(self.home), before)

    def test_cli_multiple_harnesses(self):
        result = json.loads(self.cli("install", "--harness", "codex", "--harness", "claude").stdout)
        self.assertEqual(set(result), {"codex", "claude"})
        self.cli("uninstall", "--harness", "codex", "--harness", "claude")
        self.assertFalse((self.home / ".claude" / "CLAUDE.md").exists())

    def test_generated_and_installed_cache_is_ignored_and_not_copied(self):
        cache = self.checkout / "skills" / "swarm" / "__pycache__"
        cache.mkdir()
        (cache / "generated.pyc").write_bytes(b"source bytecode")
        before_hash = content_hash(cache.parent)
        (cache / "generated.pyc").write_bytes(b"different source bytecode")
        self.assertEqual(content_hash(cache.parent), before_hash)
        self.activation.install()
        self.assertFalse((self.skills / "swarm" / "__pycache__").exists())
        local_cache = self.skills / "swarm" / "__pycache__"
        local_cache.mkdir()
        (local_cache / "ran.pyc").write_bytes(b"installed runtime bytecode")
        self.assertEqual(self.activation.status()["codex"]["state"], "installed")
        self.activation.uninstall()

    def test_user_edit_during_staging_is_not_overwritten(self):
        self.activation.install()
        self.generated("swarm", b"new upstream swarm")
        copytree = __import__("shutil").copytree
        target = self.skills / "swarm" / "SKILL.md"

        def edit_during_copy(source, destination, *args, **kwargs):
            result = copytree(source, destination, *args, **kwargs)
            if Path(source) == self.checkout / "skills" / "swarm":
                target.write_bytes(b"user edit during copy")
            return result

        with mock.patch("activate.shutil.copytree", side_effect=edit_during_copy):
            with self.assertRaisesRegex(ActivationError, "Rollback ownership gap"):
                self.activation.install()
        self.assertEqual(target.read_bytes(), b"user edit during copy")
        self.assertTrue((self.state / "pending" / "journal.json").exists())
        self.assertEqual(self.activation.status()["codex"]["state"], "gap")

    def test_instruction_edit_after_read_is_preserved_during_install_and_uninstall(self):
        self.instructions.parent.mkdir(parents=True)
        self.instructions.write_bytes(b"Original user rule\n")
        for operation in ("install", "uninstall"):
            with self.subTest(operation=operation):
                if operation == "uninstall":
                    self.activation.install()
                read_bytes = Path.read_bytes
                edited = None

                def edit_after_read(path):
                    nonlocal edited
                    data = read_bytes(path)
                    if path == self.instructions and (self.state / "pending").exists() and edited is None:
                        edited = data + b"Concurrent user rule\n"
                        path.write_bytes(edited)
                    return data

                with mock.patch.object(Path, "read_bytes", edit_after_read):
                    with self.assertRaises(ActivationError):
                        getattr(self.activation, operation)()
                self.assertIsNotNone(edited)
                self.assertEqual(self.instructions.read_bytes(), edited)
                self.assertEqual(self.activation.status()["codex"]["state"], "not-installed" if operation == "install" else "installed")
                self.assertFalse((self.state / "pending").exists())

    def test_extra_marker_during_update_refuses_commit_and_preserves_edit(self):
        self.activation.install()
        old_skills = inventory(self.skills)
        old_manifest = (self.state / "manifest.json").read_bytes()
        self.generated("swarm", b"Updated source\n")
        copy_entry = __import__("activate").copy_entry
        edited = self.instructions.read_bytes() + BEGIN.encode() + b"\nUser marker\n"

        def insert_marker(source, target):
            copy_entry(source, target)
            if Path(source) == self.checkout / "skills" / "swarm":
                self.instructions.write_bytes(edited)

        with mock.patch("activate.copy_entry", side_effect=insert_marker):
            with self.assertRaisesRegex(ActivationError, "instruction"):
                self.activation.install()
        self.assertEqual(self.instructions.read_bytes(), edited)
        self.assertEqual(inventory(self.skills), old_skills)
        self.assertEqual((self.state / "manifest.json").read_bytes(), old_manifest)
        self.assertFalse((self.state / "pending").exists())

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_instruction_file_permissions_survive_activation(self):
        self.instructions.parent.mkdir(parents=True)
        self.instructions.write_bytes(b"Private instructions\n")
        self.instructions.chmod(0o600)
        self.activation.install()
        self.assertEqual(self.instructions.stat().st_mode & 0o777, 0o600)
        self.activation.uninstall()
        self.assertEqual(self.instructions.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.instructions.read_bytes(), b"Private instructions\n")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX special entry fixture")
    def test_special_state_file_is_refused_without_blocking(self):
        self.state.mkdir(parents=True)
        os.mkfifo(self.state / "manifest.json")
        with self.assertRaisesRegex(ActivationError, "Expected a state file"):
            self.activation.install()
        self.assertEqual(self.activation.status()["codex"]["state"], "gap")
        self.assertFalse(self.skills.exists())

    def test_symlinked_provider_directory_refused_without_outside_writes(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "AGENTS.md").write_bytes(b"outside original")
        self.link(outside, self.home / ".codex", directory=True)
        before = inventory(outside)
        with self.assertRaisesRegex(ActivationError, "Linked path component"):
            self.activation.install()
        self.assertEqual(inventory(outside), before)

    def test_generated_nested_symlink_refused(self):
        outside = self.root / "outside.md"
        outside.write_bytes(b"outside source")
        self.link(outside, self.checkout / "skills" / "poteto-mode" / "reference.md")
        with self.assertRaisesRegex(ActivationError, "Symlink refused"):
            self.activation.install()
        self.assertFalse(self.home.exists())
        self.assertEqual(outside.read_bytes(), b"outside source")

    def test_same_name_link_is_preserved_without_creating_any_new_links(self):
        outside = self.root / "external-skill"
        outside.mkdir()
        (outside / "SKILL.md").write_bytes(b"external untouched")
        self.link(outside, self.skills / "poteto-mode", directory=True)
        before = inventory(outside)
        original_target = os.readlink(self.skills / "poteto-mode")
        with mock.patch("activate.os.symlink", side_effect=AssertionError("New symlink requires privilege")):
            self.activation.install()
            self.assertTrue((self.state / "backups" / "poteto-mode").is_symlink())
            self.assertFalse((self.skills / "poteto-mode").is_symlink())
            self.activation.uninstall()
        self.assertTrue((self.skills / "poteto-mode").is_symlink())
        self.assertEqual(os.readlink(self.skills / "poteto-mode"), original_target)
        self.assertEqual(inventory(outside), before)

    def test_broken_existing_link_is_preserved(self):
        missing = self.root / "missing-external"
        self.link(missing, self.skills / "swarm")
        self.activation.install()
        self.activation.uninstall()
        self.assertTrue((self.skills / "swarm").is_symlink())
        self.assertEqual(os.readlink(self.skills / "swarm"), str(missing))
        self.assertFalse(missing.exists())

    def test_linked_instruction_file_refused(self):
        outside = self.root / "outside.md"
        outside.write_bytes(b"outside instructions")
        self.link(outside, self.instructions)
        with self.assertRaisesRegex(ActivationError, "Linked path component"):
            self.activation.install()
        self.assertEqual(outside.read_bytes(), b"outside instructions")

    @unittest.skipUnless(os.name == "nt", "Windows junction fixture")
    def test_windows_junction_provider_directory_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.home.mkdir()
        junction = self.home / ".codex"
        creation = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True, text=True)
        self.assertEqual(creation.returncode, 0, creation.stdout + creation.stderr)
        self.addCleanup(lambda: junction.rmdir() if junction.exists() else None)
        with self.assertRaisesRegex(ActivationError, "Linked path component"):
            self.activation.install()
        self.assertEqual(inventory(outside), {})

    def test_root_and_checkout_overlap_and_unsafe_names_refused(self):
        for home in (Path(self.root.anchor), self.checkout, self.checkout / "fake-home"):
            with self.assertRaises(ActivationError):
                Activation(self.checkout, home)
        for name in ("../escape", "x/y", "CON", "nul", "swarm.", ""):
            with self.assertRaisesRegex(ActivationError, "Unsafe skill name"):
                self.activation.install([name])
        self.assertFalse(self.home.exists())

    def test_cli_checkout_under_home_preserves_source_and_restores_originals(self):
        checkout = self.home / "projects" / "pstack checkout"
        checkout.parent.mkdir(parents=True)
        self.checkout.rename(checkout)
        self.checkout = checkout
        self.original()
        original_skills = inventory(self.skills)
        original_instructions = b"Original instructions\r\nNo final newline"
        self.instructions.write_bytes(original_instructions)
        source = inventory(self.checkout)
        source_hash = content_hash(self.checkout)
        self.assertEqual(json.loads(self.cli("install").stdout)["codex"]["state"], "installed")
        self.assertEqual(inventory(self.checkout), source)
        self.assertEqual(content_hash(self.checkout), source_hash)
        for name, data in self.contents.items():
            self.assertEqual((self.skills / name / "SKILL.md").read_bytes(), data)
        installed = inventory(self.home)
        self.cli("install")
        self.assertEqual(inventory(self.home), installed)
        self.assertEqual(json.loads(self.cli("uninstall").stdout)["codex"]["state"], "not-installed")
        self.assertEqual(inventory(self.skills), original_skills)
        self.assertEqual(self.instructions.read_bytes(), original_instructions)
        self.assertEqual(inventory(self.checkout), source)
        self.assertEqual(content_hash(self.checkout), source_hash)

    def test_cli_checkout_in_mutable_roots_refused_without_writes(self):
        for relative in (".codex/skills/checkout", ".pstack-t3/activation/checkout"):
            with self.subTest(relative=relative):
                self.checkout = self.home / relative
                for name in ("poteto-mode", "project", "pstack-runtime"):
                    self.generated(name, b"Read-only source\n")
                before = inventory(self.home)
                self.assertIn("overlap", self.cli("install", expected=1).stderr)
                self.assertEqual(inventory(self.home), before)

    def test_checkout_overlap_with_selected_targets_and_state_refused(self):
        for relative in (
            ".codex", ".codex/skills", ".codex/AGENTS.md",
            ".pstack-t3", ".pstack-t3/activation", ".pstack-t3/activation/codex",
            ".claude", ".claude/skills/checkout", ".claude/CLAUDE.md",
        ):
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(ActivationError, "overlap"):
                    Activation(self.home / relative, self.home, ("codex", "claude"))
        self.assertFalse(self.home.exists())

    def test_another_checkout_cannot_take_ownership(self):
        self.activation.install()
        other = self.root / "other-checkout"
        shutil_source = self.checkout / "skills" / "poteto-mode"
        for name in ("poteto-mode", "project", "pstack-runtime"):
            path = other / "skills" / name
            path.mkdir(parents=True)
            (path / "SKILL.md").write_bytes((shutil_source / "SKILL.md").read_bytes())
        before = inventory(self.home)
        with self.assertRaisesRegex(ActivationError, "belongs to another checkout"):
            Activation(other, self.home).install()
        self.assertEqual(inventory(self.home), before)

    def test_unowned_backup_or_malformed_manifest_is_not_overwritten(self):
        backup = self.state / "backups" / "swarm"
        backup.mkdir(parents=True)
        (backup / "SKILL.md").write_bytes(b"unknown backup")
        before = inventory(self.home)
        with self.assertRaisesRegex(ActivationError, "Backups without"):
            self.activation.install()
        self.assertEqual(inventory(self.home), before)
        (self.state / "manifest.json").write_text('{"version": 1}', encoding="utf-8")
        self.assertEqual(self.activation.status()["codex"]["state"], "gap")
        before = inventory(self.home)
        with self.assertRaisesRegex(ActivationError, "Invalid manifest"):
            self.activation.uninstall()
        self.assertEqual(inventory(self.home), before)


if __name__ == "__main__":
    unittest.main()
