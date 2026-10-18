import importlib
import importlib.machinery
import inspect
import runpy
import sys
import unittest
from pathlib import Path

from .support import ROOT, SCRIPT, setUpModule

LIBDIR = SCRIPT.parent / "brigade_lib"
BUILT_PARENT = ROOT / "skills" / "brigade" / "scripts"


def _drop_brigade():
    for name in [n for n in sys.modules if n == "brigade_lib" or n.startswith("brigade_lib.") or n.startswith("_brigade_ns_")]:
        del sys.modules[name]


class LoaderTest(unittest.TestCase):
    def test_spend_module_object_is_shared_between_entry_and_reporting(self):
        _drop_brigade()
        self.addCleanup(_drop_brigade)
        load = runpy.run_path(str(SCRIPT))
        self.assertIs(load["seam"], load["_seam_bootstrap"])
        spend_through_reporting = load["report"].__globals__["spend"]
        self.assertIs(spend_through_reporting.__dict__, load["spend_text"].__globals__)

    def test_each_brigade_lib_file_executes_once_per_load(self):
        _drop_brigade()
        self.addCleanup(_drop_brigade)
        expected = {str(p.resolve()) for p in LIBDIR.rglob("*.py") if p != LIBDIR / "__init__.py"}
        self.assertTrue(expected)
        counts = {}
        original = importlib.machinery.SourceFileLoader.exec_module

        def counting(loader, module):
            path = str(Path(loader.path).resolve())
            if path.startswith(str(LIBDIR.resolve())):
                counts[path] = counts.get(path, 0) + 1
            return original(loader, module)

        importlib.machinery.SourceFileLoader.exec_module = counting
        try:
            runpy.run_path(str(SCRIPT))
        finally:
            importlib.machinery.SourceFileLoader.exec_module = original
        self.assertEqual(set(counts), expected)
        for path, total in sorted(counts.items()):
            self.assertEqual(total, 1, path)

    def test_seam_set_does_not_resolve_to_restored_prior_package(self):
        _drop_brigade()
        self.addCleanup(_drop_brigade)
        sys.path.insert(0, str(BUILT_PARENT))
        self.addCleanup(sys.path.remove, str(BUILT_PARENT))
        prior_seam = importlib.import_module("brigade_lib.seam")
        prior = sys.modules["brigade_lib"]
        load = runpy.run_path(str(SCRIPT))
        self.assertIs(sys.modules.get("brigade_lib"), prior)
        self.assertIs(sys.modules.get("brigade_lib.seam"), prior_seam)
        self.assertIsNot(inspect.getmodule(load["seam"].set), prior_seam)
