if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import (
    BrigadeTest,
    setUpModule,
)


class ReportRetryTest(BrigadeTest):
    def test_a_brief_tells_a_worker_whose_report_send_gets_thread_not_found_to_retry_once(self):
        self.coordinator()
        lines = self.brigade(*self.BRIEF).splitlines()
        send = next(i for i, line in enumerate(lines) if line.startswith("- After that file is written, call t3_thread_send"))
        self.assertEqual(
            lines[send + 1],
            "- If `t3_thread_send` returns thread_not_found, read the thread id again from this brief file, "
            "copy it exactly, and retry once; name both attempts in the final message.",
        )
