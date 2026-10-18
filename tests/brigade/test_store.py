from .support import BrigadeTest, setUpModule
from .store.test_next import NextMixin
from .store.test_open import OpenMixin
from .store.test_set import SetMixin


class StoreTest(BrigadeTest, NextMixin, OpenMixin, SetMixin):
    pass
