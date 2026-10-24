"""Isolate this test process from the user's real home before any module of this area runs."""
from pathlib import Path

from tests import isolation

isolation.install()

MODULES = tuple(sorted(path.stem for path in Path(__file__).parent.glob("test*.py")))


def load_tests(loader, tests, pattern):
    """Run this area's modules when the package is named, so `python3 -m unittest tests.isolation_tests` works.

    Discovery passes its pattern, so a discovery that selects one module gets that module alone.
    """
    if pattern is not None:
        return loader.discover(str(Path(__file__).parent), pattern)
    suite = loader.suiteClass()
    for name in MODULES:
        suite.addTests(loader.loadTestsFromName(f"{__name__}.{name}"))
    return suite
