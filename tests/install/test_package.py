"""Package shape for the installer split."""

import ast
import importlib.util
import sys
import unittest
from pathlib import Path

from .support import setUpModule

ROOT = Path(__file__).resolve().parents[2]
INSTALL_LIB = ROOT / "scripts" / "_install"


def load_check():
    spec = importlib.util.spec_from_file_location("pstack_check", ROOT / "scripts" / "check.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def package_modules():
    found = {}
    for file in sorted(INSTALL_LIB.glob("*.py")):
        if file.name == "__init__.py":
            continue
        found[file.stem] = ast.parse(file.read_text(), str(file))
    return found


class PackageTest(unittest.TestCase):
    def test_no_relative_import_names_a_function(self):
        for stem, module in package_modules().items():
            for node in module.body:
                if isinstance(node, ast.ImportFrom) and (node.level or 0) >= 1:
                    for alias in node.names:
                        self.assertIn(alias.name, ("layout", "records", "plans", "moves", "restore", "strays", "run", "doctor"),
                                      f"{stem}: from-import names {alias.name}")

    def test_no_two_modules_define_the_same_name(self):
        seen = {}
        for stem, module in package_modules().items():
            for node in module.body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                    self.assertNotIn(node.name, seen, f"{node.name} in {seen.get(node.name)} and {stem}")
                    seen[node.name] = stem

    def test_hosted_names_have_placeholders(self):
        layout = (INSTALL_LIB / "layout.py").read_text()
        records = (INSTALL_LIB / "records.py").read_text()
        self.assertIn("config_home = None", layout)
        self.assertIn("new_file = None", records)

    def test_install_area_passes_the_fence(self):
        check = load_check()
        tests_dir = ROOT / "tests"
        reference_path = tests_dir / check.FENCE_REFERENCE
        reference_function = check._module_function(ast.parse(reference_path.read_text(), str(reference_path)), check.FENCE_FUNCTION)
        reference = ast.dump(reference_function)
        copy = f"Copy {check.FENCE_FUNCTION} and {check.FENCE_SETUP} from tests/{check.FENCE_REFERENCE}"
        findings = check._package_findings(tests_dir, "tests", reference, copy, "install", "support.py", "tests.install.support")
        self.assertEqual(findings, [])
