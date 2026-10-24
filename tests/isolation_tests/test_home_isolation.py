"""A test process cannot delete or write anything under the user's real home."""
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

if __package__ in (None, ""):
    # The runner can be pointed at this directory alone, which never imports the area package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests import isolation

isolation.install()

ROOT = Path(__file__).resolve().parents[2]


def real_home():
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


class PathVerdictTest(unittest.TestCase):
    def test_a_path_object_is_judged_inside_and_a_resolved_cleanup_deletes_only_the_run_root_copy(self):
        state = Path(os.environ["XDG_STATE_HOME"]).resolve()
        real_state = real_home() / ".local" / "state"

        def under(path):
            if not isinstance(path, (str, bytes, os.PathLike)):
                return None
            return isolation.inside(path)

        self.assertIs(under(state), True)
        self.assertIs(under(str(state)), True)
        self.assertIs(under(os.fsencode(state)), True)
        self.assertIs(under(real_state), False)
        self.assertIsNotNone(under(real_state))

        found = state / "pstack-t3" / "brigade" / "pstack-t3" / "pstack-t3"
        found.mkdir(parents=True, exist_ok=True)
        (found / "keep").write_text("x")
        self.addCleanup(shutil.rmtree, found, True)
        self.assertIs(isolation.inside(found.resolve()), True)
        self.assertIs(isolation.inside(real_state), False)


class DeleteOutsideTest(unittest.TestCase):
    def outside(self):
        path = Path(tempfile.mkdtemp(prefix="outside-", dir=isolation.root().parent))
        self.addCleanup(subprocess.run, [sys.executable, "-c", "import shutil, sys; shutil.rmtree(sys.argv[1], True)", str(path)],
                        check=False)
        (path / "file").write_text("x")
        (path / "directory").mkdir()
        return path

    def test_every_delete_outside_the_run_root_is_refused_and_leaves_the_target_alone(self):
        cases = {
            "shutil.rmtree": lambda path: shutil.rmtree(path),
            "shutil.rmtree ignore_errors": lambda path: shutil.rmtree(path, True),
            "os.remove": lambda path: os.remove(path / "file"),
            "os.unlink": lambda path: os.unlink(path / "file"),
            "Path.unlink": lambda path: (path / "file").unlink(),
            "os.rmdir": lambda path: os.rmdir(path / "directory"),
            "Path.rmdir": lambda path: (path / "directory").rmdir(),
        }
        for name, delete in cases.items():
            with self.subTest(name):
                path = self.outside()
                self.assertFalse(isolation.inside(path))
                with self.assertRaises(PermissionError):
                    delete(path)
                self.assertTrue(path.is_dir(), name)
                self.assertEqual((path / "file").read_text(), "x", name)
                self.assertTrue((path / "directory").is_dir(), name)


class EnvironmentVariableTest(unittest.TestCase):
    def test_pointing_home_or_an_xdg_name_at_the_real_home_is_refused_and_keeps_the_isolated_value(self):
        for name in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
            with self.subTest(name):
                before = os.environ[name]
                with self.assertRaises(PermissionError):
                    os.environ[name] = str(real_home())
                self.assertEqual(os.environ[name], before)


class ChildActivationTest(unittest.TestCase):
    def test_a_child_handed_the_real_home_as_its_run_root_exits_non_zero_and_runs_no_test_body(self):
        code = "\n".join([
            "import sys",
            f"sys.path.insert(0, {str(ROOT)!r})",
            "from tests import isolation",
            "isolation.install()",
            "print('BODY RAN')",
        ])
        env = dict(os.environ, PSTACK_TEST_HOME_ROOT=str(real_home()))
        done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("BODY RAN", done.stdout)
        self.assertIn("pstack-t3 test isolation:", done.stderr)


class RealStateTest(unittest.TestCase):
    def test_a_create_inside_the_real_state_directory_is_refused_and_leaves_no_directory(self):
        real_state = real_home() / ".local" / "state"
        self.assertFalse(isolation.inside(real_state))
        probe = real_state / f".pstack-t3-isolation-create-probe-{os.getpid()}"
        try:
            with self.assertRaises(PermissionError):
                probe.mkdir()
        finally:
            if probe.is_dir():
                try:
                    probe.rmdir()
                except OSError:
                    pass
        self.assertFalse(probe.exists())
