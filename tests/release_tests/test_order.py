if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import shutil
import subprocess
import unittest

from .support import (
    HEADING,
    SECTION,
    UPKEEP,
    ReleaseCase,
    commit,
    git,
    history,
    release,
    repository,
    setUpModule,
    test_pstack_t3,
)


class OrderTest(ReleaseCase):
    def test_order_matches_the_documented_log(self):
        with history() as repo:
            self.assertEqual(SECTION, HEADING + UPKEEP + "\n".join(test_pstack_t3.release_bullets(repo)) + "\n")
            self.assert_section(repo, SECTION)

    def test_one_commit_orders_by_name_whatever_git_prints(self):
        with repository() as repo:
            (repo / "order").write_text("changes/c.md\n")
            git(repo, "config", "diff.orderFile", "order")
            commit(repo, {"changes/b.md": "- b\n", "changes/a.md": "- a\n", "changes/c.md": "- c\n"}, day=2)
            self.assertEqual(git(repo, *release.LOG), "\x01\0\nchanges/c.md\0changes/a.md\0changes/b.md\0")
            self.assert_section(repo, HEADING + UPKEEP + "- a\n- b\n- c\n")

    def test_same_second_commits_keep_commit_order(self):
        with repository() as repo:
            commit(repo, {"changes/b.md": "- b\n"}, day=2)
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            self.assert_section(repo, HEADING + UPKEEP + "- b\n- a\n")

    def test_an_earlier_dated_later_commit_keeps_commit_order(self):
        with repository() as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=9)
            commit(repo, {"changes/b.md": "- b\n"}, day=2)
            commit(repo, {"changes/c.md": "- c\n"}, day=5)
            self.assert_section(repo, HEADING + UPKEEP + "- a\n- b\n- c\n")

    def test_renamed_fragment_orders_at_the_rename(self):
        with repository() as repo:
            commit(repo, {"changes/old.md": "- old\n"}, day=2)
            commit(repo, {"changes/mid.md": "- mid\n"}, day=3)
            git(repo, "mv", "changes/old.md", "changes/new.md")
            git(repo, "commit", "-q", "-m", "mv", date="2026-01-04T12:00:00+00:00")
            self.assert_section(repo, HEADING + UPKEEP + "- mid\n- old\n")

    def test_non_ascii_fragment_name(self):
        with repository() as repo:
            commit(repo, {"changes/café.md": "- café\n"}, day=2)
            commit(repo, {"changes/a.md": "- a\n"}, day=3)
            self.assert_section(repo, HEADING + UPKEEP + "- café\n- a\n")

    def test_edited_fragment_keeps_the_order_of_its_add(self):
        with repository() as repo:
            commit(repo, {"changes/b.md": "- b\n"}, day=2)
            commit(repo, {"changes/a.md": "- a\n"}, day=3)
            commit(repo, {"changes/b.md": "- b\n- b again\n"}, day=4)
            self.assert_section(repo, HEADING + UPKEEP + "- b\n- b again\n- a\n")

    def test_git_log_record_shape(self):
        with repository() as repo:
            commit(repo, {"changes/z.md": "- z\n", "changes/a.md": "- a\n", "other": "x\n"}, day=2)
            commit(repo, {"other": "y\n"}, day=3)
            commit(repo, {"changes/b.md": "- b\n"}, day=4)
            self.assertEqual(git(repo, *release.LOG), "\x01\0\nchanges/a.md\0changes/z.md\0\x01\0\nchanges/b.md\0")

    @unittest.skipUnless(shutil.which("ssh-keygen"), "needs ssh-keygen to sign a commit")
    def test_signed_commits_keep_commit_order_with_show_signature_on(self):
        with repository() as repo:
            key = repo.parent / "key"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
            git(repo, "config", "gpg.format", "ssh")
            git(repo, "config", "user.signingkey", f"{key}.pub")
            git(repo, "config", "commit.gpgsign", "true")
            git(repo, "config", "log.showSignature", "true")
            commit(repo, {"changes/b.md": "- b\n"}, day=2)
            commit(repo, {"changes/a.md": "- a\n"}, day=3)
            self.assertIn("-----BEGIN SSH SIGNATURE-----", git(repo, "cat-file", "commit", "HEAD"))
            self.assert_section(repo, HEADING + UPKEEP + "- b\n- a\n")

    def test_added_at(self):
        self.assertEqual(release.added_at(""), {})
        self.assertEqual(
            release.added_at("\x01\0\nchanges/a.md\0changes/z.md\0\x01\0\nchanges/b.md\0"),
            {"a.md": 1, "z.md": 1, "b.md": 2},
        )
        self.assertEqual(
            release.added_at("\x01\0\nchanges/a.md\0\x01\0\nchanges/nested/x.md\0\x01\0\x01\0\nchanges/a.md\0"),
            {"a.md": 4},
        )
        for log in ("No signature\n\x01\0\nchanges/a.md\0", "changes/a.md\0", "\x01\0\nother/a.md\0"):
            with self.subTest(log=log), self.assertRaises(ValueError):
                release.added_at(log)
