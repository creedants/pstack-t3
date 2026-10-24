"""A descriptor's path without /proc, and the delete-only proof for a directory descriptor whose path cannot be read."""

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

PREAMBLE = [
    "import os",
    "_real_readlink = os.readlink",
    "def without_procfs(path, *args, **kwargs):",
    "    if os.fsdecode(path).startswith('/proc/self/fd/'):",
    "        raise FileNotFoundError(2, 'No such file or directory', os.fsdecode(path))",
    "    return _real_readlink(path, *args, **kwargs)",
    "os.readlink = without_procfs",
    "import pwd",
    "import shutil",
    "import sys",
]

HEAD = [
    f"sys.path.insert(0, {str(ROOT)!r})",
    "from tests import isolation",
    "root = isolation.install()",
    'print("ROOT", root)',
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

    def child(self, body):
        """Return the source of a child that hides /proc/self/fd, makes its run root under the alias's real target, and runs body."""
        under = f"assert str(root).startswith({os.path.realpath(self.real)!r} + os.sep), root"
        return "\n".join(PREAMBLE + HEAD + [under] + body)

    def run_child(self, body):
        return subprocess.run([sys.executable, "-c", self.child(body)], env=self.env,
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
