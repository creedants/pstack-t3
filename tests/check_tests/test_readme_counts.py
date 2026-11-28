if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from .support import setUpModule, check, tests_tree


class SkillCountCheckTest(unittest.TestCase):
    _Q = "`doctor` should show `{} pstack-t3` for each provider you use.\n"

    def _tree(self, guide, skills=(), readme=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "pstack-runtime/scripts").mkdir(parents=True)
        (root / "pstack-runtime/scripts/roles.py").write_text("")
        for name in skills:
            skill = root / name
            skill.mkdir()
            (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n\nbody\n")
        document = root / "docs/guide.md"
        document.parent.mkdir()
        document.write_text(guide)
        patcher = mock.patch.object(check, "GUIDE_PATH", document)
        patcher.start()
        self.addCleanup(patcher.stop)
        if readme is None:
            readme = f"See all {len(skills)} skills and every playbook.\n"
        self._readme_states(root, readme)
        return root

    def _readme_states(self, root, readme):
        document = root / "README.md"
        document.write_text(readme)
        patcher = mock.patch.object(check, "README_PATH", document)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_guide_that_quotes_the_shipped_count_is_quiet(self):
        root = self._tree(self._Q.format("3/3"), ("a", "b", "c"))
        self.assertEqual(check.check_tree(root), [])

    def test_a_stale_count_is_named_with_the_line_the_quote_and_the_shipped_count(self):
        root = self._tree("intro\n" + self._Q.format("2/2"), ("a", "b", "c"))
        self.assertEqual(check.check_tree(root), [
            "docs/guide.md:2: quotes the doctor count 2/2, and the shipped skill count is 3",
        ])

    def test_a_quote_whose_two_numbers_differ_is_named(self):
        root = self._tree(self._Q.format("3/4"), ("a", "b", "c"))
        self.assertEqual(check.check_tree(root), [
            "docs/guide.md:1: quotes the doctor count 3/4, and the shipped skill count is 3",
        ])

    def test_a_guide_that_quotes_no_count_is_named(self):
        root = self._tree("no count here\n", ("a",))
        self.assertEqual(check.check_tree(root), [
            "docs/guide.md: quotes no doctor count, and the shipped skill count is 1",
        ])

    def test_a_guide_that_cannot_be_read_is_named(self):
        root = self._tree(self._Q.format("1/1"), ("a",))
        (root / "docs/guide.md").unlink()
        self.assertEqual(check.check_tree(root), [
            "docs/guide.md: could not be read, so its doctor count was not checked: FileNotFoundError",
        ])

    def test_an_explicit_tree_argument_gets_no_guide_finding(self):
        root = self._tree("intro\n" + self._Q.format("2/2"), skills=("a", "b"))
        (root / "pstack-runtime/SKILL.md").write_text(
            "---\nname: pstack-runtime\ndescription: d\n---\n\n"
            "Call `watch_pull_request`. Call `unwatch_pull_request`.\n"
        )
        tests = tests_tree(root, {})
        output = io.StringIO()
        with mock.patch.object(check, "TESTS_DIR", tests), contextlib.redirect_stdout(output):
            status = check.main([str(root)])
        self.assertEqual((status, output.getvalue()), (0, "ok\n"))

    def test_a_readme_that_states_the_shipped_count_is_quiet(self):
        root = self._tree(self._Q.format("2/2"), skills=("a", "b"))
        self.assertEqual(check.check_tree(root), [])

    def test_a_stale_readme_count_is_named_with_the_line_the_stated_count_and_the_shipped_count(self):
        root = self._tree(self._Q.format("3/3"), readme="See all 60 skills and every playbook.\n", skills=("a", "b", "c"))
        self.assertEqual(check.check_tree(root), [
            "README.md:1: states all 60 skills, and the shipped skill count is 3",
        ])

    def test_a_readme_that_states_no_skill_count_is_named(self):
        root = self._tree(self._Q.format("1/1"), skills=("a",))
        self._readme_states(root, "no count here\n")
        self.assertEqual(check.check_tree(root), [
            "README.md: states no skill count, and the shipped skill count is 1",
        ])

    def test_a_readme_that_cannot_be_read_is_named(self):
        root = self._tree(self._Q.format("1/1"), skills=("a",))
        (root / "README.md").unlink()
        self.assertEqual(check.check_tree(root), [
            "README.md: could not be read, so its skill count was not checked: FileNotFoundError",
        ])

    def test_an_explicit_tree_argument_gets_no_readme_count_finding(self):
        root = self._tree("intro\n" + self._Q.format("2/2"), readme="See all 60 skills and every playbook.\n", skills=("a", "b"))
        (root / "pstack-runtime/SKILL.md").write_text(
            "---\nname: pstack-runtime\ndescription: d\n---\n\n"
            "Call `watch_pull_request`. Call `unwatch_pull_request`.\n"
        )
        tests = tests_tree(root, {})
        output = io.StringIO()
        with mock.patch.object(check, "TESTS_DIR", tests), contextlib.redirect_stdout(output):
            status = check.main([str(root)])
        self.assertEqual((status, output.getvalue()), (0, "ok\n"))


class SkillCountWriteTest(unittest.TestCase):
    def _skills(self, names):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for name in names:
            (root / name).mkdir()
            (root / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n\nbody\n")
        return root

    def test_write_guide_count_rewrites_stale_and_mismatched_quotes_only(self):
        root = self._skills(("a", "b", "c"))
        guide = root / "docs/guide.md"
        guide.parent.mkdir()
        raw = "Guide · 2/2 stays.\r\n`03/03 pstack-t3`\r\n`03/3 pstack-t3`\r\n`2/2 pstack-t3`\r\n`1/9 pstack-t3` end".encode("utf-8")
        want = "Guide · 2/2 stays.\r\n`03/03 pstack-t3`\r\n`3/3 pstack-t3`\r\n`3/3 pstack-t3`\r\n`3/3 pstack-t3` end".encode("utf-8")
        guide.write_bytes(raw)
        self.assertTrue(check.write_guide_count(guide, root))
        self.assertEqual(guide.read_bytes(), want)
        self.assertEqual(check.guide_count_findings(guide, root), [])

    def test_write_readme_count_rewrites_stale_phrase_and_keeps_surrounding_bytes(self):
        root = self._skills(("a", "b", "c"))
        readme = root / "README.md"
        raw = "· All 2 skills, recall 2 skills, all 2 skillsets.\r\nall 03 skills\r\nall 2 skills\r\nall 2 skills end".encode("utf-8")
        want = "· All 2 skills, recall 2 skills, all 2 skillsets.\r\nall 03 skills\r\nall 3 skills\r\nall 3 skills end".encode("utf-8")
        readme.write_bytes(raw)
        self.assertTrue(check.write_readme_count(readme, root))
        self.assertEqual(readme.read_bytes(), want)
        self.assertEqual(check.readme_count_findings(readme, root), [])

    def test_write_count_leaves_already_matching_files_unwritten(self):
        root = self._skills(("a", "b"))
        guide, readme = root / "docs/guide.md", root / "README.md"
        guide.parent.mkdir()
        stamp = 1_000_000
        cases = (
            ("exact", b"`2/2 pstack-t3`\n", b"all 2 skills\n"),
            ("leading-zero", b"`02/02 pstack-t3`\n", b"all 02 skills\n"),
        )
        for label, g_raw, r_raw in cases:
            with self.subTest(label):
                guide.write_bytes(g_raw)
                readme.write_bytes(r_raw)
                for path in (guide, readme):
                    os.utime(path, (stamp, stamp))
                self.assertEqual((check.guide_count_findings(guide, root), check.readme_count_findings(readme, root)), ([], []))
                self.assertFalse(check.write_guide_count(guide, root))
                self.assertFalse(check.write_readme_count(readme, root))
                self.assertEqual((guide.read_bytes(), readme.read_bytes()), (g_raw, r_raw))
                self.assertEqual((guide.stat().st_mtime_ns, readme.stat().st_mtime_ns), (stamp * 10**9, stamp * 10**9))

    def test_write_count_leaves_unquoted_and_unreadable_files_alone_for_findings(self):
        root = self._skills(("a", "b"))
        guide, readme = root / "docs/guide.md", root / "README.md"
        guide.parent.mkdir()
        stamp = 1_000_000
        wg, fg = check.write_guide_count, check.guide_count_findings
        wr, fr = check.write_readme_count, check.readme_count_findings
        cases = (
            ("unquoted guide", guide, wg, fg, b"no count\n", ("quotes no doctor count",)),
            ("unstated readme", readme, wr, fr, b"no count\n", ("states no skill count",)),
            ("missing guide", guide, wg, fg, None, ("could not be read",)),
            ("missing readme", readme, wr, fr, None, ("could not be read",)),
            ("bad utf-8 guide", guide, wg, fg, b"\xff\xfe", ("could not be read", "UnicodeDecodeError")),
            ("bad utf-8 readme", readme, wr, fr, b"\xff\xfe", ("could not be read", "UnicodeDecodeError")),
        )
        for label, path, writer, finder, raw, snippets in cases:
            with self.subTest(label):
                if raw is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(raw)
                    os.utime(path, (stamp, stamp))
                self.assertFalse(writer(path, root))
                if raw is None:
                    self.assertFalse(path.exists())
                else:
                    self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), (raw, stamp * 10**9))
                findings = "\n".join(finder(path, root))
                for snippet in snippets:
                    self.assertIn(snippet, findings)
