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


class Part01Test(OwnershipCase):
    def test_old_installer_matches_397d163(self):
            self.assertEqual(hashlib.sha256(OLD_INSTALLER.encode()).hexdigest(), OLD_SHA)
    def test_row_1_replaced_owner_stays_tracked(self):
            a, b = self.base()
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
            self.assert_claims(a, grok_paths(self.home))
            self.assert_grok_text(a)
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
            self.assert_gone()
            self.assert_empty_records()
    def test_row_1b_owner_withdraws_links_another_checkout_moved_aside(self):
            a, b = self.base()
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall"),
                "removed 3 links, restored 0 entries",
                "3 of them had been moved aside by another checkout's --replace",
            )
            self.assert_grok_text(b)
            self.assert_claims(b, grok_paths(self.home))
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
            self.assert_gone()
            self.assert_empty_records()
    def repoint_swarm(self, target):
            link = provider_link(self.home, "grok", "swarm")
            link.unlink()
            os.symlink(target, link)
            return str(target)
    def assert_repoint_survives(self, a, text):
            link = provider_link(self.home, "grok", "swarm")
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall"),
                "removed 2 links, restored 0 entries",
                KEPT.format(n=1),
            )
            self.assertEqual(os.readlink(link), text)
            self.assert_claims(a, [str(link)])
            before = snapshot(self.home)
            self.ok(
                run(self.home, a, "--harness", "grok", "uninstall"),
                "removed 0 links, restored 0 entries",
                KEPT.format(n=1),
            )
            self.assertEqual(snapshot(self.home), before)
            self.assertEqual(os.readlink(link), text)
    def test_row_2_repointed_link_to_an_existing_directory_is_left_alone(self):
            a, b = self.base()
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
            target = self.home / "unrelated-dir"
            target.mkdir()
            text = self.repoint_swarm(target)
            self.assert_repoint_survives(a, text)
            link = provider_link(self.home, "grok", "swarm")
            link.unlink()
            os.symlink(a / "skills" / "swarm", link)
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 1 links, restored 0 entries")
            self.assertFalse(os.path.lexists(link))
            self.assert_empty_records()
    def test_row_3_repointed_dangling_link_is_left_alone(self):
            a, b = self.base()
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
            text = self.repoint_swarm(self.home / "missing-target")
            self.assert_repoint_survives(a, text)
    def foreign_then_both_uninstall(self, target):
            a, b = self.two()
            link = provider_link(self.home, "grok", "swarm")
            link.parent.mkdir(parents=True)
            os.symlink(target, link)
            text = str(target)
            self.ok(run(self.home, a, "--harness", "grok", "--replace"))
            self.ok(run(self.home, b, "--harness", "grok", "--replace"))
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
            self.assertEqual(os.readlink(link), text)
            self.assertEqual((link / "SKILL.md").read_bytes(), (target / "SKILL.md").read_bytes() if (target / "SKILL.md").exists() else (link / "SKILL.md").read_bytes())
            for name in ("alpha", "pstack-runtime"):
                self.assertFalse(os.path.lexists(provider_link(self.home, "grok", name)))
            self.assert_empty_records()
            before = snapshot(self.home)
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
            self.assertEqual(snapshot(self.home), before)
    def test_row_4_foreign_symlink_comes_back_untracked(self):
            target = self.home / "foreign-target"
            target.mkdir()
            (target / "SKILL.md").write_text("foreign-skill\n")
            self.foreign_then_both_uninstall(target)
            self.assertEqual((provider_link(self.home, "grok", "swarm") / "SKILL.md").read_text(), "foreign-skill\n")
    def test_row_5_repointed_lookalike_link_is_left_alone(self):
            a, b = self.base()
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
            lookalike = self.home / "lookalike"
            (lookalike / "scripts").mkdir(parents=True)
            (lookalike / "scripts" / "install.py").write_text("print('unrelated installer')\n")
            (lookalike / "skills" / "swarm").mkdir(parents=True)
            (lookalike / "skills" / "swarm" / "SKILL.md").write_text("lookalike\n")
            text = self.repoint_swarm(lookalike / "skills" / "swarm")
            self.assert_repoint_survives(a, text)
            self.assertEqual((lookalike / "scripts" / "install.py").read_text(), "print('unrelated installer')\n")
            self.assertEqual((lookalike / "skills" / "swarm" / "SKILL.md").read_text(), "lookalike\n")
    def test_row_6_lookalike_symlink_comes_back_untracked(self):
            lookalike = self.home / "lookalike"
            (lookalike / "scripts").mkdir(parents=True)
            (lookalike / "scripts" / "install.py").write_text("print('unrelated installer')\n")
            (lookalike / "skills" / "swarm").mkdir(parents=True)
            (lookalike / "skills" / "swarm" / "SKILL.md").write_text("lookalike\n")
            self.foreign_then_both_uninstall(lookalike / "skills" / "swarm")
            self.assertEqual((lookalike / "scripts" / "install.py").read_text(), "print('unrelated installer')\n")
            self.assertEqual((provider_link(self.home, "grok", "swarm") / "SKILL.md").read_text(), "lookalike\n")
    def alias_of(self, checkout):
            alias = self.home / "a-alias"
            os.symlink(checkout, alias)
            return alias
    def test_row_7_alias_spelled_link_is_left_alone_by_uninstall(self):
            a, b = self.two()
            alias = self.alias_of(a)
            self.ok(run(self.home, a, "--harness", "grok"))
            link = provider_link(self.home, "grok", "swarm")
            link.unlink()
            text = str(alias / "skills" / "swarm")
            os.symlink(text, link)
            self.ok(run(self.home, b, "--harness", "grok", "--replace"))
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
            self.ok(
                run(self.home, alias, "--harness", "grok", "uninstall"),
                "removed 2 links, restored 0 entries",
                KEPT.format(n=1),
            )
            self.assertEqual(os.readlink(link), text)
            self.assertEqual((link / "SKILL.md").read_bytes(), (a / "skills" / "swarm" / "SKILL.md").read_bytes())
            self.assert_claims(a, [str(link)])
            before = snapshot(self.home)
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
            self.assertEqual(snapshot(self.home), before)
            self.assertEqual(os.readlink(link), text)
    def test_row_7u_legacy_rows_do_not_adopt_an_alias_spelling(self):
            a, b = self.two()
            alias = self.alias_of(a)
            self.ok(run(self.home, a, "--harness", "grok"))
            paths = grok_paths(self.home)
            owner = owner_file(self.home, a)
            if owner.exists():
                owner.unlink()
            write_legacy(self.home, [{"harnesses": ["grok"], "path": path} for path in paths], [])
            link = provider_link(self.home, "grok", "swarm")
            link.unlink()
            text = str(alias / "skills" / "swarm")
            os.symlink(text, link)
            self.ok(run(self.home, b, "--harness", "grok", "--replace"))
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
            self.ok(run(self.home, alias, "--harness", "grok", "uninstall"), "removed 2 links, restored 0 entries")
            self.assertEqual(os.readlink(link), text)
            self.assertEqual((link / "SKILL.md").read_bytes(), (a / "skills" / "swarm" / "SKILL.md").read_bytes())
            left = read_legacy(self.home)["links"]
            self.assertEqual(left, [{"harnesses": ["grok"], "path": str(link)}])
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
            self.assertEqual(os.readlink(link), text)
            self.assertEqual(read_legacy(self.home)["links"], [{"harnesses": ["grok"], "path": str(link)}])
    def test_row_8_relative_link_text_is_restored_exactly(self):
            a, b = self.two()
            self.ok(run(self.home, a, "--harness", "grok"))
            expected = {}
            for name in NAMES:
                link = provider_link(self.home, "grok", name)
                relative = os.path.relpath(os.readlink(link), link.parent)
                link.unlink()
                os.symlink(relative, link)
                expected[name] = relative
            self.ok(run(self.home, b, "--harness", "grok", "--replace"))
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
            for name in NAMES:
                link = provider_link(self.home, "grok", name)
                self.assertEqual(os.readlink(link), expected[name])
                self.assertEqual((link / "SKILL.md").read_bytes(), (a / "skills" / name / "SKILL.md").read_bytes())
            self.assert_claims(a, grok_paths(self.home))
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
            self.assert_gone()
            self.assert_empty_records()
    def test_row_9_alias_invocation_records_the_canonical_checkout(self):
            a, b = self.two()
            alias = self.alias_of(a)
            self.ok(run(self.home, alias, "--harness", "grok"))
            self.assert_grok_text(a)
            self.assert_claims(a, grok_paths(self.home))
            self.ok(run(self.home, b, "--harness", "grok", "--replace"))
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
            self.assert_claims(a, grok_paths(self.home))
            self.ok(run(self.home, alias, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
            self.assert_gone()
            self.assert_empty_records()
    def share_skills(self, source, dest):
            shutil.rmtree(dest / "skills")
            os.symlink(source / "skills", dest / "skills")
    def assert_shared_tree(self, installer, other):
            self.ok(run(self.home, installer, "--harness", "grok"))
            self.ok(run(self.home, other, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
            self.assert_grok_text(installer)
            self.assert_claims(installer, grok_paths(self.home))
            self.ok(run(self.home, installer, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
            self.assert_gone()
            self.assert_empty_records()
    def test_row_10_forward_shared_skills_tree(self):
            a, b = self.two()
            self.share_skills(a, b)
            self.assert_shared_tree(b, a)
    def test_row_10_reverse_shared_skills_tree(self):
            a, b = self.two()
            self.share_skills(b, a)
            self.assert_shared_tree(b, a)
    def test_row_11a_deleted_checkout_links_stay_and_the_other_owner_remains(self):
            a, b = self.two()
            later = make_checkout(self.home, "l")
            self.ok(run(self.home, later, "--harness", "codex"))
            self.ok(run(self.home, a, "--harness", "grok"))
            texts = {name: os.readlink(provider_link(self.home, "grok", name)) for name in NAMES}
            shutil.rmtree(a)
            self.ok(run(self.home, b, "uninstall"), "removed 0 links, restored 0 entries")
            for name in NAMES:
                link = provider_link(self.home, "grok", name)
                self.assertTrue(os.path.lexists(link))
                self.assertFalse(os.path.exists(link))
                self.assertEqual(os.readlink(link), texts[name])
            self.assert_claims(a, grok_paths(self.home))
            codex_paths = [str(provider_link(self.home, "codex", name)) for name in NAMES]
            self.assert_claims(later, codex_paths)
            self.ok(run(self.home, later, "--harness", "codex", "uninstall"), "removed 3 links, restored 0 entries")
            for name in NAMES:
                self.assertEqual(os.readlink(provider_link(self.home, "grok", name)), texts[name])
            self.assert_claims(a, grok_paths(self.home))
            tagged = [row for row in read_legacy(self.home)["links"] if row.get("checkout") == str(a)]
            self.assertEqual({row["path"] for row in tagged}, set(grok_paths(self.home)))
            self.assertIsNone(read_owner(self.home, later))
