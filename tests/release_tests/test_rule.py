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

from .support import release, setUpModule, test_pstack_t3


class RuleTest(unittest.TestCase):
    def test_bullet_fault(self):
        line = "line {} is not a bullet or a continuation line".format
        cases = (
            (["- a"], None),
            (["- a", "  b"], None),
            (["- a", "\tb"], None),
            (["- a", "- b", "  c"], None),
            ([], "holds no bullet"),
            (["prose"], line(1)),
            (["- "], line(1)),
            (["-a"], line(1)),
            (["  b"], line(1)),
            (["- a", ""], line(2)),
            (["- a", "b"], line(2)),
            (["- a", "  "], line(2)),
            (["- a", "  b", "c"], line(3)),
        )
        for lines, fault in cases:
            with self.subTest(lines=lines):
                self.assertEqual(release.bullet_fault(lines), fault)
                self.assertEqual(test_pstack_t3.fragment_holds_bullets(lines), fault is None)
        self.assertEqual(release.bullet_fault(["- a\r"]), line(1))
        self.assertEqual(release.bullet_fault(["- a", "  b\r"]), line(2))

    def test_splice(self):
        section = "## 0.3.0 (2026-10-09)\n\n- a\n"
        cases = (
            ("# Changelog\n", "# Changelog\n\n" + section),
            ("", section),
            ("# C\n\n## 0.1.0\n", "# C\n\n" + section + "\n## 0.1.0\n"),
            ("# C\n\n## Unreleased\n\n## 0.1.0\n", "# C\n\n" + section + "\n## Unreleased\n\n## 0.1.0\n"),
            ("## 0.1.0\n\n- old\n", section + "\n## 0.1.0\n\n- old\n"),
        )
        for changelog, spliced in cases:
            with self.subTest(changelog=changelog):
                self.assertEqual(release.splice(changelog, section), spliced)

    def test_released(self):
        changelog = (
            "# Changelog\n\n## 0.3.0 (tomorrow)\n\n## Unreleased\n\n## 0.10.0 (2026-10-06)\n\n"
            "## 0.2.0-rc1\n\n### 0.4.0\n\n## 0.1.0\n\n- ## 9.9.9\n"
        )
        self.assertEqual(release.released(changelog), [(0, 3, 0), (0, 10, 0), (0, 1, 0)])
        self.assertEqual(release.released("# Changelog\n"), [])
