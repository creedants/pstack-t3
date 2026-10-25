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

import os
import pwd
import py_compile
import shutil
import sqlite3
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from tests import in_process
from tests.isolation_tests.fixtures import ROOT, outside


def real_home():
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def snapshot(path):
    """Return walked entries with mode, modification time, and contents only where os.path.isfile accepts the entry."""
    found = {}
    for top, dirs, files in os.walk(path):
        for name in dirs + files:
            entry = os.path.join(top, name)
            info = os.lstat(entry)
            data = Path(entry).read_bytes() if name in files and os.path.isfile(entry) else None
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
        self.target = scratch / "target"
        self.target.write_text("x")
        self.chown_links = {name: self.protected / f"{name.split('.')[1]}-link"
                            for name in ("os.chown", "os.lchown")}
        for link in self.chown_links.values():
            link.symlink_to(self.target)
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
    def test_each_write_through_a_directory_descriptor_a_mode_time_or_owner_change_a_new_entry_or_a_database_is_refused(self):
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
            "os.mkfifo": lambda: os.mkfifo(self.protected / "fifo"),
            "os.mknod": lambda: os.mknod(self.protected / "node"),
            "os.chown": lambda: os.chown(self.protected / "file", os.getuid(), os.getgid()),
            "os.chown with dir_fd": lambda: os.chown("file", os.getuid(), os.getgid(), dir_fd=fd),
            "sqlite3.connect": lambda: sqlite3.connect(self.protected / "db.sqlite"),
            "sqlite3.connect uri": lambda: sqlite3.connect(uri, uri=True),
        }
        for name, change in cases.items():
            with self.subTest(name):
                with self.assertRaises(PermissionError):
                    change()
                self.assertUnchanged()
        sqlite3.connect(":memory:").close()

    def test_a_hard_link_in_the_run_root_to_a_protected_file_is_refused_so_no_write_reaches_it(self):
        alias = self.link.parent / "alias"
        with self.assertRaises(PermissionError):
            os.link(self.protected / "file", alias)
        self.assertFalse(os.path.lexists(alias))
        self.assertUnchanged()

    def test_chown_without_following_a_link_is_refused_on_a_protected_symlink_and_leaves_its_ctime(self):
        target_before = self.target.lstat()
        cases = {"os.chown": lambda link: os.chown(link, os.getuid(), os.getgid(),
                                                   follow_symlinks=False)}
        if getattr(os, "lchown", None) is not None:
            cases["os.lchown"] = lambda link: os.lchown(link, os.getuid(), os.getgid())
        for name, change in cases.items():
            with self.subTest(name):
                link = self.chown_links[name]
                before = link.lstat()
                with self.assertRaises(PermissionError):
                    change(link)
                after = link.lstat()
                self.assertEqual((after.st_ctime_ns, after.st_uid, after.st_gid),
                                 (before.st_ctime_ns, before.st_uid, before.st_gid))
                self.assertEqual(os.readlink(link), str(self.target))
        self.assertEqual((self.target.lstat().st_ctime_ns, self.target.lstat().st_uid,
                          self.target.lstat().st_gid),
                         (target_before.st_ctime_ns, target_before.st_uid, target_before.st_gid))


class OwnershipTest(unittest.TestCase):
    def test_lchown_on_an_unprotected_symlink_in_the_run_root_succeeds(self):
        if getattr(os, "lchown", None) is None:
            self.skipTest("os.lchown is not available on this platform")
        scratch = Path(tempfile.mkdtemp(prefix="lchown-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        target = scratch / "target"
        target.write_text("x")
        link = scratch / "link"
        link.symlink_to(target)
        os.lchown(link, os.getuid(), os.getgid())
        self.assertEqual(os.readlink(link), str(target))

    def test_chown_on_a_descriptor_inside_the_run_root_is_not_refused(self):
        if os.chown not in os.supports_fd:
            self.skipTest("os.chown does not accept a descriptor on this platform")
        scratch = Path(tempfile.mkdtemp(prefix="chown-fd-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        target = scratch / "file"
        target.write_text("x")
        fd = os.open(target, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        os.chown(fd, os.getuid(), os.getgid())


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
