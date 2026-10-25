"""Helpers shared by this area's isolation test modules."""

import os
import tempfile
from pathlib import Path

from tests import isolation

ROOT = Path(__file__).resolve().parents[2]
ORIGINALS = "PSTACK_TEST_PROTECTED_ROOTS"
_outside = None


def outside():
    """Return one directory beside the run root, made once per process and left in place, holding a file and a directory."""
    global _outside
    if _outside is None:
        _outside = Path(tempfile.mkdtemp(prefix="outside-", dir=isolation.root().parent))
        (_outside / "file").write_text("x")
        (_outside / "directory").mkdir()
        (_outside / "tree" / "leaf").mkdir(parents=True)
    return _outside
