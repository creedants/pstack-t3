import os
import tempfile
import unittest
from pathlib import Path

from tests import in_process

SCRIPT = (
    "def inner():\n"
    "    raise ValueError('boom')\n"
    "\n"
    "def outer():\n"
    "    inner()\n"
    "\n"
    "outer()\n"
)


def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
    saved = dict(os.environ)

    def restore():
        os.environ.clear()
        os.environ.update(saved)

    unittest.addModuleCleanup(restore)
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull


def setUpModule():
    fence_git_environment()


class UncaughtExceptionTest(unittest.TestCase):
    def assert_same_as_real_interpreter(self, program):
        invocation = in_process.Invocation(program=program)
        real = in_process.finished(in_process.Reason.INTERPRETER_EXIT, invocation)
        saved = os.environ.pop("PSTACK_TEST_REAL_PROCESS", None)
        try:
            here = in_process.invoke(invocation)
        finally:
            if saved is not None:
                os.environ["PSTACK_TEST_REAL_PROCESS"] = saved
        self.assertEqual(
            (here.returncode, here.stdout, here.stderr),
            (real.returncode, real.stdout, real.stderr),
        )

    def test_an_uncaught_exception_prints_the_same_traceback_as_a_real_process(self):
        self.maxDiff = None
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / "raises.py"
            program.write_text(SCRIPT)
            self.assert_same_as_real_interpreter(program)

    def test_a_script_named_runpy_keeps_its_own_traceback_frames(self):
        self.maxDiff = None
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / "runpy.py"
            program.write_text(SCRIPT)
            self.assert_same_as_real_interpreter(program)
