if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import json
import shlex
import sys
from types import SimpleNamespace
from unittest import mock

from ._support.inprocess import call, call_in_process, run_globals
from ._support.names import SCRIPT, _owner_words
from .support import BrigadeTest, setUpModule

STATUS_LINE = "outside ping: on"
PERF_DECISION = "perf: a decision is waiting for your answer"
PERF_STUCK = "perf: work hit a failure it cannot fix and needs you"


class NotifyTest(BrigadeTest):
    """The outside ping runs its command once, with one plain line, only for a new decision or a stuck failure."""

    def setUp(self):
        super().setUp()
        self.log = self.root / "pings.jsonl"
        self.open()

    def notifier(self):
        """A command that records its arguments, one JSON list per run, in self.log."""
        path = self.root / "notifier.py"
        path.write_text(f"import json, sys\nwith open({str(self.log)!r}, 'a') as handle:\n"
                        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n")
        return shlex.join([sys.executable, str(path)])

    def pings(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def outcome(self, *args, at=None):
        directory = self.at if at is None else at
        return call(self, SCRIPT, ["--store", str(self.store), "--at", str(directory),
                                   *_owner_words(directory), *(str(arg) for arg in args)])

    def ask(self, question="Which purpose?", dish=None, at=None):
        words = ["86", "add"]
        if dish is not None:
            words += ["--dish", dish]
        words += ["--question", question, "--options", "docs, engine", "--default", "docs"]
        return self.outcome(*words, at=at).stdout_text().strip()

    def test_set_notify_shows_on_and_hides_the_command_and_clearing_removes_the_status_line(self):
        command = self.notifier()
        shown = json.loads(self.brigade("set", "--notify", command))
        self.assertEqual(shown["notify"], "on")
        self.assertNotIn(command, self.brigade("set", "--notify", command))
        self.assertIn(command, (self.at / "restaurant.json").read_text())
        self.assertTrue(self.brigade("status").endswith(STATUS_LINE))
        self.brigade("set", "--notify", "")
        self.assertNotIn("notify", json.loads((self.at / "restaurant.json").read_text()))
        self.assertNotIn(STATUS_LINE, self.brigade("status"))
        self.assertEqual(self.pings(), [])

    def test_a_command_shlex_cannot_split_is_refused_and_leaves_restaurant_json_alone(self):
        before = (self.at / "restaurant.json").read_bytes()
        self.assertEqual(self.brigade("set", "--notify", "'unclosed", code=1),
                         'brigade: --notify takes a command line or ""')
        self.assertEqual((self.at / "restaurant.json").read_bytes(), before)

    def test_a_new_decision_pings_once_with_the_restaurant_name_and_the_decision_tail(self):
        self.brigade("set", "--notify", self.notifier())
        self.assertEqual(self.ask(), "Q1")
        self.assertEqual(self.pings(), [[PERF_DECISION]])

    def test_a_stuck_next_pings_once_with_the_stuck_tail(self):
        self.brigade("set", "--notify", self.notifier())
        self.brigade("next", "--failure", "stuck")
        self.assertEqual(self.pings(), [[PERF_STUCK]])

    def test_a_repeated_decision_for_a_dish_returns_the_open_id_and_pings_nothing_more(self):
        self.brigade("set", "--notify", self.notifier())
        first = self.ask(dish="D1")
        self.assertEqual(self.ask(dish="D1"), first)
        self.assertEqual(self.pings(), [[PERF_DECISION]])

    def test_next_without_failure_fixable_failure_86_list_and_86_answer_ping_nothing(self):
        self.brigade("set", "--notify", self.notifier())
        self.ask()
        self.assertEqual(len(self.pings()), 1)
        self.brigade("next")
        self.brigade("next", "--failure", "fixable")
        self.brigade("86", "list")
        self.brigade("86", "answer", "Q1", "--answer", "docs")
        self.assertEqual(self.pings(), [[PERF_DECISION]])

    def test_a_restaurant_that_never_set_notify_prints_the_same_and_runs_no_command(self):
        outcome = self.outcome("86", "add", "--question", "Which purpose?", "--options", "docs, engine",
                               "--default", "docs")
        self.assertEqual(outcome.code, 0)
        self.assertEqual(outcome.stdout_text().strip(), "Q1")
        self.assertEqual(outcome.stderr_text(), "")
        self.assertNotIn(STATUS_LINE, self.brigade("status"))
        self.assertFalse(self.log.exists())

    def test_a_notifier_that_exits_3_warns_once_and_the_decision_is_still_parked(self):
        self.brigade("set", "--notify", shlex.join([sys.executable, "-c", "import sys; sys.exit(3)"]))
        outcome = self.outcome("86", "add", "--question", "Which purpose?", "--options", "docs, engine",
                               "--default", "docs")
        self.assertEqual(outcome.code, 0)
        self.assertEqual(outcome.stdout_text().strip(), "Q1")
        self.assertEqual(outcome.stderr_text().splitlines(),
                         ["brigade: warning: the outside ping exited with status 3"])
        self.assertEqual(self.table_row(self.at, "86.tsv", "Q1")["question"], "Which purpose?")

    def test_a_notifier_that_hangs_is_stopped_at_its_timeout_with_one_warning(self):
        self.brigade("set", "--notify", shlex.join([sys.executable, "-c", "import time; time.sleep(30)"]))
        notify = run_globals(self, SCRIPT)["notify"]
        with mock.patch.object(notify, "TIMEOUT_SECONDS", 2):
            outcome = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                     *_owner_words(self.at), "86", "add", "--question", "Which purpose?",
                                                     "--options", "docs, engine", "--default", "docs"])
        self.assertEqual(outcome.code, 0)
        self.assertEqual(outcome.stdout_text().strip(), "Q1")
        self.assertEqual(outcome.stderr_text().splitlines(),
                         ["brigade: warning: the outside ping took longer than 2 seconds and was stopped"])

    def test_a_notifier_that_does_not_exist_warns_once_and_the_decision_is_still_parked(self):
        self.brigade("set", "--notify", "/nonexistent/brigade-notifier-for-test")
        outcome = self.outcome("86", "add", "--question", "Which purpose?", "--options", "docs, engine",
                               "--default", "docs")
        self.assertEqual(outcome.code, 0)
        self.assertEqual(outcome.stdout_text().strip(), "Q1")
        self.assertEqual(outcome.stderr_text().splitlines(),
                         ["brigade: warning: the outside ping could not start"])

    def test_thread_path_email_token_and_url_in_the_question_never_reach_the_argument(self):
        self.brigade("set", "--thread", "ab12cd34ef")
        self.brigade("set", "--notify", self.notifier())
        self.ask(question=f"Thread ab12cd34ef in {self.project}, mail mg1452@example.com, "
                          "token ghp_abcdef1234567890abcdef1234567890ab, see https://example.com/a")
        [[line]] = self.pings()
        self.assertEqual(line, PERF_DECISION)
        for leaked in ("ab12cd34ef", str(self.project), "mg1452", "ghp_", "https://"):
            self.assertNotIn(leaked, line)

    def test_a_restaurant_named_with_a_secret_prefix_or_a_uuid_pings_as_brigade(self):
        for name in ("sk-live-abcdef12", "3f2b9c1e-8a4d-4b7e-9c2f-1a2b3c4d5e6f"):
            directory = self.store / "bridge-kit" / name
            self.outcome("open", "--project-root", str(self.project), "--name", name, at=directory)
            self.outcome("set", "--notify", self.notifier(), at=directory)
            self.ask(at=directory)
        self.assertEqual(self.pings(), [["brigade: a decision is waiting for your answer"]] * 2)

    def test_a_thread_id_inside_the_restaurant_name_pings_as_brigade(self):
        notify = run_globals(self, SCRIPT)["notify"]
        fake = SimpleNamespace(dir=self.at, meta={"restaurant": "Perf ab12cd34ef", "thread": "ab12cd34ef",
                                                    "projectRoot": str(self.project)})
        self.assertEqual(notify._line(fake, notify.Trigger.DECISION),
                         "brigade: a decision is waiting for your answer")

    def test_an_outside_ping_leaves_no_activity_lib_module_loaded(self):
        self.brigade("set", "--notify", self.notifier())
        before = {name for name in sys.modules if name == "activity_lib" or name.startswith("activity_lib.")}
        outcome = call_in_process(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                                 *_owner_words(self.at), "86", "add", "--question", "Which purpose?",
                                                 "--options", "docs, engine", "--default", "docs"])
        self.assertEqual(outcome.code, 0)
        after = {name for name in sys.modules if name == "activity_lib" or name.startswith("activity_lib.")}
        self.assertEqual(after, before)
        self.assertEqual(self.pings(), [[PERF_DECISION]])
