if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import BrigadeTest, StoresTest, _HandoffRace, _owner_words, SCRIPT, call, call_in_process, json, setUpModule
from .handoff.fixtures import WATCH
from .handoff.test_sources import SourcesMixin
from .handoff.test_move_and_take import MoveTakeMixin
from .handoff.test_kinds import HandoffKindsMixin
from .handoff.test_torn_writes import TornWritesMixin
from .handoff.test_size_line import SizeLineMixin


class HandoffTest(StoresTest, SourcesMixin, MoveTakeMixin, HandoffKindsMixin, TornWritesMixin, _HandoffRace):
    def labelled_handoff(self):
        """core files T1 as a polish ticket under its goal G1 and moves it to engine. Returns the line ticket move printed."""
        self.open("core")
        self.open("engine")
        self.brigade("core", "goal", "add", "--name", "activity view", "--purpose", "p", "--done", "d")
        self.brigade("core", "ticket", "add", "--summary", "Tidy the guide", "--kind", "polish", "--goal", "G1")
        return self.brigade("core", "ticket", "move", "T1", "--to", "engine")

    def handed(self, *extras):
        """engine's inbox holds one handoff file per dict, from core's T1 on. Each holds the seven keys from before kinds, and that dict."""
        self.open("core")
        self.open("engine")
        inbox = self.dir("engine") / "inbox"
        inbox.mkdir()
        for number, extra in enumerate(extras, 1):
            (inbox / f"app~core~T{number}.json").write_text(json.dumps(
                {"handoff": f"app/core/T{number}", "summary": "Tidy the guide", "source": "user", "ref": "",
                 "priority": "low", "paths": "docs", "decision": "", **extra}))

    def take_handoff(self, **extra):
        """engine takes a handoff file that holds the seven keys from before kinds, and `extra`."""
        self.handed(extra)
        return self.brigade("engine", "ticket", "take")

    def unknown_kind(self, ident, quoted):
        """The warning of a take for the ticket `ident` whose handoff kind, in quotes, is `quoted`."""
        return (f"brigade: warning: {ident} came from app/core/{ident} with kind {quoted}, which is not one this version knows, "
                f"so it has no kind; run ticket set {ident} --kind <feature|fix|polish|upkeep|study>\n")

    def assert_one_handed_ticket(self, name="engine"):
        self.assertEqual([row[2:] for row in self.rows(name, "rail.tsv")],
                         [["waiting", "github (from app/core/T1)", "https://github.com/o/r/issues/7", "", "Fix the cache", "", "", "", "fix", ""]])
        log = (self.dir(name) / "log.tsv").read_text()
        self.assertTrue(log.endswith("\n"))
        self.assertTrue(all(len(line.split("\t")) == 5 for line in log.splitlines()))
        self.assertEqual([row[1:] for row in self.rows(name, "log.tsv") if "from app/core/T1" in row[4]],
                         [["ticket", "T1", "waiting", "from app/core/T1"]])
        self.assertEqual(self.inbox(name), [])
        self.assertEqual(sorted(path.name for path in self.dir(name).glob("*.tmp")), [])


class HandoffSizeTest(BrigadeTest, SizeLineMixin):
    def watch_call(self, in_process=False):
        runner = call_in_process if in_process else call
        return runner(self, SCRIPT, [
            "--store", str(self.store), "--at", str(self.at), *_owner_words(self.at), "watch",
        ])

    def assert_watch(self, stderr="", in_process=False):
        outcome = self.watch_call(in_process=in_process)
        self.assertEqual(outcome.code, 0, outcome.stdout_text() + outcome.stderr_text())
        self.assertEqual(outcome.stdout_text(), WATCH + "\n")
        self.assertEqual(outcome.stderr_text(), stderr)
        return outcome

