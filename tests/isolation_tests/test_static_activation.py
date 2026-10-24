"""Every test module, the tests package, and every area package arm the guard before anything else they run."""

if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import ast
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]


def installs(statement):
    """Return whether statement is a top-level call to isolation.install()."""
    value = statement.value if isinstance(statement, ast.Expr) else None
    func = value.func if isinstance(value, ast.Call) else None
    return (isinstance(func, ast.Attribute) and func.attr == "install"
            and isinstance(func.value, ast.Name) and func.value.id == "isolation")


def activates(path):
    """Return whether the module body holds a top-level call to isolation.install()."""
    return any(installs(statement) for statement in ast.parse(path.read_text(), str(path)).body)


def prelude_problem(path):
    """Return what is wrong with the prelude of a test module, or None when its first statements after the
    docstring and any __future__ import are the guarded sys.path insert, the isolation import, and the install."""
    body = ast.parse(path.read_text(), str(path)).body
    while body and (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    or isinstance(body[0], ast.ImportFrom) and body[0].module == "__future__"):
        body = body[1:]
    if len(body) < 3:
        return "has fewer statements than the prelude"
    test, imported, install = body[:3]
    if not (isinstance(test, ast.If) and ast.unparse(test.test) == "__package__ in (None, '')"):
        return "does not start with the `if __package__ in (None, \"\")` test"
    if "sys.path.insert(0, str(_repo_root))" not in [ast.unparse(node) for node in test.body]:
        return "has no sys.path.insert of the repository root inside the `if` test"
    if ast.unparse(imported) != "from tests import isolation":
        return "does not import isolation from tests right after the `if` test"
    if not installs(install):
        return "does not call isolation.install() right after the import"
    return None


class ActivationTest(unittest.TestCase):
    def test_the_tests_package_and_every_area_package_call_install_when_imported(self):
        paths = [TESTS / "__init__.py", *sorted(TESTS.glob("*/__init__.py"))]
        self.assertGreater(len(paths), 1)
        for path in paths:
            with self.subTest(package=str(path.relative_to(TESTS.parent))):
                self.assertTrue(activates(path), f"{path.relative_to(TESTS.parent)} does not call isolation.install()")

    def test_every_test_module_starts_with_the_prelude_that_installs_the_guard_before_its_other_imports(self):
        paths = sorted(TESTS.rglob("test_*.py"))
        self.assertGreater(len(paths), 0)
        for path in paths:
            with self.subTest(module=str(path.relative_to(TESTS.parent))):
                self.assertIsNone(prelude_problem(path))
