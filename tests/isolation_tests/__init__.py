"""Isolate this test process from the user's real home before any module of this area runs."""
from tests import isolation

isolation.install()


def load_tests(loader, tests, pattern):
    """Run this area's modules when the package is named, so `python3 -m unittest tests.isolation_tests` works."""
    suite = loader.suiteClass()
    for name in ("test_environment", "test_home_isolation", "test_static_activation"):
        suite.addTests(loader.loadTestsFromName(f"{__name__}.{name}"))
    return suite
