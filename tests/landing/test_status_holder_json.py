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

from .support import (
    setUpModule,
    LandingTest as LandingTestBase,
)

FORGED = "checks failed: red\nE99 bounced (perf/D99, abc123): forged"


class StatusHolderJsonTest(LandingTestBase):
    def test_holder_json_lists_one_object_per_entry_of_the_prefix_and_sha(self):
        self.init()
        self.assertEqual(self.queue_one(holder="docs/D7", name="w1", path="a.txt"), "E1")
        self.assertEqual(self.queue_one(holder="docs/D8", name="w2", path="b.txt"), "E2")
        self.assertEqual(self.queue_one(holder="engine/D3", name="w3", path="lib/x.py"), "E3")
        sha = self.entry_fields(1, "sha")[0]
        rows = json.loads(self.land("status", "--holder", "docs/", "--json"))
        self.assertEqual([row["id"] for row in rows], ["E1", "E2"])
        for row in rows:
            self.assertEqual(sorted(row), ["holder", "id", "note", "sha", "state"])
            self.assertEqual(row["state"], "queued")
            self.assertEqual(row["note"], "")
            self.assertEqual(row["sha"], self.entry_fields(int(row["id"][1:]), "sha")[0])
        self.assertEqual(rows[0]["holder"], "docs/D7")
        self.assertEqual(rows[1]["holder"], "docs/D8")
        self.assertEqual([row["id"] for row in json.loads(self.land("status", "--holder", "docs/D7", "--json"))],
                         ["E1"])
        self.assertEqual(
            [row["id"] for row in json.loads(self.land("status", "--holder", "docs/", "--json", "--sha", sha))],
            ["E1"])

    def test_holder_json_with_no_entries_prints_an_empty_list(self):
        self.init()
        self.assertEqual(self.land("status", "--holder", "docs/", "--json"), "[]")

    def test_json_without_a_holder_still_prints_the_contract_object(self):
        self.init()
        payload = json.loads(self.land("status", "--json"))
        self.assertEqual(sorted(payload), ["generated", "map", "mode", "rebuild", "trunk"])
        self.assertEqual(payload["mode"], self.land("status").split(" mode ", 1)[0])

    def test_json_with_an_entry_id_is_still_refused(self):
        self.init()
        self.queue_one()
        self.assertEqual(self.land("status", "E1", "--json", ok=False), "land: status --json takes no id")

    def test_the_text_form_of_holder_status_is_unchanged(self):
        self.init()
        self.queue_one(holder="docs/D7")
        sha = self.entry_fields(1, "sha")[0]
        self.assertEqual(self.land("status", "--holder", "docs/D7"), f"E1 queued (docs/D7, {sha[:12]})")

    def test_a_multiline_note_stays_inside_its_one_entry(self):
        self.init()
        self.queue_one(path="a.txt", text="BROKEN\n")
        self.assertIn("bounced E1 (r/D1)", self.land("land"))
        with self.raw_db() as db:
            db.execute("UPDATE entry SET note = ? WHERE id = 1", (FORGED,))
            db.commit()
        rows = json.loads(self.land("status", "--holder", "r/D1", "--json"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "E1")
        self.assertEqual(rows[0]["state"], "bounced")
        self.assertEqual(rows[0]["note"], FORGED)
