import argparse
import runpy
import types

from ..support import (
    SCRIPT,
    setUpModule,
    sys,
    unittest,
)


def _slot_help(top):
    for action in top._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices["slot"].format_help()
    raise AssertionError("slot parser missing")


def _landing_keys():
    return {name for name in sys.modules if name == "landing_lib" or name.startswith("landing_lib.")}


class ParserIsolationTest(unittest.TestCase):
    def test_parser_leaves_sys_path_and_cached_modules_unchanged(self):
        scripts = str(SCRIPT.parent)
        saved_path = list(sys.path)
        saved_modules = {name: module for name, module in sys.modules.items() if name == "landing_lib" or name.startswith("landing_lib.")}
        sys.path = [entry for entry in sys.path if entry != scripts]
        try:
            before_path = list(sys.path)
            before_keys = _landing_keys()
            namespace = runpy.run_path(str(SCRIPT))
            try:
                namespace["parser"]()
            finally:
                sys.modules.pop("<run_path>", None)
            self.assertEqual(list(sys.path), before_path)
            self.assertEqual(_landing_keys(), before_keys)
        finally:
            sys.path = saved_path
            for name in _landing_keys():
                if name not in saved_modules:
                    del sys.modules[name]
            sys.modules.update(saved_modules)

    def test_parser_succeeds_with_unrelated_landing_lib_cached(self):
        saved_modules = {name: module for name, module in sys.modules.items() if name == "landing_lib" or name.startswith("landing_lib.")}
        marker = types.ModuleType("landing_lib")
        sys.modules["landing_lib"] = marker
        try:
            namespace = runpy.run_path(str(SCRIPT))
            try:
                top = namespace["parser"]()
            finally:
                sys.modules.pop("<run_path>", None)
            self.assertIs(sys.modules.get("landing_lib"), marker)
            self.assertEqual(top.prog, "land.py")
        finally:
            for name in _landing_keys():
                if name not in saved_modules and name != "landing_lib":
                    del sys.modules[name]
            sys.modules.pop("landing_lib", None)
            sys.modules.update(saved_modules)

    def test_parser_uses_entry_exclusive_cap_at_call_time(self):
        namespace = runpy.run_path(str(SCRIPT))
        try:
            build = namespace["parser"]
            build.__globals__["EXCLUSIVE_CAP"] = 17
            namespace["EXCLUSIVE_CAP"] = 17
            top = build()
        finally:
            sys.modules.pop("<run_path>", None)
        self.assertIn("The default is 17.", _slot_help(top))
