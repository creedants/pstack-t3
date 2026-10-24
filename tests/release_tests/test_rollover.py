if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import unittest

from .support import (
    setUpModule,
    ReleaseCase,
    ENV,
    git,
    commit,
    run,
    state,
    repository,
    release,
)


def big_changelog():
    bullets = "".join(f"- bullet {i} {'x' * 80}\n" for i in range(300))
    return "# Changelog\n\n## 0.1.0 (2026-10-06)\n\n" + bullets


class RolloverTest(ReleaseCase):
    def test_rollover_prints_the_section_created_files_and_the_archive_git_add(self):
        with repository(changelog=big_changelog()) as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            result = run(repo, "0.1.1")
            section = "## 0.1.1 (2026-10-09)\n\n### Changed\n\n- a\n"
            steps = (
                "this script never stages, commits, tags, or pushes. the remaining steps:\n"
                "  git add CHANGELOG.md changes/ docs/changelog/\n"
                '  git commit -m "release 0.1.1"\n'
                '  git tag -a v0.1.1 -m "pstack-t3 0.1.1"    # on the commit that lands on main\n'
                "  git push origin v0.1.1\n"
                "  gh release create v0.1.1 --notes-file <release notes file>\n"
            )
            expected = (
                section
                + "\ncreated docs/changelog/0.1.0.md\n"
                + "created docs/changelog/0.1.0-2.md\n"
                + "wrote this section to CHANGELOG.md\n"
                + "deleted changes/a.md\n"
                + steps
            )
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, expected, ""))
            self.assertTrue((repo / "docs/changelog/0.1.0.md").is_file())
            self.assertTrue((repo / "docs/changelog/0.1.0-2.md").is_file())
            self.assertIn("## Archive", (repo / "CHANGELOG.md").read_text())
            self.assertIn("## 0.1.1 (2026-10-09)", (repo / "CHANGELOG.md").read_text())

    def test_rollover_dry_run_prints_would_create_and_changes_nothing(self):
        with repository(changelog=big_changelog()) as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            before = state(repo)
            result = run(repo, "0.1.1", "--dry-run")
            section = "## 0.1.1 (2026-10-09)\n\n### Changed\n\n- a\n"
            steps = (
                "this script never stages, commits, tags, or pushes. the remaining steps:\n"
                "  git add CHANGELOG.md changes/ docs/changelog/\n"
                '  git commit -m "release 0.1.1"\n'
                '  git tag -a v0.1.1 -m "pstack-t3 0.1.1"    # on the commit that lands on main\n'
                "  git push origin v0.1.1\n"
                "  gh release create v0.1.1 --notes-file <release notes file>\n"
            )
            expected = (
                section
                + "\nwould create docs/changelog/0.1.0.md\n"
                + "would create docs/changelog/0.1.0-2.md\n"
                + "would write this section to CHANGELOG.md\n"
                + "would delete changes/a.md\n"
                + "nothing changed\n"
                + steps
            )
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, expected, ""))
            self.assertEqual(state(repo), before)

    def test_rollover_keeps_every_file_under_the_token_limit(self):
        with repository(changelog=big_changelog()) as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            self.assertEqual(run(repo, "0.1.1").returncode, 0)
            self.assertTrue(release.fits((repo / "CHANGELOG.md").read_text()))
            for path in sorted((repo / "docs/changelog").iterdir()):
                self.assertTrue(release.fits(path.read_text()), path.name)

    def test_duplicate_version_in_the_archive_is_refused(self):
        with repository() as repo:
            commit(repo, {"docs/changelog/0.3.0.md": "## 0.3.0 (2026-10-06)\n\n- old\n"}, day=2)
            commit(repo, {"changes/a.md": "- a\n"}, day=3)
            before = state(repo)
            result = run(repo, "0.3.0")
            self.assertEqual(
                (result.returncode, result.stdout, result.stderr),
                (1, "", "docs/changelog already has a ## 0.3.0 heading; pick the next version\n"),
            )
            self.assertEqual(state(repo), before)

    def test_version_rule_uses_the_archived_maximum(self):
        with repository() as repo:
            commit(repo, {"docs/changelog/0.5.0.md": "## 0.5.0 (2026-10-06)\n\n- old\n"}, day=2)
            commit(repo, {"changes/a.md": "- a\n"}, day=3)
            before = state(repo)
            result = run(repo, "0.2.1")
            self.assertEqual(
                (result.returncode, result.stdout, result.stderr),
                (
                    1,
                    "",
                    "0.2.1 is not above 0.5.0, the latest version in CHANGELOG.md and docs/changelog/; "
                    "pick a higher version\n",
                ),
            )
            self.assertEqual(state(repo), before)


if __name__ == "__main__":
    unittest.main()
