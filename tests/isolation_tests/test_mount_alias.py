"""A path inside the run root that mounts protected storage is judged by the storage it reaches.

`os.path.realpath` follows symlinks and not mounts, so a bind mount at a name inside the run root used to
make protected storage look like the run root. The first case makes that mount for real in a private user
and mount namespace, which needs no root and leaves the host untouched. The second case answers the mount
table from a line that names the alias as a mount of the protected directory, the way
`test_descriptor_paths.py` answers `F_GETPATH`, so the resolver is covered on a host where the mount cannot
be made.
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

BODY = '''import json
import os
import sys

sys.path.insert(0, @REPO@)
from tests import isolation

root = isolation.install()
protected = @PROTECTED@
alias = os.path.join(str(root), "mnt")
os.mkdir(alias)
@SETUP@

verdicts = {}


def attempt(name, call):
    try:
        call()
    except PermissionError:
        verdicts[name] = "refused"
    else:
        verdicts[name] = "allowed"


def write():
    with open(os.path.join(alias, "written.txt"), "w") as handle:
        handle.write("damaged")


def truncate():
    handle = os.open(os.path.join(alias, "keep-for-delete.txt"), os.O_RDWR)
    try:
        os.ftruncate(handle, 0)
    finally:
        os.close(handle)


def rename():
    os.rename(os.path.join(alias, "keep-for-rename.txt"), os.path.join(alias, "moved.txt"))


def delete():
    handle = os.open(alias, os.O_RDONLY)
    try:
        os.unlink("keep-for-delete.txt", dir_fd=handle)
    finally:
        os.close(handle)


attempt("write", write)
attempt("truncate", truncate)
attempt("rename", rename)
attempt("delete", delete)
with open(@REPORT@, "w") as handle:
    json.dump(verdicts, handle)
'''

MOUNT = '''import ctypes

libc = ctypes.CDLL(None, use_errno=True)
if libc.mount(protected.encode(), alias.encode(), None, 4096, None) != 0:
    print("MOUNT UNAVAILABLE", ctypes.get_errno())
    raise SystemExit(0)
'''

ANSWER = '''import pstack_test_isolation_paths as paths

for name in ("keep-for-delete.txt", "keep-for-rename.txt"):
    with open(os.path.join(alias, name), "w") as handle:
        handle.write("stand-in")


def unescape(field):
    """Decode the octal escapes mountinfo writes for space, tab, newline, and the escape character."""
    out = bytearray()
    index = 0
    while index < len(field):
        if field[index] == 92 and field[index + 1:index + 4].isdigit() and len(field) - index >= 4:
            out.append(int(field[index + 1:index + 4], 8) & 255)
            index += 4
        else:
            out.append(field[index])
            index += 1
    return os.fsdecode(bytes(out))


def read_table():
    """Return (mount point, device, on-device root) for every mount this process can see."""
    with open("/proc/self/mountinfo", "rb") as handle:
        lines = handle.read().splitlines()
    table = []
    for line in lines:
        left = line.split(b" - ", 1)[0].split()
        table.append((unescape(left[4]), os.fsdecode(left[2]), unescape(left[3])))
    return tuple(table)


def on_device(path, table):
    """Return the device and on-device path of path, as the kernel would answer for the mount that covers it."""
    best = None
    for mount, device, root in table:
        if path == mount or path.startswith(mount.rstrip("/") + "/"):
            if best is None or len(mount) > len(best[0]):
                best = (mount, device, root)
    mount, device, root = best
    return device, (root.rstrip("/") + "/" + path[len(mount):].lstrip("/")).rstrip("/")


table = read_table()
device, root = on_device(os.path.realpath(protected), table)
paths._mount_table = lambda: table + ((alias, device, root),)
'''

ENTRIES = ("write", "truncate", "rename", "delete")
PROTECTED_FILES = ("keep-for-delete.txt", "keep-for-rename.txt")


class MountAliasTest(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(tempfile.mkdtemp(prefix="mount-alias-"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.protected = self.scratch / "protected"
        self.protected.mkdir()
        for name in PROTECTED_FILES:
            (self.protected / name).write_text("kept")
        self.home = self.scratch / "home"
        self.home.mkdir()
        self.tmp = self.scratch / "tmp"
        self.tmp.mkdir()
        self.report = self.scratch / "report.json"

    def child(self, setup):
        """Return the body of a child that installs the guard, makes the alias reach protected storage, and mutates through it."""
        return (BODY.replace("@REPO@", repr(str(Path(__file__).resolve().parents[2])))
                .replace("@PROTECTED@", repr(str(self.protected)))
                .replace("@SETUP@", setup)
                .replace("@REPORT@", repr(str(self.report))))

    def start(self, setup):
        """Run one child with a fresh environment, so it makes its own run root under this case's temporary directory."""
        source = self.scratch / "child.py"
        source.write_text(self.child(setup))
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "TMPDIR": str(self.tmp)}
        return subprocess.run([sys.executable, str(source)], env=env, capture_output=True, text=True,
                              cwd=str(self.scratch), timeout=600)

    def assert_nothing_moved(self, done):
        self.assertNotIn("MOUNT UNAVAILABLE", done.stdout)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        verdicts = json.loads(self.report.read_text())
        self.assertEqual(sorted(verdicts), sorted(ENTRIES))
        self.assertEqual({name: verdict for name, verdict in verdicts.items() if verdict != "refused"}, {},
                         done.stdout + done.stderr)
        self.assertEqual(sorted(os.listdir(self.protected)), sorted(PROTECTED_FILES))
        for name in PROTECTED_FILES:
            self.assertEqual((self.protected / name).read_text(), "kept", name)

    @unittest.skipUnless(sys.platform.startswith("linux"), "a private user and mount namespace is a Linux feature")
    @unittest.skipUnless(shutil.which("unshare"), "unshare is not installed")
    def test_a_real_bind_mount_at_a_name_inside_the_run_root_cannot_reach_protected_storage(self):
        source = self.scratch / "child.py"
        source.write_text(self.child(MOUNT))
        env = {"PATH": os.environ["PATH"], "HOME": str(self.home), "TMPDIR": str(self.tmp)}
        done = subprocess.run(["unshare", "-Ur", "-m", sys.executable, str(source)], env=env,
                              capture_output=True, text=True, cwd=str(self.scratch), timeout=600)
        if "MOUNT UNAVAILABLE" in done.stdout:
            raise unittest.SkipTest("this kernel refuses a bind mount in a private user namespace")
        self.assert_nothing_moved(done)

    @unittest.skipUnless(os.path.exists("/proc/self/mountinfo"), "this host has no mount table to answer")
    def test_a_mount_table_that_names_the_alias_as_protected_storage_refuses_every_mutation_through_it(self):
        self.assert_nothing_moved(self.start(ANSWER))


if __name__ == "__main__":
    unittest.main()
