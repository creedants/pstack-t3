"""Isolate this test process from the user's real home before any module of this area runs."""
from tests import isolation

isolation.install()
