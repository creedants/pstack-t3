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


class Part05Test(OwnershipCase):
    def backup_dir(self, harness="grok"):
            found = sorted((state_dir(self.home) / "backups").glob(f"*/{harness}"))
            self.assertEqual(len(found), 1, found)
            return found[0]
    def under_backups(self):
            backups = state_dir(self.home) / "backups"
            return sorted(str(path.relative_to(backups)) for path in backups.rglob("*"))
    def test_failed_move_leaves_no_backup_row(self):
            b = make_checkout(self.home, "b")
            swarm = provider_link(self.home, "grok", "swarm")
            swarm.parent.mkdir(parents=True)
            swarm.write_bytes(b"foreign\x00bytes")
            failed = run_failing(self.home, b, "shutil.move", swarm, "--harness", "grok", "--replace")
            self.skipped(
                failed,
                "linked 2 skills into grok",
                f"skipped move {swarm}: [Errno 13] Permission denied: '{swarm}'",
            )
            self.assertEqual(read_legacy(self.home)["backups"], [])
            self.assertEqual(swarm.read_bytes(), b"foreign\x00bytes")
            alpha, runtime = (str(provider_link(self.home, "grok", name)) for name in ("alpha", "pstack-runtime"))
            self.assert_claims(b, [alpha, runtime])
    def test_failed_link_leaves_no_claim_or_row(self):
            b = make_checkout(self.home, "b")
            swarm = provider_link(self.home, "grok", "swarm")
            failed = run_failing(self.home, b, "os.symlink", swarm, "--harness", "grok")
            self.skipped(
                failed,
                "linked 2 skills into grok",
                f"skipped link {swarm}: [Errno 13] Permission denied: '{swarm}'",
            )
            alpha, runtime = (str(provider_link(self.home, "grok", name)) for name in ("alpha", "pstack-runtime"))
            self.assert_claims(b, [alpha, runtime])
            self.assertEqual(sorted(row["path"] for row in read_legacy(self.home)["links"]), [alpha, runtime])
    def assert_refused(self, result, file):
            self.assertNotEqual(result.returncode, 0)
            lines = result.stderr.strip().splitlines()
            self.assertEqual(len(lines), 1, result.stderr)
            self.assertIn(str(file), lines[0])
    def test_malformed_legacy_file_is_refused_unchanged(self):
            a = make_checkout(self.home, "a")
            for text in ('["kept"]\n', '{"links": {"kept": 1}}\n', "{not json\n"):
                legacy_file(self.home).parent.mkdir(parents=True, exist_ok=True)
                legacy_file(self.home).write_text(text)
                self.assert_refused(run(self.home, a, "--harness", "grok"), legacy_file(self.home))
                self.assertEqual(legacy_file(self.home).read_text(), text)
                self.assert_gone()
    def test_owner_file_that_is_not_json_is_refused_unchanged(self):
            a = make_checkout(self.home, "a")
            owner = owner_file(self.home, a)
            owner.parent.mkdir(parents=True)
            owner.write_text("{not json\n")
            self.assert_refused(run(self.home, a, "--harness", "grok"), owner)
            self.assertEqual(owner.read_text(), "{not json\n")
            self.assert_gone()
    def test_backup_row_without_harnesses_matches_its_directory(self):
            a = make_checkout(self.home, "a")
            swarm = provider_link(self.home, "grok", "swarm")
            swarm.parent.mkdir(parents=True)
            saved = state_dir(self.home) / "backups" / "old" / "grok" / "swarm"
            saved.mkdir(parents=True)
            (saved / "SKILL.md").write_text("saved\n")
            write_legacy(self.home, [], [{"original": str(swarm), "backup": str(saved)}])
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 1 entries")
            self.assertEqual((swarm / "SKILL.md").read_text(), "saved\n")
    def test_partial_move_keeps_the_row_for_its_copy(self):
            b = make_checkout(self.home, "b")
            skills = provider_link(self.home, "grok", "swarm").parent
            swarm = skills / "swarm"
            swarm.mkdir(parents=True)
            (swarm / "SKILL.md").write_text("precious\n")
            skills.chmod(0o555)
            self.addCleanup(skills.chmod, 0o755)
            self.skipped(run(self.home, b, "--harness", "grok", "--replace"), "linked 0 skills into grok")
            rows = read_legacy(self.home)["backups"]
            self.assertEqual([row["original"] for row in rows], [str(swarm)])
            self.assertEqual((Path(rows[0]["backup"]) / "SKILL.md").read_text(), "precious\n")
            stamp = Path(rows[0]["backup"]).parent.parent.name
            self.assertEqual(self.under_backups(), [stamp, f"{stamp}/grok", f"{stamp}/grok/swarm", f"{stamp}/grok/swarm/SKILL.md"])
    def test_old_replace_before_a_removal_keeps_its_link_and_our_claim(self):
            a = make_checkout(self.home, "a")
            b = make_checkout(self.home, "b", old=True)
            self.ok(run(self.home, a, "--harness", "grok"))
            replace = [sys.executable, b / "scripts" / "install.py", "--harness", "grok", "--replace"]
            raced = interleave_removal(self.home, a, grok_paths(self.home), replace, "--harness", "grok", "uninstall")
            self.skipped(raced, "removed 0 links, restored 0 entries", KEPT.format(n=3))
            self.assert_grok_text(b)
            self.assert_claims(a, grok_paths(self.home))
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
            self.assert_grok_text(a)
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
            self.assert_gone()
            self.assertIsNone(read_owner(self.home, a))
            self.assertEqual(sorted(os.listdir(provider_link(self.home, "grok", "swarm").parent)), [])
    def test_old_replace_and_a_new_occupant_keep_the_moved_link_aside(self):
            a = make_checkout(self.home, "a")
            b = make_checkout(self.home, "b", old=True)
            self.ok(run(self.home, a, "--harness", "grok"))
            alpha = provider_link(self.home, "grok", "alpha")
            replace = [sys.executable, b / "scripts" / "install.py", "--harness", "grok", "--replace"]
            raced = interleave_removal(self.home, a, [alpha], replace, "--harness", "grok", "uninstall", occupy=b"occupant\x00")
            self.skipped(raced, "removed 0 links, restored 0 entries", KEPT.format(n=3))
            aside = set_aside(alpha)
            self.assertEqual(len(aside), 1, aside)
            self.assertIn(
                f"skipped unlink {alpha}: the link no longer matches the recorded checkout and {alpha} was taken again; "
                f"it is kept at {aside[0]}",
                raced.stdout.splitlines(),
            )
            self.assertEqual(os.readlink(aside[0]), str(b / "skills" / "alpha"))
            self.assertEqual(alpha.read_bytes(), b"occupant\x00")
            self.assert_grok_text(b, ("pstack-runtime", "swarm"))
            self.assert_claims(a, grok_paths(self.home))
    def test_entry_moved_into_a_backup_during_uninstall_keeps_its_claim(self):
            a = make_checkout(self.home, "a")
            b = make_checkout(self.home, "b", old=True)
            self.ok(run(self.home, a, "--harness", "grok"))
            self.ok(run(self.home, b, "--harness", "grok", "--replace"))
            for name in NAMES:
                provider_link(self.home, "grok", name).unlink()
            relink_then_replace = (
                "import os, subprocess, sys\n"
                f"for name in {list(NAMES)!r}:\n"
                f"    os.symlink(os.path.join({str(a / 'skills')!r}, name), os.path.join({str(provider_link(self.home, 'grok', 'x').parent)!r}, name))\n"
                f"subprocess.run([sys.executable, {str(b / 'scripts' / 'install.py')!r}, '--harness', 'grok', '--replace'], check=True)\n"
            )
            layer = str(self.backup_dir())
            withdrawn = [os.path.join(layer, name) for name in NAMES]
            raced = interleave_removal(
                self.home, a, withdrawn, [sys.executable, "-c", relink_then_replace], "--harness", "grok", "uninstall"
            )
            self.ok(raced, "removed 3 links, restored 0 entries", KEPT.format(n=3))
            self.assert_grok_text(b)
            self.assert_claims(a, grok_paths(self.home))
            self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 3 entries")
            self.assert_grok_text(a)
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 3 links, restored 0 entries")
            self.assert_gone()
            self.assertIsNone(read_owner(self.home, a))
    def test_a_hidden_entry_present_before_uninstall_survives_every_move_aside(self):
            for kind in ("file", "link"):
                with self.subTest(kind=kind):
                    self.use_fresh()
                    a = make_checkout(self.home, "a")
                    self.ok(run(self.home, a, "--harness", "grok"))
                    parent = provider_link(self.home, "grok", "swarm").parent
                    # Two planted entries already hold the colliding names.
                    plant = (
                        "import secrets, tempfile\n"
                        "secrets.token_hex = lambda n: 'deadbeef'\n"
                        "candidates = tempfile._get_candidate_names\n"
                        "def colliding():\n"
                        "    yield 'deadbeef'\n"
                        "    yield from candidates()\n"
                        "tempfile._get_candidate_names = colliding\n"
                        f"for name in ['.swarm.pstack-t3-%d-deadbeef' % os.getpid(), '.pstack-t3-deadbeef']:\n"
                        f"    hidden = os.path.join({str(parent)!r}, name)\n"
                        f"    if {kind!r} == 'file':\n"
                        "        Path(hidden).write_bytes(b'hidden\\x00\\xff')\n"
                        "    else:\n"
                        "        os.symlink('/foreign/hidden', hidden)\n"
                    )
                    raced = uninstall_hooked(self.home, a, plant)
                    self.ok(raced, "removed 3 links, restored 0 entries")
                    self.assert_gone()
                    hidden = sorted(name for name in os.listdir(parent))
                    self.assertEqual(len(hidden), 2, hidden)
                    self.assertEqual(hidden[0], ".pstack-t3-deadbeef")
                    self.assertRegex(hidden[1], r"^\.swarm\.pstack-t3-\d+-deadbeef$")
                    for name in hidden:
                        if kind == "file":
                            self.assertEqual((parent / name).read_bytes(), b"hidden\x00\xff")
                        else:
                            self.assertEqual(os.readlink(parent / name), "/foreign/hidden")
                    self.assertIsNone(read_owner(self.home, a))
    def noreplace(self, rename):
            """What the no-replace rename returned in the last hooked run. `symbol` and `libc` must have taken it away."""
            code = noreplace_result(self.home)
            if rename in ("symbol", "libc"):
                self.assertEqual(code, errno.ENOSYS)
            else:
                self.assertEqual(code, 0)
            return code
    def test_a_directory_put_back_never_replaces_a_directory_that_took_the_path(self):
            for rename in (None, "platform", "symbol"):
                with self.subTest(rename=rename):
                    self.use_fresh()
                    a = make_checkout(self.home, "a")
                    self.ok(run(self.home, a, "--harness", "grok"))
                    swarm = provider_link(self.home, "grok", "swarm")
                    hook = put_back_race(swarm, "directory", occupy="directory", rename=rename)
                    raced = uninstall_hooked(self.home, a, hook)
                    code = self.noreplace(rename)
                    self.assertEqual(os.listdir(swarm), [])
                    aside = set_aside(swarm)
                    self.assertEqual(len(aside), 1, aside)
                    if code == 0:
                        reason = f"{swarm} was taken again"
                    else:
                        reason = f"{swarm} cannot be refilled without risking an overwrite ({os.strerror(code)})"
                    self.skipped(
                        raced,
                        "removed 2 links, restored 0 entries",
                        f"skipped unlink {swarm}: the link no longer matches the recorded checkout and {reason}; "
                        f"it is kept at {aside[0]}",
                    )
                    self.assertEqual((aside[0] / "precious").read_bytes(), b"directory bytes\x00")
                    self.assert_claims(a, [str(swarm)])
    def test_a_foreign_directory_moved_aside_goes_back_to_its_empty_path_or_is_kept_aside(self):
            for rename in (None, "platform", "symbol"):
                with self.subTest(rename=rename):
                    self.use_fresh()
                    a = make_checkout(self.home, "a")
                    self.ok(run(self.home, a, "--harness", "grok"))
                    swarm = provider_link(self.home, "grok", "swarm")
                    raced = uninstall_hooked(self.home, a, put_back_race(swarm, "directory", rename=rename))
                    code = self.noreplace(rename)
                    if code == 0:
                        self.skipped(
                            raced,
                            "removed 2 links, restored 0 entries",
                            f"skipped unlink {swarm}: the link no longer matches the recorded checkout",
                        )
                        self.assertEqual((swarm / "precious").read_bytes(), b"directory bytes\x00")
                        self.assertEqual(os.listdir(swarm.parent), ["swarm"])
                    else:
                        self.assertFalse(os.path.lexists(swarm))
                        aside = set_aside(swarm)
                        self.assertEqual(len(aside), 1, aside)
                        self.skipped(
                            raced,
                            "removed 2 links, restored 0 entries",
                            f"skipped unlink {swarm}: the link no longer matches the recorded checkout and {swarm} cannot be "
                            f"refilled without risking an overwrite ({os.strerror(code)}); it is kept at {aside[0]}",
                        )
                        self.assertEqual((aside[0] / "precious").read_bytes(), b"directory bytes\x00")
                        self.assertEqual(os.listdir(swarm.parent), [aside[0].parent.name])
                    self.assert_claims(a, [str(swarm)])
