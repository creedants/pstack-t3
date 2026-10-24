if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "t3/scripts"))
sys.path.append(str(ROOT / "scripts"))

import roles  # noqa: E402
from tests.roles_tests.support import roles_source  # noqa: E402
from tests.pstack_t3.support import runtime_doc_section, runtime_section


class LaunchingSkillTestDocTest(unittest.TestCase):
    def test_haiku_section_send_a_seat_launching_test_through_launches_seats(self):
        section = runtime_doc_section("roles.md", "## Claude Haiku 5.5")
        self.assertIn('`roles.py show --role "skill tests" --launches-seats`', section)
        self.assertIn("That form refuses a configured seat that runs on the cursor driver", section)
        self.assertIn("A configured non-cursor seat is not limited by `--launches-seats`.", section)
        self.assertIn(roles.HAIKU_BRIEF[0], roles_source())
        author = (ROOT / "t3/added/pstack-author-skill/SKILL.md").read_text()
        self.assertIn("--launches-seats", author)

    def test_delegation_step_4_pastes_haiku_brief_and_the_modes_bullet_does_not(self):
        text = (ROOT / "t3/runtime.md").read_text()
        step = text.split("4. A child starts with only its brief.", 1)[1].split("\n### Permissions", 1)[0]
        self.assertIn(
            "When the role entry `roles.py show` printed for the child's seat has `haikuBrief`, "
            "paste its paragraphs unchanged, in order, at the end of the brief, "
            "per [Claude Haiku 5.5](#claude-haiku-55).",
            step,
        )
        modes = runtime_section("## Modes")
        bullet = next(
            line for line in modes.splitlines()
            if line.startswith("- A code delegate's brief passes `--playbook <name> --attempt <kind>`")
        )
        self.assertNotIn("haikuBrief", bullet)

class MuseDocTest(unittest.TestCase):
    def test_runtime_names_muse_and_limits_its_modes(self):
        runtime = (ROOT / "t3/runtime.md").read_text()
        intro = next(line for line in runtime.splitlines() if "Every provider T3 drives" in line)
        self.assertIn("OpenCode, Muse, ACP agents", intro)
        permissions = runtime_doc_section("delegation.md", "### Permissions", "\n### ")
        self.assertIn("Muse supports only `approval-required` and `full-access`.", permissions)
        self.assertIn("Read `runtimeMode` from `orchestrator_capabilities` before you seat Muse.", permissions)
        self.assertIn('replace the Muse seat with `"inherit"` per [Fallback](SKILL.md#fallback)', permissions)
        self.assertIn("never lower it", permissions)

    def test_skill_locations_exclude_muse_from_the_picker(self):
        section = runtime_doc_section("host.md", "## Skill locations")
        self.assertIn("except Muse's", section)
        self.assertIn("`muse skills`", section)

    def test_setup_does_not_treat_a_cached_muse_catalog_as_proof(self):
        text = (ROOT / "t3/setup.md").read_text()
        step1 = text.split("### 1. Check the host", 1)[1].split("\n### 2. ", 1)[0]
        self.assertIn("does not prove a Muse seat works", step1)
        self.assertIn('`driverKind: "muse"`', step1)
        step5 = text.split("### 5. Verify", 1)[1].split("\n### 6. ", 1)[0]
        self.assertIn("does not prove a Muse child can work under this thread's runtime mode", step5)
        self.assertIn("(../pstack-runtime/SKILL.md#permissions)", step5)

class ExcludedSeatsDocTest(unittest.TestCase):
    def test_excluded_seats_section_quotes_the_code_rule(self):
        section = runtime_doc_section("roles.md", "## Excluded seats")
        self.assertIn(roles.EXCLUDE_RULE, section)
        for phrase in (
            "`grok-*-fast`",
            '`{"model": "grok-4.7", "options": {"fastMode": true}}`',
            "the cursor driver cannot launch seats",
            "`validate` lists each refused seat and exits 1.",
            "`show` skips a refused configured seat with the note `skipped configured seat <provider>/<model>: <reason>`",
            "`inherit replaced by <provider>/<model>`",
        ):
            self.assertIn(phrase, section)
        source = roles_source()
        self.assertIn(roles.HAIKU_BRIEF[0], source)
        self.assertIn(roles.HAIKU_BRIEF[1], source)

    def test_excluded_seat_notes_match_roles_py(self):
        section = runtime_doc_section("roles.md", "## Excluded seats")
        source = roles_source()
        for phrase in ("inherit replaced by", "skipped configured seat"):
            self.assertIn(phrase, section)
            self.assertIn(phrase, source)
        self.assertIn("refusing to write, even with --force", source)

class SetupFastGrokDocTest(unittest.TestCase):
    def test_setup_describes_the_exclude_key_for_a_fast_grok_id(self):
        text = (ROOT / "t3/setup.md").read_text()
        self.assertIn("The `exclude` key refuses a model.", text)
        self.assertIn('Add an object entry such as `{"model": "grok-4.7-build-fast"}` to refuse a fast Grok id', text)
        self.assertIn("(../pstack-runtime/SKILL.md#excluded-seats)", text)
        self.assertNotIn("Add `fastMode` only when", text)

class AuthorSkillDocTest(unittest.TestCase):
    def test_seat_launching_test_goes_through_launches_seats(self):
        text = (ROOT / "t3/added/pstack-author-skill/SKILL.md").read_text()
        step = text.split("## 4. Test it on a fresh child", 1)[1].split("\n## 5. ", 1)[0]
        step2 = step.split("\n2. ", 1)[1].split("\n3. ", 1)[0]
        self.assertIn("stays on the `show` seat, unless its child launches seats.", step2)
        self.assertIn("`--launches-seats`", step2)
        self.assertIn("(../pstack-runtime/SKILL.md#claude-haiku-55)", step2)
