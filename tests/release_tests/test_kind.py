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
    HEADING,
    SECTION,
    STORE_KINDS,
    UPKEEP,
    ReleaseCase,
    commit,
    history,
    release,
    repository,
    run,
    setUpModule,
)


class KindTest(ReleaseCase):
    """A fragment's kind, the group each kind's bullets go under, and the bump the kinds give."""

    def assert_groups(self, fragments, section, version):
        with repository() as repo:
            commit(repo, {f"changes/{name}": text for name, text in fragments.items()}, day=2)
            result = run(repo, version, "--dry-run")
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(result.stdout[: len(section) + 1], section + "\n")

    def test_a_kind_marker_groups_each_kinds_bullets_and_leaves_the_marker_out(self):
        self.assert_groups(
            {"a.md": "- kind feature\n- a one\n  a continued\n", "b.md": "- kind fix\n- b\n", "c.md": "- kind polish\n- c\n",
             "d.md": "- kind study\n- d\n", "e.md": "- e\n"},
            "## 0.3.0 (2026-10-09)\n\n### New\n\n- a one\n  a continued\n\n### Fixed\n\n- b\n\n### Changed\n\n- e\n\n- c\n- d\n",
            "0.3.0",
        )

    def test_an_unlabelled_fragment_is_upkeep_and_a_group_with_no_bullet_is_absent(self):
        self.assert_groups({"a.md": "- a\n"}, "## 0.2.1 (2026-10-09)\n\n### Changed\n\n- a\n", "0.2.1")

    def test_a_first_bullet_that_starts_with_kind_and_names_no_kind_is_refused(self):
        for text in ("- kind\n", "- kind chore\n", "- kind feature extra\n", "- kinds\n"):
            with self.subTest(text=text), repository() as repo:
                commit(repo, {"changes/note.md": text + "- a\n"}, day=2)
                self.assert_refuses(
                    repo, "0.2.1",
                    "changes/note.md line 1 names a kind that is not feature, fix, polish, upkeep, or study; "
                    "fix the fragment, commit it, and rerun\n",
                )

    def test_a_fragment_whose_marker_is_its_only_line_holds_no_bullet(self):
        with repository() as repo:
            commit(repo, {"changes/note.md": "- kind feature\n"}, day=2)
            self.assert_refuses(
                repo, "0.3.0",
                "changes/note.md holds no bullet; fix the fragment, commit it, and rerun\n",
            )

    def test_the_kind_marker_is_removed_before_the_bullet_fault_counts_lines(self):
        with repository() as repo:
            commit(repo, {"changes/note.md": "- kind fix\n- ok\nprose\n"}, day=2)
            self.assert_refuses(
                repo, "0.2.1",
                "changes/note.md line 2 is not a bullet or a continuation line; fix the fragment, commit it, and rerun\n",
            )

    def test_a_feature_in_the_release_takes_the_next_minor_with_no_patch(self):
        with history() as repo:
            commit(repo, {"changes/one.md": "- kind feature\n- one\n"}, day=5)
            self.assert_refuses(
                repo, "0.2.1",
                "0.2.1 is not 0.3.0, the version the kinds give; pass 0.3.0\n",
            )
            self.assert_section(repo, "## 0.3.0 (2026-10-09)\n\n### New\n\n- one\n\n### Changed\n\n" + SECTION[len(HEADING + UPKEEP):],
                               "0.3.0")

    def test_no_feature_takes_the_next_patch_over_the_latest(self):
        with history() as repo:
            commit(repo, {"changes/one.md": "- kind fix\n- one\n"}, day=5)
            self.assert_refuses(
                repo, "0.3.0",
                "0.3.0 is not 0.2.1, the version the kinds give; pass 0.2.1\n",
            )
            self.assert_section(repo, HEADING + "### Fixed\n\n- one\n\n" + UPKEEP + SECTION[len(HEADING + UPKEEP):])

    def test_the_kind_tuple_is_the_one_the_store_holds(self):
        self.assertEqual(release.KINDS, STORE_KINDS)
