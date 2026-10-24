if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import BrigadeTest, StoresTest, setUpModule


class HandoffAllowanceTest(StoresTest):
    def test_a_sibling_report_ticket_does_not_consume_the_local_item_allowance(self):
        self.open("core-engine")
        self.open("engine")
        self.brigade("core-engine", "ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("core-engine", "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("engine", "ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("engine", "fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        (self.dir("core-engine") / "reports").mkdir(parents=True, exist_ok=True)
        (self.dir("core-engine") / "reports" / "D1.md").write_text("## Follow-ups\n\n- Correctness: Core follow-up.\n")
        self.assertEqual(self.brigade("core-engine", "ticket", "add", "--from-report", "reports/D1.md"),
                         "T2 added from core-engine/reports/D1.md#1: Correctness: Core follow-up.")
        self.brigade("core-engine", "ticket", "move", "T2", "--to", "engine")
        self.brigade("engine", "ticket", "take")
        (self.dir("engine") / "reports").mkdir(parents=True, exist_ok=True)
        (self.dir("engine") / "reports" / "D1.md").write_text("## Follow-ups\n\n- Correctness: Engine follow-up.\n")
        out = self.brigade("engine", "ticket", "add", "--from-report", "reports/D1.md")
        self.assertIn("added from engine/reports/D1.md#1: Correctness: Engine follow-up.", out)
        self.assertNotIn("takes one ticket per item", out)


class ExtraMarkedLedgerTest(BrigadeTest):
    def test_dry_run_renders_the_ledger_outcome_for_an_extra_marked_follow_up(self):
        self.fired_bug_fix()
        body = "## Follow-ups\n\n- Correctness: first.\n- Privacy: second.\n- Tidy docs.\n"
        self.review_file("D1.md", body)
        before = self.store_files()
        out = self.brigade("ticket", "add", "--from-report", "reports/D1.md", "--dry-run")
        self.assertEqual(out.splitlines(), [
            "would add from perf/reports/D1.md#1: Correctness: first.",
            "not filed, D1.md takes one ticket per item: Privacy: second.",
            "would note perf/reports/D1.md#2, no tracked path: Privacy: second.",
            "would note perf/reports/D1.md#3, no tracked path: Tidy docs.",
        ])
        self.assertEqual(self.store_files(), before)
        out = self.follow_ups_in(body)
        self.assertEqual(out.splitlines(), [
            "T2 added from perf/reports/D1.md#1: Correctness: first.",
            "not filed, D1.md takes one ticket per item: Privacy: second.",
            "noted perf/reports/D1.md#2, no tracked path: Privacy: second.",
            "noted perf/reports/D1.md#3, no tracked path: Tidy docs.",
        ])
        before = self.store_files()
        out = self.brigade("ticket", "add", "--from-report", "reports/D1.md", "--dry-run")
        self.assertEqual(out.splitlines(), [
            "skipped perf/reports/D1.md#1, same text as T2 (waiting): Correctness: first.",
            "not filed, D1.md takes one ticket per item: Privacy: second.",
            "already noted perf/reports/D1.md#2, no tracked path: Privacy: second.",
            "already noted perf/reports/D1.md#3, no tracked path: Tidy docs.",
        ])
        self.assertEqual(self.store_files(), before)
