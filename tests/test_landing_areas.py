import importlib
import os
import sys
import unittest
from pathlib import Path


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


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "t3/added/landing/scripts"))

import landing_lib
import landing_lib.foundation as foundation
import landing_lib.seam as seam


class LandingAreasTest(unittest.TestCase):
    def test_areas_listed_names_resolve_on_their_module(self):
        namespace = {k: v for k, v in vars(foundation).items() if not k.startswith("__")}
        seam.bind(namespace)
        for area, names in landing_lib.AREAS.items():
            module = importlib.import_module(f"landing_lib.{area}")
            namespace.update({k: v for k, v in vars(module).items() if not k.startswith("__")})
            for name in names:
                self.assertTrue(hasattr(module, name), f"{area}.{name}")


if __name__ == "__main__":
    unittest.main()
