import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "t3/scripts"))
sys.path.append(str(ROOT / "scripts"))

import roles  # noqa: E402
from tests.pstack_t3.support import runtime_doc_section, runtime_section, runtime_docs_section, runtime_bullet, override_text


class ThreadLifecycleDocTest(unittest.TestCase):
    def test_history_names_snooze_fields_and_filter(self):
        section = runtime_doc_section("host.md", "## Snooze")
        for sentence in (
            "`t3_thread_list` and `t3_thread_read` report `snoozed` and `snoozedUntil`.",
            "Pass `snoozed: true` to `t3_thread_list` to find snoozed owners.",
            '`t3_thread_organize` with `action: "snooze"` requires `snoozedUntil`.',
            "A snoozed thread wakes early when it asks for something, fails, or completes.",
            "Snooze is a sidebar state.",
            "It is not a schedule, a completion, or a settle.",
            "Wait with `schedule_task` or `watch_pull_request`, never with `snoozedUntil`.",
        ):
            self.assertIn(sentence, section)

    def test_history_states_the_thread_link_in_a_form_check_accepts(self):
        import check  # noqa: E402
        section = runtime_doc_section("host.md", "## History\n")
        self.assertIn("whose target is `t3-thread://v1/<threadId>`", section)
        self.assertIn("Do not URL-encode it, decode its `%` escapes, or add an environment ID.", section)
        self.assertIn("carry no `link` field", section)
        launch = runtime_doc_section("threads.md", "## Top-level threads")
        self.assertIn("Report it to the user as a thread link per [History](SKILL.md#history).", launch)
        self.assertEqual(check.LINK.findall("[t](t3-thread://v1/abc)"), ["t3-thread://v1/abc"])
        targets = check.LINK.findall((ROOT / "t3/runtime.md").read_text())
        self.assertFalse(any(target.startswith("t3-thread:") for target in targets))

    def test_self_settle_is_a_request_not_a_settle(self):
        section = runtime_doc_section("host.md", "## Settle on turn end") + runtime_doc_section("watching.md", "## Pull request watching")
        for phrase in (
            "`settlesWhenTurnEnds: true`",
            "an accepted request, not a settled thread",
            "or a queued message, leaves the thread active",
            "a T3 restart before the turn ends drops the request",
            "Never settle an owner whose PR watch or `schedule_task` loop is still needed",
        ):
            self.assertIn(phrase, section)

    def test_isolation_names_the_settle_action(self):
        section = runtime_doc_section("isolation.md", "## Isolation") + runtime_doc_section("host.md", "## Settle on turn end")
        for phrase in (
            "T3 runs the first effective project action with `runOnSettle: true`",
            "A thread in the main checkout skips it.",
            "The effective list is the project's override, else the environment defaults.",
            "Actions in a repository `t3.json` count only once imported into project settings",
            "`t3_project_read` returns the project's saved `scripts`, which can differ from the effective list.",
            "Take `projectSettingsOverrides.<projectId>.defaultProjectScripts` when present, else `defaultProjectScripts`",
            "When `projectSettingsFolded` is not `true`, older saved lists still count",
            "never schedule a tick to watch for it",
            "T3 storage cleanup can remove that worktree after the thread ends.",
        ):
            self.assertIn(phrase, section)
        self.assertNotIn("Read `t3.json` before you settle", section)

    def test_local_state_does_not_promise_worktrees_persist(self):
        section = runtime_doc_section("host.md", "## Local state") + runtime_doc_section("failures.md", "## After a restart")
        self.assertNotIn("Pushed branches, worktrees, launched threads, and schedules persist.", section)
        self.assertIn("unless the project enables storage cleanup", section)
        self.assertIn("by squash or rebase at the worktree's head SHA", section)
        self.assertIn("check that `worktreePath` exists before you use a saved path", section)

class ForkDocTest(unittest.TestCase):
    def setUp(self):
        self.section = runtime_docs_section("## Forks")

    def test_forks_live_under_top_level_threads(self):
        stub = runtime_section("## Top-level threads")
        self.assertIn("### Forks", stub)
        self.assertIn("Forks live in [threads.md](threads.md#forks).", stub)
        threads = runtime_doc_section("threads.md", "## Top-level threads")
        self.assertIn("`create_threads` makes up to 20 threads", threads)
        self.assertLess(stub.index("`create_threads` makes up to 20 threads"), stub.index("### Forks"))

    def test_a_fork_never_replaces_a_child(self):
        for sentence in (
            "Use them only for a separate thread this section allows.",
            "A fork never stands in for a child task, an Orchestrate sub-coordinator, or a fresh review round, "
            "and `task_status` does not apply to it.",
            "Fork only a read-only planner or investigation whose source context is the part it needs.",
            "Writers stay isolated per [Isolation](SKILL.md#isolation).",
        ):
            self.assertIn(sentence, self.section)

    def test_source_points_match_the_schema(self):
        self.assertIn(
            'Pass `sourcePoint` as `{"type": "latest_stable"}`, `{"type": "run", "runId": "<id>"}`, '
            'or `{"type": "checkpoint", "checkpointId": "<id>"}`.',
            self.section,
        )
        self.assertIn("Keep the returned `targetThreadId`.", self.section)

    def test_inherited_seat_is_checked_and_binding_is_read(self):
        for sentence in (
            "The fork inherits the source's configuration.",
            "Inheritance is not an exception.",
            "Read the fork with `t3_thread_read` for its `worktreePath`, `branch`, and `activeRunId` before you send anything.",
            "Treat a checkout it shares with another thread as read-only.",
        ):
            self.assertIn(sentence, self.section)
        self.assertIn("[Excluded seats](SKILL.md#excluded-seats)", self.section)

    def test_merge_back_is_authorized_and_read_back(self):
        for sentence in (
            "A one-line report stays the default.",
            "Call `t3_thread_merge_back` only when the user authorized it and the target needs the fork's reasoning, not only its result.",
            "A transfer moves context, not code, and it does not make a review independent.",
        ):
            self.assertIn(sentence, self.section)
        self.assertIn("`t3_thread_transfers`", self.section)

    def test_no_observed_behavior_is_a_guarantee(self):
        for claim in (
            "starts idle", "with no run", "runCount", "until the target's next turn consumes it",
            "It has the source's `worktreePath`", "stays `pending`",
        ):
            self.assertNotIn(claim, self.section)

    def test_orchestrate_sub_coordinator_stays_a_delegated_child(self):
        text = override_text("poteto-mode/playbooks/orchestrate.md")
        bullet = next(line for line in text.splitlines() if line.startswith("- **Sub-coordinator.**"))
        self.assertIn("A sub-coordinator is a `delegate_task` child that itself calls `delegate_task`.", bullet)
        self.assertNotIn("fork", bullet.lower())
        self.assertNotIn("#forks", text)

class ConfigureOwnerDocTest(unittest.TestCase):
    def setUp(self):
        self.bullet = runtime_bullet(runtime_docs_section("## Configure an owner"), "- Change an existing launched thread's model with `t3_thread_configure`")

    def test_configure_runs_only_from_a_playbook_step(self):
        self.assertIn("only when a playbook step already calls for that owner to run on another model", self.bullet)
        self.assertIn("Never call it because an owner keeps failing a gate.", self.bullet)

    def test_seat_comes_from_roles_and_is_read_back(self):
        for sentence in (
            "Resolve the seat per [Roles](SKILL.md#roles) with `roles.py show`.",
            "Call it when `t3_thread_read` shows no `activeRunId`, or after `t3_thread_interrupt` and `t3_thread_wait` end the run.",
            "compare `instanceId`, `model`, and every option per [Delegation](delegation.md#spawn-and-collect) step 3.",
            "On a refusal or a mismatch, send nothing to the thread and report the tool's error text.",
        ):
            self.assertIn(sentence, self.bullet)

    def test_configure_always_passes_a_concrete_seat(self):
        for sentence in (
            "`t3_thread_configure` requires a concrete `modelSelection`, so always pass one, even for an `inherit` seat.",
            "Build it from the resolved seat with `providerInstanceId` renamed to `instanceId`, `model` copied, and `options` copied unchanged.",
            "When the resolved seat is `inherit`, use this thread's own provider and model, `inheritedProviderInstanceId` and "
            "`inheritedModel` from `orchestrator_capabilities`, as the concrete seat when it passes [Excluded seats](SKILL.md#excluded-seats).",
            "When it fails that check, do not configure the thread, and report that no replacement seat could be selected.",
        ):
            self.assertIn(sentence, self.bullet)
        for claim in ("built per the `modelSelection` bullet above", "Omit `modelSelection`"):
            self.assertNotIn(claim, self.bullet)

    def test_configure_keeps_gates_and_fresh_protocols(self):
        for sentence in (
            "The call does not change the thread's permission modes, and it keeps the thread's retry count, scope, standing orders, and gates.",
            "The thread keeps its history, so it never counts as a fresh worker or a fresh reviewer.",
            "Never configure a verifier or reviewer thread.",
            "A failed child task gets a fresh child per [Failure handling](SKILL.md#failure-handling).",
            "A send-back and a usage-limit relaunch get the fresh worker their playbook or `roles.py backup` names.",
            "None of them uses `t3_thread_configure`.",
        ):
            self.assertIn(sentence, self.bullet)

    def test_no_in_flight_switch_or_escalation_claim(self):
        for claim in ("escalate an owner", "The next turn runs on the new seat", "instead of relaunching it"):
            self.assertNotIn(claim, self.bullet)

    def test_orchestrate_retry_links_configure_for_owners_only(self):
        text = override_text("poteto-mode/playbooks/orchestrate.md")
        bullet = next(line for line in text.splitlines() if line.startswith("- Retry by mode:"))
        self.assertIn(
            "A long-lived owner takes the new model in place per "
            "[Top-level threads](../../pstack-runtime/SKILL.md#top-level-threads), and a child gets a fresh child on it.",
            bullet,
        )
        self.assertIn("Two retries, then abandon the unit and replan around it.", bullet)

class PromoteQueuedCorrectionDocTest(unittest.TestCase):
    def setUp(self):
        self.launch = runtime_doc_section("threads.md", "## Top-level threads")
        self.bullet = runtime_bullet(runtime_docs_section("## Promote a queued correction"), "- A correction you queued earlier")

    def test_promote_sits_directly_under_the_auto_report_rule(self):
        lines = self.launch.splitlines()
        report = next(i for i, line in enumerate(lines) if line.startswith("- Autopilot-full, Autopilot-stack, and Orchestrate owners"))
        self.assertTrue(lines[report + 1].startswith("- A correction you queued earlier"))

    def test_promote_names_the_tool_keys_and_reads_delivery(self):
        for sentence in (
            "call `t3_queue_list` on the recipient and find the `queuedRunId` of that exact message.",
            "Read the recipient's `activeRunId` with `t3_thread_read`.",
            "Call `t3_queue_promote_to_steer` with that `queuedRunId`, the `activeRunId` as `targetRunId`, and the recipient's `threadId`.",
            "Its `sequence` result means T3 accepted the call, not that the run received the message.",
            "Until that read shows it, the delivery is unresolved.",
            "A `cancelled` queued run alone proves nothing.",
            "never resend a correction that may already be delivered.",
        ):
            self.assertIn(sentence, self.bullet)

    def test_brigade_events_are_never_promoted(self):
        self.assertIn(
            "Never promote brigade's event lines to an executive admin, a digest message, or a message that needs a turn of its own.",
            self.bullet,
        )
        self.assertIn("Brigade's event lines to an executive admin stay on `mode: \"queue\"`", self.launch)

    def test_cancellation_is_not_success(self):
        self.assertNotIn("a wait on that run is not a failure", self.bullet)
        self.assertNotIn("then reports `cancelled`", self.bullet)

class RunScheduleNowDocTest(unittest.TestCase):
    def setUp(self):
        self.bullet = runtime_bullet(runtime_docs_section("## Run a schedule now"), "- To run an enabled `interval` schedule's work now")

    def test_run_now_takes_the_scheduled_task_id(self):
        self.assertIn("call `run_scheduled_task_now` with its ID as `taskId`.", self.bullet)
        self.assertIn("from `scheduledTaskId` in `list_scheduled_tasks`", self.bullet)

    def test_run_now_is_dispatch_not_completion(self):
        for sentence in (
            "Each call is a new manual run.",
            "Before you retry a lost or failed response, read `list_scheduled_tasks` and the bound thread with `t3_thread_read`.",
            "The result means T3 dispatched the run, not that its turn finished.",
            "Report the returned `nextRunAt` as T3 returned it.",
        ):
            self.assertIn(sentence, self.bullet)

    def test_run_now_keeps_pause_and_watch_rules(self):
        for sentence in (
            "No event calls it by rule, a merge or an answer included.",
            "Never run a paused schedule before the answer permits work.",
            "Never run one to wait on a child, to wait on a pull request's checks, reviews, or conflicts, "
            "or to repeat a `watch_pull_request` wake.",
            "It requires a full-access or default caller.",
        ):
            self.assertIn(sentence, self.bullet)

    def test_run_now_promises_no_cadence_and_grants_no_work(self):
        self.assertNotIn("counts from it", self.bullet)
        self.assertNotIn("do the tick's work in this turn", self.bullet)
        self.assertIn("only when this thread owns that work and its mode allows the edits", self.bullet)
