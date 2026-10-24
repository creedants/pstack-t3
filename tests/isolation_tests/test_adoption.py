"""What a child process accepts as a run root, and what a guarded process hands out."""

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
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.isolation_tests.fixtures import ROOT, outside

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
        root = self.scratch / "roots"
        cases["malformed marker roots"] = self.handed(root)
        self.rewrite(root, roots="not a list")
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

    def test_a_guarded_process_refuses_to_mark_or_hand_out_a_directory_it_did_not_allocate(self):
        target = outside()
        kept = target / "old-data" / "kept"
        kept.parent.mkdir(exist_ok=True)
        kept.write_text("kept")
        self.assertIs(isolation.inside(target), False)
        with self.assertRaises(PermissionError):
            isolation.mark(target)
        self.assertFalse(os.path.lexists(target / isolation.MARKER))
        with self.assertRaises(PermissionError):
            isolation.worker_env(dict(os.environ), target)
        self.assertEqual(kept.read_text(), "kept")
        self.assertEqual(sorted(os.listdir(target)), ["directory", "file", "old-data", "tree"])

    def test_a_marker_copied_to_another_directory_is_refused_at_adoption(self):
        marked = self.scratch / "marked"
        marked.mkdir()
        token = isolation.mark(marked)
        copied = self.scratch / "copied"
        copied.mkdir()
        shutil.copyfile(marked / isolation.MARKER, copied / isolation.MARKER)
        values = isolation.layout(copied)
        for path in values.values():
            os.makedirs(path)
        env = dict(os.environ, **values)
        env[isolation.POINTER] = str(copied)
        env[isolation.TOKEN] = token
        done = self.run_child(env)
        self.assertNotEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("BODY RAN", done.stdout)
        self.assertIn("pstack-t3 test isolation:", done.stderr)

    def test_an_allocated_directory_outside_the_run_root_is_handed_out(self):
        home = isolation.allocate(isolation.root().parent)
        env = isolation.worker_env(dict(os.environ), home)
        code = "\n".join([
            "import sys",
            f"sys.path.insert(0, {str(ROOT)!r})",
            "from tests import isolation",
            "isolation.install()",
            "print('BODY RAN')",
            "import shutil",
            "shutil.rmtree(isolation.root())",
        ])
        done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=self.scratch)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("BODY RAN", done.stdout)
        self.assertFalse(os.path.lexists(home))

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
