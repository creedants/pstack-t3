from .support import BrigadeTest, setUpModule
from .report_follow_ups.test_blocks import BlocksMixin
from .report_follow_ups.test_report_input import ReportInputMixin
from .report_follow_ups.test_quoted_paths import QuotedPathsMixin
from .report_follow_ups.test_bare_items import BareItemsMixin


class ReportsTest(BrigadeTest, BlocksMixin, ReportInputMixin, QuotedPathsMixin, BareItemsMixin):
    pass
