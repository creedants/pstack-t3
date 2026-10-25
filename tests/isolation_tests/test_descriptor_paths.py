"""A descriptor's path without /proc, and the refusal of every operation whose descriptor has none.

Each case runs in its own child. A child hides `/proc/self/fd`, records every descriptor opened
through `os.open` with its real path, and answers `F_GETPATH` from that record. The guard's own
`os.stat` and `os.fstat` identity check still runs, so a lying answer would be rejected. The case that
pins the `/proc` branch keeps `/proc` visible and skips that simulation.
"""

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
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.isolation_tests.fixtures import ROOT

HIDE = [
    "import os",
    "_real_readlink = os.readlink",
    "def without_procfs(path, *args, **kwargs):",
    "    if os.fsdecode(path).startswith('/proc/self/fd/'):",
    "        raise FileNotFoundError(2, 'No such file or directory', os.fsdecode(path))",
    "    return _real_readlink(path, *args, **kwargs)",
    "os.readlink = without_procfs",
]

HEAD = [
    "import errno",
    "import fcntl",
    "import os",
    "import pwd",
    "import shutil",
    "import sys",
    f"sys.path.insert(0, {str(ROOT)!r})",
    "from tests import isolation",
    "root = isolation.install()",
    'print("ROOT", root)',
]

RECORDER = [
    "guarded_open = os.open",
    "_real_close = os.close",
    "_recorded = {}",
    "def _recording_open(path, flags, mode=0o777, *, dir_fd=None):",
    "    number = guarded_open(path, flags, mode, dir_fd=dir_fd)",
    "    name = os.fsdecode(os.fspath(path))",
    "    parent = _recorded.get(dir_fd)",
    "    if isinstance(dir_fd, int) and dir_fd >= 0 and not os.path.isabs(name):",
    "        if parent is not None:",
    "            _recorded[number] = os.path.realpath(os.path.join(parent, name))",
    "    else:",
    "        _recorded[number] = os.path.realpath(name)",
    "    return number",
    "def _recording_close(number):",
    "    _recorded.pop(number, None)",
    "    return _real_close(number)",
    "os.open = _recording_open",
    "os.close = _recording_close",
    "os.supports_dir_fd.add(_recording_open)",
]

SIMULATION = [
    "_real_fcntl = fcntl.fcntl",
    "fcntl.F_GETPATH = 9999",
    "def _simulated_getpath(number, command, buffer):",
    "    if command != 9999:",
    "        return _real_fcntl(number, command, buffer)",
    "    assert isinstance(buffer, bytes) and len(buffer) == 1024, (type(buffer), len(buffer))",
    "    path = _recorded.get(number)",
    "    if path is None:",
    "        raise OSError(errno.EINVAL, 'Inappropriate ioctl for device')",
    "    answer = path.encode()",
    "    return answer + b'\\0' * (1024 - len(answer))",
    "fcntl.fcntl = _simulated_getpath",
]

WRITE = [
    "link = os.path.join(str(root), 'alias')",
    "os.symlink(str(root), link)",
    "with open(os.path.join(link, 'written.txt'), 'w') as handle:",
    "    handle.write('ok')",
    "with open(os.path.join(link, 'written.txt')) as handle:",
    "    assert handle.read() == 'ok'",
    "print('WROTE THROUGH ALIAS')",
    "tree = os.path.join(str(root), 'deep', 'leaf')",
    "os.makedirs(tree)",
    "with open(os.path.join(tree, 'inner.txt'), 'w') as handle:",
    "    handle.write('x')",
    "shutil.rmtree(os.path.join(str(root), 'deep'))",
    'print("TREE", tree, "GONE", not os.path.lexists(os.path.join(str(root), "deep")))',
]

REFUSE = WRITE + [
    "beside = os.path.join(os.path.dirname(str(root)), 'beside')",
    "os.mkdir(beside)",
    "with open(os.path.join(beside, 'kept.txt'), 'w') as handle:",
    "    handle.write('kept')",
    "fd = os.open(beside, os.O_RDONLY)",
    "try:",
    "    os.unlink('kept.txt', dir_fd=fd)",
    "except PermissionError:",
    "    print('REFUSED DELETE BESIDE ROOT')",
    "else:",
    "    print('DELETED BESIDE ROOT')",
    "os.close(fd)",
    "assert os.path.lexists(os.path.join(beside, 'kept.txt'))",
    "print('KEPT BESIDE ROOT')",
    "home = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)",
    "os.symlink(home, os.path.join(str(root), 'home-link'))",
    "try:",
    "    os.open(os.path.join(str(root), 'home-link', 'pstack-d11-probe'), os.O_WRONLY | os.O_CREAT, 0o600)",
    "except PermissionError:",
    "    print('REFUSED WRITE IN REAL HOME')",
    "else:",
    "    print('WROTE IN REAL HOME')",
]

DELETE_INSIDE = [
    "sub = os.path.join(str(root), 'sub')",
    "os.makedirs(sub)",
    "goner = os.path.join(sub, 'gone.txt')",
    "with open(goner, 'w') as handle:",
    "    handle.write('x')",
    "fd = os.open(sub, os.O_RDONLY)",
    "try:",
    "    os.unlink('gone.txt', dir_fd=fd)",
    "finally:",
    "    os.close(fd)",
    'print("DELETED", not os.path.lexists(goner))',
    "tree = os.path.join(str(root), 'deep', 'leaf')",
    "os.makedirs(tree)",
    "with open(os.path.join(tree, 'inner.txt'), 'w') as handle:",
    "    handle.write('x')",
    "shutil.rmtree(os.path.join(str(root), 'deep'))",
    'print("TREE GONE", not os.path.lexists(os.path.join(str(root), "deep")))',
]

DELETE_PROTECTED = [
    "guarded = os.path.join(str(root), 'guarded')",
    "os.makedirs(guarded)",
    "kept = os.path.join(guarded, 'kept.txt')",
    "with open(kept, 'w') as handle:",
    "    handle.write('kept')",
    "with isolation.protecting(guarded):",
    "    fd = os.open(guarded, os.O_RDONLY)",
    "    try:",
    "        os.unlink('kept.txt', dir_fd=fd)",
    "    except PermissionError:",
    "        print('REFUSED PROTECTED DELETE')",
    "    else:",
    "        print('DELETED PROTECTED')",
    "    finally:",
    "        os.close(fd)",
    'print("KEPT PROTECTED", os.path.lexists(kept))',
]

DELETE_UNPLACED = [
    "sub = os.path.join(str(root), 'sub')",
    "os.makedirs(sub)",
    "kept = os.path.join(sub, 'kept.txt')",
    "with open(kept, 'w') as handle:",
    "    handle.write('kept')",
    "fd = guarded_open(sub, os.O_RDONLY)",
    "try:",
    "    os.unlink('kept.txt', dir_fd=fd)",
    "except PermissionError:",
    "    print('REFUSED UNPLACED DELETE')",
    "else:",
    "    print('DELETED UNPLACED')",
    "finally:",
    "    os.close(fd)",
    'print("KEPT INSIDE", os.path.lexists(kept))',
]

OUTSIDE = [
    "beside = os.path.dirname(str(root))",
    "outside = os.path.join(beside, 'outside.txt')",
    "with open(outside, 'w') as handle:",
    "    handle.write('0123456789abcdefg')",
    "fd = os.open(outside, os.O_RDWR)",
    "try:",
    "    os.ftruncate(fd, 0)",
    "except PermissionError:",
    "    print('REFUSED OUTSIDE TRUNCATE')",
    "else:",
    "    print('TRUNCATED OUTSIDE')",
    "finally:",
    "    os.close(fd)",
    'print("OUTSIDE SIZE", os.path.getsize(outside))',
    "d = os.open(beside, os.O_RDONLY)",
    "try:",
    "    os.mkdir('made', dir_fd=d)",
    "except PermissionError:",
    "    print('REFUSED OUTSIDE MKDIR')",
    "else:",
    "    print('MADE OUTSIDE')",
    "finally:",
    "    os.close(d)",
    "assert not os.path.lexists(os.path.join(beside, 'made')), 'the guard created made'",
    "print('NO MADE')",
    "inside = os.path.join(str(root), 'inside.txt')",
    "with open(inside, 'w') as handle:",
    "    handle.write('x')",
    "fd = os.open(inside, os.O_RDWR)",
    "try:",
    "    os.ftruncate(fd, 0)",
    "    assert os.fstat(fd).st_size == 0, os.fstat(fd).st_size",
    "finally:",
    "    os.close(fd)",
    "print('TRUNCATED INSIDE')",
]


CHANGING_HEAD = [
    "protected = os.path.join(str(root), 'protected')",
    "os.makedirs(protected)",
    "unexpected = os.path.join(protected, 'unexpected-entry')",
    "class ChangingPath:",
    "    def __init__(self):",
    "        self.calls = 0",
    "    def __fspath__(self):",
    "        self.calls += 1",
    "        return 'safe-entry' if self.calls < 2 else unexpected",
    "fd = os.open(str(root), os.O_RDONLY)",
    "try:",
    "    with isolation.protecting(protected):",
]

CHANGING_TAIL = [
    "except PermissionError:",
    "    print('REFUSED PERMITTED NAME')",
    "else:",
    "    print('CREATED PERMITTED NAME')",
    "finally:",
    "    os.close(fd)",
    "print('SAFE ENTRY', os.path.lexists(os.path.join(str(root), 'safe-entry')))",
    "print('PROTECTED ENTRY', os.path.lexists(unexpected))",
]

MKNOD_PROBE = [
    "import stat",
    "probe = os.path.join(str(root), 'mknod-probe')",
    "try:",
    "    os.mknod(probe, stat.S_IFREG | 0o600)",
    "except OSError:",
    "    print('MKNOD UNSUPPORTED')",
    "    raise SystemExit(0)",
    "os.unlink(probe)",
    "print('MKNOD SUPPORTED')",
]


def changing(call):
    """Return a child body that runs call with a path object that changes after its first read."""
    return CHANGING_HEAD + ["        " + call] + CHANGING_TAIL


def root_of(done):
    """Return the run root a child printed on its first line."""
    head = done.stdout.splitlines()[0]
    assert head.startswith("ROOT "), head
    return Path(head.split(" ", 1)[1])


class DescriptorPathTest(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="descriptor-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.real = self.scratch / "real"
        self.real.mkdir()
        self.alias = self.scratch / "alias"
        self.alias.symlink_to(self.real)
        self.env = {key: value for key, value in os.environ.items()
                    if key not in (isolation.POINTER, isolation.TOKEN, isolation.ORIGINALS)}
        self.env["TMPDIR"] = str(self.alias)

    def child(self, body, simulate=True):
        """Return the source of a child that hides /proc/self/fd when simulate is true, makes its run root under the alias's real target, and runs body."""
        under = f"assert str(root).startswith({os.path.realpath(self.real)!r} + os.sep), root"
        lines = (HIDE if simulate else []) + HEAD + RECORDER + (SIMULATION if simulate else []) + [under] + body
        return "\n".join(lines)

    def run_child(self, body, simulate=True):
        return subprocess.run([sys.executable, "-c", self.child(body, simulate)], env=self.env,
                              capture_output=True, text=True, cwd=str(self.scratch))

    def test_a_process_without_procfs_writes_and_removes_inside_its_run_root_through_a_symlinked_tmpdir(self):
        done = self.run_child(WRITE)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.startswith("ROOT " + os.path.realpath(self.real) + os.sep), done.stdout)
        self.assertIn("WROTE THROUGH ALIAS", done.stdout)
        self.assertIn("GONE True", done.stdout)
        self.assertFalse((self.real / "deep").exists())

    def test_a_process_without_procfs_still_refuses_a_remove_outside_the_run_root_and_a_protected_root_through_an_alias(self):
        done = self.run_child(REFUSE)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("WROTE THROUGH ALIAS", done.stdout)
        self.assertIn("GONE True", done.stdout)
        self.assertIn("REFUSED DELETE BESIDE ROOT", done.stdout)
        self.assertIn("KEPT BESIDE ROOT", done.stdout)
        self.assertIn("REFUSED WRITE IN REAL HOME", done.stdout)
        self.assertNotIn("DELETED BESIDE ROOT", done.stdout)
        self.assertNotIn("WROTE IN REAL HOME", done.stdout)
        self.assertTrue((self.real / "beside" / "kept.txt").is_file())
        home = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
        self.assertFalse(os.path.lexists(os.path.join(home, "pstack-d11-probe")))

    def test_a_delete_inside_the_run_root_through_a_directory_descriptor_passes(self):
        done = self.run_child(DELETE_INSIDE)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("DELETED True", done.stdout)
        self.assertIn("TREE GONE True", done.stdout)
        root = root_of(done)
        self.assertFalse(os.path.lexists(root / "sub" / "gone.txt"))
        self.assertFalse(os.path.lexists(root / "deep"))

    def test_a_delete_inside_a_protected_root_inside_the_run_root_through_a_directory_descriptor_is_refused(self):
        done = self.run_child(DELETE_PROTECTED)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("REFUSED PROTECTED DELETE", done.stdout)
        self.assertIn("KEPT PROTECTED True", done.stdout)
        self.assertNotIn("DELETED PROTECTED", done.stdout)
        self.assertTrue(os.path.lexists(root_of(done) / "guarded" / "kept.txt"))

    def test_a_delete_through_a_descriptor_that_resolves_through_neither_is_refused_even_inside_the_run_root(self):
        done = self.run_child(DELETE_UNPLACED)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("REFUSED UNPLACED DELETE", done.stdout)
        self.assertIn("KEPT INSIDE True", done.stdout)
        self.assertNotIn("DELETED UNPLACED", done.stdout)
        self.assertTrue(os.path.lexists(root_of(done) / "sub" / "kept.txt"))

    def test_a_truncate_and_a_mkdir_placed_outside_the_run_root_are_refused_and_a_truncate_inside_passes(self):
        done = self.run_child(OUTSIDE)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("REFUSED OUTSIDE TRUNCATE", done.stdout)
        self.assertIn("OUTSIDE SIZE 17", done.stdout)
        self.assertIn("REFUSED OUTSIDE MKDIR", done.stdout)
        self.assertIn("NO MADE", done.stdout)
        self.assertIn("TRUNCATED INSIDE", done.stdout)
        self.assertNotIn("TRUNCATED OUTSIDE", done.stdout)
        self.assertNotIn("MADE OUTSIDE", done.stdout)
        self.assertEqual((self.real / "outside.txt").stat().st_size, 17)
        self.assertFalse(os.path.lexists(self.real / "made"))

    @unittest.skipUnless(os.path.isdir("/proc/self/fd"), "this host has no /proc/self/fd")
    def test_the_same_outsides_are_refused_through_the_proc_branch_with_proc_visible(self):
        done = self.run_child(OUTSIDE, simulate=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("REFUSED OUTSIDE TRUNCATE", done.stdout)
        self.assertIn("OUTSIDE SIZE 17", done.stdout)
        self.assertIn("REFUSED OUTSIDE MKDIR", done.stdout)
        self.assertIn("NO MADE", done.stdout)
        self.assertIn("TRUNCATED INSIDE", done.stdout)
        self.assertNotIn("TRUNCATED OUTSIDE", done.stdout)
        self.assertNotIn("MADE OUTSIDE", done.stdout)
        self.assertEqual((self.real / "outside.txt").stat().st_size, 17)
        self.assertFalse(os.path.lexists(self.real / "made"))

    @unittest.skipUnless(isolation._os_mkfifo in os.supports_dir_fd,
                         "this platform's os.mkfifo takes no dir_fd")
    def test_a_mkfifo_path_that_changes_after_its_first_read_creates_only_the_permitted_entry(self):
        done = self.run_child(changing("os.mkfifo(ChangingPath(), 0o600, dir_fd=fd)"))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("CREATED PERMITTED NAME", done.stdout)
        self.assertIn("SAFE ENTRY True", done.stdout)
        self.assertIn("PROTECTED ENTRY False", done.stdout)
        self.assertNotIn("REFUSED PERMITTED NAME", done.stdout)
        root = root_of(done)
        self.assertTrue(os.path.lexists(root / "safe-entry"))
        self.assertEqual(os.listdir(root / "protected"), [])

    @unittest.skipUnless(isolation._os_mknod is not None and isolation._os_mknod in os.supports_dir_fd,
                         "this platform's os.mknod takes no dir_fd")
    def test_a_mknod_path_that_changes_after_its_first_read_creates_only_the_permitted_entry(self):
        done = self.run_child(MKNOD_PROBE + changing("os.mknod(ChangingPath(), stat.S_IFREG | 0o600, dir_fd=fd)"))
        self.assertEqual(done.returncode, 0, done.stderr)
        if "MKNOD UNSUPPORTED" in done.stdout:
            raise unittest.SkipTest("this host refuses a regular-file os.mknod")
        self.assertIn("MKNOD SUPPORTED", done.stdout)
        self.assertIn("CREATED PERMITTED NAME", done.stdout)
        self.assertIn("SAFE ENTRY True", done.stdout)
        self.assertIn("PROTECTED ENTRY False", done.stdout)
        self.assertNotIn("REFUSED PERMITTED NAME", done.stdout)
        root = root_of(done)
        self.assertTrue(os.path.lexists(root / "safe-entry"))
        self.assertEqual(os.listdir(root / "protected"), [])
