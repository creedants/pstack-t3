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
import subprocess
import sys
import tempfile
import unittest

from .support import setUpModule  # noqa: F401

_CHILD = (
    "import ctypes, os, socket, tempfile\n"
    "from tests.landing.support import _close_live_owners\n"
    "pydll = ctypes.PyDLL(None)\n"
    "pythonapi = ctypes.pythonapi\n"
    "class Canary:\n"
    "    def fileno(self):\n"
    "        raise KeyboardInterrupt('canary')\n"
    "canary = Canary()\n"
    "tmpdir = os.environ['LAND_CHILD_DIR']\n"
    "tmp = tempfile.NamedTemporaryFile(delete=False, dir=tmpdir)\n"
    "sock = socket.socket()\n"
    "_close_live_owners([tmp.fileno(), sock.fileno()])\n"
    "assert tmp.closed\n"
    "assert sock.fileno() == -1\n"
    "print(tmp.name)\n"
    "print('ok')\n"
)

_CRASH_CHILD = (
    "import os, tempfile\n"
    "tmpdir = os.environ['LAND_CHILD_DIR']\n"
    "tmp = tempfile.NamedTemporaryFile(delete=False, dir=tmpdir)\n"
    "print(tmp.name, flush=True)\n"
    "raise SystemExit(1)\n"
)


class SupportTest(unittest.TestCase):
    def test_a_ctypes_pydll_cannot_segfault_live_owner_cleanup(self):
        """Pin T766. The helper calls fileno only on known owners, never a ctypes library. Starts a process for a signal, so a regression fails the test instead of killing the runner."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {**os.environ, "LAND_CHILD_DIR": tmpdir}
            child = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True, env=env)
            self.assertEqual(child.returncode, 0, child.stderr)
            lines = child.stdout.splitlines()
            self.assertEqual(lines[-1].strip(), "ok")
            tmpname = lines[0].strip()
            self.assertTrue(tmpname.startswith(tmpdir))
            self.assertTrue(os.path.exists(tmpname))
        self.assertFalse(os.path.exists(tmpname))
        self.assertFalse(os.path.exists(tmpdir))

    def test_a_child_that_exits_nonzero_leaves_no_temp_file_behind(self):
        """Pin T768. The child file lives in a parent owned directory, so the parent removes it after a nonzero exit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {**os.environ, "LAND_CHILD_DIR": tmpdir}
            child = subprocess.run([sys.executable, "-c", _CRASH_CHILD], capture_output=True, text=True, env=env)
            self.assertNotEqual(child.returncode, 0)
            tmpname = child.stdout.strip()
            self.assertTrue(tmpname.startswith(tmpdir))
            self.assertTrue(os.path.exists(tmpname))
        self.assertFalse(os.path.exists(tmpname))
        self.assertFalse(os.path.exists(tmpdir))


if __name__ == "__main__":
    unittest.main()
