import unittest

from tests import in_process

from .support import (
    DRY_RUN,
    ENV,
    ReleaseCase,
    commit,
    history,
    repository,
    setUpModule,
    state,
)

PLAIN_ENV = {name: value for name, value in ENV.items() if name != "PYTHONDONTWRITEBYTECODE"}

GAP = (
    "release notes file lacks ## New, ## Fixed, ## Before you upgrade; "
    "add each heading with a non-empty line under it\n"
)


def bytecode_paths(repo):
    hits = [path for path in repo.rglob("__pycache__")]
    hits += [path for path in repo.rglob("*.pyc")]
    return hits


def real_without_bytecode_flag(repo, text, *args):
    notes = repo.parent / "notes.md"
    notes.write_text(text)
    return in_process.finished(
        in_process.Reason.INHERITED_ENVIRONMENT,
        in_process.Invocation(
            program=repo / "scripts/release.py",
            argv=(*args, "--notes-file", str(notes), "--date", "2026-10-09"),
            env=PLAIN_ENV,
            cwd=repo.parent,
        ),
    )


def nested_notes(heading):
    text = "Stable check: T3 Code 0.1.0 passed\n"
    for section in ("## New", "## Fixed", "## Before you upgrade"):
        if section == heading:
            text += f"\n{section}\n\n### Details\n\n- a user-facing release note.\n"
        else:
            text += f"\n{section}\n\nNone.\n"
    return text


class NotesImportWritesNothingTest(ReleaseCase):
    def test_a_refusal_without_the_bytecode_flag_writes_no_bytecode_file(self):
        with repository() as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            before = state(repo)
            result = real_without_bytecode_flag(
                repo, "Stable check: T3 Code 0.1.0 passed\n", "0.2.1"
            )
            self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", GAP))
            self.assertEqual(bytecode_paths(repo), [])
            self.assertEqual(state(repo), before)

    def test_a_dry_run_without_the_bytecode_flag_writes_no_bytecode_file(self):
        with history() as repo:
            before = state(repo)
            notes = repo.parent / "notes.md"
            notes.write_text(
                "Stable check: T3 Code 0.1.0 passed\n\n"
                "## New\n\nNone.\n\n## Fixed\n\nNone.\n\n## Before you upgrade\n\nNone.\n"
            )
            result = in_process.finished(
                in_process.Reason.INHERITED_ENVIRONMENT,
                in_process.Invocation(
                    program=repo / "scripts/release.py",
                    argv=("0.2.1", "--notes-file", str(notes), "--date", "2026-10-09", "--dry-run"),
                    env=PLAIN_ENV,
                    cwd=repo.parent,
                ),
            )
            self.assertEqual((result.returncode, result.stderr), (0, ""))
            self.assertEqual(result.stdout, DRY_RUN)
            self.assertEqual(bytecode_paths(repo), [])
            self.assertEqual(state(repo), before)


class NotesNestedHeadingTest(ReleaseCase):
    def test_a_dry_run_accepts_content_under_a_level_three_heading_in_each_section(self):
        for heading in ("## New", "## Fixed", "## Before you upgrade"):
            with self.subTest(heading=heading), history() as repo:
                notes = repo.parent / "notes.md"
                notes.write_text(nested_notes(heading))
                result = in_process.invoke(in_process.Invocation(
                    program=repo / "scripts/release.py",
                    argv=("0.2.1", "--notes-file", str(notes), "--date", "2026-10-09", "--dry-run"),
                    env=ENV,
                    cwd=repo.parent,
                ))
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                self.assertEqual(result.stdout, DRY_RUN)


if __name__ == "__main__":
    unittest.main()
