if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import BrigadeTest, setUpModule
from .store.test_next import NextMixin
from .store.test_open import OpenMixin
from .store.test_set import SetMixin


class StoreTest(BrigadeTest, NextMixin, OpenMixin, SetMixin):
    pass
