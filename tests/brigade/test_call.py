import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from .support import (
    setUpModule,
    call,
)


class CallTest(unittest.TestCase):
    def _in_process(self):
        name = "PSTACK_TEST_REAL_PROCESS"
        previous = os.environ.get(name)
        os.environ.pop(name, None)

        def restore():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

        self.addCleanup(restore)

    def _directory(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def _write(self, directory, name, source):
        path = directory / name
        body = "import sys\n" + textwrap.dedent(source).strip() + "\n\n"
        body += "if __name__ == '__main__':\n    raise SystemExit(main(sys.argv[1:]))\n"
        path.write_text(body, encoding="utf-8")
        return path

    def _captured(self, directory, name, source, argv):
        path = self._write(directory, name, source)
        outcome = call(self, path, argv)
        real = subprocess.run([sys.executable, str(path), *argv], capture_output=True)
        return outcome, real

    def test_capture_and_exit_match_a_real_process(self):
        directory = self._directory()
        rows = (
            (
                "stdout.py",
                """
                import sys
                def main(argv):
                    print(argv[0])
                    print(sys.argv[1])
                """,
                ["zeta"],
            ),
            (
                "stderr_text_then_raw.py",
                """
                import sys
                def main(argv):
                    print("TEXT", file=sys.stderr)
                    sys.stderr.buffer.write(b"RAW")
                """,
                [],
            ),
            (
                "stderr_raw_then_text.py",
                """
                import sys
                def main(argv):
                    sys.stderr.buffer.write(b"RAW")
                    print("TEXT", file=sys.stderr)
                """,
                [],
            ),
            (
                "stdout_text_then_raw.py",
                """
                import sys
                def main(argv):
                    print("TEXT")
                    sys.stdout.buffer.write(b"RAW")
                """,
                [],
            ),
            (
                "newlines.py",
                """
                import sys
                def main(argv):
                    sys.stdout.buffer.write(b"a\\r\\nb\\rc\\n")
                """,
                [],
            ),
            (
                "exit_2.py",
                """
                def main(argv):
                    raise SystemExit(2)
                """,
                [],
            ),
            (
                "exit_none.py",
                """
                def main(argv):
                    raise SystemExit(None)
                """,
                [],
            ),
            (
                "exit_boom.py",
                """
                def main(argv):
                    raise SystemExit("boom")
                """,
                [],
            ),
        )
        for name, source, argv in rows:
            with self.subTest(name=name):
                outcome, real = self._captured(directory, name, source, argv)
                self.assertEqual(outcome.code, real.returncode)
                self.assertEqual(outcome.stdout, real.stdout)
                self.assertEqual(outcome.stderr, real.stderr)
        shown = subprocess.run(
            [sys.executable, str(directory / "newlines.py")],
            capture_output=True,
            text=True,
        )
        kept = call(self, directory / "newlines.py", [])
        self.assertEqual(kept.stdout, b"a\r\nb\rc\n")
        self.assertEqual(kept.stdout_text(), shown.stdout)
        self.assertEqual(kept.stdout_text(), "a\nb\nc\n")
        outcome, real = self._captured(
            directory,
            "runtime.py",
            """
            def main(argv):
                raise RuntimeError("distinct-failure")
            """,
            [],
        )
        self.assertEqual(outcome.code, 1)
        self.assertEqual(real.returncode, 1)
        self.assertIn(b"Traceback", outcome.stderr)
        self.assertIn(b"RuntimeError", outcome.stderr)
        self.assertIn(b"distinct-failure", outcome.stderr)
        outcome, real = self._captured(
            directory,
            "broken.py",
            """
            def main(argv):
                raise BrokenPipeError("pipe")
            """,
            [],
        )
        self.assertEqual(outcome.code, real.returncode, (outcome.code, real.returncode))
        self.assertIn(b"BrokenPipeError", outcome.stderr)
        self.assertIn(b"BrokenPipeError", real.stderr)
        self.assertIn(b"raise BrokenPipeError", outcome.stderr)
        self.assertIn(b"raise BrokenPipeError", real.stderr)
        self.assertEqual(outcome.stderr.splitlines()[-1], real.stderr.splitlines()[-1])

    def test_a_raised_error_restores_cwd_environ_argv_stdio_umask_and_signals(self):
        directory = self._directory()
        elsewhere = directory / "elsewhere"
        elsewhere.mkdir()
        script = self._write(
            directory,
            "mutate.py",
            f"""
            import io
            import os
            import signal
            import sys
            def main(argv):
                os.chdir({str(elsewhere)!r})
                os.environ["BRIGADE_CALL_PROBE"] = "set"
                os.environ.pop("PATH", None)
                sys.argv.append("mutated")
                installed = ["replaced"]
                sys.argv = installed
                installed.append("tail")
                sys.stdin = io.StringIO("x")
                sys.stdout = io.StringIO()
                sys.stderr = io.StringIO()
                os.umask(0o077)
                signal.signal(signal.SIGUSR1, signal.SIG_IGN)
                sys.dont_write_bytecode = not sys.dont_write_bytecode
                raise RuntimeError("restore")
            """,
        )
        started = os.getcwd()
        argv_object = sys.argv
        argv_contents = list(sys.argv)
        stdin = sys.stdin
        stdout = sys.stdout
        stderr = sys.stderr
        bytecode = sys.dont_write_bytecode
        path = os.environ.get("PATH")
        signal.signal(signal.SIGUSR1, signal.SIG_DFL)
        outer_umask = os.umask(0o022)
        try:
            outcome = call(self, script, [])
            self.assertEqual(outcome.code, 1)
            self.assertEqual(os.getcwd(), started)
            self.assertIs(sys.argv, argv_object)
            self.assertEqual(list(sys.argv), argv_contents)
            self.assertIs(sys.stdin, stdin)
            self.assertIs(sys.stdout, stdout)
            self.assertIs(sys.stderr, stderr)
            self.assertNotIn("BRIGADE_CALL_PROBE", os.environ)
            self.assertEqual(os.environ.get("PATH"), path)
            self.assertEqual(signal.getsignal(signal.SIGUSR1), signal.SIG_DFL)
            self.assertEqual(sys.dont_write_bytecode, bytecode)
            current = os.umask(0o022)
            os.umask(current)
            self.assertEqual(current, 0o022)
        finally:
            os.chdir(started)
            os.environ.pop("BRIGADE_CALL_PROBE", None)
            if path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = path
            signal.signal(signal.SIGUSR1, signal.SIG_DFL)
            sys.dont_write_bytecode = bytecode
            if sys.argv is not argv_object:
                sys.argv = argv_object
            argv_object[:] = argv_contents
            sys.stdin = stdin
            sys.stdout = stdout
            sys.stderr = stderr
            os.umask(outer_umask)

    def test_the_next_test_loads_the_script_again(self):
        self._in_process()
        directory = self._directory()
        log = directory / "log.txt"
        script = self._write(
            directory,
            "once.py",
            f"""
            import sys
            from pathlib import Path
            log = Path({str(log)!r})
            with log.open("a", encoding="utf-8") as handle:
                handle.write("imported\\n")
            def main(argv):
                return 0
            """,
        )
        holder = unittest.TestCase()
        other = unittest.TestCase()
        try:
            call(holder, script, [])
            call(holder, script, [])
            self.assertEqual(log.read_text(encoding="utf-8"), "imported\n")
            call(other, script, [])
            self.assertEqual(log.read_text(encoding="utf-8"), "imported\nimported\n")
            self.assertTrue(holder.doCleanups())
            call(holder, script, [])
            self.assertEqual(log.read_text(encoding="utf-8"), "imported\nimported\nimported\n")
        finally:
            holder.doCleanups()
            other.doCleanups()

    def test_a_cached_function_runs_again_on_the_next_call(self):
        self._in_process()
        directory = self._directory()
        script = self._write(
            directory,
            "cached.py",
            """
            import functools
            import sys
            count = 0
            @functools.cache
            def roles_family():
                global count
                count += 1
                return count
            def run(argv):
                return 0
            def main(argv):
                print(roles_family())
            """,
        )
        first = call(self, script, [])
        second = call(self, script, [])
        self.assertEqual(first.stdout, b"1\n")
        self.assertEqual(second.stdout, b"2\n")
        self.assertEqual(first.code, 0)
        self.assertEqual(second.code, 0)

    def test_a_call_after_the_switch_is_set_runs_in_another_process(self):
        directory = self._directory()
        script = self._write(
            directory,
            "pid.py",
            """
            import os
            import sys
            def main(argv):
                print(os.getpid())
            """,
        )
        name = "PSTACK_TEST_REAL_PROCESS"
        self._in_process()
        first = call(self, script, [])
        self.assertEqual(first.code, 0, first.stderr)
        self.assertEqual(first.stdout_text().strip(), str(os.getpid()))
        os.environ[name] = "1"
        second = call(self, script, [])
        self.assertEqual(second.code, 0, second.stderr)
        self.assertNotEqual(second.stdout_text().strip(), str(os.getpid()))
        os.environ.pop(name, None)
        third = call(self, script, [])
        self.assertEqual(third.code, 0, third.stderr)
        self.assertEqual(third.stdout_text().strip(), str(os.getpid()))
