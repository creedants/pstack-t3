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
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "t3/scripts"))
sys.path.insert(0, str(ROOT / "scripts"))

import check  # noqa: E402
from tests.pstack_t3.support import (
    PREVIEW_TOOLS, fragment_holds_bullets, heading_slug, release_bullets, runtime_docs, unfenced_lines,
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

class PreviewToolDocTest(unittest.TestCase):
    def test_runtime_names_hover_drag_select_upload_and_dialog(self):
        text = (ROOT / "t3/runtime.md").read_text()
        row = next(line for line in text.splitlines() if "control-ui, browser MCP" in line)
        bullet = next(line for line in runtime_docs().splitlines() if line.startswith("- Web or Electron UI:"))
        for name in PREVIEW_TOOLS:
            self.assertIn(name, row)
            self.assertIn(name, bullet)

if __name__ == "__main__":
    unittest.main()
