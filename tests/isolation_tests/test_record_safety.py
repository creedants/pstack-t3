"""The guard's record files stay trustworthy, and its descriptor allowance stays narrow.

A record that is empty, is not JSON, names only the run root, or names a relative path is refused and
does not replace the value the process already holds. The other cases cover the guard's own record
files. One replaces the record name with a symlink to a protected canary, and the guard must replace
the name instead of following it. Another swaps the record directory for a symlink at the moment the
guard lists it, and the replacement target must keep its file. The third makes the record directory
itself a protected root, so the guard must skip that candidate and keep the file it holds. Four more
cases cover the descriptor allowance. A child forked while an anchor is open must refuse a
delete in a protected root, an anchored descriptor must not authorize an absolute or multi-component
name, a rename must be refused when only one of its descriptors is anchored, and an anchored open to
a protected link or entry is refused.
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

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.isolation_tests.fixtures import ORIGINALS, ROOT


class MalformedRecordTest(unittest.TestCase):
    def test_a_malformed_or_dropping_record_is_refused_and_does_not_replace_the_value(self):
        root = str(isolation.root())
        cases = {
            "empty": "",
            "not json": "not json",
            "json object": json.dumps({"roots": [root]}),
            "relative path": json.dumps(["."]),
            "number": json.dumps([1]),
            "run root only": json.dumps([root]),
        }
        for name, value in cases.items():
            with self.subTest(name):
                saved = os.environ[ORIGINALS]
                try:
                    with self.assertRaises(PermissionError):
                        os.environ[ORIGINALS] = value
                    self.assertEqual(os.environ[ORIGINALS], saved)
                finally:
                    os.environ[ORIGINALS] = saved
        home = Path(tempfile.mkdtemp(prefix="malformed-home-"))
        self.addCleanup(shutil.rmtree, home, True)
        with self.assertRaises(SystemExit):
            isolation.worker_env({ORIGINALS: "not json"}, home)


LOADER = '''
import importlib.util

sys.path.insert(0, REPO)
spec = importlib.util.spec_from_file_location("pstack_test_isolation_roots", ROOTS)
module = importlib.util.module_from_spec(spec)
sys.modules["pstack_test_isolation_roots"] = module
spec.loader.exec_module(module)
module._TEMPORARIES = (BASE1, BASE2)
try:
    from tests import isolation
except SystemExit as problem:
    print("STOPPED", problem)
else:
    try:
        isolation.install()
    except SystemExit as problem:
        print("STOPPED", problem)
    else:
        print("INSTALLED")
'''


CONSTANTS = '''import json
import os
import sys

REPO = @REPO@
ROOTS = @ROOTS@
BASE1 = @BASE1@
BASE2 = @BASE2@
FOLDER = os.path.join(BASE1, "pstack-t3-isolation-%d" % os.getuid())
'''


SYMLINK_CHILD = CONSTANTS + '''CANARY = @CANARY@

if os.environ.get("PSTACK_D13_PHASE") != "two":
    exec(@LOADER@)
    os.environ["PSTACK_D13_PHASE"] = "two"
    os.execve(sys.executable, [sys.executable, __file__], dict(os.environ))

os.makedirs(FOLDER, mode=0o700, exist_ok=True)
name = os.path.join(FOLDER, "%d.json" % os.getpid())
if os.path.lexists(name):
    os.unlink(name)
os.symlink(CANARY, name)
exec(@LOADER@)
print("RECORD", os.path.islink(name), os.path.isfile(name))
'''


SWAP_CHILD = CONSTANTS + '''CANARY_DIR = @CANARY_DIR@
os.makedirs(FOLDER, mode=0o700, exist_ok=True)
with open(os.path.join(FOLDER, "999999.json"), "w") as handle:
    handle.write("{}")
os.chmod(os.path.join(FOLDER, "999999.json"), 0o600)
os.makedirs(CANARY_DIR, exist_ok=True)
with open(os.path.join(CANARY_DIR, "999999.json"), "w") as handle:
    handle.write("kept")
os.chmod(os.path.join(CANARY_DIR, "999999.json"), 0o600)
real_listdir = os.listdir


def swapping(target):
    if os.path.lexists(FOLDER) and not os.path.islink(FOLDER):
        os.rename(FOLDER, FOLDER + ".moved")
        os.symlink(CANARY_DIR, FOLDER)
    return real_listdir(target)


os.listdir = swapping
exec(@LOADER@)
'''


PROTECTED_CHILD = CONSTANTS + '''os.makedirs(FOLDER, mode=0o700, exist_ok=True)
CANARY = os.path.join(FOLDER, "999999.json")
with open(CANARY, "w") as handle:
    handle.write("kept")
os.chmod(CANARY, 0o600)
exec(@LOADER@)
print("CANARY", open(CANARY).read() if os.path.exists(CANARY) else "gone")
print("SECOND", os.path.exists(os.path.join(BASE2, "pstack-t3-isolation-%d" % os.getuid(),
                                            "%d.json" % os.getpid())))
'''


FORK_ANCHOR_CHILD = CONSTANTS + '''with open(os.path.join(@STATE@, "canary.txt"), "w") as handle:
    handle.write("kept")
exec(@LOADER@)
roots = sys.modules["pstack_test_isolation_roots"]
number = os.open(@STATE@, os.O_RDONLY)
try:
    os.unlink("canary.txt", dir_fd=number)
except PermissionError:
    print("BASELINE REFUSED")
else:
    print("BASELINE ALLOWED")
with roots.anchored(number):
    pid = os.fork()
    if pid == 0:
        held = sorted(roots._ANCHORS)
        try:
            os.unlink("canary.txt", dir_fd=number)
        except PermissionError:
            print("CHILD REFUSED", held)
        else:
            print("CHILD DELETED", held)
        print("CANARY", os.path.exists(os.path.join(@STATE@, "canary.txt")))
        sys.stdout.flush()
        os._exit(0)
os.waitpid(pid, 0)
os.close(number)
'''


ANCHORED_NAME_CHILD = CONSTANTS + '''os.makedirs(os.path.join(@STATE@, "nested"), exist_ok=True)
for name in ("canary.txt", os.path.join("nested", "canary.txt")):
    with open(os.path.join(@STATE@, name), "w") as handle:
        handle.write("kept")
exec(@LOADER@)
roots = sys.modules["pstack_test_isolation_roots"]
number = os.open(@STATE@, os.O_RDONLY)
with roots.anchored(number):
    for name in (os.path.join(@STATE@, "canary.txt"), os.path.join("nested", "canary.txt")):
        try:
            os.unlink(name, dir_fd=number)
        except PermissionError:
            print("REFUSED", name)
        else:
            print("DELETED", name)
os.close(number)
print("ABSOLUTE", os.path.exists(os.path.join(@STATE@, "canary.txt")))
print("MULTI", os.path.exists(os.path.join(@STATE@, "nested", "canary.txt")))
'''


ONE_ANCHOR_RENAME_CHILD = CONSTANTS + '''os.makedirs(@OUTSIDE@, exist_ok=True)
with open(os.path.join(@STATE@, "canary.txt"), "w") as handle:
    handle.write("kept")
exec(@LOADER@)
roots = sys.modules["pstack_test_isolation_roots"]
source = os.open(@STATE@, os.O_RDONLY)
target = os.open(@OUTSIDE@, os.O_RDONLY)
with roots.anchored(target):
    try:
        os.rename("canary.txt", "stolen.txt", src_dir_fd=source, dst_dir_fd=target)
    except PermissionError:
        print("REFUSED")
    else:
        print("RENAMED")
os.close(source)
os.close(target)
print("CANARY", os.path.exists(os.path.join(@STATE@, "canary.txt")))
print("STOLEN", os.path.exists(os.path.join(@OUTSIDE@, "stolen.txt")))
'''


ANCHORED_OPEN_CHILD = CONSTANTS + '''with open(os.path.join(@STATE@, "canary.txt"), "w") as handle:
    handle.write("original")
os.symlink("canary.txt", os.path.join(@STATE@, "soft"))
with open(os.path.join(@STATE@, "keep"), "w") as handle:
    handle.write("original")
exec(@LOADER@)
roots = sys.modules["pstack_test_isolation_roots"]
number = os.open(@STATE@, os.O_RDONLY)
cases = (("SOFT", "soft", os.O_WRONLY | os.O_TRUNC),
         ("KEEP", "keep", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW),
         ("FRESH", "fresh", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW))
with roots.anchored(number):
    for label, name, flags in cases:
        try:
            handle = os.open(name, flags, 0o600, dir_fd=number)
        except PermissionError:
            print(label, "REFUSED")
        except OSError as problem:
            print(label, "FAILED", problem)
        else:
            os.close(handle)
            print(label, "OPENED")
os.close(number)
for label, name in (("CANARY", "canary.txt"), ("KEEP", "keep")):
    with open(os.path.join(@STATE@, name), "rb") as handle:
        print(label, repr(handle.read()))
'''


class RecordSafetyTest(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="record-safety-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.base1 = self.scratch / "base1"
        self.base2 = self.scratch / "base2"
        self.external = self.scratch / "external"
        self.home = self.scratch / "home"
        self.tmp = self.scratch / "tmp"
        for path in (self.base1, self.base2, self.external, self.home, self.tmp):
            path.mkdir()
        self.canary = self.external / "canary.json"
        self.canary.write_text("kept")
        self.folder = self.base1 / f"pstack-t3-isolation-{os.getuid()}"

    def child(self, template):
        """Return one child body with this case's paths and the loader filled in."""
        loader = LOADER

        def fill(text):
            return (text.replace("@REPO@", repr(str(ROOT)))
                    .replace("@ROOTS@", repr(str(ROOT / "tests" / "isolation_roots.py")))
                    .replace("@BASE1@", repr(str(self.base1)))
                    .replace("@BASE2@", repr(str(self.base2)))
                    .replace("@CANARY@", repr(str(self.canary)))
                    .replace("@CANARY_DIR@", repr(str(self.scratch / "canary-dir")))
                    .replace("@STATE@", repr(str(self.external)))
                    .replace("@OUTSIDE@", repr(str(self.scratch / "outside")))
                    .replace("@LOADER@", repr(loader)))
        return fill(template)

    def run_child(self, body, state=None):
        source = self.scratch / "child.py"
        source.write_text(body)
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "TMPDIR": str(self.tmp)}
        if state is not None:
            env["XDG_STATE_HOME"] = str(state)
        return subprocess.run([sys.executable, str(source)], env=env, capture_output=True,
                              text=True, cwd=str(self.scratch), timeout=600)

    def test_a_symlinked_record_name_is_replaced_instead_of_followed(self):
        done = self.run_child(self.child(SYMLINK_CHILD), state=self.external)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(self.canary.read_text(), "kept")
        self.assertIn("RECORD False True", done.stdout, done.stdout + done.stderr)
        records = list(self.folder.glob("*.json"))
        self.assertEqual(len(records), 1, done.stdout + done.stderr)
        self.assertFalse(records[0].is_symlink())

    def test_a_record_directory_swapped_for_a_symlink_leaves_the_replacement_canary(self):
        done = self.run_child(self.child(SWAP_CHILD))
        self.assertIn("STOPPED", done.stdout + done.stderr)
        canary = self.scratch / "canary-dir" / "999999.json"
        self.assertTrue(canary.is_file(), done.stdout + done.stderr)
        self.assertEqual(canary.read_text(), "kept")
        moved = self.folder.with_name(self.folder.name + ".moved")
        self.assertFalse((moved / "999999.json").exists(), done.stdout + done.stderr)

    def test_a_protected_record_directory_is_skipped_and_its_canary_is_kept(self):
        done = self.run_child(self.child(PROTECTED_CHILD), state=self.folder)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("CANARY kept", done.stdout, done.stdout + done.stderr)
        self.assertIn("SECOND True", done.stdout, done.stdout + done.stderr)
        self.assertEqual((self.folder / "999999.json").read_text(), "kept")

    def test_a_child_forked_while_a_descriptor_is_anchored_refuses_a_delete_in_a_protected_root(self):
        done = self.run_child(self.child(FORK_ANCHOR_CHILD), state=self.external)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("BASELINE REFUSED", done.stdout, done.stdout + done.stderr)
        self.assertIn("CHILD REFUSED", done.stdout, done.stdout + done.stderr)
        self.assertIn("CANARY True", done.stdout, done.stdout + done.stderr)
        self.assertNotIn("CHILD DELETED", done.stdout, done.stdout + done.stderr)
        self.assertEqual((self.external / "canary.txt").read_text(), "kept")

    def test_an_anchored_descriptor_cannot_authorize_an_absolute_or_multi_component_name(self):
        done = self.run_child(self.child(ANCHORED_NAME_CHILD), state=self.external)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("ABSOLUTE True", done.stdout, done.stdout + done.stderr)
        self.assertIn("MULTI True", done.stdout, done.stdout + done.stderr)
        self.assertNotIn("DELETED", done.stdout, done.stdout + done.stderr)
        self.assertEqual((self.external / "canary.txt").read_text(), "kept")
        self.assertEqual((self.external / "nested" / "canary.txt").read_text(), "kept")

    def test_a_rename_with_only_one_anchored_descriptor_is_refused(self):
        done = self.run_child(self.child(ONE_ANCHOR_RENAME_CHILD), state=self.external)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("REFUSED", done.stdout, done.stdout + done.stderr)
        self.assertIn("CANARY True", done.stdout, done.stdout + done.stderr)
        self.assertIn("STOLEN False", done.stdout, done.stdout + done.stderr)
        self.assertNotIn("RENAMED", done.stdout, done.stdout + done.stderr)
        self.assertEqual((self.external / "canary.txt").read_text(), "kept")

    def test_an_anchored_open_that_reaches_a_protected_link_or_entry_is_refused(self):
        done = self.run_child(self.child(ANCHORED_OPEN_CHILD), state=self.external)
        output = done.stdout + done.stderr
        self.assertEqual(done.returncode, 0, output)
        for line in ("SOFT REFUSED", "KEEP REFUSED", "FRESH OPENED"):
            self.assertIn(line, done.stdout, output)
        self.assertNotIn("FAILED", done.stdout, output)
        self.assertIn("CANARY b'original'", done.stdout, output)
        self.assertIn("KEEP b'original'", done.stdout, output)
        damaged = ("an anchored descriptor let os.open write through a link or reuse an entry "
                   "in a protected root\n" + output)
        self.assertEqual((self.external / "canary.txt").read_bytes(), b"original", damaged)
        self.assertEqual((self.external / "keep").read_bytes(), b"original", damaged)
        self.assertTrue((self.external / "fresh").is_file(),
                        "a fresh name with O_CREAT and O_EXCL was not created through the anchored "
                        "descriptor\n" + output)


if __name__ == "__main__":
    unittest.main()
