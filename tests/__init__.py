"""Tests for pstack-t3. Importing this package, or any area package, isolates the process from the user's real home."""
from . import isolation

isolation.install()
