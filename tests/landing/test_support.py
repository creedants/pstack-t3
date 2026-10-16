import subprocess
import sys
import unittest

from .support import setUpModule  # noqa: F401

_CHILD = (
    "import ctypes, tempfile\n"
    "from tests.landing.support import _close_live_owners\n"
    "pydll = ctypes.PyDLL(None)\n"
    "pythonapi = ctypes.pythonapi\n"
    "class Canary:\n"
    "    def fileno(self):\n"
    "        raise KeyboardInterrupt('canary')\n"
    "canary = Canary()\n"
    "tmp = tempfile.NamedTemporaryFile(delete=False)\n"
    "number = tmp.fileno()\n"
    "_close_live_owners([number])\n"
    "assert tmp.closed\n"
    "print('ok')\n"
)


class SupportTest(unittest.TestCase):
    def test_a_ctypes_pydll_cannot_segfault_live_owner_cleanup(self):
        """Pin T766. The helper calls fileno only on known owners, never a ctypes library. Starts a process for a signal, so a regression fails the test instead of killing the runner."""
        child = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(child.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
