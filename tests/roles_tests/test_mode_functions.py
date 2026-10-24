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
from .support import CANONICAL_CORPUS, WAIVER_ROWS, land, roles, setUpModule


class ModeFunctionTest(unittest.TestCase):
    def test_light_waivers_table(self):
        expected = {}
        for playbook, (first, retry) in WAIVER_ROWS.items():
            expected[(playbook, "first")] = first
            expected[(playbook, "fix")] = retry
            expected[(playbook, "bounce")] = retry
        self.assertEqual(dict(roles.LIGHT_WAIVERS), expected)
        self.assertEqual(list(roles.LIGHT_WAIVERS), list(expected))
        self.assertEqual(roles.LIGHT_WAIVERS[("investigation", "fix")], ())
        with self.assertRaises(KeyError):
            roles.LIGHT_WAIVERS[("feature", "retry")]

    def same_result(self, left, right, left_error, right_error, *args):
        try:
            left_value = left(*args)
            left_raised = None
        except left_error as error:
            left_value = None
            left_raised = error
        try:
            right_value = right(*args)
            right_raised = None
        except right_error as error:
            right_value = None
            right_raised = error
        if left_raised or right_raised:
            self.assertIsInstance(left_raised, left_error)
            self.assertIsInstance(right_raised, right_error)
            self.assertEqual(str(left_raised), str(right_raised))
        else:
            self.assertEqual(left_value, right_value)

    def test_canonical_matches_land(self):
        for path in CANONICAL_CORPUS:
            for kind in ("lease", "escalate pattern"):
                with self.subTest(path=path, kind=kind):
                    self.same_result(
                        roles.canonical, land.canonical, roles.ModeSettingsError, land.LandError, path, kind,
                    )
        for text in ("a,,b", "./a//b/,a/b", ""):
            with self.subTest(text=text):
                self.same_result(
                    roles.lease_paths, land.wanted_paths, roles.ModeSettingsError, land.LandError, text,
                )

    def test_canonical_literals(self):
        self.assertEqual(roles.canonical("./a//b/"), "a/b")
        self.assertEqual(roles.canonical("a/../b"), "b")
        self.assertEqual(roles.canonical("/../x"), "x")
        self.assertEqual(roles.canonical("."), "")

    def test_segments_match(self):
        self.assertTrue(roles.segments_match("**/migrations/**", "db/migrations/0001.sql"))
        self.assertFalse(roles.segments_match("**/migrations/**", "docs/migrations.md"))
        self.assertFalse(roles.segments_match("scripts/*.py", "scripts/tools/x.py"))
        self.assertTrue(roles.segments_match("**", ""))
