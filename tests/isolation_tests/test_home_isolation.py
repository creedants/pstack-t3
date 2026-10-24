"""A test process cannot delete outside its run root, or write inside a protected real root outside this checkout."""

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import json
import os
import pwd
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from tests import in_process

ROOT = Path(__file__).resolve().parents[2]
_outside = None


def real_home():
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def outside():
    """Return one directory beside the run root, made once per process and left in place, holding a file and a directory."""
    global _outside
    if _outside is None:
        _outside = Path(tempfile.mkdtemp(prefix="outside-", dir=isolation.root().parent))
        (_outside / "file").write_text("x")
        (_outside / "directory").mkdir()
        (_outside / "tree" / "leaf").mkdir(parents=True)
    return _outside


def snapshot(path):
    """Return every name under path with its mode, modification time, and file contents."""
    found = {}
    for top, dirs, files in os.walk(path):
        for name in dirs + files:
            entry = os.path.join(top, name)
            info = os.lstat(entry)
            data = Path(entry).read_bytes() if name in files else None
            found[os.path.relpath(entry, path)] = (info.st_mode, info.st_mtime_ns, data)
    return found


class Fixture(unittest.TestCase):
    """A protected directory inside the run root, and a symlink to it, both removed when the test ends."""

    def setUp(self):
        scratch = Path(tempfile.mkdtemp(prefix="guard-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        self.protected = scratch / "protected"
        (self.protected / "directory").mkdir(parents=True)
        (self.protected / "empty").mkdir()
        (self.protected / "directory" / "file").write_text("kept")
        (self.protected / "file").write_text("x")
        self.link = scratch / "link"
        self.link.symlink_to(self.protected)
        self.before = snapshot(self.protected)
        guard = isolation.protecting(self.protected)
        guard.__enter__()
        self.addCleanup(guard.__exit__, None, None, None)

    def assertUnchanged(self):
        self.assertEqual(snapshot(self.protected), self.before)


class PathVerdictTest(unittest.TestCase):
    def test_a_path_object_is_judged_inside_and_a_symlink_out_of_the_root_is_judged_outside(self):
        state = Path(os.environ["XDG_STATE_HOME"])
        found = state / "pstack-t3" / "brigade"
        found.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, found, True)
        escape = found / "escape"
        escape.symlink_to(outside())
        for value in (found, str(found), os.fsencode(found)):
            with self.subTest(type(value).__name__):
                self.assertIs(isolation.inside(value), True)
        self.assertIs(isolation.inside(outside()), False)
        self.assertIs(isolation.inside(escape), False)
        self.assertIs(isolation.inside(escape / "file"), False)


class DeleteOutsideTest(unittest.TestCase):
    def test_every_delete_outside_the_run_root_is_refused_and_leaves_the_target_alone(self):
        path = outside()
        before = snapshot(path)
        cases = {
            "shutil.rmtree": lambda: shutil.rmtree(path),
            "shutil.rmtree ignore_errors": lambda: shutil.rmtree(path, True),
            "os.remove": lambda: os.remove(path / "file"),
            "os.unlink": lambda: os.unlink(path / "file"),
            "Path.unlink": lambda: (path / "file").unlink(),
            "os.rmdir": lambda: os.rmdir(path / "directory"),
            "Path.rmdir": lambda: (path / "directory").rmdir(),
            "os.removedirs": lambda: os.removedirs(path / "tree" / "leaf"),
        }
        for name, delete in cases.items():
            with self.subTest(name):
                self.assertFalse(isolation.inside(path))
                with self.assertRaises(PermissionError):
                    delete()
                self.assertEqual(snapshot(path), before, name)


class SymlinkEscapeTest(Fixture):
    def test_a_symlink_in_the_run_root_to_a_protected_directory_refuses_each_change_through_it(self):
        cases = {
            "write": lambda: (self.link / "file").write_text("changed"),
            "create": lambda: (self.link / "new").write_text("y"),
            "unlink": lambda: (self.link / "file").unlink(),
            "rmdir": lambda: (self.link / "empty").rmdir(),
            "rmtree": lambda: shutil.rmtree(self.link / "directory"),
            "rmtree of the link with a slash": lambda: shutil.rmtree(f"{self.link}/"),
        }
        for name, change in cases.items():
            with self.subTest(name):
                with self.assertRaises(PermissionError):
                    change()
                self.assertUnchanged()



class FakeStateTest(unittest.TestCase):
    def test_a_create_inside_a_protected_fake_state_directory_is_refused_and_leaves_no_directory(self):
        home = Path(tempfile.mkdtemp(prefix="fake-home-"))
        self.addCleanup(shutil.rmtree, home, True)
        state = home / ".local" / "state"
        state.mkdir(parents=True)
        with isolation.protecting(state):
            for name, create in {
                "Path.mkdir": lambda: (state / "pstack-t3").mkdir(),
                "os.makedirs": lambda: os.makedirs(state / "pstack-t3" / "brigade"),
            }.items():
                with self.subTest(name):
                    with self.assertRaises(PermissionError):
                        create()
                    self.assertEqual(os.listdir(state), [])


class DescriptorAndModeTest(Fixture):
    def test_each_write_through_a_directory_descriptor_a_mode_or_time_change_or_a_database_is_refused(self):
        fd = os.open(self.protected, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        uri = "file:" + urllib.parse.quote(str(self.protected / "uri.sqlite")) + "?mode=rwc"
        cases = {
            "os.open truncate with dir_fd": lambda: os.open("file", os.O_WRONLY | os.O_TRUNC, dir_fd=fd),
            "os.open create with dir_fd": lambda: os.open("new", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=fd),
            "os.unlink with dir_fd": lambda: os.unlink("file", dir_fd=fd),
            "os.rmdir with dir_fd": lambda: os.rmdir("empty", dir_fd=fd),
            "os.mkdir with dir_fd": lambda: os.mkdir("made", dir_fd=fd),
            "os.rename with dir_fd": lambda: os.rename("file", "moved", src_dir_fd=fd, dst_dir_fd=fd),
            "os.chmod": lambda: os.chmod(self.protected / "file", 0o600),
            "os.chmod through the link": lambda: os.chmod(self.link / "file", 0o600),
            "os.utime": lambda: os.utime(self.protected / "file", (1, 1)),
            "sqlite3.connect": lambda: sqlite3.connect(self.protected / "db.sqlite"),
            "sqlite3.connect uri": lambda: sqlite3.connect(uri, uri=True),
        }
        for name, change in cases.items():
            with self.subTest(name):
                with self.assertRaises(PermissionError):
                    change()
                self.assertUnchanged()
        sqlite3.connect(":memory:").close()


class RenameTest(unittest.TestCase):
    def test_a_rename_that_removes_a_path_outside_the_run_root_is_refused_and_both_paths_stay(self):
        cache = ROOT / "tests" / "isolation_tests" / "__pycache__"
        cache.mkdir(exist_ok=True)
        source, dest = cache / "rename-source.txt", cache / "rename-dest.txt"
        source.write_text("source")
        dest.write_text("dest")
        scratch = Path(tempfile.mkdtemp(prefix="rename-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        before = snapshot(outside())
        cases = {
            "shutil.move into the root": lambda: shutil.move(str(outside() / "directory"), str(scratch / "moved")),
            "os.replace between checkout files": lambda: os.replace(source, dest),
            "os.renames of an outside tree": lambda: os.renames(outside() / "tree" / "leaf", scratch / "a" / "leaf"),
        }
        for name, move in cases.items():
            with self.subTest(name):
                with self.assertRaises(PermissionError):
                    move()
                self.assertEqual(snapshot(outside()), before, name)
                self.assertEqual((source.read_text(), dest.read_text()), ("source", "dest"), name)

    def test_a_rename_inside_the_run_root_moves_the_file(self):
        scratch = Path(tempfile.mkdtemp(prefix="rename-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        (scratch / "a").write_text("a")
        (scratch / "b").write_text("b")
        os.rename(scratch / "a", scratch / "c")
        os.replace(scratch / "c", scratch / "b")
        self.assertEqual(sorted(os.listdir(scratch)), ["b"])
        self.assertEqual((scratch / "b").read_text(), "a")

    def test_compiling_a_checkout_module_writes_its_bytecode_under_the_run_root_and_leaves_no_temporary_file_in_the_checkout(self):
        cache = Path(__file__).parent / "__pycache__"
        before = sorted(os.listdir(cache)) if cache.is_dir() else []
        written = py_compile.compile(__file__, doraise=True)
        self.assertTrue(isolation.inside(written), written)
        self.assertEqual(sorted(os.listdir(cache)) if cache.is_dir() else [], before)


class InProcessTest(unittest.TestCase):
    def test_an_in_process_run_whose_environment_drops_a_guarded_name_sees_it_absent(self):
        scratch = Path(tempfile.mkdtemp(prefix="in-process-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        program = scratch / "show.py"
        program.write_text("import os\nprint(os.environ.get('XDG_CONFIG_HOME'))\n")
        env = {key: value for key, value in os.environ.items() if key != "XDG_CONFIG_HOME"}
        done = in_process.invoke(in_process.Invocation(program=program, env=env))
        self.assertEqual((done.returncode, done.stdout), (0, "None\n"), done.stderr)
        self.assertTrue(isolation.inside(os.environ["XDG_CONFIG_HOME"]))


class EnvironmentVariableTest(Fixture):
    def test_removing_or_emptying_a_home_variable_or_pointing_it_at_a_protected_root_is_refused_and_keeps_the_value(self):
        values = {"empty": "", "relative": ".", "real home": str(real_home()), "symlink to a protected root": str(self.link)}
        for name in ("HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "TMPDIR"):
            before = os.environ[name]
            for label, value in values.items():
                with self.subTest(name=name, value=label):
                    with self.assertRaises(PermissionError):
                        os.environ[name] = value
                    self.assertEqual(os.environ[name], before)
            for label, remove in {"del": lambda: os.environ.__delitem__(name), "pop": lambda: os.environ.pop(name),
                                  "os.unsetenv": lambda: os.unsetenv(name)}.items():
                with self.subTest(name=name, remove=label):
                    with self.assertRaises(PermissionError):
                        remove()
                    self.assertEqual(os.environ[name], before)

    def test_clear_keeps_the_home_variables_so_a_saved_environment_can_be_restored(self):
        saved = dict(os.environ)
        self.addCleanup(os.environ.update, saved)
        os.environ["PSTACK_GUARD_CLEAR_PROBE"] = "1"
        os.environ.clear()
        self.assertNotIn("PSTACK_GUARD_CLEAR_PROBE", os.environ)
        self.assertEqual(os.environ["HOME"], saved["HOME"])
        os.environ.update(saved)
        self.assertEqual(dict(os.environ), saved)


class RepeatInstallTest(unittest.TestCase):
    def test_a_repeat_install_returns_the_same_root(self):
        self.assertEqual(isolation.install(), isolation.root())
        self.assertIs(sys.modules["pstack_test_isolation"], isolation)

    def test_a_repeat_install_after_the_environment_is_changed_behind_the_guard_exits(self):
        cases = {"HOME": str(isolation.root().parent), "XDG_STATE_HOME": "state"}
        for name, value in cases.items():
            with self.subTest(name):
                key = os.environ.encodekey(name)
                before = os.environ._data[key]
                dict.__setitem__(os.environ._data, key, os.environ.encodevalue(value))
                try:
                    with self.assertRaises(SystemExit):
                        isolation.install()
                finally:
                    dict.__setitem__(os.environ._data, key, before)
        self.assertEqual(isolation.install(), isolation.root())


CHILD = "\n".join([
    "import sys",
    f"sys.path.insert(0, {str(ROOT)!r})",
    "from tests import isolation",
    "isolation.install()",
    "print('BODY RAN', isolation.root())",
])


class AdoptionTest(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="adopt-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def handed(self, root):
        """Return an environment that hands a child root, marked by this process, with every variable inside it."""
        root.mkdir()
        values = isolation.layout(root)
        for path in values.values():
            os.makedirs(path)
        env = dict(os.environ, **values)
        env[isolation.POINTER] = str(root)
        env[isolation.TOKEN] = isolation.mark(root)
        return env

    def rewrite(self, root, **fields):
        marker = root / isolation.MARKER
        marker.write_text(json.dumps(dict(json.loads(marker.read_text()), **fields)))

    def run_child(self, env):
        return subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True, text=True, cwd=self.scratch)

    def test_a_child_handed_a_root_it_cannot_prove_exits_non_zero_and_runs_no_test_body(self):
        ended = subprocess.Popen(["true"])
        ended.wait()
        cases = {}
        root = self.scratch / "stale"
        cases["stale pid"] = env = self.handed(root)
        self.rewrite(root, pid=ended.pid)
        root = self.scratch / "foreign"
        cases["foreign token"] = env = self.handed(root)
        env[isolation.TOKEN] = "0" * 32
        root = self.scratch / "uid"
        cases["different uid"] = self.handed(root)
        self.rewrite(root, uid=os.getuid() + 1)
        root = self.scratch / "home"
        cases["HOME outside the root"] = env = self.handed(root)
        env["HOME"] = str(self.scratch)
        for name, env in cases.items():
            with self.subTest(name):
                done = self.run_child(env)
                self.assertNotEqual(done.returncode, 0, done.stderr)
                self.assertNotIn("BODY RAN", done.stdout)
                self.assertIn("pstack-t3 test isolation:", done.stderr)

    def test_a_child_handed_a_marked_root_adopts_it(self):
        root = self.scratch / "valid"
        done = self.run_child(self.handed(root))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), ["BODY", "RAN", os.path.realpath(root)])

    def test_the_module_loaded_by_path_and_then_imported_as_tests_isolation_is_one_guard_with_one_root(self):
        code = "\n".join([
            "import sys",
            "from importlib.util import module_from_spec, spec_from_file_location",
            f"spec = spec_from_file_location('pstack_test_isolation', {str(ROOT / 'tests' / 'isolation.py')!r})",
            "first = module_from_spec(spec)",
            "sys.modules['pstack_test_isolation'] = first",
            "spec.loader.exec_module(first)",
            "root = first.install()",
            f"sys.path.insert(0, {str(ROOT)!r})",
            "from tests import isolation",
            "print(root, isolation.install(), isolation is first)",
        ])
        tmp = self.scratch / "tmp"
        tmp.mkdir()
        env = {key: value for key, value in os.environ.items() if key not in (isolation.POINTER, isolation.TOKEN)}
        env["TMPDIR"] = str(tmp)
        done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=self.scratch)
        self.assertEqual(done.returncode, 0, done.stderr)
        first, again, same = done.stdout.split()
        self.assertEqual((again, same), (first, "True"))
        self.assertTrue(first.startswith(os.path.realpath(tmp) + os.sep), first)

    def test_an_adopted_root_that_is_a_symlink_into_a_protected_root_exits(self):
        target = self.scratch / "target"
        env = self.handed(target)
        link = self.scratch / "link"
        link.symlink_to(target)
        env[isolation.POINTER] = str(link)
        env.update(isolation.layout(link))
        self.assertEqual(isolation._adopt(env, ()), os.path.realpath(target))
        with self.assertRaises(SystemExit):
            isolation._adopt(env, (os.path.realpath(target),))
