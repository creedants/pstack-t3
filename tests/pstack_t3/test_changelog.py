if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "t3/scripts"))
sys.path.append(str(ROOT / "scripts"))

import release
import shutil
import tempfile
from tests.pstack_t3.support import fence_git_environment, fragment_name, changelog_findings, release_bullets, _git


def setUpModule():
    fence_git_environment()

class ChangelogTest(unittest.TestCase):
    def test_fragment_name_encodes_percent_before_slash(self):
        self.assertEqual(fragment_name("docs/a"), "docs%2Fa.md")
        self.assertEqual(fragment_name("docs-a"), "docs-a.md")
        self.assertEqual(fragment_name("a/b%c"), "a%2Fb%25c.md")

    def test_changelog_findings_name_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n- old\n")
            (root / "changes").mkdir()
            (root / "changes" / "note.md").write_text("Prose.\n")
            nested = root / "changes" / "nested"
            nested.mkdir()
            (nested / "x.md").write_text("- hidden\n")
            self.assertEqual(changelog_findings(root), [
                "CHANGELOG.md has a ## Unreleased heading. Write the bullet under changes/.",
                "changes/nested/x.md sits in a subdirectory. changes/ is flat.",
                "changes/note.md holds a line that is not a bullet.",
            ])
            (root / "CHANGELOG.md").write_text("# Changelog\n")
            (root / "changes" / "nested" / "x.md").unlink()
            nested.rmdir()
            (root / "changes" / "note.md").write_text("- First.\n- Second.\n")
            self.assertEqual(changelog_findings(root), [])
            (root / "changes" / "note.md").write_text(
                "- A user-facing change spans\n  two lines in the same bullet.\n"
            )
            self.assertEqual(changelog_findings(root), [])
            (root / "changes" / "note.md").write_text(
                "- A user-facing change spans\nthen prose on its own line.\n"
            )
            self.assertEqual(changelog_findings(root), [
                "changes/note.md holds a line that is not a bullet.",
            ])
            (root / "changes" / "note.md").unlink()
            self.assertEqual(changelog_findings(root), [])
            shutil.rmtree(root / "changes")
            self.assertEqual(changelog_findings(root), [])

    def test_repo_changelog_has_no_unreleased_heading(self):
        findings = changelog_findings(ROOT)
        self.assertEqual(findings, [], "\n".join(findings) or "Write bullets under changes/.")
        contributing = (ROOT / "CONTRIBUTING.md").read_text()
        self.assertIn("git " + " ".join(release.LOG), contributing)
        self.assertNotIn("Add a line under Unreleased", contributing)
        self.assertNotIn("Each bullet is one line", contributing)
        self.assertIn("Indent a continuation line under that bullet.", contributing)
        self.assertIn("docs/guide.md", contributing)
        self.assertIn("one docs change", contributing)

    def test_release_cut_matches_the_fragments_that_still_exist(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            _git(repo, "init", "-q", "-b", "main")
            (repo / "changes").mkdir()
            (repo / "changes" / "first.md").write_text("- alpha\n")
            _git(repo, "add", "changes")
            _git(repo, "commit", "-qm", "first")
            (repo / "changes" / "second.md").write_text("- beta\n")
            _git(repo, "add", "changes")
            _git(repo, "commit", "-qm", "second")
            _git(repo, "rm", "-q", "changes/first.md")
            _git(repo, "commit", "-qm", "cut")
            (repo / "changes" / "first.md").write_text(
                "- alpha again\n  kept on the next line.\n"
            )
            _git(repo, "add", "changes")
            _git(repo, "commit", "-qm", "first again")
            bullets = release_bullets(repo)
            self.assertEqual(bullets, [
                "- beta",
                "- alpha again",
                "  kept on the next line.",
            ])
            section = "## 0.3.0 (2026-10-06)\n\n" + "\n".join(bullets) + "\n"
            held = "".join(
                (repo / name).read_text()
                for name in ("changes/second.md", "changes/first.md")
            )
            self.assertEqual(section, "## 0.3.0 (2026-10-06)\n\n" + held)
            self.assertNotIn("- alpha\n", section)
            self.assertIn("  kept on the next line.\n", section)
            (repo / "CHANGELOG.md").write_text("# Changelog\n\n" + section)
            _git(repo, "add", "CHANGELOG.md")
            _git(repo, "rm", "-q", "changes/first.md", "changes/second.md")
            _git(repo, "commit", "-qm", "0.3.0")
            self.assertEqual(
                _git(repo, "show", "HEAD^:changes/first.md"),
                "- alpha again\n  kept on the next line.\n",
            )
            self.assertEqual(
                _git(repo, "show", "HEAD:CHANGELOG.md"),
                "# Changelog\n\n## 0.3.0 (2026-10-06)\n\n"
                "- beta\n- alpha again\n  kept on the next line.\n",
            )
            names = [
                line for line in _git(
                    repo, "diff-tree", "--no-commit-id", "--name-status",
                    "--no-renames", "-r", "HEAD",
                ).splitlines()
                if line
            ]
            self.assertEqual(names, [
                "A\tCHANGELOG.md",
                "D\tchanges/first.md",
                "D\tchanges/second.md",
            ])
            self.assertEqual(release_bullets(repo), [])
