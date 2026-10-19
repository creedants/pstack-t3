import re
import unittest
from pathlib import Path

from .names import BRIGADE_DOCS, ROOT, brigade_docs


class SeatLaunchDocChecks:
    def setUp(self):
        self.skill = (ROOT / "skills/brigade/SKILL.md").read_text()
        service = self.skill.split("## Run a service", 1)[1].split("\n## ", 1)[0]
        self.launch = next(line for line in service.splitlines() if line.startswith("   3. Launch the worker"))
        self.review = next(line for line in service.splitlines() if line.startswith("6. **Review.**"))

    def test_step_4_3_links_both_runtime_anchors_and_records_after_the_match(self):
        self.assertIn("../pstack-runtime/SKILL.md#top-level-threads", self.launch)
        self.assertIn("../pstack-runtime/SKILL.md#delegation", self.launch)
        self.assertIn("Step 4.4 records the thread only after the readback matches.", self.launch)

    def test_step_6_reads_configuration_before_the_verdict(self):
        self.assertIn(
            "Right after the spawn, confirm its options with `t3_thread_configuration` on the returned `childThreadId` before the verdict counts",
            self.review,
        )


class LivenessIdleDocChecks:
    def test_step_5_nudges_an_idle_worker_with_its_timebox_open(self):
        text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        section = text.split("## Liveness check", 1)[1].split("\n## ", 1)[0]
        step = next(line for line in section.splitlines() if line.startswith("5. "))
        self.assertIn("activeRunId", step)
        self.assertIn("nudge-<dish>-<that run id>", step)
        self.assertIn("goes to step 4", step)

    def test_a_held_item_skips_liveness_steps_2_to_5(self):
        text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        section = text.split("## Liveness check", 1)[1].split("\n## ", 1)[0]
        self.assertIn(
            "`D1: open decision Q1: <question>; launch no worker or verifier until 86 answer Q1`",
            section,
        )
        self.assertIn("Launch nothing and spawn nothing.", section)
        self.assertIn("Skip Liveness steps 2 to 5 for that item.", section)
        self.assertIn("Step 4.4 records that thread", section)
        self.assertNotIn("Step 4.3 records that thread", section)
        self.assertNotIn("Skip this line while `$B 86 list` parks that dish.", section)


class UsageLimitDocChecks:
    def test_step_7_relaunches_a_usage_limit_before_the_light_mode_question(self):
        text = (ROOT / "t3/added/brigade/recovery.md").read_text()
        bullet = next(line for line in text.splitlines() if line.startswith("   - Usage limit:"))
        self.assertIn("../pstack-runtime/SKILL.md#failure-handling", bullet)
        self.assertIn("roles.py backup", bullet)
        self.assertIn("The dish keeps its lease", bullet)
        self.assertIn("Never call `t3_thread_send` on the old worker.", bullet)
        question = (
            '$B 86 add --question "<provider> hit its usage limit. '
            'Run new work in light mode until it resets?" '
            '--options "light, full" --default "light"'
        )
        self.assertIn(question, bullet)
        self.assertLess(bullet.index("t3_thread_interrupt"), bullet.index("roles.py backup"))
        self.assertLess(bullet.index("roles.py backup"), bullet.index(question))
        self.assertIn("../pstack-runtime/SKILL.md#modes", bullet)
        self.assertIn("../pstack-runtime/SKILL.md#gate-review", bullet)
        self.assertIn("each code delegate's `providerInstanceId/model`", bullet)
        self.assertIn("Never pass only the worker's model when a delegate wrote the code.", bullet)
        self.assertIn("--resume", bullet)
        self.assertIn("keeps its lease and its branch", bullet)
        self.assertIn("A new branch never widens the lease.", bullet)
        self.assertLess(bullet.index("On `park`"), bullet.index("--resume"))
        self.assertIn("A parked verifier stays in review.", bullet)
        self.assertNotIn("relaunch per this bullet on the first service after it", bullet)

    def test_step_7_runs_a_review_backups_panel(self):
        text = (ROOT / "t3/added/brigade/recovery.md").read_text()
        bullet = next(line for line in text.splitlines() if line.startswith("   - Usage limit:"))
        relaunch = "When it prints `relaunch`, spawn a fresh verifier per step 6 on the printed seat."
        panel = (
            "When it prints `panel`, spawn one fresh verifier per step 6 on each printed seat, "
            "keep the dish in review until every one is terminal, then apply the printed rule per "
            "[Failure handling](../pstack-runtime/SKILL.md#failure-handling)."
        )
        rows = (
            "Run `$B pass record` once for each member that returned a verdict, "
            "with that member's seat as `--verifier` and its file as `--report`."
        )
        passes = (
            "When the rule passes, record each passing member's `pass` with no other flag "
            "and every other member with `--member`."
        )
        resumed = "When `--resume` prints `panel`, run the panel the same way."
        for phrase in (
            panel,
            "`<restaurant dir>/reports/<dish>-review-<k>-panel-<n>.md`",
            rows,
            passes,
            "record that member's `send-back` with no other flag and every other member with `--member`",
            "When fewer than two pass and none reproduces a blocker, record every member with `--member`, "
            "and the dish stays in review as on `park`.",
            resumed,
        ):
            self.assertIn(phrase, bullet)
        self.assertNotIn("copy its findings", bullet)
        self.assertLess(bullet.index(relaunch), bullet.index(panel))
        self.assertLess(bullet.index(panel), bullet.index("On `park`"))
        self.assertLess(bullet.index("When `--resume` prints `relaunch` for `verifiers`"), bullet.index(resumed))


class RoundBudgetDocChecks:
    def setUp(self):
        self.text = (ROOT / "t3/added/brigade/SKILL.md").read_text()
        self.service = self.text.split("## Run a service", 1)[1].split("\n## ", 1)[0].splitlines()

    def test_the_script_section_names_the_closing_answers_and_the_budget(self):
        script = self.text.split("## The script", 1)[1].split("\n## ", 1)[0]
        self.assertIn("A dish's round count is its send-backs written after the later of its last passing verdict "
                      "and its last answered round decision.", script)
        self.assertIn("A dish owes a round decision while that count is three or more and its state is "
                      "`in-progress`, `in-review`, `sent-back`, or `blocked`.", script)
        self.assertIn("Only `known limits`, `redesign`, or `drop` closes it.", script)
        self.assertIn("No other decision's answer restarts the count.", script)
        self.assertIn("The budget refuses nothing else.", script)
        for command in ("`dish --state in-progress`", "`dish --thread` or `--task`", "a `brief` whose fields parse",
                        "`fire` exits 1 on a ticket of that dish"):
            self.assertIn(command, script)
        self.assertIn("`ROUND_BUDGET`", script)
        for old in ("whatever state", "after the dish is dropped", "A queue bounce of that dish is refused"):
            self.assertNotIn(old, script)

    def test_step_6_records_every_round_with_its_report(self):
        review = next(line for line in self.service if line.startswith("6. **Review.**"))
        self.assertTrue(review.endswith(
            "Record every round, pass or fail, with `$B pass record` at the reviewed SHA and `--report <that file>`."
        ))

    def test_step_7_stops_fix_rounds_at_a_pending_decision(self):
        recovery = (ROOT / "t3/added/brigade/recovery.md").read_text()
        pending = [line for line in recovery.splitlines()
                   if line.startswith("   - When `pass record` or `watch` prints `<n> send-backs, decision pending`,")]
        self.assertEqual(len(pending), 1)
        bullet = pending[0]
        stub = next(line for line in self.service
                    if line.startswith("   - When `pass record` or `watch` prints `<n> send-backs, decision pending`,"))
        send_back = next(line for line in self.service if line.startswith("   - `send-back`:"))
        self.assertLess(self.service.index(stub), self.service.index(send_back))
        self.assertIn("`$B 86 add --dish <id> --round-budget`", bullet)
        self.assertIn('`$B 86 answer Q<n> --answer "<option>"`', bullet)
        self.assertIn("On `known limits`", bullet)
        self.assertIn("On `redesign`", bullet)
        self.assertIn("On `drop`", bullet)
        self.assertIn("Finish with the old worker", bullet)
        self.assertIn("launch no fix worker", bullet)
        self.assertIn("Follow this bullet in place of the `send-back` bullet below.", bullet)
        self.assertNotIn("bounce", bullet)
        self.assertNotIn("decision pending", send_back)

    def test_the_liveness_check_points_a_pending_decision_at_step_7(self):
        section = self.text.split("## Liveness check", 1)[1].split("\n## ", 1)[0]
        lines = [line for line in section.splitlines() if line.startswith("   - `D1: 3 send-backs, decision pending`.")]
        self.assertEqual(len(lines), 1)
        self.assertIn("decision-pending bullet in Run a service step 7", lines[0])


class AnswerRelayDocChecks:
    def test_the_requests_answer_row_names_still_open(self):
        text = brigade_docs()
        requests = text.split("**Requests.**", 1)[1].split("\n**", 1)[0]
        row = next(line for line in requests.splitlines() if line.startswith("| `answer "))
        self.assertIn("still open", row)
