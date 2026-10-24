if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import CLAUDE, CODEX, SCRIPT, brigade_docs, runpy, shlex

class NextMixin:
    def test_next_admin_lines_match_the_events_table(self):
        text = brigade_docs()
        rows = []
        started = False
        for line in text.splitlines():
            if line.startswith("| Line |"):
                started = True
                continue
            if not started:
                continue
            if not line.startswith("|"):
                break
            if set(line.replace("|", "").replace("-", "").strip()) == set():
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            kind = cells[0].replace("`", "").split()[0]
            store = " ".join(cells[2].replace("`", "").split())
            rows.append((kind, store))
        recorded = [(kind, store) for kind, store, _only in runpy.run_path(str(SCRIPT))["ADMIN_LINES"]]
        self.assertEqual(recorded, rows)

    def test_next_reply(self):
        self.open()
        pause = ("if the queue is paused and this thread cannot fix it and this is not a bounce handed to a fresh worker and --failure stuck was not passed: "
                 "run next --failure stuck")
        self.assertEqual(self.next_sheet("morning"), "\n".join([
            "wake: morning", "reporting: milestones",
            "see No intake source is recorded. File a user request with ticket add when the message asks for work.",
            "see Morning service is not a report.",
            "reply: no (routine: morning service)"]))
        self.assertEqual(self.next_sheet("evening"), "\n".join([
            "wake: evening", "reporting: milestones",
            "see Write the evening report for this reporting level. At digest, plain sentences and the closeout path. "
            "Otherwise at most three sentences and then the close output.",
            "run close",
            "reply: yes (evening: report)"]))
        self.assertEqual(self.next_sheet("user"), "\n".join([
            "wake: user", "reporting: milestones",
            "see Answer the user first.",
            "see File the work the message asks for with ticket add.",
            "run close",
            "reply: yes (user: message)"]))
        self.assertEqual(self.next_sheet("user", "--question").splitlines()[-1], "reply: yes (user: question)")
        self.assertEqual(self.next_sheet("liveness"), "\n".join([
            "wake: liveness", "reporting: milestones", "see watch", pause,
            "if watch printed no work in progress: run set --schedule liveness=",
            "reply: no (routine: liveness)"]))
        self.brigade("set", "--reporting", "every-turn")
        every = self.next_sheet()
        self.assertEqual(every.splitlines()[-1], "reply: yes (every-turn: wake)")
        self.assertNotIn("run close", every)
        self.assertEqual(self.next_sheet("--unsent", "misrouted").splitlines()[-1], "reply: yes (every-turn: wake)")
        self.brigade("set", "--reporting", "milestones")
        self.assertEqual(self.next_sheet("--unsent", "misrouted").splitlines()[-1], "reply: no (routine: unspecified)")
        report = self.next_sheet("--unsent", "report")
        self.assertEqual(report.splitlines()[-1], "reply: yes (report: unsent report)")
        self.assertNotIn("run close", report)
        self.brigade("set", "--intake", "github")
        intake = self.next_sheet("intake")
        self.assertIn("see Intake sources are github. File tickets from those sources with ticket add.", intake)
        self.assertEqual(intake.splitlines()[-1], "reply: no (routine: intake)")
        self.assertNotIn("run close", intake)
        self.fresh()
        self.park_item()
        self.brigade("close")
        for level, last in (("milestones", "reply: no (routine: unspecified)"),
                            ("digest", "reply: no (routine: unspecified)"),
                            ("every-turn", "reply: yes (every-turn: wake)")):
            self.brigade("set", "--reporting", level)
            self.assertEqual(self.next_sheet().splitlines()[-1], last)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("pass", "record", "D1", "--sha", "s1", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        self.assertEqual(self.next_sheet("review", "D1", "--verdict", "send-back").splitlines()[-1], "reply: no (sent-back: D1)")
        self.brigade("pass", "record", "D1", "--sha", "s2", "--verdict", "send-back", "--author", CLAUDE, "--verifier", CODEX)
        crossed = self.next_sheet("review", "D1", "--verdict", "send-back")
        self.assertEqual(crossed.splitlines()[-1], "reply: yes (decision: D1)")
        self.assertIn("run close --to-file", crossed)
        blocked = self.next_sheet("review", "D1", "--verdict", "blocked")
        self.assertEqual(blocked.splitlines()[-1], "reply: yes (decision: D1)")
        self.assertIn("run close --to-file", blocked)
        self.assertIn("if only the user can unblock D1: ask with 86 add --dish D1 --question --options --default", blocked)
        self.assertNotIn("if only the user can unblock D1: run 86 add --dish D1", blocked)
        fixable = self.next_sheet("review", "D1", "--verdict", "blocked", "--failure", "fixable")
        self.assertEqual(fixable.splitlines()[-1], "reply: no (failure: fixable)")
        self.assertNotIn("run close", fixable)
        self.assertNotIn("86 add --dish D1", fixable)
        self.assertEqual(self.next_sheet("checks", "D1", "--result", "fail").splitlines()[-1], "reply: no (failure: fail)")
        stuck = self.next_sheet("checks", "D1", "--result", "fail", "--failure", "stuck")
        self.assertEqual(stuck.splitlines()[-1], "reply: yes (stuck: stuck)")
        self.assertIn("run close --to-file", stuck)
        self.assertEqual(self.next_sheet("--unsent", "blocked").splitlines()[-1], "reply: no (failure: unsent blocked)")
        self.assertEqual(self.next_sheet("--unsent", "blocked", "--failure", "stuck").splitlines()[-1], "reply: yes (stuck: stuck)")
        digest_report = self.next_sheet("--unsent", "report")
        self.assertEqual(digest_report.splitlines()[-1], "reply: yes (report: unsent report)")
        self.assertIn("run close --to-file", digest_report)
        refused = self.next_sheet("merge", "D1")
        self.assertEqual(refused.splitlines()[-1], "reply: no (routine: merge)")
        self.assertNotIn("run dish D1 --state merged", refused)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        drained = self.next_sheet("merge", "D1")
        self.assertEqual(drained.splitlines()[-1], "reply: yes (drained: batch)")
        self.assertIn("run dish D1 --state merged", drained)
        self.assertIn("run close --to-file", drained)
        for line in drained.splitlines():
            if line.startswith("run "):
                self.brigade(*shlex.split(line[4:]))
        self.assertEqual(self.next_sheet().splitlines()[-1], "reply: no (routine: unspecified)")
        self.fresh()
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        merged = self.next_sheet("merge", "D1")
        self.assertEqual(merged.splitlines()[-1], "reply: yes (merged: D1)")
        self.assertNotIn("run close", merged)
        self.fresh()
        self.brigade("set", "--reporting", "digest")
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "second")
        self.brigade("fire", "--tickets", "T2", "--station", "bug-fix", "--summary", "Second")
        self.brigade("pass", "record", "D1", "--sha", "abc", "--verdict", "pass", "--author", CLAUDE, "--verifier", CODEX)
        self.brigade("dish", "D1", "--state", "queued")
        held = self.next_sheet("merge", "D1")
        self.assertEqual(held.splitlines()[-1], "reply: no (merged: D1)")
        self.assertNotIn("run close", held)
        self.fresh()
        same = self.run_raw("next", "--failure", "stuck", "merge", "D1")
        other = self.run_raw("next", "merge", "D1", "--failure", "stuck")
        self.assertEqual(same, other)
        self.assertEqual(same[0], 0)
        self.assertEqual(same[1].splitlines()[-1], "reply: yes (stuck: stuck)")
        self.fresh()
        self.brigade("set", "--reports-to", "admin-thread")
        evening = self.next_sheet("evening")
        self.assertEqual(evening.splitlines()[-1], "reply: no (reports to the executive admin: evening report)")
        self.assertIn("run close --to-file", evening)
        lines = evening.splitlines()
        report_line = "see t3_thread_send admin-thread mode queue: report perf: the path close --to-file prints"
        self.assertLess(lines.index("run close --to-file"), lines.index(report_line))
        user = self.next_sheet("user")
        self.assertEqual(user.splitlines()[-1], "reply: yes (user: message)")
        self.assertNotIn("run close", user)
        self.assertEqual(self.next_sheet("user", "--question").splitlines()[-1], "reply: yes (user: question)")
        failed = self.next_sheet("--unsent", "failed")
        self.assertEqual(failed.splitlines()[-1], "reply: yes (failed: unsent failed)")
        self.open_admin()
        published = self.admin("request", "--to", "perf", "from-user perf: please fix the gate")
        ident = published.split()[0]
        routed = self.next_sheet("admin", ident)
        self.assertEqual(routed.splitlines()[-1], "reply: no (reports to the executive admin: admin request)")
        self.assertIn(f"see t3_thread_send admin-thread mode queue: reply perf: sent after the answer", routed)
        self.brigade("set", "--reports-to", "")
        direct = self.next_sheet("admin", ident)
        self.assertEqual(direct.splitlines()[-1], "reply: yes (user: message)")
        self.assertIn("run close\n", direct + "\n")
        self.assertNotIn("run close --to-file", direct)
