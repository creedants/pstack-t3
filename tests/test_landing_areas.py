if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

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

    def test_leases_area_missing_public_callable(self):
        import landing_lib.leases as leases
        expected = {
            name for name, value in vars(leases).items()
            if not name.startswith("_") and callable(value)
            and (getattr(value, "__module__", "") == "landing_lib.leases"
                 or getattr(value, "__module__", "").startswith("landing_lib._leases"))
        }
        self.assertEqual(set(landing_lib.AREAS["leases"]), expected)


if __name__ == "__main__":
    unittest.main()
