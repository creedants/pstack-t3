"""Fresh install behavior."""

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

from .support import setUpModule
from .support import NAMES
from .support import ROOT
from .support import _env
from .support import _run
from .support import run_install

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
