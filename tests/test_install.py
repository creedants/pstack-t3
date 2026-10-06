"""Installer behavior a fresh home can observe."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_install(home, *args):
    env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
    env.pop("CLAUDE_CONFIG_DIR", None)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/install.py"), *args],
        env=env,
        capture_output=True,
        text=True,
    )


class FreshInstallTest(unittest.TestCase):
    def test_fresh_install_links_every_skill_and_rerun_adds_no_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            names = sorted(p.name for p in (ROOT / "skills").iterdir() if (p / "SKILL.md").is_file())
            first = run_install(home)
            self.assertEqual(first.returncode, 0, first.stderr)
            for harness in (".claude", ".agents", ".grok", ".cursor"):
                for name in names:
                    link = home / harness / "skills" / name
                    self.assertTrue(link.is_symlink(), link)
                    self.assertEqual(link.resolve(), (ROOT / "skills" / name).resolve(), link)
                    self.assertEqual((link / "SKILL.md").read_bytes(), (ROOT / "skills" / name / "SKILL.md").read_bytes())
            backups = home / ".config/pstack-t3/backups"
            self.assertFalse(backups.exists(), "a fresh install created a backup directory")
            again = run_install(home)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("already installed", again.stdout)
            self.assertFalse(backups.exists(), "rerunning install created a backup directory")

    def test_replace_saves_the_old_skill_and_rerun_adds_no_empty_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            swarm = home / ".grok/skills/swarm"
            swarm.mkdir(parents=True)
            (swarm / "SKILL.md").write_text("foreign-skill\n")
            refused = run_install(home, "--harness", "grok")
            self.assertNotEqual(refused.returncode, 0)
            self.assertFalse((home / ".grok/skills/poteto-mode").exists())
            replaced = run_install(home, "--harness", "grok", "--replace")
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
            saved = list((home / ".config/pstack-t3/backups").rglob("SKILL.md"))
            self.assertEqual([path.read_text() for path in saved], ["foreign-skill\n"])
            stamps = sorted(p.name for p in (home / ".config/pstack-t3/backups").iterdir() if p.is_dir())
            self.assertEqual(len(stamps), 1)
            again = run_install(home, "--harness", "grok")
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn("already installed", again.stdout)
            stamps_after = sorted(p.name for p in (home / ".config/pstack-t3/backups").iterdir() if p.is_dir())
            self.assertEqual(stamps_after, stamps)
            removed = run_install(home, "--harness", "grok", "uninstall")
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertEqual((swarm / "SKILL.md").read_text(), "foreign-skill\n")
            self.assertFalse((home / ".grok/skills/poteto-mode").exists())
