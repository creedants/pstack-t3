if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import BrigadeTest, _TicketsRace, setUpModule
from .tickets.test_add import AddMixin
from .tickets.test_from_report import FromReportMixin
from .tickets.test_goals_a import GoalsAMixin
from .tickets.test_goals_b import GoalsBMixin
from .tickets.test_labels import LabelsMixin
from .tickets.test_misc import MiscMixin
from .tickets.test_paths import PathsMixin
from .tickets.test_rail_a import RailAMixin
from .tickets.test_rail_b import RailBMixin


class TicketsTest(BrigadeTest, AddMixin, FromReportMixin, GoalsAMixin, GoalsBMixin, LabelsMixin, MiscMixin, PathsMixin, RailAMixin, RailBMixin, _TicketsRace):
    pass
