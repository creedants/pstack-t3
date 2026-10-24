if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import REAL_FOLLOW_UPS, TRACKED, os

class QuotedPathsMixin:
    def test_from_report_records_the_tracked_paths_each_real_follow_up_quotes(self):
        self.tracked_project(*TRACKED)
        self.fired_bug_fix()
        printed = self.follow_ups_in("## Follow-ups\n\n" + "".join(f"- {text}\n" for text, _ in REAL_FOLLOW_UPS))
        first, *_ = REAL_FOLLOW_UPS
        lines = printed.splitlines()
        self.assertEqual(lines[0], f"T2 added from perf/reports/D1.md#1: {first[0]}")
        index = 1
        for number, (text, _) in enumerate(REAL_FOLLOW_UPS[1:], 2):
            self.assertEqual(lines[index], f"not filed, D1.md takes one ticket per item: {text}")
            index += 1
            self.assertTrue(lines[index].startswith("noted "), lines[index])
            self.assertIn(f"perf/reports/D1.md#{number}", lines[index])
            self.assertTrue(lines[index].endswith(f": {text}"), lines[index])
            index += 1
            while index < len(lines) and lines[index].startswith("noted ") and f"perf/reports/D1.md#{number}" in lines[index]:
                index += 1
        self.assertEqual(index, len(lines))
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": first[1]})
        rows = (self.at / "notes.tsv").read_text().splitlines()[1:]
        self.assertEqual(len(rows), len(REAL_FOLLOW_UPS) - 1 + sum(1 for _, paths in REAL_FOLLOW_UPS[1:] if not paths))

    def test_from_report_records_the_directory_before_the_first_marked_component_of_a_pattern_that_matches(self):
        self.tracked_project(*TRACKED)
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n"
                           "- Correctness: Reword `t3/**/*.md` and `tests/test_*.py`.\n"
                           "- Safety: Nothing matches `docs/*.rst`, `**` or `*.md` has no directory, and `src/*.py` is not tracked.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "t3,tests"})

    def test_from_report_records_the_paths_quoted_beside_a_double_backtick_code_span(self):
        self.tracked_project("src/a/one.py", "src/b/two.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n"
                           "- Correctness: Replace ``with `x` `` in `src/a/one.py` and `src/b/two.py`\n"
                           "- Safety: Change ``rows[0]`` in `src/a/one.py`\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "src/a/one.py,src/b/two.py"})

    def test_from_report_skips_absolute_dot_dot_repository_and_non_numeric_suffix_quotes_and_records_the_normalized_path_of_a_line_suffixed_or_directory_quote(self):
        self.tracked_project(*TRACKED)
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n- Correctness: See `../README.md`, `/README.md`, `.`, `docs/../README.md`, "
                           "and `docs/guide.md:x`.\n- Safety: Fix `./docs//guide.md:7` and `docs/`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": ""})

    def test_from_report_records_no_paths_when_the_project_is_not_a_git_repository(self):
        (self.project / "docs").mkdir()
        (self.project / "docs/guide.md").write_text("x\n")
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Correctness: Fix `docs/guide.md`.\n"),
                         "T2 added from perf/reports/D1.md#1: Correctness: Fix `docs/guide.md`.")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": ""})

    def test_from_report_records_a_quoted_path_in_a_repository_that_tracks_a_file_name_that_is_not_utf_8(self):
        try:
            with open(os.fsencode(self.project) + b"/\xff.md", "wb") as handle:
                handle.write(b"x\n")
        except OSError:
            self.skipTest("the file system refuses a file name that is not UTF-8")
        self.tracked_project("guide.md")
        self.fired_bug_fix()
        self.assertEqual(self.follow_ups_in("## Follow-ups\n\n- Correctness: Fix `guide.md`.\n"),
                         "T2 added from perf/reports/D1.md#1: Correctness: Fix `guide.md`.")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "guide.md"})

    def test_from_report_dry_run_in_a_git_project_changes_no_store_file(self):
        self.tracked_project(*TRACKED)
        self.fired_bug_fix()
        self.review_file("D1.md", "## Follow-ups\n\n- Correctness: Fix `docs/guide.md`.\n")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "add", "--from-report", "reports/D1.md", "--dry-run"),
                         "would add from perf/reports/D1.md#1: Correctness: Fix `docs/guide.md`.")
        self.assertEqual(self.store_files(), before)

    def test_from_report_records_the_tracked_file_for_each_quoted_file_name_with_no_directory_that_resolves_to_one_and_none_for_names_with_none_or_several(self):
        self.tracked_project("t3/added/x/scripts/tool.py", "skills/x/scripts/tool.py", "src/app/one.py", "lib/two.py",
                             "src/two.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n"
                           "- Correctness: Fix `tool.py:7` and `one.py`.\n"
                           "- Safety: Fix `two.py` and `nope.py`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "src/app/one.py,t3/added/x/scripts/tool.py"})

    def test_from_report_records_nothing_for_a_file_name_only_one_nested_file_has_quoted_with_a_dot_slash_before_it_a_slash_after_it_or_spaces_or_tabs_around_it(self):
        self.tracked_project("src/app/tool.py", "tool.py:12")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n"
                           "- Correctness: One `./tool.py`.\n"
                           "- Safety: Two `tool.py/`.\n"
                           "- Privacy: Three `./tool.py:12`.\n"
                           "- Correctness: Four ` tool.py `.\n"
                           "- Correctness: Five `\ttool.py\t`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": ""})

    def test_from_report_records_nothing_for_a_quote_that_is_a_tracked_file_named_as_one_nested_file_with_a_line_number_added(self):
        self.tracked_project("tool.py:12", "src/app/tool.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n- Correctness: Fix `tool.py:12`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": ""})

    def test_from_report_records_nothing_for_a_quote_that_is_a_tracked_directory_named_as_one_nested_file_with_a_line_number_added(self):
        self.tracked_project("tool.py:12/notes.md", "src/app/tool.py")
        self.fired_bug_fix()
        self.follow_ups_in("## Follow-ups\n\n- Correctness: Fix `tool.py:12`.\n")
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": ""})
