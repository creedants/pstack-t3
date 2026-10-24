if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import BrigadeTest, _ItemsRace, setUpModule
from .items.test_dish import DishMixin
from .items.test_doc_paths import DocPathsMixin
from .items.test_doc_shared import DocSharedMixin
from .items.test_fire_kind_a import FireKindAMixin
from .items.test_fire_kind_b import FireKindBMixin
from .items.test_fire_ride import FireRideMixin
from .items.test_labels_misc import LabelsMiscMixin
from .items.test_retire import RetireMixin
from .items.test_rounds import RoundsMixin


class ItemsTest(BrigadeTest, DishMixin, DocPathsMixin, DocSharedMixin, FireKindAMixin, FireKindBMixin, FireRideMixin, LabelsMiscMixin, RetireMixin, RoundsMixin, _ItemsRace):
    pass
