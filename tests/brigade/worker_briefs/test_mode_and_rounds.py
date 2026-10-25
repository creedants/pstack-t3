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
    CLAUDE,
    CODEX,
    Path,
    SCRIPT,
    _wait_for_path,
    json,
    os,
    runpy,
)


class ModeRoundsMixin:
    def test_brief_refuses_an_item_that_owes_its_decision(self):
        self.started()
        self.brigade("set", "--thread", "c1")
        self.sent_back(2)
        self.assertIn("GOAL: g\n", self.brigade(*self.FIX_BRIEF))
        self.brigade("dish", "D1", "--state", "in-progress")
        self.send_back("a3")
        written = (self.at / "briefs" / "D1.md").read_bytes()
        before = self.unchanged()
        self.assertEqual(self.brigade(*self.FIX_BRIEF, ok=False), self.NO_FIX_ROUND)
        self.assertEqual(self.brigade("brief", "D1", "--contract", "none", "--goal", "g", "--verify", "v", "--base", "origin/main", ok=False),
                         self.NO_FIX_ROUND)
        self.unchanged(before)
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.brigade("dish", "D1", "--state", "in-review", "--sha", sha)
        self.assertEqual(self.brigade(*self.FIX_BRIEF, ok=False), self.NO_FIX_ROUND)
        self.assertEqual((self.at / "briefs" / "D1.md").read_bytes(), written)
        self.brigade(*self.ROUND_BUDGET)
        self.brigade("86", "answer", "Q1", "--answer", "redesign")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertIn("GOAL: g\n", self.brigade(*self.FIX_BRIEF))

    def test_a_first_light_feature_brief_prints_the_first_waivers(self):
        self.coordinator()
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], self.head(
            "Playbook: playbooks/feature.md", "Mode: light", "Mode source: restaurant.json", "Attempt: first",
            "Waived by mode: Arena, Interrogate, Comment Sicko"))
        report = text.split("REPORT:\n", 1)[1].splitlines()
        self.assertIn("## Threads", report[2])
        for role in ("code delegate", "claims check", "architect runner"):
            self.assertIn(role, report[2])
        self.assertEqual(report[3:5], [self.REPEAT, self.CONTESTED])
        self.assertTrue(report[5].startswith("- After that file is written, call t3_thread_send"), report[5])
        self.assertEqual((self.at / "briefs/D1.md").read_text().strip(), self.brief_body(text))
        self.assertEqual(self.mode_rows(), [])

    def test_a_brief_without_a_mode_prints_the_default(self):
        self.coordinator(mode=None)
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], self.head(
            "Playbook: playbooks/feature.md", "Mode: light", "Mode source: default", "Attempt: first",
            "Waived by mode: Arena, Interrogate, Comment Sicko"))
        report = text.split("REPORT:\n", 1)[1].splitlines()
        self.assertEqual(report[3:5], [self.REPEAT, self.CONTESTED])

    def test_a_dish_of_a_kind_whose_depth_is_full_briefs_full_from_that_kind(self):
        self.coordinator(mode=None)
        self.brigade("set", "--review", "fix=full")
        self.assertEqual(self.brigade(*self.BRIEF).split("\n\n", 1)[0], self.head(
            "Playbook: playbooks/feature.md", "Mode: full", "Mode source: brief", "Attempt: first"))
        self.brigade("set", "--mode", "light")
        self.assertIn("Mode: full\nMode source: brief\nAttempt: first\n", self.brigade(*self.BRIEF))

    def test_a_dish_of_a_kind_whose_depth_is_light_briefs_light_from_that_kind(self):
        self.coordinator(mode=None)
        self.brigade("set", "--review", "fix=light")
        self.assertEqual(self.brigade(*self.BRIEF).split("\n\n", 1)[0], self.head(
            "Playbook: playbooks/feature.md", "Mode: light", "Mode source: brief", "Attempt: first",
            "Waived by mode: Arena, Interrogate, Comment Sicko"))
        self.brigade("set", "--mode", "full")
        self.assertIn("Mode: light\nMode source: brief\nAttempt: first\n", self.brigade(*self.BRIEF))

    def test_a_depth_cleared_with_review_kind_leaves_the_brief_on_the_restaurants_mode(self):
        self.coordinator(mode="light")
        self.brigade("set", "--review", "fix=light")
        self.assertIn("Mode: light\nMode source: brief\nAttempt: first\n", self.brigade(*self.BRIEF))
        self.brigade("set", "--review", "fix=")
        self.assertIn("Mode: light\nMode source: restaurant.json\nAttempt: first\n", self.brigade(*self.BRIEF))
        self.brigade("set", "--mode", "full")
        self.assertIn("Mode: full\nMode source: restaurant.json\nAttempt: first\n", self.brigade(*self.BRIEF))

    def test_one_send_back_briefs_a_fix_attempt(self):
        self.coordinator()
        self.record("a1", "send-back")
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], self.head(
            "Playbook: playbooks/feature.md", "Mode: light", "Mode source: restaurant.json", "Attempt: fix",
            "Waived by mode: How, Architect, Arena, Interrogate, Comment Sicko"))

    def test_a_replaced_worker_keeps_the_attempt_and_adds_no_send_back(self):
        self.coordinator()
        self.brigade("dish", "D1", "--thread", "w2")
        self.brigade("dish", "D1", "--thread", "w3")
        self.assertIn("Mode: light\nMode source: restaurant.json\nAttempt: first\n", self.brigade(*self.BRIEF))
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.brigade("dish", "D1", "--thread", "w4")
        self.brigade("dish", "D1", "--thread", "w5")
        self.assertIn("Mode: light\nMode source: restaurant.json\nAttempt: fix\n", self.brigade(*self.BRIEF))
        self.assertEqual(self.mode_rows(), [])

    def test_member_send_backs_do_not_move_the_item_to_full(self):
        self.coordinator()
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back", "--member", verifier="grok/grok-4.7")
        self.record("a2", "send-back", "--member", verifier="opencode/kimi-k3")
        self.assertIn("Mode: light\nMode source: restaurant.json\nAttempt: fix\n", self.brigade(*self.BRIEF))
        self.assertEqual(self.mode_rows(), [])

    def test_a_late_send_back_counts_and_leaves_the_fix_attempt_running(self):
        self.coordinator()
        self.record("a2", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.brigade("pass", "record", "D1", "--pr", "u/1", "--sha", "a1", "--verdict", "send-back",
                                      "--reason", "other",
                                      "--author", CLAUDE, "--verifier", CODEX, "--note", "round 1", "--late"),
                         "D1: late send-back on record; D1 stays in-progress")
        self.assertEqual(self.dish_fields("state", "pr", "sha", "thread"), ("in-progress", "", "a2", ""))
        self.assertEqual(self.brigade("pass", "check", "D1", "--sha", "a1", ok=False),
                         "brigade: D1 at a1: send-back (round 1)")
        self.assertEqual(self.pass_rows()[1][3:], ["a1", "send-back", CLAUDE, CODEX, "round 1", "", "", "other"])
        self.assertIn("Mode: full\nMode source: escalated: second send-back\nAttempt: fix\n", self.brigade(*self.BRIEF))

    def test_a_fix_brief_names_the_report_of_the_latest_send_back(self):
        self.coordinator()
        first, second, panel = (self.review_file(f"D1-review-{name}.md") for name in ("1", "2", "2-panel-2"))
        line = "- A reviewer sent an earlier attempt back. Its findings: "
        self.record("a1", "send-back", "--report", first.name)
        self.assertIn(f"{line}{first}\n", self.brigade(*self.BRIEF))
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back", "--report", second.name)
        self.record("a2", "send-back", "--member", "--report", panel.name, verifier="grok/grok-4.7")
        self.assertIn(f"{line}{second}\n", self.brigade(*self.BRIEF))
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a3", "send-back")
        self.brigade("86", "add", "--dish", "D1", "--round-budget")
        self.brigade("86", "answer", "Q1", "--answer", "known limits")
        self.assertNotIn(line, self.brigade(*self.BRIEF))
        plain = self.review_file("D1-review.md")
        self.assertIn(f"{line}{plain}\n", self.brigade(*self.BRIEF))

    def test_a_queue_bounce_briefs_a_bounce_attempt(self):
        self.coordinator(station="bug-fix")
        self.record("a1", "pass")
        self.brigade("dish", "D1", "--state", "queued")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.assertEqual(self.brigade(*self.BRIEF).split("\n\n", 1)[0], self.head(
            "Playbook: playbooks/bug-fix.md", "Mode: light", "Mode source: restaurant.json", "Attempt: bounce",
            "Waived by mode: How, Why, Architect, Comment Sicko", station="bug-fix"))

    def test_the_escalation_lives_in_the_log_and_a_stale_write_changes_no_row(self):
        self.coordinator()
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back")
        self.brigade(*self.BRIEF)
        (self.at / "pass.tsv").write_text("\t".join(("at", "dish", "pr", "sha", "verdict", "author", "verifier", "note")) + "\n")
        self.brigade("set", "--mode", "light")
        self.assertIn("Mode: full\nMode source: escalated: second send-back\n", self.brigade(*self.BRIEF))
        self.brigade("set", "--thread", "c2", "--replace")
        before = (self.at / "log.tsv").read_bytes()
        self.assertEqual(self.brigade("--owner", "c1@1", "dish", "D1", "--mode", "full", "--reason", "late", ok=False),
                         "brigade: owner c1@1 is stale; this store is owned by c2@2")
        self.assertEqual(self.brigade("--owner", "c1@1", *self.BRIEF, ok=False),
                         "brigade: owner c1@1 is stale; this store is owned by c2@2")
        self.assertEqual((self.at / "log.tsv").read_bytes(), before)

    def test_set_mode_leaves_a_written_brief_unchanged(self):
        self.coordinator(mode="full")
        self.brigade(*self.BRIEF)
        written = (self.at / "briefs/D1.md").read_bytes()
        self.brigade("set", "--mode", "light")
        self.assertEqual((self.at / "briefs/D1.md").read_bytes(), written)
        self.assertIn("Mode: light\n", self.brigade(*self.BRIEF))

    def test_fire_mode_full_carries_its_reason_into_every_brief(self):
        self.coordinator("bug-fix", "light", "--mode", "full", "--reason", "tickets name a lock")
        self.assertIn("Mode: full\nMode source: escalated: tickets name a lock\nAttempt: first\nGate: brigade\n",
                      self.brigade(*self.BRIEF))
        self.assertEqual(len(self.mode_rows()), 1)

    def test_a_lease_over_a_configured_path_escalates_once(self):
        self.git_project()
        (self.project / ".pstack").mkdir()
        (self.project / ".pstack/t3-roles.json").write_text(json.dumps({"escalate": ["src/lock.py"]}))
        self.coordinator()
        brief = [*self.BRIEF]
        brief[brief.index("src/a.py")] = "src/lock.py"
        self.assertIn("Mode: full\nMode source: escalated: lease covers src/lock.py\n", self.brigade(*brief))
        self.assertIn("Mode: full\nMode source: escalated: lease covers src/lock.py\n", self.brigade(*self.BRIEF))
        self.assertEqual([row[:4] for row in self.mode_rows()], [["mode", "D1", "full", "lease covers src/lock.py"]])

    def test_a_station_that_is_not_a_playbook_briefs_only_the_mode(self):
        self.coordinator(station="correct")
        text = self.brigade(*self.BRIEF)
        self.assertEqual(text.split("\n\n", 1)[0], self.head("Mode: light", "Mode source: restaurant.json", station="correct"))

    def test_roles_mode_refuses_to_run_under_a_store_lock(self):
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        glob["HELD_LOCKS"].append(Path("/store/held"))
        with self.assertRaises(glob["BrigadeError"]) as caught:
            glob["roles_mode"](None)
        self.assertEqual(str(caught.exception),
                         "roles.py may run git; release /store/held's store lock before resolving the mode")

    def test_a_roles_failure_refuses_the_brief_and_writes_nothing(self):
        self.coordinator()
        config = Path(os.environ["XDG_CONFIG_HOME"]) / "pstack-t3" / "roles.json"
        config.parent.mkdir(parents=True)
        config.write_text("{")
        before = (self.at / "log.tsv").read_bytes()
        error = self.brigade(*self.BRIEF, ok=False)
        self.assertTrue(error.startswith(f"brigade: roles.py mode refused the brief: {config}: invalid JSON"), error)
        self.assertFalse((self.at / "briefs/D1.md").exists())
        self.assertEqual((self.at / "log.tsv").read_bytes(), before)

    def test_two_briefs_racing_append_one_mode_row(self):
        self.coordinator()
        self.record("a1", "send-back")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.record("a2", "send-back")
        case = Path(self.temporary.name) / "race"
        first = self.paused_brief(case)
        _wait_for_path(case / "paused", timeout=20)
        second = self.brigade(*self.BRIEF)
        (case / "proceed").touch()
        code, out, err = self._finish_race(first)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out, second)
        self.assertEqual(len(self.mode_rows()), 1)

    def test_a_fix_brief_points_at_the_report_of_a_pass_with_edits_round(self):
        self.briefable()
        self.review_file("D1-review-1.md", "findings\n")
        self.send_back("a1", "--report", "D1-review-1.md")
        self.brigade("dish", "D1", "--state", "in-progress")
        self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"), name="D1-review-2.md")
        self.record_edits("a2", "--edits", "D1-review-2.md")
        self.brigade("dish", "D1", "--state", "in-progress")
        text = self.brigade("brief", "D1", *self.FLAGS, "--contract", "none")
        self.assertIn(f"\n- A reviewer sent an earlier attempt back. Its findings: {self.at}/reports/D1-review-2.md\n", text)
        self.assertNotIn("D1-review-1.md", text)

