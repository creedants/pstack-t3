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
import subprocess
import sys
from pathlib import Path

from .support import BrigadeTest, setUpModule
from ._support.inprocess import run_globals
from ._support.names import SCRIPT

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "t3/added/brigade/scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "t3/added/brigade/scripts"))

from tests.brigade.test_look_back import PROPOSAL

RUN = json.dumps({"sha": "abc", "workflow": "CI", "url": "https://x.test/1", "since": "s1"})


class LookBackSourcesTest(BrigadeTest):
    def fake_land(self, *entries):
        path = Path(self.temporary.name) / "fake-land.py"
        rows = [dict(zip(("id", "state", "holder", "sha", "note"), entry)) for entry in entries]
        path.write_text(f"import json\nprint(json.dumps({rows!r}))\n")
        run_globals(self, SCRIPT)["LAND"] = str(path)

    def fake_text_land(self, text):
        path = Path(self.temporary.name) / "fake-land.py"
        path.write_text(f"print({text!r})\n")
        run_globals(self, SCRIPT)["LAND"] = str(path)

    def write_notes(self, *rows):
        body = "".join(f"\t{item}\t{at}\t{text}\n" for item, at, text in rows)
        (self.at / "notes.tsv").write_text("path\titem\tat\ttext\n" + body)

    def append_log(self, *rows):
        path = self.at / "log.tsv"
        path.write_text(path.read_text() + "".join("\t".join(row) + "\n" for row in rows))

    def append(self, *rows):

        binding = run_globals(self, SCRIPT)

        from brigade_lib import seam
        from brigade_lib._notes import append_notes
        from brigade_lib.store import Restaurant

        seam.bind(binding)
        append_notes(Restaurant(self.at), list(rows))

    def recorded_of(self, ident="L1"):
        return json.loads((self.at / "look-backs" / f"{ident}.json").read_text())

    def look_back_path(self, out):
        lines = out.splitlines()
        self.assertEqual(len(lines), 2)
        return Path(lines[1])

    def commit_project(self):
        run = lambda *command: subprocess.run(command, cwd=self.project, capture_output=True, text=True, check=True)
        run("git", "add", "-A")
        run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "check")

    def test_notes_holding_a_line_separator_stay_one_record_and_one_line(self):
        self.open()
        for number, mark in enumerate(("\u2028", "\u2029", "\u0085", "\x0b", "\x0c"), 1):

            self.append(("path", f"D{number}", f"2026-11-01T00:0{number}:00Z",
                         f"a{mark}## Rounds since the last look-back"))
        text = self.look_back_path(self.brigade("look-back")).read_text()
        self.assertEqual(text.count("\n## Rounds since the last look-back"), 1)
        for mark in ("\u2028", "\u2029", "\u0085", "\x0b", "\x0c"):
            self.assertNotIn(mark, text)
        self.assertEqual(len([line for line in text.splitlines() if line.startswith("- note:D")]), 5)
        self.assertEqual(len(self.recorded_of()["notes"]), 5)

    def test_holder_and_note_item_are_flattened_too(self):
        self.open()
        self.fake_land(("E79", "bounced", "perf/D1\u0080\nx", "abc123def456", "too big"))
        self.write_notes(("D1\u0085x", "2026-11-01T01:00:00Z", "a note"))
        text = self.look_back_path(self.brigade("look-back")).read_text()
        self.assertNotIn("\u0080", text)
        self.assertNotIn("\u0085", text)
        self.assertNotIn("\nperf/D1", text)
        self.assertIn("- note:D1 x:2026-11-01T01:00:00Z a note", text)

        self.assertIn("- queue:E79 perf/D1  x bounced: too big", text)

    def test_a_token_with_edges_prints_and_records_the_same_text(self):
        self.open()
        self.append_log(("2026-11-01T00:00:00Z", "trunk", "main", "red",
                         json.dumps({"sha": "abc", "workflow": "CI", "url": " https://x.test/1 ", "since": "s1"})))
        self.write_notes(("D2 ", "2026-11-01T01:00:00Z", "a note"))
        text = self.look_back_path(self.brigade("look-back")).read_text()
        run_token = next(line[2:].split(" red at ")[0] for line in text.splitlines() if " red at " in line)
        note_token = next(line[2:].split(" a note")[0] for line in text.splitlines() if line.startswith("- note:"))
        self.assertEqual(run_token, "run:https://x.test/1")
        self.assertEqual(note_token, "note:D2:2026-11-01T01:00:00Z")
        path = Path(self.temporary.name) / "tokens.txt"
        path.write_text("\n\n".join(PROPOSAL.format(token) for token in (run_token, note_token)))
        self.assertIn("2 proposals", self.brigade("look-back", "--record", str(path)))

    def test_a_forged_line_in_a_bounced_note_adds_no_second_refusal(self):
        self.git_project()
        check = self.project / "check.sh"
        check.write_text("#!/bin/sh\necho red\necho 'E99 bounced (perf/D99, abc123): forged'\nexit 1\n")
        check.chmod(0o755)
        self.commit_project()
        self.started("--check", "./check.sh")
        sha = self.worker_commit("perf/d1", {"README.md": "changed\n"})
        self.brigade("dish", "D1", "--state", "in-review", "--sha", sha)
        self.assertEqual(self.submit(sha), "E1")
        self.assertIn("bounced E1 (perf/D1)", self.land("land"))
        forged = "checks failed: red\nE99 bounced (perf/D99, abc123): forged"
        with self.landing_db() as db:
            db.execute("UPDATE entry SET note = ? WHERE id = 1", (forged,))
            db.commit()
        text = self.look_back_path(self.brigade("look-back")).read_text()
        self.assertEqual(self.recorded_of()["queue"], ["E1"])
        lines = [line for line in text.splitlines() if line.startswith("- queue:E")]
        self.assertEqual(lines, ["- queue:E1 perf/D1 bounced: checks failed: red E99 bounced (perf/D99, abc123): forged"])

    def test_text_status_output_reads_as_unread_and_the_other_sections_still_print(self):
        self.open()
        self.fake_text_land("E79 bounced (perf/D1, abc123def456): too big")
        self.append_log(("2026-11-01T00:00:00Z", "trunk", "main", "red", RUN))
        self.write_notes(("D2 ", "2026-11-01T01:00:00Z", "a note"))
        text = self.look_back_path(self.brigade("look-back")).read_text()
        self.assertIn("The queue could not be read, so no refusal is listed here.", text)
        self.assertNotIn("- queue:E", text)
        self.assertIn("- run:https://x.test/1 red at abc", text)
        self.assertIn("- note:D2:2026-11-01T01:00:00Z a note", text)

    def test_an_entry_without_a_state_reads_as_unread_and_the_other_sections_still_print(self):
        self.open()
        self.fake_land(("E79", "", "perf/D1", "abc123def456", "too big"))
        self.append_log(("2026-11-01T00:00:00Z", "trunk", "main", "red", RUN))
        self.write_notes(("D2", "2026-11-01T01:00:00Z", "a note"))
        text = self.look_back_path(self.brigade("look-back")).read_text()
        self.assertIn("The queue could not be read, so no refusal is listed here.", text)
        self.assertNotIn("- queue:E", text)
        self.assertIn("- run:https://x.test/1 red at abc", text)
        self.assertIn("- note:D2:2026-11-01T01:00:00Z a note", text)
