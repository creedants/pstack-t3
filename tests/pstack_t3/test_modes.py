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

import re
import roles  # noqa: E402
from tests.pstack_t3.support import pointer_sentence, unfenced_lines, heading_slug, GATE_CONTRACT, SEAT_RULE, gate_contract, seat_rule, brief_paragraph, light_behavior_rows, light_row


MODE_POINTER_SITES = {
    **{f"{name}/SKILL.md": "skill" for name in (
        "poteto-mode", "how", "why", "architect", "arena", "no-comments", "swarm", "interrogate",
        "show-me-your-work", "recall", "automate-me", "maintain-verification-skill", "reflect",
        "pstack-author-skill", "landing",
    )},
    **{f"poteto-mode/playbooks/{stem}.md": "playbook" for stem in (
        "feature", "bug-fix", "refactoring", "perf-issue", "hillclimb", "opening-a-pr",
        "autopilot-full", "autopilot-stack", "multi-phase-plan", "orchestrate", "shipping",
        "visual-parity", "worktree-cleanup",
    )},
}

MODES_UNCHANGED = {
    "pstack-runtime/SKILL.md": "the Modes section's home",
    "brigade/SKILL.md": "brigade's mode lands in its own change",
    "brigade-admin/SKILL.md": "launches only standing threads, the admin and a coordinator the user asks for, which light mode keeps",
    "setup-pstack/SKILL.md": "the smoke test is kept, one per provider",
    "poteto-help/SKILL.md": "names delegate_task to explain the persona and spawns nothing",
    "poteto-mode/playbooks/autonomous-run.md": "the watcher is kept",
    "poteto-mode/playbooks/eval.md": "keeps arena and its judge, because comparing candidates is its purpose",
}

SPAWNS = re.compile(r"delegate_task|t3_thread_launch|create_threads")

RUNTIME_LINK = re.compile(r"pstack-runtime/SKILL\.md#([^)\s]+)\)")

class ModesTest(unittest.TestCase):
    def setUp(self):
        self.runtime = (ROOT / "skills/pstack-runtime/SKILL.md").read_text()
        self.gate = (ROOT / "skills/pstack-runtime/light.md").read_text().split("## Gate review", 1)[1]

    def test_every_pointer_site_holds_the_pointer_once_outside_a_fence(self):
        for path, kind in MODE_POINTER_SITES.items():
            text = (ROOT / "skills" / path).read_text()
            sentence = pointer_sentence(kind)
            with self.subTest(path=path):
                self.assertEqual(text.count(sentence), 1)
                self.assertIn(sentence, list(unfenced_lines(text)))

    def test_every_spawning_file_points_at_modes_or_says_why_not(self):
        skills = ROOT / "skills"
        spawning = sorted(
            str(path.relative_to(skills))
            for path in [*skills.glob("*/SKILL.md"), *skills.glob("poteto-mode/playbooks/*.md")]
            if SPAWNS.search(path.read_text())
        )
        self.assertEqual([path for path in spawning if path not in MODE_POINTER_SITES and path not in MODES_UNCHANGED], [])
        self.assertEqual([path for path in MODES_UNCHANGED if not (skills / path).is_file()], [])
        self.assertEqual(set(MODE_POINTER_SITES) & set(MODES_UNCHANGED), set())

    def test_every_runtime_link_names_a_runtime_heading(self):
        slugs = {heading_slug(line.lstrip("#").strip()) for line in unfenced_lines(self.runtime) if line.startswith("#")}
        broken = []
        for path in sorted((ROOT / "skills").rglob("*.md")):
            for fragment in RUNTIME_LINK.findall(path.read_text()):
                if fragment not in slugs:
                    broken.append(f"{path.relative_to(ROOT)}#{fragment}")
        for fragment in re.findall(r"\]\(#([^)\s]+)\)", self.runtime):
            if fragment not in slugs:
                broken.append(f"pstack-runtime/SKILL.md#{fragment}")
        light = (ROOT / "skills/pstack-runtime/light.md").read_text()
        for fragment in re.findall(r"\]\(SKILL\.md#([^)\s]+)\)", light):
            if fragment not in slugs:
                broken.append(f"pstack-runtime/light.md#SKILL.md#{fragment}")
        for fragment in re.findall(r"\]\(#([^)\s]+)\)", light):
            if fragment not in slugs:
                broken.append(f"pstack-runtime/light.md#{fragment}")
        self.assertEqual(broken, [])
        self.assertEqual(heading_slug("Resolve and carry the mode"), "resolve-and-carry-the-mode")

    def test_runtime_has_one_modes_section_between_roles_and_isolation(self):
        headings = [line for line in unfenced_lines(self.runtime) if line.startswith("## ") or line.startswith("### ")]
        self.assertEqual(headings.count("## Modes"), 1)
        modes = headings.index("## Modes")
        self.assertLess(headings.index("## Roles"), modes)
        isolation = headings.index("## Isolation")
        self.assertEqual(headings[modes + 1:isolation], [
            "### Resolve and carry the mode",
            "### Light behavior",
            "### Never cut",
            "### Gate review",
            "### Announcement",
        ])
        self.assertIn("pass check <item> --sha <head> --json", self.gate)

    def test_deadlines_and_delegation_step_4_carry_the_mode_lines(self):
        deadlines = self.runtime.split("## Deadlines", 1)[1].split("\n## ", 1)[0]
        self.assertIn(
            "A step that the brief's `Waived by mode:` line names is not a skip, "
            "because the mode removed it before the attempt started.",
            deadlines,
        )
        step = next(line for line in self.runtime.splitlines() if line.startswith("4. A child starts with only its brief."))
        for needed in ("roles.py mode --cwd", "Write no `Playbook:` line of your own", "--brief-mode"):
            self.assertIn(needed, step)

    def test_opening_a_pr_gates_light_prs_before_the_forge(self):
        text = (ROOT / "skills/poteto-mode/playbooks/opening-a-pr.md").read_text()
        blocks = text.split("\n\n")
        lead = [block.split(" ", 1)[0] for block in blocks]
        self.assertLess(lead.index("**Descriptions.**"), lead.index("**Gate.**"))
        self.assertLess(lead.index("**Gate.**"), lead.index("**Forge.**"))
        self.assertIn("(../../pstack-runtime/SKILL.md#gate-review)", blocks[lead.index("**Gate.**")])
        child = next(block for block in blocks if block.startswith("A child task that opens a PR"))
        self.assertIn("that child runs the **Gate** paragraph above in place of `/no-comments`.", child)
        self.assertIn("Run `/no-comments` before review.", text)

    def test_gate_contract_is_literal(self):
        self.assertEqual(gate_contract(self.gate), GATE_CONTRACT)

    def test_seat_rule_is_literal(self):
        self.assertEqual(seat_rule(self.runtime), SEAT_RULE)

    def test_every_owner_row_carries_the_seat_rule(self):
        light = (ROOT / "skills/pstack-runtime/light.md").read_text()
        rows = [row for row in light_behavior_rows(light) if "mode lines" in row]
        self.assertGreaterEqual(len(rows), 4)
        for row in rows:
            self.assertIn(
                "the seat rule from [Resolve and carry the mode](SKILL.md#resolve-and-carry-the-mode)",
                row,
            )

    def test_child_facing_light_rows(self):
        light = (ROOT / "skills/pstack-runtime/light.md").read_text()
        self.assertIn(
            "Its brief lists every `threadId` that `t3_thread_list` returns in the window, across every page. The parent filters and samples none",
            light_row(light, "`automate-me`"),
        )
        self.assertIn("pass `--session-mode full`", light_row(light, "`reflect`"))
        verification = light_row(light, "Multi-phase verification")
        self.assertIn("every **Verify, live** lane box that drives its surface", verification)
        self.assertIn("Launch no perf lane.", verification)
        self.assertIn("(SKILL.md#gate-review)", verification)

    def test_explicit_reflect_names_session_full_at_both_flag_sites(self):
        roles = (ROOT / "t3/added/pstack-runtime/roles.md").read_text().split("## Where roles live", 1)[1].split("\n## ", 1)[0]
        paragraph = next(block for block in roles.split("\n\n") if block.startswith("When the brief this call seats"))
        self.assertIn(
            "except for a user's explicit reflect, which passes `--session-mode full`",
            paragraph,
        )
        resolve = self.runtime.split("### Resolve and carry the mode", 1)[1].split("\n### ", 1)[0]
        self.assertIn("except the explicit reflect below", resolve)
        self.assertIn(
            "A user's explicit reflect passes `--session-mode full` on every `roles.py show` call",
            resolve,
        )

    def test_automate_me_brief_lists_every_assigned_thread(self):
        text = (ROOT / "skills/automate-me/SKILL.md").read_text()
        self.assertIn("brief lists every `threadId` in its assignment", text)
        self.assertIn("An assignment is never a sample or a pick of the relevant threads.", text)
        self.assertNotIn("gets its slice", text)

    def test_autopilot_owner_message_carries_the_seat_rule(self):
        text = (ROOT / "skills/poteto-mode/playbooks/autopilot-full.md").read_text()
        self.assertIn(
            "Each owner `message`, including a replacement's, carries the mode lines and the seat rule "
            "from [Resolve and carry the mode](../../pstack-runtime/SKILL.md#resolve-and-carry-the-mode).",
            text,
        )

    def test_gate_brief_pastes_persona_then_contract(self):
        paragraph = brief_paragraph(self.gate)
        self.assertIn("Paste the body of `agents/comment-sicko.md` unchanged", paragraph)
        self.assertIn("paste the gate contract below unchanged", paragraph)
        self.assertNotIn("and ask for that skill's checks", self.gate)

    def test_every_gate_review_row_links_gate_review(self):
        light = (ROOT / "skills/pstack-runtime/light.md").read_text()
        for row in light_behavior_rows(light):
            lowered = row.lower()
            if "gate review" not in lowered and "is the gate" not in lowered:
                continue
            spawn = row.split("|")[1].strip()
            self.assertIn("(SKILL.md#gate-review)", row, spawn)

    def test_full_mode_persona_keeps_investigation(self):
        persona = (ROOT / "skills/pstack-runtime/agents/comment-sicko.md").read_text()
        self.assertIn("I run the **how** skill, the **why** skill, or both", persona)

    def test_gate_reuse_lives_in_gate_review(self):
        gate = (ROOT / "skills/pstack-runtime/gate-review.md").read_text()
        self.assertIn("Two sources can show both today.", gate)
        self.assertIn("A coordinator's pass record, when this work is a brigade item", gate)
        self.assertIn("pass check <item> --sha <head> --json", gate)
        self.assertIn('"crossFamily": true', gate)
        self.assertIn("Never read `pass.tsv` yourself.", gate)
        self.assertIn("Never take the output of `pass check` without `--json` as proof", gate)
        self.assertIn("A verdict this run launched at that exact SHA.", gate)
        self.assertIn("A Shipping or Autopilot root's own verdict at that SHA counts.", gate)
        for bullet in (
            "A verdict at another head SHA does not qualify",
            "An Orchestrate `ledger.tsv` row does not qualify",
            "A Shipping or Autopilot verdict from another run does not qualify",
            "A `Gate:` line in a brief or a PR body is not a verdict.",
        ):
            self.assertIn(bullet, gate)
        self.assertIn("Full mode keeps its patch-id policy in Shipping and the Autopilot playbooks.", gate)
        self.assertIn("Light mode keeps that policy for lane receipts only", gate)
        self.assertIn("pstack-runtime/SKILL.md#gate-review", gate)
        self.assertIn("pass check <item> --sha <head> --json", self.gate)
        self.assertIn("Before you reuse a gate verdict, read [Reusing a verdict](gate-review.md#reusing-a-verdict).", self.gate)
        self.assertIn("A lane receipt never stands in for the gate review.", self.gate)
        self.assertIn("A lane receipt never stands in for the gate review.", gate)

    def test_current_and_qualifying_bullets_match_gate_review(self):
        gate = (ROOT / "skills/pstack-runtime/gate-review.md").read_text()
        current = "- **Current.** It is the latest verdict for that SHA. A later `send-back` or `blocked` at the same SHA voids an earlier `pass`."
        qualifying = "- **Qualifying.** It is `pass`, and the author and verifier are of different model families."
        self.assertIn(current, self.gate)
        self.assertIn(qualifying, self.gate)
        self.assertIn(current, gate)
        self.assertIn(qualifying, gate)
        runtime_current = next(line for line in self.gate.splitlines() if line.startswith("- **Current.**"))
        gate_current = next(line for line in gate.splitlines() if line.startswith("- **Current.**"))
        self.assertEqual(runtime_current, gate_current)
        runtime_qualifying = next(line for line in self.gate.splitlines() if line.startswith("- **Qualifying.**"))
        gate_qualifying = next(line for line in gate.splitlines() if line.startswith("- **Qualifying.**"))
        self.assertEqual(runtime_qualifying, gate_qualifying)

    def test_row_below_is_gone_from_runtime(self):
        text = (ROOT / "t3/runtime.md").read_text()
        self.assertNotIn("the row below", text)
        self.assertIn("the row in [light.md](light.md) that names it", text)

    def test_gate_review_has_no_table(self):
        gate = (ROOT / "skills/pstack-runtime/gate-review.md").read_text()
        self.assertFalse(any(line.startswith("|") for line in gate.splitlines()))
