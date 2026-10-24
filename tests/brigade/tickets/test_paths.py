if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import TRACKED

class PathsMixin:
    def test_a_second_ticket_paths_run_changes_no_store_file(self):
        self.unrecorded_tickets()
        self.brigade("ticket", "paths")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "paths"), "T2 quotes no tracked path")
        self.assertEqual(self.store_files(), before)

    def test_fire_directory_holding_a_skill_file_names_that_file(self):
        case = self.fake_land()
        (case / "claim-proceed").touch()
        self.open()
        self.seed_doc_settings()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Long skill work")
        before = self.store_files()
        self.assertEqual(self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Skill",
                                      "--paths", "t3/added/brigade", "--timebox", "120", ok=False),
                         self.doc_line(("t3/added/brigade/SKILL.md",), timebox=120))
        self.assertEqual(self.store_files(), before)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Scripts work")
        proc = self.fire_with_fake_land(case, "--tickets", "T2", "--station", "bug-fix", "--summary", "Scripts",
                                        "--paths", "t3/added/brigade/scripts", "--timebox", "120")
        out, err = proc.communicate(timeout=20)
        self.assertEqual((proc.returncode, err.strip()), (0, ""))
        self.assertTrue(out.strip().startswith("D1"), out)

    def test_ticket_paths_dry_run_prints_the_one_tracked_file_that_has_a_quoted_file_name_and_changes_no_store_file(self):
        self.tracked_project("src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "paths", "--dry-run", owner=False), "T1 would record src/app/tool.py")
        self.assertEqual(self.store_files(), before)

    def test_ticket_paths_dry_run_prints_what_it_would_record_and_changes_no_store_file(self):
        self.unrecorded_tickets()
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "paths", "--dry-run", owner=False),
                         "T1 would record docs/guide.md,tests/test_install.py\nT2 quotes no tracked path")
        self.assertEqual(self.store_files(), before)

    def test_ticket_paths_records_a_file_under_skills_for_a_quoted_file_name_that_no_other_tracked_file_has(self):
        self.tracked_project("skills/x/scripts/tool.py", "t3/added/x/scripts/other.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths skills/x/scripts/tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "skills/x/scripts/tool.py"})

    def test_ticket_paths_records_a_quoted_file_name_that_is_a_tracked_file_at_the_root_when_another_directory_tracks_that_name(self):
        self.tracked_project("tool.py", "src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "tool.py"})

    def test_ticket_paths_records_nothing_for_a_file_name_only_one_nested_file_has_quoted_with_a_dot_slash_before_it_a_slash_after_it_or_spaces_around_it(self):
        self.tracked_project("src/app/tool.py", "tool.py:12")
        self.open()
        for quote in ("./tool.py", "tool.py/", "./tool.py:12", " tool.py "):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", f"Fix `{quote}`")
        self.assertEqual(self.brigade("ticket", "paths"), "\n".join(f"T{number} quotes no tracked path" for number in (1, 2, 3, 4)))
        self.assertEqual(self.paths_cells(), {"T1": "", "T2": "", "T3": "", "T4": ""})

    def test_ticket_paths_records_nothing_for_a_quote_that_is_a_tracked_directory_named_as_one_nested_file_with_a_line_number_added(self):
        self.tracked_project("tool.py:12/notes.md", "src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py:12`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

    def test_ticket_paths_records_nothing_for_a_quote_that_is_a_tracked_file_named_as_one_nested_file_with_a_line_number_added(self):
        self.tracked_project("tool.py:12", "src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py:12`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

    def test_ticket_paths_records_nothing_for_a_quoted_file_name_that_files_under_skills_under_t3_and_elsewhere_have(self):
        self.tracked_project("skills/x/tool.py", "t3/added/x/tool.py", "vendor/x/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

    def test_ticket_paths_records_nothing_for_a_quoted_file_name_that_no_tracked_file_has(self):
        self.tracked_project("src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `nope.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

    def test_ticket_paths_records_nothing_for_a_quoted_file_name_that_two_tracked_files_outside_skills_have(self):
        self.tracked_project("src/app/tool.py", "lib/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": ""})

    def test_ticket_paths_records_quoted_paths_on_waiting_tickets_that_record_none_and_logs_nothing(self):
        self.unrecorded_tickets()
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "paths"),
                         "T1 paths docs/guide.md,tests/test_install.py\nT2 quotes no tracked path")
        self.assertEqual(self.paths_cells(), {"T1": "docs/guide.md,tests/test_install.py", "T2": "", "T3": "t3/runtime.md", "T4": ""})
        after = self.store_files()
        self.assertEqual(sorted(name for name in after if after[name] != before[name]), ["rail.tsv"])

    def test_ticket_paths_records_the_one_tracked_file_that_has_a_quoted_file_name_with_a_line_suffix(self):
        self.tracked_project("src/app/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py:12` and `tool.py:30-34`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths src/app/tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "src/app/tool.py"})

    def test_ticket_paths_records_the_one_tracked_file_that_has_a_quoted_file_name_with_no_directory(self):
        self.tracked_project("src/app/tool.py", "docs/guide.md")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths src/app/tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "src/app/tool.py"})

    def test_ticket_paths_records_the_t3_file_for_a_quoted_file_name_that_a_file_at_another_path_under_skills_also_has(self):
        self.tracked_project("t3/scripts/roles.py", "skills/pstack-runtime/scripts/roles.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `roles.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths t3/scripts/roles.py")
        self.assertEqual(self.paths_cells(), {"T1": "t3/scripts/roles.py"})

    def test_ticket_paths_records_the_t3_file_for_a_quoted_file_name_that_a_file_at_the_same_path_under_skills_also_has(self):
        self.tracked_project("t3/added/x/scripts/tool.py", "skills/x/scripts/tool.py")
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `tool.py`")
        self.assertEqual(self.brigade("ticket", "paths"), "T1 paths t3/added/x/scripts/tool.py")
        self.assertEqual(self.paths_cells(), {"T1": "t3/added/x/scripts/tool.py"})

    def test_ticket_paths_refuses_a_project_that_is_not_a_git_repository_and_changes_no_store_file(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Fix `docs/guide.md`")
        before = self.store_files()
        for flags in ((), ("--dry-run",)):
            self.assertEqual(self.brigade("ticket", "paths", *flags, ok=False),
                             f"brigade: cannot list tracked files in {self.project.resolve()}")
        self.assertEqual(self.store_files(), before)

    def test_ticket_paths_says_so_when_every_waiting_ticket_records_paths(self):
        self.tracked_project(*TRACKED)
        self.open()
        self.assertEqual(self.brigade("ticket", "paths"), "every waiting ticket records paths")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "Reword `README.md`", "--paths", "t3/runtime.md")
        self.assertEqual(self.brigade("ticket", "paths"), "every waiting ticket records paths")
