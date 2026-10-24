"""In-process restore behavior."""

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import ctypes
import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path

from .support import setUpModule
from .support import fresh_home
from .support import make_checkout
from .support import provider_link
from .support import run
from .races import put_back_race
from .races import noreplace_result
from .hooks import uninstall_hooked
from .support import _env
from tests import in_process

class InProcessRestoreTest(unittest.TestCase):
    def test_restoring_sys_argv_and_the_working_directory_leaves_them_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / "move.py"
            program.write_text(
                "import os, sys\n"
                "os.chdir('/')\n"
                "sys.argv[:] = ['changed']\n"
                "raise SystemExit(0)\n",
                encoding="utf-8",
            )
            previous = os.getcwd()
            argv = sys.argv
            contents = list(sys.argv)
            result = in_process.invoke(in_process.Invocation(program=program))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(os.getcwd(), previous)
            self.assertIs(sys.argv, argv)
            self.assertEqual(list(sys.argv), contents)

    def test_a_preloaded_module_attribute_replaced_by_an_installer_hook_is_the_same_object_afterwards(self):
        import ctypes
        saved = ctypes.CDLL
        wrapper, home = fresh_home()
        self.addCleanup(wrapper.cleanup)
        checkout = make_checkout(home, "a")
        env = {"XDG_CONFIG_HOME": str(home / ".config")}
        previous = os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)

        def restore():
            if previous is None:
                os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)
            else:
                os.environ["PSTACK_TEST_REAL_PROCESS"] = previous

        self.addCleanup(restore)
        before = in_process.runs_here()
        installed = run(home, checkout, "--harness", "grok", env=env)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        swarm = provider_link(home, "grok", "swarm")
        raced = uninstall_hooked(home, checkout, put_back_race(swarm, "link", link_error=True, rename="libc"), env=env)
        self.assertEqual(raced.returncode, 3, raced.stderr + raced.stdout)
        self.assertEqual(noreplace_result(home), errno.ENOSYS)
        self.assertIs(ctypes.CDLL, saved)
        self.assertEqual(in_process.runs_here() - before, 2)
