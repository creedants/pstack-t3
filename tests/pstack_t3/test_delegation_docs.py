import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "t3/scripts"))
sys.path.append(str(ROOT / "scripts"))

import re
import roles  # noqa: E402
from tests.pstack_t3.support import runtime_doc_section, runtime_section, runtime_bullet


class StalledChildDocTest(unittest.TestCase):
    def test_delegation_step_5_bounds_an_open_child(self):
        waiting = (ROOT / "t3/added/pstack-runtime/waiting.md").read_text()
        self.assertIn("t3_thread_wait", waiting)
        self.assertIn("timeoutMs: 300000", waiting)
        self.assertIn("does not end its turn while a child is open", waiting)
        step = runtime_doc_section("delegation.md", "5. Collect results.", "\n6. You own")
        self.assertIn("Read [waiting.md](waiting.md)", step)

    def test_delegation_step_5_rechecks_a_final_result_after_two_minutes(self):
        waiting = (ROOT / "t3/added/pstack-runtime/waiting.md").read_text()
        ten = "A child with no new item for 10 minutes is stalled."
        two = "no pending tool or child run, and no new activity for two minutes is stalled."
        for phrase in (
            two,
            "A message that says more work follows does not hold the result.",
            "After you see that message while the child's run is still open",
            "with `timeoutMs: 120000`",
            "only when the wait timed out, `workState` is still `working`, "
            "that message is still the last item, and no tool or child run is pending.",
            "Otherwise the 10-minute rule above decides that child.",
            ten,
        ):
            self.assertIn(phrase, waiting)
        self.assertLess(waiting.index(ten), waiting.index(two))
        step = runtime_doc_section("delegation.md", "5. Collect results.", "\n6. You own")
        self.assertIn("Read [waiting.md](waiting.md)", step)

    def test_delegation_step_5_waits_out_an_ended_run(self):
        waiting = (ROOT / "t3/added/pstack-runtime/waiting.md").read_text()
        two = "no pending tool or child run, and no new activity for two minutes is stalled."
        ended = "A child's run can end while its task stays open."
        now = "If you need a result now"
        for phrase in (
            ended,
            "returns at once, because an idle thread returns immediately",
            "`waiting_for_children`",
            "Read the `activeRunId` of the `parentThreadId` from `orchestrator_capabilities` with `t3_thread_read`, and pass them as `threadId` and `runId`.",
            "Without `runId`, the call picks this thread's latest run, which can be a queued run already cancelled, and returns at once.",
            "returns `timedOut: true` after 120 seconds",
            "Never wait with a shell `sleep`",
            "a `task_status` check does not",
            "because the two-minute rule above needs an open run",
            "Count 10 minutes from the child's last activity item.",
        ):
            self.assertIn(phrase, waiting)
        self.assertLess(waiting.index(two), waiting.index(ended))
        self.assertLess(waiting.index(ended), waiting.index(now))
        step = runtime_doc_section("delegation.md", "5. Collect results.", "\n6. You own")
        self.assertIn("Read [waiting.md](waiting.md)", step)

    def test_delegation_step_5_holds_against_the_delegate_task_tool_text(self):
        waiting = (ROOT / "t3/added/pstack-runtime/waiting.md").read_text()
        stay = "does not end its turn while a child is open"
        holds = "This holds even though the `delegate_task` tool text says to end the turn."
        self.assertIn(holds, waiting)
        self.assertLess(waiting.index(stay), waiting.index(holds))
        step = runtime_doc_section("delegation.md", "5. Collect results.", "\n6. You own")
        self.assertIn("Read [waiting.md](waiting.md)", step)

    def test_failure_handling_keeps_the_two_minute_clock_out(self):
        section = runtime_section("### Failure handling", "### Fresh children by default")
        self.assertNotIn("two minutes", section)
        self.assertNotIn("120000", section)
        self.assertIn("holds the result the brief asked for", section)

    def test_permissions_never_lowers_runtime_mode(self):
        text = (ROOT / "t3/added/pstack-runtime/delegation.md").read_text()
        section = text.split("### Permissions", 1)[1].split("### Fresh children by default", 1)[0]
        self.assertIn("never lower it", section)

    def test_failure_handling_names_a_stalled_child(self):
        text = (ROOT / "t3/runtime.md").read_text()
        section = text.split("### Failure handling", 1)[1].split("### Fresh children by default", 1)[0]
        self.assertIn("A stalled child", section)

    def test_no_source_tells_a_parent_to_end_its_turn_on_an_open_child(self):
        unbounded = re.compile(r"completion notifications? wakes?|let (?:each|the) completion|end the turn rather than wait"
                               r"|`mode: \"async\"`,? and end the turn")
        found = [f"{path.relative_to(ROOT)}:{number}"
                 for path in sorted((ROOT / "t3").rglob("*.md"))
                 for number, line in enumerate(path.read_text().splitlines(), 1) if unbounded.search(line)]
        self.assertEqual(found, [])

    def test_routed_skills_collect_children_per_delegation_step_5(self):
        sources = {
            "how/SKILL.md": "../pstack-runtime", "arena/SKILL.md": "../pstack-runtime",
            "interrogate/SKILL.md": "../pstack-runtime", "swarm/SKILL.md": "../pstack-runtime",
            "why/SKILL.md": "../pstack-runtime", "no-comments/SKILL.md": "../pstack-runtime",
            "poteto-mode/playbooks/orchestrate.md": "../../pstack-runtime",
            "poteto-mode/playbooks/feature.md": "../../pstack-runtime",
            "poteto-mode/playbooks/bug-fix.md": "../../pstack-runtime",
            "poteto-mode/playbooks/refactoring.md": "../../pstack-runtime",
            "poteto-mode/playbooks/perf-issue.md": "../../pstack-runtime",
            "poteto-mode/playbooks/hillclimb.md": "../../pstack-runtime",
        }
        missing = [name for name, runtime in sources.items()
                   if f"Delegation step 5]({runtime}/SKILL.md#delegation)" not in (ROOT / "t3/overrides" / name).read_text()]
        self.assertEqual(missing, [])

    def test_failure_handling_relaunches_a_usage_limit_from_the_backups_key(self):
        section = runtime_section("### Failure handling", "\n### ") + runtime_doc_section("failures.md", "## Usage limit")
        self.assertIn("roles.py backup", section)
        for decision in ("relaunch", "panel", "park", "not-usage-limit"):
            self.assertIn(f"`{decision}`", section)
        for phrase in (
            "The candidates are the role's seats in the `backups` key, in order.",
            "A candidate is skipped when its provider is the failed provider, is already out, is not runnable or not in the catalog, the `exclude` list refuses it, or, for a review role, its model family wrote the diff.",
            "`review backups` has no backup.",
            "When no candidate is left, the command parks.",
            "It prints the original seat when its provider is back, and otherwise the first usable `backups` seat.",
            "The command prints `relaunch`, `panel`, `park`, or `not-usage-limit`.",
            "For a `verifiers` seat with `review backups` set, the command prints `panel` instead.",
            "a stable `clientRequestId` ending in `-panel-<n>`",
            "Wait until every member is terminal, even after two pass.",
            'runs this command with `--role "review backups"`, which parks it, and it counts as no pass.',
            "A member that fails otherwise respawns once.",
            "Land only if no reviewer reproduces a blocker and at least two of the panel pass.",
            "With `review backups` unset, or fewer than two usable seats, the command prints `park`.",
            "each code delegate's model",
            "`--resume` reads no error text.",
            "roles.py backup --cwd",
            "```bash",
            "A panel never seats the failed provider, seats the configured `review backups` seats it keeps, and parks when fewer than two usable seats remain. A panel reports when it drops a seat whose family is already seated.",
        ):
            self.assertIn(phrase, section)
        self.assertLess(section.index("The candidates are the role's seats"), section.index("For a `verifiers` seat with"))
        failed = section.split("- A child fails", 1)[1].split("\n- ", 1)[0]
        self.assertIn("When the failure carries a provider error, follow the next bullet before that respawn.", failed)
        self.assertLess(section.index("provider error"), section.index("roles.py backup"))

    def test_failure_handling_decides_completion_by_work_state(self):
        section = runtime_section("### Failure handling", "\n### ")
        self.assertNotIn("that child is still running nested work", section)
        for phrase in (
            "Decide completion by `workState` alone.",
            "it does not reopen the task",
            "A stalled child",
        ):
            self.assertIn(phrase, section)
        host = (ROOT / "t3/added/pstack-runtime/host.md").read_text()
        nightlies = host.split("### Pre-0.1.0 nightlies", 1)[1].split("\n## ", 1)[0]
        for phrase in (
            "0.0.46-nightly.20261008.2813",
            "startup recovery",
            "a turn held in a stopped queue does not count",
        ):
            self.assertIn(phrase, nightlies)
        waiting = (ROOT / "t3/added/pstack-runtime/waiting.md").read_text()
        self.assertIn("`hasPendingChildRuns` does not change that.", waiting)
        step = runtime_doc_section("delegation.md", "5. Collect results.", "\n6. You own")
        self.assertIn("Read [waiting.md](waiting.md)", step)

    def test_local_state_reads_a_finished_result_after_restart(self):
        section = runtime_doc_section("failures.md", "## After a restart")
        self.assertIn("A task in `result_available` finished", section)
        self.assertIn("before you respawn its slice", section)

class OpenCodeLongCommandDocTest(unittest.TestCase):
    PARAGRAPH = (
        "Nobody resumes you after your turn ends, so a command left running in the background is lost.",
        "Run every long command in the foreground with a long enough timeout, for example a bash timeout of 600000 ms.",
        "If your shell moves a command to the background anyway, keep checking its output, "
        "with `sleep 60` between checks, until it finishes.",
        "Do not end your turn until your report is written.",
    )

    def test_delegation_step_3_carries_the_opencode_brief_paragraph(self):
        step = runtime_doc_section("delegation.md", "3. Spawn every independent child", "\n4. A child starts")
        cursor = "A Cursor harness sent `options` as a string, T3 refused the call, and the child then dropped the options."
        rule = step.split(cursor, 1)[1].split("\n   - ", 2)[1]
        self.assertTrue(rule.startswith("OpenCode moves a long command to the background, tells the model to end its turn"))
        self.assertNotIn("only OpenCode", rule)
        self.assertIn("T3 publishes the task's result when the first turn ends, and the later turn does not reopen the task.", rule)
        self.assertIn("Paste this paragraph unchanged into the brief of every child seated on OpenCode.", rule)
        self.assertIn("     > " + " ".join(self.PARAGRAPH) + "\n", rule)
        self.assertIn(
            "When an OpenCode child's task is terminal and the report file its brief named is missing, "
            "the task is not finished. Read its activity with `t3_thread_read`. "
            "While a later turn on its thread still runs, wait on it per [waiting.md](waiting.md). "
            "Otherwise handle it per [Failure handling](SKILL.md#failure-handling).",
            rule,
        )

    def test_step_5_two_minute_rule_skips_a_background_wait(self):
        waiting = (ROOT / "t3/added/pstack-runtime/waiting.md").read_text()
        bullet = runtime_bullet(waiting, "   - A child with a final assistant message that holds the result")
        self.assertIn(
            "A message that says more work follows does not hold the result. "
            "A last message that says the child waits on a background command does not hold the result either, "
            "so this two-minute rule does not apply to it.",
            bullet,
        )
        step = runtime_doc_section("delegation.md", "5. Collect results.", "\n6. You own")
        self.assertIn("Read [waiting.md](waiting.md)", step)

    def test_the_paragraph_is_stated_once(self):
        text = (ROOT / "t3/runtime.md").read_text() + (ROOT / "t3/added/pstack-runtime/delegation.md").read_text()
        self.assertEqual(text.count("Nobody resumes you after your turn ends"), 1)

class SeatLaunchDocTest(unittest.TestCase):
    def setUp(self):
        self.runtime = (ROOT / "skills/pstack-runtime/threads.md").read_text()

    def test_mapping_sentence_renames_provider_instance_id_and_copies_options(self):
        section = self.runtime.split("## Top-level threads", 1)[1].split("\n## ", 1)[0]
        self.assertIn(
            "`modelSelection` is the seat with `providerInstanceId` renamed to `instanceId`, "
            "`model` copied, and `options` copied unchanged as the same object.",
            section,
        )
        self.assertIn('"options": {"effort": "xhigh"}', section)
        self.assertIn("Confirm a launched thread per [Delegation](delegation.md#spawn-and-collect) step 3.", section)

    def test_readback_sentence_names_where_to_read_and_never_retries_without_options(self):
        delegation = (ROOT / "t3/added/pstack-runtime/delegation.md").read_text()
        step = delegation.split("3. Spawn every independent child", 1)[1].split("\n4. A child starts", 1)[0]
        self.assertIn(
            "After a `t3_thread_launch` or `delegate_task` call whose seat has `options`, "
            "read the applied options from the launch result's `modelSelection.options` or from "
            "`t3_thread_configuration` on the child's `childThreadId`, and never call again with the options removed.",
            step,
        )
