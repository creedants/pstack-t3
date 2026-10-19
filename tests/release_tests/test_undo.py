import pathlib
import shlex
import unittest
from pathlib import Path
from unittest import mock

from .support import (
    setUpModule,
    ReleaseCase,
    ENV,
    git,
    commit,
    run,
    state,
    repository,
    recording_handlers,
    release,
)

from .test_rollover import big_changelog


def run_archive_undo(repo):
    for cmd in release.ARCHIVE_UNDO.split(" && "):
        parts = shlex.split(cmd)
        git(repo, *parts[1:])


class UndoTest(ReleaseCase):
    def test_printed_archive_undo_restores_a_staged_rollover(self):
        with repository(changelog=big_changelog()) as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            before = state(repo)
            self.assertEqual(run(repo, "0.1.1").returncode, 0)
            git(repo, "add", "CHANGELOG.md", "changes/", "docs/changelog")
            run_archive_undo(repo)
            self.assertEqual(state(repo), before)

    def test_printed_archive_undo_restores_an_unstaged_rollover(self):
        with repository(changelog=big_changelog()) as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            before = state(repo)
            self.assertEqual(run(repo, "0.1.1").returncode, 0)
            run_archive_undo(repo)
            self.assertEqual(state(repo), before)

    def test_undo_for_returns_the_archive_undo_when_created(self):
        with repository(changelog=big_changelog()) as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            with mock.patch.object(release, "ROOT", repo):
                cut = release.plan("0.1.1", "2026-10-09")
            self.assertTrue(cut.created)
            self.assertEqual(release.undo_for(cut), release.ARCHIVE_UNDO)

    def test_undo_for_returns_the_plain_undo_when_nothing_archived(self):
        with repository() as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            with mock.patch.object(release, "ROOT", repo):
                cut = release.plan("0.2.1", "2026-10-09")
            self.assertFalse(cut.created)
            self.assertEqual(release.undo_for(cut), release.UNDO)

    def test_failed_rollover_delete_puts_everything_back_and_deletes_created_files(self):
        with repository(changelog=big_changelog()) as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            before = state(repo)
            real_unlink = pathlib.Path.unlink
            calls = []

            def unlink(path, *args, **kwargs):
                calls.append(path.name)
                if len(calls) == 1:
                    raise PermissionError(13, "Permission denied", str(path))
                return real_unlink(path, *args, **kwargs)

            with mock.patch.object(release, "ROOT", repo):
                cut = release.plan("0.1.1", "2026-10-09")
                self.assertTrue(cut.created)
                with mock.patch.object(pathlib.Path, "unlink", unlink):
                    with self.assertRaises(SystemExit) as raised:
                        release.apply(cut)
            message = str(raised.exception)
            self.assertTrue(message.startswith("stopped partway: "))
            self.assertTrue(message.endswith("; nothing changed, fix that and rerun"))
            self.assertEqual(state(repo), before)
            self.assertFalse((repo / "docs/changelog").exists() and list((repo / "docs/changelog").iterdir()))
            self.assertEqual(list(repo.glob(".*.tmp")) + list((repo / "changes").glob(".*.tmp")), [])

    def test_failed_created_write_puts_everything_back(self):
        with repository(changelog=big_changelog()) as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            before = state(repo)
            real_write = release.write_file

            def write_file(path, data, mode):
                if path.parent.name == "changelog":
                    raise OSError(28, "No space left on device", str(path))
                return real_write(path, data, mode)

            with mock.patch.object(release, "ROOT", repo):
                cut = release.plan("0.1.1", "2026-10-09")
                with mock.patch.object(release, "write_file", write_file):
                    with self.assertRaises(SystemExit) as raised:
                        release.apply(cut)
            self.assertTrue(str(raised.exception).startswith("stopped partway: "))
            self.assertEqual(state(repo), before)


if __name__ == "__main__":
    unittest.main()
