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
