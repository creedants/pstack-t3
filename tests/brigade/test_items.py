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
