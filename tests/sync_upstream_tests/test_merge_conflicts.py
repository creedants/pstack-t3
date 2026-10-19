import hashlib
import json
import os
import stat
import unittest
from .support import BLOCK, CLASHING, FOOTER, MARKED, MARKERS, MERGED, NEW, NO_ROWS, OLD, OVERRIDDEN, PLUGIN, PORTED, MergeCase, commit, git, held, marker_lines, setUpModule, snapshot, write_tree

class MergeConflictsTest(MergeCase):
    def test_a_dry_run_prints_the_rows_and_writes_nothing(self):
        head = commit(
            self.upstream,
            {
                PLUGIN: '{"name": "pstack", "version": "1.1.0"}\n',
                "pstack/skills/alpha/SKILL.md": NEW,
                "pstack/skills/alpha/both.md": CLASHING,
                "pstack/skills/gone/kept.md": None,
            },
        )
        git(self.root, "init", "--quiet")
        git(self.root, "add", "--all")
        git(self.root, "commit", "--quiet", "--message", "checkout")
        before = snapshot(self.root)
        result = self.sync("--merge", "--dry-run")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(
            result.stdout.splitlines(),
            [
                f"vendor/pstack: {self.pinned[:12]} -> {head[:12]} (1.1.0), dry run, nothing written in the checkout",
                "clean     t3/overrides/alpha/SKILL.md  would merge",
                "conflict  t3/overrides/alpha/both.md  left as is, re-port by hand",
                "deleted   t3/overrides/gone/kept.md  upstream deleted its file, left as is",
                *FOOTER,
            ],
        )
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(list(self.root.rglob("__pycache__")), [])
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.assertEqual(git(self.root, "status", "--porcelain", "--ignored"), "")

    def test_a_dry_run_with_no_changed_override_prints_the_one_line_and_no_footer(self):
        head = commit(self.upstream, {"pstack/README.md": "edited\n"})
        result = self.sync("--merge", "--dry-run")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(
            result.stdout.splitlines(),
            [
                f"vendor/pstack: {self.pinned[:12]} -> {head[:12]} (1.0.0), dry run, nothing written in the checkout",
                NO_ROWS,
            ],
        )

    def test_merge_with_check_and_dry_run_without_merge_are_usage_errors_that_write_nothing(self):
        commit(self.upstream, {"pstack/skills/alpha/SKILL.md": NEW})
        before = snapshot(self.root)
        for flags, message in (
            (("--merge", "--check"), "--merge does not combine with --check"),
            (("--dry-run",), "--dry-run needs --merge"),
        ):
            with self.subTest(flags=flags):
                result = self.sync(*flags)
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn(message, result.stderr)
                self.assertEqual(snapshot(self.root), before)

    def test_an_unknown_ref_in_merge_mode_exits_1_with_the_git_error_and_writes_nothing(self):
        commit(self.upstream, {"pstack/skills/alpha/SKILL.md": NEW})
        before = snapshot(self.root)
        for flags in (("--merge",), ("--merge", "--dry-run")):
            with self.subTest(flags=flags):
                result = self.sync(*flags, "--ref", "nope")
                self.assertEqual((result.returncode, result.stdout), (1, ""))
                self.assertEqual(result.stderr, "error: pathspec 'nope' did not match any file(s) known to git\n")
                self.assertEqual(snapshot(self.root), before)
                self.assertEqual(list(self.scratch.iterdir()), [])

    def test_conflicts_writes_the_marked_merge_under_the_directory_and_leaves_the_override(self):
        directory = self.directory / "conflicts"
        lines = self.merge(
            {"pstack/skills/alpha/SKILL.md": CLASHING, "pstack/skills/alpha/both.md": NEW}, "--conflicts", str(directory)
        )
        saved = directory.resolve() / "alpha/SKILL.md"
        self.assertEqual(
            lines[:2],
            [
                f"conflict  t3/overrides/alpha/SKILL.md  left as is, re-port by hand, git's marked merge is in {saved}",
                "clean     t3/overrides/alpha/both.md  merged",
            ],
        )
        self.assertEqual(lines[-3:], FOOTER)
        self.assertEqual(held(directory), {"alpha": None, "alpha/SKILL.md": MARKED.encode()})
        self.assertEqual(self.override("alpha/SKILL.md"), PORTED.encode())
        self.assertEqual(self.override("alpha/both.md"), MERGED.encode())

    def test_conflicts_leaves_no_marker_line_in_any_file_in_the_checkout(self):
        directory = self.directory / "conflicts"
        self.merge(
            {"pstack/skills/alpha/SKILL.md": CLASHING, "pstack/skills/alpha/both.md": NEW}, "--conflicts", str(directory)
        )
        self.assertEqual(
            marker_lines(directory),
            [
                ("alpha/SKILL.md", b"<<<<<<< override"),
                ("alpha/SKILL.md", b"======="),
                ("alpha/SKILL.md", b">>>>>>> new upstream"),
            ],
        )
        self.assertEqual(marker_lines(self.root), [])

    def test_conflicts_saves_no_file_for_a_markers_row_or_a_deleted_row(self):
        directory = self.directory / "conflicts"
        lines = self.merge(
            {
                "pstack/skills/alpha/SKILL.md": CLASHING,
                "pstack/skills/alpha/both.md": NEW + BLOCK,
                "pstack/skills/gone/kept.md": None,
            },
            "--conflicts",
            str(directory),
        )
        saved = directory.resolve() / "alpha/SKILL.md"
        self.assertEqual(
            lines[:3],
            [
                f"conflict  t3/overrides/alpha/SKILL.md  left as is, re-port by hand, git's marked merge is in {saved}",
                f"markers   t3/overrides/alpha/both.md  {MARKERS}",
                "deleted   t3/overrides/gone/kept.md  upstream deleted its file, left as is",
            ],
        )
        self.assertEqual(held(directory), {"alpha": None, "alpha/SKILL.md": MARKED.encode()})

    def test_conflicts_with_a_dry_run_writes_the_files_and_nothing_in_the_checkout(self):
        head = commit(
            self.upstream,
            {
                PLUGIN: '{"name": "pstack", "version": "1.1.0"}\n',
                "pstack/skills/alpha/SKILL.md": NEW,
                "pstack/skills/alpha/both.md": CLASHING,
                "pstack/skills/gone/kept.md": None,
            },
        )
        git(self.root, "init", "--quiet")
        git(self.root, "add", "--all")
        git(self.root, "commit", "--quiet", "--message", "checkout")
        before = snapshot(self.root)
        directory = self.directory / "conflicts"
        result = self.sync("--merge", "--dry-run", "--conflicts", str(directory))
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        saved = directory.resolve() / "alpha/both.md"
        self.assertEqual(
            result.stdout.splitlines(),
            [
                f"vendor/pstack: {self.pinned[:12]} -> {head[:12]} (1.1.0), dry run, nothing written in the checkout",
                "clean     t3/overrides/alpha/SKILL.md  would merge",
                f"conflict  t3/overrides/alpha/both.md  left as is, re-port by hand, git's marked merge is in {saved}",
                "deleted   t3/overrides/gone/kept.md  upstream deleted its file, left as is",
                *FOOTER,
            ],
        )
        self.assertEqual(held(directory), {"alpha": None, "alpha/both.md": MARKED.encode()})
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(list(self.root.rglob("__pycache__")), [])
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.assertEqual(git(self.root, "status", "--porcelain", "--ignored"), "")

    def test_conflicts_refuses_a_path_that_resolves_to_the_checkout_or_under_it(self):
        commit(self.upstream, {"pstack/skills/alpha/SKILL.md": CLASHING})
        link = self.directory / "link"
        link.symlink_to(self.root / "t3")
        dangling = self.directory / "dangling"
        dangling.symlink_to(self.root / "missing")
        before = snapshot(self.root)
        for name, path in (
            ("an absolute path that does not exist", str(self.root / "saved/deep")),
            ("a relative path", "saved"),
            ("the checkout", str(self.root)),
            ("an existing directory", str(self.root / "t3")),
            ("a symlink to a directory in the checkout", str(link)),
            ("a dangling symlink to a missing path under the checkout", str(dangling)),
        ):
            with self.subTest(path=name):
                result = self.sync("--merge", "--conflicts", path)
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn("--conflicts needs a path outside the checkout", result.stderr)
                self.assertEqual(snapshot(self.root), before)

    def test_conflicts_refuses_a_path_that_exists(self):
        commit(self.upstream, {"pstack/skills/alpha/SKILL.md": CLASHING})
        outside = self.directory / "outside"
        (outside / "empty").mkdir(parents=True)
        (outside / "file").write_text("kept\n")
        (outside / "dangling").symlink_to(outside / "missing")
        self.assertEqual(self.sync("--merge", "--dry-run", "--conflicts", str(outside / "earlier")).returncode, 0)
        self.assertEqual(held(outside / "earlier"), {"alpha": None, "alpha/SKILL.md": MARKED.encode()})
        before = snapshot(self.root)
        existing = snapshot(outside)
        for name in ("empty", "file", "dangling", "earlier"):
            with self.subTest(path=name):
                result = self.sync("--merge", "--conflicts", str(outside / name))
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn("--conflicts needs a path that does not exist yet", result.stderr)
                self.assertEqual(snapshot(outside), existing)
                self.assertEqual(snapshot(self.root), before)

    def test_conflicts_takes_a_relative_path_that_resolves_outside_the_checkout(self):
        lines = self.merge({"pstack/skills/alpha/SKILL.md": CLASHING}, "--conflicts", "../conflicts")
        directory = self.directory.resolve() / "conflicts"
        self.assertEqual(
            lines[0],
            "conflict  t3/overrides/alpha/SKILL.md  left as is, re-port by hand, "
            f"git's marked merge is in {directory / 'alpha/SKILL.md'}",
        )
        self.assertEqual(held(self.directory / "conflicts"), {"alpha": None, "alpha/SKILL.md": MARKED.encode()})

    def test_conflicts_with_no_conflict_prints_what_a_run_without_it_prints_and_creates_nothing(self):
        directory = self.directory / "conflicts"
        commit(self.upstream, {"pstack/skills/alpha/SKILL.md": NEW})
        plain = self.sync("--merge", "--dry-run")
        flagged = self.sync("--merge", "--dry-run", "--conflicts", str(directory))
        self.assertEqual((flagged.returncode, flagged.stdout, flagged.stderr), (plain.returncode, plain.stdout, plain.stderr))
        self.assertFalse(os.path.lexists(directory))
        lines = self.merge({"other/notes.md": "edited\n"}, "--conflicts", str(directory))
        self.assertEqual(
            lines,
            [
                "clean     t3/overrides/alpha/SKILL.md  merged",
                "override drift:",
                "  alpha/SKILL.md: upstream changed since this override was written; review or re-port it, then --update-lock",
                "",
                *FOOTER,
            ],
        )
        self.assertFalse(os.path.lexists(directory))

    def test_conflicts_that_cannot_be_created_exits_1_and_writes_nothing_in_the_checkout(self):
        commit(self.upstream, {"pstack/skills/alpha/SKILL.md": CLASHING})
        file = self.directory / "file"
        file.write_text("kept\n")
        before = snapshot(self.root)
        result = self.sync("--merge", "--conflicts", str(file / "sub"))
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertNotEqual(result.stderr, "")
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_conflicts_without_merge_is_a_usage_error_that_writes_nothing_in_the_checkout(self):
        commit(self.upstream, {"pstack/skills/alpha/SKILL.md": CLASHING})
        directory = self.directory / "conflicts"
        before = snapshot(self.root)
        for flags in (("--conflicts", str(directory)), ("--check", "--conflicts", str(directory))):
            with self.subTest(flags=flags):
                result = self.sync(*flags)
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn("--conflicts needs --merge", result.stderr)
                self.assertEqual(snapshot(self.root), before)


