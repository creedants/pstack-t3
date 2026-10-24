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


class Part06Test(OwnershipCase):
    def noreplace(self, rename):
            """What the no-replace rename returned in the last hooked run. `symbol` and `libc` must have taken it away."""
            code = noreplace_result(self.home)
            if rename in ("symbol", "libc"):
                self.assertEqual(code, errno.ENOSYS)
            else:
                self.assertEqual(code, 0)
            return code
    def test_a_put_back_without_hard_links_never_replaces_a_file_that_took_the_path(self):
            for rename in (None, "platform", "symbol"):
                with self.subTest(rename=rename):
                    self.use_fresh()
                    a = make_checkout(self.home, "a")
                    self.ok(run(self.home, a, "--harness", "grok"))
                    swarm = provider_link(self.home, "grok", "swarm")
                    hook = put_back_race(swarm, "link", occupy="file", link_error=True, rename=rename)
                    raced = uninstall_hooked(self.home, a, hook)
                    code = self.noreplace(rename)
                    self.assertFalse(swarm.is_symlink())
                    self.assertEqual(swarm.read_bytes(), b"occupant\x00\xff")
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
                    self.assertEqual(os.readlink(aside[0]), "/foreign/swarm")
                    self.assert_claims(a, [str(swarm)])
    def test_a_put_back_with_no_safe_rename_keeps_the_entry_aside(self):
            a = make_checkout(self.home, "a")
            self.ok(run(self.home, a, "--harness", "grok"))
            swarm = provider_link(self.home, "grok", "swarm")
            raced = uninstall_hooked(self.home, a, put_back_race(swarm, "link", link_error=True, rename="libc"))
            self.noreplace("libc")
            self.assertFalse(os.path.lexists(swarm))
            aside = set_aside(swarm)
            self.assertEqual(len(aside), 1, aside)
            self.skipped(
                raced,
                "removed 2 links, restored 0 entries",
                f"skipped unlink {swarm}: the link no longer matches the recorded checkout and {swarm} cannot be refilled "
                f"without risking an overwrite ({os.strerror(errno.ENOSYS)}); it is kept at {aside[0]}",
            )
            self.assertEqual(os.readlink(aside[0]), "/foreign/swarm")
            self.assert_claims(a, [str(swarm)])
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
    def assert_displaced(self, path, kind):
            if kind == "directory":
                self.assertEqual(os.listdir(path), ["SKILL.md"])
                self.assertEqual((path / "SKILL.md").read_bytes(), b"displaced\x00\xfe")
            elif kind == "link":
                self.assertEqual(os.readlink(path), "/foreign/displaced")
            else:
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.read_bytes(), b"displaced\x00\xfe")
    def assert_restore_kept(self, raced, swarm, rows, kind, reason):
            """The raced restore saw the occupant arrive, left no set-aside copy, and kept the backup and its row where they were."""
            self.assertEqual((self.home / "filled").read_text(), "True")
            backup = Path(rows[0]["backup"])
            self.skipped(raced, "removed 3 links, restored 0 entries", f"skipped restore {backup}: {reason}")
            self.assert_displaced(backup, kind)
            self.assertEqual(os.listdir(backup.parent), [backup.name])
            self.assertEqual(read_legacy(self.home)["backups"], rows)
            self.assertEqual(set_aside(swarm), [])
    def restore_reason(self, swarm, kind, link_error, rename):
            """Why a put-back onto a taken swarm declined: a hard link sees the occupant, else the no-replace rename decides."""
            code = self.noreplace(rename)
            if (kind != "directory" and not link_error) or code == 0:
                return f"{swarm} was taken again"
            return f"{swarm} cannot be refilled without risking an overwrite ({os.strerror(code)})"
    def test_a_restore_never_replaces_an_entry_that_took_the_path(self):
            cases = (
                ("file", "file", False),
                ("file", "file", True),
                ("file", "link", False),
                ("file", "directory", False),
                ("directory", "directory", False),
                ("directory", "file", False),
            )
            for kind, occupy, link_error in cases:
                for rename in (None, "platform", "symbol"):
                    with self.subTest(kind=kind, occupy=occupy, link_error=link_error, rename=rename):
                        self.use_fresh()
                        a, swarm, rows = self.displaced_by(kind)
                        raced = uninstall_hooked(self.home, a, restore_race(swarm, occupy, link_error=link_error, rename=rename))
                        reason = self.restore_reason(swarm, kind, link_error, rename)
                        self.assert_restore_kept(raced, swarm, rows, kind, reason)
                        if occupy == "file":
                            self.assertEqual(swarm.read_bytes(), b"occupant\x00\xff")
                            swarm.unlink()
                        elif occupy == "link":
                            self.assertEqual(os.readlink(swarm), "/foreign/occupant")
                            swarm.unlink()
                        else:
                            self.assertEqual(os.listdir(swarm), ["precious"])
                            self.assertEqual((swarm / "precious").read_bytes(), b"directory bytes\x00")
                            shutil.rmtree(swarm)
                        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 1 entries")
                        self.assert_displaced(swarm, kind)
                        self.assert_empty_records()
    def test_a_restore_never_writes_into_another_checkout_that_took_the_path(self):
            cases = (("file", False), ("file", True), ("directory", False))
            for kind, link_error in cases:
                for rename in (None, "platform", "symbol"):
                    with self.subTest(kind=kind, link_error=link_error, rename=rename):
                        self.use_fresh()
                        a, swarm, rows = self.displaced_by(kind)
                        b = make_checkout(self.home, "b", old=True)
                        tree = snapshot(b / "skills")
                        install_b = [sys.executable, str(b / "scripts" / "install.py"), "--harness", "grok"]
                        hook = restore_race(swarm, install_b, link_error=link_error, rename=rename)
                        raced = uninstall_hooked(self.home, a, hook)
                        reason = self.restore_reason(swarm, kind, link_error, rename)
                        self.assert_restore_kept(raced, swarm, rows, kind, reason)
                        self.assertEqual(snapshot(b / "skills"), tree)
                        self.assert_grok_text(b)
                        self.ok(run(self.home, b, "--harness", "grok", "uninstall"), "removed 3 links, restored 1 entries")
                        self.assertEqual(snapshot(b / "skills"), tree)
                        self.assert_displaced(swarm, kind)
                        self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 0 entries")
                        self.assert_displaced(swarm, kind)
                        self.assertFalse(legacy_file(self.home).exists())
    def test_a_backup_swapped_during_uninstall_is_not_restored(self):
            a, swarm, rows = self.displaced_by("file")
            backup = rows[0]["backup"]
            alpha = str(provider_link(self.home, "grok", "alpha"))
            hook = (
                "swapped = False\n"
                "def swapping(original):\n"
                "    def call(source, destination, *args, **kwargs):\n"
                "        global swapped\n"
                f"        if not swapped and str(source) == {alpha!r}:\n"
                "            swapped = True\n"
                f"            os.unlink({backup!r})\n"
                f"            Path({backup!r}).write_bytes(b'swapped\\x00')\n"
                "        return original(source, destination, *args, **kwargs)\n"
                "    return call\n"
                "os.rename = swapping(os.rename)\n"
            )
            self.skipped(
                uninstall_hooked(self.home, a, hook),
                "removed 3 links, restored 0 entries",
                f"skipped restore {backup}: the backup is no longer the entry uninstall read",
            )
            self.assertFalse(os.path.lexists(swarm))
            self.assertEqual(Path(backup).read_bytes(), b"swapped\x00")
            self.assertEqual(read_legacy(self.home)["backups"], rows)
    def test_a_swapped_backup_that_reuses_the_inode_number_and_change_time_is_not_restored(self):
            """A filesystem may give a freed inode's numbers to the next new entry. Here every freed one is reused."""
            a, swarm, rows = self.displaced_by("file")
            backup = rows[0]["backup"]
            alpha = str(provider_link(self.home, "grok", "alpha"))
            held = self.home / "held-after-uninstall"
            hook = (
                "import types\n"
                f"backup = {backup!r}\n"
                "planned = os.lstat(backup)\n"
                "real_lstat = os.lstat\n"
                "def in_use():\n"
                "    for fd in range(1024):\n"
                "        try:\n"
                "            stat = os.fstat(fd)\n"
                "        except OSError:\n"
                "            continue\n"
                "        if (stat.st_dev, stat.st_ino) == (planned.st_dev, planned.st_ino):\n"
                "            return True\n"
                "    return False\n"
                "def lstat(path, *args, **kwargs):\n"
                "    stat = real_lstat(path, *args, **kwargs)\n"
                "    if str(path) != backup or (stat.st_dev, stat.st_ino) == (planned.st_dev, planned.st_ino) or in_use():\n"
                "        return stat\n"
                "    fields = {name: getattr(stat, name) for name in dir(stat) if name.startswith('st_')}\n"
                "    fields.update(st_dev=planned.st_dev, st_ino=planned.st_ino, st_ctime_ns=planned.st_ctime_ns)\n"
                "    return types.SimpleNamespace(**fields)\n"
                "swapped = False\n"
                "def swapping(original):\n"
                "    def call(source, destination, *args, **kwargs):\n"
                "        global swapped\n"
                f"        if not swapped and str(source) == {alpha!r}:\n"
                "            swapped = True\n"
                "            os.unlink(backup)\n"
                "            Path(backup).write_bytes(b'swapped\\x00')\n"
                "            os.lstat = lstat\n"
                "        return original(source, destination, *args, **kwargs)\n"
                "    return call\n"
                "os.rename = swapping(os.rename)\n"
                "planned_uninstall = module.uninstall\n"
                "def uninstall(args):\n"
                "    code = planned_uninstall(args)\n"
                f"    Path({str(held)!r}).write_text(str(in_use()))\n"
                "    return code\n"
                "module.uninstall = uninstall\n"
            )
            self.skipped(
                uninstall_hooked(self.home, a, hook),
                "removed 3 links, restored 0 entries",
                f"skipped restore {backup}: the backup is no longer the entry uninstall read",
            )
            self.assertFalse(os.path.lexists(swarm))
            self.assertEqual(Path(backup).read_bytes(), b"swapped\x00")
            self.assertEqual(read_legacy(self.home)["backups"], rows)
            self.assertEqual(held.read_text(), "False")
    def test_a_backup_that_cannot_be_held_open_is_not_restored(self):
            a, swarm, rows = self.displaced_by("file")
            backup = rows[0]["backup"]
            hook = (
                "import errno\n"
                "real_open = os.open\n"
                "def refusing(path, *args, **kwargs):\n"
                f"    if str(path) == {backup!r}:\n"
                "        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), path)\n"
                "    return real_open(path, *args, **kwargs)\n"
                "os.open = refusing\n"
            )
            self.skipped(
                uninstall_hooked(self.home, a, hook),
                "removed 3 links, restored 0 entries",
                f"skipped restore {backup}: the backup could not be held open to prove it is the entry uninstall read "
                f"({os.strerror(errno.EACCES)})",
            )
            self.assertFalse(os.path.lexists(swarm))
            self.assertEqual(read_legacy(self.home)["backups"], rows)
            self.ok(run(self.home, a, "--harness", "grok", "uninstall"), "removed 0 links, restored 1 entries")
            self.assert_displaced(swarm, "file")
            self.assert_empty_records()
