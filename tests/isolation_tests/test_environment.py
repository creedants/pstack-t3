"""The process that runs this module has a home outside the user's real home and a guard that refuses a delete outside its run root.

test_launch starts this module through each common entry point. When PSTACK_GUARD_PROBE_REPORT names a file,
the test appends one JSON line with what it found there.
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
import pwd
import secrets
import unittest
from pathlib import Path

VARIABLES = (
    "HOME",
    "XDG_STATE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_RUNTIME_DIR",
    "TMPDIR",
)
REPORT = "PSTACK_GUARD_PROBE_REPORT"


def real_home():
    return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


def within(path, base):
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


class IsolatedHomeTest(unittest.TestCase):
    def test_every_home_variable_resolves_inside_the_run_root_and_outside_the_real_home_and_a_delete_beside_the_root_is_refused(self):
        root = os.path.realpath(isolation.root())
        values = {}
        for name in VARIABLES:
            value = os.environ.get(name)
            print(f"{name}={value}")
            self.assertIsNotNone(value, f"{name} is unset")
            self.assertTrue(Path(value).is_absolute(), f"{name}={value} is not absolute")
            values[name] = os.path.realpath(value)
            self.assertFalse(within(values[name], real_home()), f"{name}={value} is inside the real home")
            self.assertTrue(within(values[name], root), f"{name}={value} is outside the run root {root}")
        self.assertFalse(within(root, real_home()), f"the run root {root} is inside the real home")
        absent = Path(root).parent / f".pstack-t3-probe-{secrets.token_hex(8)}"
        with self.assertRaises(PermissionError):
            os.rmdir(absent)
        if os.environ.get(REPORT):
            with open(os.environ[REPORT], "a", encoding="utf-8") as report:
                report.write(json.dumps({"root": root, "values": values, "refused": True}) + "\n")


if __name__ == "__main__":
    unittest.main()
