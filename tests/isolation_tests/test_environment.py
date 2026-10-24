"""The process that runs this module has a home outside the user's real home."""
import os
import pwd
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


def real_home():
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


class IsolatedHomeTest(unittest.TestCase):
    def test_every_home_variable_names_a_directory_outside_the_users_real_home(self):
        real = real_home()
        for name in VARIABLES:
            value = os.environ.get(name)
            print(f"{name}={value}")
            self.assertIsNotNone(value, f"{name} is unset")
            self.assertTrue(Path(value).is_absolute(), f"{name}={value} is not absolute")
            path = Path(value).resolve()
            self.assertFalse(path == real or real in path.parents, f"{name}={value} is inside {real}")
