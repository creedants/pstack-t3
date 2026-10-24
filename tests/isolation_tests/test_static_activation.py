"""Every test module that can be the first test-tree import calls isolation.install()."""
import ast
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]


def activates(path):
    """Return whether the module body holds a top-level call to isolation.install()."""
    tree = ast.parse(path.read_text(), str(path))
    for statement in tree.body:
        value = statement.value if isinstance(statement, ast.Expr) else None
        func = value.func if isinstance(value, ast.Call) else None
        if (isinstance(func, ast.Attribute) and func.attr == "install"
                and isinstance(func.value, ast.Name) and func.value.id == "isolation"):
            return True
    return False


class ActivationTest(unittest.TestCase):
    def test_every_area_package_calls_install_when_it_is_imported(self):
        paths = sorted(TESTS.glob("*/__init__.py"))
        self.assertGreater(len(paths), 0)
        for path in paths:
            with self.subTest(area=path.parent.name):
                self.assertTrue(activates(path), f"tests/{path.parent.name}/__init__.py does not call isolation.install()")

    def test_every_top_level_facade_calls_install_before_any_project_import(self):
        paths = sorted(TESTS.glob("test_*.py"))
        self.assertGreater(len(paths), 0)
        for path in paths:
            with self.subTest(facade=path.name):
                self.assertTrue(activates(path), f"tests/{path.name} does not call isolation.install()")
