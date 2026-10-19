import os
import pathlib
import shlex
import shutil
import signal
import subprocess
import sys
import unittest
from unittest import mock

from .support import (
    CHANGELOG,
    DIRTY,
    DRY_RUN,
    ENV,
    NOTHING_CHANGED,
    REAL_RUN,
    RELEASED,
    SECTION,
    STEPS,
    UNDO,
    UPKEEP,
    ReleaseCase,
    commit,
    git,
    history,
    recording_handlers,
    release,
    repository,
    run,
    setUpModule,
    state,
)


class RunTest(ReleaseCase):
    def test_dry_run_prints_the_section_and_changes_nothing(self):
        with history() as repo:
            before = state(repo)
            result = run(repo, "0.2.1", "--dry-run")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, DRY_RUN, ""))
            self.assertEqual(state(repo), before)
            self.assertEqual(before[0], "")
            self.assertEqual((repo / "CHANGELOG.md").read_text(), CHANGELOG)
            self.assertEqual(sorted(path.name for path in (repo / "changes").iterdir()),
                             ["alpha.md", "beta.md", "eta.md", "zeta.md"])

    def test_dry_run_takes_the_flag_before_the_version(self):
        with history() as repo:
            result = run(repo, "--dry-run", "0.2.1")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, DRY_RUN, ""))

    def test_release_writes_the_section_above_the_latest_heading(self):
        with history() as repo:
            result = run(repo, "0.2.1")
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, REAL_RUN, ""))
            self.assertEqual(
                (repo / "CHANGELOG.md").read_text(),
                "# Changelog\n\n" + SECTION + "\n## 0.2.0 (2026-10-06)\n\n- old\n\n## 0.1.0\n\nFirst.\n",
            )

    def test_release_deletes_the_fragments_and_leaves_git_alone(self):
        with history() as repo:
            head = git(repo, "rev-parse", "HEAD")
            self.assertEqual(run(repo, "0.2.1").returncode, 0)
            self.assertEqual(git(repo, "status", "--porcelain"), RELEASED)
            self.assertEqual(list((repo / "changes").iterdir()), [])
            self.assertEqual(git(repo, "diff", "--cached", "--name-only"), "")
            self.assertEqual(git(repo, "tag"), "")
            self.assertEqual(git(repo, "rev-parse", "HEAD"), head)

    def test_release_prints_the_remaining_steps(self):
        with history() as repo:
            self.assertEqual(run(repo, "0.2.1").stdout[-len(STEPS):], STEPS)

    def test_printed_git_add_stages_the_release(self):
        with history() as repo:
            printed = [line.strip() for line in run(repo, "0.2.1").stdout.splitlines()]
            add = [line for line in printed if line.startswith("git add ")]
            self.assertEqual(add, ["git add CHANGELOG.md changes/"])
            subprocess.run(shlex.split(add[0]), cwd=repo, check=True, env=ENV)
            self.assertEqual(
                git(repo, "status", "--porcelain"),
                "M  CHANGELOG.md\nD  changes/alpha.md\nD  changes/beta.md\nD  changes/eta.md\nD  changes/zeta.md\n",
            )

    def test_real_run_calls_only_read_only_git_and_never_gh(self):
        with history() as repo:
            shim = repo.parent / "shim"
            shim.mkdir()
            calls = repo.parent / "calls"
            for name in ("git", "gh"):
                real = shutil.which(name) or "/bin/true"
                (shim / name).write_text(f'#!/bin/sh\necho "{name} $1" >> "{calls}"\nexec "{real}" "$@"\n')
                (shim / name).chmod(0o755)
            result = run(repo, "0.2.1", env={**ENV, "PATH": f"{shim}{os.pathsep}{ENV['PATH']}"})
            self.assertEqual((result.returncode, result.stdout), (0, REAL_RUN))
            self.assertEqual(calls.read_text(), "git rev-parse\ngit status\ngit log\n")

    def test_changelog_without_a_heading_takes_the_section_at_the_end(self):
        with repository("# Changelog\n") as repo:
            commit(repo, {"changes/a.md": "- a\n"}, day=2)
            # A changelog with no released version takes the version as it is given.
            self.assertEqual(run(repo, "0.3.0").returncode, 0)
            self.assertEqual((repo / "CHANGELOG.md").read_text(),
                             "# Changelog\n\n## 0.3.0 (2026-10-09)\n\n" + UPKEEP + "- a\n")

    def test_rerun_refuses_then_restore_recovers(self):
        with history() as repo:
            self.assertEqual(run(repo, "0.2.1").returncode, 0)
            self.assert_refuses(repo, "0.2.1", DIRTY + RELEASED)
            git(repo, *shlex.split(UNDO)[1:])
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            self.assert_section(repo, SECTION)
            self.assertEqual(run(repo, "0.2.1").returncode, 0)
            git(repo, "add", "CHANGELOG.md", "changes/")
            git(repo, "commit", "-q", "-m", "release 0.2.1")
            self.assert_refuses(repo, "0.2.1", "CHANGELOG.md already has a ## 0.2.1 heading; pick the next version\n")

    def test_printed_undo_restores_a_staged_release(self):
        with history() as repo:
            before = state(repo)
            self.assertEqual(run(repo, "0.2.1").returncode, 0)
            git(repo, "add", "CHANGELOG.md", "changes/")
            self.assertEqual(git(repo, "status", "--porcelain"), "M  CHANGELOG.md\nD  changes/alpha.md\n"
                             "D  changes/beta.md\nD  changes/eta.md\nD  changes/zeta.md\n")
            git(repo, *shlex.split(UNDO)[1:])
            self.assertEqual(state(repo), before)

    def test_printed_undo_restores_an_unstaged_release(self):
        with history() as repo:
            before = state(repo)
            self.assertEqual(run(repo, "0.2.1").returncode, 0)
            git(repo, *shlex.split(UNDO)[1:])
            self.assertEqual(state(repo), before)

    def test_changelog_keeps_its_file_mode(self):
        with history() as repo:
            (repo / "CHANGELOG.md").chmod(0o640)
            self.assertEqual(run(repo, "0.2.1").returncode, 0)
            self.assertEqual((repo / "CHANGELOG.md").stat().st_mode & 0o777, 0o640)
            self.assertEqual([path.name for path in repo.glob(".*.tmp")], [])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root deletes from a read-only directory")
    def test_failed_first_delete_leaves_the_files_as_they_were(self):
        with history() as repo:
            before = state(repo)
            (repo / "changes").chmod(0o555)
            try:
                result = run(repo, "0.2.1")
            finally:
                (repo / "changes").chmod(0o755)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr[:17], "stopped partway: ")
            self.assertEqual(result.stderr[-len(NOTHING_CHANGED) - 1:], NOTHING_CHANGED + "\n")
            self.assertEqual(state(repo), before)
            self.assertEqual(git(repo, "status", "--porcelain"), "")
            self.assertEqual(list(repo.glob(".*.tmp")), [])
            self.assert_section(repo, SECTION)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root writes into a read-only directory")
    def test_failed_changelog_write_leaves_the_files_as_they_were(self):
        with history() as repo:
            before = state(repo)
            repo.chmod(0o555)
            try:
                result = run(repo, "0.2.1")
            finally:
                repo.chmod(0o755)
            self.assertEqual((result.returncode, result.stderr[:17]), (1, "stopped partway: "))
            self.assertEqual(result.stderr[-len(NOTHING_CHANGED) - 1:], NOTHING_CHANGED + "\n")
            self.assertEqual(state(repo), before)

    def apply_failing_at(self, repo, fail, restore=None):
        real_unlink, real_write, calls = pathlib.Path.unlink, release.write_file, []

        def unlink(path, *args, **kwargs):
            calls.append(path.name)
            if len(calls) == fail:
                raise PermissionError(13, "Permission denied", str(path))
            return real_unlink(path, *args, **kwargs)

        def write_file(path, data, mode):
            if restore and path.parent.name == "changes":
                raise OSError(28, "No space left on device", str(path))
            return real_write(path, data, mode)

        with mock.patch.object(release, "ROOT", repo):
            cut = release.plan("0.2.1", "2026-10-09")
            with mock.patch.object(pathlib.Path, "unlink", unlink), mock.patch.object(release, "write_file", write_file):
                with self.assertRaises(SystemExit) as raised:
                    release.apply(cut)
        return str(raised.exception), calls

    def test_failed_later_delete_puts_back_the_changelog_and_the_deleted_fragments(self):
        with history() as repo:
            before = state(repo)
            message, calls = self.apply_failing_at(repo, fail=3)
            self.assertEqual(calls, ["eta.md", "beta.md", "alpha.md"])
            self.assertEqual(message[:17], "stopped partway: ")
            self.assertEqual(message[-len(NOTHING_CHANGED):], NOTHING_CHANGED)
            self.assertEqual(state(repo), before)
            self.assertEqual(list(repo.glob(".*.tmp")) + list((repo / "changes").glob(".*.tmp")), [])

    def test_failed_last_delete_puts_everything_back(self):
        with history() as repo:
            before = state(repo)
            message, calls = self.apply_failing_at(repo, fail=4)
            self.assertEqual(calls, ["eta.md", "beta.md", "alpha.md", "zeta.md"])
            self.assertEqual(message[-len(NOTHING_CHANGED):], NOTHING_CHANGED)
            self.assertEqual(state(repo), before)

    def test_interrupt_puts_everything_back(self):
        with history() as repo:
            before = state(repo)
            real_unlink, calls = pathlib.Path.unlink, []

            def unlink(path, *args, **kwargs):
                calls.append(path.name)
                if len(calls) == 2:
                    raise KeyboardInterrupt
                return real_unlink(path, *args, **kwargs)

            with mock.patch.object(release, "ROOT", repo):
                cut = release.plan("0.2.1", "2026-10-09")
                with mock.patch.object(pathlib.Path, "unlink", unlink):
                    with self.assertRaises(SystemExit) as raised:
                        release.apply(cut)
            self.assertEqual(str(raised.exception), "stopped partway: interrupted" + NOTHING_CHANGED)
            self.assertEqual(state(repo), before)

    def apply_signalled(self, repo, replace=None, unlink=None, fail_unlink=None):
        """Run release.apply, sending each listed real signal right after the Nth os.replace or Path.unlink returns."""
        replace, unlink, calls = replace or {}, unlink or {}, {"replace": 0, "unlink": 0}
        real_replace, real_unlink = os.replace, pathlib.Path.unlink

        def after(kind, signals):
            calls[kind] += 1
            for number in signals.get(calls[kind], ()):
                os.kill(os.getpid(), number)

        def replacing(source, target, *args, **kwargs):
            real_replace(source, target, *args, **kwargs)
            after("replace", replace)

        def unlinking(path, *args, **kwargs):
            if calls["unlink"] + 1 == fail_unlink:
                calls["unlink"] += 1
                raise PermissionError(13, "Permission denied", str(path))
            real_unlink(path, *args, **kwargs)
            after("unlink", unlink)

        held = {number: signal.getsignal(number) for number in release.HELD}
        with recording_handlers() as missed, mock.patch.object(release, "ROOT", repo):
            cut = release.plan("0.2.1", "2026-10-09")
            with mock.patch.object(os, "replace", replacing), mock.patch.object(pathlib.Path, "unlink", unlinking):
                with self.assertRaises(SystemExit) as raised:
                    release.apply(cut)
            self.assertEqual(missed, [])
        self.assertEqual(held, {number: signal.getsignal(number) for number in release.HELD})
        return str(raised.exception)

    def test_signal_right_after_the_changelog_replace_puts_everything_back(self):
        for number in release.HELD:
            with self.subTest(signal=number), history() as repo:
                before = state(repo)
                message = self.apply_signalled(repo, replace={1: [number]})
                self.assertEqual(message, "stopped partway: interrupted" + NOTHING_CHANGED)
                self.assertEqual(state(repo), before)
                self.assertEqual(list(repo.glob(".*.tmp")) + list((repo / "changes").glob(".*.tmp")), [])

    def test_signal_right_after_each_fragment_delete_puts_everything_back(self):
        for call in (1, 2, 3, 4):
            for number in release.HELD:
                with self.subTest(delete=call, signal=number), history() as repo:
                    before = state(repo)
                    message = self.apply_signalled(repo, unlink={call: [number]})
                    self.assertEqual(message, "stopped partway: interrupted" + NOTHING_CHANGED)
                    self.assertEqual(state(repo), before)

    def test_repeated_signals_during_the_put_back_do_not_stop_it(self):
        with history() as repo:
            before = state(repo)
            message = self.apply_signalled(
                repo, fail_unlink=3, replace={2: [signal.SIGINT], 3: [signal.SIGTERM], 4: [signal.SIGHUP]})
            self.assertEqual(message[:17], "stopped partway: ")
            self.assertEqual(message[-len(NOTHING_CHANGED):], NOTHING_CHANGED)
            self.assertEqual(state(repo), before)

    def test_signal_then_more_signals_during_the_put_back_do_not_stop_it(self):
        with history() as repo:
            before = state(repo)
            message = self.apply_signalled(
                repo, unlink={2: [signal.SIGINT]}, replace={2: [signal.SIGINT, signal.SIGINT], 3: [signal.SIGTERM]})
            self.assertEqual(message, "stopped partway: interrupted" + NOTHING_CHANGED)
            self.assertEqual(state(repo), before)

    def test_signal_during_a_put_back_that_fails_still_names_the_files(self):
        with history() as repo:
            real_write = release.write_file

            def write_file(path, data, mode):
                if path.parent.name == "changes":
                    os.kill(os.getpid(), signal.SIGINT)
                    raise OSError(28, "No space left on device", str(path))
                return real_write(path, data, mode)

            with mock.patch.object(release, "write_file", write_file):
                message = self.apply_signalled(repo, unlink={2: [signal.SIGTERM]})
            self.assertIn("stopped partway: interrupted; could not put back changes/eta.md ([Errno 28]", message)
            self.assertIn(", changes/beta.md ([Errno 28]", message)
            self.assertEqual(message[-len(f"; run {UNDO} to undo, then rerun"):], f"; run {UNDO} to undo, then rerun")
            self.assertEqual((repo / "CHANGELOG.md").read_text(), CHANGELOG)
            git(repo, *shlex.split(UNDO)[1:])
            self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_a_signal_at_any_line_of_apply_leaves_the_files_as_they_were_or_released(self):
        for fail in (None, 3):
            with self.subTest(failing_delete=fail), history() as repo, mock.patch.object(release, "ROOT", repo):
                before, cut = state(repo), release.plan("0.2.1", "2026-10-09")
                real_unlink = pathlib.Path.unlink

                def run_traced(fire_at):
                    seen, deletes = 0, 0

                    def unlink(path, *args, **kwargs):
                        nonlocal deletes
                        deletes += 1
                        if deletes == fail:
                            raise PermissionError(13, "Permission denied", str(path))
                        return real_unlink(path, *args, **kwargs)

                    def local(frame, event, arg):
                        nonlocal seen
                        if event == "line":
                            seen += 1
                            if seen == fire_at:
                                sys.settrace(None)
                                os.kill(os.getpid(), signal.SIGINT)
                        return local

                    def tracer(frame, event, arg):
                        return local if frame.f_code.co_filename == release.__file__ else None

                    message = None
                    with mock.patch.object(pathlib.Path, "unlink", unlink):
                        sys.settrace(tracer)
                        try:
                            release.apply(cut)
                        except SystemExit as error:
                            message = str(error)
                        finally:
                            sys.settrace(None)
                    return seen, message

                with recording_handlers():
                    held = {number: signal.getsignal(number) for number in release.HELD}
                    lines, message = run_traced(0)
                    released = state(repo)
                    self.assertEqual(message is None, fail is None)
                    self.assertGreater(lines, 20)
                    outcomes = set()
                    for fire_at in range(1, lines + 1):
                        git(repo, *shlex.split(UNDO)[1:])
                        self.assertEqual(state(repo), before)
                        _, message = run_traced(fire_at)
                        self.assertEqual({n: signal.getsignal(n) for n in release.HELD}, held)
                        if message is None:
                            self.assertEqual(state(repo), released, f"signal at line event {fire_at}")
                            outcomes.add("released")
                        else:
                            self.assertEqual(message[:17], "stopped partway: ", f"signal at line event {fire_at}")
                            self.assertEqual(message[-len(NOTHING_CHANGED):], NOTHING_CHANGED)
                            self.assertEqual(state(repo), before, f"signal at line event {fire_at}")
                            outcomes.add("put back")
                    self.assertEqual(outcomes, {"released", "put back"} if fail is None else {"put back"})

    def test_failed_put_back_names_each_file_and_the_undo(self):
        with history() as repo:
            message, _ = self.apply_failing_at(repo, fail=3, restore=True)
            self.assertEqual(message[:17], "stopped partway: ")
            self.assertIn("; could not put back changes/eta.md ([Errno 28] No space left on device: ", message)
            self.assertIn(", changes/beta.md ([Errno 28]", message)
            self.assertNotIn("CHANGELOG.md (", message)
            self.assertEqual(message[-len(f"; run {UNDO} to undo, then rerun"):], f"; run {UNDO} to undo, then rerun")
            self.assertEqual((repo / "CHANGELOG.md").read_text(), CHANGELOG)
            git(repo, *shlex.split(UNDO)[1:])
            self.assertEqual(git(repo, "status", "--porcelain"), "")
