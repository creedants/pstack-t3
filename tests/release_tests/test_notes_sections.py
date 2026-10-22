import unittest

from tests import in_process

from .support import (
    DRY_RUN,
    ENV,
    SECTION,
    ReleaseCase,
    commit,
    history,
    repository,
    run,
    setUpModule,
    state,
)
from .test_stable_check import MISSING

SECTIONS = ("## New", "## Fixed", "## Before you upgrade")


def notes(*, omit=(), blank=(), stable=True):
    text = "Stable check: T3 Code 0.1.0 passed\n" if stable else ""
    for heading in SECTIONS:
        if heading in omit:
            continue
        text += f"\n{heading}\n"
        text += "\n\n" if heading in blank else "\nNone.\n"
    return text


def gap(*missing):
    return f"release notes file lacks {', '.join(missing)}; add each heading with a non-empty line under it\n"


def invoke(repo, text, *args):
    path = repo.parent / "notes.md"
    path.write_text(text)
    return in_process.invoke(in_process.Invocation(
        program=repo / "scripts/release.py",
        argv=(*args, "--notes-file", str(path), "--date", "2026-10-09"),
        env=ENV,
        cwd=repo.parent,
    ))


class NotesSectionsTest(ReleaseCase):
    def test_a_real_run_that_omits_a_section_refuses_and_names_only_that_heading(self):
        for heading in SECTIONS:
            with self.subTest(heading=heading), repository() as repo:
                commit(repo, {"changes/a.md": "- a\n"}, day=2)
                before = state(repo)
                result = invoke(repo, notes(omit=(heading,)), "0.2.1")
                self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", gap(heading)))
                self.assertEqual(state(repo), before)

    def test_a_real_run_that_omits_all_three_sections_names_them_in_sections_order(self):
        with repository() as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            before = state(repo)
            result = invoke(repo, notes(omit=SECTIONS), "0.2.1")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", gap(*SECTIONS)))
            self.assertEqual(state(repo), before)

    def test_a_real_run_that_leaves_a_section_blank_under_its_heading_refuses(self):
        for heading in SECTIONS:
            with self.subTest(heading=heading), repository() as repo:
                commit(repo, {"changes/a.md": "- a\n"}, day=2)
                before = state(repo)
                result = invoke(repo, notes(blank=(heading,)), "0.2.1")
                self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", gap(heading)))
                self.assertEqual(state(repo), before)

    def test_a_real_run_with_all_three_sections_writes_the_changelog_and_deletes_the_fragments(self):
        with history() as repo:
            result = run(repo, "0.2.1")
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertIn(SECTION, (repo / "CHANGELOG.md").read_text())
            self.assertEqual(list((repo / "changes").iterdir()), [])

    def test_a_dry_run_that_omits_a_section_prints_the_sentence_on_stderr_and_exits_zero(self):
        for heading in SECTIONS:
            with self.subTest(heading=heading), history() as repo:
                before = state(repo)
                result = invoke(repo, notes(omit=(heading,)), "0.2.1", "--dry-run")
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, DRY_RUN, gap(heading)))
                self.assertEqual(state(repo), before)

    def test_a_dry_run_with_a_complete_notes_file_prints_nothing_on_stderr(self):
        with history() as repo:
            result = run(repo, "0.2.1", "--dry-run")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, DRY_RUN, ""))

    def test_a_real_run_with_no_stable_record_and_no_sections_still_refuses_with_the_stable_record_message(self):
        with repository() as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            before = state(repo)
            result = invoke(repo, notes(omit=SECTIONS, stable=False), "0.2.1")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", MISSING))
            self.assertEqual(state(repo), before)


if __name__ == "__main__":
    unittest.main()
