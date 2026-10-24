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
from .report_follow_ups.test_blocks import BlocksMixin
from .report_follow_ups.test_report_input import ReportInputMixin
from .report_follow_ups.test_quoted_paths import QuotedPathsMixin
from .report_follow_ups.test_bare_items import BareItemsMixin


class ReportsTest(BrigadeTest, BlocksMixin, ReportInputMixin, QuotedPathsMixin, BareItemsMixin):
    pass
