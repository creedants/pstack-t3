import importlib.util
import unittest
from pathlib import Path

from .support import (
    setUpModule,
    ReleaseCase,
    ROOT,
    release,
)


class FitsTest(ReleaseCase):
    def test_fits_accepts_a_text_at_the_token_limit(self):
        self.assertTrue(release.fits("a" * 20000))
        self.assertTrue(release.fits("a" * 20003))

    def test_fits_refuses_a_text_past_the_token_limit(self):
        self.assertFalse(release.fits("a" * 20004))


class PartsTest(ReleaseCase):
    def test_parts_returns_one_part_when_the_section_fits(self):
        section = "## 0.1.0 (2026-10-09)\n\n- a\n"
        self.assertEqual(release.parts(section), (section,))

    def test_parts_splits_at_bullet_boundaries(self):
        bullets = "".join(f"- bullet {i} {'x' * 80}\n" for i in range(300))
        section = "## 0.2.0 (2026-10-06)\n\n" + bullets
        self.assertFalse(release.fits(section))
        got = release.parts(section)
        self.assertGreater(len(got), 1)
        self.assertTrue(got[0].startswith("## "))
        for part in got:
            self.assertTrue(release.fits(part))
            self.assertIn("- bullet", part)
        self.assertEqual("".join(got), section)

    def test_parts_keeps_a_continuation_line_with_its_bullet(self):
        bullets = "".join(f"- bullet {i} {'x' * 80}\n  continued {i}\n" for i in range(300))
        section = "## 0.2.0 (2026-10-06)\n\n" + bullets
        got = release.parts(section)
        self.assertGreater(len(got), 1)
        for part in got:
            self.assertTrue(release.fits(part))
        self.assertEqual("".join(got), section)
        for part in got[1:]:
            self.assertTrue(part.startswith("- "))


class ReadShelfTest(ReleaseCase):
    def test_read_shelf_returns_an_empty_shelf_for_no_files(self):
        self.assertEqual(release.read_shelf({}), {})

    def test_read_shelf_groups_parts_into_one_shelved_version(self):
        first = "## 0.1.0 (2026-10-06)\n\n- a\n"
        second = "- b\n- c\n"
        files = {"docs/changelog/0.1.0.md": first, "docs/changelog/0.1.0-2.md": second}
        shelf = release.read_shelf(files)
        self.assertEqual(set(shelf), {(0, 1, 0)})
        she = shelf[(0, 1, 0)]
        self.assertEqual(she.version, (0, 1, 0))
        self.assertEqual(she.heading, "## 0.1.0 (2026-10-06)")
        self.assertEqual(she.parts, (first, second))

    def test_read_shelf_refuses_an_orphan_gap(self):
        files = {"docs/changelog/0.1.0-2.md": "- b\n"}
        with self.assertRaises(SystemExit) as raised:
            release.read_shelf(files)
        self.assertIn("is an orphan", str(raised.exception))

    def test_read_shelf_refuses_a_bad_name(self):
        with self.assertRaises(SystemExit) as raised:
            release.read_shelf({"docs/changelog/notes.md": "- a\n"})
        self.assertIn("is an orphan", str(raised.exception))


class ShelveTest(ReleaseCase):
    def test_shelve_returns_splice_unchanged_when_everything_fits(self):
        changelog = "# Changelog\n\n## 0.1.0 (2026-10-06)\n\n- a\n"
        section = "## 0.2.0 (2026-10-09)\n\n### Changed\n\n- b\n"
        got = release.shelve(section, changelog, {})
        self.assertEqual(got.changelog, release.splice(changelog, section))
        self.assertEqual(got.created, {})
        self.assertEqual(got.moved, ())

    def test_shelve_refuses_a_hand_edited_archive_block(self):
        changelog = "# Changelog\n\n## 0.1.0 (2026-10-06)\n\n- a\n\n## Archive\n\n- [0.0.1](docs/changelog/0.0.1.md)\n"
        with self.assertRaises(SystemExit) as raised:
            release.shelve(None, changelog, {})
        self.assertIn("Archive block does not match", str(raised.exception))

    def test_shelve_refuses_an_orphan_version_still_in_the_changelog(self):
        section = "## 0.1.0 (2026-10-06)\n\n- a\n"
        changelog = "# Changelog\n\n" + section + "\n## Archive\n\n- [0.1.0](docs/changelog/0.1.0.md)\n"
        shelf = {(0, 1, 0): release.Shelved((0, 1, 0), "## 0.1.0 (2026-10-06)", (section,))}
        with self.assertRaises(SystemExit) as raised:
            release.shelve(None, changelog, shelf)
        self.assertIn("is an orphan", str(raised.exception))

    def test_shelve_archives_the_oldest_until_the_render_fits(self):
        big = "".join(f"- bullet {i} {'x' * 80}\n" for i in range(300))
        old = "## 0.1.0 (2026-10-06)\n\n" + big
        changelog = "# Changelog\n\n" + old
        section = "## 0.2.0 (2026-10-09)\n\n### Changed\n\n- b\n"
        got = release.shelve(section, changelog, {})
        self.assertTrue(release.fits(got.changelog))
        self.assertIn("## Archive", got.changelog)
        self.assertTrue(got.created)
        self.assertEqual(len(got.moved), 1)
        self.assertEqual(got.moved[0].version, (0, 1, 0))
        self.assertEqual("".join(got.moved[0].parts), old)

    def test_shelve_migration_is_a_fixed_point(self):
        big = "".join(f"- bullet {i} {'x' * 80}\n" for i in range(300))
        old = "## 0.1.0 (2026-10-06)\n\n" + big
        changelog = "# Changelog\n\n" + old
        first = release.shelve(None, changelog, {})
        self.assertTrue(first.created)
        shelf = release.read_shelf(first.created)
        second = release.shelve(None, first.changelog, shelf)
        self.assertEqual(second.changelog, first.changelog)
        self.assertEqual(second.created, {})
        self.assertEqual(second.moved, ())


class LimitTest(ReleaseCase):
    def test_limit_tokens_equals_the_sizes_module_value(self):
        spec = importlib.util.spec_from_file_location(
            "pstack_sizes", ROOT / "t3/added/landing/scripts/landing_lib/sizes.py")
        sizes = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sizes)
        self.assertEqual(release.LIMIT_TOKENS, sizes.DEFAULT_MAX_TOKENS)
        self.assertEqual(release.LIMIT_TOKENS, 5000)
        self.assertEqual(release.ARCHIVE, "docs/changelog")
        self.assertEqual(release.ARCHIVE_HEADING, "## Archive")


if __name__ == "__main__":
    unittest.main()
