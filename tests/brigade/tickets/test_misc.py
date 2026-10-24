if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import CLAUDE, CODEX

class MiscMixin:
    def test_dropping_an_item_leaves_its_other_tickets_alone(self):
        self.open()
        for summary in ("one", "two"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "a")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "b")
        self.brigade("ticket", "set", "T2", "--state", "done")
        self.assertEqual(self.brigade("dish", "D2", "--state", "dropped"), "D2 dropped")
        self.assertEqual(self.brigade("ticket", "list"), "T1 assigned normal fix [user] one\nT2 done normal fix [user] two")

    def test_queueing_or_merging_needs_a_pass_at_the_current_sha(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "s")
        self.brigade("fire", "--tickets", "T1", "--station", "bug-fix", "--summary", "Fix s")
        self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued", "--sha", "def", ok=False),
                         "brigade: only reviewed work lands: D1 has no review verdict for def")
        self.assertEqual(self.brigade("dish", "D1", "--state", "queued"), "D1 queued")
        self.assertEqual(self.brigade("status"), "thread not recorded\nreporting: milestones, no landing contract, waiting to land: 1\nworkers: 0 of 2 running, 2 idle, auto-start: normal\nliveness: every 600000 ms (default for milestones)\nkinds: fix 1 in flight")
        self.assertEqual(self.brigade("dish", "D1", "--state", "merged"), "D1 merged")
        self.assertEqual(self.brigade("ticket", "list", "--state", "done"), "T1 done normal fix [user] s")

    def test_tabs_and_newlines_in_input_cannot_break_a_table(self):
        self.open()
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "line one\nline\ttwo")
        self.assertEqual(self.brigade("ticket", "list"), "T1 waiting normal fix [user] line one line two")

    def test_ticket_set_prints_each_given_change_in_one_line_and_refuses_a_call_with_none(self):
        self.open()
        for summary in ("a", "b", "c"):
            self.brigade("ticket", "add", "--kind", "fix", "--summary", summary)
        self.assertEqual(self.brigade("ticket", "set", "T1", "--state", "dropped"), "T1 dropped")
        self.assertEqual(self.brigade("ticket", "set", "T2", "--state", "dropped", "--priority", "normal"),
                         "T2 dropped; priority normal")
        self.assertEqual(self.brigade("ticket", "set", "T3", "--decision", "--paths", "b,a", "--priority", "low",
                                      "--state", "waiting"), "T3 waiting; priority low; paths a,b; decision")
        self.assertEqual(self.cells("T3"), ("low", "a,b", "yes"))
        self.assertEqual(self.brigade("ticket", "set", "T3", "--paths", "", "--no-decision"), "T3 no paths; no decision")
        self.assertEqual(self.cells("T3"), ("low", "", ""))
        before = self.store_files()
        self.assertEqual(self.brigade("ticket", "set", "T3", ok=False),
                         "brigade: ticket set needs --state, --priority, --paths, --decision, --no-decision, --kind, --goal, or --no-goal")
        self.assertEqual(self.brigade("ticket", "set", "T9", ok=False), "brigade: no T9 in rail.tsv")
        self.assertEqual(self.store_files(), before)
