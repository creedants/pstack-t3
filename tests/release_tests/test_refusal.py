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
import tempfile
import unittest
from pathlib import Path

from .support import (
    CHANGELOG,
    DIRTY,
    ENV,
    NOT_A_BULLET,
    ROOT,
    SECTION,
    ReleaseCase,
    commit,
    git,
    history,
    repository,
    run,
    setUpModule,
    state,
)


class RefusalTest(ReleaseCase):
    def test_refuses_bad_version(self):
        with history() as repo:
            for version in ("v0.3.0", "0.3", "0.03.0", "0.3.0-rc1", "0.3.0.1", "٠.٣.٠"):
                with self.subTest(version=version):
                    self.assert_refuses(
                        repo, version, f"version {version} is not X.Y.Z; pass three numbers such as 0.3.0\n"
                    )

    def test_refuses_bad_date(self):
        with history() as repo:
            for date in ("2026-02-30", "20261009", "10/09/2026"):
                with self.subTest(date=date):
                    before = state(repo)
                    result = run(repo, "0.3.0", date=date)
                    self.assertEqual(
                        (result.returncode, result.stdout, result.stderr),
                        (1, "", f"date {date} is not YYYY-MM-DD; pass a real date such as 2026-10-09\n"),
                    )
                    self.assertEqual(state(repo), before)

    def test_refuses_outside_a_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            (repo / "scripts").mkdir(parents=True)
            shutil.copy(ROOT / "scripts/release.py", repo / "scripts/release.py")
            shutil.copy(ROOT / "scripts/release_notes.py", repo / "scripts/release_notes.py")
            (repo / "CHANGELOG.md").write_text(CHANGELOG)
            (repo / "changes").mkdir()
            (repo / "changes/a.md").write_text("- a\n")
            empty = Path(directory) / "empty"
            empty.mkdir()
            cases = {
                "no repository": ({**ENV, "GIT_CEILING_DIRECTORIES": directory}, "git rev-parse failed: fatal: not a git repository"),
                "no git": ({**ENV, "PATH": str(empty)}, "git rev-parse failed: [Errno 2] No such file or directory: 'git'"),
            }
            for name, (env, message) in cases.items():
                with self.subTest(name):
                    result = run(repo, "0.3.0", env=env)
                    self.assertEqual((result.returncode, result.stdout), (1, ""))
                    self.assertEqual(result.stderr[: len(message)], message)
                    self.assertEqual((repo / "CHANGELOG.md").read_text(), CHANGELOG)
                    self.assertEqual((repo / "changes/a.md").read_text(), "- a\n")

    def test_refuses_shallow_clone(self):
        with history() as repo:
            clone = repo.parent / "clone" / "repo"
            git(repo.parent, "clone", "-q", "--depth", "1", repo.as_uri(), str(clone))
            self.assert_refuses(
                clone, "0.3.0",
                "this clone is shallow, so git cannot tell when each fragment was added; "
                "run git fetch --unshallow and rerun\n",
            )

    def test_refuses_uncommitted_changes(self):
        def untracked(repo):
            (repo / "changes/new.md").write_text("- new\n")

        def modified(repo):
            (repo / "changes/beta.md").write_text("- beta, edited\n")

        def staged(repo):
            modified(repo)
            git(repo, "add", "changes/beta.md")

        def deleted(repo):
            (repo / "changes/beta.md").unlink()

        def changelog(repo):
            (repo / "CHANGELOG.md").write_text(CHANGELOG + "\nMore.\n")

        cases = (
            (untracked, "?? changes/new.md\n"),
            (modified, " M changes/beta.md\n"),
            (staged, "M  changes/beta.md\n"),
            (deleted, " D changes/beta.md\n"),
            (changelog, " M CHANGELOG.md\n"),
        )
        for change, status in cases:
            with self.subTest(change.__name__), history() as repo:
                change(repo)
                self.assert_refuses(repo, "0.2.1", DIRTY + status)

    def test_uncommitted_changes_elsewhere_do_not_refuse(self):
        with history() as repo:
            (repo / "notes.txt").write_text("unrelated\n")
            self.assert_section(repo, SECTION)

    def test_refuses_a_symlinked_changelog(self):
        with repository() as repo:
            commit(repo, {"changes/a.md": "- a\n", "real.md": CHANGELOG}, day=2)
            (repo / "CHANGELOG.md").unlink()
            (repo / "CHANGELOG.md").symlink_to("real.md")
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "link", date="2026-01-03T12:00:00+00:00")
            self.assert_refuses(repo, "0.3.0", "CHANGELOG.md is a symlink; replace it with the file before you release\n")

    def test_refuses_existing_heading(self):
        with history() as repo:
            self.assert_refuses(repo, "0.2.0", "CHANGELOG.md already has a ## 0.2.0 heading; pick the next version\n")
            self.assert_refuses(repo, "0.1.0", "CHANGELOG.md already has a ## 0.1.0 heading; pick the next version\n")

    def test_refuses_a_garbage_dated_duplicate_heading(self):
        with repository("# Changelog\n\n## 0.3.0 (tomorrow)\n\n- draft\n\n## 0.2.0 (2026-10-06)\n\n- old\n") as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            self.assert_refuses(repo, "0.3.0", "CHANGELOG.md already has a ## 0.3.0 heading; pick the next version\n")
            self.assert_refuses(
                repo, "0.2.5",
                "0.2.5 is not above 0.3.0, the latest version in CHANGELOG.md; pick a higher version\n",
            )

    def test_refuses_version_not_above_latest(self):
        with history() as repo:
            self.assert_refuses(
                repo, "0.1.5",
                "0.1.5 is not above 0.2.0, the latest version in CHANGELOG.md; pick a higher version\n",
            )
        with repository("# Changelog\n\n## 0.2.0\n\n- backport\n\n## 0.9.0 (2026-10-06)\n\n- old\n") as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            self.assert_refuses(
                repo, "0.3.0",
                "0.3.0 is not above 0.9.0, the latest version in CHANGELOG.md; pick a higher version\n",
            )
            result = run(repo, "0.9.1", "--dry-run")
            self.assertEqual((result.returncode, result.stdout[:22]), (0, "## 0.9.1 (2026-10-09)\n"))

    def test_refuses_without_fragments(self):
        message = "changes/ holds no fragments; there is nothing to release\n"
        with repository() as repo:
            self.assert_refuses(repo, "0.3.0", message)
            (repo / "changes").mkdir()
            self.assert_refuses(repo, "0.3.0", message)

    def test_refuses_a_non_md_entry(self):
        def nested(repo):
            commit(repo, {"changes/nested/x.md": "- x\n"}, day=3)

        def text(repo):
            commit(repo, {"changes/notes.txt": "- notes\n"}, day=3)

        def link(repo):
            (repo / "changes/link.md").symlink_to("a.md")
            commit(repo, {}, day=3)

        for change, name in ((nested, "nested"), (text, "notes.txt"), (link, "link.md")):
            with self.subTest(name), repository() as repo:
                commit(repo, {"changes/a.md": "- a\n"}, day=2)
                change(repo)
                self.assert_refuses(
                    repo, "0.3.0",
                    f"changes/{name} is not a fragment; changes/ holds only .md files, so move or delete it\n",
                )

    def test_ignored_non_fragment_gitkeep(self):
        with repository() as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            (repo / ".git/info/exclude").write_text("changes/.gitkeep\n")
            (repo / "changes/.gitkeep").write_text("")
            self.assertEqual(git(repo, "status", "--porcelain", "--untracked-files=all"), "")
            self.assert_refuses(
                repo, "0.3.0",
                "changes/.gitkeep is not a fragment; changes/ holds only .md files, so move or delete it\n",
            )

    def test_refuses_line_that_is_not_a_bullet(self):
        cases = (
            ("- ok\nprose\n", "line 2 " + NOT_A_BULLET),
            ("- ok\n\n- two\n", "line 2 " + NOT_A_BULLET),
            ("- ok\n\n", "line 2 " + NOT_A_BULLET),
            ("  indented\n", "line 1 " + NOT_A_BULLET),
            ("", "holds no bullet; fix the fragment, commit it, and rerun\n"),
        )
        for text, message in cases:
            with self.subTest(text=text), repository() as repo:
                commit(repo, {"changes/a.md": "- a\n", "changes/note.md": text}, day=2)
                self.assert_refuses(repo, "0.3.0", "changes/note.md " + message)

    def test_refuses_a_fragment_with_cr(self):
        for text in (b"- ok\r\n- two\r\n", b"- ok\rprose\n"):
            with self.subTest(text=text), repository() as repo:
                commit(repo, {"changes/note.md": text}, day=2)
                self.assert_refuses(repo, "0.3.0", "changes/note.md line 1 " + NOT_A_BULLET)

    def test_refuses_a_fragment_that_is_not_utf8(self):
        with repository() as repo:
            commit(repo, {"changes/note.md": b"- caf\xe9\n"}, day=2)
            self.assert_refuses(repo, "0.3.0", "changes/note.md is not UTF-8; fix it\n")

    def test_refuses_a_changelog_that_is_not_utf8(self):
        with repository() as repo:
            commit(repo, {"CHANGELOG.md": b"# Changelog\n\n- caf\xe9\n", "changes/a.md": "- a\n"}, day=2)
            self.assert_refuses(repo, "0.3.0", "CHANGELOG.md is not UTF-8; fix it\n")

    def test_refuses_ignored_fragment(self):
        with history() as repo:
            (repo / ".git/info/exclude").write_text("changes/local.md\n")
            (repo / "changes/local.md").write_text("- local\n")
            self.assertEqual(git(repo, "status", "--porcelain", "--untracked-files=all"), "")
            self.assert_refuses(
                repo, "0.2.1",
                "git log shows no commit that added changes/local.md; commit the fragment and rerun\n",
            )
