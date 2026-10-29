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

from .support import setUpModule, BrigadeTest, Path, ROOT, SCRIPT, run_globals
from .seat_cooldown.fixtures import FAILED, LIMIT_SPAN, PARENT, _CooldownHelpers


PIN = json.loads((ROOT / "tstack.json").read_text())["commit"]

WRITER = '{"instanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}}'

COOLING_END = "2026-02-01T02:00:00+00:00"


class FullSendTest(BrigadeTest, _CooldownHelpers):
    def setUp(self):
        super().setUp()
        self.freeze()
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf")
        self.brigade("set", "--thread", "c1")
        self.fire_one(station="full-send")
        self.ran = Path(self.temporary.name) / "roles-ran"
        script = Path(self.temporary.name) / "roles.py"
        script.write_text(f"from pathlib import Path\nPath({str(self.ran)!r}).write_text('ran')\nraise SystemExit(1)\n")
        glob = run_globals(self, SCRIPT)
        glob["BUILT_ROLES"] = glob["SOURCE_ROLES"] = script

    def test_a_full_send_brief_links_the_pinned_skills_in_place_of_the_playbook_mode_and_seat_rule(self):
        """The defect: a full-send brief keeps the poteto-mode head, links another tstack commit, or resolves its mode through roles.py."""
        text = self.command_in_process(*self.BRIEF)
        head = text.split("\n\nGOAL: ", 1)[0].splitlines()
        self.assertEqual(head[:4], [
            "Use tstack's full-send method in place of a poteto-mode playbook, its architect step, and its code delegate.",
            "Gate: brigade", "", "FULL SEND:"])
        for name in ("full-send", "sol-loop", "file-pr", "babysit-pr"):
            self.assertIn(f"- {name}: https://github.com/t3dotgg/tstack/blob/{PIN}/skills/{name}/SKILL.md", head)
        self.assertIn(str(self.at / "reports" / "D1-pr.md"), text)
        self.assertNotIn("poteto-mode skill", text)
        self.assertNotIn(self.SEAT_RULE, text)
        self.assertEqual([line for line in text.splitlines() if line.startswith(("Mode", "Waived by mode"))], [])
        self.assertFalse(self.ran.exists())

    def test_launch_prints_the_full_send_writer_seat_without_roles_py(self):
        """The defect: launch seats a full-send writer through the standard worker roles."""
        outcome = self.outcome_in_process("launch", "D1", "--parent", PARENT, "--catalog", str(self.catalog()))
        self.assertEqual((outcome.code, outcome.stdout_text(), outcome.stderr_text()), (0, WRITER + "\n", ""))
        self.assertFalse(self.ran.exists())

    def test_launch_refuses_a_full_send_writer_while_its_provider_cools(self):
        """The defect: launch prints the writer seat while its provider is out, or falls back to a backup seat."""
        self.write_cooldown_row(seat="claudeAgent/claude-haiku-5-5", end=COOLING_END)
        outcome = self.outcome_in_process("launch", "D1", "--parent", PARENT, "--catalog", str(self.catalog()))
        self.assertEqual((outcome.code, outcome.stdout_text()), (1, ""))
        self.assertEqual(outcome.stderr_text().strip(), f"brigade: D1: claudeAgent/claude-haiku-5-5 cooling until "
                                                        f"{COOLING_END}; a full-send item has no backup seat")
        self.assertFalse(self.ran.exists())

    def test_a_full_send_usage_limit_parks_the_item_instead_of_naming_a_backup_seat(self):
        """The defect: a full-send writer's usage limit relaunches it on a seat from the standard backups."""
        self.assertEqual(self.usage_limit(LIMIT_SPAN, role="full-send"),
                         "D1: parked until 2026-02-01T00:30:00+00:00; launch nothing before then")
        self.assertEqual(self.dish_fields("state"), ("blocked",))
        self.assertEqual([row["seat"] for row in self.cooldown_rows()], [FAILED])
        self.assertFalse(self.ran.exists())
