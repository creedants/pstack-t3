import inspect
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from scripts import check
import measure_capacity
import size_limit


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

FENCE_HEADER = "import os\nimport unittest\n\n\n"
FENCE_SOURCE = inspect.getsource(fence_git_environment)
SETUP_SOURCE = inspect.getsource(setUpModule)

def tests_tree(directory, modules):
    """A directory named tests holding landing/support.py and the given modules, each a source string."""
    tests = Path(directory) / "tests"
    landing = tests / "landing"
    landing.mkdir(parents=True)
    reference = FENCE_HEADER + FENCE_SOURCE + "\n\n" + SETUP_SOURCE
    (landing / "support.py").write_text(reference)
    for name, source in modules.items():
        (tests / name).write_text(source)
    return tests

def support_source(fence=None):
    """support.py with the landing fence, unless fence replaces that function source, plus class Doc."""
    body = FENCE_SOURCE if fence is None else fence
    return FENCE_HEADER + body + "\n\n" + SETUP_SOURCE + "\n\nclass Doc:\n    pass\n"

def brigade_tree(directory, support, modules):
    """tests_tree plus tests/brigade and the given modules. support.py is written only when support is not None."""
    tests = tests_tree(directory, {})
    brigade = tests / "brigade"
    brigade.mkdir()
    if support is not None:
        (brigade / "support.py").write_text(support)
    for name, source in modules.items():
        (brigade / name).write_text(source)
    return tests

def landing_tree(directory, support, modules):
    """tests_tree plus the given landing modules. support.py is absent when support is None."""
    tests = tests_tree(directory, {})
    landing = tests / "landing"
    if support is None:
        (landing / "support.py").unlink()
    else:
        (landing / "support.py").write_text(support)
    for name, source in modules.items():
        (landing / name).write_text(source)
    return tests

COPY = "Copy fence_git_environment and setUpModule from tests/landing/support.py"
BRIGADE_IMPORT = "Import setUpModule from tests/brigade/support.py"
LANDING_IMPORT = "Import setUpModule from tests/landing/support.py"
AREA_IMPORT = (
    "from .support import (\n"
    "    setUpModule,\n"
    "    runpy,\n"
    ")\n"
    "\n"
    "\n"
    "class AreaTest(unittest.TestCase):\n"
    "    def test_one(self):\n"
    "        pass\n"
)
IMPORTS_ONLY = "from .support import (\n    Doc,\n)\n"

