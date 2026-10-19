import unittest

from tests import in_process

from .support import (
    setUpModule,
    ReleaseCase,
    ENV,
    commit,
    state,
    repository,
)


MISSING = (
    "no stable check record; run setup-pstack and one small real item on the latest stable "
    "T3 Code release, record `Stable check: T3 Code 1.2.3 passed` in the release notes file, "
    "with 1.2.3 the version you ran, and pass it with --notes-file\n"
)


def invoke(repo, *args):
    return in_process.invoke(in_process.Invocation(
        program=repo / "scripts/release.py",
        argv=(*args, "--date", "2026-10-09"),
        env=ENV,
        cwd=repo.parent,
    ))


class StableCheckTest(ReleaseCase):
    def test_real_release_without_notes_file_refuses(self):
        with repository() as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            before = state(repo)
            result = invoke(repo, "0.2.1")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", MISSING))
            self.assertEqual(state(repo), before)

    def test_real_release_with_notes_file_holding_no_record_refuses(self):
        with repository() as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            notes = repo.parent / "notes.md"
            notes.write_text("# Release 0.2.1\n\n- fix one\n")
            before = state(repo)
            result = invoke(repo, "0.2.1", "--notes-file", str(notes))
            self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", MISSING))
            self.assertEqual(state(repo), before)

    def test_real_release_with_failed_record_refuses_and_names_version(self):
        with repository() as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            notes = repo.parent / "notes.md"
            notes.write_text("Stable check: T3 Code 1.2.3 failed\n")
            before = state(repo)
            result = invoke(repo, "0.2.1", "--notes-file", str(notes))
            self.assertEqual(
                (result.returncode, result.stdout, result.stderr),
                (1, "", "stable check on T3 Code 1.2.3 failed; record a passing result\n"),
            )
            self.assertEqual(state(repo), before)

    def test_dry_run_without_notes_file_prints_section(self):
        with repository() as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            before = state(repo)
            result = invoke(repo, "0.2.1", "--dry-run")
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertTrue(result.stdout.startswith("## 0.2.1 (2026-10-09)\n"))
            self.assertEqual(state(repo), before)


if __name__ == "__main__":
    unittest.main()
