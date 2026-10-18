import shlex
import unittest
from unittest import mock
from .support import setUpModule, ReleaseCase, git, commit, run, state, repository, release
class OversizeNewTest(ReleaseCase):
  def test_oversize_new_section_archives_itself(self):
    with repository(changelog="# Changelog\n") as repo:
      bullets = "".join(f"- bullet {i} {'x' * 80}\n" for i in range(300))
      commit(repo, {"changes/a.md": bullets}, day=2)
      with mock.patch.object(release, "ROOT", repo):
        cut = release.plan("0.1.0", "2026-10-09")
        expected = cut.section
      self.assertFalse(release.fits(expected))
      before = state(repo)
      result = run(repo, "0.1.0")
      self.assertEqual(result.returncode, 0)
      changelog_text = (repo / "CHANGELOG.md").read_text()
      self.assertIn("## Archive", changelog_text)
      self.assertTrue(release.fits(changelog_text))
      self.assertIn("- [0.1.0](docs/changelog/0.1.0.md)", changelog_text)
      rebased = release.rebase_links(expected)
      files = {}
      for p in (repo / "docs/changelog").glob("*.md"):
        files[p.name] = p.read_text()
      ordered = [files[f"0.1.0{'' if i == 1 else f'-{i}'}.md"] for i in range(1, len(files) + 1) if f"0.1.0{'' if i == 1 else f'-{i}'}.md" in files]
      self.assertTrue(ordered)
      for part in ordered:
        self.assertTrue(release.fits(part))
      self.assertEqual("".join(ordered), rebased)
      for cmd in release.ARCHIVE_UNDO.split(" && "):
        parts = shlex.split(cmd)
        git(repo, *parts[1:])
      self.assertEqual(state(repo), before)
if __name__ == "__main__":
  unittest.main()
