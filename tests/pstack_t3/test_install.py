import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "t3/scripts"))
sys.path.append(str(ROOT / "scripts"))

import os
import shutil
import subprocess
import tempfile
from tests.pstack_t3.support import fence_git_environment, config


def setUpModule():
    fence_git_environment()

class InstallTest(unittest.TestCase):
    def run_install(self, home, *args):
        env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
        env.pop("CLAUDE_CONFIG_DIR", None)
        return subprocess.run([sys.executable, str(ROOT / "scripts/install.py"), *args], env=env, capture_output=True, text=True)

    def setUp(self):
        if not (ROOT / "skills/pstack-runtime/SKILL.md").exists():
            subprocess.run([sys.executable, str(ROOT / "scripts/build.py")], check=True)

    def test_install_refuses_conflicts_then_replace_and_uninstall_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            existing = home / ".claude/skills/swarm"
            existing.mkdir(parents=True)
            (existing / "SKILL.md").write_text("mine")
            refused = self.run_install(home)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("swarm", refused.stderr)
            self.assertFalse((home / ".grok/skills/poteto-mode").exists())
            replaced = self.run_install(home, "--replace")
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
            for harness in (".claude", ".agents", ".grok", ".cursor"):
                self.assertTrue((home / harness / "skills/poteto-mode/SKILL.md").exists(), harness)
            self.assertEqual(self.run_install(home, "doctor").returncode, 0)
            again = self.run_install(home)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("already installed", again.stdout)
            self.assertEqual(self.run_install(home, "uninstall").returncode, 0)
            self.assertEqual((existing / "SKILL.md").read_text(), "mine")
            self.assertFalse((home / ".grok/skills/poteto-mode").exists())

    def test_uninstall_dry_run_changes_nothing_and_harness_filter_is_respected(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.assertEqual(self.run_install(home, "--harness", "codex,grok").returncode, 0)
            dry = self.run_install(home, "uninstall", "--dry-run")
            self.assertIn("would remove", dry.stdout)
            self.assertTrue((home / ".agents/skills/swarm").is_symlink())
            self.assertEqual(self.run_install(home, "uninstall", "--harness", "codex").returncode, 0)
            self.assertFalse((home / ".agents/skills/swarm").exists())
            self.assertTrue((home / ".grok/skills/swarm").is_symlink())
            self.assertEqual(self.run_install(home, "uninstall").returncode, 0)
            self.assertFalse((home / ".grok/skills/swarm").exists())

    def test_blocked_restore_keeps_its_backup_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            mine = home / ".grok/skills/swarm"
            mine.mkdir(parents=True)
            (mine / "SKILL.md").write_text("mine")
            self.assertEqual(self.run_install(home, "--harness", "grok", "--replace").returncode, 0)
            (mine).unlink()
            mine.mkdir()
            (mine / "SKILL.md").write_text("squatter")
            self.run_install(home, "uninstall")
            self.assertEqual((mine / "SKILL.md").read_text(), "squatter")
            shutil.rmtree(mine)
            self.run_install(home, "uninstall")
            self.assertEqual((mine / "SKILL.md").read_text(), "mine")

    def test_two_replacements_in_one_second_keep_both_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = home / ".grok/skills/swarm"
            target.parent.mkdir(parents=True)
            for text in ("first", "second"):
                if target.is_symlink():
                    target.unlink()
                target.write_text(text)
                self.assertEqual(self.run_install(home, "--harness", "grok", "--replace").returncode, 0)
            backups = sorted(p.read_text() for p in (home / ".config/pstack-t3/backups").rglob("swarm"))
            self.assertEqual(backups, ["first", "second"])

    def test_replace_never_moves_the_checkout_when_a_skills_dir_points_into_it(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".grok").mkdir()
            (home / ".grok/skills").symlink_to(ROOT / "skills")
            result = self.run_install(home, "--harness", "grok", "--replace")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((ROOT / "skills/swarm/SKILL.md").is_file())
            self.assertFalse((ROOT / "skills/swarm").is_symlink())
            self.assertEqual(self.run_install(home, "uninstall").returncode, 0)
            self.assertTrue((ROOT / "skills/swarm/SKILL.md").is_file())

    def test_shared_directory_uninstalls_only_when_every_sharing_harness_is_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".agents/skills").mkdir(parents=True)
            (home / ".grok").mkdir()
            (home / ".grok/skills").symlink_to(home / ".agents/skills")
            self.assertEqual(self.run_install(home, "--harness", "codex,grok").returncode, 0)
            partial = self.run_install(home, "uninstall", "--harness", "grok")
            self.assertIn("shared with codex", partial.stdout)
            self.assertTrue((home / ".agents/skills/swarm").is_symlink())
            self.assertEqual(self.run_install(home, "uninstall", "--harness", "codex,grok").returncode, 0)
            self.assertFalse((home / ".agents/skills/swarm").exists())

    def test_doctor_fails_when_a_skills_dir_points_inside_one_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".grok").mkdir()
            (home / ".grok/skills").symlink_to(ROOT / "skills/swarm")
            self.assertNotEqual(self.run_install(home, "doctor", "--harness", "grok").returncode, 0)

    def test_doctor_flags_stale_copies_in_directories_a_provider_also_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            self.assertEqual(self.run_install(home, "--harness", "codex").returncode, 0)
            (home / ".codex/skills/swarm").mkdir(parents=True)
            (home / ".codex/skills/swarm/SKILL.md").write_text("old")
            result = self.run_install(home, "doctor", "--harness", "codex")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(".codex/skills also holds other copies", result.stdout)

    def test_shared_real_directory_is_linked_once(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            shared = home / "shared"
            shared.mkdir()
            (home / ".grok").mkdir()
            (home / ".cursor").mkdir()
            (home / ".grok/skills").symlink_to(shared)
            (home / ".cursor/skills").symlink_to(shared)
            result = self.run_install(home, "--harness", "grok,cursor")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((shared / "swarm/SKILL.md").exists())
